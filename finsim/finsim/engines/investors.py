"""Hedge-fund investors: a seeded investor base that subscribes and redeems on
month-end dealing dates in response to the fund's trailing performance,
drawdown and the regime; management and performance fees.

  * Subscriptions/redemptions are notified on the 15th (business day) and
    dealt at month end as capital flows (CAPITAL_CONTRIBUTED / CAPITAL_WITHDRAWN).
  * Management fee: 1.5% of NAV per year, accrued daily (5900), paid monthly.
  * Performance fee: 15% of the year's net profit above the high-water mark,
    crystallised at year end (5910).
  * A large redemption is a liquidity event: cash must be raised before the
    dealing date or the prime broker finances it.
"""
from __future__ import annotations

import random
from datetime import date
from decimal import Decimal
from typing import Dict, List, Optional

from ..domain.events import Event
from ..engines.ledger import dr, cr
from ..money import D, money, ZERO

MGMT_FEE = 0.015
PERF_FEE = 0.15
INVESTOR_NAMES = ["Yale Endowment", "CalPERS", "Walton Family Office", "Blackstone Alternative Asset Management", "GIC (Singapore)",
                  "Ontario Teachers' Pension Plan", "Ford Foundation", "MetLife General Account"]


class IE:
    INVESTOR_FLOW_NOTIFIED = "INVESTOR_FLOW_NOTIFIED"
    CAPITAL_WITHDRAWN = "CAPITAL_WITHDRAWN"
    FEE_ACCRUED = "FEE_ACCRUED"
    FEE_PAID = "FEE_PAID"


