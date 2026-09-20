"""
ShafferFinEval -- equity hedge strategy engine.

Pure stdlib. No Streamlit, no network, no workbook reading: structured values
in, structured results out. The approved strategy universe arrives as parsed
`Strategy` objects from `strategy_catalog`; option chains arrive as
`OptionChain` from the data adapter.

Pipeline
--------
    FinalEquityScore + Position
      -> AdverseScore -> HedgeRatio -> HedgedShares/Notional
      -> eligible workbook strategies
      -> sized tickets (strikes, expiry, contracts, TRS notional, futures)
      -> seven-factor StrategyScore
      -> ranked list + preferred strategy + deterministic explanation
"""

from __future__ import annotations

import datetime as _dt
import math
from dataclasses import dataclass, field
from typing import Optional, Sequence

from statlib import clamp, is_finite
from strategy_catalog import (
    BUY,
    CALL,
    FUTURE,
    OPTION,
    PUT,
    SELL,
    STOCK,
    STRIKE_ATM,
    STRIKE_PCT,
    STRIKE_ZERO_COST,
    TRS,
    Strategy,
    StrategyLeg,
)

LONG = "long"
SHORT = "short"

#: Hedge-ratio curve: no hedge below an adverse score of 15, full hedge at 100.
HEDGE_FLOOR = 15.0
HEDGE_SPAN = 85.0

#: Dynamic strike selection: the widest OTM distance, at zero adverse score.
MAX_OTM_PCT = 0.15

#: The workbook's DEFAULT protective offsets. Only a leg sitting at its group
#: default is repriced by the equity score; a deliberately different strike
#: (the 2% tight put, the ATM put, the 17% tail hedge) is the product's
#: identity and is left alone, or every put strategy would collapse into one.
DEFAULT_PROTECTIVE_PCT = {PUT: -7.0, CALL: 10.0}

#: Downside/upside stress used to test whether a payoff genuinely hedges.
#: Set beyond the widest strike the workbook uses (a 20% tail-hedge call), so a
#: tail structure is measured where it actually pays rather than exactly at its
#: own strike, where its payoff is zero by construction.
STRESS_MOVE = 0.25
#: Overlay payoff at the stress point must beat this (as a fraction of spot).
MIN_HEDGE_PAYOFF = 0.01

CONTRACT_MULTIPLIER = 100

#: Option expiry targeting.
TARGET_DTE = 90
MIN_DTE = 60
MAX_DTE = 120

STRATEGY_WEIGHTS: dict[str, float] = {
    "effectiveness": 0.30,
    "cost": 0.20,
    "upside": 0.15,
    "liquidity": 0.10,
    "capital": 0.10,
    "tenor": 0.10,
    "basis": 0.05,
}

HIGH, MEDIUM, LOW = "HIGH", "MEDIUM", "LOW"


# --------------------------------------------------------------------------
# Inputs
# --------------------------------------------------------------------------

@dataclass
class Position:
    """The user's actual exposure."""

    ticker: str
    direction: str = LONG                 # long | short
    shares: float = 0.0                   # absolute quantity
    price: Optional[float] = None
    average_cost: Optional[float] = None

    @property
    def d(self) -> int:
        """+1 long, -1 short."""
        return 1 if self.direction == LONG else -1

    @property
    def market_value(self) -> Optional[float]:
        if not is_finite(self.price) or not is_finite(self.shares):
            return None
        return abs(float(self.shares)) * float(self.price)


@dataclass
class OptionQuote:
    """One listed contract. Every price field may legitimately be None."""

    strike: float
    right: str                            # P | C
    expiry: Optional[_dt.date] = None
    bid: Optional[float] = None
    ask: Optional[float] = None
    last: Optional[float] = None
    volume: Optional[float] = None
    open_interest: Optional[float] = None
    implied_volatility: Optional[float] = None
    delta: Optional[float] = None

    @property
    def mid(self) -> Optional[float]:
        if is_finite(self.bid) and is_finite(self.ask) and self.ask > 0:
            return (float(self.bid) + float(self.ask)) / 2
        if is_finite(self.last) and self.last > 0:
            return float(self.last)
        return None

    @property
    def spread_pct(self) -> Optional[float]:
        if not (is_finite(self.bid) and is_finite(self.ask)):
            return None
        mid = self.mid
        if not mid or mid <= 0:
            return None
        return (float(self.ask) - float(self.bid)) / mid


@dataclass
class OptionChain:
    """Listed contracts for one underlying, grouped by expiry."""

    ticker: str
    expiries: list[_dt.date] = field(default_factory=list)
    quotes: dict[_dt.date, list[OptionQuote]] = field(default_factory=dict)
    available: bool = False
    note: Optional[str] = None

    def strikes(self, expiry: _dt.date, right: str) -> list[float]:
        return sorted(
            q.strike for q in self.quotes.get(expiry, []) if q.right == right
        )

    def quote(self, expiry: _dt.date, right: str, strike: float) -> Optional[OptionQuote]:
        for q in self.quotes.get(expiry, []):
            if q.right == right and abs(q.strike - strike) < 1e-9:
                return q
        return None


@dataclass
class ProxyInstrument:
    """An index/ETF/futures hedge candidate."""

    symbol: str
    name: Optional[str] = None
    kind: str = "etf"                     # etf | future
    price: Optional[float] = None
    beta: Optional[float] = None          # position vs this instrument
    correlation: Optional[float] = None
    contract_multiplier: Optional[float] = None


@dataclass
class HedgeContext:
    """Everything the engine needs beyond the position itself."""

    equity_score: Optional[float] = None
    sector: Optional[str] = None
    industry: Optional[str] = None
    chain: Optional[OptionChain] = None
    proxies: list[ProxyInstrument] = field(default_factory=list)
    borrow_fee: Optional[float] = None         # annual, e.g. 0.004
    trs_financing_spread: Optional[float] = None
    as_of: Optional[_dt.date] = None


# --------------------------------------------------------------------------
# Outputs
# --------------------------------------------------------------------------

@dataclass
class TicketLeg:
    """One concrete, sized instruction."""

    action: str                           # BUY | SELL
    instrument: str                       # stock | option | trs | future
    description: str = ""
    right: Optional[str] = None
    target_strike: Optional[float] = None
    strike: Optional[float] = None
    strike_pct_from_spot: Optional[float] = None
    expiry: Optional[_dt.date] = None
    dte: Optional[int] = None
    contracts: Optional[int] = None
    shares: Optional[float] = None
    notional: Optional[float] = None
    premium: Optional[float] = None       # per share
    premium_total: Optional[float] = None
    delta: Optional[float] = None
    implied_volatility: Optional[float] = None
    open_interest: Optional[float] = None
    volume: Optional[float] = None
    missing: list[str] = field(default_factory=list)


