"""Bonds: the Debt Instruments marketplace engine (Build 2 P07, 2026-10-21; WP-10 before it) - terms and the coupon
schedule, prices and yields, the BOND mark, execution at the observed / computed price plus a disclosed half-spread, the
``bonds.trade`` fill, nightly ACT/ACT accrual, coupons on schedule, redemption at par, DV01 for Risk and the hedges
(TERMINAL_DESIGN.md §2.12, §5.4, §8.6, §11.2, §11.3, §12.1, §13.8, §19.13; BUILD2_PLAN P07, contracts C1 C5 C18 C19 C27).

Conventions stated here (each is a sentence the tests pin or the screen shows)
- WHAT IS LISTED: products/treasuries.py builds the master: every outstanding marketable US Treasury (``UST-<CUSIP>``),
  one GENERIC corporate per rating bucket x tenor (no real issuer) and generic foreign government benchmarks.  Bills,
  notes, bonds, the generic corporates and the DE / JP benchmarks trade (support FULL); TIPS, FRNs and the monthly gilt /
  OAT / BTP rows are LIMITED and their row says why (``spec.limited_reason``).  Old Build 1 rows (monthly model
  benchmarks, the universe's curated corporates) are DELISTED unless a lot or an order references them.
- SCHEDULE: coupon dates step back from maturity by 12 / freq months; a security maturing on the last day of a month
  pays on month ends (``eom``: a 2-year note maturing 28-Feb pays 31-Aug and 28/29-Feb, the Treasury convention).  Accrued
  interest per 100 on date D is ``coupon / freq x (D - prev_coupon) / (next_coupon - prev_coupon)`` quantised to 4
  decimals (ACT/ACT; 2.25 x 45 / 184 = 0.5503 after 45 days of a 184-day period), the cents ``to_c(face x accrued_4dp /
  100)`` (5,503.00 on 1,000,000).  On a coupon date the new period starts (accrued 0).  Accrued is that of the TRADE date
  (the ledger's accrual runs through it; real settlement is one day later, a stated simplification).
- PRICE / YIELD (street convention): a coupon bond's dirty price per 100 at yield y (percent, compounding ``freq`` times a
  year) is ``sum CF_i / (1 + y / (100 freq))^(i - 1 + w)`` with ``w = (next_coupon - D) / (next_coupon - prev_coupon)``;
  clean = dirty - accrued.  Bills (zero coupon) use the bond-equivalent yield: ``P = 100 / (1 + y d / 365)`` for d <= 182
  days to maturity, else ``P = 100 / ((1 + y / 2)(1 + y (d / 365 - 0.5)))``; the discount rate shown is ``(100 - P) / 100
  x 360 / d``.  Yields are solved from prices by bisection; prices are computed in binary floating point and quantised to
  6 decimals (far below a tick).
- THE MARK (``bond_mark``, registered for kind BOND through ``marks.register_kind``):
  * Treasuries: the TreasuryDirect FedInvest price of the CUSIP (END OF DAY when published, else the BUY / SELL mid),
    labelled "TreasuryDirect FedInvest <date>", quality ``close`` when it is today's.  When it is an earlier day's and
    Yahoo's yield records (^IRX ^FVX ^TNX ^TYX) have moved today, the price is moved by today's change: the FedInvest
    yield + the shift interpolated at the bond's maturity, repriced today (``computed``, with every input).  Without a
    FedInvest price: the par-curve model (Treasury.gov daily par curve + today's Yahoo shift) for bills / notes / bonds,
    the Treasury.gov daily REAL yield curve for TIPS (a real price per 100 of indexed principal); an FRN has no model.
  * Generic corporates: par curve yield (+ today's shift) + the bucket's ICE BofA OAS from FRED, ``computed``; without
    an OAS print the mark is unavailable with the reason (FRED in maintenance / not fetched) - never a static spread.
  * Generic foreign benchmarks: the ECB euro-area AAA par curve (DE) or the MOF JGB yields (JP) at the maturity,
    ``computed`` and labelled "generic benchmark priced from <series> as of <date>"; GB / FR / IT from FRED's OECD
    monthly 10-year average (LIMITED).
  * Quality: an input older than its publication rhythm (a FedInvest price before the previous business day, a curve
    older than 7 days, an OAS older than 7 days, today's shift from a stale record) makes the mark ``stale``.
- EXECUTION (``bonds.trade`` / POST /api/bonds/preview): BUY / SELL a face that is a multiple of the lot (1,000; JGBs
  100,000 yen) at the mark +/- a disclosed HALF-SPREAD (``HALF_SPREAD``, ASSUMED dealer half-widths by type and
  remaining tenor, e.g. a 2-7 year note 0.015625 = 1/64 per 100), only while the bond's session is open (US_BONDS 08:00 -
  17:00 ET, refused with "bond market closed - reopens 08:00 ET <day>"; foreign benchmarks on the FX session), only when
  the mark is ``close`` / ``computed`` (a stale or missing mark refuses: nothing is invented).  Fee USD 0.02 per 100 face,
  minimum USD 5.00 (§12.1; 200.00 on 1,000,000; a foreign bond pays 0.02 per 100 of its face in its currency, minimum the
  USD 5.00 converted at the snapshot rate).  Settlement T+1 (foreign T+2 in the bond's currency, funded through the FX
  conversion of the Foreign Markets account when the account and Treasury hold none).  A SELL beyond the position is
  OVERSELL; a SELL of face pledged in an open repo is refused ("release the repo first", C27); shorts are not in this
  build.  ``build2_bonds`` is ON by default (an explicit false in settings switches execution off, with that reason).
- FACE: ``qty`` on fills / lots is the face in units of the currency (``lot`` 1,000); ``contract_value_c(px, face)`` =
  face x px / 100 through the master's multiplier 0.01, so a lot's ``unit_cost`` is the clean price per 100 and its
  ``cost_c`` the clean principal (1,000,000 x 96.1027 x 0.01 = 961,027.00).
- FILL (§5.4): BUY ``Dr 1110 clean / Dr 1220 accrued / Dr 5000 fee / Cr 2100 clean + accrued + fee``, settlement row T+1,
  lot at the clean price, funded first (``execution.fund``: own available cash, else one Treasury sweep-in of exactly the
  shortfall).  SELL ``Dr 1200 clean + accrued - fee / Dr 5000 / Cr 1110 cost FIFO / Cr 1220 the carried accrued relieved
  pro rata / Cr 4300 the rest of the accrued received / Cr 4000 realised = clean - cost`` (FIFO).  Every line is in the
  bond's currency.
- ACCRUAL / COUPON / MATURITY: products/bonds_jobs.py (the nightly accrual, coupons, redemption at par; unchanged rules,
  now in the bond's currency and on the month-end schedule).
- DV01 (C18): ``dv01_c(services, inst, face, snapshot)`` = the SIGNED P&L in USD cents of ``face`` for a +1 bp parallel
  move of the bond's yield (a long loses: negative), full repricing of the dirty price, converted to USD at the
  snapshot's FX for a non-USD bond; ``reprice_c(services, inst, face, bp, snapshot)`` = the P&L in USD cents for a move
  of ``bp`` basis points (convexity included).  Both return ``(cents, inputs)`` / cents and None when there is no mark.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_EVEN
from typing import Any, Dict, List, Optional, Tuple

from .. import execution
from ..books import account_kind_for, account_row, lifetime_row
from ..clock import epoch_of, ny_of, trade_date as _trade_date
from ..commands import ConflictError, RejectError, TxContext
from ..db import dumps, get_setting, next_id
from ..ledger import Entry, cr, dr, open_lot, position, relieve_fifo
from ..lifecycle_jobs import ACCRUE_FROM_ET
from ..marks import CORP_BASE_DATE, add_months
from ..quotes import MarkView, unavailable
from ..units import fmt_money, from_c, qprice, to_c

SOURCE = "bonds"
KIND = "bond"                      # accrual_state.kind
GROUP = "US_BONDS"
LOT = 1000
FREQ = 2
JOB_ACCRUE = "BOND_ACCRUE"
JOB_COUPON = "BOND_COUPON"
JOB_MATURITY = "BOND_MATURITY"
JOB_LISTING = "BOND_LISTING"
COUPON_ET = "00:40"
MATURITY_ET = "00:45"
LISTING_ET = "00:15"
SETTING_FLAG = "build2_bonds"
UNSUPPORTED_TEXT = "bond execution is switched off (settings build2_bonds = false)"
CLOSED_TEXT = "bond market closed - reopens 08:00 ET"
EIGHTH = Decimal("0.125")
PX4 = Decimal("0.0001")
PX6 = Decimal("0.000001")
HUNDRED = Decimal(100)
STALE_CURVE_DAYS = 7
STALE_OAS_DAYS = 7
# (tenor label, months to maturity, zero coupon?) - the Build 1 monthly model benchmarks (retired; kept for the legacy rows' terms)
BENCHMARK_TENORS: List[Tuple[str, int, bool]] = [("1M", 1, True), ("3M", 3, True), ("6M", 6, True), ("1Y", 12, True), ("2Y", 24, False), ("3Y", 36, False),
                                                  ("5Y", 60, False), ("7Y", 84, False), ("10Y", 120, False), ("20Y", 240, False), ("30Y", 360, False)]
TENOR_YEARS: Dict[str, Decimal] = {"1M": Decimal(1) / Decimal(12), "3M": Decimal("0.25"), "6M": Decimal("0.5"), "1Y": Decimal(1), "2Y": Decimal(2), "3Y": Decimal(3),
                                   "5Y": Decimal(5), "7Y": Decimal(7), "10Y": Decimal(10), "20Y": Decimal(20), "30Y": Decimal(30)}
#: ASSUMED dealer half-spreads per 100 face by type and remaining tenor (years; None = beyond): disclosed on every preview
HALF_SPREAD: Dict[str, Tuple[Tuple[Optional[float], str], ...]] = {
    "BILL": ((None, "0.005"),),
    "UST": ((2.0, "0.0078125"), (7.0, "0.015625"), (None, "0.03125")),          # 1/128, 1/64, 1/32
    "TIPS": ((None, "0.0625"),),
    "FRN": ((None, "0.01"),),
    "IG": ((5.0, "0.10"), (10.0, "0.15"), (None, "0.25")),
    "HY": ((5.0, "0.25"), (10.0, "0.375"), (None, "0.50")),
    "GOV": ((10.0, "0.02"), (None, "0.05")),
}
HALF_SPREAD_NOTE = "assumed dealer half-spread (typical bid/ask half-width for the type and tenor; not an observed quote)"
IG = ("AAA", "AA", "A", "BBB")


# ---- terms and the schedule (module docstring SCHEDULE) -----------------------------------------------------------------------
@dataclass(frozen=True)
class BondTerms:
    coupon_pct: Decimal            # annual coupon in percent (4.5 = 4.500 %)
    maturity: date
    freq: int = FREQ
    issue: Optional[date] = None
    zero_coupon: bool = False
    benchmark: bool = False
    eom: bool = False

    @property
    def coupon_per_period(self) -> Decimal:
        """The coupon fraction per period: 0.0225 for 4.500 % semi-annual."""
        if self.zero_coupon or self.freq <= 0:
            return Decimal(0)
        return self.coupon_pct / HUNDRED / Decimal(self.freq)

    def to_dict(self) -> dict:
        return {"coupon_pct": str(self.coupon_pct), "maturity": self.maturity.isoformat(), "freq": self.freq, "issue": None if self.issue is None else self.issue.isoformat(),
                "zero_coupon": self.zero_coupon, "benchmark": self.benchmark, "eom": self.eom, "daycount": "ACT/ACT"}


def _month_end(d: date) -> date:
    return (date(d.year + (d.month == 12), d.month % 12 + 1, 1) - timedelta(days=1))


def bond_terms(inst) -> BondTerms:
    """Coupon (percent) and maturity of a BOND row: ``spec.coupon_pct`` (else ``spec.coupon`` with the master's
    convention: a value below 1 is a fraction, 0.0485 -> 4.85 %); maturity ``spec.maturity`` / ``expiry``, else ``years``
    from the Build 1 universe (2026-09-18 + round(years x 365.25) days); ``freq`` (bills / FRNs carry 0 = no coupon)."""
    spec = inst.spec or {}
    if spec.get("coupon_pct") is not None:
        c = Decimal(str(spec["coupon_pct"]))
    elif spec.get("coupon") is not None:
        c = Decimal(str(spec["coupon"]))
        if c < 1:
            c = c * HUNDRED
    else:
        raise RejectError("BOND_TERMS_MISSING", f"{inst.instrument_id}: the master has no coupon for this bond")
    maturity = spec.get("maturity") or inst.expiry
    if maturity:
        mat = date.fromisoformat(str(maturity)[:10])
    elif spec.get("years") is not None:
        mat = CORP_BASE_DATE + timedelta(days=int(round(float(spec["years"]) * 365.25)))
    else:
        raise RejectError("BOND_TERMS_MISSING", f"{inst.instrument_id}: the master has no maturity for this bond")
    issue = date.fromisoformat(str(spec["issue"])[:10]) if spec.get("issue") else None
    zero = bool(spec.get("zero_coupon")) or c == 0
    freq = int(spec["freq"]) if spec.get("freq") not in (None, "") else FREQ
    if freq <= 0:
        freq, zero = FREQ, True
    eom = bool(spec.get("eom")) and not zero
    return BondTerms(c, mat, freq, issue, zero, bool(spec.get("benchmark")), eom)


def _cdate(terms: BondTerms, k: int) -> date:
    d = add_months(terms.maturity, -(12 // terms.freq) * k)
    return _month_end(d) if terms.eom else d


def coupon_period(terms: BondTerms, asof: date) -> Tuple[date, date, int]:
    """``(prev_coupon, next_coupon, n_remaining)`` with prev <= asof < next on the (month-end aware) schedule."""
    if asof >= terms.maturity:
        raise ValueError("the bond has matured")
    k = 1
    while _cdate(terms, k) > asof:
        k += 1
        if k > 400:
            raise ValueError("coupon schedule beyond 200 years")
    return _cdate(terms, k), _cdate(terms, k - 1), k


def period_of(terms: BondTerms, d: date) -> Optional[Tuple[date, date]]:
    """``(prev_coupon, next_coupon)`` with ``prev <= d < next`` (None on or after maturity)."""
    if d >= terms.maturity:
        return None
    prev, nxt, _n = coupon_period(terms, d)
    return prev, nxt


def accrued_per_100(terms: BondTerms, d: date) -> Decimal:
    """Accrued interest per 100 on ``d`` (4 dp, module docstring SCHEDULE); 0 for bills and from maturity on."""
    if terms.zero_coupon or terms.coupon_pct == 0:
        return Decimal("0.0000")
    per = period_of(terms, d)
    if per is None:
        return Decimal("0.0000")
    period = (per[1] - per[0]).days
    if period <= 0:
        return Decimal("0.0000")
    return (terms.coupon_pct / Decimal(terms.freq) * Decimal((d - per[0]).days) / Decimal(period)).quantize(PX4, rounding=ROUND_HALF_EVEN)


def accrued_cents(face: int, terms: BondTerms, d: date) -> int:
    """``to_c(face x accrued_4dp / 100)`` - 5,503.00 for 1,000,000 face at 0.5503."""
    return to_c(Decimal(int(face)) * accrued_per_100(terms, d) / HUNDRED)


def coupon_cents(face: int, terms: BondTerms) -> int:
    """One coupon payment on ``face``: ``to_c(face x coupon / freq / 100)`` - 22,500.00 for 4.500 % on 1,000,000."""
    if terms.zero_coupon:
        return 0
    return to_c(Decimal(int(face)) * terms.coupon_per_period)


def coupon_dates(terms: BondTerms, after: date, through: date) -> List[date]:
    """Coupon dates ``C`` with ``after < C <= through`` (the maturity date is the last coupon date), ascending."""
    if terms.zero_coupon:
        return []
    out: List[date] = []
    k = 0
    while k < 2000:
        c = _cdate(terms, k)
        if c <= after:
            break
        if c <= through:
            out.append(c)
        k += 1
    return sorted(out)


def prev_coupon_date(terms: BondTerms, c: date) -> date:
    """The coupon date one period before the coupon date ``c`` (memo text of bonds.coupon)."""
    k = 0
    while k < 2000 and _cdate(terms, k) > c:
        k += 1
    return _cdate(terms, k + 1) if _cdate(terms, k) == c else add_months(c, -(12 // terms.freq))


# ---- prices and yields (module docstring PRICE / YIELD) -------------------------------------------------------------------------
def _flows(terms: BondTerms, asof: date) -> Tuple[float, List[Tuple[float, float]], Decimal]:
    """``(w, [(exponent, cash flow)], accrued_4dp)`` for the coupon bond at ``asof``."""
    prev, nxt, n = coupon_period(terms, asof)
    period = (nxt - prev).days
    w = (nxt - asof).days / period if period else 1.0
    cpn = float(terms.coupon_pct) / terms.freq
    flows = [(i - 1 + w, cpn + (100.0 if i == n else 0.0)) for i in range(1, n + 1)]
    return w, flows, accrued_per_100(terms, asof)


def _dirty_f(terms: BondTerms, asof: date, y_pct: float, flows=None) -> float:
    if terms.zero_coupon:
        d = (terms.maturity - asof).days
        y = y_pct / 100.0
        if d <= 182:
            return 100.0 / (1.0 + y * d / 365.0)
        return 100.0 / ((1.0 + y / 2.0) * (1.0 + y * (d / 365.0 - 0.5)))
    fl = flows if flows is not None else _flows(terms, asof)[1]
    per = 1.0 + y_pct / 100.0 / terms.freq
    if per <= 0:
        return float("inf")
    return sum(cf / per ** t for t, cf in fl)


def price_at(terms: BondTerms, asof: date, y_pct) -> Tuple[Decimal, Decimal, Decimal]:
    """``(clean, accrued, dirty)`` per 100 at yield ``y_pct`` (percent) on ``asof`` (6 dp; accrued 4 dp)."""
    if asof >= terms.maturity:
        raise ValueError("the bond has matured")
    acc = Decimal("0.0000") if terms.zero_coupon else accrued_per_100(terms, asof)
    dirty = Decimal(repr(_dirty_f(terms, asof, float(y_pct)))).quantize(PX6, rounding=ROUND_HALF_EVEN)
    return (dirty - acc).quantize(PX6), acc, dirty


def _bill_bey(terms: BondTerms, asof: date, price: float) -> Optional[float]:
    """The closed-form bond-equivalent yield (percent) of a bill priced ``price`` per 100 (module docstring PRICE / YIELD)."""
    d = (terms.maturity - asof).days
    if d <= 0 or price <= 0:
        return None
    if d <= 182:
        return (100.0 / price - 1.0) * 365.0 / d * 100.0
    a = d / 365.0 - 0.5                      # (1 + y/2)(1 + y a) = 100 / P  ->  (a/2) y^2 + (a + 1/2) y + 1 - 100/P = 0
    qa, qb, qc = a / 2.0, a + 0.5, 1.0 - 100.0 / price
    disc = qb * qb - 4.0 * qa * qc
    if disc < 0:
        return None
    return (-qb + math.sqrt(disc)) / (2.0 * qa) * 100.0


@lru_cache(maxsize=8192)
def _yield_cached(terms: BondTerms, asof: date, clean: Decimal) -> Optional[Decimal]:
    return _yield_solve(terms, asof, clean)


def yield_from_clean(terms: BondTerms, asof: date, clean) -> Optional[Decimal]:
    """The yield (percent, 6 dp) of ``clean`` on ``asof`` (memoised: the Debt page prices hundreds of rows a load)."""
    if asof >= terms.maturity or clean is None:
        return None
    return _yield_cached(terms, asof, Decimal(str(clean)))


def _yield_solve(terms: BondTerms, asof: date, clean) -> Optional[Decimal]:
    """The yield (percent, 6 dp) whose clean price is ``clean`` on ``asof``: bills in closed form, coupon bonds by Newton's
    method on the dirty price (bisection when Newton does not settle); None when out of range."""
    if asof >= terms.maturity or clean is None:
        return None
    if terms.zero_coupon:
        y = _bill_bey(terms, asof, float(clean))
        return None if y is None else Decimal(repr(y)).quantize(PX6, rounding=ROUND_HALF_EVEN)
    target = float(clean) + float(accrued_per_100(terms, asof))
    flows = _flows(terms, asof)[1]
    f = float(terms.freq)
    y = max(0.5, float(terms.coupon_pct))
    for _ in range(30):
        per = 1.0 + y / 100.0 / f
        if per <= 0:
            break
        p = sum(cf / per ** t for t, cf in flows)
        dp = sum(-t * cf / per ** (t + 1) for t, cf in flows) / (100.0 * f)
        if dp == 0:
            break
        step = (p - target) / dp
        y -= step
        if abs(step) < 1e-10:
            return Decimal(repr(y)).quantize(PX6, rounding=ROUND_HALF_EVEN)
    lo, hi = -20.0, 60.0
    if _dirty_f(terms, asof, lo, flows) < target or _dirty_f(terms, asof, hi, flows) > target:
        return None
    for _ in range(120):
        mid = (lo + hi) / 2.0
        if _dirty_f(terms, asof, mid, flows) > target:
            lo = mid
        else:
            hi = mid
        if hi - lo < 1e-11:
            break
    return Decimal(repr((lo + hi) / 2.0)).quantize(PX6, rounding=ROUND_HALF_EVEN)


def discount_rate(terms: BondTerms, asof: date, clean) -> Optional[Decimal]:
    """A bill's bank discount rate in percent: ``(100 - P) / 100 x 360 / d``."""
    d = (terms.maturity - asof).days
    if not terms.zero_coupon or d <= 0 or clean is None:
        return None
    return ((HUNDRED - Decimal(str(clean))) / HUNDRED * Decimal(360) / Decimal(d) * HUNDRED).quantize(Decimal("0.0001"))


