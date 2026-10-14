"""The 1M Alpha challenger in live shadow: research only. It is never shown as a score and never promoted by itself.

Protocol: SHAFFER_ALPHA_1M_SHADOW_PROTOCOL.md, fixed on 2026-09-28 before any live row was recorded.

Stage 2 (SHAFFER_ALPHA_HORIZONS.md) found one horizon worth watching. At 1M, the horizon / sector ridge (V*) beat
production's rank IC in all 4 complete eras, but only by +0.012 (t 1.1), and not on the original 45 stocks. That is
not validated. Whether the effect persists can only be learned forward, on data no fit has seen. So:

1. Frozen weights. The V* 1M model was fitted once on every matured stage-2 record, with the last era's nested
   choice (depth 4 = global → horizon group → horizon → sector, K = 10). It is saved with a SHA-256 hash in
   `finsim2/frozen/alpha_hz_1m.json`. The file is never refitted. Any change to it is a new model with a new id and
   a fresh live clock.
2. Weekly ledger. On the first daily run of each ISO week, every research stock gets a row. The row holds the
   challenger's score (model `alpha-hz:1m`) and its within-date percentile. It goes into the append-only prediction
   ledger and is graded at 21 sessions like every forecast. Production's own 1M rows ("shaffer") come from the live
   panel and the daily loop on the same date. Stocks the frozen fit never saw fall back to their sector's weights.
3. The statistic. On each graded week:
   - the challenger's cross-sectional rank IC, over every stock it scored;
   - on the stocks where production also has a forecast, both rank ICs and Δ = IC_challenger − IC_production;
   - the same Δ on the original (pre-expansion) stocks.
   Weekly 1M outcomes overlap (21 sessions, sampled every 5), so every t-statistic is divided by √(21/5).
4. The live gate (see the protocol for the reasoning). "The effect persists" needs every one of these:
   a. at least 52 graded weeks with at least 60 stocks common to both models;
   b. the challenger's mean live IC is above 0;
   c. mean live Δ ≥ 0 (not worse than production);
   d. live Δ is consistent with the backtest: z ≥ −2 against the backtest's +0.0118, using the backtest's standard
      deviation;
   e. mean live Δ on the original stocks is ≥ 0 (the backtest's weak spot);
   f. the decay alarm never fired: a one-sided CUSUM with reference half the backtest Δ and threshold 5 weekly
      standard deviations × √(21/5).
   "Better than production" is reported separately: its 95% interval must lie above zero. At the backtest's effect
   size that would take decades of weekly data, and `weeks_needed` shows how many.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import math
import os
import time
from typing import Dict, List, Optional

FROZEN_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "frozen", "alpha_hz_1m.json")
MODEL = "alpha-hz:1m"                  # ledger model name — deliberately not "shaffer:*" (never a registry challenger)
PANEL_KEY = "ahzlive:panels"
LAB, H = "1M", 21
GATE_FIXED = "2026-09-28"
MIN_WEEKS = 52
MIN_XS = 60
MIN_XS_ORIG = 20
OVERLAP = 21.0 / 5.0
Z_CONSISTENT = -2.0
CUSUM_H = 5.0
PERSISTS, ACCUMULATING, FAILED = "EFFECT PERSISTS LIVE (research only)", "ACCUMULATING", "EFFECT NOT PERSISTING"


# ------------------------------------------------------------------ 1. freeze (once, from the stage-2 records)
def _digest(spec: dict) -> str:
    body = {k: v for k, v in spec.items() if k != "hash"}
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def freeze(store, research) -> dict:
    """Fit V* 1M on every matured stage-2 record with the last era's nested choice; keep the nodes a 1M score can use."""
    from . import alphahz as A
    res = store.kv_get(A.RESEARCH_KEY)
    if not res:
        raise RuntimeError("no stage-2 result (lab:alphahz): run `lab --alpha-horizons all` first")
    choice = (res.get("choices") or [{}])[-1].get(LAB) or {}
    depth, K = int(choice.get("depth", 3)), float(choice.get("K", 1000.0))
    data, names = A.load(store, research)
    p = len(names)
    W, rms = A.fit(A.leaf_stats(data, p, "2100-01-01"), p, K, depth)
    keep = {n: [round(v, 10) for v in w] for n, w in W.items()
            if n in ("global", f"g:{A.GROUP[LAB]}", f"h:{LAB}") or n.startswith(f"h:{LAB}|")}
    v = ((res.get("horizons") or {}).get(LAB) or {}).get("variants", {}).get("V*") or {}
    wf, orig = (v.get("walkforward") or {}).get("delta") or {}, (v.get("original_universe") or {}).get("delta") or {}
    recs = data.get(LAB) or []
    spec = {"id": "alpha-hz-1m-vstar", "lab": LAB, "horizon_sessions": H, "variant": "V*", "depth": depth, "K": K,
            "features": names, "rms": [round(x, 10) for x in rms], "weights": keep,
            "fitted": time.strftime("%Y-%m-%d"), "records": len(recs), "stocks": len({r.a for r in recs}),
            "last_record": max((r.d for r in recs), default=None), "choice_era": (res.get("choices") or [{}])[-1].get("era"),
            "backtest": {"d": wf.get("mean"), "t": wf.get("t"), "dates": wf.get("n"), "d_original": orig.get("mean"),
                         "t_original": orig.get("t"), "source": f"lab:alphahz {res.get('started')}"}}
    spec["hash"] = _digest(spec)
    return spec


