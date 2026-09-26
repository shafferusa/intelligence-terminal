"""ML Lab — fine-tuning the learned Shaffer system without overfitting it (report SHAFFER_FINETUNE.md).

Starting points (fixed, never modified): production, `alpha-learned-1w-global-exp` (D) and
`alpha-learned-1w-hierarchy-exp` (E) — both passed every historical gate and are recording in live shadow exactly as
defined. Nothing here touches their formulas; every fine-tuned model is a NEW version with its own validation.

Nested walk-forward. The outer layer is untouched: every outer era (2009–12, 2013–16, 2017–20, 2021–24, 2025–, and the
2018 split) is scored by weights fitted on outcomes that matured before it, and every hyper-parameter (regularisation,
shrinkage, depth, bounds, interactions, blend weight, decay, window, regime, smoothing) is chosen only from the inner
walk-forward weeks whose outcomes matured before that outer era — never from the era it is judged on. The same rule
chooses across the families (the "nested model selection" candidate).

1W model families (each a set of options; the family's model = its nested choice):
    ranking objectives   ridge on relative returns (pointwise) · RankRLS (= D) · elastic net · pairwise logistic
                         (RankNet) · listwise softmax (ListNet)
    blend                α·rank(D) + (1 − α)·rank(E), α ∈ {0, .25, .5, .75, 1}
    stability penalty    per-coefficient ridge λ_i = λ₀ + λ₂·v_i / median(v), v_i = variance of the coefficient across
                         leave-one-era-out and leave-one-class-out fits (sign flips and swings) — global and hierarchy
    stability classes    STABLE ×1 · REGIME DEPENDENT ×3 · UNSTABLE ×30 · NO EVIDENCE ×30 penalty multipliers
    regime-conditional   w(state) = ridge toward the base weights inside one predeclared regime (volatility, market,
                         rates, breadth, risk-on/off), λ = f × the state's effective sample, f ∈ {0.25, 1, 4} (20–80%
                         shrinkage toward the base weights) — on top of D and on top of E
    time decay           half-life ∞ / 15 / 10 / 7 / 5 / 3 years — global and hierarchy
    rolling windows      expanding / 15 / 10 / 7 / 5 years — global and hierarchy
    signal clusters      PIT correlation clusters (|ρ| ≥ 0.6): within-cluster fusion penalty, or one representative
    signs                production's orientation as a hard sign constraint; or the same except signals whose learned
                         sign is stable across the training eras
    interactions         seven predeclared economic interactions (defined here, before any result)
    turnover             score smoothing (EMA across weeks) chosen by inner NET long-short, not rank IC
Gates: the Alpha program's G1–G4 against production, BH FDR across every fine-tune model, and — fixed now, before any
result — G5: the model must improve on the learned global model D (paired Δ rank IC > 0 with t ≥ 2, and Δ ≥ 0 in at
least 3 of the 4 complete eras; t ≥ 2 rather than 1 because the planted no-structure test showed selection alone reaching
t ≈ 1). Only a model passing all of them is eligible for live shadow, under a new version id.
"""
from __future__ import annotations

import math
import random
import time
from array import array
from itertools import repeat
from operator import add, mul, sub
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from . import learned as L

RESEARCH_KEY = "lab:finetune"
NAN = float("nan")
HALF_LIVES = [None, 15, 10, 7, 5, 3]
WINDOWS = [None, 15, 10, 7, 5]
BLEND_ALPHAS = [0.0, 0.25, 0.5, 0.75, 1.0]
STAB_L2 = [0.0, 1e4, 3e4, 1e5]
CLASS_MULT = {"STABLE": 1.0, "REGIME DEPENDENT": 3.0, "UNSTABLE": 30.0, "NO EVIDENCE": 30.0}
CLASS_BASE = [1e3, 1e4, 3e4]
REG_FRAC = [0.25, 1.0, 4.0]          # regime adjustment ridge = f × the state's effective sample: 20% / 50% / 80% shrinkage to base
REG_DIMS = ["volatility", "market", "rates", "breadth", "risk"]
FUSE = [1e4, 5e4]
CLUSTER_RHO = 0.6
EN_FRACS = [0.003, 0.01, 0.03]
SMOOTH = [1.0, 0.7, 0.5, 0.3]
TREE_K = [1000, 5000, 20000]
TREE_DEPTH = ["ptype", "sector", "asset"]
INTERACTIONS = [("momentum × volatility", "mom_12_1", "vol_20"), ("reversal × volatility", "ret_1w", "vol_20"),
                ("valuation × rates", "earnings_yield", "d_y10_3m"), ("quality × credit", "fund_quality", "credit_spread"),
                ("breadth × beta", "@breadth", "beta_252"), ("trend × liquidity", "dist_ma200", "volume_z"),
                ("momentum × VIX", "mom_12_1", "vix")]
GBM_INTERACTIONS = [("vix × vol_20", "vix", "vol_20"), ("dist_ma200 × dollar_mom_3m", "dist_ma200", "dollar_mom_3m"),
                    ("dollar_mom_3m × ret_6m", "dollar_mom_3m", "ret_6m")]
# one-way trading cost by product type (fraction of notional): research assumptions, stated in the report
COST_BY_PTYPE = {"single stock": 0.0005, "equity index": 0.0001, "broad equity ETF": 0.0002, "sector ETF": 0.0003,
                 "international equity ETF": 0.0004, "factor ETF": 0.0003, "leveraged / inverse ETF": 0.0005,
                 "Treasury": 0.00015, "inflation-linked": 0.0003, "cash": 0.0001, "aggregate / MBS": 0.0003,
                 "investment grade": 0.0005, "high yield": 0.0005, "loans / CLO": 0.0008, "EM debt": 0.0006,
                 "municipal": 0.0008, "hybrid": 0.0008, "commodity": 0.0005, "major FX": 0.0001, "EM FX": 0.0005,
                 "dollar index": 0.0002, "crypto": 0.0015, "VIX futures ETN": 0.001}
