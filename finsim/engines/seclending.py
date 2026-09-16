"""Securities lending: locates, loans, collateral, fees, recalls, buy-ins.

Shorting is the chain the desk actually runs:
  locate -> borrow (shares arrive in custody, collateral leaves) -> short sale
  (delivers the borrowed shares) -> proceeds settle -> daily fee accrual,
  daily collateral marks, rebate on cash collateral, manufactured dividends
  while short -> recall (2 business days) -> replacement borrow or cover ->
  return (shares leave custody, collateral comes back, fees settle).
Nothing here sets a negative quantity directly.
"""
from __future__ import annotations

import math
import random
from datetime import date
from decimal import Decimal
from typing import Dict, List, Optional

from ..domain.events import E, Event
from ..domain.models import Locate, Portfolio, Security, SecurityLoan
from ..engines.ledger import dr, cr
from ..money import D, money, qty as qqty, ZERO

COLLATERALIZATION = 1.02
RECALL_DAYS = 2
BUY_IN_PENALTY = 0.01
HOLD_DAYS = 5


class SecLendingEngine:
    def __init__(self, world):
        self.w = world

    def register(self) -> None:
        w = self.w
        w.on(E.LOCATE_REQUESTED, lambda world, ev: self._h_locate(ev))
        w.on(E.SECURITY_LOAN_OPENED, lambda world, ev: self._h_opened(ev))
        w.on(E.SECURITY_LOAN_RERATED, lambda world, ev: self._h_rerated(ev))
        w.on(E.SECURITY_LOAN_MARKED, lambda world, ev: self._h_marked(ev))
        w.on(E.BORROW_FEE_ACCRUED, lambda world, ev: self._h_fee_accrued(ev))
        w.on(E.BORROW_FEE_SETTLED, lambda world, ev: self._h_fee_settled(ev))
        w.on(E.SECURITY_LOAN_RECALLED, lambda world, ev: self._h_recalled(ev))
        w.on(E.SECURITY_LOAN_RETURNED, lambda world, ev: self._h_returned(ev))
        w.on(E.SECURITY_LOAN_BUY_IN, lambda world, ev: self._h_buy_in(ev))

    # ------------------------------------------------------------------ market view
    def market_row(self, sec: Security) -> Dict:
        w = self.w
        st = w.market.lending.state.get(sec.id)
        if st is None:
            return {}
        mine = sum((l.quantity for pf in w.portfolios.values() for l in pf.loans.values() if l.status == "OPEN" and l.security_id == sec.id), ZERO)
        return {"security_id": sec.id, "name": sec.name, "supply": st.supply, "on_loan_other": st.on_loan_other, "utilization": st.utilization,
                "rate": st.rate, "category": st.category, "available": w.market.lending.available(sec.id, int(mine)), "special_days": st.special_days,
                "history": w.market.lending.history.get(sec.id, [])[-60:]}

    def player_on_loan(self) -> Dict[str, int]:
        out: Dict[str, int] = {}
        for pf in self.w.portfolios.values():
            for l in pf.loans.values():
                if l.status == "OPEN":
                    out[l.security_id] = out.get(l.security_id, 0) + int(l.quantity)
        return out

    # ------------------------------------------------------------------ commands
    def request_locate(self, pf: Portfolio, sec: Security, quantity) -> Locate:
        from ..world import CommandError
        w = self.w
        q = qqty(quantity)
        if q <= 0:
            raise CommandError("locate quantity must be positive")
        st = w.market.lending.state.get(sec.id)
        if st is None or sec.is_option or sec.is_future:
            raise CommandError(f"{sec.id} is not lendable (no lending market for this instrument)")
        rng = random.Random(f"{w.seed}|locate|{sec.id}|{w.current_date.isoformat()}|{len(pf.locates)}")
        market_avail = w.market.lending.available(sec.id, self.player_on_loan().get(sec.id, 0))
        factor = 1.0 if st.category == "GC" else rng.uniform(0.55, 1.0)
        avail = min(q, qqty(D(market_avail) * D(str(factor))))
        lid = w.new_id("LOC")
        w.emit(E.LOCATE_REQUESTED, {"portfolio_id": pf.id, "locate_id": lid, "security_id": sec.id, "requested": q, "available": avail, "rate": st.rate,
                                    "lender": w.market.lending.lender_for(sec.id, w.current_date), "valid_through": w.calendar.next_business_day(w.current_date).isoformat(),
                                    "category": st.category, "utilization": st.utilization}, portfolio_id=pf.id)
        return pf.locates[lid]

    def borrow(self, pf: Portfolio, locate_id: str, quantity, collateral_type: str = "CASH") -> SecurityLoan:
        from ..world import CommandError
        w = self.w
        loc = pf.locates.get(locate_id)
        if loc is None:
            raise CommandError(f"unknown locate {locate_id}")
        q = qqty(quantity)
        today = w.current_date.isoformat()
        if loc.status != "OPEN" or loc.valid_through < today:
            raise CommandError(f"locate {locate_id} is {loc.status if loc.status != 'OPEN' else 'expired'}; request a new locate")
        if q <= 0 or q > loc.available - loc.used:
            raise CommandError(f"locate {locate_id} covers {loc.available - loc.used:,} shares, requested {q:,}")
        sec = w.securities[loc.security_id]
        mv = money(q * w.market.last_bar(sec.id).close)
        required = money(mv * D(str(COLLATERALIZATION)))
        coll_type = collateral_type.upper()
        loan_id = w.new_id("LN")
        payload = {"portfolio_id": pf.id, "loan_id": loan_id, "locate_id": loc.id, "security_id": sec.id, "lender": loc.lender, "borrower": pf.custody_account,
                   "quantity": q, "borrow_rate": loc.rate, "rebate_rate": self.rebate_rate(), "collateral_type": coll_type, "collateralization": COLLATERALIZATION,
                   "market_value": mv, "required_collateral": required, "hold_until": w.calendar.add_business_days(w.current_date, HOLD_DAYS).isoformat()}
        if coll_type == "CASH":
            why = w.prime.affordable(pf, required, None)
            if why:
                raise CommandError(f"cash collateral of {required:,.2f} is not financeable: {why}; borrow less or use Treasury collateral")
        else:
            csec = w.securities.get(coll_type)
            if csec is None or csec.asset_class != "GOVT_BOND":
                raise CommandError("collateral must be CASH or a Treasury security id")
            h = w.collateral.haircut(csec, "SECLOAN")
            unit_val = w.collateral.collateral_value(csec, D(1000), "SECLOAN")
            face = qqty(math.ceil(float(required / unit_val))) * 1000 if unit_val > 0 else ZERO
            if w.collateral.available_quantity(pf, csec.id) < face:
                raise CommandError(f"need {face:,} face of {csec.id} unencumbered as collateral; only {w.collateral.available_quantity(pf, csec.id):,} available")
            payload["collateral_face"] = face
        ev = w.emit(E.SECURITY_LOAN_OPENED, payload, portfolio_id=pf.id)
        return pf.loans[loan_id]

    def return_loan(self, pf: Portfolio, loan_id: str, quantity=None, cause: Optional[Event] = None, note: str = "returned by borrower") -> SecurityLoan:
        from ..world import CommandError
        w = self.w
        loan = pf.loans.get(loan_id)
        if loan is None or loan.status != "OPEN":
            raise CommandError(f"loan {loan_id} is not open")
        q = qqty(quantity) if quantity is not None else loan.quantity
        if q <= 0 or q > loan.quantity:
            raise CommandError(f"return quantity must be between 1 and {loan.quantity:,}")
        avail = w.collateral.available_quantity(pf, loan.security_id)
        if avail < q:
            raise CommandError(f"only {avail:,} {loan.security_id} in custody available to return (short position must be covered first, or borrow a replacement)")
        w.emit(E.SECURITY_LOAN_RETURNED, {"portfolio_id": pf.id, "loan_id": loan_id, "security_id": loan.security_id, "quantity": q, "note": note,
                                          "full": q == loan.quantity}, cause_id=cause.id if cause else None, portfolio_id=pf.id)
        return loan

    def rebate_rate(self) -> float:
        return round(self.w.market.curve().policy_rate - 0.0025, 5)

    # ------------------------------------------------------------------ daily
    def process_day(self, cause: Event, prev_date: date) -> None:
        w = self.w
        today = w.current_date
        days = max(1, (today - prev_date).days)
        for pf in w.portfolios.values():
            for loan in list(pf.loans.values()):
                if loan.status != "OPEN":
                    continue
                sec = w.securities[loan.security_id]
                st = w.market.lending.state.get(sec.id)
                # re-rate to the market
                if st and abs(st.rate - loan.borrow_rate) > 1e-6:
                    w.emit(E.SECURITY_LOAN_RERATED, {"portfolio_id": pf.id, "loan_id": loan.id, "old_rate": loan.borrow_rate, "new_rate": st.rate,
                                                     "rebate_rate": self.rebate_rate(), "category": st.category}, cause_id=cause.id, portfolio_id=pf.id)
                # accrue fee on market value and rebate on cash collateral
                mv = money(loan.quantity * w.market.last_bar(sec.id).close)
                fee = money(mv * D(str(loan.borrow_rate)) * days / 360)
                rebate = money(loan.collateral_amount * D(str(loan.rebate_rate)) * days / 360) if loan.collateral_type == "CASH" else ZERO
                if fee or rebate:
                    w.emit(E.BORROW_FEE_ACCRUED, {"portfolio_id": pf.id, "loan_id": loan.id, "security_id": sec.id, "fee": fee, "rebate": rebate, "days": days,
                                                  "market_value": mv, "rate": loan.borrow_rate}, cause_id=cause.id, portfolio_id=pf.id)
                # mark collateral
                self._mark(pf, loan, mv, cause)
                # recalls
                self._maybe_recall(pf, loan, st, cause)
            # overdue recalls -> buy-in
            for loan in list(pf.loans.values()):
                if loan.status == "OPEN" and loan.recall_status == "RECALLED" and loan.recall_due and loan.recall_due <= today.isoformat() and loan.recall_quantity > 0:
                    self._buy_in(pf, loan, cause)
            self._auto_return(pf, cause)
            if today.month != prev_date.month:
                self._settle_fees(pf, cause)

    def _mark(self, pf: Portfolio, loan: SecurityLoan, mv: Decimal, cause: Event) -> None:
        w = self.w
        required = money(mv * D(str(loan.collateralization)))
        if loan.collateral_type == "CASH":
            delta = required - loan.collateral_amount
            if abs(delta) >= max(D(1000), money(required * D("0.002"))):
                w.emit(E.SECURITY_LOAN_MARKED, {"portfolio_id": pf.id, "loan_id": loan.id, "market_value": mv, "required": required, "delta": delta,
                                                "collateral_type": "CASH"}, cause_id=cause.id, portfolio_id=pf.id)
            elif loan.market_value != mv:
                w.emit(E.SECURITY_LOAN_MARKED, {"portfolio_id": pf.id, "loan_id": loan.id, "market_value": mv, "required": required, "delta": ZERO,
                                                "collateral_type": "CASH"}, cause_id=cause.id, portfolio_id=pf.id)
        else:
            csec = w.securities[loan.collateral_type]
            pledged = sum((p.quantity for p in pf.pledges if p.reference == loan.id), ZERO)
            value = w.collateral.collateral_value(csec, pledged, "SECLOAN")
            unit_val = w.collateral.collateral_value(csec, D(1000), "SECLOAN")
            delta_face = ZERO
            if value < required and unit_val > 0:
                delta_face = qqty(math.ceil(float((required - value) / unit_val))) * 1000
            elif value > required * D("1.03") and unit_val > 0:
                delta_face = -(qqty(math.floor(float((value - required) / unit_val))) * 1000)
            w.emit(E.SECURITY_LOAN_MARKED, {"portfolio_id": pf.id, "loan_id": loan.id, "market_value": mv, "required": required, "delta": delta_face,
                                            "collateral_type": loan.collateral_type, "collateral_value": value}, cause_id=cause.id, portfolio_id=pf.id)

    def _maybe_recall(self, pf: Portfolio, loan: SecurityLoan, st, cause: Event) -> None:
        w = self.w
        if loan.recall_status == "RECALLED" or st is None:
            return
        rng = random.Random(f"{w.seed}|recall|{loan.id}|{w.current_date.isoformat()}")
        p = {"GC": 0.003, "HTB": 0.015, "SPECIAL": 0.05}[st.category] * (0.5 + st.utilization)
        if rng.random() < p:
            frac = rng.choice([0.3, 0.5, 0.5, 1.0])
            q = max(D(1), qqty(loan.quantity * D(str(frac))))
            w.emit(E.SECURITY_LOAN_RECALLED, {"portfolio_id": pf.id, "loan_id": loan.id, "security_id": loan.security_id, "quantity": q,
                                              "due": w.calendar.add_business_days(w.current_date, RECALL_DAYS).isoformat(), "lender": loan.lender,
                                              "reason": "lender recall" + (" during a special" if st.category == "SPECIAL" else "")}, cause_id=cause.id, portfolio_id=pf.id)

    def _buy_in(self, pf: Portfolio, loan: SecurityLoan, cause: Event) -> None:
        """Lender buys the shares in at the close and charges us; our short is covered by the buy-in trade."""
        w = self.w
        sec = w.securities[loan.security_id]
        q = loan.recall_quantity
        avail = w.collateral.available_quantity(pf, sec.id)
        if avail >= q:
            self.return_loan(pf, loan.id, q, cause, note="recall satisfied from custody")
            return
        need = q - max(ZERO, avail)
        if avail > 0:
            self.return_loan(pf, loan.id, avail, cause, note="partial recall satisfied from custody")
        ev = w.emit(E.SECURITY_LOAN_BUY_IN, {"portfolio_id": pf.id, "loan_id": loan.id, "security_id": sec.id, "quantity": need, "lender": loan.lender,
                                             "penalty": money(need * w.market.last_bar(sec.id).close * D(str(BUY_IN_PENALTY))),
                                             "note": f"recall not met by {loan.recall_due}: lender executed a buy-in"}, cause_id=cause.id, portfolio_id=pf.id)
        w.trading.system_order(pf, sec, "BUY", need, ev, f"lender buy-in for recall on {loan.id}", forced=True)

    def _auto_return(self, pf: Portfolio, cause: Event) -> None:
        """Return borrowed shares no longer needed (short covered) once they are back in custody."""
        w = self.w
        today = w.current_date.isoformat()
        by_sec: Dict[str, List[SecurityLoan]] = {}
        for l in pf.loans.values():
            if l.status == "OPEN":
                by_sec.setdefault(l.security_id, []).append(l)
        for sid, loans in by_sec.items():
            pos = pf.positions.get(sid)
            if pos is None:
                continue
            if any(o.status in ("WORKING", "PARTIALLY_FILLED") and o.security_id == sid and o.side == "SELL" for o in pf.orders.values()):
                continue
            needed = max(ZERO, -pos.quantity)
            excess = pos.borrowed_quantity - needed
            returnable = w.collateral.available_quantity(pf, sid)
            q = min(excess, returnable)
            if q <= 0:
                continue
            urgency = {"BOUGHT_IN": 0, "RECALLED": 1}
            loans.sort(key=lambda l: (urgency.get(l.recall_status, 2), l.loan_date))
            for l in loans:
                if q <= 0:
                    break
                if l.hold_until > today and l.recall_status not in ("RECALLED", "BOUGHT_IN"):
                    continue
                take = min(q, l.quantity)
                self.return_loan(pf, l.id, take, cause, note="excess borrow returned automatically")
                q -= take

    def _settle_fees(self, pf: Portfolio, cause: Event) -> None:
        for loan in list(pf.loans.values()):
            if loan.status == "OPEN" and (loan.accrued_fee or loan.accrued_rebate):
                self.w.emit(E.BORROW_FEE_SETTLED, {"portfolio_id": pf.id, "loan_id": loan.id, "security_id": loan.security_id, "fee": loan.accrued_fee,
                                                   "rebate": loan.accrued_rebate}, cause_id=cause.id, portfolio_id=pf.id)

    # ------------------------------------------------------------------ book views
    def short_book(self, pf: Portfolio) -> Dict:
        w = self.w
        rows = []
        for pos in pf.positions.values():
            if pos.is_option or pos.is_future:
                continue          # written options and short futures are cleared, not borrowed
            if pos.quantity < 0 or pos.borrowed_quantity > 0:
                sec = w.securities[pos.security_id]
                st = w.market.lending.state.get(sec.id)
                loans = [l for l in pf.loans.values() if l.security_id == sec.id and l.status == "OPEN"]
                rows.append({"security_id": sec.id, "short_quantity": max(ZERO, -pos.quantity), "borrowed": pos.borrowed_quantity, "market_value": pos.market_value,
                             "rate": st.rate if st else None, "category": st.category if st else None, "accrued_fee": sum((l.accrued_fee for l in loans), ZERO),
                             "collateral": sum((l.collateral_amount if l.collateral_type == "CASH" else l.collateral_value for l in loans), ZERO),
                             "recalled": sum((l.recall_quantity for l in loans if l.recall_status == "RECALLED"), ZERO), "loans": [l.id for l in loans]})
        return {"rows": rows, "short_mv": sum((r["market_value"] for r in rows if r["short_quantity"] > 0), ZERO),
                "htb": sum(1 for r in rows if r["category"] in ("HTB", "SPECIAL") and r["short_quantity"] > 0),
                "recalls": sum(1 for r in rows if r["recalled"] > 0)}

    # ------------------------------------------------------------------ handlers
    def _h_locate(self, ev: Event) -> None:
        p = ev.payload
        pf = self.w.portfolios[p["portfolio_id"]]
        avail = D(p["available"])
        pf.locates[p["locate_id"]] = Locate(id=p["locate_id"], portfolio_id=pf.id, security_id=p["security_id"], requested=D(p["requested"]), available=avail,
                                            rate=float(p["rate"]), lender=p["lender"], date=ev.sim_date, valid_through=p["valid_through"],
                                            status="OPEN" if avail > 0 else "NONE")

    def _h_opened(self, ev: Event) -> None:
        w = self.w
        p = ev.payload
        pf = w.portfolios[p["portfolio_id"]]
        q = D(p["quantity"])
        loan = SecurityLoan(id=p["loan_id"], portfolio_id=pf.id, security_id=p["security_id"], lender=p["lender"], borrower=p["borrower"], quantity=q,
                            loan_date=ev.sim_date, borrow_rate=float(p["borrow_rate"]), rebate_rate=float(p["rebate_rate"]), collateral_type=p["collateral_type"],
                            collateralization=float(p["collateralization"]), market_value=D(p["market_value"]), hold_until=p["hold_until"], original_quantity=q)
        loan.rate_history.append({"date": ev.sim_date, "rate": loan.borrow_rate})
        pf.loans[loan.id] = loan
        loc = pf.locates[p["locate_id"]]
        loc.used += q
        if loc.used >= loc.available:
            loc.status = "USED"
        pos = pf.position(loan.security_id)
        pos.settled_quantity += q
        pos.borrowed_quantity += q
        w.record_custody_movement(pf, loan.security_id, q, "BORROW_RECEIVE", f"borrowed from {loan.lender} under {loan.id}", ev)
        required = D(p["required_collateral"])
        if loan.collateral_type == "CASH":
            w.collateral.move_cash(pf, required, loan.id, "SECLOAN", ev, note=f"102% of {loan.security_id} loan value")
            loan.collateral_amount = required
            loan.collateral_value = required
        else:
            face = D(p["collateral_face"])
            w.collateral.pledge(pf, w.securities[loan.collateral_type], face, "SECLOAN", loan.id, ev)
            loan.collateral_amount = face
            loan.collateral_value = w.collateral.collateral_value(w.securities[loan.collateral_type], face, "SECLOAN")

    def _h_rerated(self, ev: Event) -> None:
        p = ev.payload
        loan = self.w.portfolios[p["portfolio_id"]].loans[p["loan_id"]]
        loan.borrow_rate = float(p["new_rate"])
        loan.rebate_rate = float(p["rebate_rate"])
        loan.rate_history.append({"date": ev.sim_date, "rate": loan.borrow_rate})

    def _h_marked(self, ev: Event) -> None:
        w = self.w
        p = ev.payload
        pf = w.portfolios[p["portfolio_id"]]
        loan = pf.loans[p["loan_id"]]
        loan.market_value = D(p["market_value"])
        delta = D(p["delta"])
        if p["collateral_type"] == "CASH":
            if delta != 0:
                w.collateral.move_cash(pf, delta, loan.id, "SECLOAN", ev, note="daily collateral mark")
                loan.collateral_amount += delta
            loan.collateral_value = loan.collateral_amount
        else:
            csec = w.securities[loan.collateral_type]
            if delta > 0:
                avail = w.collateral.available_quantity(pf, csec.id)
                if avail >= delta:
                    w.collateral.pledge(pf, csec, delta, "SECLOAN", loan.id, ev)
                    loan.collateral_amount += delta
                else:
                    # not enough Treasuries free: top up the shortfall in cash
                    short = D(p["required"]) - D(p.get("collateral_value", 0))
                    w.collateral.move_cash(pf, short, loan.id, "SECLOAN", ev, note="cash top-up: Treasury collateral exhausted")
            elif delta < 0:
                w.collateral.release(pf, csec.id, -delta, loan.id, ev, note="collateral mark release")
                loan.collateral_amount += delta
            loan.collateral_value = w.collateral.collateral_value(csec, loan.collateral_amount, "SECLOAN") + pf.cash_collateral.get(loan.id, ZERO)

    def _h_fee_accrued(self, ev: Event) -> None:
        w = self.w
        p = ev.payload
        pf = w.portfolios[p["portfolio_id"]]
        loan = pf.loans[p["loan_id"]]
        fee, rebate = D(p["fee"]), D(p["rebate"])
        loan.accrued_fee += fee
        loan.accrued_rebate += rebate
        pos = pf.position(p["security_id"])
        pos.borrow_fees += fee
        lines = []
        if fee:
            lines += [dr("5300", fee, p["security_id"], f"borrow fee {p['rate']*100:.2f}% on {D(p['market_value']):,.0f}"), cr("2310", fee, p["security_id"], "accrued fee payable")]
        if rebate:
            lines += [dr("1410", rebate, p["security_id"], "rebate on cash collateral"), cr("4350", rebate, p["security_id"], "rebate income")]
        w.post(pf.id, f"Borrow fee accrual {loan.id} {p['security_id']}: fee {fee:,.2f}, rebate {rebate:,.2f}", lines, ev, {"loan_id": loan.id, "security_id": p["security_id"], "kind": "BORROW_FEE"})

    def _h_fee_settled(self, ev: Event) -> None:
        w = self.w
        p = ev.payload
        pf = w.portfolios[p["portfolio_id"]]
        loan = pf.loans[p["loan_id"]]
        fee, rebate = D(p["fee"]), D(p["rebate"])
        ccy = pf.base_currency
        ca = pf.cash_account(ccy)
        net = rebate - fee
        ca.balance += net
        ca.base_value += net
        loan.accrued_fee -= fee
        loan.accrued_rebate -= rebate
        loan.fees_paid += fee
        if net:
            w.record_cash_movement(pf, ccy, net, "BORROW_FEE", f"Borrow fee {fee:,.2f} paid / rebate {rebate:,.2f} received on {loan.id}", ev)
        lines = []
        if fee:
            lines += [dr("2310", fee, p["security_id"], "fees paid to lender"), cr(f"1010:{ccy}", fee, p["security_id"], "cash")]
        if rebate:
            lines += [dr(f"1010:{ccy}", rebate, p["security_id"], "cash"), cr("1410", rebate, p["security_id"], "rebate received")]
        w.post(pf.id, f"Borrow fee settlement {loan.id}: fee {fee:,.2f}, rebate {rebate:,.2f}", lines, ev, {"loan_id": loan.id, "security_id": p["security_id"]})

    def _h_recalled(self, ev: Event) -> None:
        p = ev.payload
        loan = self.w.portfolios[p["portfolio_id"]].loans[p["loan_id"]]
        loan.recall_quantity = D(p["quantity"])
        loan.recall_due = p["due"]
        loan.recall_status = "RECALLED"

    def _h_returned(self, ev: Event) -> None:
        w = self.w
        p = ev.payload
        pf = w.portfolios[p["portfolio_id"]]
        loan = pf.loans[p["loan_id"]]
        q = D(p["quantity"])
        pos = pf.position(loan.security_id)
        pos.settled_quantity -= q
        pos.borrowed_quantity -= q
        w.record_custody_movement(pf, loan.security_id, -q, "BORROW_RETURN", f"returned to {loan.lender} under {loan.id}: {p.get('note', '')}", ev)
        frac = q / loan.quantity if loan.quantity else D(1)
        loan.quantity -= q
        loan.returned_quantity += q
        if loan.recall_status == "RECALLED":
            loan.recall_quantity = max(ZERO, loan.recall_quantity - q)
            if loan.recall_quantity == 0:
                loan.recall_status = "SATISFIED"
                loan.recall_due = None
        # collateral back
        if loan.collateral_type == "CASH":
            back = money(loan.collateral_amount * frac) if loan.quantity > 0 else loan.collateral_amount
            w.collateral.move_cash(pf, -back, loan.id, "SECLOAN", ev, note="collateral returned on loan return")
            loan.collateral_amount -= back
            loan.collateral_value = loan.collateral_amount
        else:
            face = qqty(loan.collateral_amount * frac / 1000) * 1000 if loan.quantity > 0 else loan.collateral_amount
            w.collateral.release(pf, loan.collateral_type, face, loan.id, ev, note="collateral released on loan return")
            loan.collateral_amount -= face
            cash_part = pf.cash_collateral.get(loan.id, ZERO)
            if loan.quantity == 0 and cash_part:
                w.collateral.move_cash(pf, -cash_part, loan.id, "SECLOAN", ev, note="cash top-up returned")
            loan.collateral_value = w.collateral.collateral_value(w.securities[loan.collateral_type], loan.collateral_amount, "SECLOAN") if loan.quantity > 0 else ZERO
        if loan.quantity == 0:
            loan.status = "RETURNED"
            if loan.accrued_fee or loan.accrued_rebate:
                w.derive(E.BORROW_FEE_SETTLED, {"portfolio_id": pf.id, "loan_id": loan.id, "security_id": loan.security_id, "fee": loan.accrued_fee,
                                                "rebate": loan.accrued_rebate}, ev, portfolio_id=pf.id)

    def _h_buy_in(self, ev: Event) -> None:
        w = self.w
        p = ev.payload
        pf = w.portfolios[p["portfolio_id"]]
        loan = pf.loans[p["loan_id"]]
        loan.recall_status = "BOUGHT_IN"
        pos = pf.position(p["security_id"])
        pos.buy_in_count += 1
        pen = D(p["penalty"])
        ccy = pf.base_currency
        ca = pf.cash_account(ccy)
        ca.balance -= pen
        ca.base_value -= pen
        w.record_cash_movement(pf, ccy, -pen, "BUY_IN_PENALTY", f"Buy-in penalty {loan.id}", ev)
        w.post(pf.id, f"Buy-in penalty {loan.id} {p['security_id']}: {pen:,.2f}", [dr("5700", pen, p["security_id"], "buy-in penalty"), cr(f"1010:{ccy}", pen, p["security_id"], "cash")],
               ev, {"loan_id": loan.id, "security_id": p["security_id"]})


