"""Long and short Shaffer Scores for any product FinSim2 can price (net of what it costs to hold that side).

The Shaffer evidence score (engine/shaffer.py) is direction-symmetric: it says what the evidence implies for the
asset's return. What a trader keeps is not: a short pays borrow and, at a retail broker, earns no interest on its
proceeds; a leveraged fund decays; a future embeds financing; an option costs its volatility premium. So:

    edge_long  = E[total return over h] − r·h − frictions_long             (excess over holding cash)
    edge_short = −E[total return over h] − frictions_short                 (borrow, spread; no rebate on proceeds)
    futures:   ±(E[total return] − r·h) − frictions (financing is in the futures price, so long and short are nearly symmetric)
    options:   Δ·S·(E[total return] − r·h) + (value at the forecast volatility − premium) − spread     (long only)
    SS_net = 100·tanh( edge / σ_h / 0.25 ),   σ_h = forecast volatility × √(h/252) of the position's own P&L

E[total return] is the calibrated Shaffer expected return when its calibration is significant; otherwise the
risk-free drift is assumed (no edge), and the net scores then show only the cost of carrying each side."""
from __future__ import annotations

import math
from typing import Dict, List, Optional

from . import products as P
from .market import Market

G_SCALE = 0.25
HORIZONS = [("1D", 1), ("1W", 5), ("1M", 21), ("3M", 63), ("6M", 126), ("12M", 252), ("3Y", 756), ("5Y", 1260)]


def _expected(research, asset_id: str, lab: str) -> tuple:
    try:
        full = research.shaffer_full(asset_id)
    except Exception:
        return None, None, "no Shaffer Score for the underlying"
    hz = (full.get("horizons") or {}).get(lab) or {}
    e = hz.get("expected")
    if e is None:
        return None, hz, "no significant calibration: risk-free drift assumed (no edge)"
    return float(e), hz, "calibrated Shaffer expected return"


def net_scores(research, inst_id: str, horizons=HORIZONS, market: Optional[Market] = None) -> dict:
    m = market or Market(research)
    try:
        inst = P.parse(inst_id, research.store)
    except ValueError as e:
        return {"id": inst_id, "error": str(e)}
    from .risk import RiskModel
    pr = P.Priced(inst, m, RiskModel(m))
    if pr.price is None:
        return {"id": inst_id, "error": "; ".join(pr.reasons) or "no price"}
    r, _, _ = m.short_rate("USD")
    r = r or 0.0
    und = inst.underlying if inst.type != "SPOT" else inst.id
    if inst.type == "FUTURE" and inst.spec["kind"] == "treasury":
        und = {"ZT": "UST2Y", "ZF": "UST5Y", "ZN": "UST10Y", "TN": "UST10Y", "ZB": "UST30Y", "UB": "UST30Y"}.get(inst.root, "UST10Y")
    if inst.type == "FUTURE" and inst.spec["kind"] == "equity_index":
        und = inst.spec["proxy"]                      # the ETF carries the Shaffer record (total return, with dividends)
    fvol, vsrc = m.vol_forecast(und)
    fvol = fvol or m.realized_vol(und) or 0.2
    out = {"id": inst_id, "name": inst.name, "type": inst.type, "product_type": inst.product_type, "underlying": und, "horizons": {},
           "vol_forecast": fvol, "vol_source": vsrc, "rate": r}
    a = research.store.asset(inst.id) if inst.type == "SPOT" else None
    can_short = inst.type in ("FUTURE", "FORWARD") or (inst.type == "SPOT" and a and a.get("asset_class") not in ("CRYPTO", "INDEX")
                                                        and not (a.get("meta") or {}).get("synthetic"))
    for lab, h in horizons:
        yrs = h / 252.0
        E, hz, src = _expected(research, und, lab)
        drift = E if E is not None else r * yrs
        sig = fvol * math.sqrt(yrs)
        row = {"expected_source": src, "expected_total_return": E, "raw": (hz or {}).get("raw"), "calibrated": (hz or {}).get("calibrated"),
               "confidence": ((hz or {}).get("confidence") or {}).get("value") if isinstance((hz or {}).get("confidence"), dict) else (hz or {}).get("confidence")}
        if inst.type == "OPTION":
            g = pr.greeks_per_unit() or {}
            S = pr.inputs["spot"]["value"]
            c = pr.costs(h, "BUY")
            prem = g.get("premium") or 0.0
            dir_edge = g.get("delta", 0.0) * S * 100 * (drift - r * yrs)
            vol_edge = -(c.get("volatility_premium") or 0.0)
            edge = dir_edge + vol_edge - (c.get("spread") or 0.0) - (c.get("commission") or 0.0)
            sig_p = abs(g.get("delta", 0.0)) * S * 100 * sig + abs(g.get("vega_usd") or 0.0) * 2.0
            row.update({"long": 100 * math.tanh(edge / sig_p / G_SCALE) if sig_p else None, "short": None,
                        "edge_long": edge, "edge_long_pct_premium": edge / prem if prem else None,
                        "components": {"directional": dir_edge, "volatility": vol_edge, "spread_and_commission": (c.get("spread") or 0) + (c.get("commission") or 0),
                                       "premium": prem, "theta_if_unchanged": (c.get("info") or {}).get("theta_if_unchanged")},
                        "short_note": "writing options is not supported"})
        else:
            unit = (pr.unit_notional() or 0.0) if inst.type != "SPOT" else (pr.unit_value() or 0.0)
            if not unit:
                continue
            cl = pr.costs(h, "BUY")
            cs = pr.costs(h, "SELL") if can_short else {}
            fl = (cl.get("total") or 0.0) / unit
            fs = ((cs.get("total") or 0.0) / unit) if can_short else None
            if inst.type == "SPOT":
                lev = ((a or {}).get("meta") or {}).get("leverage") or 1.0
                excess = drift - r * yrs
                el = excess - fl
                # the short keeps the full −E; its lost interest on the proceeds is in its frictions (short_financing)
                es = (-drift + r * yrs - fs) if can_short else None
            else:
                excess = drift - r * yrs
                el = excess - fl
                es = -excess - fs if fs is not None else None
            row.update({"long": 100 * math.tanh(el / sig / G_SCALE) if sig else None,
                        "short": (100 * math.tanh(es / sig / G_SCALE) if (sig and es is not None) else None),
                        "edge_long": el, "edge_short": es, "frictions_long": fl, "frictions_short": fs,
                        "costs_long": {k: v for k, v in cl.items() if k != "info"}, "costs_short": {k: v for k, v in (cs or {}).items() if k != "info"},
                        "short_note": None if can_short else "shorting is not supported for this product"})
        out["horizons"][lab] = row
    return out
