"""PB OTC equity / FX options and equity / FX forwards with the prime broker (Build 2 P12; BUILD2_PLAN C2, C4, C13, C17, C21,
C22; the owner 2026-10-21: "cmon, OTC with prime broker isnt built either").

Bound as ``services.otc_options`` (server.OPTIONAL_MODULES, C2); the contracts are rows of ``otc_contracts`` (kinds EQ_OPTION,
FX_OPTION, EQ_FORWARD, FX_FORWARD, C4) booked, terminated and margined through WP-17's ``otc.book`` / ``otc.terminate`` / SWAP_VM:
``products/swaps.py`` runs the common gate first (the kind through ``prime.require_otc_kind``, a prime-broker account, the
lifetime's eligibility: ``books.require_pb_otc`` = the PB/OTC rule AND the ECP floor) and then dispatches these kinds here (C21).
Nothing is simulated: every number is a MODEL number from OBSERVED inputs, labelled ``computed`` with each input, its source and its
time; a missing input refuses the deal (409) or labels a mark stale - never a default.

Conventions stated here
- COUNTERPARTY: the prime broker of the account, under the ISDA Master + CSA + PBA recorded at onboarding (``prime.relationship``).
  Its schedule (``prime.FIELD_SPECS``, SR-10 / C22; assumed seeds, editable) gives the dealing half-widths and the initial margin.
- EQ_OPTION: a call or put on ``units`` shares of a US-listed stock / ETF / index with a Cboe chain, European (Black-Scholes-Merton)
  or American (CRR 60-step tree), from ``finsim.engines.options_pricing``; cash-settled at expiry.  Inputs: S = the underlying's
  observed price (Yahoo, delayed, or the session close after hours); K = a price, or a percentage of S rounded to the cent; T =
  calendar days to expiry / 365; r = SOFR (``fx.rate_for``: NY Fed / FRED, else the Treasury bill yield) as the continuous rate
  ln(1 + r x d/360) / (d/365); q = the dividend yield (the trailing 12 months of Yahoo dividend events when a year is recorded, else
  the instrument master's trailing yield, else unknown: 409 NO_DIVIDEND_DATA, never 0); sigma = the Cboe implied vol at K (the strike, linear between the two
  listed strikes around it, else the nearest) of each listed expiry, interpolated in total variance (sigma^2 x t linear in t) between
  the two listed expiries around the expiry, flat before the first / beyond the last listed expiry.  A chain is usable for a deal
  when it is fresh (in the option session: within its delay + 5 min and fetched within 5 min) or, after hours, from the last
  session; otherwise a refresh is queued and the deal waits (the preview shows the old chain's vol labelled stale).
- FX_OPTION: a European call or put on the BASE currency of a pair (a USD pair or a cross), Garman-Kohlhagen written as Black on the
  covered-interest-parity forward F of the expiry's delivery date (``fx.forward_rate``, C17) discounted at the quote currency's
  rate: call = DF_q x (F N(d1) - K N(d2)); K a rate or ATM = the forward; notional in the base currency; the premium and the cash
  settlement are in USD (quote-currency amounts at the quote currency's USD rate).  sigma = the at-the-money implied vol of the
  CurrencyShares ETF of the foreign currency on Cboe (FXE EUR, FXB GBP, FXY JPY, FXF CHF, FXC CAD, FXA AUD) labelled PROXY (an
  ETF's option vol, not an FX dealer's quote; flat across strikes), else the pair's 30-day realised vol from Yahoo daily closes
  labelled "realised - no free implied vol" (crosses and the other currencies always).  The FX market must be open (a fresh print).
- EQ_FORWARD: the mid (the mark) F = S x e^((SOFR - q) T), q as above; P8B-02: dealt on the dealer's carry for the side dealt -
  SOFR + the PB's financing spread when you buy (the dealer funds its long hedge), SOFR - the spread when you sell (its short hedge
  earns less than SOFR) - plus or minus the half-width, so K - F is the whole cost of dealing (a termination deals the opposite side
  the same way); cash-settled: sign x units x (S_T - K) at the underlying's observed close of the maturity date.  P8B-07: a name with
  no dividend data (no Yahoo events over a year and no yield in the instrument master) is not priced - q is never assumed 0.
- FX_FORWARD (FX_CCS_PLAN rev 2 D3 / 3.2, release 1.0.6.1): a pair of USD / EUR / GBP is priced on the FX term curves (``fxcurves``,
  K3): F = S x DF*_base(S, V) / DF*_quote(S, V), the discount factors (1e-10) on each currency's curve plus its cross-currency basis
  level from the PB schedule (``xccy_basis_eur_bp`` / ``xccy_basis_gbp_bp``, ASSUMED; USD none), F at 6 dp (JPY-quoted pairs 4 dp); S
  = a USD pair's executable print, a cross DERIVED from its two USD pairs (6 dp; executable only when both legs are and their quote
  times are within 120 s; the cross print is stored with its difference, more than 5.0 pips refuses SPOT_INCONSISTENT).  Tenors 1W,
  2W, 1M, 2M, 3M, 6M, 9M, 1Y, 18M, 2Y (24M = 2Y) and broken dates up to the 2Y date, every value date a business day of the pair's
  value calendar (``ccycal``: both currencies, and New York for a cross; 400 VALUE_DATE naming the calendar), beyond 2Y 400 TENOR.
  Every quote stores its pricing record (the spot legs and print, each curve with its date, nodes and first node, the discount factors,
  the outright and points, the basis levels, the dealer's terms, IM, limits, settlement funding, the cross-checks and the labels).
  A pair with another currency keeps the LEGACY METHOD - LIMITED (the CIP outright of ``fx.forward_rate``: one overnight / policy rate
  per currency, simple interest, no term structure, no basis) up to 1Y; beyond 1Y 400 TENOR.  DELIVERABLE: at 00:30 ET on the value
  date the account receives one currency and pays the other at K through a linked FX deal that SETTLE_DUE settles in both currencies
  (the sold currency must be there by then - the preview warns; the contract's ``settlement_funding`` names the currencies the
  settlement-funding job buys with USD beforehand).  Its value meanwhile, for the spot date S_t of the valuation date: sign x A x
  (F_t - K) x DF*_quote(S_t, V) x USD per quote unit, F_t quantised as the quote's (so mark0 is exactly the quote's).
- DEALING: an option is priced at sigma +/- the PB's half-width (``eq_option_hw_vol`` / ``fx_option_hw_vol`` x ``otc_spread_mult``)
  against you (a buy at the higher vol, a sale at the lower); a forward at F +/- ``forward_hw_bp`` x ``otc_spread_mult`` bp of F
  against you.  The difference from the mid model value is the cost of dealing - the day-one P&L, shown before booking.
- PREMIUM: an option's premium = units (or the base notional) x the dealt price per unit, paid (bought) or received (sold) on
  T+1 (equity) / T+2 (FX: the pair's spot date) through a settlement row (2100 payable / 1200 receivable) that SETTLE_DUE pays with
  the usual Treasury sweep; at the trade it is booked as the contract's COST BASIS (FINSIM1 C11 / PORTFOLIO-08: ``Dr 1335 / Cr 2100``
  bought, ``Dr 1200 / Cr 2345`` sold), released into the swap result 4450 at expiry or termination (``swaps.close_contract``), and
  the contract's value is its market value (mid model value; MV - cost = the cost of dealing on day one).  Forwards have no premium.
- IM (C22): a SOLD option posts ``option_im_pct`` x the underlying notional in USD; a BOUGHT option none (its premium is the most it
  can lose); a forward ``forward_im_pct`` x its USD notional either side.  ``Dr 1330 / Cr 1010`` after ``swaps.fund_commitment``
  (PO-07: measured against available cash, then one Treasury sweep, then PB financing, else 409).  A deal whose IM plus premium
  cannot be funded is refused before anything is written.
- MARK / VM: the contract's mark = its mid model value to you (options: sign x units x price; forwards: sign x units x (F - K) x
  DF), labelled computed; without a usable input now it is the stored mark labelled stale.  SWAP_VM (17:05 ET) values the contract
  as of the CLOSE of the last trading day on or before the VM date (the underlying's / the pair's recorded or observed close; a
  close not yet known -> CLOSE_PENDING / CAUGHT_UP exactly like a TRS, LC-3) with the implied vol of a chain observed that day,
  else the vol of the last mark carried forward (said), T measured from that close; the change is exchanged in cash under the
  CSA (CTM collateral or STM settlement).  So a bought option receives collateral equal to its value from the dealer (a
  zero-threshold two-way CSA) and a sold one posts it.
- EXPIRY / MATURITY (job OTC_EXPIRY, C13; key {contract}:{date}; equity from 16:20 ET, FX options from 17:05 ET on the date, FX
  forwards from 00:30 ET of the value date): options and equity forwards are cash-settled at the observed close (FX: the pair's
  17:00 ET close): ``swaps.close_contract`` posts the value, unwinds the VM balance and releases the IM.  An FX forward needs no price:
  at 00:30 ET of its value date (G12: a 17:05 delivery fell after the FX day had rolled) it closes at value 0 (its value passes into
  the delivery deal), the VM balance is unwound, the IM released, and the delivery deal at K is posted for SETTLE_DUE (above).  No
  close observed yet (an option / equity forward) -> SKIPPED CLOSE_PENDING (retried; quiet first, one WARN once it persists) - never an
  earlier or intraday price.
- TERMINATION (``otc.terminate``, owner): at the model value now with the half-width against you (an option sold back at sigma - hw,
  bought back at sigma + hw; a forward closed at F -/+ hw), VM unwound, IM released; observed inputs are required (409 otherwise).
- INPUTS kept fresh for these contracts (C14 refresher ``otc_options``, never on a request thread): the underlyings and FX pairs of
  OPEN contracts are subscribed at HELD priority (their closes are observed), the chains of option underlyings / FX proxies are
  requested once each trading day after 16:20 ET (the closing vols for VM), and the Yahoo daily closes behind a realised vol are
  fetched when a preview asks for them (409 VOL_FETCHING meanwhile).
"""
from __future__ import annotations

import json
import math
import threading
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_EVEN
from typing import Dict, List, Optional, Tuple

from finsim.engines import options_pricing as _op

from .. import config, prime, tables
from .. import fx as _fx
from .. import marks as _marks
from ..books import account_row, eligibility as _eligibility, split_account_id
from ..clock import epoch_of, ny_of, ny_str
from ..commands import ConflictError, RejectError, TxContext
from ..db import dumps, next_id
from ..instruments import CCY_NAMES, FX_CROSSES, fx_cross_id, fx_pair_id
from ..ledger import Entry, balance, cr, dr
from ..quotes import MarkView
from ..units import fmt_money, from_c, qprice, to_c
from . import swaps, swaps_trs

KINDS = ("EQ_OPTION", "FX_OPTION", "EQ_FORWARD", "FX_FORWARD")
OPTION_KINDS = ("EQ_OPTION", "FX_OPTION")
FX_KINDS = ("FX_OPTION", "FX_FORWARD")
SOURCE = "otc_options"
JOB_EXPIRY = "OTC_EXPIRY"
#: when OTC_EXPIRY settles a kind on its date (G12: an FX forward delivers at 00:30 ET of its value date, with SETTLE_DUE)
EXPIRY_ET = {"EQ": "16:20", "FX": "17:05", "FX_FORWARD": "00:30"}
EQ_OPTION_GROUP = "US_EQUITY_OPTIONS"
FX_GROUP = "FX_SPOT"
TENORS = ("1W", "2W", "1M", "2M", "3M", "6M", "9M", "1Y")
TENOR_DAYS = {"1W": 7, "2W": 14}
TENOR_MONTHS = {"1M": 1, "2M": 2, "3M": 3, "6M": 6, "9M": 9, "1Y": 12}
MAX_DAYS = 400                       # a little over a year: the chains carry expiries up to a year (config.CHAIN_MAX_DAYS)
FX_PROXY = {"EUR": "FXE", "GBP": "FXB", "JPY": "FXY", "CHF": "FXF", "CAD": "FXC", "AUD": "FXA"}
PROXY_NAMES = {"FXE": "Invesco CurrencyShares Euro Trust", "FXB": "Invesco CurrencyShares British Pound Sterling Trust",
               "FXY": "Invesco CurrencyShares Japanese Yen Trust", "FXF": "Invesco CurrencyShares Swiss Franc Trust",
               "FXC": "Invesco CurrencyShares Canadian Dollar Trust", "FXA": "Invesco CurrencyShares Australian Dollar Trust"}
FX_PAIRS = ("EURUSD", "GBPUSD", "USDJPY", "USDCHF", "USDCAD", "AUDUSD", "NZDUSD")
RV_DAYS = 30                         # realised vol: the last 30 daily log returns, annualised with sqrt(252)
RV_TTL_S = 6 * 3600
RETRY_MS = 2000
PX4, PX6 = Decimal("0.0001"), Decimal("0.000001")
SIG_Q = Decimal("0.000001")
PREMIUM_LAG = {"EQ_OPTION": 1}       # equity premium T+1 business day; FX the pair's spot date
USABLE = ("live", "delayed", "close")
STEPS_NOTE = "CRR 60-step tree"
MODELS = {"EQ_OPTION": "Black-Scholes-Merton (European) / CRR 60-step tree (American) on S, K, T = days / 365, r (SOFR, continuous), q, sigma (Cboe implied vol)",
          "FX_OPTION": "Garman-Kohlhagen: Black on the CIP forward of the delivery date, discounted at the quote currency's rate",
          "EQ_FORWARD": ("mid F = S x e^((SOFR - q) x T); dealt at the dealer's forward for the side (SOFR + the PB financing spread when you buy, SOFR -"
                         " it when you sell) +/- the half-width; value = sign x units x (F - K) x e^(-SOFR x T)"),
          "FX_FORWARD": "F = CIP outright of the value date (fx.forward_rate); value = sign x A x (F - K) x DF_quote at the quote currency's USD rate"}
CONVENTIONS = ("OTC_OPTION", "OTC_FORWARD", "OTC_VM", "ELIGIBILITY")
#: FX_CCS_PLAN D2 / D3: the currencies with a term curve, a calendar and a basis level (the PB forward's curve method)
CURVE_CCYS = ("USD", "EUR", "GBP")
#: the PB forward's tenors for a curve pair (ccycal.OTC_TENORS) and their aliases
OTC_FX_TENORS = ("1W", "2W", "1M", "2M", "3M", "6M", "9M", "1Y", "18M", "2Y")
FWD_TENOR_ALIAS = {"24M": "2Y", "12M": "1Y"}
BASIS_FIELD = {"EUR": "xccy_basis_eur_bp", "GBP": "xccy_basis_gbp_bp"}
MODEL_VERSION = "fxcurves-2"
LEGACY_LABEL = "LEGACY METHOD - LIMITED: one overnight / policy rate per currency, no term structure, no basis"
CURVES_MODEL = ("F = S x DF*_base(S, V) / DF*_quote(S, V) on the FX term curves under the USD CSA (each currency's curve plus its cross-currency basis"
                " level); value = sign x A x (F - K) x DF*_quote x USD per quote unit, for the spot date of the valuation date")
CAPS_TEXT = "notional limits: none configured in the {broker} schedule; compliance has no OTC limit"
SFI_MODES = ("BUY_WITH_USD", "NONE")
#: V1061R2-01 (FX_CCS_PLAN 3.2 labels row, D16): the PB forward's schedule terms - the basis levels, the dealer half-width and the IM
#: percentage - are ASSUMED.  The curve module's forward has no dealer, so its label names the basis levels only (BASIS_ONLY_LABEL); the
#: dealer's quote names all three in one label (TERMS_LABEL), as the float / float swap's record does.
BASIS_ONLY_LABEL = "ASSUMED: the basis levels are "
TERMS_LABEL = "ASSUMED: the basis levels, the dealer half-width and IM are {name} schedule terms"
ASSUMED_SUFFIX = ", ASSUMED"


def schedule_terms_labels(labels: List[str], xt: dict) -> List[str]:
    """V1061R2-01: ``labels`` with the curve module's basis-only label replaced in place by ``TERMS_LABEL`` (appended when absent) -
    "ASSUMED: the basis levels, the dealer half-width and IM are Goldman Sachs schedule terms" for the GS account."""
    text = TERMS_LABEL.format(name=(xt or {}).get("name") or "the prime broker's")
    out, done = [], False
    for lab in labels:
        if str(lab).startswith(BASIS_ONLY_LABEL) or lab == text:
            if not done:
                out.append(text)
                done = True
            continue
        out.append(lab)
    if not done:
        out.append(text)
    return out


def assumed_source(source: Optional[str]) -> str:
    """V1061R2-01 (D16): an IM percentage's source said ASSUMED once - "the prime broker's schedule, ASSUMED"."""
    s = str(source or "the prime broker's schedule")
    return s if s.endswith(ASSUMED_SUFFIX) else s + ASSUMED_SUFFIX

tables.add_conventions({
    "OTC_OPTION": ("OTC options with the prime broker are priced from observed inputs - the underlying's price, the Cboe implied vol (FX: a"
                   " CurrencyShares ETF's at-the-money vol as a proxy, else the pair's 30-day realised vol) and SOFR - at the dealer's half-width"
                   " in vol points against you; the premium settles T+1 (equity) / T+2 (FX) and expiry is cash-settled at the observed close."),
    "OTC_FORWARD": ("OTC forwards are dealt at the model forward plus or minus the dealer's half-width against you: equity F = S x e^((SOFR -"
                    " dividend yield) x T) at mid, dealt on the dealer's carry for the side (SOFR + the PB financing spread when you buy, SOFR - it"
                    " when you sell), cash-settled at the observed close; FX the covered-interest-parity outright, delivered in both currencies on"
                    " the value date. A forward needs a current rate for each currency and the underlying's dividend data; without them it is not"
                    " priced."),
    "OTC_VM": ("OTC options and forwards carry daily variation margin under the CSA like swaps: the change in the model value at the day's"
               " close moves in cash - a bought option receives collateral equal to its value from the dealer, a sold one posts it."),
})


class NotFound(RejectError):
    http = 404


# ---- small helpers ---------------------------------------------------------------------------------------------------------------
def _date(x) -> date:
    return x if isinstance(x, date) else date.fromisoformat(str(x)[:10])


def _terms(row: dict) -> dict:
    try:
        return json.loads(row.get("terms_json") or "{}")
    except ValueError:
        return {}


def _inputs(row: dict) -> dict:
    try:
        return json.loads(row.get("inputs_json") or "{}") or {}
    except ValueError:
        return {}


def _dec(v, name: str, lo: Optional[Decimal] = None, hi: Optional[Decimal] = None) -> Decimal:
    """A positive decimal term (a decimal string or an int; floats are refused, as everywhere money travels)."""
    if v is None or (isinstance(v, str) and not v.strip()):
        raise RejectError("TERMS", f"{name} is required", {"field": name})
    if isinstance(v, (bool, float)):
        raise RejectError("TERMS", f"{name}: send a decimal string, not {type(v).__name__} {v!r}", {"field": name})
    try:
        d = Decimal(str(v).strip().replace(",", ""))
    except Exception as e:   # noqa: BLE001 - any parse failure is the same refusal
        raise RejectError("TERMS", f"{name}: {v!r} is not a number", {"field": name}) from e
    if not d.is_finite() or d <= 0:
        raise RejectError("TERMS", f"{name} must be a positive number, got {v!r}", {"field": name})
    if lo is not None and d < lo or hi is not None and d > hi:
        raise RejectError("TERMS", f"{name} must be between {lo} and {hi}, got {d}", {"field": name})
    return d


def _whole(v, name: str, lo: int, hi: int) -> int:
    d = _dec(v, name)
    if d != d.to_integral_value():
        raise RejectError("TERMS", f"{name} must be a whole number, got {v!r}", {"field": name})
    n = int(d)
    if n < lo or n > hi:
        raise RejectError("TERMS", f"{name} must be between {lo:,} and {hi:,}, got {n:,}", {"field": name})
    return n


def _money_c(v, name: str) -> int:
    d = _dec(v, name)
    if d != d.quantize(Decimal("0.01")):
        raise RejectError("TERMS", f"{name}: at most 2 decimals, got {v!r}", {"field": name})
    return to_c(d)


def _px(x, q: Decimal = PX6) -> str:
    """A price with at least 2 decimals for memos and labels (339.5 -> 339.50)."""
    s = str(Decimal(x).quantize(q, rounding=ROUND_HALF_EVEN)).rstrip("0")
    whole, _dot, frac = s.partition(".")
    return f"{whole}.{(frac + '00')[:max(2, len(frac))]}"


def _pct(x) -> str:
    return f"{(Decimal(str(x)) * 100).quantize(Decimal('0.01'))} %"


def _vol_pts(x) -> str:
    return f"{(Decimal(str(x)) * 100).quantize(Decimal('0.01'))} vol pts"


def _sign(side: str) -> int:
    return 1 if side == "BUY" else -1


def _cont(simple_pct, days: int, basis: int) -> float:
    """ln(1 + r x d/B) / (d/365): the continuously compounded equivalent of a money-market rate (percent) over ``days``."""
    r = float(simple_pct) / 100.0
    if days <= 0:
        return r
    return math.log(1.0 + r * days / float(basis)) / (days / 365.0)


def _f(x) -> float:
    return float(x)


# ---- pricing (pure) -------------------------------------------------------------------------------------------------------------
def eq_price(right: str, style: str, S, K, days: int, r: float, q: float, sigma: float) -> float:
    """Per share: BSM (European) or CRR 60-step (American) from the engine; T = days / 365; at or past expiry the intrinsic value."""
    return _op.price(right, "AMERICAN" if style == "AMERICAN" else "EUROPEAN", _f(S), _f(K), max(0, int(days)) / 365.0, r, q, max(1e-6, sigma))


def eq_greeks(right: str, style: str, S, K, days: int, r: float, q: float, sigma: float) -> dict:
    """Per share: delta, gamma (per USD 1 of S), vega (per vol point), theta (per calendar day), rho (per bp)."""
    return _op.greeks(right, "AMERICAN" if style == "AMERICAN" else "EUROPEAN", _f(S), _f(K), max(0, int(days)) / 365.0, r, q, max(1e-6, sigma))


def black_df(right: str, F: float, K: float, t_vol: float, sigma: float, df: float) -> float:
    """Black on the forward, discounted: DF x (F N(d1) - K N(d2)) for a call, DF x (K N(-d2) - F N(-d1)) for a put (GK)."""
    return df * _op.bsm(right, F, K, max(0.0, t_vol), 0.0, 0.0, max(1e-6, sigma))


def cip_forward(S, r_b_pct, b_basis: int, r_q_pct, q_basis: int, days: int) -> Decimal:
    """C17's formula on a given spot (the VM's close): F = S x (1 + r_q x d/B_q) / (1 + r_b x d/B_b), 6 dp."""
    return _marks.cip_forward(S, Decimal(str(r_b_pct)) / 100, Decimal(str(r_q_pct)) / 100, max(0, int(days)), int(b_basis), int(q_basis))


def fx_df(r_q_pct, q_basis: int, days: int) -> float:
    return 1.0 / (1.0 + float(r_q_pct) / 100.0 * max(0, int(days)) / float(q_basis))


def _strike_iv(rows, K: float) -> Optional[Tuple[float, str]]:
    usable = [c for c in rows if c.quoted and c.iv is not None and 0.01 < c.iv < 5.0]
    if not usable:
        return None
    exact = [c for c in usable if float(c.strike) == K]
    if exact:
        return exact[0].iv, f"strike {exact[0].strike}"
    below = [c for c in usable if float(c.strike) < K]
    above = [c for c in usable if float(c.strike) > K]
    if below and above:
        lo, hi = max(below, key=lambda c: c.strike), min(above, key=lambda c: c.strike)
        k0, k1 = float(lo.strike), float(hi.strike)
        return lo.iv + (hi.iv - lo.iv) * (K - k0) / (k1 - k0), f"strikes {lo.strike} - {hi.strike}"
    near = min(usable, key=lambda c: abs(float(c.strike) - K))
    return near.iv, f"nearest quoted strike {near.strike}"


def chain_vol(chain, right: str, K, expiry: date, val_date: date) -> Tuple[Optional[float], dict]:
    """sigma at (K, expiry) from a Cboe chain (module docstring EQ_OPTION): per listed expiry the IV at K of the same right (the
    other right when that side has no quoted strike), then total variance between the two listed expiries around the expiry;
    ``(None, {note})`` when nothing is quoted (the chain's iv30 is used only when no strike of any expiry is quoted)."""
    if chain is None:
        return None, {"note": "no chain"}
    k = float(K)
    pts = []
    for e in chain.expiries():
        try:
            t = (_date(e) - val_date).days / 365.0
        except ValueError:
            continue
        if t <= 0:
            continue
        rows = chain.rows_for(e)
        got = _strike_iv([c for c in rows if c.right == right], k)
        how = ""
        if got is None:
            got = _strike_iv([c for c in rows if c.right != right], k)
            how = " (other side)"
        if got is not None:
            pts.append((t, float(got[0]), e, got[1] + how))
    tE = (expiry - val_date).days / 365.0
    if not pts:
        iv30 = getattr(chain, "iv30", None)
        if iv30 is not None and 0.01 < iv30 < 5.0:
            return float(iv30), {"method": "the chain's 30-day implied vol (no quoted strike)", "points": []}
        return None, {"note": "no quoted implied volatility in the chain"}
    pts.sort()
    before = [p for p in pts if p[0] <= tE]
    after = [p for p in pts if p[0] >= tE]
    if before and after:
        p1, p2 = before[-1], after[0]
        if p1[0] == p2[0]:
            sigma, method = p1[1], f"listed expiry {p1[2]} ({p1[3]})"
        else:
            w1, w2 = p1[1] ** 2 * p1[0], p2[1] ** 2 * p2[0]
            w = w1 + (w2 - w1) * (tE - p1[0]) / (p2[0] - p1[0])
            sigma = math.sqrt(max(w, 1e-12) / tE) if tE > 0 else p1[1]
            method = (f"total variance between {p1[2]} ({p1[3]}: {p1[1]:.4f}) and {p2[2]} ({p2[3]}: {p2[1]:.4f})")
    elif after:
        p = after[0]
        sigma, method = p[1], f"the first listed expiry {p[2]} ({p[3]}), flat before it"
    else:
        p = before[-1]
        sigma, method = p[1], f"the last listed expiry {p[2]} ({p[3]}), flat beyond it"
    return sigma, {"method": method, "points": [{"expiry": p[2], "iv": f"{p[1]:.6f}", "how": p[3]} for p in pts[:8]]}


