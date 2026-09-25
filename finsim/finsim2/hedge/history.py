"""Walk-forward evaluation of hedges, the hedge baselines, and the capped ML adjustment.

For each month-start t (up to 15 years back, stepping 21 sessions), with data up to t only:
    * the position's exposure to the risk being hedged (trailing-252-day beta to the factor, or structural DV01/CS01);
    * the hedge's exposure per unit at t (its own beta, the model DV01 of a Treasury future, the option's delta);
    * the hedge quantity by the product's sizing rule, and the variance reduction EXPECTED from the trailing year.
Then over (t, t+h] the realised daily P&L of the position alone and with the hedge: variance reduction, tail loss,
drawdown, hedge P&L, upside given up and basis error. Futures are rolled at fair value, options are re-priced every
day with the Cboe volatility index of that day (European Black-Scholes; static = bought and held, dynamic = the
contract count reset weekly to the target delta), forwards carry the rate differential.

Baselines on the same windows: no hedge, fixed 25% and 50% of the exposure, the static rule (the Raw Shaffer Hedge
ratio) and the minimum-variance ratio (trailing regression of the position's P&L on the hedge's). The ML layer learns,
walk-forward, a multiplier on the raw ratio from conditions at t; it is used only if it beats the static rule and the
minimum-variance ratio out of sample (see `ml_layer`)."""
from __future__ import annotations

import math
from array import array
from typing import Dict, List, Optional, Tuple

from . import pricing as px
from .market import Market
from .risk import factor_series

STEP = 21
YEARS = 15
MIN_WINDOWS = 24
ALPHA = 0.5            # FinalHedge = RawHedge × (1 + ALPHA · MLAdjustment)
ADJ_CAP = 0.30         # |MLAdjustment| ≤ 0.30 → the final ratio stays within ±15% of the raw ratio
ML_MIN_NEFF = 30
VERSION = "hedge-2"          # hedge-2: objective-specific models (objml)


# ------------------------------------------------------------------ series
def _fx_ret(research, ccy: str) -> List[Optional[float]]:
    return factor_series(research).get(f"FX:{ccy}") or []


def _rate_series(research, sid: str) -> List[Optional[float]]:
    s = research.panel().macro(sid, lag_days=0, max_age_days=60)
    return [(v / 100.0) if v is not None else None for v in s]


def _ffill(xs):
    out, last = [], None
    for v in xs:
        last = v if v is not None else last
        out.append(last)
    return out


def _div_yield_series(research, asset_id: str) -> List[float]:
    """Trailing-year dividends / price on each date (point in time)."""
    def build():
        p = research.panel()
        cal = p.calendar()
        close = _ffill(p.series(asset_id, "close", max_fill=10))
        evs = [(e["date"], e["value"]) for e in research.store.actions(asset_id, "DIVIDEND")]
        out, j0, j1, run = [], 0, 0, 0.0
        import datetime as _dt
        for k, d in enumerate(cal):
            while j1 < len(evs) and evs[j1][0] <= d:
                run += evs[j1][1]; j1 += 1
            lo = (_dt.date.fromisoformat(d) - _dt.timedelta(days=365)).isoformat()
            while j0 < j1 and evs[j0][0] <= lo:
                run -= evs[j0][1]; j0 += 1
            out.append(run / close[k] if close[k] else 0.0)
        return out
    return research._memo(f"hedge:dy:{asset_id}", build)


