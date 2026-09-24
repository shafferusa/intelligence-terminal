"""Valuation and sensitivities for the hedge products (pure functions, no data access).

Options use FinSim's Black-Scholes-Merton (European) and Cox-Ross-Rubinstein tree (American) with a continuous
dividend yield. Futures and forwards are priced at fair value from the underlying, the financing rate and the
carry. None of this invents a market quote: every input is observed data (see `market.py`); where a model stands
in for a quote (Treasury futures, the implied volatility of an index), the product registry says so."""
from __future__ import annotations

import datetime as _dt
import math
from typing import Dict, List, Optional, Sequence, Tuple

from finsim.calendar import us_equity_holidays
from finsim.engines import options_pricing as op

OPTION_MULTIPLIER = 100


# ------------------------------------------------------------------ calendar conventions
def _is_session(d: _dt.date) -> bool:
    return d.weekday() < 5 and d not in us_equity_holidays(d.year)


def _prev_session(d: _dt.date) -> _dt.date:
    while not _is_session(d):
        d -= _dt.timedelta(days=1)
    return d


def third_friday(year: int, month: int) -> _dt.date:
    d = _dt.date(year, month, 1)
    d += _dt.timedelta(days=(4 - d.weekday()) % 7 + 14)
    return _prev_session(d)                        # Good Friday: the Thursday before


def last_business_day(year: int, month: int) -> _dt.date:
    nxt = _dt.date(year + (month == 12), month % 12 + 1, 1)
    return _prev_session(nxt - _dt.timedelta(days=1))


def business_days_before(d: _dt.date, n: int) -> _dt.date:
    while n > 0:
        d -= _dt.timedelta(days=1)
        if _is_session(d):
            n -= 1
    return d


def monthly_option_expiries(asof: str, count: int = 14) -> List[str]:
    """Standard monthly expiries (third Friday) after `asof`."""
    d = _dt.date.fromisoformat(asof)
    out, y, m = [], d.year, d.month
    while len(out) < count:
        e = third_friday(y, m)
        if e > d:
            out.append(e.isoformat())
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


QUARTERLY = (3, 6, 9, 12)
MONTH_CODE = {1: "F", 2: "G", 3: "H", 4: "J", 5: "K", 6: "M", 7: "N", 8: "Q", 9: "U", 10: "V", 11: "X", 12: "Z"}


def future_expiries(kind: str, asof: str, count: int = 3) -> List[Tuple[str, str, str]]:
    """(contract code suffix, last trading day, roll date) for the next listed contracts.
    Equity index: third Friday of Mar/Jun/Sep/Dec, rolled 8 days before. Treasury: last trading day seven business
    days before the end of the contract month, rolled on the last business day of the month before (first notice)."""
    d = _dt.date.fromisoformat(asof)
    out, y, m = [], d.year, d.month
    while len(out) < count:
        if m in QUARTERLY:
            if kind == "treasury":
                ltd = business_days_before(last_business_day(y, m) + _dt.timedelta(days=1), 7)
                py, pm = (y - 1, 12) if m == 1 else (y, m - 1)
                roll = last_business_day(py, pm)
            else:
                ltd = third_friday(y, m)
                roll = business_days_before(ltd, 6)
            if roll > d:
                out.append((f"{MONTH_CODE[m]}{y % 100:02d}", ltd.isoformat(), roll.isoformat()))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def year_frac(a: str, b: str) -> float:
    return max(0.0, (_dt.date.fromisoformat(b) - _dt.date.fromisoformat(a)).days / 365.0)


# ------------------------------------------------------------------ rates
class Curve:
    """A yield curve from constant-maturity Treasury par yields (decimals) by tenor in years, linearly interpolated
    and flat beyond the ends. Par yields are used as zero rates: close enough for sizing and sensitivities, and
    labelled as such."""

    def __init__(self, points: Dict[float, float]):
        self.pts = sorted((float(t), float(y)) for t, y in points.items() if y is not None)
        if not self.pts:
            raise ValueError("no yield curve points")

    def y(self, t: float) -> float:
        p = self.pts
        if t <= p[0][0]:
            return p[0][1]
        for (t0, y0), (t1, y1) in zip(p, p[1:]):
            if t <= t1:
                return y0 + (y1 - y0) * (t - t0) / (t1 - t0)
        return p[-1][1]

    def df(self, t: float) -> float:
        return math.exp(-self.y(t) * t)

    def shifted(self, bp: float = 0.0, by_tenor: Optional[Dict[float, float]] = None) -> "Curve":
        """Parallel shift (bp) or key-rate shifts {tenor: bp} (triangular around each key tenor)."""
        if by_tenor is None:
            return Curve({t: y + bp / 1e4 for t, y in self.pts})
        keys = sorted(by_tenor)
        out = {}
        for t, y in self.pts:
            s = 0.0
            for k, key in enumerate(keys):
                lo = keys[k - 1] if k > 0 else None
                hi = keys[k + 1] if k + 1 < len(keys) else None
                if t == key or (lo is None and t < key) or (hi is None and t > key):
                    w = 1.0
                elif lo is not None and lo < t < key:
                    w = (t - lo) / (key - lo)
                elif hi is not None and key < t < hi:
                    w = (hi - t) / (hi - key)
                else:
                    w = 0.0
                s += w * by_tenor[key]
            out[t] = y + s / 1e4
        return Curve(out)


