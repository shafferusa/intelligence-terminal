"""OTC derivative pricing: pure functions over the simulated curves.

Rates
  discount factors from the zero curve (semiannual compounding, as the bond
  pricer), simple forward rates, schedules with 30/360 (fixed) and ACT/360
  (floating) day counts, interest-rate swap PV / par rate / DV01, FRA PV,
  Black-76 caplets/floorlets and swaptions on lognormal rate vol.
Credit
  flat-hazard CDS: premium leg with accrual, protection leg with recovery,
  par spread, hazard implied from a market spread (h = s / (1 - R)).
Equity / commodity total return
  reset-to-reset total return less financing; commodity fixed-vs-floating on
  the front of the futures curve.

Everything is deterministic and side-effect free; the OTC engine feeds it
market data and books the results.
"""
from __future__ import annotations

import math
from datetime import date
from typing import Callable, Dict, List, Optional, Tuple

from .options_pricing import norm_cdf, norm_pdf
from .pricing import _add_months, interp_rate


# ------------------------------------------------------------------ curve utilities
def df(curve, t: float, shift_bp: float = 0.0) -> float:
    """Discount factor for t years from a zero curve of semiannually compounded rates (optionally shifted)."""
    if t <= 0:
        return 1.0
    z = interp_rate(curve, t) + shift_bp / 1e4
    return (1 + z / 2) ** (-2 * t)


def fwd_rate(curve, t1: float, t2: float, shift_bp: float = 0.0) -> float:
    """Simple (money-market) forward rate between t1 and t2 (years, ACT/365), quoted on the ACT/360 basis the floating
    legs accrue on: r × τ(ACT/360) = df(t1)/df(t2) − 1, so a floating leg valued off this curve prices to par."""
    if t2 <= t1:
        return interp_rate(curve, max(t1, 0.02)) + shift_bp / 1e4
    d1, d2 = df(curve, t1, shift_bp), df(curve, t2, shift_bp)
    return (d1 / d2 - 1) / ((t2 - t1) * 365.0 / 360.0)


def yearfrac(d1: date, d2: date, basis: str) -> float:
    if d2 <= d1:
        return 0.0
    if basis == "30/360":
        dd1, dd2 = min(d1.day, 30), min(d2.day, 30) if d1.day >= 30 or d2.day != 31 else 30
        return ((d2.year - d1.year) * 360 + (d2.month - d1.month) * 30 + (dd2 - dd1)) / 360.0
    if basis == "ACT/360":
        return (d2 - d1).days / 360.0
    return (d2 - d1).days / 365.0


def schedule(start: date, end: date, months: int, roll: Callable[[date], date]) -> List[Tuple[date, date, date]]:
    """(accrual start, accrual end, payment date) periods from start to end, stepping `months`, rolled to business days."""
    if months < 1:
        raise ValueError("period length must be at least one month")
    out = []
    i = 0
    cur = start
    while cur < end:
        nxt = min(end, _add_months(start, months * (i + 1)))
        if nxt < end and roll(nxt) >= end:      # the rolled maturity is this period's payment date: no sliver stub after it
            nxt = end
        out.append((cur, nxt, roll(nxt)))
        cur = nxt
        i += 1
        if i > 1200:
            break
    return out


def years(asof: date, d: date) -> float:
    return max(0.0, (d - asof).days / 365.0)


# ------------------------------------------------------------------ interest-rate swaps
def swap_legs(curve, asof: date, notional: float, fixed_rate: float, fixed_periods, float_periods, fixings: Dict[str, float],
              shift_bp: float = 0.0, spread: float = 0.0) -> Dict[str, float]:
    """PV of the fixed and floating legs (positive numbers, receiver's view). Floating periods already fixed use their fixing."""
    pv_fixed = annuity = 0.0
    for (s, e, p) in fixed_periods:
        if p <= asof:
            continue
        tau = yearfrac(s, e, "30/360")
        d = df(curve, years(asof, p), shift_bp)
        annuity += tau * d
        pv_fixed += notional * fixed_rate * tau * d
    pv_float = 0.0
    for (s, e, p) in float_periods:
        if p <= asof:
            continue
        tau = yearfrac(s, e, "ACT/360")
        d = df(curve, years(asof, p), shift_bp)
        key = s.isoformat()
        if key in fixings:
            r = fixings[key]
        else:
            r = fwd_rate(curve, years(asof, s), years(asof, e), shift_bp)
        pv_float += notional * (r + spread) * tau * d
    return {"fixed": pv_fixed, "float": pv_float, "annuity": annuity}


def swap_pv(curve, asof, notional, fixed_rate, fixed_periods, float_periods, fixings, pay_fixed: bool, shift_bp=0.0, spread=0.0) -> float:
    legs = swap_legs(curve, asof, notional, fixed_rate, fixed_periods, float_periods, fixings, shift_bp, spread)
    v = legs["fixed"] - legs["float"]
    return -v if pay_fixed else v


