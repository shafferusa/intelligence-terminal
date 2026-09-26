"""ML Lab — Shaffer Hedge vNext: turn risk forecasts, sizing, product choice and validated Alpha into economically
better realised protection (research only; hedge-2, its sizing and the resizing live shadow are unchanged).

Every experiment keeps the hedge architecture (positions → risk vector → objective → eligible products → sizing →
optimiser) and is judged on realised utility, exactly as the breadth study (hedge/volhedge.py): per case the book's
and the hedge's realised P&L over the horizon, U(λ) = [Risk(unhedged) − Risk(hedged)] − λ·ProfitSacrificed − Cost
(ES95 for crash / ES / VaR / drawdown objectives, the standard deviation otherwise), ΔU = U(challenger) − U(hedge-2) on
identical cases, moving-block bootstrap over dates (every book on a date one cluster), λ ∈ {0.5, 1, 2, 5, 10}.

Experiments (fixed 2026-09-26 before any result):
    hedge-3-risk-exp          risk estimation: Σ from an exponentially weighted covariance (RiskMetrics, λ = 0.94 per
                              day, same 252-day window) for EVERY factor instead of the equal-weighted sample; the
                              complete chain replayed point in time (1W every 10 sessions, 1M every 21, 3M every 63)
    hedge-3-sizing-obj-exp    sizing: one capped multiple m ∈ [0.5, 1.5] (step 0.05) per objective × horizon applied to
                              hedge-2's package, chosen to maximise U(λ = 1) on cases that matured before each era
    hedge-3-sizing-regime-exp the same per objective × volatility regime (high / low, PIT)
    hedge-3-product-exp       product choice: for each objective × volatility regime, the product type (index future,
                              ETF, inverse ETF, option, Treasury future, FX forward, …) with the best U(λ = 1) on cases
                              matured before each era (≥ 30 training dates, else hedge-2's choice); from a forced-product
                              replay in which the engine sizes the best product of each type (1W every 20 sessions, 1M
                              every 42)
    hedge-3-alpha-exp         Alpha → hedge: hedge-2's package × clip(1 − κ·raw/100, 0.5, 1.5) with the production
                              1W Alpha (the only validated Alpha horizon in the hedge replay) of the book's equities,
                              κ ∈ {0, 0.25, 0.5, 0.75, 1} chosen on prior eras; 1W equity books only. Directional is not
                              used: it has no validated incremental edge beyond the base prior.
    The existing resizing challenger (hedge-2-sizing-exp) is reported as is, from 2018 (its out-of-sample period).
Sizing and Alpha challengers rescale hedge-2's realised package (the hedge P&L and cost are linear in the quantity;
whole-lot rounding is ignored — stated). The sizing / product / Alpha challengers use the breadth study's saved hedge-2
cases (1W, 1M, 3M) and the forced-product replay; the risk challenger has its own replay.

Gates (per experiment × objective × horizon; the hedge success criteria):
    H1  ΔU(λ = 1) > 0 with the bootstrap one-sided p surviving Benjamini-Hochberg (q = 0.10) across every hedge test
    H2  ΔU > 0 in ≥ 3 of the 4 complete eras
    H3  tail not materially worse: hedged ES95 not above hedge-2's by more than 2% of the unhedged ES95
    H4  cost does not erase the gain: mean cost not above hedge-2's by more than 10% and basis error not by more than 5%
    H5  not one crisis: ΔU(λ = 1) > 0 with 2009, Feb–Jun 2020 and 2022 all excluded
    ≥ 60 applicable dates. Passing all: LIVE SHADOW ELIGIBLE (G5 live evidence and your approval before production).
Options remain MODEL-PRICED — FLAT VOLATILITY ASSUMPTION: every result is also shown with bought puts +5 vol points
and without option cases; an option-dependent result that disappears there is not promoted.
"""
from __future__ import annotations

import bisect
import math
import os
import pickle
import random
import time
from typing import Dict, List, Optional, Tuple

