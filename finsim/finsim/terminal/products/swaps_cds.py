"""PB OTC credit default swap INDEX (CDX.NA.IG / CDX.NA.HY 5Y) with the prime broker - the CDX delegate kind of
``products/swaps.py`` (the owner 2026-10-21: "Buy CDX.NA.IG 5Y protection $3.000M notional", "Buy CDX.NA.HY 5Y protection $4.000M
notional"; BUILD2_PLAN C1 / C4 / C13 / C21 / C22 patterns).

Bound as ``services.swaps_cds`` (server.OPTIONAL_MODULES); the contracts are ``otc_contracts`` rows of kind ``CDX`` booked,
terminated and margined through WP-17's ``otc.book`` / ``otc.terminate`` / SWAP_VM: ``swaps.py`` runs the common gate first (the
kind through ``prime.require_otc_kind``, a prime-broker account, ``books.require_pb_otc`` = the PB/OTC rule AND the ECP floor) and
then dispatches CDX here (``_module_for``).  Nothing is simulated: every number is a MODEL number from OBSERVED inputs (the FRED
proxy spread with its date, the USD curve the swaps use) or an ASSUMED, editable term, each labelled; a missing or stale input
refuses the deal (409) or labels a mark stale - never a default.

Conventions stated here (each verified from a public source on 2026-10-21 unless marked ASSUMED)
- THE INDICES: CDX.NA.IG (125 investment-grade names) and CDX.NA.HY (100 high-yield names), 5-year, the ON-THE-RUN series: the
  series roll on 20 March and 20 September (the business day after when the 20th is not one; ICE contract specification, rule
  1221: "Roll Date September 20 (or the Business Day immediately thereafter) and March 20"); Series 47 rolled on 21 September
  2026 for both indices (S&P DJI: CDX.NA.IG.INDU.47-V1 / CDX.NA.IG.CONS.47-V1 "Effective Date: 21 September 2026"; "CDX
  reference obligations CDX.NA.HY 47", 2026-09-21).  A September series matures on 20 December five years later (ICE rule 1221:
  "Each index series with a Roll Date of September 20 shall have a maturity date of December 20"), a March series on 20 June:
  S47 5Y = 2031-12-20.  The series number after S47 is COUNTED from this roll schedule (S48 from 2027-03-22 ...) - confirm it
  against the index administrator after each roll.  Version 1 / index factor 1.00 is ASSUMED: real credit events among the names are
  not tracked (no free source of ISDA Determinations Committee rulings or auction prices); the owner can SIMULATE one (``cdx.credit_event``,
  module section "the simulated credit event": equal weights, the recovery he enters, the notional and factor reduced).
- COUPON AND RECOVERY: the standard fixed coupon is 100 bp (IG) or 500 bp (HY) running, with an upfront (Markit, "The CDS Big Bang",
  2009: "The coupon will be either 100 basis points or 500 basis points and upfront payments will be exchanged"); the recovery used
  to turn a spread into an upfront is 40 % for IG (the ISDA standard-model value for senior unsecured) and 30 % for HY (the dealer
  convention for CDX.NA.HY: ASSUMED - not confirmed from a primary source in this build).  IG is quoted in spread, HY in price
  (``price = 100 - upfront %``).
- DATES AND ACCRUAL: coupons are paid quarterly on 20 March / June / September / December rolled to the next business day of the
  trading calendar (the New York business-day calendar is ASSUMED equal to the NYSE calendar the terminal has), ACT/360.  FULL FIRST
  COUPON (Markit 2009: "the protection buyer will pay the full quarterly coupon on the coupon payment date ... the protection seller
  has to rebate the accrued up to trade date"): accrual starts on the latest adjusted coupon date on or before the trade date T; the
  ACCRUED REBATE = N x C x (T + 1 - accrual start) / 360 (the accrued through the trade date inclusive) is paid by the protection
  seller to the buyer with the upfront; the buyer then pays the full coupon on the first coupon date.  The last period runs to the
  maturity date INCLUSIVE (accrual end = maturity + 1 day) and pays on the maturity rolled to a business day.
- SETTLEMENT OF THE UPFRONT (the "initial payment" = upfront and accrued rebate, net): T+1 business day - the ICE Clear Credit
  rule for cleared CDX.NA untranched contracts (SEC Release 34-66853, SR-ICC-2012-02, 2012-04-24: "the Initial Payment is to be
  made on the first business day following the trade date"); the uncleared (bilateral) standard was T+3.  CDX.NA.IG / HY 5Y are
  cleared index swaps; here the prime broker is the counterparty of record (one contract, its IM and daily VM in cash, as for the
  swaps) - the clearing-broker relationship is modelled by that contract.
- THE SPREAD (support LIMITED): there is no free CDX quote, so the mid spread = a PROXY: the ICE BofA option-adjusted spread from
  FRED - BAMLC0A0CM (US Corporate Index OAS) for IG, BAMLH0A0HYM2 (US High Yield Index OAS) for HY - daily, with its own date,
  labelled "proxy spread: ICE BofA OAS via FRED, not a CDX dealer quote", plus an editable BASIS adjustment per index (ASSUMED,
  default 0 bp).  FRED publishes a business day's value the next business day: a latest observation more than
  ``PROXY_MAX_AGE_BD`` (3) business days old (or none: FRED in maintenance with no observation kept) gives NO CDX price - the
  preview / booking answer 409 PROXY_STALE / PROXY_UNAVAILABLE with the reason; marks keep their last value labelled stale.
- DEALING: the dealer deals at the mid spread +/- the PB's half-spread (``half_spread_bp`` per index, ASSUMED seeds IG 0.50 bp / HY
  2.00 bp, editable per account) x the PB schedule's ``otc_spread_mult``, against you: a protection buyer at mid + hs, a seller at
  mid - hs; termination at mid -/+ hs (against you).  The cost of dealing is the day-one result.
- THE MODEL (ISDA-style approximation, labelled; not the ISDA Standard Model): a flat hazard rate lambda = S / (1 - R); survival
  Q(t) = exp(-lambda t), t = calendar days from the valuation date / 365; the clean risky annuity RPV01 = sum over the remaining
  coupon periods of accrual fraction x DF(payment date) x (Q(start) + Q(end)) / 2 (the half-period term is the accrual on
  default), the first period counted from the step-in date (valuation date + 1) so the accrued is excluded; DF = the USD curve
  the swaps use (``swaps.dfs_for``: semi-annual discount factors bootstrapped from the Treasury par curve), log-linear in
  between.  Upfront per unit notional (paid by the protection BUYER when positive) U = (S - C) x RPV01(S); a buyer's contract is
  worth (S_mid - C) x RPV01(S_mid) x N, a seller's the opposite.  CS01 = the PV change for +1 bp of the spread (hazard re-solved;
  + for a buyer: bought protection gains when spreads widen); the rates DV01 (``dv01_c``) = the PV change for a +1 bp parallel
  shift of the zero curve.
- BOOKING (``otc.book {account_id, kind: CDX, terms: {index: IG | HY, side: BUY_PROTECTION | SELL_PROTECTION, notional}}``): the
  IM = ``im_pct`` x N (ASSUMED seeds IG 2.00 % / HY 6.00 %, editable per account) posted ``Dr 1330 / Cr 1010`` after
  ``swaps.fund_commitment`` (PO-07); ONE initial-payment entry ``otc:{contract}:upfront``: the upfront the holder pays is the
  contract's COST BASIS (FINSIM1 C11 / PORTFOLIO-08: ``Dr 1335`` paid / ``Cr 2345`` received, per contract; released into the swap
  result 4450 at termination or maturity - never a day-one realised gain), the accrued rebate is the pre-trade part of the full
  first coupon (buyer:
  ``Cr 2340`` accrued premium payable, received in cash; seller: ``Dr 1225`` accrued premium receivable, paid in cash), and the
  net cash of the two is a settlement row (``1200`` receivable / ``2100`` payable, leg UPFRONT) that SETTLE_DUE settles on T+1
  with the usual Treasury sweep; the premium leg's accrual state starts at the accrual start with the rebate as its cumulative,
  so the period's accruals add up to the full first coupon exactly.  The contract's mark (mid) is the NAV overlay at once; mark
  minus the upfront = the cost of dealing.  A deal whose IM plus a payable initial payment cannot be funded is refused first.
- PREMIUM LEG: the ACCRUE_DAILY kinds ``swappay`` (buyer) / ``swaprcv`` (seller) through ``swaps.accrue_leg`` (``leg_specs``: a FIXED
  leg at C, ACT/360, the coupon periods) - ``Dr 4453 / Cr 2340`` (buyer) or ``Dr 1225 / Cr 4453`` (seller) each night (4453 = swap
  coupons and CDS premium, a financing GL), the last night
  of a period trued up to the coupon (the other kind is marked accrued through the last night at booking, so ACCRUE_DAILY never
  asks for a leg the contract does not have).  CDX_COUPON (C13; 00:45 ET on each coupon payment date, key ``{contract}:{date}``) pays the
  coupon ``C x N x days / 360``: buyer ``Dr 2340 / Cr 1010`` (``execution.fund`` with the overdraft rule), seller ``Dr 1010 / Cr
  1225``; recorded in ``otc_resets`` (kind PAY); accruals not yet through the period's last night -> SKIPPED ACCRUALS_PENDING
  (retried).  The last payment matures the contract (VM unwound, IM released: ``swaps.close_contract``).
- VARIATION MARGIN (SWAP_VM, 17:05 ET, swaps.py): the contract is valued at the close of the last business day b on or before the
  VM date with the proxy observation OF b (``mark(..., valuation_date)``): not published yet -> CLOSE_PENDING (retried; FRED
  publishes the next morning), a later observation but none of b (a bond-market holiday) -> CAUGHT_UP; the curve dated after the
  VM date -> CAUGHT_UP (swaps.py).  The change is exchanged in cash under the CSA like every swap.
- TERMINATION (``otc.terminate``, owner): every night owed through the termination date (inclusive: the unwind's accrued runs
  through the trade date like the rebate) is accrued first, then ``swaps.close_contract`` settles the value at mid -/+ hs, unwinds
  the VM, settles the accrued premium and releases the IM.  Inputs observed now are required (409 otherwise).
- AS-OF (FINSIM1 C10): ``book_as_of`` books a CDX the owner dealt on a past date at his dealt spread exactly (``terms.dealt_spread_bp``,
  D10): the proxy is the observation on or before the trade date (``rates.as_of_le``) plus the account basis, the curve that of the
  trade date (``CurveStore.for_date``), the upfront and rebate those of the trade date, the initial payment due T+1 from it (a
  settlement row with ``ref_type`` otc / ``ref_id`` the contract).  A basis that would not deal the payload's spread (more than 0.01
  bp away) is 409 ASOF_BASIS_NOT_CALIBRATED: set it with ``cdx.terms`` first, so no day-one basis gain or loss is ever booked.
  A VM of a past date is discounted on that date's curve.  CDX_COUPON payments are funded on their date with CSA netting (C21).
- EDITABLE ASSUMPTIONS (``cdx.terms``, light; one ``SETTINGS_CHANGED`` event with before / after): per PB account and index the
  basis adjustment (bp, -500 .. 500), the dealer half-spread (bp, 0 .. 100) and the IM (fraction, 0 .. 0.50); stored in the
  ``cdx_terms`` table this module creates (C4 ``ensure_schema_ext``).  Past postings are never restated.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Callable, Dict, List, Optional, Tuple

from .. import prime, tables
from .. import marks as _marks
from ..books import account_row, eligibility as _eligibility, split_account_id
from ..clock import epoch_of, ny_of
from ..commands import ConflictError, RejectError, TxContext
from ..db import dumps, next_id
from ..ledger import Entry, balance, cr, dr
from ..quotes import MarkView
from ..units import fmt_money, from_c, qprice, to_c
from . import swaps

KIND = "CDX"
SOURCE = "swaps_cds"
JOB_COUPON = "CDX_COUPON"
COUPON_ET = "00:45"
TENOR = "5Y"
ANCHOR_SERIES = 47
ANCHOR_ROLL = date(2026, 9, 21)                 # S47: 20 September 2026 was a Sunday -> rolled on Monday 21 September (S&P DJI)
ROLL_MONTHS = (3, 9)
IMM_MONTHS = (3, 6, 9, 12)
PROXY_MAX_AGE_BD = 3                            # FRED publishes a business day's OAS the next business day; older than 3 = stale
UPFRONT_SETTLE_BD = 1                           # T+1: ICE Clear Credit initial payment (SEC Rel. 34-66853); bilateral standard T+3
MIN_NOTIONAL_C = 100_000_00
MAX_NOTIONAL_C = 1_000_000_000_00
ZERO, ONE, HALF = Decimal(0), Decimal(1), Decimal("0.5")
PCT = Decimal(100)
BP = Decimal("0.0001")
BP_Q = Decimal("0.0001")                         # spreads in bp to 0.0001 bp (ICE: "the price quotation convention shall be .0001 basis point")
RPV_Q = Decimal("0.0000000001")
PX4 = Decimal("0.0001")
D360, D365 = Decimal(360), Decimal(365)
PROXY_LABEL = "proxy spread: ICE BofA OAS via FRED, not a CDX dealer quote"
MODEL = ("ISDA-style approximation: flat hazard lambda = S / (1 - R), Q(t) = exp(-lambda t); RPV01 = sum accrual x DF x (Q(start) + Q(end)) / 2"
         " over the remaining coupon periods from the step-in date; upfront = (S - C) x RPV01; DF from the USD curve the swaps use")
CONVENTIONS = ("CDX_PROXY", "CDX_DEAL", "CDX_COUPON", "SWAP_VM", "ELIGIBILITY")
SIDES = ("BUY_PROTECTION", "SELL_PROTECTION")

#: the two indices (verified conventions; the recovery of HY and the seeds of the editable terms are ASSUMED, labelled)
INDEX: Dict[str, dict] = {
    "IG": {"key": "IG", "name": "CDX.NA.IG", "long_name": "Markit CDX North America Investment Grade", "names": 125, "coupon_bp": Decimal(100),
           "recovery": Decimal("0.40"), "recovery_note": "40 % - the ISDA standard-model recovery for senior unsecured (market convention)",
           "fred": "BAMLC0A0CM", "fred_name": "ICE BofA US Corporate Index Option-Adjusted Spread", "quoted": "spread",
           "seed": {"basis_bp": Decimal(0), "half_spread_bp": Decimal("0.50"), "im_pct": Decimal("0.02")}},
    "HY": {"key": "HY", "name": "CDX.NA.HY", "long_name": "Markit CDX North America High Yield", "names": 100, "coupon_bp": Decimal(500),
           "recovery": Decimal("0.30"), "recovery_note": "30 % - the dealer convention for CDX.NA.HY (assumed: not confirmed from a primary source here)",
           "fred": "BAMLH0A0HYM2", "fred_name": "ICE BofA US High Yield Index Option-Adjusted Spread", "quoted": "price",
           "seed": {"basis_bp": Decimal(0), "half_spread_bp": Decimal("2.00"), "im_pct": Decimal("0.06")}},
}
#: the editable assumptions per index (``cdx.terms``): stored form, label, unit, range
TERM_SPECS = {"basis_bp": {"label": "basis adjustment to the proxy", "unit": "bp", "min": Decimal(-500), "max": Decimal(500),
                           "hint": "added to the ICE BofA OAS proxy to approximate the CDX spread (assumed; 0 = the proxy as published)"},
              "half_spread_bp": {"label": "dealer half-spread", "unit": "bp", "min": Decimal(0), "max": Decimal(100),
                                 "hint": "you deal at the mid spread +/- this (x the PB's OTC spread multiplier), against you (assumed)"},
              "im_pct": {"label": "initial margin", "unit": "pct", "min": Decimal(0), "max": Decimal("0.50"),
                         "hint": "a fraction of the notional posted to the PB for the contract's life (assumed)"}}
SCHEMA = ["CREATE TABLE IF NOT EXISTS cdx_terms (account_id TEXT PRIMARY KEY, terms_json TEXT NOT NULL, updated_ts INTEGER NOT NULL, command_id INTEGER)"]

tables.add_conventions({
    "CDX_PROXY": ("CDX index spreads are not free: the mid spread is a PROXY - the ICE BofA option-adjusted spread from FRED (US Corporate for IG,"
                  " US High Yield for HY) with its date, plus an assumed basis adjustment; stale beyond three business days, no CDX price is given."),
    "CDX_DEAL": ("CDX index swaps deal at the proxy mid spread plus or minus the dealer's half-spread against you; the upfront = (spread - coupon) x"
                 " the risky annuity (flat hazard, the swaps' USD curve), with the accrued rebate, settles the next business day; full first coupon."),
    "CDX_COUPON": ("A CDX protection buyer pays the fixed coupon (100 bp IG, 500 bp HY) quarterly on the 20th of March, June, September and December"
                   " (next business day), ACT/360; the seller receives it.  Daily variation margin on the model value under the CSA."),
})


class NotFound(RejectError):
    http = 404


# ---- small helpers -----------------------------------------------------------------------------------------------------------------
def _date(x) -> date:
    return x if isinstance(x, date) else date.fromisoformat(str(x)[:10])


def _terms(row: dict) -> dict:
    try:
        return json.loads(row.get("terms_json") or "{}")
    except ValueError:
        return {}


def _bp_text(x) -> str:
    return f"{Decimal(x).quantize(PX4)} bp"


def _pct_text(fraction, places: str = "0.01") -> str:
    return f"{(Decimal(fraction) * PCT).quantize(Decimal(places))} %"


def _sign(side: str) -> int:
    """+1 for a protection BUYER (short credit: gains when spreads widen), -1 for a seller."""
    return 1 if side == "BUY_PROTECTION" else -1


def _index(key) -> dict:
    k = str(key or "").strip().upper().replace("CDX.NA.", "").replace("CDX", "").strip(" .")
    spec = INDEX.get(k)
    if spec is None:
        raise RejectError("TERMS", f"index must be IG (CDX.NA.IG) or HY (CDX.NA.HY), got {key!r}", {"field": "index", "indices": list(INDEX)})
    return spec


# ---- the series, the dates and the schedule ---------------------------------------------------------------------------------------
def _roll_date(cal, y: int, m: int) -> date:
    return cal.roll_following(date(y, m, 20))


def on_the_run(cal, d: date) -> dict:
    """The on-the-run series on date ``d``: ``{series, roll_date, maturity, next_roll}`` counted from S47 (rolled 2026-09-21) along
    the 20 March / 20 September roll schedule (module docstring THE INDICES)."""
    series, roll = ANCHOR_SERIES, ANCHOR_ROLL
    y, m = ANCHOR_ROLL.year, ANCHOR_ROLL.month
    if d >= ANCHOR_ROLL:
        while True:
            ny_, nm = (y, 9) if m == 3 else (y + 1, 3)
            r = _roll_date(cal, ny_, nm)
            if r > d:
                nxt = r
                break
            series, roll, y, m = series + 1, r, ny_, nm
    else:
        nxt = ANCHOR_ROLL
        while roll > d:
            py, pm = (y, 3) if m == 9 else (y - 1, 9)
            nxt = roll
            series, roll, y, m = series - 1, _roll_date(cal, py, pm), py, pm
    maturity = date(roll.year + 5, 12 if roll.month == 9 else 6, 20)
    return {"series": series, "roll_date": roll, "maturity": maturity, "next_roll": nxt, "tenor": TENOR,
            "source": "S47 rolled 2026-09-21 (S&P DJI); later series counted from the 20 March / 20 September roll schedule"}


def _imm_dates(after: date, upto: date) -> List[date]:
    """The unadjusted 20 March / June / September / December dates in (after, upto]."""
    out, y = [], after.year
    while True:
        for m in IMM_MONTHS:
            d = date(y, m, 20)
            if after < d <= upto:
                out.append(d)
        if date(y, 12, 20) >= upto:
            return out
        y += 1


def accrual_start(cal, trade_date: date) -> Tuple[date, date]:
    """``(unadjusted IMM date, adjusted accrual start)``: the latest coupon date (rolled following) on or before the trade date."""
    y, cands = trade_date.year, []
    for yy in (y - 1, y):
        for m in IMM_MONTHS:
            x = date(yy, m, 20)
            a = cal.roll_following(x)
            if a <= trade_date:
                cands.append((x, a))
    return max(cands, key=lambda p: p[0])


def coupon_periods(cal, trade_date: date, maturity: date) -> List[Tuple[date, date, date]]:
    """``[(accrual start, accrual end, payment date)]`` from the accrual start of ``trade_date`` to the maturity: each end / payment is
    the next coupon date rolled following; the last period runs to the maturity INCLUSIVE (end = maturity + 1) and pays on the
    maturity rolled following (module docstring DATES AND ACCRUAL)."""
    x0, s = accrual_start(cal, trade_date)
    out: List[Tuple[date, date, date]] = []
    for x in _imm_dates(x0, maturity):
        if x == maturity:
            out.append((s, maturity + timedelta(days=1), cal.roll_following(maturity)))
            break
        e = cal.roll_following(x)
        out.append((s, e, e))
        s = e
    return out


def _periods(terms: dict) -> List[Tuple[date, date, date]]:
    return [(_date(a), _date(b), _date(c)) for a, b, c in (terms.get("fixed_periods") or [])]


# ---- the model (pure, Decimal) ------------------------------------------------------------------------------------------------------
def hazard(spread: Decimal, recovery: Decimal) -> Decimal:
    """lambda = S / (1 - R) (the credit triangle; S a fraction: 0.0078 = 78 bp)."""
    return Decimal(spread) / (ONE - Decimal(recovery))


def survival(lam: Decimal, t: Decimal) -> Decimal:
    return (-Decimal(lam) * Decimal(t)).exp()


def df_function(nodes: Dict[Decimal, Decimal], shift_bp=None) -> Callable[[Decimal], Decimal]:
    """DF(t) log-linear between the curve's nodes (DF(0) = 1), the last segment's forward rate beyond the last node; ``shift_bp``
    shifts every zero rate in parallel (DF x e^(-shift t), the rates DV01)."""
    pts = [(ZERO, ZERO)] + sorted((Decimal(str(t)), Decimal(str(v)).ln()) for t, v in nodes.items())
    shift = ZERO if shift_bp in (None, 0) else Decimal(str(shift_bp)) * BP

    def df(t) -> Decimal:
        t = Decimal(t)
        if t <= 0:
            return ONE
        for (t0, l0), (t1, l1) in zip(pts, pts[1:]):
            if t <= t1:
                w = (t - t0) / (t1 - t0)
                return (l0 + (l1 - l0) * w - shift * t).exp()
        (t0, l0), (t1, l1) = pts[-2], pts[-1]
        return (l1 + (l1 - l0) / (t1 - t0) * (t - t1) - shift * t).exp()
    return df


def rpv01(periods, valuation: date, lam: Decimal, df: Callable[[Decimal], Decimal]) -> Tuple[Decimal, List[dict]]:
    """The clean risky annuity per unit notional at ``valuation`` (module docstring THE MODEL): periods whose accrual end is after
    the step-in date (valuation + 1); the first counted from the step-in.  Returns ``(RPV01, rows)``."""
    step_in = valuation + timedelta(days=1)
    total, rows = ZERO, []
    for s, e, p in periods:
        if e <= step_in:
            continue
        a = max(s, step_in)
        frac = Decimal((e - a).days) / D360
        t_a, t_e, t_p = (Decimal((a - valuation).days) / D365, Decimal((e - valuation).days) / D365, Decimal((p - valuation).days) / D365)
        q_a, q_e, d_p = survival(lam, t_a), survival(lam, t_e), df(t_p)
        term = frac * d_p * (q_a + q_e) * HALF
        total += term
        rows.append({"start": a.isoformat(), "end": e.isoformat(), "pay": p.isoformat(), "days": (e - a).days, "accrual": str(frac.quantize(RPV_Q)),
                     "df": str(d_p.quantize(RPV_Q)), "q_start": str(q_a.quantize(RPV_Q)), "q_end": str(q_e.quantize(RPV_Q)), "term": str(term.quantize(RPV_Q))})
    return total, rows


def price(spread_bp, coupon_bp, recovery, periods, valuation: date, df) -> dict:
    """Per unit notional at a spread (bp): ``{lambda, rpv01, upfront}`` with upfront = (S - C) x RPV01(S), paid by the BUYER when
    positive; ``price = 100 - 100 x upfront``."""
    s = Decimal(str(spread_bp)) * BP
    c = Decimal(str(coupon_bp)) * BP
    lam = hazard(s, recovery)
    a, rows = rpv01(periods, valuation, lam, df)
    up = (s - c) * a
    return {"spread_bp": Decimal(str(spread_bp)), "lambda": lam, "rpv01": a, "upfront": up, "price": PCT - PCT * up, "rows": rows}


def upfront_c(notional_c: int, spread_bp, coupon_bp, rpv: Decimal) -> int:
    """The upfront in cents a protection BUYER pays (negative: receives) for a given spread, coupon and RPV01: to_c(N x (S - C) x RPV01)."""
    return to_c(from_c(int(notional_c)) * (Decimal(str(spread_bp)) - Decimal(str(coupon_bp))) * BP * Decimal(rpv))


def value_c(side: str, notional_c: int, spread_bp, coupon_bp, recovery, periods, valuation: date, df) -> Tuple[int, dict]:
    """The contract's value to its holder in cents (a buyer: N x (S - C) x RPV01(S); a seller the opposite) and the pricing dict."""
    pr = price(spread_bp, coupon_bp, recovery, periods, valuation, df)
    return _sign(side) * upfront_c(notional_c, spread_bp, coupon_bp, pr["rpv01"]), pr


