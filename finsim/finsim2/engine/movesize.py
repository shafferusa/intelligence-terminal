"""Shaffer Directional 1D – 1W: how big the move will be (research only; stage 3 of the 2026-09-27 program).

Protocol: SHAFFER_MOVE_SIZE_PROTOCOL.md (fixed before any result). Report: SHAFFER_MOVE_SIZE.md.

Stage 1 (NEW_DATA_SIGNALS.md) found nothing that beats the point-in-time prior for the DIRECTION of 1D – 1W moves, so
P(up) stays the prior-only model. What the new data did improve is the SIZE of moves: the event calendar and 8-K
event types lower the volatility-forecast error at 1W and 1M. This module forecasts the size of the next 1-day and
1-week move for every asset, with a calibrated 90% range, and tests whether the event information makes it better.

Records: every session (1D) or every 5 sessions (1W) per asset from 2001, known at the close of t:
    base     log σ5, log σ21, log σ63 (RMS of daily log returns), log VIX, log |r_t| (today's move), class dummies
    calendar macro release (CPI, jobs, GDP, PPI, retail, FOMC) on the next session, FOMC on the next session — both only
             when announced by t — scheduled releases / FOMC in the next five sessions, and for stocks: earnings expected
             within 5 / 21 sessions and sessions since the last release (engine/newinfo.py event_calendar)
    8-K      the sec_events family (stocks)
Target: log realised volatility over (t, t + h] (RMS of the daily log returns; 1D = log |r_{t+1}|, floored at 1e-4).
Models (pooled ridge, trained only on outcomes matured before each era; the level is calibrated on the training
records so that mean exp(2·forecast) equals mean realised variance):
    M0 base · M1 base + calendar · M2 base + 8-K · M3 base + calendar + 8-K
Loss: QLIKE of the variance forecast, r²/σ² − log(r²/σ²) − 1 (the standard loss for volatility forecasts), where r² is
the realised mean squared daily return over the horizon. 90% range: the forecast σ times the 5% / 95% quantiles of the
standardised training outcomes (return / forecast σ), so the range is calibrated, not assumed normal.
"""
from __future__ import annotations

import bisect
import math
import time
from array import array
from typing import Dict, List, Optional, Tuple

NAN = float("nan")


class Rec:
    __slots__ = ("a", "d", "end", "x", "y", "r2", "ret", "h")

    def __init__(self, a, d, end, x, y, r2, ret, h):
        self.a, self.d, self.end, self.x, self.y, self.r2, self.ret, self.h = a, d, end, x, y, r2, ret, h

RESEARCH_KEY = "lab:movesize"
HORIZONS = [("1D", 1, 2), ("1W", 5, 5)]           # (label, sessions, step between records: 1D every other session)
START = "2001-01-01"
ERAS = [("2009-01-01", "2013-01-01"), ("2013-01-01", "2017-01-01"), ("2017-01-01", "2021-01-01"),
        ("2021-01-01", "2025-01-01"), ("2025-01-01", "2100-01-01")]
ERA_LABEL = ["2009–12", "2013–16", "2017–20", "2021–24", "2025–"]
SPLIT = "2018-01-01"
CLASSES = ["EQUITY", "ETF", "INDEX", "TREASURY", "CORP_BOND", "COMMODITY", "FX", "CRYPTO"]
BASE = ["log_s5", "log_s21", "log_s63", "log_vix", "log_abs_r"] + [f"cls_{c}" for c in CLASSES[1:]]
CAL = ["macro_next", "fomc_next", "macro_events_5d", "fomc_5d", "earn_soon_5d", "earn_soon_21d", "earn_age"]
K8 = ["k8_count_63", "k8_mgmt_126", "k8_deal_126", "k8_bad_252", "k8_other_21"]
MODELS = {"M0": BASE, "M1": BASE + CAL, "M2": BASE + K8, "M3": BASE + CAL + K8}
ALL = BASE + CAL + K8
IDX = {f: i for i, f in enumerate(ALL)}
FLOOR = 1e-4
FDR_Q = 0.10
LAMBDA = 1.0