class Leg:
    """A hedge template evaluated historically: spot asset, rolling equity/Treasury future, option strategy or FX
    forward. `unit_pnl(k0, k1, q)` gives the daily dollar P&L of holding q units opened at session k0."""

    def __init__(self, research, spec: dict):
        self.r, self.spec = research, spec
        self.kind = spec["kind"]
        self.m = Market(research)
        self.cal = self.m.cal
        self.rate = _ffill(_rate_series(research, "DGS3MO"))

    # per-unit exposure at session k (to the objective's primary factor; filled in by Evaluator)
    def returns(self) -> List[Optional[float]]:
        """Daily return per $1 of notional (linear legs)."""
        k = self.kind
        if k == "spot":
            r = self.m.usd_returns(self.spec["asset"])
            if self.spec.get("short_borrow"):
                return r
            return r
        if k == "equity_future":
            idx = self.spec["index"]
            p = self.r.panel().series(idx, "close")
            dy = _div_yield_series(self.r, self.spec["proxy"])
            out = [None]
            for d in range(1, len(p)):
                if p[d] and p[d - 1] and self.rate[d] is not None:
                    out.append(p[d] / p[d - 1] - 1 - (self.rate[d] - dy[d]) / 252.0)
                else:
                    out.append(None)
            return out
        if k == "forward":
            ccy = self.spec["ccy"]
            fr = _ffill(_rate_series(self.r, self.spec["foreign_rate"])) if self.spec.get("foreign_rate") else [None] * len(self.cal)
            s = _fx_ret(self.r, ccy)
            return [((v - ((self.rate[d] or 0.0) - (fr[d] if fr[d] is not None else (self.rate[d] or 0.0))) / 252.0) if v is not None else None)
                    for d, v in enumerate(s)]
        raise ValueError(k)


def _beta(y, x, k, w=252) -> Tuple[Optional[float], Optional[float]]:
    """(beta, correlation) of y on x over (k−w, k]."""
    pairs = [(a, b) for a, b in zip(x[max(1, k - w + 1):k + 1], y[max(1, k - w + 1):k + 1]) if a is not None and b is not None]
    if len(pairs) < 120:
        return None, None
    n = len(pairs)
    mx = sum(a for a, _ in pairs) / n
    my = sum(b for _, b in pairs) / n
    sxx = sum((a - mx) ** 2 for a, _ in pairs)
    syy = sum((b - my) ** 2 for _, b in pairs)
    sxy = sum((a - mx) * (b - my) for a, b in pairs)
    if sxx <= 0 or syy <= 0:
        return None, None
    return sxy / sxx, sxy / math.sqrt(sxx * syy)


