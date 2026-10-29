"""TRS and cross-currency swaps through a prime broker: total-return swaps on US-listed equities / ETFs and fixed / fixed
cross-currency swaps against USD — booking, the computed marks the daily variation margin uses, nightly financing and coupon
accruals, dividend accrual, the TRS reset, the notional exchanges, the mark-to-market FX reset, payments, maturity and
termination (TERMINAL_DESIGN.md §2.12 products/swaps_trs.py, §4.1, §5.4, §11.2 TRS_RESET / XCCY_EVENTS, §11.3, §11.6, §13.11,
§13.12, §19.16; TERMINAL_WORKPLAN.md WP-26; the owner amendments of 2026-10-20 17:03 "variation margin on swaps too" and
17:07 "swap variation margin AND resets").  ``products/swaps.py`` (WP-17) owns the gates, the VM job and the views and calls
the hooks here (its module docstring HOOKS).

Conventions stated here (each is a §1.3 sentence or a rule the tests pin)
- GATES: ``swaps.h_book`` / ``swaps.preview`` check the OTC kind, the PB account (NOT_TRADEABLE_HERE elsewhere) and the
  lifetime's PB/OTC eligibility (NOT_ELIGIBLE) before they reach ``book`` / ``preview`` here; this module validates the
  TRS / XCCY terms and the observed inputs.  Nothing is booked, marked or settled without an observed input: a missing or
  stale price, rate or curve refuses (409) or skips (a job) with the reason — never a default.
- TRS TERMS: ``underlying_id`` (a US-listed EQUITY / ETF / ADR / REIT / PREFERRED quoted in USD and ACTIVE: dividends come
  from Yahoo's chart events), ``units`` (whole, > 0), ``receiver`` (true = receive the total return and pay financing;
  false = pay the total return and receive financing; or ``side`` RECEIVE / PAY), ``reset_months`` 1 / 3 / 6 / 12
  (default 1), ``tenor_months`` a multiple of it (default 12, at most 120).  The financing spread is the PB schedule's
  ``trs_spread × otc_spread_mult`` — a dealer term, never a client input.  Dates: start = the trade date rolled FOLLOWING;
  reset dates = start + k × reset_months rolled MODIFIED FOLLOWING on the trading calendar; maturity = the last reset date.
- TRS BOOKING: ``reset_px`` = the underlying's observed mark, executable-fresh (delayed) while its regular session is open (409
  QUOTE_STALE / NO_QUOTE otherwise; the preview first asks the quote worker for it at ORDER priority and the ticket asks again by
  itself); outside the session the OFFICIAL close of the last session that closed (the recorded CLOSE, else the print observed at the
  close - never the last print before 16:00 shown as the close; 409 CLOSE_PENDING with retry_ms until it is recorded: V104R2-OTC-02);
  an accepted dealer quote (C14, ``ctx.rfq_firm``) books its quoted reset price; notional = ``to_c(units × reset_px)``; IM = ``to_c(trs_im_pct × notional)`` (the PB
  schedule's ``prime.im_params`` once it exists, else 10 %, disclosed) ``Dr 1330 / Cr 1010`` after
  ``swaps.fund_commitment`` (PO-07: against the account's own cash net of its payables and working orders, then one Treasury
  sweep, then PB financing; 409 INSUFFICIENT_FUNDS refuses); ``mark_c`` 0 — the dealer's charge is in the financing spread.
- TRS MARK (SWAP_MARK): ``sign × to_c(units × (S − reset_px))``, sign +1 receiver / −1 payer, S = the underlying's current
  mark (its quality and time in ``inputs``); no observed price → the stored mark as a ``MarkView`` labelled stale (SWAP_VM
  then skips NO_MARK); a stale price → a stale ``MarkView`` of the mark at that price.  DV01 0 (the financing resets daily).
- TRS FINANCING (ACCRUE_DAILY kind ``trsfin``, idem ``accr:trsfin:{contract}:{night}``): every calendar night from the start
  date to the night before maturity, ``notional × r / 360`` cumulatively per segment (``accrue_segment``, ACCRUAL), r = SOFR
  (RATE_LAG: the latest print ≤ the night, at most 10 days old, never defaulted) + spread for the receiver and SOFR − spread
  for the payer: the dealer's spread goes against the client on both sides, as the IRS half-width does (DEVIATION from
  §13.12 "reversed for the payer", which would pay a short synthetic more than the PB charges a long one).  Owed by us →
  ``Dr 4451 / Cr 2340``; owed to us → ``Dr 1225 / Cr 4451`` (4451 = the swap financing leg, a financing GL, FINSIM1 C11).  A
  night of a period whose opening reset has not run yet raises RESET_PENDING (the night accrues on that reset's notional, so it
  waits).  The night of an as-of contract before its entered day posts with business date night + 1 (D7).
- TRS DIVIDENDS (DIVIDEND): every ``dividends`` row of the underlying with an ex-date in (start, the night or the reset date]
  not yet accrued posts once (idem ``trsdiv:{contract}:{ex_date}``, business date the ex-date): ``to_c(units × amount)``;
  receiver ``Dr 1225 / Cr 4452``, payer ``Dr 4452 / Cr 2340`` (4452 = dividend equivalents, an income GL, FINSIM1 C11).  A row that only appears after its period's reset is accrued
  when it is seen and paid at the next reset (disclosed).  No row → nothing; a dividend is never invented.
- TRS_RESET (16:25 ET on each reset date, key ``{contract}:{date}``, idem ``trsreset:{contract}:{date}``): needs the
  underlying's OFFICIAL CLOSE of that date — the recorded ``marks`` row (kind CLOSE), else the observed print stamped on that
  date within [session close, close + 15 min], never a print before the close (POSTCLOSE-01: a 15:59:59 print is not the
  close) — else SKIPPED CLOSE_PENDING (WARN, retried); needs the financing accrued
  through the night before (else SKIPPED ACCRUALS_PENDING).  Then ONE entry: the return leg ``sign × to_c(units × (S_close −
  reset_px))`` ``Dr 1010 / Cr 4450`` or reverse (STM: less the VM reference already settled daily), the accrued 1225 received
  ``Dr 1010 / Cr 1225``, the accrued 2340 paid ``Dr 2340 / Cr 1010``, the CTM VM balance unwound (``Dr 2330 / Cr 1010`` or
  ``Dr 1010 / Cr 1320``); a net payment is funded first (``execution.fund``, §11.6 overdraft rule).  Then ``reset_px :=
  S_close``, notional ``:= to_c(units × S_close)``, ``mark_c := 0``, ``vm_balance_c := 0`` and an ``otc_resets`` RESET row
  with ``detail.post_mark_c`` 0 re-bases the VM reference (WP-17's SWAP_VM rule), so nothing is paid twice.  The reset on the
  maturity date matures the contract (``swaps.close_contract``: IM released, status MATURED).
- XCCY TERMS: ``pay_ccy`` / ``recv_ccy`` (one USD, the other a currency of ``tables.POLICY_INDEX``), ``pay_notional_c`` /
  ``recv_notional_c`` (or decimal strings ``pay_notional`` / ``recv_notional``; give one, the other is struck at the deal FX
  rate; both must agree at that rate to the cent), ``pay_rate`` / ``recv_rate`` (fixed decimal fractions on the 0.001 % grid;
  blank = the PB's quote), ``pay_basis`` / ``recv_basis`` 30/360 (default) or ACT/360, ``months`` (the tenor, a multiple of 6:
  semi-annual coupons on both legs), ``mtm_reset`` (default false).  Floating legs answer 400 NOT_IN_THIS_BUILD (no non-USD
  fixing is sourced).
- XCCY DIRECTION (DEVIATION from §13.12 / §19.16, whose start lines pay the pay-leg currency): legs are named by their
  coupons, as the ticket and the market name them.  We LEND the receive-leg currency (pay its notional at the start, 1360
  receivable, receive its coupons, get it back at maturity) and BORROW the pay-leg currency (receive its notional at the
  start, 2360 payable, pay its coupons, repay it at maturity).  The design's literal lines would receive interest on the
  borrowed currency and pay it on the lent one — a free lunch no dealer gives.
- XCCY DEAL FX (PO-08): the pair is asked for at ORDER priority at every preview / booking; the observed FX mark (``fx()``:
  USD per unit; executable-fresh or CLOSE, else 409 QUOTE_STALE / NO_QUOTE) is the deal rate - when only the ECB reference
  rate of an earlier day exists (the live pair not fetched yet) the deal is refused 409 FX_STALE, never booked at it.  The
  preview, the confirm and the memos carry the rate's source and time (``deal_rate_source``).  The foreign lines of the start
  exchange carry it as their posting rate, so each leg balances in its own currency and in base (L1).
- XCCY QUOTES AND MARK: each leg is a fixed leg valued against its own curve — the USD leg on the live par curve
  (``swaps.dfs_for``), the foreign leg on a FLAT curve at that currency's policy rate (FRED via ``tables.POLICY_INDEX``;
  monthly proxies labelled) — a disclosed model: there is no free non-USD swap curve.  Par and annuity as
  ``swaps.par_annuity`` on the semi-annual grid of the remaining periods.  The PB quotes the receive leg at par − hw and the
  pay leg at par + hw (hw = ``irs_half_width_bp × otc_spread_mult``, the IRS rule); a client rate better than the quote is
  refused 400 OFF_MARKET (no fabricated day-one gain).  Mark = Σ legs ``sign × N × (K − par) × A × fx`` (+ receive, − pay;
  fx = 1 for USD, ``fx()`` otherwise).  The notionals themselves are on the balance sheet (1360 / 2360 in their currencies,
  revalued by the FX result), so the mark leaves them out — adding "the unreset notional FX effect" (§13.12) would count it
  twice in NAV (DEVIATION).  ``mark0`` = the mark at the dealt rates (≤ 0: the cost of dealing); DV01 = the PV change for
  +1 bp on the USD par curve (the risk module's rates scenario; the foreign leg's own-curve DV01 is in the preview).
- XCCY IM: ``to_c(xccy_im_pct × the USD notional)`` (``prime.im_params``, else 2 %), ``Dr 1330 / Cr 1010`` with
  ``swaps.fund_commitment``.
- XCCY DATES (P8B-06 / OTC-03): the trade date is the FX trade date (the FX day rolls at 17:00 ET) and the swap STARTS on the
  pair's spot date from it (T+2, USD/CAD T+1, ``xccy_dates``) - the initial exchange settles like any FX deal of that trade date,
  never T+0 nor on the calendar date of an evening booking.  The booking moves only the IM (with its sweep, one business date) and
  checks the start commitment (``xccy_start_check``: USD lent with the IM against available now, PO-07; a foreign currency from
  what the account holds or receives by the start date - an FX desk purchase for that value date counts - else 409
  INSUFFICIENT_FUNDS_CCY with the Convert hint, never automatic FX).  The contract is marked and margined from its trade date
  (``swaps.vm_first_date``).
- XCCY START (idem ``xccy:{contract}:start``): the XCCY_EVENTS START key from 00:45 ET of the start date (spot): lent leg
  ``Dr 1360 / Cr 1010``, borrowed leg ``Dr 1010 / Cr 2360``, each in its own currency at the deal rate, business date the start
  date.  The lent currency is funded first: USD by ``swaps.fund_commitment``, a foreign currency by ``fx.fund_ccy`` (own cash,
  else Treasury's cash in that currency, else 409 INSUFFICIENT_FUNDS_CCY with the Convert hint — never automatic FX); a START
  that cannot be funded is SKIPPED with an ERROR attention and retried.
- XCCY COUPONS (ACCRUE_DAILY kinds ``swaprcv`` / ``swappay`` → ``swaps.accrue_night`` → ``accrue_night`` here →
  ``swaps.accrue_leg``): each leg in its own currency (base at the command's FX rate), 30/360 or ACT/360 with the period
  true-up; the USD leg of a period after an MTM reset accrues on that reset's notional (FX_RESET_PENDING until it is
  recorded); nothing accrues before the start exchange (START_PENDING).  PAY (XCCY_EVENTS ``{contract}:{date}:PAY`` from
  00:45 ET of each intermediate payment date, idem ``xccypay:{contract}:{date}``): each leg's period coupon settles in its
  own currency — receive ``Dr 1010 / Cr 1225``, pay ``Dr 2340 / Cr 1010`` (funded first; §11.6 overdraft rule) — once both
  legs are accrued through the night before (else SKIPPED ACCRUALS_PENDING).
- XCCY FX_RESET (``mtm_reset``; key ``{contract}:{date}:FX_RESET`` from 17:05 ET of each period start after the first, idem
  ``xccyfx:{contract}:{date}``): the FX CLOSE of the date (the pair's recorded ``marks`` row, else the ``fx()`` print stamped
  within [17:00 − 60 s, 17:15]; else SKIPPED CLOSE_PENDING) re-strikes the USD notional to ``to_c(foreign notional ×
  fx_close)`` and the difference settles in cash against the USD notional balance — USD lent (1360): up → we lend more
  ``Dr 1360 / Cr 1010``, down → ``Dr 1010 / Cr 1360``; USD borrowed (2360): up → we borrow more ``Dr 1010 / Cr 2360``,
  down → ``Dr 2360 / Cr 1010``.  At the reset rate the two notional balances are then worth the same in base: the notional
  FX effect is realised in cash and the reset itself moves NAV by nothing.
- XCCY END (maturity, 00:45 ET; key ``{contract}:{date}:END``): the last coupons (as PAY), the reverse exchange (idem
  ``xccy:{contract}:end``: lent back ``Dr 1010 / Cr 1360``, borrowed repaid ``Dr 2360 / Cr 1010`` — funded, overdraft rule),
  then ``swaps.close_contract`` (VM unwound, IM released, MATURED).
- TERMINATION (``otc.terminate``, owner): the accruals owed through last night are posted first; TRS at the underlying's
  executable-fresh or CLOSE price (the value since the last reset, accrued 1225 / 2340 settled) through
  ``swaps.close_contract``; XCCY at the model mark: the accrued coupons settle and the notionals are re-exchanged at face in
  their own currencies (idem ``xccy:{contract}:terminate``), then ``close_contract`` with the value.  The owner's command
  refuses (409) rather than overdraw.
- VM (LC-3): ``swaps.h_vm`` values TRS / XCCY contracts through ``mark_trs`` / ``mark_xccy`` with ``valuation_date`` = the VM
  date D, at the CLOSE of R = the last trading day of the underlying's session group (FX_SPOT for the pair) on or before D:
  the recorded ``marks`` row, else the print observed at that close (never the ECB reference rate).  Unknown -> ``(None,
  {vm_outcome})``: CAUGHT_UP when the price available is of a later session (the change is measured on a later VM date),
  else CLOSE_PENDING (retried; quiet on a non-trading D and on its first attempt) - never a price dated before D or an
  intraday print of D.  VM is collateral; the P&L is the mark overlay.  The RESET row's ``detail.command_id`` orders a reset
  that posted no entry against the VMs.
- READS (ORC-6): every read goes through the caller's connection or ``marks.shared_reader``; the marks read a snapshot of
  the one underlying / pair they need.
- AS-OF (FINSIM1 C10): ``book_as_of`` books a TRS the owner dealt on a past date at his reset price exactly (``terms.reset_px``,
  D10); start = the value date; the underlying's recorded close of the trade date and SOFR of that date are required valuation
  inputs (409 ASOF_INPUT_MISSING otherwise; the day-0 VM ``(reset_px - close) x units`` with the side's sign is posted by
  ``swaps.h_book_as_of`` on the value date).  Every payment to the counterparty (a reset, an XCCY payment) is funded with CSA
  netting (C21) under ``ctx.asof_date`` = its own date.
- JOBS: the handlers write their own ``jobs_done`` row (DONE, or SKIPPED with a note — quiet for "not yet"); ``reset_keys`` /
  ``event_keys`` list every key that is not DONE.  Money is integer cents / Decimal end to end; there are no floats.
"""
from __future__ import annotations

import contextvars
import json
import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN
from typing import Callable, Dict, List, Optional, Tuple

from .. import execution, prime
from .. import fx as fxmod
from .. import marks as _marks
from ..books import account_row, eligibility as _eligibility, split_account_id
from ..clock import epoch_of, ny_of, ny_str
from ..commands import ConflictError, RejectError, TxContext
from ..db import dumps, next_id
from ..instruments import EQUITY_KINDS, fx_pair_id
from ..ledger import Entry, Line, balance, cr, dr
from ..lifecycle_jobs import accrue_segment as _core_accrue_segment, sofr_for_night
from ..quotes import MarkView
from ..tables import POLICY_INDEX, POLICY_INDEX_MONTHLY
from ..units import fmt_money, from_c, qprice, to_c
from . import swaps

JOB_RESET, JOB_EVENTS = "TRS_RESET", "XCCY_EVENTS"
SOURCE = "swaps_trs"
RESET_ET, EVENT_ET, FX_RESET_ET = "16:25", "00:45", "17:05"
TRS_IM_PCT, XCCY_IM_PCT = Decimal("0.10"), Decimal("0.02")
RESET_MONTHS = (1, 3, 6, 12)
XCCY_PERIOD_MONTHS = 6
XCCY_BASES = ("30/360", "ACT/360")
CLOSE_BEFORE_S, CLOSE_AFTER_S = 0, 900      # POSTCLOSE-01: the official close is a print at or after the session close, never before it
SESSION_LOOKBACK_DAYS = 10            # the last trading day on or before a VM date is looked for this far back
PIN_TTL_S = 300                       # PO-08: a previewed underlying / FX pair is fetched at ORDER priority for 5 minutes
EVENT_ORDER = {"START": 0, "PAY": 1, "FX_RESET": 2, "END": 3}
USABLE_QUALITIES = ("live", "delayed", "close")
RATE_Q, DF_Q, GRID_Q, BP, HALF = swaps.RATE_Q, swaps.DF_Q, swaps.GRID_Q, swaps.BP, swaps.HALF
ONE = Decimal(1)
CONVENTIONS = ("SWAP_MARK", "SWAP_VM", "SWAP_RESET", "ACCRUAL", "RATE_LAG", "DIVIDEND", "FX_RESULT", "ELIGIBILITY")
TRS_MODEL = "mark = sign x units x (S - reset_px): the underlying's observed price since the last reset"
XCCY_MODEL = ("mark = sum over legs of sign x N x (K - par) x annuity x fx (+ receive, - pay); USD leg on the par curve, foreign leg on a flat"
              " curve at its policy rate; the notionals are on the balance sheet (1360 / 2360) and are not in the mark")

Period = Tuple[date, date, date]


class NotFound(RejectError):
    http = 404


# ---- small helpers ----------------------------------------------------------------------------------------------------------------
def _date(x) -> date:
    return x if isinstance(x, date) else date.fromisoformat(str(x))


def _services(ctx: TxContext, services=None):
    s = services if services is not None else getattr(ctx, "services", None)
    if s is None:
        raise RejectError("NO_SERVICES", "the command context carries no services bundle")
    return s


def _rowd(r) -> Optional[dict]:
    if r is None:
        return None
    return dict(r) if isinstance(r, dict) else {k: r[k] for k in r.keys()}


def _terms(row: dict) -> dict:
    try:
        return json.loads(row.get("terms_json") or "{}")
    except (ValueError, TypeError):
        return {}


def _detail(r) -> dict:
    try:
        return json.loads(r["detail_json"] or "{}")
    except (ValueError, KeyError, TypeError):
        return {}


def _pct(rate) -> str:
    return f"{(Decimal(rate) * 100).quantize(Decimal('0.0001'))} %"


def _periods(terms: dict) -> List[Period]:
    return [(_date(s), _date(e), _date(p)) for s, e, p in (terms.get("periods") or [])]


def _period_index(periods: List[Period], d: date) -> Optional[int]:
    for i, (s, e, _p) in enumerate(periods):
        if s <= d < e:
            return i
    return None


def _contract(conn: sqlite3.Connection, cid: str) -> dict:
    r = conn.execute("SELECT * FROM otc_contracts WHERE contract_id=?", (cid,)).fetchone()
    if r is None:
        raise NotFound("NOT_FOUND", f"contract {cid} does not exist", {"contract_id": cid})
    return _rowd(r)


def _contract_of(services, contract, conn: Optional[sqlite3.Connection] = None) -> Optional[dict]:
    """A contract row dict from a row / dict / id / SWAP instrument (a dict without terms is re-read by its id)."""
    if contract is None:
        return None
    if isinstance(contract, sqlite3.Row):
        return _rowd(contract)
    if isinstance(contract, dict):
        if "terms_json" in contract:
            return dict(contract)
        cid = str(contract.get("contract_id") or "")
    else:
        cid = getattr(contract, "instrument_id", None) or str(contract)
    with _marks.shared_reader(services.db, conn) as c:        # ORC-6: the held connection, never a nested pooled reader
        return _rowd(c.execute("SELECT * FROM otc_contracts WHERE contract_id=?", (cid,)).fetchone())


def _pb_acct(conn: sqlite3.Connection, account_id: str) -> dict:
    acct = account_row(conn, account_id)
    if acct is None:
        raise NotFound("ACCOUNT_UNKNOWN", f"account {account_id} does not exist", {"account_id": account_id})
    acct = _rowd(acct)
    if acct["status"] != "OPEN":
        raise RejectError("ACCOUNT_CLOSED", f"account {account_id} is closed")
    if acct["kind"] != "PB":
        raise RejectError("NOT_TRADEABLE_HERE", f"{account_id} is a {acct['kind']} account: OTC swaps are booked in a prime-broker account (PB page, OTC tab)",
                          {"account_id": account_id, "kind": acct["kind"]})
    return acct


def _flag(v, name: str) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, int) and v in (0, 1):
        return bool(v)
    s = str(v if v is not None else "").strip().lower()
    if s in ("true", "1", "yes"):
        return True
    if s in ("false", "0", "no"):
        return False
    raise RejectError("TERMS", f"{name} must be true or false, got {v!r}")


def _whole(v, name: str, lo: int, hi: int) -> int:
    if v is None or isinstance(v, (bool, float)):
        raise RejectError("TERMS", f"{name} must be a whole number, got {v!r}")
    try:
        d = Decimal(str(v).replace(",", "").strip())
    except (InvalidOperation, ValueError) as e:
        raise RejectError("TERMS", f"{name} must be a whole number, got {v!r}") from e
    if not d.is_finite() or d != d.to_integral_value():
        raise RejectError("TERMS", f"{name} must be a whole number, got {v!r}")
    n = int(d)
    if n < lo or n > hi:
        raise RejectError("TERMS", f"{name} must be between {lo} and {hi}, got {n}")
    return n


