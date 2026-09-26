"""ML Lab — the historically supported Shaffer Hedge parameters (part of SHAFFER_LEARNED_WEIGHTS.md).

Same philosophy as the learned Shaffer weights: FIND the parameters history supports, then decide how much to trust them.
Research only — hedge-2 is never changed by this module.

Cases: the saved point-in-time hedge-2 replays (every book × objective × date, realised over the horizon; 1W / 1M / 3M)
and the forced-product replays (1W / 1M: hedge-2's package and the engine's best single product of every type).

Hierarchy:  Global → Risk class (Equity / Rates / Credit / Commodity / FX / Crypto / Volatility, from the objective's
            primary risk factor) → Objective → Product class (hedge-2's lead product type) → Instrument (its lead product).

Parameters learned (U = risk reduction − λ·profit sacrificed − cost, λ = 1 unless stated):
    sizing multiplier   m ∈ [0.5, 1.5] scaling hedge-2's package; best fit m̂ per node, shrunk toward the parent,
                        m_node = α·m̂ + (1 − α)·m_parent, α = dates / (dates + K) (global: toward 1 = hedge-2)
    product preference  the realised utility advantage of each product type over hedge-2's choice, per node, shrunk
    cost sensitivity    κ: how much the case's ex-ante cost difference counts against a product's historical advantage
    basis-risk penalty  β: how much the dispersion of a product's historical advantage (its hedge-error risk) counts
    tail-risk price     what extra tail protection (95% ES reduction from sizing up) historically cost in λ = 1 utility,
                        and the multiplier that is best at every λ (a risk preference, not something history can choose)
K, κ and β are chosen by walk-forward on earlier eras only (nested). Validation: walk-forward over the four complete eras
and 2025–, paired against hedge-2 on identical cases, the Hedge program's fixed gates H1–H5 and BH FDR.
"""
from __future__ import annotations

import math
import time
from typing import Dict, List, Optional, Tuple

from . import hedgenext as HN
from . import volhedge as V

K_GRID_H = [5, 20, 60, 200]
KAPPA_GRID = [0.0, 0.5, 1.0, 2.0]
BETA_GRID = [0.0, 0.25, 0.5, 1.0]
LAMBDAS = [0.5, 1.0, 2.0, 5.0, 10.0]
H_LEVELS = ["global", "risk", "objective", "product", "instrument"]
_CREDIT_BOOK = {"credit portfolio"}
_BOND_BOOK = {"bond portfolio"}


def risk_class(c: dict) -> str:
    p = (c.get("primary") or "").split(":")[0]
    if p in ("MKT", "SEC"):
        return "Equity"
    if p == "IDIO":
        return "Credit" if c.get("btype") in _CREDIT_BOOK else ("Rates" if c.get("btype") in _BOND_BOOK else "Equity")
    return {"RATE": "Rates", "CREDIT": "Credit", "CMD": "Commodity", "FX": "FX", "CRYPTO": "Crypto", "VOL": "Volatility"}.get(p, "Other")


def _lead(arm: dict) -> Tuple[str, str]:
    legs = arm.get("legs") or []
    if not legs:
        return "no hedge", "none"
    L = max(legs, key=lambda x: abs(x.get("notional") or 0.0))
    return L.get("product_type") or L.get("type") or "?", L.get("id") or "?"


def hpath(c: dict) -> List[str]:
    rc = risk_class(c)
    pt, inst = _lead(c["arms"]["A"])
    o = c["objective"]
    return ["global", f"risk:{rc}", f"objective:{rc}/{o}", f"product:{rc}/{o}/{pt}", f"instrument:{rc}/{o}/{pt}/{inst}"]


def _dates(cs: List[dict]) -> int:
    return len({c["date"] for c in cs})


def _u_multi(cases: List[dict], arm: str, lam: float = 1.0) -> Optional[float]:
    """Utility summed over objectives (each objective's own risk measure), per $1M over the horizon."""
    by: Dict[str, List[dict]] = {}
    for c in cases:
        by.setdefault(c["objective"], []).append(c)
    tot, ok = 0.0, False
    for o, cs in by.items():
        u = V.utility(V.outcome(cs, arm, o), lam)
        if u is not None:
            tot += u
            ok = True
    return tot if ok else None


