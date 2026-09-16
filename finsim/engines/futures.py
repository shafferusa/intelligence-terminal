"""Futures: daily settlement, margin, expiry.

Every day, each futures position is marked to the settlement price and the
difference is paid or received in cash — variation margin — and booked to
income (4400). Initial margin is held at the clearing member: after variation
margin the required amount (a percentage of notional, raised in stressed
regimes) is swept between cash and the margin account. If cash goes negative
the clearing member issues a margin call; after three days unresolved it
liquidates futures positions until the call is covered.

Physically deliverable contracts are auto-closed on their last trade date —
a portfolio manager never takes delivery; roll manually to keep exposure.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Dict, List, Optional

from ..domain.events import E, Event
from ..domain.models import MarginCall, Portfolio, Security
from ..engines.ledger import dr, cr
from ..money import D, money, qty as qqty, ZERO

MARGIN_CALL_GRACE_DAYS = 3
REGIME_MARGIN_MULT = {"NORMAL_GROWTH": 1.0, "RATE_CUTTING": 1.0, "RATE_HIKING": 1.15, "RECESSION": 1.4, "LIQUIDITY_STRESS": 2.0}


class FuturesEngine:
    def __init__(self, world):
        self.w = world

    def register(self) -> None:
        w = self.w
        w.on(E.FUTURES_SETTLED, lambda world, ev: self._h_settled(ev))
        w.on(E.MARGIN_SWEPT, lambda world, ev: self._h_swept(ev))
        w.on(E.MARGIN_CALL, lambda world, ev: self._h_margin_call(ev))
        w.on(E.FORCED_LIQUIDATION, lambda world, ev: None)
        w.on(E.CONTRACT_EXPIRED, lambda world, ev: None)

    # ------------------------------------------------------------------ margin parameters
    def margin_multiplier(self) -> float:
        return REGIME_MARGIN_MULT.get(self.w.market.state.regime if self.w.market.state else "NORMAL_GROWTH", 1.0)

    def initial_margin_per_contract(self, sec: Security) -> Decimal:
        bar = self.w.market.last_bar(sec.id)
        return money(bar.close * D(str(sec.multiplier)) * D(str(sec.margin_pct)) * D(str(self.margin_multiplier())))

    def required_margin(self, pf: Portfolio) -> Decimal:
        """Outright initial margin, less the calendar-spread offset: a long and a short contract of the same commodity are
        margined together at SPREAD_MARGIN_RATE of one outright (the clearing house recognises the paired risk)."""
        from .commodity_desk import SPREAD_MARGIN_RATE
        total = ZERO
        by_code: Dict[str, Dict] = {}
        for pos in pf.positions.values():
            if pos.is_future and pos.quantity != 0 and not self.w.securities[pos.security_id].expired:
                sec = self.w.securities[pos.security_id]
                im = self.initial_margin_per_contract(sec)
                total += im * abs(pos.quantity)
                if (sec.underlying_class or "").startswith("COMMODITY"):
                    g = by_code.setdefault(sec.underlying, {"long": ZERO, "short": ZERO, "im": None})
                    g["long" if pos.quantity > 0 else "short"] += abs(pos.quantity)
                    g["im"] = im if g["im"] is None else min(g["im"], im)
        for g in by_code.values():
            paired = min(g["long"], g["short"])
            if paired > 0:      # a pair contributed 2 outrights above; it is margined at SPREAD_MARGIN_RATE of one
                total -= money(paired * g["im"] * D(str(2 - SPREAD_MARGIN_RATE)))
        return max(ZERO, money(total))

    def total_required(self, pf: Portfolio) -> Decimal:
        """Everything the clearing member holds: futures initial margin plus listed-options margin."""
        return self.required_margin(pf) + pf.options_margin

    # ------------------------------------------------------------------ daily processing
    def expire_contracts(self, cause: Event) -> None:
        w = self.w
        today = w.current_date.isoformat()
        for pf in w.portfolios.values():
            for pos in list(pf.positions.values()):
                sec = w.securities[pos.security_id]
                if pos.is_future and pos.quantity != 0 and sec.expiry == today:
                    if pf.physical_delivery and w.cdesk.is_physical_commodity(sec):
                        w.cdesk.deliver(pf, sec, pos, cause)
                        continue
                    ev = w.emit(E.CONTRACT_EXPIRED, {"portfolio_id": pf.id, "security_id": sec.id, "quantity": pos.quantity,
                                                     "note": "last trade date: position auto-closed at settlement to avoid delivery"},
                                cause_id=cause.id, portfolio_id=pf.id)
                    side = "SELL" if pos.quantity > 0 else "BUY"
                    w.trading.system_order(pf, sec, side, abs(pos.quantity), ev, f"auto-close at expiry of {sec.id}", forced=False)

    def daily_settlement(self, cause: Event) -> None:
        w = self.w
        for pf in w.portfolios.values():
            for pos in list(pf.positions.values()):
                if not pos.is_future:
                    continue
                sec = w.securities[pos.security_id]
                if pos.quantity == 0 and not pos.day_fills:
                    continue
                settle = w.market.last_bar(sec.id).close
                mult = D(str(sec.multiplier))
                fills_qty = sum((D(f["quantity"]) for f in pos.day_fills), ZERO)
                carried = pos.quantity - fills_qty
                vm = money(carried * (settle - pos.settlement_price) * mult)
                for f in pos.day_fills:
                    vm += money(D(f["quantity"]) * (settle - D(f["price"])) * mult)
                w.emit(E.FUTURES_SETTLED, {"portfolio_id": pf.id, "security_id": sec.id, "settlement_price": settle, "previous_settlement": pos.settlement_price,
                                           "carried_contracts": carried, "fills": pos.day_fills, "variation_margin": vm, "currency": sec.currency,
                                           "contracts_after": pos.quantity, "notional": money(abs(pos.quantity) * settle * mult)},
                       cause_id=cause.id, portfolio_id=pf.id)

    def sweep_margin(self, cause: Event) -> None:
        w = self.w
        for pf in w.portfolios.values():
            required = self.total_required(pf)
            held = w.ledgers[pf.id].balance("1300")
            delta = required - held
            if delta != 0:
                w.emit(E.MARGIN_SWEPT, {"portfolio_id": pf.id, "required": required, "held_before": held, "amount": delta, "currency": pf.base_currency,
                                        "margin_multiplier": self.margin_multiplier(), "futures_margin": self.required_margin(pf), "options_margin": pf.options_margin},
                       cause_id=cause.id, portfolio_id=pf.id)

    def _check_margin_call(self, pf: Portfolio, cause: Event) -> None:
        w = self.w
        cash = pf.cash_account(pf.base_currency).balance
        open_call = next((m for m in pf.margin_calls if m.status == "OPEN"), None)
        if cash < 0 and self.total_required(pf) > 0:
            shortfall = -cash
            if open_call is None:
                w.emit(E.MARGIN_CALL, {"portfolio_id": pf.id, "call_id": f"MC-{w.next_seq():06d}", "amount": shortfall, "status": "OPEN", "days_open": 1,
                                       "reason": "cash overdrawn after variation and initial margin; deposit cash or reduce positions"},
                       cause_id=cause.id, portfolio_id=pf.id)
            else:
                days = open_call.days_open + 1
                if days > MARGIN_CALL_GRACE_DAYS:
                    self._force_liquidate(pf, open_call, cause)
                else:
                    w.emit(E.MARGIN_CALL, {"portfolio_id": pf.id, "call_id": open_call.id, "amount": shortfall, "status": "OPEN", "days_open": days,
                                           "reason": f"margin call outstanding {days} day(s); forced liquidation after {MARGIN_CALL_GRACE_DAYS}"},
                           cause_id=cause.id, portfolio_id=pf.id)
        elif open_call is not None:
            w.emit(E.MARGIN_CALL, {"portfolio_id": pf.id, "call_id": open_call.id, "amount": ZERO, "status": "MET", "days_open": open_call.days_open,
                                   "reason": "call met: cash restored"}, cause_id=cause.id, portfolio_id=pf.id)

    def _force_liquidate(self, pf: Portfolio, call: MarginCall, cause: Event) -> None:
        w = self.w
        ev = w.emit(E.FORCED_LIQUIDATION, {"portfolio_id": pf.id, "call_id": call.id, "cash_before": pf.cash_account(pf.base_currency).balance,
                                           "note": "clearing member liquidates futures positions to cover the unpaid margin call"},
                    cause_id=cause.id, portfolio_id=pf.id)
        futs = sorted([p for p in pf.positions.values() if p.is_future and p.quantity != 0],
                      key=lambda p: -abs(float(p.quantity) * float(w.market.last_bar(p.security_id).close) * w.securities[p.security_id].multiplier))
        closed = []
        for pos in futs:
            sec = w.securities[pos.security_id]
            side = "SELL" if pos.quantity > 0 else "BUY"
            t = w.trading.system_order(pf, sec, side, abs(pos.quantity), ev, f"forced liquidation for margin call {call.id}", forced=True)
            closed.append(sec.id)
            # settle the closing fill immediately so cash reflects it
            self._settle_position_now(pf, pos, ev)
            if pf.cash_account(pf.base_currency).balance + w.ledgers[pf.id].balance("1300") - self.total_required(pf) >= 0:
                break
        # then short option positions (buy to close at the ask), largest margin first
        if pf.cash_account(pf.base_currency).balance + w.ledgers[pf.id].balance("1300") - self.total_required(pf) < 0:
            shorts = sorted([p for p in pf.positions.values() if p.is_option and p.quantity < 0], key=lambda p: -p.margin_requirement)
            for pos in shorts:
                sec = w.securities[pos.security_id]
                w.trading.system_order(pf, sec, "BUY", abs(pos.quantity), ev, f"forced liquidation for margin call {call.id}", forced=True)
                closed.append(sec.id)
                w.options.recompute_margin(pf, ev)
                if pf.cash_account(pf.base_currency).balance + w.ledgers[pf.id].balance("1300") - self.total_required(pf) >= 0:
                    break
        # release margin no longer required
        required = self.total_required(pf)
        held = w.ledgers[pf.id].balance("1300")
        if required != held:
            w.emit(E.MARGIN_SWEPT, {"portfolio_id": pf.id, "required": required, "held_before": held, "amount": required - held, "currency": pf.base_currency,
                                    "margin_multiplier": self.margin_multiplier()}, cause_id=ev.id, portfolio_id=pf.id)
        w.emit(E.MARGIN_CALL, {"portfolio_id": pf.id, "call_id": call.id, "amount": ZERO, "status": "FORCED", "days_open": call.days_open,
                               "reason": f"forced liquidation of {', '.join(closed)}"}, cause_id=ev.id, portfolio_id=pf.id)

    def _settle_position_now(self, pf: Portfolio, pos, cause: Event) -> None:
        w = self.w
        sec = w.securities[pos.security_id]
        settle = w.market.last_bar(sec.id).close
        mult = D(str(sec.multiplier))
        fills_qty = sum((D(f["quantity"]) for f in pos.day_fills), ZERO)
        carried = pos.quantity - fills_qty
        vm = money(carried * (settle - pos.settlement_price) * mult)
        for f in pos.day_fills:
            vm += money(D(f["quantity"]) * (settle - D(f["price"])) * mult)
        w.emit(E.FUTURES_SETTLED, {"portfolio_id": pf.id, "security_id": sec.id, "settlement_price": settle, "previous_settlement": pos.settlement_price,
                                   "carried_contracts": carried, "fills": pos.day_fills, "variation_margin": vm, "currency": sec.currency,
                                   "contracts_after": pos.quantity, "notional": money(abs(pos.quantity) * settle * mult)}, cause_id=cause.id, portfolio_id=pf.id)

    # ------------------------------------------------------------------ handlers
    def _h_settled(self, ev: Event) -> None:
        w = self.w
        p = ev.payload
        pf = w.portfolios[p["portfolio_id"]]
        pos = pf.position(p["security_id"])
        pos.is_future = True
        vm = D(p["variation_margin"])
        pos.settlement_price = D(p["settlement_price"])
        pos.mark = pos.settlement_price
        pos.day_fills = []
        pos.variation_margin_total += vm
        pos.day_variation_margin += vm
        pos.realized_pnl += vm
        pos.notional = D(p["notional"])
        pos.market_value = ZERO
        ccy = p["currency"]
        pf.cash_account(ccy).balance += vm
        if vm != 0:
            w.record_cash_movement(pf, ccy, vm, "VARIATION_MARGIN", f"Daily settlement {p['security_id']} @ {pos.settlement_price}", ev)
            lines = [dr(f"1010:{ccy}", vm, p["security_id"], "variation margin received"), cr("4400", vm, p["security_id"], "futures gain")] if vm > 0 else \
                    [dr("4400", -vm, p["security_id"], "futures loss"), cr(f"1010:{ccy}", -vm, p["security_id"], "variation margin paid")]
            w.post(pf.id, f"Variation margin {p['security_id']}: {vm:,.2f} (settle {pos.settlement_price})", lines, ev, {"security_id": p["security_id"], "kind": "VM"})

    def _h_swept(self, ev: Event) -> None:
        w = self.w
        p = ev.payload
        pf = w.portfolios[p["portfolio_id"]]
        amt = D(p["amount"])
        ccy = p["currency"]
        pf.cash_account(ccy).balance -= amt
        for pos in pf.positions.values():
            if pos.is_future and pos.quantity != 0:
                pos.initial_margin = self.initial_margin_per_contract(w.securities[pos.security_id]) * abs(pos.quantity)
            elif pos.is_future:
                pos.initial_margin = ZERO
        w.record_cash_movement(pf, ccy, -amt, "INITIAL_MARGIN", f"Initial margin {'posted to' if amt > 0 else 'returned from'} clearing member (required {D(p['required']):,.0f})", ev)
        lines = [dr("1300", amt, None, "initial margin posted"), cr(f"1010:{ccy}", amt, None, "cash to clearing member")] if amt > 0 else \
                [dr(f"1010:{ccy}", -amt, None, "cash returned"), cr("1300", -amt, None, "initial margin released")]
        w.post(pf.id, f"Initial margin sweep {amt:+,.2f} (required {D(p['required']):,.2f}, multiplier {p['margin_multiplier']}x)", lines, ev, {"kind": "IM"})

    def _h_margin_call(self, ev: Event) -> None:
        p = ev.payload
        pf = self.w.portfolios[p["portfolio_id"]]
        mc = next((m for m in pf.margin_calls if m.id == p["call_id"]), None)
        if mc is None:
            mc = MarginCall(id=p["call_id"], portfolio_id=pf.id, date=ev.sim_date, amount=D(p["amount"]), reason=p["reason"], status=p["status"], days_open=int(p["days_open"]))
            pf.margin_calls.append(mc)
        else:
            mc.amount, mc.reason, mc.status, mc.days_open = D(p["amount"]), p["reason"], p["status"], int(p["days_open"])
            if p["status"] in ("MET", "FORCED"):
                mc.resolved_date = ev.sim_date