from . import volhedge as V

RESEARCH_KEY = "lab:hedgenext"
EWMA_LAMBDA = 0.94
MULTS = [round(0.5 + 0.05 * k, 2) for k in range(21)]
KAPPAS = [0.0, 0.25, 0.5, 0.75, 1.0]
FDR_Q = 0.10
MIN_TRAIN_DATES = 30
PRODUCT_OBJECTIVES = ["beta", "crash", "min_variance", "sector", "duration", "credit", "fx"]
PRODUCT_H = [("1W", 5, 20), ("1M", 21, 42)]
RISK_H = V.HORIZONS
ALPHA_BOOKS = {"AAPL", "JPM", "XOM", "XLK", "XLF", "QQQ", "IWM", "EQ10"}
TESTS = ("risk", "sizing_obj", "sizing_regime", "product", "alpha")


def vid(test: str) -> str:
    return {"risk": "hedge-3-risk-exp", "sizing_obj": "hedge-3-sizing-obj-exp", "sizing_regime": "hedge-3-sizing-regime-exp",
            "product": "hedge-3-product-exp", "alpha": "hedge-3-alpha-exp"}[test]


# ------------------------------------------------------------------ EWMA covariance (the risk challenger)
def ewma_cov(rk, factors: List[str], lam: float = EWMA_LAMBDA) -> Dict[Tuple[str, str], float]:
    """Exponentially weighted covariance of the factors' daily moves over the RiskModel's window (data up to its
    session only), pairwise over the days both exist."""
    wins = {f: rk.fwin(f) for f in factors}
    out = {}
    for ai, a in enumerate(factors):
        for b in factors[ai:]:
            xa, xb = wins[a], wins[b]
            n = min(len(xa), len(xb))
            xa, xb = xa[len(xa) - n:], xb[len(xb) - n:]
            num = den = 0.0
            ma = mb = 0.0
            pairs = [(x, y) for x, y in zip(xa, xb) if x is not None and y is not None]
            if len(pairs) < 40:
                out[(a, b)] = out[(b, a)] = 0.0
                continue
            m = len(pairs)
            ws = [lam ** (m - 1 - k) for k in range(m)]
            sw = sum(ws)
            ma = sum(w * x for w, (x, _) in zip(ws, pairs)) / sw
            mb = sum(w * y for w, (_, y) in zip(ws, pairs)) / sw
            num = sum(w * (x - ma) * (y - mb) for w, (x, y) in zip(ws, pairs))
            den = sw
            out[(a, b)] = out[(b, a)] = num / den
    return out


