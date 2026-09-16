"""OTC derivatives, dealers and ISDA/CSA state (Phase 4)."""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Dict, List, Optional

from ..money import ZERO


class OE:
    """OTC event types (also listed on domain.events.E)."""
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


PRODUCTS = ("IRS", "FRA", "CAP", "FLOOR", "SWAPTION", "XCCY", "TRS", "CDS", "COMMODITY_SWAP")
BUCKET_BY_PRODUCT = {"IRS": "rates", "FRA": "rates", "CAP": "rates", "FLOOR": "rates", "SWAPTION": "rates", "XCCY": "fx", "TRS": "equities",
                     "CDS": "credit", "COMMODITY_SWAP": "commodities"}


@dataclass
class OTCTrade:
    id: str
    portfolio_id: str
    product: str
    counterparty: str
    notional: Decimal                 # in `currency` (for TRS: units x reference price at the last reset)
    currency: str
    trade_date: str
    start: str
    maturity: str
    terms: Dict                       # product-specific, immutable after opening
    status: str = "OPEN"              # OPEN | MATURED | TERMINATED | EXERCISED | EXPIRED | SETTLED_DEFAULT
    mtm: Decimal = ZERO               # base-currency PV from the portfolio's view (dirty, includes accruals)
    analytics: Dict = field(default_factory=dict)
    fixings: Dict[str, float] = field(default_factory=dict)   # period start ISO -> rate / price
    cashflows: List[Dict] = field(default_factory=list)
    realized: Decimal = ZERO          # settled cash to date (base)
    state: Dict = field(default_factory=dict)   # mutable product state (TRS reset price, accrued dividends, ...)
    closed_date: Optional[str] = None
    notes: List[Dict] = field(default_factory=list)

    @property
    def is_open(self) -> bool:
        return self.status == "OPEN"


@dataclass
class RFQ:
    id: str
    portfolio_id: str
    product: str
    params: Dict
    quotes: List[Dict]                # [{dealer, bid, ask, mid, unit, pv_bid, pv_ask, note}]
    mid: Dict                         # engine's fair value (unit + pv)
    status: str = "OPEN"              # OPEN | EXECUTED | EXPIRED | CANCELLED
    date: str = ""
    expires: str = ""
    executed_with: Optional[str] = None
    trade_id: Optional[str] = None


@dataclass
class CSA:
    """Credit-support annex under the ISDA master with one dealer: one netting set."""
    counterparty: str
    portfolio_id: str
    threshold: Decimal                # uncollateralised exposure either side
    mta: Decimal                      # minimum transfer amount
    im_pct: Dict[str, float]          # initial margin (independent amount) by product, fraction of notional
    eligible: List[str]               # CASH | GOVT_BOND
    vm_posted: Decimal = ZERO         # cash we hold at the dealer (asset: 1400 ref CSA:<dealer>)
    vm_received: Decimal = ZERO       # dealer's cash we hold (liability 2450)
    im_posted: Decimal = ZERO         # our IM at the dealer's segregated custodian (1400 ref IM:<dealer>)
    im_received: Decimal = ZERO       # dealer's IM segregated at a third party (off balance sheet, memo)
    status: str = "ACTIVE"            # ACTIVE | TERMINATED
    history: List[Dict] = field(default_factory=list)