def write_frozen(spec: dict, path: str = FROZEN_FILE):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(spec, f, sort_keys=True, indent=1)
        f.write("\n")


def load_frozen(path: str = FROZEN_FILE) -> Optional[dict]:
    """The frozen model, after checking its hash and that the family features still mean the same thing."""
    from . import alphahz as A
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        spec = json.load(f)
    if _digest(spec) != spec.get("hash"):
        raise ValueError(f"{path}: hash mismatch — the frozen 1M weights were edited")
    if spec["features"] != A.feature_names():
        raise ValueError(f"{path}: the family features changed since the freeze — a new model needs a new id and a fresh live clock")
    return spec


def version(spec: dict) -> str:
    return f"{spec['id']}@{spec['hash'][:12]}"


def score_x(spec: dict, sector: str, asset: str, x: List[Optional[float]]) -> Optional[float]:
    from . import alphahz as A
    xs = [v if v is not None else 0.0 for v in x]
    for node in reversed(A.path(LAB, sector, asset, spec["depth"])):
        w = spec["weights"].get(node)
        if w is not None:
            return sum(wi * xi / s for wi, xi, s in zip(w, xs, spec["rms"]) if s > 1e-9)
    return None


# ------------------------------------------------------------------ 2. the weekly ledger
def _week(d: str) -> str:
    y, w, _ = _dt.date.fromisoformat(d[:10]).isocalendar()
    return f"{y}-W{w:02d}"


def due(store, today: str) -> bool:
    return not any(p.get("week") == _week(today) for p in (store.kv_get(PANEL_KEY) or []))


def record(research, progress=None, force: bool = False, spec: Optional[dict] = None) -> dict:
    """Score every research stock with the frozen model (once a week) and append the rows to the ledger. Production's
    1M rows for the same date are added where its sweep is already cached (the live panel computes them)."""
    from . import alphahz as A
    from .tracking import record as put, record_shaffer
    say = progress or (lambda m: None)
    st = research.store
    spec = spec or load_frozen()
    if not spec:
        return {"skipped": "no frozen model (finsim2/frozen/alpha_hz_1m.json)"}
    panel = research.panel()
    cal = panel.calendar()
    t = len(cal) - 1
    today = cal[t]
    if not force and not due(st, today):
        return {"skipped": "already recorded this week", "date": today}
    fs = A.feature_spec()
    rows = []
    t0 = time.time()
    stocks = A._stocks(st)
    for k, a in enumerate(stocks):
        try:
            x = A.family_features(research.zscores(a["id"]), t, fs)
        except Exception:  # noqa: BLE001 — one stock's missing data never stops the panel
            x = None
        finally:
            A._forget(research, a["id"])
        if x is None or sum(v is not None for v in x) < A.MIN_PRESENT * len(x):
            continue
        s = score_x(spec, a.get("sector") or "Unclassified", a["id"], x)
        if s is not None:
            rows.append((a, s))
        if k % 50 == 0:
            say(f"1M alpha shadow {k + 1}/{len(stocks)} ({time.time() - t0:.0f}s)")
    order = sorted(range(len(rows)), key=lambda i: rows[i][1])
    pct = {rows[i][0]["id"]: (pos / (len(rows) - 1) if len(rows) > 1 else 0.5) for pos, i in enumerate(order)}
    n = n_prod = 0
    ver = version(spec)
    for a, s in rows:
        orig = not (a.get("meta") or {}).get("expanded")
        n += put(st, panel, a["id"], today, MODEL, ver, LAB, H, None, None, None, None,
                 {"pct": pct[a["id"]], "sector": a.get("sector"), "original": orig, "depth": spec["depth"], "hash": spec["hash"]},
                 source="shadow", raw=s) is not None
        try:
            if st.kv_get(research.shaffer_key(a["id"])) is not None:          # cached production sweep: cheap
                n_prod += record_shaffer(st, panel, a["id"], research.shaffer_full(a["id"]))
        except Exception:  # noqa: BLE001
            pass
    rec = {"date": today, "week": _week(today), "scored": len(rows), "rows": n, "production_rows": n_prod,
           "version": ver, "seconds": round(time.time() - t0, 1)}
    panels = [p for p in (st.kv_get(PANEL_KEY) or []) if p.get("date") != today] + [rec]
    st.kv_set(PANEL_KEY, sorted(panels, key=lambda p: p["date"]))
    st.audit("lab.alpha_1m_shadow", today, rec)
    return rec


