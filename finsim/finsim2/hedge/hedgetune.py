"""ML Lab — fine-tuning the Shaffer Hedge parameters (report SHAFFER_HEDGE_FINETUNE.md). Research only: hedge-2, its
sizing, the resizing live shadow and the fixed gates H1–H5 are unchanged.

There is no single optimal hedge multiplier: risk-only users prefer bigger hedges, profit-sensitive users lighter ones.
So this module learns a SURFACE, not a point:
    m(objective, λ, risk class, horizon, volatility regime) · hedge-2's size,   m ∈ [0.5, 1.5]
Best fit per node and λ on outcomes matured before each era, shrunk toward the parent (dates / (dates + 60)), then made
monotone non-increasing in λ (isotonic) — a user who cares more about profit never gets a larger hedge. Validated
walk-forward, per λ, against hedge-2 at that same λ, with the Hedge program's gates (H1 at that λ with BH FDR, H2 eras,
H3 tail, H4 cost / basis, H5 without crises).

Also: the historical utility frontier (risk reduction against profit given up + cost) with its minimum-risk, balanced and
profit-preserving points; product preference by objective × volatility regime as an interpretable prior; what the H4
failures are made of (cost, basis, which objectives) — H4 itself is NOT changed, a redesign is only proposed for a new
research version; and whether validated 1W Alpha strength changes the optimal hedge.
"""
from __future__ import annotations

import math
import time
from typing import Dict, List, Optional, Tuple

from . import hedgelearn as HL
from . import hedgenext as HN
from . import volhedge as V

RESEARCH_KEY = "lab:hedgetune"
LAMBDAS = [0.5, 1.0, 2.0, 5.0, 10.0]
K_POOL = 60
FRONTIER_M = [round(0.5 + 0.1 * k, 1) for k in range(11)]
ALPHA_BUCKETS = [("weak", 0.0, 0.15), ("medium", 0.15, 0.30), ("strong", 0.30, 0.51)]
VID = "hedge-lambda-sizing-exp"


def _path(c: dict) -> List[str]:
    p = HL.hpath(c)[:3]
    return p + [f"{p[2]}|{HN._vol_state(c)}"]


def _parent(nd: str) -> str:
    return nd.split("|")[0] if "|" in nd else HL._parent(nd)


def _isotonic_dec(vals: List[float]) -> List[float]:
    """Pool-adjacent-violators for a non-increasing sequence."""
    blocks = [[v, 1] for v in vals]
    i = 0
    while i < len(blocks) - 1:
        if blocks[i][0] < blocks[i + 1][0]:
            tot = blocks[i][0] * blocks[i][1] + blocks[i + 1][0] * blocks[i + 1][1]
            n = blocks[i][1] + blocks[i + 1][1]
            blocks[i] = [tot / n, n]
            del blocks[i + 1]
            i = max(0, i - 1)
        else:
            i += 1
    out = []
    for v, n in blocks:
        out += [v] * n
    return out


def fit_surface(train: List[dict], min_dates: int = 5) -> Dict[str, dict]:
    """Per node: dates, the best-fit multiple at every λ (in-sample on `train`)."""
    groups: Dict[str, List[dict]] = {}
    for c in train:
        for nd in c["_tp"]:
            groups.setdefault(nd, []).append(c)
    out = {}
    for nd, cs in groups.items():
        n = HL._dates(cs)
        out[nd] = {"n": n, "m_hat": {l: (HL._best_mult(cs, l)[0] if n >= min_dates else None) for l in LAMBDAS}}
    return out


def shrink_surface(fit: Dict[str, dict], K: float = K_POOL) -> Dict[str, Dict[float, float]]:
    order = sorted(fit, key=lambda nd: (0 if nd == "global" else (1 if nd.startswith("risk:") else (2 if "|" not in nd else 3)), nd))
    out: Dict[str, Dict[float, float]] = {}
    for nd in order:
        par = out.get(_parent(nd)) if nd != "global" else {l: 1.0 for l in LAMBDAS}
        par = par or {l: 1.0 for l in LAMBDAS}
        f = fit[nd]
        a = f["n"] / (f["n"] + K)
        raw = [(a * f["m_hat"][l] + (1 - a) * par[l]) if f["m_hat"].get(l) is not None else par[l] for l in LAMBDAS]
        out[nd] = dict(zip(LAMBDAS, _isotonic_dec(raw)))
    return out


