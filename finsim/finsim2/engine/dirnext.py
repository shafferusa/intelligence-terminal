"""ML Lab — Shaffer Directional vNext: real short-horizon directional information beyond the PIT base prior (research only).

Question: P(R_h > 0 | point-in-time information). The critical comparison is the challenger against the PIT
prior-only model (not against zero drift): the base prior explains almost all of today's directional accuracy.
Always reported apart: base prior p0, Shaffer adjustment (p − p0), final p.

Horizons: 1D and 1W. 1M is run ONLY if a 1D or 1W challenger passes every gate (fixed in advance; otherwise
reported as not run). 3M–12M are out of scope.

Short-horizon information, all known at the record's close (point in time):
    asset      overnight gap (open vs previous close, in daily σ), close location in the day's range, intraday range vs
               its 21-day average, 1-day and 5-day return (reversal), volatility acceleration (5-day vs 63-day), volume
               surprise (vs 60-day median), Amihud illiquidity, 5- and 21-day return relative to the asset's class /
               sector peers on the same date, earnings-event reaction and drift (event proximity)
    market     share of stocks above their 50-day average, breadth thrust, net new highs, dispersion, VIX (expanding z),
               5-day VIX change, SPY 5-day return — each also × the asset's PIT beta
BLOCKED: option skew / implied volatility history, positioning; short-sale volume is LIMITED HISTORY (2019 on) and not used.

Challengers (fixed before any result), logistic with ridge, per horizon:
    directional-3-{h}-global    [1, μ/σ, raw/100, every feature]
    directional-3-{h}-compact   [1, μ/σ, raw/100, 1-day and 5-day return, gap, volatility acceleration, breadth thrust,
                                 VIX change] — small and interpretable
    directional-3-{h}-class     the global model per asset class, shrunk to it (ridge toward the global coefficients)
Benchmarks on identical records: prior-only [1, μ/σ] and the current formulation [1, μ/σ, raw/100].
Training: records matured before each era (the usual eras and the 2018 split); at most 20,000 sampled training
records per fit (fixed seed); features standardised with training statistics only.

Gates (per challenger × horizon, fixed 2026-09-26 before any result):
    G1  walk-forward, paired on identical records, vs prior-only: Brier gain t ≥ 2, log-loss gain > 0, balanced accuracy
        gain > 0; AND vs the current formulation: Brier gain t ≥ 2
    G2  2018 split: Brier gain vs prior-only t ≥ 1
    G3  Brier gain vs prior-only > 0 in ≥ 3 of the 4 complete eras
    G4  calibration: ECE not above prior-only's + 0.005, calibration slope in [0.8, 1.25]
    FDR Benjamini-Hochberg q = 0.10 across challengers × horizons (Brier-gain-vs-prior t)
    A challenger passing all is LIVE SHADOW ELIGIBLE. Raw accuracy never qualifies a model.
Also reported: calibration tables (predicted 50–55 … 75+ vs realised), calibration slope / intercept, bearish precision
by product class (ordinary equities, equity ETFs / indices, inverse / leveraged ETFs, volatility products, bonds, FX,
commodities, crypto), and regime-conditioned Brier gain (volatility, market, rates, breadth, dispersion, earnings event)
with at least 100 independent observations — descriptive only, never promoted.
"""
from __future__ import annotations

import math
import random
import time
from typing import Dict, List, Optional, Tuple

from . import directional as D
from . import weights as W
from .lab import LAB_HORIZONS
from .weights import ERAS, SPLIT, Rec, _clustered_mean

RESEARCH_KEY = "lab:dirnext"
MAX_TRAIN = 20000
ITERS = 8
CLASS_LAMBDA = 200.0
FDR_Q = 0.10
ASSET_F = ["gap", "clv", "range_ratio", "rev_1d", "rev_5d", "vol_accel", "volume_surprise", "amihud", "rel_5d", "rel_21d",
           "pead_return", "pead_volume"]