def dv01_per_100(terms: BondTerms, asof: date, y_pct) -> Optional[Decimal]:
    """The fall of the dirty price per 100 for +1 bp (positive), full repricing; None after maturity."""
    if asof >= terms.maturity or y_pct is None:
        return None
    flows = None if terms.zero_coupon else _flows(terms, asof)[1]
    y = float(y_pct)
    return Decimal(repr(_dirty_f(terms, asof, y, flows) - _dirty_f(terms, asof, y + 0.01, flows))).quantize(Decimal("0.00000001"))


def years_to(terms: BondTerms, asof: date) -> float:
    return max(0.0, (terms.maturity - asof).days / 365.0)


# ---- small helpers ------------------------------------------------------------------------------------------------------------
def round_to_eighth(y_pct) -> Decimal:
    """The nearest 1/8 % (ties to even): 4.26 -> 4.25, 4.4375 -> 4.5, 4.3125 -> 4.25."""
    return ((Decimal(str(y_pct)) / EIGHTH).quantize(Decimal(1), rounding=ROUND_HALF_EVEN) * EIGHTH).quantize(Decimal("0.001"))


def benchmark_id(tenor: str, listing: date) -> str:
    return f"UST-{tenor}-{listing.strftime('%Y%m')}"


def benchmark_name(coupon_pct: Optional[Decimal], maturity: date, zero_coupon: bool) -> str:
    mat = maturity.strftime("%d-%b-%Y")
    if zero_coupon:
        return f"US Treasury bill {mat} (model benchmark)"
    return f"US Treasury {Decimal(coupon_pct).quantize(Decimal('0.001'))} % {mat} (model benchmark)"


