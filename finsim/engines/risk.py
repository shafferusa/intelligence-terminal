"""Risk engine: factor exposures, historical-simulation VaR / expected shortfall,
stress tests, component VaR, liquidity ladders and hard/soft limits.

Method (stated on every screen that shows the numbers):
  * Exposures are linearised sensitivities of every position and OTC trade to a
    small set of risk factors: each security's own return (equities, ETFs,
    futures, TRS references), a parallel shift of the zero curve (bonds by DV01,
    rate products by DV01), the IG/HY credit index (corporate bonds and CDS by
    CS01), commodity spot by code (futures and swaps by delta), FX spot by
    currency (cash, forwards, cross-currency swaps) and implied vol (listed
    options by vega, plus gamma on the underlying move).
  * VaR is a 1-day historical simulation: the last 250 daily factor changes are
    replayed through those sensitivities; VaR(95/99) is the loss quantile, ES
    the average loss beyond it. A parametric (normal) VaR from the same P&L
    series is shown alongside. 10-day = 1-day x sqrt(10).
  * Stress tests apply named or custom shocks through the same sensitivities
    (gamma included for options); they are not full revaluations.
  * Component VaR is the Euler allocation cov(position P&L, total P&L) / var.
  * Liquidity: days to liquidate at 20% of average daily volume; cash ladder from
    scheduled settlements, coupons, dividends and OTC flows.
Nothing here changes the ledger; the daily RISK_SNAPSHOT is a derived record.
"""
from __future__ import annotations

import math
from datetime import date
from decimal import Decimal
from typing import Dict, List, Optional, Tuple

from ..domain.events import E, Event
from ..domain.models import Portfolio
from ..engines.pricing import BondPricer, Instrument
from ..money import D, money, ZERO

WINDOW = 250
STRESS_SCENARIOS: Dict[str, Dict] = {
    "EQUITY_CRASH_20": {"label": "Equities −20%", "equity": -0.20, "vol_pts": 15.0, "spreads_bp": 150.0, "rates_bp": -50.0},
    "RATES_UP_100": {"label": "Rates +100bp (parallel)", "rates_bp": 100.0, "equity": -0.05},
    "RATES_DOWN_100": {"label": "Rates −100bp (parallel)", "rates_bp": -100.0},
    "CREDIT_WIDENING_200": {"label": "Credit spreads +200bp", "spreads_bp": 200.0, "equity": -0.08, "vol_pts": 8.0},
    "VOL_SPIKE": {"label": "Implied vol +20 points", "vol_pts": 20.0, "equity": -0.05},
    "COMMODITY_SELLOFF_25": {"label": "Commodities −25%", "commodity": -0.25, "equity": -0.03},
    "OIL_SPIKE_40": {"label": "Oil +40%", "commodity_by_code": {"CL": 0.40, "BRN": 0.40, "RB": 0.35, "HO": 0.35, "NG": 0.20}, "equity": -0.06, "rates_bp": 40.0},
    "USD_STRONG_10": {"label": "USD +10% vs all", "fx": -0.10, "commodity": -0.05},
    "GFC_2008": {"label": "2008-style: equities −35%, rates −150, spreads +400, vol +40", "equity": -0.35, "rates_bp": -150.0, "spreads_bp": 400.0, "vol_pts": 40.0, "commodity": -0.30, "fx": -0.08},
    "PANDEMIC_2020": {"label": "2020-style: equities −30%, rates −100, spreads +250, vol +45, oil −40", "equity": -0.30, "rates_bp": -100.0, "spreads_bp": 250.0, "vol_pts": 45.0, "commodity": -0.40, "fx": -0.05},
    "STAGFLATION": {"label": "Stagflation: rates +200, commodities +30%, equities −15%", "equity": -0.15, "rates_bp": 200.0, "spreads_bp": 100.0, "commodity": 0.30, "vol_pts": 10.0},
}
# limits by job: (soft, hard) as fractions of NAV or ratios; None = unlimited
LIMITS_BY_JOB: Dict[str, Dict[str, Tuple[Optional[float], Optional[float]]]] = {
    "SANDBOX": {},
    "PORTFOLIO_MANAGER": {"var99_pct_nav": (0.02, 0.035), "counterparty_pct_nav": (0.03, 0.06), "illiquid_pct_nav": (0.15, 0.30), "es_pct_nav": (0.03, 0.05)},
    "GLOBAL_MACRO": {"var99_pct_nav": (0.04, 0.07), "counterparty_pct_nav": (0.05, 0.10), "illiquid_pct_nav": (0.20, 0.40), "es_pct_nav": (0.06, 0.10)},
    "COMMODITY_TRADER": {"var99_pct_nav": (0.05, 0.08), "counterparty_pct_nav": (0.05, 0.10), "illiquid_pct_nav": (0.25, 0.50), "es_pct_nav": (0.07, 0.12)},
    "FIXED_INCOME_PM": {"var99_pct_nav": (0.015, 0.03), "counterparty_pct_nav": (0.03, 0.06), "illiquid_pct_nav": (0.20, 0.40), "es_pct_nav": (0.025, 0.045)},
    "HEDGE_FUND": {"var99_pct_nav": (0.05, 0.09), "counterparty_pct_nav": (0.05, 0.10), "illiquid_pct_nav": (0.30, 0.50), "es_pct_nav": (0.08, 0.14)},
    "DERIVATIVES_TRADER": {"var99_pct_nav": (0.04, 0.07), "counterparty_pct_nav": (0.05, 0.10), "illiquid_pct_nav": (0.30, 0.50), "es_pct_nav": (0.06, 0.10)},
}
DEFAULT_LIMITS = {"var99_pct_nav": (0.03, 0.05), "counterparty_pct_nav": (0.05, 0.10), "illiquid_pct_nav": (0.25, 0.50), "es_pct_nav": (0.05, 0.08)}
ZN_DURATION = 6.5