MARKET_F = ["pct_above_50d", "breadth_thrust_10d", "net_new_highs", "dispersion_21d", "vix_z", "d_vix_5d", "spy_5d"]
FEATURES = ASSET_F + MARKET_F + [f + "×β" for f in MARKET_F]
COMPACT = ["rev_1d", "rev_5d", "gap", "vol_accel", "breadth_thrust_10d", "d_vix_5d"]
CHALLENGERS = {"global": FEATURES, "compact": COMPACT, "class": FEATURES}
PRIMARY = ["1D", "1W"]
BLOCKED = ["option skew / implied-volatility history", "positioning", "short-sale volume (limited history, 2019 on)"]
CAL_BINS = [(0.50, 0.55), (0.55, 0.60), (0.60, 0.65), (0.65, 0.70), (0.70, 0.75), (0.75, 1.01)]


def vid(lab: str, name: str) -> str:
    return f"directional-3-{lab.lower()}-{name}-exp"


def product_class(meta: dict) -> str:
    m = meta.get("meta") or {}
    cls, sec, a = meta.get("asset_class"), meta.get("sector") or "", meta.get("id")
    if m.get("product_type") in ("inverse_etf", "leveraged_etf") or sec == "Leveraged / Inverse":
        return "inverse / leveraged ETFs"
    if sec == "Volatility" or a in ("VXX", "UVXY", "VIXY", "SVXY", "VIX"):
        return "volatility products"
    if cls == "EQUITY":
        return "ordinary equities"
    if cls in ("TREASURY", "CORP_BOND") or sec == "Fixed Income":
        return "bonds"
    if cls == "FX" or sec == "Currency":
        return "FX"
    if cls == "CRYPTO" or sec == "Crypto":
        return "crypto"
    if cls == "COMMODITY" or sec in ("Precious Metals", "Energy", "Broad Commodities", "Agriculture", "Industrial Metals") and cls == "ETF":
        return "commodities"
    return "equity ETFs / indices"


