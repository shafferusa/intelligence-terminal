"""ML Lab — the residual / meta-learning program: what do the frozen learned models get wrong, and can ML learn it?

Protocol (fixed before any market result): SHAFFER_RESIDUAL_PROTOCOL.md. Report: SHAFFER_RESIDUAL_ML.md and
SHAFFER_META_CURRENT.md. E (the learned hierarchy) is the fixed baseline; nothing here refits, rescores or touches the
live-shadow models D and E, production, the Directional prior or hedge-2.

Everything is built on one `Data` object — the 1W research table, the out-of-sample (walk-forward) scores of E, D and
production, the target u and the point-in-time feature columns — so the synthetic capability worlds and the market run
go through exactly the same code:

    Data ──► residual table (records with an out-of-sample E: 2009 on; y = r = u − β·zE)
         ──► Alpha residual challengers R1, R1s, R2, R3, R4, R5 (score β·zE + γ·r̂), MT, DH, PW
         ──► meta models: E reliability, D-vs-E selector / blend; tail classifiers; pairwise
         ──► evaluation on 2013–2024 (eras 2013–16, 2017–20, 2021–24) and the untouched 2025– period; gates; FDR
"""
from __future__ import annotations

import bisect
import math
import random
import time
from array import array
from itertools import repeat
from operator import add, mul
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from . import finetune as F
from . import learned as L

RESEARCH_KEY = "lab:residual"
NAN = float("nan")
EVAL_FROM, CONFIRM_FROM = "2013-01-01", "2025-01-01"
BETA_FROM, BETA_TO = "2009-01-01", "2013-01-01"
GAMMAS = [0.0, 0.25, 0.5, 1.0]
R1_LAMBDAS = [50.0, 1e3, 1e4, 1e5]
EN_FRACS = [0.003, 0.01, 0.03]
R3_K = [1000, 5000, 20000]
R3_DEPTH = ["class", "sector", "asset"]
R4_ROUNDS = [20, 40]
R5_WEIGHTS = [(1, 0, 0), (0, 1, 0), (0, 0, 1), (1 / 3, 1 / 3, 1 / 3), (0.5, 0.5, 0), (0.5, 0, 0.5), (0, 0.5, 0.5)]
CRISES = [("2008-09-01", "2009-06-30"), ("2020-03-01", "2020-04-30"), ("2022-01-01", "2022-12-31")]
FDR_Q = 0.10
T_MIN = 2.0
ERA_NAMES = ["2009–12", "2013–16", "2017–20", "2021–24", "2025–"]
EVAL_ERAS = [1, 2, 3]            # complete residual eras; 4 = the untouched 2025– period


# ------------------------------------------------------------------ small numerics
def _inv_phi(p: float) -> float:
    """Φ⁻¹ (Acklam's rational approximation, |error| < 1.2e-9)."""
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02, 1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02, 6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00, -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00, 3.754408661907416e+00]
    if p < 0.02425:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    if p > 1 - 0.02425:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    q = p - 0.5
    r = q * q
    return (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1)


def _sig(v: float) -> float:
    return 1.0 / (1.0 + math.exp(-max(-35.0, min(35.0, v))))


def _logit(p: float) -> float:
    p = min(1 - 1e-6, max(1e-6, p))
    return math.log(p / (1 - p))


def week_normal(tab: L.Table, v: Sequence[float], mask: Optional[Sequence[bool]] = None) -> Tuple[array, array]:
    """Within-week normal scores Φ⁻¹((rank − ½)/n) and percentiles (rank − ½)/n − ½ of v (NaN where v is missing)."""
    z, p = array("d", repeat(NAN, tab.n)), array("d", repeat(NAN, tab.n))
    for _, a, b, _ in tab.weeks:
        idx = [tab.perm[k] for k in range(a, b) if v[tab.perm[k]] == v[tab.perm[k]] and (mask is None or mask[tab.perm[k]])]
        m = len(idx)
        if m < 3:
            continue
        rk = L._ranks([v[k] for k in idx])
        for k, r in zip(idx, rk):
            q = (r - 0.5) / m
            z[k], p[k] = _inv_phi(q), q - 0.5
    return z, p


def wric(tab: L.Table, s: Sequence[float], u: Sequence[float], keep: Optional[Callable[[int], bool]] = None) -> Dict[int, float]:
    """Weekly Spearman rank IC of s with u over the records scored in both (≥ 8 names), optionally a subset."""
    out = {}
    for wk, a, b, _ in tab.weeks:
        idx = [tab.perm[k] for k in range(a, b)]
        idx = [k for k in idx if s[k] == s[k] and u[k] == u[k] and (keep is None or keep(k))]
        m = len(idx)
        if m < 8:
            continue
        rs, ru = L._ranks([s[k] for k in idx]), L._ranks([u[k] for k in idx])
        mr = (m + 1) / 2.0
        num = sum((x - mr) * (y - mr) for x, y in zip(rs, ru))
        den = math.sqrt(sum((x - mr) ** 2 for x in rs) * sum((y - mr) ** 2 for y in ru))
        if den > 0:
            out[wk] = num / den
    return out


def _clust(d: Dict[int, float], h: int = 5) -> dict:
    c = L.clustered(d, h)
    return {"mean": c.get("mean"), "t": c.get("t"), "weeks": c.get("weeks")}


def _p_one(t: Optional[float]) -> float:
    if t is None:
        return 1.0
    return 1.0 - 0.5 * (1.0 + math.erf(t / math.sqrt(2.0)))


def bh(ps: List[float], q: float = FDR_Q) -> List[bool]:
    return L.benjamini_hochberg(ps, q) if ps else []


# ------------------------------------------------------------------ the data object
class Data:
    """The 1W table, out-of-sample baseline scores and point-in-time features, aligned by record index."""

    def __init__(self, tab: L.Table, ctx: F.Ctx, zE: array, zD: array, zP: array, pE: array, u: array,
                 feat_names: List[str], feats: List[array], states: Dict[str, List[Optional[str]]], label: str = "1W",
                 today: Optional[List[dict]] = None, extra: Optional[dict] = None):
        self.tab, self.ctx, self.zE, self.zD, self.zP, self.pE, self.u = tab, ctx, zE, zD, zP, pE, u
        self.feat_names, self.feats, self.states, self.label = feat_names, feats, states, label
        self.today = today or []
        self.extra = extra or {}
        self.cls = [tab.paths[a][1] if len(tab.paths[a]) > 1 else "?" for a in tab.asset]


CLASSES = ["class:Equity", "class:Rates", "class:Credit", "class:Commodity", "class:FX", "class:Crypto", "class:Volatility"]
STATE_DIMS = ["volatility", "market", "rates", "risk", "breadth"]
STATE_POS = {"volatility": "high_vol", "market": "bull", "rates": "rising_rates", "risk": "risk-on", "breadth": "wide"}


def context_columns(tab: L.Table, zE, zD, zP, pE, states: Dict[str, List[Optional[str]]], names: List[str]) -> Tuple[List[str], List[array]]:
    """The context features of the protocol (§1), one column per feature, aligned to the table."""
    n = tab.n
    cols: Dict[str, array] = {}
    cols["ctx:zE"], cols["ctx:zD"], cols["ctx:zP"] = zE, zD, zP
    cols["ctx:zD-zE"] = array("d", (d - e for d, e in zip(zD, zE)))
    cols["ctx:|zE|"] = array("d", (abs(e) for e in zE))
    cols["ctx:pE"] = pE
    cols["ctx:pE^2"] = array("d", (p * p for p in pE))
    cls = [tab.paths[a][1] if len(tab.paths[a]) > 1 else "?" for a in tab.asset]
    for c in CLASSES:
        cols[f"ctx:{c}"] = array("d", (1.0 if x == c else 0.0 for x in cls))
    for d in STATE_DIMS:
        st = states.get(d) or [None] * n
        cols[f"ctx:{d}"] = array("d", (0.0 if s is None else (1.0 if s == STATE_POS[d] else -1.0) for s in st))
    # dispersion of trailing 1W returns and signal disagreement, per record (point in time)
    j1w = names.index("ret_1w") if "ret_1w" in names else None
    disp = array("d", repeat(0.0, n))
    if j1w is not None:
        X = tab.X[j1w]
        for _, a, b, _ in tab.weeks:
            idx = [tab.perm[k] for k in range(a, b)]
            if len(idx) >= 3:
                m = sum(X[k] for k in idx) / len(idx)
                sd = math.sqrt(sum((X[k] - m) ** 2 for k in idx) / (len(idx) - 1))
                for k in idx:
                    disp[k] = sd
    cols["ctx:dispersion"] = disp
    from .. import shaffer_score as cfg
    fam_of = [cfg.ALL_FAMILY_OF.get(s_, cfg.FAMILY_OF.get(s_, "?")) for s_ in names]
    fams = sorted(set(fam_of))
    fidx = {f: [i for i in range(len(names)) if fam_of[i] == f] for f in fams}
    dis = array("d", repeat(0.0, n))
    for k in range(n):
        ms = [sum(tab.X[i][k] for i in ix) / len(ix) for ix in fidx.values() if ix]
        m = sum(ms) / len(ms)
        dis[k] = math.sqrt(sum((v - m) ** 2 for v in ms) / max(1, len(ms) - 1))
    cols["ctx:signal_disagreement"] = dis
    hist = array("d", repeat(0.0, n))
    for a, (s0, s1) in tab.slices.items():
        for j, k in enumerate(range(s0, s1)):
            hist[k] = math.log1p(j)
    cols["ctx:log_history"] = hist
    return list(cols), list(cols.values())


def build_market(store, research, progress=None, only: Optional[set] = None) -> Data:
    """The market Data: the 1W research records, E and D exactly as in live shadow (walk-forward), production's score."""
    say = progress or (lambda m: None)
    t0 = time.time()
    rows, today, names = L.build_rows(store, research, "1W", say, only)
    tab = L.Table(rows, names, 5)
    del rows
    ctx = F.Ctx(tab, lambda m: say(f"1W {m}"))
    base = F.fam_baselines(ctx)
    reg = F.Regimes(ctx)
    say(f"baselines D and E reproduced ({time.time() - t0:.0f}s)")
    return _assemble(tab, ctx, base["D"].wf, base["E"].wf, array("d", (v if v is not None else NAN for v in tab.raw)), reg.state,
                     "1W", today, {"D": base["D"], "E": base["E"], "reg": reg})


def _assemble(tab, ctx, sD, sE, sP, states, label, today=None, extra=None) -> Data:
    yr = array("d", ((r.yr if r is not None and r.yr is not None else NAN) for r in tab.rec))
    u, pu = week_normal(tab, yr)
    zE, pE = week_normal(tab, sE)
    zD, pD = week_normal(tab, sD)
    zP, _ = week_normal(tab, sP)
    fn, fc = context_columns(tab, zE, zD, zP, pE, states, tab.names)
    d = Data(tab, ctx, zE, zD, zP, pE, u, list(tab.names) + fn, list(tab.X) + fc, states, label, today,
             {**(extra or {}), "sE": sE, "sD": sD, "sP": sP})
    d.pu, d.pD = pu, pD
    return d


# ------------------------------------------------------------------ the residual table
class Resid:
    """Records with an out-of-sample E (2009 on): target r = u − β·zE, features = the 74 signals + context."""

    def __init__(self, data: Data, progress=None):
        self.data = data
        tab = data.tab
        keep = [k for k in range(tab.n) if tab.date[k] >= BETA_FROM and data.zE[k] == data.zE[k] and data.u[k] == data.u[k]
                and data.zD[k] == data.zD[k]]
        bidx = [k for k in keep if tab.date[k] < BETA_TO]
        sxx = sum(data.zE[k] ** 2 for k in bidx)
        self.beta = (sum(data.zE[k] * data.u[k] for k in bidx) / sxx) if sxx > 0 else 0.0
        rows = []
        for k in keep:
            rows.append({"asset": tab.asset[k], "path": tab.path[k], "date": tab.date[k], "end": tab.end[k], "wk": tab.wk[k],
                         "x": _Row(data.feats, k), "y": data.u[k] - self.beta * data.zE[k], "z": 0.0,
                         "o": 1 if (tab.rec[k] is not None and (tab.rec[k].yr or 0) > 0) else 0, "reg": tab.reg[k],
                         "raw": tab.raw[k], "rec": tab.rec[k]})
        self.tab = L.Table(rows, data.feat_names, tab.h, "value")
        del rows
        _demean_weekly(self.tab)
        pos = {(a, d): k for k, (a, d) in enumerate(zip(tab.asset, tab.date))}
        self.main = [pos[(a, d)] for a, d in zip(self.tab.asset, self.tab.date)]      # residual index → main index
        g = lambda v: array("d", (v[k] for k in self.main))  # noqa: E731
        self.zE, self.zD, self.zP, self.pE, self.u = g(data.zE), g(data.zD), g(data.zP), g(data.pE), g(data.u)
        self.pu, self.pD = g(data.pu), g(data.pD)
        self.yr = array("d", (r.yr for r in self.tab.rec))
        self.key = {(a, d): k for k, (a, d) in enumerate(zip(self.tab.asset, self.tab.date))}
        self.cls = [data.cls[k] for k in self.main]
        self.ctx = F.Ctx(self.tab, progress)
        self.costs = F.record_costs(self.tab)
        self.base_score = array("d", (self.beta * v for v in self.zE))

    def combined(self, rhat: array, gamma: float) -> array:
        return array("d", (((b + gamma * r) if r == r else NAN) for b, r in zip(self.base_score, rhat)))

    def metric(self, s: array) -> Dict[int, float]:
        return wric(self.tab, s, self.u)

    def pick(self, options: Dict[str, array], default: str, metric: Optional[Callable[[array], Dict[int, float]]] = None) -> dict:
        """Nested choice over option score arrays: per outer era, the option with the best mean inner metric over the
        walk-forward weeks that matured before the era — the pre-registered default while fewer than 52 such weeks
        have been scored (the first residual era, 2013–16, always uses the default)."""
        return nested_pick(self.tab, self.ctx.lr, options, default, metric or self.metric)


def nested_pick(tab: L.Table, lr: L.Learner, options: Dict[str, array], default: str, metric: Callable[[array], Dict[int, float]]) -> dict:
    keys = [default] + [k for k in options if k != default]          # ties keep the pre-registered default
    weekly = {k: metric(options[k]) for k in keys}
    week_end = {wk: e for wk, _, _, e in tab.weeks}
    wf = array("d", repeat(NAN, tab.n))
    choices = {}

    def choose(cut):
        inner = {wk for wk, e in week_end.items() if e < cut}
        scored = {wk for wk in inner if wk in weekly[default]}
        if len(scored) < L.MIN_INNER_WEEKS:
            return default

        def sc(k):
            v = [x for wk, x in weekly[k].items() if wk in scored]
            return sum(v) / len(v) if v else -1e9
        return max(keys, key=sc)
    for e in range(len(L.TEST_ERAS)):
        cut = L.TEST_ERAS[e][0]
        k = choose(cut)
        choices[cut] = k
        src = options[k]
        for a, (s0, s1) in tab.slices.items():
            lo, hi = lr._era_range(s0, s1, e)
            if lo < hi:
                wf[lo:hi] = src[lo:hi]
    choices[L.FINAL] = choose(L.FINAL)
    return {"wf": wf, "choices": choices, "final": choices[L.FINAL], "weekly": weekly}


class _Row:
    """A record's feature vector read lazily from the column arrays (no per-record copies of ~100 floats)."""
    __slots__ = ("f", "k")

    def __init__(self, f, k):
        self.f, self.k = f, k

    def __getitem__(self, i):
        return self.f[i][self.k]


