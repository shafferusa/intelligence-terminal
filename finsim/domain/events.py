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
    REGIME_FORCED = "REGIME_FORCED"
    # phase 2 — collateral
    COLLATERAL_PLEDGED = "COLLATERAL_PLEDGED"          # securities encumbered for a purpose
    COLLATERAL_RELEASED = "COLLATERAL_RELEASED"
    CASH_COLLATERAL_MOVED = "CASH_COLLATERAL_MOVED"    # cash posted (+) to / returned (-) from a counterparty
    COLLATERAL_CALL = "COLLATERAL_CALL"                # issued / updated / met / forced
    # securities lending
    LOCATE_REQUESTED = "LOCATE_REQUESTED"
    SECURITY_LOAN_OPENED = "SECURITY_LOAN_OPENED"
    SECURITY_LOAN_RERATED = "SECURITY_LOAN_RERATED"
    SECURITY_LOAN_MARKED = "SECURITY_LOAN_MARKED"
    BORROW_FEE_ACCRUED = "BORROW_FEE_ACCRUED"
    BORROW_FEE_SETTLED = "BORROW_FEE_SETTLED"
    SECURITY_LOAN_RECALLED = "SECURITY_LOAN_RECALLED"
    SECURITY_LOAN_RETURNED = "SECURITY_LOAN_RETURNED"
    SECURITY_LOAN_BUY_IN = "SECURITY_LOAN_BUY_IN"
    DIVIDEND_OBLIGATION = "DIVIDEND_OBLIGATION"        # manufactured dividend owed while short
    DIVIDEND_OBLIGATION_PAID = "DIVIDEND_OBLIGATION_PAID"
    # repo
    REPO_OPENED = "REPO_OPENED"
    REPO_INTEREST_ACCRUED = "REPO_INTEREST_ACCRUED"
    REPO_ROLLED = "REPO_ROLLED"
    REPO_MARKED = "REPO_MARKED"
    REPO_ADJUSTED = "REPO_ADJUSTED"                    # collateral added / substituted / principal reduced
    REPO_CLOSED = "REPO_CLOSED"
    # prime brokerage
    MARGIN_LOAN_CHANGED = "MARGIN_LOAN_CHANGED"        # draw (+) / repay (-)
    MARGIN_INTEREST_ACCRUED = "MARGIN_INTEREST_ACCRUED"
    MARGIN_INTEREST_SETTLED = "MARGIN_INTEREST_SETTLED"
    # fx
    FX_TRADE_EXECUTED = "FX_TRADE_EXECUTED"
    FX_TRADE_SETTLED = "FX_TRADE_SETTLED"
    FX_TRANSLATED = "FX_TRANSLATED"
    FX_FORWARD_OPENED = "FX_FORWARD_OPENED"
    FX_FORWARD_MARKED = "FX_FORWARD_MARKED"
    FX_FORWARD_SETTLED = "FX_FORWARD_SETTLED"
    # phase 3 — listed options
    OPTION_EXERCISED = "OPTION_EXERCISED"
    OPTION_ASSIGNED = "OPTION_ASSIGNED"
    OPTION_EXPIRED = "OPTION_EXPIRED"
    OPTION_CASH_SETTLED = "OPTION_CASH_SETTLED"
    OPTIONS_MARGIN_COMPUTED = "OPTIONS_MARGIN_COMPUTED"
    STRATEGY_ENTERED = "STRATEGY_ENTERED"
    STRATEGY_STATUS_CHANGED = "STRATEGY_STATUS_CHANGED"
    STOCK_SPLIT = "STOCK_SPLIT"
    CONTRACT_ADJUSTED = "CONTRACT_ADJUSTED"
    # phase 4: OTC derivatives, dealers, ISDA/CSA
    RFQ_REQUESTED = "RFQ_REQUESTED"
    RFQ_EXECUTED = "RFQ_EXECUTED"
    RFQ_EXPIRED = "RFQ_EXPIRED"
    OTC_TRADE_OPENED = "OTC_TRADE_OPENED"
    OTC_TRADE_MARKED = "OTC_TRADE_MARKED"
    OTC_FIXING = "OTC_FIXING"
    OTC_CASHFLOW = "OTC_CASHFLOW"
    OTC_TRADE_TERMINATED = "OTC_TRADE_TERMINATED"
    OTC_TRADE_EXERCISED = "OTC_TRADE_EXERCISED"
    CSA_MARGIN_MOVED = "CSA_MARGIN_MOVED"
    CSA_CALL = "CSA_CALL"
    CREDIT_EVENT = "CREDIT_EVENT"
    COUNTERPARTY_DEFAULTED = "COUNTERPARTY_DEFAULTED"
    COUNTERPARTY_EXPOSURE = "COUNTERPARTY_EXPOSURE"
    # phase 5: risk
    RISK_SNAPSHOT = "RISK_SNAPSHOT"


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
