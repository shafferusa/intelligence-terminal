"""The Shaffer System: the expected percentage total return of an asset over a horizon (SHAFFER_SYSTEM.md).

Shaffer Alpha (1M, 3M, 6M, 12M, 2Y, 3Y, 5Y) and Shaffer Directional (1D, 3D, 1W) share one learner. It forecasts
y = ln(1 + R), the log total return over the horizon, and the headline is the expected return 100(E[e^y] − 1). That
expectation is taken over the model's own calibrated residual distribution; the median 100(e^μ̂ − 1) is shown beside it.

**The hierarchy** is Global → asset class → product type → sector → industry → asset.
- The global level fits an elastic net, which selects the signals.
- The asset-class level fits an elastic-net deviation from global.
- Deeper nodes are ridge-shrunk toward their parent with strength K, so a thin node inherits its parent's equation.

**The candidate equation families** (the ML picks one per horizon × class, out of sample):

    E0 calibrated prior            [1, base]
    E1 calibrated production       E0 + evidence index / 100
    E2 elastic-net families        E1 + 11 family scores + log volatility + VIX, term spread, credit spread
    E3 limited interactions        E2 + momentum×vol, valuation×term, momentum×VIX, low-vol×VIX
    E4 threshold                   E2 + hinge terms beyond ±0.5 for momentum, valuation and mean reversion

**The walk-forward.** The eras and the nested choices are exactly as fixed in SHAFFER_SYSTEM.md §3. The adoption rule
is also from §3: learned where it is not worse than E0 and its calibration slope is positive, otherwise E0.
"""
from __future__ import annotations

import bisect
import hashlib
import json
import math
import os
import random
import time
from array import array
from typing import Dict, List, Optional, Tuple

from .. import shaffer_score as cfg

SYS_VERSION = "sys1"
RESEARCH_KEY = "lab:system"
LIVE_KEY = "system:live"
FROZEN_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "frozen", "shaffer_system.json")

HORIZONS = [("1D", 1, 5), ("3D", 3, 5), ("1W", 5, 5), ("1M", 21, 21), ("3M", 63, 21), ("6M", 126, 21), ("12M", 252, 21),
            ("24M", 504, 21), ("36M", 756, 21), ("60M", 1260, 21)]           # (label, sessions, step between records)
HMAP = {lab: h for lab, h, _ in HORIZONS}
STEP = {lab: s for lab, _, s in HORIZONS}
DIRECTIONAL = ("1D", "3D", "1W")
ALPHA = ("1M", "3M", "6M", "12M", "24M", "36M", "60M")
DISPLAY = {"24M": "2Y", "36M": "3Y", "60M": "5Y"}
RAW_LAB = {"1D": "1D", "3D": "1D", "1W": "1W", "1M": "1M", "3M": "3M", "6M": "6M", "12M": "12M", "24M": "12M", "36M": "12M", "60M": "12M"}
START = "1995-01-01"
WARMUP = 252
MIN_PRICES = 252 * 6
BASE_N0 = 2520.0
ERAS = [("2009-01-01", "2013-01-01"), ("2013-01-01", "2017-01-01"), ("2017-01-01", "2021-01-01"),
        ("2021-01-01", "2025-01-01"), ("2025-01-01", "2100-01-01")]
ERA_LABEL = ["2009–12", "2013–16", "2017–20", "2021–24", "2025–"]
SPLIT = "2018-01-01"
INNER_YEARS = 3
LAMBDAS = [0.001, 0.01, 0.1]
ALPHA_EN = 0.5
KS = [30.0, 300.0, 3000.0, 30000.0]
DEPTHS = [1, 2, 3, 4, 5, 6]
MIN_CLASS_INNER = 500
E0_CONFIG = ("E0", 0.001, 2, 300.0)          # the fixed calibrated-prior benchmark
NQ = 199                                     # quantiles of the standardised residuals (0.5% … 99.5%)
HINGE = 0.5

FAMS = ["Momentum", "Trend", "Mean Reversion", "Valuation", "Fundamental Quality", "Fundamental Growth",
        "Risk-Adjusted Performance", "Statistical / Time Series", "Relative Value", "Low volatility", "Low beta"]
COLS = (["intercept", "base rate (asset / class history)", "evidence index"] + FAMS +
        ["log volatility", "VIX", "term spread", "credit spread",
         "Momentum × volatility", "Valuation × term spread", "Momentum × VIX", "Low volatility × VIX",
         "Momentum (strong +)", "Momentum (strong −)", "Valuation (strong +)", "Valuation (strong −)",
         "Mean Reversion (strong +)", "Mean Reversion (strong −)"])
P = len(COLS)
_F = {f: 3 + i for i, f in enumerate(FAMS)}
FAMILY_COLS = {"E0": [0, 1], "E1": [0, 1, 2], "E2": list(range(0, 18)), "E3": list(range(0, 22)),
               "E4": list(range(0, 18)) + list(range(22, 28))}
FAMILY_NAMES = {"E0": "calibrated prior", "E1": "calibrated production", "E2": "elastic-net families",
                "E3": "limited interactions", "E4": "threshold"}

# ------------------------------------------------------------------ the hierarchy
COMMODITY_ETF_SECTORS = {"Agriculture", "Broad Commodities", "Precious Metals", "Industrial Metals"}
_ETF_TYPE = {"Fixed Income": "fixed income", "Convertibles": "fixed income", "International Equity": "international",
             "Leveraged / Inverse": "leveraged / inverse", "Broad Market": "broad market", "Factor": "factor",
             "Currency": "currency", "Crypto": "crypto", "Volatility": "volatility"}
G10 = {"EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF", "NZDUSD", "DXY"}


def product_type(a: dict) -> str:
    cls, sec, name = a.get("asset_class"), a.get("sector") or "", a.get("name") or ""
    if cls == "EQUITY":
        return "US common" if (a.get("country") or "United States") == "United States" else "foreign / ADR"
    if cls == "ETF":
        if sec in COMMODITY_ETF_SECTORS or any(w in name for w in ("Oil Fund", "Natural Gas Fund", "Commodity")):
            return "commodity"
        return _ETF_TYPE.get(sec, "sector")
    if cls in ("TREASURY", "CORP_BOND"):
        return "total-return index"
    if cls == "COMMODITY":
        return sec or "commodity"
    if cls == "FX":
        return "G10" if a.get("id") in G10 else "emerging"
    if cls == "INDEX":
        return "equity index"
    return "coin" if cls == "CRYPTO" else "other"


def node_path(a: dict) -> Tuple[str, ...]:
    """Six node keys, Global → asset; a missing level repeats its parent (it then adds nothing)."""
    cls = a.get("asset_class") or "?"
    k1 = f"c:{cls}"
    k2 = f"{k1}|p:{product_type(a)}"
    k3 = f"{k2}|s:{a['sector']}" if a.get("sector") else k2
    ind = (a.get("meta") or {}).get("industry")
    k4 = f"{k3}|i:{ind}" if ind else k3
    return ("global", k1, k2, k3, k4, f"{k4}|a:{a['id']}")