def _best_mult(cases: List[dict], lam: float = 1.0) -> Tuple[float, Dict[float, float]]:
    curve = {}
    for m in HN.MULTS:
        for c in cases:
            HN._scaled(c, m, "T")
        u = _u_multi(cases, "T", lam)
        if u is not None:
            curve[m] = u
    if not curve:
        return 1.0, {}
    best = max(curve, key=lambda m: (curve[m], -abs(m - 1.0)))
    return best, curve


# ------------------------------------------------------------------ sizing
def node_groups(cases: List[dict]) -> Dict[str, List[dict]]:
    g: Dict[str, List[dict]] = {}
    for c in cases:
        for nd in c["_path"]:
            g.setdefault(nd, []).append(c)
    return g


def fit_sizing(train: List[dict], min_dates: int = 5) -> Dict[str, dict]:
    """Best-fit multiple m̂ and dates for every node with training cases (independent of K)."""
    out = {}
    for nd, cs in node_groups(train).items():
        n = _dates(cs)
        if n < min_dates:
            out[nd] = {"n": n, "m_hat": None}
            continue
        m, _ = _best_mult(cs)
        out[nd] = {"n": n, "m_hat": m}
    return out


def shrink_sizing(fit: Dict[str, dict], K: float) -> Dict[str, float]:
    """m_node = α·m̂ + (1 − α)·m_parent (global toward 1.0 = hedge-2), α = dates / (dates + K)."""
    out: Dict[str, float] = {}
    order = sorted(fit, key=lambda nd: H_LEVELS.index(nd.split(":")[0]) if nd != "global" else 0)
    for nd in order:
        if nd == "global":
            parent_m = 1.0
        else:
            parent_m = out.get(_parent(nd), 1.0)
        f = fit[nd]
        if f["m_hat"] is None:
            out[nd] = parent_m
            continue
        a = f["n"] / (f["n"] + K)
        out[nd] = a * f["m_hat"] + (1 - a) * parent_m
    return out


def _parent(nd: str) -> str:
    lvl, rest = nd.split(":", 1)
    k = H_LEVELS.index(lvl)
    if k == 1:
        return "global"
    parts = rest.split("/")
    return f"{H_LEVELS[k - 1]}:{'/'.join(parts[:-1])}"


def mult_for(c: dict, sizing: Dict[str, float]) -> float:
    m = 1.0
    for nd in c["_path"]:
        if nd in sizing:
            m = sizing[nd]
    return m


def _eras():
    return [(a, b) for a, b in V.ERA_ALL]


def sizing_walk_forward(cases: List[dict], progress=None) -> dict:
    """Learn the hierarchical sizing on cases matured before each era, choose K on earlier eras (nested), apply to
    the era's cases as arm 'L'. Returns the scored cases, per-era choices and the final (today's) parameters."""
    say = progress or (lambda m: None)
    fits = {}
    for a, b in _eras() + [("2100-01-01", "2100-01-01")]:
        train = [c for c in cases if c["end"] < a]
        if _dates(train) < HN.MIN_TRAIN_DATES:
            continue
        fits[a] = fit_sizing(train)
    scored, choices = [], {}
    # the inner performance of every K in every era (the rule fitted before that era, applied to it)
    inner_u: Dict[str, Dict[float, float]] = {}
    for a, b in _eras():
        test = [c for c in cases if a <= c["date"] < b]
        if not test or a not in fits:
            continue
        inner_u[a] = {}
        for K in K_GRID_H:
            sz = shrink_sizing(fits[a], K)
            for c in test:
                HN._scaled(c, mult_for(c, sz), "T")
            ua, ut = _u_multi(test, "A"), _u_multi(test, "T")
            inner_u[a][K] = (ut - ua) if ua is not None and ut is not None else 0.0
    for a, b in _eras() + [("2100-01-01", "2100-01-01")]:
        prev = [e for e in inner_u if e < a]
        K = max(K_GRID_H, key=lambda k: sum(inner_u[e][k] for e in prev)) if prev else 60
        choices[a] = K
        if a == "2100-01-01" or a not in fits:
            continue
        sz = shrink_sizing(fits[a], K)
        for c in [c for c in cases if a <= c["date"] < b]:
            HN._scaled(c, mult_for(c, sz), "L")
            scored.append(c)
    final_fit = fits.get("2100-01-01") or {}
    final = shrink_sizing(final_fit, choices["2100-01-01"]) if final_fit else {}
    say(f"sizing: {len(scored)} cases scored walk-forward")
    return {"scored": scored, "K": choices, "final_fit": final_fit, "final": final}


