"""Live evidence for cross-sectional (ranking) challengers — the learned Shaffer Alpha models in live shadow.

Why this exists. The generic live gate (lab.live_gate) pools (asset, date) forecasts: 60 graded forecasts are reached
the first week a daily run scores ~150 assets, and it measures a pooled time-series IC. A ranking model such as the
learned 1W Alpha (alpha-learned-1w-global-exp / -hierarchy-exp) was validated on WEEKLY CROSS-SECTIONAL rank IC over
the whole research universe. Its live evidence must be the same statistic on the same kind of cross-section, one
independent observation per week — and the daily loop only scores the tracked assets (core markets + holdings +
watchlist), a dozen names, not ~155.

1. The weekly panel. Once per ISO week (the first daily run of the week), every research-universe asset is scored:
   production (model "shaffer", recorded by the Shaffer sweep) and every Shaffer challenger in live shadow (model
   "shaffer:<version>", lab.record_shadow). Rows are append-only and graded by tracking.score_matured like any forecast.
   One panel a week keeps 1W observations non-overlapping.

2. The statistic. On each matured panel date: the Spearman rank IC of the challenger's scores with the realised
   returns over the cross-section of assets that have both a challenger and a production forecast (≥ MIN_XS names),
   the same for production, and their difference Δ.

3. The live gate G3-XS, fixed on 2026-09-26 before any live panel was graded (it replaces lab.live_gate for ranking
   challengers of the learned families; a promotion still needs the explicit user action):
     a. ≥ XS_MIN_WEEKS (52) graded weekly cross-sections;
     b. the live edge exists: mean live rank IC > 0 with t ≥ 1.65 (one-sided 5%);
     c. not worse than production live: mean Δ ≥ 0;
     d. consistent with the backtest: live Δ is not below the backtest Δ by more than 2 standard errors (z ≥ −2);
     e. no decay alarm: a one-sided CUSUM on Δ (reference half the backtest Δ, threshold 5 weekly SDs) never fired.
   Beating production at t ≥ 2 is NOT required live: at the backtest effect sizes it would take ~4 years (hierarchy)
   to ~14 years (global) of weekly observations — `weeks_needed` reports it. The live gate asks whether the edge is
   present and behaves as the backtest said; superiority over production was established out of sample.

The backtest expectation of every model is frozen into its registry entry the first time the panel runs
(`live_expectation`), so later research reruns cannot move the goalposts.

4. Validated is not superior. A G3-XS pass says: the model keeps positive live ranking skill, has not materially
   deteriorated from its backtest, and is not worse than production. It does NOT prove the model is better than
   production. That is reported separately, every week, as the accumulated live difference
       Δ = IC_model − IC_production   (mean over weekly cross-sections, 95% interval, P(Δ > 0))
   with its own label — DEMONSTRABLY SUPERIOR LIVE only when the whole 95% interval is above zero — and the same for the
   learned hierarchy against the learned global model (does the hierarchy add value live, or did it only fit history?).
   A promotion records both.
"""
from __future__ import annotations

import datetime as _dt
import math
import time
from typing import Dict, List, Optional

PANEL_KEY = "livexs:panels"
MIN_XS = 60                 # assets with both forecasts on a panel date
XS_MIN_WEEKS = 52
T_EDGE = 1.65
Z_CONSISTENT = -2.0
CUSUM_H = 5.0
RANKING_FAMILIES = ("learned", "finetune")
GATE_FIXED = "2026-09-26"
# the learned engine's live-shadow versions → the fine-tune research model that reproduces them
FT_MODEL = {"alpha-learned-1w-global-exp": "learned global (D)", "alpha-learned-1w-hierarchy-exp": "learned hierarchy (E)"}
# a hierarchy is also judged live against its own global sibling
SIBLING = {"alpha-learned-1w-hierarchy-exp": "alpha-learned-1w-global-exp"}
SUPERIOR, WORSE, UNRESOLVED, NONE = "DEMONSTRABLY SUPERIOR LIVE", "WORSE LIVE", "NOT RESOLVED", "NO LIVE WEEKS YET"