class Evaluator:
    """Walk-forward evaluation of one hedge leg against one book of positions for one objective and horizon.

    book: [{"asset": id, "mv": dollars}] (today's dollar exposures, held constant through history)
    objective: "equity" (market beta), "rates" (DV01), "credit" (CS01), "fx:<CCY>", "commodity:<FACTOR>", "crypto",
               "name" (the position's own risk: beta 1 to itself), "sector:<ETF>"
    leg: {"kind": "spot"|"equity_future"|"treasury_future"|"option"|"forward", ...}
    ratio: the share of the exposure hedged (the raw hedge ratio)."""

    def __init__(self, research, book: List[dict], objective: str, leg: dict, h: int, ratio: float = 1.0, years: int = YEARS,
                 dynamic_options: bool = False):
        self.r, self.book, self.obj, self.leg, self.h, self.ratio = research, book, objective, leg, h, ratio
        self.m = Market(research)
        self.cal = self.m.cal
        self.n = len(self.cal)
        self.F = factor_series(research)
        self.years = years
        self.dynamic = dynamic_options
        self.rate = _ffill(_rate_series(research, "DGS3MO"))

    # -------------------------------------------------------------- the book
    def book_pnl(self) -> List[Optional[float]]:
        out = [0.0] * self.n
        ok = [True] * self.n
        for p in self.book:
            r = self.m.usd_returns(p["asset"])
            for d in range(self.n):
                if r[d] is None:
                    ok[d] = False
                else:
                    out[d] += p["mv"] * r[d]
        return [v if ok[d] else None for d, v in enumerate(out)]

    def factor(self) -> Optional[List[Optional[float]]]:
        o = self.obj
        if o == "equity":
            return self.F["MKT"]
        if o.startswith("fx:"):
            return self.F.get("FX:" + o[3:])
        if o.startswith("commodity:"):
            return self.F.get(o[10:])
        if o == "crypto":
            return self.F["CRYPTO"]
        if o.startswith("sector:"):
            return self.F.get("SEC:" + o[7:]) or self.F.get("IND:" + o[7:])
        if o == "rates":
            return self.F["RATE:10Y"]                 # basis points; exposures are $ per +1bp
        if o == "credit":
            return self.F["CREDIT:HY"]
        return None

    # -------------------------------------------------------------- one leg's P&L inside a window
    def _leg_window(self, k0: int, k1: int, target_usd: float, pos_series: List[Optional[float]]) -> Optional[dict]:
        """Daily hedge P&L over (k0, k1] for a hedge sized at k0 to offset `target_usd` of the objective's exposure
        (negative target = the hedge must LOSE when the factor rises). Returns the P&L list and sizing facts."""
        L = self.leg
        kind = L["kind"]
        f = self.factor()
        if kind in ("spot", "equity_future", "forward"):
            if kind == "spot" and L.get("fx_pair"):
                ccy, sgn = L["fx_pair"]                      # an FX pair: per $ it moves ± the currency's return
                fr = self.F.get(f"FX:{ccy}") or []
                ret = [(sgn * v) if v is not None else None for v in fr]
            elif kind == "spot":
                ret = self.m.usd_returns(L["asset"])
            else:
                ret = Leg(self.r, L).returns()
            if self.obj == "name":
                b_h = 1.0
            elif L.get("unit_exposure"):
                b_h = L["unit_exposure"]                 # structural: DV01, CS01, currency or commodity $ per $1 of the leg
            else:
                b_h, _ = _beta(ret, f, k0) if f is not None else (None, None)
            if not b_h:
                return None
            notional = target_usd / b_h          # $ of the hedge leg (signed: negative = short)
            borrow = (0.003 / 252.0) if (kind == "spot" and notional < 0) else 0.0
            pnl = []
            for d in range(k0 + 1, k1 + 1):
                v = ret[d]
                if v is None:
                    return None
                pnl.append(notional * v - abs(notional) * borrow)
            return {"pnl": pnl, "notional": notional, "beta": b_h}
        if kind == "treasury_future":
            curves = self._curves()
            if curves[k0] is None:
                return None
            T0 = 0.25
            spec = L["spec"]
            tf0 = px.treasury_future(curves[k0], spec["ctd_years"], T0, spec["face"])
            # target_usd is the exposure to add, in $ per +1bp; one contract carries −DV01 per +1bp
            q = target_usd / -tf0["dv01"]
            pnl, prev = [], tf0["price"]
            T = T0
            for d in range(k0 + 1, k1 + 1):
                T -= 1 / 252.0
                if T <= 1 / 252.0:                 # roll into the next quarter at fair value
                    T = 0.25
                    prev = px.treasury_future(curves[d - 1], spec["ctd_years"], T, spec["face"])["price"] if curves[d - 1] else prev
                c = curves[d]
                if c is None:
                    return None
                p = px.treasury_future(c, spec["ctd_years"], T, spec["face"])["price"]
                pnl.append(q * (p - prev) / 100.0 * spec["face"])
                prev = p
            return {"pnl": pnl, "contracts": q, "dv01": tf0["dv01"]}
        if kind == "option":
            return self._option_window(k0, k1, target_usd)
        return None

    def _curves(self):
        def build():
            from .market import CMT
            ser = [(t, _rate_series(self.r, sid)) for t, sid in CMT]
            out = []
            for d in range(self.n):
                pts = {t: s[d] for t, s in ser if s[d] is not None}
                out.append(px.Curve(pts) if len(pts) >= 3 else None)
            return out
        return self.r._memo("hedge:curves", build)

    def _option_window(self, k0: int, k1: int, target_usd: float) -> Optional[dict]:
        L = self.leg
        und = L["underlying"]
        S = self.r.panel().series(und, "close")
        vol = self.m.vol_index_series(L.get("vol_underlying", und))
        if vol is None:
            return None
        dy = _div_yield_series(self.r, L.get("proxy", und))
        if not S[k0] or vol[k0] is None or self.rate[k0] is None:
            return None
        right, mny, tenor = L["right"], L["moneyness"], L["tenor_days"]
        f = self.factor()
        b_u, _ = _beta(self.m.usd_returns(und), f, k0) if (f is not None and self.obj != "name") else (1.0, None)
        if not b_u:
            return None

        def open_at(k):
            K = S[k] * mny
            return K, tenor / 365.0

        K, T = open_at(k0)
        g = px.option(right, S[k0], K, T, self.rate[k0], dy[k0], vol[k0])
        per_contract_delta_usd = g["delta"] * S[k0] * 100 * b_u
        if not per_contract_delta_usd:
            return None
        q = target_usd / per_contract_delta_usd
        if q < 0:                                   # options are only bought: a wrong-signed target uses the other right
            return None
        prem0 = g["price"] * 100 * q
        pnl, v_prev = [], g["price"]
        age = 0
        for d in range(k0 + 1, k1 + 1):
            age += 1
            T_now = T - age / 252.0
            if S[d] is None or vol[d] is None:
                return None
            if T_now <= 1 / 365.0:                   # expiry inside the window: settle and roll into a new option
                v_exp = max(0.0, (S[d] - K) if right == "C" else (K - S[d]))
                pnl.append(q * 100 * (v_exp - v_prev))
                K, T = open_at(d)
                age = 0
                g = px.option(right, S[d], K, T, self.rate[d] or 0.0, dy[d], vol[d])
                v_prev = g["price"]
                continue
            v = px.option_price(right, S[d], K, T_now, self.rate[d] or 0.0, dy[d], vol[d])
            pnl.append(q * 100 * (v - v_prev))
            v_prev = v
            if self.dynamic and age % 5 == 0:        # dynamic: reset the contract count to the target delta
                gd = px.option(right, S[d], K, T_now, self.rate[d] or 0.0, dy[d], vol[d])
                dd = gd["delta"] * S[d] * 100 * b_u
                if dd:
                    q = max(0.0, target_usd / dd)
        return {"pnl": pnl, "contracts": q, "premium": prem0, "delta0": g["delta"]}

    # -------------------------------------------------------------- the walk-forward
    def exposure_at(self, k: int, pos_series) -> Optional[float]:
        """The book's exposure to the objective at session k in the objective's units (factor dollars)."""
        o = self.obj
        if o == "name":
            return sum(p["mv"] for p in self.book)
        if all(p.get("structural") is not None for p in self.book):
            return sum(p["mv"] * p["structural"] for p in self.book) or None     # e.g. currency look-through
        f = self.factor()
        if f is None:
            return None
        b, _ = _beta(pos_series, f, k)
        return b                                   # pos_series is book $ P&L: its beta to the factor is already in dollars

    def run(self) -> dict:
        pos = self.book_pnl()
        start = max(260, self.n - 1 - int(self.years * 252))
        regimes = self.r.regimes()
        rows = []
        for k0 in range(start, self.n - 1 - self.h, STEP):
            k1 = k0 + self.h
            if any(pos[d] is None for d in range(k0 + 1, k1 + 1)):
                continue
            expo = self.exposure_at(k0, pos)
            if expo is None:
                continue
            target = -self.ratio * expo
            leg = self._leg_window(k0, k1, target, pos)
            if leg is None:
                continue
            u = [pos[d] for d in range(k0 + 1, k1 + 1)]
            hp = leg["pnl"]
            hedged = [a + b for a, b in zip(u, hp)]
            # expected reduction from the trailing year with the same sizing (in-sample basis at k0)
            exp_red = self._expected_reduction(k0, target, pos)
            # baselines inside the window: the leg's P&L scales linearly with the ratio for linear legs
            base = {}
            for lab, rr in (("fixed_25", 0.25), ("fixed_50", 0.50)):
                sc = rr / self.ratio if self.ratio else 0.0
                base[lab] = sum((a + sc * b) ** 2 for a, b in zip(u, hp))
            mv_ratio = self._minvar_ratio(k0, pos, target)
            # the minimum-variance hedge for the SAME target fraction: the full min-variance multiple × the target share
            mv_mult = (mv_ratio * self.ratio) if mv_ratio is not None else None
            base["min_variance"] = sum((a + mv_mult * b) ** 2 for a, b in zip(u, hp)) if mv_mult is not None else None
            var_u = sum(a * a for a in u)
            var_h = sum(a * a for a in hedged)

            def mdd(xs):
                eq = peak = dd = 0.0
                for x in xs:
                    eq += x; peak = max(peak, eq); dd = min(dd, eq - peak)
                return dd
            st = {dim: (s[k0] if s else None) for dim, s in regimes.items()}
            fpath = self.factor()
            feats = self._features(k0, pos)
            feats.update(self.leg_features(k0, target, leg, expo))
            feats.update({"bear": _flag(st.get("market"), "bear"), "high_vol": _flag(st.get("volatility"), "high_vol"),
                          "rising_rates": _flag(st.get("rates"), "rising_rates")})
            rows.append({"k0": k0, "date": self.cal[k0], "end": self.cal[k1], "h": self.h, "ratio": self.ratio, "var_u": var_u, "var_h": var_h, "U": sum(u), "H": sum(hp),
                         "var_leg": sum(b * b for b in hp), "cov_uh": sum(a * b for a, b in zip(u, hp)),
                         "dd_u": mdd(u), "dd_h": mdd(hedged), "expected_reduction": exp_red, "baselines": base,
                         "regime": st, "exposure": expo, "sizing": {k: v for k, v in leg.items() if k != "pnl"},
                         "realized_opt_ratio": self._ex_post_ratio(u, hp), "features": feats, "mv_mult": mv_mult,
                         # the daily paths, compact: the objective-specific ML (objml) re-scores any hedge multiple exactly
                         "_u": array("d", u), "_hp": array("d", hp),
                         "_f": array("d", [(fpath[d] if fpath and fpath[d] is not None else math.nan) for d in range(k0 + 1, k1 + 1)])})
        return summarize(rows, self.h)

    def _expected_reduction(self, k0, target, pos) -> Optional[float]:
        lo = max(1, k0 - 251)
        # simulate the same sized leg over the trailing year (linear legs: notional × return)
        leg = self._leg_window(lo, k0, target, pos) if self.leg["kind"] in ("spot", "equity_future", "forward") else None
        if leg is None:
            return None
        u = [pos[d] for d in range(lo + 1, k0 + 1)]
        if any(v is None for v in u):
            return None
        vu = sum(a * a for a in u)
        vh = sum((a + b) ** 2 for a, b in zip(u, leg["pnl"]))
        return 1 - vh / vu if vu > 0 else None

    def _minvar_ratio(self, k0, pos, target) -> Optional[float]:
        """Minimum-variance multiple of the sized leg: regression of the position's P&L on the leg's over the trailing
        year (point in time), expressed as a multiple of the sized quantity."""
        if self.leg["kind"] not in ("spot", "equity_future", "forward"):
            return None
        lo = max(1, k0 - 251)
        leg = self._leg_window(lo, k0, target, pos)
        if leg is None:
            return None
        u = [pos[d] for d in range(lo + 1, k0 + 1)]
        hp = leg["pnl"]
        sxx = sum(b * b for b in hp)
        if sxx <= 0 or any(v is None for v in u):
            return None
        return -sum(a * b for a, b in zip(u, hp)) / sxx

    @staticmethod
    def _ex_post_ratio(u, hp) -> Optional[float]:
        sxx = sum(b * b for b in hp)
        return (-sum(a * b for a, b in zip(u, hp)) / sxx) if sxx > 0 else None

    def _features(self, k0, pos) -> dict:
        """Conditions at k0 (point in time) for the ML adjustment. The first six are the original hedge-ML inputs; the
        rest are the richer set: correlation stability, basis risk, the factor's and the leg's recent vol against their
        year, implied against realised market vol, and the book's exposure per dollar (beta, DV01/$, CS01/$...)."""
        f = self.factor()
        b252, c252 = _beta(pos, f, k0, 252) if f is not None else (None, None)
        b63, c63 = _beta(pos, f, k0, 63) if f is not None else (None, None)
        vix = self.m.vol_index_series("SPX")
        v = vix[k0] if vix else None
        mk = self.F["MKT"]
        m3 = sum(x for x in mk[k0 - 62:k0 + 1] if x is not None)
        rv21 = _rv(mk, k0, 21)
        mv = sum(abs(p["mv"]) for p in self.book) or None
        return {"vix": v, "corr_252": c252, "corr_63": c63, "beta_change": ((b63 / b252 - 1) if b252 and b63 is not None else None),
                "mkt_3m": m3, "rate": self.rate[k0],
                "corr_instability": (abs(c63 - c252) if c63 is not None and c252 is not None else None),
                "basis_risk": (math.sqrt(max(0.0, 1 - c63 * c63)) if c63 is not None else None),
                "factor_vol_ratio": _ratio(_rv(f, k0, 63), _rv(f, k0, 252)) if f is not None else None,
                "iv_rv": _ratio(v, rv21), "exposure_per_dollar": (b252 / mv) if (b252 is not None and mv) else None}

    def leg_features(self, k0: int, target: float, leg: Optional[dict] = None, expo: Optional[float] = None) -> dict:
        """The hedge leg's own conditions at k0: its recent vol against its year (linear legs); for options the delta,
        the premium per dollar of exposure hedged (cost), the implied vol and the tenor against the horizon."""
        L = self.leg
        out = {"leg_vol_ratio": None, "delta0": None, "cost": None, "iv": None, "tenor_ratio": None}
        if L["kind"] in ("spot", "equity_future", "forward"):
            try:
                ret = self.m.usd_returns(L["asset"]) if L["kind"] == "spot" and not L.get("fx_pair") else Leg(self.r, L).returns() if L["kind"] != "spot" else None
            except Exception:  # noqa: BLE001
                ret = None
            if ret:
                out["leg_vol_ratio"] = _ratio(_rv(ret, k0, 63), _rv(ret, k0, 252))
        elif L["kind"] == "option":
            vol = self.m.vol_index_series(L.get("vol_underlying", L["underlying"]))
            out["iv"] = vol[k0] if vol else None
            out["tenor_ratio"] = L["tenor_days"] * 252 / 365.0 / max(1, self.h)
            if leg and leg.get("delta0") is not None:
                out["delta0"] = leg["delta0"]
                out["cost"] = leg["premium"] / abs(target) if target else None
            elif out["iv"] is not None and self.rate[k0] is not None:
                S = self.r.panel().series(L["underlying"], "close")
                if S[k0]:
                    g = px.option(L["right"], S[k0], S[k0] * L["moneyness"], L["tenor_days"] / 365.0, self.rate[k0],
                                  _div_yield_series(self.r, L.get("proxy", L["underlying"]))[k0], out["iv"])
                    out["delta0"] = g["delta"]
                    out["cost"] = g["price"] / abs(g["delta"] * S[k0]) if g["delta"] else None
        return out


