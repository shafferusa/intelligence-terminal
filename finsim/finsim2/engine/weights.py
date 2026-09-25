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
    __slots__ = ("asset", "meta", "date", "end", "wk", "raw", "y", "yr", "x", "act", "g", "reg", "base", "score", "grp")


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
            _fill(r, sig, si, fi)
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


def _fill(r: Rec, sig: dict, si: Dict[str, int], fi: Dict[str, int]):
    """x, the active signals' (j, δ, ω, c, r, d) and the family multipliers g from a stored signal record."""
    x = array("f", bytes(4 * len(si)))
    for s_, v in (sig.get("x") or {}).items():
        j = si.get(s_)
        if j is not None:
            x[j] = v
    r.x = x
    r.act = tuple((si[s_], v[0], v[1], v[2], v[3], v[4]) for s_, v in (sig.get("a") or {}).items() if s_ in si)
    g = array("f", bytes(4 * len(fi)))
    for f, v in (sig.get("g") or {}).items():
        if f in fi:
            g[fi[f]] = v
    r.g = g


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
    """Per asset (and per walk-forward era when pooled — each era is a different fitted model) standardised score ×
    outcome products, keyed by week."""
    by: Dict[str, list] = {}
    for r in rows:
        g = getattr(r, "grp", None)
        by.setdefault(r.asset if g is None else f"{r.asset}|{g}", []).append(r)
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
    ic, rk = _ic(pr, h), _rank_ic(rows, key, h)
    out = {"n": len(rows), "assets": len({k.split("|")[0] for k in pr}), **_accuracy(rows, key, h), "ic": ic["ic"], "ic_t": ic["t"],
           "rank_ic": rk["rank_ic"], "rank_t": rk["t"], "monotonicity": _mono(rows, key)}
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
        tot_a: Dict[str, float] = {}
        for a, rs in diff.items():
            tot_a[a.split("|")[0]] = tot_a.get(a.split("|")[0], 0.0) + sum(v for _, v in rs)
        per = sorted(((v, a) for a, v in tot_a.items()), reverse=True)
        tot = sum(x for x, _ in per if x > 0)
        conc = {"top_asset": per[0][1], "share": (per[0][0] / tot) if tot > 0 else None}
    return {"n": len(rows), "production": P, "challenger": C, "delta_ic": d_ic, "delta_acc": _paired_acc(rows, lambda r: r.score, lambda r: r.raw, h),
            "baselines": baselines(rows, h), "concentration": conc}


# ------------------------------------------------------------------ one challenger over every era
INNER_YEARS = 4                      # the inner holdout that chooses the scaling strengths γ (inside each training window)
FINAL_CUT = "9999-12-31"


def _minus_years(d: str, y: int) -> str:
    return f"{int(d[:4]) - y:04d}{d[4:]}"