@dataclass
class FactorScore:
    name: str
    score: Optional[float] = None
    available: bool = False
    note: str = ""


@dataclass
class StrategyEvaluation:
    """One eligible strategy, sized and scored."""

    key: str
    name: str
    group: str
    strategy_score: Optional[float] = None
    factors: dict[str, FactorScore] = field(default_factory=dict)
    weights_used: dict[str, float] = field(default_factory=dict)
    legs: list[TicketLeg] = field(default_factory=list)
    confidence: str = LOW
    missing: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    protected_shares: Optional[float] = None
    protected_notional: Optional[float] = None
    delta_equivalent_shares: Optional[float] = None
    effective_hedge_ratio: Optional[float] = None
    total_premium: Optional[float] = None
    summary: str = ""

    @property
    def scored(self) -> bool:
        return self.strategy_score is not None


@dataclass
class HedgeResult:
    """The full hedge recommendation for one position."""

    ticker: str
    equity_score: Optional[float] = None
    direction: str = LONG
    adverse_score: float = 0.0
    hedge_ratio: float = 0.0
    hedged_shares: float = 0.0
    hedge_notional: float = 0.0
    position_market_value: Optional[float] = None
    hedge_required: bool = False
    preferred: Optional[StrategyEvaluation] = None
    ranked: list[StrategyEvaluation] = field(default_factory=list)
    excluded: list[tuple[str, str]] = field(default_factory=list)
    explanation: str = ""
    notes: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------
# STEP 1-2 -- conflict and hedge sizing
# --------------------------------------------------------------------------

def calculate_adverse_score(equity_score, direction: str) -> float:
    """AdverseScore = max(0, -d * S).

    Long + negative score, or short + positive score, is adverse.
    """
    if not is_finite(equity_score):
        return 0.0
    d = 1 if direction == LONG else -1
    return max(0.0, -d * float(equity_score))


def calculate_recommended_hedge_ratio(adverse_score: float) -> float:
    """HedgeRatio = clamp((AdverseScore - 15) / 85, 0, 1)."""
    if not is_finite(adverse_score):
        return 0.0
    return clamp((float(adverse_score) - HEDGE_FLOOR) / HEDGE_SPAN, 0.0, 1.0)


def calculate_target_hedged_shares(shares: float, hedge_ratio: float) -> float:
    if not is_finite(shares) or not is_finite(hedge_ratio):
        return 0.0
    return abs(float(shares)) * float(hedge_ratio)


# --------------------------------------------------------------------------
# STEP 4 -- eligibility by payoff, not by name
# --------------------------------------------------------------------------

def _leg_strike(leg: StrategyLeg, spot: float) -> Optional[float]:
    if leg.strike_mode == STRIKE_ATM:
        return spot
    if leg.strike_mode == STRIKE_PCT and leg.strike_pct is not None:
        return spot * (1 + leg.strike_pct / 100.0)
    if leg.strike_mode == STRIKE_ZERO_COST:
        return None                       # resolved later against the chain
    return None


def _leg_payoff(leg: StrategyLeg, spot: float, terminal: float) -> float:
    """Intrinsic payoff per share at `terminal`, ignoring premium.

    Premium is excluded deliberately: eligibility must not depend on pricing
    data that may be unavailable.
    """
    sign = 1.0 if leg.side == BUY else -1.0
    size = sign * leg.ratio

    if leg.instrument in (STOCK, TRS, FUTURE):
        return size * (terminal - spot)

    if leg.instrument == OPTION:
        strike = _leg_strike(leg, spot)
        if strike is None:
            # zero-cost call: treat as a short call struck above spot so the
            # upside cap is represented rather than ignored.
            strike = spot * 1.10
        if leg.right == PUT:
            return size * max(strike - terminal, 0.0)
        return size * max(terminal - strike, 0.0)

    return 0.0


def overlay_legs(strategy: Strategy, direction: str) -> list[StrategyLeg]:
    """The hedge overlay: the strategy minus the position you already hold.

    A workbook long-stock strategy bundles the position leg ("buy stock") with
    its hedge. For someone who already owns the stock, the overlay is what is
    left after removing one such leg.
    """
    want = BUY if direction == LONG else SELL
    out: list[StrategyLeg] = []
    removed = False
    for leg in strategy.legs:
        if not removed and leg.instrument == STOCK and leg.side == want:
            removed = True
            continue
        out.append(leg)
    return out


def overlay_payoff(
    strategy: Strategy, direction: str, spot: float, move: float
) -> float:
    """Overlay payoff per share for a proportional move in the underlying."""
    terminal = spot * (1 + move)
    return sum(_leg_payoff(leg, spot, terminal) for leg in overlay_legs(strategy, direction))


def filter_eligible_strategies(
    catalog: Sequence[Strategy], direction: str, spot: float = 100.0
) -> tuple[list[Strategy], list[tuple[str, str]]]:
    """Keep only strategies whose overlay genuinely reduces the position's risk.

    The test is the payoff itself, not the strategy's name: the overlay must
    make money at the adverse stress point (-20% for a long, +20% for a short)
    by more than 1% of spot. That admits puts, put spreads, collars, short
    stock, pay-TRS, short futures and relative-value pairs, and it rejects
    covered calls, short strangles, iron condors and anything that adds to the
    existing exposure -- none of which meaningfully offset the risk.
    """
    eligible: list[Strategy] = []
    excluded: list[tuple[str, str]] = []
    adverse_move = -STRESS_MOVE if direction == LONG else STRESS_MOVE

    for strategy in catalog:
        if not strategy.parsed_cleanly:
            excluded.append((strategy.key, "legs could not be parsed"))
            continue

        overlay = overlay_legs(strategy, direction)
        if not overlay:
            excluded.append((strategy.key, "no hedge overlay once the position leg is removed"))
            continue

        # A pair trade needs a second name. The workbook defines the structure
        # but not which name to pair against, and shorting the position's own
        # ticker would misrepresent the strategy.
        if len([l for l in strategy.legs if l.instrument == STOCK]) > 1:
            excluded.append((
                strategy.key,
                "relative-value pair needs a second name, which the workbook "
                "does not specify; no pair-selection engine exists yet",
            ))
            continue

        payoff = overlay_payoff(strategy, direction, spot, adverse_move)
        if payoff <= MIN_HEDGE_PAYOFF * spot:
            excluded.append((
                strategy.key,
                f"overlay pays {payoff / spot:+.1%} of spot at a "
                f"{adverse_move:+.0%} move -- does not offset the position",
            ))
            continue

        # A hedge must not also add exposure in the direction already held.
        # A long straddle protects the downside but its call leg doubles the
        # bullish bet, which is a volatility view rather than a hedge.
        favourable_move = -adverse_move
        favourable = overlay_payoff(strategy, direction, spot, favourable_move)
        if favourable > MIN_HEDGE_PAYOFF * spot:
            excluded.append((
                strategy.key,
                f"overlay also gains {favourable / spot:+.1%} at a "
                f"{favourable_move:+.0%} move -- adds {direction} exposure "
                f"rather than hedging it",
            ))
            continue

        eligible.append(strategy)

    return eligible, excluded


