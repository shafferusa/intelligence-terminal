"""Commodity desk depth (phase 9): calendar-spread tickets, physical delivery and storage.

Calendar spreads are strategy tickets with two futures legs (buy one contract month, sell
another of the same commodity) executed all-or-none at the open when the differential is
inside the ticket's limit; the clearing house recognises the offset with a reduced initial
margin (see futures.required_margin).

Physical mode is a portfolio setting. When it is on, a commodity future held into its last
trade date is not auto-closed: a long takes delivery — the futures position is closed at the
settlement price and the same notional of the commodity is bought into inventory (a
PHYSICAL security priced daily at the commodity's spot) — and a short makes delivery from
inventory, or is bought in at the settlement price plus a 2% penalty if it has nothing to
deliver. Inventory carries the commodity's storage and insurance cost daily and is sold
through an ordinary sell order at spot.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Dict, List, Optional

from ..domain.events import E, Event
from ..domain.models import Portfolio, Security, Strategy
from ..engines.ledger import dr, cr
from ..money import D, money, qty as qqty, price as qprice, ZERO

BUY_IN_PENALTY = 0.02
SPREAD_MARGIN_RATE = 0.35          # initial margin on a paired long/short of the same commodity, as a fraction of one outright
PHYSICAL_SPREAD_BPS = 50.0         # dealing spread on physical (spot) inventory


def inventory_id(code: str) -> str:
    return f"PHYS-{code}"


class CommodityDeskEngine:
    def __init__(self, world):
        self.w = world

    def register(self) -> None:
        w = self.w
        w.on(E.PHYSICAL_MODE_SET, lambda world, ev: self._h_mode(ev))
        w.on(E.PHYSICAL_LISTED, lambda world, ev: self._h_listed(ev))
        w.on(E.PHYSICAL_DELIVERY, lambda world, ev: self._h_delivery(ev))
        w.on(E.STORAGE_CHARGED, lambda world, ev: self._h_storage(ev))

    # ------------------------------------------------------------------ helpers
    def spec(self, code: str):
        from .commodities import SPEC_BY_CODE
        return SPEC_BY_CODE.get(code)

    def is_physical_commodity(self, sec: Security) -> bool:
        return bool(sec.is_future and (sec.underlying_class or "").startswith("COMMODITY") and self.spec(sec.underlying))

    # ------------------------------------------------------------------ commands
    def set_physical_delivery(self, pf: Portfolio, on: bool) -> Dict:
        self.w.emit(E.PHYSICAL_MODE_SET, {"portfolio_id": pf.id, "on": bool(on)}, portfolio_id=pf.id)
        return {"portfolio_id": pf.id, "physical_delivery": pf.physical_delivery}

    def place_spread(self, pf: Portfolio, near_id: str, far_id: str, side: str, quantity, limit_points=None, time_in_force: str = "DAY") -> Strategy:
        """Buy the spread = buy the near contract and sell the far one (and vice versa), one unit = one contract each leg."""
        from ..world import CommandError
        w = self.w
        near, far = w.securities.get(near_id), w.securities.get(far_id)
        if near is None or far is None or not near.is_future or not far.is_future:
            raise CommandError("both legs must be futures contracts")
        if near.underlying != far.underlying:
            raise CommandError("a calendar spread pairs two contract months of the same commodity")
        if near.id == far.id:
            raise CommandError("the legs must be different contract months")
        if near.expiry > far.expiry:
            near, far = far, near
        for leg in (near, far):
            if leg.expired or leg.expiry <= w.current_date.isoformat():
                raise CommandError(f"{leg.id} has expired")
        side = side.upper()
        if side not in ("BUY", "SELL"):
            raise CommandError("side must be BUY (buy near / sell far) or SELL (sell near / buy far)")
        n = qqty(quantity)
        if n <= 0:
            raise CommandError("quantity must be positive")
        job = w.careers.job_for(pf)
        if job and near.underlying_class not in job.allowed_classes:
            raise CommandError(f"{near.underlying_class} is outside the {job.title} mandate")
        legs = [{"security_id": near.id, "side": side, "ratio": 1, "quantity": n},
                {"security_id": far.id, "side": "SELL" if side == "BUY" else "BUY", "ratio": 1, "quantity": n}]
        # affordability: the paired position needs only the spread margin
        im = w.futures.initial_margin_per_contract(near)
        need = money(im * n * D(str(SPREAD_MARGIN_RATE)))
        why = w.prime.affordable(pf, need, None)
        if why:
            raise CommandError(f"spread not financeable: {why}")
        net_limit = None
        if limit_points is not None:
            net_limit = money(D(str(limit_points)) * D(str(near.multiplier)))
        sid = w.new_id("STR")
        w.emit(E.STRATEGY_ENTERED, {"portfolio_id": pf.id, "strategy_id": sid, "strategy_type": "FUTURES_CALENDAR", "underlying": near.underlying, "legs": legs,
                                    "quantity": n, "net_limit": net_limit, "time_in_force": time_in_force.upper()}, portfolio_id=pf.id)
        strategy = pf.strategies[sid]
        for leg in list(strategy.legs):
            w.trading.enter_order(pf.id, leg["security_id"], leg["side"], leg["quantity"], "MARKET", None, None, time_in_force, sid)
        return strategy

    def spread_analytics(self, pf: Portfolio, st: Strategy) -> Dict:
        w = self.w
        near, far = (w.securities[l["security_id"]] for l in st.legs)
        nb, fb = w.market.last_bar(near.id), w.market.last_bar(far.id)
        sign = 1 if st.legs[0]["side"] == "BUY" else -1
        diff_close = (nb.close - fb.close) * sign
        return {"kind": "FUTURES_CALENDAR", "near": near.id, "far": far.id, "near_close": nb.close, "far_close": fb.close,
                "differential_close": diff_close, "differential_per_unit_currency": money(diff_close * D(str(near.multiplier))),
                "net_at_open": w.options.strategy_net_at(pf, st, "OPEN"), "net_at_close": w.options.strategy_net_at(pf, st, "CLOSE"),
                "spread_margin_per_unit": money(w.futures.initial_margin_per_contract(near) * D(str(SPREAD_MARGIN_RATE))),
                "note": "a calendar spread earns the change in the differential; the clearing house margins the pair at 35% of one outright"}

    # ------------------------------------------------------------------ physical delivery at the last trade date
    def deliver(self, pf: Portfolio, sec: Security, pos, cause: Event) -> None:
        w = self.w
        code = sec.underlying
        spec = self.spec(code)
        settle = w.market.last_bar(sec.id).close
        contracts = abs(pos.quantity)
        units = qqty(contracts * D(str(sec.multiplier)))
        inv_id = inventory_id(code)
        if inv_id not in w.securities:
            w.emit(E.PHYSICAL_LISTED, {"security_id": inv_id, "code": code, "name": f"{spec.name} (physical, {spec.unit})", "unit": spec.unit, "group": spec.group,
                                       "price": w.market.commodities.state[code].spot, "adv": int(spec.adv * spec.multiplier * 0.1)}, cause_id=cause.id)
        inv = w.securities[inv_id]
        held = w.collateral.available_quantity(pf, inv_id) if inv_id in pf.positions else ZERO
        mult = D(str(sec.multiplier))
        parts: List = []           # (direction, contracts, units, note)
        if pos.quantity > 0:
            parts.append(("TAKE", contracts, units, f"took delivery of {units:,} {spec.unit} {spec.name} at {settle}"))
        else:
            make_c = min(contracts, qqty(held / mult) if mult else ZERO)
            if make_c > 0:
                parts.append(("MAKE", make_c, qqty(make_c * mult), f"delivered {qqty(make_c * mult):,} {spec.unit} {spec.name} from inventory at {settle}"))
            if contracts - make_c > 0:
                bi = contracts - make_c
                parts.append(("BUY_IN", bi, qqty(bi * mult), f"nothing to deliver on {bi} contract(s): bought in at {settle} plus a {BUY_IN_PENALTY:.0%} penalty"))
        first = True
        for direction, n_c, n_u, note in parts:
            amount = money(n_u * settle)
            penalty = money(amount * D(str(BUY_IN_PENALTY))) if direction == "BUY_IN" else ZERO
            ev = w.emit(E.PHYSICAL_DELIVERY, {"portfolio_id": pf.id, "security_id": sec.id, "inventory_id": inv_id, "code": code, "contracts": n_c if pos.quantity > 0 else -n_c,
                                              "units": n_u, "price": settle, "amount": amount, "direction": direction, "penalty": penalty, "currency": sec.currency, "note": note},
                        cause_id=cause.id, portfolio_id=pf.id)
            if first:   # the futures position ends at the settlement price (no further variation margin)
                w.trading.system_order(pf, sec, "SELL" if pos.quantity > 0 else "BUY", contracts, ev, f"delivery against {sec.id}", forced=False, fixed_price=settle,
                                       allow_short=True, commission_free=True)
                first = False
            if direction == "TAKE":
                w.trading.system_order(pf, inv, "BUY", n_u, ev, f"physical delivery from {sec.id}", forced=False, fixed_price=settle, commission_free=True)
            elif direction == "MAKE":
                w.trading.system_order(pf, inv, "SELL", n_u, ev, f"physical delivery into {sec.id}", forced=False, fixed_price=settle, commission_free=True)

    # ------------------------------------------------------------------ daily: storage on inventory
    def process_day(self, cause: Event, prev: date) -> None:
        w = self.w
        days = max(1, (w.current_date - prev).days)
        for pf in w.portfolios.values():
            for pos in list(pf.positions.values()):
                if pos.quantity <= 0:
                    continue
                sec = w.securities.get(pos.security_id)
                if sec is None or sec.asset_class != "PHYSICAL":
                    continue
                spec = self.spec(sec.underlying)
                mv = pos.quantity * w.market.last_bar(sec.id).close
                cost = money(mv * D(repr(spec.storage)) * days / 365)
                if cost > 0:
                    w.emit(E.STORAGE_CHARGED, {"portfolio_id": pf.id, "security_id": sec.id, "units": pos.quantity, "market_value": money(mv), "rate": spec.storage,
                                               "days": days, "amount": cost, "currency": pf.base_currency}, cause_id=cause.id, portfolio_id=pf.id)

    def inventory(self, pf: Portfolio) -> List[Dict]:
        w = self.w
        rows = []
        for pos in pf.positions.values():
            sec = w.securities.get(pos.security_id)
            if sec is None or sec.asset_class != "PHYSICAL" or pos.quantity == 0:
                continue
            spec = self.spec(sec.underlying)
            spot = w.market.commodities.state[sec.underlying].spot
            rows.append({"security_id": sec.id, "code": sec.underlying, "name": spec.name, "unit": spec.unit, "units": pos.quantity, "settled": pos.settled_quantity,
                         "spot": spot, "market_value": pos.market_value, "cost_basis": pos.cost_basis, "unrealized": pos.unrealized_pnl,
                         "storage_rate": spec.storage, "storage_per_day": money(pos.market_value * D(repr(spec.storage)) / 365),
                         "storage_paid": pf.storage_paid.get(sec.id, ZERO)})
        return rows

    # ------------------------------------------------------------------ handlers
    def _h_mode(self, ev: Event) -> None:
        pf = self.w.portfolios[ev.payload["portfolio_id"]]
        pf.physical_delivery = bool(ev.payload["on"])

    def _h_listed(self, ev: Event) -> None:
        w = self.w
        p = ev.payload
        if p["security_id"] in w.securities:
            return
        sec = Security(id=p["security_id"], name=p["name"], asset_class="PHYSICAL", market="PHYSICAL", currency="USD", country="US", sector=p["group"],
                       isin=f"XP{ev.seq:010d}", cusip=f"P{ev.seq:08d}", adv=int(p["adv"]), spread_bps=PHYSICAL_SPREAD_BPS, liquidity_tier="LARGE", underlying=p["code"],
                       underlying_class="PHYSICAL", multiplier=1.0, unit=p["unit"], beta=0.0, sigma_annual=0.0, lot_size=1)
        w.securities[sec.id] = sec
        w.market.list_security(sec, D(repr(float(p["price"]))), ev.sim_date)

    def _h_delivery(self, ev: Event) -> None:
        w = self.w
        p = ev.payload
        pf = w.portfolios[p["portfolio_id"]]
        pf.physical_deliveries.append({"date": ev.sim_date, "future": p["security_id"], "inventory": p["inventory_id"], "direction": p["direction"], "units": D(p["units"]),
                                       "price": D(p["price"]), "amount": D(p["amount"]), "penalty": D(p.get("penalty", 0)), "note": p["note"]})
        penalty = D(p.get("penalty", 0))
        if penalty > 0:
            ca = pf.cash_account(p["currency"])
            ca.balance -= penalty
            ca.base_value -= penalty
            w.record_cash_movement(pf, p["currency"], -penalty, "PENALTY", f"Buy-in penalty on {p['security_id']}: nothing to deliver", ev)
            w.post(pf.id, f"Buy-in penalty {penalty:,.2f} on {p['security_id']}", [dr("5700", penalty, p["security_id"], "delivery failure: buy-in penalty"),
                                                                                    cr(f"1010:{p['currency']}", penalty, p["security_id"], "penalty paid")], ev,
                   {"security_id": p["security_id"], "kind": "PENALTY"})

    def _h_storage(self, ev: Event) -> None:
        w = self.w
        p = ev.payload
        pf = w.portfolios[p["portfolio_id"]]
        amt = D(p["amount"])
        ca = pf.cash_account(p["currency"])
        ca.balance -= amt
        ca.base_value -= amt
        pf.storage_paid[p["security_id"]] = pf.storage_paid.get(p["security_id"], ZERO) + amt
        w.record_cash_movement(pf, p["currency"], -amt, "STORAGE", f"Storage and insurance on {D(p['units']):,} units of {p['security_id']} ({p['days']}d at {float(p['rate']):.1%} p.a.)", ev)
        w.post(pf.id, f"Storage {p['security_id']}: {amt:,.2f}", [dr("5320", amt, p["security_id"], "storage and insurance"), cr(f"1010:{p['currency']}", amt, p["security_id"], "storage paid")], ev,
               {"security_id": p["security_id"], "kind": "STORAGE"})
