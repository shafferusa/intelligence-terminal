"""Strategy templates: the old Calculations page's 60 multi-leg templates and their resolution against today's chain
(TERMINAL_DESIGN.md §13.16, §19.22; TERMINAL_WORKPLAN.md WP-24).

Conventions
- ``GROUPS`` / ``STRATEGIES`` are a verbatim copy of ``finsim/engines/playbook.py`` (60 entries in 12 groups).  That module
  is never imported here: it pulls in the simulation ``World``.  Leg rules are unchanged: ``kind STOCK | OPTION | FUTURE |
  TRS``, ``side BUY | SELL``, ``size`` (a multiple of the base units), option ``type C | P`` and ``strike "ATM" | "pct:<n>" |
  "zero_cost"``, an optional per-leg ``tenor`` in months, ``other: true`` for the second name of a relative-value template.
- Resolution is pure and Decimal.  ``resolve_strike`` takes ``ATM`` (target = spot), ``pct:<n>`` (spot × (1 + n/100)) or an
  override — a bare value with |v| < 60 is a percent, otherwise an absolute strike — and returns the nearest LISTED strike,
  ties to the lower one (the old ``min(key=(abs, k))``).  ``zero_cost_call`` is the call strike above spot whose bid is
  nearest the bought put's ask (fallback ``pct:+10``); a call without a bid cannot pay for anything and is skipped.
  ``expiry_for_tenor`` is the nearest listed expiry on or after today + months (calendar months, the day clamped to the
  month end); None when nothing that far out is listed (the template is then "not available").
- Sizing (``size_legs``): stock ``round(units × size)``; options ``max(1, round(units × size / 100))`` contracts; futures
  ``max(1, round(units × size × spot / (F × multiplier)))`` against the front ES (NQ for Nasdaq underlyings), i.e.
  notional-matched; TRS ``round(units × size)``.  Every rounding is HALF_EVEN like the rest of the terminal.
- Prices (never invented): options ask for buys / bid for sells from the cached Cboe chain; stock from the quote record —
  the chain's underlying ask/bid when a FRESH chain is cached, else the Yahoo last; futures last; a TRS leg at the
  underlying's level with the PB's dealt spread from ``services.swaps.preview`` (absent -> the template is not available).
  A leg without a price makes the whole template ``NOT_AVAILABLE``; a chain that is not cached raises ``ChainPending``
  (409 ``CHAIN_PENDING`` on a submit; the preview view answers 202 ``{"status": "fetching"}`` while the worker fetches it).
- Order sides handed to ``order.submit``: stock BUY / SELL_SHORT (a short stock leg goes through the borrow first), options
  BTO / STO, futures BUY / SELL; a TRS leg has no instrument id — it is booked through the OTC desk (``booking: otc.book``).
- ``fill_mode``: AON for option-only templates on one underlying with one or two expiries, else SEQUENTIAL with the first
  stock / futures leg as the primary (sentence AON).
- Payoff: P&L at the near expiry on a ±35 % grid of 71 points plus every strike as an extra knot, so the breakevens of a
  piecewise-linear payoff are exact (the old 71-point interpolation alone is off by a few cents when a kink sits between
  two grid points).  Each leg is measured from its estimated price (ask for buys, bid for sells); options at intrinsic plus
  the premium paid or received; swaps and futures linear; a second name assumed to move with the underlying; fees are
  reported separately and excluded from the grid, as the old ``_payoff`` did.
- Option margin: the §13.3 formula on the package's own legs — vertical spreads width × 100 net of the credit (estimated
  prices), naked contracts the Reg T-style formula (index options 15 % / 10 %), calls covered by shares bought in the same
  package 0, spreads of opposite rights on one expiry take the larger side (one side loses at expiry).  When WP-03's
  ``services.options.margin_requirement_c`` is bound, the incremental requirement against the account's positions is also
  reported; the API labels which one it shows.
"""
from __future__ import annotations

import calendar as _calendar
import hashlib
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import tables
from .books import account_kind_for, account_row, eligibility
from .commands import Command, ConflictError, RejectError
from .execution import fee_for
from .instruments import EQUITY_KINDS, INDEX_OPTION_ROOTS
from .orders_preview import quote_of
from .quotes import QUALITY_RANK, QuoteRecord, freshness
from .quotes.cache import record_from_chain_row
from .strategies_catalogue import BY_KEY, GROUPS, STRATEGIES
from .units import from_c, mul_c, qprice, to_c

# ===== the catalogue: GROUPS / STRATEGIES / BY_KEY are the verbatim playbook copy in strategies_catalogue.py (re-exported) ========
GROUP_LABELS = dict(GROUPS)

# ===== constants ==================================================================================================================
D = Decimal
ONE = D(1)
HUNDRED = D(100)
CENT = D("0.01")
PCT_OVERRIDE_LIMIT = D(60)          # an override with |v| < 60 is a percent from spot, else an absolute strike
GRID_POINTS = 71                     # ±35 % in 1 % steps, as the old _payoff
OPTION_MULT = 100
DEFAULT_UNITS = 1000
DEFAULT_TENOR_MONTHS = 3
DEFAULT_UNDERLYING = "SPY"
DEFAULT_OTHER = "QQQ"
NASDAQ_UNDERLYINGS = ("QQQ", "NDX")
NASDAQ_EXCHANGES = ("NMS", "NGM", "NCM")
LISTED_KINDS = ("STOCK", "OPTION", "FUTURE")
LISTED_ACCOUNT_KINDS = ("US_EXCHANGE", "PB")
FETCH_RETRY_MS = 1500
NAKED_PCT = {"equity": (D("0.20"), D("0.10")), "index": (D("0.15"), D("0.10"))}
CONVENTION_KEYS = ("FILL_PRICE", "AON", "OPT_MARGIN", "FUT_MARGIN", "STALE")
GRID_NOTE = ("P&L at expiry versus the underlying's price (±35 %): every leg from its estimated price (ask for buys, bid for sells), "
             "options at intrinsic plus the premium; swaps and futures treated as linear, a second name assumed to move with the "
             "underlying; fees are shown separately and excluded")
MIXED_EXPIRY_NOTE = (" · legs expire on different dates: every leg is valued at intrinsic on the near expiry, so this understates the "
                     "later leg's remaining value")


class ChainPending(ConflictError):
    """The underlying's chain is not cached: a fetch has been queued (409 CHAIN_PENDING on a submit; the preview answers 202)."""

    def __init__(self, underlying_id: str):
        super().__init__("CHAIN_PENDING", f"the option chain for {underlying_id} is being fetched; retry in {FETCH_RETRY_MS} ms",
                         {"underlying": underlying_id, "retry_ms": FETCH_RETRY_MS})
        self.underlying_id = underlying_id


# ===== small helpers =============================================================================================================
def _dec(x, what: str = "value") -> Decimal:
    if isinstance(x, Decimal):
        return x
    if x is None or isinstance(x, bool):
        raise ValueError(f"{what}: not a number ({x!r})")
    if isinstance(x, float):
        return Decimal(repr(x))
    try:
        return Decimal(str(x).replace(",", "").strip())
    except InvalidOperation as e:
        raise ValueError(f"{what}: not a number ({x!r})") from e


def _round_int(x: Decimal) -> int:
    return int(x.quantize(ONE, rounding=ROUND_HALF_EVEN))


def _q2(x) -> Decimal:
    return _dec(x).quantize(CENT, rounding=ROUND_HALF_EVEN)


def _s(x) -> Optional[str]:
    return None if x is None else str(x)


def _attr(r, name: str):
    return r.get(name) if isinstance(r, dict) else getattr(r, name, None)