def bond_price(coupon: float, years: float, yld: float, freq: int = 2) -> float:
    """Clean price per 100 face of a bullet bond at a flat yield (years may be fractional; first period shortened)."""
    if years <= 0:
        return 100.0
    n = max(1, int(math.ceil(years * freq - 1e-9)))
    first = years * freq - (n - 1)                 # fraction of the first period
    c = 100.0 * coupon / freq
    r = yld / freq
    pv = sum(c / (1 + r) ** (first + k) for k in range(n)) + 100.0 / (1 + r) ** (first + n - 1)
    accrued = c * (1 - first)
    return pv - accrued


def bond_dv01(coupon: float, years: float, yld: float) -> float:
    """Price change per 100 face for a one-basis-point FALL in yield (positive for a long bond)."""
    return (bond_price(coupon, years, yld - 0.00005) - bond_price(coupon, years, yld + 0.00005)) / 1.0


def mod_duration(coupon: float, years: float, yld: float) -> float:
    p = bond_price(coupon, years, yld)
    return bond_dv01(coupon, years, yld) / p * 1e4 if p else 0.0


# ------------------------------------------------------------------ futures and forwards
def equity_future(S: float, r: float, q: float, T: float) -> float:
    """Fair value of an equity-index future: S·e^{(r−q)T} (financing minus dividends)."""
    return S * math.exp((r - q) * T)


def treasury_future(curve: Curve, ctd_years: float, T: float, face: float = 100000.0) -> dict:
    """A Treasury future modelled as the forward price of its standard 6% notional coupon at the cheapest-to-deliver
    maturity (a conversion factor of 1): F = P·(1 + r·T) − coupon·T, with r the financing rate at T. Returns the price
    per 100, the DV01 per contract (dollars per 1bp fall in yields at the CTD tenor) and the tenor it loads on."""
    y = curve.y(ctd_years + T)
    r = curve.y(max(T, 1 / 12))
    p = bond_price(0.06, ctd_years + T, y)
    F = p * (1 + r * T) - 6.0 * T
    dv = bond_dv01(0.06, ctd_years + T, y) * (1 + r * T)
    return {"price": F, "dv01": dv * face / 100.0, "yield": y, "ctd_years": ctd_years + T, "carry_per_100": p - F}


def fx_forward(S: float, r_quote: float, r_base: float, T: float) -> float:
    """Covered interest parity for a pair quoted as quote currency per unit of base: S·e^{(r_quote − r_base)T}."""
    return S * math.exp((r_quote - r_base) * T)


# ------------------------------------------------------------------ options
def option(right: str, S: float, K: float, T: float, r: float, q: float, sigma: float, style: str = "EUROPEAN") -> dict:
    """Price and per-unit Greeks: delta, gamma (per $1), vega (per vol point), theta (per calendar day), rho (per bp)."""
    right = right.upper()[0]
    if T <= 0:
        intrinsic = max(0.0, S - K) if right == "C" else max(0.0, K - S)
        itm = intrinsic > 0
        return {"price": intrinsic, "delta": (1.0 if right == "C" else -1.0) if itm else 0.0, "gamma": 0.0, "vega": 0.0, "theta": 0.0,
                "rho": 0.0, "intrinsic": intrinsic, "extrinsic": 0.0, "iv": sigma}
    return op.greeks(right, style, S, K, T, r, q, sigma)


def option_price(right: str, S: float, K: float, T: float, r: float, q: float, sigma: float, style: str = "EUROPEAN") -> float:
    right = right.upper()[0]
    if T <= 0:
        return max(0.0, S - K) if right == "C" else max(0.0, K - S)
    return op.price(right, style, S, K, T, r, q, sigma)


def strike_for(S: float, moneyness: float, increment: float) -> float:
    """Listed strike nearest S × moneyness (0.95 = 5% out of the money for a put)."""
    return max(increment, round(S * moneyness / increment) * increment)


def strike_increment(S: float, index: bool = False) -> float:
    if index:
        return 5.0 if S >= 500 else 1.0
    return 0.5 if S < 25 else 1.0 if S < 200 else 5.0 if S < 1000 else 10.0


def solve_contracts(target: float, per_unit: float, integer: bool = True) -> Optional[float]:
    """Units needed so that units × per_unit = target (sign kept); rounded to whole contracts when required."""
    if not per_unit:
        return None
    q = target / per_unit
    return float(round(q)) if integer else q


def interp_var_time(t: float, points: Sequence[Tuple[float, float]]) -> Optional[float]:
    """Implied volatility at maturity t (years) from (maturity, vol) points, interpolated in total variance (σ²t)
    and flat beyond the ends."""
    pts = sorted((a, b) for a, b in points if b is not None and a > 0)
    if not pts:
        return None
    if t <= pts[0][0]:
        return pts[0][1]
    for (t0, v0), (t1, v1) in zip(pts, pts[1:]):
        if t <= t1:
            w = (t - t0) / (t1 - t0)
            var = (1 - w) * v0 * v0 * t0 + w * v1 * v1 * t1
            return math.sqrt(max(1e-8, var / t))
    return pts[-1][1]
