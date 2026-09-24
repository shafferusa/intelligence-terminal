"""AnalyticsEngine scoring: Quant Score, confidence, explanation and "what matters" for one asset.

Quant Score (asset a, horizon h, date t) in −100..100:
    w_i  = usefulness of signal i at horizon h (IC shrunk by its t-statistic), blended toward the IC measured
           inside the current regime when that regime has enough independent observations
    C    = Σ w_i z_i / Σ |w_i|               (a weighted average of standardised signals, direction included)
    S    = min(1, mean of the five largest |w_i| / 0.08) · min(1, median n_eff / 30)
           (how much evidence exists at this horizon, and on how many independent windows it rests)
    Score = 100 · tanh(C) · S
So the weights are learned from each signal's own history for this asset and horizon; nothing is equally weighted.
The expected return comes from regressing the realised h-day return on the historical composite C, and is shown
with that regression's typical error.
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional

from ..data.universe import HORIZONS
from . import regimes as rg
from .features import FEATURES, family, label, targets
from .horizons import pearson_idx
from .signals import score_label

STRONG_IC = 0.08
FULL_SAMPLE = 30.0          # independent windows needed before a horizon's score can reach full strength
SHORT = ("1D", "1W", "1M")
LONG = ("12M", "3Y", "5Y", "10Y")


def _weight(rec: dict, state: Dict[str, Optional[str]]) -> Optional[float]:
    u = rec.get("usefulness")
    if u is None:
        return None
    regs = rec.get("by_regime") or {}
    pairs = [(regs[s]["usefulness"], regs[s]["n_eff"]) for s in state.values() if s in regs and regs[s].get("usefulness") is not None]
    if not pairs:
        return u
    tot = sum(n for _, n in pairs)
    reg_u = sum(x * n for x, n in pairs) / tot if tot else u
    lam = 0.5 * min(1.0, (tot / len(pairs)) / 30.0)
    return (1 - lam) * u + lam * reg_u


def signal_items(z_today: Dict[str, Optional[float]], mat: Dict[str, Dict[str, dict]], horizon: str, state: Dict[str, Optional[str]]) -> List[dict]:
    items = []
    for sig, row in mat.items():
        rec = row.get(horizon)
        z = z_today.get(sig)
        if rec is None or z is None:
            continue
        w = _weight(rec, state)
        if w is None:
            continue
        items.append({"signal": sig, "label": label(sig), "family": family(sig), "z": z, "weight": w, "contribution": w * z,
                      "ic": rec["ic"], "t": rec["t"], "p": rec["p"], "q": rec.get("q"), "n_eff": rec["n_eff"], "hit_rate": rec["hit_rate"],
                      "stability": rec.get("stability"), "usefulness": rec["usefulness"]})
    items.sort(key=lambda x: -abs(x["contribution"]))
    return items


def composite(items: List[dict]) -> Optional[float]:
    tot = sum(abs(i["weight"]) for i in items)
    if tot <= 0:
        return None
    return sum(i["weight"] * i["z"] for i in items) / tot


def sample_factor(n_effs: List[float]) -> float:
    """Few independent windows (a 5-year horizon has about six in 34 years) cannot support a strong score however
    large the measured IC: damp toward zero until FULL_SAMPLE windows."""
    xs = sorted(x for x in n_effs if x is not None)
    return min(1.0, xs[len(xs) // 2] / FULL_SAMPLE) if xs else 0.0


def strength(items: List[dict]) -> float:
    ws = sorted((abs(i["weight"]) for i in items), reverse=True)[:5]
    if not ws:
        return 0.0
    return min(1.0, (sum(ws) / len(ws)) / STRONG_IC) * sample_factor([i.get("n_eff") for i in items])


def quant_score(items: List[dict]) -> Optional[float]:
    c = composite(items)
    if c is None:
        return None
    return 100.0 * math.tanh(c) * strength(items)


def composite_history(z: Dict[str, list], items: List[dict]) -> list:
    """The composite with today's weights over the whole history (for the expected-return mapping and charts)."""
    if not items:
        return []
    n = len(next(iter(z.values())))
    out = [None] * n
    for t in range(n):
        num = den = 0.0
        for it in items:
            v = z[it["signal"]][t]
            if v is not None:
                num += it["weight"] * v
                den += abs(it["weight"])
        if den > 0:
            out[t] = num / den
    return out