def realised_vol(closes: List[Tuple[date, Decimal]], upto: date, n: int = RV_DAYS) -> Tuple[Optional[float], dict]:
    """Annualised close-to-close vol of the last ``n`` daily log returns on or before ``upto`` (sample stdev x sqrt(252))."""
    pts = [(d, float(c)) for d, c in closes if d <= upto and c and c > 0]
    pts.sort()
    pts = pts[-(n + 1):]
    if len(pts) < 11:
        return None, {"note": f"only {len(pts)} daily closes (at least 11 are needed)"}
    rets = [math.log(pts[i][1] / pts[i - 1][1]) for i in range(1, len(pts))]
    m = sum(rets) / len(rets)
    var = sum((x - m) ** 2 for x in rets) / (len(rets) - 1)
    return math.sqrt(var) * math.sqrt(252.0), {"returns": len(rets), "from": pts[0][0].isoformat(), "to": pts[-1][0].isoformat()}


# ---- the per-runtime state behind the C14 refresher ------------------------------------------------------------------------------
class _State:
    def __init__(self):
        self.lock = threading.Lock()
        self.rv: Dict[str, dict] = {}            # yahoo symbol -> {closes [(date, Decimal)], fetched_at, error}
        self.pending: List[str] = []
        self.chains_day: Optional[str] = None    # the ET date the closing chains were requested

    def want(self, sym: str) -> None:
        with self.lock:
            if sym not in self.pending:
                self.pending.append(sym)

    def take(self) -> List[str]:
        with self.lock:
            out, self.pending = self.pending[:6], self.pending[6:]
            return out


_STATE_LOCK = threading.Lock()


def _state(services) -> _State:
    st = getattr(services, "_otc_options_state", None)
    if st is None:
        with _STATE_LOCK:
            st = getattr(services, "_otc_options_state", None)
            if st is None:
                st = _State()
                try:
                    setattr(services, "_otc_options_state", st)
                except AttributeError:
                    pass
    return st


# ---- terms -----------------------------------------------------------------------------------------------------------------------
def _side(t: dict) -> str:
    s = str(t.get("side") or "").strip().upper()
    if s in ("BUY", "LONG", "B"):
        return "BUY"
    if s in ("SELL", "SHORT", "S"):
        return "SELL"
    raise RejectError("TERMS", "side is required: BUY (you buy from the prime broker) or SELL (you sell to it)", {"field": "side"})


def _right(t: dict) -> str:
    r = str(t.get("right") or "").strip().upper()
    if r in ("C", "CALL"):
        return "C"
    if r in ("P", "PUT"):
        return "P"
    raise RejectError("TERMS", "right is required: CALL or PUT", {"field": "right"})


def _pair(t: dict) -> str:
    p = str(t.get("pair") or "").strip().upper().replace("/", "")
    if len(p) != 6 or p[:3] == p[3:] or p[:3] not in CCY_NAMES or p[3:] not in CCY_NAMES:
        raise RejectError("TERMS", f"pair must be a currency pair such as EURUSD or EURJPY, got {t.get('pair')!r}", {"field": "pair"})
    canon = fx_cross_id(p[:3], p[3:])
    if canon != p:
        raise RejectError("TERMS", f"{p} is quoted the other way round in the market: use {canon}", {"field": "pair", "pair": canon})
    return p


def curve_pair(pair) -> bool:
    """FX_CCS_PLAN D3: True for a pair of two of USD / EUR / GBP (priced on the FX term curves); a pair with another currency keeps the
    legacy method."""
    p = str(pair or "").upper()
    return len(p) == 6 and p[:3] in CURVE_CCYS and p[3:] in CURVE_CCYS


def tenors_for(kind: str, pair: Optional[str] = None) -> Tuple[str, ...]:
    """The tenors a kind takes: an FX forward of a curve pair (or with no pair given) the PB's 1W .. 2Y (value dates from the spot date
    on the pair's value calendar), of another pair the FX desk's ON .. 1Y (the legacy method); the others ``TENORS`` (expiries /
    maturities from the trade date)."""
    if kind != "FX_FORWARD":
        return TENORS
    if pair is None or curve_pair(pair):
        return OTC_FX_TENORS
    return tuple(_fx.TENORS)


def _tenor_months(tenor: str) -> Optional[int]:
    """Months of a tenor written nM / nY (None for weeks, ON or anything else)."""
    s = str(tenor or "").strip().upper()
    if len(s) >= 2 and s[:-1].isdigit() and s[-1] in "MY":
        return int(s[:-1]) * (12 if s[-1] == "Y" else 1)
    return None


def _parse_sfi(v, pair: str) -> dict:
    """``settlement_funding {ccy: BUY_WITH_USD | NONE}`` (FX_CCS_PLAN D9): only the pair's non-USD currencies; 400 TERMS otherwise."""
    if v in (None, "", {}):
        return {}
    if not isinstance(v, dict):
        raise RejectError("TERMS", "settlement_funding: give {currency: BUY_WITH_USD | NONE}, e.g. {\"EUR\": \"BUY_WITH_USD\"}",
                          {"field": "settlement_funding"})
    out = {}
    for k, m in v.items():
        ccy, mode = str(k or "").strip().upper(), str(m or "").strip().upper()
        if ccy not in (pair[:3], pair[3:]) or ccy == "USD":
            raise RejectError("TERMS", f"settlement_funding: {ccy or '(blank)'} is not a non-USD currency of {pair}", {"field": "settlement_funding"})
        if mode not in SFI_MODES:
            raise RejectError("TERMS", f"settlement_funding {ccy}: {m!r} - use BUY_WITH_USD (buy it with USD two business days before each obligation)"
                              " or NONE", {"field": "settlement_funding"})
        out[ccy] = mode
    return out


def parse_terms(kind: str, terms: dict) -> dict:
    """Validate and normalise the term dictionary of one kind (400 TERMS naming ``details.field``).

    EQ_OPTION {underlying_id, right, style EUROPEAN|AMERICAN, units, strike | strike_pct, tenor | expiry, side}
    FX_OPTION {pair, right, notional (base currency), strike | "ATM", tenor | expiry, side}
    EQ_FORWARD {underlying_id, units, tenor | expiry, side}
    FX_FORWARD {pair, notional (base currency), tenor | value_date, side}"""
    t = dict(terms or {})
    out: dict = {"kind": kind, "side": _side(t)}
    if kind in ("EQ_OPTION", "EQ_FORWARD"):
        uid = str(t.get("underlying_id") or t.get("underlying") or t.get("symbol") or "").strip().upper()
        if not uid:
            raise RejectError("TERMS", "underlying_id is required (a US-listed stock, ETF or index, e.g. AAPL)", {"field": "underlying_id"})
        out["underlying_id"] = uid
        out["units"] = _whole(t.get("units"), "units", 1, 100_000_000)
    else:
        out["pair"] = _pair(t)
        out["notional_c"] = _money_c(t.get("notional"), "notional") if t.get("notional_c") in (None, "") else int(_whole(t.get("notional_c"), "notional_c", 100, 10 ** 13))
        if out["notional_c"] < 1_000_00:
            raise RejectError("TERMS", f"the smallest notional is 1,000.00 {out['pair'][:3]}", {"field": "notional"})
    if kind in OPTION_KINDS:
        out["right"] = _right(t)
        style = str(t.get("style") or "EUROPEAN").strip().upper()
        if style not in ("EUROPEAN", "AMERICAN"):
            raise RejectError("TERMS", f"style must be EUROPEAN or AMERICAN, got {t.get('style')!r}", {"field": "style"})
        if kind == "FX_OPTION" and style != "EUROPEAN":
            raise RejectError("TERMS", "FX options are European in this terminal (Garman-Kohlhagen)", {"field": "style"})
        out["style"] = style
        strike = t.get("strike")
        if kind == "FX_OPTION" and (strike in (None, "") or str(strike).strip().upper() == "ATM"):
            out["strike"], out["atm"] = None, True
        elif kind == "EQ_OPTION" and strike in (None, "") and t.get("strike_pct") not in (None, ""):
            out["strike"], out["strike_pct"] = None, _dec(t.get("strike_pct"), "strike_pct", Decimal("1"), Decimal("1000"))
        else:
            out["strike"] = _dec(strike, "strike")
    tenor = str(t.get("tenor") or "").strip().upper()
    when = t.get("expiry") or t.get("value_date") or t.get("maturity_date")
    if kind == "FX_FORWARD":
        tenor = FWD_TENOR_ALIAS.get(tenor, tenor)
        out["settlement_funding"] = _parse_sfi(t.get("settlement_funding"), out["pair"])
    if tenor:
        # P8B-03: an FX forward takes the tenor table its pricer prices - a curve pair the PB's 1W .. 2Y (FX_CCS_PLAN D3), another pair the FX
        # desk's ON .. 1Y (legacy) - the same lists the desk offers (desk()["fx_forward_tenors"] / ["fx_forward_tenors_legacy"])
        allowed = tenors_for(kind, out.get("pair"))
        if tenor not in allowed:
            months = _tenor_months(tenor)
            if kind == "FX_FORWARD" and months is not None and months > 12 and not curve_pair(out["pair"]):
                raise RejectError("TENOR", f"beyond 1 year {out['pair']} has no term curve in this build: a pair with a currency outside USD / EUR / GBP is"
                                  " priced by the legacy method up to 1Y", {"field": "tenor", "tenors": list(allowed)})
            if kind == "FX_FORWARD" and months is not None and months > 24:
                raise RejectError("TENOR", "beyond 2 years the PB forward is not offered", {"field": "tenor", "tenors": list(allowed)})
            raise RejectError("TERMS", f"tenor must be one of {', '.join(allowed)} (or give a date), got {t.get('tenor')!r}", {"field": "tenor", "tenors": list(allowed)})
        out["tenor"] = tenor
    elif when:
        try:
            out["expiry"] = _date(when).isoformat()
        except ValueError as e:
            raise RejectError("TERMS", f"the date must be YYYY-MM-DD, got {when!r}", {"field": "expiry"}) from e
    else:
        raise RejectError("TERMS", "a tenor (1W .. 1Y) or a date is required", {"field": "tenor"})
    return out


def _expiry(services, t: dict, trade_date: date, fx: bool, pair: Optional[str] = None) -> Tuple[date, str]:
    """The expiry / maturity / value date of the terms: a tenor from the trade date (months modified following, weeks following on
    the trading calendar; an FX forward's tenor is the FX desk's value date from the spot date) or the date given (a business day
    after the premium / spot date, at most ``MAX_DAYS`` away)."""
    cal = services.cal
    if t["kind"] == "FX_FORWARD":
        return _fwd_value_date(services, t, trade_date, pair)
    if t.get("tenor"):
        tenor = t["tenor"]
        if tenor in TENOR_DAYS:
            return cal.roll_following(trade_date + timedelta(days=TENOR_DAYS[tenor])), tenor
        d = swaps.add_months(trade_date, TENOR_MONTHS[tenor])
        f = cal.roll_following(d)
        return (f if f.month == d.month else cal.roll_back(d)), tenor
    d = _date(t["expiry"])
    if not cal.is_business_day(d):
        raise RejectError("TERMS", f"{d.isoformat()} is not a business day (trading calendar)", {"field": "expiry"})
    if (d - trade_date).days > MAX_DAYS:
        raise RejectError("TERMS", f"{d.isoformat()} is more than {MAX_DAYS} days away (the chains and the curve inputs go to about a year)", {"field": "expiry"})
    return d, "date"


def _ccycal():
    """The currency calendars (FX_CCS_PLAN K1), imported lazily: a module that is not in the build answers unavailable, never a guess."""
    try:
        from .. import ccycal
        return ccycal
    except ImportError as e:
        raise ConflictError("CALENDAR_UNAVAILABLE", "the currency settlement calendars are not in this build: no PB forward value date", {"cause": type(e).__name__}) from e


def _fwd_value_date(services, t: dict, trade_date: date, pair: str) -> Tuple[date, str]:
    """An FX forward's value date: a curve pair (FX_CCS_PLAN D3 / D10) a tenor from the spot date on the pair's value calendar (``ccycal``),
    or a date that must be a business day of that calendar (400 VALUE_DATE naming it) up to the 2Y date (400 TENOR beyond); another pair
    (legacy) the FX desk's tenor dates (NYSE calendar, said LIMITED) up to its 1Y date (400 TENOR beyond)."""
    deliver = pair[:3] if t.get("side") == "SELL" else pair[3:]
    if curve_pair(pair):
        cc = _ccycal()
        if t.get("tenor"):
            return cc.tenor_date(services, pair, t["tenor"], trade_date), t["tenor"]
        d = _date(t["expiry"])
        cc.require_value_date(services, pair, d, deliver)
        limit = cc.tenor_date(services, pair, "2Y", trade_date)
        if d > limit:
            raise RejectError("TENOR", f"beyond 2 years the PB forward is not offered: {d.isoformat()} is after the 2-year date {limit.isoformat()}",
                              {"field": "expiry", "limit": limit.isoformat()})
        return d, "date"
    if t.get("tenor"):
        return _fx.tenor_date(services, pair, t["tenor"], trade_date), t["tenor"]
    d = _date(t["expiry"])
    if not services.cal.is_business_day(d):
        raise RejectError("VALUE_DATE", f"{pair} value date {d.isoformat()}: not a business day on the NYSE calendar (local holidays not modelled)",
                          {"field": "expiry", "value_date": d.isoformat()})
    limit = _fx.tenor_date(services, pair, "1Y", trade_date)
    if d > limit:
        raise RejectError("TENOR", f"beyond 1 year {pair} has no term curve in this build: a pair with a currency outside USD / EUR / GBP is priced by the"
                          f" legacy method up to its 1-year date {limit.isoformat()}", {"field": "expiry", "limit": limit.isoformat()})
    return d, "date"


# ---- observed inputs ---------------------------------------------------------------------------------------------------------------
def _snapshot(services, ids: List[str]):
    return swaps_trs._snapshot(services, [i for i in ids if i])


def _pin(services, ids: List[str]) -> bool:
    return swaps_trs._pin(services, [i for i in ids if i])


def _underlying(services, conn, uid: str):
    inst = services.instruments.get(uid, conn)
    if inst is None:
        raise RejectError("UNKNOWN_UNDERLYING", f"{uid} is not in the instrument master (find it on the Markets page first)", {"underlying_id": uid})
    if inst.kind not in tuple(tables.EQUITY_KINDS) + ("INDEX",):
        raise RejectError("UNDERLYING_UNSUPPORTED", f"an OTC equity option / forward needs a US-listed stock, ETF or index; {uid} is {inst.kind}",
                          {"underlying_id": uid, "kind": inst.kind})
    if inst.currency != "USD":
        raise RejectError("CURRENCY_UNSUPPORTED", f"{uid} is quoted in {inst.currency}: OTC equity underlyings are USD-quoted in this terminal",
                          {"underlying_id": uid, "currency": inst.currency})
    if inst.status != "ACTIVE":
        raise RejectError("UNDERLYING_UNSUPPORTED", f"{uid} is {inst.status}: an active listing is needed", {"underlying_id": uid})
    return inst


def _quote_info(mv) -> dict:
    if mv is None:
        return {}
    return {"price": None if mv.mark is None else str(qprice(mv.mark)), "quality": mv.quality, "source": mv.source, "quote_time": mv.quote_time,
            "delay_label": getattr(mv, "delay_label", None), "label": swaps_trs.quote_label(mv)}


def _observed(mv, what: str, fetching: bool, need_usable: bool = True) -> Decimal:
    """The observed price of an input: it must exist and (to deal) be a fresh print or the session close (409 otherwise)."""
    if mv is None or mv.mark is None or mv.quality == "unavailable":
        again = "; it is being fetched now - the ticket asks again by itself" if fetching else ""
        raise ConflictError("NO_QUOTE", f"{what}: no observed price yet ({getattr(mv, 'note', '') or 'unavailable'}){again}; nothing is priced without one",
                            {"retry_ms": RETRY_MS if fetching else None, "quality": getattr(mv, "quality", "unavailable")})
    if need_usable and mv.quality not in USABLE:
        when = f" ({ny_str(int(mv.quote_time))})" if mv.quote_time else ""
        raise ConflictError("QUOTE_STALE", f"{what}: the last price {mv.mark} is {mv.quality}{when}; a fresh print or the session close is needed (it is being"
                            " fetched)", {"quality": mv.quality, "price": str(mv.mark), "quote_time": mv.quote_time, "retry_ms": RETRY_MS})
    return qprice(mv.mark)


def _chain_ok(services, chain, now: int, under) -> Tuple[str, Optional[str]]:
    """``(quality, reason)`` of a chain for DEALING: the listed-option rule (``options.chain_freshness``) and, outside the option
    session, a chain of the last session (not an older one)."""
    from . import options as _options
    quality, _age, reason = _options.chain_freshness(services, chain, now, under)
    if quality == "close":
        last = _last_open_session_day(services, EQ_OPTION_GROUP, now)
        qt = getattr(chain, "quote_time", None) or getattr(chain, "fetched_at", None)
        if last is not None and (qt is None or ny_of(int(qt)).date() < last):
            when = ny_str(int(qt)) if qt else "an unknown time"
            return "stale", f"the chain is from {when}, before the last session ({last.isoformat()}): a refresh is needed"
    return quality, reason


def _last_open_session_day(services, group: str, now: int) -> Optional[date]:
    """The latest day of ``group`` whose session has opened by ``now`` (today once it has opened, else the previous one)."""
    today = ny_of(int(now)).date()
    for i in range(12):
        d = today - timedelta(days=i)
        try:
            ss = services.sessions.sessions_for(group, d)
        except Exception:   # noqa: BLE001 - a stand-in sessions table
            ss = None
        if ss and ss[0].open_ts <= int(now):
            return d
    return None


def _chain(services, snap, uid: str, now: int, under=None, request: bool = False):
    """The cached chain of ``uid`` (the snapshot's, else the cache's) with its dealing quality; with ``request`` a missing or stale
    chain is asked for (one fetch per underlying, on the quote worker) and a missing one raises 409 CHAIN_FETCHING."""
    chain = snap.chain(uid) if snap is not None and hasattr(snap, "chain") else None
    chains = getattr(services, "chains", None)
    if chain is None and chains is not None and hasattr(chains, "peek"):
        chain = chains.peek(uid)
    if chain is None:
        if not request:
            return None, "unavailable", "no chain cached"
        err = chains.last_error(uid) if chains is not None and hasattr(chains, "last_error") else None
        if chains is None:
            raise ConflictError("NO_CHAIN", f"{uid}: no Cboe chain source in this runtime", {"underlying_id": uid})
        if err and ("404" in err or "no cboe symbol" in err.lower() or "403" in err):
            raise ConflictError("NO_CHAIN", f"{uid} has no Cboe option chain ({err}): no implied vol to price from", {"underlying_id": uid, "error": err})
        if hasattr(chains, "keep_on_screen"):
            chains.keep_on_screen(uid)
        chains.request(uid)
        raise ConflictError("CHAIN_FETCHING", f"the {uid} option chain (Cboe, delayed) is being fetched for its implied vol - the ticket asks again by itself",
                            {"underlying_id": uid, "retry_ms": RETRY_MS, "status": "fetching"})
    quality, reason = _chain_ok(services, chain, now, under)
    if quality == "stale" and request and chains is not None:
        if hasattr(chains, "keep_on_screen"):
            chains.keep_on_screen(uid)
        chains.request(uid)
    return chain, quality, reason


def _chain_info(chain, quality: str, reason: Optional[str]) -> dict:
    return {"source": f"{chain.source} ({chain.cboe_symbol})", "quote_time": chain.quote_time, "quote_time_ny": ny_str(int(chain.quote_time)) if chain.quote_time else None,
            "fetched_at": chain.fetched_at, "quality": quality, "reason": reason}


def _usd_rate(services, days: int) -> Tuple[float, Decimal, dict]:
    pct, info = _fx.rate_for(services, "USD", max(1, int(days)))
    if pct is None:
        raise ConflictError("RATE_UNAVAILABLE", f"no USD rate: {info.get('note')}; nothing is priced without one", info)
    r = _cont(pct, max(1, int(days)), int(info.get("basis") or 360))
    return r, Decimal(str(pct)), dict(info, continuous=f"{r:.6f}")


_UNIVERSE_AS_OF: Dict[str, Optional[str]] = {}


def _universe_as_of() -> Optional[str]:
    if "v" not in _UNIVERSE_AS_OF:
        try:
            with open(config.universe_path(), "r", encoding="utf-8") as fh:
                _UNIVERSE_AS_OF["v"] = json.load(fh).get("as_of")
        except (OSError, ValueError):
            _UNIVERSE_AS_OF["v"] = None
    return _UNIVERSE_AS_OF["v"]


def dividend_yield(conn, inst, S: Decimal, today: date) -> Tuple[Optional[Decimal], dict]:
    """q (module docstring EQ_OPTION): the trailing 12 months of recorded Yahoo dividend events / S when the record covers about a
    year, else the instrument master's trailing yield (0 for a name the universe data lists without a dividend) - each said.
    P8B-07: with neither (a name looked up outside the universe data, e.g. VMRK) the yield is UNKNOWN: ``(None, {note})`` - never an
    assumed 0, which would overstate the forward of a dividend payer; the deal refuses (``_need_q``) and a mark is labelled stale."""
    rows = conn.execute("SELECT ex_date, amount FROM dividends WHERE instrument_id=? AND ex_date > ? AND ex_date <= ? ORDER BY ex_date",
                        (inst.instrument_id, (today - timedelta(days=365)).isoformat(), today.isoformat())).fetchall()
    if rows and (today - _date(rows[0]["ex_date"])).days >= 270 and S > 0:
        total = sum((Decimal(str(r["amount"])) for r in rows), Decimal(0))
        q = (total / S).quantize(Decimal("0.00000001"))
        return q, {"value": str(q), "pct": _pct(q), "method": f"trailing 12-month dividends recorded from Yahoo events: {len(rows)} ex-dates, {total} per share / S",
                   "events": [{"ex_date": r["ex_date"], "amount": str(r["amount"])} for r in rows]}
    y = (inst.spec or {}).get("dividend_yield")
    if y is not None:
        q = Decimal(str(y)).quantize(Decimal("0.00000001"))
        as_of = _universe_as_of()
        return q, {"value": str(q), "pct": _pct(q), "method": f"trailing dividend yield of the instrument master (universe data{' as of ' + as_of if as_of else ''});"
                                                              f" {len(rows)} Yahoo dividend event(s) recorded in the last year"}
    seen = (f"only {len(rows)} Yahoo dividend event(s) recorded since {rows[0]['ex_date']} (less than a year)" if rows
            else "no Yahoo dividend event recorded in the last year")
    return None, {"value": None, "pct": None, "unknown": True,
                  "method": f"dividend yield unavailable for {inst.instrument_id}: {seen} and no yield in the instrument master (universe data)",
                  "note": (f"no dividend data for {inst.instrument_id} ({seen}, no yield in the instrument master): its dividend yield is unknown and is never"
                           " assumed to be 0, so it cannot be priced")}


def _need_q(q: Optional[Decimal], qinfo: dict, inst) -> Decimal:
    """The deal's dividend yield, or 409 NO_DIVIDEND_DATA with the reason (P8B-07: an unknown yield is never priced as 0)."""
    if q is None:
        raise ConflictError("NO_DIVIDEND_DATA", qinfo.get("note") or f"no dividend data for {inst.instrument_id}", {"underlying_id": inst.instrument_id,
                                                                                                                       "field": "underlying_id"})
    return q


def _fx_ids(pair: str) -> List[str]:
    base, quote = pair[:3], pair[3:]
    ids = [pair]
    for c in (base, quote):
        if c != "USD":
            p = fx_pair_id(c)
            if p not in ids:
                ids.append(p)
    return ids


def _usd_per(services, snap, ccy: str, now: int) -> Tuple[Optional[Decimal], dict]:
    """USD per 1 unit of ``ccy`` now (the FX desk's ledger rate, 8 dp; USD = 1)."""
    if ccy == "USD":
        return Decimal(1), {"ccy": "USD", "rate": "1"}
    leg = _fx.usd_leg(services, snap, ccy, _fx._session(services, now), now)
    return leg["rate"], {k: (str(v) if isinstance(v, Decimal) else v) for k, v in leg.items()}


def _yahoo_symbol(services, pair: str) -> str:
    inst = services.instruments.get(pair)
    return (inst.yahoo_symbol if inst is not None and inst.yahoo_symbol else None) or (f"{pair[3:]}=X" if pair.startswith("USD") else f"{pair}=X")


