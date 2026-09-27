"""ML Lab — the hedge residual / action policy (residual program, protocol §11; report SHAFFER_RESIDUAL_ML.md §11).

Research only: hedge-2, its gates H1–H5 and the λ-hedge live shadow are unchanged; nothing here enters live shadow.

Question: given the context of a hedge decision, is some OTHER action than hedge-2's package reliably better for a user
with profit sensitivity λ? Two action sets, each judged on its own cases:

  sizing   0.50×, 0.75×, 1.25×, 1.50× hedge-2's package (1.00× = hedge-2) on the hedge research cases (1W, 1M);
  product  the product types hedge-2's optimiser already found eligible for that case (arms "type:…") on the product-
           replay cases (1W, 1M).

Target for every case and action: its exact additive share of ΔU(λ) against hedge-2 within the training set of its
objective (volhedge.case_contributions — the shares sum to the set's ΔU). A ridge per action predicts the gain from the
context (risk class, book type, volatility state, VIX, market state, hedge-2's cost per NAV, the book's Alpha
percentile). The policy takes the best action only when its predicted gain − 1.96·se > 0, se = the disagreement of the
same ridge refitted on each complete training era separately (no departure from hedge-2 before two eras exist): the
model's own uncertainty decides whether it is trusted. Walk-forward by era; judged per objective cell (risk class ×
objective) at that λ with the unchanged hedge gates (H1 with BH FDR across the family, H2 eras, H3 tail, H4 cost/basis,
H5 without crises).
"""
from __future__ import annotations

import math
import time
from typing import Dict, List, Optional, Tuple

from . import hedgelearn as HL
from . import hedgenext as HN
from . import hedgetune as HT
from . import volhedge as V

RESEARCH_KEY = "lab:residual:hedge"
SIZES = [0.5, 0.75, 1.25, 1.5]
POLICY_LAMBDAS = [1.0, 5.0]
HORIZONS = [("1W", 5), ("1M", 21)]
RIDGE = 10.0
Z_TRUST = 1.96
MIN_ERA_DATES = 20
RISK_CLASSES = ["Equity", "Rates", "Credit", "Commodity", "FX", "Crypto", "Volatility"]
BOOK_TYPES = ["single stock", "sector ETF", "broad equity ETF", "equity portfolio", "mixed portfolio", "bond portfolio",
              "credit portfolio", "crypto exposure"]
FEATURES = ([f"risk:{r}" for r in RISK_CLASSES] + [f"book:{b}" for b in BOOK_TYPES] +
            ["high volatility", "VIX", "bull market", "hedge-2 cost / NAV", "book Alpha percentile"])
BOOK_OF = {b["key"]: b for b in V.BOOKS}


def context(c: dict, amap: Optional[dict]) -> List[float]:
    rc = HL.risk_class(c)
    reg = c.get("regime") or {}
    x = [1.0 if rc == r else 0.0 for r in RISK_CLASSES] + [1.0 if c.get("btype") == b else 0.0 for b in BOOK_TYPES]
    x.append(1.0 if reg.get("volatility") == "high_vol" else -1.0)
    x.append(float(c.get("vix") or 20.0) / 10.0)
    x.append(1.0 if reg.get("market") == "bull" else -1.0)
    x.append(1e4 * (c["arms"]["A"].get("cost") or 0.0) / V.NAV)
    a = HT.book_alpha(BOOK_OF[c["book"]], c["date"], amap) if (amap and c.get("book") in BOOK_OF) else None
    x.append((a - 0.5) if a is not None else 0.0)
    return x


def _ridge(X: List[List[float]], y: List[float], lam: float = RIDGE) -> Optional[List[float]]:
    """Ridge with an unpenalised intercept on standardised columns; returns [intercept, coefficients on raw x]."""
    n = len(y)
    if n < 20:
        return None
    P = len(X[0])
    m = [sum(r[i] for r in X) / n for i in range(P)]
    sd = [math.sqrt(sum((r[i] - m[i]) ** 2 for r in X) / n) for i in range(P)]
    act = [i for i in range(P) if sd[i] > 1e-12]
    Z = [[(r[i] - m[i]) / sd[i] for i in act] for r in X]
    my = sum(y) / n
    k = len(act)
    A = [[sum(z[a] * z[b] for z in Z) + (lam if a == b else 0.0) for b in range(k)] for a in range(k)]
    rhs = [sum(z[a] * (v - my) for z, v in zip(Z, y)) for a in range(k)]
    from ..engine.learned import _chol_solve
    sol = _chol_solve(A, rhs) if k else []
    coef = [0.0] * P
    for j, i in enumerate(act):
        coef[i] = sol[j] / sd[i]
    return [my - sum(coef[i] * m[i] for i in range(P))] + coef


