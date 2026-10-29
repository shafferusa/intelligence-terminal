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

FINSIM1 (FINSIM1_PLAN P4, the owner's clarification of 2026-10-27: his Goldman TRS / CDX / IRS are real contracts booked as of
2026-10-21 with their lifecycle caught up)
- AS-OF BOOKING (C10, OMS-04): ``otc.book_as_of {account_id, kind, terms, as_of: {trade_date, value_date, exec_ts, batch_id, line_key,
  historical_model?}}`` - actor ``ui`` refused (403 AS_OF_FORBIDDEN); TRS / CDX / IRS (any other kind: 409 ASOF_INPUT_MISSING, its
  trade date's inputs are not recorded).  The owner's dealt terms are booked exactly (D10: TRS ``reset_px``, CDX ``dealt_spread_bp``,
  IRS ``fixed_rate``); the stored contract is compared with them before commit (409 ASOF_TERMS_MISMATCH, nothing written).  The
  valuation inputs are those OF the trade date - the curve (``CurveStore.for_date``, C12), SOFR, the CDX proxy on or before it plus
  the account basis (a basis that would not deal the spread within 0.01 bp: 409 ASOF_BASIS_NOT_CALIBRATED), the TRS underlying's
  recorded close - and a missing one is 409 ASOF_INPUT_MISSING.  Every entry of the booking - the IM, the CDX upfront, the day-0 VM
  (the trade date's variation margin, posted inside the booking) and their funding sweeps - carries business date = the value date
  through ``ctx.asof_date`` (D7, overriding the 17:00 ET accounting day); the CDX initial payment is a settlement due T+1 from the
  trade date naming its contract (``ref_type`` otc / ``ref_id``).  The row records ``booking`` AS_OF (HISTORICAL_MODEL with
  ``historical_model``), ``entered_ts`` (now), ``import_key`` (the line key: a second booking of it is 409 DUPLICATE_IMPORT),
  ``trade_date``, ``value_date`` and ``confirm_status`` CONFIRMED - "CONFIRMED - as-of booking at the owner's instruction (no
  electronic confirmation in the simulator)"; the trade time is the payload's execution time (``terms.dealt_ts``).
- THE CURVE SPREAD (owner Q1 variant A): ``calibrate_curve_spread_to_rate`` books the dealt IRS rate exactly and sets the contract
  curve spread so that rate = par + spread + half-width (pay fixed; par + spread - half-width receiving): mark0 = the cost of
  dealing, never a day-one gain; every mark, DV01 and VM of the contract uses par + spread.  Only an as-of booking calibrates:
  ``otc.book`` refuses the flag and keeps refusing OFF_MARKET.
- DATED INPUTS (PORTFOLIO-01, P4 task 3): a VM of date d values on the curve OF d (or of the 3 business days before it); it is
  CAUGHT_UP only when that curve truly does not exist and a later one does.  ``ctx.asof_date`` = d for its funding, so the sweep
  and the entry share d (POSTCLOSE-06).  An as-of contract's accrual of night D before its entered day posts with business date
  D + 1 (``lifecycle_business_date``); VM and payments of D post on D.
- GLs (C11, PORTFOLIO-08): coupon legs and the CDS premium accrue on 4453, the TRS financing on 4451 (swaps_trs), dividend
  equivalents on 4452; an upfront or OTC option premium is the contract's cost basis (1335 paid / 2345 received) and
  ``close_contract`` releases it into 4450 at termination, maturity or expiry.  4450 keeps the return leg, STM settlements and
  close-out values.
- CSA NETTING (C21, P4 task 9): VM, swap payments, CDX coupons, TRS resets and terminations are payments to the CSA counterparty,
  funded with ``csa_netting=True`` (``fund_csa`` / ``fund_commitment(csa_netting=True)``): they may use the cash collateral that
  counterparty posted; an IM may not.
- RFQ AND CONFIRMATION (C14, OMS-10; 1.0.6): the PB OTC desks ask for a quote - ``otc.rfq.request {account_id, kind, terms}`` runs the
  gates and prices the deal once (the same refusals as a booking) and stores the request REQUESTED (table ``rfqs``); the RFQ_DEALER
  lifecycle job (every tick, about 30 s; no matcher) answers it with the model quote labelled "indicative model quote, simulated dealer"
  (QUOTED, good for 120 s) or, when the model cannot price it now, EXPIRED with the reason; it expires a quote nobody accepted.  ``otc.rfq.
  accept {rfq_id}`` books within the validity through ``otc.book`` - IRS / TRS / CDX at the quoted rate / reset price / spread (the dealer
  honours its quote), the other kinds re-priced and refused 409 QUOTE_MOVED when the price moved; ``otc.rfq.decline`` closes it.  Every
  contract booked now starts PENDING_CONFIRM; the OTC_CONFIRM job confirms it at 17:05 ET of its business day ("simulated confirmation").
  Both jobs are quiet when idle and start forward (15.9 item 8): a contract booked before this release has no confirmation record and is
  never touched.  ``contract_timeline`` lists a contract's life (requested, quoted, accepted, booked, IM, confirmation, each VM, fixing,
  payment, reset, a simulated credit event, the termination); ``derivatives_view`` serves the Derivatives page (UI-11).
- FX_CCS_PLAN rev 2 (release 1.0.6.1, package P-C): ``LegSpec`` gains ``index`` (SOFR / ESTR) and ``spread`` (added to a COMPOUND_ARREARS leg,
  never compounded: cumulative ``to_c(N x ((F - 1) + spread x n / 360))``), ``fixing_for(services, d, series)``; the IRS keeps SOFR and 0.  The owner
  reference ``client_ref`` (D13): ``otc_contracts.client_ref`` (partial unique index), checked FIRST by ``h_book`` for every kind (409
  DUPLICATE_CLIENT_REF, nothing written) and written after the kind's booking; ``otc.rfq.request`` refuses it only while a contract or a REQUESTED /
  QUOTED request carries it.  VM on the CSA exposure (D8): under CTM a mark hook's ``inputs.csa_exposure_c`` is margined (``otc_vm.mark_c`` holds
  it as the next reference) while the contract's ``mark_c`` stays the clean overlay.  An XCCY's first VM key is min(start, COALESCE(trade_date,
  start)) - a NULL or unreadable trade date never raises.  ``contract_view`` adds ``client_ref``, ``linked_deals`` and ``confirmation``.
- POSITION FIELDS (C13, PORTFOLIO-02 / -06): ``position_fields`` gives accounting the MV a row and NAV carry - the live model mark
  ("model, intraday") when an input it uses was observed after the mark of the last VM (an STM contract nets what was already
  settled), else that mark ("as of the last VM"; stale when the model cannot run now) - with unrealised = MV - the cost basis and
  the display fields per kind (TRS units / reset / underlying price; CDX notional / dealt and current spread / coupon / upfront;
  IRS notional / fixed rate vs par now / DV01).
"""
from __future__ import annotations

import inspect
import json
import re
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN
from typing import Any, Dict, List, Optional, Tuple

from .. import config, execution, prime
from .. import marks as _marks
from ..books import account_row, eligibility as _eligibility, require_pb_otc, split_account_id
from ..clock import NY, accounting_day, epoch_of, ny_of
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
LEG_GL = {"swaprcv": ("1225", "4453"), "swappay": ("4453", "2340")}      # (debit, credit) of a positive accrual: coupons and CDS premium on 4453 (C11)
VM_TERMINAL = ("NO_CHANGE", "CAUGHT_UP")                                   # quiet SKIPPED notes that end a VM key
MODEL = "clean PV = sign x N x (par - K) x annuity on the remaining semi-annual periods; floating leg at par"
TERM_METHOD = "TERM: the latest SOFR print on or before the reset date (RATE_LAG)"
COMPOUND_METHOD = ("COMPOUND_ARREARS: every calendar night accrues at the latest SOFR on or before it (RATE_LAG), compounded daily"
                   " F = prod(1 + r/360); the period pays N x (F - 1)")

Period = Tuple[date, date, date]       # (accrual start, accrual end, payment date), all business-day adjusted
# FINSIM1 C10 (P4): the as-of booking of the owner's OTC contracts
ASOF_BOOKING, HISTORICAL_BOOKING = "AS_OF", "HISTORICAL_MODEL"
ASOF_KINDS = ("TRS", "CDX", "IRS")
CONFIRMED = "CONFIRMED"
ASOF_CONFIRM_LABEL = "CONFIRMED - as-of booking at the owner's instruction (no electronic confirmation in the simulator)"
HISTORICAL_LABEL = "Historical model (not an independent execution)"
CURVE_SPREAD_TEXT = "contract curve spread (SOFR swap vs Treasury par curve) {bp} bp, calibrated at booking"
ASOF_TOLERANCE_BP = Decimal("0.01")    # a CDX whose account basis deals the payload's spread within 0.01 bp books at that spread exactly


class NotBuilt(RejectError):
    """TRS / XCCY before WP-26 is bound -> HTTP 501 NOT_BUILT."""
    http = 501


class Forbidden(RejectError):
    """An as-of booking from the UI -> HTTP 403 AS_OF_FORBIDDEN (C10: actor ``ui`` is refused)."""
    http = 403


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


@contextmanager
def asof_context(ctx: TxContext, d):
    """C11 / C20: ``ctx.asof_date`` = ``d`` (an ISO string, as ``execution.asof_day`` reads it) for the block: every entry posted without
    its own business date, and the funding sweep (``treasury.sweep_in`` stamps it, C15), carries that date; the previous value is put
    back after the block."""
    prev = getattr(ctx, "asof_date", None)
    ctx.asof_date = None if d is None else (d.isoformat() if hasattr(d, "isoformat") else str(d)[:10])
    try:
        yield ctx.asof_date
    finally:
        ctx.asof_date = prev


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
    # FX_CCS_PLAN K5 (1.0.6.1): the overnight series a COMPOUND_ARREARS leg compounds (SOFR; ESTR for a EUR float leg) and the spread added to
    # the compounded rate - never compounded (ISDA "compounding with spread excluded").  The defaults keep the IRS exactly as it was.
    index: str = "SOFR"
    spread: Decimal = Decimal(0)

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


def _csa_kw(csa_netting: bool) -> dict:
    """C21: ``csa_netting=True`` only when the funding module takes it (P2's ``execution.fund``); else today's behaviour."""
    return {"csa_netting": True} if csa_netting and _accepts(execution.fund, "csa_netting") else {}


def fund_csa(ctx: TxContext, services, account_id: str, ccy: str, need_c: int, link_type: str, link_id: str, overdraft: bool = True):
    """C21 (P4 task 9): a payment to the account's CSA counterparty - variation margin, a swap payment, a CDX coupon, a TRS reset -
    funded from settled cash that may include the collateral that same counterparty posted (``csa_netting=True``), then one Treasury
    sweep, then PB financing; ``overdraft`` (lifecycle obligations, section 11.6) posts any rest as an overdraft under an open
    FUNDING_SHORTFALL, else 409 INSUFFICIENT_FUNDS.  Under ``ctx.asof_date`` (C20) the figures are those of that business date."""
    return execution.fund(ctx, account_id, ccy, int(need_c), link_type, link_id, None, immediate=True, overdraft=overdraft, services=services,
                          **_csa_kw(True))


def fund_commitment(ctx: TxContext, services, account_id: str, ccy: str, need_c: int, link_type: str, link_id: str, csa_netting: bool = False):
    """PO-07 / C12c: what the owner commits at booking or termination (an IM, a USD start exchange, a termination payment) is
    measured against the account's own cash that may leave settled cash now - min(settled, settled + receivables due today -
    payables - cash reserved by working orders) - never against cash a pending settlement already needs; the shortfall is swept
    from Treasury now (then PB financing), else 409 INSUFFICIENT_FUNDS.  ``execution.fund(..., commitments=True)`` when the funding
    module has that mode; otherwise the same rule through its available-at-today path, and settled cash is topped up if a
    receivable counted as available has not settled yet (the posting leaves settled cash now).  ``csa_netting`` (C21): a termination
    payment to the CSA counterparty may use that counterparty's collateral; an IM may not."""
    need_c = int(need_c)
    if _accepts(execution.fund, "commitments"):
        return execution.fund(ctx, account_id, ccy, need_c, link_type, link_id, None, commitments=True, services=services, **_csa_kw(csa_netting))
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


def dated_curve(services, on: date):
    """C12: the curve view OF ``on`` (``CurveStore.for_date``: the curve of that date or of the last business date within 3 business
    days before it); None when there is none.  A store without ``for_date`` (an older test double) serves its live curve."""
    curve = getattr(services, "curve", None)
    if curve is None:
        return None
    fn = getattr(curve, "for_date", None)
    if fn is None:
        return curve
    try:
        return fn(on, cal=getattr(services, "cal", None))
    except TypeError:                     # a for_date without the calendar keyword
        return fn(on)


def dfs_for(services, n_periods: int, shift_bp=None, on: Optional[date] = None) -> Tuple[Dict[Decimal, Decimal], dict]:
    """The semi-annual discount factors for ``n_periods`` (0.5 … n/2 years) from ``services.curve`` with their provenance;
    409 ``NO_CURVE`` when no curve store is bound, no curve was observed or the factors stop before the horizon.  ``on`` (C12, P4
    task 3): the curve OF that date (``dated_curve``) - a past valuation date is discounted on its own curve, never on today's."""
    curve = getattr(services, "curve", None)
    if curve is None or not hasattr(curve, "discount_factors"):
        raise ConflictError("NO_CURVE", "no par curve store is bound (services.curve): swaps cannot be marked and nothing is booked without a curve")
    if on is not None:
        curve = dated_curve(services, on)
        if curve is None:
            raise ConflictError("NO_CURVE", f"no Treasury par curve of {on.isoformat()} (or of the 3 business days before it) is recorded: the swap cannot be"
                                f" valued as of {on.isoformat()}", {"date": on.isoformat()})
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


#: FX_CCS_PLAN K5 (1.0.6.1): the overnight series a COMPOUND_ARREARS leg may compound, with who publishes it (the refusal says so)
FIXING_SERIES = {"SOFR": ("SOFR", "FRED (it publishes SOFR a business day late; a rate is never assumed)"),
                 "ESTR": ("ESTR", "the ECB (it publishes each day's ESTR at 08:00 CET on the next TARGET2 business day; a rate is never assumed)")}


def fixing_for(services, d: date, series: str = "SOFR") -> Tuple[date, Decimal]:
    """``(observation date, percent)`` of the overnight ``series`` (SOFR by default; ESTR for a EUR float leg, FX_CCS_PLAN K5) for date
    ``d``: the latest print on or before it, at most 10 days old (RATE_LAG); 409 ``RATE_UNAVAILABLE`` otherwise — never defaulted."""
    series = str(series or "SOFR").strip().upper()
    name, waits = FIXING_SERIES.get(series, (series, "its source (a rate is never assumed)"))
    rates = getattr(services, "rates", None)
    if rates is None or not hasattr(rates, "as_of_le"):
        raise ConflictError("RATE_UNAVAILABLE", f"no interest-rate data is loaded: {name} is unknown and a rate is never assumed", {"series": series})
    got = rates.as_of_le(series, d)
    if got is None or (d - got[0]).days > RATE_LAG_MAX_DAYS:
        # V104R2-BW-02 (e) class (the integrator, 1.0.6): the reason in trader words, no convention code; the refusal code is unchanged
        raise ConflictError("RATE_UNAVAILABLE", f"no {name} print within {RATE_LAG_MAX_DAYS} days on or before {d.isoformat()}: the fixing waits for {waits}",
                            {"series": series, "date": d.isoformat()})
    return got[0], qprice(got[1])


def _rate_of(pct: Decimal) -> Decimal:
    return (Decimal(pct) / Decimal(100)).quantize(RATE_Q, rounding=ROUND_HALF_EVEN)


def curve_spread(terms: dict) -> Decimal:
    """The contract curve spread of an IRS (owner Q1 variant A, C10): a fraction added to the Treasury par rate in every mark, DV01 and
    VM of the contract (0 unless the booking calibrated one)."""
    v = (terms or {}).get("curve_spread")
    if v in (None, ""):
        return Decimal(0)
    try:
        return qprice(Decimal(str(v)))
    except (InvalidOperation, ValueError):
        return Decimal(0)


def curve_spread_text(spread) -> str:
    """-0.00180 -> "contract curve spread (SOFR swap vs Treasury par curve) -18.0 bp, calibrated at booking"."""
    bp = (Decimal(spread) / BP).quantize(Decimal("0.1"))
    return CURVE_SPREAD_TEXT.format(bp=f"{'+' if bp > 0 else ''}{bp}")


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
    curve_spread: Decimal = Decimal(0)        # C10 / owner Q1 variant A: calibrated so the dealt rate = par + spread +/- the half-width

    @property
    def n_periods(self) -> int:
        return len(self.fixed_periods)

    @property
    def compound(self) -> bool:
        return self.terms.fixing == "COMPOUND_ARREARS"


def _deal(services, conn: sqlite3.Connection, acct: dict, terms: IrsTerms, today: date, as_of: Optional[dict] = None, calibrate: bool = False,
          firm_rate: Optional[Decimal] = None) -> Deal:
    """The IRS deal of ``today`` (the trade date).  ``as_of`` (C10): the valuation inputs are those OF the trade date (its curve through
    ``CurveStore.for_date``, its SOFR print) and a missing one is 409 ASOF_INPUT_MISSING; ``calibrate`` (owner Q1 variant A): the dealt
    fixed rate is booked exactly and the contract curve spread is set so that rate = par + spread + half-width (pay fixed; par + spread -
    half-width receiving), so mark0 = the half-width x N x annuity, the cost of dealing - never a day-one gain.  ``firm_rate`` (C14): the
    rate of the dealer's quote the owner accepted within its validity - booked as quoted (the dealer honours its own quote; mark0 is the
    model value on the curve now)."""
    sched = prime.read_schedule(conn, acct)
    mult = qprice(Decimal(sched.otc_spread_mult))
    hw_bp = qprice(Decimal(sched.irs_half_width_bp) * mult)
    hw = (hw_bp * BP).quantize(RATE_Q, rounding=ROUND_HALF_EVEN)
    cal = services.cal
    start = effective_date(cal, today)
    fixed_periods = period_schedule(cal, start, terms.tenor_months, terms.fixed_months)
    float_periods = period_schedule(cal, start, terms.tenor_months, terms.float_months)
    maturity = fixed_periods[-1][1]
    if as_of is not None:
        try:
            dfs, curve_inputs = dfs_for(services, len(fixed_periods), on=today)
        except ConflictError as e:
            raise ConflictError("ASOF_INPUT_MISSING", f"the IRS cannot be booked as of {today.isoformat()}: {e.message}; nothing was written",
                                dict(e.details or {}, input="UST_PAR curve", date=today.isoformat())) from e
    else:
        dfs, curve_inputs = dfs_for(services, len(fixed_periods))
    par, annuity, par_raw = par_annuity(dfs)
    quote = ((par + hw) if terms.pay_fixed else (par - hw)).quantize(RATE_Q)
    spread = Decimal(0)
    if calibrate:
        if terms.fixed_rate is None:
            raise RejectError("TERMS", "calibrate_curve_spread_to_rate books a dealt fixed_rate exactly: give fixed_rate")
        spread = ((terms.fixed_rate - par - hw) if terms.pay_fixed else (terms.fixed_rate - par + hw)).quantize(RATE_Q)
        dealt = terms.fixed_rate
    elif firm_rate is not None:
        dealt = Decimal(firm_rate).quantize(RATE_Q)          # C14: the accepted quote, as quoted
    elif terms.fixed_rate is not None:
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
    mark0 = model_mark_c(terms.notional_c, terms.pay_fixed, dealt, par + spread, annuity)
    dv01 = dv01_from(terms.notional_c, annuity, terms.pay_fixed)
    imp = im_params(services, acct["account_id"])
    im_bp = Decimal(imp["irs_im_bp"])
    im = to_c(from_c(abs(dv01)) * im_bp)                          # IM = |DV01| x the schedule's bp (50 bp static until C22)
    try:
        obs, pct = fixing_for(services, start)
    except ConflictError as e:
        if as_of is None:
            raise
        raise ConflictError("ASOF_INPUT_MISSING", f"the IRS cannot be booked as of {today.isoformat()}: {e.message}; nothing was written",
                            dict(e.details or {}, input="SOFR", date=start.isoformat())) from e
    return Deal(acct, terms, sched, start, maturity, fixed_periods, float_periods, hw_bp, hw, mult, par, par_raw, annuity, dealt, mark0, dv01, im,
                str(sched.swap_margin_mode).upper(), obs, pct, _rate_of(pct), curve_inputs, im_bp, imp["source"], spread)


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
            "conventions": list(CONVENTIONS), "curve_note": IRS_CURVE_NOTE, **_spread_fields(deal.curve_spread)}