def benchmark_of(a: dict) -> Optional[str]:
    """The proper benchmark (SHAFFER_SYSTEM.md §1); None = cash (3-month T-bill)."""
    aid, cls, pt = a.get("id"), a.get("asset_class"), product_type(a)
    if aid in ("SPY", "AGG", "DBC"):
        return None
    if cls in ("EQUITY", "INDEX") or (cls == "ETF" and pt in ("broad market", "international", "sector", "factor", "leveraged / inverse")):
        return "SPY"
    if cls in ("TREASURY", "CORP_BOND") or (cls == "ETF" and pt == "fixed income"):
        return "AGG"
    if cls == "COMMODITY" or (cls == "ETF" and pt == "commodity"):
        return "DBC"
    return None


def group_of(a: dict) -> str:
    return node_path(a)[2]                        # the base rate shrinks toward the asset's product type


def eligible(store) -> List[dict]:
    return [a for a in store.assets() if a.get("asset_class") in cfg.CLASSES and store.price_count(a["id"]) >= MIN_PRICES]


# ------------------------------------------------------------------ records
def _logret(px):
    from .directional import _logret as lr
    return lr(px)


def _std(xs) -> Optional[float]:
    v = [x for x in xs if x is not None]
    if len(v) < 20:
        return None
    m = sum(v) / len(v)
    return math.sqrt(sum((x - m) ** 2 for x in v) / (len(v) - 1))


def _macro(panel) -> Dict[str, list]:
    out = {}
    for sid in ("VIXCLS", "DGS10", "DGS2", "DBAA", "DAAA", "DGS3MO"):
        try:
            out[sid] = panel.macro(sid)
        except Exception:  # noqa: BLE001
            out[sid] = [None] * len(panel.calendar())
    return out


def macro_at(mac: Dict[str, list], t: int) -> Tuple[float, float, float, float]:
    """(log VIX / 20, 10Y − 2Y, Baa − Aaa − 1, 3-month T-bill) at index t; missing → neutral."""
    def g(s, d=None):
        v = mac[s][t] if t < len(mac[s]) else None
        return d if v is None else v
    vix = g("VIXCLS")
    term = (g("DGS10", 0.0) - g("DGS2", 0.0)) if mac["DGS10"][t] is not None and mac["DGS2"][t] is not None else 0.0
    cred = (g("DBAA") - g("DAAA") - 1.0) if mac["DBAA"][t] is not None and mac["DAAA"][t] is not None else 0.0
    return (math.log(vix / 20.0) if vix and vix > 0 else 0.0, term, cred, g("DGS3MO", 0.0) or 0.0)


def group_prefix(store, research, metas: List[dict]) -> Dict[str, Tuple[list, list]]:
    """Per product-type group: prefix sums of daily log returns and of their count, over every member."""
    cal = research.panel().calendar()
    n = len(cal)
    acc: Dict[str, Tuple[list, list]] = {}
    for a in metas:
        g = group_of(a)
        s, c = acc.setdefault(g, ([0.0] * n, [0.0] * n))
        for i, r in enumerate(_logret(research.panel().series(a["id"]))):
            if r is not None:
                s[i] += r
                c[i] += 1.0
    out = {}
    for g, (s, c) in acc.items():
        ps, pc, a1, a2 = [0.0] * n, [0.0] * n, 0.0, 0.0
        for i in range(n):
            a1 += s[i]; a2 += c[i]
            ps[i], pc[i] = a1, a2
        out[g] = (ps, pc)
    return out


def _raw_by_date(store, a: str) -> Dict[str, Dict[str, float]]:
    out: Dict[str, Dict[str, float]] = {}
    for lab in sorted(set(RAW_LAB.values())):
        rows = []
        for b in store._q("SELECT data FROM lab_records WHERE version = ? AND horizon = ? AND asset_id = ?", (cfg.VERSION, lab, a)):
            import zlib
            rows = json.loads(zlib.decompress(b[0]).decode())
        out[lab] = {r[0]: r[1] for r in rows if r[1] is not None}
    return out


def features_at(z: Dict[str, list], t: int, spec) -> List[Optional[float]]:
    from .alphahz import family_features
    return family_features(z, t, spec)


def asset_rows(research, a: dict, gprefix, mac, raws: Dict[str, Dict[str, float]], bench_rets: Dict[Optional[str], list],
               t_list: Optional[List[int]] = None, matured_only: bool = True) -> Dict[str, list]:
    """Records for one asset: {lab: [[d, end, y, yb, base, raw, logvol, logrel, f1..f11, vix, term, credit, rf]]}."""
    from .alphahz import feature_spec
    panel = research.panel()
    cal = panel.calendar()
    n = len(cal)
    px = panel.series(a["id"])
    r = _logret(px)
    first = next((i for i, v in enumerate(px) if v), None)
    if first is None:
        return {}
    z = research.zscores(a["id"])
    spec = feature_spec()
    gs, gc = gprefix.get(group_of(a), ([0.0] * n, [0.0] * n))
    bench = benchmark_of(a)
    rb = bench_rets.get(bench)
    own_s, own_c = [0.0] * n, [0.0] * n
    s_, c_ = 0.0, 0.0
    for i in range(n):
        if r[i] is not None:
            s_ += r[i]; c_ += 1.0
        own_s[i], own_c[i] = s_, c_
    lo = max(first + WARMUP, bisect.bisect_left(cal, START))
    ts = t_list if t_list is not None else range(lo, n)
    out: Dict[str, list] = {lab: [] for lab, _, _ in HORIZONS}
    for t in ts:
        if t_list is None and t % 5 and t % 21:           # records on the calendar-aligned 5- and 21-session grids
            continue
        if t < lo or not px[t] or px[t] <= 0:
            continue
        sd = _std(r[max(0, t - 62):t + 1])
        if not sd:
            continue
        rel = _std([x - y for x, y in zip(r[max(0, t - 62):t + 1], rb[max(0, t - 62):t + 1]) if x is not None and y is not None]) if rb else sd
        fam = features_at(z, t, spec)
        if sum(v is not None for v in fam) < 3:
            continue
        vix, term, cred, rf = macro_at(mac, t)
        n_own = own_c[t]
        m_own = own_s[t] / n_own if n_own else 0.0
        m_grp = gs[t] / gc[t] if gc[t] else m_own
        w = n_own / (n_own + BASE_N0)
        mday = w * m_own + (1 - w) * m_grp
        for lab, h, step in HORIZONS:
            if t_list is None and (t % step):
                continue
            end = t + h
            if end >= n:
                if matured_only:
                    continue
                y = yb = None
                dend = None
            else:
                if not px[end] or px[end] <= 0:
                    continue
                y = math.log(px[end] / px[t])
                if bench is None:
                    yb = math.log(1.0 + rf / 100.0) * h / 252.0
                else:
                    pb = bench_rets.get(("px", bench))
                    yb = math.log(pb[end] / pb[t]) if pb and pb[t] and pb[end] and pb[t] > 0 and pb[end] > 0 else None
                dend = cal[end]
            raw = None
            braw = raws.get(RAW_LAB[lab]) or {}
            for k in range(0, 6):
                if t - k >= 0 and cal[t - k] in braw:
                    raw = braw[cal[t - k]]
                    break
            out[lab].append([cal[t], dend, None if y is None else round(y, 6), None if yb is None else round(yb, 6), round(mday * h, 7),
                             raw if raw is None else round(raw, 3), round(math.log(sd * math.sqrt(252) / 0.2), 5),
                             round(math.log(rel * math.sqrt(252) / 0.2), 5) if rel else None,
                             *[None if v is None else round(v, 4) for v in fam], round(vix, 4), round(term, 4), round(cred, 4), round(rf, 4)])
    return out