def _week(d: str) -> str:
    y, w, _ = _dt.date.fromisoformat(d).isocalendar()
    return f"{y}-W{w:02d}"


def _ranks(xs: List[float]) -> List[float]:
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    r = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        for k in range(i, j + 1):
            r[order[k]] = (i + j) / 2.0 + 1
        i = j + 1
    return r


def rank_ic(x: List[float], y: List[float]) -> Optional[float]:
    n = len(x)
    if n < 3:
        return None
    rx, ry = _ranks(x), _ranks(y)
    m = (n + 1) / 2.0
    num = sum((a - m) * (b - m) for a, b in zip(rx, ry))
    den = math.sqrt(sum((a - m) ** 2 for a in rx) * sum((b - m) ** 2 for b in ry))
    return num / den if den > 0 else None


def is_ranking(v: dict) -> bool:
    return str(v.get("kind", "")).startswith("shaffer") and v.get("family") in RANKING_FAMILIES \
        and any(lab in ("1D", "1W") for lab in (v.get("validation") or {}))


def lab_of(v: dict) -> str:
    labs = [lab for lab in (v.get("validation") or {}) if lab in ("1D", "1W")]
    return labs[0] if labs else "1W"


# ------------------------------------------------------------------ 1. the weekly panel
def due(store, today: str) -> bool:
    return not any(p.get("week") == _week(today) for p in (store.kv_get(PANEL_KEY) or []))


def _cp_worker(db_path: str, asset_id: str):
    """Phase 1: an asset's January checkpoints (its own evidence sums only — priors play no part in them)."""
    from ..data.store import Store
    from .research import Research
    from .shaffer import ShafferRun, _cp_payload
    st = Store(db_path)
    try:
        run = ShafferRun(Research(st), asset_id, use_priors=False)
        run.run(checkpoints_only=True, save=False)
        return asset_id, _cp_payload(run.cls, run.yearly, run.signals)
    finally:
        st.close()


def _sweep_worker(db_path: str, asset_id: str, overrides: dict):
    """Phase 2: one asset's production sweep — the computation Research.shaffer_full makes — seeing exactly the
    checkpoints a sequential run would have stored before reaching it (`overrides`), returned to the single writer."""
    from ..data.store import Store
    from .research import Research, clean
    from .shaffer import ShafferRun, summarize
    st = Store(db_path)
    try:
        r = Research(st)
        r._cp_overrides = overrides or None
        key = r.shaffer_key(asset_id)
        run = ShafferRun(r, asset_id)
        full = clean(summarize(run.run(save=False), run))
        return asset_id, key, full, (run.cls, run.yearly, run.signals)
    finally:
        st.close()


def precompute(research, assets: List[str], workers: int = 0, progress=None) -> dict:
    """Production sweeps for the panel assets whose cached result is missing, in parallel worker processes, with
    results identical to the sequential path (record_panel with workers=1).

    The sequential path has an order effect: each sweep saves the asset's January checkpoints, and later sweeps read
    every other asset's checkpoints as priors — so when a stored checkpoint is out of date, assets later in the panel
    order see the recomputed one and earlier assets the stored one. To reproduce that exactly: phase 1 recomputes the
    checkpoints of every asset to be swept (they depend on the asset's own data only); phase 2 gives each asset the
    recomputed checkpoints of the assets swept before it in panel order and the stored ones otherwise. Every write
    (the cached result, the checkpoints, production's ledger rows) is made here, in panel order."""
    import json
    import os
    from concurrent.futures import ProcessPoolExecutor
    from .. import shaffer_score as cfg
    from .shaffer import save_checkpoints
    from .tracking import record_shaffer
    say = progress or (lambda m: None)
    st = research.store
    todo = [a for a in assets if st.kv_get(research.shaffer_key(a)) is None]
    workers = workers or max(1, min(4, (os.cpu_count() or 2) - 1))
    if len(todo) < 2 or workers < 2 or getattr(st, "_memory", False):
        return {"parallel": 0, "todo": len(todo)}
    t0 = time.time()
    import multiprocessing as mp
    # spawn on every platform: the Windows default, and safe from inside the server's threads (no fork of an open
    # SQLite connection)
    with ProcessPoolExecutor(max_workers=workers, mp_context=mp.get_context("spawn")) as ex:
        new_cp = dict(ex.map(_cp_worker, [st.path] * len(todo), todo))
        changed = [a for a in todo if json.dumps(st.kv_get(f"shaffer_cp:{a}:{cfg.VERSION}"), sort_keys=True) != json.dumps(new_cp[a], sort_keys=True)]
        say(f"live panel: {len(changed)} checkpoint(s) out of date ({time.time() - t0:.0f}s)")
        pos = {a: i for i, a in enumerate(todo)}
        ov = [{c: new_cp[c] for c in changed if pos[c] < pos[a]} for a in todo]
        done = 0
        for a, key, full, cp in ex.map(_sweep_worker, [st.path] * len(todo), todo, ov):
            st.kv_set(key, full)
            save_checkpoints(research, a, cp[0], cp[1], cp[2])
            try:
                record_shaffer(st, research.panel(), a, full)
            except Exception:  # noqa: BLE001
                pass
            done += 1
            say(f"live panel sweeps {done}/{len(todo)}: {a}")
    return {"parallel": done, "todo": len(todo), "workers": workers, "stale_checkpoints": changed, "seconds": round(time.time() - t0, 1)}