def par_rate(curve, asof, notional, fixed_periods, float_periods, fixings, spread=0.0) -> float:
    legs = swap_legs(curve, asof, notional, 1.0, fixed_periods, float_periods, fixings, 0.0, spread)
    return legs["float"] / (notional * legs["annuity"]) if legs["annuity"] > 0 else 0.0


def swap_dv01(curve, asof, notional, fixed_rate, fixed_periods, float_periods, fixings, pay_fixed: bool) -> float:
    """Change in PV for a +1bp parallel shift of the curve."""
    up = swap_pv(curve, asof, notional, fixed_rate, fixed_periods, float_periods, fixings, pay_fixed, 1.0)
    dn = swap_pv(curve, asof, notional, fixed_rate, fixed_periods, float_periods, fixings, pay_fixed, -1.0)
    return (up - dn) / 2


# ------------------------------------------------------------------ FRA
def fra_pv(curve, asof: date, notional: float, rate: float, start: date, end: date, pay_fixed: bool, fixing: Optional[float] = None) -> float:
    """Forward rate agreement settled (discounted) at the start date."""
    tau = yearfrac(start, end, "ACT/360")
    t1, t2 = years(asof, start), years(asof, end)
    f = fixing if fixing is not None else fwd_rate(curve, t1, t2)
    settle = notional * (f - rate) * tau / (1 + f * tau)      # paid at start to the fixed payer when rates rose
    v = settle * df(curve, t1)
    return v if pay_fixed else -v


# ------------------------------------------------------------------ Black-76
def black76(call: bool, F: float, K: float, T: float, sigma: float, disc: float) -> float:
    if T <= 0 or sigma <= 0:
        return disc * max(0.0, (F - K) if call else (K - F))
    sd = sigma * math.sqrt(T)
    d1 = (math.log(max(F, 1e-9) / max(K, 1e-9)) + 0.5 * sd * sd) / sd
    d2 = d1 - sd
    if call:
        return disc * (F * norm_cdf(d1) - K * norm_cdf(d2))
    return disc * (K * norm_cdf(-d2) - F * norm_cdf(-d1))


def black76_delta(call: bool, F: float, K: float, T: float, sigma: float) -> float:
    if T <= 0 or sigma <= 0:
        return (1.0 if F > K else 0.0) if call else (-1.0 if F < K else 0.0)
    sd = sigma * math.sqrt(T)
    d1 = (math.log(max(F, 1e-9) / max(K, 1e-9)) + 0.5 * sd * sd) / sd
    return norm_cdf(d1) if call else norm_cdf(d1) - 1


def black76_vega(F: float, K: float, T: float, sigma: float, disc: float) -> float:
    if T <= 0 or sigma <= 0:
        return 0.0
    sd = sigma * math.sqrt(T)
    d1 = (math.log(max(F, 1e-9) / max(K, 1e-9)) + 0.5 * sd * sd) / sd
    return disc * F * norm_pdf(d1) * math.sqrt(T) / 100.0     # per 1 vol point


def rate_vol(regime: str, vol_index: float) -> float:
    """Lognormal volatility of forward rates: rises with the equity vol index and in stress."""
    base = {"NORMAL_GROWTH": 0.22, "RATE_CUTTING": 0.26, "RATE_HIKING": 0.30, "RECESSION": 0.38, "LIQUIDITY_STRESS": 0.55}.get(regime, 0.25)
    return max(0.12, min(0.9, base * (0.7 + 0.3 * vol_index / 18.0)))


def cap_pv(curve, asof: date, notional: float, strike: float, periods, sigma: float, is_cap: bool, fixings: Dict[str, float]) -> Dict[str, float]:
    """Sum of caplets/floorlets on simple forward rates. A period that has fixed pays its intrinsic at the period end."""
    pv = delta = vega = 0.0
    for (s, e, p) in periods:
        if p <= asof:
            continue
        tau = yearfrac(s, e, "ACT/360")
        t1, t2 = years(asof, s), years(asof, e)
        disc = df(curve, years(asof, p))
        key = s.isoformat()
        if key in fixings or t1 <= 0:
            f = fixings.get(key, fwd_rate(curve, t1, t2))
            pv += notional * tau * disc * max(0.0, (f - strike) if is_cap else (strike - f))
            continue
        f = fwd_rate(curve, t1, t2)
        pv += notional * tau * black76(is_cap, f, strike, t1, sigma, disc)
        delta += notional * tau * disc * black76_delta(is_cap, f, strike, t1, sigma) * 1e-4     # per 1bp
        vega += notional * tau * black76_vega(f, strike, t1, sigma, disc)
    return {"pv": pv, "dv01": delta, "vega": vega}


