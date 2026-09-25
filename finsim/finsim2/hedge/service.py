"""What the server calls: analyse the portfolio, preview a trade with its Shaffer Hedge, execute a trade (optionally
with the hedge) atomically, and grade matured hedge recommendations. Every executed or declined proposal is written
to the append-only hedge ledger (`hedge_recommendations`)."""
from __future__ import annotations

import datetime as _dt
import math
from typing import Dict, List, Optional

from . import engine as E
from . import products as P
from .market import Market
from .risk import RiskModel, label as flabel


def nav_now(ledger) -> float:
    h = ledger.holdings()
    i = len(ledger.panel.calendar()) - 1
    nav = h["cash"]
    for a, p in h["positions"].items():
        if abs(p["quantity"]) < 1e-12:
            continue
        meta = ledger._meta(a)
        v = ledger.value(meta, p, ledger.mark(a, i), ledger._fx_at(meta.get("currency") or "USD", i))
        nav += v or 0.0
    return nav


def signed_quantity(side: str, q: float) -> float:
    side = side.upper()
    return -abs(q) if side in ("SELL", "SHORT") else abs(q)


def trade_objective(research, asset_id: str, q: float) -> str:
    """A trade's default hedge objective: its systematic risk (every factor but its own residual, which only the
    asset itself could hedge). Kept as a function so a product-specific default can be added."""
    return "systematic"


def dominant_objective(research, asset_id: str, q: float) -> str:
    """The risk a trade mostly adds (ignoring its single-name residual, which only the asset itself can hedge)."""
    m = Market(research)
    rk = RiskModel(m)
    pr = E.price_positions([{"id": asset_id, "quantity": q}], m, rk, research.store)
    R = {f: v for f, v in E.risk_vector(pr).items() if not f.startswith("IDIO:")}
    if not R:
        return "beta"
    C = rk.covariance(list(R))
    c = E.contributions(R, C)
    if not c:
        return "beta"
    obj, _ = E.auto_objective(R, C)
    return obj if obj != "name" else "beta"


def book_metrics(research, books: Dict[str, List[dict]], nav: float) -> Dict[str, dict]:
    """Risk summary of several versions of the book (before the trade, after it, after the hedge) with one risk
    model and one covariance."""
    m = Market(research)
    rk = RiskModel(m)
    priced = {k: E.price_positions(v, m, rk, research.store) for k, v in books.items()}
    Rs = {k: E.risk_vector(p) for k, p in priced.items()}
    C = rk.covariance(sorted({f for R in Rs.values() for f in R}))
    out = {}
    for k, R in Rs.items():
        met = E.summary_metrics(R, C, nav, priced[k])
        met.update(E.tail_stats(E.hist_pnl(priced[k], m, rk)))
        out[k] = met
    return out


def _merge(pos: List[dict], extra: List[dict]) -> List[dict]:
    out = {p["id"]: dict(p) for p in pos}
    for e in extra:
        if e["id"] in out:
            out[e["id"]]["quantity"] = out[e["id"]]["quantity"] + e["quantity"]
        else:
            out[e["id"]] = dict(e)
    return [p for p in out.values() if abs(p["quantity"]) > 1e-12]


def shaffer_brief(research, asset_id: str) -> Optional[dict]:
    """The Shaffer Score in brief for a trade ticket: score by horizon, ML, agreement, confidence, the strongest
    contributors and the most important contradicting evidence at the primary horizon."""
    try:
        b = research.bundle(asset_id)
    except Exception:
        return None
    ph = b.get("primary_horizon") or "3M"
    hs = b.get("horizons") or {}
    cur = hs.get(ph) or {}
    return {"primary_horizon": ph, "as_of": b.get("as_of"), "regime": (b.get("regime") or {}).get("description"),
            "horizons": {k: {"score": v.get("score"), "calibrated": v.get("calibrated"), "ml": v.get("ml_score"), "agreement": v.get("agreement"),
                             "confidence": (v.get("confidence") or {}).get("value"), "expected": v.get("expected")} for k, v in hs.items()},
            "contributors": (cur.get("contributors") or [])[:3], "contradicting": (cur.get("contradicting") or [])[:2],
            "ml_verified": cur.get("ml_verified")}