def through_night(now_ts: int) -> date:
    """The last night due at ``now``: yesterday from 00:30 ET, else the day before (§11.1 ACCRUE_DAILY)."""
    today = ny_of(int(now_ts)).date()
    return today - timedelta(days=1) if int(now_ts) >= epoch_of(today, ACCRUE_FROM_ET) else today - timedelta(days=2)


def flag_on(conn) -> bool:
    """``settings.build2_bonds``: ON unless explicitly set false (Build 2 P07: "build2_bonds on by default")."""
    v = get_setting(conn, SETTING_FLAG, True)
    if v is None:
        return True
    if isinstance(v, str):
        return v.strip().lower() not in ("0", "false", "off", "no")
    return bool(v)


def _type_of(inst) -> str:
    """BILL / NOTE / BOND / TIPS / FRN for Treasuries, CORP / GOV for the generics, LEGACY otherwise."""
    spec = inst.spec or {}
    t = str(spec.get("type") or "").upper()
    if t in ("BILL", "NOTE", "BOND", "TIPS", "FRN", "CORP", "GOV"):
        return t
    return "LEGACY"


def half_spread(inst, terms: BondTerms, asof: date) -> Tuple[Decimal, str]:
    """The ASSUMED half-spread per 100 for this bond (``spec.half_spread`` overrides the table) and its sentence."""
    spec = inst.spec or {}
    if spec.get("half_spread") is not None:
        h = Decimal(str(spec["half_spread"]))
        return h, f"{h} per 100 (this instrument's stated dealing half-spread)"
    t = _type_of(inst)
    yrs = years_to(terms, asof)
    if t == "BILL" or (t == "LEGACY" and terms.zero_coupon):
        key, what = "BILL", "a Treasury bill"
    elif t in ("NOTE", "BOND", "LEGACY"):
        key, what = "UST", "a Treasury note / bond"
    elif t == "TIPS":
        key, what = "TIPS", "a TIPS"
    elif t == "FRN":
        key, what = "FRN", "an FRN"
    elif t == "CORP":
        ig = str(spec.get("bucket") or spec.get("rating") or "").upper().rstrip("+-") in IG
        key, what = ("IG", "an investment-grade corporate") if ig else ("HY", "a high-yield corporate")
    else:
        key, what = "GOV", "a government benchmark"
    lo = 0.0
    for cap, val in HALF_SPREAD[key]:
        if cap is None or yrs <= cap:
            span = f"{lo:g}-{cap:g} years" if cap is not None else f"over {lo:g} years"
            return Decimal(val), f"{val} per 100 for {what} of {span} to maturity ({HALF_SPREAD_NOTE})"
        lo = cap
    return Decimal(HALF_SPREAD[key][-1][1]), HALF_SPREAD_NOTE


# ---- listings (tests and legacy) ------------------------------------------------------------------------------------------------
def ensure_bond(tx, master, instrument_id: str, name: str, coupon_pct, maturity: date, issue: Optional[date] = None, spec: Optional[dict] = None,
                support: str = "FULL", account_kinds: Optional[List[str]] = None, currency: str = "USD", session_group: str = GROUP, settle_days: int = 1) -> bool:
    """Insert one BOND row (INSERT OR IGNORE; multiplier 0.01, tick 0.000001, lot 1,000, US_BONDS, T+1, account kinds DEBT +
    PB unless given).  Returns True when inserted.  The live master comes from products/treasuries.py."""
    c = Decimal(str(coupon_pct)).quantize(Decimal("0.001"))
    row_spec = {"coupon": str(c / HUNDRED), "coupon_pct": str(c), "maturity": maturity.isoformat(), "issue": None if issue is None else issue.isoformat(),
                "freq": FREQ, "daycount": "ACT/ACT", "zero_coupon": c == 0, "eom": False}
    row_spec.update(spec or {})
    inserted = master._insert(tx.conn, int(tx.now.timestamp()), instrument_id=instrument_id, kind="BOND", symbol=instrument_id, name=name, currency=currency,
                              multiplier="0.01", tick="0.000001", lot=LOT, session_group=session_group, settle_days=settle_days,
                              account_kinds=list(account_kinds if account_kinds is not None else ["DEBT", "PB"]), expiry=maturity.isoformat(), spec=row_spec,
                              support=support)
    if inserted:
        master.refresh(instrument_id, tx.conn)
    return inserted


def expire_matured(conn, master, today: date, ts: int) -> List[str]:
    """Mark matured BOND rows nobody holds EXPIRED (held ones expire through ``bonds.mature``)."""
    rows = conn.execute("SELECT i.instrument_id FROM instruments i WHERE i.kind='BOND' AND i.status IN ('ACTIVE','DELISTED') AND i.expiry IS NOT NULL AND i.expiry <= ?"
                        " AND NOT EXISTS (SELECT 1 FROM v_positions v WHERE v.instrument_id=i.instrument_id AND v.qty <> 0)", (today.isoformat(),)).fetchall()
    out = []
    for r in rows:
        conn.execute("UPDATE instruments SET status='EXPIRED', updated_ts=? WHERE instrument_id=? AND status IN ('ACTIVE','DELISTED')", (ts, r["instrument_id"]))
        master.refresh(r["instrument_id"], conn)
        out.append(r["instrument_id"])
    return out


# ---- the mark (module docstring THE MARK) ------------------------------------------------------------------------------------
def _store(services):
    from . import treasuries
    return treasuries.store_of(services)


def _shift(services, terms: BondTerms, today: date) -> Tuple[Optional[Decimal], dict]:
    """Today's Yahoo yield change interpolated at the bond's maturity (percentage points), or None when no record of
    today exists (``inputs`` lists each record with its quality and time)."""
    curve, cache = getattr(services, "curve", None), getattr(services, "cache", None)
    if curve is None or cache is None:
        return None, {}
    shifts, inputs = curve.intraday_shifts(cache)
    todays = {}
    for sym, rec in list(inputs.items()):
        qt = rec.get("quote_time")
        rec["today"] = bool(qt) and ny_of(int(qt)).date() == today
    from ..quotes.rates import SHIFT_SYMBOLS
    for sym, t in SHIFT_SYMBOLS.items():
        rec = inputs.get(sym)
        if rec is not None and rec.get("today") and rec.get("quality") in ("delayed", "close", "live") and t in shifts:
            todays[t] = shifts[t]
    if not todays:
        return None, inputs
    from ..quotes.rates import _interp
    s = _interp(list(todays.items()), Decimal(repr(years_to(terms, today))))
    return (None if s is None else s.quantize(PX6)), inputs


def _view(iid: str, px: Decimal, quality: str, source: str, method: str, now: int, inputs: dict, note: str) -> MarkView:
    return MarkView(iid, qprice(px), quality, source, method, inputs.get("as_of_ts"), now, "daily" if method == "fedinvest" else "model", inputs, False, note)


def bond_mark(services, inst, now: Optional[int] = None) -> MarkView:
    """The BOND mark (module docstring THE MARK); registered through ``marks.register_kind("BOND", ...)``."""
    iid = inst.instrument_id
    now = int(now if now is not None else services.clock.epoch())
    today = ny_of(now).date()
    try:
        terms = bond_terms(inst)
    except RejectError as e:
        return unavailable(iid, e.message)
    if today >= terms.maturity:
        return unavailable(iid, f"matured on {terms.maturity.isoformat()}")
    t = _type_of(inst)
    cal = getattr(services, "cal", None)
    prev_bd = cal.prev_business_day(today) if cal is not None else today - timedelta(days=1)
    if t in ("BILL", "NOTE", "BOND", "TIPS", "FRN"):
        return _ust_mark(services, inst, terms, t, today, prev_bd, now)
    if t == "CORP":
        return _corp_mark(services, inst, terms, today, now)
    if t == "GOV":
        return _gov_mark(services, inst, terms, today, now)
    return _legacy_mark(services, inst, terms, today, now)


def _ust_mark(services, inst, terms, t, today, prev_bd, now) -> MarkView:
    iid = inst.instrument_id
    spec = inst.spec or {}
    store = _store(services)
    fi = store.fedinvest(spec.get("cusip") or inst.symbol) if store is not None else None
    real = " (real price per 100 of inflation-indexed principal)" if t == "TIPS" else ""
    if fi is not None:
        d0, px0 = fi["date"], fi["price"]
        base = {"source": f"TreasuryDirect FedInvest {d0.isoformat()}", "price": str(px0), "basis": fi["basis"], "buy": _s(fi["buy"]), "sell": _s(fi["sell"]),
                "end_of_day": _s(fi["eod"]), "date": d0.isoformat(), "quality": "close" if d0 >= prev_bd else "stale"}
        if d0 >= today:
            y0 = yield_from_clean(terms, today, px0) if t != "FRN" else None
            return _view(iid, px0, "close", f"TreasuryDirect FedInvest {d0.isoformat()}", "fedinvest", now,
                         {"fedinvest": base, "yield_pct": _s(y0), "qualities": ["close"], "as_of_date": d0.isoformat()},
                         f"TreasuryDirect FedInvest {d0.isoformat()}: {fi['basis']}{real}")
        shift, sh_in = _shift(services, terms, today) if t in ("BILL", "NOTE", "BOND") else (None, {})
        if shift is not None:
            y0 = yield_from_clean(terms, d0, px0)
            if y0 is not None:
                clean, acc, _dirty = price_at(terms, today, y0 + shift)
                quality = "computed" if d0 >= prev_bd else "stale"
                return _view(iid, clean, quality, f"TreasuryDirect FedInvest {d0.isoformat()} + today's yield change", "curve", now,
                             {"fedinvest": base, "fedinvest_yield_pct": str(y0), "shift_pct": str(shift), "shifts": sh_in, "yield_pct": str((y0 + shift).quantize(PX6)),
                              "qualities": [base["quality"]], "as_of_date": today.isoformat(),
                              "model": "the FedInvest price's yield + today's Yahoo yield change at this maturity, repriced today"},
                             f"computed: FedInvest {d0.isoformat()} moved by today's yield change ({shift:+} pp)")
        q = "close" if d0 >= prev_bd else "stale"
        y0 = yield_from_clean(terms, today, px0) if t != "FRN" else None
        return _view(iid, px0, q, f"TreasuryDirect FedInvest {d0.isoformat()}", "fedinvest", now, {"fedinvest": base, "yield_pct": _s(y0), "qualities": [q],
                     "as_of_date": d0.isoformat()}, f"TreasuryDirect FedInvest {d0.isoformat()} (an earlier business day's price; no intraday move observed){real}")
    if t == "FRN":
        return unavailable(iid, "FRN: no TreasuryDirect FedInvest price yet (a floating coupon has no curve model)")
    if t == "TIPS":
        got = store.yield_at("REAL", years_to(terms, today)) if store is not None else None
        if got is None:
            return unavailable(iid, "TIPS: no FedInvest price and no Treasury real yield curve yet - LIMITED")
        y, c = got
        clean, acc, _dirty = price_at(terms, today, y)
        q = "computed" if (today - c["as_of"]).days <= STALE_CURVE_DAYS else "stale"
        return _view(iid, clean, q, f"Treasury real yield curve {c['as_of'].isoformat()}", "curve", now,
                     {"real_curve": {"as_of": c["as_of"].isoformat(), "source": c["source"], "quality": "close" if q == "computed" else "stale"}, "yield_pct": str(y),
                      "qualities": [q], "as_of_date": c["as_of"].isoformat()}, "computed: real yield curve" + real)
    return _curve_model(services, inst, terms, today, now, None, None)


