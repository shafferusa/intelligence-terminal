"""The Shaffer Hedge walk-forward audit: `python -m finsim2 hedge-audit` → finsim2/HEDGE_AUDIT.md.

Every case is a book (today's dollar holding) and one hedge product for one risk, evaluated from month-starts over
up to 15 years with the sizing information available at each start (see history.Evaluator). The same windows
train and test the ML adjustment walk-forward; the verified models are stored in kv `hedgeml:<VERSION>` and are the
only ones the live engine may use."""
from __future__ import annotations

import math
import time
from typing import Dict, List, Optional

from . import products as P
from .engine import ml_group
from . import objml
from .history import ADJ_CAP, ALPHA, VERSION, Evaluator, ml_layer
from .market import SHORT_RATE

MV = 500_000.0
HORIZONS = [("1W", 5), ("1M", 21), ("3M", 63)]


def _opt(und, right="P", mny=0.95, days=91, vol=None):
    return {"kind": "option", "underlying": und, "right": right, "moneyness": mny, "tenor_days": days, "vol_underlying": vol or und}


def cases() -> List[dict]:
    eq_books = ["SPY", "QQQ", "IWM", "NVDA", "AAPL", "MSFT", "JPM", "XOM", "XLK", "EFA", "EEM", "AMZN"]
    out = []
    for b in eq_books:
        out.append({"book": b, "objective": "equity", "leg": {"kind": "spot", "asset": "SPY"}, "label": "SPY short", "product": "ETF short"})
        out.append({"book": b, "objective": "equity", "leg": {"kind": "equity_future", "index": "SPX", "proxy": "SPY"}, "label": "ES future",
                    "product": "Equity index future"})
        out.append({"book": b, "objective": "equity", "leg": {"kind": "spot", "asset": "SH"}, "label": "SH (inverse ETF) long",
                    "product": "Inverse ETF"})
        out.append({"book": b, "objective": "equity", "leg": _opt("SPY", mny=0.95), "label": "SPY 5% OTM put (3M)", "product": "Index/ETF put"})
        out.append({"book": b, "objective": "equity", "leg": _opt("SPY", mny=1.0), "label": "SPY ATM put (3M)", "product": "Index/ETF put"})
    for b in ("QQQ", "NVDA", "AAPL", "MSFT", "XLK", "AMZN"):
        out.append({"book": b, "objective": "equity", "leg": {"kind": "equity_future", "index": "NDX", "proxy": "QQQ"}, "label": "NQ future",
                    "product": "Equity index future"})
        out.append({"book": b, "objective": "equity", "leg": _opt("QQQ", mny=0.95), "label": "QQQ 5% OTM put (3M)", "product": "Index/ETF put"})
    for b, s in (("NVDA", "SMH"), ("AAPL", "XLK"), ("MSFT", "XLK"), ("JPM", "XLF"), ("XOM", "XLE")):
        out.append({"book": b, "objective": "equity", "leg": {"kind": "spot", "asset": s}, "label": f"{s} short", "product": "Sector ETF short"})
    for b in ("AAPL", "AMZN", "GOOGL"):
        out.append({"book": b, "objective": "name", "leg": _opt(b, mny=0.95), "label": f"{b} 5% OTM put (3M)", "product": "Single-stock put"})
        out.append({"book": b, "objective": "name", "leg": {"kind": "spot", "asset": b}, "label": f"{b} short (itself)", "product": "Stock short"})
    for b in ("TLT", "IEF", "LQD", "AGG"):
        for root in ("ZN", "ZB", "UB"):
            out.append({"book": b, "objective": "rates", "leg": {"kind": "treasury_future", "spec": P.FUTURES[root]}, "label": f"{root} future",
                        "product": "Treasury future"})
        out.append({"book": b, "objective": "rates", "leg": {"kind": "spot", "asset": "IEF"}, "label": "IEF short", "product": "Treasury ETF short"})
    for b, legs in (("HYG", ["JNK", "BKLN"]), ("JNK", ["HYG"]), ("LQD", ["VCIT"])):
        for L in legs:
            out.append({"book": b, "objective": "credit", "leg": {"kind": "spot", "asset": L}, "label": f"{L} short", "product": "Credit ETF short"})
    for b, ccy in (("EWJ", "JPY"), ("EWG", "EUR"), ("EWU", "GBP")):
        out.append({"book": b, "objective": f"fx:{ccy}", "leg": {"kind": "forward", "ccy": ccy, "foreign_rate": SHORT_RATE.get(ccy)},
                    "label": f"{ccy} forward (rolled)", "product": "FX forward"})
    out.append({"book": "GLD", "objective": "commodity:CMD:GOLD", "leg": _opt("GLD", mny=0.95), "label": "GLD 5% OTM put (3M)", "product": "Commodity ETF put"})
    out.append({"book": "USO", "objective": "commodity:CMD:OIL", "leg": _opt("USO", mny=0.95), "label": "USO 5% OTM put (3M)", "product": "Commodity ETF put"})
    out.append({"book": "BTC", "objective": "crypto", "leg": {"kind": "spot", "asset": "BITO"}, "label": "BITO short", "product": "Crypto ETF short"})
    out.append({"book": "BTC", "objective": "crypto", "leg": {"kind": "spot", "asset": "IBIT"}, "label": "IBIT short", "product": "Crypto ETF short"})
    return out