def _spread_fields(spread) -> dict:
    """The curve spread of a contract for its dicts (nothing when it has none)."""
    if not spread:
        return {}
    return {"curve_spread": str(Decimal(spread).quantize(RATE_Q)), "curve_spread_bp": str((Decimal(spread) / BP).quantize(Decimal("0.1"))),
            "curve_spread_text": curve_spread_text(spread)}


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
    creates the contract, its SWAP instrument row and (TERM) the first fixing (module docstring IM / SWAP_MARK).

    FX_CCS_PLAN D13 / K5 (1.0.6.1): the owner reference ``terms.client_ref`` (optional) is checked FIRST for every kind - a contract that
    already carries it, in any status, answers 409 DUPLICATE_CLIENT_REF and nothing is written - and written on the new contract's row
    (``otc_contracts.client_ref``, unique where set) after the kind's own booking returns, in the same command."""
    services = _services(ctx)
    kind = prime.require_otc_kind(payload.get("kind"))
    aid = str(payload.get("account_id") or "")
    conn = ctx.conn
    acct = _pb_account(conn, aid)
    require_pb_otc(services, aid)
    ref = client_ref_of(payload)
    if ref is not None:
        ensure_client_ref_column(services, conn)
        dup = contract_by_client_ref(conn, ref)
        if dup is not None:
            raise ConflictError("DUPLICATE_CLIENT_REF", f"the reference {ref} is already booked as {dup['contract_id']} ({str(dup['status']).lower()}); nothing was"
                                " written - a booking is never made twice", {"client_ref": ref, "contract_id": dup["contract_id"], "status": dup["status"]})
    if kind != "IRS":
        out = _delegate(services, kind, "book")(ctx, payload)
    else:
        raw_terms = dict(payload.get("terms") or {})
        if raw_terms.get("calibrate_curve_spread_to_rate") not in (None, False, "", "false", 0):
            raise RejectError("TERMS", "a contract curve spread is calibrated only when a dealt rate is booked as of its trade date; a new swap deals at the"
                              " dealer's quote (leave the rate blank) or at a rate no better than it", {"field": "calibrate_curve_spread_to_rate"})
        firm = getattr(ctx, "rfq_firm", None) or {}
        firm_rate = None
        if firm.get("fixed_rate") not in (None, ""):                 # C14: an accepted dealer quote books at its quoted rate
            firm_rate = Decimal(str(firm["fixed_rate"]))
            raw_terms["fixed_rate"] = str(firm_rate)
        terms = parse_irs_terms(raw_terms)
        deal = _deal(services, conn, acct, terms, ny_of(ctx.ts).date(), firm_rate=firm_rate)
        out = _book_irs(ctx, services, acct, terms, deal)
    wrote_ref = False
    if ref is not None and out.get("contract_id"):
        conn.execute("UPDATE otc_contracts SET client_ref=? WHERE contract_id=?", (ref, out["contract_id"]))      # D13: one writer, every kind
        out["client_ref"] = ref
        wrote_ref = True
    if mark_new_contract(ctx, out.get("contract_id")) or wrote_ref:   # C14: a new contract waits for the simulated 17:05 ET confirmation
        out["contract"] = contract_view(services, _contract_row(conn, out["contract_id"]), conn=conn, detail=True, now_ts=ctx.ts)
    return out


# ---- the owner reference (FX_CCS_PLAN D13, K5; 1.0.6.1) -----------------------------------------------------------------------------
#: an optional reference the owner (or his booking program) puts on a trade so that a re-run never books it twice: unique on the contracts
CLIENT_REF_SCHEMA = ["ALTER TABLE otc_contracts ADD COLUMN client_ref TEXT",
                     "CREATE UNIQUE INDEX IF NOT EXISTS ux_otc_client_ref ON otc_contracts(client_ref) WHERE client_ref IS NOT NULL"]
CLIENT_REF_RE = re.compile(r"^[A-Za-z0-9:_.\-]{1,80}$")


def client_ref_of(payload: Optional[dict]) -> Optional[str]:
    """The owner reference of a booking or a quote request: ``terms.client_ref`` (or the payload's own ``client_ref``); None when absent.
    400 TERMS when it is not 1-80 letters, digits or ``: _ . -``, or when the two places disagree."""
    p = dict(payload or {})
    t = p.get("terms") if isinstance(p.get("terms"), dict) else {}
    vals = [v for v in (t.get("client_ref"), p.get("client_ref")) if v not in (None, "")]
    if not vals:
        return None
    if any(isinstance(v, (bool, int, float, dict, list)) for v in vals):
        raise RejectError("TERMS", "client_ref must be text: 1 to 80 letters, digits or : _ . - (the owner's reference of the trade)", {"field": "client_ref"})
    refs = {str(v).strip() for v in vals}
    if len(refs) > 1:
        raise RejectError("TERMS", f"client_ref is given twice with different values ({', '.join(sorted(refs))})", {"field": "client_ref"})
    ref = refs.pop()
    if not CLIENT_REF_RE.match(ref):
        raise RejectError("TERMS", f"client_ref {ref!r} must be 1 to 80 letters, digits or : _ . - (the owner's reference of the trade)", {"field": "client_ref"})
    return ref


def ensure_client_ref_column(services, conn: Optional[sqlite3.Connection] = None) -> None:
    """The ``client_ref`` column and its partial unique index (additive; ``bind`` adds them, a booking makes sure)."""
    if conn is not None and has_columns(conn, "otc_contracts", "client_ref"):
        return
    db = getattr(services, "db", None)
    if db is not None and hasattr(db, "ensure_schema_ext"):
        db.ensure_schema_ext("swaps", CLIENT_REF_SCHEMA)


def contract_by_client_ref(conn: sqlite3.Connection, ref: Optional[str]) -> Optional[dict]:
    """The contract that carries the owner reference ``ref`` in any status (None when none does or the column does not exist yet)."""
    if not ref or not has_columns(conn, "otc_contracts", "client_ref"):
        return None
    r = conn.execute("SELECT contract_id, status, kind, account_id FROM otc_contracts WHERE client_ref=?", (str(ref),)).fetchone()
    return None if r is None else {k: r[k] for k in r.keys()}


def open_rfq_by_client_ref(conn: sqlite3.Connection, ref: Optional[str]) -> Optional[dict]:
    """A quote request still REQUESTED or QUOTED whose terms carry the owner reference ``ref`` (None otherwise): an EXPIRED or DECLINED one
    never blocks a new request (D13)."""
    if not ref or not _has_table(conn, "rfqs"):
        return None
    for r in conn.execute("SELECT rfq_id, status, account_id, kind, terms_json FROM rfqs WHERE status IN ('REQUESTED','QUOTED') AND terms_json LIKE ?"
                          " ORDER BY rfq_id", (f"%{ref}%",)).fetchall():
        t = _jloads(r["terms_json"], {}) or {}
        if str(t.get("client_ref") or "").strip() == str(ref):
            return {"rfq_id": r["rfq_id"], "status": r["status"], "account_id": r["account_id"], "kind": r["kind"]}
    return None


def _book_irs(ctx: TxContext, services, acct: dict, terms: IrsTerms, deal: Deal, as_of: Optional[dict] = None) -> dict:
    """Post the IM, create the IRS contract, its SWAP instrument row and (TERM) the first fixing - ``otc.book`` and, with ``as_of``,
    ``otc.book_as_of`` (the trade date and the execution time of the as-of record; every entry dated the value date through
    ``ctx.asof_date``)."""
    conn = ctx.conn
    aid = acct["account_id"]
    broker = prime.broker_of(acct)
    cid = next_id(conn, "swap")
    ccy = terms.currency
    idem = f"im:{cid}:open"
    funding = fund_commitment(ctx, services, aid, ccy, deal.im_c, "otc", idem)           # PO-07: against available, never cash owed elsewhere
    if deal.curve_spread:
        how = (f"booked at the dealt rate {_pct(deal.dealt)} = par {_pct(deal.par)} {'+' if deal.curve_spread >= 0 else '-'} {abs(deal.curve_spread / BP).quantize(Decimal('0.1'))} bp"
               f" contract curve spread {'+' if terms.pay_fixed else '-'} {deal.hw_bp} bp half-width")
    else:
        how = f"dealt at par {_pct(deal.par)} {'+' if terms.pay_fixed else '-'} {deal.hw_bp} bp"
    memo = (f"OTC IM {cid}: IRS {'pay' if terms.pay_fixed else 'receive'} fixed {_pct(deal.dealt)} on {fmt_money(terms.notional_c)} {ccy} to {deal.maturity.isoformat()}"
            f" with {broker}; IM = |DV01| {fmt_money(abs(deal.dv01_c))} x {_num_text(deal.im_bp)} bp = {fmt_money(deal.im_c)} ({deal.im_source}); {how};"
            f" mark0 {fmt_money(deal.mark0_c)} = cost of dealing")
    entry_id = ctx.post(Entry("OTC_IM", memo, [dr(aid, "1330", ccy, deal.im_c, instrument_id=cid, memo="OTC initial margin"), cr(aid, "1010", ccy, deal.im_c, memo="IM paid")],
                              idem, ref_type="otc", ref_id=cid))
    _insert_instrument(services, ctx, cid, deal, broker)
    t = terms.to_dict()
    t.update(fixed_rate=str(deal.dealt), fixed_rate_given=terms.fixed_rate is not None, par_rate=str(deal.par), half_width=str(deal.hw), half_width_bp=str(deal.hw_bp),
             otc_spread_mult=str(deal.mult), annuity0=str(deal.annuity), mark0_c=deal.mark0_c, dv01_0_c=deal.dv01_c, im_bp=_num_text(deal.im_bp), start_date=deal.start.isoformat(),
             maturity_date=deal.maturity.isoformat(), trade_date=(as_of["trade_date"] if as_of else ny_of(ctx.ts).date()).isoformat(),
             fixed_periods=[[s.isoformat(), e.isoformat(), p.isoformat()] for s, e, p in deal.fixed_periods],
             float_periods=[[s.isoformat(), e.isoformat(), p.isoformat()] for s, e, p in deal.float_periods], dealt_ts=(as_of["exec_ts"] if as_of else ctx.ts), model=MODEL,
             **_spread_fields(deal.curve_spread))
    if deal.curve_spread:
        t["calibrate_curve_spread_to_rate"] = True
    inputs = dict(deal.curve_inputs, as_of=ctx.ts, par_rate=str(deal.par), annuity=str(deal.annuity), remaining_periods=deal.n_periods, sofr=str(deal.fix_pct),
                  sofr_obs_date=deal.fix_obs.isoformat(), fixing=terms.fixing, model=MODEL, **_spread_fields(deal.curve_spread))
    if as_of:
        inputs["valuation_date"] = as_of["trade_date"].isoformat()
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
    """The clean PV of an OPEN IRS valued as of ``valuation_date`` (default: today) - module docstring SWAP_MARK; a past valuation date
    on the curve OF that date (C12, ``dfs_for(on=)``), today on the curve available now; the contract curve spread (owner Q1 variant A)
    is added to the par rate: ``sign x N x (par + spread - K) x A``.  Raises ConflictError NO_CURVE."""
    terms = _terms(row)
    on = valuation_date or ny_of(int(now_ts)).date()
    remaining = _remaining(row, on)
    base = {"as_of": int(now_ts), "valuation_date": on.isoformat(), "contract_id": row["contract_id"], "dealt_rate": terms.get("fixed_rate"), "model": MODEL,
            "quality": "computed"}
    if row.get("status") != "OPEN":
        return 0, dict(base, note=f"{row.get('status')}: no remaining cash flows", remaining_periods=0)
    if not remaining:
        return 0, dict(base, note="no remaining payment periods (at maturity)", remaining_periods=0)
    dfs, inputs = dfs_for(services, len(remaining), shift_bp, on=valuation_date)
    par, annuity, par_raw = par_annuity(dfs)
    spread = curve_spread(terms)
    mark = model_mark_c(int(row["notional_c"]), bool(terms.get("pay_fixed")), terms.get("fixed_rate"), par + spread, annuity)
    inputs.update(base, par_rate=str(par), par_rate_raw=str(par_raw), annuity=str(annuity), remaining_periods=len(remaining),
                  next_payment=remaining[0][2].isoformat(), last_reset_date=row.get("last_reset_date"), **_spread_fields(spread),
                  formula=(f"{'+' if terms.get('pay_fixed') else '-'}N x (par {par}" + (f" {'+' if spread >= 0 else '-'} spread {abs(spread)}" if spread else "")
                           + f" - K {terms.get('fixed_rate')}) x A {annuity}"))
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
                        "no curve: the last computed mark, labelled stale")


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
    for k in ("underlying", "fx"):
        part = inputs.get(k) or {}
        if part.get("close_date"):         # a recorded / observed CLOSE of a dated session: its date, whatever time the record carries
            add_iso(part.get("close_date"))
        else:
            add_epoch(part.get("quote_time"))
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
    row = _open_contract(conn, cid)
    if row["status"] != "OPEN":
        return _done_noop(ctx, JOB_VM, key, "NOT_OPEN", f"{cid} is {row['status']}: no variation margin")
    existing = conn.execute("SELECT * FROM otc_vm WHERE contract_id=? AND vm_date=?", (cid, ds)).fetchone()
    if existing is not None:
        _job_done(ctx, JOB_VM, key, "DONE", "already posted")
        return {"status": "DONE", "already": True, "skipped": True, "posted": False, "key": key, "mark_c": int(existing["mark_c"]), "delta_c": int(existing["delta_c"]),
                "entry_id": existing["entry_id"]}
    return _vm_core(ctx, services, row, ds)


class AsOfInputMissing(ConflictError):
    """409 ASOF_INPUT_MISSING: an as-of booking whose day-0 valuation input (the trade date's close, proxy or curve) does not exist."""


def _vm_core(ctx: TxContext, services, row: dict, ds: str, strict: bool = False) -> dict:
    """The variation margin of contract ``row`` for date ``ds`` (module docstring SWAP_VM; FINSIM1 C10 / P4 task 3): valued with the
    inputs OF that date - the curve of that date (C12), the recorded close, the proxy observation of that date - and posted with business
    date ``ds``; its funding runs under ``ctx.asof_date`` = ``ds`` (the sweep shares the date, POSTCLOSE-06) with CSA netting (C21: a VM
    payment to the CSA counterparty may use that counterparty's collateral).  ``strict`` (the as-of booking's day-0 VM): an input that
    does not exist is 409 ASOF_INPUT_MISSING and the booking writes nothing; the payment is never an overdraft."""
    conn = ctx.conn
    cid = row["contract_id"]
    key = f"{cid}:{ds}"
    idem = f"swapvm:{key}"
    aid, ccy, mode = row["account_id"], row["currency"], row["margin_mode"]
    d = date.fromisoformat(ds)
    today = ny_of(ctx.ts).date()
    first = vm_first_date(row)
    if d > today or d < first:
        raise RejectError("KEY_INVALID", f"VM date {ds} is outside {first.isoformat()} .. today {today.isoformat()}")

    def missing(code: str, note: str):
        raise AsOfInputMissing("ASOF_INPUT_MISSING", f"{cid}: the valuation input of {ds} does not exist ({code}: {note}); the as-of booking writes nothing",
                               {"contract_id": cid, "date": ds, "code": code})
    later = conn.execute("SELECT MAX(vm_date) AS d FROM otc_vm WHERE contract_id=? AND vm_date>?", (cid, ds)).fetchone()
    if later is not None and later["d"]:
        _resolve_attention(ctx, VM_PENDING_CODES, key)
        return _done_noop(ctx, JOB_VM, key, "SUPERSEDED", f"the VM of {later['d']} already measured the change since the previous VM; nothing is posted for {ds}")
    if ctx.ts < epoch_of(d, VM_ET):
        if strict:
            missing("VM_NOT_YET", f"the close of {ds} is not past yet")
        return _skip(ctx, JOB_VM, key, "VM_NOT_YET", f"{cid}: the VM of {ds} runs from {VM_ET} ET", aid, cid, quiet=True)
    acct = account_row(conn, aid)
    if acct is None or acct["status"] != "OPEN":
        raise RejectError("ACCOUNT_CLOSED", f"account {aid} is not open")
    if row["kind"] != "IRS":
        fn = _mark_hook(services, row["kind"])
        if fn is None:
            if strict:
                missing("NOT_BUILT", f"{KIND_LABELS.get(row['kind'], row['kind'])} marks are not available in this version")
            return _skip(ctx, JOB_VM, key, "NOT_BUILT", f"{KIND_LABELS.get(row['kind'], row['kind'])} marks are not available in this version", aid, cid)
        # priced as of d: the underlying's / the pair's CLOSE of the last trading day on or before d when it is recorded or observed at
        # the close; a hook answers (None, {vm_outcome: CLOSE_PENDING | CAUGHT_UP, note, quiet}) when that close is not known (LC-3)
        got = _call(fn, services, row, valuation_date=d, at=ctx.ts, snapshot=ctx.marks)
        if isinstance(got, tuple) and got[0] is None and isinstance(got[1], dict) and got[1].get("vm_outcome"):
            if strict:
                missing(str(got[1].get("vm_outcome")), str(got[1].get("note") or ""))
            return _vm_pending(ctx, row, key, ds, got[1])
        if got is None or not isinstance(got, tuple) or got[0] is None:
            if strict:
                missing("NO_MARK", str((got[1] or {}).get("note") or (got[1] or {}).get("reason") or "no mark") if isinstance(got, tuple) else "no mark")
            return _skip(ctx, JOB_VM, key, "NO_MARK", f"{cid}: the {KIND_LABELS.get(row['kind'], row['kind'])} mark is unavailable", aid, cid)
        mark_c, inputs = int(got[0]), dict(got[1] or {})
        after = _observed_after(inputs, d)
        if after is not None:
            if strict:
                missing("CAUGHT_UP", f"the inputs available are dated {after.isoformat()}, after {ds}")
            note = (f"CAUGHT_UP: the {row['kind']} inputs available are dated {after.isoformat()}, after {ds} (no close of {ds} was observed or recorded);"
                    f" nothing is posted for {ds}, the change is measured on a later VM date")
            _job_done(ctx, JOB_VM, key, "SKIPPED", note)
            _resolve_attention(ctx, ("NO_MARK", "NOT_BUILT"), key)
            return {"status": "SKIPPED", "skipped": True, "retry": False, "quiet": True, "code": "CAUGHT_UP", "note": note, "key": key, "posted": False}
    else:
        # P4 task 3: priced at d when the curve OF d exists (the curve of d or of the 3 business days before it, C12); CAUGHT_UP only when
        # it truly does not exist and a later curve does (the change is then measured on that later date)
        if dated_curve(services, d) is None:
            as_of = curve_as_of(services)
            if strict:
                missing("NO_CURVE", f"no Treasury par curve of {ds} or of the 3 business days before it")
            if as_of is not None and as_of > d:
                note = (f"CAUGHT_UP: no Treasury par curve of {ds} (or of the 3 business days before it) was recorded and a later one exists ({as_of.isoformat()});"
                        f" nothing is posted for {ds}, the change is measured on a later VM date")
                _job_done(ctx, JOB_VM, key, "SKIPPED", note)
                _resolve_attention(ctx, ("NO_CURVE",), key)
                return {"status": "SKIPPED", "skipped": True, "retry": False, "quiet": True, "code": "CAUGHT_UP", "note": note, "key": key, "posted": False}
        try:
            mark_c, inputs = _model_mark(services, row, ctx.ts, valuation_date=d)
        except ConflictError as e:
            if strict:
                missing("NO_CURVE", e.message)
            return _skip(ctx, JOB_VM, key, "NO_CURVE", f"{cid}: {e.message}; the mark keeps its last value {fmt_money(int(row['mark_c']))} (stale)", aid, cid)
    # FX_CCS_PLAN D8 / K5 (1.0.6.1): under CTM a contract whose mark hook reports ``csa_exposure_c`` (a cross-currency swap: its FULL value,
    # the clean mark plus the notionals and accrued coupons on the balance sheet at the FX close) is margined on that exposure - the CSA
    # collateralises the dirty value - while NAV keeps the clean mark as the overlay.  The reference is the previous VM's ``otc_vm.mark_c``,
    # which then holds that exposure.  STM keeps the clean mark (settling the notional FX in cash would count it twice).
    exposure = None
    if mode == "CTM" and row["kind"] != "IRS" and inputs.get("csa_exposure_c") is not None:
        try:
            exposure = int(inputs["csa_exposure_c"])
        except (TypeError, ValueError):
            exposure = None
    vm_value = exposure if exposure is not None else mark_c
    ref_c = _reference_mark_c(conn, cid)
    delta = vm_value - ref_c
    what = (f"CSA exposure {fmt_money(vm_value)} (clean mark {fmt_money(mark_c)} + the contract's balance-sheet items {fmt_money(vm_value - mark_c)})"
            if exposure is not None else f"mark {fmt_money(mark_c)}")
    if delta == 0:
        basis = (f"curve as of {inputs.get('curve_as_of')}" if row["kind"] == "IRS"
                 else f"valued at {(inputs.get('underlying') or inputs.get('fx') or {}).get('basis') or 'the close'}")
        prev = conn.execute("SELECT MAX(vm_date) AS d FROM otc_vm WHERE contract_id=?", (cid,)).fetchone()
        against = (f"unchanged vs the variation margin of {prev['d']}" if prev is not None and prev["d"]
                   else "equal to the opening reference 0.00 (no variation margin was due before)")
        note = f"NO_CHANGE: {what} {against} ({basis})"
        _job_done(ctx, JOB_VM, key, "SKIPPED", note)
        _resolve_attention(ctx, VM_PENDING_CODES, key)
        return {"status": "SKIPPED", "skipped": True, "retry": False, "quiet": True, "code": "NO_CHANGE", "note": note, "key": key, "mark_c": mark_c, "delta_c": 0,
                "posted": False, **({"csa_exposure_c": exposure} if exposure is not None else {})}
    funding = None
    lines, new_balance, reset = _vm_lines(aid, ccy, cid, mode, int(row["vm_balance_c"]), delta)
    vm_basis = (f"par {inputs.get('par_rate')}" + (f" {inputs.get('curve_spread_bp')} bp contract curve spread" if inputs.get("curve_spread_bp") else "")
                + f" vs dealt {inputs.get('dealt_rate')}, annuity {inputs.get('annuity')}, curve as of {inputs.get('curve_as_of')}"
                if row["kind"] == "IRS" else str(inputs.get("formula") or inputs.get("model") or KIND_LABELS.get(row["kind"], row["kind"])))
    if exposure is not None:
        inputs = dict(inputs, clean_mark_c=mark_c, csa_exposure_c=exposure, vm_basis="CSA exposure (the full value): clean mark + notionals and accrued coupons at the FX close")
    memo = (f"Swap VM {cid} {ds} ({mode}): {what} - reference {fmt_money(ref_c)} = {fmt_money(delta)}"
            f"{' received' if delta > 0 else ' posted'}; {vm_basis}, valued as of {ds}; VM balance {fmt_money(int(row['vm_balance_c']))} -> {fmt_money(new_balance)}"
            f"{'; collateral, not P&L' if mode == 'CTM' else '; settled to market, the mark resets to 0'}")
    with asof_context(ctx, d):                    # the funding sweep and the entry share the VM's business date (C10, POSTCLOSE-06)
        if delta < 0:
            funding = fund_csa(ctx, services, aid, ccy, -delta, "swapvm", idem, overdraft=not strict)
        entry_id = ctx.post(Entry("VM", memo, lines, idem, ref_type="otc", ref_id=cid, business_date=ds))
    conn.execute("INSERT INTO otc_vm(contract_id, vm_date, mark_c, delta_c, mode, entry_id, inputs_json) VALUES (?,?,?,?,?,?,?)",
                 (cid, ds, vm_value, delta, mode, entry_id, dumps(inputs)))          # D8: the CSA exposure is the next VM's reference
    new_mark = 0 if reset else mark_c
    conn.execute("UPDATE otc_contracts SET mark_c=?, mark_ts=?, vm_balance_c=?, inputs_json=? WHERE contract_id=?", (new_mark, ctx.ts, new_balance, dumps(inputs), cid))
    _job_done(ctx, JOB_VM, key, "DONE", f"{mode} {fmt_money(delta)} (mark {fmt_money(mark_c)})")
    _resolve_attention(ctx, VM_PENDING_CODES, key)
    ctx.touched_instruments.add((aid, cid))
    ctx.event("SWAP_VM", account_id=aid, instrument_id=cid, ref_type="otc", ref_id=cid, date=ds, mode=mode, mark_c=mark_c, reference_c=ref_c, delta_c=delta,
              vm_balance_c=new_balance, entry_id=entry_id, funded=None if funding is None else funding.funded,
              **({"csa_exposure_c": exposure} if exposure is not None else {}))
    return {"status": "DONE", "key": key, "mode": mode, "mark_c": mark_c, "reference_c": ref_c, "delta_c": delta, "vm_balance_c": new_balance, "entry_id": entry_id,
            "posted": True, "funding": None if funding is None else (funding.to_dict() if hasattr(funding, "to_dict") else None), "inputs": inputs,
            **({"csa_exposure_c": exposure} if exposure is not None else {})}


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
    quiet_until = info.get("quiet_until")
    if quiet_until is not None:                   # POSTCLOSE-03: no warning before the input is normally published (one per key after it)
        quiet = bool(info.get("quiet")) or int(ctx.ts) < int(quiet_until)
    else:
        quiet = bool(info.get("quiet")) or not repeat
    waits = str(info.get("waits_for") or "that close (never an earlier or intraday price)")
    return _skip(ctx, JOB_VM, key, outcome, f"{cid}: {note}; the variation margin of {ds} waits for {waits}", aid, cid,
                 {"close_date": info.get("close_date")}, quiet=quiet)


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
        index = str(spec.index or "SOFR").upper()
        obs, pct = fixing_for(services, night, index)               # 409 RATE_UNAVAILABLE -> the ACCRUE_DAILY key SKIPs, retried; nothing written
        r = qprice(Decimal(pct) / Decimal(100))
        same = st is not None and st["segment_start"] == s.isoformat() and night != s
        f_prev = Decimal(str(st["rate"])) if same else ONE
        cum_prev = int(st["cum_c"]) if same else 0
        f_new = compound_step(f_prev, r)
        nights = (night - s).days + 1
        spread = Decimal(spec.spread or 0)
        if spread:
            # FX_CCS_PLAN 4.2 (the float cross-currency leg, ESTR + b): the spread is added, not compounded - cumulative
            # to_c(N x ((F - 1) + b x n / 360)) with n the nights of the period so far; the payment trues the estimate up to the index
            cum_new = to_c(from_c(notional_c) * ((f_new - ONE) + spread * Decimal(nights) / DAYS_360))
            how = f"cumulative to_c({fmt_money(notional_c)} x ((F - 1) + {spread} x {nights} / 360)) = {fmt_money(cum_new)}"
        else:
            cum_new = compounded_c(notional_c, f_new)
            how = f"cumulative to_c({fmt_money(notional_c)} x (F - 1)) = {fmt_money(cum_new)}"
        amount = cum_new - cum_prev
        _put_state(conn, kind, cid, s.isoformat(), notional_c, str(f_new), night_s, cum_new)
        memo = (f"Swap {cid} {spec.leg} leg ({spec.side}) night of {night_s}: {index} {pct} % ({obs.isoformat()}) compounded daily, factor {f_prev} x (1 + {r}/360) = {f_new}"
                f" after {nights} nights; {how}; period {s.isoformat()}..{e.isoformat()}"
                " (ACCRUAL, RATE_LAG, COMPOUND_ARREARS)")
        out = {"lines": _leg_lines(aid, cid, spec, amount), "amount_c": amount, "cum_c": cum_new, "coupon_c": cum_new if last else None, "leg": spec.leg, "rate": str(r),
               "basis": spec.basis, "period": [s.isoformat(), e.isoformat()], "memo": memo, "last": last, "factor": str(f_new), "obs_date": obs.isoformat(), "sofr": str(pct)}
        if index != "SOFR" or spread:
            out.update(index=index, index_pct=str(pct), spread=str(spread), nights=nights)
        return out
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
        entry_id = ctx.post(Entry("ACCRUAL", res["memo"], res["lines"], f"accr:{kind}:{ref}:{night_s}", ref_type="otc", ref_id=ref,
                                  business_date=lifecycle_business_date(row, night_d)))
        ctx.touched_instruments.add((row["account_id"], ref))
        ctx.event("SWAP_ACCRUAL", account_id=row["account_id"], instrument_id=ref, ref_type="otc", ref_id=ref, kind=kind, leg=res["leg"], night=night_s, amount_c=amount,
                  rate=res["rate"], factor=res.get("factor"), entry_id=entry_id)
    note = (f"{res['leg']} leg {fmt_money(amount)} at {res['rate']} night of {night_s}" if res.get("rate") else res.get("note", ""))
    return {"status": "DONE", "posted": entry_id is not None, "posted_c": abs(amount), "amount_c": amount, "entry_id": entry_id, "leg": res["leg"], "rate": res.get("rate"),
            "cum_c": res.get("cum_c"), "factor": res.get("factor"), "note": note}