# ------------------------------------------------------------------ features
class FeatureBuilder:
    def __init__(self, research, store):
        from . import newinfo as N
        self.N = N
        self.b = N.Builder(research, store)
        self.cal, self.n = self.b.cal, self.b.n
        self._px: Dict[str, tuple] = {}
        self._pead: Dict[str, dict] = {}
        br = self.b.wide("breadth_plus") or {}
        vix = self.b.macro("VIXCLS")
        from .alphanext import _expanding_z
        spy = self.b.series("SPY")
        self.market = {"pct_above_50d": br.get("pct_above_50d"), "breadth_thrust_10d": br.get("breadth_thrust_10d"),
                       "net_new_highs": br.get("net_new_highs"), "dispersion_21d": br.get("dispersion_21d"),
                       "vix_z": _expanding_z(vix),
                       "d_vix_5d": [(math.log(vix[i] / vix[i - 5]) if i >= 5 and vix[i] and vix[i - 5] else None) for i in range(len(vix))],
                       "spy_5d": [(math.log(spy[i] / spy[i - 5]) if i >= 5 and spy[i] and spy[i - 5] else None) for i in range(len(spy))]}

    def _series(self, a: str):
        if a not in self._px:
            b = self.b
            self._px[a] = (b.series(a, "open"), b.series(a, "high"), b.series(a, "low"), b.series(a, "close"), b.series(a),
                           b.series(a, "volume"))
        return self._px[a]

    def asset(self, meta: dict, i: int) -> Dict[str, Optional[float]]:
        a = meta["id"]
        o, hi, lo, c, adj, vol = self._series(a)
        out: Dict[str, Optional[float]] = {k: None for k in ASSET_F}
        if i < 64 or not c[i] or not c[i - 1] or not adj[i] or not adj[i - 1]:
            return out
        r = [math.log(adj[k] / adj[k - 1]) if adj[k] and adj[k - 1] else None for k in range(i - 63, i + 1)]
        rr = [x for x in r if x is not None]
        if len(rr) < 40:
            return out
        sd = math.sqrt(sum(x * x for x in rr) / len(rr))
        if sd <= 0:
            return out
        fresh = bool(vol[i]) and not (o[i] == hi[i] == lo[i] == c[i])           # a forward-filled day repeats stale values
        if fresh and o[i]:
            out["gap"] = math.log(o[i] / c[i - 1]) / sd
        if fresh and hi[i] and lo[i] and hi[i] > lo[i]:
            out["clv"] = ((c[i] - lo[i]) - (hi[i] - c[i])) / (hi[i] - lo[i])
            rng = [math.log(hi[k] / lo[k]) for k in range(i - 21, i) if hi[k] and lo[k] and hi[k] > lo[k]]
            if len(rng) >= 15 and sum(rng) > 0:
                out["range_ratio"] = math.log(math.log(hi[i] / lo[i]) / (sum(rng) / len(rng)))
        out["rev_1d"] = r[-1] / sd if r[-1] is not None else None
        if adj[i - 5]:
            out["rev_5d"] = math.log(adj[i] / adj[i - 5]) / (sd * math.sqrt(5))
        r5 = [x for x in r[-5:] if x is not None]
        if len(r5) >= 4:
            s5 = math.sqrt(sum(x * x for x in r5) / len(r5))
            if s5 > 0:
                out["vol_accel"] = math.log(s5 / sd)
        vs = sorted(v for v in vol[i - 60:i] if v)
        if fresh and vol[i] and len(vs) >= 30:
            out["volume_surprise"] = math.log(vol[i] / vs[len(vs) // 2])
        am = [abs(r[-k - 1]) / (vol[i - k] * c[i - k]) for k in range(21) if r[-k - 1] is not None and vol[i - k] and c[i - k]]
        if len(am) >= 15 and sum(am) > 0:
            out["amihud"] = math.log(sum(am) / len(am) * 1e9)
        if a not in self._pead:
            self._pead[a] = self.b.features("earnings_events", meta) or {}
        pe = self._pead[a]
        for k in ("pead_return", "pead_volume"):
            s = pe.get(k)
            out[k] = s[i] if s else None
        return out

    def rel(self, meta: dict, i: int, k: int) -> Optional[float]:
        adj = self._series(meta["id"])[4]
        return math.log(adj[i] / adj[i - k]) if i >= k and adj[i] and adj[i - k] else None


def build_rows(recs: List[Rec], fb: FeatureBuilder) -> List[Tuple[Rec, List[Optional[float]]]]:
    rows = []
    by_date: Dict[str, List[int]] = {}
    tmp = []
    for r in recs:
        i = (r.ext or {}).get("i")
        if i is None:
            continue
        v = fb.asset(r.meta, i)
        v["_r5"], v["_r21"] = fb.rel(r.meta, i, 5), fb.rel(r.meta, i, 21)
        tmp.append((r, v))
        by_date.setdefault(r.date, []).append(len(tmp) - 1)
    for d, ns in by_date.items():
        groups: Dict[tuple, List[int]] = {}
        for n in ns:
            m = tmp[n][0].meta
            groups.setdefault((m.get("asset_class"), m.get("sector")), []).append(n)
        for g in groups.values():
            for src, dst in (("_r5", "rel_5d"), ("_r21", "rel_21d")):
                vals = sorted(tmp[n][1][src] for n in g if tmp[n][1][src] is not None)
                if len(vals) < 3:
                    continue
                med = vals[len(vals) // 2]
                for n in g:
                    x = tmp[n][1][src]
                    s = (tmp[n][0].ext or {}).get("s")
                    tmp[n][1][dst] = ((x - med) / s) if x is not None and s else None
    for r, v in tmp:
        i = r.ext["i"]
        b = r.ext.get("beta")
        b = b if b is not None else 1.0
        vec = [v.get(k) for k in ASSET_F]
        mk = [(fb.market[k][i] if fb.market.get(k) and i < len(fb.market[k]) else None) for k in MARKET_F]
        vec += mk + [(x * b if x is not None else None) for x in mk]
        rows.append((r, vec))
    return rows


# ------------------------------------------------------------------ fitting and scoring
def _standardise(vs: List[List[Optional[float]]]):
    P = len(vs[0]) if vs else 0
    st = []
    for k in range(P):
        xs = [v[k] for v in vs if v[k] is not None]
        if len(xs) < 30:
            st.append((0.0, 0.0)); continue
        xs.sort()
        lo, hi = xs[int(0.01 * len(xs))], xs[int(0.99 * len(xs)) - 1]
        xs = [min(hi, max(lo, x)) for x in xs]
        m = sum(xs) / len(xs)
        sd = math.sqrt(sum((x - m) ** 2 for x in xs) / len(xs))
        st.append((m, sd, lo, hi))
    return st


def _z(v, st):
    out = []
    for x, s in zip(v, st):
        if x is None or not s[1] or s[1] < 1e-12:
            out.append(0.0)
        else:
            out.append(max(-4.0, min(4.0, (min(s[3], max(s[2], x)) - s[0]) / s[1])))
    return out


def fit_period(train, test, h: int, seed: int) -> Dict[str, Dict[int, float]]:
    """p_up for every test record under prior-only, current and each challenger (fitted on `train`)."""
    rnd = random.Random(seed)
    sub = train if len(train) <= MAX_TRAIN else rnd.sample(train, MAX_TRAIN)
    st = _standardise([v for _, v in sub])
    zp = lambda r: D.z_of(r, D.MAIN_PRIOR) or 0.0  # noqa: E731
    o = [D._obs(r) for r, _ in sub]
    pos = {k: j for j, k in enumerate(FEATURES)}
    zsub = [_z(v, st) for _, v in sub]
    out: Dict[str, Dict[int, float]] = {}

    def design(r, z, feats):
        return [1.0, zp(r), r.raw / 100.0] + [z[pos[k]] for k in feats]
    w0 = D.logistic([[1.0, zp(r)] for r, _ in sub], o, [0.0, 0.0], 1.0, 1.0, iters=ITERS)
    w1 = D.logistic([[1.0, zp(r), r.raw / 100.0] for r, _ in sub], o, [0.0] * 3, 1.0, 1.0, iters=ITERS)
    ws = {}
    for name in ("global", "compact"):
        X = [design(r, z, CHALLENGERS[name]) for (r, _), z in zip(sub, zsub)]
        ws[name] = D.logistic(X, o, [0.0] * len(X[0]), 1.0, 1.0, iters=ITERS)
    cls_w: Dict[str, List[float]] = {}
    by_cls: Dict[str, List[int]] = {}
    for k, (r, _) in enumerate(sub):
        by_cls.setdefault(r.meta.get("asset_class"), []).append(k)
    for c, ks in by_cls.items():
        if len(ks) < 500:
            continue
        X = [design(sub[k][0], zsub[k], FEATURES) for k in ks]
        cls_w[c] = D.logistic(X, [o[k] for k in ks], ws["global"], CLASS_LAMBDA, 1.0, iters=ITERS, start=ws["global"])
    for key in ("prior", "current", "global", "compact", "class"):
        out[key] = {}
    for idx, (r, v) in test:
        z = _z(v, st)
        out["prior"][idx] = D._sig(w0[0] + w0[1] * zp(r))
        out["current"][idx] = D._sig(w1[0] + w1[1] * zp(r) + w1[2] * r.raw / 100.0)
        for name in ("global", "compact"):
            x = design(r, z, CHALLENGERS[name])
            out[name][idx] = D._sig(sum(a * b for a, b in zip(ws[name], x)))
        wc = cls_w.get(r.meta.get("asset_class")) or ws["global"]
        out["class"][idx] = D._sig(sum(a * b for a, b in zip(wc, design(r, z, FEATURES))))
    out["_weights"] = {"prior": w0, "current": w1, **ws}
    return out


def calibration(recs: List[Rec], ps: List[float]) -> dict:
    """Confidence bins (probability of the predicted direction) and a logistic calibration slope / intercept."""
    bins = []
    for lo, hi in CAL_BINS:
        sel = [(p, D._obs(r)) for r, p in zip(recs, ps) if lo <= max(p, 1 - p) < hi]
        if not sel:
            bins.append({"bin": f"{lo:.0%}–{min(hi, 1):.0%}", "n": 0})
            continue
        conf = sum(max(p, 1 - p) for p, _ in sel) / len(sel)
        right = sum(1 for p, o in sel if (p >= 0.5) == bool(o)) / len(sel)
        bins.append({"bin": f"{lo:.0%}–{min(hi, 1):.0%}", "n": len(sel), "predicted": conf, "realised": right})
    X = [[1.0, math.log(max(1e-6, p) / max(1e-6, 1 - p))] for p in ps]
    o = [D._obs(r) for r in recs]
    step = max(1, len(X) // 30000)
    w = D.logistic(X[::step], o[::step], [0.0, 1.0], 1e-3, 1.0, iters=10)
    return {"bins": bins, "intercept": w[0], "slope": w[1]}


def bearish_by_class(recs: List[Rec], ps: List[float]) -> Dict[str, dict]:
    out: Dict[str, dict] = {}
    for r, p in zip(recs, ps):
        c = product_class(r.meta)
        x = out.setdefault(c, {"records": 0, "bear_calls": 0, "bear_right": 0, "bull_calls": 0, "bull_right": 0})
        x["records"] += 1
        if p < 0.5:
            x["bear_calls"] += 1; x["bear_right"] += 1 if r.yr < 0 else 0
        elif p > 0.5:
            x["bull_calls"] += 1; x["bull_right"] += 1 if r.yr > 0 else 0
    for x in out.values():
        x["bear_precision"] = (x["bear_right"] / x["bear_calls"]) if x["bear_calls"] else None
        x["bull_precision"] = (x["bull_right"] / x["bull_calls"]) if x["bull_calls"] else None
        x["down_rate"] = None
    return out


def evaluate(recs: List[Rec], preds: Dict[str, Dict[int, float]], h: int, full: bool = True) -> dict:
    """Every challenger against prior-only and the current formulation on identical records."""
    idx = sorted(preds["prior"])
    rs = [recs[i] for i in idx]
    pp = {k: [preds[k][i] for i in idx] for k in preds if not k.startswith("_")}
    out = {"n": len(rs), "prior": D.dir_metrics(rs, pp["prior"], h), "current": D.dir_metrics(rs, pp["current"], h), "challengers": {}}
    sub = {k: {i: preds[k][i] for i in idx} for k in pp}
    for name in CHALLENGERS:
        vp = D.paired(recs, h, sub, name, "prior")
        vc = D.paired(recs, h, sub, name, "current")
        m = D.dir_metrics(rs, pp[name], h)
        c = {"metrics": m, "vs_prior": vp, "vs_current": vc,
             "balanced_gain": ((m.get("balanced_accuracy") or 0) - (out["prior"].get("balanced_accuracy") or 0)),
             "adjustment": {"mean_abs": sum(abs(a - b) for a, b in zip(pp[name], pp["prior"])) / len(rs),
                            "share_over_2pp": sum(1 for a, b in zip(pp[name], pp["prior"]) if abs(a - b) > 0.02) / len(rs)}}
        if full:
            c["calibration"] = calibration(rs, pp[name])
            c["bearish"] = bearish_by_class(rs, pp[name])
        out["challengers"][name] = c
    if full:
        out["prior_calibration"] = calibration(rs, pp["prior"])
        out["prior_bearish"] = bearish_by_class(rs, pp["prior"])
    return out


def regimes(recs: List[Rec], preds: Dict[str, Dict[int, float]], h: int, fb: FeatureBuilder, rows_v: Dict[int, list]) -> dict:
    """Brier gain vs prior-only inside regime states (n_eff ≥ 100); descriptive."""
    idx = sorted(preds["prior"])
    pos = {k: j for j, k in enumerate(FEATURES)}
    states: Dict[str, List[int]] = {}
    for i in idx:
        r = recs[i]
        rg = r.reg or (None,) * 4
        keys = [f"volatility: {rg[1]}", f"market: {rg[0]}", f"rates: {rg[2]}"]
        v = rows_v.get(i) or []
        pa = v[pos["pct_above_50d"]] if v else None
        if pa is not None:
            keys.append("breadth high" if pa >= 0.5 else "breadth low")
        dp = v[pos["dispersion_21d"]] if v else None
        if dp is not None:
            keys.append("dispersion high" if dp >= 0.08 else "dispersion low")
        pv = v[pos["pead_volume"]] if v else None
        keys.append("earnings event (≤ 60 sessions)" if pv is not None else "no recent earnings event")
        for k in keys:
            if "None" not in k:
                states.setdefault(k, []).append(i)
    out = {}
    for k, ii in states.items():
        weeks = len({recs[i].wk for i in ii})
        n_eff = weeks * 5.0 / max(5.0, h)
        if n_eff < 100:
            out[k] = {"n_eff": n_eff, "insufficient": True}
            continue
        sub = {m: {i: preds[m][i] for i in ii} for m in ("prior", "global", "compact", "class")}
        out[k] = {"n_eff": n_eff, **{m: D.paired(recs, h, sub, m, "prior").get("brier_t") for m in ("global", "compact", "class")}}
    return out


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
    recs = [r for r, _ in rows]
    vecs = [v for _, v in rows]
    say(f"{lab}: {len(rows)} records ({time.time() - t0:.0f}s)")
    cov = {k: sum(1 for v in vecs if v[j] is not None) / len(vecs) for j, k in enumerate(FEATURES)} if vecs else {}
    allp: Dict[str, Dict[int, float]] = {}
    eras = []
    for k, (a, b) in enumerate(ERAS):
        train = [(r, v) for r, v in rows if r.end < a]
        test = [(i, (r, v)) for i, (r, v) in enumerate(rows) if a <= r.date < b]
        if len(train) < 2000 or len(test) < 500:
            eras.append({"from": a, "to": b, "status": "insufficient"})
            continue
        pr = fit_period(train, test, h, seed=k)
        ev = evaluate(recs, pr, h, full=False)
        eras.append({"from": a, "to": b, **ev})
        for m, d in pr.items():
            if not m.startswith("_"):
                allp.setdefault(m, {}).update(d)
        say(f"{lab}: era {a[:4]} ({time.time() - t0:.0f}s)")
    wf = evaluate(recs, allp, h, full=True)
    train = [(r, v) for r, v in rows if r.end < SPLIT]
    test = [(i, (r, v)) for i, (r, v) in enumerate(rows) if r.date >= SPLIT]
    split = evaluate(recs, fit_period(train, test, h, seed=99), h, full=False)
    fin = fit_period(rows, [], h, seed=7)
    return {"horizon": lab, "h": h, "records": len(rows), "coverage": cov, "walkforward": wf, "split": split, "eras": eras,
            "regimes": regimes(recs, allp, h, fb, {i: v for i, v in enumerate(vecs)}),
            "weights": {k: dict(zip(["intercept", "prior_z", "production"] + CHALLENGERS[k], w)) if k in CHALLENGERS else w
                        for k, w in fin["_weights"].items()},
            "seconds": round(time.time() - t0, 1)}


# ------------------------------------------------------------------ gates
def gates(hz: dict, name: str) -> dict:
    wf = hz["walkforward"]
    c = wf["challengers"][name]
    vp, vc = c["vs_prior"], c["vs_current"]
    pri = wf["prior"]
    g = {"t": vp.get("brier_t"), "brier_gain": vp.get("brier_gain"), "logloss_gain": vp.get("logloss_gain"),
         "balanced_gain": c.get("balanced_gain"), "vs_current_t": vc.get("brier_t")}
    g["G1"] = bool((vp.get("brier_t") or 0) >= 2 and (vp.get("logloss_gain") or 0) > 0 and (c.get("balanced_gain") or 0) > 0
                   and (vc.get("brier_t") or 0) >= 2)
    g["split_t"] = ((hz["split"]["challengers"].get(name) or {}).get("vs_prior") or {}).get("brier_t")
    g["G2"] = bool((g["split_t"] or 0) >= 1)
    full = [e for e in hz["eras"][:4] if e.get("challengers")]
    g["eras_won"] = sum(1 for e in full if ((e["challengers"][name]["vs_prior"] or {}).get("brier_gain") or 0) > 0)
    g["eras_complete"] = len(full)
    g["G3"] = g["eras_won"] >= 3
    cal = c.get("calibration") or {}
    g["ece"], g["ece_prior"] = (c["metrics"] or {}).get("ece"), pri.get("ece")
    g["slope"] = cal.get("slope")
    g["G4"] = bool(g["ece"] is not None and g["ece_prior"] is not None and g["ece"] <= g["ece_prior"] + 0.005
                   and g["slope"] is not None and 0.8 <= g["slope"] <= 1.25)
    return g


def finalise(res: dict) -> dict:
    from .alphanext import benjamini_hochberg, _p
    keys, ps = [], []
    for lab, hz in res["horizons"].items():
        for name in CHALLENGERS:
            g = gates(hz, name)
            hz["walkforward"]["challengers"][name]["gates"] = g
            keys.append((lab, name)); ps.append(_p(g["t"]))
    for (lab, name), r in zip(keys, benjamini_hochberg(ps, FDR_Q) if ps else []):
        c = res["horizons"][lab]["walkforward"]["challengers"][name]
        g = c["gates"]
        g["fdr"] = r
        g["passed"] = bool(g["G1"] and g["G2"] and g["G3"] and g["G4"] and r)
        c["status"] = "LIVE SHADOW ELIGIBLE" if g["passed"] else "REJECT"
    return res


def _worker(db_path: str, lab: str):
    from ..data.store import Store
    from .research import Research
    st = Store(db_path)
    try:
        return lab, study_horizon(st, Research(st), lab, progress=lambda m: print(m, flush=True))
    finally:
        st.close()


def run_all(db_path: str, workers: int = 2, progress=None) -> dict:
    """1D and 1W; 1M only when a short-horizon challenger passes every gate (fixed in advance)."""
    from concurrent.futures import ProcessPoolExecutor, as_completed
    from ..data.store import Store
    say = progress or (lambda m: None)
    t0 = time.time()
    res = {"started": time.strftime("%Y-%m-%d %H:%M:%S"), "features": FEATURES, "compact": COMPACT, "blocked": BLOCKED, "horizons": {}}
    with ProcessPoolExecutor(max_workers=workers) as ex:
        for f in as_completed([ex.submit(_worker, db_path, lab) for lab in PRIMARY]):
            lab, hz = f.result()
            res["horizons"][lab] = hz
            say(f"{lab} finished")
    res["horizons"] = {lab: res["horizons"][lab] for lab in PRIMARY}
    finalise(res)
    if any(c.get("status") == "LIVE SHADOW ELIGIBLE" for hz in res["horizons"].values() for c in hz["walkforward"]["challengers"].values()):
        lab, hz = _worker(db_path, "1M")
        res["horizons"]["1M"] = hz
        finalise(res)
        res["1M"] = "run: a short-horizon challenger passed every gate"
    else:
        res["1M"] = "not run: no 1D or 1W challenger passed every gate (rule fixed in advance)"
    res["seconds"] = round(time.time() - t0, 1)
    st = Store(db_path)
    try:
        st.kv_set(RESEARCH_KEY, res)
    finally:
        st.close()
    return res
