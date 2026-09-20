"""
ShafferFinEval -- asset-class routers.

`score_asset` and `recommend_hedge_for` dispatch on asset class. Classes whose
models are not built return a structured `model_not_implemented` result rather
than a fabricated score, and the equity hedge engine is never forced onto a
non-equity.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Sequence

import company_scoring as comp
import hedging as hedge
import scoring
import universe as uni
from storage import EQUITY_MODEL_VERSION, HEDGE_MODEL_VERSION

IMPLEMENTED = "implemented"
NOT_IMPLEMENTED = "model_not_implemented"
NO_DATA = "no_data"

#: Asset class -> the engine that scores it. Everything absent is declared.
SCORE_ENGINES = {uni.EQUITY: EQUITY_MODEL_VERSION}
HEDGE_ENGINES = {uni.EQUITY: HEDGE_MODEL_VERSION}

MODEL_ROADMAP = {
    uni.ETF: "ETF score model not yet designed",
    uni.CRYPTO: "Crypto score model not yet designed",
    uni.INDEX: "Index score model not yet designed",
    uni.PREFERRED: "Preferred-stock score model not yet designed",
    uni.BOND: "Rates/credit score model not yet designed",
    uni.FUTURE: "Commodity/futures score model not yet designed",
    uni.FX: "FX score model not yet designed",
    uni.COMMODITY: "Commodity score model not yet designed",
    uni.OTC: "OTC derivative score model not yet designed",
    uni.CDS: "Credit score model not yet designed",
}


@dataclass
class ScoreResult:
    """Uniform score envelope across every asset class."""

    symbol: str
    asset_class: str
    status: str = NOT_IMPLEMENTED
    shaffer_score: Optional[float] = None
    classification: Optional[str] = None
    price: Optional[float] = None
    company_score: Optional[float] = None
    sector_overlay: Optional[float] = None
    sector_score: Optional[float] = None
    confidence: Optional[str] = None
    model_version: Optional[str] = None
    factor_scores: dict = field(default_factory=dict)
    raw_inputs: dict = field(default_factory=dict)
    detail: object = None                 # CompanyScoreResult for equities
    sector_detail: object = None
    message: str = ""

    @property
    def scored(self) -> bool:
        return self.status == IMPLEMENTED and self.shaffer_score is not None


def score_asset(
    asset_class: str,
    symbol: str,
    financials=None,
    universe_financials: Sequence = (),
    sector_score=None,
) -> ScoreResult:
    """Dispatch to the right scoring engine for this asset class."""
    if asset_class not in SCORE_ENGINES:
        return ScoreResult(
            symbol=symbol, asset_class=asset_class, status=NOT_IMPLEMENTED,
            message=MODEL_ROADMAP.get(
                asset_class, f"No score model for {asset_class}"
            ),
        )
    return _score_equity(symbol, financials, universe_financials, sector_score)


def _score_equity(symbol, financials, universe_financials, sector_score) -> ScoreResult:
    """Equity route: the existing company + sector engines, unchanged."""
    result = ScoreResult(
        symbol=symbol, asset_class=uni.EQUITY,
        model_version=EQUITY_MODEL_VERSION,
    )
    if financials is None:
        result.status = NO_DATA
        result.message = "No market/fundamental data could be retrieved."
        return result

    overlay = sector_score.overlay if sector_score is not None else None
    detail = comp.build_company_score(
        financials, list(universe_financials), sector_overlay=overlay
    )

    result.detail = detail
    result.sector_detail = sector_score
    result.price = financials.price
    result.company_score = detail.company_score
    result.sector_overlay = detail.sector_overlay
    result.sector_score = sector_score.raw_score if sector_score else None
    result.shaffer_score = detail.final_score
    result.classification = scoring.classify_score(detail.final_score)
    result.confidence = sector_score.confidence if sector_score else None

    if detail.final_score is None:
        result.status = NO_DATA
        result.message = " ".join(detail.notes) or "Company score unavailable."
        return result

    result.status = IMPLEMENTED
    result.factor_scores = {
        name: factor.score for name, factor in detail.factors.items()
    }
    result.factor_scores["sector_overlay"] = detail.sector_overlay
    result.factor_scores["company_score"] = detail.company_score
    for name, factor in detail.factors.items():
        for key, part in factor.components.items():
            result.factor_scores[key] = part.score
    result.raw_inputs = {
        "price": financials.price,
        "market_cap": financials.market_cap,
        "revenue": financials.revenue,
        "ebitda": financials.ebitda,
        "net_income": financials.net_income,
        "total_assets": financials.total_assets,
        "total_debt": financials.total_debt,
        "cash": financials.cash,
        "shares_outstanding": financials.shares_outstanding,
        "benchmark_level": detail.benchmark_level,
        "benchmark_ev_ebitda": detail.valuation.benchmark_ev_ebitda,
        "implied_price": detail.valuation.implied_price,
        "valuation_gap": detail.valuation.valuation_gap,
    }
    return result


def recommend_hedge_for(
    asset_class: str,
    position: hedge.Position,
    context: hedge.HedgeContext,
    catalog: Sequence,
) -> Optional[hedge.HedgeResult]:
    """Dispatch to the right hedge engine, or None when none exists.

    Deliberately returns None rather than running the equity hedge engine over
    a bond, a currency or a commodity.
    """
    if asset_class not in HEDGE_ENGINES:
        return None
    return hedge.recommend_hedge(position, context, catalog)


def hedge_engine_status(asset_class: str) -> str:
    if asset_class in HEDGE_ENGINES:
        return f"{asset_class} hedge engine: {HEDGE_ENGINES[asset_class]}"
    return f"No hedge engine for {asset_class} yet"
