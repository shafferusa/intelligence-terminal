"""
ShafferFinEval -- trade and hedge-effectiveness research.

This is the fast evidence path. A 12-month return label needs twelve months; a
closed hedged position needs only the holding period. An NVDA position held 18
days and hedged with a put spread can be graded the moment it closes.

Two jobs:

    freeze_entry_state()    at trade entry, snapshot the contemporaneous
                            Shaffer state -- score, factors, GPI, prediction,
                            hedge recommendation and every model version.
    evaluate_closed()       once closed, compute realised outcomes AND replay
                            every alternative hedge against the same path.

The entry snapshot is immutable, so a decision is always judged on what was
known when it was made, never on revised data.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from typing import Optional, Sequence

import blotter
import counterfactual as CF
import prediction as pred
import storage
from statlib import is_finite

ENTRY, EXIT = "entry", "exit"

#: Alternative structures replayed for every closed hedged position. Templates
#: are resolved against the entry price, so each is what COULD have been traded
#: that day at the production engine's own strike rules.
ALTERNATIVES = (
    ("PROTECTIVE_PUT", "Protective put"),
    ("PUT_SPREAD_HEDGE", "Put spread"),
    ("COLLAR", "Collar"),
    ("SYNTHETIC_SHORT_TRS", "Pay TRS"),
    ("SHORT_FUTURES", "Futures hedge"),
    ("NO_HEDGE", "No hedge"),
)


@dataclass
class PositionOutcome:
    """A closed economic position, graded."""

    group_key: str
    symbol: Optional[str] = None
    asset_id: Optional[int] = None
    direction: str = "long"
    quantity: Optional[float] = None
    entry_date: Optional[str] = None
    exit_date: Optional[str] = None
    holding_days: Optional[int] = None
    entry_price: Optional[float] = None
    exit_price: Optional[float] = None
    entry_score: Optional[float] = None
    entry_prediction: Optional[float] = None
    entry_hedge: Optional[str] = None
    underlying_pnl: Optional[float] = None
    hedge_pnl: Optional[float] = None
    hedge_cost: Optional[float] = None
    net_pnl: Optional[float] = None
    unhedged_return: Optional[float] = None
    hedged_return: Optional[float] = None
    max_drawdown_unhedged: Optional[float] = None
    max_drawdown_hedged: Optional[float] = None
    upside_sacrificed: Optional[float] = None
    downside_avoided: Optional[float] = None
    hedge_efficiency: Optional[float] = None
    score_agreed_with_direction: Optional[bool] = None
    prediction_error: Optional[float] = None
    alternatives: list = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def freeze_entry_state(
    conn, trade, score_row=None, hedge_result=None, gpi=None
) -> str:
    """Freeze the Shaffer state at a trade's entry.

    Stores Trade_t + Score_t + Prediction_t + HedgeRecommendation_t. Returns
    "written", "exists" (already frozen, never overwritten) or "no_score".
    """
    if not trade.trade_id:
        return "no_trade_id"

    asset_id = trade.mapped_asset_id
    if score_row is None and asset_id is not None:
        score_row = storage.get_current_score(conn, asset_id)
    if score_row is None:
        # Still record the trade context so the gap is visible later.
        return storage.save_trade_snapshot(
            conn, trade.trade_id, ENTRY, asset_id=asset_id, price=trade.price,
            model_versions={"note": "no Shaffer score stored at entry"},
        )

    hedge_payload = None
    if hedge_result is not None and getattr(hedge_result, "preferred", None):
        preferred = hedge_result.preferred
        hedge_payload = {
            "preferred": preferred.name,
            "strategy_key": preferred.key,
            "strategy_score": preferred.strategy_score,
            "hedge_ratio": hedge_result.hedge_ratio,
            "adverse_score": hedge_result.adverse_score,
            "confidence": preferred.confidence,
            "summary": preferred.summary,
            # Every eligible alternative, so counterfactuals are possible later.
            "eligible": [
                {"key": e.key, "name": e.name, "score": e.strategy_score,
                 "legs": [_leg_dict(l) for l in e.legs]}
                for e in hedge_result.ranked
            ],
        }

    return storage.save_trade_snapshot(
        conn, trade.trade_id, ENTRY, asset_id=asset_id,
        price=score_row["price"] if "price" in score_row.keys() else trade.price,
        shaffer_score=score_row["shaffer_score"],
        factor_scores=storage.loads(score_row["factor_scores_json"]),
        raw_inputs=storage.loads(score_row["raw_inputs_json"]),
        gpi=gpi if gpi is not None else storage.loads(
            score_row["gpi_json"] if "gpi_json" in score_row.keys() else None),
        predicted_return=score_row["predicted_12m_return_pct"]
        if "predicted_12m_return_pct" in score_row.keys() else None,
        preferred_hedge=score_row["preferred_hedge"],
        hedge=hedge_payload,
        model_versions={
            "score": score_row["model_version"],
            "return_calibration": pred.RETURN_CALIBRATION_VERSION,
            "hedge": storage.HEDGE_MODEL_VERSION,
            "gpi": "gpi_v1",
        },
    )


def _leg_dict(leg) -> dict:
    """A ticket leg flattened for storage and later replay."""
    return {
        "instrument": leg.instrument, "action": leg.action, "right": leg.right,
        "strike": leg.strike, "target_strike": leg.target_strike,
        "contracts": leg.contracts, "shares": leg.shares,
        "notional": leg.notional, "premium_total": leg.premium_total,
        "expiry": leg.expiry.isoformat() if leg.expiry else None,
    }


def import_and_freeze(conn, source: str, hedge_fn=None) -> dict:
    """Import a FinSim blotter, persist it, group it, and freeze entry states.

    `hedge_fn(asset_row, trade) -> HedgeResult | None` lets the caller supply a
    live hedge recommendation to store alongside the entry score.
    """
    result = blotter.import_finsim_blotter(conn, source)
    summary = {
        "rows_read": result.rows_read, "mapped": result.mapped,
        "stored": 0, "groups": 0, "frozen": 0, "already_frozen": 0,
        "no_score": 0, "unmapped": sorted(set(result.unmapped)),
        "notes": list(result.notes),
    }
    if not result.trades:
        return summary

    summary["stored"] = storage.save_trades(conn, result.trades, source)["written"]

    groups = blotter.group_trades(result.trades)
    summary["groups"] = len(groups)
    for group in groups:
        ratio = blotter.hedge_ratio_for(group)
        storage.save_trade_group(
            conn, group.group_key,
            primary_trade=group.primary.trade_id if group.primary else None,
            relationship=group.relationship, strategy_tag=group.strategy_tag,
            hedge_ratio=ratio, note=group.note)
        for leg in group.hedges:
            storage.save_hedge_link(
                conn, group.group_key,
                group.primary.trade_id if group.primary else None, leg.trade_id,
                hedge_strategy=group.strategy_tag, relationship_type=group.relationship,
                hedge_ratio=ratio)

    for trade in result.trades:
        hedge_result = None
        if hedge_fn is not None and trade.mapped_asset_id is not None:
            try:
                hedge_result = hedge_fn(trade)
            except Exception:
                hedge_result = None
        state = freeze_entry_state(conn, trade, hedge_result=hedge_result)
        if state == "written":
            summary["frozen"] += 1
        elif state == "exists":
            summary["already_frozen"] += 1
        else:
            summary["no_score"] += 1
    return summary


# --------------------------------------------------------------------------
# Grading a closed position
# --------------------------------------------------------------------------

def evaluate_closed_position(
    conn, group_key: str, exit_price: float, exit_date: Optional[str] = None,
    price_path: Optional[Sequence[float]] = None,
    alternative_templates: Optional[Sequence[dict]] = None,
) -> PositionOutcome:
    """Grade one closed position and replay its alternatives.

    This is the fast-evidence path: it needs only the holding period, not a
    12-month wait.
    """
    outcome = PositionOutcome(group_key=group_key)
    group = conn.execute(
        "SELECT * FROM trade_groups WHERE group_key=?", (group_key,)).fetchone()
    if group is None:
        outcome.notes.append(f"No stored position '{group_key}'.")
        return outcome

    trades = conn.execute(
        """SELECT t.*, a.symbol AS universe_symbol FROM trades t
           LEFT JOIN assets a ON a.asset_id=t.asset_id
           WHERE t.trade_group_id=? ORDER BY t.trade_date""",
        (group_key.split(":")[-1],)).fetchall()
    if not trades:
        trades = conn.execute(
            """SELECT t.*, a.symbol AS universe_symbol FROM trades t
               LEFT JOIN assets a ON a.asset_id=t.asset_id
               WHERE t.trade_id=?""", (group["primary_trade"],)).fetchall()
    if not trades:
        outcome.notes.append("No trades found for this position.")
        return outcome

    primary = next((t for t in trades if t["trade_id"] == group["primary_trade"]),
                   trades[0])
    outcome.symbol = primary["universe_symbol"] or primary["source_symbol"]
    outcome.asset_id = primary["asset_id"]
    outcome.direction = ("long" if (primary["side"] or "").lower().startswith("b")
                         else "short")
    outcome.quantity = primary["quantity"]
    outcome.entry_date = (primary["trade_date"] or "")[:10]
    outcome.exit_date = exit_date or _dt.date.today().isoformat()
    outcome.entry_price = primary["price"]
    outcome.exit_price = exit_price

    if outcome.entry_date and outcome.exit_date:
        try:
            outcome.holding_days = (
                _dt.date.fromisoformat(outcome.exit_date)
                - _dt.date.fromisoformat(outcome.entry_date)).days
        except ValueError:
            pass

    snapshot = storage.get_trade_snapshot(conn, primary["trade_id"], ENTRY)
    if snapshot is not None:
        outcome.entry_score = snapshot["shaffer_score"]
        outcome.entry_prediction = snapshot["predicted_return"]
        outcome.entry_hedge = snapshot["preferred_hedge"]
    else:
        outcome.missing.append("entry Shaffer snapshot")
        outcome.notes.append(
            "No frozen entry state, so this decision cannot be graded against "
            "what was known at the time.")

    hedge_trades = [t for t in trades if t["trade_id"] != primary["trade_id"]]
    actual_legs = [_trade_to_leg(t) for t in hedge_trades]
    actual_key = (snapshot and storage.loads(snapshot["hedge_json"]) or {}).get(
        "strategy_key") if snapshot else None

    actual = CF.simulate_hedge(
        actual_key or "ACTUAL_HEDGE",
        outcome.entry_hedge or "As traded", actual_legs,
        outcome.quantity, outcome.entry_price, exit_price,
        outcome.direction, price_path, label=CF.ACTUAL)

    outcome.underlying_pnl = actual.underlying_pnl
    outcome.hedge_pnl = actual.hedge_pnl
    outcome.hedge_cost = actual.hedge_cost
    outcome.net_pnl = actual.net_pnl
    outcome.unhedged_return = actual.unhedged_return
    outcome.hedged_return = actual.hedged_return
    outcome.max_drawdown_unhedged = actual.max_drawdown_unhedged
    outcome.max_drawdown_hedged = actual.max_drawdown_hedged
    outcome.upside_sacrificed = actual.upside_sacrificed
    outcome.downside_avoided = actual.downside_avoided
    outcome.hedge_efficiency = actual.hedge_efficiency
    outcome.missing.extend(actual.missing)

    # Did the score agree with how the position was actually held?
    if is_finite(outcome.entry_score):
        d = 1 if outcome.direction == "long" else -1
        outcome.score_agreed_with_direction = (d * outcome.entry_score) > 0
    if is_finite(outcome.entry_prediction) and is_finite(outcome.unhedged_return):
        outcome.prediction_error = (
            outcome.unhedged_return * 100.0 - outcome.entry_prediction)

    # Replay the alternatives that were eligible at entry.
    stored_alternatives = alternative_templates
    if stored_alternatives is None and snapshot is not None:
        payload = storage.loads(snapshot["hedge_json"]) or {}
        stored_alternatives = payload.get("eligible")
    if stored_alternatives:
        outcome.alternatives = CF.simulate_all_strategies(
            stored_alternatives, outcome.quantity, outcome.entry_price,
            exit_price, outcome.direction, price_path, actual_key=actual_key)
    else:
        outcome.alternatives = [actual, CF.simulate_hedge(
            "NO_HEDGE", "No hedge", [], outcome.quantity, outcome.entry_price,
            exit_price, outcome.direction, price_path)]
        outcome.notes.append(
            "No eligible-strategy list was frozen at entry, so only the traded "
            "hedge and an unhedged baseline could be replayed.")

    for alternative in outcome.alternatives:
        storage.save_counterfactual(
            conn, group_key, outcome.exit_date, alternative.strategy_key,
            asset_id=outcome.asset_id, strategy_name=alternative.strategy_name,
            label=alternative.label, net_pnl=alternative.net_pnl,
            hedge_efficiency=alternative.hedge_efficiency,
            context={"entry_score": outcome.entry_score,
                     "direction": outcome.direction,
                     "holding_days": outcome.holding_days,
                     "entry_price": outcome.entry_price,
                     "exit_price": exit_price},
            outcome={"net_pnl": alternative.net_pnl,
                     "hedge_pnl": alternative.hedge_pnl,
                     "protection_benefit": alternative.protection_benefit,
                     "upside_sacrificed": alternative.upside_sacrificed,
                     "drawdown_reduction": alternative.drawdown_reduction})
    return outcome


def _field(row, name):
    """Read a column that may not exist on an older stored row."""
    try:
        return row[name]
    except (IndexError, KeyError):
        return None


def _trade_to_leg(trade) -> dict:
    """Turn a stored hedge trade into a replayable leg.

    An option leg is only replayable when the blotter actually told us the
    right and the strike. Neither is ever inferred: a call and a put are
    opposite trades, and an option's traded `price` is its premium, not its
    strike. A leg missing either comes back with those fields None, which
    `counterfactual.leg_payoff` reports as an unknown payoff rather than
    silently pricing a contract nobody traded.
    """
    asset_class = (trade["asset_class"] or "").lower()
    action = "BUY" if (trade["side"] or "").lower().startswith("b") else "SELL"
    if "option" in asset_class or asset_class in ("call", "put"):
        right = (_field(trade, "option_right") or "").upper() or None
        if right is None and asset_class in ("put", "call"):
            right = asset_class[0].upper()
        elif right is None and "put" in asset_class:
            right = "P"
        elif right is None and "call" in asset_class:
            right = "C"
        return {
            "instrument": "option", "action": action, "right": right,
            "strike": _field(trade, "strike"),
            "contracts": int(abs(trade["quantity"] or 0)),
            "premium_total": trade["net_cash"],
        }
    if "trs" in asset_class or "swap" in asset_class:
        return {"instrument": "trs", "action": action,
                "shares": abs(trade["quantity"] or 0),
                "financing_cost": trade["commission"]}
    if "future" in asset_class:
        return {"instrument": "future", "action": action,
                "notional": abs((trade["quantity"] or 0) * (trade["price"] or 0))}
    return {"instrument": "stock", "action": action,
            "shares": abs(trade["quantity"] or 0)}


def hedge_effectiveness_dataset(conn) -> list[dict]:
    """Rows for the hedge-effectiveness ML research.

    ACTUAL and SIMULATED COUNTERFACTUAL rows are both included and carry their
    label, so a model can be trained on counterfactuals while the distinction
    stays visible.
    """
    rows = []
    for record in storage.list_counterfactuals(conn):
        context = storage.loads(record["context_json"]) or {}
        outcome = storage.loads(record["outcome_json"]) or {}
        rows.append({
            "group_key": record["group_key"],
            "snapshot_date": record["snapshot_date"],
            "strategy_key": record["strategy_key"],
            "label": record["label"],
            "net_pnl": record["net_pnl"],
            "hedge_efficiency": record["hedge_efficiency"],
            **{k: v for k, v in context.items()},
            **{k: v for k, v in outcome.items() if k != "net_pnl"},
        })
    return rows


def trade_performance_summary(outcomes: Sequence[PositionOutcome]) -> dict:
    """Behavioural read on a book of closed positions.

    Answers questions about MY execution, not about the market: did trades
    aligned with the score do better, and did the hedges earn their cost.
    """
    graded = [o for o in outcomes if o.net_pnl is not None]
    if not graded:
        return {"positions": 0}

    agreed = [o for o in graded if o.score_agreed_with_direction is True]
    against = [o for o in graded if o.score_agreed_with_direction is False]
    hedged = [o for o in graded if o.hedge_pnl not in (None, 0.0)]

    def mean(values):
        clean = [v for v in values if is_finite(v)]
        return sum(clean) / len(clean) if clean else None

    return {
        "positions": len(graded),
        "mean_net_pnl": mean([o.net_pnl for o in graded]),
        "aligned_with_score": len(agreed),
        "against_score": len(against),
        "mean_pnl_aligned": mean([o.net_pnl for o in agreed]),
        "mean_pnl_against": mean([o.net_pnl for o in against]),
        "hedged_positions": len(hedged),
        "mean_hedge_cost": mean([o.hedge_cost for o in hedged]),
        "mean_hedge_efficiency": mean([o.hedge_efficiency for o in hedged]),
        # A position whose hedge leg could not be replayed has no hedged
        # drawdown. Treating that None as zero would report the whole
        # unhedged drawdown as a reduction the hedge never delivered.
        "mean_drawdown_reduction": mean(
            [o.max_drawdown_unhedged - o.max_drawdown_hedged for o in hedged
             if is_finite(o.max_drawdown_unhedged)
             and is_finite(o.max_drawdown_hedged)]),
        "drawdown_comparable_positions": sum(
            1 for o in hedged if is_finite(o.max_drawdown_unhedged)
            and is_finite(o.max_drawdown_hedged)),
        "mean_upside_sacrificed": mean([o.upside_sacrificed for o in hedged]),
        "mean_prediction_error_pp": mean([o.prediction_error for o in graded]),
    }


TRADE_RESEARCH_DETAILS = """\
**The fast evidence path.**

A 12-month return label needs twelve months. A closed hedged position needs
only its holding period — an 18-day NVDA position hedged with a put spread can
be graded the day it closes.

At entry the system freezes `Trade_t + Score_t + Prediction_t +
HedgeRecommendation_t`, including every eligible alternative strategy. That
snapshot is immutable, so the decision is judged on what was known then.

At close it computes the realised underlying P&L, hedge P&L, hedge cost, net
P&L, drawdown with and without the hedge, upside sacrificed and downside
avoided — then replays protective put, put spread, collar, pay-TRS, futures
hedge and no-hedge against the same path.

**ACTUAL** rows were traded. **SIMULATED COUNTERFACTUAL** rows were not, and
are intrinsic-value replays with no slippage or liquidity assumption.

My trades are not a random sample of the market, so this dataset is used for
execution, hedging and behaviour analysis — never to train a general
asset-return model.
"""