def _curve_model(services, inst, terms, today, now, oas: Optional[Decimal], oas_in: Optional[dict]) -> MarkView:
    iid = inst.instrument_id
    curve = getattr(services, "curve", None)
    pc = curve.latest() if curve is not None else None
    if pc is None or not pc.points:
        return unavailable(iid, "no FedInvest price and no Treasury par curve fetched yet")
    t_years = Decimal(repr(years_to(terms, today)))
    par = curve.par_yield(t_years)
    shift, sh_in = (None, {})
    if pc.as_of < today:
        shift, sh_in = _shift(services, terms, today)
    y = par + (shift or 0) + (oas or 0)
    clean, acc, _dirty = price_at(terms, today, y)
    stale = (today - pc.as_of).days > STALE_CURVE_DAYS or (oas_in is not None and oas_in.get("quality") == "stale")
    inputs = {"curve": {"as_of": pc.as_of.isoformat(), "source": pc.source, "par_yield_pct": str(par), "quality": "stale" if (today - pc.as_of).days > STALE_CURVE_DAYS else "close"},
              "shift_pct": _s(shift), "shifts": sh_in, "yield_pct": str(Decimal(y).quantize(PX6)), "as_of_date": today.isoformat(),
              "model": "par curve yield at the maturity" + (" + today's Yahoo yield change" if shift is not None else "") + (" + the bucket OAS" if oas is not None else "")}
    if oas_in is not None:
        inputs["oas"] = oas_in
    inputs["qualities"] = ["stale" if stale else "close"]
    src = f"par curve model ({pc.source} {pc.as_of.isoformat()}" + (" + Yahoo intraday" if shift is not None else "") + (" + ICE BofA OAS" if oas is not None else "") + ")"
    return _view(iid, clean, "stale" if stale else "computed", src, "curve", now, inputs, "computed: " + inputs["model"])


def _corp_mark(services, inst, terms, today, now) -> MarkView:
    from ..quotes.rates import OAS_SERIES
    spec = inst.spec or {}
    bucket = str(spec.get("bucket") or spec.get("rating") or "").upper().rstrip("+-")
    sid = spec.get("oas_series") or OAS_SERIES.get(bucket)
    rates = getattr(services, "rates", None)
    got = rates.latest(sid) if (rates is not None and sid) else None
    if got is None:
        why = "FRED unavailable (maintenance)" if (rates is not None and rates.fred_status().get("maintenance")) else "not fetched yet"
        return unavailable(inst.instrument_id, f"ICE BofA {bucket} OAS ({sid}) unavailable: {why}; no static spread is used")
    stale = (today - got[0]).days > STALE_OAS_DAYS
    oas_in = {"series": sid, "value_pct": str(got[1]), "as_of": got[0].isoformat(), "quality": "stale" if stale else "close", "source": rates.source(sid) or "FRED"}
    return _curve_model(services, inst, terms, today, now, got[1], oas_in)


def _gov_mark(services, inst, terms, today, now) -> MarkView:
    from . import treasuries
    spec = inst.spec or {}
    cc = str(spec.get("country") or "")
    f = treasuries.FOREIGN.get(cc, {})
    store = _store(services)
    if f.get("curve"):
        got = store.yield_at(f["curve"], years_to(terms, today)) if store is not None else None
        if got is None:
            return unavailable(inst.instrument_id, f"no {treasuries.CURVE_SOURCE[f['curve']]} yet")
        y, c = got
        as_of, src = c["as_of"], c["source"]
    else:
        rates = getattr(services, "rates", None)
        obs = rates.latest(f.get("fred")) if rates is not None and f.get("fred") else None
        if obs is None:
            return unavailable(inst.instrument_id, f"no {f.get('fred')} observation (FRED)")
        as_of, y, src = obs[0], obs[1], f"FRED {f.get('fred')} (OECD monthly average)"
    clean, acc, _dirty = price_at(terms, today, y)
    stale = (today - as_of).days > (45 if not f.get("curve") else STALE_CURVE_DAYS)
    label = f"generic benchmark priced from {f.get('series', src)} as of {as_of.isoformat()}"
    return _view(inst.instrument_id, clean, "stale" if stale else "computed", src + f" {as_of.isoformat()}", "curve", now,
                 {"curve": {"as_of": as_of.isoformat(), "source": src, "quality": "stale" if stale else "close"}, "yield_pct": str(Decimal(y).quantize(PX6)),
                  "qualities": ["stale" if stale else "close"], "as_of_date": as_of.isoformat(), "label": label}, label)


def _legacy_mark(services, inst, terms, today, now) -> MarkView:
    """A Build 1 row still held: the par curve (+ the bucket OAS when the row has a rating; never the old static spread)."""
    from ..quotes.rates import OAS_SERIES
    spec = inst.spec or {}
    rating = str(spec.get("rating") or "").upper().rstrip("+-")
    if rating:
        sid = OAS_SERIES.get(rating)
        rates = getattr(services, "rates", None)
        got = rates.latest(sid) if (rates is not None and sid) else None
        if got is None:
            return unavailable(inst.instrument_id, f"legacy row: no ICE BofA {rating} OAS observation; no static spread is used")
        oas_in = {"series": sid, "value_pct": str(got[1]), "as_of": got[0].isoformat(), "quality": "stale" if (today - got[0]).days > STALE_OAS_DAYS else "close"}
        return _curve_model(services, inst, terms, today, now, got[1], oas_in)
    return _curve_model(services, inst, terms, today, now, None, None)


def _s(v) -> Optional[str]:
    return None if v is None else str(v)


def executable_mark(view, today: date) -> Tuple[bool, str]:
    """Whether a bond may be dealt off this mark: ``close`` (an observed price of today or the previous business day) or
    ``computed``; stale / unavailable never (nothing is invented)."""
    if view is None or view.mark is None or view.quality == "unavailable":
        return False, (getattr(view, "note", "") or "no mark")
    if view.quality == "stale":
        return False, view.note or "the mark's inputs are stale"
    if view.quality not in ("close", "computed"):
        return False, f"mark quality {view.quality}"
    return True, ""


# ---- the page / ticket figures -------------------------------------------------------------------------------------------------
def quote_view(services, inst, now_ts: Optional[int] = None, snapshot=None, conn=None) -> dict:
    """The page / ticket figures of one bond: the mark (with provenance), yield, accrued, dirty, DV01 per 1,000,000 face,
    the half-spread, whether it can be dealt here and now - never a fabricated number."""
    snap = snapshot if snapshot is not None else services.marks.snapshot([inst.instrument_id])
    now = int(now_ts if now_ts is not None else services.clock.epoch())
    today = ny_of(now).date()
    view = snap.mark(inst.instrument_id)
    terms = bond_terms(inst)
    accrued = accrued_per_100(terms, today) if today < terms.maturity else Decimal("0.0000")
    clean = None if view.mark is None else qprice(view.mark)
    y = None
    if clean is not None and today < terms.maturity and _type_of(inst) != "FRN":
        y = yield_from_clean(terms, today, clean)
    dv = dv01_per_100(terms, today, y) if y is not None else None
    if conn is not None:
        enabled = flag_on(conn)
    else:
        with services.db.reader() as c:
            enabled = flag_on(c)
    ok, why = executable_mark(view, today)
    support_ok = inst.support == "FULL"
    hs, hs_text = half_spread(inst, terms, today)
    t = _type_of(inst)
    out = {"instrument_id": inst.instrument_id, "mark": view.to_dict(), "clean": None if clean is None else str(clean), "accrued": str(accrued),
           "dirty": None if clean is None else str(qprice(clean + accrued)), "yield_pct": _s(y), "as_of_date": today.isoformat(), "terms": terms.to_dict(),
           "type": t, "dv01_per_1m": None if dv is None else str((dv * Decimal(10000)).quantize(Decimal("0.01"))),
           "discount_rate_pct": _s(discount_rate(terms, today, clean)) if terms.zero_coupon and t == "BILL" else None,
           "label": (inst.spec or {}).get("source") or ("model benchmark" if terms.benchmark else "bond"),
           "executable_here": bool(enabled and ok and support_ok), "half_spread": str(hs), "half_spread_basis": hs_text,
           "note": (inst.spec or {}).get("limited_reason") if not support_ok else (why if not ok else (view.note or "")),
           "fee_basis": "USD 0.02/100 face, min 5.00", "settlement": f"T+{inst.settle_days}", "currency": inst.currency}
    if not enabled:
        out["note"] = UNSUPPORTED_TEXT
    return out


# ---- DV01 / reprice (C18) -------------------------------------------------------------------------------------------------------
def _yield_now(services, inst, snapshot=None, now: Optional[int] = None):
    now = int(now if now is not None else (snapshot.as_of() if snapshot is not None and hasattr(snapshot, "as_of") else services.clock.epoch()))
    today = ny_of(now).date()
    terms = bond_terms(inst)
    if today >= terms.maturity:
        return None, terms, today, None, {"note": "matured"}
    snap = snapshot if snapshot is not None else services.marks.snapshot([inst.instrument_id])
    view = snap.mark(inst.instrument_id)
    if view is None or view.mark is None:
        return None, terms, today, view, {"note": getattr(view, "note", None) or "no mark"}
    y = yield_from_clean(terms, today, view.mark)
    return y, terms, today, view, {}


def _usd_rate(snapshot, ccy: str) -> Optional[Decimal]:
    if ccy == "USD":
        return Decimal(1)
    try:
        fx = snapshot.fx(ccy)
    except Exception:   # noqa: BLE001
        return None
    return None if fx is None or fx.mark is None else Decimal(str(fx.mark))


def dv01_c(services, inst, face: int, snapshot=None) -> Tuple[Optional[int], dict]:
    """C18: the SIGNED P&L in USD cents of ``face`` (signed: long > 0) for +1 bp (module docstring DV01) and its inputs."""
    y, terms, today, view, why = _yield_now(services, inst, snapshot)
    if y is None:
        return None, dict(why, basis="no yield: DV01 unavailable")
    per = dv01_per_100(terms, today, y)
    local_c = -to_c(Decimal(int(face)) * per / HUNDRED)
    snap = snapshot if snapshot is not None else services.marks.snapshot([inst.instrument_id])
    rate = _usd_rate(snap, inst.currency)
    if rate is None:
        return None, {"note": f"no USD rate for {inst.currency}", "local_c": local_c}
    usd_c = local_c if inst.currency == "USD" else to_c(from_c(local_c) * rate)
    return usd_c, {"yield_pct": str(y), "dv01_per_100": str(per), "face": int(face), "local_c": local_c, "currency": inst.currency, "usd_rate": str(rate),
                   "mark": None if view is None else str(view.mark), "mark_quality": None if view is None else view.quality,
                   "basis": "full reprice of the dirty price for +1 bp of the bond's own yield (P&L of the face held; a long loses)"}


