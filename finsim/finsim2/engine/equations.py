"""The equations tab: every catalogue equation evaluated on one asset's market data.

Public API
----------
evaluate_catalog(ctx) -> list[dict]
    One standardised analytic result per catalogue equation (all of `finsim.quant.catalog.EQUATIONS`,
    in catalogue order).
analytics_tabs(ctx, catalog=None) -> dict[str, list[dict]]
    Tab name -> standardised results (the relevant catalogue equations plus analytics that are not in the
    catalogue: rolling / relative returns, distribution, drawdown, tracking error, multifactor regression,
    PACF, volatility regime, yield curve, valuation ...).
summary_numbers(ctx) -> dict
    Headline values for the Analytics header.

The context `ctx` is a plain dict (see ARCHITECTURE.md and the module tests): price lists aligned to one
business-day calendar with None for missing values, the SPY benchmark, macro series, the universe's recent
returns, point-in-time features, horizon-engine signal records, the current 3-month rate, an optional
portfolio and optional ML-engine output.

Every result follows the standardised analytic format of ARCHITECTURE.md. Nothing here raises: an item that
cannot be computed carries value None and a note. All output is JSON-serialisable (no NaN / inf).
Standard library only; the numerics come from `finsim.quant`.
"""
import math
import random

from finsim.quant import credit as qc
from finsim.quant import derivatives as qd
from finsim.quant import fixed_income as qfi
from finsim.quant import ml as qml
from finsim.quant import nn as qnn
from finsim.quant import portfolio as qp
from finsim.quant import regression as qr
from finsim.quant import stats as qs
from finsim.quant import stochastic as qsto
from finsim.quant import swaps as qsw
from finsim.quant import timeseries as qt
from finsim.quant import volatility as qv
from finsim.quant.catalog import EQUATIONS
from finsim.quant.linalg import solve

__all__ = ["evaluate_catalog", "analytics_tabs", "summary_numbers", "FAMILIES", "TABS"]

ANN = 252
MIN_OBS = 60            # fewer returns than this: statistical items are None
MAX_POINTS = 260        # history series are downsampled to at most this many points
DIRECTION_LAG = 21      # sessions used for the Increasing / Decreasing / Stable label
NOTIONAL = 10_000_000.0
RECOVERY = 0.40

FAMILIES = ["Returns", "Statistics", "Risk", "Regression", "Time Series", "Volatility", "Stochastic Models",
            "Portfolio Theory", "Fixed Income", "Machine Learning", "Neural Networks", "Derivatives", "Credit"]
TABS = ["Returns", "Statistics", "Risk", "Regression", "Time Series", "Volatility", "Stochastic", "Portfolio",
        "Fixed Income", "Valuation"]
_TAB_OF_FAMILY = {"Returns": "Returns", "Statistics": "Statistics", "Risk": "Risk", "Regression": "Regression",
                  "Time Series": "Time Series", "Volatility": "Volatility", "Stochastic Models": "Stochastic",
                  "Portfolio Theory": "Portfolio", "Fixed Income": "Fixed Income", "Credit": "Fixed Income"}

_SECTION_FAMILY = {"regression": "Regression", "timeseries": "Time Series", "volatility": "Volatility",
                   "stochastic": "Stochastic Models", "ml": "Machine Learning", "nn": "Neural Networks",
                   "portfolio": "Portfolio Theory", "fixed_income": "Fixed Income", "forwards": "Derivatives",
                   "options": "Derivatives", "credit": "Credit"}


def _family(eq):
    i, s = eq["id"], eq["section"]
    if s == "returns":
        n = int(i)
        return "Returns" if n <= 7 else ("Statistics" if n <= 18 else "Risk")
    if s == "portfolio" and i in ("109", "110"):
        return "Risk"
    if s == "swaps":
        return "Fixed Income" if i in ("S1", "S2", "S3", "S4", "S5") else "Derivatives"
    return _SECTION_FAMILY.get(s, s)


EQ_BY_ID = {e["id"]: e for e in EQUATIONS}

# Feature (ARCHITECTURE.md names) whose horizon-engine evidence is attached to an equation.
EQ_FEATURE = {
    "1": "ret_1d", "2": "ret_1d", "3": "ret_12m", "4": "ret_12m", "8": "vol_60", "9": "vol_60",
    "10": "corr_252", "11": "corr_252", "12": "z_50", "16": "skew_60", "17": "kurt_60", "19": "sharpe_252",
    "20": "sortino_252", "22": "beta_252", "31": "alpha_252", "33": "vix", "35": "ar1_63", "41": "acf1_252",
    "43": "adf_t", "44": "ewma_vol", "46": "garch_vol", "47": "garch_vol", "48": "vol_20", "49": "vol_20",
    "50": "beta_252", "56": "half_life", "58": "half_life", "106": "beta_252", "108": "alpha_252",
    "118": "slope_10y3m", "C4": "credit_spread", "C5": "credit_spread", "C8": "credit_spread",
}

_CREDIT_IDS = {"LQD", "HYG", "JNK", "VCIT", "VCSH", "VCLT", "IGIB", "IGSB", "USIG", "SJNK", "ANGL", "SPHY",
               "USHY", "SHYG", "FALN", "BKLN", "SRLN", "EMB"}


# ============================================================================ small numeric helpers

class _Insufficient(Exception):
    """Not enough data for an item."""


class _Failed:
    def __init__(self, exc):
        self.exc = exc


def _f(v):
    if v is None or isinstance(v, bool):
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _clamp(x, lo=-1.0, hi=1.0):
    return lo if x < lo else hi if x > hi else x


def _mean(x):
    return math.fsum(x) / len(x)


def _sd(x):
    n = len(x)
    if n < 2:
        return 0.0
    m = math.fsum(x) / n
    return math.sqrt(max(0.0, math.fsum((v - m) ** 2 for v in x) / (n - 1)))


def _pref(x):
    out = [0.0]
    s = 0.0
    for v in x:
        s += v
        out.append(s)
    return out


def _pref2(x):
    out = [0.0]
    s = 0.0
    for v in x:
        s += v * v
        out.append(s)
    return out


def _quantile(sorted_x, q):
    n = len(sorted_x)
    if n == 0:
        return None
    pos = q * (n - 1)
    lo = int(math.floor(pos))
    hi = min(n - 1, lo + 1)
    return sorted_x[lo] + (sorted_x[hi] - sorted_x[lo]) * (pos - lo)