def cs01_from(side: str, notional_c: int, spread_bp, coupon_bp, recovery, periods, valuation: date, df) -> int:
    """PV change for +1 bp of the spread (hazard re-solved), signed for the holder."""
    up, _ = value_c(side, notional_c, Decimal(str(spread_bp)) + ONE, coupon_bp, recovery, periods, valuation, df)
    base, _ = value_c(side, notional_c, spread_bp, coupon_bp, recovery, periods, valuation, df)
    return up - base


def rebate_c(notional_c: int, coupon_bp, days: int) -> int:
    """The accrued rebate N x C x days / 360 (days = trade date + 1 - accrual start)."""
    return to_c(from_c(int(notional_c)) * Decimal(str(coupon_bp)) * BP * Decimal(int(days)) / D360)


def coupon_amount_c(notional_c: int, coupon_bp, s: date, e: date) -> int:
    """One period's coupon N x C x (e - s) / 360 (ACT/360) - the amount ``swaps.coupon_c`` trues the accruals up to."""
    return swaps.coupon_c(int(notional_c), Decimal(str(coupon_bp)) * BP, s, e, "ACT/360")


# ---- inputs: the curve, the proxy, the editable terms ---------------------------------------------------------------------------------
def _curve(services, periods, valuation: date, on: Optional[date] = None) -> Tuple[Dict[Decimal, Decimal], dict]:
    """The swaps' USD discount factors far enough for the last payment (``swaps.dfs_for``; 409 NO_CURVE without a curve); ``on`` (C12):
    the curve OF that date (a past valuation date or an as-of trade date), never today's."""
    last = max((p for _s, _e, p in periods), default=valuation)
    years = Decimal(max(1, (last - valuation).days)) / D365
    n = int((years * 2).to_integral_value(rounding="ROUND_CEILING")) + 1
    return swaps.dfs_for(services, max(2, n), on=on)


def proxy(services, index_key: str, today: date, on: Optional[date] = None, le: Optional[date] = None) -> dict:
    """The proxy spread of an index: the latest ICE BofA OAS observation from FRED (``on``: the observation OF that date, for a
    VM; ``le``: the latest on or before that date, for an as-of booking), with its date, age in business days and source; ``ok`` False
    with ``code`` / ``reason`` when there is none or it is stale."""
    spec = _index(index_key)
    sid = spec["fred"]
    rates = getattr(services, "rates", None)
    fred = {}
    try:
        fred = rates.fred_status() if rates is not None and hasattr(rates, "fred_status") else {}
    except Exception:   # noqa: BLE001 - informational
        fred = {}
    base = {"index": spec["key"], "series_id": sid, "series_name": spec["fred_name"], "label": PROXY_LABEL, "support": "LIMITED", "unit": "percent (FRED) -> bp x 100",
            "fred_maintenance": bool(fred.get("maintenance")), "fred_note": fred.get("note") or ""}
    if rates is None or not hasattr(rates, "latest"):
        return dict(base, ok=False, code="PROXY_UNAVAILABLE", reason="no rate store is bound: the FRED proxy spread is unknown and never defaulted")
    latest = rates.latest(sid)
    if on is not None:
        got = rates.as_of_le(sid, on) if hasattr(rates, "as_of_le") else None
        if got is None or got[0] != on:
            later = latest is not None and latest[0] > on
            return dict(base, ok=False, code="CAUGHT_UP" if later else "CLOSE_PENDING", close_date=on.isoformat(),
                        latest_as_of=None if latest is None else latest[0].isoformat(),
                        reason=(f"ICE BofA {spec['key']} OAS has no value for {on.isoformat()} (FRED skipped that date) and a later one"
                                f" ({latest[0].isoformat()})" if later
                                else f"ICE BofA {spec['key']} OAS for {on.isoformat()} not published yet (FRED posts a business day's value the next morning)"))
    elif le is not None:
        got = rates.as_of_le(sid, le) if hasattr(rates, "as_of_le") else None
    else:
        got = latest
    if got is None:
        why = "FRED unavailable (maintenance) and no observation kept" if base["fred_maintenance"] else "no observation received from FRED yet"
        return dict(base, ok=False, code="PROXY_UNAVAILABLE", reason=f"no CDX price: {spec['fred_name']} ({sid}): {why}")
    obs, pct = got
    age = services.cal.business_days_between(obs, today) if getattr(services, "cal", None) is not None else (today - obs).days
    out = dict(base, value_pct=str(qprice(pct)), spread_bp=(Decimal(pct) * PCT).quantize(BP_Q), as_of=obs.isoformat(), age_bd=age,
               source=(rates.source(sid) if hasattr(rates, "source") else None) or "FRED")
    if on is None and age > PROXY_MAX_AGE_BD:
        return dict(out, ok=False, code="PROXY_STALE",
                    reason=(f"no CDX price: the latest {sid} observation is dated {obs.isoformat()}, {age} business days old (more than {PROXY_MAX_AGE_BD};"
                            " FRED publishes a business day's value the next morning)" + (" - FRED unavailable (maintenance)" if base["fred_maintenance"] else "")))
    return dict(out, ok=True, code=None, reason=None)


def _stored_terms(conn: sqlite3.Connection, account_id: str) -> Tuple[dict, Optional[int]]:
    try:
        r = conn.execute("SELECT terms_json, updated_ts FROM cdx_terms WHERE account_id=?", (account_id,)).fetchone()
    except sqlite3.OperationalError:            # the table is created at bind; a runtime without it uses the seeds
        r = None
    if r is None:
        return {}, None
    try:
        data = json.loads(r["terms_json"] or "{}")
    except ValueError:
        data = {}
    return (data if isinstance(data, dict) else {}), int(r["updated_ts"] or 0)


def terms_for(conn: sqlite3.Connection, account_id: str, index_key: str) -> dict:
    """The account's editable CDX assumptions for one index (``basis_bp``, ``half_spread_bp``, ``im_pct`` as Decimals) - the stored
    values, else the seeds - with ``source`` ("seed (assumed)" | "as edited (assumed)")."""
    spec = _index(index_key)
    data, ts = _stored_terms(conn, account_id)
    mine = data.get(spec["key"]) if isinstance(data.get(spec["key"]), dict) else {}
    out = {}
    for k, seed in spec["seed"].items():
        v = mine.get(k)
        try:
            out[k] = Decimal(str(v)) if v not in (None, "") else seed
        except (InvalidOperation, ValueError):
            out[k] = seed
    out["source"] = "as edited (assumed)" if mine else "seed (assumed)"
    out["updated_ts"] = ts if mine else None
    return out


def _schedule(conn, acct: dict):
    return prime.read_schedule(conn, acct)