def reprice_c(services, inst, face: int, bp, snapshot=None) -> Optional[int]:
    """C18: the P&L in USD cents of ``face`` for a parallel move of ``bp`` basis points of the bond's yield (full reprice)."""
    y, terms, today, view, _why = _yield_now(services, inst, snapshot)
    if y is None:
        return None
    flows = None if terms.zero_coupon else _flows(terms, today)[1]
    base = _dirty_f(terms, today, float(y), flows)
    moved = _dirty_f(terms, today, float(y) + float(bp) / 100.0, flows)
    local_c = to_c(Decimal(int(face)) * Decimal(repr(moved - base)) / HUNDRED)
    snap = snapshot if snapshot is not None else services.marks.snapshot([inst.instrument_id])
    rate = _usd_rate(snap, inst.currency)
    if rate is None:
        return None
    return local_c if inst.currency == "USD" else to_c(from_c(local_c) * rate)


# ---- services / rows ----------------------------------------------------------------------------------------------------------------
def _services(ctx: TxContext, services=None):
    s = services if services is not None else ctx.services
    if s is None:
        raise RejectError("NO_SERVICES", "the command context carries no services bundle")
    return s


def _open_account(conn, account_id: str):
    acct = account_row(conn, account_id)
    if acct is None:
        raise RejectError("ACCOUNT_UNKNOWN", f"account {account_id} does not exist")
    if acct["status"] != "OPEN":
        raise RejectError("ACCOUNT_CLOSED", f"account {account_id} is closed")
    return acct


def _bond(services, conn, iid: str):
    inst = services.instruments.get(iid, conn)
    if inst is None:
        raise RejectError("INSTRUMENT_UNKNOWN", f"instrument {iid} is not in the master")
    if inst.kind != "BOND":
        raise RejectError("NOT_A_BOND", f"{iid} is a {inst.kind}, not a bond")
    return inst


def face_at(conn, account: str, iid: str, d: date) -> int:
    """Face held at the close of ``d``: sum of signed fills (BUY +, SELL -) with trade date <= d."""
    row = conn.execute("SELECT COALESCE(SUM(CASE side WHEN 'BUY' THEN qty ELSE -qty END), 0) FROM fills WHERE account_id=? AND instrument_id=? AND trade_date<=?",
                       (account, iid, d.isoformat())).fetchone()
    return int(row[0] or 0)


def _ref(account: str, iid: str) -> str:
    return f"{account}:{iid}"


def state_row(conn, account: str, iid: str):
    return conn.execute("SELECT * FROM accrual_state WHERE kind=? AND ref_id=?", (KIND, _ref(account, iid))).fetchone()


def _write_state(conn, account: str, iid: str, terms: BondTerms, face: int, through: date, cum_c: int) -> None:
    per = period_of(terms, through + timedelta(days=1)) or period_of(terms, through)
    if per is None:
        start, basis = terms.maturity, 1
    else:
        start, basis = per[0] + timedelta(days=1), (per[1] - per[0]).days
    conn.execute("INSERT INTO accrual_state(kind, ref_id, segment_start, base_c, rate, day_basis, accrued_through, cum_c) VALUES (?,?,?,?,?,?,?,?)"
                 " ON CONFLICT(kind, ref_id) DO UPDATE SET segment_start=excluded.segment_start, base_c=excluded.base_c, rate=excluded.rate, day_basis=excluded.day_basis,"
                 " accrued_through=excluded.accrued_through, cum_c=excluded.cum_c",
                 (KIND, _ref(account, iid), start.isoformat(), int(face), str(qprice(terms.coupon_per_period)), int(basis), through.isoformat(), int(cum_c)))


def _delete_state(conn, account: str, iid: str) -> None:
    conn.execute("DELETE FROM accrual_state WHERE kind=? AND ref_id=?", (KIND, _ref(account, iid)))


def _job_row(conn, job: str, key: str):
    return conn.execute("SELECT status, note, run_ts FROM jobs_done WHERE job=? AND key=?", (job, key)).fetchone()


def _job_done(ctx: TxContext, job: str, key: str, status: str, note: str) -> None:
    ctx.conn.execute("INSERT INTO jobs_done(job, key, run_ts, command_id, status, note) VALUES (?,?,?,?,?,?)"
                     " ON CONFLICT(job, key) DO UPDATE SET run_ts=excluded.run_ts, command_id=excluded.command_id, status=excluded.status, note=excluded.note",
                     (job, key, ctx.ts, ctx.command_id, status, note[:500]))


def _resolve_attention(ctx: TxContext, codes: Tuple[str, ...], ref_id: str) -> int:
    n = 0
    for code in codes:
        cur = ctx.conn.execute("UPDATE attention SET resolved_ts=? WHERE code=? AND ref_id=? AND resolved_ts IS NULL", (ctx.ts, code, ref_id))
        n += cur.rowcount or 0
    return n


def _skip(ctx: TxContext, job: str, key: str, code: str, message: str, account_id: Optional[str], iid: Optional[str], details: Optional[dict] = None) -> dict:
    """A PENDING outcome: jobs_done SKIPPED (the key stays due), one open WARN attention per (code, key), an event."""
    _job_done(ctx, job, key, "SKIPPED", f"{code}: {message}")
    existing = ctx.conn.execute("SELECT id FROM attention WHERE code=? AND ref_id=? AND resolved_ts IS NULL", (code, key)).fetchone()
    attention_id = int(existing["id"]) if existing else ctx.attention("WARN", SOURCE, code, message,
                                                                          dict(details or {}, account_id=account_id, ref_type=KIND, ref_id=key, auto_resolve=1))
    ctx.event("JOB_SKIPPED", account_id=account_id, instrument_id=iid, ref_type="job", ref_id=key, job=job, code=code, message=message)
    return {"status": "SKIPPED", "skipped": True, "retry": True, "code": code, "message": message, "key": key, "attention_id": attention_id}


def _done_noop(ctx: TxContext, job: str, key: str, code: str, note: str) -> dict:
    """A TERMINAL no-op: DONE with the reason, nothing posted."""
    _job_done(ctx, job, key, "DONE", f"{code}: {note}")
    return {"status": "DONE", "skipped": True, "posted": False, "code": code, "note": note, "key": key}


def _key3(payload: dict, what: str) -> Tuple[str, str, str]:
    account = str(payload.get("account_id") or "")
    iid = str(payload.get("instrument_id") or "")
    ds = str(payload.get("date") or "")
    key = payload.get("key")
    if key and not (account and iid and ds):
        parts = str(key).rsplit(":", 2)
        if len(parts) == 3:
            account, iid, ds = parts
    try:
        d = date.fromisoformat(ds)
    except ValueError as e:
        raise RejectError("KEY_INVALID", f"{what} needs account_id, instrument_id and an ISO date (got {ds!r})") from e
    if not account or not iid:
        raise RejectError("KEY_INVALID", f"{what} needs account_id and instrument_id")
    return account, iid, d.isoformat()


# ---- the trade: checks, price, preview (module docstring EXECUTION) ------------------------------------------------------------------
@dataclass
class BondTrade:
    account_id: str
    acct: dict
    lifetime_id: str
    inst: Any
    terms: BondTerms
    side: str
    face: int
    trade_date: date
    settle_date: date
    clean: Decimal                # the EXECUTION clean price per 100 (mark +/- half-spread)
    accrued: Decimal              # per 100, 4 dp
    clean_c: int
    accrued_c: int
    fee_c: int
    fee_basis: str
    mark: Any                     # MarkView
    position: int
    state: Optional[dict]
    currency: str = "USD"
    mark_price: Optional[Decimal] = None
    half_spread: Decimal = Decimal(0)
    half_spread_basis: str = ""
    yield_pct: Optional[Decimal] = None
    fx_rate: Decimal = Decimal(1)


class _Blocked(Exception):
    def __init__(self, code: str, message: str, http: int = 400, details: Optional[dict] = None):
        super().__init__(message)
        self.code, self.message, self.http, self.details = code, message, http, details or {}


def _raise(b: _Blocked):
    if b.http == 409:
        raise ConflictError(b.code, b.message, b.details)
    raise RejectError(b.code, b.message, b.details)


def _session_for(services, inst, now: int):
    group = inst.session_group or GROUP
    try:
        return services.sessions.state(group, now)
    except KeyError:
        return services.sessions.state(GROUP, now)


def _closed_text(inst, session) -> str:
    nxt = getattr(session, "next_open", None)
    when = ""
    if nxt:
        ny = ny_of(int(nxt))
        when = f" ({ny.strftime('%a %d-%b')} {ny.strftime('%H:%M')} ET)"
    if (inst.session_group or GROUP) == GROUP:
        return CLOSED_TEXT + when
    return f"{inst.currency} government bond dealing follows the FX session, now closed - reopens{when or ' Sunday 17:00 ET'}"


def _fee(services, acct, inst, face: int, clean_c: int, rate: Decimal) -> Tuple[int, str]:
    if inst.currency == "USD":
        return execution.fee_for(services, acct, inst, face, clean_c)
    per = to_c(Decimal("0.02") * Decimal(int(face)) / HUNDRED)
    minimum = to_c(Decimal("5.00") / rate) if rate > 0 else 0
    return max(per, minimum), f"{inst.currency} 0.02 per 100 face, minimum the USD 5.00 equivalent ({inst.currency} {fmt_money(minimum)} at {rate} USD per {inst.currency})"