def mult_at(c: dict, surf: Dict[str, Dict[float, float]], lam: float) -> float:
    m = 1.0
    for nd in c["_tp"]:
        if nd in surf:
            m = surf[nd][lam]
    return m


def cell_at(cases: List[dict], objective: str, arm: str, lam: float) -> dict:
    """hedgenext.cell at a chosen λ: the paired ΔU, its bootstrap p and the gates' inputs at that λ."""
    if not cases:
        return {"cases": 0}
    L = str(lam)
    p = V.paired(cases, objective, "A", arm)
    eras = {}
    for a, b in V.ERA_ALL:
        sub = [c for c in cases if a <= c["date"] < b]
        d = (V.paired(sub, objective, "A", arm, reps=0).get("d") or {}).get(L) if len(sub) >= 20 else None
        eras[V._era(a)] = {"d": d, "complete": (a, b) in V.ERAS4}
    nc = [c for c in cases if not any(x <= c["date"] < y for x, y in V.CRISES.values())]
    pc = V.paired(nc, objective, "A", arm, reps=0)
    ci = (p.get("ci") or {}).get(L)
    dl = (p.get("d") or {}).get(L)
    a_, b_ = p.get("a") or {}, p.get("b") or {}
    full = [e for e in eras.values() if e["complete"] and e["d"] is not None]
    worse_es = (b_.get("es_h") or 0) - (a_.get("es_h") or 0)
    worse_cost = ((b_.get("cost") or 0) / a_["cost"] - 1) if a_.get("cost") else (0.0 if not b_.get("cost") else 1.0)
    worse_basis = ((b_.get("basis_error") or 0) / a_["basis_error"] - 1) if a_.get("basis_error") else 0.0
    dates = len({c["date"] for c in cases})
    g = {"enough": dates >= V.MIN_DATES, "H2": sum(1 for e in full if e["d"] > 0) >= 3, "eras_won": sum(1 for e in full if e["d"] > 0),
         "eras_complete": len(full), "H3": bool(a_.get("es_u") and worse_es <= V.G4_ES * a_["es_u"]),
         "H4": bool(worse_cost <= V.G4_COST and worse_basis <= V.G4_BASIS), "H5": bool(((pc.get("d") or {}).get(L) or 0) > 0),
         "p": (p.get("p") or {}).get(L), "d": dl, "cost_increase": worse_cost, "basis_increase": worse_basis}
    return {"dates": dates, "lam": lam, "d": dl, "ci": ci, "t": (dl / ((ci[1] - ci[0]) / 3.92)) if ci and dl is not None and ci[1] > ci[0] else None,
            "a": {k: a_.get(k) for k in ("loss_reduction", "profit_sacrificed", "cost", "basis_error", "es_h", "risk_u", "risk_h")},
            "b": {k: b_.get(k) for k in ("loss_reduction", "profit_sacrificed", "cost", "basis_error", "es_h", "risk_u", "risk_h")},
            "gates": g}


def surface_walk_forward(cases: List[dict], progress=None) -> Tuple[Dict[float, List[dict]], Dict[str, dict], Dict[str, Dict[float, float]]]:
    """For every λ: the learned multiple applied to each era's cases (fitted before the era); and today's surface."""
    say = progress or (lambda m: None)
    scored: Dict[float, List[dict]] = {l: [] for l in LAMBDAS}
    for a, b in HL._eras():
        train = [c for c in cases if c["end"] < a]
        test = [c for c in cases if a <= c["date"] < b]
        if HL._dates(train) < HN.MIN_TRAIN_DATES or not test:
            continue
        surf = shrink_surface(fit_surface(train))
        for lam in LAMBDAS:
            for c in test:
                c.setdefault("_ms", {})[lam] = mult_at(c, surf, lam)
            scored[lam] = scored[lam] + test
        say(f"surface era {a}: {len(test)} cases")
    fin_fit = fit_surface(cases)
    return scored, fin_fit, shrink_surface(fin_fit)


