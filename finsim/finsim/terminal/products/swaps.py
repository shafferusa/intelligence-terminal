"""PB OTC swaps — the IRS core: dealing preview, booking with initial margin, computed clean-PV marks and signed DV01,
daily variation margin (collateralised-to-market or settled-to-market), coupon accruals (fixed, TERM SOFR, SOFR compounded
in arrears), floating fixings, net payments, maturity, termination, the lifecycle key helpers, the contract views and the
hooks the TRS / cross-currency package extends (TERMINAL_DESIGN.md §2.12 products/swaps.py, §4.1, §5.4, §11.2 SWAP_FIX /
SWAP_PAY / SWAP_VM, §11.3, §11.6, §12.4, §13.11, §19.15; TERMINAL_WORKPLAN.md WP-17; the owner amendments of 2026-10-20
17:03 "variation margin on swaps too" and 17:07 "swap variation margin AND resets").

Conventions stated here (each is a §1.3 sentence or a rule the tests pin)
- ELIGIBILITY / NOT_TRADEABLE_HERE: a swap is booked only in a prime-broker account (kind PB) of a lifetime that passes
  ``books.require_pb_otc`` (the PB/OTC threshold or an existing relationship, AND the ECP floor).  The OTC kind is checked
  first (``prime.require_otc_kind``: OTC equity / FX options and forwards are live through ``services.otc_options``; FRAs, caps,
  floors, swaptions and the generic names OPTION / FORWARD answer 400 NOT_IN_THIS_BUILD, anything else 400 OTC_KIND), then the account (a non-PB account answers 400 NOT_TRADEABLE_HERE, an unknown one 404), then the lifetime
  (400 NOT_ELIGIBLE) — for every kind; TRS / XCCY are then dispatched to ``services.swaps_trs`` (WP-26, bound in every
  served process since the WP-15 integration; a runtime without it answers 501 NOT_BUILT).  Only USD / SOFR is in this build (other currencies: 400 CURRENCY_UNSUPPORTED —
  there is no non-USD discount curve, and nothing is marked without a curve; cross-currency legs are the XCCY product).
- TERMS (§13.11): ``notional`` (decimal string) or ``notional_c``; ``pay_fixed``; ``tenor_months`` (a multiple of the
  fixed and floating periods); the fixed leg is semi-annual (``fixed_months`` 6: the par curve's grid) on 30/360 unless
  booked ACT/360; the floating leg is SOFR ACT/360 every 1 / 3 / 6 / 12 months, ``fixing`` TERM (default) or
  COMPOUND_ARREARS; ``fixed_rate`` null = dealt at par ± the half-width, else a decimal fraction on the 0.001 % grid —
  a rate better for the client than that quote (pay fixed below par + hw, receive fixed above par − hw) is refused 400
  OFF_MARKET (no fabricated day-one gain; a worse rate deals as given, its mark0 is the larger cost of dealing).
- DATES: the effective (start) date is the trade date rolled FOLLOWING to a business day of the trading calendar (never
  before the trade date: a booking on a month-end weekend does not start on the Friday before); each period end is
  ``start + k × months`` rolled MODIFIED FOLLOWING; payment date = period end; maturity = the last period end.
- SWAP_MARK (the dealt rate and the mark are MODEL numbers, disclosed in every memo and in ``inputs``): the semi-annual
  discount factors come from ``services.curve`` (``CurveStore.discount_factors``: bootstrapped from the Treasury par curve,
  or the fixed set a test gives with ``set_fixed_dfs``).  For ``n`` remaining semi-annual periods the model uses the
  0.5-year grid up to ``n/2`` years: ``par = 2 × (1 − df(n/2)) / Σ df`` and ``annuity A = Σ df / 2``.  The PB deals at
  ``par + hw`` (pay fixed) / ``par − hw`` (receive fixed), ``hw = irs_half_width_bp × otc_spread_mult`` from the live PB
  schedule.  Par and dealt rates are quantised to 0.001 % (0.1 bp, ``RATE_Q``, the screen precision a dealer quotes):
  the dealt rate is an exact term of the contract, and marks use the par rate at the same precision (the §19.15 pins).
  Clean PV to the holder = ``sign × N × (par − K) × A`` (sign +1 pay fixed, −1 receive fixed): the floating leg is valued
  at par — the current period's coupons accrue in 1225 / 2340 and are paid on the payment date, they are never part of
  the mark — and the remaining tenor is bucketed to whole semi-annual periods (the fixed periods whose payment date is
  after the valuation date).  DV01 is SIGNED, the PV change for +1 bp: ``dv01_c = sign × N × 1 bp × A`` (+ for a fixed
  payer, − for a receiver; risk multiplies it by the shock).  ``mark0 = −hw × N × A`` is the cost of dealing (shown in the
  preview); the contract is booked with ``mark_c = mark0`` so NAV carries it as the overlay at once.  Quality
  ``computed``; no curve → the stored mark comes back as a ``MarkView`` labelled ``stale`` and SWAP_VM skips (NO_CURVE)
  — nothing is booked or marked without a curve.  ``mark(..., shift_bp=x)`` re-prices with the curve shifted ``x`` bp in
  parallel (a bootstrapped curve only; a fixed set of factors cannot be shifted and is used as given).
- IM (disclosed): ``im_c = |dv01_c| × irs_im_bp`` - the PB schedule's value through ``prime.im_params`` (C22) once it exists,
  else ``SWAP_IM_BP`` (50 bp, static) - posted ``Dr 1330 contract / Cr 1010`` after ``fund_commitment`` (PO-07): the need is
  measured against what may leave the account's settled cash without spending what its payables and working orders already
  need (``execution.fund(commitments=True)``), the shortfall swept from Treasury at booking (then PB financing), else 409
  INSUFFICIENT_FUNDS; the preview's funding line (``funding_preview``) runs the same cascade.  ``balance(1330, contract) ==
  im_c`` while OPEN, 0 afterwards (L22).  No fee beyond the half-width.
- SWAP_VM (job SWAP_VM, 17:05 ET every calendar day from the start date, key ``{contract}:{date}``, idem
  ``swapvm:{contract}:{date}``): the mark of date D is valued AS OF D (the periods remaining after D) on the curve
  available when the job runs; TRS / XCCY through ``mark_trs`` / ``mark_xccy`` with ``valuation_date = D`` (the
  underlying's / the FX pair's CLOSE of D when it is recorded or observed at the close); ``Δ = mark_D − reference``, the
  reference being the mark of the latest VM — or of a later RESET row whose ``detail.post_mark_c`` re-based it (WP-26's TRS
  resets) — by command order (the RESET's journal entry, else ``detail.command_id``: a reset that posted nothing still
  re-bases) (0 before the first VM: the cost of dealing is collateralised on day one).  CTM (schedule ``swap_margin_mode`` CTM, the default): Δ < 0 →
  we post |Δ| (``Dr 1320 / Cr 1010`` after ``fund()`` with the §11.6 overdraft rule), Δ > 0 → we receive Δ (``Dr 1010 /
  Cr 2330``); ``vm_balance_c += Δ``; the single entry keeps ``1320 == max(0, −balance)`` and ``2330 == max(0, balance)``
  per contract (L22), so a zero crossing carries the release of the held side and the posting of the other in the same
  entry.  VM is collateral, not P&L — the P&L is the mark overlay.  STM: Δ settles as P&L (``Dr 1010 / Cr 4450`` or
  reverse) and ``mark_c`` resets to 0 (the overlay shows the PV since the last settle; the model mark itself is kept on
  ``otc_vm.mark_c`` as the next reference).  Quiet terminal outcomes (SKIPPED, no attention, not retried): Δ = 0 →
  ``NO_CHANGE`` (weekends, an unchanged curve); the curve available is dated AFTER D (D's curve was never observed —
  downtime) → ``CAUGHT_UP``: nothing is posted for D and the change is measured on the curve's own date; likewise for a
  TRS / XCCY whose observed inputs (the underlying or FX quote, the USD curve) are dated after D.  TRS / XCCY are valued at
  the CLOSE of the last trading day on or before D (LC-3): when that close is neither recorded nor observed at the close
  the hook answers ``(None, {vm_outcome})`` - CAUGHT_UP (quiet, terminal) when the price available is of a later session,
  else SKIPPED ``CLOSE_PENDING`` (retried; quiet on its first attempt and on non-trading days, one WARN attention once it
  persists) - never a price dated before D or an intraday print of D.  A later VM already posted → DONE ``SUPERSEDED``
  (VM never runs backwards; it resolves the pending attention).  A missing curve → SKIPPED ``NO_CURVE`` with one WARN
  attention per key, retried.  A date with an ``otc_vm`` row answers DONE "already posted": VM is never posted twice for
  a date.  ``business_date`` is the VM date even when the job catches up later.
- ACCRUAL (ACCRUE_DAILY kinds ``swaprcv`` / ``swappay``, idem ``accr:{kind}:{contract}:{night}``): the first night is
  the night of the start date.  ``swappay`` is the fixed leg of a payer and the floating leg of a receiver.  Fixed and
  TERM legs (§11.3 cumulative rounding per period segment): each night posts ``to_c(N × rate × n / 360) − cum``; the
  LAST night of a period posts the difference between the period coupon ``to_c(N × rate × days / 360)`` (days on the
  leg's basis: 30/360 or actual) and the cumulative, so the period sums to the coupon exactly (a 30/360 half-year of
  182 nights trues up −1 night on its last night; never a drift).  A TERM period accrues at its fixing (FIX_PENDING
  until SWAP_FIX recorded it: the night is SKIPPED and retried).  COMPOUND_ARREARS legs: each calendar night D accrues
  at r_D = the latest SOFR on or before D (RATE_LAG, at most 10 days old; none → the night is SKIPPED RATE_UNAVAILABLE and
  retried, never defaulted), compounded daily: ``F_D = F_{D−1} × (1 + r_D / 360)`` (F = 1 before the period's first
  night, stored at 1e-18 in ``accrual_state.rate`` for these legs), cumulative ``to_c(N × (F_D − 1))``, the night posts
  the change — so the period pays exactly ``N × (F_end − 1)``.  Receive leg ``Dr 1225 / Cr 4450``, pay leg ``Dr 4450 /
  Cr 2340`` (a negative change posts the reverse).
- SWAP_RESET: SWAP_FIX (00:40 ET on each TERM floating reset date, key ``{contract}:{date}``) fixes the period rate from
  the latest SOFR observation on or before the reset date (RATE_LAG, at most 10 days old; none → SKIPPED
  RATE_UNAVAILABLE + WARN, retried; never defaulted) into ``otc_resets`` (kind FIX, ``detail_json`` {rate, obs_date,
  series}); the first period is fixed at booking; COMPOUND_ARREARS legs have no reset fixing.  SWAP_PAY (00:45 ET on each
  payment date) settles the legs that pay on that date net: fixed / TERM amounts are the period coupons (which the
  accruals sum to exactly), a COMPOUND_ARREARS amount is the sum of the period's posted accruals; ``Dr 2340 pay / Cr 1225
  receive / Dr or Cr 1010 net`` (``fund()`` with the overdraft rule for a net payment), idem ``swappay:{contract}:{date}``,
  recorded in ``otc_resets`` (kind PAY); accruals not yet through the night before → SKIPPED ACCRUALS_PENDING, retried.
  The accrued coupons are never in the mark, so a payment never pays anything the VM already moved.  The last payment
  date is the maturity: the VM balance is unwound, the IM released (``im:{contract}:release``), status MATURED.
- Termination (``otc.terminate``, owner): the termination value is the current model mark (409 NO_CURVE without a
  curve).  First every night the contract owes through the night before today (at most the night before maturity) is
  posted on both legs exactly as ACCRUE_DAILY would (MONEY-2: ``_irs_catch_up``; a TERM period the scheduler has not fixed
  is fixed from its SOFR print first; a night that cannot accrue answers 409 FIX_PENDING / RATE_UNAVAILABLE and nothing is
  written).  Then one entry ``otc:{contract}:terminate`` unwinds the VM balance (CTM: ``Dr 2330 / Cr 1010`` or ``Dr 1010 /
  Cr 1320``), settles the value (``Dr 1010 / Cr 4450`` or reverse; STM: the value since the last settle) and the accrued
  legs (``Dr 2340 / Cr 1225``, net in cash); the IM is released; status TERMINATED; every per-contract margin balance
  is 0 afterwards (L22).  A termination that needs more cash than the account's own cash (PO-07) and Treasury hold is
  refused (409 INSUFFICIENT_FUNDS); the lifecycle's own payments use the overdraft rule instead.  The other paths that pay
  or close a contract never skip a night: SWAP_PAY (and so the maturity close) waits ACCRUALS_PENDING until the period's
  last night is posted, TRS resets / maturities and XCCY payments / END likewise, and TRS / XCCY terminations catch up
  first (``swaps_trs._catch_up``).
- Jobs: the handlers write their own ``jobs_done`` row (DONE for a settled key and for terminal no-ops; SKIPPED for a
  pending outcome); ``vm_keys`` / ``fix_keys`` / ``pay_keys`` return every key that is not terminal, so a SKIPPED key
  is retried (a retry needs a new request id — the bus replays an id with its stored result).  ``vm_keys`` covers every
  contract kind (VM is common to IRS / TRS / XCCY); ``fix_keys`` covers IRS TERM legs and ``pay_keys`` IRS contracts
  (TRS / XCCY resets and exchanges are WP-26's jobs).  Money is integer cents / Decimal end to end; there are no floats
  (the §13.11 "float helpers" are replaced by exact Decimal arithmetic on the same formulas: ``N × (par − K) × A``).
- HOOKS (C21; every one optional) of ``services.swaps_trs`` (TRS / XCCY) and ``services.otc_options`` (EQ_OPTION / FX_OPTION
  / EQ_FORWARD / FX_FORWARD, Build 2 P12), chosen by the contract kind (``_module_for``): ``preview(services, account_id,
  kind, terms[, conn, now])``, ``book(ctx, payload)``, ``terminate(ctx, payload)``, the mark - ``mark_trs`` / ``mark_xccy`` /
  ``mark(services, row[, at, valuation_date, snapshot])`` (→ ``(cents, inputs)``, a stale ``MarkView``, or with
  ``valuation_date`` ``(None, {vm_outcome: CLOSE_PENDING | CAUGHT_UP, note, quiet})``; used by SWAP_VM, ``mark`` and the
  views), ``dv01_c(services, row)``, ``leg_specs(services, row)`` (→ ``{kind: LegSpec}``: the coupon legs of an XCCY,
  accrued here per currency through ``accrue_leg``), else ``accrue_night(ctx, services, kind, ref, night)``, ``pay(ctx,
  payload)`` / ``fix(ctx, payload)`` for direct calls, ``next_events(services, row, conn)``, ``contract_detail(services, row,
  conn)`` (may carry ``side`` / ``side_text``); ``close_contract`` (VM unwind + value + IM release), ``im_params``,
  ``fund_commitment`` and ``funding_preview`` are public for them.  A kind whose module is not bound answers 501 NOT_BUILT.
- ONE READER (ORC-6): every read here goes through the connection the caller holds or ``marks.shared_reader`` (this
  thread's reading connection, else ONE pooled reader declared for the block) - never a second pooled reader inside one;
  a contracts view takes ONE small mark snapshot (the TRS underlyings / XCCY pairs) for all its rows.
"""
from __future__ import annotations

import inspect
import json
import sqlite3
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN
from typing import Any, Dict, List, Optional, Tuple

from .. import config, execution, prime
from .. import marks as _marks
from ..books import account_row, eligibility as _eligibility, require_pb_otc, split_account_id
from ..clock import epoch_of, ny_of
from ..commands import ConflictError, RejectError, TxContext
from ..db import dumps, next_id
from ..ledger import Entry, Line, balance, cr, dr, natural
from ..quotes import MarkView
from ..units import fmt_money, from_c, qprice, to_c

JOB_VM, JOB_FIX, JOB_PAY = "SWAP_VM", "SWAP_FIX", "SWAP_PAY"
SOURCE = "swaps"
VM_ET, FIX_ET, PAY_ET = "17:05", "00:40", "00:45"
RATE_Q = Decimal("0.00001")            # 0.001 % = 0.1 bp: the dealing precision of par and dealt rates
DF_Q = Decimal("0.0000000001")
GRID_Q = Decimal("0.0001")
FACTOR_Q = Decimal("0.000000000000000001")   # 1e-18: the stored precision of a COMPOUND_ARREARS factor
RATE_8 = Decimal("0.00000001")
BP = Decimal("0.0001")
HALF = Decimal("0.5")
ONE = Decimal(1)
DAYS_360 = Decimal(360)
RATE_LAG_MAX_DAYS = 10
SWAP_IM_BP = int(getattr(config, "SWAP_IM_BP", 50))
CONVENTIONS = ("SWAP_MARK", "SWAP_VM", "SWAP_RESET", "ACCRUAL", "RATE_LAG", "ELIGIBILITY")
SESSION_GROUP = "US_BONDS"             # the instruments row of a swap (rates desk hours; never quoted, never executable)
TRS_MODULE = "swaps_trs"
OTC_MODULE = "otc_options"                                                  # C21: the PB's OTC options / forwards (Build 2 P12)
OTC_OPTION_KINDS = ("EQ_OPTION", "FX_OPTION", "EQ_FORWARD", "FX_FORWARD")
CDS_MODULE = "swaps_cds"                                                   # the CDS index delegate kind (products/swaps_cds.py)
CDS_KINDS = ("CDX",)
KIND_LABELS = {"IRS": "interest-rate swap", "TRS": "total return swap", "XCCY": "cross-currency swap", "EQ_OPTION": "OTC equity option",
               "FX_OPTION": "OTC FX option", "EQ_FORWARD": "equity forward", "FX_FORWARD": "FX forward", "CDX": "CDS index (CDX)"}