# ---- terms ------------------------------------------------------------------------------------------------------------------------
def parse_terms(terms: dict) -> dict:
    """``{index, side, notional | notional_c[, tenor, series]}`` -> validated terms (400 TERMS naming the field)."""
    terms = dict(terms or {})
    spec = _index(terms.get("index") or terms.get("index_key"))
    side = str(terms.get("side") or "").strip().upper().replace(" ", "_")
    side = {"BUY": "BUY_PROTECTION", "SELL": "SELL_PROTECTION", "BUY_PROT": "BUY_PROTECTION", "SELL_PROT": "SELL_PROTECTION"}.get(side, side)
    if side not in SIDES:
        raise RejectError("TERMS", "side must be BUY_PROTECTION (pay the coupon, short credit) or SELL_PROTECTION (receive it, long credit)", {"field": "side"})
    raw_c, raw = terms.get("notional_c"), terms.get("notional")
    if isinstance(raw_c, float) or isinstance(raw, float):
        raise RejectError("TERMS", "notional must be a decimal string or integer cents, not a float", {"field": "notional"})
    try:
        if raw_c not in (None, "") and not isinstance(raw_c, bool):
            n = int(Decimal(str(raw_c)))
        elif raw not in (None, ""):
            s = str(raw).strip().replace(",", "")
            d = Decimal(s)
            if d != d.quantize(Decimal("0.01")):
                raise RejectError("TERMS", f"notional {raw!r} has more than 2 decimals", {"field": "notional"})
            n = to_c(d)
        else:
            raise RejectError("TERMS", "notional is required (USD, e.g. 3000000.00)", {"field": "notional"})
    except (InvalidOperation, ValueError) as e:
        raise RejectError("TERMS", f"notional is not a number: {raw if raw is not None else raw_c!r}", {"field": "notional"}) from e
    if n < MIN_NOTIONAL_C or n > MAX_NOTIONAL_C:
        raise RejectError("TERMS", f"notional must be between {fmt_money(MIN_NOTIONAL_C)} and {fmt_money(MAX_NOTIONAL_C)} USD", {"field": "notional"})
    tenor = str(terms.get("tenor") or TENOR).strip().upper()
    if tenor != TENOR:
        raise RejectError("TERMS", f"only the 5Y on-the-run index is offered in this build, got {tenor!r}", {"field": "tenor"})
    series = terms.get("series")
    if series not in (None, ""):
        try:
            series = int(str(series).strip().upper().lstrip("S"))
        except ValueError as e:
            raise RejectError("TERMS", f"series must be a number, got {terms.get('series')!r}", {"field": "series"}) from e
    else:
        series = None
    return {"index": spec["key"], "side": side, "notional_c": n, "tenor": tenor, "series": series}


# ---- the deal (preview and booking share it) ---------------------------------------------------------------------------------------
def _blocker(code: str, message: str, **details) -> dict:
    return {"code": code, "message": message, "details": details}


def _deal(services, conn, acct: dict, t: dict, now: int, as_of: Optional[dict] = None, firm_spread_bp=None) -> dict:
    """Everything a CDX preview shows and a booking posts; raises 409 PROXY_UNAVAILABLE / PROXY_STALE / NO_CURVE / SERIES.  ``as_of``
    (C10): the deal of the trade date ``as_of["trade_date"]`` at the payload's dealt spread ``t["dealt_spread_bp"]`` - the proxy on or
    before that date plus the account basis must deal it within 0.01 bp (409 ASOF_BASIS_NOT_CALIBRATED), the curve is that of the
    trade date; a missing input is 409 ASOF_INPUT_MISSING.  ``firm_spread_bp`` (C14): the spread of the dealer's quote the owner accepted
    within its validity, booked as quoted (the mid and the mark stay the model's now)."""
    spec = INDEX[t["index"]]
    cal = services.cal
    today = ny_of(int(now)).date() if as_of is None else as_of["trade_date"]
    otr = on_the_run(cal, today)
    if t.get("series") is not None and t["series"] != otr["series"]:
        if as_of is not None:
            raise ConflictError("ASOF_TERMS_MISMATCH", f"{spec['name']} S{t['series']} was not the on-the-run series on {today.isoformat()} (S{otr['series']});"
                                " nothing was written", {"differences": [{"term": "series", "payload": str(t["series"]), "stored": str(otr["series"])}]})
        raise RejectError("SERIES", f"{spec['name']} S{t['series']} is not the on-the-run series: S{otr['series']} (rolled {otr['roll_date'].isoformat()}) is dealt",
                          {"field": "series", "on_the_run": otr["series"]})
    px = proxy(services, spec["key"], today, le=today if as_of is not None else None)
    if not px["ok"]:
        if as_of is not None:
            raise ConflictError("ASOF_INPUT_MISSING", f"no usable {spec['name']} proxy on {today.isoformat()}: {px['reason']}; nothing was written",
                                dict(_proxy_view(px), input="CDX proxy", date=today.isoformat()))
        raise ConflictError(px["code"], px["reason"], _proxy_view(px))
    mine = terms_for(conn, acct["account_id"], spec["key"])
    sched = _schedule(conn, acct)
    mult = qprice(Decimal(sched.otc_spread_mult))
    hs = (mine["half_spread_bp"] * mult).quantize(BP_Q)
    mid = (px["spread_bp"] + mine["basis_bp"]).quantize(BP_Q)
    if mid <= 0:
        raise ConflictError("PROXY_SPREAD", f"the mid spread {mid} bp (proxy {px['spread_bp']} bp + basis {mine['basis_bp']} bp) is not positive: edit the basis",
                            {"proxy_bp": str(px["spread_bp"]), "basis_bp": str(mine["basis_bp"])})
    side, sign = t["side"], _sign(t["side"])
    deal_bp = (mid + hs) if sign > 0 else (mid - hs)
    if as_of is not None:
        raw = t.get("dealt_spread_bp")
        try:
            dealt = Decimal(str(raw).strip())
        except (InvalidOperation, ValueError, TypeError) as e:
            raise RejectError("TERMS", f"dealt_spread_bp (the owner's dealt spread in bp) is required for an as-of CDX, got {raw!r}", {"field": "dealt_spread_bp"}) from e
        if not dealt.is_finite() or dealt <= 0:
            raise RejectError("TERMS", f"dealt_spread_bp must be a positive spread in bp, got {raw!r}", {"field": "dealt_spread_bp"})
        if abs(deal_bp - dealt) > swaps.ASOF_TOLERANCE_BP:
            need = (dealt - hs - px["spread_bp"]) if sign > 0 else (dealt + hs - px["spread_bp"])
            raise ConflictError("ASOF_BASIS_NOT_CALIBRATED", f"the {spec['name']} basis of {acct['account_id']} ({mine['basis_bp']} bp) deals {deal_bp} bp on"
                                f" {today.isoformat()} (proxy {px['spread_bp']} bp {'+' if sign > 0 else '-'} half-spread {hs} bp), not the dealt {dealt} bp:"
                                f" set the basis to {need.quantize(BP_Q)} bp first (cdx.terms); nothing was written",
                                {"proxy_bp": str(px["spread_bp"]), "basis_bp": str(mine["basis_bp"]), "half_spread_bp": str(hs), "deals_bp": str(deal_bp),
                                 "dealt_bp": str(dealt), "basis_needed_bp": str(need.quantize(BP_Q)), "proxy_as_of": px.get("as_of")})
        deal_bp = dealt.quantize(BP_Q)                  # D10: the owner's dealt spread, booked exactly
    elif firm_spread_bp not in (None, ""):
        deal_bp = Decimal(str(firm_spread_bp)).quantize(BP_Q)       # C14: the accepted quote, as quoted
    if deal_bp <= 0:
        raise ConflictError("PROXY_SPREAD", f"the dealt spread {deal_bp} bp is not positive (mid {mid} bp - half-spread {hs} bp)", {})
    periods = coupon_periods(cal, today, otr["maturity"])
    try:
        dfs, curve_inputs = _curve(services, periods, today, on=today if as_of is not None else None)
    except ConflictError as e:
        if as_of is None:
            raise
        raise ConflictError("ASOF_INPUT_MISSING", f"the CDX cannot be valued as of {today.isoformat()}: {e.message}; nothing was written",
                            dict(e.details or {}, input="UST_PAR curve", date=today.isoformat())) from e
    df = df_function(dfs)
    n = int(t["notional_c"])
    coupon_bp, rec = spec["coupon_bp"], spec["recovery"]
    mark0, pmid = value_c(side, n, mid, coupon_bp, rec, periods, today, df)
    deal_val, pdeal = value_c(side, n, deal_bp, coupon_bp, rec, periods, today, df)
    cs01 = cs01_from(side, n, mid, coupon_bp, rec, periods, today, df)
    dv_up, _ = value_c(side, n, mid, coupon_bp, rec, periods, today, df_function(dfs, 1))
    dv01 = dv_up - mark0
    x0, s0 = accrual_start(cal, today)
    days = (today + timedelta(days=1) - s0).days
    reb = rebate_c(n, coupon_bp, days)
    up_buyer = upfront_c(n, deal_bp, coupon_bp, pdeal["rpv01"])          # what a BUYER pays at the dealt spread (+ pays / - receives)
    pays = deal_val                                                     # what THIS holder pays: buyer up_buyer, seller -up_buyer
    cash_in = -pays + (reb if sign > 0 else -reb)                       # net initial payment to the holder (+ received / - paid)
    settle = cal.add_business_days(today, UPFRONT_SETTLE_BD)
    imp = mine["im_pct"]
    im = to_c(from_c(n) * imp)
    first = periods[0]
    sched_rows = [{"start": s.isoformat(), "end": (e - timedelta(days=1)).isoformat(), "pay_date": p.isoformat(), "days": (e - s).days,
                   "amount_c": coupon_amount_c(n, coupon_bp, s, e), "direction": "you pay" if sign > 0 else "you receive"} for s, e, p in periods]
    blockers = []
    return {"spec": spec, "acct": acct, "otr": otr, "proxy": px, "terms_used": mine, "sched": sched, "mult": mult, "hs": hs, "mid": mid, "deal_bp": deal_bp,
            "side": side, "sign": sign, "notional_c": n, "coupon_bp": coupon_bp, "recovery": rec, "periods": periods, "dfs": dfs, "curve": curve_inputs,
            "mark0_c": mark0, "deal_value_c": deal_val, "pmid": pmid, "pdeal": pdeal, "cs01_c": cs01, "dv01_c": dv01, "accrual_start": s0, "rebate_days": days,
            "rebate_c": reb, "upfront_buyer_c": up_buyer, "pays_c": pays, "cash_in_c": cash_in, "settle_date": settle, "im_pct": imp, "im_c": im,
            "trade_date": today, "step_in": today + timedelta(days=1), "first": first, "schedule": sched_rows, "blockers": blockers,
            "margin_mode": str(sched.swap_margin_mode).upper()}


def _side_text(spec: dict, series: int, side: str) -> str:
    what = f"{spec['name']} S{series} 5Y"
    return (f"buy protection on {what} (short credit)" if side == "BUY_PROTECTION" else f"sell protection on {what} (long credit)")


def _plain(spec: dict, side: str, n: int) -> str:
    if side == "BUY_PROTECTION":
        return (f"Buying protection on USD {fmt_money(n)} of {spec['name']}: you pay the fixed {int(spec['coupon_bp'])} bp coupon quarterly and are paid"
                f" (1 - recovery) on the defaulted share of the {spec['names']} names - an insurance-like SHORT credit position that gains when"
                " credit spreads widen and loses (plus the coupon) when they tighten.")
    return (f"Selling protection on USD {fmt_money(n)} of {spec['name']}: you receive the fixed {int(spec['coupon_bp'])} bp coupon quarterly and pay"
            f" (1 - recovery) on the defaulted share of the {spec['names']} names - a LONG credit position like owning the index's bonds.")


def _preview_dict(services, conn, deal: dict, now: int) -> dict:
    spec, px, mine, sign, n = deal["spec"], deal["proxy"], deal["terms_used"], deal["sign"], deal["notional_c"]
    acct = deal["acct"]
    otr = deal["otr"]
    side_text = _side_text(spec, otr["series"], deal["side"])
    up_pct = deal["pdeal"]["upfront"] * PCT
    mid_pct = deal["pmid"]["upfront"] * PCT
    buyer_pays = deal["upfront_buyer_c"]
    you = "pay" if deal["pays_c"] > 0 else "receive"
    reb_dir = "received by you (the protection buyer)" if sign > 0 else "paid by you (the protection seller)"
    net = deal["cash_in_c"]
    out = {"kind": KIND, "kind_label": swaps.KIND_LABELS.get(KIND, "CDS index (CDX)"), "account_id": acct["account_id"], "broker": prime.broker_of(acct),
           "index": spec["key"], "index_name": spec["name"], "index_long_name": spec["long_name"], "names": spec["names"], "series": otr["series"], "version": 1,
           "tenor": TENOR, "roll_date": otr["roll_date"].isoformat(), "next_roll": otr["next_roll"].isoformat(), "series_source": otr["source"],
           "trade_date": deal["trade_date"].isoformat(), "step_in_date": deal["step_in"].isoformat(), "accrual_start": deal["accrual_start"].isoformat(),
           "maturity_date": otr["maturity"].isoformat(), "last_payment_date": deal["periods"][-1][2].isoformat(),
           "side": deal["side"], "side_text": side_text, "plain": _plain(spec, deal["side"], n), "notional_c": n, "currency": "USD",
           "exposure_c": -n if sign > 0 else n, "exposure_note": "bought protection = short credit (negative); sold protection = long credit",
           "coupon_bp": str(spec["coupon_bp"]), "coupon_pct": _pct_text(spec["coupon_bp"] * BP), "recovery": str(spec["recovery"]),
           "recovery_pct": _pct_text(spec["recovery"], "1"), "recovery_note": spec["recovery_note"], "quoted": spec["quoted"],
           "proxy": {k: (str(v) if isinstance(v, Decimal) else v) for k, v in px.items()},
           "basis_bp": str(mine["basis_bp"]), "basis_note": f"assumed basis adjustment ({mine['source']}); 0 = the proxy as published",
           "mid_spread_bp": str(deal["mid"]), "half_spread_bp": str(mine["half_spread_bp"]), "otc_spread_mult": str(deal["mult"]),
           "half_spread_eff_bp": str(deal["hs"]), "deal_spread_bp": str(deal["deal_bp"]),
           "spread_rule": (f"mid {deal['mid']} bp = proxy {px['spread_bp']} bp ({px['series_id']} {px['value_pct']} % as of {px['as_of']}) + basis {mine['basis_bp']} bp;"
                           f" dealt {deal['deal_bp']} bp = mid {'+' if sign > 0 else '-'} half-spread {mine['half_spread_bp']} bp x {deal['mult']} (against you)"),
           "hazard_mid": str(deal["pmid"]["lambda"].quantize(RPV_Q)), "hazard_deal": str(deal["pdeal"]["lambda"].quantize(RPV_Q)),
           "rpv01_mid": str(deal["pmid"]["rpv01"].quantize(RPV_Q)), "rpv01_deal": str(deal["pdeal"]["rpv01"].quantize(RPV_Q)),
           "survival_to_maturity": str(survival(deal["pmid"]["lambda"], Decimal((otr["maturity"] - deal["trade_date"]).days) / D365).quantize(PX4)),
           "upfront_c": deal["pays_c"], "upfront_buyer_c": buyer_pays, "upfront_pct": str(up_pct.quantize(PX4)), "price": str((PCT - up_pct).quantize(PX4)),
           "mid_upfront_pct": str(mid_pct.quantize(PX4)), "mid_price": str((PCT - mid_pct).quantize(PX4)),
           "upfront_rule": (f"upfront = (dealt spread {deal['deal_bp']} bp - coupon {spec['coupon_bp']} bp) x RPV01 {deal['pdeal']['rpv01'].quantize(PX4)} x notional"
                            f" = {up_pct.quantize(PX4)} % = USD {fmt_money(abs(buyer_pays))} {'paid' if buyer_pays > 0 else 'received'} by the protection buyer;"
                            f" price {(PCT - up_pct).quantize(PX4)} (= 100 - upfront %)"),
           "upfront_direction": f"you {you} USD {fmt_money(abs(deal['pays_c']))}",
           "accrued_rebate": {"days": deal["rebate_days"], "amount_c": deal["rebate_c"], "from": deal["accrual_start"].isoformat(), "through": deal["trade_date"].isoformat(),
                              "direction": reb_dir, "rule": (f"{fmt_money(n)} x {spec['coupon_bp']} bp x {deal['rebate_days']} days ({deal['accrual_start'].isoformat()} to the"
                                                              f" trade date inclusive) / 360 = {fmt_money(deal['rebate_c'])}: the full first coupon convention")},
           "initial_payment": {"amount_c": net, "direction": "received" if net > 0 else ("paid" if net < 0 else "none"), "settle_date": deal["settle_date"].isoformat(),
                               "rule": (f"upfront and accrued rebate net, settled T+{UPFRONT_SETTLE_BD} business day on {deal['settle_date'].isoformat()} (cleared CDX initial"
                                        " payment, ICE Clear Credit rule; the bilateral standard is T+3)")},
           "coupon": {"first_date": deal["first"][2].isoformat(), "first_amount_c": coupon_amount_c(n, spec["coupon_bp"], deal["first"][0], deal["first"][1]),
                      "first_rule": "the FULL first coupon (from the accrual start); the rebate above refunds the part before the trade",
                      "basis": "ACT/360", "dates": "20 Mar / Jun / Sep / Dec, next business day", "count": len(deal["periods"])},
           "schedule": deal["schedule"], "mark0_c": deal["mark0_c"], "cost_of_dealing_c": deal["mark0_c"] - deal["deal_value_c"],
           "value_at_deal_c": deal["deal_value_c"], "cs01_c": deal["cs01_c"], "dv01_c": deal["dv01_c"],
           "cs01_note": "PV change for +1 bp of the CDX spread (a protection buyer gains when spreads widen)",
           "dv01_note": "PV change for a +1 bp parallel shift of the USD zero curve",
           "im_c": deal["im_c"], "im_pct": str(deal["im_pct"]), "im_rule": f"IM = {_pct_text(deal['im_pct'])} of the notional = {fmt_money(deal['im_c'])} ({mine['source']})",
           "margin_mode": deal["margin_mode"], "fees": "none beyond the dealer's half-spread (disclosed)",
           "inputs": dict(deal["curve"], proxy_as_of=px["as_of"], proxy_series=px["series_id"], proxy_pct=px["value_pct"], valuation_date=deal["trade_date"].isoformat(),
                          rows_mid=deal["pmid"]["rows"], model=MODEL),
           "model": MODEL, "blockers": list(deal["blockers"]), "bookable": not deal["blockers"], "conventions": list(CONVENTIONS), "quality": "computed",
           "assumed": ["basis adjustment", "dealer half-spread", "initial margin %", "HY recovery 30 %", "version 1 / factor 1.00", "NY business days = the NYSE calendar"]}
    return out


