"""Shaffer Alpha across horizons, 1M – 5Y: horizon / sector / stock weights, with magnitude (research only).

Protocol: SHAFFER_ALPHA_HORIZONS_PROTOCOL.md (fixed before any result). Report: SHAFFER_ALPHA_HORIZONS.md.
Nothing here changes production, D, E, the Directional prior-only model or any hedge.

Question: for US stocks, what weights on the Shaffer families (and the new-data families that passed their own test)
does point-in-time history support at each horizon — and do horizon-, sector- or stock-specific weights rank stocks
better, out of sample, than the production Shaffer score? And by how much is a stock expected to beat SPY?

Records (``build``; lab_records version AHZ_VERSION, one blob per stock and horizon), every 21 sessions per stock:
    x    FEATURES: the stock-specific production families as signed means of clip(z / 2, ±1) of their member signals
         (market-wide families cannot rank stocks on one date and are left out), low volatility (−vol_60 z), low beta
         (−beta_252 z), plus EXTRA new-data features (batch-2 families that passed, engine/newinfo.py)
    yr   log excess return over SPY to t + h;  y = yr / (vol_60 · √(h/252)) clipped ±4
    raw  the production Shaffer score at the nearest standard research record (≤ 5 sessions) for min(h, 12M)
Horizons: 1M 3M 6M 12M 24M 36M 60M (groups short = 1M–3M, medium = 6M–12M, long = 24M–60M).

Model (target: the date's centred cross-sectional rank u of y; features demeaned within the date = pairwise ranking
least squares), weights on standardised features, every node a ridge toward its parent (partial pooling):
    global (all horizons) → horizon group → horizon → sector → stock
Records are weighted q = 21 / max(21, h) (overlapping outcomes). Variants fixed in advance:
    V1 horizon   global → group → horizon
    V2 sector    + sector
    V3 stock     + sector + stock
    V*           depth (V1 / V2 / V3) and shrinkage K chosen per horizon by nested inner validation (the three years
                 before each test era, trained on outcomes matured before them) — the deployable candidate
Walk-forward test eras 2009–12, 2013–16, 2017–20, 2021–24, 2025– (training = outcomes matured before the era) and the
2018 split. Metric: per-date Spearman rank IC; paired Δ vs production on identical records; t over dates with
n_eff = dates · 21 / h.

Magnitude (V*): in each training window the score's within-date percentile is binned in deciles; each decile's mean
excess log return, its 5% / 95% quantiles and the share beating SPY are applied to the test records. Reported:
decile spread, calibration slope, 90%-interval coverage, Brier of P(beat SPY) vs the base rate.
"""
from __future__ import annotations

import bisect
import datetime as _dt
import math
import time
from typing import Dict, List, Optional, Tuple

from .. import shaffer_score as cfg

AHZ_VERSION = "ahz1"
RESEARCH_KEY = "lab:alphahz"
HORIZONS = [("1M", 21), ("3M", 63), ("6M", 126), ("12M", 252), ("24M", 504), ("36M", 756), ("60M", 1260)]
GROUP = {"1M": "short", "3M": "short", "6M": "medium", "12M": "medium", "24M": "long", "36M": "long", "60M": "long"}
BASE_LAB = {"1M": "1M", "3M": "3M", "6M": "6M", "12M": "12M", "24M": "12M", "36M": "12M", "60M": "12M"}
STEP = 21
START = "1995-01-01"
WARMUP = 252
MIN_PRESENT = 0.5
MARKET_WIDE = {"Rates", "Credit", "Macro", "Liquidity", "Volatility", "Cross-Asset"}
K_GRID = [10.0, 100.0, 1000.0, 10000.0]
TEST_ERAS = [("2009-01-01", "2013-01-01"), ("2013-01-01", "2017-01-01"), ("2017-01-01", "2021-01-01"),
             ("2021-01-01", "2025-01-01"), ("2025-01-01", "2100-01-01")]
ERA_LABEL = ["2009–12", "2013–16", "2017–20", "2021–24", "2025–"]
SPLIT = "2018-01-01"
INNER_YEARS = 3
MIN_DATE_N = 20                   # stocks on a date for a rank IC
MIN_ERA_DATES = 24                # dates for an era to count as complete
FDR_Q = 0.10
VARIANTS = ["V1", "V2", "V3", "V*"]
DEPTH = {"V1": 3, "V2": 4, "V3": 5}


# ------------------------------------------------------------------ features
def feature_spec() -> List[Tuple[str, List[Tuple[str, int]]]]:
    out = []
    for fam, sg in cfg.FAMILIES.items():
        if fam in MARKET_WIDE:
            continue
        members = [(s, int(w)) for s, w in sg if w]
        if members:
            out.append((fam, members))
    out.append(("Low volatility", [("vol_60", -1)]))
    out.append(("Low beta", [("beta_252", -1)]))
    return out


