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

FX_CCS_PLAN rev 2 (release 1.0.6.1, package P-C) - these rules extend or replace the ones above where they say so
- XCCY FLOAT / FLOAT (D4, 3.3): ``pay_index`` and ``recv_index`` both given = the EUR/USD ESTR / SOFR variant (``variant`` FLOAT_FLOAT); one only
  = 400 NOT_IN_THIS_BUILD (fixed / floating); GBP (SONIA, the next currency) and JPY / CHF / CAD / AUD (no daily overnight rate) = 400
  NOT_IN_THIS_BUILD with the reason; an index must match its currency (400 TERMS); ACT/360 only; months a multiple of 3 (3..120); no MTM reset.
  Dates: T = the FX trade date, S = the EURUSD spot date (``ccycal``), e_k = MF(S + 3k months) on TARGET2 + New York, p_k = e_k + 2 business
  days, every SOFR date a US government securities day (400 SCHEDULE); maturity_date = p_n (the last payment and the final exchange).
- THE DEALER'S QUOTE: X0 = the executable EURUSD mid (PO-08); the full value for the spot date on the ``fxcurves`` curves (USD projection =
  discounting; EUR discounting adds the PB's basis level ``xccy_basis_eur_bp``, the EUR grid carries ``eur_ois_spread_bp``); PV(0) at spread 0 and
  its two parts; A* = sum (d_k / 360) x DF*_EUR(S, p_k) at 1e-10; s_par = PV(0) / (X0 N A*) (the EUR payer); dealt = s_par + ``xccy_basis_hw_bp``
  x ``otc_spread_mult`` rounded UP on the 0.1 bp grid for a payer (down for a receiver); a client spread better than that is 400 OFF_MARKET;
  mark0 = the full value at the dealt spread (the cost of dealing).  Coupons: N x (R x P - 1) + N x spread x d / 360 (spread excluded from the
  compounding); the paid ones are the index-exact ``to_c(N x (R + spread) x d / 360)``, R = (I(e) / I(s) - 1) x 360 / d at 1e-7.  Every quote,
  preview and contract stores the pricing record (``terms_json.pricing_record``) ``otcplan.reprice`` re-derives.
- BOOKING (D6): the IM, the contract row, then (``start_conversion``) the inception conversion in the SAME command through
  ``fx.book_linked_spot``: SELL the EUR notional / BUY USD at the desk's bid on X0, value S, linked both ways, its rows tagged xccy_start.
- START (D7): one command at 00:45 ET of S - the conversion's USD in, only the net shortfall funded, the exchange at X0, the conversion's EUR out;
  any failure rolls the step back (SKIPPED with an ERROR, retried; the tagged rows wait); not done by 06:00 ET: ERROR XCCY_EVENT_LATE.  PAY: both
  indices published (else INDEX_PENDING: quiet on its first day), one true-up entry per leg (``accr:{kind}:{cid}:trueup:{e_k}``), the
  settlement-funding deal settled around the payment (``fx.settle_linked`` xccy_event).  END: the last PAY, the reverse exchange with the
  funding deal's EUR in before and its USD out after, then ``swaps.close_contract``.
- MARK AND VM (D8, D19, every XCCY): values are for the spot date of the valuation date; ``mark_xccy`` returns the CLEAN mark (NAV's overlay =
  full value - the notionals and accrued coupons on the balance sheet at X_t) and ``inputs.csa_exposure_c`` = the full value, which the CTM
  variation margin follows (``swaps._vm_core``).  The fixed / fixed variant keeps its leg model; its exposure adds the same balance sheet.
- TERMINATION (D17, every XCCY): before S with a linked conversion 409 XCCY_NOT_STARTED; otherwise cash-settled in USD at the full value with the
  dealer's half-width against the Fund (the float variant: the basis level moved by the half-width; fixed / fixed: each leg at the quote that
  would replace it) - the notionals and accrued coupons cleared through 3900 at X_t, never a physical re-exchange, never EUR cash.