def frontier(cases: List[dict], objective: str) -> List[dict]:
    """In-sample historical frontier over hedge-2 multiples: risk reduction against profit given up + cost."""
    pts = []
    for m in FRONTIER_M:
        for c in cases:
            HN._scaled(c, m, "T")
        o = V.outcome(cases, "T", objective)
        if not o.get("n"):
            continue
        pts.append({"m": m, "risk_reduction": o.get("loss_reduction"), "profit_sacrificed": o.get("profit_sacrificed"), "cost": o.get("cost"),
                    "basis": o.get("basis_error"), "es_h": o.get("es_h"), **{f"U{l}": V.utility(o, l) for l in LAMBDAS}})
    if not pts:
        return pts
    pts_ok = [p for p in pts if p["risk_reduction"] is not None]
    if pts_ok:
        max(pts_ok, key=lambda p: p["risk_reduction"])["mark"] = "minimum risk"
        best1 = max(pts_ok, key=lambda p: p.get("U1.0") or -1e18)
        best1["mark"] = (best1.get("mark", "") + " · balanced (λ = 1)").strip(" ·")
        best10 = max(pts_ok, key=lambda p: p.get("U10.0") or -1e18)
        best10["mark"] = (best10.get("mark", "") + " · profit-preserving (λ = 10)").strip(" ·")
    return pts


def product_regime(cases: List[dict]) -> Dict[str, dict]:
    """Realised advantage of each forced product type over hedge-2's choice per objective × volatility regime, shrunk
    toward the objective (dates / (dates + 60)) — an interpretable preference prior, not a product selector."""
    out = {}
    for c in cases:
        c["_tp"] = _path(c)
    by: Dict[str, List[dict]] = {}
    for c in cases:
        for nd in c["_tp"][2:]:
            by.setdefault(nd, []).append(c)
    for nd, cs in by.items():
        res = {}
        for t in sorted({k for c in cs for k in c["arms"] if k.startswith("type:")}):
            sub = [c for c in cs if t in c["arms"]]
            n = HL._dates(sub)
            if n < 20:
                continue
            o = sub[0]["objective"]
            ua, ut = HN._u(sub, "A", o), HN._u(sub, t, o)
            if ua is None or ut is None:
                continue
            res[t.replace("type:", "")] = {"dates": n, "advantage": ut - ua}
        out[nd] = res
    shr = {}
    for nd, res in out.items():
        par = out.get(nd.split("|")[0], {}) if "|" in nd else {}
        shr[nd] = {t: {**v, "shrunk": (v["dates"] / (v["dates"] + K_POOL)) * v["advantage"] + (K_POOL / (v["dates"] + K_POOL)) * (par.get(t, {}).get("advantage") or 0.0)}
                   for t, v in res.items()}
    return shr


def book_alpha(book: dict, date: str, amap: Dict[str, List[Tuple[str, float]]]) -> Optional[float]:
    import bisect
    tot = wsum = 0.0
    for a, w in book["w"].items():
        xs = amap.get(a) or []
        k = bisect.bisect_right(xs, (date, float("inf"))) - 1
        if k < 0 or HN._days(date, xs[k][0]) > 9:
            continue
        tot += w * xs[k][1]
        wsum += abs(w)
    return (tot / wsum) if wsum >= 0.5 else None