def feature_names(extra: Optional[List[str]] = None) -> List[str]:
    return [f for f, _ in feature_spec()] + list(extra or [])


def _clip(v, lo=-1.0, hi=1.0):
    return lo if v < lo else hi if v > hi else v


def family_features(z: Dict[str, list], t: int, spec) -> List[Optional[float]]:
    out = []
    for _, members in spec:
        vals = [s * _clip(z[n][t] / cfg.S_SCALE) for n, s in members if (z.get(n) or [None] * (t + 1))[t] is not None]
        out.append(sum(vals) / len(vals) if vals else None)
    return out


def passed_extra(b2: dict) -> List[Tuple[str, str]]:
    """(family, feature) of every batch-2 family with an Alpha test that passed G1, G2 and FDR at any horizon (fixed
    rule, SHAFFER_ALPHA_HORIZONS_PROTOCOL.md)."""
    from .newinfo import FAMILIES
    fams = []
    for lab, hz in (b2.get("horizons") or {}).items():
        for fam, fr in (hz.get("families") or {}).items():
            g = ((fr.get("gates") or {}).get("alpha") or {})
            if g.get("passed") and fam not in fams and (fr.get("gates") or {}).get("track") == "historical":
                fams.append(fam)
    return [(f, feat) for f in sorted(fams) for feat in FAMILIES[f]["features"]]


# ------------------------------------------------------------------ build the records
def _stocks(store) -> List[dict]:
    return [a for a in store.assets("EQUITY") if store.price_count(a["id"]) >= 252 * 6]


def _base_raw(store, labs) -> Dict[Tuple[str, str], Dict[str, float]]:
    """(asset, standard lab) -> {date: production raw} from the standard research records."""
    out: Dict[Tuple[str, str], Dict[str, float]] = {}
    for lab in labs:
        for b in store.lab_records(cfg.VERSION, lab):
            out[(b["asset_id"], lab)] = {r[0]: r[1] for r in b["rows"] if r[1] is not None}
    return out


def build_asset(research, a: str, extra: Optional[List[Tuple[str, str]]] = None, builder=None,
                base: Optional[Dict[Tuple[str, str], Dict[str, float]]] = None) -> Dict[str, list]:
    """{horizon: rows [date, raw, y, yr, x]} for one stock (matured outcomes only)."""
    panel = research.panel()
    cal = panel.calendar()
    n = len(cal)
    px, spy = panel.series(a), panel.series("SPY")
    z = research.zscores(a)
    vol = research.features(a).get("vol_60") or [None] * n
    spec = feature_spec()
    ex: List[list] = []
    if extra and builder is not None:
        meta = research.store.asset(a) or {"id": a}
        cache: Dict[str, Optional[dict]] = {}
        for fam, feat in extra:
            if fam not in cache:
                cache[fam] = builder.features(fam, meta)
            ex.append((cache[fam] or {}).get(feat) or [None] * n)
    first = next((i for i, v in enumerate(px) if v), None)
    if first is None:
        return {}
    lo = max(first + WARMUP, bisect.bisect_left(cal, START))
    base = base or {}
    out: Dict[str, list] = {lab: [] for lab, _ in HORIZONS}
    for t in range(lo, n, STEP):
        if not px[t] or not spy[t] or not vol[t]:
            continue
        x = family_features(z, t, spec)
        if sum(v is not None for v in x) < MIN_PRESENT * len(x):
            continue
        x = [round(v, 4) if v is not None else None for v in x] + [round(s[t], 4) if s[t] is not None else None for s in ex]
        for lab, h in HORIZONS:
            if t + h >= n or not px[t + h] or not spy[t + h]:
                continue
            yr = math.log(px[t + h] / px[t]) - math.log(spy[t + h] / spy[t])
            y = _clip(yr / (vol[t] * math.sqrt(h / 252.0)), -4.0, 4.0)
            braw = base.get((a, BASE_LAB[lab])) or {}
            raw = None
            for k in range(0, 6):
                d = cal[t - k] if t - k >= 0 else None
                if d in braw:
                    raw = braw[d]
                    break
            out[lab].append([cal[t], raw, round(y, 5), round(yr, 6), x])
    return out


def _build_worker(db_path: str, assets: List[str], extra):
    from ..data.store import Store
    from .research import Research
    st = Store(db_path)
    try:
        r = Research(st)
        builder = None
        if extra:
            from .newinfo import Builder
            builder = Builder(r, st)
        base = _base_raw(st, sorted(set(BASE_LAB.values())))
        dv = r.version()
        done = 0
        for a in assets:
            recs = build_asset(r, a, extra, builder, base)
            for lab, rows in recs.items():
                if rows:
                    st.put_lab_records(a, lab, AHZ_VERSION, dv, rows)
            _forget(r, a)
            done += 1
        return done
    finally:
        st.close()


