"""Historical crisis replays of the CURRENT book (and of its Shaffer Hedge).

For each episode the actual cumulative factor moves between its peak and trough dates are applied to today's
positions as an instantaneous shock: linear products through their exposures, options fully re-priced (new
underlying, new implied volatility from the VIX change). Where a held asset traded through the episode its own
realised return is shown too ("direct"), which includes its single-name move. Also: the book's daily P&L during the
episode (today's exposures × each day's factor moves) against the last three years → VaR/ES in crisis vs normal,
and what the current Shaffer Hedge (beta and crash objectives) would have added.

Limits: today's exposures are held fixed through the episode (no rebalancing, no path); factors that did not exist yet
(e.g. crypto before 2014, some sector funds before 1999) are reported as unavailable and contribute nothing."""
from __future__ import annotations

import math
from typing import Dict, List, Optional

CRISES = [
    {"key": "dotcom", "label": "Dot-com bust", "start": "2000-03-24", "end": "2002-10-09"},
    {"key": "gfc", "label": "2008 financial crisis", "start": "2007-10-09", "end": "2009-03-09"},
    {"key": "covid", "label": "March 2020", "start": "2020-02-19", "end": "2020-03-23"},
    {"key": "infl2022", "label": "2022 inflation / rate shock", "start": "2022-01-03", "end": "2022-10-12"},
]
COMPOUND = ("MKT", "FX:", "CMD:", "CRYPTO")


def cumulative_shock(F: Dict[str, list], ia: int, ib: int) -> tuple:
    """Cumulative factor moves over (ia, ib]: returns compounded, spreads/bp/VIX points summed. Factors with data on
    fewer than half the sessions are unavailable."""
    shock, missing = {}, []
    n = ib - ia
    for f, xs in F.items():
        seg = [x for x in xs[ia + 1:ib + 1] if x is not None]
        if len(seg) < max(1.0, n / 2.0):
            missing.append(f)
            continue
        if f.startswith(COMPOUND):
            v = 1.0
            for x in seg:
                v *= 1 + x
            shock[f] = v - 1
        else:
            shock[f] = sum(seg)
    return shock, missing


def _var_es(xs: List[float]) -> tuple:
    if len(xs) < 20:
        return None, None
    s = sorted(xs)
    k = max(1, int(0.05 * len(s)))
    return -s[int(0.05 * (len(s) - 1))], -sum(s[:k]) / k


def replay(research, positions: List[dict], nav: float, hedge_objectives=("beta", "crash")) -> dict:
    from . import engine as E
    from .market import Market
    from .risk import RiskModel, factor_series
    m = Market(research)
    rk = RiskModel(m)
    store = research.store
    panel = research.panel()
    cal = panel.calendar()
    F = factor_series(research)
    priced = E.price_positions(positions, m, rk, store)
    R = E.risk_vector(priced)
    # the book's daily linear P&L with today's exposures: last three years = "normal"
    def daily(ia, ib):
        out = []
        for t in range(ia + 1, ib + 1):
            v, ok = 0.0, False
            for f, x in R.items():
                mv = (F.get(f) or [None] * len(cal))[t]
                if mv is not None:
                    v += x * mv; ok = True
            if ok:
                out.append(v)
        return out
    n = len(cal)
    var_n, es_n = _var_es(daily(max(0, n - 757), n - 1))
    hedges = {}
    for obj in hedge_objectives:
        try:
            ana = E.analyze(research, positions, obj, {"reduction": 1.0 if obj == "beta" else 0.5, "horizon": "1M"}, nav=nav, history=True)
            legs = [{"id": L["id"], "quantity": L["quantity"]} for L in ana["package"]["final"] or ana["package"]["raw"]]
            hedges[obj] = {"label": ana["objective_label"], "legs": legs, "priced": E.price_positions(legs, m, rk, store)}
        except Exception as e:
            hedges[obj] = {"error": f"{type(e).__name__}: {e}"}
    out = []
    for c in CRISES:
        ia, ib = panel.index_of(c["start"]), panel.index_of(c["end"])
        shock, missing = cumulative_shock(F, ia, ib)
        book = E.scenario_pnl(priced, shock, m, rk)
        rows = []
        for p in priced:
            fp = E.scenario_pnl([p], shock, m, rk)
            direct = None
            inst = p.get("inst")
            if inst is not None and inst.type == "SPOT" and p.get("market_value"):
                r = m.usd_returns(inst.id)
                seg = [x for x in r[ia + 1:ib + 1] if x is not None]
                if len(seg) >= 0.9 * (ib - ia):
                    v = 1.0
                    for x in seg:
                        v *= 1 + x
                    direct = p["market_value"] * (v - 1)
            rows.append({"id": p["id"], "name": p.get("name"), "factor_pnl": fp, "direct_pnl": direct})
        direct_total = sum((r["direct_pnl"] if r["direct_pnl"] is not None else r["factor_pnl"]) for r in rows)
        var_c, es_c = _var_es(daily(ia, ib))
        hres = []
        for obj, hd in hedges.items():
            if hd.get("error"):
                hres.append({"objective": obj, "error": hd["error"]})
                continue
            hp = E.scenario_pnl(hd["priced"], shock, m, rk) if hd["priced"] else 0.0
            hres.append({"objective": obj, "label": hd["label"], "legs": hd["legs"], "hedge_pnl": hp, "hedged_pnl": book + hp,
                         "loss_offset": (-hp / book) if book < 0 else None})
        by_factor = {}
        for f, x in R.items():
            if f in shock:
                by_factor[f] = x * shock[f]
        out.append({**c, "sessions": ib - ia, "pnl": book, "pnl_pct_nav": book / nav if nav else None, "pnl_direct": direct_total,
                    "positions": sorted(rows, key=lambda r: (r["direct_pnl"] if r["direct_pnl"] is not None else r["factor_pnl"])),
                    "by_factor": dict(sorted(by_factor.items(), key=lambda kv: kv[1])[:12]),
                    "shock": {k: v for k, v in sorted(shock.items(), key=lambda kv: -abs(kv[1])) if k in R or k == "MKT"},
                    "missing": [f for f in missing if f in R], "var95": var_c, "es95": es_c, "var95_normal": var_n, "es95_normal": es_n,
                    "hedges": hres})
    return {"asof": m.asof, "nav": nav, "crises": out}
