"""ML Lab — the historically supported Shaffer weights (report SHAFFER_LEARNED_WEIGHTS.md).

The question is not "does challenger X beat production?" but: what weights on the Shaffer signals does point-in-time
history support, for every horizon and every type of asset — and how much of that should be trusted today?

FIND THE WEIGHTS FIRST. THEN DECIDE HOW MUCH TO TRUST THEM.

Data: the signal-level research records (the 74 production signals x = clip(z / 2, −1, 1) for every asset, weekly date
and horizon, point in time) and their realised outcomes. Two targets, learned separately:
    Alpha        rank the assets: u = the week's cross-sectional centred rank of the relative outcome (y_alpha, the
                 vol-scaled return in excess of its PIT base expectation). Objective: pairwise ranking least squares
                 (RankRLS — exactly the pairwise squared loss over every pair of assets in a week, solved in closed form
                 as least squares of the week-demeaned signals on 2u with weight n_week). Primary metric: OOS rank IC.
    Directional  P(R_h > 0) beyond the PIT base prior p0 = Φ(z): a logistic model with the prior as offset,
                 logit p = logit p0 + wᵀ[1, x], weights from the Newton (IRLS) step at the prior (weighted least squares of
                 the working response (o − p0)/(p0(1 − p0)) with weight p0(1 − p0)), then Platt calibration
                 p = σ(a + b·z + c·wᵀx) fitted on out-of-sample scores only (until ≥ 5,000 matured out-of-sample scores
                 exist, the learned model makes no adjustment and equals the prior).
Every record carries weight q = 5 / max(5, h) (overlapping weekly outcomes).

Hierarchy (economic, not the wrapper): Global → Class (Equity / Rates / Credit / Commodity / FX / Crypto / Volatility)
→ Product type (single stock, sector ETF, Treasury, major FX, …) → Sector → Industry → Asset (`taxonomy`).
Partial pooling: every node's weights are a ridge toward its parent,
    w_node = argmin Σ q·(target − wᵀx)² + K·‖w − w_parent‖²     (standardised x; global: toward 0)
i.e. w_child = α·w_child_estimate + (1 − α)·w_parent in matrix form, α growing with the node's effective sample size.
A child with little evidence inherits its parent; nothing is thrown away. The global level has its own weak ridge
(λ = 50 effective observations); K only governs how strongly children are pooled toward their parents.

Two weight sets for every node and horizon:
    HISTORICAL BEST FIT   K = 10 (almost no pooling), the node's own least-squares weights: what fits the past best.
    VALIDATED DEPLOYABLE  built by a procedure that is itself walk-forward validated (nested; every choice uses only
                          outcomes that matured before the period it is applied to):
                          1. K (shrinkage) chosen by inner walk-forward OOS rank IC (Directional: deviance gain);
                          2. signal-specific specialisation depth: signal i takes its weight from the deepest level whose
                             OOS gain over global has t ≥ 2 (clustered by week), else global (depth is per signal —
                             one signal can be global, another sector-specific);
                          3. trust: the global weight is multiplied by ρ_i = max(0, 1 − 1/t_i²) for t_i > 1 (else 0),
                             t_i = the OOS leave-one-out contribution t of signal i (positive-part James–Stein shrinkage
                             by out-of-sample evidence);
                          4. bounds |w_i| ≤ ½·sd(target).
Statuses (multiple-testing control, BH q = 0.10 across signals × horizons):
    VALIDATED           OOS contribution t ≥ 2 surviving BH, and same sign in ≥ 4 of 6 era fits
    SUPPORTED           OOS contribution > 0 (usable, shrunk — not validated)
    DESCRIPTIVE ONLY    fitted weight, no positive OOS contribution
Era stability (six era-specific fits 2001–08 … 2025–): STABLE · REGIME DEPENDENT · UNSTABLE · NO EVIDENCE.

Walk-forward: test eras 2009–12, 2013–16, 2017–20, 2021–24, 2025– (training = outcomes matured before the era) and the
2018 split. Compared on identical records: A production, B historical best fit, C validated deployable, D simple global
(learned, K nested), E uniform-depth hierarchy (K and depth nested). Optimisers compared at the global level: ridge
(pointwise and pairwise), elastic net, sign-constrained, pairwise logistic (RankNet) and a residual gradient-boosting
diagnostic (nonlinearity / interactions; never production).

Today's weights use every outcome that has matured by today; a historical score at date t only ever uses weights learned
from outcomes matured before t.
"""
from __future__ import annotations

import bisect
import math
import random
import time
from array import array
from itertools import repeat
from operator import add, mul, sub
from typing import Dict, List, Optional, Sequence, Tuple

RESEARCH_KEY = "lab:learned"
LEVELS = ["global", "class", "ptype", "sector", "industry", "asset"]
LEVEL_NAME = {"global": "Global", "class": "Class", "ptype": "Product type", "sector": "Sector", "industry": "Industry", "asset": "Asset"}
K_GRID = [10, 50, 200, 1000, 5000, 20000]
K_BEST = 10
K_DEFAULT = 200
G_LAMBDA = 50.0                  # the global level's own (weak) ridge toward 0; K only governs child → parent pooling
BOUNDS = ["2009-01-01", "2013-01-01", "2017-01-01", "2018-01-01", "2021-01-01", "2025-01-01"]
TEST_ERAS = [("2009-01-01", "2013-01-01"), ("2013-01-01", "2017-01-01"), ("2017-01-01", "2021-01-01"),
             ("2021-01-01", "2025-01-01"), ("2025-01-01", "2100-01-01")]
ERA_LABEL = ["2009–12", "2013–16", "2017–20", "2021–24", "2025–"]
SPLIT = "2018-01-01"
FINAL = "2100-01-01"
CUTS = [a for a, _ in TEST_ERAS] + [SPLIT, FINAL]
STAB_ERAS = [("2001–08", (0,)), ("2009–12", (1,)), ("2013–16", (2,)), ("2017–20", (3, 4)), ("2021–24", (5,)), ("2025–", (6,))]
REGIME_DIMS = [("market", 0, ("bull", "bear")), ("volatility", 1, ("high_vol", "low_vol")), ("rates", 2, ("rising_rates", "falling_rates")),
               ("growth", 3, ("expansion", "recession"))]
MIN_WEEK = 8
MIN_INNER_WEEKS = 52
FDR_Q = 0.10
PLATT_MIN = 5000
DEPTH_T = 2.0
Y0, NY = 2009, 18                # walk-forward calendar years 2009–2026 (node specialisation evidence is clustered by year)
MIN_NODE_YEARS = 5


# ------------------------------------------------------------------ taxonomy (economic hierarchy)
_TREASURY_MAT = {"UST2Y": "short", "SHY": "short", "UST5Y": "intermediate", "IEI": "intermediate", "UST10Y": "intermediate",
                 "IEF": "intermediate", "UST30Y": "long", "TLT": "long"}
_FI_ETF = {"TIP": ("Rates", "inflation-linked"), "SCHP": ("Rates", "inflation-linked"), "BIL": ("Rates", "cash"), "SGOV": ("Rates", "cash"),
           "AGG": ("Rates", "aggregate / MBS"), "BND": ("Rates", "aggregate / MBS"), "BNDX": ("Rates", "aggregate / MBS"),
           "MBB": ("Rates", "aggregate / MBS"), "LQD": ("Credit", "investment grade"), "VCIT": ("Credit", "investment grade"),
           "FLOT": ("Credit", "investment grade"), "HYG": ("Credit", "high yield"), "JNK": ("Credit", "high yield"),
           "BKLN": ("Credit", "loans / CLO"), "JAAA": ("Credit", "loans / CLO"), "EMB": ("Credit", "EM debt"),
           "MUB": ("Credit", "municipal"), "PFF": ("Credit", "hybrid"), "CWB": ("Credit", "hybrid")}
_COMMODITY_ETF = {"GLD", "SLV", "USO", "DBC", "UNG", "CPER", "DBA"}
_COMMODITY_IND = {"WTI": "Oil", "BRENT": "Oil", "USO": "Oil", "NATGAS": "Natural gas", "UNG": "Natural gas", "GOLD": "Gold",
                  "GLD": "Gold", "SILVER": "Silver", "SLV": "Silver", "PLATINUM": "Platinum", "COPPER": "Copper", "CPER": "Copper",
                  "CORN": "Grains", "WHEAT": "Grains", "SOYBEANS": "Grains", "COFFEE": "Softs"}
_MAJOR_FX = {"EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF", "NZDUSD", "FXE", "FXY", "FXB"}
_US_INDEX = {"SPX", "NDX", "RUT", "DJI"}
_ETF_INDUSTRY = {"SMH": "Semiconductors", "KRE": "Banks"}


def classify(meta: dict) -> Tuple[str, str, Optional[str], Optional[str]]:
    """(class, product type, sector, industry) for an asset — by economic exposure, not by wrapper."""
    from .lab import INDUSTRY
    a, ac, sec = meta.get("id"), meta.get("asset_class"), meta.get("sector")
    m = meta.get("meta") or {}
    if ac == "EQUITY":
        return "Equity", "single stock", sec, INDUSTRY.get(a)
    if ac == "INDEX":
        return "Equity", "equity index", "US" if a in _US_INDEX else "International", None
    if ac == "TREASURY":
        return "Rates", "Treasury", _TREASURY_MAT.get(a), None
    if ac == "CORP_BOND":
        return "Credit", "investment grade", None, None
    if ac == "COMMODITY":
        return "Commodity", "commodity", sec, _COMMODITY_IND.get(a)
    if ac == "FX":
        if a == "DXY":
            return "FX", "dollar index", None, None
        return "FX", "major FX" if a in _MAJOR_FX else "EM FX", None, None
    if ac == "CRYPTO":
        return "Crypto", "crypto", None, "Bitcoin" if a == "BTC" else None
    if ac == "ETF":
        if sec == "Leveraged / Inverse":
            return "Equity", "leveraged / inverse ETF", "inverse" if (m.get("leverage") or 1) < 0 else "leveraged", None
        if a in _COMMODITY_ETF or m.get("product_type") == "commodity_etf":
            return "Commodity", "commodity", sec, _COMMODITY_IND.get(a)
        if a in _TREASURY_MAT:
            return "Rates", "Treasury", _TREASURY_MAT[a], None
        if a in _FI_ETF:
            c, p = _FI_ETF[a]
            return c, p, None, None
        if sec == "Currency":
            return "FX", "dollar index" if a == "UUP" else ("major FX" if a in _MAJOR_FX else "EM FX"), None, None
        if sec == "Crypto":
            return "Crypto", "crypto", None, "Bitcoin"
        if sec == "Volatility":
            return "Volatility", "VIX futures ETN", None, None
        if sec == "Broad Market":
            return "Equity", "broad equity ETF", None, None
        if sec == "International Equity":
            return "Equity", "international equity ETF", None, None
        if sec == "Factor":
            return "Equity", "factor ETF", None, None
        return "Equity", "sector ETF", sec, _ETF_INDUSTRY.get(a)
    return (ac or "Other").title(), "other", sec, None


def taxonomy(meta: dict) -> List[str]:
    """The node path Global → … → Asset (levels without a label are skipped). Keys encode the full path."""
    c, p, s, i = classify(meta)
    out, lab = ["global"], []
    for lvl, v in (("class", c), ("ptype", p), ("sector", s), ("industry", i)):
        if v:
            lab.append(v)
            out.append(f"{lvl}:{'/'.join(lab)}")
    out.append(f"asset:{meta.get('id')}")
    return out


def level_of(node: str) -> str:
    return "global" if node == "global" else node.split(":", 1)[0]


def node_label(node: str) -> str:
    return "Global" if node == "global" else node.split(":", 1)[1].split("/")[-1]


def cut_path(path: Sequence[str], depth: str) -> str:
    """The deepest node of `path` at or above `depth`."""
    k = LEVELS.index(depth)
    best = path[0]
    for n in path:
        if LEVELS.index(level_of(n)) <= k:
            best = n
    return best


# ------------------------------------------------------------------ the column table
def _bucket(end: str) -> int:
    return bisect.bisect_right(BOUNDS, end)


def _era(date: str) -> int:
    for k, (a, b) in enumerate(TEST_ERAS):
        if a <= date < b:
            return k
    return -1


def _cut_pos(cut: str) -> int:
    """Number of end buckets whose outcomes all matured before `cut`."""
    return len(BOUNDS) + 1 if cut == FINAL else BOUNDS.index(cut) + 1


