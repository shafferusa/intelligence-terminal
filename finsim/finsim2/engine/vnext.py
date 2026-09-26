"""Shaffer vNext — the master summary across the three research programs (SHAFFER_VNEXT_SUMMARY.md) and the
challenger registry entries. Research only: nothing is promoted and production is unchanged."""
from __future__ import annotations

import time
from typing import List, Optional

from . import alphanext as A
from . import dirnext as DN


def _f(v, d=4):
    return "—" if v is None else f"{v:+.{d}f}"


def register(store, ra: Optional[dict], rd: Optional[dict], rh: Optional[dict]) -> List[str]:
    """One registry entry per vNext challenger (kind alpha / directional / hedge, status challenger or rejected), with its
    formula, inputs, horizon or objective, benchmark and results — so every experiment is versioned."""
    from .lab import _save_registry, registry
    reg = registry(store)
    made = []
    today = time.strftime("%Y-%m-%d")

    def put(v):
        old = next((x for x in reg["versions"] if x["id"] == v["id"]), None)
        if old and old.get("status") not in ("challenger", "rejected", None):
            return
        reg["versions"] = [x for x in reg["versions"] if x["id"] != v["id"]]
        v["introduced"] = (old or {}).get("introduced") or today
        reg["versions"].append(v)
        made.append(v["id"])
    for lab, hz in ((ra or {}).get("horizons") or {}).items():
        for n, c in hz["challengers"].items():
            put({"id": c["id"], "kind": "shaffer-alpha", "family": "vnext", "horizon": lab, "hierarchy": c["depth"],
                 "status": "challenger" if c.get("status") == "LIVE SHADOW ELIGIBLE" else "rejected",
                 "formula": "ridge on [production raw, " + ", ".join(c["features"]) + "] → y_alpha",
                 "data_sources": "SEC first-reported filings, own price panel, FRED", "training_cutoff": ra.get("started"),
                 "benchmark": (ra.get("benchmark") or {}).get("id"), "validation": {"gates": c.get("gates"), "useful": c.get("useful")}})
    for lab, hz in ((rd or {}).get("horizons") or {}).items():
        for n, c in hz["walkforward"]["challengers"].items():
            feats = DN.CHALLENGERS[n]
            put({"id": DN.vid(lab, n), "kind": "shaffer-directional", "family": "vnext", "horizon": lab, "hierarchy": "class" if n == "class" else "global",
                 "status": "challenger" if c.get("status") == "LIVE SHADOW ELIGIBLE" else "rejected",
                 "formula": "logistic on [1, μ/σ, raw/100, " + ", ".join(feats) + "]", "data_sources": "own OHLCV panel, FRED VIX",
                 "training_cutoff": rd.get("started"), "benchmark": "prior-only (PIT base prior)", "validation": {"gates": c.get("gates")}})
    for t, hs in ((rh or {}).get("tests") or {}).items():
        from ..hedge import hedgenext as HN
        cells = {f"{lab}:{o}": c.get("status") for lab, objs in hs.items() for o, c in objs.items()}
        ok = any(s == "LIVE SHADOW ELIGIBLE" for s in cells.values())
        put({"id": HN.vid(t), "kind": "hedge", "family": "vnext", "status": "challenger" if ok else "rejected", "parent": "hedge-2",
             "formula": HN.TEST_LABEL[t], "training_cutoff": rh.get("started"), "benchmark": "hedge-2 (realised utility)",
             "validation": {"cells": cells}})
    _save_registry(store, reg)
    return made


