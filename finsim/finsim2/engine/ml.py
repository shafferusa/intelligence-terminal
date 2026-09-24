"""MLEngine: which variables matter, when, for which assets, over which horizon — tested out of sample.

For one asset and one horizon h:
  rows      dates t (every `STEP[h]` sessions) with the standardised signals X_t and the forward log return y_t
  folds     walk-forward: the first 40% of rows only train; the rest is cut into consecutive test blocks. A model for a
            block is trained only on rows whose h-day target window ended before the block starts (purged), so no
            future information reaches it.
  models    OLS, ridge, LASSO, elastic net, random forest, gradient boosting (XGBoost-style), and logistic regression
            for the direction; trees only where there are enough rows.
  ranking   by stable out-of-sample performance: mean fold IC − ½ × its standard deviation, not in-sample fit.
  ensemble  each block's weights come from the models' performance on EARLIER blocks only (a walk-forward ensemble).
  outputs   ML score (−100..100, where today's forecast sits among the ensemble's past forecasts, scaled by how well the
            ensemble has worked), expected return with its out-of-sample error, probability of a rise, confidence,
            feature importance (permutation on held-out blocks), an explanation of today's forecast, decay.
Pooled models (global and per asset class) stack many assets with volatility-scaled targets to find what works
across the market.
"""
from __future__ import annotations

import math
import time
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from ..data.universe import HORIZONS
from . import models as M
from .features import FEATURES, compute_features, family, label, targets
from .signals import standardize_all

VERSION = "fs2-ml-1"
STEP = {1: 5, 5: 5, 21: 5, 63: 10, 126: 21, 252: 21, 756: 21, 1260: 21, 2520: 21}
REG_MODELS = ["ols", "ridge", "lasso", "elastic_net", "random_forest", "gradient_boosting"]
TREE_MODELS = {"random_forest", "gradient_boosting"}
SHORT = ("1D", "1W", "1M")
LONG = ("12M", "3Y", "5Y", "10Y")


def _progress(cb, d, n, m):
    if cb:
        try:
            cb(d, n, m)
        except Exception:
            pass


# ------------------------------------------------------------------ datasets
def asset_dataset(z: Dict[str, list], price: list, h: int, names: Optional[List[str]] = None):
    step = STEP.get(h, 21)
    names = names or [k for k in FEATURES if k in z]
    tgt = targets(price, h)
    n = len(price)
    first = next((i for i in range(n) if sum(1 for k in names if z[k][i] is not None) >= 0.6 * len(names)), None)
    if first is None:
        return None
    idx = [t for t in range(first, n, step) if tgt[t] is not None]
    keep = [k for k in names if sum(1 for t in idx if z[k][t] is not None) >= 0.6 * max(1, len(idx))]
    X = [[z[k][t] for k in keep] for t in idx]
    y = [tgt[t] for t in idx]
    last = max(i for i in range(n) if price[i] is not None)
    x_today = [z[k][last] if z[k][last] is not None else next((v for v in reversed(z[k][max(0, last - 5):last + 1]) if v is not None), None) for k in keep]
    return {"names": keep, "idx": idx, "X": X, "y": y, "x_today": x_today, "last": last, "step": step}


