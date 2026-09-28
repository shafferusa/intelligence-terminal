"""Move size with implied volatility: does the market's own volatility forecast improve FinSim2's? (research only)

Protocol: SHAFFER_IV_PROTOCOL.md (fixed 2026-09-28 before any result). Report: SHAFFER_IV_RESEARCH.md (with the
newinfo batch ``iv`` tests). Nothing here changes the move-size display, the Shaffer System or the Hedge.

Records are the move-size records (engine/movesize.py) at 1D (every other session), 1W (every 5) and 1M (every 21);
outcomes never overlap. The reference V0 is move-size M3 (σ5 / σ21 / σ63, VIX, today's move, class, event calendar,
8-K). Challengers add implied-volatility features:

    V1  ln own IV + iv_index features          records of assets with an own Cboe index (V0 refitted on them)
    V2  vol_term (VIX / VIX3M)                 all records
    V3  ln iv30 + iv_chain features (dump)     records with dump rows (V0 refitted on them)
    V4  every added feature                    all records
    V5  vol_cboe (VIX9D, VVIX, SKEW)           all records

An added feature is centred on its training mean and a missing value is set to that mean; a feature with under 20% of
the era's training records is left out of that era's fit. The fits are exact OLS with the move-size ridge from
sufficient statistics over [1, base, u, p] (u = value where present else 0, p = presence), which lets any centring be
applied per era without another pass. Level calibration and 90% range quantiles as in move size, on a deterministic
sample of at most CALIB_SAMPLE training records (the live move-size layer does the same).
"""
from __future__ import annotations

import bisect
import math
import time
from array import array
from typing import Dict, List, Optional, Tuple

from . import movesize as MS

RESEARCH_KEY = "lab:ivstudy"
HORIZONS = [("1D", 1, 2), ("1W", 5, 5), ("1M", 21, 21)]
IVF = ["log_iv", "iv_rv_log", "iv_z", "iv_chg_5d"]
TERM = ["vix_vix3m_log", "d_vix_vix3m_21d"]
CBOE = ["vix9d_vix_log", "vvix_z", "skew_z"]
CHAIN = ["log_iv30", "chain_iv_rv_log", "chain_skew25", "chain_term", "chain_pc_oi"]
EXTRA = IVF + TERM + CBOE + CHAIN
EIDX = {f: i for i, f in enumerate(EXTRA)}
MODELS: Dict[str, Tuple[List[str], str, str]] = {
    "V1": (IVF, "iv", "+ own implied volatility (Cboe index)"),
    "V2": (TERM, "all", "+ VIX term structure (VIX / VIX3M)"),
    "V3": (CHAIN, "chain", "+ option-chain features (dump)"),
    "V4": (EXTRA, "all", "+ every implied-volatility feature"),
    "V5": (CBOE, "all", "+ VIX9D, VVIX, SKEW (Cboe CSVs)"),
}
SUBSETS = {"all": None, "iv": "log_iv", "chain": "log_iv30"}
MIN_COVER = 0.20
CALIB_SAMPLE = 120000
NB = len(MS.ALL)
NE = len(EXTRA)
P = 1 + NB + 2 * NE                                   # [1, base…, u…, p…]
NAN = float("nan")


class R:
    __slots__ = ("a", "d", "end", "x", "e", "y", "r2", "ret", "h")

    def __init__(self, base: "MS.Rec", e):
        self.a, self.d, self.end, self.x, self.y, self.r2, self.ret, self.h = base.a, base.d, base.end, base.x, base.y, base.r2, base.ret, base.h
        self.e = e


def _present(v) -> bool:
    return v is not None and v == v


# ------------------------------------------------------------------ records
def extra_rows(builder, meta: dict) -> Dict[str, list]:
    """The added features on the panel calendar for one asset."""
    cols: Dict[str, list] = {}
    a = meta["id"]
    from .newinfo import applies
    if applies("iv_index", meta):
        cols["log_iv"] = builder.log_iv(a)
        cols.update(builder.iv_index(a))
    cols.update(builder.wide("vol_term"))
    cols.update(builder.wide("vol_cboe"))
    if applies("iv_chain", meta):
        ch = builder.iv_chain(a)
        if any(v is not None for v in ch["log_iv30"]):
            cols.update(ch)
    return cols