# ------------------------------------------------------------------ 3. the statistic and 4. the gate
def weekly(store) -> List[dict]:
    from .livexs import rank_ic
    dates = {p["date"] for p in (store.kv_get(PANEL_KEY) or [])}
    ch: Dict[str, Dict[str, tuple]] = {}
    pr: Dict[str, Dict[str, float]] = {}
    for p in store.predictions(horizon=LAB):
        if p["made_on"] not in dates or p.get("realized") is None:
            continue
        if p["model"] == MODEL and p.get("raw") is not None:
            ch.setdefault(p["made_on"], {})[p["asset_id"]] = (p["raw"], p["realized"], bool((p.get("detail") or {}).get("original")))
        elif p["model"] == "shaffer":
            v = p.get("raw") if p.get("raw") is not None else p.get("score")
            if v is not None:
                pr.setdefault(p["made_on"], {})[p["asset_id"]] = v
    out = []
    for d in sorted(ch):
        c = ch[d]
        row = {"date": d, "n": len(c), "ic": rank_ic([v[0] for v in c.values()], [v[1] for v in c.values()])}
        common = [a for a in c if a in pr.get(d, {})]
        row["n_common"] = len(common)
        if len(common) >= MIN_XS:
            y = [c[a][1] for a in common]
            ic_c, ic_p = rank_ic([c[a][0] for a in common], y), rank_ic([pr[d][a] for a in common], y)
            if ic_c is not None and ic_p is not None:
                row.update({"ic_common": ic_c, "ic_prod": ic_p, "d": ic_c - ic_p})
        orig = [a for a in common if c[a][2]]
        if len(orig) >= MIN_XS_ORIG:
            y = [c[a][1] for a in orig]
            ic_c, ic_p = rank_ic([c[a][0] for a in orig], y), rank_ic([pr[d][a] for a in orig], y)
            if ic_c is not None and ic_p is not None:
                row["d_original"] = ic_c - ic_p
        out.append(row)
    return out


def _mt(xs: List[float]):
    n = len(xs)
    if n < 2:
        return (xs[0] if xs else None), None, None
    m = sum(xs) / n
    sd = math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1))
    return m, sd, (m / (sd / math.sqrt(n)) / math.sqrt(OVERLAP) if sd > 0 else None)     # overlap-adjusted


def expectation(spec: dict) -> dict:
    """The backtest's Δ and its per-date standard deviation (monthly, non-overlapping records)."""
    b = spec.get("backtest") or {}
    d, t, n = b.get("d"), b.get("t"), b.get("dates")
    sd = abs(d) * math.sqrt(n) / abs(t) if d and t and n else None
    return {"d": d, "sd_d": sd, "t": t, "dates": n, "d_original": b.get("d_original")}