# ====================================================================== lend side (securities-lending trader job)
class LE:
    SECURITY_LENT = "SECURITY_LENT"
    LEND_MARKED = "LEND_MARKED"
    LEND_FEE_ACCRUED = "LEND_FEE_ACCRUED"
    LEND_FEE_SETTLED = "LEND_FEE_SETTLED"
    LEND_RECALLED = "LEND_RECALLED"
    LEND_RETURNED = "LEND_RETURNED"


LEND_COLLATERAL = 1.02
MIN_LEND_SESSIONS = 5              # borrowers keep a loan at least this long before a random early return
EARLY_RETURN_P = 0.015             # daily probability of an early return after that
BORROWERS = ["Kestrel Macro Fund", "Vantage Multi-Strategy", "Harbor Securities Swaps", "Atlas Capital Markets", "Meridian Bank Derivatives"]


class LendDeskEngine:
    """The player as lender: lend inventory out against cash collateral, earn the fee, pay the rebate, recall when needed."""

    def __init__(self, world):
        self.w = world

    def register(self) -> None:
        w = self.w
        w.on(LE.SECURITY_LENT, lambda world, ev: self._h_lent(ev))
        w.on(LE.LEND_MARKED, lambda world, ev: self._h_marked(ev))
        w.on(LE.LEND_FEE_ACCRUED, lambda world, ev: self._h_accrued(ev))
        w.on(LE.LEND_FEE_SETTLED, lambda world, ev: self._h_settled(ev))
        w.on(LE.LEND_RECALLED, lambda world, ev: self._h_recalled(ev))
        w.on(LE.LEND_RETURNED, lambda world, ev: self._h_returned(ev))

    # ------------------------------------------------------------------ commands
    def lend(self, pf: Portfolio, sec: Security, quantity) -> Dict:
        from ..world import CommandError
        w = self.w
        q = qqty(quantity)
        if q <= 0:
            raise CommandError("quantity must be positive")
        st = w.market.lending.state.get(sec.id)
        if st is None or sec.is_option or sec.is_future:
            raise CommandError(f"{sec.id} has no lending market")
        avail = w.collateral.available_quantity(pf, sec.id)
        if avail < q:
            raise CommandError(f"only {avail:,} {sec.id} unencumbered in custody (settled, not pledged, not lent, not reserved)")
        # demand: borrowers take what the market's utilisation implies is wanted; specials are taken in full
        demand = int(st.supply * min(1.0, st.utilization + 0.15)) if st.category == "GC" else int(st.supply)
        already = sum((D(l["quantity"]) for l in pf.lends.values() if l["security_id"] == sec.id and l["status"] == "OPEN"), ZERO)
        take = min(q, max(ZERO, D(demand) - already))
        if take <= 0:
            raise CommandError(f"no borrower demand for more {sec.id} today (market utilisation {st.utilization:.0%})")
        mv = w.collateral.market_value(sec, take)
        coll = money(mv * D(str(LEND_COLLATERAL)))
        rng = random.Random(f"{w.seed}|lendout|{pf.id}|{sec.id}|{w.current_date.isoformat()}")
        lid = w.new_id("LD")
        w.emit(LE.SECURITY_LENT, {"portfolio_id": pf.id, "lend_id": lid, "security_id": sec.id, "quantity": take, "borrower": rng.choice(BORROWERS), "rate": st.rate,
                                  "rebate_rate": max(0.0, w.market.curve().policy_rate - 0.0025), "market_value": mv, "collateral": coll, "category": st.category},
               portfolio_id=pf.id)
        return pf.lends[lid]

    def recall(self, pf: Portfolio, lend_id: str, quantity=None) -> Dict:
        from ..world import CommandError
        w = self.w
        l = pf.lends.get(lend_id)
        if l is None or l["status"] != "OPEN":
            raise CommandError("no open loan with that id")
        q = qqty(quantity) if quantity is not None else D(l["quantity"])
        if q <= 0 or q > D(l["quantity"]):
            raise CommandError(f"recall quantity must be between 1 and {D(l['quantity']):,}")
        due = w.calendar.add_business_days(w.current_date, 2).isoformat()
        w.emit(LE.LEND_RECALLED, {"portfolio_id": pf.id, "lend_id": lend_id, "quantity": q, "due": due}, portfolio_id=pf.id)
        return pf.lends[lend_id]

    # ------------------------------------------------------------------ daily
    def process_day(self, cause: Event, prev: date) -> None:
        w = self.w
        today = w.current_date
        days = max(1, (today - prev).days)
        for pf in w.portfolios.values():
            for l in list(pf.lends.values()):
                if l["status"] != "OPEN":
                    continue
                sec = w.securities[l["security_id"]]
                q = D(l["quantity"])
                # returns: recalled loans come back on the due date; borrowers also return early sometimes
                rng = random.Random(f"{w.seed}|lendret|{l['id']}|{today.isoformat()}")
                seasoned = w.calendar.business_days_between(date.fromisoformat(l["opened"]), today) >= MIN_LEND_SESSIONS
                if (l.get("recall_due") and l["recall_due"] <= today.isoformat()) or (seasoned and rng.random() < EARLY_RETURN_P):
                    rq = D(l.get("recall_quantity", 0)) if l.get("recall_due") and l["recall_due"] <= today.isoformat() else q
                    w.emit(LE.LEND_RETURNED, {"portfolio_id": pf.id, "lend_id": l["id"], "quantity": rq, "note": "recalled shares returned" if l.get("recall_due") else "borrower returned early"},
                           cause_id=cause.id, portfolio_id=pf.id)
                    if D(pf.lends[l["id"]]["quantity"]) <= 0:
                        # a fully returned loan settles its accrued fee and rebate on the spot
                        if D(l.get("accrued_fee", 0)) or D(l.get("accrued_rebate", 0)):
                            w.emit(LE.LEND_FEE_SETTLED, {"portfolio_id": pf.id, "lend_id": l["id"], "fee": D(l.get("accrued_fee", 0)), "rebate": D(l.get("accrued_rebate", 0)),
                                                         "currency": pf.base_currency}, cause_id=cause.id, portfolio_id=pf.id)
                        continue
                    q = D(pf.lends[l["id"]]["quantity"])
                # re-rate to the market and mark collateral
                st = w.market.lending.state.get(sec.id)
                rate = st.rate if st else l["rate"]
                mv = w.collateral.market_value(sec, q)
                coll = money(mv * D(str(LEND_COLLATERAL)))
                w.emit(LE.LEND_MARKED, {"portfolio_id": pf.id, "lend_id": l["id"], "rate": rate, "rebate_rate": max(0.0, w.market.curve().policy_rate - 0.0025), "market_value": mv,
                                        "collateral": coll, "delta": coll - D(l["collateral"])}, cause_id=cause.id, portfolio_id=pf.id)
                fee = money(mv * D(repr(rate)) * days / 360)
                rebate = money(D(l["collateral"]) * D(repr(pf.lends[l["id"]]["rebate_rate"])) * days / 360)
                if fee or rebate:
                    w.emit(LE.LEND_FEE_ACCRUED, {"portfolio_id": pf.id, "lend_id": l["id"], "fee": fee, "rebate": rebate, "days": days}, cause_id=cause.id, portfolio_id=pf.id)
            if today.month != prev.month:
                for l in list(pf.lends.values()):
                    if D(l.get("accrued_fee", 0)) or D(l.get("accrued_rebate", 0)):
                        w.emit(LE.LEND_FEE_SETTLED, {"portfolio_id": pf.id, "lend_id": l["id"], "fee": D(l.get("accrued_fee", 0)), "rebate": D(l.get("accrued_rebate", 0)),
                                                     "currency": pf.base_currency}, cause_id=cause.id, portfolio_id=pf.id)

    def book(self, pf: Portfolio) -> Dict:
        w = self.w
        rows = list(pf.lends.values())
        open_rows = [l for l in rows if l["status"] == "OPEN"]
        inventory = []
        for pos in pf.positions.values():
            sec = w.securities[pos.security_id]
            if pos.quantity <= 0 or sec.is_future or sec.is_option:
                continue
            st = w.market.lending.state.get(sec.id)
            lent = sum((D(l["quantity"]) for l in open_rows if l["security_id"] == sec.id), ZERO)
            inventory.append({"security_id": sec.id, "held": pos.quantity, "lent": lent, "available": w.collateral.available_quantity(pf, sec.id),
                              "rate": st.rate if st else None, "category": st.category if st else None, "utilization": st.utilization if st else None,
                              "annual_fee_if_lent": money(w.collateral.market_value(sec, pos.quantity) * D(repr(st.rate))) if st else ZERO})
        return {"loans": rows, "inventory": inventory, "on_loan_mv": sum((D(l["market_value"]) for l in open_rows), ZERO),
                "collateral_held": sum((D(l["collateral"]) for l in open_rows), ZERO), "accrued_fees": sum((D(l.get("accrued_fee", 0)) for l in open_rows), ZERO),
                "accrued_rebate": sum((D(l.get("accrued_rebate", 0)) for l in open_rows), ZERO), "fees_earned": pf.lend_fees_earned,
                "utilisation_of_inventory": float(sum((D(l["quantity"]) for l in open_rows), ZERO) / sum((i["held"] for i in inventory), ZERO)) if inventory and sum((i["held"] for i in inventory), ZERO) else 0.0}

    # ------------------------------------------------------------------ handlers
    def _h_lent(self, ev: Event) -> None:
        w = self.w
        p = ev.payload
        pf = w.portfolios[p["portfolio_id"]]
        q, coll = D(p["quantity"]), D(p["collateral"])
        pf.lends[p["lend_id"]] = {"id": p["lend_id"], "security_id": p["security_id"], "quantity": q, "original_quantity": q, "borrower": p["borrower"], "rate": float(p["rate"]),
                                  "rebate_rate": float(p["rebate_rate"]), "market_value": D(p["market_value"]), "collateral": coll, "opened": ev.sim_date, "status": "OPEN",
                                  "accrued_fee": ZERO, "accrued_rebate": ZERO, "fees_earned": ZERO, "recall_due": None, "recall_quantity": ZERO, "category": p["category"], "history": []}
        w.collateral.pledge(pf, w.securities[p["security_id"]], q, "LENT", p["lend_id"], ev)
        ca = pf.cash_account(pf.base_currency)
        ca.balance += coll
        ca.base_value += coll
        w.record_cash_movement(pf, pf.base_currency, coll, "COLLATERAL", f"Cash collateral received from {p['borrower']} on loan {p['lend_id']} ({q:,} {p['security_id']})", ev)
        w.post(pf.id, f"Lent {q:,} {p['security_id']} to {p['borrower']} at {float(p['rate']):.2%}; cash collateral {coll:,.2f} received",
               [dr(f"1010:{pf.base_currency}", coll, p["security_id"], "collateral cash received"), cr("2460", coll, p["security_id"], "cash collateral received (securities lent)")], ev,
               {"lend_id": p["lend_id"], "security_id": p["security_id"], "kind": "LEND"})

    def _h_marked(self, ev: Event) -> None:
        w = self.w
        p = ev.payload
        pf = w.portfolios[p["portfolio_id"]]
        l = pf.lends[p["lend_id"]]
        delta = D(p["delta"])
        l["rate"], l["rebate_rate"], l["market_value"], l["collateral"] = float(p["rate"]), float(p["rebate_rate"]), D(p["market_value"]), D(p["collateral"])
        if delta != 0:
            ca = pf.cash_account(pf.base_currency)
            ca.balance += delta
            ca.base_value += delta
            w.record_cash_movement(pf, pf.base_currency, delta, "COLLATERAL", f"Collateral mark on loan {l['id']} {'received' if delta > 0 else 'returned'}", ev)
            w.post(pf.id, f"Lend {l['id']} collateral mark {delta:+,.2f}", [dr(f"1010:{pf.base_currency}", delta, l["security_id"], "collateral mark"), cr("2460", delta, l["security_id"], "collateral received adjusted")], ev,
                   {"lend_id": l["id"], "kind": "LEND_MARK"})

    def _h_accrued(self, ev: Event) -> None:
        w = self.w
        p = ev.payload
        pf = w.portfolios[p["portfolio_id"]]
        l = pf.lends[p["lend_id"]]
        fee, rebate = D(p["fee"]), D(p["rebate"])
        l["accrued_fee"] = D(l.get("accrued_fee", 0)) + fee
        l["accrued_rebate"] = D(l.get("accrued_rebate", 0)) + rebate
        lines = [dr("1430", fee, l["security_id"], "lending fee receivable"), cr("4360", fee, l["security_id"], "securities lending fee income"),
                 dr("5310", rebate, l["security_id"], "rebate on cash collateral"), cr("2370", rebate, l["security_id"], "rebate payable")]
        w.post(pf.id, f"Lend {l['id']}: fee {fee:,.2f} accrued, rebate {rebate:,.2f} accrued ({p['days']}d)", lines, ev, {"lend_id": l["id"], "kind": "LEND_ACCRUAL"})

    def _h_settled(self, ev: Event) -> None:
        w = self.w
        p = ev.payload
        pf = w.portfolios[p["portfolio_id"]]
        l = pf.lends[p["lend_id"]]
        fee, rebate = D(p["fee"]), D(p["rebate"])
        net = fee - rebate
        l["accrued_fee"] = D(l.get("accrued_fee", 0)) - fee
        l["accrued_rebate"] = D(l.get("accrued_rebate", 0)) - rebate
        l["fees_earned"] = D(l.get("fees_earned", 0)) + fee
        pf.lend_fees_earned += fee
        ca = pf.cash_account(p["currency"])
        ca.balance += net
        ca.base_value += net
        w.record_cash_movement(pf, p["currency"], net, "BORROW_FEE", f"Lend {l['id']}: fee {fee:,.2f} received less rebate {rebate:,.2f}", ev)
        w.post(pf.id, f"Lend {l['id']} monthly settlement: fee {fee:,.2f} less rebate {rebate:,.2f}",
               [dr(f"1010:{p['currency']}", net, l["security_id"], "net fee received"), cr("1430", fee, l["security_id"], "fee receivable settled"), dr("2370", rebate, l["security_id"], "rebate paid")], ev,
               {"lend_id": l["id"], "kind": "LEND_SETTLE"})

    def _h_recalled(self, ev: Event) -> None:
        p = ev.payload
        l = self.w.portfolios[p["portfolio_id"]].lends[p["lend_id"]]
        l["recall_due"], l["recall_quantity"] = p["due"], D(p["quantity"])
        l["history"].append({"date": ev.sim_date, "note": f"recalled {D(p['quantity']):,} due {p['due']}"})

    def _h_returned(self, ev: Event) -> None:
        w = self.w
        p = ev.payload
        pf = w.portfolios[p["portfolio_id"]]
        l = pf.lends[p["lend_id"]]
        q = min(D(p["quantity"]), D(l["quantity"]))
        frac = q / D(l["quantity"]) if D(l["quantity"]) else D(1)
        coll_back = money(D(l["collateral"]) * frac)
        l["quantity"] = D(l["quantity"]) - q
        l["collateral"] = D(l["collateral"]) - coll_back
        l["market_value"] = money(D(l["market_value"]) * (1 - frac))
        l["recall_due"], l["recall_quantity"] = None, ZERO
        if l["quantity"] <= 0:
            l["status"] = "RETURNED"
            l["closed"] = ev.sim_date
        l["history"].append({"date": ev.sim_date, "note": f"{q:,} returned: {p.get('note', '')}"})
        w.collateral.release(pf, l["security_id"], q, l["id"], ev, "shares returned by borrower")
        ca = pf.cash_account(pf.base_currency)
        ca.balance -= coll_back
        ca.base_value -= coll_back
        w.record_cash_movement(pf, pf.base_currency, -coll_back, "COLLATERAL", f"Collateral returned to {l['borrower']} on loan {l['id']}", ev)
        w.post(pf.id, f"Lend {l['id']}: {q:,} {l['security_id']} returned, collateral {coll_back:,.2f} repaid",
               [dr("2460", coll_back, l["security_id"], "collateral received repaid"), cr(f"1010:{pf.base_currency}", coll_back, l["security_id"], "cash returned")], ev,
               {"lend_id": l["id"], "kind": "LEND_RETURN"})
