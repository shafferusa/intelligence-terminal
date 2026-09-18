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
    PORTFOLIO_DELETED = "PORTFOLIO_DELETED"         # a flat book closed by the player (nothing open, nothing owed)
    TREASURY_FUNDED = "TREASURY_FUNDED"             # capital put into the save's treasury (the pool the books draw from)
    TREASURY_ALLOCATED = "TREASURY_ALLOCATED"       # cash moved between the treasury and a book (direction TO_BOOK | TO_TREASURY)
    CAPITAL_CONTRIBUTED = "CAPITAL_CONTRIBUTED"
    MARKET_CLOSE = "MARKET_CLOSE"
    REAL_HISTORY_LOADED = "REAL_HISTORY_LOADED"     # a world that tracks the market: the real closes it started from
    REAL_MACRO_LOADED = "REAL_MACRO_LOADED"         # a career world: the real economic series it started from
    CLOCK_CHANGED = "CLOCK_CHANGED"                 # update time / timezone changed from the settings page
    MARKET_SOURCE_CHANGED = "MARKET_SOURCE_CHANGED"   # a simulated save switched to tracking the real market (followed by REAL_HISTORY_LOADED)
    LIVE_SWEEP = "LIVE_SWEEP"                       # resting live orders checked against the latest real quotes (the cause of what they did)
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
    # phase 8/10 — careers, investors, clients, lending desk, treasury, AI desks
    MISSION_STARTED = "MISSION_STARTED"
    MISSION_UPDATED = "MISSION_UPDATED"
    MISSION_COMPLETED = "MISSION_COMPLETED"
    MISSION_FAILED = "MISSION_FAILED"
    SCENARIO_EVENT = "SCENARIO_EVENT"
    INVESTOR_FLOW_NOTIFIED = "INVESTOR_FLOW_NOTIFIED"
    CAPITAL_WITHDRAWN = "CAPITAL_WITHDRAWN"
    FEE_ACCRUED = "FEE_ACCRUED"
    FEE_PAID = "FEE_PAID"
    CLIENT_RFQ_RECEIVED = "CLIENT_RFQ_RECEIVED"
    CLIENT_RFQ_QUOTED = "CLIENT_RFQ_QUOTED"
    CLIENT_RFQ_RESOLVED = "CLIENT_RFQ_RESOLVED"
    SECURITY_LENT = "SECURITY_LENT"
    LEND_MARKED = "LEND_MARKED"
    LEND_FEE_ACCRUED = "LEND_FEE_ACCRUED"
    LEND_FEE_SETTLED = "LEND_FEE_SETTLED"
    LEND_RECALLED = "LEND_RECALLED"
    LEND_RETURNED = "LEND_RETURNED"
    TREASURY_SETUP = "TREASURY_SETUP"
    OPERATING_FLOW = "OPERATING_FLOW"
    DEBT_SERVICE = "DEBT_SERVICE"
    DESK_REQUEST = "DESK_REQUEST"
    DESK_REQUEST_DECIDED = "DESK_REQUEST_DECIDED"
    DESK_LIMIT_SET = "DESK_LIMIT_SET"
    DESK_CLIP_WORKED = "DESK_CLIP_WORKED"
    # phase 9 — commodity depth
    PHYSICAL_MODE_SET = "PHYSICAL_MODE_SET"
    PHYSICAL_LISTED = "PHYSICAL_LISTED"
    PHYSICAL_DELIVERY = "PHYSICAL_DELIVERY"
    STORAGE_CHARGED = "STORAGE_CHARGED"
    DAILY_BRIEFING = "DAILY_BRIEFING"
    REGIME_FORCED = "REGIME_FORCED"
    RATES_SHOCK_FORCED = "RATES_SHOCK_FORCED"
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
    # phase 6: operations depth
    CORPORATE_EVENT_ANNOUNCED = "CORPORATE_EVENT_ANNOUNCED"
    CORPORATE_ELECTION = "CORPORATE_ELECTION"
    CORPORATE_EVENT_PROCESSED = "CORPORATE_EVENT_PROCESSED"
    SECURITY_LISTED = "SECURITY_LISTED"
    SECURITY_DELISTED = "SECURITY_DELISTED"
    FAIL_CHARGE = "FAIL_CHARGE"
    BUY_IN = "BUY_IN"
    BOND_DEFAULTED = "BOND_DEFAULTED"
    # phase 7: macro world
    ECONOMIC_RELEASE = "ECONOMIC_RELEASE"
    EARNINGS_REPORTED = "EARNINGS_REPORTED"
    RATING_CHANGED = "RATING_CHANGED"
    ISSUER_DEFAULTED = "ISSUER_DEFAULTED"
    # private credit
    PC_COMMITTED = "PC_COMMITTED"
    PC_INTEREST_ACCRUED = "PC_INTEREST_ACCRUED"
    PC_INTEREST_PAID = "PC_INTEREST_PAID"
    PC_MARKED = "PC_MARKED"
    PC_RATING_CHANGED = "PC_RATING_CHANGED"
    PC_AMENDED = "PC_AMENDED"
    PC_DEFAULTED = "PC_DEFAULTED"
    PC_RECOVERED = "PC_RECOVERED"
    PC_SOLD = "PC_SOLD"
    PC_MATURED = "PC_MATURED"
    # private equity
    PE_DILIGENCE = "PE_DILIGENCE"
    PE_BID = "PE_BID"
    PE_DEAL_RESOLVED = "PE_DEAL_RESOLVED"
    PE_REPORT = "PE_REPORT"
    PE_MARKED = "PE_MARKED"
    PE_INITIATIVE = "PE_INITIATIVE"
    PE_RECAP = "PE_RECAP"
    PE_COVENANT = "PE_COVENANT"
    PE_CURED = "PE_CURED"
    PE_RESTRUCTURED = "PE_RESTRUCTURED"
    PE_EXIT_STARTED = "PE_EXIT_STARTED"
    PE_EXITED = "PE_EXITED"
    PE_SELLDOWN = "PE_SELLDOWN"
    # investment banking
    IB_PITCHED = "IB_PITCHED"
    IB_MANDATE_RESOLVED = "IB_MANDATE_RESOLVED"
    IB_UPDATE = "IB_UPDATE"
    IB_DECIDED = "IB_DECIDED"
    IB_CLOSED = "IB_CLOSED"


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