def _forget(research, a: str):
    """Release one stock's cached features / z-scores (Research memoises them; ~450 stocks would not fit in memory)."""
    mem = getattr(research, "_mem", None)
    if isinstance(mem, dict):
        for k in (f"z:{a}", f"f:{a}"):
            mem.pop(k, None)


def build(db_path: str, workers: int = 3, extra: Optional[List[Tuple[str, str]]] = None, progress=None) -> dict:
    from concurrent.futures import ProcessPoolExecutor, as_completed
    from ..data.store import Store
    st = Store(db_path)
    try:
        ids = [a["id"] for a in _stocks(st)]
        st._write(lambda conn: conn.execute("DELETE FROM lab_records WHERE version = ?", (AHZ_VERSION,)))
        st.kv_set(RESEARCH_KEY + ":build", {"extra": [list(e) for e in (extra or [])], "features": feature_names([f for _, f in (extra or [])]),
                                             "stocks": len(ids), "started": time.strftime("%Y-%m-%d %H:%M:%S")})
    finally:
        st.close()
    say = progress or (lambda m: None)
    chunks = [ids[k::max(1, workers * 4)] for k in range(max(1, workers * 4))]
    t0, done = time.time(), 0
    with ProcessPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(_build_worker, db_path, c, extra) for c in chunks if c]
        for f in as_completed(futs):
            done += f.result()
            say(f"alpha-horizon records: {done}/{len(ids)} stocks ({time.time() - t0:.0f}s)")
    return {"stocks": len(ids), "seconds": round(time.time() - t0, 1)}


# ------------------------------------------------------------------ records in memory
class R:
    __slots__ = ("a", "d", "end", "raw", "y", "yr", "x", "u", "xd", "sector", "orig")


def _end(d: str, h: int, cal_idx: Dict[str, int], cal: List[str]) -> str:
    i = cal_idx.get(d)
    return cal[min(len(cal) - 1, i + h)] if i is not None else d


def load(store, research) -> Tuple[Dict[str, List[R]], List[str]]:
    """{horizon: records} with within-date ranks and demeaned features; the feature names."""
    meta = store.kv_get(RESEARCH_KEY + ":build") or {}
    names = meta.get("features") or feature_names()
    cal = research.panel().calendar()
    idx = {d: i for i, d in enumerate(cal)}
    assets = {a["id"]: a for a in store.assets("EQUITY")}
    out: Dict[str, List[R]] = {}
    for lab, h in HORIZONS:
        recs: List[R] = []
        for b in store.lab_records(AHZ_VERSION, lab):
            m = assets.get(b["asset_id"]) or {}
            for d, raw, y, yr, x in b["rows"]:
                r = R()
                r.a, r.d, r.raw, r.y, r.yr = b["asset_id"], d, raw, y, yr
                r.x = [v if v is not None else 0.0 for v in x] + [0.0] * (len(names) - len(x))
                r.end = _end(d, h, idx, cal)
                r.sector = m.get("sector") or "Unclassified"
                r.orig = not (m.get("meta") or {}).get("expanded")
                recs.append(r)
        _prepare(recs)
        out[lab] = recs
    return out, names


def _prepare(recs: List[R]):
    by: Dict[str, List[R]] = {}
    for r in recs:
        by.setdefault(r.d, []).append(r)
    for d, rs in by.items():
        n = len(rs)
        order = sorted(range(n), key=lambda i: rs[i].y)
        for pos, i in enumerate(order):
            rs[i].u = (pos / (n - 1) - 0.5) * 2.0 if n > 1 else 0.0
        p = len(rs[0].x)
        mean = [sum(r.x[j] for r in rs) / n for j in range(p)]
        for r in rs:
            r.xd = [r.x[j] - mean[j] for j in range(p)]


# ------------------------------------------------------------------ hierarchical ridge on sufficient statistics
class S:
    __slots__ = ("p", "xx", "xy", "w")

    def __init__(self, p):
        self.p, self.w = p, 0.0
        self.xx = [0.0] * (p * p)
        self.xy = [0.0] * p

    def add(self, x, y, q):
        p = self.p
        self.w += q
        for i in range(p):
            xi = x[i] * q
            if xi:
                self.xy[i] += xi * y
                base = i * p
                for j in range(i, p):
                    self.xx[base + j] += xi * x[j]

    def merge(self, o):
        self.w += o.w
        self.xy = [a + b for a, b in zip(self.xy, o.xy)]
        self.xx = [a + b for a, b in zip(self.xx, o.xx)]


