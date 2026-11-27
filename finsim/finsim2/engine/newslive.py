"""Forward evaluation of the news features (research only).

Protocol: NEWS_SIGNALS_PROTOCOL.md (fixed 2026-09-28, before any data). The features come from data/news.py
(alt_data ``news_dj``, one row set per asset and session); nothing here feeds the Shaffer Score, the Hedge or any
production output, and a passing test promotes nothing.

Until the minimum data exists (≥ 126 sessions of ingestion, ≥ 2,000 asset-sessions with news, ≥ 60 sessions with news
on ≥ 20 assets) the status is ACCUMULATING with the progress counts. After that, five tests with Benjamini-Hochberg at
q = 0.10 across them:

    T1-1D, T1-1W   mean daily cross-sectional rank IC of sent_mean vs the next 1D / 1W log excess return over SPY
                   (sessions with news on ≥ 20 assets; t over sessions, ÷ √5 at 1W for the overlap)
    T2-1D, T2-1W   news intensity vs the stored move-size forecast: QLIKE of r² against ms_sigma² × scale, one scale vs
                   a news / no-news scale fitted on the first half of sessions, gain on the second half (t ≥ 2)
    T3             sign of sent_mean vs sign of the next 1D excess return: hit rate vs 50%, binomial
plus the event tags' conditional mean next returns (reported, not tested). Outcomes run from the close of the
feature's session; prices are exact-date adjusted closes (no forward fill), so a missing close is a missing outcome.
"""
from __future__ import annotations

import datetime as _dt
import math
from typing import Dict, List, Optional, Tuple

EVAL_KEY = "news:eval"
DATASET = "news_dj"
ALL = "news:all"
MIN_SESSIONS = 126
MIN_ASSET_SESSIONS = 2000
MIN_BROAD_SESSIONS = 60
BROAD_ASSETS = 20
FDR_Q = 0.10
HORIZONS = (("1D", 1), ("1W", 5))
FLOOR = 1e-4                         # |r| floor for QLIKE, as engine/movesize.py
RESEARCH_ONLY = "research only — not part of the Shaffer Score"


# ------------------------------------------------------------------ data
def load(store) -> Dict[str, Dict[str, Dict[str, float]]]:
    """{session: {asset: {field: value}}} from alt_data news_dj (asset ``news:all`` = every ingested article)."""
    out: Dict[str, Dict[str, Dict[str, float]]] = {}
    for a, d, f, v, _ in store.alt(DATASET):
        if v is None:
            continue
        out.setdefault(d, {}).setdefault(a, {})[f] = float(v)
    return out


def progress(data: Dict[str, Dict[str, Dict[str, float]]]) -> dict:
    ingest = sum(1 for s in data.values() if (s.get(ALL) or {}).get("n_articles", 0) >= 1)
    per = {d: sum(1 for a, f in s.items() if a != ALL and f.get("n_articles", 0) >= 1) for d, s in data.items()}
    have = {"sessions": ingest, "asset_sessions": sum(per.values()), "broad_sessions": sum(1 for n in per.values() if n >= BROAD_ASSETS)}
    need = {"sessions": MIN_SESSIONS, "asset_sessions": MIN_ASSET_SESSIONS, "broad_sessions": MIN_BROAD_SESSIONS}
    return {"have": have, "need": need, "ready": all(have[k] >= need[k] for k in need),
            "first": min((d for d in data if (data[d].get(ALL) or {}).get("n_articles")), default=None),
            "last": max((d for d in data if (data[d].get(ALL) or {}).get("n_articles")), default=None)}


def _news_day(f: dict) -> bool:
    """The protocol's news day: at least one article and (novelty ≥ 1 or any event tag)."""
    return f.get("n_articles", 0) >= 1 and (f.get("novelty", 0.0) >= 1.0 or any(v >= 1 for k, v in f.items() if k.startswith("ev_")))


# ------------------------------------------------------------------ statistics
def _ranks(xs: List[float]) -> List[float]:
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    r = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        for k in range(i, j + 1):
            r[order[k]] = (i + j) / 2.0 + 1.0
        i = j + 1
    return r


def spearman(x: List[float], y: List[float]) -> Optional[float]:
    if len(x) < 3:
        return None
    rx, ry = _ranks(x), _ranks(y)
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    sxy = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    sxx = sum((a - mx) ** 2 for a in rx)
    syy = sum((b - my) ** 2 for b in ry)
    return sxy / math.sqrt(sxx * syy) if sxx > 0 and syy > 0 else None


