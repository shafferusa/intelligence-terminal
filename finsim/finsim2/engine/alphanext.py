"""ML Lab — Shaffer Alpha vNext: extend the verified ranking edge to longer horizons with NEW information (research only).

Question: which assets will outperform or underperform other assets over the horizon? Target: y_alpha, the vol-scaled
forward return in excess of the point-in-time base expectation (directional.y_alpha); every metric is cross-sectional
(ranks within each scored week), never "against always bullish". Production (shaffer-alpha-2.1-production = the
production raw score) is the benchmark on identical records; nothing here changes production.

Each horizon (1D, 1W, 1M, 3M, 6M, 12M) is a separate problem: the models are fitted per horizon, so the data decide
which information matters where. The 74 production signals are NOT reweighted again (that failed); challengers add
information the production score does not contain, on top of the production score:
    fundamentals_x   first-reported SEC filings (data/sec_extra.py, filing-date PIT): FCF yield, accruals (earnings
                     quality), asset growth, leverage change, gross profitability, operating-margin change, net share
                     issuance (dilution / buybacks), buyback yield — equities only
    earnings         the earnings-event reaction and drift, margin / growth / ROE change, EPS and revenue surprise (newinfo)
    sector_relative  valuation, growth, profitability and 12-1 momentum relative to the asset's class / sector on the
                     same date (the production signals are absolute; relative structure is new)
    macro            credit-quality spread and term-premium / real-yield changes × the asset's PIT beta (macro sensitivity)
    breadth          market breadth × PIT beta (the one family that passed in the new-information study, at 1W)
BLOCKED (no point-in-time history on the permitted sources): forward valuation, analyst and earnings revisions,
historical consensus, options-implied expectations, positioning, flows, futures curves.

Asset-level features enter as cross-sectional percentile ranks on the record's date (robust, point in time); missing
= the median (0). Market-wide features enter as their expanding (PIT) z-score × the asset's PIT beta.

Challengers (fixed before any result), per horizon — design [1, production raw / 100, features]:
    alpha-3-{h}-global        ridge, one global model, λ = 1% of the training records
    alpha-3-{h}-class         the same, each asset class shrunk to the global model (prior weight K = 2,000 records)
    alpha-3-{h}-sector        class → sector, each node shrunk to its parent (K = 2,000)
    alpha-3-{h}-fundamental   global ridge on the slow families only (fundamentals_x, earnings, sector_relative)
Scores are 100·tanh(index / scale) with the scale matching the production raw score's median |value| in training.

Protocol: fitted on records that matured before each era (→ 2008 test 2009–12, → 2012 test 2013–16, → 2016 test
2017–20, → 2020 test 2021–24, → 2024 test 2025–) and the 2018 split; weekly clustering with n_eff = weeks·5/max(5, h).

Gates (per challenger × horizon, fixed 2026-09-26 before any result):
    G1  walk-forward paired Δ rank IC vs production: t ≥ 2 (weekly clustered), and the challenger's rank IC > 0
    G2  2018 split (train < 2018, test ≥ 2018): Δ rank IC t ≥ 1
    G3  Δ rank IC > 0 in ≥ 3 of the 4 complete eras
    G4  no material degradation: quintile spread and net-of-cost long-short return not below production's by more than
        10% of |production|; at 1D / 1W the challenger's rank IC not below production's (preserve the edge)
    FDR Benjamini-Hochberg at q = 0.10 over every challenger × horizon G1 test (one-sided p from t)
    A challenger passing all is LIVE SHADOW ELIGIBLE (never production: live shadow G5 and your approval come first).
Horizon usefulness (1M and longer, for any model incl. production): rank IC > 0 with t surviving BH across horizons,
quintile spread > 0, rank IC > 0 in ≥ 3 of 4 complete eras, net-of-cost long-short > 0 — every criterion at once.
Net-of-cost long-short: equal-weight top minus bottom quintile, formed on non-overlapping dates h sessions apart, cost
= 10 bp one-way × the turnover of each leg (names replaced).
Conviction (research only): |score| buckets < 10, 10–25, 25–50, 50–75, ≥ 75 — signed relative return, hit rate, rank
IC, volatility, drawdown, n_eff; magnitude is usable for sizing only if the bucket means rise monotonically.
"""
from __future__ import annotations

import bisect
import math
import time
from typing import Dict, List, Optional, Tuple

from . import directional as D
from . import weights as W
from .lab import LAB_HORIZONS
from .weights import ERAS, SPLIT, Rec, _clustered_mean

RESEARCH_KEY = "lab:alphanext"
FINAL_CUT = "9999-12-31"
COST_ONE_WAY = 0.0010
K_NODE = 2000.0
LAMBDA_SHARE = 0.01
FDR_Q = 0.10
BUCKETS = [(0, 10), (10, 25), (25, 50), (50, 75), (75, 101)]

FUND_X = ["fcf_yield", "accruals", "asset_growth", "lev_change", "gross_prof", "op_margin_chg", "net_issuance", "buyback_yield"]
EARN = ["pead_return", "pead_volume", "margin_yoy", "growth_accel", "roe_change", "sue_eps", "sue_rev"]
SECREL_SRC = {"rel_value_5y": "value_5y", "rel_earnings_yield": "earnings_yield", "rel_book_to_price": "book_to_price",
              "rel_sales_yield": "sales_yield", "rel_eps_growth": "eps_growth_yoy", "rel_rev_growth": "rev_growth_yoy",
              "rel_roe": "roe", "rel_mom_12_1": "mom_12_1"}
SECREL = list(SECREL_SRC)
MACRO = ["quality_spread_z", "d_quality_spread_3m", "term_premium", "d_term_premium_3m", "d_real5_3m"]
BREADTH = ["net_new_highs", "breadth_thrust_10d", "pct_above_50d", "dispersion_21d"]
WIDE = {"quality_spread_z": "credit_quality", "d_quality_spread_3m": "credit_quality", "term_premium": "term_structure",
        "d_term_premium_3m": "term_structure", "d_real5_3m": "term_structure", "net_new_highs": "breadth_plus",
        "breadth_thrust_10d": "breadth_plus", "pct_above_50d": "breadth_plus", "dispersion_21d": "breadth_plus"}
