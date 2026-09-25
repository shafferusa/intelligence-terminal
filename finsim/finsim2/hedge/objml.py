"""Objective-specific hedge ML: one capped sizing model per hedge objective, judged on that objective.

The variance-only ML layer (history.ml_layer) learns one multiple of the raw hedge and judges it on variance. A tail
hedge, a drawdown hedge or a DV01 hedge is not trying to minimise variance, so here each objective gets its own
target, its own model and its own out-of-sample test:

    metric (lower is better, per window unless pooled)          used for the hedge objectives
    variance    Σ (u + m·h)²                                    min_variance, target_vol, systematic, name, volatility
    exposure    (β of the hedged daily P&L on the factor)²      beta, neutral, sector, duration (DV01), curve, credit
                — the residual beta / DV01 / CS01 / FX exposure  (CS01), fx, commodity, crypto
    downside    Σ min(0, u + m·h)²  (daily semivariance)         —  (reported: the daily tail proxy)
    drawdown    |max drawdown of the hedged cumulative P&L|      drawdown
    var95       VaR 95% of the window P&L, pooled over windows  var
    es95        ES 95% of the window P&L, pooled over windows   es, crash

For every walk-forward window the hedge P&L path is stored, so any multiple m of the sized hedge can be re-scored
exactly (option P&L is linear in the contract count). The model's target is the ex-post best capped adjustment
a* ∈ [−cap, cap] (m = 1 + α·a*) for that metric; var95/es95 share a model whose per-window target is the window's
downside min(0, U + m·H)². A ridge model on conditions at the window start, trained only on windows that ended
before the prediction (expanding, purged), predicts a; the prediction is clipped to the cap.

Out of sample: the metric at the predicted multiple against the static rule (m = 1), a CONSTANT resizing (the mean of
the past ex-post best adjustments — so a model that only rediscovers a fixed sizing bias is not counted as skill) and,
for linear hedges, the trailing minimum-variance multiple, all on the same windows. Per-window metrics: paired t ≥ 2 with n_eff ≥ 30; pooled VaR/ES: a block bootstrap over
windows, one-sided p ≤ 0.025. Both baselines must be beaten, and the most recent third must not be worse than the
static rule. Anything else: NO VERIFIED EDGE and the adjustment stays 0. When a constant resizing beats the static rule
but the model does not beat the constant, the finding is a SIZING BIAS of the static rule, reported as such — it is
not applied automatically.

Features (all known at the window start): the original six (VIX, 1-year and 3-month correlation, beta change, 3-month
market return, bill rate) and the richer set — correlation instability, basis risk, factor vol (3M/1Y), leg vol
(3M/1Y), implied/realised market vol, exposure per dollar (beta, DV01/$, CS01/$, currency/$), regime flags (bear
market, high vol, rising rates), option delta, premium per dollar hedged, implied vol and tenor ÷ horizon, and the
hedge's own track record on windows already finished (variance reduction, share of losses offset). Historical
bid/ask, volume and open interest are not available point in time for these products, so liquidity and trading cost
are NOT features (the live score applies them separately through L and cost); saying so is better than inventing them."""
from __future__ import annotations

import math
import random
from typing import Dict, List, Optional

from .history import ADJ_CAP, ALPHA, ML_MIN_NEFF, MIN_WINDOWS, STEP

BASIC = ["vix", "corr_252", "corr_63", "beta_change", "mkt_3m", "rate"]
RICH = BASIC + ["corr_instability", "basis_risk", "factor_vol_ratio", "leg_vol_ratio", "iv_rv", "exposure_per_dollar",
                "bear", "high_vol", "rising_rates", "delta0", "cost", "iv", "tenor_ratio", "track_reduction", "track_tail"]
METRICS = ["variance", "exposure", "downside", "drawdown", "var95", "es95"]
POOLED = {"var95", "es95"}
METRIC_LABEL = {"variance": "Variance", "exposure": "Residual factor exposure (beta / DV01 / CS01 / FX)", "downside": "Downside semivariance",
                "drawdown": "Drawdown", "var95": "VaR 95% (window)", "es95": "ES 95% (window)"}
OBJECTIVE_METRIC = {"min_variance": "variance", "target_vol": "variance", "systematic": "variance", "name": "variance", "volatility": "variance",
                    "beta": "exposure", "neutral": "exposure", "sector": "exposure", "duration": "exposure", "curve": "exposure",
                    "credit": "exposure", "fx": "exposure", "commodity": "exposure", "crypto": "exposure",
                    "drawdown": "drawdown", "var": "var95", "es": "es95", "crash": "es95"}
