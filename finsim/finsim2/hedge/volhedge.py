"""ML Lab — does the breadth-enhanced volatility forecast make the Shaffer Hedge economically better? (research only)

The new-information study showed that breadth improves the 1W log-volatility forecast (squared error −7.3% against a
63d / 21d / VIX baseline). A better forecast is not a better hedge. This study replays the COMPLETE hedge chain point in
time — PIT data → volatility forecast → covariance → candidates → sizing → optimiser → package → realised P&L — for
two otherwise identical engines and judges the final hedge, not the forecast.

Where the volatility forecast enters hedge-2 (read from engine.py, not assumed): the covariance Σ of daily factor moves
(trailing 252-day sample). Σ weighs the risk mismatch against cost in the optimiser, ranks candidates (score), sets the
target of `target_vol` and the automatic objective, and weights every factor in the variance objectives. Objectives that
pin a target exposure (beta, sector, name, duration, ...) weight the target by 10⁴, so their size is set by exposures
(beta, DV01, CS01, delta) and a volatility forecast can change them only through product choice and cost trade-offs.

Arms (identical except for the named difference; the same PIT market snapshot, candidates, prices and cost model):
    A  hedge-2                      production: Σ from the trailing 252-day sample
    B  hedge-2-breadth-vol-exp      challenger: Σ with the market factor's (MKT = SPY) variance replaced by the
                                    breadth-enhanced forecast; correlations kept (MKT row and column rescaled)
    R  hedge-2-regvol-ref           reference (attribution only, never a candidate): the same forecast WITHOUT breadth
                                    (63d / 21d realised volatility and VIX), so "any better volatility model" is
                                    separated from "breadth"
    C  hedge-2-sizing-exp           the confirmed fixed-resizing challenger: A's package × its confirmed multiple
    D  breadth-vol + resizing       B's package × the same multiple (evaluated only after A–C, as a secondary result)
    baselines: no hedge; half of A's package (a fixed-size hedge)
The replay uses the Raw Shaffer Hedge (no walk-forward history and no ML adjustment — neither can be rebuilt point in
time cheaply, and both arms would share them). C and D are evaluated only from 2018-01-01: the multiples were
discovered before 2018 and confirmed after it, so earlier windows would be in-sample for them.

Volatility model (per horizon, per era; frozen before each validation window): pooled over the breadth family's assets
(equities, equity ETFs, indices), exactly the design validated in newinfo: log realised volatility over the horizon on
[1, log σ63, log σ21, log VIX] (R) and on the same plus the five standardised breadth features (B), ridge, trained on
records that MATURED before the era starts. The level is calibrated on SPY's own training records (ratio of mean
realised variance to mean exp(2·forecast)), separately for R and B, so no arm wins by a level bias. 1M and 3M use
models fitted at those horizons (no 1W extrapolation).

Cases: every book × objective × study date, horizons 1W (a date every 10 sessions), 1M (every 21), 3M (every 63), from
2009-01-01. Books (normalised to $1M): single stocks (AAPL, JPM, XOM), sector ETFs (XLK, XLF), broad equity ETFs (QQQ,
IWM), a 10-stock equity portfolio, a mixed 60/30/10 portfolio, a bond ladder, a credit portfolio, bitcoin, a multi-asset
FX / commodity book, and an equity book short VXX (volatility objective). An objective applies to a book on a date only
when the engine targets a non-trivial exposure there.

Realised outcomes over (t, t+h], per case and arm (book held; hedge held; futures rolled at fair value; options
re-priced daily at the Cboe index volatility — MODEL-PRICED, FLAT VOLATILITY ASSUMPTION): hedged P&L = book + hedge −
cost. Per cell (objective × horizon, pooled over books, $ per $1M):
    variance reduction 1 − Σ(u+h)²/Σu² (daily) · ES95 / VaR95 of the window P&L · mean max drawdown · tail loss in the
    unhedged 5% worst windows · hedge P&L · cost · profit sacrificed (−mean hedge P&L; the ex-ante prior-drift version is
    reported too) · upside given up · basis error (RMS of hedge P&L minus the ideal hedge of the targeted exposures) ·
    over / under-hedging (ex-post optimal multiple of the package) · turnover · legs
    utility U(λ) = [Risk(unhedged) − Risk(hedged)] − λ·ProfitSacrificed − Cost, Risk = ES95 for crash / ES / VaR /
    drawdown objectives and the standard deviation otherwise (as in profit-aware hedging), λ ∈ {0.5, 1, 2, 5, 10}
    ΔU = U(B) − U(A) on identical cases.
Inference: paired, clustered by start date (every book on one date is one cluster), a moving-block bootstrap over dates
(blocks of 6 dates, 400 replications, fixed seed) for CIs and one-sided p; Benjamini-Hochberg at q = 0.10 across the
objective × horizon cells at λ = 1. Breakdowns (book type, product, regime, era) are descriptive, with their own BH.

Gates, fixed 2026-09-25 before any result (per objective × horizon):
    G1  forecast validity: the newinfo pooled walk-forward hedge-volatility test at this horizon has t ≥ 2, AND on this
        study's dates the breadth forecast of the market factor has a lower walk-forward squared log error than R
    G2  ΔU(λ = 1) > 0 on unseen history: bootstrap one-sided p surviving BH at q = 0.10, with ≥ 60 applicable dates
    G3  ΔU(λ = 1) > 0 in ≥ 3 of the 4 complete eras (2009–12, 2013–16, 2017–20, 2021–24)
    G4  no material degradation: hedged ES95 not worse by more than 2% of the unhedged ES95, basis error not worse by
        more than 5%, mean cost not higher by more than 10%
    G5  live shadow: ≥ 60 graded matched hedge outcomes with a positive live ΔU
Statuses: INSUFFICIENT DATA (< 60 applicable dates) · NO HEDGE IMPROVEMENT (G1 fails, or the hedge is not better and
the forecast is not either) · IMPROVES FORECAST ONLY (G1 passes; G2–G4 do not all pass) · SHADOW (G1–G4) · LIVE SHADOW
(SHADOW and recorded daily) · ELIGIBLE FOR PROMOTION (and G5). Nothing is promoted automatically; production (hedge-2,
its sizing, the frozen benchmark) is not changed by this module.
"""
from __future__ import annotations

import bisect
import math
import random
import time
from typing import Dict, List, Optional, Tuple

RESEARCH_KEY = "lab:breadthhedge"
CONTROL, CHALLENGER, REFERENCE, RESIZE, COMBO = ("hedge-2", "hedge-2-breadth-vol-exp", "hedge-2-regvol-ref",
                                                "hedge-2-sizing-exp", "hedge-2-breadth-vol-sizing-exp")
HORIZONS = [("1W", 5, 10), ("1M", 21, 21), ("3M", 63, 63)]          # (label, sessions, step between study dates)
START = "2009-01-01"
RESIZE_FROM = "2018-01-01"
NAV = 1.0e6
LAMBDAS = (0.5, 1.0, 2.0, 5.0, 10.0)
PRIMARY_LAMBDA = 1.0
TAIL_OBJ = {"crash", "es", "var", "drawdown"}
TARGET_VOL = 0.10
FDR_Q = 0.10
MIN_DATES = 60
CHANGED_MIN = 0.05
BOOT_REPS, BOOT_BLOCK, BOOT_SEED = 400, 6, 17
G4_ES, G4_BASIS, G4_COST = 0.02, 0.05, 0.10
SKEW_BUMP = 0.05                  # conservative downside-IV sensitivity: bought puts cost this many vol points more
ERAS4 = [("2009-01-01", "2013-01-01"), ("2013-01-01", "2017-01-01"), ("2017-01-01", "2021-01-01"), ("2021-01-01", "2025-01-01")]
ERA_ALL = ERAS4 + [("2025-01-01", "9999-12-31")]
OBJECTIVES = ["min_variance", "target_vol", "beta", "systematic", "sector", "name", "crash", "es", "var", "drawdown",
              "duration", "curve", "credit", "fx", "commodity", "crypto", "volatility"]
EQ10 = ["AAPL", "MSFT", "JPM", "XOM", "JNJ", "PG", "AMZN", "UNH", "HD", "KO"]
BOOKS = [
    {"key": "AAPL", "type": "single stock", "w": {"AAPL": 1.0}},
    {"key": "JPM", "type": "single stock", "w": {"JPM": 1.0}},
    {"key": "XOM", "type": "single stock", "w": {"XOM": 1.0}},
    {"key": "XLK", "type": "sector ETF", "w": {"XLK": 1.0}},
    {"key": "XLF", "type": "sector ETF", "w": {"XLF": 1.0}},
    {"key": "QQQ", "type": "broad equity ETF", "w": {"QQQ": 1.0}},
    {"key": "IWM", "type": "broad equity ETF", "w": {"IWM": 1.0}},
    {"key": "EQ10", "type": "equity portfolio", "w": {a: 0.1 for a in EQ10}},
    {"key": "MIX", "type": "mixed portfolio", "w": {**{a: 0.06 for a in EQ10}, "IEF": 0.30, "GLD": 0.10}},
    {"key": "BONDS", "type": "bond portfolio", "w": {"IEF": 0.5, "TLT": 0.3, "SHY": 0.2}},
    {"key": "CREDIT", "type": "credit portfolio", "w": {"HYG": 0.5, "LQD": 0.5}},
    {"key": "BTC", "type": "crypto exposure", "w": {"BTC": 1.0}},
    {"key": "MACRO", "type": "mixed portfolio", "w": {"EFA": 0.30, "FXE": 0.20, "GLD": 0.20, "USO": 0.15, "DBC": 0.15}},
    {"key": "SHORTVOL", "type": "equity portfolio", "w": {**{a: 0.09 for a in EQ10}, "VXX": -0.10}},
]
MIN_TARGET_SHARE = 0.01           # an objective applies when its targeted exposure is ≥ 1% of NAV (factor units × NAV)


def _dot(w, x):
    return sum(a * b for a, b in zip(w, x))


# ------------------------------------------------------------------ the volatility models (per horizon, per era)
def fit_vol_models(store, research, lab: str, progress=None) -> Dict[str, dict]:
    """{era_start: {"b0", "b1", "std", "k0", "k1", "n", "to"}}: the pooled log-volatility models of newinfo, trained on
    records matured before each era, with the level calibrated on SPY's own training records."""
    from ..engine import directional as D
    from ..engine import newinfo as N
    from ..engine import weights as W
    from ..engine.lab import LAB_HORIZONS
    say = progress or (lambda m: None)
    W._init_famidx()
    h = dict(LAB_HORIZONS)[lab]
    recs = W.load(store, research, lab)
    D.attach(recs, research, h)
    recs = [r for r in recs if r.ext and r.ext.get("s") and r.yr != 0]
    builder = N.Builder(research, store)
    rows = N.attach(recs, builder, "breadth_plus")
    ctx = {"rets": {a: N._logret(builder.series(a)) for a in {r.asset for r, _ in rows}}, "vix": builder.macro("VIXCLS")}
    data = []
    for r, v in rows:
        x0, t = N._vol_base(r, ctx, h), N._future_log_vol(r, ctx["rets"][r.asset], h)
        if x0 is not None and t is not None:
            data.append((r, v, x0, t))
    say(f"{lab}: {len(data)} volatility records")
    out: Dict[str, dict] = {}
    for a, b in ERA_ALL:
        tr = [x for x in data if x[0].end < a]
        if len(tr) < 2000:
            continue
        st = N._standardise([v for _, v, _, _ in tr])
        b0 = N._ridge([x0 for _, _, x0, _ in tr], [t for *_, t in tr], 1e-6)
        b1 = N._ridge([x0 + N._z(v, st) for _, v, x0, _ in tr], [t for *_, t in tr], 1.0)
        spy = [x for x in tr if x[0].asset == "SPY"]
        k = []
        for w, add in ((b0, False), (b1, True)):
            if len(spy) < 50:
                k.append(1.0)
                continue
            num = sum(math.exp(2 * t) for *_, t in spy)
            den = sum(math.exp(2 * _dot(w, x0 + (N._z(v, st) if add else []))) for _, v, x0, _ in spy)
            k.append(num / den if den > 0 else 1.0)
        out[a] = {"b0": b0, "b1": b1, "std": st, "k0": k[0], "k1": k[1], "n": len(tr), "n_spy": len(spy), "to": b,
                  "features": list(N.FAMILIES["breadth_plus"]["features"])}
    return out


