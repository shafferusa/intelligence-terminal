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

import asset_models as am
import company_scoring as comp
import hedging as hedge
import political
import scoring
import universe as uni
from storage import EQUITY_MODEL_VERSION, HEDGE_MODEL_VERSION

IMPLEMENTED = "implemented"
NOT_IMPLEMENTED = "model_not_implemented"
NO_DATA = "no_data"

#: Asset class -> the Shaffer v1 engine that scores it.
#:
#: Every class now has production ARITHMETIC. What varies is whether this
#: project has DATA to feed it: equities are fed end to end from Yahoo, while
#: the rest need inputs (inventories, OPEC output, HDD, PMI, OAS, prepayment
#: speeds, CDS spreads) that no wired data source supplies. Those return a
#: structured "awaiting inputs" result rather than a fabricated score.
SCORE_ENGINES = {
    uni.EQUITY: EQUITY_MODEL_VERSION,
    uni.ETF: am.ETF.version,
    uni.PREFERRED: am.PREFERRED.version,
    uni.BOND: am.RATES.version,
    uni.FUTURE: am.FUTURES.version,
    uni.FX: am.FX.version,
    uni.COMMODITY: am.OIL.version,
    uni.CRYPTO: am.CRYPTO.version,
    uni.CDS: "cds_shaffer_v1",
    uni.OTC: "trs_shaffer_v1",
    uni.INDEX: am.ETF.version,
}

HEDGE_ENGINES = {uni.EQUITY: HEDGE_MODEL_VERSION}

#: Which model a workbook subclass routes to inside its asset class.
SUBCLASS_MODELS = {
    "reit": am.REIT, "stock": am.EQUITY, "adr (foreign stock)": am.EQUITY,
    "etf": am.ETF, "index (futures / options only)": am.ETF,
    "preferred stock": am.PREFERRED,
    "us treasury": am.RATES, "us treasury bill": am.RATES,
    "government bond (eur, de)": am.RATES, "government bond (gbp, gb)": am.RATES,
    "government bond (jpy, jp)": am.RATES, "government bond (eur, it)": am.RATES,
    "government bond (eur, fr)": am.RATES, "sovereign bond (usd)": am.RATES,
    "corporate / sovereign bond": am.CORP_CREDIT,
    "agency mbs (tba / pool)": am.MBS,
    "structured credit (clo / cmbs / rmbs / abs)": am.STRUCTURED,
    "commodity energy": am.OIL, "commodity metal": am.INDUSTRIAL_METAL,
    "commodity ag": am.AGRICULTURE, "commodity livestock": am.LIVESTOCK,
    "equity index": am.ETF, "rates": am.RATES, "volatility": am.VOLATILITY,
    "crypto": am.CRYPTO, "crypto (spot coin)": am.CRYPTO,
    "fx index": am.FX, "deliverable": am.FX, "non-deliverable (ndf)": am.FX,
}

#: Commodity codes that need a dedicated model rather than the group default.
COMMODITY_MODELS = {
    "CL": am.OIL, "BRN": am.OIL, "RB": am.OIL, "HO": am.OIL,
    "NG": am.NATGAS, "TTF": am.NATGAS,
    "GC": am.GOLD, "SI": am.GOLD, "PL": am.GOLD, "PA": am.GOLD,
    "HG": am.INDUSTRIAL_METAL, "ALI": am.INDUSTRIAL_METAL,
    "ZN": am.INDUSTRIAL_METAL, "NI": am.INDUSTRIAL_METAL,
}

#: Inputs each engine needs that NO wired data source currently supplies.
AWAITING_INPUTS = {
    am.RATES.version: "central-bank path, inflation trajectory, curve valuation",
    am.CORP_CREDIT.version: "issuer spreads vs rating/maturity cohort",
    am.MBS.version: "option-adjusted spread, prepayment speeds, rate vol",
    am.STRUCTURED.version: "deal-level collateral, OC/IC tests, DSCR/LTV",
    am.FX.version: "real-rate differentials, policy paths, current account",
    am.OIL.version: "inventories, OPEC output, refinery utilisation",
    am.NATGAS.version: "storage vs seasonal norm, HDD/CDD forecasts, LNG flows",
    am.GOLD.version: "real yields, ETF and central-bank flows",
    am.INDUSTRIAL_METAL.version: "PMI, exchange inventories, China demand",
    am.AGRICULTURE.version: "stocks-to-use, crop weather, export demand",
    am.LIVESTOCK.version: "herd counts, slaughter rates, feed costs",
    am.CRYPTO.version: "stablecoin liquidity, on-chain activity, funding/OI",
    am.VOLATILITY.version: "implied vs realised vol surface, term structure",
    am.PREFERRED.version: "issuer credit, yield/spread, call schedule",
    am.ETF.version: "constituent holdings and weights",
    "cds_shaffer_v1": "CDS spreads by tenor and issuer credit curve",
    "trs_shaffer_v1": "dealer financing spreads",
}

MODEL_ROADMAP = {
    uni.ETF: "ETF/basket arithmetic exists; awaiting constituent holdings data",
    uni.CRYPTO: "Crypto arithmetic exists; awaiting on-chain and flow data",
    uni.INDEX: "Index arithmetic exists; awaiting constituent data",
    uni.PREFERRED: "Preferred arithmetic exists; awaiting credit and call data",
    uni.BOND: "Rates/credit arithmetic exists; awaiting macro and spread data",
    uni.FUTURE: "Futures inherit an underlying; awaiting underlying inputs",
    uni.FX: "FX arithmetic exists; awaiting rate differential and policy data",
    uni.COMMODITY: "Commodity arithmetic exists; awaiting fundamental data",
    uni.OTC: "OTC overlays exist; awaiting dealer pricing",
    uni.CDS: "CDS overlay exists; awaiting spread data",
}