- HOOKS (K5): ``scheduled_flows(conn, row)`` (exchanges, coupons - known once indexed) and ``risk_sensitivities(services, row)`` (ONLY the
  off-ledger part: the clean mark's FX delta, the exchanges not yet on the ledger, rate01 per currency, basis01 per pair).
- STALE LISTING (INSTITUTIONAL_V3_PLAN 6.1 R-D; one helper, K-V5): ``last_trade_age_sessions`` counts the sessions closed since an exchange-listed
  instrument's last observed print (None for instruments without exchange prints); a new TRS on an underlying silent for more than
  ``STALE_LISTING_SESSIONS`` (5) sessions is refused at its preview and booking: 409 STALE_LISTING "no trade since <date>: the security may be
  delisted or suspended".
"""
from __future__ import annotations

import contextvars
import json
import sqlite3
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Context, Decimal, InvalidOperation, ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_EVEN, localcontext
from typing import Callable, Dict, List, Optional, Tuple

from .. import config, execution, prime
from .. import fx as fxmod
from .. import marks as _marks
from ..books import account_row, eligibility as _eligibility, split_account_id
from ..clock import epoch_of, ny_of, ny_str
from ..commands import ConflictError, RejectError, TxContext
from ..db import dumps, next_id
from ..instruments import EQUITY_KINDS, fx_pair_id
from ..ledger import Entry, Line, balance, cr, dr, natural
from ..lifecycle_jobs import accrue_segment as _core_accrue_segment, sofr_for_night
from ..quotes import MarkView
from ..tables import POLICY_INDEX, POLICY_INDEX_MONTHLY
from ..units import fmt_money, from_c, fx_c, qprice, to_c
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
FLOAT_VARIANT = "FLOAT_FLOAT"          # FX_CCS_PLAN D4 (1.0.6.1): the EUR/USD ESTR / SOFR float / float cross-currency swap

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


# ---- the stale-listing guard (INSTITUTIONAL_V3_PLAN 6.1 R-D, built in 1.0.6.1 as ONE helper; V-X of 1.0.8 reuses it: K-V5) -------------
#: a listed underlying whose last observed trade is older than this many sessions of its exchange is refused (409 STALE_LISTING)
STALE_LISTING_SESSIONS = 5
STALE_LOOKBACK_DAYS = 400                     # sessions are counted at most this far back (a print older than that is simply "older")


def last_trade(services, instrument_id: str, snapshot=None, conn: Optional[sqlite3.Connection] = None) -> Optional[Tuple[int, str]]:
    """``(epoch, source)`` of an exchange-listed instrument's last OBSERVED trade: the later of the observed quote's time and the newest recorded
    CLOSE row's quote time (``marks``); None when nothing was ever observed.  Only the time of a print counts - a quote's quality ("close") or a
    CLOSE row recorded today with an old print never makes an old trade look recent."""
    uid = str(instrument_id or "").strip().upper()
    best: Optional[Tuple[int, str]] = None
    snap = snapshot if snapshot is not None else _provider_snapshot(services, [uid])
    mv = None
    try:
        mv = snap.mark(uid) if snap is not None else None
    except Exception:   # noqa: BLE001 - a provider without this id: the recorded closes decide
        mv = None
    qt = getattr(mv, "quote_time", None) if mv is not None and getattr(mv, "mark", None) is not None else None
    if isinstance(qt, (int, float)) and not isinstance(qt, bool) and qt > 0:
        best = (int(qt), f"the observed quote ({getattr(mv, 'source', '') or 'quote'}, {getattr(mv, 'quality', '') or 'unknown'})")
    with _marks.shared_reader(services.db, conn) as c:
        r = c.execute("SELECT MAX(quote_time) AS qt FROM marks WHERE instrument_id=? AND kind='CLOSE' AND quote_time IS NOT NULL", (uid,)).fetchone()
    if r is not None and r["qt"] is not None and (best is None or int(r["qt"]) > best[0]):
        best = (int(r["qt"]), "a recorded close")
    return best


def last_trade_age_sessions(services, instrument_id: str, snapshot=None, conn: Optional[sqlite3.Connection] = None, now: Optional[int] = None) -> Optional[int]:
    """K-V5 (INSTITUTIONAL_V3_PLAN 6.1 R-D): how many sessions of the instrument's exchange have CLOSED since the session of its last observed
    trade - 0 for a print of today or of the last session.  None for an instrument without exchange prints (bonds, bills, OTC contracts,
    private marks, FX: only exchange-listed equities, ETFs, ADRs, REITs and preferreds are counted) and for one never observed (the price checks
    refuse that one with their own reason).  Pure: reads the quote snapshot, the recorded closes and the session table."""
    uid = str(instrument_id or "").strip().upper()
    with _marks.shared_reader(services.db, conn) as c:
        inst = services.instruments.get(uid, c)
        if inst is None or inst.kind not in EQUITY_KINDS:
            return None
        got = last_trade(services, uid, snapshot, c)
    if got is None:
        return None
    ts = int(now if now is not None else services.clock.epoch())
    last_day = ny_of(got[0]).date()
    today = ny_of(ts).date()
    sessions = getattr(services, "sessions", None)
    n = 0
    d = last_day + timedelta(days=1)
    stop = min(today, last_day + timedelta(days=STALE_LOOKBACK_DAYS))
    while d <= stop:
        try:
            close = sessions.session_close(inst.session_group, d) if sessions is not None else None
        except KeyError:
            close = None
        if close is not None and int(close) <= ts:
            n += 1
        d += timedelta(days=1)
    return n


def stale_listing_check(services, instrument_id: str, snapshot=None, conn: Optional[sqlite3.Connection] = None, now: Optional[int] = None,
                        what: str = "a TRS") -> Optional[dict]:
    """R-D: 409 STALE_LISTING "no trade since <date>: the security may be delisted or suspended" when the last observed trade of an exchange-listed
    underlying is older than ``STALE_LISTING_SESSIONS`` sessions; the facts otherwise (None for an instrument the rule does not cover)."""
    age = last_trade_age_sessions(services, instrument_id, snapshot, conn, now)
    if age is None:
        return None
    got = last_trade(services, instrument_id, snapshot, conn)
    last_day = ny_of(got[0]).date().isoformat() if got else None
    facts = {"underlying_id": str(instrument_id).upper(), "last_trade_date": last_day, "sessions_since": age, "max_sessions": STALE_LISTING_SESSIONS,
             "basis": got[1] if got else None}
    if age > STALE_LISTING_SESSIONS:
        raise ConflictError("STALE_LISTING", f"{str(instrument_id).upper()}: no trade since {last_day} ({age} sessions without a print, at most"
                            f" {STALE_LISTING_SESSIONS}): the security may be delisted or suspended - {what} on it is refused", facts)
    return facts


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
    that close is not recorded or observed yet.  V1061-04: that refusal first asks the quote worker for the underlying at ORDER priority
    (``_pin``) - out of session nothing else fetches an instrument nobody holds or watches, so its close was never "being recorded" and the
    stale-listing guard (R-D) never saw its last print; the next preview sees the fetched quote (and refuses a silent listing)."""
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
        fetching = _pin(services, [inst.instrument_id])             # V1061-04: the quote is asked for; the ticket's retry sees it
        raise ConflictError("CLOSE_PENDING", f"{inst.instrument_id}: the market is closed and the official close of {day.isoformat()} is not recorded yet"
                            f" ({info.get('note') or 'no print at the close'}); the reset price is that close, never the last print before it - "
                            + ("its quote is being fetched now" if fetching else "it is being recorded now"),
                            {"close_date": day.isoformat(), "retry_ms": RETRY_MS, "underlying_id": inst.instrument_id, "fetching": fetching})
    mv = MarkView(inst.instrument_id, px, "close", info.get("source") or "the official close", info.get("method") or "close", info.get("quote_time") or close_ts,
                  int(now), "close", dict(info, close_date=day.isoformat()), False, f"the official close of {day.isoformat()}")
    return px, mv


def _trs_deal(services, conn: sqlite3.Connection, marks, acct: dict, terms: TrsTerms, today: date, now: Optional[int] = None,
              firm: Optional[dict] = None) -> TrsDeal:
    """The TRS deal of ``today``: the reset price is the underlying's fresh print while its session is open, else its official close
    (V104R2-OTC-02); ``firm`` (C14) = the dealer's accepted quote, whose reset price is booked as quoted."""
    inst = _trs_underlying(services, conn, terms.underlying_id)
    ts = int(now if now is not None else services.clock.epoch())
    # INSTITUTIONAL_V3_PLAN 6.1 R-D: a delisted or suspended underlying (no print for more than 5 sessions) is refused at the preview and the
    # booking alike - never priced at an old close (AVB: last print 2026-08-14)
    stale_listing_check(services, inst.instrument_id, marks, conn, ts, what="a new TRS")
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

    def floating(v) -> bool:
        return v not in (None, "", False) and str(v).strip().upper() not in ("FIXED", "NONE", "FALSE")
    for k in ("pay_float", "recv_float", "float_index", "fixing", "pay_fixing", "recv_fixing"):
        if floating(t.get(k)):
            raise RejectError("NOT_IN_THIS_BUILD", f"{k}={t.get(k)!r} is not a term of this build: a floating leg is named by pay_index / recv_index (EUR/USD"
                              " ESTR / SOFR float / float); other floating legs and fixed / floating swaps are not offered", {"field": k})
    pay_ccy = str(t.get("pay_ccy") or "").strip().upper()
    recv_ccy = str(t.get("recv_ccy") or "").strip().upper()
    if len(pay_ccy) != 3 or len(recv_ccy) != 3:
        raise RejectError("TERMS", "pay_ccy and recv_ccy are required (three-letter ISO codes)")
    if pay_ccy == recv_ccy:
        raise RejectError("TERMS", "a cross-currency swap needs two different currencies")
    if "USD" not in (pay_ccy, recv_ccy):
        raise RejectError("CURRENCY_UNSUPPORTED", "one leg must be USD in this build (the mark-to-market reset re-strikes the USD notional)",
                          {"pay_ccy": pay_ccy, "recv_ccy": recv_ccy})
    fl_pay, fl_recv = floating(t.get("pay_index")), floating(t.get("recv_index"))
    if fl_pay and fl_recv:                       # FX_CCS_PLAN D4 (1.0.6.1): the float / float variant
        return parse_float_terms(t, pay_ccy, recv_ccy)
    if fl_pay or fl_recv:
        raise RejectError("NOT_IN_THIS_BUILD", "fixed / floating cross-currency swaps are not in this build: both legs float (EUR ESTR / USD SOFR) or both are"
                          " fixed", {"field": "pay_index" if fl_pay else "recv_index"})
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
    elif kind == "XCCY" and _is_float(parse_xccy_terms(terms)):
        out = _float_preview(services, conn, account_id, acct, parse_xccy_terms(terms), ts, marks)
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
        p = parse_xccy_terms(payload.get("terms") or {})
        return _book_xccy_float(ctx, services, acct, p) if _is_float(p) else _book_xccy(ctx, services, acct, p)
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
    if _is_float(terms):                         # FX_CCS_PLAN D8 / D19 (1.0.6.1): the full value for the spot date; clean + CSA exposure
        return _float_mark(services, row, terms, now_ts, valuation_date=valuation_date, snapshot=snapshot)
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
    # FX_CCS_PLAN D8 (1.0.6.1, every XCCY): the CSA exposure is the full (dirty) value = the clean mark + the notionals and accrued coupons on the
    # balance sheet at the same FX rate; the CTM variation margin follows it, NAV keeps the clean mark
    fx_rate = Decimal(str(inputs["fx"]["usd_per_unit"]))
    with _marks.shared_reader(services.db) as conn:
        bsheet, bdetail = _balance_sheet(conn, row, lambda c: ONE if c == "USD" else fx_rate)
    clean_c = to_c(value)
    inputs = dict(inputs, clean_mark_c=clean_c, balance_sheet_c=to_c(bsheet), balance_sheet=bdetail, csa_exposure_c=clean_c + to_c(bsheet))
    return clean_c, dict(base, **inputs)


def dv01_c(services, contract) -> int:
    """The signed PV change for +1 bp on the USD par curve: 0 for a TRS (its financing resets nightly), the USD leg's
    ``sign × N × 1 bp × A`` for an XCCY (raises ConflictError NO_CURVE; 0 when not OPEN / nothing remains)."""
    row = _contract_of(services, contract)
    if row is None or row.get("status") != "OPEN" or row["kind"] != "XCCY":
        return 0
    terms = _terms(row)
    if _is_float(terms):                         # the USD rate01 of the float swap (re-priced), 0 when it cannot be valued now
        got = _float_risk(services, row, terms)
        return int((got.get("rate01_c") or {}).get("USD") or 0)
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
    if _is_float(terms):                         # 4.2: COMPOUND_ARREARS on SOFR / ESTR (+ the dealt spread on the foreign leg)
        return _float_leg_spec(conn, row, terms, kind, night)
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
    if _is_float(_terms(row)):                   # D7: the start exchange and its linked conversion, payment versus payment, in one step
        return _float_start(ctx, services, row, d, key, job)
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
    if _is_float(_terms(row)):                   # 4.2: the index-exact coupons with their true-ups
        return _float_pay(ctx, services, row, d, job, key)
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
    if _is_float(_terms(row)):                   # 4.2 END: the last coupons, the reverse exchange with the funding deal settled around it
        return _float_end(ctx, services, row, d, key, job)
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
    if ev == "FX_RESET":
        return _ev_fx_reset(ctx, services, row, d, key)
    if ev == "START":
        res = _ev_start(ctx, services, row, d, key, JOB_EVENTS)
    elif ev == "PAY":
        res = _xccy_pay(ctx, services, row, d, JOB_EVENTS, key)
    else:
        res = _ev_end(ctx, services, row, d, key, JOB_EVENTS)
    return _event_late(ctx, row, d, ev, key, res) if _is_float(_terms(row)) else res


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
    elif _is_float(terms):
        events = _float_next_events(services, conn, row, terms, today)
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
    if _is_float(terms):
        return _float_detail(services, conn, row, terms)
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
    return [{"item": "Floating cross-currency legs other than EUR ESTR / USD SOFR (GBP SONIA, JPY TONA, CHF SARON, CAD CORRA, AUD AONIA) and fixed / floating"
                     " cross-currency swaps", "state": "NOT_OFFERED",
             "wording": ("this version offers the EUR/USD float / float swap (EUR on compounded ESTR plus the dealer's spread against USD on compounded SOFR, quarterly,"
                         " ACT/360, a disclosed and editable basis level) besides the fixed / fixed swap; a GBP SONIA leg is the next currency (its index is"
                         " stored), JPY / CHF / CAD / AUD have no daily overnight-rate source here, and fixed / floating and resetting (MTM) float / float"
                         " swaps are not built"),
             "where": "prime-broker OTC tab, Swaps (cross-currency swap)"}]


# ---- XCCY float / float: the EUR/USD ESTR / SOFR swap (FX_CCS_PLAN rev 2 D4-D8, D11, D17, 3.3, 4.2; release 1.0.6.1) ------------------------
#: the overnight index of each currency's floating leg in this build, and what the others answer (D4 "What stays out")
FLOAT_INDEX = {"USD": "SOFR", "EUR": "ESTR"}
FLOAT_INDEX_WORDS = {"SOFR": "SOFR (NY Fed): compounded in arrears = the ratio of the NY Fed's SOFR Index",
                     "ESTR": "ESTR (ECB): compounded in arrears = the ratio of the ECB's compounded ESTR index"}
FLOAT_DEFERRED = {"GBP": "not in this build yet: the SONIA index (Bank of England IUDZOS2) is stored; the GBP leg is the next currency (1.0.6.2)"}
FLOAT_STEP_MONTHS = 3
FLOAT_PAY_DELAY_BD = 2
FLOAT_MAX_MONTHS = 120
SPREAD_STEP_BP = Decimal("0.1")              # the dealt spread's grid (0.1 bp), rounded against the client
FLOAT_LATE_ET = "06:00"                      # D7: an event not done by 06:00 ET of its date raises ERROR XCCY_EVENT_LATE
MODEL_VERSION = "fxcurves-2"
P34 = Context(prec=34, rounding=ROUND_HALF_EVEN)
FLOAT_METHOD = ("RFR float / float cross-currency swap: ESTR and SOFR compounded in arrears (published indices), 2 BD payment delay, quarterly,"
                " ACT/360, spread on the EUR leg; dealer quote = par spread + half-width; MODEL on PROXY curves; basis level ASSUMED")
FLOAT_MODEL = ("full value for the spot date = sum of the USD flows x DF_USD + EURUSD x sum of the EUR flows x DF*_EUR (DF* = the EUR curve plus the"
               " EUR/USD basis level, discounting only); a coupon = N x (R x P - 1) + N x spread x days / 360, R the factor realised so far, P from"
               " the projection curve; clean mark (NAV) = full value - the notionals and accrued coupons on the balance sheet; CSA exposure (VM) ="
               " the full value")
FLOAT_COMPOUNDING = {"SOFR": "compounded in arrears (the NY Fed's SOFR Index), no lookback, 2 business-day payment delay",
                     "ESTR": "compounded in arrears (the ECB's compounded ESTR index), no lookback, 2 business-day payment delay; the spread is added, not"
                             " compounded"}
FLOAT_CALENDAR_WORDS = {"schedule": "TARGET2 + New York", "sofr": "SOFR period dates are US government securities business days (SIFMA)",
                        "roll": "modified following"}
SETTLEMENT_FUNDING_MODES = ("BUY_WITH_USD", "NONE")
INDEX_PENDING = "INDEX_PENDING"


def _is_float(terms: Optional[dict]) -> bool:
    return str((terms or {}).get("variant") or "").upper() == FLOAT_VARIANT


def _fxcurves():
    """FX_CCS_PLAN K3: the FX curve module (P-A), imported lazily; None while it is not in the tree (every price then answers 409
    CURVE_UNAVAILABLE with that reason - nothing is priced without its curves)."""
    try:
        from .. import fxcurves as m             # noqa: WPS433 - optional module, read lazily
        return m
    except Exception:   # noqa: BLE001 - a missing or broken module is "unavailable", never an import error here
        return None


def _ccycal():
    try:
        from .. import ccycal as m               # noqa: WPS433 - K1 (P-A)
        return m
    except Exception:   # noqa: BLE001
        return None


def _spread_arg(v, name: str) -> Optional[Decimal]:
    """A client spread in bp on the 0.1 bp grid; None (blank) = the dealer's quote."""
    if v is None or (isinstance(v, str) and v.strip().lower() in ("", "quote", "par")):
        return None
    if isinstance(v, (bool, float)):
        raise RejectError("TERMS", f"{name} must be a decimal string in basis points (0.1 = 0.1 bp), not a {type(v).__name__}", {"field": name})
    try:
        d = Decimal(str(v).strip())
    except (InvalidOperation, ValueError) as e:
        raise RejectError("TERMS", f"{name} is not a number: {v!r}", {"field": name}) from e
    if not d.is_finite() or abs(d) > 500:
        raise RejectError("TERMS", f"{name} must be between -500 and 500 bp, got {v!r}", {"field": name})
    if d != d.quantize(SPREAD_STEP_BP):
        raise RejectError("TERMS", f"{name} {v!r} is finer than the dealing grid 0.1 bp", {"field": name})
    return d.quantize(SPREAD_STEP_BP)


def _float_index_of(ccy: str, given, side: str) -> str:
    """The index a floating leg in ``ccy`` names: SOFR for USD, ESTR for EUR (400 TERMS for a mismatch; 400 NOT_IN_THIS_BUILD for a currency
    whose floating leg is not offered, with the reason)."""
    idx = str(given or "").strip().upper()
    idx = {"\u20acSTR": "ESTR", "EUR_ESTR": "ESTR", "EURSTR": "ESTR", "SOFR_COMPOUNDED": "SOFR"}.get(idx, idx)
    if ccy in FLOAT_DEFERRED:
        raise RejectError("NOT_IN_THIS_BUILD", f"a {ccy} floating leg is {FLOAT_DEFERRED[ccy]}", {"field": f"{side}_index", "currency": ccy})
    if ccy not in FLOAT_INDEX:
        raise RejectError("NOT_IN_THIS_BUILD", f"a {ccy} floating leg is not offered: no daily overnight rate is sourced for {ccy} in this terminal",
                          {"field": f"{side}_index", "currency": ccy})
    want = FLOAT_INDEX[ccy]
    if idx != want:
        raise RejectError("TERMS", f"the {ccy} leg floats on {want}, not {idx or '(none)'}: each index must match its currency (USD with SOFR, EUR with ESTR)",
                          {"field": f"{side}_index", "currency": ccy, "index": want})
    return want


def _settlement_funding_arg(v, ccys: Tuple[str, ...]) -> Optional[dict]:
    """D9: ``settlement_funding {ccy: BUY_WITH_USD | NONE}`` for the contract's non-USD currencies (None when absent; 400 TERMS otherwise)."""
    if v in (None, "", {}):
        return None
    if not isinstance(v, dict):
        raise RejectError("TERMS", "settlement_funding must be an object such as {\"EUR\": \"BUY_WITH_USD\"}", {"field": "settlement_funding"})
    out = {}
    for k, mode in v.items():
        ccy, m = str(k or "").strip().upper(), str(mode or "").strip().upper()
        if ccy == "USD" or ccy not in ccys:
            raise RejectError("TERMS", f"settlement_funding names {ccy or '(none)'}: only the swap's non-USD currency ({', '.join(c for c in ccys if c != 'USD')})"
                              " can be bought with USD", {"field": "settlement_funding"})
        if m not in SETTLEMENT_FUNDING_MODES:
            raise RejectError("TERMS", f"settlement_funding {ccy} must be BUY_WITH_USD or NONE, got {mode!r}", {"field": "settlement_funding"})
        out[ccy] = m
    return out


def parse_float_terms(t: dict, pay_ccy: str, recv_ccy: str) -> dict:
    """D4 / 3.3: the float / float term dictionary (both ``pay_index`` and ``recv_index`` given) - 400 TERMS / NOT_IN_THIS_BUILD with the reason."""
    foreign = recv_ccy if pay_ccy == "USD" else pay_ccy
    pay_index = _float_index_of(pay_ccy, t.get("pay_index"), "pay")
    recv_index = _float_index_of(recv_ccy, t.get("recv_index"), "recv")
    for side in ("pay", "recv"):
        b = str(t.get(f"{side}_basis") or "ACT/360").strip().upper()
        if b != "ACT/360":
            raise RejectError("TERMS", f"{side}_basis must be ACT/360 for an overnight-index leg (SOFR and ESTR are ACT/360), got {b}", {"field": f"{side}_basis"})
        if t.get(f"{side}_rate") not in (None, ""):
            raise RejectError("TERMS", f"{side}_rate is a fixed rate: a floating leg pays its index (leave it blank)", {"field": f"{side}_rate"})
    if _flag(t.get("mtm_reset", False), "mtm_reset"):
        raise RejectError("NOT_IN_THIS_BUILD", "a mark-to-market (resetting) float / float cross-currency swap is not in this build: the notionals are"
                          " exchanged at the deal rate at the start and the end (mtm_reset false)", {"field": "mtm_reset"})
    raw_months = t.get("months", t.get("tenor_months"))
    if raw_months in (None, ""):
        raise RejectError("TERMS", "months (the tenor, a multiple of 3) is required")
    months = _whole(raw_months, "months", FLOAT_STEP_MONTHS, FLOAT_MAX_MONTHS)
    if months % FLOAT_STEP_MONTHS:
        raise RejectError("TERMS", f"months {months} must be a multiple of {FLOAT_STEP_MONTHS} (quarterly periods on both legs)", {"field": "months"})
    eur_side = "pay" if pay_ccy == foreign else "recv"
    usd_side = "recv" if eur_side == "pay" else "pay"
    if t.get(f"{usd_side}_spread_bp") not in (None, "") and _spread_arg(t.get(f"{usd_side}_spread_bp"), f"{usd_side}_spread_bp") != 0:
        raise RejectError("TERMS", f"the spread is quoted on the {foreign} leg ({eur_side}_spread_bp); the USD leg pays SOFR flat", {"field": f"{usd_side}_spread_bp"})
    client_bp = _spread_arg(t.get(f"{eur_side}_spread_bp"), f"{eur_side}_spread_bp")
    pay_n = _money_arg(t.get("pay_notional_c"), t.get("pay_notional"), "pay_notional")
    recv_n = _money_arg(t.get("recv_notional_c"), t.get("recv_notional"), "recv_notional")
    if pay_n is None and recv_n is None:
        raise RejectError("TERMS", "give the pay or the receive notional (the other is struck at the deal FX rate)")
    conv = _flag(t.get("start_conversion", False), "start_conversion")
    if conv and eur_side != "pay":
        raise RejectError("TERMS", f"the inception conversion sells the {foreign} received at the start exchange; this swap pays {foreign} at the start"
                          f" (it lends {foreign}): buy it with Convert or the settlement-funding instruction instead", {"field": "start_conversion"})
    return {"variant": FLOAT_VARIANT, "pay_ccy": pay_ccy, "recv_ccy": recv_ccy, "foreign": foreign, "usd_side": "receive" if recv_ccy == "USD" else "pay",
            "pay_index": pay_index, "recv_index": recv_index, "pay_notional_c": pay_n, "recv_notional_c": recv_n, "pay_rate": None, "recv_rate": None,
            "pay_basis": "ACT/360", "recv_basis": "ACT/360", "months": months, "mtm_reset": False, "eur_side": "pay" if eur_side == "pay" else "receive",
            "client_spread_bp": client_bp, "start_conversion": conv,
            "settlement_funding": _settlement_funding_arg(t.get("settlement_funding"), (pay_ccy, recv_ccy))}


class _ZC:
    """One fxcurves ``Curve`` as the swap's full value reads it (FX_CCS_PLAN D2, K3): ``df(a, b)`` = fxcurves.df, unquantised (Decimal precision
    34) - the same arithmetic as the PB forward and the independent re-pricer (z linear in percent between nodes, the level / shift added after
    the interpolation, t = ACT/365 days from the curve's d0)."""

    def __init__(self, crv):
        self.crv = crv
        self.ccy = str(getattr(crv, "ccy", "") or "")

    def df(self, a: date, b: date) -> Decimal:
        return _fxcurves().df(self.crv, a, b)


@dataclass
class FloatState:
    """Where a booked float / float swap stands (empty for a quote): the start exchange done, the periods paid, each leg's exact coupon of a
    finished period (``known``, cents, once both indices are published - else the posted estimate) and the factor realised so far of the
    period accruing (``realised``: (R, the day after the last accrued night))."""
    started: bool = False
    ended: bool = False
    paid: Tuple[int, ...] = ()
    known: Dict[Tuple[int, str], int] = field(default_factory=dict)
    realised: Dict[Tuple[int, str], Tuple[Decimal, date]] = field(default_factory=dict)


@dataclass
class FloatLegs:
    """The fixed parts of the valuation: the legs' currencies and notionals, the periods and the spread's currency."""
    leg_ccy: Dict[str, str]
    leg_n_c: Dict[str, int]
    periods: List[Period]
    spread_ccy: str = "EUR"

    @property
    def start(self) -> date:
        return self.periods[0][0]


def float_full_value(legs: FloatLegs, spread: Decimal, X: Decimal, t_spot: date, proj: Dict[str, "_ZC"], disc: Dict[str, "_ZC"],
                     state: Optional[FloatState] = None) -> Tuple[Decimal, Decimal, Decimal]:
    """3.3 the full value in USD for the spot date ``t_spot`` (unrounded) and its USD and EUR flow sums: ``USD flows x DF_USD(t_spot, d) + X x EUR
    flows x DF*_EUR(t_spot, d)``.  The Fund's flows: the lent (receive-leg) notional out and the borrowed (pay-leg) notional in at the start
    (until exchanged), each leg's coupon N x (R x P - 1) + N x spread x d / 360 at its payment date (receive +, pay -; paid periods left
    out), the notionals back at the last payment date.  P = 1 / DF_proj(a, e), a = the period start or the day after the last accrued night."""
    st = state or FloatState()
    tot = {"USD": Decimal(0), "EUR": Decimal(0)}
    with localcontext(P34):
        for side, sign in (("receive", ONE), ("pay", -ONE)):
            ccy = legs.leg_ccy[side]
            n = from_c(int(legs.leg_n_c[side]))
            if not st.started:
                tot[ccy] += -sign * n * disc[ccy].df(t_spot, legs.start)
            sp = spread if ccy == legs.spread_ccy else Decimal(0)
            for k, (s, e, p) in enumerate(legs.periods):
                if k in st.paid:
                    continue
                known = st.known.get((k, side))
                if known is not None:
                    cpn = from_c(int(known))
                else:
                    r_fac, a = st.realised.get((k, side), (ONE, s))
                    pfac = ONE if a >= e else ONE / proj[ccy].df(a, e)
                    cpn = n * (r_fac * pfac - ONE) + n * sp * Decimal((e - s).days) / Decimal(360)
                tot[ccy] += sign * cpn * disc[ccy].df(t_spot, p)
            if not st.ended:
                tot[ccy] += sign * n * disc[ccy].df(t_spot, legs.periods[-1][2])
        return tot["USD"] + X * tot["EUR"], tot["USD"], tot["EUR"]


def _sched_term(sched, name: str, what: str) -> Decimal:
    """A dealer term of the PB schedule (FX_CCS_PLAN D5): 409 SCHEDULE_TERM_UNAVAILABLE when this build's schedule does not carry it - a level is
    never assumed by the code."""
    v = getattr(sched, name, None)
    if v in (None, ""):
        raise ConflictError("SCHEDULE_TERM_UNAVAILABLE", f"the prime broker's schedule has no {what} ({name}) in this build: the float cross-currency swap is"
                            " not priced without it", {"field": name})
    return Decimal(str(v))


@dataclass
class FloatMarket:
    """The curves of one valuation date (D2): USD (projection = discounting) and EUR (projection, carrying the PB's EUR OIS spread on its grid
    nodes; discounting adds the EUR/USD basis level) - fxcurves ``Curve`` objects."""
    on: date
    usd: object
    eur: object
    level_bp: Decimal
    eur_ois_spread_bp: Decimal
    level_source: Optional[str] = None
    level_updated_ts: Optional[int] = None

    def eur_disc(self, level_add=0):
        fxc = _fxcurves()
        return fxc.discount(self.eur, self.level_bp + Decimal(str(level_add)), f"{self.eur.ccy}USD", self.level_source, self.level_updated_ts)

    def zc(self, bu=0, be=0, mid=0, grid=0) -> Tuple[Dict[str, "_ZC"], Dict[str, "_ZC"]]:
        """(projection, discounting) curves with the bumps of a sensitivity, each re-priced in full (D14): ``bu`` / ``be`` bp on every USD /
        EUR node (fxcurves.shifted), ``mid`` bp on the EUR discount curve only (the basis level), ``grid`` bp on the EUR grid nodes only (the
        EUR OIS spread, fxcurves.with_spread)."""
        fxc = _fxcurves()
        usd = fxc.shifted(self.usd, bu) if bu else self.usd
        eur = fxc.with_spread(self.eur, Decimal(str(self.eur.spread_bp)) + Decimal(str(grid))) if grid else self.eur
        if be:
            eur = fxc.shifted(eur, be)
        eur_d = fxc.discount(eur, self.level_bp + Decimal(str(mid)), f"{self.eur.ccy}USD", self.level_source, self.level_updated_ts)
        u = _ZC(usd)
        return {"USD": u, self.eur.ccy: _ZC(eur)}, {"USD": u, self.eur.ccy: _ZC(eur_d)}


def _curve_of(services, ccy: str, on: date, acct: Optional[dict]):
    """fxcurves.curve for one currency on ``on`` with the PB's EUR OIS spread (K3); 409 CURVE_UNAVAILABLE when the module is not built."""
    fxc = _fxcurves()
    if fxc is None or not hasattr(fxc, "curve"):
        raise ConflictError("CURVE_UNAVAILABLE", f"no {ccy} term curve: the FX curve module is not in this build, and a float leg is never priced on a flat"
                            " rate", {"currency": ccy})
    return fxc.curve(services, ccy, on, pb=(acct or {}).get("account_id"))


def float_market(services, conn, acct: dict, on: date, sched=None) -> FloatMarket:
    """The USD and EUR curves of ``on`` (D2) and the PB's EUR/USD basis level and EUR OIS spread (D5)."""
    sched = sched if sched is not None else prime.read_schedule(conn, acct)
    level = _sched_term(sched, "xccy_basis_eur_bp", "EUR/USD cross-currency basis level")
    ois = getattr(sched, "eur_ois_spread_bp", None)
    usd = _curve_of(services, "USD", on, acct)
    eur = _curve_of(services, "EUR", on, acct)
    dealer = config.PRIME_BROKERS.get(prime.broker_of(acct), prime.broker_of(acct))
    return FloatMarket(on, usd, eur, level, Decimal(str(ois)) if ois not in (None, "") else Decimal(0),
                       f"{dealer} schedule (seed / as edited), ASSUMED", getattr(sched, "updated_ts", None))


def _curve_record(curve, until: date) -> dict:
    """The curve's record for a pricing record (K3 ``record(until)``), else what it exposes."""
    fn = getattr(curve, "record", None)
    if callable(fn):
        try:
            rec = fn(until)
            if isinstance(rec, dict):
                return rec
        except Exception:   # noqa: BLE001 - the record below
            pass
    first = getattr(curve, "first_node", None)
    return {"ccy": getattr(curve, "ccy", None), "kind": getattr(curve, "kind", None), "source": getattr(curve, "source", None),
            "as_of": str(getattr(curve, "d0", "")), "first_node": first, "nodes": [[str(x) for x in n] for n in getattr(curve, "nodes", [])],
            "spread_bp": None if getattr(curve, "spread_bp", None) is None else str(curve.spread_bp), "labels": list(getattr(curve, "labels", []) or []),
            "compounding": "continuously compounded"}


def _curve_label(curve) -> List[str]:
    return [str(x) for x in (getattr(curve, "labels", None) or [])]


def _first_print(curve) -> dict:
    fn = getattr(curve, "first_node", None) or {}
    if not isinstance(fn, dict):
        fn = {k: getattr(fn, k, None) for k in ("series", "obs_date", "value_pct", "z")}
    return {"series": fn.get("series"), "obs_date": None if fn.get("obs_date") is None else str(fn.get("obs_date")),
            "value_pct": None if fn.get("value_pct") is None else str(fn.get("value_pct")), "source": fn.get("source") or getattr(curve, "source", None)}


@dataclass
class FloatDeal:
    acct: dict
    p: dict
    broker: str
    dealer: str
    trade_date: date
    start: date
    periods: List[Period]
    fx_view: MarkView
    X0: Decimal
    n_usd_c: int
    n_eur_c: int
    legs: FloatLegs
    market: FloatMarket
    hw_bp: Decimal
    mult: Decimal
    pv0: Decimal
    final_timing: Decimal
    astar: Decimal
    par_bp: Decimal
    dealt_bp: Decimal
    rounding_bp: Decimal
    client_bp: Optional[Decimal]
    mark0_c: int
    sens: dict
    im_c: int
    im_pct: Decimal
    im_source: str
    margin_mode: str
    schedule_updated_ts: Optional[int]
    conversion: Optional[dict]
    net_start: Dict[str, int]
    rows: List[dict]
    full_at_dealt: Decimal

    @property
    def termination(self) -> date:
        return self.periods[-1][1]

    @property
    def maturity(self) -> date:
        return self.periods[-1][2]

    @property
    def eur_side(self) -> str:
        return self.p["eur_side"]

    @property
    def hw_eff_bp(self) -> Decimal:
        return (self.hw_bp * self.mult).quantize(Decimal("0.0001"))

    @property
    def spread(self) -> Decimal:
        return self.dealt_bp * BP


def float_dates(services, foreign: str, trade_date: date, months: int) -> Tuple[date, List[Period]]:
    """D10 / 3.3: the start (the pair's spot date on TARGET2 + New York) and the periods (e_k = MF(S + 3k months) from the start, p_k = e_k + 2
    business days, every SOFR date a US government securities day - else 400 SCHEDULE)."""
    cc = _ccycal()
    if cc is None:
        raise ConflictError("CALENDAR_UNAVAILABLE", "the currency calendars are not in this build: the float swap's dates cannot be set", {})
    pair = fx_pair_id(foreign)
    start = cc.spot_date(services, pair, trade_date)
    cal = cc.joint(cc.TARGET, cc.USNY)
    periods = [(_date(s), _date(e), _date(p)) for s, e, p in cc.schedule(cal, start, int(months), FLOAT_STEP_MONTHS, pay_delay_bd=FLOAT_PAY_DELAY_BD, sofr=True)]
    return start, periods


#: the book's NAV / headline gross / gross notional the limits block reads, kept LIMITS_TTL_S per runtime: a ticket previews on every change and
#: the risk report values the whole book (display only; the block says when the figures were taken)
LIMITS_TTL_S = 60
LIMITS_CACHE_ATTR = "_xccy_limits_cache"     # kept on the runtime itself: one cache per served process / test runtime


def _risk_totals(services, scope: str) -> dict:
    rk = getattr(services, "risk", None)
    fn = getattr(rk, "report", None) if rk is not None else None
    if not callable(fn):
        return {"unavailable": "the risk module is not bound: the ratios before and after are not computed"}
    now = int(services.clock.epoch())
    per = getattr(services, LIMITS_CACHE_ATTR, None)
    if not isinstance(per, dict):
        per = {}
        try:
            setattr(services, LIMITS_CACHE_ATTR, per)
        except (AttributeError, TypeError):      # a services object without attributes of its own: no cache
            pass
    hit = per.get(scope)
    if hit is not None and 0 <= now - int(hit["fetched_ts"]) < LIMITS_TTL_S:
        return hit
    try:
        view = fn(services, scope, _provider_snapshot(services))
    except Exception as e:   # noqa: BLE001 - display only
        return {"unavailable": f"the risk report failed ({type(e).__name__}: {e})"}
    hit = {"fetched_ts": now, "nav_c": int(getattr(view, "nav_c", 0) or 0), "gross_c": getattr(view, "gross_c", None),
           "notional_c": getattr(view, "gross_notional_c", None), "as_of": getattr(view, "as_of", None)}
    per[scope] = hit
    return hit


def _limits_block(services, conn, account_id: str, add_notional_c: int, mark0_c: int, snapshot=None) -> dict:
    """D18: no notional limit exists - the words, and the headline gross exposure / NAV and the gross notional / NAV before and after the trade
    (the swap sits outside the headline: its gross does not move; its USD notional adds to the gross notional).  Unavailable with the reason when
    the risk module cannot report now."""
    out = {"words": "notional limits: none configured in the prime broker's schedule; compliance has no OTC limit", "caps": "none configured",
           "warning_limit": "2x NAV (the Risk page's warning)", "add_notional_c": int(add_notional_c)}
    tot = _risk_totals(services, f"{split_account_id(account_id)[0]}:ALL")
    if tot.get("unavailable"):
        out["unavailable"] = tot["unavailable"]
        return out
    nav_c, gross_c, notional_c = int(tot["nav_c"]), tot.get("gross_c"), tot.get("notional_c")
    out["figures_as_of"] = tot.get("as_of") or tot.get("fetched_ts")

    def ratio(a, b):
        if a is None or not b:
            return None
        return str((Decimal(int(a)) / Decimal(int(b))).quantize(Decimal("0.0001")))
    nav_after = nav_c + int(mark0_c)
    out.update(nav_before_c=nav_c, nav_after_c=nav_after, headline_gross_c=None if gross_c is None else int(gross_c),
               headline_gross_nav_before=ratio(gross_c, nav_c), headline_gross_nav_after=ratio(gross_c, nav_after),
               gross_notional_before_c=None if notional_c is None else int(notional_c),
               gross_notional_after_c=None if notional_c is None else int(notional_c) + int(add_notional_c),
               gross_notional_nav_before=ratio(notional_c, nav_c),
               gross_notional_nav_after=ratio(None if notional_c is None else int(notional_c) + int(add_notional_c), nav_after))
    return out


def _quoted_float_flows(deal: "FloatDeal") -> List[dict]:
    """D9 (P-D-R2): the quoted swap's own flows for ``ccyfunding.plan(extra=...)`` - it is not a contract yet (a quote, or the booking, which
    builds its record before the contract row, its IM and the conversion exist), so the plan shows its EUR obligations, their purchases and the
    settlement-funding gap before it is booked.  As a booked swap's flows read: V1061-03 its IM (a USD outflow on the trade date: posted at the
    booking, one Treasury sweep, held until the swap ends), the net start flows on S (with the inception conversion the foreign currency nets
    to 0 and USD pays the conversion's spread), each coupon on its payment date (projected: ``known`` false, the index decides) and the final
    exchange on the last payment date; ``mode`` = the quote's instruction for that currency (BUY_WITH_USD, else NONE)."""
    f = deal.p["foreign"]
    sf = deal.p.get("settlement_funding") or {}

    def mode(c):
        return "BUY_WITH_USD" if sf.get(c) == "BUY_WITH_USD" else "NONE"

    def iso(x):
        return x.isoformat() if hasattr(x, "isoformat") else str(x)
    borrow = 1 if deal.eur_side == "pay" else -1            # +1: the foreign currency is borrowed (its coupons and its repayment go out)
    out: List[dict] = []
    if int(deal.im_c or 0) > 0:
        out.append({"date": deal.trade_date.isoformat(), "ccy": "USD", "amount_c": -int(deal.im_c), "known": True, "event": "IM", "kind": "XCCY",
                    "mode": "NONE",
                    "what": f"initial margin USD {fmt_money(int(deal.im_c))} posted at the booking (held by {deal.dealer} until the swap ends)"})
    for ccy, amt in sorted((deal.net_start or {}).items()):
        if int(amt):
            out.append({"date": deal.start.isoformat(), "ccy": ccy, "amount_c": int(amt), "what": "start exchange (net of the inception conversion)"
                        if deal.conversion else "start exchange", "known": True, "event": "START", "kind": "XCCY", "mode": mode(ccy)})
    for k, r in enumerate(deal.rows, start=1):
        pay = iso(r.get("pay_date") or r.get("p"))
        fc, uc = r.get(f"{f.lower()}_indicative_c"), r.get("usd_indicative_c")
        if fc is not None:
            out.append({"date": pay, "ccy": f, "amount_c": -borrow * int(fc), "what": f"coupon {k} (indicative; the {FLOAT_INDEX[f]} index decides)",
                        "known": False, "event": "PAY", "kind": "XCCY", "mode": mode(f)})
        if uc is not None:
            out.append({"date": pay, "ccy": "USD", "amount_c": borrow * int(uc), "what": f"coupon {k} (indicative; the SOFR index decides)",
                        "known": False, "event": "PAY", "kind": "XCCY", "mode": mode("USD")})
    last = deal.maturity.isoformat()
    out.append({"date": last, "ccy": f, "amount_c": -borrow * int(deal.n_eur_c), "what": "final exchange", "known": True, "event": "END", "kind": "XCCY", "mode": mode(f)})
    out.append({"date": last, "ccy": "USD", "amount_c": borrow * int(deal.n_usd_c), "what": "final exchange", "known": True, "event": "END", "kind": "XCCY",
                "mode": mode("USD")})
    return out


def _funding_line(services, conn, account_id: str, deal: "FloatDeal") -> Optional[dict]:
    """D9: the settlement-funding instruction and the plan's gap (``ccyfunding.plan``, P-D, read lazily); None while it is not built.  The plan
    is given the quoted swap's own flows (``_quoted_float_flows``), so the first quote already shows its EUR purchases and gap (P-D-R2)."""
    sf = deal.p.get("settlement_funding") or {}
    words = []
    for ccy, mode in sorted(sf.items()):
        words.append(f"every {ccy} obligation (coupons, the final exchange) is bought with USD two business days before it is due, as an audited FX"
                     f" desk spot deal" if mode == "BUY_WITH_USD" else f"{ccy} obligations are not funded automatically (instruction off)")
    out = {"instruction": sf or None, "words": "; ".join(words) or "no settlement-funding instruction: an obligation in a currency the account does not"
                                                                       " hold overdraws it (FUNDING_SHORTFALL)"}
    try:
        from .. import ccyfunding as cf          # noqa: WPS433 - K8 (P-D), optional
        fn = getattr(cf, "plan", None)
        if callable(fn):
            got = fn(services, account_id, extra=_quoted_float_flows(deal))
            if isinstance(got, dict):
                out["plan"] = {k: got.get(k) for k in ("gap", "gaps", "gap_c", "words", "note") if k in got} or None
    except Exception:   # noqa: BLE001 - the plan is display only
        pass
    return out


def _float_deal(services, conn: sqlite3.Connection, marks, acct: dict, p: dict, now_ts: int) -> FloatDeal:
    """The dealer's quote of a float / float swap at ``now_ts`` (3.3): the deal rate X0 (the executable EURUSD mid), the notionals, the dates, the
    curves of the trade date, PV(0) and its two parts, A*, the par spread, the dealt spread (par + the half-width, on the 0.1 bp grid against the
    client), mark0 (the full value at the dealt spread: the cost of dealing), the sensitivities, IM and the inception conversion."""
    foreign = p["foreign"]
    today = ny_of(int(now_ts)).date()
    fxv, X0 = _deal_fx(services, marks, foreign, today)
    eur_side = p["eur_side"]
    usd_side = "receive" if eur_side == "pay" else "pay"
    given_f = p["pay_notional_c"] if eur_side == "pay" else p["recv_notional_c"]
    given_u = p["recv_notional_c"] if eur_side == "pay" else p["pay_notional_c"]
    if given_f is not None:
        n_eur = int(given_f)
        n_usd = to_c(from_c(n_eur) * X0)
        if given_u is not None and int(given_u) != n_usd:
            raise RejectError("TERMS", f"the USD notional is struck at the deal FX rate {X0}: {foreign} {fmt_money(n_eur)} x {X0} = USD {fmt_money(n_usd)},"
                              f" not {fmt_money(int(given_u))}; leave one notional blank", {"deal_rate": str(X0), "usd_notional_c": n_usd})
    else:
        n_usd = int(given_u)
        n_eur = to_c(from_c(n_usd) / X0)
    trade_date = fxmod.fx_trade_date(services, int(now_ts))
    start, periods = float_dates(services, foreign, trade_date, p["months"])
    sched = prime.read_schedule(conn, acct)
    mult = qprice(Decimal(sched.otc_spread_mult))
    hw_bp = _sched_term(sched, "xccy_basis_hw_bp", "cross-currency swap dealing half-width")
    market = float_market(services, conn, acct, trade_date, sched)
    legs = FloatLegs({eur_side: foreign, usd_side: "USD"}, {eur_side: n_eur, usd_side: n_usd}, periods, foreign)
    proj, disc = market.zc()
    t_spot = start
    with localcontext(P34):
        pv0, _u, _e = float_full_value(legs, Decimal(0), X0, t_spot, proj, disc)
        sign_eur = -ONE if eur_side == "pay" else ONE
        e_n, p_n = periods[-1][1], periods[-1][2]
        final_timing = Decimal(0)
        for side, sign in (("receive", ONE), ("pay", -ONE)):
            ccy = legs.leg_ccy[side]
            fxr = ONE if ccy == "USD" else X0
            final_timing += sign * from_c(legs.leg_n_c[side]) * fxr * (disc[ccy].df(t_spot, p_n) - disc[ccy].df(t_spot, e_n))
        astar = sum(((Decimal((e - s).days) / Decimal(360)) * disc[foreign].df(start, pd_) for s, e, pd_ in periods), Decimal(0)).quantize(DF_Q, rounding=ROUND_HALF_EVEN)
        bpv = X0 * from_c(n_eur) * astar
        if bpv == 0:
            raise RejectError("TERMS", "the swap has no EUR annuity: it cannot be quoted")
        par = -pv0 / (sign_eur * bpv)                        # PV(s) = PV(0) + sign_eur x X0 x N x A* x s = 0
        par_bp = par / BP
        hw_eff = hw_bp * mult
        if eur_side == "pay":
            dealt_bp = (par_bp + hw_eff).quantize(SPREAD_STEP_BP, rounding=ROUND_CEILING)
        else:
            dealt_bp = (par_bp - hw_eff).quantize(SPREAD_STEP_BP, rounding=ROUND_FLOOR)
    client_bp = p.get("client_spread_bp")
    if client_bp is not None:
        better = (client_bp < dealt_bp) if eur_side == "pay" else (client_bp > dealt_bp)
        if better:
            raise RejectError("OFF_MARKET", f"{'paying' if eur_side == 'pay' else 'receiving'} {FLOAT_INDEX[foreign]} {_bp_text(client_bp)} is better for you than"
                              f" {prime.broker_of(acct)}'s quote {FLOAT_INDEX[foreign]} {_bp_text(dealt_bp)} (par {par_bp.quantize(Decimal('0.0001'))} bp"
                              f" {'+' if eur_side == 'pay' else '-'} {hw_eff.normalize()} bp half-width): a dealer does not deal there; leave the spread blank"
                              " to deal at the quote", {"spread_bp": str(client_bp), "quote_bp": str(dealt_bp), "par_bp": str(par_bp.quantize(Decimal('0.0001')))})
        dealt_bp = client_bp
    rounding_bp = (dealt_bp - par_bp - hw_eff) if eur_side == "pay" else (par_bp - hw_eff - dealt_bp)
    b = dealt_bp * BP
    with localcontext(P34):
        full_b, _u, _e = float_full_value(legs, b, X0, t_spot, proj, disc)
        mark0 = to_c(full_b)

        def bumped(**kw):
            pj, ds = market.zc(**kw)
            return float_full_value(legs, b, X0, t_spot, pj, ds)[0]
        sens = {"basis01_c": to_c(bumped(mid=1) - full_b), "rate01_c": {"USD": to_c(bumped(bu=1) - full_b), foreign: to_c(bumped(be=1) - full_b)},
                "eur_ois_spread01_c": to_c(bumped(grid=1) - full_b),
                "fx_1pct_c": to_c(float_full_value(legs, b, X0 * Decimal("1.01"), t_spot, proj, disc)[0] - full_b),
                "bp_value_c": to_c(bpv * BP)}
        rows = []
        for k, (s, e, pd_) in enumerate(periods, 1):
            dk = Decimal((e - s).days)
            ce = to_c(from_c(n_eur) * ((ONE / proj[foreign].df(s, e) - ONE) + b * dk / Decimal(360)))
            cu = to_c(from_c(n_usd) * (ONE / proj["USD"].df(s, e) - ONE))
            rows.append({"period": k, "start": s.isoformat(), "end": e.isoformat(), "pay_date": pd_.isoformat(), "days": int(dk),
                         f"{foreign.lower()}_indicative_c": ce, "usd_indicative_c": cu, "indicative": True})
    imp = swaps.im_params(services, acct["account_id"])
    im_pct = Decimal(imp["xccy_im_pct"])
    im = to_c(im_pct * from_c(n_usd))
    conversion = None
    net_start = {"USD": -n_usd if usd_side == "receive" else n_usd, foreign: n_eur if eur_side == "pay" else -n_eur}
    if p.get("start_conversion"):
        pair = fx_pair_id(foreign)
        quoted, rate = fxmod.deal_rate(X0, pair, "USD", foreign)            # the FX desk's bid on the pinned mid (its disclosed spread)
        f_c, usd_c, _adj = fxmod.deal_amounts(rate, n_eur, foreign, foreign)
        spread_pips = ((X0 - quoted) / fxmod.pip_of(pair)).normalize()
        conversion = {"on": True, "pair": pair, "mid": str(X0), "rate": str(quoted), "rate_usd_per_unit": str(rate),
                      "spread_pips": f"{spread_pips:f}", "sell": {"currency": foreign, "amount_c": f_c}, "buy": {"currency": "USD", "amount_c": usd_c},
                      "spread_cost_c": to_c(from_c(f_c) * X0) - usd_c, "value_date": start.isoformat(), "trade_date": trade_date.isoformat(), "deal_id": None,
                      "words": (f"sell {foreign} {fmt_money(f_c)} / buy USD {fmt_money(usd_c)} at {quoted} (the FX desk's bid: mid {X0} - "
                                f"{spread_pips:f} pip), value {start.isoformat()}, booked in the same command and settled with the start exchange"
                                " (payment versus payment)")}
        net_start = {"USD": usd_c - n_usd, foreign: 0}
    return FloatDeal(acct, p, prime.broker_of(acct), config.PRIME_BROKERS.get(prime.broker_of(acct), prime.broker_of(acct)), trade_date, start, periods, fxv, X0,
                     n_usd, n_eur, legs, market, hw_bp, mult, pv0, final_timing, astar, par_bp, dealt_bp, rounding_bp, client_bp, mark0, sens, im, im_pct,
                     imp["source"], str(sched.swap_margin_mode).upper(), getattr(sched, "updated_ts", None), conversion, net_start, rows, full_b)


def _bp_text(bp) -> str:
    """0.1 -> "+ 0.1 bp", 0 -> "flat", -2.5 -> "- 2.5 bp"."""
    d = Decimal(str(bp))
    if d == 0:
        return "flat"
    return f"{'+' if d > 0 else '-'} {abs(d).normalize():f} bp"


def _float_side_text(foreign: str, eur_side: str, dealt_bp) -> str:
    """"pay EUR ESTR + 0.1 bp (borrowed) / receive USD SOFR flat (lent)" (the legs named by their coupons, D4 Direction)."""
    idx = FLOAT_INDEX[foreign]
    f = f"{foreign} {idx} {_bp_text(dealt_bp)}"
    if eur_side == "pay":
        return f"pay {f} (borrowed) / receive USD SOFR flat (lent)"
    return f"receive {f} (lent) / pay USD SOFR flat (borrowed)"


def _float_labels(deal: "FloatDeal") -> List[str]:
    out = ["MODEL"]
    for c in (deal.market.usd, deal.market.eur_disc()):
        out += [x for x in _curve_label(c) if x not in out]
    out.append(f"ASSUMED: the {deal.p['foreign']}/USD basis level, the dealer half-width and IM are {deal.dealer} schedule terms")
    out.append(swaps.QUOTE_LABEL)
    return out


def _basis_lines(deal: "FloatDeal") -> List[str]:
    lvl = deal.market.level_bp
    f = deal.p["foreign"]
    par4 = deal.par_bp.quantize(Decimal("0.0001"))
    return [f"basis level {lvl.normalize():f} bp ASSUMED: an assumed level, not a neutral one (the EUR/USD basis has been negative for most of the time since"
            f" 2008); each 1 bp is worth USD {fmt_money(abs(int(deal.sens['basis01_c'])))} on this swap",
            f"par spread {par4} bp: the {f}-leg spread at which the swap is worth 0 on the dealer's curves (the final exchange paid"
            f" {FLOAT_PAY_DELAY_BD} business days after the end: {fmt_money(to_c(deal.final_timing))}; the coupon payment delays:"
            f" {fmt_money(to_c(deal.pv0) - to_c(deal.final_timing))})",
            f"dealer half-width {deal.hw_bp.normalize():f} bp x {deal.mult.normalize():f} ({deal.dealer} schedule, ASSUMED)",
            (f"dealt spread {_bp_text(deal.dealt_bp)} = {par4} {'+' if deal.eur_side == 'pay' else '-'} {deal.hw_eff_bp.normalize():f}, rounded"
             f" {'up' if deal.eur_side == 'pay' else 'down'} on the 0.1 bp grid (against you)") if deal.client_bp is None else
            f"dealt spread {_bp_text(deal.dealt_bp)}: your spread, no better than the dealer's quote"]


def _float_record(deal: "FloatDeal", now_ts: int, limits: Optional[dict], funding: Optional[dict]) -> dict:
    """3.3 the stored pricing record (the preview, the RFQ quote and ``terms_json.pricing_record`` carry it): every input with its source and
    time, the curves to the first node beyond the last payment, the dealer terms, the results.  ``otcplan.reprice`` re-derives every number from
    it alone (K9): discount factors unquantised; A* = round_half_even(sum over k of d_k / 360 x DF*_EUR(S, p_k), 1e-10); s_par = PV(0) / (X0 x N
    x A*) unrounded for the EUR payer (its negative for the receiver); dealt = the 0.1 bp ceiling (payer) / floor (receiver) of s_par +/- hw x
    mult; mark0 = the full value at the dealt spread, rounded to the cent."""
    f = deal.p["foreign"]
    fxv = deal.fx_view
    until = deal.maturity
    pv0_c, ft_c = to_c(deal.pv0), to_c(deal.final_timing)
    return {"method": FLOAT_METHOD, "model_version": MODEL_VERSION, "model": FLOAT_MODEL, "valuation_ts": int(now_ts),
            "trade_date": deal.trade_date.isoformat(), "spot_date": deal.start.isoformat(), "start_date": deal.start.isoformat(),
            "termination_date": deal.termination.isoformat(), "maturity_date": deal.maturity.isoformat(),
            "deal_fx": {"pair": fx_pair_id(f), "mid": str(deal.X0), "source": getattr(fxv, "source", None), "quote_time": getattr(fxv, "quote_time", None),
                        "quality": getattr(fxv, "quality", None), "label": quote_label(fxv), "convention": f"USD per {f} (EURUSD, market convention)"},
            "curves": {"USD": dict(_curve_record(deal.market.usd, until), role="projection and discounting (USD has no basis level)"),
                       f: dict(_curve_record(deal.market.eur, until), role=f"projection; discounting adds the {f}/USD basis level",
                               eur_ois_spread_bp=str(deal.market.eur_ois_spread_bp)),
                       f"{f}_discount": dict(_curve_record(deal.market.eur_disc(), until), role=f"the {f} discount curve under the USD CSA: the {f} curve"
                                                                                                f" plus the basis level {deal.market.level_bp} bp")},
            "legs": {side: {"currency": deal.legs.leg_ccy[side], "index": FLOAT_INDEX[deal.legs.leg_ccy[side]], "notional_c": deal.legs.leg_n_c[side],
                            "print": _first_print(deal.market.usd if deal.legs.leg_ccy[side] == "USD" else deal.market.eur),
                            "spread_bp": str(deal.dealt_bp) if deal.legs.leg_ccy[side] == f else "0", "day_count": "ACT/360",
                            "compounding": FLOAT_COMPOUNDING[FLOAT_INDEX[deal.legs.leg_ccy[side]]], "payment_delay_bd": FLOAT_PAY_DELAY_BD,
                            "calendar": "TARGET2" if deal.legs.leg_ccy[side] == "EUR" else "New York (SOFR dates: SIFMA)"} for side in ("receive", "pay")},
            "basis": {"pair": fx_pair_id(f), "level_bp": str(deal.market.level_bp), "par_bp": str(deal.par_bp), "par_bp_4dp": str(deal.par_bp.quantize(Decimal("0.0001"))),
                      "hw_bp": str(deal.hw_bp), "otc_spread_mult": str(deal.mult), "dealt_bp": str(deal.dealt_bp),
                      "rounding_bp": str(deal.rounding_bp.quantize(Decimal("0.0001"))), "on_leg": f, "eur_side": deal.eur_side,
                      "client_bp": None if deal.client_bp is None else str(deal.client_bp),
                      "source": f"{deal.dealer} schedule (seed / as edited), ASSUMED: no free cross-currency basis source", "updated_ts": deal.schedule_updated_ts},
            "schedule": [{"s": r["start"], "e": r["end"], "p": r["pay_date"], "days": r["days"], f"{f.lower()}_indicative_c": r[f"{f.lower()}_indicative_c"],
                          "usd_indicative_c": r["usd_indicative_c"]} for r in deal.rows],
            "exchanges": _float_exchanges(deal), "conversion": deal.conversion,
            "notionals": {"usd_c": deal.n_usd_c, f"{f.lower()}_c": deal.n_eur_c, "struck": f"USD = to_c({f} x X0)"},
            "results": {"pv_zero_c": pv0_c, "pv_zero": str(deal.pv0), "final_exchange_timing_c": ft_c, "coupon_delays_c": pv0_c - ft_c,
                        "annuity_star": str(deal.astar), "bp_value_c": deal.sens["bp_value_c"], "mark0_c": deal.mark0_c, "basis01_c": deal.sens["basis01_c"],
                        "rate01_c": dict(deal.sens["rate01_c"]), "eur_ois_spread01_c": deal.sens["eur_ois_spread01_c"], "fx_1pct_c": deal.sens["fx_1pct_c"],
                        "net_start_flows": dict(deal.net_start),
                        "im": {"pct": str(deal.im_pct), "im_c": deal.im_c, "rule": f"IM = {swaps.pct_text(deal.im_pct)} of the USD notional ({deal.im_source})"}},
            "limits": limits, "funding": funding, "labels": _float_labels(deal), "eur_obligations": _float_obligations(deal)}


def _float_exchanges(deal: "FloatDeal") -> dict:
    f = deal.p["foreign"]
    lent, bor = ("USD", f) if deal.eur_side == "pay" else (f, "USD")
    amt = {"USD": deal.n_usd_c, f: deal.n_eur_c}
    return {"initial": {"date": deal.start.isoformat(), "rate": str(deal.X0), "we_pay": {"currency": lent, "amount_c": amt[lent], "gl": "1360"},
                        "we_receive": {"currency": bor, "amount_c": amt[bor], "gl": "2360"},
                        "words": (f"on the start date {deal.start.isoformat()} {deal.dealer} pays {bor} {fmt_money(amt[bor])} and the Fund pays {lent}"
                                  f" {fmt_money(amt[lent])} at {deal.X0}")},
            "final": {"date": deal.maturity.isoformat(), "rate": str(deal.X0), "we_pay": {"currency": bor, "amount_c": amt[bor], "gl": "2360"},
                      "we_receive": {"currency": lent, "amount_c": amt[lent], "gl": "1360"},
                      "words": (f"with the last coupons on {deal.maturity.isoformat()} the Fund repays {bor} {fmt_money(amt[bor])} and receives {lent}"
                                f" {fmt_money(amt[lent])} back (no notional reset)")}}


def _float_obligations(deal: "FloatDeal") -> List[dict]:
    """The obligations in the foreign currency (D9): each coupon of the paid foreign leg (indicative; the index decides) and the final exchange."""
    f = deal.p["foreign"]
    sf = (deal.p.get("settlement_funding") or {}).get(f)
    cal_ok = _ccycal()
    out = []
    if deal.eur_side != "pay":
        return out
    for i, r in enumerate(deal.rows):
        amount = int(r[f"{f.lower()}_indicative_c"]) + (deal.n_eur_c if i == len(deal.rows) - 1 else 0)
        what = f"coupon {r['period']} (indicative; the {FLOAT_INDEX[f]} index decides)" + (" + the final exchange" if i == len(deal.rows) - 1 else "")
        how = "not funded automatically: the account must hold it"
        if sf == "BUY_WITH_USD":
            buy_on = None
            if cal_ok is not None:
                try:
                    cal = cal_ok.joint(cal_ok.TARGET, cal_ok.USNY)
                    buy_on = cal.add_business_days(_date(r["pay_date"]), -2) if hasattr(cal, "add_business_days") else None
                except Exception:   # noqa: BLE001 - the words without the date
                    buy_on = None
            how = f"settlement funding: {f} bought with USD" + (f" on {buy_on.isoformat()}" if buy_on else " two business days before") + f", value {r['pay_date']}"
        out.append({"date": r["pay_date"], "currency": f, "amount_c": amount, "what": what, "funded_by": how})
    return out


def _float_dict(services, conn, deal: "FloatDeal", now_ts: int, snapshot=None, limits: Optional[dict] = None, funding_line: Optional[dict] = None) -> dict:
    """The preview of a float / float swap (the shape of ``tests/fixtures/terminal/xccy_float_preview.json``, P-B's ticket)."""
    p = deal.p
    f = p["foreign"]
    pair = fx_pair_id(f)
    eur_side, usd_side = deal.eur_side, ("receive" if deal.eur_side == "pay" else "pay")
    aid = deal.acct["account_id"]
    legs = {}
    for side in ("receive", "pay"):
        ccy = deal.legs.leg_ccy[side]
        idx = FLOAT_INDEX[ccy]
        row1 = deal.rows[0]
        legs[side] = {"side": side, "kind": "swaprcv" if side == "receive" else "swappay", "currency": ccy, "index": idx, "index_name": FLOAT_INDEX_WORDS[idx],
                      "notional_c": deal.legs.leg_n_c[side], "spread_bp": str(deal.dealt_bp) if ccy == f else "0", "day_count": "ACT/360",
                      "compounding": FLOAT_COMPOUNDING[idx], "payment_delay_bd": FLOAT_PAY_DELAY_BD,
                      "calendar": "TARGET2" if ccy == "EUR" else "New York (SOFR dates: SIFMA)",
                      "current_print": _first_print(deal.market.usd if ccy == "USD" else deal.market.eur),
                      "role": ("lent: paid out at the start (1360 receivable) and received back at maturity; its coupons are received" if side == "receive"
                               else "borrowed: received at the start (2360 payable) and repaid at maturity; its coupons are paid"),
                      "period1_indicative_c": row1["usd_indicative_c"] if ccy == "USD" else row1[f"{f.lower()}_indicative_c"]}
    limits = limits if limits is not None else _limits_block(services, conn, aid, deal.n_usd_c, deal.mark0_c, snapshot)
    funding_line = funding_line if funding_line is not None else _funding_line(services, conn, aid, deal)
    record = _float_record(deal, now_ts, limits, funding_line)
    side_text = _float_side_text(f, eur_side, deal.dealt_bp)
    spread_key = f"{'pay' if eur_side == 'pay' else 'recv'}_spread_bp"
    other_key = f"{'recv' if eur_side == 'pay' else 'pay'}_spread_bp"
    out = {"kind": "XCCY", "variant": FLOAT_VARIANT, "account_id": aid, "broker": deal.broker, "dealer": deal.dealer, "currency": "USD",
           "pay_ccy": p["pay_ccy"], "recv_ccy": p["recv_ccy"], "foreign_ccy": f, "side": f"RECEIVE_{p['recv_ccy']}_PAY_{p['pay_ccy']}", "side_text": side_text,
           "pair_label": f"{pair[:3]}/{pair[3:]}", "deal_pair": pair, "deal_rate": str(deal.X0), "deal_rate_text": f"{pair} {deal.X0} (USD per {f}, market convention)",
           "deal_rate_source": quote_label(deal.fx_view), "fx_quote": _quote_dict(deal.fx_view),
           "deal_rate_as_of": getattr(deal.fx_view, "quote_time", None) or ((getattr(deal.fx_view, "inputs", None) or {}).get("as_of")),
           "notional_c": deal.n_usd_c, "usd_notional_c": deal.n_usd_c, "foreign_notional_c": deal.n_eur_c,
           "pay_notional_c": deal.legs.leg_n_c["pay"], "recv_notional_c": deal.legs.leg_n_c["receive"],
           "months": p["months"], "tenor_months": p["months"], "period_months": FLOAT_STEP_MONTHS, "payment_delay_bd": FLOAT_PAY_DELAY_BD,
           "trade_date": deal.trade_date.isoformat(), "start_date": deal.start.isoformat(), "termination_date": deal.termination.isoformat(),
           "maturity_date": deal.maturity.isoformat(), "calendars": dict(FLOAT_CALENDAR_WORDS), "legs": legs,
           spread_key: str(deal.dealt_bp), other_key: "0", "dealt_spread_bp": str(deal.dealt_bp),
           "spread": dict(record["basis"], lines=_basis_lines(deal)),
           "pv_zero_c": record["results"]["pv_zero_c"], "pv_zero_parts": {"final_exchange_timing_c": record["results"]["final_exchange_timing_c"],
                                                                          "coupon_delays_c": record["results"]["coupon_delays_c"]},
           "annuity_star": str(deal.astar), "bp_value_c": deal.sens["bp_value_c"], "mark0_c": deal.mark0_c, "cost_of_dealing_c": max(0, -deal.mark0_c),
           "basis01_c": deal.sens["basis01_c"], "rate01_c": dict(deal.sens["rate01_c"]), "eur_ois_spread01_c": deal.sens["eur_ois_spread01_c"],
           "fx_1pct_c": deal.sens["fx_1pct_c"], "dv01_c": deal.sens["rate01_c"]["USD"],
           "dv01_note": "PV change for +1 bp on the USD curve (signed); rate01_c gives each currency",
           "im_c": deal.im_c, "im_pct": swaps.pct_text(deal.im_pct), "im_rule": f"IM = {swaps.pct_text(deal.im_pct)} of the USD notional ({deal.im_source}, ASSUMED)",
           "margin_mode": deal.margin_mode,
           "csa_note": ("variation margin daily at 17:05 ET in USD on the CSA exposure (the full value: the clean mark plus the notionals and accrued coupons"
                        " at the FX close); NAV carries the clean mark"),
           "schedule": deal.rows, "exchanges": _float_exchanges(deal),
           "start_conversion": deal.conversion or {"on": False, "words": "no inception conversion: the swap's start exchange is settled from the account"},
           "net_start_flows": dict(deal.net_start, words=_net_start_words(deal)),
           "settlement_funding": p.get("settlement_funding"), "settlement_funding_words": (funding_line or {}).get("words"),
           "eur_obligations": record["eur_obligations"], "limits": limits, "labels": record["labels"], "pricing_record": record,
           "terms": _float_terms_echo(p), "conventions": list(CONVENTIONS), "model": FLOAT_MODEL, "method": FLOAT_METHOD, "blockers": [],
           "inputs": {"model": FLOAT_MODEL, "deal_rate": str(deal.X0), "fx_quality": getattr(deal.fx_view, "quality", None),
                      "fx_source": getattr(deal.fx_view, "source", None), "fx_quote_time": getattr(deal.fx_view, "quote_time", None),
                      "fx_label": quote_label(deal.fx_view), "curves_of": deal.market.on.isoformat(), "spread_precision": "0.1 bp"}}
    return out


def _net_start_words(deal: "FloatDeal") -> str:
    f = deal.p["foreign"]
    u, e = int(deal.net_start.get("USD", 0)), int(deal.net_start.get(f, 0))
    parts = []
    for ccy, v in (("USD", u), (f, e)):
        if v < 0:
            parts.append(f"pays {ccy} {fmt_money(-v)}")
        elif v > 0:
            parts.append(f"receives {ccy} {fmt_money(v)}")
    why = " (the conversion's spread)" if deal.conversion and u < 0 and e == 0 else ""
    return f"on {deal.start.isoformat()} the Fund " + (" and ".join(parts) if parts else "pays and receives nothing net") + f" net{why}"


def _float_terms_echo(p: dict) -> dict:
    out = {"pay_ccy": p["pay_ccy"], "recv_ccy": p["recv_ccy"], "pay_index": p["pay_index"], "recv_index": p["recv_index"], "months": p["months"],
           "mtm_reset": False, "start_conversion": bool(p.get("start_conversion")), "settlement_funding": p.get("settlement_funding")}
    if p.get("pay_notional_c") is not None:
        out["pay_notional_c"] = p["pay_notional_c"]
    if p.get("recv_notional_c") is not None:
        out["recv_notional_c"] = p["recv_notional_c"]
    return out


def float_start_check(services, conn, aid: str, deal: "FloatDeal", snapshot=None) -> dict:
    """3.3 Blocked: the IM now and the start-date net (with the conversion: USD 40.00 in the example; without it: the USD lent) - one commitment
    measured as a booking measures it (PO-07); a foreign currency the swap lends at the start is checked from what the account holds by then."""
    u_net = -int(deal.net_start.get("USD", 0))
    need = deal.im_c + max(0, u_net)
    f = swaps.funding_preview(services, conn, aid, "USD", need, snapshot,
                              what=f"IM + the USD the start exchange needs on {deal.start.isoformat()}" if u_net > 0 else "IM")
    f["start_date"] = deal.start.isoformat()
    f["start_net_usd_c"] = -u_net
    f_ccy = deal.p["foreign"]
    if int(deal.net_start.get(f_ccy, 0)) < 0:            # the swap lends the foreign currency at the start: from what the account holds by then
        chk = fxmod.funding_check(services, aid, f_ccy, -int(deal.net_start[f_ccy]), deal.start, conn=conn)
        f["foreign_check"] = chk
        if not chk.get("fundable"):
            f["ok"] = False
            f["note"] = (f"the start exchange pays {f_ccy} {fmt_money(-int(deal.net_start[f_ccy]))} on {deal.start.isoformat()}: {chk.get('text')}; it is never"
                         " converted automatically")
    return f


def _float_preview(services, conn, account_id: str, acct: dict, p: dict, ts: int, marks) -> dict:
    deal = _float_deal(services, conn, marks, acct, p, ts)
    out = _float_dict(services, conn, deal, ts, marks)
    out["funding"] = float_start_check(services, conn, account_id, deal, marks)
    return out


def _book_xccy_float(ctx: TxContext, services, acct: dict, p: dict) -> dict:
    """D4-D6 / 4.2 Booking, one command: the IM (one Treasury sweep), the contract (start = the spot date, trade date = the FX trade date,
    maturity = the last payment date, mark0 = the cost of dealing), the inception conversion through ``fx.book_linked_spot`` (value = the start,
    tagged xccy_start, linked both ways), the SWAP row; no exchange before the start."""
    conn = ctx.conn
    aid = acct["account_id"]
    deal = _float_deal(services, conn, ctx.marks, acct, p, int(ctx.ts))
    chk = float_start_check(services, conn, aid, deal, ctx.marks)
    if not chk.get("ok", True):
        raise ConflictError("INSUFFICIENT_FUNDS", chk.get("note") or "the IM and the start-date net cannot be funded", {"funding": chk})
    f = p["foreign"]
    linked = getattr(fxmod, "book_linked_spot", None)
    if deal.conversion and not callable(linked):
        raise ConflictError("CONVERSION_UNAVAILABLE", "the inception conversion cannot be booked in this build (the FX desk's linked spot deal is not available);"
                            " nothing was written", {"start_conversion": True})
    broker = deal.broker
    # V1061-03: the record's book-reading parts (D18 limits, D9 funding plan) before anything is posted - the plan reads this command's own
    # uncommitted writes, and the quote's own IM and start net are handed to it as flows (``_quoted_float_flows``): counted once, as quoted
    limits = _limits_block(services, conn, aid, deal.n_usd_c, deal.mark0_c, ctx.marks)
    funding_line = _funding_line(services, conn, aid, deal)
    cid = next_id(conn, "swap")
    idem = f"im:{cid}:open"
    funding = _fund(ctx, services, aid, "USD", deal.im_c, "otc", idem, overdraft=False, commit=True)      # PO-07: against available
    side_text = _float_side_text(f, deal.eur_side, deal.dealt_bp)
    memo = (f"OTC IM {cid}: {fx_pair_id(f)[:3]}/{fx_pair_id(f)[3:]} float / float cross-currency swap, {side_text}; {f} {fmt_money(deal.n_eur_c)} / USD"
            f" {fmt_money(deal.n_usd_c)} at {_px_text(deal.X0)} ({quote_label(deal.fx_view)}) from {deal.start.isoformat()} to {deal.maturity.isoformat()} with"
            f" {broker}; IM = {swaps.pct_text(deal.im_pct)} x USD {fmt_money(deal.n_usd_c)} = {fmt_money(deal.im_c)} ({deal.im_source}); mark0"
            f" {fmt_money(deal.mark0_c)} = cost of dealing (par {deal.par_bp.quantize(Decimal('0.0001'))} bp + {deal.hw_eff_bp.normalize():f} bp half-width,"
            f" dealt {_bp_text(deal.dealt_bp)})")
    entries = [ctx.post(Entry("OTC_IM", memo, [dr(aid, "1330", "USD", deal.im_c, instrument_id=cid, memo="OTC initial margin"),
                                               cr(aid, "1010", "USD", deal.im_c, memo="IM paid")], idem, ref_type="otc", ref_id=cid))]
    conversion = dict(deal.conversion) if deal.conversion else None
    conv_out = None
    name = (f"XCCY {fx_pair_id(f)[:3]}/{fx_pair_id(f)[3:]} float: {side_text}, USD {fmt_money(deal.n_usd_c)}"
            f" ({deal.start.isoformat()} to {deal.maturity.isoformat()}, {broker})")
    _insert_instrument(services, ctx, cid, name, deal.maturity, {"contract_id": cid, "otc_kind": "XCCY", "variant": FLOAT_VARIANT, "broker": broker,
                                                                  "pay_ccy": p["pay_ccy"], "recv_ccy": p["recv_ccy"], "deal_rate": str(deal.X0),
                                                                  "start_date": deal.start.isoformat(), "maturity_date": deal.maturity.isoformat(),
                                                                  "margin_mode": deal.margin_mode})
    record = _float_record(deal, ctx.ts, limits, funding_line)
    record["conversion"] = conversion
    pj = [[s.isoformat(), e.isoformat(), q.isoformat()] for s, e, q in deal.periods]
    legs = {}
    for side in ("receive", "pay"):
        ccy = deal.legs.leg_ccy[side]
        legs[side] = {"currency": ccy, "notional_c": deal.legs.leg_n_c[side], "index": FLOAT_INDEX[ccy], "basis": "ACT/360",
                      "spread_bp": str(deal.dealt_bp) if ccy == f else "0", "rate": None, "fixing": "COMPOUND_ARREARS"}
    t = {"kind": "XCCY", "variant": FLOAT_VARIANT, "pay_ccy": p["pay_ccy"], "recv_ccy": p["recv_ccy"], "foreign_ccy": f, "usd_side": p["usd_side"],
         "eur_side": deal.eur_side, "legs": legs, "pay_index": p["pay_index"], "recv_index": p["recv_index"], "deal_rate": str(deal.X0),
         "deal_pair": fx_pair_id(f), "months": p["months"], "tenor_months": p["months"], "period_months": FLOAT_STEP_MONTHS,
         "payment_delay_bd": FLOAT_PAY_DELAY_BD, "calendars": dict(FLOAT_CALENDAR_WORDS), "mtm_reset": False, "periods": pj, "fixed_periods": pj,
         "trade_date": deal.trade_date.isoformat(), "start_date": deal.start.isoformat(), "termination_date": deal.termination.isoformat(),
         "maturity_date": deal.maturity.isoformat(), "spread": {"on_leg": f, "dealt_bp": str(deal.dealt_bp), "par_bp": str(deal.par_bp),
                                                                 "level_bp": str(deal.market.level_bp), "hw_bp": str(deal.hw_bp), "otc_spread_mult": str(deal.mult)},
         "pay_spread_bp": str(deal.dealt_bp) if deal.eur_side == "pay" else "0", "recv_spread_bp": "0" if deal.eur_side == "pay" else str(deal.dealt_bp),
         "half_width_bp": str(deal.hw_bp), "otc_spread_mult": str(deal.mult), "mark0_c": deal.mark0_c, "dv01_0_c": deal.sens["rate01_c"]["USD"],
         "im_pct": str(deal.im_pct), "dealt_ts": ctx.ts, "model": FLOAT_MODEL, "deal_rate_source": quote_label(deal.fx_view),
         "deal_rate_quote_time": getattr(deal.fx_view, "quote_time", None), "start_conversion": conversion,
         "settlement_funding": p.get("settlement_funding"), "pricing_record": record}
    inputs = {"as_of": ctx.ts, "model": FLOAT_MODEL, "deal_rate": str(deal.X0), "deal_rate_source": quote_label(deal.fx_view), "full_value_c": deal.mark0_c,
              "balance_sheet_c": 0, "clean_mark_c": deal.mark0_c, "csa_exposure_c": deal.mark0_c, "valuation_date": deal.trade_date.isoformat(),
              "spot_date": deal.start.isoformat(), "curve_as_of": str(getattr(deal.market.usd, "d0", "")),
              "fx": {"pair": fx_pair_id(f), "usd_per_unit": str(deal.X0), "quality": getattr(deal.fx_view, "quality", None),
                     "source": getattr(deal.fx_view, "source", None), "quote_time": getattr(deal.fx_view, "quote_time", None)}}
    conn.execute("INSERT INTO otc_contracts(contract_id, command_id, account_id, broker, kind, terms_json, notional_c, currency, start_date, maturity_date, margin_mode, im_c,"
                 " status, last_reset_date, reset_px, mark_c, mark_ts, vm_balance_c, inputs_json, closed_ts) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,'OPEN',?,?,?,?,0,?,NULL)",
                 (cid, ctx.command_id, aid, broker, "XCCY", dumps(t), deal.n_usd_c, "USD", deal.start.isoformat(), deal.maturity.isoformat(),
                  deal.margin_mode, deal.im_c, deal.start.isoformat(), str(deal.X0), deal.mark0_c, ctx.ts, dumps(inputs)))
    if conversion:
        # D6: the inception conversion in the SAME command, after the contract row exists (fx.book_linked_spot's OA-11 check counts the swap's
        # scheduled start receipt of the foreign currency, K4), then linked both ways: the deal's inputs.link names the contract, the contract's
        # terms carry start_conversion.deal_id
        conv_out = _call_linked_spot(linked, ctx, aid, deal, cid)
        dd = conv_out.get("deal") if isinstance(conv_out, dict) else None
        conversion["deal_id"] = (dd or {}).get("deal_id") if isinstance(dd, dict) else (conv_out or {}).get("deal_id")
        if not conversion["deal_id"]:
            raise ConflictError("CONVERSION_UNAVAILABLE", "the inception conversion was not booked (no deal id came back); nothing was written",
                                {"conversion": conv_out})
        if (conv_out or {}).get("entry_id"):
            entries.append(conv_out["entry_id"])
        t["start_conversion"] = conversion
        t["pricing_record"]["conversion"] = conversion
        conn.execute("UPDATE otc_contracts SET terms_json=? WHERE contract_id=?", (dumps(t), cid))
    ctx.touched_instruments.add((aid, cid))
    ctx.event("OTC_BOOKED", account_id=aid, instrument_id=cid, ref_type="otc", ref_id=cid, kind="XCCY", variant=FLOAT_VARIANT, broker=broker,
              pay_ccy=p["pay_ccy"], recv_ccy=p["recv_ccy"], usd_notional_c=deal.n_usd_c, foreign_notional_c=deal.n_eur_c, deal_rate=str(deal.X0),
              dealt_spread_bp=str(deal.dealt_bp), par_spread_bp=str(deal.par_bp.quantize(Decimal("0.0001"))), mark0_c=deal.mark0_c, im_c=deal.im_c,
              margin_mode=deal.margin_mode, start_date=deal.start.isoformat(), maturity_date=deal.maturity.isoformat(),
              conversion_deal_id=(conversion or {}).get("deal_id"))
    view = swaps.contract_view(services, cid, conn=conn, detail=True, now_ts=ctx.ts)
    preview = _float_dict(services, conn, deal, ctx.ts, ctx.marks, limits=limits, funding_line=funding_line)
    preview["start_conversion"] = conversion or preview["start_conversion"]
    preview["pricing_record"] = t["pricing_record"]
    return {"contract": view, "contract_id": cid, "im_transfer": {"amount_c": deal.im_c, "funding": funding}, "start_exchange": None,
            "conversion": conv_out, "entries": entries, "preview": preview}


def _call_linked_spot(fn, ctx: TxContext, aid: str, deal: "FloatDeal", cid: str) -> dict:
    """K4 ``fx.book_linked_spot``: SELL the foreign notional / BUY USD at the desk's bid on the deal's mid, value = the start date, linked to the
    contract (``link {type: xccy_start, id}``) and its settlement rows tagged xccy_start / the contract."""
    f = deal.p["foreign"]
    link = {"type": "xccy_start", "id": cid, "text": f"inception conversion of {cid}"}
    out = fn(ctx, aid, "USD", f, deal.n_eur_c, f, deal.start, deal.X0, link, ("xccy_start", cid))
    return out if isinstance(out, dict) else {"deal": out}


# ---- a booked float / float swap: its state, the mark (clean + CSA exposure), the accruals, START / PAY / END ----------------------------------
def _float_legs_of(terms: dict) -> FloatLegs:
    f = terms["foreign_ccy"]
    legs = terms["legs"]
    return FloatLegs({s: legs[s]["currency"] for s in ("receive", "pay")}, {s: int(legs[s]["notional_c"]) for s in ("receive", "pay")}, _periods(terms), f)


def _float_spread(terms: dict) -> Decimal:
    return Decimal(str((terms.get("spread") or {}).get("dealt_bp") or "0")) * BP


def _index_factor(services, ccy: str, s: date, e: date) -> Decimal:
    """K3 ``fxcurves.index_factor``: I(e) / I(s) from the published compounded index (EUR: the TARGET2 fallback from the daily prints); 409
    INDEX_PENDING while either is not published - a coupon is never estimated for payment."""
    fxc = _fxcurves()
    if fxc is None or not hasattr(fxc, "index_factor"):
        raise ConflictError(INDEX_PENDING, f"the {FLOAT_INDEX.get(ccy, ccy)} index cannot be read in this build (the FX curve module is not available): the"
                            " payment waits", {"currency": ccy, "start": s.isoformat(), "end": e.isoformat()})
    return Decimal(str(fxc.index_factor(services, ccy, s, e)))


def _compounded_rate(factor: Decimal, days: int) -> Decimal:
    """D4: R = (I(e) / I(s) - 1) x 360 / d, rounded to 7 decimals of a fraction (fxcurves.compounded_rate when present)."""
    fxc = _fxcurves()
    fn = getattr(fxc, "compounded_rate", None) if fxc is not None else None
    if callable(fn):
        return Decimal(str(fn(factor, days)))
    return ((Decimal(factor) - ONE) * Decimal(360) / Decimal(int(days))).quantize(Decimal("0.0000001"), rounding=ROUND_HALF_EVEN)


def float_exact_coupon(services, ccy: str, n_c: int, s: date, e: date, spread: Decimal) -> Tuple[int, dict]:
    """D4 the exact coupon of a period: ``to_c(N x (R + spread) x d / 360)`` with R from the published indices (409 INDEX_PENDING otherwise)."""
    days = (e - s).days
    factor = _index_factor(services, ccy, s, e)
    r = _compounded_rate(factor, days)
    amount = to_c(from_c(int(n_c)) * (r + Decimal(spread)) * Decimal(days) / Decimal(360))
    return amount, {"factor": str(factor), "rate": str(r), "spread": str(Decimal(spread)), "days": days, "index": FLOAT_INDEX.get(ccy, ccy),
                    "start": s.isoformat(), "end": e.isoformat(), "formula": f"to_c({fmt_money(int(n_c))} x ({r} + {Decimal(spread)}) x {days} / 360)"}


def float_state(services, conn: sqlite3.Connection, row: dict, terms: dict, with_index: bool = True) -> FloatState:
    """Where the booked swap stands (FloatState): START / END done, the paid periods, each unpaid finished period's coupon (the exact one when both
    indices are published, else the posted estimate) and the factor realised so far of the period accruing (accrual_state)."""
    cid, aid = row["contract_id"], row["account_id"]
    legs = _float_legs_of(terms)
    periods = legs.periods
    paid_dates = {r["event_date"] for r in conn.execute("SELECT event_date FROM otc_resets WHERE contract_id=? AND kind='PAY'", (cid,)).fetchall()}
    st = FloatState(started=_has_reset(conn, cid, "NOTIONAL_START"), ended=_has_reset(conn, cid, "NOTIONAL_END"))
    st.paid = tuple(k for k, (_s, _e, p) in enumerate(periods) if p.isoformat() in paid_dates)
    spread = _float_spread(terms)
    for side, kind in (("receive", "swaprcv"), ("pay", "swappay")):
        ccy = legs.leg_ccy[side]
        a_st = _state(conn, kind, cid)
        seg = a_st["segment_start"] if a_st is not None else None
        for k, (s, e, _p) in enumerate(periods):
            if k in st.paid:
                continue
            if a_st is not None and seg == s.isoformat() and a_st["accrued_through"]:
                st.realised[(k, side)] = (Decimal(str(a_st["rate"])), _date(a_st["accrued_through"]) + timedelta(days=1))
            elif a_st is not None and seg is not None and seg > s.isoformat():
                exact = None
                if with_index:
                    try:
                        exact, _info = float_exact_coupon(services, ccy, legs.leg_n_c[side], s, e, spread if ccy == legs.spread_ccy else Decimal(0))
                    except RejectError:                    # not published yet (INDEX_PENDING): the posted estimate
                        exact = None
                if exact is None:
                    spec = swaps.LegSpec(kind, "float", ccy, legs.leg_n_c[side], "ACT/360", tuple(periods), "COMPOUND_ARREARS")
                    exact = swaps._posted_leg_c(conn, aid, cid, spec, (s, e, _p))
                st.known[(k, side)] = int(exact)
    return st


def _balance_sheet(conn: sqlite3.Connection, row: dict, fx_of) -> Tuple[Decimal, dict]:
    """D8 B(t): the contract's notionals and accrued coupons on the balance sheet in USD at the FX rate ``fx_of(ccy)``: 1360 + 1225 (assets) - 2360
    - 2340 (liabilities), each in its own currency."""
    aid, cid = row["account_id"], row["contract_id"]
    total = Decimal(0)
    detail: Dict[str, Dict[str, int]] = {}
    for r in conn.execute("SELECT gl, currency, SUM(balance_c) AS b FROM gl_balances WHERE account_id=? AND instrument_id=? AND gl IN ('1360','1225','2360','2340')"
                          " GROUP BY gl, currency", (aid, cid)).fetchall():
        b = natural(r["gl"], int(r["b"] or 0))
        if not b:
            continue
        detail.setdefault(r["currency"], {})[r["gl"]] = b
        sign = ONE if r["gl"] in ("1360", "1225") else -ONE
        total += sign * from_c(b) * fx_of(r["currency"])
    return total, detail


def _float_mark(services, row: dict, terms: dict, at: int, valuation_date=None, snapshot=None, level_shift_bp: Decimal = Decimal(0)):
    """``(clean mark cents, inputs)`` of a booked float / float swap for the spot date of the valuation date (D19): the full value (3.3) at the FX
    rate (the pair's close of the valuation date for SWAP_VM, LC-3; the live mid otherwise), minus B(t); ``inputs.csa_exposure_c`` = the full value
    (D8: the CTM variation margin follows it).  A stale ``MarkView`` of the stored mark when an input is missing; ``(None, {vm_outcome})`` for a
    VM date whose close is not known.  ``level_shift_bp`` moves the basis level (the termination's half-width, D17)."""
    cid = row["contract_id"]
    now_ts = int(at)
    f = terms["foreign_ccy"]
    pair = fx_pair_id(f)
    on = _date(valuation_date) if valuation_date is not None else fxmod.fx_trade_date(services, now_ts)
    base = {"as_of": now_ts, "valuation_date": on.isoformat(), "contract_id": cid, "model": FLOAT_MODEL, "variant": FLOAT_VARIANT, "quality": "computed"}
    if row.get("status") != "OPEN":
        return 0, dict(base, note=f"{row.get('status')}: no remaining cash flows")
    snap = snapshot if snapshot is not None else _snapshot(services, [pair])
    stored = int(row.get("mark_c") or 0)
    try:
        if valuation_date is not None:
            with _marks.shared_reader(services.db) as conn:
                px, info = _vm_close(services, pair, "FX_SPOT", on, lambda r: fx_close_on(services, conn, snap, f, r), snap.fx(f) if snap is not None else None)
            if px is None:
                return None, dict(base, **info)
            X = qprice(px)
            fx_in = {"pair": pair, "usd_per_unit": str(X), "quality": "close", "source": info.get("source"), "quote_time": info.get("quote_time"),
                     "close_date": info.get("close_date"), "basis": info.get("basis")}
        else:
            fxv = snap.fx(f) if snap is not None else None
            if fxv is None or fxv.mark is None or fxv.quality == "unavailable":
                raise ConflictError("NO_QUOTE", f"no {pair} rate: the {f} flows cannot be converted", {"pair": pair})
            X = qprice(fxv.mark)
            fx_in = {"pair": pair, "usd_per_unit": str(X), "quality": fxv.quality, "source": fxv.source, "quote_time": fxv.quote_time}
        cc = _ccycal()
        if cc is None:
            raise ConflictError("CALENDAR_UNAVAILABLE", "the currency calendars are not in this build", {})
        t_spot = cc.spot_date(services, pair, on)
        with _marks.shared_reader(services.db) as conn:
            acct = _rowd(account_row(conn, row["account_id"]))
            market = float_market(services, conn, acct, on)
            if level_shift_bp:
                market = FloatMarket(market.on, market.usd, market.eur, market.level_bp + Decimal(level_shift_bp), market.eur_ois_spread_bp)
            state = float_state(services, conn, row, terms)
            legs = _float_legs_of(terms)
            proj, disc = market.zc()
            with localcontext(P34):
                full, usd_part, f_part = float_full_value(legs, _float_spread(terms), X, t_spot, proj, disc, state)
                bsheet, bdetail = _balance_sheet(conn, row, lambda c: ONE if c == "USD" else X)
                clean = full - bsheet
    except ConflictError as e:
        if valuation_date is not None:
            # a curve / print / calendar input is missing for that VM date: retried (CLOSE_PENDING-style pending outcome, quiet the first time)
            return None, dict(base, vm_outcome="CLOSE_PENDING", note=f"{e.code}: {e.message}", waits_for="the swap's curves and prints of that date")
        return _stale_view(cid, stored, "XCCY float model (stored mark)", row.get("mark_ts"), now_ts, dict(base, stored_mark_c=stored),
                           f"{e.message}; the last computed mark, labelled stale")
    full_c, clean_c = to_c(full), to_c(clean)
    inputs = dict(base, fx=fx_in, spot_date=t_spot.isoformat(), curve_as_of=str(getattr(market.usd, "d0", "")),
                  curves={"USD": str(getattr(market.usd, "d0", "")), f: str(getattr(market.eur, "d0", ""))}, level_bp=str(market.level_bp),
                  full_value_c=full_c, balance_sheet_c=to_c(bsheet), balance_sheet=bdetail, clean_mark_c=clean_c, csa_exposure_c=full_c,
                  usd_flows_pv_c=to_c(usd_part), foreign_flows_pv=str(f_part.quantize(Decimal("0.01"))), started=state.started,
                  paid_periods=list(state.paid), qualities=[fx_in.get("quality") or "close"],
                  formula=f"clean {fmt_money(clean_c)} = full value {fmt_money(full_c)} - balance sheet {fmt_money(to_c(bsheet))} at {X}")
    if level_shift_bp:
        inputs["level_shift_bp"] = str(level_shift_bp)
    return clean_c, inputs


def _float_leg_spec(conn: sqlite3.Connection, row: dict, terms: dict, kind: str, night: date) -> Tuple[Optional[swaps.LegSpec], Optional[int]]:
    """4.2 Accruals: the COMPOUND_ARREARS ``LegSpec`` of a float leg (index SOFR / ESTR, the dealt spread on the foreign leg, ACT/360) - nights S ..
    e_n - 1; START_PENDING before the start exchange."""
    cid = row["contract_id"]
    if not _has_reset(conn, cid, "NOTIONAL_START"):
        raise RejectError("START_PENDING", f"{cid}: the start exchange has not happened; nothing accrues before it", {"contract_id": cid})
    periods = _periods(terms)
    k = _period_index(periods, night)
    if k is None:
        return None, None
    side = "receive" if kind == "swaprcv" else "pay"
    leg = terms["legs"][side]
    ccy = leg["currency"]
    spread = _float_spread(terms) if ccy == terms["foreign_ccy"] else Decimal(0)
    return swaps.LegSpec(kind, "float", ccy, int(leg["notional_c"]), "ACT/360", tuple(periods), "COMPOUND_ARREARS", None, FLOAT_INDEX[ccy], spread), k


def _settle_linked(ctx: TxContext, ref_type: str, cid: str, due: date, phase: str) -> Optional[dict]:
    """K4 ``fx.settle_linked``: settle the receive / pay legs of the deals tagged to this contract and due by ``due`` (None while not built)."""
    fn = getattr(fxmod, "settle_linked", None)
    if not callable(fn):
        return None
    return fn(ctx, ref_type, cid, due, phase)


def _float_start(ctx: TxContext, services, row: dict, d: date, key: str, job: Optional[str]) -> dict:
    """D7 / 4.2 START, one command at 00:45 ET of the start date: the conversion's receivable (USD in), the net shortfall funded, the exchange (USD
    lent out, EUR borrowed in, at the deal rate), the conversion's payable (EUR out), the NOTIONAL_START row.  Any failure rolls the whole step back
    (SAVEPOINT): SKIPPED with an ERROR naming the reason, retried; the linked rows wait - nothing settles the conversion alone."""
    conn = ctx.conn
    cid, aid = row["contract_id"], row["account_id"]
    conn.execute("SAVEPOINT xccy_float_start")
    try:
        res = _float_start_steps(ctx, services, row, d)
        conn.execute("RELEASE SAVEPOINT xccy_float_start")
    except RejectError as e:
        conn.execute("ROLLBACK TO SAVEPOINT xccy_float_start")
        conn.execute("RELEASE SAVEPOINT xccy_float_start")
        return _skip(ctx, job, key, e.code, f"{cid}: the start exchange (with its linked conversion) could not run: {e.message}; nothing was posted, the linked"
                     " settlement rows wait (retried; never automatic FX)", aid, cid, e.details, severity="ERROR")
    _job_done(ctx, job, key, "DONE", f"start exchange posted (entry {res['entry_id']}){' with the conversion settled' if res.get('conversion') else ''}")
    _resolve(ctx, ("INSUFFICIENT_FUNDS", "INSUFFICIENT_FUNDS_CCY", "XCCY_EVENT_LATE"), key)
    return dict(res, status="DONE", key=key, posted=True)


def _float_start_steps(ctx: TxContext, services, row: dict, d: date) -> dict:
    conn = ctx.conn
    cid, aid = row["contract_id"], row["account_id"]
    terms = _terms(row)
    f = terms["foreign_ccy"]
    rate = qprice(terms["deal_rate"])
    lent, bor = terms["legs"]["receive"], terms["legs"]["pay"]
    lc, bc = lent["currency"], bor["currency"]
    ln, bn = int(lent["notional_c"]), int(bor["notional_c"])
    conv = terms.get("start_conversion") or {}
    linked = bool(conv.get("deal_id"))
    idem = f"xccy:{cid}:start"
    got_in = _settle_linked(ctx, "xccy_start", cid, d, "receive") if linked else None        # 1. the conversion's receivable: USD in
    if linked and getattr(fxmod, "settle_linked", None) is None:
        raise ConflictError("CONVERSION_UNAVAILABLE", "the linked conversion cannot be settled in this build (fx.settle_linked is not available)")
    with swaps.asof_context(ctx, d):
        funding = _fund(ctx, services, aid, lc, ln, "xccy", idem, overdraft=False, commit=True)            # 2. only the net shortfall is swept

        def fxr(ccy: str):
            return None if ccy == "USD" else rate
        lines = [dr(aid, "1360", lc, ln, instrument_id=cid, fx_rate=fxr(lc), memo="notional lent (receive leg; comes back at maturity)"),
                 cr(aid, "1010", lc, ln, fx_rate=fxr(lc), memo="notional paid at the start"),
                 dr(aid, "1010", bc, bn, fx_rate=fxr(bc), memo="notional received at the start"),
                 cr(aid, "2360", bc, bn, instrument_id=cid, fx_rate=fxr(bc), memo="notional borrowed (pay leg; repaid at maturity)")]
        memo = (f"XCCY start exchange {cid}: we pay {lc} {fmt_money(ln)} (lent: the receive leg, back at maturity) and receive {bc} {fmt_money(bn)} (borrowed:"
                f" the pay leg, repaid at maturity) at the deal rate {_px_text(rate)} USD per {f}"
                + (f"; settled payment versus payment with the inception conversion {conv.get('deal_id')}" if linked else "")
                + "; each leg balances in its own currency (SWAP_RESET)")
        entry_id = ctx.post(Entry("XCCY_EXCHANGE", memo, lines, idem, ref_type="otc", ref_id=cid, business_date=d.isoformat()))          # 3. the exchange
    got_out = _settle_linked(ctx, "xccy_start", cid, d, "pay") if linked else None             # 4. the conversion's payable: EUR out
    usd_n = ln if lc == "USD" else bn
    detail = {"we_pay": {"currency": lc, "amount_c": ln, "gl": "1360"}, "we_receive": {"currency": bc, "amount_c": bn, "gl": "2360"}, "rate": str(rate),
              "funding": funding, "entry_id": entry_id, "conversion": {"deal_id": conv.get("deal_id"), "receive": got_in, "pay": got_out} if linked else None}
    conn.execute("INSERT INTO otc_resets(contract_id, event_date, kind, amount_c, detail_json, entry_id) VALUES (?,?,'NOTIONAL_START',?,?,?)",
                 (cid, d.isoformat(), usd_n, dumps(detail), entry_id))
    ctx.touched_instruments.add((aid, cid))
    ctx.event("XCCY_EXCHANGE", account_id=aid, instrument_id=cid, ref_type="otc", ref_id=cid, event="START", date=d.isoformat(), entry_id=entry_id,
              we_pay=detail["we_pay"], we_receive=detail["we_receive"], rate=str(rate), conversion=detail["conversion"])
    return {"entry_id": entry_id, "funding": funding, "we_pay": detail["we_pay"], "we_receive": detail["we_receive"], "rate": str(rate),
            "conversion": detail["conversion"]}


def _float_pay(ctx: TxContext, services, row: dict, d: date, job: Optional[str], key: str, pay_phase: bool = True) -> dict:
    """4.2 PAY (00:45 ET of p_k): both legs accrued through e_k - 1 (else ACCRUALS_PENDING), both indices published (else INDEX_PENDING: quiet on
    its first day, a WARN after), the exact coupons, one true-up entry per leg (idem ``accr:{kind}:{cid}:trueup:{e_k}``), the settlement-funding
    deal's receivable, the payment, its payable, the PAY row.  ``pay_phase`` False (the END) leaves the funding deal's USD leg for after the final
    exchange."""
    conn = ctx.conn
    cid, aid = row["contract_id"], row["account_id"]
    ds = d.isoformat()
    done = conn.execute("SELECT amount_c, detail_json, entry_id FROM otc_resets WHERE contract_id=? AND event_date=? AND kind='PAY'", (cid, ds)).fetchone()
    if done is not None:
        _job_done(ctx, job, key, "DONE", "already paid")
        return {"status": "DONE", "already": True, "posted": False, "key": key, "entry_id": done["entry_id"], "payment": _detail(done)}
    terms = _terms(row)
    legs_def = _float_legs_of(terms)
    periods = legs_def.periods
    k = next((i for i, q in enumerate(periods) if q[2] == d), None)
    if k is None:
        raise RejectError("KEY_INVALID", f"{ds} is not a payment date of {cid}")
    if ctx.ts < epoch_of(d, EVENT_ET):
        return _skip(ctx, job, key, "PAY_NOT_YET", f"{cid}: the payment of {ds} runs from {EVENT_ET} ET", aid, cid, quiet=True)
    if not _has_reset(conn, cid, "NOTIONAL_START"):
        return _skip(ctx, job, key, "START_PENDING", f"{cid}: the start exchange has not happened", aid, cid)
    s, e, _p = periods[k]
    spread = _float_spread(terms)
    legs: Dict[str, dict] = {}
    for side, kind in (("receive", "swaprcv"), ("pay", "swappay")):
        ccy = legs_def.leg_ccy[side]
        st = _state(conn, kind, cid)
        through = st["accrued_through"] if st is not None else None
        last_night = (e - timedelta(days=1)).isoformat()
        if through is None or through < last_night:
            return _skip(ctx, job, key, "ACCRUALS_PENDING", f"{cid}: the {side} leg is accrued through {through or 'nothing'}, the period ends {e.isoformat()}"
                         " (ACCRUE_DAILY first)", aid, cid, {"leg": side, "accrued_through": through, "needed": last_night})
        sp = spread if ccy == legs_def.spread_ccy else Decimal(0)
        try:
            exact, info = float_exact_coupon(services, ccy, legs_def.leg_n_c[side], s, e, sp)
        except ConflictError as ex:
            first_day = ny_of(int(ctx.ts)).date() <= d
            return _skip(ctx, job, key, INDEX_PENDING, f"{cid}: the {FLOAT_INDEX.get(ccy, ccy)} index of {s.isoformat()} or {e.isoformat()} is not published yet"
                         f" ({ex.message}); the payment waits for it - a coupon is never estimated for payment", aid, cid,
                         {"leg": side, "start": s.isoformat(), "end": e.isoformat()}, quiet=first_day)
        spec = swaps.LegSpec(kind, "float", ccy, legs_def.leg_n_c[side], "ACT/360", tuple(periods), "COMPOUND_ARREARS", None, FLOAT_INDEX[ccy], sp)
        posted = swaps._posted_leg_c(conn, aid, cid, spec, (s, e, _p))
        legs[side] = {"kind": kind, "currency": ccy, "gl": spec.gl, "notional_c": legs_def.leg_n_c[side], "amount_c": exact, "posted_c": posted,
                      "trueup_c": exact - posted, "spec": spec, **info}
    entries: List[int] = []
    for side in ("receive", "pay"):                       # 3. the true-ups: the nightly estimate moved to the published index
        lg = legs[side]
        diff = int(lg["trueup_c"])
        if diff:
            memo = (f"XCCY {cid} {lg['currency']} {side} leg {s.isoformat()}..{e.isoformat()}: true-up of the nightly estimate to the published index - exact"
                    f" {fmt_money(lg['amount_c'])} ({lg['formula']}, {lg['index']} factor {lg['factor']}) - posted {fmt_money(lg['posted_c'])} = {fmt_money(diff)}"
                    " (ACCRUAL)")
            entries.append(ctx.post(Entry("ACCRUAL", memo, swaps._leg_lines(aid, cid, lg["spec"], diff), f"accr:{lg['kind']}:{cid}:trueup:{e.isoformat()}",
                                          ref_type="otc", ref_id=cid, business_date=ds)))
    idem = f"xccypay:{cid}:{ds}"
    rl, pl = legs["receive"], legs["pay"]
    got_in = _settle_linked(ctx, "xccy_event", cid, d, "receive")                           # 4. the settlement-funding deal's receivable
    with swaps.asof_context(ctx, d):
        funding = _fund(ctx, services, aid, pl["currency"], pl["amount_c"], "xccypay", idem, overdraft=True)
        lines: List[Line] = []
        if rl["amount_c"]:
            lines += [dr(aid, "1010", rl["currency"], rl["amount_c"], memo="receive leg coupon received"),
                      cr(aid, "1225", rl["currency"], rl["amount_c"], instrument_id=cid, memo="receive leg coupon settled")]
        if pl["amount_c"]:
            lines += [dr(aid, "2340", pl["currency"], pl["amount_c"], instrument_id=cid, memo="pay leg coupon settled"),
                      cr(aid, "1010", pl["currency"], pl["amount_c"], memo="pay leg coupon paid")]
        memo = (f"XCCY payment {cid} {ds}: receive {rl['currency']} {fmt_money(rl['amount_c'])} ({rl['index']} compounded {rl['rate']} x {rl['days']} days"
                f" ACT/360 on {fmt_money(rl['notional_c'])}) / pay {pl['currency']} {fmt_money(pl['amount_c'])} ({pl['index']} compounded {pl['rate']}"
                f"{' + ' + str(Decimal(pl['spread'])) if Decimal(pl['spread']) else ''} x {pl['days']} days ACT/360 on {fmt_money(pl['notional_c'])}); each leg"
                " settles in its own currency (SWAP_RESET)")
        entry_id = ctx.post(Entry("XCCY_PAY", memo, lines, idem, ref_type="otc", ref_id=cid, business_date=ds)) if lines else None
    got_out = _settle_linked(ctx, "xccy_event", cid, d, "pay") if pay_phase else None     # 6. the funding deal's USD leg

    def usd(amount_c: int, ccy: str) -> int:
        return amount_c if ccy == "USD" else to_c(from_c(amount_c) * ctx.fx.rate(ccy))
    net_usd = usd(rl["amount_c"], rl["currency"]) - usd(pl["amount_c"], pl["currency"])
    clean = {side: {k2: v for k2, v in lg.items() if k2 != "spec"} for side, lg in legs.items()}
    detail = {"legs": clean, "net_usd_c": net_usd, "funding": funding, "entry_id": entry_id, "trueup_entries": entries,
              "settlement_funding": {"receive": got_in, "pay": got_out}}
    conn.execute("INSERT INTO otc_resets(contract_id, event_date, kind, amount_c, detail_json, entry_id) VALUES (?,?,'PAY',?,?,?)", (cid, ds, net_usd, dumps(detail), entry_id))
    _job_done(ctx, job, key, "DONE", f"receive {rl['currency']} {fmt_money(rl['amount_c'])}, pay {pl['currency']} {fmt_money(pl['amount_c'])} (index-exact)")
    _resolve(ctx, ("ACCRUALS_PENDING", INDEX_PENDING, "START_PENDING", "XCCY_EVENT_LATE"), key)
    ctx.touched_instruments.add((aid, cid))
    ctx.event("XCCY_PAY", account_id=aid, instrument_id=cid, ref_type="otc", ref_id=cid, date=ds, legs=clean, net_usd_c=net_usd, entry_id=entry_id)
    return {"status": "DONE", "key": key, "posted": entry_id is not None, "entry_id": entry_id, "legs": clean, "net_usd_c": net_usd, "funding": funding,
            "trueup_entries": entries}


def _float_end(ctx: TxContext, services, row: dict, d: date, key: str, job: Optional[str]) -> dict:
    """4.2 END (00:45 ET of the last payment date): the last PAY (its funding deal's receivable first), the reverse exchange (USD lent back, EUR
    borrowed repaid, each in its own currency at this command's FX rate), the funding deal's USD leg after it (payment versus payment), then
    ``swaps.close_contract`` (VM unwound, IM released, MATURED)."""
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
        pay_out = _float_pay(ctx, services, row, d, None, key, pay_phase=False)
        if pay_out.get("status") == "SKIPPED":
            _job_done(ctx, job, key, "SKIPPED", pay_out["note"])
            return pay_out
    terms = _terms(row)
    lc, bc = terms["legs"]["receive"]["currency"], terms["legs"]["pay"]["currency"]
    ln = balance(conn, aid, "1360", lc, cid)
    bn = balance(conn, aid, "2360", bc, cid)
    idem = f"xccy:{cid}:end"
    with swaps.asof_context(ctx, d):
        funding = _fund(ctx, services, aid, bc, bn, "xccy", idem, overdraft=True)
        lines: List[Line] = []
        if ln:
            lines += [dr(aid, "1010", lc, ln, memo="notional lent received back"), cr(aid, "1360", lc, ln, instrument_id=cid, memo="notional receivable settled")]
        if bn:
            lines += [dr(aid, "2360", bc, bn, instrument_id=cid, memo="notional payable settled"), cr(aid, "1010", bc, bn, memo="notional borrowed repaid")]
        memo = (f"XCCY maturity exchange {cid} {ds}: we receive back {lc} {fmt_money(ln)} (lent) and repay {bc} {fmt_money(bn)} (borrowed), each in its own"
                " currency at this command's FX rate (the FX result since the start is realised) (SWAP_RESET)")
        entry_id = ctx.post(Entry("XCCY_EXCHANGE", memo, lines, idem, ref_type="otc", ref_id=cid, business_date=ds)) if lines else None
    got_out = _settle_linked(ctx, "xccy_event", cid, d, "pay")
    detail = {"we_receive": {"currency": lc, "amount_c": ln, "gl": "1360"}, "we_pay": {"currency": bc, "amount_c": bn, "gl": "2360"}, "funding": funding,
              "entry_id": entry_id, "settlement_funding_pay": got_out}
    conn.execute("INSERT INTO otc_resets(contract_id, event_date, kind, amount_c, detail_json, entry_id) VALUES (?,?,'NOTIONAL_END',?,?,?)",
                 (cid, ds, ln if lc == "USD" else bn, dumps(detail), entry_id))
    closed = swaps.close_contract(ctx, services, _contract(conn, cid), "MATURED", 0, _vm_reference(conn, cid), f"otc:{cid}:mature", "OTC_MATURE",
                                  f"XCCY maturity {cid} {ds}: VM balance unwound, IM {fmt_money(int(row['im_c']))} released (SWAP_RESET)", False, True)
    _job_done(ctx, job, key, "DONE", f"matured: receive back {lc} {fmt_money(ln)}, repay {bc} {fmt_money(bn)}")
    _resolve(ctx, ("ACCRUALS_PENDING", INDEX_PENDING, "START_PENDING", "XCCY_EVENT_LATE"), key)
    ctx.touched_instruments.add((aid, cid))
    ctx.event("SWAP_MATURED", account_id=aid, instrument_id=cid, ref_type="otc", ref_id=cid, date=ds, kind="XCCY", entry_id=entry_id,
              **{k2: v for k2, v in closed.items() if k2 not in ("funding", "entry_id")})
    return {"status": "DONE", "key": key, "posted": True, "entry_id": entry_id, "pay": pay_out, "matured": closed, "funding": funding, "exchange": detail}


def _event_late(ctx: TxContext, row: dict, d: date, ev: str, key: str, res: dict) -> dict:
    """D7: a START / PAY / END of a float swap not done by 06:00 ET of its date raises one ERROR XCCY_EVENT_LATE naming the event and its last
    skip reason (the START runbook, 10.8)."""
    if not isinstance(res, dict) or res.get("status") != "SKIPPED" or ctx.ts < epoch_of(d, FLOAT_LATE_ET):
        return res
    cid, aid = row["contract_id"], row["account_id"]
    if ctx.conn.execute("SELECT 1 FROM attention WHERE code='XCCY_EVENT_LATE' AND ref_id=? AND resolved_ts IS NULL", (key,)).fetchone() is None:
        words = {"START": "start exchange (with its linked conversion)", "PAY": "coupon payment", "END": "maturity exchange"}.get(ev, ev.lower())
        msg = (f"{cid}: the {words} of {d.isoformat()} is not done by {FLOAT_LATE_ET} ET - last reason: {res.get('note') or res.get('message') or res.get('code')};"
               " the linked settlement rows wait (nothing is overdrawn); see the START runbook")
        ctx.attention("ERROR", SOURCE, "XCCY_EVENT_LATE", msg, {"account_id": aid, "ref_type": "otc", "ref_id": key, "contract_id": cid, "event": ev,
                                                                 "date": d.isoformat(), "last_code": res.get("code"), "auto_resolve": 1})
        ctx.event("ATTENTION", account_id=aid, ref_type="otc", ref_id=key, code="XCCY_EVENT_LATE", contract_id=cid, event=ev)
    return dict(res, late=True)


# ---- termination of any cross-currency swap (FX_CCS_PLAN D17; both variants) ------------------------------------------------------------
def _fixed_closeout(services, row: dict, terms: dict, snap, at: int) -> Tuple[int, dict, Decimal]:
    """D17 for the fixed / fixed variant: its clean value with the dealer's half-width against the Fund on each leg (a received leg valued at par +
    hw, a paid one at par - hw: the quotes it would take to replace them) at the live FX rate; ``(clean cents, inputs, fx)``."""
    on = ny_of(int(at)).date()
    remaining = [q for q in _periods(terms) if q[2] > on]
    foreign = terms["foreign_ccy"]
    fxv = snap.fx(foreign) if snap is not None else None
    if fxv is None or fxv.mark is None or fxv.quality == "unavailable":
        raise ConflictError("NO_QUOTE", f"no {fx_pair_id(foreign)} rate: the swap cannot be closed out now", {"pair": fx_pair_id(foreign)})
    fx_rate = qprice(fxv.mark)
    hw = Decimal(str(terms.get("half_width") or "0"))
    total = Decimal(0)
    legs = {}
    if remaining:
        for side in ("receive", "pay"):
            leg = terms["legs"][side]
            ccy = leg["currency"]
            notional = int(row["notional_c"]) if ccy == "USD" else int(leg["notional_c"])
            par, annuity, curve = leg_curve(services, ccy, len(remaining))
            against = (qprice(par) + hw) if side == "receive" else (qprice(par) - hw)
            sign = ONE if side == "receive" else -ONE
            fx = ONE if ccy == "USD" else fx_rate
            v = leg_value(sign, notional, leg["rate"], against, annuity, fx)
            total += v
            legs[side] = {"currency": ccy, "par_rate": str(par), "closeout_rate": str(against), "annuity": str(annuity), "value_c": to_c(v)}
    return to_c(total), {"fx": {"pair": fx_pair_id(foreign), "usd_per_unit": str(fx_rate), "quality": fxv.quality, "source": fxv.source,
                                "quote_time": fxv.quote_time}, "legs": legs, "half_width": str(hw), "model": XCCY_MODEL}, fx_rate


def _terminate_xccy(ctx: TxContext, services, row: dict) -> Tuple[dict, int, dict, List[int]]:
    """D17 (FX_CCS_PLAN rev 2; both variants): before the start with a linked inception conversion -> 409 XCCY_NOT_STARTED, nothing written;
    otherwise cash-settled in USD at the FULL value with the dealer's half-width against the Fund (float: the basis level moved by the half-width,
    down for the payer of the EUR leg; fixed / fixed: each leg at the quote that would replace it) - the accruals caught up first, then ONE entry
    clears the notionals and accrued coupons (1360 / 2360 / 1225 / 2340) through 3900 at the FX rate now with their USD value in cash (never EUR),
    and ``swaps.close_contract`` posts the clean close-out value to 4450, unwinds the VM and releases the IM.  Linked funding deals already booked
    stay (trades) and settle on their own value dates."""
    conn = ctx.conn
    cid, aid = row["contract_id"], row["account_id"]
    terms = _terms(row)
    float_ = _is_float(terms)
    started = _has_reset(conn, cid, "NOTIONAL_START")
    conv = terms.get("start_conversion") or {}
    if not started and float_ and conv.get("deal_id"):
        s = row["start_date"]
        raise ConflictError("XCCY_NOT_STARTED", f"{cid}: the start exchange and its conversion settle together on {s}; terminate from {s} 00:45 ET - nothing was"
                            " written", {"contract_id": cid, "start_date": s, "conversion_deal_id": conv.get("deal_id")})
    today = ny_of(ctx.ts).date()
    entries: List[int] = []
    if started:
        last_night_end = _date(terms.get("termination_date") or row["maturity_date"])
        through = min(today, last_night_end) - timedelta(days=1)
        entries += _catch_up(ctx, services, row, ("swaprcv", "swappay"), through)
    row = _contract(conn, cid)
    if float_:
        eur_side = terms.get("eur_side") or ("pay" if terms["legs"]["pay"]["currency"] != "USD" else "receive")
        hw_eff = Decimal(str(terms.get("half_width_bp") or "0")) * Decimal(str(terms.get("otc_spread_mult") or "1"))
        shift = -hw_eff if eur_side == "pay" else hw_eff
        got = _float_mark(services, row, terms, ctx.ts, snapshot=ctx.marks, level_shift_bp=shift)
        if not isinstance(got, tuple) or got[0] is None:
            note = getattr(got, "note", None) or (got[1].get("note") if isinstance(got, tuple) and isinstance(got[1], dict) else None) or "no value"
            raise ConflictError("NO_CURVE", f"{cid}: {note}: a termination needs the swap's full value now - nothing was written", {"contract_id": cid})
        clean_c, inputs = int(got[0]), dict(got[1])
        X = Decimal(str(inputs["fx"]["usd_per_unit"]))
    else:
        clean_c, inputs, X = _fixed_closeout(services, row, terms, ctx.marks, ctx.ts)
    # the balance sheet of the contract, cleared at X through 3900; its USD value is paid / received in cash
    bal = {}
    for gl in ("1360", "1225", "2360", "2340"):
        for ccy in sorted({terms["legs"]["receive"]["currency"], terms["legs"]["pay"]["currency"]}):
            b = balance(conn, aid, gl, ccy, cid)
            if b:
                bal[(gl, ccy)] = b
    lines: List[Line] = []
    b_usd = 0
    usd_of: Dict[str, int] = {}
    for (gl, ccy), b in sorted(bal.items()):
        asset = gl in ("1360", "1225")
        if ccy == "USD":
            lines.append(cr(aid, gl, "USD", b, instrument_id=cid, memo="cleared at the termination") if asset else
                         dr(aid, gl, "USD", b, instrument_id=cid, memo="cleared at the termination"))
            b_usd += b if asset else -b
        else:
            lines.append(cr(aid, gl, ccy, b, instrument_id=cid, fx_rate=X, memo=f"cleared at the termination at {X}") if asset else
                         dr(aid, gl, ccy, b, instrument_id=cid, fx_rate=X, memo=f"cleared at the termination at {X}"))
            usd_of[ccy] = usd_of.get(ccy, 0) + (b if asset else -b)
    for ccy, net in sorted(usd_of.items()):              # the foreign lines through 3900 at X: the USD side carries their value (D17)
        if not net:
            continue
        parts = [b for (gl, c), b in bal.items() if c == ccy]
        lines.append(dr(aid, "3900", ccy, net, fx_rate=X, memo=f"{ccy} cleared through FX conversion clearing") if net > 0 else
                     cr(aid, "3900", ccy, -net, fx_rate=X, memo=f"{ccy} cleared through FX conversion clearing"))
        usd_val = sum((fx_c(b, X) * (1 if gl in ("1360", "1225") else -1)) for (gl, c), b in bal.items() if c == ccy)
        lines.append(cr(aid, "3900", "USD", usd_val, memo=f"USD value of the {ccy} cleared at {X}") if usd_val > 0 else
                     dr(aid, "3900", "USD", -usd_val, memo=f"USD value of the {ccy} cleared at {X}"))
        b_usd += usd_val
        del parts
    total_cash_needed = 0
    vb = int(row["vm_balance_c"] or 0) if row["margin_mode"] == "CTM" else 0
    unwind_cash = -vb if row["margin_mode"] == "CTM" else 0      # VM posted (vb < 0) comes back, VM received (vb > 0) goes back
    ref_c = _vm_reference(conn, cid)
    result_cash = clean_c if row["margin_mode"] == "CTM" else clean_c - ref_c
    total_cash = b_usd + unwind_cash + result_cash
    funding = None
    if total_cash < 0:                                          # one funding for the whole close-out: own cash, then one Treasury sweep (USD only)
        total_cash_needed = -total_cash
        link_type, link_id = ("xccy", f"xccy:{cid}:terminate") if lines else ("otc", f"otc:{cid}:terminate")    # the entry this command posts (L19)
        funding = swaps.fund_commitment(ctx, services, aid, "USD", total_cash_needed, link_type, link_id, csa_netting=True)
    clear_id = None
    if lines:
        if b_usd > 0:
            lines.append(dr(aid, "1010", "USD", b_usd, memo="the notionals' and accrued coupons' USD value received"))
        elif b_usd < 0:
            lines.append(cr(aid, "1010", "USD", -b_usd, memo="the notionals' and accrued coupons' USD value paid"))
        memo = (f"XCCY termination {cid}: the notionals and accrued coupons on the balance sheet are cleared through 3900 at {X} (no physical re-exchange,"
                f" no {', '.join(sorted(usd_of)) or 'foreign currency'} moves); their USD value {fmt_money(b_usd)} settles in cash (D17)")
        clear_id = ctx.post(Entry("XCCY_EXCHANGE", memo, lines, f"xccy:{cid}:terminate", ref_type="otc", ref_id=cid))
        entries.append(clear_id)
        conn.execute("INSERT INTO otc_resets(contract_id, event_date, kind, amount_c, detail_json, entry_id) VALUES (?,?,'NOTIONAL_END',?,?,?)",
                     (cid, today.isoformat(), b_usd, dumps({"terminated": True, "cleared": {f"{gl}:{c}": b for (gl, c), b in bal.items()}, "rate": str(X),
                                                             "usd_value_c": b_usd, "entry_id": clear_id, "cash_settled": True}), clear_id))
    full_c = clean_c + b_usd
    memo = (f"XCCY termination {cid}: full value {fmt_money(full_c)} with the dealer's half-width against the Fund = clean {fmt_money(clean_c)} + balance"
            f" sheet {fmt_money(b_usd)}, cash-settled in USD (D17); VM balance {fmt_money(int(row['vm_balance_c']))} unwound; IM released")
    closed = swaps.close_contract(ctx, services, _contract(conn, cid), "TERMINATED", clean_c, ref_c, f"otc:{cid}:terminate", "OTC_TERMINATE", memo, False, False)
    pending = [d for d in swaps.linked_deals(conn, row) if d.get("status") == "PENDING"]
    out_inputs = dict(inputs, full_value_c=full_c, clean_c=clean_c, balance_sheet_c=b_usd, clearing_entry_id=clear_id, vm_reference_c=ref_c,
                      cash_needed_c=total_cash_needed, funding=funding.to_dict() if hasattr(funding, "to_dict") else funding,
                      linked_deals_pending=[{"deal_id": d["deal_id"], "value_date": d["value_date"], "what": d["what"]} for d in pending])
    if pending:
        out_inputs["linked_note"] = ("linked FX deals already booked stay (they are trades) and settle on their own value dates: "
                                     + ", ".join(f"{d['deal_id']} value {d['value_date']}" for d in pending))
    return closed, full_c, out_inputs, entries


# ---- the float variant's flows, risk, events and detail ---------------------------------------------------------------------------------
def _float_flows(conn: sqlite3.Connection, row: dict, terms: dict, services=None) -> List[dict]:
    """K5 ``scheduled_flows`` of a float swap: the start exchange (until it ran), each unpaid coupon (``known`` with the exact amount once the
    period has ended and both indices are published - a projection otherwise, ``known`` false) and the final exchange (until it ran).  The amounts
    of a coupon not known yet are projected on the curves of the trade date in the pricing record (never payable)."""
    cid, aid = row["contract_id"], row["account_id"]
    legs = _float_legs_of(terms)
    periods = legs.periods
    out: List[dict] = []
    started = _has_reset(conn, cid, "NOTIONAL_START")
    if not started:
        for side, sign in (("receive", -1), ("pay", 1)):
            out.append(_flow(periods[0][0], legs.leg_ccy[side], sign * legs.leg_n_c[side],
                             f"start exchange: notional {'lent' if side == 'receive' else 'borrowed'}", "START", flow_kind="exchange"))
    paid = {r["event_date"] for r in conn.execute("SELECT event_date FROM otc_resets WHERE contract_id=? AND kind='PAY'", (cid,)).fetchall()}
    rec = terms.get("pricing_record") or {}
    sched = {r.get("p"): r for r in (rec.get("schedule") or [])}
    f = legs.spread_ccy
    for k, (s, e, p) in enumerate(periods):
        if p.isoformat() in paid:
            continue
        for side, sign in (("receive", 1), ("pay", -1)):
            ccy = legs.leg_ccy[side]
            kind = "swaprcv" if side == "receive" else "swappay"
            r = sched.get(p.isoformat()) or {}
            proj = r.get("usd_indicative_c") if ccy == "USD" else r.get(f"{f.lower()}_indicative_c")
            amount, known = (None if proj is None else sign * int(proj)), False
            st = _state(conn, kind, cid)
            if st is not None and st["segment_start"] and st["segment_start"] > s.isoformat():
                try:                                       # the period has ended: the index decides (known once both indices are published)
                    exact, _info = float_exact_coupon(services if services is not None else _BOUND.get("services"), ccy, legs.leg_n_c[side], s, e,
                                                      _float_spread(terms) if ccy == f else Decimal(0))
                    amount, known = sign * exact, True
                except Exception:   # noqa: BLE001 - not published yet / not built: the projection, not known
                    pass
            out.append(_flow(p, ccy, amount, f"{side} leg coupon {s.isoformat()}..{e.isoformat()} ({FLOAT_INDEX[ccy]}"
                             f"{' ' + _bp_text(Decimal(str((terms.get('spread') or {}).get('dealt_bp') or '0'))) if ccy == f else ' flat'}"
                             f"{'' if known else ', projected: the index decides'})", "PAY" if k < len(periods) - 1 else "END", known=known,
                             flow_kind="coupon"))
    if not _has_reset(conn, cid, "NOTIONAL_END"):
        last = periods[-1][2]
        for side, sign in (("receive", 1), ("pay", -1)):
            out.append(_flow(last, legs.leg_ccy[side], sign * legs.leg_n_c[side],
                             f"final exchange: notional {'lent comes back' if side == 'receive' else 'borrowed repaid'}", "END", flow_kind="exchange"))
    out.sort(key=lambda fl: (fl["date"], {"START": 0, "PAY": 1, "END": 2}.get(fl["event"], 3)))
    return out


#: the services bundle ``register`` saw: ``scheduled_flows(conn, row)`` (K5 has no services argument) reads the published indices through it
_BOUND: Dict[str, object] = {}


def _float_risk(services, row: dict, terms: dict, snapshot=None) -> dict:
    """K5 / D14 ``risk_sensitivities`` of a float swap - ONLY the off-ledger part: ``fx_delta`` = the clean mark's FX delta in foreign units (the
    full value's foreign flows plus the foreign balance-sheet items, which the clean mark leaves to the ledger), ``fx_nominal`` = the exchanges not
    on the ledger yet (before the start both exchanges, which net to 0; after it none), ``rate01_c`` / ``basis01_c`` by re-pricing (+1 bp)."""
    cid = row["contract_id"]
    f = terms["foreign_ccy"]
    pair = fx_pair_id(f)
    notes = ["off-ledger part only: the notionals (1360 / 2360) and accrued coupons (1225 / 2340) are in the ledger's currency balances"]
    snap = snapshot if snapshot is not None else _snapshot(services, [pair])
    out = {"fx_delta": {}, "fx_nominal": {}, "rate01_c": {}, "basis01_c": {}, "eur_ois_spread01_c": None, "notes": notes, "contract_id": cid,
           "off_ledger_only": True}
    try:
        fxv = snap.fx(f) if snap is not None else None
        if fxv is None or fxv.mark is None or fxv.quality == "unavailable":
            raise ConflictError("NO_QUOTE", f"no {pair} rate")
        X = qprice(fxv.mark)
        cc = _ccycal()
        on = fxmod.fx_trade_date(services, int(services.clock.epoch()))
        t_spot = cc.spot_date(services, pair, on)
        with _marks.shared_reader(services.db) as conn:
            acct = _rowd(account_row(conn, row["account_id"]))
            market = float_market(services, conn, acct, on)
            state = float_state(services, conn, row, terms)
            legs = _float_legs_of(terms)
            b = _float_spread(terms)
            proj, disc = market.zc()
            with localcontext(P34):
                full, _u, f_part = float_full_value(legs, b, X, t_spot, proj, disc, state)
                bal_f = Decimal(0)
                for gl, sgn in (("1360", ONE), ("1225", ONE), ("2360", -ONE), ("2340", -ONE)):
                    bal_f += sgn * from_c(balance(conn, row["account_id"], gl, f, cid))

                def rp(**kw):
                    pj, ds = market.zc(**kw)
                    return float_full_value(legs, b, X, t_spot, pj, ds, state)[0]
                out["fx_delta"] = {f: to_c(f_part - bal_f)}
                out["rate01_c"] = {"USD": to_c(rp(bu=1) - full), f: to_c(rp(be=1) - full)}
                out["basis01_c"] = {f"{f}USD": to_c(rp(mid=1) - full)}
                out["eur_ois_spread01_c"] = to_c(rp(grid=1) - full)
            if not state.started:
                out["fx_nominal"] = {legs.leg_ccy["receive"]: 0, legs.leg_ccy["pay"]: 0}
                notes.append("before the start exchange: the start and the final exchange net to 0 in each currency (no notional reset)")
            out.update(valuation_date=on.isoformat(), spot_date=t_spot.isoformat(), fx=str(X))
    except (ConflictError, RejectError) as e:
        notes.append(f"unavailable: {e.message}")
    return out


def _float_next_events(services, conn, row: dict, terms: dict, today: date) -> List[dict]:
    legs = _float_legs_of(terms)
    periods = legs.periods
    cid = row["contract_id"]
    f = legs.spread_ccy
    paid = {r["event_date"] for r in conn.execute("SELECT event_date FROM otc_resets WHERE contract_id=? AND kind='PAY'", (cid,)).fetchall()}
    rec = terms.get("pricing_record") or {}
    sched = {r.get("p"): r for r in (rec.get("schedule") or [])}
    events: List[dict] = []
    if periods and not _has_reset(conn, cid, "NOTIONAL_START"):
        lent, bor = terms["legs"]["receive"], terms["legs"]["pay"]
        conv = terms.get("start_conversion") or {}
        events.append({"date": periods[0][0].isoformat(), "event": "START",
                       "detail": (f"start exchange: we pay {lent['currency']} {fmt_money(int(lent['notional_c']))} (lent) and receive {bor['currency']}"
                                  f" {fmt_money(int(bor['notional_c']))} (borrowed) at {terms.get('deal_rate')}"
                                  + (f", settled with the inception conversion {conv.get('deal_id')} (payment versus payment)" if conv.get("deal_id") else ""))})
    for k, (s, e, p) in enumerate(periods):
        ps = p.isoformat()
        if p < today or ps in paid:
            continue
        r = sched.get(ps) or {}
        lg = []
        for side in ("receive", "pay"):
            ccy = legs.leg_ccy[side]
            proj = r.get("usd_indicative_c") if ccy == "USD" else r.get(f"{f.lower()}_indicative_c")
            lg.append({"leg": "float", "side": side, "currency": ccy, "index": FLOAT_INDEX[ccy], "amount_c": proj, "known": False, "indicative": True,
                       "period": [s.isoformat(), e.isoformat()], "note": "indicative until the index is published at the period end"})
        events.append({"date": ps, "event": "MATURITY" if k == len(periods) - 1 else "PAY", "legs": lg,
                       **({"detail": "last coupons with the final exchange; VM unwound and IM released"} if k == len(periods) - 1 else {})})
    events.sort(key=lambda x: (x["date"], {"START": 0, "PAY": 1, "MATURITY": 2}.get(x["event"], 3)))
    return events


def _float_detail(services, conn, row: dict, terms: dict) -> dict:
    """The float swap's drawer: legs with their current compounding, true-ups, the payments, the linked deals and the confirmation with the
    settlement instructions (10.7)."""
    cid, aid = row["contract_id"], row["account_id"]
    f = terms["foreign_ccy"]
    legs_def = _float_legs_of(terms)
    today = ny_of(services.clock.epoch()).date()
    legs = {}
    for side, kind in (("receive", "swaprcv"), ("pay", "swappay")):
        ccy = legs_def.leg_ccy[side]
        st = _state(conn, kind, cid)
        comp = None
        if st is not None and st["accrued_through"]:
            seg = _date(st["segment_start"])
            nights = (_date(st["accrued_through"]) - seg).days + 1
            fac = Decimal(str(st["rate"]))
            comp = {"period_start": seg.isoformat(), "accrued_through": st["accrued_through"], "nights": nights, "factor": str(fac),
                    "accrued_c": int(st["cum_c"]), "rate_so_far": str(((fac - ONE) * Decimal(360) / Decimal(nights)).quantize(Decimal("0.00000001"))) if nights else None,
                    "note": "a nightly estimate at the latest print; each payment trues it up to the published index"}
        legs[side] = {"currency": ccy, "index": FLOAT_INDEX[ccy], "notional_c": legs_def.leg_n_c[side], "spread_bp": terms["legs"][side].get("spread_bp"),
                      "basis": "ACT/360", "accrued_c": balance(conn, aid, "1225" if side == "receive" else "2340", ccy, cid),
                      "notional_balance_c": balance(conn, aid, "1360" if side == "receive" else "2360", ccy, cid),
                      "role": "lent (1360)" if side == "receive" else "borrowed (2360)", "compounding": comp, "method": FLOAT_COMPOUNDING[FLOAT_INDEX[ccy]]}
    pays = []
    for r in conn.execute("SELECT event_date, amount_c, detail_json, entry_id FROM otc_resets WHERE contract_id=? AND kind='PAY' ORDER BY event_date", (cid,)).fetchall():
        dd = _detail(r)
        pays.append({"date": r["event_date"], "net_usd_c": int(r["amount_c"]), "entry_id": r["entry_id"],
                     "legs": {s: {k: (dd.get("legs") or {}).get(s, {}).get(k) for k in ("currency", "amount_c", "posted_c", "trueup_c", "rate", "factor", "days")}
                              for s in ("receive", "pay")}})
    side_text = _float_side_text(f, terms.get("eur_side") or "pay", Decimal(str((terms.get("spread") or {}).get("dealt_bp") or "0")))
    return {"kind": "XCCY", "variant": FLOAT_VARIANT, "pay_ccy": terms.get("pay_ccy"), "recv_ccy": terms.get("recv_ccy"), "deal_rate": terms.get("deal_rate"),
            "deal_pair": terms.get("deal_pair"), "mtm_reset": False, "side_text": side_text, "legs": legs, "payments": pays,
            "spread": terms.get("spread"), "trade_date": terms.get("trade_date"), "start_date": row["start_date"], "termination_date": terms.get("termination_date"),
            "maturity_date": row["maturity_date"], "started": _has_reset(conn, cid, "NOTIONAL_START"), "matured_exchange": _has_reset(conn, cid, "NOTIONAL_END"),
            "start_conversion": terms.get("start_conversion"), "settlement_funding": terms.get("settlement_funding"),
            "linked_deals": swaps.linked_deals(conn, row), "confirmation": _float_confirmation(row, terms, today),
            "pricing_record": terms.get("pricing_record"), "fx_resets": [], "today": today.isoformat()}


def _float_confirmation(row: dict, terms: dict, today: date) -> dict:
    """10.7 the confirmation block: parties, dates, amounts, rate, CSA, IM and the settlement instructions (simulator accounts); "CONFIRMED -
    simulated confirmation" (never "dealer-confirmed")."""
    f = terms["foreign_ccy"]
    broker = str(row.get("broker") or "")
    dealer = config.PRIME_BROKERS.get(broker.upper(), broker or "the dealer")
    legs = terms["legs"]
    n_f = int(legs["pay" if (terms.get("eur_side") or "pay") == "pay" else "receive"]["notional_c"])
    n_u = int(row["notional_c"])
    dealt = Decimal(str((terms.get("spread") or {}).get("dealt_bp") or "0"))
    conv = terms.get("start_conversion") or {}
    s, m = row["start_date"], row["maturity_date"]
    status = swaps._row_get(row, "confirm_status")
    booking = swaps.booking_of(row) or None
    instr = [f"initial exchange, value {s}: {dealer} pays {f} {fmt_money(n_f)}; the Fund pays USD {fmt_money(n_u)} at {terms.get('deal_rate')}"
             + (f"; settled payment versus payment with the inception conversion {conv.get('deal_id')}: the Fund delivers {f} {fmt_money(n_f)} to the FX desk"
                f" for USD at {conv.get('rate')}; the net USD ({fmt_money(int((conv.get('buy') or {}).get('amount_c') or 0) - n_u)}) comes from the account,"
                " else Treasury" if conv.get("deal_id") else ""),
             f"coupons on each payment date: the Fund pays {f} on {FLOAT_INDEX[f]} compounded {_bp_text(dealt)}; {dealer} pays USD on SOFR compounded; ACT/360"
             + ("; the EUR is bought under the settlement-funding instruction at each period end, value the payment date"
                if (terms.get("settlement_funding") or {}).get(f) == "BUY_WITH_USD" else ""),
             f"final exchange, value {m}, together with the last coupons: the Fund pays {f} {fmt_money(n_f)}; {dealer} pays USD {fmt_money(n_u)}",
             "termination: cash-settled in USD at the full value with the half-width against the Fund; refused before the start while the conversion is linked",
             f"variation margin daily in USD (CTM) on the CSA exposure; initial margin in USD held by {dealer}",
             f"accounts: {dealer} is the counterparty; the Fund's cash accounts are its {dealer} prime-brokerage cash in each currency ({row['account_id']},"
             " simulator accounts)"]
    return {"status": status, "label": swaps.confirm_label(booking, status) or swaps.NO_CONFIRM_LABEL,
            "parties": {"fund": row["account_id"], "counterparty": dealer, "role": "prime broker and OTC dealer (simulated)"},
            "product": f"{fx_pair_id(f)[:3]}/{fx_pair_id(f)[3:]} float / float cross-currency swap (ESTR / SOFR), no notional reset",
            "trade_date": terms.get("trade_date"), "start_date": s, "termination_date": terms.get("termination_date"), "maturity_date": m,
            # V1061-05: cents under *_c keys, so the API codec serves amounts ({"eur": "2000000.00", "usd": ...}), never bare cent integers
            "notionals": {f"{f.lower()}_c": n_f, "usd_c": n_u}, "deal_rate": terms.get("deal_rate"),
            "spread": f"{FLOAT_INDEX[f]} {_bp_text(dealt)} on the {f} leg",
            "client_ref": swaps._row_get(row, "client_ref"), "im_c": int(row.get("im_c") or 0), "csa": f"the existing CSA with {dealer} (CTM)",
            "settlement_instructions": instr, "today": today.isoformat()}


# ---- FX_CCS_PLAN K5 contract hooks (1.0.6.1): scheduled flows and the off-ledger risk part ---------------------------------------------
def _flow(d, ccy: str, amount_c: Optional[int], what: str, event: str, known: bool = True, **extra) -> dict:
    """One scheduled flow (K5).  ``flow_kind`` ("exchange" / "coupon") is given explicitly by every caller (P-D-R3: ``fx.funding_check``
    counts exchanges and deliveries only, never by the words); the words decide only for a caller that gives none."""
    kind = extra.pop("flow_kind", None) or ("coupon" if "coupon" in what else "exchange")
    return dict({"date": d.isoformat() if hasattr(d, "isoformat") else str(d), "ccy": ccy, "amount_c": amount_c, "what": what, "event": event,
                 "known": bool(known and amount_c is not None), "flow_kind": kind}, **extra)


def scheduled_flows(conn: sqlite3.Connection, row, services=None) -> List[dict]:
    """K5: the contract's scheduled flows not yet on the ledger, ``[{date, ccy, amount_c (+ in / - out), what, event, known}]`` in date order -
    an XCCY's start exchange (until it has run), its coupons (each leg in its own currency; a floating coupon is ``known`` only once its
    index is published, until then its projection is ``amount_c`` with ``known`` false) and its final exchange (until it has run).  A TRS
    settles at its resets (none listed).  Pure; ``fx.funding_check`` and ``ccyfunding.plan`` read it."""
    r = _rowd(row) if not isinstance(row, dict) else dict(row)
    if r is None or r.get("kind") != "XCCY" or r.get("status") != "OPEN":
        return []
    if "terms_json" not in r:
        r = _rowd(conn.execute("SELECT * FROM otc_contracts WHERE contract_id=?", (r.get("contract_id"),)).fetchone()) or {}
        if not r:
            return []
    terms = _terms(r)
    if terms.get("variant") == FLOAT_VARIANT:
        return _float_flows(conn, r, terms, services)
    cid = r["contract_id"]
    periods = _periods(terms)
    if not periods:
        return []
    lent, bor = terms["legs"]["receive"], terms["legs"]["pay"]
    out: List[dict] = []
    started = _has_reset(conn, cid, "NOTIONAL_START")
    if not started:
        ln = _leg_notional(conn, r, terms, periods, "receive", 0)
        bn = _leg_notional(conn, r, terms, periods, "pay", 0)
        out.append(_flow(periods[0][0], lent["currency"], None if ln is None else -int(ln), "start exchange: notional lent", "START", flow_kind="exchange"))
        out.append(_flow(periods[0][0], bor["currency"], None if bn is None else int(bn), "start exchange: notional borrowed", "START", flow_kind="exchange"))
    paid = {x["event_date"] for x in conn.execute("SELECT event_date FROM otc_resets WHERE contract_id=? AND kind='PAY'", (cid,)).fetchall()}
    for k, (s, e, p) in enumerate(periods):
        if p.isoformat() in paid:
            continue
        for side, sign in (("receive", 1), ("pay", -1)):
            leg = terms["legs"][side]
            n = _leg_notional(conn, r, terms, periods, side, k)
            amount = None if n is None else sign * swaps.coupon_c(n, qprice(leg["rate"]), s, e, leg["basis"])
            out.append(_flow(p, leg["currency"], amount, f"{side} leg coupon {s.isoformat()}..{e.isoformat()} (fixed {_pct(leg['rate'])})", "PAY" if k < len(periods) - 1 else "END",
                             flow_kind="coupon"))
    if not _has_reset(conn, cid, "NOTIONAL_END"):
        last = periods[-1][2]
        ln = _leg_notional(conn, r, terms, periods, "receive", len(periods) - 1)
        bn = _leg_notional(conn, r, terms, periods, "pay", len(periods) - 1)
        out.append(_flow(last, lent["currency"], None if ln is None else int(ln), "final exchange: notional lent comes back", "END", flow_kind="exchange"))
        out.append(_flow(last, bor["currency"], None if bn is None else -int(bn), "final exchange: notional borrowed repaid", "END", flow_kind="exchange"))
    out.sort(key=lambda f: (f["date"], {"START": 0, "PAY": 1, "END": 2}.get(f["event"], 3)))
    return out


def risk_sensitivities(services, row, snapshot=None) -> dict:
    """K5 / D14: ONLY the off-ledger part of the contract's currency risk - the ledger part (1360 / 2360 notionals, 1225 / 2340 accruals) is
    ``risk._currency_balances``' and is never repeated here.  ``fx_delta`` {ccy: units_c} = the clean mark's FX delta in currency units
    (the mark is linear in the FX rate, so this is exact re-pricing); ``fx_nominal`` {ccy: units_c} = the exchanges not yet on the ledger
    (before the start exchange both exchanges, which net to 0 without a notional reset; after it none - 1360 / 2360 hold the final one);
    ``rate01_c`` {ccy: usd_c} the USD value of +1 bp on each currency's curve; ``basis01_c`` {pair: usd_c}; ``notes``.  None for another kind."""
    r = _rowd(row) if not isinstance(row, dict) else dict(row)
    if r is None or r.get("kind") != "XCCY":
        return None
    if "terms_json" not in r:
        r = _contract_of(services, r) or {}
    if r.get("status") != "OPEN":
        return {"fx_delta": {}, "fx_nominal": {}, "rate01_c": {}, "basis01_c": {}, "notes": [f"{r.get('contract_id')} is {r.get('status')}: no risk"]}
    terms = _terms(r)
    if terms.get("variant") == FLOAT_VARIANT:
        return _float_risk(services, r, terms, snapshot)
    cid = r["contract_id"]
    with _marks.shared_reader(services.db) as conn:
        flows = [f for f in scheduled_flows(conn, r) if f["event"] == "START" or (f["event"] == "END" and f["what"].startswith("final exchange")
                                                                                     and not _has_reset(conn, cid, "NOTIONAL_START"))]
    nominal: Dict[str, int] = {}
    for f in flows:
        if f["amount_c"] is not None:
            nominal[f["ccy"]] = nominal.get(f["ccy"], 0) + int(f["amount_c"])
    on = ny_of(int(services.clock.epoch())).date()
    remaining = [q for q in _periods(terms) if q[2] > on]
    notes = ["fixed / fixed model: each leg on its own curve (USD par curve, the foreign leg flat at its policy rate); no basis level"]
    fx_delta: Dict[str, int] = {}
    rate01: Dict[str, int] = {}
    if remaining:
        try:
            snap = snapshot if snapshot is not None else _snapshot(services, [fx_pair_id(terms["foreign_ccy"])])
            _v, inputs = _xccy_value(services, r, terms, len(remaining), snap)
            fx_rate = Decimal(str(inputs["fx"]["usd_per_unit"]))
            for side in ("receive", "pay"):
                leg = inputs["legs"][side]
                ccy = leg["currency"]
                rate01[ccy] = rate01.get(ccy, 0) + int(leg["dv01_c"])
                if ccy != "USD" and fx_rate:
                    fx_delta[ccy] = fx_delta.get(ccy, 0) + to_c(from_c(int(leg["value_c"])) / fx_rate)
        except ConflictError as e:
            notes.append(f"unavailable: {e.message}")
    return {"fx_delta": fx_delta, "fx_nominal": nominal, "rate01_c": rate01, "basis01_c": {}, "notes": notes, "contract_id": cid, "off_ledger_only": True}


# ---- registry ---------------------------------------------------------------------------------------------------------------------
def register(bus, services=None) -> None:
    """Register ``trs.reset``, ``xccy.exchange`` and ``xccy.fx_reset`` (lifecycle); the runtime binds this module as
    ``services.swaps_trs`` so WP-17's hooks, ACCRUE_DAILY (``accrue_night`` for trsfin) and the TRS_RESET / XCCY_EVENTS
    ProductJobs (``reset_keys`` / ``event_keys``) reach it."""
    bus.register("trs.reset", h_reset)
    bus.register("xccy.exchange", h_exchange)
    bus.register("xccy.fx_reset", h_fx_reset)
    if services is not None:
        _BOUND["services"] = services


__all__ = ["preview", "book", "terminate", "mark_trs", "mark_xccy", "dv01_c", "accrue_night", "pay", "fix", "next_events", "contract_detail", "reset_keys",
           "event_keys", "register", "h_reset", "h_exchange", "h_fx_reset", "parse_trs_terms", "parse_xccy_terms", "TrsTerms", "TrsDeal", "XLeg", "XccyDeal",
           "fin_rate", "flat_dfs", "foreign_curve", "leg_curve", "leg_value", "leg_dv01", "usd_notional", "xccy_leg_spec", "close_on", "fx_close_on",
           "CONVENTIONS", "TRS_MODEL", "XCCY_MODEL", "TRS_IM_PCT", "XCCY_IM_PCT", "RESET_ET", "EVENT_ET", "FX_RESET_ET", "JOB_RESET", "JOB_EVENTS",
           "MARKS_HOOK", "RecordingMarks", "PinnedMarks", "mark_view_of", "xccy_dates", "xccy_start_check", "next_event_due", "unsupported",
           "scheduled_flows", "risk_sensitivities", "FLOAT_VARIANT", "parse_float_terms", "float_full_value", "float_dates", "float_market", "float_state",
           "float_exact_coupon", "float_start_check", "FloatLegs", "FloatState", "FloatMarket", "FloatDeal", "FLOAT_INDEX", "FLOAT_METHOD", "FLOAT_MODEL",
           "MODEL_VERSION", "INDEX_PENDING", "STALE_LISTING_SESSIONS", "last_trade", "last_trade_age_sessions", "stale_listing_check"]