def _solve(A: List[List[float]], b: List[float]) -> List[float]:
    n = len(b)
    M = [A[i][:] + [b[i]] for i in range(n)]
    for k in range(n):
        piv = max(range(k, n), key=lambda i: abs(M[i][k]))
        M[k], M[piv] = M[piv], M[k]
        d = M[k][k] or 1e-12
        for i in range(k + 1, n):
            f = M[i][k] / d
            if f:
                for j in range(k, n + 1):
                    M[i][j] -= f * M[k][j]
    out = [0.0] * n
    for i in range(n - 1, -1, -1):
        out[i] = (M[i][n] - sum(M[i][j] * out[j] for j in range(i + 1, n))) / (M[i][i] or 1e-12)
    return out


def path(lab: str, sector: str, asset: str, depth: int) -> List[str]:
    full = ["global", f"g:{GROUP[lab]}", f"h:{lab}", f"h:{lab}|s:{sector}", f"h:{lab}|s:{sector}|a:{asset}"]
    return full[:depth]


def leaf_stats(data: Dict[str, List[R]], p: int, cutoff_end: str, date_lo: str = "0000") -> Dict[Tuple[str, str, str], S]:
    """(horizon, sector, asset) -> statistics of records with end < cutoff_end and date ≥ date_lo."""
    out: Dict[Tuple[str, str, str], S] = {}
    for lab, h in HORIZONS:
        q = 21.0 / max(21.0, float(h))
        for r in data.get(lab, []):
            if r.end < cutoff_end and r.d >= date_lo:
                k = (lab, r.sector, r.a)
                s = out.get(k)
                if s is None:
                    s = out[k] = S(p)
                s.add(r.xd, r.u, q)
    return out


def fit(leaves: Dict[Tuple[str, str, str], S], p: int, K: float, depth: int) -> Tuple[Dict[str, List[float]], List[float]]:
    nodes: Dict[str, S] = {}
    for (lab, sec, a), st in leaves.items():
        for node in path(lab, sec, a, depth):
            n = nodes.get(node)
            if n is None:
                n = nodes[node] = S(p)
            n.merge(st)
    g = nodes.get("global")
    if g is None or g.w <= 0:
        return {}, [1.0] * p
    rms = [math.sqrt(g.xx[i * p + i] / g.w) if g.xx[i * p + i] > 0 else 0.0 for i in range(p)]
    act = [i for i in range(p) if rms[i] > 1e-9]
    W: Dict[str, List[float]] = {}
    for node in sorted(nodes, key=lambda k: (k.count("|") + (0 if k == "global" else 1 if k.startswith("g:") else 2), k)):
        st = nodes[node]
        if node == "global":
            prior, lam = [0.0] * p, 50.0
        else:
            parent = "global" if node.startswith("g:") else f"g:{GROUP[node.split('|')[0][2:]]}" if "|" not in node else node.rsplit("|", 1)[0]
            prior, lam = W.get(parent, [0.0] * p), K
        A = [[st.xx[min(i, j) * p + max(i, j)] / (rms[i] * rms[j]) + (lam if i == j else 0.0) for j in act] for i in act]
        b = [st.xy[i] / rms[i] + lam * prior[i] for i in act]
        sol = _solve(A, b)
        w = [0.0] * p
        for k, i in enumerate(act):
            w[i] = sol[k]
        W[node] = w
    return W, rms


def score(W, rms, lab: str, r: R, depth: int) -> Optional[float]:
    for node in reversed(path(lab, r.sector, r.a, depth)):
        w = W.get(node)
        if w is not None:
            return sum(wi * xi / s for wi, xi, s in zip(w, r.x, rms) if s > 1e-9)
    return None


# ------------------------------------------------------------------ metrics
def _spearman(xs: List[float], ys: List[float]) -> Optional[float]:
    n = len(xs)
    if n < 3:
        return None

    def rk(v):
        o = sorted(range(n), key=lambda i: v[i])
        r = [0.0] * n
        for pos, i in enumerate(o):
            r[i] = float(pos)
        return r
    a, b = rk(xs), rk(ys)
    ma, mb = sum(a) / n, sum(b) / n
    sa = math.sqrt(sum((v - ma) ** 2 for v in a))
    sb = math.sqrt(sum((v - mb) ** 2 for v in b))
    return sum((x - ma) * (y - mb) for x, y in zip(a, b)) / (sa * sb) if sa > 0 and sb > 0 else None


def date_ics(rows: List[Tuple[R, float]], use_raw: bool = False) -> Dict[str, float]:
    by: Dict[str, list] = {}
    for r, s in rows:
        v = r.raw if use_raw else s
        if v is not None:
            by.setdefault(r.d, []).append((v, r.y))
    return {d: ic for d, v in by.items() if len(v) >= MIN_DATE_N
            for ic in [_spearman([a for a, _ in v], [b for _, b in v])] if ic is not None}


