"""State models. These are *projections* of the event log: they are rebuilt by
replaying events and are never persisted on their own."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, Dict, List, Optional

from ..money import ZERO


# ----------------------------------------------------------------------------
# Reference data
# ----------------------------------------------------------------------------

@dataclass
class Security:
    id: str                    # ticker / internal id
    name: str
    asset_class: str           # EQUITY | ETF | PREFERRED | ADR | REIT | CRYPTO | INDEX | GOVT_BOND | CORP_BOND | FUTURE | OPTION | PHYSICAL
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
    delisted: bool = False
    defaulted: bool = False
    recovery_date: Optional[str] = None     # defaulted bonds: when the recovery is paid
    # option fields
    option_type: Optional[str] = None       # C | P
    strike: Optional[float] = None
    exercise_style: Optional[str] = None    # AMERICAN | EUROPEAN
    settlement_style: Optional[str] = None  # PHYSICAL | CASH
    deliverable: Optional[Dict] = None      # {"security_id": ..., "quantity": shares per contract, "cash": extra cash per contract}
    exchange: Optional[str] = None
    listed: Optional[str] = None
    index_level_source: Optional[str] = None  # for cash-settled index options: security whose price * factor is the index level
    index_factor: float = 1.0
    yahoo: Optional[str] = None             # quote symbol when it differs from the id (BTC → BTC-USD, DXY → DX-Y.NYB)
    qty_step: Decimal = Decimal("1")        # smallest tradable quantity (coins trade in ten-thousandths)
    floating: bool = False                  # floating-rate note: the coupon resets to the short rate plus the issue spread

    @property
    def is_bond(self) -> bool:
        return self.asset_class in ("GOVT_BOND", "CORP_BOND")

    @property
    def is_future(self) -> bool:
        return self.asset_class == "FUTURE"

    @property
    def is_option(self) -> bool:
        return self.asset_class == "OPTION"


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
    # options
    is_option: bool = False
    greeks: Dict[str, float] = field(default_factory=dict)   # last mark's per-contract greeks and IV
    prev_greeks: Dict[str, float] = field(default_factory=dict)   # previous close's greeks (for approximate attribution)
    covered_by_shares: Decimal = ZERO       # short calls covered by long stock (contracts)
    margin_requirement: Decimal = ZERO
    # short book
    borrowed_quantity: Decimal = ZERO       # shares held under open securities loans
    borrow_fees: Decimal = ZERO
    manufactured_dividends: Decimal = ZERO
    buy_in_count: int = 0

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
    balance: Decimal = ZERO             # settled cash in local currency
    accrued_interest: Decimal = ZERO    # receivable(+)/payable(-) not yet settled, local currency
    is_base: bool = True
    _base_value: Decimal = ZERO

    @property
    def base_value(self) -> Decimal:
        """Carrying value in the base currency (= ledger 1010:CCY balance)."""
        return self.balance if self.is_base else self._base_value

    @base_value.setter
    def base_value(self, v: Decimal) -> None:
        if not self.is_base:
            self._base_value = v


@dataclass
class Locate:
    id: str
    portfolio_id: str
    security_id: str
    requested: Decimal
    available: Decimal
    rate: float
    lender: str
    date: str
    valid_through: str
    used: Decimal = ZERO
    status: str = "OPEN"                # OPEN | USED | EXPIRED | NONE


@dataclass
class SecurityLoan:
    id: str
    portfolio_id: str
    security_id: str
    lender: str
    borrower: str
    quantity: Decimal
    loan_date: str
    borrow_rate: float                  # annual fee on loan market value
    rebate_rate: float                  # earned on cash collateral
    collateral_type: str                # CASH | security id (Treasuries)
    collateralization: float            # e.g. 1.02
    market_value: Decimal = ZERO
    collateral_amount: Decimal = ZERO   # cash posted (CASH) or pledged face (security)
    collateral_value: Decimal = ZERO    # haircut-adjusted value of collateral
    accrued_fee: Decimal = ZERO
    accrued_rebate: Decimal = ZERO
    fees_paid: Decimal = ZERO
    recall_quantity: Decimal = ZERO
    recall_due: Optional[str] = None
    recall_status: str = "NONE"         # NONE | RECALLED | SATISFIED | BOUGHT_IN
    status: str = "OPEN"                # OPEN | RETURNED
    returned_quantity: Decimal = ZERO
    hold_until: str = ""
    rate_history: List[Dict] = field(default_factory=list)
    original_quantity: Decimal = ZERO


@dataclass
class RepoTrade:
    id: str
    portfolio_id: str
    side: str                           # REPO (we borrow cash) | REVERSE (we lend cash)
    counterparty: str
    security_id: str
    quantity: Decimal                   # face pledged / received
    start_date: str
    maturity: Optional[str]             # None = open
    term_type: str                      # OVERNIGHT | TERM | OPEN
    rate: float
    haircut: float
    principal: Decimal                  # cash borrowed / lent
    accrued_interest: Decimal = ZERO
    interest_paid: Decimal = ZERO
    collateral_mv: Decimal = ZERO
    required_collateral_mv: Decimal = ZERO
    cash_margin: Decimal = ZERO         # cash posted against a shortfall (REPO side)
    status: str = "OPEN"                # OPEN | CLOSED
    rolls: int = 0
    auto_roll: bool = True
    call_id: Optional[str] = None
    history: List[Dict] = field(default_factory=list)


@dataclass
class CollateralCall:
    id: str
    portfolio_id: str
    source: str                         # REPO | PRIME | SECLOAN
    reference: str                      # repo id / "PRIME" / loan id
    amount: Decimal
    issued: str
    due: str
    status: str                         # OPEN | MET | FORCED | CANCELLED
    reason: str = ""
    cycles_open: int = 0
    resolved: Optional[str] = None


@dataclass
class Pledge:
    reference: str                      # what it secures
    purpose: str                        # REPO | SECLOAN | PRIME
    security_id: str
    quantity: Decimal


@dataclass
class FXTrade:
    id: str
    portfolio_id: str
    buy_ccy: str
    sell_ccy: str
    buy_amount: Decimal
    sell_amount: Decimal
    rate: float                         # sell_ccy per 1 buy_ccy
    trade_date: str
    settlement_date: str
    status: str                         # PENDING | SETTLED
    counterparty: str = "Citi FX"
    usd_value: Decimal = ZERO
    realized_fx: Decimal = ZERO
    tag: Optional[str] = None           # the player's #tag (groups trades across desks)


@dataclass
class FXForward:
    id: str
    portfolio_id: str
    counterparty: str
    buy_ccy: str
    sell_ccy: str
    buy_amount: Decimal
    sell_amount: Decimal
    forward_rate: float                 # sell_ccy per 1 buy_ccy
    trade_date: str
    maturity: str
    mtm: Decimal = ZERO                 # base-currency mark
    status: str = "OPEN"                # OPEN | SETTLED
    tag: Optional[str] = None
    spot_at_trade: float = 0.0
    history: List[Dict] = field(default_factory=list)


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
    condition: Optional[Dict] = None          # {"ref": "NVDA"|"CURVE:10Y"|"SPOT:CL", "op": "<="|">=", "value": float}
    condition_met_date: Optional[str] = None
    execute_at: str = "OPEN"                  # OPEN | CLOSE: which print of the next session an instruction executes against
    execution: str = "NEXT_UPDATE"            # NEXT_UPDATE | LIVE (filled immediately at the latest real quote)
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
    broker: str = "Goldman Sachs (executing broker)"


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
    greek_attribution: Dict = field(default_factory=dict)


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
    # phase 2 books
    locates: Dict[str, Locate] = field(default_factory=dict)
    loans: Dict[str, SecurityLoan] = field(default_factory=dict)
    repos: Dict[str, RepoTrade] = field(default_factory=dict)
    pledges: List[Pledge] = field(default_factory=list)
    collateral_calls: Dict[str, CollateralCall] = field(default_factory=dict)
    cash_collateral: Dict[str, Decimal] = field(default_factory=dict)   # reference -> cash posted (base ccy)
    margin_loan: Decimal = ZERO
    margin_interest_accrued: Decimal = ZERO
    fx_trades: Dict[str, FXTrade] = field(default_factory=dict)
    fx_forwards: Dict[str, FXForward] = field(default_factory=dict)
    manufactured_payable: Decimal = ZERO
    collateral_received: Dict[str, Dict] = field(default_factory=dict)  # reference -> {security_id, quantity, value}
    strategies: Dict[str, Strategy] = field(default_factory=dict)
    otc_trades: Dict[str, Any] = field(default_factory=dict)     # OTCTrade (domain.otc_models)
    private_loans: Dict[str, Any] = field(default_factory=dict)  # PrivateLoan (engines.private_credit)
    rfqs: Dict[str, Any] = field(default_factory=dict)           # RFQ
    csas: Dict[str, Any] = field(default_factory=dict)           # dealer -> CSA
    risk_history: List[Dict] = field(default_factory=list)      # daily RISK_SNAPSHOT payloads
    fail_charges: Decimal = ZERO
    options_margin: Decimal = ZERO
    options_margin_detail: List[Dict] = field(default_factory=list)
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
    # phase 8/10 books: careers, investors, client franchise, lending desk, corporate treasury, AI-desk oversight
    missions: List[Dict] = field(default_factory=list)
    mission_state: Dict[str, Any] = field(default_factory=dict)
    investors: Dict[str, Any] = field(default_factory=dict)
    client_rfqs: Dict[str, Dict] = field(default_factory=dict)
    client_stats: Dict[str, Any] = field(default_factory=dict)
    lends: Dict[str, Dict] = field(default_factory=dict)
    lend_fees_earned: Decimal = ZERO
    treasury: Dict[str, Any] = field(default_factory=dict)
    desk_requests: Dict[str, Dict] = field(default_factory=dict)
    desk_limits: Dict[str, float] = field(default_factory=lambda: {"gross_mult": 1.0, "request_mult": 1.0, "var_mult": 1.0})
    desk_notes: List[Dict] = field(default_factory=list)
    # phase 9 commodity depth
    physical_delivery: bool = False
    physical_deliveries: List[Dict] = field(default_factory=list)
    storage_paid: Dict[str, Decimal] = field(default_factory=dict)

    def cash_account(self, ccy: str) -> CashAccount:
        if ccy not in self.cash:
            self.cash[ccy] = CashAccount(self.id, ccy, is_base=(ccy == self.base_currency))
        return self.cash[ccy]

    def pledged_quantity(self, security_id: str, exclude_ref: Optional[str] = None) -> Decimal:
        return sum((p.quantity for p in self.pledges if p.security_id == security_id and p.reference != exclude_ref), ZERO)

    def position(self, security_id: str) -> Position:
        if security_id not in self.positions:
            self.positions[security_id] = Position(self.id, security_id)
        return self.positions[security_id]


@dataclass
class Strategy:
    id: str
    portfolio_id: str
    strategy_type: str
    underlying: str
    legs: List[Dict]                    # [{"security_id", "side", "ratio", "quantity", "order_id"}]
    quantity: Decimal                   # number of strategy units
    net_limit: Optional[Decimal]        # net debit (+) / credit (-) limit per unit, None = market
    status: str                         # WORKING | FILLED | PARTIAL | CANCELLED | EXPIRED
    entered_date: str
    net_premium: Decimal = ZERO         # realised net premium per unit at fill (debit positive)
    notes: List[Dict] = field(default_factory=list)


@dataclass
class NewsItem:
    id: str
    date: str
    headline: str
    body: str
    category: str
    refs: List[str]
    event_id: str
    publisher: str = ""
    link: str = ""
    time: str = ""