def _book(research, asset: str, objective: str) -> List[dict]:
    b = {"asset": asset, "mv": MV}
    if objective != "equity" and not objective.startswith(("sector", "name")):
        from .market import Market
        from .risk import RiskModel
        from .engine import _group_exposure
        rk = research._memo("hedgeaudit:rk", lambda: RiskModel(Market(research)))
        b["structural"] = _group_exposure(rk.unit_exposures(asset)["exposures"], objective)
    return [b]


def _leg(research, leg: dict, objective: str) -> dict:
    leg = dict(leg)
    if leg["kind"] == "spot" and objective != "equity" and not objective.startswith(("sector", "name")):
        from .market import Market
        from .risk import RiskModel
        from .engine import _group_exposure
        rk = research._memo("hedgeaudit:rk", lambda: RiskModel(Market(research)))
        ue = _group_exposure(rk.unit_exposures(leg["asset"])["exposures"], objective)
        if ue:
            leg["unit_exposure"] = ue
    if leg["kind"] == "forward":
        leg["unit_exposure"] = 1.0
    return leg


def run(research, progress=print, ratio: float = 1.0) -> dict:
    t0 = time.time()
    out = {"started": time.strftime("%Y-%m-%d %H:%M:%S"), "asof": research.panel().calendar()[-1], "ratio": ratio, "cases": []}
    groups: Dict[str, List[dict]] = {}
    cs = cases()
    for n, c in enumerate(cs):
        for lab, h in HORIZONS:
            if c["leg"]["kind"] == "option" and h > 63:
                continue
            try:
                ev = Evaluator(research, _book(research, c["book"], c["objective"]), c["objective"], _leg(research, c["leg"], c["objective"]), h,
                               ratio=ratio)
                res = ev.run()
            except Exception as e:  # noqa: BLE001
                out["cases"].append({**c, "horizon": lab, "h": h, "error": f"{type(e).__name__}: {e}"})
                continue
            rows = res.pop("rows", [])
            g = ml_group(c["objective"], c["leg"]["kind"], h)
            for r in rows:
                r["case"] = f"{c['book']}|{c['label']}"
            groups.setdefault(g, []).extend(rows)
            out["cases"].append({**{k: v for k, v in c.items() if k != "leg"}, "leg_kind": c["leg"]["kind"], "horizon": lab, "h": h, "result": res,
                                 "regimes": _regime_rows(rows)})
        if progress:
            progress(f"[{n + 1}/{len(cs)}] {c['book']} ← {c['label']}")
    if progress:
        progress("objective-specific hedge ML: walk-forward per group and objective")
    linear = {g: rs for g, rs in groups.items() if g.split(":")[1] in ("spot", "equity_future", "forward")}
    v1 = ml_layer(linear)                                           # the original variance-only layer, for comparison
    out["ml"] = {g: {k: v for k, v in m.items() if k != "model"} for g, m in v1.items()}
    models = objml.research(groups)
    out["objml"] = {g: {mt: {k: v for k, v in r.items() if k not in ("model", "fill")} for mt, r in res.items()} for g, res in models.items()}
    research.store.kv_set(f"hedgeml:{VERSION}", {"groups": {g: {mt: r for mt, r in res.items() if mt in objml.METRICS} for g, res in models.items()},
                                                 "trained": out["started"], "asof": out["asof"], "alpha": ALPHA, "cap": ADJ_CAP})
    out["seconds"] = round(time.time() - t0, 1)
    from ..engine.health import hedge_audit_summary
    research.store.kv_set("hedgeaudit:summary", hedge_audit_summary(out))
    return out