def tstat(vals: List[float], h: int) -> dict:
    n = len(vals)
    if n < 6:
        return {"mean": None, "t": None, "n": n}
    m = sum(vals) / n
    sd = math.sqrt(sum((v - m) ** 2 for v in vals) / (n - 1))
    n_eff = max(1.0, n * 21.0 / max(21.0, float(h)))
    return {"mean": m, "t": m / (sd / math.sqrt(n_eff)) if sd > 0 else None, "n": n, "n_eff": round(n_eff, 1)}


def paired(model: Dict[str, float], base: Dict[str, float], h: int) -> dict:
    ds = sorted(set(model) & set(base))
    return {"model": tstat([model[d] for d in ds], h), "production": tstat([base[d] for d in ds], h),
            "delta": tstat([model[d] - base[d] for d in ds], h), "dates": len(ds)}


# ------------------------------------------------------------------ the study
def _inner_choice(data, p: int, era_start: str) -> Dict[str, dict]:
    """Per horizon: {"star": (depth, K) with the best mean inner rank IC, depth: best K for that depth} — trained on
    outcomes matured before the inner window (the INNER_YEARS before the era), scored on the inner window's matured
    records."""
    y0 = int(era_start[:4]) - INNER_YEARS
    inner_lo = f"{y0}-01-01"
    leaves = leaf_stats(data, p, inner_lo)
    best: Dict[Tuple[str, int], Tuple[float, float]] = {}
    tests = {lab: [r for r in data.get(lab, []) if inner_lo <= r.d and r.end < era_start] for lab, _ in HORIZONS}
    for depth in (3, 4, 5):
        for K in K_GRID:
            W, rms = fit(leaves, p, K, depth)
            if not W:
                continue
            for lab, h in HORIZONS:
                ics = date_ics([(r, score(W, rms, lab, r, depth)) for r in tests[lab]])
                if len(ics) < 6:
                    continue
                m = sum(ics.values()) / len(ics)
                if (lab, depth) not in best or m > best[(lab, depth)][0]:
                    best[(lab, depth)] = (m, K)
    out: Dict[str, dict] = {}
    for (lab, depth), (m, K) in best.items():
        o = out.setdefault(lab, {})
        o[depth] = K
        if "star" not in o or m > o["_m"]:
            o["star"], o["_m"] = (depth, K), m
    for o in out.values():
        o.pop("_m", None)
    return out


def _calibrate(train: List[Tuple[R, float]]) -> Optional[List[dict]]:
    """Deciles of the within-date score percentile -> mean excess log return, 5% / 95% quantiles, share beating SPY."""
    pct = _pctiles(train)
    bins: List[List[float]] = [[] for _ in range(10)]
    for (r, _), q in zip(train, pct):
        if q is not None:
            bins[min(9, int(q * 10))].append(r.yr)
    if min(len(b) for b in bins) < 30:
        return None
    out = []
    for b in bins:
        b = sorted(b)
        n = len(b)
        out.append({"mean": sum(b) / n, "q05": b[int(0.05 * (n - 1))], "q95": b[int(0.95 * (n - 1))],
                    "p_beat": sum(1 for v in b if v > 0) / n, "n": n})
    return out


def _pctiles(rows: List[Tuple[R, float]]) -> List[Optional[float]]:
    by: Dict[str, List[int]] = {}
    for k, (r, s) in enumerate(rows):
        if s is not None:
            by.setdefault(r.d, []).append(k)
    out: List[Optional[float]] = [None] * len(rows)
    for d, ks in by.items():
        ks.sort(key=lambda k: rows[k][1])
        n = len(ks)
        for pos, k in enumerate(ks):
            out[k] = (pos + 0.5) / n
    return out


def _magnitude(preds: List[Tuple[R, float, dict]]) -> dict:
    """Out-of-sample: decile spread, calibration slope, interval coverage, Brier of P(beat) vs base rate."""
    if len(preds) < 200:
        return {"n": len(preds)}
    ys = [r.yr for r, _, _ in preds]
    mus = [c["mean"] for _, _, c in preds]
    mx, my = sum(mus) / len(mus), sum(ys) / len(ys)
    vx = sum((m - mx) ** 2 for m in mus)
    slope = sum((m - mx) * (y - my) for m, y in zip(mus, ys)) / vx if vx > 0 else None
    cover = sum(1 for (r, _, c) in preds if c["q05"] <= r.yr <= c["q95"]) / len(preds)
    base = sum(1 for y in ys if y > 0) / len(ys)
    brier = sum((c["p_beat"] - (1.0 if r.yr > 0 else 0.0)) ** 2 for r, _, c in preds) / len(preds)
    brier0 = sum((base - (1.0 if y > 0 else 0.0)) ** 2 for y in ys) / len(ys)
    top = [r.yr for r, _, c in preds if c.get("decile") == 9]
    bot = [r.yr for r, _, c in preds if c.get("decile") == 0]
    return {"n": len(preds), "slope": slope, "coverage_90": cover, "brier": brier, "brier_base": brier0,
            "spread_top_bottom": (sum(top) / len(top) - sum(bot) / len(bot)) if top and bot else None}


