"""ML Lab — new information research, judged against the frozen benchmark (research only; production is unchanged).

Question: does genuinely new point-in-time information improve Shaffer Alpha (relative ranking), Shaffer Directional
(absolute direction beyond the PIT prior) or Shaffer Hedge modelling (volatility forecasts), *incrementally* — on top of
what the frozen benchmark (production Shaffer, the PIT prior, the current Directional formulation) already contains?

Only information absent from both the 74 production signals and the earlier candidate families is built here. Every
family starts in shadow and is never added to production by this module.

Families (PIT: every value is visible only from the date it was published):
    earnings_events       equities — the earnings reaction day detected point in time (the first session 10–60 days after a
                          quarter end with volume ≥ 2.5 × its 60-day median and an abnormal move ≥ 1.5 σ), its abnormal
                          return and volume decaying over 60 sessions (post-earnings drift), and from SEC first reports
                          (filing date): margin change year on year, revenue-growth acceleration, ROE change
    earnings_surprise     equities — the earlier candidates' EPS / revenue SUE, re-tested under this protocol (not new data)
    credit_quality        every asset (market-wide) — Moody's Baa − Aaa quality spread (5-year z), its 3-month change, the
                          Aaa − 10Y spread's 3-month change
    term_structure        every asset (market-wide) — Kim-Wright 10Y term premium (model estimate, latest vintage) and its
                          3-month change, the 5y5y forward and its change, 5Y real-yield and 10Y breakeven changes
    fx_carry              currencies and currency ETFs — the short-rate differential of the long currency (carry) and
                          carry-adjusted 3-month momentum
    breadth_plus          equities, equity ETFs, indices (market-wide) — share of the universe's stocks above their 50-day
                          average, net new 52-week highs, cross-sectional dispersion, equal-weight minus SPY, breadth thrust
    short_volume          US equities and ETFs — FINRA Reg SHO short-sale volume ratio (5-day mean, z vs 60 days).
                          SHORT-SALE VOLUME, NOT SHORT INTEREST. Starts 2019: LIMITED HISTORY track only.
Market-wide families enter the Alpha test interacted with the asset's PIT beta (a value common to every asset cannot
change a cross-sectional ranking on its own).

Tests for every family × horizon, on identical records, with weights frozen before each era (same eras and split):
    Alpha        score = ridge fit of the excess-return target on [production score, family]; paired Δ rank IC and Δ IC
                 against the production score
    Directional  logistic [1, μ/σ, raw/100, family] against the prior-only model [1, μ/σ] and the current Directional
                 formulation [1, μ/σ, raw/100]: paired Brier, log loss, accuracy; balanced accuracy, precision, recall,
                 AUC, calibration of the new model
    Hedge        log realised volatility over the horizon on [baseline, family] against the baseline alone — log
                 trailing 63-day and 21-day volatility and log VIX, what the Shaffer Hedge already sees (strengthened
                 2026-09-25 after a 1W trial showed trailing volatility alone was too easy to beat, before any full run):
                 paired squared error (1W and longer)
Gates, fixed 2026-09-25 before any result (the historical track):
    enough       ≥ 3 complete test eras (≥ 200 records each) and ≥ 2,000 walk-forward records
    Alpha        G1 walk-forward Δ rank IC t ≥ 2 and Δ IC ≥ 0; G2 Δ rank IC > 0 in ≥ 3 complete eras, 2018 split t ≥ 1
    Directional  gate v2 — G1 vs prior-only: Brier gain t ≥ 2, log-loss gain > 0, accuracy gain > 0, and vs the current
                 formulation Brier gain t ≥ 2; G2 Brier gain vs prior-only > 0 in ≥ 3 eras, split t ≥ 1
    Hedge        G1 walk-forward squared-error gain t ≥ 2; G2 > 0 in ≥ 3 eras, split t ≥ 1
    FDR          Benjamini-Hochberg at q = 0.10 over every family × horizon × target G1 test (one-sided p from t);
                 a test passes only with G1, G2 and FDR. Family status: SHADOW if any test passes, else NO INCREMENTAL
                 VALUE. Individual features are reported with their own BH correction, never admitted one by one.
LIMITED HISTORY track (data starting after 2018-01-01): the same tests on the eras the data covers, reported as
    LIMITED HISTORY / LIVE-SHADOW ONLY — never as historically verified, and the historical gates are not relaxed.
"""
from __future__ import annotations

import bisect
import math
import random
import statistics
import time
from typing import Dict, List, Optional, Tuple

from . import directional as D
from . import weights as W
from .lab import LAB_HORIZONS
from .weights import ERAS, SPLIT, Rec, _clustered_mean

Series = List[Optional[float]]
RESEARCH_KEY = "lab:newinfo"
LIMITED_FROM = "2018-01-01"
FDR_Q = 0.10
MAX_TRAIN = 40000                 # logistic fits use at most this many training records (fixed-seed sample)
MIN_ERA_TEST = 200

FAMILIES: Dict[str, dict] = {
    "earnings_events": {
        "label": "Earnings events", "tier": 2, "track": "historical", "wide": False,
        "source": "own price / volume panel (event day, from 2000) + SEC first-reported filings (from 2009)",
        "pit": "event detected on its own close; filing figures from the filing date", "quality": "high",
        "features": ["pead_return", "pead_volume", "margin_yoy", "growth_accel", "roe_change"]},
    "earnings_surprise": {
        "label": "Earnings surprise (re-test of candidates)", "tier": 2, "track": "historical", "wide": False,
        "source": "SEC first-reported filings (from 2009)", "pit": "filing date", "quality": "high",
        "features": ["sue_eps", "sue_rev"], "retest": True},
    "credit_quality": {
        "label": "Credit quality spreads", "tier": 1, "track": "historical", "wide": True,
        "source": "FRED DBAA, DAAA, AAA10Y (1983 →)", "pit": "daily, 1-day lag", "quality": "high",
        "features": ["quality_spread_z", "d_quality_spread_3m", "d_aaa_spread_3m"]},
    "term_structure": {
        "label": "Term premium, forwards, real yields", "tier": 1, "track": "historical", "wide": True,
        "source": "FRED THREEFYTP10 (1990 →), DGS5 / DGS10, DFII5 and T10YIE (2003 →)",
        "pit": "daily; term premium is a re-estimated model (latest vintage, 7-day lag)", "quality": "medium",
        "features": ["term_premium", "d_term_premium_3m", "fwd_5y5y", "d_fwd_5y5y_3m", "d_real5_3m", "d_breakeven10_3m"]},
    "fx_carry": {
        "label": "FX carry", "tier": 2, "track": "historical", "wide": False,
        "source": "FRED DFF, ECBDFR, IUDSOIA (daily); IRSTCI01 AU / CA / CH / JP (monthly first releases, 2013 →)",
        "pit": "daily rates 1-day lag; monthly first-release dates", "quality": "high (EUR, GBP); limited before 2013 elsewhere",
        "features": ["carry", "carry_momentum"]},
    "breadth_plus": {
        "label": "Richer breadth (FinSim2 universe)", "tier": 2, "track": "historical", "wide": True,
        "source": "own price panel — 45 large-cap US equities (not the whole market)", "pit": "daily close", "quality": "medium (narrow universe)",
        "features": ["pct_above_50d", "net_new_highs", "dispersion_21d", "ew_minus_spy_63d", "breadth_thrust_10d"]},
    "short_volume": {
        "label": "Short-sale volume (FINRA Reg SHO) — not short interest", "tier": 1, "track": "limited", "wide": False,
        "source": "FINRA cdn.finra.org Reg SHO daily files (2019 →)", "pit": "published after the close; used from the next day",
        "quality": "medium (short-marked volume includes market-making)", "features": ["short_ratio_5d", "short_ratio_z"]},
}
BLOCKED = [
    {"family": "analyst_revisions", "label": "Analyst expectations / revisions", "tier": 1,
     "reason": "no point-in-time estimate history on the available tiers (Finnhub estimates / revisions 403; Alpha Vantage 25 requests a day)"},
    {"family": "options_surface", "label": "Option surface / skew", "tier": 1,
     "reason": "no historical chains (Yahoo options needs authentication; Cboe chain statistics retired); pricing stays MODEL-PRICED — FLAT VOLATILITY ASSUMPTION"},
    {"family": "futures_curves", "label": "Dated futures curves", "tier": 1,
     "reason": "only unexpired contracts are downloadable, so past curves cannot be rebuilt"},
    {"family": "cftc_positioning", "label": "CFTC positioning", "tier": 1, "reason": "www.cftc.gov is not on the allowed-domain list"},
    {"family": "short_interest", "label": "Short interest", "tier": 1, "reason": "only the last year is available (Nasdaq)"},
    {"family": "credit_oas_cds", "label": "IG / HY OAS history, CDS, CDX, ratings", "tier": 1,
     "reason": "ICE OAS on FRED is limited to 3 years; no free CDS / ratings source"},
    {"family": "flows", "label": "ETF / fund flows, creations / redemptions", "tier": 2, "reason": "no free point-in-time source"},
    {"family": "commodity_fundamentals", "label": "EIA inventories, storage, rig counts, crop reports", "tier": 2,
     "reason": "api.eia.gov / USDA / Baker Hughes are not on the allowed-domain list (the weekly EIA series are not on FRED)"},
    {"family": "fx_forwards", "label": "FX forward points, inflation differentials", "tier": 2,
     "reason": "no forward-point source; foreign CPI on FRED is stale (UK to 2025-03, Japan to 2021)"},
]
FX_CARRY = {  # long currency's rate − funding currency's rate (percent); ETFs map to their pair
    "EURUSD": ("ECBDFR", "DFF"), "FXE": ("ECBDFR", "DFF"), "GBPUSD": ("IUDSOIA", "DFF"), "FXB": ("IUDSOIA", "DFF"),
    "AUDUSD": ("IRSTCI01AUM156N", "DFF"), "USDJPY": ("DFF", "IRSTCI01JPM156N"), "FXY": ("IRSTCI01JPM156N", "DFF"),
    "USDCAD": ("DFF", "IRSTCI01CAM156N"), "USDCHF": ("DFF", "IRSTCI01CHM156N"), "UUP": ("DFF", "ECBDFR")}