# SR-10 / C22: the static IM the PB schedule replaces once prime.im_params exists (IRS |DV01| x bp; the others a fraction of the notional)
IM_DEFAULTS = {"irs_im_bp": Decimal(SWAP_IM_BP), "trs_im_pct": Decimal("0.10"), "xccy_im_pct": Decimal("0.02"), "option_im_pct": None,
               "forward_im_pct": None}
VM_PENDING_CODES = ("NO_CURVE", "NOT_BUILT", "NO_MARK", "CLOSE_PENDING")    # SKIPPED (retried) VM outcomes whose attention a later outcome resolves
FUNDING_RULE = ("funded from the account's own cash - settled cash less what its payables and working orders already need - then one Treasury"
                " sweep, then PB financing; cash a pending settlement already needs is never spent")
FLOAT_MONTHS = (1, 3, 6, 12)
FIXED_BASES = ("30/360", "ACT/360")
FIXINGS = ("TERM", "COMPOUND_ARREARS")
LEG_GL = {"swaprcv": ("1225", "4450"), "swappay": ("4450", "2340")}      # (debit, credit) of a positive accrual
VM_TERMINAL = ("NO_CHANGE", "CAUGHT_UP")                                   # quiet SKIPPED notes that end a VM key
MODEL = "clean PV = sign x N x (par - K) x annuity on the remaining semi-annual periods; floating leg at par (SWAP_MARK)"
TERM_METHOD = "TERM: the latest SOFR print on or before the reset date (RATE_LAG)"
COMPOUND_METHOD = ("COMPOUND_ARREARS: every calendar night accrues at the latest SOFR on or before it (RATE_LAG), compounded daily"
                   " F = prod(1 + r/360); the period pays N x (F - 1)")

Period = Tuple[date, date, date]       # (accrual start, accrual end, payment date), all business-day adjusted


class NotBuilt(RejectError):
    """TRS / XCCY before WP-26 is bound -> HTTP 501 NOT_BUILT."""
    http = 501


class NotFound(RejectError):
    http = 404


# ---- pure helpers: dates, day counts, the model ---------------------------------------------------------------------------------
def add_months(d: date, months: int) -> date:
    """Calendar months forward, the day clamped to the month's end (30 Oct + 4 months = 28 Feb)."""
    y, m = divmod(d.month - 1 + int(months), 12)
    y += d.year
    m += 1
    last = [31, 29 if (y % 4 == 0 and (y % 100 != 0 or y % 400 == 0)) else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][m - 1]
    return date(y, m, min(d.day, last))


def modified_following(cal, d: date) -> date:
    """The first business day on or after ``d`` unless that crosses into the next month, then the last one before."""
    rolled = cal.roll_following(d)
    return cal.roll_back(d) if rolled.month != d.month else rolled


def effective_date(cal, trade_date: date) -> date:
    """The start of a swap booked on ``trade_date``: the first business day on or after it (module docstring DATES)."""
    return cal.roll_following(trade_date)


def period_schedule(cal, anchor: date, tenor_months: int, step_months: int) -> List[Period]:
    """Periods from ``anchor`` (the effective date, a business day) every ``step_months`` to ``anchor + tenor_months`` (a
    multiple of the step); the ends are rolled modified following."""
    out: List[Period] = []
    s = modified_following(cal, anchor)
    k = 0
    while True:
        k += 1
        months = step_months * k
        if months > tenor_months:
            break
        e = modified_following(cal, add_months(anchor, months))
        out.append((s, e, e))
        s = e
        if months == tenor_months:
            break
    return out


def days_30360(d1: date, d2: date) -> int:
    """30/360 (US bond basis) days from ``d1`` to ``d2``."""
    dd1 = min(d1.day, 30)
    dd2 = d2.day
    if dd2 == 31 and dd1 == 30:
        dd2 = 30
    return (d2.year - d1.year) * 360 + (d2.month - d1.month) * 30 + (dd2 - dd1)


def period_days(s: date, e: date, basis: str) -> int:
    return days_30360(s, e) if basis == "30/360" else (e - s).days


def coupon_c(notional_c: int, rate, s: date, e: date, basis: str) -> int:
    """``to_c(N × rate × days / 360)`` for one period on its basis."""
    return to_c(from_c(int(notional_c)) * qprice(rate) * Decimal(period_days(s, e, basis)) / DAYS_360)


def compound_step(factor, rate) -> Decimal:
    """One calendar night of a COMPOUND_ARREARS leg: ``F × (1 + r / 360)`` at 1e-18 (HALF_EVEN)."""
    return (Decimal(factor) * (ONE + qprice(rate) / DAYS_360)).quantize(FACTOR_Q, rounding=ROUND_HALF_EVEN)


def compounded_c(notional_c: int, factor) -> int:
    """``to_c(N × (F − 1))``: the compounded interest of a leg on its factor."""
    return to_c(from_c(int(notional_c)) * (Decimal(factor) - ONE))


def par_annuity(dfs: Dict[Decimal, Decimal]) -> Tuple[Decimal, Decimal, Decimal]:
    """``(par quantised to RATE_Q, annuity = Σ df / 2, raw par)`` from semi-annual discount factors keyed by years."""
    pts = sorted(dfs.items())
    total = sum((v for _t, v in pts), Decimal(0))
    last = pts[-1][1]
    raw = (ONE - last) * 2 / total
    return raw.quantize(RATE_Q, rounding=ROUND_HALF_EVEN), (total * HALF).quantize(DF_Q), raw.quantize(DF_Q)


def model_mark_c(notional_c: int, pay_fixed: bool, fixed_rate, par, annuity) -> int:
    """``sign × N × (par − K) × A`` in cents (sign +1 pay fixed, −1 receive fixed)."""
    sign = ONE if pay_fixed else -ONE
    return to_c(sign * from_c(int(notional_c)) * (qprice(par) - qprice(fixed_rate)) * Decimal(annuity))


def dv01_from(notional_c: int, annuity, pay_fixed: bool = True) -> int:
    """The PV change for +1 bp, ``sign × N × 1 bp × A`` in cents (+ pay fixed, − receive fixed)."""
    v = to_c(from_c(int(notional_c)) * BP * Decimal(annuity))
    return v if pay_fixed else -v


def _pct(rate) -> str:
    return f"{(Decimal(rate) * 100).quantize(Decimal('0.0001'))} %"


def _date(x) -> date:
    return x if isinstance(x, date) else date.fromisoformat(str(x))


def _periods(terms: dict, key: str) -> List[Period]:
    return [(_date(s), _date(e), _date(p)) for s, e, p in (terms.get(key) or [])]


def _period_containing(periods, d: date) -> Optional[Period]:
    for p in periods:
        if p[0] <= d < p[1]:
            return p
    return None


# ---- terms ------------------------------------------------------------------------------------------------------------------------
@dataclass
class IrsTerms:
    notional_c: int
    currency: str
    pay_fixed: bool
    tenor_months: int
    fixed_rate: Optional[Decimal]      # None = par
    fixed_basis: str = "30/360"
    fixed_months: int = 6
    float_index: str = "SOFR"
    float_basis: str = "ACT/360"
    float_months: int = 3
    fixing: str = "TERM"

    def to_dict(self) -> dict:
        return {"notional_c": self.notional_c, "currency": self.currency, "pay_fixed": self.pay_fixed, "tenor_months": self.tenor_months,
                "fixed_rate": None if self.fixed_rate is None else str(self.fixed_rate), "fixed_basis": self.fixed_basis, "fixed_months": self.fixed_months,
                "float_index": self.float_index, "float_basis": self.float_basis, "float_months": self.float_months, "fixing": self.fixing}


def _bool(v, name: str) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, int) and v in (0, 1):
        return bool(v)
    s = str(v if v is not None else "").strip().lower()
    if s in ("true", "1", "yes", "pay", "payer", "pay_fixed"):
        return True
    if s in ("false", "0", "no", "receive", "receiver", "receive_fixed"):
        return False
    raise RejectError("TERMS", f"{name} must be true or false, got {v!r}")


def _int(v, name: str, lo: int, hi: int) -> int:
    if isinstance(v, bool) or v is None:
        raise RejectError("TERMS", f"{name} is required")
    try:
        n = int(Decimal(str(v)))
    except (InvalidOperation, ValueError) as e:
        raise RejectError("TERMS", f"{name} must be an integer, got {v!r}") from e
    if n < lo or n > hi:
        raise RejectError("TERMS", f"{name} must be between {lo} and {hi}, got {n}")
    return n


def parse_irs_terms(terms: dict) -> IrsTerms:
    """Validate the §13.11 IRS term dictionary (400 TERMS / CURRENCY_UNSUPPORTED)."""
    terms = dict(terms or {})
    raw_c, raw = terms.get("notional_c"), terms.get("notional")
    if isinstance(raw_c, float) or isinstance(raw, float):
        raise RejectError("TERMS", "notional must be a decimal string or integer cents, not a float")
    if raw_c is not None and not isinstance(raw_c, bool):
        try:
            notional_c = int(Decimal(str(raw_c)))
        except (InvalidOperation, ValueError) as e:
            raise RejectError("TERMS", f"notional_c must be integer cents, got {raw_c!r}") from e
    elif raw is not None:
        try:
            notional_c = to_c(str(raw))
        except (ValueError, TypeError) as e:
            raise RejectError("TERMS", f"notional is not a number: {raw!r}") from e
    else:
        raise RejectError("TERMS", "notional is required (a decimal string in notional or integer cents in notional_c)")
    if notional_c <= 0:
        raise RejectError("TERMS", "notional must be positive")
    ccy = str(terms.get("currency") or "USD").strip().upper()
    if ccy != "USD":
        raise RejectError("CURRENCY_UNSUPPORTED", f"{ccy} swaps are not in this build: there is no {ccy} discount curve, and nothing is marked without a curve;"
                          " USD / SOFR only (cross-currency legs are the XCCY product)", {"currency": ccy, "supported": ["USD"]})
    if "pay_fixed" in terms:
        pay_fixed = _bool(terms.get("pay_fixed"), "pay_fixed")
    elif "side" in terms:
        pay_fixed = _bool(terms.get("side"), "side")
    else:
        raise RejectError("TERMS", "pay_fixed (true = pay fixed / receive floating) is required")
    tenor = _int(terms.get("tenor_months"), "tenor_months", 1, 600)
    fixed_months = _int(terms.get("fixed_months", 6), "fixed_months", 1, 12)
    float_months = _int(terms.get("float_months", 3), "float_months", 1, 12)
    if fixed_months != 6:
        raise RejectError("TERMS", f"fixed_months must be 6 in this build (semi-annual fixed legs, the par curve's grid), got {fixed_months}")
    if float_months not in FLOAT_MONTHS:
        raise RejectError("TERMS", f"float_months must be one of {FLOAT_MONTHS}, got {float_months}")
    if tenor % fixed_months or tenor % float_months:
        raise RejectError("TERMS", f"tenor_months {tenor} must be a multiple of fixed_months {fixed_months} and float_months {float_months}")
    fixed_basis = str(terms.get("fixed_basis") or "30/360").strip().upper()
    float_basis = str(terms.get("float_basis") or "ACT/360").strip().upper()
    if fixed_basis not in FIXED_BASES:
        raise RejectError("TERMS", f"fixed_basis must be one of {', '.join(FIXED_BASES)} (30/360 unless booked otherwise), got {fixed_basis}")
    if float_basis != "ACT/360":
        raise RejectError("TERMS", f"float_basis must be ACT/360 (SOFR's day count), got {float_basis}")
    index = str(terms.get("float_index") or "SOFR").strip().upper()
    if index != "SOFR":
        raise RejectError("CURRENCY_UNSUPPORTED", f"float index {index} is not in this build; SOFR only", {"float_index": index})
    fixing = str(terms.get("fixing") or "TERM").strip().upper()
    if fixing not in FIXINGS:
        raise RejectError("TERMS", f"fixing must be TERM or COMPOUND_ARREARS, got {fixing!r}", {"supported": list(FIXINGS)})
    fr = terms.get("fixed_rate")
    fixed_rate: Optional[Decimal] = None
    if fr not in (None, "", "par", "PAR"):
        if isinstance(fr, float):
            raise RejectError("TERMS", "fixed_rate must be a decimal string (0.0401 = 4.01 %), not a float")
        try:
            d = Decimal(str(fr).strip())
        except (InvalidOperation, ValueError) as e:
            raise RejectError("TERMS", f"fixed_rate is not a number: {fr!r}") from e
        if not d.is_finite() or d <= 0 or d >= 1:
            raise RejectError("TERMS", f"fixed_rate must be a fraction between 0 and 1 (0.0401 = 4.01 %), got {fr!r}")
        if d != d.quantize(RATE_Q):
            raise RejectError("TERMS", f"fixed_rate {fr!r} is finer than the dealing precision 0.001 % (0.1 bp)")
        fixed_rate = d.quantize(RATE_Q)
    return IrsTerms(notional_c, ccy, pay_fixed, tenor, fixed_rate, fixed_basis, fixed_months, index, float_basis, float_months, fixing)


# ---- coupon legs ------------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class LegSpec:
    """One coupon leg (module docstring HOOKS): the accrual kind that carries it, its currency, notional, basis and
    periods, and how its rate is set — FIXED at ``rate``, TERM from the period's FIX row, COMPOUND_ARREARS from the
    nightly SOFR prints."""
    kind: str                          # swaprcv | swappay
    leg: str                           # fixed | float
    currency: str
    notional_c: int
    basis: str
    periods: Tuple[Period, ...]
    fixing: str                        # FIXED | TERM | COMPOUND_ARREARS
    rate: Optional[Decimal] = None

    @property
    def gl(self) -> str:
        return "1225" if self.kind == "swaprcv" else "2340"

    @property
    def side(self) -> str:
        return "receive" if self.kind == "swaprcv" else "pay"


def irs_leg_specs(row: dict) -> Dict[str, LegSpec]:
    """The two legs of an IRS contract row, keyed by accrual kind (``swappay`` = the fixed leg of a payer)."""
    terms = _terms(row)
    pay_fixed = bool(terms.get("pay_fixed"))
    ccy, n = row["currency"], int(row["notional_c"])
    fixed = LegSpec("swappay" if pay_fixed else "swaprcv", "fixed", ccy, n, str(terms.get("fixed_basis") or "30/360"), tuple(_periods(terms, "fixed_periods")), "FIXED",
                    qprice(terms["fixed_rate"]) if terms.get("fixed_rate") else None)
    flt = LegSpec("swaprcv" if pay_fixed else "swappay", "float", ccy, n, str(terms.get("float_basis") or "ACT/360"), tuple(_periods(terms, "float_periods")),
                  str(terms.get("fixing") or "TERM").upper())
    return {fixed.kind: fixed, flt.kind: flt}


def _float_leg(row: dict) -> LegSpec:
    return next(s for s in irs_leg_specs(row).values() if s.leg == "float")


def leg_specs(services, row: dict) -> Dict[str, LegSpec]:
    """The coupon legs of any contract: IRS from its terms; other kinds through ``swaps_trs.leg_specs`` ({} without it)."""
    if row["kind"] == "IRS":
        return irs_leg_specs(row)
    fn = _hook(services, "leg_specs", row["kind"])
    return dict(fn(services, row) or {}) if fn is not None else {}


# ---- services, rows ------------------------------------------------------------------------------------------------------------------
def _services(ctx: TxContext, services=None):
    s = services if services is not None else ctx.services
    if s is None:
        raise RejectError("NO_SERVICES", "the command context carries no services bundle")
    return s


def _contract_row(conn: sqlite3.Connection, contract_id: str):
    return conn.execute("SELECT * FROM otc_contracts WHERE contract_id=?", (contract_id,)).fetchone()


def _contract_dict(services, contract, conn: Optional[sqlite3.Connection] = None) -> Optional[dict]:
    """A contract as a full row dict from an id, an ``otc_contracts`` row / dict or the SWAP ``Instrument`` (None when
    unknown).  A partial dict (risk's obligations rows) is re-read by its ``contract_id``."""
    if contract is None:
        return None
    if isinstance(contract, sqlite3.Row):
        return {k: contract[k] for k in contract.keys()}
    if isinstance(contract, dict):
        if "terms_json" in contract:
            return dict(contract)
        cid = str(contract.get("contract_id") or "")
    else:
        cid = getattr(contract, "instrument_id", None) or str(contract)
    with _marks.shared_reader(services.db, conn) as c:        # ORC-6: the held connection, never a nested pooled reader
        row = _contract_row(c, cid)
    return None if row is None else {k: row[k] for k in row.keys()}


def _terms(row: dict) -> dict:
    try:
        return json.loads(row.get("terms_json") or "{}")
    except ValueError:
        return {}


def _pb_account(conn: sqlite3.Connection, account_id: str) -> dict:
    acct = account_row(conn, account_id)
    if acct is None:
        raise NotFound("ACCOUNT_UNKNOWN", f"account {account_id} does not exist", {"account_id": account_id})
    acct = dict(acct)
    if acct["status"] != "OPEN":
        raise RejectError("ACCOUNT_CLOSED", f"account {account_id} is closed")
    if acct["kind"] != "PB":
        raise RejectError("NOT_TRADEABLE_HERE", f"{account_id} is a {acct['kind']} account: OTC swaps are booked in a prime-broker account (PB page, OTC tab)",
                          {"account_id": account_id, "kind": acct["kind"]})
    return acct


def _trs_module(services):
    return getattr(services, TRS_MODULE, None)


def _module_for(services, kind: Optional[str] = None):
    """C21: the module that carries a contract kind's hooks - the OTC options / forwards (EQ_OPTION, FX_OPTION, EQ_FORWARD,
    FX_FORWARD) ``services.otc_options``, TRS / XCCY (and no kind) ``services.swaps_trs``; None while it is not bound."""
    k = str(kind or "").upper()
    if k in CDS_KINDS:
        return getattr(services, CDS_MODULE, None)
    return getattr(services, OTC_MODULE if k in OTC_OPTION_KINDS else TRS_MODULE, None)