def _demean_weekly(tab: L.Table):
    """Week-demean the fitting columns of a 'value' table (the learned engine does this itself only for ranks)."""
    for c in tab.XA:
        for _, a, b, _ in tab.weeks:
            idx = tab.perm[a:b]
            mu = sum(c[k] for k in idx) / len(idx)
            for k in idx:
                c[k] -= mu


# ------------------------------------------------------------------ Alpha residual challengers
def _ridge_fn(R: Resid, lam: float):
    ctx = R.ctx

    def w(cut):
        f = ctx.lr.fits.get(cut)
        return L.ridge(f["ns"]["global"], ctx.rms(cut), [0.0] * ctx.P, lam) if f else None
    return w


def r1(R: Resid) -> dict:
    """R1: the global ridge on the residual table; λ and γ nested."""
    opts, fns = {}, {}
    for lam in R1_LAMBDAS:
        fn = _ridge_fn(R, lam)
        rhat, _ = R.ctx.score_global(fn)
        fns[lam] = (fn, rhat)
        for g in GAMMAS:
            opts[f"{lam:g}|{g}"] = R.combined(rhat, g)
    ch = R.pick(opts, default=f"{R1_LAMBDAS[1]:g}|0.5")
    return {"name": "R1 ridge residual", "wf": ch["wf"], "choices": ch["choices"], "final": ch["final"], "fns": fns,
            "rhat_by_opt": {k: fns[float(k.split("|")[0])][1] for k in opts}}


def _era_of_year(y: int) -> int:
    return 0 if y < 2013 else (1 if y < 2017 else (2 if y < 2021 else (3 if y < 2025 else 4)))


def r1s(R: Resid, lam_choice: Dict[str, str]) -> dict:
    """R1s: R1's correction shrunk by its uncertainty r̂²/(r̂² + se²), se = between-era disagreement of R1 refitted on
    each complete training era separately (no correction before two training eras exist)."""
    ctx, tab, lr = R.ctx, R.tab, R.ctx.lr
    opts = {}
    for lam in R1_LAMBDAS:
        rhat, _ = ctx.score_global(_ridge_fn(R, lam))
        shr = array("d", repeat(NAN, tab.n))
        for e in range(len(L.TEST_ERAS)):
            cut = L.TEST_ERAS[e][0]
            if cut not in lr.fits:
                continue
            rms = ctx.rms(cut)
            by_era: Dict[int, L.Stat] = {}
            for y, st in ctx.gy.items():
                if f"{y}-12-31" < cut and y >= 2009:
                    ea = _era_of_year(y)
                    by_era[ea] = st.copy() if ea not in by_era else by_era[ea].add(st)
            coefs = [L._coef(L.ridge(st, rms, [0.0] * ctx.P, lam), rms) for st in by_era.values() if st.sw > 0]
            for a, (s0, s1) in tab.slices.items():
                lo, hi = lr._era_range(s0, s1, e)
                if lo >= hi:
                    continue
                if len(coefs) < 2:
                    for k in range(lo, hi):
                        shr[k] = 0.0 if rhat[k] == rhat[k] else NAN
                    continue
                ps = [L.score_slice(ctx.cols, c, lo, hi) for c in coefs]
                kk = len(ps)
                for j, k in enumerate(range(lo, hi)):
                    if rhat[k] != rhat[k]:
                        continue
                    vals = [p[j] for p in ps]
                    m = sum(vals) / kk
                    se2 = sum((v - m) ** 2 for v in vals) / (kk * (kk - 1))
                    r = rhat[k]
                    shr[k] = r * (r * r / (r * r + se2)) if (r * r + se2) > 0 else 0.0
        for g in GAMMAS:
            opts[f"{lam:g}|{g}"] = R.combined(shr, g)
    ch = R.pick(opts, default=f"{R1_LAMBDAS[1]:g}|0.5")
    return {"name": "R1s uncertainty-shrunk ridge residual", "wf": ch["wf"], "choices": ch["choices"], "final": ch["final"]}


def r2(R: Resid) -> dict:
    """R2: sparse elastic net residual (coordinate descent on the global statistics)."""
    ctx, lr = R.ctx, R.ctx.lr
    opts = {}
    for frac in EN_FRACS:
        def w_en(cut, frac=frac):
            f = lr.fits.get(cut)
            if not f:
                return None
            g = f["ns"]["global"]
            rms = ctx.rms(cut)
            b = [abs(g.xy[i] / rms[i]) if rms[i] > 0 else 0.0 for i in range(ctx.P)]
            return L.cd_solve(g, rms, L.G_LAMBDA, frac * max(b), start=f["W"][L.K_DEFAULT]["global"])
        rhat, _ = ctx.score_global(w_en)
        for g_ in GAMMAS:
            opts[f"{frac}|{g_}"] = R.combined(rhat, g_)
    ch = R.pick(opts, default=f"{EN_FRACS[1]}|0.5")
    return {"name": "R2 elastic-net residual", "wf": ch["wf"], "choices": ch["choices"], "final": ch["final"]}


def r3(R: Resid) -> dict:
    """R3: hierarchical residual (global → … → asset, partial pooling), K and depth nested."""
    lr = R.ctx.lr
    opts, rh = {}, {}
    for K in R3_K:
        for d in R3_DEPTH:
            key = (K, d)
            if key not in lr.wf:
                continue
            rh[f"{K}|{d}"] = lr.wf[key]
            for g in GAMMAS:
                opts[f"{K}|{d}|{g}"] = R.combined(lr.wf[key], g)
    ch = R.pick(opts, default=f"{R3_K[1]}|{R3_DEPTH[1]}|0.5")
    return {"name": "R3 hierarchical residual", "wf": ch["wf"], "choices": ch["choices"], "final": ch["final"], "rhat": rh}


def gbm_scores(R: Resid, rounds: int, n_feat: int = 20, rate: float = 0.1, sample: int = 12000, nb: int = 16, seed: int = 7) -> Tuple[array, dict]:
    """Residual boosting with depth-2 trees on 16-bin features — the 20 signals with the largest |global ridge weight|
    at the cut plus every context feature, on their raw (not week-demeaned) values so that week-level states can split —
    fitted on outcomes matured before each era."""
    lr, tab = R.ctx.lr, R.tab
    rnd = random.Random(seed)
    out = array("d", repeat(NAN, tab.n))
    splits: Dict[str, int] = {}
    ctx_idx = [i for i, nm in enumerate(tab.names) if nm.startswith("ctx:")]
    cols = tab.X
    for e in range(len(L.TEST_ERAS)):
        cut = lr._era_cut(e)
        if cut not in lr.fits:
            continue
        f = lr.fits[cut]
        g = f["W"][L.K_DEFAULT]["global"]
        sig = [i for i in range(lr.P) if i not in ctx_idx and f["rms"][i] > 0]
        top = sorted(sig, key=lambda i: -abs(g[i]))[:n_feat] + ctx_idx
        train = [k for k in range(tab.n) if tab.end[k] < cut and lr.w[k] > 0]
        if len(train) < 1500:
            continue
        if len(train) > sample:
            train = sorted(rnd.sample(train, sample))
        rr = array("d", (lr.y[k] for k in train))
        ww = array("d", (lr.w[k] for k in train))
        edges, Xb = [], []
        for i in top:
            v = sorted(cols[i][k] for k in train)
            ed = sorted(set(v[int(len(v) * q / nb)] for q in range(1, nb)))
            edges.append(ed)
            Xb.append(array("b", (bisect.bisect_right(ed, cols[i][k]) for k in train)))
        trees = []
        idx = list(range(len(train)))
        min_w = 0.02 * sum(ww)
        for _ in range(rounds):
            tr = L._fit_tree2(Xb, rr, ww, nb, idx, min_w)
            if tr is None:
                break
            trees.append(tr)
            f1, t1, leaves, _g = tr
            for i_ in idx:
                L_ = leaves[0] if Xb[f1][i_] <= t1 else leaves[1]
                rr[i_] -= rate * (L_[2] if L_[0] is None or Xb[L_[0]][i_] <= L_[1] else L_[3])
            for q in {f1} | {lf[0] for lf in leaves if lf[0] is not None}:
                nm = tab.names[top[q]]
                splits[nm] = splits.get(nm, 0) + 1
        for a, (s0, s1) in tab.slices.items():
            lo, hi = lr._era_range(s0, s1, e)
            for k in range(lo, hi):
                bins = [bisect.bisect_right(edges[q], cols[i][k]) for q, i in enumerate(top)]
                v = 0.0
                for f1, t1, leaves, _g in trees:
                    L_ = leaves[0] if bins[f1] <= t1 else leaves[1]
                    v += rate * (L_[2] if L_[0] is None or bins[L_[0]] <= L_[1] else L_[3])
                out[k] = v
    return out, {"top_splits": sorted(splits.items(), key=lambda kv: -kv[1])[:10]}


def r4(R: Resid) -> dict:
    opts, rh, info = {}, {}, {}
    for n in R4_ROUNDS:
        rhat, inf = gbm_scores(R, n)
        rh[str(n)], info[str(n)] = rhat, inf
        for g in GAMMAS:
            opts[f"{n}|{g}"] = R.combined(rhat, g)
    ch = R.pick(opts, default=f"{R4_ROUNDS[0]}|0.5")
    return {"name": "R4 boosted residual", "wf": ch["wf"], "choices": ch["choices"], "final": ch["final"], "rhat": rh, "info": info}


def _rhat_from_choice(R: Resid, model: dict, rhat_of: Callable[[str], array]) -> array:
    """The residual prediction a nested model actually used in each era (its chosen option's r̂, era by era)."""
    tab, lr = R.tab, R.ctx.lr
    out = array("d", repeat(NAN, tab.n))
    for e in range(len(L.TEST_ERAS)):
        k = model["choices"].get(L.TEST_ERAS[e][0])
        if k is None:
            continue
        src = rhat_of(k)
        for a, (s0, s1) in tab.slices.items():
            lo, hi = lr._era_range(s0, s1, e)
            if lo < hi:
                out[lo:hi] = src[lo:hi]
    return out


def r5(R: Resid, m1: dict, m3: dict, m4: dict) -> dict:
    """R5: ensemble of the R1, R3 and R4 corrections (each as its own nested choice used), weights and γ nested."""
    a = _rhat_from_choice(R, m1, lambda k: m1["fns"][float(k.split("|")[0])][1])
    b = _rhat_from_choice(R, m3, lambda k: m3["rhat"]["|".join(k.split("|")[:2])])
    c = _rhat_from_choice(R, m4, lambda k: m4["rhat"][k.split("|")[0]])
    opts = {}
    for wi, (x, y, z) in enumerate(R5_WEIGHTS):
        mix = array("d", (((x * p + y * q + z * r) if (p == p and q == q and r == r) else NAN) for p, q, r in zip(a, b, c)))
        for g in GAMMAS:
            opts[f"{wi}|{g}"] = R.combined(mix, g)
    ch = R.pick(opts, default="3|0.5")
    return {"name": "R5 residual ensemble", "wf": ch["wf"], "choices": ch["choices"], "final": ch["final"]}


# ------------------------------------------------------------------ evaluation and the Alpha gates
def _era_week(R: Resid) -> Dict[int, int]:
    return {wk: R.tab.era[R.tab.perm[a]] for wk, a, b, _ in R.tab.weeks}


def _in_crisis(d: str) -> bool:
    return any(a <= d <= b for a, b in CRISES)


def evaluate_alpha(R: Resid, s: array, name: str, base: Optional[array] = None) -> dict:
    """A1–A7 for score s against E (β·zE, i.e. E's own ranking) on identical records."""
    tab = R.tab
    base = base if base is not None else R.base_score
    base = array("d", ((b if s[k] == s[k] else NAN) for k, b in enumerate(base)))    # identical records
    ew = _era_week(R)
    wdate = {wk: tab.date[tab.perm[a]] for wk, a, b, _ in tab.weeks}
    sc, sb = R.metric(s), R.metric(base)
    d = {w: sc[w] - sb[w] for w in sc if w in sb}
    main = {w: v for w, v in d.items() if ew.get(w) in EVAL_ERAS}
    conf = {w: v for w, v in d.items() if ew.get(w) == 4}
    c = _clust(main)
    eras = {e: (sum(v for w, v in d.items() if ew.get(w) == e) / max(1, sum(1 for w in d if ew.get(w) == e))) if any(ew.get(w) == e for w in d) else None
            for e in EVAL_ERAS + [4]}
    ric_c = _clust({w: v for w, v in sc.items() if ew.get(w) in EVAL_ERAS})
    ric_b = _clust({w: v for w, v in sb.items() if ew.get(w) in EVAL_ERAS})
    # A4: net 10% tails on the same weeks
    def masked(x):
        return array("d", ((v if (tab.era[k] in EVAL_ERAS and s[k] == s[k] and base[k] == base[k]) else NAN) for k, v in enumerate(x)))
    ls_c = F.ls_stats(R.ctx, masked(s), 0.1, R.costs)
    ls_b = F.ls_stats(R.ctx, masked(base), 0.1, R.costs)
    # A5: leave one class out; outside crises
    loco = {}
    for cl in sorted(set(R.cls)):
        keep = lambda k, cl=cl: R.cls[k] != cl and tab.era[k] in EVAL_ERAS  # noqa: E731
        a_, b_ = wric(tab, s, R.u, keep), wric(tab, base, R.u, keep)
        dd = [a_[w] - b_[w] for w in a_ if w in b_]
        loco[cl] = (sum(dd) / len(dd)) if dd else None
    nc = [v for w, v in main.items() if not _in_crisis(wdate[w])]
    no_crisis = (sum(nc) / len(nc)) if nc else None
    # A6: conviction calibration (rank correlation of the 10 decile bucket means)
    cal_c, cal_b = _conviction_rho(R, s), _conviction_rho(R, base)
    full = [eras[e] for e in EVAL_ERAS if eras[e] is not None]
    g = {"A1": bool((c["mean"] or 0) > 0), "t": c["t"], "A2_t": bool((c["t"] or 0) >= T_MIN),
         "A3": bool(sum(1 for x in full if x > 0) >= 2 and all(x >= -0.005 for x in full) and len(full) == 3),
         "A4": bool(ls_c.get("net") is not None and ls_b.get("net") is not None and ls_c["net"] >= ls_b["net"]),
         "A5": bool(all(v is not None and v > 0 for v in loco.values()) and (no_crisis or 0) > 0),
         "A6": bool(cal_c is not None and cal_b is not None and cal_c >= cal_b - 0.10),
         "A7": bool(conf and sum(conf.values()) / len(conf) >= 0)}
    return {"name": name, "delta": c, "rank_ic": ric_c.get("mean"), "rank_ic_E": ric_b.get("mean"), "eras": {ERA_NAMES[e]: eras[e] for e in eras},
            "confirm": (sum(conf.values()) / len(conf)) if conf else None, "ls10": {k: ls_c.get(k) for k in ("net", "gross", "turnover", "t")},
            "ls10_E": {k: ls_b.get(k) for k in ("net", "gross", "turnover", "t")}, "loco": loco, "no_crisis": no_crisis,
            "calibration_rho": cal_c, "calibration_rho_E": cal_b, "gates": g, "p": _p_one(c["t"])}


def _conviction_rho(R: Resid, s: array) -> Optional[float]:
    _, p = week_normal(R.tab, array("d", ((v if R.tab.era[k] in EVAL_ERAS else NAN) for k, v in enumerate(s))))
    sums, cnt = [0.0] * 10, [0] * 10
    for k in range(R.tab.n):
        if p[k] == p[k] and R.u[k] == R.u[k]:
            b = min(9, int((p[k] + 0.5) * 10))
            sums[b] += R.u[k]; cnt[b] += 1
    if min(cnt) < 30:
        return None
    means = [s_ / c_ for s_, c_ in zip(sums, cnt)]
    rk = L._ranks(means)
    n = 10
    return 1 - 6 * sum((i + 1 - r) ** 2 for i, r in enumerate(rk)) / (n * (n * n - 1))


