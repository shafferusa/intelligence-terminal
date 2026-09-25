"""ML Lab: the laboratory that evaluates and refines the Shaffer Score and the Shaffer Hedge.

It does not make trades or independent recommendations. It answers: is the production formula working, and would a
different weighting — possibly specialised by asset class, sector, industry, asset or regime — have done better,
out of sample, without look-ahead?

Data. Every point-in-time sweep (compute_shaffer_score) leaves, for every asset, horizon and weekly scored date, a
research record: the family scores known that day, the production raw score and each family's contribution, and —
once the horizon has passed — the realised return (raw and vol-scaled). Records are stored in `lab_records`.

Challenger weights. For a horizon, the index Σ_f w_f · x_f over the production families' scores x_f (standardised by
their training RMS) is fitted to the vol-scaled forward return by ridge regression at every node of a hierarchy,
each node shrunk toward its parent:

    w_node = (X'X·q + λ I)⁻¹ (X'y·q + λ w_parent),    q = 5 / max(5, h)  (overlapping weekly records → effective obs)
    Global → Asset class → Sector → Industry → Asset,  λ = SHRINK_K (fixed in advance; the global node shrinks to 0)

so an asset with little evidence inherits its industry's, sector's or class's weights. Variants: `global` (one set),
`class`, `hier` (all levels), `regime` (global → volatility regime). Challenger raw = 100·tanh(index / (2·sd of the
training index)).

Protocol (no look-ahead; the rules below were fixed before any result):
    discovery      weights fitted only on records whose outcome was known before CONFIRM_FROM
    confirmation   those frozen weights scored on records dated from CONFIRM_FROM (untouched)
    walk-forward   expanding yearly refits: year Y is predicted with records whose outcome ended before 1 January Y
    live shadow    challengers are recorded daily in the prediction ledger (model "shaffer:<id>") and graded later
Gates for promotion:
    G1  walk-forward before CONFIRM_FROM: challenger IC above production, paired date-clustered t ≥ 2
    G2  confirmation: challenger IC above production with paired t ≥ 1 and monotonicity not lower by more than 0.05
    G3  live shadow: at least LIVE_MIN graded live forecasts and a live IC not below production's on the same forecasts
Promotion also needs an explicit user action. It is recorded in the version registry; switching the scoring pipeline
to a promoted formula is a new score VERSION (history and calibration are recomputed with it) — never silent.
"""
from __future__ import annotations

import datetime as _dt
import math
import time
from typing import Dict, List, Optional, Tuple

from .. import shaffer_score as cfg

CONFIRM_FROM = "2018-01-01"
SHRINK_K = 200.0          # effective observations for a node to move halfway from its parent
LIVE_MIN = 60             # graded live forecasts required for the live-shadow gate
LAB_HORIZONS = [("1D", 1), ("1W", 5), ("1M", 21), ("3M", 63), ("6M", 126), ("12M", 252)]
VARIANTS = ["global", "class", "hier", "regime"]
REGISTRY_KEY = "formula:registry"
RESEARCH_KEY = "lab:research"

# industry for the stored equities (GICS-style); unmapped assets skip the level
INDUSTRY = {
    "NVDA": "Semiconductors", "AVGO": "Semiconductors", "AMD": "Semiconductors", "INTC": "Semiconductors", "QCOM": "Semiconductors",
    "TXN": "Semiconductors", "MSFT": "Software", "ORCL": "Software", "CRM": "Software", "ADBE": "Software", "AAPL": "Hardware",
    "CSCO": "Hardware", "IBM": "IT Services", "GOOGL": "Interactive Media", "META": "Interactive Media", "NFLX": "Entertainment",
    "DIS": "Entertainment", "T": "Telecom", "VZ": "Telecom", "AMZN": "Retail", "HD": "Retail", "COST": "Retail", "WMT": "Retail",
    "TSLA": "Automobiles", "MCD": "Restaurants", "NKE": "Apparel", "PG": "Household Products", "KO": "Beverages", "PEP": "Beverages",
    "XOM": "Oil & Gas", "CVX": "Oil & Gas", "JPM": "Banks", "BAC": "Banks", "GS": "Capital Markets", "V": "Payments", "MA": "Payments",
    "BRK-B": "Diversified Financials", "UNH": "Managed Care", "LLY": "Pharmaceuticals", "JNJ": "Pharmaceuticals",
    "PFE": "Pharmaceuticals", "MRK": "Pharmaceuticals", "ABBV": "Pharmaceuticals", "CAT": "Machinery", "BA": "Aerospace",
}


# ------------------------------------------------------------------ records
def save_records(store, run, data_version: str) -> int:
    """Persist a sweep's matured research records (called by the audit / lab-build workers)."""
    n = 0
    for lab, recs in (getattr(run, "lab_records", None) or {}).items():
        rows = [[run.cal[t], round(raw, 4), round(y, 5), round(yr, 6), {f: round(v, 4) for f, v in fam.items() if v},
                 {f: round(v, 5) for f, v in (c or {}).items() if v}, K] for (t, raw, y, yr, fam, c, K) in recs]
        store.put_lab_records(run.asset_id, lab, cfg.VERSION, data_version, rows)
        n += len(rows)
    return n