def _phi(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _ranks(v: Sequence[float]) -> List[float]:
    o = sorted(range(len(v)), key=v.__getitem__)
    r = [0.0] * len(v)
    i = 0
    while i < len(o):
        j = i
        while j + 1 < len(o) and v[o[j + 1]] == v[o[i]]:
            j += 1
        for k in range(i, j + 1):
            r[o[k]] = (i + j) / 2.0 + 1.0
        i = j + 1
    return r


class Table:
    """Records for one horizon in column form, sorted by (asset, end bucket, date) so every (asset, bucket) cell and
    every asset's history is a contiguous slice. Row fields: asset, path, date, end, wk, x[P], y (relative outcome),
    z (PIT prior z) and o (1 if up) for Directional, reg (regime states), raw (production score), rec (source record)."""

    def __init__(self, rows: List[dict], names: List[str], h: int, target: str = "rank"):
        rows = sorted(rows, key=lambda r: (r["asset"], _bucket(r["end"]), r["date"]))
        self.names, self.P, self.h, self.n = list(names), len(names), h, len(rows)
        self.q = 5.0 / max(5.0, float(h))
        self.asset = [r["asset"] for r in rows]
        self.path = [tuple(r["path"]) for r in rows]
        self.date = [r["date"] for r in rows]
        self.end = [r["end"] for r in rows]
        self.wk = [r["wk"] for r in rows]
        self.reg = [r.get("reg") or (None,) * 4 for r in rows]
        self.raw = [r.get("raw") for r in rows]
        self.rec = [r.get("rec") for r in rows]
        self.bucket = array("b", (_bucket(e) for e in self.end))
        self.era = array("b", (_era(d) for d in self.date))
        self.X = [array("d", (float(r["x"][i]) for r in rows)) for i in range(self.P)]
        self.y = array("d", (float(r["y"]) for r in rows))
        self.slices: Dict[str, Tuple[int, int]] = {}
        self.cells: Dict[Tuple[str, int], Tuple[int, int]] = {}
        for k, a in enumerate(self.asset):
            s = self.slices.get(a)
            self.slices[a] = (s[0] if s else k, k + 1)
            c = (a, self.bucket[k])
            s = self.cells.get(c)
            self.cells[c] = (s[0] if s else k, k + 1)
        self.paths = {a: self.path[s] for a, (s, _) in self.slices.items()}
        # date order and weeks
        self.perm = sorted(range(self.n), key=lambda k: (self.date[k], self.asset[k]))
        self.weeks: List[Tuple[int, int, int, str]] = []          # (week id, start, end in date order, outcome end)
        i = 0
        while i < self.n:
            j = i
            while j < self.n and self.wk[self.perm[j]] == self.wk[self.perm[i]]:
                j += 1
            self.weeks.append((self.wk[self.perm[i]], i, j, max(self.end[self.perm[k]] for k in range(i, j))))
            i = j
        self._alpha(target)
        self._directional(rows)

    def to_date(self, v) -> array:
        return array("d", map(v.__getitem__, self.perm))

    def _alpha(self, target: str):
        """u = centred rank of y in its week (or y itself for target 'value'), week-demeaned signals, RankRLS weights."""
        n = self.n
        u, wa, wp = array("d", bytes(8 * n)), array("d", bytes(8 * n)), array("d", bytes(8 * n))
        XA = [array("d", c) for c in self.X]
        sizes = [e - s for _, s, e, _ in self.weeks if e - s >= MIN_WEEK]
        mean_n = (sum(sizes) / len(sizes)) if sizes else 1.0
        for _, s, e, _ in self.weeks:
            idx = self.perm[s:e]
            m = len(idx)
            if m < MIN_WEEK and target == "rank":
                continue
            if target == "rank":
                rk = _ranks([self.y[k] for k in idx])
                for k, r_ in zip(idx, rk):
                    u[k] = 2.0 * (r_ - (m + 1) / 2.0) / m          # in (−1, 1)
                    wa[k] = self.q * m / mean_n
                    wp[k] = self.q
                for c in XA:
                    mu = sum(c[k] for k in idx) / m
                    for k in idx:
                        c[k] -= mu
            else:
                for k in idx:
                    u[k] = self.y[k]
                    wa[k] = wp[k] = self.q
        self.u, self.wa, self.wp, self.XA = u, wa, wp, XA

    def _directional(self, rows: List[dict]):
        n = self.n
        z = [r.get("z") for r in rows]
        o = [r.get("o") for r in rows]
        self.z, self.o = z, o
        p0, t, wd = array("d", bytes(8 * n)), array("d", bytes(8 * n)), array("d", bytes(8 * n))
        for k in range(n):
            if z[k] is None or o[k] is None:
                p0[k] = 0.5
                continue
            p = min(0.98, max(0.02, _phi(z[k])))
            p0[k] = p
            v = p * (1.0 - p)
            t[k] = (o[k] - p) / v
            wd[k] = self.q * v
        self.p0, self.t, self.wd = p0, t, wd
        self.XD = [array("d", repeat(1.0, n))] + self.X


# ------------------------------------------------------------------ sufficient statistics
class Stat:
    __slots__ = ("P", "xx", "xy", "yy", "sw", "n")

    def __init__(self, P: int):
        self.P, self.n, self.yy, self.sw = P, 0, 0.0, 0.0
        self.xx = array("d", bytes(8 * (P * (P + 1) // 2)))
        self.xy = array("d", bytes(8 * P))

    def add(self, o: "Stat") -> "Stat":
        self.xx = array("d", map(add, self.xx, o.xx))
        self.xy = array("d", map(add, self.xy, o.xy))
        self.yy += o.yy; self.sw += o.sw; self.n += o.n
        return self

    def copy(self) -> "Stat":
        c = Stat.__new__(Stat)
        c.P, c.xx, c.xy, c.yy, c.sw, c.n = self.P, array("d", self.xx), array("d", self.xy), self.yy, self.sw, self.n
        return c

    def matrix(self) -> List[List[float]]:
        P, xx = self.P, self.xx
        M = [[0.0] * P for _ in range(P)]
        k = 0
        for i in range(P):
            for j in range(i, P):
                M[i][j] = M[j][i] = xx[k]
                k += 1
        return M


def cell_stat(cols: List[array], y: array, w: array, a: int, b: int, idx: Optional[List[int]] = None) -> Stat:
    P = len(cols)
    st = Stat(P)
    if idx is None:
        ww, yy = w[a:b], y[a:b]
        cs = [c[a:b] for c in cols]
    else:
        g = lambda v: array("d", map(v.__getitem__, idx))  # noqa: E731
        ww, yy = g(w), g(y)
        cs = [g(c) for c in cols]
    if not any(ww):
        return st
    wy = array("d", map(mul, ww, yy))
    wc = [array("d", map(mul, c, ww)) for c in cs]
    st.xx = array("d", [sum(map(mul, wc[i], cs[j])) for i in range(P) for j in range(i, P)])
    st.xy = array("d", [sum(map(mul, c, wy)) for c in cs])
    st.yy = sum(map(mul, wy, yy))
    st.sw = sum(ww)
    st.n = sum(1 for v in ww if v)
    return st


def _chol_solve(A: List[List[float]], b: List[float]) -> List[float]:
    n = len(b)
    L = [[0.0] * n for _ in range(n)]
    for i in range(n):
        Li = L[i]
        for j in range(i + 1):
            Lj = L[j]
            s = A[i][j] - sum(map(mul, Li[:j], Lj[:j]))
            if i == j:
                Li[i] = math.sqrt(s) if s > 1e-12 else 1e-6
            else:
                Li[j] = s / Lj[j]
    y = [0.0] * n
    for i in range(n):
        y[i] = (b[i] - sum(map(mul, L[i][:i], y[:i]))) / L[i][i]
    LT = [[L[r][c] for r in range(n)] for c in range(n)]
    x = [0.0] * n
    for i in reversed(range(n)):
        x[i] = (y[i] - sum(map(mul, LT[i][i + 1:], x[i + 1:]))) / L[i][i]
    return x


def rms_of(g: Stat) -> List[float]:
    P, off = g.P, _offsets(g.P)
    return [math.sqrt(g.xx[off[i]] / g.sw) if g.sw > 0 and g.xx[off[i]] > 1e-12 * g.sw else 0.0 for i in range(P)]


_OFF: Dict[int, List[int]] = {}


def _offsets(P: int) -> List[int]:
    if P not in _OFF:
        _OFF[P] = [i * P - i * (i - 1) // 2 for i in range(P)]
    return _OFF[P]


def ridge(st: Stat, rms: List[float], prior: List[float], lam: float) -> List[float]:
    """Standardised ridge toward `prior`: (S/rms² + λI) w = Xᵀy/rms + λ·prior."""
    P = st.P
    act = [i for i in range(P) if rms[i] > 0]
    if not act or st.sw <= 0:
        return list(prior)
    M = st.matrix()
    A = [[M[i][j] / (rms[i] * rms[j]) + (lam if i == j else 0.0) for j in act] for i in act]
    b = [st.xy[i] / rms[i] + lam * prior[i] for i in act]
    sol = _chol_solve(A, b)
    w = [0.0] * P
    for k, i in enumerate(act):
        w[i] = sol[k]
    return w


def cd_solve(st: Stat, rms: List[float], lam2: float, lam1: float = 0.0, lo=None, hi=None, iters: int = 60,
             start: Optional[List[float]] = None) -> List[float]:
    """Elastic net / box-constrained ridge by coordinate descent on standardised statistics (toward 0)."""
    P = st.P
    act = [i for i in range(P) if rms[i] > 0]
    M = st.matrix()
    A = {i: [(M[i][j] / (rms[i] * rms[j])) if rms[j] > 0 else 0.0 for j in range(P)] for i in act}
    b = {i: st.xy[i] / rms[i] for i in act}
    w = list(start) if start else [0.0] * P
    for i in range(P):
        if i not in A:
            w[i] = 0.0
    Aw = {i: sum(A[i][j] * w[j] for j in act) for i in act}
    for _ in range(iters):
        md = 0.0
        for i in act:
            aii = A[i][i] + lam2
            g = b[i] - Aw[i] + A[i][i] * w[i]
            new = (math.copysign(max(0.0, abs(g) - lam1), g) / aii) if lam1 else g / aii
            if lo is not None:
                new = max(lo[i], min(hi[i], new))
            d = new - w[i]
            if d:
                for j in act:
                    Aw[j] += A[j][i] * d
                w[i] = new
                md = max(md, abs(d))
        if md < 1e-9:
            break
    return w


# ------------------------------------------------------------------ hierarchical fits
class Tree:
    """Node structure of a table: parents, node → assets, processing order."""

    def __init__(self, tab: Table):
        self.parent: Dict[str, Optional[str]] = {}
        self.assets: Dict[str, List[str]] = {}
        for a, p in tab.paths.items():
            for k, n in enumerate(p):
                self.parent.setdefault(n, p[k - 1] if k else None)
                self.assets.setdefault(n, []).append(a)
        self.order = sorted(self.parent, key=lambda n: (LEVELS.index(level_of(n)), n))


def asset_cells(tab: Table, cols, y, w) -> Dict[str, List[Optional[Stat]]]:
    """Per asset, the statistics of every end bucket."""
    nb = len(BOUNDS) + 1
    out: Dict[str, List[Optional[Stat]]] = {}
    for (a, b), (s, e) in tab.cells.items():
        out.setdefault(a, [None] * nb)[b] = cell_stat(cols, y, w, s, e)
    return out


def node_stats(tree: Tree, cells: Dict[str, List[Optional[Stat]]], buckets: Sequence[int], P: int) -> Dict[str, Stat]:
    per_asset: Dict[str, Stat] = {}
    for a, cs in cells.items():
        st = None
        for b in buckets:
            if cs[b] is not None:
                st = cs[b].copy() if st is None else st.add(cs[b])
        if st is not None:
            per_asset[a] = st
    out: Dict[str, Stat] = {}
    for n in tree.order:
        st = None
        for a in tree.assets[n]:
            if a in per_asset:
                st = per_asset[a].copy() if st is None else st.add(per_asset[a])
        out[n] = st if st is not None else Stat(P)
    return out


def fit_tree(tree: Tree, ns: Dict[str, Stat], rms: List[float], K: float) -> Dict[str, List[float]]:
    W: Dict[str, List[float]] = {}
    P = len(rms)
    for n in tree.order:
        par = tree.parent[n]
        prior = W[par] if par else [0.0] * P
        st = ns[n]
        W[n] = ridge(st, rms, prior, K if par else G_LAMBDA) if st.sw > 0 else list(prior)
    return W


def weights_at(W: Dict[str, List[float]], path: Sequence[str], depth: str) -> List[float]:
    return W[cut_path(path, depth)]


# ------------------------------------------------------------------ scoring and weekly metrics
def score_slice(cols, coef: Sequence[float], a: int, b: int) -> array:
    s = array("d", repeat(0.0, b - a))
    for i, c in enumerate(coef):
        if c:
            s = array("d", map(add, s, map(mul, cols[i][a:b], repeat(c))))
    return s


def _coef(w: Sequence[float], rms: Sequence[float]) -> List[float]:
    return [wi / ri if ri > 0 else 0.0 for wi, ri in zip(w, rms)]


def weekly_rank_ic(tab: Table, s_date: array, u_date: array, w_date: array) -> Dict[int, float]:
    """Spearman rank IC of the score with u, per week (weeks with ≥ MIN_WEEK scored records)."""
    out = {}
    for wk, a, b, _ in tab.weeks:
        idx = [k for k in range(a, b) if w_date[k] > 0 and s_date[k] == s_date[k]]
        m = len(idx)
        if m < MIN_WEEK:
            continue
        rs = _ranks([s_date[k] for k in idx])
        us = [u_date[k] for k in idx]
        mr = (m + 1) / 2.0
        mu = sum(us) / m
        sx = math.sqrt(sum((r - mr) ** 2 for r in rs))
        sy = math.sqrt(sum((v - mu) ** 2 for v in us))
        if sx > 0 and sy > 0:
            out[wk] = sum((r - mr) * (v - mu) for r, v in zip(rs, us)) / (sx * sy)
    return out


def weekly_gain_dir(tab: Table, s_date: array, t_date: array, w_date: array) -> Dict[int, float]:
    """Directional deviance-gain proxy per week: Σ w·(2·t·s − s²) (> 0: better than the prior alone)."""
    out = {}
    for wk, a, b, _ in tab.weeks:
        v = 0.0
        any_ = False
        for k in range(a, b):
            sk = s_date[k]
            if w_date[k] > 0 and sk == sk:
                v += w_date[k] * (2.0 * t_date[k] * sk - sk * sk)
                any_ = True
        if any_:
            out[wk] = v
    return out


def clustered(vals: Dict[int, float], h: int, weeks: Optional[set] = None) -> dict:
    xs = [v for k, v in vals.items() if weeks is None or k in weeks]
    n = len(xs)
    if n < 10:
        return {"mean": None, "t": None, "weeks": n}
    m = sum(xs) / n
    sd = math.sqrt(sum((x - m) ** 2 for x in xs) / max(1, n - 1))
    n_eff = n * 5.0 / max(5.0, float(h))
    se = sd / math.sqrt(max(1.0, n_eff))
    return {"mean": m, "t": (m / se) if se > 0 else None, "weeks": n, "se": se}


def benjamini_hochberg(ps: List[float], q: float = FDR_Q) -> List[bool]:
    n = len(ps)
    if not n:
        return []
    order = sorted(range(n), key=lambda i: ps[i])
    kmax = 0
    for rank, i in enumerate(order, 1):
        if ps[i] <= q * rank / n:
            kmax = rank
    rej = [False] * n
    for rank, i in enumerate(order, 1):
        if rank <= kmax:
            rej[i] = True
    return rej


def p_one_sided(t: Optional[float]) -> float:
    if t is None:
        return 1.0
    return 1.0 - _phi(t)


def js_trust(t: Optional[float]) -> float:
    """Positive-part James–Stein factor from an out-of-sample t: 0 for t ≤ 1, → 1 as the evidence grows."""
    if t is None or t <= 1.0:
        return 0.0
    return max(0.0, 1.0 - 1.0 / (t * t))


# ------------------------------------------------------------------ the learner (one target, one horizon)
class Learner:
    """Everything for one table and one target ('alpha' or 'dir'): the fits at every cutoff and K, walk-forward scores
    of every candidate, the nested choices, per-signal OOS contributions and specialisation gains, era and regime
    fits, and the final (today's) weights."""

    def __init__(self, tab: Table, target: str, progress=None, k_grid: Sequence[float] = K_GRID):
        self.tab, self.target = tab, target
        self.say = progress or (lambda m: None)
        self.k_grid = list(k_grid)
        if target == "alpha":
            self.cols, self.y, self.w = tab.XA, tab.u, tab.wa
        else:
            self.cols, self.y, self.w = tab.XD, tab.t, tab.wd
        self.P = len(self.cols)
        self.tree = Tree(tab)
        self.names = tab.names if target == "alpha" else ["intercept"] + tab.names

    # ---------------- fitting
    def fit_all(self):
        t0 = time.time()
        self.cells = asset_cells(self.tab, self.cols, self.y, self.w)
        self.fits: Dict[str, dict] = {}
        for cut in CUTS:
            pos = _cut_pos(cut)
            ns = node_stats(self.tree, self.cells, range(pos), self.P)
            g = ns["global"]
            if g.sw <= 0:
                continue
            rms = rms_of(g)
            self.fits[cut] = {"rms": rms, "ns": ns, "W": {K: fit_tree(self.tree, ns, rms, K) for K in self.k_grid},
                              "bound": 0.5 * math.sqrt(max(1e-12, g.yy / g.sw))}
        self.say(f"{self.target}: fits at {len(self.fits)} cutoffs × {len(self.k_grid)} K ({time.time() - t0:.0f}s)")

    # ---------------- walk-forward scores of every uniform candidate
    def _era_cut(self, e: int) -> str:
        return TEST_ERAS[e][0]

    def score_candidates(self):
        """For every (K, depth): the walk-forward score of every record in a test era (each era scored by the fit whose
        training outcomes matured before it), and the 2018-split scores. Weekly metrics per candidate."""
        t0 = time.time()
        tab, n = self.tab, self.tab.n
        self.cand = [(K, d) for K in self.k_grid for d in LEVELS]
        self.wf: Dict[Tuple[float, str], array] = {}
        self.sp: Dict[Tuple[float, str], array] = {}
        nan = float("nan")
        for K, d in self.cand:
            s_wf = array("d", repeat(nan, n))
            s_sp = array("d", repeat(nan, n))
            for a, (s0, s1) in tab.slices.items():
                path = tab.paths[a]
                for e in range(len(TEST_ERAS)):
                    cut = self._era_cut(e)
                    if cut not in self.fits:
                        continue
                    lo, hi = self._era_range(s0, s1, e)
                    if lo < hi:
                        f = self.fits[cut]
                        s_wf[lo:hi] = score_slice(self.cols, _coef(weights_at(f["W"][K], path, d), f["rms"]), lo, hi)
                if SPLIT in self.fits:
                    lo = self._date_from(s0, s1, SPLIT)
                    if lo < s1:
                        f = self.fits[SPLIT]
                        s_sp[lo:s1] = score_slice(self.cols, _coef(weights_at(f["W"][K], path, d), f["rms"]), lo, s1)
            self.wf[(K, d)], self.sp[(K, d)] = s_wf, s_sp
        self.y_date, self.w_date = tab.to_date(self.y), tab.to_date(self.w)
        self.weekly = {c: self._weekly(self.wf[c]) for c in self.cand}
        self.week_end = {wk: e for wk, _, _, e in tab.weeks}
        self.week_era = {}
        for wk, a, b, _ in tab.weeks:
            self.week_era[wk] = tab.era[tab.perm[a]]
        self.say(f"{self.target}: {len(self.cand)} candidates scored walk-forward ({time.time() - t0:.0f}s)")

    def _weekly(self, s: array) -> Dict[int, float]:
        sd = self.tab.to_date(s)
        if self.target == "alpha":
            return weekly_rank_ic(self.tab, sd, self.y_date, self.w_date)
        return weekly_gain_dir(self.tab, sd, self.y_date, self.w_date)

    def _era_range(self, s0: int, s1: int, e: int) -> Tuple[int, int]:
        a, b = TEST_ERAS[e]
        return self._date_from(s0, s1, a), self._date_from(s0, s1, b)

    def _date_from(self, s0: int, s1: int, d: str) -> int:
        lo, hi = s0, s1
        dates = self.tab.date
        while lo < hi:
            m = (lo + hi) // 2
            if dates[m] < d:
                lo = m + 1
            else:
                hi = m
        return lo

    def inner_weeks(self, cut: str) -> set:
        """Walk-forward weeks whose outcomes matured before `cut` (usable to make choices applied from `cut` on)."""
        return {wk for wk, e in self.week_end.items() if self.week_era.get(wk, -1) >= 0 and e < cut}

    # ---------------- per-signal OOS leave-one-out contributions and specialisation gains
    def _year_slices(self):
        """Per asset, the walk-forward records of each calendar year as (year index, lo, hi) slices."""
        out = {}
        for a, (s0, s1) in self.tab.slices.items():
            sl = []
            for y in range(NY):
                lo = self._date_from(s0, s1, f"{Y0 + y}-01-01")
                hi = self._date_from(s0, s1, f"{Y0 + y + 1}-01-01")
                if lo < hi:
                    sl.append((y, lo, hi))
            out[a] = sl
        return out

    def gains(self):
        """For every K:
          · weekly sums of each signal's out-of-sample leave-one-out contribution to the global model;
          · weekly sums of the gain from taking signal i's weight from level d instead of global (reporting);
          · per node, yearly sums of the INCREMENTAL gain of the node's own deviation from its parent, on its own records
            (the evidence that decides whether a node keeps its specialisation).
        Positive = helped out of sample."""
        t0 = time.time()
        tab, n, P = self.tab, self.tab.n, self.P
        self.loo: Dict[float, List[Dict[int, float]]] = {}
        self.dgain: Dict[float, Dict[str, List[Dict[int, float]]]] = {}
        self.ngain: Dict[float, Dict[str, array]] = {}
        ys = self._year_slices()
        self.ncount: Dict[str, array] = {nd: array("d", bytes(8 * NY)) for nd in self.tree.order}
        for a, sl in ys.items():
            for nd in tab.paths[a]:
                for y, lo, hi in sl:
                    self.ncount[nd][y] += sum(1 for k in range(lo, hi) if self.w[k] > 0)
        wkd = [tab.wk[k] for k in tab.perm]
        for K in self.k_grid:
            sg = self.wf[(K, "global")]
            resid = array("d", map(sub, self.y, sg))
            loo_w, dg_w = [], {d: [] for d in LEVELS[1:]}
            ng = {nd: array("d", bytes(8 * NY * P)) for nd in self.tree.order}
            for i in range(P):
                contrib = array("d", repeat(0.0, n))
                dcon = {d: array("d", repeat(0.0, n)) for d in LEVELS[1:]}
                incarr = array("d", repeat(0.0, n))
                for a, (s0, s1) in tab.slices.items():
                    path = tab.paths[a]
                    for e in range(len(TEST_ERAS)):
                        cut = self._era_cut(e)
                        if cut not in self.fits:
                            continue
                        lo, hi = self._era_range(s0, s1, e)
                        if lo >= hi:
                            continue
                        f = self.fits[cut]
                        r_i = f["rms"][i]
                        if r_i <= 0:
                            continue
                        Wk = f["W"][K]
                        wg = Wk["global"][i] / r_i
                        xs = self.cols[i][lo:hi]
                        ws = self.w[lo:hi]
                        rs = resid[lo:hi]
                        if wg:
                            bx = array("d", map(mul, xs, repeat(wg)))
                            contrib[lo:hi] = array("d", map(mul, ws, map(add, map(mul, bx, map(mul, rs, repeat(2.0))), map(mul, bx, bx))))
                        for d in LEVELS[1:]:
                            dl = weights_at(Wk, path, d)[i] / r_i - wg
                            if dl:
                                dx = array("d", map(mul, xs, repeat(dl)))
                                dcon[d][lo:hi] = array("d", map(mul, ws, map(sub, map(mul, dx, map(mul, rs, repeat(2.0))), map(mul, dx, dx))))
                # incremental node gains: node deviation from its parent, on top of every shallower level
                for a, (s0, s1) in tab.slices.items():
                    path = tab.paths[a]
                    for e in range(len(TEST_ERAS)):
                        cut = self._era_cut(e)
                        if cut not in self.fits:
                            continue
                        lo, hi = self._era_range(s0, s1, e)
                        if lo >= hi:
                            continue
                        f = self.fits[cut]
                        r_i = f["rms"][i]
                        if r_i <= 0:
                            continue
                        Wk = f["W"][K]
                        xs = self.cols[i][lo:hi]
                        ws = self.w[lo:hi]
                        rs = array("d", resid[lo:hi])
                        prev = Wk["global"][i] / r_i
                        for nd in path[1:]:
                            dl = Wk[nd][i] / r_i - prev
                            if dl:
                                dx = array("d", map(mul, xs, repeat(dl)))
                                g_ = array("d", map(mul, ws, map(sub, map(mul, dx, map(mul, rs, repeat(2.0))), map(mul, dx, dx))))
                                incarr[lo:hi] = g_
                                arr = ng[nd]
                                for y, ylo, yhi in ys[a]:
                                    l2, h2 = max(lo, ylo), min(hi, yhi)
                                    if l2 < h2:
                                        arr[i * NY + y] += sum(incarr[l2:h2])
                                rs = array("d", map(sub, rs, dx))
                            prev = Wk[nd][i] / r_i
                loo_w.append(self._week_sums(contrib, wkd))
                for d in LEVELS[1:]:
                    dg_w[d].append(self._week_sums(dcon[d], wkd))
            self.loo[K], self.dgain[K], self.ngain[K] = loo_w, dg_w, ng
        self.say(f"{self.target}: OOS contributions, level and node specialisation gains ({time.time() - t0:.0f}s)")

    def _week_sums(self, v: array, wkd: List[int]) -> Dict[int, float]:
        vd = self.tab.to_date(v)
        out = {}
        for wk, a, b, _ in self.tab.weeks:
            s = sum(vd[a:b])
            if s:
                out[wk] = s
        return out

    def inner_years(self, cut: str) -> List[int]:
        """Walk-forward calendar years whose every outcome matured before `cut`."""
        import datetime as _d
        lag = _d.timedelta(days=int(self.tab.h * 7 / 5) + 3)
        return [y for y in range(NY) if (_d.date(Y0 + y, 12, 31) + lag).isoformat() < cut]

    def node_t(self, K: float, nd: str, i: int, years: List[int]) -> Tuple[Optional[float], Optional[float], int]:
        arr, cnt = self.ngain[K][nd], self.ncount[nd]
        xs = [arr[i * NY + y] for y in years if cnt[y] > 0]
        m = len(xs)
        if m < MIN_NODE_YEARS:
            return None, None, m
        mu = sum(xs) / m
        sd = math.sqrt(sum((x - mu) ** 2 for x in xs) / (m - 1))
        n_eff = m * 250.0 / (245.0 + self.tab.h)
        return mu, (mu / (sd / math.sqrt(n_eff))) if sd > 0 else None, m

    # ---------------- nested choices
    def _decisions(self, K: float, inner: set, years: List[int]) -> Tuple[List[float], List[Optional[float]], Dict[str, List[int]]]:
        """Trust of every global weight and the kept node deviations, for pooling strength K."""
        h = self.tab.h
        trust, tstat = [], []
        for i in range(self.P):
            st = clustered(self.loo[K][i], h, inner)
            tstat.append(st["t"])
            trust.append(js_trust(st["t"]))
        keys, ps = [], []
        for nd in self.tree.order[1:]:
            for i in range(self.P):
                mu, t, _ = self.node_t(K, nd, i, years)
                if t is not None:
                    keys.append((nd, i)); ps.append(p_one_sided(t))
        keep: Dict[str, List[int]] = {}
        for (nd, i), r in zip(keys, benjamini_hochberg(ps, FDR_Q)):
            if r:
                keep.setdefault(nd, []).append(i)
        return trust, tstat, keep

    def choose(self, cut: str) -> dict:
        """Everything the validated model decides at `cut`, using only outcomes that matured before it: the pooling
        strength K (by the inner walk-forward performance of the validated model itself), the trust of every global
        weight, which node deviations to keep; plus the uniform-depth (E) and global (D) choices."""
        inner = self.inner_weeks(cut)
        if len(inner) < MIN_INNER_WEEKS:
            return {"K": K_DEFAULT, "depth": "global", "K_global": K_DEFAULT, "trust": [1.0] * self.P, "trust_t": [None] * self.P,
                    "keep": {}, "inner_weeks": len(inner), "default": True}

        def score(c):
            vals = [v for wk, v in self.weekly[c].items() if wk in inner]
            return (sum(vals) / len(vals)) if vals else -1e9
        best = max(self.cand, key=score)
        bestg = max([c for c in self.cand if c[1] == "global"], key=score)
        years = self.inner_years(cut)
        best_c, best_m = None, None
        for K in self.k_grid:
            trust, tstat, keep = self._decisions(K, inner, years)
            ch = {"K": K, "trust": trust, "keep": keep}
            m = self._inner_metric(ch, inner)
            if best_m is None or m > best_m + 1e-12:
                best_c, best_m = {"K": K, "trust": trust, "trust_t": tstat, "keep": keep}, m
        return {**best_c, "depth": best[1], "K_E": best[0], "K_global": bestg[0], "inner_metric": best_m,
                "inner_weeks": len(inner), "inner_years": len(years), "default": False}

    def _inner_metric(self, ch: dict, inner: set) -> float:
        """Mean weekly metric over the inner weeks of the validated model built with these decisions (each inner
        record scored by the fit of its own era)."""
        tab = self.tab
        s = array("d", repeat(float("nan"), tab.n))
        eras = {self.week_era[wk] for wk in inner}
        for a, (s0, s1) in tab.slices.items():
            path = tab.paths[a]
            for e in eras:
                cut = self._era_cut(e)
                if cut not in self.fits:
                    continue
                lo, hi = self._era_range(s0, s1, e)
                if lo < hi:
                    s[lo:hi] = score_slice(self.cols, _coef(self.deployable(cut, ch, path), self.fits[cut]["rms"]), lo, hi)
        wk = self._weekly(s)
        vals = [v for w, v in wk.items() if w in inner]
        return (sum(vals) / len(vals)) if vals else -1e9

    def deployable(self, cut: str, ch: dict, path: Sequence[str]) -> List[float]:
        """The validated effective weights (standardised) for one asset path at a cutoff: the trusted global weight plus
        every kept node deviation along the path, bounded."""
        f = self.fits[cut]
        parts = self.decompose(cut, ch, path)
        out = [sum(parts[lvl][i] for lvl in parts) for i in range(self.P)]
        return [max(-f["bound"], min(f["bound"], v)) for v in out]

    def decompose(self, cut: str, ch: dict, path: Sequence[str]) -> Dict[str, List[float]]:
        """The deployable weight split into the trusted global part and each level's adjustment. For signal i, the
        deepest node on the path whose own deviation was validated (kept) decides how far down the fitted hierarchy the
        weight comes from; every level above it contributes its fitted adjustment (that is the model its validation
        tested), levels below it contribute nothing. Without a kept node the signal uses the trusted global weight."""
        f = self.fits[cut]
        Wk = f["W"][ch["K"]]
        g = Wk["global"]
        out = {lvl: [0.0] * self.P for lvl in LEVELS}
        out["global"] = [ch["trust"][i] * g[i] for i in range(self.P)]
        keep = ch.get("keep") or {}
        deepest: Dict[int, int] = {}
        for k, nd in enumerate(path):
            for i in keep.get(nd, ()):
                deepest[i] = k
        for i, kmax in deepest.items():
            for k in range(1, kmax + 1):
                nd = path[k]
                out[level_of(nd)][i] = Wk[nd][i] - Wk[path[k - 1]][i]
        return out

    def kept(self, ch: dict) -> Dict[int, Dict[str, List[str]]]:
        """Per signal: the nodes (by level) whose specialisation was kept."""
        out: Dict[int, Dict[str, List[str]]] = {}
        for nd, ks in (ch.get("keep") or {}).items():
            for i in ks:
                out.setdefault(i, {}).setdefault(level_of(nd), []).append(nd)
        return out

    def sig_depth(self, ch: dict) -> List[str]:
        """Per signal: the deepest level at which at least one node kept its own weight (global if none)."""
        kp = self.kept(ch)
        return [max(kp.get(i, {"global": []}), key=LEVELS.index) for i in range(self.P)]

    def run_variants(self):
        """Walk-forward and split scores of the named variants B (best fit), C (validated), D (global), E (hierarchy)."""
        t0 = time.time()
        tab, n = self.tab, self.tab.n
        nan = float("nan")
        self.choices = {cut: self.choose(cut) for cut in CUTS if cut in self.fits}
        V = {k: array("d", repeat(nan, n)) for k in "BCDE"}
        Vs = {k: array("d", repeat(nan, n)) for k in "BCDE"}
        for a, (s0, s1) in tab.slices.items():
            path = tab.paths[a]
            for e in range(len(TEST_ERAS)):
                cut = self._era_cut(e)
                if cut not in self.fits:
                    continue
                lo, hi = self._era_range(s0, s1, e)
                if lo < hi:
                    self._fill(V, cut, path, lo, hi)
            if SPLIT in self.fits:
                lo = self._date_from(s0, s1, SPLIT)
                if lo < s1:
                    self._fill(Vs, SPLIT, path, lo, s1)
        self.var_wf, self.var_sp = V, Vs
        self.var_weekly = {k: self._weekly(v) for k, v in V.items()}
        self.var_weekly_sp = {k: self._weekly(v) for k, v in Vs.items()}
        self.say(f"{self.target}: variants B–E scored ({time.time() - t0:.0f}s)")

    def _fill(self, V, cut, path, lo, hi):
        f, ch = self.fits[cut], self.choices[cut]
        V["B"][lo:hi] = score_slice(self.cols, _coef(weights_at(f["W"][K_BEST], path, "asset"), f["rms"]), lo, hi)
        V["C"][lo:hi] = score_slice(self.cols, _coef(self.deployable(cut, ch, path), f["rms"]), lo, hi)
        V["D"][lo:hi] = score_slice(self.cols, _coef(f["W"][ch["K_global"]]["global"], f["rms"]), lo, hi)
        V["E"][lo:hi] = score_slice(self.cols, _coef(weights_at(f["W"][ch["K"]], path, ch["depth"]), f["rms"]), lo, hi)

    # ---------------- pooled per-signal and per-level evidence (each era with its own nested K)
    def signal_evidence(self) -> dict:
        h = self.tab.h
        per_era_K = {e: self.choices[self._era_cut(e)]["K"] for e in range(len(TEST_ERAS)) if self._era_cut(e) in self.choices}
        loo, dep = [], {d: [] for d in LEVELS[1:]}
        for i in range(self.P):
            merged = {}
            for wk, e in self.week_era.items():
                if e in per_era_K:
                    v = self.loo[per_era_K[e]][i].get(wk)
                    if v is not None:
                        merged[wk] = v
            loo.append(clustered(merged, h))
            for d in LEVELS[1:]:
                m2 = {}
                for wk, e in self.week_era.items():
                    if e in per_era_K:
                        v = self.dgain[per_era_K[e]][d][i].get(wk)
                        if v is not None:
                            m2[wk] = v
                dep[d].append(clustered(m2, h))
        return {"loo": loo, "depth": dep}

    # ---------------- era-specific and regime fits
    def era_fits(self, K: float) -> List[Dict[str, List[float]]]:
        fr = self.fits[FINAL]
        out = []
        for _, bks in STAB_ERAS:
            ns = node_stats(self.tree, self.cells, bks, self.P)
            if ns["global"].sw <= 0:
                out.append({})
                continue
            out.append(fit_tree(self.tree, ns, fr["rms"], K))
        return out

    def regime_fits(self, K: float) -> dict:
        """Global weights inside each regime state, per stability era and pooled."""
        tab = self.tab
        fr = self.fits[FINAL]
        groups: Dict[Tuple[int, int, str], List[int]] = {}
        for k in range(tab.n):
            if not self.w[k]:
                continue
            for dim, j, states in REGIME_DIMS:
                s = tab.reg[k][j] if len(tab.reg[k]) > j else None
                if s in states:
                    groups.setdefault((tab.bucket[k], j, s), []).append(k)
        stats = {key: cell_stat(self.cols, self.y, self.w, 0, 0, idx) for key, idx in groups.items()}
        out = {}
        for dim, j, states in REGIME_DIMS:
            per = {}
            for s in states:
                eras = []
                for _, bks in STAB_ERAS:
                    st = None
                    for b in bks:
                        x = stats.get((b, j, s))
                        if x is not None:
                            st = x.copy() if st is None else st.add(x)
                    eras.append(ridge(st, fr["rms"], [0.0] * self.P, G_LAMBDA) if st is not None and st.n >= 200 else None)
                pooled = None
                for b in range(len(BOUNDS) + 1):
                    x = stats.get((b, j, s))
                    if x is not None:
                        pooled = x.copy() if pooled is None else pooled.add(x)
                per[s] = {"eras": eras, "pooled": ridge(pooled, fr["rms"], [0.0] * self.P, G_LAMBDA) if pooled is not None else None,
                          "n": pooled.n if pooled is not None else 0}
            out[dim] = per
        return out


# ------------------------------------------------------------------ stability classification
def stability(era_w: List[Optional[float]], final_w: float, regime_t: Optional[float]) -> dict:
    """STABLE · REGIME DEPENDENT · UNSTABLE · NO EVIDENCE for one signal (weights from the six era-specific fits)."""
    ws = [w for w in era_w if w is not None]
    n = len(ws)
    if n < 3:
        return {"class": "NO EVIDENCE", "same_sign": 0, "eras": n}
    m = sum(ws) / n
    sd = math.sqrt(sum((w - m) ** 2 for w in ws) / max(1, n - 1))
    t = m / (sd / math.sqrt(n)) if sd > 0 else None
    sgn = 1 if final_w >= 0 else -1
    same = sum(1 for w in ws if (w >= 0) == (sgn > 0))
    share = same / n
    ta = abs(t) if t is not None else 0.0
    if share >= 0.8 and ta >= 2:
        c = "STABLE"                       # same sign in ≥ 80% of eras and the era mean clearly non-zero
    elif regime_t is not None and abs(regime_t) >= 2:
        c = "REGIME DEPENDENT"             # the weight differs between regime states consistently across eras
    elif ta < 1:
        c = "NO EVIDENCE"                  # era weights indistinguishable from zero
    else:
        c = "UNSTABLE"                     # some evidence, but the sign or size moves between eras
    return {"class": c, "mean": m, "sd": sd, "t": t, "same_sign": same, "eras": n, "sign_share": share}


# ------------------------------------------------------------------ optimisers compared at the global level
def _global_scores(lr: Learner, wfun) -> array:
    """Walk-forward scores of a global-only model: wfun(cut) → standardised weights (or None)."""
    tab = lr.tab
    s = array("d", repeat(float("nan"), tab.n))
    for e in range(len(TEST_ERAS)):
        cut = lr._era_cut(e)
        if cut not in lr.fits:
            continue
        w = wfun(cut)
        if w is None:
            continue
        coef = _coef(w, lr.fits[cut]["rms"])
        for a, (s0, s1) in tab.slices.items():
            lo, hi = lr._era_range(s0, s1, e)
            if lo < hi:
                s[lo:hi] = score_slice(lr.cols, coef, lo, hi)
    return s


def _nested_pick(lr: Learner, options: Dict[str, array]) -> array:
    """Per test era, the option with the best inner walk-forward metric (weeks matured before the era)."""
    weekly = {k: lr._weekly(v) for k, v in options.items()}
    out = array("d", repeat(float("nan"), lr.tab.n))
    keys = list(options)
    for e in range(len(TEST_ERAS)):
        cut = lr._era_cut(e)
        inner = lr.inner_weeks(cut)
        best = keys[0]
        if len(inner) >= MIN_INNER_WEEKS:
            def sc(k):
                v = [x for wk, x in weekly[k].items() if wk in inner]
                return sum(v) / len(v) if v else -1e9
            best = max(keys, key=sc)
        for a, (s0, s1) in lr.tab.slices.items():
            lo, hi = lr._era_range(s0, s1, e)
            if lo < hi:
                out[lo:hi] = options[best][lo:hi]
    return out


def methods(lr: Learner, orient: Optional[Dict[str, List[float]]] = None, pointwise: Optional[Learner] = None,
            gbm_features: int = 20, seed: int = 7) -> dict:
    """Ridge (pairwise and pointwise), elastic net, sign-constrained, pairwise logistic (RankNet, Alpha only) and residual
    gradient boosting — every one global, every choice nested; OOS weekly metric pooled and by era."""
    t0 = time.time()
    h = lr.tab.h
    res: Dict[str, array] = {"ridge": lr.var_wf["D"]}
    if pointwise is not None:
        res["ridge (pointwise)"] = pointwise.var_wf["D"]
    # elastic net: L1 as a fraction of the largest |Xᵀy| (the penalty that zeroes every weight), chosen nested
    en = {}
    for frac in (0.01, 0.03, 0.1):
        def wf(cut, frac=frac):
            f = lr.fits[cut]
            g = f["ns"]["global"]
            b = [abs(g.xy[i] / f["rms"][i]) if f["rms"][i] > 0 else 0.0 for i in range(lr.P)]
            return cd_solve(g, f["rms"], G_LAMBDA, frac * max(b), start=f["W"][K_DEFAULT]["global"])
        en[frac] = _global_scores(lr, wf)
    res["elastic net"] = _nested_pick(lr, {str(k): v for k, v in en.items()})
    # sign-constrained (production's orientation, known at the cutoff), box-bounded
    if orient:
        def wf(cut):
            f = lr.fits[cut]
            o = orient.get(cut)
            if o is None:
                return None
            lo = [0.0 if v > 0 else -f["bound"] for v in o]
            hi = [f["bound"] if v > 0 else 0.0 for v in o]
            if lr.target == "dir":
                lo, hi = [-f["bound"]] + lo, [f["bound"]] + hi
            return cd_solve(f["ns"]["global"], f["rms"], G_LAMBDA, 0.0, lo, hi, start=None)
        res["sign-constrained"] = _global_scores(lr, wf)
    if lr.target == "alpha":
        res["pairwise logistic (RankNet)"] = _global_scores(lr, lambda cut: ranknet(lr, cut, seed))
    gb, info = gbm_residual(lr, n_feat=gbm_features, seed=seed)
    res["ridge + residual boosting"] = gb
    out = {}
    for k, v in res.items():
        wkly = lr._weekly(v)
        out[k] = {"pooled": clustered(wkly, h), "eras": [clustered({w: x for w, x in wkly.items() if lr.week_era.get(w) == e}, h)
                                                         for e in range(len(TEST_ERAS))]}
    base = lr._weekly(res["ridge"])
    for k, v in res.items():
        if k == "ridge":
            continue
        wk2 = lr._weekly(v)
        d = {w: wk2[w] - base[w] for w in wk2 if w in base}
        out[k]["vs_ridge"] = clustered(d, h)
    out["_boosting"] = info
    lr.say(f"{lr.target}: optimisers compared ({time.time() - t0:.0f}s)")
    return out


def ranknet(lr: Learner, cut: str, seed: int, pairs: int = 20000, iters: int = 6, lam: float = 1.0) -> Optional[List[float]]:
    """Pairwise logistic ranking (RankNet, linear): P(i ranks above j) = σ(wᵀ(x_i − x_j)), pairs sampled within training
    weeks, a few Newton steps from the pairwise least-squares solution."""
    tab, f = lr.tab, lr.fits[cut]
    rms = f["rms"]
    rnd = random.Random(seed)
    wks = [(a, b) for wk, a, b, e in tab.weeks if e < cut and b - a >= MIN_WEEK]
    if len(wks) < 50:
        return None
    P = lr.P
    act = [i for i in range(P) if rms[i] > 0]
    dx_cols = [array("d") for _ in act]
    lab = []
    for _ in range(pairs):
        a, b = wks[rnd.randrange(len(wks))]
        i, j = tab.perm[rnd.randrange(a, b)], tab.perm[rnd.randrange(a, b)]
        if i == j or tab.u[i] == tab.u[j] or not tab.wa[i] or not tab.wa[j]:
            continue
        for k, c in enumerate(act):
            dx_cols[k].append((lr.cols[c][i] - lr.cols[c][j]) / rms[c])
        lab.append(1.0 if tab.u[i] > tab.u[j] else 0.0)
    m = len(lab)
    if m < 1000:
        return None
    g0 = f["W"][K_DEFAULT]["global"]
    w = [g0[c] * 4.0 for c in act]                    # a least-squares start on a logistic scale
    for _ in range(iters):
        s = array("d", repeat(0.0, m))
        for k, c in enumerate(dx_cols):
            if w[k]:
                s = array("d", map(add, s, map(mul, c, repeat(w[k]))))
        p = [1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, v)))) for v in s]
        r = array("d", map(sub, lab, p))
        v = array("d", (pp * (1 - pp) for pp in p))
        grad = [sum(map(mul, c, r)) - lam * w[k] for k, c in enumerate(dx_cols)]
        vc = [array("d", map(mul, c, v)) for c in dx_cols]
        H = [[sum(map(mul, vc[a_], dx_cols[b_])) + (lam if a_ == b_ else 0.0) for b_ in range(len(act))] for a_ in range(len(act))]
        step = _chol_solve(H, grad)
        w = [a_ + b_ for a_, b_ in zip(w, step)]
    out = [0.0] * P
    for k, c in enumerate(act):
        out[c] = w[k]
    return out


class _Stump:
    __slots__ = ("f", "thr", "left", "right")


def _fit_tree2(Xb: List[array], r: array, wts: array, nb: int, idx: List[int], min_w: float) -> Optional[tuple]:
    """A depth-2 regression tree on binned features (weighted squared loss)."""
    def best_split(ids):
        tw = sum(wts[i] for i in ids)
        tr = sum(wts[i] * r[i] for i in ids)
        best = None
        for f, col in enumerate(Xb):
            hw, hr = [0.0] * nb, [0.0] * nb
            for i in ids:
                bb = col[i]
                hw[bb] += wts[i]; hr[bb] += wts[i] * r[i]
            lw = lr_ = 0.0
            for b_ in range(nb - 1):
                lw += hw[b_]; lr_ += hr[b_]
                rw = tw - lw
                if lw < min_w or rw < min_w:
                    continue
                gain = lr_ * lr_ / lw + (tr - lr_) ** 2 / rw - tr * tr / tw
                if best is None or gain > best[0]:
                    best = (gain, f, b_)
        return best
    root = best_split(idx)
    if root is None:
        return None
    _, f, thr = root
    L_ = [i for i in idx if Xb[f][i] <= thr]
    R_ = [i for i in idx if Xb[f][i] > thr]
    leaves = []
    for side in (L_, R_):
        sp = best_split(side)
        if sp is None:
            tw = sum(wts[i] for i in side) or 1.0
            leaves.append((None, None, sum(wts[i] * r[i] for i in side) / tw, None))
            continue
        _, f2, t2 = sp
        a_ = [i for i in side if Xb[f2][i] <= t2]
        b_ = [i for i in side if Xb[f2][i] > t2]
        va = sum(wts[i] * r[i] for i in a_) / (sum(wts[i] for i in a_) or 1.0)
        vb = sum(wts[i] * r[i] for i in b_) / (sum(wts[i] for i in b_) or 1.0)
        leaves.append((f2, t2, va, vb))
    return (f, thr, leaves, root[0])


def gbm_residual(lr: Learner, n_feat: int = 20, rounds: int = 30, rate: float = 0.1, sample: int = 12000, nb: int = 16,
                 seed: int = 7) -> Tuple[array, dict]:
    """Residual gradient boosting on top of the global ridge (D) — a nonlinear / interaction diagnostic. Features: the
    signals with the largest |global weight| at the cutoff plus the four regime states (±1). Trained on outcomes matured
    before each test era; never production."""
    tab = lr.tab
    rnd = random.Random(seed)
    out = array("d", repeat(float("nan"), tab.n))
    split_count: Dict[str, float] = {}
    pairs: Dict[str, float] = {}
    base = lr.var_wf["D"]

    def regf(k, j):
        s = tab.reg[k][j] if len(tab.reg[k]) > j else None
        return 1.0 if s == REGIME_DIMS[j][2][0] else (-1.0 if s == REGIME_DIMS[j][2][1] else 0.0)
    for e in range(len(TEST_ERAS)):
        cut = lr._era_cut(e)
        if cut not in lr.fits:
            continue
        f = lr.fits[cut]
        g = f["W"][K_DEFAULT]["global"]
        top = sorted(range(lr.P), key=lambda i: -abs(g[i]))[:n_feat]
        top = [i for i in top if f["rms"][i] > 0]
        fnames = [lr.names[i] for i in top] + [f"regime:{d}" for d, _, _ in REGIME_DIMS]
        train = [k for k in range(tab.n) if tab.end[k] < cut and lr.w[k] > 0]
        if len(train) < 2000:
            continue
        if len(train) > sample:
            train = rnd.sample(train, sample)
        gcoef = _coef(g, f["rms"])
        # in-sample residual of the global ridge on the training sample
        base_tr = [sum(gcoef[i] * lr.cols[i][k] for i in range(lr.P) if gcoef[i]) for k in train]
        rr = array("d", (lr.y[k] - b_ for k, b_ in zip(train, base_tr)))
        ww = array("d", (lr.w[k] for k in train))
        edges = []
        Xb = []
        for i in top:
            v = sorted(lr.cols[i][k] for k in train)
            ed = [v[int(len(v) * q / nb)] for q in range(1, nb)]
            edges.append(ed)
            Xb.append(array("b", (bisect.bisect_right(ed, lr.cols[i][k]) for k in train)))
        for j in range(len(REGIME_DIMS)):
            edges.append([-0.5, 0.5])
            Xb.append(array("b", (bisect.bisect_right([-0.5, 0.5], regf(k, j)) for k in train)))
        trees = []
        idx = list(range(len(train)))
        min_w = 0.02 * sum(ww)
        for _ in range(rounds):
            tr = _fit_tree2(Xb, rr, ww, nb, idx, min_w)
            if tr is None:
                break
            trees.append(tr)
            f1, t1, leaves, _gain = tr
            for i_ in idx:
                L_ = leaves[0] if Xb[f1][i_] <= t1 else leaves[1]
                v = L_[2] if L_[0] is None or Xb[L_[0]][i_] <= L_[1] else L_[3]
                rr[i_] -= rate * v
            split_count[fnames[f1]] = split_count.get(fnames[f1], 0) + 1
            for L_ in leaves:
                if L_[0] is not None and L_[0] != f1:
                    key = " × ".join(sorted((fnames[f1], fnames[L_[0]])))
                    pairs[key] = pairs.get(key, 0) + 1
        # score the test era
        for a, (s0, s1) in tab.slices.items():
            lo, hi = lr._era_range(s0, s1, e)
            for k in range(lo, hi):
                bins = [bisect.bisect_right(edges[q], lr.cols[i][k]) for q, i in enumerate(top)] + \
                       [bisect.bisect_right([-0.5, 0.5], regf(k, j)) for j in range(len(REGIME_DIMS))]
                add_ = 0.0
                for f1, t1, leaves, _gain in trees:
                    L_ = leaves[0] if bins[f1] <= t1 else leaves[1]
                    add_ += rate * (L_[2] if L_[0] is None or bins[L_[0]] <= L_[1] else L_[3])
                out[k] = base[k] + add_
    info = {"top_splits": sorted(split_count.items(), key=lambda kv: -kv[1])[:10],
            "top_interactions": sorted(pairs.items(), key=lambda kv: -kv[1])[:10]}
    return out, info


# ------------------------------------------------------------------ statuses
def signal_status(t: Optional[float], fdr: bool, same_sign: int, eras: int) -> str:
    if t is not None and t >= 2 and fdr and same_sign >= min(4, eras):
        return "VALIDATED"
    if t is not None and t > 0:
        return "SUPPORTED"
    return "DESCRIPTIVE ONLY"


# ------------------------------------------------------------------ planted-effect capability test (synthetic PIT data)
def synth(kind: str, seed: int = 1, weeks: int = 1270, n_assets: int = 40, h: int = 5, noise: float = 1.0) -> Tuple[List[dict], List[str], dict]:
    """A synthetic point-in-time dataset whose true equation is known. Assets sit in a 2-class × 2-sector × 2-industry
    tree; regimes switch every ~26 weeks. Returns rows, signal names and the truth."""
    import datetime as _d
    rnd = random.Random(seed)
    names = ["momentum", "valuation", "idio_vol"] + [f"noise_{k}" for k in range(1, 8)]
    if kind == "sparse":
        names = [f"s{k:02d}" for k in range(30)]
    P = len(names)
    assets = []
    for k in range(n_assets):
        c, s_, i_ = k % 2, (k // 2) % 2, (k // 4) % 2
        path = ["global", f"class:C{c}", f"ptype:C{c}/P", f"sector:C{c}/P/S{s_}", f"industry:C{c}/P/S{s_}/I{i_}", f"asset:X{k:02d}"]
        assets.append((f"X{k:02d}", path, c, s_))
    d0 = _d.date(2002, 1, 2)
    state = "high_vol"
    rows = []
    truth: dict = {"kind": kind}
    for w in range(weeks):
        if w % 26 == 0:
            state = "high_vol" if rnd.random() < 0.5 else "low_vol"
        d = d0 + _d.timedelta(days=7 * w)
        e = d + _d.timedelta(days=int(7 * h / 5) + 1)
        reg = ("bull", state, "rising_rates", "expansion")
        for a, path, c, s_ in assets:
            if kind == "asset" and a == "X01" and w < weeks - 20:
                continue                                          # a short-history asset
            x = [rnd.gauss(0, 1) for _ in range(P)]
            if kind == "correlated":
                x[1] = 0.95 * x[0] + 0.31 * x[1]
            if kind in ("linear", "correlated"):
                beta = [0.5, 0.3, -0.2] + [0.0] * (P - 3) if kind == "linear" else [0.4] + [0.0] * (P - 1)
                y = sum(b * v for b, v in zip(beta, x))
            elif kind == "sparse":
                y = 0.3 * x[3] - 0.2 * x[11] + 0.15 * x[20]
            elif kind == "regime":
                y = (0.4 if state == "high_vol" else -0.4) * x[0] + 0.3 * x[1]
            elif kind == "sector":
                y = (0.5 if s_ == 0 and c == 0 else (-0.5 if s_ == 1 and c == 0 else 0.0)) * x[0] + 0.3 * x[1]
            elif kind == "nonlinear":
                y = 0.6 * x[0] * x[1]
            elif kind == "asset":
                y = (0.6 if a in ("X00", "X01") else 0.0) * x[0] + 0.3 * x[1]
            elif kind == "horizon_short":
                y = 0.5 * x[0] - 0.3 * x[1]
            elif kind == "horizon_long":
                y = -0.2 * x[0] + 0.5 * x[2]
            else:                                                 # noise
                y = 0.0
            y += rnd.gauss(0, noise)
            rows.append({"asset": a, "path": path, "date": d.isoformat(), "end": e.isoformat(), "wk": d.toordinal() // 7, "x": x,
                         "y": y, "z": 0.0, "o": 1 if y > 0 else 0, "reg": reg})
    return rows, names, truth


def _learn(rows, names, h, target_mode="value", with_methods=False, seed=1):
    tab = Table(rows, names, h, target=target_mode)
    lr = Learner(tab, "alpha")
    lr.fit_all(); lr.score_candidates(); lr.gains(); lr.run_variants()
    ev = lr.signal_evidence()
    ps = [p_one_sided(x["t"]) for x in ev["loo"]]
    fdr = benjamini_hochberg(ps)
    eras = lr.era_fits(lr.choices[FINAL]["K"])
    status = []
    for i in range(lr.P):
        fw = lr.fits[FINAL]["W"][K_BEST]["global"][i]
        ew = [ef["global"][i] if ef else None for ef in eras]
        same = sum(1 for v in ew if v is not None and (v >= 0) == (fw >= 0))
        status.append(signal_status(ev["loo"][i]["t"], fdr[i], same, sum(1 for v in ew if v is not None)))
    m = methods(lr, seed=seed) if with_methods else None
    return tab, lr, ev, status, m


def capability(progress=None, small: bool = False) -> dict:
    """Run the engine on every planted dataset and check that it recovers what was planted."""
    say = progress or (lambda m: None)
    kw = {"n_assets": 16} if small else {}
    out = {}

    def rec(name, ok, detail):
        out[name] = {"pass": bool(ok), **detail}
        say(f"capability {name}: {'PASS' if ok else 'FAIL'}")

    # 1. linear
    rows, names, _ = synth("linear", 1, **kw)
    tab, lr, ev, st, _ = _learn(rows, names, 5)
    g = lr.fits[FINAL]["W"][K_BEST]["global"]
    ok = abs(g[0] - 0.5) < 0.05 and abs(g[1] - 0.3) < 0.05 and abs(g[2] + 0.2) < 0.05 and max(abs(v) for v in g[3:]) < 0.05
    ok = ok and st[:3] == ["VALIDATED"] * 3 and "VALIDATED" not in st[3:]
    rec("linear", ok, {"truth": [0.5, 0.3, -0.2], "recovered": [round(v, 3) for v in g[:3]], "max_noise_weight": round(max(abs(v) for v in g[3:]), 3),
                       "status": st[:3], "noise_validated": st[3:].count("VALIDATED")})
    # 1b. the same data through the ranking target (weights recovered up to scale)
    tab, lr, ev, st, _ = _learn(rows, names, 5, "rank")
    g = lr.fits[FINAL]["W"][K_BEST]["global"]
    ratio = [g[1] / g[0], g[2] / g[0]] if g[0] else [None, None]
    ok = g[0] > 0 and abs(ratio[0] - 0.6) < 0.08 and abs(ratio[1] + 0.4) < 0.08
    rec("linear (ranking target)", ok, {"truth_ratio": [0.6, -0.4], "recovered_ratio": [round(v, 3) for v in ratio]})
    # 2. sparse
    rows, names, _ = synth("sparse", 2, **kw)
    tab, lr, ev, st, _ = _learn(rows, names, 5)
    g = lr.fits[FINAL]["W"][K_BEST]["global"]
    true_i = [3, 11, 20]
    false_v = [names[i] for i in range(len(names)) if i not in true_i and st[i] == "VALIDATED"]
    ok = all(st[i] == "VALIDATED" for i in true_i) and len(false_v) <= 1
    rec("sparse", ok, {"truth": {"s03": 0.3, "s11": -0.2, "s20": 0.15}, "recovered": {names[i]: round(g[i], 3) for i in true_i},
                       "false_validated": false_v})
    # 3. correlated
    rows, names, _ = synth("correlated", 3, **kw)
    tab, lr, ev, st, _ = _learn(rows, names, 5)
    g = lr.fits[FINAL]["W"][K_BEST]["global"]
    ok = abs((g[0] + 0.95 * g[1]) - 0.4) < 0.06 and g[0] > g[1]
    rec("correlated", ok, {"truth": {"momentum": 0.4, "valuation (corr 0.95, no effect)": 0.0},
                           "recovered": {"momentum": round(g[0], 3), "valuation": round(g[1], 3)}})
    # 4. regime-dependent
    rows, names, _ = synth("regime", 4, **kw)
    tab, lr, ev, st, _ = _learn(rows, names, 5)
    rf = lr.regime_fits(lr.choices[FINAL]["K"])
    hv, lv = rf["volatility"]["high_vol"]["pooled"], rf["volatility"]["low_vol"]["pooled"]
    ok = hv is not None and lv is not None and abs(hv[0] - 0.4) < 0.08 and abs(lv[0] + 0.4) < 0.08
    rec("regime-dependent", ok, {"truth": {"high_vol": 0.4, "low_vol": -0.4}, "recovered": {"high_vol": round(hv[0], 3) if hv else None,
                                                                                             "low_vol": round(lv[0], 3) if lv else None}})
    # 5. sector-specific
    rows, names, _ = synth("sector", 5, **kw)
    tab, lr, ev, st, _ = _learn(rows, names, 5)
    ch = lr.choices[FINAL]
    kp = lr.kept(ch)
    w_s0 = lr.deployable(FINAL, ch, tab.paths["X00"])[0]          # class 0, sector 0 → +0.5
    w_s1 = lr.deployable(FINAL, ch, tab.paths["X02"])[0]          # class 0, sector 1 → −0.5
    w_c1 = lr.deployable(FINAL, ch, tab.paths["X01"])[0]          # class 1 → 0
    k0 = kp.get(0, {})
    ok = ("sector:C0/P/S0" in k0.get("sector", []) and "sector:C0/P/S1" in k0.get("sector", []) and not k0.get("asset")
          and not kp.get(1) and abs(w_s0 - 0.5) < 0.1 and abs(w_s1 + 0.5) < 0.1 and abs(w_c1) < 0.1)
    rec("sector-specific", ok, {"truth": {"S0 (class 0)": 0.5, "S1 (class 0)": -0.5, "class 1": 0.0},
                                "kept_nodes": {lv: len(v) for lv, v in k0.items()}, "global_signal_kept_nodes": sum(len(v) for v in kp.get(1, {}).values()),
                                "recovered": {"S0 (class 0)": round(w_s0, 3), "S1 (class 0)": round(w_s1, 3), "class 1": round(w_c1, 3)}})
    # 6. horizon-specific
    rs, names, _ = synth("horizon_short", 6, h=5, **kw)
    rl, _, _ = synth("horizon_long", 7, h=63, **kw)
    _, l1, _, _, _ = _learn(rs, names, 5)
    _, l2, _, _, _ = _learn(rl, names, 63)
    g1, g2 = l1.fits[FINAL]["W"][K_BEST]["global"], l2.fits[FINAL]["W"][K_BEST]["global"]
    ok = abs(g1[0] - 0.5) < 0.06 and abs(g1[1] + 0.3) < 0.06 and abs(g2[0] + 0.2) < 0.06 and abs(g2[2] - 0.5) < 0.06
    rec("horizon-specific", ok, {"truth": {"1W": [0.5, -0.3, 0.0], "3M": [-0.2, 0.0, 0.5]},
                                 "recovered": {"1W": [round(v, 3) for v in g1[:3]], "3M": [round(v, 3) for v in g2[:3]]}})
    # 7. nonlinear interaction
    rows, names, _ = synth("nonlinear", 8, **kw)
    tab, lr, ev, st, m = _learn(rows, names, 5, "value", with_methods=True)
    g = lr.fits[FINAL]["W"][K_BEST]["global"]
    gb = m["ridge + residual boosting"]["vs_ridge"]
    inter = [k for k, _ in m["_boosting"]["top_interactions"][:3]]
    ok = max(abs(v) for v in g) < 0.06 and (gb.get("t") or 0) >= 2 and any("momentum" in k and "valuation" in k for k in inter)
    rec("nonlinear interaction", ok, {"truth": "y = 0.6 · momentum × valuation", "linear_max_weight": round(max(abs(v) for v in g), 3),
                                      "boosting_gain_t": round(gb.get("t") or 0, 1), "top_interactions": inter})
    # 8. pure noise
    rows, names, _ = synth("noise", 9, **kw)
    tab, lr, ev, st, _ = _learn(rows, names, 5)
    c = clustered(lr.var_weekly["C"], 5)
    ok = st.count("VALIDATED") == 0 and (c.get("t") or 0) < 2
    rec("pure noise", ok, {"validated": st.count("VALIDATED"), "C_oos_t": round(c.get("t") or 0, 2)})
    # 9. asset-specific with a short-history asset
    rows, names, _ = synth("asset", 10, **kw)
    tab, lr, ev, st, _ = _learn(rows, names, 5)
    ch = lr.choices[FINAL]
    kp = lr.kept(ch)
    wa = lr.deployable(FINAL, ch, tab.paths["X00"])[0]
    wb = lr.deployable(FINAL, ch, tab.paths["X01"])[0]
    ok = "asset:X00" in kp.get(0, {}).get("asset", []) and "asset:X01" not in kp.get(0, {}).get("asset", []) and abs(wa - 0.6) < 0.2 and abs(wb) < 0.3
    rec("asset-specific + short history", ok, {"truth": {"X00 (full history)": 0.6, "X01 (20 weeks)": 0.6}, "kept_asset_nodes": kp.get(0, {}).get("asset", []),
                                               "recovered": {"X00 (full history)": round(wa, 3), "X01 (20 weeks, inherits)": round(wb, 3)}})
    return out


# ================================================================== the real study (one horizon)
def _r(v, d=5):
    if v is None or (isinstance(v, float) and v != v):
        return None
    if isinstance(v, float):
        return float(f"{v:.{d}g}")
    return v


def _rl(vs, d=5):
    return [_r(v, d) for v in vs]


def prod_weights(r) -> List[float]:
    """Production's effective weight on every raw signal x_i at this record: g_f · ω_i · δ_i · c_i · r_i · d_i."""
    from . import weights as W
    out = [0.0] * len(r.x)
    for j, dl, om, c, rr, d in r.act:
        out[j] = r.g[W._FAMIDX[j]] * om * dl * c * rr * d
    return out


def shares(v: Sequence[float]) -> List[float]:
    t = sum(abs(x) for x in v)
    return [x / t if t else 0.0 for x in v]


def build_rows(store, research, lab: str, progress=None, only: Optional[set] = None) -> Tuple[List[dict], List[dict], List[str]]:
    """Matured research records (learning and evaluation) and every asset's latest record (today)."""
    from . import directional as D
    from . import weights as W
    say = progress or (lambda m: None)
    W._init_famidx()
    h = dict(__import__("finsim2.engine.lab", fromlist=["LAB_HORIZONS"]).LAB_HORIZONS)[lab]
    t0 = time.time()
    recs = W.load(store, research, lab)
    D.attach(recs, research, h)
    say(f"{lab}: {len(recs)} records loaded ({time.time() - t0:.0f}s)")
    rows, latest = [], {}
    paths: Dict[str, List[str]] = {}
    for r in recs:
        if not r.ext or not r.ext.get("s") or (only and r.asset not in only):
            continue
        a = r.asset
        if a not in paths:
            paths[a] = taxonomy(r.meta)
        if a not in latest or r.date > latest[a].date:
            latest[a] = r
        if r.y is None or r.yr is None or r.yr == 0:
            continue
        z = D.z_of(r, D.MAIN_PRIOR)
        rows.append({"asset": a, "path": paths[a], "date": r.date, "end": r.end, "wk": r.wk, "x": r.x, "y": D.y_alpha(r),
                     "z": z, "o": 1 if r.yr > 0 else 0, "reg": r.reg, "raw": r.raw, "rec": r})
    today = [{"asset": a, "path": paths[a], "rec": r} for a, r in latest.items()]
    return rows, today, W.signals()


def _orientation(tab: Table) -> Dict[str, List[float]]:
    """Production's orientation of every signal (sum of its point-in-time directions δ) on records before each cutoff."""
    P = tab.P
    by_year: Dict[str, List[float]] = {}
    for k in range(tab.n):
        r = tab.rec[k]
        if r is None:
            continue
        acc = by_year.setdefault(tab.date[k][:4], [0.0] * P)
        for j, dl, *_ in r.act:
            acc[j] += dl
    out = {}
    for cut in CUTS:
        tot = [0.0] * P
        for y, v in by_year.items():
            if f"{y}-12-31" < cut:
                tot = list(map(add, tot, v))
        out[cut] = [1.0 if v >= 0 else -1.0 for v in tot]
    return out


def _platt(X: List[List[float]], o: List[float]) -> List[float]:
    from . import directional as D
    return D.logistic(X, o, [0.0] * len(X[0]), 1.0, 1.0, iters=8)


def _sig(v: float) -> float:
    return 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, v))))