class _Trainer:
    """Sufficient statistics per (asset, time bucket), built once per γ; any training cutoff is then a sum of buckets.

    A record belongs to the training set of cutoff c only if its outcome had matured before c (r.end < c) — the
    purge that keeps overlapping forward returns out of a test period's training data."""

    def __init__(self, recs: List[Rec], paths: List[List[str]], kind: str, h: int, cuts: List[str]):
        import bisect
        self.recs, self.paths, self.kind, self.h = recs, paths, kind, h
        self.cuts = sorted(set(cuts))
        self.bucket = [bisect.bisect_right(self.cuts, r.end) for r in recs]     # r trains cutoff k ⇔ bucket ≤ k
        self.P = n_features(kind)
        self.parents: Dict[str, str] = {}
        self.asset_nodes: Dict[str, List[str]] = {}
        for r, pth in zip(recs, paths):
            self.asset_nodes[r.asset] = pth
            for x, y in zip(pth, pth[1:]):
                self.parents[y] = x
        self._feats: Dict[tuple, list] = {}
        self._stats: Dict[tuple, Dict[str, List[PStats]]] = {}
        self._fits: Dict[tuple, Optional[Fitted]] = {}

    def feats(self, gam) -> list:
        gam = tuple(gam)
        if gam not in self._feats:
            self._feats[gam] = [features(r, self.kind, gam) for r in self.recs]
        return self._feats[gam]

    def _cum(self, gam) -> Dict[str, List[PStats]]:
        """Per asset: cumulative statistics through each bucket."""
        gam = tuple(gam)
        if gam not in self._stats:
            nb = len(self.cuts) + 1
            per: Dict[str, List[Optional[PStats]]] = {}
            for r, f, bk in zip(self.recs, self.feats(gam), self.bucket):
                row = per.setdefault(r.asset, [None] * nb)
                st = row[bk]
                if st is None:
                    st = row[bk] = PStats(self.P)
                st.add(f, r.y)
            cum = {}
            for a, row in per.items():
                acc = PStats(self.P)
                out = []
                for st in row:
                    if st is not None:
                        acc = acc.copy(); acc.merge(st)
                    out.append(acc)
                cum[a] = out
            self._stats[gam] = cum
        return self._stats[gam]

    def fit(self, cut: str, gam) -> Optional[Fitted]:
        key = (cut, tuple(gam))
        if key in self._fits:
            return self._fits[key]
        k = self.cuts.index(cut)
        node: Dict[str, PStats] = {}
        for a, row in self._cum(gam).items():
            st = row[k]
            if not st.n:
                continue
            for nd in self.asset_nodes[a]:
                if nd not in node:
                    node[nd] = PStats(self.P)
                node[nd].merge(st)
        ft = None
        if node.get("global") and node["global"].n >= 500:
            W, rms = fit_nodes(node, self.parents, self.kind, self.h)
            ft = Fitted()
            ft.W, ft.rms, ft.n = W, rms, node["global"].n
            # scale: the challenger's median |index| matched to production's median |raw| on recent training records
            fs = self.feats(gam)
            tr = [i for i in range(len(self.recs)) if self.bucket[i] <= k][-20000:]
            idxs = sorted(abs(_index(ft.weights(self.paths[i]) or [0.0] * self.P, rms, fs[i])) for i in tr)
            raws = sorted(abs(self.recs[i].raw) for i in tr)
            mi, mr = idxs[len(idxs) // 2], min(99.0, raws[len(raws) // 2])
            ft.scale = (mi / math.atanh(mr / 100.0)) if mi > 0 and mr > 0 else 1.0
        self._fits[key] = ft
        return ft

    def predict(self, ft: Fitted, gam, idx: List[int], grp: Optional[str] = None) -> List[Rec]:
        fs = self.feats(gam)
        out = []
        for i in idx:
            w = ft.weights(self.paths[i])
            if w is None:
                continue
            r, r2 = self.recs[i], Rec()
            for s_ in Rec.__slots__:
                if s_ not in ("score", "grp"):
                    setattr(r2, s_, getattr(r, s_))
            r2.score = 100 * math.tanh(_index(w, ft.rms, fs[i]) / ft.scale)
            r2.grp = grp
            out.append(r2)
        return out


def run_challenger(recs: List[Rec], h: int, kind: str, depth: str, gam=(1, 1, 1), select_gamma: bool = False) -> dict:
    """Fit on outcomes matured before each era / the split / everything, predict the held-out records."""
    paths = [_cut(node_path(r.meta, "hier"), depth) for r in recs]
    last = max(r.date for r in recs)
    outer = [a for a, _ in ERAS] + [SPLIT, FINAL_CUT]
    inner = {c: _minus_years(min(c, last), INNER_YEARS) for c in outer}
    T = _Trainer(recs, paths, kind, h, outer + list(inner.values()))

    def choose_gamma(cut: str):
        """Inner holdout inside the training window: fit on outcomes matured before (cut − 4 years), judge the IC on
        records dated from then whose outcome was still known before the cut. The test period is never looked at."""
        if not select_gamma:
            return tuple(gam)
        ic_ = inner[cut]
        val = [i for i, r in enumerate(recs) if ic_ <= r.date and r.end < cut]
        best, bg = None, (1, 1, 1)
        if len(val) < 500:
            return bg
        for g in GAMMAS:
            ft = T.fit(ic_, g)
            if ft is None:
                continue
            ic = _ic(_std_prods(T.predict(ft, g, val), lambda r: r.score), h)["ic"]
            if ic is not None and (best is None or ic > best + 1e-9):
                best, bg = ic, g
        return bg

    res = {"kind": kind, "depth": depth, "eras": [], "n_params": n_features(kind)}
    wf_rows: List[Rec] = []
    era_w = []
    for (a, b) in ERAS:
        g = choose_gamma(a)
        ft = T.fit(a, g)
        test = [i for i, r in enumerate(recs) if a <= r.date < b]
        if ft is None or len(test) < 200:
            res["eras"].append({"from": a, "to": b, "status": "insufficient", "train": ft.n if ft else 0, "test": len(test)})
            continue
        rows = T.predict(ft, g, test, grp=a)
        wf_rows.extend(rows)
        k = T.cuts.index(a)
        res["eras"].append({"from": a, "to": b, "train": ft.n, "test": len(rows), "gamma": list(g), **compare(rows, h),
                            "training": _lite(T.predict(ft, g, [i for i in range(len(recs)) if T.bucket[i] <= k]), h)})
        era_w.append((a, ft))
    res["walkforward"] = compare(wf_rows, h, with_bands=True) if wf_rows else {"n": 0}
    res["by_class"] = _by_class(wf_rows, h)
    g = choose_gamma(SPLIT)
    ft = T.fit(SPLIT, g)
    if ft:
        res["split"] = compare(T.predict(ft, g, [i for i, r in enumerate(recs) if r.date >= SPLIT]), h)
    gF = choose_gamma(FINAL_CUT)
    final = T.fit(FINAL_CUT, gF)
    res["gamma"] = list(gF)
    res["final"] = {"n": final.n, "scale": final.scale, "rms": final.rms, "weights": final.W} if final else None
    res["training"] = _lite(T.predict(final, gF, list(range(len(recs)))), h) if final else None     # in-sample: not evidence
    res["era_weights"] = {a: ft.W.get("global") for a, ft in era_w}
    res["era_rms"] = {a: ft.rms for a, ft in era_w}
    res["gates"] = gates(res)
    res["research_score"] = research_score(res)
    return res


def _lite(rows: List[Rec], h: int) -> dict:
    """Accuracy and IC only, production vs challenger (used for the in-sample training numbers)."""
    if not rows:
        return {"n": 0}
    pa, ca = _accuracy(rows, lambda r: r.raw, h), _accuracy(rows, lambda r: r.score, h)
    return {"n": len(rows), "production_acc": pa.get("acc"), "challenger_acc": ca.get("acc"),
            "production_ic": _ic(_std_prods(rows, lambda r: r.raw), h)["ic"], "challenger_ic": _ic(_std_prods(rows, lambda r: r.score), h)["ic"]}


def _by_class(rows: List[Rec], h: int) -> Dict[str, dict]:
    """Walk-forward accuracy and IC per asset class, production vs challenger (same records)."""
    by: Dict[str, List[Rec]] = {}
    for r in rows:
        by.setdefault(r.meta.get("asset_class") or "OTHER", []).append(r)
    out = {}
    for c, rs in sorted(by.items()):
        pa, ca = _accuracy(rs, lambda r: r.raw, h), _accuracy(rs, lambda r: r.score, h)
        pi, ci = _ic(_std_prods(rs, lambda r: r.raw), h), _ic(_std_prods(rs, lambda r: r.score), h)
        out[c] = {"n": len(rs), "production_acc": pa.get("acc"), "challenger_acc": ca.get("acc"), "challenger_lo": ca.get("lo"), "challenger_hi": ca.get("hi"),
                  "production_ic": pi["ic"], "challenger_ic": ci["ic"], "delta_acc": _paired_acc(rs, lambda r: r.score, lambda r: r.raw, h),
                  "baseline": baselines(rs, h)}
    return out


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
                                     "by_class": out["challengers"][key].get("by_class")}
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
def live_score(store, vid: str, meta: dict, lab: str, sig: Optional[dict], regime: Dict[str, Optional[str]], info: Optional[dict] = None) -> Optional[float]:
    """A signal-level challenger's score today, from the same pieces production used today (shaffer.signal_record).
    `info` (optional) receives the hierarchy node whose weights were used."""
    _init_famidx()
    wts = (store.kv_get(f"formula:weights:{vid}") or {}).get(lab)
    if not wts or not wts.get("weights") or not sig:
        return None
    r = Rec()
    _fill(r, sig, {s: i for i, s in enumerate(signals())}, {f: i for i, f in enumerate(families())})
    r.reg = tuple(regime.get(d) for d in ("market", "volatility", "rates", "growth"))
    gam = tuple(wts.get("gamma") or (1, 1, 1))
    w = None
    for n in reversed(_cut(node_path(meta, "hier"), wts["depth"])):
        if n in wts["weights"]:
            w = wts["weights"][n]
            if info is not None:
                info["node"] = n
            break
    if w is None:
        return None
    return 100 * math.tanh(_index(w, wts["rms"], features(r, wts["kind"], gam)) / (wts.get("scale") or 1.0))


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