# --------------------------------------------------------------------------
# STEP 6-8 -- option strike, expiry, sizing
# --------------------------------------------------------------------------

def calculate_option_target_strike(
    spot: float, adverse_score: float, right: str
) -> tuple[Optional[float], Optional[float]]:
    """Dynamic protective strike. Returns (target strike, OTM fraction).

        OTMPercent = 15% x (1 - AdverseScore / 100)

    A worse score pulls the protection closer to the money.
    """
    if not is_finite(spot) or float(spot) <= 0:
        return None, None
    otm = MAX_OTM_PCT * (1 - clamp(float(adverse_score), 0.0, 100.0) / 100.0)
    if right == PUT:
        return float(spot) * (1 - otm), -otm
    return float(spot) * (1 + otm), otm


def select_nearest_listed_strike(
    target: Optional[float], strikes: Sequence[float]
) -> Optional[float]:
    """Nearest actual listed strike to the theoretical target."""
    if not is_finite(target) or not strikes:
        return None
    return min(strikes, key=lambda k: abs(k - float(target)))


def select_target_expiry(
    expiries: Sequence[_dt.date],
    as_of: Optional[_dt.date] = None,
    target_dte: int = TARGET_DTE,
    min_dte: int = MIN_DTE,
    max_dte: int = MAX_DTE,
) -> tuple[Optional[_dt.date], Optional[int], str]:
    """Closest listed expiry to ~90 days, preferring the 60-120 day window.

    Returns (expiry, dte, reason).
    """
    today = as_of or _dt.date.today()
    dated = [(e, (e - today).days) for e in expiries if e and (e - today).days > 0]
    if not dated:
        return None, None, "no listed expiry beyond today"

    inside = [(e, d) for e, d in dated if min_dte <= d <= max_dte]
    if inside:
        expiry, dte = min(inside, key=lambda ed: abs(ed[1] - target_dte))
        return expiry, dte, (
            f"closest listed expiry to {target_dte} days inside the "
            f"{min_dte}-{max_dte} day hedge window"
        )

    expiry, dte = min(dated, key=lambda ed: abs(ed[1] - target_dte))
    return expiry, dte, (
        f"no listed expiry inside the {min_dte}-{max_dte} day window; "
        f"took the closest available at {dte} days"
    )


def calculate_option_contracts(protected_shares: float) -> int:
    """Whole contracts covering the protected shares, 100 shares each."""
    if not is_finite(protected_shares) or protected_shares <= 0:
        return 0
    return int(round(float(protected_shares) / CONTRACT_MULTIPLIER))


def calculate_delta_coverage(contracts: int, delta: Optional[float]) -> Optional[float]:
    """Share-equivalent exposure of an option leg. None when delta is unknown.

    This is INITIAL DELTA COVERAGE and is not the same as protection-floor
    coverage -- the caller must keep them distinct.
    """
    if not is_finite(delta) or not contracts:
        return None
    return contracts * CONTRACT_MULTIPLIER * abs(float(delta))


# --------------------------------------------------------------------------
# STEP 9-11 -- TRS, stock, proxy sizing
# --------------------------------------------------------------------------

def calculate_trs_notional(market_value: Optional[float], hedge_ratio: float) -> Optional[float]:
    if not is_finite(market_value) or not is_finite(hedge_ratio):
        return None
    return float(market_value) * float(hedge_ratio)


def calculate_trs_share_equivalent(
    notional: Optional[float], price: Optional[float]
) -> Optional[float]:
    if not is_finite(notional) or not is_finite(price) or float(price) <= 0:
        return None
    return float(notional) / float(price)


def trs_direction(position_direction: str) -> str:
    """Long equity is hedged by PAYING total return; short by RECEIVING it."""
    return "PAY TOTAL RETURN" if position_direction == LONG else "RECEIVE TOTAL RETURN"


def calculate_stock_hedge_size(hedged_shares: float) -> float:
    return abs(float(hedged_shares)) if is_finite(hedged_shares) else 0.0


def calculate_beta_adjusted_proxy_hedge(
    position_notional: Optional[float],
    hedge_ratio: float,
    beta: Optional[float],
) -> Optional[float]:
    """HedgeNotional = PositionNotional x HedgeRatio x Beta.

    Returns None when beta is unknown -- a dollar-matched proxy hedge would be
    a fabricated beta of 1.0.
    """
    if not is_finite(position_notional) or not is_finite(hedge_ratio):
        return None
    if not is_finite(beta):
        return None
    return float(position_notional) * float(hedge_ratio) * float(beta)


def calculate_futures_contracts(
    target_notional: Optional[float],
    futures_price: Optional[float],
    multiplier: Optional[float],
) -> Optional[int]:
    if not (is_finite(target_notional) and is_finite(futures_price) and is_finite(multiplier)):
        return None
    denominator = float(futures_price) * float(multiplier)
    if denominator <= 0:
        return None
    return int(round(float(target_notional) / denominator))


# --------------------------------------------------------------------------
# STEP 5 / 13 -- turn a workbook strategy into a concrete, sized ticket
# --------------------------------------------------------------------------

def _primary_protective_right(direction: str) -> str:
    """The right whose strike the equity score drives: puts for a long."""
    return PUT if direction == LONG else CALL