def families() -> List[str]:
    return list(cfg.FAMILIES)


def node_path(meta: dict, variant: str, regime: Optional[str] = None) -> List[str]:
    if variant == "global":
        return ["global"]
    if variant == "regime":
        return ["global"] + ([f"regime:{regime}"] if regime else [])
    cls = meta.get("asset_class") or "OTHER"
    out = ["global", f"class:{cls}"]
    if variant == "class":
        return out
    if meta.get("sector"):
        out.append(f"sector:{cls}:{meta['sector']}")
    if INDUSTRY.get(meta["id"]):
        out.append(f"industry:{INDUSTRY[meta['id']]}")
    out.append(f"asset:{meta['id']}")
    return out


def load(store, research, horizon: str) -> List[dict]:
    """Records for one horizon: asset, meta, date, end date, production raw, y (vol-scaled), yr, family vector, regime."""
    fams = families()
    metas = {a["id"]: a for a in store.assets()}
    cal = research.panel().calendar()
    idx = {d: i for i, d in enumerate(cal)}
    h = dict(LAB_HORIZONS)[horizon]
    vol = research.regimes().get("volatility") or []
    out = []
    for blob in store.lab_records(cfg.VERSION, horizon):
        meta = metas.get(blob["asset_id"]) or {"id": blob["asset_id"]}
        for d, raw, y, yr, fam, c, K in blob["rows"]:
            i = idx.get(d)
            if i is None:
                continue
            out.append({"asset": blob["asset_id"], "meta": meta, "date": d, "end": cal[min(len(cal) - 1, i + h)], "raw": raw, "y": y,
                        "yr": yr, "x": [fam.get(f, 0.0) for f in fams], "c": [(c or {}).get(f, 0.0) for f in fams],
                        "regime": vol[i] if i < len(vol) else None})
    out.sort(key=lambda r: (r["date"], r["asset"]))
    return out


# ------------------------------------------------------------------ hierarchical ridge
class _Stats:
    __slots__ = ("p", "xx", "xy", "n")

    def __init__(self, p):
        self.p, self.n = p, 0
        self.xx = [[0.0] * p for _ in range(p)]
        self.xy = [0.0] * p

    def add(self, x, y):
        self.n += 1
        for i in range(self.p):
            xi = x[i]
            if xi:
                self.xy[i] += xi * y
                row = self.xx[i]
                for j in range(i, self.p):
                    if x[j]:
                        row[j] += xi * x[j]

    def merge(self, o):
        self.n += o.n
        for i in range(self.p):
            self.xy[i] += o.xy[i]
            for j in range(i, self.p):
                self.xx[i][j] += o.xx[i][j]


def _solve(A, b):
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
    x = [0.0] * n
    for i in range(n - 1, -1, -1):
        x[i] = (M[i][n] - sum(M[i][j] * x[j] for j in range(i + 1, n))) / (M[i][i] or 1e-12)
    return x


def fit_tree(node_stats: Dict[str, _Stats], parents: Dict[str, str], h: int, lam: float = SHRINK_K) -> Tuple[Dict[str, list], list]:
    """Ridge toward the parent at every node (top-down). Returns ({node: weights on standardised scores}, rms per family)."""
    g = node_stats.get("global")
    p = len(families())
    if g is None or g.n == 0:
        return {}, [1.0] * p
    rms = [math.sqrt(g.xx[i][i] / g.n) if g.xx[i][i] > 0 else 0.0 for i in range(p)]
    q = 5.0 / max(5.0, float(h))
    act = [i for i in range(p) if rms[i] > 1e-9]
    W: Dict[str, list] = {}
    order = sorted(node_stats, key=lambda k: (k.count(":"), k != "global", k))
    for node in ["global"] + [k for k in order if k != "global"]:
        st = node_stats[node]
        prior = W.get(parents.get(node, ""), [0.0] * p) if node != "global" else [0.0] * p
        A = [[(st.xx[min(i, j)][max(i, j)] / (rms[i] * rms[j])) * q + (lam if i == j else 0.0) for j in act] for i in act]
        b = [st.xy[i] / rms[i] * q + lam * prior[i] for i in act]
        sol = _solve(A, b)
        w = [0.0] * p
        for k, i in enumerate(act):
            w[i] = sol[k]
        W[node] = w
    return W, rms


def _weights_for(W: Dict[str, list], path: List[str]) -> Optional[list]:
    for node in reversed(path):
        if node in W:
            return W[node]
    return None


def _index(w, rms, x) -> float:
    return sum(wi * xi / r for wi, xi, r in zip(w, x, rms) if r > 1e-9)