def applies(family: str, meta: dict) -> bool:
    cls, sec, a = meta.get("asset_class"), meta.get("sector") or "", meta.get("id")
    if family in ("earnings_events", "earnings_surprise"):
        return cls == "EQUITY"
    if family == "fx_carry":
        return a in FX_CARRY
    if family == "breadth_plus":
        return cls in ("EQUITY", "INDEX") or (cls == "ETF" and sec not in ("Fixed Income", "Currency", "Crypto", "Volatility")
                                             and a not in D.COMMODITY_ETFS)
    if family == "short_volume":
        return cls in ("EQUITY", "ETF")
    return True                                            # market-wide macro families


# ------------------------------------------------------------------ small series helpers (all backward-looking)
def _logret(px: Series) -> Series:
    return D._logret(px)


def _lag_diff(x: Series, k: int) -> Series:
    return [(x[i] - x[i - k]) if i >= k and x[i] is not None and x[i - k] is not None else None for i in range(len(x))]


def _roll_z(x: Series, w: int = 1260, min_n: int = 252) -> Series:
    out: Series = [None] * len(x)
    pre = D._Prefix(x)
    for i in range(len(x)):
        if x[i] is None:
            continue
        n, m, v, *_ = pre.window(i - 1, w) if i else (0, None, None)
        if n and n >= min_n and v and v > 0:
            out[i] = max(-4.0, min(4.0, (x[i] - m) / math.sqrt(v)))
    return out


def _combine(a: Series, b: Series, f) -> Series:
    return [f(x, y) if x is not None and y is not None else None for x, y in zip(a, b)]


def _hold(events: List[Tuple[str, float]], cal: List[str], hold: int = 63) -> Series:
    out: Series = [None] * len(cal)
    for avail, v in sorted(events):
        i = bisect.bisect_left(cal, avail)
        for j in range(i, min(len(cal), i + hold)):
            out[j] = v
    return out