def build_strategy_ticket(
    strategy: Strategy,
    position: Position,
    context: HedgeContext,
    adverse_score: float,
    hedged_shares: float,
    hedge_ratio: float,
) -> StrategyEvaluation:
    """Size every leg of one strategy. Missing data is recorded, never invented."""
    evaluation = StrategyEvaluation(
        key=strategy.key, name=strategy.name, group=strategy.group
    )
    spot = position.price
    overlay = overlay_legs(strategy, position.direction)
    chain = context.chain
    market_value = position.market_value

    contracts = calculate_option_contracts(hedged_shares)
    expiry = dte = None
    expiry_reason = ""
    if any(leg.instrument == OPTION for leg in overlay):
        if chain and chain.available and chain.expiries:
            expiry, dte, expiry_reason = select_target_expiry(
                chain.expiries, context.as_of
            )
        else:
            evaluation.missing.append("option chain")
            evaluation.notes.append(
                (chain.note if chain and chain.note else
                 "No option chain available; strikes are theoretical targets and "
                 "premium, delta, IV and liquidity cannot be evaluated.")
            )

    protective_right = _primary_protective_right(position.direction)
    used_dynamic = False
    total_premium = 0.0
    premium_known = True
    delta_cover = None

    for leg in overlay:
        if leg.instrument == OPTION:
            ticket = TicketLeg(
                action=leg.side.upper(), instrument=OPTION, right=leg.right,
                expiry=expiry, dte=dte,
                contracts=int(round(contracts * leg.ratio)) if contracts else 0,
            )

            # The equity score drives the PRIMARY protective strike, but only
            # where the workbook used its group default offset.
            at_default = (
                leg.strike_mode == STRIKE_PCT
                and leg.strike_pct is not None
                and abs(leg.strike_pct - DEFAULT_PROTECTIVE_PCT[leg.right]) < 1e-9
            )
            if (leg.side == BUY and leg.right == protective_right
                    and at_default and not used_dynamic):
                target, otm = calculate_option_target_strike(
                    spot, adverse_score, leg.right
                )
                ticket.target_strike = target
                used_dynamic = True
                ticket.description = "primary protection (strike set by the equity score)"
            elif leg.strike_mode == STRIKE_ZERO_COST:
                ticket.target_strike = None
                ticket.description = "zero-cost strike (requires option pricing)"
                ticket.missing.append("zero-cost strike solve")
            else:
                ticket.target_strike = _leg_strike(leg, spot) if is_finite(spot) else None
                ticket.description = "workbook template strike"

            if chain and chain.available and expiry is not None:
                strikes = chain.strikes(expiry, leg.right)
                listed = select_nearest_listed_strike(ticket.target_strike, strikes)
                ticket.strike = listed
                quote = chain.quote(expiry, leg.right, listed) if listed else None
                if quote is not None:
                    ticket.premium = quote.mid
                    ticket.delta = quote.delta
                    ticket.implied_volatility = quote.implied_volatility
                    ticket.open_interest = quote.open_interest
                    ticket.volume = quote.volume
                    if quote.mid is None:
                        ticket.missing.append("premium")
                        premium_known = False
                    else:
                        signed = 1 if leg.side == BUY else -1
                        ticket.premium_total = (
                            signed * quote.mid * (ticket.contracts or 0) * CONTRACT_MULTIPLIER
                        )
                        total_premium += ticket.premium_total
                    if (leg.side == BUY and leg.right == protective_right
                            and quote.delta is not None):
                        delta_cover = calculate_delta_coverage(
                            ticket.contracts or 0, quote.delta
                        )
                else:
                    ticket.missing.extend(["premium", "delta", "implied volatility"])
                    premium_known = False
            else:
                ticket.missing.extend(["listed strike", "premium", "delta",
                                       "implied volatility"])
                premium_known = False

            reference = ticket.strike if ticket.strike is not None else ticket.target_strike
            if is_finite(reference) and is_finite(spot) and spot:
                ticket.strike_pct_from_spot = (reference - spot) / spot
            if ticket.contracts:
                ticket.shares = ticket.contracts * CONTRACT_MULTIPLIER
                if is_finite(reference):
                    ticket.notional = ticket.shares * reference
            evaluation.legs.append(ticket)

        elif leg.instrument == TRS:
            notional = calculate_trs_notional(market_value, hedge_ratio)
            shares = calculate_trs_share_equivalent(notional, spot)
            direction_label = trs_direction(position.direction)
            # A `sell trs` leg is the paying side; `buy trs` receives.
            label = ("PAY TOTAL RETURN" if leg.side == SELL else "RECEIVE TOTAL RETURN")
            evaluation.legs.append(TicketLeg(
                action=leg.side.upper(), instrument=TRS,
                description=f"{label} on {position.ticker}; "
                            f"{'receive' if leg.side == SELL else 'pay'} the financing leg",
                notional=notional, shares=shares,
                missing=[] if context.trs_financing_spread is not None
                        else ["dealer financing spread"],
            ))
            if context.trs_financing_spread is None:
                evaluation.missing.append("TRS financing spread")
            if label != direction_label and leg.side in (BUY, SELL):
                evaluation.notes.append(
                    f"This leg {label.lower()}s, which is the opposite of the "
                    f"{direction_label.lower()} leg a {position.direction} position "
                    f"hedges with; it is part of a combined structure."
                )

        elif leg.instrument == STOCK:
            shares = calculate_stock_hedge_size(hedged_shares) * leg.ratio
            ticket = TicketLeg(
                action=leg.side.upper(), instrument=STOCK,
                description=f"{leg.side} {position.ticker} shares",
                shares=shares,
                notional=shares * spot if is_finite(spot) else None,
            )
            if leg.side == SELL:
                if context.borrow_fee is None:
                    ticket.missing.append("borrow fee")
                    evaluation.missing.append("stock borrow fee")
                else:
                    ticket.description += f" (borrow {context.borrow_fee:.2%} annual)"
            evaluation.legs.append(ticket)

        elif leg.instrument == FUTURE:
            proxy = context.proxies[0] if context.proxies else None
            target_notional = calculate_beta_adjusted_proxy_hedge(
                market_value, hedge_ratio, proxy.beta if proxy else None
            )
            n = calculate_futures_contracts(
                target_notional,
                proxy.price if proxy else None,
                proxy.contract_multiplier if proxy else None,
            )
            ticket = TicketLeg(
                action=leg.side.upper(), instrument=FUTURE,
                description=(f"{leg.side} {proxy.symbol}" if proxy
                             else "index/futures proxy (no instrument supplied)"),
                contracts=n, notional=target_notional,
            )
            for label, value in (("proxy instrument", proxy),
                                 ("beta", proxy.beta if proxy else None),
                                 ("futures price", proxy.price if proxy else None),
                                 ("contract multiplier",
                                  proxy.contract_multiplier if proxy else None)):
                if value is None:
                    ticket.missing.append(label)
                    evaluation.missing.append(label)
            evaluation.legs.append(ticket)

    # Coverage: protection floor vs delta.
    option_legs = [l for l in evaluation.legs if l.instrument == OPTION]
    if option_legs:
        primary = next((l for l in option_legs if l.action == BUY.upper()
                        and l.right == protective_right), option_legs[0])
        evaluation.protected_shares = primary.shares
    elif any(l.instrument in (STOCK, TRS) for l in evaluation.legs):
        first = next(l for l in evaluation.legs if l.instrument in (STOCK, TRS))
        evaluation.protected_shares = first.shares

    if is_finite(evaluation.protected_shares) and is_finite(spot):
        evaluation.protected_notional = evaluation.protected_shares * spot
    if is_finite(evaluation.protected_shares) and position.shares:
        evaluation.effective_hedge_ratio = (
            evaluation.protected_shares / abs(position.shares)
        )
    evaluation.delta_equivalent_shares = delta_cover
    evaluation.total_premium = total_premium if premium_known and option_legs else None

    evaluation.summary = "; ".join(
        _leg_summary(l, position.ticker) for l in evaluation.legs
    )
    return evaluation