def _bench_series(research) -> Dict:
    panel = research.panel()
    out: Dict = {None: None}
    for b in ("SPY", "AGG", "DBC"):
        try:
            p = panel.series(b)
        except Exception:  # noqa: BLE001
            p = None
        out[b] = _logret(p) if p else None
        out[("px", b)] = p
    return out


def _build_worker(db_path: str, ids: List[str], gprefix) -> int:
    from ..data.store import Store
    from .alphahz import _forget
    from .research import Research
    st = Store(db_path)
    try:
        r = Research(st)
        mac = _macro(r.panel())
        bench = _bench_series(r)
        dv = r.version()
        done = 0
        for aid in ids:
            a = st.asset(aid)
            try:
                recs = asset_rows(r, a, gprefix, mac, _raw_by_date(st, aid), bench)
            except Exception as e:  # noqa: BLE001 — one asset never stops the build
                print(f"system records {aid}: {type(e).__name__}: {e}", flush=True)
                recs = {}
            for lab, rows in recs.items():
                if rows:
                    st.put_lab_records(aid, lab, SYS_VERSION, dv, rows)
            _forget(r, aid)
            done += 1
        return done
    finally:
        st.close()


def build(db_path: str, workers: int = 2, progress=None) -> dict:
    """Records for every eligible asset (lab_records version sys1). The product-type base rates are computed once."""
    from concurrent.futures import ProcessPoolExecutor, as_completed
    from ..data.store import Store
    from .research import Research
    say = progress or (lambda m: None)
    st = Store(db_path)
    try:
        r = Research(st)
        metas = eligible(st)
        t0 = time.time()
        gp = group_prefix(st, r, metas)
        say(f"base rates for {len(gp)} product types ({time.time() - t0:.0f}s)")
        st._write(lambda conn: conn.execute("DELETE FROM lab_records WHERE version = ?", (SYS_VERSION,)))
        st.kv_set(RESEARCH_KEY + ":build", {"assets": len(metas), "started": time.strftime("%Y-%m-%d %H:%M:%S"), "version": SYS_VERSION})
    finally:
        st.close()
    ids = [a["id"] for a in metas]
    k = max(1, workers * 6)
    chunks = [ids[i::k] for i in range(k)]
    done = 0
    if workers <= 1:
        for c in chunks:
            done += _build_worker(db_path, c, gp)
            say(f"system records {done}/{len(ids)} ({time.time() - t0:.0f}s)")
    else:
        with ProcessPoolExecutor(max_workers=workers) as ex:
            futs = [ex.submit(_build_worker, db_path, c, gp) for c in chunks if c]
            for f in as_completed(futs):
                done += f.result()
                say(f"system records {done}/{len(ids)} ({time.time() - t0:.0f}s)")
    return {"assets": len(ids), "seconds": round(time.time() - t0, 1)}


# ------------------------------------------------------------------ records in memory
class Rec:
    __slots__ = ("a", "cls", "path", "bench", "d", "end", "y", "yb", "v", "s", "srel", "q", "orig")


def design(row: list) -> array:
    """The full design vector (every family's columns) from a stored row; missing family scores → 0."""
    d, end, y, yb, base, raw, lv, lrel = row[:8]
    f = [0.0 if x is None else x for x in row[8:19]]
    vix, term, cred = row[19], row[20], row[21]
    mom, val, mr, lowv = f[0], f[3], f[2], f[9]
    v = [1.0, base, (raw or 0.0) / 100.0] + f + [lv, vix, term, cred,
                                                 mom * lv, val * term, mom * vix, lowv * vix,
                                                 max(0.0, mom - HINGE), max(0.0, -mom - HINGE), max(0.0, val - HINGE),
                                                 max(0.0, -val - HINGE), max(0.0, mr - HINGE), max(0.0, -mr - HINGE)]
    return array("d", v)


def load(store, lab: str) -> List[Rec]:
    h, step = HMAP[lab], STEP[lab]
    metas = {a["id"]: a for a in store.assets()}
    out: List[Rec] = []
    for b in store.lab_records(SYS_VERSION, lab):
        a = metas.get(b["asset_id"])
        if not a:
            continue
        path, cls, bench = node_path(a), a["asset_class"], benchmark_of(a)
        orig = not (a.get("meta") or {}).get("expanded")
        for row in b["rows"]:
            if row[2] is None:
                continue
            r = Rec()
            r.a, r.cls, r.path, r.bench, r.orig = a["id"], cls, path, bench, orig
            r.d, r.end, r.y, r.yb = row[0], row[1], row[2], row[3]
            r.v = design(row)
            r.s = math.exp(row[6]) * 0.2 * math.sqrt(h / 252.0)
            r.srel = math.exp(row[7]) * 0.2 * math.sqrt(h / 252.0) if row[7] is not None else r.s
            r.q = min(1.0, step / h)
            out.append(r)
    return out


# ------------------------------------------------------------------ sufficient statistics and fits
class Stats:
    __slots__ = ("A", "b", "n")

    def __init__(self):
        self.A = array("d", [0.0]) * (P * P)
        self.b = array("d", [0.0]) * P
        self.n = 0.0

    def add(self, v, y, q):
        A, b = self.A, self.b
        self.n += q
        for i in range(P):
            vi = v[i] * q
            if vi:
                b[i] += vi * y
                base = i * P
                for j in range(i, P):
                    vj = v[j]
                    if vj:
                        A[base + j] += vi * vj

    def merge(self, o: "Stats"):
        self.n += o.n
        for i in range(P * P):
            self.A[i] += o.A[i]
        for i in range(P):
            self.b[i] += o.b[i]

    def a(self, i, j):
        return self.A[i * P + j] if i <= j else self.A[j * P + i]


def leaf_stats(recs: List[Rec], cuts: List[str]) -> Dict[Tuple[str, int], Stats]:
    """(asset, bucket) → statistics; bucket = how many cut dates are ≤ the outcome's end date."""
    out: Dict[Tuple[str, int], Stats] = {}
    for r in recs:
        k = (r.a, bisect.bisect_right(cuts, r.end))
        s = out.get(k)
        if s is None:
            s = out[k] = Stats()
        s.add(r.v, r.y, r.q)
    return out


def node_stats(leaves: Dict[Tuple[str, int], Stats], paths: Dict[str, tuple], max_bucket: int) -> Dict[str, Stats]:
    """Statistics of every node from the leaves whose outcomes ended before cut index max_bucket (bucket ≤ max_bucket)."""
    per_asset: Dict[str, Stats] = {}
    for (a, bk), s in leaves.items():
        if bk <= max_bucket:
            t = per_asset.get(a)
            if t is None:
                t = per_asset[a] = Stats()
            t.merge(s)
    nodes: Dict[str, Stats] = {}
    for a, s in per_asset.items():
        for key in dict.fromkeys(paths[a]):
            t = nodes.get(key)
            if t is None:
                t = nodes[key] = Stats()
            t.merge(s)
    return nodes