GROUPS = {"fundamentals_x": FUND_X, "earnings": EARN, "sector_relative": SECREL, "macro": MACRO, "breadth": BREADTH}
ALL = FUND_X + EARN + SECREL + MACRO + BREADTH
SLOW = FUND_X + EARN + SECREL
CHALLENGERS = {"global": (ALL, "global"), "class": (ALL, "class"), "sector": (ALL, "sector"), "fundamental": (SLOW, "global")}
BLOCKED = ["forward valuation / analyst estimates", "analyst and earnings revisions", "historical consensus",
           "options-implied expectations", "positioning (CFTC not permitted; short interest 1 year only)", "fund flows",
           "dated futures curves"]


def vid(lab: str, name: str) -> str:
    return f"alpha-3-{lab.lower()}-{name}-exp"


# ------------------------------------------------------------------ features (point in time on each record's date)
def _expanding_z(xs: List[Optional[float]], min_n: int = 252) -> List[Optional[float]]:
    out: List[Optional[float]] = [None] * len(xs)
    n = s = ss = 0.0
    for i, x in enumerate(xs):
        if x is None:
            continue
        if n >= min_n:
            m = s / n
            v = ss / n - m * m
            if v > 0:
                out[i] = max(-4.0, min(4.0, (x - m) / math.sqrt(v)))
        n += 1; s += x; ss += x * x
    return out


class FeatureBuilder:
    """Raw feature values per (asset, calendar index) for the vNext Alpha families."""

    def __init__(self, research, store):
        from . import newinfo as N
        from ..data import sec_extra as X
        self.N, self.X = N, X
        self.store = store
        self.b = N.Builder(research, store)
        self.cal = self.b.cal
        self._facts: Dict[str, object] = {}
        self._shares: Dict[str, list] = {}
        self._wide: Dict[str, List[Optional[float]]] = {}
        self._asset: Dict[str, dict] = {}
        self._px: Dict[str, tuple] = {}
        for f, fam in WIDE.items():
            series = (self.b.wide(fam) or {}).get(f) or [None] * self.b.n
            self._wide[f] = _expanding_z(series)

    def wide(self, name: str, i: int) -> Optional[float]:
        s = self._wide.get(name)
        return s[i] if s and 0 <= i < len(s) else None

    def asset_features(self, meta: dict) -> Dict[str, list]:
        a = meta["id"]
        if a not in self._asset:
            out = {}
            ev = self.b.features("earnings_events", meta) or {}
            su = self.b.features("earnings_surprise", meta) or {}
            for k in EARN:
                out[k] = ev.get(k) or su.get(k)
            self._asset[a] = out
        return self._asset[a]

    def fundamentals_x(self, meta: dict, i: int) -> Dict[str, Optional[float]]:
        """The SEC-derived features on calendar index i (filings published on or before that date)."""
        a = meta["id"]
        out = {k: None for k in FUND_X}
        if meta.get("asset_class") != "EQUITY":
            return out
        if a not in self._facts:
            self._facts[a] = self.X.Facts(self.store.alt(self.X.DATASET, a))
            rows = sorted((r["filed"], r["period_end"], r["value"]) for r in self.store.fundamentals(a, "shares"))
            self._shares[a] = rows
        f = self._facts[a]
        d = self.cal[i]
        if a not in self._px:
            self._px[a] = (self.b.series(a, "close"), self.b.series(a))
        closes, adjs = self._px[a]
        close, adj = closes[i], adjs[i]
        sh = self._shares_at(a, d)
        if not close or not sh or sh[1] <= 0:
            return out
        mcap = sh[1] * close
        fresh = lambda t: t is not None and f._days(d, t[0]) <= 550  # noqa: E731
        ocf, capex, ni = f.ttm("ocf", d), f.ttm("capex", d), f.ttm("net_income", d)
        assets, assets1 = f.instant("assets", d), f.instant("assets", d, 365)
        debt, debt1 = f.instant("debt", d), f.instant("debt", d, 365)
        gp, op, rev = f.ttm("gross_profit", d), f.ttm("op_income", d), f.ttm("revenue", d)
        op1, rev1 = f.ttm("op_income", d, 1), f.ttm("revenue", d, 1)
        bb = f.ttm("buyback", d)
        if fresh(ocf) and fresh(capex) and mcap > 0:
            out["fcf_yield"] = (ocf[1] - capex[1]) / mcap
        if fresh(ocf) and fresh(ni) and assets and assets[1]:
            out["accruals"] = (ni[1] - ocf[1]) / assets[1]
        if assets and assets1 and assets1[1]:
            out["asset_growth"] = assets[1] / assets1[1] - 1
        if assets and assets1 and assets[1] and assets1[1] and debt and debt1:
            out["lev_change"] = debt[1] / assets[1] - debt1[1] / assets1[1]
        if fresh(gp) and assets and assets[1]:
            out["gross_prof"] = gp[1] / assets[1]
        if fresh(op) and fresh(rev) and op1 and rev1 and rev[1] and rev1[1]:
            out["op_margin_chg"] = op[1] / rev[1] - op1[1] / rev1[1]
        if fresh(bb) and mcap > 0:
            out["buyback_yield"] = bb[1] / mcap
        sh1 = self._shares_at(a, d, 365)
        j = i - 252
        if sh1 and j >= 0:
            c1, a1 = closes[j], adjs[j]
            if c1 and a1 and adj and sh1[1] and sh1[1] > 0 and sh[1] > 0:
                # split-neutral share change: log(shares·close/adj) now vs a year ago
                out["net_issuance"] = -(math.log(sh[1] * close / adj) - math.log(sh1[1] * c1 / a1))
        return out

    def _shares_at(self, a: str, d: str, lag_days: int = 0):
        rows = self._shares.get(a) or []
        k = bisect.bisect_right(rows, (d, "9999", float("inf")))
        vis = rows[:k]
        if not vis:
            return None
        latest = max(vis, key=lambda x: x[1])
        if not lag_days:
            return latest[1], latest[2]
        import datetime as _dt
        tgt = _dt.date.fromisoformat(latest[1]) - _dt.timedelta(days=lag_days)
        near = [x for x in vis if abs((_dt.date.fromisoformat(x[1]) - tgt).days) <= 40]
        return (near[-1][1], near[-1][2]) if near else None