def risk_replay_date(research, i: int, lab: str, h: int, books=None, objectives=None) -> dict:
    """hedge-2 (A) and the EWMA-covariance engine (B) on session i, realised over (i, i+h]."""
    from . import engine as E
    from .market import Market
    from .risk import RiskModel, factor_series
    store = research.store
    m = Market(research, i)
    rk = RiskModel(m)
    out = {"i": i, "date": m.asof, "cases": []}
    markets = [m] + [Market(research, i + k) for k in range(1, h + 1)]
    if markets[-1].i != i + h:
        return out
    F = factor_series(research)
    regime = m.regime()
    vix, _ = m.macro("VIXCLS")
    cache: dict = {}
    adj = lambda C: ewma_cov(rk, sorted({a for a, _ in C}))  # noqa: E731
    for book in books or V.BOOKS:
        pos = V.book_positions(book, m, store)
        if pos is None:
            continue
        u = None
        for obj in objectives or V.OBJECTIVES:
            try:
                a = E.analyze(research, pos, obj, V._params(obj, lab), nav=V.NAV, replay={"market": m, "rk": rk})
            except Exception as e:  # noqa: BLE001
                out.setdefault("errors", []).append(f"{book['key']} {obj}: {type(e).__name__}: {e}")
                continue
            if not V._applicable(a):
                continue
            b = E.analyze(research, pos, obj, V._params(obj, lab), nav=V.NAV, replay={"market": m, "rk": rk, "cov_adjust": adj})
            if u is None:
                u = V._book_pnl(pos, markets, store, cache)
                if u is None:
                    break
            arms, ok = {}, True
            for key, res in (("A", a), ("B", b)):
                hp, legs = [0.0] * h, []
                for L in res["package"]:
                    p = V.leg_pnl(research, L, markets, store, cache)
                    if p is None:
                        ok = False
                        break
                    hp = [x + y for x, y in zip(hp, p)]
                    legs.append({"id": L["id"], "type": L["type"], "product_type": L["product_type"], "q": L["quantity"],
                                 "notional": L["notional"], "beta_usd": L["quantity"] * L["H"].get("MKT", 0.0), "option": L["type"] == "OPTION"})
                if not ok:
                    break
                arms[key] = {"hp": hp, "cost": sum((L.get("cost") or 0.0) for L in res["package"]), "legs": legs,
                             "skew": sum(V._skew_cost(L, m, store) for L in res["package"]), "mult": 1.0}
            if not ok:
                continue
            arms["R"] = arms["A"]
            S = a["targeted"]
            dR = {f: a["Rstar"].get(f, a["R"].get(f, 0.0)) - a["R"].get(f, 0.0) for f in S}
            ideal = [sum(dR[f] * ((F.get(f) or [None] * (k + 1))[k] or 0.0) for f in S) for k in range(i + 1, i + h + 1)]
            out["cases"].append({"book": book["key"], "btype": book["type"], "objective": obj, "u": u, "ideal": ideal, "arms": arms,
                                 "regime": regime, "vix": vix, "S": S, "primary": a.get("primary_factor"), "date": m.asof})
    return out


# ------------------------------------------------------------------ forced-product replay (product choice)
def product_type(pid: str, store) -> Optional[str]:
    from . import products as P
    try:
        inst = P.parse(pid, store)
    except (ValueError, KeyError):
        return None
    return inst.product_type or inst.type


def product_replay_date(research, i: int, lab: str, h: int, books=None, objectives=None) -> dict:
    """For every applicable book × objective: hedge-2's package, and the engine's best product of each product type
    (params use = that type's candidates, one leg), realised over (i, i+h]."""
    from . import engine as E
    from .market import Market
    from .risk import RiskModel, factor_series
    store = research.store
    m = Market(research, i)
    rk = RiskModel(m)
    out = {"i": i, "date": m.asof, "cases": []}
    markets = [m] + [Market(research, i + k) for k in range(1, h + 1)]
    if markets[-1].i != i + h:
        return out
    F = factor_series(research)
    regime = m.regime()
    vix, _ = m.macro("VIXCLS")
    cache: dict = {}
    for book in books or V.BOOKS:
        pos = V.book_positions(book, m, store)
        if pos is None:
            continue
        u = None
        for obj in objectives or PRODUCT_OBJECTIVES:
            try:
                a = E.analyze(research, pos, obj, V._params(obj, lab), nav=V.NAV, replay={"market": m, "rk": rk})
            except Exception as e:  # noqa: BLE001
                out.setdefault("errors", []).append(f"{book['key']} {obj}: {type(e).__name__}: {e}")
                continue
            if not V._applicable(a) or not a.get("pool"):
                continue
            types: Dict[str, List[str]] = {}
            for pid in a["pool"]:
                t = product_type(pid, store)
                if t:
                    types.setdefault(t, []).append(pid)
            if u is None:
                u = V._book_pnl(pos, markets, store, cache)
                if u is None:
                    break
            arms = {}
            for key, params in [("A", None)] + [(f"type:{t}", {"use": ids, "max_legs": 1}) for t, ids in types.items()]:
                res = a if params is None else E.analyze(research, pos, obj, {**V._params(obj, lab), **params}, nav=V.NAV,
                                                        replay={"market": m, "rk": rk})
                hp, legs, ok = [0.0] * h, [], True
                for L in res["package"]:
                    p = V.leg_pnl(research, L, markets, store, cache)
                    if p is None:
                        ok = False
                        break
                    hp = [x + y for x, y in zip(hp, p)]
                    legs.append({"id": L["id"], "type": L["type"], "product_type": L["product_type"], "q": L["quantity"],
                                 "notional": L["notional"], "beta_usd": L["quantity"] * L["H"].get("MKT", 0.0), "option": L["type"] == "OPTION"})
                if not ok or (params is not None and not legs):
                    continue
                arms[key] = {"hp": hp, "cost": sum((L.get("cost") or 0.0) for L in res["package"]), "legs": legs,
                             "skew": sum(V._skew_cost(L, m, store) for L in res["package"]), "mult": 1.0}
            if "A" not in arms:
                continue
            S = a["targeted"]
            dR = {f: a["Rstar"].get(f, a["R"].get(f, 0.0)) - a["R"].get(f, 0.0) for f in S}
            ideal = [sum(dR[f] * ((F.get(f) or [None] * (k + 1))[k] or 0.0) for f in S) for k in range(i + 1, i + h + 1)]
            out["cases"].append({"book": book["key"], "btype": book["type"], "objective": obj, "u": u, "ideal": ideal, "arms": arms,
                                 "regime": regime, "vix": vix, "S": S, "primary": a.get("primary_factor"), "date": m.asof})
    return out