def check_trade(services, payload: dict, conn, now_ts: int, snapshot, collect: bool = False) -> Tuple[Optional[BondTrade], List[_Blocked], List[str]]:
    """The checks and the price of a bond trade (module docstring EXECUTION).  ``collect=False`` stops at the first
    refusal (the command); ``collect=True`` (the preview) records every refusal it can and still prices the trade when the
    instrument, side, face and mark allow it.  Returns ``(trade | None, refusals, warnings)``."""
    blocked: List[_Blocked] = []
    warnings: List[str] = []

    def block(code, message, http=400, **details):
        b = _Blocked(code, message, http, details)
        if not collect:
            raise b
        blocked.append(b)

    if not flag_on(conn):
        block("UNSUPPORTED", UNSUPPORTED_TEXT, setting=SETTING_FLAG)
    account_id = str(payload.get("account_id") or "")
    if not account_id:
        raise _Blocked("ACCOUNT_UNKNOWN", "account_id is required")
    acct = account_row(conn, account_id)
    if acct is None:
        raise _Blocked("ACCOUNT_UNKNOWN", f"account {account_id} does not exist")
    if acct["status"] != "OPEN":
        raise _Blocked("ACCOUNT_CLOSED", f"account {account_id} is closed")
    lid = acct["lifetime_id"]
    lrow = lifetime_row(conn, lid)
    if lrow is None or lrow["status"] != "OPEN":
        raise _Blocked("LIFETIME_CLOSED", f"lifetime {lid} is closed (History is read-only)", 400, {"lifetime_id": lid})
    acc = getattr(services, "accounting", None)
    if acc is not None and hasattr(acc, "is_held") and acc.is_held(lid):
        block("INTEGRITY_HOLD", f"lifetime {lid} is on an integrity hold: the whole-ledger check failed and is unresolved", 409, lifetime_id=lid)
    iid = str(payload.get("instrument_id") or "").strip().upper()
    inst = services.instruments.get(iid, conn)
    if inst is None:
        raise _Blocked("INSTRUMENT_UNKNOWN", f"instrument {iid} is not in the master")
    if inst.kind != "BOND":
        raise _Blocked("NOT_A_BOND", f"{iid} is a {inst.kind}, not a bond")
    ak = account_kind_for(acct)
    if ak not in inst.account_kinds:
        where = "a Debt Instruments or prime-broker account" if "DEBT" in inst.account_kinds else ("the Foreign Markets & FX account" if "FOREIGN_FX" in inst.account_kinds
                                                                                                  else "no account in this build")
        block("NOT_TRADEABLE_HERE", f"{inst.symbol} trades in {where}, not in {account_id} ({acct['kind']})", account_kinds=list(inst.account_kinds))
    if inst.support != "FULL":
        block("LIMITED", f"{inst.symbol}: {(inst.spec or {}).get('limited_reason') or inst.support.lower() + ' in this build'}", support=inst.support)
    if inst.status != "ACTIVE":
        raise _Blocked("INSTRUMENT_EXPIRED", f"{inst.symbol} is {inst.status}")
    terms = bond_terms(inst)
    now = int(now_ts)
    tdate = _trade_date(ny_of(now), inst.session_group or GROUP, services.cal)
    if terms.maturity <= tdate:
        raise _Blocked("INSTRUMENT_EXPIRED", f"{inst.symbol} matures on {terms.maturity.isoformat()}: no trades on or after the maturity date")
    side = str(payload.get("side") or "").upper()
    if side in ("SELL_SHORT", "BUY_TO_COVER", "STO", "BTC"):
        raise _Blocked("NOT_IN_THIS_BUILD", "bond shorts need a borrow (coupons in lieu): not in this build")
    if side not in ("BUY", "SELL"):
        raise _Blocked("SIDE_INVALID", f"side {side!r}: use BUY or SELL")
    raw = payload.get("qty") if payload.get("qty") is not None else payload.get("face")
    try:
        face = int(str(raw).replace(",", "")) if raw is not None else None
    except (TypeError, ValueError):
        face = None
    lot = max(1, int(inst.lot or LOT))
    if face is None:
        raise _Blocked("QTY_INVALID", f"face must be a whole number, got {raw!r}")
    if face <= 0 or face % lot:
        raise _Blocked("QTY_INVALID", f"face must be a positive multiple of {lot:,}", 400, {"lot": lot})
    session = _session_for(services, inst, now)
    if not session.open:
        block("SESSION_CLOSED", _closed_text(inst, session), 400, phase=session.phase, next_open=session.next_open)
    pos, _cost = position(conn, account_id, iid)
    st = state_row(conn, account_id, iid)
    if st is not None and st["accrued_through"] and date.fromisoformat(st["accrued_through"]) < through_night(now):
        block("ACCRUAL_PENDING", f"{inst.symbol}: the overnight accruals of this position (after {st['accrued_through']}) have not been posted yet;"
              " the lifecycle catches up within a tick - retry in a moment", 409, accrued_through=st["accrued_through"])
    if side == "SELL":
        pledged = 0
        repo = getattr(services, "repo", None)
        if repo is not None and callable(getattr(repo, "pledged_face", None)):
            pledged = int(repo.pledged_face(conn, account_id, iid) or 0)
        if pos < face:
            block("OVERSELL", f"SELL {face:,} {inst.symbol} but the position is {max(pos, 0):,} face", 400, position=pos, qty=face)
        elif pledged and pos - pledged < face:
            block("PLEDGED_IN_REPO", f"SELL {face:,} {inst.symbol}: {pledged:,} face is pledged in an open repo and only {max(pos - pledged, 0):,} is free"
                  " - release the repo first", 409, position=pos, pledged=pledged, qty=face)
        first = conn.execute("SELECT MIN(trade_date) d FROM fills WHERE account_id=? AND instrument_id=?", (account_id, iid)).fetchone()
        if first is not None and first["d"]:
            for c in coupon_dates(terms, date.fromisoformat(first["d"]), tdate):
                row = _job_row(conn, JOB_COUPON, f"{account_id}:{iid}:{c.isoformat()}")
                if face_at(conn, account_id, iid, c - timedelta(days=1)) > 0 and (row is None or row["status"] != "DONE"):
                    block("COUPON_PENDING", f"{inst.symbol}: the coupon of {c.isoformat()} has not been paid yet; retry after the lifecycle pays it", 409,
                          coupon_date=c.isoformat())
                    break
    view = snapshot.mark(iid)
    if view is None or view.mark is None or view.quality == "unavailable":
        block("MARK_UNAVAILABLE", f"{inst.symbol}: no price ({(view.note if view is not None else '') or 'no source'}); nothing is invented", 409,
              quality=None if view is None else view.quality)
        return None, blocked, warnings
    if view.quality not in ("computed", "close"):
        block("QUOTE_STALE", f"{inst.symbol}: the mark is {view.quality} ({view.note or 'inputs not fresh'}); a bond is dealt only off a current price", 409,
              quality=view.quality, note=view.note)
    mark = qprice(view.mark)
    if mark <= 0:
        block("MARK_UNAVAILABLE", f"{inst.symbol}: the mark {mark} is not a price", 409)
        return None, blocked, warnings
    hs, hs_text = half_spread(inst, terms, tdate)
    clean = qprice((mark + hs if side == "BUY" else mark - hs).quantize(PX6))
    if clean <= 0:
        block("MARK_UNAVAILABLE", f"{inst.symbol}: the dealing price {clean} is not a price", 409)
        return None, blocked, warnings
    accrued = accrued_per_100(terms, tdate)
    clean_c = inst.contract_value_c(clean, face)
    accrued_c = to_c(Decimal(face) * accrued / HUNDRED)
    rate = _usd_rate(snapshot, inst.currency)
    if rate is None:
        block("FX_RATE_UNAVAILABLE", f"no USD rate for {inst.currency} in the mark snapshot: the trade cannot be valued", 409, currency=inst.currency)
        rate = Decimal(0)
    fee_c, fee_basis = _fee(services, acct, inst, face, clean_c, rate)
    settle = services.cal.settlement_date(tdate, inst.settle_days)
    y = yield_from_clean(terms, tdate, clean) if _type_of(inst) != "FRN" else None
    trade = BondTrade(account_id, dict(acct), lid, inst, terms, side, face, tdate, settle, clean, accrued, clean_c, accrued_c, int(fee_c), fee_basis, view, pos,
                      None if st is None else dict(st), inst.currency, mark, hs, hs_text, y, rate)
    if view.quality == "close" and view.method == "fedinvest" and (view.inputs or {}).get("as_of_date") not in (None, tdate.isoformat()):
        warnings.append(f"priced off the FedInvest price of {(view.inputs or {}).get('as_of_date')} (no intraday move observed today)")
    return trade, blocked, warnings


def validate_trade(services, payload: dict, conn, now_ts: int, snapshot) -> BondTrade:
    """The command's checks: the first refusal raises ``RejectError`` 400 / ``ConflictError`` 409 with its code."""
    try:
        trade, blocked, _w = check_trade(services, payload, conn, now_ts, snapshot, collect=False)
    except _Blocked as b:
        _raise(b)
    if blocked:
        _raise(blocked[0])
    return trade


def _funding_view(services, conn, t: BondTrade, snapshot, now: int) -> dict:
    """What the account's own cash covers and what a Treasury sweep / FX conversion would move (the preview, nothing moves)."""
    from ..accounting import available_c
    from datetime import datetime, timezone
    if t.side != "BUY":
        return {"need_c": 0, "note": "a sale is paid INTO the account at settlement"}
    need = t.clean_c + t.accrued_c + t.fee_c
    own = available_c(conn, t.account_id, t.currency, t.settle_date, snapshot, services.instruments, datetime.fromtimestamp(now, timezone.utc))
    short = max(0, need - own)
    out = {"need_c": need, "currency": t.currency, "own_available_c": own, "shortfall_c": short, "sweep_c": 0}
    if short <= 0:
        out["note"] = "covered by the account's own available cash"
        return out
    acc = getattr(services, "accounting", None)
    tre = None
    if acc is not None and hasattr(acc, "treasury_available_c"):
        try:
            tre = acc.treasury_available_c(conn, t.lifetime_id, t.currency, snapshot)
        except Exception:   # noqa: BLE001 - informative only
            tre = None
    out["treasury_available_c"] = tre
    if tre is not None and tre >= short:
        out.update(sweep_c=short, note=f"{t.currency} {fmt_money(short)} sweeps from Treasury at booking (one linked internal transfer)")
        return out
    if t.currency != "USD":
        fx = getattr(services, "fx", None)
        plan = None
        if fx is not None and callable(getattr(fx, "conversion_plan", None)):
            try:
                plan = fx.conversion_plan(services, snapshot, t.account_id, t.currency, short - max(0, tre or 0), t.settle_date, conn=conn)
            except Exception as e:   # noqa: BLE001 - the plan is P06's; its failure is shown, never hidden
                plan = {"executable": False, "reason": f"{type(e).__name__}: {e}"}
        out["fx_conversion"] = plan
        out["note"] = (f"the account and Treasury hold {t.currency} {fmt_money(max(0, own) + max(0, tre or 0))}: the rest is converted from USD at booking"
                       if plan and plan.get("executable") else f"no {t.currency} to fund it: convert USD to {t.currency} first (FX spot), or the FX conversion at booking")
        return out
    # OTC-R1-01: the booking's cascade (execution.fund, C12) is own cash -> ONE Treasury sweep -> PB financing (a PB account with
    # financing on) -> else 409 INSUFFICIENT_FUNDS; the preview applies the same cascade, so a trade the booking would refuse is
    # refused HERE (a blocker: Submit stays disabled) with the largest face the cash covers now
    if tre is None:
        out["note"] = "Treasury's available cash could not be read: the booking sweeps what Treasury holds, else it is refused for lack of funds"
        return out
    pb = _pb_room_preview(services, conn, t, snapshot)
    out["pb_headroom_c"] = pb
    covered = own + max(0, tre) + pb
    if covered >= need:
        from_tre = min(short, max(0, tre))
        out.update(sweep_c=from_tre, pb_draw_c=short - from_tre,
                   note=f"{t.currency} {fmt_money(from_tre)} sweeps from Treasury and {fmt_money(short - from_tre)} is drawn on PB financing at booking")
        return out
    max_face = _max_face_for(services, t, covered)
    out.update(refused=True, covered_c=covered, max_face=max_face,
               note=(f"not enough {t.currency}: own available {fmt_money(own)} + Treasury available {fmt_money(max(0, tre))}"
                     + (f" + PB financing {fmt_money(pb)}" if pb else "") + f" = {fmt_money(covered)} of the {fmt_money(need)} needed"))
    return out


def _pb_room_preview(services, conn, t: BondTrade, snapshot) -> int:
    """PB headroom the booking's cascade may draw (execution._pb_room): a PB account with financing on, USD only; else 0."""
    acct = t.acct or {}
    if t.currency != "USD" or acct.get("kind") != "PB" or not int(acct.get("pb_financing") or 0):
        return 0
    credit = getattr(services, "credit", None)
    acc = getattr(services, "accounting", None)
    if credit is None or not hasattr(credit, "pb_draw_for_funding") or acc is None or not hasattr(acc, "pb_headroom_c"):
        return 0
    try:
        return max(0, int(acc.pb_headroom_c(conn, dict(acct), snapshot)))
    except Exception:   # noqa: BLE001 - informative only: no headroom is assumed
        return 0


