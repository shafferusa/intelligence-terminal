"""
ShafferFinEval -- Shaffer Predicted Return.

Translates a Shaffer Score into a predicted 12-month ABSOLUTE price return from
today's market price. Pure stdlib, no Streamlit, no network.

What this is NOT
----------------
This is deliberately separate from every other price-like number in the app:

  * NOT excess return versus VTI (that is stored as a secondary research label)
  * NOT total return including dividends
  * NOT an analyst consensus target
  * NOT the peer-implied value from the valuation factor

The peer-implied value answers "what does this company's valuation look like
against its industry EBITDA peers?". The Shaffer 12M price answers "given
today's score, where does the score-to-return mapping put the market price in
twelve months?". They are shown side by side and never relabelled.

V1 calibration
--------------
    PredictedReturnPct = 0.20 x ShafferScore          (score in -100..+100)
    PredictedPrice     = CurrentPrice x (1 + PredictedReturnPct / 100)

So +100 -> +20%, +50 -> +10%, 0 -> 0%, -50 -> -10%, -100 -> -20%.

This is a transparent placeholder until the ML Lab has enough genuine
point-in-time observations to estimate the mapping empirically. The ML Lab may
PROPOSE a different alpha/beta; it never replaces this automatically.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from statlib import is_finite

#: V1 slope: percentage points of 12-month return per Shaffer Score point.
V1_SLOPE = 0.20
V1_INTERCEPT = 0.0

PREDICTION_MODEL = "shaffer_score_linear"
PREDICTION_MODEL_VERSION = "v1_0.20"
RETURN_CALIBRATION_VERSION = "return_calibration_v1_0.20"

#: Horizons the app tracks, in trading days. 12M stays the primary equity
#: target, but short horizons start producing genuine evidence within weeks
#: instead of a year. 1D/5D are unlikely to be what a fundamental score is for;
#: they are collected anyway so the Lab can SHOW that rather than assume it.
HORIZONS = {"1D": 1, "5D": 5, "1M": 21, "3M": 63, "6M": 126, "12M": 252}
PRIMARY_HORIZON = "12M"

#: Horizons that mature fast enough to learn from early.
EARLY_HORIZONS = ("1D", "5D", "1M", "3M")

#: Roughly how long a snapshot must age before each horizon has an outcome.
HORIZON_CALENDAR_DAYS = {
    "1D": 1, "5D": 7, "1M": 30, "3M": 91, "6M": 182, "12M": 365,
}

UNCALIBRATED_NOTE = "MODEL ESTIMATE — UNCERTAINTY NOT YET CALIBRATED"


@dataclass
class Prediction:
    """One score-to-return prediction."""

    shaffer_score: Optional[float] = None
    current_price: Optional[float] = None
    predicted_return_pct: Optional[float] = None
    predicted_price: Optional[float] = None
    model: str = PREDICTION_MODEL
    model_version: str = PREDICTION_MODEL_VERSION
    horizon: str = PRIMARY_HORIZON
    lower_return_pct: Optional[float] = None
    upper_return_pct: Optional[float] = None
    uncertainty_note: str = UNCALIBRATED_NOTE

    @property
    def available(self) -> bool:
        return self.predicted_return_pct is not None


def predicted_return_pct(
    shaffer_score, slope: float = V1_SLOPE, intercept: float = V1_INTERCEPT
) -> Optional[float]:
    """PredictedReturnPct = intercept + slope x ShafferScore, in percentage points."""
    if not is_finite(shaffer_score):
        return None
    return intercept + slope * float(shaffer_score)


def predicted_price(current_price, return_pct) -> Optional[float]:
    """PredictedPrice = CurrentPrice x (1 + ReturnPct / 100)."""
    if not is_finite(current_price) or not is_finite(return_pct):
        return None
    if float(current_price) <= 0:
        return None
    return float(current_price) * (1 + float(return_pct) / 100.0)


def build_prediction(
    shaffer_score,
    current_price,
    slope: float = V1_SLOPE,
    intercept: float = V1_INTERCEPT,
    model: str = PREDICTION_MODEL,
    model_version: str = PREDICTION_MODEL_VERSION,
    interval: Optional[tuple] = None,
) -> Prediction:
    """The full prediction record for one equity.

    `interval` is an optional (lower, upper) return band in percentage points.
    It is only ever supplied by the ML Lab once there is enough realised data;
    with none, the prediction is explicitly labelled as uncalibrated rather
    than dressed in a fabricated range.
    """
    ret = predicted_return_pct(shaffer_score, slope, intercept)
    prediction = Prediction(
        shaffer_score=float(shaffer_score) if is_finite(shaffer_score) else None,
        current_price=float(current_price) if is_finite(current_price) else None,
        predicted_return_pct=ret,
        predicted_price=predicted_price(current_price, ret),
        model=model,
        model_version=model_version,
    )
    if interval and all(is_finite(x) for x in interval):
        prediction.lower_return_pct = float(interval[0])
        prediction.upper_return_pct = float(interval[1])
        prediction.uncertainty_note = ""
    return prediction


def forward_return(price_then, price_later) -> Optional[float]:
    """Realised absolute price return between two observations, as a fraction."""
    if not is_finite(price_then) or not is_finite(price_later):
        return None
    if float(price_then) <= 0:
        return None
    return float(price_later) / float(price_then) - 1.0


def excess_return(stock_return, benchmark_return) -> Optional[float]:
    """Secondary research label: stock return minus VTI return.

    Stored for ML research only. The official Shaffer Predicted Return is the
    ABSOLUTE price return and is never quietly swapped for this.
    """
    if not is_finite(stock_return) or not is_finite(benchmark_return):
        return None
    return float(stock_return) - float(benchmark_return)


PREDICTION_DETAILS = """\
**Shaffer Predicted Return (V1)**

    PredictedReturnPct = 0.20 x ShafferScore
    PredictedPrice     = CurrentPrice x (1 + PredictedReturnPct / 100)

    +100 -> +20%      +50 -> +10%      0 -> 0%
     -50 -> -10%     -100 -> -20%

This is an **absolute 12-month price return from today's market price**. It is
not an excess return versus VTI, not a total return including dividends, not an
analyst target, and not the peer-implied value produced by the valuation
factor — that last one answers a different question and is shown separately.

The 0.20 slope is a transparent placeholder. The ML Lab estimates the mapping
empirically from realised outcomes and reports what it finds, but it never
replaces this calibration automatically: promotion is an explicit action.
"""