def record_panel(research, progress=None, force: bool = False, workers: int = 0) -> dict:
    """Score every research-universe asset for production and every Shaffer challenger in live shadow (once a week).
    Production's sweeps run in parallel worker processes first (identical results, one writer); `workers=1` keeps the
    sequential path."""
    from . import lab
    say = progress or (lambda m: None)
    st = research.store
    today = research.panel().calendar()[-1]
    if not force and not due(st, today):
        return {"skipped": "panel already recorded this week", "date": today}
    reg = lab.registry(st)
    chal = [v for v in reg["versions"] if str(v["kind"]).startswith("shaffer") and v["status"] == "challenger"]
    freeze_expectations(st, reg)
    assets = st.lab_record_assets(lab.SIG_VERSION, "1W")
    t0 = time.time()
    pre = precompute(research, assets, workers, say) if workers != 1 else {"parallel": 0}
    n_ok, n_rows, errors = 0, 0, []
    for k, a in enumerate(assets):
        say(f"live panel {k + 1}/{len(assets)}: {a}")
        try:
            from .tracking import record_shaffer
            record_shaffer(st, research.panel(), a, research.shaffer_full(a))   # production (idempotent per date)
            n_rows += lab.record_shadow(research, a)  # every challenger in live shadow, same date
            n_ok += 1
        except Exception as e:  # noqa: BLE001
            errors.append(f"{a}: {type(e).__name__}: {e}")
    rec = {"date": today, "week": _week(today), "assets": n_ok, "challengers": [v["id"] for v in chal], "rows": n_rows,
           "seconds": round(time.time() - t0, 1), "precompute": pre, "errors": errors[:20]}
    panels = [p for p in (st.kv_get(PANEL_KEY) or []) if p.get("date") != today] + [rec]
    st.kv_set(PANEL_KEY, sorted(panels, key=lambda p: p["date"]))
    st.audit("lab.live_panel", today, {k: v for k, v in rec.items() if k != "errors"})
    return rec


# ------------------------------------------------------------------ 2. backtest expectations (frozen)
def expectation(store, v: dict) -> Optional[dict]:
    """The backtest's weekly rank IC and Δ vs production for this version, with their weekly standard deviations."""
    lab_ = lab_of(v)
    ft = (store.kv_get("lab:finetune") or {}).get(lab_) or {}
    m = None
    key = FT_MODEL.get(v["id"]) or v.get("model")
    if key:
        m = (ft.get("models") or {}).get(key)
    if m:
        wf = m.get("walkforward") or {}
        c, p = wf.get("challenger") or {}, wf.get("paired") or {}
        weeks = p.get("weeks")
        ric, rt, d, dt = c.get("rank_ic"), c.get("rank_t"), p.get("mean"), p.get("t")
        src = f"lab:finetune {lab_} '{key}'"
    else:
        g = ((v.get("validation") or {}).get(lab_) or {}).get("gates") or {}
        d, dt, weeks, ric, rt = g.get("d_rank_ic"), g.get("t"), None, None, None
        src = f"registry validation {lab_}"
    if d is None or not dt:
        return None
    weeks = weeks or 900
    sd_d = abs(d) * math.sqrt(weeks) / abs(dt)
    sd_ic = (abs(ric) * math.sqrt(weeks) / abs(rt)) if ric and rt else None
    return {"lab": lab_, "rank_ic": ric, "sd_ic": sd_ic, "d": d, "sd_d": sd_d, "weeks": weeks, "source": src}