FLAT_COST = 0.0010
MEGA_CAPS = {"AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "AVGO", "JPM", "V", "MA", "WMT", "XOM"}
G5_T = 2.0      # raised from 1 before the full run: on a structure-free planted world, nested selection across the
                # fine-tune families reached t ≈ 1.0 against the learned global model by chance alone


# ================================================================== plumbing on top of the learned engine
class Ctx:
    """One horizon's table, its learned baseline learner (fits and uniform candidates), per-(asset, year) statistics
    and helpers to fit, score and choose options nestedly."""

    def __init__(self, tab: L.Table, progress=None, target: str = "alpha", base: bool = True):
        self.tab, self.say = tab, progress or (lambda m: None)
        self.lr = L.Learner(tab, target)
        t0 = time.time()
        self.lr.fit_all()
        if base:
            self.lr.score_candidates()
        else:
            self.lr.y_date, self.lr.w_date = tab.to_date(self.lr.y), tab.to_date(self.lr.w)
            self.lr.week_end = {wk: e for wk, _, _, e in tab.weeks}
            self.lr.week_era = {wk: tab.era[tab.perm[a]] for wk, a, b, _ in tab.weeks}
        self.P = self.lr.P
        self.cols, self.y, self.w = self.lr.cols, self.lr.y, self.lr.w
        self.years = sorted({int(e[:4]) for e in tab.end})
        self._ycells()
        self.say(f"context: {tab.n} records, fits and candidates ({time.time() - t0:.0f}s)")

    # ---- per (asset, end-year) statistics
    def _slices_by_year(self) -> Dict[Tuple[str, int], Tuple[int, int]]:
        out = {}
        tab = self.tab
        for a, (s0, s1) in tab.slices.items():
            k = s0
            while k < s1:
                y = int(tab.end[k][:4])
                j = k
                while j < s1 and int(tab.end[j][:4]) == y:
                    j += 1
                out[(a, y)] = (k, j)
                k = j
        return out

    def _ycells(self, cols=None, y=None, w=None, store: bool = True):
        cols, y, w = cols or self.cols, y or self.y, w or self.w
        cells = {key: L.cell_stat(cols, y, w, a, b) for key, (a, b) in self._slices_by_year().items()}
        if store:
            self.yc = cells
            self.gy = {}
            for (a, yr), st in cells.items():
                self.gy[yr] = st.copy() if yr not in self.gy else self.gy[yr].add(st)
        return cells

    def mult(self, cut: str, half_life=None, window=None) -> Dict[int, float]:
        cy = int(cut[:4]) if cut != L.FINAL else self.years[-1] + 1
        out = {}
        for y in self.years:
            if y >= cy:
                continue
            age = cy - y - 0.5
            if window is not None and age > window:
                continue
            out[y] = 0.5 ** (age / half_life) if half_life else 1.0
        return out

    def gstat(self, cut: str, half_life=None, window=None, gy=None) -> Optional[L.Stat]:
        gy = gy or self.gy
        st = None
        for y, m in self.mult(cut, half_life, window).items():
            if y in gy:
                s = _scaled(gy[y], m)
                st = s if st is None else st.add(s)
        return st

    def node_stats(self, cut: str, half_life=None, window=None) -> Dict[str, L.Stat]:
        m = self.mult(cut, half_life, window)
        per_asset: Dict[str, L.Stat] = {}
        for (a, y), st in self.yc.items():
            if y in m:
                s = _scaled(st, m[y])
                per_asset[a] = s if a not in per_asset else per_asset[a].add(s)
        out = {}
        tree = self.lr.tree
        for nd in tree.order:
            st = None
            for a in tree.assets[nd]:
                if a in per_asset:
                    st = per_asset[a].copy() if st is None else st.add(per_asset[a])
            out[nd] = st if st is not None else L.Stat(self.P)
        return out

    def rms(self, cut: str) -> List[float]:
        return self.lr.fits[cut]["rms"]

    # ---- scoring
    def score_global(self, wfun: Callable[[str], Optional[List[float]]], cols=None, coefs: bool = False) -> Tuple[array, array]:
        """Walk-forward (each test era by weights fitted before it) and 2018-split scores of a global model. `wfun(cut)`
        returns standardised weights (or, with coefs=True, coefficients on the raw columns)."""
        cols = cols or self.cols
        tab, lr = self.tab, self.lr
        wf, sp = array("d", repeat(NAN, tab.n)), array("d", repeat(NAN, tab.n))
        cache = {}
        for cut in [a for a, _ in L.TEST_ERAS] + [L.SPLIT]:
            if cut in lr.fits:
                w = wfun(cut)
                cache[cut] = (w if coefs else L._coef(w, self.rms(cut))) if w is not None else None
        for a, (s0, s1) in tab.slices.items():
            for e in range(len(L.TEST_ERAS)):
                c = cache.get(L.TEST_ERAS[e][0])
                lo, hi = lr._era_range(s0, s1, e)
                if c and lo < hi:
                    wf[lo:hi] = L.score_slice(cols, c, lo, hi)
            c = cache.get(L.SPLIT)
            lo = lr._date_from(s0, s1, L.SPLIT)
            if c and lo < s1:
                sp[lo:s1] = L.score_slice(cols, c, lo, s1)
        return wf, sp

    def score_tree(self, Wfun: Callable[[str], Optional[Dict[str, List[float]]]], depth: str) -> Tuple[array, array]:
        tab, lr = self.tab, self.lr
        wf, sp = array("d", repeat(NAN, tab.n)), array("d", repeat(NAN, tab.n))
        cache = {cut: Wfun(cut) for cut in [a for a, _ in L.TEST_ERAS] + [L.SPLIT] if cut in lr.fits}
        for a, (s0, s1) in tab.slices.items():
            path = tab.paths[a]
            for e in range(len(L.TEST_ERAS)):
                cut = L.TEST_ERAS[e][0]
                W = cache.get(cut)
                lo, hi = lr._era_range(s0, s1, e)
                if W and lo < hi:
                    wf[lo:hi] = L.score_slice(self.cols, L._coef(L.weights_at(W, path, depth), self.rms(cut)), lo, hi)
            W = cache.get(L.SPLIT)
            lo = lr._date_from(s0, s1, L.SPLIT)
            if W and lo < s1:
                sp[lo:s1] = L.score_slice(self.cols, L._coef(L.weights_at(W, path, depth), self.rms(L.SPLIT)), lo, s1)
        return wf, sp

    # ---- weekly metrics
    def weekly_ric(self, s: array) -> Dict[int, float]:
        return L.weekly_rank_ic(self.tab, self.tab.to_date(s), self.lr.y_date, self.lr.w_date)

    def pick(self, options: Dict[str, Tuple[array, array]], default: Optional[str] = None,
             metric: Optional[Callable[[array], Dict[int, float]]] = None) -> dict:
        """The nested choice: for every outer era (and the split, and today) the option with the best mean inner metric
        over walk-forward weeks whose outcomes matured before that era. Options are tried in order; ties keep the first."""
        metric = metric or self.weekly_ric
        keys = list(options)
        default = default or keys[0]
        weekly = {k: metric(options[k][0]) for k in keys}
        tab, lr = self.tab, self.lr
        wf, sp = array("d", repeat(NAN, tab.n)), array("d", repeat(NAN, tab.n))
        choices = {}

        def choose(cut):
            inner = lr.inner_weeks(cut)
            if len(inner) < L.MIN_INNER_WEEKS:
                return default

            def sc(k):
                v = [x for wk, x in weekly[k].items() if wk in inner]
                return sum(v) / len(v) if v else -1e9
            return max(keys, key=sc)
        for e in range(len(L.TEST_ERAS)):
            cut = L.TEST_ERAS[e][0]
            k = choose(cut)
            choices[cut] = k
            src = options[k][0]
            for a, (s0, s1) in tab.slices.items():
                lo, hi = lr._era_range(s0, s1, e)
                if lo < hi:
                    wf[lo:hi] = src[lo:hi]
        k = choose(L.SPLIT)
        choices[L.SPLIT] = k
        src = options[k][1]
        for a, (s0, s1) in tab.slices.items():
            lo = lr._date_from(s0, s1, L.SPLIT)
            if lo < s1:
                sp[lo:s1] = src[lo:s1]
        choices[L.FINAL] = choose(L.FINAL)
        return {"wf": wf, "sp": sp, "choices": choices, "final": choices[L.FINAL]}


def _scaled(st: L.Stat, m: float) -> L.Stat:
    c = L.Stat.__new__(L.Stat)
    c.P, c.n = st.P, st.n
    c.xx = array("d", map(mul, st.xx, repeat(m))) if m != 1.0 else array("d", st.xx)
    c.xy = array("d", map(mul, st.xy, repeat(m))) if m != 1.0 else array("d", st.xy)
    c.yy, c.sw = st.yy * m, st.sw * m
    return c


def ridge_vec(st: L.Stat, rms: List[float], prior: List[float], lam: Sequence[float], extra: Optional[List[List[float]]] = None,
              idx: Optional[List[int]] = None) -> List[float]:
    """Generalised ridge: (S/rms² + diag(λ) + extra) w = Xᵀy/rms + diag(λ)·prior, optionally on a subset of features."""
    P = st.P
    act = [i for i in range(P) if rms[i] > 0 and (idx is None or i in idx)]
    if not act or st.sw <= 0:
        return list(prior)
    M = st.matrix()
    pos = {i: k for k, i in enumerate(act)}
    A = [[M[i][j] / (rms[i] * rms[j]) + (lam[i] if i == j else 0.0) for j in act] for i in act]
    if extra is not None:
        for i in act:
            for j in act:
                A[pos[i]][pos[j]] += extra[i][j]
    b = [st.xy[i] / rms[i] + lam[i] * prior[i] for i in act]
    sol = L._chol_solve(A, b)
    w = [0.0] * P
    for k, i in enumerate(act):
        w[i] = sol[k]
    return w


def fit_tree_vec(tree: L.Tree, ns: Dict[str, L.Stat], rms: List[float], K: float, glam: Sequence[float]) -> Dict[str, List[float]]:
    """The learned hierarchy with a per-coefficient penalty at the global level (children pool toward their parent)."""
    W: Dict[str, List[float]] = {}
    P = len(rms)
    for n in tree.order:
        par = tree.parent[n]
        st = ns[n]
        if par is None:
            W[n] = ridge_vec(st, rms, [0.0] * P, glam) if st.sw > 0 else [0.0] * P
        else:
            W[n] = L.ridge(st, rms, W[par], K) if st.sw > 0 else list(W[par])
    return W


def week_rank(tab: L.Table, s: array) -> array:
    """Within-week percentile rank of a score (−0.5 … 0.5), NaN where unscored."""
    out = array("d", repeat(NAN, tab.n))
    for _, a, b, _ in tab.weeks:
        idx = [tab.perm[k] for k in range(a, b) if s[tab.perm[k]] == s[tab.perm[k]]]
        m = len(idx)
        if m < 2:
            continue
        rk = L._ranks([s[k] for k in idx])
        for k, r in zip(idx, rk):
            out[k] = (r - 1) / (m - 1) - 0.5
    return out


# ================================================================== model families (1W Alpha)
class Model:
    """A family's nested result: walk-forward and split scores, the per-era choices, and how to get today's weights."""

    def __init__(self, name: str, family: str, res: dict, final_fn: Optional[Callable[[str], dict]] = None, note: str = "",
                 pit: bool = True, fns: Optional[Dict[str, Callable]] = None, single: Optional[Callable] = None):
        self.name, self.family, self.note, self.pit = name, family, note, pit
        self.wf, self.sp, self.choices, self.final = res["wf"], res["sp"], res.get("choices") or {}, res.get("final")
        self.final_fn = final_fn
        self.fns, self.single = fns, single

    def wcut(self, cut: str) -> Optional[List[float]]:
        """Standardised global weights used at `cut` (global models only)."""
        if self.single is not None:
            return self.single(cut)
        if self.fns is not None:
            k = self.choices.get(cut, self.final)
            f = self.fns.get(k)
            return f(cut) if callable(f) else None
        return None


def fam_baselines(ctx: Ctx) -> Dict[str, Model]:
    lr = ctx.lr
    K0 = L.K_DEFAULT
    D = Model("learned global (D)", "baseline", {"wf": lr.wf[(K0, "global")], "sp": lr.sp[(K0, "global")], "final": "global"},
              lambda k: {"global": lr.fits[L.FINAL]["W"][K0]["global"]}, single=lambda cut: lr.fits[cut]["W"][K0]["global"])
    opts = {f"{K}|{d}": (lr.wf[(K, d)], lr.sp[(K, d)]) for K, d in lr.cand}
    e = ctx.pick(opts, default=f"{K0}|global")
    E = Model("learned hierarchy (E)", "baseline", e, lambda k: _tree_final(ctx, lambda cut: lr.fits[cut]["W"][int(float(k.split("|")[0]))], k.split("|")[1]))
    return {"D": D, "E": E}


def _tree_final(ctx: Ctx, Wfun, depth: str) -> dict:
    W = Wfun(L.FINAL)
    return {"tree": W, "depth": depth}


def fam_ranking(ctx: Ctx, seed: int = 7) -> Dict[str, Model]:
    """Ridge on relative returns (pointwise), elastic net, pairwise logistic (RankNet), listwise (ListNet)."""
    tab, lr = ctx.tab, ctx.lr
    out = {}
    # A. ridge on the relative return itself (week-demeaned y_alpha, pointwise weights)
    y2 = array("d", tab.y)
    for _, a, b, _ in tab.weeks:
        idx = tab.perm[a:b]
        if b - a < L.MIN_WEEK:
            for k in idx:
                y2[k] = 0.0
            continue
        mu = sum(tab.y[k] for k in idx) / (b - a)
        for k in idx:
            y2[k] = tab.y[k] - mu
    cells = ctx._ycells(ctx.cols, y2, tab.wp, store=False)
    gy2: Dict[int, L.Stat] = {}
    for (a, yr), st in cells.items():
        gy2[yr] = st.copy() if yr not in gy2 else gy2[yr].add(st)

    def w_rel(cut):
        g = ctx.gstat(cut, gy=gy2)
        return L.ridge(g, ctx.rms(cut), [0.0] * ctx.P, L.G_LAMBDA) if g else None
    wf, sp = ctx.score_global(w_rel)
    out["ridge (relative return)"] = Model("ridge on relative returns", "ranking objective", {"wf": wf, "sp": sp, "final": "-"},
                                           lambda k: {"global": w_rel(L.FINAL)}, single=w_rel)
    # B. elastic net (RankRLS statistics)
    en = {}
    for frac in EN_FRACS:
        def w_en(cut, frac=frac):
            g = lr.fits[cut]["ns"]["global"]
            rms = ctx.rms(cut)
            b = [abs(g.xy[i] / rms[i]) if rms[i] > 0 else 0.0 for i in range(ctx.P)]
            return L.cd_solve(g, rms, L.G_LAMBDA, frac * max(b), start=lr.fits[cut]["W"][L.K_DEFAULT]["global"])
        en[str(frac)] = ctx.score_global(w_en) + (w_en,)
    r = ctx.pick({k: v[:2] for k, v in en.items()})
    out["elastic net"] = Model("elastic net", "ranking objective", r, lambda k: {"global": en[k][2](L.FINAL)}, fns={k: v[2] for k, v in en.items()})
    # C. pairwise logistic
    rn_cache = {}

    def w_rn(cut):
        if cut not in rn_cache:
            rn_cache[cut] = L.ranknet(lr, cut, seed)
        return rn_cache[cut]
    wf, sp = ctx.score_global(w_rn)
    out["pairwise (RankNet)"] = Model("pairwise logistic (RankNet)", "ranking objective", {"wf": wf, "sp": sp, "final": "-"},
                                      lambda k: {"global": w_rn(L.FINAL)}, single=w_rn)
    # D. listwise
    ln_cache = {}

    def w_ln(cut):
        if cut not in ln_cache:
            ln_cache[cut] = listnet(ctx, cut, seed)
        return ln_cache[cut]
    wf, sp = ctx.score_global(w_ln)
    out["listwise (ListNet)"] = Model("listwise softmax (ListNet)", "ranking objective", {"wf": wf, "sp": sp, "final": "-"},
                                      lambda k: {"global": w_ln(L.FINAL)}, single=w_ln)
    return out


def listnet(ctx: Ctx, cut: str, seed: int = 7, iters: int = 60, tau: float = 4.0, weeks_max: int = 400, lam: float = 1e-3,
            step: float = 0.05) -> Optional[List[float]]:
    """Listwise ranking (ListNet top-one): minimise −Σ_week Σ_i q_i log p_i, q = softmax(τ·u), p = softmax(wᵀx̃), on
    training weeks (outcomes matured before `cut`), Adam on standardised weights from the RankRLS solution."""
    tab, lr = ctx.tab, ctx.lr
    rms = ctx.rms(cut)
    rnd = random.Random(seed)
    wks = [(a, b) for wk, a, b, e in tab.weeks if e < cut and b - a >= L.MIN_WEEK]
    if len(wks) < 50:
        return None
    if len(wks) > weeks_max:
        wks = rnd.sample(wks, weeks_max)
    act = [i for i in range(ctx.P) if rms[i] > 0]
    idx = [tab.perm[k] for a, b in wks for k in range(a, b)]
    bounds, pos = [], 0
    for a, b in wks:
        bounds.append((pos, pos + b - a))
        pos += b - a
    X = [array("d", (ctx.cols[i][k] / rms[i] for k in idx)) for i in act]
    q = array("d", bytes(8 * len(idx)))
    for s0, s1 in bounds:
        z = [math.exp(tau * ctx.y[idx[k]]) for k in range(s0, s1)]
        t = sum(z)
        for j, k in enumerate(range(s0, s1)):
            q[k] = z[j] / t
    g0 = lr.fits[cut]["W"][L.K_DEFAULT]["global"]
    w = [g0[i] * 10.0 for i in act]
    m1, m2 = [0.0] * len(act), [0.0] * len(act)
    for it in range(1, iters + 1):
        s = array("d", repeat(0.0, len(idx)))
        for k, c in enumerate(X):
            if w[k]:
                s = array("d", map(add, s, map(mul, c, repeat(w[k]))))
        d = array("d", bytes(8 * len(idx)))
        for s0, s1 in bounds:
            mx = max(s[s0:s1])
            z = [math.exp(v - mx) for v in s[s0:s1]]
            t = sum(z)
            for j, k in enumerate(range(s0, s1)):
                d[k] = z[j] / t - q[k]
        grad = [sum(map(mul, c, d)) / len(wks) + lam * w[k] for k, c in enumerate(X)]
        for k in range(len(act)):
            m1[k] = 0.9 * m1[k] + 0.1 * grad[k]
            m2[k] = 0.999 * m2[k] + 0.001 * grad[k] ** 2
            w[k] -= step * (m1[k] / (1 - 0.9 ** it)) / (math.sqrt(m2[k] / (1 - 0.999 ** it)) + 1e-8)
    out = [0.0] * ctx.P
    for k, i in enumerate(act):
        out[i] = w[k]
    return out


def fam_blend(ctx: Ctx, D: Model, E: Model) -> Model:
    rD, rE = week_rank(ctx.tab, D.wf), week_rank(ctx.tab, E.wf)
    sD, sE = week_rank(ctx.tab, D.sp), week_rank(ctx.tab, E.sp)
    opts = {}
    for a in BLEND_ALPHAS:
        opts[str(a)] = (array("d", map(lambda x, y, a=a: a * x + (1 - a) * y, rD, rE)), array("d", map(lambda x, y, a=a: a * x + (1 - a) * y, sD, sE)))
    r = ctx.pick(opts, default="0.5")
    return Model("blend α·D + (1 − α)·E", "blend", r, lambda k: {"blend": float(k), "D": D.final_fn(D.final), "E": E.final_fn(E.final)})


# ---- stability
def _block_of(y: int) -> int:
    for k, (a, b) in enumerate([(2001, 2008), (2009, 2012), (2013, 2016), (2017, 2020), (2021, 2024), (2025, 2100)]):
        if a <= y <= b:
            return k
    return 0


class Stability:
    """Leave-one-era-out and leave-one-class-out global fits at every cutoff: coefficient instability and PIT stability
    classes (era blocks 2001–08, 2009–12, 2013–16, 2017–20, 2021–24, 2025–, training years only)."""

    def __init__(self, ctx: Ctx, regime: Optional["Regimes"] = None):
        self.ctx, self.regime = ctx, regime
        self.cls_gy: Dict[str, Dict[int, L.Stat]] = {}
        for (a, yr), st in ctx.yc.items():
            c = ctx.tab.paths[a][1] if len(ctx.tab.paths[a]) > 1 else "other"
            d = self.cls_gy.setdefault(c, {})
            d[yr] = st.copy() if yr not in d else d[yr].add(st)
        self.cache: Dict[str, dict] = {}

    def at(self, cut: str) -> dict:
        if cut in self.cache:
            return self.cache[cut]
        ctx = self.ctx
        rms = ctx.rms(cut)
        P = ctx.P
        years = [y for y in ctx.mult(cut)]
        full = ctx.gstat(cut)
        w0 = L.ridge(full, rms, [0.0] * P, L.G_LAMBDA)
        loeo, blocks = [], {}
        for y in years:
            blocks.setdefault(_block_of(y), []).append(y)
        era_w = []
        for b, ys in sorted(blocks.items()):
            part = None
            for y in ys:
                part = ctx.gy[y].copy() if part is None else part.add(ctx.gy[y])
            rest = _minus(full, part)
            if rest.sw > 0 and part.sw > 0:
                loeo.append(L.ridge(rest, rms, [0.0] * P, L.G_LAMBDA))
                era_w.append(L.ridge(part, rms, [0.0] * P, L.G_LAMBDA))
        loco = []
        for c, gyc in self.cls_gy.items():
            part = None
            for y in years:
                if y in gyc:
                    part = gyc[y].copy() if part is None else part.add(gyc[y])
            if part is None or part.sw <= 0:
                continue
            rest = _minus(full, part)
            if rest.sw > 0:
                loco.append(L.ridge(rest, rms, [0.0] * P, L.G_LAMBDA))
        var = []
        for i in range(P):
            v = 0.0
            for grp in (loeo, loco):
                if len(grp) >= 2:
                    xs = [g[i] for g in grp]
                    m = sum(xs) / len(xs)
                    v += sum((x - m) ** 2 for x in xs) / (len(xs) - 1)
            var.append(v)
        classes = []
        for i in range(P):
            rt = self.regime.diff_t(cut, i) if self.regime else None
            classes.append(L.stability([ew[i] for ew in era_w], w0[i], rt)["class"] if len(era_w) >= 3 else "NO EVIDENCE")
        signs = []
        for i in range(P):
            xs = [ew[i] for ew in era_w]
            n = len(xs)
            if n >= 3:
                m = sum(xs) / n
                sd = math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1))
                t = m / (sd / math.sqrt(n)) if sd > 0 else 0.0
                share = sum(1 for x in xs if (x >= 0) == (w0[i] >= 0)) / n
                signs.append(share >= 0.8 and abs(t) >= 2)
            else:
                signs.append(False)
        out = {"w0": w0, "var": var, "classes": classes, "stable_sign": signs, "era_w": era_w}
        self.cache[cut] = out
        return out


def _minus(a: L.Stat, b: L.Stat) -> L.Stat:
    c = L.Stat.__new__(L.Stat)
    c.P, c.n = a.P, a.n - b.n
    c.xx = array("d", map(sub, a.xx, b.xx))
    c.xy = array("d", map(sub, a.xy, b.xy))
    c.yy, c.sw = a.yy - b.yy, a.sw - b.sw
    return c