def _as_date(x) -> Optional[date]:
    if isinstance(x, date):
        return x
    try:
        return date.fromisoformat(str(x)[:10])
    except (TypeError, ValueError):
        return None


def _int_param(v, code: str, floor: int) -> int:
    try:
        n = int(_dec(v, code.lower()))
    except ValueError as e:
        raise RejectError(code, f"{code.lower()} must be a whole number, got {v!r}") from e
    if n < floor:
        raise RejectError(code, f"{code.lower()} must be at least {floor}, got {n}")
    return n


# ===== pure resolution (§13.16, tested in test_terminal_strategies.py) ==============================================================
def parse_rule(rule) -> Tuple[str, Optional[Decimal]]:
    """``"ATM"`` -> ``("ATM", None)``; ``"pct:-7"`` -> ``("PCT", -7)``; ``"zero_cost"`` -> ``("ZERO_COST", None)``; a bare
    number is an override: |v| < 60 -> ``("PCT", v)``, otherwise ``("ABS", v)``.  ValueError for anything else."""
    if isinstance(rule, (int, float, Decimal)) and not isinstance(rule, bool):
        v = _dec(rule, "strike override")
        return ("PCT", v) if abs(v) < PCT_OVERRIDE_LIMIT else ("ABS", v)
    s = str(rule if rule is not None else "").strip()
    u = s.upper()
    if u == "ATM":
        return "ATM", None
    if u == "ZERO_COST":
        return "ZERO_COST", None
    if u.startswith("PCT:"):
        return "PCT", _dec(s[4:], "strike rule")
    v = _dec(s, "strike rule")
    return ("PCT", v) if abs(v) < PCT_OVERRIDE_LIMIT else ("ABS", v)


def resolve_strike(rule, spot, listed: Sequence) -> Decimal:
    """The listed strike nearest the rule's target (``ATM`` -> spot; ``pct:<n>`` -> spot × (1 + n/100); an absolute override ->
    itself); ties go to the LOWER strike, as the old ``min(strikes, key=(abs(k - target), k))``.  ValueError with no strikes."""
    strikes = sorted({qprice(k) for k in listed})
    if not strikes:
        raise ValueError("no strikes listed")
    mode, v = parse_rule(rule)
    s = qprice(spot)
    if mode == "ATM":
        target = s
    elif mode == "PCT":
        target = s * (ONE + v / HUNDRED)
    elif mode == "ABS":
        target = v
    else:
        raise ValueError("a zero_cost strike resolves through zero_cost_call()")
    return min(strikes, key=lambda k: (abs(k - target), k))


def zero_cost_call(rows, put_premium, spot) -> Optional[Decimal]:
    """The call strike ABOVE spot whose bid is nearest the bought put's ask; rows are chain rows (``right``, ``strike``,
    ``bid``); calls without a bid are skipped; ties keep the lower strike; None when no call qualifies."""
    s = qprice(spot)
    prem = qprice(put_premium)
    best: Optional[Tuple[Decimal, Decimal]] = None
    for r in sorted(rows, key=lambda r: qprice(_attr(r, "strike") or 0)):
        if _attr(r, "right") != "C":
            continue
        k, bid = _attr(r, "strike"), _attr(r, "bid")
        if k is None or qprice(k) <= s or bid is None or qprice(bid) <= 0:
            continue
        d = abs(qprice(bid) - prem)
        if best is None or d < best[0]:
            best = (d, qprice(k))
    return None if best is None else best[1]


def add_months(d: date, months: int) -> date:
    """Calendar months with the day clamped to the target month's length (31 Jan + 1 -> 28/29 Feb)."""
    y, m = divmod(d.month - 1 + int(months), 12)
    y, m = d.year + y, m + 1
    return date(y, m, min(d.day, _calendar.monthrange(y, m)[1]))


def expiry_for_tenor(expiries, today: date, months: int) -> Optional[date]:
    """The nearest listed expiry on or after ``today + months`` (ISO strings or dates); None when none is listed that far."""
    target = add_months(today, months)
    cands = [e for e in (_as_date(x) for x in expiries) if e is not None and e >= target]
    return min(cands) if cands else None


@dataclass(frozen=True)
class LegSpec:
    """One sized leg: ``qty`` is shares (STOCK / TRS) or contracts (OPTION / FUTURE); None for a futures leg without a price."""
    index: int
    kind: str
    side: str
    size: Decimal
    qty: Optional[int]
    type: Optional[str] = None
    strike_rule: Optional[str] = None
    tenor: Optional[int] = None
    other: bool = False

    def to_dict(self) -> dict:
        return {"index": self.index, "kind": self.kind, "side": self.side, "size": str(self.size), "qty": self.qty, "type": self.type,
                "strike_rule": self.strike_rule, "tenor": self.tenor, "other": self.other}


def size_legs(strategy: dict, units: int, spot, fut_px=None, fut_mult=None) -> List[LegSpec]:
    """§13.16 sizing: stock ``round(units × size)``; options ``max(1, round(units × size / 100))``; futures
    ``max(1, round(units × size × spot / (F × mult)))`` (None when no futures price); TRS ``round(units × size)``.  HALF_EVEN."""
    n_units = int(units)
    if n_units < 1:
        raise ValueError("units must be at least 1")
    s = qprice(spot)
    out: List[LegSpec] = []
    for i, leg in enumerate(strategy["legs"]):
        kind = leg["kind"]
        size = _dec(leg.get("size", 1), "size")
        base = D(n_units) * size
        if kind in ("STOCK", "TRS"):
            qty: Optional[int] = _round_int(base)
        elif kind == "OPTION":
            qty = max(1, _round_int(base / HUNDRED))
        elif kind == "FUTURE":
            if fut_px is None or fut_mult is None or qprice(fut_px) <= 0:
                qty = None
            else:
                qty = max(1, _round_int(base * s / (qprice(fut_px) * _dec(fut_mult, "multiplier"))))
        else:
            raise ValueError(f"unknown leg kind {kind!r}")
        out.append(LegSpec(i, kind, leg["side"], size, qty, leg.get("type"), leg.get("strike"), leg.get("tenor"), bool(leg.get("other"))))
    return out


def fill_mode_for(strategy: dict) -> str:
    """AON for option-only templates on one underlying with one or two expiries; SEQUENTIAL otherwise (sentence AON)."""
    legs = strategy["legs"]
    if all(l["kind"] == "OPTION" and not l.get("other") for l in legs) and len({l.get("tenor") for l in legs}) <= 2:
        return "AON"
    return "SEQUENTIAL"


def order_side(kind: str, side: str) -> str:
    """Template side -> the side ``order.submit`` validates: stock BUY / SELL_SHORT, options BTO / STO, futures BUY / SELL,
    TRS RECEIVE / PAY (booked through the OTC desk, not the ticket)."""
    if kind == "STOCK":
        return "BUY" if side == "BUY" else "SELL_SHORT"
    if kind == "OPTION":
        return "BTO" if side == "BUY" else "STO"
    if kind == "TRS":
        return "RECEIVE" if side == "BUY" else "PAY"
    return side