def _rate_arg(v, name: str) -> Optional[Decimal]:
    """A fixed rate as a decimal fraction on the 0.001 % grid; None (blank) = the PB's quote."""
    if v is None or (isinstance(v, str) and v.strip().lower() in ("", "quote", "par")):
        return None
    if isinstance(v, (bool, float)):
        raise RejectError("TERMS", f"{name} must be a decimal string (0.025 = 2.50 %), not a {type(v).__name__}")
    try:
        d = Decimal(str(v).strip())
    except (InvalidOperation, ValueError) as e:
        raise RejectError("TERMS", f"{name} is not a number: {v!r}") from e
    if not d.is_finite() or d <= -1 or d >= 1:
        raise RejectError("TERMS", f"{name} must be a fraction between -1 and 1 (0.025 = 2.50 %), got {v!r}")
    if d != d.quantize(RATE_Q):
        raise RejectError("TERMS", f"{name} {v!r} is finer than the dealing precision 0.001 % (0.1 bp)")
    return d.quantize(RATE_Q)


def _money_arg(raw_c, raw, name: str) -> Optional[int]:
    """Integer cents from ``{name}_c`` (integer cents) or ``{name}`` (a decimal string); None when neither is given."""
    for v in (raw_c, raw):
        if isinstance(v, (bool, float)):
            raise RejectError("TERMS", f"{name} must be a decimal string or integer cents, not a {type(v).__name__}")
    if raw_c not in (None, ""):
        try:
            d = Decimal(str(raw_c).strip())
        except (InvalidOperation, ValueError) as e:
            raise RejectError("TERMS", f"{name}_c must be integer cents, got {raw_c!r}") from e
        if not d.is_finite() or d != d.to_integral_value():
            raise RejectError("TERMS", f"{name}_c must be integer cents, got {raw_c!r}")
        c = int(d)
    elif raw not in (None, ""):
        try:
            c = to_c(str(raw))
        except (ValueError, TypeError) as e:
            raise RejectError("TERMS", f"{name} is not an amount: {raw!r}") from e
    else:
        return None
    if c <= 0:
        raise RejectError("TERMS", f"{name} must be positive")
    return c


#: C14 (FINSIM1 P4 task 8, 1.0.6): an accepted quote of a kind the dealer re-prices at booking (XCCY, the OTC options and forwards) books
#: on the market inputs it was quoted from.  While the RFQ_DEALER job prices a quote, every snapshot handed out by ``_snapshot`` records
#: the marks it serves (``RecordingMarks``) and the quote keeps them; while the acceptance books, they are served again (``PinnedMarks``,
#: also as ``ctx.marks``) - so Accept books what was quoted, never a print that arrived in the meantime.  Unset (the default), a snapshot
#: is the provider's own.
MARKS_HOOK: "contextvars.ContextVar[Optional[Callable]]" = contextvars.ContextVar("finsim_otc_marks_hook", default=None)


def mark_view_of(d: dict) -> MarkView:
    """A ``MarkView`` back from its ``to_dict()``."""
    v = d.get("value")
    return MarkView(str(d.get("instrument_id") or ""), None if v in (None, "") else Decimal(str(v)), str(d.get("quality") or "unavailable"),
                    str(d.get("source") or ""), str(d.get("method") or ""), d.get("as_of"), d.get("fetched_at"), str(d.get("delay_label") or ""),
                    d.get("inputs"), bool(d.get("executable")), str(d.get("note") or ""))


class RecordingMarks:
    """A snapshot that remembers every mark and FX view it served (the first one of each): ``store = {"mark": {id: view}, "fx": {ccy:
    view}}`` (``MarkView.to_dict()``); everything else is the base snapshot's."""

    def __init__(self, base, store: dict):
        self._base = base
        self._store = store

    def _keep(self, part: str, key, mv):
        to_dict = getattr(mv, "to_dict", None)
        if callable(to_dict):
            self._store.setdefault(part, {}).setdefault(str(key), to_dict())
        return mv

    def mark(self, instrument_id):
        return self._keep("mark", instrument_id, self._base.mark(instrument_id))

    def fx(self, ccy):
        return self._keep("fx", ccy, self._base.fx(ccy))

    def __getattr__(self, name):
        return getattr(self._base, name)


class PinnedMarks:
    """A snapshot serving a quote's recorded views (``RecordingMarks``' store) and the base snapshot for everything else."""

    def __init__(self, base, pinned: Optional[dict]):
        self._base = base
        self._mark = dict((pinned or {}).get("mark") or {})
        self._fx = dict((pinned or {}).get("fx") or {})

    def mark(self, instrument_id):
        d = self._mark.get(str(instrument_id))
        return mark_view_of(d) if d is not None else self._base.mark(instrument_id)

    def fx(self, ccy):
        d = self._fx.get(str(ccy))
        return mark_view_of(d) if d is not None else self._base.fx(ccy)

    def __getattr__(self, name):
        return getattr(self._base, name)


def _snapshot(services, ids: Optional[List[str]] = None):
    """A mark snapshot of ``ids`` only (a TRS underlying, an XCCY pair: cheap), else of everything; the provider itself when it
    has no snapshot().  Under ``MARKS_HOOK`` (C14) the hook wraps it."""
    snap = _provider_snapshot(services, ids)
    hook = MARKS_HOOK.get()
    return hook(snap) if hook is not None and snap is not None else snap


def _provider_snapshot(services, ids: Optional[List[str]] = None):
    m = getattr(services, "marks", None)
    if m is None:
        return None
    snap = getattr(m, "snapshot", None)
    if not callable(snap):
        return m
    if ids:
        try:
            return snap(list(ids))
        except TypeError:
            pass
    return snap()


def _pin(services, ids: List[str]) -> bool:
    """PO-08: ask the quote worker for these ids at ORDER priority (in memory: the next fetch tick takes them first).  A hint:
    the caller never fails on it.  True when a cache took the subscription."""
    cache = getattr(services, "cache", None)
    if cache is None or not hasattr(cache, "subscribe") or not ids:
        return False
    try:
        cache.subscribe(list(ids), PIN_TTL_S, "ORDER")
        return True
    except Exception:   # noqa: BLE001 - a subscription is a hint; a preview / booking never fails on it
        return False


def _px_text(px) -> str:
    """A price for a memo or a label with at least 2 decimals: 339.5 -> "339.50", 1.08499881 -> "1.08499881" (PO-23)."""
    s = str(qprice(px))
    whole, _dot, frac = s.partition(".")
    return f"{whole}.{(frac + '00')[:max(2, len(frac))]}"


def quote_label(mv) -> str:
    """Where an observed price comes from and when, for previews, confirms and memos (PO-08): "Yahoo, delayed, 2026-10-21 14:35:12
    ET" / "ECB reference rate, close, as of 2026-10-21"."""
    if mv is None or getattr(mv, "mark", None) is None:
        return "no observed price"
    inputs = getattr(mv, "inputs", None) or {}
    src = getattr(mv, "source", None) or "observed"
    if inputs.get("basis") == "ECB reference rate":
        src = "ECB reference rate"
    qt = getattr(mv, "quote_time", None)
    when = ny_str(int(qt)) if qt else (f"as of {inputs.get('as_of')}" if inputs.get("as_of") else "")
    return f"{src}, {getattr(mv, 'quality', '') or 'unknown'}" + (f", {when}" if when else "")


def _is_reference_rate(mv) -> bool:
    """True for the ECB reference-rate fallback ``marks.fx`` answers when the Yahoo pair has not been fetched (PO-08)."""
    inputs = getattr(mv, "inputs", None) or {}
    return mv is not None and (inputs.get("basis") == "ECB reference rate" or "ECB reference" in str(getattr(mv, "delay_label", "") or ""))


RETRY_MS = 2000                       # V104R1-06: a price being fetched for a preview -> 202 + retry_ms (the OTC preview route), the ticket asks again


def _usable(mv, what: str, fetching: bool = False) -> Decimal:
    """The observed price must be executable-fresh or a session CLOSE (409 NO_QUOTE / QUOTE_STALE otherwise); ``fetching``: the
    caller just asked the worker for it (PO-08): the refusal carries ``retry_ms``, so the preview route answers 202 'fetching'
    and the ticket asks again by itself (V104R1-06)."""
    if mv is None or mv.mark is None or mv.quality == "unavailable":
        again = "; it is being fetched now" if fetching else ""
        raise ConflictError("NO_QUOTE", f"{what}: no observed price yet ({getattr(mv, 'note', '') or 'unavailable'}){again}; nothing is booked or settled"
                            " without one", {"quality": getattr(mv, "quality", "unavailable"), "fetching": fetching, "retry_ms": RETRY_MS if fetching else None})
    if mv.quality not in USABLE_QUALITIES:
        when = f" ({ny_str(int(mv.quote_time))})" if mv.quote_time else ""
        raise ConflictError("QUOTE_STALE", f"{what}: the last price {mv.mark} is {mv.quality}{when}; it must be a fresh or a closing print",
                            {"quality": mv.quality, "price": str(mv.mark), "quote_time": mv.quote_time})
    return qprice(mv.mark)


def _quote_dict(mv) -> Optional[dict]:
    return mv.to_dict() if mv is not None and hasattr(mv, "to_dict") else None


def _accrue_segment_fn(services):
    lc = getattr(services, "lifecycle", None)
    fn = getattr(lc, "accrue_segment", None) if lc is not None else None
    return fn if fn is not None else _core_accrue_segment


def _state(conn: sqlite3.Connection, kind: str, ref: str):
    return conn.execute("SELECT * FROM accrual_state WHERE kind=? AND ref_id=?", (kind, ref)).fetchone()


def _resets(conn: sqlite3.Connection, cid: str, kind: str) -> Dict[str, sqlite3.Row]:
    return {r["event_date"]: r for r in conn.execute("SELECT * FROM otc_resets WHERE contract_id=? AND kind=? ORDER BY event_date", (cid, kind)).fetchall()}


def _has_reset(conn: sqlite3.Connection, cid: str, kind: str, ds: Optional[str] = None) -> bool:
    if ds is None:
        return conn.execute("SELECT 1 FROM otc_resets WHERE contract_id=? AND kind=? LIMIT 1", (cid, kind)).fetchone() is not None
    return conn.execute("SELECT 1 FROM otc_resets WHERE contract_id=? AND kind=? AND event_date=?", (cid, kind, ds)).fetchone() is not None


# ---- jobs plumbing ------------------------------------------------------------------------------------------------------------------
def _job_done(ctx: TxContext, job: Optional[str], key: str, status: str, note: str) -> None:
    if not job:
        return
    ctx.conn.execute("INSERT INTO jobs_done(job, key, run_ts, command_id, status, note) VALUES (?,?,?,?,?,?)"
                     " ON CONFLICT(job, key) DO UPDATE SET run_ts=excluded.run_ts, command_id=excluded.command_id, status=excluded.status, note=excluded.note",
                     (job, key, ctx.ts, ctx.command_id, status, (note or "")[:500]))


def _resolve(ctx: TxContext, codes: Tuple[str, ...], ref_id: str) -> None:
    for code in codes:
        ctx.conn.execute("UPDATE attention SET resolved_ts=? WHERE code=? AND ref_id=? AND resolved_ts IS NULL", (ctx.ts, code, ref_id))


def _skip(ctx: TxContext, job: Optional[str], key: str, code: str, message: str, aid: str, cid: str, details: Optional[dict] = None,
          quiet: bool = False, severity: str = "WARN") -> dict:
    """A pending outcome: jobs_done SKIPPED (the key stays due), one open attention per (code, key) unless ``quiet``, an event."""
    _job_done(ctx, job, key, "SKIPPED", f"{code}: {message}")
    attention_id = None
    if not quiet:
        existing = ctx.conn.execute("SELECT id FROM attention WHERE code=? AND ref_id=? AND resolved_ts IS NULL", (code, key)).fetchone()
        attention_id = int(existing["id"]) if existing else ctx.attention(severity, SOURCE, code, message, dict(details or {}, account_id=aid, ref_type="otc",
                                                                                                                 ref_id=key, auto_resolve=1))
    ctx.event("JOB_SKIPPED", account_id=aid, instrument_id=cid, ref_type="job", ref_id=key, job=job, code=code, message=message)
    return {"status": "SKIPPED", "skipped": True, "retry": True, "quiet": quiet, "code": code, "message": message, "note": f"{code}: {message}", "key": key,
            "attention_id": attention_id, "posted": False}


def _done_noop(ctx: TxContext, job: Optional[str], key: str, code: str, note: str, **extra) -> dict:
    _job_done(ctx, job, key, "DONE", f"{code}: {note}")
    return dict({"status": "DONE", "skipped": True, "posted": False, "code": code, "note": note, "key": key}, **extra)


def _key(payload: dict, what: str) -> Tuple[str, str, Optional[str]]:
    """``(contract_id, ISO date, event)`` from the payload fields or from ``key`` = ``{contract}:{date}[:{event}]``."""
    cid = str(payload.get("contract_id") or "")
    ds = str(payload.get("date") or "")
    ev = payload.get("event")
    key = payload.get("key")
    if key and not (cid and ds):
        parts = str(key).split(":")
        if len(parts) >= 2:
            cid, ds = parts[0], parts[1]
        if len(parts) >= 3 and not ev:
            ev = parts[2]
    try:
        d = date.fromisoformat(ds)
    except ValueError as e:
        raise RejectError("KEY_INVALID", f"{what} needs contract_id and an ISO date (got {ds!r})") from e
    if not cid:
        raise RejectError("KEY_INVALID", f"{what} needs a contract_id")
    return cid, d.isoformat(), (str(ev).strip().upper() if ev else None)


def _vm_reference(conn: sqlite3.Connection, cid: str) -> int:
    """WP-17's SWAP_VM reference (``swaps`` module docstring SWAP_VM): ONE definition, ``swaps._reference_mark_c`` (the mark of
    the latest VM, or the ``post_mark_c`` of a RESET whose command ran after it; 0 before the first VM)."""
    return swaps._reference_mark_c(conn, cid)


def _fund(ctx: TxContext, services, aid: str, ccy: str, need_c: int, link_type: str, link_id: str, overdraft: bool, commit: bool = False,
          csa: Optional[bool] = None) -> dict:
    """Make ``need_c`` of ``ccy`` settled in ``aid`` before a payment: USD through ``execution.fund`` (own cash, one Treasury
    sweep, PB financing, then 409 or the overdraft rule), a foreign currency through ``fx.fund_ccy`` (own cash, Treasury's
    cash in that currency, else 409 INSUFFICIENT_FUNDS_CCY with the Convert hint; never automatic FX).  ``overdraft``
    (lifecycle obligations, §11.6): an unfundable foreign payment is still posted under an open ERROR FUNDING_SHORTFALL.
    ``commit`` (the owner's IM, start exchange and termination payments, PO-07): USD is measured against the account's
    available cash (``swaps.fund_commitment``), never against cash a pending settlement already needs.  ``csa`` (C21; default: every
    lifecycle payment, not a commitment): a payment to the CSA counterparty may use the collateral it posted."""
    need_c = int(need_c)
    if need_c <= 0:
        return {"need_c": 0, "funded": True, "source": "none"}
    csa = (not commit) if csa is None else bool(csa)
    if ccy == "USD" and commit:
        return swaps.fund_commitment(ctx, services, aid, "USD", need_c, link_type, link_id, csa_netting=csa).to_dict()
    if ccy == "USD":
        return execution.fund(ctx, aid, "USD", need_c, link_type, link_id, None, immediate=True, overdraft=overdraft, services=services,
                              **swaps._csa_kw(csa)).to_dict()
    try:
        return fxmod.fund_ccy(ctx, aid, ccy, need_c, (link_type, link_id), services=services).to_dict()
    except ConflictError as e:
        if not overdraft or e.code != "INSUFFICIENT_FUNDS_CCY":
            raise
        d = dict(e.details or {})
        short = int(d.get("shortfall_c") or need_c)
        ctx.attention("ERROR", "execution", "FUNDING_SHORTFALL",
                      f"{aid}: {ccy} {fmt_money(short)} for {link_type} {link_id} could not be funded ({e.message}); the obligation is posted and settled {ccy}"
                      " is overdrawn", dict(d, amount_c=short, currency=ccy, account_id=aid, ref_type=link_type, ref_id=link_id, auto_resolve=1))
        ctx.event("ATTENTION", account_id=aid, ref_type=link_type, ref_id=link_id, code="FUNDING_SHORTFALL", amount_c=short, currency=ccy)
        return dict(d, need_c=need_c, funded=False, unfunded_c=short, source="overdraft")


def _insert_instrument(services, ctx: TxContext, cid: str, name: str, maturity: date, spec: dict, underlying_id: Optional[str] = None) -> None:
    master = services.instruments
    insert = getattr(master, "insert", None) or getattr(master, "_insert")
    insert(ctx.conn, ctx.ts, instrument_id=cid, kind="SWAP", symbol=cid, name=name, currency="USD", multiplier="1", tick="0.01", lot=1, price_scale="1",
           session_group=swaps.SESSION_GROUP, settle_days=0, account_kinds=["PB"], expiry=maturity.isoformat(), underlying_id=underlying_id, spec=spec,
           support="COMPUTED", status="ACTIVE")
    master.refresh(cid, ctx.conn)


def _open_contracts(conn: sqlite3.Connection, kind: str) -> List[dict]:
    rows = conn.execute("SELECT c.* FROM otc_contracts c JOIN accounts a ON a.account_id=c.account_id JOIN lifetimes l ON l.lifetime_id=a.lifetime_id"
                        " WHERE c.status='OPEN' AND a.status='OPEN' AND l.status='OPEN' AND c.kind=? ORDER BY c.contract_id", (kind,)).fetchall()
    return [_rowd(r) for r in rows]


def _jobs(conn: sqlite3.Connection, job: str, cid: str) -> Dict[str, sqlite3.Row]:
    return {r["key"]: r for r in conn.execute("SELECT key, status, note FROM jobs_done WHERE job=? AND key>=? AND key<?", (job, f"{cid}:", f"{cid};")).fetchall()}


def _close_from_view(mv, d: date, close_ts: int, label: str) -> Tuple[Optional[Decimal], dict]:
    """The observed print as the official CLOSE of ``d``: stamped on ``d`` within [close, close + 15 min] (never a print before the
    session close: POSTCLOSE-01), not stale."""
    if mv is None or mv.mark is None:
        return None, {"note": f"no observed {label} print"}
    qt = mv.quote_time
    if qt is None or ny_of(int(qt)).date() != d or int(qt) < close_ts - CLOSE_BEFORE_S or int(qt) > close_ts + CLOSE_AFTER_S:
        when = ny_str(int(qt)) if qt is not None else "no time"
        return None, {"note": f"the last {label} print ({when}) is not the close of {d.isoformat()} (session close {ny_str(close_ts)})"}
    if mv.quality in ("stale", "unavailable"):
        return None, {"note": f"the {label} print at the close is {mv.quality}"}
    if _is_reference_rate(mv):
        return None, {"note": f"only the ECB reference rate is available for {label}, not its print at the close"}
    px = qprice(mv.mark)
    return px, {"price": str(px), "source": mv.source, "method": "direct", "quote_time": int(qt), "quality": mv.quality,
                "basis": "the observed print at the session close"}


def close_on(services, conn: sqlite3.Connection, marks, inst, d: date) -> Tuple[Optional[Decimal], dict]:
    """The underlying's CLOSE of ``d``: the recorded ``marks`` row (kind CLOSE), else the observed print at the close; ``(None,
    {note})`` otherwise — never a default."""
    ds = d.isoformat()
    r = conn.execute("SELECT price, quote_time, source, method, note FROM marks WHERE instrument_id=? AND mark_date=? AND kind='CLOSE'",
                     (inst.instrument_id, ds)).fetchone()
    if r is not None:
        px = qprice(r["price"])
        return px, {"price": str(px), "source": r["source"], "method": r["method"], "quote_time": r["quote_time"], "note": r["note"],
                    "basis": "recorded CLOSE (marks)"}
    try:
        close_ts = services.sessions.session_close(inst.session_group, d)
    except KeyError:
        close_ts = None
    if close_ts is None:
        return None, {"note": f"{ds} is not a trading day of {inst.session_group}"}
    return _close_from_view(marks.mark(inst.instrument_id) if marks is not None else None, d, int(close_ts), inst.instrument_id)


def fx_close_on(services, conn: sqlite3.Connection, marks, ccy: str, d: date) -> Tuple[Optional[Decimal], dict]:
    """The FX CLOSE of ``d`` as USD per unit of ``ccy``: the pair's recorded ``marks`` row, else the ``fx()`` print at 17:00 ET."""
    ds = d.isoformat()
    pair = fx_pair_id(ccy)
    r = conn.execute("SELECT price, quote_time, source, method, note FROM marks WHERE instrument_id=? AND mark_date=? AND kind='CLOSE'", (pair, ds)).fetchone()
    if r is not None:
        px = qprice(r["price"])
        usd_per = qprice(ONE / px) if pair.startswith("USD") else px
        return usd_per, {"pair": pair, "price": str(px), "usd_per_unit": str(usd_per), "source": r["source"], "method": r["method"], "quote_time": r["quote_time"],
                         "basis": "recorded CLOSE (marks)"}
    try:
        close_ts = services.sessions.session_close("FX_SPOT", d)
    except KeyError:
        close_ts = None
    if close_ts is None:
        return None, {"note": f"{ds} is not an FX trading day"}
    px, info = _close_from_view(marks.fx(ccy) if marks is not None else None, d, int(close_ts), pair)
    if px is not None:
        info.update(pair=pair, usd_per_unit=str(px))
    return px, info


def last_session_day(services, group: str, d: date) -> Optional[Tuple[date, int]]:
    """``(the last trading day of the session group on or before d, its session close epoch)``; None within no
    SESSION_LOOKBACK_DAYS."""
    for i in range(SESSION_LOOKBACK_DAYS + 1):
        day = d - timedelta(days=i)
        try:
            close = services.sessions.session_close(group, day)
        except KeyError:
            close = None
        if close is not None:
            return day, int(close)
    return None