def fam_stability(ctx: Ctx, stab: Stability, E: Model) -> Dict[str, Model]:
    out = {}
    P = ctx.P

    def lam_vec(cut, l2):
        v = stab.at(cut)["var"]
        med = sorted(x for x in v if x > 0)
        med = med[len(med) // 2] if med else 1.0
        return [L.G_LAMBDA + l2 * (x / med if med else 0.0) for x in v]
    opts, fns = {}, {}
    for l2 in STAB_L2:
        def wfun(cut, l2=l2):
            return ridge_vec(ctx.gstat(cut), ctx.rms(cut), [0.0] * P, lam_vec(cut, l2))
        opts[str(l2)] = ctx.score_global(wfun)
        fns[str(l2)] = wfun
    r = ctx.pick(opts, default="0.0")
    out["stability penalty (global)"] = Model("stability-penalised global", "stability", r, lambda k, fns=dict(fns): {"global": fns[k](L.FINAL)}, fns=dict(fns))
    # hierarchy with the penalised global level
    opts, fns = {}, {}
    for l2 in STAB_L2[1:]:
        for K in TREE_K:
            cache = {}

            def Wfun(cut, l2=l2, K=K, cache=cache):
                if cut not in cache:
                    cache[cut] = fit_tree_vec(ctx.lr.tree, ctx.lr.fits[cut]["ns"], ctx.rms(cut), K, lam_vec(cut, l2))
                return cache[cut]
            for d in TREE_DEPTH:
                opts[f"{l2}|{K}|{d}"] = ctx.score_tree(Wfun, d)
                fns[f"{l2}|{K}|{d}"] = (Wfun, d)
    # without enough inner history every hierarchy family falls back to the SAME default as the learned hierarchy E
    # (pooling K_DEFAULT, global depth) — never to a depth picked with hindsight
    base_key = _hier_default(ctx, opts, fns)
    r = ctx.pick(opts, default=base_key)
    out["stability penalty (hierarchy)"] = Model("stability-penalised hierarchy", "stability", r,
                                                 lambda k: {"tree": fns[k][0](L.FINAL), "depth": fns[k][1]})
    # stability classes
    opts, fns = {}, {}
    for base in CLASS_BASE:
        def wfun(cut, base=base):
            cl = stab.at(cut)["classes"]
            return ridge_vec(ctx.gstat(cut), ctx.rms(cut), [0.0] * P, [L.G_LAMBDA + base * CLASS_MULT[c] for c in cl])
        opts[str(base)] = ctx.score_global(wfun)
        fns[str(base)] = wfun
    fns["0"] = lambda cut: ctx.lr.fits[cut]["W"][L.K_DEFAULT]["global"]
    opts = {"0": ctx.score_global(fns["0"]), **opts}
    r = ctx.pick(opts, default="0")
    out["stability classes"] = Model("stability-class penalties", "stability", r, lambda k, fns=dict(fns): {"global": fns[k](L.FINAL)}, fns=dict(fns))
    return out


# ---- regimes
class Regimes:
    """Per (end-year, regime dimension, state) global statistics; states are point in time (known on the record's date)."""

    def __init__(self, ctx: Ctx):
        self.ctx = ctx
        tab = ctx.tab
        names = tab.names
        j_dist = names.index("dist_ma200") if "dist_ma200" in names else None
        wide = {}
        for wk, a, b, _ in tab.weeks:
            if j_dist is None:
                continue
            xs = [tab.X[j_dist][tab.perm[k]] for k in range(a, b)]
            wide[wk] = (sum(1 for v in xs if v > 0) / len(xs)) >= 0.5 if xs else None
        self.state: Dict[str, List[Optional[str]]] = {d: [None] * tab.n for d in REG_DIMS}
        for k in range(tab.n):
            rg = tab.reg[k]
            self.state["volatility"][k] = rg[1] if rg[1] in ("high_vol", "low_vol") else None
            self.state["market"][k] = rg[0] if rg[0] in ("bull", "bear") else None
            self.state["rates"][k] = rg[2] if rg[2] in ("rising_rates", "falling_rates") else None
            wv = wide.get(tab.wk[k])
            self.state["breadth"][k] = None if wv is None else ("wide" if wv else "narrow")
            self.state["risk"][k] = None if rg[0] is None or rg[1] is None else ("risk-on" if rg[0] == "bull" and rg[1] == "low_vol" else "risk-off")
        self.stats: Dict[Tuple[str, str], Dict[int, L.Stat]] = {}
        groups: Dict[Tuple[str, str, int], List[int]] = {}
        for d in REG_DIMS:
            for k in range(tab.n):
                s = self.state[d][k]
                if s is not None and ctx.w[k] > 0:
                    groups.setdefault((d, s, int(tab.end[k][:4])), []).append(k)
        for (d, s, y), idx in groups.items():
            self.stats.setdefault((d, s), {})[y] = L.cell_stat(ctx.cols, ctx.y, ctx.w, 0, 0, idx)
        self.states = {d: sorted({s for (dd, s) in self.stats if dd == d}) for d in REG_DIMS}

    def stat(self, cut: str, d: str, s: str, years: Optional[List[int]] = None) -> Optional[L.Stat]:
        st = None
        for y in (years if years is not None else self.ctx.mult(cut)):
            x = self.stats.get((d, s), {}).get(y)
            if x is not None:
                st = x.copy() if st is None else st.add(x)
        return st

    def diff_t(self, cut: str, i: int) -> Optional[float]:
        """The strongest regime difference t of signal i across the training era blocks (for the stability classes)."""
        if not hasattr(self, "_dt"):
            self._dt = {}
        if cut not in self._dt:
            self._dt[cut] = self._diff_all(cut)
        return self._dt[cut][i]

    def _diff_all(self, cut: str) -> List[Optional[float]]:
        ctx = self.ctx
        rms = ctx.rms(cut)
        blocks: Dict[int, List[int]] = {}
        for y in ctx.mult(cut):
            blocks.setdefault(_block_of(y), []).append(y)
        best: List[Optional[float]] = [None] * ctx.P
        for d in REG_DIMS[:3]:
            st = self.states[d]
            if len(st) != 2:
                continue
            diffs = []
            for ys in blocks.values():
                a, b = self.stat(cut, d, st[0], ys), self.stat(cut, d, st[1], ys)
                if a is None or b is None or a.n < 200 or b.n < 200:
                    continue
                wa = L.ridge(a, rms, [0.0] * ctx.P, L.G_LAMBDA)
                wb = L.ridge(b, rms, [0.0] * ctx.P, L.G_LAMBDA)
                diffs.append([x - y for x, y in zip(wa, wb)])
            if len(diffs) >= 3:
                for i in range(ctx.P):
                    xs = [dd[i] for dd in diffs]
                    m = sum(xs) / len(xs)
                    sd = math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))
                    t = m / (sd / math.sqrt(len(xs))) if sd > 0 else None
                    if t is not None and (best[i] is None or abs(t) > abs(best[i])):
                        best[i] = t
        return best


def fam_regime(ctx: Ctx, reg: Regimes, E: Model) -> Dict[str, Model]:
    """Regime-conditional adjustments: inside one predeclared regime state the global weights may move away from the base
    weights, heavily regularised toward them. Added to D (global) and to E (hierarchy)."""
    tab = ctx.tab
    out = {}
    optsD, optsE, fns = {}, {}, {}
    none_dev = (array("d", repeat(0.0, tab.n)), array("d", repeat(0.0, tab.n)))
    base_cache = {}

    def base(cut):
        if cut not in base_cache:
            base_cache[cut] = ctx.lr.fits[cut]["W"][L.K_DEFAULT]["global"]
        return base_cache[cut]
    for d in REG_DIMS:
        for lam in REG_FRAC:
            ws_cache = {}

            def ws(cut, d=d, lam=lam, ws_cache=ws_cache):
                if cut not in ws_cache:
                    out_ = {}
                    for s in reg.states[d]:
                        st = reg.stat(cut, d, s)
                        out_[s] = L.ridge(st, ctx.rms(cut), base(cut), lam * st.sw) if st is not None and st.sw > 0 else base(cut)
                    ws_cache[cut] = out_
                return ws_cache[cut]
            dev = _state_dev(ctx, reg, d, ws, base)
            fns[f"{d}|{lam}"] = (d, ws)
            optsD[f"{d}|{lam}"] = (array("d", map(add, ctx.lr.wf[(L.K_DEFAULT, "global")], dev[0])),
                                   array("d", map(add, ctx.lr.sp[(L.K_DEFAULT, "global")], dev[1])))
            optsE[f"{d}|{lam}"] = (array("d", map(add, E.wf, dev[0])), array("d", map(add, E.sp, dev[1])))
    optsD = {"none": (ctx.lr.wf[(L.K_DEFAULT, "global")], ctx.lr.sp[(L.K_DEFAULT, "global")]), **optsD}
    optsE = {"none": (E.wf, E.sp), **optsE}
    r = ctx.pick(optsD, default="none")
    out["regime-conditional (global)"] = Model("global + regime-conditional", "regime", r,
                                               lambda k: {"global": base(L.FINAL), "regime": _reg_final(fns, k)})
    r = ctx.pick(optsE, default="none")
    out["regime-conditional (hierarchy)"] = Model("hierarchy + regime-conditional", "regime", r,
                                                  lambda k: {**E.final_fn(E.final), "regime": _reg_final(fns, k)})
    return out


def _reg_final(fns, k):
    if k == "none":
        return None
    d, ws = fns[k]
    return {"dim": d, "weights": ws(L.FINAL)}


def _state_dev(ctx: Ctx, reg: Regimes, d: str, ws, base) -> Tuple[array, array]:
    """(w_state − w_base)·x for every walk-forward and split record."""
    tab, lr = ctx.tab, ctx.lr
    out_wf, out_sp = array("d", repeat(0.0, tab.n)), array("d", repeat(0.0, tab.n))
    st = reg.state[d]
    for tgt, cuts in ((out_wf, [(e, L.TEST_ERAS[e][0]) for e in range(len(L.TEST_ERAS))]), (out_sp, [(None, L.SPLIT)])):
        for e, cut in cuts:
            if cut not in lr.fits:
                continue
            W, b0, rms = ws(cut), base(cut), ctx.rms(cut)
            coefs = {s: L._coef([W[s][i] - b0[i] for i in range(ctx.P)], rms) for s in W}
            for a, (s0, s1) in tab.slices.items():
                if e is None:
                    lo, hi = lr._date_from(s0, s1, L.SPLIT), s1
                else:
                    lo, hi = lr._era_range(s0, s1, e)
                if lo >= hi:
                    continue
                sc = {s: L.score_slice(ctx.cols, c, lo, hi) for s, c in coefs.items()}
                for j, k in enumerate(range(lo, hi)):
                    s = st[k]
                    if s in sc:
                        tgt[k] = sc[s][j]
    return out_wf, out_sp


# ---- time decay and rolling windows
def _hier_default(ctx: Ctx, opts: dict, fns: dict) -> str:
    """Add the learned hierarchy's own no-history default (pooling K_DEFAULT, global depth) as an option and return its key."""
    k = f"none|{L.K_DEFAULT}|global"
    W0 = lambda cut: ctx.lr.fits[cut]["W"][L.K_DEFAULT]  # noqa: E731
    opts[k] = ctx.score_tree(W0, "global")
    fns[k] = (W0, "global")
    return k


def fam_time(ctx: Ctx) -> Dict[str, Model]:
    out = {}
    P = ctx.P
    for kind, grid in (("decay", HALF_LIVES), ("window", WINDOWS)):
        opts, fns = {}, {}
        for v in grid:
            hl, win = (v, None) if kind == "decay" else (None, v)

            def wfun(cut, hl=hl, win=win):
                g = ctx.gstat(cut, hl, win)
                return L.ridge(g, ctx.rms(cut), [0.0] * P, L.G_LAMBDA) if g else None
            opts[str(v)] = ctx.score_global(wfun)
            fns[str(v)] = wfun
        r = ctx.pick(opts, default="None")
        out[f"{'time decay' if kind == 'decay' else 'rolling window'} (global)"] = Model(
            f"global, {'half-life' if kind == 'decay' else 'window'} chosen nested", "time", r, lambda k, fns=fns: {"global": fns[k](L.FINAL)}, fns=fns)
        opts, fns = {}, {}
        for v in grid:
            hl, win = (v, None) if kind == "decay" else (None, v)
            for K in TREE_K:
                cache = {}

                def Wfun(cut, hl=hl, win=win, K=K, cache=cache):
                    if cut not in cache:
                        ns = ctx.node_stats(cut, hl, win)
                        cache[cut] = L.fit_tree(ctx.lr.tree, ns, ctx.rms(cut), K)
                    return cache[cut]
                for d in TREE_DEPTH:
                    opts[f"{v}|{K}|{d}"] = ctx.score_tree(Wfun, d)
                    fns[f"{v}|{K}|{d}"] = (Wfun, d)
            ctx.say(f"time {kind} {v}: hierarchy fitted")
        r = ctx.pick(opts, default=_hier_default(ctx, opts, fns))
        out[f"{'time decay' if kind == 'decay' else 'rolling window'} (hierarchy)"] = Model(
            f"hierarchy, {'half-life' if kind == 'decay' else 'window'} + K + depth chosen nested", "time", r,
            lambda k, fns=fns: {"tree": fns[k][0](L.FINAL), "depth": fns[k][1]})
    return out


# ---- clusters and signs
def clusters(st: L.Stat, rms: List[float], rho: float = CLUSTER_RHO) -> List[List[int]]:
    """Average-linkage clusters of the signals by |correlation| on the training statistics."""
    P = st.P
    M = st.matrix()
    act = [i for i in range(P) if rms[i] > 0 and M[i][i] > 0]
    C = {(i, j): M[i][j] / math.sqrt(M[i][i] * M[j][j]) for i in act for j in act}
    cl = [[i] for i in act]
    while True:
        best, pair = rho, None
        for a in range(len(cl)):
            for b in range(a + 1, len(cl)):
                s = sum(abs(C[(i, j)]) for i in cl[a] for j in cl[b]) / (len(cl[a]) * len(cl[b]))
                if s >= best:
                    best, pair = s, (a, b)
        if pair is None:
            return cl
        a, b = pair
        cl[a] = cl[a] + cl[b]
        del cl[b]


def fam_clusters(ctx: Ctx) -> Dict[str, Model]:
    P = ctx.P
    cl_cache = {}

    def cl(cut):
        if cut not in cl_cache:
            cl_cache[cut] = clusters(ctx.lr.fits[cut]["ns"]["global"], ctx.rms(cut))
        return cl_cache[cut]
    opts, fns = {}, {}

    def w_plain(cut):
        return ctx.lr.fits[cut]["W"][L.K_DEFAULT]["global"]
    opts["ridge"] = (ctx.lr.wf[(L.K_DEFAULT, "global")], ctx.lr.sp[(L.K_DEFAULT, "global")])
    fns["ridge"] = w_plain
    for lam in FUSE:
        def wfun(cut, lam=lam):
            extra = [[0.0] * P for _ in range(P)]
            for c in cl(cut):
                m = len(c)
                if m < 2:
                    continue
                for i in c:
                    for j in c:
                        extra[i][j] += lam * ((1.0 if i == j else 0.0) - 1.0 / m)
            return ridge_vec(ctx.lr.fits[cut]["ns"]["global"], ctx.rms(cut), [0.0] * P, [L.G_LAMBDA] * P, extra)
        opts[f"fuse {lam:g}"] = ctx.score_global(wfun)
        fns[f"fuse {lam:g}"] = wfun

    def w_rep(cut):
        g = ctx.lr.fits[cut]["ns"]["global"]
        rms = ctx.rms(cut)
        M = g.matrix()
        reps = [max(c, key=lambda i: abs(g.xy[i]) / math.sqrt(M[i][i]) if M[i][i] > 0 else 0.0) for c in cl(cut)]
        return ridge_vec(g, rms, [0.0] * P, [L.G_LAMBDA] * P, idx=reps)
    opts["representative"] = ctx.score_global(w_rep)
    fns["representative"] = w_rep
    r = ctx.pick(opts, default="ridge")
    return {"signal clusters": Model("signal clusters (fusion / representatives)", "clusters", r, lambda k: {"global": fns[k](L.FINAL)}, fns=fns),
            "_clusters": cl(L.FINAL)}


