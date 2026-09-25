"""ML Lab — deep re-weighting of the Shaffer Score inside its own equation.

Question: which signal, family and scaling weights would have made the point-in-time Shaffer Score more accurate at each
horizon — and do those weights generalise to periods they never saw? This is not a separate model: every challenger is
the Shaffer equation with some of its parameters learned.

Data: the signal-level research records (lab_records version "<score version>s"). For every asset, horizon and weekly
date: each production signal's x = clip(z / S_SCALE, −1, 1); for each active signal its point-in-time direction δ,
weight ω, confidence c, regime factor r and decay d; each family's g = W·A·H/K; the production raw score; and — only
once the horizon has passed — the realised return. They reproduce production exactly:
    raw = 100·tanh( Σ_f g_f Σ_{i∈f} ω_i δ_i x_i c_i r_i d_i )

Challengers (all horizon-specific; Global → Class → Sector → Industry → Asset, each node's ridge shrunk toward its parent):
    signal     index = Σ_i β_i · δ_i x_i c_i r_i d_i         β ≥ 0: learned signal weights ω_i and family weights
                                                              W_f·A·H together; the PIT direction δ is kept
    scaling    index = Σ_i β_i · δ_i x_i c_i^γc r_i^γr d_i^γd the confidence, regime and decay strengths γ chosen in
                                                              each training window by an inner holdout (never on test)
    free       index = Σ_i β_i · x_i                          signed weights on every signal, even inactive ones (more
                                                              parameters: the overfitting check)
    interact   signal + 5 economically motivated interactions: Momentum × high volatility, Valuation × rising rates,
               Growth × rising rates, Credit × recession, Trend × bear market (signed)
    family     index = Σ_f β_f · F_f                          family weights on the production family scores (reference)
Specialisation: the `signal` challenger is also fitted with the hierarchy cut at each depth (global only, … down to
asset) to measure empirically where specialisation stops helping.

Fitting: min Σ(y − index)²·q + λ‖w − w_parent‖² on standardised features, box-bounded (|w| ≤ W_MAX), q = 5/max(5,h)
(overlapping weekly records), λ = SHRINK_K effective observations. y = the vol-scaled forward return.
Challenger raw = 100·tanh(index / s), s matched on training data so the challenger's |score| distribution is the same
as production's (their score bands are then comparable).

Validation — no weight ever sees its test period (training = outcomes matured before the period starts):
    eras         train → 2008 test 2009–12 · → 2012 test 2013–16 · → 2016 test 2017–20 · → 2020 test 2021–24 ·
                 → 2024 test 2025–
    split        train → 2017 (outcomes known before 2018), test 2018– (the earlier protocol, kept)
    live shadow  challengers that pass G1 and G2 are recorded daily in the ledger and graded on the same outcomes
Gates, fixed before any result:
    G1  walk-forward (all eras pooled): paired Δ IC date-clustered t ≥ 2 and Δ directional accuracy ≥ 0
    G2  Δ IC > 0 and Δ accuracy ≥ 0 in at least 3 of the 4 complete eras; the 2018 split's paired Δ IC t ≥ 1; and
        walk-forward accuracy above the best naive baseline
    G3  live shadow (ML Lab, engine/lab.py): ≥ 60 graded forecasts, not worse than production on the same forecasts
Status: INSUFFICIENT DATA · REJECT · SHADOW (G1+G2) · ELIGIBLE FOR PROMOTION (G1–G3). Promotion is manual.

Metrics: directional accuracy sign(score) = sign(return) (score 0 abstains) with a date-clustered 95% CI; accuracy of
the naive baselines on the same records (always bullish, the asset's PIT positive-return frequency, previous-period
direction, 12-1 momentum, 1-month mean reversion); IC (per-asset standardised, pooled by week) and cross-sectional rank
IC (per week); monotonicity and top-minus-bottom decile; accuracy by |score| band; the nine signed score bands.
"""
from __future__ import annotations

import datetime as _dt
import math
import operator
import time
from array import array
from typing import Dict, List, Optional, Tuple

from .. import shaffer_score as cfg
from .lab import INDUSTRY, LAB_HORIZONS, SIG_VERSION, node_path

SHRINK_K = 200.0
W_MAX = 0.2
ERAS = [("2009-01-01", "2013-01-01"), ("2013-01-01", "2017-01-01"), ("2017-01-01", "2021-01-01"), ("2021-01-01", "2025-01-01"),
        ("2025-01-01", "2100-01-01")]
SPLIT = "2018-01-01"
DEPTHS = ["global", "class", "sector", "industry", "asset"]
KINDS = ["signal", "scaling", "free", "interact", "family"]
MAIN_DEPTH = "class"                 # the depth every challenger kind is compared at (fixed in advance)
GAMMAS = [(1, 1, 1), (0, 1, 1), (0.5, 1, 1), (2, 1, 1), (1, 0, 1), (1, 2, 1), (1, 1, 0), (1, 1, 2)]
INTERACTIONS = [("Momentum", "volatility", "high_vol"), ("Valuation", "rates", "rising_rates"), ("Fundamental Growth", "rates", "rising_rates"),
                ("Credit", "growth", "recession"), ("Trend", "market", "bear")]
BANDS = [(-100, -75), (-75, -50), (-50, -25), (-25, -10), (-10, 10), (10, 25), (25, 50), (50, 75), (75, 100.01)]
ABS_BANDS = [(0, 10), (10, 25), (25, 50), (50, 75), (75, 100.01)]
BASELINES = ["always_bullish", "positive_frequency", "previous_direction", "momentum", "mean_reversion"]