def _vm_close(services, label: str, group: str, d: date, close_fn, view) -> Tuple[Optional[Decimal], dict]:
    """LC-3: the CLOSE that values the variation margin of date ``d`` - the close of R, the last trading day of ``group`` on or
    before ``d`` (``close_fn(R)``: the recorded ``marks`` row, else the print observed at that close).  When it is not known:
    ``vm_outcome`` CAUGHT_UP if the observed price is of a later session (the change is measured on a later VM date), else
    CLOSE_PENDING (retried) - never a price dated before ``d`` or an intraday print of it.  ``quiet`` for a non-trading day
    (the trading day's key carries the warning)."""
    got = last_session_day(services, group, d)
    if got is None:
        return None, {"vm_outcome": "CLOSE_PENDING", "note": f"no {group} trading day within {SESSION_LOOKBACK_DAYS} days on or before {d.isoformat()}",
                      "quiet": False}
    r, _close_ts = got
    px, info = close_fn(r)
    if px is not None:
        return px, dict(info, close_date=r.isoformat(), quality="close")
    qt = getattr(view, "quote_time", None) if view is not None else None
    if view is not None and getattr(view, "mark", None) is not None and qt is not None and ny_of(int(qt)).date() > r and not _is_reference_rate(view):
        return None, {"vm_outcome": "CAUGHT_UP", "close_date": r.isoformat(),
                      "note": f"no {label} close of {r.isoformat()} was recorded or observed and the price available is of a later session ({ny_str(int(qt))})"}
    # POSTCLOSE-03 (the same root cause as the CDX proxy): quiet until two hours after the session close - the official close is normally
    # recorded by then; the same tick's second pass never warns about a close that is minutes late
    return None, {"vm_outcome": "CLOSE_PENDING", "close_date": r.isoformat(), "quiet": r != d, "quiet_until": int(_close_ts) + 7200,
                  "note": f"no {label} official close of {r.isoformat()} recorded or observed yet ({info.get('note') or 'no print at the close'})"}


# ---- TRS: terms, deal, preview, booking ---------------------------------------------------------------------------------------------
@dataclass
class TrsTerms:
    underlying_id: str
    units: int
    receiver: bool
    reset_months: int
    tenor_months: int

    def to_dict(self) -> dict:
        return {"underlying_id": self.underlying_id, "units": self.units, "receiver": self.receiver, "side": "RECEIVE" if self.receiver else "PAY",
                "reset_months": self.reset_months, "tenor_months": self.tenor_months}


def parse_trs_terms(terms: dict) -> TrsTerms:
    """Validate the §13.12 TRS term dictionary (400 TERMS); module docstring TRS TERMS."""
    t = dict(terms or {})
    uid = str(t.get("underlying_id") or t.get("underlying") or t.get("symbol") or "").strip().upper()
    if not uid:
        raise RejectError("TERMS", "underlying_id is required (a US-listed equity or ETF, e.g. AAPL)")
    units = _whole(t.get("units"), "units", 1, 1_000_000_000)
    if "receiver" in t:
        receiver = _flag(t.get("receiver"), "receiver")
    elif "side" in t:
        side = str(t.get("side") or "").strip().upper()
        if side in ("RECEIVE", "RECEIVER", "RECEIVE_TOTAL_RETURN"):
            receiver = True
        elif side in ("PAY", "PAYER", "PAY_TOTAL_RETURN"):
            receiver = False
        else:
            raise RejectError("TERMS", f"side must be RECEIVE or PAY, got {t.get('side')!r}")
    else:
        raise RejectError("TERMS", "receiver is required (true = receive the total return and pay financing; false = pay it and receive financing)")
    reset_months = _whole(t.get("reset_months", 1), "reset_months", 1, 12)
    if reset_months not in RESET_MONTHS:
        raise RejectError("TERMS", f"reset_months must be one of {RESET_MONTHS}, got {reset_months}")
    tenor = _whole(t.get("tenor_months", 12), "tenor_months", reset_months, 120)
    if tenor % reset_months:
        raise RejectError("TERMS", f"tenor_months {tenor} must be a multiple of reset_months {reset_months}")
    if t.get("spread") not in (None, ""):
        raise RejectError("TERMS", "the financing spread is the prime broker's (PB schedule trs_spread x otc_spread_mult): it is not a ticket input",
                          {"field": "spread"})
    return TrsTerms(uid, units, receiver, reset_months, tenor)


def fin_rate(sofr_pct, spread, receiver: bool) -> Decimal:
    """The TRS financing rate as a decimal fraction: SOFR + spread (paid by a receiver) or SOFR − spread (received by a payer)."""
    base = Decimal(str(sofr_pct)) / Decimal(100)
    return qprice(base + Decimal(spread)) if receiver else qprice(base - Decimal(spread))


@dataclass
class TrsDeal:
    acct: dict
    terms: TrsTerms
    inst: object
    quote: MarkView
    reset_px: Decimal
    notional_c: int
    trs_spread: Decimal
    mult: Decimal
    spread: Decimal
    start: date
    maturity: date
    periods: List[Period]
    im_c: int
    margin_mode: str
    sofr: Optional[Tuple[date, Decimal]]
    im_pct: Decimal = TRS_IM_PCT
    im_source: str = "static (disclosed)"

    def rate(self) -> Optional[Decimal]:
        return None if self.sofr is None else fin_rate(self.sofr[1], self.spread, self.terms.receiver)


def _trs_underlying(services, conn: sqlite3.Connection, uid: str):
    inst = services.instruments.get(uid, conn)
    if inst is None:
        raise RejectError("UNKNOWN_UNDERLYING", f"{uid} is not in the instrument master (resolve it on the Markets page first)", {"underlying_id": uid})
    if inst.kind not in EQUITY_KINDS:
        raise RejectError("UNDERLYING_UNSUPPORTED", f"a TRS needs a US-listed equity / ETF / ADR underlying (dividends from Yahoo events); {uid} is {inst.kind}",
                          {"underlying_id": uid, "kind": inst.kind})
    if inst.currency != "USD":
        raise RejectError("CURRENCY_UNSUPPORTED", f"{uid} is quoted in {inst.currency}: TRS underlyings are USD-quoted in this build",
                          {"underlying_id": uid, "currency": inst.currency})
    if inst.status != "ACTIVE":
        raise RejectError("UNDERLYING_UNSUPPORTED", f"{uid} is {inst.status}: a TRS needs an active listing", {"underlying_id": uid, "status": inst.status})
    return inst


def _official_close_price(services, conn: sqlite3.Connection, marks, inst, now: int) -> Optional[Tuple[Decimal, MarkView]]:
    """V104R2-OTC-02: outside the underlying's regular session the reset price of a new TRS is the OFFICIAL close of the last session that
    has closed (the recorded CLOSE, else the print observed at the close - POSTCLOSE-01's rule, never the last print before 16:00 shown as
    the close); None while the session is open (a fresh print deals).  409 CLOSE_PENDING (retry_ms: the ticket asks again by itself) when
    that close is not recorded or observed yet."""
    sessions = getattr(services, "sessions", None)
    if sessions is None or not hasattr(sessions, "state"):
        return None
    try:
        st = sessions.state(inst.session_group, int(now))
    except KeyError:
        return None
    if getattr(st, "open", True):
        return None
    got = last_session_day(services, inst.session_group, ny_of(int(now)).date())
    if got is not None and got[1] > int(now):                       # before today's open: the session that closed last is an earlier one
        got = last_session_day(services, inst.session_group, got[0] - timedelta(days=1))
    if got is None:
        return None
    day, close_ts = got
    px, info = close_on(services, conn, marks, inst, day)
    if px is None:
        raise ConflictError("CLOSE_PENDING", f"{inst.instrument_id}: the market is closed and the official close of {day.isoformat()} is not recorded yet"
                            f" ({info.get('note') or 'no print at the close'}); the reset price is that close, never the last print before it - it is"
                            " being recorded now", {"close_date": day.isoformat(), "retry_ms": RETRY_MS, "underlying_id": inst.instrument_id})
    mv = MarkView(inst.instrument_id, px, "close", info.get("source") or "the official close", info.get("method") or "close", info.get("quote_time") or close_ts,
                  int(now), "close", dict(info, close_date=day.isoformat()), False, f"the official close of {day.isoformat()}")
    return px, mv


def _trs_deal(services, conn: sqlite3.Connection, marks, acct: dict, terms: TrsTerms, today: date, now: Optional[int] = None,
              firm: Optional[dict] = None) -> TrsDeal:
    """The TRS deal of ``today``: the reset price is the underlying's fresh print while its session is open, else its official close
    (V104R2-OTC-02); ``firm`` (C14) = the dealer's accepted quote, whose reset price is booked as quoted."""
    inst = _trs_underlying(services, conn, terms.underlying_id)
    ts = int(now if now is not None else services.clock.epoch())
    if firm and firm.get("reset_px") not in (None, ""):
        reset_px = qprice(Decimal(str(firm["reset_px"])))
        q = dict(firm.get("quote") or {})
        rid = firm.get("rfq_id") or "the quote"
        mv = MarkView(inst.instrument_id, reset_px, q.get("quality") or "close", f"the dealer's quote {rid} ({firm.get('reset_px_source') or 'observed'})",
                      "rfq_quote", q.get("quote_time") or firm.get("quoted_ts"), ts, q.get("delay_label") or "quoted",
                      {"rfq_id": firm.get("rfq_id"), "quoted_ts": firm.get("quoted_ts"), "source": firm.get("reset_px_source")}, False,
                      f"the reset price of the accepted quote {rid}")
    else:
        closed = _official_close_price(services, conn, marks, inst, ts)
        if closed is not None:
            reset_px, mv = closed
        else:
            fetching = _pin(services, [inst.instrument_id])                 # the reset price is fetched at ORDER priority (PO-08)
            mv = marks.mark(inst.instrument_id) if marks is not None else None
            reset_px = _usable(mv, f"{inst.instrument_id} (the TRS reset price)", fetching)
    notional = to_c(Decimal(terms.units) * reset_px)
    sched = prime.read_schedule(conn, acct)
    mult = qprice(Decimal(sched.otc_spread_mult))
    trs_spread = qprice(Decimal(sched.trs_spread))
    spread = qprice(trs_spread * mult)
    start = swaps.effective_date(services.cal, today)
    periods = swaps.period_schedule(services.cal, start, terms.tenor_months, terms.reset_months)
    imp = swaps.im_params(services, acct["account_id"])
    im_pct = Decimal(imp["trs_im_pct"])
    im = to_c(im_pct * from_c(notional))
    try:
        sofr = sofr_for_night(services, start)
    except RejectError:
        sofr = None
    return TrsDeal(acct, terms, inst, mv, reset_px, notional, trs_spread, mult, spread, start, periods[-1][1], periods, im, str(sched.swap_margin_mode).upper(), sofr,
                   im_pct, imp["source"])


def _trs_dict(deal: TrsDeal) -> dict:
    t = deal.terms
    rate = deal.rate()
    rows = []
    for s, e, _p in deal.periods:
        nights = (e - s).days
        rows.append({"date": e.isoformat(), "event": "MATURITY" if e == deal.maturity else "RESET", "period": [s.isoformat(), e.isoformat()], "nights": nights,
                     "financing_c": None if rate is None else to_c(from_c(deal.notional_c) * rate * Decimal(nights) / Decimal(360)), "indicative": True})
    sign = "+" if t.receiver else "-"
    fin = {"index": "SOFR", "basis": "ACT/360", "spread": str(deal.spread), "spread_pct": _pct(deal.spread), "direction": "paid" if t.receiver else "received",
           "formula": f"notional x (SOFR {sign} {_pct(deal.spread)}) / 360 per night (the spread goes against the client on both sides)",
           "sofr": None if deal.sofr is None else str(deal.sofr[1]), "sofr_obs_date": None if deal.sofr is None else deal.sofr[0].isoformat(),
           "rate": None if rate is None else str(rate), "rate_pct": None if rate is None else _pct(rate),
           "per_night_c": None if rate is None else to_c(from_c(deal.notional_c) * rate / Decimal(360)),
           "note": "accrues every calendar night at the latest SOFR print (RATE_LAG); the amounts per reset are indicative"
                   + ("" if deal.sofr is not None else "; no SOFR print yet: the nights wait for FRED (never defaulted)")}
    q = deal.quote
    return {"kind": "TRS", "account_id": deal.acct["account_id"], "broker": prime.broker_of(deal.acct), "currency": "USD",
            "underlying_id": deal.inst.instrument_id, "underlying_name": deal.inst.name, "units": t.units, "receiver": t.receiver,
            "side": "RECEIVE_TOTAL_RETURN" if t.receiver else "PAY_TOTAL_RETURN", "reset_px": str(deal.reset_px), "quote": _quote_dict(q),
            "reset_px_source": quote_label(q),
            "notional_c": deal.notional_c, "spread": str(deal.spread), "spread_pct": _pct(deal.spread), "trs_spread": str(deal.trs_spread),
            "otc_spread_mult": str(deal.mult), "financing": fin, "reset_months": t.reset_months, "tenor_months": t.tenor_months,
            "start_date": deal.start.isoformat(), "maturity_date": deal.maturity.isoformat(), "reset_dates": [p[1].isoformat() for p in deal.periods],
            "schedule": rows, "mark0_c": 0, "cost_of_dealing_c": 0, "dv01_c": 0,
            "dv01_note": "a TRS has no rate DV01 (its financing resets nightly); its exposure is the underlying notional",
            "im_c": deal.im_c, "im_pct": swaps.pct_text(deal.im_pct), "im_rule": f"IM = {swaps.pct_text(deal.im_pct)} of the notional ({deal.im_source})",
            "margin_mode": deal.margin_mode, "fees": "none beyond the financing spread (disclosed)", "terms": dict(t.to_dict(), spread=str(deal.spread)),
            "inputs": {"model": TRS_MODEL, "reset_px": str(deal.reset_px), "quality": getattr(q, "quality", None), "source": getattr(q, "source", None),
                       "quote_time": getattr(q, "quote_time", None)},
            "conventions": list(CONVENTIONS)}


RESET_WORDS = {1: "monthly resets", 3: "quarterly resets", 6: "semi-annual resets", 12: "annual resets"}


def _trs_deal_asof(services, conn: sqlite3.Connection, acct: dict, terms: TrsTerms, reset_px: Decimal, a: dict) -> TrsDeal:
    """C10: the deal of an as-of TRS - the owner's reset price booked exactly (D10), start = the value date (the trade date rolled to a
    business day), the trade date's recorded close and SOFR print required (409 ASOF_INPUT_MISSING otherwise)."""
    inst = _trs_underlying(services, conn, terms.underlying_id)
    td, vd = a["trade_date"], a["value_date"]
    start = swaps.effective_date(services.cal, td)
    if start != vd:
        raise ConflictError("ASOF_TERMS_MISMATCH", f"the value date {vd.isoformat()} is not the swap's start {start.isoformat()} (the trade date rolled to a business"
                            " day); nothing was written", {"differences": [{"term": "value_date", "payload": vd.isoformat(), "stored": start.isoformat()}]})
    close, info = close_on(services, conn, None, inst, td)
    if close is None:
        raise ConflictError("ASOF_INPUT_MISSING", f"no recorded {inst.instrument_id} close of {td.isoformat()} (the day-0 valuation input of the TRS); nothing was"
                            " written", {"input": "CLOSE", "instrument_id": inst.instrument_id, "date": td.isoformat(), "note": info.get("note")})
    try:
        sofr = sofr_for_night(services, start)
    except RejectError as e:
        raise ConflictError("ASOF_INPUT_MISSING", f"no SOFR print on or before {start.isoformat()} (the financing input of the TRS): {e.message}; nothing was written",
                            {"input": "SOFR", "date": start.isoformat()}) from e
    notional = to_c(Decimal(terms.units) * reset_px)
    sched = prime.read_schedule(conn, acct)
    mult = qprice(Decimal(sched.otc_spread_mult))
    trs_spread = qprice(Decimal(sched.trs_spread))
    spread = qprice(trs_spread * mult)
    periods = swaps.period_schedule(services.cal, start, terms.tenor_months, terms.reset_months)
    imp = swaps.im_params(services, acct["account_id"])
    im_pct = Decimal(imp["trs_im_pct"])
    im = to_c(im_pct * from_c(notional))
    quote = MarkView(inst.instrument_id, reset_px, "close", "the dealt reset price (as-of booking)", "dealt", a["exec_ts"], a["exec_ts"], "dealt",
                     {"close": info, "close_px": str(close), "trade_date": td.isoformat()}, False,
                     f"dealt at {_px_text(reset_px)}; the recorded close of {td.isoformat()} is {_px_text(close)}")
    return TrsDeal(acct, terms, inst, quote, reset_px, notional, trs_spread, mult, spread, start, periods[-1][1], periods, im, str(sched.swap_margin_mode).upper(), sofr,
                   im_pct, imp["source"])


def book_as_of(ctx: TxContext, payload: dict, a: dict) -> dict:
    """C10 as-of booking of a TRS (``swaps.h_book_as_of`` ran the gates and set ``ctx.asof_date`` = the value date): ``terms.reset_px``
    is the owner's dealt initial price, booked exactly; nothing else is taken from the market but the valuation inputs."""
    services = _services(ctx)
    kind = prime.require_otc_kind(payload.get("kind"))
    if kind != "TRS":
        raise ConflictError("ASOF_INPUT_MISSING", f"an as-of {kind} is not supported (no recorded inputs of its trade date); nothing was written", {"kind": kind})
    acct = _pb_acct(ctx.conn, str(payload.get("account_id") or ""))
    raw = dict(payload.get("terms") or {})
    px_raw = raw.pop("reset_px", None)
    if px_raw is None or isinstance(px_raw, (bool, float)):
        raise RejectError("TERMS", "reset_px (the dealt initial price, a decimal string) is required for an as-of TRS", {"field": "reset_px"})
    try:
        reset_px = Decimal(str(px_raw).strip())
    except (InvalidOperation, ValueError) as e:
        raise RejectError("TERMS", f"reset_px is not a price: {px_raw!r}", {"field": "reset_px"}) from e
    if not reset_px.is_finite() or reset_px <= 0:
        raise RejectError("TERMS", f"reset_px must be a positive price, got {px_raw!r}", {"field": "reset_px"})
    terms = parse_trs_terms(raw)
    deal = _trs_deal_asof(services, ctx.conn, acct, terms, qprice(reset_px), a)
    return _book_trs(ctx, services, acct, terms, deal=deal, as_of=a)


def _book_trs(ctx: TxContext, services, acct: dict, terms: TrsTerms, deal: Optional[TrsDeal] = None, as_of: Optional[dict] = None) -> dict:
    conn = ctx.conn
    aid = acct["account_id"]
    if deal is None:
        deal = _trs_deal(services, conn, ctx.marks, acct, terms, ny_of(ctx.ts).date(), now=ctx.ts, firm=getattr(ctx, "rfq_firm", None))
    broker = prime.broker_of(acct)
    cid = next_id(conn, "swap")
    uid = deal.inst.instrument_id
    idem = f"im:{cid}:open"
    funding = _fund(ctx, services, aid, "USD", deal.im_c, "otc", idem, overdraft=False, commit=True)      # PO-07: against available
    side = "receive" if terms.receiver else "pay"
    memo = (f"OTC IM {cid}: TRS {side} total return on {terms.units:,} {uid} @ {_px_text(deal.reset_px)} ({quote_label(deal.quote)}; notional"
            f" {fmt_money(deal.notional_c)} USD) to {deal.maturity.isoformat()} with {broker}; IM = {swaps.pct_text(deal.im_pct)} x notional ="
            f" {fmt_money(deal.im_c)} ({deal.im_source}); financing SOFR {'+' if terms.receiver else '-'} {_pct(deal.spread)}")
    entry_id = ctx.post(Entry("OTC_IM", memo, [dr(aid, "1330", "USD", deal.im_c, instrument_id=cid, memo="OTC initial margin"),
                                               cr(aid, "1010", "USD", deal.im_c, memo="IM paid")], idem, ref_type="otc", ref_id=cid))
    name = (f"TRS {side} {terms.units:,} {uid} @ {_px_text(deal.reset_px)} ({deal.start.isoformat()} to {deal.maturity.isoformat()},"
            f" {RESET_WORDS.get(terms.reset_months, f'resets every {terms.reset_months} months')}, {broker})")
    _insert_instrument(services, ctx, cid, name, deal.maturity, {"contract_id": cid, "otc_kind": "TRS", "broker": broker, "underlying_id": uid, "units": terms.units,
                                                                  "receiver": terms.receiver, "reset_months": terms.reset_months, "start_date": deal.start.isoformat(),
                                                                  "maturity_date": deal.maturity.isoformat(), "margin_mode": deal.margin_mode}, underlying_id=uid)
    pj = [[s.isoformat(), e.isoformat(), p.isoformat()] for s, e, p in deal.periods]
    t = dict(terms.to_dict(), kind="TRS", spread=str(deal.spread), trs_spread=str(deal.trs_spread), otc_spread_mult=str(deal.mult), financing_index="SOFR",
             financing_basis="ACT/360", periods=pj, fixed_periods=pj, trade_date=(as_of["trade_date"] if as_of else ny_of(ctx.ts).date()).isoformat(),
             start_date=deal.start.isoformat(), maturity_date=deal.maturity.isoformat(), reset_px0=str(deal.reset_px), notional0_c=deal.notional_c,
             im_pct=str(deal.im_pct), dealt_ts=(as_of["exec_ts"] if as_of else ctx.ts), model=TRS_MODEL, quote=_quote_dict(deal.quote))
    if as_of:
        t["reset_px_dealt"] = str(deal.reset_px)
    inputs = {"as_of": ctx.ts, "model": TRS_MODEL, "reset_px": str(deal.reset_px), "underlying": _quote_dict(deal.quote)}
    if as_of:
        inputs["valuation_date"] = as_of["trade_date"].isoformat()           # the as-of mark is of the trade date, whenever it was entered
    conn.execute("INSERT INTO otc_contracts(contract_id, command_id, account_id, broker, kind, terms_json, notional_c, currency, start_date, maturity_date, margin_mode, im_c,"
                 " status, last_reset_date, reset_px, mark_c, mark_ts, vm_balance_c, inputs_json, closed_ts) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,'OPEN',?,?,0,?,0,?,NULL)",
                 (cid, ctx.command_id, aid, broker, "TRS", dumps(t), deal.notional_c, "USD", deal.start.isoformat(), deal.maturity.isoformat(), deal.margin_mode,
                  deal.im_c, deal.start.isoformat(), str(deal.reset_px), ctx.ts, dumps(inputs)))
    if getattr(deal.quote, "method", None) == "rfq_quote" and not as_of:
        # C14: an accepted quote books its quoted reset price; when the underlying moved since the quote the contract is worth something at once -
        # NAV carries that model value from the booking (the dealer honoured its quote), measured on the print now
        got = mark_trs(services, _rowd(conn.execute("SELECT * FROM otc_contracts WHERE contract_id=?", (cid,)).fetchone()), at=ctx.ts, snapshot=ctx.marks)
        if isinstance(got, tuple) and got[0] is not None and int(got[0]):
            conn.execute("UPDATE otc_contracts SET mark_c=?, inputs_json=? WHERE contract_id=?", (int(got[0]), dumps(dict(got[1] or {}, as_of=ctx.ts)), cid))
    ctx.touched_instruments.add((aid, cid))
    ctx.event("OTC_BOOKED", account_id=aid, instrument_id=cid, ref_type="otc", ref_id=cid, kind="TRS", broker=broker, underlying_id=uid, units=terms.units,
              receiver=terms.receiver, reset_px=str(deal.reset_px), notional_c=deal.notional_c, spread=str(deal.spread), im_c=deal.im_c, margin_mode=deal.margin_mode,
              maturity_date=deal.maturity.isoformat())
    view = swaps.contract_view(services, cid, conn=conn, detail=True, now_ts=ctx.ts)
    return {"contract": view, "contract_id": cid, "im_transfer": {"amount_c": deal.im_c, "funding": funding}, "entries": [entry_id], "preview": _trs_dict(deal)}