def _pred(b: List[float], x: List[float]) -> float:
    return b[0] + sum(c * v for c, v in zip(b[1:], x))


def _targets(cases: List[dict], arm: str, lam: float) -> Dict[int, float]:
    """Per case (by id): n × its additive share of ΔU(λ) of `arm` vs hedge-2 within its objective's set."""
    out: Dict[int, float] = {}
    by: Dict[str, List[dict]] = {}
    for c in cases:
        if arm in c["arms"]:
            by.setdefault(c["objective"], []).append(c)
    for obj, cs in by.items():
        sh = V.case_contributions(cs, "A", arm, obj, lam)
        n = len(cs)
        for c, v in zip(cs, sh):
            out[id(c)] = n * v
    return out


def _fit(train: List[dict], arms: List[str], lam: float, amap) -> Dict[Tuple[str, str], dict]:
    """Per (objective, action): the ridge on all training cases, and one per complete training era (for se)."""
    fits: Dict[Tuple[str, str], dict] = {}
    for arm in arms:
        tg = _targets(train, arm, lam)
        by_era: Dict[Tuple[str, str], List[dict]] = {}
        for a, b in _blocks(train):
            sub = [c for c in train if a <= c["date"] < b]
            if HL._dates(sub) >= MIN_ERA_DATES:
                tge = _targets(sub, arm, lam)
                for c in sub:
                    if id(c) in tge:
                        by_era.setdefault((c["objective"], a), []).append((context(c, amap), tge[id(c)]))
        objs = {c["objective"] for c in train if id(c) in tg}
        for obj in objs:
            rows = [(context(c, amap), tg[id(c)]) for c in train if c["objective"] == obj and id(c) in tg]
            b = _ridge([r[0] for r in rows], [r[1] for r in rows])
            eb = [_ridge([r[0] for r in v], [r[1] for r in v]) for (o, _), v in by_era.items() if o == obj]
            fits[(obj, arm)] = {"b": b, "eras": [x for x in eb if x is not None], "n": len(rows)}
    return fits


def _blocks(train: List[dict]) -> List[Tuple[str, str]]:
    """The training blocks whose disagreement is se: the complete eras with data — or, while only one exists, its two
    halves (protocol amendment 1, made after the synthetic capability test and before any market result: with a
    single block no se exists, the first test era could never depart from hedge-2 and H2 would be unattainable)."""
    eras = [(a, b) for a, b in V.ERAS4 if HL._dates([c for c in train if a <= c["date"] < b]) >= MIN_ERA_DATES]
    if len(eras) >= 2:
        return eras
    out = []
    for a, b in eras:
        mid = f"{(int(a[:4]) + int(b[:4])) // 2}-01-01"
        out += [(a, mid), (mid, b)]
    return out


def _decide(c: dict, fits: Dict[Tuple[str, str], dict], arms: List[str], amap) -> Tuple[str, Optional[float], Optional[float]]:
    x = context(c, amap)
    best, best_g, best_se = "A", None, None
    for arm in arms:
        if arm not in c["arms"]:
            continue
        f = fits.get((c["objective"], arm))
        if not f or f["b"] is None or len(f["eras"]) < 2:
            continue
        g = _pred(f["b"], x)
        ps = [_pred(b, x) for b in f["eras"]]
        k = len(ps)
        mu = sum(ps) / k
        se = math.sqrt(sum((p - mu) ** 2 for p in ps) / (k * (k - 1)))
        if g - Z_TRUST * se > 0 and (best_g is None or g > best_g):
            best, best_g, best_se = arm, g, se
    return best, best_g, best_se


def _sizing_arms(cases: List[dict]):
    for c in cases:
        for m in SIZES:
            k = f"x{m:g}"
            if k not in c["arms"]:
                c["arms"][k] = HT._scaled_arm(c, m)