def study(store, research, progress=None) -> dict:
    say = progress or (lambda m: None)
    t0 = time.time()
    data, names = load(store, research)
    p = len(names)
    say(f"records: " + ", ".join(f"{lab} {len(v)}" for lab, v in data.items()) + f" ({time.time() - t0:.0f}s)")
    preds: Dict[str, Dict[str, List[Tuple[R, float]]]] = {v: {lab: [] for lab, _ in HORIZONS} for v in VARIANTS}
    mag: Dict[str, list] = {lab: [] for lab, _ in HORIZONS}
    choices = []
    eras = []
    for e, (a, b) in enumerate(TEST_ERAS):
        leaves = leaf_stats(data, p, a)
        if not leaves:
            continue
        ch = _inner_choice(data, p, a)
        choices.append({"era": ERA_LABEL[e], **{lab: {"depth": o["star"][0], "K": o["star"][1]} for lab, o in ch.items() if "star" in o}})
        fits = {}

        def get(depth, K):
            if (depth, K) not in fits:
                fits[(depth, K)] = fit(leaves, p, K, depth)
            return fits[(depth, K)]
        for lab, h in HORIZONS:
            test = [r for r in data[lab] if a <= r.d < b]
            train = [r for r in data[lab] if r.end < a]
            if not test:
                continue
            o = ch.get(lab) or {}
            for v, depth in DEPTH.items():
                W, rms = get(depth, o.get(depth, 1000.0))
                preds[v][lab] += [(r, score(W, rms, lab, r, depth)) for r in test]
            dstar, Kstar = o.get("star", (3, 1000.0))
            W, rms = get(dstar, Kstar)
            ps = [(r, score(W, rms, lab, r, dstar)) for r in test]
            preds["V*"][lab] += ps
            cal = _calibrate([(r, score(W, rms, lab, r, dstar)) for r in train])
            if cal:
                for (r, s), q in zip(ps, _pctiles(ps)):
                    if q is not None:
                        k = min(9, int(q * 10))
                        mag[lab].append((r, s, {**cal[k], "decile": k}))
        eras.append(ERA_LABEL[e])
        say(f"era {ERA_LABEL[e]} done ({time.time() - t0:.0f}s)")
    split_choice = _inner_choice(data, p, SPLIT)          # the split's own nested choice: nothing after 2018 informs it
    out = {"started": time.strftime("%Y-%m-%d %H:%M:%S"), "features": names, "choices": choices, "horizons": {}}
    for lab, h in HORIZONS:
        hz: dict = {"variants": {}}
        for v in VARIANTS:
            rows_all = [(r, s) for r, s in preds[v][lab] if s is not None]
            rows = [(r, s) for r, s in rows_all if r.raw is not None]        # identical records for the comparison
            ics = date_ics(rows)
            base_all = date_ics(rows, use_raw=True)
            res = {"walkforward": paired(ics, base_all, h), "eras": {}}
            for e, (a, b) in enumerate(TEST_ERAS):
                sub = {d: x for d, x in ics.items() if a <= d < b}
                bs = {d: x for d, x in base_all.items() if a <= d < b}
                pe = paired(sub, bs, h)
                if pe["dates"] >= MIN_ERA_DATES:
                    res["eras"][ERA_LABEL[e]] = pe
            orig = [(r, s) for r, s in rows if r.orig]
            res["original_universe"] = paired(date_ics(orig), date_ics([(r, 0.0) for r, _ in orig], use_raw=True), h)
            res["model_only"] = tstat(list(date_ics(rows_all).values()), h)     # every stock, incl. those without a production score
            hz["variants"][v] = res
        hz["split"] = _split(data, p, lab, h, split_choice)
        hz["magnitude"] = _magnitude(mag[lab])
        out["horizons"][lab] = hz
        say(f"{lab} evaluated")
    finalise(out)
    out["seconds"] = round(time.time() - t0, 1)
    return out