# ---- XCCY: terms, curves, deal, preview, booking ------------------------------------------------------------------------------------
def parse_xccy_terms(terms: dict) -> dict:
    """Validate the §13.12 cross-currency term dictionary (400 TERMS / CURRENCY_UNSUPPORTED / NOT_IN_THIS_BUILD); module
    docstring XCCY TERMS."""
    t = dict(terms or {})
    for k in ("pay_index", "recv_index", "pay_float", "recv_float", "float_index", "fixing", "pay_fixing", "recv_fixing"):
        v = t.get(k)
        if v not in (None, "", False) and str(v).strip().upper() not in ("FIXED", "NONE", "FALSE"):
            raise RejectError("NOT_IN_THIS_BUILD", f"floating cross-currency legs are not in this build ({k}={v!r}): no non-USD reference fixing is sourced;"
                              " both legs are fixed-rate", {"field": k})
    pay_ccy = str(t.get("pay_ccy") or "").strip().upper()
    recv_ccy = str(t.get("recv_ccy") or "").strip().upper()
    if len(pay_ccy) != 3 or len(recv_ccy) != 3:
        raise RejectError("TERMS", "pay_ccy and recv_ccy are required (three-letter ISO codes)")
    if pay_ccy == recv_ccy:
        raise RejectError("TERMS", "a cross-currency swap needs two different currencies")
    if "USD" not in (pay_ccy, recv_ccy):
        raise RejectError("CURRENCY_UNSUPPORTED", "one leg must be USD in this build (the mark-to-market reset re-strikes the USD notional)",
                          {"pay_ccy": pay_ccy, "recv_ccy": recv_ccy})
    foreign = recv_ccy if pay_ccy == "USD" else pay_ccy
    supported = sorted(c for c in POLICY_INDEX if c != "USD")
    if foreign not in supported:
        raise RejectError("CURRENCY_UNSUPPORTED", f"{foreign} is not supported: the foreign leg is valued on its FRED policy rate ({', '.join(supported)})",
                          {"currency": foreign, "supported": supported})
    pay_n = _money_arg(t.get("pay_notional_c"), t.get("pay_notional"), "pay_notional")
    recv_n = _money_arg(t.get("recv_notional_c"), t.get("recv_notional"), "recv_notional")
    if pay_n is None and recv_n is None:
        raise RejectError("TERMS", "give the pay or the receive notional (the other is struck at the deal FX rate)")
    bases = {}
    for side in ("pay", "recv"):
        b = str(t.get(f"{side}_basis") or "30/360").strip().upper()
        if b not in XCCY_BASES:
            raise RejectError("TERMS", f"{side}_basis must be one of {', '.join(XCCY_BASES)}, got {b}")
        bases[side] = b
    raw_months = t.get("months", t.get("tenor_months"))
    if raw_months in (None, ""):
        raise RejectError("TERMS", "months (the tenor, a multiple of 6) is required")
    months = _whole(raw_months, "months", XCCY_PERIOD_MONTHS, 360)
    if months % XCCY_PERIOD_MONTHS:
        raise RejectError("TERMS", f"months {months} must be a multiple of {XCCY_PERIOD_MONTHS} (semi-annual coupons on both legs)")
    return {"pay_ccy": pay_ccy, "recv_ccy": recv_ccy, "foreign": foreign, "usd_side": "receive" if recv_ccy == "USD" else "pay",
            "pay_notional_c": pay_n, "recv_notional_c": recv_n, "pay_rate": _rate_arg(t.get("pay_rate"), "pay_rate"),
            "recv_rate": _rate_arg(t.get("recv_rate"), "recv_rate"), "pay_basis": bases["pay"], "recv_basis": bases["recv"], "months": months,
            "mtm_reset": _flag(t.get("mtm_reset", False), "mtm_reset")}


def flat_dfs(rate, n_periods: int) -> Dict[Decimal, Decimal]:
    """Semi-annual discount factors of a flat curve at ``rate`` (a decimal fraction, semi-annual compounding):
    ``df(k/2) = 1 / (1 + rate/2)^k`` at 1e-10 HALF_EVEN, keyed by years like ``swaps.dfs_for``."""
    step = ONE + Decimal(rate) / 2
    out: Dict[Decimal, Decimal] = {}
    f = ONE
    for k in range(1, int(n_periods) + 1):
        f = f * step
        out[(HALF * k).quantize(GRID_Q)] = (ONE / f).quantize(DF_Q, rounding=ROUND_HALF_EVEN)
    return out


def foreign_curve(services, ccy: str, n_periods: int) -> Tuple[Dict[Decimal, Decimal], dict]:
    """The flat curve of a foreign leg at its FRED policy rate (409 NO_CURVE when the rate was never observed, or - P8B-01 / OTC-01 - when
    its series has stopped updating: a daily print older than 10 days or a monthly average more than 62 days after its month,
    ``tables.rate_age``; CHF's ended 2024-03).  The curve carries the rate's date and age."""
    from ..tables import rate_age
    rates = getattr(services, "rates", None)
    got = rates.policy_rate(ccy) if rates is not None and hasattr(rates, "policy_rate") else None
    series = POLICY_INDEX.get(ccy)
    if got is None:
        raise ConflictError("NO_CURVE", f"no {ccy} rate (FRED {series}) has been observed: the {ccy} leg cannot be valued; nothing is booked or marked without a"
                            " curve", {"currency": ccy, "series": series})
    obs, pct = got
    clock = getattr(services, "clock", None)
    age = rate_age(series, obs, ny_of(int(clock.epoch())).date() if clock is not None else None)
    if age["stale"]:
        what = (f"its last value is the {obs.strftime('%B %Y')} average ({pct} %), {age['age_days']} days after that month ended" if age["monthly"]
                else f"its last print is {pct} % of {obs.isoformat()}, {age['age_days']} days old")
        raise ConflictError("NO_CURVE", f"no current {ccy} rate: {series} has stopped updating - {what}; a {'monthly average' if age['monthly'] else 'daily rate'}"
                            f" older than {age['max_age_days']} days is never used, so the {ccy} leg cannot be valued (nothing is booked or marked on it)",
                            {"currency": ccy, "series": series, "as_of": obs.isoformat(), "value_pct": str(pct), "stale": True, "age_days": age["age_days"],
                             "max_age_days": age["max_age_days"]})
    dfs = flat_dfs(Decimal(str(pct)) / Decimal(100), n_periods)
    agetxt = f", {age['text']}" if age.get("age_days") is not None else ""
    return dfs, {"currency": ccy, "series": series, "value_pct": str(pct), "as_of": obs.isoformat(), "monthly_proxy": series in POLICY_INDEX_MONTHLY,
                 "age_days": age.get("age_days"), "age": age.get("text"),
                 "curve": f"flat at the {ccy} policy rate {pct} % ({series}, as of {obs.isoformat()}{agetxt}; semi-annual; a disclosed model: no free {ccy} swap curve)",
                 "quality": "close", "dfs": {str(k): str(v) for k, v in sorted(dfs.items())}}


def leg_curve(services, ccy: str, n_periods: int) -> Tuple[Decimal, Decimal, dict]:
    """``(par, annuity, inputs)`` of a currency's curve for ``n_periods`` remaining semi-annual periods (409 NO_CURVE)."""
    if ccy == "USD":
        dfs, inputs = swaps.dfs_for(services, n_periods)
        inputs = dict(inputs, currency="USD", curve="the live par curve (Treasury par yields, bootstrapped)")
    else:
        dfs, inputs = foreign_curve(services, ccy, n_periods)
    par, annuity, raw = swaps.par_annuity(dfs)
    return par, annuity, dict(inputs, par_rate=str(par), par_rate_raw=str(raw), annuity=str(annuity), periods=int(n_periods))


def leg_value(sign: Decimal, notional_c: int, rate, par, annuity, fx_rate) -> Decimal:
    """``sign × N × (K − par) × A × fx`` in USD (unrounded): a received fixed leg is worth N × (K − par) × A over its face."""
    return Decimal(sign) * from_c(int(notional_c)) * (qprice(rate) - qprice(par)) * Decimal(annuity) * Decimal(fx_rate)


def leg_dv01(sign: Decimal, notional_c: int, annuity, fx_rate) -> Decimal:
    """The PV change for +1 bp on the leg's own curve: a received fixed leg loses ``N × 1 bp × A``, a paid one gains it."""
    return -Decimal(sign) * from_c(int(notional_c)) * BP * Decimal(annuity) * Decimal(fx_rate)


@dataclass
class XLeg:
    side: str                # receive | pay
    currency: str
    notional_c: int
    rate: Decimal
    rate_given: bool
    basis: str
    par: Decimal
    annuity: Decimal
    quote: Decimal
    curve: dict

    @property
    def kind(self) -> str:
        return "swaprcv" if self.side == "receive" else "swappay"

    @property
    def sign(self) -> Decimal:
        return ONE if self.side == "receive" else -ONE


@dataclass
class XccyDeal:
    acct: dict
    p: dict
    recv: XLeg
    pay: XLeg
    deal_rate: Decimal
    fx_view: MarkView
    start: date
    maturity: date
    periods: List[Period]
    hw_bp: Decimal
    hw: Decimal
    mult: Decimal
    mark0_c: int
    dv01_c: int
    dv01_foreign_c: int
    im_c: int
    margin_mode: str
    im_pct: Decimal = XCCY_IM_PCT
    im_source: str = "static (disclosed)"
    trade_date: Optional[date] = None        # P8B-06: the FX trade date; ``start`` is its spot date

    @property
    def foreign(self) -> str:
        return self.p["foreign"]

    @property
    def usd_leg(self) -> XLeg:
        return self.recv if self.recv.currency == "USD" else self.pay

    @property
    def foreign_leg(self) -> XLeg:
        return self.pay if self.recv.currency == "USD" else self.recv

    def fx(self, ccy: str) -> Decimal:
        return ONE if ccy == "USD" else self.deal_rate


def _deal_fx(services, marks, foreign: str, today: date) -> Tuple[MarkView, Decimal]:
    """PO-08: the deal FX rate is the pair's observed quote, executable-fresh or a session close.  The pair is asked for at ORDER
    priority first; when only the ECB reference rate of an earlier day exists (the live pair has not been fetched yet) the deal is
    refused 409 FX_STALE - never booked at yesterday's reference rate."""
    pair = fx_pair_id(foreign)
    fetching = _pin(services, [pair])
    fxv = marks.fx(foreign) if marks is not None else None
    if _is_reference_rate(fxv):
        as_of = str(((fxv.inputs or {}).get("as_of")) or "")
        if not as_of or as_of < today.isoformat():
            raise ConflictError("FX_STALE", f"{pair}: only the ECB reference rate of {as_of or 'an earlier day'} is available - the live {pair} quote has not been"
                                f" fetched yet{' (it is being fetched now)' if fetching else ''}; a cross-currency swap is dealt at"
                                " the observed rate of today, never at an earlier day's reference rate",
                                {"pair": pair, "reference_as_of": as_of or None, "reference_rate": str(fxv.mark), "fetching": fetching,
                                 "retry_ms": RETRY_MS if fetching else None})
    return fxv, _usable(fxv, f"{pair} (the deal FX rate)", fetching)


def xccy_dates(services, foreign: str, now_ts: int) -> Tuple[date, date]:
    """P8B-06 / OTC-03: ``(trade date, start date)`` of a cross-currency swap dealt at ``now_ts``: the FX trade date (the FX day rolls at
    17:00 ET, ``fx.fx_trade_date``) and the pair's spot date from it (T+2, USD/CAD T+1, ``fx.spot_date``) - the initial exchange settles
    on the spot date like any FX deal of that trade date, never on the trade date itself or on the calendar date of an evening booking."""
    td = fxmod.fx_trade_date(services, int(now_ts))
    return td, fxmod.spot_date(services, fx_pair_id(foreign), td)


def _xccy_deal(services, conn: sqlite3.Connection, marks, acct: dict, p: dict, today: date, now_ts: Optional[int] = None) -> XccyDeal:
    foreign = p["foreign"]
    fxv, deal_rate = _deal_fx(services, marks, foreign, today)
    usd_side = p["usd_side"]
    given_usd = p["recv_notional_c"] if usd_side == "receive" else p["pay_notional_c"]
    given_f = p["pay_notional_c"] if usd_side == "receive" else p["recv_notional_c"]
    if given_f is not None:
        f_c = given_f
        usd_c = to_c(from_c(f_c) * deal_rate)
        if given_usd is not None and given_usd != usd_c:
            raise RejectError("TERMS", f"the USD notional is struck at the deal FX rate {deal_rate}: {foreign} {fmt_money(f_c)} x {deal_rate} = USD {fmt_money(usd_c)},"
                              f" not {fmt_money(given_usd)}; leave one notional blank", {"deal_rate": str(deal_rate), "usd_notional_c": usd_c})
    else:
        usd_c = given_usd
        f_c = to_c(from_c(usd_c) / deal_rate)
    sched = prime.read_schedule(conn, acct)
    mult = qprice(Decimal(sched.otc_spread_mult))
    hw_bp = qprice(Decimal(sched.irs_half_width_bp) * mult)
    hw = (hw_bp * BP).quantize(RATE_Q, rounding=ROUND_HALF_EVEN)
    trade_date, start = xccy_dates(services, foreign, now_ts if now_ts is not None else int(services.clock.epoch()))
    periods = swaps.period_schedule(services.cal, start, p["months"], XCCY_PERIOD_MONTHS)
    n = len(periods)
    broker = prime.broker_of(acct)
    legs: Dict[str, XLeg] = {}
    for side, pre in (("receive", "recv"), ("pay", "pay")):
        ccy = p[f"{pre}_ccy"]
        par, annuity, curve = leg_curve(services, ccy, n)
        quote = ((par - hw) if side == "receive" else (par + hw)).quantize(RATE_Q)
        given = p[f"{pre}_rate"]
        if given is not None and ((side == "receive" and given > quote) or (side == "pay" and given < quote)):
            raise RejectError("OFF_MARKET", f"the {ccy} {side} leg at {_pct(given)} is better for you than {broker}'s quote {_pct(quote)} (model par {_pct(par)}"
                              f" {'-' if side == 'receive' else '+'} {hw_bp} bp): a dealer does not deal there; leave the rate blank to deal at the quote",
                              {"leg": side, "currency": ccy, "rate": str(given), "quote": str(quote), "par_rate": str(par), "half_width_bp": str(hw_bp)})
        legs[side] = XLeg(side, ccy, usd_c if ccy == "USD" else f_c, given if given is not None else quote, given is not None, p[f"{pre}_basis"], par, annuity,
                          quote, curve)
    recv, pay = legs["receive"], legs["pay"]

    def fx_of(ccy: str) -> Decimal:
        return ONE if ccy == "USD" else deal_rate
    mark0 = to_c(sum((leg_value(l.sign, l.notional_c, l.rate, l.par, l.annuity, fx_of(l.currency)) for l in (recv, pay)), Decimal(0)))
    u, f = (recv, pay) if recv.currency == "USD" else (pay, recv)
    dv01 = to_c(leg_dv01(u.sign, u.notional_c, u.annuity, ONE))
    dv01_f = to_c(leg_dv01(f.sign, f.notional_c, f.annuity, deal_rate))
    imp = swaps.im_params(services, acct["account_id"])
    im_pct = Decimal(imp["xccy_im_pct"])
    im = to_c(im_pct * from_c(usd_c))
    return XccyDeal(acct, p, recv, pay, deal_rate, fxv, start, periods[-1][1], periods, hw_bp, hw, mult, mark0, dv01, dv01_f, im,
                    str(sched.swap_margin_mode).upper(), im_pct, imp["source"], trade_date)


def _leg_dict(leg: XLeg, deal: XccyDeal) -> dict:
    s, e, _p = deal.periods[0]
    role = ("lent: paid out at the start (1360 receivable) and received back at maturity; its coupons are received" if leg.side == "receive"
            else "borrowed: received at the start (2360 payable) and repaid at maturity; its coupons are paid")
    fx = deal.fx(leg.currency)
    return {"side": leg.side, "kind": leg.kind, "currency": leg.currency, "notional_c": leg.notional_c, "rate": str(leg.rate), "rate_pct": _pct(leg.rate),
            "rate_given": leg.rate_given, "quote": str(leg.quote), "quote_pct": _pct(leg.quote), "par_rate": str(leg.par), "par_rate_pct": _pct(leg.par),
            "annuity": str(leg.annuity), "basis": leg.basis, "role": role, "per_night_c": to_c(from_c(leg.notional_c) * leg.rate / Decimal(360)),
            "first_coupon_c": swaps.coupon_c(leg.notional_c, leg.rate, s, e, leg.basis),
            "value_c": to_c(leg_value(leg.sign, leg.notional_c, leg.rate, leg.par, leg.annuity, fx)),
            "dv01_c": to_c(leg_dv01(leg.sign, leg.notional_c, leg.annuity, fx)), "curve": leg.curve}


def _xccy_dict(deal: XccyDeal) -> dict:
    p = deal.p
    rows = []
    for k, (s, e, pd_) in enumerate(deal.periods):
        row = {"date": pd_.isoformat(), "event": "MATURITY" if pd_ == deal.maturity else "PAY", "period": [s.isoformat(), e.isoformat()]}
        for leg in (deal.recv, deal.pay):
            row[f"{leg.side}_c"] = swaps.coupon_c(leg.notional_c, leg.rate, s, e, leg.basis)
            row[f"{leg.side}_ccy"] = leg.currency
        row["indicative"] = bool(p["mtm_reset"] and k > 0)
        row["fx_reset"] = s.isoformat() if (p["mtm_reset"] and k > 0) else None
        rows.append(row)
    lent, bor = deal.recv, deal.pay
    pair = fx_pair_id(deal.foreign)
    return {"kind": "XCCY", "account_id": deal.acct["account_id"], "broker": prime.broker_of(deal.acct), "currency": "USD", "pay_ccy": p["pay_ccy"],
            "recv_ccy": p["recv_ccy"], "foreign_ccy": deal.foreign, "side": f"RECEIVE_{deal.recv.currency}_PAY_{deal.pay.currency}",
            "deal_rate": str(deal.deal_rate), "deal_pair": pair, "fx_quote": _quote_dict(deal.fx_view), "deal_rate_source": quote_label(deal.fx_view),
            "deal_rate_as_of": getattr(deal.fx_view, "quote_time", None) or ((getattr(deal.fx_view, "inputs", None) or {}).get("as_of")),
            "notional_c": deal.usd_leg.notional_c,
            "usd_notional_c": deal.usd_leg.notional_c, "foreign_notional_c": deal.foreign_leg.notional_c, "pay_notional_c": deal.pay.notional_c,
            "recv_notional_c": deal.recv.notional_c, "legs": {"receive": _leg_dict(deal.recv, deal), "pay": _leg_dict(deal.pay, deal)},
            "half_width": str(deal.hw), "half_width_bp": str(deal.hw_bp), "otc_spread_mult": str(deal.mult), "mark0_c": deal.mark0_c,
            "cost_of_dealing_c": max(0, -deal.mark0_c), "dv01_c": deal.dv01_c, "dv01_foreign_c": deal.dv01_foreign_c,
            "dv01_note": "PV change for +1 bp on the USD par curve (signed); dv01_foreign_c is the foreign leg's on its own curve, in USD",
            "im_c": deal.im_c, "im_pct": swaps.pct_text(deal.im_pct), "im_rule": f"IM = {swaps.pct_text(deal.im_pct)} of the USD notional ({deal.im_source})",
            "margin_mode": deal.margin_mode, "mtm_reset": p["mtm_reset"], "months": p["months"], "tenor_months": p["months"],
            "trade_date": deal.trade_date.isoformat() if deal.trade_date else None, "start_date": deal.start.isoformat(), "maturity_date": deal.maturity.isoformat(),
            "pair_label": f"{pair[:3]}/{pair[3:]}",
            "fx_reset_dates": [s.isoformat() for s, _e, _p in deal.periods[1:]] if p["mtm_reset"] else [],
            "start_exchange": {"date": deal.start.isoformat(), "rate": str(deal.deal_rate), "pair": pair,
                               "when": (f"on the spot date {deal.start.isoformat()} (trade date {deal.trade_date.isoformat() if deal.trade_date else 'n/a'}"
                                        f" + {(deal.start - deal.trade_date).days if deal.trade_date else 'n/a'} days), from 00:45 ET; nothing accrues before it"),
                               "we_pay": {"currency": lent.currency, "amount_c": lent.notional_c, "gl": "1360", "note": "lent: comes back at maturity"},
                               "we_receive": {"currency": bor.currency, "amount_c": bor.notional_c, "gl": "2360", "note": "borrowed: repaid at maturity"}},
            "schedule": rows, "fees": "none beyond the half-width on each leg (disclosed)",
            "terms": {"pay_ccy": p["pay_ccy"], "recv_ccy": p["recv_ccy"], "pay_notional_c": deal.pay.notional_c, "recv_notional_c": deal.recv.notional_c,
                      "pay_rate": str(deal.pay.rate), "recv_rate": str(deal.recv.rate), "pay_basis": deal.pay.basis, "recv_basis": deal.recv.basis,
                      "months": p["months"], "mtm_reset": p["mtm_reset"]},
            "inputs": {"model": XCCY_MODEL, "deal_rate": str(deal.deal_rate), "fx_quality": getattr(deal.fx_view, "quality", None),
                       "fx_source": getattr(deal.fx_view, "source", None), "fx_quote_time": getattr(deal.fx_view, "quote_time", None),
                       "fx_label": quote_label(deal.fx_view), "rate_precision": "0.001 % (0.1 bp)"},
            "conventions": list(CONVENTIONS)}


