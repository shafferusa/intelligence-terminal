"""Cents <-> Decimal, quantisation and base conversion with the rounding plug (TERMINAL_DESIGN.md §2.2, §5.3).

Conventions
- Money lives in SQLite as integer cents (``*_c``) and in Python as ``decimal.Decimal``; floats never reach a
  posting (a float argument is a TypeError here on purpose).
- Every rounding is HALF_EVEN (banker's rounding), as ``finsim.money.money`` does.
- ``base_with_plug`` implements the §5.3 rule: in a group of lines in one currency at one rate whose native
  amounts sum to zero, every line but the largest-|amount| line is converted with ``fx_c``; that line's base is
  minus the sum of the others, so the group sums to zero in base exactly, by construction.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN
from typing import Iterable, List, Sequence, Union

from finsim.money import D

Number = Union[Decimal, str, int]
CENT = Decimal("0.01")
PRICE_8 = Decimal("0.00000001")
ONE = Decimal(1)


def _dec(x: Number) -> Decimal:
    if isinstance(x, bool):
        raise TypeError("bool is not a money amount")
    if isinstance(x, float):
        raise TypeError("floats are not allowed in accounting; pass a Decimal, str or int")
    if isinstance(x, Decimal):
        d = x
    elif isinstance(x, (int, str)):
        try:
            d = Decimal(str(x).replace(",", "").strip()) if isinstance(x, str) else Decimal(x)
        except InvalidOperation as e:
            raise ValueError(f"not a number: {x!r}") from e
    else:
        raise TypeError(f"unsupported amount type {type(x).__name__}")
    if not d.is_finite():
        raise ValueError(f"not a finite number: {x!r}")
    return d


def to_c(x: Number) -> int:
    """Quantise to 0.01 HALF_EVEN and return integer cents.  ValueError on NaN/inf, TypeError on float."""
    return int(_dec(x).quantize(CENT, rounding=ROUND_HALF_EVEN) * 100)


def from_c(c: int) -> Decimal:
    """Integer cents -> Decimal with exactly two decimal places."""
    if isinstance(c, bool) or not isinstance(c, int):
        raise TypeError(f"cents must be an int, got {type(c).__name__}")
    return (Decimal(c) / Decimal(100)).quantize(CENT)


def qprice(x: Number) -> Decimal:
    """Canonical price / rate: at most 8 decimal places, trailing zeros removed, never exponent notation,
    so ``str(qprice(s))`` round-trips through ``qprice`` unchanged."""
    d = _dec(x).quantize(PRICE_8, rounding=ROUND_HALF_EVEN)
    if d == d.to_integral_value():
        return d.quantize(ONE)
    return d.normalize()


def mul_c(qty: int, px: Number, mult: Number = ONE, scale: Number = ONE) -> int:
    """``to_c(qty × px × mult × scale)`` — contract value in cents (signed by ``qty``)."""
    return to_c(Decimal(int(qty)) * _dec(px) * _dec(mult) * _dec(scale))


def fx_c(amount_c: int, rate: Number) -> int:
    """HALF_EVEN base conversion of one line: ``to_c(from_c(amount_c) × rate)``."""
    return to_c(from_c(int(amount_c)) * _dec(rate))


def base_with_plug(amounts_c: Sequence[int], rate: Number) -> List[int]:
    """The §5.3 rounding plug.  Every line but the first line with the largest |amount| is converted with
    ``fx_c``; that line's base is ``-sum(others)``.  Requires ``sum(amounts_c) == 0`` (ValueError otherwise), and
    then guarantees ``sum(result) == 0`` exactly."""
    amounts = [int(a) for a in amounts_c]
    if not amounts:
        return []
    if sum(amounts) != 0:
        raise ValueError(f"amounts must sum to zero in their currency, got {sum(amounts)}")
    r = _dec(rate)
    plug_idx = max(range(len(amounts)), key=lambda i: (abs(amounts[i]), -i))
    out = [fx_c(a, r) for a in amounts]
    out[plug_idx] = -sum(b for i, b in enumerate(out) if i != plug_idx)
    return out


def fmt_money(c: int) -> str:
    """``1,697,525.00`` (a leading ``-`` for negatives)."""
    c = int(c)
    sign = "-" if c < 0 else ""
    whole, frac = divmod(abs(c), 100)
    return f"{sign}{whole:,}.{frac:02d}"


def sum_c(values: Iterable[int]) -> int:
    return sum(int(v) for v in values)