# ------------------------------------------------------------------ data
def signals() -> List[str]:
    return [s for f, sg in cfg.FAMILIES.items() for s, _ in sg]


def families() -> List[str]:
    return list(cfg.FAMILIES)


class Rec:
    __slots__ = ("asset", "meta", "date", "end", "wk", "raw", "y", "yr", "x", "act", "g", "reg", "base", "score")


def _week(d: str) -> int:
    return _dt.date.fromisoformat(d).toordinal() // 7


def load(store, research, lab: str) -> List[Rec]:
    """Signal-level records for one horizon, compact, with PIT regime states and the naive baselines' calls."""
    sigs, fams = signals(), families()
    si = {s: i for i, s in enumerate(sigs)}
    fi = {f: i for i, f in enumerate(fams)}
    metas = {a["id"]: a for a in store.assets()}
    panel = research.panel()
    cal = panel.calendar()
    idx = {d: i for i, d in enumerate(cal)}
    h = dict(LAB_HORIZONS)[lab]
    regs = research.regimes()
    dims = ("market", "volatility", "rates", "growth")
    out: List[Rec] = []
    for blob in store.lab_records(SIG_VERSION, lab):
        a = blob["asset_id"]
        meta = metas.get(a) or {"id": a}
        try:
            px = panel.series(a)
        except Exception:  # noqa: BLE001
            px = []
        mine = []
        for d, raw, y, yr, sig in blob["rows"]:
            i = idx.get(d)
            if i is None or not sig:
                continue
            r = Rec()
            r.asset, r.meta, r.date, r.end, r.wk = a, meta, d, cal[min(len(cal) - 1, i + h)], _week(d)
            r.raw, r.y, r.yr = raw, y, yr
            x = array("f", bytes(4 * len(sigs)))
            for s_, v in (sig.get("x") or {}).items():
                j = si.get(s_)
                if j is not None:
                    x[j] = v
            r.x = x
            r.act = tuple((si[s_], v[0], v[1], v[2], v[3], v[4]) for s_, v in (sig.get("a") or {}).items() if s_ in si)
            g = array("f", bytes(4 * len(fams)))
            for f, v in (sig.get("g") or {}).items():
                if f in fi:
                    g[fi[f]] = v
            r.g = g
            r.reg = tuple((regs.get(dm) or [None] * len(cal))[i] for dm in dims)
            # naive baselines (all known at t)
            def lr(a_, b_):
                return math.log(px[a_] / px[b_]) if 0 <= b_ < len(px) and a_ < len(px) and px[a_] and px[b_] else None
            prev, mom, mr = lr(i, i - h), lr(i - 21, i - 252), lr(i, i - 21)
            r.base = [1.0, None, (1.0 if prev > 0 else -1.0) if prev else None, (1.0 if mom > 0 else -1.0) if mom else None,
                      (-1.0 if mr > 0 else 1.0) if mr else None]
            mine.append(r)
        # the asset's PIT frequency of positive outcomes: only outcomes that had matured before t
        mine.sort(key=lambda r: r.date)
        ends = sorted(mine, key=lambda r: r.end)
        k = pos = n = 0
        for r in mine:
            while k < len(ends) and ends[k].end < r.date:
                n += 1; pos += ends[k].yr > 0; k += 1
            r.base[1] = ((1.0 if pos / n > 0.5 else -1.0) if n >= 20 else None)
        out.extend(mine)
    out.sort(key=lambda r: (r.date, r.asset))
    return out


# ------------------------------------------------------------------ features
def _fam_scores(r: Rec, gam=(1, 1, 1)) -> Dict[int, float]:
    fam_of = _FAMIDX
    out: Dict[int, float] = {}
    for j, dl, om, c, rr, d in r.act:
        f = fam_of[j]
        out[f] = out.get(f, 0.0) + om * dl * r.x[j] * (c ** gam[0]) * (rr ** gam[1]) * (d ** gam[2])
    return out


_FAMIDX: List[int] = []


def _init_famidx():
    global _FAMIDX
    fams = families()
    _FAMIDX = [fams.index(cfg.FAMILY_OF[s]) for s in signals()]


def n_features(kind: str) -> int:
    p = len(signals())
    return {"signal": p, "scaling": p, "free": p, "interact": p + len(INTERACTIONS), "family": len(families())}[kind]


def features(r: Rec, kind: str, gam=(1, 1, 1)) -> List[Tuple[int, float]]:
    if kind in ("signal", "scaling", "interact"):
        f = [(j, dl * r.x[j] * (c ** gam[0]) * (rr ** gam[1]) * (d ** gam[2])) for j, dl, om, c, rr, d in r.act if r.x[j]]
        if kind == "interact":
            fs = _fam_scores(r)
            fams = families()
            dims = {"market": 0, "volatility": 1, "rates": 2, "growth": 3}
            p = len(signals())
            for k, (fam, dm, state) in enumerate(INTERACTIONS):
                v = fs.get(fams.index(fam), 0.0) if r.reg[dims[dm]] == state else 0.0
                if v:
                    f.append((p + k, v))
        return sorted(f)
    if kind == "free":
        return [(j, float(v)) for j, v in enumerate(r.x) if v]
    if kind == "family":
        return sorted((k, v) for k, v in _fam_scores(r).items() if v)
    raise ValueError(kind)


def bounds(kind: str, j: int) -> Tuple[float, float]:
    if kind == "free" or (kind == "interact" and j >= len(signals())):
        return -W_MAX, W_MAX
    return 0.0, W_MAX