def xccy_start_check(services, conn, aid: str, deal: XccyDeal, snapshot=None) -> dict:
    """P8B-06 / P8B-12: can the account pay the lent notional at the start (the spot date) and the IM now?  USD lent: IM + the USD lent as
    one commitment now (PO-07, ``swaps.funding_preview``), as when the exchange settled at the booking.  A foreign currency lent: what the
    account will hold by the start date - its cash, the receivables due by then (an FX desk spot purchase for that value date counts) and
    Treasury's cash in that currency (``fx.funding_check``) - never an automatic conversion; the IM is checked apart.
    ``{ok, currency, need_c, note, hint, ...}``."""
    lent = deal.recv
    if lent.currency == "USD":
        f = swaps.funding_preview(services, conn, aid, "USD", lent.notional_c + deal.im_c, snapshot,
                                  what=f"IM + the USD lent at the start ({deal.start.isoformat()})")
        f["treasury_settled_c"] = balance(conn, f"{split_account_id(aid)[0]}:TREASURY", "1010", "USD")
        f["start_date"] = deal.start.isoformat()
        return f
    chk = fxmod.funding_check(services, aid, lent.currency, lent.notional_c, deal.start, conn=conn)
    ccy, need = lent.currency, lent.notional_c
    hint = f"buy {ccy} {fmt_money(int(chk.get('shortfall_c') or 0))} with Convert (the FX desk, value on or before {deal.start.isoformat()})"
    note = (f"the start exchange pays {ccy} {fmt_money(need)} on {deal.start.isoformat()}: {chk.get('text')}" +
            ("" if chk.get("fundable") else f" - {hint}; it is never converted automatically"))
    return {"currency": ccy, "need_c": need, "ok": bool(chk.get("fundable")), "note": note, "hint": None if chk.get("fundable") else hint,
            "own_settled_c": balance(conn, aid, "1010", ccy), "available_c": max(0, int(chk.get("own_available_c") or 0) + int(chk.get("forwards_due_c") or 0)),
            "own_available_c": chk.get("own_available_c"), "receivables_due_c": chk.get("forwards_due_c"), "sweep_c": int(chk.get("from_treasury_c") or 0),
            "treasury_available_c": chk.get("treasury_available_c"), "shortfall_c": chk.get("shortfall_c"), "start_date": deal.start.isoformat(),
            "treasury_settled_c": balance(conn, f"{split_account_id(aid)[0]}:TREASURY", "1010", ccy), "check": chk}


def _book_xccy(ctx: TxContext, services, acct: dict, p: dict) -> dict:
    conn = ctx.conn
    aid = acct["account_id"]
    today = ny_of(ctx.ts).date()
    deal = _xccy_deal(services, conn, ctx.marks, acct, p, today, now_ts=ctx.ts)
    # P8B-06: the start exchange settles on the spot date (the XCCY_EVENTS START key from 00:45 ET), with its own funding sweep on that
    # business date; at the booking only the IM moves.  The owner commits now to what the start needs: refused, nothing written, when it
    # could not be paid (P8B-12: a foreign currency counts what arrives by the start date)
    chk = xccy_start_check(services, conn, aid, deal, ctx.marks)
    if not chk.get("ok", True):
        if deal.recv.currency == "USD":
            raise ConflictError("INSUFFICIENT_FUNDS", chk.get("note") or "the USD lent at the start cannot be funded", {"funding": chk})
        raise ConflictError("INSUFFICIENT_FUNDS_CCY", chk.get("note") or f"no {deal.recv.currency} for the start exchange",
                            {"currency": deal.recv.currency, "need_c": deal.recv.notional_c, "shortfall_c": chk.get("shortfall_c"), "hint": chk.get("hint"),
                             "start_date": deal.start.isoformat()})
    broker = prime.broker_of(acct)
    cid = next_id(conn, "swap")
    idem = f"im:{cid}:open"
    funding = _fund(ctx, services, aid, "USD", deal.im_c, "otc", idem, overdraft=False, commit=True)      # PO-07: against available
    recv, pay = deal.recv, deal.pay
    memo = (f"OTC IM {cid}: XCCY receive {recv.currency} {fmt_money(recv.notional_c)} at {_pct(recv.rate)} / pay {pay.currency} {fmt_money(pay.notional_c)} at"
            f" {_pct(pay.rate)} to {deal.maturity.isoformat()} with {broker}; deal rate {_px_text(deal.deal_rate)} ({quote_label(deal.fx_view)}); IM ="
            f" {swaps.pct_text(deal.im_pct)} x USD {fmt_money(deal.usd_leg.notional_c)} = {fmt_money(deal.im_c)} ({deal.im_source}); mark0"
            f" {fmt_money(deal.mark0_c)} = cost of dealing")
    entries = [ctx.post(Entry("OTC_IM", memo, [dr(aid, "1330", "USD", deal.im_c, instrument_id=cid, memo="OTC initial margin"),
                                               cr(aid, "1010", "USD", deal.im_c, memo="IM paid")], idem, ref_type="otc", ref_id=cid))]
    name = (f"XCCY receive {recv.currency} {_pct(recv.rate)} / pay {pay.currency} {_pct(pay.rate)}, USD {fmt_money(deal.usd_leg.notional_c)}"
            f" ({deal.start.isoformat()} to {deal.maturity.isoformat()}{', MTM' if p['mtm_reset'] else ''}, {broker})")
    _insert_instrument(services, ctx, cid, name, deal.maturity, {"contract_id": cid, "otc_kind": "XCCY", "broker": broker, "pay_ccy": p["pay_ccy"],
                                                                  "recv_ccy": p["recv_ccy"], "deal_rate": str(deal.deal_rate), "mtm_reset": p["mtm_reset"],
                                                                  "start_date": deal.start.isoformat(), "maturity_date": deal.maturity.isoformat(),
                                                                  "margin_mode": deal.margin_mode})
    pj = [[s.isoformat(), e.isoformat(), q.isoformat()] for s, e, q in deal.periods]
    legs = {leg.side: {"currency": leg.currency, "notional_c": leg.notional_c, "rate": str(leg.rate), "basis": leg.basis, "par_rate": str(leg.par),
                       "quote": str(leg.quote), "annuity0": str(leg.annuity), "rate_given": leg.rate_given} for leg in (recv, pay)}
    t = {"kind": "XCCY", "pay_ccy": p["pay_ccy"], "recv_ccy": p["recv_ccy"], "foreign_ccy": deal.foreign, "usd_side": p["usd_side"], "legs": legs,
         "deal_rate": str(deal.deal_rate), "deal_pair": fx_pair_id(deal.foreign), "months": p["months"], "tenor_months": p["months"],
         "period_months": XCCY_PERIOD_MONTHS, "mtm_reset": p["mtm_reset"], "periods": pj, "fixed_periods": pj,
         "trade_date": (deal.trade_date or today).isoformat(),
         "start_date": deal.start.isoformat(), "maturity_date": deal.maturity.isoformat(), "half_width": str(deal.hw), "half_width_bp": str(deal.hw_bp),
         "otc_spread_mult": str(deal.mult), "mark0_c": deal.mark0_c, "dv01_0_c": deal.dv01_c, "im_pct": str(deal.im_pct), "dealt_ts": ctx.ts, "model": XCCY_MODEL,
         "deal_rate_source": quote_label(deal.fx_view), "deal_rate_quote_time": getattr(deal.fx_view, "quote_time", None)}
    inputs = {"as_of": ctx.ts, "model": XCCY_MODEL, "deal_rate": str(deal.deal_rate), "deal_rate_source": quote_label(deal.fx_view),
              "legs": {leg.side: leg.curve for leg in (recv, pay)}}
    conn.execute("INSERT INTO otc_contracts(contract_id, command_id, account_id, broker, kind, terms_json, notional_c, currency, start_date, maturity_date, margin_mode, im_c,"
                 " status, last_reset_date, reset_px, mark_c, mark_ts, vm_balance_c, inputs_json, closed_ts) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,'OPEN',?,?,?,?,0,?,NULL)",
                 (cid, ctx.command_id, aid, broker, "XCCY", dumps(t), deal.usd_leg.notional_c, "USD", deal.start.isoformat(), deal.maturity.isoformat(),
                  deal.margin_mode, deal.im_c, deal.start.isoformat(), str(deal.deal_rate), deal.mark0_c, ctx.ts, dumps(inputs)))
    start_res = None
    if deal.start <= today:                  # never with a spot start (P8B-06); kept for a calendar where spot is today
        start_res = _start_exchange(ctx, services, _contract(conn, cid), overdraft=False)
        entries.append(start_res["entry_id"])
    ctx.touched_instruments.add((aid, cid))
    ctx.event("OTC_BOOKED", account_id=aid, instrument_id=cid, ref_type="otc", ref_id=cid, kind="XCCY", broker=broker, pay_ccy=p["pay_ccy"], recv_ccy=p["recv_ccy"],
              usd_notional_c=deal.usd_leg.notional_c, foreign_notional_c=deal.foreign_leg.notional_c, deal_rate=str(deal.deal_rate), mark0_c=deal.mark0_c,
              dv01_c=deal.dv01_c, im_c=deal.im_c, margin_mode=deal.margin_mode, mtm_reset=p["mtm_reset"], maturity_date=deal.maturity.isoformat())
    view = swaps.contract_view(services, cid, conn=conn, detail=True, now_ts=ctx.ts)
    return {"contract": view, "contract_id": cid, "im_transfer": {"amount_c": deal.im_c, "funding": funding}, "start_exchange": start_res, "entries": entries,
            "preview": _xccy_dict(deal)}


def _start_exchange(ctx: TxContext, services, row: dict, overdraft: bool = False) -> dict:
    """The start notional exchange (module docstring XCCY START); 409 when the lent currency cannot be funded."""
    conn = ctx.conn
    cid, aid = row["contract_id"], row["account_id"]
    terms = _terms(row)
    done = conn.execute("SELECT entry_id FROM otc_resets WHERE contract_id=? AND kind='NOTIONAL_START'", (cid,)).fetchone()
    if done is not None:
        return {"already": True, "entry_id": done["entry_id"]}
    rate = qprice(terms["deal_rate"])
    lent, bor = terms["legs"]["receive"], terms["legs"]["pay"]
    lc, bc = lent["currency"], bor["currency"]
    ln, bn = int(lent["notional_c"]), int(bor["notional_c"])
    idem = f"xccy:{cid}:start"
    funding = _fund(ctx, services, aid, lc, ln, "xccy", idem, overdraft=overdraft, commit=not overdraft)    # PO-07: the owner's commitment

    def fxr(ccy: str):
        return None if ccy == "USD" else rate
    lines = [dr(aid, "1360", lc, ln, instrument_id=cid, fx_rate=fxr(lc), memo="notional lent (receive leg; comes back at maturity)"),
             cr(aid, "1010", lc, ln, fx_rate=fxr(lc), memo="notional paid at the start"),
             dr(aid, "1010", bc, bn, fx_rate=fxr(bc), memo="notional received at the start"),
             cr(aid, "2360", bc, bn, instrument_id=cid, fx_rate=fxr(bc), memo="notional borrowed (pay leg; repaid at maturity)")]
    foreign = terms["foreign_ccy"]
    memo = (f"XCCY start exchange {cid}: we pay {lc} {fmt_money(ln)} (lent: the receive leg, back at maturity) and receive {bc} {fmt_money(bn)} (borrowed: the pay"
            f" leg, repaid at maturity) at the deal rate {_px_text(rate)} USD per {foreign}"
            + (f" ({terms['deal_rate_source']})" if terms.get("deal_rate_source") else "") + "; each leg balances in its own currency (SWAP_RESET)")
    start = terms["start_date"]
    entry_id = ctx.post(Entry("XCCY_EXCHANGE", memo, lines, idem, ref_type="otc", ref_id=cid, business_date=start))
    usd_n = ln if lc == "USD" else bn
    detail = {"we_pay": {"currency": lc, "amount_c": ln, "gl": "1360"}, "we_receive": {"currency": bc, "amount_c": bn, "gl": "2360"}, "rate": str(rate),
              "funding": funding, "entry_id": entry_id}
    conn.execute("INSERT INTO otc_resets(contract_id, event_date, kind, amount_c, detail_json, entry_id) VALUES (?,?,'NOTIONAL_START',?,?,?)",
                 (cid, start, usd_n, dumps(detail), entry_id))
    ctx.touched_instruments.add((aid, cid))
    ctx.event("XCCY_EXCHANGE", account_id=aid, instrument_id=cid, ref_type="otc", ref_id=cid, event="START", date=start, entry_id=entry_id, **{k: v for k, v in detail.items()
                                                                                                                                           if k != "entry_id"})
    return {"entry_id": entry_id, "funding": funding, "we_pay": detail["we_pay"], "we_receive": detail["we_receive"], "rate": str(rate)}


# ---- preview / book / terminate hooks (WP-17 delegates TRS / XCCY here) ---------------------------------------------------------------
def preview(services, account_id: str, kind: str, terms: dict, conn: Optional[sqlite3.Connection] = None, now: Optional[int] = None) -> dict:
    """``otc.preview`` for TRS / XCCY (pure, no writes; raises the same codes as ``otc.book``)."""
    k = prime.require_otc_kind(kind)
    with _marks.shared_reader(services.db, conn) as c:        # ORC-6: the caller's connection, else ONE reader for everything below
        return _preview(services, c, account_id, k, terms, now)


def _preview(services, conn: sqlite3.Connection, account_id: str, kind: str, terms: dict, now: Optional[int]) -> dict:
    acct = _pb_acct(conn, account_id)
    ts = int(now if now is not None else services.clock.epoch())
    today = ny_of(ts).date()
    marks = _snapshot(services)
    if kind == "TRS":
        deal = _trs_deal(services, conn, marks, acct, parse_trs_terms(terms), today, now=ts)
        out = _trs_dict(deal)
        out["funding"] = swaps.funding_preview(services, conn, account_id, "USD", deal.im_c, marks, what="IM")         # PO-07
    elif kind == "XCCY":
        xd = _xccy_deal(services, conn, marks, acct, parse_xccy_terms(terms), today, now_ts=ts)
        out = _xccy_dict(xd)
        # P8B-06 / P8B-12: the start exchange is on the spot date; the check is the booking's own (xccy_start_check): USD lent with the IM as one
        # commitment now; a foreign currency lent from what the account holds or receives by the start date, never an automatic conversion
        out["funding"] = xccy_start_check(services, conn, account_id, xd, marks)
        if xd.recv.currency != "USD":
            out["im_funding"] = swaps.funding_preview(services, conn, account_id, "USD", xd.im_c, marks, what="IM")
    else:
        raise RejectError("OTC_KIND", f"{kind} is not a TRS or a cross-currency swap (IRS is products/swaps.py)")
    try:
        out["eligibility"] = _eligibility(services, split_account_id(account_id)[0]).to_dict()
    except Exception:   # noqa: BLE001 — informational only; WP-17 already ran require_pb_otc
        out["eligibility"] = None
    out["as_of"] = ts
    return out


def book(ctx: TxContext, payload: dict) -> dict:
    """``otc.book`` for TRS / XCCY (WP-17 checked the kind, the PB account and the eligibility first)."""
    services = _services(ctx)
    kind = prime.require_otc_kind(payload.get("kind"))
    acct = _pb_acct(ctx.conn, str(payload.get("account_id") or ""))
    if kind == "TRS":
        return _book_trs(ctx, services, acct, parse_trs_terms(payload.get("terms") or {}))
    if kind == "XCCY":
        return _book_xccy(ctx, services, acct, parse_xccy_terms(payload.get("terms") or {}))
    raise RejectError("OTC_KIND", f"{kind} is not a TRS or a cross-currency swap (IRS is products/swaps.py)")


# ---- marks and DV01 (SWAP_VM, swaps.mark, the views) ------------------------------------------------------------------------------------
def _stale_view(cid: str, value_c: int, source: str, quote_time, now_ts: int, inputs: dict, note: str) -> MarkView:
    return MarkView(cid, from_c(int(value_c)), "stale", source, "swap_model", quote_time, now_ts, "model", dict(inputs, qualities=["stale"], note=note), False, note)


def mark_trs(services, contract, at: Optional[int] = None, valuation_date=None, snapshot=None):
    """``(cents, inputs)`` = ``sign × to_c(units × (S − reset_px))`` (module docstring TRS MARK); a stale ``MarkView`` when the
    underlying has no usable price; None for an unknown contract.  ``valuation_date`` (SWAP_VM) prices at the CLOSE of the last
    trading day on or before it, recorded or observed at the close - else ``(None, {vm_outcome: CLOSE_PENDING | CAUGHT_UP})``,
    never an earlier day's price or an intraday print (LC-3)."""
    row = _contract_of(services, contract)
    if row is None:
        return None
    cid = row["contract_id"]
    now_ts = int(at if at is not None else services.clock.epoch())
    terms = _terms(row)
    uid = terms.get("underlying_id")
    base = {"as_of": now_ts, "contract_id": cid, "model": TRS_MODEL, "underlying_id": uid, "units": terms.get("units"), "receiver": terms.get("receiver"),
            "reset_px": row.get("reset_px"), "last_reset_date": row.get("last_reset_date"), "quality": "computed"}
    if row.get("status") != "OPEN":
        return 0, dict(base, note=f"{row.get('status')}: no remaining cash flows")
    stored = int(row.get("mark_c") or 0)
    snap = snapshot if snapshot is not None else _snapshot(services, [uid] if uid else None)
    price, info = None, {}
    if valuation_date is not None:
        vd = _date(valuation_date)
        with _marks.shared_reader(services.db) as conn:
            inst = services.instruments.get(uid, conn)
            if inst is None:
                return None, dict(base, vm_outcome="CLOSE_PENDING", note=f"{uid} is not in the instrument master", quiet=False)
            price, info = _vm_close(services, uid, inst.session_group, vd, lambda r: close_on(services, conn, snap, inst, r),
                                    snap.mark(uid) if snap is not None else None)
        if price is None:
            return None, dict(base, valuation_date=vd.isoformat(), **info)
    if price is None:
        mv = snap.mark(uid) if snap is not None else None
        if mv is None or mv.mark is None or mv.quality == "unavailable":
            note = f"{uid}: no observed price ({getattr(mv, 'note', '') or 'unavailable'}); the last computed mark, labelled stale"
            return _stale_view(cid, stored, "TRS model (stored mark)", row.get("mark_ts"), now_ts, dict(base, stored_mark_c=stored), note)
        price = qprice(mv.mark)
        info = {"price": str(price), "quality": mv.quality, "source": mv.source, "quote_time": mv.quote_time, "delay_label": mv.delay_label}
    sign = 1 if terms.get("receiver") else -1
    reset_px = qprice(row.get("reset_px") or "0")
    units = int(terms.get("units") or 0)
    value = sign * to_c(Decimal(units) * (price - reset_px))
    inputs = dict(base, S=str(price), underlying=dict(info, instrument_id=uid), qualities=[info.get("quality") or "close"],
                  valuation_date=None if valuation_date is None else _date(valuation_date).isoformat(),
                  formula=f"{'+' if sign > 0 else '-'}{units} x ({price} - {reset_px})")
    if info.get("quality") == "stale":
        return _stale_view(cid, value, "TRS model", info.get("quote_time"), now_ts, inputs, f"{uid}: the price {price} is stale; the mark at that price")
    return value, inputs


def _xccy_value(services, row: dict, terms: dict, n: int, snap, fx_view=None) -> Tuple[Decimal, dict]:
    foreign = terms["foreign_ccy"]
    pair = fx_pair_id(foreign)
    fxv = fx_view if fx_view is not None else (snap.fx(foreign) if snap is not None else None)
    if fxv is None or fxv.mark is None or fxv.quality == "unavailable":
        raise ConflictError("NO_QUOTE", f"no {pair} rate: the {foreign} leg cannot be converted", {"pair": pair})
    fx_rate = qprice(fxv.mark)
    total = Decimal(0)
    legs = {}
    qualities = [fxv.quality]
    for side in ("receive", "pay"):
        leg = terms["legs"][side]
        ccy = leg["currency"]
        notional = int(row["notional_c"]) if ccy == "USD" else int(leg["notional_c"])
        par, annuity, curve = leg_curve(services, ccy, n)
        sign = ONE if side == "receive" else -ONE
        fx = ONE if ccy == "USD" else fx_rate
        v = leg_value(sign, notional, leg["rate"], par, annuity, fx)
        total += v
        qualities.append(curve.get("quality") or "close")
        legs[side] = {"currency": ccy, "notional_c": notional, "rate": leg["rate"], "par_rate": str(par), "annuity": str(annuity), "value_c": to_c(v),
                      "dv01_c": to_c(leg_dv01(sign, notional, annuity, fx)), "curve": curve.get("curve"),
                      "curve_as_of": curve.get("curve_as_of") or curve.get("as_of")}
    return total, {"fx": {"pair": pair, "usd_per_unit": str(fx_rate), "quality": fxv.quality, "source": fxv.source, "quote_time": fxv.quote_time},
                   "legs": legs, "remaining_periods": n, "qualities": qualities}


def mark_xccy(services, contract, at: Optional[int] = None, valuation_date=None, snapshot=None):
    """``(cents, inputs)`` = the coupon legs' clean PVs over their faces in USD (module docstring XCCY QUOTES AND MARK); the
    stored mark labelled stale when a curve, a policy rate or the FX rate is missing; None for an unknown contract."""
    row = _contract_of(services, contract)
    if row is None:
        return None
    cid = row["contract_id"]
    now_ts = int(at if at is not None else services.clock.epoch())
    on = _date(valuation_date) if valuation_date is not None else ny_of(now_ts).date()
    terms = _terms(row)
    base = {"as_of": now_ts, "valuation_date": on.isoformat(), "contract_id": cid, "model": XCCY_MODEL, "quality": "computed"}
    if row.get("status") != "OPEN":
        return 0, dict(base, note=f"{row.get('status')}: no remaining cash flows", remaining_periods=0)
    remaining = [q for q in _periods(terms) if q[2] > on]
    if not remaining:
        return 0, dict(base, note="no remaining payment periods (at maturity)", remaining_periods=0)
    foreign = terms["foreign_ccy"]
    snap = snapshot if snapshot is not None else _snapshot(services, [fx_pair_id(foreign)])
    fx_view = None
    if valuation_date is not None:      # LC-3: the pair's CLOSE (recorded, or the print at 17:00 ET) of the last FX day on or before the VM date
        with _marks.shared_reader(services.db) as conn:
            px, info = _vm_close(services, fx_pair_id(foreign), "FX_SPOT", on, lambda r: fx_close_on(services, conn, snap, foreign, r),
                                 snap.fx(foreign) if snap is not None else None)
        if px is None:
            return None, dict(base, **info)
        fx_view = MarkView(fx_pair_id(foreign), px, "close", info.get("source") or "FX close", info.get("method") or "close", info.get("quote_time"),
                           now_ts, "close", dict(info), False, f"FX close of {info.get('close_date') or on.isoformat()}")
    try:
        value, inputs = _xccy_value(services, row, terms, len(remaining), snap, fx_view)
    except ConflictError as e:
        stored = int(row.get("mark_c") or 0)
        return _stale_view(cid, stored, "XCCY model (stored mark)", row.get("mark_ts"), now_ts, dict(base, stored_mark_c=stored),
                           f"{e.message}; the last computed mark, labelled stale")
    return to_c(value), dict(base, **inputs)


