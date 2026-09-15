"""Trade engine: orders, execution, positions, lots.

Execution is not "fill at last price". A market order crosses the spread and
pays square-root market impact scaled by the day's volatility and the order's
share of volume; large orders are capped at a participation rate and the
remainder keeps working (partial fills). Limit orders fill only when the
market reaches them; stops trigger and become market/limit orders.

Applying TRADE_EXECUTED updates the trade-date position and lots (FIFO relief
on sells, producing realized P&L), then derives: LEDGER_POSTED,
SETTLEMENT_INSTRUCTION_CREATED, VALUATION_MARKED.
"""
from __future__ import annotations

import math
import random
from datetime import date
from decimal import Decimal
from typing import Dict, List, Optional, Tuple

from ..calendar import settlement_date
from ..domain.events import E, Event
from ..domain.models import Bar, Lot, Order, Portfolio, Security, Trade
from ..engines.ledger import dr, cr
from ..engines.pricing import BondPricer
from ..money import D, money, price as qprice, qty as qqty, ZERO

PARTICIPATION_CAP = 0.20         # max share of a session's volume one order may take
IMPACT_K = 0.6                   # impact = K * sigma_daily * sqrt(Q/ADV)
EQUITY_COMMISSION_PER_SHARE = D("0.005")
EQUITY_COMMISSION_MIN = D("1.00")
BOND_COMMISSION_BPS = D("0.5")   # of face