def _mean_t(xs: List[float], overlap: int = 1) -> Tuple[Optional[float], Optional[float]]:
    n = len(xs)
    if n < 2:
        return (xs[0] if xs else None), None
    m = sum(xs) / n
    sd = math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1))
    if sd == 0:
        return m, None
    return m, m / (sd / math.sqrt(n)) / math.sqrt(overlap)


def p_two(t: Optional[float]) -> Optional[float]:
    return None if t is None else math.erfc(abs(t) / math.sqrt(2))


def p_upper(t: Optional[float]) -> Optional[float]:
    return None if t is None else 0.5 * math.erfc(t / math.sqrt(2))


def binom_two_sided(k: int, n: int) -> Optional[float]:
    """Two-sided p of k successes in n fair trials (doubled smaller tail; normal approximation above 20,000)."""
    if n <= 0:
        return None
    if n > 20000:
        z = (abs(k - n / 2) - 0.5) / math.sqrt(n / 4)
        return min(1.0, math.erfc(max(z, 0.0) / math.sqrt(2)))
    lp = [math.lgamma(n + 1) - math.lgamma(i + 1) - math.lgamma(n - i + 1) - n * math.log(2) for i in range(n + 1)]
    lo = sum(math.exp(v) for v in lp[:k + 1])
    hi = sum(math.exp(v) for v in lp[k:])
    return min(1.0, 2 * min(lo, hi))


def bh(pvals: Dict[str, Optional[float]], q: float = FDR_Q) -> Dict[str, bool]:
    """Benjamini-Hochberg step-up; a test without a p-value counts as p = 1 (it still counts in m)."""
    items = sorted(((1.0 if p is None else p), k) for k, p in pvals.items())
    m = len(items)
    cut = 0
    for i, (p, _) in enumerate(items, 1):
        if p <= i / m * q:
            cut = i
    return {k: i < cut for i, (_, k) in enumerate(items)}


def qlike(r2: float, v: float) -> float:
    x = max(r2, FLOOR * FLOOR) / v
    return x - math.log(x) - 1.0


# ------------------------------------------------------------------ outcomes
class _Outcomes:
    """Log returns from the close of session i to the close of i + h (exact-date adjusted closes)."""

    def __init__(self, research):
        self.p = research.panel()
        self.cal = self.p.calendar()
        self.pos = {d: i for i, d in enumerate(self.cal)}
        self._s: Dict[str, list] = {}
        self.spy = self._series("SPY")

    def _series(self, a: str) -> list:
        if a not in self._s:
            try:
                self._s[a] = self.p.series(a, "adj_close", max_fill=0)
            except Exception:  # noqa: BLE001 - an asset without prices has no outcomes
                self._s[a] = [None] * len(self.cal)
        return self._s[a]

    def ret(self, a: str, d: str, h: int) -> Optional[float]:
        i = self.pos.get(d)
        if i is None or i + h >= len(self.cal):
            return None
        s = self._series(a)
        x, y = s[i], s[i + h]
        return math.log(y / x) if x and y and x > 0 and y > 0 else None

    def excess(self, a: str, d: str, h: int) -> Optional[float]:
        r, m = self.ret(a, d, h), self.ret("SPY", d, h)
        return None if r is None or m is None else r - m


# ------------------------------------------------------------------ tests
def t1(data, out: _Outcomes, h: int) -> dict:
    ics = []
    for d in sorted(data):
        xs, ys = [], []
        for a, f in data[d].items():
            if a == ALL or f.get("n_articles", 0) < 1 or "sent_mean" not in f:
                continue
            e = out.excess(a, d, h)
            if e is not None:
                xs.append(f["sent_mean"]); ys.append(e)
        if len(xs) >= BROAD_ASSETS:
            ic = spearman(xs, ys)
            if ic is not None:
                ics.append(ic)
    m, t = _mean_t(ics, h)
    return {"sessions": len(ics), "mean_ic": m, "t": t, "p": p_two(t)}