# ------------------------------------------------------------------ chunked, resumable replays (same pattern as volhedge)
def _worker(db_path: str, kind: str, lab: str, h: int, idx: List[int], stream: str):
    from ..data.store import Store
    from ..engine.research import Research
    done = {d["i"]: d for d in V.read_stream(stream)}
    st = Store(db_path)
    try:
        r = Research(st)
        cr = V._credit_state(r)
        out = []
        fn = risk_replay_date if kind == "risk" else product_replay_date
        for i in idx:
            if i in done:
                out.append(done[i])
                continue
            d = fn(r, i, lab, h)
            for c in d["cases"]:
                c["credit"] = cr[i]
            out.append(d)
            with open(stream, "ab") as fh:
                pickle.dump(d, fh)
        return out
    finally:
        st.close()


def replay(db_path: str, kind: str, workers: int = 3, progress=None, horizons=None) -> Dict[str, List[dict]]:
    """kind = "risk" (A vs EWMA Σ) or "product" (forced products). Chunks are saved and reused on a rerun."""
    from concurrent.futures import ProcessPoolExecutor, as_completed
    from ..data.store import Store
    from ..engine.research import Research
    say = progress or (lambda m: None)
    st = Store(db_path)
    try:
        cal = Research(st).panel().calendar()
    finally:
        st.close()
    cdir = V._cache_dir(db_path)
    labs = RISK_H if kind == "risk" else PRODUCT_H
    res = {}
    for lab, h, step in labs:
        if horizons and lab not in horizons:
            continue
        idx = V.study_dates(cal, h, step)
        chunks = [idx[k::V.CHUNKS] for k in range(V.CHUNKS)]
        dates, todo = [], []
        for k, ch in enumerate(chunks):
            f = os.path.join(cdir, f"{kind}_{lab}_{k}.pkl")
            if ch and os.path.exists(f):
                with open(f, "rb") as fh:
                    dates += pickle.load(fh)
            elif ch:
                todo.append((k, ch, f))
        t1 = time.time()
        with ProcessPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(_worker, db_path, kind, lab, h, ch, f + ".stream"): (k, f) for k, ch, f in todo}
            for n, fu in enumerate(as_completed(futs), 1):
                k, f = futs[fu]
                part = fu.result()
                with open(f + ".tmp", "wb") as fh:
                    pickle.dump(part, fh)
                os.replace(f + ".tmp", f)
                dates += part
                say(f"{kind} {lab}: {n}/{len(todo)} chunks ({time.time() - t1:.0f}s)")
        dates.sort(key=lambda d: d["i"])
        with open(os.path.join(cdir, f"{kind}_{lab}_dates.pkl"), "wb") as fh:
            pickle.dump(dates, fh)
        res[lab] = dates
    return res


