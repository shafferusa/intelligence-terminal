"""
ShafferFinEval -- turning macro series into Shaffer v1 factor scores.

Pure computation on top of `macro_data`. Every function returns a normalized
score in [-100, +100] or None; None means the input is not available and the
engine drops the factor and renormalizes.

What can genuinely go live today, and what cannot:

    LIVE     rates (5 of 6 factors), gold (5 of 7), volatility (4 of 5),
             corporate-credit spread and rates legs
    BLOCKED  FX  -- needs foreign real rates and policy paths; only US macro
                    is wired, and a differential cannot be built from one side
             oil / gas / metals / ag -- need inventories, OPEC output, HDD/CDD,
                    PMI and mine supply, none of which have a source here
             ETF -- needs constituent holdings; Yahoo's holdings module is
                    crumb-gated

Nothing is estimated to fill those gaps.
"""

from __future__ import annotations

import math
from typing import Optional

from asset_models import normalize_time_series
from statlib import is_finite

#: How much history a time-series percentile needs to mean anything.
MIN_HISTORY = 60

#: Trading days used for trend and realised-volatility windows.
TREND_WINDOW = 63
REALISED_VOL_WINDOW = 21


def _percentile_score(value, history, higher_is_better=True) -> Optional[float]:
    if not is_finite(value) or len(history) < MIN_HISTORY:
        return None
    return normalize_time_series(value, history, higher_is_better, "percentile")


def _zscore_score(value, history, higher_is_better=True) -> Optional[float]:
    if not is_finite(value) or len(history) < MIN_HISTORY:
        return None
    return normalize_time_series(value, history, higher_is_better, "zscore")


def realised_volatility(prices, window: int = REALISED_VOL_WINDOW) -> Optional[float]:
    """Annualised realised volatility from a close series."""
    clean = [p for p in prices if is_finite(p) and p > 0]
    if len(clean) < window + 1:
        return None
    returns = [math.log(clean[i] / clean[i - 1]) for i in range(1, len(clean))][-window:]
    if len(returns) < 2:
        return None
    mean = sum(returns) / len(returns)
    variance = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    return math.sqrt(variance) * math.sqrt(252)


def trend_score(prices, window: int = TREND_WINDOW) -> Optional[float]:
    """Momentum as a percentile of the asset's own trailing returns."""
    clean = [p for p in prices if is_finite(p) and p > 0]
    if len(clean) < window + MIN_HISTORY:
        return None
    returns = [clean[i] / clean[i - window] - 1 for i in range(window, len(clean))]
    if len(returns) < MIN_HISTORY:
        return None
    return _percentile_score(returns[-1], returns[:-1], True)


# --------------------------------------------------------------------------
# Rates -- the leverage item: live rates feeds Treasuries, futures, IRS, swaptions
# --------------------------------------------------------------------------

