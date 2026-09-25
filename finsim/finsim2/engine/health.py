"""Model health: is each engine still working, judged by its own out-of-sample record and by what happened live.

Engines (never combined): the Shaffer Score (per horizon), the ML return model, the ML risk models (volatility,
drawdown probability, tail loss, beta change), the Shaffer Hedge (the static sizing rule) and the hedge ML adjustment.

Evidence per engine:
  * walk-forward — the out-of-sample record the engine was built on (Shaffer: every cached asset's matured score
    history, pooled BY DATE exactly as the audit does, so correlated assets are not counted as independent tests;
    ML: each trained asset's walk-forward comparison with its baselines; hedge: the hedge audit's walk-forward);
  * live — forecasts recorded in the prediction ledger on the day they were made and graded when their horizon
    passed (expected vs realised), and graded hedge recommendations. Nothing is ever re-scored with hindsight.

Status (rules fixed in advance, first match wins):
  INSUFFICIENT DATA   fewer than 30 independent out-of-sample observations (or no trained model / audit)
  NO VERIFIED EDGE    the walk-forward record does not clear its own bar (Shaffer: date-clustered t < 2; ML: verified
                      against every baseline in fewer than 20% of the trained assets or fewer than 2 — one asset in
                      twenty passing is what chance alone produces; hedge: median variance reduction < 5%; hedge ML:
                      no group verified)
  DECAYING            it cleared the bar, but the last three years are at or below zero (or live IC ≤ 0 on ≥ 30
                      graded forecasts)
  WEAKENING           the last three years are below half of the full record (or verified but not stable across
                      halves, for the ML risk models)
  HEALTHY             everything else
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional

STATUSES = ["HEALTHY", "WEAKENING", "DECAYING", "NO VERIFIED EDGE", "INSUFFICIENT DATA"]
RECENT_YEARS = 3
MIN_NEFF = 30


def _corr(a: List[float], b: List[float]) -> Optional[float]:
    n = len(a)
    if n < 3:
        return None
    ma, mb = sum(a) / n, sum(b) / n
    sa = math.sqrt(sum((x - ma) ** 2 for x in a))
    sb = math.sqrt(sum((y - mb) ** 2 for y in b))
    if sa <= 0 or sb <= 0:
        return None
    return sum((x - ma) * (y - mb) for x, y in zip(a, b)) / (sa * sb)


def _week(iso: str) -> int:
    import datetime as _dt
    return _dt.date.fromisoformat(iso).toordinal() // 7


def clustered_ic(series: List[dict], h: int, since: Optional[str] = None) -> dict:
    """series: per asset {"dates", "x", "y"}. Standardise x and y within each asset, pool the products by week (the
    mean across assets each week), then the mean and its t with overlapping windows removed (the audit's method)."""
    by: Dict[int, List[float]] = {}
    for s in series:
        rows = [(d, x, y) for d, x, y in zip(s["dates"], s["x"], s["y"]) if x is not None and y is not None and (since is None or d >= since)]
        if len(rows) < 20:
            continue
        xs, ys = [r[1] for r in rows], [r[2] for r in rows]
        mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
        sx = math.sqrt(sum((v - mx) ** 2 for v in xs) / len(xs))
        sy = math.sqrt(sum((v - my) ** 2 for v in ys) / len(ys))
        if sx <= 1e-12 or sy <= 1e-12:
            continue
        for (d, x, y) in rows:
            by.setdefault(_week(d), []).append((x - mx) / sx * (y - my) / sy)
    if len(by) < 20:
        return {"ic": None, "t": None, "weeks": len(by), "n_eff": 0.0}
    ms = [sum(v) / len(v) for _, v in sorted(by.items())]
    n = len(ms)
    m = sum(ms) / n
    sd = math.sqrt(sum((v - m) ** 2 for v in ms) / max(1, n - 1))
    n_eff = n * 5.0 / max(5.0, float(h))
    return {"ic": m, "t": (m / (sd / math.sqrt(n_eff))) if sd > 0 and n_eff > 1 else None, "weeks": n, "n_eff": n_eff}


def classify(n_eff: float, verified: bool, full: Optional[float], recent: Optional[float], live_ic: Optional[float] = None,
             live_n: int = 0, stable: Optional[bool] = None) -> str:
    if n_eff < MIN_NEFF:
        return "INSUFFICIENT DATA"
    if not verified:
        return "NO VERIFIED EDGE"
    if (recent is not None and recent <= 0) or (live_ic is not None and live_n >= 30 and live_ic <= 0):
        return "DECAYING"
    if (recent is not None and full is not None and full > 0 and recent < 0.5 * full) or stable is False:
        return "WEAKENING"
    return "HEALTHY"


def live_record(store, model: str, horizon: Optional[str] = None) -> dict:
    """Expected vs realised for graded forecasts of one model (and horizon)."""
    rows = [p for p in store.predictions(horizon=horizon) if p["model"] == model and p.get("realized") is not None]
    out = {"graded": len(rows), "pending": sum(1 for p in store.predictions(horizon=horizon) if p["model"] == model and p.get("realized") is None)}
    if not rows:
        return out
    ref = [(p["predicted"] if p.get("predicted") is not None else p.get("score"), p["realized"]) for p in rows]
    ref = [(a, b) for a, b in ref if a is not None]
    graded = [p for p in rows if p.get("correct") is not None]
    out["hit"] = (sum(1 for p in graded if p["correct"]) / len(graded)) if graded else None
    out["ic"] = _corr([a for a, _ in ref], [b for _, b in ref]) if len(ref) >= 10 else None
    pr = [(p["predicted"], p["realized"]) for p in rows if p.get("predicted") is not None]
    if pr:
        out["mean_expected"] = sum(a for a, _ in pr) / len(pr)
        out["mean_realized"] = sum(b for _, b in pr) / len(pr)
        out["mae"] = sum(abs(a - b) for a, b in pr) / len(pr)
    if model == "ml_drawdown" and pr:
        base = sum(b for _, b in pr) / len(pr)
        out["brier"] = sum((a - b) ** 2 for a, b in pr) / len(pr)
        out["brier_base_rate"] = sum((base - b) ** 2 for _, b in pr) / len(pr)      # hindsight base rate: a generous yardstick
    inside = [p for p in rows if p.get("range_lo") is not None and p.get("range_hi") is not None]
    if inside:
        out["range_coverage"] = sum(1 for p in inside if p["range_lo"] <= p["realized"] <= p["range_hi"]) / len(inside)
    return out


# ------------------------------------------------------------------ engines
def shaffer_health(research) -> List[dict]:
    from .. import shaffer_score as cfg
    from .research import BUNDLE_VERSION
    st = research.store
    suffix = f":{research.version()}:{cfg.VERSION}:{BUNDLE_VERSION}"
    sums = [st.kv_get(k) for k in st.kv_keys("shaffer2:") if k.endswith(suffix)]
    sums = [s for s in sums if s]
    last = research.panel().calendar()[-1]
    since = f"{int(last[:4]) - RECENT_YEARS}{last[4:]}"
    out = []
    for lab, h in cfg.HORIZONS:
        if lab == "1D":
            continue
        ser = []
        for s in sums:
            hs = (s.get("history") or {}).get(lab) or {}
            if hs.get("dates"):
                ser.append({"dates": hs["dates"], "x": hs["raw"], "y": hs["realized"]})
        full = clustered_ic(ser, h)
        rec = clustered_ic(ser, h, since)
        live = live_record(st, "shaffer", lab)
        verified = (full["t"] or 0) >= 2
        status = classify(full["n_eff"], verified, full["ic"], rec["ic"], live.get("ic"), live.get("graded", 0))
        out.append({"engine": "Shaffer Score", "horizon": lab, "status": status, "assets": len(ser),
                    "walk_forward": {"ic": full["ic"], "t": full["t"], "n_eff": full["n_eff"], "recent_ic": rec["ic"], "recent_t": rec["t"],
                                     "recent_from": since},
                    "live": live, "bar": "date-clustered t ≥ 2 over the full out-of-sample record"})
    return out


ML_PROBLEMS = [("return", "ML return model"), ("volatility", "ML volatility model"), ("drawdown", "ML drawdown model"),
               ("tail_loss", "ML tail-loss model"), ("beta_change", "ML beta-change model")]


def ml_health(research) -> List[dict]:
    st = research.store
    res = [st.kv_get(k) for k in st.kv_keys("ml:")]
    res = [r for r in res if r and r.get("horizons")]
    labs = sorted({lab for r in res for lab in r["horizons"]}, key=lambda l: ["1D", "1W", "1M", "3M", "6M", "12M", "3Y", "5Y", "10Y"].index(l)
                  if l in ["1D", "1W", "1M", "3M", "6M", "12M", "3Y", "5Y", "10Y"] else 99)
    out = []
    for key, name in ML_PROBLEMS:
        for lab in labs:
            cases = verified = stable = decayed = 0
            n_eff = 0.0
            for r in res:
                hz = r["horizons"].get(lab) or {}
                if hz.get("status") != "ok":
                    continue
                if key == "return":
                    x = hz
                    ok = bool(hz.get("verified"))
                    st_ok = None
                    n_eff += (hz.get("ensemble") or {}).get("n_eff") or 0.0
                    decayed += 1 if ok and (hz.get("decay") or {}).get("flag") else 0
                else:
                    x = hz.get(key)
                    if not x:
                        continue
                    ok = bool(x.get("verified"))
                    st_ok = x.get("stable")
                    n_eff += ((x.get("model") or {}).get("n_eff") or x.get("n_eff") or ((x.get("n") or 0) * 5.0 / 21.0))
                cases += 1
                verified += ok
                stable += 1 if ok and st_ok else 0
            if not cases:
                continue
            edge = verified >= max(2, math.ceil(0.2 * cases))
            if key == "return":
                status = classify(n_eff, edge, None, None)
                if status == "HEALTHY" and decayed * 2 >= verified:
                    status = "DECAYING"
            else:
                status = classify(n_eff, edge, None, None, stable=(stable >= max(1, verified / 2)) if verified else None)
            live = live_record(st, {"return": "ml_ensemble", "volatility": "ml_vol", "drawdown": "ml_drawdown"}.get(key, ""), lab) \
                if key in ("return", "volatility", "drawdown") else {}
            out.append({"engine": name, "horizon": lab, "status": status, "assets": cases, "verified": verified,
                        "stable": stable if key != "return" else None, "decayed": decayed if key == "return" else None, "live": live,
                        "bar": "beats every naive baseline out of sample" + ("" if key == "return" else "; stable = in both halves")})
    return out


def hedge_health(research) -> List[dict]:
    from ..hedge.history import VERSION
    st = research.store
    out = []
    summ = st.kv_get("hedgeaudit:summary")
    graded = [x for x in st.hedges(limit=1000) if x.get("graded_on")]
    eff = [x["effectiveness"] for x in graded if x.get("effectiveness") is not None]
    live = {"graded": len(graded), "mean_effectiveness": (sum(eff) / len(eff)) if eff else None,
            "mean_realized_reduction": (sum(x["realized_reduction"] for x in graded if x.get("realized_reduction") is not None) /
                                        max(1, sum(1 for x in graded if x.get("realized_reduction") is not None))) if graded else None}
    if not summ:
        out.append({"engine": "Shaffer Hedge (static rule)", "horizon": "all", "status": "INSUFFICIENT DATA", "live": live,
                    "reason": "no hedge audit has been run (python -m finsim2 hedge-audit)"})
    else:
        for g in summ.get("groups") or []:
            med, early, recent = g.get("median_reduction"), g.get("median_early"), g.get("median_recent")
            verified = med is not None and med >= 0.05
            status = classify(g.get("n_eff") or 0.0, verified, early, recent)
            out.append({"engine": "Shaffer Hedge (static rule)", "horizon": g["group"], "status": status, "assets": g.get("cases"),
                        "walk_forward": {"median_reduction": med, "early_third": early, "recent_third": recent, "n_eff": g.get("n_eff"),
                                         "tail_reduction": g.get("median_tail")},
                        "live": live, "bar": "median realised variance reduction ≥ 5%; recent third vs early third"})
    models = (st.kv_get(f"hedgeml:{VERSION}") or {}).get("groups") or {}
    per: Dict[str, List[int]] = {}
    for g, res in models.items():
        for mt, r in res.items():
            if r.get("n"):
                c = per.setdefault(mt, [0, 0, 0])
                c[0] += 1; c[1] += 1 if r.get("verified") else 0; c[2] += int(r.get("n_eff") or 0)
    if not per:
        out.append({"engine": "Hedge ML adjustment", "horizon": "all", "status": "INSUFFICIENT DATA",
                    "reason": "no objective-specific hedge models trained yet (python -m finsim2 hedge-audit)"})
    for mt, (n, v, ne) in per.items():
        out.append({"engine": "Hedge ML adjustment", "horizon": mt, "status": classify(ne, v > 0, None, None), "groups": n, "verified": v,
                    "bar": "beats the static rule and the minimum-variance multiple out of sample"})
    return out


def report(research) -> dict:
    rows = []
    for fn in (shaffer_health, ml_health, hedge_health):
        try:
            rows.extend(fn(research))
        except Exception as e:  # noqa: BLE001 - one engine's failure must not hide the others
            rows.append({"engine": fn.__name__.replace("_health", ""), "horizon": "—", "status": "INSUFFICIENT DATA",
                         "reason": f"health check failed: {type(e).__name__}: {e}"})
    counts = {s: sum(1 for r in rows if r["status"] == s) for s in STATUSES}
    return {"asof": research.panel().calendar()[-1], "rows": rows, "counts": counts, "rules": __doc__.split("Status (rules fixed in advance, first match wins):")[1].strip()}


def hedge_audit_summary(audit_result: dict) -> dict:
    """Compact per-risk walk-forward summary of a hedge audit, stored in kv `hedgeaudit:summary` for the dashboard."""
    groups: Dict[str, dict] = {}
    med = lambda xs: sorted(xs)[len(xs) // 2] if xs else None
    for c in audit_result.get("cases") or []:
        r = c.get("result") or {}
        if not r.get("n"):
            continue
        o = c.get("objective") or ""
        g = ("Currency" if o.startswith("fx:") else "Commodity" if o.startswith("commodity") else
             {"equity": "Equity beta", "name": "Single name", "rates": "Rates (DV01)", "credit": "Credit (CS01)", "crypto": "Crypto"}.get(o, o))
        b = groups.setdefault(g, {"group": g, "red": [], "early": [], "recent": [], "tail": [], "n_eff": 0.0, "cases": 0})
        b["cases"] += 1
        b["n_eff"] += r.get("n_eff") or 0.0
        for k, src in (("red", r.get("realized_reduction")), ("early", r.get("early_third")), ("recent", r.get("recent_third")),
                       ("tail", (r.get("tail") or {}).get("reduction"))):
            if src is not None:
                b[k].append(src)
    return {"asof": audit_result.get("asof"), "groups": [{"group": g, "cases": b["cases"], "n_eff": b["n_eff"], "median_reduction": med(b["red"]),
                                                          "median_early": med(b["early"]), "median_recent": med(b["recent"]), "median_tail": med(b["tail"])}
                                                         for g, b in groups.items()]}