def alpha_conditional(cases: List[dict], amap) -> dict:
    """Split hedge cases by the book's validated 1W Alpha strength (weak / medium / strong, by the distance of its
    out-of-sample percentile from the middle) and sign; the best multiple per bucket and λ, and a walk-forward test of
    'multiple per Alpha bucket' against 'one multiple' (both learned before each era)."""
    books = {b["key"]: b for b in V.BOOKS}
    sub = []
    for c in cases:
        b = books.get(c["book"])
        if not b:
            continue
        a = book_alpha(b, c["date"], amap)
        if a is None:
            continue
        c["_alpha"] = a
        c["_ab"] = next(nm for nm, lo, hi in ALPHA_BUCKETS if lo <= abs(a) < hi) + (" +" if a >= 0 else " −")
        sub.append(c)
    if not sub:
        return {"cases": 0}
    buckets = {}
    for bk in sorted({c["_ab"] for c in sub}):
        cs = [c for c in sub if c["_ab"] == bk]
        buckets[bk] = {"cases": len(cs), "dates": HL._dates(cs), "best_by_lambda": {str(l): HL._best_mult(cs, l)[0] for l in LAMBDAS}}
    # walk-forward: one multiple vs a multiple per bucket (λ = 1)
    for c in sub:
        c.pop("_mL", None)
    scored = []
    for a, b in HL._eras():
        train = [c for c in sub if c["end"] < a]
        test = [c for c in sub if a <= c["date"] < b]
        if HL._dates(train) < HN.MIN_TRAIN_DATES or not test:
            continue
        one = HL._best_mult(train, 1.0)[0]
        per = {}
        for bk in {c["_ab"] for c in train}:
            tr = [c for c in train if c["_ab"] == bk]
            n = HL._dates(tr)
            per[bk] = (n / (n + K_POOL)) * HL._best_mult(tr, 1.0)[0] + (K_POOL / (n + K_POOL)) * one if n >= 5 else one
        for c in test:
            HN._scaled(c, one, "P")
            m = per.get(c["_ab"], one)
            c["arms"]["Q"] = _scaled_arm(c, m)
        scored += test
    by_obj = {}
    for o in sorted({c["objective"] for c in scored}):
        cs = [c for c in scored if c["objective"] == o]
        p = V.paired(cs, o, "P", "Q")
        ci = (p.get("ci") or {}).get("1.0")
        d = (p.get("d") or {}).get("1.0")
        by_obj[o] = {"dates": HL._dates(cs), "d": d, "t": (d / ((ci[1] - ci[0]) / 3.92)) if ci and d is not None and ci[1] > ci[0] else None,
                     "p": (p.get("p") or {}).get("1.0")}
    return {"cases": len(sub), "buckets": buckets, "alpha_vs_one": by_obj}


def _scaled_arm(c: dict, m: float) -> dict:
    a = c["arms"]["A"]
    return {"hp": [m * x for x in a["hp"]], "cost": m * a["cost"], "skew": m * a["skew"], "mult": m,
            "legs": [{**L_, "q": L_["q"] * m, "notional": L_["notional"] * m, "beta_usd": L_["beta_usd"] * m} for L_ in a["legs"]]}


def h4_anatomy(cells: Dict[str, Dict[str, dict]]) -> List[dict]:
    """Every surface cell at λ = 1 that survived FDR but failed H4: how far cost and basis error rose against the fixed
    thresholds, what the extra risk reduction cost per dollar, and the objective's type."""
    out = []
    for lab, objs in cells.items():
        for key, c in objs.items():
            g = c.get("gates") or {}
            if c.get("lam") != 1.0 or not g.get("fdr") or g.get("H4"):
                continue
            a, b = c.get("a") or {}, c.get("b") or {}
            d_risk = (b.get("loss_reduction") or 0) - (a.get("loss_reduction") or 0)
            d_cost = (b.get("cost") or 0) - (a.get("cost") or 0)
            obj = key.split("/")[-1]
            out.append({"horizon": lab, "node": key, "objective": obj,
                        "type": "variance-sensitive" if obj in V.VARIANCE_SENSITIVE else "hard-target",
                        "cost_increase": g.get("cost_increase"), "basis_increase": g.get("basis_increase"),
                        "failed_on": ", ".join(x for x, bad in (("cost", (g.get("cost_increase") or 0) > V.G4_COST),
                                                               ("basis", (g.get("basis_increase") or 0) > V.G4_BASIS)) if bad),
                        "extra_risk_reduction": d_risk, "extra_cost": d_cost, "extra_profit_sacrificed": (b.get("profit_sacrificed") or 0) - (a.get("profit_sacrificed") or 0),
                        "risk_per_cost_dollar": (d_risk / d_cost) if d_cost > 0 else None, "d": c.get("d"), "t": c.get("t")})
    return out