def walk_forward(cases: List[dict], arms: List[str], lam: float, amap, say=None) -> Tuple[List[dict], dict]:
    """Each era's cases decided by the policy fitted on cases matured before it; the chosen package is arm 'P'."""
    scored = []
    choice_counts: Dict[str, int] = {}
    for a, b in V.ERA_ALL:
        train = [c for c in cases if c["end"] < a]
        test = [c for c in cases if a <= c["date"] < b]
        if HL._dates(train) < HN.MIN_TRAIN_DATES or not test:
            continue
        fits = _fit(train, arms, lam, amap)
        for c in test:
            arm, g, se = _decide(c, fits, arms, amap)
            c.setdefault("_pol", {})[lam] = (arm, g, se)
            choice_counts[arm] = choice_counts.get(arm, 0) + 1
            scored.append(c)
    return scored, choice_counts


def _finalise(cells: Dict[str, dict]):
    keys = [k for k, c in cells.items() if (c.get("gates") or {}).get("enough") and (c.get("gates") or {}).get("p") is not None]
    for k, r in zip(keys, V.benjamini_hochberg([cells[k]["gates"]["p"] for k in keys], HN.FDR_Q) if keys else []):
        cells[k]["gates"]["fdr"] = r
    for k, c in cells.items():
        g = c.get("gates") or {}
        g["H1"] = bool(g.get("enough") and (g.get("d") or 0) > 0 and g.get("fdr"))
        ok = g["H1"] and g.get("H2") and g.get("H3") and g.get("H4") and g.get("H5")
        c["passed"] = bool(ok)
        c["status"] = "INSUFFICIENT DATA" if not g.get("enough") else ("PASSED H1–H5" if ok else "NO HEDGE IMPROVEMENT")


def counterfactual(cases: List[dict], lams=POLICY_LAMBDAS) -> List[dict]:
    """Descriptive: for every objective × volatility state, each sizing action's realised risk reduction, profit given up,
    cost, basis error and U(λ) — the table the policy learns from (in-sample, all eras)."""
    out = []
    groups: Dict[Tuple[str, str], List[dict]] = {}
    for c in cases:
        groups.setdefault((c["objective"], HN._vol_state(c)), []).append(c)
    for (obj, vs), cs in sorted(groups.items()):
        if HL._dates(cs) < 30:
            continue
        for arm, lab in [("x0.5", "0.50×"), ("x0.75", "0.75×"), ("A", "1.00× (hedge-2)"), ("x1.25", "1.25×"), ("x1.5", "1.50×")]:
            o = V.outcome(cs, arm, obj)
            out.append({"objective": obj, "volatility": vs, "action": lab, "cases": o.get("n"), "risk_reduction": o.get("loss_reduction"),
                        "profit_given_up": o.get("profit_sacrificed"), "cost": o.get("cost"), "basis_error": o.get("basis_error"),
                        **{f"U@{l:g}": V.utility(o, l) for l in lams}})
    return out


def run(db_path: str, amap: Optional[dict] = None, progress=None) -> dict:
    say = progress or (lambda m: None)
    t0 = time.time()
    res = {"started": time.strftime("%Y-%m-%d %H:%M:%S"), "cells": {}, "choices": {}, "counterfactual": {}, "final": {}}
    for kind in ("sizing", "product"):
        for lab, h in HORIZONS:
            try:
                if kind == "sizing":
                    cases = [c for d in V.load_dates(db_path, lab) for c in d["cases"]]
                else:
                    cases = [c for d in HN.load(db_path, f"product_{lab}_dates.pkl") for c in d["cases"]]
            except FileNotFoundError:
                continue
            for c in cases:
                c["end"] = HL._end(c["date"], h)
                c["_tp"] = HT._path(c)
            if kind == "sizing":
                _sizing_arms(cases)
                arms = [f"x{m:g}" for m in SIZES]
                res["counterfactual"][lab] = counterfactual(cases)
            else:
                arms = sorted({k for c in cases for k in c["arms"] if k.startswith("type:")})
            for lam in POLICY_LAMBDAS:
                scored, counts = walk_forward(cases, arms, lam, amap)
                res["choices"][f"{kind}/{lab}@{lam:g}"] = counts
                for c in scored:
                    arm = c["_pol"][lam][0]
                    c["arms"]["P"] = c["arms"][arm]
                for nd in sorted({c["_tp"][2] for c in scored}):
                    sub = [c for c in scored if c["_tp"][2] == nd]
                    if HL._dates(sub) < 30:
                        continue
                    cell = HT.cell_at(sub, sub[0]["objective"], "P", lam)
                    departed = sum(1 for c in sub if c["_pol"][lam][0] != "A")
                    res["cells"][f"{kind}/{lab}/{nd.split(':', 1)[1]}@{lam:g}"] = {**cell, "kind": kind, "horizon": lab, "node": nd,
                                                                                   "objective": sub[0]["objective"], "departures": departed,
                                                                                   "departure_share": departed / len(sub)}
                # today's policy (every matured case) — which contexts would depart from hedge-2
                fits = _fit(cases, arms, lam, amap)
                res["final"][f"{kind}/{lab}@{lam:g}"] = {f"{o}|{a}": {"n": f["n"], "eras": len(f["eras"]),
                                                                       "coef": dict(zip(["intercept"] + FEATURES, f["b"])) if f["b"] else None}
                                                          for (o, a), f in fits.items()}
                say(f"hedge policy {kind} {lab} λ={lam:g}: {len(scored)} cases scored ({time.time() - t0:.0f}s)")
            for c in cases:
                c.pop("_pol", None)
    _finalise(res["cells"])
    res["passed"] = sorted(k for k, c in res["cells"].items() if c.get("passed"))
    res["seconds"] = round(time.time() - t0, 1)
    return res