def _max_face_for(services, t: BondTrade, avail_c: int) -> int:
    """The largest face (a multiple of the lot) whose clean + accrued + fee fits ``avail_c`` at the preview's price."""
    lot = max(1, int(t.inst.lot or LOT))
    per_face = (t.clean + t.accrued) / HUNDRED
    if avail_c <= 0 or per_face <= 0:
        return 0
    face = int((Decimal(avail_c) / HUNDRED / per_face) // lot) * lot
    while face > 0:
        clean_c = t.inst.contract_value_c(t.clean, face)
        fee_c, _basis = _fee(services, t.acct, t.inst, face, clean_c, t.fx_rate)
        if clean_c + to_c(Decimal(face) * t.accrued / HUNDRED) + int(fee_c) <= avail_c:
            return face
        face -= lot
    return 0


def preview(services, payload: dict, now: Optional[int] = None) -> dict:
    """POST /api/bonds/preview: the trade's economics and every refusal, writing nothing (module docstring EXECUTION)."""
    from ..marks import shared_reader
    now = int(now if now is not None else services.clock.epoch())
    iid = str(payload.get("instrument_id") or "").strip().upper()
    snapshot = services.marks.snapshot([iid])
    with shared_reader(services.db) as conn:
        try:
            t, blocked, warnings = check_trade(services, payload, conn, now, snapshot, collect=True)
        except _Blocked as b:
            return {"ok": False, "blockers": [{"code": b.code, "message": b.message, "http": b.http, "details": b.details}], "warnings": [], "instrument_id": iid}
        inst = services.instruments.get(iid, conn)
        out: dict = {"ok": not blocked and t is not None, "blockers": [{"code": b.code, "message": b.message, "http": b.http, "details": b.details} for b in blocked],
                     "warnings": warnings, "instrument_id": iid, "account_id": payload.get("account_id"), "side": str(payload.get("side") or "").upper()}
        if inst is not None:
            out["instrument"] = {"instrument_id": inst.instrument_id, "symbol": inst.symbol, "name": inst.name, "currency": inst.currency, "lot": inst.lot,
                                 "support": inst.support, "type": _type_of(inst), "settle_days": inst.settle_days, "session_group": inst.session_group,
                                 "limited_reason": (inst.spec or {}).get("limited_reason")}
            out["session"] = _session_for(services, inst, now)
        if t is None:
            view = snapshot.mark(iid)
            out["mark"] = view.to_dict() if view is not None else None
            return out
        net = -(t.clean_c + t.accrued_c + t.fee_c) if t.side == "BUY" else (t.clean_c + t.accrued_c - t.fee_c)
        fund_view = _funding_view(services, conn, t, snapshot, now)
        if fund_view.get("refused"):
            mf = int(fund_view.get("max_face") or 0)
            out["blockers"].append({"code": "INSUFFICIENT_FUNDS", "http": 409,
                                    "details": {"currency": t.currency, "need_c": fund_view["need_c"], "covered_c": fund_view.get("covered_c"), "max_face": mf},
                                    "message": f"{fund_view['note']}: the booking would be refused - "
                                               + (f"buy at most {mf:,} face now, or add cash to Treasury first" if mf > 0 else "add cash to Treasury first")})
            out["ok"] = False
        if t.currency != "USD" and fund_view.get("shortfall_c", 0) > 0 and int(fund_view.get("treasury_available_c") or 0) < int(fund_view["shortfall_c"]) and not (
                (fund_view.get("fx_conversion") or {}).get("executable")):
            out["blockers"].append({"code": "INSUFFICIENT_FUNDS", "http": 409, "details": {"currency": t.currency, "shortfall_c": fund_view["shortfall_c"]},
                                    "message": f"the account and Treasury hold no {t.currency} for this {t.currency} {fmt_money(fund_view['need_c'])}: buy {t.currency} with"
                                               f" an FX spot deal first (it settles T+2, like the bond); no FX conversion at booking is available"})
            out["ok"] = False
        dv, dv_in = (None, {})
        try:
            dv, dv_in = dv01_c(services, t.inst, t.face if t.side == "BUY" else -t.face, snapshot)
        except Exception:   # noqa: BLE001 - informative only
            dv = None
        out.update({"face": t.face, "currency": t.currency, "mark": t.mark.to_dict() if hasattr(t.mark, "to_dict") else None, "mark_price": str(t.mark_price),
                    "half_spread": str(t.half_spread), "half_spread_basis": t.half_spread_basis, "price": str(t.clean),
                    "price_basis": f"the mark {t.mark_price} {'+' if t.side == 'BUY' else '-'} the half-spread {t.half_spread} per 100",
                    "yield_pct": _s(t.yield_pct), "accrued": str(t.accrued), "clean_c": t.clean_c, "accrued_c": t.accrued_c, "fee_c": t.fee_c, "fee_basis": t.fee_basis,
                    "net_cash_c": net, "trade_date": t.trade_date.isoformat(), "settle_date": t.settle_date.isoformat(), "settlement": f"T+{t.inst.settle_days}",
                    "position_before": t.position, "position_after": t.position + (t.face if t.side == "BUY" else -t.face), "dv01_change_c": dv, "dv01_inputs": dv_in,
                    "funding": fund_view,
                    "arithmetic": (f"clean {t.face:,} x {t.clean} / 100 = {fmt_money(t.clean_c)}; accrued {t.face:,} x {t.accrued} / 100 = {fmt_money(t.accrued_c)};"
                                   f" fee {fmt_money(t.fee_c)}; {'pay' if t.side == 'BUY' else 'receive'} {t.currency} {fmt_money(abs(net))} on {t.settle_date.isoformat()}")})
        return out


# ---- bonds.trade ------------------------------------------------------------------------------------------------------------------------
def _quote_json(v: BondTrade) -> str:
    m = v.mark
    return dumps({"mark": m.to_dict() if hasattr(m, "to_dict") else None, "mark_price": None if v.mark_price is None else str(v.mark_price), "clean": str(v.clean),
                  "half_spread": str(v.half_spread), "half_spread_basis": v.half_spread_basis, "accrued": str(v.accrued),
                  "dirty": str(qprice(v.clean + v.accrued)), "yield_pct": _s(v.yield_pct),
                  "basis": "dealt at the mark +/- the disclosed half-spread; ACT/ACT accrued of the trade date (EXECUTION, ACCRUED)",
                  "terms": v.terms.to_dict(), "trade_date": v.trade_date.isoformat(), "currency": v.currency})


def _insert_fill(ctx: TxContext, v: BondTrade, fill_id: str, effect: str, entry_id: int, idem: str, origin: str = "ORDER", basis: str = "model",
                 settle: Optional[date] = None, price: Optional[Decimal] = None, gross_c: Optional[int] = None, fee_c: Optional[int] = None, quote_json: Optional[str] = None,
                 trade_date: Optional[date] = None) -> None:
    ctx.conn.execute(
        "INSERT INTO fills(fill_id, order_id, account_id, instrument_id, side, qty, price, gross_c, fee_c, currency, fx_rate, ts, trade_date, settle_date,"
        " origin, price_basis, quote_json, bar_ts, cap_qty, cap_note, entry_id, idem_key) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (fill_id, None, v.account_id, v.inst.instrument_id, effect, int(v.face), str(price if price is not None else v.clean), int(gross_c if gross_c is not None else v.clean_c),
         int(fee_c if fee_c is not None else v.fee_c), v.currency, str(v.fx_rate if v.currency != "USD" else 1), ctx.ts, (trade_date or v.trade_date).isoformat(),
         settle.isoformat() if settle else None, origin, basis, quote_json if quote_json is not None else _quote_json(v), None, None, None, entry_id, idem))


def _settlement(ctx: TxContext, v: BondTrade, fill_id: str, amount_c: int, gl: str) -> str:
    settle_id = next_id(ctx.conn, "settlement")
    ctx.conn.execute("INSERT INTO settlements(settle_id, fill_id, fx_deal_id, account_id, leg, currency, amount_c, gl, due_date, status) VALUES (?,?,?,?,?,?,?,?,?,'PENDING')",
                     (settle_id, fill_id, None, v.account_id, "CASH", v.currency, int(amount_c), gl, v.settle_date.isoformat()))
    return settle_id


def _fund(ctx: TxContext, v: BondTrade, total: int, fill_id: str, services):
    if v.currency == "USD":
        return execution.fund(ctx, v.account_id, "USD", total, "fill", fill_id, v.settle_date, services=services)
    try:
        return execution.fund(ctx, v.account_id, v.currency, total, "fill", fill_id, v.settle_date, services=services, fx_convert=True)
    except TypeError:      # an execution.fund without the FX conversion (C12 b) yet: own + Treasury cash in the currency only
        return execution.fund(ctx, v.account_id, v.currency, total, "fill", fill_id, v.settle_date, services=services)


