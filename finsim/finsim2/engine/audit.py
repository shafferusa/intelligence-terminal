"""The Shaffer Score / ML audit: run the point-in-time engines over the universe and answer, from the out-of-sample
record, whether they work. Two passes: (1) every asset's January evidence checkpoints (its own sums, independent of
any prior), (2) the full point-in-time Shaffer sweep with the class/global priors those checkpoints give, cached for
the UI; then ML v2 on a representative cross-asset set; then the aggregates behind the 26 audit questions.

    python -m finsim2 audit [--workers 3] [--ml SPY,QQQ,...]
"""
from __future__ import annotations

import math
import os
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from typing import Dict, List, Optional

from .. import shaffer_score as cfg

ML_SET = ["SPY", "QQQ", "IWM", "EFA", "EEM", "TLT", "IEF", "LQD", "HYG", "GOLD", "WTI", "COPPER", "EURUSD", "USDJPY", "DXY",
          "BTC", "AAPL", "NVDA", "JPM", "XOM", "JNJ"]
BANDS = [(-100, -60), (-60, -40), (-40, -20), (-20, -5), (-5, 5), (5, 20), (20, 40), (40, 60), (60, 100)]


def _spearman(a: List[float], b: List[float]) -> Optional[float]:
    from .models import spearman
    return spearman(a, b) if len(a) >= 10 and len(set(a)) > 1 else None


def _pearson(a: List[float], b: List[float]) -> Optional[float]:
    n = len(a)
    if n < 3:
        return None
    ma, mb = sum(a) / n, sum(b) / n
    va = sum((x - ma) ** 2 for x in a)
    vb = sum((y - mb) ** 2 for y in b)
    if va <= 1e-15 or vb <= 1e-15:
        return None
    return sum((x - ma) * (y - mb) for x, y in zip(a, b)) / math.sqrt(va * vb)


def _partial(x: List[float], y: List[float], z: List[float]) -> Optional[float]:
    """Correlation of x and y after removing their linear relation to z (the rest of the score)."""
    rxy, rxz, ryz = _pearson(x, y), _pearson(x, z), _pearson(y, z)
    if rxy is None:
        return None
    if rxz is None or ryz is None:
        return rxy
    den = math.sqrt(max(1e-12, (1 - rxz * rxz) * (1 - ryz * ryz)))
    return (rxy - rxz * ryz) / den


def _tstat(r, n_eff):
    if r is None or n_eff <= 3:
        return None
    return r * math.sqrt(n_eff - 2) / math.sqrt(max(1e-12, 1 - r * r))


# ------------------------------------------------------------------ worker functions (each in its own process: own Store and Research)
def _open(db_path):
    from ..data.store import Store
    from .research import Research
    st = Store(db_path)
    return st, Research(st)


def _checkpoint_worker(db_path: str, asset_id: str) -> tuple:
    from .shaffer import ShafferRun
    st, r = _open(db_path)
    try:
        t0 = time.time()
        ShafferRun(r, asset_id, use_priors=False).run(checkpoints_only=True)
        return asset_id, "ok", round(time.time() - t0, 1)
    except Exception as e:
        return asset_id, f"failed: {type(e).__name__}: {e}", 0.0
    finally:
        st.close()