# ===== package shapes =============================================================================================================
@dataclass
class PackageLeg:
    index: int
    kind: str
    symbol: str
    instrument_id: Optional[str]
    side: str                          # the order side (BUY / SELL_SHORT / BTO / STO / BUY / SELL / RECEIVE / PAY)
    template_side: str                 # BUY | SELL as in the template
    qty: int
    est_price: Optional[Decimal]
    price_basis: str
    cash_c: int                        # signed: negative = paid
    fee_c: int
    fee_basis: str
    multiplier: Decimal
    order_type: str = "MARKET"
    strike: Optional[Decimal] = None
    expiry: Optional[str] = None
    right: Optional[str] = None
    tenor_months: Optional[int] = None
    pct_from_spot: Optional[Decimal] = None
    delta_shares: Optional[Decimal] = None
    vega: Optional[Decimal] = None
    theta: Optional[Decimal] = None
    iv: Optional[float] = None
    notional_c: Optional[int] = None
    margin_c: Optional[int] = None
    quote: Dict[str, Any] = field(default_factory=dict)
    booking: str = "order.submit"
    materialised: bool = True
    other: bool = False
    what: str = ""
    otc_preview: Optional[dict] = None

    def to_dict(self) -> dict:
        return {"index": self.index, "kind": self.kind, "symbol": self.symbol, "instrument_id": self.instrument_id, "side": self.side,
                "template_side": self.template_side, "qty": self.qty, "order_type": self.order_type, "est_price": _s(self.est_price),
                "price_basis": self.price_basis, "cash": str(from_c(self.cash_c)), "cash_c": self.cash_c, "fee": str(from_c(self.fee_c)), "fee_c": self.fee_c,
                "fee_basis": self.fee_basis, "multiplier": str(self.multiplier), "strike": _s(self.strike), "expiry": self.expiry, "right": self.right,
                "tenor_months": self.tenor_months, "pct_from_spot": _s(self.pct_from_spot), "delta_shares": _s(self.delta_shares), "vega": _s(self.vega),
                "theta": _s(self.theta), "iv": self.iv, "notional": None if self.notional_c is None else str(from_c(self.notional_c)),
                "notional_c": self.notional_c, "margin": None if self.margin_c is None else str(from_c(self.margin_c)), "margin_c": self.margin_c,
                "quote": dict(self.quote), "booking": self.booking, "materialised": self.materialised, "other": self.other, "what": self.what,
                "otc_preview": self.otc_preview}


@dataclass
class PackageSpec:
    strategy_key: str
    title: str
    group: str
    account_id: str
    underlying_id: str
    other_id: Optional[str]
    units: int
    tenor_months: int
    spot: Decimal
    legs: List[PackageLeg]
    fill_mode: str
    primary_index: Optional[int]
    expiry: Optional[str]
    totals: Dict[str, Any]
    margin: Dict[str, Any]
    payoff: Dict[str, Any]
    chain_quality: Optional[str]
    as_of: int
    warnings: List[str] = field(default_factory=list)
    missing_instruments: List[str] = field(default_factory=list)
    conventions: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        strat = BY_KEY[self.strategy_key]
        return {"strategy": {k: v for k, v in strat.items() if k != "legs"}, "strategy_key": self.strategy_key, "title": self.title, "group": self.group,
                "group_label": GROUP_LABELS.get(self.group, self.group), "account_id": self.account_id, "underlying_id": self.underlying_id,
                "other_id": self.other_id, "params": {"underlying": self.underlying_id, "other": self.other_id, "units": self.units, "tenor_months": self.tenor_months},
                "spot": str(self.spot), "legs": [l.to_dict() for l in self.legs], "fill_mode": self.fill_mode, "primary_index": self.primary_index,
                "expiry": self.expiry, "totals": dict(self.totals), "margin": dict(self.margin), "payoff": dict(self.payoff), "chain_quality": self.chain_quality,
                "as_of": self.as_of, "warnings": list(self.warnings), "missing_instruments": list(self.missing_instruments),
                "conventions": dict(self.conventions), "aon": self.fill_mode == "AON"}


# ===== payoff (the old _payoff, Decimal, exact breakevens) ===========================================================================
def payoff_grid(legs: Sequence[PackageLeg], spot, levels: Dict[str, Decimal], underlying_id: str, mixed_expiries: bool = False) -> dict:
    """P&L at the near expiry on 71 grid points (±35 %) plus every strike as an extra knot.  Returns ``payoff`` (pairs of
    strings), ``max_gain`` / ``max_loss`` (None when unlimited), ``unlimited_up`` / ``unlimited_down``, ``breakevens``, ``grid_note``."""
    s0 = qprice(spot)
    if s0 <= 0:
        raise ValueError("spot must be positive")
    grid = [_q2(s0 * D(65 + i) / HUNDRED) for i in range(GRID_POINTS)]
    lo, hi = grid[0], grid[-1]

    def scale(sym: str) -> Decimal:
        if sym == underlying_id:
            return ONE
        base = levels.get(sym)
        return ONE if base is None or base <= 0 else qprice(base) / s0

    knots = set(grid)
    for leg in legs:
        if leg.kind == "OPTION" and leg.strike is not None:
            k = _q2(leg.strike / scale(leg.symbol))
            if lo <= k <= hi:
                knots.add(k)
    pts: List[Tuple[Decimal, Decimal]] = []
    for st in sorted(knots):
        pnl = D(0)
        for leg in legs:
            sign = ONE if leg.template_side == "BUY" else -ONE
            st_sym = st * scale(leg.symbol)
            if leg.kind in ("STOCK", "TRS"):
                if leg.est_price is not None:
                    pnl += sign * D(leg.qty) * (st_sym - leg.est_price)
            elif leg.kind == "FUTURE":
                if leg.est_price is not None:
                    pnl += sign * D(leg.qty) * leg.multiplier * leg.est_price * (st / s0 - ONE)
            elif leg.kind == "OPTION" and leg.strike is not None:
                intrinsic = max(D(0), st_sym - leg.strike) if leg.right == "C" else max(D(0), leg.strike - st_sym)
                pnl += sign * D(leg.qty) * leg.multiplier * intrinsic + from_c(leg.cash_c)
        pts.append((st, _q2(pnl)))
    vals = [p for _, p in pts]
    linear_long = any(l.kind in ("STOCK", "TRS", "FUTURE") and l.template_side == "BUY" for l in legs)
    linear_short = any(l.kind in ("STOCK", "TRS", "FUTURE") and l.template_side == "SELL" for l in legs)
    sold_call = any(l.kind == "OPTION" and l.right == "C" and l.template_side == "SELL" for l in legs)
    bought_call = any(l.kind == "OPTION" and l.right == "C" and l.template_side == "BUY" for l in legs)
    unlimited_up = bool(legs) and linear_long and not sold_call
    unlimited_down = bool(legs) and linear_short and not bought_call
    if sold_call and not (bought_call or linear_long):        # a naked short call is unlimited too
        unlimited_down = True
    breakevens: List[Decimal] = []
    for (a, pa), (b, pb) in zip(pts, pts[1:]):
        if (pa < 0 <= pb) or (pa >= 0 > pb):
            be = a if pb == pa else _q2(a + (b - a) * (-pa) / (pb - pa))
            if be not in breakevens:
                breakevens.append(be)
    return {"payoff": [[str(st), str(p)] for st, p in pts], "max_gain": None if unlimited_up or not vals else str(max(vals)),
            "max_loss": None if unlimited_down or not vals else str(min(vals)), "unlimited_up": unlimited_up, "unlimited_down": unlimited_down,
            "breakevens": [str(b) for b in breakevens], "grid_note": GRID_NOTE + (MIXED_EXPIRY_NOTE if mixed_expiries else ""),
            "grid": {"points": len(pts), "low": str(lo), "high": str(hi), "band": "±35 %"}}


