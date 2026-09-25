"""Shaffer Hedge: identify the risk, set the target, find products that carry that risk, size each in its own risk
unit, optimise the package, score it, then (only if verified) apply a capped ML adjustment.

    R_after = R_before + B q                                     (B: exposures per unit of each hedge product)
    min_q  (γh/2)·(R + Bq − R*)ᵀ Σ (R + Bq − R*) + Σ_j c_j|q_j| + Σ_j k_j|q_j|^1.5
           risk mismatch (incl. every hedge's own residual,    cost over the     market impact (square-root law,
           i.e. basis risk, via its IDIO factor)               horizon           the liquidity penalty)
    γ = A / NAV (A = relative risk aversion, default 2), h = horizon in sessions, Σ = daily factor covariance.
    Factors the objective does not target keep R*_f = R_f, so a hedge that adds sector or currency exposure is penalised.
    Turnover: existing hedges enter R, so the optimiser only adds what is missing.

    SH_j = 100·tanh(E_j·Q_j·L_j·R_j·B_j·T_j / 1.0)
    E: realised ÷ expected variance reduction in the walk-forward history (−1…1.25); expected reduction if no history
    Q: value of the risk removed ÷ (value + expected cost); value = (γh/2)·ΔVar
    L: 1 / (1 + participation/10%) with participation = hedge notional ÷ average daily traded value
    R: realised reduction in windows that started in today's regime ÷ all windows, shrunk by n/(n+20), in [0.5, 1.5]
    B: share of the target risk the product can remove at all: ρ² of the product with the targeted P&L
    T: tail suitability (crash objective): hedge gain in the −20% scenario ÷ the gain of a linear hedge with the same
       delta, in [0.5, 1.5]; 1 for other objectives"""
from __future__ import annotations

import math
import random
from typing import Dict, List, Optional, Tuple

from . import pricing as px
from . import products as P
from .history import ALPHA, ADJ_CAP, Evaluator, ml_adjustment
from .market import SHORT_RATE, Market
from .risk import RiskModel, label as flabel, unit as funit

K_H = 1.0
RISK_AVERSION = 2.0
TARGET_WEIGHT = 1.0e4     # continuous sizing: the user's target is effectively binding
SELECT_WEIGHT = 50.0      # choosing products and whole lots: a lot-granularity miss must not outweigh large cost differences
VARIANCE_OBJECTIVES = {"min_variance", "var", "es", "drawdown", "target_vol"}
FACTOR_BY_FACTOR = {"systematic", "sector", "name", "fx", "commodity", "credit"}
HORIZON = {"1D": 1, "1W": 5, "1M": 21, "3M": 63, "6M": 126, "12M": 252}
OBJECTIVES = {
    "systematic": "Hedge the systematic risk (every factor except single-name residual)",
    "beta": "Reduce market beta", "neutral": "Neutralize market beta", "sector": "Reduce sector / industry exposure",
    "name": "Hedge a specific position (single-name risk)", "duration": "Reduce duration (rates)", "curve": "Hedge the yield curve key rate by key rate",
    "credit": "Reduce credit-spread risk", "fx": "Reduce currency exposure", "commodity": "Reduce commodity exposure",
    "crypto": "Reduce crypto exposure", "volatility": "Reduce volatility (vega) exposure", "crash": "Protect against a market crash",
    "var": "Reduce Value at Risk", "es": "Reduce Expected Shortfall", "drawdown": "Reduce drawdown risk", "min_variance": "Minimise variance",
    "target_vol": "Reach a target volatility",
}
SECTOR_HEDGES = {"SEC:XLK": ["XLK", "QQQ", "FUT:NQ", "FUT:MNQ"], "SEC:XLF": ["XLF", "KRE"], "SEC:XLE": ["XLE", "USO"], "SEC:XLV": ["XLV"], "SEC:XLY": ["XLY"],
                 "SEC:XLP": ["XLP"], "SEC:XLI": ["XLI"], "SEC:XLB": ["XLB"], "SEC:XLU": ["XLU"], "SEC:XLRE": ["XLRE", "VNQ"], "SEC:XLC": ["XLC"],
                 "IND:SMH": ["SMH"], "STY:SIZE": ["IWM", "FUT:RTY", "FUT:M2K"], "STY:VALUE": ["IWD", "IWF"]}
RATE_HEDGES = {"RATE:2Y": ["FUT:ZT", "SHY"], "RATE:5Y": ["FUT:ZF", "IEI"], "RATE:10Y": ["FUT:ZN", "FUT:TN", "IEF"], "RATE:30Y": ["FUT:ZB", "FUT:UB", "TLT"],
               "REAL:10Y": ["TIP", "SCHP", "FUT:ZN"]}
CREDIT_HEDGES = {"CREDIT:HY": ["HYG", "JNK", "BKLN"], "CREDIT:IG": ["LQD", "VCIT"]}
FX_HEDGES = {"EUR": ["FWD:EURUSD", "FUT:6E", "FXE", "EURUSD"], "JPY": ["FWD:USDJPY", "FUT:6J", "FXY", "USDJPY"], "GBP": ["FWD:GBPUSD", "FUT:6B", "FXB", "GBPUSD"],
             "AUD": ["FWD:AUDUSD", "FUT:6A", "AUDUSD"], "CAD": ["FWD:USDCAD", "FUT:6C", "USDCAD"], "CHF": ["FWD:USDCHF", "FUT:6S", "USDCHF"],
             "INR": ["USDINR"], "CNY": ["USDCNY"], "MXN": ["USDMXN"], "NZD": ["NZDUSD"]}
COMMODITY_HEDGES = {"CMD:OIL": ["USO", "FUT:CL", "XLE"], "CMD:GAS": ["UNG", "FUT:NG"], "CMD:GOLD": ["GLD", "FUT:GC"], "CMD:SILVER": ["SLV", "FUT:SI"],
                    "CMD:COPPER": ["CPER", "FUT:HG"], "CMD:AGRI": ["DBA"], "CMD:BROAD": ["DBC"]}
CRYPTO_HEDGES = ["IBIT", "BITO", "FUT:BTC", "BTC"]
MARKET_HEDGES = ["SPY", "FUT:ES", "FUT:MES", "SH", "OPT:SPY", "OPT:SPX", "QQQ", "FUT:NQ", "FUT:MNQ", "OPT:QQQ", "IWM", "FUT:RTY", "FUT:M2K",
                 "FUT:YM", "FUT:MYM", "VXX"]
VOL_HEDGES = ["VXX", "OPT:SPY", "OPT:SPX"]


# ------------------------------------------------------------------ positions and the risk vector
def price_positions(positions: List[dict], m: Market, rk: RiskModel, store) -> List[dict]:
    out = []
    for p in positions:
        q = float(p.get("quantity") or 0.0)
        if not q:
            continue
        try:
            inst = P.parse(p["id"], store)
        except ValueError as e:
            out.append({"id": p["id"], "quantity": q, "error": str(e)})
            continue
        pr = P.Priced(inst, m, rk)
        e = pr.exposures()
        uv = pr.unit_value()
        entry = p.get("entry")                 # linear derivatives: value = q·mult·(price − entry)
        if inst.type in ("FUTURE", "FORWARD") and entry is not None and pr.price is not None:
            unit = inst.spec["face"] / 100.0 if inst.type == "FUTURE" and inst.spec.get("kind") == "treasury" else inst.multiplier if inst.type == "FUTURE" and inst.spec.get("kind") != "fx" else (inst.spec.get("size") if inst.type == "FUTURE" else 1.0)
            fx = m.fx(inst.currency) or 1.0
            mv = q * unit * (pr.price - entry) * fx
        else:
            mv = q * uv if uv is not None else None
        out.append({"id": p["id"], "quantity": q, "inst": inst, "priced": pr, "market_value": mv, "notional": (q * (pr.unit_notional() or 0.0)),
                    "exposures": {f: q * v for f, v in e.items()}, "name": inst.name, "type": inst.type,
                    "greeks": ({k: (v * q if k not in ("delta", "gamma", "iv") else v) for k, v in pr.greeks_per_unit().items()} if pr.greeks_per_unit() else None),
                    "error": None if (pr.price is not None and e) else ("; ".join(pr.reasons) or "no risk model")})
    return out


def risk_vector(priced: List[dict]) -> Dict[str, float]:
    R: Dict[str, float] = {}
    for p in priced:
        for f, v in (p.get("exposures") or {}).items():
            R[f] = R.get(f, 0.0) + v
    return R


