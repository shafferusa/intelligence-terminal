"""Profit-aware hedge designs: the Shaffer Score × Shaffer Hedge interaction.

A hedge is not judged by how much variance it removes at any cost. For a trade (and optionally the book it joins) we
compare structurally different hedge designs over the trade's horizon h:

    none · partial beta 25% / 50% · full beta · sector hedge · index put · tail put on the asset · volatility hedge

each sized by the Shaffer Hedge engine for that objective, and score them on

    U(design) = [Risk(before) − Risk(after)] − λ · [E P&L(before) − E P&L(after)] − cost

Risk is the measure of the selected objective: ES 95% of the h-day P&L for tail / VaR / drawdown / crash objectives
(the default), its standard deviation for variance, beta and neutralising objectives. The P&L distribution is a
historical simulation: every overlapping h-day window of the last SIM_YEARS years gives one scenario of cumulative
factor moves (market, styles, sectors, rates, credit, currencies, commodities, crypto, volatility and each asset's
own idiosyncratic move), DEMEANED so the past drift of the sample is not smuggled in as a forecast. Linear products
move with their exposures; options are fully re-priced at the horizon (underlying from its exposures, implied vol
from the VIX move, time to expiry reduced by h).

Expected P&L comes from explicit drifts, never from the scenario sample:
    every product      a CAPM-style prior: its market beta × the market's long-run average return (SPY over all
                       available history). An asset's own past average is NOT used: a stock that doubled for ten
                       years would otherwise be "expected" to keep doing so (survivorship)
    the traded asset   plus the Shaffer evidence part of the expected return at h, only when that horizon's
                       calibration supports it (the source is shown)
    structural drifts  volatility ETPs and inverse / leveraged funds keep their own long-run average (VXX's roll
                       decay is a property of the product, not a past accident)
    equity futures     the index's prior minus the bill rate (futures earn the excess return)

So with a strong, supported bullish score a full beta hedge gives up a lot of expected profit and a tail put may
win; with a neutral or negative score, reducing risk costs little profit and bigger hedges win. With no verified
Shaffer edge the profit side is only the asset's long-run average — the panel says so. The user decides.
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional

from . import engine as E
from . import pricing as px

SIM_YEARS = 10
LAMBDAS = (0.5, 1.0, 2.0, 5.0, 10.0)
TAIL_OBJ = {"crash", "es", "var", "drawdown", None, "", "auto"}
DESIGNS = [
    {"key": "none", "label": "No hedge", "objective": None},
    {"key": "main50", "label": "Main risk, half (engine's choice)", "objective": "auto", "reduction": 0.5},
    {"key": "main100", "label": "Main risk, fully (engine's choice)", "objective": "auto", "reduction": 1.0},
    {"key": "beta25", "label": "Partial beta hedge (25%)", "objective": "beta", "reduction": 0.25, "linear": True},
    {"key": "beta50", "label": "Partial beta hedge (50%)", "objective": "beta", "reduction": 0.5, "linear": True},
    {"key": "beta100", "label": "Full beta hedge", "objective": "beta", "reduction": 1.0, "linear": True},
    {"key": "sector", "label": "Sector hedge (50%)", "objective": "sector", "reduction": 0.5, "linear": True},
    {"key": "index_put", "label": "Index put (covers half a −20% market fall)", "objective": "crash", "reduction": 0.5, "only": "index_option"},
    {"key": "own_put", "label": "Tail put on the asset", "objective": "name", "reduction": 0.5, "only": "own_option"},
    {"key": "vol", "label": "Volatility hedge (long VIX futures ETF)", "objective": "crash", "reduction": 0.5, "only": "vol"},
]


STRUCTURAL = {"VXX", "UVXY", "VIXY", "SVXY", "SH", "PSQ", "DOG", "RWM", "SDS", "SQQQ", "SPXU", "TQQQ", "SSO", "QLD", "UPRO"}
MARKET = "SPY"


def _beta(m, rk, asset_id: str) -> Optional[float]:
    try:
        return rk.unit_exposures(asset_id)["exposures"].get("MKT") or 0.0          # $ of market exposure per $1 held
    except Exception:  # noqa: BLE001
        return None


def prior_drift(m, rk, asset_id: str, h: int) -> Optional[float]:
    """Expected simple return over h sessions with no forecast: β × the market's long-run average (own average for
    products whose drift is structural)."""
    if asset_id in STRUCTURAL:
        return _drift(m, asset_id, h)
    mk = _drift(m, MARKET, h, years=40)
    b = 1.0 if asset_id == MARKET else _beta(m, rk, asset_id)
    return (b * mk) if (mk is not None and b is not None) else None


def _drift(m, asset_id: str, h: int, years: int = SIM_YEARS) -> Optional[float]:
    """Long-run average simple return over h sessions (from the mean daily USD log return)."""
    r = m.usd_returns(asset_id)
    seg = [x for x in r[max(1, m.i - 252 * years):m.i + 1] if x is not None]
    if len(seg) < 252:
        return None
    lr = [math.log1p(x) for x in seg if x > -1]
    return math.exp(sum(lr) / len(lr) * h) - 1


def expected_return(research, asset_id: str, lab: str, h: int, m, use_shaffer: bool = True, rk=None) -> dict:
    """The traded asset's expected return at h and where it comes from: the no-forecast prior, plus the Shaffer
    evidence part only where the calibration supports it."""
    full = {}
    try:
        full = (research.shaffer_full(asset_id).get("horizons") or {}).get(lab) or {}
    except Exception:  # noqa: BLE001
        full = {}
    prior = prior_drift(m, rk, asset_id, h) if rk is not None else None
    out = {"calibrated": full.get("calibrated"), "raw": full.get("raw"), "confidence": full.get("confidence"),
           "evidence": full.get("expected_edge"), "prior": prior, "own_long_run": _drift(m, asset_id, h),
           "oos_t": ((full.get("oos") or {}).get("t") if isinstance(full.get("oos"), dict) else None)}
    try:        # historical reliability of a score this size (ML Lab weight research) — shown, not used by the hedge math
        from ..engine.weights import reliability
        out["reliability"] = reliability(research.store, lab, full.get("raw"))
    except Exception:  # noqa: BLE001
        out["reliability"] = None
    try:        # Shaffer Alpha / Directional research outputs — shown, not used by the hedge math in this phase
        from ..engine.directional import hedge_view
        out["research"] = hedge_view(research.store, research, asset_id, lab, full.get("raw"))
    except Exception:  # noqa: BLE001
        out["research"] = None
    ev = full.get("expected_edge") if use_shaffer else None
    if prior is None:
        out.update(value=None, source="no expected return available")
    elif ev is not None:
        out.update(value=prior + ev, source="market prior (β × long-run market return) + Shaffer evidence (calibration supported)")
    else:
        out.update(value=prior, source="market prior only (β × long-run market return): no supported Shaffer evidence at this horizon")
    return out


def scenarios(research, m, rk, h: int, assets: List[str], years: int = SIM_YEARS, step: int = 5) -> List[Dict[str, float]]:
    """Demeaned cumulative h-day factor moves (plus the listed assets' idiosyncratic moves), one per window."""
    from .crisis import cumulative_shock
    from .risk import factor_series
    F = factor_series(research)
    lo = max(1, m.i - 252 * years)
    out = []
    for k in range(lo, m.i - h + 1, step):
        shock, _ = cumulative_shock(F, k, k + h)
        for a in assets:
            idio = (rk._idio.get(a) or [])[k + 1:k + h + 1]
            vals = [x for x in idio if x is not None]
            if len(vals) >= h / 2:
                shock[f"IDIO:{a}"] = sum(vals)
        out.append(shock)
    keys = {f for s in out for f in s}
    for f in keys:
        xs = [s[f] for s in out if f in s]
        mu = sum(xs) / len(xs) if xs else 0.0
        for s in out:
            if f in s:
                s[f] -= mu
    return out


def _pnl(priced: List[dict], shock: Dict[str, float], m, rk, h: int, drifts: Dict[str, float], vix0: Optional[float], rf: float) -> float:
    tot = 0.0
    for p in priced:
        inst, pr = p.get("inst"), p.get("priced")
        if inst is None or pr is None or pr.price is None:
            continue
        if inst.type == "OPTION":
            S = pr.inputs["spot"]["value"]
            e = rk.unit_exposures(inst.underlying)["exposures"]
            ret = sum(v * shock.get(f, 0.0) for f, v in e.items()) + (drifts.get(inst.underlying) or 0.0)
            iv = pr.inputs["implied_vol"]["value"]
            dv = shock.get("VOL", 0.0) * ((iv * 100 / vix0) if vix0 else 1.0) / 100.0
            T = max(0.0, pr._T() - h / 252.0)
            new = px.option_price(inst.right, S * (1 + ret), inst.strike, T, pr.inputs["rate"]["value"], pr.inputs["dividend_yield"]["value"],
                                  max(0.01, iv + dv), "EUROPEAN" if inst.style == "EUROPEAN" else inst.style)
            tot += p["quantity"] * inst.multiplier * (new - pr.price)
            continue
        tot += sum(v * shock.get(f, 0.0) for f, v in (p.get("exposures") or {}).items())
        if inst.type == "SPOT" and p.get("market_value"):
            tot += p["market_value"] * (drifts.get(inst.id) or 0.0)
        elif inst.type == "FUTURE" and inst.spec.get("kind") == "equity_index" and p.get("notional"):
            tot += p["notional"] * ((drifts.get(inst.spec.get("proxy")) or 0.0) - rf * h / 252.0)
    return tot


def _stats(xs: List[float]) -> dict:
    n = len(xs)
    s = sorted(xs)
    k = max(1, int(0.05 * n))
    mu = sum(xs) / n
    return {"mean": mu, "sd": math.sqrt(sum((x - mu) ** 2 for x in xs) / max(1, n - 1)), "var95": -s[int(0.05 * (n - 1))],
            "es95": -sum(s[:k]) / k, "upside": sum(max(0.0, x) for x in xs) / n, "downside": sum(max(0.0, -x) for x in xs) / n,
            "p_loss": sum(1 for x in xs if x < 0) / n, "worst": s[0]}


def _filter(design: dict, cands: List[dict], trade_id: str) -> Optional[List[str]]:
    only = design.get("only")
    el = [c for c in cands if c.get("status") == "ELIGIBLE"]
    if only == "index_option":
        ids = [c["id"] for c in el if c.get("type") == "OPTION" and c["id"].startswith(("OPT:SPY", "OPT:SPX", "OPT:QQQ", "OPT:IWM", "OPT:NDX"))]
    elif only == "own_option":
        ids = [c["id"] for c in el if c.get("type") == "OPTION" and c["id"].startswith(f"OPT:{trade_id}:")]
    elif only == "vol":
        ids = [c["id"] for c in el if c["id"] in ("VXX", "UVXY", "VIXY")]
    elif design.get("linear"):            # a beta or sector hedge is a linear product, not a volatility product
        ids = [c["id"] for c in el if c.get("type") in ("SPOT", "FUTURE", "FORWARD") and c["id"] != trade_id
               and c["id"] not in ("VXX", "UVXY", "VIXY", "SVXY")]
    else:
        return None
    return ids


def compare(research, trade: dict, horizon: str = "1M", objective: Optional[str] = None, lam: float = 1.0, nav: Optional[float] = None,
            book: Optional[List[dict]] = None, use_shaffer: bool = True) -> dict:
    """trade = {"id", "quantity"}; book = the existing positions (incremental risk is measured on book + trade)."""
    from .market import Market
    from .risk import RiskModel
    h = E.HORIZON.get(horizon, 21)
    m = Market(research)
    rk = RiskModel(m)
    store = research.store
    pos = [{"id": trade["id"], "quantity": float(trade["quantity"])}]
    base = [dict(p) for p in (book or []) if float(p.get("quantity") or 0)] + pos
    priced_base = E.price_positions(base, m, rk, store)
    if not priced_base or any(p.get("error") for p in priced_base if p["id"] == trade["id"]):
        raise ValueError(f"{trade['id']} cannot be priced for a hedge comparison")
    exp = expected_return(research, trade["id"], horizon, h, m, use_shaffer, rk)
    side = 1 if float(trade["quantity"]) > 0 else -1
    risk_key = "es95" if objective in TAIL_OBJ else "sd"
    vix0, _ = m.macro("VIXCLS")
    rf = (m.macro("DGS3MO")[0] or 0.0) / 100.0
    rows, legs_of = [], {}
    for d in DESIGNS:
        if d["objective"] is None:
            legs_of[d["key"]] = []
            rows.append({**d})
            continue
        try:
            params = {"reduction": d["reduction"], "horizon": horizon}
            ana = E.analyze(research, pos, d["objective"], params, nav=nav, history=False)
            use = _filter(d, ana.get("candidates") or [], trade["id"])
            if use is not None:
                if not use:
                    rows.append({**d, "unavailable": "no eligible product of this kind"})
                    continue
                ana = E.analyze(research, pos, d["objective"], {**params, "use": use, "max_legs": 1}, nav=nav, history=False)
            legs = [{"id": L["id"], "quantity": L["quantity"]} for L in (ana["package"]["final"] or ana["package"]["raw"])]
            legs = [L for L in legs if L["id"] != trade["id"]]           # never "hedge" by undoing the trade itself
            if not legs:
                rows.append({**d, "unavailable": "the engine found no hedge of this kind worth adding"})
                continue
            cost = sum(((L.get("cost") or {}).get("total") or 0.0) for L in (ana["package"]["final"] or ana["package"]["raw"]) if L["id"] != trade["id"])
            labels = sorted({L.get("pricing_label") for L in (ana["package"]["final"] or ana["package"]["raw"]) if L.get("pricing_label")})
            legs_of[d["key"]] = legs
            if d["objective"] == "auto":
                d = {**d, "label": f"{d['label'].split(' (')[0]}: {ana.get('objective_label') or ana.get('objective')}"}
            rows.append({**d, "legs": [{**L, "name": next((c.get("name") for c in ana["candidates"] if c["id"] == L["id"]), L["id"])} for L in legs],
                         "cost": cost, "pricing_labels": labels})
        except Exception as e:  # noqa: BLE001 - one design failing must not hide the others
            rows.append({**d, "unavailable": f"{type(e).__name__}: {e}"})
    # one simulation for every design: same scenarios, same drifts
    ids = {trade["id"]} | {p["id"] for p in base} | {L["id"] for ls in legs_of.values() for L in ls}
    priced_legs = {k: E.price_positions(ls, m, rk, store) for k, ls in legs_of.items()}
    unders = set()
    for p in priced_base + [x for ls in priced_legs.values() for x in ls]:
        inst = p.get("inst")
        if inst is None:
            continue
        unders.add(inst.underlying if inst.type == "OPTION" else inst.spec.get("proxy") if inst.type == "FUTURE" and inst.spec.get("proxy") else inst.id)
    drifts = {a: prior_drift(m, rk, a, h) for a in unders | ids if a}
    if exp.get("value") is not None:
        drifts[trade["id"]] = exp["value"]
    idio_assets = sorted(a for a in unders | ids if a and a in rk._idio)
    scen = scenarios(research, m, rk, h, idio_assets)
    if len(scen) < 60:
        raise ValueError("not enough history for a scenario comparison at this horizon")
    base_pnl = [_pnl(priced_base, s, m, rk, h, drifts, vix0, rf) for s in scen]
    b = _stats(base_pnl)
    for r in rows:
        if r.get("unavailable"):
            continue
        hp = [_pnl(priced_legs[r["key"]], s, m, rk, h, drifts, vix0, rf) for s in scen] if priced_legs.get(r["key"]) else [0.0] * len(scen)
        st = _stats([a + c for a, c in zip(base_pnl, hp)])
        r.update(stats=st, risk_reduction=b[risk_key] - st[risk_key], profit_sacrificed=b["mean"] - st["mean"],
                 upside_kept=(st["upside"] / b["upside"]) if b["upside"] else None, downside_cut=(1 - st["downside"] / b["downside"]) if b["downside"] else None)
        r.setdefault("cost", 0.0)
    ok = [r for r in rows if not r.get("unavailable")]

    def util(r, l):
        return r["risk_reduction"] - l * r["profit_sacrificed"] - (r.get("cost") or 0.0)
    for r in ok:
        r["utility"] = util(r, lam)
    best = max(ok, key=lambda r: r["utility"]) if ok else None
    sens = {str(l): max(ok, key=lambda r: util(r, l))["key"] for l in LAMBDAS} if ok else {}
    cal = exp.get("calibrated")
    thesis = None
    if cal is not None and cal * side <= -20:
        thesis = (f"Your trade is {'long' if side > 0 else 'short'} but the calibrated Shaffer Score at {horizon} is {cal:+.0f}: the "
                  "current quantitative evidence disagrees with the trade. Reducing risk costs little expected profit here.")
    elif cal is not None and cal * side >= 50 and exp.get("evidence") is not None:
        thesis = (f"The calibrated Shaffer Score at {horizon} ({cal:+.0f}) supports the trade and its evidence enters the expected "
                  "return, so designs that remove market exposure give up more expected profit than a tail hedge.")
    cls = (store.asset(trade["id"]) or {}).get("asset_class")
    notes = []
    if cls not in ("EQUITY", "INDEX") and not (cls == "ETF" and abs(_beta(m, rk, trade["id"]) or 0) > 0.5):
        notes.append("Income, carry and the term premium of bonds, currencies and commodities are not modelled (the prior is β × the "
                     "market): for this trade the profit side of the comparison is understated.")
    if not exp.get("evidence"):
        notes.append("No supported Shaffer evidence at this horizon: the profit side uses only the market prior, so the Shaffer Score does "
                     "not change which design wins here.")
    return {"notes": notes, "asof": m.asof, "trade": trade, "horizon": horizon, "h": h, "objective": objective, "risk_measure": "ES 95%" if risk_key == "es95" else "standard deviation",
            "lambda": lam, "expected": exp, "before": b, "designs": rows, "recommended": best["key"] if best else None,
            "sensitivity": sens, "thesis": thesis, "scenarios": len(scen), "book_included": bool(book),
            "method": __doc__.split("\n\n")[1].replace("\n", " ")}
