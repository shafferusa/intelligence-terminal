"""HorizonEngine: how well has each signal predicted each horizon, for one asset?

For signal i and horizon h the evidence is the rank information coefficient
    IC = Corr(rank signal_t, rank fwd_return_{t,t+h}),
the hit rate (how often the signal's side matched the sign of the forward return), a t-statistic and p-value on
the effective number of independent observations n_eff = n / h (overlapping h-day windows are not independent),
the IC in the most recent third of the sample, the IC in each third (stability), and the IC inside each regime.

`usefulness = IC × min(1, |t| / 2)`: an IC with weak evidence is shrunk toward zero. Nothing here is hard-coded;
the ranking of signals comes out of the data.
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence

from ..data.universe import HORIZONS

Series = List[Optional[float]]
MIN_OBS = 60
REGIME_MAX_H = 252          # regime splits for horizons up to 12 months (longer ones have too few independent windows)


# ------------------------------------------------------------------ statistics
def _betacf(a: float, b: float, x: float) -> float:
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    d = 1.0 / (d if abs(d) > 1e-30 else 1e-30)
    h = d
    for m in range(1, 200):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d; d = 1.0 / (d if abs(d) > 1e-30 else 1e-30)
        c = 1.0 + aa / c if abs(c) > 1e-30 else 1e30
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d; d = 1.0 / (d if abs(d) > 1e-30 else 1e-30)
        c = 1.0 + aa / c if abs(c) > 1e-30 else 1e30
        de = d * c
        h *= de
        if abs(de - 1.0) < 3e-12:
            break
    return h


def betainc(a: float, b: float, x: float) -> float:
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    lb = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log(1 - x)
    if x < (a + 1) / (a + b + 2):
        return math.exp(lb) * _betacf(a, b, x) / a
    return 1.0 - math.exp(lb) * _betacf(b, a, 1 - x) / b


def t_pvalue(t: float, df: float) -> float:
    """Two-sided p-value of a Student t statistic."""
    if df <= 0 or t is None or not math.isfinite(t):
        return 1.0
    return betainc(df / 2.0, 0.5, df / (df + t * t))


def ranks(xs: Series) -> Series:
    """Percentile ranks (0..1, ties averaged) of the non-missing values; None stays None."""
    idx = sorted((v, i) for i, v in enumerate(xs) if v is not None)
    out: Series = [None] * len(xs)
    n = len(idx)
    j = 0
    while j < n:
        k = j
        while k + 1 < n and idx[k + 1][0] == idx[j][0]:
            k += 1
        r = (j + k) / 2.0 / max(1, n - 1)
        for m in range(j, k + 1):
            out[idx[m][1]] = r
        j = k + 1
    return out


def _local_ranks(xs: Series, idx: Sequence[int]) -> Series:
    """Ranks of xs over the rows in idx only (other rows stay None)."""
    sub = ranks([xs[i] for i in idx])
    out: Series = [None] * len(xs)
    for i, r in zip(idx, sub):
        out[i] = r
    return out


def pearson_idx(a: Series, b: Series, idx: Sequence[int]) -> Optional[float]:
    n = len(idx)
    if n < 3:
        return None
    sa = sb = saa = sbb = sab = 0.0
    for i in idx:
        x, y = a[i], b[i]
        sa += x; sb += y; saa += x * x; sbb += y * y; sab += x * y
    ma, mb = sa / n, sb / n
    va, vb = saa / n - ma * ma, sbb / n - mb * mb
    if va <= 1e-15 or vb <= 1e-15:
        return None
    return max(-1.0, min(1.0, (sab / n - ma * mb) / math.sqrt(va * vb)))


def evidence(ic: Optional[float], n_eff: float) -> Dict[str, Optional[float]]:
    if ic is None or n_eff < 3:
        return {"t": None, "p": None}
    df = max(1.0, n_eff - 2)
    t = ic * math.sqrt(df / max(1e-12, 1 - ic * ic))
    return {"t": t, "p": t_pvalue(t, df)}


def usefulness(ic: Optional[float], t: Optional[float]) -> Optional[float]:
    if ic is None or t is None:
        return None
    return ic * min(1.0, abs(t) / 2.0)


# ------------------------------------------------------------------ evaluation
def persistence(signal: Series, lag: int = 21) -> float:
    """Decorrelation length of a signal in sessions: −lag / ln ρ(lag). A slow-moving signal (a yield level) carries
    few independent observations even over many days, so it deflates the effective sample below."""
    xs = signal[::1]
    idx = [i for i in range(lag, len(xs)) if xs[i] is not None and xs[i - lag] is not None][::5]
    if len(idx) < 50:
        return 1.0
    a = [xs[i] for i in idx]
    b = [xs[i - lag] for i in idx]
    n = len(a)
    ma, mb = sum(a) / n, sum(b) / n
    va = sum((x - ma) ** 2 for x in a) / n
    vb = sum((x - mb) ** 2 for x in b) / n
    if va <= 0 or vb <= 0:
        return 1.0
    rho = sum((x - ma) * (y - mb) for x, y in zip(a, b)) / n / math.sqrt(va * vb)
    if rho <= 0.05:
        return 1.0
    if rho >= 0.999:
        return 2520.0
    return min(2520.0, -lag / math.log(rho))


def bh_qvalues(pvals: Dict[str, Optional[float]]) -> Dict[str, Optional[float]]:
    """Benjamini–Hochberg false-discovery-rate q-values across the signals tested at one horizon."""
    items = sorted((p, k) for k, p in pvals.items() if p is not None)
    m = len(items)
    out: Dict[str, Optional[float]] = {k: None for k in pvals}
    prev = 1.0
    for rank in range(m, 0, -1):
        p, k = items[rank - 1]
        q = min(prev, p * m / rank)
        out[k] = q
        prev = q
    return out


def evaluate(signal: Series, target: Series, h: int, regimes: Optional[Dict[str, List[Optional[str]]]] = None,
             signal_ranks: Optional[Series] = None, target_ranks: Optional[Series] = None, cutoff: Optional[int] = None,
             persist: float = 1.0, rank_cache: Optional[dict] = None) -> Optional[dict]:
    """Evidence that `signal` (standardised) has predicted `target` (h-session forward return). Only rows < cutoff
    (default: all) are used, so a caller can evaluate as of a past date without looking ahead."""
    n_all = len(signal)
    end = n_all if cutoff is None else min(n_all, cutoff)
    step = 1 if h <= 5 else min(21, max(1, h // 4))
    if cutoff is not None:
        step = max(step, 5)             # as-of-date refits: weekly rows are plenty for weights and keep re-ranking cheap
    # with a cutoff, a row counts only if its target window had closed by then (no lookahead)
    idx = [i for i in range(0, end, step) if signal[i] is not None and target[i] is not None and (cutoff is None or i + h < cutoff)]
    if len(idx) < (MIN_OBS if h <= 252 else 40):
        return None
    if cutoff is not None:
        # as of a past date, rank only the rows known then: ranks over the whole history would carry later data in
        sr = _local_ranks(signal, idx)
        key = (cutoff, len(idx), hash(tuple(idx)))
        tr = rank_cache.get(key) if rank_cache is not None else None
        if tr is None:
            tr = _local_ranks(target, idx)
            if rank_cache is not None:
                rank_cache[key] = tr
    else:
        sr = signal_ranks if signal_ranks is not None else ranks(signal)
        tr = target_ranks if target_ranks is not None else ranks(target)
    ic = pearson_idx(sr, tr, idx)
    if ic is None:
        return None
    n = len(idx) * step
    n_eff = max(1.0, n / max(h, persist))           # overlapping windows and a slow-moving signal both cut independence
    ev = evidence(ic, n_eff)
    direction = 1 if ic >= 0 else -1
    # hit rate: when the signal leaned (|z| > 0.25), did the forward return have the sign it pointed to?
    hits = tot = 0
    for i in idx:
        z = signal[i]
        if abs(z) > 0.25:
            tot += 1
            if (z * direction > 0) == (target[i] > 0):
                hits += 1
    third = len(idx) // 3
    parts = [idx[:third], idx[third:2 * third], idx[2 * third:]] if third >= 20 else []
    ics = [pearson_idx(sr, tr, p) for p in parts]
    ics = [x for x in ics if x is not None]
    stability = (sum(1 for x in ics if (x >= 0) == (ic >= 0)) / len(ics)) if ics else None
    recent = ics[-1] if ics else None
    rec = {"h": h, "ic": ic, "persistence": round(persist, 1), "ic_recent": recent, "ic_thirds": ics, "stability": stability, "hit_rate": (hits / tot) if tot >= 20 else None,
           "t": ev["t"], "p": ev["p"], "n": n, "n_eff": round(n_eff, 1), "direction": direction,
           "usefulness": usefulness(ic, ev["t"]), "first": idx[0], "last": idx[-1]}
    if regimes and h <= REGIME_MAX_H:
        by = {}
        for dim, states in regimes.items():
            for st in set(s for s in states if s is not None):
                sub = [i for i in idx if states[i] == st]
                if len(sub) >= 40:
                    r_ic = pearson_idx(sr, tr, sub)
                    ne = max(1.0, len(sub) * step / max(h, persist))
                    e2 = evidence(r_ic, ne)
                    by[st] = {"ic": r_ic, "n_eff": round(ne, 1), "t": e2["t"], "p": e2["p"], "usefulness": usefulness(r_ic, e2["t"])}
        rec["by_regime"] = by
    return rec


def matrix(signals: Dict[str, Series], price: Series, regimes: Optional[Dict[str, List[Optional[str]]]] = None,
           horizons=HORIZONS, cutoff: Optional[int] = None) -> Dict[str, Dict[str, dict]]:
    """The signal-horizon matrix for one asset: {signal: {horizon label: record}}."""
    from .features import targets
    out: Dict[str, Dict[str, dict]] = {}
    sig_ranks = {k: ranks(v) for k, v in signals.items()}
    pers = {k: persistence(v) for k, v in signals.items()}
    for lab, h in horizons:
        tgt = targets(price, h)
        if sum(1 for v in tgt if v is not None) < 40:
            continue
        tr = ranks(tgt)
        recs = {}
        for name, s in signals.items():
            rec = evaluate(s, tgt, h, regimes, sig_ranks[name], tr, cutoff, pers[name])
            if rec is not None:
                rec["horizon"] = lab
                rec["signal"] = name
                recs[name] = rec
        qs = bh_qvalues({k: r.get("p") for k, r in recs.items()})
        for name, rec in recs.items():
            rec["q"] = qs.get(name)
            out.setdefault(name, {})[lab] = rec
    return out


def best_horizon(row: Dict[str, dict]) -> Optional[str]:
    best, val = None, 0.0
    for lab, rec in row.items():
        u = rec.get("usefulness")
        if u is not None and abs(u) > val:
            best, val = lab, abs(u)
    return best


def leaderboard(mat: Dict[str, Dict[str, dict]], horizon: str, top: int = 30) -> List[dict]:
    """Signals ranked for one horizon by |usefulness| (evidence-weighted IC)."""
    rows = [dict(rec, signal=name) for name, row in mat.items() for lab, rec in row.items() if lab == horizon and rec.get("usefulness") is not None]
    rows.sort(key=lambda r: -abs(r["usefulness"]))
    return rows[:top]
