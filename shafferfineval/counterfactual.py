"""
ShafferFinEval -- counterfactual hedge simulation.

For every hedge decision the engine ranks several eligible strategies and
recommends one. Only the recommended one is ever traded, so without a
counterfactual record the ML Lab could never learn whether a different choice
would have done better.

This module stores what EVERY eligible strategy was at decision time, then
replays each one against the realised path. Results are labelled:

    ACTUAL                  a strategy that was really traded
    SIMULATED COUNTERFACTUAL  a strategy that was scored but not traded

The two are never blurred. A simulated payoff is intrinsic-value arithmetic on
the realised underlying move, not a claim about fills, slippage or liquidity.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional, Sequence

from statlib import clamp, is_finite

ACTUAL = "ACTUAL"
SIMULATED = "SIMULATED COUNTERFACTUAL"

CONTRACT_MULTIPLIER = 100


@dataclass
class HedgeOutcome:
    """What one hedge did, or would have done, over a realised path."""

    strategy_key: str
    strategy_name: str
    label: str = SIMULATED
    unhedged_return: Optional[float] = None
    hedged_return: Optional[float] = None
    hedge_pnl: Optional[float] = None
    underlying_pnl: Optional[float] = None
    hedge_cost: Optional[float] = None
    net_pnl: Optional[float] = None
    protection_benefit: Optional[float] = None
    net_protection_benefit: Optional[float] = None
    upside_sacrificed: Optional[float] = None
    downside_avoided: Optional[float] = None
    max_drawdown_unhedged: Optional[float] = None
    max_drawdown_hedged: Optional[float] = None
    drawdown_reduction: Optional[float] = None
    hedge_efficiency: Optional[float] = None
    missing: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def leg_payoff(leg: dict, spot: float, terminal: float) -> Optional[float]:
    """Intrinsic payoff of one stored ticket leg, in currency.

    Options use contracts x 100 x intrinsic; stock/TRS/futures are linear in
    the underlying move.
    """
    instrument = (leg.get("instrument") or "").lower()
    action = (leg.get("action") or "").upper()
    sign = 1.0 if action == "BUY" else -1.0

    if instrument == "option":
        strike = leg.get("strike") if leg.get("strike") is not None else leg.get("target_strike")
        contracts = leg.get("contracts") or 0
        if not is_finite(strike) or not contracts:
            return None
        right = (leg.get("right") or "").upper()
        intrinsic = (max(float(strike) - terminal, 0.0) if right.startswith("P")
                     else max(terminal - float(strike), 0.0))
        return sign * contracts * CONTRACT_MULTIPLIER * intrinsic

    if instrument in ("stock", "trs"):
        shares = leg.get("shares")
        if not is_finite(shares):
            return None
        return sign * float(shares) * (terminal - spot)

    if instrument == "future":
        notional = leg.get("notional")
        if not is_finite(notional) or spot <= 0:
            return None
        move = (terminal - spot) / spot
        return sign * float(notional) * move

    return None


def leg_cost(leg: dict) -> Optional[float]:
    """Cash cost of one leg at inception. Positive = paid out."""
    instrument = (leg.get("instrument") or "").lower()
    if instrument == "option":
        total = leg.get("premium_total")
        return float(total) if is_finite(total) else None
    if instrument == "trs":
        financing = leg.get("financing_cost")
        return float(financing) if is_finite(financing) else None
    if instrument == "stock" and (leg.get("action") or "").upper() == "SELL":
        borrow = leg.get("borrow_cost")
        return float(borrow) if is_finite(borrow) else None
    return 0.0


def simulate_hedge(
    strategy_key: str,
    strategy_name: str,
    legs: Sequence[dict],
    position_shares: float,
    entry_price: float,
    exit_price: float,
    direction: str = "long",
    path: Optional[Sequence[float]] = None,
    label: str = SIMULATED,
) -> HedgeOutcome:
    """Replay one hedge structure against a realised move.

    `path` is an optional price series for drawdown metrics; without it the
    drawdown fields stay unavailable rather than being guessed from endpoints.
    """
    outcome = HedgeOutcome(strategy_key, strategy_name, label)
    if not all(is_finite(x) for x in (position_shares, entry_price, exit_price)):
        outcome.missing.append("position, entry or exit price")
        return outcome
    if entry_price <= 0:
        outcome.missing.append("valid entry price")
        return outcome

    d = 1.0 if direction == "long" else -1.0
    shares = abs(float(position_shares))
    underlying_move = float(exit_price) - float(entry_price)
    underlying_pnl = d * shares * underlying_move
    outcome.underlying_pnl = underlying_pnl
    outcome.unhedged_return = d * (underlying_move / float(entry_price))

    hedge_pnl, cost, unknown = 0.0, 0.0, False
    for leg in legs or []:
        payoff = leg_payoff(leg, float(entry_price), float(exit_price))
        if payoff is None:
            unknown = True
            outcome.missing.append(
                f"{leg.get('instrument')} leg payoff (missing sizing or strike)")
            continue
        hedge_pnl += payoff
        leg_expense = leg_cost(leg)
        if leg_expense is None:
            unknown = True
            outcome.missing.append(f"{leg.get('instrument')} leg cost")
        else:
            cost += leg_expense

    if unknown and not legs:
        outcome.notes.append("No legs supplied; nothing to simulate.")
        return outcome

    outcome.hedge_pnl = hedge_pnl
    outcome.hedge_cost = cost if not unknown else None
    net = underlying_pnl + hedge_pnl - (cost if not unknown else 0.0)
    outcome.net_pnl = net
    notional = shares * float(entry_price)
    outcome.hedged_return = net / notional if notional else None

    # Protection benefit only means something when the position lost money.
    if underlying_pnl < 0:
        unhedged_loss = -underlying_pnl
        hedged_loss = max(-net, 0.0)
        outcome.protection_benefit = unhedged_loss - hedged_loss
        outcome.downside_avoided = outcome.protection_benefit
        if outcome.hedge_cost is not None:
            outcome.net_protection_benefit = outcome.protection_benefit - outcome.hedge_cost
            if outcome.hedge_cost > 0:
                outcome.hedge_efficiency = outcome.protection_benefit / outcome.hedge_cost
            else:
                outcome.notes.append(
                    "Hedge cost is zero or a credit, so hedge efficiency is "
                    "undefined rather than infinite.")
    else:
        outcome.upside_sacrificed = max(underlying_pnl - net, 0.0)

    if path and len(path) >= 2:
        outcome.max_drawdown_unhedged = _max_drawdown(
            [d * shares * (float(p) - float(entry_price)) for p in path])
        hedged_path = []
        for p in path:
            leg_total = 0.0
            for leg in legs or []:
                payoff = leg_payoff(leg, float(entry_price), float(p))
                if payoff is not None:
                    leg_total += payoff
            hedged_path.append(d * shares * (float(p) - float(entry_price)) + leg_total)
        outcome.max_drawdown_hedged = _max_drawdown(hedged_path)
        if (outcome.max_drawdown_unhedged is not None
                and outcome.max_drawdown_hedged is not None):
            outcome.drawdown_reduction = (
                outcome.max_drawdown_unhedged - outcome.max_drawdown_hedged)
    else:
        outcome.missing.append("price path for drawdown metrics")

    return outcome


def _max_drawdown(pnl_series: Sequence[float]) -> Optional[float]:
    """Largest peak-to-trough fall in a P&L series, as a positive number."""
    if not pnl_series:
        return None
    peak, worst = pnl_series[0], 0.0
    for value in pnl_series:
        peak = max(peak, value)
        worst = max(worst, peak - value)
    return worst


def simulate_all_strategies(
    stored_strategies: Sequence[dict],
    position_shares: float,
    entry_price: float,
    exit_price: float,
    direction: str = "long",
    path: Optional[Sequence[float]] = None,
    actual_key: Optional[str] = None,
) -> list[HedgeOutcome]:
    """Replay EVERY eligible strategy recorded at decision time.

    The one actually traded is labelled ACTUAL; the rest are SIMULATED
    COUNTERFACTUAL. This is what lets the Lab ask "what would each hedge have
    done?" without having traded all of them.
    """
    outcomes = []
    for entry in stored_strategies or []:
        key = entry.get("key") or entry.get("strategy_key") or "?"
        outcomes.append(simulate_hedge(
            key, entry.get("name") or key, entry.get("legs") or [],
            position_shares, entry_price, exit_price, direction, path,
            label=ACTUAL if (actual_key and key == actual_key) else SIMULATED,
        ))

    no_hedge = simulate_hedge(
        "NO_HEDGE", "No hedge", [], position_shares, entry_price, exit_price,
        direction, path, label=SIMULATED if actual_key else ACTUAL)
    no_hedge.notes.append("Baseline: what the unhedged position did.")
    outcomes.append(no_hedge)
    return outcomes


def rank_outcomes(outcomes: Sequence[HedgeOutcome],
                  objective: str = "net_pnl") -> list[HedgeOutcome]:
    """Rank realised/simulated outcomes. Unscoreable ones sort last."""
    def key(outcome):
        value = getattr(outcome, objective, None)
        return (value is not None, value or 0.0)
    return sorted(outcomes, key=key, reverse=True)


def hedge_research_row(outcome: HedgeOutcome, context: dict) -> dict:
    """Flatten one outcome plus its decision-time context into an ML row."""
    row = {
        "strategy_key": outcome.strategy_key,
        "label": outcome.label,
        "unhedged_return": outcome.unhedged_return,
        "hedged_return": outcome.hedged_return,
        "hedge_pnl": outcome.hedge_pnl,
        "hedge_cost": outcome.hedge_cost,
        "net_pnl": outcome.net_pnl,
        "protection_benefit": outcome.protection_benefit,
        "net_protection_benefit": outcome.net_protection_benefit,
        "upside_sacrificed": outcome.upside_sacrificed,
        "downside_avoided": outcome.downside_avoided,
        "drawdown_reduction": outcome.drawdown_reduction,
        "hedge_efficiency": outcome.hedge_efficiency,
    }
    row.update(context or {})
    return row


#: Decision-time features the hedge ML research needs, stored with every
#: recommendation so a counterfactual can be evaluated in its own context.
HEDGE_CONTEXT_FEATURES = (
    "shaffer_score", "adverse_score", "hedge_ratio", "realized_vol",
    "implied_vol", "iv_rv_spread", "iv_percentile", "beta", "correlation",
    "liquidity", "skew", "term_structure", "borrow_fee", "financing_spread",
    "position_notional", "holding_period_days", "asset_class", "gpi",
    "drawdown_state", "market_regime",
)