def _var(R: Dict[str, float], C) -> float:
    fs = [f for f in R if R[f]]
    return max(0.0, sum(R[a] * R[b] * C.get((a, b), 0.0) for a in fs for b in fs))


def contributions(R: Dict[str, float], C) -> Dict[str, float]:
    """Euler contributions to variance: R_f (ΣR)_f / Var."""
    fs = [f for f in R if R[f]]
    v = _var(R, C)
    if v <= 0:
        return {}
    return {a: R[a] * sum(C.get((a, b), 0.0) * R[b] for b in fs) / v for a in fs}


def greeks_total(priced: List[dict]) -> dict:
    t = {"delta_usd": 0.0, "gamma_usd_1pct": 0.0, "vega_usd": 0.0, "theta_usd": 0.0, "rho_usd": 0.0}
    for p in priced:
        g = p.get("greeks")
        if g:
            for k in t:
                t[k] += g.get(k) or 0.0
    return t


def summary_metrics(R: Dict[str, float], C, nav: float, priced: List[dict]) -> dict:
    """Beta, DV01 by key rate, CS01, currency and commodity $, crypto, vega, daily σ and the largest factor's share."""
    dv = {f: -v for f, v in R.items() if f.startswith("RATE:")}
    cs = {f: -v for f, v in R.items() if f.startswith("CREDIT:")}
    sig = math.sqrt(_var(R, C))
    contrib = contributions(R, C)
    top = max(contrib.items(), key=lambda kv: abs(kv[1])) if contrib else (None, None)
    g = greeks_total(priced)
    return {"beta_usd": R.get("MKT", 0.0), "beta": (R.get("MKT", 0.0) / nav) if nav else None, "dv01": sum(dv.values()), "krd": dv,
            "real_dv01": -R.get("REAL:10Y", 0.0), "cs01": sum(cs.values()), "cs01_by": cs,
            "fx": {f[3:]: v for f, v in R.items() if f.startswith("FX:")}, "commodity": {f[4:]: v for f, v in R.items() if f.startswith("CMD:")},
            "crypto": R.get("CRYPTO", 0.0), "vol_usd_per_vix": R.get("VOL", 0.0), "sigma_daily": sig, "sigma_annual": sig * math.sqrt(252),
            "sigma_annual_pct": (sig * math.sqrt(252) / nav) if nav else None, "largest_factor": top[0], "largest_share": top[1],
            "greeks": g}


# ------------------------------------------------------------------ objectives
def position_exposures(priced: List[dict], asset: Optional[str]) -> Dict[str, float]:
    out: Dict[str, float] = {}
    for p in priced:
        inst = p.get("inst")
        if inst is None or asset not in (inst.id, inst.underlying):
            continue
        for f, v in (p.get("exposures") or {}).items():
            out[f] = out.get(f, 0.0) + v
    return out


def targeted(objective: str, R: Dict[str, float], params: dict, priced: Optional[List[dict]] = None) -> List[str]:
    fs = [f for f in R if abs(R[f]) > 1e-9]
    if objective in ("beta", "neutral", "crash"):
        return ["MKT"]
    if objective == "systematic":
        # style tilts (size, value) are secondary and noisily estimated: they stay in the "keep small" part of the
        # objective instead of being pinned, so a style hedge never outweighs the trade itself
        return [f for f in fs if not f.startswith(("IDIO:", "STY:"))]
    if objective == "sector":
        want = params.get("factor")
        return [want] if want else [f for f in fs if f.startswith(("SEC:", "IND:"))]
    if objective == "name":
        a = params.get("asset") or next((f[5:] for f in sorted(fs, key=lambda f: -abs(R[f])) if f.startswith("IDIO:")), None)
        params["asset"] = a
        pe = position_exposures(priced or [], a)
        return [f for f in pe if abs(pe[f]) > 1e-9]
    if objective in ("duration", "curve"):
        return [f for f in fs if f.startswith(("RATE:", "REAL:"))]
    if objective == "credit":
        return [f for f in fs if f.startswith("CREDIT:")]
    if objective == "fx":
        c = params.get("currency")
        return [f"FX:{c}"] if c else [f for f in fs if f.startswith("FX:")]
    if objective == "commodity":
        c = params.get("factor")
        return [c] if c else [f for f in fs if f.startswith("CMD:")]
    if objective == "crypto":
        return ["CRYPTO"]
    if objective == "volatility":
        return ["VOL"]
    return fs                                     # var, es, drawdown, min_variance, target_vol: every factor


def target_vector(objective: str, R: Dict[str, float], S: List[str], params: dict, C, nav: float,
                  priced: Optional[List[dict]] = None) -> Dict[str, float]:
    Rs = dict(R)
    p = params.get("reduction")
    if objective == "neutral":
        p = 1.0
    if objective == "min_variance":
        return {f: (0.0 if f in S else R[f]) for f in R}
    if objective == "name":
        pe = position_exposures(priced or [], params.get("asset"))
        pp = 0.5 if p is None else max(0.0, min(1.0, float(p)))
        for f in S:
            Rs[f] = R.get(f, 0.0) - pp * pe.get(f, 0.0)
        return Rs
    if objective == "beta" and params.get("target_beta") is not None and nav:
        Rs["MKT"] = float(params["target_beta"]) * nav
        return Rs
    if objective == "target_vol" and params.get("target_vol") and nav:
        cur = math.sqrt(_var(R, C) * 252) / nav
        k = min(1.0, float(params["target_vol"]) / cur) if cur > 0 else 1.0
        for f in S:
            Rs[f] = R[f] * k
        return Rs
    if params.get("targets"):
        for f, v in params["targets"].items():
            Rs[f] = float(v)
        return Rs
    p = 0.5 if p is None else max(0.0, min(1.0, float(p)))
    for f in S:
        Rs[f] = R.get(f, 0.0) * (1 - p)
    return Rs


def auto_objective(R: Dict[str, float], C) -> Tuple[str, dict]:
    """The objective for the risk that contributes most to variance."""
    c = contributions(R, C)
    if not c:
        return "beta", {}
    groups: Dict[str, float] = {}
    for f, v in c.items():
        g = ("beta" if f == "MKT" else "sector" if f.startswith(("SEC:", "IND:", "STY:")) else "name" if f.startswith("IDIO:")
             else "duration" if f.startswith(("RATE:", "REAL:")) else "credit" if f.startswith("CREDIT:") else "fx" if f.startswith("FX:")
             else "commodity" if f.startswith("CMD:") else "crypto" if f == "CRYPTO" else "volatility")
        groups[g] = groups.get(g, 0.0) + v
    g = max(groups.items(), key=lambda kv: kv[1])[0]
    if g == "name":
        top = max(((f, v) for f, v in c.items() if f.startswith("IDIO:")), key=lambda kv: kv[1])[0]
        return "name", {"asset": top[5:]}
    return g, {}


