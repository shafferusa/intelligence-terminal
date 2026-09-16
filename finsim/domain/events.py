"""Immutable domain events.

The event log is the system of record. Every state mutation is the application
of an event; totals (positions, cash, NAV) are reconstructed by replaying the
log. Events carry a `cause_id` so the audit trail can show, for example, that a
TRADE_EXECUTED event caused a LEDGER_POSTED event, a SETTLEMENT_INSTRUCTION_CREATED
event, and a VALUATION_MARKED event.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import date
from decimal import Decimal
from typing import Any, Dict, Optional


class E:
    """Event type constants."""
    WORLD_CREATED = "WORLD_CREATED"
    PORTFOLIO_CREATED = "PORTFOLIO_CREATED"
    CAPITAL_CONTRIBUTED = "CAPITAL_CONTRIBUTED"
    MARKET_CLOSE = "MARKET_CLOSE"
    REGIME_CHANGED = "REGIME_CHANGED"
    NEWS_PUBLISHED = "NEWS_PUBLISHED"
    ORDER_ENTERED = "ORDER_ENTERED"
    ORDER_REJECTED = "ORDER_REJECTED"
    ORDER_CANCELLED = "ORDER_CANCELLED"
    ORDER_STATUS_CHANGED = "ORDER_STATUS_CHANGED"
    TRADE_EXECUTED = "TRADE_EXECUTED"
    TRADE_STATUS_CHANGED = "TRADE_STATUS_CHANGED"
    SETTLEMENT_INSTRUCTION_CREATED = "SETTLEMENT_INSTRUCTION_CREATED"
    SETTLEMENT_STATUS_CHANGED = "SETTLEMENT_STATUS_CHANGED"
    SETTLEMENT_COMPLETED = "SETTLEMENT_COMPLETED"
    CASH_MOVEMENT = "CASH_MOVEMENT"
    CUSTODY_MOVEMENT = "CUSTODY_MOVEMENT"
    DIVIDEND_DECLARED = "DIVIDEND_DECLARED"
    DIVIDEND_ENTITLED = "DIVIDEND_ENTITLED"
    DIVIDEND_PAID = "DIVIDEND_PAID"
    COUPON_PAID = "COUPON_PAID"
    BOND_MATURED = "BOND_MATURED"
    INTEREST_ACCRUED = "INTEREST_ACCRUED"
    INTEREST_SETTLED = "INTEREST_SETTLED"
    LEDGER_POSTED = "LEDGER_POSTED"
    VALUATION_MARKED = "VALUATION_MARKED"
    NAV_SNAPSHOT = "NAV_SNAPSHOT"
    DAY_STARTED = "DAY_STARTED"
    DAY_CLOSING = "DAY_CLOSING"
    DAY_CLOSED = "DAY_CLOSED"
    FUTURES_SETTLED = "FUTURES_SETTLED"          # daily variation margin per position
    MARGIN_SWEPT = "MARGIN_SWEPT"                # initial margin moved between cash and clearing account
    MARGIN_CALL = "MARGIN_CALL"
    FORCED_LIQUIDATION = "FORCED_LIQUIDATION"
    CONTRACT_EXPIRED = "CONTRACT_EXPIRED"
    RISK_BREACH = "RISK_BREACH"
    PERFORMANCE_REVIEW = "PERFORMANCE_REVIEW"
    CAREER_EVENT = "CAREER_EVENT"
    DAILY_BRIEFING = "DAILY_BRIEFING"


@dataclass
class Event:
    seq: int
    id: str
    type: str
    sim_date: str                    # ISO date the event happened in the simulation
    payload: Dict[str, Any] = field(default_factory=dict)
    cause_id: Optional[str] = None   # the event that caused this one
    portfolio_id: Optional[str] = None

    def to_json(self) -> str:
        return json.dumps(asdict(self), default=_json_default, separators=(",", ":"))

    @staticmethod
    def from_json(s: str) -> "Event":
        d = json.loads(s)
        return Event(**d)

    def to_dict(self) -> Dict[str, Any]:
        return json.loads(self.to_json())


def _json_default(o):
    if isinstance(o, Decimal):
        return str(o)
    if isinstance(o, date):
        return o.isoformat()
    raise TypeError(f"not JSON serialisable: {type(o)}")