def _en(G: List[List[float]], c: List[float], prior: List[float], lam: float, alpha: float, iters: int = 300) -> List[float]:
    """Elastic net on normal equations (per-observation scale), intercept (index 0) unpenalised, penalty on the
    deviation from `prior`: min ½ wᵀGw − cᵀw + λ[α|w − p|₁ + ½(1−α)|w − p|²]."""
    k = len(c)
    w = list(prior)
    for _ in range(iters):
        mx = 0.0
        for j in range(k):
            gj = G[j]
            rho = c[j] - sum(gj[m] * w[m] for m in range(k) if m != j)
            if j == 0:
                new = rho / gj[j] if gj[j] > 1e-12 else 0.0
            else:
                x = rho - gj[j] * prior[j]
                sh = math.copysign(max(0.0, abs(x) - lam * alpha), x)
                new = prior[j] + sh / (gj[j] + lam * (1 - alpha))
            mx = max(mx, abs(new - w[j]))
            w[j] = new
        if mx < 1e-8:
            break
    return w


def fit(nodes: Dict[str, Stats], family: str, lam: float, K: float, depth: int) -> Optional[dict]:
    """Top-down: global elastic net, class elastic-net deviation, deeper ridge toward the parent (standardised columns)."""
    from .alphahz import _solve
    g = nodes.get("global")
    cols = FAMILY_COLS[family]
    if g is None or g.n < 200:
        return None
    rms = [1.0] + [math.sqrt(g.a(i, i) / g.n) if g.a(i, i) > 0 else 0.0 for i in cols[1:]]
    act = [k for k, i in enumerate(cols) if k == 0 or rms[k] > 1e-9]
    W: Dict[str, List[float]] = {}
    N: Dict[str, float] = {}
    order = sorted(nodes, key=lambda key: (0 if key == "global" else key.count("|") + 1, key))
    for key in order:
        lvl = 0 if key == "global" else key.count("|") + 1
        if lvl >= depth:
            continue
        st = nodes[key]
        if key == "global":
            parent = [0.0] * len(cols)
        else:
            pk = "global" if lvl == 1 else key.rsplit("|", 1)[0]
            if pk not in W:
                continue
            parent = W[pk]
        A = [[st.a(cols[i], cols[j]) / (rms[i] * rms[j]) if rms[i] and rms[j] else 0.0 for j in range(len(cols))] for i in range(len(cols))]
        b = [st.b[cols[i]] / rms[i] if rms[i] else 0.0 for i in range(len(cols))]
        if lvl <= 1:
            nn = max(st.n, 1.0)
            G = [[A[i][j] / nn for j in act] for i in act]
            c = [b[i] / nn for i in act]
            sol = _en(G, c, [parent[i] for i in act], lam, ALPHA_EN)
        else:
            M = [[A[i][j] + (K if i == j else 0.0) for j in act] for i in act]
            rhs = [b[i] + K * parent[i] for i in act]
            sol = _solve(M, rhs)
        w = [0.0] * len(cols)
        for k2, i in enumerate(act):
            w[i] = sol[k2]
        W[key] = w
        N[key] = st.n
    return {"family": family, "lam": lam, "K": K, "cols": cols, "rms": rms, "W": W, "n": N}


def predict(m: dict, r, depth: int) -> Optional[float]:
    W = m["W"]
    for key in reversed(r.path[:depth]):
        w = W.get(key)
        if w is not None:
            v, rms = r.v, m["rms"]
            return sum(wi * v[c] / s for wi, c, s in zip(w, m["cols"], rms) if s)
    return None


def node_used(m: dict, path: tuple, depth: int) -> Optional[str]:
    return next((k for k in reversed(path[:depth]) if k in m["W"]), None)


# ------------------------------------------------------------------ residual distribution → ranges, probabilities
def _quantiles(xs: List[float]) -> List[float]:
    xs = sorted(xs)
    n = len(xs)
    return [xs[min(n - 1, int(((k + 0.5) / NQ) * n))] for k in range(NQ)]


def resid_model(pairs: List[Tuple[float, float, float]]) -> Optional[dict]:
    """From (y, μ̂, s) triples: c = std of (y − μ̂)/s and the quantiles of the unit-variance standardised residual."""
    e = [(y - m) / s for y, m, s in pairs if s and s > 0]
    if len(e) < 200:
        return None
    mu = sum(e) / len(e)
    c = math.sqrt(sum((x - mu) ** 2 for x in e) / (len(e) - 1))
    if c <= 0:
        return None
    return {"c": c, "q": [round(x / c, 5) for x in _quantiles([x - mu for x in e])], "bias": mu, "n": len(e)}


def distribution(mu: float, shat: float, rm: dict) -> dict:
    """E[R], median, P(R > 0), 50% / 90% ranges and E|R| of R = e^y − 1 with y = μ̂ + ŝ·e, e from the residual quantiles."""
    q = rm["q"]
    vals = [math.exp(mu + shat * e) - 1.0 for e in q]
    er = sum(vals) / len(vals)
    pos = sum(1 for x in vals if x > 0) / len(vals)
    pick = lambda p: vals[min(len(vals) - 1, max(0, int(p * len(vals))))]  # noqa: E731
    return {"expected": er, "median": math.exp(mu) - 1.0, "p_pos": pos, "r50": [pick(0.25), pick(0.75)], "r90": [pick(0.05), pick(0.95)],
            "abs_move": sum(abs(x) for x in vals) / len(vals)}


def p_beat(mu: float, mu_b: float, srel: float, rm: dict) -> float:
    """P(y − y_b > 0) with y − y_b = (μ̂ − μ̂_b) + ŝ_rel·e."""
    q = rm["q"]
    return sum(1 for e in q if (mu - mu_b) + srel * e > 0) / len(q)


# ------------------------------------------------------------------ nested choice, walk-forward
def _add_years(d: str, k: int) -> str:
    return f"{int(d[:4]) + k:04d}{d[4:]}"


def _mse_by_class(m: dict, recs: List[Rec], depth: int) -> Dict[str, Tuple[float, int]]:
    acc: Dict[str, List[float]] = {}
    for r in recs:
        p = predict(m, r, depth)
        if p is None:
            continue
        a = acc.setdefault(r.cls, [0.0, 0])
        a[0] += (r.y - p) ** 2
        a[1] += 1
    return {c: (s / n, n) for c, (s, n) in acc.items() if n}