def _split(data, p, lab, h, choice) -> dict:
    depth, K = (choice.get(lab) or {}).get("star", (3, 1000.0))
    W, rms = fit(leaf_stats(data, p, SPLIT), p, K, depth)
    rows = [(r, score(W, rms, lab, r, depth)) for r in data[lab] if r.d >= SPLIT and r.raw is not None]
    return paired(date_ics(rows), date_ics(rows, use_raw=True), h)


def gates(hz: dict, v: str) -> dict:
    r = hz["variants"][v]
    wf = r["walkforward"]["delta"]
    eras = r["eras"]
    won = sum(1 for e in eras.values() if (e["delta"]["mean"] or 0) > 0)
    need = max(2, math.ceil(2 * len(eras) / 3))
    sp = (hz.get("split") or {}).get("delta") or {}
    orig = (r.get("original_universe") or {}).get("delta") or {}
    g = {"G1": (wf.get("t") or 0) >= 2 and (wf.get("mean") or 0) > 0, "eras_complete": len(eras), "eras_won": won,
         "G2": len(eras) >= 2 and won >= need and (sp.get("t") or 0) >= 1 if v == "V*" else len(eras) >= 2 and won >= need,
         "G3": (orig.get("mean") or 0) > 0, "t": wf.get("t"), "mean": wf.get("mean")}
    return g


def finalise(out: dict) -> dict:
    from .newinfo import benjamini_hochberg, _p_one_sided
    tests = []
    for lab, hz in out["horizons"].items():
        hz["gates"] = {v: gates(hz, v) for v in VARIANTS}
        for v in VARIANTS:
            tests.append((lab, v, _p_one_sided(hz["gates"][v]["t"])))
    rej = benjamini_hochberg([p for *_, p in tests], FDR_Q)
    for (lab, v, pv), ok in zip(tests, rej):
        g = out["horizons"][lab]["gates"][v]
        g["p"], g["fdr"] = pv, ok
        g["passed"] = bool(g["G1"] and g["G2"] and g["G3"] and ok)
    out["summary"] = {lab: {"status": "PASSED" if hz["gates"]["V*"]["passed"] else "NOT VALIDATED",
                            "passed_variants": [v for v in VARIANTS if hz["gates"][v]["passed"]]}
                      for lab, hz in out["horizons"].items()}
    return out


# ------------------------------------------------------------------ today's view (fit on every matured outcome)
def today(store, research, res: Optional[dict] = None) -> dict:
    """Per stock and horizon: percentile, expected excess return vs SPY, 90% range, P(beat SPY) — from the V* choice of
    the last era, fitted on every matured outcome. Research display: only horizons whose V* passed are marked VALIDATED."""
    res = res or store.kv_get(RESEARCH_KEY) or {}
    data, names = load(store, research)
    p = len(names)
    last = (res.get("choices") or [{}])[-1]
    leaves = leaf_stats(data, p, "2100-01-01")
    meta = store.kv_get(RESEARCH_KEY + ":build") or {}
    extra = [tuple(e) for e in meta.get("extra") or []]
    builder = None
    if extra:
        from .newinfo import Builder
        builder = Builder(research, store)
    cal = research.panel().calendar()
    t = len(cal) - 1
    spec = feature_spec()
    cur: Dict[str, List[float]] = {}
    for a in _stocks(store):
        z = research.zscores(a["id"])
        x = family_features(z, t, spec)
        if sum(v is not None for v in x) < MIN_PRESENT * len(x):
            continue
        ex = []
        for fam, feat in extra:
            f = builder.features(fam, a) if builder else None
            ex.append(((f or {}).get(feat) or [None] * len(cal))[t])
        cur[a["id"]] = [v if v is not None else 0.0 for v in x + ex]
        _forget(research, a["id"])
    sectors = {a["id"]: a.get("sector") or "Unclassified" for a in store.assets("EQUITY")}
    out = {"date": cal[t], "horizons": {}}
    for lab, h in HORIZONS:
        ch = last.get(lab) or {"depth": 3, "K": 1000.0}
        W, rms = fit(leaves, p, ch["K"], ch["depth"])
        train = [(r, score(W, rms, lab, r, ch["depth"])) for r in data[lab]]
        cal10 = _calibrate(train)
        rows = []
        for a, x in cur.items():
            r = R()
            r.a, r.x, r.sector, r.d = a, x, sectors.get(a, "Unclassified"), cal[t]
            rows.append((r, score(W, rms, lab, r, ch["depth"])))
        pct = _pctiles(rows)
        status = ((res.get("summary") or {}).get(lab) or {}).get("status", "NOT RUN")
        items = []
        for (r, s), q in zip(rows, pct):
            if q is None or not cal10:
                continue
            c = cal10[min(9, int(q * 10))]
            items.append({"asset": r.a, "pct": round(q * 100), "exp_excess": c["mean"], "lo": c["q05"], "hi": c["q95"], "p_beat": c["p_beat"]})
        items.sort(key=lambda i: -i["pct"])
        out["horizons"][lab] = {"status": status, "depth": ch["depth"], "K": ch["K"], "items": items}
    return out