def load(db_path: str, name: str) -> List[dict]:
    with open(os.path.join(V._cache_dir(db_path), name), "rb") as fh:
        return pickle.load(fh)


# ------------------------------------------------------------------ rescaled arms (sizing, Alpha) and learned rules
def _scaled(c: dict, m: float, key: str = "S") -> dict:
    a = c["arms"]["A"]
    c["arms"][key] = {"hp": [m * x for x in a["hp"]], "cost": m * a["cost"], "skew": m * a["skew"], "mult": m,
                      "legs": [{**L, "q": L["q"] * m, "notional": L["notional"] * m, "beta_usd": L["beta_usd"] * m} for L in a["legs"]]}
    return c


def _u(cases: List[dict], arm: str, objective: str) -> Optional[float]:
    return V.utility(V.outcome(cases, arm, objective), 1.0)


def _best_mult(train: List[dict], objective: str) -> float:
    best, bu = 1.0, None
    for m in MULTS:
        for c in train:
            _scaled(c, m, "T")
        u = _u(train, "T", objective)
        if u is not None and (bu is None or u > bu + 1e-9):
            best, bu = m, u
    return best


def _vol_state(c: dict) -> str:
    return "high_vol" if (c.get("regime") or {}).get("volatility") == "high_vol" else "low_vol"


def walk_forward(cases: List[dict], objective: str, rule, key: str = "S") -> List[dict]:
    """For each complete era (and 2025–): learn on cases that matured before it (date + horizon < era start, i.e. the
    case's window ended), apply to the era's cases as arm `key`. Returns the cases that were scored."""
    out = []
    for a, b in V.ERA_ALL:
        train = [c for c in cases if c["end"] < a]
        test = [c for c in cases if a <= c["date"] < b]
        if not test:
            continue
        rule(train, test, objective, key)
        out += test
    return out


def rule_obj(train, test, objective, key):
    m = _best_mult(train, objective) if len({c["date"] for c in train}) >= MIN_TRAIN_DATES else 1.0
    for c in test:
        _scaled(c, m, key)


def rule_regime(train, test, objective, key):
    ms = {}
    for st in ("high_vol", "low_vol"):
        tr = [c for c in train if _vol_state(c) == st]
        ms[st] = _best_mult(tr, objective) if len({c["date"] for c in tr}) >= MIN_TRAIN_DATES else 1.0
    for c in test:
        _scaled(c, ms[_vol_state(c)], key)


def rule_product(train, test, objective, key):
    """Per volatility state, the product type whose forced hedge had the best U(λ = 1) on the training cases where it
    was available (≥ MIN_TRAIN_DATES dates), else hedge-2's package."""
    choice = {}
    for st in ("high_vol", "low_vol"):
        tr = [c for c in train if _vol_state(c) == st]
        best, bu = None, None
        for t in sorted({k for c in tr for k in c["arms"] if k.startswith("type:")}):
            sub = [c for c in tr if t in c["arms"]]
            if len({c["date"] for c in sub}) < MIN_TRAIN_DATES:
                continue
            ua, ut = _u(sub, "A", objective), _u(sub, t, objective)
            if ua is not None and ut is not None and ut - ua > (bu if bu is not None else 0.0):
                best, bu = t, ut - ua
        choice[st] = best
    for c in test:
        t = choice[_vol_state(c)]
        c["arms"][key] = c["arms"][t] if t and t in c["arms"] else c["arms"]["A"]
        c.setdefault("_choice", {})[key] = t or "hedge-2"