class InvestorEngine:
    def __init__(self, world):
        self.w = world

    def register(self) -> None:
        w = self.w
        w.on(IE.INVESTOR_FLOW_NOTIFIED, lambda world, ev: self._h_notified(ev))
        w.on(IE.CAPITAL_WITHDRAWN, lambda world, ev: self._h_withdrawn(ev))
        w.on(IE.FEE_ACCRUED, lambda world, ev: self._h_fee_accrued(ev))
        w.on(IE.FEE_PAID, lambda world, ev: self._h_fee_paid(ev))

    def applies(self, pf) -> bool:
        return pf.job == "HEDGE_FUND"

    def book(self, pf) -> Dict:
        st = pf.investors
        return {"investors": st.get("holders", {}), "pending": st.get("pending", []), "mgmt_fee_accrued": D(st.get("mgmt_accrued", 0)),
                "fees_paid": D(st.get("fees_paid", 0)), "high_water_mark": D(st.get("hwm", pf.contributed_capital)), "flows": st.get("flows", [])[-40:],
                "mgmt_fee": MGMT_FEE, "perf_fee": PERF_FEE, "total_subscribed": sum((D(v) for v in st.get("holders", {}).values()), ZERO)}

    # ------------------------------------------------------------------ daily
    def process_day(self, cause: Event, prev: date) -> None:
        w = self.w
        today = w.current_date
        for pf in w.portfolios.values():
            if not self.applies(pf):
                continue
            st = pf.investors
            if not st.get("holders"):
                self._seed_holders(pf, cause)
                st = pf.investors
            nav = w.pnl.compute_summary(pf)["nav"]
            days = max(1, (today - prev).days)
            # management fee accrual
            fee = money(nav * D(str(MGMT_FEE)) * days / 365)
            if fee > 0:
                w.emit(IE.FEE_ACCRUED, {"portfolio_id": pf.id, "kind": "MANAGEMENT", "amount": fee, "nav": nav, "days": days}, cause_id=cause.id, portfolio_id=pf.id)
            # notifications on the 15th, dealing at month end
            if today.day >= 15 and prev.day < 15 or (today.month != prev.month and today.day >= 15):
                self._notify(pf, nav, cause)
            if w.calendar.is_month_end(today):
                self._deal(pf, nav, cause)
                if D(st.get("mgmt_accrued", 0)) > 0:
                    w.emit(IE.FEE_PAID, {"portfolio_id": pf.id, "kind": "MANAGEMENT", "amount": D(st["mgmt_accrued"]), "currency": pf.base_currency}, cause_id=cause.id, portfolio_id=pf.id)
                if today.month == 12:
                    self._performance_fee(pf, cause)

    def _seed_holders(self, pf, cause: Event) -> None:
        rng = random.Random(f"{self.w.seed}|investors|{pf.id}")
        cap = pf.contributed_capital
        weights = [rng.uniform(0.5, 2.0) for _ in INVESTOR_NAMES[:6]]
        tot = sum(weights)
        holders = {name: money(cap * D(repr(wgt / tot))) for name, wgt in zip(INVESTOR_NAMES[:6], weights)}
        diff = cap - sum(holders.values(), ZERO)
        holders[INVESTOR_NAMES[0]] += diff
        self.w.emit(IE.INVESTOR_FLOW_NOTIFIED, {"portfolio_id": pf.id, "kind": "SEED", "holders": holders, "hwm": cap}, cause_id=cause.id, portfolio_id=pf.id)

    def _trailing(self, pf, months: int) -> Optional[float]:
        snaps = pf.nav_history
        if len(snaps) < 22:
            return None
        n = min(len(snaps) - 1, 21 * months)
        a, b = snaps[-1 - n], snaps[-1]
        flows = sum((s.capital_flows for s in snaps[-n:]), ZERO)
        return float((b.nav - flows - a.nav) / a.nav) if a.nav else None

    def _notify(self, pf, nav: Decimal, cause: Event) -> None:
        w = self.w
        st = pf.investors
        rng = random.Random(f"{w.seed}|flows|{pf.id}|{w.current_date.isoformat()}")
        r3 = self._trailing(pf, 3)
        dd = float((pf.peak_nav - nav) / pf.peak_nav) if pf.peak_nav else 0.0
        regime = w.market.state.regime
        pending = []
        for name, held in st["holders"].items():
            held = D(held)
            if held <= 0:
                continue
            base = -0.02 if regime in ("RECESSION", "LIQUIDITY_STRESS") else 0.01
            perf = 0.0 if r3 is None else max(-0.25, min(0.25, r3 * 2.5))
            score = base + perf - max(0.0, dd - 0.05) * 2.0 + rng.gauss(0, 0.04)
            if score > 0.06:
                amt = money(held * D(repr(min(0.5, score))))
                pending.append({"investor": name, "kind": "SUBSCRIPTION", "amount": amt, "dealing": w.calendar.month_end(w.current_date).isoformat()})
            elif score < -0.06:
                amt = money(held * D(repr(min(1.0, -score * 2))))
                pending.append({"investor": name, "kind": "REDEMPTION", "amount": amt, "dealing": w.calendar.month_end(w.current_date).isoformat()})
        if pending:
            w.emit(IE.INVESTOR_FLOW_NOTIFIED, {"portfolio_id": pf.id, "kind": "NOTICE", "pending": pending, "trailing_3m": r3, "drawdown": dd}, cause_id=cause.id, portfolio_id=pf.id)

    def _deal(self, pf, nav: Decimal, cause: Event) -> None:
        w = self.w
        st = pf.investors
        today = w.current_date.isoformat()
        for p in list(st.get("pending", [])):
            if p["dealing"] > today:
                continue
            amt = D(p["amount"])
            if p["kind"] == "SUBSCRIPTION":
                w.emit("CAPITAL_CONTRIBUTED", {"portfolio_id": pf.id, "currency": pf.base_currency, "amount": amt, "reason": f"subscription from {p['investor']}", "investor": p["investor"]},
                       cause_id=cause.id, portfolio_id=pf.id)
            else:
                w.emit(IE.CAPITAL_WITHDRAWN, {"portfolio_id": pf.id, "currency": pf.base_currency, "amount": amt, "reason": f"redemption by {p['investor']}", "investor": p["investor"]},
                       cause_id=cause.id, portfolio_id=pf.id)

    def _performance_fee(self, pf, cause: Event) -> None:
        w = self.w
        st = pf.investors
        nav = w.pnl.compute_summary(pf)["nav"]
        hwm = D(st.get("hwm", pf.contributed_capital))
        profit = nav - hwm
        if profit > 0:
            fee = money(profit * D(str(PERF_FEE)))
            w.emit(IE.FEE_ACCRUED, {"portfolio_id": pf.id, "kind": "PERFORMANCE", "amount": fee, "nav": nav, "hwm": hwm}, cause_id=cause.id, portfolio_id=pf.id)
            w.emit(IE.FEE_PAID, {"portfolio_id": pf.id, "kind": "PERFORMANCE", "amount": fee, "currency": pf.base_currency, "new_hwm": nav - fee}, cause_id=cause.id, portfolio_id=pf.id)

    # ------------------------------------------------------------------ handlers
    def _h_notified(self, ev: Event) -> None:
        p = ev.payload
        pf = self.w.portfolios[p["portfolio_id"]]
        st = pf.investors
        if p["kind"] == "SEED":
            st["holders"] = {k: D(v) for k, v in p["holders"].items()}
            st["hwm"] = D(p["hwm"])
            st["start_capital"] = D(p["hwm"])
            st.setdefault("pending", [])
            st.setdefault("flows", [])
        else:
            st.setdefault("pending", []).extend([{**x, "amount": D(x["amount"]), "notified": ev.sim_date} for x in p["pending"]])
            st["flows"] = st.get("flows", []) + [{"date": ev.sim_date, "kind": "NOTICE", "detail": f"{x['investor']} {x['kind'].lower()} {D(x['amount']):,.0f} for {x['dealing']}"} for x in p["pending"]]

    def _h_withdrawn(self, ev: Event) -> None:
        w = self.w
        p = ev.payload
        pf = w.portfolios[p["portfolio_id"]]
        amt = D(p["amount"])
        ca = pf.cash_account(p["currency"])
        ca.balance -= amt
        ca.base_value -= amt
        pf.contributed_capital -= amt
        pf.day_capital_flows -= amt
        st = pf.investors
        inv = p.get("investor")
        if inv and inv in st.get("holders", {}):
            st["holders"][inv] = max(ZERO, D(st["holders"][inv]) - amt)
        st["pending"] = [x for x in st.get("pending", []) if not (x.get("investor") == inv and x["kind"] == "REDEMPTION" and D(x["amount"]) == amt)]
        st["flows"] = st.get("flows", []) + [{"date": ev.sim_date, "kind": "REDEMPTION", "detail": f"{inv} redeemed {amt:,.0f}"}]
        w.record_cash_movement(pf, p["currency"], -amt, "CAPITAL", p.get("reason", "capital withdrawn"), ev)
        w.post(pf.id, f"Capital withdrawn {p['currency']} {amt:,.2f} ({p.get('reason', '')})", [dr("3000", amt), cr(f"1010:{p['currency']}", amt)], ev, {"kind": "CAPITAL"})

    def _h_fee_accrued(self, ev: Event) -> None:
        w = self.w
        p = ev.payload
        pf = w.portfolios[p["portfolio_id"]]
        amt = D(p["amount"])
        st = pf.investors
        acct = "5900" if p["kind"] == "MANAGEMENT" else "5910"
        st["mgmt_accrued"] = D(st.get("mgmt_accrued", 0)) + amt if p["kind"] == "MANAGEMENT" else D(st.get("mgmt_accrued", 0))
        st["perf_accrued"] = D(st.get("perf_accrued", 0)) + (amt if p["kind"] == "PERFORMANCE" else ZERO)
        w.post(pf.id, f"{p['kind'].title()} fee accrual {amt:,.2f}", [dr(acct, amt, None, f"{p['kind'].lower()} fee"), cr("2360", amt, None, "fees payable to the manager")], ev, {"kind": "FEE"})

    def _h_fee_paid(self, ev: Event) -> None:
        w = self.w
        p = ev.payload
        pf = w.portfolios[p["portfolio_id"]]
        amt = D(p["amount"])
        st = pf.investors
        ca = pf.cash_account(p["currency"])
        ca.balance -= amt
        ca.base_value -= amt
        if p["kind"] == "MANAGEMENT":
            st["mgmt_accrued"] = D(st.get("mgmt_accrued", 0)) - amt
        else:
            st["perf_accrued"] = D(st.get("perf_accrued", 0)) - amt
            st["hwm"] = D(p["new_hwm"])
        st["fees_paid"] = D(st.get("fees_paid", 0)) + amt
        st["flows"] = st.get("flows", []) + [{"date": ev.sim_date, "kind": "FEE", "detail": f"{p['kind'].lower()} fee paid {amt:,.0f}"}]
        w.record_cash_movement(pf, p["currency"], -amt, "FEE", f"{p['kind'].title()} fee paid to the manager", ev)
        w.post(pf.id, f"{p['kind'].title()} fee paid {amt:,.2f}", [dr("2360", amt, None, "fees payable settled"), cr(f"1010:{p['currency']}", amt, None, "fee paid")], ev, {"kind": "FEE"})