def h_trade(ctx: TxContext, payload: dict) -> dict:
    """``bonds.trade``: module docstring EXECUTION / FILL.  One immediate fill at the mark +/- the half-spread inside the
    command's transaction; returns the fill, the entry lines' amounts, the settlement row, the lot and the accrual state."""
    services = _services(ctx)
    conn = ctx.conn
    v = validate_trade(services, payload, conn, ctx.ts, ctx.marks)
    aid, inst, iid, ccy = v.account_id, v.inst, v.inst.instrument_id, v.currency
    fill_id = next_id(conn, "fill")
    idem_fill = f"bonds:{ctx.request_id}"
    funding = None
    st = v.state
    cum_before = int(st["cum_c"]) if st is not None else 0
    hs_txt = f"mark {v.mark_price} {'+' if v.side == 'BUY' else '-'} half-spread {v.half_spread}"
    basis = "fedinvest" if getattr(v.mark, "method", "") == "fedinvest" else "model"
    if v.side == "BUY":
        total = v.clean_c + v.accrued_c + v.fee_c
        funding = _fund(ctx, v, total, fill_id, services)
        lines = [dr(aid, "1110", ccy, v.clean_c, instrument_id=iid, memo=f"clean price {v.clean} per 100 ({hs_txt})")]
        if v.accrued_c:
            lines.append(dr(aid, "1220", ccy, v.accrued_c, instrument_id=iid, memo=f"accrued interest purchased {v.accrued} per 100 (ACT/ACT)"))
        if v.fee_c:
            lines.append(dr(aid, "5000", ccy, v.fee_c, instrument_id=iid, memo=v.fee_basis))
        lines.append(cr(aid, "2100", ccy, total, memo="payable at settlement"))
        memo = (f"BUY {v.face:,} {inst.symbol} @ {v.clean} clean ({hs_txt}) + {v.accrued} accrued, fee {ccy} {fmt_money(v.fee_c)}; "
                f"settles {v.settle_date.isoformat()} (EXECUTION, ACCRUED, COST_BASIS)")
        entry_id = ctx.post(Entry("FILL", memo, lines, f"fill:{fill_id}", ref_type="fill", ref_id=fill_id))
        _insert_fill(ctx, v, fill_id, "BUY", entry_id, idem_fill, settle=v.settle_date, basis=basis)
        lot_id = open_lot(ctx.tx, aid, iid, "LONG", fill_id, v.face, v.clean, v.clean_c, ccy)
        settle_id = _settlement(ctx, v, fill_id, -total, "2100")
        reliefs: List[dict] = []
        cost_relieved = realised = relieved_accr = income = 0
        new_face = v.position + v.face
        through = date.fromisoformat(st["accrued_through"]) if st is not None and st["accrued_through"] else v.trade_date
        cum_after = cum_before + v.accrued_c
        net_cash = -total
    else:
        conn.execute("PRAGMA defer_foreign_keys=ON")     # lot_relief rows precede their fill row inside this transaction
        rl = relieve_fifo(ctx.tx, aid, iid, "LONG", v.face, fill_id, v.clean_c)
        cost_relieved = sum(r.cost_relieved_c for r in rl)
        realised = sum(r.realised_c for r in rl)
        reliefs = [r.__dict__ for r in rl]
        relieved_accr = cum_before if v.face == v.position else to_c(Decimal(cum_before) / HUNDRED * Decimal(v.face) / Decimal(v.position))
        income = v.accrued_c - relieved_accr
        receivable = v.clean_c + v.accrued_c - v.fee_c
        lines = [dr(aid, "1200", ccy, receivable, memo="receivable at settlement (clean + accrued - fee)")]
        if v.fee_c:
            lines.append(dr(aid, "5000", ccy, v.fee_c, instrument_id=iid, memo=v.fee_basis))
        if cost_relieved:
            lines.append(cr(aid, "1110", ccy, cost_relieved, instrument_id=iid, memo="cost relieved FIFO"))
        if relieved_accr:
            lines.append(cr(aid, "1220", ccy, relieved_accr, instrument_id=iid, memo="accrued interest carried, relieved pro rata"))
        if income:
            lines.append(cr(aid, "4300", ccy, income, instrument_id=iid, memo="accrued received beyond the carried accrual (interest since the last night)"))
        if realised:
            lines.append(cr(aid, "4000", ccy, realised, instrument_id=iid, memo="realised (clean - cost; accrued is income)"))
        memo = (f"SELL {v.face:,} {inst.symbol} @ {v.clean} clean ({hs_txt}) + {v.accrued} accrued, fee {ccy} {fmt_money(v.fee_c)}, FIFO cost {fmt_money(cost_relieved)},"
                f" realised {fmt_money(realised)}; settles {v.settle_date.isoformat()} (EXECUTION, ACCRUED, FIFO)")
        entry_id = ctx.post(Entry("FILL", memo, lines, f"fill:{fill_id}", ref_type="fill", ref_id=fill_id))
        _insert_fill(ctx, v, fill_id, "SELL", entry_id, idem_fill, settle=v.settle_date, basis=basis)
        lot_id = None
        settle_id = _settlement(ctx, v, fill_id, receivable, "1200")
        new_face = v.position - v.face
        through = date.fromisoformat(st["accrued_through"]) if st is not None and st["accrued_through"] else v.trade_date
        cum_after = cum_before - relieved_accr
        net_cash = receivable
    if new_face > 0:
        _write_state(conn, aid, iid, v.terms, new_face, through, cum_after)
    else:
        _delete_state(conn, aid, iid)
    ctx.touched_instruments.add((aid, iid))
    ctx.touched_accounts.add(aid)
    ctx.event("BOND_TRADE", account_id=aid, instrument_id=iid, ref_type="fill", ref_id=fill_id, side=v.side, face=v.face, clean=str(v.clean), accrued=str(v.accrued),
              mark=str(v.mark_price), half_spread=str(v.half_spread), clean_c=v.clean_c, accrued_c=v.accrued_c, fee_c=v.fee_c, realised_c=realised,
              settle_date=v.settle_date.isoformat(), entry_id=entry_id, currency=ccy, funding=None if funding is None else funding.to_dict())
    new_qty, _ = position(conn, aid, iid)
    return {"fill": {"fill_id": fill_id, "entry_id": entry_id, "account_id": aid, "instrument_id": iid, "side": v.side, "qty": v.face, "face": v.face, "price": str(v.clean),
                     "price_basis": basis, "origin": "ORDER", "gross_c": v.clean_c, "accrued_c": v.accrued_c, "fee_c": v.fee_c, "fee_basis": v.fee_basis,
                     "trade_date": v.trade_date.isoformat(), "settle_date": v.settle_date.isoformat(), "idem_key": idem_fill, "memo": memo, "currency": ccy,
                     "mark": str(v.mark_price), "half_spread": str(v.half_spread)},
            "economics": {"clean": str(v.clean), "mark": str(v.mark_price), "half_spread": str(v.half_spread), "half_spread_basis": v.half_spread_basis,
                          "accrued": str(v.accrued), "dirty": str(qprice(v.clean + v.accrued)), "yield_pct": _s(v.yield_pct), "clean_c": v.clean_c,
                          "accrued_c": v.accrued_c, "fee_c": v.fee_c, "net_cash_c": net_cash, "net_cash": str(from_c(net_cash)), "cost_relieved_c": cost_relieved,
                          "realised_c": realised, "accrued_relieved_c": relieved_accr, "accrued_income_c": income, "currency": ccy},
            "settlement": {"settle_id": settle_id, "due_date": v.settle_date.isoformat(), "amount_c": net_cash, "gl": "2100" if v.side == "BUY" else "1200", "status": "PENDING",
                           "currency": ccy},
            "lot_id": lot_id, "reliefs": reliefs, "position_qty": new_qty, "funding": None if funding is None else funding.to_dict(),
            "accrual_state": None if new_face <= 0 else {"face": new_face, "cum_c": cum_after, "accrued_through": through.isoformat()},
            "mark": v.mark.to_dict() if hasattr(v.mark, "to_dict") else None,
            "note": f"dealt at the {('TreasuryDirect FedInvest' if basis == 'fedinvest' else 'computed')} mark {v.mark_price} {'+' if v.side == 'BUY' else '-'} the disclosed"
                    f" half-spread {v.half_spread} per 100; no dealer quote was observed", "terms": v.terms.to_dict()}


# ---- the instrument page (C5) ----------------------------------------------------------------------------------------------------
TYPE_LABEL = {"BILL": "Treasury bill", "NOTE": "Treasury note", "BOND": "Treasury bond", "TIPS": "Treasury inflation-protected security (TIPS)",
              "FRN": "Treasury floating-rate note", "CORP": "generic corporate (no real issuer)", "GOV": "generic government benchmark", "LEGACY": "bond (Build 1 row)"}


def instrument_extra(rt, inst, now: int) -> dict:
    """C5: the instrument page of a bond: the headline price with its source and as-of, the labelled terms and the actions."""
    q = quote_view(rt, inst, now)
    m = q["mark"] or {}
    spec = inst.spec or {}
    t = q["type"]
    terms = bond_terms(inst)
    spec_view = [{"label": "type", "value": TYPE_LABEL.get(t, t)}]
    if spec.get("cusip"):
        spec_view.append({"label": "CUSIP", "value": spec["cusip"]})
    spec_view += [{"label": "issuer", "value": spec.get("issuer") or "n/a"},
                  {"label": "coupon", "value": "zero coupon (discount)" if terms.zero_coupon and t == "BILL" else ("floating (13-week bill + spread)" if t == "FRN"
                                                                                                                     else f"{terms.coupon_pct} % {'annual' if terms.freq == 1 else 'semi-annual'}")},
                  {"label": "maturity", "value": terms.maturity.isoformat()},
                  {"label": "issue date", "value": spec.get("issue") or "not in the source record"},
                  {"label": "day count", "value": spec.get("daycount") or "ACT/ACT"},
                  {"label": "face lot", "value": f"{inst.currency} {inst.lot:,}"},
                  {"label": "settlement", "value": f"T+{inst.settle_days} in {inst.currency}"}]
    if spec.get("outstanding_musd"):
        spec_view.append({"label": "amount outstanding", "value": f"USD {Decimal(spec['outstanding_musd']):,.0f} mn (MSPD)"})
    if spec.get("rating"):
        spec_view.append({"label": "rating bucket", "value": f"{spec['rating']} ({spec.get('grade') or ''})".strip()})
    if spec.get("oas_series"):
        spec_view.append({"label": "spread series", "value": f"ICE BofA {spec.get('bucket')} OAS (FRED {spec['oas_series']})"})
    spec_view.append({"label": "source", "value": spec.get("source") or "n/a"})
    if spec.get("limited_reason"):
        spec_view.append({"label": "LIMITED", "value": spec["limited_reason"]})
    headline = {"value": q["clean"], "quality": m.get("quality"), "as_of": m.get("as_of"), "as_of_date": (m.get("inputs") or {}).get("as_of_date"),
                "source": m.get("source"), "note": m.get("note"), "method": m.get("method"), "yield_pct": q["yield_pct"], "accrued": q["accrued"],
                "dirty": q["dirty"], "dv01_per_1m": q["dv01_per_1m"], "discount_rate_pct": q.get("discount_rate_pct"), "inputs": m.get("inputs"),
                "label": "clean price per 100" + (" (real, per 100 of indexed principal)" if t == "TIPS" else "")}
    actions = [] if inst.support != "FULL" else [{"key": "buy", "label": "Buy", "side": "BUY", "ticket": "BOND"}, {"key": "sell", "label": "Sell", "side": "SELL", "ticket": "BOND"}]
    return {"headline": headline, "spec_view": spec_view, "actions": actions, "executable_here": q["executable_here"], "half_spread": q["half_spread"],
            "half_spread_basis": q["half_spread_basis"], "note": q["note"], "ticket": "BOND"}


# ---- the lifecycle handlers and key helpers live in bonds_jobs.py (the ~900-line rule); they resolve lazily here ----------------------------------
_JOB_EXPORTS = ("h_accrue", "h_coupon", "h_mature", "h_list", "accrue_keys", "coupon_keys", "maturity_keys", "listing_keys")


def __getattr__(name: str):
    """``bonds.h_accrue`` / ``bonds.accrue_keys`` ... keep their documented import path: the names are resolved from
    ``bonds_jobs`` on first access, which works in either import order (PEP 562)."""
    if name in _JOB_EXPORTS:
        from . import bonds_jobs
        return getattr(bonds_jobs, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def register(bus, services=None) -> None:
    """Register ``bonds.trade`` (ui), ``bonds.accrue`` / ``bonds.coupon`` / ``bonds.mature`` (lifecycle), the light
    ``bonds.list`` and the debt master's light commands (``debt.sync``, ``debt.prices``); the runtime binds this module as
    ``services.bonds``."""
    from . import bonds_jobs as jobs
    from . import treasuries
    bus.register("bonds.trade", h_trade)
    bus.register("bonds.accrue", jobs.h_accrue)
    bus.register("bonds.coupon", jobs.h_coupon)
    bus.register("bonds.mature", jobs.h_mature)
    bus.register("bonds.list", jobs.h_list, light=True)
    treasuries.register(bus, services)


def bind(rt) -> None:
    """The BOND mark (``marks.register_kind``); the debt store and refresher come with products/treasuries.py's bind."""
    marks = getattr(rt, "marks", None)
    if marks is not None and hasattr(marks, "register_kind"):
        marks.register_kind("BOND", bond_mark)


def register_routes(r) -> None:
    """C1: POST /api/bonds/preview (no write) and POST /api/bonds/trade (the command)."""
    from ..api import run_command

    def bonds_preview(req):
        return preview(req.rt, dict(req.body or {}), req.now)

    def bonds_trade(req):
        return run_command(req, "bonds.trade", {k: v for k, v in (req.body or {}).items() if k != "request_id"})

    r.add("POST", r"/api/bonds/preview", bonds_preview, mutating=False)
    r.add("POST", r"/api/bonds/trade", bonds_trade)


__all__ = ["BondTerms", "bond_terms", "period_of", "coupon_period", "accrued_per_100", "accrued_cents", "coupon_cents", "coupon_dates", "prev_coupon_date",
           "round_to_eighth", "benchmark_id", "benchmark_name", "through_night", "flag_on", "ensure_bond", "expire_matured", "face_at", "state_row", "quote_view",
           "validate_trade", "check_trade", "preview", "h_trade", "h_accrue", "h_coupon", "h_mature", "h_list", "accrue_keys", "coupon_keys", "maturity_keys",
           "listing_keys", "register", "bind", "register_routes", "bond_mark", "price_at", "yield_from_clean", "dv01_per_100", "dv01_c", "reprice_c", "half_spread",
           "instrument_extra", "executable_mark", "discount_rate", "JOB_ACCRUE", "JOB_COUPON", "JOB_MATURITY", "JOB_LISTING", "SETTING_FLAG", "UNSUPPORTED_TEXT",
           "CLOSED_TEXT", "BENCHMARK_TENORS", "HALF_SPREAD"]