def _flag(state, value) -> Optional[float]:
    return None if state is None else (1.0 if state == value else 0.0)


def _ratio(a, b) -> Optional[float]:
    return (a / b) if a is not None and b else None


def _rv(xs, k: int, w: int) -> Optional[float]:
    """Annualised realised volatility of a daily series over (k−w, k]."""
    seg = [x for x in xs[max(1, k - w + 1):k + 1] if x is not None]
    if len(seg) < max(10, w // 2):
        return None
    return math.sqrt(252.0 * sum(x * x for x in seg) / len(seg))


def summarize(rows: List[dict], h: int) -> dict:
    n = len(rows)
    if n == 0:
        return {"n": 0, "reason": "no complete historical windows"}
    vu = sum(r["var_u"] for r in rows)
    vh = sum(r["var_h"] for r in rows)
    realized = 1 - vh / vu if vu > 0 else None
    exp = [r["expected_reduction"] for r in rows if r["expected_reduction"] is not None]
    expected = sum(exp) / len(exp) if exp else None
    n_eff = n * min(1.0, STEP / max(h, 1))
    base = {}
    for lab in ("fixed_25", "fixed_50", "min_variance"):
        vs = [r["baselines"].get(lab) for r in rows]
        if all(v is not None for v in vs):
            base[lab] = 1 - sum(vs) / vu if vu > 0 else None
    base["no_hedge"] = 0.0
    base["static_rule"] = realized
    # tail: the worst 10% of unhedged windows, and what the hedge did in them
    worst = sorted(rows, key=lambda r: r["U"])[:max(1, n // 10)]
    tail_u = sum(r["U"] for r in worst) / len(worst)
    tail_h = sum(r["U"] + r["H"] for r in worst) / len(worst)
    ups = [r for r in rows if r["U"] > 0]
    by_regime: Dict[str, dict] = {}
    for dim in ("market", "volatility", "rates"):
        for r in rows:
            s = r["regime"].get(dim)
            if not s:
                continue
            b = by_regime.setdefault(s, {"var_u": 0.0, "var_h": 0.0, "n": 0})
            b["var_u"] += r["var_u"]; b["var_h"] += r["var_h"]; b["n"] += 1
    for s, b in by_regime.items():
        b["realized_reduction"] = 1 - b["var_h"] / b["var_u"] if b["var_u"] > 0 else None
        b["n_eff"] = b["n"] * min(1.0, STEP / max(h, 1))
    third = max(1, n // 3)
    def red(rs):
        a = sum(r["var_u"] for r in rs)
        return 1 - sum(r["var_h"] for r in rs) / a if a > 0 else None
    eff = (realized / expected) if (realized is not None and expected and expected > 0.02) else None
    worked = sorted(rows, key=lambda r: (r["U"] + r["H"]) - r["U"], reverse=True)
    return {"n": n, "n_eff": n_eff, "first": rows[0]["date"], "last": rows[-1]["date"], "realized_reduction": realized,
            "expected_reduction": expected, "effectiveness": eff, "baselines": base,
            "tail": {"unhedged": tail_u, "hedged": tail_h, "reduction": (1 - tail_h / tail_u) if tail_u < 0 else None, "windows": len(worst)},
            "drawdown": {"unhedged": sum(r["dd_u"] for r in rows) / n, "hedged": sum(r["dd_h"] for r in rows) / n},
            "hedge_pnl_mean": sum(r["H"] for r in rows) / n,
            "upside_sacrificed": (sum(-min(0.0, r["H"]) for r in ups) / len(ups)) if ups else None,
            "residual_daily": math.sqrt(sum(r["var_h"] for r in rows) / (n * h)),          # realised basis/residual risk, $ per day
            "unhedged_daily": math.sqrt(vu / (n * h)),
            "by_regime": by_regime, "recent_third": red(rows[-third:]), "early_third": red(rows[:third]),
            "best": [{"date": r["date"], "end": r["end"], "position": r["U"], "hedged": r["U"] + r["H"]} for r in worked[:3]],
            "worst": [{"date": r["date"], "end": r["end"], "position": r["U"], "hedged": r["U"] + r["H"]} for r in worked[-3:]],
            "rows": rows}


# ------------------------------------------------------------------ the ML adjustment
def ml_layer(rows_by_group: Dict[str, List[dict]], alpha: float = ALPHA, cap: float = ADJ_CAP) -> dict:
    """Walk-forward ridge model of log(ex-post optimal ratio / raw ratio) from conditions at t, per group of
    (objective kind, leg kind, horizon). Evaluated on windows it never trained on (expanding training, purged by h):
    the adjusted hedge's realised variance against the static rule and the minimum-variance ratio. `verified` only
    when it beats both with paired t ≥ 2, n_eff ≥ 30 and a non-negative improvement in the most recent third."""
    from ..engine import models as M
    out = {}
    feats = ["vix", "corr_252", "corr_63", "beta_change", "mkt_3m", "rate"]
    for g, rows in rows_by_group.items():
        rows = sorted([r for r in rows if r.get("realized_opt_ratio") is not None and r["realized_opt_ratio"] > -5], key=lambda r: r["date"])
        h = rows[0]["h"] if rows else 21
        X = [[(r["features"].get(k) if r["features"].get(k) is not None else 0.0) for k in feats] for r in rows]
        # learn the error in the hedge's BETA, not the target: the ex-post full-hedge multiple × the target share is 1
        # when the sizing was exactly right
        y = [max(-1.0, min(1.0, math.log(max(0.05, r["realized_opt_ratio"] * r.get("ratio", 1.0))))) for r in rows]
        preds = [None] * len(rows)
        start = max(MIN_WINDOWS, len(rows) // 3)
        for j in range(start, len(rows)):
            tr = [i for i in range(j) if rows[i]["end"] < rows[j]["date"]]        # purged: outcomes known before j
            if len(tr) < MIN_WINDOWS:
                continue
            mod = M.make_model("ridge")
            try:
                mod.fit([X[i] for i in tr], [y[i] for i in tr])
                preds[j] = mod.predict([X[j]])[0]
            except Exception:
                preds[j] = None
        test = [j for j in range(len(rows)) if preds[j] is not None]
        if len(test) < MIN_WINDOWS:
            out[g] = {"verified": False, "reason": f"only {len(test)} out-of-sample windows", "n": len(test)}
            continue
        d_static, d_mv = [], []
        for j in test:
            r = rows[j]
            adj = alpha * max(-cap, min(cap, math.exp(preds[j]) - 1))
            u_var, h_var = r["var_u"], r["var_h"]
            # hedged variance with the leg scaled by (1 + adj): Σ(u + (1+adj)h)² from the stored sums
            su, sh, suh = r["var_u"], r.get("var_leg"), r.get("cov_uh")
            if sh is None or suh is None:
                continue
            v_adj = su + 2 * (1 + adj) * suh + (1 + adj) ** 2 * sh
            d_static.append(h_var - v_adj)
            mvb = r["baselines"].get("min_variance")
            if mvb is not None:
                d_mv.append(mvb - v_adj)
        def tstat(xs):
            if len(xs) < 3:
                return None
            m = sum(xs) / len(xs)
            sd = math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))
            n_eff = len(xs) * min(1.0, STEP / max(h, 1))
            return m / (sd / math.sqrt(n_eff)) if sd > 0 else None
        t1, t2 = tstat(d_static), tstat(d_mv)
        n_eff = len(d_static) * min(1.0, STEP / max(h, 1))
        third = d_static[-max(1, len(d_static) // 3):]
        reasons = []
        if n_eff < ML_MIN_NEFF:
            reasons.append(f"n_eff {n_eff:.0f} < {ML_MIN_NEFF}")
        if t1 is None or t1 < 2:
            reasons.append(f"does not beat the static rule (t {t1:.1f})" if t1 is not None else "no comparison with the static rule")
        if d_mv and (t2 is None or t2 < 2):
            reasons.append(f"does not beat the minimum-variance ratio (t {t2:.1f})" if t2 is not None else "no comparison with minimum variance")
        if third and sum(third) < 0:
            reasons.append("decayed: worse than the static rule in the most recent third")
        mod = M.make_model("ridge")
        try:
            mod.fit(X, y)
            state = M.get_state(mod)
        except Exception:
            state = None
        out[g] = {"verified": not reasons, "reasons": reasons, "n": len(test), "n_eff": n_eff, "t_vs_static": t1, "t_vs_min_variance": t2,
                  "mean_gain_vs_static": (sum(d_static) / len(d_static)) if d_static else None, "features": feats, "model": state,
                  "alpha": alpha, "cap": cap}
    return out


def ml_adjustment(model: Optional[dict], features: dict) -> dict:
    """Today's capped adjustment from a verified group model; 0 otherwise."""
    if not model or not model.get("verified") or not model.get("model"):
        return {"adjustment": 0.0, "verified": False, "reasons": (model or {}).get("reasons") or ["no model for this hedge"]}
    from ..engine import models as M
    mod = M.from_state(model["model"])
    x = [(features.get(k) if features.get(k) is not None else 0.0) for k in model["features"]]
    raw = mod.predict([x])[0]
    adj = max(-model["cap"], min(model["cap"], math.exp(raw) - 1))
    return {"adjustment": adj, "applied": model["alpha"] * adj, "verified": True, "reasons": [], "raw_prediction": raw}