def _funding(services, conn, deal: dict, snap=None) -> dict:
    """The preview's funding block: the IM now and, when the initial payment is a payable, its amount - checked together (PO-07)."""
    aid = deal["acct"]["account_id"]
    im = int(deal["im_c"])
    pay = max(0, -int(deal["cash_in_c"]))
    what = "IM + initial payment" if (im and pay) else ("initial payment" if pay else "IM")
    f = swaps.funding_preview(services, conn, aid, "USD", im + pay, snap, what=what)
    f["im_c"], f["initial_payment_c"] = im, pay
    if pay:
        f["payment_note"] = f"the initial payment USD {fmt_money(pay)} is paid on {deal['settle_date'].isoformat()} (checked now together with the IM)"
    return f


def preview(services, account_id: str, kind: str, terms: dict, conn=None, now: Optional[int] = None) -> dict:
    """C21 ``otc.preview`` for CDX (swaps.preview ran the gate): the proxy spread with its date and the basis, the dealer's spread, the
    upfront in USD and %, the HY price, the accrued rebate, the coupon schedule, CS01, IM and the funding.  Pure (no writes); raises
    the codes ``otc.book`` raises."""
    k = prime.require_otc_kind(kind)
    if k != KIND:
        raise RejectError("OTC_KIND", f"{k} is not a CDS index")
    with _marks.shared_reader(services.db, conn) as c:
        acct = swaps._pb_account(c, account_id)
        t = parse_terms(terms)
        ts = int(now if now is not None else services.clock.epoch())
        deal = _deal(services, c, acct, t, ts)
        out = _preview_dict(services, c, deal, ts)
        out["funding"] = _funding(services, c, deal)
        if not out["funding"].get("ok", True):
            out["blockers"].append(_blocker("INSUFFICIENT_FUNDS", out["funding"]["note"]))
            out["bookable"] = False
        try:
            out["eligibility"] = _eligibility(services, split_account_id(account_id)[0]).to_dict()
        except Exception:   # noqa: BLE001 - informational; the gate already passed
            out["eligibility"] = None
        try:
            out["relationship"] = prime.relationship(c, account_id).get("agreements_line")
        except Exception:   # noqa: BLE001
            out["relationship"] = None
        out["terms"] = {"index": t["index"], "side": t["side"], "notional_c": t["notional_c"], "tenor": TENOR, "series": deal["otr"]["series"]}
        out["as_of"] = ts
        return out


# ---- booking ----------------------------------------------------------------------------------------------------------------------
def _insert_instrument(services, ctx: TxContext, cid: str, name: str, maturity: date, spec: dict) -> None:
    master = services.instruments
    insert = getattr(master, "insert", None) or getattr(master, "_insert")
    insert(ctx.conn, ctx.ts, instrument_id=cid, kind="SWAP", symbol=cid, name=name, currency="USD", multiplier="1", tick="0.01", lot=1, price_scale="1",
           session_group=swaps.SESSION_GROUP, settle_days=0, account_kinds=["PB"], expiry=maturity.isoformat(), spec=spec, support="COMPUTED", status="ACTIVE")
    master.refresh(cid, ctx.conn)


def book(ctx: TxContext, payload: dict) -> dict:
    """C21 ``otc.book`` for CDX (WP-17 checked the kind, the PB account and the eligibility first): re-prices inside the transaction,
    refuses a deal it cannot fund, funds and posts the IM, posts the initial payment (upfront + accrued rebate) with its T+1
    settlement row, seeds the premium leg's accrual state, creates the contract and its SWAP instrument row."""
    services = ctx.services
    conn = ctx.conn
    acct = swaps._pb_account(conn, str(payload.get("account_id") or ""))
    t = parse_terms(payload.get("terms") or {})
    firm = getattr(ctx, "rfq_firm", None) or {}
    deal = _deal(services, conn, acct, t, ctx.ts, firm_spread_bp=firm.get("dealt_spread_bp"))
    return _book(ctx, services, acct, t, deal)


def book_as_of(ctx: TxContext, payload: dict, a: dict) -> dict:
    """C10 as-of booking of a CDX (``swaps.h_book_as_of`` ran the gates and set ``ctx.asof_date`` = the value date): the owner's dealt
    spread ``terms.dealt_spread_bp`` booked exactly at the inputs of the trade date (module docstring AS-OF)."""
    services = ctx.services
    conn = ctx.conn
    acct = swaps._pb_account(conn, str(payload.get("account_id") or ""))
    raw = dict(payload.get("terms") or {})
    t = parse_terms(raw)
    t["dealt_spread_bp"] = raw.get("dealt_spread_bp")
    deal = _deal(services, conn, acct, t, ctx.ts, as_of=a)
    if deal["trade_date"] != a["value_date"]:
        raise ConflictError("ASOF_TERMS_MISMATCH", f"a CDX starts on its trade date {deal['trade_date'].isoformat()}, not the value date {a['value_date'].isoformat()};"
                            " nothing was written", {"differences": [{"term": "value_date", "payload": a["value_date"].isoformat(), "stored": deal["trade_date"].isoformat()}]})
    return _book(ctx, services, acct, t, deal, as_of=a)


def _book(ctx: TxContext, services, acct: dict, t: dict, deal: dict, as_of: Optional[dict] = None) -> dict:
    conn = ctx.conn
    aid = acct["account_id"]
    fund = _funding(services, conn, deal, ctx.marks)
    if not fund.get("ok", True):
        raise ConflictError("INSUFFICIENT_FUNDS", fund["note"], {"funding": fund})
    spec, sign, n, otr = deal["spec"], deal["sign"], deal["notional_c"], deal["otr"]
    broker = prime.broker_of(acct)
    cid = next_id(conn, "cdx")
    side_text = _side_text(spec, otr["series"], deal["side"])
    entries: List[int] = []
    funding = None
    im_c = int(deal["im_c"])
    if im_c > 0:
        idem = f"im:{cid}:open"
        funding = swaps.fund_commitment(ctx, services, aid, "USD", im_c, "otc", idem)
        memo = (f"OTC IM {cid}: {side_text} USD {fmt_money(n)} with {broker}; IM = {_pct_text(deal['im_pct'])} x notional = {fmt_money(im_c)}"
                f" ({deal['terms_used']['source']})")
        entries.append(ctx.post(Entry("OTC_IM", memo, [dr(aid, "1330", "USD", im_c, instrument_id=cid, memo="OTC initial margin"),
                                                       cr(aid, "1010", "USD", im_c, memo="IM paid")], idem, ref_type="otc", ref_id=cid)))
    pays, reb, cash_in = int(deal["pays_c"]), int(deal["rebate_c"]), int(deal["cash_in_c"])
    lines = []
    if pays > 0:                                     # C11 / PORTFOLIO-08: the upfront is the contract's cost basis, released at termination / maturity
        lines.append(dr(aid, "1335", "USD", pays, instrument_id=cid, memo="CDX upfront paid (contract cost)"))
    elif pays < 0:
        lines.append(cr(aid, "2345", "USD", -pays, instrument_id=cid, memo="CDX upfront received (contract cost)"))
    if reb > 0:
        if sign > 0:
            lines.append(cr(aid, "2340", "USD", reb, instrument_id=cid, memo="accrued premium before the trade (part of the full first coupon)"))
        else:
            lines.append(dr(aid, "1225", "USD", reb, instrument_id=cid, memo="accrued premium before the trade (part of the full first coupon)"))
    settle_id = None
    sd = deal["settle_date"].isoformat()
    if cash_in > 0:
        lines.append(dr(aid, "1200", "USD", cash_in, memo=f"CDX initial payment receivable {sd}"))
    elif cash_in < 0:
        lines.append(cr(aid, "2100", "USD", -cash_in, memo=f"CDX initial payment payable {sd}"))
    if lines:
        memo = (f"CDX initial payment {cid}: {side_text} USD {fmt_money(n)}; upfront {(deal['pdeal']['upfront'] * PCT).quantize(PX4)} % ="
                f" {fmt_money(abs(pays))} {'paid' if pays > 0 else 'received'} (dealt {deal['deal_bp']} bp vs coupon {spec['coupon_bp']} bp, RPV01"
                f" {deal['pdeal']['rpv01'].quantize(PX4)}); accrued rebate {deal['rebate_days']} days = {fmt_money(reb)} {'received' if sign > 0 else 'paid'};"
                f" net {fmt_money(abs(cash_in))} {'receivable' if cash_in > 0 else 'payable'} {sd} (T+{UPFRONT_SETTLE_BD}); proxy {deal['proxy']['series_id']}"
                f" {deal['proxy']['value_pct']} % as of {deal['proxy']['as_of']} + basis {deal['terms_used']['basis_bp']} bp; the upfront is the contract's cost,"
                " released to the swap result when the contract ends")
        entries.append(ctx.post(Entry("CDX_UPFRONT", memo, lines, f"otc:{cid}:upfront", ref_type="otc", ref_id=cid)))
    if cash_in:
        settle_id = next_id(conn, "settlement")
        if swaps.has_columns(conn, "settlements", "ref_type", "ref_id"):         # C10 / C15: the settlement names its contract
            conn.execute("INSERT INTO settlements(settle_id, fill_id, fx_deal_id, account_id, leg, currency, amount_c, gl, due_date, status, ref_type, ref_id)"
                         " VALUES (?,?,?,?,?,?,?,?,?,'PENDING','otc',?)", (settle_id, None, None, aid, "UPFRONT", "USD", cash_in, "1200" if cash_in > 0 else "2100", sd, cid))
        else:
            conn.execute("INSERT INTO settlements(settle_id, fill_id, fx_deal_id, account_id, leg, currency, amount_c, gl, due_date, status) VALUES (?,?,?,?,?,?,?,?,?,'PENDING')",
                         (settle_id, None, None, aid, "UPFRONT", "USD", cash_in, "1200" if cash_in > 0 else "2100", sd))
    periods = deal["periods"]
    stored = {"kind": KIND, "index": spec["key"], "index_name": spec["name"], "series": otr["series"], "version": 1, "tenor": TENOR, "side": deal["side"],
              "receiver": sign < 0, "notional_c": n, "coupon_bp": str(spec["coupon_bp"]), "recovery": str(spec["recovery"]), "fred_series": spec["fred"],
              "trade_date": deal["trade_date"].isoformat(), "step_in_date": deal["step_in"].isoformat(), "accrual_start": deal["accrual_start"].isoformat(),
              "maturity_date": otr["maturity"].isoformat(), "roll_date": otr["roll_date"].isoformat(), "fixed_basis": "ACT/360",
              "fixed_periods": [[s.isoformat(), e.isoformat(), p.isoformat()] for s, e, p in periods],
              "proxy_bp": str(deal["proxy"]["spread_bp"]), "proxy_as_of": deal["proxy"]["as_of"], "basis_bp": str(deal["terms_used"]["basis_bp"]),
              "mid_spread_bp": str(deal["mid"]), "half_spread_bp": str(deal["terms_used"]["half_spread_bp"]), "otc_spread_mult": str(deal["mult"]),
              "deal_spread_bp": str(deal["deal_bp"]), "upfront_c": int(deal["pays_c"]), "upfront_buyer_c": int(deal["upfront_buyer_c"]),
              "upfront_pct": str((deal["pdeal"]["upfront"] * PCT).quantize(PX4)), "price": str((PCT - deal["pdeal"]["upfront"] * PCT).quantize(PX4)),
              "rebate_days": deal["rebate_days"], "rebate_c": reb, "initial_payment_c": cash_in, "settle_date": sd, "settle_id": settle_id,
              "im_pct": str(deal["im_pct"]), "mark0_c": int(deal["mark0_c"]), "cs01_0_c": int(deal["cs01_c"]), "rpv01_0": str(deal["pdeal"]["rpv01"].quantize(RPV_Q)),
              "margin_mode": deal["margin_mode"], "dealt_ts": (as_of["exec_ts"] if as_of else ctx.ts), "model": MODEL, "upfront_gl": "1335" if int(deal["pays_c"]) > 0 else "2345"}
    name = f"CDS index: {side_text} USD {fmt_money(n)} to {otr['maturity'].isoformat()} ({broker})"
    _insert_instrument(services, ctx, cid, name, otr["maturity"], {"contract_id": cid, "otc_kind": KIND, "broker": broker, "index": spec["name"], "series": otr["series"],
                                                                   "side": deal["side"], "receiver": sign < 0, "maturity_date": otr["maturity"].isoformat(),
                                                                   "coupon_bp": str(spec["coupon_bp"])})
    inputs = dict(deal["curve"], as_of=ctx.ts, quality="computed", note="the mid model value at the deal", proxy=_proxy_view(deal["proxy"]),
                  mid_spread_bp=str(deal["mid"]), rpv01=str(deal["pmid"]["rpv01"].quantize(RPV_Q)), valuation_date=deal["trade_date"].isoformat(), model=MODEL)
    conn.execute("INSERT INTO otc_contracts(contract_id, command_id, account_id, broker, kind, terms_json, notional_c, currency, start_date, maturity_date, margin_mode, im_c,"
                 " status, last_reset_date, reset_px, mark_c, mark_ts, vm_balance_c, inputs_json, closed_ts) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,'OPEN',NULL,NULL,?,?,0,?,NULL)",
                 (cid, ctx.command_id, aid, broker, KIND, dumps(stored), n, "USD", deal["trade_date"].isoformat(), otr["maturity"].isoformat(), deal["margin_mode"], im_c,
                  int(deal["mark0_c"]), ctx.ts, dumps(inputs)))
    # the premium leg's accrual starts at the accrual start with the rebate as its cumulative: the period sums to the full first coupon
    kind = "swappay" if sign > 0 else "swaprcv"
    conn.execute("INSERT INTO accrual_state(kind, ref_id, segment_start, base_c, rate, day_basis, accrued_through, cum_c) VALUES (?,?,?,?,?,360,?,?)"
                 " ON CONFLICT(kind, ref_id) DO UPDATE SET segment_start=excluded.segment_start, base_c=excluded.base_c, rate=excluded.rate,"
                 " day_basis=excluded.day_basis, accrued_through=excluded.accrued_through, cum_c=excluded.cum_c",
                 (kind, cid, deal["accrual_start"].isoformat(), n, str(qprice(spec["coupon_bp"] * BP)), deal["trade_date"].isoformat(), reb))
    # the other leg kind has nothing to accrue: marked accrued through the last night so ACCRUE_DAILY never asks for it (no no-op keys)
    last_night = (periods[-1][1] - timedelta(days=1)).isoformat()
    conn.execute("INSERT INTO accrual_state(kind, ref_id, segment_start, base_c, rate, day_basis, accrued_through, cum_c) VALUES (?,?,?,0,'0',360,?,0)"
                 " ON CONFLICT(kind, ref_id) DO NOTHING", ("swaprcv" if sign > 0 else "swappay", cid, last_night, last_night))
    ctx.touched_instruments.add((aid, cid))
    ctx.touched_accounts.add(aid)
    ctx.event("OTC_BOOKED", account_id=aid, instrument_id=cid, ref_type="otc", ref_id=cid, kind=KIND, broker=broker, side=deal["side"], side_text=side_text,
              notional_c=n, index=spec["name"], series=otr["series"], deal_spread_bp=str(deal["deal_bp"]), upfront_c=int(deal["pays_c"]), rebate_c=reb,
              initial_payment_c=cash_in, settle_id=settle_id, mark0_c=int(deal["mark0_c"]), im_c=im_c, margin_mode=deal["margin_mode"])
    view = swaps.contract_view(services, cid, conn=conn, detail=True, now_ts=ctx.ts)
    pv = _preview_dict(services, conn, deal, ctx.ts)
    return {"contract": view, "contract_id": cid, "im_transfer": {"amount_c": im_c, "funding": funding.to_dict() if hasattr(funding, "to_dict") else funding},
            "initial_payment": {"amount_c": cash_in, "direction": "received" if cash_in > 0 else ("paid" if cash_in < 0 else "none"), "settle_date": sd,
                                "settle_id": settle_id, "upfront_c": int(deal["pays_c"]), "rebate_c": reb},
            "entries": entries, "preview": pv}


