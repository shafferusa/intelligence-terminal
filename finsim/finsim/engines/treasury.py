"""Corporate treasury job: the book is a company's treasury rather than a fund.

The company has operating flows (monthly foreign-currency receipts and
base-currency payments), a debt stack (fixed notes and a floating-rate loan
with quarterly coupons, maturities to refinance), a minimum liquidity policy
and hedging objectives: hedge a share of the next twelve months' FX receipts
with forwards or cross-currency swaps, keep the floating share of interest cost
within a band using swaps, and never breach minimum liquidity. Everything is
scheduled and seeded; the flows are ordinary cash movements and ledger entries.
"""
from __future__ import annotations

import random
from datetime import date
from decimal import Decimal
from typing import Dict, List, Optional

from ..domain.events import Event
from ..engines.ledger import dr, cr
from ..engines.pricing import _add_months
from ..money import D, money, ZERO


class TE:
    TREASURY_SETUP = "TREASURY_SETUP"
    OPERATING_FLOW = "OPERATING_FLOW"
    DEBT_SERVICE = "DEBT_SERVICE"


DEBT_STACK = [
    {"id": "NOTE-2028", "kind": "FIXED", "principal": 150_000_000, "coupon": 0.0475, "months": 6, "maturity_years": 2.5},
    {"id": "NOTE-2031", "kind": "FIXED", "principal": 200_000_000, "coupon": 0.0525, "months": 6, "maturity_years": 5.5},
    {"id": "TERM-LOAN", "kind": "FLOATING", "principal": 250_000_000, "spread_bps": 175.0, "months": 3, "maturity_years": 4.0},
]
RECEIPTS = {"EUR": 9_000_000, "GBP": 4_000_000, "JPY": 900_000_000}     # monthly foreign receipts (local units)
BASE_PAYMENTS = 14_000_000                                              # monthly base-currency payroll/suppliers
BASE_RECEIPTS = 6_000_000
MIN_LIQUIDITY = 40_000_000
HEDGE_TARGET = 0.5


