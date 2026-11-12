"""Decimal money helpers.

All cash, cost, and P&L amounts in the system are `Decimal` quantised to cents.
Prices are quantised to 4 decimal places. Analytics (yields, durations, Greeks
later) may be floats — they are never posted to the ledger.
"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_EVEN, getcontext
from typing import Union

getcontext().prec = 28

Numeric = Union[int, float, str, Decimal]

CENT = Decimal("0.01")
PRICE_Q = Decimal("0.0001")
QTY_Q = Decimal("1")
ZERO = Decimal("0")


def D(x: Numeric) -> Decimal:
    """Coerce to Decimal without float artefacts."""
    if isinstance(x, Decimal):
        return x
    if isinstance(x, float):
        return Decimal(repr(x))
    return Decimal(str(x))


def money(x: Numeric) -> Decimal:
    """Round to cents (banker's rounding, as most accounting systems do)."""
    return D(x).quantize(CENT, rounding=ROUND_HALF_EVEN)


def price(x: Numeric) -> Decimal:
    return D(x).quantize(PRICE_Q, rounding=ROUND_HALF_EVEN)


def qty(x: Numeric) -> Decimal:
    return D(x).quantize(QTY_Q, rounding=ROUND_HALF_EVEN)


def to_json_number(x):
    """Decimals are serialised as strings so nothing is lost in transit; the UI
    parses them. Floats stay floats."""
    if isinstance(x, Decimal):
        return str(x)
    return x