# ------------------------------------------------------------------ walk-forward
def _folds(idx: List[int], h: int, n_blocks: int = 6, min_train_frac: float = 0.4) -> List[Tuple[List[int], List[int]]]:
    N = len(idx)
    start = max(60, int(N * min_train_frac))
    if N - start < 30:
        return []
    k = max(2, min(n_blocks, (N - start) // 20))
    size = (N - start) // k
    out = []
    for j in range(k):
        a = start + j * size
        b = N if j == k - 1 else a + size
        train = [i for i in range(a) if idx[i] + h < idx[a]]       # purge: the target window closed before the block
        if len(train) >= 40:
            out.append((train, list(range(a, b))))
    return out


def _fit_predict(name: str, X, y, Xt, n_rows: int):
    kw = {}
    if name == "random_forest":
        kw = {"n_estimators": 30 if n_rows > 1500 else 40}
    if name == "gradient_boosting":
        kw = {"n_estimators": 80}
    m = M.make_model(name, **kw)
    m.fit(X, y)
    return m, m.predict(Xt)


def _sharpe_of(preds: List[float], ys: List[float], h: int, step: int) -> Optional[float]:
    k = max(1, h // step)
    r = [(1.0 if p > 0 else -1.0) * y for j, (p, y) in enumerate(zip(preds, ys)) if j % k == 0]
    if len(r) < 8:
        return None
    mu = sum(r) / len(r)
    sd = math.sqrt(sum((x - mu) ** 2 for x in r) / (len(r) - 1))
    return (mu / sd) * math.sqrt(252.0 / h) if sd > 0 else None


def walk_forward(ds: dict, h: int, models: Sequence[str], progress=None) -> dict:
    X, y, idx, step = ds["X"], ds["y"], ds["idx"], ds["step"]
    folds = _folds(idx, h)
    if not folds:
        return {"folds": 0}
    per: Dict[str, dict] = {m: {"preds": [], "fold_ic": []} for m in models}
    ybin = [1.0 if v > 0 else 0.0 for v in y]
    logit = {"preds": [], "fold_ic": []}
    test_rows: List[int] = []
    last_fold_models: Dict[str, object] = {}
    for fi, (tr, te) in enumerate(folds):
        Xtr = [X[i] for i in tr]; ytr = [y[i] for i in tr]; Xte = [X[i] for i in te]
        test_rows += te
        for m in models:
            try:
                mod, p = _fit_predict(m, Xtr, ytr, Xte, len(tr))
            except Exception:
                mod, p = None, [0.0] * len(te)
            per[m]["preds"] += p
            per[m]["fold_ic"].append(M.spearman(p, [y[i] for i in te]))
            if fi == len(folds) - 1:
                last_fold_models[m] = mod
        try:
            lg = M.make_model("logistic")
            lg.fit(Xtr, [ybin[i] for i in tr])
            pp = lg.predict_proba(Xte)
        except Exception:
            pp = [0.5] * len(te)
        logit["preds"] += pp
        logit["fold_ic"].append(M.spearman(pp, [y[i] for i in te]))
        _progress(progress, fi + 1, len(folds), f"fold {fi + 1}/{len(folds)}")
    ys = [y[i] for i in test_rows]
    board = []
    for m in models:
        fic = [v for v in per[m]["fold_ic"] if v is not None]
        mean = sum(fic) / len(fic) if fic else None
        sd = math.sqrt(sum((v - mean) ** 2 for v in fic) / max(1, len(fic) - 1)) if len(fic) > 1 else None
        preds = per[m]["preds"]
        board.append({"model": m, "label": M.MODEL_LABELS.get(m, m), "ic": M.spearman(preds, ys),
                      "accuracy": sum(1 for p, v in zip(preds, ys) if (p > 0) == (v > 0)) / len(ys),
                      "rmse": math.sqrt(sum((p - v) ** 2 for p, v in zip(preds, ys)) / len(ys)),
                      "sharpe": _sharpe_of(preds, ys, h, step), "fold_ic": fic, "fold_ic_mean": mean, "fold_ic_sd": sd,
                      "positive_folds": (sum(1 for v in fic if v > 0) / len(fic)) if fic else None,
                      "robust": (mean - 0.5 * (sd or 0.0)) if mean is not None else None,
                      "stability": ("High" if sd is not None and mean is not None and sd < 0.08 and mean > 0 else
                                    "Medium" if sd is not None and sd < 0.15 else "Low")})
    lfic = [v for v in logit["fold_ic"] if v is not None]
    board.append({"model": "logistic", "label": M.MODEL_LABELS.get("logistic", "logistic"), "ic": M.spearman(logit["preds"], ys),
                  "accuracy": sum(1 for p, v in zip(logit["preds"], ys) if (p > 0.5) == (v > 0)) / len(ys), "rmse": None,
                  "sharpe": _sharpe_of([p - 0.5 for p in logit["preds"]], ys, h, step), "fold_ic": lfic,
                  "fold_ic_mean": (sum(lfic) / len(lfic)) if lfic else None, "fold_ic_sd": None,
                  "positive_folds": (sum(1 for v in lfic if v > 0) / len(lfic)) if lfic else None, "robust": None, "stability": None})
    # walk-forward ensemble: block k is weighted by the models' ICs on blocks before k
    ens = []
    pos = 0
    weights_hist = []
    for fi, (tr, te) in enumerate(folds):
        if fi == 0:
            w = {m: 1.0 for m in models}
        else:
            w = {}
            for m in models:
                prev = [v for v in per[m]["fold_ic"][:fi] if v is not None]
                w[m] = max(0.0, sum(prev) / len(prev)) if prev else 0.0
            if sum(w.values()) <= 0:
                w = {m: 0.0 for m in models}
        tot = sum(w.values())
        weights_hist.append(w)
        for j in range(len(te)):
            ens.append(sum(w[m] * per[m]["preds"][pos + j] for m in models) / tot if tot > 0 else 0.0)
        pos += len(te)
    efic = []
    pos = 0
    for tr, te in folds:
        efic.append(M.spearman(ens[pos:pos + len(te)], [y[i] for i in te]))
        pos += len(te)
    efic_v = [v for v in efic if v is not None]
    return {"folds": len(folds), "board": board, "per": per, "logit": logit, "test_rows": test_rows, "ens": ens,
            "ens_ic": M.spearman(ens, ys), "ens_fold_ic": efic, "ens_positive_folds": (sum(1 for v in efic_v if v > 0) / len(efic_v)) if efic_v else None,
            "ens_rmse": math.sqrt(sum((p - v) ** 2 for p, v in zip(ens, ys)) / len(ys)), "ens_sharpe": _sharpe_of(ens, ys, h, step),
            "last_fold": folds[-1], "last_fold_models": last_fold_models, "fold_bounds": [(idx[te[0]], idx[te[-1]]) for _, te in folds]}


def _importance(models: Dict[str, object], weights: Dict[str, float], X, y, names: List[str]) -> Dict[str, float]:
    """Permutation importance (drop in rank IC when a feature is shuffled) on the last held-out block, averaged over
    the models with their ensemble weights; negatives clipped to zero, normalised to 1."""
    total: Dict[str, float] = {n: 0.0 for n in names}
    wsum = 0.0
    for m, mod in models.items():
        w = weights.get(m, 0.0)
        if mod is None or w <= 0:
            continue
        try:
            imp = M.permutation_importance(mod, X, y, lambda a, b: M.spearman(b, a) or 0.0, n_repeats=2, seed=1)
        except Exception:
            continue
        for k, v in zip(names, imp):
            total[k] += w * max(0.0, v)
        wsum += w
    s = sum(total.values())
    return {k: v / s for k, v in total.items()} if s > 0 else {}


def _families(imp: Dict[str, float]) -> Dict[str, float]:
    out: Dict[str, float] = {}
    for k, v in imp.items():
        out[family(k)] = out.get(family(k), 0.0) + v
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


def _pct_rank(values: List[float], x: float) -> float:
    if not values:
        return 0.5
    return (sum(1 for v in values if v < x) + 0.5 * sum(1 for v in values if v == x)) / len(values)


def train_horizon(ds: dict, h: int, lab: str, progress=None) -> dict:
    X, y = ds["X"], ds["y"]
    n_rows = len(X)
    use_trees = h <= 252 and n_rows >= 300
    models = [m for m in REG_MODELS if use_trees or m not in TREE_MODELS]
    wf = walk_forward(ds, h, models, progress)
    if not wf.get("folds"):
        return {"horizon": lab, "status": "insufficient", "reason": f"only {n_rows} usable rows", "rows": n_rows}
    board = sorted(wf["board"], key=lambda r: -(r["robust"] if r["robust"] is not None else -9))
    # final weights from all blocks; final models on all rows whose targets are known
    weights = {}
    for m in models:
        fic = [v for v in wf["per"][m]["fold_ic"] if v is not None]
        weights[m] = max(0.0, sum(fic) / len(fic)) if fic else 0.0
    wsum = sum(weights.values())
    finals = {}
    preds_today = {}
    for m in models:
        if weights[m] <= 0 and m not in ("ridge", "gradient_boosting"):
            continue
        try:
            mod, p = _fit_predict(m, X, y, [ds["x_today"]], n_rows)
            finals[m] = mod
            preds_today[m] = p[0]
        except Exception:
            pass
    ens_today = (sum(weights[m] * preds_today[m] for m in preds_today if weights[m] > 0) / sum(weights[m] for m in preds_today if weights[m] > 0)) \
        if any(weights[m] > 0 for m in preds_today) else None
    try:
        lg = M.make_model("logistic")
        lg.fit(X, [1.0 if v > 0 else 0.0 for v in y])
        prob_up = lg.predict_proba([ds["x_today"]])[0]
    except Exception:
        prob_up = None
    ens_ic = wf["ens_ic"]
    step = ds["step"]
    n_oos = len(wf["test_rows"])
    n_eff = max(1.0, n_oos * step / h)
    quality = min(1.0, max(0.0, ens_ic or 0.0) / 0.10)
    score = None
    if ens_today is not None and wf["ens"]:
        pct = _pct_rank(wf["ens"], ens_today)
        score = (2 * pct - 1) * 100 * quality
    agree = None
    if ens_today is not None and preds_today:
        agree = sum(1 for m, p in preds_today.items() if weights.get(m, 0) > 0 and (p > 0) == (ens_today > 0)) / max(1, sum(1 for m in preds_today if weights.get(m, 0) > 0))
    sample = min(1.0, math.log10(max(1.0, n_eff)) / 2.0)
    stab = wf["ens_positive_folds"] or 0.0
    conf_v = sample * (0.45 * quality + 0.3 * stab + 0.25 * (agree if agree is not None else 0.5))
    # importance on the last held-out block with the last fold's models
    tr, te = wf["last_fold"]
    imp = _importance(wf["last_fold_models"], weights, [X[i] for i in te], [y[i] for i in te], ds["names"])
    # explanation: weighted contributions of today's features
    contrib = {n: 0.0 for n in ds["names"]}
    cw = 0.0
    for m, mod in finals.items():
        w = weights.get(m, 0.0)
        if w <= 0:
            continue
        try:
            c = mod.contributions(ds["x_today"])
        except Exception:
            continue
        for n, v in zip(ds["names"], c):
            contrib[n] += w * v
        cw += w
    if cw > 0:
        contrib = {k: v / cw for k, v in contrib.items()}
    ranked = sorted(contrib.items(), key=lambda kv: -abs(kv[1]))
    thr = 0.05 * max((abs(v) for _, v in ranked), default=0.0)
    explain = {"bullish": [{"feature": k, "label": label(k), "family": family(k), "contribution": v} for k, v in ranked if v > thr][:6],
               "bearish": [{"feature": k, "label": label(k), "family": family(k), "contribution": v} for k, v in ranked if v < -thr][:6],
               "neutral": [{"feature": k, "label": label(k), "family": family(k), "contribution": v} for k, v in ranked if abs(v) <= thr][:4]}
    from .tracking import decay
    oos = [(ds["idx"][i], p, y[i]) for i, p in zip(wf["test_rows"], wf["ens"])]
    best = board[0] if board else None
    return {"horizon": lab, "h": h, "status": "ok", "rows": n_rows, "features": ds["names"], "folds": wf["folds"], "fold_bounds": wf["fold_bounds"],
            "leaderboard": [{k: v for k, v in r.items() if k != "fold_ic"} | {"fold_ic": r["fold_ic"]} for r in board],
            "best_model": best["model"] if best and (best.get("robust") or -1) > 0 else None,
            "ensemble": {"weights": {m: (weights[m] / wsum if wsum > 0 else 0.0) for m in models}, "ic": ens_ic, "fold_ic": wf["ens_fold_ic"],
                         "positive_folds": wf["ens_positive_folds"], "rmse": wf["ens_rmse"], "sharpe": wf["ens_sharpe"], "n_oos": n_oos, "n_eff": n_eff},
            "prediction": ens_today, "expected": (math.exp(ens_today) - 1) if ens_today is not None else None, "error": wf["ens_rmse"],
            "predictions": preds_today, "prob_up": prob_up, "score": score, "quality": quality,
            "confidence": {"value": conf_v, "label": "High" if conf_v >= 0.65 else "Medium" if conf_v >= 0.4 else "Low",
                           "parts": {"out_of_sample_ic": quality, "stability": stab, "model_agreement": agree, "sample_size": sample}},
            "importance": dict(sorted(imp.items(), key=lambda kv: -kv[1])[:20]), "family_importance": _families(imp), "explain": explain,
            "decay": decay(oos, h), "oos": oos[-600:],
            "message": None if (ens_ic or 0) > 0 else "No model combination has beaten noise out of sample at this horizon; the ML score is set to zero."}


def train_asset(research, asset_id: str, progress: Optional[Callable] = None, horizons=HORIZONS) -> dict:
    t0 = time.time()
    panel = research.panel()
    price = panel.series(asset_id)
    z = research.zscores(asset_id)
    cal = panel.calendar()
    res = {"asset_id": asset_id, "version": VERSION, "trained_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "data_version": research.version(),
           "as_of": cal[max(i for i in range(len(price)) if price[i] is not None)], "horizons": {}}
    for k, (lab, h) in enumerate(horizons):
        _progress(progress, k, len(horizons), f"{lab}: building the dataset")
        ds = asset_dataset(z, price, h)
        if ds is None or len(ds["X"]) < 80:
            res["horizons"][lab] = {"horizon": lab, "h": h, "status": "insufficient", "reason": "not enough history for this horizon"}
            continue
        _progress(progress, k, len(horizons), f"{lab}: walk-forward over {len(ds['X'])} rows")
        out = train_horizon(ds, h, lab)
        res["horizons"][lab] = out
        if out.get("status") == "ok":
            fb = out["fold_bounds"]
            for r in out["leaderboard"]:
                research.store.add_model_run({"level": "asset", "key": asset_id, "horizon": lab, "model": r["model"], "version": VERSION,
                                              "train_start": cal[ds["idx"][0]], "train_end": cal[fb[-1][0]] if fb else None,
                                              "test_start": cal[fb[0][0]] if fb else None, "test_end": cal[fb[-1][1]] if fb else None,
                                              "features": ds["names"], "params": M.make_model(r["model"]).params if r["model"] in M.MODEL_NAMES else {},
                                              "metrics": {k2: r.get(k2) for k2 in ("ic", "accuracy", "rmse", "sharpe", "robust", "fold_ic_mean", "fold_ic_sd", "positive_folds")}})
            from .tracking import record
            record(research.store, panel, asset_id, res["as_of"], "ml_ensemble", VERSION, lab, h, out.get("expected"), out.get("error"),
                   out["confidence"]["value"], out.get("score"), {"weights": out["ensemble"]["weights"]})
    fam_s: Dict[str, float] = {}
    fam_l: Dict[str, float] = {}
    for lab, r in res["horizons"].items():
        for f, v in (r.get("family_importance") or {}).items():
            if lab in SHORT:
                fam_s[f] = fam_s.get(f, 0.0) + v
            if lab in LONG:
                fam_l[f] = fam_l.get(f, 0.0) + v
    res["family_importance"] = {"short": fam_s, "long": fam_l}
    ok = {k: v for k, v in res["horizons"].items() if v.get("status") == "ok"}
    res["summary"] = {"asset_id": asset_id, "horizons_trained": list(ok), "seconds": round(time.time() - t0, 1),
                      "best": {k: v.get("best_model") for k, v in ok.items()}, "scores": {k: v.get("score") for k, v in ok.items()}}
    research.store.kv_set(f"ml:{asset_id}", res)
    _progress(progress, len(horizons), len(horizons), "done")
    return res


# ------------------------------------------------------------------ pooled models (global / asset class)
POOL_HORIZONS = [("1M", 21), ("3M", 63), ("6M", 126), ("12M", 252)]


def train_pooled(research, level: str = "global", key: str = "all", progress: Optional[Callable] = None, max_rows: int = 9000) -> dict:
    """Stack assets (every asset, or one asset class) with volatility-scaled forward returns, and learn which signal
    families predict across them. Rows are month-ends; walk-forward folds are cut by date, purged by the horizon."""
    t0 = time.time()
    store = research.store
    panel = research.panel()
    assets = [a for a in store.assets(None if level == "global" else key) if store.price_count(a["id"]) > 1500]
    names = [k for k in FEATURES]
    per_h: Dict[str, List[tuple]] = {lab: [] for lab, _ in POOL_HORIZONS}
    for n_a, a in enumerate(assets):
        _progress(progress, n_a, len(assets) + len(POOL_HORIZONS), f"features for {a['id']}")
        try:
            f = compute_features(panel, a["id"])
        except Exception:
            continue
        z = standardize_all(f)
        price = panel.series(a["id"])
        vol = f.get("vol_60") or [None] * len(price)
        for lab, h in POOL_HORIZONS:
            tgt = targets(price, h)
            for t in range(300, len(price), 21):
                if tgt[t] is None or not vol[t]:
                    continue
                row = [z[k][t] if k in z else None for k in names]
                if sum(1 for v in row if v is not None) < 0.5 * len(names):
                    continue
                per_h[lab].append((t, a["id"], row, tgt[t] / (vol[t] * math.sqrt(h / 252.0))))
        del f, z
    out = {"level": level, "key": key, "assets": len(assets), "version": VERSION, "trained_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "horizons": {}}
    for k, (lab, h) in enumerate(POOL_HORIZONS):
        _progress(progress, len(assets) + k, len(assets) + len(POOL_HORIZONS), f"{lab}: walk-forward")
        rows = sorted(per_h[lab], key=lambda r: r[0])
        if len(rows) > max_rows:                              # thin evenly, keeping chronology
            stride = len(rows) / max_rows
            rows = [rows[int(i * stride)] for i in range(max_rows)]
        if len(rows) < 300:
            out["horizons"][lab] = {"status": "insufficient", "rows": len(rows)}
            continue
        ds = {"names": names, "idx": [r[0] for r in rows], "X": [r[2] for r in rows], "y": [r[3] for r in rows], "x_today": [None] * len(names),
              "last": rows[-1][0], "step": 1}
        wf = walk_forward(ds, h, ["ridge", "elastic_net", "gradient_boosting"])
        if not wf.get("folds"):
            out["horizons"][lab] = {"status": "insufficient", "rows": len(rows)}
            continue
        weights = {}
        for m in ["ridge", "elastic_net", "gradient_boosting"]:
            fic = [v for v in wf["per"][m]["fold_ic"] if v is not None]
            weights[m] = max(0.0, sum(fic) / len(fic)) if fic else 0.0
        tr, te = wf["last_fold"]
        imp = _importance(wf["last_fold_models"], weights, [ds["X"][i] for i in te], [ds["y"][i] for i in te], names)
        board = sorted(wf["board"], key=lambda r: -(r["robust"] if r["robust"] is not None else -9))
        out["horizons"][lab] = {"status": "ok", "rows": len(rows), "folds": wf["folds"], "leaderboard": board, "ensemble_ic": wf["ens_ic"],
                                "importance": dict(sorted(imp.items(), key=lambda kv: -kv[1])[:20]), "family_importance": _families(imp)}
        for r in board:
            store.add_model_run({"level": level, "key": key, "horizon": lab, "model": r["model"], "version": VERSION, "train_start": None, "train_end": None,
                                 "test_start": None, "test_end": None, "features": names, "params": {}, "metrics": {k2: r.get(k2) for k2 in ("ic", "accuracy", "robust", "sharpe")}})
    out["summary"] = {"level": level, "key": key, "assets": len(assets), "seconds": round(time.time() - t0, 1),
                      "ic": {lab: (v.get("ensemble_ic")) for lab, v in out["horizons"].items()}}
    store.kv_set(f"mlpool:{level}:{key}", out)
    _progress(progress, 1, 1, "done")
    return out