def _rv(services, pair: str, upto: date, request: bool) -> Tuple[Optional[float], dict]:
    """The pair's 30-day realised vol from Yahoo daily closes (the refresher's cache); with ``request`` a missing / old series is
    asked for and a missing one raises 409 VOL_FETCHING."""
    st = _state(services)
    sym = _yahoo_symbol(services, pair)
    now = int(services.clock.epoch())
    with st.lock:
        got = st.rv.get(sym)
    if got is None or got.get("closes") is None:
        if got is not None and got.get("error") and not request:
            return None, {"note": f"Yahoo daily closes of {sym} unavailable: {got['error']}"}
        if request:
            st.want(sym)
            if got is not None and got.get("error") and now - int(got.get("fetched_at") or 0) < 60:
                raise ConflictError("VOL_UNAVAILABLE", f"{pair}: no free implied vol and the Yahoo daily closes of {sym} failed ({got['error']}); nothing is priced"
                                    " without a vol", {"pair": pair, "symbol": sym})
            raise ConflictError("VOL_FETCHING", f"{pair}: the daily closes of {sym} (Yahoo) are being fetched for the realised vol - the ticket asks again"
                                " by itself", {"pair": pair, "symbol": sym, "retry_ms": RETRY_MS + 1000, "status": "fetching"})
        return None, {"note": f"no Yahoo daily closes of {sym} cached"}
    if request and now - int(got.get("fetched_at") or 0) > RV_TTL_S:
        st.want(sym)
    sigma, info = realised_vol(got["closes"], upto)
    if sigma is None:
        return None, dict(info, symbol=sym)
    return sigma, dict(info, symbol=sym, fetched_at=got.get("fetched_at"), method=f"30-day realised vol of {pair} from Yahoo daily closes ({sym}),"
                                                                                    f" {info['from']} .. {info['to']} - realised, no free implied vol")


def _fx_vol(services, snap, pair: str, expiry: date, val_date: date, now: int, request: bool, carried: Optional[dict] = None,
            day_only: Optional[date] = None) -> Tuple[Optional[float], dict]:
    """sigma of an FX option (module docstring FX_OPTION): the CurrencyShares proxy's ATM implied vol, else the realised vol.
    ``day_only`` (VM): a proxy chain observed that day only, else the carried vol / the realised vol up to that day."""
    base, quote = pair[:3], pair[3:]
    foreign = quote if base == "USD" else (base if quote == "USD" else None)
    etf = FX_PROXY.get(foreign) if foreign else None
    notes = []
    if etf:
        inst = services.instruments.get(etf)
        if inst is None:
            notes.append(f"the proxy {etf} is not in the instrument master")
            worker = getattr(services, "worker", None)
            if request and worker is not None and hasattr(worker, "resolve"):
                try:
                    worker.resolve(etf)
                    notes[-1] += " (being looked up on Yahoo)"
                except Exception:   # noqa: BLE001 - a lookup is a hint
                    pass
        else:
            chain = snap.chain(etf) if snap is not None and hasattr(snap, "chain") else None
            chains = getattr(services, "chains", None)
            if chain is None and chains is not None and hasattr(chains, "peek"):
                chain = chains.peek(etf)
            err = chains.last_error(etf) if chains is not None and hasattr(chains, "last_error") else None
            if chain is None and request and chains is not None and not err:
                if hasattr(chains, "keep_on_screen"):
                    chains.keep_on_screen(etf)
                chains.request(etf)
                raise ConflictError("CHAIN_FETCHING", f"the {etf} option chain (Cboe, the {foreign} proxy) is being fetched for its implied vol - the ticket asks"
                                    " again by itself", {"underlying_id": etf, "retry_ms": RETRY_MS, "status": "fetching"})
            if chain is not None:
                quality, reason = _chain_ok(services, chain, now, inst)
                observed_day = ny_of(int(chain.quote_time or chain.fetched_at)).date()
                if day_only is None or observed_day == day_only:
                    spot = chain.spot
                    if spot is not None and spot > 0:
                        sigma, how = chain_vol(chain, "C", spot, expiry, val_date)
                        if sigma is not None:
                            if quality == "stale" and request and chains is not None:
                                chains.request(etf)
                            return sigma, {"method": f"PROXY: the at-the-money implied vol of {etf} ({PROXY_NAMES.get(etf, etf)}) on Cboe - an ETF's option vol,"
                                                     f" not an FX dealer's quote; flat across strikes ({how.get('method')})", "proxy": etf, "kind": "proxy",
                                           "chain": _chain_info(chain, quality, reason), "quality": quality}
                    notes.append(f"the {etf} chain has no at-the-money implied vol")
                else:
                    notes.append(f"no {etf} chain observed on {day_only.isoformat()}")
            elif err:
                notes.append(f"the {etf} chain is unavailable ({err[:80]})")
    if carried is not None and day_only is not None and carried.get("sigma"):
        return float(carried["sigma"]), {"method": f"carried from the mark of {carried.get('valuation_date') or 'the booking'}: {carried.get('sigma_method') or ''}"
                                                   f" ({'; '.join(notes) if notes else 'no new observation that day'})", "kind": "carried", "quality": "computed"}
    sigma, info = _rv(services, pair, val_date, request)
    if sigma is None:
        if carried is not None and carried.get("sigma"):
            return float(carried["sigma"]), {"method": f"carried from {carried.get('valuation_date') or 'the booking'}: {info.get('note')}", "kind": "carried",
                                             "quality": "stale"}
        return None, {"note": "; ".join(notes + [info.get("note") or "no realised vol"])}
    return sigma, dict(info, kind="realised", quality="close", notes=notes)


# ---- the deal: one function per kind (pure: reads only) -------------------------------------------------------------------------------
def _schedule(conn, acct: dict):
    return prime.read_schedule(conn, acct)


def _im_terms(services, acct: dict) -> dict:
    return swaps.im_params(services, acct["account_id"])


def _blocker(code: str, message: str, **details) -> dict:
    return {"code": code, "message": message, "details": details}


def _eq_inputs(services, conn, snap, uid: str, now: int, today: date, request: bool):
    inst = _underlying(services, conn, uid)
    fetching = _pin(services, [uid]) if request else False
    mv = snap.mark(uid) if snap is not None else None
    S = _observed(mv, f"{uid} (the underlying)", fetching)
    return inst, mv, S


def _eq_option_deal(services, conn, snap, acct: dict, t: dict, now: int, request: bool) -> dict:
    sched = _schedule(conn, acct)
    today = ny_of(now).date()
    uid = t["underlying_id"]
    inst, mv, S = _eq_inputs(services, conn, snap, uid, now, today, request)
    expiry, tenor = _expiry(services, t, today, False)
    premium_date = services.cal.add_business_days(today, PREMIUM_LAG["EQ_OPTION"])
    if expiry <= premium_date:
        raise RejectError("TERMS", f"the expiry {expiry.isoformat()} must be after the premium settles ({premium_date.isoformat()}, T+1)", {"field": "expiry"})
    days = (expiry - today).days
    K = t["strike"] if t.get("strike") is not None else (S * t["strike_pct"] / 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)
    chain, cq, creason = _chain(services, snap, uid, now, inst, request=request)
    sigma, vinfo = chain_vol(chain, t["right"], K, expiry, today)
    if sigma is None:
        raise ConflictError("NO_VOL", f"{uid}: {vinfo.get('note')}: no implied vol to price from", {"underlying_id": uid})
    r, r_pct, rinfo = _usd_rate(services, days)
    q, qinfo = dividend_yield(conn, inst, S, today)
    q = _need_q(q, qinfo, inst)
    mult = Decimal(sched.otc_spread_mult)
    hw = (Decimal(sched.eq_option_hw_vol) * mult).quantize(SIG_Q)
    sign = _sign(t["side"])
    deal_sigma = sigma + float(hw) if sign > 0 else max(0.0001, sigma - float(hw))
    mid_px = Decimal(repr(eq_price(t["right"], t["style"], S, K, days, r, float(q), sigma))).quantize(PX4, rounding=ROUND_HALF_EVEN)
    deal_px = Decimal(repr(eq_price(t["right"], t["style"], S, K, days, r, float(q), deal_sigma))).quantize(PX4, rounding=ROUND_HALF_EVEN)
    g = eq_greeks(t["right"], t["style"], S, K, days, r, float(q), sigma)
    units = int(t["units"])
    premium_c = to_c(Decimal(units) * deal_px)
    mid_c = to_c(Decimal(units) * mid_px)
    notional_c = to_c(Decimal(units) * S)
    imp = _im_terms(services, acct)
    im_pct = Decimal(imp["option_im_pct"]) if imp.get("option_im_pct") is not None else Decimal(sched.option_im_pct)
    im_c = to_c(from_c(notional_c) * im_pct) if sign < 0 else 0
    blockers = []
    if cq == "stale":
        blockers.append(_blocker("VOL_STALE", f"{uid}: {creason or 'the chain is stale'} - a refresh is queued; the ticket asks again by itself", retry_ms=RETRY_MS))
    model = "Black-Scholes-Merton (European)" if t["style"] == "EUROPEAN" or (t["right"] == "C" and q <= 0) else f"CRR 60-step tree (American)"
    pos = {"delta_shares": _f(g["delta"]) * units * sign, "delta_usd_c": to_c(Decimal(repr(g["delta"])) * units * sign * S),
           "gamma_shares_per_usd": _f(g["gamma"]) * units * sign, "vega_usd_c": to_c(Decimal(repr(g["vega"])) * units * sign),
           "theta_usd_c": to_c(Decimal(repr(g["theta"])) * units * sign), "rho_usd_c": to_c(Decimal(repr(g["rho"])) * units * sign)}
    inputs = {"S": str(S), "K": str(K), "days": days, "T": f"{days / 365.0:.6f}", "r": f"{r:.6f}", "r_pct": str(r_pct), "q": str(q), "sigma": f"{sigma:.6f}",
              "sigma_method": f"Cboe implied vol of {uid}: {vinfo.get('method')}", "sigma_quality": cq, "right": t["right"], "style": t["style"], "units": units,
              "sign": sign, "model": model, "valuation_date": today.isoformat(), "underlying": dict(_quote_info(mv), instrument_id=uid),
              "chain": _chain_info(chain, cq, creason), "rate": rinfo, "dividend": qinfo}
    return {"kind": "EQ_OPTION", "sched": sched, "today": today, "trade_date": today, "expiry": expiry, "tenor": tenor, "premium_date": premium_date, "S": S, "K": K,
            "sigma": sigma, "deal_sigma": deal_sigma, "hw": hw, "mult": mult, "mid_px": mid_px, "deal_px": deal_px, "premium_c": premium_c, "mid_c": mid_c,
            "mark0_c": sign * mid_c, "cost_c": abs(premium_c - mid_c), "notional_c": notional_c, "im_c": im_c, "im_pct": im_pct, "im_source": imp.get("source"),
            "greeks": g, "pos": pos, "inputs": inputs, "blockers": blockers, "underlying_id": uid, "inst": inst, "quote": mv, "sign": sign, "units": units,
            "chain_quality": cq, "dv01_c": pos["rho_usd_c"], "margin_mode": str(sched.swap_margin_mode).upper()}


def _fx_spot(services, snap, pair: str, now: int, request: bool) -> Tuple[Decimal, dict]:
    session = _fx._session(services, now)
    pm = _fx.pair_mid(services, snap, pair, session, now)
    if request:
        _pin(services, _fx_ids(pair))
    if pm["mid"] is None:
        raise ConflictError("NO_QUOTE", f"{pair}: no observed rate yet ({pm.get('note') or 'unavailable'}) - it is being fetched; the ticket asks again by itself",
                            {"pair": pair, "retry_ms": RETRY_MS})
    return qprice(pm["mid"]), pm


def _fx_option_deal(services, conn, snap, acct: dict, t: dict, now: int, request: bool) -> dict:
    sched = _schedule(conn, acct)
    pair = t["pair"]
    base, quote = pair[:3], pair[3:]
    S, pm = _fx_spot(services, snap, pair, now, request)
    td = _fx.fx_trade_date(services, now)
    expiry, tenor = _expiry(services, t, td, True, pair)
    sd = _fx.spot_date(services, pair, td)
    if expiry <= sd:
        raise RejectError("TERMS", f"the expiry {expiry.isoformat()} must be after the premium settles ({sd.isoformat()}, the spot date)", {"field": "expiry"})
    vd = _fx.spot_date(services, pair, expiry)
    F, fin = _fx.forward_rate(services, pair, vd, snap)
    if F is None:
        raise ConflictError("FORWARD_UNAVAILABLE", f"{pair}: no forward to the delivery date {vd.isoformat()} - {fin.get('note')}", dict(fin, field="pair"))
    days_d = int(fin["days"])
    rq, rb = fin["r_quote"], fin["r_base"]
    df_q = fx_df(rq["value_pct"], int(rq["basis"]), days_d)
    t_vol_days = (expiry - td).days
    K = F if t.get("atm") else t["strike"]
    Kq = PX6 if "JPY" not in pair else PX4
    K = Decimal(K).quantize(Kq, rounding=ROUND_HALF_EVEN)
    sigma, vinfo = _fx_vol(services, snap, pair, expiry, td, now, request)
    if sigma is None:
        raise ConflictError("NO_VOL", f"{pair}: {vinfo.get('note')}", {"pair": pair})
    mult = Decimal(sched.otc_spread_mult)
    hw = (Decimal(sched.fx_option_hw_vol) * mult).quantize(SIG_Q)
    sign = _sign(t["side"])
    deal_sigma = sigma + float(hw) if sign > 0 else max(0.0001, sigma - float(hw))
    T = t_vol_days / 365.0
    mid_px = Decimal(repr(black_df(t["right"], _f(F), _f(K), T, sigma, df_q))).quantize(PX6, rounding=ROUND_HALF_EVEN)
    deal_px = Decimal(repr(black_df(t["right"], _f(F), _f(K), T, deal_sigma, df_q))).quantize(PX6, rounding=ROUND_HALF_EVEN)
    usd_q, qleg = _usd_per(services, snap, quote, now)
    usd_b, bleg = _usd_per(services, snap, base, now)
    if usd_q is None or usd_b is None:
        raise ConflictError("NO_QUOTE", f"{pair}: no USD rate for {quote if usd_q is None else base} - the premium cannot be valued in USD", {"pair": pair})
    N = from_c(int(t["notional_c"]))
    premium_q_c = to_c(N * deal_px)
    mid_q_c = to_c(N * mid_px)
    premium_c = to_c(from_c(premium_q_c) * usd_q)
    mid_c = to_c(from_c(mid_q_c) * usd_q)
    notional_c = to_c(N * usd_b)
    imp = _im_terms(services, acct)
    im_pct = Decimal(imp["option_im_pct"]) if imp.get("option_im_pct") is not None else Decimal(sched.option_im_pct)
    im_c = to_c(from_c(notional_c) * im_pct) if sign < 0 else 0
    greeks = _fx_greeks(t["right"], S, K, rb, rq, days_d, t_vol_days, sigma, pair)
    pos = {"delta_base": greeks["delta"] * float(N) * sign, "delta_usd_c": to_c(Decimal(repr(greeks["delta"])) * N * sign * usd_b),
           "vega_usd_c": to_c(Decimal(repr(greeks["vega"])) * N * sign * usd_q), "theta_usd_c": to_c(Decimal(repr(greeks["theta"])) * N * sign * usd_q),
           "rho_usd_c": to_c(Decimal(repr(greeks["rho"])) * N * sign * usd_q), "gamma_base_per_pip": greeks["gamma"] * float(N) * sign}
    blockers = []
    if not pm.get("executable"):
        blockers.append(_blocker("QUOTE_STALE", f"{pair}: {str(pm.get('reason') or 'not executable').lower().replace('_', ' ')} - FX options are dealt in the FX"
                                 " session on a fresh print", reason=pm.get("reason")))
    if vinfo.get("quality") == "stale":
        blockers.append(_blocker("VOL_STALE", f"{pair}: the proxy chain is stale - a refresh is queued; the ticket asks again by itself", retry_ms=RETRY_MS))
    inputs = {"S": str(S), "F": str(F), "K": str(K), "days_to_delivery": days_d, "days_to_expiry": t_vol_days, "T": f"{T:.6f}", "df_quote": f"{df_q:.10f}",
              "sigma": f"{sigma:.6f}", "sigma_method": vinfo.get("method"), "sigma_kind": vinfo.get("kind"), "right": t["right"], "notional_c": int(t["notional_c"]),
              "sign": sign, "pair": pair, "usd_per_quote": str(usd_q), "usd_per_base": str(usd_b), "valuation_date": td.isoformat(), "delivery_date": vd.isoformat(),
              "fx": {"pair": pair, "mid": str(S), "quality": pm.get("quality"), "quote_time": pm.get("quote_time"), "source": pm.get("source"), "basis": pm.get("basis")},
              "forward": {k: fin.get(k) for k in ("outright", "points", "arithmetic", "spot_date", "value_date", "days", "limited", "note")},
              "r_base": rb, "r_quote": rq, "vol": {k: v for k, v in vinfo.items() if k != "method"}}
    return {"kind": "FX_OPTION", "sched": sched, "today": td, "trade_date": td, "expiry": expiry, "tenor": tenor, "premium_date": sd, "S": S, "K": K, "F": F,
            "sigma": sigma, "deal_sigma": deal_sigma, "hw": hw, "mult": mult, "mid_px": mid_px, "deal_px": deal_px, "premium_c": premium_c, "mid_c": mid_c,
            "premium_q_c": premium_q_c, "mark0_c": sign * mid_c, "cost_c": abs(premium_c - mid_c), "notional_c": notional_c, "im_c": im_c, "im_pct": im_pct,
            "im_source": imp.get("source"), "greeks": greeks, "pos": pos, "inputs": inputs, "blockers": blockers, "pair": pair, "sign": sign, "usd_q": usd_q,
            "usd_b": usd_b, "dv01_c": pos["rho_usd_c"], "margin_mode": str(sched.swap_margin_mode).upper(), "vd": vd, "fin": fin}


def _fx_value(right: Optional[str], S: Decimal, K: Decimal, rb: dict, rq: dict, days_d: int, t_vol_days: int, sigma: float, d_rq_bp: float = 0.0) -> float:
    """Per unit of base in the quote currency: an option (Black on the CIP forward, discounted) from a SPOT."""
    rqv = float(rq["value_pct"]) + d_rq_bp / 100.0
    F = float(cip_forward(S, rb["value_pct"], int(rb["basis"]), Decimal(repr(rqv)), int(rq["basis"]), days_d))
    df = fx_df(rqv, int(rq["basis"]), days_d)
    return black_df(right, F, float(K), t_vol_days / 365.0, sigma, df)


def _fx_greeks(right: str, S: Decimal, K: Decimal, rb: dict, rq: dict, days_d: int, t_days: int, sigma: float, pair: str) -> dict:
    """Per unit of base, by bump and reprice on the spot: delta (base units), gamma (per pip), vega (per vol point), theta (per day),
    rho (per bp of the quote currency's rate) - all in the quote currency."""
    p0 = _fx_value(right, S, K, rb, rq, days_d, t_days, sigma)
    h = float(S) * 0.0001
    up = _fx_value(right, S + Decimal(repr(h)), K, rb, rq, days_d, t_days, sigma)
    dn = _fx_value(right, S - Decimal(repr(h)), K, rb, rq, days_d, t_days, sigma)
    pip = float(_fx.pip_of(pair))
    delta = (up - dn) / (2 * h)
    gamma = (up - 2 * p0 + dn) / (h * h) * pip
    vega = _fx_value(right, S, K, rb, rq, days_d, t_days, sigma + 0.01) - p0
    theta = (_fx_value(right, S, K, rb, rq, max(0, days_d - 1), max(0, t_days - 1), sigma) - p0) if t_days > 0 else 0.0
    rho = _fx_value(right, S, K, rb, rq, days_d, t_days, sigma, d_rq_bp=1.0) - p0
    return {"price": p0, "delta": delta, "gamma": gamma, "vega": vega, "theta": theta, "rho": rho}


def eq_forward_px(S: Decimal, carry_pct, q, days: int) -> Tuple[Decimal, float]:
    """``(F, r)``: F = S x e^((r - q) x T) at 4 dp, r = the continuous equivalent of the ACT/360 money-market rate ``carry_pct``
    (percent) over ``days``, T = days / 365."""
    r = _cont(carry_pct, max(1, int(days)), 360)
    return (S * Decimal(repr(math.exp((r - float(q)) * (int(days) / 365.0))))).quantize(PX4, rounding=ROUND_HALF_EVEN), r


def eq_forward_carry(sofr_pct, spread, sign: int) -> Decimal:
    """P8B-02: the dealer's carry for a client deal (percent): SOFR + the PB financing spread when the client BUYS the forward (the dealer
    funds its long hedge at its debit rate), SOFR - the spread when he SELLS it (the dealer's short hedge earns less than SOFR).  The
    mid, the contract's mark, carries at SOFR alone; the spread is charged once, against the client, on the side dealt."""
    return Decimal(_px(Decimal(str(sofr_pct)) + (1 if sign > 0 else -1) * Decimal(str(spread)) * 100, Decimal("0.0001")))


def _eq_forward_deal(services, conn, snap, acct: dict, t: dict, now: int, request: bool) -> dict:
    sched = _schedule(conn, acct)
    today = ny_of(now).date()
    uid = t["underlying_id"]
    inst, mv, S = _eq_inputs(services, conn, snap, uid, now, today, request)
    maturity, tenor = _expiry(services, t, today, False)
    days = (maturity - today).days
    if days < 2:
        raise RejectError("TERMS", "a forward needs at least two days to maturity", {"field": "tenor"})
    r_csa, sofr_pct, rinfo = _usd_rate(services, days)
    spread = Decimal(sched.financing_spread)
    q, qinfo = dividend_yield(conn, inst, S, today)
    q = _need_q(q, qinfo, inst)
    T = days / 365.0
    sign = _sign(t["side"])
    # P8B-02: F (the mid and the mark) carries at SOFR - the funding-neutral forward; the dealer deals on its own carry for the side,
    # SOFR + spread for a purchase and SOFR - spread for a sale, then adds the half-width against you: K - F is the whole cost of dealing
    F, r_mid = eq_forward_px(S, sofr_pct, q, days)
    carry_pct = eq_forward_carry(sofr_pct, spread, sign)
    F_deal, r_fin = eq_forward_px(S, carry_pct, q, days)
    mult = Decimal(sched.otc_spread_mult)
    hw_bp = (Decimal(sched.forward_hw_bp) * mult).quantize(Decimal("0.0001"))
    hw_px = (F_deal * hw_bp / Decimal(10000)).quantize(PX4, rounding=ROUND_HALF_EVEN)
    K = F_deal + hw_px if sign > 0 else F_deal - hw_px
    units = int(t["units"])
    df = math.exp(-r_csa * T)
    mark0_c = to_c(Decimal(units) * (F - K) * Decimal(repr(df))) * sign
    notional_c = to_c(Decimal(units) * F)
    imp = _im_terms(services, acct)
    im_pct = Decimal(imp["forward_im_pct"]) if imp.get("forward_im_pct") is not None else Decimal(sched.forward_im_pct)
    im_c = to_c(from_c(notional_c) * im_pct)
    dv01_c = to_c(Decimal(units) * sign * (F - K) * Decimal(repr(math.exp(-(r_csa + 0.0001) * T) - df))) + \
        to_c(Decimal(units) * sign * F * Decimal(repr(math.exp(0.0001 * T) - 1.0)) * Decimal(repr(df)))
    side_words = "you buy: SOFR + the PB financing spread" if sign > 0 else "you sell: SOFR - the PB financing spread"
    inputs = {"S": str(S), "F": str(F), "F_deal": str(F_deal), "K": str(K), "days": days, "T": f"{T:.6f}", "r_csa": f"{r_csa:.6f}", "sofr_pct": str(sofr_pct),
              "financing_spread": str(spread), "carry_pct": str(carry_pct), "carry_side": side_words, "r_mid": f"{r_mid:.6f}", "r_financing": f"{r_fin:.6f}",
              "q": str(q), "df": f"{df:.10f}", "units": units, "sign": sign,
              "valuation_date": today.isoformat(), "underlying": dict(_quote_info(mv), instrument_id=uid), "rate": rinfo, "dividend": qinfo,
              "formula": f"mid F = {S} x e^(({r_mid:.6f} - {q}) x {T:.6f}) = {F} (SOFR carry)",
              "deal_formula": (f"dealer's forward = {S} x e^(({r_fin:.6f} - {q}) x {T:.6f}) = {F_deal} (carry {carry_pct} %: {side_words}); K = {F_deal}"
                               f" {'+' if sign > 0 else '-'} {hw_px} ({hw_bp} bp half-width) = {K}")}
    return {"kind": "EQ_FORWARD", "sched": sched, "today": today, "trade_date": today, "expiry": maturity, "tenor": tenor, "S": S, "F": F, "F_deal": F_deal, "K": K,
            "hw_bp": hw_bp, "hw_px": hw_px, "mult": mult, "units": units, "sign": sign, "mark0_c": mark0_c, "cost_c": abs(mark0_c), "notional_c": notional_c,
            "im_c": im_c, "im_pct": im_pct, "im_source": imp.get("source"), "premium_c": 0, "inputs": inputs, "blockers": [], "underlying_id": uid, "inst": inst,
            "quote": mv, "dv01_c": dv01_c, "margin_mode": str(sched.swap_margin_mode).upper(), "spread": spread, "carry_pct": carry_pct}


# ---- the PB FX forward on the FX term curves (FX_CCS_PLAN rev 2 D2 / D3 / D5 / 3.2; K3) -----------------------------------------------
Q10 = Decimal("0.0000000001")


def _fxc():
    """The FX term-curve module (P-A, K3), imported lazily; a build without it refuses the curve pairs' forwards - never another rate."""
    try:
        from .. import fxcurves
        return fxcurves
    except ImportError as e:
        raise ConflictError("CURVE_UNAVAILABLE", "the FX term curves are not in this build: no PB forward is priced for a USD / EUR / GBP pair", {"cause": type(e).__name__}) from e


def _q10(x) -> Decimal:
    return Decimal(x).quantize(Q10, rounding=ROUND_HALF_EVEN)


def _pxq(pair: str) -> Decimal:
    return PX4 if str(pair)[3:] == "JPY" else PX6


def _xccy_terms(services, conn, acct: dict) -> dict:
    """The PB's cross-currency terms (D5: the basis levels, the EUR OIS spread; ``prime.xccy_terms``) - 409 when the schedule of this build
    has none (never an assumed 0)."""
    fn = getattr(prime, "xccy_terms", None)
    if fn is None:
        raise ConflictError("SCHEDULE_TERMS", "the prime-broker schedule of this build has no cross-currency basis levels: no PB forward on the curves", {})
    return fn(services, acct["account_id"], conn=conn)