# ------------------------------------------------------------------ packed sufficient statistics
class PStats:
    __slots__ = ("P", "xx", "xy", "n", "off")

    def __init__(self, P: int):
        self.P, self.n = P, 0
        self.xx = array("d", bytes(8 * (P * (P + 1) // 2)))
        self.xy = array("d", bytes(8 * P))
        self.off = [i * P - i * (i - 1) // 2 for i in range(P)]

    def add(self, feats, y):
        self.n += 1
        xx, xy, off = self.xx, self.xy, self.off
        for a, (i, vi) in enumerate(feats):
            xy[i] += vi * y
            base = off[i] - i
            for j, vj in feats[a:]:
                xx[base + j] += vi * vj

    def merge(self, o: "PStats"):
        self.n += o.n
        self.xx = array("d", map(operator.add, self.xx, o.xx))
        self.xy = array("d", map(operator.add, self.xy, o.xy))

    def copy(self) -> "PStats":
        c = PStats.__new__(PStats)
        c.P, c.n, c.off, c.xx, c.xy = self.P, self.n, self.off, array("d", self.xx), array("d", self.xy)
        return c

    def get(self, i, j):
        if i > j:
            i, j = j, i
        return self.xx[self.off[i] - i + j]


def solve_box(st: PStats, rms, prior, q: float, lam: float, lo, hi, iters: int = 40, tol: float = 1e-7) -> List[float]:
    """min ½wᵀAw − bᵀw, lo ≤ w ≤ hi, A = q·S/(rms rmsᵀ) + λI, b = q·Xᵀy/rms + λ·prior (coordinate descent)."""
    P = st.P
    act = [i for i in range(P) if rms[i] > 1e-12]
    w = [0.0] * P
    if not act:
        return w
    A = {i: [q * st.get(i, j) / (rms[i] * rms[j]) + (lam if i == j else 0.0) for j in act] for i in act}
    b = {i: q * st.xy[i] / rms[i] + lam * prior[i] for i in act}
    pos = {i: k for k, i in enumerate(act)}
    for i in act:
        w[i] = min(hi[i], max(lo[i], prior[i]))
    Aw = {i: sum(A[i][pos[j]] * w[j] for j in act) for i in act}
    for _ in range(iters):
        md = 0.0
        for i in act:
            aii = A[i][pos[i]]
            g = b[i] - Aw[i] + aii * w[i]
            new = min(hi[i], max(lo[i], g / aii))
            dlt = new - w[i]
            if dlt:
                row = A[i]
                for j in act:
                    Aw[j] += A[j][pos[i]] * dlt
                w[i] = new
                md = max(md, abs(dlt))
        if md < tol:
            break
    return w


def _cut(path: List[str], depth: str) -> List[str]:
    keep = {"global": ("global",), "class": ("global", "class:"), "sector": ("global", "class:", "sector:"),
            "industry": ("global", "class:", "sector:", "industry:"), "asset": ("global", "class:", "sector:", "industry:", "asset:")}[depth]
    return [n for n in path if n == "global" or n.startswith(keep[1:] or ("§",))]


class Fitted:
    """Weights per node for one training cutoff, plus standardisation and the score scale."""
    __slots__ = ("W", "rms", "scale", "n")

    def weights(self, path: List[str]) -> Optional[List[float]]:
        for n in reversed(path):
            if n in self.W:
                return self.W[n]
        return None


def fit_nodes(node_st: Dict[str, PStats], parents: Dict[str, str], kind: str, h: int) -> Tuple[Dict[str, List[float]], List[float]]:
    g = node_st.get("global")
    P = n_features(kind)
    if g is None or g.n < 50:
        return {}, [0.0] * P
    rms = [math.sqrt(g.get(i, i) / g.n) if g.get(i, i) > 0 else 0.0 for i in range(P)]
    lo = [bounds(kind, i)[0] for i in range(P)]
    hi = [bounds(kind, i)[1] for i in range(P)]
    q = 5.0 / max(5.0, float(h))
    W: Dict[str, List[float]] = {}
    for node in sorted(node_st, key=lambda k: (k != "global", k.count(":") and ["class", "sector", "industry", "asset"].index(k.split(":")[0]), k)):
        prior = [0.0] * P if node == "global" else W.get(parents.get(node, "global"), W.get("global", [0.0] * P))
        W[node] = solve_box(node_st[node], rms, prior, q, SHRINK_K, lo, hi)
    return W, rms


def _index(w, rms, feats) -> float:
    return sum(w[j] * v / rms[j] for j, v in feats if rms[j] > 1e-12)


# ------------------------------------------------------------------ metrics
def _clustered_mean(vals: Dict[int, List[float]], h: int) -> dict:
    if len(vals) < 10:
        return {"mean": None, "se": None, "weeks": len(vals)}
    ms = [sum(v) / len(v) for _, v in sorted(vals.items())]
    n = len(ms)
    m = sum(ms) / n
    sd = math.sqrt(sum((x - m) ** 2 for x in ms) / max(1, n - 1))
    n_eff = n * 5.0 / max(5.0, float(h))
    return {"mean": m, "se": sd / math.sqrt(max(1.0, n_eff)), "weeks": n}


def _std_prods(rows, key) -> Dict[str, List[Tuple[int, float]]]:
    by: Dict[str, list] = {}
    for r in rows:
        by.setdefault(r.asset, []).append(r)
    out = {}
    for a, rs in by.items():
        xs = [key(r) for r in rs]
        ys = [r.y for r in rs]
        if len(rs) < 20:
            continue
        mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
        sx = math.sqrt(sum((v - mx) ** 2 for v in xs) / len(xs))
        sy = math.sqrt(sum((v - my) ** 2 for v in ys) / len(ys))
        if sx <= 1e-12 or sy <= 1e-12:
            continue
        out[a] = [(r.wk, (key(r) - mx) / sx * (r.y - my) / sy) for r in rs]
    return out


def _ic(prods, h) -> dict:
    by: Dict[int, List[float]] = {}
    for rs in prods.values():
        for w, v in rs:
            by.setdefault(w, []).append(v)
    c = _clustered_mean(by, h)
    return {"ic": c["mean"], "t": (c["mean"] / c["se"]) if c["mean"] is not None and c["se"] else None, "weeks": c["weeks"]}


def _rank_ic(rows, key, h) -> dict:
    by: Dict[int, list] = {}
    for r in rows:
        by.setdefault(r.wk, []).append((key(r), r.yr))
    vals = {}
    for w, pairs in by.items():
        if len(pairs) < 8:
            continue
        rx = _ranks([p[0] for p in pairs]); ry = _ranks([p[1] for p in pairs])
        n = len(pairs)
        mx = sum(rx) / n; my = sum(ry) / n
        sx = math.sqrt(sum((a - mx) ** 2 for a in rx)); sy = math.sqrt(sum((b - my) ** 2 for b in ry))
        if sx > 0 and sy > 0:
            vals[w] = [sum((a - mx) * (b - my) for a, b in zip(rx, ry)) / (sx * sy)]
    c = _clustered_mean(vals, h)
    return {"rank_ic": c["mean"], "t": (c["mean"] / c["se"]) if c["mean"] is not None and c["se"] else None, "weeks": c["weeks"]}


def _ranks(xs):
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    r = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        for k in range(i, j + 1):
            r[order[k]] = (i + j) / 2.0
        i = j + 1
    return r


def _accuracy(rows, key, h) -> dict:
    by: Dict[int, List[float]] = {}
    n = 0
    for r in rows:
        s = key(r)
        if not s or r.yr == 0:
            continue
        n += 1
        by.setdefault(r.wk, []).append(1.0 if (s > 0) == (r.yr > 0) else 0.0)
    c = _clustered_mean(by, h)
    if c["mean"] is None:
        return {"acc": None, "n": n}
    return {"acc": c["mean"], "lo": c["mean"] - 1.96 * c["se"], "hi": c["mean"] + 1.96 * c["se"], "n": n, "weeks": c["weeks"],
            "coverage": n / max(1, len(rows))}


def _paired_acc(rows, ka, kb, h) -> dict:
    by: Dict[int, List[float]] = {}
    for r in rows:
        a, b = ka(r), kb(r)
        if not a or not b or r.yr == 0:
            continue
        ca = 1.0 if (a > 0) == (r.yr > 0) else 0.0
        cb = 1.0 if (b > 0) == (r.yr > 0) else 0.0
        by.setdefault(r.wk, []).append(ca - cb)
    c = _clustered_mean(by, h)
    if c["mean"] is None:
        return {"delta": None}
    return {"delta": c["mean"], "lo": c["mean"] - 1.96 * c["se"], "hi": c["mean"] + 1.96 * c["se"], "t": c["mean"] / c["se"] if c["se"] else None}


def _bands(rows, key, h, bands=BANDS, absolute=False) -> List[dict]:
    out = []
    for lo, hi in bands:
        sel = [r for r in rows if lo <= (abs(key(r)) if absolute else key(r)) < hi]
        if not sel:
            out.append({"band": f"{lo:g}..{min(hi, 100):g}", "n": 0})
            continue
        rets = sorted(r.yr for r in sel)
        acc = _accuracy(sel, key, h)
        weeks = len({r.wk for r in sel})
        out.append({"band": f"{lo:g}..{min(hi, 100):g}", "n": len(sel), "independent": weeks * 5.0 / max(5.0, float(h)),
                    "positive": sum(1 for x in rets if x > 0) / len(rets), "mean": math.expm1(sum(rets) / len(rets)),
                    "median": math.expm1(rets[len(rets) // 2]), "correct": acc.get("acc"), "lo": acc.get("lo"), "hi": acc.get("hi")})
    return out


def _mono(rows, key, k=10) -> Optional[float]:
    if len(rows) < 50 * k:
        return None
    order = sorted(rows, key=key)
    size = len(order) // k
    means = [sum(r.y for r in order[j * size:(j + 1) * size]) / size for j in range(k)]
    rk = sorted(range(k), key=lambda j: means[j])
    rr = [0] * k
    for p_, j in enumerate(rk):
        rr[j] = p_
    return 1 - 6 * sum((j - rr[j]) ** 2 for j in range(k)) / (k * (k * k - 1))


def metrics(rows: List[Rec], key, h: int, with_bands: bool = False) -> dict:
    if not rows:
        return {"n": 0}
    pr = _std_prods(rows, key)
    out = {"n": len(rows), "assets": len(pr), **_accuracy(rows, key, h), **_ic(pr, h), **{k: v for k, v in _rank_ic(rows, key, h).items() if k != "weeks"},
           "monotonicity": _mono(rows, key)}
    out["rank_t"] = out.pop("t", None) if False else _rank_ic(rows, key, h)["t"]
    out["ic_t"] = _ic(pr, h)["t"]
    order = sorted(rows, key=key)
    dec = max(1, len(order) // 10)
    out["top_minus_bottom"] = (math.expm1(sum(r.yr for r in order[-dec:]) / dec) - math.expm1(sum(r.yr for r in order[:dec]) / dec)) if len(order) >= 100 else None
    out["abs_bands"] = _bands(rows, key, h, ABS_BANDS, absolute=True)
    if with_bands:
        out["bands"] = _bands(rows, key, h)
    return out


def baselines(rows: List[Rec], h: int) -> dict:
    out = {}
    for k, name in enumerate(BASELINES):
        out[name] = _accuracy(rows, lambda r, k=k: r.base[k], h)
    best = max((v.get("acc") or 0.0, k) for k, v in out.items())
    return {"by": out, "best": best[1], "best_acc": best[0]}


def compare(rows: List[Rec], h: int, with_bands=False) -> dict:
    """Challenger (r.score) vs production (r.raw) on exactly the same records, plus the naive baselines."""
    if not rows:
        return {"n": 0}
    P = metrics(rows, lambda r: r.raw, h, with_bands)
    C = metrics(rows, lambda r: r.score, h, with_bands)
    pp, pc = _std_prods(rows, lambda r: r.raw), _std_prods(rows, lambda r: r.score)
    diff = {}
    for a in set(pp) & set(pc):
        mb = dict(pp[a])
        diff[a] = [(w, v - mb[w]) for w, v in pc[a] if w in mb]
    d_ic = _ic(diff, h)
    conc = None
    if diff:
        per = sorted(((sum(v for _, v in rs), a) for a, rs in diff.items()), reverse=True)
        tot = sum(x for x, _ in per if x > 0)
        conc = {"top_asset": per[0][1], "share": (per[0][0] / tot) if tot > 0 else None}
    return {"n": len(rows), "production": P, "challenger": C, "delta_ic": d_ic, "delta_acc": _paired_acc(rows, lambda r: r.score, lambda r: r.raw, h),
            "baselines": baselines(rows, h), "concentration": conc}


# ------------------------------------------------------------------ one challenger over every era
def _periods():
    cuts = sorted({s for s, _ in ERAS} | {SPLIT})
    return cuts


def _period_of(end: str, cuts) -> int:
    k = 0
    while k < len(cuts) and end >= cuts[k]:
        k += 1
    return k                      # 0 = before the first cut


def run_challenger(recs: List[Rec], h: int, kind: str, depth: str, gam=(1, 1, 1), select_gamma: bool = False) -> dict:
    """Fit on outcomes matured before each era / the split / everything, predict the held-out records."""
    cuts = _periods()
    paths = [_cut(node_path(r.meta, "hier"), depth) for r in recs]
    parents = {}
    for pth in paths:
        for a, b in zip(pth, pth[1:]):
            parents[b] = a

    def fit_until(cut: str, gamma) -> Optional[Fitted]:
        P = n_features(kind)
        node: Dict[str, PStats] = {}
        train = []
        for r, pth in zip(recs, paths):
            if r.end >= cut:
                continue
            f = features(r, kind, gamma)
            train.append((r, pth, f))
            for nd in pth:
                st = node.get(nd)
                if st is None:
                    st = node[nd] = PStats(P)
                st.add(f, r.y)
        if not node.get("global") or node["global"].n < 500:
            return None
        W, rms = fit_nodes(node, parents, kind, h)
        ft = Fitted()
        ft.W, ft.rms, ft.n = W, rms, node["global"].n
        # scale: match the challenger's median |index| to production's median |raw| on the training records
        idxs = sorted(abs(_index(ft.weights(pth) or [0.0] * P, rms, f)) for _, pth, f in train[-20000:])
        raws = sorted(abs(r.raw) for r, _, _ in train[-20000:])
        mi, mr = idxs[len(idxs) // 2], min(99.0, raws[len(raws) // 2])
        ft.scale = (mi / math.atanh(mr / 100.0)) if mi > 0 and mr > 0 else 1.0
        return ft

    def choose_gamma(cut: str):
        """Inner holdout inside the training window: fit on outcomes before cut − 4 years, judge IC on the last 4."""
        if not select_gamma:
            return gam
        inner = f"{int(cut[:4]) - 4}{cut[4:]}"
        best, bg = None, (1, 1, 1)
        for g in GAMMAS:
            ft = fit_until(inner, g)
            if ft is None:
                continue
            val = [r for r, pth in zip(recs, paths) if inner <= r.date and r.end < cut]
            for r, pth in zip(recs, paths):
                pass
            if len(val) < 500:
                continue
            sub = []
            for r in val:
                w = ft.weights(_cut(node_path(r.meta, "hier"), depth))
                r.score = 100 * math.tanh(_index(w, ft.rms, features(r, kind, g)) / ft.scale) if w else 0.0
                sub.append(r)
            ic = _ic(_std_prods(sub, lambda r: r.score), h)["ic"]
            if ic is not None and (best is None or ic > best):
                best, bg = ic, g
        return bg

    def predict(ft: Fitted, gamma, rows_paths) -> List[Rec]:
        out = []
        for r, pth in rows_paths:
            w = ft.weights(pth)
            if w is None:
                continue
            r2 = Rec()
            for s_ in Rec.__slots__:
                if s_ != "score":
                    setattr(r2, s_, getattr(r, s_))
            r2.score = 100 * math.tanh(_index(w, ft.rms, features(r, kind, gamma)) / ft.scale)
            out.append(r2)
        return out

    res = {"kind": kind, "depth": depth, "eras": [], "n_params": n_features(kind)}
    wf_rows: List[Rec] = []
    era_w = []
    for (a, b) in ERAS:
        g = choose_gamma(a)
        ft = fit_until(a, g)
        test = [(r, pth) for r, pth in zip(recs, paths) if a <= r.date < b]
        if ft is None or len(test) < 200:
            res["eras"].append({"from": a, "to": b, "status": "insufficient", "train": ft.n if ft else 0, "test": len(test)})
            continue
        rows = predict(ft, g, test)
        wf_rows.extend(rows)
        cmp_ = compare(rows, h)
        res["eras"].append({"from": a, "to": b, "train": ft.n, "test": len(rows), "gamma": list(g), **cmp_})
        era_w.append((a, ft))
    res["walkforward"] = compare(wf_rows, h, with_bands=True) if wf_rows else {"n": 0}
    g = choose_gamma(SPLIT)
    ft = fit_until(SPLIT, g)
    if ft:
        res["split"] = compare(predict(ft, g, [(r, pth) for r, pth in zip(recs, paths) if r.date >= SPLIT]), h)
    gF = choose_gamma("2100-01-01")
    final = fit_until("2100-01-01", gF)
    res["gamma"] = list(gF)
    res["final"] = {"n": final.n, "scale": final.scale, "rms": final.rms, "weights": final.W} if final else None
    res["era_weights"] = {a: ft.W.get("global") for a, ft in era_w}
    res["era_rms"] = {a: ft.rms for a, ft in era_w}
    res["gates"] = gates(res)
    res["research_score"] = research_score(res)
    return res


def gates(res: dict) -> dict:
    wf = res.get("walkforward") or {}
    d_ic, d_acc = wf.get("delta_ic") or {}, wf.get("delta_acc") or {}
    full = [e for e in res["eras"][:4] if e.get("delta_ic")]
    won = sum(1 for e in full if (e["delta_ic"].get("ic") or 0) > 0 and (e["delta_acc"].get("delta") or 0) >= 0)
    sp = (res.get("split") or {}).get("delta_ic") or {}
    b = (wf.get("baselines") or {})
    ch_acc = ((wf.get("challenger") or {}).get("acc"))
    enough = len(full) >= 3 and (wf.get("n") or 0) >= 2000
    g1 = bool(enough and (d_ic.get("t") or 0) >= 2 and (d_acc.get("delta") or 0) >= 0)
    g2 = bool(enough and won >= 3 and (sp.get("t") or 0) >= 1 and ch_acc is not None and ch_acc > (b.get("best_acc") or 1.0))
    status = "INSUFFICIENT DATA" if not enough else ("SHADOW" if g1 and g2 else "REJECT")
    return {"G1_walkforward": g1, "G2_eras": g2, "eras_won": won, "eras_complete": len(full), "status": status}


def research_score(res: dict) -> dict:
    """Multi-metric ranking of a challenger (for reporting, not a gate): evidence minus complexity and instability."""
    wf = res.get("walkforward") or {}
    t_ic = (wf.get("delta_ic") or {}).get("t") or 0.0
    t_acc = (wf.get("delta_acc") or {}).get("t") or 0.0
    full = [e for e in res["eras"][:4] if e.get("delta_ic")]
    won = sum(1 for e in full if (e["delta_ic"].get("ic") or 0) > 0) / max(1, len(full))
    ew = [w for w in (res.get("era_weights") or {}).values() if w]
    flips = turnover = 0.0
    if len(ew) >= 2:
        P = len(ew[0])
        live = [i for i in range(P) if any(abs(w[i]) > 1e-6 for w in ew)]
        flips = sum(1 for i in live if len({(w[i] > 1e-6) - (w[i] < -1e-6) for w in ew} - {0}) > 1) / max(1, len(live))
        turnover = sum(sum(abs(a - b) for a, b in zip(w1, w2)) for w1, w2 in zip(ew, ew[1:])) / max(1e-9, sum(sum(abs(v) for v in w) for w in ew[1:]))
    conc = ((wf.get("concentration") or {}).get("share") or 0.0)
    comp = math.log10(max(1, res.get("n_params") or 1))
    score = t_ic + t_acc + 2 * won - 0.5 * comp - 2 * flips - turnover - (1.0 if conc > 0.5 else 0.0)
    return {"score": score, "t_ic": t_ic, "t_acc": t_acc, "eras_won_share": won, "sign_flips": flips, "turnover": turnover,
            "concentration": conc, "complexity": comp}


# ------------------------------------------------------------------ weights: production vs challenger, stability
def effective_weights(recs: List[Rec], res: dict, node_filter=None) -> Dict[str, dict]:
    """Average effective coefficient on each signal's x = clip(z/2, −1, 1), production vs the challenger's final fit, per
    hierarchy node (global, each class / sector / industry, and assets): what each one actually does with the signal."""
    sigs = signals()
    fin = res.get("final")
    if not fin:
        return {}
    kind, depth = res["kind"], res["depth"]
    acc: Dict[str, List[list]] = {}
    for r in recs:
        pth = _cut(node_path(r.meta, "hier"), depth)
        nodes = [n for n in node_path(r.meta, "hier") if node_filter is None or node_filter(n)]
        prod = [0.0] * len(sigs)
        for j, dl, om, c, rr, d in r.act:
            prod[j] = r.g[_FAMIDX[j]] * om * dl * c * rr * d
        w = None
        for n in reversed(pth):
            if n in fin["weights"]:
                w = fin["weights"][n]; break
        ch = [0.0] * len(sigs)
        if w:
            if kind == "free":
                ch = [w[j] / fin["rms"][j] if fin["rms"][j] > 1e-12 else 0.0 for j in range(len(sigs))]
            elif kind in ("signal", "scaling", "interact"):
                gm = res.get("gamma") or [1, 1, 1]
                for j, dl, om, c, rr, d in r.act:
                    if fin["rms"][j] > 1e-12:
                        ch[j] = w[j] / fin["rms"][j] * dl * (c ** gm[0]) * (rr ** gm[1]) * (d ** gm[2])
        for n in nodes:
            a = acc.setdefault(n, [[0.0] * len(sigs), [0.0] * len(sigs), 0])
            for j in range(len(sigs)):
                a[0][j] += prod[j]; a[1][j] += ch[j]
            a[2] += 1
    out = {}
    for n, (p, c, k) in acc.items():
        if k < 100:
            continue
        pm = [v / k for v in p]; cm = [v / (k * fin["scale"]) for v in c]
        sp, sc = sum(abs(v) for v in pm) or 1.0, sum(abs(v) for v in cm) or 1.0
        out[n] = {"records": k, "signals": {sigs[j]: {"production": pm[j] / sp, "challenger": cm[j] / sc} for j in range(len(sigs)) if pm[j] or cm[j]}}
    return out


def stability(res: dict) -> Dict[str, dict]:
    """Per signal: the challenger's global weight in each era fit — same sign every era, and how much it moves."""
    sigs = signals()
    ew = res.get("era_weights") or {}
    er = res.get("era_rms") or {}
    out = {}
    for j, s in enumerate(sigs):
        vals = [(w[j] / er[a][j]) if w and er.get(a) and er[a][j] > 1e-12 else 0.0 for a, w in ew.items()]
        if not any(vals):
            continue
        nz = [v for v in vals if abs(v) > 1e-9]
        out[s] = {"eras": vals, "same_sign": len({v > 0 for v in nz}) <= 1 and len(nz) == len(vals), "mean": sum(vals) / len(vals)}
    return out


# ------------------------------------------------------------------ the whole study
def study_horizon(store, research, lab: str, progress=None, kinds=None, depths=None) -> dict:
    say = progress or (lambda m: None)
    _init_famidx()
    h = dict(LAB_HORIZONS)[lab]
    t0 = time.time()
    recs = load(store, research, lab)
    out = {"horizon": lab, "records": len(recs), "assets": len({r.asset for r in recs}),
           "first": recs[0].date if recs else None, "last": recs[-1].date if recs else None, "challengers": {}, "specialisation": {}}
    if len(recs) < 5000:
        out["reason"] = "not enough signal-level research records (python -m finsim2 lab --build)"
        return out
    say(f"{lab}: {len(recs)} records loaded ({time.time() - t0:.0f}s)")
    for kd in (kinds or KINDS):
        say(f"{lab}: {kd} @ {MAIN_DEPTH}")
        out["challengers"][f"{kd}@{MAIN_DEPTH}"] = run_challenger(recs, h, kd, MAIN_DEPTH, select_gamma=(kd == "scaling"))
    for dp in (depths or DEPTHS):
        key = f"signal@{dp}"
        if key not in out["challengers"]:
            say(f"{lab}: specialisation {dp}")
            out["challengers"][key] = run_challenger(recs, h, "signal", dp)
        wf = out["challengers"][key].get("walkforward") or {}
        out["specialisation"][dp] = {"acc": (wf.get("challenger") or {}).get("acc"), "ic": (wf.get("challenger") or {}).get("ic"),
                                     "production_acc": (wf.get("production") or {}).get("acc"), "production_ic": (wf.get("production") or {}).get("ic"),
                                     "delta_acc": (wf.get("delta_acc") or {}).get("delta"), "delta_ic": (wf.get("delta_ic") or {}).get("ic"),
                                     "by_class": _by_class(out["challengers"][key], recs, h)}
    # the best challenger by the multi-metric research score (among those not INSUFFICIENT)
    ranked = sorted(((c["research_score"]["score"], k) for k, c in out["challengers"].items() if c["gates"]["status"] != "INSUFFICIENT DATA"), reverse=True)
    out["best"] = ranked[0][1] if ranked else None
    if out["best"]:
        b = out["challengers"][out["best"]]
        out["weights"] = effective_weights(recs, b)
        out["stability"] = stability(b)
    out["production_all"] = metrics(recs, lambda r: r.raw, h, with_bands=True)
    out["baselines_all"] = baselines(recs, h)
    out["seconds"] = round(time.time() - t0, 1)
    for c in out["challengers"].values():          # the per-node weights live with the registered version, not here
        if c.get("final"):
            c["final_n"] = c["final"]["n"]
    return out


def _by_class(ch: dict, recs, h) -> dict:
    wf_eras = ch.get("eras") or []
    out = {}
    for e in wf_eras:
        pass
    return out


def slim(res: dict) -> dict:
    """The research result without per-node weights (for kv / the UI)."""
    out = dict(res)
    out["challengers"] = {k: {kk: vv for kk, vv in c.items() if kk not in ("final", "era_rms")} for k, c in (res.get("challengers") or {}).items()}
    return out


def _horizon_worker(db_path: str, lab: str) -> Tuple[str, dict, dict]:
    from ..data.store import Store
    from .research import Research
    st = Store(db_path)
    try:
        res = study_horizon(st, Research(st), lab, progress=lambda m: print(m, flush=True))
        finals = {k: c.get("final") for k, c in (res.get("challengers") or {}).items() if c.get("final")}
        return lab, slim(res), finals
    finally:
        st.close()


def run_all(db_path: str, workers: int = 4, progress=None) -> dict:
    from concurrent.futures import ProcessPoolExecutor, as_completed
    from ..data.store import Store
    say = progress or (lambda m: None)
    t0 = time.time()
    out = {"started": time.strftime("%Y-%m-%d %H:%M:%S"), "score_version": cfg.VERSION, "eras": ERAS, "split": SPLIT, "shrink_k": SHRINK_K,
           "w_max": W_MAX, "main_depth": MAIN_DEPTH, "signals": signals(), "horizons": {}}
    finals: Dict[str, dict] = {}
    with ProcessPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(_horizon_worker, db_path, lab) for lab, _ in LAB_HORIZONS]
        for f in as_completed(futs):
            lab, res, fin = f.result()
            out["horizons"][lab] = res
            finals[lab] = fin
            say(f"{lab} done: best {res.get('best')}")
    out["seconds"] = round(time.time() - t0, 1)
    st = Store(db_path)
    try:
        out["registered"] = register(st, out, finals)
        st.kv_set(RESEARCH_KEY, out)
    finally:
        st.close()
    return out


RESEARCH_KEY = "lab:weights"


def register(store, res: dict, finals: Dict[str, dict]) -> List[str]:
    """One challenger version per kind@depth (all horizons), with frozen final weights, gamma, scale and validation."""
    from .lab import _save_registry, registry
    reg = registry(store)
    made = []
    keys = sorted({k for hz in res["horizons"].values() for k in (hz.get("challengers") or {})})
    for key in keys:
        kind, depth = key.split("@")
        vid = f"shaffer-{cfg.VERSION}-sig-{kind}-{depth}-exp"
        old = next((x for x in reg["versions"] if x["id"] == vid), None)
        if old and old.get("status") not in ("challenger", None):
            continue
        per = {lab: hz["challengers"][key] for lab, hz in res["horizons"].items() if key in (hz.get("challengers") or {})}
        store.kv_set(f"formula:weights:{vid}", {lab: {"kind": kind, "depth": depth, "gamma": c.get("gamma"), **(finals.get(lab, {}).get(key) or {})}
                                                 for lab, c in per.items()})
        reg["versions"] = [x for x in reg["versions"] if x["id"] != vid]
        reg["versions"].append({
            "id": vid, "kind": "shaffer", "status": "challenger", "parent": f"shaffer-{cfg.VERSION}", "variant": f"sig-{kind}-{depth}",
            "family": "signal-weights", "introduced": (old or {}).get("introduced") or time.strftime("%Y-%m-%d"),
            "live_shadow_from": (old or {}).get("live_shadow_from") or time.strftime("%Y-%m-%d"),
            "description": f"Shaffer equation with learned {kind} weights, hierarchy to {depth}",
            "formula": {"signal": "Σ β_i δ_i x_i c_i r_i d_i", "scaling": "Σ β_i δ_i x_i c_i^γc r_i^γr d_i^γd", "free": "Σ β_i x_i",
                        "interact": "Σ β_i δ_i x_i c_i r_i d_i + Σ β_k F_k·1[regime]", "family": "Σ β_f F_f"}[kind],
            "training_cutoff": res.get("started"),
            "validation": {lab: {"gates": {"G1_discovery": c["gates"]["G1_walkforward"], "G2_confirmation": c["gates"]["G2_eras"]},
                                 "status": c["gates"]["status"]} for lab, c in per.items()}})
        made.append(vid)
    _save_registry(store, reg)
    return made


# ------------------------------------------------------------------ live shadow + hedge reliability
def live_score(store, vid: str, meta: dict, lab: str, live: dict, regime: Dict[str, Optional[str]]) -> Optional[float]:
    """A signal-level challenger's score today, from the same pieces production used today."""
    _init_famidx()
    wts = (store.kv_get(f"formula:weights:{vid}") or {}).get(lab)
    if not wts or not wts.get("weights") or not live.get("K"):
        return None
    sigs = signals()
    si = {s: i for i, s in enumerate(sigs)}
    r = Rec()
    r.x = array("f", bytes(4 * len(sigs)))
    act = []
    fams = families()
    g = array("f", bytes(4 * len(fams)))
    for f in live.get("families") or []:
        if f["family"] in fams:
            g[fams.index(f["family"])] = (f.get("W") or 0) * (f.get("A") or 0) * (f.get("H") or 0) / live["K"]
    for sg in live.get("signals") or []:
        j = si.get(sg["signal"])
        if j is None or sg.get("z") is None:
            continue
        x = max(-1.0, min(1.0, sg["z"] / cfg.S_SCALE))
        r.x[j] = x
        if sg.get("s") is not None and x:
            dl = 1 if (sg["s"] / x) > 0 else -1
            act.append((j, dl, sg.get("omega") or 0.0, sg.get("c") or 0.0, sg.get("r") or 1.0, sg.get("d") or 1.0))
    r.act, r.g = tuple(act), g
    r.reg = tuple(regime.get(d) for d in ("market", "volatility", "rates", "growth"))
    kind = wts["kind"]
    gam = tuple(wts.get("gamma") or (1, 1, 1))
    w = None
    for n in reversed(_cut(node_path(meta, "hier"), wts["depth"])):
        if n in wts["weights"]:
            w = wts["weights"][n]; break
    if w is None:
        return None
    return 100 * math.tanh(_index(w, wts["rms"], features(r, kind, gam)) / (wts.get("scale") or 1.0))


def reliability(store, lab: str, score: Optional[float]) -> Optional[dict]:
    """How often a production score of this size was right historically at this horizon, against the best naive
    baseline — prepared for the Shaffer Hedge (not used by its math in this phase)."""
    if score is None:
        return None
    res = (store.kv_get(RESEARCH_KEY) or {}).get("horizons", {}).get(lab) or {}
    pa = res.get("production_all") or {}
    for b in pa.get("abs_bands") or []:
        lo, hi = (float(x) for x in b["band"].split(".."))
        if lo <= abs(score) < hi + (0.01 if hi >= 100 else 0):
            base = res.get("baselines_all") or {}
            return {"band": b["band"], "correct": b.get("correct"), "lo": b.get("lo"), "hi": b.get("hi"), "n": b.get("n"),
                    "best_baseline": base.get("best"), "baseline_acc": base.get("best_acc"),
                    "validated": bool(b.get("lo") is not None and base.get("best_acc") is not None and b["lo"] > base["best_acc"])}
    return None