def freeze_expectations(store, reg: Optional[dict] = None) -> List[str]:
    """Write each ranking challenger's backtest expectation into its registry entry once (never overwritten)."""
    from . import lab
    reg = reg or lab.registry(store)
    done = []
    for v in reg["versions"]:
        if v.get("status") != "challenger" or not is_ranking(v) or v.get("live_expectation"):
            continue
        e = expectation(store, v)
        if e:
            v["live_expectation"] = {**e, "frozen": time.strftime("%Y-%m-%d"), "gate": f"G3-XS (fixed {GATE_FIXED})"}
            done.append(v["id"])
    if done:
        lab._save_registry(store, reg)
    return done


# ------------------------------------------------------------------ 3. the statistic and the gate
def weekly(store, vid: str, lab_: str, ref: str = "shaffer") -> List[dict]:
    """Per matured panel date: n, the challenger's and the reference model's (production unless `ref` names another
    ledger model) cross-sectional rank IC on the same assets, and Δ."""
    dates = {p["date"] for p in (store.kv_get(PANEL_KEY) or [])}
    ch, pr = {}, {}
    for p in store.predictions(horizon=lab_):
        if p["made_on"] not in dates or p.get("realized") is None:
            continue
        val = p.get("raw") if p.get("raw") is not None else p.get("score")
        if val is None:
            continue
        if p["model"] == f"shaffer:{vid}":
            ch.setdefault(p["made_on"], {})[p["asset_id"]] = (val, p["realized"])
        elif p["model"] == ref:
            pr.setdefault(p["made_on"], {})[p["asset_id"]] = (val, p["realized"])
    out = []
    for d in sorted(ch):
        common = [a for a in ch[d] if a in pr.get(d, {})]
        if len(common) < MIN_XS:
            continue
        y = [ch[d][a][1] for a in common]
        ic_c = rank_ic([ch[d][a][0] for a in common], y)
        ic_p = rank_ic([pr[d][a][0] for a in common], y)
        if ic_c is None or ic_p is None:
            continue
        out.append({"date": d, "n": len(common), "ic": ic_c, "ic_prod": ic_p, "d": ic_c - ic_p})
    return out


def _mt(xs: List[float]):
    n = len(xs)
    if n < 2:
        return (xs[0] if xs else None), None, None
    m = sum(xs) / n
    sd = math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1))
    return m, sd, (m / (sd / math.sqrt(n)) if sd > 0 else None)


def cusum(ds: List[float], ref: float, sd: float) -> dict:
    """One-sided CUSUM for a drop of Δ from its backtest level toward zero: S_k = max(0, S_{k−1} + (ref − Δ_k))."""
    s, peak, fired = 0.0, 0.0, None
    for k, d in enumerate(ds):
        s = max(0.0, s + (ref - d))
        peak = max(peak, s)
        if fired is None and sd > 0 and s > CUSUM_H * sd:
            fired = k
    return {"S": s, "peak": peak, "threshold": CUSUM_H * sd, "fired_at_week": fired}