def booking_of(row) -> str:
    try:
        return str((row.get("booking") if isinstance(row, dict) else row["booking"]) or "").upper()
    except (KeyError, IndexError):
        return ""


def entered_business_day(row) -> Optional[date]:
    """The accounting day an as-of contract was entered on (None for a live contract)."""
    if booking_of(row) not in (ASOF_BOOKING, HISTORICAL_BOOKING):
        return None
    ets = row.get("entered_ts") if isinstance(row, dict) else None
    if ets in (None, ""):
        return None
    return accounting_day(ny_of(int(ets)))


def lifecycle_business_date(row, night: date) -> Optional[str]:
    """D7 (P4 task 3): the business date real time would have given the accrual of night D of an as-of contract - D + 1, the day that
    night's accrual posts - while D is before the day the contract was entered; None otherwise (the posting day, as every nightly
    accrual).  VM and payments of D always post on D."""
    entered = entered_business_day(row)
    if entered is None or night >= entered:
        return None
    return (night + timedelta(days=1)).isoformat()


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
    # PORTFOLIO-08 / C11: the contract cost basis (an upfront or premium paid on 1335, received on 2345) is released into the swap result
    cost_paid = balance(conn, aid, "1335", ccy, cid)
    cost_recv = balance(conn, aid, "2345", ccy, cid)
    if cost_paid > 0:
        lines += [dr(aid, "4450", ccy, cost_paid, instrument_id=cid, memo="contract cost paid released (upfront / premium)"),
                  cr(aid, "1335", ccy, cost_paid, instrument_id=cid, memo="contract cost paid released")]
    elif cost_paid < 0:
        lines += [dr(aid, "1335", ccy, -cost_paid, instrument_id=cid, memo="contract cost released"), cr(aid, "4450", ccy, -cost_paid, instrument_id=cid, memo="contract cost released")]
    if cost_recv > 0:
        lines += [dr(aid, "2345", ccy, cost_recv, instrument_id=cid, memo="contract cost received released"),
                  cr(aid, "4450", ccy, cost_recv, instrument_id=cid, memo="contract cost received released (upfront / premium)")]
    elif cost_recv < 0:
        lines += [dr(aid, "4450", ccy, -cost_recv, instrument_id=cid, memo="contract cost released"), cr(aid, "2345", ccy, -cost_recv, instrument_id=cid, memo="contract cost released")]
    cost_released = cost_recv - cost_paid
    if cost_released:
        memo = f"{memo}; contract cost {fmt_money(cost_released)} released to the swap result"
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
    if cash < 0 and overdraft:              # a lifecycle obligation (maturity): settled cash, the overdraft rule (s11.6); CSA netting (C21)
        funding = fund_csa(ctx, services, aid, ccy, -cash, "otc", idem, overdraft=True)
    elif cash < 0:                          # the owner's termination: against available cash (PO-07), else 409 INSUFFICIENT_FUNDS; CSA netting
        funding = fund_commitment(ctx, services, aid, ccy, -cash, "otc", idem, csa_netting=True)
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
            "im_released_c": im, "cost_released_c": cost_released, "realised_c": result + cost_released,
            "funding": None if funding is None else (funding.to_dict() if hasattr(funding, "to_dict") else None)}


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
        with asof_context(ctx, d):               # the payment of d is funded on d (C10 / C20) from cash that may include the CSA collateral (C21)
            funding = fund_csa(ctx, services, aid, ccy, -net, "swappay", idem, overdraft=True)
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
            f" through the night of {through.isoformat()}" + (f" ({len(caught)} night accrual(s) posted first)" if caught else ""))
    closed = close_contract(ctx, services, row, "TERMINATED", value_c, ref_c, f"otc:{cid}:terminate", "OTC_TERMINATE", memo, True, False)
    ctx.event("OTC_TERMINATED", account_id=row["account_id"], instrument_id=cid, ref_type="otc", ref_id=cid, termination_value_c=value_c, inputs=inputs,
              caught_up=len(caught), **{k: v for k, v in closed.items() if k not in ("funding", "value_c")})
    view = contract_view(services, _contract_row(conn, cid), conn=conn, detail=True, now_ts=ctx.ts)
    entries = [c["entry_id"] for c in caught] + [e for e in (closed["entry_id"], closed["im_entry_id"]) if e is not None]
    return {"contract": view, "contract_id": cid, "termination_value_c": value_c, "result_c": closed["result_c"], "cash_c": closed["cash_c"], "entries": entries,
            "cost_released_c": closed.get("cost_released_c", 0), "realised_c": closed.get("realised_c"),
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


def _row_get(row, name: str):
    """A column of an ``otc_contracts`` row (a dict or a sqlite3.Row); None when the column does not exist (an older database)."""
    try:
        return row.get(name) if isinstance(row, dict) else row[name]
    except (KeyError, IndexError):
        return None


def booked_business_day(row) -> Optional[date]:
    """P8R1-02: the business day of a LIVE booking - the accounting day (ACCT_DAY: the 17:00 ET cut-off) of the instant it was booked
    (``entered_ts``, else the terms' ``dealt_ts``), the day its IM and the IM's funding sweep carry.  None for an as-of booking (it keeps
    the owner's value date, D7) and for a row that records neither instant."""
    if booking_of(row) in (ASOF_BOOKING, HISTORICAL_BOOKING):
        return None
    ts = _row_get(row, "entered_ts")
    if ts in (None, ""):
        try:
            ts = _terms({k: row[k] for k in row.keys()} if not isinstance(row, dict) else row).get("dealt_ts")
        except (ValueError, TypeError, AttributeError):
            ts = None
    if ts in (None, "") or isinstance(ts, bool):
        return None
    try:
        return accounting_day(ny_of(int(ts)))
    except (ValueError, TypeError, OverflowError):
        return None


def vm_first_date(row) -> date:
    """The first date of a contract's SWAP_VM: its start date; P8B-06 (FX_CCS_PLAN G16): a cross-currency swap starts on the spot date of its
    trade date, and it is marked - and margined under the CSA - from its trade date (``terms.trade_date``), not only from its start.
    P8R1-02: a LIVE booking is never margined on a business day before its own - the first VM date is at least the booking's accounting day
    (``booked_business_day``).  A contract booked after the 17:00 ET cut-off of day D (its IM and the IM's sweep carry business date D + 1)
    gets its first variation margin on D + 1, valued at the close of D + 1 - never on the closed day D after D's snapshot, which put one
    booking on two business dates and the day-one cost of dealing into a day already closed.  Equity forwards, TRS, IRS and CDX start on
    the New York calendar date of the booking, so this is where they were dated wrong; an as-of booking keeps its value date (D7)."""
    start = _date(row["start_date"])
    first = start
    if row["kind"] == "XCCY":
        # FX_CCS_PLAN D8 (G16 / G17): while no VM exists, the first key is min(start, COALESCE(trade_date, start)) - the column, else the terms;
        # a NULL or unreadable trade date never raises (it falls back to the start date)
        try:
            td = _row_get(row, "trade_date") or _terms({k: row[k] for k in row.keys()} if not isinstance(row, dict) else row).get("trade_date")
            if td and _date(td) < start:
                first = _date(td)
        except (ValueError, TypeError, AttributeError):
            pass
    booked = booked_business_day(row)
    if booked is not None and booked > first:
        first = booked
    return first


def _jobs(conn: sqlite3.Connection, job: str, cid: str, lo: Optional[str] = None, hi: Optional[str] = None) -> Dict[str, sqlite3.Row]:
    lo_key, hi_key = f"{cid}:{lo or '0000-00-00'}", f"{cid}:{hi or '9999-99-99'}"
    return {r["key"]: r for r in conn.execute("SELECT key, status, note FROM jobs_done WHERE job=? AND key>=? AND key<=?", (job, lo_key, hi_key)).fetchall()}


def _terminal(r) -> bool:
    return r is not None and (r["status"] == "DONE" or (r["status"] == "SKIPPED" and str(r["note"] or "").startswith(VM_TERMINAL)))


PROXY_RETRY_ET = "08:00"


def _next_weekday(d: date) -> date:
    d = d + timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def _prev_weekday(d: date) -> date:
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def proxy_retry_ts(d: date) -> int:
    """POSTCLOSE-03: FRED publishes a business day's OAS the next business morning, so a CDX VM of date ``d`` waiting for it is asked for
    again from 08:00 ET of the weekday after the last weekday on or before ``d`` - not every few minutes all night."""
    return epoch_of(_next_weekday(_prev_weekday(d)), PROXY_RETRY_ET)


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
        d = vm_first_date(row)
        if last is not None and last["d"]:
            d = max(d, date.fromisoformat(last["d"]) + timedelta(days=1))
        if d > today:
            continue
        done = _jobs(conn, JOB_VM, cid, d.isoformat(), today.isoformat())
        while d <= today:
            if now < epoch_of(d, VM_ET):
                break
            ds = d.isoformat()
            r = done.get(f"{cid}:{ds}")
            if not _terminal(r):
                if (row["kind"] in CDS_KINDS and r is not None and r["status"] == "SKIPPED" and str(r["note"] or "").startswith("CLOSE_PENDING")
                        and now < proxy_retry_ts(d)):
                    break                       # the OAS of that date is not published before the next business morning (later dates wait too)
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
def _mark_dict(services, row: dict, now_ts: int, snapshot=None, got=None) -> dict:
    if got is None:
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
    live_got = mark(services, row, ts, snapshot=snapshot) if is_open else None
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
           "mark": _mark_dict(services, row, ts, snapshot, got=live_got),
           "dv01_c": dv01, "im_balance_c": balance(conn, aid, "1330", ccy, cid), "vm_posted_c": balance(conn, aid, "1320", ccy, cid),
           "vm_received_c": balance(conn, aid, "2330", ccy, cid), "accrued_receive_c": balance(conn, aid, "1225", ccy, cid), "accrued_pay_c": balance(conn, aid, "2340", ccy, cid),
           "result_c": balance(conn, aid, "4450", ccy, cid), "fixing": fixing, "next_fix_date": next_fix, "next_pay_date": next_pay, "next_events": events,
           "remaining_periods": remaining, "terms": terms, "conventions": list(CONVENTIONS),
           "financing_c": balance(conn, aid, "4451", ccy, cid), "dividends_c": balance(conn, aid, "4452", ccy, cid), "coupons_c": balance(conn, aid, "4453", ccy, cid),
           "cost_c": balance(conn, aid, "1335", ccy, cid) - balance(conn, aid, "2345", ccy, cid)}
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
    out.update(_asof_view(conn, row, terms))
    # FX_CCS_PLAN K5 (1.0.6.1): the owner reference, the FX desk deals linked to the contract (an inception conversion, settlement-funding
    # purchases) and its confirmation block (the kind's own when it gives one)
    out["client_ref"] = _row_get(row, "client_ref")
    out["linked_deals"] = linked_deals(conn, row)
    out["confirmation"] = (det or {}).get("confirmation") if isinstance(det, dict) and (det or {}).get("confirmation") else confirmation_of(row, terms, out)
    out["position"] = _position_fields(services, row, live_got, conn, ts) if is_open else None      # C13: what NAV carries now, and the display fields
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

LINK_WORDS = {"xccy_start": "inception conversion: sells the currency received at the start exchange (settles with it, payment versus payment)",
              "otc_funding": "settlement funding: buys the currency an obligation of the contract needs, two business days before it is due",
              "xccy_event": "settlement funding of a cross-currency swap payment"}


def linked_deals(conn: sqlite3.Connection, row: dict) -> List[dict]:
    """FX_CCS_PLAN K5 / D6 / D9: the FX desk deals linked to a contract - its inception conversion (``inputs.link`` type ``xccy_start``) and the
    settlement-funding purchases (type ``otc_funding``, naming the contracts they fund) - with their legs, value date and status.  Read only;
    [] when there are none (every contract booked before 1.0.6.1)."""
    cid = str(row.get("contract_id") or "")
    if not cid or not _has_table(conn, "fx_deals"):
        return []
    found: Dict[str, dict] = {}
    cand = {r["deal_id"]: r for r in conn.execute("SELECT * FROM fx_deals WHERE inputs_json LIKE ? ORDER BY trade_ts, deal_id", (f"%{cid}%",)).fetchall()}
    if has_columns(conn, "settlements", "ref_type", "ref_id"):
        for s in conn.execute("SELECT DISTINCT fx_deal_id FROM settlements WHERE ref_id=? AND fx_deal_id IS NOT NULL", (cid,)).fetchall():
            if s["fx_deal_id"] not in cand:
                r = conn.execute("SELECT * FROM fx_deals WHERE deal_id=?", (s["fx_deal_id"],)).fetchone()
                if r is not None:
                    cand[r["deal_id"]] = r
    for did, r in cand.items():
        inp = _jloads(r["inputs_json"], {}) or {}
        link = inp.get("link") if isinstance(inp.get("link"), dict) else {}
        ids = {str(link.get("id") or "")} | {str(x) for x in (link.get("contracts") or [])}
        tagged = False
        if has_columns(conn, "settlements", "ref_type", "ref_id"):
            tagged = conn.execute("SELECT 1 FROM settlements WHERE fx_deal_id=? AND ref_id=? LIMIT 1", (did, cid)).fetchone() is not None
        if cid not in ids and not tagged:
            continue
        ltype = str(link.get("type") or ("xccy_start" if tagged else ""))
        found[did] = {"deal_id": did, "kind": r["kind"], "link_type": ltype or None, "what": LINK_WORDS.get(ltype, "linked FX deal"),
                      "buy_ccy": r["buy_ccy"], "buy_c": int(r["buy_c"]), "sell_ccy": r["sell_ccy"], "sell_c": int(r["sell_c"]), "rate": r["rate"], "mid": r["mid"],
                      "spread_cost_c": int(r["spread_cost_c"] or 0), "trade_date": r["trade_date"], "value_date": r["value_date"], "status": r["status"],
                      "settled_ts": r["settled_ts"], "due": link.get("due"), "obligations": link.get("obligations"), "contracts": sorted(x for x in ids if x)}
    return sorted(found.values(), key=lambda d: (d["value_date"] or "", d["deal_id"]))


def confirmation_of(row: dict, terms: dict, view: Optional[dict] = None) -> dict:
    """FX_CCS_PLAN K5 / 10.7: a contract's confirmation block in trader words, when its kind gives none of its own - the parties, the dates,
    the notional, the CSA and margin terms, the owner reference and the confirmation status ("CONFIRMED - simulated confirmation"; never
    "dealer-confirmed")."""
    v = view or {}
    status = _row_get(row, "confirm_status")
    booking = booking_of(row) or None
    dealer = config.PRIME_BROKERS.get(str(row.get("broker") or "").upper(), str(row.get("broker") or "the dealer"))
    return {"status": status, "label": confirm_label(booking, status) or NO_CONFIRM_LABEL,
            "parties": {"fund": row.get("account_id"), "counterparty": dealer, "role": "prime broker and OTC dealer (simulated)"},
            "kind": KIND_LABELS.get(row.get("kind"), row.get("kind")), "trade_date": _row_get(row, "trade_date") or terms.get("trade_date"),
            "start_date": row.get("start_date"), "maturity_date": row.get("maturity_date"), "notional_c": int(row.get("notional_c") or 0),
            "currency": row.get("currency"), "client_ref": _row_get(row, "client_ref"),
            "csa": f"the existing CSA with {dealer}: variation margin daily in USD ({row.get('margin_mode')}), initial margin in USD held by {dealer}",
            "im_c": int(row.get("im_c") or 0), "side_text": v.get("side_text")}