def t2(data, out: _Outcomes, lab: str, h: int) -> dict:
    fld = "ms_sigma_" + lab.lower()
    rows = []                                           # (session, sigma², r², news day)
    for d in sorted(data):
        for a, f in data[d].items():
            s = f.get(fld)
            if a == ALL or not s or s <= 0:
                continue
            r = out.ret(a, d, h)
            if r is not None:
                rows.append((d, s * s, r * r, _news_day(f)))
    sess = sorted({r[0] for r in rows})
    if len(sess) < 4:
        return {"sessions": len(sess), "rows": len(rows), "gain": None, "t": None, "p": None, "note": "not enough sessions with a move-size snapshot"}
    cut = sess[len(sess) // 2]
    first = [r for r in rows if r[0] < cut]
    second = [r for r in rows if r[0] >= cut]
    ratio = lambda rs: sum(max(r2, FLOOR * FLOOR) / v for _, v, r2, _ in rs) / len(rs)  # noqa: E731
    fn, fq = [r for r in first if r[3]], [r for r in first if not r[3]]
    if not fn or not fq or not second:
        return {"sessions": len(sess), "rows": len(rows), "gain": None, "t": None, "p": None, "note": "no news or no no-news rows in the first half"}
    k0, kn, kq = ratio(first), ratio(fn), ratio(fq)
    per: Dict[str, List[float]] = {}
    for d, v, r2, nd in second:
        per.setdefault(d, []).append(qlike(r2, v * k0) - qlike(r2, v * (kn if nd else kq)))
    gains = [sum(x) / len(x) for x in per.values()]
    m, t = _mean_t(gains, h)
    return {"sessions": len(sess), "rows": len(rows), "test_sessions": len(gains), "scale_common": k0, "scale_news": kn, "scale_quiet": kq,
            "news_share": len(fn) / len(first), "gain": m, "t": t, "p": p_upper(t)}


def t3(data, out: _Outcomes) -> dict:
    hit = n = 0
    for d in data:
        for a, f in data[d].items():
            s = f.get("sent_mean")
            if a == ALL or f.get("n_articles", 0) < 1 or not s:
                continue
            e = out.excess(a, d, 1)
            if e:
                n += 1
                hit += (s > 0) == (e > 0)
    return {"n": n, "hits": hit, "hit_rate": hit / n if n else None, "p": binom_two_sided(hit, n) if n else None}


def event_means(data, out: _Outcomes) -> Dict[str, dict]:
    acc: Dict[str, Dict[str, List[float]]] = {}
    for d in data:
        for a, f in data[d].items():
            if a == ALL:
                continue
            for k, v in f.items():
                if k.startswith("ev_") and v >= 1:
                    g = acc.setdefault(k[3:], {"1D": [], "1W": []})
                    for lab, h in HORIZONS:
                        e = out.excess(a, d, h)
                        if e is not None:
                            g[lab].append(e)
    return {k: {lab: {"n": len(v), "mean": sum(v) / len(v) if v else None} for lab, v in g.items()} for k, g in sorted(acc.items())}


def evaluate(store, research, save: bool = True) -> dict:
    """The protocol's status and, once the minimum data exists, its five tests (stored in kv ``news:eval``)."""
    data = load(store)
    pr = progress(data)
    res = {"label": RESEARCH_ONLY, "protocol": "NEWS_SIGNALS_PROTOCOL.md (fixed 2026-09-28)", "progress": pr,
           "evaluated_at": _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()}
    if not pr["ready"]:
        res["status"] = "ACCUMULATING"
    else:
        out = _Outcomes(research)
        tests = {"T1-1D": t1(data, out, 1), "T1-1W": t1(data, out, 5), "T2-1D": t2(data, out, "1D", 1), "T2-1W": t2(data, out, "1W", 5),
                 "T3": t3(data, out)}
        sig = bh({k: v.get("p") for k, v in tests.items()})
        for k, v in tests.items():
            v["bh_significant"] = sig[k]
            if k.startswith("T1"):
                v["passed"] = sig[k]
            elif k.startswith("T2"):
                v["passed"] = sig[k] and (v.get("gain") or 0) > 0 and (v.get("t") or 0) >= 2
            else:
                v["passed"] = sig[k] and (v.get("hit_rate") or 0) > 0.5 and (tests["T1-1D"].get("mean_ic") or 0) > 0
        res.update(tests=tests, events=event_means(data, out), fdr_q=FDR_Q,
                   status="EVIDENCE — RESEARCH ONLY" if any(v["passed"] for v in tests.values()) else "NO EVIDENCE")
    if save:
        store.kv_set(EVAL_KEY, res)
    return res


def summary(store) -> dict:
    """The last evaluation for the API / UI (progress counted fresh, so it is current between daily runs)."""
    saved = store.kv_get(EVAL_KEY)
    pr = progress(load(store))
    if not saved:
        return {"label": RESEARCH_ONLY, "status": "ACCUMULATING" if not pr["ready"] else "NOT RUN", "progress": pr,
                "protocol": "NEWS_SIGNALS_PROTOCOL.md (fixed 2026-09-28)"}
    return {**saved, "progress": pr}