def _leg_summary(leg: TicketLeg, ticker: str) -> str:
    if leg.instrument == OPTION:
        strike = leg.strike if leg.strike is not None else leg.target_strike
        strike_text = f"${strike:,.2f}" if is_finite(strike) else "strike n/a"
        if leg.strike is None and is_finite(strike):
            strike_text += " (target)"
        expiry = leg.expiry.isoformat() if leg.expiry else "expiry n/a"
        right = "put" if leg.right == PUT else "call"
        return (f"{leg.action} {leg.contracts or 0} {ticker} {strike_text} "
                f"{right} {expiry}")
    if leg.instrument == TRS:
        notional = f"${leg.notional:,.0f}" if is_finite(leg.notional) else "notional n/a"
        return f"{leg.description.split(';')[0]} {notional}"
    if leg.instrument == STOCK:
        shares = f"{leg.shares:,.0f}" if is_finite(leg.shares) else "n/a"
        return f"{leg.action} {shares} {ticker} shares"
    if leg.instrument == FUTURE:
        if leg.contracts is None:
            return f"{leg.action} index futures (unsized: {', '.join(leg.missing)})"
        symbol = leg.description.split()[-1] if leg.description else "index"
        return f"{leg.action} {leg.contracts} {symbol} futures"
    return leg.description


# --------------------------------------------------------------------------
# STEP 12 -- seven-factor strategy score
# --------------------------------------------------------------------------

def ticket_payoff(evaluation: StrategyEvaluation, spot: float, move: float) -> float:
    """Overlay payoff per share using the strikes actually selected.

    Scoring must read the ticket, not the workbook template: the dynamic
    strike rule means the traded strike often differs from the default.
    """
    terminal = spot * (1 + move)
    total = 0.0
    for leg in evaluation.legs:
        sign = 1.0 if leg.action == BUY.upper() else -1.0
        if leg.instrument in (STOCK, TRS, FUTURE):
            ratio = 1.0
            if leg.instrument == STOCK and is_finite(leg.shares) and leg.shares:
                ratio = 1.0
            total += sign * ratio * (terminal - spot)
        elif leg.instrument == OPTION:
            strike = leg.strike if leg.strike is not None else leg.target_strike
            if not is_finite(strike):
                continue
            size = sign * ((leg.contracts or 0) * CONTRACT_MULTIPLIER)
            base = max(strike - terminal, 0.0) if leg.right == PUT else max(terminal - strike, 0.0)
            total += size * base
    return total


def _per_share(evaluation: StrategyEvaluation, position: Position,
               spot: float, move: float) -> float:
    """Ticket payoff expressed per position share, so it is comparable."""
    shares = abs(position.shares) or 1.0
    raw = ticket_payoff(evaluation, spot, move)
    option_legs = [l for l in evaluation.legs if l.instrument == OPTION]
    if option_legs:
        return raw / shares
    return raw * (evaluation.effective_hedge_ratio or 0.0)


def score_hedge_effectiveness(
    strategy: Strategy, position: Position, evaluation: StrategyEvaluation,
    context: HedgeContext, hedge_ratio: float,
) -> FactorScore:
    """How much of the adverse move the overlay actually offsets.

    Measured from the payoff itself, then scaled by how much of the position
    the sized hedge really covers, and by proxy correlation when the hedge is
    not on the same underlying.
    """
    spot = position.price
    if not is_finite(spot) or spot <= 0:
        return FactorScore("effectiveness", None, False, "no price")

    move = -STRESS_MOVE if position.direction == LONG else STRESS_MOVE
    payoff = _per_share(evaluation, position, spot, move)
    # Perfect = offsetting the whole adverse move on the shares we set out to
    # hedge, so a partial-notional hedge cannot look like a full one.
    denominator = STRESS_MOVE * spot * max(hedge_ratio, 1e-9)
    offset = payoff / denominator

    coverage = 1.0

    proxy_factor = 1.0
    note = "same-underlying hedge"
    if any(l.instrument == FUTURE for l in evaluation.legs):
        proxy = context.proxies[0] if context.proxies else None
        if proxy is None or proxy.correlation is None:
            return FactorScore(
                "effectiveness", None, False,
                "proxy hedge with no correlation estimate",
            )
        proxy_factor = clamp(abs(proxy.correlation), 0.0, 1.0)
        note = f"proxy hedge, correlation {proxy.correlation:.2f}"

    score = clamp(100.0 * clamp(offset, 0.0, 1.0) * coverage * proxy_factor, 0.0, 100.0)
    return FactorScore(
        "effectiveness", score, True,
        f"{offset:.0%} of the adverse move offset, {coverage:.0%} covered, {note}",
    )


#: Moneyness span over which the structural cost proxy decays to zero.
STRUCTURAL_COST_SPAN = 0.30


def _leg_size_ratio(leg: TicketLeg, evaluation: StrategyEvaluation) -> float:
    """A leg's contracts relative to the strategy's base option size."""
    option_legs = [l for l in evaluation.legs if l.instrument == OPTION and l.contracts]
    if not option_legs or not leg.contracts:
        return 1.0
    base = min(l.contracts for l in option_legs)
    return (leg.contracts / base) if base else 1.0


def _structural_cost_proxy(
    evaluation: StrategyEvaluation, position: Position
) -> FactorScore:
    """Relative option cost from moneyness alone, when no chain is available.

    Premium rises as a bought strike approaches spot, so `1 - |K-S|/S / 0.30`
    is a monotonic stand-in for "how much time value am I buying". Sold legs
    subtract, so a put spread scores cheaper than the outright put and a collar
    cheaper still. These are relative units, NOT dollars, and are never shown
    as a premium.
    """
    spot = position.price
    if not is_finite(spot) or spot <= 0:
        return FactorScore("cost", None, False, "no price for a cost comparison")

    units = 0.0
    for leg in evaluation.legs:
        if leg.instrument != OPTION:
            continue
        strike = leg.strike if leg.strike is not None else leg.target_strike
        if not is_finite(strike):
            # An unpriced zero-cost leg is designed to offset the bought leg.
            units -= 0.8 if leg.action == SELL.upper() else 0.0
            continue
        moneyness = abs(float(strike) - spot) / spot
        weight = max(0.0, 1.0 - moneyness / STRUCTURAL_COST_SPAN) * _leg_size_ratio(
            leg, evaluation
        )
        units += weight if leg.action == BUY.upper() else -weight

    score = clamp(100.0 * (1.0 - units), 0.0, 100.0)
    return FactorScore(
        "cost", score, True,
        f"structural estimate only ({units:.2f} premium units) -- no option "
        f"chain, so this is relative, not a priced cost",
    )


