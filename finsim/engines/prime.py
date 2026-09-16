"""Prime brokerage financing.

Cash never simply goes negative in the base currency: a shortfall becomes an
explicit margin loan from the prime broker, at policy + 100bp, secured by the
portfolio. Each day the broker values eligible collateral at financing
haircuts (unencumbered longs only — assets pledged to repo or lenders cannot
support the loan), adds the short-sale margin requirement, and computes excess
liquidity. A negative excess is a margin call, due next cycle; unresolved for a
second cycle, the broker liquidates: futures first, then unencumbered longs,
through ordinary forced trades that settle, hit the ledger and the P&L.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Dict, List, Optional

from ..domain.events import E, Event
from ..domain.models import Portfolio
from ..engines.ledger import dr, cr
from ..money import D, money, ZERO

PB_SPREAD = 0.010
SHORT_MARGIN = 0.30
CALL_GRACE_CYCLES = 1
PRIME_BROKER = "Harbor Prime Brokerage"


class PrimeEngine:
    def __init__(self, world):
        self.w = world

    def register(self) -> None:
        w = self.w
        w.on(E.MARGIN_LOAN_CHANGED, lambda world, ev: self._h_loan(ev))
        w.on(E.MARGIN_INTEREST_ACCRUED, lambda world, ev: self._h_accrued(ev))
        w.on(E.MARGIN_INTEREST_SETTLED, lambda world, ev: self._h_settled(ev))

    def rate(self) -> float:
        return round(self.w.market.curve().policy_rate + PB_SPREAD, 5)

    # ------------------------------------------------------------------ financing view
    def financing(self, pf: Portfolio) -> Dict:
        w = self.w
        rows = []
        cv = ZERO
        for pos in pf.positions.values():
            if pos.is_future or pos.quantity <= 0:
                continue
            sec = w.securities[pos.security_id]
            if sec.is_option:
                continue
            avail = w.collateral.available_quantity(pf, sec.id, include_covered=True)
            h = w.collateral.haircut(sec, "PRIME")
            mv = w.collateral.market_value(sec, max(ZERO, avail))
            v = money(mv * D(str(1 - h))) if h is not None and avail > 0 else ZERO
            cv += v
            rows.append({"security_id": sec.id, "available": avail, "market_value": mv, "haircut": h, "collateral_value": v, "eligible": h is not None})
        cash = pf.cash_account(pf.base_currency).balance
        cv += max(ZERO, cash)
        # short securities need PB margin; written options are margined at the options clearing member (1300), not here
        short_mv = sum((-pos.market_value for pos in pf.positions.values() if pos.quantity < 0 and not pos.is_future and not pos.is_option), ZERO)
        short_req = money(short_mv * D(str(SHORT_MARGIN)))
        requirement = pf.margin_loan + short_req
        excess = cv - requirement
        return {"broker": PRIME_BROKER, "loan_balance": pf.margin_loan, "rate": self.rate(), "accrued_interest": pf.margin_interest_accrued,
                "collateral_value": cv, "cash_component": max(ZERO, cash), "short_market_value": short_mv, "short_requirement": short_req,
                "requirement": requirement, "excess_liquidity": excess, "rows": rows, "call": w.collateral.open_call(pf, "PRIME", "PRIME")}

    def affordable(self, pf: Portfolio, cost: Decimal, haircut) -> Optional[str]:
        """Can the book pay `cost`? Settled/projected cash first; otherwise the prime broker finances it if excess
        liquidity survives the purchase (a purchase of X at haircut h consumes X*h of excess; cash collateral or
        ineligible assets consume the full amount). Returns a rejection reason or None."""
        cash = self.w.trading.projected_cash(pf, pf.base_currency)
        if cost <= cash:
            return None
        fin = self.financing(pf)
        h = D(str(haircut)) if haircut is not None else D(1)
        consumed = money(cost * h)
        if fin["excess_liquidity"] - consumed >= 0 and cost <= cash + fin["excess_liquidity"]:
            return None
        return (f"needs ~{cost:,.0f}: projected cash {cash:,.0f} and prime-broker excess liquidity {fin['excess_liquidity']:,.0f} "
                f"(financing this would consume {consumed:,.0f} of excess)")

    # ------------------------------------------------------------------ commands
    def draw(self, pf: Portfolio, amount, cause: Optional[Event] = None, note: str = "drawn by player") -> None:
        from ..world import CommandError
        amt = money(amount)
        if amt <= 0:
            raise CommandError("amount must be positive")
        fin = self.financing(pf)
        if cause is None and amt > fin["excess_liquidity"]:
            raise CommandError(f"draw of {amt:,.0f} exceeds excess liquidity {fin['excess_liquidity']:,.0f} at the prime broker")
        self.w.emit(E.MARGIN_LOAN_CHANGED, {"portfolio_id": pf.id, "amount": amt, "note": note, "currency": pf.base_currency, "rate": self.rate()},
                    cause_id=cause.id if cause else None, portfolio_id=pf.id)

    def repay(self, pf: Portfolio, amount, cause: Optional[Event] = None, note: str = "repaid by player") -> None:
        from ..world import CommandError
        amt = money(amount)
        if amt <= 0 or amt > pf.margin_loan:
            raise CommandError(f"repayment must be between 0 and the loan balance {pf.margin_loan:,.2f}")
        if cause is None and pf.cash_account(pf.base_currency).balance < amt:
            raise CommandError("insufficient settled cash to repay")
        self.w.emit(E.MARGIN_LOAN_CHANGED, {"portfolio_id": pf.id, "amount": -amt, "note": note, "currency": pf.base_currency, "rate": self.rate()},
                    cause_id=cause.id if cause else None, portfolio_id=pf.id)

    def fund_settlement(self, pf: Portfolio, shortfall: Decimal, reference: str, cause: Event) -> bool:
        """Draw on the margin loan to fund a purchase settlement, if excess liquidity allows. The purchased
        securities become financing collateral once they arrive, so the test is against excess less the
        cash consumed."""
        fin = self.financing(pf)
        if fin["excess_liquidity"] - shortfall < 0:
            return False
        self.draw(pf, shortfall, cause, note=f"settlement funding for {reference}")
        return True

    # ------------------------------------------------------------------ daily
    def process_day(self, cause: Event, prev_date: date) -> None:
        w = self.w
        today = w.current_date
        days = max(1, (today - prev_date).days)
        for pf in w.portfolios.values():
            ccy = pf.base_currency
            # interest on the loan
            if pf.margin_loan > 0:
                i = money(pf.margin_loan * D(str(self.rate())) * days / 360)
                if i:
                    w.emit(E.MARGIN_INTEREST_ACCRUED, {"portfolio_id": pf.id, "interest": i, "days": days, "rate": self.rate(), "currency": ccy}, cause_id=cause.id, portfolio_id=pf.id)
            if today.month != prev_date.month and pf.margin_interest_accrued:
                w.emit(E.MARGIN_INTEREST_SETTLED, {"portfolio_id": pf.id, "interest": pf.margin_interest_accrued, "currency": ccy}, cause_id=cause.id, portfolio_id=pf.id)
            # sweep: negative base cash becomes a loan; cash not committed to pending settlements repays it
            cash = pf.cash_account(ccy).balance
            if cash < 0:
                self.draw(pf, -cash, cause, note="automatic draw: settled cash overdrawn")
            elif cash > 0 and pf.margin_loan > 0:
                committed = sum((si.cash_amount for si in pf.settlements.values() if si.instruction_type == "RVP" and si.currency == ccy
                                 and si.status in ("PENDING", "MATCHED", "FAILED")), ZERO)
                free = cash - committed
                if free > 0:
                    self.repay(pf, min(free, pf.margin_loan), cause, note="automatic repayment from uncommitted settled cash")
            # margin requirement
            fin = self.financing(pf)
            call = fin["call"]
            if fin["excess_liquidity"] < 0:
                if call is not None and call.cycles_open >= CALL_GRACE_CYCLES:
                    self._force_liquidate(pf, call, cause)
                else:
                    w.collateral.issue_or_update_call(pf, "PRIME", "PRIME", -fin["excess_liquidity"],
                                                      f"excess liquidity {fin['excess_liquidity']:,.0f}: collateral value {fin['collateral_value']:,.0f} vs loan {pf.margin_loan:,.0f} + short requirement {fin['short_requirement']:,.0f}", cause)
            elif call is not None:
                w.collateral.resolve_call(pf, call, "MET", "excess liquidity restored", cause)

    def _force_liquidate(self, pf: Portfolio, call, cause: Event) -> None:
        w = self.w
        ev = w.emit(E.FORCED_LIQUIDATION, {"portfolio_id": pf.id, "call_id": call.id, "cash_before": pf.cash_account(pf.base_currency).balance,
                                           "note": "prime broker liquidates positions to cover the unmet margin call"}, cause_id=cause.id, portfolio_id=pf.id)
        shortfall = -self.financing(pf)["excess_liquidity"]
        closed = []
        # futures first (cash settles same day), then unencumbered longs by size
        futs = sorted([p for p in pf.positions.values() if p.is_future and p.quantity != 0], key=lambda p: -abs(float(p.notional)))
        for pos in futs:
            if shortfall <= 0:
                break
            sec = w.securities[pos.security_id]
            side = "SELL" if pos.quantity > 0 else "BUY"
            w.trading.system_order(pf, sec, side, abs(pos.quantity), ev, f"forced liquidation for margin call {call.id}", forced=True)
            w.futures._settle_position_now(pf, pos, ev)
            closed.append(sec.id)
            shortfall -= pos.initial_margin
        # then written options (buy to close at the ask); their clearing margin comes back to cash at once
        shorts = sorted([p for p in pf.positions.values() if p.is_option and p.quantity < 0], key=lambda p: -float(p.margin_requirement))
        for pos in shorts:
            if shortfall <= 0:
                break
            sec = w.securities[pos.security_id]
            held = w.ledgers[pf.id].balance("1300")
            t = w.trading.system_order(pf, sec, "BUY", abs(pos.quantity), ev, f"forced liquidation for margin call {call.id}", forced=True)
            closed.append(sec.id)
            w.options.recompute_margin(pf, ev)
            w.futures.sweep_margin(ev)
            released = held - w.ledgers[pf.id].balance("1300")
            shortfall -= released - (t.net_amount if t else ZERO)
        longs = sorted([p for p in pf.positions.values() if not p.is_future and not p.is_option and p.quantity > 0], key=lambda p: -float(p.market_value))
        for pos in longs:
            if shortfall <= 0:
                break
            sec = w.securities[pos.security_id]
            avail = min(pos.quantity, w.collateral.available_quantity(pf, sec.id) - sum((o.quantity - o.filled_quantity for o in pf.orders.values()
                                                                                          if o.security_id == sec.id and o.side == "SELL" and o.status in ("WORKING", "PARTIALLY_FILLED")), ZERO))
            if avail <= 0:
                continue
            h = w.collateral.haircut(sec, "PRIME") or 1.0
            px = w.market.last_bar(sec.id).close
            unit = px * (D(1) / 100 if sec.is_bond else D(1))
            # selling releases (1-h) of collateral value but brings 100% cash: liquidity gain per unit = h * unit
            need_units = shortfall / (unit * D(str(max(h, 0.05)))) if unit else avail
            q = min(avail, max(D(sec.lot_size), (need_units // sec.lot_size + 1) * sec.lot_size))
            t = w.trading.system_order(pf, sec, "SELL", q, ev, f"forced liquidation for margin call {call.id}", forced=True)
            closed.append(sec.id)
            if t:
                shortfall -= money(t.net_amount * D(str(h)))
        w.collateral.resolve_call(pf, call, "FORCED", f"forced liquidation of {', '.join(closed) if closed else 'nothing available'}", ev)

    # ------------------------------------------------------------------ handlers
    def _h_loan(self, ev: Event) -> None:
        w = self.w
        p = ev.payload
        pf = w.portfolios[p["portfolio_id"]]
        amt = D(p["amount"])
        ccy = p["currency"]
        ca = pf.cash_account(ccy)
        ca.balance += amt
        ca.base_value += amt
        pf.margin_loan += amt
        w.record_cash_movement(pf, ccy, amt, "MARGIN_LOAN", f"Prime broker loan {'draw' if amt > 0 else 'repayment'}: {p.get('note', '')}", ev)
        lines = [dr(f"1010:{ccy}", amt, None, "cash from prime broker"), cr("2700", amt, None, f"margin loan {PRIME_BROKER}")] if amt > 0 else \
                [dr("2700", -amt, None, "margin loan repaid"), cr(f"1010:{ccy}", -amt, None, "cash to prime broker")]
        w.post(pf.id, f"Margin loan {amt:+,.2f} ({p.get('note', '')}) balance {pf.margin_loan:,.2f}", lines, ev, {"kind": "MARGIN_LOAN"})

    def _h_accrued(self, ev: Event) -> None:
        w = self.w
        p = ev.payload
        pf = w.portfolios[p["portfolio_id"]]
        i = D(p["interest"])
        pf.margin_interest_accrued += i
        w.post(pf.id, f"Margin loan interest accrual {i:,.2f} ({p['days']}d @ {p['rate']*100:.2f}%)", [dr("5600", i, None, "margin interest"), cr("2340", i, None, "accrued margin interest")],
               ev, {"kind": "MARGIN_INTEREST"})

    def _h_settled(self, ev: Event) -> None:
        w = self.w
        p = ev.payload
        pf = w.portfolios[p["portfolio_id"]]
        i = D(p["interest"])
        ccy = p["currency"]
        ca = pf.cash_account(ccy)
        ca.balance -= i
        ca.base_value -= i
        pf.margin_interest_accrued -= i
        w.record_cash_movement(pf, ccy, -i, "MARGIN_INTEREST", "Monthly margin loan interest paid", ev)
        w.post(pf.id, f"Margin loan interest paid {i:,.2f}", [dr("2340", i, None, "accrued interest paid"), cr(f"1010:{ccy}", i, None, "cash")], ev, {"kind": "MARGIN_INTEREST"})