def finalize_alpha(results: Dict[str, dict]) -> Dict[str, dict]:
    keys = [k for k, v in results.items() if v.get("gates")]
    for k, r in zip(keys, bh([results[k]["p"] for k in keys])):
        g = results[k]["gates"]
        g["A2"] = bool(g["A2_t"] and r)
        g["fdr"] = r
        results[k]["passed"] = all(g[x] for x in ("A1", "A2", "A3", "A4", "A5", "A6", "A7"))
        results[k]["status"] = "ELIGIBLE (passed A1–A7)" if results[k]["passed"] else "NOT VALIDATED"
    return results


# ------------------------------------------------------------------ fast ridge logistic on column arrays
LOGIT_LAMBDA = 10.0             # fixed (not tuned): a weak ridge on standardised inputs, only for numerical stability
MAX_TRAIN = 60000               # meta / tail fits use at most this many training records (seeded sample)


def logit_fit(cols: List[array], y: array, lam: float = LOGIT_LAMBDA, pen: Optional[List[float]] = None,
              offset: Optional[array] = None, iters: int = 30, w: Optional[array] = None) -> List[float]:
    """argmin Σ w·logloss(y, σ(offset + Σ b·col)) + λ/2 Σ pen·b² by Newton steps; C-speed column products."""
    n, P = len(y), len(cols)
    pen = pen if pen is not None else [1.0] * P
    b = [0.0] * P
    for _ in range(iters):
        s_ = array("d", offset) if offset is not None else array("d", repeat(0.0, n))
        for i in range(P):
            if b[i]:
                s_ = array("d", map(add, s_, map(mul, cols[i], repeat(b[i]))))
        p = array("d", map(_sig, s_))
        g = array("d", (q - t for q, t in zip(p, y)))
        v = array("d", (q * (1.0 - q) for q in p))
        if w is not None:
            g = array("d", map(mul, g, w))
            v = array("d", map(mul, v, w))
        grad = [sum(map(mul, cols[i], g)) + lam * pen[i] * b[i] for i in range(P)]
        cv = [array("d", map(mul, cols[i], v)) for i in range(P)]
        H = [[0.0] * P for _ in range(P)]
        for i in range(P):
            for j in range(i, P):
                H[i][j] = H[j][i] = sum(map(mul, cv[i], cols[j]))
            H[i][i] += lam * pen[i] + 1e-9
        step = L._chol_solve(H, grad)
        b = [bi - si for bi, si in zip(b, step)]
        if max(abs(x) for x in step) < 1e-7:
            break
    return b


class Design:
    """Standardised input columns for a set of records (means / sds from the training records only)."""

    def __init__(self, cols: List[array], train: List[int], names: List[str], intercept: bool = True):
        self.names, self.intercept = names, intercept
        self.m, self.sd = [], []
        for c in cols:
            xs = [c[k] for k in train]
            m = sum(xs) / len(xs) if xs else 0.0
            sd = math.sqrt(sum((x - m) ** 2 for x in xs) / max(1, len(xs) - 1)) if xs else 0.0
            self.m.append(m)
            self.sd.append(sd if sd > 1e-12 else 0.0)
        self.cols = cols

    def matrix(self, idx: List[int]) -> List[array]:
        out = [array("d", repeat(1.0, len(idx)))] if self.intercept else []
        for c, m, sd in zip(self.cols, self.m, self.sd):
            out.append(array("d", (((c[k] - m) / sd) if sd else 0.0 for k in idx)))
        return out

    def pen(self) -> List[float]:
        return ([0.0] if self.intercept else []) + [1.0] * len(self.cols)

    def lin(self, b: List[float], k: int) -> float:
        v = b[0] if self.intercept else 0.0
        o = 1 if self.intercept else 0
        for i, (c, m, sd) in enumerate(zip(self.cols, self.m, self.sd)):
            if sd:
                v += b[o + i] * (c[k] - m) / sd
        return v

    def lin_vals(self, b: List[float], vals: Sequence[float]) -> float:
        """The linear predictor for one out-of-table input vector (aligned with the design's columns)."""
        v = b[0] if self.intercept else 0.0
        o = 1 if self.intercept else 0
        for i, (x, m, sd) in enumerate(zip(vals, self.m, self.sd)):
            if sd and x == x:
                v += b[o + i] * (x - m) / sd
        return v

    def contributions(self, b: List[float], vals: Sequence[float]) -> List[Tuple[str, float]]:
        o = 1 if self.intercept else 0
        out = [(nm, b[o + i] * (x - m) / sd) for i, (nm, x, m, sd) in enumerate(zip(self.names, vals, self.m, self.sd)) if sd and x == x]
        return sorted(out, key=lambda kv: -abs(kv[1]))

    def coef_table(self, b: List[float]) -> List[Tuple[str, float]]:
        o = 1 if self.intercept else 0
        return sorted(((nm, b[o + i]) for i, nm in enumerate(self.names)), key=lambda kv: -abs(kv[1]))


def era_cuts() -> List[Tuple[int, str]]:
    return [(e, L.TEST_ERAS[e][0]) for e in range(1, len(L.TEST_ERAS))]


def era_logistic(R: "Resid", cols: List[array], names: List[str], label: array, lam: float = LOGIT_LAMBDA,
                 offset: Optional[array] = None, seed: int = 5, intercept: bool = True, final: bool = True) -> Tuple[array, Dict[str, dict]]:
    """Walk-forward ridge logistic: each era e ≥ 1 scored (linear predictor, NaN elsewhere) by the fit on records whose
    outcomes matured before it; plus the final fit on every matured record (today's model)."""
    tab = R.tab
    rnd = random.Random(seed)
    lin = array("d", repeat(NAN, tab.n))
    fits: Dict[str, dict] = {}
    ok = [k for k in range(tab.n) if label[k] == label[k] and all(c[k] == c[k] for c in cols)]
    for e, cut in era_cuts() + ([(None, L.FINAL)] if final else []):
        train = [k for k in ok if tab.end[k] < cut]
        if len(train) < 1000 or len({label[k] for k in train}) < 2:
            continue
        if len(train) > MAX_TRAIN:
            train = sorted(rnd.sample(train, MAX_TRAIN))
        dz = Design(cols, train, names, intercept)
        X = dz.matrix(train)
        yv = array("d", (label[k] for k in train))
        off = array("d", (offset[k] for k in train)) if offset is not None else None
        b = logit_fit(X, yv, lam, dz.pen(), off)
        fits[cut] = {"b": b, "design": dz, "n": len(train)}
        if e is None:
            continue
        for k in range(tab.n):
            if tab.era[k] == e and all(c[k] == c[k] for c in cols):
                lin[k] = dz.lin(b, k) + (offset[k] if offset is not None else 0.0)
    return lin, fits


def ctx_cols(R: "Resid", exclude: Sequence[str] = ()) -> Tuple[List[str], List[array]]:
    names = [nm for nm in R.tab.names if nm.startswith("ctx:") and nm not in exclude]
    return names, [R.tab.X[R.tab.names.index(nm)] for nm in names]


def weekly_auc(tab: L.Table, s: array, lab: array, keep: Optional[Callable[[int], bool]] = None) -> Dict[int, float]:
    """Per week: AUC − ½ of score s for the binary label (weeks with ≥ 3 of each class)."""
    out = {}
    for wk, a, b, _ in tab.weeks:
        idx = [tab.perm[k] for k in range(a, b)]
        idx = [k for k in idx if s[k] == s[k] and lab[k] == lab[k] and (keep is None or keep(k))]
        pos = sum(1 for k in idx if lab[k] > 0.5)
        neg = len(idx) - pos
        if pos < 3 or neg < 3:
            continue
        rk = L._ranks([s[k] for k in idx])
        rs = sum(r for r, k in zip(rk, idx) if lab[k] > 0.5)
        out[wk] = (rs - pos * (pos + 1) / 2.0) / (pos * neg) - 0.5
    return out


def _eras_mean(R: "Resid", d: Dict[int, float]) -> Dict[int, Optional[float]]:
    ew = _era_week(R)
    out = {}
    for e in EVAL_ERAS + [4]:
        v = [x for w, x in d.items() if ew.get(w) == e]
        out[e] = (sum(v) / len(v)) if v else None
    return out


def _main_only(R: "Resid", d: Dict[int, float]) -> Dict[int, float]:
    ew = _era_week(R)
    return {w: v for w, v in d.items() if ew.get(w) in EVAL_ERAS}


def _two_of_three(eras: Dict[int, Optional[float]], floor: float = 0.0) -> bool:
    full = [eras[e] for e in EVAL_ERAS if eras.get(e) is not None]
    return len(full) == 3 and sum(1 for x in full if x > 0) >= 2 and all(x >= floor for x in full)


# ------------------------------------------------------------------ E reliability (REL)
def reliability(R: "Resid") -> dict:
    """P(E ranks this asset on the correct side of the week's median) from the context features, walk-forward."""
    tab = R.tab
    lab = array("d", (((1.0 if (pe > 0) == (pu > 0) else 0.0) if (pe == pe and pu == pu and pe != 0 and pu != 0) else NAN)
                      for pe, pu in zip(R.pE, R.pu)))
    names, cols = ctx_cols(R)
    lin, fits = era_logistic(R, cols, names, lab)
    rel = array("d", ((_sig(v) if v == v else NAN) for v in lin))
    auc = weekly_auc(tab, rel, lab)
    c = _clust(_main_only(R, auc))
    eras = _eras_mean(R, auc)
    # E's rank IC inside reliability terciles (terciles formed within each week)
    terc = array("d", repeat(NAN, tab.n))
    for wk, a, b, _ in tab.weeks:
        idx = [tab.perm[k] for k in range(a, b) if rel[tab.perm[k]] == rel[tab.perm[k]]]
        if len(idx) < 9:
            continue
        rk = L._ranks([rel[k] for k in idx])
        for k, r in zip(idx, rk):
            terc[k] = min(2, int(3 * (r - 0.5) / len(idx)))
    ic_t, era_tb = [], {}
    tb: Dict[int, float] = {}
    per = []
    for t in range(3):
        d = wric(tab, R.zE, R.u, keep=lambda k, t=t: terc[k] == t)
        per.append(d)
        m = _main_only(R, d)
        ic_t.append((sum(m.values()) / len(m)) if m else None)
    for w in per[2]:
        if w in per[0]:
            tb[w] = per[2][w] - per[0][w]
    era_tb = _eras_mean(R, tb)
    mono = all(x is not None for x in ic_t) and ic_t[0] < ic_t[1] < ic_t[2]
    by_class = {}
    for cl in sorted(set(R.cls)):
        v = [rel[k] for k in range(tab.n) if R.cls[k] == cl and rel[k] == rel[k] and tab.era[k] in EVAL_ERAS]
        by_class[cl] = (sum(v) / len(v)) if v else None
    g = {"auc": bool((c["mean"] or 0) > 0), "t": c["t"], "t_ok": bool((c["t"] or 0) >= T_MIN), "monotone": bool(mono),
         "eras": bool(_two_of_three(era_tb, -1e9))}
    # descriptive (not a gate): does REL know more than E's own conviction? A conviction-only model (|zE|, pE, pE²)
    # already predicts E's hits when E has any skill — even for an optimal E (capability world E).
    cn = ["ctx:|zE|", "ctx:pE", "ctx:pE^2"]
    lin0, _ = era_logistic(R, [R.tab.X[R.tab.names.index(x)] for x in cn], cn, lab, final=False)
    auc0 = weekly_auc(tab, lin0, lab)
    beyond = _clust(_main_only(R, {w: auc[w] - auc0[w] for w in auc if w in auc0}))
    fin = fits.get(L.FINAL)
    return {"name": "REL E reliability", "rel": rel, "label": lab, "auc_minus_half": c, "auc_eras": {ERA_NAMES[e]: v for e, v in eras.items()},
            "ic_terciles": ic_t, "top_minus_bottom_eras": {ERA_NAMES[e]: v for e, v in era_tb.items()}, "by_class": by_class,
            "gates": g, "p": _p_one(c["t"]), "fits": fits, "beyond_conviction": beyond,
            "beyond_conviction_eras": {ERA_NAMES[e]: v for e, v in _eras_mean(R, {w: auc[w] - auc0[w] for w in auc if w in auc0}).items()},
            "coefficients": fin["design"].coef_table(fin["b"])[:12] if fin else []}


# ------------------------------------------------------------------ D versus E (DE-A choose, DE-B blend)
def d_vs_e(R: "Resid", rel: Optional[array] = None) -> dict:
    tab = R.tab
    lab = array("d", (((1.0 if abs(pe - pu) < abs(pd - pu) else 0.0) if (pe == pe and pd == pd and pu == pu and abs(pe - pu) != abs(pd - pu)) else NAN)
                      for pe, pd, pu in zip(R.pE, R.pD, R.pu)))           # ties (same rank under D and E) carry no label
    names, cols = ctx_cols(R)
    lin, fits = era_logistic(R, cols, names, lab)
    P = array("d", ((_sig(v) if v == v else NAN) for v in lin))
    sA = array("d", (((ze if p >= 0.5 else zd) if p == p else NAN) for p, ze, zd in zip(P, R.zE, R.zD)))
    sB = array("d", (((p * ze + (1 - p) * zd) if p == p else NAN) for p, ze, zd in zip(P, R.zE, R.zD)))
    E_ = array("d", ((ze if p == p else NAN) for p, ze in zip(P, R.zE)))
    D_ = array("d", ((zd if p == p else NAN) for p, zd in zip(P, R.zD)))
    ch_E = F.rank_churn(R.ctx, E_)
    out = {"P": P, "label": lab, "fits": {k: {"n": v["n"]} for k, v in fits.items()}}
    wE, wD = R.metric(E_), R.metric(D_)
    out["always_E"] = _clust(_main_only(R, wE))
    out["always_D"] = _clust(_main_only(R, wD))
    out["always_D_minus_E"] = _clust(_main_only(R, {w: wD[w] - wE[w] for w in wD if w in wE}))
    out["always_D_minus_E_eras"] = {ERA_NAMES[e]: v for e, v in _eras_mean(R, {w: wD[w] - wE[w] for w in wD if w in wE}).items()}
    for key, s_ in (("DE-A", sA), ("DE-B", sB)):
        w_ = R.metric(s_)
        d = {w: w_[w] - wE[w] for w in w_ if w in wE}
        c = _clust(_main_only(R, d))
        eras = _eras_mean(R, d)
        ch = F.rank_churn(R.ctx, s_)
        g = {"beats_E": bool((c["mean"] or 0) > 0), "t": c["t"], "t_ok": bool((c["t"] or 0) >= T_MIN), "eras": _two_of_three(eras, -1e9),
             "churn": bool(ch is not None and ch_E is not None and ch >= ch_E - 0.05)}
        out[key] = {"name": key, "score": s_, "delta_vs_E": c, "eras": {ERA_NAMES[e]: v for e, v in eras.items()}, "churn": ch, "churn_E": ch_E,
                    "gates": g, "p": _p_one(c["t"]), "confirm": eras.get(4)}
    # what the selector learned: P(E closer) by state
    by = {}
    for d_ in ("volatility", "market", "risk"):
        j = R.tab.names.index(f"ctx:{d_}") if f"ctx:{d_}" in R.tab.names else None
        if j is None:
            continue
        for sgn, nm in ((1.0, STATE_POS[d_]), (-1.0, "other")):
            v = [P[k] for k in range(tab.n) if P[k] == P[k] and R.tab.X[j][k] == sgn and tab.era[k] in EVAL_ERAS]
            by[f"{d_}={nm}"] = (sum(v) / len(v)) if v else None
    out["P_by_state"] = by
    fin = fits.get(L.FINAL)
    out["coefficients"] = fin["design"].coef_table(fin["b"])[:12] if fin else []
    out["final"] = fin
    return out