# ------------------------------------------------------------------ the report (SHAFFER_WEIGHT_RESEARCH.md)
_HZ_ORDER = [lab for lab, _ in LAB_HORIZONS]


def _p(v, d=1, signed=False):
    if v is None:
        return "—"
    return f"{v * 100:+.{d}f}%" if signed else f"{v * 100:.{d}f}%"


def _n(v, d=3, signed=False):
    if v is None:
        return "—"
    return f"{v:+.{d}f}" if signed else f"{v:.{d}f}"


def _ic_t(x):
    x = x or {}
    return f"{_n(x.get('ic'), 3, True)} (t {_n(x.get('t'), 1)})" if x.get("ic") is not None else "—"


def _acc_ci(a):
    a = a or {}
    return f"{_p(a.get('acc'))} [{_p(a.get('lo'))}, {_p(a.get('hi'))}]" if a.get("acc") is not None else "—"


def _status(c: dict, live: Optional[dict]) -> str:
    st = (c.get("gates") or {}).get("status") or "INSUFFICIENT DATA"
    if st == "SHADOW" and live and live.get("passed"):
        return "ELIGIBLE FOR PROMOTION"
    return st


def summary_rows(res: dict) -> List[dict]:
    """The main table: one row per horizon (walk-forward over the unseen eras)."""
    out = []
    for lab in _HZ_ORDER:
        hz = (res.get("horizons") or {}).get(lab)
        if not hz or not hz.get("challengers"):
            continue
        b = hz["challengers"].get(hz.get("best") or "") or {}
        wf = b.get("walkforward") or {}
        bl = wf.get("baselines") or {}
        pa, ca = (wf.get("production") or {}).get("acc"), (wf.get("challenger") or {}).get("acc")
        out.append({"horizon": lab, "best": hz.get("best"), "production_acc": pa, "challenger_acc": ca, "baseline": bl.get("best"),
                    "baseline_acc": bl.get("best_acc"), "excess": (ca - bl["best_acc"]) if ca is not None and bl.get("best_acc") is not None else None,
                    "production_excess": (pa - bl["best_acc"]) if pa is not None and bl.get("best_acc") is not None else None,
                    "production_ic": (wf.get("production") or {}).get("ic"), "challenger_ic": (wf.get("challenger") or {}).get("ic"),
                    "status": (b.get("gates") or {}).get("status", "INSUFFICIENT DATA")})
    return out