def _ends(first, last, maxp=MAX_POINTS):
    """Evenly spaced window ends in [first, last], always including `last`."""
    if last < first:
        return []
    step = max(1, -(-(last - first + 1) // maxp))
    return list(range(last, first - 1, -step))[::-1]


def _series(dates, values, maxp=MAX_POINTS):
    """Downsample a full series to <= maxp points; `prev` is the value DIRECTION_LAG entries back."""
    n = len(values)
    if n == 0:
        return None
    idx = [h - 1 for h in _ends(1, n, maxp)]
    prev = values[n - 1 - DIRECTION_LAG] if n > DIRECTION_LAG else None
    return {"dates": [dates[i] for i in idx], "values": [values[i] for i in idx], "prev": prev}


def _roll(n, window, fn, dates, maxp=MAX_POINTS, lag=DIRECTION_LAG):
    """Rolling statistic evaluated at <= maxp window ends: fn(lo, hi) on slices [lo:hi]."""
    if n < window:
        return None
    vals = []
    ends = _ends(window, n, maxp)
    for hi in ends:
        vals.append(_try(fn, hi - window, hi))
    prev = _try(fn, n - lag - window, n - lag) if n - lag >= window else None
    return {"dates": [dates[h - 1] for h in ends], "values": vals, "prev": prev}


def _try(fn, *a):
    try:
        return _f(fn(*a))
    except Exception:  # noqa: BLE001 - history points are best effort
        return None


def _sig(x, digits=3):
    """Round to `digits` significant figures (for variables and history values)."""
    if x is None or isinstance(x, bool) or not isinstance(x, (int, float)):
        return x
    if not math.isfinite(x):
        return None
    if x == 0:
        return 0.0
    return round(x, digits - 1 - int(math.floor(math.log10(abs(x)))))


def _ordinal(p):
    k = int(round(p * 100))
    k = max(0, min(100, k))
    suf = "th" if 10 <= k % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(k % 10, "th")
    return "%d%s percentile" % (k, suf)


def _level(p):
    if p is None:
        return "typical"
    if p >= 0.9:
        return "very high"
    if p >= 0.7:
        return "elevated"
    if p <= 0.1:
        return "very low"
    if p <= 0.3:
        return "low"
    return "normal"


def _tscore(t, cap=0.45):
    """Lean from a t-statistic: zero until |t| > 1, 'Slightly' around t = 2, capped below a full signal."""
    if t is None:
        return None
    return _clamp(math.copysign(max(0.0, abs(t) - 1.0) / 5.0, t), -cap, cap)


def _label(score):
    if score is None:
        return None
    if score >= 0.5:
        return "Bullish"
    if score >= 0.15:
        return "Slightly Bullish"
    if score <= -0.5:
        return "Bearish"
    if score <= -0.15:
        return "Slightly Bearish"
    return "Neutral"


def _dec(a):
    """Decimals giving ~3 significant figures for a magnitude a (> 0)."""
    if a >= 100:
        return 0
    if a >= 10:
        return 1
    if a >= 1:
        return 2
    return min(8, 2 - int(math.floor(math.log10(a))))


def _fmt(v, fmt):
    """Human display with sensible rounding (never fake precision)."""
    if v is None:
        return "unavailable"
    if isinstance(v, bool) or fmt == "bool":
        return "Yes" if v else "No"
    if isinstance(v, str):
        return v
    x = _f(v)
    if x is None:
        return "unavailable"
    a = abs(x)
    if fmt in ("pct", "ret"):
        y = x * 100.0
        ay = abs(y)
        if ay < 5e-7:
            return "0%"
        s = ("%.*f" % (_dec(ay), y))
        if ay >= 1000:
            s = "{:,.0f}".format(y)
        return ("+" if fmt == "ret" and y > 0 else "") + s + "%"
    if fmt == "bp":
        b = x * 1e4
        return ("%.1f bp" % b) if abs(b) < 10 else ("{:,.0f} bp".format(b))
    if fmt == "price":
        return "{:,.2f}".format(x) if a >= 1 else "%.4g" % x
    if fmt == "money":
        return ("-" if x < 0 else "") + "${:,.0f}".format(a)
    if fmt == "days":
        return ("%.1f days" % x) if a < 100 else ("{:,.0f} days".format(x))
    if fmt == "int":
        return "{:,d}".format(int(round(x)))
    if fmt == "sci":
        return "%.2e" % x
    if fmt == "ratio":
        return ("%.2f" % x) if a >= 0.01 or a == 0 else "%.2g" % x
    # "num": three significant figures
    if a == 0:
        return "0"
    if a >= 1000:
        return "{:,.0f}".format(x)
    if a < 1e-3:
        return "%.2e" % x
    return "%.*f" % (_dec(a), x)


def _clean(o):
    """JSON-safe copy: NaN / inf -> None, tuples -> lists."""
    if o is None or isinstance(o, (bool, str, int)):
        return o
    if isinstance(o, float):
        return o if math.isfinite(o) else None
    if isinstance(o, dict):
        return {str(k): _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    x = _f(o)
    return x if x is not None else str(o)


def _vars(**kw):
    return {k: ([_sig(x, 4) for x in v] if isinstance(v, (list, tuple)) else
                ({kk: _sig(vv, 4) for kk, vv in v.items()} if isinstance(v, dict) else _sig(v, 4)))
            for k, v in kw.items()}


def _memo(fn):
    name = fn.__name__

    def wrapper(self):
        c = self._memo
        if name in c:
            v = c[name]
            if isinstance(v, _Failed):
                raise v.exc
            return v
        try:
            v = fn(self)
        except Exception as exc:  # noqa: BLE001 - cached so an expensive failure is not repeated
            c[name] = _Failed(exc)
            raise
        c[name] = v
        return v
    wrapper.__name__ = name
    wrapper.__doc__ = fn.__doc__
    return wrapper


def _adf_p(t, trend="ct"):
    """Approximate p-value of an ADF t-statistic by interpolating the asymptotic Dickey-Fuller quantiles."""
    if t is None:
        return None
    table = {"ct": [(-3.96, 0.01), (-3.66, 0.025), (-3.41, 0.05), (-3.12, 0.10), (-2.18, 0.50),
                    (-1.25, 0.90), (-0.94, 0.95), (-0.66, 0.975), (-0.33, 0.99)],
             "c": [(-3.43, 0.01), (-3.12, 0.025), (-2.86, 0.05), (-2.57, 0.10), (-1.57, 0.50),
                   (-0.44, 0.90), (-0.07, 0.95), (0.23, 0.975), (0.60, 0.99)]}[trend]
    if t <= table[0][0]:
        return 0.01
    if t >= table[-1][0]:
        return 0.99
    for (t0, p0), (t1, p1) in zip(table, table[1:]):
        if t0 <= t <= t1:
            return p0 + (p1 - p0) * (t - t0) / (t1 - t0)
    return None


def _durbin_levinson(rho):
    """Partial autocorrelations from autocorrelations rho[0] = rho_1 ..."""
    out = []
    prev = []
    for k in range(1, len(rho) + 1):
        if k == 1:
            phi_kk = rho[0]
            cur = [phi_kk]
        else:
            num = rho[k - 1] - sum(prev[j] * rho[k - 2 - j] for j in range(k - 1))
            den = 1.0 - sum(prev[j] * rho[j] for j in range(k - 1))
            phi_kk = num / den if den else 0.0
            cur = [prev[j] - phi_kk * prev[k - 2 - j] for j in range(k - 1)] + [phi_kk]
        out.append(phi_kk)
        prev = cur
    return out


def _logit_irls(X, y, l2=1.0, iters=15):
    """Small ridge-penalised logistic regression by Newton / IRLS (intercept unpenalised). Returns (w, b)."""
    k = len(X[0])
    w = [0.0] * (k + 1)
    rows = [[1.0] + list(r) for r in X]
    for _ in range(iters):
        H = [[0.0] * (k + 1) for _ in range(k + 1)]
        g = [0.0] * (k + 1)
        for r, t in zip(rows, y):
            p = qml.sigmoid(sum(a * b for a, b in zip(w, r)))
            wt = max(p * (1.0 - p), 1e-9)
            e = t - p
            for i in range(k + 1):
                ri = r[i]
                g[i] += e * ri
                Hi = H[i]
                c = wt * ri
                for j in range(i, k + 1):
                    Hi[j] += c * r[j]
        for i in range(k + 1):
            for j in range(i):
                H[i][j] = H[j][i]
            if i > 0:
                H[i][i] += l2
                g[i] -= l2 * w[i]
        step = solve(H, g)
        w = [a + b for a, b in zip(w, step)]
        if max(abs(s) for s in step) < 1e-8:
            break
    return w[1:], w[0]


# ============================================================================ prepared context

class _Prep:
    """Aligned, cleaned inputs plus memoised shared computations for one context."""

    def __init__(self, ctx):
        self.ctx = ctx if isinstance(ctx, dict) else {}
        c = self.ctx
        self._memo = {}
        self.asset = c.get("asset") or {}
        self.aid = str(self.asset.get("id") or "")
        self.aclass = str(self.asset.get("asset_class") or "").upper()
        self.dates_all = list(c.get("dates") or [])
        N = self.N = len(self.dates_all)

        def col(name):
            v = c.get(name) or []
            out = [_f(x) for x in v[:N]]
            return out + [None] * (N - len(out))

        adj = col("adj")
        if not any(x is not None for x in adj):
            adj = col("close")
        self.adj_all = [x if (x is not None and x > 0) else None for x in adj]
        self.close_all = col("close")
        self.market_all = [x if (x is not None and x > 0) else None for x in col("market")]
        e = N - 1
        while e >= 0 and self.adj_all[e] is None:
            e -= 1
        s = e
        while s - 1 >= 0 and self.adj_all[s - 1] is not None:
            s -= 1
        self.s, self.e = max(s, 0), e + 1          # valid segment [s, e)
        self.P = self.adj_all[self.s:self.e] if e >= 0 else []
        self.D = self.dates_all[self.s:self.e] if e >= 0 else []
        self.L = len(self.P)
        P = self.P
        self.lr = [math.log(P[i] / P[i - 1]) for i in range(1, self.L)]
        self.sr = [P[i] / P[i - 1] - 1.0 for i in range(1, self.L)]
        self.rd = self.D[1:]
        M = self.market_all[self.s:self.e]
        self.mret = [math.log(M[i] / M[i - 1]) if (M[i] is not None and M[i - 1] is not None) else None
                     for i in range(1, self.L)]
        idx = [i for i, m in enumerate(self.mret) if m is not None]
        self.pa = [self.lr[i] for i in idx]
        self.pm = [self.mret[i] for i in idx]
        self.pdates = [self.rd[i] for i in idx]
        self.pidx = idx
        rf = _f(c.get("rf"))
        self.rf_note = None
        if rf is None:
            y3m = self.macro_last("y3m")
            rf = y3m if y3m is not None else 0.04
            self.rf_note = "rf missing from the context: %s used" % ("latest 3-month yield" if y3m is not None
                                                                      else "a 4% placeholder")
        self.rf = rf
        dur = _f(self.asset.get("duration"))
        self.duration = dur if dur and dur > 0 else None
        self.is_bond = self.aclass in ("TREASURY", "CORP_BOND") or self.duration is not None
        name = str(self.asset.get("name") or "").lower()
        self.is_credit = (self.aclass == "CORP_BOND" or self.aid.upper() in _CREDIT_IDS or
                          (self.is_bond and any(w in name for w in ("corporate", "high yield", "credit",
                                                                    "investment grade", "loan"))))
        self.is_fx = self.aclass == "FX"
        self.is_commodity = self.aclass in ("COMMODITY", "FUTURE")

    # ---------------------------------------------------------------- access helpers

    def need(self, k):
        if len(self.lr) < k:
            raise _Insufficient("needs at least %d daily returns (have %d)" % (k, len(self.lr)))

    def win(self, k, minimum=MIN_OBS):
        self.need(minimum)
        return self.lr[-min(k, len(self.lr)):]

    def swin(self, k, minimum=MIN_OBS):
        self.need(minimum)
        return self.sr[-min(k, len(self.sr)):]

    def pairs(self, k, minimum=MIN_OBS):
        if len(self.pa) < minimum:
            raise _Insufficient("needs at least %d paired asset/SPY returns (have %d)" % (minimum, len(self.pa)))
        n = min(k, len(self.pa))
        return self.pa[-n:], self.pm[-n:]

    def macro_all(self, name):
        key = "macro:" + name
        if key not in self._memo:
            v = (self.ctx.get("macro") or {}).get(name) or []
            out = [_f(x) for x in v[:self.N]]
            self._memo[key] = out + [None] * (self.N - len(out))
        return self._memo[key]

    def macro_last(self, name, max_age=10):
        v = self.macro_all(name)
        for i in range(len(v) - 1, max(-1, len(v) - 1 - max_age), -1):
            if v[i] is not None:
                return v[i]
        return None

    def macro_at(self, name, idx, max_age=10):
        v = self.macro_all(name)
        for i in range(min(idx, len(v) - 1), max(-1, idx - max_age), -1):
            if v[i] is not None:
                return v[i]
        return None

    def macro_seg(self, name):
        return self.macro_all(name)[self.s:self.e]

    def feat(self, name):
        v = (self.ctx.get("features") or {}).get(name)
        if not v:
            return None
        key = "feat:" + name
        if key not in self._memo:
            out = [_f(x) for x in v[:self.N]]
            self._memo[key] = out + [None] * (self.N - len(out))
        return self._memo[key]

    def feat_last(self, name, max_age=10):
        v = self.feat(name)
        if not v:
            return None
        for i in range(len(v) - 1, max(-1, len(v) - 1 - max_age), -1):
            if v[i] is not None:
                return v[i]
        return None

    def spot(self):
        c = self.close_all[self.e - 1] if self.e > 0 else None
        return c if c is not None and c > 0 else (self.P[-1] if self.P else None)

    # ---------------------------------------------------------------- evidence (horizon engine)

    def evidence(self, feature, own_percentile=None):
        if not feature:
            return None
        rows = (self.ctx.get("signals") or {}).get(feature)
        if not rows:
            return None
        recs = rows.items() if isinstance(rows, dict) else [(r.get("horizon"), r) for r in rows]
        best = None
        for lab, rec in recs:
            if not isinstance(rec, dict):
                continue
            u = _f(rec.get("usefulness"))
            if u is None:
                continue
            if best is None or abs(u) > abs(best[1]["_u"]):
                best = (lab or rec.get("horizon"), dict(rec, _u=u))
        if best is None:
            return None
        lab, rec = best
        u = rec["_u"]
        conf = _f(rec.get("confidence"))
        if conf is None and _f(rec.get("p")) is not None:
            conf = _clamp(1.0 - _f(rec.get("p")), 0.0, 1.0)
        z = self.feat_z(feature)
        if z is None and own_percentile is not None:
            z = qs.norm_ppf(_clamp(own_percentile, 0.01, 0.99))
        sig = None
        if z is not None:
            direction = rec.get("direction") or (1 if u >= 0 else -1)
            strength = min(1.0, abs(u) / 0.15) if abs(u) >= 0.02 else 0.0
            sig = _label(math.tanh(z / 1.5) * (1 if direction >= 0 else -1) * strength)
        return {"usefulness": _sig(u), "confidence": _sig(conf), "best_horizon": lab,
                "hit_rate": _sig(_f(rec.get("hit_rate"))), "ic": _sig(_f(rec.get("ic"))),
                "n": rec.get("n"), "n_eff": _sig(_f(rec.get("n_eff"))), "signal": sig}

    def feat_z(self, name):
        key = "featz:" + name
        if key in self._memo:
            return self._memo[key]
        z = None
        v = self.feat(name)
        if v:
            vals = [x for x in v if x is not None]
            last = self.feat_last(name)
            if last is not None and len(vals) >= 30:
                sd = _sd(vals)
                if sd > 0:
                    z = (last - _mean(vals)) / sd
        self._memo[key] = z
        return z

    # ---------------------------------------------------------------- result builder

    def res(self, eid, value, fmt="num", variables=None, hist=None, feature=None, sscore=None, interp=None,
            applies=True, note=None, regime=None, display=None, pool=None, meta=None, experimental=False,
            regime_from_pct=None):
        eq = meta or EQ_BY_ID[eid]
        if isinstance(value, float) and not math.isfinite(value):
            value = None
        vals = []
        h_dates, h_vals, prev = [], [], None
        extra_hist = {}
        if hist:
            h_dates = list(hist.get("dates") or [])
            h_vals = [_f(x) for x in hist.get("values") or []]
            prev = _f(hist.get("prev"))
            for k in ("bins", "counts"):
                if k in hist:
                    extra_hist[k] = hist[k]
            vals = [x for x in h_vals if x is not None]
        numeric = isinstance(value, (int, float)) and not isinstance(value, bool)
        pct = None
        source = [x for x in pool if x is not None] if pool else vals
        if numeric and len(source) >= 10:
            less = sum(1 for x in source if x < value)
            eq_ = sum(1 for x in source if x == value)
            pct = round((less + 0.5 * eq_) / len(source), 3)
        direction = None
        if numeric and prev is not None and len(vals) >= 5:
            sd = _sd(vals)
            d = value - prev
            if abs(d) <= 0.25 * sd or sd == 0 and d == 0:
                direction = "Stable"
            else:
                direction = "Increasing" if d > 0 else "Decreasing"
        if regime is None and regime_from_pct and pct is not None:
            hi_lab, lo_lab = regime_from_pct
            regime = hi_lab if pct >= 0.8 else (lo_lab if pct <= 0.2 else None)
        ev = self.evidence(feature, pct) if feature else None
        signal = ev["signal"] if ev and ev.get("signal") else _label(sscore) if sscore is not None else None
        disp = display if display is not None else _fmt(value, fmt)
        name = eq.get("name")
        if callable(interp):
            try:
                interp = interp(pct)
            except Exception:  # noqa: BLE001
                interp = None
        if not interp:
            if value is None:
                interp = "%s could not be computed for this asset." % name
            else:
                interp = "%s is %s%s." % (name, disp, (", %s of its own history" % _ordinal(pct)) if pct is not None
                                          else "")
        notes = [note] if note else []
        out = {
            "id": eq["id"], "name": name, "family": eq.get("family") or _family(eq),
            "formula": eq.get("latex", ""), "variables": variables or {}, "value": value if numeric or
            isinstance(value, bool) or value is None else value, "display": disp, "fmt": fmt,
            "percentile": pct, "direction": direction, "signal": signal,
            "usefulness": ev["usefulness"] if ev else None, "confidence": ev["confidence"] if ev else None,
            "best_horizon": ev["best_horizon"] if ev else None, "hit_rate": ev["hit_rate"] if ev else None,
            "ic": ev["ic"] if ev else None, "n": ev["n"] if ev else None, "n_eff": ev["n_eff"] if ev else None,
            "regime": regime, "interpretation": interp,
            "history": dict({"dates": h_dates, "values": [_sig(x, 6) for x in h_vals]}, **extra_hist),
            "applies": bool(applies), "note": "; ".join(notes) if notes else (eq.get("note") or ""),
        }
        if experimental:
            out["experimental"] = True
            out["note"] = "Experimental. " + out["note"]
        if eq.get("note"):
            out["about"] = eq.get("note")
        return _clean(out)

    def empty(self, eid, note, meta=None, applies=True, experimental=False):
        return self.res(eid, None, "num", note=note, meta=meta, applies=applies, experimental=experimental)

    # ================================================================ shared computations

    @_memo
    def cum_lr(self):
        return _pref(self.lr)

    @_memo
    def cum_lr2(self):
        return _pref2(self.lr)

    @_memo
    def ols_mkt(self):
        a, m = self.pairs(252)
        fit = qr.ols_fit(qr.add_constant(m), a)
        fit["n"] = len(a)
        fit["x"], fit["y"] = m, a
        return fit

    @_memo
    def beta_hist(self):
        pa, pm = self.pa, self.pm

        def beta(lo, hi):
            return qp.market_beta(pa[lo:hi], pm[lo:hi])
        return _roll(len(pa), min(252, max(MIN_OBS, len(pa))), beta, self.pdates)

    @_memo
    def gbm(self):
        x = self.win(756)
        sd = _sd(x)
        sigma = sd * math.sqrt(ANN)
        mu = _mean(x) * ANN + 0.5 * sigma * sigma
        return {"mu": mu, "sigma": sigma, "n": len(x), "m_d": _mean(x), "sd_d": sd}

    @_memo
    def garch(self):
        x = self.win(756, 100)
        fit = qv.garch_fit(x, fast=False)
        m = _mean(x)
        full = [r - m for r in self.lr]
        h = qv.garch_variance(full, fit["omega"], fit["alpha"], fit["beta"])
        fit["cond_vol"] = [math.sqrt(max(v, 0.0) * ANN) for v in h[1:]]
        fit["mean"] = m
        fit["n"] = len(x)
        return fit

    @_memo
    def ou(self):
        self.need(MIN_OBS)
        lp = [math.log(v) for v in self.P[-253:]]
        c, phi, s2 = qt.fit_ar1(lp)
        out = {"c": c, "phi": phi, "s2": s2, "x0": lp[-1], "n": len(lp)}
        try:
            out["adf_ok"] = bool(qt.adf_test(lp, 1, "ct")["stationary"])
        except (ValueError, ZeroDivisionError):
            out["adf_ok"] = None
        if 0.0 < phi < 1.0:
            k = -math.log(phi)
            out.update(kappa_d=k, kappa=k * ANN, theta=c / (1.0 - phi),
                       sigma=math.sqrt(max(0.0, s2 * 2.0 * k / (1.0 - phi * phi))) * math.sqrt(ANN))
        return out

    @_memo
    def ml_data(self):
        return _ml_dataset(self)

    @_memo
    def ml_models(self):
        return _ml_fit(self)

    @_memo
    def nn_model(self):
        return _nn_fit(self)

    @_memo
    def port(self):
        return _portfolio_inputs(self)

    @_memo
    def curve(self):
        pts = {}
        for t, nm in ((0.25, "y3m"), (2.0, "y2"), (5.0, "y5"), (10.0, "y10"), (30.0, "y30")):
            v = self.macro_last(nm)
            if v is not None:
                pts[t] = v
        if not pts:
            raise _Insufficient("no Treasury yields in the context")
        return pts

    def curve_at(self, idx):
        pts = {}
        for t, nm in ((0.25, "y3m"), (2.0, "y2"), (5.0, "y5"), (10.0, "y10"), (30.0, "y30")):
            v = self.macro_at(nm, idx)
            if v is not None:
                pts[t] = v
        return pts

    @_memo
    def fi(self):
        return _fixed_income_inputs(self)

    @_memo
    def opt(self):
        S = self.spot()
        if S is None:
            raise _Insufficient("no price")
        x = self.win(60)
        sigma = _sd(x) * math.sqrt(ANN)
        if sigma <= 0:
            raise _Insufficient("zero realised volatility")
        q = _f(self.asset.get("dividend_yield")) or 0.0
        return {"S": S, "K": S, "r": self.rf, "q": q, "T": 0.25, "sigma": sigma}

    @_memo
    def swap(self):
        return _swap_inputs(self)

    @_memo
    def cred(self):
        s = self.macro_last("credit_spread")
        how = "BAA − 10Y (credit_spread)"
        if s is None:
            baa, y10 = self.macro_last("baa"), self.macro_last("y10")
            if baa is None or y10 is None:
                raise _Insufficient("credit spread unavailable")
            s, how = baa - y10, "BAA − 10Y computed from the yields"
        s = max(s, 1e-5)
        lam = s / (1.0 - RECOVERY)
        r = self.macro_last("y5") or self.rf
        times, acc, dfs, q = qc.cds_schedule(lam, r, 5.0, 4)
        return {"s": s, "lam": lam, "r": r, "acc": acc, "dfs": dfs, "q": q, "how": how}

    @_memo
    def credit_hist(self):
        v = self.macro_all("credit_spread")
        if not any(x is not None for x in v):
            baa, y10 = self.macro_all("baa"), self.macro_all("y10")
            v = [a - b if a is not None and b is not None else None for a, b in zip(baa, y10)]
        idx = [i for i, x in enumerate(v) if x is not None]
        if len(idx) < 10:
            return None
        return {"dates": [self.dates_all[i] for i in idx], "values": [v[i] for i in idx]}

    @_memo
    def heston(self):
        return _heston(self)

    @_memo
    def universe(self):
        return _universe(self)

    @_memo
    def vol20(self):
        """Full 20-day annualised realised volatility series (indexed like lr from position 19)."""
        self.need(MIN_OBS)
        c2 = self.cum_lr2()
        c1 = self.cum_lr()
        w = 20
        out = []
        for hi in range(w, len(self.lr) + 1):
            s1 = c1[hi] - c1[hi - w]
            s2 = c2[hi] - c2[hi - w]
            var = max(0.0, (s2 - s1 * s1 / w) / (w - 1))
            out.append(math.sqrt(var * ANN))
        return out, self.rd[w - 1:]


# ============================================================================ handlers (catalogue)

_H = {}


def _eq(*ids):
    def deco(fn):
        for i in ids:
            _H[i] = fn
        return fn
    return deco


# ---------------------------------------------------------------------------- 1-20 returns & statistics

@_eq("1", "2")
def _e_ret(p, eid):
    if len(p.lr) < 1:
        raise _Insufficient("needs two prices")
    simple = eid == "1"
    series = p.sr if simple else p.lr
    v = series[-1]
    h = {"dates": p.rd[-MAX_POINTS:], "values": series[-MAX_POINTS:]}
    var = _vars(P_t=p.P[-1], P_prev=p.P[-2], D_t=0.0) if simple else _vars(P_t=p.P[-1], P_prev=p.P[-2])
    return p.res(eid, v, "ret", var, h, feature=EQ_FEATURE.get(eid), pool=series,
                 interp=lambda pc: "The latest session's %s return was %s%s." % (
                     "total" if simple else "log", _fmt(v, "ret"),
                     (" — a %s move (%s of all daily returns)" % (
                         "large" if pc is not None and (pc > 0.95 or pc < 0.05) else "typical", _ordinal(pc)))
                     if pc is not None else ""),
                 note="Adjusted close: dividends are already reinvested, so D_t = 0." if simple else None)


@_eq("3", "4")
def _e_cum(p, eid):
    x = p.win(252)
    n = len(x)
    c = p.cum_lr()
    L = len(p.lr)
    vals = [c[hi] - c[hi - n] for hi in range(n, L + 1)]
    if eid == "3":
        v = qs.cumulative_return(p.sr[-n:])
        vals = [math.exp(a) - 1.0 for a in vals]
    else:
        v = qs.cumulative_log_return(x)
    h = _series(p.rd[n - 1:], vals)
    vol = _sd(x) * math.sqrt(ANN)
    z = (sum(x) / vol) if vol > 0 else 0.0
    return p.res(eid, v, "ret", _vars(window=n, start_price=p.P[-n - 1], end_price=p.P[-1]), h,
                 feature=EQ_FEATURE.get(eid), sscore=_clamp(z / 3.0, -0.45, 0.45),
                 interp=lambda pc: "Over the last %d sessions the asset returned %s (%s)%s." % (
                     n, _fmt(v, "ret"), "compounded" if eid == "3" else "sum of log returns",
                     (", %s of its rolling 1-year history" % _ordinal(pc)) if pc is not None else ""),
                 note="Rule-of-thumb momentum lean (1-year return / volatility) unless the horizon engine has "
                      "tested evidence.")


@_eq("5")
def _e_ann(p, eid):
    p.need(MIN_OBS)
    n = min(1260, len(p.sr))
    v = qs.annualized_return(p.sr[-n:])
    c = p.cum_lr()
    L = len(p.lr)
    vals = [math.exp((c[hi] - c[hi - n]) * ANN / n) - 1.0 for hi in range(n, L + 1)]
    h = _series(p.rd[n - 1:], vals)
    return p.res(eid, v, "pct", _vars(sessions=n, years=n / ANN), h,
                 interp=lambda pc: "Compounded at %s a year over the last %.1f years%s." % (
                     _fmt(v, "pct"), n / ANN, (" (%s of rolling history)" % _ordinal(pc)) if pc is not None else ""))


@_eq("6", "7", "13")
def _e_mean(p, eid):
    if eid == "13":
        x = p.swin(10 ** 9)
        probs = [1.0 / len(x)] * len(x)
        v = qs.expected_value(x, probs)
        return p.res(eid, v, "ret", _vars(n=len(x), probability_each=1.0 / len(x), annualised=v * ANN),
                     interp="Weighting every past session equally, the expected daily return is %s (≈ %s a year)." % (
                         _fmt(v, "ret"), _fmt(v * ANN, "pct")),
                     note="Expected value of the empirical distribution of all daily simple returns in the history.")
    x = p.swin(252)
    n = len(x)
    if eid == "6":
        v = qs.mean(x)
        c = _pref(p.sr)
        vals = [(c[hi] - c[hi - n]) / n for hi in range(n, len(p.sr) + 1)]
        h = _series(p.rd[n - 1:], vals)
        return p.res(eid, v, "ret", _vars(window=n, annualised=v * ANN), h,
                     interp="The average daily return over the last %d sessions is %s (≈ %s annualised)." % (
                         n, _fmt(v, "ret"), _fmt(v * ANN, "pct")))
    v = qs.geometric_mean_return(x)
    return p.res(eid, v, "ret", _vars(window=n, arithmetic_mean=qs.mean(x)),
                 interp="The compounded (geometric) daily return is %s, %s the arithmetic mean — the gap is the "
                        "volatility drag." % (_fmt(v, "ret"), "below" if v < qs.mean(x) else "above"))


@_eq("8", "9")
def _e_var(p, eid):
    x = p.win(252)
    n = len(x)
    c1, c2 = p.cum_lr(), p.cum_lr2()
    vals = []
    for hi in range(n, len(p.lr) + 1):
        s1, s2 = c1[hi] - c1[hi - n], c2[hi] - c2[hi - n]
        var = max(0.0, (s2 - s1 * s1 / n) / (n - 1))
        vals.append(var if eid == "8" else math.sqrt(var * ANN))
    h = _series(p.rd[n - 1:], vals)
    if eid == "8":
        v = qs.variance(x)
        return p.res(eid, v, "sci", _vars(window=n, annualised_variance=v * ANN), h, feature=EQ_FEATURE[eid],
                     interp=lambda pc: "Daily return variance is %s (annualised %s)%s." % (
                         _fmt(v, "sci"), _fmt(v * ANN, "num"),
                         ("; %s of its rolling history" % _ordinal(pc)) if pc is not None else ""))
    v = qs.stdev(x) * math.sqrt(ANN)
    return p.res(eid, v, "pct", _vars(window=n, daily_sd=qs.stdev(x)), h, feature=EQ_FEATURE[eid],
                 regime_from_pct=("High volatility", "Low volatility"),
                 interp=lambda pc: "Annualised volatility over the last year is %s%s." % (
                     _fmt(v, "pct"), (", %s of its history: %s risk" % (_ordinal(pc), _level(pc)))
                     if pc is not None else ""),
                 note="Daily standard deviation × √252.")


@_eq("10", "11")
def _e_cov(p, eid):
    a, m = p.pairs(252)
    fn = qs.covariance if eid == "10" else qs.correlation
    v = fn(a, m)
    pa, pm = p.pa, p.pm
    n = len(a)
    h = _roll(len(pa), n, lambda lo, hi: fn(pa[lo:hi], pm[lo:hi]), p.pdates)
    if eid == "10":
        return p.res(eid, v, "sci", _vars(window=n, annualised=v * ANN), h, feature=EQ_FEATURE[eid],
                     interp="Daily covariance with SPY is %s (%s annualised)." % (_fmt(v, "sci"), _fmt(v * ANN, "num")))
    return p.res(eid, v, "ratio", _vars(window=n), h, feature=EQ_FEATURE[eid],
                 interp=lambda pc: "Correlation with SPY is %s: %s co-movement with the market%s." % (
                     _fmt(v, "ratio"), "strong" if abs(v) > 0.7 else "moderate" if abs(v) > 0.4 else "weak",
                     (" (%s of its history)" % _ordinal(pc)) if pc is not None else ""))


@_eq("12")
def _e_z(p, eid):
    p.need(MIN_OBS)
    P = p.P
    w = 50
    seg = P[-w:]
    mu, sd = _mean(seg), _sd(seg)
    if sd <= 0:
        raise _Insufficient("constant price")
    v = qs.zscore(P[-1], mu, sd)
    c1, c2 = _pref(P), _pref2(P)
    vals = []
    for hi in range(w, len(P) + 1):
        s1, s2 = c1[hi] - c1[hi - w], c2[hi] - c2[hi - w]
        var = (s2 - s1 * s1 / w) / (w - 1)
        vals.append((P[hi - 1] - s1 / w) / math.sqrt(var) if var > 1e-18 else None)
    h = _series(p.D[w - 1:], vals)
    return p.res(eid, v, "num", _vars(price=P[-1], mean_50=mu, sd_50=sd), h, feature=EQ_FEATURE[eid],
                 interp="The price is %s standard deviations %s its 50-day mean." % (
                     _fmt(abs(v), "num"), "above" if v >= 0 else "below"))


@_eq("14")
def _e_cond(p, eid):
    p.need(MIN_OBS)
    r = p.sr
    today = r[-1]
    x = [r[t + 1] for t in range(len(r) - 1)]
    y = [(1 if r[t] > 0 else -1 if r[t] < 0 else 0) for t in range(len(r) - 1)]
    ce = qs.conditional_expectation(x, y)
    key = 1 if today > 0 else -1 if today < 0 else 0
    v = ce.get(key)
    grp = [a for a, b in zip(x, y) if b == key]
    se = _sd(grp) / math.sqrt(len(grp)) if len(grp) > 1 else None
    t = v / se if se else 0.0
    return p.res(eid, v, "ret", _vars(E_next_given_up=ce.get(1), E_next_given_down=ce.get(-1),
                                      today_sign=key, n_group=len(grp), t_stat=t),
                 sscore=_tscore(t),
                 interp="After %s days like today, the next session has averaged %s (t = %.1f, %d cases)." % (
                     "up" if key > 0 else "down" if key < 0 else "flat", _fmt(v, "ret"), t, len(grp)),
                 note="Point in time: only pairs (r_t, r_t+1) already observed are used.")


@_eq("15")
def _e_bayes(p, eid):
    p.need(MIN_OBS)
    r = p.sr
    n = len(r) - 1
    up_next = [r[t + 1] > 0 for t in range(n)]
    down_today = [r[t] < 0 for t in range(n)]
    n_a = sum(up_next)
    n_b = sum(down_today)
    n_ab = sum(1 for a, b in zip(up_next, down_today) if a and b)
    if not n_a or not n_b:
        raise _Insufficient("no up or no down days")
    p_b_a, p_a, p_b = n_ab / n_a, n_a / n, n_b / n
    v = qs.bayes(p_b_a, p_a, p_b)
    down_now = r[-1] < 0
    return p.res(eid, v, "pct", _vars(P_down_today_given_up_tomorrow=p_b_a, P_up=p_a, P_down=p_b, n=n),
                 sscore=_clamp((v - 0.5) * 8.0, -0.45, 0.45) if down_now else None,
                 interp="After a down day the next day has been up %s of the time (base rate %s)%s." % (
                     _fmt(v, "pct"), _fmt(p_a, "pct"), "; today was a down day" if down_now else ""),
                 note="Counts over the whole history; the lean applies only when today was a down day.")


@_eq("16", "17")
def _e_shape(p, eid):
    x = p.win(252)
    n = len(x)
    fn = qs.skewness if eid == "16" else qs.excess_kurtosis
    v = fn(x)
    lr = p.lr
    h = _roll(len(lr), n, lambda lo, hi: fn(lr[lo:hi]), p.rd)
    if eid == "16":
        txt = "negative: large falls are more common than large rises" if v < -0.2 else \
            "positive: large rises are more common than large falls" if v > 0.2 else "close to symmetric"
        return p.res(eid, v, "num", _vars(window=n), h, feature=EQ_FEATURE[eid],
                     interp="Skewness of daily returns is %s — %s." % (_fmt(v, "num"), txt))
    return p.res(eid, v, "num", _vars(window=n), h, feature=EQ_FEATURE[eid],
                 interp="Excess kurtosis is %s — %s." % (
                     _fmt(v, "num"), "fat tails: extreme days are far more frequent than a normal curve implies"
                     if v > 1 else "tails close to normal" if v > -0.5 else "thin tails"))


@_eq("18")
def _e_se(p, eid):
    x = p.win(252)
    v = qs.standard_error(x)
    t = qs.mean(x) / v if v > 0 else None
    return p.res(eid, v, "pct", _vars(window=len(x), mean=qs.mean(x), t_mean=t),
                 interp="The mean daily return is known only to ±%s (t = %s): %s." % (
                     _fmt(v, "pct"), _fmt(t, "num"),
                     "not statistically different from zero" if t is not None and abs(t) < 2 else "significant"))


@_eq("19", "20")
def _e_sharpe(p, eid):
    x = p.win(252)
    n = len(x)
    rf_d = p.rf / ANN
    lr = p.lr
    if eid == "19":
        v = qs.sharpe_ratio(x, rf_d, ANN)
        c1, c2 = p.cum_lr(), p.cum_lr2()
        vals = []
        for hi in range(n, len(lr) + 1):
            s1, s2 = c1[hi] - c1[hi - n], c2[hi] - c2[hi - n]
            var = (s2 - s1 * s1 / n) / (n - 1)
            vals.append((s1 / n - rf_d) / math.sqrt(var) * math.sqrt(ANN) if var > 0 else None)
        h = _series(p.rd[n - 1:], vals)
    else:
        v = qs.sortino_ratio(x, rf_d, ANN)
        h = _roll(len(lr), n, lambda lo, hi: qs.sortino_ratio(lr[lo:hi], rf_d, ANN), p.rd)
    word = "Sharpe" if eid == "19" else "Sortino"
    return p.res(eid, v, "ratio", _vars(window=n, rf=p.rf, mean_daily=qs.mean(x)), h, feature=EQ_FEATURE[eid],
                 sscore=_clamp(v / 4.0, -0.4, 0.4),
                 interp=lambda pc: "The 1-year %s ratio is %s: %s risk-adjusted return%s." % (
                     word, _fmt(v, "ratio"), "strong" if v > 1 else "positive" if v > 0 else "negative",
                     (" (%s of its history)" % _ordinal(pc)) if pc is not None else ""),
                 note=("rf = %.2f%% (current 3-month rate)" % (p.rf * 100)) + ("; " + p.rf_note if p.rf_note else ""))


# ---------------------------------------------------------------------------- 21-33 regression

@_eq("21", "22", "23", "24", "25", "26", "27", "28", "29", "30", "31")
def _e_ols(p, eid):
    f = p.ols_mkt()
    a0, b1 = f["beta"]
    n = f["n"]
    y, yhat, res = f["y"], f["fitted"], f["residuals"]
    base = dict(alpha=a0, beta=b1, n=n)
    if eid == "21":
        rm = p.mret[-1]
        if rm is None:
            raise _Insufficient("no SPY return for the latest session")
        v = qr.linear_model([[1.0, rm]], [a0, b1])[0]
        return p.res(eid, v, "ret", _vars(r_market=rm, actual=p.lr[-1], **base),
                     interp="The market model (y = α + β·SPY) implied %s for the latest session; the actual log "
                            "return was %s." % (_fmt(v, "ret"), _fmt(p.lr[-1], "ret")))
    if eid == "22":
        h = p.beta_hist()
        return p.res(eid, b1, "ratio", _vars(alpha_daily=a0, alpha_ann=a0 * ANN, n=n), h, feature=EQ_FEATURE[eid],
                     interp=lambda pc: "Beta of %s: over the past year the asset moved about %s× the market%s." % (
                         _fmt(b1, "ratio"), _fmt(b1, "ratio"),
                         (" (%s of its history)" % _ordinal(pc)) if pc is not None else ""))
    if eid == "23":
        fit_vals = qr.fitted_values(qr.add_constant(f["x"]), f["beta"])
        v = _sd(fit_vals) * math.sqrt(ANN)
        return p.res(eid, v, "pct", _vars(total_vol=_sd(y) * math.sqrt(ANN), share=f["r2"], **base),
                     interp="Market-driven (fitted) volatility is %s of the asset's %s total." % (
                         _fmt(v, "pct"), _fmt(_sd(y) * math.sqrt(ANN), "pct")),
                     note="Standard deviation of the fitted values ŷ = α + β·r_SPY, annualised.")
    if eid == "24":
        v = qr.residuals([y[-1]], [yhat[-1]])[0]
        idio = _sd(res) * math.sqrt(ANN)
        h = {"dates": p.pdates[-n:], "values": res}
        return p.res(eid, v, "ret", _vars(idio_vol_ann=idio, **base), h, pool=res,
                     interp="The latest stock-specific (residual) return was %s; idiosyncratic volatility is %s a "
                            "year." % (_fmt(v, "ret"), _fmt(idio, "pct")))
    if eid == "25":
        v = qr.sse(y, yhat)
        return p.res(eid, v, "sci", _vars(**base), interp="Sum of squared residuals over %d sessions is %s." % (
            n, _fmt(v, "sci")))
    if eid == "26":
        v = qr.mse(y, yhat)
        return p.res(eid, v, "sci", _vars(**base), interp="Mean squared error of the market model is %s (daily)." % (
            _fmt(v, "sci")))
    if eid == "27":
        v = qr.rmse(y, yhat)
        return p.res(eid, v, "pct", _vars(annualised=v * math.sqrt(ANN), **base),
                     interp="The market model misses by %s per day on a root-mean-square basis." % _fmt(v, "pct"))
    if eid == "28":
        v = qr.mae(y, yhat)
        return p.res(eid, v, "pct", _vars(**base),
                     interp="The typical absolute miss of the market model is %s per day." % _fmt(v, "pct"))
    if eid == "29":
        v = qr.r_squared(y, yhat)
        pa, pm = p.pa, p.pm
        h = _roll(len(pa), n, lambda lo, hi: qs.correlation(pa[lo:hi], pm[lo:hi]) ** 2, p.pdates)
        return p.res(eid, v, "pct", _vars(**base), h,
                     interp=lambda pc: "The market explains %s of the asset's daily variance%s." % (
                         _fmt(v, "pct"), (" (%s of its history)" % _ordinal(pc)) if pc is not None else ""))
    if eid == "30":
        v = qr.adjusted_r_squared(f["r2"], n, 1)
        return p.res(eid, v, "pct", _vars(r2=f["r2"], k=1, **base),
                     interp="Adjusted R² is %s after the one-regressor penalty." % _fmt(v, "pct"))
    # 31: t-statistics
    ta, tb = f["t"]
    v = qr.t_statistic(a0, f["se"][0])
    return p.res(eid, v, "num", _vars(t_alpha=ta, t_beta=tb, se_alpha=f["se"][0], se_beta=f["se"][1], **base),
                 feature=EQ_FEATURE[eid], sscore=_tscore(ta),
                 interp="Alpha's t-statistic is %s (beta's %s): alpha is %s." % (
                     _fmt(ta, "num"), _fmt(tb, "num"), "statistically significant" if abs(ta) >= 2 else
                     "not distinguishable from zero"))


@_eq("32")
def _e_loglin(p, eid):
    p.need(MIN_OBS)
    n = min(1260, p.L)
    P = p.P[-n:]
    X = [[1.0, i / ANN] for i in range(n)]
    b = qr.log_linear_regression(X, P)
    g = math.exp(b[1]) - 1.0
    dev = math.log(P[-1]) - (b[0] + b[1] * (n - 1) / ANN)
    lp = [math.log(v) for v in p.P]

    def growth(lo, hi):
        xs = list(range(lo, hi, 5))
        return math.exp(qr.simple_regression([i / ANN for i in xs], [lp[i] for i in xs])[1]) - 1.0
    h = _roll(p.L, n, growth, p.D, maxp=130)
    return p.res(eid, g, "pct", _vars(intercept=b[0], slope=b[1], years=n / ANN, deviation_from_trend=dev), h,
                 sscore=_clamp(g / 0.4, -0.4, 0.4),
                 interp="The %.1f-year log-linear trend grows %s a year; the price is %s %s the trend line." % (
                     n / ANN, _fmt(g, "pct"), _fmt(abs(dev), "pct"), "above" if dev >= 0 else "below"),
                 note="ln P regressed on time in years; growth = e^slope − 1.")


@_eq("33")
def _e_inter(p, eid):
    p.need(MIN_OBS)
    vix = p.macro_seg("vix")
    rows, ys = [], []
    for i in range(len(p.lr)):
        m = p.mret[i]
        a, b = vix[i + 1], vix[i]
        if m is None or a is None or b is None:
            continue
        rows.append((m, a - b))
        ys.append(p.lr[i])
    rows, ys = rows[-252:], ys[-252:]
    if len(ys) < MIN_OBS:
        raise _Insufficient("VIX history unavailable")
    b = qr.interaction_model([r[0] for r in rows], [r[1] for r in rows], ys)
    X = [[1.0, r[0], r[1], r[0] * r[1]] for r in rows]
    t = qr.coef_t_stats(X, ys, b)
    return p.res(eid, b[3], "num", _vars(b0=b[0], b_market=b[1], b_dvix=b[2], b_interaction=b[3], t=t, n=len(ys)),
                 feature=EQ_FEATURE[eid],
                 interp="When VIX rises one point, the asset's market beta shifts by %s (t = %s)%s." % (
                     _fmt(b[3], "num"), _fmt(t[3], "num"), "" if abs(t[3]) >= 2 else " — not significant"),
                 note="Asset log return ~ SPY return + ΔVIX + SPY×ΔVIX over the last year.")


# ---------------------------------------------------------------------------- 34-43 time series

@_eq("34")
def _e_arp(p, eid):
    x = p.win(1260, 100)
    best = None
    for k in range(1, 6):
        fit = qt.fit_ar(x[5 - k:], k)
        sse = math.fsum(e * e for e in fit["residuals"])
        n = len(fit["residuals"])
        aic = n * math.log(sse / n) + 2 * (k + 1)
        if best is None or aic < best[0]:
            best = (aic, k, fit)
    aic, k, fit = best
    f = fit["const"] + sum(fit["phi"][i] * x[-1 - i] for i in range(k))
    return p.res(eid, f, "ret", _vars(p=k, const=fit["const"], phi=fit["phi"], aic=aic, n=len(x)),
                 interp="AIC picks AR(%d); its one-day-ahead return forecast is %s — autoregressive effects in daily "
                        "returns are small." % (k, _fmt(f, "ret")),
                 note="p chosen by AIC over 1..5 on the last %d daily returns." % len(x))


@_eq("35")
def _e_ar1(p, eid):
    x = p.win(252)
    c, phi, s2 = qt.fit_ar1(x)
    lr = p.lr
    h = _roll(len(lr), len(x), lambda lo, hi: qt.fit_ar1(lr[lo:hi])[1], p.rd)
    t = phi * math.sqrt(len(x))
    return p.res(eid, phi, "ratio", _vars(c=c, phi=phi, sigma2=s2, t_approx=t, n=len(x)), h,
                 feature=EQ_FEATURE[eid],
                 interp="AR(1) coefficient %s: %s (t ≈ %s)." % (
                     _fmt(phi, "ratio"), "today's return tends to continue tomorrow" if phi > 0.05 else
                     "today's return tends to reverse tomorrow" if phi < -0.05 else "almost no day-to-day memory",
                     _fmt(t, "num")))


@_eq("36")
def _e_ma(p, eid):
    x = p.win(1260, 100)
    fit = qt.fit_ma(x, 1)
    th = fit["theta"][0]
    return p.res(eid, th, "ratio", _vars(mu=fit["mu"], theta=th, sigma2=fit["sigma2"], n=len(x)),
                 interp="MA(1) coefficient θ = %s: yesterday's shock carries %s into today's return." % (
                     _fmt(th, "ratio"), "a small echo" if abs(th) < 0.1 else "a noticeable echo"))


@_eq("37")
def _e_arma(p, eid):
    x = p.win(1260, 100)
    fit = qt.fit_arma(x, 1, 1)
    c, phi, th = fit["const"], fit["phi"][0], fit["theta"][0]
    f = c + phi * x[-1] + th * fit["residuals"][-1]
    return p.res(eid, f, "ret", _vars(const=c, phi=phi, theta=th, sigma2=fit["sigma2"], n=len(x)),
                 interp="ARMA(1,1) (φ = %s, θ = %s) forecasts %s for the next session." % (
                     _fmt(phi, "ratio"), _fmt(th, "ratio"), _fmt(f, "ret")))


@_eq("38", "39")
def _e_diff(p, eid):
    if p.L < 3:
        raise _Insufficient("needs three prices")
    if eid == "38":
        d = qt.diff(p.P[-MAX_POINTS - 1:])
        v = d[-1]
        return p.res(eid, v, "price", _vars(P_t=p.P[-1], P_prev=p.P[-2]),
                     {"dates": p.D[-len(d):], "values": d}, pool=d,
                     interp="The adjusted price changed by %s in the latest session." % _fmt(v, "price"))
    lp = [math.log(v) for v in p.P[-MAX_POINTS - 2:]]
    d2 = qt.diff_n(lp, 2)
    v = d2[-1]
    return p.res(eid, v, "ret", _vars(r_t=p.lr[-1], r_prev=p.lr[-2]), {"dates": p.D[-len(d2):], "values": d2},
                 pool=d2, interp="The second difference of log price (change in the daily return) is %s." % _fmt(v, "ret"))


@_eq("40")
def _e_arima(p, eid):
    p.need(100)
    n = min(1261, p.L)
    lp = [math.log(v) for v in p.P[-n:]]
    fit = qt.fit_arima(lp, 1, 1, 1)
    d = qt.diff(lp)
    c, phi, th = fit["const"], fit["phi"][0], fit["theta"][0]
    step = c + phi * d[-1] + th * fit["residuals"][-1]
    v = p.P[-1] * math.exp(step)
    return p.res(eid, v, "price", _vars(const=c, phi=phi, theta=th, expected_change=step, n=n), feature=None,
                 interp="ARIMA(1,1,1) on log price forecasts %s for the next session (%s)." % (
                     _fmt(v, "price"), _fmt(step, "ret")))


@_eq("41")
def _e_acf(p, eid):
    x = p.win(1260, MIN_OBS)
    rho = qt.acf(x, 5)
    band = 1.96 / math.sqrt(len(x))
    lr = p.lr
    w = min(252, len(x))
    h = _roll(len(lr), w, lambda lo, hi: qt.autocorrelation(lr[lo:hi], 1), p.rd)
    sig = [k + 1 for k, r in enumerate(rho) if abs(r) > band]
    return p.res(eid, rho[0], "ratio", _vars(rho=rho, band_95=band, n=len(x)), h, feature=EQ_FEATURE[eid],
                 interp="Lag-1 autocorrelation is %s; %s." % (
                     _fmt(rho[0], "ratio"), ("lags %s exceed the ±%s noise band" % (sig, _fmt(band, "ratio")))
                     if sig else "no lag up to 5 is outside the noise band"))


@_eq("42")
def _e_stat(p, eid):
    x = p.win(1260, MIN_OBS)
    chk = qt.stationarity_check(x)
    lp = [math.log(v) for v in p.P[-len(x):]]
    chk_p = qt.stationarity_check(lp)
    ok = bool(chk["stationary"])
    return p.res(eid, ok, "bool", _vars(mean_shift_z=chk["mean_shift_z"], var_ratio=chk["var_ratio"],
                                        log_price_stationary=bool(chk_p["stationary"]), n=len(x)),
                 display="Stationary" if ok else "Not stationary",
                 interp="Daily returns %s weakly stationary across the two halves of the sample (variance ratio %s); "
                        "the log price %s." % ("look" if ok else "do not look", _fmt(chk["var_ratio"], "ratio"),
                                                "does too" if chk_p["stationary"] else "does not, as expected"),
                 note="Heuristic split-sample check, not a formal test.")


@_eq("43")
def _e_adf(p, eid):
    p.need(MIN_OBS)
    n = min(253, p.L)
    lp_all = [math.log(v) for v in p.P]
    lp = lp_all[-n:]
    r = qt.adf_test(lp, 1, "ct")
    pv = _adf_p(r["t_stat"], "ct")
    rr = qt.adf_test(p.lr[-min(252, len(p.lr)):], 1, "c")
    h = _roll(len(lp_all), n, lambda lo, hi: qt.adf_test(lp_all[lo:hi], 1, "ct")["t_stat"], p.D, maxp=120)
    return p.res(eid, r["t_stat"], "num", _vars(crit_5=r["crit_5"], gamma=r["gamma"], p_approx=pv,
                                                returns_t=rr["t_stat"], n=n), h, feature=EQ_FEATURE[eid],
                 interp="ADF t = %s (5%% critical %s, p ≈ %s): the log price %s." % (
                     _fmt(r["t_stat"], "num"), r["crit_5"], _fmt(pv, "ratio"),
                     "looks mean-reverting around its trend" if r["stationary"] else
                     "behaves like a random walk (no reliable mean reversion)"),
                 note="Log price, last year, constant + trend, 1 lag; p-value interpolated from Dickey-Fuller tables.")


# ---------------------------------------------------------------------------- 44-50 volatility

def _vol_interp(label, v, pc):
    return "%s is %s%s." % (label, _fmt(v, "pct"), (", %s of its history: %s near-term risk" % (_ordinal(pc), _level(pc)))
                            if pc is not None else "")


def _vol_score(pc):
    return None if pc is None else _clamp(-(pc - 0.5) * 0.8, -0.4, 0.4)


@_eq("44")
def _e_ewma(p, eid):
    p.need(MIN_OBS)
    h = qv.ewma_variance(p.lr, 0.94)
    series = [math.sqrt(max(v, 0.0) * ANN) for v in h[1:]]
    v = series[-1]
    hist = _series(p.rd, series)
    pool = series[-756:]
    pc = _pctile(v, pool)
    return p.res(eid, v, "pct", _vars(lam=0.94, daily_var=h[-1]), hist, feature=EQ_FEATURE[eid], pool=pool,
                 sscore=_vol_score(pc), regime_from_pct=("High volatility", "Low volatility"),
                 interp=lambda pc: _vol_interp("EWMA volatility (λ = 0.94)", v, pc),
                 note="Percentile vs the last 3 years; lean = rule of thumb (high volatility → cautious).")


def _pctile(v, pool):
    pool = [x for x in pool if x is not None]
    if v is None or len(pool) < 10:
        return None
    return (sum(1 for x in pool if x < v) + 0.5 * sum(1 for x in pool if x == v)) / len(pool)


@_eq("45")
def _e_arch(p, eid):
    x = p.win(1260, 100)
    m = _mean(x)
    r2 = [(v - m) ** 2 for v in x]
    a, b, _ = qr.simple_regression(r2[:-1], r2[1:])
    alpha = _clamp(b, 0.0, 0.95)
    omega = _mean(r2) * (1.0 - alpha)
    h = qv.arch_variance([v - m for v in x], omega, [alpha])
    series = [math.sqrt(max(v, 0.0) * ANN) for v in h[1:]]
    v = series[-1]
    return p.res(eid, v, "pct", _vars(omega=omega, alpha=alpha, n=len(x)), _series(p.rd[-len(x):], series),
                 pool=series, regime_from_pct=("High volatility", "Low volatility"),
                 interp=lambda pc: _vol_interp("ARCH(1) next-day volatility (α = %s)" % _fmt(alpha, "ratio"), v, pc),
                 note="α from OLS of r²_t on r²_{t−1} (clipped to [0, 0.95]); ω by variance targeting.")


@_eq("46")
def _e_garch(p, eid):
    g = p.garch()
    nxt = math.sqrt(g["next_var"] * ANN)
    cur = g["cond_vol"][-2] if len(g["cond_vol"]) > 1 else nxt
    pers = g["persistence"]
    lr_vol = math.sqrt(g["omega"] / (1.0 - pers) * ANN) if pers < 1 else None
    hl = math.log(0.5) / math.log(pers) if 0 < pers < 1 else None
    series = g["cond_vol"]
    hist = _series(p.rd, series)
    pool = series[-756:]
    pc = _pctile(nxt, pool)
    return p.res(eid, nxt, "pct", _vars(omega=g["omega"], alpha=g["alpha"], beta=g["beta"], persistence=pers,
                                        current_vol=cur, next_day_vol=nxt, long_run_vol=lr_vol,
                                        shock_half_life_days=hl, n=g["n"]), hist, feature=EQ_FEATURE[eid],
                 pool=pool, sscore=_vol_score(pc), regime_from_pct=("High volatility", "Low volatility"),
                 interp=lambda pc: "GARCH(1,1) next-day volatility is %s vs a long-run %s%s; shocks fade with a "
                                   "half-life of %s." % (_fmt(nxt, "pct"), _fmt(lr_vol, "pct"),
                                                         (" (%s of 3 years: %s risk)" % (_ordinal(pc), _level(pc)))
                                                         if pc is not None else "", _fmt(hl, "days")),
                 note="Fitted by maximum likelihood on the last 3 years with variance targeting.")


@_eq("47")
def _e_gjr(p, eid):
    g = p.garch()
    x = p.win(756, 100)
    m = _mean(x)
    r = [v - m for v in x]
    var = _mean([v * v for v in r])
    best = None
    for gam in (0.0, 0.02, 0.04, 0.06, 0.08, 0.10, 0.13, 0.16, 0.20):
        a = max(0.001, g["alpha"] - gam / 2.0)
        b = g["beta"]
        pers = a + gam / 2.0 + b
        if pers >= 0.999:
            continue
        w = var * (1.0 - pers)
        h = qv.gjr_garch_variance(r, w, a, gam, b, var)
        nll = 0.5 * math.fsum(math.log(hh) + rr * rr / hh for hh, rr in zip(h, r))
        if best is None or nll < best[0]:
            best = (nll, gam, a, b, w, h)
    if best is None:
        raise _Insufficient("GARCH persistence too close to 1")
    nll, gam, a, b, w, h = best
    v = math.sqrt(h[-1] * ANN)
    series = [math.sqrt(max(x_, 0.0) * ANN) for x_ in h[1:]]
    return p.res(eid, v, "pct", _vars(omega=w, alpha=a, gamma=gam, beta=b, n=len(x)),
                 _series(p.rd[-len(x):], series), feature=EQ_FEATURE[eid], pool=series,
                 regime_from_pct=("High volatility", "Low volatility"),
                 interp="GJR-GARCH next-day volatility is %s; the leverage term γ = %s %s." % (
                     _fmt(v, "pct"), _fmt(gam, "ratio"), "means falls raise volatility more than rises" if gam > 0
                     else "shows no down-move asymmetry"),
                 note="γ chosen on a small grid by likelihood, α and β from the GARCH(1,1) fit.")


@_eq("48", "49")
def _e_rv(p, eid):
    p.need(MIN_OBS)
    w = 21
    x = p.lr[-w:]
    c2 = p.cum_lr2()
    vals = [(c2[hi] - c2[hi - w]) for hi in range(w, len(p.lr) + 1)]
    if eid == "48":
        v = qv.realized_variance(x)
        hist = _series(p.rd[w - 1:], vals)
        return p.res(eid, v, "sci", _vars(window=w, annualised_vol=math.sqrt(v * ANN / w)), hist,
                     feature=EQ_FEATURE[eid], pool=vals[-756:],
                     interp=lambda pc: "21-day realised variance is %s%s." % (
                         _fmt(v, "sci"), (" (%s of 3 years)" % _ordinal(pc)) if pc is not None else ""))
    v = qv.realized_vol_annualized(x)
    series = [math.sqrt(s * ANN / w) for s in vals]
    pool = series[-756:]
    pc = _pctile(v, pool)
    return p.res(eid, v, "pct", _vars(window=w, realized_variance=qv.realized_variance(x)),
                 _series(p.rd[w - 1:], series), feature=EQ_FEATURE[eid], pool=pool, sscore=_vol_score(pc),
                 regime_from_pct=("High volatility", "Low volatility"),
                 interp=lambda pc: ("Volatility is in its %s: %s near-term risk (21-day realised %s)." % (
                     _ordinal(pc), _level(pc), _fmt(v, "pct"))) if pc is not None else
                 "21-day realised volatility is %s." % _fmt(v, "pct"),
                 note="Percentile vs the last 3 years.")


@_eq("50")
def _e_rbeta(p, eid):
    if len(p.pa) < 80:
        raise _Insufficient("needs 80 paired returns")
    n = min(1260, len(p.pa))
    rb = qv.rolling_beta(p.pa[-n:], p.pm[-n:], 60)
    dates = p.pdates[-n:][59:]
    v = rb[-1]
    return p.res(eid, v, "ratio", _vars(window=60, beta_252=p.ols_mkt()["beta"][1]), _series(dates, rb),
                 feature=EQ_FEATURE[eid], pool=rb,
                 interp=lambda pc: "The 60-day rolling beta is %s%s." % (
                     _fmt(v, "ratio"), (" (%s of 5 years)" % _ordinal(pc)) if pc is not None else ""))


# ---------------------------------------------------------------------------- 51-62 stochastic processes

@_eq("51", "52", "53", "54", "55")
def _e_gbm(p, eid):
    g = p.gbm()
    mu, sigma = g["mu"], g["sigma"]
    dt = 1.0 / ANN
    S = p.P[-1]
    drift_d = (mu - 0.5 * sigma * sigma) * dt
    base = dict(mu=mu, sigma=sigma, dt=dt, n=g["n"])
    note = "μ and σ calibrated on the last %d daily log returns." % g["n"]
    if eid in ("51", "52"):
        inc = [(r - drift_d) / sigma for r in p.lr[-g["n"]:]]
        if eid == "51":
            v = inc[-1]
            return p.res(eid, v, "num", _vars(z=v / math.sqrt(dt), mean_increment=_mean(inc),
                                              sd_increment=_sd(inc), theoretical_sd=math.sqrt(dt), **base),
                         pool=inc,
                         interp="The latest session corresponds to a Brownian shock ΔW = %s (%s standard deviations);"
                                " historical increments have sd %s vs √Δt = %s." % (
                                    _fmt(v, "num"), _fmt(v / math.sqrt(dt), "num"), _fmt(_sd(inc), "num"),
                                    _fmt(math.sqrt(dt), "num")), note=note)
        k = min(252, len(inc))
        path = [0.0]
        for d in inc[-k:]:
            path.append(path[-1] + d)
        v = path[-1]
        return p.res(eid, v, "num", _vars(T_years=k / ANN, expected=0.0, sd=math.sqrt(k / ANN), **base),
                     {"dates": p.rd[-k:], "values": path[1:]},
                     interp="The drift-adjusted Brownian path over the last year ended at W = %s (1 s.d. = %s): %s." % (
                         _fmt(v, "num"), _fmt(math.sqrt(k / ANN), "num"),
                         "an unusually strong year" if v > 1.5 else "an unusually weak year" if v < -1.5
                         else "within normal randomness"), note=note)
    if eid == "53":
        v = qsto.gbm_euler_step(S, mu, sigma, dt, 0.0)
        up, dn = qsto.gbm_euler_step(S, mu, sigma, dt, 1.0), qsto.gbm_euler_step(S, mu, sigma, dt, -1.0)
        return p.res(eid, v, "price", _vars(S=S, plus_1sd=up, minus_1sd=dn, **base),
                     interp="One Euler step expects %s tomorrow, with a ±1σ range of %s to %s." % (
                         _fmt(v, "price"), _fmt(dn, "price"), _fmt(up, "price")), note=note)
    if eid == "54":
        pts = {q: qsto.gbm_exact(S, mu, sigma, 1.0, qs.norm_ppf(q)) for q in (0.05, 0.5, 0.95)}
        v = pts[0.5]
        return p.res(eid, v, "price", _vars(S0=S, p5=pts[0.05], p50=v, p95=pts[0.95], mean=S * math.exp(mu), **base),
                     interp="Under GBM the 1-year median price is %s with a 90%% range of %s to %s." % (
                         _fmt(v, "price"), _fmt(pts[0.05], "price"), _fmt(pts[0.95], "price")),
                     note=note + " Closed form S_T = S_0 exp((μ − σ²/2)T + σ√T z).")
    drift, diff = qsto.ito_lemma(0.0, 1.0 / S, -1.0 / (S * S), mu * S, sigma * S)
    return p.res(eid, drift, "pct", _vars(f="ln S", f_x=1.0 / S, f_xx=-1.0 / (S * S), diffusion=diff, **base),
                 interp="By Itô's lemma ln S drifts at μ − σ²/2 = %s a year (diffusion %s)." % (
                     _fmt(drift, "pct"), _fmt(diff, "pct")), note=note)


def _ou_caveat(o):
    if o.get("adf_ok") is False:
        return " (but the ADF test does not confirm mean reversion, so treat this as descriptive)"
    return ""


@_eq("56", "57", "58")
def _e_ou(p, eid):
    o = p.ou()
    base = dict(phi=o["phi"], n=o["n"])
    if "kappa" not in o:
        return p.res(eid, None, "num", _vars(**base),
                     interp="No mean reversion detected in the log price over the last year (AR(1) φ = %s ≥ 1)." %
                            _fmt(o["phi"], "ratio"),
                     note="OU fit on log price, last 252 sessions: φ outside (0, 1), so κ is undefined.")
    k, th = o["kappa"], o["theta"]
    if eid == "56":
        return p.res(eid, k, "num", _vars(kappa=k, theta_price=math.exp(th), sigma=o["sigma"], **base),
                     interp="OU mean-reversion speed κ = %s a year toward a level of %s%s." % (
                         _fmt(k, "num"), _fmt(math.exp(th), "price"), _ou_caveat(o)),
                     note="OU on log price (last 252 sessions) from the AR(1) regression; κ = −ln φ × 252.")
    if eid == "57":
        v = math.exp(qsto.ou_expected(o["x0"], k, th, 0.25))
        chg = v / p.P[-1] - 1.0
        sd = o["sigma"] * math.sqrt(0.25)
        return p.res(eid, v, "price", _vars(current=p.P[-1], theta_price=math.exp(th), kappa=k, expected_change=chg),
                     sscore=_clamp(chg / sd / 3.0 if sd > 0 else 0.0, -0.4, 0.4) if o.get("adf_ok") else None,
                     interp="If the OU fit holds, the expected price in 3 months is %s (%s)%s." % (
                         _fmt(v, "price"), _fmt(chg, "ret"), _ou_caveat(o)),
                     note="Weak evidence: a one-year OU fit on a price series; treat as descriptive.")
    lp = [math.log(v) for v in p.P]

    def hl(lo, hi):
        _, ph, _ = qt.fit_ar1(lp[lo:hi])
        return qsto.half_life(-math.log(ph)) if 0 < ph < 1 else None
    hist = _roll(len(lp), min(253, len(lp)), hl, p.D, maxp=160)
    v = qsto.half_life(o["kappa_d"])
    return p.res(eid, v, "days", _vars(kappa_daily=o["kappa_d"], **base), hist, feature=EQ_FEATURE[eid],
                 interp="Deviations from the fitted mean halve in about %s%s%s." % (
                     _fmt(v, "days"), " — slow enough to be indistinguishable from a trend" if v > 252 else "",
                     _ou_caveat(o)))


@_eq("59", "60")
def _e_rates(p, eid):
    y = [v for v in p.macro_all("y3m") if v is not None][-756:]
    r0 = p.macro_last("y3m")
    if r0 is None or len(y) < MIN_OBS:
        v = p.rf
        return p.res(eid, v, "pct", _vars(r0=p.rf), note="3-month yield history unavailable: shows the current rate.",
                     interp="Without a rate history the 1-year expected short rate is taken as today's %s." % _fmt(v, "pct"))
    dt = 1.0 / ANN
    c, phi, s2 = qt.fit_ar1(y)
    note = "Calibrated by AR(1) on the last %d daily 3-month yields." % len(y)
    if 0.0 < phi < 1.0:
        a = -math.log(phi) / dt
        b = c / (1.0 - phi)
        sig = math.sqrt(max(0.0, s2 * 2.0 * a / (1.0 - phi * phi)))
    else:
        a, b, sig = 0.0, r0, math.sqrt(max(s2, 0.0) / dt)
        note += " No mean reversion found (φ ≥ 1): random-walk expectation."
    exp1 = qsto.ou_expected(r0, a, b, 1.0) if a > 0 else r0
    if eid == "59":
        zcb = qsto.vasicek_zcb_price(r0, a, b, sig, 1.0) if a > 0 else math.exp(-r0)
        return p.res(eid, exp1, "pct", _vars(r0=r0, a=a, b=b, sigma=sig, zcb_1y=zcb),
                     interp="Vasicek expects the 3-month rate to move from %s to %s in a year (long-run level %s)." % (
                         _fmt(r0, "pct"), _fmt(exp1, "pct"), _fmt(b, "pct")), note=note)
    sq = [(y[i] - y[i - 1]) / math.sqrt(y[i - 1]) for i in range(1, len(y)) if y[i - 1] > 1e-4]
    sig_c = _sd(sq) / math.sqrt(dt) if len(sq) > 10 else None
    feller = (2 * a * b >= sig_c ** 2) if sig_c is not None and a > 0 else None
    return p.res(eid, exp1, "pct", _vars(r0=r0, kappa=a, theta=b, sigma=sig_c, feller=feller),
                 interp="CIR shares Vasicek's mean path (%s in a year) but scales shocks by √r%s." % (
                     _fmt(exp1, "pct"), "" if feller is None else
                     "; the Feller condition %s" % ("holds (rate stays positive)" if feller else "fails")), note=note)


@_eq("61", "62")
def _e_heston(p, eid):
    h = p.heston()
    base = _vars(v0_vol=math.sqrt(h["v0"]), kappa=h["kappa"], theta_vol=math.sqrt(h["theta"]), xi=h["xi"],
                 rho=h["rho"], mu=h["mu"], paths=500)
    note = "v from 21-day realised variance; κ, θ, ξ from AR(1) on monthly realised variance; 500 seeded paths, " \
           "weekly steps." + (" " + h["note"] if h.get("note") else "")
    if eid == "61":
        v = h["p50"]
        return p.res(eid, v, "price", dict(base, p5=_sig(h["p5"]), p95=_sig(h["p95"]), mean=_sig(h["mean"])),
                     interp="Heston Monte Carlo puts the 1-year median at %s with a 90%% range of %s to %s "
                            "(ρ = %s)." % (_fmt(v, "price"), _fmt(h["p5"], "price"), _fmt(h["p95"], "price"),
                                           _fmt(h["rho"], "ratio")), note=note)
    v = h["vol_T"]
    return p.res(eid, v, "pct", base,
                 interp="Heston variance mean-reverts from %s toward %s volatility; the simulated 1-year-ahead level "
                        "averages %s." % (_fmt(math.sqrt(h["v0"]), "pct"), _fmt(math.sqrt(h["theta"]), "pct"),
                                          _fmt(v, "pct")), note=note)


def _heston(p):
    p.need(126)
    lr = p.lr
    blocks = []
    for end in range(len(lr), 20, -21):
        seg = lr[end - 21:end]
        blocks.append((sum(r * r for r in seg) * ANN / 21.0, sum(seg)))
    blocks = blocks[:240][::-1]
    v = [b[0] for b in blocks]
    R = [b[1] for b in blocks]
    dt = 21.0 / ANN
    note = None
    kappa, theta, xi, rho = 2.0, _mean(v), 0.5, -0.5
    if len(v) >= 8:
        c, phi, _ = qt.fit_ar1(v)
        if 0.0 < phi < 1.0:
            kappa = -math.log(phi) / dt
            theta = c / (1.0 - phi)
        else:
            note = "AR(1) on variance gave φ outside (0,1): κ = 2 assumed."
        theta = max(theta, 1e-6)
        res = [(v[i] - v[i - 1] - kappa * (theta - v[i - 1]) * dt) / math.sqrt(max(v[i - 1], 1e-8))
               for i in range(1, len(v))]
        xi = max(0.01, _sd(res) / math.sqrt(dt))
        dv = [v[i] - v[i - 1] for i in range(1, len(v))]
        try:
            rho = _clamp(qs.correlation(R[1:], dv), -0.99, 0.99)
        except (ZeroDivisionError, ValueError):
            rho = 0.0
    else:
        note = "Short history: κ = 2, ξ = 0.5, ρ = −0.5 assumed."
    v0 = qv.realized_variance(lr[-21:]) * ANN / 21.0
    g = p.gbm()
    rng = random.Random(0)
    S0 = p.P[-1]
    ST, VT = [], []
    for _ in range(500):
        s, vv = qsto.heston_paths(S0, v0, g["mu"], kappa, theta, xi, rho, 52, 1.0 / 52, seed=rng)
        ST.append(s[-1])
        VT.append(max(vv[-1], 0.0))
    ST.sort()
    return {"v0": v0, "kappa": kappa, "theta": theta, "xi": xi, "rho": rho, "mu": g["mu"],
            "p5": _quantile(ST, 0.05), "p50": _quantile(ST, 0.5), "p95": _quantile(ST, 0.95), "mean": _mean(ST),
            "vol_T": math.sqrt(_mean(VT)), "note": note}


# ---------------------------------------------------------------------------- 63-83 machine learning

_ML_PREF = ["ret_1m", "ret_3m", "mom_12_1", "rel_strength_6m", "ma_cross", "z_50", "dist_ma200", "rsi_14",
            "vol_20", "vol_ratio", "drawdown_252", "beta_252", "vix", "d_y10_3m", "credit_spread", "value_5y"]
_ML_H = 21
_ML_MAX_ROWS = 520


def _local_feature(p, name, t):
    a = p.adj_all
    s = p.s

    def px(i):
        return a[i] if i >= s and a[i] is not None else None
    if name in ("ret_1m", "ret_3m"):
        k = 21 if name == "ret_1m" else 63
        x0, x1 = px(t - k), px(t)
        return math.log(x1 / x0) if x0 and x1 else None
    if name == "mom_12_1":
        x0, x1 = px(t - 252), px(t - 21)
        return math.log(x1 / x0) if x0 and x1 else None
    if name == "vol_20":
        if t - 20 < s:
            return None
        r = [math.log(a[i] / a[i - 1]) for i in range(t - 19, t + 1)]
        return _sd(r) * math.sqrt(ANN)
    if name == "z_50":
        if t - 49 < s:
            return None
        seg = a[t - 49:t + 1]
        sd = _sd(seg)
        return (a[t] - _mean(seg)) / sd if sd > 0 else None
    if name == "dist_ma200":
        if t - 199 < s:
            return None
        return math.log(a[t]) - math.log(_mean(a[t - 199:t + 1]))
    return None


def _ml_dataset(p):
    if p.e <= 0:
        raise _Insufficient("no prices")
    last = p.e - 1
    idx = list(range(last, p.s - 1, -5))[::-1][-(_ML_MAX_ROWS + 10):]
    feats = p.ctx.get("features") or {}
    names = []
    for nm in _ML_PREF:
        col = p.feat(nm) if feats.get(nm) else None
        if not col:
            continue
        cov = sum(1 for t in idx if col[t] is not None) / max(1, len(idx))
        if cov >= 0.6 and col[last] is not None:
            names.append(nm)
        if len(names) >= 8:
            break
    source = "feature store"
    if len(names) >= 3:
        def get(nm, t):
            return p.feat(nm)[t]
    else:
        names = ["ret_1m", "ret_3m", "mom_12_1", "vol_20", "z_50", "dist_ma200"]
        source = "price-derived features (feature store unavailable)"

        def get(nm, t):
            return _local_feature(p, nm, t)
    rows, ys, ts = [], [], []
    xcur = None
    a = p.adj_all
    for t in idx:
        x = [get(nm, t) for nm in names]
        if any(v is None for v in x):
            continue
        if t == last:
            xcur = x
        if t + _ML_H <= last and a[t + _ML_H] is not None:
            rows.append(x)
            ys.append(math.log(a[t + _ML_H] / a[t]))
            ts.append(t)
    rows, ys, ts = rows[-_ML_MAX_ROWS:], ys[-_ML_MAX_ROWS:], ts[-_ML_MAX_ROWS:]
    if len(rows) < 40:
        raise _Insufficient("not enough point-in-time feature rows for the models (%d < 40)" % len(rows))
    if xcur is None:
        raise _Insufficient("current feature row incomplete")
    ntr = int(0.8 * len(rows))
    t_test = ts[ntr]
    tr = [i for i in range(ntr) if ts[i] + _ML_H < t_test]
    te = list(range(ntr, len(rows)))
    k = len(names)
    mu = [_mean([rows[i][j] for i in tr]) for j in range(k)]
    sd = [(_sd([rows[i][j] for i in tr]) or 1.0) for j in range(k)]

    def z(x):
        return [_clamp((v - m) / s, -5.0, 5.0) for v, m, s in zip(x, mu, sd)]
    Z = [z(r) for r in rows]
    return {"names": names, "source": source, "Xtr": [Z[i] for i in tr], "ytr": [ys[i] for i in tr],
            "Xte": [Z[i] for i in te], "yte": [ys[i] for i in te], "xcur": z(xcur),
            "train_dates": (p.dates_all[ts[tr[0]]], p.dates_all[ts[tr[-1]]]),
            "test_dates": (p.dates_all[ts[te[0]]], p.dates_all[ts[te[-1]]]), "n": len(rows)}


def _reg_metrics(pred, y, base):
    mse = _mean([(a - b) ** 2 for a, b in zip(pred, y)])
    sst = _mean([(b - base) ** 2 for b in y])
    hit = _mean([1.0 if (a > 0) == (b > 0) else 0.0 for a, b in zip(pred, y)])
    return {"mse": mse, "r2_oos": 1.0 - mse / sst if sst > 0 else None, "hit": hit}


def _ml_fit(p):
    d = p.ml_data()
    Xtr, ytr, Xte, yte, xc = d["Xtr"], d["ytr"], d["Xte"], d["yte"], d["xcur"]
    ybar = _mean(ytr)
    n, k = len(Xtr), len(Xtr[0])
    M = {"data": d}
    # linear (OLS)
    try:
        beta = qr.ols(qr.add_constant(Xtr), ytr)
        b0, w = beta[0], beta[1:]
    except ValueError:
        b0, w = qml.ridge(Xtr, ytr, 1e-6)
    lin = lambda x: qml.linear_predictor(x, w, b0)  # noqa: E731
    M["ols"] = {"b0": b0, "w": w, "cur": lin(xc), "tr_pred": [lin(x) for x in Xtr],
                "m": _reg_metrics([lin(x) for x in Xte], yte, ybar)}
    # logistic (IRLS)
    yb = [1.0 if v > 0 else 0.0 for v in ytr]
    ybte = [1.0 if v > 0 else 0.0 for v in yte]
    lw, lb = _logit_irls(Xtr, yb, l2=1.0)
    pte = [qml.logistic_predict_proba(x, lw, lb) for x in Xte]
    M["logit"] = {"w": lw, "b": lb, "z": qml.linear_predictor(xc, lw, lb), "p": qml.logistic_predict_proba(xc, lw, lb),
                  "acc": _mean([1.0 if (q > 0.5) == (t > 0.5) else 0.0 for q, t in zip(pte, ybte)]),
                  "bce": qml.binary_cross_entropy(ybte, pte),
                  "bce_base": qml.binary_cross_entropy(ybte, [_mean(yb)] * len(ybte)),
                  "hinge": _mean([qml.hinge_loss(2 * t - 1, qml.linear_predictor(x, lw, lb)) for x, t in zip(Xte, ybte)]),
                  "base_rate": _mean(yb)}
    # softmax over three classes (one-vs-rest logits)
    srt = sorted(ytr)
    q1, q2 = _quantile(srt, 1 / 3), _quantile(srt, 2 / 3)
    cls = lambda v: 0 if v <= q1 else (1 if v <= q2 else 2)  # noqa: E731
    ovr = []
    for c in range(3):
        ovr.append(_logit_irls(Xtr, [1.0 if cls(v) == c else 0.0 for v in ytr], l2=1.0, iters=10))
    sm = lambda x: qml.softmax([qml.linear_predictor(x, ww, bb) for ww, bb in ovr])  # noqa: E731
    ce = _mean([qml.cross_entropy([1.0 if cls(v) == c else 0.0 for c in range(3)], sm(x)) for x, v in zip(Xte, yte)])
    M["softmax"] = {"cur": sm(xc), "ce": ce, "thresholds": (q1, q2)}
    # ridge / lasso / elastic net
    lam_r = 0.1 * n
    rb0, rw = qml.ridge(Xtr, ytr, lam_r)
    rp = lambda x: qml.linear_predictor(x, rw, rb0)  # noqa: E731
    M["ridge"] = {"lam": lam_r, "b0": rb0, "w": rw, "cur": rp(xc), "m": _reg_metrics([rp(x) for x in Xte], yte, ybar),
                  "obj": qml.regularized_objective(ytr, [rp(x) for x in Xtr], rw, lam_r / n)}
    yc = [v - ybar for v in ytr]
    lam_max = max(abs(sum(Xtr[i][j] * yc[i] for i in range(n))) / n for j in range(k)) or 1e-6
    lb0, lwl = qml.lasso(Xtr, ytr, 0.5 * lam_max, max_iter=300)
    lp_ = lambda x: qml.linear_predictor(x, lwl, lb0)  # noqa: E731
    M["lasso"] = {"lam": 0.5 * lam_max, "w": lwl, "cur": lp_(xc), "zeros": sum(1 for v in lwl if v == 0.0),
                  "m": _reg_metrics([lp_(x) for x in Xte], yte, ybar)}
    eb0, ew = qml.elastic_net(Xtr, ytr, lam_max, 0.5, max_iter=300)
    ep = lambda x: qml.linear_predictor(x, ew, eb0)  # noqa: E731
    M["enet"] = {"lam": lam_max, "w": ew, "cur": ep(xc), "zeros": sum(1 for v in ew if v == 0.0),
                 "m": _reg_metrics([ep(x) for x in Xte], yte, ybar)}
    # trees
    min_leaf = max(10, n // 20)
    tree = qml.fit_tree(Xtr, ytr, max_depth=3, min_leaf=min_leaf)
    M["tree"] = {"tree": tree, "cur": qml.tree_predict(tree, xc), "split": qml.best_split(Xtr, ytr, min_leaf),
                 "m": _reg_metrics([qml.tree_predict(tree, x) for x in Xte], yte, ybar)}
    forest = qml.random_forest_fit(Xtr, ytr, n_trees=15, max_depth=3, min_leaf=min_leaf, seed=0)
    M["rf"] = {"cur": qml.random_forest_predict(forest, xc),
               "m": _reg_metrics([qml.random_forest_predict(forest, x) for x in Xte], yte, ybar)}
    gb = qml.gradient_boosting_fit(Xtr, ytr, n_estimators=30, learning_rate=0.1, max_depth=1, min_leaf=min_leaf)
    gtr = [qml.gradient_boosting_predict(gb, x) for x in Xtr]
    leaves = [[gb["learning_rate"] * v for v in qml.tree_leaves(t)] for t in gb["trees"]]
    M["gbm"] = {"cur": qml.gradient_boosting_predict(gb, xc),
                "m": _reg_metrics([qml.gradient_boosting_predict(gb, x) for x in Xte], yte, ybar),
                "xgb": qml.xgb_objective(ytr, gtr, leaves, gamma=0.0, lam=1.0),
                "sse": math.fsum((a - b) ** 2 for a, b in zip(ytr, gtr)), "n_trees": len(gb["trees"])}
    M["ybar"] = ybar
    return M


_ML_ALIASES = {"ols": ("linear", "ols", "linear_regression"), "ridge": ("ridge",), "enet": ("elastic_net", "enet",
               "elasticnet"), "lasso": ("lasso",), "tree": ("tree", "regression_tree", "cart"),
               "rf": ("rf", "random_forest", "forest"), "gbm": ("gbm", "gradient_boosting", "boosting", "gbt")}


def _ml_override(p, key):
    """(value, horizon) from ctx['ml'] predictions when the ML engine already produced this model."""
    ml = p.ctx.get("ml") or {}
    if key == "logit":
        d = ml.get("logistic_prob_up") or {}
    else:
        preds = ml.get("predictions") or {}
        d = None
        for a in _ML_ALIASES.get(key, ()):
            if a in preds:
                d = preds[a]
                break
        d = d or {}
    if not isinstance(d, dict) or not d:
        return None
    for h in ("1M", "3M", "1W"):
        if _f(d.get(h)) is not None:
            return _f(d[h]), h
    for h, v in d.items():
        if _f(v) is not None:
            return _f(v), h
    return None


def _ml_res(p, eid, key, value, fmt, variables, interp, note_extra=""):
    ov = _ml_override(p, key) if key else None
    note = "Weekly point-in-time rows; target = next-month (21-session) log return; trained on the first 80%, " \
           "evaluated on the last 20% chronologically (purged)."
    if ov is not None:
        value = ov[0]
        variables = dict(variables or {}, source="ML engine", horizon=ov[1])
        note = "Value from the walk-forward ML engine (%s horizon)." % ov[1]
    return p.res(eid, value, fmt, variables, interp=interp if ov is None else None,
                 note=note + (" " + note_extra if note_extra else ""))


def _named(names, w):
    return {n: _sig(v) for n, v in zip(names, w)}


@_eq("63", "64", "65", "66", "67", "68", "69", "70", "71", "72", "73", "74", "75", "76", "77", "78", "79", "80")
def _e_ml(p, eid):
    M = p.ml_models()
    d = M["data"]
    nm = d["names"]
    src = "Features: %s (%s)." % (", ".join(nm), d["source"])
    if eid == "63":
        o = M["ols"]
        imp = ((p.ctx.get("ml") or {}).get("importance") or None)
        v = dict(theta=_named(nm, o["w"]), intercept=_sig(o["b0"]), oos_hit=_sig(o["m"]["hit"]))
        if imp:
            v["importance"] = imp if isinstance(imp, dict) else str(imp)
        return _ml_res(p, eid, "ols", o["cur"], "ret", v,
                       "The linear model f_θ(x) forecasts %s over the next month (out-of-sample direction hit rate %s)." % (
                           _fmt(o["cur"], "ret"), _fmt(o["m"]["hit"], "pct")), src)
    if eid == "64":
        o = M["ols"]
        base = _mean([(y - M["ybar"]) ** 2 for y in d["yte"]])
        v = o["m"]["mse"]
        return _ml_res(p, eid, None, v, "sci", _vars(test_mse=v, naive_mse=base, n_test=len(d["yte"])),
                       "Out-of-sample empirical risk (MSE) is %s vs %s for always predicting the training mean — %s." % (
                           _fmt(v, "sci"), _fmt(base, "sci"), "the model adds value" if v < base else
                           "the model does not beat the naive forecast"), src)
    if eid == "65":
        o = M["ridge"]
        return _ml_res(p, eid, None, o["obj"], "sci", _vars(lam=o["lam"] / len(d["ytr"]), penalty="l2"),
                       "The ridge objective (training MSE + λ‖θ‖²) is %s." % _fmt(o["obj"], "sci"), src)
    if eid in ("66", "67", "68"):
        o = M["logit"]
        if eid == "66":
            return _ml_res(p, eid, None, o["z"], "num", dict(w=_named(nm, o["w"]), b=_sig(o["b"])),
                           "The logistic score wᵀx + b is %s (positive leans up)." % _fmt(o["z"], "num"), src)
        if eid == "67":
            v = qml.sigmoid(o["z"])
            return _ml_res(p, eid, None, v, "pct", _vars(z=o["z"]),
                           "σ(%s) = %s: the score mapped to a probability." % (_fmt(o["z"], "num"), _fmt(v, "pct")), src)
        r = _ml_res(p, eid, "logit", o["p"], "pct", _vars(oos_accuracy=o["acc"], base_rate=o["base_rate"]),
                    "The logistic model gives a %s probability that next month's return is positive "
                    "(out-of-sample accuracy %s vs base rate %s)." % (_fmt(o["p"], "pct"), _fmt(o["acc"], "pct"),
                                                                      _fmt(o["base_rate"], "pct")), src)
        pu = r["value"]
        if pu is not None:
            edge = o["acc"] - max(o["base_rate"], 1 - o["base_rate"])
            r["signal"] = _label(_clamp((pu - 0.5) * 4.0, -0.45, 0.45) if edge > 0 else 0.0)
        return r
    if eid == "69":
        o = M["logit"]
        return _ml_res(p, eid, None, o["bce"], "num", _vars(test_bce=o["bce"], base_rate_bce=o["bce_base"]),
                       "Out-of-sample cross-entropy is %s vs %s for the base rate — %s." % (
                           _fmt(o["bce"], "num"), _fmt(o["bce_base"], "num"),
                           "better than guessing" if o["bce"] < o["bce_base"] else "no better than the base rate"), src)
    if eid in ("70", "71"):
        o = M["softmax"]
        pr = o["cur"]
        if eid == "70":
            return _ml_res(p, eid, None, pr[2], "pct", _vars(p_down=pr[0], p_flat=pr[1], p_up=pr[2],
                                                             thresholds=list(o["thresholds"])),
                           "Softmax class probabilities for next month: down %s, flat %s, up %s." % (
                               _fmt(pr[0], "pct"), _fmt(pr[1], "pct"), _fmt(pr[2], "pct")),
                           src + " Classes are training terciles; one-vs-rest logistic scores.")
        return _ml_res(p, eid, None, o["ce"], "num", _vars(test_ce=o["ce"], uniform=math.log(3)),
                       "Out-of-sample 3-class cross-entropy is %s vs ln 3 = %s for a uniform guess." % (
                           _fmt(o["ce"], "num"), _fmt(math.log(3), "num")), src)
    if eid == "72":
        o = M["logit"]
        return _ml_res(p, eid, None, o["hinge"], "num", _vars(labels="±1 (up/down)"),
                       "Mean out-of-sample hinge loss of the logistic score is %s (1.0 = no margin)." % _fmt(
                           o["hinge"], "num"), src)
    if eid == "73":
        o = M["ridge"]
        return _ml_res(p, eid, "ridge", o["cur"], "ret", dict(lam=_sig(o["lam"]), w=_named(nm, o["w"]),
                                                              oos_r2=_sig(o["m"]["r2_oos"])),
                       "Ridge forecasts %s over the next month (out-of-sample R² %s)." % (
                           _fmt(o["cur"], "ret"), _fmt(o["m"]["r2_oos"], "pct")), src)
    if eid == "74":
        o = M["lasso"]
        return _ml_res(p, eid, None, o["zeros"], "int", dict(lam=_sig(o["lam"]), w=_named(nm, o["w"]),
                                                             forecast=_sig(o["cur"])),
                       "LASSO sets %d of %d coefficients to zero; its forecast is %s." % (
                           o["zeros"], len(nm), _fmt(o["cur"], "ret")), src)
    if eid == "75":
        o = M["enet"]
        return _ml_res(p, eid, "enet", o["cur"], "ret", dict(lam=_sig(o["lam"]), l1_ratio=0.5, zeros=o["zeros"],
                                                             w=_named(nm, o["w"])),
                       "Elastic net forecasts %s over the next month (%d zero coefficients)." % (
                           _fmt(o["cur"], "ret"), o["zeros"]), src)
    if eid == "76":
        o = M["tree"]
        return _ml_res(p, eid, "tree", o["cur"], "ret", _vars(depth=3, oos_hit=o["m"]["hit"]),
                       "The regression tree's leaf for today's conditions predicts %s." % _fmt(o["cur"], "ret"), src)
    if eid == "77":
        o = M["tree"]
        sp = o["split"]
        if sp is None:
            raise _Insufficient("no valid split")
        j, thr, sse = sp
        return _ml_res(p, eid, None, thr, "num", dict(feature=nm[j], threshold_z=_sig(thr), sse=_sig(sse)),
                       "The best first split is %s ≤ %s (standardised units)." % (nm[j], _fmt(thr, "num")), src)
    if eid == "78":
        o = M["rf"]
        return _ml_res(p, eid, "rf", o["cur"], "ret", _vars(trees=15, depth=3, oos_hit=o["m"]["hit"]),
                       "The random forest forecasts %s (out-of-sample direction hit %s)." % (
                           _fmt(o["cur"], "ret"), _fmt(o["m"]["hit"], "pct")), src)
    o = M["gbm"]
    if eid == "79":
        return _ml_res(p, eid, "gbm", o["cur"], "ret", _vars(trees=o["n_trees"], learning_rate=0.1,
                                                             oos_hit=o["m"]["hit"]),
                       "Gradient boosting forecasts %s (out-of-sample direction hit %s)." % (
                           _fmt(o["cur"], "ret"), _fmt(o["m"]["hit"], "pct")), src)
    return _ml_res(p, eid, None, o["xgb"], "sci", _vars(sse=o["sse"], gamma=0.0, lam=1.0, trees=o["n_trees"]),
                   "The boosted model's XGBoost objective (loss + leaf penalty) is %s." % _fmt(o["xgb"], "sci"), src)


def _universe(p):
    uni = p.ctx.get("universe_returns") or {}
    mk = p.mret[-252:]
    feats = {}
    series = {}
    for aid, rets in uni.items():
        r = [_f(x) for x in (rets or [])][-252:]
        vals = [x for x in r if x is not None]
        if len(vals) < 150:
            continue
        m = mk[-len(r):] if len(mk) >= len(r) else [None] * (len(r) - len(mk)) + mk
        pr = [(a, b) for a, b in zip(r, m) if a is not None and b is not None]
        if len(pr) < 100:
            continue
        corr = qs.correlation([a for a, _ in pr], [b for _, b in pr])
        feats[aid] = [_mean(vals) * ANN, _sd(vals) * math.sqrt(ANN), corr]
        series[aid] = r
    me = p.aid or "ASSET"
    if me not in feats:
        x = p.lr[-252:]
        a, m = p.pairs(252)
        feats[me] = [_mean(x) * ANN, _sd(x) * math.sqrt(ANN), qs.correlation(a, m)]
        series[me] = x
    return {"feats": feats, "series": series, "me": me}


@_eq("81")
def _e_kmeans(p, eid):
    u = p.universe()
    ids = sorted(u["feats"])
    if len(ids) < 6:
        raise _Insufficient("needs at least 6 assets in the universe")
    X = [u["feats"][i] for i in ids]
    mu = [_mean([r[j] for r in X]) for j in range(3)]
    sd = [(_sd([r[j] for r in X]) or 1.0) for j in range(3)]
    Z = [[(r[j] - mu[j]) / sd[j] for j in range(3)] for r in X]
    k = min(4, max(2, len(ids) // 5))
    cents, labels, inertia = qml.kmeans(Z, k, seed=0)
    me = ids.index(u["me"])
    c = labels[me]
    peers = [ids[i] for i in range(len(ids)) if labels[i] == c and i != me]
    cen = [cents[c][j] * sd[j] + mu[j] for j in range(3)]
    desc = "%s return, %s volatility, %s market correlation" % (
        "high" if cents[c][0] > 0.5 else "low" if cents[c][0] < -0.5 else "average",
        "high" if cents[c][1] > 0.5 else "low" if cents[c][1] < -0.5 else "average",
        "high" if cents[c][2] > 0.5 else "low" if cents[c][2] < -0.5 else "average")
    return p.res(eid, c + 1, "int", {"k": k, "cluster_size": len(peers) + 1, "peers": peers[:12],
                                     "centroid": {"ann_return": _sig(cen[0]), "ann_vol": _sig(cen[1]),
                                                  "corr_spy": _sig(cen[2])}, "inertia": _sig(inertia),
                                     "universe": len(ids)},
                 display="Cluster %d of %d" % (c + 1, k),
                 interp="Among %d assets clustered by 1-year return, volatility and SPY correlation, this asset sits in "
                        "a group of %d (%s)%s." % (len(ids), len(peers) + 1, desc,
                                                   (" with " + ", ".join(peers[:5])) if peers else ""),
                 note="k-means++ (seed 0) on standardised features; cluster numbers are arbitrary labels.")


@_eq("82", "83")
def _e_pca(p, eid):
    u = p.universe()
    ids = sorted(u["series"])
    me = u["me"]
    n = min(len(v) for v in u["series"].values())
    if len(ids) < 4 or n < 100:
        raise _Insufficient("needs at least 4 assets with 100 common days")
    others = [i for i in ids if i != me]
    if len(others) > 29:
        step = len(others) / 29.0
        others = [others[int(i * step)] for i in range(29)]
    use = [me] + others
    cols = []
    for aid in use:
        r = u["series"][aid][-n:]
        vals = [x for x in r if x is not None]
        m = _mean(vals)
        s = _sd(vals) or 1.0
        cols.append([((x if x is not None else m) - m) / s for x in r])
    X = [[c[t] for c in cols] for t in range(n)]
    cov = qml.covariance_matrix(X)
    vals, vecs, ratios = qml.pca(cov)
    v1 = vecs[0]
    if sum(v1) < 0:
        v1 = [-x for x in v1]
    score = qml.pca_project([X[-1]], [v1], [0.0] * len(use))[0][0]
    if eid == "82":
        return p.res(eid, ratios[0], "pct", _vars(eigenvalue_1=vals[0], pc2_share=ratios[1] if len(ratios) > 1 else None,
                                                  assets=len(use), days=n),
                     interp="The first principal component explains %s of the co-movement of %d assets — %s." % (
                         _fmt(ratios[0], "pct"), len(use), "one common factor dominates" if ratios[0] > 0.4 else
                         "returns are fairly diversified"),
                     note="PCA of the correlation matrix of the last %d daily returns (up to 30 assets)." % n)
    load = v1[0]
    avg = _mean([abs(x) for x in v1])
    return p.res(eid, load, "ratio", _vars(average_abs_loading=avg, latest_pc1_score=score, assets=len(use)),
                 interp="This asset's PC1 loading is %s vs an average of %s: it is %s the common factor." % (
                     _fmt(load, "ratio"), _fmt(avg, "ratio"), "more exposed to" if abs(load) > avg else "less exposed to"),
                 note="Projection z = Vᵀ(x − μ) on standardised returns; latest PC1 score in variables.")


# ---------------------------------------------------------------------------- 84-100 neural network

def _nn_fit(p):
    d = p.ml_data()
    Xtr, ytr = d["Xtr"][-160:], d["ytr"][-160:]
    yb = [1.0 if v > 0 else 0.0 for v in ytr]
    lr, hidden, epochs = 0.02, 4, 80
    model = qnn.train_mlp(Xtr, yb, hidden=hidden, task="binary", epochs=epochs, lr=lr, act="tanh", seed=0)
    pte = [qnn.mlp_predict(model, x) for x in d["Xte"]]
    acc = _mean([1.0 if (q > 0.5) == (y > 0) else 0.0 for q, y in zip(pte, d["yte"])])
    xc = d["xcur"]
    W1, b1, W2, b2 = model["W1"], model["b1"], model["W2"], model["b2"]
    z1 = qnn.preactivation(W1, xc, b1)
    a1 = qnn.layer_activation(z1, "tanh")
    z2 = qnn.deep_layer([W2], a1, [b2])
    prob = qnn.layer_activation(z2, "sigmoid")[0]
    # full-batch gradient at the final parameters
    k = len(xc)
    gW1 = [[0.0] * k for _ in range(hidden)]
    gb1 = [0.0] * hidden
    gW2 = [0.0] * hidden
    gb2 = 0.0
    per_row = []
    for x, t in zip(Xtr, yb):
        zz1 = qnn.preactivation(W1, x, b1)
        aa1 = qnn.activation(zz1, "tanh")
        out = sum(w * a for w, a in zip(W2, aa1)) + b2
        pr = qml.sigmoid(out)
        d_out = qnn.output_error(qnn.softmax_ce_grad([pr], [t]), [out], "linear")
        d1 = qnn.hidden_delta([W2], d_out, zz1, "tanh")
        gw = qnn.weight_gradient(d1, x)
        gb = qnn.bias_gradient(d1)
        for j in range(hidden):
            gW2[j] += d_out[0] * aa1[j]
            gb1[j] += gb[j]
            for i in range(k):
                gW1[j][i] += gw[j][i]
        gb2 += d_out[0]
        per_row.append([g for row in gw for g in row] + gb + [d_out[0] * a for a in aa1] + d_out)
    n = len(Xtr)
    grad = [g / n for row in gW1 for g in row] + [g / n for g in gb1] + [g / n for g in gW2] + [gb2 / n]
    theta = [w for row in W1 for w in row] + list(b1) + list(W2) + [b2]
    norm = lambda v: math.sqrt(sum(x * x for x in v))  # noqa: E731
    new_gd = qnn.gd_step(theta, grad, lr)
    rng = random.Random(0)
    batch = [per_row[rng.randrange(n)] for _ in range(min(32, n))]
    new_sgd = qnn.sgd_step(theta, batch, lr)
    new_mom, vel = qnn.momentum_step(theta, [0.0] * len(theta), grad, lr, 0.9)
    m1 = qnn.adam_first_moment([0.0] * len(theta), grad)
    v1 = qnn.adam_second_moment([0.0] * len(theta), grad)
    new_adam = qnn.adam_update(theta, m1, v1, 1, lr)
    # chain rule for one weight (last training row, W1[0][0])
    x, t = Xtr[-1], yb[-1]
    zz1 = qnn.preactivation(W1, x, b1)
    aa1 = qnn.activation(zz1, "tanh")
    out = sum(w * a for w, a in zip(W2, aa1)) + b2
    pr = qml.sigmoid(out)
    chain = qnn.chain_rule(pr - t, W2[0], qnn.activation_derivative(zz1[0], "tanh"), x[0])
    d_last = qnn.output_error(qnn.softmax_ce_grad([pr], [t]), [out], "linear")
    delta_hidden = qnn.hidden_delta([W2], d_last, zz1, "tanh")
    return {"model": model, "acc": acc, "prob": prob, "z1": z1, "a1": a1, "z2": z2[0], "final_loss":
            model["loss_history"][-1], "first_loss": model["loss_history"][0], "gW1": norm([g / n for r in gW1 for g in r]),
            "gb1": norm([g / n for g in gb1]), "grad_norm": norm(grad),
            "gd_step": norm([a - b for a, b in zip(new_gd, theta)]),
            "sgd_step": norm([a - b for a, b in zip(new_sgd, theta)]),
            "mom_step": norm([a - b for a, b in zip(new_mom, theta)]), "m_norm": norm(m1), "v_norm": norm(v1),
            "adam_step": norm([a - b for a, b in zip(new_adam, theta)]), "chain": chain,
            "ce_grad": pr - t, "out_err": d_last[0], "hidden_delta": delta_hidden, "lr": lr, "hidden": hidden,
            "n_train": n, "n_params": len(theta), "epochs": epochs}


@_eq(*[str(i) for i in range(84, 101)])
def _e_nn(p, eid):
    N = p.nn_model()
    d = p.ml_data()
    note = ("1-hidden-layer tanh network (%d units, %d parameters), full-batch Adam for %d epochs on the last %d "
            "training rows; target = next month up / down." % (N["hidden"], N["n_params"], N["epochs"], N["n_train"]))
    nm = d["names"]
    i = int(eid)
    if i == 84:
        v = math.sqrt(sum(z * z for z in N["z1"]))
        r = p.res(eid, v, "num", _vars(z=N["z1"], inputs=len(nm)), experimental=True, note=note,
                  interp="Today's inputs produce hidden pre-activations with norm %s." % _fmt(v, "num"))
    elif i == 85:
        v = _mean(N["a1"])
        r = p.res(eid, v, "num", _vars(a=N["a1"], activation="tanh"), experimental=True, note=note,
                  interp="The mean hidden tanh activation for today's inputs is %s (range −1 to 1)." % _fmt(v, "num"))
    elif i in (86, 88):
        v = N["prob"]
        r = p.res(eid, v, "pct", _vars(oos_accuracy=N["acc"], final_loss=N["final_loss"]), experimental=True,
                  note=note, interp="The network gives a %s probability of a positive next month (out-of-sample "
                                    "accuracy %s)." % (_fmt(v, "pct"), _fmt(N["acc"], "pct")))
    elif i == 87:
        v = N["z2"]
        r = p.res(eid, v, "num", _vars(output_logit=v), experimental=True, note=note,
                  interp="The output layer's pre-activation (logit) is %s." % _fmt(v, "num"))
    elif i == 89:
        v = N["chain"]
        r = p.res(eid, v, "sci", _vars(link="dL/dŷ · W2[0] · tanh'(z) · x[0]"), experimental=True, note=note,
                  interp="By the chain rule the loss gradient for the first input weight on the last training row "
                         "is %s." % _fmt(v, "sci"))
    elif i == 90:
        v = N["ce_grad"]
        r = p.res(eid, v, "num", {}, experimental=True, note=note,
                  interp="Softmax / sigmoid cross-entropy gradient ŷ − y on the last training row is %s." % _fmt(v, "num"))
    elif i == 91:
        v = N["out_err"]
        r = p.res(eid, v, "num", {}, experimental=True, note=note,
                  interp="The output-layer error δ on the last training row is %s." % _fmt(v, "num"))
    elif i == 92:
        v = math.sqrt(sum(x * x for x in N["hidden_delta"]))
        r = p.res(eid, v, "sci", _vars(delta=N["hidden_delta"]), experimental=True, note=note,
                  interp="Back-propagated hidden-layer errors have norm %s." % _fmt(v, "sci"))
    elif i == 93:
        v = N["gW1"]
        r = p.res(eid, v, "sci", _vars(final_loss=N["final_loss"], first_loss=N["first_loss"]), experimental=True,
                  note=note, interp="At the end of training the first-layer weight gradient has norm %s (loss %s, "
                                    "from %s)." % (_fmt(v, "sci"), _fmt(N["final_loss"], "num"),
                                                   _fmt(N["first_loss"], "num")))
    elif i == 94:
        v = N["gb1"]
        r = p.res(eid, v, "sci", {}, experimental=True, note=note,
                  interp="The first-layer bias gradient norm is %s." % _fmt(v, "sci"))
    elif i == 95:
        v = N["gd_step"]
        r = p.res(eid, v, "sci", _vars(lr=N["lr"], grad_norm=N["grad_norm"]), experimental=True, note=note,
                  interp="One more gradient-descent step would move the parameters by %s." % _fmt(v, "sci"))
    elif i == 96:
        v = N["sgd_step"]
        r = p.res(eid, v, "sci", _vars(batch=32, lr=N["lr"]), experimental=True, note=note,
                  interp="A 32-row stochastic step would move the parameters by %s." % _fmt(v, "sci"))
    elif i == 97:
        v = N["mom_step"]
        r = p.res(eid, v, "sci", _vars(beta=0.9, lr=N["lr"]), experimental=True, note=note,
                  interp="A momentum step from rest moves the parameters by %s." % _fmt(v, "sci"))
    elif i == 98:
        v = N["m_norm"]
        r = p.res(eid, v, "sci", _vars(beta1=0.9), experimental=True, note=note,
                  interp="Adam's first moment after one update has norm %s." % _fmt(v, "sci"))
    elif i == 99:
        v = N["v_norm"]
        r = p.res(eid, v, "sci", _vars(beta2=0.999), experimental=True, note=note,
                  interp="Adam's second moment after one update has norm %s." % _fmt(v, "sci"))
    else:
        v = N["adam_step"]
        r = p.res(eid, v, "sci", _vars(lr=N["lr"], t=1), experimental=True, note=note,
                  interp="A bias-corrected Adam step moves the parameters by %s (≈ η per coordinate)." % _fmt(v, "sci"))
    return r


# ---------------------------------------------------------------------------- 101-110 portfolio

def _treasury_proxy(p, D=8.5, C=90.0):
    y = p.macro_seg("y10")
    out = []
    for i in range(1, p.L):
        a, b = y[i - 1], y[i]
        if a is None or b is None:
            out.append(None)
        else:
            dy = b - a
            out.append(a / ANN - D * dy + 0.5 * C * dy * dy)
    return out


def _portfolio_inputs(p):
    port = p.ctx.get("portfolio")
    note = None
    ids, rets, w = None, None, None
    if port and isinstance(port, dict) and port.get("weights") and port.get("returns"):
        wts = {k: _f(v) for k, v in port["weights"].items() if _f(v) is not None}
        rr = {k: [_f(x) for x in v] for k, v in (port.get("returns") or {}).items() if k in wts and v}
        if len(rr) >= 2 and p.aid in rr:
            n = min(len(v) for v in rr.values())
            keys = sorted(rr)
            rows = [[rr[k][len(rr[k]) - n + t] for k in keys] for t in range(n)]
            rows = [r for r in rows if all(x is not None for x in r)][-252:]
            if len(rows) >= MIN_OBS:
                ids, rets, w = keys, rows, [wts[k] for k in keys]
                note = "Portfolio of %d holdings, last %d common sessions." % (len(keys), len(rows))
        if ids is None:
            note = "Asset not held (or too little common history) in the portfolio: shown for a 50/50 proxy."
    if ids is None:
        second, other = "SPY", p.mret
        if p.aid.upper() == "SPY":
            second, other = "10Y Treasury (proxy)", _treasury_proxy(p)
        rows = [[a, b] for a, b in zip(p.lr, other) if b is not None][-252:]
        if len(rows) < MIN_OBS:
            raise _Insufficient("not enough common history for the portfolio proxy")
        ids, rets, w = [p.aid or "ASSET", second], rows, [0.5, 0.5]
        note = note or "No portfolio: 50/50 proxy of this asset and %s." % second
    k = len(ids)
    n = len(rets)
    mu = [_mean([r[j] for r in rets]) * ANN for j in range(k)]
    cov = [[qs.covariance([r[i] for r in rets], [r[j] for r in rets]) * ANN for j in range(k)] for i in range(k)]
    me = ids.index(p.aid) if p.aid in ids else 0
    return {"ids": ids, "rets": rets, "w": w, "mu": mu, "cov": cov, "me": me, "note": note, "n": n}


def _wd(ids, w):
    return {i: _sig(x) for i, x in zip(ids, w)}


@_eq("101", "102", "103", "104", "105")
def _e_port(p, eid):
    q = p.port()
    ids, mu, cov, w, me = q["ids"], q["mu"], q["cov"], q["w"], q["me"]
    who = ids[me]
    if eid == "101":
        lam = 3.0
        wm = qp.mean_variance_weights_budget(mu, cov, lam)
        return p.res(eid, wm[me], "pct", {"weights": _wd(ids, wm), "risk_aversion": lam, "mu": _wd(ids, mu)},
                     interp="With risk aversion 3 and 1-year estimates, the mean-variance optimum holds %s in %s." % (
                         _fmt(wm[me], "pct"), who),
                     note=q["note"] + " Unconstrained (shorting allowed); 1-year means are very noisy inputs.")
    if eid == "102":
        s = sum(w)
        ok = qp.is_fully_invested(w, 1e-6)
        return p.res(eid, s, "ratio", {"weights": _wd(ids, w)}, display="%s (Σw = %.3f)" % ("Yes" if ok else "No", s),
                     interp="The weights sum to %.3f: the portfolio is %sfully invested." % (s, "" if ok else "not "),
                     note=q["note"])
    if eid == "103":
        target = sum(a * b for a, b in zip(w, mu))
        wf = qp.frontier_weights(mu, cov, target)
        return p.res(eid, wf[me], "pct", {"weights": _wd(ids, wf), "target_return": _sig(target)},
                     interp="The minimum-variance mix earning the current portfolio's expected %s holds %s in %s." % (
                         _fmt(target, "pct"), _fmt(wf[me], "pct"), who), note=q["note"])
    if eid == "104":
        wg = qp.gmv_weights(cov)
        vol = math.sqrt(qp.portfolio_variance(wg, cov))
        return p.res(eid, wg[me], "pct", {"weights": _wd(ids, wg), "gmv_vol": _sig(vol)},
                     interp="The global minimum-variance portfolio holds %s in %s (volatility %s)." % (
                         _fmt(wg[me], "pct"), who, _fmt(vol, "pct")), note=q["note"])
    rc = qp.risk_contributions(w, cov)
    sig = sum(rc)
    shares = [x / sig for x in rc] if sig else rc
    return p.res(eid, shares[me], "pct", {"risk_share": _wd(ids, shares), "portfolio_vol": _sig(sig),
                                          "weights": _wd(ids, w)},
                 interp="%s is %s of the weight but %s of the portfolio's risk." % (
                     who, _fmt(w[me], "pct"), _fmt(shares[me], "pct")), note=q["note"])


@_eq("106", "107", "108")
def _e_capm(p, eid):
    a, m = p.pairs(252)
    beta = qp.market_beta(a, m)
    if eid == "106":
        var = {"window": len(a)}
        q = None
        try:
            q = p.port()
        except Exception:  # noqa: BLE001
            pass
        if q and q["note"].startswith("Portfolio of"):
            pr = [sum(wi * x for wi, x in zip(q["w"], row)) for row in q["rets"]]
            mm = [x for x in p.mret if x is not None][-len(pr):]
            if len(mm) == len(pr):
                var["portfolio_beta"] = _sig(qp.market_beta(pr, mm))
        return p.res(eid, beta, "ratio", var, p.beta_hist(), feature=EQ_FEATURE[eid],
                     interp="Market beta is %s: a 1%% SPY move has come with a %s%% move in the asset." % (
                         _fmt(beta, "ratio"), _fmt(beta, "ratio")))
    msr = [math.exp(x) - 1.0 for x in p.mret if x is not None][-2520:]
    em = _mean(msr) * ANN
    if eid == "107":
        v = qp.capm_expected_return(p.rf, beta, em)
        return p.res(eid, v, "pct", _vars(rf=p.rf, beta=beta, E_market=em, market_years=len(msr) / ANN),
                     interp="CAPM expects %s a year: %s plus %s × the %s market premium." % (
                         _fmt(v, "pct"), _fmt(p.rf, "pct"), _fmt(beta, "ratio"), _fmt(em - p.rf, "pct")),
                     note="E[R_m] = SPY's average arithmetic return over the last %.0f years." % (len(msr) / ANN))
    sa = [math.exp(x) - 1.0 for x in a]
    sm = [math.exp(x) - 1.0 for x in m]
    rp, rm = _mean(sa) * ANN, _mean(sm) * ANN
    v = qp.jensens_alpha(rp, p.rf, beta, rm)
    t = p.ols_mkt()["t"][0]
    return p.res(eid, v, "pct", _vars(R_p=rp, R_m=rm, rf=p.rf, beta=beta, t_alpha=t), feature=EQ_FEATURE[eid],
                 sscore=_tscore(t, 0.4),
                 interp="Jensen's alpha over the last year is %s (t = %s): %s." % (
                     _fmt(v, "pct"), _fmt(t, "num"), "significant outperformance" if t >= 2 else
                     "significant underperformance" if t <= -2 else "not statistically distinguishable from zero"))


@_eq("109", "110")
def _e_var95(p, eid):
    x = p.swin(252)
    n = len(x)
    mu_l, s_l = -_mean(x), _sd(x)
    fn = qp.parametric_var if eid == "109" else qp.expected_shortfall
    v = fn(mu_l, s_l, 0.95)
    sr = p.sr
    c1, c2 = _pref(sr), _pref2(sr)
    vals = []
    for hi in range(n, len(sr) + 1):
        s1, s2 = c1[hi] - c1[hi - n], c2[hi] - c2[hi - n]
        var = max(0.0, (s2 - s1 * s1 / n) / (n - 1))
        vals.append(fn(-s1 / n, math.sqrt(var), 0.95))
    h = _series(p.rd[n - 1:], vals)
    word = "VaR" if eid == "109" else "Expected shortfall"
    return p.res(eid, v, "pct", _vars(mu_loss=mu_l, sigma_loss=s_l, alpha=0.95, window=n), h,
                 regime_from_pct=("High risk", "Low risk"),
                 interp=lambda pc: ("On 1 day in 20 the loss is expected to exceed %s of value" % _fmt(v, "pct")
                                    if eid == "109" else "When a 1-in-20 bad day happens, the average loss is %s" %
                                    _fmt(v, "pct")) + ((" (%s of its history)." % _ordinal(pc)) if pc is not None else "."),
                 note="%s at 95%%, 1 day, normal approximation on the last %d daily returns." % (word, n))


# ---------------------------------------------------------------------------- 111-118 fixed income

def _interp_curve(pts, T):
    ks = sorted(pts)
    if T <= ks[0]:
        return pts[ks[0]]
    if T >= ks[-1]:
        return pts[ks[-1]]
    for a, b in zip(ks, ks[1:]):
        if a <= T <= b:
            return pts[a] + (pts[b] - pts[a]) * (T - a) / (b - a)
    return pts[ks[-1]]


def _maturity_from_duration(D, y):
    """Maturity of a semi-annual par bond whose modified duration is D (closed form)."""
    if y <= 1e-6:
        return D
    x = 1.0 - D * y
    if x <= 0.01:
        return 30.0
    return max(0.25, min(30.0, -math.log(x) / (2.0 * math.log(1.0 + y / 2.0))))


def _fixed_income_inputs(p):
    curve = p.curve()
    applies = p.is_bond
    note = None
    add_spread = False
    if not applies:
        y, M, label = curve.get(10.0), 10.0, "10-year Treasury"
        if y is None:
            y = _interp_curve(curve, 10.0)
        note = "Shown for the 10-year Treasury; these equations apply to bonds and bond funds."
        series = "y10"
    elif p.is_credit and p.macro_last("baa") is not None and p.aclass == "CORP_BOND":
        y = p.macro_last("baa")
        M = _maturity_from_duration(p.duration, y) if p.duration else 20.0
        label, series = "Moody's BAA corporate yield", "baa"
    else:
        cs = p.macro_last("credit_spread") if p.is_credit else None
        add_spread = cs is not None
        spread = cs if add_spread else 0.0
        M = 10.0
        if p.duration:
            for _ in range(3):
                M = _maturity_from_duration(p.duration, _interp_curve(curve, M) + spread)
        y = _interp_curve(curve, M) + spread
        if add_spread:
            label, series = "Treasury curve + BAA spread at %.1f years" % M, None
        else:
            near = min(((0.25, "y3m"), (2.0, "y2"), (5.0, "y5"), (10.0, "y10"), (30.0, "y30")),
                       key=lambda kv: abs(kv[0] - M))
            label = "Treasury curve at %.1f years" % M
            series = near[1] if abs(near[0] - M) <= 1.0 else None
    times, cfs = qfi.bond_cash_flows(100.0, y, M, 2)
    # the same yield 3 months (63 sessions) ago, for the seasoned-bond comparison
    idx = p.N - 1 - 63
    y_old = None
    if idx >= 0:
        if series in ("y10", "baa"):
            y_old = p.macro_at(series, idx)
        else:
            old = p.curve_at(idx)
            y_old = _interp_curve(old, M) if old else None
            if y_old is not None and add_spread:
                cs = p.macro_at("credit_spread", idx)
                y_old = y_old + cs if cs is not None else None
    return {"y": y, "M": M, "times": times, "cfs": cfs, "applies": applies, "note": note, "label": label,
            "series": series, "y_old": y_old, "curve": curve}


def _yield_series(p, fi):
    if fi["series"]:
        v = p.macro_all(fi["series"])
    else:
        return None
    idx = [i for i, x in enumerate(v) if x is not None]
    if len(idx) < 10:
        return None
    return _series([p.dates_all[i] for i in idx], [v[i] for i in idx])


@_eq("111", "112", "113", "114", "115", "116", "117", "118")
def _e_fi(p, eid):
    fi = p.fi()
    y, M, t, c = fi["y"], fi["M"], fi["times"], fi["cfs"]
    base = dict(yield_=y, maturity=M, coupon=y, freq=2)
    ap, note = fi["applies"], fi["note"]
    lab = fi["label"]
    if eid == "111":
        v = qfi.bond_price_cf(c, t, y, 2)
        seasoned = None
        if fi["y_old"] is not None:
            tt, cc = qfi.bond_cash_flows(100.0, fi["y_old"], M, 2)
            seasoned = qfi.bond_price_cf(cc, tt, y, 2)
        return p.res(eid, v, "price", _vars(seasoned_price=seasoned, yield_3m_ago=fi["y_old"], **base),
                     applies=ap, note=note,
                     interp="A %.1f-year par bond at the %s (%s) prices at %s%s." % (
                         M, lab, _fmt(y, "pct"), _fmt(v, "price"),
                         ("; one bought at par 3 months ago (at %s) would now be worth %s" % (
                             _fmt(fi["y_old"], "pct"), _fmt(seasoned, "price"))) if seasoned is not None else ""))
    if eid == "112":
        price = qfi.bond_price_cf(c, t, y, 2)
        v = qfi.ytm(price, c, t, 2)
        h = _yield_series(p, fi)
        feat = "y10" if fi["series"] == "y10" else None
        return p.res(eid, v, "pct", _vars(price=price, **base), h, feature=feat, applies=ap, note=note,
                     interp=lambda pc: "Yield to maturity is %s (%s)%s." % (
                         _fmt(v, "pct"), lab, (", %s of its history" % _ordinal(pc)) if pc is not None else ""))
    if eid == "113":
        v = qfi.macaulay_duration(c, t, y, 2)
        return p.res(eid, v, "num", _vars(**base), applies=ap, note=note,
                     interp="The weighted-average time to the cash flows is %s years." % _fmt(v, "num"))
    if eid == "114":
        v = qfi.modified_duration(c, t, y, 2)
        return p.res(eid, v, "num", _vars(stated_duration=p.duration, **base), applies=ap, note=note,
                     interp="A 1-point rise in yield cuts the price by about %s%%%s." % (
                         _fmt(v, "num"), (" (fund's stated duration %s)" % _fmt(p.duration, "num")) if p.duration else ""))
    if eid == "115":
        v = qfi.convexity(c, t, y, 2)
        md = qfi.modified_duration(c, t, y, 2)
        dp_up = qfi.price_change_approx(100.0, md, v, 0.01)
        return p.res(eid, v, "num", _vars(stated_convexity=_f(p.asset.get("convexity")), dP_plus_100bp=dp_up, **base),
                     applies=ap, note=note,
                     interp="Convexity %s: a +100 bp move changes the price by about %s per 100." % (
                         _fmt(v, "num"), _fmt(dp_up, "price")))
    if eid == "116":
        price = qfi.bond_price_cf(c, t, y, 2)
        v = qfi.dv01(price, qfi.modified_duration(c, t, y, 2))
        return p.res(eid, v, "price", _vars(price=price, **base), applies=ap, note=note,
                     interp="One basis point of yield is worth %s per 100 of face value." % ("%.4f" % v))
    if eid == "117":
        v = qfi.zero_coupon_price(y, M, 100.0)
        return p.res(eid, v, "price", _vars(yield_=y, T=M), applies=ap, note=note,
                     interp="A %.1f-year zero-coupon bond at %s costs %s per 100 of face." % (
                         M, _fmt(y, "pct"), _fmt(v, "price")))
    cur = fi["curve"]
    if 2.0 not in cur or 10.0 not in cur:
        raise _Insufficient("2- and 10-year yields needed")
    v = qfi.forward_rate(cur[2.0], 2.0, cur[10.0], 10.0, continuous=False)
    y2, y10 = p.macro_all("y2"), p.macro_all("y10")
    idx = [i for i in range(p.N) if y2[i] is not None and y10[i] is not None]
    h = _series([p.dates_all[i] for i in idx], [qfi.forward_rate(y2[i], 2.0, y10[i], 10.0, False) for i in idx]) \
        if len(idx) > 10 else None
    return p.res(eid, v, "pct", _vars(y2=cur[2.0], y10=cur[10.0]), h, feature=EQ_FEATURE[eid], applies=True,
                 interp=lambda pc: "The curve implies an 8-year rate of %s starting in 2 years (spot 10-year %s)%s." % (
                     _fmt(v, "pct"), _fmt(cur[10.0], "pct"), (", %s of its history" % _ordinal(pc)) if pc is not None
                     else ""),
                 note="Annual compounding, Treasury 2y and 10y constant-maturity yields.")


# ---------------------------------------------------------------------------- D1-D20 forwards and options

@_eq("D1", "D2", "D3", "D4", "D5", "D6")
def _e_fwd(p, eid):
    o = p.opt() if p.L > MIN_OBS else None
    S = o["S"] if o else p.spot()
    if S is None:
        raise _Insufficient("no price")
    r, T = p.rf, 0.25
    q = _f(p.asset.get("dividend_yield")) or 0.0
    base = dict(S=S, r=r, T=T)
    rn = "r = current 3-month rate %s, treated as continuous." % _fmt(r, "pct")
    if eid == "D1":
        v = qd.forward_price(S, r, T)
        return p.res(eid, v, "price", _vars(**base), interp="The 3-month no-income forward price is %s (carry %s)." % (
            _fmt(v, "price"), _fmt(v / S - 1, "ret")), note=rn)
    if eid == "D2":
        v = qd.forward_price_dividend(S, r, q, T)
        return p.res(eid, v, "price", _vars(q=q, **base),
                     interp="With a %s income yield the 3-month forward is %s." % (_fmt(q, "pct"), _fmt(v, "price")),
                     note=rn + ("" if p.asset.get("dividend_yield") is not None else " Dividend yield unknown: q = 0."))
    if eid == "D3":
        rf_for = _f(p.asset.get("foreign_rate"))
        note = rn
        if p.is_fx:
            if rf_for is None:
                rf_for = r
                note += " Foreign rate unavailable: approximated by the USD rate, so F ≈ S."
        else:
            rf_for = q
            note += " Applies to currency pairs; shown with the asset's income yield in the foreign-rate slot."
        v = qd.fx_forward(S, r, rf_for, T)
        return p.res(eid, v, "price", _vars(r_dom=r, r_for=rf_for, **base), applies=p.is_fx, note=note,
                     interp="Covered interest parity gives a 3-month forward of %s (%s vs spot)." % (
                         _fmt(v, "price"), _fmt(v / S - 1, "ret")))
    if eid == "D4":
        v = qd.commodity_forward(S, r, 0.0, 0.0, T)
        return p.res(eid, v, "price", _vars(storage=0.0, convenience=None, **base), applies=p.is_commodity,
                     note=rn + " Storage cost and convenience yield unknown (set to 0): the carry shown is financing "
                               "only." + ("" if p.is_commodity else " Applies to commodities."),
                     interp="Financing-only cost of carry gives a 3-month forward of %s." % _fmt(v, "price"))
    f0 = qd.forward_price_dividend(S, r, q, T)
    mult = _f(p.asset.get("multiplier")) or 1.0
    ft = f0 * 1.01
    fn = qd.futures_pnl_long if eid == "D5" else qd.futures_pnl_short
    v = fn(1, mult, f0, ft)
    return p.res(eid, v, "price", _vars(F0=f0, Ft=ft, contracts=1, multiplier=mult),
                 interp="A 1%% rise in the futures price makes one %s contract %s %s." % (
                     "long" if eid == "D5" else "short", "gain" if v >= 0 else "lose", _fmt(abs(v), "price")),
                 note="One lot, multiplier %s (price units)." % _fmt(mult, "num"))


@_eq(*["D%d" % i for i in range(7, 21)])
def _e_opt(p, eid):
    o = p.opt()
    S, K, r, q, T, s = o["S"], o["K"], o["r"], o["q"], o["T"], o["sigma"]
    C = qd.bsm_call(S, K, r, s, T, q)
    Pp = qd.bsm_put(S, K, r, s, T, q)
    base = _vars(S=S, K=K, r=r, q=q, T=T, sigma=s)
    note = "No option quotes: 3-month at-the-money option priced with σ = 60-day realised volatility (%s)." % _fmt(s, "pct")
    i = int(eid[1:])
    if i in (7, 8):
        up, dn = S * math.exp(s * math.sqrt(T)), S * math.exp(-s * math.sqrt(T))
        fn = qd.long_call if i == 7 else qd.long_put
        prem = C if i == 7 else Pp
        res = fn(S, K, prem)
        return p.res(eid, res["profit"], "price", dict(base, payoff=_sig(res["payoff"]), premium=_sig(prem),
                                                       profit_up_1sd=_sig(fn(up, K, prem)["profit"]),
                                                       profit_down_1sd=_sig(fn(dn, K, prem)["profit"])), note=note,
                     interp="If the price is unchanged at expiry the %s pays %s and loses its %s premium; a 1σ %s "
                            "move yields %s." % ("call" if i == 7 else "put", _fmt(res["payoff"], "price"),
                                                 _fmt(prem, "price"), "up" if i == 7 else "down",
                                                 _fmt(fn(up if i == 7 else dn, K, prem)["profit"], "price")))
    if i == 9:
        v = qd.parity_gap(C, Pp, S, K, r, T, q)
        return p.res(eid, v, "sci", dict(base, call=_sig(C), put=_sig(Pp)), note=note,
                     interp="Put-call parity holds to %s (zero by construction with model prices)." % _fmt(v, "sci"))
    if i in (10, 11):
        v = C if i == 10 else Pp
        return p.res(eid, v, "price", dict(base, pct_of_spot=_sig(v / S)), note=note,
                     interp="A 3-month at-the-money %s is worth %s (%s of spot)." % (
                         "call" if i == 10 else "put", _fmt(v, "price"), _fmt(v / S, "pct")))
    d1, d2 = qd.d1_d2(S, K, r, s, T, q)
    if i == 12:
        return p.res(eid, d1, "num", dict(base, d2=_sig(d2)), note=note,
                     interp="d₁ = %s and d₂ = %s; N(d₂) = %s is the risk-neutral chance the call ends in the money." % (
                         _fmt(d1, "num"), _fmt(d2, "num"), _fmt(qs.norm_cdf(d2), "pct")))
    if i == 13:
        v = qd.call_delta(S, K, r, s, T, q)
        return p.res(eid, v, "ratio", base, note=note,
                     interp="Call delta %s: the call gains about %s per 1 of price." % (_fmt(v, "ratio"), _fmt(v, "ratio")))
    if i == 14:
        v = qd.put_delta(S, K, r, s, T, q)
        return p.res(eid, v, "ratio", base, note=note,
                     interp="Put delta %s: the put gains about %s per 1 fall in price." % (_fmt(v, "ratio"),
                                                                                         _fmt(-v, "ratio")))
    if i == 15:
        v = qd.gamma(S, K, r, s, T, q)
        return p.res(eid, v, "num", base, note=note,
                     interp="Gamma %s: delta changes by that much per 1 move in price." % _fmt(v, "num"))
    if i == 16:
        v = qd.vega(S, K, r, s, T, q)
        return p.res(eid, v, "price", dict(base, per_vol_point=_sig(v / 100)), note=note,
                     interp="Vega: each volatility point adds %s to either option." % _fmt(v / 100, "price"))
    if i in (17, 18):
        v = (qd.call_rho if i == 17 else qd.put_rho)(S, K, r, s, T, q)
        return p.res(eid, v, "price", dict(base, per_bp=_sig(v / 1e4)), note=note,
                     interp="%s rho: a 1-point rise in rates changes the option by %s." % (
                         "Call" if i == 17 else "Put", _fmt(v / 100, "price")))
    if i == 19:
        v = qd.theta(S, K, r, s, T, q, "call")
        return p.res(eid, v, "price", dict(base, per_day=_sig(v / 365), put_theta=_sig(qd.theta(S, K, r, s, T, q, "put"))),
                     note=note, interp="The call loses about %s of value per calendar day to time decay." % _fmt(
                         abs(v) / 365, "price"))
    v = qd.implied_vol(C, S, K, r, T, q)
    return p.res(eid, v, "pct", dict(base, call_price=_sig(C), round_trip_error=_sig(v - s)), note=note,
                 interp="Inverting the model price recovers σ = %s (input %s): the implied-volatility solver round-trips "
                        "exactly." % (_fmt(v, "pct"), _fmt(s, "pct")))


# ---------------------------------------------------------------------------- S1-S9 swaps

def _swap_curve(pts, shift=0.0):
    times = [0.5 * (i + 1) for i in range(10)]
    acc = [0.5] * 10
    dfs = [math.exp(-(_interp_curve(pts, t) + shift) * t) for t in times]
    return times, acc, dfs, qsw.forward_rates_from_dfs(dfs, acc)


def _swap_inputs(p):
    pts = {k: v for k, v in p.curve().items() if k <= 10.0}
    if len(pts) < 2:
        raise _Insufficient("needs at least two Treasury curve points")
    times, acc, dfs, L = _swap_curve(pts)
    K = qsw.par_swap_rate(L, acc, dfs)
    t2, a2, d2, L2 = _swap_curve(pts, 0.001)
    return {"pts": pts, "acc": acc, "dfs": dfs, "L": L, "K": K, "dfs_up": d2, "L_up": L2}


@_eq("S1", "S2", "S3", "S4", "S5")
def _e_swap(p, eid):
    w = p.swap()
    N, K, acc, dfs, L = NOTIONAL, w["K"], w["acc"], w["dfs"], w["L"]
    ap = p.is_bond
    note = "5-year swap, semi-annual, curve = Treasury yields interpolated (no swap-spread data)." + \
        ("" if ap else " A rates instrument, shown for context.")
    fixed = qsw.fixed_leg_pv(N, K, acc, dfs)
    flt = qsw.floating_leg_pv(N, L, acc, dfs)
    if eid == "S1":
        v = qsw.swap_pv(flt, fixed)
        return p.res(eid, v, "money", _vars(pv_receive_float=flt, pv_pay_fixed=fixed), applies=ap, note=note,
                     interp="At the par rate the swap is worth %s to either side." % _fmt(v, "money"))
    if eid == "S2":
        return p.res(eid, fixed, "money", _vars(notional=N, K=K), applies=ap, note=note,
                     interp="The fixed leg of a $10mm 5-year swap at %s is worth %s today." % (
                         _fmt(K, "pct"), _fmt(fixed, "money")))
    if eid == "S3":
        return p.res(eid, flt, "money", _vars(notional=N, forwards=L), applies=ap, note=note,
                     interp="The floating leg, projected off the curve's forwards, is worth %s." % _fmt(flt, "money"))
    if eid == "S4":
        v0 = qsw.irs_pv(N, L, K, acc, dfs)
        v = qsw.irs_pv(N, w["L_up"], K, acc, w["dfs_up"])
        return p.res(eid, v, "money", _vars(pv_at_par=v0, shift_bp=10, notional=N), applies=ap, note=note,
                     interp="After a +10 bp parallel shift the pay-fixed swap gains %s (≈ %s per bp)." % (
                         _fmt(v, "money"), _fmt(v / 10, "money")))
    y2, y5, y10, y3 = (p.macro_all(n) for n in ("y2", "y5", "y10", "y3m"))
    idx = [i for i in range(p.N) if y2[i] is not None and y10[i] is not None]

    def par_at(i):
        pts = {k: v for k, v in ((0.25, y3[i]), (2.0, y2[i]), (5.0, y5[i]), (10.0, y10[i])) if v is not None}
        _, a, d, l_ = _swap_curve(pts)
        return qsw.par_swap_rate(l_, a, d)
    sel = [idx[j - 1] for j in _ends(1, len(idx), 160)] if len(idx) > 10 else []
    h = {"dates": [p.dates_all[i] for i in sel], "values": [_try(par_at, i) for i in sel],
         "prev": _try(par_at, idx[-1 - DIRECTION_LAG]) if len(idx) > DIRECTION_LAG else None} if sel else None
    return p.res(eid, K, "pct", _vars(curve={str(k): v for k, v in w["pts"].items()}), h, applies=ap, note=note,
                 interp=lambda pc: "The 5-year par swap rate implied by the Treasury curve is %s%s." % (
                     _fmt(K, "pct"), (", %s of its history" % _ordinal(pc)) if pc is not None else ""))


@_eq("S6", "S7", "S8", "S9")
def _e_trs(p, eid):
    p.need(63)
    s0, sT = p.P[-64], p.P[-1]
    dt = 63.0 / ANN
    ref = p.macro_at("y3m", p.e - 64) if p.e - 64 >= 0 else None
    ref = ref if ref is not None else p.rf
    spread = 0.005
    tr = qsw.trs_total_return(s0, sT, 0.0)
    note = "TRS on this asset over the last 63 sessions, $10mm notional, financing at the 3-month rate at the start " \
           "(%s) + 50 bp; adjusted prices include dividends." % _fmt(ref, "pct")
    if eid == "S6":
        c = p.cum_lr()
        vals = [math.exp(c[hi] - c[hi - 63]) - 1.0 for hi in range(63, len(p.lr) + 1)]
        return p.res(eid, tr, "ret", _vars(S0=s0, ST=sT), _series(p.rd[62:], vals), note=note,
                     interp=lambda pc: "The asset's 3-month total return is %s%s." % (
                         _fmt(tr, "ret"), (" (%s of its history)" % _ordinal(pc)) if pc is not None else ""))
    if eid == "S7":
        v = qsw.trs_equity_leg(NOTIONAL, tr)
        return p.res(eid, v, "money", _vars(total_return=tr), note=note,
                     interp="The total-return receiver's equity leg on $10mm is %s." % _fmt(v, "money"))
    if eid == "S8":
        v = qsw.trs_financing_leg(NOTIONAL, ref, spread, dt)
        return p.res(eid, v, "money", _vars(ref_rate=ref, spread=spread, dt=dt), note=note,
                     interp="The financing leg costs %s for the quarter." % _fmt(v, "money"))
    v = qsw.trs_net_cash_flow(NOTIONAL, s0, sT, 0.0, ref, spread, dt)
    return p.res(eid, v, "money", _vars(total_return=tr, ref_rate=ref, spread=spread), note=note,
                 interp="Net of financing the receiver %s %s over the quarter." % (
                     "made" if v >= 0 else "lost", _fmt(abs(v), "money")))


# ---------------------------------------------------------------------------- C1-C9 credit

@_eq(*["C%d" % i for i in range(1, 10)])
def _e_credit(p, eid):
    c = p.cred()
    s, lam, R = c["s"], c["lam"], RECOVERY
    ap = p.is_credit
    note = "Spread = %s; recovery 40%%; flat hazard λ = s / (1 − R)." % c["how"]
    if not ap:
        note += " Computed on the Moody's BAA corporate index; applies to corporate bonds and credit funds."
    lgd = qc.loss_given_default(R)
    pd1, pd5 = qc.default_probability(lam, 1.0), qc.default_probability(lam, 5.0)
    hist = p.credit_hist()

    def spread_hist(fn):
        if not hist:
            return None
        return _series(hist["dates"], [fn(x) for x in hist["values"]])
    if eid == "C1":
        return p.res(eid, lgd, "pct", _vars(recovery=R), applies=ap, note=note,
                     interp="With 40%% recovery a default loses %s of exposure." % _fmt(lgd, "pct"))
    if eid == "C2":
        v = qc.expected_loss(pd1, lgd, NOTIONAL)
        return p.res(eid, v, "money", _vars(pd_1y=pd1, lgd=lgd, ead=NOTIONAL), applies=ap, note=note,
                     interp="Expected 1-year credit loss on $10mm is %s." % _fmt(v, "money"))
    if eid == "C3":
        v = qc.survival_probability(lam, 5.0)
        return p.res(eid, v, "pct", _vars(hazard=lam, survival_1y=qc.survival_probability(lam, 1.0)), applies=ap,
                     note=note, interp="The spread implies a %s chance of surviving 5 years." % _fmt(v, "pct"))
    if eid == "C4":
        h = spread_hist(lambda x: qc.default_probability(max(x, 0.0) / (1 - R), 1.0))
        return p.res(eid, pd1, "pct", _vars(hazard=lam, pd_5y=pd5), h, feature=EQ_FEATURE[eid], applies=ap,
                     note=note, regime_from_pct=("Wide credit spreads", "Tight credit spreads"),
                     interp=lambda pc: "Market-implied default probability is %s over 1 year and %s over 5%s." % (
                         _fmt(pd1, "pct"), _fmt(pd5, "pct"), (" (%s of its history)" % _ordinal(pc))
                         if pc is not None else ""))
    if eid == "C5":
        v = qc.credit_triangle_spread(lam, R)
        h = spread_hist(lambda x: x)
        return p.res(eid, v, "bp", _vars(hazard=lam, recovery=R), h, feature=EQ_FEATURE[eid], applies=ap, note=note,
                     regime_from_pct=("Wide credit spreads", "Tight credit spreads"),
                     interp=lambda pc: "The credit spread is %s%s." % (
                         _fmt(v, "bp"), (", %s of its history (%s)" % (_ordinal(pc), _level(pc))) if pc is not None
                         else ""))
    acc, dfs, q = c["acc"], c["dfs"], c["q"]
    if eid == "C6":
        v = qc.cds_protection_leg_pv(NOTIONAL, R, dfs, q)
        return p.res(eid, v, "money", _vars(hazard=lam, rate=c["r"], maturity=5), applies=ap, note=note,
                     interp="Protection on $10mm for 5 years is worth %s today." % _fmt(v, "money"))
    if eid == "C7":
        v = qc.cds_premium_leg_pv(NOTIONAL, s, acc, dfs, q)
        return p.res(eid, v, "money", _vars(spread=s, rate=c["r"]), applies=ap, note=note,
                     interp="Paying %s a year for 5 years is worth %s today." % (_fmt(s, "bp"), _fmt(v, "money")))
    if eid == "C8":
        v = qc.par_cds_spread(R, acc, dfs, q)
        return p.res(eid, v, "bp", _vars(hazard=lam, credit_triangle=s), feature=EQ_FEATURE[eid], applies=ap,
                     note=note, interp="The par 5-year CDS spread is %s (credit triangle %s)." % (
                         _fmt(v, "bp"), _fmt(s, "bp")))
    v = qc.bootstrap_flat_hazard(s, R, 5.0, c["r"], 4)
    return p.res(eid, v, "pct", _vars(spread=s, first_guess=lam), applies=ap, note=note,
                 interp="The flat hazard rate that reprices a %s CDS is %s a year." % (_fmt(s, "bp"), _fmt(v, "pct")))


# ============================================================================ public API

_CACHE = {"entry": None}   # (ctx, key, prep): the last context, so the tabs reuse the catalogue work


def _key(ctx):
    try:
        d = ctx.get("dates") or []
        a = ctx.get("adj") or ctx.get("close") or []
        return (len(d), d[-1] if d else None, a[-1] if a else None, id(ctx.get("signals")), id(ctx.get("portfolio")),
                id(ctx.get("ml")), (ctx.get("asset") or {}).get("id"))
    except Exception:  # noqa: BLE001
        return None


def _prep(ctx):
    k = _key(ctx)
    entry = _CACHE["entry"]          # one atomic read; the tuple is never mutated
    if entry is not None and entry[0] is ctx and entry[1] == k:
        return entry[2]
    p = _Prep(ctx)
    _CACHE["entry"] = (ctx, k, p)
    return p


def _eval_one(p, eq):
    eid = eq["id"]
    fn = _H.get(eid)
    try:
        if fn is None:
            return p.empty(eid, "No evaluator for this equation.")
        return fn(p, eid)
    except _Insufficient as exc:
        return p.empty(eid, "Not computable: %s." % exc, experimental=eq["section"] == "nn")
    except Exception as exc:  # noqa: BLE001 - one failing item never breaks the tab
        return p.empty(eid, "Not computable: %s: %s." % (type(exc).__name__, exc), experimental=eq["section"] == "nn")


def evaluate_catalog(ctx):
    """One standardised result per catalogue equation, in catalogue order. Never raises."""
    try:
        p = _prep(ctx)
    except Exception:  # noqa: BLE001
        p = _Prep({})
    cached = getattr(p, "catalog", None)
    if cached is not None:
        return [dict(r) for r in cached]
    out = []
    for eq in EQUATIONS:
        try:
            out.append(_eval_one(p, eq))
        except Exception as exc:  # noqa: BLE001 - even the empty builder failed
            out.append({"id": eq["id"], "name": eq["name"], "family": _family(eq), "formula": eq["latex"],
                        "variables": {}, "value": None, "display": "unavailable", "fmt": "num", "percentile": None,
                        "direction": None, "signal": None, "usefulness": None, "confidence": None,
                        "best_horizon": None, "hit_rate": None, "ic": None, "n": None, "n_eff": None, "regime": None,
                        "interpretation": "%s could not be computed." % eq["name"],
                        "history": {"dates": [], "values": []}, "applies": True, "note": "Error: %s" % exc})
    p.catalog = out
    return [dict(r) for r in out]


# ============================================================================ analytics tabs (non-catalogue items)

def _meta(xid, name, family, formula, about=""):
    return {"id": xid, "name": name, "family": family, "latex": formula, "note": about}


def _x(p, xid, name, family, formula, fn):
    meta = _meta(xid, name, family, formula)
    try:
        return fn(meta)
    except _Insufficient as exc:
        return p.empty(xid, "Not computable: %s." % exc, meta=meta)
    except Exception as exc:  # noqa: BLE001
        return p.empty(xid, "Not computable: %s: %s." % (type(exc).__name__, exc), meta=meta)


def _tab_returns(p):
    out = []
    for lab, k, feat in (("1M", 21, "ret_1m"), ("3M", 63, "ret_3m"), ("12M", 252, "ret_12m")):
        def fn(meta, k=k, lab=lab, feat=feat):
            p.need(k)
            c = p.cum_lr()
            vals = [math.exp(c[hi] - c[hi - k]) - 1.0 for hi in range(k, len(p.lr) + 1)]
            v = vals[-1]
            return p.res(meta["id"], v, "ret", _vars(window=k), _series(p.rd[k - 1:], vals), feature=feat, meta=meta,
                         interp=lambda pc: "The %s return is %s%s." % (lab, _fmt(v, "ret"), (
                             ", %s of its rolling history" % _ordinal(pc)) if pc is not None else ""))
        out.append(_x(p, "X-ret-%s" % lab, "Rolling %s return" % lab, "Returns",
                      r"R_{t-%d,t} = P_t / P_{t-%d} - 1" % (k, k), fn))
    for lab, k in (("3M", 63), ("12M", 252)):
        def rel(meta, k=k, lab=lab):
            p.need(k)
            vals, dates = [], []
            m = p.market_all[p.s:p.e]
            P = p.P
            for hi in range(k, p.L):
                if m[hi] is not None and m[hi - k] is not None:
                    vals.append(P[hi] / P[hi - k] - m[hi] / m[hi - k])
                    dates.append(p.D[hi])
            if not vals or m[-1] is None or m[-1 - k] is None:
                raise _Insufficient("SPY history unavailable")
            v = vals[-1]
            return p.res(meta["id"], v, "ret", _vars(asset=P[-1] / P[-1 - k] - 1, spy=m[-1] / m[-1 - k] - 1),
                         _series(dates, vals), feature="rel_strength_6m" if lab == "12M" else "excess_3m", meta=meta,
                         interp="Over %s the asset has %s SPY by %s." % (
                             lab, "beaten" if v >= 0 else "lagged", _fmt(abs(v), "pct")))
        out.append(_x(p, "X-rel-%s" % lab, "Relative return vs SPY (%s)" % lab, "Returns",
                      r"R^{rel} = R_{asset} - R_{SPY}", rel))

    def excess(meta):
        p.need(252)
        tr = p.P[-1] / p.P[-253] - 1.0
        v = tr - p.rf
        return p.res(meta["id"], v, "ret", _vars(total_return=tr, rf=p.rf), meta=meta,
                     interp="The 12-month return exceeds cash (%s) by %s." % (_fmt(p.rf, "pct"), _fmt(v, "ret")),
                     note="Cash approximated by the current 3-month rate.")
    out.append(_x(p, "X-excess-12M", "Excess return over cash (12M)", "Returns", r"R - R_f", excess))
    return out


def _tab_statistics(p):
    out = []

    def median(meta):
        x = sorted(p.swin(252))
        v = _quantile(x, 0.5)
        return p.res(meta["id"], v, "ret", _vars(mean=_mean(x), window=len(x)), meta=meta,
                     interp="The median daily return is %s vs a mean of %s." % (_fmt(v, "ret"), _fmt(_mean(x), "ret")))
    out.append(_x(p, "X-median", "Median daily return", "Statistics", r"\tilde{R} = \mathrm{median}(R_t)", median))

    def pct(meta):
        x = sorted(p.swin(252))
        qv_ = {q: _quantile(x, q) for q in (0.05, 0.25, 0.75, 0.95)}
        return p.res(meta["id"], qv_[0.05], "ret", _vars(p5=qv_[0.05], p25=qv_[0.25], p75=qv_[0.75], p95=qv_[0.95]),
                     meta=meta, interp="On 1 day in 20 the return has been below %s; on 1 in 20 above %s." % (
                         _fmt(qv_[0.05], "ret"), _fmt(qv_[0.95], "ret")))
    out.append(_x(p, "X-percentiles", "Return percentiles (5/25/75/95)", "Statistics", r"Q_p(R_t)", pct))

    def dist(meta):
        x = p.swin(1260)
        lo, hi = min(x), max(x)
        nb = 30
        w = (hi - lo) / nb or 1e-9
        counts = [0] * nb
        for v in x:
            counts[min(nb - 1, int((v - lo) / w))] += 1
        bins = [lo + w * i for i in range(nb + 1)]
        sk, ku = qs.skewness(x), qs.excess_kurtosis(x)
        jb = len(x) / 6.0 * (sk * sk + ku * ku / 4.0)
        r = p.res(meta["id"], len(x), "int", _vars(mean=_mean(x), sd=_sd(x), skew=sk, excess_kurtosis=ku, min=lo,
                                                   max=hi, jarque_bera=jb), meta=meta,
                  interp="%d daily returns: skew %s, excess kurtosis %s; Jarque-Bera %s %s normality." % (
                      len(x), _fmt(sk, "num"), _fmt(ku, "num"), _fmt(jb, "num"),
                      "rejects" if jb > 5.99 else "does not reject"),
                  note="Histogram of daily simple returns over up to 5 years (30 bins) in history.bins / counts.")
        r["history"] = {"dates": [], "values": [], "bins": [_sig(b, 4) for b in bins], "counts": counts}
        return r
    out.append(_x(p, "X-distribution", "Return distribution", "Statistics", r"f(R) \approx \text{histogram}", dist))
    return out


def _drawdowns(P):
    peak = P[0]
    out = []
    for v in P:
        peak = max(peak, v)
        out.append(v / peak - 1.0)
    return out


def _tab_risk(p):
    out = []

    def dvol(meta):
        x = p.win(252)
        v = qs.downside_deviation(x) * math.sqrt(ANN)
        lr = p.lr
        h = _roll(len(lr), len(x), lambda lo, hi: qs.downside_deviation(lr[lo:hi]) * math.sqrt(ANN), p.rd)
        return p.res(meta["id"], v, "pct", _vars(window=len(x), total_vol=_sd(x) * math.sqrt(ANN)), h,
                     feature="downside_vol_60", meta=meta,
                     interp=lambda pc: "Downside volatility is %s vs total volatility %s%s." % (
                         _fmt(v, "pct"), _fmt(_sd(x) * math.sqrt(ANN), "pct"),
                         (" (%s of its history)" % _ordinal(pc)) if pc is not None else ""))
    out.append(_x(p, "X-downside-vol", "Downside volatility", "Risk",
                  r"\sigma_D = \sqrt{252 \cdot \frac{1}{n}\sum \min(R_t, 0)^2}", dvol))

    def te(meta):
        a, m = p.pairs(252)
        act = [x - y for x, y in zip(a, m)]
        tev = _sd(act) * math.sqrt(ANN)
        pa, pm = p.pa, p.pm
        h = _roll(len(pa), len(a), lambda lo, hi: _sd([x - y for x, y in zip(pa[lo:hi], pm[lo:hi])]) * math.sqrt(ANN),
                  p.pdates)
        return p.res(meta["id"], tev, "pct", _vars(window=len(a)), h, meta=meta,
                     interp="Tracking error vs SPY is %s a year." % _fmt(tev, "pct"))
    out.append(_x(p, "X-tracking-error", "Tracking error vs SPY", "Risk", r"TE = \sigma(R_p - R_b)\sqrt{252}", te))

    def ir(meta):
        a, m = p.pairs(252)
        act = [x - y for x, y in zip(a, m)]
        tev = _sd(act) * math.sqrt(ANN)
        v = _mean(act) * ANN / tev if tev > 0 else None
        return p.res(meta["id"], v, "ratio", _vars(active_return=_mean(act) * ANN, tracking_error=tev), meta=meta,
                     sscore=_clamp(v / 3.0, -0.4, 0.4) if v is not None else None,
                     interp="Information ratio %s: %s per unit of active risk." % (
                         _fmt(v, "ratio"), "outperformance" if (v or 0) > 0 else "underperformance"))
    out.append(_x(p, "X-information-ratio", "Information ratio", "Risk", r"IR = \frac{E[R_p - R_b]}{TE}", ir))

    def mdd(meta):
        p.need(MIN_OBS)
        dd = _drawdowns(p.P)
        v = min(dd)
        v1 = min(_drawdowns(p.P[-253:]))
        return p.res(meta["id"], v, "pct", _vars(current_drawdown=dd[-1], max_drawdown_1y=v1, years=p.L / ANN),
                     _series(p.D, dd), feature="drawdown_252", meta=meta,
                     interp="The deepest peak-to-trough loss in the history is %s; the asset is now %s below its peak." % (
                         _fmt(v, "pct"), _fmt(abs(dd[-1]), "pct")))
    out.append(_x(p, "X-max-drawdown", "Maximum drawdown", "Risk", r"MDD = \min_t (P_t / \max_{s \le t} P_s - 1)", mdd))

    def hvar(meta):
        x = sorted(p.swin(1260))
        v = -_quantile(x, 0.05)
        tail = [t for t in x if t <= -v]
        es = -_mean(tail) if tail else None
        return p.res(meta["id"], v, "pct", _vars(es_hist=es, window=len(x)), meta=meta,
                     interp="Historically 1 day in 20 has lost more than %s (average of those days %s)." % (
                         _fmt(v, "pct"), _fmt(es, "pct")))
    out.append(_x(p, "X-historical-var", "Historical VaR / ES (95%, 1 day)", "Risk",
                  r"VaR_{95} = -Q_{0.05}(R_t)", hvar))

    return out


def _mrc_item(p):
    def mrc(meta):
        q = p.port()
        cov, w = q["cov"], q["w"]
        sw = [sum(cov[i][j] * w[j] for j in range(len(w))) for i in range(len(w))]
        sig = math.sqrt(max(sum(a * b for a, b in zip(w, sw)), 1e-18))
        m = [x / sig for x in sw]
        return p.res(meta["id"], m[q["me"]], "pct", {"marginal": _wd(q["ids"], m), "portfolio_vol": _sig(sig)},
                     meta=meta, note=q["note"],
                     interp="Adding 1%% of weight to %s changes portfolio volatility by about %s points." % (
                         q["ids"][q["me"]], _fmt(m[q["me"]], "num")))
    return _x(p, "X-marginal-risk", "Marginal risk contribution", "Portfolio Theory",
              r"MRC_i = \frac{(\Sigma w)_i}{\sigma_p}", mrc)


def _tab_regression(p):
    out = []

    def rb(meta):
        h = p.beta_hist()
        if not h:
            raise _Insufficient("not enough paired returns")
        v = p.ols_mkt()["beta"][1]
        return p.res(meta["id"], v, "ratio", _vars(window=252), h, feature="beta_252", meta=meta,
                     interp=lambda pc: "The rolling 1-year beta is %s%s." % (
                         _fmt(v, "ratio"), (", %s of its history" % _ordinal(pc)) if pc is not None else ""))
    out.append(_x(p, "X-rolling-beta", "Rolling regression beta (252d)", "Regression",
                  r"\beta_t = \frac{Cov_{252}(R_i, R_m)}{Var_{252}(R_m)}", rb))

    def mf(meta):
        p.need(MIN_OBS)
        facs = [("market", None), ("d_y10", "y10"), ("dollar", "dollar"), ("oil", "oil"), ("d_credit", "credit_spread")]
        ser = {}
        for nm, src in facs[1:]:
            v = p.macro_seg(src)
            if sum(1 for x in v[-253:] if x is not None) < 200:
                continue
            if nm in ("dollar", "oil"):
                ser[nm] = [math.log(v[i + 1] / v[i]) if v[i] and v[i + 1] and v[i] > 0 and v[i + 1] > 0 else None
                           for i in range(len(v) - 1)]
            else:
                ser[nm] = [v[i + 1] - v[i] if v[i] is not None and v[i + 1] is not None else None
                           for i in range(len(v) - 1)]
        names = ["market"] + list(ser)
        rows, ys = [], []
        for i in range(len(p.lr)):
            row = [p.mret[i]] + [ser[n][i] for n in names[1:]]
            if any(x is None for x in row):
                continue
            rows.append([1.0] + row)
            ys.append(p.lr[i])
        rows, ys = rows[-252:], ys[-252:]
        if len(ys) < MIN_OBS:
            raise _Insufficient("not enough rows with all factors")
        f = qr.ols_fit(rows, ys)
        missing = [n for n, _ in facs if n not in names]
        return p.res(meta["id"], f["r2"], "pct", {"coef": _named(["const"] + names, f["beta"]),
                                                  "t": _named(["const"] + names, f["t"]), "n": len(ys),
                                                  "adj_r2": _sig(f["adj_r2"])}, meta=meta,
                     interp="Market, rates, dollar, oil and credit together explain %s of daily moves; significant: %s." % (
                         _fmt(f["r2"], "pct"), ", ".join(n for n, t in zip(names, f["t"][1:]) if abs(t) >= 2) or "none"),
                     note="Daily log returns on SPY return, Δ10y yield, dollar and oil log changes, Δcredit spread "
                          "(last year)." + (" Missing factors: %s." % ", ".join(missing) if missing else ""))
    out.append(_x(p, "X-multifactor", "Multifactor regression", "Regression",
                  r"R_t = \alpha + \beta_m R_m + \beta_y \Delta y + \beta_\$ r_\$ + \beta_o r_o + \beta_c \Delta s + \varepsilon", mf))

    def stab(meta):
        a, m = p.pairs(756, 180)
        k = len(a) // 3
        betas = [qp.market_beta(a[i * k:(i + 1) * k], m[i * k:(i + 1) * k]) for i in range(3)]
        v = _sd(betas)
        return p.res(meta["id"], v, "ratio", _vars(betas=betas, sessions_each=k), meta=meta,
                     interp="Beta across three sub-periods was %s: %s." % (
                         ", ".join(_fmt(b, "ratio") for b in betas), "stable" if v < 0.15 else
                         "moderately unstable" if v < 0.35 else "unstable"),
                     note="Dispersion (standard deviation) of beta in three equal sub-periods of up to 3 years.")
    out.append(_x(p, "X-beta-stability", "Regression stability (beta by sub-period)", "Regression",
                  r"\mathrm{sd}(\hat\beta_1, \hat\beta_2, \hat\beta_3)", stab))
    return out


def _tab_timeseries(p):
    out = []

    def pacf(meta):
        x = p.win(1260)
        rho = qt.acf(x, 5)
        ph = _durbin_levinson(rho)
        band = 1.96 / math.sqrt(len(x))
        return p.res(meta["id"], ph[0], "ratio", _vars(pacf=ph, band_95=band, n=len(x)), meta=meta,
                     interp="Partial autocorrelations (lags 1-5): %s; %s." % (
                         ", ".join(_fmt(v, "ratio") for v in ph),
                         "none outside the noise band" if all(abs(v) <= band for v in ph) else
                         "some lags exceed the ±%s band" % _fmt(band, "ratio")),
                     note="Durbin-Levinson recursion on the sample autocorrelations.")
    out.append(_x(p, "X-pacf", "Partial autocorrelation (PACF)", "Time Series", r"\phi_{kk}", pacf))

    def mom(meta):
        p.need(21 * 12)
        lr = p.lr
        blocks = [sum(lr[e - 21:e]) for e in range(len(lr), 20, -21)][::-1]
        v = qt.autocorrelation(blocks, 1)
        band = 1.96 / math.sqrt(len(blocks))
        return p.res(meta["id"], v, "ratio", _vars(months=len(blocks), band_95=band), meta=meta,
                     feature="mom_12_1",
                     interp="Month-to-month return autocorrelation is %s: %s." % (
                         _fmt(v, "ratio"), "momentum has persisted" if v > band else
                         "months have tended to reverse" if v < -band else "no reliable persistence"),
                     note="Lag-1 autocorrelation of non-overlapping 21-session returns.")
    out.append(_x(p, "X-momentum-persistence", "Momentum persistence", "Time Series",
                  r"\rho_1(R^{(21)})", mom))
    return out


def _tab_volatility(p):
    out = []

    def regime(meta):
        vol, dates = p.vol20()
        w = min(756, len(vol))
        v = _pctile(vol[-1], vol[-w:])
        lab = "Low" if v < 0.2 else "Normal" if v < 0.8 else "High" if v < 0.95 else "Extreme"
        h = _roll(len(vol), w, lambda lo, hi: _pctile(vol[hi - 1], vol[lo:hi]), dates)
        r = p.res(meta["id"], v, "pct", _vars(vol_20=vol[-1], window=w), h, feature="vol_pctile",
                  meta=meta, regime="%s volatility" % lab, display="%s (%s)" % (_ordinal(v), lab),
                  interp="20-day volatility of %s is in its %s over 3 years: %s volatility regime." % (
                      _fmt(vol[-1], "pct"), _ordinal(v), lab.lower()))
        r["percentile"] = round(v, 3)
        return r
    out.append(_x(p, "X-vol-regime", "Volatility regime", "Volatility", r"\mathrm{pctile}_{3y}(\sigma_{20})", regime))

    def vov(meta):
        vol, dates = p.vol20()
        if len(vol) < 126:
            raise _Insufficient("needs 126 days of 20-day volatility")
        v = _sd(vol[-126:])
        h = _roll(len(vol), 126, lambda lo, hi: _sd(vol[lo:hi]), dates)
        return p.res(meta["id"], v, "pct", _vars(window=126), h, feature="vol_of_vol", meta=meta,
                     interp=lambda pc: "Volatility itself has varied by %s (sd of 20-day vol over 6 months)%s." % (
                         _fmt(v, "pct"), (", %s of its history" % _ordinal(pc)) if pc is not None else ""))
    out.append(_x(p, "X-vol-of-vol", "Volatility of volatility", "Volatility", r"\mathrm{sd}_{126}(\sigma_{20})", vov))
    return out


def _tab_fixed_income(p):
    out = []

    def curve(meta):
        cur = p.curve()
        if 0.25 not in cur or 10.0 not in cur:
            raise _Insufficient("3-month and 10-year yields needed")
        old = p.curve_at(p.N - 1 - 63) if p.N > 63 else {}
        slope = cur[10.0] - cur[0.25]
        chg = {str(k): _sig(cur[k] - old[k]) for k in cur if k in old}
        y3, y10 = p.macro_all("y3m"), p.macro_all("y10")
        idx = [i for i in range(p.N) if y3[i] is not None and y10[i] is not None]
        h = _series([p.dates_all[i] for i in idx], [y10[i] - y3[i] for i in idx]) if len(idx) > 10 else None
        return p.res(meta["id"], slope, "bp", {"curve": {str(k): _sig(v) for k, v in cur.items()}, "change_3m": chg},
                     h, feature="slope_10y3m", meta=meta, regime="Inverted curve" if slope < 0 else None,
                     interp="The 10y − 3m slope is %s (%s)%s." % (
                         _fmt(slope, "bp"), "inverted" if slope < 0 else "upward sloping",
                         ("; the 10-year moved %s in 3 months" % _fmt(cur[10.0] - old[10.0], "bp"))
                         if 10.0 in old else ""))
    out.append(_x(p, "X-yield-curve", "Yield curve", "Fixed Income", r"y(T),\; y_{10} - y_{3m}", curve))

    def spread(meta):
        h = p.credit_hist()
        if not h:
            raise _Insufficient("credit spread unavailable")
        v = h["values"]
        d1 = v[-1] - v[-22] if len(v) > 22 else None
        d3 = v[-1] - v[-64] if len(v) > 64 else None
        return p.res(meta["id"], d3, "bp", _vars(spread=v[-1], change_1m=d1, change_3m=d3),
                     _series(h["dates"], v), pool=[v[i] - v[i - 63] for i in range(63, len(v))][-2520:],
                     feature="d_credit_3m", meta=meta,
                     interp="The BAA − 10Y spread is %s, %s %s over 3 months." % (
                         _fmt(v[-1], "bp"), "wider by" if (d3 or 0) >= 0 else "tighter by", _fmt(abs(d3 or 0), "bp")))
    out.append(_x(p, "X-spread-change", "Credit spread changes", "Fixed Income", r"\Delta s_{3M}", spread))
    return out


_VALUATION = [("value_5y", "Value vs 5-year mean (−z)", "num"), ("earnings_yield", "Earnings yield", "pct"),
              ("pe_rel_5y", "P/E vs own 5 years (z)", "num"), ("eps_growth_yoy", "EPS growth (YoY)", "pct"),
              ("rev_growth_yoy", "Revenue growth (YoY)", "pct"), ("net_margin", "Net margin", "pct")]


def _tab_valuation(p):
    out = []
    for key, name, fmt in _VALUATION:
        def fn(meta, key=key, name=name, fmt=fmt):
            col = p.feat(key)
            v = p.feat_last(key, 400) if col else None
            if v is None:
                return p.res(meta["id"], None, fmt, meta=meta, note="Not available for this asset.",
                             interp="%s is not available for this asset." % name)
            seg = col[p.s:p.e] if p.e > p.s else col
            idx = [i for i, x in enumerate(seg) if x is not None]
            h = _series([p.D[i] for i in idx], [seg[i] for i in idx]) if len(idx) > 10 else None
            return p.res(meta["id"], v, fmt, {}, h, feature=key, meta=meta,
                         interp=lambda pc: "%s is %s%s." % (name, _fmt(v, fmt), (", %s of its own history" %
                                                                                 _ordinal(pc)) if pc is not None else ""),
                         note="Point-in-time feature (fundamentals keyed by SEC filing date).")
        out.append(_x(p, "X-%s" % key, name, "Valuation", key.replace("_", r"\_"), fn))
    return out


def analytics_tabs(ctx, catalog=None):
    """Tab name -> standardised results (relevant catalogue equations + non-catalogue analytics). Never raises."""
    tabs = {t: [] for t in TABS}
    try:
        p = _prep(ctx)
    except Exception:  # noqa: BLE001
        p = _Prep({})
    cat = catalog if catalog is not None else evaluate_catalog(ctx)
    for r in cat:
        t = _TAB_OF_FAMILY.get(r.get("family"))
        if t:
            tabs[t].append(r)
    builders = {"Returns": _tab_returns, "Statistics": _tab_statistics, "Risk": _tab_risk,
                "Regression": _tab_regression, "Time Series": _tab_timeseries, "Volatility": _tab_volatility,
                "Fixed Income": _tab_fixed_income, "Valuation": _tab_valuation}
    for t, fn in builders.items():
        try:
            tabs[t].extend(fn(p))
        except Exception:  # noqa: BLE001
            pass
    try:
        tabs["Portfolio"].append(_mrc_item(p))
    except Exception:  # noqa: BLE001
        pass
    return tabs


# ============================================================================ summary header

def summary_numbers(ctx):
    """Headline values for the Analytics header (None where not computable). Never raises."""
    try:
        p = _prep(ctx)
    except Exception:  # noqa: BLE001
        p = _Prep({})
    out = {"asset": p.aid or None, "as_of": p.D[-1] if p.D else None, "n": p.L}

    def put(key, fn):
        try:
            v = fn()
            out[key] = _sig(_f(v), 6) if not isinstance(v, bool) else v
        except Exception:  # noqa: BLE001
            out[key] = None

    def vol20():
        f = p.feat_last("vol_20")
        if f is not None:
            return f
        return p.vol20()[0][-1]
    put("vol_20", vol20)
    put("garch_vol", lambda: math.sqrt(p.garch()["next_var"] * ANN))
    put("beta", lambda: p.ols_mkt()["beta"][1])
    put("sharpe", lambda: qs.sharpe_ratio(p.win(252), p.rf / ANN, ANN))
    put("max_drawdown", lambda: min(_drawdowns(p.P)) if p.L >= 2 else None)
    put("var_95", lambda: qp.parametric_var(-_mean(p.swin(252)), _sd(p.swin(252)), 0.95))
    put("es_95", lambda: qp.expected_shortfall(-_mean(p.swin(252)), _sd(p.swin(252)), 0.95))
    put("half_life", lambda: qsto.half_life(p.ou()["kappa_d"]) if "kappa_d" in p.ou() else None)

    def adf_p():
        p.need(MIN_OBS)
        lp = [math.log(v) for v in p.P[-253:]]
        return _adf_p(qt.adf_test(lp, 1, "ct")["t_stat"])
    put("adf_p", adf_p)
    out["rf"] = _sig(p.rf, 6)
    return _clean(out)