def live_gate(store, spec: Optional[dict] = None) -> dict:
    from .livexs import cusum, superiority
    spec = spec or load_frozen() or {}
    rows = weekly(store)
    e = expectation(spec) if spec else {}
    ics = [r["ic"] for r in rows if r.get("ic") is not None]
    ds = [r["d"] for r in rows if r.get("d") is not None]
    dos = [r["d_original"] for r in rows if r.get("d_original") is not None]
    m_ic, _, t_ic = _mt(ics)
    m_d, sd_d, t_d = _mt(ds)
    m_o, _, t_o = _mt(dos)
    out = {"gate": f"1M live shadow (fixed {GATE_FIXED})", "model": spec.get("id"), "version": version(spec) if spec else None,
           "weeks_graded": len(rows), "weeks_compared": len(ds), "required": MIN_WEEKS, "mean_ic": m_ic, "t_ic": t_ic,
           "mean_d": m_d, "t_d": t_d, "mean_d_original": m_o, "t_d_original": t_o, "weeks_original": len(dos), "expectation": e,
           "last": rows[-1]["date"] if rows else None, "panels": (store.kv_get(PANEL_KEY) or [])[-12:]}
    sdd = e.get("sd_d") or sd_d
    if e.get("d") is not None and sdd and ds:
        out["z_consistency"] = (m_d - e["d"]) / (sdd * math.sqrt(OVERLAP) / math.sqrt(len(ds)))
        out["cusum"] = cusum(ds, 0.5 * e["d"], sdd * math.sqrt(OVERLAP))
    if e.get("d") and e.get("sd_d"):
        out["weeks_needed_vs_production"] = math.ceil(4.0 * e["sd_d"] ** 2 * OVERLAP / e["d"] ** 2)
    checks = {"weeks": len(ds) >= MIN_WEEKS,
              "edge": bool(m_ic is not None and m_ic > 0),
              "vs_production": bool(m_d is not None and m_d >= 0),
              "consistent": bool(out.get("z_consistency") is not None and out["z_consistency"] >= Z_CONSISTENT),
              "original_stocks": bool(m_o is not None and m_o >= 0),
              "no_decay_alarm": bool(out.get("cusum") is not None and out["cusum"]["fired_at_week"] is None)}
    out["checks"] = checks
    out["passed"] = all(checks.values())
    failed_early = bool(out.get("cusum") and out["cusum"]["fired_at_week"] is not None)
    out["label"] = PERSISTS if out["passed"] else (FAILED if failed_early or (checks["weeks"] and not out["passed"]) else ACCUMULATING)
    sup = superiority(ds)
    if sup.get("se"):                                               # the interval, overlap-adjusted
        se = sup["se"] * math.sqrt(OVERLAP)
        sup["se"], sup["ci95"] = se, [sup["mean"] - 1.96 * se, sup["mean"] + 1.96 * se]
        from .livexs import SUPERIOR, WORSE, UNRESOLVED
        sup["status"] = SUPERIOR if sup["ci95"][0] > 0 else (WORSE if sup["ci95"][1] < 0 else UNRESOLVED)
        sup["t"] = t_d
    out["superiority_vs_production"] = {**sup, "weeks_needed_95": out.get("weeks_needed_vs_production")}
    out["recent"] = rows[-12:]
    return out


def summary(store) -> dict:
    try:
        spec = load_frozen()
    except ValueError as e:
        return {"error": str(e)}
    if not spec:
        return {"error": "no frozen 1M model"}
    g = live_gate(store, spec)
    g["frozen"] = {k: spec.get(k) for k in ("id", "hash", "fitted", "records", "stocks", "last_record", "depth", "K", "choice_era", "backtest")}
    return g


def markdown(g: dict) -> str:
    def f(v, d=4):
        return "—" if v is None else f"{v:+.{d}f}"
    fr = g.get("frozen") or {}
    L = [f"1M Alpha challenger — live shadow ({g.get('gate')})", "",
         f"frozen {fr.get('id')} sha256 {str(fr.get('hash'))[:16]}… fitted {fr.get('fitted')} on {fr.get('records')} records "
         f"({fr.get('stocks')} stocks, depth {fr.get('depth')}, K {fr.get('K')}); backtest Δ {f((fr.get('backtest') or {}).get('d'))} "
         f"(t {f((fr.get('backtest') or {}).get('t'), 2)})",
         f"weeks graded {g.get('weeks_graded')} · compared with production {g.get('weeks_compared')} / {g.get('required')} needed",
         f"live IC {f(g.get('mean_ic'))} (t {f(g.get('t_ic'), 2)}) · live Δ {f(g.get('mean_d'))} (t {f(g.get('t_d'), 2)}) · "
         f"original stocks Δ {f(g.get('mean_d_original'))} ({g.get('weeks_original')} weeks) · z vs backtest {f(g.get('z_consistency'), 2)}",
         "checks: " + ", ".join(f"{k} {'✓' if v else '·'}" for k, v in (g.get("checks") or {}).items()),
         f"status: {g.get('label')} · vs production: {(g.get('superiority_vs_production') or {}).get('status')} "
         f"(weeks needed for a 95% answer at the backtest effect: {g.get('weeks_needed_vs_production')})"]
    for p in g.get("panels") or []:
        L.append(f"  panel {p['date']} ({p['week']}): {p['scored']} stocks scored, {p.get('production_rows', 0)} production rows added")
    return "\n".join(L) + "\n"
