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

VERSION = "fs2-ml-2"
HOLDOUT_FRAC = 0.15          # the last 15% of rows: untouched by model selection and ensemble weights, scored once
DD_THRESHOLD = {5: 0.05, 21: 0.075, 63: 0.10, 126: 0.15, 252: 0.20}   # drawdown-probability targets by horizon
EDGE_MARGIN = 0.01           # the ensemble's IC must beat the best baseline's by at least this much
EDGE_T = 2.0                 # and be significant on its effective sample
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
def _expected_max_normal(h: int) -> float:
    """E[max of h iid standard normals] (Gumbel approximation; exact small-h values): the vol-scaled baseline for the
    worst daily loss over h sessions."""
    exact = {1: 0.0, 2: 0.5642, 3: 0.8463, 4: 1.0294, 5: 1.1630}
    if h in exact:
        return exact[h]
    a = math.sqrt(2 * math.log(h))
    return a - (math.log(math.log(h)) + math.log(4 * math.pi)) / (2 * a) + 0.5772 / a


def asset_dataset(z: Dict[str, list], price: list, h: int, names: Optional[List[str]] = None, market: Optional[list] = None):
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
    # baseline inputs, all known at t: the previous h-session return, 12-1 momentum, minus the 20-day z-score
    prev = [(math.log(price[t] / price[t - h]) if t - h >= 0 and price[t] and price[t - h] else None) for t in idx]
    mom = [(z.get("mom_12_1") or [None] * n)[t] for t in idx]
    mr = [(-(z.get("z_20") or [None] * n)[t]) if (z.get("z_20") or [None] * n)[t] is not None else None for t in idx]
    # other targets: realised volatility over the next h sessions, and whether the drawdown inside the window exceeds X
    lr = [None] + [math.log(price[i] / price[i - 1]) if price[i] and price[i - 1] else None for i in range(1, n)]
    vol_t, dd_t, vol_now, ewma_now = [], [], [], []
    thr = DD_THRESHOLD.get(h)
    ew, ew_at = None, {}
    for k in range(n):                                   # EWMA variance (λ = 0.94) of returns up to and including k
        if lr[k] is not None:
            ew = lr[k] * lr[k] if ew is None else 0.94 * ew + 0.06 * lr[k] * lr[k]
        ew_at[k] = ew
    for t in idx:
        past = [lr[k] for k in range(max(1, t - 19), t + 1) if lr[k] is not None]
        vol_now.append(math.sqrt(252.0 / len(past) * sum(r * r for r in past)) if len(past) >= 10 else None)
        ewma_now.append(math.sqrt(252.0 * ew_at[t]) if ew_at.get(t) is not None else None)
        rs = [lr[k] for k in range(t + 1, min(n, t + h + 1)) if lr[k] is not None]
        vol_t.append(math.sqrt(252.0 / max(1, len(rs)) * sum(r * r for r in rs)) if len(rs) >= max(3, h // 2) else None)
        if thr is not None and price[t]:
            peak, worst = price[t], 0.0
            for k in range(t + 1, min(n, t + h + 1)):
                if price[k]:
                    peak = max(peak, price[k])
                    worst = min(worst, price[k] / peak - 1)
            dd_t.append(1.0 if worst <= -thr else 0.0)
        else:
            dd_t.append(None)
    # tail loss: the worst daily log return inside (t, t+h] (as a positive loss); baselines known at t: the same
    # statistic over the previous h sessions, and the EWMA daily vol × E[max of h normals]
    tail_t, tail_prev, tail_vol = [], [], []
    emax = _expected_max_normal(h)
    for t in idx:
        fut = [lr[k] for k in range(t + 1, min(n, t + h + 1)) if lr[k] is not None]
        tail_t.append(-min(fut) if len(fut) >= max(3, h // 2) else None)
        past = [lr[k] for k in range(max(1, t - h + 1), t + 1) if lr[k] is not None]
        tail_prev.append(-min(past) if len(past) >= max(3, h // 2) else None)
        tail_vol.append(math.sqrt(ew_at[t]) * emax if ew_at.get(t) is not None else None)
    # beta change: realised beta to the market over (t, t+h] minus the trailing 252-session beta at t
    beta_t = [None] * len(idx)
    beta_now = [None] * len(idx)
    if market is not None and h >= 21:
        mr_ = [None] + [math.log(market[i] / market[i - 1]) if market[i] and market[i - 1] else None for i in range(1, n)]

        def beta(lo, hi):
            pr = [(lr[k], mr_[k]) for k in range(lo, hi) if lr[k] is not None and mr_[k] is not None]
            if len(pr) < 15:
                return None
            mx = sum(b for _, b in pr) / len(pr)
            my = sum(a for a, _ in pr) / len(pr)
            vx = sum((b - mx) ** 2 for _, b in pr)
            return sum((a - my) * (b - mx) for a, b in pr) / vx if vx > 0 else None
        for j, t in enumerate(idx):
            b0 = beta(max(1, t - 251), t + 1)
            b1 = beta(t + 1, min(n, t + h + 1))
            beta_now[j] = b0
            beta_t[j] = (b1 - b0) if b0 is not None and b1 is not None else None
    return {"names": keep, "idx": idx, "X": X, "y": y, "x_today": x_today, "last": last, "step": step,
            "prev": prev, "mom": mom, "mr": mr, "vol_target": vol_t, "dd_target": dd_t, "dd_threshold": thr,
            "vol_now": vol_now, "ewma_now": ewma_now, "tail_target": tail_t, "tail_prev": tail_prev, "tail_vol": tail_vol,
            "beta_target": beta_t, "beta_now": beta_now}


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


# ------------------------------------------------------------------ evaluation helpers
def _t_of(ic: Optional[float], n_eff: float) -> Optional[float]:
    if ic is None or n_eff <= 3:
        return None
    return ic * math.sqrt(n_eff - 2) / math.sqrt(max(1e-12, 1 - ic * ic))


def _reg_metrics(pred: List[Optional[float]], y: List[float], h: int, step: int) -> dict:
    pairs = [(p, v) for p, v in zip(pred, y) if p is not None and v is not None]
    if len(pairs) < 10:
        return {"n": len(pairs)}
    p, v = [a for a, _ in pairs], [b for _, b in pairs]
    ic = M.spearman(p, v) if len(set(p)) > 1 else None
    lean = [(a, b) for a, b in pairs if a != 0]
    n_eff = len(pairs) * step / max(h, step)
    return {"n": len(pairs), "n_eff": n_eff, "ic": ic, "t": _t_of(ic, n_eff), "pearson": M.pearson(p, v) if len(set(p)) > 1 else None,
            "hit": (sum(1 for a, b in lean if (a > 0) == (b > 0)) / len(lean)) if lean else None,
            "rmse": math.sqrt(sum((a - b) ** 2 for a, b in pairs) / len(pairs)), "mae": sum(abs(a - b) for a, b in pairs) / len(pairs),
            "sharpe": _sharpe_of(p, v, h, step)}


def _risk_compare(pred: List[float], y: List[float], baselines: Dict[str, List[Optional[float]]], h: int, step: int) -> dict:
    """A risk model against its baselines on exactly the same out-of-sample rows (every baseline available): RMSE
    and IC of each; verified = lower RMSE than every baseline; stable = lower in BOTH halves of the rows (time order)."""
    keep = [j for j in range(len(y)) if pred[j] is not None and y[j] is not None and all(b[j] is not None for b in baselines.values())]
    if len(keep) < 20:
        return {"n": len(keep), "verified": False, "stable": False}
    sub = lambda xs, ks: [xs[j] for j in ks]
    out = {"model": _reg_metrics(sub(pred, keep), sub(y, keep), h, step)}
    for k, b in baselines.items():
        out["baseline_" + k] = _reg_metrics(sub(b, keep), sub(y, keep), h, step)
    rm = out["model"].get("rmse")
    bs = [out["baseline_" + k].get("rmse") for k in baselines]
    out["verified"] = bool(rm is not None and all(v is not None and rm < v for v in bs))
    half = len(keep) // 2
    stable = True
    for part in (keep[:half], keep[half:]):
        e = lambda xs: math.sqrt(sum((xs[j] - y[j]) ** 2 for j in part) / len(part))
        stable &= all(e(pred) < e(b) for b in baselines.values())
    out["stable"] = out["verified"] and stable
    best = min(bs) if bs and all(v is not None for v in bs) else None
    out["rmse_improvement"] = (1 - rm / best) if rm is not None and best else None
    return out


def _cls_metrics(prob: List[float], yb: List[float], base: Optional[List[float]] = None) -> dict:
    pairs = [(p, v) for p, v in zip(prob, yb) if p is not None and v is not None]
    if len(pairs) < 20:
        return {"n": len(pairs)}
    p, v = [a for a, _ in pairs], [b for _, b in pairs]
    pos = sum(v)
    neg = len(v) - pos
    tp = sum(1 for a, b in pairs if a >= 0.5 and b == 1)
    fp = sum(1 for a, b in pairs if a >= 0.5 and b == 0)
    fn = sum(1 for a, b in pairs if a < 0.5 and b == 1)
    tn = len(pairs) - tp - fp - fn
    prec = tp / (tp + fp) if tp + fp else None
    rec = tp / (tp + fn) if tp + fn else None
    auc = None
    if pos and neg:                                   # Mann-Whitney
        r = M.rank(p)
        auc = (sum(rk for rk, b in zip(r, v) if b == 1) - pos * (pos + 1) / 2) / (pos * neg)
    out = {"n": len(pairs), "base_rate": pos / len(v), "accuracy": (tp + tn) / len(pairs),
           "balanced_accuracy": 0.5 * ((tp / pos if pos else 0) + (tn / neg if neg else 0)), "precision": prec, "recall": rec,
           "f1": (2 * prec * rec / (prec + rec)) if prec and rec else None, "auc": auc,
           "brier": sum((a - b) ** 2 for a, b in pairs) / len(pairs)}
    bins = []
    for lo in (0.0, 0.2, 0.4, 0.6, 0.8):
        sub = [(a, b) for a, b in pairs if lo <= a < lo + 0.2 or (lo == 0.8 and a == 1.0)]
        if sub:
            bins.append({"lo": lo, "hi": lo + 0.2, "n": len(sub), "mean_prob": sum(a for a, _ in sub) / len(sub), "freq": sum(b for _, b in sub) / len(sub)})
    out["reliability"] = bins
    if base is not None:
        bp = [(a, b) for a, b in zip(base, yb) if a is not None and b is not None]
        if bp:
            out["baseline_brier"] = sum((a - b) ** 2 for a, b in bp) / len(bp)
    return out


def _expanding_mean(ds: dict, rows: List[int], h: int, series: List[Optional[float]]) -> List[Optional[float]]:
    """For each row, the mean of `series` over earlier rows whose outcome was already known (idx + h < row idx)."""
    idx = ds["idx"]
    out, s, n, k = [], 0.0, 0, 0
    order = sorted(range(len(idx)), key=lambda i: idx[i])
    for r in rows:
        while k < len(order) and idx[order[k]] + h < idx[r]:
            v = series[order[k]]
            if v is not None:
                s += v; n += 1
            k += 1
        out.append(s / n if n >= 10 else None)
    return out


def _perm_pvalue(pred: List[float], y: List[float], block: int, reps: int = 200, seed: int = 7) -> Optional[float]:
    """Random-signal baseline: how often a block-shuffled copy of the predictions (autocorrelation kept) scores at
    least the same rank IC."""
    import random
    ic = M.spearman(pred, y)
    if ic is None or len(pred) < 30:
        return None
    rnd = random.Random(seed)
    blocks = [pred[i:i + block] for i in range(0, len(pred), max(1, block))]
    hits = 0
    for _ in range(reps):
        rnd.shuffle(blocks)
        sh = [x for b in blocks for x in b]
        v = M.spearman(sh, y)
        if v is not None and v >= ic:
            hits += 1
    return (hits + 1) / (reps + 1)


def _split(ds: dict, h: int) -> Tuple[int, List[int]]:
    """Development rows [0, H0) and the untouched holdout; training rows for the holdout are purged by h."""
    N = len(ds["idx"])
    H0 = int(N * (1 - HOLDOUT_FRAC))
    return H0, list(range(H0, N))


def _sub(ds: dict, rows: List[int]) -> dict:
    return {**ds, "X": [ds["X"][i] for i in rows], "y": [ds["y"][i] for i in rows], "idx": [ds["idx"][i] for i in rows]}


def _simple_wf(X, target, idx, h, dev_n, names_models, classifier=False):
    """Walk-forward (purged, expanding) predictions of `target` on the development test blocks plus the holdout,
    for the volatility and drawdown problems. Returns (rows, preds, final models)."""
    rows_ok = [i for i in range(len(idx)) if target[i] is not None]
    if len(rows_ok) < 120:
        return [], [], {}
    dev = [i for i in rows_ok if i < dev_n]
    hold = [i for i in rows_ok if i >= dev_n]
    folds = _folds([idx[i] for i in dev], h)
    out_rows, out_pred = [], []
    for tr_pos, te_pos in folds + ([] if not hold else [(None, None)]):
        if tr_pos is None:
            te = hold
            tr = [i for i in dev if idx[i] + h < idx[hold[0]]]
        else:
            tr = [dev[k] for k in tr_pos]
            te = [dev[k] for k in te_pos]
        if len(tr) < 40 or not te:
            continue
        preds = []
        for m in names_models:
            try:
                mod = M.make_model(m) if not classifier else (M.make_model(m) if m == "logistic" else M.make_model(m, loss="logistic"))
                mod.fit([X[i] for i in tr], [target[i] for i in tr])
                preds.append(mod.predict_proba([X[i] for i in te]) if classifier else mod.predict([X[i] for i in te]))
            except Exception:
                continue
        if not preds:
            continue
        out_rows += te
        out_pred += [sum(p[j] for p in preds) / len(preds) for j in range(len(te))]
    finals = {}
    for m in names_models:
        try:
            mod = M.make_model(m) if not classifier else (M.make_model(m) if m == "logistic" else M.make_model(m, loss="logistic"))
            mod.fit([X[i] for i in rows_ok], [target[i] for i in rows_ok])
            finals[m] = mod
        except Exception:
            pass
    return out_rows, out_pred, finals


def train_horizon(ds: dict, h: int, lab: str, progress=None, shaffer: Optional[List[Optional[float]]] = None) -> dict:
    """Return model (ensemble), baselines, verified-edge test, holdout confirmation, and the separate direction,
    volatility and drawdown problems for one asset and horizon."""
    X, y, step = ds["X"], ds["y"], ds["step"]
    n_rows = len(X)
    use_trees = h <= 252 and n_rows >= 300
    models = [m for m in REG_MODELS if use_trees or m not in TREE_MODELS]
    H0, hold = _split(ds, h)
    dev = _sub(ds, list(range(H0)))
    wf = walk_forward(dev, h, models, progress)
    if not wf.get("folds"):
        return {"horizon": lab, "status": "insufficient", "reason": f"only {n_rows} usable rows", "rows": n_rows}
    board = sorted(wf["board"], key=lambda r: -(r["robust"] if r["robust"] is not None else -9))
    weights = {}
    for m in models:
        fic = [v for v in wf["per"][m]["fold_ic"] if v is not None]
        weights[m] = max(0.0, sum(fic) / len(fic)) if fic else 0.0
    wsum = sum(weights.values())
    # holdout: models trained on development rows whose outcome was known before the holdout, weighted as chosen on dev
    hold_pred = []
    if hold and wsum > 0:
        tr = [i for i in range(H0) if ds["idx"][i] + h < ds["idx"][hold[0]]]
        parts = {}
        for m in models:
            if weights[m] <= 0:
                continue
            try:
                _, p = _fit_predict(m, [X[i] for i in tr], [y[i] for i in tr], [X[i] for i in hold], len(tr))
                parts[m] = p
            except Exception:
                pass
        tot = sum(weights[m] for m in parts)
        if tot > 0:
            hold_pred = [sum(weights[m] * parts[m][j] for m in parts) / tot for j in range(len(hold))]
    # final models on every row (today's forecast may use everything known today)
    finals, preds_today = {}, {}
    for m in models:
        if weights[m] <= 0 and m not in ("ridge", "gradient_boosting"):
            continue
        try:
            mod, p = _fit_predict(m, X, y, [ds["x_today"]], n_rows)
            finals[m] = mod
            preds_today[m] = p[0]
        except Exception:
            pass
    live_w = {m: weights[m] for m in preds_today if weights[m] > 0}
    ens_today = (sum(w * preds_today[m] for m, w in live_w.items()) / sum(live_w.values())) if live_w else None
    # ---------- evaluation on genuinely out-of-sample rows: development test blocks and the holdout
    dev_rows = wf["test_rows"]
    oos_rows = dev_rows + (hold if hold_pred else [])
    oos_pred = wf["ens"] + hold_pred
    ys = [y[i] for i in oos_rows]
    ens_dev = _reg_metrics(wf["ens"], [y[i] for i in dev_rows], h, step)
    ens_hold = _reg_metrics(hold_pred, [y[i] for i in hold], h, step) if hold_pred else {"n": 0}
    ens_all = _reg_metrics(oos_pred, ys, h, step)
    hist_mean = _expanding_mean(ds, oos_rows, h, y)
    base_preds = {"zero": [0.0] * len(oos_rows), "historical_mean": hist_mean, "previous_return": [ds["prev"][i] for i in oos_rows],
                  "momentum": [ds["mom"][i] for i in oos_rows], "mean_reversion": [ds["mr"][i] for i in oos_rows]}
    if shaffer is not None:
        base_preds["shaffer"] = [shaffer[i] for i in oos_rows]
    baselines = {k: _reg_metrics(v, ys, h, step) for k, v in base_preds.items()}
    for k, v in baselines.items():                           # the ensemble on exactly the same rows, for a fair comparison
        rows_k = [j for j, pv in enumerate(base_preds[k]) if pv is not None]
        v["ensemble_ic_same_rows"] = M.spearman([oos_pred[j] for j in rows_k], [ys[j] for j in rows_k]) if len(rows_k) >= 10 else None
    always_long = {"hit": sum(1 for v in ys if v > 0) / len(ys) if ys else None, "sharpe": _sharpe_of([1.0] * len(ys), ys, h, step)}
    hm = [(p, v) for p, v in zip(hist_mean, ys) if p is not None]
    r2 = None
    if hm:
        sse_m = sum((a - b) ** 2 for a, b in hm)
        sse_e = sum((oos_pred[j] - ys[j]) ** 2 for j in range(len(ys)) if hist_mean[j] is not None)
        r2 = 1 - sse_e / sse_m if sse_m > 0 else None
    perm_p = _perm_pvalue(oos_pred, ys, max(1, h // step))
    # ---------- the verified-edge rule
    if (ens_all.get("n_eff") or 0) < 10:
        return {"horizon": lab, "h": h, "status": "insufficient", "rows": n_rows,
                "reason": f"about {ens_all.get('n_eff') or 0:.0f} independent out-of-sample observations: too few to judge any model",
                "baselines": baselines, "message": "NO VERIFIED ML EDGE: too few independent observations at this horizon."}
    rivals = {k: v for k, v in baselines.items() if k not in ("zero", "historical_mean")}
    beaten = {k: (v.get("ensemble_ic_same_rows") or -1) > (v.get("ic") if v.get("ic") is not None else -1) + EDGE_MARGIN for k, v in rivals.items() if v.get("n", 0) >= 10}
    reasons = []
    if (ens_all.get("t") or 0) < EDGE_T:
        reasons.append(f"out-of-sample IC {fmt(ens_all.get('ic'))} is not significant (t {fmt(ens_all.get('t'), 1)} < {EDGE_T:g})")
    lost = [k for k, ok in beaten.items() if not ok]
    if lost:
        reasons.append("does not beat " + ", ".join(k.replace("_", " ") for k in lost))
    if ens_hold.get("n", 0) >= 10 and (ens_hold.get("ic") or 0) <= 0:
        reasons.append(f"the untouched holdout disagrees (IC {fmt(ens_hold.get('ic'))})")
    if perm_p is not None and perm_p >= 0.05:
        reasons.append(f"a shuffled copy does as well {perm_p:.0%} of the time")
    verified = not reasons
    quality = min(1.0, max(0.0, ens_all.get("ic") or 0.0) / 0.10)
    score = 0.0
    if verified and ens_today is not None and oos_pred:
        score = (2 * _pct_rank(oos_pred, ens_today) - 1) * 100 * quality
    agree = None
    if ens_today is not None and live_w:
        agree = sum(1 for m in live_w if (preds_today[m] > 0) == (ens_today > 0)) / len(live_w)
    n_eff = ens_all.get("n_eff") or 1.0
    sample = min(1.0, math.log10(max(1.0, n_eff)) / 2.0)
    stab = wf["ens_positive_folds"] or 0.0
    conf_v = sample * (0.45 * quality + 0.3 * stab + 0.25 * (agree if agree is not None else 0.5)) if verified else 0.0
    # ---------- direction: probability of a rise (logistic walk-forward, calibration and Brier against the base rate)
    ybin = [1.0 if v > 0 else 0.0 for v in y]
    lg_rows = dev_rows
    lg_prob = wf["logit"]["preds"]
    if hold:
        tr = [i for i in range(H0) if ds["idx"][i] + h < ds["idx"][hold[0]]]
        try:
            lg = M.make_model("logistic"); lg.fit([X[i] for i in tr], [ybin[i] for i in tr])
            lg_rows = dev_rows + hold
            lg_prob = lg_prob + lg.predict_proba([X[i] for i in hold])
        except Exception:
            pass
    base_rate = _expanding_mean(ds, lg_rows, h, ybin)
    direction = _cls_metrics(lg_prob, [ybin[i] for i in lg_rows], base_rate)
    try:
        lg = M.make_model("logistic"); lg.fit(X, ybin)
        prob_up = lg.predict_proba([ds["x_today"]])[0]
        lg_final = lg
    except Exception:
        prob_up, lg_final = None, None
    direction["verified"] = bool(direction.get("auc") and direction["auc"] > 0.5 and direction.get("baseline_brier") is not None
                                 and direction["brier"] < direction["baseline_brier"])
    # ---------- volatility (future realised) and drawdown probability
    vol_out, dd_out, vol_final, dd_final = None, None, {}, {}
    if 5 <= h <= 252:
        vt = ds["vol_target"]
        rows_v, pv, vol_final = _simple_wf(X, vt, ds["idx"], h, H0, ["ridge"] + (["gradient_boosting"] if use_trees else []))
        if rows_v:
            vol_out = _risk_compare(pv, [vt[i] for i in rows_v], {"current_vol": [ds["vol_now"][i] for i in rows_v],
                                                                   "ewma": [ds["ewma_now"][i] for i in rows_v]}, h, step)
        dt = ds["dd_target"]
        rows_d, pd, dd_final = _simple_wf(X, dt, ds["idx"], h, H0, ["logistic"], classifier=True)
        if rows_d:
            freq = _expanding_mean(ds, rows_d, h, dt)
            dd_out = _cls_metrics(pd, [dt[i] for i in rows_d], freq)
            dd_out["threshold"] = ds["dd_threshold"]
            dd_out["verified"] = bool(dd_out.get("baseline_brier") is not None and dd_out.get("brier") is not None and dd_out["brier"] < dd_out["baseline_brier"])
            ok = [j for j in range(len(rows_d)) if freq[j] is not None]
            half = len(ok) // 2
            yd = [dt[i] for i in rows_d]
            br = lambda ps, part: sum((ps[j] - yd[j]) ** 2 for j in part) / max(1, len(part))
            dd_out["stable"] = bool(dd_out["verified"] and half >= 10 and all(br(pd, part) < br(freq, part) for part in (ok[:half], ok[half:])))
    # ---------- tail loss (worst day in the window) and beta change: separate risk problems, each against baselines
    tail_out, beta_out, tail_final, beta_final = None, None, {}, {}
    if 5 <= h <= 252 and ds.get("tail_target"):
        tt = ds["tail_target"]
        rows_t, pt, tail_final = _simple_wf(X, tt, ds["idx"], h, H0, ["ridge"] + (["gradient_boosting"] if use_trees else []))
        if rows_t:
            tail_out = _risk_compare(pt, [tt[i] for i in rows_t], {"previous_window": [ds["tail_prev"][i] for i in rows_t],
                                                                    "vol_scaled": [ds["tail_vol"][i] for i in rows_t]}, h, step)
        bt = ds.get("beta_target") or []
        if any(v is not None for v in bt):
            rows_b, pb, beta_final = _simple_wf(X, bt, ds["idx"], h, H0, ["ridge"])
            if rows_b:
                # Blume (1971): betas regress about a third of the way to 1 — the textbook forecast a model has to beat
                bn = ds.get("beta_now") or [None] * len(bt)
                beta_out = _risk_compare(pb, [bt[i] for i in rows_b], {"no_change": [0.0] * len(rows_b),
                                                                        "mean_change": _expanding_mean(ds, rows_b, h, bt),
                                                                        "blume": [(1 - bn[i]) / 3 if bn[i] is not None else None for i in rows_b]}, h, step)
    today_vol = today_dd = None
    if vol_final:
        try:
            today_vol = sum(m.predict([ds["x_today"]])[0] for m in vol_final.values()) / len(vol_final)
        except Exception:
            pass
    if dd_final:
        try:
            today_dd = sum(m.predict_proba([ds["x_today"]])[0] for m in dd_final.values()) / len(dd_final)
        except Exception:
            pass
    # ---------- importance and explanation (as before; importance on the last development block)
    tr, te = wf["last_fold"]
    dX, dy = dev["X"], dev["y"]
    imp = _importance(wf["last_fold_models"], weights, [dX[i] for i in te], [dy[i] for i in te], ds["names"])
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
        for nme, v in zip(ds["names"], c):
            contrib[nme] += w * v
        cw += w
    if cw > 0:
        contrib = {k: v / cw for k, v in contrib.items()}
    ranked = sorted(contrib.items(), key=lambda kv: -abs(kv[1]))
    thr = 0.05 * max((abs(v) for _, v in ranked), default=0.0)
    mk = lambda k, v: {"feature": k, "label": label(k), "family": family(k), "contribution": v}
    explain = {"bullish": [mk(k, v) for k, v in ranked if v > thr][:6], "bearish": [mk(k, v) for k, v in ranked if v < -thr][:6],
               "neutral": [mk(k, v) for k, v in ranked if abs(v) <= thr][:4]}
    from .tracking import decay
    oos = [(ds["idx"][i], p, y[i]) for i, p in zip(oos_rows, oos_pred)]
    best = board[0] if board else None
    state = {"names": ds["names"], "weights": {m: w for m, w in live_w.items()}, "models": {m: M.get_state(finals[m]) for m in live_w if m in finals},
             "logit": M.get_state(lg_final) if lg_final else None, "vol": {m: M.get_state(v) for m, v in vol_final.items()},
             "dd": {m: M.get_state(v) for m, v in dd_final.items()}, "oos_pred": oos_pred[-600:], "verified": verified, "quality": quality}
    return {"horizon": lab, "h": h, "status": "ok", "rows": n_rows, "features": ds["names"], "folds": wf["folds"], "fold_bounds": wf["fold_bounds"],
            "dev_rows": H0, "holdout_rows": len(hold),
            "leaderboard": [{k: v for k, v in r.items() if k != "fold_ic"} | {"fold_ic": r["fold_ic"]} for r in board],
            "best_model": best["model"] if best and (best.get("robust") or -1) > 0 else None,
            "ensemble": {"weights": {m: (weights[m] / wsum if wsum > 0 else 0.0) for m in models}, "ic": ens_all.get("ic"), "fold_ic": wf["ens_fold_ic"],
                         "positive_folds": wf["ens_positive_folds"], "rmse": ens_all.get("rmse"), "sharpe": ens_all.get("sharpe"),
                         "n_oos": len(oos_rows), "n_eff": n_eff, "t": ens_all.get("t"), "r2_vs_mean": r2, "permutation_p": perm_p,
                         "development": ens_dev, "holdout": ens_hold},
            "baselines": baselines, "always_long": always_long, "verified": verified, "edge_reasons": reasons,
            "prediction": ens_today, "expected": (math.exp(ens_today) - 1) if ens_today is not None else None, "error": ens_all.get("rmse"),
            "predictions": preds_today, "prob_up": prob_up, "direction": direction, "volatility": vol_out, "drawdown": dd_out,
            "tail_loss": tail_out, "beta_change": beta_out,
            "vol_forecast": today_vol, "dd_probability": today_dd, "score": score, "quality": quality,
            "confidence": {"value": conf_v, "label": "High" if conf_v >= 0.65 else "Medium" if conf_v >= 0.4 else "Low",
                           "parts": {"out_of_sample_ic": quality, "stability": stab, "model_agreement": agree, "sample_size": sample}},
            "importance": dict(sorted(imp.items(), key=lambda kv: -kv[1])[:20]), "family_importance": _families(imp), "explain": explain,
            "decay": decay(oos, h), "oos": oos[-600:], "_state": state,
            "message": None if verified else "NO VERIFIED ML EDGE: " + "; ".join(reasons) + ". The ML score is set to zero."}


def _params_used(name: str, n_rows: int) -> dict:
    """The parameters `_fit_predict` actually uses (defaults plus its row-count overrides)."""
    kw = {}
    if name == "random_forest":
        kw = {"n_estimators": 30 if n_rows > 1500 else 40}
    if name == "gradient_boosting":
        kw = {"n_estimators": 80}
    return dict(M.make_model(name, **kw).params) if name in M.MODEL_NAMES else {}


def forecast_today(research, asset_id: str) -> Optional[dict]:
    """Daily forecasts from the saved models (no retraining): each horizon's ensemble prediction, its ML score (0 without
    a verified edge), P(rise), future volatility and drawdown probability, recorded in the prediction ledger."""
    saved = research.store.kv_get(f"mlmodels:{asset_id}")
    if not saved:
        return None
    panel = research.panel()
    z = research.zscores(asset_id)
    price = panel.series(asset_id)
    cal = panel.calendar()
    last = max(i for i in range(len(price)) if price[i] is not None)
    out = {"as_of": cal[last], "horizons": {}}
    from .tracking import record
    for lab, st in (saved.get("horizons") or {}).items():
        x = [next((v for v in reversed(z.get(k, [None])[max(0, last - 5):last + 1]) if v is not None), None) for k in st["names"]]
        try:
            preds = {m: M.from_state(s).predict([x])[0] for m, s in st["models"].items()}
        except Exception:
            continue
        w = {m: st["weights"].get(m, 0.0) for m in preds}
        if not preds or sum(w.values()) <= 0:
            continue
        ens = sum(w[m] * preds[m] for m in preds) / sum(w.values())
        score = (2 * _pct_rank(st["oos_pred"], ens) - 1) * 100 * st["quality"] if st.get("verified") else 0.0
        prob = M.from_state(st["logit"]).predict_proba([x])[0] if st.get("logit") else None
        vol = (sum(M.from_state(s).predict([x])[0] for s in st["vol"].values()) / len(st["vol"])) if st.get("vol") else None
        dd = (sum(M.from_state(s).predict_proba([x])[0] for s in st["dd"].values()) / len(st["dd"])) if st.get("dd") else None
        h = dict(HORIZONS)[lab]
        out["horizons"][lab] = {"prediction": ens, "expected": math.exp(ens) - 1, "score": score, "prob_up": prob, "vol_forecast": vol, "dd_probability": dd,
                                "verified": st.get("verified")}
        record(research.store, panel, asset_id, cal[last], "ml_ensemble", saved.get("version", VERSION), lab, h, math.exp(ens) - 1, None, None, score,
               {"verified": st.get("verified"), "prob_up": prob, "vol_forecast": vol, "dd_probability": dd, "from_saved_models": saved.get("trained_at")},
               source="live", raw=score)
        from .tracking import record_risk
        record_risk(research.store, panel, asset_id, cal[last], saved.get("version", VERSION), lab, h, vol, dd, DD_THRESHOLD.get(h))
    return out


def fmt(x, d=3):
    return "—" if x is None else f"{x:.{d}f}"


def train_asset(research, asset_id: str, progress: Optional[Callable] = None, horizons=HORIZONS) -> dict:
    t0 = time.time()
    panel = research.panel()
    price = panel.series(asset_id)
    z = research.zscores(asset_id)
    cal = panel.calendar()
    res = {"asset_id": asset_id, "version": VERSION, "trained_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "data_version": research.version(),
           "as_of": cal[max(i for i in range(len(price)) if price[i] is not None)], "horizons": {}}
    states: Dict[str, dict] = {}
    for k, (lab, h) in enumerate(horizons):
        _progress(progress, k, len(horizons), f"{lab}: building the dataset")
        ds = asset_dataset(z, price, h, market=panel.series(panel.benchmark) if asset_id != panel.benchmark else None)
        if ds is None or len(ds["X"]) < 80:
            res["horizons"][lab] = {"horizon": lab, "h": h, "status": "insufficient", "reason": "not enough history for this horizon"}
            continue
        _progress(progress, k, len(horizons), f"{lab}: walk-forward over {len(ds['X'])} rows")
        try:                                                     # the point-in-time Shaffer Score as a baseline to beat
            ss = research.shaffer_series(asset_id, lab)
            ss_rows = [ss[t] for t in ds["idx"]]
        except Exception:
            ss_rows = None
        out = train_horizon(ds, h, lab, shaffer=ss_rows)
        state = out.pop("_state", None)
        if state is not None:
            states[lab] = state
        res["horizons"][lab] = out
        if out.get("status") == "ok":
            fb = out["fold_bounds"]
            H0 = out["dev_rows"]
            import platform
            for r in out["leaderboard"]:
                params = dict((state or {}).get("models", {}).get(r["model"], {}).get("params") or _params_used(r["model"], len(ds["X"])))
                research.store.add_model_run({"level": "asset", "key": asset_id, "horizon": lab, "model": r["model"], "version": VERSION,
                                              "train_start": cal[ds["idx"][0]], "train_end": cal[fb[-1][0]] if fb else None,
                                              "test_start": cal[fb[0][0]] if fb else None, "test_end": cal[fb[-1][1]] if fb else None,
                                              "features": ds["names"], "params": params,
                                              "metrics": {**{k2: r.get(k2) for k2 in ("ic", "accuracy", "rmse", "sharpe", "robust", "fold_ic_mean", "fold_ic_sd", "positive_folds")},
                                                          "data_version": research.version(), "dataset_cutoff": res["as_of"], "seed": params.get("seed"),
                                                          "development": [cal[ds["idx"][0]], cal[ds["idx"][H0 - 1]]] if H0 else None,
                                                          "holdout": [cal[ds["idx"][H0]], cal[ds["idx"][-1]]] if H0 < len(ds["idx"]) else None,
                                                          "software": f"finsim2 {VERSION}; python {platform.python_version()}"}})
            from .tracking import record
            record(research.store, panel, asset_id, res["as_of"], "ml_ensemble", VERSION, lab, h, out.get("expected"), out.get("error"),
                   out["confidence"]["value"], out.get("score"),
                   {"weights": out["ensemble"]["weights"], "verified": out.get("verified"), "prob_up": out.get("prob_up"),
                    "vol_forecast": out.get("vol_forecast"), "dd_probability": out.get("dd_probability")}, source="live", raw=out.get("score"))
            from .tracking import record_risk
            record_risk(research.store, panel, asset_id, res["as_of"], VERSION, lab, h, out.get("vol_forecast"), out.get("dd_probability"), DD_THRESHOLD.get(h))
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
    research.store.kv_set(f"mlmodels:{asset_id}", {"version": VERSION, "trained_at": res["trained_at"], "data_version": res["data_version"], "horizons": states})
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
