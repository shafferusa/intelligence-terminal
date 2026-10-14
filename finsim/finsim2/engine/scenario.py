"""Scenario analysis: what a set of market moves would do to the portfolio.

Factors: 10-year yield (bp), Nasdaq-100 (%), oil (%), US dollar (%), VIX (to a level), credit spreads (bp).
1. Each holding's sensitivity to the six factors is estimated by regressing its weekly USD returns on the factors'
   weekly changes over five years.
2. Factors the user leaves blank are not assumed flat: they take their expected value given the ones specified,
   from the factors' historical covariance (E[f_u | f_s] = Σ_us Σ_ss⁻¹ f_s).
3. Bonds with a duration use −D·Δy + ½·C·Δy² for the rate move instead of the regression beta.
4. Historical analogues: the 21-session windows whose factor moves came closest to the scenario, and what
   today's holdings did in them.
Linear sensitivities understate the damage in crashes (correlations rise); the page says so.
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional

from finsim.quant.linalg import inverse
from .align import Panel
from .portfolio import aligned_returns, regress, _weekly

FACTOR_KEYS = ["y10_bp", "nasdaq_pct", "oil_pct", "usd_pct", "vix_pts", "credit_bp"]
FACTOR_LABELS = {"y10_bp": "10-year yield (bp)", "nasdaq_pct": "Nasdaq-100 (%)", "oil_pct": "Oil (%)", "usd_pct": "US dollar (%)",
                 "vix_pts": "VIX (points)", "credit_bp": "Credit spreads (bp)"}


def _level_changes(xs: List[Optional[float]], k: int, scale: float) -> List[Optional[float]]:
    out = []
    for i in range(0, len(xs) - k + 1, k):
        a, b = xs[i], xs[i + k - 1]
        out.append((b - a) * scale if a is not None and b is not None else None)
    return out


def factor_changes(panel: Panel, days: int = 1260, k: int = 5) -> Dict[str, List[Optional[float]]]:
    """Weekly (k-session) changes of the six factors in the units of the scenario inputs."""
    y10 = panel.macro("DGS10")[-days:]
    credit = panel.macro("BAA10Y")[-days:]
    vix = panel.macro("VIXCLS")[-days:]
    px = aligned_returns(panel, [a for a in ("QQQ", "WTI", "DXY") if panel.store.asset(a)], days)
    wk = _weekly(px, k)
    out = {"y10_bp": _level_changes(y10, k, 100.0), "credit_bp": _level_changes(credit, k, 100.0), "vix_pts": _level_changes(vix, k, 1.0)}
    m = len(out["y10_bp"])
    out["nasdaq_pct"] = [v * 100 if v is not None else None for v in wk.get("QQQ", [None] * m)]
    out["oil_pct"] = [v * 100 if v is not None else None for v in wk.get("WTI", [None] * m)]
    out["usd_pct"] = [v * 100 if v is not None else None for v in wk.get("DXY", [None] * m)]
    n = min(len(v) for v in out.values())
    return {kk: out[kk][-n:] for kk in FACTOR_KEYS}


def available(fchg: Dict[str, list]) -> List[str]:
    """Factors with enough history to use (a missing series is reported, not silently treated as flat)."""
    return [k for k in FACTOR_KEYS if fchg.get(k) and sum(1 for v in fchg[k] if v is not None) >= 0.8 * len(fchg[k])]


def complete_shock(shock: Dict[str, float], fchg: Dict[str, list], keys: Optional[List[str]] = None) -> Dict[str, float]:
    keys = keys or FACTOR_KEYS
    spec = [k for k in keys if shock.get(k) is not None]
    rest = [k for k in FACTOR_KEYS if k not in spec]
    rows = [t for t in range(len(fchg[FACTOR_KEYS[0]])) if all(fchg[k][t] is not None for k in keys)]
    if not spec or len(rows) < 30:
        return {k: (shock.get(k) or 0.0) if k in keys else None for k in FACTOR_KEYS}
    mean = {k: sum(fchg[k][t] for t in rows) / len(rows) for k in keys}

    def cov(a, b):
        return sum((fchg[a][t] - mean[a]) * (fchg[b][t] - mean[b]) for t in rows) / (len(rows) - 1)
    S_ss = [[cov(a, b) for b in spec] for a in spec]
    try:
        inv = inverse(S_ss)
    except Exception:
        return {k: (shock.get(k) or 0.0) if k in keys else None for k in FACTOR_KEYS}
    x = [shock[k] for k in spec]
    coef = [sum(inv[i][j] * x[j] for j in range(len(spec))) for i in range(len(spec))]
    out = {k: float(shock[k]) for k in spec}
    for u in rest:
        out[u] = sum(cov(u, s) * coef[i] for i, s in enumerate(spec)) if u in keys else None
    return out


def run(store, panel: Panel, positions: List[dict], shock: Dict[str, float], nav: float) -> dict:
    """positions: [{asset_id, market_value, duration, convexity}] (USD)."""
    cur_vix = next((v for v in reversed(panel.macro("VIXCLS")) if v is not None), None)
    if shock.get("vix_level") is not None and cur_vix is not None:
        shock = dict(shock, vix_pts=float(shock["vix_level"]) - cur_vix)
    fchg = factor_changes(panel)
    keys = available(fchg)
    missing = [k for k in FACTOR_KEYS if k not in keys]
    full = complete_shock(shock, fchg, keys)
    assets = [p["asset_id"] for p in positions]
    wk = _weekly(aligned_returns(panel, assets, 1260), 5)
    m = len(fchg[FACTOR_KEYS[0]])
    rows = []
    total = 0.0
    for p in positions:
        a = p["asset_id"]
        y = wk.get(a, [])[-m:]
        reg = regress(y, {k: fchg[k][-len(y):] for k in keys}) if len(y) > 40 and keys else {}
        betas = reg.get("betas", {})
        contrib = {k: betas.get(k, 0.0) * (full.get(k) or 0.0) for k in keys}
        dur = p.get("duration")
        if dur and full.get("y10_bp") is not None:
            dy = full["y10_bp"] / 1e4
            conv = p.get("convexity") or (dur * dur / 100.0)
            contrib["y10_bp"] = -dur * dy + 0.5 * conv * dy * dy
        move = sum(contrib.values())
        pnl = move * p["market_value"]
        total += pnl
        rows.append({"asset_id": a, "market_value": p["market_value"], "move": move, "pnl": pnl, "contrib": contrib,
                     "r2": reg.get("r2"), "method": "duration + regression" if dur else "regression on weekly factor moves"})
    analogues = historical_analogues(panel, positions, shock, nav)
    return {"shock": shock, "completed": full, "labels": FACTOR_LABELS, "positions": rows, "pnl": total,
            "pnl_pct": total / nav if nav else None, "analogues": analogues, "current_vix": cur_vix, "unavailable": missing,
            "note": "Linear sensitivities from five years of weekly data; blank factors move with the ones you set, as they have historically. Real crises are usually worse than linear estimates."}


def historical_analogues(panel: Panel, positions: List[dict], shock: Dict[str, float], nav: float, k: int = 21, top: int = 8) -> List[dict]:
    spec = [f for f in FACTOR_KEYS if shock.get(f) is not None]
    if not spec or not positions or not nav:
        return []
    cal = panel.calendar()
    days = min(len(cal), 252 * 25)
    y10 = panel.macro("DGS10")[-days:]
    credit = panel.macro("BAA10Y")[-days:]
    vix = panel.macro("VIXCLS")[-days:]
    px = {a: panel.series(a)[-days:] for a in ("QQQ", "WTI", "DXY") if panel.store.asset(a)}
    series = {"y10_bp": (y10, "lvl", 100.0), "credit_bp": (credit, "lvl", 100.0), "vix_pts": (vix, "lvl", 1.0),
              "nasdaq_pct": (px.get("QQQ"), "ret", 100.0), "oil_pct": (px.get("WTI"), "ret", 100.0), "usd_pct": (px.get("DXY"), "ret", 100.0)}
    weights = {p["asset_id"]: p["market_value"] / nav for p in positions}
    prices = {a: panel.series(a)[-days:] for a in weights}
    dates = cal[-days:]
    cands = []
    for s in range(0, days - k, 5):
        e = s + k
        vec = {}
        ok = True
        for f in spec:
            xs, kind, sc = series[f]
            if xs is None or xs[s] is None or xs[e] is None or (kind == "ret" and not xs[s]):
                ok = False
                break
            vec[f] = (xs[e] - xs[s]) * sc if kind == "lvl" else (xs[e] / xs[s] - 1) * sc
        if not ok:
            continue
        dist = math.sqrt(sum(((vec[f] - shock[f]) / (abs(shock[f]) + 1.0)) ** 2 for f in spec))
        port = 0.0
        cov_w = 0.0
        for a, w in weights.items():
            p = prices[a]
            if p[s] and p[e]:
                port += w * (p[e] / p[s] - 1)
                cov_w += abs(w)
        if cov_w < 0.5:
            continue
        cands.append((dist, s, e, vec, port))
    cands.sort()
    out, used = [], []
    for dist, s, e, vec, port in cands:
        if any(abs(s - u) < k for u in used):
            continue
        used.append(s)
        out.append({"start": dates[s], "end": dates[e], "moves": vec, "portfolio_return": port, "distance": dist})
        if len(out) >= top:
            break
    return out