# ------------------------------------------------------------------ product preference, cost sensitivity, basis penalty
def _blocks_sd(train: List[dict], t: str, objective: str, k: int = 5) -> Optional[float]:
    ds = sorted({c["date"] for c in train if t in c["arms"]})
    if len(ds) < 2 * k:
        return None
    vals = []
    for j in range(k):
        sel = set(ds[j::k])
        sub = [c for c in train if c["date"] in sel and t in c["arms"]]
        ua, ut = HN._u(sub, "A", objective), HN._u(sub, t, objective)
        if ua is not None and ut is not None:
            vals.append(ut - ua)
    if len(vals) < 3:
        return None
    m = sum(vals) / len(vals)
    return math.sqrt(sum((v - m) ** 2 for v in vals) / (len(vals) - 1))


def fit_products(train: List[dict]) -> Dict[str, Dict[str, dict]]:
    """Per node (global / risk class / objective): every product type's realised advantage over hedge-2 and its
    dispersion, where it was available on ≥ MIN_TRAIN_DATES dates."""
    out: Dict[str, Dict[str, dict]] = {}
    for nd, cs in node_groups(train).items():
        if nd.split(":")[0] not in ("global", "risk", "objective"):
            continue
        res = {}
        for t in sorted({k for c in cs for k in c["arms"] if k.startswith("type:")}):
            sub = [c for c in cs if t in c["arms"]]
            n = _dates(sub)
            if n < HN.MIN_TRAIN_DATES:
                continue
            by: Dict[str, List[dict]] = {}
            for c in sub:
                by.setdefault(c["objective"], []).append(c)
            adv = 0.0
            for o, cc in by.items():
                ua, ut = HN._u(cc, "A", o), HN._u(cc, t, o)
                if ua is not None and ut is not None:
                    adv += ut - ua
            sd = _blocks_sd(sub, t, sub[0]["objective"]) if nd.startswith("objective:") else None
            res[t] = {"n": n, "adv": adv, "sd": sd}
        out[nd] = res
    return out


def shrink_products(fit: Dict[str, Dict[str, dict]], K: float) -> Dict[str, Dict[str, float]]:
    out: Dict[str, Dict[str, float]] = {}
    for nd in sorted(fit, key=lambda n: 0 if n == "global" else (1 if n.startswith("risk:") else 2)):
        par = out.get(_parent(nd), {}) if nd != "global" else {}
        res = dict(par)
        for t, x in fit[nd].items():
            a = x["n"] / (x["n"] + K)
            res[t] = a * x["adv"] + (1 - a) * par.get(t, 0.0)
        out[nd] = res
    return out


def choose_product(c: dict, prefs: Dict[str, Dict[str, float]], sds: Dict[str, Dict[str, dict]], kappa: float, beta: float) -> Optional[str]:
    node = c["_path"][2]
    pref = prefs.get(node) or prefs.get(c["_path"][1]) or prefs.get("global") or {}
    cost_a = c["arms"]["A"]["cost"]
    best, bs = None, 0.0
    for t in c["arms"]:
        if not t.startswith("type:") or t not in pref:
            continue
        sd = ((sds.get(node) or {}).get(t) or {}).get("sd") or 0.0
        s = pref[t] - kappa * (c["arms"][t]["cost"] - cost_a) - beta * sd
        if s > bs:
            best, bs = t, s
    return best


