"""A λ-aware live grader for hedge sizing: prospective evidence for a λ-conditional hedge size.

Why a new grader. The existing hedge live gate (lab.hedge_live_gate) re-scores the hedges the user happened to ask for
and measures realised VARIANCE per hedge group. A λ-conditional size (hedge/hedgetune.py: m(objective, λ, risk class,
horizon, volatility regime) × hedge-2) is a claim about UTILITY at a given risk preference,
    U(λ) = risk reduction − λ · profit sacrificed − cost,
which is a property of a distribution of outcomes, not of one hedge — and it needs a regular sample, not the user's
irregular requests.

Design (fixed 2026-09-26, before any live case exists):

1. The panel. On a fixed schedule per horizon (1W every 10 sessions, 1M every 21, 3M every 63 — the research steps,
   so windows do not overlap), the standard books × objectives of the hedge research (hedge/volhedge.py BOOKS,
   OBJECTIVES) are run through hedge-2 on that day's market. The recommended package (legs, quantities, costs, the
   targeted exposure change) is FROZEN into the store the day it is made — nothing about it can change later.
2. Grading. When the horizon has passed, the book's and each leg's daily P&L over the window are computed from market
   data (hedge/volhedge.leg_pnl, futures rolled at fair value) and the case is stored in exactly the research case
   format — so the live statistic is the backtest statistic.
3. The challenger's arm. For every λ-cell a version claims (objective node, horizon, λ, multiple m), the multiple is
   frozen into the registry entry when the panel first runs; its arm is hedge-2's frozen package × m.
4. The statistic: ΔU(λ) = U(m · hedge-2) − U(hedge-2) on identical live cases at that cell's λ, with the research's
   moving-block bootstrap over decision dates; realised cost, basis error and tail (ES) of both arms.
5. The live gate H-LIVE (a promotion still needs the explicit user action):
     a. ≥ LIVE_MIN_DATES (26) graded decision dates in the cell;
     b. ΔU(λ) ≥ 0 live;
     c. consistent with the backtest: live ΔU not below the backtest ΔU by more than 2 live standard errors;
     d. H3 and H4 hold live (tail not worse by more than 2% of the unhedged ES; cost ≤ +10%, basis error ≤ +5% — the
        fixed thresholds; a multiple below 1 passes H4 trivially on cost);
   Significance of ΔU is not required live: the backtest t of the passing cells was ~1.7 over decades.
"""
from __future__ import annotations

import math
import time
from typing import Dict, List, Optional

from . import volhedge as V

INDEX_KEY = "hedgelive:index"
LIVE_MIN_DATES = 26
Z_CONSISTENT = -2.0
GATE_FIXED = "2026-09-26"
BOOT_REPS = 400


def _key(lab: str, date: str) -> str:
    return f"hedgelive:{lab}:{date}"


def _index(store) -> Dict[str, List[dict]]:
    return store.kv_get(INDEX_KEY) or {}


def due(store, lab: str, i: int, step: int) -> bool:
    ix = [x for x in _index(store).get(lab) or [] if not x.get("replay")]
    return not ix or i - ix[-1]["i"] >= step


# ------------------------------------------------------------------ 1. the panel: freeze hedge-2's packages
def record(research, progress=None, labs: Optional[List[str]] = None, force: bool = False, books=None, objectives=None,
           at: Optional[int] = None) -> dict:
    """Freeze hedge-2's packages for today's panel. `at` (a past session) is for tests and replays only — a replayed
    entry is marked and never counts as live evidence."""
    from . import engine as E
    from .market import Market
    from .risk import RiskModel
    say = progress or (lambda m: None)
    store = research.store
    cal = research.panel().calendar()
    i = len(cal) - 1 if at is None else at
    out = {}
    for lab, h, step in V.HORIZONS:
        if labs and lab not in labs:
            continue
        if not force and not due(store, lab, i, step):
            continue
        t0 = time.time()
        m = Market(research, i)
        rk = RiskModel(m)
        regime = m.regime()
        vix, _ = m.macro("VIXCLS")
        cases, errors = [], []
        for book in books or V.BOOKS:
            pos = V.book_positions(book, m, store)
            if pos is None:
                continue
            same: Dict[str, dict] = {}
            for obj in objectives or V.OBJECTIVES:
                twin = "es" if obj in ("es", "var", "drawdown") else None
                try:
                    if twin and twin in same:
                        a = dict(same[twin], objective=obj)
                    else:
                        a = E.analyze(research, pos, obj, V._params(obj, lab), nav=V.NAV, replay={"market": m, "rk": rk})
                        if twin:
                            same[twin] = a
                except Exception as e:  # noqa: BLE001
                    errors.append(f"{book['key']} {obj}: {type(e).__name__}: {e}")
                    continue
                if not V._applicable(a):
                    continue
                pk = a["package"]
                S = a["targeted"]
                cases.append({"book": book["key"], "btype": book["type"], "objective": obj, "pos": pos,
                              "legs": [{"id": L["id"], "type": L["type"], "product_type": L["product_type"], "quantity": L["quantity"],
                                        "notional": L["notional"], "beta_usd": L["quantity"] * L["H"].get("MKT", 0.0),
                                        "option": L["type"] == "OPTION"} for L in pk],
                              "cost": sum((L.get("cost") or 0.0) for L in pk), "skew": sum(V._skew_cost(L, m, store) for L in pk),
                              "S": S, "dR": {f: a["Rstar"].get(f, a["R"].get(f, 0.0)) - a["R"].get(f, 0.0) for f in S},
                              "primary": a.get("primary_factor"), "var_prod": a.get("var_mkt"), "regime": regime, "vix": vix})
        entry = {"date": cal[i], "i": i, "lab": lab, "h": h, "made": time.strftime("%Y-%m-%d %H:%M:%S"), "cases": cases,
                 "errors": errors[:20], "graded": False, "replay": at is not None}
        store.kv_set(_key(lab, cal[i]), entry)
        ix = _index(store)
        ix[lab] = sorted([x for x in (ix.get(lab) or []) if x["date"] != cal[i]] + [{"date": cal[i], "i": i, "n": len(cases), "graded": False,
                                                                                       "replay": at is not None}], key=lambda x: x["i"])
        store.kv_set(INDEX_KEY, ix)
        out[lab] = {"date": cal[i], "cases": len(cases), "seconds": round(time.time() - t0, 1), "errors": len(errors)}
        say(f"hedge panel {lab} {cal[i]}: {len(cases)} cases frozen")
    return out