# ------------------------------------------------------------------ capability: a planted policy and a noise world
def synth_cases(kind: str = "planted", seed: int = 3, weeks: int = 880, books: Tuple[str, ...] = ("AAPL", "JPM", "XOM")) -> List[dict]:
    """Synthetic hedge cases (1W, min-variance). 'planted': in low volatility the book's true market beta is 0.5, so
    hedge-2's full market hedge over-hedges and 0.5× is right; in high volatility hedge-2 is right. 'noise': hedge-2 is
    right everywhere. Used by the capability test of the policy machinery."""
    import datetime as _d
    import random as _r
    rnd = _r.Random(seed)
    d0 = _d.date(2009, 1, 5)
    out = []
    vol = "low_vol"
    for w in range(weeks):
        if w % 26 == 0:
            vol = "high_vol" if rnd.random() < 0.5 else "low_vol"
        date = (d0 + _d.timedelta(days=7 * w)).isoformat()
        sig = 0.02 if vol == "high_vol" else 0.008
        mkt = [rnd.gauss(0, sig) * V.NAV for _ in range(5)]
        for b in books:
            beta = 0.5 if (kind == "planted" and vol == "low_vol") else 1.0
            u = [beta * m + rnd.gauss(0, 0.004) * V.NAV for m in mkt]
            hp = [-m for m in mkt]
            out.append({"book": b, "btype": "single stock", "objective": "min_variance", "u": u, "ideal": [beta * h for h in hp],
                        "arms": {"A": {"hp": hp, "cost": 60.0, "skew": 0.0, "mult": 1.0,
                                       "legs": [{"id": "SPY", "product_type": "etf", "q": 1.0, "notional": V.NAV, "beta_usd": V.NAV}]}},
                        "regime": {"volatility": vol, "market": "bull"}, "vix": 30.0 if vol == "high_vol" else 14.0, "primary": "MKT",
                        "date": date})
    return out


def capability(kind: str = "planted") -> dict:
    cases = synth_cases(kind)
    for c in cases:
        c["end"] = HL._end(c["date"], 5)
        c["_tp"] = HT._path(c)
    _sizing_arms(cases)
    arms = [f"x{m:g}" for m in SIZES]
    lam = 1.0
    scored, counts = walk_forward(cases, arms, lam, None)
    for c in scored:
        c["arms"]["P"] = c["arms"][c["_pol"][lam][0]]
    cells = {}
    for nd in sorted({c["_tp"][2] for c in scored}):
        sub = [c for c in scored if c["_tp"][2] == nd]
        cells[nd] = HT.cell_at(sub, sub[0]["objective"], "P", lam)
    _finalise(cells)
    low = [c for c in scored if c["regime"]["volatility"] == "low_vol"]
    high = [c for c in scored if c["regime"]["volatility"] == "high_vol"]
    share = lambda cs, arm: (sum(1 for c in cs if c["_pol"][lam][0] == arm) / len(cs)) if cs else None  # noqa: E731
    return {"kind": kind, "choices": counts, "low_vol_to_half": share(low, "x0.5"), "high_vol_kept": share(high, "A"),
            "passed": [k for k, c in cells.items() if c.get("passed")], "cells": {k: {"d": c.get("d"), "gates": c.get("gates")} for k, c in cells.items()}}