def score_cost_efficiency(
    evaluation: StrategyEvaluation, position: Position, context: HedgeContext
) -> FactorScore:
    """Cheaper is better. Unavailable pricing is reported, never assumed free."""
    option_legs = [l for l in evaluation.legs if l.instrument == OPTION]
    market_value = position.market_value

    if option_legs:
        if evaluation.total_premium is None:
            # No live pricing. Rather than call every option strategy equally
            # costless -- which would rank an ATM put above a put spread -- fall
            # back to a STRUCTURAL proxy built only from moneyness, which is a
            # property of the ticket we already know. No price is invented, the
            # note says so, and confidence drops to LOW.
            return _structural_cost_proxy(evaluation, position)
        if not is_finite(market_value) or not market_value:
            return FactorScore("cost", None, False, "no position market value")
        net_cost = evaluation.total_premium / market_value
        # 0% of notional -> 100; 8% or worse -> 0. Credits cap at 100.
        return FactorScore(
            "cost", clamp(100.0 * (1 - net_cost / 0.08), 0.0, 100.0), True,
            f"net premium {net_cost:+.2%} of position value",
        )

    if any(l.instrument == TRS for l in evaluation.legs):
        if context.trs_financing_spread is None:
            return FactorScore("cost", None, False, "TRS financing spread unavailable")
        spread = float(context.trs_financing_spread)
        return FactorScore(
            "cost", clamp(100.0 * (1 - spread / 0.03), 0.0, 100.0), True,
            f"financing spread {spread:.2%}",
        )

    if any(l.instrument == STOCK and l.action == SELL.upper() for l in evaluation.legs):
        if context.borrow_fee is None:
            return FactorScore("cost", None, False, "borrow fee unavailable")
        fee = float(context.borrow_fee)
        return FactorScore(
            "cost", clamp(100.0 * (1 - fee / 0.05), 0.0, 100.0), True,
            f"borrow {fee:.2%} annual",
        )

    if any(l.instrument == FUTURE for l in evaluation.legs):
        return FactorScore(
            "cost", 85.0, True,
            "futures: commissions and roll only, no premium outlay",
        )

    if any(l.instrument == STOCK for l in evaluation.legs):
        return FactorScore("cost", 90.0, True, "cash equity, commissions only")

    return FactorScore("cost", None, False, "no cost basis identified")


def score_upside_retention(
    strategy: Strategy, position: Position, evaluation: StrategyEvaluation
) -> FactorScore:
    """How much of the favourable move survives the hedge.

    A long put keeps the upside (only the premium is lost); short stock, pay
    TRS and short futures remove it on the hedged portion; a collar caps it;
    a put spread keeps it.
    """
    spot = position.price
    if not is_finite(spot) or spot <= 0:
        return FactorScore("upside", None, False, "no price")

    move = STRESS_MOVE if position.direction == LONG else -STRESS_MOVE
    payoff = _per_share(evaluation, position, spot, move)
    capture = 1 + payoff / (STRESS_MOVE * spot)
    return FactorScore(
        "upside", clamp(100.0 * capture, 0.0, 100.0), True,
        f"{clamp(capture, 0.0, 1.0):.0%} of a {abs(move):.0%} favourable move retained",
    )


def score_liquidity(evaluation: StrategyEvaluation, context: HedgeContext) -> FactorScore:
    """Bid/ask, volume and open interest where the chain provides them."""
    option_legs = [l for l in evaluation.legs if l.instrument == OPTION]
    if option_legs:
        chain = context.chain
        if not (chain and chain.available):
            return FactorScore("liquidity", None, False, "no option chain")
        ois = [l.open_interest for l in option_legs if is_finite(l.open_interest)]
        vols = [l.volume for l in option_legs if is_finite(l.volume)]
        if not ois and not vols:
            return FactorScore("liquidity", None, False, "no volume or open interest")
        oi = min(ois) if ois else 0.0
        vol = min(vols) if vols else 0.0
        oi_score = clamp(100.0 * math.log10(max(oi, 1)) / 4.0, 0.0, 100.0)
        vol_score = clamp(100.0 * math.log10(max(vol, 1)) / 3.5, 0.0, 100.0)
        return FactorScore(
            "liquidity", 0.6 * oi_score + 0.4 * vol_score, True,
            f"thinnest leg: {oi:,.0f} open interest, {vol:,.0f} traded",
        )

    if any(l.instrument == FUTURE for l in evaluation.legs):
        return FactorScore("liquidity", 90.0, True, "listed index futures")
    if any(l.instrument == STOCK for l in evaluation.legs):
        return FactorScore("liquidity", 85.0, True, "cash equity")
    if any(l.instrument == TRS for l in evaluation.legs):
        return FactorScore("liquidity", 45.0, True, "bilateral OTC, unwound at dealer price")
    return FactorScore("liquidity", None, False, "no instrument identified")


def score_capital_efficiency(
    evaluation: StrategyEvaluation, position: Position, context: HedgeContext
) -> FactorScore:
    """Cash, margin and collateral required to carry the hedge."""
    market_value = position.market_value
    option_legs = [l for l in evaluation.legs if l.instrument == OPTION]
    has_short_option = any(l.action == SELL.upper() for l in option_legs)

    if option_legs and not has_short_option:
        if evaluation.total_premium is None or not is_finite(market_value) or not market_value:
            return FactorScore("capital", None, False, "premium outlay unavailable")
        outlay = abs(evaluation.total_premium) / market_value
        return FactorScore(
            "capital", clamp(100.0 * (1 - outlay / 0.08), 0.0, 100.0), True,
            f"premium outlay {outlay:.2%} of position value, no margin",
        )
    if option_legs and has_short_option:
        return FactorScore(
            "capital", 60.0, True,
            "short option legs require margin against the written strikes",
        )
    if any(l.instrument == FUTURE for l in evaluation.legs):
        return FactorScore("capital", 80.0, True, "futures initial margin only")
    if any(l.instrument == TRS for l in evaluation.legs):
        return FactorScore("capital", 55.0, True, "CSA collateral against mark-to-market")
    if any(l.instrument == STOCK for l in evaluation.legs):
        return FactorScore("capital", 35.0, True, "short sale ties up margin and borrow")
    return FactorScore("capital", None, False, "no capital profile identified")