def run(db_path: str, amap: Optional[dict] = None, progress=None) -> dict:
    say = progress or (lambda m: None)
    t0 = time.time()
    res = {"started": time.strftime("%Y-%m-%d %H:%M:%S"), "surface": {}, "cells": {}, "frontier": {}, "products": {}, "alpha": {}}
    for lab, h, _ in V.HORIZONS:
        try:
            cases = [c for d in V.load_dates(db_path, lab) for c in d["cases"]]
        except FileNotFoundError:
            continue
        for c in cases:
            c["end"] = HL._end(c["date"], h)
            c["_tp"] = _path(c)
        scored, fin_fit, surf = surface_walk_forward(cases, lambda m: say(f"{lab} {m}"))
        res["surface"][lab] = {nd: {"dates": fin_fit[nd]["n"], "best_fit": {str(l): fin_fit[nd]["m_hat"][l] for l in LAMBDAS},
                                    "shrunk": {str(l): v for l, v in surf[nd].items()}} for nd in surf if nd.count("/") <= 1 or "|" in nd}
        res["cells"][lab] = {}
        for lam in LAMBDAS:
            cs = scored[lam]
            for c in cs:
                c["arms"]["L"] = _scaled_arm(c, c["_ms"][lam])
            for nd in sorted({c["_tp"][2] for c in cs}):
                sub = [c for c in cs if c["_tp"][2] == nd]
                if HL._dates(sub) < 30:
                    continue
                cell = cell_at(sub, sub[0]["objective"], "L", lam)
                res["cells"][lab][f"{nd.split(':', 1)[1]}@{lam}"] = {**cell, "node": nd, "objective": sub[0]["objective"]}
        # the frontier per risk class × objective (in-sample, descriptive)
        res["frontier"][lab] = {}
        for nd in sorted({c["_tp"][2] for c in cases}):
            sub = [c for c in cases if c["_tp"][2] == nd]
            if HL._dates(sub) >= 30:
                res["frontier"][lab][nd.split(":", 1)[1]] = frontier(sub, sub[0]["objective"])
        if amap:
            res["alpha"][lab] = alpha_conditional(cases, amap)
        say(f"{lab}: surface, frontier, Alpha link ({time.time() - t0:.0f}s)")
    for lab, h, _ in HN.PRODUCT_H:
        try:
            cases = [c for d in HN.load(db_path, f"product_{lab}_dates.pkl") for c in d["cases"]]
        except FileNotFoundError:
            continue
        res["products"][lab] = product_regime(cases)
    _finalise(res)
    res["h4"] = h4_anatomy({lab: {k: v for k, v in cs.items()} for lab, cs in res["cells"].items()})
    res["seconds"] = round(time.time() - t0, 1)
    return res


def _finalise(res: dict):
    keys, ps = [], []
    for lab, cs in res["cells"].items():
        for k, c in cs.items():
            g = c.get("gates") or {}
            if g.get("enough") and g.get("p") is not None:
                keys.append((lab, k)); ps.append(g["p"])
    for (lab, k), r in zip(keys, V.benjamini_hochberg(ps, HN.FDR_Q) if ps else []):
        res["cells"][lab][k]["gates"]["fdr"] = r
    for lab, cs in res["cells"].items():
        for k, c in cs.items():
            g = c.get("gates") or {}
            g["H1"] = bool(g.get("enough") and (g.get("d") or 0) > 0 and g.get("fdr"))
            ok = g["H1"] and g.get("H2") and g.get("H3") and g.get("H4") and g.get("H5")
            c["status"] = "INSUFFICIENT DATA" if not g.get("enough") else ("LIVE SHADOW ELIGIBLE" if ok else "NO HEDGE IMPROVEMENT")


H4_PROPOSAL = ("Proposed for a NEW research version only (not applied here, not tested on these results): replace the flat "
               "'cost ≤ +10%, basis error ≤ +5%' guard by an objective-aware efficiency test — the extra risk reduction bought "
               "per extra dollar of cost must be ≥ 1, the gain must hold at λ = 1 and λ = 2, and for hard-target objectives "
               "the basis error must stay within +25% while variance-sensitive objectives are judged on realised variance "
               "only. It would be committed before any run that uses it.")