def product_walk_forward(cases: List[dict], progress=None) -> dict:
    say = progress or (lambda m: None)
    fits = {}
    for a, b in _eras() + [("2100-01-01", "2100-01-01")]:
        train = [c for c in cases if c["end"] < a]
        if _dates(train) < HN.MIN_TRAIN_DATES:
            continue
        fits[a] = fit_products(train)
    grid = [(K, k, b_) for K in K_GRID_H for k in KAPPA_GRID for b_ in BETA_GRID]
    inner: Dict[str, Dict[tuple, float]] = {}
    for a, b in _eras():
        test = [c for c in cases if a <= c["date"] < b]
        if not test or a not in fits:
            continue
        inner[a] = {}
        prefs_by_K = {K: shrink_products(fits[a], K) for K in K_GRID_H}
        for g in grid:
            for c in test:
                t = choose_product(c, prefs_by_K[g[0]], fits[a], g[1], g[2])
                c["arms"]["T"] = c["arms"][t] if t else c["arms"]["A"]
            ua, ut = _u_multi(test, "A"), _u_multi(test, "T")
            inner[a][g] = (ut - ua) if ua is not None and ut is not None else 0.0
    scored, choices = [], {}
    for a, b in _eras() + [("2100-01-01", "2100-01-01")]:
        prev = [e for e in inner if e < a]
        g = max(grid, key=lambda x: (sum(inner[e][x] for e in prev), -x[1] - x[2])) if prev else (60, 1.0, 0.0)
        choices[a] = g
        if a == "2100-01-01" or a not in fits:
            continue
        prefs = shrink_products(fits[a], g[0])
        for c in [c for c in cases if a <= c["date"] < b]:
            t = choose_product(c, prefs, fits[a], g[1], g[2])
            c["arms"]["L"] = c["arms"][t] if t else c["arms"]["A"]
            c["_choice_L"] = t or "hedge-2"
            scored.append(c)
    fin = fits.get("2100-01-01") or {}
    g = choices["2100-01-01"]
    # the in-sample best (κ, β) on every era — the historical best fit, for comparison with the validated choice
    best_fit = max(grid, key=lambda x: sum(inner[e][x] for e in inner)) if inner else None
    say(f"products: {len(scored)} cases scored walk-forward")
    return {"scored": scored, "choices": {k: list(v) for k, v in choices.items()}, "best_fit": list(best_fit) if best_fit else None,
            "final_fit": fin, "final": shrink_products(fin, g[0]) if fin else {}}


# ------------------------------------------------------------------ tail price and the λ profile
def tail_profile(cases: List[dict]) -> Dict[str, dict]:
    """For global, each risk class and each objective: the best multiple at every λ and what sizing up to 1.5× buys in
    tail protection (95% ES reduction) against what it costs in λ = 1 utility."""
    out = {}
    for nd, cs in node_groups(cases).items():
        if nd.split(":")[0] not in ("global", "risk", "objective") or _dates(cs) < HN.MIN_TRAIN_DATES:
            continue
        best = {str(l): _best_mult(cs, l)[0] for l in LAMBDAS}
        for c in cs:
            HN._scaled(c, 1.5, "T")
        by: Dict[str, List[dict]] = {}
        for c in cs:
            by.setdefault(c["objective"], []).append(c)
        d_es = d_u = 0.0
        for o, cc in by.items():
            oa, ot = V.outcome(cc, "A", o), V.outcome(cc, "T", o)
            if oa.get("es_h") is not None and ot.get("es_h") is not None:
                d_es += ot["es_h"] - oa["es_h"]                      # ES is a loss (negative); larger = less tail loss
            ua, ut = V.utility(oa, 1.0), V.utility(ot, 1.0)
            if ua is not None and ut is not None:
                d_u += ut - ua
        out[nd] = {"dates": _dates(cs), "best_multiple_by_lambda": best, "es_reduction_1_5x": d_es, "utility_change_1_5x": d_u,
                   "price_per_es": (-d_u / d_es) if d_es > 0 else None}
    return out


# ------------------------------------------------------------------ the whole study
def _cell_table(scored: List[dict], objectives: List[str]) -> Dict[str, dict]:
    out = {}
    for o in objectives:
        sub = [c for c in scored if c["objective"] == o]
        if sub:
            out[o] = HN.cell(sub, o, "L")
    return out