def choose(leaves, paths, cuts, recs: List[Rec], cut: str, say) -> Tuple[Dict[str, tuple], Dict[tuple, dict]]:
    """The nested choice for training that ends at `cut`: inner window = the INNER_YEARS before it. Returns
    {class: (family, λ, depth, K)} and the inner fits keyed by (family, λ, K)."""
    inner_lo = _add_years(cut, -INNER_YEARS)
    kb_inner = bisect.bisect_right(cuts, inner_lo) - 1       # buckets ending before inner_lo
    nodes = node_stats(leaves, paths, kb_inner)
    inner = [r for r in recs if inner_lo <= r.d and r.end < cut]
    classes = sorted({r.cls for r in inner})
    # step 1: family × λ at depth 2 (K fixed)
    lam_of: Dict[str, float] = {}
    fam_mse: Dict[str, Dict[str, Tuple[float, int]]] = {}
    for fam in FAMILY_COLS:
        best = None
        for lam in (LAMBDAS if fam not in ("E0", "E1") else LAMBDAS[:1]):
            m = fit(nodes, fam, lam, 300.0, 2)
            if not m:
                continue
            mc = _mse_by_class(m, inner, 2)
            tot = sum(v[0] * v[1] for v in mc.values()) / max(1, sum(v[1] for v in mc.values()))
            if best is None or tot < best[0]:
                best = (tot, lam, mc)
        if best:
            lam_of[fam], fam_mse[fam] = best[1], best[2]
    if not fam_mse:
        return {}, {}
    glob = min(fam_mse, key=lambda f: sum(v[0] * v[1] for v in fam_mse[f].values()) / max(1, sum(v[1] for v in fam_mse[f].values())))
    fam_of: Dict[str, str] = {}
    for c in classes:
        cand = [(fam_mse[f][c][0], f) for f in fam_mse if c in fam_mse[f]]
        n_c = max((fam_mse[f][c][1] for f in fam_mse if c in fam_mse[f]), default=0)
        fam_of[c] = min(cand)[1] if cand and n_c >= MIN_CLASS_INNER else glob
    # step 2: depth × K for each family in use
    fits: Dict[tuple, dict] = {}
    choice: Dict[str, tuple] = {}
    for fam in sorted(set(fam_of.values())):
        best: Dict[str, tuple] = {}
        for K in KS:
            m = fit(nodes, fam, lam_of[fam], K, 6)
            if not m:
                continue
            fits[(fam, lam_of[fam], K)] = m
            for D in DEPTHS:
                mc = _mse_by_class(m, [r for r in inner if fam_of.get(r.cls) == fam], D)
                for c, (v, n) in mc.items():
                    if c not in best or v < best[c][0]:
                        best[c] = (v, D, K)
        for c in classes:
            if fam_of[c] == fam and c in best:
                choice[c] = (fam, lam_of[fam], best[c][1], best[c][2])
    say(f"  choice at {cut}: " + ", ".join(f"{c}={v[0]}/d{v[2]}/K{int(v[3])}" for c, v in sorted(choice.items())))
    return choice, fits


def _resid_from(fits_inner, choice, recs_inner, preds_bench=None) -> Dict[str, dict]:
    """Residual models per class from the inner-window predictions of the chosen configuration (honest residuals)."""
    by: Dict[str, List[Tuple[float, float, float]]] = {}
    for r in recs_inner:
        cf = choice.get(r.cls)
        if not cf:
            continue
        m = fits_inner.get((cf[0], cf[1], cf[3]))
        p = predict(m, r, cf[2]) if m else None
        if p is not None:
            by.setdefault(r.cls, []).append((r.y, p, r.s))
    return {c: rm for c, v in by.items() if (rm := resid_model(v))}


def _paired_t(by_date: Dict[str, List[float]], h: int, step: int) -> dict:
    ms = [sum(v) / len(v) for _, v in sorted(by_date.items()) if v]
    n = len(ms)
    if n < 10:
        return {"mean": None, "t": None, "dates": n}
    m = sum(ms) / n
    sd = math.sqrt(sum((x - m) ** 2 for x in ms) / (n - 1))
    adj = math.sqrt(min(1.0, step / h))                   # overlapping outcome windows
    return {"mean": m, "t": (m / (sd / math.sqrt(n)) * adj) if sd > 0 else None, "dates": n, "n_eff": round(n * min(1.0, step / h), 1)}


def _spearman(x: List[float], y: List[float]) -> Optional[float]:
    from .livexs import rank_ic
    return rank_ic(x, y)


def study_horizon(store, lab: str, progress=None) -> dict:
    say = progress or (lambda m: None)
    h, step = HMAP[lab], STEP[lab]
    t0 = time.time()
    recs = load(store, lab)
    if len(recs) < 5000:
        return {"records": len(recs), "skipped": "too few records"}
    paths = {r.a: r.path for r in recs}
    cuts = sorted({c for a, _ in ERAS for c in (a, _add_years(a, -INNER_YEARS))} | {SPLIT, _add_years(SPLIT, -INNER_YEARS)})
    last_end = max(r.end for r in recs)
    fin_cut = "2100-01-01"
    fin_inner = _add_years(last_end[:4] + "-01-01", -INNER_YEARS + 1)
    cuts = sorted(set(cuts) | {fin_inner})
    say(f"{lab}: {len(recs)} records, {len(paths)} assets ({time.time() - t0:.0f}s)")
    leaves = leaf_stats(recs, cuts)
    say(f"{lab}: statistics ({time.time() - t0:.0f}s)")
    base_rate: Dict[str, float] = {}
    rows = []                                           # (rec, μ̂, μ̂_E0, dist-inputs)
    eras_done, choices = [], []
    for e, (a, b) in enumerate(ERAS):
        test = [r for r in recs if a <= r.d < b]
        if not test:
            continue
        choice, fits_inner = choose(leaves, paths, cuts, recs, a, say)
        if not choice:
            continue
        inner_lo = _add_years(a, -INNER_YEARS)
        inner = [r for r in recs if inner_lo <= r.d and r.end < a]
        rm = _resid_from(fits_inner, choice, inner)
        kb = bisect.bisect_right(cuts, a) - 1
        nodes = node_stats(leaves, paths, kb)
        fits = {k: fit(nodes, k[0], k[1], k[2], 6) for k in {(v[0], v[1], v[3]) for v in choice.values()}}
        e0 = fit(nodes, E0_CONFIG[0], E0_CONFIG[1], E0_CONFIG[3], E0_CONFIG[2])
        train = [r for r in recs if r.end < a]
        for c in {r.cls for r in train}:
            ys = [r.y > 0 for r in train if r.cls == c]
            base_rate[c] = sum(ys) / len(ys)
        mu_of: Dict[Tuple[str, str], float] = {}
        tmp = []
        for r in test:
            cf = choice.get(r.cls)
            if not cf or not fits.get((cf[0], cf[1], cf[3])):
                continue
            mu = predict(fits[(cf[0], cf[1], cf[3])], r, cf[2])
            mu0 = predict(e0, r, E0_CONFIG[2]) if e0 else None
            if mu is None or mu0 is None:
                continue
            mu_of[(r.a, r.d)] = mu
            tmp.append((r, mu, mu0))
        for r, mu, mu0 in tmp:
            rmc = rm.get(r.cls)
            dist = distribution(mu, rmc["c"] * r.s, rmc) if rmc else None
            pb = None
            if rmc and r.yb is not None:
                mub = mu_of.get((r.bench, r.d)) if r.bench else r.yb
                if mub is not None:
                    pb = p_beat(mu, mub, rmc["c"] * r.srel, rmc)
            rows.append((r, mu, mu0, dist, pb, ERA_LABEL[e], base_rate.get(r.cls, 0.5)))
        eras_done.append(ERA_LABEL[e])
        choices.append({"era": ERA_LABEL[e], **{c: {"family": v[0], "lam": v[1], "depth": v[2], "K": v[3]} for c, v in choice.items()}})
        say(f"{lab}: era {ERA_LABEL[e]} — {len(tmp)} test records ({time.time() - t0:.0f}s)")
    by_cls: Dict[str, list] = {}
    for x in rows:
        by_cls.setdefault(x[0].cls, []).append(x)
    res = {"records": len(recs), "assets": len(paths), "eras": eras_done, "choices": choices,
           "classes": {c: _evaluate(xs, h, step) for c, xs in by_cls.items()}, "all": _evaluate(rows, h, step)}
    for c, ev in res["classes"].items():
        ev["adopted"] = "learned" if (ev["gain"]["mean"] or -1) >= 0 and (ev["calibration"]["slope"] or -1) > 0 else "prior"
        ev["reliability"] = _reliability(ev)
    # the final fit: the nested choice on the last INNER_YEARS, fitted on every matured record
    fin_choice, fin_inner_fits = choose(leaves, paths, cuts, recs, _add_years(fin_inner, INNER_YEARS), say)
    kb_all = len(cuts)
    nodes = node_stats(leaves, paths, kb_all)
    inner = [r for r in recs if fin_inner <= r.d]
    final_fits = {}
    classes_out = {}
    e0_fin = fit(nodes, E0_CONFIG[0], E0_CONFIG[1], E0_CONFIG[3], E0_CONFIG[2])
    e0_inner = fit(node_stats(leaves, paths, bisect.bisect_right(cuts, fin_inner) - 1), E0_CONFIG[0], E0_CONFIG[1], E0_CONFIG[3], E0_CONFIG[2])
    for c in sorted({r.cls for r in recs}):
        ev = res["classes"].get(c) or {}
        use_learned = ev.get("adopted") == "learned" and c in fin_choice
        cf = fin_choice[c] if use_learned else E0_CONFIG
        key = f"{cf[0]}|{cf[1]}|{cf[3]}"
        if key not in final_fits:
            final_fits[key] = fit(nodes, cf[0], cf[1], cf[3], 6) if use_learned else e0_fin
        inner_fit = fin_inner_fits.get((cf[0], cf[1], cf[3])) if use_learned else e0_inner
        rm = _resid_from({(cf[0], cf[1], cf[3]): inner_fit}, {c: cf}, [r for r in inner if r.cls == c]).get(c) if inner_fit else None
        ys = [r.y > 0 for r in recs if r.cls == c]
        classes_out[c] = {"adopted": "learned" if use_learned else "prior", "family": cf[0], "lam": cf[1], "depth": cf[2], "K": cf[3],
                          "fit": key, "resid": rm, "base_rate_pos": sum(ys) / len(ys) if ys else 0.5, "reliability": ev.get("reliability", "Low"),
                          "oos": {k: ev.get(k) for k in ("gain", "calibration", "rank_ic", "coverage", "brier_pos", "vs_production")}}
    res["final"] = {"choice": {c: list(v) for c, v in fin_choice.items()}, "inner_from": fin_inner, "classes": classes_out,
                    "fits": {k: _compact(m) for k, m in final_fits.items() if m}}
    res["seconds"] = round(time.time() - t0, 1)
    return res