def _curve_of(services, ccy: str, on: date, acct_id: Optional[str]):
    """The currency's zero curve on ``on`` (``fxcurves.curve``; the EUR grid carries the PB's EUR OIS spread)."""
    return _fxc().curve(services, ccy, on, pb=acct_id)


def _fwd_curves(services, pair: str, on: date, acct_id: Optional[str], xt: dict) -> dict:
    """D2 / D5: the discount curve of each currency of ``pair`` on ``on`` - its curve plus its basis level from the PB schedule (USD none);
    RATE_UNAVAILABLE / CURVE_UNAVAILABLE refuse."""
    fxc = _fxc()
    return {c: fxc.discount_curve(services, c, on, acct_id, xt) for c in (pair[:3], pair[3:])}


def _dfs(curves: dict, pair: str, s: date, v: date) -> Tuple[Decimal, Decimal]:
    """The raw (unquantised) discount factors DF*_base(s, v), DF*_quote(s, v) on the given discount curves (any order of s and v:
    DF(s, v) for v before s is the inverse, D19)."""
    fxc = _fxc()
    return Decimal(fxc.df(curves[pair[:3]], s, v)), Decimal(fxc.df(curves[pair[3:]], s, v))


def fwd_outright(S: Decimal, df_b: Decimal, df_q: Decimal, pair: str) -> Decimal:
    """D3: F = S x DF*_base / DF*_quote at the pair's precision (6 dp; JPY-quoted 4 dp), from the 1e-10 discount factors."""
    return (Decimal(S) * _q10(df_b) / _q10(df_q)).quantize(_pxq(pair), rounding=ROUND_HALF_EVEN)


def fwd_value_c(sign: int, A: Decimal, F: Decimal, K: Decimal, df_q: Decimal, usd_q: Decimal) -> int:
    """3.2: the forward's value to you in USD cents for the spot date of the valuation date: sign x A x (F - K) x DF*_quote x USD per quote
    unit, with the quote's quantisations (F at the pair's precision, DF at 1e-10) - so mark0 is exactly the quote's.  Worked (3.2): -2,000,000
    x 0.000173 x 0.9094909816 x 1.3323 = -419.2533 -> -419.25."""
    return int(sign) * to_c(Decimal(A) * (Decimal(F) - Decimal(K)) * _q10(df_q) * Decimal(usd_q))


def _raw_value(sign: int, A: Decimal, S: Decimal, K: Decimal, df_b: Decimal, df_q: Decimal, usd_q: Decimal, dF: Decimal = Decimal(0)) -> Decimal:
    """The forward's value in USD without intermediate rounding: sign x A x (S x DF_b / DF_q + dF - K) x DF_q x USD per quote unit."""
    from decimal import localcontext
    with localcontext() as c:
        c.prec = 34
        return Decimal(sign) * A * ((S * df_b / df_q) + dF - K) * df_q * usd_q


def fwd_sensitivities(curves: dict, pair: str, sign: int, A: Decimal, S: Decimal, K: Decimal, usd_q: Decimal, s: date, v: date) -> dict:
    """3.2 sensitivities, each by re-pricing without intermediate rounding (USD cents): ``pv_delta`` {ccy: currency cents} (base +sign x A x
    DF*_base, quote -sign x A x K x DF*_quote), ``rate01_c`` {ccy} (+1 bp on every node of that currency's discount curve,
    ``fxcurves.shifted``), ``basis01_c`` {USD pair: c} (+1 bp on that basis level: the same discount curve), ``spread01_c`` {"EUR"} (+1 bp
    of EUR OIS spread: the EUR grid nodes, ``fxcurves.with_spread``), ``points01_c`` (F + 1 pip).  Worked (3.2, at T): rate01 EUR +420.66 /
    GBP -420.57, EUR OIS spread +423.92, points01 -242.34, PV deltas EUR -1,882,614.77 / GBP +1,576,373.42."""
    from ..instruments import fx_pair_id
    fxc = _fxc()
    base, quote = pair[:3], pair[3:]
    b0, q0 = _dfs(curves, pair, s, v)
    v0 = _raw_value(sign, A, S, K, b0, q0, usd_q)
    out: dict = {"pv_delta": {base: to_c(Decimal(sign) * A * b0), quote: -to_c(Decimal(sign) * A * K * q0)}, "rate01_c": {}, "basis01_c": {}, "spread01_c": {}}
    for c in (base, quote):
        bumped = dict(curves, **{c: fxc.shifted(curves[c], 1)})
        b1, q1 = _dfs(bumped, pair, s, v)
        d = to_c(_raw_value(sign, A, S, K, b1, q1, usd_q) - v0)
        out["rate01_c"][c] = d
        if c in BASIS_FIELD:
            out["basis01_c"][fx_pair_id(c)] = d
    if "EUR" in (base, quote):
        e = curves["EUR"]
        bumped = dict(curves, EUR=fxc.with_spread(e, Decimal(e.spread_bp) + 1))
        b1, q1 = _dfs(bumped, pair, s, v)
        out["spread01_c"]["EUR"] = to_c(_raw_value(sign, A, S, K, b1, q1, usd_q) - v0)
    out["points01_c"] = to_c(_raw_value(sign, A, S, K, b0, q0, usd_q, _fx.pip_of(pair)) - v0)
    return out


