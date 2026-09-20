"""
ShafferFinEval -- shared statistical primitives.

Pure stdlib. Used by both scoring engines so the sector model and the company
model normalise, winsorize and rank identically. Extracted verbatim from
sector_scoring.py; the behaviour is unchanged and sector_scoring re-exports
these names, so its public API is exactly as it was.
"""

from __future__ import annotations

import math
from typing import Iterable, Optional, Sequence

#: Winsorization bounds for company-level observations.
WINSOR_LOWER, WINSOR_UPPER = 0.05, 0.95

#: Minimum observations pulled in from each tail. floor(0.05 * n) is 0 for
#: every n < 20, which would leave small samples with no outlier protection.
MIN_WINSOR_TRIM = 1


def is_finite(value) -> bool:
    if value is None or isinstance(value, bool):
        return False
    if not isinstance(value, (int, float)):
        return False
    return math.isfinite(float(value))


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def percentile(sorted_values: Sequence[float], q: float) -> Optional[float]:
    """Linear-interpolation percentile of an already-sorted sequence.

    q is a fraction in [0, 1]. q=0 is the minimum, q=1 the maximum.
    """
    if not sorted_values:
        return None
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    position = q * (len(sorted_values) - 1)
    low = math.floor(position)
    high = math.ceil(position)
    if low == high:
        return float(sorted_values[int(position)])
    weight = position - low
    return float(sorted_values[low]) * (1 - weight) + float(sorted_values[high]) * weight


def winsorize(
    values: Iterable[float],
    lower: float = WINSOR_LOWER,
    upper: float = WINSOR_UPPER,
    min_trim: int = MIN_WINSOR_TRIM,
) -> list[float]:
    """Clamp observations to the 5th/95th percentile bounds.

    Count-based (the scipy.stats.mstats.winsorize convention): the k lowest and
    k highest observations are pulled in to the next value inward, where
    k = floor(limit * n). Nothing is discarded -- extremes are pulled back to
    the bound, so a single abnormal company cannot drag a mean around.

    Why not an interpolated percentile: with n = 20 and a 95% bound, linear
    interpolation lands *between* the top two observations, so the outlier
    partly sets its own cap and still leaks into the mean. Count-based bounds
    are taken from observations strictly inside the tail, which removes that.

    `min_trim` guarantees at least one observation is pulled in from each tail
    once the sample is large enough to allow it. Without it, floor(0.05 * n)
    is 0 for every n < 20, and a sample of 8 would get no outlier protection at
    all. It is reduced automatically if the sample is too small to trim both
    tails and still leave a value untouched.
    """
    cleaned = sorted(float(v) for v in values if is_finite(v))
    n = len(cleaned)
    if n == 0:
        return []
    if n < 3:
        return cleaned                      # nothing meaningful to trim

    k_low = max(int(math.floor(lower * n)), min_trim)
    k_high = max(int(math.floor((1.0 - upper) * n)), min_trim)
    # Always leave at least one untouched observation in the middle.
    while k_low + k_high >= n and (k_low > 0 or k_high > 0):
        if k_high >= k_low:
            k_high -= 1
        else:
            k_low -= 1

    low_bound = cleaned[k_low]
    high_bound = cleaned[n - 1 - k_high]
    if low_bound > high_bound:              # degenerate; leave the data alone
        return cleaned
    return [min(max(v, low_bound), high_bound) for v in cleaned]


def winsorized_mean(
    values: Iterable[float],
    lower: float = WINSOR_LOWER,
    upper: float = WINSOR_UPPER,
) -> tuple[Optional[float], int]:
    """Mean after winsorization. Returns (mean, n_used)."""
    clipped = winsorize(values, lower, upper)
    if not clipped:
        return None, 0
    return sum(clipped) / len(clipped), len(clipped)


def average_ranks(values: Sequence[float]) -> list[float]:
    """Ascending ranks, 1-based, with ties sharing the average rank."""
    indexed = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    position = 0
    while position < len(indexed):
        end = position
        while end + 1 < len(indexed) and values[indexed[end + 1]] == values[indexed[position]]:
            end += 1
        shared = (position + end) / 2 + 1        # average of the 1-based ranks
        for k in range(position, end + 1):
            ranks[indexed[k]] = shared
        position = end + 1
    return ranks


def median(values: Iterable[float]) -> Optional[float]:
    cleaned = sorted(float(v) for v in values if is_finite(v))
    if not cleaned:
        return None
    return percentile(cleaned, 0.5)


def percentile_rank_within(value, peer_values: Iterable[float]) -> Optional[float]:
    """Percentile position of `value` inside a peer distribution, in [0, 1].

    The target is ranked as a member of the combined population using average
    ranks, then p = (rank - 1) / (N - 1). Lowest in the group is 0, highest 1.
    Returns None when there are no usable peers to rank against -- a lone
    company has no percentile, and 0.5 would be a fabricated middle.
    """
    if not is_finite(value):
        return None
    peers = [float(v) for v in peer_values if is_finite(v)]
    if not peers:
        return None
    population = peers + [float(value)]
    ranks = average_ranks(population)
    n = len(population)
    if n < 2:
        return None
    return (ranks[-1] - 1) / (n - 1)


def percentile_to_score(p: Optional[float], higher_is_better: bool = True) -> Optional[float]:
    """Map a [0, 1] percentile onto [-100, +100].

        higher_is_better -> 200p - 100   (lowest -100, highest +100)
        lower is better  -> 100 - 200p   (lowest +100, highest -100)
    """
    if not is_finite(p):
        return None
    score = (200 * float(p) - 100) if higher_is_better else (100 - 200 * float(p))
    return clamp(score, -100.0, 100.0)