def _hook(services, name: str, kind: Optional[str] = None):
    mod = _module_for(services, kind)
    return getattr(mod, name, None) if mod is not None else None


def _mark_hook(services, kind: str):
    """The mark hook of a non-IRS kind: ``swaps_trs.mark_trs`` / ``mark_xccy``, ``otc_options.mark`` (C21)."""
    k = str(kind or "").upper()
    return _hook(services, {"TRS": "mark_trs", "XCCY": "mark_xccy"}.get(k, "mark"), k)


def _delegate(services, kind: str, fn_name: str):
    """The hook of a non-IRS kind (``_module_for``), else 501 NOT_BUILT; unknown kinds -> prime's gate (400)."""
    k = prime.require_otc_kind(kind)
    fn = _hook(services, fn_name, k)
    if fn is None:
        raise NotBuilt("NOT_BUILT", f"{KIND_LABELS.get(k, k)}s are not available in this version (interest-rate swaps are)", {"kind": k})
    return fn


def _call(fn, *args, **kwargs):
    """Call a hook with the keyword arguments it accepts (a hook written before a keyword existed is called without it)."""
    return fn(*args, **{k: v for k, v in kwargs.items() if _accepts(fn, k)})


# ---- initial margin parameters and funding (SR-10 / C22, PO-07 / C12c) ---------------------------------------------------------------
def im_params(services, account_id: str) -> dict:
    """The PB's initial-margin parameters (C22): ``prime.im_params(services, account_id)`` once the prime module provides it, else
    the static values of this build - IRS |DV01| x 50 bp, TRS 10 % and XCCY 2 % of the notional.  The ``*_pct`` values are
    fractions (0.10 = 10 %; a value of 1 or more is read as a percent); ``source`` says where they came from."""
    out = dict(IM_DEFAULTS, source="static (disclosed)")
    fn = getattr(prime, "im_params", None)
    got = None
    if fn is not None:
        try:
            got = fn(services, account_id)
        except RejectError:                       # an unknown / closed account refuses through its own gate first
            got = None
    if isinstance(got, dict):
        for k in IM_DEFAULTS:
            v = got.get(k)
            if v in (None, ""):
                continue
            try:
                d = Decimal(str(v))
            except (InvalidOperation, ValueError):
                continue
            if not d.is_finite() or d < 0:
                continue
            out[k] = d / 100 if (k.endswith("_pct") and d >= 1) else d
        out["source"] = "the prime broker's schedule"
    return out


def pct_text(fraction) -> str:
    """0.10 -> "10.00 %" (a fraction shown in percent with 2 decimals)."""
    return f"{(Decimal(fraction) * 100).quantize(Decimal('0.01'))} %"


def fund_commitment(ctx: TxContext, services, account_id: str, ccy: str, need_c: int, link_type: str, link_id: str):
    """PO-07 / C12c: what the owner commits at booking or termination (an IM, a USD start exchange, a termination payment) is
    measured against the account's own cash that may leave settled cash now - min(settled, settled + receivables due today -
    payables - cash reserved by working orders) - never against cash a pending settlement already needs; the shortfall is swept
    from Treasury now (then PB financing), else 409 INSUFFICIENT_FUNDS.  ``execution.fund(..., commitments=True)`` when the funding
    module has that mode; otherwise the same rule through its available-at-today path, and settled cash is topped up if a
    receivable counted as available has not settled yet (the posting leaves settled cash now)."""
    need_c = int(need_c)
    if _accepts(execution.fund, "commitments"):
        return execution.fund(ctx, account_id, ccy, need_c, link_type, link_id, None, commitments=True, services=services)
    res = execution.fund(ctx, account_id, ccy, need_c, link_type, link_id, None, services=services)
    if need_c > 0 and balance(ctx.conn, account_id, "1010", ccy) < need_c:
        more = execution.fund(ctx, account_id, ccy, need_c, link_type, link_id, None, immediate=True, services=services)
        res.sweep_c, res.pb_draw_c = int(res.sweep_c or 0) + int(more.sweep_c or 0), int(res.pb_draw_c or 0) + int(more.pb_draw_c or 0)
        res.sweep_id = res.sweep_id or more.sweep_id
        if more.source not in ("own", "none"):
            res.source = more.source
    return res


def funding_preview(services, conn: sqlite3.Connection, account_id: str, ccy: str, need_c: int, snapshot=None, what: str = "IM") -> dict:
    """The funding line a preview shows for ``fund_commitment`` (PO-07), computed exactly as ``execution.fund(commitments=True)``
    will run it (C12): own = what may leave settled cash now without spending what the payables and working orders need
    (``accounting.immediate_available_c``: min(settled, available)), then ONE Treasury sweep of min(shortfall, Treasury available
    less the book's reserve), then PB financing within the headroom (USD, the switch on), else the booking is refused.  Pure; the
    keys ``own_settled_c`` / ``sweep_c`` are kept for earlier readers."""
    from .. import accounting as _acc
    need = int(need_c)
    settled = balance(conn, account_id, "1010", ccy)
    acct = account_row(conn, account_id)
    acct = dict(acct) if acct is not None else {"account_id": account_id, "kind": "PB", "pb_financing": 0, "lifetime_id": split_account_id(account_id)[0]}
    master = getattr(services, "instruments", None)
    now = services.clock.now()
    snap = snapshot if snapshot is not None else _full_snapshot(services)
    available = _acc.available_c(conn, account_id, ccy, None, snap, master, now)
    imm = getattr(_acc, "immediate_available_c", None)
    own = imm(conn, account_id, ccy, None, snap, master, now) if imm is not None else max(0, min(settled, available))
    tre_fn = getattr(_acc, "treasury_available_c", None)
    tre = max(0, int(tre_fn(conn, acct["lifetime_id"], ccy, snap, master, now))) if tre_fn is not None else 0
    pb = 0
    credit = getattr(services, "credit", None)
    accounting = getattr(services, "accounting", None)
    if (ccy == "USD" and acct.get("kind") == "PB" and int(acct.get("pb_financing") or 0) and credit is not None and hasattr(credit, "pb_draw_for_funding")
            and accounting is not None and hasattr(accounting, "pb_headroom_c")):
        pb = max(0, int(accounting.pb_headroom_c(conn, acct, snap)))
    committed = max(0, settled - own)
    shortfall = max(0, need - own)
    sweep = min(shortfall, tre)
    pb_draw = min(shortfall - sweep, pb)
    unfunded = shortfall - sweep - pb_draw
    if unfunded > 0:
        note = (f"{what} {ccy} {fmt_money(need)} cannot be funded: own {fmt_money(own)}, Treasury {fmt_money(tre)}, PB financing {fmt_money(pb)}"
                " - booking is refused")
    elif shortfall == 0:
        note = f"{what} {ccy} {fmt_money(need)} from the account's own cash {fmt_money(own)}"
    else:
        note = (f"{what} {ccy} {fmt_money(need)}: own {fmt_money(own)}, sweep {fmt_money(sweep)} from Treasury"
                + (f" + PB financing {fmt_money(pb_draw)}" if pb_draw else ""))
    if committed > 0:
        note += f" ({fmt_money(committed)} of the settled cash is already needed by payables / working orders and is not spent)"
    return {"currency": ccy, "need_c": need, "settled_c": settled, "own_settled_c": settled, "available_c": available, "own_c": own, "committed_c": committed,
            "shortfall_c": shortfall, "sweep_c": sweep, "treasury_available_c": tre, "pb_draw_c": pb_draw, "unfunded_c": unfunded, "ok": unfunded == 0,
            "rule": FUNDING_RULE, "note": note}


def _full_snapshot(services):
    m = getattr(services, "marks", None)
    snap = getattr(m, "snapshot", None) if m is not None else None
    return snap() if callable(snap) else m


# ---- the curve and the fixings ------------------------------------------------------------------------------------------------------
def curve_as_of(services) -> Optional[date]:
    """The date of the curve ``services.curve`` serves (None when nothing was observed)."""
    curve = getattr(services, "curve", None)
    fn = getattr(curve, "as_of", None) if curve is not None else None
    return fn() if fn is not None else None


def dfs_for(services, n_periods: int, shift_bp=None) -> Tuple[Dict[Decimal, Decimal], dict]:
    """The semi-annual discount factors for ``n_periods`` (0.5 … n/2 years) from ``services.curve`` with their provenance;
    409 ``NO_CURVE`` when no curve store is bound, no curve was observed or the factors stop before the horizon."""
    curve = getattr(services, "curve", None)
    if curve is None or not hasattr(curve, "discount_factors"):
        raise ConflictError("NO_CURVE", "no par curve store is bound (services.curve): swaps cannot be marked and nothing is booked without a curve")
    years = HALF * Decimal(int(n_periods))
    shifts = None
    if shift_bp not in (None, 0):
        shifts = {ONE: Decimal(str(shift_bp)) / Decimal(100)}
    dfs = curve.discount_factors(years, shifts) if shifts else curve.discount_factors(years)
    if not dfs:
        raise ConflictError("NO_CURVE", "no par curve has been observed yet (Treasury.gov par yields): the swap cannot be marked; retried when the curve arrives")
    grid = [(HALF * Decimal(k)).quantize(GRID_Q) for k in range(1, int(n_periods) + 1)]
    norm = {Decimal(str(t)).quantize(GRID_Q): Decimal(str(v)) for t, v in dfs.items()}
    missing = [str(t) for t in grid if t not in norm]
    if missing:
        raise ConflictError("NO_CURVE", f"the curve's discount factors stop before {grid[-1]} years (missing tenors {', '.join(missing)})", {"missing": missing})
    as_of = curve.as_of() if hasattr(curve, "as_of") else None
    latest = curve.latest() if hasattr(curve, "latest") else None
    inputs = {"curve_as_of": as_of.isoformat() if as_of else None, "curve_source": getattr(latest, "source", None) or ("fixed discount factors" if as_of else None),
              "tenors": [str(t) for t in grid], "dfs": {str(t): str(norm[t]) for t in grid}, "shift_bp": None if shifts is None else str(shift_bp)}
    return {t: norm[t] for t in grid}, inputs


def fixing_for(services, d: date) -> Tuple[date, Decimal]:
    """``(observation date, SOFR percent)`` for date ``d``: the latest print on or before it, at most 10 days old
    (RATE_LAG); 409 ``RATE_UNAVAILABLE`` otherwise — never defaulted."""
    rates = getattr(services, "rates", None)
    if rates is None or not hasattr(rates, "as_of_le"):
        raise ConflictError("RATE_UNAVAILABLE", "no rate store is bound (services.rates): SOFR is unknown and is never defaulted", {"series": "SOFR"})
    got = rates.as_of_le("SOFR", d)
    if got is None or (d - got[0]).days > RATE_LAG_MAX_DAYS:
        raise ConflictError("RATE_UNAVAILABLE", f"no SOFR observation within {RATE_LAG_MAX_DAYS} days on or before {d.isoformat()} (RATE_LAG; waits for FRED)",
                            {"series": "SOFR", "date": d.isoformat()})
    return got[0], qprice(got[1])


def _rate_of(pct: Decimal) -> Decimal:
    return (Decimal(pct) / Decimal(100)).quantize(RATE_Q, rounding=ROUND_HALF_EVEN)


# ---- the deal (preview and booking share it) -------------------------------------------------------------------------------------------
@dataclass
class Deal:
    acct: dict
    terms: IrsTerms
    schedule: Any
    start: date
    maturity: date
    fixed_periods: List[Period]
    float_periods: List[Period]
    hw_bp: Decimal
    hw: Decimal
    mult: Decimal
    par: Decimal
    par_raw: Decimal
    annuity: Decimal
    dealt: Decimal
    mark0_c: int
    dv01_c: int
    im_c: int
    margin_mode: str
    fix_obs: date
    fix_pct: Decimal
    fix_rate: Decimal
    curve_inputs: dict = field(default_factory=dict)
    im_bp: Decimal = Decimal(SWAP_IM_BP)
    im_source: str = "static (disclosed)"

    @property
    def n_periods(self) -> int:
        return len(self.fixed_periods)

    @property
    def compound(self) -> bool:
        return self.terms.fixing == "COMPOUND_ARREARS"


def _deal(services, conn: sqlite3.Connection, acct: dict, terms: IrsTerms, today: date) -> Deal:
    sched = prime.read_schedule(conn, acct)
    mult = qprice(Decimal(sched.otc_spread_mult))
    hw_bp = qprice(Decimal(sched.irs_half_width_bp) * mult)
    hw = (hw_bp * BP).quantize(RATE_Q, rounding=ROUND_HALF_EVEN)
    cal = services.cal
    start = effective_date(cal, today)
    fixed_periods = period_schedule(cal, start, terms.tenor_months, terms.fixed_months)
    float_periods = period_schedule(cal, start, terms.tenor_months, terms.float_months)
    maturity = fixed_periods[-1][1]
    dfs, curve_inputs = dfs_for(services, len(fixed_periods))
    par, annuity, par_raw = par_annuity(dfs)
    quote = ((par + hw) if terms.pay_fixed else (par - hw)).quantize(RATE_Q)
    if terms.fixed_rate is not None:
        # a client rate BETTER than the dealer's quote (paying less than par + hw, receiving more than par - hw) would be a fabricated
        # day-one gain in NAV that no dealer gives: refused, as the cross-currency quotes are (WP-26 OFF_MARKET); a worse rate deals as given
        if (terms.pay_fixed and terms.fixed_rate < quote) or (not terms.pay_fixed and terms.fixed_rate > quote):
            raise RejectError("OFF_MARKET", f"{'paying' if terms.pay_fixed else 'receiving'} fixed at {_pct(terms.fixed_rate)} is better for you than {prime.broker_of(acct)}'s quote"
                              f" {_pct(quote)} (model par {_pct(par)} {'+' if terms.pay_fixed else '-'} {hw_bp} bp): a dealer does not deal there; leave the rate"
                              " blank to deal at the quote", {"fixed_rate": str(terms.fixed_rate), "quote": str(quote), "par_rate": str(par), "half_width_bp": str(hw_bp),
                                                              "pay_fixed": terms.pay_fixed})
        dealt = terms.fixed_rate
    else:
        dealt = quote
    if dealt <= 0:
        raise RejectError("TERMS", f"the dealt rate {dealt} is not positive (par {par}, half-width {hw})")
    mark0 = model_mark_c(terms.notional_c, terms.pay_fixed, dealt, par, annuity)
    dv01 = dv01_from(terms.notional_c, annuity, terms.pay_fixed)
    imp = im_params(services, acct["account_id"])
    im_bp = Decimal(imp["irs_im_bp"])
    im = to_c(from_c(abs(dv01)) * im_bp)                          # IM = |DV01| x the schedule's bp (50 bp static until C22)
    obs, pct = fixing_for(services, start)
    return Deal(acct, terms, sched, start, maturity, fixed_periods, float_periods, hw_bp, hw, mult, par, par_raw, annuity, dealt, mark0, dv01, im,
                str(sched.swap_margin_mode).upper(), obs, pct, _rate_of(pct), curve_inputs, im_bp, imp["source"])


def _schedule_rows(deal: Deal) -> List[dict]:
    """One row per payment date: the fixed coupon and the floating coupon — TERM: the first period at its fixing, later
    periods indicative at the current fixing; COMPOUND_ARREARS: every period indicative, compounded daily at the current
    SOFR (their rates are only known in arrears)."""
    rows: Dict[str, dict] = {}
    t = deal.terms
    for s, e, p in deal.fixed_periods:
        r = rows.setdefault(p.isoformat(), {"date": p.isoformat()})
        r.update(fixed_c=coupon_c(t.notional_c, deal.dealt, s, e, t.fixed_basis), fixed_period=[s.isoformat(), e.isoformat()], fixed_days=period_days(s, e, t.fixed_basis))
    for i, (s, e, p) in enumerate(deal.float_periods):
        r = rows.setdefault(p.isoformat(), {"date": p.isoformat()})
        if deal.compound:
            f = ONE
            for _n in range((e - s).days):
                f = compound_step(f, deal.fix_rate)
            amount, indicative = compounded_c(t.notional_c, f), True
        else:
            amount, indicative = coupon_c(t.notional_c, deal.fix_rate, s, e, t.float_basis), i > 0
        r.update(float_c=amount, float_period=[s.isoformat(), e.isoformat()], float_days=period_days(s, e, t.float_basis), float_rate=str(deal.fix_rate),
                 indicative=indicative)
    out = [rows[k] for k in sorted(rows)]
    for r in out:
        r.setdefault("fixed_c", None)
        r.setdefault("float_c", None)
    return out


def _fixing_dict(deal: Deal) -> dict:
    out = {"series": "SOFR", "rate": str(deal.fix_rate), "rate_pct": _pct(deal.fix_rate), "obs_date": deal.fix_obs.isoformat(), "sofr": str(deal.fix_pct),
           "reset_date": deal.float_periods[0][0].isoformat(), "method": TERM_METHOD, "fixing": deal.terms.fixing, "indicative": False}
    if deal.compound:
        out.update(method=COMPOUND_METHOD, indicative=True, note="the current SOFR, for the indicative schedule only: each night compounds at its own print")
    return out