def finalize_family(items: Dict[str, dict], gate_keys) -> Dict[str, dict]:
    """BH FDR across one pre-registered family; `gate_keys` = the required gates (or, per member, a dict of them)."""
    keys = [k for k, v in items.items() if v.get("gates")]
    for k, r in zip(keys, bh([items[k]["p"] for k in keys])):
        g = items[k]["gates"]
        g["fdr"] = bool(r)
        req = gate_keys[k] if isinstance(gate_keys, dict) else gate_keys
        items[k]["passed"] = all(g.get(x) for x in req) and bool(r)
        items[k]["status"] = "PASSED (eligible for a live-shadow proposal)" if items[k]["passed"] else "NOT VALIDATED"
    return items


# ------------------------------------------------------------------ tail classifiers (T1 benchmark; T2–T4)
def _tree_boost_logit(R: "Resid", base_lin: array, lab: array, rounds: int = 20, rate: float = 0.1, n_feat: int = 20,
                      nb: int = 16, sample: int = 12000, seed: int = 9, splits: Optional[Dict[str, int]] = None,
                      final: Optional[dict] = None) -> array:
    """T4: Newton boosting of depth-2 trees on the logistic residual of a base linear predictor, walk-forward; with
    `final`, also the model fitted on every matured record (today's), stored into it after the walk-forward eras (so
    the walk-forward draws are unchanged)."""
    tab, lr = R.tab, R.ctx.lr
    rnd = random.Random(seed)
    out = array("d", repeat(NAN, tab.n))
    ctx_idx = [i for i, nm in enumerate(tab.names) if nm.startswith("ctx:")]
    cols = tab.X
    base_in = _tail_base_insample(R, lab, final is not None)
    for e, cut in era_cuts() + ([(None, L.FINAL)] if final is not None else []):
        if cut not in lr.fits:
            continue
        g_ = lr.fits[cut]["W"][L.K_DEFAULT]["global"]
        sig = [i for i in range(lr.P) if i not in ctx_idx and lr.fits[cut]["rms"][i] > 0]
        top = sorted(sig, key=lambda i: -abs(g_[i]))[:n_feat] + ctx_idx
        train = [k for k in range(tab.n) if tab.end[k] < cut and lab[k] == lab[k] and base_in[cut][k] == base_in[cut][k]]
        if len(train) < 1500:
            continue
        if len(train) > sample:
            train = sorted(rnd.sample(train, sample))
        F_ = array("d", (base_in[cut][k] for k in train))
        yv = [lab[k] for k in train]
        edges, Xb = [], []
        for i in top:
            v = sorted(cols[i][k] for k in train)
            ed = sorted(set(v[int(len(v) * q / nb)] for q in range(1, nb)))
            edges.append(ed)
            Xb.append(array("b", (bisect.bisect_right(ed, cols[i][k]) for k in train)))
        trees = []
        idx = list(range(len(train)))
        for _ in range(rounds):
            p = [_sig(f) for f in F_]
            hess = array("d", (max(1e-6, q * (1 - q)) for q in p))
            r = array("d", ((t - q) / h for t, q, h in zip(yv, p, hess)))
            tr = L._fit_tree2(Xb, r, hess, nb, idx, 0.02 * sum(hess))
            if tr is None:
                break
            trees.append(tr)
            f1, t1, leaves, _g = tr
            if splits is not None:
                for q in {f1} | {lf[0] for lf in leaves if lf[0] is not None}:
                    splits[tab.names[top[q]]] = splits.get(tab.names[top[q]], 0) + 1
            for i_ in idx:
                L_ = leaves[0] if Xb[f1][i_] <= t1 else leaves[1]
                F_[i_] += rate * (L_[2] if L_[0] is None or Xb[L_[0]][i_] <= L_[1] else L_[3])
        if e is None:
            final.update({"top": top, "edges": edges, "trees": trees, "rate": rate})
            continue
        for k in range(tab.n):
            if tab.era[k] != e or base_lin[k] != base_lin[k]:
                continue
            bins = [bisect.bisect_right(edges[q], cols[i][k]) for q, i in enumerate(top)]
            v = base_lin[k]
            for f1, t1, leaves, _g in trees:
                L_ = leaves[0] if bins[f1] <= t1 else leaves[1]
                v += rate * (L_[2] if L_[0] is None or bins[L_[0]] <= L_[1] else L_[3])
            out[k] = v
    return out


def _tail_base_insample(R: "Resid", lab: array, final: bool = False) -> Dict[str, array]:
    """T1's in-sample linear predictor on each cut's training records (the starting point T4 boosts from)."""
    tab = R.tab
    pe = R.pE
    cols = [pe, array("d", (x * x for x in pe)), array("d", (x ** 3 for x in pe))]
    out = {}
    ok = [k for k in range(tab.n) if lab[k] == lab[k] and pe[k] == pe[k]]
    for e, cut in era_cuts() + ([(None, L.FINAL)] if final else []):
        train = [k for k in ok if tab.end[k] < cut]
        arr = array("d", repeat(NAN, tab.n))
        if len(train) >= 1000:
            dz = Design(cols, train, ["pE", "pE^2", "pE^3"])
            b = logit_fit(dz.matrix(train), array("d", (lab[k] for k in train)), LOGIT_LAMBDA, dz.pen())
            for k in train:
                arr[k] = dz.lin(b, k)
        out[cut] = arr
    return out


def tails(R: "Resid") -> dict:
    tab = R.tab
    pe = R.pE
    cub = [pe, array("d", (x * x for x in pe)), array("d", (x ** 3 for x in pe))]
    sig_idx = [i for i, nm in enumerate(tab.names)]
    feat = [tab.X[i] for i in sig_idx]
    fnames = [tab.names[i] for i in sig_idx]
    cls_cols, cls_names = [], []
    for c in sorted(set(R.cls)):
        cls_cols.append(array("d", ((p if R.cls[k] == c else 0.0) for k, p in enumerate(pe))))
        cls_names.append(f"{c}×pE")
    out = {}
    for side, cond in (("top", lambda p: p >= 0.4), ("bottom", lambda p: p < -0.4)):
        lab = array("d", (((1.0 if cond(p) else 0.0) if p == p else NAN) for p in R.pu))
        l1, f1 = era_logistic(R, cub, ["pE", "pE^2", "pE^3"], lab)
        l2, f2 = era_logistic(R, cub + feat, ["pE", "pE^2", "pE^3"] + fnames, lab)
        l3, f3 = era_logistic(R, cub + feat + cls_cols, ["pE", "pE^2", "pE^3"] + fnames + cls_names, lab)
        t4_splits: Dict[str, int] = {}
        t4_final: dict = {}
        l4 = _tree_boost_logit(R, l1, lab, splits=t4_splits, final=t4_final)
        p1 = array("d", ((_sig(v) if v == v else NAN) for v in l1))
        base = {"name": f"T1 {side}", "prob": p1, "final": f1.get(L.FINAL)}
        res = {"T1": base}
        for key, ln in (("T2", l2), ("T3", l3), ("T4", l4)):
            p = array("d", ((_sig(v) if v == v else NAN) for v in ln))
            gain: Dict[int, float] = {}
            ll_m = ll_b = 0.0
            nn = 0
            for wk, a, b, _ in tab.weeks:
                idx = [tab.perm[j] for j in range(a, b)]
                idx = [k for k in idx if p[k] == p[k] and p1[k] == p1[k] and lab[k] == lab[k]]
                if len(idx) < 8:
                    continue
                gain[wk] = sum((p1[k] - lab[k]) ** 2 - (p[k] - lab[k]) ** 2 for k in idx) / len(idx)
                if tab.era[idx[0]] in EVAL_ERAS:
                    for k in idx:
                        ll_m -= lab[k] * math.log(max(1e-9, p[k])) + (1 - lab[k]) * math.log(max(1e-9, 1 - p[k]))
                        ll_b -= lab[k] * math.log(max(1e-9, p1[k])) + (1 - lab[k]) * math.log(max(1e-9, 1 - p1[k]))
                        nn += 1
            c = _clust(_main_only(R, gain))
            eras = _eras_mean(R, gain)
            g = {"brier_gain": bool((c["mean"] or 0) > 0), "t": c["t"], "t_ok": bool((c["t"] or 0) >= T_MIN),
                 "eras": _two_of_three(eras, -1e9), "logloss": bool(nn and ll_m <= ll_b)}
            res[key] = {"name": f"{key} {side} decile", "prob": p, "brier_gain": c, "eras": {ERA_NAMES[e]: v for e, v in eras.items()},
                        "logloss": (ll_m / nn) if nn else None, "logloss_T1": (ll_b / nn) if nn else None, "gates": g, "p": _p_one(c["t"]),
                        "confirm": eras.get(4)}
            if key == "T2" and f2.get(L.FINAL):
                res[key]["coefficients"] = f2[L.FINAL]["design"].coef_table(f2[L.FINAL]["b"])[:10]
            if key == "T4":
                res[key]["top_splits"] = sorted(t4_splits.items(), key=lambda kv: -kv[1])[:8]
                res[key]["final"] = t4_final or None
            if key == "T3" and f3.get(L.FINAL):
                res[key]["final"] = f3[L.FINAL]
            if key == "T2" and f2.get(L.FINAL):
                res[key]["final"] = f2[L.FINAL]
        # calibration of T1 (the benchmark tail probability) by E-percentile band
        res["T1"]["base_rate"] = sum(lab[k] for k in range(tab.n) if lab[k] == lab[k]) / max(1, sum(1 for k in range(tab.n) if lab[k] == lab[k]))
        out[side] = res
    return out


# ------------------------------------------------------------------ pairwise
PW_PAIRS, PW_OPP, PW_SEED = 400, 40, 13


def _family_means(R: "Resid") -> Tuple[List[str], List[array]]:
    from .. import shaffer_score as cfg
    tab = R.tab
    groups: Dict[str, List[int]] = {}
    for i, nm in enumerate(tab.names):
        if nm.startswith("ctx:"):
            continue
        f = cfg.ALL_FAMILY_OF.get(nm, cfg.FAMILY_OF.get(nm))
        if f:
            groups.setdefault(f, []).append(i)
    names, cols = [], []
    for f, ix in sorted(groups.items()):
        names.append(f"fam:{f}")
        cols.append(array("d", (sum(tab.X[i][k] for i in ix) / len(ix) for k in range(tab.n))))
    return names, cols


def pairwise(R: "Resid", rel: Optional[array] = None) -> dict:
    tab = R.tab
    rnd = random.Random(PW_SEED)
    fn, fc = _family_means(R)
    base_names, base_cols = ["zE", "zD"] + fn, [R.zE, R.zD] + fc
    if "vol_20" in tab.names:
        base_names.append("vol_20"); base_cols.append(tab.X[tab.names.index("vol_20")])
    if rel is not None:
        base_names.append("REL"); base_cols.append(array("d", ((_logit(v) if v == v else 0.0) for v in rel)))
    same = [None]
    pairs: Dict[int, List[Tuple[int, int]]] = {}
    for wk, a, b, _ in tab.weeks:
        idx = [tab.perm[k] for k in range(a, b) if R.u[tab.perm[k]] == R.u[tab.perm[k]] and R.zE[tab.perm[k]] == R.zE[tab.perm[k]]]
        if len(idx) < 10:
            continue
        ps = []
        for _ in range(PW_PAIRS):
            i, j = rnd.sample(idx, 2)
            ps.append((i, j))
        pairs[wk] = ps
    wk_era = _era_week(R)
    wk_end = {wk: e for wk, _, _, e in tab.weeks}
    names = base_names + ["same_class×zE"]

    def feats(i, j):
        return [c[i] - c[j] for c in base_cols] + [(R.zE[i] - R.zE[j]) if R.cls[i] == R.cls[j] else 0.0]
    fits = {}
    acc: Dict[int, float] = {}
    acc_E: Dict[int, float] = {}
    for e, cut in era_cuts() + [(None, L.FINAL)]:
        tr = [(i, j) for wk, ps in pairs.items() if wk_end[wk] < cut for (i, j) in ps]
        if len(tr) < 2000:
            continue
        if len(tr) > MAX_TRAIN:
            tr = random.Random(PW_SEED + 1).sample(tr, MAX_TRAIN)
        X = [array("d") for _ in names]
        yv = array("d")
        for i, j in tr:
            for c, v in zip(X, feats(i, j)):
                c.append(v)
            yv.append(1.0 if R.u[i] > R.u[j] else 0.0)
        sd = [math.sqrt(sum(v * v for v in c) / len(c)) or 1.0 for c in X]
        Xs = [array("d", (v / s_ for v in c)) for c, s_ in zip(X, sd)]
        b = logit_fit(Xs, yv, LOGIT_LAMBDA)
        coef = [bi / s_ for bi, s_ in zip(b, sd)]
        fits[cut] = coef
        if e is None:
            continue
        for wk, ps in pairs.items():
            if wk_era.get(wk) != e:
                continue
            hit = hitE = 0
            for i, j in ps:
                v = sum(c * f for c, f in zip(coef, feats(i, j)))
                y = R.u[i] > R.u[j]
                hit += (v > 0) == y
                hitE += (R.zE[i] > R.zE[j]) == y
            acc[wk], acc_E[wk] = hit / len(ps), hitE / len(ps)
    d = {w: acc[w] - acc_E[w] for w in acc}
    c = _clust(_main_only(R, d))
    eras = _eras_mean(R, d)
    g = {"accuracy_gain": bool((c["mean"] or 0) > 0), "t": c["t"], "t_ok": bool((c["t"] or 0) >= T_MIN), "eras": _two_of_three(eras, -1e9)}
    # per-asset score: mean win probability against PW_OPP random same-week opponents
    score = array("d", repeat(NAN, tab.n))
    rnd2 = random.Random(PW_SEED + 2)
    for wk, a, b, _ in tab.weeks:
        e = wk_era.get(wk)
        coef = fits.get(L.TEST_ERAS[e][0]) if e is not None and e >= 1 else None
        if coef is None:
            continue
        idx = [tab.perm[k] for k in range(a, b) if R.zE[tab.perm[k]] == R.zE[tab.perm[k]]]
        if len(idx) < 10:
            continue
        for i in idx:
            opp = rnd2.sample(idx, min(PW_OPP, len(idx) - 1) + 1)
            opp = [j for j in opp if j != i][:PW_OPP]
            score[i] = sum(_sig(sum(cc * f for cc, f in zip(coef, feats(i, j)))) for j in opp) / len(opp)
    fin = fits.get(L.FINAL)
    return {"name": "PW-L pairwise logistic", "accuracy": _clust(_main_only(R, acc)), "accuracy_E": _clust(_main_only(R, acc_E)),
            "delta": c, "eras": {ERA_NAMES[e]: v for e, v in eras.items()}, "gates": g, "p": _p_one(c["t"]), "confirm": eras.get(4),
            "score": score, "coefficients": sorted(zip(names, fin), key=lambda kv: -abs(kv[1]))[:12] if fin else [], "names": names}


# ------------------------------------------------------------------ refits judged against E: multi-task (MT), dynamic hierarchy (DH)
MT_K = [1000, 5000, 20000]
MT_DEPTH = ["class", "asset"]          # the economic-group node sits at the 'class' level of the multi-task tree
DH_K = [1000, 5000, 20000]