# ------------------------------------------------------------------ candidates (risk first: products that carry the risk)
def candidate_ids(objective: str, S: List[str], dR: Dict[str, float], m: Market, params: dict, positions: List[dict]) -> List[str]:
    ids: List[str] = []
    fut_eq = px.future_expiries("equity", m.asof, 1)[0][0]
    fut_tr = px.future_expiries("treasury", m.asof, 1)[0][0]
    hdays = HORIZON.get(params.get("horizon", "1M"), 21)
    exps = px.monthly_option_expiries(m.asof, 14)
    import datetime as _dt
    min_days = max(30, int(hdays * 365 / 252) + 7)
    exp = next((e for e in exps if (_dt.date.fromisoformat(e) - _dt.date.fromisoformat(m.asof)).days >= min_days), exps[-1])
    fwd_date = (_dt.date.fromisoformat(m.asof) + _dt.timedelta(days=max(31, int(hdays * 365 / 252) + 7))).isoformat()

    def expand(sym: str, need_up: bool):
        if sym.startswith("FUT:"):
            root = sym[4:]
            spec = P.FUTURES.get(root, {})
            code = fut_tr if spec.get("kind") == "treasury" else fut_eq
            return [P.future_id(root, code)]
        if sym.startswith("FWD:"):
            return [P.forward_id(sym[4:], fwd_date)]
        if sym.startswith("OPT:"):
            und = sym[4:]
            S0 = m.price(und)
            if not S0:
                return [P.option_id(und, "C" if need_up else "P", 0, exp)]
            inc = px.strike_increment(S0, und in P.INDEX_OPTION_UNDERLYINGS)
            right = "C" if need_up else "P"
            mny = [1.0, 1.05] if need_up else [1.0, 0.95]
            if objective == "crash" and not need_up:
                mny = [0.95, 0.90]
            return [P.option_id(und, right, px.strike_for(S0, x, inc), exp) for x in mny]
        return [sym]
    for f in S:
        up = dR.get(f, 0.0) > 0                        # the hedge must ADD exposure (e.g. a short book needs longs)
        if f == "MKT":
            base = MARKET_HEDGES
        elif f in SECTOR_HEDGES:
            base = SECTOR_HEDGES[f] + (["QQQ", "FUT:NQ", "OPT:QQQ"] if f in ("SEC:XLK", "IND:SMH") else [])
        elif f.startswith("IDIO:"):
            a = f[5:]
            base = [a, f"OPT:{a}"]
        elif f in RATE_HEDGES:
            base = RATE_HEDGES[f]
        elif f in CREDIT_HEDGES:
            base = CREDIT_HEDGES[f] + ["FUT:ZF"]
        elif f.startswith("FX:"):
            base = FX_HEDGES.get(f[3:], [])
        elif f in COMMODITY_HEDGES:
            base = COMMODITY_HEDGES[f] + (["OPT:USO"] if f == "CMD:OIL" else ["OPT:GLD"] if f == "CMD:GOLD" else [])
        elif f == "CRYPTO":
            base = CRYPTO_HEDGES
        elif f == "VOL":
            base = VOL_HEDGES
        else:
            base = []
        if objective == "crash" and "VXX" not in base:
            base = base + ["VXX"]
        for s in base:
            if s.startswith("OPT:") and objective == "volatility":
                up_opt = dR.get("VOL", 0.0) > 0
                for x in expand(s, True) + expand(s, False):
                    ids.append(x)
                continue
            for x in expand(s, up):
                ids.append(x)
    for c in params.get("candidates") or []:
        ids.append(c)
    held = {p["id"] for p in positions if float(p.get("quantity") or 0) != 0}
    seen, out = set(), []
    for x in ids:
        if x in held and objective != "name":
            continue                                  # selling what you hold is not a hedge (except for a single name)
        if x not in seen:
            seen.add(x); out.append(x)
    return out


# ------------------------------------------------------------------ the optimiser
def _wblock(C, S, curve=False, w_s: Optional[float] = None) -> Dict[tuple, float]:
    w_s = TARGET_WEIGHT if w_s is None else w_s
    """The objective's weight matrix: the covariance within the targeted factors and within the others, none across,
    so targeted exposures are pinned at their targets while every other exposure is kept as small as possible.
    `curve`: targeted factors matched one by one (no cross-covariance between them) — key rates for the curve
    objective, and market / sector / industry / currency / commodity / credit factors for multi-factor objectives."""
    Sset = set(S)
    out = {}
    for (a, b), v in C.items():
        if (a in Sset) != (b in Sset):
            continue
        if curve and a != b and a in Sset and b in Sset:
            continue                              # targeted factors matched one by one (key rates, sector and market, …)
        out[(a, b)] = v * (w_s if a in Sset else 1.0)
    return out


def optimise(R: Dict[str, float], Rstar: Dict[str, float], legs: List[dict], C, gamma_h: float, curve_diag: bool = False,
             iters: int = 200, S: Optional[List[str]] = None, w_s: Optional[float] = None) -> List[float]:
    """Coordinate descent on J(q) = (γh/2)·dᵀWd + Σ c_j|q_j| + Σ k_j|q_j|^1.5 with d = R + Bq − R*.
    Legs may be sign-constrained (options are only bought)."""
    fs = sorted(set(R) | set(Rstar) | {f for L in legs for f in L["H"]})
    Sset = set(S if S is not None else fs)
    Wb = _wblock(C, list(Sset), curve_diag, w_s)
    W = {(a, b): Wb.get((a, b), 0.0) for a in fs for b in fs}
    d = {f: R.get(f, 0.0) - (Rstar.get(f, 0.0) if f in Sset else 0.0) for f in fs}
    q = [0.0] * len(legs)
    Hs = [L["H"] for L in legs]
    WH = [{a: sum(W[(a, b)] * H.get(b, 0.0) for b in fs) for a in fs} for H in Hs]
    for _ in range(iters):
        moved = 0.0
        for j, L in enumerate(legs):
            H = Hs[j]
            # d without leg j
            dj = {f: d[f] - q[j] * H.get(f, 0.0) for f in fs}
            a = 0.5 * gamma_h * sum(H.get(f, 0.0) * WH[j][f] for f in fs)
            b = gamma_h * sum(dj[f] * WH[j][f] for f in fs)
            c, k = L["c"], L["k"]
            if a <= 0:
                continue

            def J(x):
                return a * x * x + b * x + c * abs(x) + k * abs(x) ** 1.5
            lo_sign = L.get("sign")
            best = 0.0
            for sgn in (1.0, -1.0):
                if lo_sign and sgn != lo_sign:
                    continue
                # minimise over x = sgn·t, t ≥ 0: derivative 2a t + sgn·b + c + 1.5 k t^0.5 = 0 (increasing in t)
                g0 = sgn * b + c
                if g0 >= 0:
                    continue
                lo_t, hi_t = 0.0, max(1.0, -g0 / (2 * a) * 2)
                for _ in range(80):
                    mid = (lo_t + hi_t) / 2
                    gm = 2 * a * mid + sgn * b + c + 1.5 * k * math.sqrt(mid)
                    if gm > 0:
                        hi_t = mid
                    else:
                        lo_t = mid
                x = sgn * (lo_t + hi_t) / 2
                if J(x) < J(best):
                    best = x
            if L.get("bound"):
                lo_b, hi_b = L["bound"]
                best = min(hi_b, max(lo_b, best))
            if best != q[j]:
                moved = max(moved, abs(best - q[j]) / (abs(q[j]) + 1.0))
                for f in fs:
                    d[f] += (best - q[j]) * H.get(f, 0.0)
                q[j] = best
        if moved < 1e-7:
            break
    return q


def _opt_leg(c: dict, h: int, C) -> dict:
    pr, inst = c["priced"], c["inst"]
    cu = pr.costs(h, "BUY").get("total") or 0.0
    cs = pr.costs(h, "SELL").get("total") or 0.0
    adv = (c.get("liquidity") or {}).get("adv_usd")
    sig_d = math.sqrt(max(_var(c["H"], C), 0.0))
    k = (0.5 * sig_d * math.sqrt(abs(pr.unit_notional() or 0.0) / adv)) if adv else 0.0
    return {"cu": cu, "cs": cs, "c": max(0.0, (cu + cs) / 2), "k": k, "H": c["H"], "sign": 1.0 if inst.type == "OPTION" else None,
            "bound": c.get("bound")}


def objective_value(R, Rstar, legs: List[dict], q: List[float], C, gamma_h: float, curve: bool = False,
                    S: Optional[List[str]] = None, w_s: Optional[float] = None) -> float:
    """J(q) with the actual side-dependent cost of each leg (buying and selling can cost differently)."""
    Sset = set(S if S is not None else (set(R) | set(Rstar)))
    d = {f: R.get(f, 0.0) - (Rstar.get(f, 0.0) if f in Sset else 0.0) for f in set(R) | set(Rstar)}
    for L, x in zip(legs, q):
        for f, v in L["H"].items():
            d[f] = d.get(f, 0.0) + x * v
    fs = [f for f in d if d[f]]
    W = _wblock(C, list(Sset), curve, SELECT_WEIGHT if w_s is None else w_s)
    risk = 0.0
    for a in fs:
        for b in fs:
            risk += d[a] * d[b] * W.get((a, b), 0.0)
    cost = sum(abs(x) * (L["cu"] if x > 0 else L["cs"]) + L["k"] * abs(x) ** 1.5 for L, x in zip(legs, q))
    return 0.5 * gamma_h * risk + cost