# ------------------------------------------------------------------ report
def _f(v, d=4):
    return "—" if v is None else f"{v:+.{d}f}"


def _pc(v) -> str:
    return "—" if v is None else f"{v:.0%}"


def markdown(res: dict, view: Optional[dict] = None) -> str:
    L = ["# Shaffer Alpha across horizons (1M – 5Y) — horizon / sector / stock weights", "",
         f"Research only (protocol: SHAFFER_ALPHA_HORIZONS_PROTOCOL.md). Built {res.get('started')}. Features: "
         + ", ".join(res.get("features") or []) + ".", "",
         "Walk-forward rank IC of each variant and of production on identical records; Δ = variant − production, "
         "t over dates with n_eff = dates · 21 / h. 24M–60M are compared with production's 12M score. "
         "A horizon PASSES only with G1 (Δ t ≥ 2), G2 (Δ > 0 in ⅔ of complete eras, split t ≥ 1), G3 (Δ > 0 on the "
         "original, survivorship-free stocks) and BH FDR q = 0.10 across horizons × variants.", "",
         "| Horizon | Status | V* rank IC | Production | Δ (t) | Eras won | Split Δ t | Original-universe Δ | V1 Δ t | V2 Δ t | V3 Δ t |",
         "|---|---|---|---|---|---|---|---|---|---|---|"]
    for lab, hz in res.get("horizons", {}).items():
        v = hz["variants"]["V*"]["walkforward"]
        g = hz["gates"]["V*"]
        sp = (hz.get("split") or {}).get("delta") or {}
        og = (hz["variants"]["V*"].get("original_universe") or {}).get("delta") or {}
        L.append(f"| {lab} | {res['summary'][lab]['status']} | {_f(v['model']['mean'])} | {_f(v['production']['mean'])} | "
                 f"{_f(v['delta']['mean'])} ({_f(v['delta']['t'], 2)}) | {g['eras_won']}/{g['eras_complete']} | {_f(sp.get('t'), 2)} | "
                 f"{_f(og.get('mean'))} | " + " | ".join(_f(hz['gates'][x]['t'], 2) for x in ("V1", "V2", "V3")) + " |")
    L += ["", "## Chosen depth and shrinkage (nested, per era)", "", "| Era | " + " | ".join(lab for lab, _ in HORIZONS) + " |",
          "|---|" + "---|" * len(HORIZONS)]
    depth_name = {3: "horizon", 4: "sector", 5: "stock"}
    for c in res.get("choices", []):
        L.append(f"| {c['era']} | " + " | ".join(f"{depth_name.get(c[lab]['depth'], '?')}, K={c[lab]['K']:g}" if lab in c else "—"
                                                 for lab, _ in HORIZONS) + " |")
    L += ["", "## Magnitude (out of sample, V*)", "",
          "| Horizon | Records | Top − bottom decile (excess log return) | Calibration slope | 90% range coverage | Brier P(beat SPY) | Base-rate Brier |",
          "|---|---|---|---|---|---|---|"]
    for lab, hz in res.get("horizons", {}).items():
        m = hz.get("magnitude") or {}
        L.append(f"| {lab} | {m.get('n', 0)} | {_f(m.get('spread_top_bottom'), 3)} | {_f(m.get('slope'), 2)} | "
                 f"{_pc(m.get('coverage_90'))} | {_f(m.get('brier'), 4)} | {_f(m.get('brier_base'), 4)} |")
    if view:
        L += ["", f"## Today ({view.get('date')}) — top and bottom 10 per horizon (research display)", ""]
        for lab, hv in view["horizons"].items():
            it = hv["items"]
            if not it:
                continue
            L += [f"**{lab}** — {hv['status']}", "", "| Stock | Percentile | Expected excess vs SPY | 90% range | P(beat SPY) |", "|---|---|---|---|---|"]
            for i in it[:10] + it[-10:]:
                L.append(f"| {i['asset']} | {i['pct']} | {math.expm1(i['exp_excess']):+.1%} | [{math.expm1(i['lo']):+.0%}, {math.expm1(i['hi']):+.0%}] | {i['p_beat']:.0%} |")
            L.append("")
    return "\n".join(L) + "\n"


def run(db_path: str, progress=None) -> dict:
    from ..data.store import Store
    from .research import Research
    st = Store(db_path)
    try:
        r = Research(st)
        res = study(st, r, progress)
        st.kv_set(RESEARCH_KEY, res)
        return res
    finally:
        st.close()