def build_rows(recs: List[Rec], fb: FeatureBuilder) -> List[Tuple[Rec, List[float]]]:
    """(record, feature vector over ALL) with asset-level features as cross-sectional percentile ranks per date and
    market-wide features as PIT z × beta."""
    sig_idx = {s: j for j, s in enumerate(W.signals())}
    raw: Dict[int, Dict[str, Optional[float]]] = {}
    for n, r in enumerate(recs):
        i = (r.ext or {}).get("i")
        if i is None:
            continue
        v: Dict[str, Optional[float]] = {}
        af = fb.asset_features(r.meta)
        for k in EARN:
            s = af.get(k)
            v[k] = s[i] if s and i < len(s) else None
        v.update(fb.fundamentals_x(r.meta, i))
        for k, src in SECREL_SRC.items():
            j = sig_idx.get(src)
            v[k] = float(r.x[j]) if (j is not None and r.x is not None and r.x[j] != 0.0) else None
        beta = (r.ext or {}).get("beta")
        b = beta if beta is not None else 1.0
        for k in MACRO + BREADTH:
            z = fb.wide(k, i)
            v[k] = (z * b) if z is not None else None
        raw[n] = v
    # cross-sectional ranks per date (asset-level); sector-relative ranks within (class, sector)
    by_date: Dict[str, List[int]] = {}
    for n in raw:
        by_date.setdefault(recs[n].date, []).append(n)
    out: Dict[int, List[float]] = {n: [0.0] * len(ALL) for n in raw}
    pos = {k: j for j, k in enumerate(ALL)}
    for d, ns in by_date.items():
        for k in FUND_X + EARN:
            _rank_into(out, raw, ns, k, pos[k])
        groups: Dict[tuple, List[int]] = {}
        for n in ns:
            m = recs[n].meta
            groups.setdefault((m.get("asset_class"), m.get("sector")), []).append(n)
        for g in groups.values():
            if len(g) >= 3:
                for k in SECREL:
                    _rank_into(out, raw, g, k, pos[k])
        for n in ns:
            for k in MACRO + BREADTH:
                x = raw[n].get(k)
                out[n][pos[k]] = x if x is not None else 0.0
    return [(recs[n], out[n]) for n in sorted(out)]


def _rank_into(out, raw, ns, k, j):
    vals = [(raw[n].get(k), n) for n in ns if raw[n].get(k) is not None]
    if len(vals) < 3:
        return
    vals.sort()
    m = len(vals)
    idx = 0
    while idx < m:
        e = idx
        while e + 1 < m and vals[e + 1][0] == vals[idx][0]:
            e += 1
        p = ((idx + e) / 2.0) / (m - 1) - 0.5
        for q in range(idx, e + 1):
            out[vals[q][1]][j] = p
        idx = e + 1


# ------------------------------------------------------------------ hierarchical ridge from cumulative sufficient statistics
def _node_keys(meta: dict, depth: str) -> List[str]:
    keys = ["global"]
    if depth in ("class", "sector"):
        keys.append(f"c:{meta.get('asset_class')}")
    if depth == "sector":
        keys.append(f"s:{meta.get('asset_class')}:{meta.get('sector')}")
    return keys


class Stats:
    """Per node, per era bucket: XᵀX (upper), Xᵀy, n. Buckets from the record's maturity date."""

    def __init__(self, p: int, cuts: List[str]):
        self.p, self.cuts = p, cuts
        self.nodes: Dict[str, List[list]] = {}

    def add(self, keys: List[str], x: List[float], y: float, end: str):
        b = bisect.bisect_right(self.cuts, end)          # usable by cut k when b <= k (r.end < cut)
        p = self.p
        for key in keys:
            nd = self.nodes.get(key)
            if nd is None:
                nd = self.nodes[key] = [[None] * (len(self.cuts) + 1)]
            bk = nd[0]
            if bk[b] is None:
                bk[b] = [[[0.0] * p for _ in range(p)], [0.0] * p, [0]]
            A, v, cnt = bk[b]
            for i in range(p):
                xi = x[i]
                if xi == 0.0:
                    continue
                Ai = A[i]
                for j in range(i, p):
                    Ai[j] += xi * x[j]
                v[i] += xi * y
            cnt[0] += 1

    def upto(self, key: str, k: int) -> Optional[tuple]:
        """Sums over buckets 0..k (records maturing before cut k)."""
        nd = self.nodes.get(key)
        if nd is None:
            return None
        p = self.p
        A = [[0.0] * p for _ in range(p)]
        v = [0.0] * p
        n = 0
        for b in range(0, k + 1):
            x = nd[0][b]
            if x is None:
                continue
            for i in range(p):
                Ai, Xi = A[i], x[0][i]
                for j in range(i, p):
                    Ai[j] += Xi[j]
                v[i] += x[1][i]
            n += x[2][0]
        for i in range(p):
            for j in range(i):
                A[i][j] = A[j][i]
        return A, v, n


def _solve(A: List[List[float]], b: List[float]) -> List[float]:
    n = len(b)
    M = [row[:] + [b[i]] for i, row in enumerate(A)]
    for c in range(n):
        piv = max(range(c, n), key=lambda r: abs(M[r][c]))
        if abs(M[piv][c]) < 1e-12:
            continue
        M[c], M[piv] = M[piv], M[c]
        for r in range(n):
            if r != c and M[r][c]:
                f = M[r][c] / M[c][c]
                for k in range(c, n + 1):
                    M[r][k] -= f * M[c][k]
    return [M[i][n] / M[i][i] if abs(M[i][i]) > 1e-12 else 0.0 for i in range(n)]