def select_package(R, Rstar, pool, legs_all, C, gamma_h, max_legs, curve=False, exact=False, S=None, w_opt=None, w_sel=None,
                   must_hedge: bool = True):
    """Forward stepwise: add the product whose optimised inclusion lowers J the most; stop at `max_legs` or when the
    next leg improves J by less than 1%. Then round each leg to whole lots, searching the neighbouring integers.
    exact=True: the single best product sized by its unit rule (no cost trade-off)."""
    if not pool:
        return [], []
    if exact:
        c = pool[0]
        return [c], [c["unit_quantity"]]
    chosen: List[dict] = []
    q: List[float] = []
    J0 = objective_value(R, Rstar, [], [], C, gamma_h, curve, S, w_sel)
    cur = J0
    while len(chosen) < max_legs:
        best = None
        for c in pool:
            if c in chosen:
                continue
            trial = chosen + [c]
            legs = [legs_all[x["id"]] for x in trial]
            qs = optimise(R, Rstar, legs, C, gamma_h, curve_diag=curve, S=S, w_s=w_opt)
            qs = round_lots(R, Rstar, trial, legs, qs, C, gamma_h, curve, S, w_sel)
            if not qs[-1]:
                continue
            Jt = objective_value(R, Rstar, legs, qs, C, gamma_h, curve, S, w_sel)
            if best is None or Jt < best[0]:
                best = (Jt, c, qs)
        first_forced = must_hedge and not chosen and best is not None     # an explicit target always gets its best product
        if best is None or (not first_forced and (best[0] >= cur or (chosen and cur - best[0] < 0.02 * abs(J0)))):
            break
        cur = best[0]
        chosen.append(best[1])
        q = best[2]
    return chosen, q


def round_lots(R, Rstar, trial, legs, qs, C, gamma_h, curve, S=None, w_sel=None):
    """Whole contracts/shares: for each leg try floor and ceiling (coordinate-wise, twice) and keep the lower J."""
    out = [lot(c["inst"], x) for c, x in zip(trial, qs)]
    for _ in range(2):
        for j, c in enumerate(trial):
            base = qs[j]
            opts = {lot(c["inst"], base), math.floor(base), math.ceil(base)} if c["inst"].type != "FORWARD" else {lot(c["inst"], base)}
            if legs[j].get("sign") == 1.0:
                opts = {o for o in opts if o >= 0}
            bestv, bestJ = out[j], None
            for o in opts:
                t = list(out)
                t[j] = float(o)
                Jt = objective_value(R, Rstar, legs, t, C, gamma_h, curve, S, w_sel)
                if bestJ is None or Jt < bestJ:
                    bestv, bestJ = float(o), Jt
            out[j] = bestv
    return out


def lot(inst: P.Instrument, q: float) -> float:
    """Round to a tradeable quantity: whole contracts and shares; forwards in 1,000 units of currency."""
    if inst.type == "FORWARD":
        return round(q / 1000.0) * 1000.0
    if inst.type == "SPOT":
        a = inst.underlying
        return round(q, 4) if a in ("BTC", "ETH", "SOL") else float(round(q))
    return float(round(q))


# ------------------------------------------------------------------ scenarios
SCENARIOS = [
    {"key": "up10", "label": "Market +10%", "mkt": 0.10}, {"key": "up5", "label": "Market +5%", "mkt": 0.05},
    {"key": "flat", "label": "Unchanged (after the horizon: time decay)", "mkt": 0.0, "decay": True},
    {"key": "dn5", "label": "Market −5%", "mkt": -0.05}, {"key": "dn10", "label": "Market −10%", "mkt": -0.10},
    {"key": "dn20", "label": "Market −20%", "mkt": -0.20}, {"key": "vol", "label": "Volatility +10 VIX points", "vix": 10.0},
    {"key": "rates", "label": "Rates +100bp (parallel)", "rates": 100.0}, {"key": "steep", "label": "Curve steepens (2Y −25bp, 30Y +50bp)", "steep": True},
    {"key": "credit", "label": "Credit widens (HY +200bp, IG +75bp)", "credit": True}, {"key": "usd", "label": "US dollar +10%", "usd": 0.10},
    {"key": "oil", "label": "Oil −30%", "oil": -0.30}, {"key": "crypto", "label": "Crypto −50%", "crypto": -0.50},
]


def _vix_response(m: Market) -> Tuple[float, float]:
    """VIX points per 100% market move on down days and on up days (5 years to i)."""
    from .risk import factor_series
    F = factor_series(m.research)
    mk, vx = F["MKT"], F["VOL"]
    lo = max(1, m.i - 1260)
    dn = [(a, b) for a, b in zip(mk[lo:m.i + 1], vx[lo:m.i + 1]) if a is not None and b is not None and a < 0]
    up = [(a, b) for a, b in zip(mk[lo:m.i + 1], vx[lo:m.i + 1]) if a is not None and b is not None and a > 0]

    def slope(pairs):
        sxx = sum(a * a for a, _ in pairs)
        return sum(a * b for a, b in pairs) / sxx if sxx > 0 else -100.0
    return slope(dn), slope(up)


def factor_shock(sc: dict, m: Market, kdn: float, kup: float) -> Dict[str, float]:
    s: Dict[str, float] = {}
    if sc.get("mkt"):
        s["MKT"] = sc["mkt"]
        s["VOL"] = (kdn if sc["mkt"] < 0 else kup) * sc["mkt"]
    if sc.get("vix"):
        s["VOL"] = sc["vix"]
    if sc.get("rates"):
        for f in ("RATE:2Y", "RATE:5Y", "RATE:10Y", "RATE:30Y"):
            s[f] = sc["rates"]
        s["REAL:10Y"] = sc["rates"] * 0.75
    if sc.get("steep"):
        s.update({"RATE:2Y": -25.0, "RATE:5Y": 0.0, "RATE:10Y": 25.0, "RATE:30Y": 50.0})
    if sc.get("credit"):
        s.update({"CREDIT:HY": 200.0, "CREDIT:IG": 75.0})
    if sc.get("usd"):
        for c in ("EUR", "JPY", "GBP", "AUD", "CAD", "CHF", "CNY", "MXN", "INR", "NZD"):
            s[f"FX:{c}"] = -sc["usd"] / (1 + sc["usd"])
    if sc.get("oil"):
        s["CMD:OIL"] = sc["oil"]
    if sc.get("crypto"):
        s["CRYPTO"] = sc["crypto"]
    return s


def scenario_pnl(priced: List[dict], shock: Dict[str, float], m: Market, rk: RiskModel, decay_days: int = 0) -> float:
    """Dollar P&L of the positions under a factor shock: linear products by their exposures; options fully re-priced
    (new underlying from its betas, new implied vol from the VIX move, less time if decaying)."""
    tot = 0.0
    vix0, _ = m.macro("VIXCLS")
    for p in priced:
        inst, pr = p.get("inst"), p.get("priced")
        if inst is None or pr is None or pr.price is None:
            continue
        if inst.type == "OPTION":
            S = pr.inputs["spot"]["value"]
            e = rk.unit_exposures(inst.underlying)["exposures"]
            ret = sum(v * shock.get(f, 0.0) for f, v in e.items() if not f.startswith("IDIO:"))
            iv = pr.inputs["implied_vol"]["value"]
            dv = shock.get("VOL", 0.0) * ((iv * 100 / vix0) if vix0 else 1.0) / 100.0
            T = max(0.0, pr._T() - decay_days / 365.0)
            new = px.option_price(inst.right, S * (1 + ret), inst.strike, T, pr.inputs["rate"]["value"], pr.inputs["dividend_yield"]["value"],
                                  max(0.01, iv + dv), "EUROPEAN" if inst.style == "EUROPEAN" else inst.style)
            tot += p["quantity"] * inst.multiplier * (new - pr.price)
        else:
            tot += sum(v * shock.get(f, 0.0) for f, v in (p.get("exposures") or {}).items())
            if decay_days and inst.type in ("FUTURE", "FORWARD"):
                inf = pr.costs(decay_days, "BUY" if p["quantity"] > 0 else "SELL").get("info") or {}
                tot -= abs(p["quantity"]) * (inf.get("carry_in_price") or inf.get("forward_points") or 0.0)
    return tot


# ------------------------------------------------------------------ historical simulation: VaR, ES, drawdown
def hist_pnl(priced: List[dict], m: Market, rk: RiskModel, days: int = 504) -> List[float]:
    from .risk import factor_series
    F = factor_series(m.research)
    out = []
    lo = max(1, m.i - days + 1)
    for d in range(lo, m.i + 1):
        tot, ok = 0.0, True
        for p in priced:
            inst, pr = p.get("inst"), p.get("priced")
            if inst is None or pr is None or pr.price is None:
                continue
            for f, v in (p.get("exposures") or {}).items():
                x = (rk._idio.get(f[5:]) or [None] * (d + 1))[d] if f.startswith("IDIO:") else (F.get(f) or [None] * (d + 1))[d]
                if x is None:
                    continue
                tot += v * x
            if inst.type == "OPTION" and p.get("greeks"):
                r = m.usd_returns(inst.underlying)[d]
                if r is not None:
                    tot += p["greeks"]["gamma_usd_1pct"] * (r * 100) ** 2
        out.append(tot)
    return out