def economic_group(path: Sequence[str]) -> str:
    """The economic group of an asset across wrappers: equities, sector ETFs and commodities of the same sector share a
    group (energy stocks with crude and energy ETFs, metals miners with gold, …); rates products with Treasury ETFs;
    other classes stay whole."""
    cls = path[1].split(":", 1)[1] if len(path) > 1 else "?"
    sec = next((n.split(":", 1)[1].split("/")[-1] for n in path if n.startswith("sector:")), None)
    ptype = next((n.split(":", 1)[1].split("/")[-1] for n in path if n.startswith("ptype:")), None)
    if cls in ("Equity", "Commodity") and sec:
        return GROUP_ALIAS.get(sec, sec)
    if cls == "Equity":
        return "Broad equity" if ptype in ("equity index", "broad equity ETF", "international equity ETF", "factor ETF") else f"Equity/{ptype}"
    return cls


GROUP_ALIAS = {"Precious Metals": "Materials", "Industrial Metals": "Materials", "Metals": "Materials", "Energy": "Energy",
               "Agriculture": "Consumer Staples", "Basic Materials": "Materials", "Technology": "Information Technology",
               "Financial": "Financials", "Healthcare": "Health Care"}


def _rebuild_rows(tab: L.Table, path_of: Callable[[str], List[str]]) -> List[dict]:
    return [{"asset": tab.asset[k], "path": path_of(tab.asset[k]), "date": tab.date[k], "end": tab.end[k], "wk": tab.wk[k],
             "x": _Row(tab.X, k), "y": tab.y[k], "z": tab.z[k], "o": tab.o[k], "reg": tab.reg[k], "raw": tab.raw[k], "rec": tab.rec[k]}
            for k in range(tab.n)]


def multitask(data: Data, R: "Resid", progress=None) -> dict:
    """MT: global + economic group + asset, a full refit of the learned hierarchy on a different tree, K and depth nested
    exactly as E's own choice is made; judged against E on identical residual-period records."""
    tab = data.tab
    grp = {a: economic_group(p) for a, p in tab.paths.items()}
    rows = _rebuild_rows(tab, lambda a: ["global", f"class:G/{grp[a]}", f"asset:{a}"])
    t2 = L.Table(rows, tab.names, tab.h, "rank")
    del rows
    c2 = F.Ctx(t2, progress)
    opts = {f"{K}|{d}": (c2.lr.wf[(K, d)], c2.lr.sp[(K, d)]) for K in MT_K for d in MT_DEPTH if (K, d) in c2.lr.wf}
    ch = c2.pick(opts, default=f"{MT_K[1]}|class")
    s = _to_resid(R, t2, ch["wf"])
    groups: Dict[str, List[str]] = {}
    for a, g in grp.items():
        groups.setdefault(g, []).append(a)
    return {"name": "MT multi-task (economic groups)", "score": s, "choices": ch["choices"], "final": ch["final"], "groups": groups}


def _to_resid(R: "Resid", t2: L.Table, v: array) -> array:
    out = array("d", repeat(NAN, R.tab.n))
    for k in range(t2.n):
        j = R.key.get((t2.asset[k], t2.date[k]))
        if j is not None:
            out[j] = v[k]
    return out


def dynamic_hierarchy(data: Data, R: "Resid", progress=None) -> dict:
    """DH: E's tree, each node's deviation from its parent scaled by the positive-part James–Stein factor of its own
    out-of-sample incremental gain (the learned engine's node evidence over walk-forward years matured before the cut)."""
    lr = data.ctx.lr
    tab = data.tab
    keep_grid = lr.k_grid
    lr.k_grid = [K for K in DH_K if K in keep_grid]
    try:
        if not hasattr(lr, "ngain") or any(K not in lr.ngain for K in lr.k_grid):
            lr.gains()
    finally:
        lr.k_grid = keep_grid
    opts, trust_summary = {}, {}
    for K in DH_K:
        wf = array("d", repeat(NAN, tab.n))
        cache: Dict[Tuple[str, str], List[float]] = {}
        for a, (s0, s1) in tab.slices.items():
            path = tab.paths[a]
            for e in range(1, len(L.TEST_ERAS)):
                cut = L.TEST_ERAS[e][0]
                if cut not in lr.fits:
                    continue
                lo, hi = lr._era_range(s0, s1, e)
                if lo >= hi:
                    continue
                w = _dh_weights(lr, cut, K, path, cache)
                wf[lo:hi] = L.score_slice(lr.cols, L._coef(w, lr.fits[cut]["rms"]), lo, hi)
        opts[str(K)] = wf
    ch = nested_pick(tab, lr, opts, str(DH_K[1]), data.ctx.weekly_ric)
    # how much specialisation survived at the final cut (share of nodes × signals with trust > 0)
    K = int(ch["final"])
    years = lr.inner_years(L.FINAL)
    tot = kept = 0
    by_level: Dict[str, List[float]] = {}
    for nd in lr.tree.order[1:]:
        for i in range(lr.P):
            _, t, _ = lr.node_t(K, nd, i, years)
            j = L.js_trust(t)
            tot += 1
            kept += j > 0
            by_level.setdefault(L.level_of(nd), []).append(j)
    trust_summary = {"nodes_x_signals": tot, "trusted": kept, "mean_trust_by_level": {k: sum(v) / len(v) for k, v in by_level.items()}}
    return {"name": "DH dynamic hierarchy", "score": _to_resid(R, tab, ch["wf"]), "choices": ch["choices"], "final": ch["final"], "trust": trust_summary}


def _dh_weights(lr: L.Learner, cut: str, K: float, path: Sequence[str], cache: dict) -> List[float]:
    key = (cut, str(K), tuple(path))
    if key in cache:
        return cache[key]
    W = lr.fits[cut]["W"][K]
    years = lr.inner_years(cut)
    w = list(W["global"])
    for d in range(1, len(path)):
        nd, par = path[d], path[d - 1]
        for i in range(lr.P):
            dev = W[nd][i] - W[par][i]
            if dev:
                _, t, _ = lr.node_t(K, nd, i, years)
                w[i] += L.js_trust(t) * dev
    cache[key] = w
    return w


# ------------------------------------------------------------------ multi-horizon transfer (MH-1M, MH-3M)
MH_C = [0.5, 1.0, 2.0]
MH_LAMBDAS = [1e3, 1e4, 1e5]


def horizon_ctx(store, research, lab: str, progress=None, only: Optional[set] = None) -> Tuple[L.Table, F.Ctx]:
    from .lab import LAB_HORIZONS
    say = progress or (lambda m: None)
    h = dict(LAB_HORIZONS)[lab]
    rows, _, names = L.build_rows(store, research, lab, say, only)
    tab = L.Table(rows, names, h)
    del rows
    return tab, F.Ctx(tab, lambda m: say(f"{lab} {m}"), base=False)


def multi_horizon(data: Data, tab: L.Table, ctx: F.Ctx, lab: str) -> dict:
    """1M / 3M global ridge shrunk toward c × the 1W global weights fitted before the same cut (instead of toward 0)."""
    P = ctx.P
    lr1 = data.ctx.lr
    if list(tab.names) != list(data.tab.names):
        raise ValueError("multi-horizon transfer needs the same signal set at 1W and at " + lab)
    D_wf, D_sp = ctx.score_global(lambda cut: ctx.lr.fits[cut]["W"][L.K_DEFAULT]["global"])
    Dw = ctx.weekly_ric(D_wf)
    opts = {}
    for c in MH_C:
        for lam in MH_LAMBDAS:
            def w(cut, c=c, lam=lam):
                f1 = lr1.fits.get(cut)
                prior = [c * v for v in f1["W"][L.K_DEFAULT]["global"]] if f1 else [0.0] * P
                return L.ridge(ctx.lr.fits[cut]["ns"]["global"], ctx.rms(cut), prior, lam)
            opts[f"c={c:g}|λ={lam:g}"] = ctx.score_global(w)
    ch = ctx.pick(opts, default="c=1|λ=10000")
    costs = F.record_costs(tab)
    ev = F.evaluate(ctx, f"MH-{lab}", ch["wf"], ch["sp"], Dw, costs, lab)
    evD = F.evaluate(ctx, f"learned global {lab}", D_wf, D_sp, Dw, costs, lab)
    g = ev["gates"]
    gates = {"G1": bool(g.get("G1")), "G2": bool(g.get("G2")), "G3": bool(g.get("G3")), "G4": bool(g.get("G4")), "vs_global": bool(g.get("G5")),
             "t": g.get("vsD_t")}
    return {"name": f"MH-{lab} multi-horizon transfer", "lab": lab, "records": tab.n, "choices": ch["choices"], "final": ch["final"],
            "eval": ev, "eval_global": evD, "gates": gates, "p": _p_one(g.get("vsD_t")), "vsD": ev.get("vsD"), "eras_vsD": ev.get("eras_vsD")}


# ------------------------------------------------------------------ Directional residual (DR-1W, DR-1M)
DR_LAMBDAS = [10.0, 100.0, 1000.0]
DR_CAPS = [0.01, 0.02, 0.03, 0.05]
DR_INPUTS = ["ret_1w", "ret_1m", "volume_z", "vol_20"]


def directional_residual(tab: L.Table, lr: L.Learner, pE: array, zDmE: array, states: Dict[str, List[Optional[str]]], disp: array,
                         rel: Optional[array], label: str, progress=None) -> dict:
    """logit P = logit P_prior + x·b, |P − P_prior| ≤ cap; P_prior = the Directional program's prior-only model (Platt on
    the PIT prior z, refitted before every era). λ and cap nested by the weekly Brier gain over the prior."""
    from . import directional as D
    from . import dirnext as DN
    rnd = random.Random(21)
    n = tab.n
    z = array("d", ((v if v is not None else NAN) for v in tab.z))
    o = array("d", ((float(v) if v is not None else NAN) for v in tab.o))
    cols, names = [pE, zDmE], ["E percentile", "zD − zE"]
    if rel is not None:
        cols.append(array("d", ((_logit(v) if v == v else 0.0) for v in rel))); names.append("E reliability (logit)")
    for d in ("volatility", "market", "breadth"):
        st = states.get(d) or [None] * n
        cols.append(array("d", ((0.0 if x is None else (1.0 if x == STATE_POS[d] else -1.0)) for x in st))); names.append(f"{d} state")
    cols.append(disp); names.append("dispersion")
    for nm in DR_INPUTS:
        if nm in tab.names:
            cols.append(tab.X[tab.names.index(nm)]); names.append(nm)
    ok = [k for k in range(n) if z[k] == z[k] and o[k] == o[k]]
    prior = array("d", repeat(NAN, n))
    opts = {f"{lam:g}|{cap:g}": array("d", repeat(NAN, n)) for lam in DR_LAMBDAS for cap in DR_CAPS}
    fits = {}
    for e, cut in [(e, L.TEST_ERAS[e][0]) for e in range(len(L.TEST_ERAS))] + [(None, L.FINAL)]:
        tr_all = [k for k in ok if tab.end[k] < cut]
        if len(tr_all) < 2000:
            continue
        sub = tr_all if len(tr_all) <= MAX_TRAIN else sorted(rnd.sample(tr_all, MAX_TRAIN))
        pl = logit_fit([array("d", repeat(1.0, len(sub))), array("d", (z[k] for k in sub))], array("d", (o[k] for k in sub)), 1e-3, [0.0, 0.0])
        test = [k for k in ok if e is not None and tab.era[k] == e]
        for k in test:
            prior[k] = _sig(pl[0] + pl[1] * z[k])
        tr = [k for k in tr_all if all(c[k] == c[k] for c in cols)]
        if len(tr) < 2000:
            fits[cut] = {"platt": pl, "b": None}
            continue
        if len(tr) > MAX_TRAIN:
            tr = sorted(rnd.sample(tr, MAX_TRAIN))
        dz = Design(cols, tr, names, intercept=True)
        X = dz.matrix(tr)
        off = array("d", (pl[0] + pl[1] * z[k] for k in tr))
        yv = array("d", (o[k] for k in tr))
        fits[cut] = {"platt": pl, "design": dz, "b": {}}
        for lam in DR_LAMBDAS:
            b = logit_fit(X, yv, lam, [1.0] * len(X), off)          # the intercept is shrunk too: no free drift over the prior
            fits[cut]["b"][lam] = b
            if e is None:
                continue
            for k in test:
                if not all(c[k] == c[k] for c in cols):
                    continue
                p0 = prior[k]
                p1 = _sig(_logit(p0) + dz.lin(b, k))
                for cap in DR_CAPS:
                    opts[f"{lam:g}|{cap:g}"][k] = min(p0 + cap, max(p0 - cap, p1))
    for v in opts.values():                                   # records without inputs keep the prior
        for k in range(n):
            if v[k] != v[k] and prior[k] == prior[k]:
                v[k] = prior[k]

    def brier_gain(p):
        out = {}
        for wk, a, b, _ in tab.weeks:
            idx = [tab.perm[j] for j in range(a, b)]
            idx = [k for k in idx if p[k] == p[k] and prior[k] == prior[k]]
            if idx:
                out[wk] = sum((prior[k] - o[k]) ** 2 - (p[k] - o[k]) ** 2 for k in idx) / len(idx)
        return out
    ch = nested_pick(tab, lr, opts, "100|0.02", brier_gain)
    wf = ch["wf"]
    idx = [k for k in range(n) if wf[k] == wf[k] and prior[k] == prior[k] and tab.era[k] >= 1]
    recs = [tab.rec[k] for k in range(n)]
    preds = {"DR": {k: wf[k] for k in idx}, "prior": {k: prior[k] for k in idx}}
    h = tab.h
    m = D.dir_metrics([recs[k] for k in idx], [wf[k] for k in idx], h)
    mp = D.dir_metrics([recs[k] for k in idx], [prior[k] for k in idx], h)
    vp = D.paired(recs, h, preds, "DR", "prior")
    cal = DN.calibration([recs[k] for k in idx], [wf[k] for k in idx])
    eras = {}
    for e in range(1, len(L.TEST_ERAS)):
        ids = [k for k in idx if tab.era[k] == e]
        eras[e] = D.paired(recs, h, {kk: {k: preds[kk][k] for k in ids} for kk in preds}, "DR", "prior") if ids else {}
    won = sum(1 for e in (1, 2, 3) if (eras[e].get("brier_gain") or 0) > 0)
    g = {"t": vp.get("brier_t"), "brier": bool((vp.get("brier_t") or 0) >= T_MIN), "logloss": bool((vp.get("logloss_gain") or 0) > 0),
         "balanced": bool((m.get("balanced_accuracy") or 0) > (mp.get("balanced_accuracy") or 0)),
         "slope": bool(cal.get("slope") is not None and 0.8 <= cal["slope"] <= 1.25),
         "ece": bool(m.get("ece") is not None and mp.get("ece") is not None and m["ece"] <= mp["ece"] + 0.005),
         "eras": won == 3, "eras_won": won}
    adj = [abs(wf[k] - prior[k]) for k in idx]
    fin = fits.get(L.FINAL) or {}
    lam_f = float(ch["final"].split("|")[0])
    coef = fin["design"].coef_table(fin["b"][lam_f]) if fin.get("b") else []
    return {"name": f"DR-{label} directional residual", "choices": ch["choices"], "final": ch["final"], "gates": g, "p": _p_one(vp.get("brier_t")),
            "vs_prior": {k: vp.get(k) for k in ("brier_gain", "brier_t", "logloss_gain", "delta_acc", "auc_a", "auc_b", "n")},
            "metrics": {k: m.get(k) for k in ("brier", "log_loss", "balanced_accuracy", "ece", "accuracy")},
            "metrics_prior": {k: mp.get(k) for k in ("brier", "log_loss", "balanced_accuracy", "ece", "accuracy")},
            "calibration_slope": cal.get("slope"), "eras": {ERA_NAMES[e]: eras[e].get("brier_gain") for e in eras},
            "mean_abs_adjustment": (sum(adj) / len(adj)) if adj else None, "coefficients": coef[:10], "final_fit": fin, "cap_final": ch["final"],
            "prob": wf, "prior": prior}