def fit_tree(stats: Stats, k: int, nodes: List[str]) -> Dict[str, List[float]]:
    """Global ridge (λ = LAMBDA_SHARE·n, intercept unpenalised); each deeper node shrunk to its parent with K_NODE."""
    out: Dict[str, List[float]] = {}
    for key in sorted(nodes, key=lambda s: (s != "global", s.count(":"))):
        st = stats.upto(key, k)
        if st is None or st[2] < 200:
            continue
        A, v, n = st
        p = len(v)
        if key == "global":
            lam = LAMBDA_SHARE * n
            prior = [0.0] * p
        else:
            parent = "global" if key.startswith("c:") else "c:" + key.split(":")[1]
            prior = out.get(parent) or out.get("global")
            if prior is None:
                continue
            lam = K_NODE
        M = [row[:] for row in A]
        rhs = v[:]
        for i in range(1, p):
            M[i][i] += lam
            rhs[i] += lam * prior[i]
        if key != "global":
            M[0][0] += lam
            rhs[0] += lam * prior[0]
        out[key] = _solve(M, rhs)
    return out


def _weights_for(fit: Dict[str, List[float]], keys: List[str]) -> Optional[List[float]]:
    for k in reversed(keys):
        if k in fit:
            return fit[k]
    return None


# ------------------------------------------------------------------ metrics
def paired_rank_ic(rows: List[Rec], h: int) -> dict:
    """Weekly cross-sectional rank IC of the challenger (r.score) minus production (r.raw), clustered by week."""
    weeks: Dict[int, list] = {}
    for r in rows:
        weeks.setdefault(r.wk, []).append(r)
    d: Dict[int, List[float]] = {}
    for wk, rs in weeks.items():
        if len(rs) < 8:
            continue
        ys = W._ranks([D.y_alpha(r) for r in rs])
        m = (len(rs) - 1) / 2.0
        sy = math.sqrt(sum((b - m) ** 2 for b in ys))

        def ric(vals):
            xs = W._ranks(vals)
            sx = math.sqrt(sum((a - m) ** 2 for a in xs))
            return sum((a - m) * (b - m) for a, b in zip(xs, ys)) / (sx * sy) if sx > 0 and sy > 0 else None
        c, p = ric([r.score for r in rs]), ric([r.raw for r in rs])
        if c is not None and p is not None:
            d[wk] = [c - p]
    cm = _clustered_mean(d, h)
    return {"mean": cm["mean"], "t": (cm["mean"] / cm["se"]) if cm["mean"] is not None and cm["se"] else None, "weeks": cm["weeks"]}