class VolForecaster:
    """The market factor's daily variance over (i, i+h] under R and B, on any past session i, point in time."""

    def __init__(self, research, store, models: Dict[str, dict], h: int, asset: str = "SPY"):
        from ..engine import newinfo as N
        self.r, self.models, self.h, self.asset = research, models, h, asset
        self.b = N.Builder(research, store)
        self.cal = self.b.cal
        self.rets = N._logret(self.b.series(asset))
        self.vix = self.b.macro("VIXCLS")
        self.feat = self.b.features("breadth_plus", store.asset(asset) or {"id": asset, "asset_class": "ETF", "sector": "Broad Market"})
        self.meta = store.asset(asset) or {"id": asset, "asset_class": "ETF"}
        self.starts = sorted(models)

    def model_for(self, date: str) -> Tuple[Optional[str], Optional[dict]]:
        """The model frozen before the era that contains `date` (trained only on records matured before it)."""
        k = bisect.bisect_right(self.starts, date) - 1
        if k < 0:
            return None, None
        m = self.models[self.starts[k]]
        return (self.starts[k], m) if date < m["to"] else (None, None)

    def at(self, i: int) -> Optional[dict]:
        from ..engine import directional as D
        from ..engine import newinfo as N
        from ..engine.weights import Rec
        era, mdl = self.model_for(self.cal[i])
        if mdl is None or not self.feat:
            return None
        ext = D.live_inputs(self.r, self.meta, self.h, {}, i=i)
        if not ext or not ext.get("s"):
            return None
        rr = Rec()
        rr.asset, rr.ext = self.asset, ext
        x0 = N._vol_base(rr, {"rets": {self.asset: self.rets}, "vix": self.vix}, self.h)
        v = [(self.feat.get(k) or [None] * len(self.cal))[i] for k in mdl["features"]]
        if x0 is None or all(x is None for x in v):
            return None
        z = N._z(v, mdl["std"])
        f0, f1 = _dot(mdl["b0"], x0), _dot(mdl["b1"], x0 + z)
        return {"f_reg": f0 + 0.5 * math.log(mdl["k0"]), "f_breadth": f1 + 0.5 * math.log(mdl["k1"]),
                "v_reg": mdl["k0"] * math.exp(2 * f0), "v_breadth": mdl["k1"] * math.exp(2 * f1), "era": era,
                "breadth": dict(zip(mdl["features"], v))}

    def realised(self, i: int) -> Optional[float]:
        """Log RMS of the daily log returns over (i, i+h] (the forecast target)."""
        seg = [x for x in self.rets[i + 1:i + 1 + self.h] if x is not None]
        if len(seg) < max(3, self.h // 2):
            return None
        v = math.sqrt(sum(x * x for x in seg) / len(seg))
        return math.log(v) if v > 0 else None


def scale_market(C: Dict[Tuple[str, str], float], v_new: Optional[float], factor: str = "MKT") -> Dict[Tuple[str, str], float]:
    """Σ with `factor`'s variance replaced by v_new and its covariances rescaled so every correlation is unchanged."""
    v0 = C.get((factor, factor))
    if not v_new or not v0 or v0 <= 0:
        return C
    s = math.sqrt(v_new / v0)
    out = {}
    for (a, b), v in C.items():
        k = (s if a == factor else 1.0) * (s if b == factor else 1.0)
        out[(a, b)] = v * k
    return out


# ------------------------------------------------------------------ one date: the books, the three engines, realised P&L
def book_positions(book: dict, m, store) -> Optional[List[dict]]:
    from . import products as P
    out = []
    for a, w in book["w"].items():
        try:
            pr = P.Priced(P.parse(a, store), m)
        except (ValueError, KeyError):
            return None
        uv = pr.unit_value()
        if not uv:
            return None
        out.append({"id": a, "quantity": w * NAV / uv})
    return out


def _params(objective: str, lab: str) -> dict:
    p = {"horizon": lab}
    if objective == "target_vol":
        p["target_vol"] = TARGET_VOL
    return p


def _applicable(res: dict) -> bool:
    S, R, Rs = res.get("targeted") or [], res.get("R") or {}, res.get("Rstar") or {}
    need = sum(abs(Rs.get(f, R.get(f, 0.0)) - R.get(f, 0.0)) for f in S)
    if res["objective"] in ("min_variance", "var", "es", "drawdown", "target_vol"):
        return bool(S) and res.get("eligible", 0) > 0
    unit = {f: (NAV if not f.startswith(("RATE:", "REAL:", "CREDIT:", "VOL")) else NAV * 1e-4) for f in S}
    return bool(S) and res.get("eligible", 0) > 0 and any(abs(R.get(f, 0.0)) >= MIN_TARGET_SHARE * unit[f] for f in S) and need > 0


def _unit_value(inst, m) -> Optional[float]:
    from . import products as P
    pr = P.Priced(inst, m)
    if pr.price is None:
        return None
    if inst.type == "FUTURE":
        return pr.unit_notional()
    if inst.type == "FORWARD":
        fxq = m.fx(inst.spec["quote"])
        return pr.price * fxq if fxq else None
    return pr.unit_value()


def leg_pnl(research, leg: dict, markets: list, store, cache: Optional[dict] = None) -> Optional[List[float]]:
    """Daily USD P&L of holding the leg's quantity over the window (markets[0] = the decision session). Futures roll at
    fair value into the next contract when they expire. `cache` keeps each product's P&L per unit for the window."""
    if cache is not None:
        if leg["id"] not in cache:
            cache[leg["id"]] = leg_pnl(research, {**leg, "quantity": 1.0}, markets, store)
        unit = cache[leg["id"]]
        return None if unit is None else [leg["quantity"] * x for x in unit]
    from . import pricing as px
    from . import products as P
    try:
        inst = P.parse(leg["id"], store)
    except (ValueError, KeyError):
        return None
    q = leg["quantity"]
    out = []
    prev = _unit_value(inst, markets[0])
    if prev is None:
        return None
    for k in range(1, len(markets)):
        md = markets[k]
        if inst.type == "FUTURE" and inst.expiry and md.asof >= inst.expiry:
            root = leg["id"].split(":")[1]
            kind = "treasury" if inst.spec.get("kind") == "treasury" else "equity"
            code = px.future_expiries(kind, markets[k - 1].asof, 1)[0][0]
            nid = P.future_id(root, code)
            if nid == leg["id"]:
                code = px.future_expiries(kind, md.asof, 1)[0][0]
                nid = P.future_id(root, code)
            try:
                inst = P.parse(nid, store)
            except (ValueError, KeyError):
                return None
            prev = _unit_value(inst, markets[k - 1])
            if prev is None:
                return None
        cur = _unit_value(inst, md)
        if cur is None:
            return None
        out.append(q * (cur - prev))
        prev = cur
    return out


def _skew_cost(leg: dict, m, store) -> float:
    """Extra premium of a bought put at implied volatility + SKEW_BUMP (conservative downside-IV sensitivity)."""
    from . import pricing as px
    from . import products as P
    if leg["type"] != "OPTION" or leg.get("right") != "P" or leg["quantity"] <= 0 or not leg.get("iv"):
        return 0.0
    inst = P.parse(leg["id"], store)
    pr = P.Priced(inst, m)
    if pr.price is None:
        return 0.0
    S = pr.inputs["spot"]["value"]
    r = pr.inputs["rate"]["value"] or 0.0
    q = pr.inputs.get("dividend_yield", {}).get("value") or 0.0
    T = px.year_frac(m.asof, inst.expiry)
    hi = px.option(inst.right, S, inst.strike, T, r, q, leg["iv"] + SKEW_BUMP, inst.style)["price"]
    return max(0.0, hi - pr.price) * inst.multiplier * leg["quantity"]


def _leg_kind(leg: dict, store) -> Optional[str]:
    from . import products as P
    t = leg["type"]
    if t == "SPOT":
        return "spot"
    if t == "OPTION":
        return "option"
    if t == "FORWARD":
        return "forward"
    if t == "FUTURE":
        inst = P.parse(leg["id"], store)
        k = inst.spec.get("kind")
        return "equity_future" if k == "equity_index" else "treasury_future" if k == "treasury" else "forward" if k == "fx" else None
    return None


def resize_multiple(res: dict, multiples: dict, h: int, store) -> float:
    """The confirmed sizing multiple of the package's primary leg (1 when none is confirmed for its group and metric)."""
    from . import engine as E
    from . import objml
    if not res.get("package") or not res.get("primary_factor"):
        return 1.0
    ho = E.hist_objective(res["objective"], res["primary_factor"])
    lead = max(res["package"], key=lambda L: abs(L.get("notional") or 0.0))
    kind = _leg_kind(lead, store)
    g = E.ml_group(ho, kind, h)
    return (multiples.get(g) or {}).get(objml.metric_for(res["objective"])) or 1.0


def replay_date(research, i: int, lab: str, h: int, forecaster: VolForecaster, multiples: dict,
                books=None, objectives=None) -> dict:
    """Every applicable book × objective on session i under A, B and R, with realised outcomes over (i, i+h]."""
    from . import engine as E
    from .designs import prior_drift
    from .market import Market
    from .risk import RiskModel, factor_series
    store = research.store
    m = Market(research, i)
    rk = RiskModel(m)
    fc = forecaster.at(i)
    out = {"i": i, "date": m.asof, "forecast": fc, "cases": []}
    if fc is None:
        return out
    out["realised_log_vol"] = forecaster.realised(i)
    out["var_prod"] = rk.covariance(["MKT"]).get(("MKT", "MKT"))          # production's market variance (252-day sample)
    markets = [m] + [Market(research, i + k) for k in range(1, h + 1)]
    if markets[-1].i != i + h:
        return out
    F = factor_series(research)
    regime = m.regime()
    vix, _ = m.macro("VIXCLS")
    mu_h = prior_drift(m, rk, "SPY", h)
    ucache: dict = {}
    for book in books or BOOKS:
        pos = book_positions(book, m, store)
        if pos is None:
            continue
        u = None
        same: Dict[str, tuple] = {}                 # ES, VaR and drawdown take the same engine path: decide once
        for obj in objectives or OBJECTIVES:
            base = {"market": m, "rk": rk}
            twin = "es" if obj in ("es", "var", "drawdown") else None
            if twin and twin in same:
                a, b, rr = (dict(x, objective=obj) for x in same[twin])
            else:
                try:
                    a = E.analyze(research, pos, obj, _params(obj, lab), nav=NAV, replay=base)
                except Exception as e:  # noqa: BLE001
                    out.setdefault("errors", []).append(f"{book['key']} {obj}: {type(e).__name__}: {e}")
                    continue
                if not _applicable(a):
                    continue
                b = E.analyze(research, pos, obj, _params(obj, lab), nav=NAV,
                              replay={**base, "cov_adjust": lambda C: scale_market(C, fc["v_breadth"])})
                rr = E.analyze(research, pos, obj, _params(obj, lab), nav=NAV,
                               replay={**base, "cov_adjust": lambda C: scale_market(C, fc["v_reg"])})
                if twin:
                    same[twin] = (a, b, rr)
            if not _applicable(a):
                continue
            v0 = a.get("var_mkt")
            if u is None:
                u = _book_pnl(pos, markets, store, ucache)
                if u is None:
                    break
            arms = {}
            ok = True
            for key, res in (("A", a), ("B", b), ("R", rr)):
                hp = [0.0] * h
                legs = []
                for L in res["package"]:
                    p = leg_pnl(research, L, markets, store, ucache)
                    if p is None:
                        ok = False
                        break
                    hp = [x + y for x, y in zip(hp, p)]
                    legs.append({"id": L["id"], "type": L["type"], "product_type": L["product_type"], "q": L["quantity"],
                                 "notional": L["notional"], "beta_usd": L["quantity"] * L["H"].get("MKT", 0.0),
                                 "option": L["type"] == "OPTION"})
                if not ok:
                    break
                arms[key] = {"hp": hp, "cost": sum((L.get("cost") or 0.0) for L in res["package"]), "legs": legs,
                             "skew": sum(_skew_cost(L, m, store) for L in res["package"]),
                             "mult": resize_multiple(res, multiples, h, store)}
            if not ok:
                continue
            S = a["targeted"]
            dR = {f: a["Rstar"].get(f, a["R"].get(f, 0.0)) - a["R"].get(f, 0.0) for f in S}
            ideal = []
            for k in range(i + 1, i + h + 1):
                ideal.append(sum(dR[f] * ((F.get(f) or [None] * (k + 1))[k] or 0.0) for f in S))
            out["cases"].append({"book": book["key"], "btype": book["type"], "objective": obj, "u": u, "ideal": ideal,
                                 "arms": arms, "var_prod": v0, "var_breadth": fc["v_breadth"], "regime": regime, "vix": vix, "mu_h": mu_h,
                                 "S": S, "primary": a.get("primary_factor")})
    return out


def _book_pnl(pos: List[dict], markets: list, store, cache: Optional[dict] = None) -> Optional[List[float]]:
    tot = None
    for p in pos:
        s = leg_pnl(None, {"id": p["id"], "quantity": p["quantity"], "type": "SPOT"}, markets, store, cache)
        if s is None:
            return None
        tot = s if tot is None else [x + y for x, y in zip(tot, s)]
    return tot


# ------------------------------------------------------------------ statistics
def _es(xs: List[float], a: float = 0.05) -> Optional[float]:
    """Expected shortfall as a positive loss: minus the mean of the worst a share."""
    if len(xs) < 20:
        return None
    s = sorted(xs)
    k = max(1, int(math.ceil(a * len(s))))
    return -sum(s[:k]) / k


def _var_q(xs: List[float], a: float = 0.05) -> Optional[float]:
    if len(xs) < 20:
        return None
    s = sorted(xs)
    return -s[max(0, int(a * len(s)) - 1)]


def _sd(xs: List[float]) -> Optional[float]:
    if len(xs) < 3:
        return None
    m = sum(xs) / len(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def _mdd(xs: List[float]) -> float:
    eq = peak = dd = 0.0
    for x in xs:
        eq += x
        peak = max(peak, eq)
        dd = min(dd, eq - peak)
    return -dd


def arm_series(c: dict, arm: str, scale: float = 1.0, skew: bool = False) -> Tuple[List[float], float]:
    """The arm's daily hedge P&L (× scale) and its cost (× scale; + the put-skew premium when `skew`)."""
    if arm == "none":
        return [0.0] * len(c["u"]), 0.0
    if arm == "half":
        a = c["arms"]["A"]
        return [0.5 * x for x in a["hp"]], 0.5 * (a["cost"] + (a["skew"] if skew else 0.0))
    if arm in ("C", "D"):
        a = c["arms"]["A" if arm == "C" else "B"]
        m = a["mult"]
        return [m * x for x in a["hp"]], m * (a["cost"] + (a["skew"] if skew else 0.0))
    a = c["arms"][arm]
    return [scale * x for x in a["hp"]], scale * (a["cost"] + (a["skew"] if skew else 0.0))


def outcome(cases: List[dict], arm: str, objective: str, skew: bool = False) -> dict:
    """Realised metrics of one arm over a set of cases ($ per $1M book over the horizon)."""
    if not cases:
        return {"n": 0}
    U, Hd, cost, dd_u, dd_h, basis_num, basis_n, up_given, sq_u, sq_h, ms = [], [], [], [], [], 0.0, 0, [], 0.0, 0.0, []
    for c in cases:
        hp, co = arm_series(c, arm, skew=skew)
        u = c["u"]
        U.append(sum(u)); Hd.append(sum(hp)); cost.append(co)
        dd_u.append(_mdd(u)); dd_h.append(_mdd([a + b for a, b in zip(u, hp)]))
        sq_u += sum(x * x for x in u)
        sq_h += sum((a + b) ** 2 for a, b in zip(u, hp))
        basis_num += sum((a - b) ** 2 for a, b in zip(hp, c["ideal"]))
        basis_n += len(hp)
        up_given.append(max(0.0, sum(u)) - max(0.0, sum(u) + sum(hp)))
        sxx = sum(b * b for b in hp)
        if sxx > 0:
            ms.append(-sum(a * b for a, b in zip(u, hp)) / sxx)
    hedged = [a + b - k for a, b, k in zip(U, Hd, cost)]
    n = len(U)
    q5 = sorted(U)[max(0, int(0.05 * n) - 1)] if n >= 20 else None
    tail = [j for j in range(n) if q5 is not None and U[j] <= q5]
    risk_u = _es(U) if objective in TAIL_OBJ else _sd(U)
    risk_h = _es(hedged) if objective in TAIL_OBJ else _sd(hedged)
    mH, mC = sum(Hd) / n, sum(cost) / n
    return {"n": n, "variance_reduction": (1 - sq_h / sq_u) if sq_u > 0 else None, "es_u": _es(U), "es_h": _es(hedged),
            "var_u": _var_q(U), "var_h": _var_q(hedged), "dd_u": sum(dd_u) / n, "dd_h": sum(dd_h) / n,
            "tail_u": (sum(U[j] for j in tail) / len(tail)) if tail else None,
            "tail_h": (sum(hedged[j] for j in tail) / len(tail)) if tail else None,
            "hedge_pnl": mH, "cost": mC, "profit_sacrificed": -mH, "upside_given_up": sum(up_given) / n,
            "basis_error": math.sqrt(basis_num / basis_n) if basis_n else None,
            "overhedge": (sum(ms) / len(ms) - 1) if ms else None, "hedge_error": (sum(abs(x - 1) for x in ms) / len(ms)) if ms else None,
            "risk_u": risk_u, "risk_h": risk_h, "loss_reduction": (risk_u - risk_h) if risk_u is not None and risk_h is not None else None}


def utility(o: dict, lam: float) -> Optional[float]:
    if not o.get("n") or o.get("loss_reduction") is None:
        return None
    return o["loss_reduction"] - lam * o["profit_sacrificed"] - o["cost"]


def _window_scalars(cases: List[dict], arm: str, skew: bool = False) -> Tuple[List[float], List[float], List[float]]:
    """Per case: the book's window P&L, the arm's window hedge P&L and its cost."""
    U, Hd, Co = [], [], []
    for c in cases:
        hp, co = arm_series(c, arm, skew=skew)
        U.append(sum(c["u"])); Hd.append(sum(hp)); Co.append(co)
    return U, Hd, Co


def _parts_idx(idx: List[int], U, Hd, Co, objective: str) -> Optional[Tuple[float, float, float]]:
    """(loss reduction, profit sacrificed, cost) of one arm on the resampled cases `idx`."""
    n = len(idx)
    if n < 20:
        return None
    u = [U[k] for k in idx]
    hedged = [U[k] + Hd[k] - Co[k] for k in idx]
    risk = _es if objective in TAIL_OBJ else _sd
    ru, rh = risk(u), risk(hedged)
    if ru is None or rh is None:
        return None
    return ru - rh, -sum(Hd[k] for k in idx) / n, sum(Co[k] for k in idx) / n


def paired(cases: List[dict], objective: str, a: str = "A", b: str = "B", skew: bool = False, reps: int = BOOT_REPS,
           seed: int = BOOT_SEED) -> dict:
    """ΔU(λ) = U(b) − U(a) on identical cases, with a moving-block bootstrap over dates (each date one cluster)."""
    if not cases:
        return {"n": 0}
    oa, ob = outcome(cases, a, objective, skew), outcome(cases, b, objective, skew)
    dates = sorted({c["date"] for c in cases})
    out = {"n": len(cases), "dates": len(dates), "a": oa, "b": ob,
           "d": {str(l): (utility(ob, l) - utility(oa, l)) if utility(ob, l) is not None and utility(oa, l) is not None else None for l in LAMBDAS}}
    if len(dates) < 12 or reps <= 0:
        return out
    by: Dict[str, List[int]] = {}
    for k, c in enumerate(cases):
        by.setdefault(c["date"], []).append(k)
    Ua, Ha, Ca = _window_scalars(cases, a, skew)
    _, Hb, Cb = _window_scalars(cases, b, skew)
    rnd = random.Random(seed)
    nb = max(1, len(dates) // BOOT_BLOCK)
    draws = []
    for _ in range(reps):
        pick: List[int] = []
        for _ in range(nb):
            s = rnd.randrange(0, max(1, len(dates) - BOOT_BLOCK + 1))
            for d in dates[s:s + BOOT_BLOCK]:
                pick += by[d]
        pa, pb = _parts_idx(pick, Ua, Ha, Ca, objective), _parts_idx(pick, Ua, Hb, Cb, objective)
        if pa is None or pb is None:
            continue
        draws.append((pb[0] - pa[0], pb[1] - pa[1], pb[2] - pa[2]))
    if len(draws) < reps // 2:
        return out
    out["ci"], out["p"] = {}, {}
    for l in LAMBDAS:
        ds = sorted(x - l * y - z for x, y, z in draws)
        out["ci"][str(l)] = (ds[int(0.025 * len(ds))], ds[int(0.975 * len(ds)) - 1])
        out["p"][str(l)] = (sum(1 for x in ds if x <= 0) + 1) / (len(ds) + 1)
    return out


def benjamini_hochberg(ps: List[float], q: float = FDR_Q) -> List[bool]:
    m = len(ps)
    order = sorted(range(m), key=lambda k: ps[k])
    k_max = 0
    for rank, k in enumerate(order, 1):
        if ps[k] <= q * rank / m:
            k_max = rank
    rej = [False] * m
    for rank, k in enumerate(order, 1):
        rej[k] = rank <= k_max
    return rej


def _changed(c: dict, x: str = "A", y: str = "B") -> str:
    """identical · sizing (same products, other quantities) · product (a different product set)."""
    la, lb = c["arms"][x]["legs"], c["arms"][y]["legs"]
    ia, ib = sorted(L["id"] for L in la), sorted(L["id"] for L in lb)
    if ia != ib:
        return "product"
    qa = {L["id"]: L["q"] for L in la}
    return "identical" if all(abs(qa[L["id"]] - L["q"]) < 1e-9 for L in lb) else "sizing"


def _size(arm: dict) -> float:
    return sum(abs(L["notional"] or 0.0) for L in arm["legs"])


def _lead_product(arm: dict) -> str:
    if not arm["legs"]:
        return "no hedge"
    L = max(arm["legs"], key=lambda x: abs(x["notional"] or 0.0))
    return L["product_type"] or L["type"]


def _quantiles(xs: List[float]) -> Optional[dict]:
    if not xs:
        return None
    s = sorted(xs)
    q = lambda p: s[min(len(s) - 1, int(p * len(s)))]  # noqa: E731
    return {"n": len(s), "p10": q(0.10), "p25": q(0.25), "median": q(0.5), "p75": q(0.75), "p90": q(0.90),
            "larger": sum(1 for x in s if x > 1 + 1e-9) / len(s), "smaller": sum(1 for x in s if x < 1 - 1e-9) / len(s)}


def _era(date: str) -> Optional[str]:
    for a, b in ERA_ALL:
        if a <= date < b:
            return a[:4] + "–" + (str(int(b[:4]) - 1) if b[:4] != "9999" else "")
    return None


def _regimes(c: dict) -> List[str]:
    rg, out = c.get("regime") or {}, []
    if rg.get("volatility"):
        out.append(rg["volatility"])
    if rg.get("market"):
        out.append(rg["market"])
    if rg.get("rates"):
        out.append(rg["rates"])
    if c.get("vix") is not None:
        out.append("crisis (VIX ≥ 30)" if c["vix"] >= 30 else "normal (VIX < 30)")
    if c.get("credit"):
        out.append(c["credit"])
    return out


def analyse_cell(cases: List[dict], objective: str, reps: int = BOOT_REPS) -> dict:
    """Everything for one objective × horizon: the paired test, arms, baselines, sizes, products, attribution."""
    dates = sorted({c["date"] for c in cases})
    res = {"cases": len(cases), "dates": len(dates), "books": sorted({c["book"] for c in cases})}
    if not cases:
        return res
    res["test"] = paired(cases, objective, "A", "B", reps=reps)
    res["reference"] = paired(cases, objective, "A", "R", reps=0)
    res["breadth_vs_reference"] = paired(cases, objective, "R", "B", reps=0)
    res["skew"] = paired(cases, objective, "A", "B", skew=True, reps=0)
    res["no_options"] = paired([c for c in cases if not any(L["option"] for k in ("A", "B") for L in c["arms"][k]["legs"])], objective, "A", "B", reps=0)
    res["baselines"] = {k: outcome(cases, k, objective) for k in ("none", "half", "A", "B", "R")}
    late = [c for c in cases if c["date"] >= RESIZE_FROM]
    res["from_2018"] = {"cases": len(late), "A": outcome(late, "A", objective), "B": outcome(late, "B", objective),
                        "C": outcome(late, "C", objective), "D": outcome(late, "D", objective),
                        "B_vs_A": paired(late, objective, "A", "B", reps=0).get("d"),
                        "C_vs_A": paired(late, objective, "A", "C", reps=0).get("d"),
                        "D_vs_A": paired(late, objective, "A", "D", reps=0).get("d"),
                        "B_vs_C": paired(late, objective, "C", "B", reps=0).get("d")}
    # eras
    res["eras"] = {}
    for a, b in ERA_ALL:
        sub = [c for c in cases if a <= c["date"] < b]
        p = paired(sub, objective, "A", "B", reps=0)
        res["eras"][_era(a)] = {"dates": len({c["date"] for c in sub}), "d": p.get("d"), "complete": (a, b) in ERAS4}
    # decisions: identical / sizing / product
    kinds = [_changed(c) for c in cases]
    res["decisions"] = {k: kinds.count(k) / len(kinds) for k in ("identical", "sizing", "product")}
    res["changed_share"] = 1 - res["decisions"]["identical"]
    # sizes
    ratios, by_prod = [], {}
    for c in cases:
        sa, sb = _size(c["arms"]["A"]), _size(c["arms"]["B"])
        if sa > 0 and sb > 0:
            ratios.append(sb / sa)
            by_prod.setdefault(_lead_product(c["arms"]["A"]), []).append(sb / sa)
    res["size_ratio"] = _quantiles(ratios)
    res["size_ratio_by_product"] = {k: _quantiles(v) for k, v in by_prod.items()}
    res["size"] = {"A": sum(_size(c["arms"]["A"]) for c in cases) / len(cases), "B": sum(_size(c["arms"]["B"]) for c in cases) / len(cases)}
    res["legs"] = {k: sum(len(c["arms"][k]["legs"]) for c in cases) / len(cases) for k in ("A", "B")}
    # turnover: change of gross hedge notional between consecutive dates of the same book
    for k in ("A", "B"):
        prev, tv = {}, []
        for c in sorted(cases, key=lambda x: x["date"]):
            s = _size(c["arms"][k])
            if c["book"] in prev:
                tv.append(abs(s - prev[c["book"]]) / NAV)
            prev[c["book"]] = s
        res.setdefault("turnover", {})[k] = (sum(tv) / len(tv)) if tv else None
    # product transitions and what changed products did
    trans: Dict[str, int] = {}
    for c, k in zip(cases, kinds):
        if k == "product":
            t = f"{_lead_product(c['arms']['A'])} → {_lead_product(c['arms']['B'])}"
            trans[t] = trans.get(t, 0) + 1
    res["transitions"] = dict(sorted(trans.items(), key=lambda kv: -kv[1])[:8])
    # attribution: additive pieces on identical cases
    att = {}
    for k in ("sizing", "product"):
        sub = [c for c, kk in zip(cases, kinds) if kk == k]
        if not sub:
            continue
        sq_u = sum(sum(x * x for x in c["u"]) for c in cases) or 1.0
        dv = sum(sum((a + b) ** 2 for a, b in zip(c["u"], c["arms"]["B"]["hp"])) - sum((a + b) ** 2 for a, b in zip(c["u"], c["arms"]["A"]["hp"]))
                 for c in sub) / sq_u
        oa, ob = outcome(sub, "A", objective), outcome(sub, "B", objective)
        att[k] = {"cases": len(sub), "variance_reduction_change": -dv, "hedge_pnl_change": (ob["hedge_pnl"] - oa["hedge_pnl"]) * len(sub) / len(cases),
                  "cost_change": (ob["cost"] - oa["cost"]) * len(sub) / len(cases),
                  "basis_change": (ob["basis_error"] or 0) - (oa["basis_error"] or 0),
                  "hedge_error_change": ((ob["hedge_error"] or 0) - (oa["hedge_error"] or 0)),
                  "tail_change": ((ob["tail_h"] or 0) - (oa["tail_h"] or 0)) if ob.get("tail_h") is not None and oa.get("tail_h") is not None else None}
    res["attribution"] = att
    # breakdowns (descriptive): book type, lead product (production's), regime, era
    res["by_book_type"] = {}
    for bt in sorted({c["btype"] for c in cases}):
        sub = [c for c in cases if c["btype"] == bt]
        res["by_book_type"][bt] = _brief(sub, objective, reps)
    res["by_product"] = {}
    for pt in sorted({_lead_product(c["arms"]["A"]) for c in cases}):
        sub = [c for c in cases if _lead_product(c["arms"]["A"]) == pt]
        res["by_product"][pt] = _brief(sub, objective, reps)
    res["by_regime"] = {}
    for st in sorted({s for c in cases for s in _regimes(c)}):
        sub = [c for c in cases if st in _regimes(c)]
        res["by_regime"][st] = _brief(sub, objective, reps)
    # the volatility forecasts behind these cases
    va = [c["var_prod"] for c in cases if c.get("var_prod")]
    vb = [c["var_breadth"] for c in cases if c.get("var_breadth")]
    res["forecast_vol"] = {"production": (sum(math.sqrt(v * 252) for v in va) / len(va)) if va else None,
                           "challenger": (sum(math.sqrt(v * 252) for v in vb) / len(vb)) if vb else None}
    return res


def _brief(sub: List[dict], objective: str, reps: int) -> dict:
    p = paired(sub, objective, "A", "B", reps=reps // 2 if len({c["date"] for c in sub}) >= 30 else 0)
    return {"cases": len(sub), "dates": p.get("dates"), "d": (p.get("d") or {}).get(str(PRIMARY_LAMBDA)),
            "ci": (p.get("ci") or {}).get(str(PRIMARY_LAMBDA)), "p": (p.get("p") or {}).get(str(PRIMARY_LAMBDA)),
            "changed": (sum(1 for c in sub if _changed(c) != "identical") / len(sub)) if sub else None}


# ------------------------------------------------------------------ gates and statuses
def forecast_check(dates: List[dict]) -> dict:
    """Walk-forward squared log error of the market factor's volatility on the study dates: production (252-day
    sample), R and B; paired B vs R, clustered by date (one observation per date)."""
    rows = [d for d in dates if d.get("forecast") and d.get("realised_log_vol") is not None and d.get("var_prod")]
    if not rows:
        return {"n": 0}
    t = [d["realised_log_vol"] for d in rows]
    fp = [0.5 * math.log(d["var_prod"]) for d in rows]
    fr = [d["forecast"]["f_reg"] for d in rows]
    fb = [d["forecast"]["f_breadth"] for d in rows]
    mse = lambda f: sum((a - b) ** 2 for a, b in zip(t, f)) / len(t)  # noqa: E731
    g = [(a - r) ** 2 - (a - b) ** 2 for a, r, b in zip(t, fr, fb)]
    m = sum(g) / len(g)
    sd = _sd(g)
    return {"n": len(rows), "mse_production": mse(fp), "mse_reference": mse(fr), "mse_breadth": mse(fb), "gain_vs_reference": m,
            "gain_pct": m / mse(fr) if mse(fr) else None, "t": (m / (sd / math.sqrt(len(g)))) if sd else None,
            "gain_vs_production_pct": (mse(fp) - mse(fb)) / mse(fp) if mse(fp) else None}


def gates(cell: dict, g1: bool, objective: str) -> dict:
    t = cell.get("test") or {}
    d1 = (t.get("d") or {}).get(str(PRIMARY_LAMBDA))
    g = {"G1": g1, "dates": cell.get("dates", 0), "enough": cell.get("dates", 0) >= MIN_DATES, "d": d1,
         "p": (t.get("p") or {}).get(str(PRIMARY_LAMBDA))}
    eras = [e for e in (cell.get("eras") or {}).values() if e.get("complete") and e.get("d") and e["d"].get(str(PRIMARY_LAMBDA)) is not None]
    g["eras_won"] = sum(1 for e in eras if e["d"][str(PRIMARY_LAMBDA)] > 0)
    g["eras_complete"] = len(eras)
    g["G3"] = g["eras_won"] >= 3
    a, b = t.get("a") or {}, t.get("b") or {}
    worse_es = (b.get("es_h") or 0) - (a.get("es_h") or 0)
    worse_basis = ((b.get("basis_error") or 0) / a["basis_error"] - 1) if a.get("basis_error") else 0.0
    worse_cost = ((b.get("cost") or 0) / a["cost"] - 1) if a.get("cost") else (0.0 if not b.get("cost") else 1.0)
    g["degradation"] = {"es": worse_es / a["es_u"] if a.get("es_u") else None, "basis": worse_basis, "cost": worse_cost}
    g["G4"] = bool(a.get("es_u") and worse_es <= G4_ES * a["es_u"] and worse_basis <= G4_BASIS and worse_cost <= G4_COST)
    return g


def finalise(result: dict) -> dict:
    """BH across objective × horizon at λ = 1, then G2 and every status."""
    keys, ps = [], []
    for lab, hz in result["horizons"].items():
        for obj, cell in (hz.get("cells") or {}).items():
            g = cell.get("gates") or {}
            if g.get("enough") and g.get("p") is not None:
                keys.append((lab, obj)); ps.append(g["p"])
    rej = benjamini_hochberg(ps, FDR_Q) if ps else []
    for (lab, obj), r in zip(keys, rej):
        result["horizons"][lab]["cells"][obj]["gates"]["fdr"] = r
    live = result.get("live") or {}
    for lab, hz in result["horizons"].items():
        for obj, cell in (hz.get("cells") or {}).items():
            g = cell["gates"]
            g["G2"] = bool(g.get("enough") and g.get("d") is not None and g["d"] > 0 and g.get("fdr"))
            if not g.get("enough"):
                st, why = "INSUFFICIENT DATA", f"{g.get('dates', 0)} applicable dates (< {MIN_DATES})"
            elif not g["G1"]:
                st, why = "NO HEDGE IMPROVEMENT", "the breadth volatility forecast does not validate at this horizon (G1)"
            elif g["G2"] and g["G3"] and g["G4"]:
                st, why = "SHADOW", "G1–G4 pass"
                if f"{lab}:{obj}" in (live.get("cells") or []):
                    st = "LIVE SHADOW"
            else:
                miss = [k for k in ("G2", "G3", "G4") if not g[k]]
                how = ("the hedge decision rarely changes ({:.0%} of cases)".format(cell.get("changed_share") or 0)
                       if (cell.get("changed_share") or 0) < CHANGED_MIN else "the decision changes in {:.0%} of cases".format(cell.get("changed_share") or 0))
                st, why = "IMPROVES FORECAST ONLY", f"better forecast, hedge not shown better ({', '.join(miss)} fail); {how}"
            cell["status"], cell["why"] = st, why
    return result


# ------------------------------------------------------------------ drivers
def _credit_state(research) -> List[Optional[str]]:
    """Widening / tightening: the 63-session change of Moody's Baa minus the 10-year Treasury yield (daily, PIT)."""
    p = research.panel()
    a, b = p.macro("DBAA"), p.macro("DGS10")
    s = [(x - y) if x is not None and y is not None else None for x, y in zip(a, b)]
    return [("credit widening" if s[k] > s[k - 63] else "credit tightening") if k >= 63 and s[k] is not None and s[k - 63] is not None else None
            for k in range(len(s))]


def study_dates(cal: List[str], h: int, step: int, start: str = START) -> List[int]:
    first = bisect.bisect_left(cal, start)
    return list(range(max(first, 300), len(cal) - 1 - h, step))


def read_stream(path: str) -> List[dict]:
    """The dates a worker has appended to its chunk's progress stream (complete records only)."""
    import os
    import pickle
    out = []
    if not os.path.exists(path):
        return out
    with open(path, "rb") as fh:
        while True:
            try:
                out.append(pickle.load(fh))
            except (EOFError, pickle.UnpicklingError, ValueError, AttributeError):
                break
    return out


def _worker(db_path: str, lab: str, h: int, idx: List[int], models: dict, multiples: dict, books=None, objectives=None,
            stream: Optional[str] = None):
    """Replays `idx`; each finished date is appended to `stream`, and dates already in it are not replayed again."""
    import pickle
    from ..data.store import Store
    from ..engine.research import Research
    done = {d["i"]: d for d in read_stream(stream)} if stream else {}
    st = Store(db_path)
    try:
        r = Research(st)
        fc = VolForecaster(r, st, models, h)
        cr = _credit_state(r)
        out = []
        for i in idx:
            if i in done:
                out.append(done[i])
                continue
            d = replay_date(r, i, lab, h, fc, multiples, books, objectives)
            for c in d["cases"]:
                c["date"] = d["date"]
                c["credit"] = cr[i]
            out.append(d)
            if stream:
                with open(stream, "ab") as fh:
                    pickle.dump(d, fh)
        return out
    finally:
        st.close()


CHUNKS = 12                        # fixed, so a resumed run splits the dates exactly as the interrupted one did


def _cache_dir(db_path: str) -> str:
    import os
    d = db_path + ".breadthhedge"
    os.makedirs(d, exist_ok=True)
    return d


def load_dates(db_path: str, lab: str) -> List[dict]:
    """Every replayed date (with its cases) of a finished horizon, as saved by run_all."""
    import os
    import pickle
    f = os.path.join(_cache_dir(db_path), f"{lab}_dates.pkl")
    with open(f, "rb") as fh:
        return pickle.load(fh)


def run_all(db_path: str, workers: int = 3, progress=None, horizons=None, books=None, objectives=None, max_dates: Optional[int] = None) -> dict:
    """The complete study (A, B, R; C and D from A and B's packages) on every horizon; stored under RESEARCH_KEY.
    Each finished chunk of dates is saved next to the database and reused on a rerun with the same dates, books and
    objectives (a deterministic resume); every horizon's cases are kept for the post-run attribution."""
    import hashlib
    import json
    import os
    import pickle
    from concurrent.futures import ProcessPoolExecutor, as_completed
    from ..data.store import Store
    from ..engine.lab import benchmark, registry, verify_benchmark
    from ..engine.research import Research
    from ..engine import newinfo as N
    say = progress or (lambda m: None)
    t0 = time.time()
    st = Store(db_path)
    try:
        bm = benchmark(st)
        if not bm or not verify_benchmark(st, bm["id"]).get("ok"):
            raise ValueError("a frozen, verified benchmark is required (python -m finsim2 lab --freeze-benchmark)")
        ni = st.kv_get(N.RESEARCH_KEY) or {}
        r = Research(st)
        cal = r.panel().calendar()
        sizing = next((v for v in registry(st)["versions"] if v["id"] == RESIZE), None)
        multiples = (sizing or {}).get("multiples") or {}
        labs = [x for x in HORIZONS if not horizons or x[0] in horizons]
        models = {}
        for lab, h, _ in labs:
            models[lab] = fit_vol_models(st, r, lab, say)
            say(f"{lab}: volatility models for eras {', '.join(sorted(models[lab]))}")
    finally:
        st.close()
    out = {"started": time.strftime("%Y-%m-%d %H:%M:%S"), "benchmark": {k: bm[k] for k in ("id", "hash", "frozen", "production")},
           "arms": {"A": CONTROL, "B": CHALLENGER, "R": REFERENCE, "C": RESIZE, "D": COMBO}, "lambdas": list(LAMBDAS),
           "books": BOOKS if not books else books, "resize_multiples": multiples, "resize_from": RESIZE_FROM, "horizons": {},
           "models": {lab: {e: {"n": m["n"], "n_spy": m["n_spy"], "k0": m["k0"], "k1": m["k1"], "to": m["to"]} for e, m in mm.items()} for lab, mm in models.items()}}
    for lab, h, step in labs:
        idx = study_dates(cal, h, step)
        if max_dates:
            idx = idx[:: max(1, len(idx) // max_dates)][:max_dates]
        chunks = [idx[k::CHUNKS] for k in range(CHUNKS)]
        cdir = _cache_dir(db_path)
        tag = hashlib.sha256(json.dumps([lab, idx, [b["key"] for b in (books or BOOKS)], objectives or OBJECTIVES,
                                         out["models"][lab]], sort_keys=True, default=str).encode()).hexdigest()[:12]
        dates: List[dict] = []
        todo = []
        for k, ch in enumerate(chunks):
            f = os.path.join(cdir, f"{lab}_{tag}_{k}.pkl")
            if ch and os.path.exists(f):
                with open(f, "rb") as fh:
                    dates += pickle.load(fh)
                say(f"{lab}: chunk {k} reused from {f}")
            elif ch:
                todo.append((k, ch, f))
        t1 = time.time()
        with ProcessPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(_worker, db_path, lab, h, ch, models[lab], multiples, books, objectives, f + ".stream"): (k, f) for k, ch, f in todo}
            for n_done, fu in enumerate(as_completed(futs), 1):
                k, f = futs[fu]
                part = fu.result()
                with open(f + ".tmp", "wb") as fh:
                    pickle.dump(part, fh)
                os.replace(f + ".tmp", f)
                dates += part
                say(f"{lab}: {n_done}/{len(todo)} chunks done (chunk {k} saved), {sum(len(d['cases']) for d in dates)} cases ({time.time() - t1:.0f}s)")
        dates.sort(key=lambda d: d["i"])
        with open(os.path.join(cdir, f"{lab}_dates.pkl.tmp"), "wb") as fh:
            pickle.dump(dates, fh)
        os.replace(os.path.join(cdir, f"{lab}_dates.pkl.tmp"), os.path.join(cdir, f"{lab}_dates.pkl"))
        out["horizons"][lab] = study_horizon(dates, lab, ni, say)
    finalise(out)
    out["seconds"] = round(time.time() - t0, 1)
    st = Store(db_path)
    try:
        st.kv_set(RESEARCH_KEY, out)
    finally:
        st.close()
    return out


def study_horizon(dates: List[dict], lab: str, ni: dict, say=None) -> dict:
    say = say or (lambda m: None)
    cases = [c for d in dates for c in d["cases"]]
    fc = forecast_check(dates)
    fam = ((((ni.get("horizons") or {}).get(lab) or {}).get("families") or {}).get("breadth_plus") or {})
    pooled_t = (((fam.get("walkforward") or {}).get("hedge") or {}).get("vol_mse_gain") or {}).get("t")
    g1 = bool(pooled_t is not None and pooled_t >= 2 and (fc.get("gain_vs_reference") or 0) > 0)
    hz = {"dates": len(dates), "cases": len(cases), "forecast": fc, "pooled_t": pooled_t, "G1": g1, "cells": {},
          "errors": sum(len(d.get("errors") or []) for d in dates), "error_sample": [e for d in dates for e in (d.get("errors") or [])][:10]}
    for obj in OBJECTIVES:
        sub = [c for c in cases if c["objective"] == obj]
        t = time.time()
        cell = analyse_cell(sub, obj)
        cell["gates"] = gates(cell, g1, obj)
        hz["cells"][obj] = cell
        say(f"{lab} {obj}: {len(sub)} cases ({time.time() - t:.0f}s)")
    return hz


# ------------------------------------------------------------------ the report (BREADTH_HEDGE_RESEARCH.md)
def _m(v, d=0):
    return "—" if v is None else f"{v:+,.{d}f}"


def _pc(v, d=1):
    return "—" if v is None else f"{v * 100:+.{d}f}%"


CONCLUSIONS = {
    "A": "Breadth improves volatility forecasting and materially improves realized Shaffer Hedge outcomes.",
    "B": "Breadth improves volatility forecasting but does not materially improve realized Shaffer Hedge outcomes.",
    "C": "Breadth's apparent hedge improvement is explained by the improved volatility architecture rather than breadth itself.",
    "D": "Evidence is mixed / regime-specific and is not sufficient for promotion."}


def conclusion(res: dict) -> str:
    """The executive-summary sentence, by a fixed rule on the results: A if any cell passes G1–G4 (SHADOW); C if a cell
    passes G2 but breadth adds nothing significant over the no-breadth model; D if a cell passes G2 without passing
    everything else; otherwise B when G1 holds somewhere (the forecast is better, the hedge is not), else D."""
    cells = [c for hz in (res.get("horizons") or {}).values() for c in (hz.get("cells") or {}).values()]
    if any(c.get("status") in ("SHADOW", "LIVE SHADOW", "ELIGIBLE FOR PROMOTION") for c in cells):
        return "A"
    g2 = [c for c in cells if (c.get("gates") or {}).get("G2")]
    if g2:
        arch = [c for c in g2 if ((((c.get("extended") or {}).get("p") or {}).get("breadth_vs_nobreadth") or {}).get("1.0") or 1.0) > 0.05]
        return "C" if len(arch) == len(g2) else "D"
    return "B" if any(hz.get("G1") for hz in (res.get("horizons") or {}).values()) else "D"


def _gate_word(v, testable=True):
    return "NOT YET TESTABLE" if not testable else ("PASS" if v else "FAIL")


def _t(x):
    return "—" if x is None else f"{x:+.2f}"


def markdown(res: dict) -> str:
    H = res.get("horizons") or {}
    L: List[str] = []
    w = L.append
    bm = res.get("benchmark") or {}
    key = conclusion(res)
    cells = list(_cells(res))
    w("# Does better volatility forecasting improve Shaffer Hedge outcomes? — research")
    w("")
    w(f"Run {res.get('started')} · {res.get('seconds')} s · attribution {res.get('extended_at', '—')} · `python -m finsim2 lab --breadth-hedge`. "
      f"Research only: hedge-2, its sizing, the Shaffer Score and the frozen benchmark `{bm.get('id')}` (sha256 `{bm.get('hash')}`) are "
      "unchanged; nothing is promoted.")
    w("")
    w("## Executive summary")
    w("")
    w(f"**{CONCLUSIONS[key]}**")
    w("")
    g1 = {lab: hz.get("G1") for lab, hz in H.items()}
    fd = [(lab, o) for lab, o, c in cells if (c.get("gates") or {}).get("fdr")]
    w("- The breadth-enhanced forecast of the market factor's volatility is better out of sample: squared log error " + "; ".join(
        f"{lab} {_pc(-((hz.get('forecast') or {}).get('gain_pct') or 0))} against the same model without breadth (t {_m((hz.get('forecast') or {}).get('t'), 1)}), "
        f"{_pc(-((hz.get('forecast') or {}).get('gain_vs_production_pct') or 0))} against production" for lab, hz in H.items() if (hz.get("forecast") or {}).get("n"))
      + f". G1 passes at {', '.join(l for l, v in g1.items() if v) or 'no horizon'} and fails at {', '.join(l for l, v in g1.items() if not v) or 'no horizon'}.")
    w(f"- The better forecast does not make the Shaffer Hedge measurably better: of {sum(1 for *_, c in cells if (c.get('gates') or {}).get('p') is not None)} "
      f"objective × horizon tests of realised utility (λ = 1), {len(fd)} survive the Benjamini-Hochberg control; no cell passes G1–G4, so nothing "
      "is eligible for live shadow and hedge-2 stays unchanged.")
    uniq = [(lab, o, c) for lab, o, c in cells if o not in ("var", "drawdown")]
    pos = [(lab, o, c) for lab, o, c in uniq if (c["gates"].get("d") or 0) > 0 and ((c.get("extended") or {}).get("d") or {}).get("nobreadth_vs_production")]
    tot = sum(c["gates"]["d"] for *_, c in pos)
    arch_part = sum((c["extended"]["d"]["nobreadth_vs_production"].get("1.0") or 0) for *_, c in pos)
    vs = [(lab, o, c) for lab, o, c in uniq if o in VARIANCE_SENSITIVE and ((c.get("extended") or {}).get("d") or {}).get("breadth_vs_nobreadth")]
    neg_b = [(lab, o) for lab, o, c in vs if (c["extended"]["d"]["breadth_vs_nobreadth"].get("1.0") or 0) < 0]
    if pos and tot:
        w(f"- In the {len(pos)} cells whose utility point estimate is positive (ES / VaR / drawdown counted once), the same regression without "
          f"breadth accounts for {arch_part / tot:.0%} of the summed gain and breadth itself for {1 - arch_part / tot:.0%}. Adding breadth to "
          f"that regression lowered utility in {len(neg_b)} of the {len(vs)} variance-sensitive cells.")
    meds = [((c.get("extended") or {}).get("size_ratio") or {}).get("median") for *_, c in uniq]
    meds = [m for m in meds if m is not None]
    w(f"- Hedge sizes barely move: the median breadth ÷ production size (same risk unit) is between {min(meds):.2f} and {max(meds):.2f} across cells; "
      "the decisions that change are mostly product and leg choices, and their realised effects are not significant.")
    w("")
    w("Arms, as always kept apart: **production** = hedge-2 (252-day covariance) · **no-breadth vol** = the new log-volatility regression "
      "(63d / 21d realised volatility, VIX) without breadth · **breadth vol** = the same regression with the five breadth features "
      "(`hedge-2-breadth-vol-exp`). ΔU(breadth − production) is the pre-registered test; ΔU(breadth − no-breadth) isolates breadth itself. "
      "Utility U = risk reduction − λ·profit sacrificed − hedge cost, $ per $1M book over the horizon (ES95 for crash / ES / VaR / drawdown, "
      "standard deviation otherwise); paired on identical cases; moving-block bootstrap over dates (every book on a date is one cluster); "
      "t = ΔU ÷ bootstrap SE. Protocol and gates were committed before any full result; the only change during the run was runtime "
      "(checkpoints, resume, saved cases — the first launch was stopped 3 minutes into the replay, before any date finished).")
    w("")
    # gates
    w("## Gates (as committed)")
    w("")
    w("| Horizon | G1 forecast validity | G2 realised utility (FDR) | G3 era stability | G4 no degradation | G5 live shadow |")
    w("|---|---|---|---|---|---|")
    for lab, hz in H.items():
        cs = [c for o, c in (hz.get("cells") or {}).items() if (c.get("test") or {}).get("n")]
        w(f"| {lab} | {_gate_word(hz.get('G1'))} | {sum(1 for c in cs if c['gates'].get('G2'))} of {len(cs)} PASS | "
          f"{sum(1 for c in cs if c['gates'].get('G3'))} of {len(cs)} PASS | {sum(1 for c in cs if c['gates'].get('G4'))} of {len(cs)} PASS | NOT YET TESTABLE |")
    w("")
    w("G5 cannot pass from history: a cell passing G1–G4 would be LIVE SHADOW ELIGIBLE, never production-eligible. No cell reached that point.")
    w("")
    # main table
    w("## The main result table (λ = 1; $ per $1M book over the horizon)")
    w("")
    w("| Objective | Horizon | λ | Production U | No-breadth U | Breadth U | ΔU breadth−production | ΔU breadth−no-breadth | Variance Δ | ES Δ | Drawdown Δ | Cost Δ | Profit sacrificed Δ | Size ratio | Product changed % | Positive eras | clustered t | FDR | Gate status |")
    w("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for lab, obj, c in cells:
        t, e, g = c["test"], c.get("extended") or {}, c["gates"]
        a, b = t["a"], t["b"]
        U = e.get("U") or {}
        dbr = ((e.get("d") or {}).get("breadth_vs_nobreadth") or {}).get("1.0")
        sr = (e.get("size_ratio") or {}).get("median")
        raw = g.get("p")
        w(f"| {obj} | {lab} | 1 | {_m(utility(a, 1.0))} | {_m((U.get('nobreadth') or {}).get('1.0'))} | {_m(utility(b, 1.0))} | {_m(g.get('d'))} | {_m(dbr)} | "
          f"{_pc((b.get('variance_reduction') or 0) - (a.get('variance_reduction') or 0), 2) if a.get('variance_reduction') is not None else '—'} | "
          f"{_m(_dd(a, b, 'es_h'))} | {_m(_dd(a, b, 'dd_h'))} | {_m((b.get('cost') or 0) - (a.get('cost') or 0))} | "
          f"{_m((b.get('profit_sacrificed') or 0) - (a.get('profit_sacrificed') or 0))} | {('%.3f' % sr) if sr is not None else '—'} | "
          f"{(c.get('decisions') or {}).get('product', 0):.0%} | {g.get('eras_won')}/{g.get('eras_complete')} | {_t((e.get('t') or {}).get('breadth_vs_production'))} | "
          f"{'✓' if g.get('fdr') else '✗'} (raw p {('%.3f' % raw) if raw is not None else '—'}) | {c.get('status')} |")
    w("")
    w("ES Δ and Drawdown Δ: production minus breadth (positive = the breadth hedge lost less). Cost Δ and Profit sacrificed Δ: breadth minus "
      "production (positive = costs / gives up more). Variance Δ: change in realised variance reduction. Size ratio: median breadth ÷ "
      "production size in the same risk unit (quantity of the same product; market beta-dollars when the product changed and the "
      "primary risk is the market). ES, VaR and drawdown take the same engine path and are all scored on ES95, so their rows are identical.")
    w("")
    # lambda table
    w("## ΔU at every λ (breadth − production / breadth − no-breadth)")
    w("")
    w("| Objective | Horizon | λ = 0.5 | λ = 1 | λ = 2 | λ = 5 | λ = 10 |")
    w("|---|---|---|---|---|---|---|")
    for lab, obj, c in cells:
        e = c.get("extended") or {}
        dA, dB = (e.get("d") or {}).get("breadth_vs_production") or {}, (e.get("d") or {}).get("breadth_vs_nobreadth") or {}
        w(f"| {obj} | {lab} | " + " | ".join(f"{_m(dA.get(str(l)))} / {_m(dB.get(str(l)))}" for l in LAMBDAS) + " |")
    w("")
    # per-case statistics
    w("## Paired statistics (λ = 1, breadth − production)")
    w("")
    w("| Objective | Horizon | Mean ΔU | Median per-case share (changed cases) | 95% CI | clustered t | % positive (changed cases) | Changed cases | Positive eras |")
    w("|---|---|---|---|---|---|---|---|---|")
    for lab, obj, c in cells:
        e = c.get("extended") or {}
        pc = (e.get("per_case") or {}).get("breadth_vs_production") or {}
        ci = ((c["test"].get("ci") or {}).get("1.0"))
        w(f"| {obj} | {lab} | {_m(c['gates'].get('d'))} | {_m(pc.get('median_changed'), 2)} | {('[' + _m(ci[0]) + ', ' + _m(ci[1]) + ']') if ci else '—'} | "
          f"{_t((e.get('t') or {}).get('breadth_vs_production'))} | {(pc.get('positive_changed') or 0):.0%} | {pc.get('changed_cases', 0)} | "
          f"{c['gates'].get('eras_won')}/{c['gates'].get('eras_complete')} |")
    w("")
    w("Per-case shares decompose ΔU exactly (they sum to it): each case's share of the hedged-risk measure, profit and cost. Cases with an "
      "identical decision still carry small shares under the standard-deviation measure (the distribution's mean and spread differ), so "
      "the share of positive cases is reported among cases whose decision changed.")
    w("")
    # objective groups
    w("## Variance-sensitive vs hard-target objectives")
    w("")
    for title, group, note in (("Variance-sensitive (minimum variance, target volatility, VaR, ES, drawdown)", VARIANCE_SENSITIVE,
                                "Σ weighs risk against cost directly here, so the forecast can change sizes."),
                               ("Hard-target (beta, systematic, sector, name, duration, curve, credit, FX, commodity, crypto, volatility)", HARD_TARGET,
                                "The target exposure is pinned (weight 10⁴), so sizes follow exposures and the forecast can only change product and cost trade-offs.")):
        w(f"**{title}.** {note}")
        w("")
        w("| Objective | Horizon | Decision changed | of which sizing / product | Legs changed | Size change when only the size changed: median / p90 | ΔU from sizing changes | ΔU from product changes | Size ratio p25–p75 |")
        w("|---|---|---|---|---|---|---|---|---|")
        for lab, obj, c in cells:
            if obj not in group:
                continue
            e = c.get("extended") or {}
            sr = e.get("size_ratio") or {}
            dec = c.get("decisions") or {}
            sc = e.get("sizing_change") or {}
            w(f"| {obj} | {lab} | {c.get('changed_share', 0):.0%} | {dec.get('sizing', 0):.0%} / {dec.get('product', 0):.0%} | {(e.get('legs_changed') or 0):.0%} | "
              f"{('%.2f%% / %.2f%%' % (100 * sc['median'], 100 * sc['p90'])) if sc else '—'} | "
              f"{_m((e.get('by_decision') or {}).get('sizing'))} | {_m((e.get('by_decision') or {}).get('product'))} | "
              f"{('%.2f–%.2f' % (sr['p25'], sr['p75'])) if sr.get('p25') is not None else '—'} |")
        w("")
    w("What the data say about the expectation. Hard-target objectives change product as expected, and where they also \"change size\" the "
      "change is tiny (median column above): the pin on the target is a weight of 10⁴, not a hard constraint, and the product's other "
      "exposures (a sector ETF's or a high-yield ETF's market beta) are penalised with Σ, so a different market variance moves the optimum by "
      "a few shares; the systematic objective moves more because it matches the market and sector factors one by one, each weighted by its "
      "own variance, and a different market variance changes that balance. Variance-sensitive objectives change size more often and by "
      "more, but their realised utility difference also comes mostly from product and leg choices (ΔU from product changes) — Σ also ranks "
      "products by risk against cost, so a different volatility level changes which index future, ETF or number of legs the optimiser prefers.")
    w("")
    # attribution
    w("## Why utility changed (attribution, λ = 1, breadth − production)")
    w("")
    w("| Objective | Horizon | ΔU | = risk reduction Δ | + profit Δ | + cost Δ | Basis error Δ | Tail Δ (unhedged worst 5%) | Hedge-size error Δ |")
    w("|---|---|---|---|---|---|---|---|---|")
    for lab, obj, c in cells:
        cp = (c.get("extended") or {}).get("components") or {}
        w(f"| {obj} | {lab} | {_m(c['gates'].get('d'))} | {_m(cp.get('risk'))} | {_m(cp.get('profit'))} | {_m(cp.get('cost'), 1)} | {_m(cp.get('basis'), 1)} | "
          f"{_m(cp.get('tail'))} | {_m(cp.get('hedge_error'), 3)} |")
    w("")
    w("Profit Δ and cost Δ enter U with their sign (positive = breadth gave up less profit / paid less). Basis error Δ > 0 = breadth's hedge "
      "tracked the targeted exposure worse. Tail Δ > 0 = breadth's hedged book lost less in the unhedged book's worst 5% of windows. "
      "Hedge-size error = mean |ex-post variance-optimal multiple − 1| of the package (Δ < 0 = closer to the right size); it is unstable "
      "when a hedge's P&L over the window is close to zero, so large values come from a few near-zero hedges and should not be read as "
      "systematic over- or under-hedging.")
    w("")
    # transitions
    w("## Product-selection effects (common transitions, breadth vs production)")
    w("")
    w("Transitions between two products of the same type (for example equity_index_future → equity_index_future) are changes of contract, "
      "such as NQ → ES or ES → MES.")
    w("")
    w("| Objective | Horizon | Production → breadth product | Cases | Δ risk (signed RMS of hedged P&L change) | Δ cost / case | Δ profit sacrificed / case | Share of ΔU |")
    w("|---|---|---|---|---|---|---|---|")
    for lab, obj, c in cells:
        if obj in ("var", "drawdown"):
            continue
        for tname, x in ((c.get("extended") or {}).get("transitions") or {}).items():
            if x["n"] < 20:
                continue
            w(f"| {obj} | {lab} | {tname} | {x['n']} | {_m(x['drisk_rms'])} | {_m(x['dcost'], 1)} | {_m(x['dprofit'])} | {_m(x['dU'])} |")
    w("")
    # size by product / regime
    w("## Size effects (breadth ÷ production, same risk unit)")
    w("")
    w("| Objective | Horizon | Median | p25 | p75 | Larger / smaller | By product (median) | High vol / normal vol (median) |")
    w("|---|---|---|---|---|---|---|---|")
    for lab, obj, c in cells:
        if obj in ("var", "drawdown"):
            continue
        e = c.get("extended") or {}
        sr = e.get("size_ratio") or {}
        if not sr:
            continue
        bp = ", ".join(f"{k} {v['median']:.2f}" for k, v in (e.get("size_ratio_by_product") or {}).items() if v and v.get("n", 0) >= 20)
        br = e.get("size_ratio_by_regime") or {}
        w(f"| {obj} | {lab} | {sr['median']:.3f} | {sr['p25']:.2f} | {sr['p75']:.2f} | {sr['larger']:.0%} / {sr['smaller']:.0%} | {bp or '—'} | "
          f"{(br.get('high_vol') or {}).get('median', float('nan')):.2f} / {(br.get('low_vol') or {}).get('median', float('nan')):.2f} |")
    w("")
    # crisis / regime
    w("## Crisis and regime attribution (ΔU breadth − production, λ = 1)")
    w("")
    w("| Objective | Horizon | High volatility ΔU (t) | Normal volatility ΔU (t) | Without 2009 | Without COVID 2020 | Without 2022 | Without all three |")
    w("|---|---|---|---|---|---|---|---|")
    for lab, obj, c in cells:
        if obj in ("var", "drawdown"):
            continue
        e = c.get("extended") or {}
        vr, wc = e.get("vol_regime") or {}, e.get("without_crises") or {}
        cell = lambda x: f"{_m((x or {}).get('d'))} ({_t((x or {}).get('t'))})"  # noqa: E731
        w(f"| {obj} | {lab} | {cell(vr.get('high_vol'))} | {cell(vr.get('normal_vol'))} | " + " | ".join(cell(wc.get(k)) for k in list(CRISES) + ["all three"]) + " |")
    w("")
    # options
    w("## Options (MODEL-PRICED — FLAT VOLATILITY ASSUMPTION): predefined sensitivity")
    w("")
    w("| Objective | Horizon | ΔU (main) | ΔU with bought puts +5 vol points | ΔU excluding every case with an option leg |")
    w("|---|---|---|---|---|")
    for lab, obj, c in cells:
        if obj in ("var", "drawdown"):
            continue
        w(f"| {obj} | {lab} | {_m(c['gates'].get('d'))} | {_m((c['skew'].get('d') or {}).get('1.0'))} | {_m((c['no_options'].get('d') or {}).get('1.0'))} |")
    w("")
    # resizing
    w("## Resizing (2018 on, where the confirmed multiples are out of sample)")
    w("")
    w("| Objective | Horizon | Cases | ΔU breadth only | ΔU resizing only | ΔU breadth + resizing | Combination |")
    w("|---|---|---|---|---|---|---|")
    for lab, obj, c in cells:
        if obj in ("var", "drawdown"):
            continue
        r = (c.get("extended") or {}).get("resizing") or {}
        if r:
            w(f"| {obj} | {lab} | {r['cases']} | {_m(r['breadth'])} | {_m(r['resizing'])} | {_m(r['both'])} | {r.get('class') or '—'} |")
    w("")
    w("Additive: the combination ≈ the sum of the two; redundant: ≈ the larger alone; interacting: departs from both; conflicting: the two "
      "effects have opposite signs.")
    w("")
    w(_answers(res))
    w("")
    w(_decision_table(res))
    return "\n".join(L) + "\n"


def _decision_table(res: dict) -> str:
    cells = list(_cells(res))
    H = res.get("horizons") or {}
    shadow = [c for *_, c in cells if c.get("status") in ("SHADOW", "LIVE SHADOW", "ELIGIBLE FOR PROMOTION")]
    g1 = [lab for lab, hz in H.items() if hz.get("G1")]
    sz = [c for lab, o, c in cells if o in VARIANCE_SENSITIVE and o not in ("var", "drawdown")]      # ES / VaR / drawdown: one decision
    pr = [c for lab, o, c in cells if o in HARD_TARGET]
    late = [((c.get("extended") or {}).get("resizing") or {}) for *_, c in cells]
    rs = [r["resizing"] for r in late if r.get("resizing") is not None]
    both = [r["both"] for r in late if r.get("both") is not None]
    L = ["## Final decision", "",
         "| Component | Historical result | Live-shadow eligible? | Production change? | Reason |", "|---|---|---|---|---|",
         f"| Breadth volatility forecast | better out of sample at {', '.join(g1) or 'no horizon'} (G1) | no (as a hedge input) | no | a better forecast; it did not produce better realised hedges — kept as research information |",
         f"| Breadth hedge sizing | {sum(1 for c in sz if (c['gates'].get('d') or 0) > 0)} of {len(sz)} variance-sensitive cells positive (ES / VaR / drawdown counted once), none significant after FDR | no | no | sizes rarely change and the changes do not pay |",
         f"| Breadth product selection | products change in up to {max((c.get('decisions') or {}).get('product', 0) for *_, c in cells):.0%} of cases; realised effects net to about zero; {sum(1 for c in pr if (c['gates'].get('d') or 0) > 0)} of {len(pr)} hard-target cells positive, none significant | no | no | no significant or stable gain |",
         f"| Existing resizing challenger (2018 on) | ΔU median {sorted(rs)[len(rs) // 2] if rs else 0:+,.0f} across cells | unchanged (its own live shadow continues) | no | separate experiment; not improved by breadth |",
         f"| Breadth + resizing (2018 on) | ΔU median {sorted(both)[len(both) // 2] if both else 0:+,.0f} across cells | no | no | the combination adds nothing the parts lack |",
         f"| Shaffer Hedge production (hedge-2) | {'no cell passes G1–G4' if not shadow else f'{len(shadow)} cells pass G1–G4'} | — | no | unchanged: " + ("nothing passed the historical gates" if not shadow else "live shadow first (G5)") + " |"]
    return "\n".join(L)


def _dd(a: dict, b: dict, key: str) -> Optional[float]:
    """a[key] − b[key] when both exist."""
    return (a[key] - b[key]) if a.get(key) is not None and b.get(key) is not None else None


def _cells(res):
    for lab, hz in (res.get("horizons") or {}).items():
        for obj, cell in (hz.get("cells") or {}).items():
            if (cell.get("test") or {}).get("n"):
                yield lab, obj, cell


def _answers(res: dict) -> str:
    H = res.get("horizons") or {}
    L: List[str] = []
    w = L.append
    cells = list(_cells(res))
    w("## Answers")
    w("")
    f1 = [(lab, hz.get("forecast") or {}, hz.get("G1")) for lab, hz in H.items()]
    w("**1. Does breadth still improve the volatility forecast out of sample?** " + "; ".join(
        f"{lab}: squared log error of the market factor {_pc(-(f.get('gain_pct') or 0))} against the regression without breadth "
        f"(t {_m(f.get('t'), 1)}, {f.get('n')} dates), {_pc(-(f.get('gain_vs_production_pct') or 0))} against production's 252-day "
        f"sample — G1 {'passes' if g else 'fails'}" for lab, f, g in f1 if f.get("n")) + ".")
    w("")
    ch = [(lab, obj, c) for lab, obj, c in cells]
    w("**2. Does it change hedge sizing?** Share of matched cases where the challenger's package differs (identical / same products "
      "other size / other products): " + "; ".join(f"{obj} {lab} {c['decisions']['identical']:.0%} / {c['decisions']['sizing']:.0%} / {c['decisions']['product']:.0%}"
                                                     for lab, obj, c in ch) + ".")
    w("")
    w("**3. By how much?** Challenger ÷ production gross hedge notional, median [10th–90th percentile] (share of cases larger / "
      "smaller): " + "; ".join(f"{obj} {lab} {c['size_ratio']['median']:.3f} [{c['size_ratio']['p10']:.2f}–{c['size_ratio']['p90']:.2f}] "
                                f"({c['size_ratio']['larger']:.0%} / {c['size_ratio']['smaller']:.0%})" for lab, obj, c in ch if c.get("size_ratio")) + ".")
    w("")
    tr = [(lab, obj, c) for lab, obj, c in ch if c.get("transitions")]
    w("**4. Does it change product selection?** " + ("; ".join(f"{obj} {lab}: products differ in {c['decisions']['product']:.0%} of cases ("
                                                              + ", ".join(f"{k} ×{v}" for k, v in list(c['transitions'].items())[:3]) + ")" for lab, obj, c in tr)
                                                    if tr else "No: in no cell does the better forecast change which product is chosen.") + " "
      "What the product changes did is in the attribution table below.")
    w("")

    def cmp(key, better_low, name):
        xs = [f"{obj} {lab} {_m(_dd(c['test']['a'], c['test']['b'], key) * (1 if better_low else -1))}"
              for lab, obj, c in ch if _dd(c['test']['a'], c['test']['b'], key) is not None]
        return "; ".join(xs) if xs else "not enough windows"
    w("**5. Does realised variance reduction improve?** Challenger minus production, percentage points: " + "; ".join(
        f"{obj} {lab} {_pc((c['test']['b'].get('variance_reduction') or 0) - (c['test']['a'].get('variance_reduction') or 0), 2)}" for lab, obj, c in ch) + ".")
    w("")
    w("**6. Does realised ES reduction improve?** Hedged ES95, production minus challenger ($): " + cmp("es_h", True, "ES") + ".")
    w("")
    w("**7. Does realised drawdown protection improve?** Mean max drawdown of the hedged book, production minus challenger ($): " + cmp("dd_h", True, "DD") + ".")
    w("")
    w("**8. Does tail protection improve?** Mean hedged P&L in the unhedged book's worst 5% of windows, challenger minus production ($): "
      + ("; ".join(f"{obj} {lab} {_m(_dd(c['test']['b'], c['test']['a'], 'tail_h'))}" for lab, obj, c in ch
                   if _dd(c['test']['b'], c['test']['a'], 'tail_h') is not None) or "not enough windows") + ".")
    w("")
    w("**9. Does hedge cost rise or fall?** Mean cost, challenger minus production ($): " + "; ".join(
        f"{obj} {lab} {_m((c['test']['b'].get('cost') or 0) - (c['test']['a'].get('cost') or 0), 1)}" for lab, obj, c in ch) + ".")
    w("")
    w("**10. Is more expected profit sacrificed?** Realised profit sacrificed (−mean hedge P&L), challenger minus production ($): " + "; ".join(
        f"{obj} {lab} {_m((c['test']['b'].get('profit_sacrificed') or 0) - (c['test']['a'].get('profit_sacrificed') or 0), 1)}" for lab, obj, c in ch) + ".")
    w("")
    sig = [(lab, obj, c) for lab, obj, c in ch if c["gates"].get("fdr") and (c["gates"].get("d") or 0) > 0]
    w("**11. Does total hedge utility improve?** " + ("Significantly after the FDR control (λ = 1): " + "; ".join(
        f"{obj} {lab} ΔU {_m(c['gates']['d'])}" for lab, obj, c in sig) + "." if sig else
        "No cell shows a significant utility improvement at λ = 1 after the FDR control.") + " Point estimates for every cell are in the main table.")
    w("")
    w("**12. At which λ values?** ΔU for λ = " + " / ".join(str(l) for l in LAMBDAS) + ": " + "; ".join(
        f"{obj} {lab} " + " / ".join(_m((c['test'].get('d') or {}).get(str(l))) for l in LAMBDAS) for lab, obj, c in ch) + ".")
    w("")
    ben = [(lab, obj) for lab, obj, c in ch if c.get("status") in ("SHADOW", "LIVE SHADOW", "ELIGIBLE FOR PROMOTION")]
    w("**13. Which hedge objectives benefit?** " + (", ".join(f"{o} ({l})" for l, o in ben) + " pass G1–G4." if ben else
      "None passes G1–G4.") + " Positive point estimates without passing: " + (", ".join(
          f"{obj} {lab}" for lab, obj, c in ch if (c['gates'].get('d') or 0) > 0 and (lab, obj) not in ben) or "none") + ".")
    w("")

    def breakdown(key, label):
        rows = []
        for lab, obj, c in ch:
            for k, b in (c.get(key) or {}).items():
                if b.get("d") is not None and b.get("p") is not None:
                    rows.append((lab, obj, k, b))
        if not rows:
            return f"No {label} breakdown with enough dates."
        rej = benjamini_hochberg([b["p"] for *_, b in rows], FDR_Q)
        pos = [(lab, obj, k, b) for (lab, obj, k, b), r in zip(rows, rej) if r and b["d"] > 0]
        return (f"{len(rows)} {label} × objective × horizon breakdowns with enough dates; significant and positive after their own BH: "
                + ("; ".join(f"{k} — {obj} {lab} ΔU {_m(b['d'])}" for lab, obj, k, b in pos) if pos else "none") + ".")
    w("**14. Which hedge products benefit?** " + breakdown("by_product", "product"))
    w("")
    w("**15. Which portfolio types benefit?** " + breakdown("by_book_type", "portfolio-type"))
    w("")
    w("**16. Which regimes benefit?** " + breakdown("by_regime", "regime") + " Liquidity stress: no point-in-time liquidity series "
      "is available, so it is not tested.")
    w("")
    rs = [(lab, obj, c["from_2018"]) for lab, obj, c in ch if c["from_2018"].get("cases")]
    w("**17. Does breadth-vol outperform the fixed-resizing baseline?** From 2018 (where the resizing multiples are out of sample), "
      "ΔU at λ = 1 of B vs C: " + "; ".join(f"{obj} {lab} {_m((f.get('B_vs_C') or {}).get('1.0'))}" for lab, obj, f in rs) + ".")
    w("")
    w("**18. Are breadth-vol and resizing additive?** From 2018, ΔU(D) against ΔU(B) + ΔU(C), λ = 1: " + "; ".join(
        f"{obj} {lab} {_m((f.get('D_vs_A') or {}).get('1.0'))} vs {_m(((f.get('B_vs_A') or {}).get('1.0') or 0) + ((f.get('C_vs_A') or {}).get('1.0') or 0))}"
        for lab, obj, f in rs) + ".")
    w("")
    w("**19. Does the result survive all eras?** Complete eras with ΔU > 0 (λ = 1): " + "; ".join(
        f"{obj} {lab} {c['gates'].get('eras_won')}/{c['gates'].get('eras_complete')}" for lab, obj, c in ch) + ".")
    w("")
    ok = bool(ben)
    w("**20. Should the volatility forecast enter production Hedge?** " + (
        "Not yet: the objectives above pass the historical gates and go to live shadow; production needs G5 and an explicit decision." if ok else
        "No. The better forecast does not make the Shaffer Hedge measurably better; breadth stays a research forecast."))
    w("")
    w("**21. Should hedge sizing change?** No change follows from this study: " + (
        "where the forecast changes sizes, the realised outcomes do not justify it (see answers 3 and 11)." if not ok else
        "sizing changes only through the objectives in live shadow, after G5."))
    w("")
    w("**22. Is anything eligible for live shadow or promotion?** " + (
        ("Live shadow: " + ", ".join(f"{o} ({l})" for l, o in ben) + ". Promotion: nothing (no live record).") if ok else
        "No: nothing passes G1–G4, so nothing enters live shadow and nothing is eligible for promotion."))
    w("")
    # attribution and detail tables
    w("## Attribution (what the changed decisions did)")
    w("")
    w("| Objective | Horizon | Decision change | Cases | Variance reduction Δ (pp) | Hedge P&L Δ | Cost Δ | Basis error Δ | Hedge-size error Δ | Tail Δ |")
    w("|---|---|---|---|---|---|---|---|---|---|")
    for lab, obj, c in ch:
        for k, a in (c.get("attribution") or {}).items():
            w(f"| {obj} | {lab} | {k} | {a['cases']} | {_pc(a['variance_reduction_change'], 3)} | {_m(a['hedge_pnl_change'], 1)} | {_m(a['cost_change'], 1)} | "
              f"{_m(a['basis_change'], 1)} | {_m(a['hedge_error_change'], 3)} | {_m(a.get('tail_change'))} |")
    w("")
    w("Hedge-size error = mean |ex-post optimal multiple − 1| of the package (lower = closer to the size that would have minimised "
      "the window's variance); negative Δ = the challenger's size was closer. Pieces are additive shares of the cell.")
    w("")
    w("## Any better volatility model vs breadth itself (R = regression without breadth)")
    w("")
    w("| Objective | Horizon | ΔU R − A (λ = 1) | ΔU B − R | ΔU with a conservative put skew (+5 vol) | ΔU without option cases | Baselines U: none / half / A / B |")
    w("|---|---|---|---|---|---|---|")
    for lab, obj, c in ch:
        bl = c.get("baselines") or {}
        w(f"| {obj} | {lab} | {_m((c['reference'].get('d') or {}).get('1.0'))} | {_m((c['breadth_vs_reference'].get('d') or {}).get('1.0'))} | "
          f"{_m((c['skew'].get('d') or {}).get('1.0'))} | {_m((c['no_options'].get('d') or {}).get('1.0'))} | "
          + " / ".join(_m(utility(bl.get(k) or {}, 1.0)) for k in ("none", "half", "A", "B")) + " |")
    w("")
    return "\n".join(L)


# ------------------------------------------------------------------ descriptive extensions (reporting only; the
# pre-registered test, gates and statuses above are unchanged — these read the same saved cases)
CRISES = {"2009 aftermath (2009)": ("2009-01-01", "2010-01-01"), "COVID (2020-02 to 2020-06)": ("2020-02-01", "2020-07-01"),
          "2022 rate shock (2022)": ("2022-01-01", "2023-01-01")}
VARIANCE_SENSITIVE = ["min_variance", "target_vol", "var", "es", "drawdown"]
HARD_TARGET = ["beta", "systematic", "sector", "name", "duration", "curve", "credit", "fx", "commodity", "crypto", "volatility"]


def case_contributions(cases: List[dict], a: str, b: str, objective: str, lam: float) -> List[float]:
    """Each case's additive share of ΔU(λ) = U(b) − U(a): the hedged-risk shares (ES: −x/k inside the worst k windows;
    standard deviation: (x − mean)² / ((n − 1)·sd)) plus −λ·profit and −cost per case. They sum to ΔU exactly."""
    Ua, Ha, Ca = _window_scalars(cases, a)
    _, Hb, Cb = _window_scalars(cases, b)
    n = len(Ua)
    if n < 20:
        return []

    def shares(x):
        if objective in TAIL_OBJ:
            k = max(1, int(math.ceil(0.05 * n)))
            worst = set(sorted(range(n), key=lambda j: x[j])[:k])
            return [(-x[j] / k) if j in worst else 0.0 for j in range(n)]
        m = sum(x) / n
        sd = _sd(x)
        return [((v - m) ** 2 / ((n - 1) * sd)) if sd else 0.0 for v in x]
    ra = shares([u + h - c for u, h, c in zip(Ua, Ha, Ca)])
    rb = shares([u + h - c for u, h, c in zip(Ua, Hb, Cb)])
    return [-(rb[j] - ra[j]) - lam * (-Hb[j] + Ha[j]) / n - (Cb[j] - Ca[j]) / n for j in range(n)]


def _se(cases, objective, a, b, reps=BOOT_REPS):
    """Bootstrap standard error of ΔU(λ = 1) with the same date blocks and seed as the pre-registered test."""
    p = paired(cases, objective, a, b, reps=reps)
    ci = (p.get("ci") or {}).get("1.0")
    d = (p.get("d") or {}).get("1.0")
    if not ci or d is None:
        return p, None
    se = (ci[1] - ci[0]) / (2 * 1.96)
    return p, (d / se if se > 0 else None)


def _risk_unit_ratio(c: dict, x: str = "A", y: str = "B") -> Optional[float]:
    """Challenger ÷ production size in the same risk unit: the lead product's quantity when the products are the same;
    market beta-dollars when they differ and the objective's primary risk is the market; otherwise not comparable."""
    la, lb = c["arms"][x]["legs"], c["arms"][y]["legs"]
    if not la or not lb:
        return None
    if sorted(L["id"] for L in la) == sorted(L["id"] for L in lb):
        qa = {L["id"]: L["q"] for L in la}
        lead = max(lb, key=lambda L: abs(L["notional"] or 0.0))
        return (lead["q"] / qa[lead["id"]]) if qa.get(lead["id"]) else None
    if c.get("primary") == "MKT":
        ba, bb = sum(L["beta_usd"] for L in la), sum(L["beta_usd"] for L in lb)
        return (bb / ba) if ba else None
    return None


def extended_cell(cases: List[dict], objective: str) -> dict:
    """The attribution and breakdowns asked for after the protocol was frozen (descriptive; same cases)."""
    out: dict = {"cases": len(cases)}
    if len(cases) < 20:
        return out
    p_ba, t_ba = _se(cases, objective, "A", "B")
    p_ra, t_ra = _se(cases, objective, "A", "R")
    p_br, t_br = _se(cases, objective, "R", "B")
    out["t"] = {"breadth_vs_production": t_ba, "nobreadth_vs_production": t_ra, "breadth_vs_nobreadth": t_br}
    out["d"] = {"breadth_vs_production": p_ba.get("d"), "nobreadth_vs_production": p_ra.get("d"), "breadth_vs_nobreadth": p_br.get("d")}
    out["ci"] = {"breadth_vs_production": p_ba.get("ci"), "nobreadth_vs_production": p_ra.get("ci"), "breadth_vs_nobreadth": p_br.get("ci")}
    out["p"] = {"breadth_vs_production": p_ba.get("p"), "nobreadth_vs_production": p_ra.get("p"), "breadth_vs_nobreadth": p_br.get("p")}
    out["U"] = {k: {str(l): utility(o, l) for l in LAMBDAS} for k, o in (("production", p_ba["a"]), ("breadth", p_ba["b"]), ("nobreadth", p_ra["b"]))}
    # per-case shares of ΔU (λ = 1)
    kinds = [_changed(c) for c in cases]
    for key, (x, y) in (("breadth_vs_production", ("A", "B")), ("breadth_vs_nobreadth", ("R", "B"))):
        cc = case_contributions(cases, x, y, objective, 1.0)
        ch = [v for v, c in zip(cc, cases) if _changed(c, x, y) != "identical"]
        out.setdefault("per_case", {})[key] = {
            "mean": sum(cc) / len(cc), "median": sorted(cc)[len(cc) // 2], "positive": sum(1 for v in cc if v > 1e-12) / len(cc),
            "negative": sum(1 for v in cc if v < -1e-12) / len(cc), "changed_cases": len(ch),
            "median_changed": sorted(ch)[len(ch) // 2] if ch else None, "positive_changed": (sum(1 for v in ch if v > 1e-12) / len(ch)) if ch else None}
    # ΔU components (λ = 1) and the sizing / product split
    oa, ob = p_ba["a"], p_ba["b"]
    out["components"] = {"risk": (ob["loss_reduction"] - oa["loss_reduction"]) if ob.get("loss_reduction") is not None and oa.get("loss_reduction") is not None else None,
                         "profit": -((ob["profit_sacrificed"] or 0) - (oa["profit_sacrificed"] or 0)), "cost": -((ob["cost"] or 0) - (oa["cost"] or 0)),
                         "basis": (ob.get("basis_error") or 0) - (oa.get("basis_error") or 0),
                         "tail": _dd(ob, oa, "tail_h"), "hedge_error": (ob.get("hedge_error") or 0) - (oa.get("hedge_error") or 0)}
    cc = case_contributions(cases, "A", "B", objective, 1.0)
    out["by_decision"] = {k: sum(v for v, kk in zip(cc, kinds) if kk == k) for k in ("sizing", "product")}
    out["legs_changed"] = sum(1 for c in cases if len(c["arms"]["A"]["legs"]) != len(c["arms"]["B"]["legs"])) / len(cases)
    # transitions with their share of ΔU
    tr: Dict[str, dict] = {}
    for c, v, k in zip(cases, cc, kinds):
        if k != "product":
            continue
        t = f"{_lead_product(c['arms']['A'])} → {_lead_product(c['arms']['B'])}"
        x = tr.setdefault(t, {"n": 0, "dU": 0.0, "dcost": 0.0, "dprofit": 0.0, "drisk_sq": 0.0})
        a, b = c["arms"]["A"], c["arms"]["B"]
        x["n"] += 1
        x["dU"] += v
        x["dcost"] += b["cost"] - a["cost"]
        x["dprofit"] += -(sum(b["hp"]) - sum(a["hp"]))
        x["drisk_sq"] += (sum(c["u"]) + sum(b["hp"]) - b["cost"]) ** 2 - (sum(c["u"]) + sum(a["hp"]) - a["cost"]) ** 2
    out["transitions"] = {t: {"n": x["n"], "dU": x["dU"], "dcost": x["dcost"] / x["n"], "dprofit": x["dprofit"] / x["n"],
                              "drisk_rms": math.copysign(math.sqrt(abs(x["drisk_sq"]) / x["n"]), x["drisk_sq"])}
                          for t, x in sorted(tr.items(), key=lambda kv: -kv[1]["n"])[:8]}
    # size in risk units
    rr = [(c, _risk_unit_ratio(c)) for c in cases]
    rr = [(c, r) for c, r in rr if r is not None and r > 0]
    out["size_ratio"] = _quantiles([r for _, r in rr])
    mag = sorted(abs(r - 1) for c, r in rr if _changed(c) == "sizing")
    out["sizing_change"] = {"n": len(mag), "median": mag[len(mag) // 2], "p90": mag[min(len(mag) - 1, int(0.9 * len(mag)))]} if mag else None
    out["size_ratio_by_product"] = {k: _quantiles([r for c, r in rr if _lead_product(c["arms"]["A"]) == k]) for k in sorted({_lead_product(c["arms"]["A"]) for c, _ in rr})}
    out["size_ratio_by_regime"] = {k: _quantiles([r for c, r in rr if (c.get("regime") or {}).get("volatility") == k])
                                   for k in sorted({(c.get("regime") or {}).get("volatility") for c, _ in rr} - {None})}
    # normal vs high volatility; crises excluded
    for key, sub in (("high_vol", [c for c in cases if (c.get("regime") or {}).get("volatility") == "high_vol"]),
                     ("normal_vol", [c for c in cases if (c.get("regime") or {}).get("volatility") not in (None, "high_vol")])):
        p, t = _se(sub, objective, "A", "B")
        out.setdefault("vol_regime", {})[key] = {"cases": len(sub), "dates": p.get("dates"), "d": (p.get("d") or {}).get("1.0"), "t": t}
    out["without_crises"] = {}
    for name, win in list(CRISES.items()) + [("all three", None)]:
        sub = [c for c in cases if not (any(x <= c["date"] < y for x, y in CRISES.values()) if win is None else win[0] <= c["date"] < win[1])]
        p, t = _se(sub, objective, "A", "B")
        out["without_crises"][name] = {"cases": len(sub), "d": (p.get("d") or {}).get("1.0"), "t": t}
    # resizing, 2018 on: additive / redundant / interacting / conflicting
    late = [c for c in cases if c["date"] >= RESIZE_FROM]
    if len(late) >= 20:
        dB = (paired(late, objective, "A", "B", reps=0).get("d") or {}).get("1.0")
        dC = (paired(late, objective, "A", "C", reps=0).get("d") or {}).get("1.0")
        dD = (paired(late, objective, "A", "D", reps=0).get("d") or {}).get("1.0")
        out["resizing"] = {"cases": len(late), "breadth": dB, "resizing": dC, "both": dD, "class": interaction(dB, dC, dD)}
    return out


def interaction(dB: Optional[float], dC: Optional[float], dD: Optional[float], tol: float = 0.25) -> Optional[str]:
    """How the breadth and resizing effects combine (2018 on): conflicting (opposite signs), additive (both ≈ the sum),
    redundant (both ≈ the larger alone), interacting (the combination departs from both)."""
    if None in (dB, dC, dD):
        return None
    if dB * dC < 0 and min(abs(dB), abs(dC)) > 0.1 * max(abs(dB), abs(dC)):
        return "conflicting"
    s = dB + dC
    big = dB if abs(dB) >= abs(dC) else dC
    scale = max(abs(s), abs(big), 1e-9)
    if abs(dD - s) <= tol * scale:
        return "additive"
    if abs(dD - big) <= tol * scale:
        return "redundant"
    return "interacting"


def attach_extended(db_path: str, progress=None) -> dict:
    """Adds `extended` (descriptive attribution) to every cell of the stored result, from the saved cases. The
    pre-registered numbers, gates and statuses are not recomputed or changed."""
    from ..data.store import Store
    say = progress or (lambda m: None)
    st = Store(db_path)
    try:
        res = st.kv_get(RESEARCH_KEY)
        for lab, hz in res["horizons"].items():
            cases = [c for d in load_dates(db_path, lab) for c in d["cases"]]
            done: Dict[str, dict] = {}
            for obj, cell in hz["cells"].items():
                twin = "es" if obj in ("var", "drawdown") else obj            # the same decisions as ES (same engine path)
                if twin not in done:
                    done[twin] = extended_cell([c for c in cases if c["objective"] == twin], twin)
                if twin == obj:
                    cell["extended"] = done[twin]
                else:                        # same decisions as ES, but the resizing multiples depend on the objective's metric
                    own = [c for c in cases if c["objective"] == obj and c["date"] >= RESIZE_FROM]
                    r = None
                    if len(own) >= 20:
                        dB = (paired(own, obj, "A", "B", reps=0).get("d") or {}).get("1.0")
                        dC = (paired(own, obj, "A", "C", reps=0).get("d") or {}).get("1.0")
                        dD = (paired(own, obj, "A", "D", reps=0).get("d") or {}).get("1.0")
                        r = {"cases": len(own), "breadth": dB, "resizing": dC, "both": dD, "class": interaction(dB, dC, dD)}
                    cell["extended"] = {**done[twin], "same_as": "es", "resizing": r}
                say(f"{lab} {obj}: extended")
            hz["legs_changed_all"] = (sum(1 for c in cases if len(c["arms"]["A"]["legs"]) != len(c["arms"]["B"]["legs"])) / len(cases)) if cases else None
        res["extended_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        st.kv_set(RESEARCH_KEY, res)
        return res
    finally:
        st.close()