def trade_preview(app, asset_id: str, side: str, quantity: float, objective: Optional[str] = None, params: Optional[dict] = None) -> dict:
    research, store = app.research, app.store
    led = app.ledger()
    params = dict(params or {})
    q = signed_quantity(side, quantity)
    inst = P.parse(asset_id, store)
    m = Market(research)
    pr = P.Priced(inst, m, RiskModel(m))
    ok, why = pr.eligibility()
    nav = nav_now(led) or 0.0
    trade = [{"id": asset_id, "quantity": q}]
    obj = objective if objective not in (None, "", "auto") else trade_objective(research, asset_id, q)
    params.setdefault("reduction", 1.0)
    params.setdefault("horizon", "1M")
    ana = E.analyze(research, trade, obj, params, nav=max(nav, abs(q * (pr.unit_notional() or 0.0)), 1.0))
    book0 = led.positions_for_hedge()
    book1 = _merge(book0, trade)
    raw = [{"id": L["id"], "quantity": L["quantity"]} for L in ana["package"]["raw"]]
    fin = [{"id": L["id"], "quantity": L["quantity"]} for L in ana["package"]["final"]]
    books = {"before_trade": book0, "after_trade": book1, "after_hedge": _merge(book1, fin)}
    metrics = book_metrics(research, books, max(nav, 1.0))
    from .scoring import net_scores
    ns = net_scores(research, asset_id, [(params["horizon"], E.HORIZON.get(params["horizon"], 21))], m)
    under = inst.underlying if inst.type != "SPOT" else asset_id
    return {"trade": {"asset_id": asset_id, "name": inst.name, "side": side.upper(), "quantity": quantity, "signed": q, "price": pr.price,
                      "notional": abs(q) * (pr.unit_notional() or 0.0), "cash_cost": q * (pr.unit_value() or 0.0), "type": inst.type,
                      "eligible": ok, "reasons": why, "risk_unit": pr.risk_unit(), "costs": pr.costs(E.HORIZON.get(params["horizon"], 21), "BUY" if q > 0 else "SELL")},
            "nav": nav, "free_cash": led.holdings()["cash"] - (led.holdings().get("requirement") or 0.0),
            "shaffer": shaffer_brief(research, under), "net_scores": ns, "hedge": ana, "portfolio": metrics,
            "raw_package": raw, "final_package": fin}


def execute(app, primary: Optional[dict], hedge_legs: List[dict], proposal: Optional[dict] = None, mode: str = "trade_hedge") -> dict:
    """Record the primary trade and (mode trade_hedge) the hedge legs as ONE package; mode trade_only records the
    primary trade alone and logs the proposal as declined. Returns the ids and the new free cash."""
    led = app.ledger()
    legs = []
    if primary:
        pq = float(primary["quantity"])
        if not math.isfinite(pq) or pq <= 0:
            raise ValueError("the quantity must be positive: the side (Buy, Sell, Short, Cover) sets the direction")
        legs.append({"asset_id": primary["asset_id"], "quantity": pq, "side": primary["side"], "note": "primary"})
    if mode == "trade_hedge":
        for L in hedge_legs:
            q = float(L["quantity"])
            if not math.isfinite(q):
                raise ValueError(f"invalid hedge quantity for {L.get('id')}")
            if not q:
                continue
            side = L.get("side") or ("BUY" if q > 0 else "SELL")
            a = L["id"]
            if not P.parse(a, app.store).type in ("FUTURE", "FORWARD", "OPTION"):
                h = led.holdings()["positions"].get(a, {}).get("quantity", 0.0)
                side = ("BUY" if h >= 0 else "COVER") if q > 0 else ("SELL" if h > 0 and h >= abs(q) - 1e-9 else "SHORT")
            legs.append({"asset_id": a, "quantity": abs(q), "side": side, "note": "Shaffer Hedge"})
    if not legs:
        raise ValueError("nothing to execute")
    ids = led.trade_package(legs, note="Shaffer Hedge package" if len(legs) > 1 else "")
    pkg = (app.store.transaction(ids[0]) or {}).get("package_id")
    rec_id = None
    if proposal:
        rec_id = record(app.store, proposal, "executed" if mode == "trade_hedge" else "declined", pkg)
    h = led.holdings()
    return {"ids": ids, "package_id": pkg, "legs": legs, "hedge_record": rec_id, "free_cash": h["cash"] - (h.get("requirement") or 0.0)}


