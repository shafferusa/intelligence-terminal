"""Corporate actions: cash dividends, bond coupons, bond maturities.

Dividends: declared (news) -> ex-date entitlement (income recognised, receivable
booked; the market engine drops the price by the dividend that day) -> pay
date (cash arrives, receivable extinguished). Entitlement uses the trade-date
position held before the ex-date.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Dict

from ..domain.events import E, Event
from ..domain.models import CorporateAction, Portfolio
from ..engines.ledger import dr, cr
from ..engines.pricing import BondPricer
from ..money import D, money, ZERO


class CorporateActionEngine:
    def __init__(self, world):
        self.w = world

    def register(self) -> None:
        w = self.w
        w.on(E.DIVIDEND_DECLARED, lambda world, ev: self._h_declared(ev))
        w.on(E.DIVIDEND_ENTITLED, lambda world, ev: self._h_entitled(ev))
        w.on(E.DIVIDEND_PAID, lambda world, ev: self._h_paid(ev))
        w.on(E.COUPON_PAID, lambda world, ev: self._h_coupon(ev))
        w.on(E.DIVIDEND_OBLIGATION, lambda world, ev: self._h_obligation(ev))
        w.on(E.DIVIDEND_OBLIGATION_PAID, lambda world, ev: self._h_obligation_paid(ev))
        w.on(E.BOND_MATURED, lambda world, ev: self._h_matured(ev))
        w.on(E.BOND_DEFAULTED, lambda world, ev: self._h_defaulted(ev))

    # ------------------------------------------------------------------ scheduling
    def declare_upcoming(self, cause: Event) -> None:
        w = self.w
        today = w.current_date
        for sec in w.securities.values():
            if sec.dividend_per_share <= 0:
                continue
            for year in (today.year, today.year + 1):
                for ex in w.market.dividend_ex_dates(sec, year):
                    sched = w.market.dividend_schedule(sec, ex)
                    ca_id = f"DIV-{sec.id}-{ex.isoformat()}"
                    if ca_id in w.corporate_actions or ex < today or sched["declared"] > today:
                        continue
                    ev = w.emit(E.DIVIDEND_DECLARED, {"ca_id": ca_id, "security_id": sec.id, "amount": sec.dividend_per_share, "currency": sec.currency,
                                                      "declared_date": sched["declared"].isoformat(), "ex_date": ex.isoformat(),
                                                      "record_date": sched["record"].isoformat(), "pay_date": sched["pay"].isoformat()}, cause_id=cause.id)
                    w.emit(E.NEWS_PUBLISHED, {"headline": f"{sec.name} declares quarterly dividend of ${sec.dividend_per_share} per share",
                                              "body": f"{sec.name} ({sec.id}) declared a cash dividend of ${sec.dividend_per_share} per share, "
                                                      f"payable {sched['pay'].isoformat()} to holders of record {sched['record'].isoformat()}. "
                                                      f"Shares trade ex-dividend on {ex.isoformat()}.",
                                              "category": "CORPORATE_ACTION", "refs": [sec.id, ca_id]}, cause_id=ev.id)

    # ------------------------------------------------------------------ daily processing
    def process_day(self, cause: Event) -> None:
        w = self.w
        today = w.current_date.isoformat()
        for ca in list(w.corporate_actions.values()):
            if ca.action_type != "CASH_DIVIDEND":
                continue
            if ca.ex_date == today and ca.status == "DECLARED":
                self.entitle_today(ca, cause)
            if ca.pay_date == today and ca.status == "EX":
                for pid, ent in ca.entitlements.items():
                    if not ent.get("paid"):
                        if ent.get("obligation"):
                            w.emit(E.DIVIDEND_OBLIGATION_PAID, {"ca_id": ca.id, "portfolio_id": pid, "security_id": ca.security_id, "amount": ent["amount"],
                                                                "currency": ca.currency}, cause_id=cause.id, portfolio_id=pid)
                        else:
                            w.emit(E.DIVIDEND_PAID, {"ca_id": ca.id, "portfolio_id": pid, "security_id": ca.security_id, "amount": ent["amount"],
                                                     "currency": ca.currency}, cause_id=cause.id, portfolio_id=pid)
                ca.status = "PAID"
        # bonds
        for pf in w.portfolios.values():
            for pos in list(pf.positions.values()):
                sec = w.securities[pos.security_id]
                if not sec.is_bond or pos.quantity <= 0:
                    continue
                if sec.defaulted:
                    if sec.recovery_date == today:
                        w.emit(E.BOND_MATURED, {"portfolio_id": pf.id, "security_id": sec.id, "quantity": pos.quantity,
                                                "principal": money(pos.quantity * D(repr(sec.recovery_rate))), "currency": sec.currency, "recovery": True},
                               cause_id=cause.id, portfolio_id=pf.id)
                    continue
                cds = [d.isoformat() for d in BondPricer.coupon_dates(sec)]
                if today in cds:
                    cpn = money(pos.quantity * D(sec.coupon) / sec.freq)
                    w.emit(E.COUPON_PAID, {"portfolio_id": pf.id, "security_id": sec.id, "quantity": pos.quantity, "amount": cpn, "currency": sec.currency},
                           cause_id=cause.id, portfolio_id=pf.id)
                if sec.maturity == today:
                    w.emit(E.BOND_MATURED, {"portfolio_id": pf.id, "security_id": sec.id, "quantity": pos.quantity, "principal": money(pos.quantity),
                                            "currency": sec.currency}, cause_id=cause.id, portfolio_id=pf.id)

    def entitle_today(self, ca, cause: Event) -> None:
        """Ex-date processing for a cash dividend: holders are entitled, shorts owe a manufactured dividend."""
        w = self.w
        if True:
            if True:
                for pf in w.portfolios.values():
                    q = self._quantity_before_today(pf, ca.security_id)
                    if q > 0:
                        amt = money(q * ca.amount_per_unit)
                        w.emit(E.DIVIDEND_ENTITLED, {"ca_id": ca.id, "portfolio_id": pf.id, "security_id": ca.security_id, "quantity": q,
                                                     "amount": amt, "currency": ca.currency, "pay_date": ca.pay_date}, cause_id=cause.id, portfolio_id=pf.id)
                    elif q < 0:
                        amt = money(-q * ca.amount_per_unit)
                        w.emit(E.DIVIDEND_OBLIGATION, {"ca_id": ca.id, "portfolio_id": pf.id, "security_id": ca.security_id, "quantity": -q,
                                                       "amount": amt, "currency": ca.currency, "pay_date": ca.pay_date}, cause_id=cause.id, portfolio_id=pf.id)
                # mark EX even if nobody held it
                ca.status = "EX"


    def default_bond(self, reference: str, recovery: float, cause: Event) -> None:
        """Issuer default: the bond marks at recovery, accrued interest is written off, coupons stop, and the recovery
        is paid 30 business days later."""
        w = self.w
        sec = w.securities[reference]
        if sec.defaulted:
            return
        writeoffs = {pf.id: pf.positions[reference].accrued_interest for pf in w.portfolios.values()
                     if reference in pf.positions and pf.positions[reference].quantity > 0}
        w.emit(E.BOND_DEFAULTED, {"security_id": reference, "issuer": sec.issuer, "recovery": recovery, "redemption_date": w.calendar.add_business_days(w.current_date, 30).isoformat(),
                                  "writeoffs": writeoffs}, cause_id=cause.id)

    def _h_defaulted(self, ev: Event) -> None:
        w = self.w
        p = ev.payload
        sec = w.securities[p["security_id"]]
        sec.defaulted = True
        sec.recovery_rate = float(p["recovery"])
        sec.recovery_date = p["redemption_date"]
        sec.rating = "D"
        rp = D(repr(float(p["recovery"]) * 100)).quantize(D("0.0001"))
        h = w.market.history.get(sec.id)
        if h:
            b = h[-1]
            b.close, b.bid, b.ask = rp, rp, rp
            b.low = min(b.low, rp)
        w.market._prev_close[sec.id] = rp
        for pid, acc in p.get("writeoffs", {}).items():
            pf = w.portfolios[pid]
            pos = pf.position(sec.id)
            amt = D(acc)
            pos.accrued_interest = ZERO
            pos.interest_income -= amt
            if amt:
                w.post(pf.id, f"Default of {sec.issuer}: accrued interest {amt:,.2f} on {sec.id} written off",
                       [dr("4300", amt, sec.id, "accrued interest written off"), cr("1220", amt, sec.id, "accrued interest receivable reversed")], ev, {"security_id": sec.id, "kind": "DEFAULT"})
            w.pnl.mark_position(pf, sec.id, ev)

    def _quantity_before_today(self, pf: Portfolio, security_id: str) -> Decimal:
        pos = pf.positions.get(security_id)
        if not pos:
            return ZERO
        q = pos.quantity
        for tid in pf.day_trade_ids:
            t = pf.trades[tid]
            if t.security_id == security_id:
                q -= t.quantity if t.side == "BUY" else -t.quantity
        return q

    # ------------------------------------------------------------------ handlers
    def _h_declared(self, ev: Event) -> None:
        p = ev.payload
        self.w.corporate_actions[p["ca_id"]] = CorporateAction(id=p["ca_id"], security_id=p["security_id"], action_type="CASH_DIVIDEND",
                                                                declared_date=p["declared_date"], ex_date=p["ex_date"], record_date=p["record_date"],
                                                                pay_date=p["pay_date"], amount_per_unit=D(p["amount"]), currency=p["currency"], status="DECLARED")

    def _h_entitled(self, ev: Event) -> None:
        w = self.w
        p = ev.payload
        ca = w.corporate_actions[p["ca_id"]]
        pf = w.portfolios[p["portfolio_id"]]
        amt = D(p["amount"])
        ca.entitlements[pf.id] = {"quantity": D(p["quantity"]), "amount": amt, "paid": False}
        ca.status = "EX"
        pos = pf.position(p["security_id"])
        pos.dividend_income += amt
        w.post(pf.id, f"Dividend entitlement {p['security_id']}: {D(p['quantity']):,} x {ca.amount_per_unit} = {amt:,.2f}, payable {p['pay_date']}",
               [dr("1210", amt, p["security_id"], "dividend receivable"), cr("4200", amt, p["security_id"], "dividend income")], ev,
               {"ca_id": ca.id, "security_id": p["security_id"]})

    def _h_paid(self, ev: Event) -> None:
        w = self.w
        p = ev.payload
        ca = w.corporate_actions[p["ca_id"]]
        pf = w.portfolios[p["portfolio_id"]]
        amt = D(p["amount"])
        ca.entitlements[pf.id]["paid"] = True
        pf.cash_account(p["currency"]).balance += amt
        w.record_cash_movement(pf, p["currency"], amt, "DIVIDEND", f"Dividend {ca.id} on {p['security_id']}", ev)
        w.post(pf.id, f"Dividend paid {p['security_id']} {ca.id}: {amt:,.2f}",
               [dr(f"1010:{p['currency']}", amt, p["security_id"], "dividend cash received"), cr("1210", amt, p["security_id"], "receivable extinguished")], ev,
               {"ca_id": ca.id, "security_id": p["security_id"]})

    def _h_obligation(self, ev: Event) -> None:
        """Manufactured dividend: short over the ex-date owes the lender a payment in lieu."""
        w = self.w
        p = ev.payload
        ca = w.corporate_actions[p["ca_id"]]
        pf = w.portfolios[p["portfolio_id"]]
        amt = D(p["amount"])
        ca.entitlements[pf.id] = {"quantity": -D(p["quantity"]), "amount": amt, "paid": False, "obligation": True}
        ca.status = "EX"
        pos = pf.position(p["security_id"])
        pos.manufactured_dividends += amt
        pf.manufactured_payable += amt
        w.post(pf.id, f"Manufactured dividend {p['security_id']}: short {D(p['quantity']):,} x {ca.amount_per_unit} = {amt:,.2f} owed to lender, payable {p['pay_date']}",
               [dr("5400", amt, p["security_id"], "payment in lieu of dividend"), cr("2320", amt, p["security_id"], "manufactured dividend payable")], ev,
               {"ca_id": ca.id, "security_id": p["security_id"], "kind": "MANUFACTURED_DIVIDEND"})

    def _h_obligation_paid(self, ev: Event) -> None:
        w = self.w
        p = ev.payload
        ca = w.corporate_actions[p["ca_id"]]
        pf = w.portfolios[p["portfolio_id"]]
        amt = D(p["amount"])
        ca.entitlements[pf.id]["paid"] = True
        pf.manufactured_payable -= amt
        ca_ = pf.cash_account(p["currency"])
        ca_.balance -= amt
        ca_.base_value -= amt
        w.record_cash_movement(pf, p["currency"], -amt, "MANUFACTURED_DIVIDEND", f"Payment in lieu {ca.id} on {p['security_id']} to lender", ev)
        w.post(pf.id, f"Manufactured dividend paid {p['security_id']} {ca.id}: {amt:,.2f}",
               [dr("2320", amt, p["security_id"], "payable settled"), cr(f"1010:{p['currency']}", amt, p["security_id"], "cash paid to lender")], ev,
               {"ca_id": ca.id, "security_id": p["security_id"]})

    def _h_coupon(self, ev: Event) -> None:
        w = self.w
        p = ev.payload
        pf = w.portfolios[p["portfolio_id"]]
        pos = pf.position(p["security_id"])
        amt = D(p["amount"])
        # true-up accrual to the full coupon, then pay it
        delta = amt - pos.accrued_interest
        lines = []
        if delta != 0:
            pos.interest_income += delta
            lines.append(dr("1220", delta, p["security_id"], "final accrual to coupon date"))
            lines.append(cr("4300", delta, p["security_id"], "interest income"))
        pos.accrued_interest = ZERO
        pf.cash_account(p["currency"]).balance += amt
        w.record_cash_movement(pf, p["currency"], amt, "COUPON", f"Coupon on {p['security_id']}", ev)
        lines += [dr(f"1010:{p['currency']}", amt, p["security_id"], "coupon received"), cr("1220", amt, p["security_id"], "accrued interest collected")]
        w.post(pf.id, f"Coupon {p['security_id']}: {D(p['quantity']):,} face -> {amt:,.2f}", lines, ev, {"security_id": p["security_id"]})

    def _h_matured(self, ev: Event) -> None:
        w = self.w
        p = ev.payload
        pf = w.portfolios[p["portfolio_id"]]
        pos = pf.position(p["security_id"])
        principal = D(p["principal"])
        cost = pos.cost_basis
        realized = principal - cost
        pos.realized_pnl += realized
        pos.quantity = ZERO
        pos.settled_quantity = ZERO
        pos.cost_basis = ZERO
        pos.lots = []
        pos.market_value = ZERO
        adj = pos.valuation_adjustment
        pos.valuation_adjustment = ZERO
        pf.cash_account(p["currency"]).balance += principal
        w.record_cash_movement(pf, p["currency"], principal, "MATURITY", f"Principal redemption {p['security_id']}", ev)
        w.record_custody_movement(pf, p["security_id"], -D(p["quantity"]), "REDEMPTION", "matured", ev)
        lines = [dr(f"1010:{p['currency']}", principal, p["security_id"], "principal received"),
                 cr("1110", cost, p["security_id"], "cost basis removed"), cr("4000", realized, p["security_id"], "realized on redemption"),
                 cr("1150", adj, p["security_id"], "valuation adjustment reversed"), dr("4100", adj, p["security_id"], "unrealized reversed")]
        w.post(pf.id, f"Maturity {p['security_id']}: principal {principal:,.2f}", lines, ev, {"security_id": p["security_id"]})