def _shaffer_worker(db_path: str, asset_id: str) -> dict:
    """Full point-in-time run with priors; caches the summary for the UI; returns what the audit aggregates."""
    from .research import BUNDLE_VERSION, clean
    from .shaffer import ShafferRun, summarize
    st, r = _open(db_path)
    try:
        t0 = time.time()
        run = ShafferRun(r, asset_id)
        res = run.run()
        summ = clean(summarize(res, run))
        st.kv_set(f"shaffer2:{asset_id}:{r.version()}:{cfg.VERSION}:{BUNDLE_VERSION}", summ)
        out = {"asset_id": asset_id, "class": run.cls, "seconds": round(time.time() - t0, 1), "history_years": summ.get("history_years"),
               "performance": summ["performance"], "calibration": {k: {kk: v.get(kk) for kk in ("ic", "t", "n_eff", "monotonicity", "usable")} if v else None
                                                                   for k, v in summ["calibration"].items()},
               "live": {k: {kk: v.get(kk) for kk in ("raw", "calibrated", "expected", "confidence", "evidence", "n_eff", "reason")} for k, v in summ["horizons"].items()},
               "validation": summ["validation"], "records": {}, "fam": {}, "decay": {}, "status_counts": {}}
        # per horizon: realised returns by score band, and each family's own and incremental out-of-sample IC
        for lab, h in run.horizons:
            realized = {p[3]: (p[2], p[1]) for p in res["oos"][lab]}
            recs = [(tau, raw, fam, realized[tau]) for (tau, raw, _c, _e), (_, fam) in zip(res["history"][lab], run.fam_records[lab]) if tau in realized]
            bands = {f"{lo}..{hi}": [] for lo, hi in BANDS}
            for tau, raw, fam, (yr, yv) in recs:
                for lo, hi in BANDS:
                    if lo <= raw < hi or (hi == 100 and raw == 100):
                        bands[f"{lo}..{hi}"].append(yr)
                        break
            out["records"][lab] = {"n": len(recs), "bands": bands}
            if len(recs) >= 30:                  # for the date-clustered t of the production score's IC
                pr = _std_products([x[1] for x in recs], [x[3][1] for x in recs], [0.0] * len(recs))
                out.setdefault("score_prods", {})[lab] = {"weeks": [x[0] // 5 for x in recs], "prods": pr} if pr else None
            fams = sorted({f for _, _, fam, _ in recs for f in fam})
            fstats = {}
            for f in fams:
                xs = [fam[f][0] for _, _, fam, _ in recs if f in fam]
                ys = [yv for _, _, fam, (yr, yv) in recs if f in fam]
                others = [sum(v[1] for g, v in fam.items() if g != f) for _, _, fam, _ in recs if f in fam]
                if len(xs) < 30 or len(set(xs)) < 3:
                    continue
                own = _pearson(xs, ys)
                part = _partial(xs, ys, others)
                n_eff = len(xs) * 5.0 / max(5.0, float(h))
                fstats[f] = {"ic": own, "partial": part, "n_eff": n_eff, "t": _tstat(own, n_eff), "t_partial": _tstat(part, n_eff)}
                pr = _std_products(xs, ys, others)
                if pr:
                    fstats[f]["weeks"] = [tau // 5 for tau, _, fam, _ in recs if f in fam]
                    fstats[f]["prods"] = pr
            # influence: each family's share of |contribution| in each record, and its validation multiplier V, split
            # at CONFIRM_FROM (does V shrink a family whose out-of-sample record is negative?)
            infl: Dict[str, dict] = {}
            for tau, _, fam, _ in recs:
                tot = sum(abs(v[1]) for v in fam.values())
                if tot <= 0:
                    continue
                part = "decide" if run.cal[tau] < CONFIRM_FROM else "confirm"
                for f, v in fam.items():
                    b = infl.setdefault(f, {}).setdefault(part, [0, 0.0, 0.0])
                    b[0] += 1; b[1] += abs(v[1]) / tot; b[2] += v[2] if len(v) > 2 else 1.0
            for f, parts in infl.items():
                if f in fstats:
                    fstats[f]["influence"] = {p: {"n": b[0], "share": b[1] / b[0], "V": b[2] / b[0]} for p, b in parts.items()}
            out["fam"][lab] = fstats
            out.setdefault("candidates", {})[lab] = candidate_stats(res.get("shadow", {}).get(lab) or [], h, run.cal)
            ev = res["evidence"].get(lab) or {}
            out["decay"][lab] = {sg: e.get("decay") for sg, e in ev.items() if e.get("decay") in ("DECAYING", "WEAKENING", "HEALTHY")}
            cnt = {}
            for e in ev.values():
                cnt[e.get("status", "?")] = cnt.get(e.get("status", "?"), 0) + 1
            out["status_counts"][lab] = cnt
        out["sign_checks"] = run.sign_checks
        from .lab import save_records
        out["lab_records"] = save_records(st, run, r.version())
        first = next((i for i, v in enumerate(run.price) if v is not None), 0)
        out["first_price"] = run.cal[first]
        out["first_score"] = {lab: (run.cal[res["history"][lab][0][0]] if res["history"][lab] else None) for lab, _ in run.horizons}
        return out
    except Exception as e:
        return {"asset_id": asset_id, "error": f"{type(e).__name__}: {e}"}
    finally:
        st.close()


def _mono(xs: List[float], ys: List[float], k: int = 10) -> Optional[float]:
    """Monotonicity of mean outcome across k score-ordered bins: Spearman of bin rank vs bin mean (1 = monotone)."""
    if len(xs) < 5 * k:
        return None
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    size = len(order) // k
    means = [sum(ys[i] for i in order[j * size:(j + 1) * size]) / size for j in range(k)]
    return _spearman(list(range(k)), means)


CONFIRM_FROM = "2018-01-01"   # common split date ≈ the last third of the pooled 2001-2026 record: before = decide, from = confirm


def _resid(x: List[float], z: List[float]) -> List[float]:
    n = len(x)
    mx, mz = sum(x) / n, sum(z) / n
    vz = sum((b - mz) ** 2 for b in z)
    beta = (sum((a - mx) * (b - mz) for a, b in zip(x, z)) / vz) if vz > 1e-15 else 0.0
    return [(a - mx) - beta * (b - mz) for a, b in zip(x, z)]


def _std_products(x: List[float], y: List[float], z: List[float]) -> Optional[List[float]]:
    """Products of the standardised residuals of x and y on z: their mean is the partial correlation, and the
    per-date values let the audit pool assets by date (cross-sectional dependence) instead of treating them as
    independent."""
    rx, ry = _resid(x, z), _resid(y, z)
    n = len(rx)
    sx, sy = math.sqrt(sum(a * a for a in rx) / n), math.sqrt(sum(b * b for b in ry) / n)
    if sx <= 1e-12 or sy <= 1e-12:
        return None
    return [round(a / sx * b / sy, 4) for a, b in zip(rx, ry)]


def candidate_stats(rows: List[tuple], h: int, cal: List[str]) -> dict:
    """Out-of-sample evidence for each shadow family at one horizon for one asset. rows = (t, y, raw, raw_with_all,
    {family: (family score, raw with that family)}), matured score records in time order. The record is split at a
    common date (CONFIRM_FROM): before it decides, from it confirms (an admission decided on the whole record would
    itself be in-sample selection). Per part: the family's own IC, its partial IC given the production score (with the
    per-date standardised products for date-clustered pooling), the score's IC with and without the family, and
    calibration monotonicity with and without."""
    out = {}
    if len(rows) < 60:
        return out
    fams = sorted({f for r in rows for f in r[4]})
    for f in fams + ["ALL"]:
        res = {}
        for part in ("decide", "confirm"):
            sub = [r for r in rows if (cal[r[0]] < CONFIRM_FROM) == (part == "decide")]
            if f == "ALL":
                use = [(r[0], r[1], r[2], r[3], r[3]) for r in sub if r[3] is not None]
            else:
                use = [(r[0], r[1], r[2], r[4][f][0], r[4][f][1]) for r in sub if f in r[4]]
            if len(use) < 30:
                continue
            ts, ys, raws, fs, withs = [u[0] for u in use], [u[1] for u in use], [u[2] for u in use], [u[3] for u in use], [u[4] for u in use]
            n_eff = len(use) * 5.0 / max(5.0, float(h))
            varies = f != "ALL" and len(set(fs)) > 2
            own = _pearson(fs, ys) if varies else None
            part_ic = _partial(fs, ys, raws) if varies else None
            ic0, ic1 = _pearson(raws, ys), _pearson(withs, ys)
            prods = _std_products(fs, ys, raws) if varies else None
            res[part] = {"n": len(use), "n_eff": n_eff, "own": own, "t_own": _tstat(own, n_eff), "partial": part_ic, "t_partial": _tstat(part_ic, n_eff),
                         "ic_without": ic0, "ic_with": ic1, "mono_without": _mono(raws, ys), "mono_with": _mono(withs, ys),
                         "weeks": [t // 5 for t in ts] if prods else None, "prods": prods}
        if res:
            out[f] = res
    return out


def _influence(xs: List[dict]) -> dict:
    """Record-weighted mean influence share and V across assets, per part (decide / confirm)."""
    out = {}
    for part in ("decide", "confirm"):
        ps = [x["influence"][part] for x in xs if (x.get("influence") or {}).get(part)]
        n = sum(p["n"] for p in ps)
        if n:
            out[part] = {"n": n, "share": sum(p["share"] * p["n"] for p in ps) / n, "V": sum(p["V"] * p["n"] for p in ps) / n}
    return out


def _risk_brief(x: Optional[dict]) -> Optional[dict]:
    """A risk-model comparison (ml._risk_compare) reduced to what the audit aggregates."""
    if not x or not x.get("model"):
        return None
    base = {k[9:]: (v or {}).get("rmse") for k, v in x.items() if k.startswith("baseline_")}
    return {"verified": x.get("verified"), "stable": x.get("stable"), "rmse": x["model"].get("rmse"), "ic": x["model"].get("ic"),
            "baselines": base, "improvement": x.get("rmse_improvement")}


def _ml_worker(db_path: str, asset_id: str) -> dict:
    from . import ml
    st, r = _open(db_path)
    try:
        t0 = time.time()
        res = ml.train_asset(r, asset_id)
        hz = {}
        for lab, o in res["horizons"].items():
            if not o.get("baselines"):
                continue
            hz[lab] = {"status": o.get("status"), "verified": o.get("verified"), "reasons": o.get("edge_reasons") or ([o.get("reason")] if o.get("reason") else []),
                       "ensemble": {k: (o.get("ensemble") or {}).get(k) for k in ("ic", "t", "n_eff", "permutation_p", "r2_vs_mean")},
                       "holdout": ((o.get("ensemble") or {}).get("holdout") or {}).get("ic"),
                       "baselines": {k: {"ic": v.get("ic"), "same": v.get("ensemble_ic_same_rows"), "n_eff": v.get("n_eff")} for k, v in o["baselines"].items()},
                       "direction": {k: (o.get("direction") or {}).get(k) for k in ("auc", "brier", "baseline_brier", "verified", "accuracy")},
                       "volatility": {"verified": (o.get("volatility") or {}).get("verified"), "rmse": ((o.get("volatility") or {}).get("model") or {}).get("rmse"),
                                      "rmse_current": ((o.get("volatility") or {}).get("baseline_current_vol") or {}).get("rmse"),
                                      "rmse_ewma": ((o.get("volatility") or {}).get("baseline_ewma") or {}).get("rmse")} if o.get("volatility") else None,
                       "drawdown": {k: (o.get("drawdown") or {}).get(k) for k in ("brier", "baseline_brier", "verified", "stable", "auc", "threshold")} if o.get("drawdown") else None,
                       "risk": {k: _risk_brief(o.get(k)) for k in ("volatility", "tail_loss", "beta_change")},
                       "oos": o.get("oos") or []}
        for lab, v in hz.items():
            try:
                ss = r.shaffer_series(asset_id, lab)
            except Exception:
                ss = None
            v["combined"] = _combined(v.pop("oos"), ss) if ss else None
        return {"asset_id": asset_id, "seconds": round(time.time() - t0, 1), "horizons": hz}
    except Exception as e:
        return {"asset_id": asset_id, "error": f"{type(e).__name__}: {e}"}
    finally:
        st.close()


def _expanding_z(xs: List[float]) -> List[Optional[float]]:
    out, s, ss = [], 0.0, 0.0
    for k, x in enumerate(xs):
        if k >= 20:
            m = s / k
            sd = math.sqrt(max(1e-12, ss / k - m * m))
            out.append((x - m) / sd)
        else:
            out.append(None)
        s += x; ss += x * x
    return out


def _combined(oos: List[tuple], ss: List[Optional[float]]) -> Optional[dict]:
    """Combined = α·Shaffer + (1 − α)·ML on standardised (expanding, past-only) forecasts: α chosen on the first half of
    the common out-of-sample period, then judged on the untouched second half against each alone."""
    rows = [(i, p, y, ss[i]) for i, p, y in oos if i < len(ss) and ss[i] is not None]
    if len(rows) < 80:
        return None
    zp = _expanding_z([r[1] for r in rows])
    zs = _expanding_z([r[3] for r in rows])
    rows = [(r[2], a, b) for r, a, b in zip(rows, zp, zs) if a is not None and b is not None]
    half = len(rows) // 2
    first, second = rows[:half], rows[half:]
    grid = [k / 10 for k in range(11)]
    best = max(grid, key=lambda al: _spearman([al * b + (1 - al) * a for _, a, b in first], [y for y, _, _ in first]) or -9)
    ys = [y for y, _, _ in second]
    return {"alpha": best, "n_test": len(second),
            "ic_combined": _spearman([best * b + (1 - best) * a for _, a, b in second], ys),
            "ic_shaffer": _spearman([b for _, _, b in second], ys), "ic_ml": _spearman([a for _, a, _ in second], ys)}


# ------------------------------------------------------------------ orchestration
def run_universe(db_path: str, assets: Optional[List[str]] = None, ml_assets: Optional[List[str]] = None, workers: int = 3,
                 progress=None, skip_ml: bool = False) -> dict:
    from ..data.store import Store
    st = Store(db_path)
    ids = assets or [a["id"] for a in st.assets() if st.price_count(a["id"]) >= 252 * 6]
    st.close()
    say = progress or (lambda m: None)
    t0 = time.time()
    out = {"started": time.strftime("%Y-%m-%d %H:%M:%S"), "assets": ids, "checkpoints": {}, "shaffer": {}, "ml": {}}
    with ProcessPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(_checkpoint_worker, db_path, a) for a in ids]
        for k, f in enumerate(as_completed(futs)):
            a, status, secs = f.result()
            out["checkpoints"][a] = status
            say(f"[1/3 checkpoints {k + 1}/{len(ids)}] {a}: {status} ({secs}s)")
    with ProcessPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(_shaffer_worker, db_path, a) for a in ids]
        for k, f in enumerate(as_completed(futs)):
            r = f.result()
            out["shaffer"][r["asset_id"]] = r
            say(f"[2/3 Shaffer {k + 1}/{len(ids)}] {r['asset_id']}: {r.get('error') or str(r.get('seconds')) + 's'}")
    mls = [] if skip_ml else [a for a in (ml_assets or ML_SET) if a in ids]
    with ProcessPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(_ml_worker, db_path, a) for a in mls]
        for k, f in enumerate(as_completed(futs)):
            r = f.result()
            out["ml"][r["asset_id"]] = r
            say(f"[3/3 ML {k + 1}/{len(mls)}] {r['asset_id']}: {r.get('error') or str(r.get('seconds')) + 's'}")
    out["seconds"] = round(time.time() - t0, 1)
    return out



# ------------------------------------------------------------------ the report
def _fmt(x, d=3):
    return "—" if x is None else (f"{x:+.{d}f}" if isinstance(x, float) and d and x != 0 else f"{x:.{d}f}" if isinstance(x, float) else str(x))


def _pct(x, d=1):
    return "—" if x is None else f"{100 * x:.{d}f}%"


def _stouffer(ts: List[float]) -> Optional[float]:
    ts = [t for t in ts if t is not None]
    return sum(ts) / math.sqrt(len(ts)) if ts else None


ADMIT_T_DECIDE = 2.0      # date-clustered t of the pooled incremental IC before CONFIRM_FROM
ADMIT_T_CONFIRM = 1.0     # and a positive, at least weakly significant, pooled incremental IC from CONFIRM_FROM on
ADMIT_MIN_ASSETS = 5
ADMIT_MIN_SHARE = 0.5     # a majority of assets with a positive incremental IC on the confirmation part
ADMIT_MONO_SLACK = 0.05


def _clustered(xs: List[dict], h: int) -> tuple:
    """Pool per-asset standardised products by week (assets that move together are not independent evidence):
    mean over assets each week, then the mean of that weekly series and its t with overlapping windows removed."""
    by: Dict[int, List[float]] = {}
    for x in xs:
        for w, p in zip(x.get("weeks") or [], x.get("prods") or []):
            by.setdefault(w, []).append(p)
    if len(by) < 20:
        return None, None, 0
    ms = [sum(v) / len(v) for _, v in sorted(by.items())]
    n = len(ms)
    m = sum(ms) / n
    sd = math.sqrt(sum((v - m) ** 2 for v in ms) / max(1, n - 1))
    n_eff = n * 5.0 / max(5.0, float(h))
    return m, (m / (sd / math.sqrt(n_eff)) if sd > 0 and n_eff > 1 else None), n


def candidate_verdicts(per_asset: List[dict], h: int = 21) -> dict:
    """Pool each candidate family's per-asset statistics (by date, not as independent assets) and apply the rule."""
    out = {}
    fams = sorted({f for d in per_asset for f in d})
    for f in fams:
        rec = {}
        for part in ("decide", "confirm"):
            xs = [d[f][part] for d in per_asset if f in d and part in d[f]]
            if not xs:
                continue
            W = sum(x["n_eff"] for x in xs)
            wmean = lambda k: (sum((x[k] or 0.0) * x["n_eff"] for x in xs if x[k] is not None) / max(1e-9, sum(x["n_eff"] for x in xs if x[k] is not None))) if any(x[k] is not None for x in xs) else None
            cm, ct, weeks = _clustered(xs, h)
            parts = [x["partial"] for x in xs if x.get("partial") is not None]
            rec[part] = {"assets": len(xs), "n_eff": W, "own": wmean("own"), "t_own": _stouffer([x["t_own"] for x in xs]),
                         "partial": cm if cm is not None else wmean("partial"), "t_partial": ct, "t_stouffer": _stouffer([x["t_partial"] for x in xs]),
                         "weeks": weeks, "share_positive": (sum(1 for v in parts if v > 0) / len(parts)) if parts else None,
                         "ic_without": wmean("ic_without"), "ic_with": wmean("ic_with"),
                         "d_ic": (wmean("ic_with") - wmean("ic_without")) if wmean("ic_with") is not None and wmean("ic_without") is not None else None,
                         "mono_without": wmean("mono_without"), "mono_with": wmean("mono_with")}
        d, c = rec.get("decide") or {}, rec.get("confirm") or {}
        why = []
        if f != "ALL":
            if (d.get("assets") or 0) < ADMIT_MIN_ASSETS:
                why.append(f"only {d.get('assets') or 0} assets with enough history")
            if (d.get("t_partial") or 0.0) < ADMIT_T_DECIDE:
                why.append(f"incremental IC not significant before {CONFIRM_FROM[:4]} (date-clustered t {_fmt(d.get('t_partial'), 1)})")
            if (c.get("partial") or 0.0) <= 0 or (c.get("t_partial") or 0.0) < ADMIT_T_CONFIRM:
                why.append(f"not confirmed from {CONFIRM_FROM[:4]} (incremental IC {_fmt(c.get('partial'))}, t {_fmt(c.get('t_partial'), 1)})")
            if (c.get("share_positive") or 0.0) < ADMIT_MIN_SHARE:
                why.append(f"positive in only {_pct(c.get('share_positive'), 0)} of assets from {CONFIRM_FROM[:4]}")
            if c.get("d_ic") is not None and c["d_ic"] < 0:
                why.append(f"the score's IC falls with it from {CONFIRM_FROM[:4]} ({_fmt(c['d_ic'])})")
            if c.get("mono_with") is not None and c.get("mono_without") is not None and c["mono_with"] < c["mono_without"] - ADMIT_MONO_SLACK:
                why.append("calibration less monotone with it")
        rec["verdict"] = "ADMIT" if f != "ALL" and not why else ("—" if f == "ALL" else "REJECT")
        rec["reasons"] = why
        out[f] = rec
    return out


def aggregates(u: dict) -> dict:
    S = {a: v for a, v in u["shaffer"].items() if not v.get("error")}
    labs = [lab for lab, _ in cfg.HORIZONS]
    hmap = dict(cfg.HORIZONS)
    agg = {"n_assets": len(S), "errors": {a: v["error"] for a, v in u["shaffer"].items() if v.get("error")}, "by_horizon": {}, "by_class": {},
           "by_regime": {}, "bands": {}, "families": {}, "decay": {}, "history": [], "ml": {}, "combined": {}, "status": {}}
    for a, v in sorted(S.items()):
        agg["history"].append({"asset": a, "class": v["class"], "first_price": v.get("first_price"), "years": v.get("history_years"),
                               "first_score_3M": (v.get("first_score") or {}).get("3M"), "oos_years_3M": (v["performance"].get("3M") or {}).get("years")})
    for lab in labs:
        h = hmap[lab]
        perf = [(a, v["class"], v["performance"].get(lab) or {}) for a, v in S.items()]
        ok = [(a, c, p) for a, c, p in perf if p.get("ic") is not None]
        if not ok:
            agg["by_horizon"][lab] = {"assets": 0}
            continue
        w = [p["n_eff"] for _, _, p in ok]
        ics = [p["ic"] for _, _, p in ok]
        hits = [(p["hit"], p.get("n_lean") or 0) for _, _, p in ok if p.get("hit") is not None]
        agg["by_horizon"][lab] = {"assets": len(ok), "ic_weighted": sum(i * n for i, n in zip(ics, w)) / sum(w), "ic_median": sorted(ics)[len(ics) // 2],
                                  "share_positive": sum(1 for i in ics if i > 0) / len(ics), "t_combined": _stouffer([p.get("t") for _, _, p in ok]),
                                  "hit": (sum(hh * n for hh, n in hits) / sum(n for _, n in hits)) if hits and sum(n for _, n in hits) else None,
                                  "n_eff_total": sum(w), "n_eff_median": sorted(w)[len(w) // 2],
                                  "t_clustered": _clustered([p for p in ((v.get("score_prods") or {}).get(lab) for v in S.values()) if p], h)[1]}
        cls = {}
        for a, c, p in ok:
            cls.setdefault(c, []).append(p)
        agg["by_class"][lab] = {c: {"assets": len(ps), "ic": sum(p["ic"] * p["n_eff"] for p in ps) / sum(p["n_eff"] for p in ps),
                                    "share_positive": sum(1 for p in ps if p["ic"] > 0) / len(ps),
                                    "hit": (sum(p["hit"] for p in ps if p.get("hit") is not None) / max(1, sum(1 for p in ps if p.get("hit") is not None))) if any(p.get("hit") is not None for p in ps) else None}
                                for c, ps in cls.items()}
        reg = {}
        for a, c, p in ok:
            for st, r in (p.get("by_regime") or {}).items():
                if r.get("ic") is not None:
                    reg.setdefault(st, []).append((r["ic"], r["n_eff"], r.get("hit")))
        agg["by_regime"][lab] = {st: {"assets": len(xs), "ic": sum(i * n for i, n, _ in xs) / sum(n for _, n, _ in xs),
                                      "hit": (sum(hh for _, _, hh in xs if hh is not None) / max(1, sum(1 for _, _, hh in xs if hh is not None))) if any(hh is not None for _, _, hh in xs) else None}
                                 for st, xs in reg.items()}
        # score bands pooled across assets
        bands = {}
        for lo, hi in BANDS:
            key = f"{lo}..{hi}"
            ys = [y for v in S.values() for y in ((v["records"].get(lab) or {}).get("bands") or {}).get(key, [])]
            if not ys:
                bands[key] = {"n": 0}
                continue
            n = len(ys)
            m = sum(ys) / n
            sd = math.sqrt(sum((y - m) ** 2 for y in ys) / max(1, n - 1))
            n_eff = n * 5.0 / max(5.0, float(h))
            ys_sorted = sorted(ys)
            bands[key] = {"n": n, "n_eff": n_eff, "mean": math.exp(m) - 1, "median": math.exp(ys_sorted[n // 2]) - 1, "hit": sum(1 for y in ys if y > 0) / n,
                          "vol": sd, "ci": (math.exp(m - 1.96 * sd / math.sqrt(max(1.0, n_eff))) - 1, math.exp(m + 1.96 * sd / math.sqrt(max(1.0, n_eff))) - 1)}
        agg["bands"][lab] = bands
        # families pooled
        fam = {}
        for v in S.values():
            for f, st in (v["fam"].get(lab) or {}).items():
                fam.setdefault(f, []).append(st)
        agg["families"][lab] = {f: {"assets": len(xs), "ic": sum((x["ic"] or 0) * x["n_eff"] for x in xs) / max(1e-9, sum(x["n_eff"] for x in xs)),
                                    "partial": sum((x["partial"] or 0) * x["n_eff"] for x in xs) / max(1e-9, sum(x["n_eff"] for x in xs)),
                                    "t": _stouffer([x["t"] for x in xs]), "t_partial": _stouffer([x["t_partial"] for x in xs]),
                                    "t_partial_clustered": _clustered([x for x in xs if x.get("prods")], h)[1],
                                    "influence": _influence(xs)} for f, xs in fam.items()}
        agg.setdefault("candidates", {})[lab] = candidate_verdicts([(v.get("candidates") or {}).get(lab) or {} for v in S.values()], h)
        dec = {}
        for v in S.values():
            for sg, dc in (v["decay"].get(lab) or {}).items():
                d = dec.setdefault(sg, {"DECAYING": 0, "WEAKENING": 0, "HEALTHY": 0})
                d[dc] += 1
        agg["decay"][lab] = dec
        st = {}
        for v in S.values():
            for k, c in (v["status_counts"].get(lab) or {}).items():
                st[k] = st.get(k, 0) + c
        agg["status"][lab] = st
        # ML v2
        mls = [(a, (m.get("horizons") or {}).get(lab)) for a, m in u["ml"].items() if not m.get("error")]
        mls = [(a, x) for a, x in mls if x]
        if mls:
            base_names = sorted({k for _, x in mls for k in x["baselines"]})
            beats = {}
            for k in base_names:
                pairs = [(x["baselines"][k].get("same"), x["baselines"][k].get("ic")) for _, x in mls if k in x["baselines"]]
                pairs = [(e, b) for e, b in pairs if e is not None and b is not None]
                beats[k] = {"cases": len(pairs), "ensemble_wins": sum(1 for e, b in pairs if e > b + 0.01), "mean_baseline_ic": (sum(b for _, b in pairs) / len(pairs)) if pairs else None,
                            "mean_ensemble_ic": (sum(e for e, _ in pairs) / len(pairs)) if pairs else None}
            ens = [x["ensemble"].get("ic") for _, x in mls if x["ensemble"].get("ic") is not None]
            r2 = [x["ensemble"].get("r2_vs_mean") for _, x in mls if x["ensemble"].get("r2_vs_mean") is not None]
            agg["ml"][lab] = {"assets": len(mls), "verified": [a for a, x in mls if x.get("verified")], "mean_ensemble_ic": (sum(ens) / len(ens)) if ens else None,
                              "baselines": {k: v for k, v in beats.items() if v["cases"]},
                              "r2_vs_mean": r2, "r2_positive": sum(1 for v in r2 if v > 0),
                              "direction_verified": sum(1 for _, x in mls if (x.get("direction") or {}).get("verified")),
                              "vol_verified": sum(1 for _, x in mls if (x.get("volatility") or {}).get("verified")),
                              "vol_cases": sum(1 for _, x in mls if x.get("volatility")),
                              "dd_verified": sum(1 for _, x in mls if (x.get("drawdown") or {}).get("verified")),
                              "dd_cases": sum(1 for _, x in mls if x.get("drawdown")),
                              "dd_stable": sum(1 for _, x in mls if (x.get("drawdown") or {}).get("stable")),
                              "risk_models": _risk_table(mls),
                              "reasons": {a: x.get("reasons") for a, x in mls}}
            cb = [x["combined"] for _, x in mls if x.get("combined")]
            if cb:
                def mean(k):
                    xs = [c[k] for c in cb if c.get(k) is not None]
                    return sum(xs) / len(xs) if xs else None
                agg["combined"][lab] = {"cases": len(cb), "ic_combined": mean("ic_combined"), "ic_shaffer": mean("ic_shaffer"), "ic_ml": mean("ic_ml"),
                                        "alpha_mean": mean("alpha"),
                                        "combined_beats_both": sum(1 for c in cb if None not in (c["ic_combined"], c["ic_shaffer"], c["ic_ml"]) and c["ic_combined"] > max(c["ic_shaffer"], c["ic_ml"]))}
    return agg


def _ratio_gain(d: Optional[dict], k: str, kb: str) -> Optional[float]:
    return (1 - d[k] / d[kb]) if d and d.get(k) is not None and d.get(kb) else None


def _risk_table(mls) -> dict:
    """Per model: cases, verified (beats every baseline OOS), stable (beats them in both halves of the OOS period; — where
    not measured), median error improvement over the best baseline."""
    rows = [("Return (R² vs historical mean)", lambda x: {"verified": x.get("verified"), "stable": None, "improvement": x["ensemble"].get("r2_vs_mean")}),
            ("Direction (Brier vs base rate)", lambda x: ({"verified": x["direction"].get("verified"), "stable": None,
                                                            "improvement": _ratio_gain(x["direction"], "brier", "baseline_brier")}
                                                           if (x.get("direction") or {}).get("brier") is not None else None)),
            ("Volatility (vs current vol, EWMA)", lambda x: (x.get("risk") or {}).get("volatility")),
            ("Drawdown probability (Brier vs base rate)", lambda x: ({"verified": x["drawdown"].get("verified"), "stable": x["drawdown"].get("stable"),
                                                                     "improvement": _ratio_gain(x["drawdown"], "brier", "baseline_brier")}
                                                                    if x.get("drawdown") else None)),
            ("Tail loss (vs previous window, vol-scaled)", lambda x: (x.get("risk") or {}).get("tail_loss")),
            ("Beta change (vs no change, mean change, Blume)", lambda x: (x.get("risk") or {}).get("beta_change"))]
    out = {}
    for name, get in rows:
        xs = [v for v in (get(x) for _, x in mls) if v]
        imp = sorted(v["improvement"] for v in xs if v.get("improvement") is not None)
        out[name] = {"cases": len(xs), "verified": sum(1 for v in xs if v.get("verified")),
                     "stable": None if all(v.get("stable") is None for v in xs) else sum(1 for v in xs if v.get("stable")),
                     "median_improvement": imp[len(imp) // 2] if imp else None}
    return out


def markdown(u: dict, agg: dict) -> str:
    from .features import FEATURES
    L = []
    w = L.append
    labs = [lab for lab, _ in cfg.HORIZONS]
    S = {a: v for a, v in u["shaffer"].items() if not v.get("error")}
    w("# Shaffer Score v2 and ML v2: the audit")
    w("")
    w(f"Generated {u.get('started')} from the real research store (prices up to the latest close). "
      f"Universe: {agg['n_assets']} assets with at least six years of daily history; ML: {len(u['ml'])} representative assets. "
      f"Run time {u.get('seconds', 0) / 60:.0f} minutes. Every number below is out of sample: each score was computed on its date "
      "with only the information available then, and compared with what happened afterwards.")
    if agg["errors"]:
        w("")
        w("Assets that failed: " + ", ".join(f"{a} ({e})" for a, e in agg["errors"].items()))
    w("")
    w("## 1. The exact formula")
    w("")
    w("```")
    w("SS_raw(a,h,t) = 100 · tanh( Σ_f W_f,a,h,t · A_f,a · H_f,h · FamilyScore_f,a,h,t / K_a,h )")
    w("FamilyScore_f = Σ_{i∈f} ω_i · s_i · c_i · r_i · d_i          ω = correlation-penalised weights, Σω = 1")
    w("K_a,h = %.2f · Σ_f A_f,a · H_f,h over the families with data" % cfg.KAPPA)
    w("edge = shrunk (g(SS_raw) − ȳ),  g = isotonic map from raw score to forward return (vol units), out of sample only; ȳ = the asset's average")
    w("SS_cal = 100 · tanh( edge / %.2f )   — the evidence relative to the asset's own average" % cfg.G_SCALE)
    w("E[R] = exp((ȳ + edge)·σ·√(h/252)) − 1 = typical + evidence part (the evidence part has the sign of SS_cal)")
    w("```")
    w("")
    w("## 2–3. Families and every signal (prior direction: + bullish when high, − bearish when high, 0 learned from evidence)")
    w("")
    for f, sigs in cfg.FAMILIES.items():
        w(f"**{f}** — " + "; ".join(f"`{sg}` ({'+' if p > 0 else '−' if p < 0 else '0'}) {FEATURES.get(sg, ('', '', ''))[2]}" for sg, p in sigs))
        w("")
    w("## 4. Standardisation")
    w("")
    w(f"Each signal's z-score uses the mean and standard deviation of its values strictly before t (expanding, after 252 observations), capped at ±3. "
      f"s = δ · clip(z / {cfg.S_SCALE:g}, −1, 1), δ = the direction the signal is used in.")
    w("")
    w("## 5. Predictive weight")
    w("")
    w("w = |PS| × (¼ + ¾ · stability). PS (predictive strength, correlation units) is the median of: the Pearson IC of the z-score with the vol-scaled "
      "forward return; the IC implied by the directional hit rate (ρ = sin(π(hit − ½))); and the IC implied by the conditional-return spread "
      "(E[y | z > ½] − E[y | z < −½] ÷ 2.28σ). Stability = share of the three thirds of the matured history whose IC has the same sign. "
      f"Hierarchical evidence: PS is shrunk toward the asset-class IC (itself shrunk toward the global IC) with weight {20:g} ÷ (n_eff + 20), "
      "from other assets' January checkpoints known at the time.")
    w("")
    w("## 6. Confidence")
    w("")
    w(f"c = min(1, √(n_eff / {cfg.N_FULL:g})) × |IC| / (|IC| + 1.96·SE) × (1 − ½·q) × data quality; SE = 1/√(n_eff − 3); q = Benjamini–Hochberg "
      "across all signals at that horizon; data quality = share of the last year with a value. n_eff = rows × step ÷ max(h, persistence), persistence "
      "= −21 / ln ρ₂₁ of the signal.")
    w("")
    w("## 7. Regime adjustment")
    w("")
    w(f"r = clip(1 + mean over regime dimensions of λ·(ratio − 1), {cfg.R_MIN}, {cfg.R_MAX}); ratio = δ·IC in today's state ÷ |PS| (clipped 0..2); "
      f"λ = n_eff_state ÷ (n_eff_state + {cfg.N_REGIME:g}). Dimensions: market, volatility, rates, inflation, growth, dollar, liquidity. Up to 12M only.")
    w("")
    w("## 8. Decay")
    w("")
    w(f"trend = 0.6·(δ·IC₃ᵧ ÷ |IC|) + 0.4·(δ·IC₁ᵧ ÷ |IC|); HEALTHY ≥ 0.6, WEAKENING ≥ 0.2, else DECAYING; INSUFFICIENT DATA below 10 independent 3-year "
      f"observations. d = 1 − λ(1 − clip(½ + ½·trend, {cfg.D_MIN}, 1)), λ = n_eff₃ᵧ ÷ (n_eff₃ᵧ + 30): gradual, never flips a sign.")
    w("")
    w("## 9. Correlated signals")
    w("")
    w("Within a family, ω_i = (w_i ÷ Σ_j ρ²_ij) normalised, ρ_ij the correlation of the two signals' z-scores up to the refit (the sum includes ρ_ii = 1). "
      "Two identical signals therefore count as one; families (not signals) are then summed.")
    w("")
    w("## 10–11. Asset and horizon applicability")
    w("")
    w("| Family | " + " | ".join(cfg.CLASSES) + " |")
    w("|---|" + "---|" * len(cfg.CLASSES))
    for f in cfg.FAMILIES:
        w(f"| {f} | " + " | ".join(f"{cfg.applicability(f, c):.2f}" for c in cfg.CLASSES) + " |")
    w("")
    w("| Family | " + " | ".join(labs) + " |")
    w("|---|" + "---|" * len(labs))
    for f in cfg.FAMILIES:
        w(f"| {f} | " + " | ".join(f"{cfg.horizon_fit(f, l):.1f}" for l in labs) + " |")
    w("")
    w("H is an economic prior; the evidence-based family weight W (evidence × out-of-sample validation V) decides the rest.")
    w("")
    w("## 12. How the historical scores are generated")
    w("")
    w("One forward sweep over the calendar per asset. An observation dated t (signal z-scores at t, forward return t→t+h) enters the evidence only at "
      "t + h + 1. Evidence is refitted at the first session of each month; scores are computed weekly and on the last session by the one scoring "
      "routine, with that month's evidence, the signals on the day and the regime on the day. Matured scores feed the family validation "
      "multipliers and the calibration from the next refit. `compute_shaffer_score(asset, horizon, as_of)` runs the sweep to `as_of`; the live score is "
      "the same call on the latest session (tests prove a past date scored directly equals its record, and that later prices do not change it). "
      f"Macro data are FRED first releases on their publication dates; fundamentals are dated by SEC filing; splits are applied to per-share data.")
    w("")
    w("## 13. History per asset")
    w("")
    w("| Asset | Class | Prices since | Years | First 3M score | 3M out-of-sample record (years) |")
    w("|---|---|---|---|---|---|")
    for r in agg["history"]:
        w(f"| {r['asset']} | {r['class']} | {r['first_price']} | {r['years']} | {r['first_score_3M'] or '—'} | {_fmt(r['oos_years_3M'], 1) if r['oos_years_3M'] else '—'} |")
    w("")
    w("## 14. Effective sample by horizon")
    w("")
    w("| Horizon | Assets scored | Median independent observations per asset | Total |")
    w("|---|---|---|---|")
    for lab in labs:
        b = agg["by_horizon"].get(lab) or {}
        w(f"| {lab} | {b.get('assets', 0)} | {_fmt(b.get('n_eff_median'), 0) if b.get('n_eff_median') else '—'} | {_fmt(b.get('n_eff_total'), 0) if b.get('n_eff_total') else '—'} |")
    w("")
    w("## 15–16. Calibration: what each score band was followed by (all assets pooled)")
    w("")
    for lab in labs:
        bands = agg["bands"].get(lab)
        if not bands or not any(v.get("n") for v in bands.values()):
            continue
        w(f"**{lab}**")
        w("")
        w("| Raw score | Scores | Indep. obs. | Mean return | Median | Up share | Volatility (log) | 95% CI of the mean |")
        w("|---|---|---|---|---|---|---|---|")
        for k, v in bands.items():
            if not v.get("n"):
                w(f"| {k} | 0 | | | | | | |")
                continue
            w(f"| {k} | {v['n']} | {v['n_eff']:.0f} | {_pct(v['mean'])} | {_pct(v['median'])} | {_pct(v['hit'], 0)} | {v['vol']:.3f} | {_pct(v['ci'][0])} … {_pct(v['ci'][1])} |")
        w("")
    w("## 16b. Calibrated score and expected return: sign consistency")
    w("")
    w("Every scored date where an expected return was shown. The calibrated score is the evidence relative to the asset's own average; "
      "the expected return is total (average + evidence), so the two may differ in sign for an asset with a strong average — the evidence "
      "part must never differ from the calibrated score.")
    w("")
    w("| Horizon | Scores with an expected return | Total return and calibrated score differ in sign | Evidence part differs in sign (bug) |")
    w("|---|---|---|---|")
    for lab in labs:
        tot = [0, 0, 0]
        for v in S.values():
            c = (v.get("sign_checks") or {}).get(lab)
            if c:
                tot = [a + b for a, b in zip(tot, c)]
        if tot[0]:
            w(f"| {lab} | {tot[0]} | {tot[1]} ({tot[1] / tot[0]:.0%}) | {tot[2]} |")
    w("")
    w("## 17–18. Out-of-sample IC and hit rate by horizon")
    w("")
    w("The Stouffer t treats assets as independent; assets move together, so it overstates significance. The date-clustered t averages "
      "each week's evidence across assets first and is the one to read.")
    w("")
    w("| Horizon | Assets | IC (weighted by n_eff) | Median IC | Share of assets with IC > 0 | t (Stouffer, assets independent) | t (date-clustered) | Hit rate (|score| ≥ 5) |")
    w("|---|---|---|---|---|---|---|---|")
    for lab in labs:
        b = agg["by_horizon"].get(lab) or {}
        if not b.get("assets"):
            w(f"| {lab} | 0 | | | | | | |")
            continue
        w(f"| {lab} | {b['assets']} | {_fmt(b['ic_weighted'])} | {_fmt(b['ic_median'])} | {_pct(b['share_positive'], 0)} | {_fmt(b['t_combined'], 1)} | {_fmt(b.get('t_clustered'), 1)} | {_pct(b['hit'], 1)} |")
    w("")
    w("## 19. By asset class")
    w("")
    for lab in ("1W", "1M", "3M", "6M", "12M"):
        bc = agg["by_class"].get(lab)
        if not bc:
            continue
        w(f"**{lab}**: " + "; ".join(f"{c} IC {_fmt(v['ic'])} ({v['assets']} assets, {_pct(v['share_positive'], 0)} positive, hit {_pct(v['hit'], 0)})" for c, v in sorted(bc.items())))
        w("")
    w("## 20. By regime")
    w("")
    for lab in ("1W", "1M", "3M", "6M", "12M"):
        br = agg["by_regime"].get(lab)
        if not br:
            continue
        w(f"**{lab}**: " + "; ".join(f"{st.replace('_', ' ')} {_fmt(v['ic'])}" for st, v in sorted(br.items())))
        w("")
    w("## 21–22. Which families add independent information, and which look useless")
    w("")
    w("Own IC = the family score's out-of-sample IC; incremental IC = its partial correlation with the forward return after removing the rest of the score. "
      "Pooled across assets (weighted by independent observations; t combined by Stouffer).")
    w("")
    for lab in ("1W", "1M", "3M", "6M", "12M"):
        fm = agg["families"].get(lab)
        if not fm:
            continue
        w(f"**{lab}**")
        w("")
        w("| Family | Assets | Own IC | t | Incremental IC | t (Stouffer) | t (date-clustered) | Verdict (clustered) |")
        w("|---|---|---|---|---|---|---|---|")
        for f, v in sorted(fm.items(), key=lambda kv: -(kv[1].get("t_partial_clustered") if kv[1].get("t_partial_clustered") is not None else (kv[1]["t_partial"] or -99))):
            tc = v.get("t_partial_clustered")
            tp = tc if tc is not None else (v["t_partial"] or 0)
            verdict = "adds independent information" if tp >= 2 else "some evidence" if tp >= 1 else "negative record" if tp <= -2 else "no measurable value" if abs(tp) < 1 else "weak"
            if v["assets"] < ADMIT_MIN_ASSETS:
                verdict = f"not evidence: only {v['assets']} asset(s)"
            w(f"| {f} | {v['assets']} | {_fmt(v['ic'])} | {_fmt(v['t'], 1)} | {_fmt(v['partial'])} | {_fmt(v['t_partial'], 1)} | {_fmt(tc, 1)} | {verdict} |")
        w("")
    w("## 21b. Does validation shrink the families with a negative record?")
    w("")
    w("Influence = a family's share of Σ|family contribution| in each scored record (its real say in the score); V = its validation "
      "multiplier (0.5 + t/4, clipped to [0, 1], from its own matured out-of-sample record). Both averaged over records before and from "
      f"{CONFIRM_FROM[:4]}. A family whose incremental record is negative should see V and influence fall toward zero; no sign is ever "
      "reversed and no weight is set by hand. 'Not shrinking enough' = incremental t (date-clustered) ≤ −1 and influence from "
      f"{CONFIRM_FROM[:4]} still above half its earlier level, or V still ≥ 0.4.")
    w("")
    for lab in ("1W", "1M", "3M", "6M", "12M"):
        fm = agg["families"].get(lab)
        if not fm:
            continue
        w(f"**{lab}**")
        w("")
        w(f"| Family | Incremental t (clustered) | V before → from {CONFIRM_FROM[:4]} | Influence before → from {CONFIRM_FROM[:4]} | Assessment |")
        w("|---|---|---|---|---|")
        for f, v in sorted(fm.items(), key=lambda kv: (kv[1].get("t_partial_clustered") if kv[1].get("t_partial_clustered") is not None else 0)):
            inf = v.get("influence") or {}
            d, c = inf.get("decide") or {}, inf.get("confirm") or {}
            tc = v.get("t_partial_clustered")
            if v["assets"] < ADMIT_MIN_ASSETS:
                verdict = f"not evidence: only {v['assets']} asset(s)"
            elif tc is None or tc > -1:
                verdict = "—" if tc is None or tc < 1 else "positive record"
            elif c and d and (c["share"] > 0.5 * d["share"] or c["V"] >= 0.4):
                verdict = "NOT shrinking enough"
            else:
                verdict = "shrinking"
            w(f"| {f} | {_fmt(tc, 1)} | {_fmt(d.get('V'), 2)} → {_fmt(c.get('V'), 2)} | {_pct(d.get('share'), 1)} → {_pct(c.get('share'), 1)} | {verdict} |")
        w("")
    w("## 23. Decaying signals (at the latest refit)")
    w("")
    for lab in ("1W", "1M", "3M", "6M", "12M"):
        dec = agg["decay"].get(lab)
        if not dec:
            continue
        top = sorted(dec.items(), key=lambda kv: -(kv[1]["DECAYING"]))[:10]
        w(f"**{lab}**: " + "; ".join(f"`{sg}` decaying in {d['DECAYING']} assets, weakening in {d['WEAKENING']}, healthy in {d['HEALTHY']}" for sg, d in top if d["DECAYING"]))
        st = agg["status"].get(lab) or {}
        w("")
        w(f"Signal status across assets at {lab}: " + "; ".join(f"{k}: {v}" for k, v in sorted(st.items(), key=lambda kv: -kv[1])))
        w("")
    w("## 24. ML against each baseline")
    w("")
    w("Cases = assets where both had out-of-sample forecasts on the same rows; wins = ensemble rank IC above the baseline's by more than 0.01. "
      "A constant forecast (zero) has no rank IC, so the zero and historical-mean baselines are also judged by squared error: "
      "R² vs mean > 0 means the ensemble's out-of-sample errors were smaller than the expanding historical mean's.")
    w("")
    for lab in labs:
        m = agg["ml"].get(lab)
        if not m:
            continue
        w(f"**{lab}** — {m['assets']} assets; verified ML edge: {', '.join(m['verified']) or 'none'}; mean ensemble IC {_fmt(m['mean_ensemble_ic'])}; "
          f"direction model verified in {m['direction_verified']}; volatility model verified in {m['vol_verified']}/{m['vol_cases']}; drawdown model verified in {m['dd_verified']}/{m['dd_cases']}; "
          f"R² vs mean > 0 in {m['r2_positive']}/{len(m['r2_vs_mean'])} (median {_fmt(sorted(m['r2_vs_mean'])[len(m['r2_vs_mean']) // 2]) if m['r2_vs_mean'] else '—'})")
        w("")
        w("| Baseline | Cases | Ensemble wins | Mean baseline IC | Mean ensemble IC (same rows) |")
        w("|---|---|---|---|---|")
        for k, v in m["baselines"].items():
            w(f"| {k.replace('_', ' ')} | {v['cases']} | {v['ensemble_wins']} | {_fmt(v['mean_baseline_ic'])} | {_fmt(v['mean_ensemble_ic'])} |")
        w("")
    w("## 24b. Return models vs risk models")
    w("")
    w("Each model is walk-forward out of sample against its naive baselines on the same rows. Verified = beats every baseline over "
      "the whole OOS period (lower RMSE, or lower Brier for probabilities); stable = also beats them in BOTH halves of the OOS period "
      "(— = not measured for that model). Improvement = 1 − model error ÷ best baseline error (R² vs the historical mean for "
      "returns), median across assets.")
    w("")
    w("| Horizon | Model | Cases | Verified | Stable | Median improvement |")
    w("|---|---|---|---|---|---|")
    for lab in labs:
        m = agg["ml"].get(lab)
        for name, v in ((m or {}).get("risk_models") or {}).items():
            if v["cases"]:
                w(f"| {lab} | {name} | {v['cases']} | {v['verified']} | {'—' if v['stable'] is None else v['stable']} | {_fmt(v['median_improvement'])} |")
    w("")
    w("## 25. Shaffer vs ML")
    w("")
    w("| Horizon | Cases | Mean Shaffer IC | Mean ML IC (same rows) | ML better in |")
    w("|---|---|---|---|---|")
    for lab in labs:
        m = agg["ml"].get(lab)
        if not m or "shaffer" not in m["baselines"]:
            continue
        v = m["baselines"]["shaffer"]
        w(f"| {lab} | {v['cases']} | {_fmt(v['mean_baseline_ic'])} | {_fmt(v['mean_ensemble_ic'])} | {v['ensemble_wins']} |")
    w("")
    w("## 26. Does combining them help out of sample?")
    w("")
    w("α chosen on the first half of each asset's common out-of-sample period; ICs measured on the untouched second half.")
    w("")
    w("| Horizon | Cases | Mean α (weight on Shaffer) | IC combined | IC Shaffer | IC ML | Combined beats both in |")
    w("|---|---|---|---|---|---|---|")
    for lab in labs:
        c = agg["combined"].get(lab)
        if not c:
            continue
        w(f"| {lab} | {c['cases']} | {_fmt(c['alpha_mean'], 2)} | {_fmt(c['ic_combined'])} | {_fmt(c['ic_shaffer'])} | {_fmt(c['ic_ml'])} | {c['combined_beats_both']} |")
    w("")
    from .candidates import CANDIDATE_FEATURES
    w("## 27. Candidate families (shadow): do they add information?")
    w("")
    w(f"{len(cfg.CANDIDATE_FAMILIES)} families of economically different information were added in shadow (Earnings Surprise and Breadth in research phase 2): they are computed point in time and run through the same "
      "evidence machinery (weights, confidence, regime, decay, validation) but are NOT in the production score. Every asset's matured "
      f"out-of-sample record is split at one common date, {CONFIRM_FROM}: before it decides, from it confirms. The incremental IC (partial "
      "correlation of the family score with the forward return given the production score) is pooled **by date** — each week's average "
      "across assets, then a t on that weekly series with overlapping windows removed — because a hundred correlated equities are not a "
      "hundred independent tests. "
      f"**Admission rule** (fixed before the results): at least {ADMIT_MIN_ASSETS} assets; date-clustered t ≥ {ADMIT_T_DECIDE:g} before "
      f"{CONFIRM_FROM[:4]}; positive with t ≥ {ADMIT_T_CONFIRM:g} from {CONFIRM_FROM[:4]}, positive in a majority of assets; the score's IC "
      f"with the family not lower and its calibration monotonicity not more than {ADMIT_MONO_SLACK:g} lower from {CONFIRM_FROM[:4]}. "
      "Production weights were not re-tuned. (A first run pooled assets as independent — Stouffer — and split each asset's record into its "
      "own thirds; that overstated significance and was replaced by this test before anything was admitted.)")
    w("")
    for f, sigs in cfg.CANDIDATE_FAMILIES.items():
        w(f"**{f}** — " + "; ".join(f"`{sg}` ({'+' if p > 0 else '−' if p < 0 else '0'}) {CANDIDATE_FEATURES[sg][2]}" for sg, p in sigs))
        w("")
    admitted = []
    for lab in labs:
        c = (agg.get("candidates") or {}).get(lab) or {}
        if not c:
            continue
        w(f"**{lab}**")
        w("")
        w(f"| Family | Assets | Own IC (before {CONFIRM_FROM[:4]}) | Incremental IC before (clustered t) | Incremental IC from {CONFIRM_FROM[:4]} (clustered t) | Assets > 0 (from {CONFIRM_FROM[:4]}) | Score IC without → with | Monotonicity without → with | Verdict |")
        w("|---|---|---|---|---|---|---|---|---|")
        for f, r in sorted(c.items(), key=lambda kv: (kv[0] == "ALL", kv[0])):
            if f.startswith("VARIANT:"):
                continue
            d, k = r.get("decide") or {}, r.get("confirm") or {}
            name = "All candidates together" if f == "ALL" else f
            verdict = r["verdict"] if f == "ALL" or r["verdict"] == "ADMIT" else "REJECT: " + "; ".join(r["reasons"])
            if r["verdict"] == "ADMIT":
                admitted.append((f, lab))
            w(f"| {name} | {d.get('assets', 0)} | {_fmt(d.get('own'))} | {_fmt(d.get('partial'))} ({_fmt(d.get('t_partial'), 1)}) | "
              f"{_fmt(k.get('partial'))} ({_fmt(k.get('t_partial'), 1)}) | {_pct(k.get('share_positive'), 0)} | {_fmt(k.get('ic_without'))} → {_fmt(k.get('ic_with'))} | "
              f"{_fmt(k.get('mono_without'), 2)} → {_fmt(k.get('mono_with'), 2)} | {verdict} |")
        w("")
    w("**Admitted:** " + (", ".join(f"{f} at {lab}" for f, lab in admitted) if admitted else "none — every candidate family stays in shadow.")
      + " Admission is applied only by recording it in `shaffer_score.ADMITTED` (and a new score version), never automatically.")
    w("")
    w("## 27b. Methodology variants (shadow): should the economic prior H or a stricter V decide influence?")
    w("")
    from .shaffer import ShafferRun
    w((ShafferRun._variants.__doc__ or "").strip().replace("\n", " ").replace("  ", " "))
    w("")
    w("The same test as a candidate family: the variant's score must carry information beyond the production score (clustered t ≥ 2 before "
      f"{CONFIRM_FROM[:4]}, ≥ 1 from it) and must not lower the score's IC or calibration monotonicity from {CONFIRM_FROM[:4]}.")
    w("")
    w(f"| Horizon | Variant | Assets | Score IC production → variant (from {CONFIRM_FROM[:4]}) | Monotonicity production → variant | Beyond production: before (t) | from {CONFIRM_FROM[:4]} (t) | Verdict |")
    w("|---|---|---|---|---|---|---|---|")
    for lab in labs:
        c = (agg.get("candidates") or {}).get(lab) or {}
        for f, r in sorted(c.items()):
            if not f.startswith("VARIANT:"):
                continue
            d, k = r.get("decide") or {}, r.get("confirm") or {}
            verdict = "ADOPT (pending a new score version)" if r["verdict"] == "ADMIT" else "REJECT: " + "; ".join(r["reasons"])
            w(f"| {lab} | {f[8:].replace('_', ' ')} | {d.get('assets', 0)} | {_fmt(k.get('ic_without'))} → {_fmt(k.get('ic_with'))} | "
              f"{_fmt(k.get('mono_without'), 2)} → {_fmt(k.get('mono_with'), 2)} | {_fmt(d.get('partial'))} ({_fmt(d.get('t_partial'), 1)}) | "
              f"{_fmt(k.get('partial'))} ({_fmt(k.get('t_partial'), 1)}) | {verdict} |")
    w("")
    return "\n".join(L)