def _deal_dict(deal: Deal) -> dict:
    t = deal.terms
    return {"kind": "IRS", "account_id": deal.acct["account_id"], "broker": prime.broker_of(deal.acct), "currency": t.currency, "notional_c": t.notional_c,
            "pay_fixed": t.pay_fixed, "side": "PAY_FIXED" if t.pay_fixed else "RECEIVE_FIXED", "tenor_months": t.tenor_months,
            "start_date": deal.start.isoformat(), "maturity_date": deal.maturity.isoformat(),
            "par_rate": str(deal.par), "par_rate_pct": _pct(deal.par), "par_rate_raw": str(deal.par_raw),
            "dealt_rate": str(deal.dealt), "dealt_rate_pct": _pct(deal.dealt), "fixed_rate_given": t.fixed_rate is not None,
            "half_width": str(deal.hw), "half_width_bp": str(deal.hw_bp), "otc_spread_mult": str(deal.mult), "annuity": str(deal.annuity),
            "mark0_c": deal.mark0_c, "cost_of_dealing_c": -deal.mark0_c if deal.mark0_c < 0 else 0, "dv01_c": deal.dv01_c,
            "dv01_note": "PV change for +1 bp (signed: a fixed payer gains when rates rise)", "im_c": deal.im_c, "im_bp": _num_text(deal.im_bp),
            "im_rule": f"IM = |DV01| x {_num_text(deal.im_bp)} bp ({deal.im_source})",
            "margin_mode": deal.margin_mode, "fees": "none beyond the dealer's half-width (disclosed)", "fixing_method": t.fixing,
            "fixing": _fixing_dict(deal), "schedule": _schedule_rows(deal), "terms": dict(t.to_dict(), fixed_rate=str(deal.dealt)),
            "inputs": dict(deal.curve_inputs, sofr=str(deal.fix_pct), sofr_obs_date=deal.fix_obs.isoformat(), remaining_periods=deal.n_periods,
                           rate_precision="0.001 % (0.1 bp)", model=MODEL, im_rule=f"IM = |DV01| x {_num_text(deal.im_bp)} bp ({deal.im_source})"),
            "conventions": list(CONVENTIONS)}


def _num_text(d) -> str:
    """50 -> "50", 37.5 -> "37.5" (a parameter shown as given)."""
    q = qprice(d)
    return str(q)


def preview(services, account_id: str, kind: str, terms: dict, conn: Optional[sqlite3.Connection] = None, now: Optional[int] = None) -> dict:
    """§13.11 ``otc.preview``: par rate, dealt rate (± the PB half-width), ``mark0`` (cost of dealing), DV01, IM, the
    schedules, the fixing, funding and conventions.  Pure (no writes); raises the same codes as ``otc.book``."""
    k = prime.require_otc_kind(kind)
    with _marks.shared_reader(services.db, conn) as c:        # ORC-6: one reader for the gate, the eligibility and the delegate
        return _preview(services, c, account_id, k, terms, now)


def _preview(services, conn, account_id: str, kind: str, terms: dict, now: Optional[int]) -> dict:
    acct = _pb_account(conn, account_id)
    require_pb_otc(services, account_id)
    if kind != "IRS":
        return _call(_delegate(services, kind, "preview"), services, account_id, kind, terms, conn=conn, now=now)
    t = parse_irs_terms(terms)
    ts = int(now if now is not None else services.clock.epoch())
    deal = _deal(services, conn, acct, t, ny_of(ts).date())
    out = _deal_dict(deal)
    out["funding"] = funding_preview(services, conn, account_id, t.currency, deal.im_c)
    try:
        out["eligibility"] = _eligibility(services, split_account_id(account_id)[0]).to_dict()
    except Exception:   # noqa: BLE001 — informational only; require_pb_otc already passed
        out["eligibility"] = None
    out["as_of"] = ts
    return out


# ---- booking ------------------------------------------------------------------------------------------------------------------------
def _insert_instrument(services, ctx: TxContext, cid: str, deal: Deal, broker: str) -> None:
    master = services.instruments
    insert = getattr(master, "insert", None) or getattr(master, "_insert")
    t = deal.terms
    name = f"IRS {'pay' if t.pay_fixed else 'receive'} fixed {_pct(deal.dealt)} {fmt_money(t.notional_c)} {t.currency} {deal.start.isoformat()} to {deal.maturity.isoformat()} ({broker})"
    insert(ctx.conn, ctx.ts, instrument_id=cid, kind="SWAP", symbol=cid, name=name, currency=t.currency, multiplier="1", tick="0.00001", lot=1, price_scale="1",
           session_group=SESSION_GROUP, settle_days=0, account_kinds=["PB"], expiry=deal.maturity.isoformat(),
           spec={"contract_id": cid, "otc_kind": "IRS", "broker": broker, "pay_fixed": t.pay_fixed, "fixed_rate": str(deal.dealt), "notional_c": t.notional_c,
                 "start_date": deal.start.isoformat(), "maturity_date": deal.maturity.isoformat(), "margin_mode": deal.margin_mode, "fixing": t.fixing},
           support="COMPUTED", status="ACTIVE")
    master.refresh(cid, ctx.conn)


def h_book(ctx: TxContext, payload: dict) -> dict:
    """``otc.book {account_id, kind: IRS, terms}``: re-prices the preview inside the transaction, funds and posts the IM,
    creates the contract, its SWAP instrument row and (TERM) the first fixing (module docstring IM / SWAP_MARK)."""
    services = _services(ctx)
    kind = prime.require_otc_kind(payload.get("kind"))
    aid = str(payload.get("account_id") or "")
    conn = ctx.conn
    acct = _pb_account(conn, aid)
    require_pb_otc(services, aid)
    if kind != "IRS":
        return _delegate(services, kind, "book")(ctx, payload)
    terms = parse_irs_terms(payload.get("terms") or {})
    deal = _deal(services, conn, acct, terms, ny_of(ctx.ts).date())
    broker = prime.broker_of(acct)
    cid = next_id(conn, "swap")
    ccy = terms.currency
    idem = f"im:{cid}:open"
    funding = fund_commitment(ctx, services, aid, ccy, deal.im_c, "otc", idem)           # PO-07: against available, never cash owed elsewhere
    memo = (f"OTC IM {cid}: IRS {'pay' if terms.pay_fixed else 'receive'} fixed {_pct(deal.dealt)} on {fmt_money(terms.notional_c)} {ccy} to {deal.maturity.isoformat()}"
            f" with {broker}; IM = |DV01| {fmt_money(abs(deal.dv01_c))} x {_num_text(deal.im_bp)} bp = {fmt_money(deal.im_c)} ({deal.im_source}); dealt at par"
            f" {_pct(deal.par)} {'+' if terms.pay_fixed else '-'} {deal.hw_bp} bp; mark0 {fmt_money(deal.mark0_c)} = cost of dealing (SWAP_MARK)")
    entry_id = ctx.post(Entry("OTC_IM", memo, [dr(aid, "1330", ccy, deal.im_c, instrument_id=cid, memo="OTC initial margin"), cr(aid, "1010", ccy, deal.im_c, memo="IM paid")],
                              idem, ref_type="otc", ref_id=cid))
    _insert_instrument(services, ctx, cid, deal, broker)
    t = terms.to_dict()
    t.update(fixed_rate=str(deal.dealt), fixed_rate_given=terms.fixed_rate is not None, par_rate=str(deal.par), half_width=str(deal.hw), half_width_bp=str(deal.hw_bp),
             otc_spread_mult=str(deal.mult), annuity0=str(deal.annuity), mark0_c=deal.mark0_c, dv01_0_c=deal.dv01_c, im_bp=_num_text(deal.im_bp), start_date=deal.start.isoformat(),
             maturity_date=deal.maturity.isoformat(), trade_date=ny_of(ctx.ts).date().isoformat(),
             fixed_periods=[[s.isoformat(), e.isoformat(), p.isoformat()] for s, e, p in deal.fixed_periods],
             float_periods=[[s.isoformat(), e.isoformat(), p.isoformat()] for s, e, p in deal.float_periods], dealt_ts=ctx.ts, model=MODEL)
    inputs = dict(deal.curve_inputs, as_of=ctx.ts, par_rate=str(deal.par), annuity=str(deal.annuity), remaining_periods=deal.n_periods, sofr=str(deal.fix_pct),
                  sofr_obs_date=deal.fix_obs.isoformat(), fixing=terms.fixing, model=MODEL)
    conn.execute("INSERT INTO otc_contracts(contract_id, command_id, account_id, broker, kind, terms_json, notional_c, currency, start_date, maturity_date, margin_mode, im_c,"
                 " status, last_reset_date, reset_px, mark_c, mark_ts, vm_balance_c, inputs_json, closed_ts) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,'OPEN',?,NULL,?,?,0,?,NULL)",
                 (cid, ctx.command_id, aid, broker, "IRS", dumps(t), terms.notional_c, ccy, deal.start.isoformat(), deal.maturity.isoformat(), deal.margin_mode, deal.im_c,
                  deal.float_periods[0][0].isoformat(), deal.mark0_c, ctx.ts, dumps(inputs)))
    fix_detail = dict(_fixing_dict(deal), period_start=deal.float_periods[0][0].isoformat(), period_end=deal.float_periods[0][1].isoformat(), source="booking")
    if not deal.compound:
        conn.execute("INSERT INTO otc_resets(contract_id, event_date, kind, amount_c, detail_json, entry_id) VALUES (?,?,'FIX',0,?,NULL)",
                     (cid, deal.float_periods[0][0].isoformat(), dumps(fix_detail)))
    ctx.touched_instruments.add((aid, cid))
    ctx.event("OTC_BOOKED", account_id=aid, instrument_id=cid, ref_type="otc", ref_id=cid, kind="IRS", broker=broker, notional_c=terms.notional_c, pay_fixed=terms.pay_fixed,
              dealt_rate=str(deal.dealt), par_rate=str(deal.par), mark0_c=deal.mark0_c, dv01_c=deal.dv01_c, im_c=deal.im_c, margin_mode=deal.margin_mode,
              maturity_date=deal.maturity.isoformat(), fixing=fix_detail)
    view = contract_view(services, _contract_row(conn, cid), conn=conn, detail=True, now_ts=ctx.ts)
    return {"contract": view, "contract_id": cid, "im_transfer": {"amount_c": deal.im_c, "funding": funding.to_dict() if hasattr(funding, "to_dict") else None},
            "entries": [entry_id], "preview": _deal_dict(deal)}


# ---- the mark ------------------------------------------------------------------------------------------------------------------------
def _remaining(row: dict, on: date) -> List[Period]:
    return [p for p in _periods(_terms(row), "fixed_periods") if p[2] > on]


def _model_mark(services, row: dict, now_ts: int, shift_bp=None, valuation_date: Optional[date] = None) -> Tuple[int, dict]:
    """The clean PV of an OPEN IRS valued as of ``valuation_date`` (default: today) on the curve available now — module
    docstring SWAP_MARK; raises ConflictError NO_CURVE."""
    terms = _terms(row)
    on = valuation_date or ny_of(int(now_ts)).date()
    remaining = _remaining(row, on)
    base = {"as_of": int(now_ts), "valuation_date": on.isoformat(), "contract_id": row["contract_id"], "dealt_rate": terms.get("fixed_rate"), "model": MODEL,
            "quality": "computed"}
    if row.get("status") != "OPEN":
        return 0, dict(base, note=f"{row.get('status')}: no remaining cash flows", remaining_periods=0)
    if not remaining:
        return 0, dict(base, note="no remaining payment periods (at maturity)", remaining_periods=0)
    dfs, inputs = dfs_for(services, len(remaining), shift_bp)
    par, annuity, par_raw = par_annuity(dfs)
    mark = model_mark_c(int(row["notional_c"]), bool(terms.get("pay_fixed")), terms.get("fixed_rate"), par, annuity)
    inputs.update(base, par_rate=str(par), par_rate_raw=str(par_raw), annuity=str(annuity), remaining_periods=len(remaining),
                  next_payment=remaining[0][2].isoformat(), last_reset_date=row.get("last_reset_date"),
                  formula=f"{'+' if terms.get('pay_fixed') else '-'}N x (par {par} - K {terms.get('fixed_rate')}) x A {annuity}")
    return mark, inputs


def _detail(r) -> dict:
    try:
        return json.loads(r["detail_json"] or "{}")
    except (ValueError, KeyError, TypeError):
        return {}


def mark(services, contract, at: Optional[int] = None, shift_bp=None, snapshot=None):
    """``(clean PV cents, inputs)`` for the contract (an id, a row or the SWAP instrument, as ``marks.py`` dispatches);
    ``MarkView`` labelled ``stale`` with the stored mark when no curve is available; None for an unknown contract.
    TRS / XCCY contracts are delegated to ``services.swaps_trs.mark_trs`` / ``mark_xccy``, the OTC options / forwards to
    ``services.otc_options.mark`` (C21; None while not bound); ``snapshot`` (one per view) goes along to the hooks."""
    row = _contract_dict(services, contract)
    if row is None:
        return None
    if row["kind"] != "IRS":
        fn = _mark_hook(services, row["kind"])
        return _call(fn, services, row, at=at, snapshot=snapshot) if fn is not None else None
    now_ts = int(at if at is not None else services.clock.epoch())
    try:
        return _model_mark(services, row, now_ts, shift_bp)
    except ConflictError as e:
        stored = int(row.get("mark_c") or 0)
        inputs = {"note": e.message, "stored_mark_c": stored, "stored_mark_ts": row.get("mark_ts"), "qualities": ["stale"], "as_of": row.get("mark_ts")}
        return MarkView(row["contract_id"], from_c(stored), "stale", "swap model (stored mark)", "swap_model", row.get("mark_ts"), now_ts, "model", inputs, False,
                        "no curve: the last computed mark, labelled stale (SWAP_MARK)")


def dv01_c(services, contract) -> int:
    """The signed PV change for +1 bp, ``sign × N × 1 bp × annuity`` of the remaining periods (raises ConflictError
    NO_CURVE; 0 when not OPEN / nothing remains)."""
    row = _contract_dict(services, contract)
    if row is None:
        raise RejectError("NOT_FOUND", "unknown contract")
    if row["kind"] != "IRS":
        fn = _hook(services, "dv01_c", row["kind"])
        return int(fn(services, row)) if fn is not None else 0
    if row.get("status") != "OPEN":
        return 0
    remaining = _remaining(row, ny_of(services.clock.epoch()).date())
    if not remaining:
        return 0
    dfs, _inputs = dfs_for(services, len(remaining))
    _par, annuity, _raw = par_annuity(dfs)
    return dv01_from(int(row["notional_c"]), annuity, bool(_terms(row).get("pay_fixed")))


# ---- jobs plumbing ------------------------------------------------------------------------------------------------------------------
def _job_done(ctx: TxContext, job: str, key: str, status: str, note: str) -> None:
    ctx.conn.execute("INSERT INTO jobs_done(job, key, run_ts, command_id, status, note) VALUES (?,?,?,?,?,?)"
                     " ON CONFLICT(job, key) DO UPDATE SET run_ts=excluded.run_ts, command_id=excluded.command_id, status=excluded.status, note=excluded.note",
                     (job, key, ctx.ts, ctx.command_id, status, (note or "")[:500]))


def _resolve_attention(ctx: TxContext, codes: Tuple[str, ...], ref_id: str) -> int:
    n = 0
    for code in codes:
        cur = ctx.conn.execute("UPDATE attention SET resolved_ts=? WHERE code=? AND ref_id=? AND resolved_ts IS NULL", (ctx.ts, code, ref_id))
        n += cur.rowcount or 0
    return n


def _skip(ctx: TxContext, job: str, key: str, code: str, message: str, account_id: str, cid: str, details: Optional[dict] = None, quiet: bool = False) -> dict:
    """A PENDING outcome: jobs_done SKIPPED (the key stays due), one open WARN attention per (code, key) unless ``quiet``,
    an event."""
    _job_done(ctx, job, key, "SKIPPED", f"{code}: {message}")
    attention_id = None
    if not quiet:
        existing = ctx.conn.execute("SELECT id FROM attention WHERE code=? AND ref_id=? AND resolved_ts IS NULL", (code, key)).fetchone()
        attention_id = int(existing["id"]) if existing else ctx.attention("WARN", SOURCE, code, message, dict(details or {}, account_id=account_id, ref_type="otc",
                                                                                                                ref_id=key, auto_resolve=1))
    ctx.event("JOB_SKIPPED", account_id=account_id, instrument_id=cid, ref_type="job", ref_id=key, job=job, code=code, message=message)
    return {"status": "SKIPPED", "skipped": True, "retry": True, "quiet": quiet, "code": code, "message": message, "key": key, "attention_id": attention_id}


def _done_noop(ctx: TxContext, job: str, key: str, code: str, note: str) -> dict:
    _job_done(ctx, job, key, "DONE", f"{code}: {note}")
    return {"status": "DONE", "skipped": True, "posted": False, "code": code, "note": note, "key": key}


def _key(payload: dict, what: str) -> Tuple[str, str]:
    cid = str(payload.get("contract_id") or "")
    ds = str(payload.get("date") or "")
    key = payload.get("key")
    if key and not (cid and ds):
        parts = str(key).rsplit(":", 1)
        if len(parts) == 2:
            cid, ds = parts
    try:
        d = date.fromisoformat(ds)
    except ValueError as e:
        raise RejectError("KEY_INVALID", f"{what} needs contract_id and an ISO date (got {ds!r})") from e
    if not cid:
        raise RejectError("KEY_INVALID", f"{what} needs a contract_id")
    return cid, d.isoformat()