def summary(ra: Optional[dict], rd: Optional[dict], rh: Optional[dict]) -> str:
    L: List[str] = []
    w = L.append
    w("# Shaffer vNext — master summary")
    w("")
    w("Three separate research programs, each against fixed production on identical point-in-time records, unseen eras, the 2018 "
      "split, BH FDR and fixed gates. Production (shaffer-2.1, shaffer-alpha-2.1-production, the Directional research definition, "
      "hedge-2, the resizing live shadow, benchmark benchmark-2.1-2026-09-25) is unchanged; nothing is promoted. Details: "
      "`SHAFFER_ALPHA_VNEXT.md`, `SHAFFER_DIRECTIONAL_VNEXT.md`, `SHAFFER_HEDGE_VNEXT.md`.")
    w("")
    w("| System | Horizon / Objective | Production metric | Best challenger | Challenger metric | Incremental improvement | Eras positive | FDR | Live shadow? | Promotion? |")
    w("|---|---|---|---|---|---|---|---|---|---|")
    for lab, hz in ((ra or {}).get("horizons") or {}).items():
        p = (hz["production"].get("walkforward") or {})
        b = A.best_challenger(hz)
        c = hz["challengers"].get(b) or {}
        cm = ((c.get("walkforward") or {}).get("challenger") or {})
        g = c.get("gates") or {}
        pr = (c.get("walkforward") or {}).get("paired") or {}
        w(f"| Alpha | {lab} | rank IC {_f(p.get('rank_ic'))} (t {_f(p.get('rank_t'), 1)}) | {b or '—'} | rank IC {_f(cm.get('rank_ic'))} | "
          f"Δ rank IC {_f(pr.get('mean'))} (t {_f(pr.get('t'), 1)}) | {g.get('eras_won')}/{g.get('eras_complete')} | {'✓' if g.get('fdr') else '✗'} | "
          f"{'yes' if c.get('status') == 'LIVE SHADOW ELIGIBLE' else 'no'} | no |")
    for lab, hz in ((rd or {}).get("horizons") or {}).items():
        wf = hz["walkforward"]
        pr = wf["prior"]
        best = max(wf["challengers"].items(), key=lambda kv: kv[1]["vs_prior"].get("brier_t") or -99)
        n, c = best
        g = c.get("gates") or {}
        w(f"| Directional | {lab} | prior-only Brier {_f(pr.get('brier'))} | {n} | Brier {_f(c['metrics'].get('brier'))} | "
          f"Brier gain vs prior {_f(c['vs_prior'].get('brier_gain'), 5)} (t {_f(c['vs_prior'].get('brier_t'), 1)}) | {g.get('eras_won')}/{g.get('eras_complete')} | "
          f"{'✓' if g.get('fdr') else '✗'} | {'yes' if c.get('status') == 'LIVE SHADOW ELIGIBLE' else 'no'} | no |")
    if rd and "1M" not in (rd.get("horizons") or {}):
        w(f"| Directional | 1M | — | — | — | {rd.get('1M')} | — | — | no | no |")
    T = (rh or {}).get("tests") or {}
    major = ["min_variance", "target_vol", "beta", "systematic", "sector", "crash", "es", "duration", "credit", "fx"]
    for o in major:
        for lab in ("1W", "1M", "3M"):
            best = None
            for t, hs in T.items():
                c = (hs.get(lab) or {}).get(o)
                if not c or not (c.get("gates") or {}).get("enough"):
                    continue
                d = (c.get("d") or {}).get("1.0")
                if d is not None and (best is None or d > best[1]):
                    best = (t, d, c)
            if not best:
                continue
            t, d, c = best
            g = c.get("gates") or {}
            from ..hedge import hedgenext as HN
            w(f"| Hedge | {o} {lab} | U(hedge-2) {((c.get('a') or {}).get('loss_reduction') or 0) - ((c.get('a') or {}).get('profit_sacrificed') or 0) - ((c.get('a') or {}).get('cost') or 0):+,.0f} | "
              f"{HN.vid(t)} | U {((c.get('b') or {}).get('loss_reduction') or 0) - ((c.get('b') or {}).get('profit_sacrificed') or 0) - ((c.get('b') or {}).get('cost') or 0):+,.0f} | "
              f"ΔU(λ=1) {d:+,.0f} (t {_f(c.get('t'), 1)}) | {g.get('eras_won')}/{g.get('eras_complete')} | {'✓' if g.get('fdr') else '✗'} | "
              f"{'yes' if c.get('status') == 'LIVE SHADOW ELIGIBLE' else 'no'} | no |")
    w("")
    w("Alpha rows show the challenger with the best paired t (ties to the best evidence, not the best point estimate); Hedge rows the "
      "experiment with the largest ΔU at λ = 1 for that objective and horizon. \"Promotion\" is always no in this phase: promotion "
      "needs the live shadow (≥ 60 graded paired outcomes) and your explicit approval.")
    w("")
    return "\n".join(L) + "\n"