def _phi(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def superiority(ds: List[float]) -> dict:
    """The accumulated live difference Δ over weekly cross-sections: mean, standard error, 95% interval, one-sided p and
    P(Δ > 0) (normal approximation, flat prior) — and a label kept separate from the validation gate."""
    n = len(ds)
    m, sd, t = _mt(ds)
    if n < 2 or sd is None:
        return {"weeks": n, "mean": m, "status": NONE if n == 0 else UNRESOLVED}
    se = sd / math.sqrt(n)
    lo, hi = m - 1.96 * se, m + 1.96 * se
    return {"weeks": n, "mean": m, "se": se, "ci95": [lo, hi], "t": t, "p_one_sided": (1.0 - _phi(t)) if t is not None else None,
            "prob_positive": _phi(t) if t is not None else None,
            "status": SUPERIOR if lo > 0 else (WORSE if hi < 0 else UNRESOLVED)}


def live_gate(store, v: dict) -> dict:
    """G3-XS for a ranking challenger (see the module docstring)."""
    lab_ = lab_of(v)
    rows = weekly(store, v["id"], lab_)
    e = v.get("live_expectation") or expectation(store, v) or {}
    ics, ds = [r["ic"] for r in rows], [r["d"] for r in rows]
    m_ic, sd_ic, t_ic = _mt(ics)
    m_d, sd_d, t_d = _mt(ds)
    out = {"gate": "G3-XS", "lab": lab_, "weeks": len(rows), "required": XS_MIN_WEEKS, "mean_ic": m_ic, "t_ic": t_ic,
           "mean_ic_prod": _mt([r["ic_prod"] for r in rows])[0], "mean_d": m_d, "t_d": t_d, "expectation": e,
           "graded": len(rows), "last": rows[-1]["date"] if rows else None,
           "mean_n": (sum(r["n"] for r in rows) / len(rows)) if rows else None}
    sdd = e.get("sd_d") or sd_d
    if e.get("d") is not None and sdd and rows:
        out["z_consistency"] = (m_d - e["d"]) / (sdd / math.sqrt(len(rows)))
        out["cusum"] = cusum(ds, 0.5 * e["d"], sdd)
    if e.get("d"):
        out["weeks_needed_vs_production"] = math.ceil((2.0 * (e.get("sd_d") or 0) / abs(e["d"])) ** 2) if e.get("sd_d") else None
    if e.get("rank_ic") and e.get("sd_ic"):
        out["weeks_needed_edge"] = math.ceil((T_EDGE * e["sd_ic"] / abs(e["rank_ic"])) ** 2)
    checks = {"weeks": len(rows) >= XS_MIN_WEEKS,
              "edge": bool(m_ic is not None and m_ic > 0 and (t_ic or 0) >= T_EDGE),
              "vs_production": bool(m_d is not None and m_d >= 0),
              "consistent": bool(out.get("z_consistency") is not None and out["z_consistency"] >= Z_CONSISTENT),
              "no_decay_alarm": bool(out.get("cusum") is not None and out["cusum"]["fired_at_week"] is None)}
    out["checks"] = checks
    out["passed"] = all(checks.values())
    out["label"] = "LIVE VALIDATED (G3-XS)" if out["passed"] else "ACCUMULATING"
    # kept apart from the gate: is it better than production live, and (hierarchies) better than its global sibling?
    out["superiority_vs_production"] = {**superiority(ds), "reference": "production",
                                        "weeks_needed_95": out.get("weeks_needed_vs_production")}
    sib = SIBLING.get(v["id"])
    if sib:
        out["vs_sibling"] = {**superiority([r["d"] for r in weekly(store, v["id"], lab_, ref=f"shaffer:{sib}")]), "reference": sib}
    return out


def summary(store) -> dict:
    """Every ranking challenger in live shadow: its live evidence and gate, plus the panel log."""
    from . import lab
    reg = lab.registry(store)
    out = {"panels": (store.kv_get(PANEL_KEY) or [])[-12:], "models": {}, "gate": {
        "fixed": GATE_FIXED, "min_weeks": XS_MIN_WEEKS, "t_edge": T_EDGE, "z_consistent": Z_CONSISTENT, "cusum_h": CUSUM_H, "min_xs": MIN_XS}}
    for v in reg["versions"]:
        if v.get("status") == "challenger" and is_ranking(v):
            g = live_gate(store, v)
            g["recent"] = weekly(store, v["id"], g["lab"])[-12:]
            out["models"][v["id"]] = g
    return out
