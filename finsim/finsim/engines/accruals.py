"""Accruals: bond coupon interest and interest on cash balances.

Bond accrued interest is trued-up daily to Actual/Actual accrual. Cash earns
(policy rate - 25bp) on positive balances and pays (policy rate + 150bp) on
overdrafts, Actual/360, accrued daily and settled on the first business day of
each month — so financing costs are visible as accruals before they hit cash.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from ..domain.events import E, Event
from ..engines.ledger import dr, cr
from ..engines.pricing import BondPricer
from ..money import D, money, ZERO

DEPOSIT_SPREAD = -0.0025
OVERDRAFT_SPREAD = 0.015


class AccrualEngine:
    def __init__(self, world):
        self.w = world

    def register(self) -> None:
        self.w.on(E.INTEREST_ACCRUED, lambda world, ev: self._h_accrued(ev))
        self.w.on(E.INTEREST_SETTLED, lambda world, ev: self._h_settled(ev))

    def process_day(self, cause: Event, prev_date: date) -> None:
        w = self.w
        today = w.current_date
        days = (today - prev_date).days
        policy = w.market.curve().policy_rate
        for pf in w.portfolios.values():
            # bonds
            for pos in pf.positions.values():
                sec = w.securities[pos.security_id]
                if not sec.is_bond or pos.quantity <= 0 or sec.defaulted:
                    continue
                target = money(pos.quantity * BondPricer.accrued_per_100(sec, today) / 100 * w.fx.k(sec.currency))
                delta = target - pos.accrued_interest
                if delta != 0:
                    w.emit(E.INTEREST_ACCRUED, {"portfolio_id": pf.id, "kind": "BOND", "security_id": sec.id, "amount": delta, "currency": sec.currency,
                                                "accrued_balance": target}, cause_id=cause.id, portfolio_id=pf.id)
            # cash (a portfolio created today earns for today only, not the weekend before it existed)
            pf_days = 1 if pf.created == today.isoformat() else days
            if pf_days <= 0:
                continue
            for ca in pf.cash.values():
                if ca.balance == 0:
                    continue
                if ca.currency == pf.base_currency:
                    if ca.balance < 0:
                        continue          # base-currency shortfalls are prime-broker loans, charged there
                    rate = policy + DEPOSIT_SPREAD
                    amt = money(ca.balance * D(rate) * pf_days / 360)
                    if amt != 0:
                        w.emit(E.INTEREST_ACCRUED, {"portfolio_id": pf.id, "kind": "CASH", "currency": ca.currency, "amount": amt, "rate": rate, "days": pf_days,
                                                    "balance": ca.balance}, cause_id=cause.id, portfolio_id=pf.id)
                else:
                    frate = w.market.fx.rate.get(ca.currency, policy)
                    rate = frate + (DEPOSIT_SPREAD if ca.balance > 0 else OVERDRAFT_SPREAD)
                    amt = money(ca.balance * D(rate) * pf_days / 360)
                    if amt != 0:
                        w.emit(E.INTEREST_ACCRUED, {"portfolio_id": pf.id, "kind": "FX_CASH", "currency": ca.currency, "amount": amt, "rate": rate, "days": pf_days,
                                                    "balance": ca.balance, "base_amount": w.fx.to_base(ca.currency, amt)}, cause_id=cause.id, portfolio_id=pf.id)
            if today.month != prev_date.month:
                for ca in pf.cash.values():
                    if ca.accrued_interest != 0:
                        w.emit(E.INTEREST_SETTLED, {"portfolio_id": pf.id, "currency": ca.currency, "amount": ca.accrued_interest},
                               cause_id=cause.id, portfolio_id=pf.id)

    def _h_accrued(self, ev: Event) -> None:
        w = self.w
        p = ev.payload
        pf = w.portfolios[p["portfolio_id"]]
        amt = D(p["amount"])
        if p["kind"] == "BOND":
            pos = pf.position(p["security_id"])
            pos.accrued_interest += amt
            pos.interest_income += amt
            w.post(pf.id, f"Accrued interest {p['security_id']}: {amt:,.2f}",
                   [dr("1220", amt, p["security_id"], "accrued interest"), cr("4300", amt, p["security_id"], "interest income")], ev,
                   {"security_id": p["security_id"], "kind": "BOND"})
        elif p["kind"] == "FX_CASH":
            ca = pf.cash_account(p["currency"])
            base = D(p["base_amount"])
            ca.balance += amt
            ca.base_value += base
            w.record_cash_movement(pf, p["currency"], amt, "INTEREST", f"{p['currency']} cash interest @ {p['rate']*100:.3f}% (paid daily)", ev)
            if base > 0:
                lines = [dr(f"1010:{p['currency']}", base, None, "interest received"), cr("4300", base, None, f"{p['currency']} deposit interest")]
            else:
                lines = [dr("5100", -base, None, f"{p['currency']} overdraft interest"), cr(f"1010:{p['currency']}", -base, None, "interest paid")]
            w.post(pf.id, f"{p['currency']} cash interest {amt:,.2f} ({p['days']}d)", lines, ev, {"kind": "FX_CASH", "currency": p["currency"]})
        else:
            ca = pf.cash_account(p["currency"])
            ca.accrued_interest += amt
            if amt > 0:
                lines = [dr("1230", amt, None, "interest receivable on cash"), cr("4300", amt, None, f"cash interest @ {p['rate']*100:.3f}%")]
            else:
                lines = [dr("5100", -amt, None, f"overdraft interest @ {p['rate']*100:.3f}%"), cr("2300", -amt, None, "interest payable on overdraft")]
            w.post(pf.id, f"Cash interest accrual {p['currency']} {amt:,.2f} ({p['days']}d)", lines, ev, {"kind": "CASH", "currency": p["currency"]})

    def _h_settled(self, ev: Event) -> None:
        w = self.w
        p = ev.payload
        pf = w.portfolios[p["portfolio_id"]]
        ca = pf.cash_account(p["currency"])
        amt = D(p["amount"])
        ca.accrued_interest -= amt
        ca.balance += amt
        w.record_cash_movement(pf, p["currency"], amt, "INTEREST", "Monthly cash interest settlement", ev)
        if amt > 0:
            lines = [dr(f"1010:{p['currency']}", amt, None, "interest received"), cr("1230", amt, None, "receivable cleared")]
        else:
            lines = [dr("2300", -amt, None, "payable cleared"), cr(f"1010:{p['currency']}", -amt, None, "interest paid")]
        w.post(pf.id, f"Cash interest settled {p['currency']} {amt:,.2f}", lines, ev, {"kind": "CASH", "currency": p["currency"]})