def _compact(m: dict) -> dict:
    return {"family": m["family"], "lam": m["lam"], "K": m["K"], "cols": m["cols"], "rms": [round(x, 8) for x in m["rms"]],
            "W": {k: [round(x, 8) for x in w] for k, w in m["W"].items()}, "n": {k: round(v, 1) for k, v in m["n"].items()}}


def _evaluate(xs, h: int, step: int) -> dict:
    if not xs:
        return {}
    gain: Dict[str, List[float]] = {}
    vs_prod: Dict[str, List[float]] = {}
    eras: Dict[str, Dict[str, List[float]]] = {}
    ic_by: Dict[str, List[Tuple[float, float]]] = {}
    sx = sy = sxx = sxy = 0.0
    cov50 = cov90 = nd = 0
    brier = brier0 = 0.0
    bb = bb0 = 0.0
    nb = 0
    dec = [[0.0, 0.0, 0] for _ in range(10)]
    mus = sorted(mu for _, mu, *_ in xs)
    edges = [mus[int(k * len(mus) / 10)] for k in range(1, 10)]
    for r, mu, mu0, dist, pb, era, br in xs:
        g = (r.y - mu0) ** 2 - (r.y - mu) ** 2
        gain.setdefault(r.d, []).append(g)
        eras.setdefault(era, {}).setdefault(r.d, []).append(g)
        if r.cls == "EQUITY":
            ic_by.setdefault(r.d, []).append((mu, r.y))
        sx += mu; sy += r.y; sxx += mu * mu; sxy += mu * r.y
        k = bisect.bisect_right(edges, mu)
        dec[k][0] += mu; dec[k][1] += r.y; dec[k][2] += 1
        R = math.exp(r.y) - 1
        if dist:
            nd += 1
            cov50 += dist["r50"][0] <= R <= dist["r50"][1]
            cov90 += dist["r90"][0] <= R <= dist["r90"][1]
            o = 1.0 if r.y > 0 else 0.0
            brier += (dist["p_pos"] - o) ** 2
            brier0 += (br - o) ** 2
        if pb is not None and r.yb is not None:
            o = 1.0 if r.y > r.yb else 0.0
            bb += (pb - o) ** 2
            bb0 += (0.5 - o) ** 2
            nb += 1
    n = len(xs)
    vx = sxx / n - (sx / n) ** 2
    slope = ((sxy / n - (sx / n) * (sy / n)) / vx) if vx > 1e-18 else None
    ics = [_spearman([a for a, _ in v], [b for _, b in v]) for v in ic_by.values() if len(v) >= 20]
    ics = [x for x in ics if x is not None]
    ic_m = sum(ics) / len(ics) if ics else None
    ic_t = None
    if len(ics) > 2:
        sd = math.sqrt(sum((x - ic_m) ** 2 for x in ics) / (len(ics) - 1))
        ic_t = ic_m / (sd / math.sqrt(len(ics))) * math.sqrt(min(1.0, step / h)) if sd > 0 else None
    return {"n": n, "gain": _paired_t(gain, h, step), "eras": {e: _paired_t(v, h, step) for e, v in eras.items()},
            "calibration": {"slope": slope, "intercept": (sy / n - (slope or 0) * sx / n), "mean_mu": sx / n, "mean_y": sy / n,
                            "deciles": [{"mu": d[0] / d[2], "y": d[1] / d[2], "n": d[2]} for d in dec if d[2]]},
            "rank_ic": {"mean": ic_m, "t": ic_t, "dates": len(ics)},
            "coverage": {"r50": cov50 / nd if nd else None, "r90": cov90 / nd if nd else None},
            "brier_pos": {"model": brier / nd if nd else None, "base_rate": brier0 / nd if nd else None},
            "brier_beat": {"model": bb / nb if nb else None, "coin": bb0 / nb if nb else None, "n": nb},
            "vs_production": None}


def _reliability(ev: dict) -> str:
    g, s = ev.get("gain") or {}, (ev.get("calibration") or {}).get("slope")
    if (g.get("t") or 0) >= 2 and s is not None and 0.5 <= s <= 1.5:
        return "High"
    if (g.get("mean") or -1) >= 0 and (s or -1) > 0:
        return "Medium"
    return "Low"


def run_study(db_path: str, progress=None, horizons=None, workers: int = 1) -> dict:
    """Every horizon (or `horizons`), sequentially or in `workers` processes; stores the result and the frozen spec."""
    from ..data.store import Store
    say = progress or (lambda m: None)
    labs = [lab for lab, _, _ in HORIZONS if not horizons or lab in horizons]
    st = Store(db_path)
    try:
        prev = st.kv_get(RESEARCH_KEY) or {}
    finally:
        st.close()
    out = {"version": SYS_VERSION, "started": time.strftime("%Y-%m-%d %H:%M:%S"), "horizons": dict(prev.get("horizons") or {})}
    if workers > 1:
        from concurrent.futures import ProcessPoolExecutor, as_completed
        with ProcessPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(_study_worker, db_path, lab): lab for lab in labs}
            for f in as_completed(futs):
                out["horizons"][futs[f]] = f.result()
                say(f"{futs[f]} done")
    else:
        for lab in labs:
            out["horizons"][lab] = _study_worker(db_path, lab)
    st = Store(db_path)
    try:
        st.kv_set(RESEARCH_KEY, out)
        spec = make_spec(st, out)
        st.kv_set(LIVE_KEY, spec)
    finally:
        st.close()
    return out


