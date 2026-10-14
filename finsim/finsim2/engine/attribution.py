"""Why did my portfolio move today? A daily P&L attribution that reconciles to the NAV change.

    total P&L(d) = NAV(d) − NAV(d−1) − external flows(d)                                   (from the one ledger replay)
                 = Σ_positions overnight P&L          positions held at d−1's close, valued at d's close and rate
                   + income                            dividends (paid or owed on shorts) and borrow fees booked on d
                   + interest                          cash / short-proceeds interest booked on d
                   − fees                              commissions on d's trades
                   + trading & other                   everything else: same-day trades marked to the close, expiries

The "trading & other" line is shown, never hidden; on a day without trades it is zero up to rounding.
Overnight P&L is grouped by asset, asset class, sector, country and currency, and also split by factor with the book's
exposures at d−1 (point in time) times d's factor moves: market beta, styles, sectors, rates, credit, currencies,
commodities, crypto, volatility; the remainder is idiosyncratic (single-name moves and option non-linearity)."""
from __future__ import annotations

from typing import Dict, List, Optional

GROUPS = [("Market beta", ("MKT",)), ("Style", ("STY:",)), ("Sector / industry", ("SEC:", "IND:")), ("Rates", ("RATE:", "REAL:")),
          ("Credit", ("CREDIT:",)), ("Currency", ("FX:",)), ("Commodity", ("CMD:",)), ("Crypto", ("CRYPTO",)), ("Volatility", ("VOL",))]


def _group(f: str) -> str:
    for name, pre in GROUPS:
        if f.startswith(pre):
            return name
    return "Idiosyncratic"


def attribute(ledger, research, date: Optional[str] = None) -> dict:
    panel = ledger.panel
    cal = panel.calendar()
    nh = ledger.nav_history()
    if len(nh["dates"]) < 2:
        return {"error": "at least two sessions of history are needed"}
    k = len(nh["dates"]) - 1 if not date else max(1, max(j for j, d in enumerate(nh["dates"]) if d <= date))
    d, d0 = nh["dates"][k], nh["dates"][k - 1]
    total = nh["nav"][k] - nh["nav"][k - 1] - nh["flows"][k]
    i, i0 = panel.index_of(d), panel.index_of(d0)
    h0, h1 = ledger.holdings(d0), ledger.holdings(d)
    rows = []
    for a, p in h0["positions"].items():
        if abs(p["quantity"]) < 1e-12:
            continue
        meta = ledger._meta(a)
        ccy = meta.get("currency") or "USD"
        v0 = ledger.value(meta, p, ledger.mark(a, i0), ledger._fx_at(ccy, i0))
        v1 = ledger.value(meta, p, ledger.mark(a, i), ledger._fx_at(ccy, i))
        pnl = (v1 - v0) if v0 is not None and v1 is not None else 0.0
        rows.append({"asset_id": a, "name": meta.get("name"), "asset_class": meta.get("asset_class") or ("DERIVATIVE" if meta.get("instrument") else None),
                     "sector": meta.get("sector"), "country": meta.get("country"), "currency": ccy, "quantity": p["quantity"], "pnl": pnl})
    income = (h1.get("income", 0.0) - h0.get("income", 0.0)) - (h1.get("financing", 0.0) - h0.get("financing", 0.0))
    interest = h1.get("interest", 0.0) - h0.get("interest", 0.0)
    fees = h1.get("fees", 0.0) - h0.get("fees", 0.0)
    overnight = sum(r["pnl"] for r in rows)
    trading = total - overnight - income - interest + fees
    lines = {"Positions held overnight": overnight, "Dividends and borrow": income, "Interest": interest, "Fees": -fees, "Trading & other": trading}

    def by(key):
        out: Dict[str, float] = {}
        for r in rows:
            out[r.get(key) or "—"] = out.get(r.get(key) or "—", 0.0) + r["pnl"]
        return dict(sorted(out.items(), key=lambda kv: -abs(kv[1])))

    # factor view: exposures at d−1 (point in time) × factor moves on d
    factors: Dict[str, float] = {}
    factor_error = None
    try:
        from ..hedge.engine import price_positions, risk_vector
        from ..hedge.market import Market
        from ..hedge.risk import RiskModel, factor_series
        from .portfolio import is_instrument
        m = Market(research, i0)
        rk = RiskModel(m)
        pos = [{"id": r["asset_id"], "quantity": r["quantity"], **({"entry": h0["positions"][r["asset_id"]].get("avg")} if is_instrument(r["asset_id"]) else {})}
               for r in rows]
        R = risk_vector(price_positions(pos, m, rk, research.store))
        F = factor_series(research)
        for f, x in R.items():
            mv = (F.get(f) or [None] * len(cal))[i]
            if mv is not None and not f.startswith("IDIO:"):
                factors[f] = x * mv
    except Exception as e:                                # a factor view that cannot be built is reported, not guessed
        factor_error = f"{type(e).__name__}: {e}"
    grouped: Dict[str, float] = {}
    for f, v in factors.items():
        grouped[_group(f)] = grouped.get(_group(f), 0.0) + v
    grouped["Idiosyncratic & non-linear"] = overnight - sum(factors.values())
    return {"date": d, "previous": d0, "total": total, "lines": lines, "reconciles": abs(total - sum(lines.values())) < 1e-6,
            "positions": sorted(rows, key=lambda r: -abs(r["pnl"])), "by_asset_class": by("asset_class"), "by_sector": by("sector"),
            "by_country": by("country"), "by_currency": by("currency"),
            "by_factor": dict(sorted(grouped.items(), key=lambda kv: -abs(kv[1]))),
            "factors": dict(sorted(factors.items(), key=lambda kv: -abs(kv[1]))[:20]), "factor_error": factor_error,
            "dates": nh["dates"][-60:]}