def long_short(rows: List[Rec], key, h: int) -> dict:
    """Equal-weight top minus bottom quintile, formed on non-overlapping dates (≥ h sessions apart), net of 10 bp
    one-way × turnover of each leg. Returns mean gross / net per formation, t, and annualised net."""
    weeks: Dict[int, list] = {}
    for r in rows:
        weeks.setdefault(r.wk, []).append(r)
    step = max(1, int(round(h / 5.0)))
    ws = sorted(weeks)
    picked = ws[::step]
    gross, net = [], []
    prev_top = prev_bot = None
    for wk in picked:
        rs = weeks[wk]
        if len(rs) < 10:
            continue
        o = sorted(rs, key=key)
        k = max(1, len(o) // 5)
        top, bot = o[-k:], o[:k]
        g = sum(math.expm1(r.yr) for r in top) / k - sum(math.expm1(r.yr) for r in bot) / k
        tset, bset = {r.asset for r in top}, {r.asset for r in bot}
        tt = 1.0 if prev_top is None else len(tset - prev_top) / k
        tb = 1.0 if prev_bot is None else len(bset - prev_bot) / k
        c = COST_ONE_WAY * 2 * (tt + tb)
        gross.append(g); net.append(g - c)
        prev_top, prev_bot = tset, bset
    if len(net) < 10:
        return {"n": len(net)}
    m = sum(net) / len(net)
    sd = math.sqrt(sum((x - m) ** 2 for x in net) / (len(net) - 1))
    per_year = 252.0 / max(1, h) if h >= 5 else 52.0
    return {"n": len(net), "gross": sum(gross) / len(gross), "net": m, "t": (m / (sd / math.sqrt(len(net)))) if sd else None,
            "net_annual": m * per_year}


def conviction(rows: List[Rec], key, h: int) -> List[dict]:
    """|score| buckets: signed relative return (score sign × return minus the week's median), hit rate, pooled rank
    IC, volatility and drawdown of the bucket's weekly mean, n_eff."""
    weeks: Dict[int, list] = {}
    for r in rows:
        weeks.setdefault(r.wk, []).append(r)
    med = {wk: sorted(r.yr for r in rs)[len(rs) // 2] for wk, rs in weeks.items() if len(rs) >= 8}
    out = []
    for lo, hi in BUCKETS:
        sub = [r for r in rows if r.wk in med and lo <= abs(key(r)) < hi]
        if len(sub) < 50:
            out.append({"bucket": f"{lo}–{hi if hi <= 100 else ''}", "n": len(sub)})
            continue
        rel = [math.copysign(1.0, key(r)) * (r.yr - med[r.wk]) for r in sub if key(r) != 0]
        hit = sum(1 for x in rel if x > 0) / len(rel)
        by_w: Dict[int, List[float]] = {}
        for r, x in zip([r for r in sub if key(r) != 0], rel):
            by_w.setdefault(r.wk, []).append(x)
        series = [sum(v) / len(v) for _, v in sorted(by_w.items())]
        eq = peak = dd = 0.0
        for x in series:
            eq += x; peak = max(peak, eq); dd = min(dd, eq - peak)
        xs, ys = W._ranks([key(r) for r in sub]), W._ranks([D.y_alpha(r) for r in sub])
        m = (len(sub) - 1) / 2.0
        sx = math.sqrt(sum((a - m) ** 2 for a in xs)); sy = math.sqrt(sum((b - m) ** 2 for b in ys))
        mean = sum(rel) / len(rel)
        out.append({"bucket": f"{lo}–{hi if hi <= 100 else ''}", "n": len(sub), "n_eff": len(by_w) * 5.0 / max(5.0, h),
                    "relative_return": math.expm1(mean), "hit": hit,
                    "rank_ic": (sum((a - m) * (b - m) for a, b in zip(xs, ys)) / (sx * sy)) if sx > 0 and sy > 0 else None,
                    "volatility": (math.sqrt(sum((x - sum(series) / len(series)) ** 2 for x in series) / max(1, len(series) - 1))) if series else None,
                    "drawdown": dd})
    means = [b.get("relative_return") for b in out if b.get("relative_return") is not None]
    mono = len(means) == len(out) and all(b >= a for a, b in zip(means, means[1:]))
    return {"buckets": out, "monotonic": mono}


def by_group(rows: List[Rec], h: int, key_fn) -> Dict[str, dict]:
    """Weekly rank IC of challenger and production inside each group (≥ 8 names a week), for stability."""
    groups: Dict[str, List[Rec]] = {}
    for r in rows:
        g = key_fn(r)
        if g:
            groups.setdefault(g, []).append(r)
    out = {}
    for g, rs in groups.items():
        c, p = D.alpha_metrics(rs, lambda r: r.score, h), D.alpha_metrics(rs, lambda r: r.raw, h)
        if c.get("rank_ic") is not None:
            out[g] = {"n": len(rs), "challenger": c["rank_ic"], "challenger_t": c.get("rank_t"), "production": p.get("rank_ic"), "production_t": p.get("rank_t")}
    return out


def evaluate(rows: List[Rec], h: int, groups: bool = False) -> dict:
    """Challenger (r.score) and production (r.raw) on the same records."""
    if not rows:
        return {"n": 0}
    extra = {}
    if groups:
        extra = {"by_class": by_group(rows, h, lambda r: r.meta.get("asset_class")),
                 "by_sector": by_group(rows, h, lambda r: r.meta.get("sector") if r.meta.get("asset_class") == "EQUITY" else None)}
    return {"n": len(rows), **extra, "challenger": D.alpha_metrics(rows, lambda r: r.score, h),
            "production": D.alpha_metrics(rows, lambda r: r.raw, h), "paired": paired_rank_ic(rows, h),
            "ls_challenger": long_short(rows, lambda r: r.score, h), "ls_production": long_short(rows, lambda r: r.raw, h),
            "mono_challenger": W._mono(rows, lambda r: r.score), "mono_production": W._mono(rows, lambda r: r.raw)}


# ------------------------------------------------------------------ one horizon
def study_horizon(store, research, lab: str, progress=None) -> dict:
    say = progress or (lambda m: None)
    t0 = time.time()
    W._init_famidx()
    h = dict(LAB_HORIZONS)[lab]
    recs = W.load(store, research, lab)
    D.attach(recs, research, h)
    recs = [r for r in recs if r.ext and r.ext.get("s") and r.yr != 0]
    fb = FeatureBuilder(research, store)
    rows = build_rows(recs, fb)
    say(f"{lab}: {len(rows)} records with features ({time.time() - t0:.0f}s)")
    cuts = sorted({a for a, _ in ERAS} | {SPLIT, FINAL_CUT})
    pos = {k: j for j, k in enumerate(ALL)}
    coverage = {k: sum(1 for _, v in rows if v[pos[k]] != 0.0) / len(rows) for k in ALL} if rows else {}
    out = {"horizon": lab, "h": h, "records": len(rows), "assets": len({r.asset for r, _ in rows}), "coverage": coverage, "challengers": {}}
    for name, (feats, depth) in CHALLENGERS.items():
        idx = [pos[k] for k in feats]
        stats = Stats(2 + len(feats), cuts)
        nodes = set()
        for r, v in rows:
            keys = _node_keys(r.meta, depth)
            nodes.update(keys)
            stats.add(keys, [1.0, r.raw / 100.0] + [v[j] for j in idx], D.y_alpha(r), r.end)
        fits = {c: fit_tree(stats, cuts.index(c), sorted(nodes)) for c in cuts}

        def scored(cut, test):
            ft = fits[cut]
            k = cuts.index(cut)
            # scale: production's median |raw| against the model's median |index| on its training records
            train = [(r, v) for r, v in rows if bisect.bisect_right(cuts, r.end) <= k][-20000:]
            idxs = []
            for r, v in train:
                w = _weights_for(ft, _node_keys(r.meta, depth))
                if w:
                    idxs.append(abs(sum(a * b for a, b in zip(w, [1.0, r.raw / 100.0] + [v[j] for j in idx]))))
            raws = sorted(abs(r.raw) for r, _ in train)
            scale = 1.0
            if idxs and raws:
                mi, mr = sorted(idxs)[len(idxs) // 2], raws[len(raws) // 2]
                scale = mi / max(1e-9, math.atanh(min(0.99, mr / 100.0))) if mr > 0 else 1.0
            res = []
            for r, v in test:
                w = _weights_for(ft, _node_keys(r.meta, depth))
                if w is None:
                    continue
                q = W.Rec()
                for s in Rec.__slots__:
                    setattr(q, s, getattr(r, s, None))
                q.score = 100.0 * math.tanh(sum(a * b for a, b in zip(w, [1.0, r.raw / 100.0] + [v[j] for j in idx])) / max(scale, 1e-9))
                res.append(q)
            return res
        eras, wf = [], []
        for a, b in ERAS:
            test = [(r, v) for r, v in rows if a <= r.date < b]
            sc = scored(a, test)
            ev = evaluate(sc, h)
            eras.append({"from": a, "to": b, **ev})
            wf += sc
        split = evaluate(scored(SPLIT, [(r, v) for r, v in rows if r.date >= SPLIT]), h)
        wfe = evaluate(wf, h, groups=True)
        wg = fits[FINAL_CUT].get("global")
        out["challengers"][name] = {"id": vid(lab, name), "features": feats, "depth": depth, "walkforward": wfe, "split": split,
                                    "eras": eras, "conviction": conviction(wf, lambda r: r.score, h),
                                    "weights": dict(zip(["intercept", "production"] + feats, wg)) if wg else None,
                                    "nodes": len(fits[FINAL_CUT])}
        say(f"{lab}: {name} done ({time.time() - t0:.0f}s)")
    base = next(iter(out["challengers"].values()))["walkforward"]
    out["production"] = {"walkforward": base.get("production"), "ls": base.get("ls_production"), "mono": base.get("mono_production")}
    allwf = []
    for a, b in ERAS:
        allwf += [r for r, _ in rows if a <= r.date < b]
    out["production"]["conviction"] = conviction(allwf, lambda r: r.raw, h)
    out["production"]["eras"] = [{"from": a, "to": b, **(D.alpha_metrics([r for r in allwf if a <= r.date < b], lambda r: r.raw, h))} for a, b in ERAS]
    out["seconds"] = round(time.time() - t0, 1)
    return out


# ------------------------------------------------------------------ gates, FDR, statuses
def _p(t: Optional[float]) -> float:
    return 1.0 - D._phi(t) if t is not None else 1.0


def benjamini_hochberg(ps: List[float], q: float = FDR_Q) -> List[bool]:
    m = len(ps)
    order = sorted(range(m), key=lambda k: ps[k])
    kmax = 0
    for rank, k in enumerate(order, 1):
        if ps[k] <= q * rank / m:
            kmax = rank
    rej = [False] * m
    for rank, k in enumerate(order, 1):
        rej[k] = rank <= kmax
    return rej


def gates(ch: dict, lab: str) -> dict:
    wf = ch["walkforward"]
    pr = wf.get("paired") or {}
    c, p = wf.get("challenger") or {}, wf.get("production") or {}
    g = {"G1": bool((pr.get("t") or 0) >= 2 and (c.get("rank_ic") or 0) > 0), "t": pr.get("t"), "d_rank_ic": pr.get("mean")}
    g["split_t"] = ((ch.get("split") or {}).get("paired") or {}).get("t")
    g["G2"] = bool((g["split_t"] or 0) >= 1)
    full = [e for e in ch["eras"][:4] if (e.get("paired") or {}).get("mean") is not None]
    g["eras_won"] = sum(1 for e in full if e["paired"]["mean"] > 0)
    g["eras_complete"] = len(full)
    g["G3"] = g["eras_won"] >= 3

    def ok(a, b):
        return a is not None and b is not None and a >= b - 0.1 * abs(b)
    lsc, lsp = (wf.get("ls_challenger") or {}).get("net"), (wf.get("ls_production") or {}).get("net")
    g4 = ok(c.get("quintile_spread"), p.get("quintile_spread")) and ok(lsc, lsp)
    if lab in ("1D", "1W"):
        g4 = g4 and (c.get("rank_ic") or -1) >= (p.get("rank_ic") or 0)
    g["G4"] = bool(g4)
    return g


def usefulness(metrics: dict, ls: dict, eras: List[dict]) -> dict:
    full = [e for e in eras[:4] if e.get("rank_ic") is not None]
    return {"rank_ic": metrics.get("rank_ic"), "t": metrics.get("rank_t"), "quintile_spread": metrics.get("quintile_spread"),
            "eras_positive": sum(1 for e in full if e["rank_ic"] > 0), "eras_complete": len(full), "net_ls": (ls or {}).get("net")}


def finalise(res: dict) -> dict:
    keys, ps = [], []
    for lab, hz in res["horizons"].items():
        for name, ch in hz["challengers"].items():
            ch["gates"] = gates(ch, lab)
            keys.append((lab, name)); ps.append(_p(ch["gates"]["t"]))
    for (lab, name), r in zip(keys, benjamini_hochberg(ps)):
        ch = res["horizons"][lab]["challengers"][name]
        g = ch["gates"]
        g["fdr"] = r
        g["passed"] = bool(g["G1"] and g["G2"] and g["G3"] and g["G4"] and r)
        ch["status"] = "LIVE SHADOW ELIGIBLE" if g["passed"] else "REJECT"
    # horizon usefulness (production and every challenger), BH across all of them
    u_keys, u_ps = [], []
    for lab, hz in res["horizons"].items():
        pe = [e for e in hz["production"]["eras"]]
        hz["production"]["useful"] = usefulness(hz["production"]["walkforward"] or {}, hz["production"]["ls"], pe)
        u_keys.append((lab, None)); u_ps.append(_p(hz["production"]["useful"]["t"]))
        for name, ch in hz["challengers"].items():
            ce = [e.get("challenger") or {} for e in ch["eras"]]
            ch["useful"] = usefulness(ch["walkforward"].get("challenger") or {}, ch["walkforward"].get("ls_challenger"), ce)
            u_keys.append((lab, name)); u_ps.append(_p(ch["useful"]["t"]))
    for (lab, name), r in zip(u_keys, benjamini_hochberg(u_ps)):
        hz = res["horizons"][lab]
        u = hz["production"]["useful"] if name is None else hz["challengers"][name]["useful"]
        u["fdr"] = r
        u["useful"] = bool(r and (u["rank_ic"] or 0) > 0 and (u["quintile_spread"] or 0) > 0 and u["eras_positive"] >= 3 and (u["net_ls"] or 0) > 0)
    return res


def _worker(db_path: str, lab: str):
    from ..data.store import Store
    from .research import Research
    st = Store(db_path)
    try:
        return lab, study_horizon(st, Research(st), lab, progress=lambda m: print(m, flush=True))
    finally:
        st.close()


def run_all(db_path: str, workers: int = 3, progress=None, horizons=None) -> dict:
    from concurrent.futures import ProcessPoolExecutor, as_completed
    from ..data.store import Store
    from .lab import benchmark, verify_benchmark
    say = progress or (lambda m: None)
    t0 = time.time()
    st = Store(db_path)
    try:
        bm = benchmark(st)
        if not bm or not verify_benchmark(st, bm["id"]).get("ok"):
            raise ValueError("a frozen, verified benchmark is required")
    finally:
        st.close()
    res = {"started": time.strftime("%Y-%m-%d %H:%M:%S"), "benchmark": {k: bm[k] for k in ("id", "hash", "frozen", "production")},
           "groups": GROUPS, "blocked": BLOCKED, "challengers": {k: {"features": len(v[0]), "depth": v[1]} for k, v in CHALLENGERS.items()},
           "horizons": {}}
    labs = [lab for lab, _ in LAB_HORIZONS if not horizons or lab in horizons]
    with ProcessPoolExecutor(max_workers=workers) as ex:
        for f in as_completed([ex.submit(_worker, db_path, lab) for lab in labs]):
            lab, hz = f.result()
            res["horizons"][lab] = hz
            say(f"{lab} finished")
    res["horizons"] = {lab: res["horizons"][lab] for lab in labs if lab in res["horizons"]}
    finalise(res)
    res["seconds"] = round(time.time() - t0, 1)
    st = Store(db_path)
    try:
        st.kv_set(RESEARCH_KEY, res)
    finally:
        st.close()
    return res


# ------------------------------------------------------------------ the report (SHAFFER_ALPHA_VNEXT.md)
def _f(v, d=3, sign=True):
    return "—" if v is None else (f"{v:+.{d}f}" if sign else f"{v:.{d}f}")


def _pc(v, d=2):
    return "—" if v is None else f"{v * 100:+.{d}f}%"


def best_challenger(hz: dict) -> Optional[str]:
    chs = hz.get("challengers") or {}
    ok = [(n, c) for n, c in chs.items() if ((c.get("walkforward") or {}).get("paired") or {}).get("t") is not None]
    return max(ok, key=lambda nc: nc[1]["walkforward"]["paired"]["t"])[0] if ok else None


def markdown(res: dict) -> str:
    H = res.get("horizons") or {}
    L: List[str] = []
    w = L.append
    passed = [(lab, n) for lab, hz in H.items() for n, c in hz["challengers"].items() if c.get("status") == "LIVE SHADOW ELIGIBLE"]
    useful_p = [lab for lab, hz in H.items() if (hz["production"].get("useful") or {}).get("useful")]
    useful_c = [(lab, n) for lab, hz in H.items() for n, c in hz["challengers"].items() if (c.get("useful") or {}).get("useful")]
    long_new = [(lab, n) for lab, n in useful_c if lab not in ("1D", "1W")]
    w("# Shaffer Alpha vNext — research")
    w("")
    w(f"Run {res.get('started')} · {res.get('seconds')} s · `python -m finsim2 lab --vnext alpha`. Research only; production "
      f"(`shaffer-alpha-2.1-production`, benchmark `{(res.get('benchmark') or {}).get('id')}`) is unchanged. Protocol, challengers and "
      "gates were committed before the full run (module docstring of `engine/alphanext.py`); the 1W smoke run was seen while testing "
      "the code and nothing was changed after it.")
    w("")
    w("## Summary")
    w("")
    w("- **What was tested:** four challengers per horizon (global, class, sector, fundamental) that ADD new point-in-time information "
      "to the production score — SEC fundamentals first reported (FCF yield, accruals, asset growth, leverage change, gross "
      "profitability, operating-margin change, net issuance, buybacks), earnings events and surprises, sector-relative valuation / "
      "growth / momentum, macro sensitivity (credit and term premium × beta) and breadth × beta — against the production score on "
      "identical records, walk-forward over four unseen eras, the 2018 split and 2025–.")
    w(f"- **Improved (every gate incl. FDR):** " + (", ".join(f"{n} at {lab}" for lab, n in passed) if passed else "nothing") + ".")
    w(f"- **Horizons verified useful** (rank IC > 0 surviving FDR, positive quintile spread, ≥ 3 of 4 eras, positive net-of-cost "
      f"long-short): production at {', '.join(useful_p) or 'no horizon'}; challengers at " +
      (", ".join(f"{n} {lab}" for lab, n in useful_c) or "no horizon") + ".")
    w(f"- **Longer horizons (1M–12M):** " + ("newly useful: " + ", ".join(f"{n} {lab}" for lab, n in long_new) + "."
                                            if long_new else "no model — production or challenger — is verified useful beyond 1W. The ranking edge does not extend past 1W with the information available."))
    w(f"- **Blocked data (the bottleneck):** {', '.join(res.get('blocked') or BLOCKED)}.")
    w("")
    w("## Main table (walk-forward, identical records)")
    w("")
    w("| Horizon | Model | Rank IC (t) | Δ rank IC vs production (t) | Quintile spread | Decile spread | Net L/S per period (t) · annualised | Hit vs median | Monotonicity | Eras Δ > 0 | Split Δ t | FDR | Status |")
    w("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for lab, hz in H.items():
        p = hz["production"]
        pw, pls = p.get("walkforward") or {}, p.get("ls") or {}
        w(f"| {lab} | production | {_f(pw.get('rank_ic'), 4)} ({_f(pw.get('rank_t'), 1)}) | — | {_pc(pw.get('quintile_spread'))} | {_pc(pw.get('decile_spread'))} | "
          f"{_pc(pls.get('net'), 3)} ({_f(pls.get('t'), 1)}) · {_pc(pls.get('net_annual'), 1)} | {_pc((pw.get('hit_vs_median') or 0.5) - 0.5)} | {_f(p.get('mono'), 2)} | — | — | — | production |")
        for n, c in hz["challengers"].items():
            wf, g = c["walkforward"], c.get("gates") or {}
            cm, ls = wf.get("challenger") or {}, wf.get("ls_challenger") or {}
            w(f"| {lab} | {n} | {_f(cm.get('rank_ic'), 4)} ({_f(cm.get('rank_t'), 1)}) | {_f((wf.get('paired') or {}).get('mean'), 4)} ({_f((wf.get('paired') or {}).get('t'), 1)}) | "
              f"{_pc(cm.get('quintile_spread'))} | {_pc(cm.get('decile_spread'))} | {_pc(ls.get('net'), 3)} ({_f(ls.get('t'), 1)}) · {_pc(ls.get('net_annual'), 1)} | "
              f"{_pc((cm.get('hit_vs_median') or 0.5) - 0.5)} | {_f(wf.get('mono_challenger'), 2)} | {g.get('eras_won')}/{g.get('eras_complete')} | {_f(g.get('split_t'), 1)} | "
              f"{'✓' if g.get('fdr') else '✗'} | {c.get('status')} |")
    w("")
    w("Hit vs median is shown as the excess over 50%. Net L/S: equal-weight top minus bottom quintile per non-overlapping formation "
      "period of the horizon, net of 10 bp one-way × turnover. Monotonicity: Spearman of the ten score-decile mean outcomes.")
    w("")
    w("## Horizon usefulness (every criterion at once)")
    w("")
    w("| Horizon | Model | Rank IC | t | FDR | Quintile spread | Eras rank IC > 0 | Net L/S | Useful? |")
    w("|---|---|---|---|---|---|---|---|---|")
    for lab, hz in H.items():
        for n, u in [("production", hz["production"].get("useful") or {})] + [(n, c.get("useful") or {}) for n, c in hz["challengers"].items()]:
            w(f"| {lab} | {n} | {_f(u.get('rank_ic'), 4)} | {_f(u.get('t'), 1)} | {'✓' if u.get('fdr') else '✗'} | {_pc(u.get('quintile_spread'))} | "
              f"{u.get('eras_positive')}/{u.get('eras_complete')} | {_pc(u.get('net_ls'), 3)} | {'**YES**' if u.get('useful') else 'no'} |")
    w("")
    w("## Era stability (Δ rank IC vs production, walk-forward eras)")
    w("")
    w("| Horizon | Model | 2009–12 | 2013–16 | 2017–20 | 2021–24 | 2025– |")
    w("|---|---|---|---|---|---|---|")
    for lab, hz in H.items():
        for n, c in hz["challengers"].items():
            w(f"| {lab} | {n} | " + " | ".join(_f((e.get("paired") or {}).get("mean"), 4) for e in c["eras"]) + " |")
    w("")
    w("## Stability by asset class and sector (best challenger vs production, rank IC)")
    w("")
    w("| Horizon | Best challenger | Group | Records | Challenger rank IC (t) | Production rank IC (t) |")
    w("|---|---|---|---|---|---|")
    for lab, hz in H.items():
        b = best_challenger(hz)
        if not b:
            continue
        wf = hz["challengers"][b]["walkforward"]
        for kind in ("by_class", "by_sector"):
            for g, x in sorted((wf.get(kind) or {}).items(), key=lambda kv: -kv[1]["n"]):
                w(f"| {lab} | {b} | {g} | {x['n']:,} | {_f(x['challenger'], 4)} ({_f(x.get('challenger_t'), 1)}) | {_f(x.get('production'), 4)} ({_f(x.get('production_t'), 1)}) |")
    w("")
    w("## Specialisation (global → class → sector)")
    w("")
    w("| Horizon | Global Δ rank IC (t) | Class Δ (t) | Sector Δ (t) | Deeper beats its parent? |")
    w("|---|---|---|---|---|")
    for lab, hz in H.items():
        ch = hz["challengers"]
        t = {n: ((ch.get(n) or {}).get("walkforward") or {}).get("paired") or {} for n in ("global", "class", "sector")}
        better = []
        if (t["class"].get("mean") or -9) > (t["global"].get("mean") or -9):
            better.append("class > global")
        if (t["sector"].get("mean") or -9) > (t["class"].get("mean") or -9):
            better.append("sector > class")
        w(f"| {lab} | {_f(t['global'].get('mean'), 4)} ({_f(t['global'].get('t'), 1)}) | {_f(t['class'].get('mean'), 4)} ({_f(t['class'].get('t'), 1)}) | "
          f"{_f(t['sector'].get('mean'), 4)} ({_f(t['sector'].get('t'), 1)}) | {', '.join(better) or 'no'} |")
    w("")
    w("## What the models used, by horizon (global challenger, final fit)")
    w("")
    w("Weights on standardised inputs (asset-level features are cross-sectional percentile ranks −0.5…0.5; market-wide ones are "
      "PIT z × beta), largest magnitude first. A weight is not evidence by itself; the gates above are.")
    w("")
    for lab, hz in H.items():
        wg = (hz["challengers"].get("global") or {}).get("weights") or {}
        top = sorted(((k, v) for k, v in wg.items() if k not in ("intercept",)), key=lambda kv: -abs(kv[1]))[:8]
        w(f"- **{lab}:** " + ", ".join(f"{k} {v:+.3f}" for k, v in top))
    w("")
    w("## Conviction: does a larger |Alpha| mean a larger relative return?")
    w("")
    w("| Horizon | Model | |score| bucket | Records | n_eff | Signed relative return | Hit | Rank IC | Volatility | Drawdown | Monotonic? |")
    w("|---|---|---|---|---|---|---|---|---|---|---|")
    for lab, hz in H.items():
        b = best_challenger(hz)
        for n, cv in [("production", hz["production"].get("conviction") or {})] + ([(b, hz["challengers"][b].get("conviction") or {})] if b else []):
            for k, x in enumerate(cv.get("buckets") or []):
                w(f"| {lab} | {n} | {x['bucket']} | {x['n']:,} | {_f(x.get('n_eff'), 0, False)} | {_pc(x.get('relative_return'), 3)} | "
                  f"{_pc((x.get('hit') or 0.5) - 0.5) if x.get('hit') is not None else '—'} | {_f(x.get('rank_ic'), 3)} | {_pc(x.get('volatility'), 2)} | {_pc(x.get('drawdown'), 1)} | "
                  f"{('**yes**' if cv.get('monotonic') else 'no') if k == 0 else ''} |")
    w("")
    w("If the bucket means do not rise monotonically, Alpha magnitude must not size positions (answer below).")
    w("")
    w("## Answers")
    w("")
    mono = [(lab, n) for lab, hz in H.items() for n, cv in [("production", hz["production"].get("conviction") or {})] if cv.get("monotonic")]
    w(f"- **Live-shadow eligibility:** " + (", ".join(f"{vid(lab, n)}" for lab, n in passed) if passed else "none — no challenger passes G1–G4 and the FDR control") + ".")
    w("- **Production eligibility:** none (live shadow — 60 graded paired outcomes — and your approval come first).")
    w(f"- **Conviction sizing:** production |Alpha| is monotonic in relative return at {', '.join(l for l, _ in mono) or 'no horizon'}; "
      "elsewhere magnitude must not be used for sizing.")
    w("- **Next data bottleneck:** point-in-time analyst estimates and revisions (forward valuation, earnings revisions), then options-"
      "implied expectations, positioning / short interest history and fund flows. Every longer-horizon Alpha source that is plausibly "
      "informative is on that list; the fundamentals available from filings were tested here.")
    return "\n".join(L) + "\n"