# ------------------------------------------------------------------ 2. grading at maturity
def grade(research, progress=None) -> dict:
    from .market import Market
    from .risk import factor_series
    say = progress or (lambda m: None)
    store = research.store
    last = len(research.panel().calendar()) - 1
    ix = _index(store)
    done = {}
    F = None
    for lab, rows in ix.items():
        for row in rows:
            if row.get("graded") or row["i"] + dict((l, h) for l, h, _ in V.HORIZONS)[lab] > last:
                continue
            e = store.kv_get(_key(lab, row["date"])) or {}
            h, i = e["h"], e["i"]
            markets = [Market(research, i + k) for k in range(h + 1)]
            F = F or factor_series(research)
            cache: dict = {}
            graded = []
            for c in e["cases"]:
                u = V._book_pnl(c["pos"], markets, store, cache)
                if u is None:
                    continue
                hp, ok = [0.0] * h, True
                for L in c["legs"]:
                    p = V.leg_pnl(research, L, markets, store, cache)
                    if p is None:
                        ok = False
                        break
                    hp = [x + y for x, y in zip(hp, p)]
                if not ok:
                    continue
                ideal = [sum(c["dR"][f] * ((F.get(f) or [None] * (k + 1))[k] or 0.0) for f in c["S"]) for k in range(i + 1, i + h + 1)]
                legs = [{"id": L["id"], "type": L["type"], "product_type": L["product_type"], "q": L["quantity"], "notional": L["notional"],
                         "beta_usd": L["beta_usd"], "option": L["option"]} for L in c["legs"]]
                graded.append({"date": e["date"], "end": markets[-1].asof, "book": c["book"], "btype": c["btype"], "objective": c["objective"],
                               "u": u, "ideal": ideal, "arms": {"A": {"hp": hp, "cost": c["cost"], "legs": legs, "skew": c["skew"], "mult": 1.0}},
                               "var_prod": c.get("var_prod"), "regime": c.get("regime"), "vix": c.get("vix"), "S": c["S"], "primary": c.get("primary")})
            e["graded"], e["graded_cases"], e["graded_on"] = True, graded, markets[-1].asof
            store.kv_set(_key(lab, row["date"]), e)
            row["graded"], row["n_graded"] = True, len(graded)
            done.setdefault(lab, 0)
            done[lab] += len(graded)
            say(f"hedge panel {lab} {row['date']}: {len(graded)} cases graded")
    store.kv_set(INDEX_KEY, ix)
    return done


def live_cases(store, lab: str, replays: bool = False) -> List[dict]:
    out = []
    for row in _index(store).get(lab) or []:
        if row.get("graded") and (replays or not row.get("replay")):
            out += (store.kv_get(_key(lab, row["date"])) or {}).get("graded_cases") or []
    return out


# ------------------------------------------------------------------ 3. the λ-cells a version claims, frozen
def claimed_cells(v: dict) -> List[dict]:
    """[{horizon, node, objective, lam, multiple}] — from the version's frozen cells."""
    out = []
    for k, c in (v.get("live_cells") or v.get("eligible_cells") or {}).items():
        lab, rest = k.split(" ", 1)
        node, lam = rest.split("@")
        if c.get("multiple") is None:
            continue
        out.append({"key": k, "horizon": lab, "node": node, "objective": c.get("objective") or node.split("/")[-1],
                    "lam": float(lam), "multiple": float(c["multiple"])})
    return out