def tail_stats(pnl: List[float], seed: int = 11) -> dict:
    if len(pnl) < 60:
        return {}
    s = sorted(pnl)
    n = len(s)
    var95, var99 = -s[int(0.05 * n)], -s[int(0.01 * n)]
    es95 = -sum(s[:max(1, int(0.05 * n))]) / max(1, int(0.05 * n))
    rnd = random.Random(seed)
    dds = []
    for _ in range(200):
        eq = peak = dd = 0.0
        for _ in range(252):
            eq += pnl[rnd.randrange(n)]
            peak = max(peak, eq)
            dd = min(dd, eq - peak)
        dds.append(dd)
    dds.sort()
    return {"var95": var95, "var99": var99, "es95": es95, "expected_drawdown_1y": dds[len(dds) // 2], "days": n}


# ------------------------------------------------------------------ the analysis
def analyze(research, positions: List[dict], objective: Optional[str] = None, params: Optional[dict] = None,
            nav: Optional[float] = None, history: bool = True, ml_models: Optional[dict] = None) -> dict:
    """Risk first: the risk vector of `positions` ([{"id", "quantity", "entry"?}]), the objective's target, every
    candidate product with its eligibility, sizing, cost, basis, liquidity, walk-forward record and score, the
    optimised Raw Shaffer Hedge package, the capped ML adjustment, scenarios and before/after risk."""
    params = dict(params or {})
    m = Market(research)
    rk = RiskModel(m)
    store = research.store
    priced = price_positions(positions, m, rk, store)
    R = risk_vector(priced)
    gross = sum(abs(p["market_value"] or 0.0) for p in priced)
    nav = nav if nav else (gross or 1.0)
    hkey = params.get("horizon", "1M")
    h = HORIZON.get(hkey, 21)
    A = float(params.get("risk_aversion", RISK_AVERSION))
    gamma_h = A / nav * h
    auto = objective in (None, "", "auto")
    fac0 = sorted(set(R))
    C0 = rk.covariance(fac0)
    if auto:
        objective, extra = auto_objective(R, C0)
        params = {**extra, **params}
    S = targeted(objective, R, params, priced)
    Rstar = target_vector(objective, R, S, params, C0, nav, priced)
    dR = {f: Rstar.get(f, 0.0) - R.get(f, 0.0) for f in S}
    ids = candidate_ids(objective, S, dR, m, params, positions)
    # every factor any candidate touches enters the covariance (their own residuals are the basis risk)
    cands: List[dict] = []
    for cid in ids:
        try:
            inst = P.parse(cid, store)
        except (ValueError, KeyError) as e:
            cands.append({"id": cid, "eligible": False, "reasons": [str(e)], "name": cid})
            continue
        pr = P.Priced(inst, m, rk)
        ok, why = pr.eligibility()
        cands.append({"id": cid, "inst": inst, "priced": pr, "eligible": ok, "reasons": why, "name": inst.name, "H": pr.exposures() if pr.price is not None else {}})
    facs = sorted(set(R) | set(Rstar) | {f for c in cands for f in c.get("H", {})})
    C = rk.covariance(facs)
    var0 = _var(R, C)
    ideal = dict(R)
    ideal.update({f: Rstar[f] for f in S})
    var_ideal = _var(ideal, C)
    prim = max(S, key=lambda f: abs(dR.get(f, 0.0)) * math.sqrt(max(C.get((f, f), 0.0), 1e-30))) if S else None
    tgt_pnl_var = _var({f: R.get(f, 0.0) for f in S}, C)
    kdn, kup = _vix_response(m)
    crash_shock = factor_shock({"mkt": -0.20}, m, kdn, kup)
    for c in cands:
        if not c.get("eligible"):
            c["status"] = P.NOT_ELIGIBLE
            continue
        inst, pr, H = c["inst"], c["priced"], c["H"]
        c["sizing_rule"], c["risk_unit"] = pr.sizing_rule(), pr.risk_unit()
        HS = {f: H.get(f, 0.0) for f in S if H.get(f)}
        if not HS:
            c.update({"eligible": False, "status": "NOT RELEVANT", "reasons": [f"carries none of the targeted risk ({', '.join(flabel(f) for f in S[:3])})"]})
            continue
        # the product's own risk unit: the quantity whose exposure best matches the required change on the targeted
        # factors (a covariance-weighted projection; with one factor it is Δexposure ÷ exposure per unit)
        WS = _wblock(C, S, objective == "curve" or (objective in FACTOR_BY_FACTOR and len(S) > 1))
        num = sum(HS.get(a, 0.0) * WS.get((a, b), 0.0) * dR.get(b, 0.0) for a in S for b in S)
        den = sum(HS.get(a, 0.0) * WS.get((a, b), 0.0) * HS.get(b, 0.0) for a in S for b in S)
        if den <= 0:
            c.update({"eligible": False, "status": "NOT RELEVANT", "reasons": ["no measurable targeted risk"]})
            continue
        q = num / den
        if objective == "crash":
            q = _crash_units(c, R, crash_shock, m, rk, params)
            if q is None:
                c.update({"eligible": False, "status": "WRONG DIRECTION", "reasons": ["gains nothing in a market crash"]})
                continue
        if inst.type == "OPTION" and q < 0:
            c.update({"eligible": False, "status": "WRONG DIRECTION", "reasons": ["options are only bought: this right moves the exposure the wrong way"]})
            continue
        held_q = sum(p["quantity"] for p in priced if p.get("inst") is not None and p["inst"].id == inst.id)
        if held_q:
            c["bound"] = (-held_q, 0.0) if held_q > 0 else (0.0, -held_q)     # may reduce the holding, never flip it
            q = min(c["bound"][1], max(c["bound"][0], q))
        ql = lot(inst, q)
        if ql == 0:
            ql = math.copysign(1.0, q) if inst.type != "SPOT" else round(q, 2) or math.copysign(1.0, q)
        after = dict(R)
        for f, v in H.items():
            after[f] = after.get(f, 0.0) + ql * v
        var1 = _var(after, C)
        side = "BUY" if ql > 0 else "SELL"
        cost = pr.costs(h, side)
        ctot = abs(ql) * (cost.get("total") or 0.0)
        value = 0.5 * gamma_h * max(0.0, _var(R, _wblock(C, S)) - _var(after, _wblock(C, S)))
        liq = pr.liquidity()
        notional = abs(ql) * (pr.unit_notional() or 0.0)
        part = (notional / liq["adv_usd"]) if liq.get("adv_usd") else None
        L = 1.0 / (1.0 + part / 0.10) if part is not None else 0.8
        # basis: what the product cannot remove — correlation of its P&L with the targeted P&L
        cov_th = sum(R.get(a, 0.0) * H.get(b, 0.0) * C.get((a, b), 0.0) for a in S for b in H)
        var_h = _var(H, C)
        rho2 = (cov_th * cov_th / (tgt_pnl_var * var_h)) if tgt_pnl_var > 0 and var_h > 0 else 0.0
        basis = math.sqrt(max(0.0, var1 - var_ideal))
        exp_red = (var0 - var1) / var0 if var0 > 0 else 0.0
        # tail suitability
        T = 1.0
        if objective == "crash":
            hedge_only = [{"inst": inst, "priced": pr, "quantity": ql, "exposures": {f: ql * v for f, v in H.items()}}]
            g_opt = scenario_pnl(hedge_only, crash_shock, m, rk)
            lin = ql * H.get("MKT", 0.0) * crash_shock["MKT"]
            T = max(0.5, min(1.5, g_opt / lin)) if lin > 0 else 1.0
        hist = None
        E = None
        Rg = 1.0
        if history:
            hist = walk_forward(research, priced, objective, S, prim, inst, pr, h, params, rk)
            if hist and hist.get("n") and objective == "crash":
                tr = (hist.get("tail") or {}).get("reduction")
                p_req = float(params.get("reduction") or 0.5)
                E = (tr / p_req) if tr is not None and p_req > 0 else None
            elif hist and hist.get("n"):
                E = hist.get("effectiveness")
                if E is None and hist.get("realized_reduction") is not None and exp_red > 0.02:
                    E = hist["realized_reduction"] / exp_red
                reg = m.regime()
                vals = []
                for dim in ("market", "volatility"):
                    st = reg.get(dim)
                    b = (hist.get("by_regime") or {}).get(st) if st else None
                    if b and b.get("realized_reduction") is not None and hist.get("realized_reduction"):
                        lam = b["n"] / (b["n"] + 20.0)
                        vals.append(1 + lam * (b["realized_reduction"] / hist["realized_reduction"] - 1))
                if vals:
                    Rg = max(0.5, min(1.5, sum(vals) / len(vals)))
        E_used = effectiveness_term(E) if E is not None else (1.0 if exp_red > 0 else -1.0)
        Q = value / (value + max(0.0, ctot)) if value > 0 else 0.0
        B = max(0.0, min(1.0, rho2))
        sh = 100.0 * math.tanh(E_used * Q * L * Rg * B * T / K_H)
        c.update({"status": "ELIGIBLE", "unit_quantity": ql, "side": side, "notional": notional, "expected_reduction": exp_red,
                  "sigma_before": math.sqrt(var0), "sigma_after": math.sqrt(var1), "basis_risk_daily": basis, "cost": cost, "cost_total": ctot,
                  "cost_pct_nav": ctot / nav if nav else None, "efficiency": (value / ctot) if ctot > 0 else None, "liquidity": liq,
                  "participation": part, "score": sh, "components": {"E": E_used, "Q": Q, "L": L, "R": Rg, "B": B, "T": T,
                  "E_source": "walk-forward realised ÷ expected" if E is not None else "expected (no history)"},
                  "history": _hist_view(hist), "_features": (hist or {}).get("features_today"), "greeks": pr.greeks_per_unit(),
                  "inputs": pr.inputs, "notes": pr.notes,
                  "hedge_pct": {f: (-(ql * H.get(f, 0.0)) / R[f]) if R.get(f) else None for f in S},
                  "exposure_added": {f: ql * v for f, v in H.items() if f not in S and abs(ql * v) > 1e-6}})
    eligible = [c for c in cands if c.get("status") == "ELIGIBLE"]
    eligible.sort(key=lambda c: -c["score"])
    # ---------- the optimised package (Raw Shaffer Hedge): forward stepwise selection of legs, then whole lots
    pool = [c for c in eligible if c["id"] in (params.get("use") or [])] or _pool(eligible, S, int(params.get("max_candidates", 10)))
    max_legs = int(params.get("max_legs", 2))
    legs_all = {c["id"]: _opt_leg(c, h, C) for c in pool}
    exact = bool(params.get("exact"))
    if objective == "crash":                       # crash protection: the best-scoring product covering the crash loss
        chosen, q = (pool[:1], [pool[0]["unit_quantity"]]) if pool else ([], [])
    else:
        var_obj = objective in VARIANCE_OBJECTIVES
        one_by_one = objective == "curve" or (objective in FACTOR_BY_FACTOR and len(S) > 1)
        legs_n = max(3, max_legs) if (var_obj or objective in ("systematic", "name")) and "max_legs" not in params else max_legs
        chosen, q = select_package(R, Rstar, pool, legs_all, C, gamma_h, legs_n,
                                   curve=one_by_one, exact=exact, S=S,
                                   w_opt=1.0 if var_obj else None, w_sel=1.0 if var_obj else None, must_hedge=not var_obj)
    package = [_leg_view(c, ql, R, S, h, nav) for c, ql in zip(chosen, q) if ql]
    raw_after = _apply(R, package, cands)
    # ---------- ML adjustment (separate, capped, only if verified)
    ml = _ml(research, hist_objective(objective, prim) if prim else None, package, cands, h, ml_models)
    final_pkg = []
    for L in package:
        adj = ml["by_leg"].get(L["id"], {}).get("applied", 0.0)
        c = next(x for x in cands if x["id"] == L["id"])
        fq = lot(c["inst"], L["quantity"] * (1 + adj)) or L["quantity"]
        final_pkg.append(_leg_view(c, fq, R, S, h, nav) | {"raw_quantity": L["quantity"], "ml_applied": adj})
    final_after = _apply(R, final_pkg, cands)
    # ---------- before / after
    hedge_priced = lambda pkg: [{"inst": next(x for x in cands if x["id"] == L["id"])["inst"], "priced": next(x for x in cands if x["id"] == L["id"])["priced"],
                                 "quantity": L["quantity"], "exposures": {f: L["quantity"] * v for f, v in next(x for x in cands if x["id"] == L["id"])["H"].items()},
                                 "greeks": _scale_greeks(next(x for x in cands if x["id"] == L["id"])["priced"].greeks_per_unit(), L["quantity"])} for L in pkg]
    books = {"before": priced, "raw": priced + hedge_priced(package), "final": priced + hedge_priced(final_pkg)}
    risk_tab = {}
    for k, bk in books.items():
        Rk = risk_vector(bk)
        met = summary_metrics(Rk, C, nav, bk)
        met.update(tail_stats(hist_pnl(bk, m, rk)))
        risk_tab[k] = met
    scen = []
    for sc in SCENARIOS:
        shock = factor_shock(sc, m, kdn, kup)
        dd = h if sc.get("decay") else 0
        b = scenario_pnl(priced, shock, m, rk, dd)
        hr = scenario_pnl(hedge_priced(package), shock, m, rk, dd)
        hf = scenario_pnl(hedge_priced(final_pkg), shock, m, rk, dd)
        scen.append({"key": sc["key"], "label": sc["label"], "before": b, "hedge_raw": hr, "after_raw": b + hr, "hedge_final": hf, "after_final": b + hf,
                     "offset_raw": (-hr / b) if b < 0 else None})
    by_factor = []
    for f in sorted(set(R) | set(raw_after), key=lambda f: -abs(R.get(f, 0.0))):
        if f.startswith("IDIO:") and abs(R.get(f, 0.0)) < 1:
            continue
        by_factor.append({"factor": f, "label": flabel(f), "unit": funit(f), "current": R.get(f, 0.0), "target": Rstar.get(f, R.get(f, 0.0)),
                          "targeted": f in S, "after_raw": raw_after.get(f, 0.0), "after_final": final_after.get(f, 0.0),
                          "hedge_pct_raw": ((R[f] - raw_after.get(f, 0.0)) / R[f]) if R.get(f) else None,
                          "hedge_pct_final": ((R[f] - final_after.get(f, 0.0)) / R[f]) if R.get(f) else None,
                          "share_of_risk": contributions(R, C).get(f)})
    warnings = []
    shares = contributions(R, C)
    tshare = sum(max(0.0, shares.get(f) or 0.0) for f in S)
    if S and objective not in VARIANCE_OBJECTIVES and tshare < 0.02:
        top = max(shares, key=lambda f: shares.get(f) or 0.0) if shares else None
        warnings.append(f"Almost none of this risk is what the objective targets ({tshare:.1%} of its variance)"
                        + (f"; the largest risk is {flabel(top)} ({shares[top]:.0%}) — the Automatic objective hedges that." if top else "."))
    if risk_tab["raw"]["sigma_daily"] > risk_tab["before"]["sigma_daily"] * 1.001 and package:
        warnings.append(f"Hedging this risk RAISES total volatility ({risk_tab['before']['sigma_daily']:,.0f} → {risk_tab['raw']['sigma_daily']:,.0f} $/day): "
                        "the targeted exposure has been offsetting other risks in the book.")
    if not package and eligible:
        warnings.append("No package: the requested change is smaller than one tradeable lot of every eligible product, or costs exceed the benefit.")
    if not eligible:
        warnings.append("No eligible product can hedge this risk with FinSim2's data (see the ineligible list for the reasons).")
    return {"asof": m.asof, "objective": objective, "objective_label": OBJECTIVES.get(objective, objective), "auto": auto, "params": params,
            "warnings": warnings,
            "horizon": hkey, "horizon_days": h, "nav": nav, "risk_aversion": A,
            "positions": [{k: v for k, v in p.items() if k not in ("inst", "priced")} for p in priced],
            "targeted": S, "primary_factor": prim, "primary_label": flabel(prim) if prim else None,
            "risk": {"before": risk_tab["before"], "raw": risk_tab["raw"], "final": risk_tab["final"]}, "factors": by_factor,
            "candidates": [_cand_view(c) for c in eligible] + [_cand_view(c) for c in cands if c.get("status") != "ELIGIBLE"],
            "best": _bests(eligible, float(params.get("reduction") or 0.5)), "package": {"raw": package, "final": final_pkg}, "ml": ml, "scenarios": scen,
            "regime": m.regime(), "data": _freshness(m)}


def _group_exposure(e: Dict[str, float], ho: str) -> float:
    """Per-$ exposure to the historical objective's factor group: $ per +1bp for rates and credit, $ per 100% move
    for a currency, commodity or bitcoin."""
    if ho == "rates":
        return sum(v for f, v in e.items() if f.startswith(("RATE:", "REAL:")))
    if ho == "credit":
        return sum(v for f, v in e.items() if f.startswith("CREDIT:"))
    if ho.startswith("fx:"):
        return e.get("FX:" + ho[3:], 0.0)
    if ho.startswith("commodity:"):
        return e.get(ho[10:], 0.0)
    if ho == "crypto":
        return e.get("CRYPTO", 0.0)
    return 0.0


def _pool(eligible: List[dict], S: List[str], n: int) -> List[dict]:
    """Candidates for the optimiser: the two best-scoring products for EACH targeted factor (by the share of that
    factor they can remove), then the best overall, up to n."""
    out: List[dict] = []
    for f in S:
        rel = [c for c in eligible if c.get("hedge_pct", {}).get(f)]
        rel.sort(key=lambda c: -(c["score"] + 20 * min(1.0, abs(c["hedge_pct"][f]))))
        for c in rel[:2]:
            if c not in out:
                out.append(c)
    for c in eligible:
        if len(out) >= n:
            break
        if c not in out:
            out.append(c)
    return out[:max(n, len(out))]


def _crash_units(c: dict, R: Dict[str, float], shock: Dict[str, float], m: Market, rk: RiskModel, params: dict) -> Optional[float]:
    """Units that cover `reduction` of the book's loss in the −20% market scenario (options fully re-priced)."""
    loss = sum(v * shock.get(f, 0.0) for f, v in R.items())
    if loss >= 0:
        return None
    one = [{"inst": c["inst"], "priced": c["priced"], "quantity": 1.0, "exposures": c["H"]}]
    g = scenario_pnl(one, shock, m, rk)
    if g <= 0:
        return None
    p = float(params.get("reduction") or 0.5)
    return lot(c["inst"], p * -loss / g) or 1.0


def _scale_greeks(g, q):
    if not g:
        return None
    return {k: (v * q if k not in ("delta", "gamma", "iv") else v) for k, v in g.items()}


def _apply(R, pkg, cands):
    after = dict(R)
    for L in pkg:
        c = next(x for x in cands if x["id"] == L["id"])
        for f, v in c["H"].items():
            after[f] = after.get(f, 0.0) + L["quantity"] * v
    return after


def _leg_view(c: dict, q: float, R: Dict[str, float], S: List[str], h: int, nav: float) -> dict:
    inst, pr, H = c["inst"], c["priced"], c["H"]
    side = "BUY" if q > 0 else "SELL"
    cost = pr.costs(h, side)
    g = pr.greeks_per_unit()
    unit_n = pr.unit_notional() or 0.0
    why = []
    gross = sum(abs(v) for f, v in R.items() if not f.startswith(("RATE:", "REAL:", "CREDIT:", "VOL")))
    for f in S:
        if R.get(f) and H.get(f):
            material = f.startswith(("RATE:", "REAL:", "CREDIT:", "VOL")) or abs(R[f]) >= 0.05 * gross
            if material:
                share = -(q * H[f]) / R[f]
                why.append(f"{'removes' if share >= 0 else 'adds back'} {abs(share):.0%} of the {flabel(f).lower()} exposure")
    added = {f: q * v for f, v in H.items() if f not in S and abs(q * v) > max(1.0, 0.02 * sum(abs(x) for x in R.values()))}
    if added:
        big = sorted(added.items(), key=lambda kv: -abs(kv[1]))[:2]
        why.append("adds " + ", ".join(f"{flabel(f).lower()} {v:+,.0f}" for f, v in big) + " (basis)")
    view = {"id": c["id"], "name": c["name"], "type": inst.type, "product_type": inst.product_type, "side": side, "quantity": q,
            "units": "contracts" if inst.type in ("FUTURE", "OPTION") else ("currency units" if inst.type == "FORWARD" else "shares"),
            "price": pr.price, "multiplier": inst.multiplier, "unit_notional": unit_n, "notional": abs(q) * unit_n,
            "market_value": q * (pr.unit_value() or 0.0), "risk_unit": pr.risk_unit(), "sizing_rule": pr.sizing_rule(),
            "cost": {k: (v * abs(q) if isinstance(v, (int, float)) and k != "rolls" else v) for k, v in cost.items() if k != "info"}
                    | {"info": {k: (v * abs(q) if isinstance(v, (int, float)) and k != "forecast_vol" else v) for k, v in (cost.get("info") or {}).items()}},
            "cost_pct_nav": (abs(q) * (cost.get("total") or 0.0) / nav) if nav else None, "why": "; ".join(why) or "reduces the targeted risk",
            "expiry": inst.expiry, "roll": inst.roll, "notes": pr.notes}
    if inst.type == "FUTURE":
        proxy = inst.spec.get("proxy") or ("IEF" if inst.spec["kind"] == "treasury" else inst.spec.get("pair") or "SPY")
        sd = (pr.m.realized_vol(proxy) or 0.15) / math.sqrt(252)
        view["margin_estimate"] = abs(q) * unit_n * max(0.03, 5.0 * sd)
        view["margin_note"] = "estimate (5 daily σ of the underlying, at least 3% of notional); margin is collateral, not exposure"
        if inst.spec["kind"] == "treasury":
            view["dv01_per_contract"] = pr.inputs["treasury"]["dv01"]
        else:
            view["beta"] = (H.get("MKT", 0.0) / unit_n) if unit_n else None
    if g:
        view["option"] = {"right": inst.right, "strike": inst.strike, "expiry": inst.expiry, "style": inst.style, "premium_per_contract": g["premium"],
                          "premium_total": g["premium"] * abs(q), "delta": g["delta"], "gamma": g["gamma"], "vega_usd": g["vega_usd"] * q,
                          "theta_usd": g["theta_usd"] * q, "delta_adjusted_notional": g["delta_usd"] * q, "iv": g["iv"],
                          "hedge_ratio_scenarios": _option_ratio_path(c, q, R)}
    return view


def _option_ratio_path(c: dict, q: float, R: Dict[str, float]) -> List[dict]:
    """Hedge ratio of an option leg (its delta-dollars against the book's beta-dollars) now and after market moves,
    with delta re-computed at the new underlying price and the implied vol moved with the VIX."""
    inst, pr = c["inst"], c["priced"]
    S0 = pr.inputs["spot"]["value"]
    iv = pr.inputs["implied_vol"]["value"]
    r, qy = pr.inputs["rate"]["value"], pr.inputs["dividend_yield"]["value"]
    m = pr.m
    kdn, kup = _vix_response(m)
    vix0, _ = m.macro("VIXCLS")
    beta_u = (c["H"].get("MKT", 0.0) / (pr.greeks_per_unit()["delta_usd"] or 1.0)) if pr.greeks_per_unit() else 1.0
    out = []
    for mv in (0.0, -0.05, -0.10, -0.20, 0.05, 0.10):
        S1 = S0 * (1 + beta_u * mv)
        dv = (kdn if mv < 0 else kup) * mv * ((iv * 100 / vix0) if vix0 else 1.0) / 100.0
        g = px.option(inst.right, S1, inst.strike, pr._T(), r, qy, max(0.01, iv + dv), "EUROPEAN")
        book = R.get("MKT", 0.0) * (1 + mv)
        hedge = g["delta"] * S1 * inst.multiplier * q * beta_u
        out.append({"market_move": mv, "delta": g["delta"], "hedge_ratio": (-hedge / book) if book else None, "option_value": g["price"] * inst.multiplier * q})
    return out


def _cand_view(c: dict) -> dict:
    keep = ("id", "name", "eligible", "status", "reasons", "sizing_rule", "risk_unit", "unit_quantity", "side", "notional", "expected_reduction",
            "sigma_before", "sigma_after", "basis_risk_daily", "cost", "cost_total", "cost_pct_nav", "efficiency", "liquidity", "participation", "score",
            "components", "history", "greeks", "notes", "hedge_pct", "exposure_added", "inputs")
    out = {k: c.get(k) for k in keep if k in c}
    inst = c.get("inst")
    if inst is not None:
        out["type"], out["product_type"], out["expiry"] = inst.type, inst.product_type, inst.expiry
        out["product"] = P.PRODUCT_TYPE.get(inst.product_type, {}).get("name", inst.product_type)
    return out


def effectiveness_term(x: float) -> float:
    """E of the Shaffer Hedge Score from x = realised ÷ expected (crash: tail offset ÷ requested share). A hedge that
    delivers far more than asked (a deep put sized on the −20% scenario that offsets 3× the requested tail) is a
    directional bet, not a better hedge: E peaks at 1.25 and falls back beyond it, floored at −1."""
    return max(-1.0, min(x, 2.5 - x))


def _bests(el: List[dict], p_req: float = 0.5) -> dict:
    if not el:
        return {}
    pick = lambda key, rev=False: (sorted(el, key=key, reverse=rev)[0]["id"])
    out = {"best_score": el[0]["id"], "best_risk_match": pick(lambda c: c["sigma_after"]), "cheapest": pick(lambda c: c["cost_total"]),
           "lowest_basis": pick(lambda c: c["basis_risk_daily"])}
    tails = [c for c in el if (c.get("history") or {}).get("tail_reduction") is not None]
    if tails:
        # the tail offset closest to what was asked for (more is over-hedging, not protection)
        out["best_crash"] = sorted(tails, key=lambda c: abs((c["history"]["tail_reduction"] or -9) - p_req))[0]["id"]
    return out


def _hist_view(hist: Optional[dict]) -> Optional[dict]:
    if not hist or not hist.get("n"):
        return hist and {"n": 0, "reason": hist.get("reason")}
    return {"n": hist["n"], "n_eff": hist["n_eff"], "first": hist["first"], "last": hist["last"], "realized_reduction": hist["realized_reduction"],
            "expected_reduction": hist["expected_reduction"], "effectiveness": hist["effectiveness"], "baselines": hist["baselines"],
            "tail_reduction": hist["tail"]["reduction"], "tail": hist["tail"], "drawdown": hist["drawdown"], "hedge_pnl_mean": hist["hedge_pnl_mean"],
            "upside_sacrificed": hist["upside_sacrificed"], "residual_daily": hist.get("residual_daily"), "unhedged_daily": hist.get("unhedged_daily"),
            "by_regime": {k: {kk: v.get(kk) for kk in ("realized_reduction", "n", "n_eff")} for k, v in hist["by_regime"].items()},
            "recent_third": hist["recent_third"], "early_third": hist["early_third"], "best": hist["best"], "worst": hist["worst"]}


def hist_objective(objective: str, prim: str) -> Optional[str]:
    if prim == "MKT":
        return "equity"
    if prim.startswith(("SEC:", "IND:")):
        return "sector:" + prim.split(":")[1]
    if prim.startswith("IDIO:"):
        return "name"
    if prim.startswith(("RATE:", "REAL:")):
        return "rates"
    if prim.startswith("CREDIT:"):
        return "credit"
    if prim.startswith("FX:"):
        return "fx:" + prim[3:]
    if prim.startswith("CMD:"):
        return "commodity:" + prim
    if prim == "CRYPTO":
        return "crypto"
    return None


def leg_template(inst: P.Instrument, pr: P.Priced, store) -> Optional[dict]:
    if inst.type == "SPOT":
        a = store.asset(inst.id) or {}
        t = {"kind": "spot", "asset": inst.id, "duration": a.get("duration")}
        if a.get("asset_class") == "FX" and inst.id != "DXY":
            base = (a.get("meta") or {}).get("base_currency") or inst.id[:3]
            t["fx_pair"] = (a.get("currency"), -1.0) if base == "USD" else (base, 1.0)
        return t
    if inst.type == "FUTURE":
        k = inst.spec["kind"]
        if k == "equity_index":
            return {"kind": "equity_future", "index": inst.underlying, "proxy": inst.spec["proxy"]}
        if k == "treasury":
            return {"kind": "treasury_future", "spec": inst.spec}
        if k == "fx":
            return {"kind": "forward", "ccy": inst.spec["ccy"], "foreign_rate": SHORT_RATE.get(inst.spec["ccy"])}
        return None
    if inst.type == "OPTION":
        import datetime as _dt
        S = pr.inputs["spot"]["value"]
        days = (_dt.date.fromisoformat(inst.expiry) - _dt.date.fromisoformat(pr.m.asof)).days
        return {"kind": "option", "underlying": inst.underlying, "right": inst.right, "moneyness": inst.strike / S, "tenor_days": days,
                "vol_underlying": inst.underlying}
    if inst.type == "FORWARD":
        base, quote = inst.spec["base"], inst.spec["quote"]
        ccy = quote if base == "USD" else base
        return {"kind": "forward", "ccy": ccy, "foreign_rate": SHORT_RATE.get(ccy)}
    return None


def walk_forward(research, priced, objective, S, prim, inst, pr, h, params, rk=None) -> Optional[dict]:
    """The candidate's walk-forward record against the current book (spot holdings at today's dollar value; option and
    futures holdings by their underlying's delta-dollars)."""
    ho = hist_objective(objective, prim)
    tmpl = leg_template(inst, pr, research.store)
    if ho is None or tmpl is None:
        return {"n": 0, "reason": "no historical model for this objective or product"}
    book = []
    for p in priced:
        i2 = p.get("inst")
        if i2 is None or p.get("market_value") is None:
            continue
        if i2.type == "SPOT":
            ent = {"asset": i2.id, "mv": p["market_value"]}
            if rk is not None and ho != "equity" and not ho.startswith(("sector:", "name")):
                ent["structural"] = _group_exposure(rk.unit_exposures(i2.id)["exposures"], ho)
            book.append(ent)
        elif i2.type == "OPTION" and p.get("greeks"):
            book.append({"asset": i2.underlying, "mv": p["greeks"]["delta_usd"]})
        elif i2.type == "FUTURE" and i2.spec.get("kind") == "equity_index":
            book.append({"asset": i2.spec["proxy"], "mv": p["notional"]})
    if not book:
        return {"n": 0, "reason": "nothing in the book has a price history"}
    if rk is not None and tmpl.get("kind") == "spot" and ho != "equity" and not ho.startswith(("sector:", "name")):
        ue = _group_exposure(rk.unit_exposures(inst.id)["exposures"], ho)
        if ue:
            tmpl["unit_exposure"] = ue
        if tmpl.get("fx_pair"):
            tmpl["unit_exposure"] = tmpl["fx_pair"][1] if ho == f"fx:{tmpl['fx_pair'][0]}" else ue
    if tmpl.get("kind") == "forward":
        tmpl["unit_exposure"] = 1.0
    ratio = float(params.get("reduction") or 0.5) if objective not in ("neutral",) else 1.0
    key = f"hedgewf:{research.version()}:{ho}:{h}:{ratio}:{sorted((b['asset'], round(b['mv'])) for b in book)}:{sorted(tmpl.items(), key=str)}"
    cached = research._mem.get(key)
    if cached is not None:
        return cached
    try:
        ev = Evaluator(research, book, ho, tmpl, h, ratio=ratio)
        res = ev.run()
        res["features_today"] = ev._features(ev.n - 1, ev.book_pnl())       # conditions now, for the ML adjustment
    except Exception as e:  # noqa: BLE001 - one product's history must not break the analysis
        return {"n": 0, "reason": f"history failed: {type(e).__name__}: {e}"}
    research._mem[key] = res
    return res


def ml_group(hobj: Optional[str], leg_kind: Optional[str], h: int) -> str:
    return f"{(hobj or 'none').split(':')[0]}:{leg_kind}:{h}"


def _ml(research, hobj, package, cands, h, models) -> dict:
    """ML adjustment per leg from the verified walk-forward models (kv hedgeml:<VERSION>); 0 when unverified."""
    from .history import VERSION
    models = models if models is not None else (research.store.kv_get(f"hedgeml:{VERSION}") or {})
    out = {"alpha": ALPHA, "cap": ADJ_CAP, "by_leg": {}, "trained": bool(models), "note": None}
    if not models:
        out["note"] = "no hedge ML models trained yet (python -m finsim2 hedge-audit): adjustment 0"
    for L in package:
        c = next(x for x in cands if x["id"] == L["id"])
        tmpl = leg_template(c["inst"], c["priced"], research.store) or {}
        group = ml_group(hobj, tmpl.get("kind"), h)
        mdl = (models.get("groups") or {}).get(group)
        feats = c.get("_features") or {}
        adj = ml_adjustment(mdl, feats)
        adj["group"] = group
        out["by_leg"][L["id"]] = adj | {"applied": adj.get("applied", 0.0) if adj.get("verified") else 0.0}
    return out


def _freshness(m: Market) -> dict:
    out = {}
    for sid in ("DGS3MO", "DGS10", "VIXCLS", "BAMLH0A0HYM2"):
        v, d = m.macro(sid)
        out[sid] = {"value": v, "date": d}
    p, d = m.close("SPY")
    out["prices"] = {"date": d}
    return out