GRID = [round(-ADJ_CAP + i * ADJ_CAP / 6, 6) for i in range(13)]      # adjustments −cap … +cap
TRACK_N = 12


def metric_for(objective: Optional[str]) -> str:
    return OBJECTIVE_METRIC.get(objective or "", "variance")


# ------------------------------------------------------------------ scoring a multiple on one window
def _mult(a: float, alpha: float = ALPHA) -> float:
    return 1 + alpha * a


def window_metric(row: dict, metric: str, m: float) -> Optional[float]:
    """The metric of the hedged window with the hedge leg scaled by m (exact, from the stored daily paths)."""
    u, hp = row["_u"], row["_hp"]
    if metric == "variance":
        return sum((a + m * b) ** 2 for a, b in zip(u, hp))
    if metric == "downside":
        return sum(min(0.0, a + m * b) ** 2 for a, b in zip(u, hp))
    if metric == "drawdown":
        eq = peak = dd = 0.0
        for a, b in zip(u, hp):
            eq += a + m * b
            peak = max(peak, eq)
            dd = min(dd, eq - peak)
        return -dd
    if metric == "exposure":
        f = row["_f"]
        pr = [(a + m * b, x) for a, b, x in zip(u, hp, f) if not math.isnan(x)]
        if len(pr) < 5:
            return None
        mx = sum(x for _, x in pr) / len(pr)
        sxx = sum((x - mx) ** 2 for _, x in pr)
        if sxx <= 0:
            return None
        my = sum(y for y, _ in pr) / len(pr)
        return (sum((y - my) * (x - mx) for y, x in pr) / sxx) ** 2
    if metric in POOLED:                          # the per-window training target of the VaR/ES model
        return min(0.0, row["U"] + m * row["H"]) ** 2
    raise ValueError(metric)


def _var_es(pnl: List[float]) -> tuple:
    s = sorted(pnl)
    k = max(1, int(math.ceil(0.05 * len(s))))
    return -s[max(0, int(0.05 * (len(s) - 1)))], -sum(s[:k]) / k


def pooled(rows: List[dict], mults: List[float], metric: str) -> Optional[float]:
    pnl = [r["U"] + m * r["H"] for r, m in zip(rows, mults)]
    if len(pnl) < 20:
        return None
    v, e = _var_es(pnl)
    return v if metric == "var95" else e


def best_adjustment(row: dict, metric: str, alpha: float = ALPHA) -> Optional[float]:
    vals = [(window_metric(row, metric, _mult(a, alpha)), abs(a), a) for a in GRID]
    vals = [v for v in vals if v[0] is not None]
    return min(vals)[2] if vals else None             # ties → the smallest adjustment


# ------------------------------------------------------------------ features
def track_features(rows: List[dict], date: str) -> dict:
    """The static hedge's record on the last TRACK_N windows that had ENDED before `date` (point in time)."""
    done = [r for r in rows if r["end"] < date][-TRACK_N:]
    if len(done) < 6:
        return {"track_reduction": None, "track_tail": None}
    vu = sum(r["var_u"] for r in done)
    red = 1 - sum(r["var_h"] for r in done) / vu if vu > 0 else None
    down = [r for r in done if r["U"] < 0]
    tail = (-sum(r["H"] for r in down) / -sum(r["U"] for r in down)) if down and sum(r["U"] for r in down) < 0 else None
    return {"track_reduction": red, "track_tail": tail}


def design(rows: List[dict], feats: List[str]) -> List[List[float]]:
    """Feature vectors with each missing value replaced by that feature's mean over EARLIER windows (point in time)."""
    sums, cnt, out = [0.0] * len(feats), [0] * len(feats), []
    for r in rows:
        f = r["features"]
        x = []
        for i, k in enumerate(feats):
            v = f.get(k)
            x.append(v if v is not None else (sums[i] / cnt[i] if cnt[i] else 0.0))
        for i, k in enumerate(feats):
            if f.get(k) is not None:
                sums[i] += f[k]; cnt[i] += 1
        out.append(x)
    return out


