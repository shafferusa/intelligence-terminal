"""ML Lab — Shaffer Alpha vs Shaffer Directional (research only; production is not changed).

The production Shaffer Score is a zero-centred *evidence* score: is this setup better or worse than normal for the asset?
Judging its sign against the sign of the absolute future return mixes two questions. This module keeps them apart:

    A. SHAFFER ALPHA        the production score (and signal-level challengers of it), judged as a ranking of relative
                            opportunity: target = the vol-scaled return in excess of a point-in-time base expectation,
                            y_A = y − μ_base/σ_h. Metrics: Pearson IC (per asset), cross-sectional rank IC, decile and
                            quintile spreads, hit rate against the weekly cross-sectional median, ranking stability.
    B. SHAFFER DIRECTIONAL  p_up = P(R_h > 0 | point-in-time information); DirectionalScore = 100·(2·p_up − 1).
                            Built from a point-in-time base-return prior plus Shaffer evidence, judged on absolute
                            direction against the naive baselines, with probability metrics (Brier, log loss, AUC,
                            calibration) and bearish-call precision.

Base-return priors μ(a, h, t) — every one uses only information available at t (outcomes only once matured):
    zero          0
    class         expanding mean of matured h-returns of the asset class (shrunk to the global mean)
    hier          global → class → sector → asset, each level shrunk to its parent (K = 20 independent outcomes;
                  crypto K = 200) — thin histories inherit from the group
    beta_market   β_t (252 days) × the market's expanding drift, as a log drift: β·(μ_m + σ_m²/2) − σ_a²/2
    carry         bond products with an observable yield of matching maturity: yield·h − σ²h/2; spot FX: uncovered
                  interest parity (r_quote − r_base) where both policy rates exist; zero elsewhere
    hedge_prior   the Shaffer Hedge design prior made point-in-time: β × market drift, own trailing drift for the
                  products the hedge treats as structural (VXX, leveraged / inverse funds)
    frequency     the hierarchical PIT positive-return frequency (the climatology forecaster) read as a prior
    product       product-specific: equity-like → beta_market; bonds with a yield → carry; spot FX and commodities → 0
                  (no roll / forward data: no drift is invented); crypto → hier with heavy shrinkage; VXX → own
                  trailing drift (roll decay); leveraged / inverse → beta_market (β ≈ leverage, drag from own vol);
                  anything else → hier
p_prior = Φ(μ / σ_h), σ_h = the asset's 63-day daily volatility × √h (point-in-time).

Directional challengers (fitted on outcomes matured before each era, frozen, scored on the era):
    prior:<p>             logistic calibration of the prior alone: logit p = a + b·z,  z = μ/σ_h
    production            logistic calibration of the production score alone (zero drift): a + b·raw/100
    alpha+prior@<depth>   a + b·z + c·raw/100, per hierarchy node, ridge-shrunk toward the parent node
    signal+prior@<depth>  signal-level weights (δ kept, β ≥ 0) fitted to the residual 1[R>0] − Φ(z), then
                          a + b·z + c·index (the signal weights learned for the directional target)
Alpha challengers: signal-level weights fitted to y_A (hierarchy cut at every depth) and interact@class.
The main prior (`product`) is fixed in advance for every challenger that needs one.

Gates, fixed before any result:
    Directional G1  pooled walk-forward: Brier score better than the climatology forecaster (the hierarchical PIT
                    positive-return frequency) with paired date-clustered t ≥ 2, and accuracy above the best naive
                    baseline on the same records
                G2  Brier better than climatology in ≥ 3 of the 4 complete eras, and the 2018 split's paired t ≥ 1
    Alpha       G1  pooled walk-forward paired Δ IC (vs production, on y_A) t ≥ 2 and Δ rank IC ≥ 0
                G2  Δ IC > 0 in ≥ 3 of the 4 complete eras and the 2018 split's Δ IC t ≥ 1
    G3 (both)       live shadow (engine/lab.py), then a manual promotion.
"""
from __future__ import annotations

import bisect
import math
import time
from typing import Dict, List, Optional, Tuple

from .. import shaffer_score as cfg
from . import weights as W
from .lab import LAB_HORIZONS, node_path
from .weights import DEPTHS, ERAS, SPLIT, Rec, _cut, _clustered_mean

PRIORS = ["zero", "class", "hier", "beta_market", "carry", "hedge_prior", "product", "frequency"]
PRIOR_LABEL = {"zero": "always-zero drift", "class": "expanding asset-class drift", "hier": "class → sector → asset drift (shrunk)",
               "beta_market": "β × PIT market drift", "carry": "observable carry (bond yield, FX interest parity)",
               "hedge_prior": "Shaffer Hedge market prior (PIT)", "product": "product-specific prior",
               "frequency": "hierarchical PIT positive-return frequency (the climatology forecaster itself)"}
MAIN_PRIOR = "product"
DRIFT_K = 20.0                 # independent matured outcomes: weight n/(n+K) on a group's own mean, the rest on its parent
CRYPTO_K = 200.0
LOGIT_LAMBDA = 50.0            # ridge toward the parent node's logistic coefficients
MARKET = "SPY"
MAIN_DIR_DEPTH = "class"
# Directional gates. v1 (the Alpha vs Directional research): G1 walk-forward Brier better than climatology (t ≥ 2) and
# accuracy above the best naive baseline; G2 ≥ 3 of 4 eras and the 2018 split. v2 (fixed 2026-09-25, before any
# new-information result): additionally G_prior — on identical PIT records the challenger must beat the prior-only model
# (paired Brier gain t ≥ 2, lower log loss, higher accuracy) and, for anything newer than it, the current Directional
# formulation (alpha+prior@global; paired Brier gain t ≥ 2). Live shadow requires v2.
GATES_VERSION = "v2"
RESEARCH_KEY = "lab:directional"
VOL_WINDOW, BETA_WINDOW, OWN_WINDOW = 63, 252, 2520
YIELD = {"UST2Y": "DGS2", "UST5Y": "DGS5", "UST10Y": "DGS10", "UST30Y": "DGS30", "CORP_BAA": "DBAA",
         "SHY": "DGS2", "IEI": "DGS5", "IEF": "DGS7", "TLT": "DGS20", "TIP": "DGS7", "SCHP": "DGS5", "AGG": "DGS5", "BND": "DGS5",
         "MBB": "DGS5", "LQD": "DBAA", "VCIT": "DBAA", "BIL": "DGS3MO", "SGOV": "DGS3MO", "FLOT": "DGS3MO"}
# spot FX: expected log change under uncovered interest parity = (rate of the quote currency − rate of the base currency)
FX_UIP = {"EURUSD": ("DFF", "ECBDFR"), "GBPUSD": ("DFF", "IUDSOIA"), "AUDUSD": ("DFF", "IRSTCI01AUM156N"),
          "USDJPY": ("IRSTCI01JPM156N", "DFF"), "USDCAD": ("IRSTCI01CAM156N", "DFF"), "USDCHF": ("IRSTCI01CHM156N", "DFF")}
COMMODITY_ETFS = {"GLD", "SLV", "USO", "DBC", "UNG", "CPER", "DBA"}     # commodity futures / bullion funds (not equity sector funds)
OWN_DRIFT = {"VXX"}            # structural roll decay the market prior cannot see


_INV = __import__("statistics").NormalDist().inv_cdf


def _phi(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _sig(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x)) if x > -35 else 0.0


def _logit(p: float) -> float:
    p = min(1 - 1e-6, max(1e-6, p))
    return math.log(p / (1 - p))


def product_rule(meta: dict) -> str:
    """Which prior the product-specific rule uses for this product."""
    a, cls, sec = meta.get("id"), meta.get("asset_class"), meta.get("sector") or ""
    if a in OWN_DRIFT:
        return "own"
    if a in YIELD:
        return "carry"
    if cls in ("FX", "COMMODITY") or sec == "Currency" or a in COMMODITY_ETFS:
        return "zero"
    if cls == "CRYPTO" or sec == "Crypto":
        return "hier"
    if cls in ("EQUITY", "INDEX") or (cls == "ETF" and sec not in ("Fixed Income",)):
        return "beta_market"
    return "hier"


# ------------------------------------------------------------------ point-in-time inputs per record
class _Prefix:
    """Prefix sums over a daily series with gaps: windowed mean / variance / covariance at any index in O(1)."""

    def __init__(self, xs: List[Optional[float]], ys: Optional[List[Optional[float]]] = None):
        n = len(xs)
        self.n = [0] * (n + 1); self.sx = [0.0] * (n + 1); self.sxx = [0.0] * (n + 1)
        self.sy = [0.0] * (n + 1); self.syy = [0.0] * (n + 1); self.sxy = [0.0] * (n + 1)
        for i in range(n):
            x = xs[i]; y = ys[i] if ys is not None else 0.0
            ok = x is not None and (ys is None or y is not None)
            self.n[i + 1] = self.n[i] + ok
            self.sx[i + 1] = self.sx[i] + (x if ok else 0.0)
            self.sxx[i + 1] = self.sxx[i] + (x * x if ok else 0.0)
            self.sy[i + 1] = self.sy[i] + (y if ok else 0.0)
            self.syy[i + 1] = self.syy[i] + (y * y if ok else 0.0)
            self.sxy[i + 1] = self.sxy[i] + (x * y if ok else 0.0)

    def window(self, i: int, w: Optional[int]):
        """Stats over days (i − w, i] (w None = everything up to i). Returns n, mean x, var x, mean y, var y, cov."""
        a, b = (0 if w is None else max(0, i + 1 - w)), i + 1
        n = self.n[b] - self.n[a]
        if n < 2:
            return n, None, None, None, None, None
        mx, my = (self.sx[b] - self.sx[a]) / n, (self.sy[b] - self.sy[a]) / n
        vx = max(0.0, (self.sxx[b] - self.sxx[a]) / n - mx * mx)
        vy = max(0.0, (self.syy[b] - self.syy[a]) / n - my * my)
        return n, mx, vx, my, vy, (self.sxy[b] - self.sxy[a]) / n - mx * my


def _logret(px: List[Optional[float]]) -> List[Optional[float]]:
    out: List[Optional[float]] = [None] * len(px)
    for i in range(1, len(px)):
        a, b = px[i - 1], px[i]
        out[i] = math.log(b / a) if a and b and a > 0 and b > 0 else None
    return out


def _group_keys(meta: dict) -> List[str]:
    cls = meta.get("asset_class") or "OTHER"
    return ["g", f"c:{cls}", f"s:{cls}:{meta.get('sector') or '-'}", f"a:{meta['id']}"]


def _shrunk(acc: Dict[str, list], keys: List[str], h: int, idx: int, root: float, crypto: bool) -> List[float]:
    """Hierarchical shrinkage of a running statistic (idx 1 = mean return, 2 = positive share) down the group chain."""
    out, parent = [], root
    for lvl, k in enumerate(keys):
        a = acc.get(k)
        K = CRYPTO_K if crypto and lvl >= 1 else DRIFT_K
        if a and a[0]:
            neff = a[0] * 5.0 / max(5.0, float(h))
            parent = (neff * (a[idx] / a[0]) + K * parent) / (neff + K)
        out.append(parent)
    return out