# ------------------------------------------------------------------ evaluation helpers
def _std_prod(rows: List[Tuple[str, str, float, float]]) -> Dict[str, List[Tuple[str, float]]]:
    """rows: (asset, date, x, y) → per asset [(date, standardised x·y)] (the audit's per-asset standardisation)."""
    by: Dict[str, list] = {}
    for a, d, x, y in rows:
        by.setdefault(a, []).append((d, x, y))
    out = {}
    for a, rs in by.items():
        if len(rs) < 20:
            continue
        xs, ys = [r[1] for r in rs], [r[2] for r in rs]
        mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
        sx = math.sqrt(sum((v - mx) ** 2 for v in xs) / len(xs))
        sy = math.sqrt(sum((v - my) ** 2 for v in ys) / len(ys))
        if sx <= 1e-12 or sy <= 1e-12:
            continue
        out[a] = [(d, (x - mx) / sx * (y - my) / sy) for d, x, y in rs]
    return out


def _clustered(prods: Dict[str, list], h: int) -> dict:
    by: Dict[int, List[float]] = {}
    for rs in prods.values():
        for d, v in rs:
            by.setdefault(_dt.date.fromisoformat(d).toordinal() // 7, []).append(v)
    if len(by) < 20:
        return {"ic": None, "t": None, "weeks": len(by)}
    ms = [sum(v) / len(v) for _, v in sorted(by.items())]
    n = len(ms)
    m = sum(ms) / n
    sd = math.sqrt(sum((v - m) ** 2 for v in ms) / max(1, n - 1))
    n_eff = n * 5.0 / max(5.0, float(h))
    return {"ic": m, "t": (m / (sd / math.sqrt(n_eff))) if sd > 0 and n_eff > 1 else None, "weeks": n, "n_eff": n_eff}


def _paired(pa: Dict[str, list], pb: Dict[str, list], h: int) -> dict:
    """Challenger − production, per record, pooled by week: is the challenger's IC higher, clustered by date?"""
    diff = {}
    for a in set(pa) & set(pb):
        mb = dict(pb[a])
        diff[a] = [(d, v - mb[d]) for d, v in pa[a] if d in mb]
    return _clustered(diff, h)


def _mono(xs: List[float], ys: List[float], k: int = 10) -> Optional[float]:
    if len(xs) < 5 * k:
        return None
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    size = len(order) // k
    means = [sum(ys[i] for i in order[j * size:(j + 1) * size]) / size for j in range(k)]
    rk = sorted(range(k), key=lambda j: means[j])
    r = [0] * k
    for pos, j in enumerate(rk):
        r[j] = pos
    return 1 - 6 * sum((j - r[j]) ** 2 for j in range(k)) / (k * (k * k - 1))


def metrics(rows: List[dict], key: str, h: int) -> dict:
    """IC (date-clustered), hit rate, monotonicity and top-minus-bottom decile of a score column over records."""
    if not rows:
        return {"n": 0}
    pr = _std_prod([(r["asset"], r["date"], r[key], r["y"]) for r in rows])
    cl = _clustered(pr, h)
    lean = [r for r in rows if abs(r[key]) >= 5]
    xs, ys = [r[key] for r in rows], [r["y"] for r in rows]
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    dec = max(1, len(order) // 10)
    spread = (sum(rows[i]["yr"] for i in order[-dec:]) - sum(rows[i]["yr"] for i in order[:dec])) / dec if len(order) >= 50 else None
    return {"n": len(rows), "assets": len(pr), "ic": cl["ic"], "t": cl["t"], "weeks": cl["weeks"],
            "hit": (sum(1 for r in lean if (r[key] > 0) == (r["yr"] > 0)) / len(lean)) if len(lean) >= 30 else None,
            "monotonicity": _mono(xs, ys), "top_minus_bottom": spread, "_prods": pr}


def _strip(m: dict) -> dict:
    return {k: v for k, v in m.items() if not k.startswith("_")}


# ------------------------------------------------------------------ one horizon, one variant
def study(recs: List[dict], h: int, variant: str) -> dict:
    fams = families()
    p = len(fams)
    paths = [node_path(r["meta"], variant, r["regime"]) for r in recs]
    parents = {}
    for pth in paths:
        for a, b in zip(pth, pth[1:]):
            parents[b] = a
    ends = sorted(set(r["end"][:4] for r in recs))
    # bucket stats by (path, year the outcome became known)
    buckets: Dict[Tuple[tuple, str], _Stats] = {}
    for r, pth in zip(recs, paths):
        key = (tuple(pth), r["end"][:4])
        st = buckets.get(key)
        if st is None:
            st = buckets[key] = _Stats(p)
        st.add(r["x"], r["y"])

    def tree_until(pred) -> Tuple[Dict[str, list], list, float]:
        node: Dict[str, _Stats] = {}
        for (pth, yr), st in buckets.items():
            if not pred(yr):
                continue
            for nd in pth:
                acc = node.get(nd)
                if acc is None:
                    acc = node[nd] = _Stats(p)
                acc.merge(st)
        W, rms = fit_tree(node, parents, h)
        return W, rms, node.get("global").n if node.get("global") else 0

    def scale_of(W, rms, train_rows) -> float:
        vals = [_index(_weights_for(W, pth) or [0.0] * p, rms, r["x"]) for r, pth in train_rows]
        if len(vals) < 30:
            return 1.0
        m = sum(vals) / len(vals)
        sd = math.sqrt(sum((v - m) ** 2 for v in vals) / len(vals))
        return 2 * sd if sd > 0 else 1.0

    # ---- discovery → confirmation (frozen)
    cy = CONFIRM_FROM[:4]
    Wd, rmsd, nd = tree_until(lambda yr: yr < cy)
    disc_rows = [(r, pth) for r, pth in zip(recs, paths) if r["end"] < CONFIRM_FROM]
    conf = [(r, pth) for r, pth in zip(recs, paths) if r["date"] >= CONFIRM_FROM]
    out = {"variant": variant, "discovery_records": nd}
    if Wd:
        sc = scale_of(Wd, rmsd, disc_rows)
        crows = []
        for r, pth in conf:
            w = _weights_for(Wd, pth)
            if w is None:
                continue
            crows.append({**r, "ch": 100 * math.tanh(_index(w, rmsd, r["x"]) / sc)})
        mp, mc = metrics(crows, "raw", h), metrics(crows, "ch", h)
        out["confirmation"] = {"production": _strip(mp), "challenger": _strip(mc),
                               "paired": _strip(_paired(mc.get("_prods") or {}, mp.get("_prods") or {}, h))}
    # ---- expanding yearly walk-forward over the whole history
    wf_rows = []
    years = sorted(set(r["date"][:4] for r in recs))
    by_year: Dict[str, list] = {}
    for r, pth in zip(recs, paths):
        by_year.setdefault(r["date"][:4], []).append((r, pth))
    for Y in years:
        W, rms, n = tree_until(lambda yr, Y=Y: yr < Y)
        if n < 200 or not W:
            continue
        train = [(r, pth) for r, pth in zip(recs, paths) if r["end"][:4] < Y]
        sc = scale_of(W, rms, train[-5000:])
        for r, pth in by_year[Y]:
            w = _weights_for(W, pth)
            if w is not None:
                wf_rows.append({**r, "ch": 100 * math.tanh(_index(w, rms, r["x"]) / sc)})
    pre = [r for r in wf_rows if r["date"] < CONFIRM_FROM]
    post = [r for r in wf_rows if r["date"] >= CONFIRM_FROM]
    wf = {}
    for part, rows in (("before", pre), ("from", post), ("all", wf_rows)):
        mp, mc = metrics(rows, "raw", h), metrics(rows, "ch", h)
        wf[part] = {"production": _strip(mp), "challenger": _strip(mc), "paired": _strip(_paired(mc.get("_prods") or {}, mp.get("_prods") or {}, h))}
    # where does it help? by asset class, sector and volatility regime (walk-forward rows)
    groups = {}
    for gname, keyf in (("class", lambda r: r["meta"].get("asset_class") or "OTHER"), ("sector", lambda r: r["meta"].get("sector") or "—"),
                        ("regime", lambda r: r.get("regime") or "—")):
        gs: Dict[str, list] = {}
        for r in wf_rows:
            gs.setdefault(keyf(r), []).append(r)
        groups[gname] = {k: {"production": metrics(v, "raw", h)["ic"], "challenger": metrics(v, "ch", h)["ic"], "n": len(v)}
                         for k, v in gs.items() if len(v) >= 200}
    wf["groups"] = groups
    out["walkforward"] = wf
    # ---- final weights on everything matured (the version the live shadow records)
    Wf, rmsf, nf = tree_until(lambda yr: True)
    out["final"] = {"records": nf, "scale": scale_of(Wf, rmsf, list(zip(recs, paths))[-5000:]), "rms": rmsf,
                    "weights": {k: v for k, v in Wf.items()}}
    out["discovery_weights"] = {k: v for k, v in Wd.items() if k == "global" or k.startswith(("class:", "regime:"))}
    # gates
    g1 = wf["before"]["paired"]
    g2 = (out.get("confirmation") or {})
    gp = g2.get("paired") or {}
    mono_ok = (g2.get("challenger") or {}).get("monotonicity") is not None and (g2.get("production") or {}).get("monotonicity") is not None and \
        g2["challenger"]["monotonicity"] >= g2["production"]["monotonicity"] - 0.05
    out["gates"] = {"G1_discovery": bool(g1.get("t") is not None and g1["t"] >= 2 and (g1.get("ic") or 0) > 0),
                    "G2_confirmation": bool(gp.get("t") is not None and gp["t"] >= 1 and (gp.get("ic") or 0) > 0 and mono_ok)}
    return out


def production_weights(recs: List[dict]) -> Dict[str, float]:
    """Production's effective say per family: mean share of Σ|contribution| (what the formula actually gives each family)."""
    fams = families()
    tot = [0.0] * len(fams)
    n = 0
    for r in recs:
        s = sum(abs(c) for c in r["c"])
        if s <= 0:
            continue
        n += 1
        for i, c in enumerate(r["c"]):
            tot[i] += abs(c) / s
    return {f: (tot[i] / n if n else 0.0) for i, f in enumerate(fams)}


def weight_share(w: list, rms: list) -> Dict[str, float]:
    """A challenger's weights as shares of Σ|w| on standardised scores, signed (the sign is what the data said)."""
    fams = families()
    s = sum(abs(v) for v in w) or 1.0
    return {f: w[i] / s for i, f in enumerate(fams)}


def family_research(recs: List[dict], h: int) -> Dict[str, dict]:
    """Each production family's own out-of-sample IC before / from CONFIRM_FROM (clustered)."""
    out = {}
    for i, f in enumerate(families()):
        row = {}
        for part, pred in (("before", lambda r: r["date"] < CONFIRM_FROM), ("from", lambda r: r["date"] >= CONFIRM_FROM)):
            rows = [(r["asset"], r["date"], r["x"][i], r["y"]) for r in recs if pred(r) and r["x"][i]]
            cl = _clustered(_std_prod(rows), h)
            row[part] = {"ic": cl["ic"], "t": cl["t"], "n": len(rows)}
        out[f] = row
    return out


def historical_performance(recs: List[dict], h: int) -> dict:
    """Production score accuracy by class, sector, regime and asset (every record is point in time)."""
    out = {"all": _strip(metrics(recs, "raw", h))}
    for gname, keyf in (("class", lambda r: r["meta"].get("asset_class") or "OTHER"), ("sector", lambda r: r["meta"].get("sector") or "—"),
                        ("regime", lambda r: r.get("regime") or "—"), ("asset", lambda r: r["asset"])):
        gs: Dict[str, list] = {}
        for r in recs:
            gs.setdefault(keyf(r), []).append(r)
        res = {}
        for k, v in gs.items():
            if len(v) < (100 if gname != "asset" else 60):
                continue
            m = metrics(v, "raw", h)
            res[k] = {"n": m["n"], "ic": m["ic"], "t": m["t"], "hit": m["hit"]}
        out[gname] = res
    return out


# ------------------------------------------------------------------ the full research run
def run(research, progress=None, horizons=None, variants=None) -> dict:
    say = progress or (lambda m: None)
    st = research.store
    t0 = time.time()
    res = {"started": time.strftime("%Y-%m-%d %H:%M:%S"), "score_version": cfg.VERSION, "confirm_from": CONFIRM_FROM, "shrink_k": SHRINK_K,
           "families": families(), "horizons": {}, "data_version": research.version()}
    for lab, h in (horizons or LAB_HORIZONS):
        say(f"{lab}: loading research records")
        recs = load(st, research, lab)
        if len(recs) < 1000:
            res["horizons"][lab] = {"records": len(recs), "reason": "not enough research records (run the lab build or the audit)"}
            continue
        hz = {"records": len(recs), "assets": len({r["asset"] for r in recs}), "first": recs[0]["date"], "last": recs[-1]["date"],
              "production_weights": production_weights([r for r in recs if r["end"] < CONFIRM_FROM]),
              "production_weights_recent": production_weights([r for r in recs if r["date"] >= CONFIRM_FROM]),
              "performance": historical_performance(recs, h), "families": family_research(recs, h), "variants": {}}
        for v in (variants or VARIANTS):
            say(f"{lab}: {v} weights — discovery, confirmation, walk-forward")
            s = study(recs, h, v)
            s["weights_share"] = {k: weight_share(w, s["final"]["rms"]) for k, w in s["final"]["weights"].items()
                                  if k == "global" or k.startswith(("class:", "regime:", "sector:"))
                                  or k in ("asset:SPY", "asset:NVDA", "asset:JPM", "asset:TLT", "asset:GLD", "asset:AAPL", "asset:XLK")}
            s["discovery_share"] = {k: weight_share(w, s["final"]["rms"]) for k, w in s.pop("discovery_weights").items()}
            hz["variants"][v] = s
        res["horizons"][lab] = hz
    res["seconds"] = round(time.time() - t0, 1)
    return res


def _horizon_worker(db_path: str, lab: str, variants=None) -> Tuple[str, dict]:
    from ..data.store import Store
    from .research import Research
    st = Store(db_path)
    try:
        r = Research(st)
        res = run(r, horizons=[(lab, dict(LAB_HORIZONS)[lab])], variants=variants)
        return lab, res["horizons"][lab]
    finally:
        st.close()


def run_parallel(db_path: str, workers: int = 3, progress=None, variants=None) -> dict:
    """The full research run, one process per horizon; stores the result (kv lab:research) and registers challengers."""
    from concurrent.futures import ProcessPoolExecutor, as_completed
    from ..data.store import Store
    from .research import Research
    say = progress or (lambda m: None)
    t0 = time.time()
    st = Store(db_path)
    r = Research(st)
    res = {"started": time.strftime("%Y-%m-%d %H:%M:%S"), "score_version": cfg.VERSION, "confirm_from": CONFIRM_FROM, "shrink_k": SHRINK_K,
           "families": families(), "horizons": {}, "data_version": r.version(), "records": st.lab_record_summary()}
    with ProcessPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(_horizon_worker, db_path, lab, variants) for lab, _ in LAB_HORIZONS]
        for k, f in enumerate(as_completed(futs)):
            lab, hz = f.result()
            res["horizons"][lab] = hz
            say(f"[{k + 1}/{len(LAB_HORIZONS)}] {lab}: {hz.get('records')} records")
    res["seconds"] = round(time.time() - t0, 1)
    res["challengers"] = register_challengers(st, res)
    res["signals"] = signal_research(r)
    store_result(st, res)
    st.close()
    return res


def store_result(store, res: dict):
    """The research result for the UI, without the per-node weights (those live with each version)."""
    slim = dict(res)
    slim["horizons"] = {}
    for lab, hz in (res.get("horizons") or {}).items():
        hz2 = dict(hz)
        hz2["variants"] = {v: {k: x for k, x in s.items() if k != "final"} | {"final_records": (s.get("final") or {}).get("records")}
                           for v, s in (hz.get("variants") or {}).items()}
        slim["horizons"][lab] = hz2
    store.kv_set(RESEARCH_KEY, slim)


def signal_research(research) -> dict:
    """Signal-level health at the latest refit, across every cached asset: how many assets see each signal HEALTHY,
    WEAKENING or DECAYING, and how many have it switched off (not significant after the multiple-testing control)."""
    from .research import BUNDLE_VERSION
    st = research.store
    suffix = f":{research.version()}:{cfg.VERSION}:{BUNDLE_VERSION}"
    out: Dict[str, Dict[str, dict]] = {}
    for k in st.kv_keys("shaffer2:"):
        if not k.endswith(suffix):
            continue
        s = st.kv_get(k) or {}
        for lab, ev in (s.get("evidence") or {}).items():
            for sg, e in (ev or {}).items():
                b = out.setdefault(lab, {}).setdefault(sg, {"family": cfg.ALL_FAMILY_OF.get(sg), "assets": 0, "active": 0, "HEALTHY": 0,
                                                             "WEAKENING": 0, "DECAYING": 0, "ic_sum": 0.0})
                b["assets"] += 1
                b["active"] += 1 if e.get("w") else 0
                if e.get("decay") in ("HEALTHY", "WEAKENING", "DECAYING"):
                    b[e["decay"]] += 1
                b["ic_sum"] += e.get("ic") or 0.0
    for lab, sigs in out.items():
        for b in sigs.values():
            b["mean_ic"] = b.pop("ic_sum") / b["assets"] if b["assets"] else None
            b["verdict"] = ("decaying" if b["DECAYING"] > max(b["HEALTHY"], b["WEAKENING"]) else
                            "useless (rarely significant)" if b["active"] < 0.1 * b["assets"] else "working" if b["HEALTHY"] >= b["DECAYING"] else "mixed")
    return out


# ------------------------------------------------------------------ versions and promotion
def registry(store) -> dict:
    reg = store.kv_get(REGISTRY_KEY) or {"versions": []}
    ids = {v["id"] for v in reg["versions"]}
    if f"shaffer-{cfg.VERSION}" not in ids:
        reg["versions"].append({"id": f"shaffer-{cfg.VERSION}", "kind": "shaffer", "status": "production", "introduced": "2026-09-25",
                                "description": "Shaffer v2.1: point-in-time evidence score (calibration bin-merge fix over v2.0)",
                                "formula": cfg.FORMULA, "families": {f: [s for s, _ in sg] for f, sg in cfg.FAMILIES.items()},
                                "applicability": {f: cfg.APPLICABILITY.get(f) for f in cfg.FAMILIES},
                                "horizon_fit": {f: cfg.HORIZON_FIT.get(f) for f in cfg.FAMILIES},
                                "training_cutoff": "expanding, point in time (every date uses only earlier data)", "validation": None})
    from ..hedge.history import VERSION as HV
    if HV not in ids:
        reg["versions"].append({"id": HV, "kind": "hedge", "status": "production", "introduced": "2026-09-25",
                                "description": "Shaffer Hedge: raw hedge from beta / DV01 / CS01 / FX notional / option Greeks; "
                                               "objective-specific capped ML adjustment only where verified",
                                "formula": "FinalHedge = RawHedge × (1 + 0.5·MLAdjustment), |MLAdjustment| ≤ 0.30", "validation": None})
    return reg


def _save_registry(store, reg):
    store.kv_set(REGISTRY_KEY, reg)


def register_challengers(store, research_result: dict) -> List[str]:
    """One challenger version per weighting variant (all horizons), with its frozen weights and validation. Existing
    challengers of the same variant and score version are replaced (a challenger is a research result, not history);
    promoted or retired versions are never touched."""
    reg = registry(store)
    made = []
    for v in VARIANTS:
        hzs = {lab: hz["variants"][v] for lab, hz in (research_result.get("horizons") or {}).items() if (hz.get("variants") or {}).get(v)}
        if not hzs:
            continue
        vid = f"shaffer-{cfg.VERSION}-{v}-exp"
        old = next((x for x in reg["versions"] if x["id"] == vid), None)
        if old and old.get("status") not in ("challenger", None):
            continue
        reg["versions"] = [x for x in reg["versions"] if x["id"] != vid]
        store.kv_set(f"formula:weights:{vid}", {lab: {"weights": s["final"]["weights"], "rms": s["final"]["rms"], "scale": s["final"]["scale"]}
                                                 for lab, s in hzs.items()})
        reg["versions"].append({
            "id": vid, "kind": "shaffer", "status": "challenger", "parent": f"shaffer-{cfg.VERSION}", "variant": v,
            "introduced": (old or {}).get("introduced") or time.strftime("%Y-%m-%d"),
            "live_shadow_from": (old or {}).get("live_shadow_from") or time.strftime("%Y-%m-%d"),
            "description": {"global": "one learned weight per family", "class": "weights by asset class, shrunk to global",
                            "hier": "weights by class → sector → industry → asset, each shrunk to its parent",
                            "regime": "weights by volatility regime, shrunk to global"}[v],
            "formula": r"SS^{ch}=100\tanh\left(\frac{\sum_f w_{f,node(a)}\,x_{f,a,t}/\mathrm{rms}_f}{2\,\mathrm{sd}}\right)",
            "training_cutoff": research_result.get("started"), "data_version": research_result.get("data_version"),
            "validation": {lab: {"gates": s["gates"], "confirmation": s.get("confirmation"), "walkforward": {k: s["walkforward"][k] for k in ("before", "from")}}
                           for lab, s in hzs.items()}})
        made.append(vid)
    _save_registry(store, reg)
    return made


def register_hedge_challenger(store, sizing: dict, trained: str) -> Optional[str]:
    """The sizing study's confirmed multiples as a Shaffer Hedge challenger: H = m · H_raw per group and objective metric."""
    from ..hedge.history import VERSION as HV
    mult = {g: {mt: r["multiple"] for mt, r in res.items() if r.get("passed")} for g, res in sizing.items()}
    mult = {g: v for g, v in mult.items() if v}
    reg = registry(store)
    vid = f"{HV}-sizing-exp"
    old = next((x for x in reg["versions"] if x["id"] == vid), None)
    if old and old.get("status") not in ("challenger", None):
        return None
    reg["versions"] = [x for x in reg["versions"] if x["id"] != vid]
    reg["versions"].append({"id": vid, "kind": "hedge", "status": "challenger", "parent": HV, "variant": "sizing",
                            "introduced": (old or {}).get("introduced") or time.strftime("%Y-%m-%d"),
                            "live_shadow_from": (old or {}).get("live_shadow_from") or time.strftime("%Y-%m-%d"),
                            "description": "the static rule resized by a constant per hedge group and objective, learned before "
                                           f"{CONFIRM_FROM[:4]} and confirmed after it",
                            "formula": "H = m_{group, objective} · H_raw", "multiples": mult, "training_cutoff": trained,
                            "validation": {"sizing": sizing}})
    _save_registry(store, reg)
    return vid


def hedge_live_gate(store, v: dict) -> dict:
    """G3 for a hedge challenger: on graded live recommendations whose legs belong to a resized group, the realised
    variance with the challenger's multiples against the hedge that was actually recommended."""
    from ..hedge.objml import metric_for
    mult = v.get("multiples") or {}
    n, better, vs, vc = 0, 0, 0.0, 0.0
    for k in store.kv_keys("hedgegrade:"):
        g = store.kv_get(k) or {}
        mt = metric_for(g.get("objective"))
        ms = {lid: (mult.get(grp) or {}).get(mt) for lid, grp in (g.get("groups") or {}).items()}
        if not any(ms.values()):
            continue
        u, legs = g.get("u") or [], g.get("legs") or {}
        v1 = sum((a + sum(legs[l][j] for l in legs)) ** 2 for j, a in enumerate(u))
        v2 = sum((a + sum((ms.get(l) or 1.0) * legs[l][j] for l in legs)) ** 2 for j, a in enumerate(u))
        n += 1; better += v2 < v1; vs += v1; vc += v2
    return {"graded": n, "required": LIVE_MIN, "challenger_better": better, "variance_ratio": (vc / vs) if vs else None,
            "passed": bool(n >= LIVE_MIN and vs > 0 and vc < vs)}


def challenger_score(store, vid: str, asset_meta: dict, horizon: str, fam_scores: Dict[str, float], regime: Optional[str] = None) -> Optional[float]:
    wts = (store.kv_get(f"formula:weights:{vid}") or {}).get(horizon)
    if not wts:
        return None
    variant = vid.split("-")[2] if vid.count("-") >= 3 else "hier"
    w = _weights_for(wts["weights"], node_path(asset_meta, variant, regime))
    if w is None:
        return None
    x = [fam_scores.get(f, 0.0) for f in families()]
    return 100 * math.tanh(_index(w, wts["rms"], x) / (wts.get("scale") or 1.0))


def record_shadow(research, asset_id: str) -> int:
    """Live shadow: today's challenger scores for an asset go into the append-only ledger (graded like production)."""
    from .tracking import record
    st = research.store
    reg = registry(st)
    chal = [v for v in reg["versions"] if v["kind"] == "shaffer" and v["status"] == "challenger"]
    if not chal:
        return 0
    full = research.shaffer_full(asset_id)
    panel = research.panel()
    last = panel.calendar()[-1]
    meta = st.asset(asset_id) or {"id": asset_id}
    vol = (research.regimes().get("volatility") or [None])[-1]
    n = 0
    hmap = dict(cfg.HORIZONS)
    for lab, r in (full.get("horizons") or {}).items():
        if r.get("raw") is None or r.get("date") != last or lab not in dict(LAB_HORIZONS):
            continue
        fam = {f["family"]: f.get("score") or 0.0 for f in r.get("families") or []}
        for v in chal:
            s = challenger_score(st, v["id"], meta, lab, fam, vol)
            if s is None:
                continue
            pid = record(st, panel, asset_id, last, f"shaffer:{v['id']}", v["id"], lab, hmap[lab], None, None, None, s,
                         {"production_raw": r.get("raw")}, source="shadow", raw=s)
            n += pid is not None
    return n


def live_gate(store, vid: str) -> dict:
    """G3: challenger vs production on the SAME graded live forecasts (asset, horizon, date)."""
    ch = {(p["asset_id"], p["horizon"], p["made_on"]): p for p in store.predictions() if p["model"] == f"shaffer:{vid}" and p.get("realized") is not None}
    pr = {(p["asset_id"], p["horizon"], p["made_on"]): p for p in store.predictions() if p["model"] == "shaffer" and p.get("realized") is not None}
    common = [k for k in ch if k in pr]
    out = {"graded": len(common), "required": LIVE_MIN}
    if len(common) < 10:
        out["passed"] = False
        return out

    def ic(src):
        xs = [(src[k].get("raw") if src[k].get("raw") is not None else src[k].get("score")) for k in common]
        ys = [src[k]["realized"] for k in common]
        n = len(xs)
        mx, my = sum(xs) / n, sum(ys) / n
        sx = math.sqrt(sum((a - mx) ** 2 for a in xs)); sy = math.sqrt(sum((b - my) ** 2 for b in ys))
        return sum((a - mx) * (b - my) for a, b in zip(xs, ys)) / (sx * sy) if sx > 0 and sy > 0 else None
    out["challenger_ic"], out["production_ic"] = ic(ch), ic(pr)
    out["passed"] = bool(len(common) >= LIVE_MIN and out["challenger_ic"] is not None and out["production_ic"] is not None
                         and out["challenger_ic"] >= out["production_ic"])
    return out


def stage(store, v: dict) -> dict:
    """Where a version stands in the promotion process, per horizon."""
    if v["status"] != "challenger":
        return {"stage": v["status"]}
    if v["kind"] == "hedge":
        sz = (v.get("validation") or {}).get("sizing") or {}
        confirmed = sum(1 for res in sz.values() for r in res.values() if r.get("passed"))
        live = hedge_live_gate(store, v)
        return {"stage": "rejected" if not confirmed else ("eligible for promotion" if live["passed"] else "live shadow"),
                "confirmed_multiples": confirmed, "live": live, "eligible_horizons": ["all"] if confirmed and live["passed"] else []}
    per = {}
    for lab, val in (v.get("validation") or {}).items():
        g = val.get("gates") or {}
        per[lab] = "discovery failed" if not g.get("G1_discovery") else "confirmation failed" if not g.get("G2_confirmation") else "live shadow"
    live = live_gate(store, v["id"])
    ready = [lab for lab, s in per.items() if s == "live shadow"] if live.get("passed") else []
    return {"stage": "eligible for promotion" if ready else ("live shadow" if any(s == "live shadow" for s in per.values()) else "rejected"),
            "by_horizon": per, "live": live, "eligible_horizons": ready}


def promote(store, vid: str, confirm: bool = False) -> dict:
    reg = registry(store)
    v = next((x for x in reg["versions"] if x["id"] == vid), None)
    if v is None:
        raise ValueError(f"no version {vid}")
    s = stage(store, v)
    if s["stage"] != "eligible for promotion":
        raise ValueError(f"{vid} is not eligible for promotion ({s['stage']}): it must pass discovery, confirmation and "
                         f"{LIVE_MIN} graded live-shadow forecasts")
    if not confirm:
        raise ValueError("promotion needs an explicit confirmation")
    for x in reg["versions"]:
        if x["kind"] == v["kind"] and x["status"] == "production":
            x["status"] = "retired"; x["retired"] = time.strftime("%Y-%m-%d")
    v["status"] = "production"; v["promoted"] = time.strftime("%Y-%m-%d"); v["promoted_horizons"] = s["eligible_horizons"]
    _save_registry(store, reg)
    store.audit("lab.promote", vid, {"horizons": s["eligible_horizons"]})
    return v