# ===== option margin on the package's own legs (§13.3) ===================================================================================
def package_margin_c(option_legs: Sequence[dict], spot, index: bool = False, long_shares: int = 0) -> Tuple[int, List[dict]]:
    """Requirement in cents for the short option legs of one package.  Each leg: ``{side (BUY|SELL), right, strike, expiry,
    qty, est_price, mark}`` (``mark`` = the contract's current mark, ``est_price`` the bid/ask the leg trades at).
    Covered calls (shares bought in the package, 100 per contract) 0; a vertical spread ``width × 100 × n`` net of the credit
    (short est_price − long est_price, floored at 0: a debit spread needs no margin beyond the debit paid); the rest naked: call ``max(prem + 0.20 S − OTM, prem + 0.10 S)``, put ``max(prem +
    0.20 S − OTM, prem + 0.10 K)`` per contract (index options 15 % / 10 %), ``prem`` = mark × 100.  Spreads of opposite rights
    on one expiry take the larger side (only one side can lose at expiry); naked requirements add up."""
    s = qprice(spot)
    pct_a, pct_b = NAKED_PCT["index" if index else "equity"]
    longs = [dict(l, left=int(l["qty"])) for l in option_legs if l["side"] == "BUY"]
    shares_left = int(long_shares)
    detail: List[dict] = []
    spreads: Dict[str, Dict[str, int]] = {}
    naked_total = 0
    for sl in sorted((l for l in option_legs if l["side"] == "SELL"), key=lambda l: (l.get("expiry") or "", l["right"], qprice(l["strike"]))):
        left = int(sl["qty"])
        k, right, exp = qprice(sl["strike"]), sl["right"], sl.get("expiry")
        est = qprice(sl["est_price"])
        if right == "C" and left and shares_left >= OPTION_MULT:
            n = min(left, shares_left // OPTION_MULT)
            shares_left -= n * OPTION_MULT
            left -= n
            detail.append({"leg": sl.get("index"), "rule": "covered call", "contracts": n, "requirement_c": 0, "requirement": "0.00",
                           "note": f"covered by {n * OPTION_MULT} shares bought in the package"})

        def width(l, _k=k, _right=right):
            return (qprice(l["strike"]) - _k) if _right == "C" else (_k - qprice(l["strike"]))
        cands = [l for l in longs if l["right"] == right and l.get("expiry") == exp and l["left"] > 0]
        cands.sort(key=lambda l: (max(D(0), width(l)), qprice(l["strike"])))
        for l in cands:
            if left <= 0:
                break
            n = min(left, l["left"])
            w = max(D(0), width(l))
            credit_c = to_c((est - qprice(l["est_price"])) * HUNDRED * n)
            req = max(0, to_c(w * HUNDRED * n) - max(0, credit_c))      # a debit spread adds nothing: its loss is the debit already paid
            spreads.setdefault(exp or "", {"C": 0, "P": 0})[right] += req
            l["left"] -= n
            left -= n
            detail.append({"leg": sl.get("index"), "rule": "vertical spread", "contracts": n, "with_strike": str(qprice(l["strike"])), "width": str(w),
                           "credit_c": credit_c, "requirement_c": req, "requirement": str(from_c(req)),
                           "note": f"width {w} × 100 × {n} = {from_c(to_c(w * HUNDRED * n))} net of credit {from_c(max(0, credit_c))}"
                                   + (" (a debit spread: covered by the long)" if credit_c < 0 else "")})
        if left > 0:
            prem = qprice(sl["mark"]) * HUNDRED
            otm = max(D(0), k - s) if right == "C" else max(D(0), s - k)
            a = prem + pct_a * s * HUNDRED - otm * HUNDRED
            b = prem + pct_b * (s if right == "C" else k) * HUNDRED
            per = max(a, b)
            req = to_c(per * left)
            naked_total += req
            detail.append({"leg": sl.get("index"), "rule": "naked " + ("call" if right == "C" else "put") + (" (index 15 % / 10 %)" if index else ""),
                           "contracts": left, "per_contract": str(_q2(per)), "requirement_c": req, "requirement": str(from_c(req)),
                           "note": f"max(premium {_q2(prem)} + {pct_a * 100:.0f} % × S × 100 − OTM {_q2(otm * HUNDRED)}, premium + {pct_b * 100:.0f} % × "
                                   f"{'S' if right == 'C' else 'K'} × 100) = {_q2(per)} per contract"})
    total = naked_total + sum(max(v["C"], v["P"]) for v in spreads.values())
    return total, detail


# ===== availability per template per account ===============================================================================================
def _nasdaq(inst) -> bool:
    return inst is not None and (inst.symbol.upper() in NASDAQ_UNDERLYINGS or str((inst.spec or {}).get("exchange") or "") in NASDAQ_EXCHANGES)


def front_month(services, root: str):
    """The earliest ACTIVE listed contract of ``root`` in the master (the front month), or None."""
    best = None
    for inst in services.instruments.all():
        if inst.kind == "FUTURE" and inst.status == "ACTIVE" and (inst.spec or {}).get("root") == root and inst.expiry:
            if best is None or inst.expiry < best.expiry:
                best = inst
    return best


def _check_name(services, inst, kinds: set, akind: str) -> Optional[str]:
    if inst.support in ("UNSUPPORTED", "COMPUTED"):
        return f"NOT AVAILABLE / {inst.symbol} is {inst.support.lower()} in this build"
    if inst.status != "ACTIVE":
        return f"NOT AVAILABLE / {inst.symbol} is {inst.status.lower()}"
    if "STOCK" in kinds or "TRS" in kinds:
        if inst.kind == "INDEX":
            return f"NOT AVAILABLE / {inst.symbol} is an index: no shares to trade (use the option or futures templates)"
        if "STOCK" in kinds and not services.instruments.tradeable_in(inst, akind):
            return f"NOT TRADEABLE HERE / {inst.symbol} ({inst.kind}) does not trade in a {akind} account"
        if "TRS" in kinds and inst.kind not in EQUITY_KINDS:
            return f"NOT AVAILABLE / a TRS needs an equity or ETF underlying, not {inst.kind}"
    if "OPTION" in kinds and not inst.cboe_symbol:
        return f"NOT AVAILABLE / no listed options for {inst.symbol} (no Cboe symbol)"
    return None


def availability(services, strategy: dict, acct=None, under=None, other=None) -> Tuple[bool, Optional[str]]:
    """``(available, reason)`` for one template in one account (``acct`` an accounts row; None = no account context).  TRS legs
    need a PB account of an eligible lifetime and the swaps module; futures legs need the front month listed; stock / option
    legs need an account that trades listed products and, when the underlying is known, a tradeable name with a chain."""
    kinds = {l["kind"] for l in strategy["legs"]}
    under_kinds = {l["kind"] for l in strategy["legs"] if not l.get("other")}
    other_kinds = {l["kind"] for l in strategy["legs"] if l.get("other")}
    akind = account_kind_for(acct) if acct is not None else None
    if acct is not None:
        if akind == "TREASURY":
            return False, "NOT AVAILABLE HERE / Treasury never trades: it only funds the accounts"
        if kinds & set(LISTED_KINDS) and akind not in LISTED_ACCOUNT_KINDS:
            return False, f"NOT AVAILABLE HERE / listed products do not trade in a {akind} account"
        if "TRS" in kinds:
            if akind != "PB":
                return False, "NOT ELIGIBLE / not a PB account"
            e = eligibility(services, acct["lifetime_id"])
            if not (e.pb_otc and e.ecp):
                return False, f"NOT ELIGIBLE / {e.pb_otc_reason}"
    if "TRS" in kinds:
        swaps = getattr(services, "swaps", None)
        if swaps is None or not hasattr(swaps, "preview"):
            return False, "NOT AVAILABLE / TRS preview needs the swaps module (WP-17 / WP-26): not in this build"
    if "FUTURE" in kinds:
        root = "NQ" if _nasdaq(under) else "ES"
        if front_month(services, root) is None:
            return False, f"NOT AVAILABLE / no front month listed for {root}"
    if under is not None and under_kinds:
        why = _check_name(services, under, under_kinds, akind or "US_EXCHANGE")
        if why:
            return False, why
    if other is not None and other_kinds:
        why = _check_name(services, other, other_kinds, akind or "US_EXCHANGE")
        if why:
            return False, f"{why} (second name)"
    return True, None


# ===== quotes for the legs ==========================================================================================================
def _chain_for(services, symbol: str):
    """``(snapshot, fresh)`` from ``services.chains`` (fresh = within the cache's max age); a stale snapshot is used with a
    refresh requested; nothing cached -> the fetch is queued and ``ChainPending`` raised."""
    chains = getattr(services, "chains", None)
    if chains is None or not hasattr(chains, "get"):
        raise RejectError("NOT_AVAILABLE", f"no option chain cache is bound for {symbol} (the quotes worker is not running)")
    snap = chains.get(symbol)
    if snap is not None:
        return snap, True
    snap = chains.peek(symbol) if hasattr(chains, "peek") else None
    if hasattr(chains, "request"):
        chains.request(symbol)
    if snap is None:
        raise ChainPending(symbol)
    return snap, False


def _snapshot_quality(services, snap, now: int, group: str = "US_EQUITY") -> str:
    rec = QuoteRecord(snap.underlying_id, "cboe", snap.source, "direct", last=snap.spot, quote_time=snap.quote_time, fetched_at=int(snap.fetched_at),
                      delay_s=snap.delay_s, delay_label=snap.delay_label, session_group=group)
    return _quality(services, rec, now)


def _quality(services, rec: QuoteRecord, now: int) -> str:
    cache = getattr(services, "cache", None)
    if cache is not None and hasattr(cache, "quality_for"):
        return cache.quality_for(rec, now)
    sessions = getattr(services, "sessions", None)
    st = None
    if sessions is not None:
        try:
            st = sessions.state(rec.session_group, now)
        except KeyError:
            st = None
    return freshness(rec, st, now)


def _row_quality(services, snap, row, occ: str, index: bool, now: int) -> str:
    stand_in = SimpleNamespace(instrument_id=occ, currency="USD", session_group="US_INDEX_OPTIONS" if index else "US_EQUITY_OPTIONS")
    return _quality(services, record_from_chain_row(stand_in, snap, row), now)


def _level_of(services, inst, snap) -> Optional[Decimal]:
    if snap is not None and snap.spot is not None and snap.spot > 0:
        return qprice(snap.spot)
    rec = quote_of(services, inst.instrument_id)
    px = rec.price() if rec is not None else None
    return qprice(px) if px is not None and px > 0 else None


def _stock_quote(services, inst, buy: bool, snap, fresh: bool, now: int) -> Tuple[Optional[Decimal], str, dict]:
    """``(price, basis, provenance)``: the chain's underlying ask/bid when a fresh chain is cached, else the record's last."""
    rec = quote_of(services, inst.instrument_id)
    u = snap.underlying if (snap is not None and fresh) else None
    if u is not None and u.bid is not None and u.ask is not None and u.bid > 0 and u.ask >= u.bid:
        px = u.ask if buy else u.bid
        return qprice(px), "Cboe underlying " + ("ask" if buy else "bid"), {
            "last": _s(u.last), "bid": _s(u.bid), "ask": _s(u.ask), "bid_size": u.bid_size, "ask_size": u.ask_size, "quality": _snapshot_quality(services, snap, now),
            "source": snap.source, "as_of": snap.quote_time, "delay_label": snap.delay_label, "basis": "Cboe underlying NBBO"}
    if rec is not None and rec.last is not None and rec.last > 0:
        return qprice(rec.last), "last", {"last": _s(rec.last), "bid": _s(rec.bid), "ask": _s(rec.ask), "bid_size": rec.bid_size, "ask_size": rec.ask_size,
                                            "quality": rec.quality, "source": rec.source, "as_of": rec.quote_time, "delay_label": rec.delay_label,
                                            "basis": "last observed trade (no bid/ask in the free feed)"}
    return None, "", {"quality": "unavailable", "note": f"no quote for {inst.instrument_id}"}


def _greek(sign: Decimal, contracts: int, g) -> Optional[Decimal]:
    if g is None:
        return None
    return _q2(sign * D(contracts) * HUNDRED * Decimal(repr(float(g))))


# ===== the package ==================================================================================================================
def package_legs(services, account_id: str, key: str, params: Optional[dict] = None) -> PackageSpec:
    """Resolve one template for one account against today's cached quotes (§13.16).  ``params``: ``underlying`` (default SPY),
    ``other`` (second name, default QQQ), ``units`` (default 1000), ``tenor_months`` (default 3), ``strikes`` ({leg index: override}).
    Raises ``RejectError`` UNKNOWN_STRATEGY / ACCOUNT_UNKNOWN / INSTRUMENT_UNKNOWN / UNITS / TENOR / STRIKES / NOT_AVAILABLE and
    ``ChainPending`` (409 CHAIN_PENDING) while the chain is being fetched.  Never fetches, never writes."""
    params = dict(params or {})
    strat = BY_KEY.get(str(key or "").strip().upper())
    if strat is None:
        raise RejectError("UNKNOWN_STRATEGY", f"unknown strategy {key!r}", {"known": sorted(BY_KEY)})
    with services.db.reader() as conn:
        row = account_row(conn, str(account_id or ""))
        acct = dict(row) if row is not None else None
    if acct is None:
        raise RejectError("ACCOUNT_UNKNOWN", f"account {account_id!r} does not exist")
    units = _int_param(params.get("units", DEFAULT_UNITS), "UNITS", 1)
    tenor = _int_param(params.get("tenor_months", DEFAULT_TENOR_MONTHS), "TENOR", 1)
    overrides = params.get("strikes") or {}
    if not isinstance(overrides, dict):
        raise RejectError("STRIKES", "strikes must be a map of leg index -> override (a percent when |value| < 60, else a strike)")
    under_id = str(params.get("underlying") or DEFAULT_UNDERLYING).strip().upper()
    needs_other = any(l.get("other") for l in strat["legs"])
    other_id = str(params.get("other") or DEFAULT_OTHER).strip().upper() if needs_other else None
    under = services.instruments.get(under_id)
    if under is None:
        raise RejectError("INSTRUMENT_UNKNOWN", f"instrument {under_id!r} is not in the master (resolve it first)")
    other = None
    if needs_other:
        other = services.instruments.get(other_id)
        if other is None:
            raise RejectError("INSTRUMENT_UNKNOWN", f"second name {other_id!r} is not in the master (resolve it first)")
    ok, reason = availability(services, strat, acct, under, other)
    if not ok:
        raise RejectError("NOT_AVAILABLE", f"{strat['key']}: {reason}", {"reason": reason, "strategy_key": strat["key"], "account_id": account_id})
    now = int(services.clock.epoch())
    today = services.clock.today()
    names = {under_id: under}
    if other is not None:
        names[other_id] = other
    warnings: List[str] = []
    snaps: Dict[str, Tuple[Any, bool]] = {}
    for sym in sorted({(other_id if l.get("other") else under_id) for l in strat["legs"] if l["kind"] == "OPTION"}):
        snaps[sym] = _chain_for(services, sym)
    levels: Dict[str, Decimal] = {}
    for sym, inst in names.items():
        lvl = _level_of(services, inst, snaps.get(sym, (None, False))[0])
        if lvl is None:
            raise RejectError("NOT_AVAILABLE", f"{strat['key']}: no price for {sym} (quote unavailable)", {"reason": f"NOT AVAILABLE / no price for {sym}"})
        levels[sym] = lvl
    spot = levels[under_id]
    fut = None
    fut_rec = None
    if any(l["kind"] == "FUTURE" for l in strat["legs"]):
        fut = front_month(services, "NQ" if _nasdaq(under) else "ES")
        fut_rec = quote_of(services, fut.instrument_id) if fut is not None else None
        if fut is None or fut_rec is None or fut_rec.last is None or fut_rec.last <= 0:
            raise RejectError("NOT_AVAILABLE", f"{strat['key']}: no futures quote for {fut.instrument_id if fut else 'the front month'}",
                              {"reason": "NOT AVAILABLE / no futures quote"})
    sized = size_legs(strat, units, spot, None if fut_rec is None else fut_rec.last, None if fut is None else fut.multiplier)
    legs: List[PackageLeg] = []
    missing: List[str] = []
    qualities: List[str] = []
    expiries: List[str] = []
    tenors_used = set()
    put_ask_for_zero_cost: Optional[Decimal] = None
    for spec, rule_leg in zip(sized, strat["legs"]):
        sym = other_id if spec.other else under_id
        inst = names[sym]
        buy = spec.side == "BUY"
        sign = ONE if buy else -ONE
        if spec.kind == "STOCK":
            snap, fresh = snaps.get(sym, (None, False))
            px, basis, prov = _stock_quote(services, inst, buy, snap, fresh, now)
            if px is None:
                raise RejectError("NOT_AVAILABLE", f"{strat['key']}: no quote for {sym}", {"reason": f"NOT AVAILABLE / no quote for {sym}"})
            gross = mul_c(spec.qty, px, inst.multiplier, inst.price_scale)
            fee_c, fee_basis = fee_for(services, acct, inst, spec.qty)
            what = f"{'buy' if buy else 'short'} {spec.qty:,} {sym} at {px}" + ("" if buy else " (locate and borrow first)")
            legs.append(PackageLeg(spec.index, "STOCK", sym, inst.instrument_id, order_side("STOCK", spec.side), spec.side, spec.qty, px, basis,
                                   -gross if buy else gross, fee_c, fee_basis, inst.multiplier, delta_shares=D(sign * spec.qty), quote=prov,
                                   other=spec.other, what=what))
            qualities.append(prov.get("quality", "unavailable"))
        elif spec.kind == "OPTION":
            snap, fresh = snaps[sym]
            leg_tenor = int(spec.tenor) if spec.tenor else tenor
            tenors_used.add(leg_tenor)
            exp = expiry_for_tenor(snap.expiries(), today, leg_tenor)
            if exp is None:
                raise RejectError("NOT_AVAILABLE", f"{strat['key']}: no listed expiry on or after {add_months(today, leg_tenor)} for {sym} ({leg_tenor} months)",
                                  {"reason": "NOT AVAILABLE / no listed expiry >= tenor"})
            exp_s = exp.isoformat()
            rows = snap.rows_for(exp_s)
            typed = [r for r in rows if r.right == spec.type]
            strikes = sorted({r.strike for r in typed})
            if not strikes:
                raise RejectError("NOT_AVAILABLE", f"{strat['key']}: no {('calls' if spec.type == 'C' else 'puts')} listed for {sym} {exp_s}",
                                  {"reason": "NOT AVAILABLE / no strikes listed"})
            level = levels[sym]
            ov = overrides.get(str(spec.index), overrides.get(spec.index))
            if ov not in (None, ""):
                try:
                    k = resolve_strike(ov, level, strikes)
                except ValueError as e:
                    raise RejectError("STRIKES", f"leg {spec.index}: {e}") from e
            elif spec.strike_rule == "zero_cost":
                k = zero_cost_call(rows, put_ask_for_zero_cost if put_ask_for_zero_cost is not None else D(0), level)
                if k is None or k not in strikes:
                    k = resolve_strike("pct:+10", level, strikes)
                    warnings.append(f"leg {spec.index}: no call above spot with a bid near the put's ask; pct:+10 used instead")
            else:
                k = resolve_strike(spec.strike_rule, level, strikes)
            row = next((r for r in typed if r.strike == k), None)
            if row is None:
                raise RejectError("NOT_AVAILABLE", f"{strat['key']}: strike {k} vanished from the chain", {"reason": "NOT AVAILABLE / chain changed"})
            px = row.ask if buy else row.bid
            if px is None or px <= 0 or (row.ask is not None and row.bid is not None and row.ask < row.bid):
                raise RejectError("NOT_AVAILABLE", f"{strat['key']}: no {'ask' if buy else 'bid'} for {row.occ} (Cboe shows no usable quote)",
                                  {"reason": f"NOT AVAILABLE / no {'ask' if buy else 'bid'} for {row.occ}"})
            px = qprice(px)
            if buy and spec.type == "P":
                put_ask_for_zero_cost = px
            index = inst.kind == "INDEX" or str((inst.spec or {}).get("root") or "") in INDEX_OPTION_ROOTS or inst.symbol in INDEX_OPTION_ROOTS
            occ = row.occ
            opt_inst = services.instruments.get(occ)
            if opt_inst is None:
                missing.append(occ)
            mult = opt_inst.multiplier if opt_inst is not None else D(OPTION_MULT)
            gross = mul_c(spec.qty, px, mult)
            fee_c, fee_basis = fee_for(services, acct, opt_inst if opt_inst is not None else SimpleNamespace(kind="OPTION"), spec.qty)
            quality = _row_quality(services, snap, row, occ, index, now)
            qualities.append(quality)
            pct = _q2((k / level - ONE) * HUNDRED)
            what = (f"{'buy' if buy else 'sell'} {spec.qty} {sym} {exp_s} {k} {'call' if spec.type == 'C' else 'put'} at {px} ({'+' if pct >= 0 else ''}{pct} % from spot)")
            legs.append(PackageLeg(spec.index, "OPTION", sym, occ, order_side("OPTION", spec.side), spec.side, spec.qty, px, "Cboe " + ("ask" if buy else "bid"),
                                   -gross if buy else gross, fee_c, fee_basis, mult, strike=k, expiry=exp_s, right=spec.type, tenor_months=leg_tenor,
                                   pct_from_spot=pct, delta_shares=_greek(sign, spec.qty, row.delta), vega=_greek(sign, spec.qty, row.vega),
                                   theta=_greek(sign, spec.qty, row.theta), iv=row.iv,
                                   quote={"bid": _s(row.bid), "ask": _s(row.ask), "bid_size": row.bid_size, "ask_size": row.ask_size, "mid": _s(row.mid),
                                          "quality": quality, "source": f"{snap.source} ({snap.cboe_symbol})", "as_of": snap.quote_time, "fetched_at": snap.fetched_at,
                                          "delay_label": snap.delay_label, "basis": "Cboe " + ("ask" if buy else "bid"), "chain_fresh": fresh},
                                   materialised=opt_inst is not None, other=spec.other, what=what))
            expiries.append(exp_s)
        elif spec.kind == "FUTURE":
            f_px = qprice(fut_rec.last)
            n = spec.qty
            notional = mul_c(n, f_px, fut.multiplier)
            im_c = int((fut.spec or {}).get("im_c") or 0) * n
            fee_c, fee_basis = fee_for(services, acct, fut, n)
            delta = _q2(sign * D(n) * fut.multiplier * f_px / spot)
            what = f"{'buy' if buy else 'sell'} {n} {fut.instrument_id} ({fut.multiplier} × {f_px} = {from_c(notional):,} notional, margin instead of cash)"
            legs.append(PackageLeg(spec.index, "FUTURE", fut.instrument_id, fut.instrument_id, order_side("FUTURE", spec.side), spec.side, n, f_px, "last", 0,
                                   fee_c, fee_basis, fut.multiplier, expiry=fut.expiry, delta_shares=delta, notional_c=notional, margin_c=im_c,
                                   quote={"last": _s(fut_rec.last), "quality": fut_rec.quality, "source": fut_rec.source, "as_of": fut_rec.quote_time,
                                          "delay_label": fut_rec.delay_label, "basis": "last observed trade"},
                                   what=what))
            qualities.append(fut_rec.quality)
        elif spec.kind == "TRS":
            level = levels[sym]
            notional = mul_c(spec.qty, level)
            terms = {"underlying_id": sym, "units": spec.qty, "receiver": buy, "tenor_months": tenor, "reset_months": 1}
            otc = services.swaps.preview(services, account_id, "TRS", terms)
            otc = otc if isinstance(otc, dict) else (otc.to_dict() if hasattr(otc, "to_dict") else dict(getattr(otc, "__dict__", {})))
            what = f"{'receive' if buy else 'pay'} total return on {spec.qty:,} {sym} (notional {from_c(notional):,}) at the PB's dealt spread; booked through the OTC desk"
            legs.append(PackageLeg(spec.index, "TRS", sym, None, order_side("TRS", spec.side), spec.side, spec.qty, level, "PB dealt spread (otc.preview)", 0, 0,
                                   tables.FEES["DEFAULT"]["SWAP"]["basis"], ONE, delta_shares=D(sign * spec.qty), notional_c=notional, booking="otc.book",
                                   quote={"last": str(level), "basis": "underlying level (reset price)"}, what=what, otc_preview=otc))
            warnings.append(f"leg {spec.index}: a TRS is booked through the OTC desk (otc.book), not by order.submit")
    chain_quality = max(qualities, key=lambda q: QUALITY_RANK.get(q, 9)) if qualities else None
    for sym, (snap, fresh) in snaps.items():
        if not fresh:
            warnings.append(f"the {sym} chain is {now - int(snap.fetched_at)} s old (stale); a refresh is in flight")
    net_cash = sum(l.cash_c for l in legs)
    fees = sum(l.fee_c for l in legs)
    premium = sum(l.cash_c for l in legs if l.kind == "OPTION")
    deltas = [l.delta_shares for l in legs if l.delta_shares is not None]
    delta = _q2(sum(deltas)) if deltas else None
    vegas = [l.vega for l in legs if l.kind == "OPTION" and l.vega is not None]
    thetas = [l.theta for l in legs if l.kind == "OPTION" and l.theta is not None]
    index_under = under.kind == "INDEX" or under.symbol in INDEX_OPTION_ROOTS
    margin = _margin_block(services, account_id, legs, spot, index_under)
    totals = {"net_cash": str(from_c(net_cash)), "net_cash_c": net_cash, "fees": str(from_c(fees)), "fees_c": fees,
              "net_cash_after_fees": str(from_c(net_cash - fees)), "net_cash_after_fees_c": net_cash - fees,
              "net_option_premium": str(from_c(premium)), "net_option_premium_c": premium, "delta_shares": _s(delta),
              "dollar_delta": None if delta is None else str(from_c(to_c(delta * spot))), "vega": _s(_q2(sum(vegas))) if vegas else None,
              "theta": _s(_q2(sum(thetas))) if thetas else None, "futures_margin": str(from_c(sum(l.margin_c or 0 for l in legs if l.kind == "FUTURE"))),
              "futures_margin_c": sum(l.margin_c or 0 for l in legs if l.kind == "FUTURE"), "option_margin": margin.get("requirement"),
              "option_margin_c": margin.get("requirement_c"), "spot": str(spot), "units": units, "tenor_months": tenor, "legs": len(legs),
              "greeks_note": "delta / vega / theta from the Cboe chain's greeks × contracts × 100, signed; stock and swap legs ±shares; futures notional / spot"}
    near = min(expiries) if expiries else None
    payoff = payoff_grid(legs, spot, levels, under_id, mixed_expiries=len(tenors_used) > 1)
    fill_mode = fill_mode_for(strat)
    primary = next((l.index for l in legs if l.kind in ("STOCK", "FUTURE")), None)
    return PackageSpec(strat["key"], strat["title"], strat["group"], account_id, under_id, other_id, units, tenor, spot, legs, fill_mode, primary, near, totals,
                       margin, payoff, chain_quality, now, warnings, missing, {k: tables.CONVENTIONS[k] for k in CONVENTION_KEYS if k in tables.CONVENTIONS})


def _margin_block(services, account_id: str, legs: Sequence[PackageLeg], spot: Decimal, index: bool) -> dict:
    option_legs = [{"index": l.index, "instrument_id": l.instrument_id, "side": l.template_side, "right": l.right, "strike": l.strike, "expiry": l.expiry,
                    "qty": l.qty, "est_price": l.est_price, "mark": _dec(l.quote.get("mid")) if l.quote.get("mid") else l.est_price}
                   for l in legs if l.kind == "OPTION" and l.strike is not None and l.est_price is not None]
    long_shares = sum(l.qty for l in legs if l.kind == "STOCK" and l.template_side == "BUY")
    if not option_legs:
        return {"requirement_c": 0, "requirement": "0.00", "method": "none (no option legs)", "detail": [], "incremental_c": None, "incremental": None,
                "formula": tables.CONVENTIONS["OPT_MARGIN"]}
    req, detail = package_margin_c(option_legs, spot, index=index, long_shares=long_shares)
    out = {"requirement_c": req, "requirement": str(from_c(req)), "method": "§13.3 formula on the package's own legs (account positions not netted)",
           "detail": detail, "incremental_c": None, "incremental": None, "formula": tables.CONVENTIONS["OPT_MARGIN"]}
    options = getattr(services, "options", None)
    if options is not None and hasattr(options, "margin_requirement_c"):
        inc, inc_detail = _incremental_margin(services, account_id, legs, options)
        out.update({"incremental_c": inc, "incremental": str(from_c(inc)), "incremental_detail": inc_detail,
                    "method": "incremental: options.margin_requirement_c with the package's legs added to the account's positions (WP-03)"})
    return out


def _incremental_margin(services, account_id: str, legs: Sequence[PackageLeg], options) -> Tuple[int, Any]:
    """``after − before`` through WP-03's ``margin_requirement_c(positions, marks)`` with the package's option legs as synthetic
    positions (signed qty, mark = the leg's estimated price).  Not guarded: a failure in the margin module surfaces."""
    from .accounting import PositionView
    from .quotes import MarkView
    marks_src = getattr(services, "marks", None)
    marks = marks_src.snapshot() if marks_src is not None and hasattr(marks_src, "snapshot") else marks_src
    positions = list(services.accounting.positions(account_id, snapshot=marks))
    before, _ = options.margin_requirement_c(positions, marks)
    synthetic = []
    for l in legs:
        if l.kind != "OPTION" or l.instrument_id is None or l.est_price is None:
            continue
        qty = l.qty if l.template_side == "BUY" else -l.qty
        mv = mul_c(qty, l.est_price, l.multiplier)
        mark = MarkView(l.instrument_id, l.est_price, l.quote.get("quality", "delayed"), str(l.quote.get("source") or ""), "chain", l.quote.get("as_of"),
                        l.quote.get("fetched_at"), str(l.quote.get("delay_label") or ""), None, False, "package leg (preview)")
        synthetic.append(PositionView(account_id=account_id, instrument_id=l.instrument_id, symbol=l.instrument_id, name=l.what, kind="OPTION", klass="OPTION",
                                      currency="USD", qty=qty, cost_c=abs(l.cash_c), avg_px=l.est_price, mark=mark, mv_c=mv, mv_base_c=mv, notional_c=mv,
                                      unrealised_c=0, unrealised_base_c=0, realised_itd_c=0, income_itd_c=0, financing_itd_c=0, fees_itd_c=0, income_pending_c=0,
                                      day_pnl_c=None, weight=None, days_held=None, settle_px=None, borrow=None, lots=[], extra={"package_leg": True}))
    after, detail = options.margin_requirement_c(positions + synthetic, marks)
    return max(0, int(after) - int(before)), detail


# ===== materialising option rows (light command) ===============================================================================
def _ensure_rid(payload: dict, today: str) -> str:
    h = hashlib.sha256((payload["underlying_id"] + "|" + "|".join(sorted(payload["occs"]))).encode("utf-8")).hexdigest()[:40]
    return f"strat:ensure:{today}:{h}"


def ensure_instruments(services, spec: PackageSpec) -> List[str]:
    """Materialise the option rows a resolved package needs (OCC ids from the cached chain) through the light command
    ``strategies.ensure_instruments`` — from the API / preview path only (never from inside a handler: see
    ``ensure_instruments_tx``).  Returns the ids created; raises when the command fails."""
    missing = [l for l in spec.legs if l.kind == "OPTION" and l.instrument_id and services.instruments.get(l.instrument_id) is None]
    if not missing:
        return []
    bus = getattr(services, "bus", None)
    if bus is None:
        raise RuntimeError("ensure_instruments needs services.bus")
    created: List[str] = []
    today = services.clock.today().isoformat()
    for sym in sorted({l.symbol for l in missing}):
        payload = {"underlying_id": sym, "occs": sorted({l.instrument_id for l in missing if l.symbol == sym})}
        res = bus.execute(Command("strategies.ensure_instruments", payload, _ensure_rid(payload, today), "ui"))
        if not res.ok:
            raise RuntimeError(f"strategies.ensure_instruments failed: {res.code} {res.message}")
        created.extend(res.body.get("created") or [])
    for l in missing:
        l.materialised = services.instruments.get(l.instrument_id) is not None
    spec.missing_instruments = [l.instrument_id for l in spec.legs if l.kind == "OPTION" and not l.materialised]
    return created


def ensure_instruments_tx(ctx, services, spec: PackageSpec) -> List[str]:
    """The same inside an open command (``order.submit`` with a package): rows are inserted in the caller's transaction."""
    created: List[str] = []
    for l in spec.legs:
        if l.kind == "OPTION" and l.instrument_id and services.instruments.get(l.instrument_id, ctx.conn) is None:
            created.extend(_materialise(ctx, services, l.symbol, [l.instrument_id]))
            l.materialised = True
    spec.missing_instruments = []
    return created


def _materialise(ctx, services, underlying_id: str, occs: Sequence[str]) -> List[str]:
    under = services.instruments.get(underlying_id, ctx.conn)
    if under is None:
        raise RejectError("INSTRUMENT_UNKNOWN", f"underlying {underlying_id!r} is not in the master")
    chains = getattr(services, "chains", None)
    snap = chains.peek(underlying_id) if chains is not None and hasattr(chains, "peek") else None
    created: List[str] = []
    for occ in occs:
        if services.instruments.get(occ, ctx.conn) is not None:
            continue
        row = snap.by_occ(occ) if snap is not None else None
        services.instruments.ensure_option(ctx.tx, occ, under, row if row is not None else {"option": occ})
        created.append(occ)
    return created


def h_ensure_instruments(ctx, payload: dict) -> dict:
    """Light: ``{underlying_id, occs: [...]}`` -> OPTION rows inserted on first sight (idempotent; INSERT OR IGNORE)."""
    occs = [str(o) for o in (payload.get("occs") or [])]
    created = _materialise(ctx, ctx.services, str(payload.get("underlying_id") or ""), occs)
    return {"created": created, "requested": occs}


def register(bus) -> None:
    bus.register("strategies.ensure_instruments", h_ensure_instruments, light=True)


# ===== API view functions (WP-11 routes GET /api/strategies and POST /api/strategies/preview here) =========================================
def catalogue_view(services=None, account_id: Optional[str] = None, instrument_id: Optional[str] = None) -> dict:
    """``{groups: [[code, label] …], strategies: [{key, group, group_label, title, simple, thesis, best_when, risk, notes?, legs,
    fill_mode, available, reason}]}``; ``available`` / ``reason`` are per account (and per underlying when given) without touching
    the chain.  Unknown account -> RejectError ACCOUNT_UNKNOWN; unknown instrument -> INSTRUMENT_UNKNOWN."""
    acct = under = None
    if services is not None and account_id:
        with services.db.reader() as conn:
            row = account_row(conn, str(account_id))
        if row is None:
            raise RejectError("ACCOUNT_UNKNOWN", f"account {account_id!r} does not exist")
        acct = dict(row)
    if services is not None and instrument_id:
        under = services.instruments.get(str(instrument_id).strip().upper())
        if under is None:
            raise RejectError("INSTRUMENT_UNKNOWN", f"instrument {instrument_id!r} is not in the master (resolve it first)")
    out = []
    for s in STRATEGIES:
        if services is not None:
            ok, reason = availability(services, s, acct, under, None)
        else:
            ok, reason = True, None
        row = {k: v for k, v in s.items()}
        row["legs"] = [dict(l) for l in s["legs"]]
        row.update({"group_label": GROUP_LABELS.get(s["group"], s["group"]), "fill_mode": fill_mode_for(s), "available": ok, "reason": reason})
        out.append(row)
    return {"groups": [[code, label] for code, label in GROUPS], "strategies": out, "count": len(out), "account_id": account_id, "instrument_id": instrument_id,
            "defaults": {"units": DEFAULT_UNITS, "tenor_months": DEFAULT_TENOR_MONTHS, "underlying": DEFAULT_UNDERLYING, "other": DEFAULT_OTHER},
            "conventions": {k: tables.CONVENTIONS[k] for k in CONVENTION_KEYS if k in tables.CONVENTIONS}}


def preview_view(services, body: dict) -> Tuple[int, dict]:
    """``POST /api/strategies/preview`` body ``{account_id, key, params: {underlying, other?, units, tenor_months, strikes?}}`` ->
    ``(200, PackageSpec.to_dict())``, or ``(202, {status: "fetching", underlying, retry_ms})`` while the chain loads.  The option
    rows the package needs are materialised (light command) so a following ``order.submit`` finds them.  Validation errors
    propagate as ``RejectError`` for the API layer's mapping."""
    body = dict(body or {})
    account_id = str(body.get("account_id") or "")
    key = str(body.get("key") or body.get("strategy_key") or "")
    params = body.get("params") if isinstance(body.get("params"), dict) else {k: v for k, v in body.items() if k not in ("account_id", "key", "strategy_key")}
    try:
        spec = package_legs(services, account_id, key, params)
    except ChainPending as e:
        return 202, {"status": "fetching", "underlying": e.underlying_id, "retry_ms": FETCH_RETRY_MS, "message": e.message}
    if spec.missing_instruments and getattr(services, "bus", None) is not None:
        ensure_instruments(services, spec)
    return 200, spec.to_dict()


__all__ = ["GROUPS", "STRATEGIES", "BY_KEY", "GROUP_LABELS", "LegSpec", "PackageLeg", "PackageSpec", "ChainPending", "parse_rule", "resolve_strike",
           "zero_cost_call", "add_months", "expiry_for_tenor", "size_legs", "fill_mode_for", "order_side", "payoff_grid", "package_margin_c", "availability",
           "front_month", "package_legs", "ensure_instruments", "ensure_instruments_tx", "h_ensure_instruments", "register", "catalogue_view", "preview_view"]
