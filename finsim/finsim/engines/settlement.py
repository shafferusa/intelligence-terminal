"""Settlement & custody engine.

Trade lifecycle after execution: CAPTURED -> MATCHED -> AFFIRMED -> CLEARED ->
SETTLEMENT_PENDING -> SETTLED. A small, seeded share of trades break at
matching (details disagree with the broker) and re-match the next day, which
can push them past their contractual settlement date and produce a fail.

Settlement is Delivery-versus-Payment: securities and cash move together and
only if both legs can be honoured. A buy fails when settled cash is short; a
sell fails when the securities are not yet in custody (e.g. bought on a trade
whose own settlement failed). Failed instructions retry every business day.
Cash movement and custody movement are recorded separately, and the ledger
moves the payable/receivable into cash on settlement.
"""
from __future__ import annotations

import random
from datetime import date
from decimal import Decimal
from typing import List, Optional

from ..domain.events import E, Event
from ..domain.models import Portfolio, SettlementInstruction
from ..engines.ledger import dr, cr
from ..money import D, money, ZERO

MATCH_BREAK_PROBABILITY = 0.03


class SettlementEngine:
    def __init__(self, world):
        self.w = world

    def register(self) -> None:
        w = self.w
        w.on(E.SETTLEMENT_INSTRUCTION_CREATED, lambda world, ev: self._h_created(ev))
        w.on(E.SETTLEMENT_STATUS_CHANGED, lambda world, ev: self._h_status(ev))
        w.on(E.SETTLEMENT_COMPLETED, lambda world, ev: self._h_completed(ev))

    # ------------------------------------------------------------------ daily processing
    def process_lifecycle(self, cause: Event) -> None:
        """Advance post-trade states for every open trade (runs at each close)."""
        w = self.w
        today = w.current_date.isoformat()
        for pf in w.portfolios.values():
            for t in list(pf.trades.values()):
                if w.securities[t.security_id].is_future:
                    continue
                if t.status == "EXECUTED":
                    self._status(t, "CAPTURED", cause, "trade captured in middle-office system")
                if t.status == "CAPTURED":
                    rng = random.Random(f"{w.seed}|match|{t.id}|{today}")
                    if rng.random() < MATCH_BREAK_PROBABILITY and not any(h["status"] == "CAPTURED" and h.get("note", "").startswith("match break") for h in t.status_history):
                        self._status(t, "CAPTURED", cause, "match break: broker confirmation differs (quantity/price); re-matching next day")
                        continue
                    self._status(t, "MATCHED", cause, "matched against executing broker confirmation")
                if t.status == "MATCHED":
                    self._status(t, "AFFIRMED", cause, "affirmed by custodian")
                if t.status == "AFFIRMED":
                    self._status(t, "CLEARED", cause, "accepted by clearing agent (NSCC-style CNS) / DTC-style depository")
                if t.status == "CLEARED":
                    self._status(t, "SETTLEMENT_PENDING", cause, f"awaiting settlement {t.settlement_date}")
                    si = pf.settlements.get(t.settlement_instruction_id)
                    if si and si.status == "PENDING":
                        self._si_status(si, "MATCHED", cause, "instruction matched at custodian")

    def process_due(self, cause: Event) -> None:
        w = self.w
        today = w.current_date
        for pf in w.portfolios.values():
            due = [si for si in pf.settlements.values() if si.status in ("PENDING", "MATCHED", "FAILED")
                   and date.fromisoformat(si.settlement_date) <= today]
            # settle receipts before deliveries so same-day round trips can work (DVP ordering by custodian)
            due.sort(key=lambda s: (0 if s.instruction_type == "RVP" else 1, s.settlement_date, s.id))
            for si in due:
                if si.status == "PENDING":
                    self._fail(si, cause, "instruction unmatched at custodian on settlement date")
                    continue
                reason = self._check(pf, si, cause)
                if reason:
                    self._fail(si, cause, reason)
                else:
                    w.emit(E.SETTLEMENT_COMPLETED, {"si_id": si.id, "portfolio_id": pf.id, "trade_id": si.trade_id, "security_id": si.security_id,
                                                    "instruction_type": si.instruction_type, "quantity": si.quantity, "cash_amount": si.cash_amount,
                                                    "currency": si.currency, "late": si.settlement_date < today.isoformat(),
                                                    "contractual_settlement_date": si.settlement_date},
                           cause_id=cause.id, portfolio_id=pf.id)

    def _check(self, pf: Portfolio, si: SettlementInstruction, cause: Optional[Event] = None) -> Optional[str]:
        if si.instruction_type == "RVP":
            bal = pf.cash_account(si.currency).balance
            if bal < si.cash_amount:
                shortfall = si.cash_amount - bal
                if si.currency == pf.base_currency and cause is not None and self.w.prime.fund_settlement(pf, shortfall, si.id, cause):
                    return None
                return f"insufficient settled {si.currency} cash: need {si.cash_amount:,.2f}, have {bal:,.2f}; prime broker would not finance the shortfall"
        else:
            pos = pf.position(si.security_id)
            if pos.settled_quantity < si.quantity:
                return f"securities not in custody: need {si.quantity:,}, settled position is {pos.settled_quantity:,}"
        return None

    def _fail(self, si: SettlementInstruction, cause: Event, reason: str) -> None:
        self._si_status(si, "FAILED", cause, reason)
        t = self.w.portfolios[si.portfolio_id].trades[si.trade_id]
        if t.status != "FAILED":
            self._status(t, "FAILED", cause, f"settlement failed: {reason}")

    def _status(self, t, status: str, cause: Event, note: str) -> None:
        self.w.emit(E.TRADE_STATUS_CHANGED, {"portfolio_id": t.portfolio_id, "trade_id": t.id, "status": status, "note": note},
                    cause_id=cause.id, portfolio_id=t.portfolio_id)

    def _si_status(self, si, status: str, cause: Event, note: str) -> None:
        self.w.emit(E.SETTLEMENT_STATUS_CHANGED, {"portfolio_id": si.portfolio_id, "si_id": si.id, "status": status, "note": note},
                    cause_id=cause.id, portfolio_id=si.portfolio_id)

    # ------------------------------------------------------------------ handlers
    def _h_created(self, ev: Event) -> None:
        p = ev.payload
        pf = self.w.portfolios[p["portfolio_id"]]
        si = SettlementInstruction(id=p["si_id"], trade_id=p["trade_id"], portfolio_id=pf.id, security_id=p["security_id"], isin=p["isin"],
                                   cusip=p["cusip"], instruction_type=p["instruction_type"], quantity=D(p["quantity"]),
                                   cash_amount=D(p["cash_amount"]), currency=p["currency"], trade_date=p["trade_date"],
                                   settlement_date=p["settlement_date"], delivering_party=p["delivering_party"],
                                   receiving_party=p["receiving_party"], custodian=p["custodian"], status="PENDING")
        si.history.append({"status": "PENDING", "date": ev.sim_date, "event_id": ev.id, "note": "instruction generated from trade"})
        pf.settlements[si.id] = si
        pf.trades[si.trade_id].settlement_instruction_id = si.id

    def _h_status(self, ev: Event) -> None:
        p = ev.payload
        si = self.w.portfolios[p["portfolio_id"]].settlements[p["si_id"]]
        si.status = p["status"]
        si.history.append({"status": p["status"], "date": ev.sim_date, "event_id": ev.id, "note": p.get("note", "")})
        if p["status"] == "FAILED":
            si.fail_count += 1
            si.fail_reason = p.get("note")

    def _h_completed(self, ev: Event) -> None:
        w = self.w
        p = ev.payload
        pf = w.portfolios[p["portfolio_id"]]
        si = pf.settlements[p["si_id"]]
        t = pf.trades[p["trade_id"]]
        q, cash, ccy = D(p["quantity"]), D(p["cash_amount"]), p["currency"]
        pos = pf.position(p["security_id"])
        ca = pf.cash_account(ccy)
        if p["instruction_type"] == "RVP":
            ca.balance -= cash
            pos.settled_quantity += q
            pos.pending_receive -= q
            w.record_cash_movement(pf, ccy, -cash, "SETTLEMENT", f"RVP {si.id} / trade {t.id}: paid for {q:,} {si.security_id}", ev)
            w.record_custody_movement(pf, si.security_id, q, "RECEIVE", f"RVP {si.id}: received vs payment", ev)
            lines = [dr("2100", cash, si.security_id, "payable extinguished"), cr(f"1010:{ccy}", cash, si.security_id, "cash paid on settlement")]
        else:
            ca.balance += cash
            pos.settled_quantity -= q
            pos.pending_deliver -= q
            w.record_cash_movement(pf, ccy, cash, "SETTLEMENT", f"DVP {si.id} / trade {t.id}: proceeds for {q:,} {si.security_id}", ev)
            w.record_custody_movement(pf, si.security_id, -q, "DELIVER", f"DVP {si.id}: delivered vs payment", ev)
            lines = [dr(f"1010:{ccy}", cash, si.security_id, "cash received on settlement"), cr("1200", cash, si.security_id, "receivable extinguished")]
        si.status = "SETTLED"
        si.settled_date = ev.sim_date
        note = "settled DVP" + (" (late — after contractual settlement date)" if p.get("late") else "")
        si.history.append({"status": "SETTLED", "date": ev.sim_date, "event_id": ev.id, "note": note})
        t.status = "SETTLED"
        t.status_history.append({"status": "SETTLED", "date": ev.sim_date, "event_id": ev.id, "note": note})
        w.post(pf.id, f"Settlement {si.id} ({si.instruction_type}) trade {t.id}: {q:,} {si.security_id} vs {ccy} {cash:,.2f}", lines, ev,
               {"si_id": si.id, "trade_id": t.id, "security_id": si.security_id})