def score_tenor_fit(evaluation: StrategyEvaluation) -> FactorScore:
    """Does the hedge last about as long as the risk it covers."""
    dtes = [l.dte for l in evaluation.legs if l.dte is not None]
    if not dtes:
        if any(l.instrument in (TRS, STOCK, FUTURE) for l in evaluation.legs):
            return FactorScore(
                "tenor", 80.0, True,
                "open-ended exposure, closed whenever the view changes",
            )
        return FactorScore("tenor", None, False, "no expiry available")

    dte = min(dtes)
    if MIN_DTE <= dte <= MAX_DTE:
        score = 100.0 - 40.0 * abs(dte - TARGET_DTE) / (MAX_DTE - TARGET_DTE)
    elif dte < MIN_DTE:
        score = clamp(60.0 * dte / MIN_DTE, 0.0, 60.0)      # expires too soon
    else:
        score = clamp(60.0 - 30.0 * (dte - MAX_DTE) / MAX_DTE, 0.0, 60.0)
    return FactorScore("tenor", clamp(score, 0.0, 100.0), True,
                       f"{dte} days to expiry against a {TARGET_DTE}-day target")


def score_basis_quality(evaluation: StrategyEvaluation, context: HedgeContext) -> FactorScore:
    """Same-underlying instruments hedge company risk; proxies do not."""
    if any(l.instrument == FUTURE for l in evaluation.legs):
        proxy = context.proxies[0] if context.proxies else None
        if proxy is None or proxy.correlation is None:
            return FactorScore("basis", None, False, "proxy correlation unavailable")
        return FactorScore(
            "basis", clamp(100.0 * abs(proxy.correlation) ** 2, 0.0, 100.0), True,
            f"{proxy.symbol} proxy, r={proxy.correlation:.2f}; leaves "
            f"company-specific risk unhedged",
        )
    if any(l.instrument in (OPTION, STOCK, TRS) for l in evaluation.legs):
        return FactorScore("basis", 100.0, True, "same underlying, no basis risk")
    return FactorScore("basis", None, False, "no instrument identified")


def calculate_strategy_score(
    factors: dict[str, FactorScore]
) -> tuple[Optional[float], dict[str, float]]:
    """0.30E + 0.20C + 0.15U + 0.10L + 0.10K + 0.10T + 0.05B.

    An unavailable factor is dropped and the remaining weights renormalise --
    the same rule the scoring engines use. Confidence is reported separately
    and never silently adjusts this number.
    """
    available = {k: f for k, f in factors.items() if f.available and f.score is not None}
    if not available:
        return None, {}
    total = sum(STRATEGY_WEIGHTS[k] for k in available)
    if total <= 0:
        return None, {}
    weights = {k: STRATEGY_WEIGHTS[k] / total for k in available}
    return clamp(sum(weights[k] * available[k].score for k in available), 0.0, 100.0), weights


def classify_confidence(evaluation: StrategyEvaluation) -> str:
    """HIGH / MEDIUM / LOW from how much of the evaluation rested on real data."""
    for factor in evaluation.factors.values():
        if factor.available and "structural estimate only" in (factor.note or ""):
            return LOW
    n_available = sum(1 for f in evaluation.factors.values() if f.available)
    total = len(STRATEGY_WEIGHTS)
    if n_available == total and not evaluation.missing:
        return HIGH
    if n_available >= total - 2:
        return MEDIUM
    return LOW


# --------------------------------------------------------------------------
# Ranking, explanation, orchestration
# --------------------------------------------------------------------------

def rank_hedge_strategies(
    evaluations: list[StrategyEvaluation],
) -> list[StrategyEvaluation]:
    """Best first. Unscoreable strategies sort last, never silently dropped."""
    return sorted(
        evaluations,
        key=lambda e: (e.strategy_score is not None, e.strategy_score or 0.0),
        reverse=True,
    )


def explain_strategy_selection(
    result: HedgeResult, catalog_size: int = 0
) -> str:
    """Deterministic prose built from the computed factors. No LLM."""
    if not result.hedge_required:
        return (
            f"{result.ticker} carries a Shaffer Score of "
            f"{result.equity_score:+.1f} against a {result.direction} position, "
            f"an adverse score of {result.adverse_score:.0f}. That is below the "
            f"{HEDGE_FLOOR:.0f}-point threshold where the model starts hedging, "
            f"so no fundamental hedge is required. Optional insurance "
            f"structures remain available to inspect, but the model is not "
            f"asking for protection."
        ) if is_finite(result.equity_score) else (
            f"{result.ticker} has no Shaffer Score, so no hedge can be recommended."
        )

    preferred = result.preferred
    if preferred is None:
        return (
            f"{result.ticker} needs roughly {result.hedge_ratio:.0%} protection, "
            f"but no approved workbook strategy could be evaluated with the "
            f"data available."
        )

    parts = [
        f"The model prefers the {result.ticker} {preferred.name.lower()} because "
        f"the Shaffer Score of {result.equity_score:+.1f} is adverse to the "
        f"current {result.direction} position (adverse score "
        f"{result.adverse_score:.0f}), calling for about "
        f"{result.hedge_ratio:.0%} protection on "
        f"{result.hedged_shares:,.0f} shares "
        f"(${result.hedge_notional:,.0f})."
    ]

    effectiveness = preferred.factors.get("effectiveness")
    upside = preferred.factors.get("upside")
    cost = preferred.factors.get("cost")
    basis = preferred.factors.get("basis")

    descriptors = []
    if effectiveness and effectiveness.available:
        descriptors.append(f"offsets {effectiveness.note.split(' of the')[0]} of the adverse move")
    if basis and basis.available and basis.score is not None and basis.score >= 99:
        descriptors.append("hedges the name directly with no basis risk")
    if upside and upside.available and upside.score is not None:
        if upside.score >= 90:
            descriptors.append("preserves the upside")
        elif upside.score >= 40:
            descriptors.append("retains part of the upside")
        else:
            descriptors.append("gives up the upside on the hedged portion")
    if cost and cost.available and cost.note:
        descriptors.append(cost.note)
    if descriptors:
        parts.append("It " + ", ".join(descriptors) + ".")

    runner_up = next(
        (e for e in result.ranked[1:] if e.scored), None
    )
    if runner_up is not None and preferred.strategy_score is not None:
        gap = preferred.strategy_score - (runner_up.strategy_score or 0)
        reasons = []
        r_up = runner_up.factors.get("upside")
        r_cost = runner_up.factors.get("cost")
        r_eff = runner_up.factors.get("effectiveness")
        if (r_up and r_up.available and upside and upside.available
                and r_up.score is not None and upside.score is not None
                and r_up.score < upside.score - 10):
            reasons.append("it removes more of the upside")
        if (r_cost and not r_cost.available):
            reasons.append("its cost cannot be priced from available data")
        elif (r_cost and cost and r_cost.available and cost.available
              and r_cost.score is not None and cost.score is not None
              and r_cost.score < cost.score - 10):
            reasons.append("it costs more")
        if (r_eff and r_eff.available and effectiveness and effectiveness.available
                and r_eff.score is not None and effectiveness.score is not None
                and r_eff.score > effectiveness.score + 10):
            reasons.append("it offsets more of the move but at a worse trade-off elsewhere")
        tail = (" because " + " and ".join(reasons)) if reasons else ""
        parts.append(
            f"{runner_up.name} ranks next at "
            f"{runner_up.strategy_score:.0f} versus "
            f"{preferred.strategy_score:.0f} ({gap:+.0f})" + tail + "."
        )

    if preferred.confidence != HIGH and preferred.missing:
        parts.append(
            f"Confidence is {preferred.confidence} because "
            + ", ".join(sorted(set(preferred.missing)))
            + " could not be retrieved; those factors were dropped from the "
            "strategy score rather than assumed."
        )
    if catalog_size:
        parts.append(
            f"{len(result.ranked)} of {catalog_size} approved workbook "
            f"strategies passed the payoff eligibility test."
        )
    return " ".join(parts)