def _reference_mark_c(conn: sqlite3.Connection, cid: str) -> int:
    """The VM reference (module docstring SWAP_VM): the mark of the latest VM, or the ``post_mark_c`` of the latest RESET
    row when its command ran after that VM's; 0 before the first VM.  Order = the command id (the RESET's journal entry's,
    else ``detail.command_id``), so a reset that posted no entry (STM with zero financing and no dividends) still re-bases
    the reference."""
    vm = conn.execute("SELECT v.mark_c, j.command_id FROM otc_vm v LEFT JOIN journal_entries j ON j.entry_id=v.entry_id WHERE v.contract_id=?"
                      " ORDER BY v.vm_date DESC LIMIT 1", (cid,)).fetchone()
    ref, ref_seq = (int(vm["mark_c"]), int(vm["command_id"] or 0)) if vm else (0, 0)
    r = conn.execute("SELECT r.detail_json, j.command_id FROM otc_resets r LEFT JOIN journal_entries j ON j.entry_id=r.entry_id WHERE r.contract_id=?"
                     " AND r.kind='RESET' ORDER BY r.event_date DESC LIMIT 1", (cid,)).fetchone()
    if r is not None:
        d = _detail(r)
        try:
            seq = int(r["command_id"] or d.get("command_id") or 0)
        except (TypeError, ValueError):
            seq = 0
        if "post_mark_c" in d and seq > ref_seq:
            ref = int(d["post_mark_c"] or 0)
    return ref


def _accepts(fn, name: str) -> bool:
    """True when the hook takes the keyword ``name`` (a hook written before it existed is called without it)."""
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False
    return name in params or any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values())


def _observed_after(inputs: dict, d: date) -> Optional[date]:
    """The latest date of an OBSERVED input of a TRS / XCCY mark that is after ``d`` (None when every input is dated on or
    before it): the underlying's quote, the FX quote, the USD par curve.  SWAP_VM then answers CAUGHT_UP, as for an IRS
    whose curve is dated after the VM date — the change is measured on a later VM date, never at a price of another day."""
    seen: List[date] = []

    def add_epoch(v) -> None:
        if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0:
            seen.append(ny_of(int(v)).date())

    def add_iso(v) -> None:
        if v:
            try:
                seen.append(date.fromisoformat(str(v)[:10]))
            except ValueError:
                pass
    add_epoch((inputs.get("underlying") or {}).get("quote_time"))
    add_epoch((inputs.get("fx") or {}).get("quote_time"))
    add_iso(inputs.get("curve_as_of"))
    for leg in (inputs.get("legs") or {}).values():
        if isinstance(leg, dict) and leg.get("currency") == "USD":
            add_iso(leg.get("curve_as_of"))
    later = [x for x in seen if x > d]
    return max(later) if later else None


def _open_contract(conn: sqlite3.Connection, cid: str) -> dict:
    row = _contract_row(conn, cid)
    if row is None:
        raise NotFound("NOT_FOUND", f"contract {cid} does not exist", {"contract_id": cid})
    return {k: row[k] for k in row.keys()}


def _vm_lines(aid: str, ccy: str, cid: str, mode: str, old_balance: int, delta: int) -> Tuple[List[Line], int, int]:
    """The §5.4 VM lines for ``delta``; returns ``(lines, new vm_balance_c, mark reset flag)``."""
    lines: List[Line] = []
    if mode == "CTM":
        new = old_balance + delta
        d1320 = max(0, -new) - max(0, -old_balance)
        d2330 = max(0, new) - max(0, old_balance)
        if d1320 < 0:
            lines += [dr(aid, "1010", ccy, -d1320, memo="VM posted returned by the PB"), cr(aid, "1320", ccy, -d1320, instrument_id=cid, memo="release of VM posted")]
        if d2330 < 0:
            lines += [dr(aid, "2330", ccy, -d2330, instrument_id=cid, memo="release of VM received"), cr(aid, "1010", ccy, -d2330, memo="VM received returned to the PB")]
        if d1320 > 0:
            lines += [dr(aid, "1320", ccy, d1320, instrument_id=cid, memo="VM posted (collateral, not P&L)"), cr(aid, "1010", ccy, d1320, memo="VM paid to the PB")]
        if d2330 > 0:
            lines += [dr(aid, "1010", ccy, d2330, memo="VM received from the PB"), cr(aid, "2330", ccy, d2330, instrument_id=cid, memo="VM received (collateral, not P&L)")]
        return lines, new, 0
    if delta > 0:
        lines = [dr(aid, "1010", ccy, delta, memo="STM settlement received"), cr(aid, "4450", ccy, delta, instrument_id=cid, memo="swap result (STM daily settlement)")]
    else:
        lines = [dr(aid, "4450", ccy, -delta, instrument_id=cid, memo="swap result (STM daily settlement)"), cr(aid, "1010", ccy, -delta, memo="STM settlement paid")]
    return lines, 0, 1


# ---- variation margin ---------------------------------------------------------------------------------------------------------------
def h_vm(ctx: TxContext, payload: dict) -> dict:
    """SWAP_VM for one (contract, date) — module docstring SWAP_VM.  Idempotent per date."""
    services = _services(ctx)
    conn = ctx.conn
    cid, ds = _key(payload, "swap.vm")
    key = f"{cid}:{ds}"
    idem = f"swapvm:{key}"
    row = _open_contract(conn, cid)
    aid, ccy, mode = row["account_id"], row["currency"], row["margin_mode"]
    if row["status"] != "OPEN":
        return _done_noop(ctx, JOB_VM, key, "NOT_OPEN", f"{cid} is {row['status']}: no variation margin")
    existing = conn.execute("SELECT * FROM otc_vm WHERE contract_id=? AND vm_date=?", (cid, ds)).fetchone()
    if existing is not None:
        _job_done(ctx, JOB_VM, key, "DONE", "already posted")
        return {"status": "DONE", "already": True, "skipped": True, "posted": False, "key": key, "mark_c": int(existing["mark_c"]), "delta_c": int(existing["delta_c"]),
                "entry_id": existing["entry_id"]}
    d = date.fromisoformat(ds)
    today = ny_of(ctx.ts).date()
    if d > today or d < _date(row["start_date"]):
        raise RejectError("KEY_INVALID", f"VM date {ds} is outside {row['start_date']} .. today {today.isoformat()}")
    later = conn.execute("SELECT MAX(vm_date) AS d FROM otc_vm WHERE contract_id=? AND vm_date>?", (cid, ds)).fetchone()
    if later is not None and later["d"]:
        _resolve_attention(ctx, VM_PENDING_CODES, key)
        return _done_noop(ctx, JOB_VM, key, "SUPERSEDED", f"the VM of {later['d']} already measured the change since the previous VM; nothing is posted for {ds}")
    if ctx.ts < epoch_of(d, VM_ET):
        return _skip(ctx, JOB_VM, key, "VM_NOT_YET", f"{cid}: the VM of {ds} runs from {VM_ET} ET", aid, cid, quiet=True)
    acct = account_row(conn, aid)
    if acct is None or acct["status"] != "OPEN":
        raise RejectError("ACCOUNT_CLOSED", f"account {aid} is not open")
    if row["kind"] != "IRS":
        fn = _mark_hook(services, row["kind"])
        if fn is None:
            return _skip(ctx, JOB_VM, key, "NOT_BUILT", f"{KIND_LABELS.get(row['kind'], row['kind'])} marks are not available in this version", aid, cid)
        # priced as of d: the underlying's / the pair's CLOSE of the last trading day on or before d when it is recorded or observed at
        # the close; a hook answers (None, {vm_outcome: CLOSE_PENDING | CAUGHT_UP, note, quiet}) when that close is not known (LC-3)
        got = _call(fn, services, row, valuation_date=d, at=ctx.ts, snapshot=ctx.marks)
        if isinstance(got, tuple) and got[0] is None and isinstance(got[1], dict) and got[1].get("vm_outcome"):
            return _vm_pending(ctx, row, key, ds, got[1])
        if got is None or not isinstance(got, tuple) or got[0] is None:
            return _skip(ctx, JOB_VM, key, "NO_MARK", f"{cid}: the {KIND_LABELS.get(row['kind'], row['kind'])} mark is unavailable", aid, cid)
        mark_c, inputs = int(got[0]), dict(got[1] or {})
        after = _observed_after(inputs, d)
        if after is not None:
            note = (f"CAUGHT_UP: the {row['kind']} inputs available are dated {after.isoformat()}, after {ds} (no close of {ds} was observed or recorded);"
                    f" nothing is posted for {ds}, the change is measured on a later VM date")
            _job_done(ctx, JOB_VM, key, "SKIPPED", note)
            _resolve_attention(ctx, ("NO_MARK", "NOT_BUILT"), key)
            return {"status": "SKIPPED", "skipped": True, "retry": False, "quiet": True, "code": "CAUGHT_UP", "note": note, "key": key, "posted": False}
    else:
        as_of = curve_as_of(services)
        if as_of is not None and as_of > d:
            note = (f"CAUGHT_UP: the curve available is as of {as_of.isoformat()}, after {ds} (that day's curve was never observed); nothing is posted for {ds},"
                    " the change is measured on the curve's own date")
            _job_done(ctx, JOB_VM, key, "SKIPPED", note)
            _resolve_attention(ctx, ("NO_CURVE",), key)
            return {"status": "SKIPPED", "skipped": True, "retry": False, "quiet": True, "code": "CAUGHT_UP", "note": note, "key": key, "posted": False}
        try:
            mark_c, inputs = _model_mark(services, row, ctx.ts, valuation_date=d)
        except ConflictError as e:
            return _skip(ctx, JOB_VM, key, "NO_CURVE", f"{cid}: {e.message}; the mark keeps its last value {fmt_money(int(row['mark_c']))} (stale)", aid, cid)
    ref_c = _reference_mark_c(conn, cid)
    delta = mark_c - ref_c
    if delta == 0:
        basis = (f"curve as of {inputs.get('curve_as_of')}" if row["kind"] == "IRS"
                 else f"valued at {(inputs.get('underlying') or inputs.get('fx') or {}).get('basis') or 'the close'}")
        note = f"NO_CHANGE: mark {fmt_money(mark_c)} unchanged vs the last VM ({basis})"
        _job_done(ctx, JOB_VM, key, "SKIPPED", note)
        _resolve_attention(ctx, VM_PENDING_CODES, key)
        return {"status": "SKIPPED", "skipped": True, "retry": False, "quiet": True, "code": "NO_CHANGE", "note": note, "key": key, "mark_c": mark_c, "delta_c": 0,
                "posted": False}
    funding = None
    if delta < 0:
        funding = execution.fund(ctx, aid, ccy, -delta, "swapvm", idem, None, immediate=True, overdraft=True, services=services)
    lines, new_balance, reset = _vm_lines(aid, ccy, cid, mode, int(row["vm_balance_c"]), delta)
    vm_basis = (f"par {inputs.get('par_rate')} vs dealt {inputs.get('dealt_rate')}, annuity {inputs.get('annuity')}, curve as of {inputs.get('curve_as_of')}"
                if row["kind"] == "IRS" else str(inputs.get("formula") or inputs.get("model") or KIND_LABELS.get(row["kind"], row["kind"])))
    memo = (f"Swap VM {cid} {ds} ({mode}): mark {fmt_money(mark_c)} - reference {fmt_money(ref_c)} = {fmt_money(delta)}"
            f"{' received' if delta > 0 else ' posted'}; {vm_basis}, valued as of {ds}; VM balance {fmt_money(int(row['vm_balance_c']))} -> {fmt_money(new_balance)} (SWAP_VM"
            f"{'; collateral, not P&L' if mode == 'CTM' else '; settled to market, mark reset to 0'})")
    entry_id = ctx.post(Entry("VM", memo, lines, idem, ref_type="otc", ref_id=cid, business_date=ds))
    conn.execute("INSERT INTO otc_vm(contract_id, vm_date, mark_c, delta_c, mode, entry_id, inputs_json) VALUES (?,?,?,?,?,?,?)",
                 (cid, ds, mark_c, delta, mode, entry_id, dumps(inputs)))
    new_mark = 0 if reset else mark_c
    conn.execute("UPDATE otc_contracts SET mark_c=?, mark_ts=?, vm_balance_c=?, inputs_json=? WHERE contract_id=?", (new_mark, ctx.ts, new_balance, dumps(inputs), cid))
    _job_done(ctx, JOB_VM, key, "DONE", f"{mode} {fmt_money(delta)} (mark {fmt_money(mark_c)})")
    _resolve_attention(ctx, VM_PENDING_CODES, key)
    ctx.touched_instruments.add((aid, cid))
    ctx.event("SWAP_VM", account_id=aid, instrument_id=cid, ref_type="otc", ref_id=cid, date=ds, mode=mode, mark_c=mark_c, reference_c=ref_c, delta_c=delta,
              vm_balance_c=new_balance, entry_id=entry_id, funded=None if funding is None else funding.funded)
    return {"status": "DONE", "key": key, "mode": mode, "mark_c": mark_c, "reference_c": ref_c, "delta_c": delta, "vm_balance_c": new_balance, "entry_id": entry_id,
            "posted": True, "funding": None if funding is None else (funding.to_dict() if hasattr(funding, "to_dict") else None), "inputs": inputs}


def _vm_pending(ctx: TxContext, row: dict, key: str, ds: str, info: dict) -> dict:
    """A VM date whose close is not known (LC-3): CAUGHT_UP (quiet, terminal: an observed price of a later session exists, so the
    change is measured on a later VM date) or CLOSE_PENDING (SKIPPED, retried; never a price dated before the VM date or an
    intraday print of it) - quiet on its first attempt and on non-trading days, one WARN attention once it persists."""
    cid, aid = row["contract_id"], row["account_id"]
    outcome = str(info.get("vm_outcome") or "CLOSE_PENDING")
    note = str(info.get("note") or outcome)
    if outcome == "CAUGHT_UP":
        text = f"CAUGHT_UP: {note}; nothing is posted for {ds}, the change is measured on a later VM date"
        _job_done(ctx, JOB_VM, key, "SKIPPED", text)
        _resolve_attention(ctx, VM_PENDING_CODES, key)
        return {"status": "SKIPPED", "skipped": True, "retry": False, "quiet": True, "code": "CAUGHT_UP", "note": text, "key": key, "posted": False}
    prev = ctx.conn.execute("SELECT note FROM jobs_done WHERE job=? AND key=?", (JOB_VM, key)).fetchone()
    repeat = prev is not None and str(prev["note"] or "").startswith(outcome)
    return _skip(ctx, JOB_VM, key, outcome, f"{cid}: {note}; the VM of {ds} waits for that close (never an earlier or intraday price)", aid, cid,
                 {"close_date": info.get("close_date")}, quiet=bool(info.get("quiet")) or not repeat)


# ---- fixings ------------------------------------------------------------------------------------------------------------------------
def _fix_for_period(conn: sqlite3.Connection, cid: str, s: date, e: date):
    """The FIX row of the floating period ``[s, e)`` (None when it has not been fixed)."""
    return conn.execute("SELECT event_date, detail_json FROM otc_resets WHERE contract_id=? AND kind='FIX' AND event_date>=? AND event_date<? ORDER BY event_date DESC LIMIT 1",
                        (cid, s.isoformat(), e.isoformat())).fetchone()


def h_fix(ctx: TxContext, payload: dict) -> dict:
    """SWAP_FIX for one (contract, reset date) — module docstring SWAP_RESET."""
    services = _services(ctx)
    conn = ctx.conn
    cid, ds = _key(payload, "swap.fix")
    key = f"{cid}:{ds}"
    row = _open_contract(conn, cid)
    aid = row["account_id"]
    if row["status"] != "OPEN":
        return _done_noop(ctx, JOB_FIX, key, "NOT_OPEN", f"{cid} is {row['status']}")
    if row["kind"] != "IRS":
        fn = _hook(services, "fix", row["kind"])
        if fn is not None:
            return fn(ctx, payload)
        return _done_noop(ctx, JOB_FIX, key, "NOT_IRS", f"{KIND_LABELS.get(row['kind'], row['kind'])}s have no interest-rate reset fixing")
    flt = _float_leg(row)
    if flt.fixing != "TERM":
        return _done_noop(ctx, JOB_FIX, key, "COMPOUND_ARREARS", f"{cid} compounds SOFR in arrears: no reset fixing ({COMPOUND_METHOD})")
    d = date.fromisoformat(ds)
    period = next((p for p in flt.periods if p[0] == d), None)
    if period is None:
        raise RejectError("KEY_INVALID", f"{ds} is not a floating reset date of {cid}")
    existing = conn.execute("SELECT detail_json FROM otc_resets WHERE contract_id=? AND event_date=? AND kind='FIX'", (cid, ds)).fetchone()
    if existing is not None:
        _job_done(ctx, JOB_FIX, key, "DONE", "already fixed")
        return {"status": "DONE", "already": True, "posted": False, "key": key, "fixing": _detail(existing)}
    try:
        detail = _record_fix(ctx, services, row, period)
    except ConflictError as e:
        return _skip(ctx, JOB_FIX, key, "RATE_UNAVAILABLE", e.message, aid, cid)
    return {"status": "DONE", "key": key, "posted": False, "fixing": detail}


def _record_fix(ctx: TxContext, services, row: dict, period: Period, source: str = "SWAP_FIX") -> dict:
    """Record the TERM fixing of the floating period ``period`` (module docstring SWAP_RESET): the latest SOFR print on or before
    its reset date (RATE_LAG); its SWAP_FIX key is DONE.  Raises ConflictError RATE_UNAVAILABLE before any write.  Used by the
    SWAP_FIX job and by a termination that catches up a period the scheduler has not fixed yet (MONEY-2)."""
    conn = ctx.conn
    cid, aid = row["contract_id"], row["account_id"]
    s = period[0]
    ds = s.isoformat()
    key = f"{cid}:{ds}"
    obs, pct = fixing_for(services, s)
    rate = _rate_of(pct)
    detail = {"rate": str(rate), "rate_pct": _pct(rate), "obs_date": obs.isoformat(), "series": "SOFR", "sofr": str(pct), "period_start": period[0].isoformat(),
              "period_end": period[1].isoformat(), "source": source, "method": TERM_METHOD, "fixing": "TERM"}
    conn.execute("INSERT INTO otc_resets(contract_id, event_date, kind, amount_c, detail_json, entry_id) VALUES (?,?,'FIX',0,?,NULL)", (cid, ds, dumps(detail)))
    conn.execute("UPDATE otc_contracts SET last_reset_date=? WHERE contract_id=?", (ds, cid))
    _job_done(ctx, JOB_FIX, key, "DONE", f"SOFR {pct} % ({obs.isoformat()}) -> {_pct(rate)}" + ("" if source == "SWAP_FIX" else f" ({source})"))
    _resolve_attention(ctx, ("RATE_UNAVAILABLE",), key)
    ctx.event("SWAP_FIX", account_id=aid, instrument_id=cid, ref_type="otc", ref_id=cid, date=ds, fixing=detail)
    return detail