class TradingEngine:
    def __init__(self, world):
        self.w = world

    def register(self) -> None:
        w = self.w
        w.on(E.ORDER_ENTERED, lambda world, ev: self._h_order_entered(ev))
        w.on(E.ORDER_REJECTED, lambda world, ev: self._h_order_rejected(ev))
        w.on(E.ORDER_CANCELLED, lambda world, ev: self._h_order_cancelled(ev))
        w.on(E.ORDER_STATUS_CHANGED, lambda world, ev: self._h_order_status(ev))
        w.on(E.TRADE_EXECUTED, lambda world, ev: self._h_trade_executed(ev))
        w.on(E.TRADE_STATUS_CHANGED, lambda world, ev: self._h_trade_status(ev))

    # ------------------------------------------------------------------ commands
    def enter_order(self, portfolio_id: str, security_id: str, side: str, quantity, order_type: str, limit_price, stop_price,
                    time_in_force: str, strategy_tag: Optional[str]) -> Order:
        from ..world import CommandError
        w = self.w
        pf = w.portfolio(portfolio_id)
        sec = w.security(security_id)
        side = side.upper()
        order_type = order_type.upper()
        tif = time_in_force.upper()
        q = qqty(quantity)
        base = {"portfolio_id": pf.id, "security_id": sec.id, "side": side, "order_type": order_type, "quantity": q,
                "limit_price": qprice(limit_price) if limit_price is not None else None,
                "stop_price": qprice(stop_price) if stop_price is not None else None, "time_in_force": tif, "strategy_tag": strategy_tag}
        reason = self._validate(pf, sec, side, order_type, q, base["limit_price"], base["stop_price"], tif)
        if reason:
            ev = w.emit(E.ORDER_REJECTED, {**base, "order_id": w.new_id("ORD"), "reason": reason}, portfolio_id=pf.id)
            raise CommandError(f"Order rejected: {reason}")
        oid = w.new_id("ORD")
        ev = w.emit(E.ORDER_ENTERED, {**base, "order_id": oid}, portfolio_id=pf.id)
        order = pf.orders[oid]
        self.try_execute(order, session="NOW", cause=ev)
        return order

    def cancel(self, portfolio_id: str, order_id: str) -> Order:
        from ..world import CommandError
        pf = self.w.portfolio(portfolio_id)
        if order_id not in pf.orders:
            raise CommandError(f"Unknown order {order_id}")
        o = pf.orders[order_id]
        if o.status not in ("WORKING", "PARTIALLY_FILLED", "ENTERED"):
            raise CommandError(f"Order {order_id} is {o.status} and cannot be cancelled")
        self.w.emit(E.ORDER_CANCELLED, {"portfolio_id": pf.id, "order_id": order_id}, portfolio_id=pf.id)
        return o

    def _validate(self, pf: Portfolio, sec: Security, side: str, order_type: str, q: Decimal, limit, stop, tif) -> Optional[str]:
        w = self.w
        if side not in ("BUY", "SELL"):
            return f"side must be BUY or SELL, got {side}"
        if order_type not in ("MARKET", "LIMIT", "STOP", "STOP_LIMIT"):
            return f"unsupported order type {order_type}"
        if tif not in ("DAY", "GTC"):
            return f"time in force must be DAY or GTC"
        if q <= 0:
            return "quantity must be positive"
        if sec.lot_size > 1 and q % sec.lot_size != 0:
            return f"{sec.id} trades in multiples of {sec.lot_size:,} face"
        if order_type in ("LIMIT", "STOP_LIMIT") and (limit is None or limit <= 0):
            return "limit price required"
        if order_type in ("STOP", "STOP_LIMIT") and (stop is None or stop <= 0):
            return "stop price required"
        if sec.is_bond and date.fromisoformat(sec.maturity) <= w.current_date:
            return f"{sec.id} has matured"
        if side == "SELL":
            pos = pf.positions.get(sec.id)
            held = pos.quantity if pos else ZERO
            open_sells = sum((o.quantity - o.filled_quantity for o in pf.orders.values()
                              if o.security_id == sec.id and o.side == "SELL" and o.status in ("WORKING", "PARTIALLY_FILLED")), ZERO)
            if held - open_sells < q:
                if held <= 0:
                    return f"no long position in {sec.id}: short selling requires a securities borrow (securities-lending module, Phase 2)"
                return f"sell quantity {q:,} exceeds available long position {held - open_sells:,} (net of working sell orders)"
        if side == "BUY" and order_type == "MARKET":
            bar = w.market.last_bar(sec.id)
            est = self._estimate_cost(sec, q, bar.ask)
            avail = self.projected_cash(pf, sec.currency)
            if est > avail:
                return (f"insufficient projected {sec.currency} cash: order needs ~{est:,.2f} but projected settled cash "
                        f"(settled cash + receivables - payables - working buys) is {avail:,.2f}")
        return None

    def _estimate_cost(self, sec: Security, q: Decimal, px: Decimal) -> Decimal:
        if sec.is_bond:
            gross = money(q * px / 100)
            acc = money(q * BondPricer.accrued_per_100(sec, self.w.current_date) / 100)
            return gross + acc + self._commission(sec, q)
        return money(q * px) + self._commission(sec, q)

    def projected_cash(self, pf: Portfolio, ccy: str) -> Decimal:
        """Settled cash + unsettled sale receivables - unsettled purchase payables - working buy orders."""
        w = self.w
        cash = pf.cash_account(ccy).balance
        recv = sum((si.cash_amount for si in pf.settlements.values() if si.instruction_type == "DVP" and si.currency == ccy
                    and si.status in ("PENDING", "MATCHED", "FAILED")), ZERO)
        pay = sum((si.cash_amount for si in pf.settlements.values() if si.instruction_type == "RVP" and si.currency == ccy
                   and si.status in ("PENDING", "MATCHED", "FAILED")), ZERO)
        working = ZERO
        for o in pf.orders.values():
            if o.side == "BUY" and o.status in ("WORKING", "PARTIALLY_FILLED"):
                sec = w.securities[o.security_id]
                if sec.currency != ccy:
                    continue
                px = o.limit_price or w.market.last_bar(sec.id).ask
                working += self._estimate_cost(sec, o.quantity - o.filled_quantity, px)
        return cash + recv - pay - working

    # ------------------------------------------------------------------ execution
    def _commission(self, sec: Security, q: Decimal) -> Decimal:
        if sec.is_bond:
            return money(q * BOND_COMMISSION_BPS / 10000)
        return max(EQUITY_COMMISSION_MIN, money(q * EQUITY_COMMISSION_PER_SHARE))

    def try_execute(self, order: Order, session: str, cause: Event) -> List[Trade]:
        """Attempt to fill (part of) an order. session: NOW (against current quote) or OPEN (next day's open)."""
        w = self.w
        pf = w.portfolios[order.portfolio_id]
        sec = w.securities[order.security_id]
        bar = w.market.last_bar(sec.id)
        remaining = order.quantity - order.filled_quantity
        if remaining <= 0 or order.status in ("FILLED", "CANCELLED", "REJECTED", "EXPIRED"):
            return []
        rng = random.Random(f"{w.seed}|exec|{order.id}|{w.current_date.isoformat()}|{session}")

        # Reference prices
        if session == "NOW":
            ref_bid, ref_ask, ref_last = bar.bid, bar.ask, bar.close
        else:
            half = (bar.ask - bar.bid) / 2
            ref_bid, ref_ask, ref_last = qprice(bar.open - half), qprice(bar.open + half), bar.open

        # Stop trigger
        if order.order_type in ("STOP", "STOP_LIMIT") and not order.triggered:
            hit = (ref_last >= order.stop_price) if order.side == "BUY" else (ref_last <= order.stop_price)
            if session == "OPEN":
                hit = hit or ((bar.high >= order.stop_price) if order.side == "BUY" else (bar.low <= order.stop_price))
            if not hit:
                self._set_order_status(order, "WORKING", cause, "stop not triggered")
                return []
            w.emit(E.ORDER_STATUS_CHANGED, {"portfolio_id": pf.id, "order_id": order.id, "status": order.status, "triggered": True,
                                            "note": f"stop {order.stop_price} triggered"}, cause_id=cause.id, portfolio_id=pf.id)

        # Limit check
        effective_limit = order.limit_price if order.order_type in ("LIMIT", "STOP_LIMIT") else None
        if effective_limit is not None:
            if session == "NOW":
                marketable = (ref_ask <= effective_limit) if order.side == "BUY" else (ref_bid >= effective_limit)
            else:
                marketable = (bar.low <= effective_limit) if order.side == "BUY" else (bar.high >= effective_limit)
            if not marketable:
                self._set_order_status(order, "WORKING", cause, "limit not marketable")
                return []

        # Liquidity: participation cap, impact
        regime = w.market.regime()
        session_volume = max(1, int(bar.volume * (1.0 if session == "OPEN" else 0.6)))
        if sec.is_bond:
            cap = qqty(D(session_volume) * D(PARTICIPATION_CAP))
            cap = max(cap, D(sec.lot_size))
            cap = cap - (cap % sec.lot_size)
        else:
            cap = max(D(1), qqty(D(session_volume) * D(PARTICIPATION_CAP)))
        fill_qty = min(remaining, cap)
        sigma_daily = max(0.002, w.market.realized_vol(sec.id) / math.sqrt(252.0)) if not sec.is_bond else 0.003
        participation = float(fill_qty) / max(1.0, float(sec.adv) * regime.depth_mult)
        impact_frac = IMPACT_K * sigma_daily * math.sqrt(participation) * rng.uniform(0.8, 1.2)
        if sec.is_bond:
            impact_frac *= 0.5
        if order.side == "BUY":
            raw = float(ref_ask) * (1 + impact_frac)
            px = qprice(raw)
            if effective_limit is not None and px > effective_limit:
                px = effective_limit
        else:
            raw = float(ref_bid) * (1 - impact_frac)
            px = qprice(raw)
            if effective_limit is not None and px < effective_limit:
                px = effective_limit
        spread_cost = money((ref_ask - ref_last if order.side == "BUY" else ref_last - ref_bid) * fill_qty * (D(1) / 100 if sec.is_bond else D(1)))
        impact_cost = money((px - ref_ask if order.side == "BUY" else ref_bid - px) * fill_qty * (D(1) / 100 if sec.is_bond else D(1)))

        commission = self._commission(sec, fill_qty)
        if sec.is_bond:
            gross = money(fill_qty * px / 100)
            accrued = money(fill_qty * BondPricer.accrued_per_100(sec, w.current_date) / 100)
        else:
            gross = money(fill_qty * px)
            accrued = ZERO
        net = gross + accrued + commission if order.side == "BUY" else gross + accrued - commission
        sd = settlement_date(w.calendar, w.settlement_config, sec.market, w.current_date)
        tid = w.new_id("TRD")
        payload = {
            "trade_id": tid, "order_id": order.id, "portfolio_id": pf.id, "security_id": sec.id, "side": order.side,
            "quantity": fill_qty, "price": px, "gross_amount": gross, "accrued_interest": accrued, "commission": commission,
            "net_amount": net, "currency": sec.currency, "trade_date": w.current_date.isoformat(), "settlement_date": sd.isoformat(),
            "execution_detail": {"session": session, "reference_bid": ref_bid, "reference_ask": ref_ask, "reference_last": ref_last,
                                 "spread_cost": spread_cost, "impact_cost": impact_cost, "impact_bps": round(impact_frac * 1e4, 2),
                                 "participation_of_adv": round(participation, 4), "session_volume": session_volume,
                                 "partial": bool(fill_qty < remaining), "regime": regime.name},
        }
        ev = w.emit(E.TRADE_EXECUTED, payload, cause_id=cause.id, portfolio_id=pf.id)
        trade = pf.trades[tid]
        if order.filled_quantity < order.quantity:
            if order.time_in_force == "DAY" and session == "OPEN":
                pass  # expiry handled at close
        return [trade]

    def _set_order_status(self, order: Order, status: str, cause: Event, note: str = "") -> None:
        if order.status != status:
            self.w.emit(E.ORDER_STATUS_CHANGED, {"portfolio_id": order.portfolio_id, "order_id": order.id, "status": status, "note": note},
                        cause_id=cause.id, portfolio_id=order.portfolio_id)

    def expire_day_orders(self, cause: Event) -> None:
        for pf in self.w.portfolios.values():
            for o in list(pf.orders.values()):
                if o.time_in_force == "DAY" and o.status in ("WORKING", "PARTIALLY_FILLED", "ENTERED"):
                    self.w.emit(E.ORDER_STATUS_CHANGED, {"portfolio_id": pf.id, "order_id": o.id, "status": "EXPIRED",
                                                         "note": "day order expired unfilled" if o.filled_quantity == 0 else "day order expired with partial fill"},
                                cause_id=cause.id, portfolio_id=pf.id)

    def work_open_orders(self, cause: Event) -> None:
        for pf in self.w.portfolios.values():
            for o in list(pf.orders.values()):
                if o.status in ("WORKING", "PARTIALLY_FILLED"):
                    self.try_execute(o, session="OPEN", cause=cause)

    # ------------------------------------------------------------------ handlers
    def _h_order_entered(self, ev: Event) -> None:
        p = ev.payload
        pf = self.w.portfolios[p["portfolio_id"]]
        pf.orders[p["order_id"]] = Order(
            id=p["order_id"], portfolio_id=pf.id, security_id=p["security_id"], side=p["side"], order_type=p["order_type"],
            quantity=D(p["quantity"]), limit_price=D(p["limit_price"]) if p.get("limit_price") is not None else None,
            stop_price=D(p["stop_price"]) if p.get("stop_price") is not None else None, time_in_force=p["time_in_force"],
            status="ENTERED", entered_date=ev.sim_date, strategy_tag=p.get("strategy_tag"))

    def _h_order_rejected(self, ev: Event) -> None:
        p = ev.payload
        pf = self.w.portfolios[p["portfolio_id"]]
        pf.orders[p["order_id"]] = Order(
            id=p["order_id"], portfolio_id=pf.id, security_id=p["security_id"], side=p["side"], order_type=p["order_type"],
            quantity=D(p["quantity"]), limit_price=D(p["limit_price"]) if p.get("limit_price") is not None else None,
            stop_price=D(p["stop_price"]) if p.get("stop_price") is not None else None, time_in_force=p["time_in_force"],
            status="REJECTED", entered_date=ev.sim_date, reason=p["reason"], strategy_tag=p.get("strategy_tag"))

    def _h_order_cancelled(self, ev: Event) -> None:
        o = self.w.portfolios[ev.payload["portfolio_id"]].orders[ev.payload["order_id"]]
        o.status = "CANCELLED"

    def _h_order_status(self, ev: Event) -> None:
        p = ev.payload
        o = self.w.portfolios[p["portfolio_id"]].orders[p["order_id"]]
        o.status = p["status"]
        if p.get("triggered"):
            o.triggered = True
        if p.get("note"):
            o.reason = p["note"]

    def _h_trade_executed(self, ev: Event) -> None:
        w = self.w
        p = ev.payload
        pf = w.portfolios[p["portfolio_id"]]
        sec = w.securities[p["security_id"]]
        q, px = D(p["quantity"]), D(p["price"])
        gross, accrued, commission, net = D(p["gross_amount"]), D(p["accrued_interest"]), D(p["commission"]), D(p["net_amount"])
        trade = Trade(id=p["trade_id"], order_id=p["order_id"], portfolio_id=pf.id, security_id=sec.id, side=p["side"], quantity=q,
                      price=px, gross_amount=gross, accrued_interest=accrued, commission=commission, net_amount=net,
                      currency=p["currency"], trade_date=p["trade_date"], settlement_date=p["settlement_date"], status="EXECUTED",
                      execution_detail={k: (D(v) if isinstance(v, str) and _looks_decimal(v) else v) for k, v in p["execution_detail"].items()})
        trade.status_history.append({"status": "EXECUTED", "date": ev.sim_date, "event_id": ev.id, "note": "filled by executing broker"})
        pf.trades[trade.id] = trade
        pf.day_trade_ids.append(trade.id)
        pos = pf.position(sec.id)
        pos.trade_ids.append(trade.id)
        pos.commissions += commission
        pos.mark = px if pos.mark == 0 else pos.mark
        order = pf.orders[trade.order_id]
        cost_acct = "1110" if sec.is_bond else "1100"
        lines: List[Dict] = []
        if trade.side == "BUY":
            lot = Lot(id=f"LOT-{ev.seq:06d}", trade_id=trade.id, open_date=trade.trade_date, quantity=q, original_quantity=q,
                      cost_per_unit=px, cost_total=gross)
            pos.lots.append(lot)
            pos.quantity += q
            pos.cost_basis += gross
            pos.pending_receive += q
            if sec.is_bond:
                pos.accrued_interest += accrued
            memo = f"BUY {q:,} {sec.id} @ {px} (trade {trade.id})"
            lines = [dr(cost_acct, gross, sec.id, "cost of securities purchased"),
                     dr("1220", accrued, sec.id, "accrued interest purchased") if sec.is_bond else None,
                     dr("5000", commission, sec.id, "commission"),
                     cr("2100", net, sec.id, f"payable to broker, settles {trade.settlement_date}")]
        else:
            trueup = ZERO
            if sec.is_bond:
                # The position's accrual is as of the last close; bring it to trade date before relieving.
                target = money(pos.quantity * BondPricer.accrued_per_100(sec, date.fromisoformat(trade.trade_date)) / 100)
                trueup = target - pos.accrued_interest
                pos.accrued_interest = target
                pos.interest_income += trueup
            relieved, cost_relieved = self._relieve_fifo(pos, q, ev)
            realized = gross - cost_relieved
            trade.realized_pnl = realized
            trade.lots_relieved = relieved
            pos.quantity -= q
            pos.cost_basis -= cost_relieved
            pos.realized_pnl += realized
            pos.pending_deliver += q
            if sec.is_bond:
                pos.accrued_interest -= accrued
            memo = f"SELL {q:,} {sec.id} @ {px} (trade {trade.id}) realized {realized:,.2f}"
            lines = [dr("1220", trueup, sec.id, "accrual to trade date") if trueup else None,
                     cr("4300", trueup, sec.id, "interest income to trade date") if trueup else None,
                     dr("1200", net, sec.id, f"receivable from broker, settles {trade.settlement_date}"),
                     dr("5000", commission, sec.id, "commission"),
                     cr(cost_acct, cost_relieved, sec.id, "cost of securities sold (FIFO)"),
                     cr("1220", accrued, sec.id, "accrued interest sold") if sec.is_bond else None,
                     cr("4000", realized, sec.id, "realized gain/loss")]
        lines = [l for l in lines if l]
        # order bookkeeping
        prev_filled = order.filled_quantity
        order.filled_quantity += q
        order.avg_fill_price = qprice((order.avg_fill_price * prev_filled + px * q) / order.filled_quantity)
        order.trade_ids.append(trade.id)
        order.status = "FILLED" if order.filled_quantity >= order.quantity else "PARTIALLY_FILLED"

        # derived events
        w.post(pf.id, memo, lines, ev, {"trade_id": trade.id, "order_id": order.id, "security_id": sec.id})
        w.derive(E.SETTLEMENT_INSTRUCTION_CREATED, {
            "si_id": f"SI-{w.next_seq():06d}", "trade_id": trade.id, "portfolio_id": pf.id, "security_id": sec.id, "isin": sec.isin,
            "cusip": sec.cusip, "instruction_type": "RVP" if trade.side == "BUY" else "DVP", "quantity": q, "cash_amount": net,
            "currency": sec.currency, "trade_date": trade.trade_date, "settlement_date": trade.settlement_date,
            "delivering_party": trade.broker if trade.side == "BUY" else pf.custody_account,
            "receiving_party": pf.custody_account if trade.side == "BUY" else trade.broker,
            "custodian": "Meridian Custody Services"}, ev, portfolio_id=pf.id)
        w.pnl.mark_position(pf, sec.id, ev)

    def _relieve_fifo(self, pos, q: Decimal, ev: Event) -> Tuple[List[Dict], Decimal]:
        remaining = q
        relieved = []
        cost = ZERO
        for lot in pos.lots:
            if remaining <= 0:
                break
            if lot.quantity <= 0:
                continue
            take = min(lot.quantity, remaining)
            lot_cost = money(lot.cost_total * take / lot.quantity) if take < lot.quantity else lot.cost_total
            lot.quantity -= take
            lot.cost_total -= lot_cost
            remaining -= take
            cost += lot_cost
            relieved.append({"lot_id": lot.id, "quantity": take, "cost": lot_cost, "cost_per_unit": lot.cost_per_unit, "open_date": lot.open_date})
        if remaining > 0:
            raise RuntimeError(f"FIFO relief short by {remaining} — validation should have prevented this")
        pos.lots = [l for l in pos.lots if l.quantity > 0]
        return relieved, cost

    def _h_trade_status(self, ev: Event) -> None:
        p = ev.payload
        t = self.w.portfolios[p["portfolio_id"]].trades[p["trade_id"]]
        t.status = p["status"]
        t.status_history.append({"status": p["status"], "date": ev.sim_date, "event_id": ev.id, "note": p.get("note", "")})


def _looks_decimal(s: str) -> bool:
    try:
        D(s)
        return any(ch.isdigit() for ch in s) and not any(ch.isalpha() for ch in s)
    except Exception:
        return False