def dv01_c(services, contract) -> int:
    """The signed PV change for +1 bp on the USD par curve: 0 for a TRS (its financing resets nightly), the USD leg's
    ``sign × N × 1 bp × A`` for an XCCY (raises ConflictError NO_CURVE; 0 when not OPEN / nothing remains)."""
    row = _contract_of(services, contract)
    if row is None or row.get("status") != "OPEN" or row["kind"] != "XCCY":
        return 0
    terms = _terms(row)
    remaining = [q for q in _periods(terms) if q[2] > ny_of(services.clock.epoch()).date()]
    if not remaining:
        return 0
    _par, annuity, _in = leg_curve(services, "USD", len(remaining))
    return to_c(leg_dv01(ONE if terms.get("usd_side") == "receive" else -ONE, int(row["notional_c"]), annuity, ONE))


# ---- TRS financing and dividends (ACCRUE_DAILY kind trsfin) --------------------------------------------------------------------------
def _trs_dividends(ctx: TxContext, row: dict, terms: dict, through: date) -> List[dict]:
    """Post the dividend accrual of every ``dividends`` row of the underlying with an ex-date in (start, through] (and not after
    maturity) that is not accrued yet (module docstring TRS DIVIDENDS)."""
    conn = ctx.conn
    cid, aid = row["contract_id"], row["account_id"]
    uid = terms["underlying_id"]
    units = int(terms["units"])
    hi = min(through.isoformat(), row["maturity_date"])
    out: List[dict] = []
    for r in conn.execute("SELECT ex_date, amount, pay_date, source FROM dividends WHERE instrument_id=? AND ex_date>? AND ex_date<=? ORDER BY ex_date",
                          (uid, row["start_date"], hi)).fetchall():
        idem = f"trsdiv:{cid}:{r['ex_date']}"
        if conn.execute("SELECT 1 FROM journal_entries WHERE idem_key=?", (idem,)).fetchone() is not None:
            continue
        amount = to_c(Decimal(units) * qprice(r["amount"]))
        if amount == 0:
            continue
        if terms.get("receiver"):
            lines = [dr(aid, "1225", "USD", amount, instrument_id=cid, memo="dividend receivable (TRS receiver)"),
                     cr(aid, "4452", "USD", amount, instrument_id=cid, memo=f"{uid} dividend equivalent ex {r['ex_date']}")]
        else:
            lines = [dr(aid, "4452", "USD", amount, instrument_id=cid, memo=f"{uid} dividend equivalent ex {r['ex_date']}"),
                     cr(aid, "2340", "USD", amount, instrument_id=cid, memo="dividend payable (TRS payer)")]
        memo = (f"TRS dividend {cid}: {uid} {qprice(r['amount'])} per share ex {r['ex_date']} ({r['source']}) x {units:,} units = {fmt_money(amount)},"
                f" {'receivable' if terms.get('receiver') else 'payable'} until the next reset (DIVIDEND)")
        entry_id = ctx.post(Entry("ACCRUAL", memo, lines, idem, ref_type="otc", ref_id=cid, business_date=r["ex_date"]))
        ctx.touched_instruments.add((aid, cid))
        ctx.event("TRS_DIVIDEND", account_id=aid, instrument_id=cid, ref_type="otc", ref_id=cid, ex_date=r["ex_date"], amount=str(qprice(r["amount"])), units=units,
                  amount_c=amount, receiver=bool(terms.get("receiver")), entry_id=entry_id)
        out.append({"ex_date": r["ex_date"], "per_share": str(qprice(r["amount"])), "amount_c": amount, "entry_id": entry_id})
    return out


def _trs_night(ctx: TxContext, services, row: dict, night: date) -> dict:
    """One TRS financing night (module docstring TRS FINANCING) and the dividends seen through it."""
    conn = ctx.conn
    cid, aid = row["contract_id"], row["account_id"]
    terms = _terms(row)
    periods = _periods(terms)
    night_s = night.isoformat()
    k = _period_index(periods, night)
    if k is None:
        return {"status": "DONE", "posted": False, "posted_c": 0, "amount_c": 0, "note": f"night {night_s} is outside {cid}'s financing periods"}
    s = periods[k][0]
    if s > _date(row.get("last_reset_date") or row["start_date"]):
        raise RejectError("RESET_PENDING", f"{cid}: the night of {night_s} belongs to the period from {s.isoformat()}, whose TRS_RESET has not run (the night"
                          " accrues on that reset's notional; it waits)", {"contract_id": cid, "reset_date": s.isoformat()})
    st = _state(conn, "trsfin", cid)
    if st is not None and st["accrued_through"] and st["accrued_through"] >= night_s:
        return {"status": "DONE", "posted": False, "posted_c": 0, "amount_c": 0, "already": True, "note": f"night {night_s} already accrued"}
    obs, pct = sofr_for_night(services, night)
    spread = qprice(terms["spread"])
    receiver = bool(terms.get("receiver"))
    rate = fin_rate(pct, spread, receiver)
    base = int(row["notional_c"])
    amount = int(_accrue_segment_fn(services)(ctx, "trsfin", cid, base, rate, 360, night))
    owed = amount if receiver else -amount
    st2 = _state(conn, "trsfin", cid)
    n = (night - _date(st2["segment_start"])).days + 1
    lines: List[Line] = []
    if owed > 0:
        lines = [dr(aid, "4451", "USD", owed, instrument_id=cid, memo="TRS financing accrued (paid by us)"),
                 cr(aid, "2340", "USD", owed, instrument_id=cid, memo="TRS financing payable")]
    elif owed < 0:
        lines = [dr(aid, "1225", "USD", -owed, instrument_id=cid, memo="TRS financing receivable"),
                 cr(aid, "4451", "USD", -owed, instrument_id=cid, memo="TRS financing accrued (received)")]
    memo = (f"TRS financing {cid} night of {night_s}: {fmt_money(base)} x (SOFR {pct} % ({obs.isoformat()}) {'+' if receiver else '-'} {_pct(spread)}) ="
            f" {_pct(rate)} ACT/360, night {n} of the segment: cumulative to_c(N x r x {n} / 360) = {fmt_money(int(st2['cum_c']))}"
            f" ({'paid by' if receiver else 'received by'} the {'receiver' if receiver else 'payer'}) (ACCRUAL, RATE_LAG)")
    entry_id = ctx.post(Entry("ACCRUAL", memo, lines, f"accr:trsfin:{cid}:{night_s}", ref_type="otc", ref_id=cid,
                              business_date=swaps.lifecycle_business_date(row, night))) if lines else None
    divs = _trs_dividends(ctx, row, terms, night)
    if entry_id is not None:
        ctx.touched_instruments.add((aid, cid))
        ctx.event("SWAP_ACCRUAL", account_id=aid, instrument_id=cid, ref_type="otc", ref_id=cid, kind="trsfin", night=night_s, amount_c=amount, owed_c=owed,
                  rate=str(rate), sofr=str(pct), entry_id=entry_id)
    return {"status": "DONE", "posted": entry_id is not None or bool(divs), "posted_c": abs(owed), "amount_c": amount, "owed_c": owed, "entry_id": entry_id,
            "rate": str(rate), "sofr": str(pct), "obs_date": obs.isoformat(), "cum_c": int(st2["cum_c"]), "dividends": divs,
            "note": f"financing {fmt_money(abs(owed))} {'paid' if owed > 0 else 'received'} at {_pct(rate)} night of {night_s}"
                    + (f"; {len(divs)} dividend(s) accrued" if divs else "")}


# ---- XCCY coupon legs (ACCRUE_DAILY kinds swaprcv / swappay through swaps.accrue_night) ---------------------------------------------
def usd_notional(conn: sqlite3.Connection, row: dict, terms: dict, periods: List[Period], k: int) -> Optional[int]:
    """The USD notional of period ``k``: the booked one, or the FX reset recorded at the period's start (None until then)."""
    n0 = int(terms["legs"][terms["usd_side"]]["notional_c"])
    if k <= 0 or not terms.get("mtm_reset"):
        return n0
    r = conn.execute("SELECT detail_json FROM otc_resets WHERE contract_id=? AND kind='FX_RESET' AND event_date=?",
                     (row["contract_id"], periods[k][0].isoformat())).fetchone()
    return None if r is None else int(_detail(r)["usd_notional_c"])


def _leg_notional(conn: sqlite3.Connection, row: dict, terms: dict, periods: List[Period], side: str, k: int) -> Optional[int]:
    leg = terms["legs"][side]
    return usd_notional(conn, row, terms, periods, k) if leg["currency"] == "USD" else int(leg["notional_c"])


def xccy_leg_spec(conn: sqlite3.Connection, row: dict, kind: str, night: date) -> Tuple[Optional[swaps.LegSpec], Optional[int]]:
    """The ``swaps.LegSpec`` that accrues ``night`` for an XCCY leg (``(None, None)`` outside the periods); RejectError
    START_PENDING / FX_RESET_PENDING when the night cannot accrue yet."""
    cid = row["contract_id"]
    terms = _terms(row)
    periods = _periods(terms)
    if not _has_reset(conn, cid, "NOTIONAL_START"):
        raise RejectError("START_PENDING", f"{cid}: the start exchange has not happened; nothing accrues before it", {"contract_id": cid})
    k = _period_index(periods, night)
    if k is None:
        return None, None
    side = "receive" if kind == "swaprcv" else "pay"
    leg = terms["legs"][side]
    notional = _leg_notional(conn, row, terms, periods, side, k)
    if notional is None:
        raise RejectError("FX_RESET_PENDING", f"{cid}: the USD notional of the period from {periods[k][0].isoformat()} is set by its FX reset, which has not run",
                          {"contract_id": cid, "reset_date": periods[k][0].isoformat()})
    return swaps.LegSpec(kind, "fixed", leg["currency"], notional, leg["basis"], tuple(periods), "FIXED", qprice(leg["rate"])), k


def _xccy_night(ctx: TxContext, services, row: dict, kind: str, night: date) -> dict:
    conn = ctx.conn
    cid, aid = row["contract_id"], row["account_id"]
    night_s = night.isoformat()
    spec, _k = xccy_leg_spec(conn, row, kind, night)
    if spec is None:
        return {"status": "DONE", "posted": False, "posted_c": 0, "amount_c": 0, "note": f"night {night_s} is outside {cid}'s coupon periods"}
    res = swaps.accrue_leg(ctx, services, row, spec, night)
    amount = int(res["amount_c"])
    entry_id = None
    if res["lines"]:
        memo = res["memo"] + (f" (XCCY {spec.currency} leg; base at this command's FX rate)" if spec.currency != "USD" else " (XCCY USD leg)")
        entry_id = ctx.post(Entry("ACCRUAL", memo, res["lines"], f"accr:{kind}:{cid}:{night_s}", ref_type="otc", ref_id=cid,
                                  business_date=swaps.lifecycle_business_date(row, night)))
        ctx.touched_instruments.add((aid, cid))
        ctx.event("SWAP_ACCRUAL", account_id=aid, instrument_id=cid, ref_type="otc", ref_id=cid, kind=kind, leg=spec.side, currency=spec.currency, night=night_s,
                  amount_c=amount, rate=res.get("rate"), entry_id=entry_id)
    note = (f"{spec.side} leg {spec.currency} {fmt_money(amount)} at {res.get('rate')} night of {night_s}" if res.get("rate") else res.get("note", ""))
    return {"status": "DONE", "posted": entry_id is not None, "posted_c": abs(amount), "amount_c": amount, "currency": spec.currency, "entry_id": entry_id,
            "leg": spec.side, "rate": res.get("rate"), "cum_c": res.get("cum_c"), "note": note}


def accrue_night(ctx: TxContext, services, kind: str, ref: str, night) -> dict:
    """The ACCRUE_DAILY hook: ``trsfin`` (``services.swaps_trs.accrue_night``) and the XCCY coupon legs ``swaprcv`` / ``swappay``
    (``swaps.accrue_night`` delegates them here); a pending night raises RejectError (the job SKIPs it and retries)."""
    services = _services(ctx, services)
    row = _contract(ctx.conn, ref)
    night_d = _date(night)
    if row["status"] != "OPEN":
        return {"status": "DONE", "posted": False, "posted_c": 0, "note": f"{ref} is {row['status']}: nothing accrues"}
    if kind == "trsfin":
        if row["kind"] != "TRS":
            raise RejectError("KIND_UNKNOWN", f"{ref} is a {row['kind']} contract: trsfin is the TRS financing leg")
        return _trs_night(ctx, services, row, night_d)
    if kind in ("swaprcv", "swappay"):
        if row["kind"] != "XCCY":
            raise RejectError("KIND_UNKNOWN", f"{ref} is a {row['kind']} contract: its {kind} leg is not accrued here")
        return _xccy_night(ctx, services, row, kind, night_d)
    raise RejectError("KIND_UNKNOWN", f"accrual kind {kind!r} is not a TRS / XCCY leg (trsfin, swaprcv, swappay)")


def _catch_up(ctx: TxContext, services, row: dict, kinds: Tuple[str, ...], through: date) -> List[int]:
    """Post the nights owed through ``through`` before a termination settles them; 409 with the pending reason otherwise."""
    conn = ctx.conn
    cid = row["contract_id"]
    start = _date(row["start_date"])
    posted: List[int] = []
    for kind in kinds:
        st = _state(conn, kind, cid)
        night = start if st is None or not st["accrued_through"] else _date(st["accrued_through"]) + timedelta(days=1)
        while night <= through:
            try:
                res = _trs_night(ctx, services, row, night) if kind == "trsfin" else _xccy_night(ctx, services, row, kind, night)
            except RejectError as e:
                raise ConflictError(e.code, f"{cid}: the termination settles the accruals through {through.isoformat()}, but the night of {night.isoformat()}"
                                    f" cannot accrue: {e.message}", e.details) from e
            if res.get("entry_id"):
                posted.append(int(res["entry_id"]))
            night += timedelta(days=1)
    return posted


# ---- TRS_RESET ----------------------------------------------------------------------------------------------------------------------
def h_reset(ctx: TxContext, payload: dict) -> dict:
    """``trs.reset`` for one (contract, reset date) — module docstring TRS_RESET.  Idempotent per date."""
    services = _services(ctx)
    conn = ctx.conn
    cid, ds, _ev = _key(payload, "trs.reset")
    key = f"{cid}:{ds}"
    idem = f"trsreset:{key}"
    row = _contract(conn, cid)
    aid = row["account_id"]
    if row["kind"] != "TRS":
        raise RejectError("KIND", f"{cid} is a {row['kind']} contract: trs.reset is for TRS contracts")
    if row["status"] != "OPEN":
        return _done_noop(ctx, JOB_RESET, key, "NOT_OPEN", f"{cid} is {row['status']}: no reset")
    done = conn.execute("SELECT amount_c, detail_json, entry_id FROM otc_resets WHERE contract_id=? AND event_date=? AND kind='RESET'", (cid, ds)).fetchone()
    if done is not None:
        _job_done(ctx, JOB_RESET, key, "DONE", "already reset")
        return {"status": "DONE", "already": True, "posted": False, "key": key, "cash_c": int(done["amount_c"]), "entry_id": done["entry_id"], "reset": _detail(done)}
    d = date.fromisoformat(ds)
    terms = _terms(row)
    periods = _periods(terms)
    k = next((i for i, q in enumerate(periods) if q[1] == d), None)
    if k is None:
        raise RejectError("KEY_INVALID", f"{ds} is not a reset date of {cid}")
    if ctx.ts < epoch_of(d, RESET_ET):
        return _skip(ctx, JOB_RESET, key, "RESET_NOT_YET", f"{cid}: the reset of {ds} runs from {RESET_ET} ET at the underlying's close", aid, cid, quiet=True)
    s = periods[k][0]
    if _date(row["last_reset_date"]) < s:
        return _skip(ctx, JOB_RESET, key, "RESET_ORDER", f"{cid}: the reset of {s.isoformat()} has not run yet; resets settle in date order", aid, cid)
    uid = terms["underlying_id"]
    inst = services.instruments.get(uid, conn)
    if inst is None:
        raise RejectError("UNKNOWN_UNDERLYING", f"{uid} is not in the instrument master")
    close_px, close_in = close_on(services, conn, ctx.marks, inst, d)
    if close_px is None:
        return _skip(ctx, JOB_RESET, key, "CLOSE_PENDING", f"{cid}: no {uid} CLOSE for {ds} yet ({close_in.get('note')}); the reset waits for the observed close"
                     " (never a default)", aid, cid, {"underlying_id": uid, "date": ds})
    st = _state(conn, "trsfin", cid)
    need = (d - timedelta(days=1)).isoformat()
    through = st["accrued_through"] if st is not None else None
    if through is None or through < need:
        return _skip(ctx, JOB_RESET, key, "ACCRUALS_PENDING", f"{cid}: the financing is accrued through {through or 'nothing'}, the period ends {ds} (ACCRUE_DAILY"
                     " first)", aid, cid, {"accrued_through": through, "needed": need})
    divs = _trs_dividends(ctx, row, terms, d)
    receiver = bool(terms.get("receiver"))
    sign = 1 if receiver else -1
    units = int(terms["units"])
    reset_px = qprice(row["reset_px"])
    ret = sign * to_c(Decimal(units) * (close_px - reset_px))
    mode = row["margin_mode"]
    vb = int(row["vm_balance_c"] or 0)
    ref = _vm_reference(conn, cid)
    settle = ret if mode == "CTM" else ret - ref
    recv = balance(conn, aid, "1225", "USD", cid)
    payb = balance(conn, aid, "2340", "USD", cid)
    lines: List[Line] = []
    cash = 0
    if settle:
        lines += [dr(aid, "1010", "USD", settle, memo="TRS return leg (received if positive)"),
                  cr(aid, "4450", "USD", settle, instrument_id=cid, memo=f"return leg {uid} {reset_px} -> {close_px} x {units:,}")]
        cash += settle
    if recv:
        lines += [dr(aid, "1010", "USD", recv, memo="TRS accrued receivable settled"), cr(aid, "1225", "USD", recv, instrument_id=cid, memo="accrued receivable settled")]
        cash += recv
    if payb:
        lines += [dr(aid, "2340", "USD", payb, instrument_id=cid, memo="accrued payable settled"), cr(aid, "1010", "USD", payb, memo="TRS accrued payable paid")]
        cash -= payb
    if mode == "CTM" and vb > 0:
        lines += [dr(aid, "2330", "USD", vb, instrument_id=cid, memo="VM received returned (re-measured at the reset)"),
                  cr(aid, "1010", "USD", vb, memo="VM collateral returned to the PB")]
        cash -= vb
    elif mode == "CTM" and vb < 0:
        lines += [dr(aid, "1010", "USD", -vb, memo="VM posted returned by the PB"), cr(aid, "1320", "USD", -vb, instrument_id=cid, memo="VM posted released at the reset")]
        cash += -vb
    funding = None
    if cash < 0:
        with swaps.asof_context(ctx, d):          # the reset of d is funded on d (C10 / C20) with CSA netting (C21)
            funding = _fund(ctx, services, aid, "USD", -cash, "trsreset", idem, overdraft=True)
    new_notional = to_c(Decimal(units) * close_px)
    memo = (f"TRS reset {cid} {ds}: return {'+' if sign > 0 else '-'}{units:,} x ({uid} close {close_px} - reset {reset_px}) = {fmt_money(ret)}"
            f"{'' if mode == 'CTM' else f' (STM: {fmt_money(settle)} after the {fmt_money(ref)} settled daily)'}; accrued received {fmt_money(recv)}, accrued paid"
            f" {fmt_money(payb)}; VM balance {fmt_money(vb)} re-measured to 0 against the post-reset mark; net cash {fmt_money(cash)}; notional"
            f" {fmt_money(int(row['notional_c']))} -> {fmt_money(new_notional)} at {close_px} (SWAP_RESET)")
    entry_id = ctx.post(Entry("TRS_RESET", memo, lines, idem, ref_type="otc", ref_id=cid, business_date=ds)) if lines else None
    detail = {"close": close_in, "reset_px_old": str(reset_px), "reset_px": str(close_px), "units": units, "receiver": receiver, "return_c": ret,
              "settled_return_c": settle, "accrued_received_c": recv, "accrued_paid_c": payb, "vm_unwound_c": vb if mode == "CTM" else 0, "vm_reference_c": ref,
              "cash_c": cash, "notional_old_c": int(row["notional_c"]), "notional_c": new_notional, "post_mark_c": 0, "dividends": divs, "funding": funding,
              "period": [s.isoformat(), ds], "command_id": ctx.command_id}
    conn.execute("UPDATE otc_contracts SET reset_px=?, notional_c=?, last_reset_date=?, mark_c=0, mark_ts=?, vm_balance_c=0 WHERE contract_id=?",
                 (str(close_px), new_notional, ds, ctx.ts, cid))
    conn.execute("INSERT INTO otc_resets(contract_id, event_date, kind, amount_c, detail_json, entry_id) VALUES (?,?,'RESET',?,?,?)", (cid, ds, cash, dumps(detail), entry_id))
    ctx.touched_instruments.add((aid, cid))
    out = {"status": "DONE", "key": key, "posted": entry_id is not None, "entry_id": entry_id, "return_c": ret, "settled_return_c": settle, "accrued_received_c": recv,
           "accrued_paid_c": payb, "vm_unwound_c": detail["vm_unwound_c"], "cash_c": cash, "reset_px": str(close_px), "notional_c": new_notional, "dividends": divs,
           "funding": funding, "close": close_in}
    if ds == row["maturity_date"]:
        closed = swaps.close_contract(ctx, services, _contract(conn, cid), "MATURED", 0, 0, f"otc:{cid}:mature", "OTC_MATURE",
                                      f"TRS maturity {cid} {ds}: the last reset settled every leg; IM {fmt_money(int(row['im_c']))} released (SWAP_RESET)", False, True)
        conn.execute("DELETE FROM accrual_state WHERE kind='trsfin' AND ref_id=?", (cid,))
        out["matured"] = closed
        ctx.event("SWAP_MATURED", account_id=aid, instrument_id=cid, ref_type="otc", ref_id=cid, date=ds, **{k2: v for k2, v in closed.items() if k2 != "funding"})
    _job_done(ctx, JOB_RESET, key, "DONE", f"return {fmt_money(ret)}, net cash {fmt_money(cash)}, notional {fmt_money(new_notional)}" + ("; matured" if "matured" in out else ""))
    _resolve(ctx, ("CLOSE_PENDING", "ACCRUALS_PENDING", "RESET_ORDER"), key)
    ctx.event("TRS_RESET", account_id=aid, instrument_id=cid, ref_type="otc", ref_id=cid, date=ds, return_c=ret, cash_c=cash, reset_px=str(close_px),
              notional_c=new_notional, entry_id=entry_id)
    return out