def rule_alpha(train, test, objective, key):
    best, bu = 0.0, None
    if len({c["date"] for c in train}) >= MIN_TRAIN_DATES:
        for kappa in KAPPAS:
            for c in train:
                _scaled(c, max(0.5, min(1.5, 1 - kappa * (c.get("alpha") or 0.0))), "T")
            u = _u(train, "T", objective)
            if u is not None and (bu is None or u > bu + 1e-9):
                best, bu = kappa, u
    for c in test:
        _scaled(c, max(0.5, min(1.5, 1 - best * (c.get("alpha") or 0.0))), key)
        c.setdefault("_kappa", {})[key] = best


# ------------------------------------------------------------------ one test cell
def cell(cases: List[dict], objective: str, arm: str = "S", reps: int = V.BOOT_REPS) -> dict:
    """The paired test of `arm` against hedge-2 (A) with the gates' inputs."""
    if not cases:
        return {"cases": 0}
    p = V.paired(cases, objective, "A", arm, reps=reps)
    ps = V.paired(cases, objective, "A", arm, skew=True, reps=0)
    nopt = [c for c in cases if not any(L["option"] for k in ("A", arm) for L in c["arms"][k]["legs"])]
    po = V.paired(nopt, objective, "A", arm, reps=0)
    eras = {}
    for a, b in V.ERA_ALL:
        sub = [c for c in cases if a <= c["date"] < b]
        d = (V.paired(sub, objective, "A", arm, reps=0).get("d") or {}).get("1.0") if len(sub) >= 20 else None
        eras[V._era(a)] = {"dates": len({c["date"] for c in sub}), "d": d, "complete": (a, b) in V.ERAS4}
    nc = [c for c in cases if not any(x <= c["date"] < y for x, y in V.CRISES.values())]
    pc = V.paired(nc, objective, "A", arm, reps=0)
    kinds = [V._changed(c, "A", arm) for c in cases]
    ci = (p.get("ci") or {}).get("1.0")
    d1 = (p.get("d") or {}).get("1.0")
    a, b = p.get("a") or {}, p.get("b") or {}
    out = {"cases": len(cases), "dates": len({c["date"] for c in cases}), "d": p.get("d"), "ci": p.get("ci"), "p": p.get("p"),
           "t": (d1 / ((ci[1] - ci[0]) / 3.92)) if ci and d1 is not None and ci[1] > ci[0] else None,
           "a": a, "b": b, "skew": ps.get("d"), "no_options": po.get("d"), "no_crisis": (pc.get("d") or {}).get("1.0"),
           "eras": eras, "changed": 1 - kinds.count("identical") / len(kinds), "product_changed": kinds.count("product") / len(kinds)}
    full = [e for e in eras.values() if e["complete"] and e["d"] is not None]
    worse_es = (b.get("es_h") or 0) - (a.get("es_h") or 0)
    worse_cost = ((b.get("cost") or 0) / a["cost"] - 1) if a.get("cost") else (0.0 if not b.get("cost") else 1.0)
    worse_basis = ((b.get("basis_error") or 0) / a["basis_error"] - 1) if a.get("basis_error") else 0.0
    out["gates"] = {"enough": out["dates"] >= V.MIN_DATES, "H2": sum(1 for e in full if e["d"] > 0) >= 3, "eras_won": sum(1 for e in full if e["d"] > 0),
                    "eras_complete": len(full), "H3": bool(a.get("es_u") and worse_es <= V.G4_ES * a["es_u"]),
                    "H4": bool(worse_cost <= V.G4_COST and worse_basis <= V.G4_BASIS), "H5": bool((out["no_crisis"] or 0) > 0),
                    "p": (p.get("p") or {}).get("1.0"), "d": d1}
    return out