def _proxy_view(px: dict) -> dict:
    return {k: (str(v) if isinstance(v, Decimal) else v) for k, v in px.items() if k not in ("fred_note",)}


# ---- valuation: live and as of a close (SWAP_VM) ------------------------------------------------------------------------------------
def _stale(row: dict, now: int, inputs: dict, note: str) -> MarkView:
    stored = int(row.get("mark_c") or 0)
    return MarkView(row["contract_id"], from_c(stored), "stale", "CDX model (stored mark)", "swap_model", row.get("mark_ts"), now, "model",
                    dict(inputs, qualities=["stale"], note=note), False, note)


def _valuation(services, row: dict, now: int, valuation_date: Optional[date] = None, shift_spread_bp=None, conn=None):
    """``(value_c, inputs)`` of an OPEN contract, or ``(None, {code, reason, vm_outcome?})`` when an input is missing.  Live
    (``valuation_date`` None): today, the latest non-stale proxy; a VM date: the last business day b on or before it, the proxy
    observation OF b (module docstring VARIATION MARGIN)."""
    terms = _terms(row)
    spec = _index(terms.get("index"))
    cal = services.cal
    today = ny_of(int(now)).date()
    if valuation_date is not None:
        b = cal.roll_back(_date(valuation_date))
        px = proxy(services, spec["key"], today, on=b)
        v = b
    else:
        px = proxy(services, spec["key"], today)
        v = today
    if not px["ok"]:
        out = {"code": px["code"], "reason": px["reason"], "proxy": _proxy_view(px)}
        if valuation_date is not None and px["code"] in ("CAUGHT_UP", "CLOSE_PENDING"):
            d = _date(valuation_date)
            out.update(vm_outcome=px["code"], note=px["reason"], close_date=b.isoformat(), quiet=not cal.is_business_day(d),
                       quiet_until=epoch_of(cal.next_business_day(b), "12:00"),
                       waits_for="that day's OAS (never another day's value)")   # the reason already says when FRED publishes (no doubled sentence)
        return None, out
    with _marks.shared_reader(services.db, conn) as c:
        mine = terms_for(c, row["account_id"], spec["key"])
    mid = (px["spread_bp"] + mine["basis_bp"]).quantize(BP_Q)
    if shift_spread_bp not in (None, 0):
        mid = (mid + Decimal(str(shift_spread_bp))).quantize(BP_Q)
    if mid <= 0:
        return None, {"code": "PROXY_SPREAD", "reason": f"the mid spread {mid} bp is not positive (proxy {px['spread_bp']} + basis {mine['basis_bp']})"}
    periods = _periods(terms)
    if not [p for p in periods if p[1] > v + timedelta(days=1)]:
        return 0, {"as_of": int(now), "valuation_date": v.isoformat(), "note": "no remaining coupon period (at maturity)", "quality": "computed"}
    try:
        dfs, curve_inputs = _curve(services, periods, v, on=v if valuation_date is not None else None)     # a past date on its own curve (C12)
    except ConflictError as e:
        return None, {"code": e.code, "reason": e.message}
    side, n = terms.get("side") or "BUY_PROTECTION", int(row["notional_c"])
    val, pr = value_c(side, n, mid, Decimal(terms.get("coupon_bp") or spec["coupon_bp"]), Decimal(terms.get("recovery") or spec["recovery"]), periods, v, df_function(dfs))
    proxy_epoch = epoch_of(_date(px["as_of"]), "17:00")
    inputs = dict(curve_inputs, as_of=int(now), proxy_close_ts=proxy_epoch, valuation_date=v.isoformat(), contract_id=row["contract_id"], quality="computed", model=MODEL,
                  proxy=_proxy_view(px), basis_bp=str(mine["basis_bp"]), mid_spread_bp=str(mid), coupon_bp=terms.get("coupon_bp"), recovery=terms.get("recovery"),
                  hazard=str(pr["lambda"].quantize(RPV_Q)), rpv01=str(pr["rpv01"].quantize(RPV_Q)), upfront_pct=str((pr["upfront"] * PCT).quantize(PX4)),
                  price=str(pr["price"].quantize(PX4)), side=side,
                  # read by swaps.h_vm's memo (named like the IRS inputs): the spread pair and the annuity of a CDS
                  par_rate=f"CDX mid {mid} bp", dealt_rate=f"{terms.get('deal_spread_bp')} bp dealt, coupon {terms.get('coupon_bp')} bp", annuity=f"RPV01 {pr['rpv01'].quantize(PX4)}",
                  note=f"{PROXY_LABEL} ({px['series_id']} {px['value_pct']} % as of {px['as_of']}) + basis {mine['basis_bp']} bp; {MODEL}")
    return val, inputs


def mark(services, contract, at: Optional[int] = None, valuation_date=None, snapshot=None):
    """C21 mark hook: ``(cents, inputs)``; live without a usable proxy / curve -> the stored mark as a stale ``MarkView``; with
    ``valuation_date`` (SWAP_VM) -> ``(None, {vm_outcome: CLOSE_PENDING | CAUGHT_UP, note, quiet})`` when that close is not known, or
    ``(None, {note})`` (NO_MARK, retried) when the curve is missing."""
    row = swaps._contract_dict(services, contract)
    if row is None:
        return None
    now = int(at if at is not None else services.clock.epoch())
    if row.get("status") != "OPEN":
        return 0, {"as_of": now, "note": f"{row.get('status')}: no remaining cash flows", "quality": "computed"}
    val, inputs = _valuation(services, row, now, valuation_date)
    if val is None:
        if valuation_date is not None:
            return None, dict(inputs, note=inputs.get("note") or inputs.get("reason"))
        return _stale(row, now, inputs, f"no current CDX value: {inputs.get('reason')}; the last computed mark, labelled stale")
    return int(val), inputs


def cs01_c(services, contract, now: Optional[int] = None) -> Optional[int]:
    """The contract's CS01 now: the PV change for +1 bp of its spread (signed for the holder); None without a usable proxy / curve."""
    row = swaps._contract_dict(services, contract)
    if row is None or row.get("status") != "OPEN":
        return None if row is None else 0
    ts = int(now if now is not None else services.clock.epoch())
    base, _ = _valuation(services, row, ts)
    up, _ = _valuation(services, row, ts, shift_spread_bp=1)
    return None if base is None or up is None else int(up) - int(base)


def dv01_c(services, contract) -> int:
    """C21 ``dv01_c``: the RATES DV01 - the PV change for a +1 bp parallel shift of the USD zero curve (raises ConflictError when the
    proxy or the curve is missing; 0 when not OPEN)."""
    row = swaps._contract_dict(services, contract)
    if row is None:
        raise RejectError("NOT_FOUND", "unknown contract")
    if row.get("status") != "OPEN":
        return 0
    now = int(services.clock.epoch())
    terms = _terms(row)
    spec = _index(terms.get("index"))
    today = ny_of(now).date()
    px = proxy(services, spec["key"], today)
    if not px["ok"]:
        raise ConflictError(px["code"], px["reason"])
    with _marks.shared_reader(services.db) as c:
        mine = terms_for(c, row["account_id"], spec["key"])
    mid = (px["spread_bp"] + mine["basis_bp"]).quantize(BP_Q)
    periods = _periods(terms)
    dfs, _inputs = _curve(services, periods, today)
    side, n = terms.get("side") or "BUY_PROTECTION", int(row["notional_c"])
    c_bp, rec = Decimal(terms.get("coupon_bp") or spec["coupon_bp"]), Decimal(terms.get("recovery") or spec["recovery"])
    base, _ = value_c(side, n, mid, c_bp, rec, periods, today, df_function(dfs))
    up, _ = value_c(side, n, mid, c_bp, rec, periods, today, df_function(dfs, 1))
    return int(up - base)


# ---- the premium leg (ACCRUE_DAILY through swaps.accrue_leg) -------------------------------------------------------------------------
def leg_specs(services, row: dict) -> Dict[str, swaps.LegSpec]:
    """C21 ``leg_specs``: the premium leg - ``swappay`` for a buyer, ``swaprcv`` for a seller - FIXED at the coupon, ACT/360, the coupon
    periods (accrued nightly by ``swaps.accrue_leg``)."""
    terms = _terms(row)
    buyer = (terms.get("side") or "BUY_PROTECTION") == "BUY_PROTECTION"
    kind = "swappay" if buyer else "swaprcv"
    c = qprice(Decimal(str(terms.get("coupon_bp") or "100")) * BP)
    return {kind: swaps.LegSpec(kind, "premium", row["currency"], int(row["notional_c"]), "ACT/360", tuple(_periods(terms)), "FIXED", c)}


def _catch_up(ctx: TxContext, services, row: dict, through: date) -> List[dict]:
    """Post the premium nights owed through ``through`` (inclusive) before a termination settles them; 409 with the reason otherwise."""
    conn = ctx.conn
    cid = row["contract_id"]
    posted: List[dict] = []
    for kind, spec in leg_specs(services, row).items():
        st = conn.execute("SELECT accrued_through FROM accrual_state WHERE kind=? AND ref_id=?", (kind, cid)).fetchone()
        night = _date(row["start_date"]) if st is None or not st["accrued_through"] else _date(st["accrued_through"]) + timedelta(days=1)
        last_night = spec.periods[-1][1] - timedelta(days=1) if spec.periods else through
        while night <= min(through, last_night):
            try:
                res = swaps.accrue_night(ctx, services, kind, cid, night.isoformat())
            except RejectError as e:
                raise ConflictError(e.code, f"{cid}: the termination settles the premium through {through.isoformat()}, but the night of {night.isoformat()}"
                                    f" cannot accrue: {e.message}", e.details) from e
            if res.get("entry_id") is not None:
                posted.append({"kind": kind, "leg": "premium", "night": night.isoformat(), "amount_c": int(res.get("amount_c") or 0), "entry_id": res["entry_id"]})
            night += timedelta(days=1)
    return posted


# ---- termination -----------------------------------------------------------------------------------------------------------------------
def terminate(ctx: TxContext, payload: dict) -> dict:
    """C21 ``otc.terminate`` for CDX (owner): the premium accrued through today first, then the close-out at the mid spread -/+ the
    half-spread against you (a buyer sells protection back at mid - hs, a seller buys it back at mid + hs)."""
    services = ctx.services
    conn = ctx.conn
    cid = str(payload.get("contract_id") or "")
    row = swaps._open_contract(conn, cid)
    if row["status"] != "OPEN":
        raise ConflictError("NOT_OPEN", f"{cid} is {row['status']}; only an OPEN contract can be terminated", {"contract_id": cid})
    acct = account_row(conn, row["account_id"])
    if acct is None or acct["status"] != "OPEN":
        raise RejectError("ACCOUNT_CLOSED", f"account {row['account_id']} is not open")
    terms = _terms(row)
    spec = _index(terms.get("index"))
    today = ny_of(ctx.ts).date()
    px = proxy(services, spec["key"], today)
    if not px["ok"]:
        raise ConflictError(px["code"], px["reason"], {"contract_id": cid})
    mine = terms_for(conn, row["account_id"], spec["key"])
    mult = qprice(Decimal(_schedule(conn, dict(acct)).otc_spread_mult))
    hs = (mine["half_spread_bp"] * mult).quantize(BP_Q)
    mid = (px["spread_bp"] + mine["basis_bp"]).quantize(BP_Q)
    side = terms.get("side") or "BUY_PROTECTION"
    term_bp = (mid - hs) if side == "BUY_PROTECTION" else (mid + hs)
    if term_bp <= 0:
        raise ConflictError("PROXY_SPREAD", f"the unwind spread {term_bp} bp is not positive", {"contract_id": cid})
    periods = _periods(terms)
    dfs, curve_inputs = _curve(services, periods, today)
    df = df_function(dfs)
    c_bp, rec = Decimal(terms.get("coupon_bp")), Decimal(terms.get("recovery"))
    value, pr = value_c(side, int(row["notional_c"]), term_bp, c_bp, rec, periods, today, df)
    mid_val, _ = value_c(side, int(row["notional_c"]), mid, c_bp, rec, periods, today, df)
    caught = _catch_up(ctx, services, row, today)
    row = swaps._open_contract(conn, cid)
    ref_c = swaps._reference_mark_c(conn, cid)
    inputs = dict(curve_inputs, proxy=_proxy_view(px), basis_bp=str(mine["basis_bp"]), mid_spread_bp=str(mid), half_spread_eff_bp=str(hs), unwind_spread_bp=str(term_bp),
                  rpv01=str(pr["rpv01"].quantize(RPV_Q)), mid_value_c=mid_val, valuation_date=today.isoformat(), model=MODEL)
    memo = (f"CDX termination {cid}: value {fmt_money(value)} at the unwind spread {term_bp} bp (mid {mid} bp {'-' if side == 'BUY_PROTECTION' else '+'} half-spread {hs} bp,"
            f" against you; mid value {fmt_money(mid_val)}), RPV01 {pr['rpv01'].quantize(PX4)}, proxy {px['series_id']} {px['value_pct']} % as of {px['as_of']};"
            f" VM balance {fmt_money(int(row['vm_balance_c']))} unwound; accrued premium settled through {today.isoformat()}"
            + (f" ({len(caught)} night(s) accrued first)" if caught else "") + " (CDX_DEAL)")
    closed = swaps.close_contract(ctx, services, row, "TERMINATED", value, ref_c, f"otc:{cid}:terminate", "OTC_TERMINATE", memo, True, False)
    ctx.event("OTC_TERMINATED", account_id=row["account_id"], instrument_id=cid, ref_type="otc", ref_id=cid, termination_value_c=value, inputs=inputs,
              caught_up=len(caught), **{k: v for k, v in closed.items() if k not in ("funding", "value_c")})
    view = swaps.contract_view(services, cid, conn=conn, detail=True, now_ts=ctx.ts)
    entries = [c["entry_id"] for c in caught] + [e for e in (closed["entry_id"], closed["im_entry_id"]) if e is not None]
    return {"contract": view, "contract_id": cid, "termination_value_c": value, "mid_value_c": mid_val, "cost_of_unwinding_c": mid_val - value, "result_c": closed["result_c"],
            "cost_released_c": closed.get("cost_released_c", 0), "realised_c": closed.get("realised_c"),
            "cash_c": closed["cash_c"], "entries": entries, "im_released_c": closed["im_released_c"], "accrued_receive_c": closed["accrued_receive_c"],
            "accrued_pay_c": closed["accrued_pay_c"], "inputs": inputs, "accrued_through": today.isoformat(), "caught_up": caught, "funding": closed.get("funding")}