# ------------------------------------------------------------------ uncertainty: E percentile buckets → relative return
BUCKETS = [(0, 5), (5, 10), (10, 20), (20, 40), (40, 60), (60, 80), (80, 90), (90, 95), (95, 100)]


def _q(v: List[float], q: float) -> Optional[float]:
    if not v:
        return None
    s_ = sorted(v)
    i = q * (len(s_) - 1)
    lo = int(math.floor(i))
    hi = min(len(s_) - 1, lo + 1)
    return s_[lo] + (s_[hi] - s_[lo]) * (i - lo)


def buckets(R: "Resid", score: Optional[array] = None) -> dict:
    """E's out-of-sample percentile buckets on 2013–2024 (and the 2025– period separately): the asset's 1W log return
    minus the week's cross-sectional mean, per bucket. The 2.5–97.5% range of single outcomes is the honest interval
    for one asset's week; the CI is for the bucket mean (week-clustered)."""
    tab = R.tab
    s = score if score is not None else R.zE
    _, p = week_normal(tab, s)
    rel = array("d", repeat(NAN, tab.n))
    for wk, a, b, _ in tab.weeks:
        idx = [tab.perm[k] for k in range(a, b) if p[tab.perm[k]] == p[tab.perm[k]]]
        if len(idx) < 8:
            continue
        m = sum(R.yr[k] for k in idx) / len(idx)
        for k in idx:
            rel[k] = R.yr[k] - m
    out = {}
    for period, eras in (("2013–2024", set(EVAL_ERAS)), ("2025–", {4})):
        rows = []
        for lo, hi in BUCKETS:
            ks = [k for k in range(tab.n) if rel[k] == rel[k] and tab.era[k] in eras and lo <= 100 * (p[k] + 0.5) < hi + (1e-9 if hi == 100 else 0)]
            v = [rel[k] for k in ks]
            if len(v) < 30:
                rows.append({"bucket": f"{lo}–{hi}", "n": len(v)})
                continue
            wk_m: Dict[int, List[float]] = {}
            for k in ks:
                wk_m.setdefault(tab.wk[k], []).append(rel[k])
            wm = [sum(x) / len(x) for x in wk_m.values()]
            mu = sum(v) / len(v)
            sdw = math.sqrt(sum((x - sum(wm) / len(wm)) ** 2 for x in wm) / max(1, len(wm) - 1))
            se = sdw / math.sqrt(len(wm))
            cost = sum(R.costs[k] for k in ks) / len(ks)
            sd = math.sqrt(sum((x - mu) ** 2 for x in v) / (len(v) - 1))
            neg = [x for x in v if x < 0]
            rows.append({"bucket": f"{lo}–{hi}", "n": len(v), "weeks": len(wm), "mean": mu, "median": _q(v, 0.5), "hit": sum(1 for x in v if x > 0) / len(v),
                         "vol": sd, "downside": math.sqrt(sum(x * x for x in neg) / len(v)), "p05": _q(v, 0.05),
                         "net_long": mu - 2 * cost, "net_short": -mu - 2 * cost, "ci95": (mu - 1.96 * se, mu + 1.96 * se),
                         "range95": (_q(v, 0.025), _q(v, 0.975))})
        ms = [r.get("mean") for r in rows]
        mono = None
        if all(x is not None for x in ms):
            rk = L._ranks(ms)
            nb = len(ms)
            mono = 1 - 6 * sum((i + 1 - r) ** 2 for i, r in enumerate(rk)) / (nb * (nb * nb - 1))
        viol = sum(1 for a, b in zip(ms, ms[1:]) if a is not None and b is not None and b < a)
        out[period] = {"rows": rows, "monotonicity_rho": mono, "violations": viol}
    return out


def bucket_of(pct: float) -> int:
    for i, (lo, hi) in enumerate(BUCKETS):
        if lo <= pct < hi or (hi == 100 and pct >= 100):
            return i
    return len(BUCKETS) - 1


# ------------------------------------------------------------------ synthetic capability worlds (protocol §13)
SYN_WORLDS = {
    "A": "missing linear signal",
    "B": "missing sector-specific signal",
    "C": "missing volatility-regime interaction",
    "D": "missing nonlinear threshold",
    "E": "optimal baseline, pure-noise residual",
    "F": "baseline overconfident in one asset class",
    "G": "two baselines, each best in a different regime",
}