# ---- XCCY events: START, PAY, FX_RESET, END ---------------------------------------------------------------------------------------------
def _xccy_row(conn: sqlite3.Connection, cid: str) -> dict:
    row = _contract(conn, cid)
    if row["kind"] != "XCCY":
        raise RejectError("KIND", f"{cid} is a {row['kind']} contract: the XCCY events are for cross-currency swaps")
    return row


def _ev_start(ctx: TxContext, services, row: dict, d: date, key: str, job: Optional[str]) -> dict:
    conn = ctx.conn
    cid, aid = row["contract_id"], row["account_id"]
    done = conn.execute("SELECT entry_id FROM otc_resets WHERE contract_id=? AND kind='NOTIONAL_START'", (cid,)).fetchone()
    if done is not None:
        _job_done(ctx, job, key, "DONE", "already exchanged")
        return {"status": "DONE", "already": True, "posted": False, "key": key, "entry_id": done["entry_id"]}
    if d.isoformat() != row["start_date"]:
        raise RejectError("KEY_INVALID", f"{d.isoformat()} is not the start date of {cid} ({row['start_date']})")
    if ctx.ts < epoch_of(d, EVENT_ET):
        return _skip(ctx, job, key, "START_NOT_YET", f"{cid}: the start exchange runs from {EVENT_ET} ET on {d.isoformat()}", aid, cid, quiet=True)
    conn.execute("SAVEPOINT xccy_start")
    try:
        res = _start_exchange(ctx, services, row, overdraft=False)
        conn.execute("RELEASE SAVEPOINT xccy_start")
    except ConflictError as e:
        conn.execute("ROLLBACK TO SAVEPOINT xccy_start")
        conn.execute("RELEASE SAVEPOINT xccy_start")
        if e.code not in ("INSUFFICIENT_FUNDS", "INSUFFICIENT_FUNDS_CCY"):
            raise
        return _skip(ctx, job, key, e.code, f"{cid}: the start exchange cannot be funded: {e.message} (never automatic FX; retried)", aid, cid, e.details, severity="ERROR")
    _job_done(ctx, job, key, "DONE", f"start exchange posted (entry {res['entry_id']})")
    _resolve(ctx, ("INSUFFICIENT_FUNDS", "INSUFFICIENT_FUNDS_CCY"), key)
    return dict(res, status="DONE", key=key, posted=True)


def _xccy_pay(ctx: TxContext, services, row: dict, d: date, job: Optional[str], key: str) -> dict:
    """Both legs' coupons of the period paid on ``d`` (module docstring XCCY COUPONS: PAY)."""
    conn = ctx.conn
    cid, aid = row["contract_id"], row["account_id"]
    ds = d.isoformat()
    done = conn.execute("SELECT amount_c, detail_json, entry_id FROM otc_resets WHERE contract_id=? AND event_date=? AND kind='PAY'", (cid, ds)).fetchone()
    if done is not None:
        _job_done(ctx, job, key, "DONE", "already paid")
        return {"status": "DONE", "already": True, "posted": False, "key": key, "entry_id": done["entry_id"], "payment": _detail(done)}
    terms = _terms(row)
    periods = _periods(terms)
    k = next((i for i, q in enumerate(periods) if q[2] == d), None)
    if k is None:
        raise RejectError("KEY_INVALID", f"{ds} is not a payment date of {cid}")
    if ctx.ts < epoch_of(d, EVENT_ET):
        return _skip(ctx, job, key, "PAY_NOT_YET", f"{cid}: the payment of {ds} runs from {EVENT_ET} ET", aid, cid, quiet=True)
    if not _has_reset(conn, cid, "NOTIONAL_START"):
        return _skip(ctx, job, key, "START_PENDING", f"{cid}: the start exchange has not happened", aid, cid)
    s, e, _p = periods[k]
    legs: Dict[str, dict] = {}
    for side, kind in (("receive", "swaprcv"), ("pay", "swappay")):
        leg = terms["legs"][side]
        ccy = leg["currency"]
        notional = _leg_notional(conn, row, terms, periods, side, k)
        if notional is None:
            return _skip(ctx, job, key, "FX_RESET_PENDING", f"{cid}: the USD notional of the period from {s.isoformat()} waits for its FX reset", aid, cid)
        st = _state(conn, kind, cid)
        through = st["accrued_through"] if st is not None else None
        last_night = (e - timedelta(days=1)).isoformat()
        if through is None or through < last_night:
            return _skip(ctx, job, key, "ACCRUALS_PENDING", f"{cid}: the {side} leg is accrued through {through or 'nothing'}, the period ends {e.isoformat()}"
                         " (ACCRUE_DAILY first)", aid, cid, {"leg": side, "accrued_through": through, "needed": last_night})
        amount = swaps.coupon_c(notional, qprice(leg["rate"]), s, e, leg["basis"])
        gl = "1225" if side == "receive" else "2340"
        have = balance(conn, aid, gl, ccy, cid)
        if have < amount:
            return _skip(ctx, job, key, "ACCRUALS_PENDING", f"{cid}: the {side} leg accrued {ccy} {fmt_money(have)} in {gl}, the period coupon is"
                         f" {fmt_money(amount)}", aid, cid)
        legs[side] = {"currency": ccy, "gl": gl, "notional_c": notional, "amount_c": amount, "rate": leg["rate"], "basis": leg["basis"],
                      "days": swaps.period_days(s, e, leg["basis"]), "period": [s.isoformat(), e.isoformat()]}
    idem = f"xccypay:{cid}:{ds}"
    rl, pl = legs["receive"], legs["pay"]
    funding = _fund(ctx, services, aid, pl["currency"], pl["amount_c"], "xccypay", idem, overdraft=True)
    lines: List[Line] = []
    if rl["amount_c"]:
        lines += [dr(aid, "1010", rl["currency"], rl["amount_c"], memo="receive leg coupon received"),
                  cr(aid, "1225", rl["currency"], rl["amount_c"], instrument_id=cid, memo="receive leg coupon settled")]
    if pl["amount_c"]:
        lines += [dr(aid, "2340", pl["currency"], pl["amount_c"], instrument_id=cid, memo="pay leg coupon settled"),
                  cr(aid, "1010", pl["currency"], pl["amount_c"], memo="pay leg coupon paid")]
    memo = (f"XCCY payment {cid} {ds}: receive {rl['currency']} {fmt_money(rl['amount_c'])} ({rl['rate']} x {rl['days']} days {rl['basis']} on"
            f" {fmt_money(rl['notional_c'])}) / pay {pl['currency']} {fmt_money(pl['amount_c'])} ({pl['rate']} x {pl['days']} days {pl['basis']} on"
            f" {fmt_money(pl['notional_c'])}); each leg settles in its own currency (SWAP_RESET)")
    entry_id = ctx.post(Entry("XCCY_PAY", memo, lines, idem, ref_type="otc", ref_id=cid, business_date=ds)) if lines else None

    def usd(amount_c: int, ccy: str) -> int:
        return amount_c if ccy == "USD" else to_c(from_c(amount_c) * ctx.fx.rate(ccy))
    net_usd = usd(rl["amount_c"], rl["currency"]) - usd(pl["amount_c"], pl["currency"])
    detail = {"legs": legs, "net_usd_c": net_usd, "funding": funding, "entry_id": entry_id}
    conn.execute("INSERT INTO otc_resets(contract_id, event_date, kind, amount_c, detail_json, entry_id) VALUES (?,?,'PAY',?,?,?)", (cid, ds, net_usd, dumps(detail), entry_id))
    _job_done(ctx, job, key, "DONE", f"receive {rl['currency']} {fmt_money(rl['amount_c'])}, pay {pl['currency']} {fmt_money(pl['amount_c'])}")
    _resolve(ctx, ("ACCRUALS_PENDING", "FX_RESET_PENDING", "START_PENDING"), key)
    ctx.touched_instruments.add((aid, cid))
    ctx.event("XCCY_PAY", account_id=aid, instrument_id=cid, ref_type="otc", ref_id=cid, date=ds, legs=legs, net_usd_c=net_usd, entry_id=entry_id)
    return {"status": "DONE", "key": key, "posted": entry_id is not None, "entry_id": entry_id, "legs": legs, "net_usd_c": net_usd, "funding": funding}


def _ev_fx_reset(ctx: TxContext, services, row: dict, d: date, key: str) -> dict:
    """The MTM reset of the USD notional at the FX close (module docstring XCCY FX_RESET)."""
    conn = ctx.conn
    cid, aid = row["contract_id"], row["account_id"]
    ds = d.isoformat()
    terms = _terms(row)
    if not terms.get("mtm_reset"):
        return _done_noop(ctx, JOB_EVENTS, key, "NOT_MTM", f"{cid} was booked with fixed notionals (no FX reset)")
    done = conn.execute("SELECT amount_c, detail_json, entry_id FROM otc_resets WHERE contract_id=? AND event_date=? AND kind='FX_RESET'", (cid, ds)).fetchone()
    if done is not None:
        _job_done(ctx, JOB_EVENTS, key, "DONE", "already reset")
        return {"status": "DONE", "already": True, "posted": False, "key": key, "entry_id": done["entry_id"], "reset": _detail(done)}
    periods = _periods(terms)
    k = next((i for i, q in enumerate(periods) if q[0] == d), None)
    if k is None or k == 0:
        raise RejectError("KEY_INVALID", f"{ds} is not an FX reset date of {cid} (the starts of its periods after the first)")
    if ctx.ts < epoch_of(d, FX_RESET_ET):
        return _skip(ctx, JOB_EVENTS, key, "FX_RESET_NOT_YET", f"{cid}: the FX reset of {ds} runs from {FX_RESET_ET} ET at the FX close", aid, cid, quiet=True)
    if not _has_reset(conn, cid, "NOTIONAL_START"):
        return _skip(ctx, JOB_EVENTS, key, "START_PENDING", f"{cid}: the start exchange has not happened", aid, cid)
    old = usd_notional(conn, row, terms, periods, k - 1)
    if old is None:
        return _skip(ctx, JOB_EVENTS, key, "FX_RESET_ORDER", f"{cid}: the FX reset of {periods[k - 1][0].isoformat()} has not run yet", aid, cid)
    foreign = terms["foreign_ccy"]
    fx_px, fx_in = fx_close_on(services, conn, ctx.marks, foreign, d)
    if fx_px is None:
        return _skip(ctx, JOB_EVENTS, key, "CLOSE_PENDING", f"{cid}: no {fx_pair_id(foreign)} close for {ds} yet ({fx_in.get('note')}); the FX reset waits for the"
                     " observed close (never a default)", aid, cid, {"pair": fx_pair_id(foreign), "date": ds})
    f_notional = int(terms["legs"]["pay" if terms["usd_side"] == "receive" else "receive"]["notional_c"])
    new = to_c(from_c(f_notional) * fx_px)
    diff = new - old
    lent = terms["usd_side"] == "receive"
    idem = f"xccyfx:{cid}:{ds}"
    lines: List[Line] = []
    cash = 0
    if diff and lent:
        lines = [dr(aid, "1360", "USD", diff, instrument_id=cid, memo="USD notional lent re-struck"), cr(aid, "1010", "USD", diff, memo="FX reset difference")]
        cash = -diff
    elif diff:
        lines = [dr(aid, "1010", "USD", diff, memo="FX reset difference"), cr(aid, "2360", "USD", diff, instrument_id=cid, memo="USD notional borrowed re-struck")]
        cash = diff
    funding = _fund(ctx, services, aid, "USD", -cash, "xccyfx", idem, overdraft=True) if cash < 0 else None
    memo = (f"XCCY FX reset {cid} {ds}: USD notional {fmt_money(old)} -> {foreign} {fmt_money(f_notional)} x {fx_px} ({fx_in.get('basis')}) = {fmt_money(new)};"
            f" the difference {fmt_money(diff)} {'paid (we lend more USD)' if lent and diff > 0 else 'received back' if lent and diff < 0 else 'received (we borrow more USD)' if diff > 0 else 'repaid' if diff < 0 else 'is 0'}"
            f" in cash, so the notional FX effect is realised (SWAP_RESET)")
    entry_id = ctx.post(Entry("XCCY_FX_RESET", memo, lines, idem, ref_type="otc", ref_id=cid, business_date=ds)) if lines else None
    detail = {"fx": fx_in, "usd_per_unit": str(fx_px), "foreign_ccy": foreign, "foreign_notional_c": f_notional, "old_usd_notional_c": old, "usd_notional_c": new,
              "diff_c": diff, "cash_c": cash, "usd_leg": "lent (1360)" if lent else "borrowed (2360)", "period": [periods[k][0].isoformat(), periods[k][1].isoformat()],
              "funding": funding}
    conn.execute("UPDATE otc_contracts SET notional_c=?, reset_px=?, last_reset_date=? WHERE contract_id=?", (new, str(fx_px), ds, cid))
    conn.execute("INSERT INTO otc_resets(contract_id, event_date, kind, amount_c, detail_json, entry_id) VALUES (?,?,'FX_RESET',?,?,?)", (cid, ds, cash, dumps(detail), entry_id))
    _job_done(ctx, JOB_EVENTS, key, "DONE", f"USD notional {fmt_money(old)} -> {fmt_money(new)} at {fx_px}; cash {fmt_money(cash)}")
    _resolve(ctx, ("CLOSE_PENDING", "START_PENDING", "FX_RESET_ORDER"), key)
    ctx.touched_instruments.add((aid, cid))
    ctx.event("XCCY_FX_RESET", account_id=aid, instrument_id=cid, ref_type="otc", ref_id=cid, date=ds, usd_per_unit=str(fx_px), old_usd_notional_c=old, usd_notional_c=new,
              diff_c=diff, cash_c=cash, entry_id=entry_id)
    return {"status": "DONE", "key": key, "posted": entry_id is not None, "entry_id": entry_id, "fx": str(fx_px), "old_usd_notional_c": old, "usd_notional_c": new,
            "diff_c": diff, "cash_c": cash, "funding": funding}


def _ev_end(ctx: TxContext, services, row: dict, d: date, key: str, job: Optional[str]) -> dict:
    """Maturity: the last coupons, the reverse exchange, then the close (module docstring XCCY END)."""
    conn = ctx.conn
    cid, aid = row["contract_id"], row["account_id"]
    ds = d.isoformat()
    done = conn.execute("SELECT entry_id FROM otc_resets WHERE contract_id=? AND kind='NOTIONAL_END'", (cid,)).fetchone()
    if done is not None:
        _job_done(ctx, job, key, "DONE", "already matured")
        return {"status": "DONE", "already": True, "posted": False, "key": key, "entry_id": done["entry_id"]}
    if ds != row["maturity_date"]:
        raise RejectError("KEY_INVALID", f"{ds} is not the maturity of {cid} ({row['maturity_date']})")
    if ctx.ts < epoch_of(d, EVENT_ET):
        return _skip(ctx, job, key, "END_NOT_YET", f"{cid}: the maturity exchange runs from {EVENT_ET} ET on {ds}", aid, cid, quiet=True)
    if not _has_reset(conn, cid, "NOTIONAL_START"):
        return _skip(ctx, job, key, "START_PENDING", f"{cid}: the start exchange has not happened", aid, cid)
    pay_out = None
    if not _has_reset(conn, cid, "PAY", ds):
        pay_out = _xccy_pay(ctx, services, row, d, None, key)
        if pay_out.get("status") == "SKIPPED":
            _job_done(ctx, job, key, "SKIPPED", pay_out["note"])
            return pay_out
    terms = _terms(row)
    lc, bc = terms["legs"]["receive"]["currency"], terms["legs"]["pay"]["currency"]
    ln = balance(conn, aid, "1360", lc, cid)
    bn = balance(conn, aid, "2360", bc, cid)
    idem = f"xccy:{cid}:end"
    funding = _fund(ctx, services, aid, bc, bn, "xccy", idem, overdraft=True)
    lines: List[Line] = []
    if ln:
        lines += [dr(aid, "1010", lc, ln, memo="notional lent received back"), cr(aid, "1360", lc, ln, instrument_id=cid, memo="notional receivable settled")]
    if bn:
        lines += [dr(aid, "2360", bc, bn, instrument_id=cid, memo="notional payable settled"), cr(aid, "1010", bc, bn, memo="notional borrowed repaid")]
    memo = (f"XCCY maturity exchange {cid} {ds}: we receive back {lc} {fmt_money(ln)} (lent) and repay {bc} {fmt_money(bn)} (borrowed), each in its own currency"
            " at this command's FX rate (the FX result since the start is realised) (SWAP_RESET)")
    entry_id = ctx.post(Entry("XCCY_EXCHANGE", memo, lines, idem, ref_type="otc", ref_id=cid, business_date=ds)) if lines else None
    detail = {"we_receive": {"currency": lc, "amount_c": ln, "gl": "1360"}, "we_pay": {"currency": bc, "amount_c": bn, "gl": "2360"}, "funding": funding, "entry_id": entry_id}
    conn.execute("INSERT INTO otc_resets(contract_id, event_date, kind, amount_c, detail_json, entry_id) VALUES (?,?,'NOTIONAL_END',?,?,?)",
                 (cid, ds, ln if lc == "USD" else bn, dumps(detail), entry_id))
    closed = swaps.close_contract(ctx, services, _contract(conn, cid), "MATURED", 0, _vm_reference(conn, cid), f"otc:{cid}:mature", "OTC_MATURE",
                                  f"XCCY maturity {cid} {ds}: VM balance unwound, IM {fmt_money(int(row['im_c']))} released (SWAP_RESET)", False, True)
    _job_done(ctx, job, key, "DONE", f"matured: receive back {lc} {fmt_money(ln)}, repay {bc} {fmt_money(bn)}")
    _resolve(ctx, ("ACCRUALS_PENDING", "FX_RESET_PENDING", "START_PENDING"), key)
    ctx.touched_instruments.add((aid, cid))
    ctx.event("SWAP_MATURED", account_id=aid, instrument_id=cid, ref_type="otc", ref_id=cid, date=ds, kind="XCCY", entry_id=entry_id,
              **{k2: v for k2, v in closed.items() if k2 not in ("funding", "entry_id")})
    return {"status": "DONE", "key": key, "posted": True, "entry_id": entry_id, "pay": pay_out, "matured": closed, "funding": funding, "exchange": detail}


def h_exchange(ctx: TxContext, payload: dict) -> dict:
    """``xccy.exchange`` for one (contract, date, event): START, PAY or END (FX_RESET is accepted too)."""
    services = _services(ctx)
    cid, ds, ev = _key(payload, "xccy.exchange")
    if ev not in EVENT_ORDER:
        raise RejectError("KEY_INVALID", f"xccy.exchange needs an event START, PAY, FX_RESET or END (got {ev!r})")
    key = f"{cid}:{ds}:{ev}"
    row = _xccy_row(ctx.conn, cid)
    if row["status"] != "OPEN":
        return _done_noop(ctx, JOB_EVENTS, key, "NOT_OPEN", f"{cid} is {row['status']}")
    d = date.fromisoformat(ds)
    if ev == "START":
        return _ev_start(ctx, services, row, d, key, JOB_EVENTS)
    if ev == "PAY":
        return _xccy_pay(ctx, services, row, d, JOB_EVENTS, key)
    if ev == "FX_RESET":
        return _ev_fx_reset(ctx, services, row, d, key)
    return _ev_end(ctx, services, row, d, key, JOB_EVENTS)


def h_fx_reset(ctx: TxContext, payload: dict) -> dict:
    """``xccy.fx_reset`` for one (contract, reset date) — module docstring XCCY FX_RESET."""
    cid, ds, _ev = _key(payload, "xccy.fx_reset")
    key = f"{cid}:{ds}:FX_RESET"
    row = _xccy_row(ctx.conn, cid)
    if row["status"] != "OPEN":
        return _done_noop(ctx, JOB_EVENTS, key, "NOT_OPEN", f"{cid} is {row['status']}")
    return _ev_fx_reset(ctx, _services(ctx), row, date.fromisoformat(ds), key)


def pay(ctx: TxContext, payload: dict) -> dict:
    """``swap.pay`` delegated by WP-17: an XCCY payment date pays both legs (the maturity runs the END event); a TRS pays at
    its resets (``trs.reset``) — a no-op here."""
    services = _services(ctx)
    cid, ds, _ev = _key(payload, "swap.pay")
    key = f"{cid}:{ds}"
    row = _contract(ctx.conn, cid)
    if row["status"] != "OPEN":
        return _done_noop(ctx, swaps.JOB_PAY, key, "NOT_OPEN", f"{cid} is {row['status']}")
    if row["kind"] == "XCCY":
        if ds == row["maturity_date"]:
            return _ev_end(ctx, services, row, date.fromisoformat(ds), key, swaps.JOB_PAY)
        return _xccy_pay(ctx, services, row, date.fromisoformat(ds), swaps.JOB_PAY, key)
    return _done_noop(ctx, swaps.JOB_PAY, key, "TRS_PAYS_AT_RESET", f"{cid} settles its legs at its resets (trs.reset), not through swap.pay")


