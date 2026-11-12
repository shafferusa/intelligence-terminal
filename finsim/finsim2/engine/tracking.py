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
           expected: Optional[float], error_band: Optional[float], confidence: Optional[float], score: Optional[float],
           detail: Optional[dict] = None, **extra) -> Optional[int]:
    """Store a forecast once per (asset, horizon, model, date). The ledger is append-only (never rewritten)."""
    if expected is None and score is None and extra.get("raw") is None:
        return None
    for p in store.predictions(asset_id=asset_id, horizon=horizon):
        if p["model"] == model and p["made_on"] == made_on:
            return None
    return store.add_prediction({"asset_id": asset_id, "horizon": horizon, "model": model, "model_version": version, "made_on": made_on,
                                 "target_date": _add_bdays(panel, made_on, h), "predicted": expected, "error_band": error_band,
                                 "confidence": confidence, "score": score, "detail": detail or {}, **extra})


RISK_MODELS = ("ml_vol", "ml_drawdown")


def record_risk(store, panel: Panel, asset_id: str, made_on: str, version: str, horizon: str, h: int,
                vol: Optional[float], dd: Optional[float], threshold: Optional[float]) -> int:
    """The ML risk forecasts as their own ledger rows, graded against what they forecast: realised volatility over
    the horizon (annualised) and whether the drawdown inside the window reached the threshold."""
    n = 0
    if vol is not None:
        n += record(store, panel, asset_id, made_on, "ml_vol", version, horizon, h, vol, None, None, None, {"kind": "volatility"}, source="live") is not None
    if dd is not None and threshold:
        n += record(store, panel, asset_id, made_on, "ml_drawdown", version, horizon, h, dd, None, None, None,
                    {"kind": "drawdown", "threshold": threshold}, source="live") is not None
    return n


def _realised_risk(p: dict, px: list, i0: int, i1: int) -> Optional[float]:
    seg = [v for v in px[i0:i1 + 1] if v]
    if len(seg) < 3:
        return None
    if p["model"] == "ml_vol" or (p.get("detail") or {}).get("kind") == "volatility":
        lr = [math.log(b / a) for a, b in zip(seg, seg[1:])]
        return math.sqrt(252.0 / len(lr) * sum(x * x for x in lr))
    thr = (p.get("detail") or {}).get("threshold")
    if not thr:
        return None
    peak, worst = seg[0], 0.0
    for v in seg[1:]:
        peak = max(peak, v)
        worst = min(worst, v / peak - 1)
    return 1.0 if worst <= -thr else 0.0


def record_shaffer(store, panel: Panel, asset_id: str, full: dict) -> int:
    """Put today's Shaffer Score for every horizon into the ledger (raw, calibrated, expected return, range,
    confidence, regime and the family points), once per asset, horizon and date."""
    from .. import shaffer_score as cfg
    n = 0
    hmap = dict(cfg.HORIZONS)
    last = panel.calendar()[-1]
    for lab, r in (full.get("horizons") or {}).items():
        if r.get("raw") is None or r.get("date") != last:
            continue
        rng = r.get("range")
        fams = {f["family"]: round(f["points"], 3) for f in r.get("families") or []}
        pid = record(store, panel, asset_id, last, "shaffer", cfg.VERSION, lab, hmap[lab], r.get("expected"),
                     ((rng[1] - rng[0]) / 2) if rng else None, r.get("confidence"),
                     r.get("calibrated") if r.get("calibrated") is not None else r.get("raw"),
                     {"families": fams, "evidence": r.get("evidence"), "n_eff": r.get("n_eff")},
                     source="live", raw=r.get("raw"), calibrated=r.get("calibrated"),
                     range_lo=rng[0] if rng else None, range_hi=rng[1] if rng else None, regime=r.get("regime"))
        n += pid is not None
    return n


def score_matured(store, panel: Panel) -> int:
    """Grade every forecast whose horizon has passed: realised return, error, and whether the direction was right
    (the expected return's sign, else the score's)."""
    cal = panel.calendar()
    last = cal[-1]
    n = 0
    for p in store.predictions(unscored_only=True):
        if p["target_date"] > last:
            continue
        px = panel.series(p["asset_id"])
        if p["model"] in RISK_MODELS or (p.get("detail") or {}).get("kind") == "volatility":
            realized = _realised_risk(p, px, panel.index_of(p["made_on"]), panel.index_of(p["target_date"]))
            if realized is not None:
                store.score_prediction(p["id"], realized, realized - p["predicted"], last, None)
                n += 1
            continue
        a = px[panel.index_of(p["made_on"])]
        b = px[panel.index_of(p["target_date"])]
        if a and b:
            realized = b / a - 1
            ref = p["predicted"] if p.get("predicted") is not None else p.get("score")
            err = (realized - p["predicted"]) if p.get("predicted") is not None else None
            correct = None if ref is None or ref == 0 else (ref > 0) == (realized > 0)
            store.score_prediction(p["id"], realized, err, last, correct)
            n += 1
    return n


def report(store, asset_id: Optional[str] = None) -> dict:
    preds = store.predictions(asset_id=asset_id)
    scored = [p for p in preds if p.get("realized") is not None]
    out = {"total": len(preds), "scored": len(scored), "pending": len(preds) - len(scored), "recent": preds[-200:]}
    if scored:
        graded = [p for p in scored if p.get("correct") is not None]
        out["hit_rate"] = (sum(1 for p in graded if p["correct"]) / len(graded)) if graded else None
        errs = [abs(p["error"]) for p in scored if p.get("error") is not None]
        out["mae"] = (sum(errs) / len(errs)) if errs else None
        ref = [(p["predicted"] if p.get("predicted") is not None else p.get("score"), p["realized"]) for p in scored]
        ref = [(a, b) for a, b in ref if a is not None]
        if len(ref) >= 10:
            a = [x for x, _ in ref]
            b = [y for _, y in ref]
            out["ic"] = pearson_idx(ranks(a), ranks(b), list(range(len(a))))
    by = {}
    for p in scored:
        k = p["model"]
        g = by.setdefault(k, {"model": k, "scored": 0, "correct": 0})
        g["scored"] += 1
        g["correct"] += 1 if p.get("correct") else 0
    for g in by.values():
        g["hit_rate"] = g["correct"] / g["scored"] if g["scored"] else None
    out["by_model"] = list(by.values())
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