def build(store, research, progress=None, assets: Optional[List[str]] = None) -> Dict[str, List[R]]:
    from . import newinfo as N
    from .directional import _logret
    say = progress or (lambda m: None)
    b = N.Builder(research, store)
    start_i = bisect.bisect_left(b.cal, MS.START)
    metas = [a for a in store.assets() if (not assets or a["id"] in assets) and store.price_count(a["id"]) >= 252 * 6
             and a.get("asset_class") in MS.CLASSES]
    out: Dict[str, List[R]] = {lab: [] for lab, _, _ in HORIZONS}
    t0 = time.time()
    for k, m in enumerate(metas):
        rets = _logret(b.series(m["id"]))
        cols = extra_rows(b, m)
        for lab, h, step in HORIZONS:
            for r in MS.asset_rows(b, m, h, step, start_i, rets):
                if r.y is None:
                    continue
                i = bisect.bisect_left(b.cal, r.d)
                e = array("d", [NAN] * NE)
                for f, col in cols.items():
                    if f in EIDX and i < len(col) and col[i] is not None:
                        e[EIDX[f]] = col[i]
                out[lab].append(R(r, e))
        if k % 25 == 0:
            say(f"IV move-size records {k + 1}/{len(metas)} ({time.time() - t0:.0f}s)")
    return out


# ------------------------------------------------------------------ sufficient statistics
class Stats:
    __slots__ = ("A", "b", "n")

    def __init__(self):
        self.A = [0.0] * (P * P)
        self.b = [0.0] * P
        self.n = 0

    def add(self, r: R):
        v = [(0, 1.0)]
        x = r.x
        for k in range(NB):
            z = x[k]
            if z == z and z:
                v.append((1 + k, z))
        e = r.e
        for j in range(NE):
            z = e[j]
            if z == z:
                if z:
                    v.append((1 + NB + j, z))
                v.append((1 + NB + NE + j, 1.0))
        A, y = self.A, r.y
        self.n += 1
        for a, (i, vi) in enumerate(v):
            self.b[i] += vi * y
            base = i * P
            for j, vj in v[a:]:
                A[base + j] += vi * vj


def year_stats(rows: List[R]) -> Dict[int, Stats]:
    out: Dict[int, Stats] = {}
    for r in rows:
        yr = int(r.end[:4])
        s = out.get(yr)
        if s is None:
            s = out[yr] = Stats()
        s.add(r)
    return out


def _sum(stats: Dict[int, Stats], before_year: int) -> Optional[Stats]:
    ys = [s for y, s in stats.items() if y < before_year]
    if not ys:
        return None
    t = Stats()
    for s in ys:
        t.n += s.n
        for i in range(P):
            t.b[i] += s.b[i]
        A = t.A
        for i, v in enumerate(s.A):
            if v:
                A[i] += v
    return t