def swaption_pv(curve, asof: date, notional: float, strike: float, expiry: date, fixed_periods, float_periods, sigma: float, payer: bool) -> Dict[str, float]:
    """European swaption on the forward par rate, valued with Black-76 on the forward annuity."""
    legs = swap_legs(curve, asof, notional, 1.0, fixed_periods, float_periods, {}, 0.0)
    ann = legs["annuity"]
    if ann <= 0:
        return {"pv": 0.0, "forward_rate": 0.0, "dv01": 0.0, "vega": 0.0, "annuity": 0.0}
    F = legs["float"] / (notional * ann)
    T = years(asof, expiry)
    pv = notional * ann * black76(payer, F, strike, T, sigma, 1.0)
    dv01 = notional * ann * black76_delta(payer, F, strike, T, sigma) * 1e-4
    vega = notional * ann * black76_vega(F, strike, T, sigma, 1.0)
    return {"pv": pv, "forward_rate": F, "dv01": dv01, "vega": vega, "annuity": ann}


# ------------------------------------------------------------------ credit default swaps
def hazard_from_spread(spread_bps: float, recovery: float) -> float:
    return max(1e-6, (spread_bps / 1e4) / max(0.05, 1 - recovery))


def cds_legs(curve, asof: date, notional: float, running_bps: float, hazard: float, recovery: float, periods) -> Dict[str, float]:
    """Premium leg (with accrual on default) and protection leg under a flat hazard rate."""
    s = running_bps / 1e4
    prem = prot = risky_annuity = 0.0
    prev_t = 0.0
    surv_prev = 1.0
    for (a, b, p) in periods:
        if p <= asof:
            continue
        t_end = years(asof, b)
        t_start = max(0.0, years(asof, a))
        tau = yearfrac(max(a, asof), b, "ACT/360")
        surv_end = math.exp(-hazard * t_end)
        disc = df(curve, years(asof, p))
        # premium paid at period end if survival; half accrual if default within the period
        risky_annuity += tau * disc * (surv_end + 0.5 * (surv_prev - surv_end))
        # protection paid mid-period on default
        mid_disc = df(curve, 0.5 * (t_start + t_end))
        prot += (1 - recovery) * (surv_prev - surv_end) * mid_disc
        surv_prev = surv_end
        prev_t = t_end
    prem = notional * s * risky_annuity
    return {"premium": prem, "protection": notional * prot, "risky_annuity": risky_annuity}


def cds_pv(curve, asof, notional, running_bps, hazard, recovery, periods, buyer: bool) -> float:
    legs = cds_legs(curve, asof, notional, running_bps, hazard, recovery, periods)
    v = legs["protection"] - legs["premium"]
    return v if buyer else -v


def cds_par_spread(curve, asof, notional, hazard, recovery, periods) -> float:
    legs = cds_legs(curve, asof, notional, 100.0, hazard, recovery, periods)
    return (legs["protection"] / (notional * legs["risky_annuity"])) * 1e4 if legs["risky_annuity"] > 0 else 0.0


def cds_cs01(curve, asof, notional, running_bps, market_bps, recovery, periods, buyer: bool) -> float:
    """PV change for +1bp in the market spread."""
    up = cds_pv(curve, asof, notional, running_bps, hazard_from_spread(market_bps + 1, recovery), recovery, periods, buyer)
    dn = cds_pv(curve, asof, notional, running_bps, hazard_from_spread(max(0.5, market_bps - 1), recovery), recovery, periods, buyer)
    return (up - dn) / 2


# ------------------------------------------------------------------ total return / commodity swaps
def trs_pv(units: float, price_now: float, price_reset: float, dividends_accrued: float, financing_accrued: float, receiver: bool) -> float:
    """Value since the last reset: price change on the notional units + dividends collected - financing accrued."""
    v = units * (price_now - price_reset) + dividends_accrued - financing_accrued
    return v if receiver else -v


def commodity_swap_pv(curve, asof: date, quantity: float, fixed_price: float, periods, expected_price: Callable[[date], float],
                      realised: Dict[str, float], pay_fixed: bool) -> float:
    """Fixed-for-floating on a commodity: each period settles (average floating - fixed) x quantity at its end."""
    pv = 0.0
    for (s, e, p) in periods:
        if p <= asof:
            continue
        key = s.isoformat()
        f = realised.get(key, expected_price(e))
        pv += quantity * (f - fixed_price) * df(curve, years(asof, p))
    return pv if pay_fixed else -pv


if __name__ == "__main__":     # quick self-checks
    from ..domain.models import YieldCurve
    c = YieldCurve("2026-01-05", [0.25, 0.5, 1, 2, 3, 5, 7, 10, 20, 30], [0.04] * 10)
    asof = date(2026, 1, 5)
    roll = lambda d: d
    fp = schedule(asof, date(2031, 1, 5), 6, roll)
    fl = schedule(asof, date(2031, 1, 5), 3, roll)
    r = par_rate(c, asof, 1e6, fp, fl, {})
    assert abs(swap_pv(c, asof, 1e6, r, fp, fl, {}, True)) < 1e-6
    assert swap_dv01(c, asof, 1e6, r, fp, fl, {}, True) > 0
    print("ok", r)