class IncRidge:
    """Ridge on standardised features from running sums, so an expanding window adds rows without refitting from
    scratch: b = (C + λI)⁻¹ c with C the feature correlation matrix and c the feature–target covariances ÷ sd."""

    def __init__(self, p: int, lam: float = 5.0):
        self.p, self.lam, self.n = p, lam, 0
        self.sx, self.sy, self.syy = [0.0] * p, 0.0, 0.0
        self.sxx = [[0.0] * p for _ in range(p)]
        self.sxy = [0.0] * p

    def add(self, x: List[float], y: float):
        self.n += 1
        self.sy += y; self.syy += y * y
        for i in range(self.p):
            xi = x[i]
            self.sx[i] += xi; self.sxy[i] += xi * y
            row = self.sxx[i]
            for j in range(i, self.p):
                row[j] += xi * x[j]

    def solve(self) -> Optional[dict]:
        n, p = self.n, self.p
        if n < 3:
            return None
        mu = [v / n for v in self.sx]
        my = self.sy / n
        cov = lambda i, j: (self.sxx[min(i, j)][max(i, j)] / n) - mu[i] * mu[j]
        sd = [math.sqrt(max(0.0, cov(i, i))) for i in range(p)]
        act = [i for i in range(p) if sd[i] > 1e-12]
        if not act:
            return {"mu": mu, "sd": sd, "b": [0.0] * p, "my": my}
        A = [[cov(i, j) / (sd[i] * sd[j]) + (self.lam if i == j else 0.0) for j in act] for i in act]
        c = [(self.sxy[i] / n - mu[i] * my) / sd[i] for i in act]
        bz = _solve(A, c)
        b = [0.0] * p
        for k, i in enumerate(act):
            b[i] = bz[k] / sd[i]
        return {"mu": mu, "sd": sd, "b": b, "my": my}


def _solve(A: List[List[float]], c: List[float]) -> List[float]:
    n = len(c)
    M = [row[:] + [c[i]] for i, row in enumerate(A)]
    for k in range(n):
        piv = max(range(k, n), key=lambda i: abs(M[i][k]))
        M[k], M[piv] = M[piv], M[k]
        d = M[k][k] or 1e-12
        for i in range(k + 1, n):
            f = M[i][k] / d
            if f:
                for j in range(k, n + 1):
                    M[i][j] -= f * M[k][j]
    x = [0.0] * n
    for i in range(n - 1, -1, -1):
        x[i] = (M[i][n] - sum(M[i][j] * x[j] for j in range(i + 1, n))) / (M[i][i] or 1e-12)
    return x


def predict(model: dict, x: List[float]) -> float:
    return model["my"] + sum(b * (xi - m) for b, xi, m in zip(model["b"], x, model["mu"]))


# ------------------------------------------------------------------ the walk-forward
def _tstat(xs: List[float], h: int) -> Optional[float]:
    if len(xs) < 3:
        return None
    m = sum(xs) / len(xs)
    sd = math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))
    n_eff = len(xs) * min(1.0, STEP / max(h, 1))
    return m / (sd / math.sqrt(n_eff)) if sd > 0 else None


def _boot_p(rows: List[dict], m_adj: List[float], m_base: List[float], metric: str, h: int, reps: int = 400, seed: int = 11) -> Optional[float]:
    """One-sided block-bootstrap p-value that the adjusted pooled VaR/ES is NOT lower than the baseline's."""
    n = len(rows)
    if n < 20:
        return None
    block = max(1, int(math.ceil(h / STEP)))
    rnd = random.Random(seed)
    worse = 0
    for _ in range(reps):
        idx = []
        while len(idx) < n:
            s0 = rnd.randrange(n)
            idx.extend(range(s0, min(n, s0 + block)))
        idx = idx[:n]
        rs = [rows[i] for i in idx]
        a, b = pooled(rs, [m_adj[i] for i in idx], metric), pooled(rs, [m_base[i] for i in idx], metric)
        if a is None or b is None or a >= b:
            worse += 1
    return worse / reps