# ---- accruals (ACCRUE_DAILY) ------------------------------------------------------------------------------------------------------------
def _state(conn: sqlite3.Connection, kind: str, ref: str):
    return conn.execute("SELECT * FROM accrual_state WHERE kind=? AND ref_id=?", (kind, ref)).fetchone()


def _put_state(conn: sqlite3.Connection, kind: str, ref: str, segment_start: str, base_c: int, rate: str, through: str, cum_c: int) -> None:
    conn.execute("INSERT INTO accrual_state(kind, ref_id, segment_start, base_c, rate, day_basis, accrued_through, cum_c) VALUES (?,?,?,?,?,360,?,?)"
                 " ON CONFLICT(kind, ref_id) DO UPDATE SET segment_start=excluded.segment_start, base_c=excluded.base_c, rate=excluded.rate,"
                 " day_basis=excluded.day_basis, accrued_through=excluded.accrued_through, cum_c=excluded.cum_c",
                 (kind, ref, segment_start, int(base_c), rate, through, int(cum_c)))


def _accrue_segment_fn(services):
    lc = getattr(services, "lifecycle", None)
    fn = getattr(lc, "accrue_segment", None) if lc is not None else None
    if fn is None:
        from ..lifecycle_jobs import accrue_segment as fn   # noqa: WPS433 — the core helper when no scheduler is bound
    return fn


def _leg_lines(aid: str, cid: str, spec: LegSpec, amount: int) -> List[Line]:
    gl_dr, gl_cr = LEG_GL[spec.kind]
    what = f"{spec.leg} leg accrued ({spec.side})"
    if amount > 0:
        return [dr(aid, gl_dr, spec.currency, amount, instrument_id=cid, memo=what), cr(aid, gl_cr, spec.currency, amount, instrument_id=cid, memo=what)]
    if amount < 0:
        return [dr(aid, gl_cr, spec.currency, -amount, instrument_id=cid, memo="accrual true-up (reversal)"),
                cr(aid, gl_dr, spec.currency, -amount, instrument_id=cid, memo="accrual true-up (reversal)")]
    return []


def accrue_leg(ctx: TxContext, services, row: dict, spec: LegSpec, night: date) -> dict:
    """One leg's accrual for ``night`` (module docstring ACCRUAL): persists ``accrual_state`` and returns the lines (in the
    leg's currency), the amount and a memo; raises RejectError FIX_PENDING / RATE_UNAVAILABLE / ACCRUAL_GAP before any
    write."""
    conn = ctx.conn
    cid, aid = row["contract_id"], row["account_id"]
    kind, notional_c = spec.kind, int(spec.notional_c)
    night_s = night.isoformat()
    st = _state(conn, kind, cid)
    if st is not None and st["accrued_through"] and st["accrued_through"] >= night_s:
        return {"lines": [], "amount_c": 0, "already": True, "leg": spec.leg, "note": f"night {night_s} already accrued", "rate": None}
    if st is not None and st["accrued_through"]:
        nxt = (_date(st["accrued_through"]) + timedelta(days=1)).isoformat()
        if nxt != night_s:
            raise RejectError("ACCRUAL_GAP", f"{kind} {cid}: night {night_s} requested but {nxt} is the next unaccrued night (accruals post in date order)")
    period = _period_containing(spec.periods, night)
    if period is None:
        return {"lines": [], "amount_c": 0, "leg": spec.leg, "note": f"night {night_s} is outside the contract's periods", "rate": None, "outside": True}
    s, e, _p = period
    last = night == e - timedelta(days=1)
    if spec.fixing == "COMPOUND_ARREARS":
        obs, pct = fixing_for(services, night)                     # 409 RATE_UNAVAILABLE -> the ACCRUE_DAILY key SKIPs, retried; nothing written
        r = qprice(Decimal(pct) / Decimal(100))
        same = st is not None and st["segment_start"] == s.isoformat() and night != s
        f_prev = Decimal(str(st["rate"])) if same else ONE
        cum_prev = int(st["cum_c"]) if same else 0
        f_new = compound_step(f_prev, r)
        cum_new = compounded_c(notional_c, f_new)
        amount = cum_new - cum_prev
        _put_state(conn, kind, cid, s.isoformat(), notional_c, str(f_new), night_s, cum_new)
        nights = (night - s).days + 1
        memo = (f"Swap {cid} {spec.leg} leg ({spec.side}) night of {night_s}: SOFR {pct} % ({obs.isoformat()}) compounded daily, factor {f_prev} x (1 + {r}/360) = {f_new}"
                f" after {nights} nights; cumulative to_c({fmt_money(notional_c)} x (F - 1)) = {fmt_money(cum_new)}; period {s.isoformat()}..{e.isoformat()}"
                " (ACCRUAL, RATE_LAG, COMPOUND_ARREARS)")
        return {"lines": _leg_lines(aid, cid, spec, amount), "amount_c": amount, "cum_c": cum_new, "coupon_c": cum_new if last else None, "leg": spec.leg, "rate": str(r),
                "basis": spec.basis, "period": [s.isoformat(), e.isoformat()], "memo": memo, "last": last, "factor": str(f_new), "obs_date": obs.isoformat(), "sofr": str(pct)}
    fix_detail = None
    if spec.fixing == "TERM":
        fx = _fix_for_period(conn, cid, s, e)
        if fx is None or _date(fx["event_date"]) > night:
            raise RejectError("FIX_PENDING", f"{cid}: the floating period from {s.isoformat()} has no fixing yet (SWAP_FIX {s.isoformat()} runs first)",
                              {"contract_id": cid, "period_start": s.isoformat()})
        fix_detail = _detail(fx)
        rate = qprice(fix_detail.get("rate"))
    else:
        if spec.rate is None:
            raise RejectError("TERMS", f"{cid}: the fixed leg carries no rate")
        rate = qprice(spec.rate)
    rate_s = str(rate)
    if night == s and st is not None:
        conn.execute("UPDATE accrual_state SET segment_start=?, cum_c=0, base_c=?, rate=?, day_basis=360 WHERE kind=? AND ref_id=?", (night_s, notional_c, rate_s, kind, cid))
        st = _state(conn, kind, cid)
    coupon = coupon_c(notional_c, rate, s, e, spec.basis)
    if last:
        same = st is not None and int(st["base_c"]) == notional_c and str(st["rate"]) == rate_s and int(st["day_basis"]) == 360
        cum_prev = int(st["cum_c"]) if same else 0
        seg_start = st["segment_start"] if same else night_s
        amount = coupon - cum_prev
        _put_state(conn, kind, cid, seg_start, notional_c, rate_s, night_s, coupon)
        cum_new = coupon
        how = f"last night of the period: true-up to the {spec.basis} coupon {fmt_money(coupon)} (cumulative before {fmt_money(cum_prev)})"
    else:
        amount = int(_accrue_segment_fn(services)(ctx, kind, cid, notional_c, rate, 360, night))
        st2 = _state(conn, kind, cid)
        cum_new = int(st2["cum_c"]) if st2 is not None else amount
        n = (night - _date(st2["segment_start"])).days + 1 if st2 is not None else 1
        how = f"night {n} of the segment: cumulative to_c(N x rate x {n} / 360) = {fmt_money(cum_new)}"
    memo = (f"Swap {cid} {spec.leg} leg ({spec.side}) night of {night_s}: {fmt_money(notional_c)} x {_pct(rate)} / 360 ({spec.basis}), {how};"
            f" period {s.isoformat()}..{e.isoformat()}" + (f", SOFR fixing {fix_detail.get('sofr')} % ({fix_detail.get('obs_date')})" if fix_detail else "") + " (ACCRUAL, RATE_LAG)")
    return {"lines": _leg_lines(aid, cid, spec, amount), "amount_c": amount, "cum_c": cum_new, "coupon_c": coupon, "leg": spec.leg, "rate": rate_s, "basis": spec.basis,
            "period": [s.isoformat(), e.isoformat()], "memo": memo, "last": last}


def accrual_lines_swap(ctx: TxContext, contract, d, rates=None, kinds: Optional[Tuple[str, ...]] = None) -> List[Line]:
    """§2.12: the accrual lines of night ``d`` for the swap's legs (both by default); persists each leg's segment state.
    ``rates`` is accepted for the design's signature — fixings come from the contract's recorded resets / SOFR prints."""
    services = _services(ctx)
    row = _contract_dict(services, contract, ctx.conn)
    if row is None:
        raise RejectError("NOT_FOUND", "unknown contract")
    specs = leg_specs(services, row)
    out: List[Line] = []
    for kind in (kinds or tuple(sorted(specs))):
        spec = specs.get(kind)
        if spec is None:
            raise RejectError("KIND_UNKNOWN", f"{row['contract_id']} has no {kind} leg")
        out += accrue_leg(ctx, services, row, spec, _date(d))["lines"]
    return out


def accrue_night(ctx: TxContext, services, kind: str, ref: str, night) -> dict:
    """The ACCRUE_DAILY hook (``services.swaps.accrue_night``) for kinds ``swaprcv`` / ``swappay``: posts one leg's night
    under ``accr:{kind}:{contract}:{night}``.  XCCY legs: through ``swaps_trs.leg_specs`` when WP-26 provides it, else
    ``swaps_trs.accrue_night``."""
    if kind not in LEG_GL:
        raise RejectError("KIND_UNKNOWN", f"accrual kind {kind!r} is not a swap leg (swaprcv / swappay)")
    row = _open_contract(ctx.conn, ref)
    if row["kind"] != "IRS" and _hook(services, "leg_specs", row["kind"]) is None:
        fn = _hook(services, "accrue_night", row["kind"])
        if fn is None:
            raise RejectError("NOT_IN_THIS_BUILD", f"{KIND_LABELS.get(row['kind'], row['kind'])} leg accruals are not available in this version")
        return fn(ctx, services, kind, ref, night)
    night_d = _date(night)
    night_s = night_d.isoformat()
    if row["status"] != "OPEN":
        return {"status": "DONE", "note": f"{ref} is {row['status']}: nothing accrues", "posted": False, "posted_c": 0}
    spec = leg_specs(services, row).get(kind)
    if spec is None:
        return {"status": "DONE", "note": f"{ref} has no {kind} leg", "posted": False, "posted_c": 0}
    res = accrue_leg(ctx, services, row, spec, night_d)
    entry_id = None
    amount = int(res["amount_c"])
    if res["lines"]:
        entry_id = ctx.post(Entry("ACCRUAL", res["memo"], res["lines"], f"accr:{kind}:{ref}:{night_s}", ref_type="otc", ref_id=ref))
        ctx.touched_instruments.add((row["account_id"], ref))
        ctx.event("SWAP_ACCRUAL", account_id=row["account_id"], instrument_id=ref, ref_type="otc", ref_id=ref, kind=kind, leg=res["leg"], night=night_s, amount_c=amount,
                  rate=res["rate"], factor=res.get("factor"), entry_id=entry_id)
    note = (f"{res['leg']} leg {fmt_money(amount)} at {res['rate']} night of {night_s}" if res.get("rate") else res.get("note", ""))
    return {"status": "DONE", "posted": entry_id is not None, "posted_c": abs(amount), "amount_c": amount, "entry_id": entry_id, "leg": res["leg"], "rate": res.get("rate"),
            "cum_c": res.get("cum_c"), "factor": res.get("factor"), "note": note}


# ---- payments, maturity, termination ----------------------------------------------------------------------------------------------------
def _posted_leg_c(conn: sqlite3.Connection, account_id: str, cid: str, spec: LegSpec, period: Period) -> int:
    """The natural-sign sum of a leg's accrual postings over the nights of ``period`` (``accr:{kind}:{cid}:{night}``)."""
    s, e, _p = period
    lo, hi = f"accr:{spec.kind}:{cid}:{s.isoformat()}", f"accr:{spec.kind}:{cid}:{(e - timedelta(days=1)).isoformat()}"
    r = conn.execute("SELECT COALESCE(SUM(l.amount_c), 0) AS s FROM journal_lines l JOIN journal_entries j ON j.entry_id=l.entry_id"
                     " WHERE j.idem_key>=? AND j.idem_key<=? AND l.account_id=? AND l.gl=? AND l.currency=? AND l.instrument_id=?",
                     (lo, hi, account_id, spec.gl, spec.currency, cid)).fetchone()
    return natural(spec.gl, int(r["s"] or 0))


def leg_coupon_c(conn: sqlite3.Connection, row: dict, spec: LegSpec, period: Period) -> Tuple[Optional[int], Optional[str]]:
    """``(amount, rate)`` of a leg's coupon for ``period``: FIXED / TERM = the period coupon on the leg's basis (None while a
    TERM period is not fixed); COMPOUND_ARREARS = the period's posted accruals (rate = the realised annualised rate)."""
    s, e, _p = period
    if spec.fixing == "COMPOUND_ARREARS":
        amount = _posted_leg_c(conn, row["account_id"], row["contract_id"], spec, period)
        days = (e - s).days
        realised = (from_c(amount) * DAYS_360 / (from_c(int(spec.notional_c)) * Decimal(days))).quantize(RATE_8) if days and spec.notional_c else None
        return amount, None if realised is None else str(realised)
    if spec.fixing == "TERM":
        fx = _fix_for_period(conn, row["contract_id"], s, e)
        if fx is None:
            return None, None
        rate = qprice(_detail(fx).get("rate"))
    else:
        rate = qprice(spec.rate)
    return coupon_c(int(spec.notional_c), rate, s, e, spec.basis), str(rate)


def _unwind_lines(aid: str, ccy: str, cid: str, row: dict, value_c: int, ref_c: int) -> Tuple[List[Line], int, int]:
    """The close-out lines: VM balance unwound (CTM) / value since the last settle (STM) and the termination value.
    Returns ``(lines, cash_c, result_c)`` with ``cash_c`` the net cash movement (+ received)."""
    lines: List[Line] = []
    cash = 0
    if row["margin_mode"] == "CTM":
        vb = int(row["vm_balance_c"])
        if vb > 0:
            lines += [dr(aid, "2330", ccy, vb, instrument_id=cid, memo="VM received unwound"), cr(aid, "1010", ccy, vb, memo="VM received returned to the PB")]
            cash -= vb
        elif vb < 0:
            lines += [dr(aid, "1010", ccy, -vb, memo="VM posted returned by the PB"), cr(aid, "1320", ccy, -vb, instrument_id=cid, memo="VM posted unwound")]
            cash += -vb
        result = value_c
    else:
        result = value_c - ref_c
    if result > 0:
        lines += [dr(aid, "1010", ccy, result, memo="termination value received"), cr(aid, "4450", ccy, result, instrument_id=cid, memo="swap result (termination value)")]
        cash += result
    elif result < 0:
        lines += [dr(aid, "4450", ccy, -result, instrument_id=cid, memo="swap result (termination value)"), cr(aid, "1010", ccy, -result, memo="termination value paid")]
        cash += result
    return lines, cash, result


def close_contract(ctx: TxContext, services, row: dict, status: str, value_c: int, ref_c: int, idem: str, kind: str, memo: str, settle_accrued: bool,
                   overdraft: bool) -> dict:
    """Close a contract (module docstring Termination / SWAP_RESET maturity): one entry for the VM unwind, the value and
    (optionally) the accrued legs, the IM release, status TERMINATED / MATURED, accrual state removed, the SWAP row
    EXPIRED.  Public for WP-26's maturities."""
    conn = ctx.conn
    cid, aid, ccy = row["contract_id"], row["account_id"], row["currency"]
    lines, cash, result = _unwind_lines(aid, ccy, cid, row, value_c, ref_c)
    recv_c = pay_c = 0
    if settle_accrued:
        recv_c = balance(conn, aid, "1225", ccy, cid)
        pay_c = balance(conn, aid, "2340", ccy, cid)
        if pay_c:
            lines.append(dr(aid, "2340", ccy, pay_c, instrument_id=cid, memo="accrued pay leg settled"))
        if recv_c:
            lines.append(cr(aid, "1225", ccy, recv_c, instrument_id=cid, memo="accrued receive leg settled"))
        net = recv_c - pay_c
        if net > 0:
            lines.append(dr(aid, "1010", ccy, net, memo="accrued coupons net received"))
        elif net < 0:
            lines.append(cr(aid, "1010", ccy, -net, memo="accrued coupons net paid"))
        cash += net
    funding = None
    if cash < 0 and overdraft:              # a lifecycle obligation (maturity): settled cash, the overdraft rule (s11.6)
        funding = execution.fund(ctx, aid, ccy, -cash, "otc", idem, None, immediate=True, overdraft=True, services=services)
    elif cash < 0:                          # the owner's termination: against available cash (PO-07), else 409 INSUFFICIENT_FUNDS
        funding = fund_commitment(ctx, services, aid, ccy, -cash, "otc", idem)
    entry_id = None
    if lines:
        entry_id = ctx.post(Entry(kind, memo, lines, idem, ref_type="otc", ref_id=cid))
    im = int(row["im_c"])
    im_entry = None
    if im > 0:
        im_entry = ctx.post(Entry("OTC_IM", f"OTC IM released {cid}: {fmt_money(im)} ({status.lower()})",
                                  [dr(aid, "1010", ccy, im, memo="IM returned"), cr(aid, "1330", ccy, im, instrument_id=cid, memo="OTC initial margin released")],
                                  f"im:{cid}:release", ref_type="otc", ref_id=cid))
    conn.execute("UPDATE otc_contracts SET status=?, closed_ts=?, mark_c=0, mark_ts=?, vm_balance_c=0, im_c=0 WHERE contract_id=?", (status, ctx.ts, ctx.ts, cid))
    conn.execute("DELETE FROM accrual_state WHERE kind IN ('swaprcv','swappay') AND ref_id=?", (cid,))
    conn.execute("UPDATE instruments SET status='EXPIRED', updated_ts=? WHERE instrument_id=?", (ctx.ts, cid))
    try:
        services.instruments.refresh(cid, conn)
    except Exception:   # noqa: BLE001 — a stale cache entry is refreshed on the next load
        pass
    ctx.touched_instruments.add((aid, cid))
    return {"entry_id": entry_id, "im_entry_id": im_entry, "value_c": value_c, "result_c": result, "cash_c": cash, "accrued_receive_c": recv_c, "accrued_pay_c": pay_c,
            "im_released_c": im, "funding": None if funding is None else (funding.to_dict() if hasattr(funding, "to_dict") else None)}


