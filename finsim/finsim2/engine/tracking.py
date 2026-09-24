"""Prediction tracking, model decay and correlation decay.

Every research run stores its forecasts (asset, horizon, model, model version, date, expected return, error band,
confidence, score). When a forecast's horizon has passed, the realised return is filled in and the error scored,
so FinSim2 accumulates an honest record of what its models actually got right. Decay compares the information
coefficient of out-of-sample forecasts over long and recent windows; a relationship that has stopped working is
flagged. Correlation decay does the same for pairs of assets.
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence, Tuple

from .align import Panel
from .horizons import pearson_idx, ranks

DECAY_WINDOWS = [("5Y", 1260), ("3Y", 756), ("1Y", 252), ("6M", 126)]


def _add_bdays(panel: Panel, iso: str, h: int) -> str:
    from datetime import date, timedelta
    cal = panel.calendar()
    i = panel.index_of(iso)
    if i + h < len(cal):
        return cal[i + h]
    d = date.fromisoformat(cal[-1])
    k = i + h - (len(cal) - 1)
    while k > 0:
        d += timedelta(days=1)
        if d.weekday() < 5:
            k -= 1
    return d.isoformat()


def record(store, panel: Panel, asset_id: str, made_on: str, model: str, version: str, horizon: str, h: int,
           expected: Optional[float], error_band: Optional[float], confidence: Optional[float], score: Optional[float], detail: Optional[dict] = None) -> Optional[int]:
    """Store a forecast once per (asset, horizon, model, date)."""
    if expected is None:
        return None
    for p in store.predictions(asset_id=asset_id, horizon=horizon):
        if p["model"] == model and p["made_on"] == made_on:
            return None
    return store.add_prediction({"asset_id": asset_id, "horizon": horizon, "model": model, "model_version": version, "made_on": made_on,
                                 "target_date": _add_bdays(panel, made_on, h), "predicted": expected, "error_band": error_band,
                                 "confidence": confidence, "score": score, "detail": detail or {}})


def score_matured(store, panel: Panel) -> int:
    cal = panel.calendar()
    last = cal[-1]
    n = 0
    for p in store.predictions(unscored_only=True):
        if p["target_date"] > last:
            continue
        px = panel.series(p["asset_id"])
        a = px[panel.index_of(p["made_on"])]
        b = px[panel.index_of(p["target_date"])]
        if a and b:
            realized = b / a - 1
            store.score_prediction(p["id"], realized, realized - p["predicted"], last)
            n += 1
    return n


def report(store, asset_id: Optional[str] = None) -> dict:
    preds = store.predictions(asset_id=asset_id)
    scored = [p for p in preds if p.get("realized") is not None]
    out = {"total": len(preds), "scored": len(scored), "pending": len(preds) - len(scored), "recent": preds[-200:]}
    if scored:
        hits = sum(1 for p in scored if (p["predicted"] > 0) == (p["realized"] > 0))
        out["hit_rate"] = hits / len(scored)
        out["mae"] = sum(abs(p["error"]) for p in scored) / len(scored)
        if len(scored) >= 10:
            a = [p["predicted"] for p in scored]
            b = [p["realized"] for p in scored]
            out["ic"] = pearson_idx(ranks(a), ranks(b), list(range(len(a))))
    return out


def decay(oos: Sequence[Tuple[int, float, float]], h: int) -> dict:
    """oos: (calendar index, prediction, realised) from walk-forward testing. IC over the trailing windows."""
    if not oos:
        return {"windows": {}, "flag": False}
    last = max(i for i, _, _ in oos)
    res = {}
    for lab, w in DECAY_WINDOWS:
        rows = [(p, r) for i, p, r in oos if i > last - w]
        if len(rows) >= 20:
            a = [p for p, _ in rows]
            b = [r for _, r in rows]
            res[lab] = {"ic": pearson_idx(ranks(a), ranks(b), list(range(len(a)))), "n": len(rows), "n_eff": max(1.0, len(rows) / max(1, h / 5))}
    long_ic = (res.get("5Y") or res.get("3Y") or {}).get("ic")
    short_ic = (res.get("6M") or res.get("1Y") or {}).get("ic")
    flag = long_ic is not None and short_ic is not None and long_ic >= 0.05 and short_ic <= 0.0
    return {"windows": res, "flag": flag, "message": "MODEL DECAY DETECTED: the recent out-of-sample IC has fallen to zero or below" if flag else None}


def correlation_decay(panel: Panel, a: str, b: str) -> dict:
    from .features import log_returns
    ra, rb = log_returns(panel.series(a)), log_returns(panel.series(b))
    out = {}
    for lab, w in [("5Y", 1260), ("1Y", 252), ("3M", 63)]:
        idx = [i for i in range(max(0, len(ra) - w), len(ra)) if ra[i] is not None and rb[i] is not None]
        if len(idx) >= 20:
            out[lab] = pearson_idx(ra, rb, idx)
    vals = [v for v in out.values() if v is not None]
    shift = (max(vals) - min(vals)) if len(vals) >= 2 else None
    return {"pair": [a, b], "windows": out, "shift": shift, "flag": shift is not None and shift >= 0.25}