def rates_factors(snapshot) -> tuple[dict, list]:
    """Factor scores for `rates_shaffer_v1`. Positive = bullish bond TOTAL RETURN."""
    values, missing = {}, []

    # Central-bank path: the 2y yield sitting BELOW the effective funds rate is
    # the market pricing cuts, which is bullish bond prices.
    funds, two_year = snapshot.latest("fed_funds"), snapshot.latest("ust_2y")
    funds_history = snapshot.history("fed_funds")
    two_history = snapshot.history("ust_2y")
    if is_finite(funds) and is_finite(two_year) and len(two_history) >= MIN_HISTORY:
        spread = funds - two_year
        history = [f - t for f, t in zip(funds_history[-len(two_history):], two_history)]
        values["central_bank"] = _percentile_score(spread, history, True)
    else:
        missing.append("central-bank path (fed funds vs 2y)")

    # Inflation: a FALLING breakeven is bullish bonds, so lower is better.
    breakeven = snapshot.get("breakeven_10y")
    if breakeven is not None and len(breakeven.values) >= MIN_HISTORY + 63:
        change = breakeven.change(63)
        history = [breakeven.values[i] - breakeven.values[i - 63]
                   for i in range(63, len(breakeven.values))]
        values["inflation"] = _percentile_score(change, history, False)
    else:
        missing.append("inflation trajectory (10y breakeven)")

    # Growth: RISING unemployment is bullish bonds, so higher change is better.
    unemployment = snapshot.get("unemployment")
    if unemployment is not None and len(unemployment.values) >= MIN_HISTORY + 6:
        change = unemployment.change(6)
        history = [unemployment.values[i] - unemployment.values[i - 6]
                   for i in range(6, len(unemployment.values))]
        values["growth"] = _percentile_score(change, history, True)
    else:
        missing.append("growth / labour trajectory")

    # Yield level: a higher starting yield is a better forward return.
    ten_year = snapshot.get("ust_10y")
    if ten_year is not None and len(ten_year.values) >= MIN_HISTORY:
        values["yield_level"] = _percentile_score(
            ten_year.latest, ten_year.values[:-1], True)
    else:
        missing.append("yield level vs regime")

    # Curve: a steeper 10s2s relative to its own history.
    if ten_year is not None and len(two_history) >= MIN_HISTORY:
        n = min(len(ten_year.values), len(two_history))
        slope_history = [a - b for a, b in
                         zip(ten_year.values[-n:], two_history[-n:])]
        values["curve"] = _percentile_score(slope_history[-1], slope_history[:-1], True)
    else:
        missing.append("curve valuation")

    # Fiscal / sovereign policy has no clean series here and is NOT estimated.
    missing.append("fiscal / sovereign policy (no wired source)")

    return {k: v for k, v in values.items() if v is not None}, missing


# --------------------------------------------------------------------------
# Gold
# --------------------------------------------------------------------------

def gold_factors(snapshot) -> tuple[dict, list]:
    """Factor scores for `gold_shaffer_v1`."""
    values, missing = {}, []

    real = snapshot.get("real_10y")
    if real is not None and len(real.values) >= MIN_HISTORY:
        values["real_yield"] = _percentile_score(real.latest, real.values[:-1], False)
    else:
        missing.append("real yields")

    dollar = snapshot.get("dollar_index")
    if dollar is not None and len(dollar.values) >= MIN_HISTORY:
        values["usd"] = _percentile_score(dollar.latest, dollar.values[:-1], False)
    else:
        missing.append("US dollar")

    breakeven = snapshot.get("breakeven_10y")
    if breakeven is not None and len(breakeven.values) >= MIN_HISTORY:
        values["inflation"] = _percentile_score(
            breakeven.latest, breakeven.values[:-1], True)
    else:
        missing.append("inflation expectations")

    vix = snapshot.get("vix")
    if vix is not None and len(vix.values) >= MIN_HISTORY:
        values["risk"] = _percentile_score(vix.latest, vix.values[:-1], True)
    else:
        missing.append("market stress")

    gold = snapshot.get("gold")
    if gold is not None:
        momentum = trend_score(gold.values)
        if momentum is not None:
            values["momentum"] = momentum
        else:
            missing.append("gold momentum (insufficient history)")
    else:
        missing.append("gold price")

    missing.append("ETF / central-bank flows (no wired source)")
    missing.append("geopolitical impact (entered manually)")
    return values, missing


# --------------------------------------------------------------------------
# Volatility
# --------------------------------------------------------------------------