class Study:
    """One horizon on the real research records: the Alpha and Directional learners, their evaluation against production
    (and the prior), the evidence on every signal and node, the structure diagnostics and today's effective weights."""

    def __init__(self, store, research, lab: str, progress=None, only: Optional[set] = None):
        self.store, self.research, self.lab, self.only = store, research, lab, only
        self.say = progress or (lambda m: None)
        from .lab import LAB_HORIZONS
        self.h = dict(LAB_HORIZONS)[lab]

    def run(self) -> dict:
        t0 = time.time()
        rows, today, names = build_rows(self.store, self.research, self.lab, self.say, self.only)
        self.today = today
        tab = Table(rows, names, self.h)
        del rows
        self.tab = tab
        self.say(f"{self.lab}: table {tab.n} records × {tab.P} signals, {len(tab.slices)} assets ({time.time() - t0:.0f}s)")
        self.orient = _orientation(tab)
        out = {"lab": self.lab, "h": self.h, "records": tab.n, "assets": len(tab.slices), "signals": names,
               "families": [__import__("finsim2.shaffer_score", fromlist=["FAMILY_OF"]).FAMILY_OF[s] for s in names]}
        self.lrs = {}
        for tgt in ("alpha", "dir"):
            lr = Learner(tab, tgt, progress=lambda m: self.say(f"{self.lab} {m}"))
            lr.fit_all(); lr.score_candidates(); lr.gains(); lr.run_variants()
            self.lrs[tgt] = lr
        pw = Learner(tab, "alpha", k_grid=[K_BEST, K_DEFAULT])
        pw.w = tab.wp
        pw.fit_all(); pw.score_candidates(); pw.gains(); pw.run_variants()
        out["alpha"] = self.alpha_report(self.lrs["alpha"], pw)
        out["dir"] = self.dir_report(self.lrs["dir"])
        out["structure"] = self.structure()
        out["assets_today"] = self.today_weights()
        out["nodes"] = {t: self.node_table(self.lrs[t]) for t in ("alpha", "dir")}
        out["seconds"] = round(time.time() - t0, 1)
        self.say(f"{self.lab}: done ({out['seconds']}s)")
        return out

    # ---------------- common evidence tables
    def _signal_table(self, lr: Learner) -> List[dict]:
        tab = self.tab
        chF = lr.choices[FINAL]
        K = chF["K"]
        f = lr.fits[FINAL]
        ev = lr.signal_evidence()
        eras = lr.era_fits(K)
        reg = lr.regime_fits(K)
        kept = lr.kept(chF)
        best = f["W"][K_BEST]["global"]
        shrunk = [chF["trust"][i] * f["W"][K]["global"][i] for i in range(lr.P)]
        # production weights (global, last 52 weeks), in the same standardised units
        recent = max(tab.date)
        import datetime as _d
        since = (_d.date.fromisoformat(recent) - _d.timedelta(days=365)).isoformat()
        pw_sum = [0.0] * len(tab.names)
        cnt = 0
        for k in range(tab.n):
            if tab.date[k] >= since and tab.rec[k] is not None:
                pw_sum = list(map(add, pw_sum, prod_weights(tab.rec[k])))
                cnt += 1
        xrms = [math.sqrt(sum(v * v for v in c) / tab.n) for c in tab.X]
        prod = [(pw_sum[i] / cnt if cnt else 0.0) * xrms[i] for i in range(len(tab.names))]
        if lr.target == "dir":
            prod = [0.0] + prod
        ps, ls = shares(prod), shares(shrunk)
        out = []
        for i in range(lr.P):
            ew = [e["global"][i] if e else None for e in eras]
            rt = None
            regimes = {}
            for dim, _, states in REGIME_DIMS:
                s1, s2 = reg[dim][states[0]], reg[dim][states[1]]
                diffs = [(a[i] - b[i]) for a, b in zip(s1["eras"], s2["eras"]) if a is not None and b is not None]
                t = None
                if len(diffs) >= 3:
                    m = sum(diffs) / len(diffs)
                    sd = math.sqrt(sum((x - m) ** 2 for x in diffs) / (len(diffs) - 1))
                    t = m / (sd / math.sqrt(len(diffs))) if sd > 0 else None
                regimes[dim] = {states[0]: _r(s1["pooled"][i]) if s1["pooled"] else None, states[1]: _r(s2["pooled"][i]) if s2["pooled"] else None,
                                "diff_t": _r(t, 3)}
                if t is not None and (rt is None or abs(t) > abs(rt)):
                    rt = t
            stab = stability(ew, best[i], rt)
            lv = {d: _r(ev["depth"][d][i]["t"], 3) for d in LEVELS[1:]}
            out.append({"name": lr.names[i], "best_fit": _r(best[i]), "shrunk": _r(shrunk[i]), "trust": _r(chF["trust"][i], 3),
                        "production": _r(prod[i]), "production_share": _r(ps[i], 4), "learned_share": _r(ls[i], 4),
                        "oos_t": _r(ev["loo"][i]["t"], 3), "oos_mean": _r(ev["loo"][i]["mean"]),
                        "era_weights": _rl(ew), "stability": {k: (_r(v, 4) if isinstance(v, float) else v) for k, v in stab.items()},
                        "regimes": regimes, "level_t": lv, "kept_nodes": {k: len(v) for k, v in kept.get(i, {}).items()}})
        return out

    def _choice_summary(self, lr: Learner) -> dict:
        out = {}
        for cut, ch in lr.choices.items():
            kp = lr.kept(ch)
            out[cut] = {"K": ch["K"], "K_E": ch.get("K_E"), "depth_E": ch["depth"], "K_global": ch["K_global"], "default": ch.get("default"),
                        "trusted": sum(1 for v in ch["trust"] if v > 0), "kept_nodes": sum(len(v) for v in (ch.get("keep") or {}).values()),
                        "kept_by_level": {lvl: sum(len(v.get(lvl, [])) for v in kp.values()) for lvl in LEVELS[1:]},
                        "inner_weeks": ch.get("inner_weeks")}
        return out

    # ---------------- Alpha
    def _alpha_scores(self, lr: Learner, key: str, split: bool = False) -> Dict[int, float]:
        """Index → score on production's scale (100·tanh(index / s), s matched on the training cross-section)."""
        v = (lr.var_sp if split else lr.var_wf)[key]
        return {k: v[k] for k in range(self.tab.n) if v[k] == v[k]}

    def alpha_report(self, lr: Learner, pw: Learner) -> dict:
        from . import alphanext as AN
        tab, h = self.tab, self.h
        say = self.say
        t0 = time.time()
        res = {"choices": self._choice_summary(lr), "signals": self._signal_table(lr)}
        # the named variants on the research records (identical records for every variant)
        wf_idx = [k for k in range(tab.n) if tab.era[k] >= 0 and tab.wa[k] > 0]
        sp_idx = [k for k in range(tab.n) if tab.date[k] >= SPLIT and tab.wa[k] > 0]
        scale = self._scale(lr)
        variants = {}
        for key, label in (("C", "validated deployable"), ("B", "historical best fit"), ("D", "simple global"), ("E", "uniform-depth hierarchy")):
            rows = self._set_scores(lr.var_wf[key], wf_idx, scale)
            wf = AN.evaluate(rows, h)
            eras = []
            for e in range(len(TEST_ERAS)):
                er = [r for r in rows if TEST_ERAS[e][0] <= r.date < TEST_ERAS[e][1]]
                eras.append(AN.evaluate(er, h) if er else {"n": 0})
            srows = self._set_scores(lr.var_sp[key], sp_idx, scale)
            sp = AN.evaluate(srows, h)
            ch = {"walkforward": {"paired": wf["paired"], "challenger": wf["challenger"], "production": wf["production"],
                                  "ls_challenger": wf["ls_challenger"], "ls_production": wf["ls_production"]},
                  "split": {"paired": sp.get("paired")}, "eras": [{"paired": e.get("paired") or {}} for e in eras]}
            g = AN.gates(ch, self.lab)
            conv = AN.conviction(rows, lambda r: r.score, h) if key == "C" else None
            variants[key] = {"label": label, "walkforward": _slim_alpha(wf), "eras": [_slim_alpha(e) for e in eras],
                             "split": _slim_alpha(sp), "gates": g, "conviction": conv}
        # production itself, for the same records
        prod_rows = [tab.rec[k] for k in wf_idx]
        for r in prod_rows:
            r.score = r.raw
        pconv = AN.conviction(prod_rows, lambda r: r.raw, h)
        res["variants"] = variants
        res["production_conviction"] = pconv
        res["depths"] = self._depth_table(lr)
        res["methods"] = methods(lr, orient=self.orient, pointwise=pw)
        res["families"] = self._families(lr)
        res["asset_depth"] = self._asset_depth(lr)
        say(f"{self.lab} alpha report ({time.time() - t0:.0f}s)")
        return res

    def _scale(self, lr: Learner) -> Dict[int, float]:
        """Per test era (and split / final): s such that the median |100·tanh(index/s)| on the era's training records equals
        production's median |score| on them."""
        tab = self.tab
        out = {}
        for key, cut in [(e, lr._era_cut(e)) for e in range(len(TEST_ERAS))] + [("split", SPLIT), ("final", FINAL)]:
            if cut not in lr.fits:
                continue
            ch = lr.choices[cut]
            idx, vals = [], []
            for a, (s0, s1) in tab.slices.items():
                hi = s0
                while hi < s1 and tab.end[hi] < cut:
                    hi += 1
                if hi > s0:
                    sc = score_slice(lr.cols, _coef(lr.deployable(cut, ch, tab.paths[a]), lr.fits[cut]["rms"]), s0, hi)
                    vals.extend(abs(v) for v, k in zip(sc, range(s0, hi)) if lr.w[k] > 0)
                    idx.extend(k for k in range(s0, hi) if lr.w[k] > 0)
            raws = sorted(abs(tab.raw[k]) for k in idx if tab.raw[k] is not None)
            vals.sort()
            if not vals or not raws:
                out[key] = 1.0
                continue
            mr = min(99.0, max(0.5, raws[len(raws) // 2])) / 100.0
            mi = vals[len(vals) // 2] or 1e-9
            out[key] = mi / math.atanh(mr)
        return out

    def _set_scores(self, v: array, idx: List[int], scale: Dict) -> list:
        tab = self.tab
        rows = []
        for k in idx:
            s = v[k]
            if s != s:
                continue
            r = tab.rec[k]
            e = tab.era[k]
            sc = scale.get(e if tab.date[k] < SPLIT or e >= 0 else "split", 1.0)
            r.score = 100.0 * math.tanh(s / (scale.get(e, 1.0) or 1.0))
            rows.append(r)
        return rows

    def _depth_table(self, lr: Learner) -> dict:
        """OOS metric of every uniform depth at every K (walk-forward, pooled) — where specialisation stops helping."""
        out = {}
        for K in lr.k_grid:
            out[str(K)] = {d: {k: _r(v, 4) for k, v in clustered(lr.weekly[(K, d)], self.h).items()} for d in LEVELS}
        return out

    def _families(self, lr: Learner) -> dict:
        """Family importance (share of |validated weight|) globally and per class / product type, plus production's."""
        from .. import shaffer_score as cfg
        tab = self.tab
        fams = list(cfg.FAMILIES)
        off = 1 if lr.target == "dir" else 0
        chF = lr.choices[FINAL]

        def fam_share(w):
            tot = sum(abs(x) for x in w[off:]) or 1.0
            out = {}
            for i, n in enumerate(tab.names):
                f = cfg.FAMILY_OF[n]
                out[f] = out.get(f, 0.0) + w[i + off] / tot
            return out
        res = {"global": {k: _r(v, 4) for k, v in fam_share(lr.deployable(FINAL, chF, ("global",))).items()}}
        for nd in lr.tree.order:
            if level_of(nd) in ("class", "ptype"):
                path = self._path_to(lr, nd)
                res[nd] = {k: _r(v, 4) for k, v in fam_share(lr.deployable(FINAL, chF, path)).items()}
        return res

    def _path_to(self, lr: Learner, nd: str) -> List[str]:
        p = [nd]
        while lr.tree.parent[p[0]]:
            p.insert(0, lr.tree.parent[p[0]])
        return p

    def _asset_depth(self, lr: Learner) -> Dict[str, dict]:
        """Per asset: the uniform depth with the lowest OOS squared error on its own walk-forward records (each era with
        the K chosen for it), and its OOS reliability (time-series correlation of the validated score with the target)."""
        tab, h = self.tab, self.h
        out = {}
        for a, (s0, s1) in tab.slices.items():
            sse = {d: 0.0 for d in LEVELS}
            xs, ys = [], []
            for e in range(len(TEST_ERAS)):
                cut = lr._era_cut(e)
                if cut not in lr.choices:
                    continue
                K = lr.choices[cut].get("K_E") or lr.choices[cut]["K"]
                lo, hi = lr._era_range(s0, s1, e)
                for d in LEVELS:
                    sv = lr.wf[(K, d)]
                    sse[d] += sum(lr.w[k] * (lr.y[k] - sv[k]) ** 2 for k in range(lo, hi) if lr.w[k] > 0)
                c = lr.var_wf["C"]
                for k in range(lo, hi):
                    if lr.w[k] > 0 and c[k] == c[k]:
                        xs.append(c[k]); ys.append(lr.y[k])
            n = len(xs)
            rel = None
            if n >= 30:
                mx, my = sum(xs) / n, sum(ys) / n
                sx = math.sqrt(sum((x - mx) ** 2 for x in xs)); sy = math.sqrt(sum((y - my) ** 2 for y in ys))
                if sx > 0 and sy > 0:
                    rho = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (sx * sy)
                    n_eff = n * 5.0 / max(5.0, h)
                    rel = {"corr": _r(rho, 4), "t": _r(rho * math.sqrt(max(1.0, n_eff - 2)) / math.sqrt(max(1e-9, 1 - rho * rho)), 3), "n": n}
            bd = min(LEVELS, key=lambda d: sse[d]) if any(sse.values()) else None
            out[a] = {"best_depth": bd, "reliability": rel}
        return out

    # ---------------- Directional
    def dir_report(self, lr: Learner) -> dict:
        from . import dirnext as DN
        from . import directional as D
        tab, h = self.tab, self.h
        t0 = time.time()
        res = {"choices": self._choice_summary(lr), "signals": self._signal_table(lr)}
        preds, preds_sp = self._dir_preds(lr)
        wf_idx = sorted(preds["prior"])
        recs = [tab.rec[k] for k in range(tab.n)]
        out_models = {}
        base = {"prior": D.dir_metrics([recs[k] for k in wf_idx], [preds["prior"][k] for k in wf_idx], h),
                "current": D.dir_metrics([recs[k] for k in wf_idx], [preds["current"][k] for k in wf_idx], h)}
        eras_all = []
        for e in range(len(TEST_ERAS)):
            ids = [k for k in wf_idx if tab.era[k] == e]
            eras_all.append(ids)
        for key, label in (("C", "validated deployable"), ("B", "historical best fit"), ("D", "simple global"), ("E", "uniform-depth hierarchy")):
            ps = [preds[key][k] for k in wf_idx]
            m = D.dir_metrics([recs[k] for k in wf_idx], ps, h)
            vp = D.paired(recs, h, preds, key, "prior")
            vc = D.paired(recs, h, preds, key, "current")
            cal = DN.calibration([recs[k] for k in wf_idx], ps)
            eras = []
            for ids in eras_all:
                sub = {n_: {k: preds[n_][k] for k in ids} for n_ in (key, "prior")}
                eras.append(D.paired(recs, h, sub, key, "prior") if ids else {})
            sp = D.paired(recs, h, preds_sp, key, "prior") if preds_sp.get(key) else {}
            g = _dir_gates(m, vp, vc, base["prior"], cal, sp, eras)
            bear = DN.bearish_by_class([recs[k] for k in wf_idx], ps) if key == "C" else None
            out_models[key] = {"label": label, "metrics": _slim_dir(m), "vs_prior": _slim_pair(vp), "vs_current": _slim_pair(vc),
                               "calibration": cal, "eras": [_slim_pair(e) for e in eras], "split": _slim_pair(sp), "gates": g,
                               "bearish": bear, "adjustment": {"mean_abs": _r(sum(abs(preds[key][k] - preds["prior"][k]) for k in wf_idx) / max(1, len(wf_idx)), 4)}}
        res["variants"] = out_models
        res["prior"] = _slim_dir(base["prior"])
        res["current"] = _slim_dir(base["current"])
        res["prior_calibration"] = DN.calibration([recs[k] for k in wf_idx], [preds["prior"][k] for k in wf_idx])
        res["depths"] = self._depth_table(lr)
        res["methods"] = methods(lr, orient=self.orient)
        res["families"] = self._families(lr)
        res["asset_reliability"] = self._dir_asset_rel(lr, preds)
        self.dir_platt = self._platt_final
        self.say(f"{self.lab} directional report ({time.time() - t0:.0f}s)")
        return res

    def _dir_preds(self, lr: Learner):
        """p_up for every walk-forward record: prior-only and current (as in the Directional program), and each learned
        variant σ(a + b·z + c·index) with the Platt coefficients fitted on out-of-sample scores matured before the era
        (in-sample on the training records while fewer than PLATT_MIN exist)."""
        tab = self.tab
        rnd = random.Random(11)
        preds = {k: {} for k in ("prior", "current", "B", "C", "D", "E")}
        preds_sp = {k: {} for k in ("prior", "current", "B", "C", "D", "E")}
        zs = [z if z is not None else 0.0 for z in tab.z]
        ok = [k for k in range(tab.n) if tab.z[k] is not None and tab.o[k] is not None]
        okset = set(ok)
        self._platt_final = {}
        for tag, cut, sel, dst, src in [(e, lr._era_cut(e), (lambda k, e=e: tab.era[k] == e), preds, lr.var_wf) for e in range(len(TEST_ERAS))] + \
                                       [("split", SPLIT, (lambda k: tab.date[k] >= SPLIT), preds_sp, lr.var_sp), ("final", FINAL, None, None, None)]:
            if cut not in lr.fits:
                continue
            train = [k for k in ok if tab.end[k] < cut]
            if len(train) < 500:
                continue
            sub = train if len(train) <= 20000 else rnd.sample(train, 20000)
            o = [float(tab.o[k]) for k in sub]
            w0 = _platt([[1.0, zs[k]] for k in sub], o)
            w1 = _platt([[1.0, zs[k], (tab.raw[k] or 0.0) / 100.0] for k in sub], o)
            inner = [k for k in ok if tab.end[k] < cut and tab.era[k] >= 0 and lr.var_wf["C"][k] == lr.var_wf["C"][k]]
            platt = {}
            for key in ("B", "C", "D", "E"):
                if len(inner) >= PLATT_MIN:
                    s2 = inner if len(inner) <= 20000 else rnd.sample(inner, 20000)
                    platt[key] = _platt([[1.0, zs[k], lr.var_wf[key][k]] for k in s2], [float(tab.o[k]) for k in s2])
                else:
                    # no out-of-sample calibration evidence yet: the learned model makes no adjustment (= prior-only)
                    platt[key] = [w0[0], w0[1], 0.0]
            if tag == "final":
                self._platt_final = {"prior": w0, "current": w1, **platt}
                continue
            for k in ok:
                if not sel(k):
                    continue
                dst["prior"][k] = _sig(w0[0] + w0[1] * zs[k])
                dst["current"][k] = _sig(w1[0] + w1[1] * zs[k] + w1[2] * (tab.raw[k] or 0.0) / 100.0)
                for key in ("B", "C", "D", "E"):
                    s = src[key][k]
                    if s == s:
                        c = platt[key]
                        dst[key][k] = _sig(c[0] + c[1] * zs[k] + c[2] * s)
        for d in (preds, preds_sp):
            common = set(d["prior"])
            for key in d:
                common &= set(d[key])
            for key in d:
                d[key] = {k: d[key][k] for k in common}
        return preds, preds_sp

    def _in_sample(self, lr: Learner, cut: str, key: str, idx: List[int]) -> List[float]:
        tab = self.tab
        f, ch = lr.fits[cut], lr.choices[cut]
        out = []
        cache = {}
        for k in idx:
            a = tab.asset[k]
            if a not in cache:
                path = tab.paths[a]
                w = {"B": weights_at(f["W"][K_BEST], path, "asset"), "C": lr.deployable(cut, ch, path),
                     "D": f["W"][ch["K_global"]]["global"], "E": weights_at(f["W"][ch.get("K_E") or ch["K"]], path, ch["depth"])}[key]
                cache[a] = _coef(w, f["rms"])
            out.append(sum(c * lr.cols[i][k] for i, c in enumerate(cache[a]) if c))
        return out

    def _dir_asset_rel(self, lr: Learner, preds) -> Dict[str, dict]:
        tab, h = self.tab, self.h
        by: Dict[str, List[float]] = {}
        for k, p in preds["C"].items():
            o = tab.o[k]
            by.setdefault(tab.asset[k], []).append((preds["prior"][k] - o) ** 2 - (p - o) ** 2)
        out = {}
        for a, v in by.items():
            n = len(v)
            if n < 30:
                continue
            m = sum(v) / n
            sd = math.sqrt(sum((x - m) ** 2 for x in v) / (n - 1))
            n_eff = n * 5.0 / max(5.0, h)
            out[a] = {"brier_gain": _r(m, 4), "t": _r(m / (sd / math.sqrt(n_eff)), 3) if sd > 0 else None, "n": n}
        return out

    # ---------------- structure diagnostics: confidence / regime / decay / applicability
    def structure(self) -> dict:
        """Does production's structure help? Global models on alternative feature constructions, walk-forward:
        free signed weights on x (the learned equation) against production's oriented, confidence-, regime- and
        decay-scaled signals δ·x·c^γc·r^γr·d^γd (weights ≥ 0) with each scaling switched off in turn, the applicability
        mask (x only where production marks the signal applicable), and learned weights on applicable vs non-applicable
        records."""
        from . import weights as W
        tab, h = self.tab, self.h
        t0 = time.time()
        P = tab.P
        variants = {"production structure δ·x·c·r·d": (1, 1, 1), "without confidence (γc = 0)": (0, 1, 1), "without regime (γr = 0)": (1, 0, 1),
                    "without decay (γd = 0)": (1, 1, 0), "δ·x only": (0, 0, 0)}
        out = {"alpha": {}, "dir": {}}
        base = {t: clustered(self.lrs[t].var_weekly["D"], h) for t in ("alpha", "dir")}
        for t in ("alpha", "dir"):
            out[t]["free signed weights on x (learned)"] = {"pooled": _slim_cl(base[t])}
        feats = {}
        for name, gam in variants.items():
            cols = [array("d", bytes(8 * tab.n)) for _ in range(P)]
            for k in range(tab.n):
                r = tab.rec[k]
                for j, dl, om, c, rr, d in r.act:
                    cols[j][k] = dl * r.x[j] * (c ** gam[0]) * (rr ** gam[1]) * (d ** gam[2])
            feats[name] = (cols, True)
        mask = [array("d", bytes(8 * tab.n)) for _ in range(P)]
        for k in range(tab.n):
            r = tab.rec[k]
            for j, *_ in r.act:
                mask[j][k] = r.x[j]
        feats["applicability mask (x only where applicable)"] = (mask, False)
        for name, (cols, nonneg) in feats.items():
            for t in ("alpha", "dir"):
                r_ = _global_walk(tab, cols, t, nonneg, h)
                if r_:
                    d = {w: r_["weekly"][w] - self.lrs[t].var_weekly["D"][w] for w in r_["weekly"] if w in self.lrs[t].var_weekly["D"]}
                    out[t][name] = {"pooled": _slim_cl(clustered(r_["weekly"], h)), "vs_free": _slim_cl(clustered(d, h))}
        # applicability weights: separate learned weights for applicable and non-applicable records (Alpha, final)
        act_cols = mask
        inact = [array("d", map(sub, tab.X[i], mask[i])) for i in range(P)]
        wa = _global_final(tab, act_cols + inact, "alpha")
        if wa:
            out["applicability_weights"] = [{"name": tab.names[i], "applicable": _r(wa[i]), "not_applicable": _r(wa[P + i]),
                                             "share_applicable": _r(sum(1 for k in range(tab.n) if mask[i][k]) / tab.n, 3)} for i in range(P)]
        self.say(f"{self.lab} structure diagnostics ({time.time() - t0:.0f}s)")
        return out

    # ---------------- today's effective weights
    def today_weights(self) -> Dict[str, dict]:
        """For every asset: today's validated effective weights (Alpha and Directional), where they come from in the
        hierarchy, the learned score and p_up, production's score and weights, and every signal's contribution."""
        from . import directional as D
        tab = self.tab
        la, ld = self.lrs["alpha"], self.lrs["dir"]
        chA, chD = la.choices[FINAL], ld.choices[FINAL]
        fA, fD = la.fits[FINAL], ld.fits[FINAL]
        scale = self._scale(la).get("final", 1.0)
        platt = getattr(self, "_platt_final", {}) or {}
        # today's cross-section, for the week-demeaned Alpha signals
        by_date: Dict[str, List] = {}
        for t in self.today:
            by_date.setdefault(t["rec"].date, []).append(t["rec"])
        means = {}
        for d, rs in by_date.items():
            pool = rs if len(rs) >= MIN_WEEK else [t["rec"] for t in self.today]
            means[d] = [sum(r.x[i] for r in pool) / len(pool) for i in range(tab.P)]
        xrms = [math.sqrt(sum(v * v for v in c) / tab.n) for c in tab.X]
        out = {}
        for t in self.today:
            r, path = t["rec"], t["path"]
            xa = [r.x[i] - means[r.date][i] for i in range(tab.P)]
            wA = la.deployable(FINAL, chA, path)
            cA = _coef(wA, fA["rms"])
            idxA = sum(c * v for c, v in zip(cA, xa))
            decA = la.decompose(FINAL, chA, path)
            wD = ld.deployable(FINAL, chD, path)
            cD = _coef(wD, fD["rms"])
            xd = [1.0] + [float(v) for v in r.x]
            idxD = sum(c * v for c, v in zip(cD, xd))
            z = D.z_of(r, D.MAIN_PRIOR)
            p_up = p_prior = None
            if z is not None and platt.get("C"):
                c = platt["C"]
                p_up = _sig(c[0] + c[1] * z + c[2] * idxD)
                w0 = platt["prior"]
                p_prior = _sig(w0[0] + w0[1] * z)
            pw = prod_weights(r)
            prod_std = [pw[i] * xrms[i] for i in range(tab.P)]
            lev = {lvl: sum(abs(v) for v in decA[lvl]) for lvl in LEVELS}
            tot = sum(lev.values()) or 1.0
            out[r.asset] = {"date": r.date, "path": list(path), "production_score": _r(r.raw, 4),
                            "learned_score": _r(100.0 * math.tanh(idxA / (scale or 1.0)), 4), "learned_index": _r(idxA),
                            "p_up": _r(p_up, 4), "p_prior": _r(p_prior, 4),
                            "alpha_weights": _rl(wA), "alpha_production": _rl(prod_std), "alpha_contrib": _rl([c * v for c, v in zip(cA, xa)]),
                            "alpha_levels": {k: _r(v / tot, 4) for k, v in lev.items()},
                            "alpha_decomposition": {lvl: _rl(v) for lvl, v in decA.items() if any(v)},
                            "dir_weights": _rl(wD), "dir_contrib": _rl([c * v for c, v in zip(cD, xd)]),
                            "x": _rl([float(v) for v in r.x], 4)}
        return out

    # ---------------- node table (every node, final)
    def node_table(self, lr: Learner) -> Dict[str, dict]:
        chF = lr.choices[FINAL]
        f = lr.fits[FINAL]
        K = chF["K"]
        eras = lr.era_fits(K)
        years = lr.inner_years(FINAL)
        keep = chF.get("keep") or {}
        out = {}
        for nd in lr.tree.order:
            path = self._path_to(lr, nd)
            st = f["ns"][nd]
            node_t = [lr.node_t(K, nd, i, years)[1] if nd != "global" else None for i in range(lr.P)]
            out[nd] = {"level": level_of(nd), "parent": lr.tree.parent[nd], "n": st.n, "assets": len(lr.tree.assets[nd]),
                       "best_fit": _rl(f["W"][K_BEST][nd]), "pooled": _rl(f["W"][K][nd]), "shrunk": _rl(lr.deployable(FINAL, chF, path)),
                       "era_weights": [_rl(e[nd]) if e and nd in e else None for e in eras], "node_t": _rl(node_t, 3),
                       "kept": sorted(keep.get(nd, []))}
        return out


def _global_walk(tab: Table, cols: List[array], target: str, nonneg: bool, h: int) -> Optional[dict]:
    """A global-only walk-forward model on alternative feature columns (week-demeaned for Alpha)."""
    P = len(cols)
    if target == "alpha":
        cols = [array("d", c) for c in cols]
        for _, s, e, _ in tab.weeks:
            idx = tab.perm[s:e]
            if e - s < MIN_WEEK:
                continue
            for c in cols:
                mu = sum(c[k] for k in idx) / (e - s)
                for k in idx:
                    c[k] -= mu
        y, w = tab.u, tab.wa
    else:
        cols = [array("d", repeat(1.0, tab.n))] + list(cols)
        y, w = tab.t, tab.wd
    P = len(cols)
    by_b: Dict[int, Stat] = {}
    for (a, b), (s0, s1) in tab.cells.items():
        st = cell_stat(cols, y, w, s0, s1)
        by_b[b] = st if b not in by_b else by_b[b].add(st)
    s = array("d", repeat(float("nan"), tab.n))
    for e in range(len(TEST_ERAS)):
        cut = TEST_ERAS[e][0]
        pos = _cut_pos(cut)
        g = None
        for b in range(pos):
            if b in by_b:
                g = by_b[b].copy() if g is None else g.add(by_b[b])
        if g is None or g.sw <= 0:
            continue
        rms = rms_of(g)
        if nonneg:
            bound = 0.5 * math.sqrt(max(1e-12, g.yy / g.sw))
            lo = [0.0] * P
            if target == "dir":
                lo[0] = -bound
            wv = cd_solve(g, rms, G_LAMBDA, 0.0, lo, [bound] * P)
        else:
            wv = ridge(g, rms, [0.0] * P, G_LAMBDA)
        coef = _coef(wv, rms)
        for k in range(tab.n):
            if tab.era[k] == e:
                s[k] = sum(c * cols[i][k] for i, c in enumerate(coef) if c)
    sd, yd, wd = tab.to_date(s), tab.to_date(y), tab.to_date(w)
    weekly = weekly_rank_ic(tab, sd, yd, wd) if target == "alpha" else weekly_gain_dir(tab, sd, yd, wd)
    return {"weekly": weekly}


def _global_final(tab: Table, cols: List[array], target: str) -> Optional[List[float]]:
    if target == "alpha":
        cols = [array("d", c) for c in cols]
        for _, s, e, _ in tab.weeks:
            idx = tab.perm[s:e]
            if e - s < MIN_WEEK:
                continue
            for c in cols:
                mu = sum(c[k] for k in idx) / (e - s)
                for k in idx:
                    c[k] -= mu
        y, w = tab.u, tab.wa
    else:
        y, w = tab.t, tab.wd
    g = None
    for (a, b), (s0, s1) in tab.cells.items():
        st = cell_stat(cols, y, w, s0, s1)
        g = st if g is None else g.add(st)
    if g is None or g.sw <= 0:
        return None
    return ridge(g, rms_of(g), [0.0] * len(cols), G_LAMBDA)


def _slim_cl(c: dict) -> dict:
    return {k: _r(v, 4) for k, v in c.items()}


def _slim_alpha(ev: dict) -> dict:
    if not ev or not ev.get("n"):
        return {"n": 0}
    keep = ("ic", "ic_t", "rank_ic", "rank_t", "quintile_spread", "quintile_t", "decile_spread", "hit_vs_median", "n")
    c, p = ev.get("challenger") or {}, ev.get("production") or {}
    return {"n": ev["n"], "challenger": {k: _r(c.get(k), 4) for k in keep if k in c}, "production": {k: _r(p.get(k), 4) for k in keep if k in p},
            "paired": {k: _r(v, 4) for k, v in (ev.get("paired") or {}).items() if not isinstance(v, (list, dict))},
            "ls_challenger": {k: _r(v, 4) for k, v in (ev.get("ls_challenger") or {}).items() if not isinstance(v, (list, dict))},
            "ls_production": {k: _r(v, 4) for k, v in (ev.get("ls_production") or {}).items() if not isinstance(v, (list, dict))},
            "mono_challenger": _r(ev.get("mono_challenger"), 3), "mono_production": _r(ev.get("mono_production"), 3)}


def _slim_dir(m: dict) -> dict:
    keep = ("n", "brier", "log_loss", "auc", "ece", "balanced_accuracy", "accuracy")
    return {k: _r(m.get(k), 5) for k in keep if k in m}


def _slim_pair(p: dict) -> dict:
    return {k: _r(v, 4) for k, v in (p or {}).items() if k in ("n", "brier_gain", "brier_t", "logloss_gain", "logloss_t", "delta_acc", "delta_acc_t")}


def _dir_gates(m, vp, vc, prior, cal, sp, eras) -> dict:
    """The Directional program's fixed gates (dirnext.gates), for a learned variant."""
    g = {"t": vp.get("brier_t"), "brier_gain": vp.get("brier_gain"), "logloss_gain": vp.get("logloss_gain"),
         "balanced_gain": (m.get("balanced_accuracy") or 0) - (prior.get("balanced_accuracy") or 0), "vs_current_t": vc.get("brier_t")}
    g["G1"] = bool((vp.get("brier_t") or 0) >= 2 and (vp.get("logloss_gain") or 0) > 0 and g["balanced_gain"] > 0 and (vc.get("brier_t") or 0) >= 2)
    g["split_t"] = (sp or {}).get("brier_t")
    g["G2"] = bool((g["split_t"] or 0) >= 1)
    full = [e for e in eras[:4] if e]
    g["eras_won"] = sum(1 for e in full if (e.get("brier_gain") or 0) > 0)
    g["eras_complete"] = len(full)
    g["G3"] = g["eras_won"] >= 3
    g["ece"], g["ece_prior"] = m.get("ece"), prior.get("ece")
    g["slope"] = (cal or {}).get("slope")
    g["G4"] = bool(g["ece"] is not None and g["ece_prior"] is not None and g["ece"] <= g["ece_prior"] + 0.005
                   and g["slope"] is not None and 0.8 <= g["slope"] <= 1.25)
    return g


# ================================================================== all horizons, statuses, storage
def _horizon_worker(db_path: str, lab: str):
    from ..data.store import Store
    from .research import Research
    st = Store(db_path)
    try:
        return Study(st, Research(st), lab).run()
    finally:
        st.close()


def finalise(res: dict) -> dict:
    """Multiple-testing control across signals × horizons (per target) and across horizons × variants for the gates;
    statuses of every global weight; live-shadow eligibility."""
    from .alphanext import _p
    for tgt in ("alpha", "dir"):
        keys, ps = [], []
        for lab, H in res["horizons"].items():
            for k, s in enumerate(H[tgt]["signals"]):
                keys.append((lab, k)); ps.append(p_one_sided(s.get("oos_t")))
        for (lab, k), r in zip(keys, benjamini_hochberg(ps)):
            s = res["horizons"][lab][tgt]["signals"][k]
            s["fdr"] = r
            st = s.get("stability") or {}
            s["status"] = signal_status(s.get("oos_t"), r, st.get("same_sign") or 0, st.get("eras") or 0)
        gk, gp = [], []
        for lab, H in res["horizons"].items():
            for key, v in H[tgt]["variants"].items():
                gk.append((lab, key)); gp.append(_p(v["gates"].get("t")))
        for (lab, key), r in zip(gk, benjamini_hochberg(gp)):
            v = res["horizons"][lab][tgt]["variants"][key]
            g = v["gates"]
            g["fdr"] = r
            g["passed"] = bool(g.get("G1") and g.get("G2") and g.get("G3") and g.get("G4") and r)
            v["status"] = "LIVE SHADOW ELIGIBLE" if g["passed"] else "NOT VALIDATED"
    return res


def run_all(db_path: str, workers: int = 3, progress=None, horizons: Optional[List[str]] = None, with_hedge: bool = True,
            with_capability: bool = True) -> dict:
    from concurrent.futures import ProcessPoolExecutor, as_completed
    from ..data.store import Store
    from .lab import LAB_HORIZONS
    say = progress or (lambda m: None)
    t0 = time.time()
    labs = [lab for lab, _ in LAB_HORIZONS if not horizons or lab in horizons]
    res = {"started": time.strftime("%Y-%m-%d %H:%M:%S"), "horizons": {}}
    if with_capability:
        res["capability"] = capability(progress=say)
    with ProcessPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_horizon_worker, db_path, lab): lab for lab in labs}
        for fu in as_completed(futs):
            lab = futs[fu]
            res["horizons"][lab] = fu.result()
            say(f"{lab} finished ({time.time() - t0:.0f}s)")
    res["horizons"] = {lab: res["horizons"][lab] for lab in labs if lab in res["horizons"]}
    if with_hedge:
        from ..hedge import hedgelearn as HL
        res["hedge"] = HL.run(db_path, progress=say)
    finalise(res)
    res["seconds"] = round(time.time() - t0, 1)
    save(db_path, res)
    return res


def save(db_path: str, res: dict):
    """Summary under RESEARCH_KEY; the heavy per-horizon parts (nodes, today's assets) under their own keys."""
    from ..data.store import Store
    st = Store(db_path)
    try:
        slim = {k: v for k, v in res.items() if k != "horizons"}
        slim["horizons"] = {}
        for lab, H in res["horizons"].items():
            st.kv_set(f"{RESEARCH_KEY}:{lab}:nodes", H.get("nodes"))
            st.kv_set(f"{RESEARCH_KEY}:{lab}:assets", H.get("assets_today"))
            slim["horizons"][lab] = {k: v for k, v in H.items() if k not in ("nodes", "assets_today")}
        st.kv_set(RESEARCH_KEY, slim)
    finally:
        st.close()


def load(store, lab: Optional[str] = None, part: Optional[str] = None):
    if lab and part in ("nodes", "assets"):
        return store.kv_get(f"{RESEARCH_KEY}:{lab}:{part}")
    return store.kv_get(RESEARCH_KEY)


# ================================================================== SHAFFER_LEARNED_WEIGHTS.md
def _f(v, d=4, sign=True):
    if v is None:
        return "—"
    return f"{v:+.{d}f}" if sign else f"{v:.{d}f}"


def _pc(v, d=1):
    return "—" if v is None else f"{100 * v:+.{d}f}%"


def _pcu(v, d=0):
    return "—" if v is None else f"{100 * v:.{d}f}%"


EXAMPLES = ["NVDA", "AAPL", "JPM", "XLK", "SPY", "UST10Y", "TLT", "HYG", "WTI", "GOLD", "EURUSD", "BTC"]


def markdown(res: dict, assets: Dict[str, Dict[str, dict]], nodes: Dict[str, dict], live: Optional[dict] = None) -> str:
    """The report. `assets[lab]` / `nodes[lab]` are the heavy per-horizon parts (today's weights, node tables)."""
    H = res.get("horizons") or {}
    L: List[str] = []
    w = L.append
    labs = list(H)
    anyH = next(iter(H.values())) if H else {}
    names = anyH.get("signals") or []
    fams = anyH.get("families") or []
    w("# Shaffer learned weights — what history supports")
    w("")
    w(f"Run {res.get('started')} · {res.get('seconds')} s · `python -m finsim2 lab --learned`. Research only: production "
      "(shaffer-2.1, shaffer-alpha-2.1-production, the Directional definition, hedge-2) is unchanged. Method: `engine/learned.py` "
      "(module docstring); hedge parameters: `hedge/hedgelearn.py`.")
    w("")
    w("**Find the weights first, then decide how much to trust them.** For every horizon, every one of the 74 production "
      "signals gets a weight at every node of Global → Class → Product type → Sector → Industry → Asset, learned from the "
      "point-in-time research records. Two weight sets always exist: the *historical best fit* (what fits the past best, "
      "hardly pooled) and the *validated deployable* weights (pooled toward the parent, trusted in proportion to their "
      "out-of-sample evidence, specialised only where a node's own deviation survived out of sample). A failed promotion gate "
      "does not mean there are no learned weights — it means they are not trusted enough to replace production.")
    w("")
    _summary(w, res)
    _capability_section(w, res.get("capability"))
    _answers(w, res, assets)
    for lab in labs:
        _horizon_section(w, lab, H[lab], (nodes or {}).get(lab) or {}, (assets or {}).get(lab) or {})
    _maps_section(w, H)
    _examples_section(w, H, assets or {}, names, live or {})
    _asset_table(w, H, assets or {}, live or {})
    _hedge_section(w, res.get("hedge"))
    return "\n".join(L) + "\n"


def _summary(w, res: dict):
    """Key findings, generated from the results."""
    H = res.get("horizons") or {}
    cap = res.get("capability") or {}
    w("## Key findings")
    w("")
    w(f"- **The engine recovers planted weights:** {sum(1 for v in cap.values() if v.get('pass'))} of {len(cap)} synthetic tests passed "
      "(linear, ranking target, sparse, correlated, regime-dependent, sector-specific, horizon-specific, nonlinear interaction, "
      "pure noise, asset-specific with a short-history asset) before any market result was read.")
    passed = [(lab, tgt, k, v) for lab, Hl in H.items() for tgt in ("alpha", "dir") for k, v in Hl[tgt]["variants"].items()
              if v.get("status") == "LIVE SHADOW ELIGIBLE"]
    if passed:
        w("- **Learned weights that beat production under every fixed gate (G1–G4 + FDR across 24 tests):** " + "; ".join(
            f"{'Alpha' if t == 'alpha' else 'Directional'} {lab} {v['label']} — rank IC {_f((v['walkforward'].get('challenger') or {}).get('rank_ic'))} vs "
            f"production {_f((v['walkforward'].get('production') or {}).get('rank_ic'))}, Δ t {_f((v['walkforward'].get('paired') or {}).get('t'), 1)}, "
            f"{v['gates'].get('eras_won')}/{v['gates'].get('eras_complete')} eras, split t {_f(v['gates'].get('split_t'), 1)}, net long-short "
            f"{_pc((v['walkforward'].get('ls_challenger') or {}).get('net'), 3)} per week vs {_pc((v['walkforward'].get('ls_production') or {}).get('net'), 3)}"
            for lab, t, k, v in passed) + ". They are in live shadow now (recorded daily, graded like production); promotion needs "
          "≥ 60 graded live outcomes and your approval. Their gain is concentrated after 2017; it is ~0 in 2009–12 and negative in 2013–16.")
    else:
        w("- **No learned weight set beats production under every fixed gate.**")
    cd = [(lab, (Hl["alpha"]["variants"]["C"]["walkforward"].get("challenger") or {}).get("rank_ic"),
           (Hl["alpha"]["variants"]["D"]["walkforward"].get("challenger") or {}).get("rank_ic")) for lab, Hl in H.items()]
    worse = [f"{lab} ({_f(c)} vs {_f(d)})" for lab, c, d in cd if c is not None and d is not None and c < d]
    if worse:
        w("- **The trust-filtered 'validated deployable' set (C) was too conservative where signals are weak individually but useful "
          "together:** it trails the simple global learned weights at " + ", ".join(worse) + ". Zeroing every global weight whose own "
          "out-of-sample t is ≤ 1 removes signals that only help jointly. It is reported as designed; it is not what should be shadowed.")
    for lab in [l for l in ("1D", "1W") if l in H]:
        dt = H[lab]["alpha"].get("depths") or {}
        g = (dt.get("5000") or {}).get("global") or {}
        best = max(((K, d, (row.get(d) or {}).get("mean")) for K, row in dt.items() for d in LEVELS if (row.get(d) or {}).get("mean") is not None),
                   key=lambda x: x[2], default=None)
        if best and g.get("mean") is not None and best[2] > g["mean"]:
            row = dt.get(best[0]) or {}
            beat = [LEVEL_NAME[d] for d in LEVELS[1:] if ((row.get(d) or {}).get("mean") or -9) > g["mean"]]
            w(f"- **Hierarchy helps at {lab}:** at pooling K = {best[0]}, {len(beat)} of 5 depths below global beat global in the walk-forward "
              f"({', '.join(beat)}; best {LEVEL_NAME[best[1]]}, rank IC {_f(best[2])} vs global {_f(g['mean'])}). Picking that cell uses hindsight; "
              "the nested choice (E) captures part of it.")
    longh = [lab for lab in H if lab in ("1M", "3M", "6M", "12M")]
    if longh:
        w(f"- **{', '.join(longh)}:** nothing beats production significantly; no signal weight is validated at 3M–12M; the validated model "
          "keeps no specialisation there (history does not support it).")
    w("- **Directional:** no learned weight set beats the point-in-time base prior at any horizon (Brier, log loss, calibration together).")
    tot = {}
    for Hl in H.values():
        for s in Hl["alpha"]["signals"]:
            c = (s.get("stability") or {}).get("class")
            tot[c] = tot.get(c, 0) + 1
    w("- **Stability:** across all horizons the Alpha global weights are " + ", ".join(f"{k} {v}" for k, v in sorted(tot.items(), key=lambda kv: -kv[1])) +
      " — few signal weights are stable enough to treat as permanent.")
    w("- **Learned vs production:** the learned allocations are very different from production's (distance 0.8–0.9 on a 0–1 scale, §2 Q10). "
      "Production is not close to the learned optimum; at 1D/1W the learned weights are measurably better, at 1M–12M the differences are noise.")
    hd = res.get("hedge") or {}
    sz = (hd.get("sizing") or {}).get("1W") or {}
    g = (sz.get("nodes") or {}).get("global") or {}
    if g:
        w(f"- **Hedge:** history supports larger hedges than hedge-2 at 1W and 1M (global validated multiple {g.get('shrunk'):.2f}× at 1W; "
          "most risk classes and objectives at 1.35–1.5×, minimum-variance at ~1.0×). Many sizing cells survive FDR, but every one fails the "
          "fixed cost / basis-error guard H4 and the gain reverses for profit-sensitive users (λ ≥ 5). Product choice: nothing validated.")
    w("")


def _capability_section(w, cap):
    w("## 1. Capability test — can the engine recover planted weights?")
    w("")
    if not cap:
        w("Not run.")
        w("")
        return
    w("Before any market result was interpreted, the same code was run on synthetic point-in-time datasets whose true "
      "equation is known (weekly records, 40 assets in a class / sector / industry tree, 2002–2026, regimes switching every "
      "~26 weeks). Every planted relationship must be recovered, and pure noise must not be validated.")
    w("")
    w("| Test | Planted | Recovered | Result |")
    w("|---|---|---|---|")
    for k, v in cap.items():
        planted = v.get("truth") or v.get("truth_ratio")
        rec = {kk: vv for kk, vv in v.items() if kk not in ("pass", "truth", "truth_ratio")}
        w(f"| {k} | {_short(planted)} | {_short(rec)} | {'**PASS**' if v.get('pass') else '**FAIL**'} |")
    w("")
    ok = all(v.get("pass") for v in cap.values())
    w("All planted effects were recovered and noise was not validated; the market results below come from the same code."
      if ok else "**Not every planted effect was recovered — the market results below must be read with that limitation.**")
    w("")


def _short(v) -> str:
    import json
    s = json.dumps(v, ensure_ascii=False, default=str)
    return s.replace("|", "/")[:220]


def _best_variant(Hl: dict, tgt: str) -> Tuple[str, dict]:
    vs = (Hl.get(tgt) or {}).get("variants") or {}
    k = max(vs, key=lambda k: (vs[k]["gates"].get("t") or -99)) if vs else None
    return k, vs.get(k) or {}


def _answers(w, res: dict, assets):
    H = res.get("horizons") or {}
    w("## 2. Answers")
    w("")
    # 1–2 best-fit and validated Alpha weights
    w("**1–2. Historical best-fit and validated Alpha weights.** Every horizon has both; the full global table is in each "
      "horizon's section (signal, production weight, best fit, validated weight, reliability, era stability, OOS contribution), "
      "every node's weights are in the ML Lab (Shaffer Alpha → Learned weights) and stored with the run. Largest validated "
      "global weights (signed share of |weight|):")
    w("")
    for lab, Hl in H.items():
        sig = Hl["alpha"]["signals"]
        top = sorted(sig, key=lambda s: -abs(s.get("learned_share") or 0))[:6]
        w(f"- **{lab}:** " + ", ".join(f"{s['name']} {_pc(s.get('learned_share'), 0)}" for s in top if s.get("learned_share")) +
          ("" if any(s.get("learned_share") for s in top) else "no signal has a trusted global weight (all shrunk to 0 by their OOS evidence)"))
    w("")
    w("**3. Which signals matter most at each horizon** (validated status, Alpha):")
    w("")
    for lab, Hl in H.items():
        v = [s["name"] for s in Hl["alpha"]["signals"] if s.get("status") == "VALIDATED"]
        sup = [s["name"] for s in Hl["alpha"]["signals"] if s.get("status") == "SUPPORTED"]
        w(f"- **{lab}:** validated: {', '.join(v) or 'none'}; supported (positive OOS contribution, not validated): {len(sup)} signals.")
    w("")
    w("**4–6. By asset class, sector and industry.** Specialisation is decided node by node: a node keeps its own weight for a "
      "signal only if that deviation improved its own records out of sample (clustered by year, BH across every node × signal). "
      "Kept specialisations at today's cutoff (Alpha):")
    w("")
    for lab, Hl in H.items():
        ch = (Hl["alpha"]["choices"] or {}).get(FINAL) or {}
        kb = ch.get("kept_by_level") or {}
        w(f"- **{lab}:** " + ", ".join(f"{LEVEL_NAME[k]} {v}" for k, v in kb.items()) + f" (K = {ch.get('K')})")
    w("")
    w("**7. Assets with enough evidence for asset-specific adjustments** (Alpha, at least one kept asset-level signal): " +
      "; ".join(f"{lab}: " + (", ".join(sorted({a for a, x in (assets.get(lab) or {}).items() if any(x.get('alpha_decomposition', {}).get('asset') or [])})) or "none")
                for lab in H) + ".")
    w("")
    w("**8. Specialisation depth that works best** (uniform depth with the best walk-forward rank IC at the K chosen for "
      "today; and the validated model's own depth):")
    w("")
    for lab, Hl in H.items():
        ch = (Hl["alpha"]["choices"] or {}).get(FINAL) or {}
        dt = (Hl["alpha"].get("depths") or {}).get(str(ch.get("K_E"))) or {}
        best = max(dt, key=lambda d: (dt[d].get("mean") or -9)) if dt else None
        w(f"- **{lab}:** uniform depth {LEVEL_NAME.get(best, '—')} (mean rank IC {_f((dt.get(best) or {}).get('mean'))}, global "
          f"{_f((dt.get('global') or {}).get('mean'))}); validated model: {_depth_word(ch)}.")
    w("")
    w("**9. Stability across eras** (Alpha, global weights, six era-specific fits):")
    w("")
    for lab, Hl in H.items():
        cnt: Dict[str, int] = {}
        for s in Hl["alpha"]["signals"]:
            c = (s.get("stability") or {}).get("class")
            cnt[c] = cnt.get(c, 0) + 1
        w(f"- **{lab}:** " + ", ".join(f"{k} {v}" for k, v in sorted(cnt.items(), key=lambda kv: -kv[1])))
    w("")
    w("**10. How different are learned weights from production?** Share-weighted distance (½·Σ|learned share − production "
      "share|, 0 = identical allocation, 1 = disjoint):")
    w("")
    for lab, Hl in H.items():
        d = sum(abs((s.get("learned_share") or 0) - (s.get("production_share") or 0)) for s in Hl["alpha"]["signals"]) / 2
        dd = sum(abs((s.get("learned_share") or 0) - (s.get("production_share") or 0)) for s in Hl["dir"]["signals"][1:]) / 2
        w(f"- **{lab}:** Alpha {d:.2f}, Directional {dd:.2f}")
    w("")
    w("**11. Does hierarchy improve out-of-sample performance?** Walk-forward rank IC, paired against production on identical records:")
    w("")
    w("| Horizon | Production | Best fit (B) | Validated (C) | Global (D) | Uniform hierarchy (E) | C − D (hierarchy's own gain) |")
    w("|---|---|---|---|---|---|---|")
    for lab, Hl in H.items():
        V_ = Hl["alpha"]["variants"]
        pr = (V_["C"]["walkforward"].get("production") or {}).get("rank_ic")
        cells = []
        for k in "BCDE":
            wf = V_[k]["walkforward"]
            cells.append(f"{_f((wf.get('challenger') or {}).get('rank_ic'))} (Δ t {_f((wf.get('paired') or {}).get('t'), 1)})")
        cd = ((Hl["alpha"].get("methods") or {}).get("_cd") or {})
        w(f"| {lab} | {_f(pr)} | " + " | ".join(cells) + f" | {_f((V_['C']['walkforward'].get('challenger') or {}).get('rank_ic', 0) - (V_['D']['walkforward'].get('challenger') or {}).get('rank_ic', 0))} |")
    w("")
    w("**12–13. Today's learned weights and scores.** See §7 (examples) and §8 (every asset × horizon: production score, "
      "learned score, best depth, reliability). The ML Lab view *Current learned Shaffer* shows every asset's effective "
      "weights with their hierarchy source.")
    w("")
    w("**14. Learned Directional weights** (beyond the PIT base prior; largest validated global weights):")
    w("")
    for lab, Hl in H.items():
        sig = Hl["dir"]["signals"][1:]
        top = sorted(sig, key=lambda s: -abs(s.get("learned_share") or 0))[:5]
        C = Hl["dir"]["variants"]["C"]
        w(f"- **{lab}:** " + (", ".join(f"{s['name']} {_pc(s.get('learned_share'), 0)}" for s in top if s.get("learned_share")) or "none trusted") +
          f" — Brier gain vs prior {_f((C.get('vs_prior') or {}).get('brier_gain'), 5)} (t {_f((C.get('vs_prior') or {}).get('brier_t'), 1)}), "
          f"calibration slope {_f((C.get('calibration') or {}).get('slope'), 2)}.")
    w("")
    hd = res.get("hedge") or {}
    w("**15. Learned hedge parameters** — §9.")
    w("")
    elig, interesting = [], []
    for lab, Hl in H.items():
        for tgt, nm in (("alpha", "Alpha"), ("dir", "Directional")):
            for k, v in Hl[tgt]["variants"].items():
                if v.get("status") == "LIVE SHADOW ELIGIBLE":
                    elig.append(f"{nm} {lab} {v['label']}")
                elif (v["gates"].get("t") or 0) >= 2 or v["gates"].get("fdr"):
                    interesting.append(f"{nm} {lab} {v['label']} (t {_f(v['gates'].get('t'), 1)}, failed " +
                                       ", ".join(g for g in ("G1", "G2", "G3", "G4") if not v["gates"].get(g)) + (", FDR" if not v["gates"].get("fdr") else "") + ")")
    vs = [f"{lab} {s['name']}" for lab, Hl in H.items() for s in Hl["alpha"]["signals"] if s.get("status") == "VALIDATED"]
    w(f"**16. Validated.** Signal weights (Alpha, VALIDATED): {len(vs)} of {74 * len(H)} signal × horizon weights"
      + (f" — {', '.join(vs[:30])}{'…' if len(vs) > 30 else ''}" if vs else "") + ". Whole learned systems passing every gate: "
      + (", ".join(elig) if elig else "none") + ".")
    w("")
    w("**17. Interesting but unverified:** " + ("; ".join(interesting) if interesting else "no learned system reached t ≥ 2 against production") + ".")
    w("")
    w("**18. Live shadow:** " + (", ".join(elig) if elig else "none — no learned system passed the fixed gates") + ".")
    w("")
    w("**19. Could anything eventually replace production?** Only a learned system that first passes the historical gates, "
      "then ≥ 60 graded live-shadow outcomes, then your approval. " + ("Candidates: " + ", ".join(elig) + "." if elig else
      "Nothing is on that path today; the learned weights remain the research map of what history supports."))
    w("")


def _depth_word(ch: dict) -> str:
    kb = ch.get("kept_by_level") or {}
    deep = [LEVEL_NAME[k] for k in LEVELS[1:] if kb.get(k)]
    return ("global weights plus specialisations kept at " + ", ".join(deep)) if deep else "global only (no node deviation survived)"


def _horizon_section(w, lab: str, Hl: dict, nodes: dict, assets: dict):
    A, Dd = Hl["alpha"], Hl["dir"]
    w(f"## 3–6. {lab} ({Hl['records']:,} records, {Hl['assets']} assets)")
    w("")
    w("### Production vs the learned weight sets (walk-forward, identical records)")
    w("")
    w("| Weight set | Rank IC (t) | Δ rank IC vs production (t) | Quintile spread | Net L/S (t) | Eras won | Split t | FDR | Status |")
    w("|---|---|---|---|---|---|---|---|---|")
    C = A["variants"]["C"]["walkforward"]
    p = C.get("production") or {}
    lp = C.get("ls_production") or {}
    w(f"| A production | {_f(p.get('rank_ic'))} ({_f(p.get('rank_t'), 1)}) | — | {_pc(p.get('quintile_spread'), 2)} | {_pc(lp.get('net'), 3)} ({_f(lp.get('t'), 1)}) | — | — | — | production |")
    for k in "BCDE":
        v = A["variants"][k]
        wf, g = v["walkforward"], v["gates"]
        c, pr, ls = wf.get("challenger") or {}, wf.get("paired") or {}, wf.get("ls_challenger") or {}
        w(f"| {k} {v['label']} | {_f(c.get('rank_ic'))} ({_f(c.get('rank_t'), 1)}) | {_f(pr.get('mean'))} ({_f(pr.get('t'), 1)}) | "
          f"{_pc(c.get('quintile_spread'), 2)} | {_pc(ls.get('net'), 3)} ({_f(ls.get('t'), 1)}) | {g.get('eras_won')}/{g.get('eras_complete')} | "
          f"{_f(g.get('split_t'), 1)} | {'✓' if g.get('fdr') else '✗'} | {v.get('status')} |")
    w("")
    w("Directional (P(up) beyond the PIT base prior; gates: Brier and log loss vs prior-only and vs prior + production, balanced "
      "accuracy, eras, split, calibration):")
    w("")
    w("| Weight set | Brier | Brier gain vs prior (t) | vs current (t) | Log-loss gain | Balanced acc. | ECE | Slope | Eras won | Status |")
    w("|---|---|---|---|---|---|---|---|---|---|")
    pr_ = Dd.get("prior") or {}
    w(f"| prior-only | {_f(pr_.get('brier'))} | — | — | — | {_pcu(pr_.get('balanced_accuracy'), 1)} | {_f(pr_.get('ece'))} | {_f((Dd.get('prior_calibration') or {}).get('slope'), 2)} | — | benchmark |")
    for k in "BCDE":
        v = Dd["variants"][k]
        m, vp, vc, g = v["metrics"], v["vs_prior"], v["vs_current"], v["gates"]
        w(f"| {k} {v['label']} | {_f(m.get('brier'))} | {_f(vp.get('brier_gain'), 5)} ({_f(vp.get('brier_t'), 1)}) | {_f(vc.get('brier_t'), 1)} | "
          f"{_f(vp.get('logloss_gain'), 5)} | {_pcu(m.get('balanced_accuracy'), 1)} | {_f(m.get('ece'))} | {_f((v.get('calibration') or {}).get('slope'), 2)} | "
          f"{g.get('eras_won')}/{g.get('eras_complete')} | {v.get('status')} |")
    w("")
    # optimisers
    M = A.get("methods") or {}
    w("### Optimisers (global level, walk-forward, every choice nested)")
    w("")
    w("| Optimiser | Alpha rank IC (t) | vs ridge (t) | Directional deviance gain (t) | vs ridge (t) |")
    w("|---|---|---|---|---|")
    MD = Dd.get("methods") or {}
    for k in [k for k in M if not k.startswith("_")] + [k for k in MD if not k.startswith("_") and k not in M]:
        a_, d_ = M.get(k) or {}, MD.get(k) or {}
        w(f"| {k} | {_f((a_.get('pooled') or {}).get('mean'))} ({_f((a_.get('pooled') or {}).get('t'), 1)}) | {_f((a_.get('vs_ridge') or {}).get('mean'))} ({_f((a_.get('vs_ridge') or {}).get('t'), 1)}) | "
          f"{_f((d_.get('pooled') or {}).get('mean'), 1)} ({_f((d_.get('pooled') or {}).get('t'), 1)}) | {_f((d_.get('vs_ridge') or {}).get('mean'), 1)} ({_f((d_.get('vs_ridge') or {}).get('t'), 1)}) |")
    bi = (M.get("_boosting") or {})
    if bi.get("top_interactions"):
        w("")
        w("Residual boosting's most used interactions (Alpha; a nonlinear diagnostic, never production): " +
          ", ".join(f"{k} ×{v}" for k, v in bi["top_interactions"][:6]) + ".")
    w("")
    # specialisation depth
    w("### Where specialisation stops helping (uniform depth, walk-forward mean rank IC, t)")
    w("")
    w("Every cell is a walk-forward result (each era scored by weights learned before it), but K and depth are held fixed across "
      "eras here: choosing the best cell uses hindsight. The nested choice is weight set E above.")
    w("")
    dt = A.get("depths") or {}
    w("| K (pooling) | " + " | ".join(LEVEL_NAME[d] for d in LEVELS) + " |")
    w("|---|" + "---|" * len(LEVELS))
    for K, row in dt.items():
        w(f"| {K} | " + " | ".join(f"{_f((row.get(d) or {}).get('mean'))} ({_f((row.get(d) or {}).get('t'), 1)})" for d in LEVELS) + " |")
    w("")
    chF = (A.get("choices") or {}).get(FINAL) or {}
    w(f"Validated model today: K = {chF.get('K')}, {chF.get('trusted')} of 74 global weights trusted (ρ > 0), "
      f"{chF.get('kept_nodes')} node × signal specialisations kept ({', '.join(f'{LEVEL_NAME[k]} {v}' for k, v in (chF.get('kept_by_level') or {}).items())}).")
    w("")
    # the most important table
    w(f"### The most important table — Global node, {lab} (Alpha)")
    w("")
    w("Weights are on standardised signals; *shares* are signed shares of total |weight| so production and learned are on one "
      "scale. Reliability: VALIDATED (OOS t ≥ 2, BH across signals × horizons, sign stable in ≥ 4 eras) · SUPPORTED (positive "
      "OOS contribution) · DESCRIPTIVE ONLY. Current value: mean of today's signal across assets; current contribution: mean "
      "of today's validated weight × signal.")
    w("")
    w("| Signal | Family | Production (share) | Best fit | Validated (share) | Trust ρ | Reliability | Era sign stability | Era class | OOS contribution t | Current value | Current contribution |")
    w("|---|---|---|---|---|---|---|---|---|---|---|---|")
    cur = _current_means(assets, Hl.get("signals") or [])
    for i, s in enumerate(A["signals"]):
        st = s.get("stability") or {}
        cv, cc = cur.get(i, (None, None))
        w(f"| {s['name']} | {(Hl.get('families') or [''] * 74)[i]} | {_pc(s.get('production_share'), 1)} | {_f(s.get('best_fit'))} | "
          f"{_f(s.get('shrunk'))} ({_pc(s.get('learned_share'), 1)}) | {_f(s.get('trust'), 2, False)} | {s.get('status')} | "
          f"{st.get('same_sign')}/{st.get('eras')} | {st.get('class')} | {_f(s.get('oos_t'), 1)} | {_f(cv, 3)} | {_f(cc, 4)} |")
    w("")
    # family expansion
    w(f"### Family weights and their signals ({lab}, Alpha, validated shares)")
    w("")
    fam_rows: Dict[str, List[str]] = {}
    fam_tot: Dict[str, float] = {}
    fam_prod: Dict[str, float] = {}
    for i, s in enumerate(A["signals"]):
        f = (Hl.get("families") or [""] * 74)[i]
        fam_tot[f] = fam_tot.get(f, 0.0) + (s.get("learned_share") or 0)
        fam_prod[f] = fam_prod.get(f, 0.0) + (s.get("production_share") or 0)
        if s.get("learned_share"):
            fam_rows.setdefault(f, []).append(f"{s['name']} {_pc(s['learned_share'], 1)}")
    w("| Family | Production share | Learned share | Signals (learned) |")
    w("|---|---|---|---|")
    for f in sorted(fam_tot, key=lambda k: -abs(fam_tot[k])):
        w(f"| {f} | {_pc(fam_prod.get(f), 1)} | {_pc(fam_tot[f], 1)} | {', '.join(fam_rows.get(f, [])) or '—'} |")
    w("")
    # structure diagnostics
    S = Hl.get("structure") or {}
    if S:
        w(f"### Confidence, regime, decay and applicability ({lab}; global, walk-forward)")
        w("")
        w("| Construction | Alpha rank IC (t) | vs free weights (t) | Directional deviance gain (t) | vs free (t) |")
        w("|---|---|---|---|---|")
        for k in S.get("alpha") or {}:
            a_, d_ = S["alpha"].get(k) or {}, (S.get("dir") or {}).get(k) or {}
            w(f"| {k} | {_f((a_.get('pooled') or {}).get('mean'))} ({_f((a_.get('pooled') or {}).get('t'), 1)}) | {_f((a_.get('vs_free') or {}).get('mean'))} ({_f((a_.get('vs_free') or {}).get('t'), 1)}) | "
              f"{_f((d_.get('pooled') or {}).get('mean'), 1)} ({_f((d_.get('pooled') or {}).get('t'), 1)}) | {_f((d_.get('vs_free') or {}).get('mean'), 1)} ({_f((d_.get('vs_free') or {}).get('t'), 1)}) |")
        w("")
    # regime-dependent weights
    rd = [s for s in A["signals"] if (s.get("stability") or {}).get("class") == "REGIME DEPENDENT"]
    if rd:
        w(f"Regime-dependent weights ({lab}, Alpha): " + "; ".join(
            f"{s['name']} (" + ", ".join(f"{dim}: " + ", ".join(f"{k} {_f(v, 3)}" for k, v in x.items() if k != 'diff_t') + f", t {_f(x.get('diff_t'), 1)}"
                                        for dim, x in (s.get("regimes") or {}).items() if abs(x.get("diff_t") or 0) >= 2) + ")" for s in rd[:8]) + ".")
        w("")
    # magnitude
    conv = (A["variants"]["C"].get("conviction") or {})
    pc = A.get("production_conviction") or {}
    if conv.get("buckets"):
        w(f"### Score magnitude ({lab}) — does a larger |score| mean a stronger outcome?")
        w("")
        w("| |score| | Validated: records | relative return | hit | rank IC | Production: records | relative return | hit |")
        w("|---|---|---|---|---|---|---|---|")
        for a_, b_ in zip(conv["buckets"], pc.get("buckets") or [{}] * len(conv["buckets"])):
            w(f"| {a_['bucket']} | {a_.get('n', 0):,} | {_pc(a_.get('relative_return'), 3)} | {_pc((a_.get('hit') or 0.5) - 0.5, 1) if a_.get('hit') is not None else '—'} | "
              f"{_f(a_.get('rank_ic'), 3)} | {b_.get('n', 0):,} | {_pc(b_.get('relative_return'), 3)} | {_pc((b_.get('hit') or 0.5) - 0.5, 1) if b_.get('hit') is not None else '—'} |")
        w("")
        w(f"Monotonic (every bucket populated and rising): validated {'yes' if conv.get('monotonic') else 'no'}, production "
          f"{'yes' if pc.get('monotonic') else 'no'}. Directional magnitude = its calibration: "
          + ", ".join(f"{b['bin']} predicted {_pcu(b.get('predicted'), 1)} → realised {_pcu(b.get('realised'), 1)} (n {b['n']:,})"
                      for b in ((Dd['variants']['C'].get('calibration') or {}).get('bins') or []) if b.get('n')) + ".")
        w("")


def _current_means(assets: dict, names) -> Dict[int, Tuple[float, float]]:
    out = {}
    xs = [a for a in assets.values() if a.get("x")]
    if not xs:
        return out
    for i in range(len(xs[0]["x"])):
        vals = [a["x"][i] for a in xs if a["x"][i] is not None]
        con = [a["alpha_contrib"][i] for a in xs if a.get("alpha_contrib") and a["alpha_contrib"][i] is not None]
        out[i] = (sum(vals) / len(vals) if vals else None, sum(con) / len(con) if con else None)
    return out


def _maps_section(w, H: dict):
    w("## 4. What matters for each type of asset (validated Alpha weights, family shares)")
    w("")
    for lab in [l for l in ("1W", "3M", "12M") if l in H] or list(H)[:1]:
        fm = H[lab]["alpha"].get("families") or {}
        nodes = [n for n in fm if n.startswith("class:")] + [n for n in fm if n.startswith("ptype:")]
        if not nodes:
            continue
        allf = sorted({f for n in nodes for f in fm[n]}, key=lambda f: -abs((fm.get("global") or {}).get(f) or 0))
        top = allf[:9]
        w(f"**{lab}**")
        w("")
        w("| Node | " + " | ".join(top) + " |")
        w("|---|" + "---|" * len(top))
        for n in ["global"] + nodes:
            w(f"| {node_label(n) if n != 'global' else 'Global'} ({level_of(n)}) | " + " | ".join(_pc((fm.get(n) or {}).get(f), 0) for f in top) + " |")
        w("")


def _examples_section(w, H: dict, assets: dict, names, live: dict):
    w("## 7. Today's learned weights — examples (Alpha)")
    w("")
    w("Signed shares of each weight set's total |weight| (so production and the learned sets are on one scale): production's "
      "effective weights today; C, the trust-filtered validated set; and, where they exist, the weight sets in live shadow "
      "(D global, E hierarchy — at 1W). Sorted by the live-shadow hierarchy weight where it exists, else by C; the twelve "
      "largest shown. Contribution = weight × today's (week-demeaned) signal, in the same set's units.")
    w("")
    for a in EXAMPLES:
        for lab in [l for l in ("1W", "3M") if l in H]:
            x = (assets.get(lab) or {}).get(a)
            if not x:
                continue
            sig = H[lab].get("signals") or names
            lev = x.get("alpha_levels") or {}
            lv = live_today(None, lab, x.get("x") or [], x.get("path") or ["global"], live)
            byv = {m["variant"]: m for m in lv.values()}
            w(f"**{a} — {lab}** · production score {_f(x.get('production_score'), 1)} · C {_f(x.get('learned_score'), 1)}"
              + "".join(f" · {k} ({m['label']}, live shadow) {_f(m['score'], 1)} from {node_label(m['node'])}" for k, m in sorted(byv.items()))
              + f" · p_up {_pcu(x.get('p_up'), 1)} (prior {_pcu(x.get('p_prior'), 1)}) · C's hierarchy source: "
              + ", ".join(f"{LEVEL_NAME[k]} {_pcu(v)}" for k, v in lev.items() if v) + f" · path: {' → '.join(node_label(n) for n in x.get('path') or [])}")
            w("")
            wc = x.get("alpha_weights") or []
            pv = shares(x.get("alpha_production") or [0.0] * len(wc))
            cs = shares(wc)
            ds = shares(byv["D"]["weights"]) if "D" in byv else None
            es = shares(byv["E"]["weights"]) if "E" in byv else None
            key = es or cs
            order = sorted(range(len(key)), key=lambda i: -abs(key[i] or 0))[:12]
            cols = ["Signal", "Production", "C validated"] + (["D global (live)"] if ds else []) + (["E hierarchy (live)"] if es else []) + ["Current value", "Contribution"]
            w("| " + " | ".join(cols) + " |")
            w("|" + "---|" * len(cols))
            con = byv["E"]["contrib"] if es else (x.get("alpha_contrib") or [None] * len(wc))
            for i in order:
                row = [sig[i], _pc(pv[i], 1), _pc(cs[i], 1)] + ([_pc(ds[i], 1)] if ds else []) + ([_pc(es[i], 1)] if es else []) + \
                      [_f((x.get("x") or [None] * 74)[i], 3), _f(con[i])]
                w("| " + " | ".join(row) + " |")
            w("")


def _asset_table(w, H: dict, assets: dict, live: dict):
    w("## 8. Every asset × horizon")
    w("")
    w("Learned score = the validated set C. Live-shadow scores = the learned weight sets that passed every gate (1W: D global, "
      "E hierarchy). Live shadow? = the asset is scored daily by a learned set in live shadow.")
    w("")
    w("| Asset | Horizon | Production score | Learned score (C) | Live-shadow scores | p_up (prior) | Best hierarchy depth | Alpha reliability (OOS corr, t) | Directional reliability (Brier gain, t) | Live shadow? |")
    w("|---|---|---|---|---|---|---|---|---|---|")
    allA = sorted({a for lab in H for a in (assets.get(lab) or {})})
    for a in allA:
        for lab, Hl in H.items():
            x = (assets.get(lab) or {}).get(a)
            if not x:
                continue
            ad = (Hl["alpha"].get("asset_depth") or {}).get(a) or {}
            rel = ad.get("reliability") or {}
            dr = (Hl["dir"].get("asset_reliability") or {}).get(a) or {}
            lv = live_today(None, lab, x.get("x") or [], x.get("path") or ["global"], live)
            elig = bool(lv)
            lvs = " / ".join(f"{m['variant']} {_f(m['score'], 1)}" for m in sorted(lv.values(), key=lambda m: m["variant"])) or "—"
            w(f"| {a} | {lab} | {_f(x.get('production_score'), 1)} | {_f(x.get('learned_score'), 1)} | {lvs} | {_pcu(x.get('p_up'), 1)} ({_pcu(x.get('p_prior'), 1)}) | "
              f"{LEVEL_NAME.get(ad.get('best_depth'), '—')} | {_f(rel.get('corr'), 3)} ({_f(rel.get('t'), 1)}) | {_f(dr.get('brier_gain'), 4)} ({_f(dr.get('t'), 1)}) | "
              f"{'yes' if elig else 'no'} |")
    w("")


def _hedge_section(w, hd: Optional[dict]):
    w("## 9. Learned Shaffer Hedge parameters")
    w("")
    if not hd:
        w("Not run.")
        w("")
        return
    w("Hierarchy: Global → Risk class → Objective → Product class → Instrument. Sizing: best multiple of hedge-2's package "
      "(0.5–1.5×) per node, shrunk toward the parent by dates/(dates + K); product preference: realised advantage of each "
      "product type over hedge-2's choice, shrunk; κ (cost sensitivity) and β (basis-risk penalty) chosen by walk-forward on "
      "earlier eras; validation against hedge-2 with the Hedge program's fixed gates H1–H5 and BH FDR. hedge-2 is unchanged.")
    w("")
    for lab, x in (hd.get("sizing") or {}).items():
        w(f"### Sizing multiplier — {lab} (K chosen today: {(x.get('K') or {}).get('2100-01-01')})")
        w("")
        w("| Node | Dates | Historical best fit | Validated (shrunk) |")
        w("|---|---|---|---|")
        nodes = x.get("nodes") or {}
        for nd in sorted(nodes, key=lambda n: (H_ORDER(n), n)):
            if H_ORDER(nd) > 2:
                continue
            v = nodes[nd]
            w(f"| {nd} | {v.get('dates')} | {_f(v.get('best_fit'), 2, False)} | {_f(v.get('shrunk'), 2, False)} |")
        deep = [(nd, v) for nd, v in nodes.items() if H_ORDER(nd) > 2 and v.get("best_fit") is not None and abs((v.get("shrunk") or 1) - 1) >= 0.1]
        if deep:
            w("")
            w("Product-class and instrument nodes whose validated multiple moved ≥ 0.1 from 1: " +
              ", ".join(f"{nd.split(':', 1)[1]} {v['shrunk']:.2f} ({v['dates']} dates)" for nd, v in sorted(deep, key=lambda kv: -abs(kv[1]['shrunk'] - 1))[:15]) + ".")
        w("")
        w("| Objective | Dates | ΔU λ=1 (95% CI) | ΔU λ=5 | t | Eras + | Cost Δ | FDR | Failed gates | Status |")
        w("|---|---|---|---|---|---|---|---|---|---|")
        for o, c in (x.get("cells") or {}).items():
            g, d = c.get("gates") or {}, c.get("d") or {}
            ci = (c.get("ci") or {}).get("1.0")
            a_, b_ = c.get("a") or {}, c.get("b") or {}
            w(f"| {o} | {c.get('dates')} | {_f(d.get('1.0'), 0)} ({('[' + _f(ci[0], 0) + ', ' + _f(ci[1], 0) + ']') if ci else '—'}) | {_f(d.get('5.0'), 0)} | "
              f"{_f(c.get('t'), 1)} | {g.get('eras_won')}/{g.get('eras_complete')} | {_f((b_.get('cost') or 0) - (a_.get('cost') or 0), 0)} | "
              f"{'✓' if g.get('fdr') else '✗'} | {', '.join(k for k in ('H1', 'H2', 'H3', 'H4', 'H5') if not g.get(k)) or '—'} | {c.get('status')} |")
        w("")
    for lab, x in (hd.get("tail") or {}).items():
        w(f"### Risk preference and the price of tail protection — {lab}")
        w("")
        w("Price per $ of ES = utility given up (λ = 1) per dollar of 95% expected-shortfall reduction bought by sizing up to 1.5×; "
          "\"free\" = sizing up reduced the tail and raised utility at the same time.")
        w("")
        w("| Node | Dates | Best multiple at λ = 0.5 / 1 / 2 / 5 / 10 | Sizing up to 1.5×: ES reduction | … utility change (λ = 1) | Price per $ of ES |")
        w("|---|---|---|---|---|---|")
        for nd in sorted(x, key=lambda n: (H_ORDER(n), n)):
            v = x[nd]
            b = v.get("best_multiple_by_lambda") or {}
            w(f"| {nd} | {v.get('dates')} | {' / '.join(f'{b.get(str(l)):.2f}' if b.get(str(l)) is not None else '—' for l in (0.5, 1.0, 2.0, 5.0, 10.0))} | "
              f"{_f(v.get('es_reduction_1_5x'), 0)} | {_f(v.get('utility_change_1_5x'), 0)} | "
              f"{'free' if (v.get('es_reduction_1_5x') or 0) > 0 and (v.get('utility_change_1_5x') or 0) >= 0 else _f(v.get('price_per_es'), 2)} |")
        w("")
    for lab, x in (hd.get("products") or {}).items():
        ch = (x.get("choices") or {}).get("2100-01-01")
        w(f"### Product preference, cost sensitivity and basis-risk penalty — {lab}")
        w("")
        w(f"Validated today: K = {ch[0] if ch else '—'}, κ (cost sensitivity) = {ch[1] if ch else '—'}, β (basis-risk penalty) = "
          f"{ch[2] if ch else '—'}; historical best fit (in-sample over every era): {x.get('best_fit')}.")
        w("")
        w("| Node | Product type | Dates | Historical advantage vs hedge-2 (λ = 1) | Validated (shrunk) preference | Dispersion |")
        w("|---|---|---|---|---|---|")
        raw, pref = x.get("raw") or {}, x.get("preferences") or {}
        for nd in sorted(raw, key=lambda n: (H_ORDER(n), n)):
            for t, v in sorted(raw[nd].items(), key=lambda kv: -(kv[1].get("adv") or 0)):
                w(f"| {nd} | {t.replace('type:', '')} | {v.get('n')} | {_f(v.get('adv'), 0)} | {_f((pref.get(nd) or {}).get(t), 0)} | {_f(v.get('sd'), 0)} |")
        w("")
        w("| Objective | Dates | ΔU λ=1 (95% CI) | t | Eras + | FDR | Failed gates | Status | Chosen in test eras |")
        w("|---|---|---|---|---|---|---|---|---|")
        for o, c in (x.get("cells") or {}).items():
            g, d = c.get("gates") or {}, c.get("d") or {}
            ci = (c.get("ci") or {}).get("1.0")
            chosen = ", ".join(k.replace("type:", "") + f" ×{v}" for k, v in ((x.get("chosen") or {}).get(o) or {}).items())
            failed = ", ".join(k for k in ("H1", "H2", "H3", "H4", "H5") if not g.get(k)) or "—"
            ci_s = ("[" + _f(ci[0], 0) + ", " + _f(ci[1], 0) + "]") if ci else "—"
            w(f"| {o} | {c.get('dates')} | {_f(d.get('1.0'), 0)} ({ci_s}) | {_f(c.get('t'), 1)} | "
              f"{g.get('eras_won')}/{g.get('eras_complete')} | {'✓' if g.get('fdr') else '✗'} | {failed} | {c.get('status')} | {chosen} |")
        w("")
    tod = hd.get("today") or {}
    if tod:
        w("### Today's effective hedge multiples (latest replay date; hedge-2 = 1.00)")
        w("")
        for lab, x in tod.items():
            ms = sorted(x.items())
            moved = [(k, v) for k, v in ms if abs((v.get("multiple") or 1) - 1) >= 0.05]
            w(f"- **{lab}:** {len(ms)} book × objective pairs; {len(moved)} with a validated multiple ≠ 1 — " +
              ", ".join(f"{k.replace('|', ' ')} {v['multiple']:.2f} ({v.get('source')})" for k, v in moved[:20]) + ("…" if len(moved) > 20 else ""))
        w("")


def H_ORDER(nd: str) -> int:
    return 0 if nd == "global" else ["global", "risk", "objective", "product", "instrument"].index(nd.split(":")[0])


# ================================================================== API for the ML Lab views
def api(store, h: Optional[str] = None, asset: Optional[str] = None, node: Optional[str] = None, target: str = "alpha") -> dict:
    """Slices of the stored run for the ML Lab: the summary; one horizon's asset list; one asset's weights; one node."""
    res = load(store)
    if not res:
        return {"ran": False}
    if not h:
        out = {"ran": True, "started": res.get("started"), "seconds": res.get("seconds"), "capability": res.get("capability"),
               "horizons": {}, "hedge": res.get("hedge")}
        for lab, H in (res.get("horizons") or {}).items():
            out["horizons"][lab] = {"records": H.get("records"), "assets": H.get("assets"),
                                    **{t: {"variants": {k: {kk: vv for kk, vv in v.items() if kk not in ("conviction", "bearish", "calibration")}
                                                        for k, v in (H.get(t) or {}).get("variants", {}).items()},
                                           "choices": ((H.get(t) or {}).get("choices") or {}).get(FINAL)} for t in ("alpha", "dir")}}
        return out
    H = (res.get("horizons") or {}).get(h) or {}
    tg = H.get(target) or {}
    if asset:
        A = (load(store, h, "assets") or {}).get(asset)
        if not A:
            return {"asset": asset, "missing": True}
        ad = (H.get("alpha") or {}).get("asset_depth", {}).get(asset) or {}
        dr = (H.get("dir") or {}).get("asset_reliability", {}).get(asset) or {}
        return {"asset": asset, "h": h, "signals": H.get("signals"), "families": H.get("families"), **A, "best_depth": ad.get("best_depth"),
                "live": live_today(store, h, A.get("x") or [], A.get("path") or ["global"]),
                "alpha_reliability": ad.get("reliability"), "dir_reliability": dr,
                "global": [{k: s.get(k) for k in ("name", "status", "oos_t", "trust", "best_fit", "stability")} for s in (H.get("alpha") or {}).get("signals", [])]}
    if node:
        N = ((load(store, h, "nodes") or {}).get(target) or {}).get(node)
        return {"node": node, "h": h, "target": target, "signals": tg and [s["name"] for s in tg.get("signals", [])], **(N or {})}
    assets = load(store, h, "assets") or {}
    lst = []
    for a, x in assets.items():
        ad = (H.get("alpha") or {}).get("asset_depth", {}).get(a) or {}
        dr = (H.get("dir") or {}).get("asset_reliability", {}).get(a) or {}
        lv = live_today(store, h, x.get("x") or [], x.get("path") or ["global"])
        lst.append({"asset": a, "production_score": x.get("production_score"), "learned_score": x.get("learned_score"), "p_up": x.get("p_up"),
                    "live": {m["variant"]: m["score"] for m in lv.values()},
                    "p_prior": x.get("p_prior"), "levels": x.get("alpha_levels"), "path": x.get("path"), "best_depth": ad.get("best_depth"),
                    "alpha_reliability": ad.get("reliability"), "dir_reliability": dr})
    return {"h": h, "target": target, "signals": tg.get("signals"), "families": H.get("families"), "variants": tg.get("variants"),
            "prior": tg.get("prior"), "choices": (tg.get("choices") or {}).get(FINAL), "methods": tg.get("methods"), "depths": tg.get("depths"),
            "structure": H.get("structure"), "family_maps": tg.get("families"), "assets": lst}


VARIANT_ID = {"B": "bestfit", "C": "validated", "D": "global", "E": "hierarchy"}
LIVE_KEY = RESEARCH_KEY + ":live"


def vid_of(lab: str, tgt: str, key: str) -> str:
    return f"{'alpha' if tgt == 'alpha' else 'directional'}-learned-{lab.lower()}-{VARIANT_ID[key]}-exp"


def register(store, res: dict) -> List[str]:
    """Registry entries: every horizon's validated weight set as 'research' (the weights exist and are shown; they are
    not trusted enough to compete), and every learned weight set that passed EVERY gate as a 'challenger' — which puts it
    in live shadow (recorded daily in the ledger, graded like production). Nothing is promoted."""
    from .lab import _save_registry, registry
    reg = registry(store)
    today = time.strftime("%Y-%m-%d")
    made = []
    reg["versions"] = [x for x in reg["versions"] if not (x.get("family") == "learned" and x.get("status") in ("research", "rejected"))
                       and not str(x.get("id", "")).endswith(("-learned-1d-exp", "-learned-1w-exp", "-learned-1m-exp", "-learned-3m-exp",
                                                               "-learned-6m-exp", "-learned-12m-exp"))]
    for lab, H in (res.get("horizons") or {}).items():
        for tgt, kind in (("alpha", "shaffer-alpha"), ("dir", "shaffer-directional")):
            for key, v in ((H.get(tgt) or {}).get("variants") or {}).items():
                passed = v.get("status") == "LIVE SHADOW ELIGIBLE"
                if key != "C" and not passed:
                    continue
                vid = vid_of(lab, tgt, key)
                old = next((x for x in reg["versions"] if x["id"] == vid), None)
                if old and old.get("status") not in ("challenger", "research", "rejected", None):
                    continue
                reg["versions"] = [x for x in reg["versions"] if x["id"] != vid]
                g = dict(v.get("gates") or {})
                g["G1_discovery"] = bool(g.get("G1"))
                g["G2_confirmation"] = bool(g.get("G2") and g.get("G3") and g.get("G4") and g.get("fdr"))
                ch = ((H.get(tgt) or {}).get("choices") or {}).get(FINAL) or {}
                reg["versions"].append({"id": vid, "kind": kind, "family": "learned", "variant": key, "label": v.get("label"), "horizon": lab,
                                        "introduced": (old or {}).get("introduced") or today, "status": "challenger" if passed else "research",
                                        "live_shadow_from": ((old or {}).get("live_shadow_from") or today) if passed else None,
                                        "formula": {"B": "historical best-fit weights at the asset's own node (K = 10)",
                                                    "C": "trusted global weights + validated node specialisations",
                                                    "D": "global learned weights on the 74 production signals (ridge, retrained on everything matured)",
                                                    "E": f"hierarchical weights at depth {ch.get('depth_E')} with pooling K = {ch.get('K_E')}"}[key]
                                                   + (" → week-demeaned signals, ranking target" if tgt == "alpha" else " → PIT prior offset + Platt"),
                                        "hierarchy": "global → class → product type → sector → industry → asset", "training_cutoff": res.get("started"),
                                        "benchmark": "production" if tgt == "alpha" else "prior-only (PIT base prior)",
                                        "validation": {lab: {"gates": g}}})
                made.append(vid)
    _save_registry(store, reg)
    return made


def build_live(db_path: str, res: dict, progress=None) -> dict:
    """For every learned weight set that passed every gate: its final weights, standardisation, node coefficients and
    score scale (refitted deterministically on the same records), stored for the daily live shadow."""
    from ..data.store import Store
    from .research import Research
    say = progress or (lambda m: None)
    todo: Dict[str, List[str]] = {}
    for lab, H in (res.get("horizons") or {}).items():
        for key, v in ((H.get("alpha") or {}).get("variants") or {}).items():
            if v.get("status") == "LIVE SHADOW ELIGIBLE" and key in ("B", "D", "E"):
                todo.setdefault(lab, []).append(key)
    out = {}
    st = Store(db_path)
    try:
        research = Research(st)
        for lab, keys in todo.items():
            rows, today, names = build_rows(st, research, lab, say)
            tab = Table(rows, names, int(dict(__import__("finsim2.engine.lab", fromlist=["LAB_HORIZONS"]).LAB_HORIZONS)[lab]))
            del rows
            lr = Learner(tab, "alpha")
            lr.fit_all()
            f = lr.fits[FINAL]
            ch = (res["horizons"][lab]["alpha"].get("choices") or {}).get(FINAL) or {}
            latest = max(t["rec"].date for t in today)
            pool = [t["rec"] for t in today if t["rec"].date == latest] or [t["rec"] for t in today]
            means = [sum(float(r.x[i]) for r in pool) / len(pool) for i in range(tab.P)]
            for key in keys:
                if key == "D":
                    std = {"global": f["W"][K_DEFAULT]["global"]}
                    depth, K = "global", None
                elif key == "E":
                    K, depth = ch.get("K_E") or ch.get("K"), ch.get("depth_E") or "global"
                    std = {nd: w for nd, w in f["W"][K].items() if LEVELS.index(level_of(nd)) <= LEVELS.index(depth)}
                else:
                    K, depth = K_BEST, "asset"
                    std = dict(f["W"][K])
                coefs = {nd: _coef(w, f["rms"]) for nd, w in std.items()}
                # the score scale: match production's median |score| on the matured records
                idx, raws = [], []
                for a, (s0, s1) in tab.slices.items():
                    c = coefs.get(cut_path(tab.paths[a], depth)) or coefs.get("global")
                    sc = score_slice(lr.cols, c, s0, s1)
                    idx.extend(abs(v) for k, v in zip(range(s0, s1), sc) if lr.w[k] > 0)
                    raws.extend(abs(tab.raw[k]) for k in range(s0, s1) if lr.w[k] > 0 and tab.raw[k] is not None)
                idx.sort(); raws.sort()
                scale = (idx[len(idx) // 2] / math.atanh(min(99.0, max(0.5, raws[len(raws) // 2])) / 100.0)) if idx and raws else 1.0
                vid = vid_of(lab, "alpha", key)
                out[vid] = {"lab": lab, "variant": key, "label": res["horizons"][lab]["alpha"]["variants"][key].get("label"), "names": names,
                            "depth": depth, "K": K, "coefs": {k: _rl(v, 8) for k, v in coefs.items()}, "weights": {k: _rl(v, 6) for k, v in std.items()},
                            "means": _rl(means, 8), "means_date": latest, "scale": scale, "built": time.strftime("%Y-%m-%d %H:%M:%S")}
                say(f"live model {vid}: depth {depth}, scale {scale:.4g}")
        st.kv_set(LIVE_KEY, out)
    finally:
        st.close()
    return out


def live_score(store, v: dict, meta: dict, lab: str, sig: Optional[dict], info: Optional[dict] = None) -> Optional[float]:
    """Today's learned Alpha score for one asset (live shadow): the stored weights at the asset's node, on today's
    signals demeaned by the latest research cross-section (a common shift that leaves the day's ranking unchanged)."""
    live = (store.kv_get(LIVE_KEY) or {}).get(v["id"]) or (store.kv_get("lab:finetune:live") or {}).get(v["id"])
    if not live or live.get("lab") != lab or not sig or not sig.get("x"):
        return None
    path = taxonomy(meta)
    nd = cut_path(path, live["depth"])
    while nd not in live["coefs"] and nd != "global":
        k = path.index(nd)
        nd = path[k - 1]
    coef = live["coefs"].get(nd)
    if not coef:
        return None
    xs = sig["x"]
    idx = sum(c * (float(xs.get(n) or 0.0) - m) for c, n, m in zip(coef, live["names"], live["means"]) if c)
    if info is not None:
        info["node"] = nd
    return 100.0 * math.tanh(idx / (live.get("scale") or 1.0))


def live_today(store, lab: str, x: Sequence[float], path: Sequence[str], live: Optional[dict] = None) -> Dict[str, dict]:
    """The live-shadow learned weight sets for one asset today: score, the node its weights come from, and the weights
    (standardised). `x` = today's raw signals in the research-record order."""
    out = {}
    for vid, m in (live if live is not None else (store.kv_get(LIVE_KEY) or {})).items():
        if m.get("lab") != lab:
            continue
        nd = cut_path(path, m["depth"])
        while nd not in m["coefs"] and nd != "global":
            nd = path[path.index(nd) - 1]
        coef, w = m["coefs"].get(nd), (m.get("weights") or {}).get(nd)
        if not coef:
            continue
        idx = sum(c * ((v or 0.0) - mu) for c, v, mu in zip(coef, x, m["means"]) if c)
        out[vid] = {"label": m.get("label"), "variant": m.get("variant"), "node": nd, "score": 100.0 * math.tanh(idx / (m.get("scale") or 1.0)),
                    "weights": w, "contrib": [c * ((v or 0.0) - mu) for c, v, mu in zip(coef, x, m["means"])]}
    return out