def markdown(res: dict, live: Optional[Dict[str, dict]] = None) -> str:
    """The research report. `live`: {version id: live_gate result} (G3), when a store is available."""
    live = live or {}
    H = res.get("horizons") or {}
    sigs = res.get("signals") or signals()
    L: List[str] = []
    w = L.append
    w("# Shaffer Score — signal-level weight research")
    w("")
    w(f"Run {res.get('started')} · score version {res.get('score_version')} · {sum((h.get('records') or 0) for h in H.values()):,} point-in-time records "
      f"over {max([h.get('assets') or 0 for h in H.values()] or [0])} assets · {res.get('seconds')} s · generated by `python -m finsim2 lab --weights`.")
    w("")
    w("Question: which weights *inside the Shaffer equation* would have made the point-in-time score more accurate at each horizon, "
      "and do they hold on periods they never saw? Every challenger is the Shaffer equation "
      "`raw = 100·tanh(Σ_f g_f Σ_i ω_i δ_i x_i c_i r_i d_i)` with some parameters learned per horizon — signal weights ω, family weights "
      "W·A·H (learned jointly as one β per signal), the confidence / regime / decay strengths, five regime interactions — each hierarchy "
      "node's ridge shrunk toward its parent (global → class → sector → industry → asset), weights bounded "
      f"(|β| ≤ {res.get('w_max')} on standardised inputs), shrinkage λ = {res.get('shrink_k'):.0f} effective observations. The research "
      "records reproduce production's raw score from the stored pieces (test `SignalRecords`).")
    w("")
    w("Validation: weights frozen before each test era, trained only on outcomes that had matured before it: → 2008 test 2009–12, → 2012 "
      "test 2013–16, → 2016 test 2017–20, → 2020 test 2021–24, → 2024 test 2025–now; plus the earlier split (→ 2017, test 2018–). "
      "Gates fixed before any result: **G1** pooled walk-forward paired Δ IC date-clustered t ≥ 2 and Δ accuracy ≥ 0; **G2** Δ IC > 0 and "
      "Δ accuracy ≥ 0 in ≥ 3 of the 4 complete eras, the 2018 split's Δ IC t ≥ 1, and walk-forward accuracy above the best naive baseline; "
      "**G3** ≥ 60 graded live-shadow forecasts, not worse than production on the same ones. Promotion is manual.")
    w("")
    w("Accuracy = sign(score) = sign(return) (a score of exactly 0 abstains), averaged per week then across weeks, 95% interval with "
      "n_eff = weeks × 5 / max(5, h). IC = per-asset standardised score × outcome, pooled per week (date-clustered t). Rank IC = "
      "cross-sectional Spearman per week. Naive baselines on the same records: always bullish, the asset's PIT positive-return frequency, "
      "previous-period direction, 12-1 momentum, 1-month mean reversion.")
    w("")
    w("## The main table (walk-forward over the unseen eras)")
    w("")
    w("| Horizon | Production accuracy | Best challenger accuracy | Naive baseline | Excess vs baseline (challenger / production) | Production IC | Challenger IC | Status |")
    w("|---|---|---|---|---|---|---|---|")
    rows = summary_rows(res)
    for r in rows:
        w(f"| {r['horizon']} | {_p(r['production_acc'])} | {_p(r['challenger_acc'])} ({r['best']}) | {_p(r['baseline_acc'])} ({(r['baseline'] or '').replace('_', ' ')}) | "
          f"{_p(r['excess'], 1, True)} / {_p(r['production_excess'], 1, True)} | {_n(r['production_ic'])} | {_n(r['challenger_ic'])} | {r['status']} |")
    w("")
    # ---- the 15 answers
    w("## Answers")
    w("")
    passed = [(lab, k, c) for lab in _HZ_ORDER if lab in H for k, c in (H[lab].get("challengers") or {}).items() if (c.get("gates") or {}).get("status") == "SHADOW"]
    g1 = [(lab, k, c) for lab in _HZ_ORDER if lab in H for k, c in (H[lab].get("challengers") or {}).items() if (c.get("gates") or {}).get("G1_walkforward")]
    w("**1. Did any signal-level challenger beat production?** " + (
        "Yes — passed G1 and G2: " + "; ".join(f"{lab} {k}" for lab, k, _ in passed) + "." if passed else
        ("Some passed G1 (pooled walk-forward) but not G2 (eras / split / baseline): " + "; ".join(f"{lab} {k}" for lab, k, _ in g1) + ". None passed both."
         if g1 else "No. No challenger passed G1 (pooled walk-forward improvement with t ≥ 2) at any horizon, so none passed both historical gates.")))
    w("")
    w("**2. At which horizons?** " + (", ".join(sorted({lab for lab, _, _ in passed}, key=_HZ_ORDER.index)) if passed else "None."))
    w("")
    w("**3–4. How much did directional accuracy and IC change?** Best challenger per horizon (by the multi-metric research score), "
      "walk-forward, paired on the same records:")
    w("")
    w("| Horizon | Challenger | Δ accuracy [95%] | Δ IC (t) | Δ rank IC | Monotonicity prod → ch. | Eras won | 2018 split Δ IC (t) |")
    w("|---|---|---|---|---|---|---|---|")
    for lab in _HZ_ORDER:
        hz = H.get(lab) or {}
        c = (hz.get("challengers") or {}).get(hz.get("best") or "")
        if not c:
            continue
        wf = c.get("walkforward") or {}
        da = wf.get("delta_acc") or {}
        P, C = wf.get("production") or {}, wf.get("challenger") or {}
        drk = (C.get("rank_ic") - P.get("rank_ic")) if C.get("rank_ic") is not None and P.get("rank_ic") is not None else None
        w(f"| {lab} | {hz['best']} | {_p(da.get('delta'), 2, True)} [{_p(da.get('lo'), 2, True)}, {_p(da.get('hi'), 2, True)}] | {_ic_t(wf.get('delta_ic'))} | {_n(drk, 3, True)} | "
          f"{_n(P.get('monotonicity'), 2)} → {_n(C.get('monotonicity'), 2)} | {(c.get('gates') or {}).get('eras_won')}/{(c.get('gates') or {}).get('eras_complete')} | {_ic_t((c.get('split') or {}).get('delta_ic'))} |")
    w("")
    w("**5. Which hierarchy level worked best?** Signal-weight challenger fitted with the hierarchy cut at each depth (walk-forward accuracy / IC):")
    w("")
    w("| Horizon | Production | " + " | ".join(DEPTHS) + " | Best level |")
    w("|---|---|" + "---|" * len(DEPTHS) + "---|")
    for lab in _HZ_ORDER:
        sp = (H.get(lab) or {}).get("specialisation") or {}
        if not sp:
            continue
        prod = next((v for v in sp.values() if v.get("production_acc") is not None), {})
        best = max(((v.get("ic") if v.get("ic") is not None else -9, d) for d, v in sp.items()), default=(None, "—"))[1]
        w(f"| {lab} | {_p(prod.get('production_acc'))} / {_n(prod.get('production_ic'))} | " +
          " | ".join(f"{_p((sp.get(d) or {}).get('acc'))} / {_n((sp.get(d) or {}).get('ic'))}" for d in DEPTHS) + f" | {best} (by IC) |")
    w("")
    w("**6–8. Which signals gained and lost weight, and which changes were stable across eras?** From each horizon's best challenger, "
      "global node: each signal's share of Σ|effective weight on x|, production vs challenger; *stable* = the challenger's era-by-era global "
      "weight kept one sign in every era fit.")
    w("")
    for lab in _HZ_ORDER:
        hz = H.get(lab) or {}
        g = ((hz.get("weights") or {}).get("global") or {}).get("signals") or {}
        if not g:
            continue
        st = hz.get("stability") or {}
        d = sorted(((v.get("challenger", 0) - v.get("production", 0), s) for s, v in g.items()), reverse=True)
        gain = [f"{s} {_p(g[s]['production'], 1)}→{_p(g[s]['challenger'], 1)}{' ✓' if (st.get(s) or {}).get('same_sign') else ''}" for x, s in d[:6] if x > 0.002]
        lose = [f"{s} {_p(g[s]['production'], 1)}→{_p(g[s]['challenger'], 1)}{' ✓' if (st.get(s) or {}).get('same_sign') else ''}" for x, s in d[::-1][:6] if x < -0.002]
        stable = [s for s, v in st.items() if v.get("same_sign")]
        w(f"* **{lab}** ({hz.get('best')}) — gained: {', '.join(gain) or 'none'}. Lost: {', '.join(lose) or 'none'}. "
          f"Stable sign in every era: {len(stable)} of {len(st)} signals used ({', '.join(stable[:12])}{' …' if len(stable) > 12 else ''}). ✓ = stable.")
    w("")
    w("**9. Which improvements disappeared on unseen data?** Challengers whose in-sample (training) accuracy or IC beat production but whose "
      "walk-forward did not:")
    w("")
    gone = []
    for lab in _HZ_ORDER:
        for k, c in ((H.get(lab) or {}).get("challengers") or {}).items():
            tr, wf = c.get("training") or {}, c.get("walkforward") or {}
            if tr.get("challenger_acc") is None or tr.get("production_acc") is None:
                continue
            ins = (tr["challenger_acc"] - tr["production_acc"], (tr.get("challenger_ic") or 0) - (tr.get("production_ic") or 0))
            oos = ((wf.get("delta_acc") or {}).get("delta"), (wf.get("delta_ic") or {}).get("ic"))
            if (ins[0] > 0 or ins[1] > 0) and ((oos[0] or 0) <= 0 or (oos[1] or 0) <= 0 or not (c.get("gates") or {}).get("G1_walkforward")):
                gone.append(f"| {lab} | {k} | {_p(ins[0], 1, True)} / {_n(ins[1], 3, True)} | {_p(oos[0], 1, True)} / {_n(oos[1], 3, True)} |")
    if gone:
        w("| Horizon | Challenger | In-sample Δ accuracy / Δ IC | Walk-forward Δ accuracy / Δ IC |")
        w("|---|---|---|---|")
        L.extend(gone)
    else:
        w("None.")
    w("")
    w("**10. Does |Shaffer Score| correspond more strongly to the probability of being right?** Directional accuracy by |score| band, walk-forward "
      "records (production → best challenger):")
    w("")
    w("| Horizon | " + " | ".join(f"|SS| {lo:g}–{min(hi, 100):g}" for lo, hi in ABS_BANDS) + " |")
    w("|---|" + "---|" * len(ABS_BANDS))
    for lab in _HZ_ORDER:
        hz = H.get(lab) or {}
        c = (hz.get("challengers") or {}).get(hz.get("best") or "")
        if not c:
            continue
        pb = ((c.get("walkforward") or {}).get("production") or {}).get("abs_bands") or []
        cb = ((c.get("walkforward") or {}).get("challenger") or {}).get("abs_bands") or []
        w(f"| {lab} | " + " | ".join(f"{_p(a.get('correct'), 0)} → {_p(b.get('correct'), 0)} <sub>({a.get('n', 0):,} / {b.get('n', 0):,})</sub>" for a, b in zip(pb, cb)) + " |")
    w("")
    w("**11–13. Best validated accuracy, best naive baseline, excess.** *Validated* = production, or a challenger that passed G1 and G2.")
    w("")
    w("| Horizon | Best validated accuracy | Best naive baseline | Excess |")
    w("|---|---|---|---|")
    for r in rows:
        hz = H[r["horizon"]]
        cands = [(r["production_acc"], "production")] + [(((c.get("walkforward") or {}).get("challenger") or {}).get("acc"), k)
                                                           for k, c in hz["challengers"].items() if (c.get("gates") or {}).get("status") == "SHADOW"]
        acc, who = max((x for x in cands if x[0] is not None), default=(None, "—"))
        w(f"| {r['horizon']} | {_p(acc)} ({who}) | {_p(r['baseline_acc'])} ({(r['baseline'] or '').replace('_', ' ')}) | "
          f"{_p((acc - r['baseline_acc']) if acc is not None and r['baseline_acc'] is not None else None, 1, True)} |")
    w("")
    w("**14. Which challenger versions should enter live shadow?** " + (
        "; ".join(f"`shaffer-{res.get('score_version')}-sig-{k.replace('@', '-')}-exp` at {lab}" for lab, k, _ in passed) +
        " — recorded daily in the prediction ledger next to production (production score, challenger score, both versions, hierarchy node, "
        "horizon, confidence, maturity date), never rewritten." if passed else
        "None: only challengers that pass G1 and G2 at a horizon are recorded in the live shadow, and none did."))
    w("")
    elig = [vid for vid, g in live.items() if g.get("passed")]
    w("**15. Is any challenger eligible for promotion?** " + ("Yes: " + ", ".join(elig) + " (promotion stays a manual action)." if elig else
      "No. Promotion needs G1, G2 and G3 (≥ 60 graded live-shadow forecasts); no challenger has all three."))
    w("")
    # ---- per horizon detail
    w("## Per horizon")
    for lab in _HZ_ORDER:
        hz = H.get(lab)
        if not hz:
            continue
        w("")
        w(f"### {lab} — {hz.get('records', 0):,} records, {hz.get('assets')} assets, {hz.get('first')} → {hz.get('last')}")
        w("")
        if not hz.get("challengers"):
            w(hz.get("reason") or "No research.")
            continue
        w("| Challenger | Params / node | Training acc. (in-sample) | 2018 split acc. | Walk-forward acc. [95%] | Live shadow | Baseline | Δ IC (t) | Eras won | Research score | Status |")
        w("|---|---|---|---|---|---|---|---|---|---|---|")
        for k, c in sorted(hz["challengers"].items(), key=lambda kv: -(kv[1].get("research_score") or {}).get("score", -99)):
            wf, tr, sp = c.get("walkforward") or {}, c.get("training") or {}, c.get("split") or {}
            vid = f"shaffer-{res.get('score_version')}-sig-{k.replace('@', '-')}-exp"
            lv = live.get(vid) or {}
            w(f"| {k} | {c.get('n_params')} | {_p(tr.get('challenger_acc'))} (prod. {_p(tr.get('production_acc'))}) | {_p((sp.get('challenger') or {}).get('acc'))} (prod. {_p((sp.get('production') or {}).get('acc'))}) | "
              f"{_acc_ci(wf.get('challenger'))} (prod. {_p((wf.get('production') or {}).get('acc'))}) | {lv.get('graded', 0)} graded | {_p((wf.get('baselines') or {}).get('best_acc'))} | "
              f"{_ic_t(wf.get('delta_ic'))} | {(c.get('gates') or {}).get('eras_won')}/{(c.get('gates') or {}).get('eras_complete')} | {_n((c.get('research_score') or {}).get('score'), 2)} | {_status(c, lv)} |")
        b = hz["challengers"].get(hz.get("best") or "") or {}
        w("")
        w(f"Best by research score: **{hz.get('best')}**. By era (production → challenger):")
        w("")
        w("| Test era | Train / test records | Accuracy | IC | Δ IC (t) | Best naive |")
        w("|---|---|---|---|---|---|")
        for e in b.get("eras") or []:
            era = f"{e['from'][:4]}–{'now' if e['to'] >= '2100' else int(e['to'][:4]) - 1}"
            if not e.get("production"):
                w(f"| {era} | {e.get('train', 0):,} / {e.get('test', 0):,} | {e.get('status')} | | | |")
                continue
            w(f"| {era} | {e['train']:,} / {e['test']:,} | {_p(e['production'].get('acc'))} → {_p(e['challenger'].get('acc'))} | {_n(e['production'].get('ic'))} → {_n(e['challenger'].get('ic'))} | "
              f"{_ic_t(e.get('delta_ic'))} | {_p((e.get('baselines') or {}).get('best_acc'))} ({((e.get('baselines') or {}).get('best') or '').replace('_', ' ')}) |")
        w("")
        w("Score bands, walk-forward records — production | best challenger:")
        w("")
        w("| Score | Indep. obs. | % positive | Avg return | Median | Direction right [95%] | ‖ | Indep. obs. | % positive | Avg return | Median | Direction right [95%] |")
        w("|---|---|---|---|---|---|---|---|---|---|---|---|")
        pb = ((b.get("walkforward") or {}).get("production") or {}).get("bands") or []
        cb = ((b.get("walkforward") or {}).get("challenger") or {}).get("bands") or []
        for x, y in zip(pb, cb):
            def cells(z):
                if not z.get("n"):
                    return "0 | | | | "
                return f"{z.get('independent', 0):,.0f} | {_p(z.get('positive'), 0)} | {_p(z.get('mean'), 2, True)} | {_p(z.get('median'), 2, True)} | {_acc_ci({'acc': z.get('correct'), 'lo': z.get('lo'), 'hi': z.get('hi')})}"
            w(f"| {x['band']} | {cells(x)} | ‖ | {cells(y)} |")
        bc = b.get("by_class") or {}
        if bc:
            w("")
            w("By asset class (walk-forward): ")
            w("")
            w("| Class | Records | Production acc. | Challenger acc. [95%] | Δ accuracy | Production IC | Challenger IC | Best naive |")
            w("|---|---|---|---|---|---|---|---|")
            for cl, v in sorted(bc.items(), key=lambda kv: -kv[1].get("n", 0)):
                w(f"| {cl} | {v.get('n', 0):,} | {_p(v.get('production_acc'))} | {_acc_ci({'acc': v.get('challenger_acc'), 'lo': v.get('challenger_lo'), 'hi': v.get('challenger_hi')})} | "
                  f"{_p((v.get('delta_acc') or {}).get('delta'), 1, True)} | {_n(v.get('production_ic'))} | {_n(v.get('challenger_ic'))} | {_p((v.get('baseline') or {}).get('best_acc'))} ({((v.get('baseline') or {}).get('best') or '').replace('_', ' ')}) |")
    return "\n".join(L) + "\n"