def expected_return(comp_hist: list, price: list, h: int) -> Optional[dict]:
    """Regress the realised h-day log return on the composite: expected return today, its typical error, and where
    today's composite sits in its own history."""
    tgt = targets(price, h)
    step = 1 if h <= 5 else min(21, max(1, h // 4))
    xs, ys = [], []
    for i in range(0, len(tgt), step):
        if comp_hist[i] is not None and tgt[i] is not None:
            xs.append(comp_hist[i]); ys.append(tgt[i])
    today = next((v for v in reversed(comp_hist) if v is not None), None)
    if len(xs) < 40 or today is None:
        return None
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    vx = sum((x - mx) ** 2 for x in xs) / n
    b = (sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / n) / vx if vx > 1e-12 else 0.0
    a = my - b * mx
    rmse = math.sqrt(sum((y - a - b * x) ** 2 for x, y in zip(xs, ys)) / n)
    hist = sorted(v for v in comp_hist if v is not None)
    pct = sum(1 for v in hist if v <= today) / len(hist)
    er = a + b * today
    vy = sum((y - my) ** 2 for y in ys) / n
    corr = b * math.sqrt(vx / vy) if vy > 0 and vx > 0 else 0.0
    return {"expected": math.exp(er) - 1, "error": rmse, "base_rate": math.exp(my) - 1, "percentile": pct, "n": n * step, "slope": b, "corr": corr}


def confidence(items: List[dict], rec_n_eff: Optional[float], regime_share: Dict[str, float], data_quality: float,
               agreement: Optional[float] = None, accuracy_corr: Optional[float] = None) -> Dict[str, float]:
    """0..1 from: statistical significance after false-discovery control (evidence), independent observations
    (sample size), sign stability across the three thirds of history, how well the combined signal has tracked
    later returns (accuracy), how familiar today's regime is, data completeness, and quant/ML agreement."""
    top = items[:5]
    ev = (sum(1.0 - min(1.0, (i.get("q") if i.get("q") is not None else (i.get("p") or 1.0)) / 0.2) for i in top) / len(top)) if top else 0.0
    st_vals = [i["stability"] for i in top if i.get("stability") is not None]
    stab = (sum(st_vals) / len(st_vals)) if st_vals else 0.3
    sample = min(1.0, math.log10(max(1.0, rec_n_eff or 1.0)) / 2.0)          # 10 independent windows -> 0.5, 100 -> 1
    acc = min(1.0, max(0.0, accuracy_corr or 0.0) / 0.15)
    shares = [v for v in regime_share.values() if v]
    regime = min(1.0, (sum(shares) / len(shares)) / 0.5) if shares else 0.5
    agr = 0.5 if agreement is None else agreement
    parts = {"evidence": ev, "sample_size": sample, "stability": stab, "accuracy": acc, "regime_similarity": regime,
             "data_quality": data_quality, "model_agreement": agr}
    # everything is gated by the number of independent observations: a perfect record over five windows is not evidence
    total = sample * (0.35 * ev + 0.25 * acc + 0.2 * stab + 0.1 * regime + 0.05 * data_quality + 0.05 * agr)
    return {"value": total, "label": "High" if total >= 0.65 else "Medium" if total >= 0.4 else "Low", "parts": parts}


def explain(items: List[dict], n: int = 5) -> Dict[str, List[dict]]:
    pos = [i for i in items if i["contribution"] > 0.02]
    neg = [i for i in items if i["contribution"] < -0.02]
    neu = [i for i in items if abs(i["contribution"]) <= 0.02 and abs(i["weight"]) >= 0.03]
    def f(xs):
        return [{"signal": i["signal"], "label": i["label"], "family": i["family"], "contribution": i["contribution"], "z": i["z"]} for i in xs[:n]]
    return {"bullish": f(pos), "bearish": f(neg), "neutral": f(neu)}


def what_matters(mat: Dict[str, Dict[str, dict]], horizons, ml_importance: Optional[Dict[str, float]] = None) -> List[dict]:
    """Families ranked by the evidence-weighted predictive usefulness of their signals over `horizons`
    (blended half-and-half with the ML models' permutation importance when available)."""
    fam: Dict[str, float] = {}
    for sig, row in mat.items():
        vals = [abs(row[h]["usefulness"]) for h in horizons if h in row and row[h].get("usefulness") is not None]
        if vals:
            fam[family(sig)] = fam.get(family(sig), 0.0) + max(vals)
    tot = sum(fam.values())
    if tot <= 0:
        return []
    share = {k: v / tot for k, v in fam.items()}
    if ml_importance:
        mt = sum(ml_importance.values()) or 1.0
        for k in set(share) | set(ml_importance):
            share[k] = 0.5 * share.get(k, 0.0) + 0.5 * ml_importance.get(k, 0.0) / mt
    rows = sorted(share.items(), key=lambda kv: -kv[1])
    top = rows[0][1] if rows else 1.0
    return [{"family": k, "share": v, "importance": "HIGH" if v >= 0.6 * top else "MEDIUM" if v >= 0.3 * top else "LOW"} for k, v in rows]


def horizon_scores(z_series: Dict[str, list], mat: Dict[str, Dict[str, dict]], price: list, reg: Dict[str, list],
                   horizons=HORIZONS, oos: Optional[Dict[str, dict]] = None) -> Dict[str, dict]:
    """Quant score, expected return, confidence and explanation for every horizon, as of the last date."""
    state = rg.current(reg)
    share = rg.history_share(reg, state)
    z_today = {k: next((x for x in reversed(v[-5:]) if x is not None), None) for k, v in z_series.items()}
    avail = sum(1 for k in mat if z_today.get(k) is not None)
    quality = avail / len(mat) if mat else 0.0
    out = {}
    for lab, h in horizons:
        items = signal_items(z_today, mat, lab, state)
        if not items:
            ne = sorted(r[lab]["n_eff"] for r in mat.values() if lab in r)
            med = ne[len(ne) // 2] if ne else None
            out[lab] = {"horizon": lab, "h": h, "score": None, "label": None, "confidence": {"value": 0.0, "label": "Low", "parts": {}}, "n_signals": 0,
                        "n_eff": med, "reason": (f"insufficient evidence: about {med:.0f} independent {lab} windows in the history" if med else "not enough history")}
            continue
        sc = quant_score(items)
        n_effs = sorted(i["n_eff"] for i in items)
        er = expected_return(composite_history(z_series, items), price, h)
        o = (oos or {}).get(lab) or {}
        acc = None
        if o.get("ic") is not None and o.get("n"):                        # out-of-sample IC of the point-in-time score,
            n_oos = o["n"] * min(21, max(1, h // 4)) / h                    # discounted when it rests on few windows
            acc = max(0.0, o["ic"]) * min(1.0, n_oos / 20.0)
        conf = confidence(items, n_effs[len(n_effs) // 2], share, quality, accuracy_corr=acc if acc is not None else 0.5 * abs((er or {}).get("corr") or 0.0))
        out[lab] = {"horizon": lab, "h": h, "score": sc, "label": score_label(sc), "composite": composite(items), "strength": strength(items),
                    "oos": {"ic": o.get("ic"), "p": o.get("p"), "n": o.get("n")},
                    "confidence": conf, "expected": er, "n_signals": len(items), "top": items[:12], "explain": explain(items),
                    "n_eff": n_effs[len(n_effs) // 2]}
    return out


def primary_horizon(hs: Dict[str, dict]) -> Optional[str]:
    """The horizon where the evidence is strongest: |score| × confidence (the calibrated score when it exists)."""
    best, val = None, 0.0
    for lab, r in hs.items():
        v = r.get("calibrated") if r.get("calibrated") is not None else r.get("score")
        c = (r.get("confidence") or {}).get("value")
        if v is None or not c:
            continue
        if abs(v) * c > val:
            best, val = lab, abs(v) * c
    return best


def family_share(hs: Dict[str, dict], horizons) -> List[dict]:
    """Which families move the Shaffer Score over these horizons: share of the absolute family points."""
    tot: Dict[str, float] = {}
    for lab in horizons:
        for f in (hs.get(lab) or {}).get("families") or []:
            tot[f["family"]] = tot.get(f["family"], 0.0) + abs(f.get("points") or 0.0)
    s = sum(tot.values())
    if s <= 0:
        return []
    rows = sorted(({"family": f, "share": v / s} for f, v in tot.items() if v > 0), key=lambda x: -x["share"])
    for r in rows:
        r["importance"] = "HIGH" if r["share"] >= 0.25 else "MEDIUM" if r["share"] >= 0.1 else "LOW"
    return rows


def agreement(ss: Optional[float], ml: Optional[float], ml_verified: bool = True, ss_conf: Optional[float] = None) -> Optional[str]:
    """Shaffer vs ML at one horizon, from the signs, the magnitudes and the confidence behind them:
    STRONG AGREEMENT / MODERATE AGREEMENT / MIXED / STRONG DISAGREEMENT, or NO VERIFIED ML EDGE."""
    if ss is None or ml is None:
        return None
    if not ml_verified or ml == 0:
        return "NO VERIFIED ML EDGE"
    if abs(ss) < 10 and abs(ml) < 10:
        return "MODERATE AGREEMENT"          # both see little
    same = (ss > 0) == (ml > 0)
    sim = 1.0 - abs(abs(ss) - abs(ml)) / max(abs(ss) + abs(ml), 1e-9)
    if same and abs(ss) >= 10 and abs(ml) >= 10:
        return "STRONG AGREEMENT" if sim >= 0.5 and (ss_conf or 0) >= 0.3 else "MODERATE AGREEMENT"
    if not same and abs(ss) >= 20 and abs(ml) >= 20:
        return "STRONG DISAGREEMENT"
    return "MIXED"
