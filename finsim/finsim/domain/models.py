"""State models. These are *projections* of the event log: they are rebuilt by
replaying events and are never persisted on their own."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Dict, List, Optional

from ..money import ZERO


# ----------------------------------------------------------------------------
# Reference data
# ----------------------------------------------------------------------------

@dataclass
class Security:
    id: str                    # ticker / internal id
    name: str
    asset_class: str           # EQUITY | ETF | PREFERRED | ADR | REIT | GOVT_BOND | CORP_BOND
    market: str                # settlement market code, e.g. US_EQUITY
    currency: str
    country: str
    sector: str
    isin: str
    cusip: str
    # equity-style fields
    shares_outstanding: Optional[int] = None
    dividend_yield: float = 0.0      # annual, as fraction
    dividend_per_share: Decimal = ZERO   # quarterly amount
    div_anchor_month: int = 0        # 0,1,2 within quarter
    div_day_bd: int = 10             # business day of month for ex-date
    beta: float = 1.0
    sigma_annual: float = 0.25       # idiosyncratic vol
    adv: int = 1_000_000             # average daily volume (shares / face)
    spread_bps: float = 5.0
    liquidity_tier: str = "MID"
    fundamentals: Dict[str, float] = field(default_factory=dict)
    # bond-style fields
    coupon: Optional[float] = None       # annual coupon rate
    maturity: Optional[str] = None       # ISO
    issue_date: Optional[str] = None
    freq: int = 2
    rating: Optional[str] = None
    issuer: Optional[str] = None
    spread_bps_credit: float = 0.0       # credit spread over govt curve (bps)
    recovery_rate: float = 0.4
    lot_size: int = 1
    # futures-style fields
    underlying: Optional[str] = None       # commodity/index code, e.g. CL, ES, ZN
    underlying_class: Optional[str] = None # COMMODITY_ENERGY | COMMODITY_METAL | COMMODITY_AG | COMMODITY_LIVESTOCK | EQUITY_INDEX | RATES
    contract_month: Optional[str] = None   # YYYY-MM
    multiplier: float = 1.0
    tick_size: float = 0.01
    expiry: Optional[str] = None           # last trade date ISO
    margin_pct: float = 0.0                # initial margin as fraction of notional (before regime multiplier)
    unit: str = ""
    expired: bool = False

    @property
    def is_bond(self) -> bool:
        return self.asset_class in ("GOVT_BOND", "CORP_BOND")

    @property
    def is_future(self) -> bool:
        return self.asset_class == "FUTURE"


@dataclass
class Bar:
    date: str
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
    bid: Decimal
    ask: Decimal


@dataclass
class YieldCurve:
    date: str
    tenors: List[float]
    rates: List[float]     # zero rates, annual, semiannual compounding, as fractions
    ig_spread_bps: float = 110.0
    hy_spread_bps: float = 380.0
    policy_rate: float = 0.0425


# ----------------------------------------------------------------------------
# Portfolio state
# ----------------------------------------------------------------------------

@dataclass
class Lot:
    id: str
    trade_id: str
    open_date: str
    quantity: Decimal          # remaining
    original_quantity: Decimal
    cost_per_unit: Decimal     # clean price per share / per 100 face for bonds
    cost_total: Decimal        # remaining cost


@dataclass
class Position:
    portfolio_id: str
    security_id: str
    quantity: Decimal = ZERO             # trade-date quantity
    settled_quantity: Decimal = ZERO     # in custody
    pending_receive: Decimal = ZERO
    pending_deliver: Decimal = ZERO
    lots: List[Lot] = field(default_factory=list)
    cost_basis: Decimal = ZERO           # sum of open lots' cost
    valuation_adjustment: Decimal = ZERO # ledger 1150 balance attributable to this security
    accrued_interest: Decimal = ZERO     # ledger 1220 balance for bonds
    realized_pnl: Decimal = ZERO
    dividend_income: Decimal = ZERO
    interest_income: Decimal = ZERO
    commissions: Decimal = ZERO
    mark: Decimal = ZERO
    market_value: Decimal = ZERO
    trade_ids: List[str] = field(default_factory=list)
    # futures
    is_future: bool = False
    settlement_price: Decimal = ZERO      # last daily settlement price used for VM
    variation_margin_total: Decimal = ZERO
    initial_margin: Decimal = ZERO        # cash held at the clearing broker for this position
    notional: Decimal = ZERO
    day_variation_margin: Decimal = ZERO
    average_cost_future: Decimal = ZERO
    day_fills: List[Dict] = field(default_factory=list)

    @property
    def unrealized_pnl(self) -> Decimal:
        return self.market_value - self.cost_basis

    @property
    def average_cost(self) -> Decimal:
        return (self.cost_basis / self.quantity) if self.quantity else ZERO


@dataclass
class CashAccount:
    portfolio_id: str
    currency: str
    balance: Decimal = ZERO             # settled cash (ledger 1010:CCY)
    accrued_interest: Decimal = ZERO    # receivable(+)/payable(-) not yet settled


@dataclass
class Order:
    id: str
    portfolio_id: str
    security_id: str
    side: str                 # BUY | SELL
    order_type: str           # MARKET | LIMIT | STOP | STOP_LIMIT
    quantity: Decimal
    limit_price: Optional[Decimal]
    stop_price: Optional[Decimal]
    time_in_force: str        # DAY | GTC
    status: str               # ENTERED | WORKING | PARTIALLY_FILLED | FILLED | CANCELLED | REJECTED | EXPIRED
    entered_date: str
    filled_quantity: Decimal = ZERO
    avg_fill_price: Decimal = ZERO
    trade_ids: List[str] = field(default_factory=list)
    reason: Optional[str] = None
    triggered: bool = False
    strategy_tag: Optional[str] = None
    trail_pct: Optional[float] = None         # TRAILING_STOP: distance from the best close, as fraction
    trail_level: Optional[Decimal] = None     # current trailing stop level
    condition: Optional[Dict] = None          # {"ref": "NVRA"|"CURVE:10Y"|"SPOT:CL", "op": "<="|">=", "value": float}
    condition_met_date: Optional[str] = None
    history: List[Dict] = field(default_factory=list)


TRADE_STATES = ["EXECUTED", "CAPTURED", "MATCHED", "AFFIRMED", "CLEARED", "SETTLEMENT_PENDING", "SETTLED", "FAILED", "CANCELLED"]


@dataclass
class Trade:
    id: str
    order_id: str
    portfolio_id: str
    security_id: str
    side: str
    quantity: Decimal
    price: Decimal                 # clean execution price
    gross_amount: Decimal          # qty * price (bonds: face*price/100)
    accrued_interest: Decimal      # bonds only
    commission: Decimal
    net_amount: Decimal            # cash to pay (BUY) / receive (SELL)
    currency: str
    trade_date: str
    settlement_date: str
    status: str
    status_history: List[Dict] = field(default_factory=list)
    execution_detail: Dict = field(default_factory=dict)
    realized_pnl: Decimal = ZERO
    lots_relieved: List[Dict] = field(default_factory=list)
    settlement_instruction_id: Optional[str] = None
    broker: str = "Harbor Securities (executing broker)"


@dataclass
class SettlementInstruction:
    id: str
    trade_id: str
    portfolio_id: str
    security_id: str
    isin: str
    cusip: str
    instruction_type: str     # DVP | RVP | DF | RF
    quantity: Decimal
    cash_amount: Decimal
    currency: str
    trade_date: str
    settlement_date: str
    delivering_party: str
    receiving_party: str
    custodian: str
    status: str               # PENDING | MATCHED | SETTLED | FAILED | CANCELLED
    history: List[Dict] = field(default_factory=list)
    fail_count: int = 0
    fail_reason: Optional[str] = None
    settled_date: Optional[str] = None


@dataclass
class CashMovement:
    id: str
    portfolio_id: str
    currency: str
    amount: Decimal           # +in / -out
    kind: str                 # SETTLEMENT | DIVIDEND | COUPON | INTEREST | CAPITAL | REDEMPTION
    reference: str
    date: str
    balance_after: Decimal
    event_id: str


@dataclass
class CustodyMovement:
    id: str
    portfolio_id: str
    security_id: str
    quantity: Decimal         # +receive / -deliver
    kind: str
    reference: str
    date: str
    balance_after: Decimal
    event_id: str


@dataclass
class CorporateAction:
    id: str
    security_id: str
    action_type: str          # CASH_DIVIDEND | COUPON | MATURITY
    declared_date: str
    ex_date: str
    record_date: str
    pay_date: str
    amount_per_unit: Decimal
    currency: str
    status: str               # DECLARED | EX | PAID
    entitlements: Dict[str, Dict] = field(default_factory=dict)   # portfolio_id -> {qty, amount, paid}


@dataclass
class NAVSnapshot:
    portfolio_id: str
    date: str
    nav: Decimal
    cash: Dict[str, Decimal]
    market_value: Decimal
    receivables: Decimal
    payables: Decimal
    accrued: Decimal
    day_pnl: Decimal
    explain: Dict[str, Decimal]
    by_position: Dict[str, Dict[str, Decimal]]
    capital_flows: Decimal
    cumulative_realized: Decimal
    unrealized: Decimal
    gross_exposure: Decimal
    net_exposure: Decimal
    long_exposure: Decimal
    short_exposure: Decimal
    leverage: Decimal
    ledger_nav: Decimal
    event_id: str


@dataclass
class MarginCall:
    id: str
    portfolio_id: str
    date: str
    amount: Decimal
    reason: str
    status: str                 # OPEN | MET | FORCED
    days_open: int = 0
    resolved_date: Optional[str] = None


@dataclass
class Portfolio:
    id: str
    name: str
    portfolio_type: str
    base_currency: str
    benchmark: Optional[str]
    created: str
    realism: str = "PROFESSIONAL"
    mode: str = "SANDBOX"
    custody_account: str = ""
    job: str = "SANDBOX"
    level: int = 0
    reviews: List[Dict] = field(default_factory=list)
    breaches: List[Dict] = field(default_factory=list)
    margin_calls: List[MarginCall] = field(default_factory=list)
    briefings: List[Dict] = field(default_factory=list)
    career_log: List[Dict] = field(default_factory=list)
    peak_nav: Decimal = ZERO
    cash: Dict[str, CashAccount] = field(default_factory=dict)
    positions: Dict[str, Position] = field(default_factory=dict)
    orders: Dict[str, Order] = field(default_factory=dict)
    trades: Dict[str, Trade] = field(default_factory=dict)
    settlements: Dict[str, SettlementInstruction] = field(default_factory=dict)
    cash_movements: List[CashMovement] = field(default_factory=list)
    custody_movements: List[CustodyMovement] = field(default_factory=list)
    nav_history: List[NAVSnapshot] = field(default_factory=list)
    contributed_capital: Decimal = ZERO
    day_capital_flows: Decimal = ZERO
    day_trade_ids: List[str] = field(default_factory=list)

    def cash_account(self, ccy: str) -> CashAccount:
        if ccy not in self.cash:
            self.cash[ccy] = CashAccount(self.id, ccy)
        return self.cash[ccy]

    def position(self, security_id: str) -> Position:
        if security_id not in self.positions:
            self.positions[security_id] = Position(self.id, security_id)
        return self.positions[security_id]


@dataclass
class NewsItem:
    id: str
    date: str
    headline: str
    body: str
    category: str
    refs: List[str]
    event_id: str