def fam_signs(ctx: Ctx, stab: Stability, orient: Dict[str, List[float]]) -> Dict[str, Model]:
    P = ctx.P
    opts, fns = {}, {}
    opts["free"] = (ctx.lr.wf[(L.K_DEFAULT, "global")], ctx.lr.sp[(L.K_DEFAULT, "global")])
    fns["free"] = lambda cut: ctx.lr.fits[cut]["W"][L.K_DEFAULT]["global"]
    for kind in ("economic sign", "economic sign unless learned sign stable"):
        def wfun(cut, kind=kind):
            g = ctx.lr.fits[cut]["ns"]["global"]
            bound = ctx.lr.fits[cut]["bound"]
            o = orient.get(cut) or [1.0] * P
            free = stab.at(cut)["stable_sign"] if kind != "economic sign" else [False] * P
            lo = [-bound if (free[i] or o[i] < 0) else 0.0 for i in range(P)]
            hi = [bound if (free[i] or o[i] > 0) else 0.0 for i in range(P)]
            return L.cd_solve(g, ctx.rms(cut), L.G_LAMBDA, 0.0, lo, hi)
        opts[kind] = ctx.score_global(wfun)
        fns[kind] = wfun
    r = ctx.pick(opts, default="free")
    return {"signs": Model("sign rules (economic / learned)", "signs", r, lambda k: {"global": fns[k](L.FINAL)}, fns=fns)}


# ---- interactions
def interaction_columns(ctx: Ctx, reg: Regimes, specs) -> List[array]:
    tab = ctx.tab
    names = tab.names
    cols = []
    for _, a, b in specs:
        xa = [1.0 if s == "wide" else (-1.0 if s == "narrow" else 0.0) for s in reg.state["breadth"]] if a == "@breadth" else tab.X[names.index(a)]
        xb = tab.X[names.index(b)]
        c = array("d", map(mul, xa, xb))
        for _, s0, s1, _ in tab.weeks:
            idx = tab.perm[s0:s1]
            if s1 - s0 < L.MIN_WEEK:
                continue
            mu = sum(c[k] for k in idx) / (s1 - s0)
            for k in idx:
                c[k] -= mu
        cols.append(c)
    return cols


def fam_interactions(ctx: Ctx, reg: Regimes) -> Dict[str, Model]:
    tab = ctx.tab
    extra = interaction_columns(ctx, reg, INTERACTIONS + GBM_INTERACTIONS)
    cols = list(ctx.cols) + extra
    P0, P1 = ctx.P, len(cols)
    cells = ctx._ycells(cols, ctx.y, ctx.w, store=False)
    gyx: Dict[int, L.Stat] = {}
    for (a, yr), st in cells.items():
        gyx[yr] = st.copy() if yr not in gyx else gyx[yr].add(st)
    econ = list(range(P0, P0 + len(INTERACTIONS)))
    gbm = list(range(P0 + len(INTERACTIONS), P1))
    fits = {}

    def fit(cut, idx):
        key = (cut, tuple(idx))
        if key not in fits:
            g = ctx.gstat(cut, gy=gyx)
            rms = L.rms_of(g)
            w = ridge_vec(g, rms, [0.0] * P1, [L.G_LAMBDA] * P1, idx=list(range(P0)) + idx)
            fits[key] = L._coef(w, rms)
        return fits[key]
    out = {}
    opts = {"none": ctx.score_global(lambda cut: fit(cut, []), cols=cols, coefs=True),
            "economic": ctx.score_global(lambda cut: fit(cut, econ), cols=cols, coefs=True)}
    r = ctx.pick(opts, default="none")
    out["interactions (economic)"] = Model("global + predeclared economic interactions", "interactions", r,
                                           lambda k: {"global_coefs": fit(L.FINAL, econ if k == "economic" else []), "extra": INTERACTIONS if k == "economic" else []})
    singles = {}
    for j, (nm, _, _) in enumerate(INTERACTIONS):
        o = {"none": opts["none"], nm: ctx.score_global(lambda cut, j=j: fit(cut, [P0 + j]), cols=cols, coefs=True)}
        singles[nm] = ctx.pick(o, default="none")
        singles[nm]["alone"] = o[nm]
    r = ctx.pick({"none": opts["none"], "gbm": ctx.score_global(lambda cut: fit(cut, gbm), cols=cols, coefs=True)}, default="none")
    out["interactions (GBM-suggested)"] = Model("global + GBM-suggested interactions", "interactions", r, None,
                                                note="the three interactions were read off a boosting diagnostic run over every era, so their "
                                                     "selection is not point in time — descriptive only, never eligible", pit=False)
    out["_singles"] = singles
    out["_interaction_weights"] = {nm: fit(L.FINAL, [P0 + j])[P0 + j] for j, (nm, _, _) in enumerate(INTERACTIONS)}
    return out


# ---- turnover: score smoothing chosen by inner net long-short
def smooth_scores(tab: L.Table, s: array, beta: float) -> array:
    """EMA of each asset's score across consecutive weekly records (only past and present scores)."""
    if beta >= 1.0:
        return s
    out = array("d", repeat(NAN, tab.n))
    for a, (s0, s1) in tab.slices.items():
        prev = None
        for k in range(s0, s1):
            v = s[k]
            if v != v:
                prev = None
                continue
            prev = v if prev is None else beta * v + (1 - beta) * prev
            out[k] = prev
    return out


def fam_turnover(ctx: Ctx, base: Dict[str, Model], costs: array) -> Dict[str, Model]:
    out = {}
    metric = lambda s: ls_weekly(ctx, s, 0.2, costs)  # noqa: E731
    for nm, M in base.items():
        opts = {str(b): (smooth_scores(ctx.tab, M.wf, b), smooth_scores(ctx.tab, M.sp, b)) for b in SMOOTH}
        r = ctx.pick(opts, default="1.0", metric=metric)
        out[f"{nm} + turnover smoothing"] = Model(f"{M.name} + score smoothing (chosen by inner net L/S)", "turnover", r,
                                                  lambda k, M=M: {**(M.final_fn(M.final) if M.final_fn else {}), "smooth": float(k)})
    return out


# ================================================================== evaluation
def record_costs(tab: L.Table) -> array:
    out = array("d", repeat(FLAT_COST, tab.n))
    for a, (s0, s1) in tab.slices.items():
        p = tab.paths[a]
        pt = p[2].split("/")[-1] if len(p) > 2 and p[2].startswith("ptype:") else None
        c = COST_BY_PTYPE.get(pt, FLAT_COST)
        for k in range(s0, s1):
            out[k] = c
    return out


def ls_weekly(ctx: Ctx, s: array, frac: float, costs: Optional[array], round_trip: bool = False) -> Dict[int, float]:
    return ls_stats(ctx, s, frac, costs, round_trip)["weekly"]


def ls_stats(ctx: Ctx, s: array, frac: float, costs: Optional[array], round_trip: bool = False, era: Optional[int] = None) -> dict:
    """Equal-weight top minus bottom `frac` of the week's scored cross-section, formed on non-overlapping weeks of the
    horizon (1D: one-day trades from weekly snapshots, a full round trip each). Gross and net (per-product one-way costs
    × traded weight), turnover, holding persistence, hit rate, drawdown."""
    tab = ctx.tab
    h = tab.h
    step = max(1, int(round(h / 5.0)))
    weeks = [(wk, a, b) for wk, a, b, _ in tab.weeks]
    weeks = weeks[::step]
    prev = {"L": set(), "S": set()}
    held = {"L": {}, "S": {}}
    runs: List[int] = []
    gross, net, turn, weekly = [], [], [], {}
    for wk, a, b in weeks:
        idx = [tab.perm[k] for k in range(a, b) if s[tab.perm[k]] == s[tab.perm[k]]]
        if era is not None and (not idx or tab.era[idx[0]] != era):
            continue
        if len(idx) < 10 or not idx or tab.era[idx[0]] < 0:
            continue
        o = sorted(idx, key=lambda k: s[k])
        n = max(1, int(round(frac * len(o))))
        top, bot = o[-n:], o[:n]
        g = sum(math.expm1(tab.rec[k].yr) for k in top) / n - sum(math.expm1(tab.rec[k].yr) for k in bot) / n
        cst, tt = 0.0, 0.0
        for leg, members in (("L", top), ("S", bot)):
            names = {tab.asset[k]: k for k in members}
            if round_trip:
                c = sum(2 * costs[k] for k in members) / n if costs else 0.0
                t = 1.0
            else:
                enter = [names[x] for x in names if x not in prev[leg]]
                exit_ = len(prev[leg] - set(names))
                c = ((sum(costs[k] for k in enter) + exit_ * (sum(costs[k] for k in members) / n)) / n) if costs else 0.0
                t = (len(enter) + exit_) / (2 * n)
            cst += c
            tt += t / 2
            for x in list(held[leg]):
                if x not in names:
                    runs.append(held[leg].pop(x))
            for x in names:
                held[leg][x] = held[leg].get(x, 0) + 1
            prev[leg] = set(names)
        gross.append(g); net.append(g - cst); turn.append(tt)
        weekly[wk] = g - cst
    runs += [v for leg in held.values() for v in leg.values()]
    if len(net) < 10:
        return {"n": len(net), "weekly": weekly}
    m = sum(net) / len(net)
    sd = math.sqrt(sum((x - m) ** 2 for x in net) / (len(net) - 1))
    eq = peak = dd = 0.0
    for x in net:
        eq += x; peak = max(peak, eq); dd = min(dd, eq - peak)
    per_year = 52.0 / step if h >= 5 else 52.0
    return {"n": len(net), "gross": sum(gross) / len(gross), "net": m, "t": (m / (sd / math.sqrt(len(net)))) if sd else None,
            "net_annual": m * per_year, "turnover": sum(turn) / len(turn), "hit": sum(1 for x in net if x > 0) / len(net),
            "drawdown": dd, "persistence_weeks": (sum(runs) / len(runs) * step) if runs else None, "weekly": weekly}


def rank_churn(ctx: Ctx, s: array) -> Optional[float]:
    """Mean Spearman correlation between consecutive formation weeks' scores of the same assets (1 = no churn)."""
    tab = ctx.tab
    step = max(1, int(round(tab.h / 5.0)))
    weeks = [(a, b) for _, a, b, _ in tab.weeks][::step]
    prev, cors = None, []
    for a, b in weeks:
        cur = {tab.asset[tab.perm[k]]: s[tab.perm[k]] for k in range(a, b) if s[tab.perm[k]] == s[tab.perm[k]]}
        if prev:
            common = [x for x in cur if x in prev]
            if len(common) >= 10:
                r1, r2 = L._ranks([cur[x] for x in common]), L._ranks([prev[x] for x in common])
                n = len(common)
                mr = (n + 1) / 2
                num = sum((p - mr) * (q - mr) for p, q in zip(r1, r2))
                den = math.sqrt(sum((p - mr) ** 2 for p in r1) * sum((q - mr) ** 2 for q in r2))
                if den > 0:
                    cors.append(num / den)
        prev = cur if cur else prev
    return sum(cors) / len(cors) if cors else None


def _vs(ctx: Ctx, wk: Dict[int, float], base: Dict[int, float]) -> Tuple[dict, List[Optional[float]], int, bool]:
    d = {w: wk[w] - base[w] for w in wk if w in base}
    c = L.clustered(d, ctx.tab.h)
    eras = []
    for e in range(len(L.TEST_ERAS)):
        de = [v for w, v in d.items() if ctx.lr.week_era.get(w) == e]
        eras.append(sum(de) / len(de) if de else None)
    ok = sum(1 for x in eras[:4] if x is not None and x >= 0)
    return c, eras, ok, bool((c.get("mean") or 0) > 0 and (c.get("t") or 0) >= G5_T and ok >= 3)


def evaluate(ctx: Ctx, name: str, wf: array, sp: array, D_weekly: Dict[int, float], costs: array, lab: str,
             E_weekly: Optional[Dict[int, float]] = None) -> dict:
    """The Alpha program's metrics and gates G1–G4 against production, plus the comparison with the learned global model
    (G5 inputs), net long-short with product costs, turnover and rank churn."""
    from . import alphanext as AN
    tab = ctx.tab
    h = tab.h
    rows, srows = [], []
    for k in range(tab.n):
        if tab.wa[k] <= 0:
            continue
        r = tab.rec[k]
        if tab.era[k] >= 0 and wf[k] == wf[k]:
            rows.append((k, r))
        if tab.date[k] >= L.SPLIT and sp[k] == sp[k]:
            srows.append((k, r))
    for k, r in rows:
        r.score = wf[k]
    ev = AN.evaluate([r for _, r in rows], h)
    eras = []
    for e in range(len(L.TEST_ERAS)):
        er = [r for k, r in rows if tab.era[k] == e]
        eras.append(AN.evaluate(er, h) if er else {"n": 0})
    for k, r in srows:
        r.score = sp[k]
    spv = AN.evaluate([r for _, r in srows], h)
    ch = {"walkforward": {"paired": ev.get("paired") or {}, "challenger": ev.get("challenger") or {}, "production": ev.get("production") or {},
                          "ls_challenger": ev.get("ls_challenger") or {}, "ls_production": ev.get("ls_production") or {}},
          "split": {"paired": spv.get("paired") or {}}, "eras": [{"paired": (e or {}).get("paired") or {}} for e in eras]}
    g = AN.gates(ch, lab)
    wk = ctx.weekly_ric(wf)
    vsD, eras_vsD, g["eras_vsD"], okD = _vs(ctx, wk, D_weekly)
    g["vsD_t"], g["vsD_mean"] = vsD.get("t"), vsD.get("mean")
    g["G5"] = okD
    vsE = eras_vsE = None
    if E_weekly is not None:
        # G5 = beat BOTH learned models already in live shadow (global D and hierarchy E) — tightened on 2026-09-26 when
        # the learned engine's E walk-forward was found to be under-reported (it was scored with the validated model's K)
        vsE, eras_vsE, g["eras_vsE"], okE = _vs(ctx, wk, E_weekly)
        g["vsE_t"], g["vsE_mean"] = vsE.get("t"), vsE.get("mean")
        g["G5_D"], g["G5_E"] = okD, okE
        g["G5"] = okD and okE
    ls20 = ls_stats(ctx, wf, 0.2, costs)
    return {"name": name, "walkforward": L._slim_alpha(ev), "eras": [L._slim_alpha(e) for e in eras], "split": L._slim_alpha(spv),
            "gates": g, "vsD": {k: L._r(v, 4) for k, v in vsD.items()}, "eras_vsD": L._rl(eras_vsD, 4),
            "vsE": {k: L._r(v, 4) for k, v in (vsE or {}).items()}, "eras_vsE": L._rl(eras_vsE, 4) if eras_vsE else None,
            "ls20": {k: L._r(v, 5) for k, v in ls20.items() if k != "weekly"}, "churn": L._r(rank_churn(ctx, wf), 4)}