def synth_world(kind: str, seed: int = 1, weeks: int = 1270, n_assets: int = 48, P: int = 8) -> Data:
    """A synthetic 1W world whose baseline E is intentionally incomplete in a known way. Assets: two classes (Equity,
    Rates) × two sectors; volatility and market states switch every ~26 / ~39 weeks. E, D and 'production' are fixed
    point-in-time formulas (trivially out of sample), so the residual machinery is tested exactly as on market data."""
    import datetime as _d
    from types import SimpleNamespace
    rnd = random.Random(1000 + 17 * seed + ord(kind))
    names = [f"sig{k}" for k in range(P - 1)] + ["ret_1w"]
    d0 = _d.date(2002, 1, 2)
    vol, mkt = "high_vol", "bull"
    rows = []
    for w in range(weeks):
        if w % 26 == 0:
            vol = "high_vol" if rnd.random() < 0.5 else "low_vol"
        if w % 39 == 0:
            mkt = "bull" if rnd.random() < 0.6 else "bear"
        d = d0 + _d.timedelta(days=7 * w)
        e = d + _d.timedelta(days=8)
        for k in range(n_assets):
            c, s_ = k % 2, (k // 2) % 2
            C = ("Equity", "Rates")[c]
            path = ["global", f"class:{C}", f"ptype:{C}/P", f"sector:{C}/P/S{s_}", f"asset:X{k:02d}"]
            x = [rnd.gauss(0, 1) for _ in range(P)]
            hv = vol == "high_vol"
            sE, sD = x[0], x[0] + 0.7 * rnd.gauss(0, 1)
            if kind == "A":
                mu = 0.3 * x[0] + 0.25 * x[1]
            elif kind == "B":
                mu = 0.3 * x[0] + (0.45 * x[1] if (c == 0 and s_ == 0) else 0.0)
            elif kind == "C":
                mu = 0.3 * x[0] + (0.35 if hv else -0.35) * x[1]
            elif kind == "D":
                mu = 0.3 * x[0] + 0.9 * ((1.0 if x[1] > 1.0 else 0.0) - 0.1587)
            elif kind == "E":
                mu = 0.3 * x[0]
            elif kind == "F":
                mu = 0.3 * x[0] if c == 0 else 0.0
            elif kind == "G":
                mu = 0.3 * x[0] if hv else 0.3 * x[1]
                sD = x[1]
            else:
                raise ValueError(kind)
            y = mu + rnd.gauss(0, 1)
            rows.append({"asset": f"X{k:02d}", "path": path, "date": d.isoformat(), "end": e.isoformat(), "wk": d.toordinal() // 7,
                         "x": x, "y": y, "z": 0.0, "o": 1 if y > 0 else 0, "reg": (mkt, vol, "rising_rates", "expansion"), "raw": x[0] + x[2],
                         "rec": SimpleNamespace(yr=0.01 * y, raw=None, meta={}, act=(), reg=None, sE=sE, sD=sD)})
    tab = L.Table(rows, names, 5, "rank")
    del rows
    sE = array("d", (r.sE for r in tab.rec))
    sD = array("d", (r.sD for r in tab.rec))
    sP = array("d", tab.raw)
    states = {d: [None] * tab.n for d in STATE_DIMS}
    for k, rg in enumerate(tab.reg):
        states["volatility"][k], states["market"][k], states["rates"][k] = rg[1], rg[0], rg[2]
        states["risk"][k] = "risk-on" if rg[0] == "bull" and rg[1] == "low_vol" else "risk-off"
    return _assemble(tab, None, sD, sE, sP, states, f"synthetic {kind}")


def alpha_suite(R: Resid, extra: Optional[Dict[str, array]] = None, progress=None) -> Tuple[Dict[str, dict], Dict[str, dict]]:
    """R1, R1s, R2, R3, R4, R5 (+ any extra per-asset scores, e.g. MT, DH, PW) evaluated with A1–A7 and FDR."""
    say = progress or (lambda m: None)
    t0 = time.time()
    m1 = r1(R); m1s = r1s(R, None); m2 = r2(R); m3 = r3(R); m4 = r4(R); m5 = r5(R, m1, m3, m4)
    models = {"R1": m1, "R1s": m1s, "R2": m2, "R3": m3, "R4": m4, "R5": m5}
    say(f"alpha residual challengers fitted ({time.time() - t0:.0f}s)")
    res = {k: evaluate_alpha(R, m["wf"], m["name"]) for k, m in models.items()}
    for k, s_ in (extra or {}).items():
        res[k] = evaluate_alpha(R, s_, k)
    finalize_alpha(res)
    return res, models


def r1_final_weights(R: Resid, m1: dict) -> Dict[str, float]:
    lam = float(m1["final"].split("|")[0])
    w = m1["fns"][lam][0](L.FINAL)
    return dict(zip(R.tab.names, w)) if w else {}


def capability(progress=None, worlds: Sequence[str] = tuple(SYN_WORLDS)) -> dict:
    """Protocol §13: every world must behave as planted before market data is touched."""
    say = progress or (lambda m: None)
    out = {}
    for kind in worlds:
        t0 = time.time()
        d = synth_world(kind)
        R = Resid(d)
        rel = reliability(R)
        de = d_vs_e(R, rel["rel"])
        pw = pairwise(R, rel["rel"])
        alpha, models = alpha_suite(R, {"PW": pw["score"]})
        meta = finalize_family({"REL": rel, "DE-A": de["DE-A"], "DE-B": de["DE-B"]}, {"REL": REL_GATES, "DE-A": DE_GATES, "DE-B": DE_GATES})
        pair = finalize_family({"PW-L": pw}, ("accuracy_gain", "t_ok", "eras"))
        tl = tails(R) if kind in ("E", "F") else None
        tail_items = {f"{k} {side}": tl[side][k] for side in ("top", "bottom") for k in ("T2", "T3", "T4")} if tl else {}
        if tail_items:
            finalize_family(tail_items, ("brier_gain", "t_ok", "eras", "logloss"))
        w = r1_final_weights(R, models["R1"])
        sigw = {k: v for k, v in w.items() if not k.startswith("ctx:")}
        top_sig = max(sigw, key=lambda k: abs(sigw[k])) if sigw else None
        splits = [nm for nm, _ in (models["R4"]["info"].get(models["R4"]["final"].split("|")[0]) or {}).get("top_splits", [])]
        dt = lambda k: alpha[k]["delta"]["mean"] or 0.0  # noqa: E731
        tt = lambda k: alpha[k]["delta"]["t"] or 0.0      # noqa: E731
        checks: Dict[str, bool] = {}
        if kind == "A":
            checks = {"R1 passes A1–A7": alpha["R1"]["passed"], "recovers sig1 as the largest missing weight (+)": top_sig == "sig1" and sigw["sig1"] > 0}
        elif kind == "B":
            checks = {"R3 improves E (t ≥ 2)": tt("R3") >= 2, "hierarchy beats the global residual": dt("R3") > dt("R1"),
                      "chooses sector or asset depth": models["R3"]["final"].split("|")[1] in ("sector", "asset"),
                      "A5 flags the single-sector concentration": not alpha["R3"]["gates"]["A5"]}
        elif kind == "C":
            checks = {"R4 passes A1–A7": alpha["R4"]["passed"], "R4 splits on volatility and sig1": "ctx:volatility" in splits[:3] and "sig1" in splits[:3],
                      "linear residual cannot see it (R1 t < 2)": tt("R1") < 2}
        elif kind == "D":
            checks = {"R4 passes A1–A7": alpha["R4"]["passed"], "trees beat the linear residual": dt("R4") > dt("R1"), "R4 splits on sig1 first": bool(splits) and splits[0] == "sig1"}
        elif kind == "E":
            checks = {"no Alpha residual passes": not any(v.get("passed") for v in alpha.values()),
                      "no D/E selector passes": not meta["DE-A"]["passed"] and not meta["DE-B"]["passed"],
                      "no pairwise model passes": not pair["PW-L"].get("passed"),
                      "no tail model passes": not any(v.get("passed") for v in tail_items.values()),
                      "REL adds nothing beyond conviction (t < 2)": (rel["beyond_conviction"]["t"] or 0) < 2}
        elif kind == "F":
            checks = {"R3 improves E (t ≥ 2)": tt("R3") >= 2,
                      "REL lower in the overconfident class": (rel["by_class"].get("class:Rates") or 1) < (rel["by_class"].get("class:Equity") or 0),
                      "REL beyond conviction (t ≥ 2)": (rel["beyond_conviction"]["t"] or 0) >= 2,
                      "class-aware tail model (T3) passes": bool(tail_items.get("T3 top", {}).get("passed") or tail_items.get("T3 bottom", {}).get("passed"))}
        elif kind == "G":
            pb = de["P_by_state"]
            checks = {"a D/E selector passes vs always-E": meta["DE-A"]["passed"] or meta["DE-B"]["passed"],
                      "prefers E in high volatility, D in low": (pb.get("volatility=high_vol") or 0) > 0.5 > (pb.get("volatility=other") or 1)}
        out[kind] = {"world": SYN_WORLDS[kind], "pass": all(checks.values()), "checks": checks, "seconds": round(time.time() - t0, 1),
                     "alpha": {k: {"delta": v["delta"]["mean"], "t": v["delta"]["t"], "passed": v["passed"],
                                   "failed": [x for x in ("A1", "A2", "A3", "A4", "A5", "A6", "A7") if not v["gates"].get(x)]} for k, v in alpha.items()},
                     "rel": {"auc_minus_half": rel["auc_minus_half"]["mean"], "t": rel["auc_minus_half"]["t"], "beyond_conviction_t": rel["beyond_conviction"]["t"],
                             "passed": meta["REL"]["passed"], "by_class": rel["by_class"]},
                     "de": {k: {"delta": meta[k]["delta_vs_E"]["mean"], "t": meta[k]["delta_vs_E"]["t"], "passed": meta[k]["passed"]} for k in ("DE-A", "DE-B")},
                     "de_P_by_state": de["P_by_state"], "pairwise": {"delta": pw["delta"]["mean"], "t": pw["delta"]["t"], "passed": pair["PW-L"].get("passed")},
                     "tails": {k: {"gain": v["brier_gain"]["mean"], "t": v["brier_gain"]["t"], "passed": v.get("passed")} for k, v in tail_items.items()},
                     "r1_top_signal": top_sig, "r4_top_splits": splits[:5], "r3_final": models["R3"]["final"]}
        say(f"capability world {kind} ({SYN_WORLDS[kind]}): {'PASS' if out[kind]['pass'] else 'FAIL'} ({time.time() - t0:.0f}s)")
    return out


# ------------------------------------------------------------------ today's research-only view
def _norm_scores(v: Dict[str, float]) -> Tuple[Dict[str, float], Dict[str, float]]:
    items = [(a, x) for a, x in v.items() if x is not None and x == x]
    if len(items) < 3:
        return {}, {}
    rk = L._ranks([x for _, x in items])
    m = len(items)
    z = {a: _inv_phi((r - 0.5) / m) for (a, _), r in zip(items, rk)}
    p = {a: (r - 0.5) / m - 0.5 for (a, _), r in zip(items, rk)}
    return z, p


def today_features(data: Data, today: List[dict], sE: Dict[str, float], sD: Dict[str, float], sP: Dict[str, float]) -> Dict[str, List[float]]:
    """Every asset's latest record as a feature vector aligned with data.feat_names (signals + context), with the context
    computed on today's cross-section exactly as on each historical week."""
    from .. import shaffer_score as cfg
    tab = data.tab
    names = list(tab.names)
    zE, pE = _norm_scores(sE)
    zD, _ = _norm_scores(sD)
    zP, _ = _norm_scores(sP)
    recs = {t["asset"]: t for t in today if t["asset"] in zE}
    j1w = names.index("ret_1w") if "ret_1w" in names else None
    jd = names.index("dist_ma200") if "dist_ma200" in names else None
    xs1 = [float(t["rec"].x[j1w]) for t in recs.values()] if j1w is not None else []
    disp = math.sqrt(sum((v - sum(xs1) / len(xs1)) ** 2 for v in xs1) / (len(xs1) - 1)) if len(xs1) > 2 else 0.0
    wide = None
    if jd is not None and recs:
        wide = (sum(1 for t in recs.values() if float(t["rec"].x[jd]) > 0) / len(recs)) >= 0.5
    fam_of = [cfg.ALL_FAMILY_OF.get(s_, cfg.FAMILY_OF.get(s_, "?")) for s_ in names]
    fidx: Dict[str, List[int]] = {}
    for i, f in enumerate(fam_of):
        fidx.setdefault(f, []).append(i)
    out = {}
    for a, t in recs.items():
        r = t["rec"]
        x = [float(v) for v in r.x]
        d: Dict[str, float] = {"ctx:zE": zE[a], "ctx:zD": zD.get(a, 0.0), "ctx:zP": zP.get(a, 0.0)}
        d["ctx:zD-zE"] = d["ctx:zD"] - d["ctx:zE"]
        d["ctx:|zE|"] = abs(d["ctx:zE"])
        d["ctx:pE"] = pE[a]
        d["ctx:pE^2"] = pE[a] ** 2
        cls = t["path"][1] if len(t["path"]) > 1 else "?"
        for c in CLASSES:
            d[f"ctx:{c}"] = 1.0 if cls == c else 0.0
        rg = r.reg or (None,) * 4
        st = {"volatility": rg[1], "market": rg[0], "rates": rg[2],
              "risk": None if rg[0] is None or rg[1] is None else ("risk-on" if rg[0] == "bull" and rg[1] == "low_vol" else "risk-off"),
              "breadth": None if wide is None else ("wide" if wide else "narrow")}
        for dim in STATE_DIMS:
            d[f"ctx:{dim}"] = 0.0 if st[dim] is None else (1.0 if st[dim] == STATE_POS[dim] else -1.0)
        d["ctx:dispersion"] = disp
        ms = [sum(x[i] for i in ix) / len(ix) for ix in fidx.values() if ix]
        mm = sum(ms) / len(ms)
        d["ctx:signal_disagreement"] = math.sqrt(sum((v - mm) ** 2 for v in ms) / max(1, len(ms) - 1))
        s0, s1 = tab.slices.get(a, (0, 0))
        d["ctx:log_history"] = math.log1p(s1 - s0)
        out[a] = x + [d[nm] for nm in data.feat_names[len(names):]]
    return out


def r1_today(R: Resid, choice: str, feats: Dict[str, List[float]]) -> Dict[str, dict]:
    """R1's correction today at a final choice 'λ|γ', and its uncertainty: se = the disagreement of the same ridge
    refitted on each residual era separately (as R1s does at every cut). `shrunk` = R1s's correction r̂·r̂²/(r̂² + se²);
    the correction is material only when |r̂| > 1.96·se."""
    ctx = R.ctx
    lam, gamma = float(choice.split("|")[0]), float(choice.split("|")[1])
    rms = ctx.rms(L.FINAL)
    w = L.ridge(ctx.lr.fits[L.FINAL]["ns"]["global"], rms, [0.0] * ctx.P, lam)
    coef = L._coef(w, rms)
    by_era: Dict[int, L.Stat] = {}
    for y, st in ctx.gy.items():
        if y >= 2009:
            ea = _era_of_year(y)
            by_era[ea] = st.copy() if ea not in by_era else by_era[ea].add(st)
    ecoefs = [L._coef(L.ridge(st, rms, [0.0] * ctx.P, lam), rms) for st in by_era.values() if st.sw > 0]
    assets = list(feats)
    P = len(R.tab.names)
    means = [sum(feats[a][i] for a in assets) / len(assets) for i in range(P)]
    out = {}
    for a in assets:
        xa = [feats[a][i] - means[i] for i in range(P)]
        r = sum(c * v for c, v in zip(coef, xa))
        ps = [sum(c * v for c, v in zip(ec, xa)) for ec in ecoefs]
        k = len(ps)
        se = math.sqrt(sum((p - sum(ps) / k) ** 2 for p in ps) / (k * (k - 1))) if k >= 2 else None
        shr = (r * r * r / (r * r + se * se)) if se is not None and (r * r + se * se) > 0 else 0.0
        contrib = sorted(((R.tab.names[i], coef[i] * xa[i]) for i in range(P)), key=lambda kv: -abs(kv[1]))[:3]
        out[a] = {"rhat": r, "gamma": gamma, "se": se, "shrunk": shr, "material": bool(se is not None and abs(r) > 1.96 * se), "drivers": contrib}
    return out


def adjusted_today(R: Resid, feats: Dict[str, List[float]], alpha: Dict[str, dict], models: Dict[str, dict]) -> Tuple[Optional[str], Dict[str, float]]:
    """Today's adjusted Alpha (within-today percentile of β·zE + γ·r̂) from the residual model that passed its gates —
    only R1 / R1s have a closed-form correction for today; another passing model is reported, not applied."""
    jz = R.tab.names.index("ctx:zE")
    for key in ("R1s", "R1"):
        if (alpha.get(key) or {}).get("passed"):
            ch = models[key]["final"]
            t = r1_today(R, ch, feats)
            g = float(ch.split("|")[1])
            s_ = {a: R.beta * feats[a][jz] + g * (t[a]["shrunk"] if key == "R1s" else t[a]["rhat"]) for a in feats}
            _, p = _norm_scores(s_)
            return key, {a: 100 * (v + 0.5) for a, v in p.items()}
    return None, {}


def _fit_prob(fit: Optional[dict], vals: Sequence[float]) -> Optional[float]:
    if not fit:
        return None
    return _sig(fit["design"].lin_vals(fit["b"], vals))


# ------------------------------------------------------------------ the market study (1W)
ALPHA_GATES = ("A1", "A2", "A3", "A4", "A5", "A6", "A7")
REL_GATES = ("auc", "t_ok", "monotone", "eras")
DE_GATES = ("beats_E", "t_ok", "eras", "churn")
TAIL_GATES = ("brier_gain", "t_ok", "eras", "logloss")
PAIR_GATES = ("accuracy_gain", "t_ok", "eras")


def _slim_alpha(v: dict) -> dict:
    return {k: v.get(k) for k in ("name", "delta", "rank_ic", "rank_ic_E", "eras", "confirm", "ls10", "ls10_E", "loco", "no_crisis",
                                  "calibration_rho", "calibration_rho_E", "gates", "p", "passed", "status")}


def study_1w(store, research, progress=None, rel_path: Optional[str] = None, with_refits: bool = True, only: Optional[set] = None) -> dict:
    """The complete 1W part of the program on the frozen E (and D): residual challengers, MT, DH, PW, reliability,
    D-vs-E, tails, pairwise, Directional residual 1W, buckets, today's research-only view."""
    import pickle
    say = progress or (lambda m: None)
    t0 = time.time()
    data = build_market(store, research, say, only)
    E, D = data.extra["E"], data.extra["D"]
    res: dict = {"records": data.tab.n, "assets": len(data.tab.slices), "signals": list(data.tab.names),
                 "context": [nm for nm in data.feat_names if nm.startswith("ctx:")]}
    res["reproduction"] = {"E_rank_ic": _clust(data.ctx.weekly_ric(E.wf)).get("mean"), "D_rank_ic": _clust(data.ctx.weekly_ric(D.wf)).get("mean"),
                           "E_choices": {k: str(v) for k, v in E.choices.items()}, "E_final": str(E.final)}
    R = Resid(data, lambda m: say(f"residual {m}"))
    res["residual"] = {"records": R.tab.n, "beta": R.beta, "from": min(R.tab.date), "to": max(R.tab.date)}
    say(f"residual table: {R.tab.n} records, β = {R.beta:.4f} ({time.time() - t0:.0f}s)")
    # meta first (REL feeds PW and the Directional residual)
    rel = reliability(R)
    if rel_path:
        with open(rel_path + ".tmp", "wb") as fh:
            pickle.dump({(a, d): rel["rel"][k] for k, (a, d) in enumerate(zip(R.tab.asset, R.tab.date)) if rel["rel"][k] == rel["rel"][k]}, fh)
        import os
        os.replace(rel_path + ".tmp", rel_path)
    say(f"E reliability ({time.time() - t0:.0f}s)")
    de = d_vs_e(R, rel["rel"])
    say(f"D vs E ({time.time() - t0:.0f}s)")
    pw = pairwise(R, rel["rel"])
    say(f"pairwise ({time.time() - t0:.0f}s)")
    extra = {"PW": pw["score"]}
    mt = dh = None
    if with_refits:
        mt = multitask(data, R, lambda m: say(f"MT {m}"))
        extra["MT"] = mt["score"]
        say(f"multi-task refit ({time.time() - t0:.0f}s)")
        dh = dynamic_hierarchy(data, R, say)
        extra["DH"] = dh["score"]
        say(f"dynamic hierarchy ({time.time() - t0:.0f}s)")
    alpha, models = alpha_suite(R, extra, say)
    say(f"Alpha residual family evaluated ({time.time() - t0:.0f}s)")
    tl = tails(R)
    say(f"tails ({time.time() - t0:.0f}s)")
    # the Directional residual on the full 1W table (prior-only is defined on every record; E's inputs from 2009)
    relm = array("d", repeat(NAN, data.tab.n))
    for k, j in enumerate(R.main):
        relm[j] = rel["rel"][k]
    disp = data.feats[data.feat_names.index("ctx:dispersion")]
    zdme = array("d", (d_ - e_ for d_, e_ in zip(data.zD, data.zE)))
    dr = directional_residual(data.tab, data.ctx.lr, data.pE, zdme, data.states, disp, relm, "1W", say)
    say(f"Directional residual 1W ({time.time() - t0:.0f}s)")
    bk = buckets(R)
    # ---- families, FDR, statuses
    finalize_family({"REL": rel, "DE-A": de["DE-A"], "DE-B": de["DE-B"]}, {"REL": REL_GATES, "DE-A": DE_GATES, "DE-B": DE_GATES})
    tail_items = {f"{k} {side}": tl[side][k] for side in ("top", "bottom") for k in ("T2", "T3", "T4")}
    finalize_family(tail_items, TAIL_GATES)
    finalize_family({"PW-L": pw}, PAIR_GATES)
    # ---- today
    fin_E = E.final_fn(E.final)
    fin_D = D.final_fn(D.final)
    sE = {a: v["score"] for a, v in F.today_scores(data.ctx, data.today, fin_E).items()}
    sD = {a: v["score"] for a, v in F.today_scores(data.ctx, data.today, fin_D).items()}
    sP = {t["asset"]: t["rec"].raw for t in data.today if t["rec"].raw is not None}
    feats = today_features(data, data.today, sE, sD, sP)
    rt = r1_today(R, models["R1"]["final"], feats)
    adj_model, adj = adjusted_today(R, feats, alpha, models)
    today = _today_view(data, R, feats, sE, sD, rt, rel, de, tl, dr, bk)
    for row in today:
        row["adjusted_pct"] = adj.get(row["asset"], row["E_pct"])
    res["adjusted_by"] = adj_model
    say(f"today's view: {len(today)} assets ({time.time() - t0:.0f}s)")
    res.update({
        "alpha": {k: _slim_alpha(v) for k, v in alpha.items()},
        "alpha_models": {k: {"choices": m.get("choices"), "final": m.get("final")} for k, m in models.items()},
        "r1_final_weights": r1_final_weights(R, models["R1"]),
        "r4_splits": models["R4"].get("info"),
        "mt": {k: v for k, v in (mt or {}).items() if k != "score"} if mt else None,
        "dh": {k: v for k, v in (dh or {}).items() if k != "score"} if dh else None,
        "rel": {k: v for k, v in rel.items() if k not in ("rel", "label", "fits")},
        "de": {**{k: {kk: vv for kk, vv in de[k].items() if kk != "score"} for k in ("DE-A", "DE-B")},
               **{k: de[k] for k in ("always_E", "always_D", "always_D_minus_E", "always_D_minus_E_eras", "P_by_state", "coefficients")}},
        "tails": {side: {k: {kk: vv for kk, vv in v.items() if kk not in ("prob", "final")} for k, v in tl[side].items()} for side in tl},
        "pairwise": {k: v for k, v in pw.items() if k != "score"},
        "directional_1W": {k: v for k, v in dr.items() if k not in ("prob", "prior", "final_fit")},
        "buckets": bk, "today": today, "seconds": round(time.time() - t0, 1)})
    res["_alpha_map"] = _alpha_map(data)
    return res


def _alpha_map(data: Data) -> Dict[str, List[Tuple[str, float]]]:
    """E's out-of-sample within-week percentile (0–1) by asset and date — the book Alpha input of the hedge policy."""
    out: Dict[str, List[Tuple[str, float]]] = {}
    for k in range(data.tab.n):
        p = data.pE[k]
        if p == p:
            out.setdefault(data.tab.asset[k], []).append((data.tab.date[k], round(p + 0.5, 4)))
    return {a: sorted(v) for a, v in out.items()}


def _today_view(data: Data, R: Resid, feats: Dict[str, List[float]], sE, sD, rt, rel, de, tl, dr, bk) -> List[dict]:
    from . import directional as Dm
    names = data.feat_names
    ctx_idx = [i for i, nm in enumerate(names) if nm.startswith("ctx:")]
    ctxv = lambda a: [feats[a][i] for i in ctx_idx]  # noqa: E731
    relfit = (rel.get("fits") or {}).get(L.FINAL)
    defit = de.get("final")
    zE, pE = _norm_scores(sE)
    zD, pD = _norm_scores(sD)
    rows_b = bk["2013–2024"]["rows"]
    # the tail classifiers' final fits: the benchmark T1 unless a richer model passed its gates
    t1 = {side: tl[side]["T1"].get("final") for side in ("top", "bottom")}
    tail_model = {side: next((k for k in ("T4", "T2", "T3") if tl[side][k].get("passed") and tl[side][k].get("final")), "T1")
                  for side in ("top", "bottom")}

    def tail_p(side, a, cub):
        m = tail_model[side]
        if m == "T1":
            return _fit_prob(t1[side], cub)
        if m == "T4":
            f4 = tl[side]["T4"]["final"]
            v = t1[side]["design"].lin_vals(t1[side]["b"], cub) if t1[side] else 0.0
            bins = [bisect.bisect_right(f4["edges"][q], feats[a][i]) for q, i in enumerate(f4["top"])]
            for f1_, th, leaves, _g in f4["trees"]:
                L_ = leaves[0] if bins[f1_] <= th else leaves[1]
                v += f4["rate"] * (L_[2] if L_[0] is None or bins[L_[0]] <= L_[1] else L_[3])
            return _sig(v)
        return _fit_prob(tl[side][m]["final"], cub + list(feats[a]) + ([pE[a] if feats[a][jn[f"ctx:{c}"]] else 0.0 for c in sorted(set(R.cls))] if m == "T3" else []))
    # Directional: prior-only (Platt at FINAL) and the residual's final fit
    drf = dr.get("final_fit") or {}
    lam_f = float(dr["final"].split("|")[0])
    cap_f = float(dr["final"].split("|")[1])
    dr_names = drf["design"].names if drf.get("design") else []
    jn = {nm: names.index(nm) for nm in names}
    out = []
    for t in data.today:
        a = t["asset"]
        if a not in feats:
            continue
        x = feats[a]
        pc = 100 * (pE[a] + 0.5)
        b = rows_b[bucket_of(pc)]
        relp = _fit_prob(relfit, ctxv(a))
        pde = _fit_prob(defit, ctxv(a))
        cub = [pE[a], pE[a] ** 2, pE[a] ** 3]
        ptop, pbot = tail_p("top", a, cub), tail_p("bottom", a, cub)
        z = Dm.z_of(t["rec"], Dm.MAIN_PRIOR)
        p0 = _sig(drf["platt"][0] + drf["platt"][1] * z) if (drf.get("platt") and z is not None) else None
        dadj = None
        if p0 is not None and drf.get("design"):
            vals = []
            for nm in dr_names:
                if nm == "E percentile":
                    vals.append(pE[a])
                elif nm == "zD − zE":
                    vals.append(zD.get(a, 0.0) - zE[a])
                elif nm == "E reliability (logit)":
                    vals.append(_logit(relp) if relp is not None else 0.0)
                elif nm.endswith(" state"):
                    vals.append(x[jn[f"ctx:{nm.split()[0]}"]])
                elif nm == "dispersion":
                    vals.append(x[jn["ctx:dispersion"]])
                else:
                    vals.append(x[jn[nm]] if nm in jn else 0.0)
            p1 = _sig(_logit(p0) + drf["design"].lin_vals(drf["b"][lam_f], vals))
            dadj = min(cap_f, max(-cap_f, p1 - p0))
        r = rt.get(a) or {}
        e_contrib = []
        if relfit:
            e_contrib = relfit["design"].contributions(relfit["b"], ctxv(a))[:2]
        out.append({"asset": a, "class": (t["path"][1].split(":", 1)[1] if len(t["path"]) > 1 else "?"), "date": t["rec"].date,
                    "E": sE.get(a), "E_pct": pc, "D": sD.get(a), "D_pct": 100 * (pD[a] + 0.5) if a in pD else None,
                    "residual": r.get("rhat"), "residual_se": r.get("se"), "residual_material": r.get("material"), "residual_drivers": r.get("drivers"),
                    "reliability": relp, "reliability_drivers": e_contrib, "p_E_better": pde, "preferred": (None if pde is None else ("E" if pde >= 0.5 else "D")),
                    "exp_rel_return": b.get("mean"), "exp_ci95": b.get("ci95"), "range95": b.get("range95"), "bucket": b.get("bucket"), "bucket_n": b.get("n"),
                    "p_top_decile": ptop, "p_bottom_decile": pbot, "tail_model": f"{tail_model['top']}/{tail_model['bottom']}", "dir_prior": p0, "dir_adjustment": dadj})
    return sorted(out, key=lambda r: -(r["E_pct"] or 0))



def _map_1w(data: Data, tab: L.Table, v: Sequence[float], max_days: int = 6) -> array:
    """A 1W per-record quantity carried to another horizon's records: the same asset's latest 1W record on or before
    the date (at most `max_days` old) — point in time."""
    import datetime as _d
    by: Dict[str, Tuple[List[str], List[float]]] = {}
    for a, (s0, s1) in data.tab.slices.items():
        pairs = sorted((data.tab.date[k], v[k]) for k in range(s0, s1) if v[k] == v[k])
        by[a] = ([p[0] for p in pairs], [p[1] for p in pairs])
    out = array("d", repeat(NAN, tab.n))
    for k in range(tab.n):
        ds, vs = by.get(tab.asset[k], ([], []))
        j = bisect.bisect_right(ds, tab.date[k]) - 1
        if j >= 0 and (_d.date.fromisoformat(tab.date[k]) - _d.date.fromisoformat(ds[j])).days <= max_days:
            out[k] = vs[j]
    return out


def study_horizons(store, research, progress=None, rel_path: Optional[str] = None, rel_wait: float = 4 * 3600,
                   only: Optional[set] = None) -> dict:
    """MH-1M and MH-3M (transfer from the 1W weights) and DR-1M (the Directional residual at 1M; E's inputs and REL are
    carried from the latest 1W record)."""
    import os
    import pickle
    say = progress or (lambda m: None)
    t0 = time.time()
    data = build_market(store, research, lambda m: say(f"1W {m}"), only)
    out: dict = {}
    for lab in ("1M", "3M"):
        tab, ctx = horizon_ctx(store, research, lab, say, only)
        out[f"MH-{lab}"] = multi_horizon(data, tab, ctx, lab)
        say(f"MH-{lab} ({time.time() - t0:.0f}s)")
        if lab != "1M":
            continue
        rel = None
        if rel_path:
            t1 = time.time()
            while not os.path.exists(rel_path) and time.time() - t1 < rel_wait:
                time.sleep(20)
            if os.path.exists(rel_path):
                with open(rel_path, "rb") as fh:
                    rm = pickle.load(fh)
                relw = array("d", (rm.get((a, d), NAN) for a, d in zip(data.tab.asset, data.tab.date)))
                rel = _map_1w(data, tab, relw)
        pE = _map_1w(data, tab, data.pE)
        zdme = _map_1w(data, tab, array("d", (d_ - e_ for d_, e_ in zip(data.zD, data.zE))))
        disp = _map_1w(data, tab, data.feats[data.feat_names.index("ctx:dispersion")])
        reg = F.Regimes(ctx)
        dr = directional_residual(tab, ctx.lr, pE, zdme, reg.state, disp, rel, lab, say)
        out[f"DR-{lab}"] = {k: v for k, v in dr.items() if k not in ("prob", "prior", "final_fit")}
        out[f"DR-{lab}"]["rel_available"] = rel is not None
        say(f"DR-{lab} ({time.time() - t0:.0f}s)")
    out["seconds"] = round(time.time() - t0, 1)
    return out


# ------------------------------------------------------------------ orchestration, statuses, versions
def _worker(db_path: str, kind: str, rel_path: str, only: Optional[set] = None, log: Optional[str] = None):
    from ..data.store import Store
    from .research import Research
    st = Store(db_path)

    def say(m):
        if log:
            with open(log, "a") as fh:
                fh.write(f"{time.strftime('%H:%M:%S')} [{kind}] {m}\n")
    try:
        r = Research(st)
        if kind == "1W":
            return study_1w(st, r, say, rel_path=rel_path, only=only)
        return study_horizons(st, r, say, rel_path=rel_path, only=only)
    finally:
        st.close()


def run_all(db_path: str, workers: int = 3, progress=None, with_capability: bool = True, only: Optional[set] = None,
            log: Optional[str] = None) -> dict:
    """Capability suite first (the engine is not run on market data if any world fails), then the 1W study, the
    horizon study and — once E's percentile map exists — the hedge policy."""
    import os
    from concurrent.futures import ProcessPoolExecutor
    say = progress or (lambda m: None)
    t0 = time.time()
    res: dict = {"started": time.strftime("%Y-%m-%d %H:%M:%S"), "protocol": "SHAFFER_RESIDUAL_PROTOCOL.md (2026-09-27)"}
    if with_capability:
        from ..hedge import hedgepolicy as HP
        res["capability"] = capability(say)
        hp = {k: HP.capability(k) for k in ("planted", "noise")}
        ok_p = hp["planted"]["passed"] and (hp["planted"]["low_vol_to_half"] or 0) > 0.8 and (hp["planted"]["high_vol_kept"] or 0) > 0.8
        res["capability"]["H"] = {"world": "hedge policy: planted 0.5× sizing in low volatility / a noise world", "pass": bool(ok_p and not hp["noise"]["passed"]),
                                  "checks": {"recovers the planted sizing and passes H1–H5": bool(ok_p), "noise world passes nothing": not hp["noise"]["passed"]},
                                  "detail": {k: {kk: vv for kk, vv in v.items() if kk != "cells"} for k, v in hp.items()}}
        say(f"capability hedge policy: {'PASS' if res['capability']['H']['pass'] else 'FAIL'}")
        if not all(v["pass"] for v in res["capability"].values()):
            res["aborted"] = "capability suite failed — market data not touched"
            return res
    rel_path = db_path + ".residual_rel.pkl"
    if os.path.exists(rel_path):
        os.remove(rel_path)
    with ProcessPoolExecutor(max_workers=max(2, workers)) as ex:
        f1 = ex.submit(_worker, db_path, "1W", rel_path, only, log)
        f2 = ex.submit(_worker, db_path, "horizons", rel_path, only, log)
        res["1W"] = f1.result()
        say(f"1W study finished ({time.time() - t0:.0f}s)")
        from ..hedge import hedgepolicy as HP
        res["hedge"] = HP.run(db_path, res["1W"].get("_alpha_map"), progress=say)
        say(f"hedge policy finished ({time.time() - t0:.0f}s)")
        res["horizons"] = f2.result()
        say(f"horizons finished ({time.time() - t0:.0f}s)")
    finalise(res)
    res["seconds"] = round(time.time() - t0, 1)
    return res


def finalise(res: dict) -> dict:
    """FDR families that span the two processes (horizon, directional) and every candidate's final status."""
    H = res.get("horizons") or {}
    hz = {k: H[k] for k in ("MH-1M", "MH-3M") if k in H}
    finalize_family(hz, ("G1", "G2", "G3", "G4", "vs_global"))
    dr = {"DR-1W": (res.get("1W") or {}).get("directional_1W") or {}, **{k: H[k] for k in ("DR-1M",) if k in H}}
    dr = {k: v for k, v in dr.items() if v.get("gates")}
    finalize_family(dr, ("brier", "logloss", "balanced", "slope", "ece", "eras"))
    for k, v in {**hz, **dr}.items():
        v["status"] = "PASSED (eligible for a live-shadow proposal)" if v.get("passed") else "NOT VALIDATED"
    res["eligible"] = eligible(res)
    return res


def eligible(res: dict) -> List[dict]:
    W = res.get("1W") or {}
    out = []
    for k, v in (W.get("alpha") or {}).items():
        if v.get("passed"):
            out.append({"family": "alpha", "candidate": k, "name": v.get("name")})
    for k in ("rel",):
        if (W.get(k) or {}).get("passed"):
            out.append({"family": "meta", "candidate": "REL", "name": "E reliability"})
    for k, v in (W.get("de") or {}).items():
        if isinstance(v, dict) and v.get("passed"):
            out.append({"family": "meta", "candidate": k, "name": v.get("name")})
    for side, d in (W.get("tails") or {}).items():
        for k, v in d.items():
            if v.get("passed"):
                out.append({"family": "tail", "candidate": f"{k} {side}", "name": v.get("name")})
    if (W.get("pairwise") or {}).get("passed"):
        out.append({"family": "pair", "candidate": "PW-L", "name": "pairwise logistic"})
    H = res.get("horizons") or {}
    for k in ("MH-1M", "MH-3M", "DR-1M"):
        if (H.get(k) or {}).get("passed"):
            out.append({"family": "horizon" if k.startswith("MH") else "directional", "candidate": k, "name": H[k].get("name")})
    if (W.get("directional_1W") or {}).get("passed"):
        out.append({"family": "directional", "candidate": "DR-1W", "name": "Directional residual 1W"})
    for k in (res.get("hedge") or {}).get("passed") or []:
        out.append({"family": "hedge", "candidate": k, "name": "hedge action policy cell"})
    return out


VERSION_OF = {"R1": "ridge", "R1s": "ridge-shrunk", "R2": "elasticnet", "R3": "hierarchy", "R4": "gbm", "R5": "ensemble",
              "MT": "multitask", "DH": "dynamic-hierarchy", "PW": "pairwise-score", "REL": "reliability", "DE-A": "de-select",
              "DE-B": "de-blend", "PW-L": "pairwise", "MH-1M": "transfer-1m", "MH-3M": "transfer-3m", "DR-1W": "dir-1w", "DR-1M": "dir-1m"}


def vid_of(key: str) -> str:
    return f"resid-{VERSION_OF.get(key, key.lower().replace(' ', '-'))}-exp"


def register(store, res: dict) -> List[str]:
    """Every pre-registered candidate gets its own immutable research version. Nothing is put into live shadow here —
    a passing candidate is only marked 'eligible for a live-shadow proposal'. D, E, production, the Directional prior,
    hedge-2 and the λ-hedge shadow are never touched."""
    from .lab import _save_registry, registry
    reg = registry(store)
    today = time.strftime("%Y-%m-%d")
    protected = {"alpha-learned-1w-global-exp", "alpha-learned-1w-hierarchy-exp", "hedge-lambda-sizing-exp"}
    W = res.get("1W") or {}
    H = res.get("horizons") or {}
    items: Dict[str, Tuple[str, dict]] = {}
    for k, v in (W.get("alpha") or {}).items():
        items[k] = ("shaffer-alpha", v)
    items["REL"] = ("meta", W.get("rel") or {})
    for k in ("DE-A", "DE-B"):
        items[k] = ("meta", (W.get("de") or {}).get(k) or {})
    items["PW-L"] = ("meta", W.get("pairwise") or {})
    for side in ("top", "bottom"):
        for k in ("T2", "T3", "T4"):
            items[f"{k} {side}"] = ("tail", ((W.get("tails") or {}).get(side) or {}).get(k) or {})
    items["DR-1W"] = ("shaffer-directional", W.get("directional_1W") or {})
    for k in ("MH-1M", "MH-3M", "DR-1M"):
        if k in H:
            items[k] = ("shaffer-alpha" if k.startswith("MH") else "shaffer-directional", H[k])
    made = []
    for k, (kind, v) in items.items():
        vid = vid_of(k)
        if vid in protected or not v:
            continue
        old = next((x for x in reg["versions"] if x["id"] == vid), None)
        if old and old.get("status") not in ("research", None):
            continue
        reg["versions"] = [x for x in reg["versions"] if x["id"] != vid]
        reg["versions"].append({"id": vid, "kind": kind, "family": "residual", "model": k, "status": "research",
                                "introduced": (old or {}).get("introduced") or today, "parent": "alpha-learned-1w-hierarchy-exp",
                                "formula": v.get("name"), "benchmark": "E (frozen, out of sample)" if kind != "shaffer-directional" else "prior-only",
                                "training_cutoff": res.get("started"), "eligible_for_live_shadow_proposal": bool(v.get("passed")),
                                "validation": {"gates": v.get("gates")}, "protocol": res.get("protocol")})
        made.append(vid)
    _save_registry(store, reg)
    return made


def save(store, res: dict):
    slim = dict(res)
    W = dict(res.get("1W") or {})
    W.pop("_alpha_map", None)
    store.kv_set(RESEARCH_KEY + ":today", {"today": W.pop("today", None), "built": res.get("started")})
    slim["1W"] = W
    store.kv_set(RESEARCH_KEY, slim)


def load(store) -> Optional[dict]:
    r = store.kv_get(RESEARCH_KEY)
    if r is None:
        return None
    t = store.kv_get(RESEARCH_KEY + ":today") or {}
    if r.get("1W") is not None:
        r["1W"]["today"] = t.get("today")
    return r
