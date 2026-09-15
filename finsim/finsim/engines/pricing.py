"""Pricing engine.

Every instrument type implements the same small interface so new instruments
plug in without touching the rest of the system:

    price()          -> mark (clean for bonds)
    market_value()   -> Decimal, in instrument currency
    accrued()        -> accrued interest per unit (bonds)
    cash_flows()     -> list of (date, amount per 100 face)
    risk_metrics()   -> dict of analytics (duration, DV01, beta ...)
    next_events()    -> upcoming coupon / dividend dates

Bond maths here is closed-form/numeric pure Python; a QuantLib-backed pricer
can replace `BondPricer` behind the same interface later.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Dict, List, Optional, Tuple

from ..domain.models import Security, YieldCurve
from ..money import D, money, price as qprice, ZERO


def interp_rate(curve: YieldCurve, t: float) -> float:
    ten, rates = curve.tenors, curve.rates
    if t <= ten[0]:
        return rates[0]
    if t >= ten[-1]:
        return rates[-1]
    for i in range(1, len(ten)):
        if t <= ten[i]:
            w = (t - ten[i - 1]) / (ten[i] - ten[i - 1])
            return rates[i - 1] + w * (rates[i] - rates[i - 1])
    return rates[-1]


def _add_months(d: date, months: int) -> date:
    y = d.year + (d.month - 1 + months) // 12
    m = (d.month - 1 + months) % 12 + 1
    day = min(d.day, 28)
    return date(y, m, day)


class BondPricer:
    """Fixed-coupon bullet bond analytics. Prices are per 100 face.

    Day count: Actual/Actual (ICMA-style: actual days in the period)."""

    @staticmethod
    def coupon_dates(sec: Security) -> List[date]:
        mat = date.fromisoformat(sec.maturity)
        issue = date.fromisoformat(sec.issue_date)
        step = 12 // sec.freq
        dates = []
        d = mat
        while d > issue:
            dates.append(d)
            d = _add_months(d, -step)
        return sorted(dates)

    @staticmethod
    def period(sec: Security, asof: date) -> Tuple[date, date]:
        """(previous coupon date or issue, next coupon date) around `asof`."""
        cds = BondPricer.coupon_dates(sec)
        issue = date.fromisoformat(sec.issue_date)
        prev = issue
        for cd in cds:
            if cd > asof:
                return prev, cd
            prev = cd
        return prev, cds[-1]

    @staticmethod
    def accrued_per_100(sec: Security, asof: date) -> Decimal:
        mat = date.fromisoformat(sec.maturity)
        if asof >= mat:
            return ZERO
        prev, nxt = BondPricer.period(sec, asof)
        if asof <= prev:
            return ZERO
        cpn = D(sec.coupon) * 100 / sec.freq
        frac = D((asof - prev).days) / D((nxt - prev).days)
        return (cpn * frac).quantize(Decimal("0.000001"))

    @staticmethod
    def cash_flows(sec: Security, asof: date) -> List[Tuple[date, float]]:
        """Remaining cash flows per 100 face after `asof` (exclusive)."""
        cpn = sec.coupon * 100 / sec.freq
        mat = date.fromisoformat(sec.maturity)
        out = []
        for cd in BondPricer.coupon_dates(sec):
            if cd > asof:
                out.append((cd, cpn + (100.0 if cd == mat else 0.0)))
        return out

    @staticmethod
    def dirty_price_from_yield(sec: Security, asof: date, y: float) -> float:
        f = sec.freq
        total = 0.0
        for cd, cf in BondPricer.cash_flows(sec, asof):
            t = (cd - asof).days / 365.25
            total += cf / (1 + y / f) ** (f * t)
        return total

    @staticmethod
    def clean_price_from_curve(sec: Security, curve: YieldCurve, asof: date) -> Decimal:
        f = sec.freq
        spread = sec.spread_bps_credit / 1e4
        total = 0.0
        for cd, cf in BondPricer.cash_flows(sec, asof):
            t = (cd - asof).days / 365.25
            z = interp_rate(curve, t) + spread
            total += cf / (1 + z / f) ** (f * t)
        clean = D(total) - BondPricer.accrued_per_100(sec, asof)
        return qprice(clean)

    @staticmethod
    def yield_to_maturity(sec: Security, asof: date, clean: float) -> float:
        dirty = clean + float(BondPricer.accrued_per_100(sec, asof))
        y = sec.coupon or 0.04
        for _ in range(60):
            p = BondPricer.dirty_price_from_yield(sec, asof, y)
            dp = (BondPricer.dirty_price_from_yield(sec, asof, y + 1e-5) - p) / 1e-5
            if abs(dp) < 1e-12:
                break
            step = (p - dirty) / dp
            y -= step
            if abs(step) < 1e-10:
                break
        return y

    @staticmethod
    def risk_metrics(sec: Security, asof: date, clean: float, curve: Optional[YieldCurve] = None) -> Dict[str, float]:
        mat = date.fromisoformat(sec.maturity)
        if asof >= mat:
            return {"ytm": 0.0, "macaulay_duration": 0.0, "modified_duration": 0.0, "convexity": 0.0, "dv01_per_100": 0.0,
                    "accrued": 0.0, "dirty_price": clean, "years_to_maturity": 0.0}
        y = BondPricer.yield_to_maturity(sec, asof, clean)
        f = sec.freq
        dirty = clean + float(BondPricer.accrued_per_100(sec, asof))
        mac = 0.0
        for cd, cf in BondPricer.cash_flows(sec, asof):
            t = (cd - asof).days / 365.25
            pv = cf / (1 + y / f) ** (f * t)
            mac += t * pv
        mac /= dirty
        mod = mac / (1 + y / f)
        up = BondPricer.dirty_price_from_yield(sec, asof, y + 1e-4)
        dn = BondPricer.dirty_price_from_yield(sec, asof, y - 1e-4)
        convexity = (up + dn - 2 * dirty) / (dirty * 1e-8)
        dv01 = (dn - up) / 2  # per 100 face, per 1bp
        out = {
            "ytm": y, "macaulay_duration": mac, "modified_duration": mod, "convexity": convexity,
            "dv01_per_100": dv01, "accrued": float(BondPricer.accrued_per_100(sec, asof)), "dirty_price": dirty,
            "years_to_maturity": (mat - asof).days / 365.25,
        }
        if curve is not None:
            t = out["years_to_maturity"]
            bench = interp_rate(curve, t)
            out["benchmark_yield"] = bench
            out["spread_to_curve_bps"] = (y - bench) * 1e4
            out["credit_spread_bps"] = sec.spread_bps_credit
            out["default_probability_1y"] = BondPricer.default_probability(sec)
        return out

    @staticmethod
    def default_probability(sec: Security) -> float:
        """Hazard from spread: PD ≈ spread / (1 - recovery)."""
        if not sec.spread_bps_credit:
            return 0.0
        return min(0.99, (sec.spread_bps_credit / 1e4) / (1 - sec.recovery_rate))


class Instrument:
    """Uniform valuation facade over a security + market data."""

    def __init__(self, sec: Security, mark: Decimal, asof: date, curve: Optional[YieldCurve] = None, beta_hint: Optional[float] = None):
        self.sec = sec
        self.mark = mark
        self.asof = asof
        self.curve = curve

    def price(self) -> Decimal:
        return self.mark

    def accrued(self) -> Decimal:
        return BondPricer.accrued_per_100(self.sec, self.asof) if self.sec.is_bond else ZERO

    def market_value(self, quantity: Decimal, include_accrued: bool = False) -> Decimal:
        if self.sec.is_bond:
            px = self.mark + (self.accrued() if include_accrued else ZERO)
            return money(quantity * px / 100)
        return money(quantity * self.mark)

    def cash_flows(self) -> List[Tuple[str, float]]:
        if self.sec.is_bond:
            return [(d.isoformat(), cf) for d, cf in BondPricer.cash_flows(self.sec, self.asof)]
        return []

    def risk_metrics(self, quantity: Decimal = D(1)) -> Dict[str, float]:
        if self.sec.is_bond:
            m = BondPricer.risk_metrics(self.sec, self.asof, float(self.mark), self.curve)
            m["position_dv01"] = m["dv01_per_100"] * float(quantity) / 100
            return m
        return {"beta": self.sec.beta, "sigma_annual": self.sec.sigma_annual, "delta": 1.0,
                "beta_dollar_exposure": float(self.market_value(quantity)) * self.sec.beta}

    def next_events(self) -> List[Dict]:
        if self.sec.is_bond:
            prev, nxt = BondPricer.period(self.sec, self.asof)
            return [{"type": "COUPON", "date": nxt.isoformat(), "amount_per_100": self.sec.coupon * 100 / self.sec.freq}]
        return []
