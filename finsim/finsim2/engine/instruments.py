"""The trade layer of the Shaffer System: an underlying's forecast → an instrument position's expected P&L.

The Shaffer Alpha and Directional forecasts describe the underlying economic asset's unlevered percentage move
(SHAFFER_SYSTEM.md §1). This module converts them:

    spot / ETF / FX            q × unit value × E[R_h]
    futures, forwards          q × unit notional × E[R_h]             (h = the horizon, capped at expiry)
    options                    q × multiplier × (E[payoff at expiry] − premium), with ln S_T ~ N(ln S + μ̂_T, ŝ_T²)
                               (the lognormal form of the forecast; T = sessions to expiry)

Any horizon in sessions is interpolated from the Shaffer System horizons: μ̂ linearly in h, ŝ² linearly in h.
Futures on total-return indices (the Treasury futures) earn no carry, so their forecast overstates the future's
move by the carry; the output says so.
"""
from __future__ import annotations

import datetime as _dt
import math
from typing import Dict, Optional, Tuple

from .system import HMAP


def _phi(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def forecast_at(system: Dict[str, dict], sessions: int) -> Optional[Tuple[float, float]]:
    """(μ̂, ŝ) of the log return over `sessions`, interpolated from the asset's Shaffer System forecasts."""
    pts = sorted((HMAP[k], v["mu_log"], v["sigma_log"]) for k, v in (system or {}).items()
                 if k in HMAP and v.get("mu_log") is not None and v.get("sigma_log"))
    if not pts or sessions <= 0:
        return None
    if sessions <= pts[0][0]:
        h, m, s = pts[0]
        return m * sessions / h, s * math.sqrt(sessions / h)
    if sessions >= pts[-1][0]:
        h, m, s = pts[-1]
        return m * sessions / h, s * math.sqrt(sessions / h)
    for (h0, m0, s0), (h1, m1, s1) in zip(pts, pts[1:]):
        if h0 <= sessions <= h1:
            u = (sessions - h0) / (h1 - h0)
            return m0 + u * (m1 - m0), math.sqrt(s0 * s0 + u * (s1 * s1 - s0 * s0))
    return None


def expected_return(system: Dict[str, dict], sessions: int) -> Optional[float]:
    f = forecast_at(system, sessions)
    return None if f is None else math.exp(f[0] + 0.5 * f[1] * f[1]) - 1.0


def option_expected_payoff(S: float, K: float, right: str, mu: float, s: float) -> float:
    """E[max(S_T − K, 0)] (call) or E[max(K − S_T, 0)] (put) with ln S_T ~ N(ln S + μ, s²)."""
    if s <= 1e-12:
        st = S * math.exp(mu)
        return max(st - K, 0.0) if right.upper().startswith("C") else max(K - st, 0.0)
    fwd = S * math.exp(mu + 0.5 * s * s)
    d1 = (math.log(S / K) + mu + s * s) / s
    d2 = d1 - s
    if right.upper().startswith("C"):
        return fwd * _phi(d1) - K * _phi(d2)
    return K * _phi(-d2) - fwd * _phi(-d1)


def sessions_to(expiry: Optional[str], asof: str) -> Optional[int]:
    if not expiry:
        return None
    try:
        days = (_dt.date.fromisoformat(expiry[:10]) - _dt.date.fromisoformat(asof[:10])).days
    except ValueError:
        return None
    return max(1, round(days * 252 / 365))


def position_expected_pnl(inst, priced, quantity: float, system: Dict[str, dict], horizon_sessions: int, asof: str,
                          underlying_price: Optional[float] = None) -> dict:
    """Expected P&L (USD) of `quantity` units of `inst` over the horizon under the underlying's Shaffer forecast."""
    t = inst.type
    out = {"instrument": inst.id, "type": t, "underlying": inst.underlying, "quantity": quantity, "expected_pnl": None, "note": None}
    if not system:
        out["note"] = f"no Shaffer System forecast for {inst.underlying}"
        return out
    if t in ("SPOT", "FUTURE", "FORWARD"):
        h = horizon_sessions
        if t != "SPOT":
            te = sessions_to(inst.expiry, asof)
            if te:
                h = min(h, te)
        er = expected_return(system, h)
        unit = priced.unit_value() if t == "SPOT" else priced.unit_notional()
        if er is None or unit is None:
            out["note"] = "no price or forecast"
            return out
        out.update(expected_pnl=quantity * unit * er, expected_return=er, sessions=h)
        if t == "FUTURE" and (inst.spec or {}).get("kind") == "treasury":
            out["note"] = "the forecast is of the total-return index; a future earns no carry, so this overstates its move by the carry"
        return out
    if t == "OPTION":
        te = sessions_to(inst.expiry, asof) or horizon_sessions
        f = forecast_at(system, te)
        S = underlying_price
        if f is None or not S or not inst.strike or priced.price is None:
            out["note"] = "no forecast, underlying price or premium"
            return out
        pay = option_expected_payoff(S, inst.strike, inst.right or "C", f[0], f[1])
        out.update(expected_pnl=quantity * inst.multiplier * (pay - priced.price), expected_payoff=pay, premium=priced.price, sessions=te,
                   note="held to expiry; the lognormal form of the underlying's Shaffer forecast; not discounted")
        return out
    out["note"] = f"no conversion for {t}"
    return out