# ------------------------------------------------------------------ records
def _rms(rets, a: int, b: int) -> Optional[float]:
    seg = [x for x in rets[a:b] if x is not None]
    if len(seg) < max(1, (b - a) // 2):
        return None
    return math.sqrt(sum(x * x for x in seg) / len(seg))


def asset_rows(builder, meta: dict, h: int, step: int, start_i: int, rets) -> List[dict]:
    """Records for one asset (features at t, outcome over (t, t+h]); the outcome is None when not yet matured."""
    n = builder.n
    vix = builder.macro("VIXCLS")
    cal_f = builder.features("event_calendar", meta) or {}
    k8_f = builder.features("sec_events", meta) or {}
    nxt = _next_session_events(builder)
    cls = meta.get("asset_class")
    cols_cal = {k: (cal_f.get(k) or [None] * n) for k in CAL[2:]}
    cols_k8 = {k: (k8_f.get(k) or [None] * n) for k in K8}
    out = []
    for t in range(max(start_i, 64), n, step):
        if rets[t] is None:
            continue
        s5, s21, s63 = _rms(rets, t - 4, t + 1), _rms(rets, t - 20, t + 1), _rms(rets, t - 62, t + 1)
        if not s5 or not s21 or not s63 or vix[t] is None or vix[t] <= 0:
            continue
        x = {"log_s5": math.log(max(s5, FLOOR)), "log_s21": math.log(max(s21, FLOOR)), "log_s63": math.log(max(s63, FLOOR)),
             "log_vix": math.log(vix[t]), "log_abs_r": math.log(max(abs(rets[t]), FLOOR))}
        for c in CLASSES[1:]:
            x[f"cls_{c}"] = 1.0 if cls == c else 0.0
        x["macro_next"], x["fomc_next"] = nxt[0][t], nxt[1][t]
        for k in CAL[2:]:
            x[k] = cols_cal[k][t]
        for k in K8:
            x[k] = cols_k8[k][t]
        y = _rms(rets, t + 1, t + 1 + h) if t + h < n else None
        out.append(Rec(meta["id"], builder.cal[t], builder.cal[min(n - 1, t + h)], array("d", [NAN if x[f] is None else x[f] for f in ALL]),
                       math.log(max(y, FLOOR)) if y is not None else None, y * y if y is not None else None,
                       (sum(v for v in rets[t + 1:t + 1 + h] if v is not None) if t + h < n else None), h))
    return out


def _next_session_events(builder) -> Tuple[List[Optional[float]], List[Optional[float]]]:
    """(scheduled macro release on the next session, FOMC on the next session) — only when announced by t."""
    if "_next" in builder._wide:
        return builder._wide["_next"]
    from ..data.calendar import EVENTS as EVCAL
    n, cal = builder.n, builder.cal
    mac: List[Optional[float]] = [None] * n
    fom: List[Optional[float]] = [None] * n
    rows = builder.store.alt(EVCAL, "macro:US")
    ev: Dict[int, List[Tuple[int, str]]] = {}
    first = None
    for _, d, f, v, pub in rows:
        if not v:
            continue
        e = bisect.bisect_left(cal, str(d)[:10])
        if e >= n or cal[e] != str(d)[:10]:
            continue
        k = bisect.bisect_left(cal, str(pub)[:10])
        ev.setdefault(e, []).append((k, f))
        first = k if first is None else min(first, k)
    if first is not None:
        for i in range(first, n):
            known = [f for k, f in ev.get(i + 1, []) if k <= i]
            mac[i] = 1.0 if known else 0.0
            fom[i] = 1.0 if "fomc" in known else 0.0
    builder._wide["_next"] = (mac, fom)
    return mac, fom


def build(store, research, progress=None, assets: Optional[List[str]] = None) -> Dict[str, List[dict]]:
    from . import newinfo as N
    from .directional import _logret
    say = progress or (lambda m: None)
    b = N.Builder(research, store)
    start_i = bisect.bisect_left(b.cal, START)
    metas = [a for a in store.assets() if (not assets or a["id"] in assets) and store.price_count(a["id"]) >= 252 * 6
             and a.get("asset_class") in CLASSES]
    out: Dict[str, List[dict]] = {lab: [] for lab, _, _ in HORIZONS}
    t0 = time.time()
    for k, m in enumerate(metas):
        rets = _logret(b.series(m["id"]))
        for lab, h, step in HORIZONS:
            out[lab] += asset_rows(b, m, h, step, start_i, rets)
        if k % 20 == 0:
            say(f"move-size records {k + 1}/{len(metas)} ({time.time() - t0:.0f}s)")
    return out


# ------------------------------------------------------------------ fitting (sufficient statistics per outcome year)
def _z(x, i: int) -> float:
    v = x[i]
    return 0.0 if v != v else v                       # missing -> 0 (after the per-model mean shift below)


class YearStats:
    """Σ v vᵀ and Σ v·y over records, v = [1, x (missing = 0), missing indicators are not used]."""
    __slots__ = ("A", "b", "n")

    def __init__(self):
        p = len(ALL) + 1
        self.A = [0.0] * (p * p)
        self.b = [0.0] * p
        self.n = 0

    def add(self, r: "Rec"):
        v = [1.0] + [_z(r.x, i) for i in range(len(ALL))]
        p = len(v)
        A, y = self.A, r.y
        self.n += 1
        for i in range(p):
            vi = v[i]
            if vi:
                self.b[i] += vi * y
                base = i * p
                for j in range(i, p):
                    vj = v[j]
                    if vj:
                        A[base + j] += vi * vj


def year_stats(rows: List["Rec"]) -> Dict[int, YearStats]:
    out: Dict[int, YearStats] = {}
    for r in rows:
        if r.y is None:
            continue
        yr = int(r.end[:4])
        s = out.get(yr)
        if s is None:
            s = out[yr] = YearStats()
        s.add(r)
    return out


def fit(stats: Dict[int, YearStats], before_year: int, feats: List[str], train_rows: List["Rec"]) -> Optional[dict]:
    """OLS with a small ridge on the named features, on outcomes that matured before `before_year`; then the level
    calibration and the standardised-outcome quantiles on those training records."""
    ys = [s for y, s in stats.items() if y < before_year]
    if sum(s.n for s in ys) < 5000:
        return None
    P = len(ALL) + 1
    sel = [0] + [IDX[f] + 1 for f in feats]
    A = [[sum(s.A[min(i, j) * P + max(i, j)] for s in ys) + (LAMBDA if i == j and i else 0.0) for j in sel] for i in sel]
    b = [sum(s.b[i] for s in ys) for i in sel]
    from .alphahz import _solve
    w = _solve(A, b)
    cols = [IDX[f] for f in feats]
    f = [w[0] + sum(wi * _z(r.x, c) for wi, c in zip(w[1:], cols)) for r in train_rows]
    num = sum(r.r2 for r in train_rows)
    den = sum(math.exp(2 * v) for v in f)
    k = num / den if den > 0 else 1.0
    std = sorted(r.ret / math.sqrt(k * math.exp(2 * v) * r.h) for r, v in zip(train_rows, f) if r.ret is not None)
    q = (std[int(0.05 * (len(std) - 1))], std[int(0.95 * (len(std) - 1))]) if len(std) > 100 else (-1.645, 1.645)
    return {"w": w, "cols": cols, "k": k, "q": q, "feats": feats}


def predict(m: dict, r: "Rec") -> float:
    """Forecast daily variance over the horizon."""
    return m["k"] * math.exp(2 * (m["w"][0] + sum(wi * _z(r.x, c) for wi, c in zip(m["w"][1:], m["cols"]))))


def qlike(r2: float, v: float) -> float:
    x = max(r2, FLOOR * FLOOR) / v
    return x - math.log(x) - 1.0


# ------------------------------------------------------------------ the study
def _paired(by_date: Dict[str, List[float]], h: int) -> dict:
    ms = [sum(v) / len(v) for _, v in sorted(by_date.items())]
    n = len(ms)
    if n < 20:
        return {"mean": None, "t": None, "dates": n}
    m = sum(ms) / n
    sd = math.sqrt(sum((v - m) ** 2 for v in ms) / (n - 1))
    n_eff = n * (1.0 if h == 1 else 1.0)            # records are non-overlapping (1D daily, 1W every 5 sessions)
    return {"mean": m, "t": m / (sd / math.sqrt(n_eff)) if sd > 0 else None, "dates": n}


def study(data: Dict[str, List[dict]], progress=None) -> dict:
    say = progress or (lambda m: None)
    out = {"started": time.strftime("%Y-%m-%d %H:%M:%S"), "horizons": {}}
    for lab, h, _ in HORIZONS:
        rows = [r for r in data[lab] if r.y is not None]
        stats = year_stats(rows)
        res: dict = {"records": len(rows), "models": {}}
        preds: Dict[str, List[Tuple["Rec", float, dict]]] = {mk: [] for mk in MODELS}
        for e, (a, b) in enumerate(ERAS):
            train = [r for r in rows if r.end < a]
            test = [r for r in rows if a <= r.d < b]
            if len(train) < 5000 or not test:
                continue
            for mk, feats in MODELS.items():
                m = fit(stats, int(a[:4]), feats, train)
                preds[mk] += [(r, predict(m, r), m) for r in test]
            say(f"{lab} era {ERA_LABEL[e]}: {len(train)} train / {len(test)} test")
        base = {id(r): v for r, v, _ in preds["M0"]}
        for mk in MODELS:
            ql = [qlike(r.r2, v) for r, v, _ in preds[mk]]
            cover = [1.0 if m["q"][0] * math.sqrt(v * h) <= r.ret <= m["q"][1] * math.sqrt(v * h) else 0.0
                     for r, v, m in preds[mk] if r.ret is not None]
            res["models"][mk] = {"n": len(ql), "qlike": sum(ql) / len(ql) if ql else None,
                                 "coverage_90": sum(cover) / len(cover) if cover else None}
            if mk == "M0":
                continue
            diff: Dict[str, List[float]] = {}
            eras: Dict[str, Dict[str, List[float]]] = {}
            for r, v, _ in preds[mk]:
                d = qlike(r.r2, base[id(r)]) - qlike(r.r2, v)          # > 0: the model's loss is lower
                diff.setdefault(r.d, []).append(d)
                e = next((ERA_LABEL[k] for k, (a, b) in enumerate(ERAS) if a <= r.d < b), None)
                eras.setdefault(e, {}).setdefault(r.d, []).append(d)
            res["models"][mk]["gain"] = _paired(diff, h)
            res["models"][mk]["eras"] = {e: _paired(v, h) for e, v in eras.items()}
            res["models"][mk]["split"] = _split(rows, stats, mk, h)
        out["horizons"][lab] = res
    finalise(out)
    return out


def _split(rows: List["Rec"], stats, mk: str, h: int) -> dict:
    train = [r for r in rows if r.end < SPLIT]
    test = [r for r in rows if r.d >= SPLIT]
    if len(train) < 5000 or not test:
        return {}
    y = int(SPLIT[:4])
    m0, m1 = fit(stats, y, MODELS["M0"], train), fit(stats, y, MODELS[mk], train)
    diff: Dict[str, List[float]] = {}
    for r in test:
        diff.setdefault(r.d, []).append(qlike(r.r2, predict(m0, r)) - qlike(r.r2, predict(m1, r)))
    return _paired(diff, h)


def finalise(out: dict) -> dict:
    from .newinfo import benjamini_hochberg, _p_one_sided
    tests = []
    for lab, hz in out["horizons"].items():
        for mk in ("M1", "M2", "M3"):
            m = hz["models"].get(mk) or {}
            g = m.get("gain") or {}
            eras = [e for e, v in (m.get("eras") or {}).items() if e != "2025–" and v.get("dates", 0) >= 20]
            won = sum(1 for e in eras if (m["eras"][e].get("mean") or 0) > 0)
            cov = m.get("coverage_90")
            m["gates"] = {"G1": (g.get("t") or 0) >= 2 and (g.get("mean") or 0) > 0, "eras_complete": len(eras), "eras_won": won,
                          "G2": len(eras) >= 3 and won >= len(eras) - 1 and ((m.get("split") or {}).get("t") or 0) >= 1,
                          "G3": cov is not None and 0.87 <= cov <= 0.93}
            tests.append((lab, mk, _p_one_sided(g.get("t"))))
    rej = benjamini_hochberg([p for *_, p in tests], FDR_Q)
    for (lab, mk, p), ok in zip(tests, rej):
        g = out["horizons"][lab]["models"][mk]["gates"]
        g["p"], g["fdr"] = p, ok
        g["passed"] = bool(g["G1"] and g["G2"] and g["G3"] and ok)
    return out


# ------------------------------------------------------------------ today
def today(store, research, res: dict, data: Dict[str, List[dict]]) -> dict:
    """Per asset and horizon: forecast move size (1σ) and the calibrated 90% range, from the best passing model (else
    M0), fitted on every matured record."""
    from . import newinfo as N
    from .directional import _logret
    b = N.Builder(research, store)
    t = b.n - 1
    view = {"date": b.cal[t], "horizons": {}}
    for lab, h, step in HORIZONS:
        hz = res["horizons"].get(lab) or {}
        passed = [mk for mk in ("M3", "M1", "M2") if ((hz.get("models") or {}).get(mk) or {}).get("gates", {}).get("passed")]
        mk = passed[0] if passed else "M0"
        rows = [r for r in data[lab] if r.y is not None]
        m = fit(year_stats(rows), 9999, MODELS[mk], rows)
        items = []
        for a in store.assets():
            if a.get("asset_class") not in CLASSES or store.price_count(a["id"]) < 252 * 6:
                continue
            rets = _logret(b.series(a["id"]))
            cur = asset_rows(b, a, h, 10 ** 9, t, rets)
            if not cur:
                continue
            v = predict(m, cur[0])
            s = math.sqrt(v * h)
            xm, xe = cur[0].x[IDX["macro_next"]], cur[0].x[IDX["earn_soon_5d"]]
            items.append({"asset": a["id"], "sigma": s, "lo": m["q"][0] * s, "hi": m["q"][1] * s,
                          "macro_next": None if xm != xm else xm, "earn_soon_5d": None if xe != xe else xe})
        items.sort(key=lambda i: -i["sigma"])
        view["horizons"][lab] = {"model": mk, "items": items}
    return view


def _yn(v) -> str:
    return "—" if v is None else ("yes" if v else "no")


def _f(v, d=4):
    return "—" if v is None else f"{v:+.{d}f}"


def markdown(res: dict, view: Optional[dict] = None) -> str:
    L = ["# How big will the move be? Shaffer Directional 1D – 1W move size — research (stage 3)", "",
         f"Built {res.get('started')} (protocol SHAFFER_MOVE_SIZE_PROTOCOL.md). P(up) stays the point-in-time prior-only model "
         "(stage 1 found no new information that beats it); this study forecasts the SIZE of the next day's and week's move.", "",
         "QLIKE gain = M0's loss − the model's loss (positive = better), per date, walk-forward. PASSED needs G1 (gain t ≥ 2), "
         "G2 (gain in all but one complete era, ≥ 3 eras; split t ≥ 1), G3 (90% range covers 87–93% out of sample) and BH FDR q = 0.10.", "",
         "| Horizon | Model | Records | QLIKE | QLIKE gain (t) | Eras won | Split t | 90% coverage | Status |", "|---|---|---|---|---|---|---|---|---|"]
    names = {"M0": "base (σ5/σ21/σ63, VIX, today's move)", "M1": "+ event calendar", "M2": "+ 8-K events", "M3": "+ calendar + 8-K"}
    for lab, hz in res.get("horizons", {}).items():
        for mk, m in hz["models"].items():
            g = m.get("gates") or {}
            gain = m.get("gain") or {}
            cov = m.get("coverage_90")
            st = "reference" if mk == "M0" else ("PASSED" if g.get("passed") else "NOT VALIDATED")
            L.append(f"| {lab} | {mk} {names[mk]} | {m.get('n')} | {_f(m.get('qlike'))} | {_f(gain.get('mean'))} ({_f(gain.get('t'), 1)}) | "
                     f"{g.get('eras_won', '—')}/{g.get('eras_complete', '—')} | {_f((m.get('split') or {}).get('t'), 1)} | "
                     f"{'—' if cov is None else f'{cov:.1%}'} | {st} |")
    if view:
        L += ["", f"## Today ({view.get('date')}) — largest expected moves (research display)", ""]
        for lab, hv in view["horizons"].items():
            L += [f"**{lab}** (model {hv['model']})", "", "| Asset | Expected move (1σ) | 90% range | Macro release next session | Earnings window (5d) |",
                  "|---|---|---|---|---|"]
            for i in hv["items"][:15]:
                L.append(f"| {i['asset']} | ±{math.expm1(i['sigma']):.1%} | [{math.expm1(i['lo']):+.1%}, {math.expm1(i['hi']):+.1%}] | "
                         f"{_yn(i.get('macro_next'))} | {_yn(i.get('earn_soon_5d'))} |")
            L.append("")
    return "\n".join(L) + "\n"


def run(db_path: str, progress=None) -> Tuple[dict, dict]:
    from ..data.store import Store
    from .research import Research
    st = Store(db_path)
    try:
        r = Research(st)
        data = build(st, r, progress)
        res = study(data, progress)
        view = today(st, r, res, data)
        st.kv_set(RESEARCH_KEY, res)
        st.kv_set(RESEARCH_KEY + ":today", view)
        return res, view
    finally:
        st.close()