def activate(store, vid: str = "hedge-lambda-sizing-exp") -> Optional[dict]:
    """Put a λ-conditional sizing version whose cells passed every research gate into live shadow, freezing its cells,
    their multiples and their backtest ΔU (never overwritten afterwards)."""
    from ..engine.lab import _save_registry, registry
    reg = registry(store)
    v = next((x for x in reg["versions"] if x["id"] == vid), None)
    if not v or not v.get("eligible_cells"):
        return None
    if v.get("status") == "challenger" and v.get("live_cells"):
        return v
    cells = (store.kv_get("lab:hedgetune") or {}).get("cells") or {}
    frozen = {}
    for k, c in v["eligible_cells"].items():
        lab, rest = k.split(" ", 1)
        bt = (cells.get(lab) or {}).get(rest) or {}
        ci = bt.get("ci")
        frozen[k] = {**c, "backtest_d": bt.get("d"), "backtest_se": ((ci[1] - ci[0]) / 3.92) if ci else None, "backtest_dates": bt.get("dates")}
    v.update({"status": "challenger", "live_cells": frozen, "live_shadow_from": v.get("live_shadow_from") or time.strftime("%Y-%m-%d"),
              "live_gate": f"H-LIVE (fixed {GATE_FIXED})", "note": "graded by hedge/hedgelive.py (λ-specific utility on a fixed panel)"})
    _save_registry(store, reg)
    return v


# ------------------------------------------------------------------ 4–5. the statistic and the gate
def cell_live(store, cell: dict, cases: Optional[List[dict]] = None) -> dict:
    from . import hedgetune as HT
    cs = cases if cases is not None else live_cases(store, cell["horizon"])
    sub = []
    for c in cs:
        if c["objective"] != cell["objective"]:
            continue
        c["_tp"] = HT._path(c)
        if c["_tp"][2] != f"objective:{cell['node']}":
            continue
        c["arms"]["L"] = HT._scaled_arm(c, cell["multiple"])
        sub.append(c)
    dates = len({c["date"] for c in sub})
    out = {"cell": cell["key"], "dates": dates, "cases": len(sub), "required": LIVE_MIN_DATES, "lam": cell["lam"], "multiple": cell["multiple"]}
    if not sub:
        out["passed"] = False
        return out
    L_ = str(cell["lam"])
    p = V.paired(sub, cell["objective"], "A", "L", reps=BOOT_REPS if dates >= 12 else 0)
    a_, b_ = p.get("a") or {}, p.get("b") or {}
    d = (p.get("d") or {}).get(L_)
    ci = (p.get("ci") or {}).get(L_)
    se = ((ci[1] - ci[0]) / 3.92) if ci else None
    out.update({"d": d, "ci": ci, "p": (p.get("p") or {}).get(L_), "a": {k: a_.get(k) for k in ("loss_reduction", "profit_sacrificed", "cost", "basis_error", "es_h")},
                "b": {k: b_.get(k) for k in ("loss_reduction", "profit_sacrificed", "cost", "basis_error", "es_h")}})
    worse_es = (b_.get("es_h") or 0) - (a_.get("es_h") or 0)
    worse_cost = ((b_.get("cost") or 0) / a_["cost"] - 1) if a_.get("cost") else 0.0
    worse_basis = ((b_.get("basis_error") or 0) / a_["basis_error"] - 1) if a_.get("basis_error") else 0.0
    bt_d = cell.get("backtest_d")
    out["z_consistency"] = ((d - bt_d) / se) if d is not None and bt_d is not None and se else None
    checks = {"dates": dates >= LIVE_MIN_DATES, "utility": bool(d is not None and d >= 0),
              "consistent": bool(out["z_consistency"] is not None and out["z_consistency"] >= Z_CONSISTENT),
              "H3": bool(a_.get("es_u") and worse_es <= V.G4_ES * a_["es_u"]), "H4": bool(worse_cost <= V.G4_COST and worse_basis <= V.G4_BASIS)}
    out["checks"] = checks
    out["passed"] = all(checks.values())
    return out


def live_gate(store, v: dict) -> dict:
    cells = [dict(c, **{k: (v.get("live_cells") or {}).get(c["key"], {}).get(k) for k in ("backtest_d", "backtest_se")}) for c in claimed_cells(v)]
    by = {}
    res = [cell_live(store, c, by.setdefault(c["horizon"], live_cases(store, c["horizon"]))) for c in cells]
    return {"gate": "H-LIVE", "cells": res, "graded": sum(r["dates"] for r in res), "required": LIVE_MIN_DATES,
            "passed": bool(res) and any(r["passed"] for r in res), "passed_cells": [r["cell"] for r in res if r["passed"]]}


def summary(store) -> dict:
    from ..engine.lab import registry
    ix = {lab: [r for r in rows if not r.get("replay")] for lab, rows in _index(store).items()}
    out = {"panel": {lab: {"dates": len(rows), "graded": sum(1 for r in rows if r.get("graded")), "last": rows[-1]["date"] if rows else None,
                           "cases": sum(r.get("n", 0) for r in rows)} for lab, rows in ix.items()},
           "gate": {"fixed": GATE_FIXED, "min_dates": LIVE_MIN_DATES, "z_consistent": Z_CONSISTENT}, "versions": {}}
    for v in registry(store)["versions"]:
        if v.get("kind") == "hedge" and v.get("live_cells") and v.get("status") == "challenger":
            out["versions"][v["id"]] = live_gate(store, v)
    return out