_close = close_contract


def h_pay(ctx: TxContext, payload: dict) -> dict:
    """SWAP_PAY for one (contract, payment date) — module docstring SWAP_RESET; the last payment matures the contract."""
    services = _services(ctx)
    conn = ctx.conn
    cid, ds = _key(payload, "swap.pay")
    key = f"{cid}:{ds}"
    idem = f"swappay:{key}"
    row = _open_contract(conn, cid)
    aid, ccy = row["account_id"], row["currency"]
    if row["status"] != "OPEN":
        return _done_noop(ctx, JOB_PAY, key, "NOT_OPEN", f"{cid} is {row['status']}")
    if row["kind"] != "IRS":
        fn = _hook(services, "pay", row["kind"])
        if fn is not None:
            return fn(ctx, payload)
        return _skip(ctx, JOB_PAY, key, "NOT_BUILT", f"{KIND_LABELS.get(row['kind'], row['kind'])} payments are not available in this version", aid, cid)
    existing = conn.execute("SELECT detail_json FROM otc_resets WHERE contract_id=? AND event_date=? AND kind='PAY'", (cid, ds)).fetchone()
    if existing is not None:
        _job_done(ctx, JOB_PAY, key, "DONE", "already paid")
        return {"status": "DONE", "already": True, "posted": False, "key": key, "payment": _detail(existing)}
    d = date.fromisoformat(ds)
    paying = [(spec, p) for spec in irs_leg_specs(row).values() for p in spec.periods if p[2] == d]
    if not paying:
        raise RejectError("KEY_INVALID", f"{ds} is not a payment date of {cid}")
    if ctx.ts < epoch_of(d, PAY_ET):
        return _skip(ctx, JOB_PAY, key, "PAY_NOT_YET", f"{cid}: the payment of {ds} runs from {PAY_ET} ET", aid, cid, quiet=True)
    legs: Dict[str, dict] = {}
    for spec, p in paying:
        st = _state(conn, spec.kind, cid)
        through = st["accrued_through"] if st is not None else None
        last_night = (p[1] - timedelta(days=1)).isoformat()
        if through is None or through < last_night:
            return _skip(ctx, JOB_PAY, key, "ACCRUALS_PENDING", f"{cid}: the {spec.leg} leg is accrued through {through or 'nothing'}, the period ends {p[1].isoformat()}"
                         " (ACCRUE_DAILY first)", aid, cid, {"leg": spec.leg, "accrued_through": through, "needed": last_night})
        amount, rate = leg_coupon_c(conn, row, spec, p)
        if amount is None:
            return _skip(ctx, JOB_PAY, key, "FIX_PENDING", f"{cid}: no fixing for the floating period from {p[0].isoformat()}", aid, cid)
        have = balance(conn, aid, spec.gl, spec.currency, cid)
        if have < amount:
            return _skip(ctx, JOB_PAY, key, "ACCRUALS_PENDING", f"{cid}: {spec.leg} leg accrued {fmt_money(have)} in {spec.gl} but the period coupon is {fmt_money(amount)}",
                         aid, cid)
        legs[spec.leg] = {"kind": spec.kind, "gl": spec.gl, "amount_c": amount, "rate": rate, "basis": spec.basis, "fixing": spec.fixing,
                          "period": [p[0].isoformat(), p[1].isoformat()], "days": period_days(p[0], p[1], spec.basis)}
    receive_c = sum(v["amount_c"] for v in legs.values() if v["kind"] == "swaprcv")
    pay_c = sum(v["amount_c"] for v in legs.values() if v["kind"] == "swappay")
    net = receive_c - pay_c
    funding = None
    if net < 0:
        funding = execution.fund(ctx, aid, ccy, -net, "swappay", idem, None, immediate=True, overdraft=True, services=services)
    lines: List[Line] = []
    if pay_c:
        lines.append(dr(aid, "2340", ccy, pay_c, instrument_id=cid, memo="pay leg coupon settled"))
    if receive_c:
        lines.append(cr(aid, "1225", ccy, receive_c, instrument_id=cid, memo="receive leg coupon settled"))
    if net > 0:
        lines.append(dr(aid, "1010", ccy, net, memo="net coupon received"))
    elif net < 0:
        lines.append(cr(aid, "1010", ccy, -net, memo="net coupon paid"))
    memo = (f"Swap payment {cid} {ds}: receive {fmt_money(receive_c)} / pay {fmt_money(pay_c)} / net {fmt_money(net)}; "
            + "; ".join(f"{leg} {v['rate']} x {v['days']} days ({v['basis']}{', compounded in arrears' if v['fixing'] == 'COMPOUND_ARREARS' else ''})"
                        f" = {fmt_money(v['amount_c'])}" for leg, v in legs.items()) + " (SWAP_RESET)")
    entry_id = ctx.post(Entry("SWAP_PAY", memo, lines, idem, ref_type="otc", ref_id=cid, business_date=ds)) if lines else None
    detail = {"receive_c": receive_c, "pay_c": pay_c, "net_c": net, "legs": legs, "entry_id": entry_id}
    conn.execute("INSERT INTO otc_resets(contract_id, event_date, kind, amount_c, detail_json, entry_id) VALUES (?,?,'PAY',?,?,?)", (cid, ds, net, dumps(detail), entry_id))
    ctx.touched_instruments.add((aid, cid))
    out = {"status": "DONE", "key": key, "receive_c": receive_c, "pay_c": pay_c, "net_c": net, "entry_id": entry_id, "legs": legs, "posted": entry_id is not None,
           "funding": None if funding is None else (funding.to_dict() if hasattr(funding, "to_dict") else None)}
    if ds == row["maturity_date"]:
        row = _open_contract(conn, cid)
        closed = close_contract(ctx, services, row, "MATURED", 0, _reference_mark_c(conn, cid), f"otc:{cid}:mature", "OTC_MATURE",
                                f"Swap maturity {cid} {ds}: VM balance {fmt_money(int(row['vm_balance_c']))} unwound, IM {fmt_money(int(row['im_c']))} released (SWAP_RESET)",
                                False, True)
        out["matured"] = closed
        ctx.event("SWAP_MATURED", account_id=aid, instrument_id=cid, ref_type="otc", ref_id=cid, date=ds, **{k: v for k, v in closed.items() if k != "funding"})
    _job_done(ctx, JOB_PAY, key, "DONE", f"net {fmt_money(net)} (receive {fmt_money(receive_c)}, pay {fmt_money(pay_c)})" + ("; matured" if "matured" in out else ""))
    _resolve_attention(ctx, ("ACCRUALS_PENDING", "FIX_PENDING"), key)
    ctx.event("SWAP_PAY", account_id=aid, instrument_id=cid, ref_type="otc", ref_id=cid, date=ds, receive_c=receive_c, pay_c=pay_c, net_c=net, entry_id=entry_id)
    return out


def _irs_catch_up(ctx: TxContext, services, row: dict, through: date) -> List[dict]:
    """MONEY-2: post every night an IRS owes through ``through`` (the night before the termination, at most the night before
    maturity) on both legs, exactly as ACCRUE_DAILY would (``accrue_night``: the same ``accr:{kind}:{contract}:{night}`` entries),
    before the termination settles the accrued legs.  A TERM period the scheduler has not fixed yet is fixed first from its SOFR
    print (``_record_fix``); a night that still cannot accrue answers 409 with the reason (FIX_PENDING / RATE_UNAVAILABLE) and
    nothing is written - a termination never drops a coupon owed."""
    conn = ctx.conn
    cid = row["contract_id"]
    start = _date(row["start_date"])
    posted: List[dict] = []
    specs = irs_leg_specs(row)
    for kind in sorted(specs):
        spec = specs[kind]
        st = _state(conn, kind, cid)
        night = start if st is None or not st["accrued_through"] else _date(st["accrued_through"]) + timedelta(days=1)
        while night <= through:
            try:
                try:
                    res = accrue_night(ctx, services, kind, cid, night)
                except RejectError as e:
                    period = _period_containing(spec.periods, night)
                    if e.code != "FIX_PENDING" or spec.fixing != "TERM" or period is None:
                        raise
                    _record_fix(ctx, services, row, period, source="termination catch-up")
                    res = accrue_night(ctx, services, kind, cid, night)
            except RejectError as e:
                raise ConflictError(e.code, f"{cid}: the termination settles every coupon owed through the night of {through.isoformat()}, but the night of"
                                    f" {night.isoformat()} cannot accrue yet: {e.message}", dict(e.details or {}, night=night.isoformat())) from e
            if res.get("entry_id") is not None:
                posted.append({"kind": kind, "leg": res.get("leg"), "night": night.isoformat(), "amount_c": int(res.get("amount_c") or 0), "entry_id": res["entry_id"]})
            night += timedelta(days=1)
    return posted


def h_terminate(ctx: TxContext, payload: dict) -> dict:
    """``otc.terminate {contract_id}`` (owner): close out at the current model mark — module docstring Termination."""
    services = _services(ctx)
    conn = ctx.conn
    cid = str(payload.get("contract_id") or "")
    row = _open_contract(conn, cid)
    if row["status"] != "OPEN":
        raise ConflictError("NOT_OPEN", f"{cid} is {row['status']}; only an OPEN contract can be terminated", {"contract_id": cid, "status": row["status"]})
    if row["kind"] != "IRS":
        return _delegate(services, row["kind"], "terminate")(ctx, payload)
    acct = account_row(conn, row["account_id"])
    if acct is None or acct["status"] != "OPEN":
        raise RejectError("ACCOUNT_CLOSED", f"account {row['account_id']} is not open")
    value_c, inputs = _model_mark(services, row, ctx.ts)       # 409 NO_CURVE: no termination without a mark
    through = min(ny_of(ctx.ts).date(), _date(row["maturity_date"])) - timedelta(days=1)
    caught = _irs_catch_up(ctx, services, row, through)        # MONEY-2: every night owed is posted first (409 when one cannot accrue)
    row = _open_contract(conn, cid)
    ref_c = _reference_mark_c(conn, cid)
    memo = (f"Swap termination {cid}: value {fmt_money(value_c)} = the current model mark (par {inputs.get('par_rate')} vs dealt {inputs.get('dealt_rate')}, annuity"
            f" {inputs.get('annuity')}, curve as of {inputs.get('curve_as_of')}); VM balance {fmt_money(int(row['vm_balance_c']))} unwound; accrued legs settled"
            f" through the night of {through.isoformat()}" + (f" ({len(caught)} night accrual(s) posted first)" if caught else "") + " (SWAP_MARK)")
    closed = close_contract(ctx, services, row, "TERMINATED", value_c, ref_c, f"otc:{cid}:terminate", "OTC_TERMINATE", memo, True, False)
    ctx.event("OTC_TERMINATED", account_id=row["account_id"], instrument_id=cid, ref_type="otc", ref_id=cid, termination_value_c=value_c, inputs=inputs,
              caught_up=len(caught), **{k: v for k, v in closed.items() if k not in ("funding", "value_c")})
    view = contract_view(services, _contract_row(conn, cid), conn=conn, detail=True, now_ts=ctx.ts)
    entries = [c["entry_id"] for c in caught] + [e for e in (closed["entry_id"], closed["im_entry_id"]) if e is not None]
    return {"contract": view, "contract_id": cid, "termination_value_c": value_c, "result_c": closed["result_c"], "cash_c": closed["cash_c"], "entries": entries,
            "im_released_c": closed["im_released_c"], "accrued_receive_c": closed["accrued_receive_c"], "accrued_pay_c": closed["accrued_pay_c"], "inputs": inputs,
            "accrued_through": through.isoformat(), "caught_up": caught, "funding": closed.get("funding")}


# ---- lifecycle key helpers (WP-08 ProductJob: keys_fn(conn, now_ts) -> [(contract_id, date)]) ---------------------------------------------
def _open_contracts(conn: sqlite3.Connection, kinds: Optional[Tuple[str, ...]] = None) -> List[sqlite3.Row]:
    sql = ("SELECT c.* FROM otc_contracts c JOIN accounts a ON a.account_id=c.account_id JOIN lifetimes l ON l.lifetime_id=a.lifetime_id"
           " WHERE c.status='OPEN' AND a.status='OPEN' AND l.status='OPEN'")
    params: list = []
    if kinds:
        sql += f" AND c.kind IN ({','.join('?' for _ in kinds)})"
        params += list(kinds)
    return conn.execute(sql + " ORDER BY c.contract_id", params).fetchall()


def _jobs(conn: sqlite3.Connection, job: str, cid: str, lo: Optional[str] = None, hi: Optional[str] = None) -> Dict[str, sqlite3.Row]:
    lo_key, hi_key = f"{cid}:{lo or '0000-00-00'}", f"{cid}:{hi or '9999-99-99'}"
    return {r["key"]: r for r in conn.execute("SELECT key, status, note FROM jobs_done WHERE job=? AND key>=? AND key<=?", (job, lo_key, hi_key)).fetchall()}


def _terminal(r) -> bool:
    return r is not None and (r["status"] == "DONE" or (r["status"] == "SKIPPED" and str(r["note"] or "").startswith(VM_TERMINAL)))


def vm_keys(conn: sqlite3.Connection, now_ts: int) -> List[Tuple[str, str]]:
    """``(contract_id, date)`` pairs whose SWAP_VM is due: every calendar date after the last posted VM (from the start
    date) up to today whose 17:05 ET has passed and that is not terminal in ``jobs_done`` (DONE, or SKIPPED NO_CHANGE /
    CAUGHT_UP); every contract kind."""
    now = int(now_ts)
    today = ny_of(now).date()
    out: List[Tuple[str, str]] = []
    for row in _open_contracts(conn):
        cid = row["contract_id"]
        last = conn.execute("SELECT MAX(vm_date) AS d FROM otc_vm WHERE contract_id=?", (cid,)).fetchone()
        d = date.fromisoformat(row["start_date"])
        if last is not None and last["d"]:
            d = max(d, date.fromisoformat(last["d"]) + timedelta(days=1))
        if d > today:
            continue
        done = _jobs(conn, JOB_VM, cid, d.isoformat(), today.isoformat())
        while d <= today:
            if now < epoch_of(d, VM_ET):
                break
            ds = d.isoformat()
            if not _terminal(done.get(f"{cid}:{ds}")):
                out.append((cid, ds))
            d += timedelta(days=1)
    return out


def fix_keys(conn: sqlite3.Connection, now_ts: int) -> List[Tuple[str, str]]:
    """``(contract_id, reset date)`` pairs whose SWAP_FIX is due: TERM floating periods of IRS contracts starting on or
    before today (00:40 ET passed) without a FIX row and not DONE."""
    now = int(now_ts)
    today = ny_of(now).date()
    out: List[Tuple[str, str]] = []
    for row in _open_contracts(conn, ("IRS",)):
        cid = row["contract_id"]
        flt = _float_leg({k: row[k] for k in row.keys()})
        if flt.fixing != "TERM":
            continue
        fixed = {r["event_date"] for r in conn.execute("SELECT event_date FROM otc_resets WHERE contract_id=? AND kind='FIX'", (cid,)).fetchall()}
        done = _jobs(conn, JOB_FIX, cid)
        for s, _e, _p in flt.periods:
            if s > today or now < epoch_of(s, FIX_ET):
                continue
            ds = s.isoformat()
            if ds not in fixed and not _terminal(done.get(f"{cid}:{ds}")):
                out.append((cid, ds))
    return out


def pay_keys(conn: sqlite3.Connection, now_ts: int) -> List[Tuple[str, str]]:
    """``(contract_id, payment date)`` pairs whose SWAP_PAY is due: payment dates of either leg of an IRS contract on or
    before today (00:45 ET passed) without a PAY row and not DONE."""
    now = int(now_ts)
    today = ny_of(now).date()
    out: List[Tuple[str, str]] = []
    for row in _open_contracts(conn, ("IRS",)):
        cid = row["contract_id"]
        paid = {r["event_date"] for r in conn.execute("SELECT event_date FROM otc_resets WHERE contract_id=? AND kind='PAY'", (cid,)).fetchall()}
        done = _jobs(conn, JOB_PAY, cid)
        terms = _terms({k: row[k] for k in row.keys()})
        dates = sorted({p[2] for p in _periods(terms, "fixed_periods")} | {p[2] for p in _periods(terms, "float_periods")})
        for p in dates:
            if p > today or now < epoch_of(p, PAY_ET):
                continue
            ds = p.isoformat()
            if ds not in paid and not _terminal(done.get(f"{cid}:{ds}")):
                out.append((cid, ds))
    return out