def recommend_hedge(
    position: Position,
    context: HedgeContext,
    catalog: Sequence[Strategy],
) -> HedgeResult:
    """Full pipeline: conflict -> ratio -> eligible strategies -> ranked tickets."""
    result = HedgeResult(
        ticker=position.ticker,
        equity_score=context.equity_score,
        direction=position.direction,
        position_market_value=position.market_value,
    )

    if not is_finite(context.equity_score):
        result.notes.append("No Shaffer Score available, so no hedge is recommended.")
        result.explanation = explain_strategy_selection(result, len(catalog))
        return result

    result.adverse_score = calculate_adverse_score(context.equity_score, position.direction)
    result.hedge_ratio = calculate_recommended_hedge_ratio(result.adverse_score)
    result.hedged_shares = calculate_target_hedged_shares(
        position.shares, result.hedge_ratio
    )
    result.hedge_notional = (
        result.hedged_shares * position.price if is_finite(position.price) else 0.0
    )
    result.hedge_required = result.hedge_ratio > 0

    if not catalog:
        result.notes.append(
            "The approved strategy catalog is empty (no workbook found); "
            "no strategies can be recommended."
        )
        result.explanation = explain_strategy_selection(result, 0)
        return result

    spot = position.price if is_finite(position.price) else 100.0
    eligible, excluded = filter_eligible_strategies(catalog, position.direction, spot)
    result.excluded = excluded

    if not result.hedge_required:
        # Still size the catalog so optional insurance can be inspected, but
        # the model is explicit that none of it is required.
        result.notes.append(
            f"Adverse score {result.adverse_score:.0f} is below the "
            f"{HEDGE_FLOOR:.0f}-point threshold: model-required hedge is 0%."
        )
        result.explanation = explain_strategy_selection(result, len(catalog))
        return result

    evaluations: list[StrategyEvaluation] = []
    for strategy in eligible:
        evaluation = build_strategy_ticket(
            strategy, position, context, result.adverse_score,
            result.hedged_shares, result.hedge_ratio,
        )
        evaluation.factors = {
            "effectiveness": score_hedge_effectiveness(
                strategy, position, evaluation, context, result.hedge_ratio),
            "cost": score_cost_efficiency(evaluation, position, context),
            "upside": score_upside_retention(strategy, position, evaluation),
            "liquidity": score_liquidity(evaluation, context),
            "capital": score_capital_efficiency(evaluation, position, context),
            "tenor": score_tenor_fit(evaluation),
            "basis": score_basis_quality(evaluation, context),
        }
        evaluation.strategy_score, evaluation.weights_used = calculate_strategy_score(
            evaluation.factors
        )
        evaluation.confidence = classify_confidence(evaluation)
        if evaluation.strategy_score is None:
            result.excluded.append(
                (strategy.key, "no factor could be evaluated from available data")
            )
            continue

        # Effectiveness carries 30% and is the whole point of a hedge. If it
        # cannot be measured, renormalising the other factors would REWARD the
        # strategy for missing exactly the data that would have marked it down.
        if not evaluation.factors["effectiveness"].available:
            result.excluded.append((
                strategy.key,
                "hedge effectiveness cannot be measured "
                f"({evaluation.factors['effectiveness'].note})",
            ))
            continue

        unsized = [l.instrument for l in evaluation.legs
                   if l.instrument == FUTURE and l.contracts is None]
        if unsized:
            result.excluded.append((
                strategy.key,
                "cannot be sized: no proxy instrument, beta or contract "
                "multiplier supplied for the futures leg",
            ))
            continue

        evaluations.append(evaluation)

    result.ranked = rank_hedge_strategies(evaluations)
    result.preferred = result.ranked[0] if result.ranked else None
    result.explanation = explain_strategy_selection(result, len(catalog))
    return result


HEDGE_MODEL_DETAILS = """\
**Position conflict**

    d = +1 long, -1 short
    AdverseScore = max(0, -d x ShafferScore)
    HedgeRatio   = clamp((AdverseScore - 15) / 85, 0, 1)

    HedgedShares  = |shares| x HedgeRatio
    HedgeNotional = HedgedShares x price

**Dynamic strike**

    OTMPercent  = 15% x (1 - AdverseScore / 100)
    TargetStrike = spot x (1 - OTMPercent)   for a protective put
                 = spot x (1 + OTMPercent)   for a protective call

The nearest ACTUAL listed strike is then selected, and both are shown.

**Eligibility**

A workbook strategy qualifies only if its overlay -- the strategy minus the
position leg you already hold -- makes money at the adverse stress point
(-20% for a long, +20% for a short) by more than 1% of spot. This is a payoff
test, not a name test: it admits puts, put spreads, collars, short stock,
pay-TRS, short futures and relative-value pairs, and rejects covered calls,
short strangles and iron condors.

**Strategy score**

    0.30 Effectiveness + 0.20 Cost + 0.15 Upside retained
  + 0.10 Liquidity + 0.10 Capital efficiency + 0.10 Tenor fit
  + 0.05 Basis quality

An unavailable factor is dropped and the remaining weights renormalise.
Confidence (HIGH/MEDIUM/LOW) is reported separately and never silently
changes the strategy score.
"""