# ------------------------------------------------------------------ family builders: {feature: Series on the calendar}
class Builder:
    """Computes family features per asset on the panel calendar (cached per panel)."""

    def __init__(self, research, store):
        self.research, self.store = research, store
        self.panel = research.panel()
        self.cal = self.panel.calendar()
        self.n = len(self.cal)
        self._macro: Dict[str, Series] = {}
        self._wide: Dict[str, Dict[str, Series]] = {}

    def macro(self, sid: str) -> Series:
        if sid not in self._macro:
            try:
                self._macro[sid] = self.panel.macro(sid)
            except Exception:  # noqa: BLE001
                self._macro[sid] = [None] * self.n
        return self._macro[sid]

    def series(self, a: str, field: str = "adj_close") -> Series:
        try:
            return self.panel.series(a, field)
        except Exception:  # noqa: BLE001
            return [None] * self.n

    # ---- market-wide
    def credit_quality(self) -> Dict[str, Series]:
        q = _combine(self.macro("DBAA"), self.macro("DAAA"), lambda b, a: b - a)
        return {"quality_spread_z": _roll_z(q), "d_quality_spread_3m": _lag_diff(q, 63), "d_aaa_spread_3m": _lag_diff(self.macro("AAA10Y"), 63)}

    def term_structure(self) -> Dict[str, Series]:
        tp = self.macro("THREEFYTP10")
        fwd = _combine(self.macro("DGS10"), self.macro("DGS5"), lambda t, f: 2 * t - f)
        return {"term_premium": tp, "d_term_premium_3m": _lag_diff(tp, 63), "fwd_5y5y": fwd, "d_fwd_5y5y_3m": _lag_diff(fwd, 63),
                "d_real5_3m": _lag_diff(self.macro("DFII5"), 63), "d_breakeven10_3m": _lag_diff(self.macro("T10YIE"), 63)}

    def breadth_plus(self) -> Dict[str, Series]:
        eq = [a["id"] for a in self.store.assets() if a.get("asset_class") == "EQUITY"]
        px = {a: self.series(a) for a in eq}
        spy = self.series("SPY")
        n = self.n
        above = [0] * n; cnt = [0] * n; hi = [0] * n; lo = [0] * n; cnt252 = [0] * n
        r21: List[List[float]] = [[] for _ in range(n)]
        r63: List[List[float]] = [[] for _ in range(n)]
        for a, p in px.items():
            pre = D._Prefix(p)
            from collections import deque
            dmax: deque = deque(); dmin: deque = deque()
            for i in range(n):
                v = p[i]
                if v is None:
                    continue
                while dmax and (p[dmax[-1]] is None or p[dmax[-1]] <= v):
                    dmax.pop()
                while dmin and (p[dmin[-1]] is None or p[dmin[-1]] >= v):
                    dmin.pop()
                dmax.append(i); dmin.append(i)
                while dmax[0] <= i - 252:
                    dmax.popleft()
                while dmin[0] <= i - 252:
                    dmin.popleft()
                k, m, *_ = pre.window(i, 50)
                if k and k >= 45:
                    cnt[i] += 1; above[i] += 1 if v > m else 0
                if i >= 252 and pre.window(i, 252)[0] >= 240:
                    cnt252[i] += 1
                    hi[i] += 1 if dmax[0] == i else 0
                    lo[i] += 1 if dmin[0] == i else 0
                if i >= 21 and p[i - 21]:
                    r21[i].append(math.log(v / p[i - 21]))
                if i >= 63 and p[i - 63]:
                    r63[i].append(math.log(v / p[i - 63]))
        pct = [above[i] / cnt[i] if cnt[i] >= 20 else None for i in range(n)]
        out = {"pct_above_50d": pct,
               "net_new_highs": [(hi[i] - lo[i]) / cnt252[i] if cnt252[i] >= 20 else None for i in range(n)],
               "dispersion_21d": [statistics.pstdev(r21[i]) if len(r21[i]) >= 20 else None for i in range(n)],
               "ew_minus_spy_63d": [(sum(r63[i]) / len(r63[i]) - math.log(spy[i] / spy[i - 63]))
                                    if len(r63[i]) >= 20 and i >= 63 and spy[i] and spy[i - 63] else None for i in range(n)],
               "breadth_thrust_10d": _lag_diff(pct, 10)}
        return out

    def wide(self, family: str) -> Dict[str, Series]:
        if family not in self._wide:
            self._wide[family] = getattr(self, family)()
        return self._wide[family]

    # ---- per asset
    def earnings_events(self, a: str) -> Dict[str, Series]:
        from .candidates import quarterly_values
        cal, n = self.cal, self.n
        p, vol, spy = self.series(a), self.series(a, "volume"), self.series("SPY")
        r, rm = _logret(p), _logret(spy)
        ar = [(x - y) if x is not None and y is not None else None for x, y in zip(r, rm)]
        arp = D._Prefix(ar)
        pead: Series = [None] * n
        pvol: Series = [None] * n
        last_event = None                                  # (index, standardised return, log volume ratio)
        done_quarter = None
        for i in range(n):
            if p[i] is None:
                continue
            d = cal[i]
            y, m = int(d[:4]), int(d[5:7])
            qm = ((m - 1) // 3) * 3                        # the calendar quarter end before this date
            qend = f"{y - 1}-12-31" if qm == 0 else f"{y}-{qm:02d}-{[31, 30, 30, 31][qm // 3 - 1]:02d}"
            days = (_date(d) - _date(qend)).days
            if 10 <= days <= 60 and done_quarter != qend and i >= 61 and vol[i]:
                past = sorted(v for v in vol[i - 60:i] if v)
                k, _, v_ar, *_ = arp.window(i - 1, 63)
                if len(past) >= 40 and k and k >= 40 and v_ar and ar[i] is not None:
                    ratio = vol[i] / past[len(past) // 2]
                    z = ar[i] / math.sqrt(v_ar)
                    if ratio >= 2.5 and abs(z) >= 1.5:
                        last_event = (i, max(-6.0, min(6.0, z)), math.log(ratio))
                        done_quarter = qend
            if last_event is not None:
                age = i - last_event[0]
                w = max(0.0, 1.0 - age / 60.0)
                pead[i] = last_event[1] * w
                pvol[i] = last_event[2] * w
            elif i > 300:
                pead[i] = pvol[i] = 0.0
        out = {"pead_return": pead, "pead_volume": pvol}
        try:
            rows = self.store.fundamentals(a)
        except Exception:  # noqa: BLE001
            rows = []
        rev = {e: (f, v) for e, f, v in quarterly_values(rows, "revenue")}
        ni = {e: (f, v) for e, f, v in quarterly_values(rows, "net_income")}
        eqv = {str(x["period_end"])[:10]: (str(x["filed"])[:10], x["value"]) for x in rows if x.get("concept") == "equity" and x.get("value")}

        def year_ago(end, dct):
            e = _date(end)
            return next((dct[k] for k in dct if 350 <= (e - _date(k)).days <= 380), None)

        def quarter_ago(end, dct):
            e = _date(end)
            return next((k for k in dct if 80 <= (e - _date(k)).days <= 100), None)
        mg, ga, rc = [], [], []
        for end, (filed, rv) in rev.items():
            if end in ni and rv:
                ya_r, ya_n = year_ago(end, rev), year_ago(end, ni)
                if ya_r and ya_n and ya_r[1]:
                    mg.append((filed, ni[end][1] / rv - ya_n[1] / ya_r[1]))
            ya = year_ago(end, rev)
            pq = quarter_ago(end, rev)
            if ya and ya[1] and pq:
                ya_p = year_ago(pq, rev)
                if ya_p and ya_p[1] and rev[pq][1]:
                    ga.append((max(filed, rev[pq][0]), (rv / ya[1] - 1) - (rev[pq][1] / ya_p[1] - 1)))
        for end, (filed, nv) in ni.items():
            q = eqv.get(end)
            ya = year_ago(end, ni)
            if q and ya and q[1]:
                qa = next((eqv[k] for k in eqv if 350 <= (_date(end) - _date(k)).days <= 380), None)
                if qa and qa[1]:
                    rc.append((max(filed, q[0]), nv / q[1] - ya[1] / qa[1]))
        clip = lambda ev: [(f, max(-1.0, min(1.0, v))) for f, v in ev]  # noqa: E731
        out.update(margin_yoy=_hold(clip(mg), cal), growth_accel=_hold(clip(ga), cal), roe_change=_hold(clip(rc), cal))
        return out

    def earnings_surprise(self, a: str) -> Dict[str, Series]:
        from .candidates import earnings_surprise
        return earnings_surprise(self.panel, a) or {}

    def fx_carry(self, a: str) -> Dict[str, Series]:
        long_r, fund_r = FX_CARRY[a]
        carry = _combine(self.macro(long_r), self.macro(fund_r), lambda x, y: x - y)
        p = self.series(a)
        mom = [(math.log(p[i] / p[i - 63]) + carry[i] / 100.0 * 63 / 252) if i >= 63 and p[i] and p[i - 63] and carry[i] is not None else None
               for i in range(self.n)]
        return {"carry": carry, "carry_momentum": mom}

    def short_volume(self, a: str) -> Dict[str, Series]:
        from ..data.finra import DATASET
        rows = self.store.alt(DATASET, a)
        day: Dict[str, Dict[str, float]] = {}
        for _, d, f, v, _pub in rows:
            day.setdefault(d, {})[f] = v
        obs = sorted((d, x["short"] / x["total"]) for d, x in day.items() if x.get("total") and x.get("short") is not None)
        dates = [d for d, _ in obs]
        vals = [v for _, v in obs]
        s5: Series = [None] * self.n
        sz: Series = [None] * self.n
        for i, d in enumerate(self.cal):
            k = bisect.bisect_left(dates, d)          # files dated strictly before d (each is published the next day)
            if k >= 5:
                m5 = sum(vals[k - 5:k]) / 5
                s5[i] = m5
                if k >= 60:
                    w = vals[k - 60:k]
                    mu = sum(w) / 60
                    sd = math.sqrt(sum((x - mu) ** 2 for x in w) / 60)
                    sz[i] = max(-4.0, min(4.0, (m5 - mu) / sd)) if sd > 0 else 0.0
        return {"short_ratio_5d": s5, "short_ratio_z": sz}

    def features(self, family: str, meta: dict) -> Optional[Dict[str, Series]]:
        if not applies(family, meta):
            return None
        if FAMILIES[family]["wide"]:
            return self.wide(family)
        return getattr(self, family)(meta["id"])


def _date(s: str):
    import datetime as _dt
    return _dt.date.fromisoformat(s[:10])


# ------------------------------------------------------------------ attach features to the research records
def attach(recs: List[Rec], builder: Builder, family: str) -> List[Tuple[Rec, List[Optional[float]]]]:
    """(record, feature vector at the record's date) for records the family applies to and has at least one value."""
    feats = FAMILIES[family]["features"]
    cache: Dict[str, Optional[Dict[str, Series]]] = {}
    out = []
    for r in recs:
        if r.asset not in cache:
            cache[r.asset] = builder.features(family, r.meta)
        f = cache[r.asset]
        if not f:
            continue
        i = (r.ext or {}).get("i")
        if i is None:
            continue
        v = [(f.get(k) or [None] * builder.n)[i] for k in feats]
        if any(x is not None for x in v):
            out.append((r, v))
    return out


# ------------------------------------------------------------------ fitting helpers
def _standardise(train: List[List[Optional[float]]]) -> List[Tuple[float, float]]:
    P = len(train[0]) if train else 0
    st = []
    for k in range(P):
        xs = [row[k] for row in train if row[k] is not None]
        if len(xs) < 30:
            st.append((0.0, 0.0)); continue
        m = sum(xs) / len(xs)
        sd = math.sqrt(sum((x - m) ** 2 for x in xs) / len(xs))
        st.append((m, sd))
    return st


def _z(v: List[Optional[float]], st) -> List[float]:
    return [max(-4.0, min(4.0, (x - m) / sd)) if x is not None and sd > 1e-12 else 0.0 for x, (m, sd) in zip(v, st)]


def _ridge(X: List[List[float]], y: List[float], lam: float = 10.0) -> List[float]:
    P = len(X[0])
    A = [[lam if i == j else 0.0 for j in range(P)] for i in range(P)]
    b = [0.0] * P
    for x, t in zip(X, y):
        for i in range(P):
            xi = x[i]
            b[i] += xi * t
            Ai = A[i]
            for j in range(i, P):
                Ai[j] += xi * x[j]
    for i in range(P):
        for j in range(i):
            A[i][j] = A[j][i]
    return D._solve(A, b)


def _dot(w, x):
    return sum(a * b for a, b in zip(w, x))


def _future_log_vol(r: Rec, rets: Series, h: int) -> Optional[float]:
    i = (r.ext or {}).get("i")
    seg = [x for x in rets[i + 1:i + 1 + h] if x is not None] if i is not None else []
    if len(seg) < max(3, h // 2):
        return None
    v = math.sqrt(sum(x * x for x in seg) / len(seg))
    return math.log(v) if v > 0 else None


def _weekly_rank_ic(rows: List[Rec], key, tkey) -> Dict[int, float]:
    weeks: Dict[int, list] = {}
    for r in rows:
        weeks.setdefault(r.wk, []).append(r)
    out = {}
    for wk, rs in weeks.items():
        if len(rs) < 8:
            continue
        a, b = W._ranks([key(r) for r in rs]), W._ranks([tkey(r) for r in rs])
        m = (len(rs) - 1) / 2.0
        sa = math.sqrt(sum((x - m) ** 2 for x in a)); sb = math.sqrt(sum((x - m) ** 2 for x in b))
        if sa > 0 and sb > 0:
            out[wk] = sum((x - m) * (y - m) for x, y in zip(a, b)) / (sa * sb)
    return out


def _paired_stat(by: Dict[int, List[float]], h: int) -> dict:
    c = _clustered_mean(by, h)
    return {"mean": c["mean"], "t": (c["mean"] / c["se"]) if c["mean"] is not None and c["se"] else None, "weeks": c["weeks"]}


# ------------------------------------------------------------------ one family at one horizon
def _vol_base(r: Rec, ctx: dict, h: int) -> Optional[List[float]]:
    """The volatility forecast any family must beat: log trailing 63-day and 21-day realised volatility and log VIX,
    all scaled to the horizon (what the Shaffer Hedge can already see)."""
    i = (r.ext or {}).get("i")
    s63 = (r.ext or {}).get("s")
    rs = ctx["rets"].get(r.asset) or []
    seg = [x for x in rs[max(0, i - 20):i + 1] if x is not None] if i is not None else []
    vix = ctx["vix"][i] if i is not None and i < len(ctx["vix"]) else None
    if not s63 or len(seg) < 15 or not vix:
        return None
    s21 = math.sqrt(sum(x * x for x in seg) / len(seg)) * math.sqrt(h)
    if s21 <= 0:
        return None
    return [1.0, math.log(s63 / math.sqrt(h)), math.log(s21 / math.sqrt(h)), math.log(vix / 100.0 / math.sqrt(252))]


def _eval_period(train, test, h, wide, ctx, seed):
    """Fit on `train`, score `test` (lists of (rec, raw feature vector)); returns per-record predictions."""
    st = _standardise([v for _, v in train])
    rnd = random.Random(seed)
    sub = train if len(train) <= MAX_TRAIN else rnd.sample(train, MAX_TRAIN)

    def xa(r, v):                                        # alpha design: market-wide features interact with PIT beta
        z = _z(v, st)
        b = (r.ext or {}).get("beta")
        return [r.raw / 100.0] + ([x * (b if b is not None else 1.0) for x in z] if wide else z)
    wa = _ridge([xa(r, v) for r, v in train], [D.y_alpha(r) for r, _ in train])
    zp = lambda r: D.z_of(r, D.MAIN_PRIOR) or 0.0       # noqa: E731
    X0 = [[1.0, zp(r)] for r, _ in sub]
    X1 = [[1.0, zp(r), r.raw / 100.0] for r, _ in sub]
    X2 = [[1.0, zp(r), r.raw / 100.0] + _z(v, st) for r, v in sub]
    o = [D._obs(r) for r, _ in sub]
    w0 = D.logistic(X0, o, [0.0] * 2, 1.0, 1.0, iters=10)
    w1 = D.logistic(X1, o, [0.0] * 3, 1.0, 1.0, iters=10)
    w2 = D.logistic(X2, o, [0.0] * len(X2[0]), 1.0, 1.0, iters=10)
    vol = None
    rets = ctx["rets"]
    if h >= 5:
        vt = [(r, v, _vol_base(r, ctx, h), _future_log_vol(r, rets[r.asset], h)) for r, v in train]
        vt = [(r, v, b, t) for r, v, b, t in vt if b is not None and t is not None]
        if len(vt) >= 500:
            b0 = _ridge([b for _, _, b, _ in vt], [t for *_, t in vt], 1e-6)
            b1 = _ridge([b + _z(v, st) for _, v, b, _ in vt], [t for *_, t in vt], 1.0)
            vol = (b0, b1)
    out = []
    for r, v in test:
        z = _z(v, st)
        rec = {"r": r, "alpha": _dot(wa, xa(r, v)), "p0": D._sig(_dot(w0, [1.0, zp(r)])), "p1": D._sig(_dot(w1, [1.0, zp(r), r.raw / 100.0])),
               "p2": D._sig(_dot(w2, [1.0, zp(r), r.raw / 100.0] + z))}
        if vol:
            x0, t = _vol_base(r, ctx, h), _future_log_vol(r, rets[r.asset], h)
            if x0 is not None and t is not None:
                rec["v"] = (t, _dot(vol[0], x0), _dot(vol[1], x0 + z))
        out.append(rec)
    return out, {"alpha_w": wa, "dir_w": w2, "features_std": st}


def _metrics(preds: List[dict], h: int) -> dict:
    """Alpha, Directional and Hedge statistics of one set of test predictions (paired on identical records)."""
    if not preds:
        return {"n": 0}
    rs = [p["r"] for p in preds]
    pm = {id(p["r"]): p for p in preds}
    # alpha: weekly cross-sectional rank IC, challenger vs production, and per-asset IC
    ric_c = _weekly_rank_ic(rs, lambda r: pm[id(r)]["alpha"], D.y_alpha)
    ric_p = _weekly_rank_ic(rs, lambda r: r.raw, D.y_alpha)
    d_ric = {w: [ric_c[w] - ric_p[w]] for w in ric_c if w in ric_p}
    pp, pc = D._std_prods_t(rs, lambda r: r.raw, D.y_alpha), D._std_prods_t(rs, lambda r: pm[id(r)]["alpha"], D.y_alpha)
    d_ic: Dict[int, List[float]] = {}
    for a in set(pp) & set(pc):
        mb = dict(pp[a])
        for w, v in pc[a]:
            if w in mb:
                d_ic.setdefault(w, []).append(v - mb[w])
    ric_c_m = _clustered_mean({w: [v] for w, v in ric_c.items()}, h)["mean"]
    ric_p_m = _clustered_mean({w: [v] for w, v in ric_p.items()}, h)["mean"]
    # directional: new (p2) vs prior-only (p0) and vs the current formulation (p1)
    ll = lambda p, o: -(o * math.log(min(1 - 1e-6, max(1e-6, p))) + (1 - o) * math.log(min(1 - 1e-6, max(1e-6, 1 - p))))  # noqa: E731
    b20: Dict[int, List[float]] = {}; b21: Dict[int, List[float]] = {}; l20: Dict[int, List[float]] = {}; a20: Dict[int, List[float]] = {}
    for p in preds:
        r, o = p["r"], D._obs(p["r"])
        b20.setdefault(r.wk, []).append((p["p0"] - o) ** 2 - (p["p2"] - o) ** 2)
        b21.setdefault(r.wk, []).append((p["p1"] - o) ** 2 - (p["p2"] - o) ** 2)
        l20.setdefault(r.wk, []).append(ll(p["p0"], o) - ll(p["p2"], o))
        if p["p0"] != 0.5 and p["p2"] != 0.5:
            a20.setdefault(r.wk, []).append((1.0 if (p["p2"] > 0.5) == bool(o) else 0.0) - (1.0 if (p["p0"] > 0.5) == bool(o) else 0.0))
    new = D.dir_metrics(rs, [pm[id(r)]["p2"] for r in rs], h)
    pri = D.dir_metrics(rs, [pm[id(r)]["p0"] for r in rs], h)
    # hedge: squared-error gain of the volatility forecast
    vg: Dict[int, List[float]] = {}
    for p in preds:
        if "v" in p:
            t, f0, f1 = p["v"]
            vg.setdefault(p["r"].wk, []).append((t - f0) ** 2 - (t - f1) ** 2)
    return {"n": len(preds), "assets": len({r.asset for r in rs}),
            "alpha": {"rank_ic": ric_c_m, "rank_ic_production": ric_p_m, "d_rank_ic": _paired_stat(d_ric, h), "d_ic": _paired_stat(d_ic, h)},
            "directional": {"brier_vs_prior": _paired_stat(b20, h), "brier_vs_current": _paired_stat(b21, h),
                            "logloss_vs_prior": _paired_stat(l20, h), "acc_vs_prior": _paired_stat(a20, h),
                            "new": {k: new.get(k) for k in ("accuracy", "balanced_accuracy", "bull_precision", "bear_precision", "bull_recall",
                                                            "bear_recall", "brier", "log_loss", "auc", "ece", "base_rate")},
                            "prior_accuracy": pri.get("accuracy"), "excess_vs_prior": (new.get("accuracy") - pri["accuracy"]) if new.get("accuracy") is not None and pri.get("accuracy") is not None else None},
            "hedge": {"vol_mse_gain": _paired_stat(vg, h), "n": sum(len(v) for v in vg.values())} if vg else None}


def _regimes(preds: List[dict], h: int) -> Dict[str, dict]:
    """Directional Brier gain vs the prior and alpha Δ rank IC within each regime state (n_eff ≥ 100 only)."""
    dims = ("market", "volatility", "rates", "growth")
    out: Dict[str, dict] = {}
    for k, dm in enumerate(dims):
        states = sorted({(p["r"].reg or (None,) * 4)[k] for p in preds} - {None})
        for s in states:
            sub = [p for p in preds if (p["r"].reg or (None,) * 4)[k] == s]
            weeks = len({p["r"].wk for p in sub})
            n_eff = weeks * 5.0 / max(5.0, float(h))
            if n_eff < 100:
                out[s] = {"n_eff": n_eff, "insufficient": True}
                continue
            m = _metrics(sub, h)
            out[s] = {"n_eff": n_eff, "brier_vs_prior": m["directional"]["brier_vs_prior"], "d_rank_ic": m["alpha"]["d_rank_ic"]}
    return out


def study_family(recs: List[Rec], builder: Builder, family: str, h: int, rets) -> dict:
    """`rets`: {asset: daily log returns} or a context {"rets": …, "vix": …}."""
    ctx = rets if isinstance(rets, dict) and "rets" in rets else {"rets": rets, "vix": getattr(builder, "vix", None) or []}
    fam = FAMILIES[family]
    rows = attach(recs, builder, family)
    res = {"family": family, "records": len(rows), "assets": len({r.asset for r, _ in rows}),
           "first": min((r.date for r, _ in rows), default=None), "eras": []}
    if len(rows) < 1000:
        res["status_reason"] = "fewer than 1,000 records with data"
        return res
    cov = [sum(1 for _, v in rows if v[k] is not None) / len(rows) for k in range(len(fam["features"]))]
    res["coverage"] = dict(zip(fam["features"], cov))
    wf: List[dict] = []
    for k, (a, b) in enumerate(ERAS):
        train = [(r, v) for r, v in rows if r.end < a]
        test = [(r, v) for r, v in rows if a <= r.date < b]
        if len(train) < 1000 or len(test) < MIN_ERA_TEST:
            res["eras"].append({"from": a, "to": b, "status": "insufficient", "train": len(train), "test": len(test)})
            continue
        preds, _ = _eval_period(train, test, h, fam["wide"], ctx, seed=k)
        res["eras"].append({"from": a, "to": b, "train": len(train), "test": len(test), **_metrics(preds, h)})
        wf += preds
    res["walkforward"] = _metrics(wf, h) if wf else {"n": 0}
    res["regimes"] = _regimes(wf, h) if wf else {}
    train = [(r, v) for r, v in rows if r.end < SPLIT]
    test = [(r, v) for r, v in rows if r.date >= SPLIT]
    if len(train) >= 1000 and len(test) >= MIN_ERA_TEST:
        res["split"] = _metrics(_eval_period(train, test, h, fam["wide"], ctx, seed=99)[0], h)
    # individual features (alpha, univariate on top of production), for the multiple-testing report
    res["features"] = {}
    for j, name in enumerate(fam["features"]):
        one = [(r, [v[j]]) for r, v in rows if v[j] is not None]
        preds = []
        for k, (a, b) in enumerate(ERAS):
            tr = [(r, v) for r, v in one if r.end < a]
            te = [(r, v) for r, v in one if a <= r.date < b]
            if len(tr) < 1000 or len(te) < MIN_ERA_TEST:
                continue
            st = _standardise([v for _, v in tr])
            xa = lambda r, v: [r.raw / 100.0] + [z * (((r.ext or {}).get("beta") or 1.0) if fam["wide"] else 1.0) for z in _z(v, st)]  # noqa: E731
            w = _ridge([xa(r, v) for r, v in tr], [D.y_alpha(r) for r, _ in tr])
            preds += [{"r": r, "score": _dot(w, xa(r, v))} for r, v in te]
        if preds:
            pm = {id(p["r"]): p["score"] for p in preds}
            rs = [p["r"] for p in preds]
            c, pr = _weekly_rank_ic(rs, lambda r: pm[id(r)], D.y_alpha), _weekly_rank_ic(rs, lambda r: r.raw, D.y_alpha)
            res["features"][name] = {"coverage": cov[j], **_paired_stat({w: [c[w] - pr[w]] for w in c if w in pr}, h)}
    res["gates"] = gates(res, fam["track"])
    return res


def gates(res: dict, track: str) -> dict:
    """The fixed gates (see the module docstring); FDR is applied later across every test."""
    full = [e for e in res.get("eras", [])[:4] if e.get("n")]
    wf, sp = res.get("walkforward") or {}, res.get("split") or {}
    enough = len(full) >= 3 and (wf.get("n") or 0) >= 2000
    out = {"track": track, "enough": enough, "eras_complete": len(full)}
    t = lambda d, *ks: _get(d, *ks, "t") or 0.0          # noqa: E731
    m = lambda d, *ks: _get(d, *ks, "mean") or 0.0       # noqa: E731
    out["alpha"] = {"G1": t(wf, "alpha", "d_rank_ic") >= 2 and m(wf, "alpha", "d_ic") >= 0,
                    "eras_won": sum(1 for e in full if m(e, "alpha", "d_rank_ic") > 0), "split_t": t(sp, "alpha", "d_rank_ic"),
                    "t": t(wf, "alpha", "d_rank_ic")}
    out["alpha"]["G2"] = out["alpha"]["eras_won"] >= 3 and out["alpha"]["split_t"] >= 1
    out["directional"] = {"G1": (t(wf, "directional", "brier_vs_prior") >= 2 and m(wf, "directional", "logloss_vs_prior") > 0
                                 and m(wf, "directional", "acc_vs_prior") > 0 and t(wf, "directional", "brier_vs_current") >= 2),
                          "eras_won": sum(1 for e in full if m(e, "directional", "brier_vs_prior") > 0),
                          "split_t": t(sp, "directional", "brier_vs_prior"), "t": t(wf, "directional", "brier_vs_prior")}
    out["directional"]["G2"] = out["directional"]["eras_won"] >= 3 and out["directional"]["split_t"] >= 1
    hg = wf.get("hedge")
    if hg:
        out["hedge"] = {"G1": t(wf, "hedge", "vol_mse_gain") >= 2, "t": t(wf, "hedge", "vol_mse_gain"),
                        "eras_won": sum(1 for e in full if m(e, "hedge", "vol_mse_gain") > 0), "split_t": t(sp, "hedge", "vol_mse_gain")}
        out["hedge"]["G2"] = out["hedge"]["eras_won"] >= 3 and out["hedge"]["split_t"] >= 1
    return out


def _get(d, *ks):
    for k in ks:
        if not isinstance(d, dict):
            return None
        d = d.get(k)
    return d


# ------------------------------------------------------------------ multiple testing and statuses
def _p_one_sided(t: Optional[float]) -> float:
    return 1.0 - D._phi(t) if t is not None else 1.0


def benjamini_hochberg(ps: List[float], q: float = FDR_Q) -> List[bool]:
    """Rejections at FDR q (step-up)."""
    m = len(ps)
    order = sorted(range(m), key=lambda i: ps[i])
    k_max = 0
    for rank, i in enumerate(order, 1):
        if ps[i] <= q * rank / m:
            k_max = rank
    rej = [False] * m
    for rank, i in enumerate(order, 1):
        if rank <= k_max:
            rej[i] = True
    return rej


def finalise(result: dict) -> dict:
    """Apply BH across every family × horizon × target G1 test and every individual feature, and set statuses."""
    tests, feats = [], []
    for lab, hz in (result.get("horizons") or {}).items():
        for fam, fr in (hz.get("families") or {}).items():
            g = fr.get("gates") or {}
            for tgt in ("alpha", "directional", "hedge"):
                if g.get(tgt) and g.get("track") == "historical":
                    tests.append((lab, fam, tgt, _p_one_sided(g[tgt].get("t"))))
            for name, st in (fr.get("features") or {}).items():
                feats.append((lab, fam, name, _p_one_sided(st.get("t"))))
    rej = benjamini_hochberg([p for *_, p in tests])
    for (lab, fam, tgt, p), ok in zip(tests, rej):
        g = result["horizons"][lab]["families"][fam]["gates"][tgt]
        g["p"], g["fdr"] = p, ok
        g["passed"] = bool(g["G1"] and g["G2"] and ok)
    frej = benjamini_hochberg([p for *_, p in feats])
    for (lab, fam, name, p), ok in zip(feats, frej):
        st = result["horizons"][lab]["families"][fam]["features"][name]
        st["p"], st["fdr"], st["nominal"] = p, ok, p < 0.05
    result["fdr"] = {"q": FDR_Q, "family_tests": len(tests), "family_rejections": sum(rej), "feature_tests": len(feats),
                     "feature_nominal": sum(1 for *_, p in feats if p < 0.05), "feature_rejections": sum(frej)}
    summary = {}
    for fam, spec in FAMILIES.items():
        rows = [(lab, (hz.get("families") or {}).get(fam)) for lab, hz in (result.get("horizons") or {}).items()]
        rows = [(lab, fr) for lab, fr in rows if fr]
        if spec["track"] == "limited":
            recent = [(lab, tgt) for lab, fr in rows for tgt in ("alpha", "directional", "hedge")
                      if (fr.get("gates") or {}).get(tgt, {}).get("G1")]
            status, why = "LIMITED HISTORY", ("positive in the recent eras: " + ", ".join(f"{l} {t}" for l, t in recent) +
                                              " — live-shadow track only" if recent else "no incremental value in the recent eras it covers")
        elif not rows or not any((fr.get("gates") or {}).get("enough") for _, fr in rows):
            status, why = "INSUFFICIENT DATA", "fewer than 3 complete eras with data"
        else:
            passed = [(lab, tgt) for lab, fr in rows for tgt in ("alpha", "directional", "hedge") if (fr.get("gates") or {}).get(tgt, {}).get("passed")]
            status = "SHADOW" if passed else "NO INCREMENTAL VALUE"
            why = ("passed G1, G2 and FDR: " + ", ".join(f"{l} {t}" for l, t in passed)) if passed else "no test passed G1, G2 and the FDR control"
        summary[fam] = {"status": status, "why": why, "label": spec["label"], "tier": spec["tier"], "track": spec["track"]}
    for b in BLOCKED:
        summary[b["family"]] = {"status": "BLOCKED", "why": b["reason"], "label": b["label"], "tier": b["tier"], "track": "—"}
    result["summary"] = summary
    return result


# ------------------------------------------------------------------ the study
def study_horizon(store, research, lab: str, families=None, progress=None) -> dict:
    say = progress or (lambda m: None)
    W._init_famidx()
    h = dict(LAB_HORIZONS)[lab]
    t0 = time.time()
    recs = W.load(store, research, lab)
    D.attach(recs, research, h)
    recs = [r for r in recs if r.ext and r.ext.get("s") and r.yr != 0]
    builder = Builder(research, store)
    rets = {}
    for r in recs:
        if r.asset not in rets:
            rets[r.asset] = _logret(builder.series(r.asset))
    ctx = {"rets": rets, "vix": builder.macro("VIXCLS")}
    out = {"horizon": lab, "records": len(recs), "families": {}}
    say(f"{lab}: {len(recs)} records ({time.time() - t0:.0f}s)")
    for fam in (families or list(FAMILIES)):
        t1 = time.time()
        out["families"][fam] = study_family(recs, builder, fam, h, ctx)
        say(f"{lab}: {fam} done ({time.time() - t1:.0f}s)")
    out["seconds"] = round(time.time() - t0, 1)
    return out


def _worker(db_path: str, lab: str, families):
    from ..data.store import Store
    from .research import Research
    st = Store(db_path)
    try:
        return lab, study_horizon(st, Research(st), lab, families, progress=lambda m: print(m, flush=True))
    finally:
        st.close()


def run_all(db_path: str, workers: int = 2, progress=None, horizons=None, families=None) -> dict:
    """Every family at every horizon, judged against the frozen benchmark (required)."""
    from concurrent.futures import ProcessPoolExecutor, as_completed
    from ..data.store import Store
    from .lab import benchmark, verify_benchmark
    st = Store(db_path)
    try:
        bm = benchmark(st)
        if not bm:
            raise ValueError("no frozen benchmark: run `python -m finsim2 lab --freeze-benchmark` first")
        ok = verify_benchmark(st, bm["id"])
        if not ok.get("ok"):
            raise ValueError(f"benchmark {bm['id']} does not verify: {ok}")
    finally:
        st.close()
    say = progress or (lambda m: None)
    t0 = time.time()
    out = {"started": time.strftime("%Y-%m-%d %H:%M:%S"), "benchmark": {"id": bm["id"], "hash": bm["hash"], "frozen": bm["frozen"],
                                                                         "production": bm["production"]},
           "families_spec": {k: {kk: vv for kk, vv in v.items()} for k, v in FAMILIES.items()}, "blocked": BLOCKED,
           "eras": ERAS, "split": SPLIT, "fdr_q": FDR_Q, "horizons": {}}
    labs = [lab for lab, _ in LAB_HORIZONS if not horizons or lab in horizons]
    with ProcessPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(_worker, db_path, lab, families) for lab in labs]
        for f in as_completed(futs):
            lab, res = f.result()
            out["horizons"][lab] = res
            say(f"{lab} done")
    finalise(out)
    out["seconds"] = round(time.time() - t0, 1)
    st = Store(db_path)
    try:
        st.kv_set(RESEARCH_KEY, out)
    finally:
        st.close()
    return out


# ------------------------------------------------------------------ the report (NEW_INFORMATION_RESEARCH.md)
_HZ = [lab for lab, _ in LAB_HORIZONS]


def _f(v, d=3, signed=True):
    return "—" if v is None else (f"{v:+.{d}f}" if signed else f"{v:.{d}f}")


def _pct(v, d=1):
    return "—" if v is None else f"{v * 100:+.{d}f}%"


def _best(res: dict, fam: str, path: Tuple[str, ...]) -> Tuple[Optional[str], Optional[float], Optional[float]]:
    """(horizon, mean, t) of the largest t across horizons for a walk-forward statistic."""
    best = (None, None, None)
    for lab in _HZ:
        fr = (((res.get("horizons") or {}).get(lab) or {}).get("families") or {}).get(fam) or {}
        st = _get(fr.get("walkforward") or {}, *path) or {}
        if st.get("t") is not None and (best[2] is None or st["t"] > best[2]):
            best = (lab, st.get("mean"), st["t"])
    return best


def _tests(res: dict):
    for lab in _HZ:
        for fam, fr in ((((res.get("horizons") or {}).get(lab) or {}).get("families")) or {}).items():
            for tgt in ("alpha", "directional", "hedge"):
                g = (fr.get("gates") or {}).get(tgt)
                if g:
                    yield lab, fam, tgt, g, fr


def markdown(res: dict) -> str:
    H = res.get("horizons") or {}
    S = res.get("summary") or {}
    bm = res.get("benchmark") or {}
    L: List[str] = []
    w = L.append
    w("# New information for the Shaffer systems — research")
    w("")
    w(f"Run {res.get('started')} · {res.get('seconds')} s · generated by `python -m finsim2 lab --newinfo`. Research only: production "
      "scoring, the Directional research definition and the hedge math are unchanged, and no family is added to production.")
    w("")
    w(f"**Benchmark:** `{bm.get('id')}` (frozen {bm.get('frozen')}, sha256 `{bm.get('hash')}`) — Shaffer Score `{(bm.get('production') or {}).get('score')}`, "
      f"Shaffer Alpha `{(bm.get('production') or {}).get('alpha')}`, Shaffer Hedge `{(bm.get('production') or {}).get('hedge')}`; the Directional "
      "research definition (prior-only and current). Every family is judged by what it adds to this benchmark on identical records.")
    w("")
    w("Question: which genuinely new point-in-time information improves Shaffer Alpha (relative ranking), Shaffer Directional "
      "(absolute direction beyond the PIT prior) or Shaffer Hedge modelling (volatility forecasts) *incrementally*? Families were "
      "built only from information absent from the 74 production signals and the earlier candidate families (the earnings-surprise "
      "family is labelled as a re-test). Data sources and their status: `NEW_DATA_SOURCES.md`.")
    w("")
    w("Protocol (fixed before any result): weights and standardisation frozen before each era (→ 2008 test 2009–12, → 2012 test "
      "2013–16, → 2016 test 2017–20, → 2020 test 2021–24, → 2024 test 2025–) and the 2018 split; Alpha — paired Δ rank IC against the "
      "production score; Directional — gate v2 (beat the prior-only model: Brier t ≥ 2, lower log loss, higher accuracy; and the "
      "current formulation: Brier t ≥ 2); Hedge — squared error of the log realised-volatility forecast against a baseline of "
      "63-day and 21-day realised volatility and VIX; G2 ≥ 3 of 4 eras and the split; Benjamini-Hochberg at q = "
      f"{res.get('fdr_q', FDR_Q)} across all {((res.get('fdr') or {}).get('family_tests'))} family × horizon × target tests. "
      "Families whose data start after 2018 are on a separate LIMITED HISTORY track and are never presented as historically verified.")
    w("")
    w("## The main table")
    w("")
    w("| Family | Assets | Historical coverage | Alpha Δ rank IC (best horizon, t) | Directional Δ Brier vs prior (best, t) | Directional Δ accuracy vs prior | Hedge improvement (best, t) | Era stability | Status |")
    w("|---|---|---|---|---|---|---|---|---|")
    for fam, spec in FAMILIES.items():
        rows = [(lab, ((H.get(lab) or {}).get("families") or {}).get(fam) or {}) for lab in _HZ]
        rows = [(lab, fr) for lab, fr in rows if fr.get("records")]
        assets = max((fr.get("assets") or 0 for _, fr in rows), default=0)
        first = min((fr.get("first") for _, fr in rows if fr.get("first")), default=None)
        a = _best(res, fam, ("alpha", "d_rank_ic"))
        d = _best(res, fam, ("directional", "brier_vs_prior"))
        hg = _best(res, fam, ("hedge", "vol_mse_gain"))
        dacc = None
        if d[0]:
            dacc = (((H.get(d[0]) or {}).get("families") or {}).get(fam, {}).get("walkforward") or {}).get("directional", {}).get("excess_vs_prior")
        won = [(g.get("eras_won"), (fr.get("gates") or {}).get("eras_complete")) for lab, f_, tgt, g, fr in _tests(res) if f_ == fam]
        stab = max(won, key=lambda x: (x[0] or 0), default=(None, None))
        w(f"| {spec['label']} | {assets} | from {first or '—'} ({spec['track']}) | {_f(a[1])} ({a[0] or '—'}, t {_f(a[2], 1)}) | "
          f"{_f(d[1], 5)} ({d[0] or '—'}, t {_f(d[2], 1)}) | {_pct(dacc, 2)} | {_f(hg[1], 4)} ({hg[0] or '—'}, t {_f(hg[2], 1)}) | "
          f"{stab[0] if stab[0] is not None else '—'}/{stab[1] if stab[1] is not None else '—'} eras | **{(S.get(fam) or {}).get('status', '—')}** |")
    for b in res.get("blocked") or BLOCKED:
        w(f"| {b['label']} | — | — | — | — | — | — | — | **BLOCKED** |")
    w("")
    w("\"Best horizon\" = the horizon with the largest t (a positive best is not a pass: passing needs G1, G2 and the FDR "
      "control). Era stability = the most test eras with a gain, of the complete ones, over the family's tests.")
    w("")
    # answers
    w("## Answers")
    w("")
    w("**1. Which new datasets were actually obtainable?** From the allowed domains: SEC first-reported filings and the own "
      "price / volume panel (earnings events), FRED credit, term-premium, real-yield, breakeven and foreign short-rate series, "
      "FINRA Reg SHO short-sale volume (2019 →), and — only as recent windows or daily quotas — Finnhub earnings / "
      "recommendations, Nasdaq short interest (1 year), Massive whole-market aggregates (2 years), Alpha Vantage consensus "
      "(25 requests a day). Full list with status: `NEW_DATA_SOURCES.md`.")
    w("")
    w("**2. Which were point in time historically?** " + "; ".join(
        f"{spec['label']} — {spec['pit']} ({'historical track' if spec['track'] == 'historical' else 'LIMITED HISTORY: starts after 2018'})"
        for spec in FAMILIES.values()) + ".")
    w("")
    w("**3. Which families were built?**")
    w("")
    w("| Family | Features | Source | Data quality | Records (largest horizon) | Coverage by feature |")
    w("|---|---|---|---|---|---|")
    for fam, spec in FAMILIES.items():
        frs = [((H.get(lab) or {}).get("families") or {}).get(fam) or {} for lab in _HZ]
        fr = max(frs, key=lambda x: x.get("records") or 0)
        cov = ", ".join(f"{k} {v * 100:.0f}%" for k, v in (fr.get("coverage") or {}).items())
        w(f"| {spec['label']}{' *(re-test)*' if spec.get('retest') else ''} | {', '.join(spec['features'])} | {spec['source']} | {spec['quality']} | {fr.get('records', 0):,} | {cov or '—'} |")
    w("")
    for q, tgt, name in ((4, "alpha", "Alpha ranking"), (5, "directional", "Directional forecasts beyond the prior-only model"),
                         (6, "hedge", "hedge modelling (volatility forecasts beyond 63d / 21d realised volatility and VIX)")):
        passed = [(lab, fam) for lab, fam, t, g, fr in _tests(res) if t == tgt and g.get("passed")]
        g1 = [(lab, fam, g) for lab, fam, t, g, fr in _tests(res) if t == tgt and g.get("G1") and not g.get("passed")]
        w(f"**{q}. Which improved {name}?** " + (
            "Passed G1, G2 and FDR: " + "; ".join(f"{FAMILIES[f]['label']} at {lab}" for lab, f in passed) + "." if passed else
            "None passed G1, G2 and the FDR control.") + (
            " Passed G1 but not the rest: " + "; ".join(f"{FAMILIES[f]['label']} at {lab} (eras {g.get('eras_won')}, split t {_f(g.get('split_t'), 1)}, FDR {'yes' if g.get('fdr') else 'no'})"
                                                      for lab, f, g in g1) + "." if g1 else ""))
        w("")
    stable = [(lab, fam, tgt) for lab, fam, tgt, g, fr in _tests(res)
              if (fr.get("gates") or {}).get("eras_complete", 0) >= 3 and g.get("eras_won") == (fr.get("gates") or {}).get("eras_complete") and (g.get("t") or 0) > 0]
    w("**7. Which effects survived all eras?** " + ("; ".join(f"{FAMILIES[f]['label']} — {tgt} at {lab}" for lab, f, tgt in stable) + " (a gain in every complete era; significance is a separate question)."
                                                    if stable else "None: no family × target × horizon gained in every complete era."))
    w("")
    reg = []
    for lab in _HZ:
        for fam, fr in (((H.get(lab) or {}).get("families")) or {}).items():
            gd = (fr.get("gates") or {}).get("directional") or {}
            ga = (fr.get("gates") or {}).get("alpha") or {}
            for state, st in (fr.get("regimes") or {}).items():
                if st.get("insufficient"):
                    continue
                if ((st.get("brier_vs_prior") or {}).get("t") or 0) >= 2 and not gd.get("G1"):
                    reg.append(f"{FAMILIES[fam]['label']} — Directional at {lab} in {state} (t {_f(st['brier_vs_prior']['t'], 1)}, n_eff {st['n_eff']:.0f})")
                if ((st.get("d_rank_ic") or {}).get("t") or 0) >= 2 and not ga.get("G1"):
                    reg.append(f"{FAMILIES[fam]['label']} — Alpha at {lab} in {state} (t {_f(st['d_rank_ic']['t'], 1)}, n_eff {st['n_eff']:.0f})")
    w("**8. Which effects were regime-specific?** " + ("Significant (t ≥ 2) inside one regime state while failing G1 overall — "
      "not tested against the multiple comparisons this search involves, so these are leads, not findings: " + "; ".join(reg) + "."
      if reg else "None reached t ≥ 2 inside a regime state with at least 100 independent observations while failing overall."))
    w("")
    gone = [(lab, fam, tgt) for lab, fam, tgt, g, fr in _tests(res) if g.get("G1") and g.get("G2") and not g.get("fdr")]
    fd = res.get("fdr") or {}
    w(f"**9. Which disappeared after multiple-testing correction?** Family tests: {fd.get('family_tests')} run, {fd.get('family_rejections')} "
      f"survive Benjamini-Hochberg at q = {fd.get('q')}" + (" — lost to the correction despite G1 and G2: " + "; ".join(f"{FAMILIES[f]['label']} {t} at {l}" for l, f, t in gone) if gone else "") +
      f". Individual features: {fd.get('feature_tests')} tests, {fd.get('feature_nominal')} nominally significant (one-sided p < 0.05), "
      f"{fd.get('feature_rejections')} survive the correction. Features are never admitted one by one.")
    w("")
    sh = [f for f, s in S.items() if s["status"] == "SHADOW"]
    lim = [f for f, s in S.items() if s["status"] == "LIMITED HISTORY"]
    w("**10. Which should remain shadow?** " + (", ".join(FAMILIES[f]["label"] for f in sh) + " (passed every historical gate); "
      if sh else "No family earned SHADOW status. ") + ("LIMITED HISTORY, kept on the live-shadow track only: " + ", ".join(FAMILIES[f]["label"] + f" — {S[f]['why']}" for f in lim) + "." if lim else ""))
    w("")
    w("**11. Which qualify for live shadow?** " + (", ".join(FAMILIES[f]["label"] for f in sh) + " — to be recorded daily next to the benchmark and graded before any promotion."
      if sh else "None by the historical gates. Separately, the benchmark's own prior-only and current Directional p_up are now recorded daily for every asset and horizon, so any future challenger has a live comparison."))
    w("")
    w("**12. Which are blocked by data availability?** " + "; ".join(f"{b['label']} — {b['reason']}" for b in res.get("blocked") or BLOCKED) + ".")
    w("")
    w("**13. Which data source would add the most future value?** An analyst-estimates source with point-in-time revision "
      "history (the highest-priority missing family), then CFTC positioning (`www.cftc.gov` on the allowlist), EIA inventories "
      "(`api.eia.gov`), and historical option chains. Each is an owner's data decision, not something to approximate.")
    w("")
    w("**14. Is the 74-signal set information-limited rather than weight-limited?** See *Reading these results* above.")
    w("")
    w("**15. Is anything eligible for promotion?** No. Promotion needs the historical gates, the FDR control and a live-shadow "
      "record (G3); no family has live history, and nothing is added to production in this phase.")
    w("")
    # per family detail
    w("## Per family")
    for fam, spec in FAMILIES.items():
        w("")
        w(f"### {spec['label']} — {(S.get(fam) or {}).get('status', '—')}")
        w("")
        w(f"{(S.get(fam) or {}).get('why', '')}. Source: {spec['source']}. PIT: {spec['pit']}.")
        w("")
        w("| Horizon | Records | Alpha Δ rank IC (t) | Alpha Δ IC (t) | Dir. Brier vs prior (t) | vs current (t) | Log loss vs prior (t) | Δ accuracy vs prior | Balanced acc. | Hedge vol gain (t) | Eras won A / D / H | Split t A / D / H |")
        w("|---|---|---|---|---|---|---|---|---|---|---|---|")
        for lab in _HZ:
            fr = ((H.get(lab) or {}).get("families") or {}).get(fam) or {}
            wf = fr.get("walkforward") or {}
            if not wf.get("n"):
                w(f"| {lab} | {fr.get('records', 0):,} | {fr.get('status_reason') or 'insufficient'} | | | | | | | | | |")
                continue
            a, d, hg, g = wf["alpha"], wf["directional"], wf.get("hedge") or {}, fr.get("gates") or {}
            ba = (d.get("new") or {}).get("balanced_accuracy")
            bal = f"{ba * 100:.1f}%" if ba is not None else "—"
            ts = lambda st: f"{_f(st.get('mean'), 4)} ({_f(st.get('t'), 1)})" if st else "—"  # noqa: E731
            w(f"| {lab} | {wf['n']:,} | {ts(a['d_rank_ic'])} | {ts(a['d_ic'])} | {_f(d['brier_vs_prior'].get('mean'), 5)} ({_f(d['brier_vs_prior'].get('t'), 1)}) | "
              f"{_f(d['brier_vs_current'].get('t'), 1)} | {_f(d['logloss_vs_prior'].get('t'), 1)} | {_pct(d.get('excess_vs_prior'), 2)} | "
              f"{bal} | {ts(hg.get('vol_mse_gain'))} | "
              f"{(g.get('alpha') or {}).get('eras_won', '—')} / {(g.get('directional') or {}).get('eras_won', '—')} / {(g.get('hedge') or {}).get('eras_won', '—')} of {g.get('eras_complete', '—')} | "
              f"{_f((g.get('alpha') or {}).get('split_t'), 1)} / {_f((g.get('directional') or {}).get('split_t'), 1)} / {_f((g.get('hedge') or {}).get('split_t'), 1)} |")
        feats = {}
        for lab in _HZ:
            for name, st in ((((H.get(lab) or {}).get("families") or {}).get(fam) or {}).get("features") or {}).items():
                feats.setdefault(name, []).append((lab, st))
        if feats:
            w("")
            w("Individual features (Alpha Δ rank IC t by horizon; † nominal p < 0.05, ✓ survives FDR): " + "; ".join(
                f"{name}: " + ", ".join(f"{lab} {_f(st.get('t'), 1)}{'✓' if st.get('fdr') else '†' if st.get('nominal') else ''}" for lab, st in v)
                for name, v in feats.items()))
    return "\n".join(L) + "\n"