def finalise(res: dict) -> dict:
    keys, ps = [], []
    for test, hs in res["tests"].items():
        for lab, objs in hs.items():
            for obj, c in objs.items():
                g = c.get("gates") or {}
                if g.get("enough") and g.get("p") is not None:
                    keys.append((test, lab, obj)); ps.append(g["p"])
    rej = V.benjamini_hochberg(ps, FDR_Q) if ps else []
    for (test, lab, obj), r in zip(keys, rej):
        res["tests"][test][lab][obj]["gates"]["fdr"] = r
    for test, hs in res["tests"].items():
        for lab, objs in hs.items():
            for obj, c in objs.items():
                g = c.get("gates") or {}
                g["H1"] = bool(g.get("enough") and (g.get("d") or 0) > 0 and g.get("fdr"))
                ok = g["H1"] and g.get("H2") and g.get("H3") and g.get("H4") and g.get("H5")
                if not g.get("enough"):
                    c["status"] = "INSUFFICIENT DATA"
                else:
                    c["status"] = "LIVE SHADOW ELIGIBLE" if ok else "NO HEDGE IMPROVEMENT"
    return res


def _alpha_map(store, research) -> Dict[str, List[Tuple[str, float]]]:
    """{asset: [(date, production 1W raw)]} from the research records (weekly, point in time)."""
    from ..engine import weights as W
    W._init_famidx()
    out: Dict[str, List[Tuple[str, float]]] = {}
    for r in W.load(store, research, "1W"):
        out.setdefault(r.asset, []).append((r.date, r.raw))
    for v in out.values():
        v.sort()
    return out


def _book_alpha(book: dict, date: str, amap) -> Optional[float]:
    tot, wsum = 0.0, 0.0
    for a, w in book["w"].items():
        xs = amap.get(a) or []
        k = bisect.bisect_right(xs, (date, float("inf"))) - 1
        if k < 0 or _days(date, xs[k][0]) > 9:                  # the latest weekly record, at most 9 days old
            return None
        tot += w * xs[k][1] / 100.0
        wsum += abs(w)
    return tot / wsum if wsum else None


def _days(a: str, b: str) -> int:
    import datetime as _dt
    return (_dt.date.fromisoformat(a) - _dt.date.fromisoformat(b)).days