class TreasuryEngine:
    def __init__(self, world):
        self.w = world

    def register(self) -> None:
        w = self.w
        w.on(TE.TREASURY_SETUP, lambda world, ev: self._h_setup(ev))
        w.on(TE.OPERATING_FLOW, lambda world, ev: self._h_flow(ev))
        w.on(TE.DEBT_SERVICE, lambda world, ev: self._h_debt(ev))

    def applies(self, pf) -> bool:
        return pf.job == "TREASURY_MANAGER"

    def setup(self, pf, cause: Event) -> None:
        w = self.w
        start = w.current_date
        debt = []
        for d in DEBT_STACK:
            mat = w.calendar.roll(_add_months(start, int(d["maturity_years"] * 12)))
            debt.append({**d, "issue": start.isoformat(), "maturity": mat.isoformat(), "paid": []})
        w.emit(TE.TREASURY_SETUP, {"portfolio_id": pf.id, "debt": debt, "receipts": RECEIPTS, "base_payments": BASE_PAYMENTS, "base_receipts": BASE_RECEIPTS,
                                   "min_liquidity": MIN_LIQUIDITY, "hedge_target": HEDGE_TARGET}, cause_id=cause.id if cause else None, portfolio_id=pf.id)

    # ------------------------------------------------------------------ daily
    def process_day(self, cause: Event, prev: date) -> None:
        w = self.w
        today = w.current_date
        for pf in w.portfolios.values():
            if not self.applies(pf):
                continue
            t = pf.treasury
            if not t:
                self.setup(pf, cause)
                t = pf.treasury
            # monthly operating flows on the 20th business-day-rolled
            flow_day = w.calendar.roll(date(today.year, today.month, 20))
            if today == flow_day and t.get("last_flow_month") != f"{today.year}-{today.month:02d}":
                rng = random.Random(f"{w.seed}|treasury|{pf.id}|{today.isoformat()}")
                legs = [{"currency": c, "amount": money(D(a) * D(repr(rng.uniform(0.85, 1.15))))} for c, a in t["receipts"].items()]
                legs.append({"currency": pf.base_currency, "amount": money(D(t["base_receipts"]) * D(repr(rng.uniform(0.9, 1.1))) - D(t["base_payments"]) * D(repr(rng.uniform(0.95, 1.05))))})
                w.emit(TE.OPERATING_FLOW, {"portfolio_id": pf.id, "month": f"{today.year}-{today.month:02d}", "legs": legs}, cause_id=cause.id, portfolio_id=pf.id)
            # debt service
            for d in t["debt"]:
                if d.get("retired"):
                    continue
                per = self._periods(d)
                for (s, e, p) in per:
                    key = p.isoformat()
                    if p <= today and key not in d["paid"]:
                        tau = (e - s).days / 360.0
                        if d["kind"] == "FIXED":
                            rate = d["coupon"]
                        else:
                            rate = w.market.curve().policy_rate + d["spread_bps"] / 1e4
                        amt = money(D(d["principal"]) * D(repr(rate)) * D(repr(tau)))
                        principal = D(d["principal"]) if p >= date.fromisoformat(d["maturity"]) else ZERO
                        w.emit(TE.DEBT_SERVICE, {"portfolio_id": pf.id, "debt_id": d["id"], "pay_date": key, "interest": amt, "principal": principal, "rate": rate,
                                                 "currency": pf.base_currency}, cause_id=cause.id, portfolio_id=pf.id)

    def _periods(self, d: Dict):
        return [(s, e, p) for (s, e, p) in __import__("finsim.engines.otc_pricing", fromlist=["schedule"]).schedule(date.fromisoformat(d["issue"]), date.fromisoformat(d["maturity"]), int(d["months"]), self.w.calendar.roll)]

    # ------------------------------------------------------------------ analytics
    def dashboard(self, pf) -> Dict:
        w = self.w
        t = pf.treasury
        if not t:
            return {}
        today = w.current_date
        fx_exposure = {}
        for c, a in t["receipts"].items():
            fx_exposure[c] = {"next_12m_receipts_local": D(a) * 12, "next_12m_receipts_base": w.fx.to_base(c, D(a) * 12),
                              "hedged_base": sum((w.fx.to_base(f.sell_ccy, f.sell_amount) for f in pf.fx_forwards.values() if f.status == "OPEN" and f.sell_ccy == c), ZERO)
                              + sum((money(D(str(tr.terms["ccy_notional"]))) * w.fx.usd(c) for tr in pf.otc_trades.values() if tr.status == "OPEN" and tr.product == "XCCY" and tr.terms["ccy"] == c and tr.terms["direction"] == "BORROW_FOREIGN"), ZERO)}
            fx_exposure[c]["hedge_ratio"] = float(fx_exposure[c]["hedged_base"] / fx_exposure[c]["next_12m_receipts_base"]) if fx_exposure[c]["next_12m_receipts_base"] else 0.0
        floating = sum(D(d["principal"]) for d in t["debt"] if d["kind"] == "FLOATING" and not d.get("retired"))
        total = sum(D(d["principal"]) for d in t["debt"] if not d.get("retired"))
        swapped = sum((tr.notional for tr in pf.otc_trades.values() if tr.status == "OPEN" and tr.product == "IRS" and tr.terms.get("pay_fixed")), ZERO)
        cash = pf.cash_account(pf.base_currency).balance
        interest_cost = sum(D(d["principal"]) * D(repr(d["coupon"] if d["kind"] == "FIXED" else w.market.curve().policy_rate + d["spread_bps"] / 1e4)) for d in t["debt"] if not d.get("retired"))
        return {"debt": t["debt"], "total_debt": total, "floating_debt": floating, "floating_share_after_swaps": float((floating - min(swapped, floating)) / total) if total else 0.0,
                "annual_interest_cost": money(interest_cost), "fx_exposure": fx_exposure, "hedge_target": t["hedge_target"], "min_liquidity": D(t["min_liquidity"]),
                "liquidity": cash, "liquidity_ok": cash >= D(t["min_liquidity"]), "flows": t.get("flows", [])[-24:], "next_flow_day": w.calendar.roll(date(today.year, today.month, 20)).isoformat(),
                "next_debt_events": sorted([{"debt_id": d["id"], "date": p.isoformat(), "kind": "MATURITY" if p >= date.fromisoformat(d["maturity"]) else "COUPON"}
                                            for d in t["debt"] if not d.get("retired") for (s, e, p) in self._periods(d) if p > today][:6], key=lambda x: x["date"])}

    # ------------------------------------------------------------------ handlers
    def _h_setup(self, ev: Event) -> None:
        p = ev.payload
        pf = self.w.portfolios[p["portfolio_id"]]
        pf.treasury = {"debt": [dict(d, paid=list(d.get("paid", []))) for d in p["debt"]], "receipts": {k: int(v) for k, v in p["receipts"].items()}, "base_payments": int(p["base_payments"]),
                       "base_receipts": int(p["base_receipts"]), "min_liquidity": int(p["min_liquidity"]), "hedge_target": float(p["hedge_target"]), "flows": []}
        w = self.w
        total = sum(D(d["principal"]) for d in p["debt"])
        w.post(pf.id, f"Treasury opening balance sheet: debt {total:,.0f} funded the operating assets (plant, working capital) carried at cost",
               [dr("1900", total, None, "operating assets at cost (never revalued)"), cr("2380", total, None, "long-term debt")], ev, {"kind": "TREASURY"})

    def _h_flow(self, ev: Event) -> None:
        w = self.w
        p = ev.payload
        pf = w.portfolios[p["portfolio_id"]]
        t = pf.treasury
        t["last_flow_month"] = p["month"]
        lines = []
        net_base = ZERO
        for leg in p["legs"]:
            ccy, amt = leg["currency"], D(leg["amount"])
            ca = pf.cash_account(ccy)
            base = amt if ccy == pf.base_currency else w.fx.to_base(ccy, amt)
            ca.balance += amt
            ca.base_value += base
            net_base += base
            w.record_cash_movement(pf, ccy, amt, "OPERATING", f"Operating {'receipts' if amt > 0 else 'payments'} {p['month']}", ev)
            lines.append(dr(f"1010:{ccy}", base, None, f"operating flow {ccy} {amt:+,.0f}"))
        lines.append(cr("4980", net_base, None, "operating result"))
        t["flows"] = t.get("flows", []) + [{"date": ev.sim_date, "kind": "OPERATING", "legs": [{"currency": l["currency"], "amount": D(l["amount"])} for l in p["legs"]], "base": net_base}]
        w.post(pf.id, f"Operating flows {p['month']}: net {net_base:+,.2f} base", lines, ev, {"kind": "OPERATING"})

    def _h_debt(self, ev: Event) -> None:
        w = self.w
        p = ev.payload
        pf = w.portfolios[p["portfolio_id"]]
        t = pf.treasury
        d = next(x for x in t["debt"] if x["id"] == p["debt_id"])
        d["paid"].append(p["pay_date"])
        interest, principal = D(p["interest"]), D(p["principal"])
        ca = pf.cash_account(p["currency"])
        ca.balance -= interest + principal
        ca.base_value -= interest + principal
        lines = [dr("5950", interest, None, f"interest on {d['id']} at {float(p['rate']):.3%}"), cr(f"1010:{p['currency']}", interest + principal, None, "debt service paid")]
        if principal:
            lines.append(dr("2380", principal, None, f"{d['id']} repaid at maturity"))
            d["retired"] = True
        w.record_cash_movement(pf, p["currency"], -(interest + principal), "DEBT_SERVICE", f"{d['id']}: interest {interest:,.0f}{' + principal ' + format(principal, ',.0f') if principal else ''}", ev)
        t["flows"] = t.get("flows", []) + [{"date": ev.sim_date, "kind": "DEBT_SERVICE", "legs": [{"currency": p["currency"], "amount": -(interest + principal)}], "base": -(interest + principal), "debt_id": d["id"]}]
        w.post(pf.id, f"Debt service {d['id']}: interest {interest:,.2f}" + (f", principal {principal:,.2f}" if principal else ""), lines, ev, {"kind": "DEBT_SERVICE"})