# ---- the quarterly coupon (job CDX_COUPON) -------------------------------------------------------------------------------------------
def h_coupon(ctx: TxContext, payload: dict) -> dict:
    """CDX_COUPON for one (contract, payment date) - module docstring PREMIUM LEG.  Idempotent per date; the last one matures."""
    services = ctx.services
    conn = ctx.conn
    cid, ds = swaps._key(payload, "cdx.coupon")
    key = f"{cid}:{ds}"
    idem = f"cdxcpn:{key}"
    row = swaps._open_contract(conn, cid)
    aid, ccy = row["account_id"], row["currency"]
    if row["kind"] != KIND:
        raise RejectError("KIND", f"{cid} is a {row['kind']} contract: cdx.coupon is for CDS index contracts")
    if row["status"] != "OPEN":
        return swaps._done_noop(ctx, JOB_COUPON, key, "NOT_OPEN", f"{cid} is {row['status']}")
    existing = conn.execute("SELECT detail_json FROM otc_resets WHERE contract_id=? AND event_date=? AND kind='PAY'", (cid, ds)).fetchone()
    if existing is not None:
        swaps._job_done(ctx, JOB_COUPON, key, "DONE", "already paid")
        return {"status": "DONE", "already": True, "posted": False, "key": key}
    d = date.fromisoformat(ds)
    terms = _terms(row)
    specs = leg_specs(services, row)
    kind, spec = next(iter(specs.items()))
    period = next((p for p in spec.periods if p[2] == d), None)
    if period is None:
        raise RejectError("KEY_INVALID", f"{ds} is not a coupon payment date of {cid}")
    if ctx.ts < epoch_of(d, COUPON_ET):
        return swaps._skip(ctx, JOB_COUPON, key, "PAY_NOT_YET", f"{cid}: the coupon of {ds} is paid from {COUPON_ET} ET", aid, cid, quiet=True)
    st = conn.execute("SELECT accrued_through FROM accrual_state WHERE kind=? AND ref_id=?", (kind, cid)).fetchone()
    through = st["accrued_through"] if st is not None else None
    last_night = (period[1] - timedelta(days=1)).isoformat()
    if through is None or through < last_night:
        return swaps._skip(ctx, JOB_COUPON, key, "ACCRUALS_PENDING", f"{cid}: the premium is accrued through {through or 'nothing'}, the period ends {last_night}"
                           " (ACCRUE_DAILY first)", aid, cid, {"accrued_through": through, "needed": last_night})
    amount, rate = swaps.leg_coupon_c(conn, row, spec, period)
    have = balance(conn, aid, spec.gl, ccy, cid)
    if amount is None or have < amount:
        return swaps._skip(ctx, JOB_COUPON, key, "ACCRUALS_PENDING", f"{cid}: premium accrued {fmt_money(have)} in {spec.gl} but the coupon is {fmt_money(amount or 0)}", aid, cid)
    buyer = kind == "swappay"
    funding = None
    if buyer:
        with swaps.asof_context(ctx, d):         # the coupon of d is funded on d (C10 / C20) with CSA netting (C21)
            funding = swaps.fund_csa(ctx, services, aid, ccy, amount, "cdxcpn", idem, overdraft=True)
        lines = [dr(aid, "2340", ccy, amount, instrument_id=cid, memo="CDX coupon paid (accrued premium settled)"), cr(aid, "1010", ccy, amount, memo="CDX coupon paid")]
    else:
        lines = [dr(aid, "1010", ccy, amount, memo="CDX coupon received"), cr(aid, "1225", ccy, amount, instrument_id=cid, memo="CDX coupon received (accrued premium settled)")]
    days = (period[1] - period[0]).days
    memo = (f"CDX coupon {cid} {ds}: {fmt_money(int(row['notional_c']))} x {terms.get('coupon_bp')} bp x {days} days ({period[0].isoformat()} to"
            f" {(period[1] - timedelta(days=1)).isoformat()}) / 360 = {fmt_money(amount)} {'paid' if buyer else 'received'} (CDX_COUPON)")
    entry_id = ctx.post(Entry("SWAP_PAY", memo, lines, idem, ref_type="otc", ref_id=cid, business_date=ds))
    net = -amount if buyer else amount
    detail = {"receive_c": 0 if buyer else amount, "pay_c": amount if buyer else 0, "net_c": net, "period": [period[0].isoformat(), period[1].isoformat()], "days": days,
              "coupon_bp": terms.get("coupon_bp"), "rate": rate, "entry_id": entry_id, "kind": "CDX coupon"}
    conn.execute("INSERT INTO otc_resets(contract_id, event_date, kind, amount_c, detail_json, entry_id) VALUES (?,?,'PAY',?,?,?)", (cid, ds, net, dumps(detail), entry_id))
    ctx.touched_instruments.add((aid, cid))
    out = {"status": "DONE", "key": key, "amount_c": amount, "net_c": net, "entry_id": entry_id, "posted": True,
           "funding": None if funding is None else (funding.to_dict() if hasattr(funding, "to_dict") else None)}
    if period == spec.periods[-1]:
        row = swaps._open_contract(conn, cid)
        closed = swaps.close_contract(ctx, services, row, "MATURED", 0, swaps._reference_mark_c(conn, cid), f"otc:{cid}:mature", "OTC_MATURE",
                                      f"CDX maturity {cid} {ds}: VM balance {fmt_money(int(row['vm_balance_c']))} unwound, IM {fmt_money(int(row['im_c']))} released"
                                      " (CDX_COUPON)", False, True)
        out["matured"] = closed
        ctx.event("SWAP_MATURED", account_id=aid, instrument_id=cid, ref_type="otc", ref_id=cid, date=ds, **{k: v for k, v in closed.items() if k != "funding"})
    swaps._job_done(ctx, JOB_COUPON, key, "DONE", f"{'paid' if buyer else 'received'} {fmt_money(amount)}" + ("; matured" if "matured" in out else ""))
    swaps._resolve_attention(ctx, ("ACCRUALS_PENDING",), key)
    ctx.event("SWAP_PAY", account_id=aid, instrument_id=cid, ref_type="otc", ref_id=cid, date=ds, receive_c=detail["receive_c"], pay_c=detail["pay_c"], net_c=net, entry_id=entry_id)
    return out


def coupon_keys(conn: sqlite3.Connection, now_ts: int) -> List[Tuple[str, str]]:
    """``(contract_id, payment date)`` pairs whose CDX_COUPON is due: payment dates on or before today (00:45 ET passed) of OPEN CDX
    contracts without a PAY row and not terminal in ``jobs_done``."""
    now = int(now_ts)
    today = ny_of(now).date()
    out: List[Tuple[str, str]] = []
    for row in swaps._open_contracts(conn, (KIND,)):
        cid = row["contract_id"]
        paid = {r["event_date"] for r in conn.execute("SELECT event_date FROM otc_resets WHERE contract_id=? AND kind='PAY'", (cid,)).fetchall()}
        done = swaps._jobs(conn, JOB_COUPON, cid)
        for _s, _e, p in _periods(_terms({k: row[k] for k in row.keys()})):
            if p > today or now < epoch_of(p, COUPON_ET):
                continue
            ds = p.isoformat()
            if ds not in paid and not swaps._terminal(done.get(f"{cid}:{ds}")):
                out.append((cid, ds))
    return out


# ---- views ----------------------------------------------------------------------------------------------------------------------------
def next_events(services, contract, conn=None, limit: int = 4) -> List[dict]:
    """C21: the next coupon payments (amount known: a fixed coupon) and the maturity."""
    row = swaps._contract_dict(services, contract, conn)
    if row is None or row.get("status") != "OPEN":
        return []
    terms = _terms(row)
    today = ny_of(services.clock.epoch()).date()
    buyer = (terms.get("side") or "BUY_PROTECTION") == "BUY_PROTECTION"
    out = []
    for s, e, p in _periods(terms):
        if p < today:
            continue
        amt = coupon_amount_c(int(row["notional_c"]), terms.get("coupon_bp"), s, e)
        out.append({"date": p.isoformat(), "event": "MATURITY" if p == _periods(terms)[-1][2] else "PAY",
                    "legs": [{"leg": "coupon", "side": "pay" if buyer else "receive", "amount_c": amt, "known": True, "period": [s.isoformat(), e.isoformat()]}],
                    "detail": f"CDX coupon {terms.get('coupon_bp')} bp x {(e - s).days} days / 360"})
        if len(out) >= 2:
            break
    if not any(x["event"] == "MATURITY" for x in out):
        out.append({"date": row["maturity_date"], "event": "MATURITY", "detail": "scheduled termination: the last coupon, VM unwound and IM released"})
    return out[:max(1, int(limit))]


def contract_detail(services, contract, conn=None) -> Optional[dict]:
    """C21: the CDX specifics for the contracts table / drawer - the side as a sentence, the index, the dealt spread and upfront, the
    rebate and the initial payment, the live spread and CS01."""
    row = swaps._contract_dict(services, contract, conn)
    if row is None:
        return None
    terms = _terms(row)
    spec = _index(terms.get("index"))
    side = terms.get("side") or "BUY_PROTECTION"
    series = terms.get("series")
    cs = None
    live = None
    if row.get("status") == "OPEN":
        try:
            now = int(services.clock.epoch())
            base, inputs = _valuation(services, row, now, conn=conn)
            up, _ = _valuation(services, row, now, shift_spread_bp=1, conn=conn)
            if base is not None and up is not None:
                cs = int(up) - int(base)
                live = {"mid_spread_bp": inputs.get("mid_spread_bp"), "proxy": inputs.get("proxy"), "basis_bp": inputs.get("basis_bp"), "upfront_pct": inputs.get("upfront_pct"),
                        "price": inputs.get("price"), "rpv01": inputs.get("rpv01"), "value_c": int(base)}
            else:
                live = {"error": inputs.get("reason")}
        except (ConflictError, RejectError) as e:
            live = {"error": f"{e.code}: {e.message}"}
    n = int(row["notional_c"])
    st = _side_text(spec, series, side)
    cs_txt = "" if cs is None else f"; CS01 {'+' if cs >= 0 else '-'}{fmt_money(abs(cs))} per bp"
    terms_text = (f"{spec['name']} S{series} 5Y to {terms.get('maturity_date')}, coupon {terms.get('coupon_bp')} bp, recovery {_pct_text(Decimal(terms.get('recovery')), '1')};"
                  f" dealt {terms.get('deal_spread_bp')} bp (proxy {terms.get('proxy_bp')} bp as of {terms.get('proxy_as_of')} + basis {terms.get('basis_bp')} bp"
                  f" {'+' if side == 'BUY_PROTECTION' else '-'} half-spread); upfront {terms.get('upfront_pct')} % (price {terms.get('price')}), accrued rebate"
                  f" {fmt_money(int(terms.get('rebate_c') or 0))}, initial payment {fmt_money(abs(int(terms.get('initial_payment_c') or 0)))}"
                  f" {'received' if int(terms.get('initial_payment_c') or 0) > 0 else 'paid'} {terms.get('settle_date')}")
    events = credit_events_of(services, [row["contract_id"]], conn).get(row["contract_id"], [])
    if events:
        terms_text += (f"; {len(events)} simulated credit event(s): notional {fmt_money(int(terms.get('original_notional_c') or n))} x factor"
                       f" {terms.get('index_factor')} = {fmt_money(n)}")
    return {"side": side, "side_text": f"{st}: USD {fmt_money(n)} notional{cs_txt}", "index": spec["key"], "index_name": spec["name"], "series": series,
            "credit_events": events, "index_factor": terms.get("index_factor") or "1.000000", "original_notional_c": int(terms.get("original_notional_c") or n),
            "names": spec["names"], "weights": WEIGHTS_NOTE,
            "exposure_c": -n if side == "BUY_PROTECTION" else n, "cs01_c": cs, "live": live, "terms_text": terms_text, "summary": terms_text,
            "deal_spread_bp": terms.get("deal_spread_bp"), "upfront_c": terms.get("upfront_c"), "upfront_pct": terms.get("upfront_pct"), "price": terms.get("price"),
            "rebate_c": terms.get("rebate_c"), "initial_payment_c": terms.get("initial_payment_c"), "settle_date": terms.get("settle_date"),
            "coupon_bp": terms.get("coupon_bp"), "recovery": terms.get("recovery"), "label": PROXY_LABEL}


def _account_contracts(conn, account_id: str) -> List[dict]:
    return [dict(r) for r in conn.execute("SELECT * FROM otc_contracts WHERE account_id=? AND kind=? ORDER BY contract_id", (account_id, KIND)).fetchall()]


def desk(services, account_id: str) -> dict:
    """What the CDX ticket needs: the indices (series, maturity, coupon, recovery), the proxy spreads with their dates and staleness,
    this account's editable assumptions, the PB's spread multiplier, the eligibility, the relationship and the account's CDX rows."""
    with _marks.shared_reader(services.db) as conn:
        acct = account_row(conn, account_id)
        if acct is None:
            raise NotFound("ACCOUNT_UNKNOWN", f"account {account_id} does not exist", {"account_id": account_id})
        acct = dict(acct)
        if acct["kind"] != "PB":
            raise RejectError("NOT_TRADEABLE_HERE", f"{account_id} is not a prime-broker account: CDX is dealt with a prime broker", {"account_id": account_id})
        sched = prime.read_schedule(conn, acct)
        try:
            rel = prime.relationship(conn, account_id)
        except Exception:   # noqa: BLE001
            rel = {}
        terms = {k: terms_for(conn, account_id, k) for k in INDEX}
        rows = _account_contracts(conn, account_id)
    try:
        elig = _eligibility(services, split_account_id(account_id)[0]).to_dict()
    except Exception as e:   # noqa: BLE001
        elig = {"error": str(e)[:200]}
    blocked = None
    if not (elig.get("pb_otc") and elig.get("ecp")):
        blocked = elig.get("pb_otc_reason") or "this book is not eligible for OTC bookings"
    now = int(services.clock.epoch())
    today = ny_of(now).date()
    otr = on_the_run(services.cal, today)
    mult = qprice(Decimal(sched.otc_spread_mult))
    indices = {}
    for k, spec in INDEX.items():
        px = proxy(services, k, today)
        t = terms[k]
        ind = {"key": k, "name": spec["name"], "long_name": spec["long_name"], "names": spec["names"], "series": otr["series"], "tenor": TENOR,
               "roll_date": otr["roll_date"].isoformat(), "next_roll": otr["next_roll"].isoformat(), "maturity_date": otr["maturity"].isoformat(),
               "coupon_bp": str(spec["coupon_bp"]), "recovery": str(spec["recovery"]), "recovery_note": spec["recovery_note"], "quoted": spec["quoted"],
               "proxy": _proxy_view(px), "basis_bp": str(t["basis_bp"]), "half_spread_bp": str(t["half_spread_bp"]), "half_spread_eff_bp": str((t["half_spread_bp"] * mult).quantize(BP_Q)),
               "im_pct": str(t["im_pct"]), "terms_source": t["source"], "terms_updated_ts": t["updated_ts"],
               "seed": {kk: str(vv) for kk, vv in spec["seed"].items()}}
        if px["ok"]:
            ind["mid_spread_bp"] = str((px["spread_bp"] + t["basis_bp"]).quantize(BP_Q))
        indices[k] = ind
    return {"account_id": account_id, "broker": prime.broker_of(acct), "relationship": rel.get("agreements_line") if isinstance(rel, dict) else None,
            "eligibility": elig, "blocked": blocked, "otc_spread_mult": str(mult), "margin_mode": str(sched.swap_margin_mode).upper(), "indices": indices,
            "term_specs": {k: {kk: (str(vv) if isinstance(vv, Decimal) else vv) for kk, vv in v.items()} for k, v in TERM_SPECS.items()},
            "series": otr["series"], "maturity_date": otr["maturity"].isoformat(), "settle_rule": f"T+{UPFRONT_SETTLE_BD} business day (cleared CDX initial payment)",
            "label": PROXY_LABEL, "support": "LIMITED", "model": MODEL, "contracts": [r["contract_id"] for r in rows],
            "conventions": {k: tables.CONVENTIONS.get(k, "") for k in CONVENTIONS}, "unsupported": unsupported()}