def _study_worker(db_path: str, lab: str) -> dict:
    from ..data.store import Store
    st = Store(db_path)
    try:
        return study_horizon(st, lab, progress=lambda m: print(m, flush=True))
    finally:
        st.close()


# ------------------------------------------------------------------ the frozen live spec
def _digest(spec: dict) -> str:
    body = {k: v for k, v in spec.items() if k != "hash"}
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def make_spec(store, res: dict) -> dict:
    from .research import Research
    r = Research(store)
    metas = eligible(store)
    gp = group_prefix(store, r, metas)
    gm = {g: (ps[-1] / pc[-1] if pc[-1] else 0.0) for g, (ps, pc) in gp.items()}
    spec = {"version": SYS_VERSION, "protocol": "SHAFFER_SYSTEM.md", "fitted": time.strftime("%Y-%m-%d"), "data_through": r.panel().calendar()[-1],
            "group_mean": {g: round(v, 9) for g, v in gm.items()}, "horizons": {}}
    for lab, hz in (res.get("horizons") or {}).items():
        fin = (hz or {}).get("final")
        if fin:
            spec["horizons"][lab] = {"classes": fin["classes"], "fits": fin["fits"]}
    spec["hash"] = _digest(spec)
    return spec


def write_frozen(spec: dict, path: str = FROZEN_FILE):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(spec, f, sort_keys=True, separators=(",", ":"))


def load_spec(store=None) -> Optional[dict]:
    """The live spec: the store's own refit when present, else the committed frozen file (hash checked)."""
    spec = store.kv_get(LIVE_KEY) if store is not None else None
    if not spec and os.path.exists(FROZEN_FILE):
        with open(FROZEN_FILE, encoding="utf-8") as f:
            spec = json.load(f)
    if spec and _digest(spec) != spec.get("hash"):
        raise ValueError("Shaffer System spec: hash mismatch")
    return spec


# ------------------------------------------------------------------ today's forecast for one asset
def inputs_today(research, a: dict) -> Optional[dict]:
    """What the forecast needs at the last session (computed with the bundle, cached with it)."""
    from .alphahz import feature_spec
    panel = research.panel()
    cal = panel.calendar()
    px = panel.series(a["id"])
    r = _logret(px)
    t = len(cal) - 1
    while t > 0 and px[t] is None:
        t -= 1
    sd = _std(r[max(0, t - 62):t + 1])
    if not sd or not px[t]:
        return None
    bench = benchmark_of(a)
    rel = None
    if bench:
        rb = _logret(panel.series(bench))
        rel = _std([x - y for x, y in zip(r[max(0, t - 62):t + 1], rb[max(0, t - 62):t + 1]) if x is not None and y is not None])
    z = research.zscores(a["id"])
    fam = features_at(z, t, feature_spec())
    vix, term, cred, rf = macro_at(_macro(panel), t)
    own = [x for x in r[:t + 1] if x is not None]
    return {"date": cal[t], "fam": fam, "logvol": math.log(sd * math.sqrt(252) / 0.2), "logrel": math.log(rel * math.sqrt(252) / 0.2) if rel else None,
            "own_sum": sum(own), "own_n": len(own), "vix": vix, "term": term, "credit": cred, "rf": rf, "bench": bench}


class _Row:
    __slots__ = ("path", "v")


def forecast(spec: dict, a: dict, inp: dict, raws: Dict[str, Optional[float]], move: Optional[Dict[str, dict]] = None,
             bench_mu: Optional[Dict[str, float]] = None) -> Dict[str, dict]:
    """Every horizon's Shaffer forecast for one asset. `raws` = production evidence index by production horizon;
    `move` = the move-size forecasts {"1D": {"sigma"}, "1W": {...}} for Directional dispersion; `bench_mu` = the
    benchmark's own μ̂ by horizon (for P(beat))."""
    out: Dict[str, dict] = {}
    if not spec or not inp:
        return out
    path = node_path(a)
    cls = a.get("asset_class")
    gm = (spec.get("group_mean") or {}).get(group_of(a), 0.0)
    n_own = inp["own_n"]
    m_own = inp["own_sum"] / n_own if n_own else gm
    w = n_own / (n_own + BASE_N0)
    mday = w * m_own + (1 - w) * gm
    for lab, h, _ in HORIZONS:
        hs = (spec.get("horizons") or {}).get(lab)
        cs = (hs or {}).get("classes", {}).get(cls)
        if not cs or not cs.get("resid"):
            continue
        m = hs["fits"].get(cs["fit"])
        if not m:
            continue
        raw = raws.get(RAW_LAB[lab])
        row = [inp["date"], None, None, None, mday * h, raw, inp["logvol"], inp["logrel"], *inp["fam"], inp["vix"], inp["term"], inp["credit"], inp["rf"]]
        rr = _Row()
        rr.path, rr.v = path, design(row)
        mu = predict(m, rr, cs["depth"])
        if mu is None:
            continue
        s63 = math.exp(inp["logvol"]) * 0.2 * math.sqrt(h / 252.0)
        shat = cs["resid"]["c"] * s63
        disp_src = "Shaffer System residuals"
        if lab in DIRECTIONAL and move:
            ms = _move_sigma(move, lab)
            if ms:
                shat, disp_src = ms, "move-size model (SHAFFER_MOVE_SIZE.md)" + (" — interpolated for 3D" if lab == "3D" else "")
        d = distribution(mu, shat, cs["resid"])
        if lab in DIRECTIONAL and move and lab != "3D" and (move.get(lab) or {}).get("lo_pct") is not None:
            d["r90"] = [move[lab]["lo_pct"], move[lab]["hi_pct"]]
        node = node_used(m, path, cs["depth"])
        lean = []
        for k in path[:cs["depth"]]:
            if k in m["W"] and k != "global":
                nk = m["n"].get(k, 0.0)
                lean.append({"node": _nice(k), "records": nk, "own_weight": (nk / (nk + cs["K"])) if k.count("|") >= 2 else None})
        contrib = []
        for wi, c, s in zip(m["W"][node], m["cols"], m["rms"]):
            if c == 0 or not s:
                continue
            x = wi * rr.v[c] / s
            if abs(x) > 1e-7:
                contrib.append({"name": COLS[c], "log_points": x})
        contrib.sort(key=lambda x: -abs(x["log_points"]))
        pb = None
        if inp.get("bench") is None:
            mub = math.log(1 + inp["rf"] / 100.0) * h / 252.0
        else:
            mub = (bench_mu or {}).get(lab)
        if mub is not None:
            srel = (math.exp(inp["logrel"]) * 0.2 * math.sqrt(h / 252.0) if inp.get("logrel") is not None else s63) * cs["resid"]["c"]
            pb = p_beat(mu, mub, srel, cs["resid"])
        out[lab] = {"horizon": lab, "label": DISPLAY.get(lab, lab), "system": "Directional" if lab in DIRECTIONAL else "Alpha",
                    "score": 100.0 * d["expected"], "expected": d["expected"], "median": d["median"], "mu_log": mu, "sigma_log": shat,
                    "p_pos": d["p_pos"], "p_beat": pb, "benchmark": inp.get("bench") or "cash (3-month T-bill)",
                    "range50": d["r50"], "range90": d["r90"], "abs_move": d["abs_move"], "dispersion": disp_src,
                    "reliability": cs.get("reliability"), "adopted": cs.get("adopted"), "family": cs.get("family"),
                    "family_name": FAMILY_NAMES.get(cs.get("family")), "depth": cs.get("depth"), "K": cs.get("K"),
                    "node": _nice(node) if node else None, "hierarchy": lean, "version": f"{spec['version']}@{spec['hash'][:10]}",
                    "positive": [c for c in contrib if c["log_points"] > 0][:4], "negative": [c for c in contrib if c["log_points"] < 0][:4],
                    "evidence_index": raw, "oos": cs.get("oos")}
    return out