def model_for(asset_class: str, subclass=None, symbol=None):
    """Pick the Shaffer v1 model for one asset, by class then subclass."""
    if asset_class == uni.EQUITY:
        key = (subclass or "").lower()
        return am.REIT if key == "reit" else am.EQUITY
    if asset_class == uni.COMMODITY and symbol:
        model = COMMODITY_MODELS.get(str(symbol).upper())
        if model is not None:
            return model
    key = (subclass or "").lower()
    if key in SUBCLASS_MODELS:
        return SUBCLASS_MODELS[key]
    defaults = {
        uni.ETF: am.ETF, uni.INDEX: am.ETF, uni.PREFERRED: am.PREFERRED,
        uni.BOND: am.RATES, uni.FUTURE: am.FUTURES, uni.FX: am.FX,
        uni.COMMODITY: am.OIL, uni.CRYPTO: am.CRYPTO,
    }
    return defaults.get(asset_class)


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


AWAITING_DATA = "awaiting_inputs"


def score_asset(
    asset_class: str,
    symbol: str,
    financials=None,
    universe_financials: Sequence = (),
    sector_score=None,
    subclass: Optional[str] = None,
    factor_values: Optional[dict] = None,
    political_overlay: Optional[float] = None,
) -> ScoreResult:
    """Dispatch to the Shaffer v1 engine for this asset class.

    Equities run end to end from Yahoo. Every other class has production
    arithmetic but no wired data source for its inputs, so unless
    `factor_values` are supplied it returns a structured awaiting-inputs
    result naming exactly what is missing. Nothing is fabricated.
    """
    if asset_class not in SCORE_ENGINES:
        return ScoreResult(
            symbol=symbol, asset_class=asset_class, status=NOT_IMPLEMENTED,
            message=MODEL_ROADMAP.get(
                asset_class, f"No score model for {asset_class}"),
        )

    model = model_for(asset_class, subclass, symbol)

    if asset_class == uni.EQUITY and model is am.EQUITY:
        return _score_equity(symbol, financials, universe_financials,
                             sector_score, political_overlay)

    if model is None:
        return ScoreResult(
            symbol=symbol, asset_class=asset_class, status=NOT_IMPLEMENTED,
            message=f"No Shaffer v1 model mapped for {asset_class}/{subclass}",
        )

    return _score_generic(symbol, asset_class, model, factor_values,
                          political_overlay)


def _score_generic(symbol, asset_class, model, factor_values,
                   political_overlay=None) -> ScoreResult:
    """Run any Shaffer v1 equation over supplied normalized factor scores."""
    result = ScoreResult(
        symbol=symbol, asset_class=asset_class, model_version=model.version,
    )
    if not factor_values:
        result.status = AWAITING_DATA
        result.message = (
            f"{model.name} arithmetic ({model.version}) is implemented, but its "
            f"inputs are not wired to a data source yet: "
            f"{AWAITING_INPUTS.get(model.version, 'asset-specific fundamentals')}."
        )
        return result

    blended = am.blend(model, factor_values)
    result.detail = blended
    result.confidence = blended.confidence
    result.factor_scores = {
        f.key: f.score for f in blended.factors if f.score is not None}
    result.raw_inputs = dict(factor_values)

    if blended.score is None:
        result.status = NO_DATA
        result.message = " ".join(blended.notes) or "No factor could be scored."
        return result

    total = blended.score
    if political_overlay is not None:
        result.factor_scores["political_overlay"] = political_overlay
        total = max(-100.0, min(100.0, total + political_overlay))
    result.shaffer_score = total
    result.classification = scoring.classify_score(total)
    result.status = IMPLEMENTED
    result.message = blended.direction
    return result


def _score_equity(symbol, financials, universe_financials, sector_score,
                  political_overlay=None) -> ScoreResult:
    """Equity route: the existing company + sector engines, unchanged.

    The political overlay is added as a SEPARATE layer on top:

        FinalShafferEquityScore = CompanyScore + SectorOverlay + PoliticalOverlay

    The company arithmetic itself is untouched.
    """
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
    final = detail.final_score
    if final is not None and political_overlay is not None:
        final = max(-100.0, min(100.0, final + political_overlay))
        result.factor_scores["political_overlay"] = political_overlay
    result.shaffer_score = final
    result.classification = scoring.classify_score(final)
    result.confidence = sector_score.confidence if sector_score else None

    if detail.final_score is None:
        result.status = NO_DATA
        result.message = " ".join(detail.notes) or "Company score unavailable."
        return result

    result.status = IMPLEMENTED
    result.factor_scores.update({
        name: factor.score for name, factor in detail.factors.items()
    })
    result.factor_scores["sector_overlay"] = detail.sector_overlay
    if political_overlay is not None:
        result.factor_scores["political_overlay"] = political_overlay
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


def hedge_roadmap(asset_class: str) -> str:
    """What a future hedge engine for this class would use."""
    return {
        uni.BOND: "Treasury futures, interest-rate swaps and swaptions",
        uni.FX: "forwards, NDFs, FX options and futures",
        uni.COMMODITY: "futures, options and calendar spreads",
        uni.FUTURE: "offsetting futures and options on futures",
        uni.CDS: "CDS and duration-matched Treasury hedges",
        uni.OTC: "offsetting swaps and options",
        uni.ETF: "index options, futures and constituent hedges",
        uni.CRYPTO: "perpetuals, futures and options",
    }.get(asset_class, "no hedge engine planned yet")


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
    return (f"No hedge engine for {asset_class} yet. The equity engine is "
            f"deliberately NOT applied to it; a future engine would use "
            f"{hedge_roadmap(asset_class)}.")
