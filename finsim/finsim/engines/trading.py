"""Trade engine for a once-per-day world.

Orders entered by the player never execute immediately: they are instructions
for the *next* daily update ("market-on-next-update"). During the update every
working order is evaluated against that day's session bar (open/high/low/close,
volume) with realistic liquidity:

  MARKET          fills at the open, crossing the spread and paying square-root
                  market impact scaled by volatility and participation
  LIMIT / TAKE_PROFIT   fills only if the session traded through the price;
                  a favourable gap at the open fills at the open
  STOP / STOP_LIMIT     triggers if the session traded through the stop, then
                  fills like a market (or limit) order from the trigger price,
                  or from the open if the market gapped through it
  TRAILING_STOP   a stop that ratchets with each day's close
  condition       any order can carry "only if X <= / >= value" on a security
                  close, a curve tenor yield (CURVE:10Y) or a commodity spot
                  (SPOT:CL); evaluated at the close, then executed at the close

Orders above 20% of the session's volume partially fill and keep working
(GTC) or expire (DAY = good for the next session).

Futures are margined instruments: positions can be short, settle daily through
variation margin (futures.py), commissions settle same day through the
clearing broker, and there is no DVP settlement instruction.
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
from ..engines.pricing import BondPricer, interp_rate
from ..money import D, money, price as qprice, qty as qqty, ZERO

PARTICIPATION_CAP = 0.20
IMPACT_K = 0.6
EQUITY_COMMISSION_PER_SHARE = D("0.005")
EQUITY_COMMISSION_MIN = D("1.00")
BOND_COMMISSION_BPS = D("0.5")
FUTURES_COMMISSION_PER_CONTRACT = D("2.50")
ORDER_TYPES = ("MARKET", "LIMIT", "STOP", "STOP_LIMIT", "TAKE_PROFIT", "TRAILING_STOP")


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
                    time_in_force: str, strategy_tag: Optional[str], trail_pct: Optional[float] = None, condition: Optional[Dict] = None) -> Order:
        from ..world import CommandError
        w = self.w
        pf = w.portfolio(portfolio_id)
        sec = w.security(security_id)
        side, order_type, tif = side.upper(), order_type.upper(), time_in_force.upper()
        q = qqty(quantity)
        last = w.market.last_bar(sec.id).close if w.market.history.get(sec.id) else ZERO
        trail_level = None
        if order_type == "TRAILING_STOP" and trail_pct:
            trail_level = qprice(last * D(1 - float(trail_pct))) if side == "SELL" else qprice(last * D(1 + float(trail_pct)))
        base = {"portfolio_id": pf.id, "security_id": sec.id, "side": side, "order_type": order_type, "quantity": q,
                "limit_price": qprice(limit_price) if limit_price is not None else None,
                "stop_price": qprice(stop_price) if stop_price is not None else None, "time_in_force": tif, "strategy_tag": strategy_tag,
                "trail_pct": float(trail_pct) if trail_pct else None, "trail_level": trail_level, "condition": self._norm_condition(condition)}
        reason = self._validate(pf, sec, side, order_type, q, base["limit_price"], base["stop_price"], tif, base["trail_pct"], base["condition"])
        if reason:
            w.emit(E.ORDER_REJECTED, {**base, "order_id": w.new_id("ORD"), "reason": reason}, portfolio_id=pf.id)
            raise CommandError(f"Order rejected: {reason}")
        oid = w.new_id("ORD")
        w.emit(E.ORDER_ENTERED, {**base, "order_id": oid}, portfolio_id=pf.id)
        return pf.orders[oid]

    def _norm_condition(self, c: Optional[Dict]) -> Optional[Dict]:
        if not c or not c.get("ref"):
            return None
        return {"ref": str(c["ref"]).upper().strip(), "op": c.get("op", "<="), "value": float(c["value"])}

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

    def _validate(self, pf: Portfolio, sec: Security, side: str, order_type: str, q: Decimal, limit, stop, tif, trail_pct, condition) -> Optional[str]:
        w = self.w
        if side not in ("BUY", "SELL"):
            return f"side must be BUY or SELL, got {side}"
        if order_type not in ORDER_TYPES:
            return f"unsupported order type {order_type}"
        if tif not in ("DAY", "GTC"):
            return "time in force must be DAY (good for the next session) or GTC"
        if q <= 0:
            return "quantity must be positive"
        if sec.lot_size > 1 and q % sec.lot_size != 0:
            return f"{sec.id} trades in multiples of {sec.lot_size:,} face"
        if order_type in ("LIMIT", "STOP_LIMIT", "TAKE_PROFIT") and (limit is None or limit <= 0):
            return "limit price required"
        if order_type in ("STOP", "STOP_LIMIT") and (stop is None or stop <= 0):
            return "stop price required"
        if order_type == "TRAILING_STOP" and (not trail_pct or trail_pct <= 0 or trail_pct >= 0.5):
            return "trailing stop needs a trail percentage between 0 and 50%"
        if condition:
            if condition["op"] not in ("<=", ">="):
                return "condition operator must be <= or >="
            ref = condition["ref"]
            if not (ref in w.securities or (ref.startswith("CURVE:") and ref[6:].rstrip("Y").replace(".", "").isdigit())
                    or (ref.startswith("SPOT:") and ref[5:] in w.market.commodities.state)):
                return f"unknown condition reference {ref} (use a ticker, CURVE:10Y, or SPOT:CL)"
        if sec.is_bond and date.fromisoformat(sec.maturity) <= w.current_date:
            return f"{sec.id} has matured"
        if sec.is_future and (sec.expired or (sec.expiry and date.fromisoformat(sec.expiry) <= w.current_date)):
            return f"{sec.id} has expired / is in its last session; use a later contract month"
        job = w.careers.job_for(pf)
        if job and job.allowed_classes and self._class_key(sec) not in job.allowed_classes:
            return f"{sec.id} ({self._class_key(sec)}) is outside this job's mandate ({', '.join(sorted(job.allowed_classes))})"
        if sec.is_future:
            # margin check: initial margin for the resulting position must fit projected cash
            im = self.w.futures.initial_margin_per_contract(sec) * q
            avail = self.projected_cash(pf, sec.currency)
            pos = pf.positions.get(sec.id)
            reduces = pos is not None and ((pos.quantity > 0 and side == "SELL") or (pos.quantity < 0 and side == "BUY"))
            if not reduces and im > avail:
                return f"insufficient cash for initial margin: ~{im:,.0f} required, projected cash {avail:,.0f}"
            return None
        if side == "SELL":
            pos = pf.positions.get(sec.id)
            held = pos.quantity if pos else ZERO
            open_sells = sum((o.quantity - o.filled_quantity for o in pf.orders.values()
                              if o.security_id == sec.id and o.side == "SELL" and o.status in ("WORKING", "PARTIALLY_FILLED")), ZERO)
            if held - open_sells < q:
                if held <= 0:
                    return f"no long position in {sec.id}: short selling requires a securities borrow (securities-lending module, Phase 2)"
                return f"sell quantity {q:,} exceeds available long position {held - open_sells:,} (net of working sell orders)"
        if side == "BUY":
            bar = w.market.last_bar(sec.id)
            px = limit if (order_type in ("LIMIT", "TAKE_PROFIT") and limit) else bar.ask
            est = self._estimate_cost(sec, q, px)
            avail = self.projected_cash(pf, sec.currency)
            if est > avail:
                return (f"insufficient projected {sec.currency} cash: order needs ~{est:,.2f} but projected settled cash "
                        f"(settled cash + receivables - payables - working buys - margin) is {avail:,.2f}")
        return None

    @staticmethod
    def _class_key(sec: Security) -> str:
        if sec.is_future:
            return sec.underlying_class or "FUTURE"
        if sec.is_bond:
            return sec.asset_class
        return "EQUITY"

    def _estimate_cost(self, sec: Security, q: Decimal, px: Decimal) -> Decimal:
        if sec.is_future:
            return self.w.futures.initial_margin_per_contract(sec) * q
        if sec.is_bond:
            gross = money(q * px / 100)
            acc = money(q * BondPricer.accrued_per_100(sec, self.w.current_date) / 100)
            return gross + acc + self._commission(sec, q)
        return money(q * px) + self._commission(sec, q)

    def projected_cash(self, pf: Portfolio, ccy: str) -> Decimal:
        """Settled cash + sale receivables - purchase payables - working buys (+ working sells at the bid) - margin for futures orders.

        Expected proceeds of working sell orders count as projected liquidity, as a treasury projection would; if
        the sell does not fill, a purchase relying on it fails at settlement and retries."""
        w = self.w
        cash = pf.cash_account(ccy).balance
        recv = sum((si.cash_amount for si in pf.settlements.values() if si.instruction_type == "DVP" and si.currency == ccy
                    and si.status in ("PENDING", "MATCHED", "FAILED")), ZERO)
        pay = sum((si.cash_amount for si in pf.settlements.values() if si.instruction_type == "RVP" and si.currency == ccy
                   and si.status in ("PENDING", "MATCHED", "FAILED")), ZERO)
        working = ZERO
        for o in pf.orders.values():
            if o.status in ("WORKING", "PARTIALLY_FILLED"):
                sec = w.securities[o.security_id]
                if sec.currency != ccy:
                    continue
                if sec.is_future:
                    working += self._estimate_cost(sec, o.quantity - o.filled_quantity, ZERO)
                elif o.side == "BUY":
                    px = o.limit_price or w.market.last_bar(sec.id).ask
                    working += self._estimate_cost(sec, o.quantity - o.filled_quantity, px)
                else:
                    px = o.limit_price if o.order_type in ("LIMIT", "TAKE_PROFIT") and o.limit_price else w.market.last_bar(sec.id).bid
                    rem = o.quantity - o.filled_quantity
                    working -= (money(rem * px / 100) if sec.is_bond else money(rem * px)) - self._commission(sec, rem)
        return cash + recv - pay - working

    # ------------------------------------------------------------------ execution during the daily update
    def _commission(self, sec: Security, q: Decimal) -> Decimal:
        if sec.is_future:
            return money(q * FUTURES_COMMISSION_PER_CONTRACT)
        if sec.is_bond:
            return money(q * BOND_COMMISSION_BPS / 10000)
        return max(EQUITY_COMMISSION_MIN, money(q * EQUITY_COMMISSION_PER_SHARE))

    def _ref_value(self, ref: str) -> Optional[float]:
        w = self.w
        if ref.startswith("CURVE:"):
            t = float(ref[6:].rstrip("Y"))
            return interp_rate(w.market.curve(), t) * 100.0
        if ref.startswith("SPOT:"):
            return w.market.spot(ref[5:])
        if ref in w.securities and w.market.history.get(ref):
            return float(w.market.last_bar(ref).close)
        return None

    def work_orders(self, cause: Event) -> None:
        """Evaluate every working order against today's session."""
        w = self.w
        for pf in w.portfolios.values():
            for o in list(pf.orders.values()):
                if o.status in ("WORKING", "PARTIALLY_FILLED"):
                    self._evaluate(o, cause)

    def _evaluate(self, order: Order, cause: Event) -> None:
        w = self.w
        pf = w.portfolios[order.portfolio_id]
        sec = w.securities[order.security_id]
        if not w.market.history.get(sec.id):
            return
        bar = w.market.last_bar(sec.id)
        today = w.current_date.isoformat()
        if sec.is_future and sec.expired:
            self._set_status(order, "CANCELLED", cause, "contract expired before the order could execute")
            return
        remaining = order.quantity - order.filled_quantity
        if remaining <= 0:
            return
        half = (bar.ask - bar.bid) / 2
        session = "OPEN"
        base: Optional[Decimal] = None

        # condition gate: evaluated at the close, executed at the close
        if order.condition and not order.condition_met_date:
            v = self._ref_value(order.condition["ref"])
            if v is None:
                return
            ok = (v <= order.condition["value"]) if order.condition["op"] == "<=" else (v >= order.condition["value"])
            if not ok:
                self._note(order, cause, f"condition not met: {order.condition['ref']} = {v:,.4g}")
                return
            w.emit(E.ORDER_STATUS_CHANGED, {"portfolio_id": pf.id, "order_id": order.id, "status": order.status, "condition_met_date": today,
                                            "note": f"condition met: {order.condition['ref']} = {v:,.4g} {order.condition['op']} {order.condition['value']}"},
                   cause_id=cause.id, portfolio_id=pf.id)
        if order.condition:
            session = "CLOSE"

        otype = order.order_type
        if otype == "TRAILING_STOP":
            level = order.trail_level
            hit = (bar.low <= level) if order.side == "SELL" else (bar.high >= level)
            if not hit:
                new_level = qprice(bar.close * D(1 - order.trail_pct)) if order.side == "SELL" else qprice(bar.close * D(1 + order.trail_pct))
                better = (new_level > level) if order.side == "SELL" else (new_level < level)
                if better:
                    w.emit(E.ORDER_STATUS_CHANGED, {"portfolio_id": pf.id, "order_id": order.id, "status": order.status, "trail_level": new_level,
                                                    "note": f"trailing stop ratchets to {new_level}"}, cause_id=cause.id, portfolio_id=pf.id)
                return
            gapped = (bar.open <= level) if order.side == "SELL" else (bar.open >= level)
            base = bar.open if gapped else level
            if not order.triggered:
                w.emit(E.ORDER_STATUS_CHANGED, {"portfolio_id": pf.id, "order_id": order.id, "status": order.status, "triggered": True,
                                                "note": f"trailing stop {level} triggered"}, cause_id=cause.id, portfolio_id=pf.id)
        elif otype in ("STOP", "STOP_LIMIT"):
            if not order.triggered:
                hit = (bar.high >= order.stop_price) if order.side == "BUY" else (bar.low <= order.stop_price)
                if not hit:
                    return
                w.emit(E.ORDER_STATUS_CHANGED, {"portfolio_id": pf.id, "order_id": order.id, "status": order.status, "triggered": True,
                                                "note": f"stop {order.stop_price} triggered"}, cause_id=cause.id, portfolio_id=pf.id)
                gapped = (bar.open >= order.stop_price) if order.side == "BUY" else (bar.open <= order.stop_price)
                base = bar.open if gapped else order.stop_price
            else:
                base = bar.open
            if otype == "STOP_LIMIT":
                lim = order.limit_price
                tradable = (bar.low <= lim) if order.side == "BUY" else (bar.high >= lim)
                if not tradable or ((base > lim) if order.side == "BUY" else (base < lim)):
                    self._note(order, cause, "stop triggered but limit not reached")
                    return
        elif otype in ("LIMIT", "TAKE_PROFIT"):
            lim = order.limit_price
            if session == "CLOSE":
                ok = (bar.close <= lim) if order.side == "BUY" else (bar.close >= lim)
                if not ok:
                    self._note(order, cause, "limit not marketable at the close")
                    return
                base = bar.close
            else:
                tradable = (bar.low <= lim) if order.side == "BUY" else (bar.high >= lim)
                if not tradable:
                    self._note(order, cause, f"limit {lim} not reached (session {bar.low}-{bar.high})")
                    return
                base = min(bar.open, lim) if order.side == "BUY" else max(bar.open, lim)
        else:  # MARKET
            base = bar.close if session == "CLOSE" else bar.open

        self._fill(order, sec, bar, base, half, session, cause)

    def _fill(self, order: Order, sec: Security, bar: Bar, base: Decimal, half: Decimal, session: str, cause: Event,
              forced: bool = False, note: str = "") -> Optional[Trade]:
        w = self.w
        pf = w.portfolios[order.portfolio_id]
        remaining = order.quantity - order.filled_quantity
        rng = random.Random(f"{w.seed}|exec|{order.id}|{w.current_date.isoformat()}")
        regime = w.market.regime()
        session_volume = max(1, int(bar.volume))
        cap = qqty(D(session_volume) * D(PARTICIPATION_CAP))
        if sec.lot_size > 1:
            cap = max(D(sec.lot_size), cap - (cap % sec.lot_size))
        cap = max(D(1), cap)
        fill_qty = min(remaining, cap) if not forced else remaining
        if sec.is_bond:
            sigma_daily = 0.003
        elif sec.is_future:
            sigma_daily = max(0.003, sec.sigma_annual / math.sqrt(252.0))
        else:
            sigma_daily = max(0.002, w.market.realized_vol(sec.id) / math.sqrt(252.0))
        participation = float(fill_qty) / max(1.0, float(sec.adv) * regime.depth_mult)
        impact_frac = IMPACT_K * sigma_daily * math.sqrt(participation) * rng.uniform(0.8, 1.2)
        if sec.is_bond:
            impact_frac *= 0.5
        if forced:
            impact_frac += 0.005
        if order.side == "BUY":
            px = qprice(float(base + half) * (1 + impact_frac))
            if order.limit_price is not None and order.order_type in ("LIMIT", "STOP_LIMIT", "TAKE_PROFIT") and px > order.limit_price:
                px = order.limit_price
        else:
            px = qprice(float(base - half) * (1 - impact_frac))
            if order.limit_price is not None and order.order_type in ("LIMIT", "STOP_LIMIT", "TAKE_PROFIT") and px < order.limit_price:
                px = order.limit_price
        if sec.is_future and sec.tick_size:
            t = D(str(sec.tick_size))
            px = qprice((px / t).quantize(Decimal("1")) * t)
        unit = D(1) / 100 if sec.is_bond else (D(str(sec.multiplier)) if sec.is_future else D(1))
        spread_cost = money(half * fill_qty * unit)
        impact_cost = money(abs(px - (base + half if order.side == "BUY" else base - half)) * fill_qty * unit)
        commission = self._commission(sec, fill_qty)
        if sec.is_bond:
            gross = money(fill_qty * px / 100)
            accrued = money(fill_qty * BondPricer.accrued_per_100(sec, w.current_date) / 100)
        elif sec.is_future:
            gross = money(fill_qty * px * D(str(sec.multiplier)))    # notional, informational
            accrued = ZERO
        else:
            gross = money(fill_qty * px)
            accrued = ZERO
        if sec.is_future:
            net = commission
        else:
            net = gross + accrued + commission if order.side == "BUY" else gross + accrued - commission
        sd = settlement_date(w.calendar, w.settlement_config, sec.market, w.current_date)
        tid = w.new_id("TRD")
        payload = {
            "trade_id": tid, "order_id": order.id, "portfolio_id": pf.id, "security_id": sec.id, "side": order.side,
            "quantity": fill_qty, "price": px, "gross_amount": gross, "accrued_interest": accrued, "commission": commission,
            "net_amount": net, "currency": sec.currency, "trade_date": w.current_date.isoformat(), "settlement_date": sd.isoformat(),
            "execution_detail": {"session": session, "reference_open": bar.open, "reference_high": bar.high, "reference_low": bar.low,
                                 "reference_close": bar.close, "base_price": base, "spread_cost": spread_cost, "impact_cost": impact_cost,
                                 "impact_bps": round(impact_frac * 1e4, 2), "participation_of_adv": round(participation, 4),
                                 "session_volume": session_volume, "partial": bool(fill_qty < remaining), "regime": regime.name,
                                 "forced": forced, "note": note},
        }
        ev = w.emit(E.TRADE_EXECUTED, payload, cause_id=cause.id, portfolio_id=pf.id)
        return pf.trades[tid]

    def _note(self, order: Order, cause: Event, note: str) -> None:
        order.reason = note
        order.history.append({"date": self.w.current_date.isoformat(), "note": note})

    def _set_status(self, order: Order, status: str, cause: Event, note: str = "") -> None:
        if order.status != status:
            self.w.emit(E.ORDER_STATUS_CHANGED, {"portfolio_id": order.portfolio_id, "order_id": order.id, "status": status, "note": note},
                        cause_id=cause.id, portfolio_id=order.portfolio_id)

    def expire_day_orders(self, cause: Event) -> None:
        today = self.w.current_date.isoformat()
        for pf in self.w.portfolios.values():
            for o in list(pf.orders.values()):
                if o.time_in_force == "DAY" and o.status in ("WORKING", "PARTIALLY_FILLED", "ENTERED") and o.entered_date < today:
                    self._set_status(o, "EXPIRED", cause, "good-for-day order expired unfilled" if o.filled_quantity == 0 else "day order expired after partial fill")

    def system_order(self, pf: Portfolio, sec: Security, side: str, qty: Decimal, cause: Event, reason: str, forced: bool) -> Optional[Trade]:
        """Order created by the system (contract expiry, forced liquidation). Fills at the close with slippage."""
        w = self.w
        oid = w.new_id("ORD")
        w.emit(E.ORDER_ENTERED, {"portfolio_id": pf.id, "security_id": sec.id, "side": side, "order_type": "MARKET", "quantity": qty,
                                 "limit_price": None, "stop_price": None, "time_in_force": "DAY", "strategy_tag": "SYSTEM", "order_id": oid,
                                 "trail_pct": None, "trail_level": None, "condition": None, "system_reason": reason}, cause_id=cause.id, portfolio_id=pf.id)
        order = pf.orders[oid]
        bar = w.market.last_bar(sec.id)
        return self._fill(order, sec, bar, bar.close, (bar.ask - bar.bid) / 2, "CLOSE", cause, forced=forced, note=reason)

    # ------------------------------------------------------------------ handlers
    def _order_from_payload(self, p: Dict, status: str, ev: Event) -> Order:
        o = Order(id=p["order_id"], portfolio_id=p["portfolio_id"], security_id=p["security_id"], side=p["side"], order_type=p["order_type"],
                  quantity=D(p["quantity"]), limit_price=D(p["limit_price"]) if p.get("limit_price") is not None else None,
                  stop_price=D(p["stop_price"]) if p.get("stop_price") is not None else None, time_in_force=p["time_in_force"],
                  status=status, entered_date=ev.sim_date, strategy_tag=p.get("strategy_tag"), reason=p.get("reason") or p.get("system_reason"),
                  trail_pct=p.get("trail_pct"), trail_level=D(p["trail_level"]) if p.get("trail_level") is not None else None,
                  condition=p.get("condition"))
        o.history.append({"date": ev.sim_date, "note": "entered — executes at the next daily update" if status == "WORKING" else p.get("reason", "")})
        return o

    def _h_order_entered(self, ev: Event) -> None:
        p = ev.payload
        pf = self.w.portfolios[p["portfolio_id"]]
        pf.orders[p["order_id"]] = self._order_from_payload(p, "WORKING", ev)

    def _h_order_rejected(self, ev: Event) -> None:
        p = ev.payload
        pf = self.w.portfolios[p["portfolio_id"]]
        pf.orders[p["order_id"]] = self._order_from_payload(p, "REJECTED", ev)

    def _h_order_cancelled(self, ev: Event) -> None:
        o = self.w.portfolios[ev.payload["portfolio_id"]].orders[ev.payload["order_id"]]
        o.status = "CANCELLED"
        o.history.append({"date": ev.sim_date, "note": "cancelled by player"})

    def _h_order_status(self, ev: Event) -> None:
        p = ev.payload
        o = self.w.portfolios[p["portfolio_id"]].orders[p["order_id"]]
        o.status = p["status"]
        if p.get("triggered"):
            o.triggered = True
        if p.get("trail_level") is not None:
            o.trail_level = D(p["trail_level"])
        if p.get("condition_met_date"):
            o.condition_met_date = p["condition_met_date"]
        if p.get("note"):
            o.reason = p["note"]
            o.history.append({"date": ev.sim_date, "note": p["note"]})

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
        trade.status_history.append({"status": "EXECUTED", "date": ev.sim_date, "event_id": ev.id,
                                     "note": "filled by clearing broker" if sec.is_future else "filled by executing broker"})
        if sec.is_future:
            trade.broker = "Harbor Securities (futures clearing member)"
        pf.trades[trade.id] = trade
        pf.day_trade_ids.append(trade.id)
        pos = pf.position(sec.id)
        pos.trade_ids.append(trade.id)
        pos.commissions += commission
        order = pf.orders[trade.order_id]
        prev_filled = order.filled_quantity
        order.filled_quantity += q
        order.avg_fill_price = qprice((order.avg_fill_price * prev_filled + px * q) / order.filled_quantity)
        order.trade_ids.append(trade.id)
        order.status = "FILLED" if order.filled_quantity >= order.quantity else "PARTIALLY_FILLED"

        if sec.is_future:
            self._apply_futures_trade(pf, pos, sec, trade, ev)
            return

        cost_acct = "1110" if sec.is_bond else "1100"
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
        w.post(pf.id, memo, lines, ev, {"trade_id": trade.id, "order_id": order.id, "security_id": sec.id})
        w.derive(E.SETTLEMENT_INSTRUCTION_CREATED, {
            "si_id": f"SI-{w.next_seq():06d}", "trade_id": trade.id, "portfolio_id": pf.id, "security_id": sec.id, "isin": sec.isin,
            "cusip": sec.cusip, "instruction_type": "RVP" if trade.side == "BUY" else "DVP", "quantity": q, "cash_amount": net,
            "currency": sec.currency, "trade_date": trade.trade_date, "settlement_date": trade.settlement_date,
            "delivering_party": trade.broker if trade.side == "BUY" else pf.custody_account,
            "receiving_party": pf.custody_account if trade.side == "BUY" else trade.broker,
            "custodian": "Meridian Custody Services"}, ev, portfolio_id=pf.id)
        w.pnl.mark_position(pf, sec.id, ev)

    def _apply_futures_trade(self, pf: Portfolio, pos, sec: Security, trade: Trade, ev: Event) -> None:
        """Futures: signed contract position; P&L flows through daily variation margin (futures.py)."""
        w = self.w
        signed = trade.quantity if trade.side == "BUY" else -trade.quantity
        pos.is_future = True
        old = pos.quantity
        new = old + signed
        if old == 0 or (old > 0) != (new > 0) and new != 0:
            pos.cost_basis = ZERO
            avg = trade.price if new != 0 else ZERO
        elif abs(new) > abs(old):
            avg = qprice((pos.average_cost_future * abs(old) + trade.price * trade.quantity) / abs(new))
        else:
            avg = pos.average_cost_future
        pos.quantity = new
        pos.settled_quantity = new
        pos.average_cost_future = avg if new != 0 else ZERO
        if pos.settlement_price == 0:
            pos.settlement_price = trade.price
        pos.day_fills.append({"quantity": signed, "price": trade.price, "trade_id": trade.id})
        trade.status = "CLEARED"
        trade.status_history.append({"status": "CLEARED", "date": ev.sim_date, "event_id": ev.id, "note": "cleared through the futures clearing member; margined daily"})
        pf.cash_account(sec.currency).balance -= trade.commission
        w.record_cash_movement(pf, sec.currency, -trade.commission, "COMMISSION", f"Futures commission {trade.id} {sec.id}", ev)
        w.post(pf.id, f"{trade.side} {trade.quantity:,} {sec.id} @ {trade.price} (trade {trade.id}, cleared)",
               [dr("5000", trade.commission, sec.id, "futures commission"), cr(f"1010:{sec.currency}", trade.commission, sec.id, "paid to clearing broker")],
               ev, {"trade_id": trade.id, "order_id": trade.order_id, "security_id": sec.id})

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