def source_of(conn: sqlite3.Connection, row: dict, cache: Optional[dict] = None) -> dict:
    """15.7 item 7: the contract's source and its label - ``blotters.source_label(conn, "otc_contract", row)`` (P2: the D1 as-of label
    with the entered time and the batch) when that module is in the tree, else the same wording here."""
    try:
        from .. import blotters as _bl          # optional module (FINSIM1 P2); imported lazily, never required
        fn = getattr(_bl, "source_label", None)
    except Exception:   # noqa: BLE001 - a missing or broken module: the wording below
        fn = None
    if callable(fn):
        try:
            got = fn(conn, "otc_contract", row, cache)
            if isinstance(got, dict) and got.get("label"):
                return got
        except Exception:   # noqa: BLE001 - the label is display only
            pass
    booking = booking_of(row) or None
    dealer = config.PRIME_BROKERS.get(str(row.get("broker") or "").upper(), str(row.get("broker") or "the dealer"))
    if booking == HISTORICAL_BOOKING:
        return {"source": "historical_model", "label": HISTORICAL_LABEL, "asof": False, "entered_ts": row.get("entered_ts"), "batch_id": None, "booking": booking,
                "badge": "MODEL"}
    if booking == ASOF_BOOKING:
        batch = None
        r = conn.execute("SELECT payload_json FROM commands WHERE id=?", (row.get("command_id"),)).fetchone() if row.get("command_id") is not None else None
        if r is not None:
            try:
                batch = ((json.loads(r[0] or "{}") or {}).get("as_of") or {}).get("batch_id")
            except ValueError:
                batch = None
        ets = row.get("entered_ts")
        when = ny_of(int(ets)).strftime("%Y-%m-%d %H:%M ET") if ets not in (None, "") else "at an unrecorded time"
        label = (f"As-of booking - simulated OTC trade with {dealer}, value date {row.get('value_date') or row.get('start_date')}, entered {when} by the operator at"
                 f" the owner's instruction" + (f" (batch {batch})" if batch else ""))
        return {"source": "as_of", "label": label, "asof": True, "entered_ts": ets, "batch_id": batch, "booking": booking, "badge": "AS-OF"}
    return {"source": "simulated", "label": f"Simulated OTC trade with {dealer}", "asof": False, "entered_ts": None, "batch_id": None, "booking": booking, "badge": None}


def _asof_view(conn: sqlite3.Connection, row: dict, terms: dict) -> dict:
    """The as-of record of a contract for the views (15.7 item 7, C10): booking, the entered / trade / value dates, the confirmation, the
    source label; the contract curve spread of an IRS (P4 task 4)."""
    booking = booking_of(row) or None
    src = source_of(conn, row)
    status = row.get("confirm_status")
    out = {"booking": booking, "entered_ts": row.get("entered_ts"), "import_key": row.get("import_key"),
           "trade_date": row.get("trade_date") or terms.get("trade_date"), "value_date": row.get("value_date") or row.get("start_date"),
           "confirm_status": status, "confirmed_ts": row.get("confirmed_ts"), "confirm_label": confirm_label(booking, status),
           "source": src, "source_label": src.get("label"), "batch_id": src.get("batch_id"), "asof": bool(src.get("asof"))}
    if status is None:
        out["confirm_note"] = NO_CONFIRM_LABEL
    spread = curve_spread(terms)
    if spread:
        out.update(_spread_fields(spread))
    return out

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


# ---- otc.book_as_of (FINSIM1 C10, OMS-04) ------------------------------------------------------------------------------------------------
def _truthy(v) -> bool:
    if isinstance(v, bool):
        return v
    return str(v if v is not None else "").strip().lower() in ("true", "1", "yes")


def parse_as_of(payload: dict, now_ts: int) -> dict:
    """The ``as_of`` block of an as-of booking: ``{trade_date, value_date, exec_ts (an ISO time with its offset), batch_id, line_key,
    historical_model?}`` -> dates, the execution epoch and the flags (400 AS_OF naming the field otherwise).  The execution time is on
    the trade date (New York), not after now; the value date is on or after the trade date."""
    a = payload.get("as_of")
    if not isinstance(a, dict):
        raise RejectError("AS_OF", "as_of {trade_date, value_date, exec_ts, batch_id, line_key} is required", {"field": "as_of"})
    try:
        trade_date = date.fromisoformat(str(a.get("trade_date") or ""))
    except ValueError as e:
        raise RejectError("AS_OF", f"as_of.trade_date must be an ISO date, got {a.get('trade_date')!r}", {"field": "trade_date"}) from e
    try:
        value_date = date.fromisoformat(str(a.get("value_date") or a.get("trade_date")))
    except ValueError as e:
        raise RejectError("AS_OF", f"as_of.value_date must be an ISO date, got {a.get('value_date')!r}", {"field": "value_date"}) from e
    raw = str(a.get("exec_ts") or "")
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError as e:
        raise RejectError("AS_OF", f"as_of.exec_ts must be an ISO time with its UTC offset (2026-10-21T19:45:00-04:00), got {raw!r}", {"field": "exec_ts"}) from e
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=NY)
    exec_ts = int(dt.timestamp())
    batch_id, line_key = str(a.get("batch_id") or "").strip(), str(a.get("line_key") or "").strip()
    if not batch_id or not line_key:
        raise RejectError("AS_OF", "as_of.batch_id and as_of.line_key are required (the import batch and its line)", {"field": "batch_id" if not batch_id else "line_key"})
    if ny_of(exec_ts).date() != trade_date:
        raise RejectError("AS_OF", f"as_of.exec_ts {raw} is not on the trade date {trade_date.isoformat()} (New York)", {"field": "exec_ts"})
    if value_date < trade_date:
        raise RejectError("AS_OF", f"the value date {value_date.isoformat()} is before the trade date {trade_date.isoformat()}", {"field": "value_date"})
    if exec_ts > int(now_ts):
        raise RejectError("AS_OF", f"an as-of booking records a past execution; {raw} is after now", {"field": "exec_ts"})
    return {"trade_date": trade_date, "value_date": value_date, "exec_ts": exec_ts, "exec_iso": raw, "batch_id": batch_id, "line_key": line_key,
            "historical_model": _truthy(a.get("historical_model"))}


def _dec_eq(a, b) -> bool:
    try:
        return Decimal(str(a)) == Decimal(str(b))
    except (InvalidOperation, ValueError, TypeError):
        return False


def asof_terms_mismatch(kind: str, payload_terms: dict, row: dict, a: dict) -> List[dict]:
    """D10: every dealt term of the payload against the contract as stored - ``[{term, payload, stored}]`` (empty when they agree)."""
    t = dict(payload_terms or {})
    st = _terms(row)
    out: List[dict] = []

    def check(term, want, have, eq=None):
        same = eq(want, have) if eq is not None else want == have
        if not same:
            out.append({"term": term, "payload": None if want is None else str(want), "stored": None if have is None else str(have)})
    check("trade_date", a["trade_date"].isoformat(), str(st.get("trade_date") or ""))
    if kind == "TRS":
        check("underlying_id", str(t.get("underlying_id") or "").strip().upper(), str(st.get("underlying_id") or ""))
        check("units", str(t.get("units")), str(st.get("units")), _dec_eq)
        check("receiver", _truthy(t.get("receiver")) if "receiver" in t else str(t.get("side") or "").upper().startswith("RECEIVE"), bool(st.get("receiver")))
        check("reset_months", str(t.get("reset_months", 1)), str(st.get("reset_months")), _dec_eq)
        check("tenor_months", str(t.get("tenor_months", 12)), str(st.get("tenor_months")), _dec_eq)
        check("reset_px", t.get("reset_px"), row.get("reset_px"), _dec_eq)
        check("reset_px0", t.get("reset_px"), st.get("reset_px0"), _dec_eq)
        check("value_date", a["value_date"].isoformat(), str(row.get("start_date")))
    elif kind == "CDX":
        check("index", str(t.get("index") or "").upper().replace("CDX.NA.", ""), str(st.get("index") or ""))
        if t.get("series") not in (None, ""):
            check("series", str(t.get("series")).upper().lstrip("S"), str(st.get("series")), _dec_eq)
        check("tenor", str(t.get("tenor") or "5Y").upper(), str(st.get("tenor") or ""))
        check("side", str(t.get("side") or "").upper(), str(st.get("side") or ""))
        want_n = to_c(str(t.get("notional"))) if t.get("notional") not in (None, "") else t.get("notional_c")
        check("notional_c", want_n, row.get("notional_c"), _dec_eq)
        check("dealt_spread_bp", t.get("dealt_spread_bp"), st.get("deal_spread_bp"), _dec_eq)
    elif kind == "IRS":
        want_n = to_c(str(t.get("notional"))) if t.get("notional") not in (None, "") else t.get("notional_c")
        check("notional_c", want_n, row.get("notional_c"), _dec_eq)
        check("pay_fixed", _truthy(t.get("pay_fixed")), bool(st.get("pay_fixed")))
        check("tenor_months", t.get("tenor_months"), st.get("tenor_months"), _dec_eq)
        check("fixed_rate", t.get("fixed_rate"), st.get("fixed_rate"), _dec_eq)
        for k, default in (("fixed_months", 6), ("float_months", 3)):
            check(k, t.get(k, default), st.get(k), _dec_eq)
        for k, default in (("fixed_basis", "30/360"), ("float_basis", "ACT/360"), ("float_index", "SOFR"), ("fixing", "TERM")):
            check(k, str(t.get(k) or default).upper(), str(st.get(k) or "").upper())
        check("value_date", a["value_date"].isoformat(), str(row.get("start_date")))
    return out


def h_book_as_of(ctx: TxContext, payload: dict) -> dict:
    """``otc.book_as_of {account_id, kind, terms, as_of: {trade_date, value_date, exec_ts, batch_id, line_key, historical_model?}}`` (C10):
    an OTC contract the owner dealt on a past date, booked at his dealt terms exactly (D10) as of that date - the IM, the upfront and the
    trade date's variation margin with business date = the value date (D7, through ``ctx.asof_date``), its valuation inputs those OF
    the trade date (the curve, SOFR, the CDX proxy plus the account basis, the TRS underlying's close).  Refused: actor ``ui`` (403
    AS_OF_FORBIDDEN), a line key already booked (409 DUPLICATE_IMPORT), a missing input (409 ASOF_INPUT_MISSING), a contract that would
    differ from the payload in any dealt term (409 ASOF_TERMS_MISMATCH) - each writes nothing.  The contract reads CONFIRMED (as-of)."""
    if str(ctx.actor or "") == "ui":
        raise Forbidden("AS_OF_FORBIDDEN", "an as-of booking is entered by the operator at the owner's instruction (the historical import), not from the trading"
                        " screens", {"actor": ctx.actor})
    services = _services(ctx)
    kind = prime.require_otc_kind(payload.get("kind"))
    aid = str(payload.get("account_id") or "")
    conn = ctx.conn
    acct = _pb_account(conn, aid)
    require_pb_otc(services, aid)
    a = parse_as_of(payload, ctx.ts)
    ensure_schema(getattr(services, "db", None))
    dup = conn.execute("SELECT contract_id FROM otc_contracts WHERE import_key=?", (a["line_key"],)).fetchone()
    if dup is not None:
        raise ConflictError("DUPLICATE_IMPORT", f"line {a['line_key']} is already booked as {dup['contract_id']}; nothing was written",
                            {"line_key": a["line_key"], "contract_id": dup["contract_id"]})
    if kind not in ASOF_KINDS:
        raise ConflictError("ASOF_INPUT_MISSING", f"an as-of {KIND_LABELS.get(kind, kind)} needs the volatility / FX inputs of its trade date, which this build does not"
                            " record; nothing was written", {"kind": kind, "supported": list(ASOF_KINDS)})
    terms = dict(payload.get("terms") or {})
    booking = HISTORICAL_BOOKING if a["historical_model"] else ASOF_BOOKING
    with asof_context(ctx, a["value_date"]):
        if kind == "IRS":
            calibrate = _truthy(terms.get("calibrate_curve_spread_to_rate"))
            t = parse_irs_terms(terms)
            deal = _deal(services, conn, acct, t, a["trade_date"], as_of=a, calibrate=calibrate)
            out = _book_irs(ctx, services, acct, t, deal, as_of=a)
        else:
            out = _delegate(services, kind, "book_as_of")(ctx, dict(payload, terms=terms), a)
        cid = out["contract_id"]
        conn.execute("UPDATE otc_contracts SET booking=?, entered_ts=?, import_key=?, trade_date=?, value_date=?, confirm_status=?, confirmed_ts=? WHERE contract_id=?",
                     (booking, ctx.ts, a["line_key"], a["trade_date"].isoformat(), a["value_date"].isoformat(), CONFIRMED, ctx.ts, cid))
        row = _open_contract(conn, cid)
        day0 = _vm_core(ctx, services, row, a["trade_date"].isoformat(), strict=True)        # IM and day-0 VM on the value date
    row = _open_contract(conn, cid)
    diff = asof_terms_mismatch(kind, terms, row, a)
    if diff:
        raise ConflictError("ASOF_TERMS_MISMATCH", f"the contract {cid} would differ from the dealt terms in " + ", ".join(f"{x['term']} ({x['payload']} vs {x['stored']})"
                            for x in diff) + "; nothing was written", {"differences": diff, "contract_id": cid})
    ctx.event("OTC_BOOKED_AS_OF", account_id=aid, instrument_id=cid, ref_type="otc", ref_id=cid, kind=kind, booking=booking, trade_date=a["trade_date"].isoformat(),
              value_date=a["value_date"].isoformat(), exec_ts=a["exec_ts"], entered_ts=ctx.ts, batch_id=a["batch_id"], line_key=a["line_key"],
              day0_vm={k: day0.get(k) for k in ("status", "code", "mark_c", "delta_c", "entry_id")})
    entries = list(out.get("entries") or []) + ([day0["entry_id"]] if day0.get("entry_id") else [])
    view = contract_view(services, row, conn=conn, detail=True, now_ts=ctx.ts)
    return dict(out, contract=view, contract_id=cid, booking=booking, import_key=a["line_key"], entries=entries,
                day0_vm={k: day0.get(k) for k in ("status", "code", "mark_c", "delta_c", "entry_id", "note")}, confirm_status=CONFIRMED,
                confirm_label=ASOF_CONFIRM_LABEL, source=view.get("source"), source_label=view.get("source_label"))


# ---- FINSIM1 hooks (C10 columns, C11 chart rows, C13) ---------------------------------------------------------------------------------
#: C10: the as-of booking columns of otc_contracts; C15's settlement reference columns (P6 adds the same statements in collateral.bind - the
#: ALTER runs only when the column is missing, so whichever module binds first creates them)
C10_SCHEMA = ["ALTER TABLE otc_contracts ADD COLUMN booking TEXT",
              "ALTER TABLE otc_contracts ADD COLUMN entered_ts INTEGER",
              "ALTER TABLE otc_contracts ADD COLUMN import_key TEXT",
              "ALTER TABLE otc_contracts ADD COLUMN trade_date TEXT",
              "ALTER TABLE otc_contracts ADD COLUMN value_date TEXT",
              "ALTER TABLE otc_contracts ADD COLUMN confirm_status TEXT",
              "ALTER TABLE otc_contracts ADD COLUMN confirmed_ts INTEGER",
              "CREATE INDEX IF NOT EXISTS ix_otc_import_key ON otc_contracts(import_key)",
              "ALTER TABLE settlements ADD COLUMN ref_type TEXT",
              "ALTER TABLE settlements ADD COLUMN ref_id TEXT"]
C10_COLUMNS = ("booking", "entered_ts", "import_key", "trade_date", "value_date", "confirm_status", "confirmed_ts")


def ensure_schema(db) -> dict:
    """C10 / C15: the as-of columns of ``otc_contracts`` and the ``ref_type`` / ``ref_id`` of ``settlements`` (idempotent, additive:
    ``Database.ensure_schema_ext``; inside the caller's transaction when this thread holds the writer in one)."""
    if db is None or not hasattr(db, "ensure_schema_ext"):
        return {}
    return db.ensure_schema_ext("swaps", C10_SCHEMA + RFQ_SCHEMA + CLIENT_REF_SCHEMA)


def has_columns(conn: sqlite3.Connection, table: str, *cols: str) -> bool:
    have = {r[1] for r in conn.execute(f'PRAGMA table_info("{table}")').fetchall()}
    return all(c in have for c in cols)


def bind(rt) -> None:
    """C10 / C11 (FINSIM1_PLAN P4): the chart rows of every GL the ledger defines (4451 / 4452 / 4453 / 1335 / 2345 reach a database an
    older release created; ``Database.open`` syncs them too) and the as-of columns.  Exception-free by design: a failure is logged and
    the module stays usable (its live paths do not need the new columns)."""
    from .. import ledger as _ledger
    db = getattr(rt, "db", None)
    log = getattr(rt, "log", None)
    if db is None:
        return
    try:
        with db.writer() as tx:
            _ledger.ensure_chart_rows(tx.conn)
    except Exception as e:   # noqa: BLE001 - logged; Database.open's own chart sync covers the codes as well
        if log:
            log.warning("swaps.bind: chart rows not synced: %s", e)
    try:
        ensure_schema(db)
    except Exception as e:   # noqa: BLE001 - logged; otc.book_as_of adds the columns itself before it writes them
        if log:
            log.warning("swaps.bind: the as-of columns could not be added: %s", e)
    try:
        add_jobs()
    except Exception as e:   # noqa: BLE001 - logged; the RFQ desk still requests, the quotes wait for the job
        if log:
            log.warning("swaps.bind: the RFQ_DEALER / OTC_CONFIRM jobs were not added: %s", e)


MARK_INTRADAY = "model, intraday"
MARK_LAST_VM = "as of the last VM"
MARK_AT_BOOKING = "as of the booking (no variation margin yet)"


def _last_vm(conn: sqlite3.Connection, cid: str) -> Optional[sqlite3.Row]:
    return conn.execute("SELECT vm_date, mark_c FROM otc_vm WHERE contract_id=? ORDER BY vm_date DESC LIMIT 1", (cid,)).fetchone()


def _cost_c(conn: sqlite3.Connection, row: dict) -> int:
    """C9c: the contract's cost basis = GL 1335 (upfront / premium paid) - GL 2345 (received), natural cents in its currency."""
    aid, ccy, cid = row["account_id"], row["currency"], row["contract_id"]
    return balance(conn, aid, "1335", ccy, cid) - balance(conn, aid, "2345", ccy, cid)