def _f(x) -> float:
    return float(x)


class RiskEngine:
    def __init__(self, world):
        self.w = world

    def register(self) -> None:
        self.w.on(E.RISK_SNAPSHOT, lambda world, ev: self._h_snapshot(ev))

    # ------------------------------------------------------------------ exposures
    def exposures(self, pf: Portfolio) -> List[Dict]:
        """One row per position / trade / cash balance with linear sensitivities in base currency."""
        w = self.w
        rows: List[Dict] = []
        curve = w.market.curve()
        for pos in pf.positions.values():
            if pos.quantity == 0:
                continue
            sec = w.securities[pos.security_id]
            row = {"id": sec.id, "kind": "POSITION", "name": sec.name, "bucket": w.pnl.bucket_of(sec), "quantity": _f(pos.quantity), "market_value": _f(pos.market_value),
                   "equity": {}, "dv01": 0.0, "cs01": 0.0, "commodity": {}, "fx": {}, "vega": 0.0, "gamma_usd": 0.0, "underlying": None, "rate_vega": 0.0}
            if sec.is_future:
                notional = _f(pos.quantity) * _f(w.market.last_bar(sec.id).close) * sec.multiplier if w.market.history.get(sec.id) else _f(pos.notional) * (1 if pos.quantity > 0 else -1)
                uc = sec.underlying_class or ""
                if uc.startswith("COMMODITY"):
                    row["commodity"][sec.underlying] = notional
                elif uc == "RATES":
                    row["dv01"] = -notional * ZN_DURATION * 1e-4
                else:
                    row["equity"]["SPXE"] = notional
                row["market_value"] = notional
            elif sec.is_option:
                g = pos.greeks or {}
                n = _f(pos.quantity) * sec.multiplier
                S = g.get("underlying", 0.0)
                src = sec.index_level_source or sec.underlying
                row["equity"][src] = g.get("delta", 0.0) * n * S
                row["gamma_usd"] = 0.5 * g.get("gamma", 0.0) * n * S * S     # P&L per (return)^2
                row["vega"] = g.get("vega", 0.0) * n
                row["underlying"] = src
            elif sec.is_bond:
                rm = Instrument(sec, pos.mark, w.current_date, curve).risk_metrics(pos.quantity)
                row["dv01"] = -rm.get("position_dv01", 0.0)      # P&L per +1bp
                if sec.asset_class == "CORP_BOND":
                    row["cs01"] = -rm.get("position_dv01", 0.0)
                    row["rating"] = sec.rating
            else:
                row["equity"][sec.id] = _f(pos.market_value)
            rows.append(row)
        for t in pf.otc_trades.values():
            if t.status != "OPEN":
                continue
            an = t.analytics or {}
            row = {"id": t.id, "kind": "OTC", "name": w.otc.describe(t), "bucket": w.otc.bucket_of(t.id), "quantity": _f(t.notional), "market_value": _f(t.mtm),
                   "equity": {}, "dv01": an.get("dv01", 0.0) or 0.0, "cs01": an.get("cs01", 0.0) or 0.0, "commodity": {}, "fx": {}, "vega": 0.0,
                   "gamma_usd": 0.0, "underlying": None, "rate_vega": an.get("vega", 0.0) or 0.0}
            if t.product == "TRS":
                row["equity"][t.terms["security_id"]] = an.get("delta_units", 0.0) * an.get("price", 0.0)
            elif t.product == "XCCY":
                row["fx"][t.terms["ccy"]] = an.get("fx_delta_1pct", 0.0) * 100.0
            elif t.product == "COMMODITY_SWAP":
                row["commodity"][t.terms["code"]] = an.get("delta_units", 0.0) * an.get("spot", 0.0)
            elif t.product == "CDS":
                row["rating"] = w.securities[t.terms["reference"]].rating
            rows.append(row)
        for ccy, ca in pf.cash.items():
            if ccy != pf.base_currency and ca.balance != 0:
                rows.append({"id": f"CASH:{ccy}", "kind": "CASH", "name": f"{ccy} cash", "bucket": "fx", "quantity": _f(ca.balance), "market_value": _f(ca.base_value),
                             "equity": {}, "dv01": 0.0, "cs01": 0.0, "commodity": {}, "fx": {ccy: _f(ca.base_value)}, "vega": 0.0, "gamma_usd": 0.0, "underlying": None, "rate_vega": 0.0})
        for f in pf.fx_forwards.values():
            if f.status != "OPEN":
                continue
            row = {"id": f.id, "kind": "FX_FORWARD", "name": f"FX forward {f.buy_ccy}/{f.sell_ccy}", "bucket": "fx", "quantity": _f(f.buy_amount), "market_value": _f(f.mtm),
                   "equity": {}, "dv01": 0.0, "cs01": 0.0, "commodity": {}, "fx": {}, "vega": 0.0, "gamma_usd": 0.0, "underlying": None, "rate_vega": 0.0}
            if f.buy_ccy != pf.base_currency:
                row["fx"][f.buy_ccy] = row["fx"].get(f.buy_ccy, 0.0) + _f(w.fx.to_base(f.buy_ccy, f.buy_amount))
            if f.sell_ccy != pf.base_currency:
                row["fx"][f.sell_ccy] = row["fx"].get(f.sell_ccy, 0.0) - _f(w.fx.to_base(f.sell_ccy, f.sell_amount))
            rows.append(row)
        return rows

    def factor_summary(self, pf: Portfolio, rows: Optional[List[Dict]] = None) -> Dict:
        w = self.w
        rows = rows if rows is not None else self.exposures(pf)
        eq_by_sec: Dict[str, float] = {}
        beta_dollar = 0.0
        for r in rows:
            for sid, v in r["equity"].items():
                eq_by_sec[sid] = eq_by_sec.get(sid, 0.0) + v
        for sid, v in eq_by_sec.items():
            beta_dollar += v * (w.securities[sid].beta if sid in w.securities else 1.0)
        commodity: Dict[str, float] = {}
        fx: Dict[str, float] = {}
        for r in rows:
            for k, v in r["commodity"].items():
                commodity[k] = commodity.get(k, 0.0) + v
            for k, v in r["fx"].items():
                fx[k] = fx.get(k, 0.0) + v
        return {"equity_dollar": sum(eq_by_sec.values()), "beta_dollar": beta_dollar, "equity_by_security": eq_by_sec,
                "dv01": sum(r["dv01"] for r in rows), "cs01": sum(r["cs01"] for r in rows), "vega": sum(r["vega"] for r in rows),
                "rate_vega": sum(r["rate_vega"] for r in rows), "gamma_usd": sum(r["gamma_usd"] for r in rows),
                "commodity": commodity, "fx": fx, "gross_commodity": sum(abs(v) for v in commodity.values()), "gross_fx": sum(abs(v) for v in fx.values())}

    # ------------------------------------------------------------------ factor history
    def factor_changes(self, window: int = WINDOW) -> List[Dict]:
        """Daily factor changes, oldest first: {equity: {sid: r}, rates_bp, spreads_ig_bp, spreads_hy_bp, commodity: {code: r}, fx: {ccy: r}, vol: {under: pts}}."""
        w = self.w
        m = w.market
        curves = m.curves[-(window + 1):]
        n = len(curves) - 1
        if n <= 0:
            return []
        dates = [c.date for c in curves]
        out: List[Dict] = []
        eq_hist = {sid: {b.date: _f(b.close) for b in m.history[sid][-(window + 2):]} for sid in m.history if m.history[sid] and not w.securities[sid].is_future}
        comm = {code: dict(h[-(window + 2):]) for code, h in m.commodities.spot_history.items()}
        fxh = {c: {d: s for d, s, r in h[-(window + 2):]} for c, h in m.fx.history.items()}
        volh = {u: {x[0]: x[1] for x in h[-(window + 2):]} for u, h in m.vol.history.items()}
        for i in range(1, len(curves)):
            d0, d1 = dates[i - 1], dates[i]
            c0, c1 = curves[i - 1], curves[i]
            eq = {}
            for sid, h in eq_hist.items():
                if d0 in h and d1 in h and h[d0] > 0:
                    eq[sid] = math.log(h[d1] / h[d0])
            cm = {code: math.log(h[d1] / h[d0]) for code, h in comm.items() if d0 in h and d1 in h and h[d0] > 0}
            fx = {c: math.log(h[d1] / h[d0]) for c, h in fxh.items() if c != "USD" and d0 in h and d1 in h and h[d0] > 0}
            vol = {u: (h[d1] - h[d0]) * 100.0 for u, h in volh.items() if d0 in h and d1 in h}
            out.append({"date": d1, "equity": eq, "rates_bp": (c1.rates[5] - c0.rates[5]) * 1e4, "spreads_ig_bp": c1.ig_spread_bps - c0.ig_spread_bps,
                        "spreads_hy_bp": c1.hy_spread_bps - c0.hy_spread_bps, "commodity": cm, "fx": fx, "vol": vol})
        return out

    # ------------------------------------------------------------------ P&L under a shock
    def pnl_under(self, rows: List[Dict], shock: Dict) -> Tuple[float, Dict[str, float]]:
        """P&L (base) of the exposure rows under a factor-change dict. Returns (total, per row id)."""
        w = self.w
        eq = shock.get("equity", {})
        eq_all = shock.get("equity_all")           # uniform equity shock (stress): security move = beta x shock
        rates = shock.get("rates_bp", 0.0)
        ig, hy = shock.get("spreads_ig_bp", 0.0), shock.get("spreads_hy_bp", 0.0)
        cm = shock.get("commodity", {})
        cm_all = shock.get("commodity_all")
        fx = shock.get("fx", {})
        fx_all = shock.get("fx_all")
        vol = shock.get("vol", {})
        vol_all = shock.get("vol_all")
        rate_vol_pts = shock.get("rate_vol_pts", (vol_all or 0.0) / 4.0 if vol_all is not None else 0.0)
        total = 0.0
        per: Dict[str, float] = {}
        for r in rows:
            p = 0.0
            for sid, v in r["equity"].items():
                if eq_all is not None:
                    ret = eq_all * (w.securities[sid].beta if sid in w.securities else 1.0)
                else:
                    ret = eq.get(sid, 0.0)
                p += v * ret
                if r["gamma_usd"] and r["underlying"] == sid:
                    p += r["gamma_usd"] * ret * ret
            p += r["dv01"] * rates
            if r["cs01"]:
                hy_name = (r.get("rating") or "BBB") not in ("AAA", "AA+", "AA", "AA-", "A+", "A", "A-", "BBB+", "BBB", "BBB-")
                p += r["cs01"] * (hy if hy_name else ig)
            for code, v in r["commodity"].items():
                p += v * (cm_all if cm_all is not None and code not in cm else cm.get(code, 0.0))
            for ccy, v in r["fx"].items():
                p += v * (fx_all if fx_all is not None else fx.get(ccy, 0.0))
            if r["vega"]:
                p += r["vega"] * (vol_all if vol_all is not None else vol.get(r["underlying"], 0.0))
            if r["rate_vega"] and rate_vol_pts:
                p += r["rate_vega"] * rate_vol_pts
            per[r["id"]] = per.get(r["id"], 0.0) + p
            total += p
        return total, per

    # ------------------------------------------------------------------ VaR
    def var(self, pf: Portfolio, rows: Optional[List[Dict]] = None) -> Dict:
        rows = rows if rows is not None else self.exposures(pf)
        changes = self.factor_changes()
        if not rows or len(changes) < 20:
            return {"available": False, "reason": "needs positions and at least 20 days of factor history", "days": len(changes)}
        pnls = []
        per_rows: Dict[str, List[float]] = {r["id"]: [] for r in rows}
        for ch in changes:
            tot, per = self.pnl_under(rows, ch)
            pnls.append(tot)
            for k in per_rows:
                per_rows[k].append(per.get(k, 0.0))
        srt = sorted(pnls)
        n = len(srt)

        def q(p: float) -> float:
            k = max(0, min(n - 1, int(math.floor(p * n))))
            return srt[k]
        var95, var99 = -q(0.05), -q(0.01)
        tail = srt[:max(1, int(math.floor(0.025 * n)))]
        es975 = -sum(tail) / len(tail)
        mean = sum(pnls) / n
        sd = math.sqrt(sum((x - mean) ** 2 for x in pnls) / max(1, n - 1))
        var_total = sd * sd
        comp = {}
        for k, series in per_rows.items():
            cov = sum((series[i] - sum(series) / n) * (pnls[i] - mean) for i in range(n)) / max(1, n - 1)
            comp[k] = (cov / var_total) * var99 if var_total > 0 else 0.0
        worst_i = min(range(n), key=lambda i: pnls[i])
        return {"available": True, "days": n, "var95": var95, "var99": var99, "es975": es975, "parametric_var99": 2.326 * sd, "sd_daily": sd,
                "var99_10d": var99 * math.sqrt(10), "worst_day": {"date": changes[worst_i]["date"], "pnl": pnls[worst_i]}, "best_day": max(pnls),
                "component_var99": comp, "series": [{"date": c["date"], "pnl": round(p, 2)} for c, p in zip(changes, pnls)][-60:],
                "method": "1-day historical simulation over the last %d sessions of factor changes replayed through linear sensitivities (delta, DV01, CS01, vega, "
                          "FX and commodity deltas; gamma on the underlying move); VaR = loss quantile, ES = mean loss beyond the 97.5%% quantile; "
                          "parametric VaR = 2.326 x standard deviation of the same P&L series; component VaR = Euler allocation." % n}

    # ------------------------------------------------------------------ stress
    def stress(self, pf: Portfolio, rows: Optional[List[Dict]] = None, custom: Optional[Dict] = None) -> Dict:
        rows = rows if rows is not None else self.exposures(pf)
        scenarios = dict(STRESS_SCENARIOS)
        if custom:
            scenarios = {"CUSTOM": {"label": custom.get("label", "Custom"), **{k: v for k, v in custom.items() if k != "label"}}}
        out = {}
        for name, sc in scenarios.items():
            shock = {"equity_all": sc.get("equity", 0.0), "rates_bp": sc.get("rates_bp", 0.0), "spreads_ig_bp": sc.get("spreads_bp", 0.0) * 0.5,
                     "spreads_hy_bp": sc.get("spreads_bp", 0.0), "commodity_all": sc.get("commodity", 0.0), "commodity": sc.get("commodity_by_code", {}),
                     "fx_all": sc.get("fx", 0.0), "vol_all": sc.get("vol_pts", 0.0)}
            tot, per = self.pnl_under(rows, shock)
            top = sorted(per.items(), key=lambda kv: kv[1])[:5]
            out[name] = {"label": sc.get("label", name), "pnl": tot, "shock": {k: v for k, v in sc.items() if k != "label"}, "worst_contributors": top}
        return out

    # ------------------------------------------------------------------ liquidity
    def liquidity(self, pf: Portfolio) -> Dict:
        w = self.w
        nav = _f(w.pnl.compute_summary(pf)["nav"])
        rows = []
        buckets = {"1d": 0.0, "5d": 0.0, "20d": 0.0, "over": 0.0}
        for pos in pf.positions.values():
            if pos.quantity == 0:
                continue
            sec = w.securities[pos.security_id]
            depth = max(1.0, sec.adv * w.market.regime().depth_mult * 0.2)
            days = abs(_f(pos.quantity)) / depth
            mv = abs(_f(pos.notional if pos.is_future else pos.market_value))
            b = "1d" if days <= 1 else "5d" if days <= 5 else "20d" if days <= 20 else "over"
            buckets[b] += mv
            rows.append({"id": sec.id, "quantity": _f(pos.quantity), "market_value": mv, "adv": sec.adv, "days_to_liquidate": days, "bucket": b,
                         "encumbered": _f(pf.pledged_quantity(sec.id)) + _f(w.options.covered_shares_needed(pf, sec.id))})
        rows.sort(key=lambda r: -r["days_to_liquidate"])
        ladder = self.cash_ladder(pf)
        illiquid = buckets["20d"] + buckets["over"]
        return {"positions": rows, "buckets": buckets, "illiquid_pct_nav": illiquid / nav if nav else 0.0, "nav": nav, "cash_ladder": ladder,
                "regime_depth": w.market.regime().depth_mult}

    def cash_ladder(self, pf: Portfolio) -> List[Dict]:
        """Known cash flows over the next 20 business days by day."""
        w = self.w
        today = w.current_date
        horizon = [w.calendar.add_business_days(today, i).isoformat() for i in range(1, 21)]
        flows: Dict[str, List[Dict]] = {d: [] for d in horizon}
        for si in pf.settlements.values():
            if si.status in ("PENDING", "MATCHED", "FAILED"):
                d = max(si.settlement_date, horizon[0])
                if d in flows:
                    flows[d].append({"kind": "SETTLEMENT", "amount": _f(-si.cash_amount if si.instruction_type == "RVP" else si.cash_amount), "ref": si.id})
        for ca in w.corporate_actions.values():
            ent = ca.entitlements.get(pf.id)
            if ent and not ent.get("paid") and ca.pay_date in flows:
                flows[ca.pay_date].append({"kind": "DIVIDEND", "amount": _f(ent["amount"]), "ref": ca.id})
        for pos in pf.positions.values():
            sec = w.securities[pos.security_id]
            if sec.is_bond and pos.quantity > 0:
                for d, cf in BondPricer.cash_flows(sec, today)[:3]:
                    if d.isoformat() in flows:
                        flows[d.isoformat()].append({"kind": "COUPON", "amount": _f(pos.quantity) * cf / 100, "ref": sec.id})
        for u in w.otc.upcoming(pf, 20):
            t = pf.otc_trades[u["trade_id"]]
            amt = None
            if u["kind"] == "FIXED_COUPON":
                from ..engines import otc_pricing as px
                per = next(((s, e, p) for s, e, p in w.otc.periods(t.start, t.maturity, t.terms["fixed_months"]) if p.isoformat() == u["date"]), None)
                if per:
                    amt = _f(t.notional) * t.terms["fixed_rate"] * px.yearfrac(per[0], per[1], "30/360") * (-1 if t.terms["pay_fixed"] else 1)
            elif u["kind"] == "CDS_PREMIUM":
                from ..engines import otc_pricing as px
                per = next(((s, e, p) for s, e, p in w.otc.periods(t.start, t.maturity, 3) if p.isoformat() == u["date"]), None)
                if per:
                    amt = _f(t.notional) * t.terms["running_bps"] / 1e4 * px.yearfrac(per[0], per[1], "ACT/360") * (-1 if t.terms["buyer"] else 1)
            if u["date"] in flows:
                flows[u["date"]].append({"kind": u["kind"], "amount": amt, "ref": t.id, "estimated": amt is None})
        out = []
        running = _f(pf.cash_account(pf.base_currency).balance)
        for d in horizon:
            known = sum(f["amount"] for f in flows[d] if f.get("amount") is not None)
            running += known
            out.append({"date": d, "flows": flows[d], "net_known": known, "projected_cash": running})
        return out

    # ------------------------------------------------------------------ limits
    def limits(self, pf: Portfolio, var_: Dict, expo: Dict, liq: Dict) -> List[Dict]:
        w = self.w
        job = w.careers.job_for(pf)
        table = LIMITS_BY_JOB.get(pf.job, DEFAULT_LIMITS)
        nav = liq["nav"]
        rows = []
        if not nav:
            return rows

        def add(name, value, soft, hard, unit="pct"):
            util = (value / hard) if hard else (value / soft if soft else 0.0)
            status = "HARD" if hard is not None and value > hard else "SOFT" if soft is not None and value > soft else "OK"
            rows.append({"name": name, "value": value, "soft": soft, "hard": hard, "utilization": util, "status": status, "unit": unit})
        if var_.get("available"):
            s, h = table.get("var99_pct_nav", (None, None))
            add("VaR 99% 1-day / NAV", var_["var99"] / nav, s, h)
            s, h = table.get("es_pct_nav", (None, None))
            add("Expected shortfall 97.5% / NAV", var_["es975"] / nav, s, h)
        s, h = table.get("counterparty_pct_nav", (None, None))
        add("Largest counterparty exposure / NAV", max([r["pct_nav"] for r in expo["rows"]] or [0.0]), s, h)
        s, h = table.get("illiquid_pct_nav", (None, None))
        add("Positions needing > 20 days to liquidate / NAV", liq["illiquid_pct_nav"], s, h)
        if job and job.key != "SANDBOX":
            summ = w.pnl.compute_summary(pf)
            lev = _f(summ["gross_exposure"]) / nav
            add("Gross leverage", lev, job.max_gross_leverage * 0.8, job.max_gross_leverage, "x")
            dd = _f((pf.peak_nav - summ["nav"]) / pf.peak_nav) if pf.peak_nav else 0.0
            add("Drawdown from peak", dd, job.max_drawdown * 0.6, job.max_drawdown)
        return rows

    def hard_breaches(self, pf: Portfolio) -> List[str]:
        snap = pf.risk_history[-1] if pf.risk_history else None
        if not snap or snap["date"] != self.w.current_date.isoformat():
            return []
        return [l["name"] for l in snap["limits"] if l["status"] == "HARD"]

    def blocks_order(self, pf: Portfolio, sec, side: str) -> Optional[str]:
        """While a hard limit is breached, only risk-reducing orders are accepted."""
        hard = self.hard_breaches(pf)
        if not hard:
            return None
        pos = pf.positions.get(sec.id)
        q = pos.quantity if pos else ZERO
        reducing = (side == "SELL" and q > 0) or (side == "BUY" and q < 0)
        if reducing:
            return None
        return f"hard risk limit breached ({'; '.join(hard)}): only risk-reducing orders are accepted until the book is back within limits"

    # ------------------------------------------------------------------ daily
    def report(self, pf: Portfolio) -> Dict:
        rows = self.exposures(pf)
        fs = self.factor_summary(pf, rows)
        v = self.var(pf, rows)
        st = self.stress(pf, rows)
        liq = self.liquidity(pf)
        expo = self.w.otc.exposure(pf)
        lim = self.limits(pf, v, expo, liq)
        return {"date": self.w.current_date.isoformat(), "factors": fs, "var": v, "stress": st, "liquidity": liq, "limits": lim,
                "counterparty": {"total_current_exposure": expo["total_current_exposure"], "total_pfe": expo["total_pfe"],
                                 "largest": max(expo["rows"], key=lambda r: r["current_exposure"], default=None)},
                "rows": rows}

    def process_day(self, cause: Event) -> None:
        w = self.w
        for pf in w.portfolios.values():
            rep = self.report(pf)
            v = rep["var"]
            payload = {"portfolio_id": pf.id, "date": rep["date"], "nav": rep["liquidity"]["nav"],
                       "var95": v.get("var95"), "var99": v.get("var99"), "es975": v.get("es975"), "parametric_var99": v.get("parametric_var99"), "var_days": v.get("days"),
                       "stress": {k: s["pnl"] for k, s in rep["stress"].items()}, "worst_stress": min(rep["stress"].items(), key=lambda kv: kv[1]["pnl"])[0],
                       "factors": {k: val for k, val in rep["factors"].items() if not isinstance(val, dict)},
                       "commodity": rep["factors"]["commodity"], "fx": rep["factors"]["fx"], "illiquid_pct_nav": rep["liquidity"]["illiquid_pct_nav"],
                       "limits": rep["limits"], "counterparty_exposure": rep["counterparty"]["total_current_exposure"]}
            w.emit(E.RISK_SNAPSHOT, payload, cause_id=cause.id, portfolio_id=pf.id)
            for l in rep["limits"]:
                if l["status"] in ("SOFT", "HARD") and l["name"] not in ("Gross leverage", "Drawdown from peak"):
                    w.emit(E.RISK_BREACH, {"portfolio_id": pf.id, "kind": "VAR" if "VaR" in l["name"] or "shortfall" in l["name"] else "COUNTERPARTY" if "counterparty" in l["name"] else "LIQUIDITY",
                                           "text": f"{l['name']} = {l['value']:.2%} vs {'hard' if l['status'] == 'HARD' else 'soft'} limit {(l['hard'] if l['status'] == 'HARD' else l['soft']):.2%}",
                                           "value": l["value"], "nav": D(repr(rep["liquidity"]["nav"])), "severity": l["status"]}, cause_id=cause.id, portfolio_id=pf.id)

    def _h_snapshot(self, ev: Event) -> None:
        p = ev.payload
        pf = self.w.portfolios[p["portfolio_id"]]
        pf.risk_history.append({k: v for k, v in p.items() if k != "portfolio_id"})
        if len(pf.risk_history) > 260:
            pf.risk_history = pf.risk_history[-260:]