def run_all(db_path: str, workers: int = 3, progress=None, skip_replays: bool = False) -> dict:
    """The two replays (risk, product), then every experiment on identical cases; stored under RESEARCH_KEY."""
    from ..data.store import Store
    from ..engine.lab import registry
    from ..engine.research import Research
    import datetime as _dt
    say = progress or (lambda m: None)
    t0 = time.time()
    if not skip_replays:
        replay(db_path, "risk", workers, say)
        replay(db_path, "product", workers, say)
    st = Store(db_path)
    try:
        amap = _alpha_map(st, Research(st))
        sizing = next((v for v in registry(st)["versions"] if v["id"] == V.RESIZE), None)
    finally:
        st.close()
    res = {"started": time.strftime("%Y-%m-%d %H:%M:%S"), "tests": {t: {} for t in TESTS}, "existing_resizing": {}, "product_layer": {}}
    hmap = {lab: h for lab, h, _ in V.HORIZONS}

    def ends(cases, h):
        for c in cases:
            c["end"] = (_dt.date.fromisoformat(c["date"]) + _dt.timedelta(days=int(h * 7 / 5) + 3)).isoformat()
        return cases
    # 1. risk (own replay)
    for lab, h, _ in RISK_H:
        try:
            cases = [c for d in load(db_path, f"risk_{lab}_dates.pkl") for c in d["cases"]]
        except FileNotFoundError:
            continue
        res["tests"]["risk"][lab] = {}
        for obj in V.OBJECTIVES:
            sub = [c for c in cases if c["objective"] == obj]
            res["tests"]["risk"][lab][obj] = cell(sub, obj, "B") if sub else {"cases": 0}
            if sub:
                res["tests"]["risk"][lab][obj]["decisions"] = {k: sum(1 for c in sub if V._changed(c) == k) / len(sub) for k in ("identical", "sizing", "product")}
        say(f"risk {lab} analysed")
    # 2–3. sizing and 5. Alpha on the breadth study's saved hedge-2 cases
    for lab, h, _ in V.HORIZONS:
        try:
            cases = ends([c for d in V.load_dates(db_path, lab) for c in d["cases"]], h)
        except FileNotFoundError:
            continue
        for test, rule in (("sizing_obj", rule_obj), ("sizing_regime", rule_regime)):
            res["tests"][test][lab] = {}
            for obj in V.OBJECTIVES:
                sub = [c for c in cases if c["objective"] == obj]
                if not sub:
                    continue
                scored = walk_forward(sub, obj, rule)
                cc = cell(scored, obj, "S")
                cc["multiples"] = sorted({(c["arms"]["S"]["mult"], _vol_state(c) if test == "sizing_regime" else "all") for c in scored})[:12]
                res["tests"][test][lab][obj] = cc
            say(f"{test} {lab} analysed")
        # the existing confirmed resizing challenger, 2018 on (its out-of-sample period)
        res["existing_resizing"][lab] = {}
        for obj in V.OBJECTIVES:
            late = [c for c in cases if c["objective"] == obj and c["date"] >= V.RESIZE_FROM]
            if late:
                res["existing_resizing"][lab][obj] = cell(late, obj, "C", reps=V.BOOT_REPS // 2)
        if lab == "1W":
            res["tests"]["alpha"][lab] = {}
            for obj in V.OBJECTIVES:
                sub = []
                for c in cases:
                    if c["objective"] != obj or c["book"] not in ALPHA_BOOKS:
                        continue
                    book = next(b for b in V.BOOKS if b["key"] == c["book"])
                    c["alpha"] = _book_alpha(book, c["date"], amap)
                    if c["alpha"] is not None:
                        sub.append(c)
                if not sub:
                    continue
                scored = walk_forward(sub, obj, rule_alpha)
                cc = cell(scored, obj, "S")
                cc["kappas"] = sorted({c["_kappa"]["S"] for c in scored})
                res["tests"]["alpha"][lab][obj] = cc
            say("alpha 1W analysed")
    # 4. product choice (forced-product replay)
    for lab, h, _ in PRODUCT_H:
        try:
            cases = ends([c for d in load(db_path, f"product_{lab}_dates.pkl") for c in d["cases"]], h)
        except FileNotFoundError:
            continue
        res["tests"]["product"][lab] = {}
        res["product_layer"][lab] = {}
        for obj in PRODUCT_OBJECTIVES:
            sub = [c for c in cases if c["objective"] == obj]
            if not sub:
                continue
            # the descriptive layer: every product type against hedge-2 where both exist, by volatility state
            layer = {}
            for t in sorted({k for c in sub for k in c["arms"] if k.startswith("type:")}):
                for st_ in ("all", "high_vol", "low_vol"):
                    ss = [c for c in sub if t in c["arms"] and (st_ == "all" or _vol_state(c) == st_)]
                    if len({c["date"] for c in ss}) < 20:
                        continue
                    oa, ot = V.outcome(ss, "A", obj), V.outcome(ss, t, obj)
                    layer.setdefault(t[5:], {})[st_] = {"dates": len({c["date"] for c in ss}), "U_prod": V.utility(oa, 1.0), "U_type": V.utility(ot, 1.0),
                                                       "risk_reduction": ot.get("loss_reduction"), "cost": ot.get("cost"), "basis": ot.get("basis_error"),
                                                       "profit_sacrificed": ot.get("profit_sacrificed"),
                                                       "production_uses_it": sum(1 for c in ss if V._lead_product(c["arms"]["A"]) == t[5:]) / len(ss)}
            res["product_layer"][lab][obj] = layer
            scored = walk_forward(sub, obj, rule_product)
            cc = cell(scored, obj, "S")
            cc["choices"] = {k: sum(1 for c in scored if c["_choice"]["S"] == k) for k in sorted({c["_choice"]["S"] for c in scored})}
            res["tests"]["product"][lab][obj] = cc
        say(f"product {lab} analysed")
    finalise(res)
    res["seconds"] = round(time.time() - t0, 1)
    st = Store(db_path)
    try:
        st.kv_set(RESEARCH_KEY, res)
    finally:
        st.close()
    return res