def fix(ctx: TxContext, payload: dict) -> dict:
    """``swap.fix`` delegated by WP-17: TRS financing uses the nightly SOFR print and XCCY legs are fixed — nothing to fix."""
    cid, ds, _ev = _key(payload, "swap.fix")
    return _done_noop(ctx, swaps.JOB_FIX, f"{cid}:{ds}", "NO_FIXING", f"{cid}: TRS financing accrues at the nightly SOFR print and cross-currency legs are"
                      " fixed-rate; there is no reset fixing")


# ---- termination (otc.terminate, owner) ---------------------------------------------------------------------------------------------
def _terminate_trs(ctx: TxContext, services, row: dict) -> Tuple[dict, int, dict, List[int]]:
    conn = ctx.conn
    cid = row["contract_id"]
    terms = _terms(row)
    uid = terms["underlying_id"]
    fetching = _pin(services, [uid])
    mv = ctx.marks.mark(uid) if ctx.marks is not None else None
    px = _usable(mv, f"{uid} (the TRS termination price)", fetching)
    today = ny_of(ctx.ts).date()
    through = min(today, _date(row["maturity_date"])) - timedelta(days=1)
    posted = _catch_up(ctx, services, row, ("trsfin",), through)
    divs = _trs_dividends(ctx, row, terms, today)
    row = _contract(conn, cid)
    sign = 1 if terms.get("receiver") else -1
    units = int(terms["units"])
    reset_px = qprice(row["reset_px"])
    value = sign * to_c(Decimal(units) * (px - reset_px))
    ref = _vm_reference(conn, cid)
    memo = (f"TRS termination {cid}: value {'+' if sign > 0 else '-'}{units:,} x ({uid} {_px_text(px)} ({quote_label(mv)}) - reset {_px_text(reset_px)}) ="
            f" {fmt_money(value)}; VM balance {fmt_money(int(row['vm_balance_c']))} unwound; accrued financing and dividends settled through the night of"
            f" {through.isoformat()} (SWAP_MARK, SWAP_RESET)")
    closed = swaps.close_contract(ctx, services, row, "TERMINATED", value, ref, f"otc:{cid}:terminate", "OTC_TERMINATE", memo, True, False)
    conn.execute("DELETE FROM accrual_state WHERE kind='trsfin' AND ref_id=?", (cid,))
    inputs = {"S": str(px), "quote": _quote_dict(mv), "reset_px": str(reset_px), "units": units, "receiver": bool(terms.get("receiver")), "vm_reference_c": ref,
              "accrual_entries": posted, "dividends": divs, "model": TRS_MODEL}
    return closed, value, inputs, posted + [x["entry_id"] for x in divs]


def _terminate_xccy(ctx: TxContext, services, row: dict) -> Tuple[dict, int, dict, List[int]]:
    conn = ctx.conn
    cid, aid = row["contract_id"], row["account_id"]
    got = mark_xccy(services, row, at=ctx.ts, snapshot=ctx.marks)
    if not isinstance(got, tuple):
        raise ConflictError("NO_CURVE", f"{cid}: {getattr(got, 'note', 'no mark')}: a termination needs the current model mark", {"contract_id": cid})
    value, inputs = got
    terms = _terms(row)
    today = ny_of(ctx.ts).date()
    entries: List[int] = []
    exch = None
    if _has_reset(conn, cid, "NOTIONAL_START"):
        through = min(today, _date(row["maturity_date"])) - timedelta(days=1)
        entries += _catch_up(ctx, services, row, ("swaprcv", "swappay"), through)
        lc, bc = terms["legs"]["receive"]["currency"], terms["legs"]["pay"]["currency"]
        racc, pacc = balance(conn, aid, "1225", lc, cid), balance(conn, aid, "2340", bc, cid)
        ln, bn = balance(conn, aid, "1360", lc, cid), balance(conn, aid, "2360", bc, cid)
        idem = f"xccy:{cid}:terminate"
        funding = _fund(ctx, services, aid, bc, pacc + bn, "xccy", idem, overdraft=False, commit=True, csa=True)
        lines: List[Line] = []
        if racc:
            lines += [dr(aid, "1010", lc, racc, memo="accrued receive leg settled"), cr(aid, "1225", lc, racc, instrument_id=cid, memo="accrued receive leg settled")]
        if ln:
            lines += [dr(aid, "1010", lc, ln, memo="notional lent received back"), cr(aid, "1360", lc, ln, instrument_id=cid, memo="notional receivable settled")]
        if pacc:
            lines += [dr(aid, "2340", bc, pacc, instrument_id=cid, memo="accrued pay leg settled"), cr(aid, "1010", bc, pacc, memo="accrued pay leg paid")]
        if bn:
            lines += [dr(aid, "2360", bc, bn, instrument_id=cid, memo="notional payable settled"), cr(aid, "1010", bc, bn, memo="notional borrowed repaid")]
        memo = (f"XCCY termination exchange {cid}: accrued coupons settle (receive {lc} {fmt_money(racc)}, pay {bc} {fmt_money(pacc)}) and the notionals are"
                f" re-exchanged at face (receive back {lc} {fmt_money(ln)}, repay {bc} {fmt_money(bn)}) in their own currencies (SWAP_RESET)")
        entry_id = ctx.post(Entry("XCCY_EXCHANGE", memo, lines, idem, ref_type="otc", ref_id=cid)) if lines else None
        exch = {"accrued_receive_c": racc, "accrued_pay_c": pacc, "lent_back_c": ln, "repaid_c": bn, "funding": funding, "entry_id": entry_id}
        conn.execute("INSERT INTO otc_resets(contract_id, event_date, kind, amount_c, detail_json, entry_id) VALUES (?,?,'NOTIONAL_END',?,?,?)",
                     (cid, today.isoformat(), ln if lc == "USD" else bn, dumps(dict(exch, terminated=True)), entry_id))
        if entry_id is not None:
            entries.append(entry_id)
    ref = _vm_reference(conn, cid)
    memo = (f"XCCY termination {cid}: value {fmt_money(value)} = the current model mark of the coupon legs; VM balance {fmt_money(int(row['vm_balance_c']))} unwound;"
            " IM released")
    closed = swaps.close_contract(ctx, services, _contract(conn, cid), "TERMINATED", value, ref, f"otc:{cid}:terminate", "OTC_TERMINATE", memo, False, False)
    return closed, value, dict(inputs, exchange=exch, vm_reference_c=ref), entries


def terminate(ctx: TxContext, payload: dict) -> dict:
    """``otc.terminate`` for TRS / XCCY (WP-17 checked that the contract is OPEN) — module docstring TERMINATION."""
    services = _services(ctx)
    conn = ctx.conn
    cid = str(payload.get("contract_id") or "")
    row = _contract(conn, cid)
    if row["status"] != "OPEN":
        raise ConflictError("NOT_OPEN", f"{cid} is {row['status']}; only an OPEN contract can be terminated", {"contract_id": cid, "status": row["status"]})
    acct = account_row(conn, row["account_id"])
    if acct is None or acct["status"] != "OPEN":
        raise RejectError("ACCOUNT_CLOSED", f"account {row['account_id']} is not open")
    if row["kind"] == "TRS":
        closed, value, inputs, extra = _terminate_trs(ctx, services, row)
    elif row["kind"] == "XCCY":
        closed, value, inputs, extra = _terminate_xccy(ctx, services, row)
    else:
        raise RejectError("OTC_KIND", f"{cid} is a {row['kind']} contract (IRS terminations are products/swaps.py)")
    ctx.event("OTC_TERMINATED", account_id=row["account_id"], instrument_id=cid, ref_type="otc", ref_id=cid, kind=row["kind"], termination_value_c=value,
              **{k: v for k, v in closed.items() if k not in ("funding", "value_c")})
    view = swaps.contract_view(services, cid, conn=conn, detail=True, now_ts=ctx.ts)
    entries = [e for e in extra + [closed["entry_id"], closed["im_entry_id"]] if e is not None]
    return {"contract": view, "contract_id": cid, "termination_value_c": value, "result_c": closed["result_c"], "cash_c": closed["cash_c"], "entries": entries,
            "im_released_c": closed["im_released_c"], "accrued_receive_c": closed["accrued_receive_c"], "accrued_pay_c": closed["accrued_pay_c"], "inputs": inputs,
            "cost_released_c": closed.get("cost_released_c", 0), "realised_c": closed.get("realised_c")}


# ---- lifecycle key helpers (WP-08 ProductJob: keys_fn(conn, now_ts)) -------------------------------------------------------------------
def reset_keys(conn: sqlite3.Connection, now_ts: int) -> List[Tuple[str, str]]:
    """``(contract_id, reset date)`` of every OPEN TRS whose reset date is today or earlier (16:25 ET passed), not reset and not
    DONE in ``jobs_done``; in date order per contract."""
    now = int(now_ts)
    today = ny_of(now).date()
    out: List[Tuple[str, str]] = []
    for row in _open_contracts(conn, "TRS"):
        cid = row["contract_id"]
        done = _jobs(conn, JOB_RESET, cid)
        have = _resets(conn, cid, "RESET")
        for _s, e, _p in _periods(_terms(row)):
            if e > today or now < epoch_of(e, RESET_ET):
                break
            ds = e.isoformat()
            r = done.get(f"{cid}:{ds}")
            if ds in have or (r is not None and r["status"] == "DONE"):
                continue
            out.append((cid, ds))
    return out


def event_keys(conn: sqlite3.Connection, now_ts: int) -> List[Tuple[str, str, str]]:
    """``(contract_id, date, event)`` of every OPEN XCCY event that is due: START (00:45 ET of the start date, unless exchanged at
    booking), PAY (00:45 ET of each intermediate payment date), FX_RESET (17:05 ET of each period start after the first, MTM
    only) and END (00:45 ET of the maturity) — not recorded in ``otc_resets`` and not DONE; in date / event order."""
    now = int(now_ts)
    today = ny_of(now).date()
    out: List[Tuple[str, str, str]] = []
    for row in _open_contracts(conn, "XCCY"):
        cid = row["contract_id"]
        terms = _terms(row)
        periods = _periods(terms)
        if not periods:
            continue
        done = _jobs(conn, JOB_EVENTS, cid)
        have = {(r["event_date"], r["kind"]) for r in conn.execute("SELECT event_date, kind FROM otc_resets WHERE contract_id=?", (cid,)).fetchall()}
        cands = [(periods[0][0], "START", "NOTIONAL_START", EVENT_ET)]
        for _s, e, p in periods[:-1]:
            cands.append((p, "PAY", "PAY", EVENT_ET))
            if terms.get("mtm_reset"):
                cands.append((e, "FX_RESET", "FX_RESET", FX_RESET_ET))
        cands.append((periods[-1][2], "END", "NOTIONAL_END", EVENT_ET))
        for d, ev, kind, hhmm in sorted(cands, key=lambda c: (c[0], EVENT_ORDER[c[1]])):
            if d > today or now < epoch_of(d, hhmm):
                continue
            ds = d.isoformat()
            r = done.get(f"{cid}:{ds}:{ev}")
            if (ds, kind) in have or (r is not None and r["status"] == "DONE"):
                continue
            out.append((cid, ds, ev))
    return out


def next_event_due(conn: sqlite3.Connection, now_ts: int) -> Optional[Tuple[int, str]]:
    """P8B-15: ``(epoch, ref)`` of the next XCCY_EVENTS work - the earliest START / PAY / FX_RESET / END of an OPEN cross-currency swap that
    is neither recorded nor DONE (00:45 ET; an FX reset 17:05 ET; an overdue one is due now) - or None when no swap is open."""
    now = int(now_ts)
    best: Optional[Tuple[int, str]] = None
    words = {"START": "start exchange", "PAY": "payment", "FX_RESET": "FX reset", "END": "maturity"}
    for row in _open_contracts(conn, "XCCY"):
        cid = row["contract_id"]
        terms = _terms(row)
        periods = _periods(terms)
        if not periods:
            continue
        done = _jobs(conn, JOB_EVENTS, cid)
        have = {(r["event_date"], r["kind"]) for r in conn.execute("SELECT event_date, kind FROM otc_resets WHERE contract_id=?", (cid,)).fetchall()}
        cands = [(periods[0][0], "START", "NOTIONAL_START", EVENT_ET)]
        for _s, e, p in periods[:-1]:
            cands.append((p, "PAY", "PAY", EVENT_ET))
            if terms.get("mtm_reset"):
                cands.append((e, "FX_RESET", "FX_RESET", FX_RESET_ET))
        cands.append((periods[-1][2], "END", "NOTIONAL_END", EVENT_ET))
        for d, ev, kind, hhmm in sorted(cands, key=lambda c: (c[0], EVENT_ORDER[c[1]])):
            ds = d.isoformat()
            r = done.get(f"{cid}:{ds}:{ev}")
            if (ds, kind) in have or (r is not None and r["status"] == "DONE"):
                continue
            ts = max(now, epoch_of(d, hhmm))
            if best is None or ts < best[0]:
                best = (ts, f"{cid} {words.get(ev, ev.lower())} {ds}")
            break
    return best


try:   # P8B-15: GET /api/jobs shows when XCCY_EVENTS next has work (the job itself is lifecycle_eod's)
    from ..lifecycle_eod import add_next_due as _add_next_due
    _add_next_due(JOB_EVENTS, next_event_due)
except ImportError:
    pass


# ---- views ------------------------------------------------------------------------------------------------------------------------
def next_events(services, contract, conn: Optional[sqlite3.Connection] = None, limit: int = 4) -> List[dict]:
    """The next scheduled events (risk §14 and the contracts table): TRS resets / maturity with the accrued legs; XCCY payments
    with each leg's coupon in its currency (None while an FX reset has not set the USD notional), FX resets and the maturity."""
    if conn is None:
        with _marks.shared_reader(services.db) as c:        # ORC-6: never a second pooled reader
            return next_events(services, contract, c, limit)
    row = _contract_of(services, contract, conn)
    if row is None or row.get("status") != "OPEN" or row["kind"] not in ("TRS", "XCCY"):
        return []
    cid, aid = row["contract_id"], row["account_id"]
    today = ny_of(services.clock.epoch()).date()
    terms = _terms(row)
    periods = _periods(terms)
    events: List[dict] = []
    if row["kind"] == "TRS":
        last = row.get("last_reset_date") or ""
        upcoming = [q for q in periods if q[1].isoformat() > last and q[1] >= today]
        for i, (s, e, _p) in enumerate(upcoming):
            ev = {"date": e.isoformat(), "event": "MATURITY" if e.isoformat() == row["maturity_date"] else "RESET", "period": [s.isoformat(), e.isoformat()],
                  "detail": "the return leg at the underlying's close, the accrued financing and dividends settle; the notional is re-struck"}
            if i == 0:
                ev.update(accrued_receivable_c=balance(conn, aid, "1225", "USD", cid), accrued_payable_c=balance(conn, aid, "2340", "USD", cid),
                          mark_c=int(row.get("mark_c") or 0), legs=[])
            events.append(ev)
    else:
        maturity = row["maturity_date"]
        paid = {r["event_date"] for r in conn.execute("SELECT event_date FROM otc_resets WHERE contract_id=? AND kind='PAY'", (cid,)).fetchall()}
        fxr = {r["event_date"] for r in conn.execute("SELECT event_date FROM otc_resets WHERE contract_id=? AND kind='FX_RESET'", (cid,)).fetchall()}
        if periods and not _has_reset(conn, cid, "NOTIONAL_START"):
            # P8B-06: the start exchange of a swap that starts on its spot date, until it has run
            lent, bor = terms["legs"]["receive"], terms["legs"]["pay"]
            events.append({"date": periods[0][0].isoformat(), "event": "START",
                           "detail": (f"start exchange: we pay {lent['currency']} {fmt_money(_leg_notional(conn, row, terms, periods, 'receive', 0) or int(lent['notional_c']))}"
                                      f" (lent) and receive {bor['currency']} {fmt_money(_leg_notional(conn, row, terms, periods, 'pay', 0) or int(bor['notional_c']))}"
                                      f" (borrowed) at {terms.get('deal_rate')}")})
        for k, (s, e, p) in enumerate(periods):
            ps = p.isoformat()
            if p < today or ps in paid:
                continue
            legs = []
            for side in ("receive", "pay"):
                leg = terms["legs"][side]
                n = _leg_notional(conn, row, terms, periods, side, k)
                amount = None if n is None else swaps.coupon_c(n, qprice(leg["rate"]), s, e, leg["basis"])
                legs.append({"leg": "fixed", "side": side, "currency": leg["currency"], "amount_c": amount, "rate": leg["rate"], "known": amount is not None,
                             "period": [s.isoformat(), e.isoformat()]})
            events.append({"date": ps, "event": "MATURITY" if ps == maturity else "PAY", "legs": legs,
                           **({"detail": "last coupons, then the notionals are re-exchanged; VM unwound and IM released"} if ps == maturity else {})})
            if terms.get("mtm_reset") and ps != maturity and ps not in fxr:
                events.append({"date": ps, "event": "FX_RESET", "detail": f"USD notional re-struck at the {fx_pair_id(terms['foreign_ccy'])} close (17:00 ET)"})
        events.sort(key=lambda x: (x["date"], {"PAY": 0, "FX_RESET": 1, "MATURITY": 2}.get(x["event"], 3)))
    return events[:max(1, int(limit))]


def contract_detail(services, contract, conn: Optional[sqlite3.Connection] = None) -> Optional[dict]:
    """The TRS / XCCY specifics WP-17's ``contract_view`` does not show (an interface request asks it to merge them): TRS
    underlying, units, reset price, notional, financing rate, accrued legs and resets; XCCY legs per currency (notional,
    rate, accrued coupons, notional balance), deal rate and FX resets."""
    if conn is None:
        with _marks.shared_reader(services.db) as c:        # ORC-6: never a second pooled reader
            return contract_detail(services, contract, c)
    row = _contract_of(services, contract, conn)
    if row is None or row["kind"] not in ("TRS", "XCCY"):
        return None
    cid, aid = row["contract_id"], row["account_id"]
    terms = _terms(row)
    if row["kind"] == "TRS":
        rate = None
        try:
            obs, pct = sofr_for_night(services, ny_of(services.clock.epoch()).date())
            rate = {"sofr": str(pct), "obs_date": obs.isoformat(), "rate": str(fin_rate(pct, terms["spread"], bool(terms.get("receiver"))))}
        except RejectError:
            rate = None
        resets = [{"date": r["event_date"], "cash_c": int(r["amount_c"]), "entry_id": r["entry_id"], "detail": _detail(r)} for r in _resets(conn, cid, "RESET").values()]
        return {"kind": "TRS", "underlying_id": terms.get("underlying_id"), "units": terms.get("units"), "receiver": terms.get("receiver"),
                "side": "RECEIVE_TOTAL_RETURN" if terms.get("receiver") else "PAY_TOTAL_RETURN", "reset_px": row.get("reset_px"), "notional_c": int(row["notional_c"]),
                "spread": terms.get("spread"), "financing_now": rate, "accrued_receivable_c": balance(conn, aid, "1225", "USD", cid),
                "accrued_payable_c": balance(conn, aid, "2340", "USD", cid), "last_reset_date": row.get("last_reset_date"), "resets": resets,
                "reset_dates": [q[1].isoformat() for q in _periods(terms)]}
    legs = {}
    for side in ("receive", "pay"):
        leg = terms["legs"][side]
        ccy = leg["currency"]
        legs[side] = {"currency": ccy, "rate": leg["rate"], "basis": leg["basis"], "notional_c": int(row["notional_c"]) if ccy == "USD" else int(leg["notional_c"]),
                      "accrued_c": balance(conn, aid, "1225" if side == "receive" else "2340", ccy, cid),
                      "notional_balance_c": balance(conn, aid, "1360" if side == "receive" else "2360", ccy, cid),
                      "role": "lent (1360)" if side == "receive" else "borrowed (2360)"}
    fx = [{"date": r["event_date"], "cash_c": int(r["amount_c"]), "entry_id": r["entry_id"], "detail": _detail(r)} for r in _resets(conn, cid, "FX_RESET").values()]
    return {"kind": "XCCY", "pay_ccy": terms.get("pay_ccy"), "recv_ccy": terms.get("recv_ccy"), "deal_rate": terms.get("deal_rate"), "mtm_reset": terms.get("mtm_reset"),
            "legs": legs, "fx_resets": fx, "started": _has_reset(conn, cid, "NOTIONAL_START"), "matured_exchange": _has_reset(conn, cid, "NOTIONAL_END")}


def unsupported() -> List[dict]:
    """Settings > Unsupported: what the cross-currency swap of this build does not do (P8B-05: said where the owner looks, never found by
    refusal)."""
    return [{"item": "Floating cross-currency legs (SOFR, ESTR, SONIA, TONA ...) and a cross-currency basis", "state": "NOT_OFFERED",
             "wording": "cross-currency swaps are fixed / fixed in this version: no free non-USD reference fixing is sourced to set and settle a floating"
                        " leg, so both legs are fixed-rate (semi-annual) at the dealer's quote; one leg is USD",
             "where": "prime-broker OTC tab, Swaps (cross-currency swap)"}]


# ---- registry ---------------------------------------------------------------------------------------------------------------------
def register(bus, services=None) -> None:
    """Register ``trs.reset``, ``xccy.exchange`` and ``xccy.fx_reset`` (lifecycle); the runtime binds this module as
    ``services.swaps_trs`` so WP-17's hooks, ACCRUE_DAILY (``accrue_night`` for trsfin) and the TRS_RESET / XCCY_EVENTS
    ProductJobs (``reset_keys`` / ``event_keys``) reach it."""
    bus.register("trs.reset", h_reset)
    bus.register("xccy.exchange", h_exchange)
    bus.register("xccy.fx_reset", h_fx_reset)


__all__ = ["preview", "book", "terminate", "mark_trs", "mark_xccy", "dv01_c", "accrue_night", "pay", "fix", "next_events", "contract_detail", "reset_keys",
           "event_keys", "register", "h_reset", "h_exchange", "h_fx_reset", "parse_trs_terms", "parse_xccy_terms", "TrsTerms", "TrsDeal", "XLeg", "XccyDeal",
           "fin_rate", "flat_dfs", "foreign_curve", "leg_curve", "leg_value", "leg_dv01", "usd_notional", "xccy_leg_spec", "close_on", "fx_close_on",
           "CONVENTIONS", "TRS_MODEL", "XCCY_MODEL", "TRS_IM_PCT", "XCCY_IM_PCT", "RESET_ET", "EVENT_ET", "FX_RESET_ET", "JOB_RESET", "JOB_EVENTS",
           "MARKS_HOOK", "RecordingMarks", "PinnedMarks", "mark_view_of", "xccy_dates", "xccy_start_check", "next_event_due", "unsupported"]