def volatility_factors(snapshot, underlying_prices=None) -> tuple[dict, list]:
    """Factor scores for `vol_shaffer_v1`. Positive = attractive to be LONG vol."""
    values, missing = {}, []

    vix = snapshot.get("vix")
    if vix is None or len(vix.values) < MIN_HISTORY:
        return {}, ["VIX history"]

    # Vol value: implied (VIX) against realised. Cheap implied is attractive to
    # buy, so the spread is lower-is-better.
    realised = realised_volatility(underlying_prices or []) if underlying_prices else None
    if realised is not None:
        spread = vix.latest / 100.0 - realised
        history = []
        prices = underlying_prices
        for i in range(MIN_HISTORY, len(vix.values)):
            past_realised = realised_volatility(prices[: len(prices) - (len(vix.values) - i)])
            if past_realised is not None:
                history.append(vix.values[i] / 100.0 - past_realised)
        if len(history) >= MIN_HISTORY:
            values["vol_value"] = _percentile_score(spread, history, False)
        else:
            missing.append("implied-vs-realised history")
    else:
        missing.append("realised volatility (no underlying series)")

    vix_3m = snapshot.get("vix_3m")
    if vix_3m is not None and len(vix_3m.values) >= MIN_HISTORY:
        n = min(len(vix.values), len(vix_3m.values))
        ratio_history = [a / b for a, b in
                         zip(vix_3m.values[-n:], vix.values[-n:]) if b]
        if len(ratio_history) >= MIN_HISTORY:
            values["curve"] = _percentile_score(
                ratio_history[-1], ratio_history[:-1], False)
    else:
        missing.append("VIX term structure")

    values["stress"] = _percentile_score(vix.latest, vix.values[:-1], True)

    vol_of_vol = realised_volatility(vix.values)
    if vol_of_vol is not None:
        history = [realised_volatility(vix.values[:i])
                   for i in range(MIN_HISTORY, len(vix.values))]
        history = [h for h in history if h is not None]
        if len(history) >= MIN_HISTORY:
            values["vol_of_vol"] = _percentile_score(vol_of_vol, history, True)
    else:
        missing.append("vol-of-vol")

    missing.append("positioning / crowding (no wired source)")
    return {k: v for k, v in values.items() if v is not None}, missing


# --------------------------------------------------------------------------
# Corporate credit -- spread and rates legs only
# --------------------------------------------------------------------------

def corporate_credit_factors(snapshot, high_yield: bool = False) -> tuple[dict, list]:
    """Spread and rates legs for `corp_credit_shaffer_v1`.

    Issuer-level credit strength, cash-flow quality and technicals need
    per-issuer data that is not wired, so only the market-level legs are filled.
    """
    values, missing = {}, []

    key = "hy_oas" if high_yield else "ig_oas"
    oas = snapshot.get(key)
    if oas is not None and len(oas.values) >= MIN_HISTORY:
        # A WIDER spread is better compensation, so higher is better.
        values["spread"] = _percentile_score(oas.latest, oas.values[:-1], True)
    else:
        missing.append(f"{key} spread history")

    rates_values, rates_missing = rates_factors(snapshot)
    if rates_values:
        from asset_models import RATES, blend
        rates_score = blend(RATES, rates_values).score
        if rates_score is not None:
            values["rates"] = rates_score
    if not values.get("rates"):
        missing.append("rates leg")

    missing.extend([
        "issuer credit strength (needs per-issuer fundamentals)",
        "debt-service / cash-flow quality",
        "technical / liquidity",
        "issuer geopolitical impact (entered manually)",
    ])
    return values, missing


#: Engines that can be scored from wired sources today, and what each is missing.
LIVE_ENGINES = {
    "rates_shaffer_v1": rates_factors,
    "gold_shaffer_v1": gold_factors,
    "vol_shaffer_v1": volatility_factors,
    "corp_credit_shaffer_v1": corporate_credit_factors,
}

#: Engines whose arithmetic is implemented and tested but which have NO wired
#: data source. Listed explicitly so the gap is visible rather than implied.
BLOCKED_ENGINES = {
    "fx_shaffer_v1": "needs foreign real rates and policy paths; only US macro is wired",
    "oil_shaffer_v1": "needs EIA inventories, OPEC output and refinery utilisation",
    "natgas_shaffer_v1": "needs storage vs seasonal norm, HDD/CDD forecasts, LNG flows",
    "industrial_metal_shaffer_v1": "needs PMI, exchange inventories, China demand",
    "ag_shaffer_v1": "needs stocks-to-use, crop weather and export demand",
    "livestock_shaffer_v1": "needs herd counts, slaughter rates and feed costs",
    "crypto_shaffer_v1": "needs stablecoin liquidity, on-chain activity, funding/OI",
    "etf_shaffer_v1": "needs constituent holdings; Yahoo's holdings module is crumb-gated",
    "mbs_shaffer_v1": "needs option-adjusted spread and prepayment speeds",
    "structured_shaffer_v1": "needs deal-level collateral, OC/IC tests, DSCR/LTV",
    "preferred_shaffer_v1": "needs issuer credit, yield/spread and call schedules",
    "reit_shaffer_v1": "needs AFFO, NAV, cap rates and occupancy",
}
