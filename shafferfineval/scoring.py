"""
ShafferFinEval -- shared score classification.

Pure stdlib. The four-band vocabulary used by every engine in the app, kept in
one place so the sector model, the company model and the UI cannot drift apart.

History: this module previously held the V1 company model (forward P/E and
Debt/Revenue against sector peers). That model is RETIRED -- it is superseded
by `company_scoring.py`, which scores valuation, growth, profitability and
debt against industry peers and adds the sector overlay.
"""

from __future__ import annotations

from typing import Optional

from statlib import clamp, is_finite

SCORE_MIN = -100.0
SCORE_MAX = 100.0

BULLISH = "BULLISH"
SEMI_BULLISH = "SEMI-BULLISH"
SEMI_BEARISH = "SEMI-BEARISH"
BEARISH = "BEARISH"

#: Lower bound of each band, highest first.
BAND_THRESHOLDS = (
    (40.0, BULLISH),
    (0.0, SEMI_BULLISH),
    (-40.0, SEMI_BEARISH),
)


def classify_score(score) -> Optional[str]:
    """Map a -100..+100 score onto the four-band label.

        +40 to +100  BULLISH
          0 to +40   SEMI-BULLISH
        -40 to 0     SEMI-BEARISH
       -100 to -40   BEARISH
    """
    if not is_finite(score):
        return None
    value = float(score)
    for threshold, label in BAND_THRESHOLDS:
        if value >= threshold:
            return label
    return BEARISH


CLASSIFICATION_DETAILS = """\
**Score interpretation**

    +40 to +100 = Bullish
      0 to +40  = Semi-Bullish
    -40 to 0    = Semi-Bearish
    -100 to -40 = Bearish
"""

__all__ = [
    "SCORE_MIN", "SCORE_MAX", "BULLISH", "SEMI_BULLISH", "SEMI_BEARISH",
    "BEARISH", "BAND_THRESHOLDS", "classify_score", "CLASSIFICATION_DETAILS",
    "clamp",
]