def attach(recs: List[Rec], research, h: int) -> dict:
    """Point-in-time σ_h, every prior's μ, and the climatology p (hierarchical positive frequency) for every record.
    Returns the latest group statistics (for live use)."""
    panel = research.panel()
    cal = panel.calendar()
    idx = {d: i for i, d in enumerate(cal)}
    rm = _logret(panel.series(MARKET))
    mk = _Prefix(rm)
    macro_cache: Dict[str, list] = {}

    def macro(sid: str) -> list:
        if sid not in macro_cache:
            try:
                macro_cache[sid] = panel.macro(sid)
            except Exception:  # noqa: BLE001
                macro_cache[sid] = [None] * len(cal)
        return macro_cache[sid]

    per_asset: Dict[str, Tuple[_Prefix, _Prefix]] = {}
    for r in recs:
        if r.asset not in per_asset:
            try:
                ra = _logret(panel.series(r.asset))
            except Exception:  # noqa: BLE001
                ra = [None] * len(cal)
            per_asset[r.asset] = (_Prefix(ra), _Prefix(ra, rm))
    # matured-outcome sweep for the class / hier priors and the climatology forecaster
    by_date = sorted(recs, key=lambda r: (r.date, r.asset))
    by_end = sorted(recs, key=lambda r: r.end)
    acc: Dict[str, list] = {}
    k = 0
    for r in by_date:
        while k < len(by_end) and by_end[k].end < r.date:
            e = by_end[k]
            for g in _group_keys(e.meta):
                a = acc.setdefault(g, [0, 0.0, 0.0])
                a[0] += 1; a[1] += e.yr; a[2] += 1.0 if e.yr > 0 else 0.0
            k += 1
        r.ext = _inputs(r.meta, idx.get(r.date), h, acc, per_asset[r.asset], mk, macro, fallback_sd=_fallback_sd(r, h))
    for e in by_end[k:]:                                   # for live use: every outcome that has matured by now
        for g in _group_keys(e.meta):
            a = acc.setdefault(g, [0, 0.0, 0.0])
            a[0] += 1; a[1] += e.yr; a[2] += 1.0 if e.yr > 0 else 0.0
    return {"groups": {g: list(v) for g, v in acc.items()}, "n_matured": len(by_end)}


def _fallback_sd(r: Rec, h: int) -> Optional[float]:
    return abs(r.yr / r.y) / math.sqrt(h) if r.y and abs(r.y) < 3.99 else None


def _inputs(meta: dict, i: int, h: int, acc: Dict[str, list], prefixes, mk: "_Prefix", macro, fallback_sd=None) -> dict:
    """Every prior's μ for one asset on calendar index i, from matured-group statistics `acc` and daily prefixes."""
    keys = _group_keys(meta)
    crypto = (meta.get("asset_class") == "CRYPTO" or meta.get("sector") == "Crypto")
    drift = _shrunk(acc, keys, h, 1, 0.0, crypto)
    freq = _shrunk(acc, keys, h, 2, 0.5, crypto)
    own, pair = prefixes
    a = meta["id"]
    n_v, _, v_d, *_ = own.window(i, VOL_WINDOW)
    sd = math.sqrt(v_d) if n_v and n_v >= 40 and v_d else fallback_sd
    mu: Dict[str, Optional[float]] = {"zero": 0.0, "class": drift[1], "hier": drift[3]}
    n_b, _, _, _, vm_w, cov = pair.window(i, BETA_WINDOW)
    n_m, mm, vm, *_ = mk.window(i, None)
    beta = (cov / vm_w) if n_b and n_b >= 126 and vm_w else None
    bm = (h * (beta * (mm + vm / 2.0) - (sd * sd if sd else 0.0) / 2.0)) if beta is not None and n_m and n_m >= 252 else None
    mu["beta_market"] = bm
    n_o, mo, *_ = own.window(i, OWN_WINDOW)
    own_mu = h * mo if n_o and n_o >= 252 else None
    carry = None
    if a in YIELD:
        yv = macro(YIELD[a])[i]
        if yv is not None:
            carry = yv / 100.0 * h / 252.0 - (sd * sd * h / 2.0 if sd else 0.0)
    elif a in FX_UIP:
        q, b = (macro(s_)[i] for s_ in FX_UIP[a])
        if q is not None and b is not None:
            carry = (q - b) / 100.0 * h / 252.0
    mu["carry"] = carry if carry is not None else 0.0
    mu["hedge_prior"] = own_mu if a in _hedge_structural() else bm
    rule = product_rule(meta)
    mu["product"] = {"own": own_mu, "carry": carry, "zero": 0.0, "hier": drift[3], "beta_market": bm}[rule]
    if mu["product"] is None:                              # the rule's input is missing: the simpler validated prior
        mu["product"] = drift[3]
    return {"i": i, "s": sd * math.sqrt(h) if sd else None, "mu": mu, "clim": freq[3], "beta": beta, "rule": rule}


def _hedge_structural():
    from ..hedge.designs import STRUCTURAL
    return STRUCTURAL


def z_of(r: Rec, prior: str) -> Optional[float]:
    e = r.ext or {}
    if prior == "frequency":                         # the climatology probability, on the same z scale (p = Φ(z))
        c = e.get("clim")
        return None if c is None else _INV(min(0.99, max(0.01, c)))
    m, s = (e.get("mu") or {}).get(prior), e.get("s")
    if m is None or not s:
        return None
    return max(-6.0, min(6.0, m / s))


def p_prior(r: Rec, prior: str) -> Optional[float]:
    z = z_of(r, prior)
    return None if z is None else _phi(z)


def y_alpha(r: Rec) -> float:
    """Target A: the vol-scaled return in excess of the point-in-time base expectation (main prior)."""
    z = z_of(r, MAIN_PRIOR)
    return max(-4.0, min(4.0, r.y - (z or 0.0)))


# ------------------------------------------------------------------ small logistic regression with a ridge toward a prior
def _solve(A: List[List[float]], b: List[float]) -> List[float]:
    n = len(b)
    M = [row[:] + [b[i]] for i, row in enumerate(A)]
    for c in range(n):
        p = max(range(c, n), key=lambda r_: abs(M[r_][c]))
        if abs(M[p][c]) < 1e-12:
            return [0.0] * n
        M[c], M[p] = M[p], M[c]
        for r_ in range(n):
            if r_ != c and M[r_][c]:
                f = M[r_][c] / M[c][c]
                for k in range(c, n + 1):
                    M[r_][k] -= f * M[c][k]
    return [M[i][n] / M[i][i] for i in range(n)]


def logistic(X: List[List[float]], o: List[float], prior: List[float], lam: float, q: float, iters: int = 12,
             start: Optional[List[float]] = None) -> List[float]:
    """argmax Σ q·loglik − λ/2‖w − prior‖² by Newton steps (the intercept is penalised like the rest)."""
    P = len(prior)
    w = list(start or prior)
    for _ in range(iters):
        H = [[(lam if i == j else 0.0) for j in range(P)] for i in range(P)]
        g = [lam * (w[i] - prior[i]) for i in range(P)]
        for x, y in zip(X, o):
            s = sum(wi * xi for wi, xi in zip(w, x))
            p = _sig(s)
            d = q * (p - y)
            v = q * p * (1 - p)
            for i in range(P):
                g[i] += d * x[i]
                xi = v * x[i]
                Hi = H[i]
                for j in range(i, P):
                    Hi[j] += xi * x[j]
        for i in range(P):
            for j in range(i):
                H[i][j] = H[j][i]
        step = _solve(H, g)
        w = [wi - si for wi, si in zip(w, step)]
        if max(abs(si) for si in step) < 1e-7:
            break
    return w