def _display_fields(services, row: dict, terms: dict, inputs: dict, conn: sqlite3.Connection) -> dict:
    """PORTFOLIO-06: what a position row and the drawer show per kind - ``units`` / ``unit_label``, ``entry`` (the dealt price, spread or
    rate), ``current`` (the underlying price, the spread or the par rate now; None when not observed), ``notional_c`` and ``extra``."""
    kind = row["kind"]
    n = int(row["notional_c"])
    out = {"units": None, "unit_label": None, "entry": None, "current": None, "notional_c": n, "extra": {}}
    if kind == "TRS":
        uid = terms.get("underlying_id")
        units = int(terms.get("units") or 0)
        cur = inputs.get("S") or (inputs.get("underlying") or {}).get("price")
        out.update(units=units, unit_label=f"{uid} reference shares", entry=row.get("reset_px"), current=None if cur is None else str(cur),
                   extra={"underlying_id": uid, "reset_px": row.get("reset_px"), "underlying_px": None if cur is None else str(cur), "side": "receive" if terms.get("receiver") else "pay",
                          "entry_label": "reset price", "current_label": f"{uid} price"})
    elif kind in CDS_KINDS:
        up = terms.get("upfront_c")
        out.update(units=n // 100, unit_label="USD notional", entry=None if terms.get("deal_spread_bp") is None else f"{terms.get('deal_spread_bp')} bp",
                   current=None if inputs.get("mid_spread_bp") is None else f"{inputs.get('mid_spread_bp')} bp",
                   extra={"index": terms.get("index_name"), "series": terms.get("series"), "side": terms.get("side"), "coupon_bp": terms.get("coupon_bp"),
                          "upfront_c": up, "upfront": None if up is None else ("received" if int(up) < 0 else "paid"), "entry_label": "dealt spread",
                          "current_label": "spread now (proxy + basis)", "proxy_as_of": (inputs.get("proxy") or {}).get("as_of")})
    elif kind == "IRS":
        spread = curve_spread(terms)
        par = inputs.get("par_rate")
        fair = None if par is None else str((Decimal(par) + spread).quantize(RATE_Q))
        dv01 = None
        if inputs.get("annuity") not in (None, ""):
            try:
                dv01 = dv01_from(n, Decimal(str(inputs["annuity"])), bool(terms.get("pay_fixed")))
            except (InvalidOperation, ValueError):
                dv01 = None
        out.update(units=n // 100, unit_label="USD notional", entry=terms.get("fixed_rate"), current=fair,
                   extra={"side": "pay fixed" if terms.get("pay_fixed") else "receive fixed", "fixed_rate": terms.get("fixed_rate"), "par_rate": par, "fair_rate": fair,
                          "dv01_c": dv01, "entry_label": "fixed rate", "current_label": "par now" + (" with the contract curve spread" if spread else ""),
                          **_spread_fields(spread)})
    elif kind == "FX_FORWARD":
        # OTC-18 (FINSIM1_PLAN 15.15b, the 1.0.6 fixer): a forward reads as what it delivers - buy EUR 1,000,000.00 / sell USD 1,120,032.00, value
        # 2027-01-29 - with the dealt rate against the forward now, never as a USD notional to "receive" or "pay"
        pair = str(terms.get("pair") or "")
        base, quote = pair[:3], pair[3:]
        buy = str(terms.get("side") or "").upper() == "BUY"
        bc, bn, sc, sn = terms.get("buy_ccy"), int(terms.get("buy_c") or 0), terms.get("sell_ccy"), int(terms.get("sell_c") or 0)
        legs = f"buy {bc} {fmt_money(bn)} / sell {sc} {fmt_money(sn)}, value {row.get('maturity_date')}"
        cur = inputs.get("F")
        out.update(units=int(terms.get("notional_c") or 0) // 100, unit_label=f"{base} {'bought' if buy else 'sold'} forward vs {quote}", entry=terms.get("strike"),
                   current=None if cur in (None, "") else str(cur),
                   extra={"side": f"buy {base} / sell {quote}" if buy else f"sell {base} / buy {quote}", "legs": legs, "buy": [bc, bn], "sell": [sc, sn],
                          "value_date": row.get("maturity_date"), "pair": pair, "entry_label": "dealt rate", "current_label": "forward now (mid)"})
    elif kind == "EQ_FORWARD":
        uid = terms.get("underlying_id")
        buy = str(terms.get("side") or "").upper() == "BUY"
        cur = inputs.get("F")
        out.update(units=int(terms.get("units") or 0), unit_label=f"{uid} shares forward", entry=terms.get("strike"), current=None if cur in (None, "") else str(cur),
                   extra={"side": f"{'long' if buy else 'short'} {uid} forward", "maturity_date": row.get("maturity_date"), "entry_label": "dealt forward",
                          "current_label": "forward now (mid)", "underlying_id": uid})
    elif kind in ("EQ_OPTION", "FX_OPTION"):
        buy = str(terms.get("side") or "").upper() == "BUY"
        what = "call" if terms.get("right") == "C" else "put"
        under = terms.get("underlying_id") or terms.get("pair")
        units = int(terms.get("units") or 0) if kind == "EQ_OPTION" else int(terms.get("notional_c") or 0) // 100
        cur = inputs.get("price_per_unit")
        out.update(units=units, unit_label=(f"{under} shares, {what} {terms.get('strike')}" if kind == "EQ_OPTION" else f"{str(under)[:3]} notional, {what} {terms.get('strike')}"),
                   entry=terms.get("premium_per_unit"), current=None if cur in (None, "") else str(cur),
                   extra={"side": f"{'bought' if buy else 'sold'} {what}", "expiry": row.get("maturity_date"), "entry_label": "premium per unit",
                          "current_label": "model price per unit now"})
    elif kind == "XCCY" and str(terms.get("variant") or "").upper() == "FLOAT_FLOAT":
        # FX_CCS_PLAN 1.0.6.1: the float / float swap reads as its legs - pay EUR ESTR + 0.1 bp / receive USD SOFR flat - with both notionals
        f = str(terms.get("foreign_ccy") or "EUR")
        legs = terms.get("legs") or {}
        sp = (terms.get("spread") or {}).get("dealt_bp")
        f_side = "pay" if (legs.get("pay") or {}).get("currency") == f else "receive"
        f_n = int((legs.get(f_side) or {}).get("notional_c") or 0)
        out.update(units=n // 100, unit_label=f"USD notional (vs {f} {fmt_money(f_n)})", entry=None if sp is None else f"{sp} bp",
                   current=None, extra={"side": f"{'pay' if f_side == 'pay' else 'receive'} {f} {(legs.get(f_side) or {}).get('index') or ''} spread {sp} bp",
                                        "deal_rate": terms.get("deal_rate"), "foreign_notional_c": f_n, "usd_notional_c": n, "variant": "FLOAT_FLOAT",
                                        "entry_label": "dealt spread", "current_label": "clean mark (model)",
                                        "clean_mark_c": inputs.get("clean_mark_c"), "csa_exposure_c": inputs.get("csa_exposure_c")})
    else:                                     # XCCY: the notional; its legs are in the contract's own detail
        out.update(units=n // 100, unit_label=f"{row.get('currency') or 'USD'} notional")
    return out


def _epoch_on(ds, hhmm: str) -> int:
    try:
        return epoch_of(date.fromisoformat(str(ds)[:10]), hhmm)
    except ValueError:
        return 0


def _input_stamp(inputs: Optional[dict]) -> int:
    """The time of the latest observed input behind a mark (epoch): an underlying / FX print or the close of its date (16:00), the curve
    of a date (18:00), the CDX proxy of a date (17:00).  0 when the inputs carry no time."""
    inputs = inputs or {}
    best = 0
    for k in ("underlying", "fx"):
        part = inputs.get(k) or {}
        if not isinstance(part, dict):
            continue
        if part.get("close_date"):
            best = max(best, _epoch_on(part["close_date"], "16:00"))
        for t in (part.get("quote_time"), part.get("as_of")):
            if isinstance(t, (int, float)) and not isinstance(t, bool) and t > 0:
                best = max(best, int(t))
    if inputs.get("curve_as_of"):
        best = max(best, _epoch_on(inputs["curve_as_of"], "18:00"))
    for leg in (inputs.get("legs") or {}).values():
        if isinstance(leg, dict) and leg.get("curve_as_of"):
            best = max(best, _epoch_on(leg["curve_as_of"], "18:00"))
    px = inputs.get("proxy") or {}
    if isinstance(px, dict) and px.get("as_of"):
        best = max(best, _epoch_on(px["as_of"], "17:00"))
    return best


def _reference_stamp(conn: sqlite3.Connection, row: dict) -> int:
    """When the mark NAV carries now was made: the inputs of the last variation margin (``otc_contracts.inputs_json`` - each VM writes
    its inputs there; the booking's before the first VM) - the time it was computed and the time of its latest input - or the close of a
    later TRS reset.  A live mark is "intraday" only when an input it uses was observed after that."""
    try:
        ref = json.loads(row.get("inputs_json") or "{}")
    except ValueError:
        ref = {}
    stamp = _input_stamp(ref)
    vd = ref.get("valuation_date") if isinstance(ref, dict) else None
    made = ref.get("as_of") if isinstance(ref, dict) else None
    if vd:                                   # a mark valued as of a date (a VM, an as-of booking): the close of that date, whenever it was computed
        stamp = max(stamp, _epoch_on(vd, VM_ET))
    elif isinstance(made, (int, float)) and not isinstance(made, bool):
        stamp = max(stamp, int(made))         # a live mark (a booking): the time it was made
    r = conn.execute("SELECT event_date, detail_json FROM otc_resets WHERE contract_id=? AND kind='RESET' ORDER BY event_date DESC LIMIT 1",
                     (row["contract_id"],)).fetchone()
    if r is not None:
        d = _detail(r)
        stamp = max(stamp, _epoch_on(r["event_date"], "16:00"), _input_stamp({"underlying": d.get("close") or {}}))
    return stamp


def _position_fields(services, row: dict, got, conn: sqlite3.Connection, ts: int) -> Optional[dict]:
    """C13 from a mark result already computed (``mark``): the live model mark when it could be computed from usable inputs observed after
    those of the mark NAV carried at the last variation margin (``model, intraday``: an STM contract nets the value already settled),
    else that mark (``as of the last VM``; stale when the model cannot run now)."""
    if row.get("status") != "OPEN":
        return None
    cid = row["contract_id"]
    terms = _terms(row)
    overlay = int(row.get("mark_c") or 0)
    last = _last_vm(conn, cid)
    live = isinstance(got, tuple) and got[0] is not None
    inputs = dict(got[1] or {}) if live else (dict(getattr(got, "inputs", None) or {}) if got is not None else {})
    if live and _input_stamp(inputs) > _reference_stamp(conn, row):
        settled = _reference_mark_c(conn, cid) if str(row.get("margin_mode") or "").upper() == "STM" else 0
        mv = int(got[0]) - settled
        label, quality, as_of = MARK_INTRADAY, "computed", inputs.get("as_of") or ts
    else:
        mv = overlay
        label = MARK_LAST_VM if last is not None else MARK_AT_BOOKING
        quality, as_of = ("computed" if live else "stale"), row.get("mark_ts")
    cost = _cost_c(conn, row)
    out = {"mv_c": mv, "unrealised_c": mv - cost, "mark_label": label, "quality": quality, "as_of": as_of, "overlay_c": overlay,
           "move_since_vm_c": mv - overlay, "last_vm_date": None if last is None else last["vm_date"], "cost_c": cost}
    out.update(_display_fields(services, row, terms, inputs, conn))
    return out


def position_fields(services, row, snapshot=None, now_ts: Optional[int] = None) -> Optional[dict]:
    """C13 (PORTFOLIO-02 / -06): ``{mv_c, unrealised_c, mark_label, quality, as_of, units, unit_label, entry, current, notional_c, extra}`` of
    an OPEN OTC contract for the position rows, NAV and Risk (through accounting).  The MV is the live model mark when it can be computed
    from usable inputs now - labelled "model, intraday", so the move since the last variation margin shows in the day's P&L and in
    unrealised - else the mark NAV carried at the last VM ("as of the last VM", stale).  unrealised = MV - the contract cost (1335 -
    2345).  None for a closed contract or an unknown row (accounting then keeps its overlay)."""
    if row is None:
        return None
    rowd = {k: row[k] for k in row.keys()} if isinstance(row, sqlite3.Row) else dict(row)
    if "terms_json" not in rowd:
        rowd = _contract_dict(services, rowd) or {}
    if not rowd or rowd.get("status") != "OPEN":
        return None
    ts = int(now_ts if now_ts is not None else services.clock.epoch())
    got = mark(services, rowd, ts, snapshot=snapshot)
    with _marks.shared_reader(services.db) as conn:
        return _position_fields(services, rowd, got, conn, ts)


# ---- RFQ and the simulated confirmation (FINSIM1 C14, OMS-10; P4 task 8) ------------------------------------------------------------------
#: C14: the quote requests (additive, D2: their own table; the statuses are a column, never a widened CHECK)
RFQ_SCHEMA = ["CREATE TABLE IF NOT EXISTS rfqs (rfq_id TEXT PRIMARY KEY, command_id INTEGER, account_id TEXT NOT NULL, broker TEXT, kind TEXT NOT NULL,"
              " terms_json TEXT NOT NULL, status TEXT NOT NULL, requested_ts INTEGER NOT NULL, quoted_ts INTEGER, quote_json TEXT, quote_label TEXT,"
              " ttl_s INTEGER, expires_ts INTEGER, closed_ts INTEGER, contract_id TEXT, accept_command_id INTEGER, reason TEXT, updated_ts INTEGER)",
              "CREATE INDEX IF NOT EXISTS ix_rfqs_status ON rfqs(status)",
              "CREATE INDEX IF NOT EXISTS ix_rfqs_account ON rfqs(account_id)",
              "CREATE INDEX IF NOT EXISTS ix_rfqs_contract ON rfqs(contract_id)"]
RFQ_JOB, CONFIRM_JOB = "RFQ_DEALER", "OTC_CONFIRM"
RFQ_TTL_S = 120                        # a quote is good for 120 s; the dealer job expires it on its first tick after that
RFQ_OPEN = ("REQUESTED", "QUOTED")
RFQ_STATUSES = ("REQUESTED", "QUOTED", "ACCEPTED", "DECLINED", "EXPIRED")
RFQ_WORDS = {"REQUESTED": "awaiting the dealer's quote", "QUOTED": "quoted - awaiting your acceptance", "ACCEPTED": "accepted and booked",
             "DECLINED": "declined", "EXPIRED": "expired"}
QUOTE_LABEL = "indicative model quote, simulated dealer"
FIRM_KINDS = ("IRS", "TRS", "CDX")     # an accepted quote books at its quoted price; the other kinds re-price and must match it (QUOTE_MOVED)
PENDING_CONFIRM = "PENDING_CONFIRM"
CONFIRM_ET = "17:05"
SIM_CONFIRM_LABEL = "CONFIRMED - simulated confirmation"
PENDING_CONFIRM_LABEL = "PENDING CONFIRMATION - the simulated confirmation runs at 17:05 ET on the trade's business day"
NO_CONFIRM_LABEL = "no confirmation record: booked before confirmations were simulated"
#: V104R2-OTC-06: disclosed (the model is unchanged - the owner decides): the Treasury par curve stands in for the SOFR swap curve
IRS_CURVE_NOTE = ("model par = the Treasury par curve (treasury.gov) used as a proxy for the SOFR swap curve: no SOFR swap rate is sourced, and a real"
                  " dealer quotes off the SOFR swap curve, which usually sits below Treasury par")


def confirm_label(booking, status) -> Optional[str]:
    """The confirmation of a contract in trader words: an as-of booking's CONFIRMED (the owner's instruction), the simulated 17:05 ET
    confirmation of a live contract, or PENDING until then; None for a contract booked before confirmations were simulated."""
    if status == CONFIRMED:
        return ASOF_CONFIRM_LABEL if booking in (ASOF_BOOKING, HISTORICAL_BOOKING) else SIM_CONFIRM_LABEL
    if status == PENDING_CONFIRM:
        return PENDING_CONFIRM_LABEL
    return None


def mark_new_contract(ctx: TxContext, cid: Optional[str]) -> bool:
    """C14: a contract booked now (``otc.book``, an accepted quote) records its entry time, trade and value dates and starts PENDING_CONFIRM;
    the OTC_CONFIRM job confirms it at 17:05 ET of its business day.  An as-of contract keeps its own record (CONFIRMED at the owner's
    instruction).  False when nothing changed (the columns are absent, the contract already carries a status)."""
    conn = ctx.conn
    if not cid or not has_columns(conn, "otc_contracts", *C10_COLUMNS):
        return False
    row = _contract_row(conn, cid)
    if row is None or row["confirm_status"] is not None:
        return False
    terms = _terms({k: row[k] for k in row.keys()})
    conn.execute("UPDATE otc_contracts SET entered_ts=?, trade_date=?, value_date=?, confirm_status=? WHERE contract_id=?",
                 (ctx.ts, terms.get("trade_date") or ny_of(ctx.ts).date().isoformat(), row["start_date"], PENDING_CONFIRM, cid))
    return True


def confirm_date(entered_ts, cal=None) -> Optional[date]:
    """The business day whose 17:05 ET runs the simulated confirmation of a contract entered at ``entered_ts``: its accounting day (the 17:00
    ET cut-off), rolled to a business day of the calendar."""
    if entered_ts in (None, ""):
        return None
    d = accounting_day(ny_of(int(entered_ts)))
    if cal is not None and hasattr(cal, "roll_following"):
        d = cal.roll_following(d)
    return d


def confirm_keys(conn: sqlite3.Connection, now_ts: int, cal=None) -> List[Tuple[str, str]]:
    """OTC_CONFIRM: ``(contract_id, confirmation date)`` of every contract PENDING_CONFIRM whose 17:05 ET has passed.  Quiet when idle (no
    pending contract, no key).  Only contracts this release booked are ever pending: it never confirms a date before it was installed
    (15.9 item 8: new jobs start forward), and a contract booked before (no confirmation record) is never touched."""
    if not has_columns(conn, "otc_contracts", "confirm_status", "entered_ts"):
        return []
    now = int(now_ts)
    out: List[Tuple[str, str]] = []
    for r in conn.execute("SELECT contract_id, entered_ts FROM otc_contracts WHERE confirm_status=? ORDER BY contract_id", (PENDING_CONFIRM,)).fetchall():
        d = confirm_date(r["entered_ts"], cal)
        if d is not None and now >= epoch_of(d, CONFIRM_ET):
            out.append((r["contract_id"], d.isoformat()))
    return out


def h_confirm(ctx: TxContext, payload: dict) -> dict:
    """``otc.confirm {contract_id, date}`` (OTC_CONFIRM, 17:05 ET): the simulated confirmation of a contract booked live - PENDING_CONFIRM ->
    CONFIRMED, labelled "simulated confirmation" (there is no electronic confirmation platform in the simulator).  Idempotent; nothing posts."""
    cid, ds = _key(payload, "otc.confirm")
    key = f"{cid}:{ds}"
    row = _open_contract(ctx.conn, cid)
    if row.get("confirm_status") != PENDING_CONFIRM:
        return _done_noop(ctx, CONFIRM_JOB, key, "NOT_PENDING", f"{cid} is {row.get('confirm_status') or 'not awaiting a confirmation'}")
    if ctx.ts < epoch_of(date.fromisoformat(ds), CONFIRM_ET):
        return _skip(ctx, CONFIRM_JOB, key, "CONFIRM_NOT_YET", f"{cid}: the simulated confirmation runs from {CONFIRM_ET} ET of {ds}", row["account_id"], cid,
                     quiet=True)
    ctx.conn.execute("UPDATE otc_contracts SET confirm_status=?, confirmed_ts=? WHERE contract_id=?", (CONFIRMED, ctx.ts, cid))
    ctx.event("OTC_CONFIRMED", account_id=row["account_id"], instrument_id=cid, ref_type="otc", ref_id=cid, date=ds, broker=row.get("broker"), label=SIM_CONFIRM_LABEL)
    _job_done(ctx, CONFIRM_JOB, key, "DONE", f"confirmed ({SIM_CONFIRM_LABEL})")
    return {"status": "DONE", "key": key, "posted": False, "contract_id": cid, "confirm_status": CONFIRMED, "note": SIM_CONFIRM_LABEL}


def _has_table(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def _jloads(s, default=None):
    if s in (None, ""):
        return default
    try:
        return json.loads(s)
    except (ValueError, TypeError):
        return default


def _rfq_row(conn: sqlite3.Connection, rid: str) -> Optional[dict]:
    if not rid or not _has_table(conn, "rfqs"):
        return None
    r = conn.execute("SELECT * FROM rfqs WHERE rfq_id=?", (rid,)).fetchone()
    return None if r is None else {k: r[k] for k in r.keys()}


def _hms(ts) -> str:
    return ny_of(int(ts)).strftime("%H:%M:%S ET") if ts not in (None, "") else "an unrecorded time"


def _notional_of(terms: dict) -> Optional[int]:
    raw_c, raw = terms.get("notional_c"), terms.get("notional")
    try:
        if raw_c not in (None, "") and not isinstance(raw_c, bool):
            return int(Decimal(str(raw_c)))
        if raw not in (None, ""):
            return to_c(str(raw).replace(",", ""))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return None


def rfq_describe(kind: str, terms: dict, pv: Optional[dict] = None) -> Tuple[str, str, Optional[int], Optional[int]]:
    """``(instrument words, side words, units, notional cents)`` of a quote request (its terms, else the quote's preview)."""
    k = str(kind or "").upper()
    t, pv = dict(terms or {}), dict(pv or {})
    if k == "IRS":
        m = t.get("tenor_months")
        try:
            m = int(m)
            tenor = f" {m // 12}Y" if m % 12 == 0 else f" {m}M"
        except (TypeError, ValueError):
            tenor = ""
        pay = _truthy(t.get("pay_fixed")) if "pay_fixed" in t else str(t.get("side") or "").upper().startswith("PAY")
        return f"USD SOFR interest-rate swap{tenor}", "PAY FIXED" if pay else "RECEIVE FIXED", None, _notional_of(t) or pv.get("notional_c")
    if k == "TRS":
        uid = str(t.get("underlying_id") or pv.get("underlying_id") or "?").upper()
        rec = _truthy(t.get("receiver")) if "receiver" in t else str(t.get("side") or "").upper().startswith("RECEIVE")
        try:
            units = int(str(t.get("units")).replace(",", ""))
        except (TypeError, ValueError):
            units = None
        return f"{uid} total return swap", "RECEIVE TOTAL RETURN" if rec else "PAY TOTAL RETURN", units, pv.get("notional_c")
    if k == "CDX":
        idx = str(t.get("index") or pv.get("index") or "").upper().replace("CDX.NA.", "").strip(" .")
        name = {"IG": "CDX.NA.IG", "HY": "CDX.NA.HY"}.get(idx, idx or "CDX")
        series = pv.get("series") or t.get("series")
        side = str(t.get("side") or pv.get("side") or "BUY_PROTECTION").upper().replace(" ", "_")
        side = {"BUY": "BUY_PROTECTION", "SELL": "SELL_PROTECTION"}.get(side, side)
        return f"{name}{' S' + str(series) if series else ''} 5Y", side.replace("_", " "), None, _notional_of(t) or pv.get("notional_c")
    if k == "XCCY":
        r, p = str(t.get("recv_ccy") or "?").upper(), str(t.get("pay_ccy") or "?").upper()
        # P8B-12: named by the market pair (EUR/USD, USD/JPY), whichever way round the legs go; the side says who pays what
        foreign = r if r != "USD" else p
        try:
            from ..instruments import fx_pair_id as _pair_id
            pair = _pair_id(foreign) if foreign not in ("?", "USD") else f"{r}{p}"
        except Exception:   # noqa: BLE001 - an unknown code: the legs as typed
            pair = f"{r}{p}"
        floating = str(t.get("pay_index") or "").strip().upper() not in ("", "FIXED", "NONE") and str(t.get("recv_index") or "").strip().upper() not in (
            "", "FIXED", "NONE")
        what = "float / float cross-currency swap" if floating or str(pv.get("variant") or "").upper() == "FLOAT_FLOAT" else "cross-currency swap"
        return f"{pair[:3]}/{pair[3:]} {what}", f"RECEIVE {r} / PAY {p}", None, pv.get("usd_notional_c")
    what = "option" if k in OTC_OPTION_KINDS[:2] else "forward"
    under = str(t.get("underlying_id") or t.get("pair") or pv.get("underlying_id") or pv.get("pair") or "").upper()
    side = str(t.get("side") or "").upper().replace("_", " ")
    right = {"C": " CALL", "P": " PUT", "CALL": " CALL", "PUT": " PUT"}.get(str(t.get("right") or "").upper(), "") if what == "option" else ""
    try:
        units = int(str(t.get("units")).replace(",", "")) if t.get("units") not in (None, "") else None
    except (TypeError, ValueError):
        units = None
    return f"{under} OTC {what}".strip(), f"{side or 'TRADE'}{right}", units, pv.get("notional_c")


def preview_blocker(pv: Optional[dict]) -> Optional[dict]:
    """The first reason a priced deal cannot be booked: a blocker the kind's preview raised (a stale vol, a deal that cannot be funded), else
    a funding line that is not fundable; None when the deal is bookable."""
    pv = pv or {}
    for b in pv.get("blockers") or []:
        if isinstance(b, dict) and b.get("code"):
            return b
    for k in ("funding", "im_funding"):
        f = pv.get(k) or {}
        if isinstance(f, dict) and f.get("ok") is False:
            return {"code": "INSUFFICIENT_FUNDS", "message": f.get("note") or "the deal cannot be funded", "details": {"funding": f}}
    return None


def _quote_terms(kind: str, pv: dict) -> Tuple[Optional[dict], dict, str]:
    """``(firm, compare, words)`` of the simulated dealer's quote, from the kind's preview at quote time: ``firm`` = the price an acceptance
    books at (IRS rate, TRS reset price, CDX spread), ``compare`` = what the booking must reproduce, ``words`` = the quote in trader words."""
    k = str(kind or "").upper()
    if k == "IRS":
        rate = pv.get("dealt_rate")
        sign = "+" if pv.get("pay_fixed") else "-"
        words = (f"{'pay' if pv.get('pay_fixed') else 'receive'} fixed {pv.get('dealt_rate_pct')} (model par {pv.get('par_rate_pct')} {sign}"
                 f" {pv.get('half_width_bp')} bp half-width)")
        return {"fixed_rate": rate}, {"fixed_rate": rate}, words
    if k == "TRS":
        px = pv.get("reset_px")
        fin = pv.get("financing") or {}
        units = pv.get("units")
        words = (f"reset price {px} on {units:,} {pv.get('underlying_id')} ({pv.get('reset_px_source')}); financing SOFR"
                 f" {'+' if pv.get('receiver') else '-'} {fin.get('spread_pct') or pv.get('spread_pct')}") if isinstance(units, int) else f"reset price {px}"
        return {"reset_px": px, "reset_px_source": pv.get("reset_px_source"), "quote": pv.get("quote")}, {"reset_px": px}, words
    if k == "CDX":
        bp = pv.get("deal_spread_bp")
        words = f"{bp} bp (mid {pv.get('mid_spread_bp')} bp); {pv.get('upfront_direction') or ''} upfront".replace(";  upfront", "")
        return {"dealt_spread_bp": bp}, {"deal_spread_bp": bp}, words
    # OTC-23 (FINSIM1_PLAN 15.15b, the 1.0.6 fixer): the re-priced kinds spell the deal out - what you buy and sell, both amounts, the dates
    if k == "XCCY" and str(pv.get("variant") or "").upper() == "FLOAT_FLOAT":
        # FX_CCS_PLAN P-C task 1 (1.0.6.1): the float / float quote - re-priced at the acceptance on the pinned EURUSD print and compared on the
        # deal rate and both legs' spreads; the words name the deal FX, both legs, both notionals and the dates
        f = str(pv.get("foreign_ccy") or "EUR")
        legs = pv.get("legs") or {}
        idx = {s: (legs.get(s) or {}).get("index") or "" for s in ("receive", "pay")}
        spread_words = {}
        for s in ("receive", "pay"):
            sp = (legs.get(s) or {}).get("spread_bp")
            try:
                v = Decimal(str(sp or "0"))
            except (InvalidOperation, ValueError):
                v = Decimal(0)
            spread_words[s] = "flat" if v == 0 else f"{'+' if v > 0 else '-'} {abs(v).normalize():f} bp"
        rl, pl = legs.get("receive") or {}, legs.get("pay") or {}
        cmp = {"deal_rate": pv.get("deal_rate"), "pay_spread_bp": pv.get("pay_spread_bp"), "recv_spread_bp": pv.get("recv_spread_bp")}
        words = (f"deal FX {pv.get('deal_rate')}; pay {pl.get('currency')} {idx['pay']} {spread_words['pay']} / receive {rl.get('currency')} {idx['receive']}"
                 f" {spread_words['receive']}; {f} {_money_words(pv.get('foreign_notional_c'))} / USD {_money_words(pv.get('usd_notional_c'))};"
                 f" {pv.get('start_date')} to {pv.get('maturity_date')}")
        return None, cmp, words
    if k == "XCCY":
        legs = pv.get("legs") or {}
        rl, pl = legs.get("receive") or {}, legs.get("pay") or {}
        cmp = {"deal_rate": pv.get("deal_rate"), "recv_rate": rl.get("rate"), "pay_rate": pl.get("rate")}
        words = (f"you pay {pl.get('currency')} {_money_words(pl.get('notional_c'))} at {pl.get('rate_pct')} (borrowed) / receive {rl.get('currency')}"
                 f" {_money_words(rl.get('notional_c'))} at {rl.get('rate_pct')} (lent); exchanged at {pv.get('deal_rate')} on {pv.get('start_date')}"
                 f" and back on {pv.get('maturity_date')}")
        return None, cmp, words
    if k in OTC_OPTION_KINDS[:2]:
        return None, {"premium_per_unit": pv.get("premium_per_unit")}, (f"{pv.get('side_text') or ''}: premium {pv.get('premium_per_unit')} per unit"
                                                                        f" ({pv.get('deal_sigma_pct')} vol)").lstrip(": ")
    if k == "FX_FORWARD" and isinstance(pv.get("delivery"), dict):
        rc, pc = pv["delivery"].get("receive") or {}, pv["delivery"].get("pay") or {}
        words = (f"you buy {rc.get('ccy')} {_money_words(rc.get('amount_c'))} / sell {pc.get('ccy')} {_money_words(pc.get('amount_c'))} for value"
                 f" {pv.get('value_date') or pv.get('expiry')} at {pv.get('strike')} (model forward {pv.get('forward')})")
        return None, {"strike": pv.get("strike")}, words
    if k == "EQ_FORWARD":
        words = (f"you {'buy' if str(pv.get('side') or '').upper() == 'BUY' else 'sell'} {int(pv.get('units') or 0):,} {pv.get('underlying_id')} forward for"
                 f" {pv.get('expiry')} at {pv.get('strike')} (mid forward {pv.get('forward')})")
        return None, {"strike": pv.get("strike")}, words
    return None, {"strike": pv.get("strike")}, f"forward {pv.get('strike')} (model forward {pv.get('forward')})"


def _money_words(c) -> str:
    """Integer cents as an amount for a sentence ('1,000,000.00'); 'n/a' when unknown."""
    try:
        return fmt_money(int(c))
    except (TypeError, ValueError):
        return "n/a"


def rfq_view(row, now: int, full: bool = False) -> dict:
    """One quote request for the desk, the Pending blotter (C7 rows) and the Derivatives page: status in words, the stage, the quote in words
    with its validity, the actions open to it; ``full`` adds the quote's priced detail (``quote_preview``, what the ticket renders)."""
    r = {k: row[k] for k in row.keys()} if isinstance(row, sqlite3.Row) else dict(row)
    terms = _jloads(r.get("terms_json"), {}) or {}
    quote = _jloads(r.get("quote_json"), {}) or {}
    pv = quote.get("preview") or {}
    kind = str(r.get("kind") or "")
    label, side, units, notional_c = rfq_describe(kind, terms, pv)
    status = str(r.get("status") or "")
    exp = r.get("expires_ts")
    live = status == "QUOTED" and exp is not None and int(now) < int(exp)
    stage = {"REQUESTED": "AWAITING_DEALER_QUOTE", "QUOTED": "AWAITING_ACCEPTANCE" if live else "QUOTE_LAPSED"}.get(status, status)
    dealer = config.PRIME_BROKERS.get(str(r.get("broker") or "").upper(), str(r.get("broker") or "the dealer"))
    words = RFQ_WORDS.get(status, status.lower())
    if status == "QUOTED" and not live:
        words = "quote lapsed - the dealer job expires it on its next tick"
    out = {"rfq_id": r["rfq_id"], "account_id": r["account_id"], "broker": r.get("broker"), "dealer": dealer, "kind": kind,
           "kind_label": KIND_LABELS.get(kind, kind), "terms": terms, "status": status, "status_words": words, "stage": stage,
           "requested_ts": r.get("requested_ts"), "quoted_ts": r.get("quoted_ts"), "expires_ts": exp, "ttl_s": r.get("ttl_s"),
           "valid_s": max(0, int(exp) - int(now)) if live else 0, "closed_ts": r.get("closed_ts"), "contract_id": r.get("contract_id"),
           "reason": r.get("reason"), "label": label, "summary": f"{label} with {dealer}: {side.lower()}", "side": side, "side_words": side,
           "units": units, "notional_c": notional_c, "quote_words": quote.get("words"), "quote_label": r.get("quote_label"),
           "quote": None if not quote else {"words": quote.get("words"), "label": QUOTE_LABEL, "firm": bool(quote.get("firm")), "quoted_ts": quote.get("quoted_ts"),
                                           "expires_ts": quote.get("expires_ts"), "terms": quote.get("firm") or quote.get("compare")},
           "actions": {"accept": live, "decline": status in RFQ_OPEN}}
    if full:
        out["quote_preview"] = pv or None
    return out


def rfq_rows(services, scope: Optional[str] = None, status: str = "open", account_id: Optional[str] = None, limit: int = 200) -> List[dict]:
    """C14 (P2's Pending rows, the Derivatives page, the desk): the quote requests of a scope (``{lid}:ALL``), an account or all of them -
    the open ones (REQUESTED / QUOTED) by default, ``status`` a status or ``all`` - newest first.  [] before the table exists."""
    now = int(services.clock.epoch())
    with _marks.shared_reader(services.db) as conn:
        if not _has_table(conn, "rfqs"):
            return []
        where, params = [], []
        st = str(status or "open").lower()
        if st == "open":
            where.append("status IN ('REQUESTED','QUOTED')")
        elif st != "all":
            where.append("status=?")
            params.append(st.upper())
        if account_id:
            where.append("account_id=?")
            params.append(str(account_id))
        elif scope and str(scope).endswith(":ALL"):
            where.append("account_id LIKE ?")
            params.append(str(scope)[:-4] + ":%")
        elif scope and str(scope) != "ALL":
            where.append("account_id=?")
            params.append(str(scope))
        sql = "SELECT * FROM rfqs" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY requested_ts DESC, rfq_id DESC LIMIT ?"
        rows = conn.execute(sql, params + [max(1, int(limit))]).fetchall()
    return [rfq_view(r, now) for r in rows]


def rfq_detail(services, rid: str) -> Optional[dict]:
    with _marks.shared_reader(services.db) as conn:
        row = _rfq_row(conn, rid)
    return None if row is None else rfq_view(row, int(services.clock.epoch()), full=True)


def _ensure_rfq_table(services, conn: sqlite3.Connection) -> None:
    if not _has_table(conn, "rfqs"):
        ensure_schema(getattr(services, "db", None))
    if not _has_table(conn, "rfqs"):
        raise RejectError("NOT_IN_THIS_BUILD", "quote requests need the rfqs table, which could not be created", {})


def h_rfq_request(ctx: TxContext, payload: dict) -> dict:
    """``otc.rfq.request {account_id, kind, terms}`` (C14): ask the prime broker's simulated dealer for a quote.  The request runs the gates
    of ``otc.book`` and prices the deal once (``preview``: the terms, the inputs and the funding are checked as a booking checks them - the
    same refusals); it is stored REQUESTED and the RFQ_DEALER job answers it on the next lifecycle tick (about 30 s)."""
    services = _services(ctx)
    kind = prime.require_otc_kind(payload.get("kind"))
    aid = str(payload.get("account_id") or "")
    conn = ctx.conn
    acct = _pb_account(conn, aid)
    require_pb_otc(services, aid)
    raw = dict(payload.get("terms") or {})
    if raw.get("calibrate_curve_spread_to_rate") not in (None, False, "", "false", 0):
        raise RejectError("TERMS", "a contract curve spread is calibrated only for an as-of booking; a quote request asks for the dealer's price",
                          {"field": "calibrate_curve_spread_to_rate"})
    _ensure_rfq_table(services, conn)
    ref = client_ref_of(payload)
    if ref is not None:                           # FX_CCS_PLAN D13: refused only while a contract or an open request carries the reference
        ensure_client_ref_column(services, conn)
        dup = contract_by_client_ref(conn, ref)
        if dup is not None:
            raise ConflictError("DUPLICATE_CLIENT_REF", f"the reference {ref} is already booked as {dup['contract_id']} ({str(dup['status']).lower()}); no new quote"
                                " is asked for", {"client_ref": ref, "contract_id": dup["contract_id"], "status": dup["status"]})
        open_q = open_rfq_by_client_ref(conn, ref)
        if open_q is not None:
            raise ConflictError("DUPLICATE_CLIENT_REF", f"the reference {ref} already has an open quote request {open_q['rfq_id']}"
                                f" ({RFQ_WORDS.get(open_q['status'], open_q['status'].lower())}); accept or decline it first",
                                {"client_ref": ref, "rfq_id": open_q["rfq_id"], "status": open_q["status"]})
        raw["client_ref"] = ref                   # the accept passes terms.client_ref into the booking (K5)
    pv = preview(services, aid, kind, raw, conn=conn, now=ctx.ts)
    b = preview_blocker(pv)
    if b is not None:
        raise ConflictError(str(b["code"]), str(b.get("message") or b["code"]), dict(b.get("details") or {}, blockers=pv.get("blockers") or []))
    rid = next_id(conn, "rfq")
    broker = prime.broker_of(acct)
    conn.execute("INSERT INTO rfqs(rfq_id, command_id, account_id, broker, kind, terms_json, status, requested_ts, ttl_s, updated_ts)"
                 " VALUES (?,?,?,?,?,?,'REQUESTED',?,?,?)", (rid, ctx.command_id, aid, broker, kind, dumps(raw), ctx.ts, RFQ_TTL_S, ctx.ts))
    label, side, _u, _n = rfq_describe(kind, raw, pv)
    ctx.event("RFQ_REQUESTED", account_id=aid, ref_type="rfq", ref_id=rid, kind=kind, broker=broker, label=label, side=side, terms=raw)
    return {"rfq": rfq_view(_rfq_row(conn, rid), ctx.ts), "rfq_id": rid, "status": "REQUESTED",
            "note": "the simulated dealer answers on the next lifecycle tick (about 30 s)"}


def h_rfq_decline(ctx: TxContext, payload: dict) -> dict:
    """``otc.rfq.decline {rfq_id}`` (C14): the owner declines an open request or quote; nothing is booked.  409 RFQ_CLOSED when it is no
    longer open."""
    conn = ctx.conn
    rid = str(payload.get("rfq_id") or "")
    row = _rfq_row(conn, rid)
    if row is None:
        raise NotFound("RFQ_UNKNOWN", f"quote request {rid or '(none)'} does not exist", {"rfq_id": rid})
    if row["status"] not in RFQ_OPEN:
        raise ConflictError("RFQ_CLOSED", f"{rid} is {row['status'].lower()}: there is nothing to decline", {"rfq_id": rid, "status": row["status"]})
    conn.execute("UPDATE rfqs SET status='DECLINED', closed_ts=?, reason=?, updated_ts=? WHERE rfq_id=?", (ctx.ts, "declined by you", ctx.ts, rid))
    ctx.event("RFQ_DECLINED", account_id=row["account_id"], ref_type="rfq", ref_id=rid, kind=row["kind"], was=row["status"])
    return {"rfq": rfq_view(_rfq_row(conn, rid), ctx.ts), "rfq_id": rid, "status": "DECLINED"}


def _quote_moved(conn: sqlite3.Connection, kind: str, quote: dict, cid: str, out: dict) -> List[dict]:
    """What an acceptance booked that differs from the accepted quote: ``[{term, quoted, booked}]`` (empty when it matches)."""
    k = str(kind or "").upper()
    firm = quote.get("firm") or {}
    row = _contract_row(conn, cid)
    terms = _terms({kk: row[kk] for kk in row.keys()}) if row is not None else {}
    diffs: List[dict] = []

    def chk(name, want, have):
        if want in (None, ""):
            return
        if not _dec_eq(want, have):
            diffs.append({"term": name, "quoted": str(want), "booked": None if have is None else str(have)})
    if k == "IRS":
        chk("fixed_rate", firm.get("fixed_rate"), terms.get("fixed_rate"))
    elif k == "TRS":
        chk("reset_px", firm.get("reset_px"), row["reset_px"] if row is not None else None)
    elif k == "CDX":
        chk("deal_spread_bp", firm.get("dealt_spread_bp"), terms.get("deal_spread_bp"))
    else:
        pv = out.get("preview") or {}
        if k == "XCCY":
            legs = pv.get("legs") or {}
            got = {"deal_rate": pv.get("deal_rate"), "recv_rate": (legs.get("receive") or {}).get("rate"), "pay_rate": (legs.get("pay") or {}).get("rate"),
                   "pay_spread_bp": pv.get("pay_spread_bp"), "recv_spread_bp": pv.get("recv_spread_bp")}     # the float / float spreads (1.0.6.1)
        elif k in OTC_OPTION_KINDS[:2]:
            got = {"premium_per_unit": pv.get("premium_per_unit")}
        else:
            got = {"strike": pv.get("strike")}
        for name, want in (quote.get("compare") or {}).items():
            chk(name, want, got.get(name))
    return diffs


def h_rfq_accept(ctx: TxContext, payload: dict) -> dict:
    """``otc.rfq.accept {rfq_id}`` (C14): book the quoted deal within the quote's validity (120 s).  IRS / TRS / CDX book at the quoted
    rate / reset price / spread (the dealer honours its quote); the other kinds (XCCY, OTC options and forwards) re-price on the market
    prints the quote was priced from (``quote["pinned"]``) and must equal the quote - else 409 QUOTE_MOVED, nothing written (an input that
    is not a print moved: a vol surface, a curve, the dealer's schedule).  The booking is ``otc.book``'s (gates, IM and its funding); the
    contract starts PENDING_CONFIRM.  Refused, writing nothing: a request not quoted yet, a quote past its validity (409 QUOTE_EXPIRED -
    the dealer job marks it EXPIRED), a closed one."""
    conn = ctx.conn
    rid = str(payload.get("rfq_id") or "")
    row = _rfq_row(conn, rid)
    if row is None:
        raise NotFound("RFQ_UNKNOWN", f"quote request {rid or '(none)'} does not exist", {"rfq_id": rid})
    status = row["status"]
    if status != "QUOTED":
        why = {"REQUESTED": "the dealer has not quoted yet (the simulated dealer answers on the next lifecycle tick, about 30 s)",
               "ACCEPTED": f"it was already accepted (contract {row.get('contract_id')})", "DECLINED": "it was declined",
               "EXPIRED": "the quote expired" + (f" ({row.get('reason')})" if row.get("reason") else "")}.get(status, status.lower())
        raise ConflictError("QUOTE_EXPIRED" if status == "EXPIRED" else "RFQ_NOT_QUOTED", f"{rid} cannot be accepted: {why}; nothing was booked",
                            {"rfq_id": rid, "status": status})
    if row.get("expires_ts") is None or ctx.ts >= int(row["expires_ts"]):
        raise ConflictError("QUOTE_EXPIRED", f"{rid}: the quote was valid until {_hms(row.get('expires_ts'))} ({int(row.get('ttl_s') or RFQ_TTL_S)} s); request a new"
                            " quote - nothing was booked", {"rfq_id": rid, "expires_ts": row.get("expires_ts")})
    quote = _jloads(row.get("quote_json"), {}) or {}
    firm = quote.get("firm") or None
    pinned = None if firm else (quote.get("pinned") or None)
    terms = _jloads(row.get("terms_json"), {}) or {}
    prev = getattr(ctx, "rfq_firm", None)
    prev_marks = ctx.marks
    ctx.rfq_firm = dict(firm, rfq_id=rid, quoted_ts=row.get("quoted_ts"), expires_ts=row.get("expires_ts")) if firm else None
    tok = None
    if pinned:                                    # a re-priced kind books on the marks it was quoted from (swaps_trs.MARKS_HOOK)
        from . import swaps_trs as _trs
        ctx.marks = _trs.PinnedMarks(prev_marks, pinned)
        tok = _trs.MARKS_HOOK.set(lambda s: _trs.PinnedMarks(s, pinned))
    try:
        out = h_book(ctx, {"account_id": row["account_id"], "kind": row["kind"], "terms": terms})
    finally:
        ctx.rfq_firm = prev
        ctx.marks = prev_marks
        if tok is not None:
            _trs.MARKS_HOOK.reset(tok)
    cid = out.get("contract_id")
    moved = _quote_moved(conn, row["kind"], quote, cid, out)
    if moved:
        raise ConflictError("QUOTE_MOVED", f"{rid}: the dealer's price moved since the quote (" + ", ".join(f"{d['term']} quoted {d['quoted']}, now {d['booked']}"
                            for d in moved) + "); request a new quote - nothing was booked", {"rfq_id": rid, "differences": moved})
    conn.execute("UPDATE rfqs SET status='ACCEPTED', closed_ts=?, contract_id=?, accept_command_id=?, reason=?, updated_ts=? WHERE rfq_id=?",
                 (ctx.ts, cid, ctx.command_id, f"booked as {cid}", ctx.ts, rid))
    ctx.event("RFQ_ACCEPTED", account_id=row["account_id"], instrument_id=cid, ref_type="rfq", ref_id=rid, kind=row["kind"], contract_id=cid,
              quote=quote.get("words"))
    return dict(out, rfq=rfq_view(_rfq_row(conn, rid), ctx.ts), rfq_id=rid)


def rfq_keys(conn: sqlite3.Connection, now_ts: int) -> List[Tuple[str, str]]:
    """RFQ_DEALER: ``(rfq_id, QUOTE)`` for every REQUESTED request and ``(rfq_id, EXPIRE)`` for every QUOTED one past its validity.  Quiet
    when idle (15.9 item 8): no open request, no key - an import's catch-up cutoff never moves with its ticks."""
    if not _has_table(conn, "rfqs"):
        return []
    now = int(now_ts)
    out: List[Tuple[str, str]] = []
    for r in conn.execute("SELECT rfq_id, status, expires_ts FROM rfqs WHERE status IN ('REQUESTED','QUOTED') ORDER BY rfq_id").fetchall():
        if r["status"] == "REQUESTED":
            out.append((r["rfq_id"], "QUOTE"))
        elif r["expires_ts"] is not None and now >= int(r["expires_ts"]):
            out.append((r["rfq_id"], "EXPIRE"))
    return out


def h_rfq_dealer(ctx: TxContext, payload: dict) -> dict:
    """``otc.rfq.dealer {rfq_id, action}`` (RFQ_DEALER, every tick; no matcher): QUOTE prices a REQUESTED request with the kind's model on the
    inputs now - the quote, labelled "indicative model quote, simulated dealer", is good for 120 s (QUOTED); a request the model cannot price
    now is EXPIRED with the reason.  EXPIRE closes a QUOTED request past its validity.  Writes its own jobs_done row; posts nothing."""
    services = _services(ctx)
    conn = ctx.conn
    rid = str(payload.get("rfq_id") or "")
    action = str(payload.get("action") or "").upper()
    key = str(payload.get("key") or f"{rid}:{action}")
    row = _rfq_row(conn, rid)
    if row is None:
        return _done_noop(ctx, RFQ_JOB, key, "UNKNOWN", f"{rid} does not exist")
    if action == "EXPIRE":
        if row["status"] != "QUOTED":
            return _done_noop(ctx, RFQ_JOB, key, "NOT_QUOTED", f"{rid} is {row['status']}")
        if ctx.ts < int(row["expires_ts"] or 0):
            return _done_noop(ctx, RFQ_JOB, key, "STILL_VALID", f"{rid} is valid until {_hms(row['expires_ts'])}")
        reason = f"not accepted within {int(row.get('ttl_s') or RFQ_TTL_S)} s (valid until {_hms(row['expires_ts'])})"
        conn.execute("UPDATE rfqs SET status='EXPIRED', closed_ts=?, reason=?, updated_ts=? WHERE rfq_id=?", (ctx.ts, reason, ctx.ts, rid))
        ctx.event("RFQ_EXPIRED", account_id=row["account_id"], ref_type="rfq", ref_id=rid, kind=row["kind"], reason=reason)
        _job_done(ctx, RFQ_JOB, key, "DONE", f"expired: {reason}")
        return {"status": "DONE", "key": key, "posted": False, "rfq_id": rid, "outcome": "EXPIRED", "note": reason}
    if action != "QUOTE":
        raise RejectError("KEY_INVALID", f"RFQ_DEALER key {key}: the action is QUOTE or EXPIRE")
    if row["status"] != "REQUESTED":
        return _done_noop(ctx, RFQ_JOB, key, "NOT_REQUESTED", f"{rid} is {row['status']}")
    aid, kind = row["account_id"], row["kind"]
    terms = _jloads(row.get("terms_json"), {}) or {}
    pinned: Dict[str, dict] = {}
    try:
        _pb_account(conn, aid)
        require_pb_otc(services, aid)
        from . import swaps_trs as _trs
        # a kind re-priced at booking (XCCY, the OTC options and forwards): record the market inputs the quote is priced from, so the
        # acceptance books on them (swaps_trs.MARKS_HOOK); IRS / TRS / CDX keep their firm price in quote["firm"]
        tok = _trs.MARKS_HOOK.set(None if str(kind).upper() in FIRM_KINDS else (lambda s: _trs.RecordingMarks(s, pinned)))
        try:
            pv = preview(services, aid, kind, terms, conn=conn, now=ctx.ts)
        finally:
            _trs.MARKS_HOOK.reset(tok)
        b = preview_blocker(pv)
        if b is not None:
            raise ConflictError(str(b["code"]), str(b.get("message") or b["code"]))
    except RejectError as e:                      # 400 / 409 alike: the dealer does not quote, said with the reason
        reason = f"the simulated dealer could not quote: {e.message}"
        conn.execute("UPDATE rfqs SET status='EXPIRED', closed_ts=?, reason=?, updated_ts=? WHERE rfq_id=?", (ctx.ts, reason[:500], ctx.ts, rid))
        ctx.event("RFQ_EXPIRED", account_id=aid, ref_type="rfq", ref_id=rid, kind=kind, reason=reason, no_quote=True, code=e.code)
        _job_done(ctx, RFQ_JOB, key, "DONE", f"no quote: {e.code}")
        return {"status": "DONE", "key": key, "posted": False, "rfq_id": rid, "outcome": "NO_QUOTE", "code": e.code, "note": reason}
    firm, compare, words = _quote_terms(kind, pv)
    expires = int(ctx.ts) + RFQ_TTL_S
    quote = {"firm": firm, "compare": compare, "words": words, "label": QUOTE_LABEL, "quoted_ts": ctx.ts, "expires_ts": expires, "ttl_s": RFQ_TTL_S,
             "preview": pv}
    if pinned and not firm:
        quote["pinned"] = pinned                  # the marks the quote was priced from (an acceptance books on them)
    label = f"{words} - {QUOTE_LABEL}, valid until {_hms(expires)}"
    conn.execute("UPDATE rfqs SET status='QUOTED', quoted_ts=?, quote_json=?, quote_label=?, ttl_s=?, expires_ts=?, updated_ts=? WHERE rfq_id=?",
                 (ctx.ts, dumps(quote), label, RFQ_TTL_S, expires, ctx.ts, rid))
    ctx.event("RFQ_QUOTED", account_id=aid, ref_type="rfq", ref_id=rid, kind=kind, quote=words, expires_ts=expires, label=QUOTE_LABEL)
    _job_done(ctx, RFQ_JOB, key, "DONE", f"quoted: {words}")
    return {"status": "DONE", "key": key, "posted": False, "rfq_id": rid, "outcome": "QUOTED", "note": f"quoted: {words}"}


# ---- the contract timeline (OMS-10: requested, quoted, accepted, booked, IM posted, each VM, resets, termination) --------------------------
TIMELINE_ORDER = {"REQUESTED": 0, "QUOTED": 1, "ACCEPTED": 2, "BOOKED": 3, "CONVERSION": 3, "IM": 4, "CONFIRMED": 5, "UPFRONT": 5, "FIX": 6, "FUNDING": 6,
                  "DEAL_SETTLED": 6, "TRUEUP": 6, "VM": 7, "PAY": 7, "RESET": 7, "EXCHANGE": 7, "CREDIT_EVENT": 8, "TERMINATED": 9, "MATURED": 9,
                  "CONFIRM_PENDING": 10}
RESET_STEPS = {"FIX": ("FIX", "fixing"), "PAY": ("PAY", "payment"), "RESET": ("RESET", "reset"), "NOTIONAL_START": ("EXCHANGE", "start exchange"),
               "NOTIONAL_END": ("EXCHANGE", "maturity exchange"), "FX_RESET": ("RESET", "FX notional reset")}


#: P8R1-03: the booking and the steps that post entries (their business date is said in words when they were recorded after the 17:00 ET
#: cut-off); a quote request, a quote and its acceptance post nothing
TIMELINE_POSTING = ("BOOKED", "IM", "UPFRONT", "VM", "PAY", "RESET", "EXCHANGE", "CREDIT_EVENT", "TERMINATED", "MATURED", "CONVERSION", "FUNDING",
                    "DEAL_SETTLED", "TRUEUP")


def contract_timeline(services, contract, conn: Optional[sqlite3.Connection] = None) -> List[dict]:
    """The life of one OTC contract in time order (OMS-10, P4 task 8): the quote request it came from (requested, quoted, accepted), the
    booking (an as-of booking with its trade date and entry time), the initial margin, the confirmation, the CDX upfront, every variation
    margin, fixing, payment, reset and exchange, a simulated credit event, the termination or maturity.  Each step ``{date, business_date,
    ts, recorded_ts, after_cutoff, step, words, amount_c, entry_id, ref, pending, asof}``; a step still to come (a pending confirmation)
    has ``pending`` true.

    P8R1-03: ``date`` and ``ts`` never make an instant that did not happen, and the rows read in order:
    - a step recorded on its own day is shown at its New York calendar date and clock time (``date`` + ``ts``); when it posts entries whose
      business date is the NEXT day (recorded after the 17:00 ET cut-off), ``after_cutoff`` is true and ``business_date`` names that day,
      said in words by the page - never that business date beside a clock time of the evening before;
    - a step recorded on a later day than the business date it belongs to (an as-of booking's lifecycle entered at the import, a variation
      margin caught up the next morning) is shown at its business date "as of" (``asof`` true, no clock time), and ``recorded_ts`` is when
      it was recorded; the as-of booking itself keeps its trade date (its ``ts`` is the as-of dealt instant, which orders the steps);
    - the rows are sorted by that date, then by the time recorded, then by the step's place in the life of a contract."""
    if conn is None:
        with _marks.shared_reader(services.db) as c:
            return contract_timeline(services, contract, c)
    row = _contract_dict(services, contract, conn)
    if row is None:
        return []
    cid = row["contract_id"]
    terms = _terms(row)
    dealer = config.PRIME_BROKERS.get(str(row.get("broker") or "").upper(), str(row.get("broker") or "the dealer"))
    items: List[dict] = []

    def add(ts, d, step, words, amount_c=None, entry_id=None, ref=None, pending=False, asof=False):
        """``d`` = the step's business date (its entry's, its job's date; None: the accounting day of ``ts``, as ``ledger.post`` dates)."""
        t = None if ts in (None, "") or isinstance(ts, bool) else int(ts)
        cal = ny_of(t).date().isoformat() if t is not None else None
        bd = d or (accounting_day(ny_of(t)).isoformat() if t is not None else None)
        recorded = None
        if asof:                                   # the as-of booking (and its own fixing): the trade date, "as of"
            shown = d or cal
        elif t is not None and bd is not None and cal is not None and bd < cal:
            shown, asof, recorded = bd, True, t     # recorded after the business date it belongs to: "as of" that date
        else:
            shown = cal or bd
        after = bool(not asof and t is not None and bd is not None and cal is not None and bd > cal and step in TIMELINE_POSTING)
        items.append({"date": shown, "business_date": bd, "ts": t, "recorded_ts": recorded, "after_cutoff": after, "step": step, "words": words,
                      "amount_c": amount_c, "entry_id": entry_id, "ref": ref, "pending": pending, "asof": asof})
    if _has_table(conn, "rfqs"):
        q = conn.execute("SELECT * FROM rfqs WHERE contract_id=? ORDER BY requested_ts LIMIT 1", (cid,)).fetchone()
        if q is not None:
            qd = _jloads(q["quote_json"], {}) or {}
            add(q["requested_ts"], None, "REQUESTED", f"quote requested from {dealer} ({q['rfq_id']})", ref=q["rfq_id"])
            if q["quoted_ts"]:
                add(q["quoted_ts"], None, "QUOTED", f"quoted: {qd.get('words') or ''} ({QUOTE_LABEL}), valid until {_hms(q['expires_ts'])}", ref=q["rfq_id"])
            if q["closed_ts"]:
                add(q["closed_ts"], None, "ACCEPTED", "quote accepted by you", ref=q["rfq_id"])
    booking = booking_of(row)
    booked_ts = None
    if booking in (ASOF_BOOKING, HISTORICAL_BOOKING):
        booked_ts = terms.get("dealt_ts") or row.get("entered_ts")
        add(booked_ts, row.get("trade_date") or terms.get("trade_date"), "BOOKED",
            f"traded as of {row.get('trade_date') or terms.get('trade_date')} with {dealer} (value date {row.get('value_date') or row.get('start_date')}); entered"
            f" {ny_of(int(row['entered_ts'])).strftime('%Y-%m-%d %H:%M ET') if row.get('entered_ts') else 'at an unrecorded time'} by the operator at the owner's"
            " instruction", asof=True)            # ts = the as-of dealt instant (it orders the steps); the page shows "as of", not a clock time
    else:
        ev = conn.execute("SELECT ts FROM events WHERE ref_type='otc' AND ref_id=? AND type='OTC_BOOKED' ORDER BY id LIMIT 1", (cid,)).fetchone()
        booked_ts = ev["ts"] if ev is not None else terms.get("dealt_ts")
        add(booked_ts, None, "BOOKED", f"booked with {dealer}: {KIND_LABELS.get(row['kind'], row['kind'])}")
    for idem, step, what, gl in ((f"im:{cid}:open", "IM", "initial margin posted", "1330"), (f"otc:{cid}:upfront", "UPFRONT", "initial payment booked", None),
                                 (f"otc:{cid}:premium", "UPFRONT", "option premium booked", None)):
        e = conn.execute("SELECT entry_id, ts, business_date FROM journal_entries WHERE idem_key=?", (idem,)).fetchone()
        if e is None:
            continue
        amt = None
        if gl:
            a = conn.execute("SELECT COALESCE(SUM(amount_c), 0) FROM journal_lines WHERE entry_id=? AND gl=?", (e["entry_id"], gl)).fetchone()[0]
            amt = int(a or 0)
        add(e["ts"], e["business_date"], step, what + (f" {fmt_money(amt)}" if amt else ""), amount_c=amt, entry_id=e["entry_id"])
    # FX_CCS_PLAN K5 (1.0.6.1): the FX desk deals linked to the contract (its inception conversion, the settlement-funding purchases) and the
    # true-ups of the nightly estimates to the published indices
    for dl in linked_deals(conn, row):
        dr_ = conn.execute("SELECT trade_ts, settled_ts, entry_id FROM fx_deals WHERE deal_id=?", (dl["deal_id"],)).fetchone()
        what = ("inception conversion" if dl.get("link_type") == "xccy_start" else "settlement-funding purchase")
        add(dr_["trade_ts"] if dr_ is not None else None, None, "CONVERSION" if dl.get("link_type") == "xccy_start" else "FUNDING",
            f"{what} {dl['deal_id']} booked: buy {dl['buy_ccy']} {fmt_money(dl['buy_c'])} / sell {dl['sell_ccy']} {fmt_money(dl['sell_c'])} at {dl['rate']},"
            f" value {dl['value_date']}", entry_id=None if dr_ is None else dr_["entry_id"], ref=dl["deal_id"])
        if dl.get("status") == "SETTLED" and dr_ is not None and dr_["settled_ts"]:
            add(dr_["settled_ts"], dl["value_date"], "DEAL_SETTLED", f"{what} {dl['deal_id']} settled (value {dl['value_date']})", ref=dl["deal_id"])
    for e in conn.execute("SELECT entry_id, ts, business_date, idem_key FROM journal_entries WHERE idem_key LIKE ? ORDER BY entry_id",
                          (f"accr:%:{cid}:trueup:%",)).fetchall():
        parts = str(e["idem_key"]).split(":")
        amt = conn.execute("SELECT currency, amount_c FROM journal_lines WHERE entry_id=? AND gl IN ('1225','2340') ORDER BY line_no LIMIT 1",
                           (e["entry_id"],)).fetchone()
        sign = 1
        if amt is not None:
            sign = 1 if (int(amt["amount_c"]) > 0) == (parts[1] == "swaprcv") else -1
        words = (f"{'receive' if parts[1] == 'swaprcv' else 'pay'} leg trued up to the published index for the period ending {parts[-1]}"
                 + (f": {amt['currency']} {fmt_money(sign * abs(int(amt['amount_c'])))}" if amt is not None else ""))
        add(e["ts"], e["business_date"], "TRUEUP", words, amount_c=None if amt is None else sign * abs(int(amt["amount_c"])), entry_id=e["entry_id"])
    status = row.get("confirm_status")
    if status == CONFIRMED:
        add(row.get("confirmed_ts"), None if booking not in (ASOF_BOOKING, HISTORICAL_BOOKING) else (row.get("trade_date") or None), "CONFIRMED",
            confirm_label(booking, status))
    elif status == PENDING_CONFIRM:
        d = confirm_date(row.get("entered_ts"), getattr(services, "cal", None))
        add(None if d is None else epoch_of(d, CONFIRM_ET), None if d is None else d.isoformat(), "CONFIRM_PENDING", PENDING_CONFIRM_LABEL, pending=True)
    for r in conn.execute("SELECT v.vm_date, v.mark_c, v.delta_c, v.mode, v.entry_id, v.inputs_json, j.ts FROM otc_vm v LEFT JOIN journal_entries j"
                          " ON j.entry_id=v.entry_id WHERE v.contract_id=? ORDER BY v.vm_date", (cid,)).fetchall():
        d = int(r["delta_c"])
        vin = _jloads(r["inputs_json"], {}) or {}
        if vin.get("csa_exposure_c") is not None and vin.get("clean_mark_c") is not None:   # FX_CCS_PLAN D8: margined on the CSA exposure
            valued = f"CSA exposure {fmt_money(int(r['mark_c']))} (clean mark {fmt_money(int(vin['clean_mark_c']))})"
        else:
            valued = f"mark {fmt_money(int(r['mark_c']))}"
        add(r["ts"], r["vm_date"], "VM", f"variation margin {fmt_money(d)} {'received' if d > 0 else 'posted'} ({r['mode']}); {valued}",
            amount_c=d, entry_id=r["entry_id"])
    asof_contract = booking in (ASOF_BOOKING, HISTORICAL_BOOKING)
    for r in conn.execute("SELECT o.event_date, o.kind, o.amount_c, o.entry_id, o.detail_json, j.ts, j.business_date FROM otc_resets o LEFT JOIN journal_entries j"
                          " ON j.entry_id=o.entry_id WHERE o.contract_id=? ORDER BY o.event_date, o.kind", (cid,)).fetchall():
        step, what = RESET_STEPS.get(r["kind"], ("PAY", r["kind"].lower()))
        det = _detail(r)
        words = what
        ts = r["ts"]
        at_booking = r["kind"] == "FIX" and det.get("source") == "booking"
        if ts is None and r["kind"] == "FIX":           # a fixing posts nothing: at booking (the first period) or by SWAP_FIX (00:40 ET of its date, or later)
            if at_booking:
                ts = booked_ts
            else:
                jd = conn.execute("SELECT run_ts FROM jobs_done WHERE job=? AND key=?", (JOB_FIX, f"{cid}:{r['event_date']}")).fetchone()
                ts = jd["run_ts"] if jd is not None and jd["run_ts"] else epoch_of(_date(r["event_date"]), FIX_ET)
        if r["kind"] == "FIX":
            words = f"SOFR fixing {det.get('rate_pct') or det.get('rate')} for {det.get('period_start')}..{det.get('period_end')}"
        elif r["kind"] in ("PAY", "RESET", "FX_RESET") and int(r["amount_c"] or 0):
            words = f"{what}: net {fmt_money(int(r['amount_c']))}" + (" (CDX coupon)" if det.get("kind") == "CDX coupon" else "")
        add(ts, r["business_date"] or r["event_date"], step, words, amount_c=int(r["amount_c"] or 0) if r["kind"] != "FIX" else None, entry_id=r["entry_id"],
            asof=at_booking and asof_contract)
    for e in conn.execute("SELECT ts, type, payload_json FROM events WHERE ref_type='otc' AND ref_id=? AND type IN ('CDX_CREDIT_EVENT','OTC_TERMINATED',"
                          "'SWAP_MATURED','OTC_EXPIRED') ORDER BY id", (cid,)).fetchall():
        p = _jloads(e["payload_json"], {}) or {}
        if e["type"] == "CDX_CREDIT_EVENT":
            ce = conn.execute("SELECT business_date FROM journal_entries WHERE entry_id=?", (p.get("entry_id"),)).fetchone() if p.get("entry_id") else None
            add(e["ts"], ce["business_date"] if ce is not None else None, "CREDIT_EVENT",
                f"simulated credit event of {p.get('event_date')}: {p.get('reference_entity')} at {p.get('recovery_pct')} % recovery - net"
                f" {fmt_money(int(p.get('net_c') or 0))} settling {p.get('settle_date')}; notional {fmt_money(int(p.get('notional_after_c') or 0))}",
                amount_c=p.get("net_c"), entry_id=p.get("entry_id"))
        elif e["type"] == "OTC_TERMINATED":
            add(e["ts"], None, "TERMINATED", f"terminated at the model value {fmt_money(int(p.get('termination_value_c') or 0))}"
                + (" (the full value, cash-settled in USD; nothing re-exchanged)" if row.get("kind") == "XCCY" else ""),
                amount_c=p.get("termination_value_c"))
        else:
            add(e["ts"], p.get("date"), "MATURED", "matured: VM unwound, IM released")
    items.sort(key=lambda x: (x["date"] or "9999-99-99", x["ts"] if x["ts"] is not None else 10 ** 11, TIMELINE_ORDER.get(x["step"], 6)))
    return items


# ---- the Derivatives page (UI-11, P4 task 10): OTC contracts, listed options, futures, servicing --------------------------------------------
DERIV_TABS = ("otc", "options", "futures", "servicing")
#: P4 task 10: the delivery rule of each futures root - "fnd" = physical delivery with a first notice day on the last business day of the
#: month before the delivery month (COMEX metals, CBOT rates and grains); "last_trade" = physical delivery at the last trading day (NYMEX
#: energy, CME FX); "cash" = cash-settled at expiry.  CME / COMEX / CBOT / NYMEX contract specifications, summarised (approximate dates)
FUT_DELIVERY = {"GC": "fnd", "SI": "fnd", "HG": "fnd", "ZB": "fnd", "ZN": "fnd", "ZF": "fnd", "ZT": "fnd", "ZC": "fnd", "ZW": "fnd", "ZS": "fnd",
                "CL": "last_trade", "NG": "last_trade", "6E": "last_trade", "6J": "last_trade", "6B": "last_trade",
                "ES": "cash", "NQ": "cash", "YM": "cash", "RTY": "cash", "BZ": "cash"}


def first_notice(cal, root: str, delivery_month: Optional[date], last_trade: Optional[date]) -> dict:
    """``{rule, first_notice, act_by, words}`` of a futures contract (P4 task 10): physical delivery with a first notice day on the last business
    day of the month before the delivery month (roll or close by the business day before it), physical delivery at the last trading day, or
    cash settlement - dates approximate, on the terminal's NYSE calendar."""
    rule = FUT_DELIVERY.get(str(root or "").upper(), "cash")
    if rule == "fnd" and delivery_month is not None and cal is not None:
        fnd = cal.roll_back(delivery_month - timedelta(days=1))
        act = cal.roll_back(fnd - timedelta(days=1))
        return {"rule": "physical", "first_notice": fnd.isoformat(), "act_by": act.isoformat(),
                "words": (f"physically delivered: first notice about {fnd.isoformat()} (the last business day before the delivery month); roll or close before"
                          f" about {act.isoformat()} to avoid delivery")}
    if rule == "last_trade":
        lt = None if last_trade is None else last_trade.isoformat()
        return {"rule": "physical", "first_notice": None, "act_by": lt,
                "words": f"physically delivered: positions still open at the last trading day ({lt or 'see the contract'}) go to delivery - roll or close before it"}
    lt = None if last_trade is None else last_trade.isoformat()
    return {"rule": "cash", "first_notice": None, "act_by": None, "words": f"cash-settled at expiry ({lt or 'see the contract'})"}


def _deriv_otc(services, lifetime_id: str) -> dict:
    scope = f"{lifetime_id}:ALL"
    rows = contracts_view(services, scope=scope)
    for c in rows:
        c.pop("inputs", None)                            # the page reads the mark and position fields; the raw inputs stay on the OTC tab
    credit = {}
    cds = getattr(services, "swaps_cds", None)
    fn = getattr(cds, "credit_events_of", None) if cds is not None else None
    if callable(fn):
        try:
            credit = fn(services, [c["contract_id"] for c in rows if c.get("kind") == "CDX"]) or {}
        except Exception as e:   # noqa: BLE001 - shown unavailable, never a failed page
            credit = {"error": f"{type(e).__name__}: {e}"}
    open_rfqs = rfq_rows(services, scope)
    recent = [r for r in rfq_rows(services, scope, status="all", limit=20) if r["status"] not in RFQ_OPEN]
    return {"tab": "otc", "lifetime_id": lifetime_id, "contracts": rows, "rfqs": open_rfqs, "recent_rfqs": recent, "credit_events": credit,
            "credit_event_offered": callable(getattr(cds, "h_credit_event", None)) if cds is not None else False,
            "note": "OTC values are MODEL numbers from observed inputs, never dealer-confirmed prices; dealing stays on the prime broker's OTC tab"}


def _deriv_options(services, lifetime_id: str) -> dict:
    rk = getattr(services, "risk", None)
    fn = getattr(rk, "report", None) if rk is not None else None
    if not callable(fn):
        return {"tab": "options", "available": False, "reason": "the risk module is not bound in this build: option greeks are unavailable", "rows": [],
                "strategies": []}
    view = fn(services, f"{lifetime_id}:ALL")
    opts = [p for p in (getattr(view, "positions", []) or []) if getattr(p, "kind", None) == "OPTION"]
    rows = []
    strategies = []
    master = getattr(services, "instruments", None)
    with _marks.shared_reader(services.db) as conn:
        names = {r["account_id"]: r["name"] for r in conn.execute("SELECT account_id, name FROM accounts WHERE lifetime_id=?", (lifetime_id,)).fetchall()}
        for p in opts:
            d = p.to_dict() if hasattr(p, "to_dict") else {}
            g = getattr(p, "greeks", None) or {}
            inst = master.get(p.instrument_id, conn) if master is not None else None
            rows.append({"account_id": p.account_id, "instrument_id": p.instrument_id, "symbol": p.symbol, "name": p.name, "qty": p.qty, "mark": d.get("mark"),
                         "mv_c": p.mv_base_c if p.mv_base_c is not None else p.mv_c, "quality": p.quality, "underlying_id": p.underlying_id,
                         "underlying_mark": d.get("underlying_mark"), "right": p.right or (inst.right if inst is not None else None),
                         "style": p.style or (inst.style if inst is not None else None), "multiplier": d.get("multiplier"),
                         "strike": None if inst is None or inst.strike is None else str(inst.strike), "expiry": None if inst is None else inst.expiry,
                         "greeks": {k: (None if v is None else str(v)) for k, v in g.items()} if g else None, "greeks_source": p.greeks_source,
                         "greeks_note": p.greeks_note, "delta_c": p.delta_c, "label": p.label or p.name})
        held = {(r["account_id"], r["instrument_id"]) for r in rows}
        accts = sorted({r["account_id"] for r in rows})
        if accts:
            q = ",".join("?" for _ in accts)
            for pk in conn.execute(f"SELECT package_id, account_id, strategy_key, candidate_id, legs_json, status FROM hedge_packages WHERE account_id IN ({q})"
                                   " ORDER BY created_ts", tuple(accts)).fetchall():
                legs = _jloads(pk["legs_json"], []) or []
                ids = [str(x.get("instrument_id") or "") for x in legs if isinstance(x, dict)]
                if len(ids) < 2 or not all((pk["account_id"], i) in held for i in ids):
                    continue                        # a strategy is grouped while every one of its option legs is still held
                strategies.append({"package_id": pk["package_id"], "account_id": pk["account_id"], "strategy": pk["strategy_key"] or pk["candidate_id"],
                                   "strategy_words": str(pk["strategy_key"] or pk["candidate_id"] or "").replace("_", " ").lower(),
                                   "legs": [{"instrument_id": str(x.get("instrument_id")), "side": x.get("side"), "qty": x.get("qty"), "price": x.get("price")}
                                            for x in legs if isinstance(x, dict)]})
    for r in rows:
        r["account_name"] = names.get(r["account_id"], r["account_id"])
        r["strategy"] = next((s["strategy_words"] for s in strategies if s["account_id"] == r["account_id"] and r["instrument_id"] in
                              [l["instrument_id"] for l in s["legs"]]), None)
    rows.sort(key=lambda r: (r.get("expiry") or "", r["underlying_id"] or "", r["instrument_id"]))
    return {"tab": "options", "available": True, "rows": rows, "strategies": strategies, "as_of": getattr(view, "as_of", None),
            "greeks_total": getattr(view, "greeks", None), "note": "greeks per contract (Cboe where published, else the model on the mark's inputs); position"
                                                                   " greeks = per contract x contracts x multiplier"}


def _deriv_futures(services, lifetime_id: str) -> dict:
    acc = getattr(services, "accounting", None)
    if acc is None or not hasattr(acc, "positions"):
        return {"tab": "futures", "available": False, "reason": "the accounting service is not bound", "rows": []}
    scope = f"{lifetime_id}:ALL"
    snap_fn = getattr(getattr(services, "marks", None), "snapshot", None)
    snap = snap_fn() if callable(snap_fn) else None
    positions = [p for p in acc.positions(scope, snapshot=snap) if getattr(p, "kind", None) == "FUTURE"]
    rows = []
    cal = getattr(services, "cal", None)
    with _marks.shared_reader(services.db) as conn:
        names = {r["account_id"]: r["name"] for r in conn.execute("SELECT account_id, name FROM accounts WHERE lifetime_id=?", (lifetime_id,)).fetchall()}
        for p in positions:
            inst = services.instruments.get(p.instrument_id, conn) if getattr(services, "instruments", None) is not None else None
            spec = dict(getattr(inst, "spec", None) or {}) if inst is not None else {}
            mult = Decimal(str(getattr(inst, "multiplier", None) or "1")) if inst is not None else Decimal(1)
            last_trade = _date(inst.expiry) if inst is not None and getattr(inst, "expiry", None) else None
            month = None
            try:
                month = date(int(spec.get("year")), int(spec.get("month")), 1)
            except (TypeError, ValueError):
                month = None
            im_c = balance(conn, p.account_id, "1300", p.currency, p.instrument_id)
            vms = conn.execute("SELECT settle_date, settle_px, vm_c FROM futures_settle WHERE account_id=? AND instrument_id=? ORDER BY settle_date DESC LIMIT 6",
                               (p.account_id, p.instrument_id)).fetchall()
            last = vms[0] if vms else None
            mark = p.mark.mark if getattr(p, "mark", None) is not None else None
            ote = None
            if mark is not None and p.avg_px is not None:
                ote = to_c((Decimal(str(mark)) - Decimal(str(p.avg_px))) * Decimal(int(p.qty)) * mult)
            since = None
            if mark is not None and last is not None:
                since = to_c((Decimal(str(mark)) - Decimal(str(last["settle_px"]))) * Decimal(int(p.qty)) * mult)
            fn_ = first_notice(cal, spec.get("root") or (p.symbol or "")[:2], month, last_trade)
            rows.append({"account_id": p.account_id, "account_name": names.get(p.account_id, p.account_id), "instrument_id": p.instrument_id, "symbol": p.symbol,
                         "name": p.name, "root": spec.get("root"), "qty": int(p.qty), "multiplier": str(mult), "avg_px": None if p.avg_px is None else str(p.avg_px),
                         "mark": None if mark is None else str(mark), "quality": getattr(p.mark, "quality", None), "im_c": im_c,
                         "last_settle": None if last is None else {"date": last["settle_date"], "price": last["settle_px"], "vm_c": int(last["vm_c"] or 0)},
                         "vm_recent": [{"date": r["settle_date"], "price": r["settle_px"], "vm_c": int(r["vm_c"] or 0)} for r in vms],
                         "ote_c": ote, "move_since_settle_c": since, "last_trade": None if last_trade is None else last_trade.isoformat(), "delivery": fn_})
    rows.sort(key=lambda r: (r["last_trade"] or "", r["instrument_id"]))
    return {"tab": "futures", "available": True, "rows": rows,
            "note": ("IM = the initial margin posted (GL 1300); daily VM = each settle's variation margin in cash; open trade equity = (mark - average entry) x"
                     " contracts x multiplier; first notice dates are approximate (exchange rule summarised on the NYSE calendar)")}


def _deriv_servicing(services, lifetime_id: str) -> dict:
    today = ny_of(int(services.clock.epoch())).date()
    items: List[dict] = []
    with _marks.shared_reader(services.db) as conn:
        names = {r["account_id"]: r["name"] for r in conn.execute("SELECT account_id, name FROM accounts WHERE lifetime_id=?", (lifetime_id,)).fetchall()}
        crows = conn.execute("SELECT * FROM otc_contracts WHERE account_id LIKE ? AND status='OPEN' ORDER BY contract_id", (f"{lifetime_id}:%",)).fetchall()
        for r in crows:
            rd = {k: r[k] for k in r.keys()}
            try:
                evs = next_events(services, rd, conn, limit=4)
            except (ConflictError, RejectError) as e:
                evs = [{"date": None, "event": "UNAVAILABLE", "detail": e.message}]
            label, side, _u, _n = rfq_describe(rd["kind"], _terms(rd))
            for e in evs:
                legs = e.get("legs") or []
                amt = None
                if legs and all(l.get("amount_c") is not None for l in legs):
                    amt = sum(int(l["amount_c"]) * (1 if l.get("side") == "receive" else -1) for l in legs)
                items.append({"date": e.get("date"), "event": e.get("event"), "kind": "OTC", "contract_id": rd["contract_id"], "account_id": rd["account_id"],
                              "account_name": names.get(rd["account_id"], rd["account_id"]), "what": f"{rd['contract_id']} {label} ({side.lower()})",
                              "detail": e.get("detail") or ", ".join(f"{l.get('side')} {l.get('leg')} {fmt_money(int(l['amount_c'])) if l.get('amount_c') is not None else 'not known yet'}"
                                                                      for l in legs), "amount_c": amt})
        for r in conn.execute("SELECT l.account_id, l.instrument_id, SUM(l.qty_remaining) AS q, i.kind, i.expiry, i.symbol, i.spec_json FROM lots l JOIN"
                              " instruments i ON i.instrument_id=l.instrument_id JOIN accounts a ON a.account_id=l.account_id WHERE a.lifetime_id=?"
                              " AND l.qty_remaining<>0 AND i.kind IN ('OPTION','FUTURE') GROUP BY l.account_id, l.instrument_id", (lifetime_id,)).fetchall():
            if not r["expiry"]:
                continue
            spec = _jloads(r["spec_json"], {}) or {}
            if r["kind"] == "OPTION":
                items.append({"date": r["expiry"], "event": "EXPIRY", "kind": "Option", "instrument_id": r["instrument_id"], "account_id": r["account_id"],
                              "account_name": names.get(r["account_id"], r["account_id"]), "what": f"{r['symbol']} x {int(r['q'])}",
                              "detail": "listed option expiry: in-the-money contracts are exercised / assigned, the rest expire worthless", "amount_c": None})
            else:
                try:
                    month = date(int(spec.get("year")), int(spec.get("month")), 1)
                except (TypeError, ValueError):
                    month = None
                fn_ = first_notice(getattr(services, "cal", None), spec.get("root"), month, _date(r["expiry"]))
                if fn_.get("first_notice"):
                    items.append({"date": fn_["act_by"], "event": "ROLL_BY", "kind": "Future", "instrument_id": r["instrument_id"], "account_id": r["account_id"],
                                  "account_name": names.get(r["account_id"], r["account_id"]), "what": f"{r['symbol']} x {int(r['q'])}", "detail": fn_["words"],
                                  "amount_c": None})
                items.append({"date": r["expiry"], "event": "LAST_TRADE", "kind": "Future", "instrument_id": r["instrument_id"], "account_id": r["account_id"],
                              "account_name": names.get(r["account_id"], r["account_id"]), "what": f"{r['symbol']} x {int(r['q'])}",
                              "detail": "last trading day" + (f"; {fn_['words']}" if fn_.get("rule") == "cash" else ""), "amount_c": None})
    items = [x for x in items if x.get("date") is None or x["date"] >= today.isoformat()]
    items.sort(key=lambda x: (x.get("date") or "9999-99-99", x.get("kind") or "", x.get("what") or ""))
    return {"tab": "servicing", "available": True, "rows": items, "as_of": today.isoformat(),
            "note": "upcoming resets, payments, coupons, maturities, option expiries and futures delivery dates; amounts not known yet show as such"}


def derivatives_view(services, lifetime_id: str, tab: str = "otc") -> dict:
    """``GET /api/derivatives?tab=&book=`` (UI-11, P4 task 10): one tab of the Derivatives page - ``otc`` (the book's OTC contracts with open
    quote requests on top and CDX credit events), ``options`` (listed options with greeks, strategies grouped), ``futures`` (IM, daily VM,
    open trade equity, delivery dates), ``servicing`` (what is coming: resets, payments, coupons, expiries, first notice)."""
    t = str(tab or "otc").lower()
    if t not in DERIV_TABS:
        raise RejectError("TAB", f"tab must be one of {', '.join(DERIV_TABS)}", {"tab": t})
    fn = {"otc": _deriv_otc, "options": _deriv_options, "futures": _deriv_futures, "servicing": _deriv_servicing}[t]
    out = fn(services, lifetime_id)
    out.setdefault("available", True)
    out["generated_ts"] = int(services.clock.epoch())
    return out


# ---- routes (C1: a bound product module adds its routes ahead of api_products) -------------------------------------------------------------
_PREVIEW_REFUSALS = (400, 409)


def _refused_preview(kind: str, account_id: str, e: RejectError) -> dict:
    """V104R2-BW-05: a correctly refused OTC preview (no curve, no proxy, an unknown underlying, a rate better than the quote ...) answers 200
    with the reason as a blocker, as the bond / FX / order tickets do - no 4xx (and no console error) for a refusal the trader must read."""
    return {"refused": True, "bookable": False, "kind": kind, "account_id": account_id, "code": e.code, "message": e.message, "details": e.details or {},
            "blockers": [{"code": e.code, "message": e.message, "details": e.details or {}}]}


def register_routes(router) -> None:
    """C1 / C14 / UI-11: ``POST /api/otc/preview`` (takes over api_products' route: a refusal answers 200 with its blocker, V104R2-BW-05),
    ``GET|POST /api/otc/rfqs``, ``GET /api/otc/rfqs/{id}``, ``POST /api/otc/rfqs/{id}/accept|decline``, ``GET /api/otc/contracts/{id}/timeline``
    and ``GET /api/derivatives?tab=&book=``."""
    from ..api import ApiError, NotFound as _ApiNotFound, need, run_command

    def otc_preview(req):
        sw = need(req.rt, "swaps")
        kind = str(req.b("kind") or "").upper()
        allowed = ("IRS", "TRS", "XCCY", "CDX") + (OTC_OPTION_KINDS if getattr(req.rt, "otc_options", None) is not None else ())
        if kind not in allowed:
            raise ApiError(400, "NOT_IN_THIS_BUILD" if kind else "BAD_REQUEST", f"OTC kind {kind!r}: the prime brokers here deal {', '.join(allowed)}")
        aid = str(req.b("account_id") or "")
        with _marks.shared_reader(req.rt.db) as conn:
            if account_row(conn, aid) is None:
                raise _ApiNotFound(f"account {aid} does not exist")
        try:
            return sw.preview(req.rt, aid, kind, dict(req.b("terms") or {}))
        except NotBuilt:
            raise
        except NotFound:
            raise
        except RejectError as e:
            retry = (e.details or {}).get("retry_ms")
            if retry:                     # V104R1-06: an input being fetched for this preview: 202 + retry_ms, the ticket asks again by itself
                return 202, dict(e.details or {}, status="fetching", pending=True, code=e.code, message=e.message, retry_ms=int(retry)), {}
            if e.http in _PREVIEW_REFUSALS:
                return _refused_preview(kind, aid, e)
            raise

    def rfqs_list(req):
        need(req.rt, "swaps")
        aid = req.q("account_id")
        scope = None if aid else req.scope()
        return rfq_rows(req.rt, scope=scope, status=req.q("status") or "open", account_id=aid, limit=req.q_int("limit", 200, 1, 1000))

    def rfqs_request(req):
        payload = {k: v for k, v in (req.body or {}).items() if k != "request_id"}
        return run_command(req, "otc.rfq.request", payload)

    def rfq_one(req):
        need(req.rt, "swaps")
        out = rfq_detail(req.rt, req.params[0])
        if out is None:
            raise _ApiNotFound(f"quote request {req.params[0]} does not exist")
        return out

    def rfq_accept(req):
        return run_command(req, "otc.rfq.accept", {"rfq_id": req.params[0]})

    def rfq_decline(req):
        return run_command(req, "otc.rfq.decline", {"rfq_id": req.params[0]})

    def timeline(req):
        need(req.rt, "swaps")
        with _marks.shared_reader(req.rt.db) as conn:
            if _contract_row(conn, req.params[0]) is None:
                raise _ApiNotFound(f"contract {req.params[0]} does not exist")
            return {"contract_id": req.params[0], "timeline": contract_timeline(req.rt, req.params[0], conn)}

    def derivatives(req):
        need(req.rt, "swaps")
        try:
            return derivatives_view(req.rt, req.lifetime(), req.q("tab") or "otc")
        except RejectError as e:
            raise ApiError(400, e.code, e.message, e.details)

    router.add("POST", r"/api/otc/preview", otc_preview, mutating=False)
    router.add("GET", r"/api/otc/rfqs", rfqs_list)
    router.add("POST", r"/api/otc/rfqs", rfqs_request)
    router.add("GET", r"/api/otc/rfqs/([^/]+)", rfq_one)
    router.add("POST", r"/api/otc/rfqs/([^/]+)/accept", rfq_accept)
    router.add("POST", r"/api/otc/rfqs/([^/]+)/decline", rfq_decline)
    router.add("GET", r"/api/otc/contracts/([^/]+)/timeline", timeline)
    router.add("GET", r"/api/derivatives", derivatives)


def _rfq_args(conn, clock, cal, sessions, s):
    return (conn, clock.epoch())


def _confirm_args(conn, clock, cal, sessions, s):
    return (conn, clock.epoch(), cal)


def add_jobs() -> None:
    """C14: the RFQ_DEALER job (every tick; no matcher) and the OTC_CONFIRM job (17:05 ET) join the lifecycle registry through the existing
    ``lifecycle_eod.add_product_job`` hook (idempotent by name).  Both are quiet when idle."""
    from ..lifecycle_eod import ProductJob, add_product_job
    add_product_job(ProductJob(RFQ_JOB, "otc.rfq.dealer", "swaps", "rfq_keys", ("rfq_id", "action"),
                               "every lifecycle tick (about 30 s): quotes REQUESTED quote requests, expires QUOTED ones past their 120 s validity", _rfq_args))
    add_product_job(ProductJob(CONFIRM_JOB, "otc.confirm", "swaps", "confirm_keys", ("contract_id", "date"),
                               "17:05 ET of a new contract's business day: the simulated confirmation", _confirm_args))


# ---- registry ---------------------------------------------------------------------------------------------------------------------
def register(bus, services=None) -> None:
    """Register ``otc.book`` / ``otc.terminate`` (ui) and ``swap.vm`` / ``swap.fix`` / ``swap.pay`` (lifecycle); the runtime
    binds this module as ``services.swaps`` so ``marks.py``, ``accounting`` (``dv01_c``), risk (``next_events``),
    ACCRUE_DAILY (``accrue_night``) and the SWAP_* ProductJobs (``vm_keys`` / ``fix_keys`` / ``pay_keys``) can reach it."""
    bus.register("otc.book", h_book)
    bus.register("otc.book_as_of", h_book_as_of)
    bus.register("otc.terminate", h_terminate)
    bus.register("swap.vm", h_vm)
    bus.register("swap.fix", h_fix)
    bus.register("swap.pay", h_pay)
    bus.register("otc.rfq.request", h_rfq_request)                 # C14 (P4 task 8): quote request, the dealer's quote, accept / decline
    bus.register("otc.rfq.accept", h_rfq_accept)
    bus.register("otc.rfq.decline", h_rfq_decline)
    bus.register("otc.rfq.dealer", h_rfq_dealer)
    bus.register("otc.confirm", h_confirm)


__all__ = ["client_ref_of", "contract_by_client_ref", "open_rfq_by_client_ref", "ensure_client_ref_column", "CLIENT_REF_SCHEMA", "linked_deals",
           "confirmation_of", "FIXING_SERIES", "vm_first_date",
           "rfq_rows", "rfq_view", "rfq_detail", "rfq_keys", "rfq_describe", "h_rfq_request", "h_rfq_accept", "h_rfq_decline", "h_rfq_dealer",
           "confirm_keys", "confirm_date", "h_confirm", "confirm_label", "mark_new_contract", "contract_timeline", "derivatives_view", "first_notice",
           "preview_blocker", "register_routes", "add_jobs", "RFQ_SCHEMA", "RFQ_TTL_S", "QUOTE_LABEL", "PENDING_CONFIRM", "SIM_CONFIRM_LABEL",
           "h_book_as_of", "parse_as_of", "asof_terms_mismatch", "asof_context", "fund_csa", "lifecycle_business_date", "dated_curve", "curve_spread",
           "curve_spread_text", "source_of", "MARK_INTRADAY", "MARK_LAST_VM", "ASOF_CONFIRM_LABEL", "bind", "ensure_schema", "has_columns", "position_fields", "C10_SCHEMA", "C10_COLUMNS", "preview", "mark", "dv01_c", "accrual_lines_swap", "accrue_night", "accrue_leg", "contract_view", "ContractView", "contracts_view", "next_events",
           "vm_keys", "fix_keys", "pay_keys", "register", "parse_irs_terms", "IrsTerms", "Deal", "LegSpec", "irs_leg_specs", "leg_specs", "leg_coupon_c",
           "close_contract", "NotBuilt", "period_schedule", "effective_date", "modified_following", "add_months", "days_30360", "coupon_c", "compound_step",
           "compounded_c", "par_annuity", "model_mark_c", "dv01_from", "curve_as_of", "fixing_for", "h_book", "h_terminate", "h_vm", "h_fix", "h_pay",
           "RATE_Q", "SWAP_IM_BP", "CONVENTIONS", "MODEL", "COMPOUND_METHOD", "TERM_METHOD"]