def record(store, ana: dict, status: str, package_id: Optional[str] = None, source: str = "live") -> int:
    """Append one hedge recommendation to the ledger (never updated except for its one-time grade)."""
    h = ana.get("hedge") or ana
    days = h.get("horizon_days") or 21
    made = h.get("asof")
    evald = None
    if made:
        evald = (_dt.date.fromisoformat(made) + _dt.timedelta(days=int(days * 365 / 252) + 1)).isoformat()
    prim = h.get("primary_factor")
    f = next((x for x in h.get("factors") or [] if x["factor"] == prim), {})
    raw = (h.get("package") or {}).get("raw") or []
    fin = (h.get("package") or {}).get("final") or []
    best = (h.get("candidates") or [{}])[0] if h.get("candidates") else {}
    rb, ra = (h.get("risk") or {}).get("before") or {}, (h.get("risk") or {}).get("raw") or {}
    ml = h.get("ml") or {}
    book = [{"id": p["id"], "quantity": p["quantity"]} for p in h.get("positions") or []]
    return store.add_hedge({
        "made_on": made, "source": source, "status": status, "portfolio_id": "main", "package_id": package_id, "objective": h.get("objective"),
        "risk_factor": prim, "exposure": f.get("current"), "target": f.get("target"), "horizon": h.get("horizon"), "horizon_days": days,
        "eval_date": evald, "candidates": [{"id": c.get("id"), "score": c.get("score"), "status": c.get("status")} for c in (h.get("candidates") or [])[:12]],
        "selected": [{"id": L["id"], "quantity": L["quantity"], "raw_quantity": L.get("raw_quantity", L["quantity"])} for L in (fin or raw)],
        "raw_ratio": f.get("hedge_pct_raw"), "ml_adjustment": max((abs(v.get("applied") or 0.0) for v in (ml.get("by_leg") or {}).values()), default=0.0),
        "final_ratio": f.get("hedge_pct_final"), "expected_cost": sum((L.get("cost") or {}).get("total") or 0.0 for L in raw),
        "expected_reduction": (1 - (ra.get("sigma_daily") or 0) ** 2 / (rb.get("sigma_daily") or 1) ** 2) if rb.get("sigma_daily") else None,
        "expected_basis": best.get("basis_risk_daily"), "regime": ", ".join(f"{k}:{v}" for k, v in (h.get("regime") or {}).items() if v),
        "score": best.get("score"), "ml_confidence": None, "detail": {"book": book, "objective_label": h.get("objective_label")}})


def grade(app) -> int:
    """Grade every recommendation whose evaluation date has passed: the book's daily P&L with and without the hedge
    legs over [made_on, eval_date] (spot at the close, derivatives at their model marks)."""
    store, research = app.store, app.research
    panel = research.panel()
    today = panel.calendar()[-1]
    n = 0
    led = app.ledger()
    for rec in store.hedges(500, ungraded_before=today):
        try:
            k0, k1 = panel.index_of(rec["made_on"]), panel.index_of(rec["eval_date"])
            if k1 <= k0:
                continue

            def pnl(pos):
                out = [0.0] * (k1 - k0)
                for p in pos:
                    a, q = p["id"], p["quantity"]
                    prev = led.mark(a, k0)
                    if prev is None:
                        continue
                    meta = led._meta(a)
                    from .series import unit_size
                    u = unit_size(led.inst(a)) if P.parse(a, store).type != "SPOT" else 1.0
                    for j, k in enumerate(range(k0 + 1, k1 + 1)):
                        cur = led.mark(a, k)
                        if cur is None:
                            continue
                        out[j] += q * u * (cur - prev) * (led._fx_at(meta.get("currency") or "USD", k) or 1.0)
                        prev = cur
                return out
            u = pnl((rec.get("detail") or {}).get("book") or [])
            hdg = pnl(rec.get("selected") or [])
            vu = sum(x * x for x in u)
            vh = sum((a + b) ** 2 for a, b in zip(u, hdg))
            red = (1 - vh / vu) if vu > 0 else None
            U, H = sum(u), sum(hdg)
            store.grade_hedge(rec["id"], {"realized_reduction": red, "hedge_pnl": H, "upside_sacrificed": (-min(0.0, H)) if U > 0 else 0.0,
                                          "basis_error": math.sqrt(vh / max(1, len(u))),
                                          "effectiveness": (red / rec["expected_reduction"]) if red is not None and rec.get("expected_reduction") else None})
            n += 1
        except Exception:
            continue
    return n