def _walk(rows: List[dict], metric: str, feats: List[str], alpha: float, cap: float) -> tuple:
    """Walk-forward predicted adjustment for every window: expanding training on windows that ENDED before the
    prediction date (purged), refit once per start date. Also the CONSTANT baseline: the mean of the same training
    targets (a fixed resizing learned from history, no conditioning) — a model that only rediscovers a constant
    sizing bias has no conditional skill."""
    tgt = [best_adjustment(r, metric, alpha) for r in rows]
    X = design(rows, feats)
    by_end = sorted(range(len(rows)), key=lambda i: rows[i]["end"])
    rg = IncRidge(len(feats))
    k, preds, model, last_date = 0, [None] * len(rows), None, None
    const: List[Optional[float]] = [None] * len(rows)
    start = max(MIN_WINDOWS, len(rows) // 3)
    for j in range(len(rows)):
        d = rows[j]["date"]
        if d != last_date:
            while k < len(by_end) and rows[by_end[k]]["end"] < d:
                i = by_end[k]
                if tgt[i] is not None:
                    rg.add(X[i], tgt[i])
                k += 1
            model = rg.solve() if rg.n >= MIN_WINDOWS else None
            last_date = d
        if j >= start and model is not None:
            preds[j] = max(-cap, min(cap, predict(model, X[j])))
            const[j] = max(-cap, min(cap, model["my"]))
    return preds, const


def evaluate(rows: List[dict], metric: str, feats: List[str], alpha: float = ALPHA, cap: float = ADJ_CAP) -> dict:
    """One group × one metric: out-of-sample comparison of the ML multiple with the static rule and min-variance."""
    rows = sorted([r for r in rows if r.get("_u") is not None], key=lambda r: r["date"])
    if not rows:
        return {"verified": False, "n": 0, "reasons": ["no windows"]}
    h = rows[0]["h"]
    # the track-record features: the same case's (book × hedge) earlier, finished windows (point in time)
    by_case: Dict[str, List[dict]] = {}
    for r in rows:
        prev = by_case.setdefault(r.get("case", ""), [])
        r["features"].update(track_features(prev, r["date"]))
        prev.append(r)
    preds, const = _walk(rows, metric, feats, alpha, cap)
    test = [j for j in range(len(rows)) if preds[j] is not None]
    if metric == "exposure":
        test = [j for j in test if window_metric(rows[j], metric, 1.0) is not None]
    n_eff = len(test) * min(1.0, STEP / max(h, 1))
    if len(test) < MIN_WINDOWS:
        return {"verified": False, "n": len(test), "n_eff": n_eff, "reasons": [f"only {len(test)} out-of-sample windows"]}
    tr = [rows[j] for j in test]
    m_adj = [_mult(preds[j], alpha) for j in test]
    m_mv = [rows[j].get("mv_mult") for j in test]
    m_c = [_mult(const[j], alpha) for j in test]
    has_mv = all(v is not None for v in m_mv)
    out = {"n": len(test), "n_eff": n_eff, "metric": metric, "mean_adjustment": sum(preds[j] for j in test) / len(test),
           "share_capped": sum(1 for j in test if abs(preds[j]) >= cap - 1e-9) / len(test)}
    reasons = []
    if metric in POOLED:
        s, a = pooled(tr, [1.0] * len(tr), metric), pooled(tr, m_adj, metric)
        out.update({"static": s, "adjusted": a, "gain_vs_static": (1 - a / s) if s else None,
                    "p_vs_static": _boot_p(tr, m_adj, [1.0] * len(tr), metric, h)})
        out.update({"constant": pooled(tr, m_c, metric), "p_vs_constant": _boot_p(tr, m_adj, m_c, metric, h),
                    "constant_adjustment": sum(const[j] for j in test) / len(test)})
        if has_mv:
            mv = pooled(tr, m_mv, metric)
            out.update({"min_variance": mv, "p_vs_min_variance": _boot_p(tr, m_adj, m_mv, metric, h)})
        third = tr[-max(20, len(tr) // 3):]
        recent = (pooled(third, [1.0] * len(third), metric) or 0) - (pooled(third, m_adj[-len(third):], metric) or 0)
        for key, lab in (("p_vs_static", "the static rule"), ("p_vs_constant", "a constant resizing"), ("p_vs_min_variance", "the minimum-variance ratio")):
            if key == "p_vs_min_variance" and not has_mv:
                continue
            p = out.get(key)
            if p is None or p > 0.025:
                reasons.append(f"does not beat {lab} (bootstrap p {'—' if p is None else f'{p:.2f}'})")
    else:
        st = [window_metric(r, metric, 1.0) for r in tr]
        ad = [window_metric(r, metric, m) for r, m in zip(tr, m_adj)]
        d_s = [a - b for a, b in zip(st, ad)]                      # > 0: the ML multiple did better
        out.update({"static": sum(st) / len(st), "adjusted": sum(ad) / len(ad), "gain_vs_static": 1 - sum(ad) / sum(st) if sum(st) > 0 else None,
                    "t_vs_static": _tstat(d_s, h)})
        cm = [window_metric(r, metric, m) for r, m in zip(tr, m_c)]
        out.update({"constant": sum(cm) / len(cm), "t_vs_constant": _tstat([a - b for a, b in zip(cm, ad)], h),
                    "constant_adjustment": sum(const[j] for j in test) / len(test)})
        if has_mv:
            mv = [window_metric(r, metric, m) for r, m in zip(tr, m_mv)]
            out.update({"min_variance": sum(mv) / len(mv), "t_vs_min_variance": _tstat([a - b for a, b in zip(mv, ad)], h)})
        recent = sum(d_s[-max(1, len(d_s) // 3):])
        if out["t_vs_static"] is None or out["t_vs_static"] < 2:
            reasons.append("does not beat the static rule (t {})".format("—" if out["t_vs_static"] is None else f"{out['t_vs_static']:.1f}"))
        if out["t_vs_constant"] is None or out["t_vs_constant"] < 2:
            reasons.append("does not beat a constant resizing (t {})".format("—" if out["t_vs_constant"] is None else f"{out['t_vs_constant']:.1f}"))
        if has_mv and (out.get("t_vs_min_variance") is None or out["t_vs_min_variance"] < 2):
            reasons.append("does not beat the minimum-variance ratio (t {})".format("—" if out.get("t_vs_min_variance") is None else f"{out['t_vs_min_variance']:.1f}"))
    if n_eff < ML_MIN_NEFF:
        reasons.insert(0, f"n_eff {n_eff:.0f} < {ML_MIN_NEFF}")
    if recent < 0:
        reasons.append("decayed: worse than the static rule in the most recent third")
    out["reasons"] = reasons
    out["verified"] = not reasons
    return out


def fit_final(rows: List[dict], metric: str, feats: List[str], alpha: float = ALPHA) -> Optional[dict]:
    rows = sorted([r for r in rows if r.get("_u") is not None], key=lambda r: r["date"])
    tgt = [best_adjustment(r, metric, alpha) for r in rows]
    X = design(rows, feats)
    rg = IncRidge(len(feats))
    for x, t in zip(X, tgt):
        if t is not None:
            rg.add(x, t)
    if rg.n < MIN_WINDOWS:
        return None
    fill = {}
    for i, k in enumerate(feats):
        xs = [r["features"].get(k) for r in rows if r["features"].get(k) is not None]
        fill[k] = sum(xs) / len(xs) if xs else 0.0
    return {"model": rg.solve(), "fill": fill}


def research(rows_by_group: Dict[str, List[dict]], alpha: float = ALPHA, cap: float = ADJ_CAP) -> dict:
    """Every group × metric with the rich features, plus variance with the original six (does richer help?).
    Returns {group: {metric: result (+ the final model when verified)}}."""
    out: Dict[str, dict] = {}
    for g, rows in rows_by_group.items():
        res = {}
        for metric in METRICS:
            r = evaluate(rows, metric, RICH, alpha, cap)
            r["features"] = RICH
            if r["verified"]:
                fin = fit_final(rows, metric, RICH, alpha)
                if fin:
                    r.update(fin)
                else:
                    r["verified"] = False
                    r["reasons"] = ["final fit failed"]
            res[metric] = r
        basic = evaluate(rows, "variance", BASIC, alpha, cap)
        res["variance_basic"] = {k: v for k, v in basic.items()}
        out[g] = res
    return out


def adjustment(models: Optional[dict], group: str, objective: Optional[str], features: dict, alpha: float = ALPHA, cap: float = ADJ_CAP) -> dict:
    """Today's capped adjustment for this objective from a verified objective-specific model; 0 otherwise."""
    metric = metric_for(objective)
    mdl = ((models or {}).get("groups") or {}).get(group, {}).get(metric)
    if not mdl or not mdl.get("verified") or not mdl.get("model"):
        why = (mdl or {}).get("reasons") or ["no model for this hedge and objective"]
        return {"adjustment": 0.0, "applied": 0.0, "verified": False, "metric": metric, "reasons": why}
    feats = mdl["features"]
    x = [(features.get(k) if features.get(k) is not None else mdl["fill"].get(k, 0.0)) for k in feats]
    a = max(-cap, min(cap, predict(mdl["model"], x)))
    return {"adjustment": a, "applied": alpha * a, "verified": True, "metric": metric, "reasons": []}