# ---- views ------------------------------------------------------------------------------------------------------------------------
def _mark_dict(services, row: dict, now_ts: int, snapshot=None) -> dict:
    got = mark(services, row, now_ts, snapshot=snapshot)
    if isinstance(got, MarkView):
        d = got.to_dict()
        d["mark_c"] = None if got.mark is None else to_c(got.mark)
        return d
    if isinstance(got, tuple) and got[0] is not None:
        val, inputs = got
        inputs = dict(inputs or {})
        return {"instrument_id": row["contract_id"], "value": str(from_c(int(val))), "mark_c": int(val), "quality": "computed", "source": "swap model", "method": "swap_model",
                "as_of": inputs.get("as_of"), "fetched_at": now_ts, "delay_label": "model", "inputs": inputs, "executable": False,
                "note": inputs.get("note") or inputs.get("model") or MODEL}
    return {"instrument_id": row["contract_id"], "value": None, "mark_c": None, "quality": "unavailable", "source": "", "method": "", "as_of": None, "fetched_at": now_ts,
            "delay_label": "", "inputs": None, "executable": False, "note": "no model mark available in this version" if row["kind"] != "IRS" else "no mark"}


def side_text(kind: str, side: Optional[str], terms: Optional[dict] = None, detail: Optional[dict] = None) -> str:
    """The contract's side as a sentence for the contracts table (PO-23; C21: any kind - a hook's ``side_text`` wins)."""
    if detail and detail.get("side_text"):
        return str(detail["side_text"])
    s = str(side or "").upper()
    terms = terms or {}
    uid = (detail or {}).get("underlying_id") or terms.get("underlying_id")
    if s == "PAY_FIXED":
        return "pay fixed / receive SOFR"
    if s == "RECEIVE_FIXED":
        return "receive fixed / pay SOFR"
    if s == "RECEIVE_TOTAL_RETURN":
        return f"receive the {uid + ' ' if uid else ''}total return / pay financing"
    if s == "PAY_TOTAL_RETURN":
        return f"pay the {uid + ' ' if uid else ''}total return / receive financing"
    parts = s.split("_")
    if len(parts) == 4 and parts[0] == "RECEIVE" and parts[2] == "PAY":
        return f"receive {parts[1]} (lent) / pay {parts[3]} (borrowed)"
    return s.replace("_", " ").lower() if s else KIND_LABELS.get(str(kind or "").upper(), str(kind or "")).lower()


def _view_snapshot(services, rows) -> Optional[object]:
    """ONE mark snapshot for a contracts view, holding only what its TRS / XCCY marks read (the underlyings, the USD pairs):
    the marks of every row then come from the same instant at the cost of one small snapshot."""
    m = getattr(services, "marks", None)
    snap = getattr(m, "snapshot", None) if m is not None else None
    if not callable(snap):
        return None
    ids = set()
    for r in rows:
        try:
            t = json.loads((r["terms_json"] if not isinstance(r, dict) else r.get("terms_json")) or "{}")
        except (ValueError, TypeError, KeyError, IndexError):
            t = {}
        if t.get("underlying_id"):
            ids.add(str(t["underlying_id"]).upper())
        if t.get("foreign_ccy"):
            from ..instruments import fx_pair_id
            ids.add(fx_pair_id(str(t["foreign_ccy"])))
    try:
        return snap(sorted(ids)) if ids else None
    except TypeError:                     # a provider whose snapshot() takes no ids
        return snap()


def _compound_status(conn: sqlite3.Connection, row: dict, spec: LegSpec, today: date) -> dict:
    """The running state of a COMPOUND_ARREARS leg: the current period, nights compounded, factor, accrued and the
    realised annualised rate so far."""
    period = _period_containing(spec.periods, today) or next((p for p in spec.periods if p[0] > today), None) or (spec.periods[-1] if spec.periods else None)
    out = {"method": COMPOUND_METHOD, "fixing": "COMPOUND_ARREARS", "series": "SOFR", "period_start": None if period is None else period[0].isoformat(),
           "period_end": None if period is None else period[1].isoformat(), "nights": 0, "factor": "1", "accrued_c": 0, "rate_so_far": None, "accrued_through": None}
    st = _state(conn, spec.kind, row["contract_id"])
    if period is not None and st is not None and st["segment_start"] == period[0].isoformat() and st["accrued_through"]:
        nights = (_date(st["accrued_through"]) - period[0]).days + 1
        f = Decimal(str(st["rate"]))
        out.update(nights=nights, factor=str(f), accrued_c=int(st["cum_c"]), accrued_through=st["accrued_through"],
                   rate_so_far=str(((f - ONE) * DAYS_360 / Decimal(nights)).quantize(RATE_8)) if nights > 0 else None)
    return out


def next_events(services, contract, conn: Optional[sqlite3.Connection] = None, limit: int = 4) -> List[dict]:
    """The contract's next scheduled events (risk §14 and the contracts table): the next TERM fixing, the next payment
    dates with each leg's amount (None while not yet known: an unfixed TERM period, a period still compounding) and the
    maturity, sorted by date."""
    if conn is None:
        with _marks.shared_reader(services.db) as c:        # ORC-6: never a second pooled reader
            return next_events(services, contract, c, limit)
    row = _contract_dict(services, contract, conn)
    if row is None or row.get("status") != "OPEN":
        return []
    if row["kind"] != "IRS":
        fn = _hook(services, "next_events", row["kind"])
        # the caller's connection goes along: the delegate never takes a second pooled reader inside this one
        return list(fn(services, row, conn) or []) if fn is not None else []
    cid = row["contract_id"]
    today = ny_of(services.clock.epoch()).date()
    specs = irs_leg_specs(row)
    flt = next(s for s in specs.values() if s.leg == "float")
    events: List[dict] = []
    if flt.fixing == "TERM":
        nxt = next((p for p in flt.periods if p[0] > today), None)
        if nxt is not None:
            events.append({"date": nxt[0].isoformat(), "event": "FIX", "detail": f"SOFR fixing for {nxt[0].isoformat()}..{nxt[1].isoformat()} (term SOFR at the reset; see Rate lag)"})
    paid = {r["event_date"] for r in conn.execute("SELECT event_date FROM otc_resets WHERE contract_id=? AND kind='PAY'", (cid,)).fetchall()}
    pays = sorted({p[2] for s in specs.values() for p in s.periods if p[2] >= today and p[2].isoformat() not in paid})
    for d in pays[:2]:
        legs = []
        for spec in specs.values():
            p = next((p for p in spec.periods if p[2] == d), None)
            if p is None:
                continue
            if spec.fixing == "COMPOUND_ARREARS":
                amount, rate = None, None
            else:
                amount, rate = leg_coupon_c(conn, row, spec, p)
            legs.append({"leg": spec.leg, "side": spec.side, "amount_c": amount, "rate": rate, "known": amount is not None, "period": [p[0].isoformat(), p[1].isoformat()]})
        events.append({"date": d.isoformat(), "event": "MATURITY" if d.isoformat() == row["maturity_date"] else "PAY", "legs": legs})
    if not any(e["date"] == row["maturity_date"] for e in events):
        events.append({"date": row["maturity_date"], "event": "MATURITY", "detail": "last payment; VM unwound and IM released"})
    events.sort(key=lambda e: (e["date"], e["event"]))
    return events[:max(1, int(limit))]


def contract_view(services, contract, conn: Optional[sqlite3.Connection] = None, detail: bool = False, now_ts: Optional[int] = None, snapshot=None) -> dict:
    """§15 ``ContractView``: the row, its terms, the live mark (or the stored one labelled stale), the signed DV01, margin
    and accrued balances, the latest fixing (TERM) or the compounding state (COMPOUND_ARREARS), the next events and, with
    ``detail``, ``history: {vm, resets}``; ``kind_label`` / ``side_text`` let a table render any kind (C21)."""
    if conn is None:
        with _marks.shared_reader(services.db) as c:        # ORC-6: never a second pooled reader
            return contract_view(services, contract, c, detail, now_ts, snapshot)
    row = _contract_dict(services, contract, conn)
    if row is None:
        raise NotFound("NOT_FOUND", "unknown contract")
    ts = int(now_ts if now_ts is not None else services.clock.epoch())
    today = ny_of(ts).date()
    cid, aid, ccy = row["contract_id"], row["account_id"], row["currency"]
    terms = _terms(row)
    try:
        dv01 = dv01_c(services, row)
    except (ConflictError, RejectError):
        dv01 = None
    is_open = row["status"] == "OPEN"
    fixing = None
    next_fix = None
    if row["kind"] == "IRS":
        flt = _float_leg(row)
        if flt.fixing == "COMPOUND_ARREARS":
            fixing = _compound_status(conn, row, flt, today) if is_open else {"method": COMPOUND_METHOD, "fixing": "COMPOUND_ARREARS", "series": "SOFR", "nights": 0}
        else:
            fx = conn.execute("SELECT event_date, detail_json FROM otc_resets WHERE contract_id=? AND kind='FIX' ORDER BY event_date DESC LIMIT 1", (cid,)).fetchone()
            fixing = None if fx is None else dict(_detail(fx), reset_date=fx["event_date"])
            next_fix = next((p[0].isoformat() for p in flt.periods if p[0] > today), None) if is_open else None
    pays = sorted({p[2] for p in _periods(terms, "fixed_periods")} | {p[2] for p in _periods(terms, "float_periods")})
    next_pay = next((p.isoformat() for p in pays if p > today), None) if is_open else None
    remaining = len(_remaining(row, today)) if is_open else 0
    try:
        events = next_events(services, row, conn) if is_open else []
    except (ConflictError, RejectError):
        events = []
    out = {"contract_id": cid, "account_id": aid, "broker": row["broker"], "kind": row["kind"], "status": row["status"], "currency": ccy, "notional_c": int(row["notional_c"]),
           "pay_fixed": bool(terms.get("pay_fixed")), "side": "PAY_FIXED" if terms.get("pay_fixed") else "RECEIVE_FIXED", "fixed_rate": terms.get("fixed_rate"),
           "fixed_rate_pct": _pct(terms["fixed_rate"]) if terms.get("fixed_rate") else None, "par_rate_dealt": terms.get("par_rate"), "half_width_bp": terms.get("half_width_bp"),
           "tenor_months": terms.get("tenor_months"), "fixing_method": terms.get("fixing") or ("TERM" if row["kind"] == "IRS" else None), "start_date": row["start_date"],
           "maturity_date": row["maturity_date"], "margin_mode": row["margin_mode"], "im_c": int(row["im_c"]), "mark_c": int(row["mark_c"] or 0), "mark_ts": row["mark_ts"],
           "vm_balance_c": int(row["vm_balance_c"] or 0), "last_reset_date": row["last_reset_date"], "closed_ts": row["closed_ts"],
           "mark": _mark_dict(services, row, ts, snapshot),
           "dv01_c": dv01, "im_balance_c": balance(conn, aid, "1330", ccy, cid), "vm_posted_c": balance(conn, aid, "1320", ccy, cid),
           "vm_received_c": balance(conn, aid, "2330", ccy, cid), "accrued_receive_c": balance(conn, aid, "1225", ccy, cid), "accrued_pay_c": balance(conn, aid, "2340", ccy, cid),
           "result_c": balance(conn, aid, "4450", ccy, cid), "fixing": fixing, "next_fix_date": next_fix, "next_pay_date": next_pay, "next_events": events,
           "remaining_periods": remaining, "terms": terms, "conventions": list(CONVENTIONS)}
    try:
        out["inputs"] = json.loads(row.get("inputs_json") or "null")
    except ValueError:
        out["inputs"] = None
    out["balances_by_ccy"] = _balances_by_ccy(conn, aid, cid)
    det = None
    if row["kind"] != "IRS":
        # TRS / XCCY / the OTC options and forwards: the specifics from the kind's module (underlying, units, reset price, financing;
        # per-currency legs, notionals, FX resets; strike, expiry ...) and the contract's own side - never the IRS PAY_FIXED /
        # RECEIVE_FIXED reading of a missing pay_fixed term
        out.update(pay_fixed=None, fixed_rate=None, fixed_rate_pct=None)
        fn = _hook(services, "contract_detail", row["kind"])
        if fn is not None:
            try:
                det = fn(services, row, conn)
            except (RejectError, ConflictError) as e:
                out["detail_error"] = f"{e.code}: {e.message}"
        out["detail"] = det
        if row["kind"] == "TRS":
            out["side"] = (det or {}).get("side") or ("RECEIVE_TOTAL_RETURN" if terms.get("receiver") else "PAY_TOTAL_RETURN")
        elif row["kind"] == "XCCY":
            legs = (det or {}).get("legs") or terms.get("legs") or {}
            out["side"] = f"RECEIVE_{(legs.get('receive') or {}).get('currency') or '?'}_PAY_{(legs.get('pay') or {}).get('currency') or '?'}"
        else:
            out["side"] = (det or {}).get("side") or terms.get("side") or row["kind"]
    out["kind_label"] = KIND_LABELS.get(row["kind"], row["kind"])
    out["side_text"] = side_text(row["kind"], out.get("side"), terms, det)
    out["notional_ccy"] = row["currency"]
    out["mark_as_of"] = (out.get("mark") or {}).get("as_of")
    if detail:
        vm = [{"vm_date": r["vm_date"], "mark_c": int(r["mark_c"]), "delta_c": int(r["delta_c"]), "mode": r["mode"], "entry_id": r["entry_id"], "inputs": _detail({"detail_json": r["inputs_json"]})}
              for r in conn.execute("SELECT * FROM otc_vm WHERE contract_id=? ORDER BY vm_date", (cid,)).fetchall()]
        resets = [{"event_date": r["event_date"], "kind": r["kind"], "amount_c": int(r["amount_c"]), "entry_id": r["entry_id"], "detail": _detail(r)}
                  for r in conn.execute("SELECT * FROM otc_resets WHERE contract_id=? ORDER BY event_date, kind", (cid,)).fetchall()]
        out["history"] = {"vm": vm, "resets": resets}
    return out


ContractView = contract_view

BALANCE_GLS = {"1225": "accrued_receive_c", "2340": "accrued_pay_c", "1360": "notional_lent_c", "2360": "notional_borrowed_c", "1320": "vm_posted_c",
               "2330": "vm_received_c", "1330": "im_c"}


def _balances_by_ccy(conn: sqlite3.Connection, account_id: str, cid: str) -> Dict[str, Dict[str, int]]:
    """``{currency: {accrued_receive_c, accrued_pay_c, notional_lent_c, notional_borrowed_c, vm_posted_c, vm_received_c, im_c}}`` of the
    contract (natural sign, per currency): an XCCY's foreign accruals and notionals are in their own currency, not in ``row.currency``."""
    out: Dict[str, Dict[str, int]] = {}
    for r in conn.execute("SELECT gl, currency, SUM(balance_c) AS b FROM gl_balances WHERE account_id=? AND instrument_id=? AND gl IN"
                          f" ({','.join('?' for _ in BALANCE_GLS)}) GROUP BY gl, currency ORDER BY currency, gl", (account_id, cid, *BALANCE_GLS)).fetchall():
        b = natural(r["gl"], int(r["b"] or 0))
        if b:
            out.setdefault(r["currency"], {})[BALANCE_GLS[r["gl"]]] = b
    return out


def contracts_view(services, account_id: Optional[str] = None, scope: Optional[str] = None, detail: bool = False) -> List[dict]:
    """``GET /api/otc/contracts``: the contracts of an account, of a lifetime scope (``{lid}:ALL``) or all of them - read on ONE
    connection (this thread's, ORC-6) with ONE small mark snapshot for every row."""
    with _marks.shared_reader(services.db) as conn:
        if account_id:
            rows = conn.execute("SELECT * FROM otc_contracts WHERE account_id=? ORDER BY contract_id", (account_id,)).fetchall()
        elif scope and str(scope).endswith(":ALL"):
            rows = conn.execute("SELECT * FROM otc_contracts WHERE account_id LIKE ? ORDER BY contract_id", (str(scope)[:-4] + ":%",)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM otc_contracts ORDER BY contract_id").fetchall()
        snap = _view_snapshot(services, [r for r in rows if r["status"] == "OPEN"])
        return [contract_view(services, r, conn, detail, snapshot=snap) for r in rows]


# ---- registry ---------------------------------------------------------------------------------------------------------------------
def register(bus, services=None) -> None:
    """Register ``otc.book`` / ``otc.terminate`` (ui) and ``swap.vm`` / ``swap.fix`` / ``swap.pay`` (lifecycle); the runtime
    binds this module as ``services.swaps`` so ``marks.py``, ``accounting`` (``dv01_c``), risk (``next_events``),
    ACCRUE_DAILY (``accrue_night``) and the SWAP_* ProductJobs (``vm_keys`` / ``fix_keys`` / ``pay_keys``) can reach it."""
    bus.register("otc.book", h_book)
    bus.register("otc.terminate", h_terminate)
    bus.register("swap.vm", h_vm)
    bus.register("swap.fix", h_fix)
    bus.register("swap.pay", h_pay)


__all__ = ["preview", "mark", "dv01_c", "accrual_lines_swap", "accrue_night", "accrue_leg", "contract_view", "ContractView", "contracts_view", "next_events",
           "vm_keys", "fix_keys", "pay_keys", "register", "parse_irs_terms", "IrsTerms", "Deal", "LegSpec", "irs_leg_specs", "leg_specs", "leg_coupon_c",
           "close_contract", "NotBuilt", "period_schedule", "effective_date", "modified_following", "add_months", "days_30360", "coupon_c", "compound_step",
           "compounded_c", "par_annuity", "model_mark_c", "dv01_from", "curve_as_of", "fixing_for", "h_book", "h_terminate", "h_vm", "h_fix", "h_pay",
           "RATE_Q", "SWAP_IM_BP", "CONVENTIONS", "MODEL", "COMPOUND_METHOD", "TERM_METHOD"]