def fit(stats: Dict[int, Stats], before_year: int, extra: List[str], train: List[R]) -> Optional[dict]:
    S = _sum(stats, before_year)
    if S is None or S.n < 5000:
        return None
    g = lambda i, j: S.A[min(i, j) * P + max(i, j)]          # noqa: E731
    used, mu = [], {}
    for f in extra:
        j = EIDX[f]
        cnt = g(0, 1 + NB + NE + j)
        if cnt >= MIN_COVER * S.n and cnt > 0:
            used.append(f)
            mu[f] = g(0, 1 + NB + j) / cnt
    # design columns as linear combinations of the stored columns
    cols: List[List[Tuple[int, float]]] = [[(0, 1.0)]] + [[(1 + k, 1.0)] for k in range(NB)]
    for f in used:
        j = EIDX[f]
        cols.append([(1 + NB + j, 1.0), (1 + NB + NE + j, -mu[f])])
    m = len(cols)
    A = [[sum(ca * cb * g(ia, ib) for ia, ca in cols[i] for ib, cb in cols[j]) + (MS.LAMBDA if i == j and i else 0.0)
          for j in range(m)] for i in range(m)]
    bvec = [sum(c * S.b[i] for i, c in col) for col in cols]
    from .alphahz import _solve
    w = _solve(A, bvec)
    model = {"w": w, "used": used, "mu": mu, "n": S.n}
    step = max(1, len(train) // CALIB_SAMPLE)
    samp = train[::step]
    f = [_lin(model, r) for r in samp]
    num = sum(r.r2 for r in samp)
    den = sum(math.exp(2 * v) for v in f)
    model["k"] = num / den if den > 0 else 1.0
    std = sorted(r.ret / math.sqrt(model["k"] * math.exp(2 * v) * r.h) for r, v in zip(samp, f) if r.ret is not None)
    model["q"] = (std[int(0.05 * (len(std) - 1))], std[int(0.95 * (len(std) - 1))]) if len(std) > 100 else (-1.645, 1.645)
    return model


def _lin(m: dict, r: R) -> float:
    w, x = m["w"], r.x
    s = w[0]
    for k in range(NB):
        z = x[k]
        if z == z:
            s += w[1 + k] * z
    for n, f in enumerate(m["used"]):
        z = r.e[EIDX[f]]
        if z == z:
            s += w[1 + NB + n] * (z - m["mu"][f])
    return s


def predict(m: dict, r: R) -> float:
    return m["k"] * math.exp(2 * _lin(m, r))


# ------------------------------------------------------------------ the study
def _subset(rows: List[R], key: Optional[str]) -> List[R]:
    if key is None:
        return rows
    j = EIDX[key]
    return [r for r in rows if r.e[j] == r.e[j]]


def _era(d: str) -> Optional[str]:
    return next((MS.ERA_LABEL[k] for k, (a, b) in enumerate(MS.ERAS) if a <= d < b), None)


def study(data: Dict[str, List[R]], progress=None) -> dict:
    say = progress or (lambda m: None)
    out = {"started": time.strftime("%Y-%m-%d %H:%M:%S"), "protocol": "SHAFFER_IV_PROTOCOL.md", "horizons": {}}
    for lab, h, _ in HORIZONS:
        rows = data[lab]
        res: dict = {"records": len(rows), "models": {}, "subsets": {}}
        for sname, key in SUBSETS.items():
            sub = _subset(rows, key)
            res["subsets"][sname] = {"records": len(sub), "assets": len({r.a for r in sub})}
            models = [mk for mk, (_, s, _) in MODELS.items() if s == sname]
            if not models or len(sub) < 5000:
                for mk in models:
                    res["models"][mk] = {"n": len(sub), "status": "INSUFFICIENT DATA", "why": f"{len(sub)} records"}
                continue
            stats = year_stats(sub)
            t0 = time.time()
            preds: Dict[str, List[Tuple[R, float, tuple]]] = {mk: [] for mk in ["V0"] + models}
            used_eras: Dict[str, List[str]] = {mk: [] for mk in models}
            for e, (a, b) in enumerate(MS.ERAS):
                train = [r for r in sub if r.end < a]
                test = [r for r in sub if a <= r.d < b]
                if len(train) < 5000 or not test:
                    continue
                y = int(a[:4])
                m0 = fit(stats, y, [], train)
                if m0 is None:
                    continue
                preds["V0"] += [(r, predict(m0, r), m0["q"]) for r in test]
                for mk in models:
                    m = fit(stats, y, MODELS[mk][0], train)
                    if m is None:
                        continue
                    if m["used"]:
                        used_eras[mk].append(MS.ERA_LABEL[e])
                    preds[mk] += [(r, predict(m, r), m["q"]) for r in test]
                say(f"{lab} [{sname}] era {MS.ERA_LABEL[e]}: {len(train)} train / {len(test)} test ({time.time() - t0:.0f}s)")
            base = {id(r): (v, q) for r, v, q in preds["V0"]}
            res["models"].setdefault(f"V0:{sname}", _summary(preds["V0"], h))
            for mk in models:
                s = _summary(preds[mk], h)
                diff: Dict[str, List[float]] = {}
                eras: Dict[str, Dict[str, List[float]]] = {}
                width = []
                for r, v, q in preds[mk]:
                    v0, q0 = base[id(r)]
                    d = MS.qlike(r.r2, v0) - MS.qlike(r.r2, v)
                    diff.setdefault(r.d, []).append(d)
                    eras.setdefault(_era(r.d), {}).setdefault(r.d, []).append(d)
                    width.append((q[1] - q[0]) * math.sqrt(v) / ((q0[1] - q0[0]) * math.sqrt(v0)))
                s["gain"] = MS._paired(diff, h)
                s["eras"] = {e: MS._paired(v, h) for e, v in eras.items()}
                s["width_ratio"] = sum(width) / len(width) if width else None
                s["features_used_in"] = used_eras[mk]
                s["split"] = _split(sub, stats, mk, h)
                s["subset"] = sname
                res["models"][mk] = s
        out["horizons"][lab] = res
    finalise(out)
    return out


def _summary(preds: List[Tuple[R, float, tuple]], h: int) -> dict:
    ql = [MS.qlike(r.r2, v) for r, v, _ in preds]
    return {"n": len(ql), "qlike": sum(ql) / len(ql) if ql else None, "coverage_90": _coverage(preds, h)}


def _coverage(preds, h: int) -> Optional[float]:
    """Share of outcomes inside the calibrated 90% range, q5 · σ√h … q95 · σ√h."""
    c = [1.0 if q[0] * math.sqrt(v * h) <= r.ret <= q[1] * math.sqrt(v * h) else 0.0 for r, v, q in preds if r.ret is not None]
    return sum(c) / len(c) if c else None


def _split(rows: List[R], stats, mk: str, h: int) -> dict:
    train = [r for r in rows if r.end < MS.SPLIT]
    test = [r for r in rows if r.d >= MS.SPLIT]
    if len(train) < 5000 or not test:
        return {}
    y = int(MS.SPLIT[:4])
    m0, m1 = fit(stats, y, [], train), fit(stats, y, MODELS[mk][0], train)
    if m0 is None or m1 is None:
        return {}
    diff: Dict[str, List[float]] = {}
    for r in test:
        diff.setdefault(r.d, []).append(MS.qlike(r.r2, predict(m0, r)) - MS.qlike(r.r2, predict(m1, r)))
    return MS._paired(diff, h)


def finalise(out: dict) -> dict:
    from .newinfo import benjamini_hochberg, _p_one_sided
    tests = []
    for lab, hz in out["horizons"].items():
        for mk in MODELS:
            m = hz["models"].get(mk) or {}
            if m.get("status") == "INSUFFICIENT DATA":
                continue
            g = m.get("gain") or {}
            eras = [e for e, v in (m.get("eras") or {}).items() if e != "2025–" and v.get("dates", 0) >= 20]
            won = sum(1 for e in eras if (m["eras"][e].get("mean") or 0) > 0)
            with_data = [e for e in eras if e in (m.get("features_used_in") or [])]
            cov = m.get("coverage_90")
            m["gates"] = {"G1": (g.get("t") or 0) >= 2 and (g.get("mean") or 0) > 0, "eras_complete": len(eras), "eras_won": won,
                          "eras_with_data": len(with_data),
                          "G2": len(eras) >= 3 and won >= len(eras) - 1 and ((m.get("split") or {}).get("t") or 0) >= 1,
                          "G3": cov is not None and 0.87 <= cov <= 0.93}
            if len(with_data) < 3:
                m["status"] = "INSUFFICIENT DATA"
                m["why"] = f"its features have data in {len(with_data)} complete era(s)"
                continue
            tests.append((lab, mk, _p_one_sided(g.get("t"))))
    rej = benjamini_hochberg([p for *_, p in tests], MS.FDR_Q)
    for (lab, mk, p), ok in zip(tests, rej):
        m = out["horizons"][lab]["models"][mk]
        g = m["gates"]
        g["p"], g["fdr"] = p, ok
        g["passed"] = bool(g["G1"] and g["G2"] and g["G3"] and ok)
        m["status"] = "PASSED" if g["passed"] else "NOT VALIDATED"
    out["fdr"] = {"q": MS.FDR_Q, "tests": len(tests), "rejections": sum(rej)}
    return out


# ------------------------------------------------------------------ run + report
def run(db_path: str, progress=None) -> dict:
    from ..data.store import Store
    from .research import Research
    st = Store(db_path)
    try:
        data = build(st, Research(st), progress)
        res = study(data, progress)
        st.kv_set(RESEARCH_KEY, res)
        return res
    finally:
        st.close()


def _f(v, d=4):
    return "—" if v is None else f"{v:+.{d}f}"


def markdown(res: dict) -> str:
    L = ["## Target 1 — move size with implied volatility (1D / 1W / 1M)", "",
         f"Built {res.get('started')}. Reference V0 = move-size M3 (σ5 / σ21 / σ63, VIX, today's move, class, event calendar, "
         "8-K). QLIKE gain = V0's loss − the model's loss (positive = better), per date, walk-forward, V0 refitted on the "
         "same records. PASSED needs G1 (gain t ≥ 2), G2 (gain in all but one complete era, ≥ 3; split t ≥ 1), G3 (90% range "
         f"covers 87–93%) and BH q = 0.10 over the {res.get('fdr', {}).get('tests', 0)} evaluable tests. Range width = the "
         "model's mean 90% range ÷ V0's (below 1 = sharper).", "",
         "| Horizon | Model | Records (assets) | QLIKE gain (t) | Eras won | Split t | 90% coverage | Range width | Status |",
         "|---|---|---|---|---|---|---|---|---|"]
    for lab, hz in (res.get("horizons") or {}).items():
        subs = hz.get("subsets") or {}
        for mk, (_, sname, name) in MODELS.items():
            m = (hz.get("models") or {}).get(mk) or {}
            g, gain = m.get("gates") or {}, m.get("gain") or {}
            cov, wr = m.get("coverage_90"), m.get("width_ratio")
            sb = subs.get(sname) or {}
            st = m.get("status") or "—"
            if st == "INSUFFICIENT DATA" and m.get("why"):
                st += f" ({m['why']})"
            L.append(f"| {lab} | {mk} {name} | {sb.get('records', '—')} ({sb.get('assets', '—')}) | {_f(gain.get('mean'))} "
                     f"({_f(gain.get('t'), 1)}) | {g.get('eras_won', '—')}/{g.get('eras_complete', '—')} | "
                     f"{_f((m.get('split') or {}).get('t'), 1)} | {'—' if cov is None else f'{cov:.1%}'} | "
                     f"{'—' if wr is None else f'{wr:.3f}'} | {st} |")
    L.append("")
    return "\n".join(L) + "\n"


def report(ms: dict, fam: dict) -> str:
    """SHAFFER_IV_RESEARCH.md: the verdict, target 1 (move size) and targets 2–4 (the newinfo batch-iv tests)."""
    import re
    from . import newinfo as N
    passed = [(lab, mk) for lab, hz in (ms.get("horizons") or {}).items() for mk in MODELS
              if ((hz.get("models") or {}).get(mk) or {}).get("status") == "PASSED"]
    summ = fam.get("summary") or {}
    L = ["# Implied volatility and futures curves — research", "",
         f"Protocol `SHAFFER_IV_PROTOCOL.md` (fixed 2026-09-28 before any result). Families run {fam.get('started')}, move size "
         f"{ms.get('started')}. **Research only** — the Shaffer System, the Hedge and the move-size display are unchanged; a "
         "pass is a research display until the owner approves its use.", "", "## Verdict", ""]
    L.append("- **Move size:** " + (", ".join(f"{mk} at {lab}" for lab, mk in passed) + " PASSED" if passed
                                    else "no implied-volatility model passed every gate"))
    for f in N.BATCH_IV:
        if f in summ:
            L.append(f"- **{summ[f]['label']}** (`{f}`): {summ[f]['status']} — {summ[f]['why']}")
    L += ["", markdown(ms), "## Targets 2–4 — Alpha, Directional and Hedge (newinfo batch `iv`)", "",
          re.sub(r"(?m)^(#+) ", r"#\1 ", N.markdown(fam))]
    return "\n".join(L) + "\n"