# ---- the editable assumptions (cdx.terms) ------------------------------------------------------------------------------------------
def _parse_term(field: str, value) -> Decimal:
    spec = TERM_SPECS.get(field)
    if spec is None:
        raise RejectError("TERMS_FIELD", f"{field!r} is not an editable CDX term; one of {', '.join(TERM_SPECS)}", {"field": field})
    if value is None or isinstance(value, (bool, float)) or (isinstance(value, str) and not value.strip()):
        raise RejectError("TERMS_VALUE", f"{spec['label']}: a decimal string is required (never stored as 0), got {value!r}", {"field": field})
    s = str(value).strip().replace(",", "")
    pct = s.endswith("%")
    try:
        d = Decimal(s.rstrip("%").strip())
    except (InvalidOperation, ValueError) as e:
        raise RejectError("TERMS_VALUE", f"{spec['label']}: {value!r} is not a number", {"field": field}) from e
    if not d.is_finite():
        raise RejectError("TERMS_VALUE", f"{spec['label']}: {value!r} is not a finite number", {"field": field})
    if spec["unit"] == "pct" and (pct or d >= 1):
        d = d / PCT                                     # "2" or "2 %" -> 0.02 (a fraction of 1 or more is read as a percent)
    if d != d.quantize(Decimal("0.00000001")):
        raise RejectError("TERMS_VALUE", f"{spec['label']}: at most 8 decimal places", {"field": field})
    if d < spec["min"] or d > spec["max"]:
        lo, hi = (spec["min"] * PCT, spec["max"] * PCT) if spec["unit"] == "pct" else (spec["min"], spec["max"])
        raise RejectError("TERMS_VALUE", f"{spec['label']}: {d if spec['unit'] != 'pct' else d * PCT} is outside {lo} to {hi} {'%' if spec['unit'] == 'pct' else 'bp'}",
                          {"field": field})
    return qprice(d)


def h_terms(ctx: TxContext, payload: dict) -> dict:
    """``cdx.terms {account_id, index, basis_bp?, half_spread_bp?, im_pct?}`` (light): validates, stores and records ONE
    SETTINGS_CHANGED event with before / after; 400 NOTHING_TO_UPDATE when nothing differs."""
    aid = str(payload.get("account_id") or "")
    if not aid:
        raise RejectError("ACCOUNT_REQUIRED", "account_id is required")
    conn = ctx.conn
    acct = account_row(conn, aid)
    if acct is None:
        raise NotFound("ACCOUNT_UNKNOWN", f"account {aid} does not exist", {"account_id": aid})
    if acct["kind"] != "PB" or acct["status"] != "OPEN":
        raise RejectError("NOT_TRADEABLE_HERE", f"{aid} is not an open prime-broker account", {"account_id": aid})
    spec = _index(payload.get("index"))
    given = {k: payload[k] for k in TERM_SPECS if k in payload}
    if not given:
        raise RejectError("NOTHING_TO_UPDATE", f"no CDX term given; editable: {', '.join(TERM_SPECS)}")
    parsed = {k: _parse_term(k, v) for k, v in given.items()}
    data, _ts = _stored_terms(conn, aid)
    cur = terms_for(conn, aid, spec["key"])
    mine = dict(data.get(spec["key"]) or {})
    before, after = {}, {}
    for k, v in parsed.items():
        if Decimal(str(cur[k])) != v:
            before[k], after[k] = str(cur[k]), str(v)
            mine[k] = str(v)
    if not after:
        raise RejectError("NOTHING_TO_UPDATE", "every given term already has that value", {"given": {k: str(v) for k, v in parsed.items()}})
    for k, seed in spec["seed"].items():
        mine.setdefault(k, str(seed))
    data[spec["key"]] = mine
    conn.execute("INSERT INTO cdx_terms(account_id, terms_json, updated_ts, command_id) VALUES (?,?,?,?) ON CONFLICT(account_id) DO UPDATE SET"
                 " terms_json=excluded.terms_json, updated_ts=excluded.updated_ts, command_id=excluded.command_id", (aid, json.dumps(data, sort_keys=True), ctx.ts, ctx.command_id))
    event_id = ctx.event("SETTINGS_CHANGED", account_id=aid, ref_type="cdx_terms", ref_id=aid, index=spec["name"], before=before, after=after, fields=sorted(after),
                         note="CDX assumptions (assumed, editable); past postings are never restated")
    return {"account_id": aid, "index": spec["key"], "before": before, "after": after, "terms": {k: str(v) for k, v in terms_for(conn, aid, spec["key"]).items()
                                                                                                 if k not in ("updated_ts",)}, "event_id": event_id}


# ---- risk (a credit-derivatives block for risk.report) -------------------------------------------------------------------------------
SPREAD_SHOCKS_BP = (-25, 25, 50, 100)


def risk_block(services, rows, base_c: int) -> Optional[dict]:
    """The credit-derivatives block of the Risk page: per CDX contract its notional exposure (bought protection = short credit), CS01
    and the spread it is valued at; totals; spread shocks re-priced (full revaluation at the shocked spread).  ``rows`` are risk's
    PosRisk rows (SWAP rows whose ``extra.kind`` is CDX); None when there are none."""
    mine = [r for r in rows if getattr(r, "kind", None) == "SWAP" and (getattr(r, "extra", None) or {}).get("kind") == KIND]
    if not mine:
        return None
    now = int(services.clock.epoch())
    out_rows, cs_total, short_c, long_c, missing = [], 0, 0, 0, []
    shocks = {b: 0 for b in SPREAD_SHOCKS_BP}
    with _marks.shared_reader(services.db) as conn:
        for r in mine:
            row = swaps._contract_dict(services, r.instrument_id, conn)
            if row is None:
                continue
            terms = _terms(row)
            n = int(row["notional_c"])
            exp = -n if terms.get("side") == "BUY_PROTECTION" else n
            base, inputs = _valuation(services, row, now, conn=conn)
            item = {"contract_id": row["contract_id"], "account_id": row["account_id"], "label": _side_text(_index(terms.get("index")), terms.get("series"), terms.get("side")),
                    "index": terms.get("index_name"), "side": terms.get("side"), "notional_c": n, "exposure_c": exp, "maturity_date": row["maturity_date"],
                    "cs01_c": None, "mid_spread_bp": None, "proxy_as_of": None, "value_c": None}
            if exp < 0:
                short_c += -exp
            else:
                long_c += exp
            if base is None:
                item["note"] = inputs.get("reason")
                missing.append(row["contract_id"])
                out_rows.append(item)
                continue
            up, _ = _valuation(services, row, now, shift_spread_bp=1, conn=conn)
            item.update(cs01_c=None if up is None else int(up) - int(base), mid_spread_bp=inputs.get("mid_spread_bp"), proxy_as_of=(inputs.get("proxy") or {}).get("as_of"),
                        value_c=int(base))
            if item["cs01_c"] is not None:
                cs_total += item["cs01_c"]
            for b in SPREAD_SHOCKS_BP:
                v, _ = _valuation(services, row, now, shift_spread_bp=b, conn=conn)
                if v is not None:
                    shocks[b] += int(v) - int(base)
            out_rows.append(item)
    pct = None if not base_c else (Decimal(short_c + long_c) / Decimal(base_c)).quantize(PX4)
    return {"bucket": "Credit derivatives (CDX notional)", "rows": out_rows, "cs01_c": cs_total, "short_credit_c": short_c, "long_credit_c": long_c,
            "net_credit_c": long_c - short_c, "gross_pct_nav": pct, "missing": missing,
            "scenarios": [{"name": f"credit spreads {'+' if b > 0 else ''}{b} bp", "shock_bp": b, "pnl_c": shocks[b]} for b in SPREAD_SHOCKS_BP],
            "note": ("CS01 = the PV change for +1 bp of each index spread (bought protection gains when spreads widen); scenarios re-price every contract at"
                     " the shocked spread; spreads are a proxy (ICE BofA OAS via FRED + an assumed basis), not CDX dealer quotes")}


# ---- the simulated credit event (FINSIM1 P4 task 11; directive section 9) ----------------------------------------------------------------
#: one row per (contract, reference entity): its own table (additive, D2), so a repeat is refused and the figures are kept for the views
CREDIT_SCHEMA = ["CREATE TABLE IF NOT EXISTS cdx_credit_events (contract_id TEXT NOT NULL, entity_key TEXT NOT NULL, reference_entity TEXT NOT NULL,"
                 " event_date TEXT NOT NULL, recovery_pct TEXT NOT NULL, names INTEGER NOT NULL, defaulted_c INTEGER NOT NULL, protection_c INTEGER NOT NULL,"
                 " accrued_c INTEGER NOT NULL, accrued_days INTEGER NOT NULL, net_c INTEGER NOT NULL, settle_date TEXT NOT NULL, settle_id TEXT,"
                 " notional_before_c INTEGER NOT NULL, notional_after_c INTEGER NOT NULL, cost_released_c INTEGER NOT NULL, im_released_c INTEGER NOT NULL,"
                 " entry_id INTEGER, command_id INTEGER, ts INTEGER NOT NULL, PRIMARY KEY (contract_id, entity_key))"]
CREDIT_EVENT_LABEL = "simulated credit event - recovery entered by the owner; no ISDA Determinations Committee ruling or auction in the simulator"
CREDIT_SETTLE_BD = 1                            # the protection settlement, net of the accrued premium, T+1 business day after the event date
WEIGHTS_NOTE = "equal weights (assumed): each of the index's names is 1 / names of the original notional"


def _parse_recovery(v) -> Decimal:
    if v is None or isinstance(v, (bool, float)) or (isinstance(v, str) and not v.strip()):
        raise RejectError("TERMS", "recovery_pct: the recovery you enter, a percent from 0 to 100 (40 = 40 %)", {"field": "recovery_pct"})
    s = str(v).strip().rstrip("%").strip()
    try:
        d = Decimal(s)
    except (InvalidOperation, ValueError) as e:
        raise RejectError("TERMS", f"recovery_pct: {v!r} is not a number (40 = 40 %)", {"field": "recovery_pct"}) from e
    if not d.is_finite() or d < 0 or d > 100 or d != d.quantize(Decimal("0.0001")):
        raise RejectError("TERMS", f"recovery_pct: a percent from 0 to 100 with at most 4 decimals, got {v!r}", {"field": "recovery_pct"})
    return d


def _ensure_credit_table(services, conn) -> None:
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='cdx_credit_events'").fetchone() is None:
        db = getattr(services, "db", None)
        if db is not None and hasattr(db, "ensure_schema_ext"):
            db.ensure_schema_ext("swaps_cds", SCHEMA + CREDIT_SCHEMA)