def _move_sigma(move: Dict[str, dict], lab: str) -> Optional[float]:
    s1, s5 = (move.get("1D") or {}).get("sigma"), (move.get("1W") or {}).get("sigma")
    if lab == "1D":
        return s1
    if lab == "1W":
        return s5
    if s1 and s5:                                       # 3D: daily variance interpolated between the 1D and 1W forecasts
        v = (s1 * s1 + 2 * (s5 * s5 / 5.0)) / 3.0
        return math.sqrt(3 * v)
    return None


def _nice(key: str) -> str:
    if key == "global":
        return "Global"
    return " → ".join(part.split(":", 1)[1] for part in key.split("|"))


# ------------------------------------------------------------------ report
def _n(v, d=4):
    return "—" if v is None else f"{v:+.{d}f}"


def markdown(res: dict) -> str:
    L = ["# The Shaffer System: walk-forward results", "",
         f"Run {res.get('started')} under SHAFFER_SYSTEM.md (protocol fixed before any result). Each Shaffer Score is the expected "
         "percentage total return over its horizon. The learned equation is adopted per horizon and asset class only where its "
         "walk-forward error is not worse than the calibrated prior (E0) and its calibration slope is positive. Otherwise the "
         "calibrated prior is the production equation.", "",
         "**The columns:**",
         "- **MSE gain vs E0:** the out-of-sample reduction in squared error of the log return against the calibrated prior "
         "(per date, t adjusted for overlapping windows).",
         "- **Slope:** the regression of the realised log return on the forecast (1 = perfectly calibrated).",
         "- **Rank IC:** the within-date cross-section of stocks.",
         "- **Coverage:** of the 50% and 90% ranges.",
         "- **Brier:** of P(return > 0), against the base rate.", ""]
    for lab, hz in (res.get("horizons") or {}).items():
        if not hz or hz.get("skipped"):
            L += [f"## {DISPLAY.get(lab, lab)} — skipped ({(hz or {}).get('skipped')})", ""]
            continue
        a = hz.get("all") or {}
        L += [f"## {DISPLAY.get(lab, lab)} ({'Directional' if lab in DIRECTIONAL else 'Alpha'}) — {hz.get('records')} records, {hz.get('assets')} assets, eras {', '.join(hz.get('eras') or [])}", "",
              "| Class | Records | MSE gain vs E0 (t) | Eras won | Slope | Rank IC (t) | Coverage 50 / 90 | Brier P(>0) model / base | Adopted | Reliability | Final equation |",
              "|---|---|---|---|---|---|---|---|---|---|---|"]
        fin = (hz.get("final") or {}).get("classes") or {}
        for c, ev in sorted((hz.get("classes") or {}).items(), key=lambda x: -x[1].get("n", 0)):
            g, cal, ic, cov, br = ev["gain"], ev["calibration"], ev["rank_ic"], ev["coverage"], ev["brier_pos"]
            won = sum(1 for e in ev["eras"].values() if (e.get("mean") or 0) > 0)
            fc = fin.get(c) or {}
            eq = f"{fc.get('family')} {FAMILY_NAMES.get(fc.get('family'), '')}, depth {fc.get('depth')}, K {fc.get('K')}" if fc else "—"
            pc = lambda v: "—" if v is None else f"{v:.0%}"  # noqa: E731
            L.append(f"| {c} | {ev['n']} | {_n(g.get('mean'), 6)} ({_n(g.get('t'), 1)}) | {won}/{len(ev['eras'])} | {_n(cal.get('slope'), 2)} | "
                     f"{_n(ic.get('mean'), 3)} ({_n(ic.get('t'), 1)}) | {pc(cov.get('r50'))} / {pc(cov.get('r90'))} | "
                     f"{_n(br.get('model'), 4)} / {_n(br.get('base_rate'), 4)} | {ev.get('adopted')} | {ev.get('reliability')} | {eq} |")
        L += ["", f"All classes: MSE gain {_n(a.get('gain', {}).get('mean'), 6)} (t {_n(a.get('gain', {}).get('t'), 1)}), slope "
              f"{_n(a.get('calibration', {}).get('slope'), 2)}, 90% coverage {(a.get('coverage') or {}).get('r90') or 0:.0%}.", ""]
        ch = hz.get("choices") or []
        if ch:
            L.append("Nested choices by era: " + "; ".join(f"{x['era']}: " + ", ".join(f"{c} {v['family']}/d{v['depth']}/K{int(v['K'])}"
                                                                                    for c, v in x.items() if c != 'era') for x in ch))
            L.append("")
    return "\n".join(L) + "\n"


# ------------------------------------------------------------------ the app: forecasts attached to every asset bundle
_SPEC_CACHE: Dict[str, tuple] = {}


def spec_for(store) -> Optional[dict]:
    """The live spec, re-read at most every 10 minutes per store."""
    key = getattr(store, "path", "mem")
    hit = _SPEC_CACHE.get(key)
    if hit and time.time() - hit[0] < 600:
        return hit[1]
    spec = load_spec(store)
    _SPEC_CACHE[key] = (time.time(), spec)
    return spec


PROD_LAB = {"1D": "1D", "1W": "1W", "1M": "1M", "3M": "3M", "6M": "6M", "12M": "12M"}


def for_bundle(research, b: dict) -> Dict[str, dict]:
    """Every horizon's forecast for a bundle: inputs cached in the bundle, production's evidence index from it, the
    move-size forecasts of today (when computed for this session), the benchmark's own forecast for P(beat)."""
    spec = spec_for(research.store)
    inp = b.get("sys_inputs")
    if not spec or not inp:
        return {}
    a = b["asset"]
    raws = {lab: (b["horizons"].get(lab) or {}).get("score") for lab in PROD_LAB}
    move = None
    try:
        from . import movesize
        it = ((research.store.kv_get(movesize.TODAY_KEY) or {}).get("items") or {}).get(a["id"])
        if it and it.get("date") == inp.get("date"):
            move = it.get("horizons")
    except Exception:  # noqa: BLE001
        move = None
    bench = inp.get("bench")
    bench_mu = research.system_mu(bench) if bench and bench != a["id"] else None
    key = f"sys:{a['id']}:{spec['hash'][:10]}:{inp.get('date')}:{bool(move)}:{bool(bench_mu)}"
    return research._memo(key, lambda: forecast(spec, a, inp, raws, move, bench_mu))