def _regime_rows(rows: List[dict]) -> Dict[str, dict]:
    agg: Dict[str, dict] = {}
    for r in rows:
        for dim in ("market", "volatility", "rates", "inflation", "growth", "liquidity"):
            s = (r.get("regime") or {}).get(dim)
            if not s:
                continue
            a = agg.setdefault(s, {"var_u": 0.0, "var_h": 0.0, "n": 0})
            a["var_u"] += r["var_u"]; a["var_h"] += r["var_h"]; a["n"] += 1
    return agg


# ------------------------------------------------------------------ the report
def _f(x, d=2, pct=False):
    if x is None:
        return "—"
    return f"{x:+.{d}%}" if pct else f"{x:,.{d}f}"


def markdown(a: dict) -> str:
    from . import engine as E
    from .risk import LABELS, UNITS
    L = []
    w = L.append
    ok = [c for c in a["cases"] if c.get("result", {}).get("n")]
    w("# Shaffer Hedge: the audit")
    w("")
    w(f"Generated {a['started']} from the real research store (data to {a['asof']}); {len(ok)} case-horizons with history, "
      f"run time {a['seconds'] / 60:.1f} minutes. Every result is walk-forward: the hedge is sized at each month-start with information "
      f"available then (trailing-year betas, structural DV01/CS01/currency exposure, the option's delta from that day's Cboe volatility "
      f"index) for {a['ratio']:.0%} of the exposure, and judged on what happened over the following horizon. A $500,000 holding is the book.")
    w("")
    w("## 1–3. Risk factors, instruments and their sizing units")
    w("")
    w("| Factor | Unit |")
    w("|---|---|")
    for k, u in UNITS.items():
        w(f"| {k} | {u} |")
    w("")
    w("| Product type | Status | Risk unit | Sizing rule | Reason / limitation |")
    w("|---|---|---|---|---|")
    for p in P.PRODUCT_TYPES:
        w(f"| {p['name']} | {p['status']} | {p['risk_units']} | {p['sizing']} | {p['reason'] or ''} |")
    w("")
    w("## 4. Exact hedge math")
    w("")
    w("```")
    w(E.__doc__.strip())
    w("```")
    w("")
    w("## 21–25. Walk-forward results")
    w("")
    w("Realised reduction = 1 − Σ(hedged daily P&L)² ÷ Σ(unhedged daily P&L)² over every window. Effectiveness = realised ÷ the reduction "
      "expected from the trailing year at each start (linear hedges). Tail = the worst 10% of windows for the unhedged book.")
    w("")

    def table(key_fn, title):
        agg: Dict[str, dict] = {}
        for c in ok:
            k = key_fn(c)
            r = c["result"]
            b = agg.setdefault(k, {"n": 0, "red": [], "eff": [], "tail": [], "pnl": [], "ups": [], "neff": 0.0})
            b["n"] += 1
            b["neff"] += r.get("n_eff") or 0
            for fld, src in (("red", "realized_reduction"), ("eff", "effectiveness"), ("pnl", "hedge_pnl_mean"), ("ups", "upside_sacrificed")):
                if r.get(src) is not None:
                    b[fld].append(r[src])
            if (r.get("tail") or {}).get("reduction") is not None:
                b["tail"].append(r["tail"]["reduction"])
        w(f"**{title}**")
        w("")
        w("| Group | Cases | Indep. windows | Median realised variance reduction | Median effectiveness | Median tail-loss reduction | Mean hedge P&L per window | Mean upside given up |")
        w("|---|---|---|---|---|---|---|---|")
        med = lambda xs: sorted(xs)[len(xs) // 2] if xs else None
        for k, b in sorted(agg.items(), key=lambda kv: -(med(kv[1]["red"]) or -9)):
            w(f"| {k} | {b['n']} | {b['neff']:.0f} | {_f(med(b['red']), 1, True)} | {_f(med(b['eff']), 2)} | {_f(med(b['tail']), 1, True)} | "
              f"{_f(sum(b['pnl']) / len(b['pnl']) if b['pnl'] else None, 0)} | {_f(sum(b['ups']) / len(b['ups']) if b['ups'] else None, 0)} |")
        w("")
    table(lambda c: c["product"], "22. By hedge product")
    table(lambda c: {"equity": "Equity beta", "name": "Single name", "rates": "Rates (DV01)", "credit": "Credit (CS01)", "crypto": "Crypto"}.get(
        c["objective"], "Currency" if c["objective"].startswith("fx:") else "Commodity" if c["objective"].startswith("commodity") else c["objective"]),
        "23. By risk")
    table(lambda c: c["horizon"], "25. By horizon")
    # by regime
    reg: Dict[str, dict] = {}
    for c in ok:
        for s, b in (c.get("regimes") or {}).items():
            a2 = reg.setdefault(s, {"var_u": 0.0, "var_h": 0.0, "n": 0})
            a2["var_u"] += b["var_u"]; a2["var_h"] += b["var_h"]; a2["n"] += b["n"]
    w("**24. By regime at the start of the window** (all cases pooled; variance-weighted)")
    w("")
    w("| Regime | Windows | Realised variance reduction |")
    w("|---|---|---|")
    from ..engine.regimes import STATE_LABEL
    for s, b in sorted(reg.items(), key=lambda kv: -kv[1]["n"]):
        w(f"| {STATE_LABEL.get(s, s)} | {b['n']} | {_f(1 - b['var_h'] / b['var_u'] if b['var_u'] else None, 1, True)} |")
    w("")
    w("## 20, 26. Baselines and cost against risk reduction")
    w("")
    w("Per case: realised variance reduction of the static rule (the Raw Shaffer Hedge ratio) against fixed 25% / 50% hedges and the "
      "trailing minimum-variance ratio, on the same windows (linear hedges).")
    w("")
    w("| Book | Hedge | Horizon | Static rule | Fixed 25% | Fixed 50% | Min-variance | Mean hedge P&L | Residual $/day | Unhedged $/day |")
    w("|---|---|---|---|---|---|---|---|---|---|")
    for c in ok:
        r = c["result"]
        b = r.get("baselines") or {}
        if c["leg_kind"] not in ("spot", "equity_future", "forward"):
            continue
        w(f"| {c['book']} | {c['label']} | {c['horizon']} | {_f(b.get('static_rule'), 1, True)} | {_f(b.get('fixed_25'), 1, True)} | "
          f"{_f(b.get('fixed_50'), 1, True)} | {_f(b.get('min_variance'), 1, True)} | {_f(r.get('hedge_pnl_mean'), 0)} | "
          f"{_f(r.get('residual_daily'), 0)} | {_f(r.get('unhedged_daily'), 0)} |")
    w("")
    w("## 27–28. Where it worked and where it failed")
    w("")
    best, worst = [], []
    for c in ok:
        for e in c["result"].get("best") or []:
            best.append((e["hedged"] - e["position"], c, e))
        for e in c["result"].get("worst") or []:
            worst.append((e["hedged"] - e["position"], c, e))
    best.sort(key=lambda x: -x[0])
    worst.sort(key=lambda x: x[0])
    w("**Largest gains from hedging (hedge P&L in the window):**")
    w("")
    for g, c, e in best[:10]:
        w(f"- {c['book']} hedged with {c['label']} ({c['horizon']}), {e['date']} → {e['end']}: book {e['position']:+,.0f}, hedged {e['hedged']:+,.0f}")
    w("")
    w("**Largest losses from hedging:**")
    w("")
    for g, c, e in worst[:10]:
        w(f"- {c['book']} hedged with {c['label']} ({c['horizon']}), {e['date']} → {e['end']}: book {e['position']:+,.0f}, hedged {e['hedged']:+,.0f}")
    w("")
    fails = [c for c in ok if (c["result"].get("realized_reduction") or 0) < 0]
    if fails:
        w("**Hedges that increased variance overall** (realised reduction below zero):")
        w("")
        for c in fails:
            w(f"- {c['book']} ← {c['label']} ({c['horizon']}): realised {_f(c['result']['realized_reduction'], 1, True)}")
        w("")
    w("## 18–19. The ML adjustment")
    w("")
    w(f"FinalHedge = RawHedge × (1 + {ALPHA} × MLAdjustment), |MLAdjustment| ≤ {ADJ_CAP} → at most ±{ALPHA * ADJ_CAP:.0%} of the raw hedge. "
      "A ridge model per (risk, hedge kind, horizon) learns log(ex-post minimum-variance ratio ÷ raw ratio) from the VIX, the trailing "
      "1-year and 3-month correlation, the change in beta, the 3-month market return and the bill rate, trained only on windows that "
      "ended before each prediction. It is used only when it beats the static rule AND the minimum-variance ratio out of sample "
      "(paired t ≥ 2 on n_eff ≥ 30) and has not decayed in the latest third.")
    w("")
    w("| Group | OOS windows | n_eff | t vs static rule | t vs min-variance | Verified | Reasons |")
    w("|---|---|---|---|---|---|---|")
    for g, m in sorted(a["ml"].items()):
        w(f"| {g} | {m.get('n', 0)} | {_f(m.get('n_eff'), 0)} | {_f(m.get('t_vs_static'), 1)} | {_f(m.get('t_vs_min_variance'), 1)} | "
          f"{'yes' if m.get('verified') else 'no'} | {'; '.join(m.get('reasons') or []) or '—'} |")
    w("")
    if a.get("objml"):
        w("## 18b. Objective-specific hedge ML (richer features)")
        w("")
        w(objml.__doc__.split("\n\n")[1].replace("\n", " "))
        w("")
        w("Verified = beats the static rule AND the minimum-variance multiple out of sample (t ≥ 2, or bootstrap p ≤ 0.025 for the pooled "
          "VaR/ES), n_eff ≥ 30, not worse in the latest third. Gain = 1 − adjusted ÷ static on the same windows.")
        w("")
        w("| Group | Metric | OOS windows | n_eff (dates) | Gain vs static | Gain vs constant | t / p vs static | t / p vs constant | t / p vs min-var | Mean adj. (constant) | Verified | Reasons |")
        w("|---|---|---|---|---|---|---|---|---|---|---|---|")
        tally: Dict[str, List[int]] = {}
        for g, res in sorted(a["objml"].items()):
            for mt in objml.METRICS + ["variance_basic"]:
                r = res.get(mt) or {}
                if not r.get("n"):
                    continue
                t = tally.setdefault(mt, [0, 0])
                t[0] += 1; t[1] += 1 if r.get("verified") else 0
                ts = _f(r.get("t_vs_static"), 1) if mt not in objml.POOLED else ("p " + _f(r.get("p_vs_static"), 2))
                tm = _f(r.get("t_vs_min_variance"), 1) if mt not in objml.POOLED else ("p " + _f(r.get("p_vs_min_variance"), 2))
                tc = _f(r.get("t_vs_constant"), 1) if mt not in objml.POOLED else ("p " + _f(r.get("p_vs_constant"), 2))
                gc = (1 - r["adjusted"] / r["constant"]) if r.get("adjusted") is not None and r.get("constant") else None
                w(f"| {g} | {mt.replace('_', ' ')} | {r.get('n', 0)} | {_f(r.get('n_eff'), 0)} | {_f(r.get('gain_vs_static'), 1, True)} | {_f(gc, 1, True)} | {ts} | {tc} | {tm} | "
                  f"{_f(r.get('mean_adjustment'), 2)} ({_f(r.get('constant_adjustment'), 2)}) | {'yes' if r.get('verified') else 'no'} | {'; '.join(r.get('reasons') or []) or '—'} |")
        w("")
        w("| Metric | Groups tested | Verified |")
        w("|---|---|---|")
        for mt, (n, v) in tally.items():
            w(f"| {objml.METRIC_LABEL.get(mt, 'Variance, original six features')} | {n} | {v} |")
        w("")
        tests = sum(n for n, _ in tally.values())
        w(f"**Reading it honestly.** {tests} group × metric tests were run; at a one-sided 2.3% level about {0.023 * tests:.0f} would pass by "
          "chance alone. Where a model passes, compare its mean adjustment with the constant's: when they are nearly equal and the gain "
          "vs the constant is a fraction of a percent, the useful information is the static rule's SIZING BIAS (what the constant "
          "learned), not conditional skill. The sizing bias is reported below; it is not applied to the static rule automatically.")
        w("")
        w("| Group | Constant adjustment learned | Variance gain of the constant vs static | Model's gain vs static |")
        w("|---|---|---|---|")
        for g, res in sorted(a["objml"].items()):
            r = res.get("variance") or {}
            if r.get("n") and r.get("constant") and r.get("static") and abs(r.get("constant_adjustment") or 0) > 0.05:
                w(f"| {g} | {_f(r.get('constant_adjustment'), 2)} (hedge × {1 + ALPHA * r['constant_adjustment']:.2f}) | "
                  f"{_f(1 - r['constant'] / r['static'], 1, True)} | {_f(r.get('gain_vs_static'), 1, True)} |")
        w("")
    errs = [c for c in a["cases"] if c.get("error") or not c.get("result", {}).get("n")]
    if errs:
        w("## Cases without history")
        w("")
        for c in errs:
            w(f"- {c['book']} ← {c['label']} ({c['horizon']}): {c.get('error') or c.get('result', {}).get('reason')}")
        w("")
    return "\n".join(L)