def h_credit_event(ctx: TxContext, payload: dict) -> dict:
    """``cdx.credit_event {contract_id, reference_entity, recovery_pct, event_date}`` (P4 task 11): the owner simulates the default of one
    constituent of a CDX index at the recovery he enters - "simulated credit event - recovery entered by the owner; no ISDA Determinations
    Committee ruling or auction in the simulator".  It never fires by itself (no job runs it) and once per contract and name (409
    DUPLICATE_EVENT).  Equal weights (assumed): the defaulted notional Dn = the original notional / names (IG 125, HY 100).
    - the premium is accrued first through the event date on the notional before it (as a termination does);
    - protection Dn x (1 - recovery) to the protection buyer (paid by a seller), realised in the swap result 4450, net of the accrued premium
      on Dn from the period's accrual start through the event date (inclusive), settling T+1 business day (a settlement row, ``ref_type`` otc);
      nights after the event date already accrued on Dn (an event entered late) are reversed;
    - the notional becomes N - Dn (the terms keep the original notional and an index factor): the premium accrual and the IM follow it from the
      next night (the IM released returns to cash), the cost basis (1335 / 2345) is released pro rata Dn / N into 4450, the model mark NAV
      carries is scaled to the reduced notional and the next SWAP_VM values the reduced contract."""
    if str(ctx.actor or "") in ("lifecycle", "matcher", "worker"):
        raise swaps.Forbidden("CREDIT_EVENT_OWNER_ONLY", "a credit event is entered by the owner; it never fires by itself", {"actor": ctx.actor})
    services = ctx.services
    conn = ctx.conn
    cid = str(payload.get("contract_id") or "")
    row = swaps._open_contract(conn, cid)
    if row["kind"] != KIND:
        raise RejectError("KIND", f"{cid} is a {swaps.KIND_LABELS.get(row['kind'], row['kind'])}: a credit event applies to a CDS index contract",
                          {"contract_id": cid, "kind": row["kind"]})
    if row["status"] != "OPEN":
        raise ConflictError("NOT_OPEN", f"{cid} is {row['status']}; a credit event applies to an OPEN contract", {"contract_id": cid})
    entity = " ".join(str(payload.get("reference_entity") or "").split())
    if not entity or len(entity) > 120:
        raise RejectError("TERMS", "reference_entity: the name of the defaulted constituent (1 to 120 characters)", {"field": "reference_entity"})
    ekey = entity.upper()
    rec = _parse_recovery(payload.get("recovery_pct"))
    try:
        ed = date.fromisoformat(str(payload.get("event_date") or "").strip()[:10])
    except ValueError as e:
        raise RejectError("TERMS", f"event_date: an ISO date (2026-11-02), got {payload.get('event_date')!r}", {"field": "event_date"}) from e
    today = ny_of(ctx.ts).date()
    if ed > today:
        raise RejectError("TERMS", f"event_date {ed.isoformat()} is in the future: a credit event is entered once it happened", {"field": "event_date"})
    start = _date(row["start_date"])
    if ed < start:
        raise RejectError("TERMS", f"event_date {ed.isoformat()} is before the contract's trade date {start.isoformat()}: this contract did not cover it",
                          {"field": "event_date"})
    terms = _terms(row)
    periods = _periods(terms)
    period = next((p for p in periods if p[0] <= ed < p[1]), None)
    if period is None:
        raise RejectError("TERMS", f"event_date {ed.isoformat()} is outside the contract's coupon periods", {"field": "event_date"})
    s, e, pay_date = period
    if conn.execute("SELECT 1 FROM otc_resets WHERE contract_id=? AND kind='PAY' AND event_date=?", (cid, pay_date.isoformat())).fetchone() is not None:
        raise ConflictError("EVENT_PERIOD_SETTLED", f"{cid}: the coupon of the period of {ed.isoformat()} ({s.isoformat()} .. {(e - timedelta(days=1)).isoformat()})"
                            f" was paid on {pay_date.isoformat()}; enter a credit event of the current period", {"contract_id": cid, "event_date": ed.isoformat()})
    _ensure_credit_table(services, conn)
    dup = conn.execute("SELECT event_date FROM cdx_credit_events WHERE contract_id=? AND entity_key=?", (cid, ekey)).fetchone()
    if dup is not None:
        raise ConflictError("DUPLICATE_EVENT", f"{cid}: a credit event of {entity} is already recorded (event date {dup['event_date']}); one event per contract"
                            " and reference entity", {"contract_id": cid, "reference_entity": entity})
    spec = _index(terms.get("index"))
    names = int(spec["names"])
    original = int(terms.get("original_notional_c") or terms.get("notional_c") or row["notional_c"])
    dn = to_c(from_c(original) / Decimal(names))
    n_before = int(row["notional_c"])
    n_after = n_before - dn
    if n_after <= 0:
        raise ConflictError("NOTIONAL_EXHAUSTED", f"{cid}: the remaining notional {fmt_money(n_before)} does not cover another defaulted name of"
                            f" {fmt_money(dn)}", {"contract_id": cid})
    side = terms.get("side") or "BUY_PROTECTION"
    buyer = side == "BUY_PROTECTION"
    coupon_bp = Decimal(str(terms.get("coupon_bp") or spec["coupon_bp"]))
    recovery = rec / PCT
    protection = to_c(from_c(dn) * (ONE - recovery))
    accrued_days = (ed + timedelta(days=1) - s).days
    accrued = to_c(from_c(dn) * coupon_bp * BP * Decimal(accrued_days) / D360)
    # 1. the premium through the event date on the notional before it (a late entry's later nights are reversed below)
    caught = _catch_up(ctx, services, row, ed)
    row = swaps._open_contract(conn, cid)
    aid, ccy = row["account_id"], row["currency"]
    leg = "swappay" if buyer else "swaprcv"
    st = conn.execute("SELECT segment_start, base_c, rate, accrued_through, cum_c FROM accrual_state WHERE kind=? AND ref_id=?", (leg, cid)).fetchone()
    through = _date(st["accrued_through"]) if st is not None and st["accrued_through"] else None
    if through is None or through < ed or through >= e:
        raise ConflictError("ACCRUALS_PENDING", f"{cid}: the premium is accrued through {through.isoformat() if through else 'nothing'}, not within the period of"
                            f" the event date {ed.isoformat()}; try again after the next accrual run", {"contract_id": cid})
    late_nights = (through - ed).days
    late = to_c(from_c(dn) * coupon_bp * BP * Decimal(late_nights) / D360) if late_nights > 0 else 0
    cum_new = int(st["cum_c"]) - accrued - late
    adjust = 0
    if through == e - timedelta(days=1):           # the period's last night is already trued up: the rest is the coupon on N - Dn, to the cent
        adjust = coupon_amount_c(n_after, coupon_bp, s, e) - cum_new
        cum_new += adjust
    # 2. the protection settlement, the accrued premium on Dn, the cost basis released pro rata
    net = protection - accrued if buyer else accrued - protection
    sd = services.cal.add_business_days(ed, CREDIT_SETTLE_BD)
    acc_gl = "2340" if buyer else "1225"
    lines = []
    what = f"accrued premium on the defaulted notional {fmt_money(dn)}, {s.isoformat()} .. {ed.isoformat()} ({accrued_days} days)"
    if buyer:
        lines.append(dr(aid, "2340", ccy, accrued + late, instrument_id=cid, memo=what + (f" and {late_nights} later night(s) reversed" if late else "")))
        if late:
            lines.append(cr(aid, "4453", ccy, late, instrument_id=cid, memo=f"premium accrued after the event date on the defaulted notional reversed ({late_nights} nights)"))
        lines.append(cr(aid, "4450", ccy, protection, instrument_id=cid, memo=f"protection received: {fmt_money(dn)} x (1 - {rec} %) (simulated credit event)"))
    else:
        lines.append(cr(aid, "1225", ccy, accrued + late, instrument_id=cid, memo=what + (f" and {late_nights} later night(s) reversed" if late else "")))
        if late:
            lines.append(dr(aid, "4453", ccy, late, instrument_id=cid, memo=f"premium accrued after the event date on the defaulted notional reversed ({late_nights} nights)"))
        lines.append(dr(aid, "4450", ccy, protection, instrument_id=cid, memo=f"protection paid: {fmt_money(dn)} x (1 - {rec} %) (simulated credit event)"))
    if adjust:
        a_gl, b_gl = ("4453", acc_gl) if buyer else (acc_gl, "4453")
        if adjust > 0:
            lines += [dr(aid, a_gl, ccy, adjust, instrument_id=cid, memo="premium true-up to the period coupon on the reduced notional"),
                      cr(aid, b_gl, ccy, adjust, instrument_id=cid, memo="premium true-up to the period coupon on the reduced notional")]
        else:
            lines += [dr(aid, b_gl, ccy, -adjust, instrument_id=cid, memo="premium true-up to the period coupon on the reduced notional"),
                      cr(aid, a_gl, ccy, -adjust, instrument_id=cid, memo="premium true-up to the period coupon on the reduced notional")]
    if net > 0:
        lines.append(dr(aid, "1200", ccy, net, memo=f"credit event settlement receivable {sd.isoformat()}"))
    elif net < 0:
        lines.append(cr(aid, "2100", ccy, -net, memo=f"credit event settlement payable {sd.isoformat()}"))
    cost_paid = balance(conn, aid, "1335", ccy, cid)
    cost_recv = balance(conn, aid, "2345", ccy, cid)
    released = 0
    if cost_recv > 0:
        rel = to_c(from_c(cost_recv) * Decimal(dn) / Decimal(n_before))
        if rel:
            lines += [dr(aid, "2345", ccy, rel, instrument_id=cid, memo="contract cost received released pro rata (defaulted notional)"),
                      cr(aid, "4450", ccy, rel, instrument_id=cid, memo="contract cost received released pro rata (simulated credit event)")]
            released = rel
    elif cost_paid > 0:
        rel = to_c(from_c(cost_paid) * Decimal(dn) / Decimal(n_before))
        if rel:
            lines += [dr(aid, "4450", ccy, rel, instrument_id=cid, memo="contract cost paid released pro rata (simulated credit event)"),
                      cr(aid, "1335", ccy, rel, instrument_id=cid, memo="contract cost paid released pro rata (defaulted notional)")]
            released = -rel
    n_events = int(conn.execute("SELECT COUNT(*) FROM cdx_credit_events WHERE contract_id=?", (cid,)).fetchone()[0] or 0) + 1
    memo = (f"CDX credit event {cid}: {entity} defaulted on {ed.isoformat()}, recovery {rec} % entered by the owner; {spec['name']} equal weights 1 / {names}:"
            f" defaulted notional {fmt_money(dn)}; protection {fmt_money(protection)} {'received' if buyer else 'paid'}, accrued premium {fmt_money(accrued)}"
            f" ({accrued_days} days) {'paid' if buyer else 'received'}, net {fmt_money(abs(net))} {'receivable' if net > 0 else 'payable'} {sd.isoformat()};"
            f" notional {fmt_money(n_before)} -> {fmt_money(n_after)}" + (f"; contract cost {fmt_money(abs(released))} released" if released else "")
            + f" ({CREDIT_EVENT_LABEL})")
    entry_id = ctx.post(Entry("CDX_CREDIT_EVENT", memo, lines, f"otc:{cid}:credit:{n_events}", ref_type="otc", ref_id=cid))
    settle_id = None
    if net:
        settle_id = next_id(conn, "settlement")
        if swaps.has_columns(conn, "settlements", "ref_type", "ref_id"):
            conn.execute("INSERT INTO settlements(settle_id, fill_id, fx_deal_id, account_id, leg, currency, amount_c, gl, due_date, status, ref_type, ref_id)"
                         " VALUES (?,?,?,?,?,?,?,?,?,'PENDING','otc',?)", (settle_id, None, None, aid, "CREDIT EVENT", ccy, net, "1200" if net > 0 else "2100",
                                                                           sd.isoformat(), cid))
        else:
            conn.execute("INSERT INTO settlements(settle_id, fill_id, fx_deal_id, account_id, leg, currency, amount_c, gl, due_date, status)"
                         " VALUES (?,?,?,?,?,?,?,?,?,'PENDING')", (settle_id, None, None, aid, "CREDIT EVENT", ccy, net, "1200" if net > 0 else "2100", sd.isoformat()))
    # 3. the IM follows the reduced notional (the release returns to cash); the notional, the terms' index factor, the accrual base and the mark
    im_before = int(row["im_c"])
    try:
        im_pct = Decimal(str(terms.get("im_pct")))
        im_after = to_c(from_c(n_after) * im_pct)
    except (InvalidOperation, ValueError, TypeError):
        im_after = to_c(from_c(im_before) * Decimal(n_after) / Decimal(n_before))
    im_rel = max(0, im_before - im_after)
    im_entry = None
    if im_rel:
        im_entry = ctx.post(Entry("OTC_IM", f"OTC IM released {cid}: {fmt_money(im_rel)} - the notional fell to {fmt_money(n_after)} after the simulated credit event"
                                  f" of {entity}; IM {fmt_money(im_before)} -> {fmt_money(im_after)}",
                                  [dr(aid, "1010", ccy, im_rel, memo="IM returned (reduced notional)"),
                                   cr(aid, "1330", ccy, im_rel, instrument_id=cid, memo="OTC initial margin released (credit event)")],
                                  f"im:{cid}:credit:{n_events}", ref_type="otc", ref_id=cid))
    terms["original_notional_c"] = original
    terms["index_factor"] = str((Decimal(n_after) / Decimal(original)).quantize(Decimal("0.000001")))
    terms["defaulted_names"] = int(terms.get("defaulted_names") or 0) + 1
    mark_before = int(row["mark_c"] or 0)
    mark_after = to_c(from_c(mark_before) * Decimal(n_after) / Decimal(n_before))
    conn.execute("UPDATE otc_contracts SET notional_c=?, im_c=?, mark_c=?, mark_ts=?, terms_json=? WHERE contract_id=?",
                 (n_after, im_before - im_rel, mark_after, ctx.ts, dumps(terms), cid))
    conn.execute("UPDATE accrual_state SET base_c=?, cum_c=? WHERE kind=? AND ref_id=?", (n_after, cum_new, leg, cid))
    conn.execute("INSERT INTO cdx_credit_events(contract_id, entity_key, reference_entity, event_date, recovery_pct, names, defaulted_c, protection_c, accrued_c,"
                 " accrued_days, net_c, settle_date, settle_id, notional_before_c, notional_after_c, cost_released_c, im_released_c, entry_id, command_id, ts)"
                 " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                 (cid, ekey, entity, ed.isoformat(), str(rec), names, dn, protection, accrued, accrued_days, net, sd.isoformat(), settle_id, n_before, n_after,
                  released, im_rel, entry_id, ctx.command_id, ctx.ts))
    ctx.touched_instruments.add((aid, cid))
    ctx.touched_accounts.add(aid)
    out = {"contract_id": cid, "reference_entity": entity, "event_date": ed.isoformat(), "recovery_pct": str(rec), "names": names, "defaulted_c": dn,
           "protection_c": protection, "accrued_c": accrued, "accrued_days": accrued_days, "net_c": net, "value_c": net, "settle_date": sd.isoformat(),
           "settle_id": settle_id, "notional_before_c": n_before, "notional_after_c": n_after, "cost_released_c": released, "im_released_c": im_rel,
           "im_after_c": im_before - im_rel, "mark_before_c": mark_before, "mark_after_c": mark_after, "late_nights": late_nights, "reversed_c": late,
           "true_up_c": adjust, "entry_id": entry_id, "im_entry_id": im_entry, "caught_up": caught, "label": CREDIT_EVENT_LABEL, "weights": WEIGHTS_NOTE,
           "side": side, "date": sd.isoformat()}
    ctx.event("CDX_CREDIT_EVENT", account_id=aid, instrument_id=cid, ref_type="otc", ref_id=cid, **{k: v for k, v in out.items() if k != "caught_up"})
    out["contract"] = swaps.contract_view(services, cid, conn=conn, detail=True, now_ts=ctx.ts)
    out["entries"] = [c["entry_id"] for c in caught] + [x for x in (entry_id, im_entry) if x is not None]
    return out


def credit_events_of(services, contract_ids, conn=None) -> Dict[str, List[dict]]:
    """The simulated credit events of these contracts (the Derivatives page and the contract views): ``{contract_id: [event]}``."""
    ids = [str(c) for c in (contract_ids or []) if c]
    out: Dict[str, List[dict]] = {}
    if not ids:
        return out
    with _marks.shared_reader(services.db, conn) as conn:
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='cdx_credit_events'").fetchone() is None:
            return out
        q = ",".join("?" for _ in ids)
        for r in conn.execute(f"SELECT * FROM cdx_credit_events WHERE contract_id IN ({q}) ORDER BY event_date, ts", ids).fetchall():
            d = {k: r[k] for k in r.keys()}
            d["label"] = CREDIT_EVENT_LABEL
            out.setdefault(d["contract_id"], []).append(d)
    return out


# ---- routes, registry, binding -----------------------------------------------------------------------------------------------------
def register_routes(router) -> None:
    """C1: ``POST /api/otc/cdx/preview`` {account_id, terms}, ``GET /api/otc/cdx/desk?account_id=``, ``POST /api/otc/cdx/terms`` {account_id,
    index, basis_bp?, half_spread_bp?, im_pct?}.  Booking and termination use the OTC routes (``POST /api/otc/contracts`` kind CDX,
    ``POST /api/otc/contracts/{id}/terminate``)."""
    from ..api import ApiError, NotFound as _ApiNotFound, need, run_command

    def cdx_preview(req):
        rt = req.rt
        sw = need(rt, "swaps")
        aid = str(req.b("account_id") or "")
        with _marks.shared_reader(rt.db) as conn:
            if account_row(conn, aid) is None:
                raise _ApiNotFound(f"account {aid} does not exist")
        try:
            return sw.preview(rt, aid, KIND, dict(req.b("terms") or {}))
        except NotFound:
            raise
        except RejectError as e:
            if e.http not in (400, 409):
                raise
            # V104R2-BW-05: a correctly refused preview (no FRED proxy, a stale one, a bad notional) answers 200 with its blocker, never a 4xx
            return {"refused": True, "bookable": False, "kind": KIND, "account_id": aid, "code": e.code, "message": e.message, "details": e.details or {},
                    "blockers": [{"code": e.code, "message": e.message, "details": e.details or {}}]}

    def credit_event(req):
        payload = {k: v for k, v in (req.body or {}).items() if k not in ("request_id", "contract_id")}
        payload["contract_id"] = req.params[0]
        return run_command(req, "cdx.credit_event", payload)

    def cdx_desk(req):
        aid = str(req.q("account_id") or "")
        if not aid:
            raise ApiError(400, "ACCOUNT_REQUIRED", "account_id is required")
        try:
            return desk(req.rt, aid)
        except NotFound as e:
            raise _ApiNotFound(e.message, e.details)

    def cdx_terms(req):
        return run_command(req, "cdx.terms", {k: v for k, v in (req.body or {}).items() if k != "request_id"})

    router.add("POST", r"/api/otc/cdx/preview", cdx_preview, mutating=False)
    router.add("GET", r"/api/otc/cdx/desk", cdx_desk)
    router.add("POST", r"/api/otc/cdx/terms", cdx_terms)
    router.add("POST", r"/api/otc/contracts/([^/]+)/credit-event", credit_event)


def unsupported() -> List[dict]:
    return [{"item": "CDX index spreads from a dealer or the clearing house", "state": "LIMITED",
             "wording": "no free CDX quote: the spread is the ICE BofA option-adjusted spread from FRED plus an assumed basis, labelled a proxy",
             "where": "prime-broker OTC tab, Credit"},
            {"item": "CDX credit events", "state": "SIMULATED",
             "wording": ("SIMULATED on request - the owner can trigger a constituent default at a recovery he enters; no ISDA Determinations Committee"
                         " rulings or auctions are tracked"), "where": "Derivatives page, OTC contracts (a CDX row)"},
            {"item": "Off-the-run CDX series, other tenors, tranches, single-name CDS and index options", "state": "NOT_OFFERED",
             "wording": "only the on-the-run 5-year CDX.NA.IG and CDX.NA.HY are dealt", "where": "prime-broker OTC tab, Credit"}]


def register(bus, services=None) -> None:
    bus.register("cdx.coupon", h_coupon)
    bus.register("cdx.terms", h_terms, light=True)
    bus.register("cdx.credit_event", h_credit_event)                # P4 task 11: owner-triggered only (no job runs it)


def bind(rt) -> None:
    """C4: the ``cdx_terms`` table; C13: the CDX_COUPON job before SWAP_VM (a coupon date settles before that day's VM)."""
    db = getattr(rt, "db", None)
    if db is not None and hasattr(db, "ensure_schema_ext"):
        db.ensure_schema_ext("swaps_cds", SCHEMA + CREDIT_SCHEMA)
    try:
        from ..lifecycle_eod import ProductJob, add_product_job
        add_product_job(ProductJob(JOB_COUPON, "cdx.coupon", "swaps_cds", "coupon_keys", ("contract_id", "date"), "each CDX coupon date 00:45 ET",
                                   lambda conn, clock, cal, sessions, s: (conn, clock.epoch())), before="SWAP_VM")
    except ImportError:
        pass


__all__ = ["h_credit_event", "credit_events_of", "CREDIT_SCHEMA", "CREDIT_EVENT_LABEL", "book_as_of", "INDEX", "KIND", "on_the_run", "accrual_start", "coupon_periods", "hazard", "survival", "df_function", "rpv01", "price", "upfront_c", "value_c", "cs01_from",
           "rebate_c", "coupon_amount_c", "proxy", "terms_for", "parse_terms", "preview", "book", "mark", "cs01_c", "dv01_c", "leg_specs", "terminate", "h_coupon",
           "coupon_keys", "next_events", "contract_detail", "desk", "h_terms", "risk_block", "register_routes", "register", "bind", "unsupported", "PROXY_LABEL", "MODEL"]