# ------------------------------------------------------------------ directional metrics
def _auc(ps: List[float], os_: List[float]) -> Optional[float]:
    pos = sum(os_)
    neg = len(os_) - pos
    if not pos or not neg:
        return None
    order = sorted(range(len(ps)), key=lambda i: ps[i])
    rank_sum, i = 0.0, 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and ps[order[j + 1]] == ps[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1
        for k in range(i, j + 1):
            if os_[order[k]]:
                rank_sum += avg
        i = j + 1
    return (rank_sum - pos * (pos + 1) / 2.0) / (pos * neg)


def _share_ci(by: Dict[int, List[float]], h: int) -> dict:
    c = _clustered_mean(by, h)
    if c["mean"] is None:
        return {"value": None, "n": sum(len(v) for v in by.values())}
    return {"value": c["mean"], "lo": max(0.0, c["mean"] - 1.96 * c["se"]), "hi": min(1.0, c["mean"] + 1.96 * c["se"]),
            "n": sum(len(v) for v in by.values())}


def dir_metrics(recs: List[Rec], ps: List[float], h: int, with_bands: bool = False) -> dict:
    """Probability forecasts p_up for records (same order) against 1[R_h > 0]."""
    rows = [(r, p) for r, p in zip(recs, ps) if p is not None and r.yr != 0]
    if not rows:
        return {"n": 0}
    os_ = [1.0 if r.yr > 0 else 0.0 for r, _ in rows]
    pv = [min(1 - 1e-6, max(1e-6, p)) for _, p in rows]
    tp = fn = fp = tn = 0
    by_acc: Dict[int, List[float]] = {}
    by_brier: Dict[int, List[float]] = {}
    for (r, p), o in zip(rows, os_):
        if p != 0.5:
            up = p > 0.5
            if up and o: tp += 1
            elif up: fp += 1
            elif o: fn += 1
            else: tn += 1
            by_acc.setdefault(r.wk, []).append(1.0 if up == bool(o) else 0.0)
        clim = (r.ext or {}).get("clim")
        if clim is not None:
            by_brier.setdefault(r.wk, []).append((clim - o) ** 2 - (p - o) ** 2)
    n = len(rows)
    acc = _share_ci(by_acc, h)
    brier = sum((p - o) ** 2 for p, o in zip(pv, os_)) / n
    clim_rows = [((r.ext or {}).get("clim"), o) for (r, _), o in zip(rows, os_) if (r.ext or {}).get("clim") is not None]
    brier_clim = sum((c - o) ** 2 for c, o in clim_rows) / len(clim_rows) if clim_rows else None
    db = _clustered_mean(by_brier, h)
    out = {"n": n, "base_rate": sum(os_) / n, "accuracy": acc.get("value"), "acc_lo": acc.get("lo"), "acc_hi": acc.get("hi"),
           "coverage": (tp + fn + fp + tn) / n,
           "balanced_accuracy": ((tp / (tp + fn) if tp + fn else 0.0) + (tn / (tn + fp) if tn + fp else 0.0)) / 2.0 if (tp + fn) and (tn + fp) else None,
           "bull_precision": tp / (tp + fp) if tp + fp else None, "bear_precision": tn / (tn + fn) if tn + fn else None,
           "bull_recall": tp / (tp + fn) if tp + fn else None, "bear_recall": tn / (tn + fp) if tn + fp else None,
           "confusion": {"up_called_up": tp, "up_called_down": fn, "down_called_up": fp, "down_called_down": tn},
           "bull_calls": (tp + fp) / n, "brier": brier, "brier_clim": brier_clim,
           "brier_skill": (1 - brier / brier_clim) if brier_clim else None,
           "brier_gain": db["mean"], "brier_gain_t": (db["mean"] / db["se"]) if db["mean"] is not None and db["se"] else None,
           "log_loss": -sum(o * math.log(p) + (1 - o) * math.log(1 - p) for p, o in zip(pv, os_)) / n,
           "auc": _auc(pv, os_)}
    # calibration: ten bins of p
    order = sorted(range(n), key=lambda i: pv[i])
    cal, ece = [], 0.0
    for b in range(10):
        sl = order[b * n // 10:(b + 1) * n // 10]
        if not sl:
            continue
        mp = sum(pv[i] for i in sl) / len(sl)
        fr = sum(os_[i] for i in sl) / len(sl)
        cal.append({"p": mp, "realized": fr, "n": len(sl)})
        ece += len(sl) / n * abs(mp - fr)
    out["calibration"], out["ece"] = cal, ece
    # bearish calls: P(R < 0 | score below threshold) against the unconditional rate on the same records
    down_all = 1 - out["base_rate"]
    bear = []
    for thr in (-20, -40, -60):
        by: Dict[int, List[float]] = {}
        for (r, p), o in zip(rows, os_):
            if 100 * (2 * p - 1) < thr:
                by.setdefault(r.wk, []).append(1.0 - o)
        c = _share_ci(by, h)
        bear.append({"threshold": thr, "n": c["n"], "p_down": c.get("value"), "lo": c.get("lo"), "hi": c.get("hi"),
                     "unconditional": down_all, "lift": (c["value"] - down_all) if c.get("value") is not None else None})
    out["bearish"] = bear
    if with_bands:
        out["bands"] = dir_bands(rows, os_, h)
    return out


def dir_bands(rows, os_, h) -> List[dict]:
    out = []
    for lo, hi in W.BANDS:
        sel = [((r, p), o) for (r, p), o in zip(rows, os_) if lo <= 100 * (2 * p - 1) < hi]
        if not sel:
            out.append({"band": f"{lo:g}..{min(hi, 100):g}", "n": 0})
            continue
        rets = sorted(r.yr for (r, _), _ in sel)
        by_pos: Dict[int, List[float]] = {}
        by_acc: Dict[int, List[float]] = {}
        for (r, p), o in sel:
            by_pos.setdefault(r.wk, []).append(o)
            if p != 0.5:
                by_acc.setdefault(r.wk, []).append(1.0 if (p > 0.5) == bool(o) else 0.0)
        rp, ac = _share_ci(by_pos, h), _share_ci(by_acc, h)
        out.append({"band": f"{lo:g}..{min(hi, 100):g}", "n": len(sel), "independent": len(by_pos) * 5.0 / max(5.0, float(h)),
                    "mean": math.expm1(sum(rets) / len(rets)), "median": math.expm1(rets[len(rets) // 2]),
                    "positive": sum(o for _, o in sel) / len(sel), "expected_p": sum(p for (_, p), _ in sel) / len(sel),
                    "realized_p": rp.get("value"), "p_lo": rp.get("lo"), "p_hi": rp.get("hi"),
                    "correct": ac.get("value"), "lo": ac.get("lo"), "hi": ac.get("hi")})
    return out


def base_rates(recs: List[Rec]) -> dict:
    rs = [r for r in recs if r.yr != 0]
    by: Dict[str, List[int]] = {}
    for r in rs:
        by.setdefault(r.meta.get("asset_class") or "OTHER", []).append(1 if r.yr > 0 else 0)
    return {"all": sum(1 for r in rs if r.yr > 0) / max(1, len(rs)), "n": len(rs),
            "by_class": {k: {"rate": sum(v) / len(v), "n": len(v)} for k, v in sorted(by.items())}}


# ------------------------------------------------------------------ alpha metrics (ranking relative opportunity)
def _std_prods_t(rows, key, tkey) -> Dict[str, List[Tuple[int, float]]]:
    by: Dict[str, list] = {}
    for r in rows:
        by.setdefault(r.asset, []).append(r)
    out = {}
    for a, rs in by.items():
        if len(rs) < 20:
            continue
        xs, ys = [key(r) for r in rs], [tkey(r) for r in rs]
        mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
        sx = math.sqrt(sum((v - mx) ** 2 for v in xs) / len(xs))
        sy = math.sqrt(sum((v - my) ** 2 for v in ys) / len(ys))
        if sx > 1e-12 and sy > 1e-12:
            out[a] = [(r.wk, (x - mx) / sx * (y - my) / sy) for r, x, y in zip(rs, xs, ys)]
    return out


def _pooled(prods) -> dict:
    by: Dict[int, List[float]] = {}
    for rs in prods.values():
        for w_, v in rs:
            by.setdefault(w_, []).append(v)
    return by


def alpha_metrics(rows: List[Rec], key, h: int) -> dict:
    if not rows:
        return {"n": 0}
    ic = _clustered_mean(_pooled(_std_prods_t(rows, key, y_alpha)), h)
    weeks: Dict[int, list] = {}
    for r in rows:
        weeks.setdefault(r.wk, []).append(r)
    rk, q5, hit, stab = {}, {}, {}, []
    prev = None
    for wk in sorted(weeks):
        rs = weeks[wk]
        if len(rs) >= 8:
            xs, ys = W._ranks([key(r) for r in rs]), W._ranks([y_alpha(r) for r in rs])
            m = (len(rs) - 1) / 2.0
            sx = math.sqrt(sum((a - m) ** 2 for a in xs)); sy = math.sqrt(sum((b - m) ** 2 for b in ys))
            if sx > 0 and sy > 0:
                rk[wk] = [sum((a - m) * (b - m) for a, b in zip(xs, ys)) / (sx * sy)]
            o = sorted(rs, key=key)
            k = max(1, len(o) // 5)
            q5[wk] = [sum(r.yr for r in o[-k:]) / k - sum(r.yr for r in o[:k]) / k]
            mk_ = sorted(key(r) for r in rs)[len(rs) // 2]
            my_ = sorted(r.yr for r in rs)[len(rs) // 2]
            hs = [1.0 if (key(r) > mk_) == (r.yr > my_) else 0.0 for r in rs if key(r) != mk_ and r.yr != my_]
            if hs:
                hit[wk] = hs
        cur = {r.asset: key(r) for r in rs}
        if prev:
            common = [a for a in cur if a in prev]
            if len(common) >= 8:
                a_, b_ = W._ranks([prev[a] for a in common]), W._ranks([cur[a] for a in common])
                m = (len(common) - 1) / 2.0
                sa = math.sqrt(sum((x - m) ** 2 for x in a_)); sb = math.sqrt(sum((x - m) ** 2 for x in b_))
                if sa > 0 and sb > 0:
                    stab.append(sum((x - m) * (y - m) for x, y in zip(a_, b_)) / (sa * sb))
        prev = cur
    r_ = _clustered_mean(rk, h); q_ = _clustered_mean(q5, h); ht = _clustered_mean(hit, h)
    order = sorted(rows, key=key)
    dec = max(1, len(order) // 10)
    t = lambda c: (c["mean"] / c["se"]) if c["mean"] is not None and c["se"] else None  # noqa: E731
    return {"n": len(rows), "ic": ic["mean"], "ic_t": t(ic), "rank_ic": r_["mean"], "rank_t": t(r_),
            "quintile_spread": q_["mean"], "quintile_t": t(q_), "hit_vs_median": ht["mean"], "hit_t": ((ht["mean"] - 0.5) / ht["se"]) if ht["mean"] is not None and ht["se"] else None,
            "decile_spread": (math.expm1(sum(r.yr for r in order[-dec:]) / dec) - math.expm1(sum(r.yr for r in order[:dec]) / dec)) if len(order) >= 100 else None,
            "rank_stability": (sum(stab) / len(stab)) if stab else None}


def alpha_compare(rows: List[Rec], h: int) -> dict:
    P = alpha_metrics(rows, lambda r: r.raw, h)
    C = alpha_metrics(rows, lambda r: r.score, h)
    pp, pc = _std_prods_t(rows, lambda r: r.raw, y_alpha), _std_prods_t(rows, lambda r: r.score, y_alpha)
    diff = {}
    for a in set(pp) & set(pc):
        mb = dict(pp[a])
        diff[a] = [(w_, v - mb[w_]) for w_, v in pc[a] if w_ in mb]
    d = _clustered_mean(_pooled(diff), h)
    return {"n": len(rows), "production": P, "challenger": C,
            "delta_ic": {"ic": d["mean"], "t": (d["mean"] / d["se"]) if d["mean"] is not None and d["se"] else None},
            "delta_rank_ic": (C["rank_ic"] - P["rank_ic"]) if C.get("rank_ic") is not None and P.get("rank_ic") is not None else None}


# ------------------------------------------------------------------ directional challengers over the eras
FINAL_CUT = W.FINAL_CUT


def _xrow(r: Rec, model: str, prior: str, extra: Optional[float] = None) -> List[float]:
    if model == "prior":
        return [1.0] if prior == "zero" else [1.0, z_of(r, prior) or 0.0]
    if model == "production":
        return [1.0, r.raw / 100.0]
    if model == "alpha+prior":
        return [1.0, z_of(r, MAIN_PRIOR) or 0.0, r.raw / 100.0]
    if model == "signal+prior":
        return [1.0, z_of(r, MAIN_PRIOR) or 0.0, extra or 0.0]
    raise ValueError(model)


def _obs(r: Rec) -> float:
    return 1.0 if r.yr > 0 else 0.0


def fit_logit_tree(rows: List[Tuple[List[float], float, List[str]]], h: int) -> Dict[str, List[float]]:
    """One logistic per hierarchy node, each shrunk toward its parent's coefficients (global: weak prior at 0)."""
    if not rows:
        return {}
    P = len(rows[0][0])
    q = 5.0 / max(5.0, float(h))
    nodes: Dict[str, List[int]] = {}
    parent: Dict[str, str] = {}
    for k, (_, _, pth) in enumerate(rows):
        for a, b in zip([None] + pth, pth):
            nodes.setdefault(b, []).append(k)
            if a:
                parent[b] = a
    level = lambda n: 0 if n == "global" else ["class", "sector", "industry", "asset"].index(n.split(":")[0]) + 1  # noqa: E731
    out: Dict[str, List[float]] = {}
    for n in sorted(nodes, key=lambda n: (level(n), n)):
        ks = nodes[n]
        pr = out.get(parent.get(n, ""), [0.0] * P)
        glob = n == "global"
        out[n] = logistic([rows[k][0] for k in ks], [rows[k][1] for k in ks], pr, 1.0 if glob else LOGIT_LAMBDA, q,
                          iters=15 if glob else 6, start=pr)
    return out


def _predict_tree(coef: Dict[str, List[float]], x: List[float], pth: List[str]) -> Optional[float]:
    for n in reversed(pth):
        if n in coef:
            return _sig(sum(a * b for a, b in zip(coef[n], x)))
    return None


def run_dir(recs: List[Rec], h: int, model: str, depth: str = "global", prior: str = MAIN_PRIOR, keep: bool = False) -> dict:
    """Fit on outcomes matured before each era / the split / everything; score the held-out records."""
    paths = [_cut(node_path(r.meta, "hier"), depth) for r in recs]
    outer = [a for a, _ in ERAS] + [SPLIT, FINAL_CUT]
    T = None
    if model == "signal+prior":
        T = W._Trainer(recs, paths, "signal", h, outer, target=lambda r: _obs(r) - (p_prior(r, MAIN_PRIOR) or 0.5))

    def fit(cut: str):
        """Returns (predict(indices) -> p list, detail) or None."""
        tr = [i for i, r in enumerate(recs) if r.end < cut]
        if len(tr) < 500:
            return None
        extra = None
        detail: dict = {"train": len(tr)}
        if T is not None:
            ft = T.fit(cut, (1, 1, 1))
            if ft is None:
                return None
            fs = T.feats((1, 1, 1))

            def extra(i):
                w = ft.weights(paths[i])
                return math.tanh(W._index(w, ft.rms, fs[i]) / ft.scale) if w else 0.0
            detail["signal"] = {"W": ft.W, "rms": ft.rms, "scale": ft.scale, "n": ft.n}
        rows = [(_xrow(recs[i], model, prior, extra(i) if extra else None), _obs(recs[i]),
                 paths[i] if model == "alpha+prior" else ["global"]) for i in tr]
        coef = fit_logit_tree(rows, h)
        detail["coef"] = coef

        def pred(idx: List[int]) -> List[Optional[float]]:
            return [_predict_tree(coef, _xrow(recs[i], model, prior, extra(i) if extra else None),
                                  paths[i] if model == "alpha+prior" else ["global"]) for i in idx]
        return pred, detail

    res = {"model": model, "depth": depth, "prior": prior if model == "prior" else MAIN_PRIOR, "eras": []}
    wf_idx, wf_p, era_sig = [], [], {}
    for a, b in ERAS:
        test = [i for i, r in enumerate(recs) if a <= r.date < b]
        f = fit(a)
        if f is None or len(test) < 200:
            res["eras"].append({"from": a, "to": b, "status": "insufficient", "test": len(test)})
            continue
        pred, det = f
        ps = pred(test)
        m = dir_metrics([recs[i] for i in test], ps, h)
        bl = W.baselines([recs[i] for i in test], h)
        res["eras"].append({"from": a, "to": b, "train": det["train"], "test": len(test), **m, "baseline": bl["best"],
                            "baseline_acc": bl["best_acc"], "coef_global": det["coef"].get("global")})
        wf_idx += test; wf_p += ps
        if "signal" in det:
            era_sig[a] = det["signal"]
    if keep:
        res["_pred"] = dict(zip(wf_idx, wf_p))
    if wf_idx:
        rs = [recs[i] for i in wf_idx]
        res["walkforward"] = dir_metrics(rs, wf_p, h, with_bands=True)
        pm = {id(r): p for r, p in zip(rs, wf_p)}
        res["ranking"] = alpha_metrics([r for r in rs if pm.get(id(r)) is not None], lambda r: pm[id(r)], h)
        bl = W.baselines(rs, h)
        res["walkforward"].update(baselines=bl["by"], baseline=bl["best"], baseline_acc=bl["best_acc"],
                                  excess=(res["walkforward"]["accuracy"] - bl["best_acc"]) if res["walkforward"].get("accuracy") is not None else None)
    else:
        res["walkforward"] = {"n": 0}
    f = fit(SPLIT)
    if f:
        test = [i for i, r in enumerate(recs) if r.date >= SPLIT]
        res["split"] = dir_metrics([recs[i] for i in test], f[0](test), h)
    f = fit(FINAL_CUT)
    if f:
        res["final"] = {"coef": f[1]["coef"], "train": f[1]["train"]}
        if "signal" in f[1]:
            sg = f[1]["signal"]
            res["final"]["signal"] = {"weights": sg["W"], "rms": sg["rms"], "scale": sg["scale"], "n": sg["n"]}
    if era_sig:
        res["era_weights"] = {a: d["W"].get("global") for a, d in era_sig.items()}
        res["era_rms"] = {a: d["rms"] for a, d in era_sig.items()}
    res["gates"] = dir_gates(res)
    return res


PAIRS = [("alpha+prior@global", "prior:product"), ("alpha+prior@class", "prior:product"), ("alpha+prior@sector", "prior:product"),
         ("signal+prior@global", "prior:product"),
         ("prior:product", "prior:zero"), ("prior:frequency", "prior:zero")]


def _spec(name: str) -> dict:
    if name.startswith("prior:"):
        return {"model": "prior", "prior": name.split(":", 1)[1]}
    m, d = name.split("@")
    return {"model": m, "depth": d}


def paired(recs: List[Rec], h: int, preds: Dict[str, Dict[int, float]], a: str, b: str) -> dict:
    """Does model a add to model b? Same walk-forward records: weekly-clustered Brier gain (b − a), accuracy and AUC."""
    pa, pb = preds[a], preds[b]
    common = [i for i in pa if i in pb and pa[i] is not None and pb[i] is not None]
    by_b: Dict[int, List[float]] = {}
    by_a: Dict[int, List[float]] = {}
    by_l: Dict[int, List[float]] = {}
    ll = lambda p, o: -(o * math.log(min(1 - 1e-6, max(1e-6, p))) + (1 - o) * math.log(min(1 - 1e-6, max(1e-6, 1 - p))))  # noqa: E731
    for i in common:
        r = recs[i]
        o = _obs(r)
        by_b.setdefault(r.wk, []).append((pb[i] - o) ** 2 - (pa[i] - o) ** 2)
        by_l.setdefault(r.wk, []).append(ll(pb[i], o) - ll(pa[i], o))
        if pa[i] != 0.5 and pb[i] != 0.5:
            by_a.setdefault(r.wk, []).append((1.0 if (pa[i] > 0.5) == bool(o) else 0.0) - (1.0 if (pb[i] > 0.5) == bool(o) else 0.0))
    c, d, lg = _clustered_mean(by_b, h), _clustered_mean(by_a, h), _clustered_mean(by_l, h)
    os_ = [_obs(recs[i]) for i in common]
    au_a, au_b = _auc([pa[i] for i in common], os_), _auc([pb[i] for i in common], os_)
    return {"a": a, "b": b, "n": len(common), "brier_gain": c["mean"], "brier_t": (c["mean"] / c["se"]) if c["mean"] is not None and c["se"] else None,
            "delta_acc": d["mean"], "delta_acc_t": (d["mean"] / d["se"]) if d["mean"] is not None and d["se"] else None,
            "logloss_gain": lg["mean"], "logloss_t": (lg["mean"] / lg["se"]) if lg["mean"] is not None and lg["se"] else None,
            "auc_a": au_a, "auc_b": au_b, "adds": bool(c["mean"] is not None and c["se"] and c["mean"] / c["se"] >= 2)}


def prior_gate(pr: Optional[dict]) -> bool:
    """Gate v2's G_prior on one paired comparison (challenger vs the prior-only model)."""
    return bool(pr and (pr.get("brier_t") or 0) >= 2 and (pr.get("logloss_gain") or 0) > 0 and (pr.get("delta_acc") or 0) > 0)


def bear_composition(recs: List[Rec], pred: Dict[int, float], threshold: float = -20.0) -> Dict[str, dict]:
    """Bearish calls (score below the threshold) grouped by the product rule that set the base prior, with P(R < 0)."""
    out: Dict[str, list] = {}
    for i, p in pred.items():
        if p is not None and 100 * (2 * p - 1) < threshold:
            r = recs[i]
            a = out.setdefault((r.ext or {}).get("rule") or "?", [0, 0])
            a[0] += 1; a[1] += 1 if r.yr < 0 else 0
    return {k: {"n": n, "p_down": d / n} for k, (n, d) in sorted(out.items())}


def paired_study(recs: List[Rec], h: int, pairs=PAIRS, with_composition: bool = False):
    names = sorted({x for p in pairs for x in p})
    preds = {n: run_dir(recs, h, keep=True, **_spec(n))["_pred"] for n in names}
    out = [paired(recs, h, preds, a, b) for a, b in pairs]
    if with_composition:
        return out, {n: bear_composition(recs, preds[n]) for n in ("prior:product", "alpha+prior@global") if n in preds}
    return out


def eval_fixed(recs: List[Rec], h: int, pfun) -> dict:
    """A fixed (unfitted) probability rule on the same eras: e.g. production read literally, p = (1 + raw/100)/2."""
    res = {"model": "fixed", "eras": []}
    wf_r, wf_p = [], []
    for a, b in ERAS:
        rs = [r for r in recs if a <= r.date < b]
        if len(rs) < 200:
            continue
        ps = [pfun(r) for r in rs]
        bl = W.baselines(rs, h)
        res["eras"].append({"from": a, "to": b, "test": len(rs), **dir_metrics(rs, ps, h), "baseline": bl["best"], "baseline_acc": bl["best_acc"]})
        wf_r += rs; wf_p += ps
    res["walkforward"] = dir_metrics(wf_r, wf_p, h, with_bands=True) if wf_r else {"n": 0}
    if wf_r:
        res["ranking"] = alpha_metrics(wf_r, pfun, h)
        bl = W.baselines(wf_r, h)
        res["walkforward"].update(baselines=bl["by"], baseline=bl["best"], baseline_acc=bl["best_acc"],
                                  excess=(res["walkforward"]["accuracy"] - bl["best_acc"]) if res["walkforward"].get("accuracy") is not None else None)
    sp = [r for r in recs if r.date >= SPLIT]
    res["split"] = dir_metrics(sp, [pfun(r) for r in sp], h)
    res["gates"] = dir_gates(res)
    return res


def dir_gates(res: dict) -> dict:
    wf = res.get("walkforward") or {}
    full = [e for e in res["eras"][:4] if e.get("brier_gain") is not None]
    won = sum(1 for e in full if (e.get("brier_gain") or 0) > 0)
    sp = res.get("split") or {}
    enough = len(full) >= 3 and (wf.get("n") or 0) >= 2000
    g1 = bool(enough and (wf.get("brier_gain_t") or 0) >= 2 and wf.get("accuracy") is not None and wf.get("baseline_acc") is not None
              and wf["accuracy"] > wf["baseline_acc"])
    g2 = bool(enough and won >= 3 and (sp.get("brier_gain_t") or 0) >= 1)
    return {"G1_walkforward": g1, "G2_eras": g2, "eras_won": won, "eras_complete": len(full),
            "status": "INSUFFICIENT DATA" if not enough else ("SHADOW" if g1 and g2 else "REJECT")}


# ------------------------------------------------------------------ alpha challengers (signal weights fitted to y_A)
def run_alpha(recs: List[Rec], h: int, kind: str, depth: str) -> dict:
    paths = [_cut(node_path(r.meta, "hier"), depth) for r in recs]
    outer = [a for a, _ in ERAS] + [SPLIT, FINAL_CUT]
    T = W._Trainer(recs, paths, kind, h, outer, target=y_alpha)
    g = (1, 1, 1)
    res = {"kind": kind, "depth": depth, "eras": [], "n_params": W.n_features(kind), "gamma": [1, 1, 1]}
    wf_rows, era = [], []
    for a, b in ERAS:
        ft = T.fit(a, g)
        test = [i for i, r in enumerate(recs) if a <= r.date < b]
        if ft is None or len(test) < 200:
            res["eras"].append({"from": a, "to": b, "status": "insufficient", "test": len(test)})
            continue
        rows = T.predict(ft, g, test)
        wf_rows += rows
        res["eras"].append({"from": a, "to": b, "train": ft.n, "test": len(rows), **alpha_compare(rows, h)})
        era.append((a, ft))
    res["walkforward"] = alpha_compare(wf_rows, h) if wf_rows else {"n": 0}
    ft = T.fit(SPLIT, g)
    if ft:
        res["split"] = alpha_compare(T.predict(ft, g, [i for i, r in enumerate(recs) if r.date >= SPLIT]), h)
    final = T.fit(FINAL_CUT, g)
    res["final"] = {"n": final.n, "scale": final.scale, "rms": final.rms, "weights": final.W} if final else None
    res["era_weights"] = {a: f.W.get("global") for a, f in era}
    res["era_rms"] = {a: f.rms for a, f in era}
    res["gates"] = alpha_gates(res)
    return res


def alpha_gates(res: dict) -> dict:
    wf = res.get("walkforward") or {}
    full = [e for e in res["eras"][:4] if e.get("delta_ic")]
    won = sum(1 for e in full if (e["delta_ic"].get("ic") or 0) > 0)
    sp = ((res.get("split") or {}).get("delta_ic") or {})
    enough = len(full) >= 3 and (wf.get("n") or 0) >= 2000
    g1 = bool(enough and ((wf.get("delta_ic") or {}).get("t") or 0) >= 2 and (wf.get("delta_rank_ic") or 0) >= 0)
    g2 = bool(enough and won >= 3 and (sp.get("t") or 0) >= 1)
    return {"G1_walkforward": g1, "G2_eras": g2, "eras_won": won, "eras_complete": len(full),
            "status": "INSUFFICIENT DATA" if not enough else ("SHADOW" if g1 and g2 else "REJECT")}


# ------------------------------------------------------------------ the study
FOCUS_SIGNALS = ["idio_vol_252", "ma_cross", "macd", "ret_3m", "ret_6m", "ret_12m", "mom_12_1"]


def _sig_res(res: dict) -> Optional[dict]:
    """A directional signal+prior result in the shape weights.effective_weights / stability expect."""
    fin = (res.get("final") or {}).get("signal")
    if not fin:
        return None
    return {"kind": "signal", "depth": res["depth"], "final": fin, "gamma": [1, 1, 1],
            "era_weights": res.get("era_weights") or {}, "era_rms": res.get("era_rms") or {}}


def signal_findings(recs: List[Rec], alpha_res: dict, dir_res: dict) -> dict:
    """Per signal: production's share, the alpha and the directional challenger's learned share (global node), sign and
    magnitude stability across the era fits, and which target the evidence supports."""
    sigs = W.signals()
    ea = W.effective_weights(recs, alpha_res).get("global", {}).get("signals", {}) if alpha_res.get("final") else {}
    dres = _sig_res(dir_res)
    ed = W.effective_weights(recs, dres).get("global", {}).get("signals", {}) if dres else {}
    sa, sd_ = W.stability(alpha_res), (W.stability(dres) if dres else {})

    def mag(st):
        if not st:
            return None
        v = st["eras"]
        m = sum(v) / len(v)
        sd = math.sqrt(sum((x - m) ** 2 for x in v) / len(v))
        return (sd / abs(m)) if abs(m) > 1e-9 else None
    out = {}
    for s in sigs:
        a, d = ea.get(s) or {}, ed.get(s) or {}
        if not a and not d and s not in FOCUS_SIGNALS:
            continue
        stA, stD = sa.get(s), sd_.get(s)
        helps = [t for t, st in (("alpha ranking", stA), ("absolute direction", stD)) if st and st.get("same_sign") and abs(st.get("mean") or 0) > 1e-6]
        out[s] = {"production": a.get("production", d.get("production")), "alpha": a.get("challenger"), "directional": d.get("challenger"),
                  "alpha_eras": (stA or {}).get("eras"), "alpha_same_sign": bool(stA and stA.get("same_sign")), "alpha_cv": mag(stA),
                  "dir_eras": (stD or {}).get("eras"), "dir_same_sign": bool(stD and stD.get("same_sign")), "dir_cv": mag(stD),
                  "helps": " and ".join(helps) if helps else "neither (not stable in either target)"}
    return out


def study_horizon(store, research, lab: str, progress=None) -> Tuple[dict, dict]:
    """Returns (result for the UI / report, frozen final fits for the registry)."""
    say = progress or (lambda m: None)
    W._init_famidx()
    h = dict(LAB_HORIZONS)[lab]
    t0 = time.time()
    allrecs = W.load(store, research, lab)
    info = attach(allrecs, research, h)
    recs = [r for r in allrecs if r.ext and r.ext.get("s") and r.yr != 0]
    out = {"horizon": lab, "h": h, "records": len(recs), "assets": len({r.asset for r in recs}),
           "first": recs[0].date if recs else None, "last": recs[-1].date if recs else None,
           "base_rate": base_rates(recs), "wf_base_rate": base_rates([r for r in recs if r.date >= ERAS[0][0]]),
           "groups": info["groups"], "directional": {}, "alpha": {}}
    finals: dict = {}
    if len(recs) < 5000:
        out["reason"] = "not enough signal-level research records (python -m finsim2 lab --build)"
        return out, finals
    rules: Dict[str, Dict[str, int]] = {}
    for r in recs:
        c = rules.setdefault(r.meta.get("asset_class") or "OTHER", {})
        c[r.ext["rule"]] = c.get(r.ext["rule"], 0) + 1
    out["prior_rules"] = rules
    # how the production score reads as a directional call (Q1–Q2)
    wf = [r for r in recs if r.date >= ERAS[0][0]]
    out["production_view"] = {"mean_raw": sum(r.raw for r in wf) / max(1, len(wf)), "bull_share": sum(1 for r in wf if r.raw > 0) / max(1, len(wf)),
                              "bear_share": sum(1 for r in wf if r.raw < 0) / max(1, len(wf)), "base_rate": out["wf_base_rate"]["all"],
                              "near_zero_share": sum(1 for r in wf if abs(r.raw) < 10) / max(1, len(wf))}
    say(f"{lab}: {len(recs)} records with PIT priors ({time.time() - t0:.0f}s)")
    D = out["directional"]
    for p in PRIORS:
        D[f"prior:{p}"] = run_dir(recs, h, "prior", prior=p)
    say(f"{lab}: priors done")
    D["production (read as p = (1 + raw/100)/2)"] = eval_fixed(recs, h, lambda r: 0.5 + r.raw / 200.0)
    D["production"] = run_dir(recs, h, "production")
    for dp in DEPTHS:
        D[f"alpha+prior@{dp}"] = run_dir(recs, h, "alpha+prior", depth=dp)
        say(f"{lab}: alpha+prior@{dp}")
    for dp in DEPTHS:
        D[f"signal+prior@{dp}"] = run_dir(recs, h, "signal+prior", depth=dp)
        say(f"{lab}: signal+prior@{dp}")
    A = out["alpha"]
    for dp in DEPTHS:
        A[f"signal@{dp}"] = run_alpha(recs, h, "signal", dp)
        say(f"{lab}: alpha signal@{dp}")
    A["interact@class"] = run_alpha(recs, h, "interact", "class")
    out["production_alpha"] = alpha_metrics(wf, lambda r: r.raw, h)
    out["production_alpha_all"] = alpha_metrics(recs, lambda r: r.raw, h)
    # c: y_A per unit of raw/100 (the alpha score's expected edge in vol units) over every matured record
    sxx = sum((r.raw / 100.0) ** 2 for r in recs)
    out["alpha_edge_c"] = sum(r.raw / 100.0 * y_alpha(r) for r in recs) / sxx if sxx else 0.0
    # the best of each (walk-forward Brier skill / Δ IC t), specialisation per target, signal findings
    cand = [(v["walkforward"].get("brier_skill") if v.get("walkforward") else None, k) for k, v in D.items()]
    out["best_directional"] = max(((s, k) for s, k in cand if s is not None), default=(None, None))[1]
    out["best_alpha"] = max((((v.get("walkforward") or {}).get("delta_ic") or {}).get("t") or -99, k) for k, v in A.items())[1]
    out["specialisation"] = {
        "alpha": {dp: {"ic": ((A[f"signal@{dp}"].get("walkforward") or {}).get("challenger") or {}).get("ic"),
                       "rank_ic": ((A[f"signal@{dp}"].get("walkforward") or {}).get("challenger") or {}).get("rank_ic"),
                       "delta_ic": ((A[f"signal@{dp}"].get("walkforward") or {}).get("delta_ic") or {}).get("ic")} for dp in DEPTHS},
        "directional": {dp: {k: {"accuracy": (D[f"{k}@{dp}"].get("walkforward") or {}).get("accuracy"),
                                 "brier_skill": (D[f"{k}@{dp}"].get("walkforward") or {}).get("brier_skill"),
                                 "balanced": (D[f"{k}@{dp}"].get("walkforward") or {}).get("balanced_accuracy")}
                             for k in ("alpha+prior", "signal+prior")} for dp in DEPTHS}}
    out["signals"] = signal_findings(recs, A["signal@class"], D["signal+prior@class"])
    out["paired"], out["bear_composition"] = paired_study(recs, h, with_composition=True)
    out["seconds"] = round(time.time() - t0, 1)
    for k, v in D.items():
        if v.get("final"):
            finals[f"dir:{k}"] = {"model": v["model"], "depth": v["depth"], "prior": v.get("prior"), **v["final"]}
    for k, v in A.items():
        if v.get("final"):
            finals[f"alpha:{k}"] = {"kind": v["kind"], "depth": v["depth"], "gamma": [1, 1, 1], **v["final"]}
    return out, finals


def slim(res: dict) -> dict:
    drop = ("final", "era_rms", "_pred")
    out = dict(res)
    out["directional"] = {k: {kk: vv for kk, vv in v.items() if kk not in drop} for k, v in (res.get("directional") or {}).items()}
    out["alpha"] = {k: {kk: vv for kk, vv in v.items() if kk not in drop} for k, v in (res.get("alpha") or {}).items()}
    return out


def _horizon_worker(db_path: str, lab: str):
    from ..data.store import Store
    from .research import Research
    st = Store(db_path)
    try:
        res, fin = study_horizon(st, Research(st), lab, progress=lambda m: print(m, flush=True))
        return lab, slim(res), fin
    finally:
        st.close()


def run_all(db_path: str, workers: int = 3, progress=None, horizons=None) -> dict:
    from concurrent.futures import ProcessPoolExecutor, as_completed
    from ..data.store import Store
    say = progress or (lambda m: None)
    t0 = time.time()
    out = {"started": time.strftime("%Y-%m-%d %H:%M:%S"), "score_version": cfg.VERSION, "eras": ERAS, "split": SPLIT,
           "priors": PRIORS, "prior_labels": PRIOR_LABEL, "main_prior": MAIN_PRIOR, "drift_k": DRIFT_K, "crypto_k": CRYPTO_K,
           "logit_lambda": LOGIT_LAMBDA, "horizons": {}}
    finals: Dict[str, dict] = {}
    labs = [lab for lab, _ in LAB_HORIZONS if not horizons or lab in horizons]
    with ProcessPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(_horizon_worker, db_path, lab) for lab in labs]
        for f in as_completed(futs):
            lab, res, fin = f.result()
            out["horizons"][lab] = res
            finals[lab] = fin
            say(f"{lab} done: directional best {res.get('best_directional')}, alpha best {res.get('best_alpha')}")
    out["seconds"] = round(time.time() - t0, 1)
    st = Store(db_path)
    try:
        out["registered"] = register(st, out, finals)
        st.kv_set(RESEARCH_KEY, out)
    finally:
        st.close()
    return out


# ------------------------------------------------------------------ versions (all experimental; production unchanged)
def _vid(key: str) -> str:
    kind, name = key.split(":", 1)
    slug = name.replace("+", "-").replace("@", "-").replace(":", "-")
    return f"shaffer-{'directional' if kind == 'dir' else 'alpha'}-{cfg.VERSION}-{slug}-exp"


def register(store, res: dict, finals: Dict[str, dict]) -> List[str]:
    from .lab import _save_registry, registry
    reg = registry(store)
    made = []
    alias = f"shaffer-alpha-{cfg.VERSION}-production"
    if not any(v["id"] == alias for v in reg["versions"]):
        reg["versions"].append({"id": alias, "kind": "shaffer-alpha", "status": "production", "family": "alias", "parent": f"shaffer-{cfg.VERSION}",
                                "introduced": time.strftime("%Y-%m-%d"),
                                "description": f"The production Shaffer Score shaffer-{cfg.VERSION} read as what it is: an alpha / evidence score "
                                               "(setup better or worse than normal), judged by ranking — not by absolute direction"})
    keys = sorted({k for fin in finals.values() for k in fin})
    for key in keys:
        if key.startswith("dir:prior:") or key == "dir:production":
            continue                         # baselines of the study, not candidate versions
        vid = _vid(key)
        old = next((x for x in reg["versions"] if x["id"] == vid), None)
        if old and old.get("status") not in ("challenger", None):
            continue
        kind_, name = key.split(":", 1)
        per = {}
        for lab, hz in res["horizons"].items():
            src = (hz.get("directional") if kind_ == "dir" else hz.get("alpha")) or {}
            if name in src:
                per[lab] = src[name]
        store.kv_set(f"formula:weights:{vid}", {lab: {**(finals.get(lab, {}).get(key) or {}), "groups": res["horizons"][lab].get("groups"),
                                                      "alpha_edge_c": res["horizons"][lab].get("alpha_edge_c")}
                                                for lab in per})
        reg["versions"] = [x for x in reg["versions"] if x["id"] != vid]
        reg["versions"].append({
            "id": vid, "kind": "shaffer-directional" if kind_ == "dir" else "shaffer-alpha", "status": "challenger",
            "family": "directional" if kind_ == "dir" else "signal-weights", "parent": f"shaffer-{cfg.VERSION}",
            "variant": name, "introduced": (old or {}).get("introduced") or time.strftime("%Y-%m-%d"),
            "live_shadow_from": (old or {}).get("live_shadow_from") or time.strftime("%Y-%m-%d"),
            "description": (f"Shaffer Directional (research): p_up from the {MAIN_PRIOR} prior + {name.split('@')[0]}, hierarchy to {name.split('@')[-1]}"
                            if kind_ == "dir" else f"Shaffer Alpha challenger: {name} weights fitted to the excess return y − μ/σ"),
            "training_cutoff": res.get("started"),
            "validation": {lab: {"gates": {"G1_discovery": c["gates"]["G1_walkforward"], "G2_confirmation": c["gates"]["G2_eras"]},
                                 "status": c["gates"]["status"]} for lab, c in per.items()}})
        made.append(vid)
    _save_registry(store, reg)
    return made


# ------------------------------------------------------------------ live: today's p_up (live shadow + hedge information)
def live_inputs(research, meta: dict, h: int, groups: Dict[str, list]) -> Optional[dict]:
    panel = research.panel()
    cal = panel.calendar()
    i = len(cal) - 1
    try:
        ra = _logret(panel.series(meta["id"]))
    except Exception:  # noqa: BLE001
        return None
    rm = _logret(panel.series(MARKET))
    cache: Dict[str, list] = {}

    def macro(sid):
        if sid not in cache:
            try:
                cache[sid] = panel.macro(sid)
            except Exception:  # noqa: BLE001
                cache[sid] = [None] * len(cal)
        return cache[sid]
    return _inputs(meta, i, h, groups or {}, (_Prefix(ra), _Prefix(ra, rm)), _Prefix(rm), macro)


def live_p(spec: dict, meta: dict, h: int, raw: Optional[float], sig: Optional[dict], ext: dict, info: Optional[dict] = None) -> Optional[float]:
    """p_up today from a frozen directional fit (`spec` = its formula:weights entry for the horizon)."""
    if not spec or not spec.get("coef") or not ext or not ext.get("s"):
        return None
    r = Rec()
    r.asset, r.meta, r.raw, r.ext = meta["id"], meta, raw if raw is not None else 0.0, ext
    model, prior = spec["model"], spec.get("prior") or MAIN_PRIOR
    extra = None
    if model == "signal+prior":
        fin = spec.get("signal") or {}
        if not sig or not fin.get("weights"):
            return None
        W._init_famidx()
        W._fill(r, sig, {s: i for i, s in enumerate(W.signals())}, {f: i for i, f in enumerate(W.families())})
        w = None
        for n in reversed(_cut(node_path(meta, "hier"), spec["depth"])):
            if n in fin["weights"]:
                w = fin["weights"][n]; break
        if w is None:
            return None
        extra = math.tanh(W._index(w, fin["rms"], W.features(r, "signal")) / (fin.get("scale") or 1.0))
    elif raw is None and model in ("production", "alpha+prior"):
        return None
    pth = _cut(node_path(meta, "hier"), spec["depth"]) if model == "alpha+prior" else ["global"]
    if info is not None:
        info["node"] = next((n for n in reversed(pth) if n in spec["coef"]), None)
    return _predict_tree(spec["coef"], _xrow(r, model, prior, extra), pth)


def live_score(store, research, vid: str, meta: dict, lab: str, raw: Optional[float], sig: Optional[dict]) -> Tuple[Optional[float], dict]:
    spec = (store.kv_get(f"formula:weights:{vid}") or {}).get(lab)
    h = dict(LAB_HORIZONS)[lab]
    if not spec:
        return None, {}
    ext = live_inputs(research, meta, h, spec.get("groups") or {})
    info: dict = {}
    p = live_p(spec, meta, h, raw, sig, ext, info)
    if p is None:
        return None, {}
    return 100 * (2 * p - 1), {"p_up": p, "clim": (ext or {}).get("clim"), "node": info.get("node"), "prior_mu": ((ext or {}).get("mu") or {}).get(MAIN_PRIOR)}


def live_gate(store, vid: str) -> dict:
    """G3 for a directional challenger: ≥ LIVE_MIN graded live forecasts whose Brier score beats the climatology
    forecaster's on the same forecasts."""
    from .lab import LIVE_MIN
    rows = [p for p in store.predictions() if p["model"] == f"shaffer:{vid}" and p.get("realized") is not None]
    out = {"graded": len(rows), "required": LIVE_MIN}
    if len(rows) < 10:
        out["passed"] = False
        return out
    b = c = 0.0
    for p in rows:
        d = p.get("detail") or {}
        o = 1.0 if p["realized"] > 0 else 0.0
        b += (d.get("p_up", 0.5) - o) ** 2
        c += ((d.get("clim") if d.get("clim") is not None else 0.5) - o) ** 2
    out.update(brier=b / len(rows), brier_clim=c / len(rows), passed=len(rows) >= LIVE_MIN and b <= c)
    return out


def hedge_view(store, research, asset_id: str, lab: str, raw: Optional[float]) -> Optional[dict]:
    """Research outputs for the hedge design layer (informational; the hedge math does not use them in this phase):
    alpha score, directional p_up, expected absolute return, horizon risk, and whether the directional model is validated."""
    res = (store.kv_get(RESEARCH_KEY) or {}).get("horizons", {}).get(lab)
    if not res:
        return None
    vid = _vid(f"dir:alpha+prior@{MAIN_DIR_DEPTH}")
    spec = (store.kv_get(f"formula:weights:{vid}") or {}).get(lab)
    meta = store.asset(asset_id) or {"id": asset_id}
    h = dict(LAB_HORIZONS)[lab]
    ext = live_inputs(research, meta, h, (spec or {}).get("groups") or res.get("groups") or {})
    if not ext:
        return None
    p = live_p(spec, meta, h, raw, None, ext) if spec else None
    mu, s = (ext.get("mu") or {}).get(MAIN_PRIOR), ext.get("s")
    edge = (res.get("alpha_edge_c") or 0.0) * (raw or 0.0) / 100.0 * (s or 0.0)
    status = ((res.get("directional") or {}).get(f"alpha+prior@{MAIN_DIR_DEPTH}") or {}).get("gates", {}).get("status")
    return {"alpha": raw, "p_up": p, "directional_score": (100 * (2 * p - 1)) if p is not None else None,
            "expected_return": math.expm1(mu + edge) if mu is not None else None, "prior_return": math.expm1(mu) if mu is not None else None,
            "prior": ext.get("rule"), "sigma_h": s, "model": vid, "status": status or "not researched",
            "validated": status in ("SHADOW", "ELIGIBLE FOR PROMOTION")}


# ------------------------------------------------------------------ the report (SHAFFER_DIRECTIONAL_RESEARCH.md)
_HZ = [lab for lab, _ in LAB_HORIZONS]
_p, _n, _ic_t = W._p, W._n, W._ic_t


def _pc(v, d=1):
    return _p(v, d)


def _ci(v, lo, hi, d=1):
    return f"{_p(v, d)} [{_p(lo, d)}, {_p(hi, d)}]" if v is not None else "—"


def _best_dir(hz: dict) -> Tuple[Optional[str], dict]:
    k = hz.get("best_directional")
    return k, ((hz.get("directional") or {}).get(k) or {})


def _status(v: dict) -> str:
    return (v.get("gates") or {}).get("status") or "—"


def summary_rows(res: dict) -> List[dict]:
    out = []
    for lab in _HZ:
        hz = (res.get("horizons") or {}).get(lab)
        if not hz or not hz.get("directional"):
            continue
        k, b = _best_dir(hz)
        wf = b.get("walkforward") or {}
        bear = next((x for x in wf.get("bearish") or [] if x["threshold"] == -20), {})
        ba = hz.get("best_alpha")
        A = ((hz.get("alpha") or {}).get(ba) or {}).get("walkforward") or {}
        out.append({"horizon": lab, "base_rate": (hz.get("wf_base_rate") or {}).get("all"), "alpha_ic": (hz.get("production_alpha") or {}).get("ic"),
                    "alpha_rank_ic": (hz.get("production_alpha") or {}).get("rank_ic"), "alpha_rank_t": (hz.get("production_alpha") or {}).get("rank_t"),
                    "best_alpha": ba, "best_alpha_ic": (A.get("challenger") or {}).get("ic"),
                    "best": k, "accuracy": wf.get("accuracy"), "naive": wf.get("baseline_acc"), "naive_rule": wf.get("baseline"),
                    "excess": wf.get("excess"), "balanced": wf.get("balanced_accuracy"), "brier": wf.get("brier"), "brier_skill": wf.get("brier_skill"),
                    "bear_precision": wf.get("bear_precision"), "bear20": bear.get("p_down"), "status": _status(b),
                    "bear_calls": ((wf.get("coverage") or 0) - (wf.get("bull_calls") or 0)) if wf.get("coverage") is not None else None})
    return out


def markdown(res: dict) -> str:
    H = res.get("horizons") or {}
    L: List[str] = []
    w = L.append
    w("# Shaffer Alpha vs Shaffer Directional — research")
    w("")
    w(f"Run {res.get('started')} · score version {res.get('score_version')} · {sum((h.get('records') or 0) for h in H.values()):,} point-in-time records · "
      f"{res.get('seconds')} s · generated by `python -m finsim2 lab --directional`. Research only: the production Shaffer Score is unchanged.")
    w("")
    w("The production Shaffer Score is a zero-centred evidence score (is this setup better or worse than normal?). Earlier "
      "reports judged its sign against the sign of the *absolute* future return. This research keeps two questions apart:")
    w("")
    w("* **A. Shaffer Alpha** — the production score (and signal-level challengers fitted to the same target), judged as a "
      "ranking of relative opportunity: target = the vol-scaled return in excess of a point-in-time base expectation. "
      "It does not need to beat \"always bullish\"; the question is whether higher scores outperformed lower ones.")
    w("* **B. Shaffer Directional** — p_up = P(R_h > 0 | point-in-time information), DirectionalScore = 100·(2·p_up − 1), from a "
      "point-in-time base-return prior plus Shaffer evidence. Judged on absolute direction against the best naive baseline "
      "(always bullish, the asset's PIT positive-return frequency, previous direction, 12-1 momentum, 1-month mean reversion) "
      "and as a probability (Brier score against the climatology forecaster = the hierarchical PIT positive-return frequency).")
    w("")
    w("Base priors (all point-in-time; outcomes enter only once matured): " + "; ".join(f"**{k}** — {v}" for k, v in (res.get("prior_labels") or PRIOR_LABEL).items()) +
      f". Shrinkage: K = {res.get('drift_k', DRIFT_K):.0f} independent outcomes (crypto {res.get('crypto_k', CRYPTO_K):.0f}). The main prior for every "
      f"challenger is **{res.get('main_prior', MAIN_PRIOR)}**, fixed in advance. Eras: → 2008 test 2009–12, → 2012 test 2013–16, → 2016 test 2017–20, "
      "→ 2020 test 2021–24, → 2024 test 2025–now, plus the 2018 split; weights and calibration frozen before each test period.")
    w("")
    w("Gates, fixed before any result — Directional: **G1** walk-forward Brier better than climatology (paired date-clustered t ≥ 2) "
      "*and* accuracy above the best naive baseline; **G2** Brier better than climatology in ≥ 3 of 4 complete eras and the 2018 "
      "split's t ≥ 1. Alpha: **G1** paired Δ IC vs production (on the excess-return target) t ≥ 2 and Δ rank IC ≥ 0; **G2** Δ IC > 0 in "
      "≥ 3 of 4 eras and the split's t ≥ 1. **G3** live shadow, then a manual promotion.")
    w("")
    w("*Accuracy* below always means absolute direction: sign(p_up − ½) = sign(R_h) (p = ½ abstains). *Excess accuracy* = accuracy "
      "minus the best naive baseline's accuracy on the same records.")
    w("")
    w("## The main table (walk-forward, unseen eras 2009 → now)")
    w("")
    w("| Horizon | Positive-return base rate | Alpha IC (rank IC, t) | Directional accuracy | Naive accuracy | Excess accuracy | Balanced accuracy | Brier score (skill vs climatology) | Bearish precision | Status |")
    w("|---|---|---|---|---|---|---|---|---|---|")
    rows = summary_rows(res)
    for r in rows:
        w(f"| {r['horizon']} | {_pc(r['base_rate'])} | {_n(r['alpha_ic'])} ({_n(r['alpha_rank_ic'])}, t {_n(r['alpha_rank_t'], 1)}) | {_pc(r['accuracy'])} <sub>{r['best']}</sub> | "
          f"{_pc(r['naive'])} <sub>{(r['naive_rule'] or '').replace('_', ' ')}</sub> | {_p(r['excess'], 1, True)} | {_pc(r['balanced'])} | "
          f"{_n(r['brier'], 4)} ({_p(r['brier_skill'], 2, True)}) | {_pc(r['bear_precision'])} <sub>{_p(r['bear_calls'], 1)} of records</sub> | {r['status']} |")
    w("")
    w("Directional columns: the directional model with the best walk-forward Brier skill at that horizon. Alpha IC: the production "
      "score against the excess-return target (per-asset time-series IC; rank IC = weekly cross-sectional).")
    w("")
    w("## Answers")
    w("")
    # 1–2
    w("**1. What does the current production Shaffer Score predict?** Relative opportunity. Against the excess-return target "
      "(return minus the point-in-time base expectation) and across assets each week:")
    w("")
    w("| Horizon | IC (t) | Rank IC (t) | Top − bottom quintile, weekly (t) | Hit rate vs weekly median | Rank stability week to week | Mean score | Share of bullish scores |")
    w("|---|---|---|---|---|---|---|---|")
    for lab in _HZ:
        hz = H.get(lab) or {}
        pa, pv = hz.get("production_alpha") or {}, hz.get("production_view") or {}
        if not pa:
            continue
        w(f"| {lab} | {_n(pa.get('ic'))} ({_n(pa.get('ic_t'), 1)}) | {_n(pa.get('rank_ic'))} ({_n(pa.get('rank_t'), 1)}) | {_p(pa.get('quintile_spread'), 2, True)} ({_n(pa.get('quintile_t'), 1)}) | "
          f"{_pc(pa.get('hit_vs_median'))} | {_n(pa.get('rank_stability'), 2)} | {_n(pv.get('mean_raw'), 1)} | {_pc(pv.get('bull_share'))} |")
    w("")
    w("**2. Why does its sign get only ~50–51% absolute directional accuracy?** Because it is centred on zero by construction "
      "while outcomes are not: it calls \"up\" about as often as \"down\", the base rate of positive returns is well above one half at "
      "longer horizons, and most scores sit near zero. Read literally as p_up = (1 + score/100)/2:")
    w("")
    w("| Horizon | Base rate | Production bullish share | |score| < 10 | Accuracy | Balanced accuracy | Bullish precision | Bearish precision | Brier (skill) |")
    w("|---|---|---|---|---|---|---|---|---|")
    for lab in _HZ:
        hz = H.get(lab) or {}
        f = ((hz.get("directional") or {}).get("production (read as p = (1 + raw/100)/2)") or {}).get("walkforward") or {}
        pv = hz.get("production_view") or {}
        if not f:
            continue
        w(f"| {lab} | {_pc(pv.get('base_rate'))} | {_pc(pv.get('bull_share'))} | {_pc(pv.get('near_zero_share'))} | {_pc(f.get('accuracy'))} | {_pc(f.get('balanced_accuracy'))} | "
          f"{_pc(f.get('bull_precision'))} | {_pc(f.get('bear_precision'))} | {_n(f.get('brier'), 4)} ({_p(f.get('brier_skill'), 2, True)}) |")
    w("")
    # 3
    w("**3. What is the unconditional positive-return rate at each horizon?** (all research records / the walk-forward period; by class)")
    w("")
    classes = sorted({c for hz in H.values() for c in ((hz.get("base_rate") or {}).get("by_class") or {})})
    w("| Horizon | All | Walk-forward 2009– | " + " | ".join(classes) + " |")
    w("|---|---|---|" + "---|" * len(classes))
    for lab in _HZ:
        hz = H.get(lab) or {}
        br = hz.get("base_rate") or {}
        if not br:
            continue
        w(f"| {lab} | {_pc(br.get('all'))} | {_pc((hz.get('wf_base_rate') or {}).get('all'))} | " +
          " | ".join(_pc(((br.get('by_class') or {}).get(c) or {}).get('rate')) for c in classes) + " |")
    w("")
    # 4
    w("**4. Which base-return prior works best out of sample?** Each prior alone, calibrated on the training window "
      "(logit p = a + b·μ/σ), walk-forward Brier skill vs climatology (accuracy in brackets):")
    w("")
    w("| Horizon | " + " | ".join(PRIORS) + " | Best |")
    w("|---|" + "---|" * (len(PRIORS) + 1))
    for lab in _HZ:
        D_ = (H.get(lab) or {}).get("directional") or {}
        if not D_:
            continue
        cells, best = [], (None, "—")
        for p in PRIORS:
            wf = (D_.get(f"prior:{p}") or {}).get("walkforward") or {}
            cells.append(f"{_p(wf.get('brier_skill'), 2, True)} ({_pc(wf.get('accuracy'))})")
            if wf.get("brier_skill") is not None and (best[0] is None or wf["brier_skill"] > best[0]):
                best = (wf["brier_skill"], p)
        w(f"| {lab} | " + " | ".join(cells) + f" | {best[1]} |")
    w("")
    # 5
    w("**5. Does Alpha + base return improve absolute directional forecasts?** The main prior alone vs the prior plus the production "
      "score (hierarchy to class), walk-forward on the same records:")
    w("")
    w("| Horizon | Prior alone: accuracy / Brier skill / AUC | Prior + Alpha: accuracy / Brier skill / AUC | Δ accuracy | Δ Brier gain per record | Status |")
    w("|---|---|---|---|---|---|")
    for lab in _HZ:
        D_ = (H.get(lab) or {}).get("directional") or {}
        a, b = (D_.get(f"prior:{MAIN_PRIOR}") or {}).get("walkforward") or {}, D_.get(f"alpha+prior@{MAIN_DIR_DEPTH}") or {}
        bw = b.get("walkforward") or {}
        if not a or not bw:
            continue
        w(f"| {lab} | {_pc(a.get('accuracy'))} / {_p(a.get('brier_skill'), 2, True)} / {_n(a.get('auc'))} | {_pc(bw.get('accuracy'))} / {_p(bw.get('brier_skill'), 2, True)} / {_n(bw.get('auc'))} | "
          f"{_p((bw.get('accuracy') or 0) - (a.get('accuracy') or 0), 2, True)} | {_n((bw.get('brier_gain') or 0) - (a.get('brier_gain') or 0), 5, True)} | {_status(b)} |")
    w("")
    w("(Δ Brier gain: the change in the mean per-record Brier improvement over climatology; each model's own t is in the per-horizon tables.)")
    w("")
    w("Paired on the same walk-forward records (weekly-clustered): does the first model *add* to the second? Brier gain > 0 means "
      "the first is better; *adds* = t ≥ 2.")
    w("")
    w("| Horizon | Comparison | Brier gain (t) | Δ accuracy (t) | AUC | Adds? |")
    w("|---|---|---|---|---|---|")
    for lab in _HZ:
        for pr in (H.get(lab) or {}).get("paired") or []:
            w(f"| {lab} | {pr['a']} vs {pr['b']} | {_n(pr.get('brier_gain'), 5, True)} ({_n(pr.get('brier_t'), 1)}) | {_p(pr.get('delta_acc'), 2, True)} ({_n(pr.get('delta_acc_t'), 1)}) | "
              f"{_n(pr.get('auc_a'))} vs {_n(pr.get('auc_b'))} | {'yes' if pr.get('adds') else 'no'} |")
    w("")
    # 6–11
    w("**6–11. Directional accuracy, naive baseline, excess, balanced accuracy, bearish precision, Brier / calibration** — every "
      "directional model, walk-forward. ECE = expected calibration error over ten probability bins.")
    for lab in _HZ:
        D_ = (H.get(lab) or {}).get("directional") or {}
        if not D_:
            continue
        w("")
        w(f"*{lab}* — naive baseline {_pc((next(iter(D_.values())).get('walkforward') or {}).get('baseline_acc'))} "
          f"({((next(iter(D_.values())).get('walkforward') or {}).get('baseline') or '').replace('_', ' ')})")
        w("")
        w("| Model | Accuracy [95%] | Excess | Balanced | Bull precision / recall | Bear precision / recall | Brier (skill, t) | Log loss | AUC | ECE | Eras won | Status |")
        w("|---|---|---|---|---|---|---|---|---|---|---|---|")
        for k, v in D_.items():
            f = v.get("walkforward") or {}
            if not f.get("n"):
                continue
            w(f"| {k} | {_ci(f.get('accuracy'), f.get('acc_lo'), f.get('acc_hi'))} | {_p(f.get('excess'), 1, True)} | {_pc(f.get('balanced_accuracy'))} | "
              f"{_pc(f.get('bull_precision'))} / {_pc(f.get('bull_recall'))} | {_pc(f.get('bear_precision'))} / {_pc(f.get('bear_recall'))} | "
              f"{_n(f.get('brier'), 4)} ({_p(f.get('brier_skill'), 2, True)}, t {_n(f.get('brier_gain_t'), 1)}) | {_n(f.get('log_loss'), 4)} | {_n(f.get('auc'))} | {_n(f.get('ece'), 3)} | "
              f"{(v.get('gates') or {}).get('eras_won', '—')}/{(v.get('gates') or {}).get('eras_complete', '—')} | {_status(v)} |")
    w("")
    w("**10 (bearish calls). P(R < 0 | DirectionalScore below a threshold)** — best directional model per horizon, against the "
      "unconditional P(R < 0) on the same records:")
    w("")
    w("| Horizon | Model | Unconditional P(R<0) | Score < −20 | Score < −40 | Score < −60 |")
    w("|---|---|---|---|---|---|")
    for lab in _HZ:
        hz = H.get(lab) or {}
        k, b = _best_dir(hz)
        bear = (b.get("walkforward") or {}).get("bearish") or []
        if not bear:
            continue
        cell = lambda x: (f"{_pc(x.get('p_down'))} [{_pc(x.get('lo'))}, {_pc(x.get('hi'))}] <sub>n {x.get('n', 0):,}</sub>" if x.get("p_down") is not None else f"— <sub>n {x.get('n', 0):,}</sub>")  # noqa: E731
        w(f"| {lab} | {k} | {_pc(bear[0].get('unconditional'))} | " + " | ".join(cell(x) for x in bear) + " |")
    w("")
    # 12
    comp = [(lab, m, c) for lab in _HZ for m, c in ((H.get(lab) or {}).get("bear_composition") or {}).items()]
    if comp:
        keys = sorted({k for _, _, c in comp for k in c})
        w("")
        w("Where the bearish calls (score < −20) come from — grouped by the rule that set the product's base prior (own = VXX's "
          "own trailing drift; beta_market includes leveraged / inverse funds) — and P(R < 0) within each:")
        w("")
        w("| Horizon | Model | " + " | ".join(keys) + " |")
        w("|---|---|" + "---|" * len(keys))
        for lab, m, c in comp:
            w(f"| {lab} | {m} | " + " | ".join((f"{c[k]['n']:,} ({_pc(c[k]['p_down'])} down)" if k in c else "—") for k in keys) + " |")
    w("")
    w("**12. Does score strength map monotonically to realized p_up?** Best directional model per horizon: expected p_up → "
      "realized p_up by score band (walk-forward records; n in brackets).")
    w("")
    w("| Horizon | " + " | ".join(f"{lo:g}…{min(hi, 100):g}" for lo, hi in W.BANDS) + " | Monotone? |")
    w("|---|" + "---|" * (len(W.BANDS) + 1))
    for lab in _HZ:
        hz = H.get(lab) or {}
        k, b = _best_dir(hz)
        bands = (b.get("walkforward") or {}).get("bands") or []
        if not bands:
            continue
        real = [x.get("realized_p") for x in bands if x.get("n", 0) >= 50]
        mono = all(b2 >= a2 - 0.01 for a2, b2 in zip(real, real[1:])) if len(real) >= 3 else None
        w(f"| {lab} | " + " | ".join((f"{_pc(x.get('expected_p'), 0)}→{_pc(x.get('realized_p'), 0)} <sub>({x['n']:,})</sub>" if x.get("n") else "—") for x in bands) +
          f" | {'yes' if mono else 'no' if mono is not None else '—'} |")
    w("")
    # 13
    w("**13. Which signal weights differ between the Alpha and the Directional target?** Signal-level challengers at class level: "
      "each signal's share of the global node's |effective weight| (production → alpha challenger → directional challenger); "
      "sign-stable = kept (non-zero, same direction) in every era fit; CV = era-to-era variation of the weight (lower is steadier).")
    for lab in _HZ:
        sg = (H.get(lab) or {}).get("signals") or {}
        if not sg:
            continue
        w("")
        w(f"*{lab}*")
        w("")
        w("| Signal | Production | Alpha challenger | Directional challenger | Alpha sign-stable (CV) | Directional sign-stable (CV) | Supports |")
        w("|---|---|---|---|---|---|---|")
        keys = [s for s in FOCUS_SIGNALS if s in sg] + sorted((s for s in sg if s not in FOCUS_SIGNALS),
                                                              key=lambda s: -max(abs(sg[s].get("alpha") or 0), abs(sg[s].get("directional") or 0)))[:8]
        for s in keys:
            v = sg[s]
            w(f"| {s}{' *' if s in FOCUS_SIGNALS else ''} | {_p(v.get('production'), 1, True)} | {_p(v.get('alpha'), 1, True)} | {_p(v.get('directional'), 1, True)} | "
              f"{'yes' if v.get('alpha_same_sign') else 'no'} ({_n(v.get('alpha_cv'), 2)}) | {'yes' if v.get('dir_same_sign') else 'no'} ({_n(v.get('dir_cv'), 2)}) | {v.get('helps')} |")
    w("")
    w("\\* = the signals the signal-weight report flagged (idiosyncratic volatility, trend, raw momentum).")
    w("")
    # 14
    w("**14. Which hierarchy level performs best for each target?** Walk-forward, per depth.")
    w("")
    w("| Horizon | Target | " + " | ".join(DEPTHS) + " | Best |")
    w("|---|---|" + "---|" * (len(DEPTHS) + 1))
    for lab in _HZ:
        sp = (H.get(lab) or {}).get("specialisation") or {}
        if not sp:
            continue
        a = sp.get("alpha") or {}
        best = max(((v.get("ic") if v.get("ic") is not None else -9), d) for d, v in a.items())[1] if a else "—"
        w(f"| {lab} | Alpha: IC (rank IC) | " + " | ".join(f"{_n((a.get(d) or {}).get('ic'))} ({_n((a.get(d) or {}).get('rank_ic'))})" for d in DEPTHS) + f" | {best} |")
        for key in ("alpha+prior", "signal+prior"):
            d_ = {d: (sp.get("directional") or {}).get(d, {}).get(key) or {} for d in DEPTHS}
            best = max(((v.get("brier_skill") if v.get("brier_skill") is not None else -9), d) for d, v in d_.items())[1]
            w(f"| {lab} | Directional {key}: accuracy (Brier skill) | " + " | ".join(f"{_pc(d_[d].get('accuracy'))} ({_p(d_[d].get('brier_skill'), 2, True)})" for d in DEPTHS) + f" | {best} |")
    w("")
    # 15
    w("**15. Does performance persist across unseen eras?** Best directional model and best alpha challenger per horizon, era by era.")
    w("")
    w("| Horizon | Model | 2009–12 | 2013–16 | 2017–20 | 2021–24 | 2025– | 2018 split |")
    w("|---|---|---|---|---|---|---|---|")
    for lab in _HZ:
        hz = H.get(lab) or {}
        k, b = _best_dir(hz)
        if b:
            cells = [f"acc {_pc(e.get('accuracy'))} vs {_pc(e.get('baseline_acc'))}, Brier gain {_n(e.get('brier_gain'), 4, True)}" if e.get("n") else "—" for e in b.get("eras") or []]
            sp = b.get("split") or {}
            w(f"| {lab} | {k} (directional) | " + " | ".join(cells) + f" | Brier gain {_n(sp.get('brier_gain'), 4, True)} (t {_n(sp.get('brier_gain_t'), 1)}) |")
        ba = hz.get("best_alpha")
        a = (hz.get("alpha") or {}).get(ba) or {}
        if a:
            cells = [f"Δ IC {_ic_t(e.get('delta_ic'))}" if e.get("delta_ic") else "—" for e in a.get("eras") or []]
            w(f"| {lab} | {ba} (alpha) | " + " | ".join(cells) + f" | Δ IC {_ic_t((a.get('split') or {}).get('delta_ic'))} |")
    w("")
    # 16
    shadow = [(lab, k) for lab in _HZ for sec in ("directional", "alpha") for k, v in ((H.get(lab) or {}).get(sec) or {}).items()
              if _status(v) == "SHADOW" and not k.startswith("prior:") and not k.startswith("production")]
    w("**16. Which challengers qualify for live shadow?** " + ("; ".join(f"{lab} {k}" for lab, k in shadow) +
      " — recorded daily next to production (score, p_up, climatology, versions, node, horizon, confidence, maturity), never rewritten."
      if shadow else "None — no directional or alpha challenger passed both historical gates at any horizon."))
    w("")
    # 17
    w("**17. Should Alpha and Directional remain separate?** The test of the hypothesis \"Alpha ranks, Directional calls direction\" "
      "on the same walk-forward records:")
    w("")
    w("| Horizon | Ranking (rank IC on the excess target): production alpha | Ranking: best directional p_up | Direction (accuracy / Brier skill): production read literally | Direction: best directional |")
    w("|---|---|---|---|---|")
    for lab in _HZ:
        hz = H.get(lab) or {}
        k, b = _best_dir(hz)
        f = ((hz.get("directional") or {}).get("production (read as p = (1 + raw/100)/2)") or {})
        if not b:
            continue
        w(f"| {lab} | {_n((f.get('ranking') or {}).get('rank_ic'))} (t {_n((f.get('ranking') or {}).get('rank_t'), 1)}) | "
          f"{_n((b.get('ranking') or {}).get('rank_ic'))} (t {_n((b.get('ranking') or {}).get('rank_t'), 1)}) | "
          f"{_pc((f.get('walkforward') or {}).get('accuracy'))} / {_p((f.get('walkforward') or {}).get('brier_skill'), 2, True)} | "
          f"{_pc((b.get('walkforward') or {}).get('accuracy'))} / {_p((b.get('walkforward') or {}).get('brier_skill'), 2, True)} |")
    w("")
    w("**18. Is anything eligible for production promotion?** No. Promotion needs G1, G2 and G3 (≥ 60 graded live-shadow "
      "forecasts); nothing in this phase has live history, and production is not changed in this phase.")
    w("")
    return "\n".join(L) + "\n"