def run(db_path: str, progress=None) -> dict:
    say = progress or (lambda m: None)
    t0 = time.time()
    res = {"started": time.strftime("%Y-%m-%d %H:%M:%S"), "sizing": {}, "products": {}, "tail": {}, "today": {}}
    for lab, h, _ in V.HORIZONS:
        try:
            cases = [c for d in V.load_dates(db_path, lab) for c in d["cases"]]
        except FileNotFoundError:
            continue
        for c in cases:
            c["end"] = _end(c["date"], h)
            c["_path"] = hpath(c)
        sw = sizing_walk_forward(cases, lambda m: say(f"{lab} {m}"))
        cells = _cell_table(sw["scored"], V.OBJECTIVES)
        nodes = {nd: {"dates": f["n"], "best_fit": f["m_hat"], "shrunk": sw["final"].get(nd)} for nd, f in sw["final_fit"].items()}
        res["sizing"][lab] = {"cells": cells, "K": sw["K"], "nodes": nodes}
        res["tail"][lab] = tail_profile([c for c in cases])
        # today's parameters: the latest date's books
        last = max(c["date"] for c in cases)
        res["today"].setdefault(lab, {})
        for c in cases:
            if c["date"] == last:
                res["today"][lab][f"{c['book']}|{c['objective']}"] = {"path": c["_path"], "multiple": mult_for(c, sw["final"]),
                                                                    "source": _source(c, sw["final_fit"])}
        say(f"{lab}: sizing and tail done ({time.time() - t0:.0f}s)")
    for lab, h, _ in HN.PRODUCT_H:
        try:
            cases = [c for d in HN.load(db_path, f"product_{lab}_dates.pkl") for c in d["cases"]]
        except FileNotFoundError:
            continue
        for c in cases:
            c["end"] = _end(c["date"], h)
            c["_path"] = hpath(c)
        pw = product_walk_forward(cases, lambda m: say(f"{lab} {m}"))
        cells = _cell_table(pw["scored"], HN.PRODUCT_OBJECTIVES)
        choice_counts: Dict[str, Dict[str, int]] = {}
        for c in pw["scored"]:
            d = choice_counts.setdefault(c["objective"], {})
            d[c["_choice_L"]] = d.get(c["_choice_L"], 0) + 1
        res["products"][lab] = {"cells": cells, "choices": pw["choices"], "best_fit": pw["best_fit"], "chosen": choice_counts,
                                "preferences": {nd: {t: v for t, v in p.items()} for nd, p in pw["final"].items()},
                                "raw": {nd: {t: {"n": x["n"], "adv": x["adv"], "sd": x["sd"]} for t, x in p.items()} for nd, p in pw["final_fit"].items()}}
        say(f"{lab}: products done ({time.time() - t0:.0f}s)")
    _finalise(res)
    res["seconds"] = round(time.time() - t0, 1)
    return res


def _end(date: str, h: int) -> str:
    import datetime as _dt
    return (_dt.date.fromisoformat(date) + _dt.timedelta(days=int(h * 7 / 5) + 3)).isoformat()


def _source(c: dict, fit: Dict[str, dict]) -> str:
    """The deepest node with its own evidence (≥ 5 dates) on the case's path."""
    src = "global"
    for nd in c["_path"]:
        f = fit.get(nd)
        if f and f.get("m_hat") is not None:
            src = nd.split(":")[0]
    return src


def _finalise(res: dict):
    keys, ps = [], []
    for part in ("sizing", "products"):
        for lab, x in res[part].items():
            for o, c in x["cells"].items():
                g = c.get("gates") or {}
                if g.get("enough") and g.get("p") is not None:
                    keys.append((part, lab, o)); ps.append(g["p"])
    rej = V.benjamini_hochberg(ps, HN.FDR_Q) if ps else []
    for (part, lab, o), r in zip(keys, rej):
        res[part][lab]["cells"][o]["gates"]["fdr"] = r
    for part in ("sizing", "products"):
        for lab, x in res[part].items():
            for o, c in x["cells"].items():
                g = c.get("gates") or {}
                g["H1"] = bool(g.get("enough") and (g.get("d") or 0) > 0 and g.get("fdr"))
                ok = g["H1"] and g.get("H2") and g.get("H3") and g.get("H4") and g.get("H5")
                c["status"] = "INSUFFICIENT DATA" if not g.get("enough") else ("LIVE SHADOW ELIGIBLE" if ok else "NO HEDGE IMPROVEMENT")