def _jsafe(x):
    """A record as JSON-safe values (Decimals and dates as strings)."""
    if isinstance(x, dict):
        return {k: _jsafe(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_jsafe(v) for v in x]
    if isinstance(x, Decimal):
        return str(x)
    if isinstance(x, date):
        return x.isoformat()
    return x


def _spot_blockers(pair: str, sp: dict) -> List[dict]:
    """D3 / 3.2 Blocked: a spot that is not executable gives no quote - NO_QUOTE / QUOTE_STALE / SESSION_CLOSED / SPOT_INCONSISTENT with the
    reason in words (asked again by itself)."""
    if sp.get("executable"):
        return []
    code = str(sp.get("code") or "QUOTE_STALE")
    reason = str(sp.get("reason") or "not executable")
    return [_blocker(code, f"{pair}: {reason} - the PB forward is dealt in the FX session on fresh, consistent prints; asked again by itself",
                     reason=reason, retry_ms=RETRY_MS)]


def _limits_block(services, acct: dict, notional_c: int, mark0_c: int) -> dict:
    """D18: no notional limit is configured - said, with the book's headline gross / NAV (the Risk page's 2x warning) and gross notional /
    NAV before and after the trade (an OTC FX forward is outside the headline: only the notional and NAV move)."""
    broker = prime.broker_of(acct)
    name = config.PRIME_BROKERS.get(broker, broker) if hasattr(config, "PRIME_BROKERS") else broker
    out = {"text": CAPS_TEXT.format(broker=name), "caps": "none configured", "warning_limit": "2x NAV (the Risk page's gross-exposure warning)"}
    try:
        from .. import risk as _risk
        lid = split_account_id(acct["account_id"])[0]
        snap = services.marks.snapshot()
        ex = _risk.exposure_summary(services, f"{lid}:ALL", snapshot=snap)
        nav_c = int(services.accounting.nav(f"{lid}:ALL", snapshot=snap).nav_c)
    except Exception as e:   # noqa: BLE001 - the ratios are information; their absence is said
        out["note"] = f"the book's exposure could not be measured now: {type(e).__name__}: {e}"[:200]
        return out
    nav_after = nav_c + int(mark0_c)

    def ratio(x, n):
        return None if not n or n <= 0 else str((Decimal(abs(int(x))) / Decimal(n)).quantize(Decimal("0.0001")))
    after_notional = int(ex["gross_notional_c"]) + abs(int(notional_c))
    out.update(book=lid, nav_before_c=nav_c, nav_after_c=nav_after, headline_gross_before_c=int(ex["gross_c"]), headline_gross_after_c=int(ex["gross_c"]),
               gross_notional_before_c=int(ex["gross_notional_c"]), gross_notional_after_c=after_notional,
               headline_gross_nav_before=ratio(ex["gross_c"], nav_c), headline_gross_nav_after=ratio(ex["gross_c"], nav_after),
               gross_notional_nav_before=ratio(ex["gross_notional_c"], nav_c), gross_notional_nav_after=ratio(after_notional, nav_after),
               basis="an OTC FX forward is outside the headline gross (its risk is the currency exposure); its USD notional adds to the gross notional")
    return out


def _quoted_flows(pair: str, sell_ccy: str, sell_c: int, buy_ccy: Optional[str], buy_c: Optional[int], vd: date, sfi: dict, im_c: int = 0,
                  im_date: Optional[date] = None) -> List[dict]:
    """D9 (P-D-R2): the quoted forward's own delivery legs for ``ccyfunding.plan(extra=...)`` - the trade is not a contract yet (a quote, or
    the booking, which builds its record before the contract row and its IM exist), so the plan shows its purchase and the settlement-funding
    gap before it is booked.  V1061-03: its IM (``im_c``, posted at the booking on ``im_date`` - one Treasury sweep - and released only at the
    00:30 delivery, after the purchase's USD leg has settled) is a USD outflow too, as it is in the plan of the booked contract (the ledger),
    so the quote's gap equals the booked one.  ``[{date, ccy, amount_c (+ in / - out), what, known, event, kind, mode}]``; ``mode`` = the
    quote's instruction for that currency (BUY_WITH_USD, else NONE)."""
    v = vd.isoformat()

    def mode(c):
        return "BUY_WITH_USD" if (sfi or {}).get(c) == "BUY_WITH_USD" else "NONE"
    out = [{"date": v, "ccy": sell_ccy, "amount_c": -abs(int(sell_c)), "what": f"forward delivery: deliver {sell_ccy} {fmt_money(abs(int(sell_c)))} ({pair})",
            "known": True, "event": "DELIVERY", "kind": "FX_FORWARD", "mode": mode(sell_ccy)}]
    if int(im_c or 0) > 0 and im_date is not None:
        out.append({"date": im_date.isoformat(), "ccy": "USD", "amount_c": -int(im_c), "known": True, "event": "IM", "kind": "FX_FORWARD", "mode": "NONE",
                    "what": f"initial margin USD {fmt_money(int(im_c))} posted at the booking (held until the delivery on {v})"})
    if buy_ccy and buy_c not in (None, ""):
        out.append({"date": v, "ccy": buy_ccy, "amount_c": abs(int(buy_c)), "what": f"forward delivery: receive {buy_ccy} {fmt_money(abs(int(buy_c)))} ({pair})",
                    "known": True, "event": "DELIVERY", "kind": "FX_FORWARD", "mode": mode(buy_ccy)})
    return out


def _funding_block(services, acct: dict, pair: str, sell_ccy: str, sell_c: int, vd: date, sfi: dict, buy_ccy: Optional[str] = None,
                   buy_c: Optional[int] = None, im_c: int = 0, im_date: Optional[date] = None) -> dict:
    """D9: the contract's settlement-funding instruction in words and the plan's gap (``ccyfunding.plan``, read lazily; "unavailable", said,
    while that module is not in the build - never a guessed gap).  The plan is given the quoted trade's own legs and IM (``_quoted_flows``),
    so the first quote already shows its purchase and the gap the booked contract will show (P-D-R2, V1061-03)."""
    mode = (sfi or {}).get(sell_ccy) if sell_ccy != "USD" else None
    if sell_ccy == "USD":
        line = f"the USD delivered on {vd.isoformat()} comes from the account's cash, then one Treasury sweep"
    elif mode == "BUY_WITH_USD":
        line = (f"settlement funding ON: the {sell_ccy} {fmt_money(int(sell_c))} delivered on {vd.isoformat()} is bought with USD two business days before (value"
                f" {vd.isoformat()}), an audited FX desk spot deal; nothing converts on its own")
    else:
        line = (f"settlement funding OFF for {sell_ccy}: the {sell_ccy} {fmt_money(int(sell_c))} delivered on {vd.isoformat()} must be in this account or Treasury by"
                " then (buy it on the FX desk); a delivery that cannot be funded waits, with an error")
    out = {"sfi": dict(sfi or {}), "line": line}
    mod = getattr(services, "ccyfunding", None)
    fn = getattr(mod, "plan", None) if mod is not None else None
    if not callable(fn):
        out["plan"] = {"available": False, "reason": "the settlement-funding plan is not in this build"}
        return out
    try:
        p = fn(services, acct["account_id"], extra=_quoted_flows(pair, sell_ccy, sell_c, buy_ccy, buy_c, vd, sfi, im_c, im_date))
    except Exception as e:   # noqa: BLE001 - said, never a guessed gap
        out["plan"] = {"available": False, "reason": f"{type(e).__name__}: {e}"[:200]}
        return out
    if isinstance(p, dict):
        out["plan"] = _jsafe({k: p.get(k) for k in ("available", "reason", "gaps", "note") if k in p})
        out["gaps"] = _jsafe(p.get("gaps") or [])
    return out


def _fx_forward_deal(services, conn, snap, acct: dict, t: dict, now: int, request: bool) -> dict:
    """FX_FORWARD: a curve pair on the FX term curves (3.2), another pair by the legacy method (1.5)."""
    if curve_pair(t["pair"]):
        return _fx_forward_curve_deal(services, conn, snap, acct, t, now, request)
    return _fx_forward_legacy_deal(services, conn, snap, acct, t, now, request)


def _fx_forward_curve_deal(services, conn, snap, acct: dict, t: dict, now: int, request: bool) -> dict:
    """3.2 / D3: the PB forward of a USD / EUR / GBP pair on the FX term curves (``fxcurves.forward``), dealt at F -/+ the PB half-width,
    with its full pricing record (the curve module's spot, curves, discount factors, outright, basis and labels, plus the dealer's terms, IM,
    the sensitivities, the limits, the settlement funding, the cross-checks and the settlement)."""
    fxc = _fxc()
    cc = _ccycal()
    sched = _schedule(conn, acct)
    xt = _xccy_terms(services, conn, acct)
    aid = acct["account_id"]
    pair = t["pair"]
    base, quote = pair[:3], pair[3:]
    td = _fx.fx_trade_date(services, now)
    on = ny_of(now).date()
    vd, tenor = _expiry(services, t, td, True, pair)
    sd = cc.spot_date(services, pair, td)
    if vd <= sd:
        raise RejectError("TERMS", f"the value date {vd.isoformat()} must be after the spot date {sd.isoformat()} (a spot deal is the FX desk's)",
                          {"field": "tenor" if t.get("tenor") else "expiry"})
    if request:
        _pin(services, _fx_ids(pair))
    sp = fxc.spot(services, pair, snap, now)
    if sp.get("mid") is None:
        raise ConflictError("NO_QUOTE", f"{pair}: no observed rate yet ({sp.get('reason') or 'unavailable'}) - it is being fetched; the ticket asks again by"
                            " itself", {"pair": pair, "retry_ms": RETRY_MS})
    fwd = fxc.forward(services, pair, vd, snap, aid, now=now, on=on, trade_date=td, spot_rec=sp)
    S, F = Decimal(fwd["S"]), Decimal(fwd["F"])
    df_b, df_q = Decimal(fwd["df_base"]), Decimal(fwd["df_quote"])
    points = Decimal(str(fwd["forward"]["points"]))
    mult = Decimal(sched.otc_spread_mult)
    hw_bp = (Decimal(sched.forward_hw_bp) * mult).quantize(Decimal("0.0001"))
    hw_px = (F * hw_bp / Decimal(10000)).quantize(_pxq(pair), rounding=ROUND_HALF_EVEN)
    sign = _sign(t["side"])
    K = F + hw_px if sign > 0 else F - hw_px
    usd_q, _ql = _usd_per(services, snap, quote, now)
    usd_b, _bl = _usd_per(services, snap, base, now)
    if usd_q is None or usd_b is None:
        raise ConflictError("NO_QUOTE", f"{pair}: no USD rate for {quote if usd_q is None else base}", {"pair": pair, "retry_ms": RETRY_MS})
    A = from_c(int(t["notional_c"]))
    mark0_c = fwd_value_c(sign, A, F, K, df_q, usd_q)
    notional_c = to_c(A * usd_b)
    imp = _im_terms(services, acct)
    im_pct = Decimal(imp["forward_im_pct"]) if imp.get("forward_im_pct") is not None else Decimal(sched.forward_im_pct)
    im_c = to_c(from_c(notional_c) * im_pct)
    # V1061R2-01 (D16): the IM percentage and the dealer half-width are schedule terms - ASSUMED wherever they are shown or stored
    im_source = assumed_source(imp.get("source"))
    hw_source = f"the dealer half-width is a {xt.get('name') or 'prime-broker'} schedule term, ASSUMED"
    buy_ccy, sell_ccy = (base, quote) if sign > 0 else (quote, base)
    pi = _fx.pair_info(buy_ccy, sell_ccy)
    if pi["cross"]:
        base_c, quote_c = int(t["notional_c"]), to_c(A * K)
        buy_c, sell_c = (base_c, quote_c) if sign > 0 else (quote_c, base_c)
        ledger_rate = str(K)
    else:
        econ = _fx._usd_pair_economics(pi, K, K, int(t["notional_c"]), base)
        buy_c, sell_c, ledger_rate = int(econ["buy_c"]), int(econ["sell_c"]), str(econ["rate"])
    blockers = _spot_blockers(pair, sp)
    curves = _fwd_curves(services, pair, on, aid, xt)
    sens = fwd_sensitivities(curves, pair, sign, A, S, K, usd_q, sd, vd)
    labels = schedule_terms_labels(list(fwd.get("labels") or ["MODEL"]), xt)       # V1061R2-01: the half-width and IM named ASSUMED too
    try:
        checks = fxc.cross_checks(services, pair, vd, snap, fwd=fwd, pb=aid, now=now)
    except Exception as e:   # noqa: BLE001 - the cross-checks are information; their failure is said, the quote stands
        checks = {"note": f"cross-checks unavailable: {getattr(e, 'message', None) or e}"[:200]}
    broker = prime.broker_of(acct)
    days = (vd - sd).days
    record = _jsafe({k: v for k, v in fwd.items() if k not in ("F", "S", "df_base", "df_quote")})
    record.update(
        kind="FX_FORWARD", tenor=tenor, side=t["side"], notional_base_c=int(t["notional_c"]),
        dealer={"broker": config.PRIME_BROKERS.get(broker, broker) if hasattr(config, "PRIME_BROKERS") else broker, "forward_hw_bp": str(sched.forward_hw_bp),
                "otc_spread_mult": str(mult), "hw_bp": str(hw_bp), "hw_px": str(hw_px), "K": str(K), "side": t["side"],
                "schedule_source": f"{xt.get('source') or 'prime-broker schedule'}", "schedule_updated_ts": xt.get("updated_ts"),
                "rule": f"K = F {'+' if sign > 0 else '-'} {hw_bp} bp of F = {F} {'+' if sign > 0 else '-'} {hw_px} = {K} (against you)", "hw_source": hw_source},
        im={"pct": str(im_pct), "usd_notional_c": notional_c, "im_c": im_c, "usd_rate": str(usd_b),
            "rule": f"{_pct(im_pct)} x the USD notional {fmt_money(notional_c)} ({base} {fmt_money(int(t['notional_c']))} x {usd_b})", "source": im_source},
        amounts={"buy_ccy": buy_ccy, "buy_c": buy_c, "sell_ccy": sell_ccy, "sell_c": sell_c, "usd_notional_c": notional_c, "mark0_c": mark0_c,
                 "usd_per_base": str(usd_b), "usd_per_quote": str(usd_q),
                 "mark0_arithmetic": f"{'+' if sign > 0 else '-'}{fmt_money(int(t['notional_c']))} x ({F} - {K}) x {df_q} x {usd_q} = {from_c(mark0_c)}"},
        sensitivities={"pv_delta_c": sens["pv_delta"], "rate01_c": sens["rate01_c"], "basis01_c": sens["basis01_c"], "spread01_c": sens["spread01_c"],
                       "points01_c": sens["points01_c"], "valuation": f"at {td.isoformat()} for the spot date {sd.isoformat()}",
                       "basis": "each by re-pricing without intermediate rounding, USD; PV deltas in currency units"},
        cross_checks=_jsafe(checks), labels=labels,
        settlement={"value_date": vd.isoformat(), "deliver": {"ccy": sell_ccy, "amount_c": sell_c}, "receive": {"ccy": buy_ccy, "amount_c": buy_c},
                    "delivery": "at 00:30 ET on the value date through an FX deal at K, settled by SETTLE_DUE in both currencies"})
    fin = {"outright": str(F), "points": str(points), "pip": str(_fx.pip_of(pair)), "spot_date": sd.isoformat(), "value_date": vd.isoformat(), "days": days,
           "limited": False, "method": "curves", "arithmetic": (record.get("forward") or {}).get("arithmetic") or f"F = {S} x {df_b} / {df_q} = {F}", "note": ""}
    inputs = {"S": str(S), "F": str(F), "K": str(K), "days_to_value": days, "df_quote": str(df_q), "df_base": str(df_b), "notional_c": int(t["notional_c"]), "sign": sign,
              "pair": pair, "usd_per_quote": str(usd_q), "usd_per_base": str(usd_b), "valuation_date": td.isoformat(), "value_date": vd.isoformat(),
              "spot_date": sd.isoformat(), "method": "curves", "model_version": MODEL_VERSION, "labels": labels,
              "fx": {"pair": pair, "mid": str(S), "quality": sp.get("quality") or ((sp.get("legs") or [{}])[0].get("quality")), "quote_time": sp.get("quote_time"),
                     "source": sp.get("arithmetic") or ((sp.get("legs") or [{}])[0].get("source")), "basis": sp.get("basis"), "executable": sp.get("executable")},
              "forward": fin,
              "curves": {c: {k: (record.get("curves") or {}).get(c, {}).get(k) for k in ("kind", "as_of", "source", "level_bp", "spread_bp", "z_S", "z_V")}
                         for c in (base, quote)}}
    return {"kind": "FX_FORWARD", "sched": sched, "today": td, "trade_date": td, "expiry": vd, "tenor": tenor, "S": S, "F": F, "K": K, "hw_bp": hw_bp, "hw_px": hw_px,
            "mult": mult, "sign": sign, "mark0_c": mark0_c, "cost_c": abs(mark0_c), "notional_c": notional_c, "im_c": im_c, "im_pct": im_pct,
            "im_source": im_source, "hw_source": hw_source, "premium_c": 0, "inputs": inputs, "blockers": blockers, "pair": pair, "usd_q": usd_q, "usd_b": usd_b,
            "dv01_c": sens["rate01_c"].get(quote), "margin_mode": str(sched.swap_margin_mode).upper(), "buy_ccy": buy_ccy, "sell_ccy": sell_ccy, "buy_c": buy_c,
            "sell_c": sell_c, "sd": sd, "fin": fin, "ledger_rate": ledger_rate, "method": "CURVES", "record": record, "sens": sens, "points": points,
            "df_b": df_b, "df_q": df_q, "labels": labels, "sfi": dict(t.get("settlement_funding") or {}), "xt": xt}


def _fx_forward_legacy_deal(services, conn, snap, acct: dict, t: dict, now: int, request: bool) -> dict:
    """1.5: a pair with a currency outside USD / EUR / GBP - the pre-1.0.6.1 method (``fx.forward_rate``: one overnight / policy rate per
    currency, simple interest) up to 1Y, labelled LEGACY METHOD - LIMITED."""
    sched = _schedule(conn, acct)
    pair = t["pair"]
    base, quote = pair[:3], pair[3:]
    S, pm = _fx_spot(services, snap, pair, now, request)
    td = _fx.fx_trade_date(services, now)
    vd, tenor = _expiry(services, t, td, True, pair)
    sd = _fx.spot_date(services, pair, td)
    if vd <= sd:
        raise RejectError("TERMS", f"the value date {vd.isoformat()} must be after the spot date {sd.isoformat()} (a spot deal is the FX desk's)",
                          {"field": "tenor" if t.get("tenor") else "expiry"})
    F, fin = _fx.forward_rate(services, pair, vd, snap)
    if F is None:
        raise ConflictError("FORWARD_UNAVAILABLE", f"{pair} {tenor}: no forward price - {fin.get('note')}", dict(fin, field="pair"))
    mult = Decimal(sched.otc_spread_mult)
    hw_bp = (Decimal(sched.forward_hw_bp) * mult).quantize(Decimal("0.0001"))
    Kq = PX6 if "JPY" not in pair else PX4
    hw_px = (F * hw_bp / Decimal(10000)).quantize(Kq, rounding=ROUND_HALF_EVEN)
    sign = _sign(t["side"])
    K = (F + hw_px if sign > 0 else F - hw_px).quantize(Kq, rounding=ROUND_HALF_EVEN)
    rq = fin["r_quote"]
    days_d = int(fin["days"])
    df_q = fx_df(rq["value_pct"], int(rq["basis"]), days_d)
    usd_q, _ql = _usd_per(services, snap, quote, now)
    usd_b, _bl = _usd_per(services, snap, base, now)
    if usd_q is None or usd_b is None:
        raise ConflictError("NO_QUOTE", f"{pair}: no USD rate for {quote if usd_q is None else base}", {"pair": pair})
    A = from_c(int(t["notional_c"]))
    mark0_c = sign * to_c(A * (F - K) * Decimal(repr(df_q)) * usd_q)
    notional_c = to_c(A * usd_b)
    imp = _im_terms(services, acct)
    im_pct = Decimal(imp["forward_im_pct"]) if imp.get("forward_im_pct") is not None else Decimal(sched.forward_im_pct)
    im_c = to_c(from_c(notional_c) * im_pct)
    buy_ccy, sell_ccy = (base, quote) if sign > 0 else (quote, base)
    # the delivery amounts follow the FX desk's ledger convention (fx module docstring): a USD pair's USD leg is fx_c(foreign, rate)
    # at the 8-dp ledger rate (USD per unit of the foreign currency); a cross's quote leg is the base notional x K
    pi = _fx.pair_info(buy_ccy, sell_ccy)
    if pi["cross"]:
        base_c, quote_c = int(t["notional_c"]), to_c(A * K)
        buy_c, sell_c = (base_c, quote_c) if sign > 0 else (quote_c, base_c)
        ledger_rate = str(K)
    else:
        econ = _fx._usd_pair_economics(pi, K, K, int(t["notional_c"]), base)
        buy_c, sell_c, ledger_rate = int(econ["buy_c"]), int(econ["sell_c"]), str(econ["rate"])
    blockers = []
    if not pm.get("executable"):
        blockers.append(_blocker("QUOTE_STALE", f"{pair}: {str(pm.get('reason') or 'not executable').lower().replace('_', ' ')} - FX forwards are dealt in the FX"
                                 " session on a fresh print", reason=pm.get("reason")))
    rq_up = dict(rq, value_pct=str(Decimal(str(rq["value_pct"])) + Decimal("0.01")))
    F_up = cip_forward(S, fin["r_base"]["value_pct"], int(fin["r_base"]["basis"]), rq_up["value_pct"], int(rq["basis"]), days_d)
    dv01_c = sign * (to_c(A * (F_up - K) * Decimal(repr(fx_df(rq_up["value_pct"], int(rq["basis"]), days_d))) * usd_q)
                     - to_c(A * (F - K) * Decimal(repr(df_q)) * usd_q))
    inputs = {"S": str(S), "F": str(F), "K": str(K), "days_to_value": days_d, "df_quote": f"{df_q:.10f}", "notional_c": int(t["notional_c"]), "sign": sign, "pair": pair,
              "usd_per_quote": str(usd_q), "usd_per_base": str(usd_b), "valuation_date": td.isoformat(), "value_date": vd.isoformat(),
              "fx": {"pair": pair, "mid": str(S), "quality": pm.get("quality"), "quote_time": pm.get("quote_time"), "source": pm.get("source"), "basis": pm.get("basis")},
              "forward": {k: fin.get(k) for k in ("outright", "points", "arithmetic", "spot_date", "value_date", "days", "limited", "note")},
              "r_base": fin["r_base"], "r_quote": rq, "method": "legacy", "labels": ["MODEL", LEGACY_LABEL]}
    # 1.5: the legacy method's own sensitivities (re-priced on its two simple rates): the value is sign x A x (S x DF_b - K x DF_q) x USD per quote
    # unit with DF = 1 / (1 + r x d / B), so the PV deltas are +sign x A x DF_b (base) and -sign x A x K x DF_q (quote)
    rb = fin["r_base"]
    df_b = Decimal(repr(fx_df(rb["value_pct"], int(rb["basis"]), days_d)))
    sens = {"pv_delta": {base: to_c(Decimal(sign) * A * df_b), quote: -to_c(Decimal(sign) * A * K * Decimal(repr(df_q)))},
            "rate01_c": {quote: dv01_c}, "basis01_c": {}, "spread01_c": {},
            "points01_c": to_c(Decimal(sign) * A * _fx.pip_of(pair) * Decimal(repr(df_q)) * usd_q)}
    record = {"kind": "FX_FORWARD", "pair": pair, "method": f"{LEGACY_LABEL}: {fin.get('arithmetic')}", "model_version": "legacy-cip-1",
              "trade_date": td.isoformat(), "spot_date": sd.isoformat(), "value_date": vd.isoformat(), "days": days_d, "tenor": tenor, "side": t["side"],
              "spot": inputs["fx"], "forward": {"outright": str(F), "points": fin.get("points"), "pip": fin.get("pip"), "arithmetic": fin.get("arithmetic")},
              "rates": {"base": fin["r_base"], "quote": rq}, "dealer": {"forward_hw_bp": str(sched.forward_hw_bp), "otc_spread_mult": str(mult), "hw_px": str(hw_px),
                                                                           "K": str(K), "side": t["side"]},
              "labels": ["MODEL", LEGACY_LABEL], "sensitivities": {"pv_delta_c": sens["pv_delta"], "rate01_c": sens["rate01_c"], "points01_c": sens["points01_c"]}}
    return {"kind": "FX_FORWARD", "sched": sched, "today": td, "trade_date": td, "expiry": vd, "tenor": tenor, "S": S, "F": F, "K": K, "hw_bp": hw_bp, "hw_px": hw_px,
            "mult": mult, "sign": sign, "mark0_c": mark0_c, "cost_c": abs(mark0_c), "notional_c": notional_c, "im_c": im_c, "im_pct": im_pct,
            "im_source": imp.get("source"), "premium_c": 0, "inputs": inputs, "blockers": blockers, "pair": pair, "usd_q": usd_q, "usd_b": usd_b, "dv01_c": dv01_c,
            "margin_mode": str(sched.swap_margin_mode).upper(), "buy_ccy": buy_ccy, "sell_ccy": sell_ccy, "buy_c": buy_c, "sell_c": sell_c, "sd": sd, "fin": fin,
            "ledger_rate": ledger_rate, "method": "LEGACY", "record": record, "sens": sens, "points": fin.get("points"), "df_b": df_b, "df_q": Decimal(repr(df_q)),
            "labels": ["MODEL", LEGACY_LABEL], "sfi": dict(t.get("settlement_funding") or {})}


DEALS = {"EQ_OPTION": _eq_option_deal, "FX_OPTION": _fx_option_deal, "EQ_FORWARD": _eq_forward_deal, "FX_FORWARD": _fx_forward_deal}


def _side_text(kind: str, terms: dict) -> str:
    side = "bought" if terms.get("side") == "BUY" else "sold"
    if kind == "EQ_OPTION":
        return (f"{side} {int(terms.get('units') or 0):,} {terms.get('underlying_id')} {terms.get('expiry')} {_px(terms.get('strike') or 0, PX4)}"
                f" {'call' if terms.get('right') == 'C' else 'put'} ({str(terms.get('style') or '').lower()}, cash-settled)")
    if kind == "FX_OPTION":
        pair = str(terms.get("pair") or "")
        base, quote = pair[:3], pair[3:]
        what = f"{base} call / {quote} put" if terms.get("right") == "C" else f"{base} put / {quote} call"
        return f"{side} {base} {fmt_money(int(terms.get('notional_c') or 0))} {what} at {terms.get('strike')}, expiry {terms.get('expiry')} (European, cash-settled in USD)"
    if kind == "EQ_FORWARD":
        verb = "buy" if terms.get("side") == "BUY" else "sell"
        return f"{verb} {int(terms.get('units') or 0):,} {terms.get('underlying_id')} forward at {terms.get('strike')} for {terms.get('expiry')} (cash-settled)"
    pair = str(terms.get("pair") or "")
    verb = "buy" if terms.get("side") == "BUY" else "sell"
    return f"{verb} {pair[:3]} {fmt_money(int(terms.get('notional_c') or 0))} vs {pair[3:]} at {terms.get('strike')}, value {terms.get('expiry')} (delivered)"


def _deal(services, conn, snap, acct: dict, kind: str, t: dict, now: int, request: bool) -> dict:
    return DEALS[kind](services, conn, snap, acct, t, now, request)


def _store_terms(deal: dict, t: dict) -> dict:
    kind = deal["kind"]
    out = dict(t)
    out.pop("strike_pct", None)
    out.update(kind=kind, trade_date=deal["trade_date"].isoformat(), expiry=deal["expiry"].isoformat(), tenor=deal.get("tenor"),
               receiver=deal["sign"] > 0,              # the sign accounting gives the position row (a sold / short contract shows negative)
               dealt_ts=None, margin_mode=deal["margin_mode"], otc_spread_mult=str(deal["mult"]), model=MODELS[kind])
    if kind in OPTION_KINDS:
        out.update(strike=str(deal["K"]), atm=bool(t.get("atm")), strike_pct=str(t["strike_pct"]) if t.get("strike_pct") is not None else None,
                   premium_c=deal["premium_c"], premium_per_unit=str(deal["deal_px"]), mid_per_unit=str(deal["mid_px"]), sigma0=f"{deal['sigma']:.6f}",
                   deal_sigma=f"{deal['deal_sigma']:.6f}", hw_vol=str(deal["hw"]), premium_date=deal["premium_date"].isoformat(), S0=str(deal["S"]))
        if kind == "FX_OPTION":
            out.update(premium_quote_c=deal["premium_q_c"], usd_per_quote0=str(deal["usd_q"]), F0=str(deal["F"]), delivery_date=deal["vd"].isoformat())
    else:
        out.update(strike=str(deal["K"]), F0=str(deal["F"]), hw_bp=str(deal["hw_bp"]), S0=str(deal["S"]))
        if kind == "EQ_FORWARD":
            # P8B-02: F0 is the mid (SOFR carry); the dealer's forward of the side dealt and its carry are kept beside it
            out.update(financing_spread=deal["inputs"]["financing_spread"], F_deal0=str(deal["F_deal"]), carry_pct=str(deal["carry_pct"]),
                       carry_side=deal["inputs"]["carry_side"], mark_carry="SOFR")
        else:
            out.update(buy_ccy=deal["buy_ccy"], sell_ccy=deal["sell_ccy"], buy_c=deal["buy_c"], sell_c=deal["sell_c"], value_date=deal["expiry"].isoformat(),
                       ledger_rate=deal["ledger_rate"])
            # K6 (FX_CCS_PLAN 1.0.6.1): the method, the points, the discount factors and the settlement-funding instruction; the pricing record
            # itself is added at booking (book) - the preview carries it at its top level
            out.update(pricing_method=deal.get("method") or "LEGACY", points=None if deal.get("points") is None else str(deal["points"]),
                       df_base=None if deal.get("df_b") is None else str(deal["df_b"]), df_quote=None if deal.get("df_q") is None else str(deal["df_q"]),
                       spot_date=deal["sd"].isoformat(), settlement_funding=dict(deal.get("sfi") or {}), labels=list(deal.get("labels") or []))
    return out


def _record_extras(services, conn, acct: dict, deal: dict, snap) -> dict:
    """The parts of an FX forward's pricing record that read the book (D18 limits, D9 funding) and the cross-checks (D2): computed once
    per deal (the preview and the booking), stored in the record.  A legacy deal carries the limits and funding too."""
    if deal.get("kind") != "FX_FORWARD" or deal.get("_extras_done"):
        return deal.get("record") or {}
    rec = deal.setdefault("record", {})
    rec["limits"] = _limits_block(services, acct, int(deal["notional_c"]), int(deal["mark0_c"]))
    # V1061-03: built before the booking posts the IM (book() calls this first), so the quote's own IM is handed to the plan once
    rec["funding"] = _funding_block(services, acct, deal["pair"], deal["sell_ccy"], int(deal["sell_c"]), deal["expiry"], deal.get("sfi") or {},
                                    deal.get("buy_ccy"), deal.get("buy_c"), int(deal.get("im_c") or 0), deal.get("trade_date"))
    deal["_extras_done"] = True
    return rec


def _preview_dict(services, conn, acct: dict, deal: dict, t: dict, now: int) -> dict:
    kind = deal["kind"]
    sign = deal["sign"]
    terms = _store_terms(deal, t)
    out = {"kind": kind, "kind_label": swaps.KIND_LABELS.get(kind, kind), "account_id": acct["account_id"], "broker": prime.broker_of(acct),
           "side": t["side"], "side_text": _side_text(kind, terms), "trade_date": deal["trade_date"].isoformat(), "expiry": deal["expiry"].isoformat(),
           "tenor": deal.get("tenor"), "days": (deal["expiry"] - deal["trade_date"]).days, "currency": "USD", "notional_c": deal["notional_c"],
           "mark0_c": deal["mark0_c"], "cost_of_dealing_c": deal["cost_c"], "im_c": deal["im_c"], "im_pct": _pct(deal["im_pct"]),
           "margin_mode": deal["margin_mode"], "dv01_c": deal.get("dv01_c"), "inputs": deal["inputs"], "model": MODELS[kind], "terms": terms,
           "blockers": deal["blockers"], "bookable": not deal["blockers"], "conventions": list(CONVENTIONS), "quality": "computed"}
    if kind in OPTION_KINDS:
        hw_txt = _vol_pts(deal["hw"])
        out.update(strike=str(deal["K"]), right=t["right"], style=t["style"], sigma=f"{deal['sigma']:.6f}", sigma_pct=f"{deal['sigma'] * 100:.2f} %",
                   deal_sigma_pct=f"{deal['deal_sigma'] * 100:.2f} %", half_width=hw_txt, half_width_rule=f"{_vol_pts(deal['sched'].eq_option_hw_vol if kind == 'EQ_OPTION' else deal['sched'].fx_option_hw_vol)} x multiplier {deal['mult']}",
                   mid_per_unit=str(deal["mid_px"]), premium_per_unit=str(deal["deal_px"]), premium_c=deal["premium_c"], premium_date=deal["premium_date"].isoformat(),
                   premium_rule=(f"premium {'paid' if sign > 0 else 'received'} on {deal['premium_date'].isoformat()} ({'T+1' if kind == 'EQ_OPTION' else 'T+2, the spot date'});"
                                 " booked now as an OTC result, the contract's value is the overlay"),
                   greeks={k: (f"{v:.6f}" if isinstance(v, float) else v) for k, v in deal["greeks"].items()}, position_greeks={k: (round(v, 4) if isinstance(v, float) else v)
                                                                                                                            for k, v in deal["pos"].items()},
                   im_rule=(f"a sold option posts {_pct(deal['im_pct'])} of the underlying notional {fmt_money(deal['notional_c'])} = {fmt_money(deal['im_c'])}"
                            if sign < 0 else f"a bought option posts no initial margin (its premium is the most it can lose); a sale would post {_pct(deal['im_pct'])}"
                                             " of the underlying notional")
                   + f" ({deal['im_source']})",
                   price_rule=f"mid {deal['mid_px']} at {deal['sigma'] * 100:.2f} % vol; dealt {deal['deal_px']} at {deal['deal_sigma'] * 100:.2f} % ({'+' if sign > 0 else '-'}{hw_txt}, against you)")
        if kind == "EQ_OPTION":
            out.update(underlying_id=deal["underlying_id"], units=deal["units"], S=str(deal["S"]), quote=_quote_info(deal["quote"]),
                       settlement="cash at the underlying's observed close on the expiry date")
        else:
            out.update(pair=deal["pair"], notional_base_c=int(t["notional_c"]), base_ccy=deal["pair"][:3], quote_ccy=deal["pair"][3:], S=str(deal["S"]), F=str(deal["F"]),
                       premium_quote_c=deal["premium_q_c"], usd_per_quote=str(deal["usd_q"]), delivery_date=deal["vd"].isoformat(),
                       vol_label=deal["inputs"].get("sigma_kind") or "", settlement="cash in USD at the pair's observed 17:00 ET close on the expiry date")
    else:
        out.update(strike=str(deal["K"]), forward=str(deal["F"]), S=str(deal["S"]), half_width=f"{deal['hw_bp']} bp", half_width_px=str(deal["hw_px"]),
                   price_rule=f"model forward {deal['F']} {'+' if sign > 0 else '-'} {deal['hw_px']} ({deal['hw_bp']} bp, against you) = {deal['K']}",
                   im_rule=f"{_pct(deal['im_pct'])} of the USD notional {fmt_money(deal['notional_c'])} = {fmt_money(deal['im_c'])} ({deal['im_source']})", premium_c=0)
        if kind == "EQ_FORWARD":
            # P8B-02: the mid is the SOFR-carry forward; the dealt price carries the PB financing spread on the side dealt plus the half-width,
            # both against you, so the cost of dealing shows the whole charge
            sp = _pct(deal["spread"])
            out.update(underlying_id=deal["underlying_id"], units=deal["units"], quote=_quote_info(deal["quote"]), forward_deal=str(deal["F_deal"]),
                       carry_pct=str(deal["carry_pct"]), financing_spread=str(deal["spread"]),
                       price_rule=(f"mid forward {deal['F']} at SOFR {deal['inputs']['sofr_pct']} %; the dealer's forward {deal['F_deal']} at SOFR"
                                   f" {'+' if sign > 0 else '-'} {sp} (its financing spread, against you) {'+' if sign > 0 else '-'} {deal['hw_px']}"
                                   f" ({deal['hw_bp']} bp half-width) = {deal['K']}"),
                       settlement="cash: sign x units x (the underlying's observed close on the maturity date - K)")
        else:
            sd = deal["sd"]
            out.update(pair=deal["pair"], notional_base_c=int(t["notional_c"]), base_ccy=deal["pair"][:3], quote_ccy=deal["pair"][3:], value_date=deal["expiry"].isoformat(),
                       delivery={"receive": {"ccy": deal["buy_ccy"], "amount_c": deal["buy_c"]}, "pay": {"ccy": deal["sell_ccy"], "amount_c": deal["sell_c"]}},
                       settlement=(f"delivered at 00:30 ET on {deal['expiry'].isoformat()}: you receive {deal['buy_ccy']} {fmt_money(deal['buy_c'])} and pay"
                                   f" {deal['sell_ccy']} {fmt_money(deal['sell_c'])} (an FX deal at K settled by SETTLE_DUE; the sold currency must be in the account or"
                                   " Treasury by then)"),
                       spot_date=sd.isoformat(), days_from_spot=(deal["expiry"] - sd).days, method=deal.get("method") or "LEGACY", labels=list(deal.get("labels") or []),
                       points=None if deal.get("points") is None else str(deal["points"]), settlement_funding=dict(deal.get("sfi") or {}),
                       pricing_record=deal.get("record"), sensitivities=(deal.get("record") or {}).get("sensitivities"),
                       cross_checks=(deal.get("record") or {}).get("cross_checks"), limits=(deal.get("record") or {}).get("limits"),
                       funding_plan=(deal.get("record") or {}).get("funding"))
            if deal.get("method") == "CURVES":
                out["model"] = CURVES_MODEL
                out["half_width_source"] = deal.get("hw_source")          # V1061R2-01: shown beside the dealt rate on the ticket
                out["price_rule"] = (f"outright {deal['F']} = {deal['S']} x DF*_{deal['pair'][:3]} {deal['df_b']} / DF*_{deal['pair'][3:]} {deal['df_q']}"
                                     f" ({out['points']} pips); dealt {deal['F']} {'+' if sign > 0 else '-'} {deal['hw_px']} ({deal['hw_bp']} bp, against you) = {deal['K']}"
                                     + (f"; {deal['hw_source']}" if deal.get("hw_source") else ""))
            else:
                out["model"] = MODELS[kind] + " - " + LEGACY_LABEL
            try:
                chk = _fx.funding_check(services, acct["account_id"], deal["sell_ccy"], int(deal["sell_c"]), deal["expiry"], conn=conn)
                # G11: funding_check says it in ``text`` (+ its ``fixes``); the note reads it, then the settlement-funding line (never empty)
                note = chk.get("note") or chk.get("text") or ""
                if not chk.get("fundable") and chk.get("fixes"):
                    note += " - " + "; ".join(chk["fixes"])
                fl = ((deal.get("record") or {}).get("funding") or {}).get("line")
                sfi_on = (deal.get("sfi") or {}).get(deal["sell_ccy"]) == "BUY_WITH_USD"
                if sfi_on and fl:
                    # the instruction buys the delivered currency beforehand: the note leads with it, today's holding follows as information
                    note = fl + (f" (today: {note})" if note else "")
                elif fl:
                    note = (note + ". " if note else "") + fl
                out["delivery_check"] = {"fundable": bool(chk.get("fundable")), "note": note or f"{deal['sell_ccy']} {fmt_money(int(deal['sell_c']))} due {deal['expiry'].isoformat()}",
                                         "sfi": (deal.get("sfi") or {}).get(deal["sell_ccy"]), "covered_by_sfi": bool(sfi_on)}
            except Exception as e:   # noqa: BLE001 - a warning only
                out["delivery_check"] = {"fundable": None, "note": f"the delivery check could not run: {type(e).__name__}: {e}"[:160]}
    return out


def _funding(services, conn, acct: dict, deal: dict, snap) -> dict:
    """The preview's funding block: the IM (now, PO-07) and, for a bought option, the premium payable on its date - checked
    together through the same cascade, so a deal that could not be paid for is refused before anything is written."""
    aid = acct["account_id"]
    im = int(deal["im_c"])
    prem = int(deal.get("premium_c") or 0) if deal["sign"] > 0 else 0
    f = swaps.funding_preview(services, conn, aid, "USD", im + prem, snap, what="IM + premium" if prem and im else ("premium" if prem else "IM"))
    f["im_c"], f["premium_c"] = im, prem
    if prem:
        f["premium_note"] = (f"premium USD {fmt_money(prem)} is paid on {deal['premium_date'].isoformat()} from the account's cash, then one Treasury sweep"
                             " (settlement); checked now together with the IM")
    return f


def preview(services, account_id: str, kind: str, terms: dict, conn=None, now: Optional[int] = None) -> dict:
    """C21 ``otc.preview`` for the OTC options / forwards (swaps.preview ran the gate): the price with every input, the greeks, the
    dealer half-width, the IM, the premium and its date, the funding, the eligibility and the conventions.  Pure (no writes);
    raises the codes ``otc.book`` raises (409 CHAIN_FETCHING / VOL_FETCHING / NO_QUOTE carry ``retry_ms``)."""
    k = prime.require_otc_kind(kind)
    if k not in KINDS:
        raise RejectError("OTC_KIND", f"{k} is not an OTC option or forward")
    with _marks.shared_reader(services.db, conn) as c:
        acct = swaps._pb_account(c, account_id)
        t = parse_terms(k, terms)
        ts = int(now if now is not None else services.clock.epoch())
        ids = [t.get("underlying_id")] if k in ("EQ_OPTION", "EQ_FORWARD") else _fx_ids(t["pair"])
        snap = _snapshot(services, ids)
        deal = _deal(services, c, snap, acct, k, t, ts, request=True)
        _record_extras(services, c, acct, deal, snap)
        out = _preview_dict(services, c, acct, deal, t, ts)
        out["funding"] = _funding(services, c, acct, deal, snap)
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
        out["as_of"] = ts
        return out


# ---- booking ----------------------------------------------------------------------------------------------------------------------------
def _insert_instrument(services, ctx: TxContext, cid: str, name: str, maturity: date, spec: dict, underlying_id: Optional[str]) -> None:
    master = services.instruments
    insert = getattr(master, "insert", None) or getattr(master, "_insert")
    insert(ctx.conn, ctx.ts, instrument_id=cid, kind="SWAP", symbol=cid, name=name, currency="USD", multiplier="1", tick="0.01", lot=1, price_scale="1",
           session_group=swaps.SESSION_GROUP, settle_days=0, account_kinds=["PB"], expiry=maturity.isoformat(), underlying_id=underlying_id, spec=spec,
           support="COMPUTED", status="ACTIVE")
    master.refresh(cid, ctx.conn)


def book(ctx: TxContext, payload: dict) -> dict:
    """C21 ``otc.book`` for the OTC options / forwards (WP-17 checked the kind, the PB account and the eligibility first): re-prices
    inside the transaction on the command's snapshot, refuses a deal with a blocker (409, its code) or that cannot be paid for,
    funds and posts the IM, books the premium (options) with its settlement row, creates the contract and its SWAP instrument row."""
    services = ctx.services
    kind = prime.require_otc_kind(payload.get("kind"))
    conn = ctx.conn
    acct = swaps._pb_account(conn, str(payload.get("account_id") or ""))
    aid = acct["account_id"]
    t = parse_terms(kind, payload.get("terms") or {})
    deal = _deal(services, conn, ctx.marks, acct, kind, t, ctx.ts, request=False)
    if deal["blockers"]:
        b = deal["blockers"][0]
        raise ConflictError(b["code"], b["message"], dict(b.get("details") or {}, blockers=deal["blockers"]))
    fund = _funding(services, conn, acct, deal, ctx.marks)
    if not fund.get("ok", True):
        raise ConflictError("INSUFFICIENT_FUNDS", fund["note"], {"funding": fund})
    broker = prime.broker_of(acct)
    cid = next_id(conn, "otc")
    _record_extras(services, conn, acct, deal, ctx.marks)
    terms = _store_terms(deal, t)
    terms["dealt_ts"] = ctx.ts
    if kind == "FX_FORWARD" and deal.get("record"):
        terms["pricing_record"] = dict(deal["record"], booked_ts=ctx.ts)
    side_text = _side_text(kind, terms)
    entries: List[int] = []
    im_c = int(deal["im_c"])
    funding = None
    if im_c > 0:
        idem = f"im:{cid}:open"
        funding = swaps.fund_commitment(ctx, services, aid, "USD", im_c, "otc", idem)
        memo = (f"OTC IM {cid}: {side_text} with {broker}; IM = {_pct(deal['im_pct'])} x notional {fmt_money(deal['notional_c'])} = {fmt_money(im_c)}"
                f" ({deal['im_source']})")
        entries.append(ctx.post(Entry("OTC_IM", memo, [dr(aid, "1330", "USD", im_c, instrument_id=cid, memo="OTC initial margin"),
                                                       cr(aid, "1010", "USD", im_c, memo="IM paid")], idem, ref_type="otc", ref_id=cid)))
    settle_id = None
    prem = int(deal.get("premium_c") or 0)
    if kind in OPTION_KINDS and prem > 0:
        sign = deal["sign"]
        rule = f"{deal['units']:,} x {deal['deal_px']}" if kind == "EQ_OPTION" else (f"{deal['pair'][:3]} {fmt_money(int(t['notional_c']))} x {deal['deal_px']} ="
                                                                                 f" {deal['pair'][3:]} {fmt_money(deal['premium_q_c'])} at {deal['usd_q']} USD")
        memo = (f"OTC option premium {cid}: {side_text}; premium {rule} = USD {fmt_money(prem)} {'payable' if sign > 0 else 'receivable'}"
                f" {deal['premium_date'].isoformat()} (dealt at {deal['deal_sigma'] * 100:.2f} % vol, mid {deal['sigma'] * 100:.2f} %; the cost of dealing"
                f" {fmt_money(deal['cost_c'])} is the day-one result) (OTC_OPTION)")
        if sign > 0:                             # C11: the premium is the contract's cost basis (1335 paid / 2345 received), released at expiry
            lines = [dr(aid, "1335", "USD", prem, instrument_id=cid, memo="OTC option premium paid (contract cost)"),
                     cr(aid, "2100", "USD", prem, memo=f"premium payable {deal['premium_date']}")]
            settle_amt, gl = -prem, "2100"
        else:
            lines = [dr(aid, "1200", "USD", prem, memo=f"premium receivable {deal['premium_date']}"),
                     cr(aid, "2345", "USD", prem, instrument_id=cid, memo="OTC option premium received (contract cost)")]
            settle_amt, gl = prem, "1200"
        entries.append(ctx.post(Entry("OTC_PREMIUM", memo, lines, f"otc:{cid}:premium", ref_type="otc", ref_id=cid)))
        settle_id = next_id(conn, "settlement")
        conn.execute("INSERT INTO settlements(settle_id, fill_id, fx_deal_id, account_id, leg, currency, amount_c, gl, due_date, status) VALUES (?,?,?,?,?,?,?,?,?,'PENDING')",
                     (settle_id, None, None, aid, "PREMIUM", "USD", int(settle_amt), gl, deal["premium_date"].isoformat()))
        terms["premium_settle_id"] = settle_id
    uid = deal.get("underlying_id") or deal.get("pair")
    name = f"{swaps.KIND_LABELS.get(kind, kind)}: {side_text} ({broker})"
    _insert_instrument(services, ctx, cid, name, deal["expiry"], {"contract_id": cid, "otc_kind": kind, "broker": broker, "side": t["side"],
                                                                  "expiry": deal["expiry"].isoformat(), "strike": str(deal["K"])}, uid)
    inputs = dict(deal["inputs"], as_of=ctx.ts, quality="computed", note="the mid model value at the deal")
    conn.execute("INSERT INTO otc_contracts(contract_id, command_id, account_id, broker, kind, terms_json, notional_c, currency, start_date, maturity_date, margin_mode, im_c,"
                 " status, last_reset_date, reset_px, mark_c, mark_ts, vm_balance_c, inputs_json, closed_ts) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,'OPEN',NULL,?,?,?,0,?,NULL)",
                 (cid, ctx.command_id, aid, broker, kind, dumps(terms), int(deal["notional_c"]), "USD", deal["trade_date"].isoformat(), deal["expiry"].isoformat(),
                  deal["margin_mode"], im_c, str(deal["S"]), int(deal["mark0_c"]), ctx.ts, dumps(inputs)))
    ctx.touched_instruments.add((aid, cid))
    ctx.touched_accounts.add(aid)
    ctx.event("OTC_BOOKED", account_id=aid, instrument_id=cid, ref_type="otc", ref_id=cid, kind=kind, broker=broker, side=t["side"], side_text=side_text,
              notional_c=int(deal["notional_c"]), strike=str(deal["K"]), expiry=deal["expiry"].isoformat(), premium_c=prem, mark0_c=int(deal["mark0_c"]), im_c=im_c,
              margin_mode=deal["margin_mode"], settle_id=settle_id)
    view = swaps.contract_view(services, cid, conn=conn, detail=True, now_ts=ctx.ts)
    pv = _preview_dict(services, conn, acct, deal, t, ctx.ts)
    return {"contract": view, "contract_id": cid, "im_transfer": {"amount_c": im_c, "funding": funding.to_dict() if hasattr(funding, "to_dict") else funding},
            "premium": {"amount_c": prem, "direction": ("paid" if deal["sign"] > 0 else "received") if prem else None,
                        "settle_date": deal.get("premium_date").isoformat() if deal.get("premium_date") else None, "settle_id": settle_id},
            "entries": entries, "preview": pv}


# ---- valuation: live and as of a close (SWAP_VM) ---------------------------------------------------------------------------------------
def _stale(cid: str, row: dict, now: int, inputs: dict, note: str) -> MarkView:
    stored = int(row.get("mark_c") or 0)
    return MarkView(cid, from_c(stored), "stale", "OTC model (stored mark)", "swap_model", row.get("mark_ts"), now, "model", dict(inputs, qualities=["stale"], note=note),
                    False, note)


def _close_px(services, conn, snap, row: dict, terms: dict, d: date):
    """The CLOSE that values a VM of date ``d`` (LC-3, as a TRS): ``(price, info)`` or ``(None, {vm_outcome, note, quiet})``.
    Equity: the underlying's close of the last trading day on or before d; FX: the pair's 17:00 ET close (a cross from its two USD
    pairs), plus USD per unit of the base and the quote currency at that close."""
    kind = row["kind"]
    if kind in ("EQ_OPTION", "EQ_FORWARD"):
        uid = terms["underlying_id"]
        inst = services.instruments.get(uid, conn)
        if inst is None:
            return None, {"vm_outcome": "CLOSE_PENDING", "note": f"{uid} is not in the instrument master", "quiet": False}
        view = snap.mark(uid) if snap is not None else None
        return swaps_trs._vm_close(services, uid, inst.session_group, d, lambda r: swaps_trs.close_on(services, conn, snap, inst, r), view)
    pair = terms["pair"]
    base, quote = pair[:3], pair[3:]
    rates = {}
    info_all = {}
    for c in (base, quote):
        if c == "USD":
            rates[c] = Decimal(1)
            continue
        view = snap.fx(c) if snap is not None else None
        px, info = swaps_trs._vm_close(services, fx_pair_id(c), FX_GROUP, d, lambda r, c=c: swaps_trs.fx_close_on(services, conn, snap, c, r), view)
        if px is None:
            return None, info
        rates[c] = Decimal(str(info.get("usd_per_unit") or px))
        info_all[c] = info
    S = (rates[base] / rates[quote]).quantize(PX6 if "JPY" not in pair else PX4, rounding=ROUND_HALF_EVEN)
    close_date = next((i.get("close_date") for i in info_all.values() if i.get("close_date")), None)
    qt = max((int(i.get("quote_time") or 0) for i in info_all.values()), default=0) or None
    return S, {"price": str(S), "usd_per_base": str(rates[base]), "usd_per_quote": str(rates[quote]), "close_date": close_date, "quality": "close",
               "quote_time": qt, "legs": {k: {kk: vv for kk, vv in v.items() if kk in ("price", "usd_per_unit", "source", "basis", "quote_time", "close_date")}
                                          for k, v in info_all.items()}}


def _carried(row: dict) -> dict:
    inp = _inputs(row)
    return {"sigma": inp.get("sigma"), "sigma_method": inp.get("sigma_method"), "valuation_date": inp.get("valuation_date")}


def _value(services, conn, snap, row: dict, terms: dict, now: int, close: Optional[Tuple[Decimal, dict]] = None, val_date: Optional[date] = None,
           bump: Optional[dict] = None) -> Tuple[int, dict]:
    """The contract's mid model value to you in USD cents with its inputs.  ``close`` / ``val_date``: as of a close (VM); else the
    observed inputs now (stale ones allowed, labelled).  ``bump``: {sigma: +x, r_bp: +1} for greeks / termination.  Raises
    ConflictError when an input is missing."""
    kind = row["kind"]
    bump = bump or {}
    sign = 1 if terms.get("side") == "BUY" else -1
    K = Decimal(str(terms["strike"]))
    expiry = _date(terms["expiry"])
    carried = _carried(row)
    base_inputs = {"kind": kind, "K": str(K), "sign": sign, "model": MODELS[kind]}
    if kind in ("EQ_OPTION", "EQ_FORWARD"):
        uid = terms["underlying_id"]
        inst = services.instruments.get(uid, conn)
        if inst is None:
            raise ConflictError("NO_MARK", f"{uid} is not in the instrument master")
        if close is not None:
            S, uinfo = close[0], dict(close[1], instrument_id=uid)
            day = _date(uinfo.get("close_date") or val_date)
        else:
            mv = snap.mark(uid) if snap is not None else None
            if mv is None or mv.mark is None or mv.quality == "unavailable":
                raise ConflictError("NO_QUOTE", f"{uid}: no observed price ({getattr(mv, 'note', '') or 'unavailable'})")
            S, uinfo = qprice(mv.mark), dict(_quote_info(mv), instrument_id=uid)
            day = ny_of(now).date()
        days = max(0, (expiry - day).days)
        r_csa, sofr_pct, rinfo = _usd_rate(services, max(1, days))
        if bump.get("r_bp"):                       # a parallel move of SOFR: the discount rate and (forwards) the carry
            r_csa += bump["r_bp"] / 10000.0
            sofr_pct += Decimal(repr(float(bump["r_bp"]) / 100.0))
        q, qinfo = dividend_yield(conn, inst, S, day)
        q = _need_q(q, qinfo, inst)                # P8B-07: an unknown yield is no mark (the stored one, labelled stale) - never q = 0
        units = int(terms["units"])
        if kind == "EQ_OPTION":
            sigma, smethod, squality = None, None, "computed"
            chain = snap.chain(uid) if snap is not None and hasattr(snap, "chain") else None
            if chain is None:
                chains = getattr(services, "chains", None)
                chain = chains.peek(uid) if chains is not None and hasattr(chains, "peek") else None
            if chain is not None:
                cday = ny_of(int(chain.quote_time or chain.fetched_at)).date()
                if close is None or cday == day:
                    sigma, vinfo = chain_vol(chain, terms["right"], K, expiry, day)
                    smethod = f"Cboe implied vol of {uid} observed {ny_str(int(chain.quote_time or chain.fetched_at))}: {vinfo.get('method')}" if sigma is not None else None
            if sigma is None:
                if not carried.get("sigma"):
                    raise ConflictError("NO_VOL", f"{uid}: no chain observed and no earlier vol")
                sigma = float(carried["sigma"])
                smethod = f"carried from the mark of {carried.get('valuation_date') or 'the booking'} ({carried.get('sigma_method') or ''}); no {uid} chain observed" + (
                    f" on {day.isoformat()}" if close is not None else " now")
                squality = "carried"
            sigma += float(bump.get("sigma") or 0.0)
            px = eq_price(terms["right"], terms.get("style") or "EUROPEAN", S, K, days, r_csa, float(q), sigma)
            value_c = sign * to_c(Decimal(units) * Decimal(repr(px)).quantize(PX4, rounding=ROUND_HALF_EVEN))
            inputs = dict(base_inputs, S=str(S), days=days, T=f"{days / 365.0:.6f}", r=f"{r_csa:.6f}", q=str(q), sigma=f"{sigma:.6f}", sigma_method=smethod,
                          sigma_quality=squality, units=units, price_per_unit=f"{px:.4f}", valuation_date=day.isoformat(), underlying=uinfo, rate=rinfo, dividend=qinfo,
                          formula=f"{'+' if sign > 0 else '-'}{units:,} x {px:.4f} ({'call' if terms['right'] == 'C' else 'put'} {K}, {days} days, vol {sigma * 100:.2f} %)")
            return value_c, inputs
        spread = Decimal(str(terms.get("financing_spread") or "0"))
        T = days / 365.0
        # P8B-02: the mark carries at SOFR (the funding-neutral mid); the PB financing spread was charged once, at the deal, on the side dealt
        F = S * Decimal(repr(math.exp((r_csa - float(q)) * T)))
        df = math.exp(-r_csa * T)
        value_c = sign * to_c(Decimal(units) * (F - K) * Decimal(repr(df)))
        inputs = dict(base_inputs, S=str(S), F=str(F.quantize(PX4)), days=days, T=f"{T:.6f}", r_csa=f"{r_csa:.6f}", r_mid=f"{r_csa:.6f}", q=str(q),
                      sofr_pct=str(sofr_pct), financing_spread=str(spread), df=f"{df:.10f}", units=units, valuation_date=day.isoformat(), underlying=uinfo,
                      rate=rinfo, dividend=qinfo, formula=f"{'+' if sign > 0 else '-'}{units:,} x (F {F.quantize(PX4)} - K {K}) x DF {df:.6f} (F at the SOFR carry)")
        return value_c, inputs
    # FX kinds
    pair = terms["pair"]
    base, quote = pair[:3], pair[3:]
    if kind == "FX_FORWARD" and str(terms.get("pricing_method") or "").upper() == "CURVES":
        return _curve_fwd_value(services, conn, snap, row, terms, now, close=close, val_date=val_date, bump=bump)
    if close is not None:
        S = close[0]
        usd_q = Decimal(str(close[1].get("usd_per_quote") or "1"))
        usd_b = Decimal(str(close[1].get("usd_per_base") or "1"))
        day = _date(close[1].get("close_date") or val_date)
        fxinfo = dict(close[1], pair=pair)
    else:
        S, pm = _fx_spot(services, snap, pair, now, False)
        usd_q, _ql = _usd_per(services, snap, quote, now)
        usd_b, _bl = _usd_per(services, snap, base, now)
        if usd_q is None or usd_b is None:
            raise ConflictError("NO_QUOTE", f"{pair}: no USD rate")
        day = _fx.fx_trade_date(services, now)
        fxinfo = {"pair": pair, "mid": str(S), "quality": pm.get("quality"), "quote_time": pm.get("quote_time"), "source": pm.get("source"), "basis": pm.get("basis")}
    vd = _date(terms.get("delivery_date") or terms.get("value_date") or terms["expiry"])
    sd = _fx.spot_date(services, pair, day)
    days_d = max(0, (vd - sd).days)
    rb_pct, rb = _fx.rate_for(services, base, max(1, days_d))
    rq_pct, rq = _fx.rate_for(services, quote, max(1, days_d))
    if rb_pct is None or rq_pct is None:
        raise ConflictError("RATE_UNAVAILABLE", f"{pair}: {(rb if rb_pct is None else rq).get('note')}")
    rbd = dict(rb, value_pct=str(rb_pct))
    rqd = dict(rq, value_pct=str(Decimal(str(rq_pct)) + Decimal(repr(float(bump.get("r_bp") or 0) / 100.0))))
    F = cip_forward(S, rbd["value_pct"], int(rbd["basis"]), rqd["value_pct"], int(rqd["basis"]), days_d) if days_d > 0 else S
    df_q = fx_df(rqd["value_pct"], int(rqd["basis"]), days_d)
    A = from_c(int(terms["notional_c"]))
    if kind == "FX_OPTION":
        t_days = max(0, (expiry - day).days)
        sigma, vinfo = _fx_vol(services, snap, pair, expiry, day, now, False, carried=carried if carried.get("sigma") else None,
                               day_only=day if close is not None else None)
        if sigma is None:
            if not carried.get("sigma"):
                raise ConflictError("NO_VOL", f"{pair}: {vinfo.get('note')}")
            sigma, vinfo = float(carried["sigma"]), {"method": f"carried from {carried.get('valuation_date') or 'the booking'}", "kind": "carried"}
        sigma += float(bump.get("sigma") or 0.0)
        px = black_df(terms["right"], float(F), float(K), t_days / 365.0, sigma, df_q)
        value_q = Decimal(repr(px)).quantize(PX6, rounding=ROUND_HALF_EVEN) * A
        value_c = sign * to_c(value_q * usd_q)
        inputs = dict(base_inputs, S=str(S), F=str(F), days_to_delivery=days_d, days_to_expiry=t_days, T=f"{t_days / 365.0:.6f}", df_quote=f"{df_q:.10f}",
                      sigma=f"{sigma:.6f}", sigma_method=vinfo.get("method"), sigma_kind=vinfo.get("kind"), notional_c=int(terms["notional_c"]), pair=pair,
                      usd_per_quote=str(usd_q), valuation_date=day.isoformat(), fx=fxinfo, r_base=rbd, r_quote=rqd, price_per_unit=f"{px:.6f}",
                      formula=f"{'+' if sign > 0 else '-'}{pair[:3]} {fmt_money(int(terms['notional_c']))} x {px:.6f} {quote} x {usd_q} USD/{quote}")
        return value_c, inputs
    value_c = sign * to_c(A * (F - K) * Decimal(repr(df_q)) * usd_q)
    inputs = dict(base_inputs, S=str(S), F=str(F), days_to_value=days_d, df_quote=f"{df_q:.10f}", notional_c=int(terms["notional_c"]), pair=pair, usd_per_quote=str(usd_q),
                  valuation_date=day.isoformat(), fx=fxinfo, r_base=rbd, r_quote=rqd,
                  formula=f"{'+' if sign > 0 else '-'}{pair[:3]} {fmt_money(int(terms['notional_c']))} x (F {F} - K {K}) x DF {df_q:.6f} x {usd_q} USD/{quote}")
    return value_c, inputs


def _curve_fwd_value(services, conn, snap, row: dict, terms: dict, now: int, close=None, val_date=None, bump: Optional[dict] = None) -> Tuple[int, dict]:
    """3.2 / D19: a curve-priced FX forward's value to you in USD cents for the spot date S_t of the valuation date - sign x A x (F_t - K) x
    DF*_quote(S_t, V) x USD per quote unit, F_t and the discount factors quantised as at the quote - on the curves on or before the
    valuation date (live: now, the derived cross / the pair's print; VM: the 17:00 ET closes of the VM date, the cross derived from its two
    USD pairs).  Once the spot date reaches the value date the discount factors run backwards (DF(S_t, V) = 1 / DF(V, S_t), D19).  ``bump``
    {r_bp: x}: the quote currency's discount curve moved by x bp (``dv01_c``).  Raises ConflictError when an input is missing (the mark is
    then the stored one, labelled stale; the VM waits and retries)."""
    fxc = _fxc()
    cc = _ccycal()
    sign = 1 if terms.get("side") == "BUY" else -1
    K = Decimal(str(terms["strike"]))
    vd = _date(terms.get("value_date") or terms["expiry"])
    A = from_c(int(terms["notional_c"]))
    pair = terms["pair"]
    base, quote = pair[:3], pair[3:]
    if close is not None:
        S = Decimal(close[0])
        usd_q = Decimal(str(close[1].get("usd_per_quote") or "1"))
        usd_b = Decimal(str(close[1].get("usd_per_base") or "1"))
        day = _date(close[1].get("close_date") or val_date)
        on, td = day, day
        fxinfo = dict(close[1], pair=pair)
    else:
        sp = fxc.spot(services, pair, snap, now)
        if sp.get("mid") is None:
            raise ConflictError("NO_QUOTE", f"{pair}: no observed rate ({sp.get('reason') or 'unavailable'})")
        S = Decimal(sp["mid"])
        usd_q, _ql = _usd_per(services, snap, quote, now)
        usd_b, _bl = _usd_per(services, snap, base, now)
        if usd_q is None or usd_b is None:
            raise ConflictError("NO_QUOTE", f"{pair}: no USD rate")
        td, on = _fx.fx_trade_date(services, now), ny_of(now).date()
        day = td
        legs = sp.get("legs") or [{}]
        fxinfo = {"pair": pair, "mid": str(S), "quality": legs[0].get("quality") if len(legs) == 1 else _fx._worst(*(lg.get("quality") for lg in legs)),
                  "quote_time": sp.get("quote_time"), "source": sp.get("arithmetic") or legs[0].get("source"), "basis": sp.get("basis"),
                  "executable": sp.get("executable")}
    sd = cc.spot_date(services, pair, td)
    acct = account_row(conn, row["account_id"])
    xt = _xccy_terms(services, conn, dict(acct) if acct is not None else {"account_id": row["account_id"]})
    curves = _fwd_curves(services, pair, on, row["account_id"], xt)
    if bump and bump.get("r_bp"):
        curves = dict(curves, **{quote: fxc.shifted(curves[quote], Decimal(str(bump["r_bp"])))})
    b_raw, q_raw = _dfs(curves, pair, sd, vd)
    df_b, df_q = _q10(b_raw), _q10(q_raw)
    F = fwd_outright(S, df_b, df_q, pair)
    value_c = fwd_value_c(sign, A, F, K, df_q, usd_q)
    labels = ["MODEL"] + [lab for c in (base, quote) for lab in (curves[c].labels or [])]
    inputs = {"kind": "FX_FORWARD", "K": str(K), "sign": sign, "model": CURVES_MODEL, "method": "curves", "S": str(S), "F": str(F), "df_base": str(df_b),
              "df_quote": str(df_q), "spot_date": sd.isoformat(), "days_to_value": (vd - sd).days, "notional_c": int(terms["notional_c"]), "pair": pair,
              "usd_per_quote": str(usd_q), "usd_per_base": str(usd_b), "valuation_date": day.isoformat(), "fx": fxinfo, "labels": list(dict.fromkeys(labels)),
              "curves": {c: {"as_of": curves[c].d0.isoformat(), "kind": curves[c].kind, "level_bp": str(curves[c].level_bp)} for c in (base, quote)},
              "formula": (f"{'+' if sign > 0 else '-'}{base} {fmt_money(int(terms['notional_c']))} x (F {F} - K {K}) x DF*_{quote} {df_q} x {usd_q} USD/{quote}"
                          f" (F = {S} x {df_b} / {df_q}, for the spot date {sd.isoformat()})")}
    return value_c, inputs


def _forward_sensitivities(services, row: dict, terms: dict, snapshot) -> Optional[dict]:
    """K6: an open FX forward's PV deltas and rate / basis / points sensitivities NOW, by re-pricing (curves) or on the legacy method's two
    simple rates; None when the terms carry no pair."""
    pair = str(terms.get("pair") or "")
    if len(pair) != 6:
        return None
    sign = 1 if terms.get("side") == "BUY" else -1
    K = Decimal(str(terms["strike"]))
    A = from_c(int(terms["notional_c"]))
    vd = _date(terms.get("value_date") or terms["expiry"])
    base, quote = pair[:3], pair[3:]
    snap = _snap_for(services, row, terms, snapshot)
    now = int(services.clock.epoch())
    usd_q, _ql = _usd_per(services, snap, quote, now)
    if usd_q is None:
        raise ConflictError("NO_QUOTE", f"{pair}: no USD rate for {quote}")
    day = _fx.fx_trade_date(services, now)
    if str(terms.get("pricing_method") or "").upper() == "CURVES":
        fxc = _fxc()
        cc = _ccycal()
        sp = fxc.spot(services, pair, snap, now)
        if sp.get("mid") is None:
            raise ConflictError("NO_QUOTE", f"{pair}: no observed rate ({sp.get('reason') or 'unavailable'})")
        S = Decimal(sp["mid"])
        sd = cc.spot_date(services, pair, day)
        with _marks.shared_reader(services.db) as conn:
            acct = account_row(conn, row["account_id"])
            xt = _xccy_terms(services, conn, dict(acct) if acct is not None else {"account_id": row["account_id"]})
        curves = _fwd_curves(services, pair, ny_of(now).date(), row["account_id"], xt)
        s = fwd_sensitivities(curves, pair, sign, A, S, K, usd_q, sd, vd)
        labels = ["MODEL"] + [lab for c in (base, quote) for lab in (curves[c].labels or [])]
        return {"fx_delta": s["pv_delta"], "rate01_c": s["rate01_c"], "basis01_c": s["basis01_c"], "spread01_c": s["spread01_c"], "points01_c": s["points01_c"],
                "labels": list(dict.fromkeys(labels)), "valuation_date": day.isoformat(), "spot_date": sd.isoformat()}
    # the legacy method (1.5): DF = 1 / (1 + r x d / B) per currency, d from today's spot date
    sd = _fx.spot_date(services, pair, day)
    days = max(0, (vd - sd).days)
    rb_pct, rb = _fx.rate_for(services, base, max(1, days))
    rq_pct, rq = _fx.rate_for(services, quote, max(1, days))
    if rb_pct is None or rq_pct is None:
        raise ConflictError("RATE_UNAVAILABLE", f"{pair}: {(rb if rb_pct is None else rq).get('note')}")
    dfb = Decimal(repr(fx_df(rb_pct, int(rb["basis"]), days)))
    dfq = Decimal(repr(fx_df(rq_pct, int(rq["basis"]), days)))
    dfq1 = Decimal(repr(fx_df(Decimal(str(rq_pct)) + Decimal("0.01"), int(rq["basis"]), days)))
    S, _pm = _fx_spot(services, snap, pair, now, False)
    v0 = Decimal(sign) * A * (S * dfb / dfq - K) * dfq * usd_q
    v1 = Decimal(sign) * A * (S * dfb / dfq1 - K) * dfq1 * usd_q
    return {"fx_delta": {base: to_c(Decimal(sign) * A * dfb), quote: -to_c(Decimal(sign) * A * K * dfq)}, "rate01_c": {quote: to_c(v1 - v0)}, "basis01_c": {},
            "points01_c": to_c(Decimal(sign) * A * _fx.pip_of(pair) * dfq * usd_q), "labels": ["MODEL", LEGACY_LABEL],
            "notes": [f"{row.get('contract_id')}: {LEGACY_LABEL}; no basis level, rate01 of the quote currency only"]}


def _snap_for(services, row: dict, terms: dict, snapshot):
    """The snapshot to value a contract on: the caller's when it holds this contract's names, else a small one of them (a view's
    snapshot holds only the TRS / XCCY names it collected)."""
    ids = [terms.get("underlying_id")] if row["kind"] in ("EQ_OPTION", "EQ_FORWARD") else _fx_ids(terms.get("pair") or "")
    if snapshot is not None and hasattr(snapshot, "quote") and all(snapshot.quote(i) is not None for i in ids if i):
        return snapshot
    return _snapshot(services, ids) or snapshot


def mark(services, contract, at: Optional[int] = None, valuation_date=None, snapshot=None):
    """C21 mark hook: ``(cents, inputs)`` = the mid model value to you (module docstring MARK / VM); a stale ``MarkView`` with the
    stored mark when an input is missing now; with ``valuation_date`` (SWAP_VM) the value as of the close of the last trading day on
    or before it, or ``(None, {vm_outcome: CLOSE_PENDING | CAUGHT_UP, note, quiet})`` when that close is not known (LC-3), or
    ``(None, {note})`` when another input is missing (the VM skips NO_MARK and retries)."""
    row = swaps._contract_dict(services, contract)
    if row is None or row["kind"] not in KINDS:
        return None
    cid = row["contract_id"]
    now = int(at if at is not None else services.clock.epoch())
    terms = _terms(row)
    base = {"as_of": now, "contract_id": cid, "kind": row["kind"], "model": MODELS[row["kind"]], "quality": "computed"}
    if row.get("status") != "OPEN":
        return 0, dict(base, note=f"{row.get('status')}: settled")
    snap = _snap_for(services, row, terms, snapshot)
    with _marks.shared_reader(services.db) as conn:
        if valuation_date is not None:
            vd = _date(valuation_date)
            close = _close_px(services, conn, snap, row, terms, vd)
            if close[0] is None:
                return None, dict(base, valuation_date=vd.isoformat(), **close[1])
            try:
                value_c, inputs = _value(services, conn, snap, row, terms, now, close=close, val_date=vd)
            except (ConflictError, RejectError) as e:
                return None, dict(base, valuation_date=vd.isoformat(), note=e.message)
            return value_c, dict(base, **inputs, note=f"valued at the close of {inputs.get('valuation_date')}")
        try:
            value_c, inputs = _value(services, conn, snap, row, terms, now)
        except (ConflictError, RejectError) as e:
            return _stale(cid, row, now, dict(base), f"{e.message}; the last computed mark, labelled stale")
    return value_c, dict(base, **inputs, note=inputs.get("formula") or "")


def dv01_c(services, contract) -> int:
    """C21: the signed value change for +1 bp of the discounting rate (USD SOFR; FX: the quote currency's rate), from the inputs of
    the last stored mark when the live inputs are not at hand - raises ConflictError (NO_MARK) when nothing can be valued.

    A PB forward on the FX term curves (3.2) answers its rate01 on the quote currency's curve from ``_forward_sensitivities``, re-priced
    without intermediate rounding: the number its ticket showed (the preview's ``dv01_c``) and the Risk page's rate01 for that currency
    (OTC-09: the contracts table and drawer say it is that currency's rate01 and point to Risk > Currency risk (FX)).  The difference of
    two rounded marks is not used for it: F moves by about 0.000174 for +1 bp on the worked example and its 6 dp rounding alone can move
    that difference by up to 2,000,000 x 0.0000005 x 0.9095 x 1.3323 = 1.21 USD (-421.51 there against the rate01 -420.57)."""
    row = swaps._contract_dict(services, contract)
    if row is None or row.get("status") != "OPEN" or row["kind"] not in KINDS:
        return 0
    terms = _terms(row)
    if row["kind"] == "FX_FORWARD" and str(terms.get("pricing_method") or "").upper() == "CURVES":
        quote = str(terms.get("pair") or "")[3:]
        try:
            sens = _forward_sensitivities(services, row, terms, None)
        except (ConflictError, RejectError) as e:
            raise ConflictError("NO_MARK", f"{row['contract_id']}: {e.message}") from e
        got = ((sens or {}).get("rate01_c") or {}).get(quote)
        if got is None:
            raise ConflictError("NO_MARK", f"{row['contract_id']}: no rate sensitivity on the {quote or 'quote currency'} curve now")
        return int(got)
    snap = _snap_for(services, row, terms, None)
    with _marks.shared_reader(services.db) as conn:
        try:
            v0, _i = _value(services, conn, snap, row, terms, int(services.clock.epoch()))
            v1, _j = _value(services, conn, snap, row, terms, int(services.clock.epoch()), bump={"r_bp": 1.0})
        except (ConflictError, RejectError) as e:
            raise ConflictError("NO_MARK", f"{row['contract_id']}: {e.message}") from e
    return int(v1 - v0)


# ---- termination ------------------------------------------------------------------------------------------------------------------------
def terminate(ctx: TxContext, payload: dict) -> dict:
    """C21 ``otc.terminate`` (WP-17 checked the contract is OPEN): close out at the model value now with the dealer's half-width
    against you (module docstring TERMINATION) - VM unwound, IM released (``swaps.close_contract``)."""
    services = ctx.services
    conn = ctx.conn
    cid = str(payload.get("contract_id") or "")
    r = conn.execute("SELECT * FROM otc_contracts WHERE contract_id=?", (cid,)).fetchone()
    if r is None:
        raise NotFound("NOT_FOUND", f"contract {cid} does not exist", {"contract_id": cid})
    row = {k: r[k] for k in r.keys()}
    if row["status"] != "OPEN":
        raise ConflictError("NOT_OPEN", f"{cid} is {row['status']}; only an OPEN contract can be terminated", {"contract_id": cid, "status": row["status"]})
    acct = account_row(conn, row["account_id"])
    if acct is None or acct["status"] != "OPEN":
        raise RejectError("ACCOUNT_CLOSED", f"account {row['account_id']} is not open")
    terms = _terms(row)
    kind = row["kind"]
    sign = 1 if terms.get("side") == "BUY" else -1
    snap = _snap_for(services, row, terms, ctx.marks)
    mid_c, inputs = _value(services, conn, snap, row, terms, ctx.ts)
    _require_observed(services, kind, inputs, terms)
    sched = prime.read_schedule(conn, acct)
    mult = Decimal(sched.otc_spread_mult)
    if kind in OPTION_KINDS:
        hw = float((Decimal(sched.eq_option_hw_vol if kind == "EQ_OPTION" else sched.fx_option_hw_vol) * mult).quantize(SIG_Q))
        # you sell a bought option back at sigma - hw, buy a sold one back at sigma + hw: both lower your value
        value_c, inputs_hw = _value(services, conn, snap, row, terms, ctx.ts, bump={"sigma": -hw if sign > 0 else hw})
        rule = f"model at {float(inputs['sigma']) * 100:.2f} % {'-' if sign > 0 else '+'} {hw * 100:.2f} vol pts (the dealer's half-width, against you)"
    else:
        hw_bp = (Decimal(sched.forward_hw_bp) * mult).quantize(Decimal("0.0001"))
        F = Decimal(str(inputs["F"]))
        hw_px = F * hw_bp / Decimal(10000)
        if kind == "EQ_FORWARD":
            # P8B-02: the close-out deals the opposite side on the dealer's carry for it (a long is sold back at SOFR - spread, a short bought
            # back at SOFR + spread) and its half-width, as a new deal would: value = sign x units x (K_close - K) x DF
            close = -sign
            spread = Decimal(sched.financing_spread)
            carry = eq_forward_carry(inputs["sofr_pct"], spread, close)
            F_close, _r = eq_forward_px(Decimal(str(inputs["S"])), carry, Decimal(str(inputs["q"])), int(inputs["days"]))
            hw_close = (F_close * hw_bp / Decimal(10000)).quantize(PX4, rounding=ROUND_HALF_EVEN)
            K_close = F_close + close * hw_close
            value_c = sign * to_c(Decimal(int(terms["units"])) * (K_close - Decimal(str(terms["strike"]))) * Decimal(inputs["df"]))
            rule = (f"closed at {K_close}: the dealer's forward {F_close} at SOFR {'+' if close > 0 else '-'} {_pct(spread)} (its financing spread, against you)"
                    f" {'+' if close > 0 else '-'} {hw_close} ({hw_bp} bp half-width), against K {terms['strike']}")
        else:
            value_c = mid_c - to_c(from_c(int(terms["notional_c"])) * hw_px * Decimal(inputs["df_quote"]) * Decimal(inputs["usd_per_quote"]))
            rule = f"model forward {inputs['F']} {'-' if sign > 0 else '+'} {hw_bp} bp (the dealer's half-width, against you)"
    ref_c = swaps._reference_mark_c(conn, cid)
    pending = conn.execute("SELECT settle_id, due_date, amount_c FROM settlements WHERE account_id=? AND status='PENDING' AND settle_id=?",
                           (row["account_id"], terms.get("premium_settle_id") or "")).fetchone()
    memo = (f"OTC termination {cid}: {_side_text(kind, terms)}; value {fmt_money(value_c)} = {rule} (mid {fmt_money(mid_c)}); VM balance"
            f" {fmt_money(int(row['vm_balance_c']))} unwound; IM released" + (f"; the premium settlement {pending['settle_id']} still settles on {pending['due_date']}" if pending else ""))
    closed = swaps.close_contract(ctx, services, row, "TERMINATED", value_c, ref_c, f"otc:{cid}:terminate", "OTC_TERMINATE", memo, False, False)
    ctx.event("OTC_TERMINATED", account_id=row["account_id"], instrument_id=cid, ref_type="otc", ref_id=cid, kind=kind, termination_value_c=value_c, mid_c=mid_c,
              **{k: v for k, v in closed.items() if k not in ("funding", "value_c")})
    view = swaps.contract_view(services, cid, conn=conn, detail=True, now_ts=ctx.ts)
    entries = [e for e in (closed["entry_id"], closed["im_entry_id"]) if e is not None]
    return {"contract": view, "contract_id": cid, "termination_value_c": value_c, "mid_value_c": mid_c, "result_c": closed["result_c"], "cash_c": closed["cash_c"],
            "entries": entries, "im_released_c": closed["im_released_c"], "accrued_receive_c": 0, "accrued_pay_c": 0, "inputs": inputs, "rule": rule,
            "funding": closed.get("funding"), "cost_released_c": closed.get("cost_released_c", 0), "realised_c": closed.get("realised_c")}


def _require_observed(services, kind: str, inputs: dict, terms: dict) -> None:
    """A termination deals now: the underlying / pair must be a fresh print or the session close (409 QUOTE_STALE otherwise)."""
    q = (inputs.get("underlying") or {}).get("quality") if kind in ("EQ_OPTION", "EQ_FORWARD") else (inputs.get("fx") or {}).get("quality")
    if q not in USABLE:
        what = terms.get("underlying_id") or terms.get("pair")
        raise ConflictError("QUOTE_STALE", f"{what}: the last price is {q or 'unavailable'}; a termination needs a fresh print or the session close", {"quality": q})


# ---- expiry / maturity (job OTC_EXPIRY) ---------------------------------------------------------------------------------------------
def _job_done(ctx: TxContext, key: str, status: str, note: str) -> None:
    ctx.conn.execute("INSERT INTO jobs_done(job, key, run_ts, command_id, status, note) VALUES (?,?,?,?,?,?)"
                     " ON CONFLICT(job, key) DO UPDATE SET run_ts=excluded.run_ts, command_id=excluded.command_id, status=excluded.status, note=excluded.note",
                     (JOB_EXPIRY, key, ctx.ts, ctx.command_id, status, (note or "")[:500]))


def _skip(ctx: TxContext, key: str, code: str, message: str, aid: str, cid: str, quiet: bool) -> dict:
    _job_done(ctx, key, "SKIPPED", f"{code}: {message}")
    attention_id = None
    if not quiet:
        ex = ctx.conn.execute("SELECT id FROM attention WHERE code=? AND ref_id=? AND resolved_ts IS NULL", (code, key)).fetchone()
        attention_id = int(ex["id"]) if ex else ctx.attention("WARN", SOURCE, code, message, {"account_id": aid, "ref_type": "otc", "ref_id": key, "auto_resolve": 1})
    ctx.event("JOB_SKIPPED", account_id=aid, instrument_id=cid, ref_type="job", ref_id=key, job=JOB_EXPIRY, code=code, message=message)
    return {"status": "SKIPPED", "skipped": True, "retry": True, "quiet": quiet, "code": code, "message": message, "note": f"{code}: {message}", "key": key,
            "attention_id": attention_id, "posted": False}


def _payoff_c(kind: str, terms: dict, S: Decimal, close_info: dict) -> Tuple[int, str]:
    """The settlement value to you in USD cents at the observed close ``S`` and its arithmetic."""
    sign = 1 if terms.get("side") == "BUY" else -1
    K = Decimal(str(terms["strike"]))
    if kind == "EQ_OPTION":
        units = int(terms["units"])
        intrinsic = max(Decimal(0), S - K) if terms["right"] == "C" else max(Decimal(0), K - S)
        return sign * to_c(Decimal(units) * intrinsic), f"{'+' if sign > 0 else '-'}{units:,} x max(0, {'S - K' if terms['right'] == 'C' else 'K - S'}) = {units:,} x {intrinsic}"
    if kind == "EQ_FORWARD":
        units = int(terms["units"])
        return sign * to_c(Decimal(units) * (S - K)), f"{'+' if sign > 0 else '-'}{units:,} x ({S} - {K})"
    pair = terms["pair"]
    A = from_c(int(terms["notional_c"]))
    usd_q = Decimal(str(close_info.get("usd_per_quote") or "1"))
    intrinsic = max(Decimal(0), S - K) if terms["right"] == "C" else max(Decimal(0), K - S)
    return (sign * to_c(A * intrinsic * usd_q),
            f"{'+' if sign > 0 else '-'}{pair[:3]} {fmt_money(int(terms['notional_c']))} x max(0, {'S - K' if terms['right'] == 'C' else 'K - S'}) = {intrinsic}"
            f" {pair[3:]} per unit x {usd_q} USD/{pair[3:]}")


def h_expire(ctx: TxContext, payload: dict) -> dict:
    """``otc.expire {contract_id, date}`` (job OTC_EXPIRY): settle an option / forward at its expiry / maturity (module docstring
    EXPIRY / MATURITY).  Idempotent: a closed contract is a DONE no-op."""
    services = ctx.services
    conn = ctx.conn
    cid, ds = swaps._key(payload, "otc.expire")
    key = f"{cid}:{ds}"
    r = conn.execute("SELECT * FROM otc_contracts WHERE contract_id=?", (cid,)).fetchone()
    if r is None:
        raise NotFound("NOT_FOUND", f"contract {cid} does not exist", {"contract_id": cid})
    row = {k: r[k] for k in r.keys()}
    if row["kind"] not in KINDS:
        raise RejectError("OTC_KIND", f"{cid} is a {row['kind']}: swaps mature through their own jobs")
    if row["status"] != "OPEN":
        _job_done(ctx, key, "DONE", f"NOT_OPEN: {cid} is {row['status']}")
        return {"status": "DONE", "skipped": True, "posted": False, "code": "NOT_OPEN", "note": f"{cid} is {row['status']}", "key": key}
    if ds != row["maturity_date"]:
        raise RejectError("KEY_INVALID", f"{cid} expires on {row['maturity_date']}, not {ds}")
    d = date.fromisoformat(ds)
    et = _expiry_et(row["kind"])
    if ctx.ts < epoch_of(d, et):
        return _skip(ctx, key, "NOT_YET", f"{cid} settles from {et} ET on {ds}", row["account_id"], cid, True)
    terms = _terms(row)
    aid = row["account_id"]
    kind = row["kind"]
    ref_c = swaps._reference_mark_c(conn, cid)
    entries: List[int] = []
    if kind == "FX_FORWARD":
        # G12 (FX_CCS_PLAN D3 / 4.1): delivered at 00:30 ET of the value date with no price input - the contract closes at 0 (its value passes
        # into the delivery deal at K), its VM balance is unwound and its IM released; SETTLE_DUE settles the deal's two legs in the same tick
        S, info = None, {}
        value_c = 0
        memo = (f"OTC FX forward delivery {cid}: {_side_text(kind, terms)}; delivered at the dealt rate K {terms.get('strike')}: receive {terms.get('buy_ccy')}"
                f" {fmt_money(int(terms.get('buy_c') or 0))}, pay {terms.get('sell_ccy')} {fmt_money(int(terms.get('sell_c') or 0))}, value {ds} (00:30 ET; no price"
                f" input - the contract's value passes into the delivery deal, the P&L since the last VM is in the FX result of its legs); VM balance"
                f" {fmt_money(int(row['vm_balance_c']))} unwound; IM released (OTC_FORWARD)")
    else:
        snap = _snap_for(services, row, terms, ctx.marks)
        S, info = _close_px(services, conn, snap, row, terms, d)
        if S is None:
            prev = conn.execute("SELECT note FROM jobs_done WHERE job=? AND key=?", (JOB_EXPIRY, key)).fetchone()
            repeat = prev is not None and str(prev["note"] or "").startswith("CLOSE_PENDING")
            return _skip(ctx, key, "CLOSE_PENDING", f"{cid}: {info.get('note') or 'the close is not known yet'}; the settlement waits for the observed close of {ds}"
                         " (never an earlier or intraday price)", aid, cid, quiet=not repeat)
        if info.get("close_date") and info["close_date"] != ds:
            return _skip(ctx, key, "CLOSE_PENDING", f"{cid}: the close available is of {info['close_date']}, not {ds}", aid, cid, quiet=False)
        value_c, arith = _payoff_c(kind, terms, S, info)
        memo = (f"OTC {'maturity' if kind == 'EQ_FORWARD' else 'expiry'} {cid}: {_side_text(kind, terms)}; observed close {_px(S, PX6 if kind in FX_KINDS else PX4)}"
                f" ({info.get('basis') or info.get('source') or 'close'} {info.get('close_date') or ds}); value {arith} = {fmt_money(value_c)}; VM balance"
                f" {fmt_money(int(row['vm_balance_c']))} unwound; IM released (OTC_OPTION / OTC_FORWARD)")
    closed = swaps.close_contract(ctx, services, row, "MATURED", value_c, ref_c, f"otc:{cid}:expire", "OTC_EXPIRY", memo, False, True)
    entries += [e for e in (closed["entry_id"], closed["im_entry_id"]) if e is not None]
    delivery = None
    if kind == "FX_FORWARD":
        delivery = _deliver(ctx, services, row, terms)
        if delivery.get("entry_id"):
            entries.append(delivery["entry_id"])
    _job_done(ctx, key, "DONE", (f"{kind} delivered at K {terms.get('strike')}" if kind == "FX_FORWARD" else f"{kind} settled at {S}") + f": value {fmt_money(value_c)}")
    ctx.conn.execute("UPDATE attention SET resolved_ts=? WHERE code='CLOSE_PENDING' AND ref_id=? AND resolved_ts IS NULL", (ctx.ts, key))
    ctx.event("OTC_EXPIRED", account_id=aid, instrument_id=cid, ref_type="otc", ref_id=cid, kind=kind, close=None if S is None else str(S), value_c=value_c,
              delivery=delivery, **{k: v for k, v in closed.items() if k not in ("funding", "value_c")})
    return {"status": "DONE", "posted": True, "key": key, "contract_id": cid, "close": None if S is None else str(S), "value_c": value_c, "entries": entries,
            "delivery": delivery, "result_c": closed["result_c"], "cash_c": closed["cash_c"], "im_released_c": closed["im_released_c"], "close_info": info}


def _expiry_et(kind: str) -> str:
    """The time OTC_EXPIRY settles a kind on its date: FX forwards 00:30 ET (G12), FX options 17:05 ET, equity 16:20 ET."""
    return EXPIRY_ET.get(str(kind), EXPIRY_ET["FX" if kind in FX_KINDS else "EQ"])


def _deliver(ctx: TxContext, services, row: dict, terms: dict) -> dict:
    """The value-date exchange of a deliverable FX forward: a linked FX deal (kind FORWARD, dealt at K) posted at once with its two
    settlement legs due today (``fx.post_forward``); SETTLE_DUE settles both, funding the sold currency as the FX desk does."""
    conn = ctx.conn
    aid = row["account_id"]
    pair = terms["pair"]
    pi = _fx.pair_info(terms["buy_ccy"], terms["sell_ccy"])
    deal_id = next_id(conn, "fx_deal")
    K = Decimal(str(terms["strike"]))
    rate = str(terms.get("ledger_rate") or K)
    if pi["cross"]:
        mids = {}
        for c in (pi["buy_ccy"], pi["sell_ccy"]):
            mids[c] = str(ctx.fx.rate(c))
        extra = {"cross": True, "usd_mids_at_trade": mids}
    else:
        extra = {"cross": False, "foreign_ccy": pi["foreign"]}
    inputs = dict(extra, pair=pair, rate_quoted=str(K), link={"type": "otc", "id": row["contract_id"], "text": f"delivery of {row['contract_id']}"},
                  note=f"deliverable OTC FX forward {row['contract_id']} with {row['broker']}")
    conn.execute("INSERT INTO fx_deals(deal_id, command_id, account_id, kind, buy_ccy, sell_ccy, buy_c, sell_c, rate, mid, spread_cost_c, quote_json, trade_ts,"
                 " trade_date, value_date, status, settled_ts, entry_id, inputs_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'PENDING',NULL,0,?)",
                 (deal_id, ctx.command_id, aid, "FORWARD", terms["buy_ccy"], terms["sell_ccy"], int(terms["buy_c"]), int(terms["sell_c"]), rate, str(K), 0,
                  dumps({"pair": pair, "source": "OTC forward", "contract_id": row["contract_id"]}), int(terms.get("dealt_ts") or ctx.ts), terms.get("trade_date") or ctx.today,
                  ctx.today, dumps(inputs)))
    out = _fx.post_forward(ctx, deal_id, services=services, force=True)
    return {"deal_id": deal_id, "entry_id": out.get("entry_id"), "settlements": out.get("settlements"), "buy_ccy": terms["buy_ccy"], "buy_c": int(terms["buy_c"]),
            "sell_ccy": terms["sell_ccy"], "sell_c": int(terms["sell_c"]), "rate_quoted": str(K)}


def next_expiry_due(conn, now_ts: int) -> Optional[Tuple[int, str]]:
    """P8B-15: ``(epoch, ref)`` of the next OTC_EXPIRY work - the earliest expiry / maturity / delivery of an OPEN option or forward not yet
    DONE (equity from 16:20 ET, FX from 17:05 ET; an overdue one is due now) - or None when no such contract is open."""
    now = int(now_ts)
    best: Optional[Tuple[int, str]] = None
    rows = conn.execute("SELECT c.contract_id, c.kind, c.maturity_date FROM otc_contracts c JOIN accounts a ON a.account_id=c.account_id JOIN lifetimes l"
                        " ON l.lifetime_id=a.lifetime_id WHERE c.status='OPEN' AND a.status='OPEN' AND l.status='OPEN'"
                        f" AND c.kind IN ({','.join('?' for _ in KINDS)}) ORDER BY c.maturity_date, c.contract_id", KINDS).fetchall()
    for r in rows:
        key = f"{r['contract_id']}:{r['maturity_date']}"
        done = conn.execute("SELECT status FROM jobs_done WHERE job=? AND key=?", (JOB_EXPIRY, key)).fetchone()
        if done is not None and done["status"] == "DONE":
            continue
        ts = max(now, epoch_of(date.fromisoformat(r["maturity_date"]), _expiry_et(r["kind"])))
        what = {"FX_FORWARD": "delivery", "EQ_FORWARD": "maturity"}.get(r["kind"], "expiry")
        if best is None or ts < best[0]:
            best = (ts, f"{r['contract_id']} {what} {r['maturity_date']}")
    return best


def expiry_keys(conn, now_ts: int) -> List[Tuple[str, str]]:
    """``(contract_id, date)`` of every OPEN option / forward of an OPEN account and lifetime whose expiry / maturity is today or
    earlier and whose settlement time has passed (equity 16:20 ET, FX options 17:05 ET, FX forwards 00:30 ET), not DONE in ``jobs_done``."""
    now = int(now_ts)
    today = ny_of(now).date()
    rows = conn.execute("SELECT c.contract_id, c.kind, c.maturity_date FROM otc_contracts c JOIN accounts a ON a.account_id=c.account_id JOIN lifetimes l"
                        " ON l.lifetime_id=a.lifetime_id WHERE c.status='OPEN' AND a.status='OPEN' AND l.status='OPEN' AND c.maturity_date<=?"
                        f" AND c.kind IN ({','.join('?' for _ in KINDS)}) ORDER BY c.maturity_date, c.contract_id", (today.isoformat(), *KINDS)).fetchall()
    out = []
    for r in rows:
        d = date.fromisoformat(r["maturity_date"])
        if now < epoch_of(d, _expiry_et(r["kind"])):
            continue
        key = f"{r['contract_id']}:{r['maturity_date']}"
        done = conn.execute("SELECT status FROM jobs_done WHERE job=? AND key=?", (JOB_EXPIRY, key)).fetchone()
        if done is not None and done["status"] == "DONE":
            continue
        out.append((r["contract_id"], r["maturity_date"]))
    return out


# ---- views ------------------------------------------------------------------------------------------------------------------------------
def next_events(services, contract, conn=None, limit: int = 4) -> List[dict]:
    """C21: the premium settlement (while pending) and the expiry / maturity / delivery with what happens then."""
    if conn is None:
        with _marks.shared_reader(services.db) as c:
            return next_events(services, contract, c, limit)
    row = swaps._contract_dict(services, contract, conn)
    if row is None or row.get("status") != "OPEN" or row["kind"] not in KINDS:
        return []
    terms = _terms(row)
    out = []
    sid = terms.get("premium_settle_id")
    if sid:
        s = conn.execute("SELECT due_date, amount_c, status FROM settlements WHERE settle_id=?", (sid,)).fetchone()
        if s is not None and s["status"] == "PENDING":
            out.append({"date": s["due_date"], "event": "PREMIUM", "amount_c": int(s["amount_c"]),
                        "detail": f"premium USD {fmt_money(abs(int(s['amount_c'])))} {'paid' if int(s['amount_c']) < 0 else 'received'} (settlement {sid})"})
    kind = row["kind"]
    if kind == "FX_FORWARD":
        sfi = terms.get("settlement_funding") or {}
        sell = terms.get("sell_ccy")
        if sell and sell != "USD" and sfi.get(sell) == "BUY_WITH_USD":
            # D9: the purchase trades two business days of the purchase pair's value calendar (TARGET2 + New York for EUR) before the value date
            buy_on = row["maturity_date"]
            try:
                from ..instruments import fx_pair_id
                buy_on = _ccycal().value_calendar(services, fx_pair_id(sell)).add_business_days(_date(row["maturity_date"]), -2).isoformat()
            except Exception:   # noqa: BLE001 - the event still shows, dated on the value date
                pass
            out.append({"date": buy_on, "event": "SETTLEMENT_FUNDING",
                        "detail": f"buy {sell} {fmt_money(int(terms.get('sell_c') or 0))} with USD (value {row['maturity_date']}), an audited FX desk spot deal"
                                  " (settlement-funding instruction)"})
        out.append({"date": row["maturity_date"], "event": "DELIVERY", "detail": f"00:30 ET: receive {terms.get('buy_ccy')} {fmt_money(int(terms.get('buy_c') or 0))}, pay"
                                                                                 f" {terms.get('sell_ccy')} {fmt_money(int(terms.get('sell_c') or 0))} at {terms.get('strike')}"})
    elif kind == "EQ_FORWARD":
        out.append({"date": row["maturity_date"], "event": "MATURITY", "detail": f"cash: units x ({terms.get('underlying_id')} close - {terms.get('strike')})"})
    else:
        out.append({"date": row["maturity_date"], "event": "EXPIRY", "detail": "cash-settled at the observed close" + (" (17:00 ET)" if kind == "FX_OPTION" else "")})
    return out[:max(1, int(limit))]


def contract_detail(services, contract, conn=None) -> Optional[dict]:
    """C21: what the contracts table and its drawer show for these kinds: side and side_text, the underlying / pair, the strike /
    forward, the expiry, the premium and its settlement, the dealt vol and the model."""
    if conn is None:
        with _marks.shared_reader(services.db) as c:
            return contract_detail(services, contract, c)
    row = swaps._contract_dict(services, contract, conn)
    if row is None or row["kind"] not in KINDS:
        return None
    terms = _terms(row)
    kind = row["kind"]
    side = ("LONG_" if terms.get("side") == "BUY" else "SHORT_") + ("CALL" if terms.get("right") == "C" else "PUT" if terms.get("right") == "P" else "FORWARD")
    out = {"kind": kind, "side": side, "side_text": _side_text(kind, terms), "underlying_id": terms.get("underlying_id"), "pair": terms.get("pair"),
           "right": terms.get("right"), "style": terms.get("style"), "strike": terms.get("strike"), "expiry": terms.get("expiry"), "units": terms.get("units"),
           "notional_base_c": terms.get("notional_c"), "model": MODELS[kind], "trade_date": terms.get("trade_date")}
    if kind in OPTION_KINDS:
        sid = terms.get("premium_settle_id")
        s = conn.execute("SELECT due_date, amount_c, status, entry_id FROM settlements WHERE settle_id=?", (sid,)).fetchone() if sid else None
        out.update(premium_c=terms.get("premium_c"), premium_per_unit=terms.get("premium_per_unit"), mid_per_unit=terms.get("mid_per_unit"),
                   premium_date=terms.get("premium_date"), premium_status=s["status"] if s is not None else None, premium_entry_id=s["entry_id"] if s is not None else None,
                   dealt_vol_pct=f"{float(terms.get('deal_sigma') or 0) * 100:.2f} %", mid_vol_pct=f"{float(terms.get('sigma0') or 0) * 100:.2f} %",
                   half_width=_vol_pts(terms.get("hw_vol") or 0))
    else:
        out.update(forward_at_deal=terms.get("F0"), half_width=f"{terms.get('hw_bp')} bp", delivery={"receive": [terms.get("buy_ccy"), terms.get("buy_c")],
                                                                                                     "pay": [terms.get("sell_ccy"), terms.get("sell_c")]}
                   if kind == "FX_FORWARD" else None)
        if kind == "FX_FORWARD":
            out.update(fx_im_base(row, terms))
            out.update(confirmation=_confirmation(conn, row, terms), pricing_record=terms.get("pricing_record"), method=terms.get("pricing_method") or "LEGACY",
                       labels=terms.get("labels") or (terms.get("pricing_record") or {}).get("labels") or (["MODEL", LEGACY_LABEL]),
                       settlement_funding=terms.get("settlement_funding") or {}, spot_date=terms.get("spot_date"), points=terms.get("points"))
    return out


def _confirmation(conn, row: dict, terms: dict) -> dict:
    """K5 / 4.1 / 10.7: the FX forward's confirmation block - parties, dates, amounts, rate, CSA, IM and the settlement instructions in trader
    words; a simulated confirmation (never "dealer-confirmed")."""
    acct = account_row(conn, row["account_id"])
    a = dict(acct) if acct is not None else {}
    broker = config.PRIME_BROKERS.get(row.get("broker"), row.get("broker")) if hasattr(config, "PRIME_BROKERS") else row.get("broker")
    status = str(row.get("confirm_status") or "") if "confirm_status" in row.keys() else ""
    words = "CONFIRMED - simulated confirmation" if status.upper().startswith("CONFIRM") else "PENDING - the simulated confirmation follows at 17:05 ET"
    buy, sell = terms.get("buy_ccy"), terms.get("sell_ccy")
    vd = terms.get("value_date") or row.get("maturity_date")
    sfi = (terms.get("settlement_funding") or {}).get(sell)
    sell_line = (f"the Fund delivers {sell} {fmt_money(int(terms.get('sell_c') or 0))} on {vd} from its {broker} prime-brokerage {sell} cash"
                 + (" (bought with USD under the settlement-funding instruction two business days before)" if sfi == "BUY_WITH_USD" else ""))
    buy_line = f"{broker} delivers {buy} {fmt_money(int(terms.get('buy_c') or 0))} on {vd} into the Fund's {broker} prime-brokerage {buy} cash"
    return {"words": words, "status": status or None,
            "parties": {"fund": f"{a.get('name') or row['account_id']} ({row['account_id']})", "counterparty": f"{broker} (prime broker), ISDA Master + CSA + PBA"},
            "trade_date": terms.get("trade_date"), "spot_date": terms.get("spot_date"), "value_date": vd,
            "deal": f"{'BUY' if terms.get('side') == 'BUY' else 'SELL'} {terms.get('pair', '')[:3]} {fmt_money(int(terms.get('notional_c') or 0))} / "
                    f"{'SELL' if terms.get('side') == 'BUY' else 'BUY'} {terms.get('pair', '')[3:]} at {terms.get('strike')} (forward {terms.get('F0')}, {terms.get('points') or '?'} pips)",
            "rate": terms.get("strike"), "csa": f"VM daily in USD ({terms.get('margin_mode') or 'CTM'}) on the contract's value; IM in USD held by {broker}",
            "im": f"USD {fmt_money(int(row.get('im_c') or 0))}" if int(row.get("im_c") or 0) else "released",
            "settlement_instructions": [sell_line, buy_line, "delivered at 00:30 ET on the value date through an FX deal at the dealt rate, settled by SETTLE_DUE"],
            "labels": terms.get("labels") or ["MODEL"]}


def fx_im_base(row, terms: dict) -> dict:
    """P8R1-07: what the USD notional of an FX forward is - the base notional at the USD rate of the base currency WHEN DEALT (the spot
    mid of a USD pair, EUR/USD 1.1141; a cross's base against USD), the IM's base; never the dealt forward the deal delivers at.  The rate
    is the stored USD notional over the stored base notional (the booking computed one from the other: exact to the cent), so a contract
    booked before this field existed reads the same.  ``{im_base_rate, im_base_pair}``; empty when either notional is missing."""
    try:
        base_c, usd_c = int(terms.get("notional_c") or 0), int(row.get("notional_c") or 0)
    except (TypeError, ValueError):
        return {}
    pair = str(terms.get("pair") or "")
    if base_c <= 0 or usd_c <= 0 or len(pair) != 6:
        return {}
    rate = (Decimal(usd_c) / Decimal(base_c)).quantize(Decimal("0.000001"), rounding=ROUND_HALF_EVEN)
    text = f"{rate:.6f}".rstrip("0").rstrip(".")
    return {"im_base_rate": text, "im_base_pair": f"{pair[:3]}/USD"}


# ---- K6 hooks (FX_CCS_PLAN 7): the scheduled flows and the off-ledger risk of an OTC FX forward ---------------------------------------
def _row_dict(row) -> Optional[dict]:
    if row is None:
        return None
    if isinstance(row, dict):
        return row
    try:
        return {k: row[k] for k in row.keys()}
    except Exception:   # noqa: BLE001 - not a row
        return None


def scheduled_flows(conn, row) -> List[dict]:
    """K6: the flows a contract has scheduled and not yet posted - an OPEN FX_FORWARD's two delivery legs on its value date
    ``[{date, ccy, amount_c (+ in / - out), what, event, known, contract_id}]``; nothing for another kind or once delivered (the
    delivery deal is then on the ledger).  ``conn`` is unused (the terms carry the amounts) and kept for the common signature."""
    r = _row_dict(row)
    if r is None or str(r.get("kind") or "") != "FX_FORWARD" or str(r.get("status") or "OPEN") != "OPEN":
        return []
    t = _terms(r)
    vd = str(t.get("value_date") or r.get("maturity_date") or t.get("expiry") or "")[:10]
    cid = r.get("contract_id")
    out = []
    for ccy, amt, sign, verb in ((t.get("buy_ccy"), t.get("buy_c"), 1, "receive"), (t.get("sell_ccy"), t.get("sell_c"), -1, "deliver")):
        if not ccy or amt in (None, ""):
            continue
        out.append({"date": vd, "ccy": str(ccy), "amount_c": sign * abs(int(amt)), "event": "DELIVERY", "flow_kind": "delivery", "known": True, "contract_id": cid,
                    "what": f"{verb} {ccy} {fmt_money(abs(int(amt)))}: delivery of the {t.get('pair') or ''} forward {cid} at {t.get('strike')}, value {vd}"})
    return out


def risk_sensitivities(services, row, snapshot=None) -> Optional[dict]:
    """K6 / D14: ONLY the off-ledger part of an OPEN FX_FORWARD's risk (the ledger holds nothing of it until delivery):
    ``{fx_nominal {ccy: units_c} (both legs), fx_delta {ccy: units_c} (the PV deltas), rate01_c {ccy: usd_c}, basis01_c {pair: usd_c},
    points01_c, notes}``; after delivery (or for another kind) None.  Sensitivities that cannot be re-priced now are left out and named
    in ``notes`` - never estimated."""
    r = _row_dict(row)
    if r is None or str(r.get("kind") or "") != "FX_FORWARD" or str(r.get("status") or "OPEN") != "OPEN":
        return None
    t = _terms(r)
    nominal: Dict[str, int] = {}
    for ccy, amt, sign in ((t.get("buy_ccy"), t.get("buy_c"), 1), (t.get("sell_ccy"), t.get("sell_c"), -1)):
        if ccy and amt not in (None, ""):
            nominal[str(ccy)] = nominal.get(str(ccy), 0) + sign * abs(int(amt))
    out = {"contract_id": r.get("contract_id"), "kind": "FX_FORWARD", "pair": t.get("pair"), "fx_nominal": nominal, "fx_delta": {}, "rate01_c": {},
           "basis01_c": {}, "points01_c": None, "notes": []}
    try:
        sens = _forward_sensitivities(services, r, t, snapshot)
    except (ConflictError, RejectError) as e:
        out["notes"].append(f"{r.get('contract_id')}: sensitivities unavailable now - {e.message}")
        return out
    except Exception as e:   # noqa: BLE001 - a hook never breaks the Risk page; named
        out["notes"].append(f"{r.get('contract_id')}: sensitivities unavailable now - {type(e).__name__}: {e}"[:240])
        return out
    if sens:
        out.update(sens)
    return out



# ---- the desk (GET /api/otc/options/desk) and the inputs refresher -------------------------------------------------------------------
def desk(services, account_id: str) -> dict:
    """What the OTC tab needs once: the relationship (agreements), the eligibility, the PB's dealing and IM terms, the tenors, pairs
    and vol sources."""
    with _marks.shared_reader(services.db) as conn:
        acct = account_row(conn, account_id)
        if acct is None:
            raise NotFound("ACCOUNT_UNKNOWN", f"account {account_id} does not exist", {"account_id": account_id})
        acct = dict(acct)
        if acct["kind"] != "PB":
            raise RejectError("NOT_TRADEABLE_HERE", f"{account_id} is not a prime-broker account", {"account_id": account_id})
        rel = prime.relationship(conn, account_id)
        sched = prime.read_schedule(conn, acct)
    try:
        elig = _eligibility(services, split_account_id(account_id)[0]).to_dict()
    except Exception as e:   # noqa: BLE001
        elig = {"error": str(e)[:200]}
    terms = {k: str(getattr(sched, k)) for k in prime.OTC_TERM_FIELDS}
    terms.update(otc_spread_mult=str(sched.otc_spread_mult), swap_margin_mode=sched.swap_margin_mode, financing_spread=str(sched.financing_spread))
    # FX_CCS_PLAN D5: the cross-currency terms (basis levels, EUR OIS spread, the swap half-width) - ASSUMED schedule terms, shown on the OTC tab
    for k in getattr(prime, "XCCY_TERM_FIELDS", ()):
        if getattr(sched, k, None) is not None:
            terms[k] = str(getattr(sched, k))
    pairs = [p for p in FX_PAIRS if services.instruments.get(p) is not None] + [p for p in FX_CROSSES if services.instruments.get(p) is not None]
    curves_status = _curves_status(services)
    vol_source = {}
    for p in pairs:
        base, quote = p[:3], p[3:]
        foreign = quote if base == "USD" else (base if quote == "USD" else None)
        etf = FX_PROXY.get(foreign) if foreign else None
        vol_source[p] = f"proxy: {etf} at-the-money implied vol" if etf else "realised: 30-day vol of the pair (no free implied vol)"
    # P8B-04 / P8B-01: a pair is offered for forwards (and FX options, which price off the forward) only while both of its currencies have a
    # usable rate (fx.rate_for: a series that exists and is still updating); the others are spot only, each with its reason
    rates_of: Dict[str, Tuple[Optional[Decimal], dict]] = {}
    spot_only: Dict[str, str] = {}
    for p in pairs:
        for c in (p[:3], p[3:]):
            if c not in rates_of:
                rates_of[c] = _fx.rate_for(services, c)      # every currency's rate status (the fixed / fixed swap ticket reads it)
        if curve_pair(p):
            continue                         # FX_CCS_PLAN D3: priced on the term curves; a curve that is unavailable now refuses its preview, with the reason
        for c in (p[:3], p[3:]):
            pct, info = rates_of[c]
            if pct is None:
                spot_only[p] = f"spot only: {info.get('note') or 'no ' + c + ' rate'}"
                break
    rates = {c: {"ok": pct is not None, "label": info.get("label") if pct is not None else None, "note": info.get("note") if pct is None else None,
                 "as_of": info.get("as_of"), "age_days": (info.get("age") or {}).get("age_days"), "series": info.get("series")}
             for c, (pct, info) in sorted(rates_of.items())}
    blocked = None
    if not (elig.get("pb_otc") and elig.get("ecp")):
        blocked = elig.get("pb_otc_reason") or "this book is not eligible for OTC bookings"
    return {"account_id": account_id, "broker": rel["broker"], "name": rel["name"], "relationship": rel, "eligibility": elig, "blocked": blocked,
            "terms": terms, "labels": {k: prime.FIELD_SPECS[k]["label"] for k in prime.OTC_TERM_FIELDS}, "tenors": list(TENORS),
            "fx_forward_tenors": list(tenors_for("FX_FORWARD")), "fx_forward_tenors_legacy": list(tenors_for("FX_FORWARD", "USDJPY")),
            "forward_methods": {p: ("curves" if curve_pair(p) else "legacy") for p in pairs}, "legacy_label": LEGACY_LABEL, "curves": curves_status,
            "pairs": pairs, "forward_pairs": [p for p in pairs if p not in spot_only],
            "spot_only": spot_only, "rates": rates,
            "vol_source": vol_source, "kinds": [{"kind": k, "label": swaps.KIND_LABELS.get(k, k)} for k in KINDS],
            "conventions": {k: tables.CONVENTIONS.get(k, "") for k in CONVENTIONS + ("SWAP_VM",)}}


def _curves_status(services) -> Dict[str, dict]:
    """The FX term curves of USD / EUR / GBP as the desk shows them: ``{ccy: {ok, kind, as_of, label | note}}`` (a curve that cannot be built
    now says why; its pairs' previews refuse with that reason - never another rate)."""
    out: Dict[str, dict] = {}
    try:
        on = _fx.fx_trade_date(services, int(services.clock.epoch()))
    except Exception:   # noqa: BLE001
        on = None
    for c in CURVE_CCYS:
        try:
            cv = _curve_of(services, c, on, None)
            d0 = getattr(cv, "d0", None)
            out[c] = {"ok": True, "kind": getattr(cv, "kind", None), "as_of": d0.isoformat() if isinstance(d0, date) else d0,
                      "label": (list(getattr(cv, "labels", None) or []) or [None])[0], "source": getattr(cv, "source", None)}
        except (ConflictError, RejectError) as e:
            out[c] = {"ok": False, "note": e.message, "code": e.code}
        except Exception as e:   # noqa: BLE001 - the desk still opens; the reason is said
            out[c] = {"ok": False, "note": f"{type(e).__name__}: {e}"[:200]}
    return out


def reference_ids(conn) -> Dict[str, List[str]]:
    """``{instrument_id: [kinds]}`` the OPEN options / forwards observe: the equity underlyings, the FX pairs and the USD pairs of
    their currencies, and (``chain:`` prefixed) the chains their vols come from."""
    out: Dict[str, List[str]] = {}
    for r in conn.execute("SELECT c.kind, c.terms_json FROM otc_contracts c JOIN accounts a ON a.account_id=c.account_id JOIN lifetimes l ON l.lifetime_id=a.lifetime_id"
                          f" WHERE c.status='OPEN' AND a.status='OPEN' AND l.status='OPEN' AND c.kind IN ({','.join('?' for _ in KINDS)})", KINDS).fetchall():
        try:
            t = json.loads(r["terms_json"] or "{}")
        except ValueError:
            continue
        if t.get("underlying_id"):
            out.setdefault(t["underlying_id"], []).append(r["kind"])
            if r["kind"] == "EQ_OPTION":
                out.setdefault("chain:" + t["underlying_id"], []).append(r["kind"])
        if t.get("pair"):
            for i in _fx_ids(t["pair"]):
                out.setdefault(i, []).append(r["kind"])
            if r["kind"] == "FX_OPTION":
                base, quote = t["pair"][:3], t["pair"][3:]
                foreign = quote if base == "USD" else (base if quote == "USD" else None)
                if foreign in FX_PROXY:
                    out.setdefault("chain:" + FX_PROXY[foreign], []).append(r["kind"])
    return out


def refresh(services, now: int):
    """C14 refresher (the quote worker's fetch threads): the Yahoo daily closes previews asked for (realised vols), the HELD
    subscription of the names OPEN contracts observe, and once each trading day after 16:20 ET the chains behind their vols."""
    st = _state(services)
    providers = getattr(services, "providers", None)
    for sym in st.take():
        try:
            bars = providers.yahoo.daily(sym, 45) if providers is not None else []
            closes = [(b.date, Decimal(str(b.c))) for b in bars if b.c is not None]
            with st.lock:
                st.rv[sym] = {"closes": closes, "fetched_at": int(now), "error": None}
        except Exception as e:   # noqa: BLE001 - recorded and said on the next preview
            with st.lock:
                st.rv[sym] = {"closes": None, "fetched_at": int(now), "error": str(e)[:160]}
    db = getattr(services, "db", None)
    if db is None:
        return None
    with db.reader() as conn:
        refs = reference_ids(conn)
    names = [i for i in refs if not i.startswith("chain:")]
    cache = getattr(services, "cache", None)
    if names and cache is not None and hasattr(cache, "subscribe"):
        cache.subscribe(names, 900, "HELD")
    chains = [i[6:] for i in refs if i.startswith("chain:")]
    day = ny_of(int(now)).date()
    sessions = getattr(services, "sessions", None)
    if chains and sessions is not None and st.chains_day != day.isoformat() and int(now) >= epoch_of(day, EXPIRY_ET["EQ"]):
        try:
            trading = sessions.is_trading_day(EQ_OPTION_GROUP, day)
        except Exception:   # noqa: BLE001
            trading = False
        cc = getattr(services, "chains", None)
        if trading and cc is not None:
            for uid in chains:
                cc.request(uid)
            st.chains_day = day.isoformat()
    return None


# ---- routes (C1), unsupported rows, wiring -----------------------------------------------------------------------------------------
def register_routes(router) -> None:
    """C1: ``POST /api/otc/preview`` for every OTC kind (the swaps keep their preview; these kinds come here through swaps.preview's
    gate - the earlier route answered only IRS / TRS / XCCY); ``GET /api/otc/options/desk?account_id=``."""
    from ..api import ApiError, NotFound as _ApiNotFound, need

    def otc_preview(req):
        rt = req.rt
        sw = need(rt, "swaps")
        kind = str(req.b("kind") or "").strip().upper()
        if not kind:
            raise ApiError(400, "BAD_REQUEST", "kind is required: " + ", ".join(prime.OTC_LIVE))
        aid = str(req.b("account_id") or "")
        with _marks.shared_reader(rt.db) as conn:
            if account_row(conn, aid) is None:
                raise _ApiNotFound(f"account {aid} does not exist")
        try:
            return sw.preview(rt, aid, kind, dict(req.b("terms") or {}))
        except ConflictError as e:
            # an input being fetched for this preview (the Cboe chain, the Yahoo daily closes, a first quote): 202 + retry_ms like every
            # fetch-triggering read of the API, never an error - the ticket asks again by itself
            retry = (e.details or {}).get("retry_ms")
            if not retry:
                raise
            return 202, dict(e.details or {}, status="fetching", pending=True, code=e.code, message=e.message, retry_ms=int(retry)), {}

    def desk_get(req):
        aid = str(req.q("account_id") or "")
        if not aid:
            raise ApiError(400, "ACCOUNT_REQUIRED", "account_id is required")
        try:
            return desk(req.rt, aid)
        except NotFound as e:
            raise _ApiNotFound(e.message, e.details)

    router.add("POST", r"/api/otc/preview", otc_preview, mutating=False)
    router.add("GET", r"/api/otc/options/desk", desk_get)


def unsupported() -> List[dict]:
    return [{"item": "Forward rate agreements (FRAs) with a prime broker", "state": "NOT_OFFERED",
             "wording": "no free forward-rate fixings to settle them against; an interest-rate swap covers the rate view", "where": "prime-broker OTC tab"},
            {"item": "FX options on crosses and on currencies without a CurrencyShares ETF", "state": "LIMITED",
             "wording": "priced on the pair's 30-day realised volatility from Yahoo daily closes (no free implied FX volatility), said on the ticket",
             "where": "prime-broker OTC tab, Options"},
            {"item": "American-style FX options; barrier, digital and other exotic options", "state": "NOT_OFFERED",
             "wording": "FX options are European (Garman-Kohlhagen); exotics need inputs that are not free", "where": "prime-broker OTC tab"},
            {"item": "Forwards, FX options and cross-currency legs in a currency whose rate has stopped updating (CHF)", "state": "LIMITED",
             "wording": ("a daily rate older than 10 days, or a monthly average more than 62 days after its month, is never used: the price is refused with"
                         " the rate's date and age, and the pair is listed spot only (CHF's free series stopped in 2024-03)"),
             "where": "prime-broker OTC tab, FX desk forwards"},
            {"item": "OTC equity forwards and options on a name without dividend data", "state": "LIMITED",
             "wording": ("a name with no Yahoo dividend events over a year and no yield in the instrument master is not priced: its dividend yield is"
                         " unknown and never assumed to be 0"), "where": "prime-broker OTC tab, Options and Forwards"},
            # FX_CCS_PLAN 1.5 / D3 (1.0.6.1): what the PB forward on the term curves does not cover
            {"item": "PB FX forwards of a pair with a currency outside USD, EUR and GBP", "state": "LIMITED",
             "wording": ("priced by the legacy method up to 1 year: one overnight / policy rate per currency, simple interest, no term structure, no"
                         " cross-currency basis (labelled LEGACY METHOD - LIMITED); 18 months and 2 years are refused - no term curve for that currency in"
                         " this build"), "where": "prime-broker OTC tab, Forwards"},
            {"item": "PB FX forwards beyond 2 years", "state": "NOT_OFFERED",
             "wording": "the forward is offered up to the 2-year date of its pair (the term curves and the dealer's terms are set up to it)",
             "where": "prime-broker OTC tab, Forwards"}]


def register(bus, services=None) -> None:
    bus.register("otc.expire", h_expire)


def bind(rt) -> None:
    """C13: the OTC_EXPIRY job before SWAP_VM (an expiring contract settles before that day's VM); C14: the inputs refresher."""
    try:
        from ..lifecycle_eod import ProductJob, add_product_job
        add_product_job(ProductJob(JOB_EXPIRY, "otc.expire", "otc_options", "expiry_keys", ("contract_id", "date"),
                                   "the expiry / maturity date: equity from 16:20 ET, FX options from 17:05 ET, FX forwards delivered from 00:30 ET of the value date",
                                   lambda conn, clock, cal, sessions, s: (conn, clock.epoch())),
                        before="SWAP_VM")
    except ImportError:
        pass
    try:
        from ..lifecycle_eod import add_next_due
        add_next_due(JOB_EXPIRY, next_expiry_due)              # P8B-15: GET /api/jobs shows when it next has work
    except ImportError:
        pass
    try:
        from ..quotes.worker import register_refresher
        register_refresher("otc_options", 15, refresh, label="OTC option inputs (Yahoo daily closes, Cboe chains)")
    except ImportError:
        pass


__all__ = ["KINDS", "preview", "book", "terminate", "mark", "dv01_c", "next_events", "contract_detail", "expiry_keys", "h_expire", "register", "bind",
           "register_routes", "unsupported", "desk", "reference_ids", "refresh", "parse_terms", "chain_vol", "realised_vol", "eq_price", "black_df", "cip_forward",
           "dividend_yield", "JOB_EXPIRY", "MODELS", "CONVENTIONS", "FX_PROXY", "tenors_for", "eq_forward_px", "eq_forward_carry", "next_expiry_due",
           "scheduled_flows", "risk_sensitivities", "curve_pair", "fwd_outright", "fwd_value_c", "fwd_sensitivities", "OTC_FX_TENORS", "LEGACY_LABEL",
           "CURVE_CCYS", "EXPIRY_ET"]