def thresholds(ctx: Ctx, s: array, costs: array, fracs=(0.5, 0.3, 0.2, 0.1, 0.05), round_trip: bool = False) -> List[dict]:
    out = []
    for f in fracs:
        st = ls_stats(ctx, s, f, costs, round_trip)
        fl = ls_stats(ctx, s, f, array("d", repeat(FLAT_COST, ctx.tab.n)), round_trip)
        out.append({"frac": f, **{k: L._r(v, 5) for k, v in st.items() if k != "weekly"}, "net_flat10bp": L._r(fl.get("net"), 5)})
    return out


CONV_EDGES = [0.0, 0.05, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95, 1.0000001]


def conviction(ctx: Ctx, s: array) -> dict:
    """Within-week score percentile → the relative-return distribution (OOS walk-forward records)."""
    tab, h = ctx.tab, ctx.tab.h
    pr = week_rank(tab, s)
    med = {}
    for wk, a, b, _ in tab.weeks:
        ys = sorted(tab.rec[tab.perm[k]].yr for k in range(a, b))
        med[wk] = ys[len(ys) // 2] if ys else 0.0
    buckets = []
    for lo, hi in zip(CONV_EDGES[:-1], CONV_EDGES[1:]):
        by: Dict[int, List[float]] = {}
        allv = []
        for k in range(tab.n):
            p = pr[k]
            if p != p or tab.era[k] < 0:
                continue
            if lo <= p + 0.5 < hi:
                v = math.expm1(tab.rec[k].yr - med[tab.wk[k]])
                by.setdefault(tab.wk[k], []).append(v)
                allv.append(v)
        if len(allv) < 50:
            buckets.append({"bucket": f"{int(lo * 100)}–{int(min(hi, 1) * 100)}", "n": len(allv)})
            continue
        cm = L.clustered({w: sum(v) / len(v) for w, v in by.items()}, h)
        allv.sort()
        neg = [v for v in allv if v < 0]
        m = sum(allv) / len(allv)
        buckets.append({"bucket": f"{int(lo * 100)}–{int(min(hi, 1) * 100)}", "n": len(allv), "mean": m, "median": allv[len(allv) // 2],
                        "hit": sum(1 for v in allv if v > 0) / len(allv), "vol": math.sqrt(sum((v - m) ** 2 for v in allv) / (len(allv) - 1)),
                        "downside": (sum(neg) / len(neg)) if neg else 0.0,
                        "ci": [cm["mean"] - 1.96 * cm["se"], cm["mean"] + 1.96 * cm["se"]] if cm.get("se") else None})
    means = [b.get("mean") for b in buckets]
    mono = all(m is not None for m in means) and all(b >= a for a, b in zip(means, means[1:]))
    rel = None
    if all(m is not None for m in means):
        rk = L._ranks(means)
        n = len(rk)
        rel = 1 - 6 * sum((i + 1 - r) ** 2 for i, r in enumerate(rk)) / (n * (n * n - 1))
    top = [abs(m) for m in means[len(means) // 2:] if m is not None]
    scale = (sum(top) / len(top)) if top else None
    for b in buckets:
        b["conviction_multiplier"] = (b["mean"] / scale) if scale and b.get("mean") is not None else None
    return {"buckets": [{k: (L._r(v, 5) if isinstance(v, float) else v) for k, v in b.items()} for b in buckets], "monotonic": mono,
            "alpha_reliability": L._r(rel, 3)}


def neutralized_ic(ctx: Ctx, scores: Dict[str, array]) -> dict:
    """Weekly rank IC against the relative return after removing, cross-sectionally, market beta, class / product type,
    value and volatility exposures (size: no point-in-time market cap in the store — not neutralised)."""
    tab = ctx.tab
    names = tab.names
    jb, jv, jo = names.index("beta_252"), names.index("earnings_yield"), names.index("vol_20")
    out = {k: {} for k in scores}
    raw = {k: {} for k in scores}
    for wk, a, b, _ in tab.weeks:
        idx = [tab.perm[k] for k in range(a, b) if tab.wa[tab.perm[k]] > 0 and tab.era[tab.perm[k]] >= 0]
        if len(idx) < 20:
            continue
        cls = sorted({tab.paths[tab.asset[k]][1] for k in idx})
        X = [[1.0, tab.X[jb][k], tab.X[jv][k], tab.X[jo][k]] + [1.0 if tab.paths[tab.asset[k]][1] == c else 0.0 for c in cls[1:]] for k in idx]
        y = [tab.y[k] for k in idx]
        p = len(X[0])
        A = [[sum(X[r][i] * X[r][j] for r in range(len(X))) + (1e-6 if i == j else 0.0) for j in range(p)] for i in range(p)]
        bb = [sum(X[r][i] * y[r] for r in range(len(X))) for i in range(p)]
        beta = L._chol_solve(A, bb)
        res = [yy - sum(bi * xi for bi, xi in zip(beta, xr)) for yy, xr in zip(y, X)]
        rr = L._ranks(res)
        ry = L._ranks(y)
        n = len(idx)
        mr = (n + 1) / 2
        for key, s in scores.items():
            sv = [s[k] for k in idx]
            if any(v != v for v in sv):
                continue
            rs = L._ranks(sv)
            for tgt, dst in ((rr, out), (ry, raw)):
                num = sum((x - mr) * (q - mr) for x, q in zip(rs, tgt))
                den = math.sqrt(sum((x - mr) ** 2 for x in rs) * sum((q - mr) ** 2 for q in tgt))
                if den > 0:
                    dst[key][wk] = num / den
    return {k: {"raw": {kk: L._r(vv, 4) for kk, vv in L.clustered(raw[k], tab.h).items()},
                "neutralized": {kk: L._r(vv, 4) for kk, vv in L.clustered(out[k], tab.h).items()}} for k in scores}


# ================================================================== the 1W study
def contribution(ctx: Ctx, base: Model, new: Model) -> Optional[List[Optional[float]]]:
    """Why a global model differs from the learned global one: for every signal, the change in mean weekly OOS rank IC
    when ONLY that signal's weight is moved from D's to the new model's (per era, with each era's own fit)."""
    if new.wcut(L.TEST_ERAS[0][0]) is None and new.wcut(L.TEST_ERAS[1][0]) is None:
        return None
    tab, lr = ctx.tab, ctx.lr
    dw = {}
    for e in range(len(L.TEST_ERAS)):
        cut = L.TEST_ERAS[e][0]
        a, b = base.wcut(cut), new.wcut(cut)
        if a is None or b is None or cut not in lr.fits:
            continue
        rms = ctx.rms(cut)
        dw[e] = [((b[i] - a[i]) / rms[i]) if rms[i] > 0 else 0.0 for i in range(ctx.P)]
    base_ric = ctx.weekly_ric(base.wf)
    m0 = sum(base_ric.values()) / len(base_ric)
    out = []
    for i in range(ctx.P):
        s = array("d", base.wf)
        any_ = False
        for e, d in dw.items():
            if not d[i]:
                continue
            any_ = True
            for a_, (s0, s1) in tab.slices.items():
                lo, hi = lr._era_range(s0, s1, e)
                if lo < hi:
                    s[lo:hi] = array("d", map(add, s[lo:hi], map(mul, ctx.cols[i][lo:hi], repeat(d[i]))))
        if not any_:
            out.append(0.0)
            continue
        r = ctx.weekly_ric(s)
        out.append(sum(r.values()) / len(r) - m0)
    return out


def hierarchy_mean(tab: L.Table, fin: dict) -> List[float]:
    """The hierarchy's deployable weights averaged over assets, weighted by each asset's number of records (its global
    node equals the learned global model's; this shows where the children move it on average)."""
    P = tab.P
    acc, tot = [0.0] * P, 0
    for a, (s0, s1) in tab.slices.items():
        wv = L.weights_at(fin["tree"], tab.paths[a], fin["depth"])
        n = s1 - s0
        acc = [x + n * y for x, y in zip(acc, wv)]
        tot += n
    return [x / tot for x in acc] if tot else acc


def hierarchy_mean_post(db_path: str, final: str, progress=None) -> dict:
    """For a run that predates res['hierarchy_mean']: refit the hierarchy on all matured 1W data at its final choice."""
    from ..data.store import Store
    from .research import Research
    st = Store(db_path)
    try:
        rows, _, names = L.build_rows(st, Research(st), "1W", progress or (lambda m: None))
    finally:
        st.close()
    tab = L.Table(rows, names, 5)
    del rows
    lr = L.Learner(tab, "alpha")
    lr.fit_all()
    K, depth = final.split("|")
    return {"weights": L._rl(hierarchy_mean(tab, {"tree": lr.fits[L.FINAL]["W"][int(float(K))], "depth": depth}), 6), "choice": final}


def today_scores(ctx: Ctx, today: List[dict], fin: dict, reg: Optional[Regimes] = None) -> Dict[str, dict]:
    """Today's score of every asset under a model's final (all-matured-data) weights, on the latest research record,
    signals demeaned by the latest cross-section."""
    tab = ctx.tab
    rms = ctx.rms(L.FINAL)
    by_date: Dict[str, List] = {}
    for t in today:
        by_date.setdefault(t["rec"].date, []).append(t["rec"])
    means = {}
    for d, rs in by_date.items():
        pool = rs if len(rs) >= L.MIN_WEEK else [t["rec"] for t in today]
        means[d] = [sum(float(r.x[i]) for r in pool) / len(pool) for i in range(ctx.P)]

    def idx_of(fin_, t):
        r = t["rec"]
        xa = [float(r.x[i]) - means[r.date][i] for i in range(ctx.P)]
        if "global" in fin_ and fin_["global"] is not None:
            w = fin_["global"]
        elif "tree" in fin_:
            w = L.weights_at(fin_["tree"], t["path"], fin_["depth"])
        else:
            return None, None
        c = L._coef(w, rms)
        v = sum(a * b for a, b in zip(c, xa))
        rg = fin_.get("regime")
        if rg:
            dim = rg["dim"]
            st = _today_state(r, dim, today)
            ws = rg["weights"].get(st)
            if ws is not None:
                v += sum(a * b for a, b in zip(L._coef([ws[i] - (fin_.get("global") or w)[i] for i in range(ctx.P)], rms), xa))
        return v, w
    out = {}
    if "blend" in fin:
        dv = {t["asset"]: idx_of(fin["D"], t)[0] for t in today}
        ev = {t["asset"]: idx_of(fin["E"], t)[0] for t in today}
        rd, re_ = _rank_map(dv), _rank_map(ev)
        for t in today:
            a = t["asset"]
            if a in rd and a in re_:
                out[a] = {"score": fin["blend"] * rd[a] + (1 - fin["blend"]) * re_[a], "weights": None}
        return out
    for t in today:
        v, w = idx_of(fin, t)
        if v is not None:
            out[t["asset"]] = {"score": v, "weights": L._rl(w, 5) if w else None}
    return out


def _today_state(r, dim: str, today) -> Optional[str]:
    rg = r.reg or (None,) * 4
    if dim == "volatility":
        return rg[1]
    if dim == "market":
        return rg[0]
    if dim == "rates":
        return rg[2]
    if dim == "risk":
        return None if rg[0] is None or rg[1] is None else ("risk-on" if rg[0] == "bull" and rg[1] == "low_vol" else "risk-off")
    return None


def _rank_map(v: Dict[str, Optional[float]]) -> Dict[str, float]:
    items = [(a, x) for a, x in v.items() if x is not None]
    if len(items) < 2:
        return {}
    rk = L._ranks([x for _, x in items])
    n = len(items)
    return {a: (r - 1) / (n - 1) - 0.5 for (a, _), r in zip(items, rk)}


def logo(rows: List[dict], names: List[str], h: int, groups: Dict[str, Callable[[dict], bool]], say) -> Dict[str, dict]:
    """Leave one group out: rebuild the records without the group, refit the learned global model walk-forward, and
    compare with production on the remaining records."""
    out = {}
    for g, excl in groups.items():
        sub = [r for r in rows if not excl(r)]
        if len(sub) < 5000:
            continue
        t0 = time.time()
        tab = L.Table(sub, names, h)
        ctx = Ctx(tab, base=False)
        wf, sp = ctx.score_global(lambda cut: ctx.lr.fits[cut]["W"][L.K_DEFAULT]["global"])
        from . import alphanext as AN
        rs = []
        for k in range(tab.n):
            if tab.era[k] >= 0 and tab.wa[k] > 0 and wf[k] == wf[k]:
                tab.rec[k].score = wf[k]
                rs.append(tab.rec[k])
        ev = AN.evaluate(rs, h)
        out[g] = {"records": tab.n, "assets": len(tab.slices), "rank_ic": L._r((ev.get("challenger") or {}).get("rank_ic"), 4),
                  "production": L._r((ev.get("production") or {}).get("rank_ic"), 4),
                  "d": L._r((ev.get("paired") or {}).get("mean"), 4), "t": L._r((ev.get("paired") or {}).get("t"), 3)}
        say(f"LOGO without {g}: Δ {out[g]['d']} (t {out[g]['t']}) ({time.time() - t0:.0f}s)")
    return out


def group_ic(ctx: Ctx, scores: Dict[str, array]) -> Dict[str, dict]:
    """Within-group weekly rank IC (each class / product type as its own cross-section, ≥ 8 names a week)."""
    tab = ctx.tab
    gof = {a: tab.paths[a][1].split(":", 1)[1] if len(tab.paths[a]) > 1 else "?" for a in tab.slices}
    out: Dict[str, dict] = {}
    for g in sorted(set(gof.values())):
        res = {}
        for key, s in scores.items():
            vals = {}
            for wk, a, b, _ in tab.weeks:
                idx = [tab.perm[k] for k in range(a, b) if gof[tab.asset[tab.perm[k]]] == g and tab.era[tab.perm[k]] >= 0
                       and s[tab.perm[k]] == s[tab.perm[k]] and tab.wa[tab.perm[k]] > 0]
                if len(idx) < 8:
                    continue
                rs, ry = L._ranks([s[k] for k in idx]), L._ranks([tab.y[k] for k in idx])
                n = len(idx)
                mr = (n + 1) / 2
                den = math.sqrt(sum((x - mr) ** 2 for x in rs) * sum((y - mr) ** 2 for y in ry))
                if den > 0:
                    vals[wk] = sum((x - mr) * (y - mr) for x, y in zip(rs, ry)) / den
            c = L.clustered(vals, tab.h)
            res[key] = {"mean": L._r(c.get("mean"), 4), "t": L._r(c.get("t"), 3), "weeks": c.get("weeks")}
        out[g] = res
    return out


def dir_alpha(ctx: Ctx, alpha_wf: array) -> dict:
    """Does the validated 1W Alpha percentile add to P(R_1W > 0) beyond the PIT prior? Logistic [1, z, a] fitted on
    walk-forward records matured before each era (their Alpha is itself out of sample), the Directional program's
    fixed gates against prior-only and prior + production."""
    from . import directional as D
    tab = ctx.tab
    pct = week_rank(tab, alpha_wf)
    rnd = random.Random(5)
    preds = {"prior": {}, "current": {}, "alpha": {}}
    ok = [k for k in range(tab.n) if tab.z[k] is not None and tab.o[k] is not None and pct[k] == pct[k]]
    for e in range(len(L.TEST_ERAS)):
        cut = L.TEST_ERAS[e][0]
        train = [k for k in ok if tab.end[k] < cut]
        test = [k for k in ok if tab.era[k] == e]
        if len(train) < 5000 or not test:
            continue
        sub = train if len(train) <= 20000 else rnd.sample(train, 20000)
        o = [float(tab.o[k]) for k in sub]
        w0 = L._platt([[1.0, tab.z[k]] for k in sub], o)
        w1 = L._platt([[1.0, tab.z[k], (tab.raw[k] or 0.0) / 100.0] for k in sub], o)
        w2 = L._platt([[1.0, tab.z[k], pct[k]] for k in sub], o)
        for k in test:
            preds["prior"][k] = L._sig(w0[0] + w0[1] * tab.z[k])
            preds["current"][k] = L._sig(w1[0] + w1[1] * tab.z[k] + w1[2] * (tab.raw[k] or 0.0) / 100.0)
            preds["alpha"][k] = L._sig(w2[0] + w2[1] * tab.z[k] + w2[2] * pct[k])
    recs = [tab.rec[k] for k in range(tab.n)]
    idx = sorted(preds["alpha"])
    if not idx:
        return {"n": 0}
    from . import dirnext as DN
    m = D.dir_metrics([recs[k] for k in idx], [preds["alpha"][k] for k in idx], tab.h)
    pr = D.dir_metrics([recs[k] for k in idx], [preds["prior"][k] for k in idx], tab.h)
    vp, vc = D.paired(recs, tab.h, preds, "alpha", "prior"), D.paired(recs, tab.h, preds, "alpha", "current")
    cal = DN.calibration([recs[k] for k in idx], [preds["alpha"][k] for k in idx])
    eras = []
    for e in range(len(L.TEST_ERAS)):
        ids = [k for k in idx if tab.era[k] == e]
        sub = {n_: {k: preds[n_][k] for k in ids} for n_ in ("alpha", "prior")}
        eras.append(D.paired(recs, tab.h, sub, "alpha", "prior") if ids else {})
    g = L._dir_gates(m, vp, vc, pr, cal, {}, eras)
    return {"metrics": L._slim_dir(m), "prior": L._slim_dir(pr), "vs_prior": L._slim_pair(vp), "vs_current": L._slim_pair(vc),
            "calibration_slope": L._r(cal.get("slope"), 3), "eras": [L._slim_pair(e) for e in eras], "gates": g,
            "passed": bool(g.get("G1") and g.get("G3") and g.get("G4"))}


def study_1w(store, research, progress=None, only: Optional[set] = None, with_logo: bool = True) -> dict:
    say = progress or (lambda m: None)
    t0 = time.time()
    rows, today, names = L.build_rows(store, research, "1W", say, only)
    tab = L.Table(rows, names, 5)
    ctx = Ctx(tab, lambda m: say(f"1W {m}"))
    costs = record_costs(tab)
    base = fam_baselines(ctx)
    D, E = base["D"], base["E"]
    say(f"1W baselines: D and E ({time.time() - t0:.0f}s)")
    reg = Regimes(ctx)
    stab = Stability(ctx, reg)
    orient = L._orientation(tab)
    fams: Dict[str, Model] = {}
    for fn, args in ((fam_ranking, ()), (fam_stability, (stab, E)), (fam_regime, (reg, E)), (fam_time, ()), (fam_clusters, ()),
                     (fam_signs, (stab, orient)), (fam_interactions, (reg,))):
        t1 = time.time()
        r = fn(ctx, *args)
        fams.update(r)
        say(f"1W family {fn.__name__[4:]} ({time.time() - t1:.0f}s)")
    fams["blend"] = fam_blend(ctx, D, E)
    fams.update(fam_turnover(ctx, {"learned global": D, "learned hierarchy": E}, costs))
    models = {k: v for k, v in fams.items() if isinstance(v, Model)}
    extras = {k: v for k, v in fams.items() if not isinstance(v, Model)}
    # nested model selection across the families (PIT families only)
    cand = {k: (m.wf, m.sp) for k, m in models.items() if m.pit}
    meta = ctx.pick({"learned global (D)": (D.wf, D.sp), **cand}, default="learned global (D)")
    models["nested model selection"] = Model("nested selection across every family", "meta", meta,
                                             lambda k: (models[k].final_fn(models[k].final) if k in models and models[k].final_fn else
                                                        D.final_fn(D.final)))
    Dw = ctx.weekly_ric(D.wf)
    Ew = ctx.weekly_ric(E.wf)
    prod = array("d", (v if v is not None else NAN for v in tab.raw))
    res = {"lab": "1W", "records": tab.n, "assets": len(tab.slices), "signals": names, "models": {}}
    for k, m in {"learned global (D)": D, "learned hierarchy (E)": E, **models}.items():
        ev = evaluate(ctx, m.name, m.wf, m.sp, Dw, costs, "1W", Ew)
        ev.update({"family": m.family, "pit": m.pit, "note": m.note, "choices": {c: str(v) for c, v in m.choices.items()}, "final": str(m.final)})
        res["models"][k] = ev
    say(f"1W models evaluated ({time.time() - t0:.0f}s)")
    # reproduction check against the recorded learned run
    res["reproduction"] = {"D_rank_ic": res["models"]["learned global (D)"]["walkforward"]["challenger"].get("rank_ic"),
                           "E_rank_ic": res["models"]["learned hierarchy (E)"]["walkforward"]["challenger"].get("rank_ic")}
    # thresholds, costs, conviction, neutralisation, groups
    key_models = {"production": prod, "learned global (D)": D.wf, "learned hierarchy (E)": E.wf}
    best = max((k for k in models if models[k].pit and k != "nested model selection"),
               key=lambda k: min(res["models"][k]["gates"].get("vsD_t") or -99, res["models"][k]["gates"].get("vsE_t") or -99))
    key_models[best] = models[best].wf
    key_models["nested model selection"] = models["nested model selection"].wf
    res["best"] = best
    res["thresholds"] = {k: thresholds(ctx, s, costs) for k, s in key_models.items()}
    res["conviction"] = {k: conviction(ctx, s) for k, s in key_models.items()}
    res["neutralization"] = neutralized_ic(ctx, key_models)
    res["groups"] = group_ic(ctx, {k: key_models[k] for k in ("production", "learned global (D)", "learned hierarchy (E)")})
    res["churn"] = {k: L._r(rank_churn(ctx, s), 4) for k, s in key_models.items()}
    say(f"1W thresholds, conviction, neutralisation ({time.time() - t0:.0f}s)")
    # why: per-signal contribution of the best global challenger(s) versus D
    res["contributions"] = {}
    for k, m in models.items():
        if not m.pit or m.family in ("baseline", "meta"):
            continue
        if res["models"][k]["gates"].get("vsD_t") is not None and (res["models"][k]["gates"]["vsD_t"] or 0) >= 1:
            c = contribution(ctx, D, m)
            if c is not None:
                res["contributions"][k] = L._rl(c, 5)
    # weights today
    res["weights"] = {"D": L._rl(D.final_fn(D.final)["global"], 6)}
    res["hierarchy_mean"] = {"weights": L._rl(hierarchy_mean(tab, E.final_fn(E.final)), 6), "choice": str(E.final)}
    for k, m in models.items():
        if m.final_fn is None:
            continue
        try:
            fin = m.final_fn(m.final)
        except Exception:  # noqa: BLE001
            continue
        if fin and fin.get("global") is not None:
            res["weights"][k] = L._rl(fin["global"], 6)
    res["finals"] = {}
    for k, m in models.items():
        if m.final_fn is None or not m.pit:
            continue
        try:
            fin = m.final_fn(m.final)
        except Exception:  # noqa: BLE001
            continue
        if fin.get("regime") or fin.get("blend") is not None:
            continue                                  # not a linear per-node score: no live scorer
        if fin.get("global") is not None:
            res["finals"][k] = {"depth": "global", "weights": {"global": L._rl(fin["global"], 7)}}
        elif fin.get("tree") is not None:
            dep = fin["depth"]
            res["finals"][k] = {"depth": dep, "weights": {nd: L._rl(v, 7) for nd, v in fin["tree"].items()
                                                          if L.LEVELS.index(L.level_of(nd)) <= L.LEVELS.index(dep)}}
    res["today"] = {}
    for k in ["learned global (D)", "learned hierarchy (E)", best, "nested model selection"]:
        m = base.get("D") if k == "learned global (D)" else (E if k == "learned hierarchy (E)" else models.get(k))
        if m and m.final_fn:
            try:
                res["today"][k] = {a: {"score": L._r(v["score"], 5)} for a, v in today_scores(ctx, today, m.final_fn(m.final), reg).items()}
            except Exception as e:  # noqa: BLE001
                res["today"][k] = {"error": str(e)}
    res["production_today"] = {t["asset"]: L._r(t["rec"].raw, 4) for t in today}
    res["stability_today"] = {"classes": stab.at(L.FINAL)["classes"], "stable_sign": stab.at(L.FINAL)["stable_sign"],
                              "var": L._rl(stab.at(L.FINAL)["var"], 4)}
    res["clusters"] = [[names[i] for i in c] for c in extras.get("_clusters", [])]
    res["interaction_singles"] = {nm: {"choices": {c: str(v) for c, v in r.get("choices", {}).items()},
                                       "alone": _eval_short(ctx, r["alone"][0], Dw)} for nm, r in (extras.get("_singles") or {}).items()}
    res["interaction_weights"] = {k: L._r(v, 5) for k, v in (extras.get("_interaction_weights") or {}).items()}
    res["orientation_final"] = orient.get(L.FINAL)
    res["dir_alpha"] = dir_alpha(ctx, models[best].wf if res["models"][best]["gates"].get("G5") else E.wf)
    res["dir_alpha_model"] = best if res["models"][best]["gates"].get("G5") else "learned hierarchy (E)"
    # the alpha map for the hedge research: every asset's OOS percentile of the hierarchy model by date
    pct = week_rank(tab, E.wf)
    amap: Dict[str, List[Tuple[str, float]]] = {}
    for k in range(tab.n):
        if pct[k] == pct[k]:
            amap.setdefault(tab.asset[k], []).append((tab.date[k], L._r(pct[k], 4)))
    res["_alpha_map"] = {a: sorted(v) for a, v in amap.items()}
    if with_logo:
        groups = {"Equity class": lambda r: r["path"][1] == "class:Equity", "Rates class": lambda r: r["path"][1] == "class:Rates",
                  "Credit class": lambda r: r["path"][1] == "class:Credit", "Commodity class": lambda r: r["path"][1] == "class:Commodity",
                  "FX class": lambda r: r["path"][1] == "class:FX", "crypto": lambda r: r["path"][1] == "class:Crypto",
                  "bond products (rates + credit)": lambda r: r["path"][1] in ("class:Rates", "class:Credit"),
                  "ETFs": lambda r: (r["rec"].meta or {}).get("asset_class") == "ETF",
                  "inverse / leveraged products": lambda r: "leveraged / inverse ETF" in r["path"][2],
                  "mega-cap stocks": lambda r: r["asset"] in MEGA_CAPS,
                  "Information Technology": lambda r: "Information Technology" in "/".join(r["path"]),
                  "Financials": lambda r: "Financials" in "/".join(r["path"]), "Energy": lambda r: "/Energy" in "/".join(r["path"]),
                  "Health Care": lambda r: "Health Care" in "/".join(r["path"])}
        res["logo"] = logo(rows, names, 5, groups, lambda m: say(f"1W {m}"))
    res["seconds"] = round(time.time() - t0, 1)
    return res


def _eval_short(ctx: Ctx, s: array, Dw) -> dict:
    wk = ctx.weekly_ric(s)
    c = L.clustered(wk, ctx.tab.h)
    d = L.clustered({w: wk[w] - Dw[w] for w in wk if w in Dw}, ctx.tab.h)
    return {"rank_ic": L._r(c.get("mean"), 4), "t": L._r(c.get("t"), 3), "vsD": L._r(d.get("mean"), 4), "vsD_t": L._r(d.get("t"), 3)}


# ================================================================== 1D: a cost-aware model
def _weekly_zero(ctx: Ctx, weekly: Dict[int, float]) -> Dict[int, float]:
    """Formation weeks the strategy could have traded, with 0 where it stood aside (so standing aside is not free luck)."""
    step = max(1, int(round(ctx.tab.h / 5.0)))
    out = {}
    for wk, a, b, _ in ctx.tab.weeks[::step]:
        if ctx.tab.era[ctx.tab.perm[a]] >= 0:
            out[wk] = weekly.get(wk, 0.0)
    return out


def study_1d(store, research, progress=None, only: Optional[set] = None) -> dict:
    """1D: lower turnover, larger edge thresholds, extreme scores only, stronger shrinkage and regime filters — every
    choice made by the inner NET long-short (one-day trades from weekly snapshots: a full round trip each)."""
    say = progress or (lambda m: None)
    t0 = time.time()
    rows, today, names = L.build_rows(store, research, "1D", say, only)
    tab = L.Table(rows, names, 1)
    del rows
    ctx = Ctx(tab, lambda m: say(f"1D {m}"))
    costs = record_costs(tab)
    base = fam_baselines(ctx)
    D, E = base["D"], base["E"]
    reg = Regimes(ctx)
    prod = array("d", (v if v is not None else NAN for v in tab.raw))
    shr = {}
    for lam in (1e3, 1e4, 1e5):
        shr[f"global λ={lam:g}"] = ctx.score_global(lambda cut, lam=lam: L.ridge(ctx.lr.fits[cut]["ns"]["global"], ctx.rms(cut), [0.0] * ctx.P, lam))
    models = {"learned global (D)": (D.wf, D.sp), "learned hierarchy (E)": (E.wf, E.sp), **shr}
    filters = {"none": None, "low volatility only": ("volatility", "low_vol"), "high volatility only": ("volatility", "high_vol"),
               "bull only": ("market", "bull"), "bear only": ("market", "bear")}
    opts, spec = {}, {}
    for mk, (wf, sp) in models.items():
        for fk, flt in filters.items():
            if flt:
                d, st = flt
                mask = [s == st for s in reg.state[d]]
                wf2 = array("d", (v if m else NAN for v, m in zip(wf, mask)))
                sp2 = array("d", (v if m else NAN for v, m in zip(sp, mask)))
            else:
                wf2, sp2 = wf, sp
            for frac in (0.5, 0.2, 0.1, 0.05):
                key = f"{mk} | {fk} | {int(frac * 100)}%"
                opts[key] = (wf2, sp2)
                spec[key] = frac
    metric_cache = {}

    def metric_for(key):
        def m(s):
            if key not in metric_cache:
                metric_cache[key] = _weekly_zero(ctx, ls_weekly(ctx, s, spec[key], costs, round_trip=True))
            return metric_cache[key]
        return m
    # pick with a per-option metric
    weekly = {k: metric_for(k)(v[0]) for k, v in opts.items()}
    chosen = {}
    for cut in [a for a, _ in L.TEST_ERAS] + [L.SPLIT, L.FINAL]:
        inner = ctx.lr.inner_weeks(cut)
        if len(inner) < L.MIN_INNER_WEEKS:
            chosen[cut] = None
            continue
        best, bv = None, 0.0
        for k in opts:
            v = [x for w, x in weekly[k].items() if w in inner]
            m = sum(v) / len(v) if v else -1e9
            if m > bv:
                best, bv = k, m
        chosen[cut] = best                               # None = stand aside (no option had a positive inner net)
    # the nested cost-aware strategy, era by era
    net_weeks: Dict[int, float] = {}
    for e in range(len(L.TEST_ERAS)):
        k = chosen.get(L.TEST_ERAS[e][0])
        for wk in [w for w in weekly[next(iter(opts))] if ctx.lr.week_era.get(w) == e]:
            net_weeks[wk] = weekly[k].get(wk, 0.0) if k else 0.0
    c = L.clustered(net_weeks, 5)
    eras = []
    for e in range(len(L.TEST_ERAS)):
        v = [x for w, x in net_weeks.items() if ctx.lr.week_era.get(w) == e]
        eras.append(L._r(sum(v) / len(v), 6) if v else None)
    res = {"lab": "1D", "records": tab.n, "note": "1D records are weekly snapshots: each one-day trade is a full round trip; turnover per trade 100%.",
           "thresholds": {k: thresholds(ctx, s, costs, round_trip=True) for k, s in
                          {"production": prod, "learned global (D)": D.wf, "learned hierarchy (E)": E.wf}.items()},
           "nested_cost_aware": {"choices": {k: v for k, v in chosen.items()}, "net_per_trade": L._r(c.get("mean"), 6), "t": L._r(c.get("t"), 3),
                                 "weeks": c.get("weeks"), "eras": eras,
                                 "eras_positive": sum(1 for x in eras[:4] if x is not None and x > 0),
                                 "active_share": L._r(sum(1 for v in net_weeks.values() if v) / max(1, len(net_weeks)), 3)},
           "models": {k: _eval_short(ctx, v[0], ctx.weekly_ric(D.wf)) for k, v in models.items()},
           "production": _eval_short(ctx, prod, ctx.weekly_ric(D.wf)), "seconds": round(time.time() - t0, 1)}
    g = res["nested_cost_aware"]
    g["profitable"] = bool((g["t"] or 0) >= 2 and (g["net_per_trade"] or 0) > 0 and g["eras_positive"] >= 3)
    return res


# ================================================================== 1M–12M: sample limits and lower-dimensional models
FUND_FAMS = ["Valuation", "Fundamental Quality", "Fundamental Growth"]
SUBSETS = {"fundamental only": FUND_FAMS, "relative value + fundamental": FUND_FAMS + ["Relative Value"],
           "macro + fundamental": FUND_FAMS + ["Rates", "Credit", "Macro"]}


def study_long(store, research, lab: str, progress=None, only: Optional[set] = None) -> dict:
    from .. import shaffer_score as cfg
    say = progress or (lambda m: None)
    t0 = time.time()
    from .lab import LAB_HORIZONS
    h = dict(LAB_HORIZONS)[lab]
    rows, today, names = L.build_rows(store, research, lab, say, only)
    tab = L.Table(rows, names, h)
    del rows
    ctx = Ctx(tab, lambda m: say(f"{lab} {m}"), base=False)
    P = ctx.P
    fam_of = [cfg.FAMILY_OF[n] for n in names]
    D = Model("learned global (D)", "baseline", {"wf": None, "sp": None}, None)
    D.wf, D.sp = ctx.score_global(lambda cut: ctx.lr.fits[cut]["W"][L.K_DEFAULT]["global"])
    Dw = ctx.weekly_ric(D.wf)
    stab = Stability(ctx)
    orient = L._orientation(tab)
    variants: Dict[str, Tuple[array, array]] = {}
    # stronger shrinkage
    opts = {f"λ={lam:g}": ctx.score_global(lambda cut, lam=lam: L.ridge(ctx.lr.fits[cut]["ns"]["global"], ctx.rms(cut), [0.0] * P, lam))
            for lam in (L.G_LAMBDA, 1e3, 1e4, 1e5)}
    r = ctx.pick(opts, default=f"λ={L.G_LAMBDA:g}")
    variants["stronger shrinkage (λ nested)"] = (r["wf"], r["sp"])
    # family-level weights
    fams = list(dict.fromkeys(fam_of))

    def w_family(cut):
        g = ctx.lr.fits[cut]["ns"]["global"]
        o = orient.get(cut) or [1.0] * P
        M = [[(o[i] / sum(1 for f in fam_of if f == fams[j])) if fam_of[i] == fams[j] else 0.0 for j in range(len(fams))] for i in range(P)]
        S = g.matrix()
        FtF = [[sum(M[i][a] * S[i][j] * M[j][b] for i in range(P) if M[i][a] for j in range(P) if M[j][b]) for b in range(len(fams))] for a in range(len(fams))]
        Fty = [sum(M[i][a] * g.xy[i] for i in range(P) if M[i][a]) for a in range(len(fams))]
        rf = [math.sqrt(FtF[a][a] / g.sw) if FtF[a][a] > 0 else 0.0 for a in range(len(fams))]
        act = [a for a in range(len(fams)) if rf[a] > 0]
        A = [[FtF[a][b] / (rf[a] * rf[b]) + (L.G_LAMBDA if a == b else 0.0) for b in act] for a in act]
        sol = L._chol_solve(A, [Fty[a] / rf[a] for a in act])
        wf_ = [0.0] * len(fams)
        for k, a in enumerate(act):
            wf_[a] = sol[k] / rf[a]
        return [sum(M[i][a] * wf_[a] for a in range(len(fams))) for i in range(P)]
    variants["family-level weights (15)"] = ctx.score_global(w_family, coefs=True)
    for kk in (10, 20):
        def w_top(cut, kk=kk):
            g = ctx.lr.fits[cut]["ns"]["global"]
            off = L._offsets(P)
            sc = [abs(g.xy[i]) / math.sqrt(g.xx[off[i]]) if g.xx[off[i]] > 0 else 0.0 for i in range(P)]
            idx = sorted(range(P), key=lambda i: -sc[i])[:kk]
            return ridge_vec(g, ctx.rms(cut), [0.0] * P, [L.G_LAMBDA] * P, idx=idx)
        variants[f"top {kk} signals (training correlation)"] = ctx.score_global(w_top)

    def w_stable(cut):
        cl = stab.at(cut)["classes"]
        idx = [i for i in range(P) if cl[i] == "STABLE"]
        g = ctx.lr.fits[cut]["ns"]["global"]
        return ridge_vec(g, ctx.rms(cut), [0.0] * P, [L.G_LAMBDA] * P, idx=idx) if idx else [0.0] * P
    variants["stable signals only"] = ctx.score_global(w_stable)
    for nm, fl in SUBSETS.items():
        idx = [i for i in range(P) if fam_of[i] in fl]
        variants[nm] = ctx.score_global(lambda cut, idx=idx: ridge_vec(ctx.lr.fits[cut]["ns"]["global"], ctx.rms(cut), [0.0] * P, [L.G_LAMBDA] * P, idx=idx))
    # broader hierarchy: class level only, K nested
    tree_opts = {}
    for K in L.K_GRID:
        tree_opts[str(K)] = ctx.score_tree(lambda cut, K=K: ctx.lr.fits[cut]["W"][K], "class")
    r = ctx.pick(tree_opts)
    variants["class-level hierarchy (K nested)"] = (r["wf"], r["sp"])
    meta = ctx.pick({"learned global (D)": (D.wf, D.sp), **variants}, default="learned global (D)")
    variants["nested selection (reduced dimension)"] = (meta["wf"], meta["sp"])
    costs = record_costs(tab)
    res = {"lab": lab, "records": tab.n, "assets": len(tab.slices), "models": {}}
    for k, (wf, sp) in {"learned global (D)": (D.wf, D.sp), **variants}.items():
        res["models"][k] = evaluate(ctx, k, wf, sp, Dw, costs, lab)
    # sample limits
    wk_wf = [wk for wk, e in ctx.lr.week_era.items() if e >= 0]
    xs = [b - a for _, a, b, _ in tab.weeks if tab.era[tab.perm[a]] >= 0]
    fin = stab.at(L.FINAL)
    ts = []
    for i in range(P):
        v = [ew[i] for ew in fin["era_w"]]
        if len(v) >= 3:
            m = sum(v) / len(v)
            sd = math.sqrt(sum((x - m) ** 2 for x in v) / (len(v) - 1))
            ts.append(abs(m / (sd / math.sqrt(len(v)))) if sd > 0 else 0.0)
    res["sample"] = {"weeks": len(wk_wf), "n_eff": L._r(len(wk_wf) * 5.0 / h, 4), "mean_cross_section": L._r(sum(xs) / len(xs), 4) if xs else None,
                     "signals": P, "n_eff_per_signal": L._r(len(wk_wf) * 5.0 / h / P, 3), "coef_era_t_median": L._r(sorted(ts)[len(ts) // 2], 3) if ts else None,
                     "coef_era_t_ge2": sum(1 for t in ts if t >= 2), "classes": {c: fin["classes"].count(c) for c in set(fin["classes"])}}
    res["groups"] = group_ic(ctx, {"production": array("d", (v if v is not None else NAN for v in tab.raw)), "learned global (D)": D.wf})
    res["seconds"] = round(time.time() - t0, 1)
    return res


# ================================================================== extended capability tests
def _rec(y: float):
    from types import SimpleNamespace
    return SimpleNamespace(yr=0.01 * y, raw=None, meta={}, act=(), reg=None)


def capability2(progress=None, n_assets: int = 16) -> dict:
    """New planted effects for the new optimisers: ranking-only, time-decaying, regime-switching, hierarchy-specific and
    transaction-cost-sensitive. Each must be recovered by the machinery that claims to find it."""
    import datetime as _d
    say = progress or (lambda m: None)
    out = {}

    def rec(name, ok, detail):
        out[name] = {"pass": bool(ok), **detail}
        say(f"capability {name}: {'PASS' if ok else 'FAIL'}")

    def rows_of(fn, seed, weeks=1270, P=10, extra=None):
        rnd = random.Random(seed)
        names = ["momentum", "valuation", "idio_vol"] + [f"noise_{k}" for k in range(1, P - 2)]
        d0 = _d.date(2002, 1, 2)
        rows, state, slow = [], "high_vol", {}
        for w in range(weeks):
            if w % 26 == 0:
                state = "high_vol" if rnd.random() < 0.5 else "low_vol"
            d = d0 + _d.timedelta(days=7 * w)
            e = d + _d.timedelta(days=8)
            for k in range(n_assets):
                c, s_ = k % 2, (k // 2) % 2
                path = ["global", f"class:C{c}", f"ptype:C{c}/P", f"sector:C{c}/P/S{s_}", f"asset:X{k:02d}"]
                x = [rnd.gauss(0, 1) for _ in range(P)]
                if extra == "slow":
                    slow[k] = 0.97 * slow.get(k, rnd.gauss(0, 1)) + math.sqrt(1 - 0.97 ** 2) * rnd.gauss(0, 1)
                    x[1] = slow[k]
                y = fn(x, d.year, state, c, s_, rnd)
                rows.append({"asset": f"X{k:02d}", "path": path, "date": d.isoformat(), "end": e.isoformat(), "wk": d.toordinal() // 7, "x": x,
                             "y": y, "z": 0.0, "o": 1 if y > 0 else 0, "reg": ("bull", state, "rising_rates", "expansion"), "rec": _rec(y), "raw": 0.0})
        return rows, names
    # 1. ranking-specific: a monotone, heavy-tailed transform — ranks carry the signal, magnitudes mislead
    rows, names = rows_of(lambda x, yr, st, c, s_, rnd: math.exp(1.5 * (0.5 * x[0] - 0.3 * x[1])) * (1 + 0.2 * rnd.gauss(0, 1)) + 3 * rnd.gauss(0, 1) ** 3, 11)
    tr, tv = L.Table(rows, names, 5, "rank"), L.Table(rows, names, 5, "value")
    cr, cv = Ctx(tr, base=False), Ctx(tv, base=False)
    sr, _ = cr.score_global(lambda cut: cr.lr.fits[cut]["W"][L.K_DEFAULT]["global"])
    sv, _ = cv.score_global(lambda cut: cv.lr.fits[cut]["W"][L.K_DEFAULT]["global"])
    ir, iv = L.clustered(cr.weekly_ric(sr), 5), L.clustered(cr.weekly_ric(sv), 5)
    g = cr.lr.fits[L.FINAL]["W"][L.K_DEFAULT]["global"]
    ok = g[0] > 0 and g[1] < 0 and abs(g[1] / g[0] + 0.6) < 0.2 and (ir["mean"] or 0) > (iv["mean"] or 0)
    rec("ranking-specific effect", ok, {"truth_ratio": -0.6, "recovered_ratio": round(g[1] / g[0], 3) if g[0] else None,
                                         "rank_objective_ric": round(ir["mean"] or 0, 4), "value_regression_ric": round(iv["mean"] or 0, 4)})
    # 2. time-decaying: the relationship moves from valuation to momentum in 2014
    rows, names = rows_of(lambda x, yr, st, c, s_, rnd: (0.5 * x[1] if yr < 2014 else 0.5 * x[0]) + rnd.gauss(0, 1), 12)
    ctx = Ctx(L.Table(rows, names, 5, "value"), base=False)
    tm = fam_time(ctx)
    m = tm["time decay (global)"]
    wF = m.final_fn(m.final)["global"]
    mw = tm["rolling window (global)"]
    ok = m.final not in ("None", None) and mw.final not in ("None", None) and wF[0] > 2 * abs(wF[1])
    rec("time-decaying relationship", ok, {"truth_after_2014": {"momentum": 0.5, "valuation": 0.0}, "chosen_half_life": m.final,
                                           "chosen_window": mw.final, "final_weights": {"momentum": round(wF[0], 3), "valuation": round(wF[1], 3)}})
    # 3. regime-switching recovered by the regime-conditional family
    rows, names = rows_of(lambda x, yr, st, c, s_, rnd: (0.4 if st == "high_vol" else -0.4) * x[0] + 0.3 * x[1] + rnd.gauss(0, 1), 13)
    ctx = Ctx(L.Table(rows, names, 5, "value"))
    reg = Regimes(ctx)
    E = fam_baselines(ctx)["E"]
    fr = fam_regime(ctx, reg, E)["regime-conditional (global)"]
    fin = fr.final_fn(fr.final)
    ws = (fin.get("regime") or {}).get("weights") or {}
    ok = fr.final.startswith("volatility") and abs(ws.get("high_vol", [0])[0] - 0.4) < 0.15 and abs(ws.get("low_vol", [0])[0] + 0.4) < 0.15
    rec("regime-switching relationship", ok, {"truth": {"high_vol": 0.4, "low_vol": -0.4}, "chosen": fr.final,
                                              "recovered": {s: round(v[0], 3) for s, v in ws.items()}})
    # 4. hierarchy-specific: the nested hierarchy must beat the global model out of sample
    rows, names, _ = L.synth("sector", 5, n_assets=n_assets)
    ctx = Ctx(L.Table(rows, names, 5, "value"))
    b = fam_baselines(ctx)
    dD, dE = ctx.weekly_ric(b["D"].wf), ctx.weekly_ric(b["E"].wf)
    d = L.clustered({w: dE[w] - dD[w] for w in dE if w in dD}, 5)
    rec("hierarchy-specific relationship", (d.get("t") or 0) >= 2, {"hierarchy_minus_global_ric": round(d.get("mean") or 0, 4), "t": round(d.get("t") or 0, 2)})
    # 5. transaction-cost-sensitive: a fast signal ranks better gross, a slow one earns more net
    rows, names = rows_of(lambda x, yr, st, c, s_, rnd: 0.2 * x[0] + 0.12 * x[1] + rnd.gauss(0, 1), 14, extra="slow")
    ctx = Ctx(L.Table(rows, names, 5, "value"), base=False)
    fast = ctx.score_global(lambda cut: ridge_vec(ctx.lr.fits[cut]["ns"]["global"], ctx.rms(cut), [0.0] * ctx.P, [L.G_LAMBDA] * ctx.P, idx=[0]))
    slowm = ctx.score_global(lambda cut: ridge_vec(ctx.lr.fits[cut]["ns"]["global"], ctx.rms(cut), [0.0] * ctx.P, [L.G_LAMBDA] * ctx.P, idx=[1]))
    high = array("d", repeat(0.004, ctx.tab.n))
    by_ric = ctx.pick({"fast": fast, "slow": slowm})["final"]
    by_net = ctx.pick({"fast": fast, "slow": slowm}, metric=lambda s: ls_weekly(ctx, s, 0.2, high))["final"]
    rec("transaction-cost-sensitive effect", by_ric == "fast" and by_net == "slow", {"chosen_by_rank_ic": by_ric, "chosen_by_net_after_costs": by_net})
    # 6. no false improvement: on a plain linear world with no time, regime or hierarchy structure, nested selection
    #    across the fine-tune families must not beat the learned global model
    rows, names = rows_of(lambda x, yr, st, c, s_, rnd: 0.3 * x[0] - 0.2 * x[1] + rnd.gauss(0, 1), 15)
    ctx = Ctx(L.Table(rows, names, 5, "value"))
    reg = Regimes(ctx)
    b = fam_baselines(ctx)
    fams = {**fam_time(ctx), **fam_regime(ctx, reg, b["E"]), "blend": fam_blend(ctx, b["D"], b["E"]), **fam_stability(ctx, Stability(ctx, reg), b["E"])}
    meta = ctx.pick({"D": (b["D"].wf, b["D"].sp), **{k: (m.wf, m.sp) for k, m in fams.items()}}, default="D")
    dD, dM = ctx.weekly_ric(b["D"].wf), ctx.weekly_ric(meta["wf"])
    d = L.clustered({w: dM[w] - dD[w] for w in dM if w in dD}, 5)
    rec("no false improvement (plain world)", (d.get("t") or 0) < 2, {"nested_minus_global_ric": round(d.get("mean") or 0, 4), "t": round(d.get("t") or 0, 2),
                                                                      "chosen_today": meta["final"]})
    return out


# ================================================================== orchestration, statuses, versions
VERSION_OF = {"ridge (relative return)": "rank-relret", "elastic net": "rank-elasticnet", "pairwise (RankNet)": "rank-pairwise",
              "listwise (ListNet)": "rank-listwise", "blend": "blend", "stability penalty (global)": "stability",
              "stability penalty (hierarchy)": "stability-hier", "stability classes": "stability-class",
              "regime-conditional (global)": "regime", "regime-conditional (hierarchy)": "regime-hier",
              "time decay (global)": "decay", "time decay (hierarchy)": "decay-hier", "rolling window (global)": "window",
              "rolling window (hierarchy)": "window-hier", "signal clusters": "cluster", "signs": "sign",
              "interactions (economic)": "interact", "learned global + turnover smoothing": "smooth",
              "learned hierarchy + turnover smoothing": "smooth-hier", "nested model selection": "nested"}


def vid_of(lab: str, key: str) -> str:
    return f"alpha-learned-{lab.lower()}-{VERSION_OF.get(key, key.replace(' ', '-'))}-exp"


def _worker(db_path: str, kind: str, lab: str):
    from ..data.store import Store
    from .research import Research
    st = Store(db_path)
    try:
        r = Research(st)
        if kind == "1W":
            return study_1w(st, r)
        if kind == "1D":
            return study_1d(st, r)
        return study_long(st, r, lab)
    finally:
        st.close()


def finalise(res: dict) -> dict:
    """BH FDR across every fine-tuned 1W model (point-in-time families) and, separately, across every 1M–12M variant;
    statuses: LIVE SHADOW ELIGIBLE needs G1–G4 against production, FDR, and — for 1W fine-tunes — G5 against the learned
    global model."""
    from .alphanext import _p
    W = res.get("1W") or {}
    keys = [k for k, v in (W.get("models") or {}).items() if v.get("family") not in ("baseline",) and v.get("pit")]
    for k, r in zip(keys, L.benjamini_hochberg([_p(W["models"][k]["gates"].get("t")) for k in keys])):
        g = W["models"][k]["gates"]
        g["fdr"] = r
        g["passed"] = bool(g.get("G1") and g.get("G2") and g.get("G3") and g.get("G4") and r and g.get("G5"))
        W["models"][k]["status"] = "LIVE SHADOW ELIGIBLE" if g["passed"] else "NOT VALIDATED"
    for k, v in (W.get("models") or {}).items():
        if k not in keys:
            v["status"] = "baseline (live shadow)" if v.get("family") == "baseline" else "descriptive only (selection not PIT)"
    lk = [(lab, k) for lab in ("1M", "3M", "6M", "12M") for k, v in ((res.get(lab) or {}).get("models") or {}).items() if k != "learned global (D)"]
    for (lab, k), r in zip(lk, L.benjamini_hochberg([_p(res[lab]["models"][k]["gates"].get("t")) for lab, k in lk])):
        g = res[lab]["models"][k]["gates"]
        g["fdr"] = r
        g["passed"] = bool(g.get("G1") and g.get("G2") and g.get("G3") and g.get("G4") and r)
        res[lab]["models"][k]["status"] = "LIVE SHADOW ELIGIBLE" if g["passed"] else "NOT VALIDATED"
    return res


def run_all(db_path: str, workers: int = 3, progress=None, with_hedge: bool = True) -> dict:
    from concurrent.futures import ProcessPoolExecutor, as_completed
    say = progress or (lambda m: None)
    t0 = time.time()
    res = {"started": time.strftime("%Y-%m-%d %H:%M:%S")}
    res["capability"] = capability2(progress=say)
    jobs = [("1W", "1W"), ("1D", "1D"), ("long", "1M"), ("long", "3M"), ("long", "6M"), ("long", "12M")]
    with ProcessPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_worker, db_path, kind, lab): lab for kind, lab in jobs}
        for fu in as_completed(futs):
            lab = futs[fu]
            res[lab] = fu.result()
            say(f"{lab} finished ({time.time() - t0:.0f}s)")
    if with_hedge:
        from ..hedge import hedgetune as HT
        res["hedge"] = HT.run(db_path, (res.get("1W") or {}).get("_alpha_map"), progress=say)
    finalise(res)
    res["seconds"] = round(time.time() - t0, 1)
    return res


def save(store, res: dict):
    slim = {k: v for k, v in res.items() if k not in ("1W",)}
    W = dict(res.get("1W") or {})
    store.kv_set(RESEARCH_KEY + ":1W:today", {"today": W.pop("today", None), "production_today": W.pop("production_today", None),
                                             "weights": W.pop("weights", None)})
    W.pop("_alpha_map", None)
    slim["1W"] = W
    store.kv_set(RESEARCH_KEY, slim)


LIVE_KEY = "lab:finetune:live"


def build_live(db_path: str, res: dict, progress=None) -> dict:
    """For every 1W fine-tune that passed every gate (G1–G5 + FDR) and has a linear per-node score: its final weights as
    coefficients, today's cross-section means and a score scale matched to production, stored under its OWN key — the
    live-shadow models of the learned engine (lab:learned:live) are never rewritten."""
    say = progress or (lambda m: None)
    W = res.get("1W") or {}
    todo = [k for k, v in (W.get("models") or {}).items() if v.get("status") == "LIVE SHADOW ELIGIBLE" and k in (W.get("finals") or {})]
    skipped = [k for k, v in (W.get("models") or {}).items() if v.get("status") == "LIVE SHADOW ELIGIBLE" and k not in (W.get("finals") or {})]
    for k in skipped:
        say(f"live shadow: {k} passed every gate but has no linear per-node scorer (regime / blend) — recorded as research")
    out = {}
    if not todo:
        from ..data.store import Store
        st = Store(db_path)
        try:
            st.kv_set(LIVE_KEY, out)
        finally:
            st.close()
        return out
    from ..data.store import Store
    from .research import Research
    st = Store(db_path)
    try:
        rows, today, names = L.build_rows(st, Research(st), "1W", say)
        tab = L.Table(rows, names, 5)
        del rows
        lr = L.Learner(tab, "alpha")
        lr.fit_all()
        rms = lr.fits[L.FINAL]["rms"]
        latest = max(t["rec"].date for t in today)
        pool = [t["rec"] for t in today if t["rec"].date == latest] or [t["rec"] for t in today]
        means = [sum(float(r.x[i]) for r in pool) / len(pool) for i in range(tab.P)]
        for k in todo:
            fin = W["finals"][k]
            depth = fin["depth"]
            coefs = {nd: L._coef(w, rms) for nd, w in fin["weights"].items()}
            idx, raws = [], []
            for a, (s0, s1) in tab.slices.items():
                c = coefs.get(L.cut_path(tab.paths[a], depth)) or coefs.get("global")
                sc = L.score_slice(lr.cols, c, s0, s1)
                idx.extend(abs(v) for kk, v in zip(range(s0, s1), sc) if lr.w[kk] > 0)
                raws.extend(abs(tab.raw[kk]) for kk in range(s0, s1) if lr.w[kk] > 0 and tab.raw[kk] is not None)
            idx.sort(); raws.sort()
            scale = (idx[len(idx) // 2] / math.atanh(min(99.0, max(0.5, raws[len(raws) // 2])) / 100.0)) if idx and raws else 1.0
            vid = vid_of("1W", k)
            out[vid] = {"lab": "1W", "variant": k, "label": k, "names": names, "depth": depth, "coefs": {nd: L._rl(v, 8) for nd, v in coefs.items()},
                        "weights": fin["weights"], "means": L._rl(means, 8), "means_date": latest, "scale": scale,
                        "built": time.strftime("%Y-%m-%d %H:%M:%S")}
            say(f"live model {vid}: depth {depth}, scale {scale:.4g}")
        st.kv_set(LIVE_KEY, out)
    finally:
        st.close()
    return out


def register(store, res: dict) -> List[str]:
    """Every fine-tuned 1W model gets its own immutable version id: 'challenger' (live shadow) only when it passed every
    gate, otherwise 'research'. The two existing live-shadow versions are never touched."""
    from .lab import _save_registry, registry
    reg = registry(store)
    today = time.strftime("%Y-%m-%d")
    made = []
    protected = {"alpha-learned-1w-global-exp", "alpha-learned-1w-hierarchy-exp"}
    for lab in ("1W", "1M", "3M", "6M", "12M"):
        for k, v in ((res.get(lab) or {}).get("models") or {}).items():
            if v.get("family") == "baseline" or k == "learned global (D)" or not v.get("pit", True):
                continue
            vid = vid_of(lab, k)
            if vid in protected:
                continue
            old = next((x for x in reg["versions"] if x["id"] == vid), None)
            if old and old.get("status") not in ("challenger", "research", "rejected", None):
                continue
            passed = v.get("status") == "LIVE SHADOW ELIGIBLE"
            reg["versions"] = [x for x in reg["versions"] if x["id"] != vid]
            g = dict(v.get("gates") or {})
            g["G1_discovery"] = bool(g.get("G1"))
            g["G2_confirmation"] = bool(g.get("G2") and g.get("G3") and g.get("G4") and g.get("fdr") and (g.get("G5", True)))
            reg["versions"].append({"id": vid, "kind": "shaffer-alpha", "family": "finetune", "model": k, "horizon": lab,
                                    "introduced": (old or {}).get("introduced") or today, "status": "challenger" if passed else "research",
                                    "live_shadow_from": ((old or {}).get("live_shadow_from") or today) if passed else None,
                                    "formula": v.get("name"), "parent": "alpha-learned-1w-global-exp" if lab == "1W" else None,
                                    "training_cutoff": res.get("started"), "benchmark": "production + learned global (G5)",
                                    "validation": {lab: {"gates": g}}})
            made.append(vid)
    hd = res.get("hedge") or {}
    elig = {f"{lab} {k}": {"objective": c.get("objective"), "lam": c.get("lam"),
                           "multiple": (((hd.get("surface") or {}).get(lab) or {}).get(c.get("node")) or {}).get("shrunk", {}).get(str(c.get("lam")))}
            for lab, cs in (hd.get("cells") or {}).items() for k, c in cs.items() if c.get("status") == "LIVE SHADOW ELIGIBLE"}
    from ..hedge.hedgetune import VID
    reg["versions"] = [x for x in reg["versions"] if x["id"] != VID]
    # 'research' even when cells pass: the hedge live grader (lab.hedge_live_gate) grades variance per group, not utility at
    # a chosen λ — a λ-aware grader must exist before a λ-conditional size can be shadowed honestly
    reg["versions"].append({"id": VID, "kind": "hedge", "family": "finetune", "introduced": today, "status": "research",
                            "parent": "hedge-2", "formula": "λ-conditional hedge-size surface m(objective, λ, risk class, horizon, volatility regime)",
                            "benchmark": "hedge-2 (realised utility at the same λ)", "training_cutoff": res.get("started"),
                            "eligible_cells": elig, "note": "live shadow needs a λ-aware hedge grader (not built)" if elig else None})
    made.append(VID)
    _save_registry(store, reg)
    return made
