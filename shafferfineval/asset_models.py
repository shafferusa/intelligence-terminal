"""
ShafferFinEval -- Shaffer v1 production arithmetic for every asset class.

Pure stdlib. No Streamlit, no network, no data fetching: structured normalized
factor scores in, structured asset scores out.

These equations ARE production. The ML Lab may test, compare and propose
alternatives, but nothing here changes without an explicit version bump.

Every factor arrives already normalized to [-100, +100] (percentile rank,
z-score or an explicit transform -- see `normalize`). Raw units are never mixed
into a weighted equation. A factor with no data is DROPPED and the remaining
weights are renormalized; it never silently becomes zero.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Sequence

from statlib import (
    average_ranks,
    clamp,
    is_finite,
    percentile_rank_within,
    percentile_to_score,
)

SCORE_MIN, SCORE_MAX = -100.0, 100.0

# Direction labels: a positive score is never ambiguous.
LONG_ASSET = "Positive = bullish the asset"
LONG_BOND = "Positive = bullish bond TOTAL RETURN (not bullish yield)"
BUY_PROTECTION = "Positive = attractive to BUY protection"
LONG_VOL = "Positive = attractive to be LONG volatility"
BUY_OPTION = "Positive = attractive to BUY this option"
RECEIVE_FIXED = "Positive = attractive to RECEIVE fixed (bullish bonds)"
RECEIVE_TRS = "Positive = attractive to RECEIVE total return"


@dataclass
class FactorSpec:
    """One weighted term in a Shaffer v1 equation."""

    key: str
    label: str
    weight: float
    higher_is_better: bool = True
    children: Optional[list] = None          # nested sub-equation
    note: str = ""


@dataclass
class AssetModel:
    version: str
    name: str
    factors: list[FactorSpec]
    direction: str = LONG_ASSET
    scale: float = 1.0                       # applied after the blend
    note: str = ""

    @property
    def weight_total(self) -> float:
        return sum(f.weight for f in self.factors)


@dataclass
class FactorResult:
    key: str
    label: str
    score: Optional[float] = None
    weight_used: Optional[float] = None
    contribution: Optional[float] = None
    available: bool = False
    children: list = field(default_factory=list)
    note: str = ""


@dataclass
class AssetScore:
    """Uniform result for every asset class."""

    model_version: str
    model_name: str
    direction: str
    score: Optional[float] = None
    raw_score: Optional[float] = None
    factors: list[FactorResult] = field(default_factory=list)
    weights_used: dict = field(default_factory=dict)
    coverage: float = 0.0
    n_available: int = 0
    n_total: int = 0
    confidence: str = "LOW"
    missing: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def scored(self) -> bool:
        return self.score is not None


# --------------------------------------------------------------------------
# Normalization
# --------------------------------------------------------------------------

def normalize_cross_sectional(
    value, peer_values: Sequence[float], higher_is_better: bool = True
) -> Optional[float]:
    """Shaffer v1 cross-sectional normalization: percentile rank.

        higher-is-better: FactorScore = 200p - 100
        lower-is-better:  FactorScore = 100 - 200p

    Average rank for ties. Returns None when it cannot be ranked -- never 0,
    which would be a fabricated neutral reading.
    """
    p = percentile_rank_within(value, peer_values)
    return percentile_to_score(p, higher_is_better)


def normalize_time_series(
    value, history: Sequence[float], higher_is_better: bool = True,
    method: str = "percentile",
) -> Optional[float]:
    """Normalize against an asset's OWN history, for time-series factors.

    `percentile` ranks today against the history; `zscore` uses a tanh-squashed
    standard score so the result stays inside [-100, +100] without clipping a
    genuine extreme to the boundary.
    """
    if not is_finite(value):
        return None
    clean = [float(v) for v in history if is_finite(v)]
    if len(clean) < 3:
        return None

    if method == "zscore":
        mean = sum(clean) / len(clean)
        variance = sum((v - mean) ** 2 for v in clean) / max(len(clean) - 1, 1)
        std = variance ** 0.5
        if std <= 0:
            return None
        import math
        z = (float(value) - mean) / std
        score = 100.0 * math.tanh(z / 2.0)
        return score if higher_is_better else -score

    return normalize_cross_sectional(value, clean, higher_is_better)


def normalize_linear(value, low: float, high: float,
                     higher_is_better: bool = True) -> Optional[float]:
    """Map a bounded raw quantity onto [-100, +100] against explicit anchors.

    Used where an absolute scale is genuinely meaningful (an occupancy rate, a
    coverage ratio) rather than a peer comparison.
    """
    if not is_finite(value) or not is_finite(low) or not is_finite(high):
        return None
    if high == low:
        return None
    fraction = (float(value) - low) / (high - low)
    score = 200.0 * clamp(fraction, 0.0, 1.0) - 100.0
    return score if higher_is_better else -score


# --------------------------------------------------------------------------
# The blender: one implementation, every asset class
# --------------------------------------------------------------------------

def blend(model: AssetModel, values: dict) -> AssetScore:
    """Apply a Shaffer v1 equation to normalized factor scores.

    `values` maps factor key -> score in [-100, +100], or a nested dict for a
    sub-equation. Missing or unusable factors are dropped and the remaining
    weights renormalized over what is available.
    """
    result = AssetScore(
        model_version=model.version, model_name=model.name,
        direction=model.direction,
    )

    resolved: list[FactorResult] = []
    for spec in model.factors:
        if spec.children:
            child_model = AssetModel(model.version, spec.label, spec.children)
            child_values = values.get(spec.key) or {}
            if not isinstance(child_values, dict):
                child_values = {}
            child = blend(child_model, child_values)
            entry = FactorResult(
                key=spec.key, label=spec.label, score=child.score,
                available=child.score is not None,
                children=child.factors, note=spec.note,
            )
            if not entry.available:
                result.missing.append(spec.label)
            resolved.append(entry)
            continue

        raw = values.get(spec.key)
        score = float(raw) if is_finite(raw) else None
        if score is not None:
            score = clamp(score, SCORE_MIN, SCORE_MAX)
        entry = FactorResult(
            key=spec.key, label=spec.label, score=score,
            available=score is not None, note=spec.note,
        )
        if not entry.available:
            result.missing.append(spec.label)
        resolved.append(entry)

    result.factors = resolved
    result.n_total = len(resolved)
    available = [f for f in resolved if f.available]
    result.n_available = len(available)
    result.coverage = (len(available) / len(resolved)) if resolved else 0.0

    if not available:
        result.notes.append(
            f"No factor in {model.name} could be calculated from available data."
        )
        result.confidence = "LOW"
        return result

    spec_by_key = {s.key: s for s in model.factors}
    weight_total = sum(spec_by_key[f.key].weight for f in available)
    if weight_total <= 0:
        result.notes.append("No weighted factor available.")
        return result

    for entry in available:
        entry.weight_used = spec_by_key[entry.key].weight / weight_total
        entry.contribution = entry.weight_used * entry.score
        result.weights_used[entry.key] = entry.weight_used

    raw_score = sum(e.contribution for e in available)
    result.raw_score = clamp(raw_score, SCORE_MIN, SCORE_MAX)
    result.score = clamp(result.raw_score * model.scale, SCORE_MIN, SCORE_MAX)

    if result.missing:
        result.notes.append(
            "Dropped for missing data: " + ", ".join(result.missing)
            + f". Remaining weights renormalized over {weight_total:.2f}."
        )
    result.confidence = classify_confidence(result.coverage, len(available))
    return result


def classify_confidence(coverage: float, n_available: int) -> str:
    """Deterministic: coverage of the equation plus how many terms survived."""
    if coverage >= 0.85 and n_available >= 3:
        return "HIGH"
    if coverage >= 0.55 and n_available >= 2:
        return "MEDIUM"
    return "LOW"


# ==========================================================================
# THE MODELS -- Shaffer v1 production arithmetic
# ==========================================================================

EQUITY = AssetModel(
    "equity_shaffer_v1", "Common equity",
    [
        FactorSpec("valuation", "Valuation", 0.40),
        FactorSpec("growth", "Growth", 0.25),
        FactorSpec("profitability", "Profitability", 0.20),
        FactorSpec("debt", "Debt / financial strength", 0.15),
    ],
    scale=0.75,
    note="CompanyScore = 0.75 x (0.40V + 0.25G + 0.20P + 0.15D)",
)

REIT = AssetModel(
    "reit_shaffer_v1", "REIT",
    [
        FactorSpec("valuation", "Valuation", 0.35, children=[
            FactorSpec("p_affo", "P/AFFO vs peers", 0.45, higher_is_better=False),
            FactorSpec("nav", "Discount to NAV", 0.30),
            FactorSpec("cap_rate", "Implied cap rate vs peers", 0.25),
        ]),
        FactorSpec("growth", "Growth", 0.25, children=[
            FactorSpec("noi_growth", "NOI growth", 0.60),
            FactorSpec("affo_growth", "AFFO growth", 0.40),
        ]),
        FactorSpec("quality", "Quality", 0.20, children=[
            FactorSpec("occupancy", "Occupancy", 0.55),
            FactorSpec("affo_margin", "AFFO margin", 0.45),
        ]),
        FactorSpec("debt", "Debt", 0.20, children=[
            FactorSpec("net_debt_ebitda", "Net debt / EBITDA", 0.50,
                       higher_is_better=False),
            FactorSpec("interest_coverage", "Interest coverage", 0.30),
            FactorSpec("debt_market_cap", "Debt / market cap", 0.20,
                       higher_is_better=False),
        ]),
    ],
    note="Lower P/AFFO, larger NAV discount and higher cap rate all score positive.",
)

PREFERRED = AssetModel(
    "preferred_shaffer_v1", "Preferred stock",
    [
        FactorSpec("credit", "Issuer credit quality", 0.35),
        FactorSpec("yield_spread", "Yield / spread attractiveness", 0.30),
        FactorSpec("rates", "Rate environment", 0.20),
        FactorSpec("call_risk", "Call price / date risk", 0.15,
                   higher_is_better=False),
    ],
)

ETF = AssetModel(
    "etf_shaffer_v1", "ETF / index basket",
    [
        FactorSpec("underlying", "Weighted underlying Shaffer", 0.80),
        FactorSpec("breadth", "Breadth", 0.20, children=[
            FactorSpec("percent_positive", "Percent positive constituents", 0.40),
            FactorSpec("median_score", "Median constituent score", 0.30),
            FactorSpec("dispersion", "Dispersion", 0.15, higher_is_better=False),
            FactorSpec("participation", "Participation", 0.15),
        ]),
    ],
    note="Breadth is built from constituents, never a copy of the cap-weighted score.",
)

RATES = AssetModel(
    "rates_shaffer_v1", "Government bond / Treasury",
    [
        FactorSpec("central_bank", "Central-bank policy path", 0.27,
                   note="Easing expectation is positive for bond prices"),
        FactorSpec("inflation", "Inflation trajectory", 0.22,
                   note="Falling inflation is positive"),
        FactorSpec("growth", "Growth / labour trajectory", 0.18,
                   note="Weakening growth is generally positive for bonds"),
        FactorSpec("yield_level", "Yield vs historical regime", 0.13),
        FactorSpec("curve", "Curve / maturity value", 0.08),
        FactorSpec("policy", "Fiscal / sovereign policy", 0.12),
    ],
    direction=LONG_BOND,
)

CORP_CREDIT = AssetModel(
    "corp_credit_shaffer_v1", "Corporate bond",
    [
        FactorSpec("credit", "Credit strength", 0.32, children=[
            FactorSpec("net_debt_ebitda", "Net debt / EBITDA", 0.35,
                       higher_is_better=False),
            FactorSpec("interest_coverage", "Interest coverage", 0.25),
            FactorSpec("fcf_debt", "FCF / debt", 0.20),
            FactorSpec("equity_signal", "Issuer equity Shaffer signal", 0.20),
        ]),
        FactorSpec("spread", "Spread value", 0.23),
        FactorSpec("rates", "Rates", 0.18),
        FactorSpec("cash_flow", "Debt-service / cash-flow quality", 0.09),
        FactorSpec("technical", "Technical / liquidity", 0.08),
        FactorSpec("gpi", "Issuer geopolitical / policy impact", 0.10),
    ],
    direction=LONG_BOND,
)

MBS = AssetModel(
    "mbs_shaffer_v1", "Agency MBS",
    [
        FactorSpec("oas", "Option-adjusted spread value", 0.30),
        FactorSpec("rates", "Rate environment", 0.25),
        FactorSpec("prepayment", "Prepayment / refinancing risk", 0.20,
                   higher_is_better=False),
        FactorSpec("volatility", "Rate-volatility environment", 0.15,
                   higher_is_better=False),
        FactorSpec("carry", "Income / carry", 0.10),
    ],
    direction=LONG_BOND,
)

STRUCTURED = AssetModel(
    "structured_shaffer_v1", "Structured credit",
    [
        FactorSpec("spread", "Spread value", 0.30),
        FactorSpec("collateral", "Collateral quality", 0.25),
        FactorSpec("coverage", "Coverage / protection", 0.20),
        FactorSpec("structure", "Structure", 0.15),
        FactorSpec("liquidity", "Liquidity", 0.10),
    ],
    direction=LONG_BOND,
    note="Subtype raw features (CLO / CMBS / RMBS / ABS) are stored separately.",
)

FX = AssetModel(
    "fx_shaffer_v1", "FX pair",
    [
        FactorSpec("real_rate", "Real-rate differential", 0.25),
        FactorSpec("central_bank", "Relative central-bank path", 0.20),
        FactorSpec("growth", "Relative growth", 0.13),
        FactorSpec("current_account", "Current account / external balance", 0.09),
        FactorSpec("valuation", "PPP / REER valuation", 0.08),
        FactorSpec("carry", "Carry / forward points", 0.08),
        FactorSpec("gpi", "Relative geopolitical impact", 0.17),
    ],
    direction="Positive = BASE currency expected to strengthen vs QUOTE",
)

OIL = AssetModel(
    "oil_shaffer_v1", "Crude oil",
    [
        FactorSpec("inventories", "Inventories vs seasonal norm", 0.22,
                   higher_is_better=False),
        FactorSpec("supply", "Supply / OPEC / spare capacity", 0.17,
                   higher_is_better=False),
        FactorSpec("demand", "Demand", 0.17),
        FactorSpec("curve", "Curve (backwardation / contango)", 0.12),
        FactorSpec("refining", "Refining utilisation / cracks", 0.08),
        FactorSpec("usd", "US dollar", 0.07, higher_is_better=False),
        FactorSpec("gpi", "Geopolitical impact", 0.17),
    ],
)

NATGAS = AssetModel(
    "natgas_shaffer_v1", "Natural gas",
    [
        FactorSpec("storage", "Storage vs seasonal norm", 0.25,
                   higher_is_better=False),
        FactorSpec("weather", "Weather (HDD / CDD)", 0.17),
        FactorSpec("supply", "Production", 0.17, higher_is_better=False),
        FactorSpec("lng", "LNG export demand", 0.13),
        FactorSpec("curve", "Futures curve", 0.08),
        FactorSpec("demand", "Power / industrial demand", 0.05),
        FactorSpec("gpi", "Geopolitical impact", 0.15),
    ],
)

GOLD = AssetModel(
    "gold_shaffer_v1", "Gold / precious metals",
    [
        FactorSpec("real_yield", "Real yields", 0.27, higher_is_better=False),
        FactorSpec("usd", "US dollar", 0.18, higher_is_better=False),
        FactorSpec("flows", "ETF / central-bank flows", 0.13),
        FactorSpec("inflation", "Inflation expectations", 0.13),
        FactorSpec("risk", "Market stress", 0.09),
        FactorSpec("momentum", "Price momentum", 0.08),
        FactorSpec("gpi", "Safe-haven geopolitical demand", 0.12),
    ],
)

INDUSTRIAL_METAL = AssetModel(
    "industrial_metal_shaffer_v1", "Industrial metal",
    [
        FactorSpec("pmi", "Manufacturing cycle (PMI)", 0.23),
        FactorSpec("inventory", "Exchange inventory", 0.18, higher_is_better=False),
        FactorSpec("supply", "Mine / smelter supply", 0.17, higher_is_better=False),
        FactorSpec("china", "China industrial / property demand", 0.13),
        FactorSpec("usd", "US dollar", 0.09, higher_is_better=False),
        FactorSpec("curve", "Curve / physical tightness", 0.08),
        FactorSpec("gpi", "Geopolitical impact", 0.12),
    ],
)

AGRICULTURE = AssetModel(
    "ag_shaffer_v1", "Agriculture",
    [
        FactorSpec("stocks_use", "Stocks-to-use ratio", 0.27,
                   higher_is_better=False),
        FactorSpec("weather", "Crop weather", 0.22),
        FactorSpec("production", "Expected harvest", 0.18,
                   higher_is_better=False),
        FactorSpec("exports", "Export demand", 0.13),
        FactorSpec("curve", "Curve / seasonality", 0.08),
        FactorSpec("gpi", "Geopolitical impact", 0.12),
    ],
)

LIVESTOCK = AssetModel(
    "livestock_shaffer_v1", "Livestock",
    [
        FactorSpec("herd", "Herd size", 0.25, higher_is_better=False),
        FactorSpec("feed", "Feed costs", 0.20),
        FactorSpec("slaughter", "Slaughter rates", 0.20),
        FactorSpec("demand", "Demand", 0.15),
        FactorSpec("curve", "Curve", 0.10),
        FactorSpec("seasonality", "Seasonality", 0.10),
    ],
    note="No political factor in v1: not yet justified by evidence.",
)

CRYPTO = AssetModel(
    "crypto_shaffer_v1", "Crypto",
    [
        FactorSpec("liquidity", "System / stablecoin liquidity", 0.25),
        FactorSpec("momentum", "Momentum", 0.20),
        FactorSpec("flows", "ETF / exchange flows", 0.15),
        FactorSpec("network", "Network activity", 0.15),
        FactorSpec("leverage", "Funding / basis / OI", 0.15),
        FactorSpec("supply", "Issuance / unlocks", 0.10, higher_is_better=False),
    ],
    note="Network metrics are token-specific and never copied across tokens.",
)

VOLATILITY = AssetModel(
    "vol_shaffer_v1", "Volatility product",
    [
        FactorSpec("vol_value", "Implied vs expected realised", 0.30,
                   higher_is_better=False,
                   note="Cheap implied vol is attractive to buy"),
        FactorSpec("curve", "Vol term structure", 0.25),
        FactorSpec("stress", "Market stress", 0.20),
        FactorSpec("vol_of_vol", "Vol-of-vol", 0.15),
        FactorSpec("positioning", "Positioning / crowding", 0.10,
                   higher_is_better=False),
    ],
    direction=LONG_VOL,
)

FUTURES = AssetModel(
    "futures_shaffer_v1", "Futures contract",
    [
        FactorSpec("underlying", "Underlying Shaffer score", 0.85),
        FactorSpec("carry_curve", "Roll economics / basis", 0.10),
        FactorSpec("liquidity", "Liquidity", 0.05),
    ],
    note="Futures inherit the underlying; no independent economic model.",
)

OPTION = AssetModel(
    "option_shaffer_v1", "Listed option",
    [
        FactorSpec("direction", "Direction (underlying x option sign)", 0.55),
        FactorSpec("vol_value", "Implied vs expected realised vol", 0.25,
                   higher_is_better=False),
        FactorSpec("theta", "Decay per unit exposure", 0.10,
                   higher_is_better=False),
        FactorSpec("liquidity", "Liquidity", 0.10),
    ],
    direction=BUY_OPTION,
    note="Separate from the hedge-engine strategy score.",
)

STRUCTURED_SUBTYPES = ("CLO", "CMBS", "RMBS", "ABS")

MODELS = {
    "equity": EQUITY, "reit": REIT, "preferred": PREFERRED, "etf": ETF,
    "rates": RATES, "corp_credit": CORP_CREDIT, "mbs": MBS,
    "structured": STRUCTURED, "fx": FX, "oil": OIL, "natgas": NATGAS,
    "gold": GOLD, "industrial_metal": INDUSTRIAL_METAL, "agriculture": AGRICULTURE,
    "livestock": LIVESTOCK, "crypto": CRYPTO, "volatility": VOLATILITY,
    "futures": FUTURES, "option": OPTION,
}


# ==========================================================================
# DERIVATIVE OVERLAYS
#
# These inherit an underlying score rather than running an independent economic
# model, so a derivative can never contradict its own underlying.
# ==========================================================================

@dataclass
class DerivativeScore:
    model_version: str
    instrument: str
    direction: str
    score: Optional[float] = None
    underlying_score: Optional[float] = None
    overlay: Optional[float] = None
    components: dict = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def scored(self) -> bool:
        return self.score is not None


def _overlay_sum(components: dict, missing: list) -> float:
    total = 0.0
    for name, value in components.items():
        if is_finite(value):
            total += float(value)
        else:
            missing.append(name)
    return total


def score_cds(
    credit_strength, relative_value_overlay=None, carry_overlay=None,
    protection_side: str = "buy",
) -> DerivativeScore:
    """CDS. Positive BuyProtectionScore = attractive to BUY protection.

        BuyProtectionScore  = -CreditStrength + CDSRelativeValueOverlay
        SellProtectionScore = -BuyProtectionScore, plus carry/spread value

    A healthy issuer (high CreditStrength) makes protection unattractive to buy
    and attractive to sell, which is why the sign flips.
    """
    result = DerivativeScore(
        "cds_shaffer_v1", "CDS",
        BUY_PROTECTION if protection_side == "buy"
        else "Positive = attractive to SELL protection",
    )
    if not is_finite(credit_strength):
        result.missing.append("issuer credit strength")
        result.notes.append("No issuer credit score, so no CDS score.")
        return result

    result.underlying_score = float(credit_strength)
    rv = _overlay_sum({"CDS relative value": relative_value_overlay}, result.missing)
    buy = clamp(-float(credit_strength) + rv, SCORE_MIN, SCORE_MAX)

    if protection_side == "buy":
        result.overlay = rv
        result.score = buy
        result.components = {"credit_strength": float(credit_strength),
                             "relative_value": rv}
        return result

    carry = _overlay_sum({"carry / spread value": carry_overlay}, result.missing)
    result.overlay = carry
    result.score = clamp(-buy + carry, SCORE_MIN, SCORE_MAX)
    result.components = {"credit_strength": float(credit_strength),
                         "relative_value": rv, "carry": carry}
    result.notes.append(
        "Selling protection earns carry, so the sell score is not simply the "
        "negative of the buy score."
    )
    return result


def score_trs(underlying_score, carry_cost_overlay=None,
              side: str = "receive") -> DerivativeScore:
    """Total return swap.

        ReceiveTRS =  UnderlyingScore - CarryCostOverlay
        PayTRS     = -UnderlyingScore - CarryCostOverlay

    The carry cost is subtracted on BOTH sides: financing, dealer spread and
    collateral are paid whichever way the swap faces.
    """
    result = DerivativeScore(
        "trs_shaffer_v1", "Total return swap",
        RECEIVE_TRS if side == "receive"
        else "Positive = attractive to PAY total return",
    )
    if not is_finite(underlying_score):
        result.missing.append("underlying score")
        result.notes.append("TRS inherits its underlying; none was available.")
        return result

    cost = _overlay_sum({"carry / financing cost": carry_cost_overlay}, result.missing)
    result.underlying_score = float(underlying_score)
    result.overlay = -cost
    base = float(underlying_score) if side == "receive" else -float(underlying_score)
    result.score = clamp(base - cost, SCORE_MIN, SCORE_MAX)
    result.components = {"underlying": float(underlying_score), "carry_cost": cost}
    return result


def score_irs(rates_score, carry_overlay=None, side: str = "receive") -> DerivativeScore:
    """Interest rate swap.

        ReceiveFixed ~=  RatesScore      (benefits from falling rates)
        PayFixed     ~= -RatesScore      (benefits from rising rates)

    The rates engine is the underlying; the overlay carries curve, tenor, swap
    spread and liquidity.
    """
    result = DerivativeScore(
        "irs_shaffer_v1", "Interest rate swap",
        RECEIVE_FIXED if side == "receive"
        else "Positive = attractive to PAY fixed (bearish bonds)",
    )
    if not is_finite(rates_score):
        result.missing.append("rates score")
        result.notes.append("IRS inherits the rates engine; no score available.")
        return result

    overlay = _overlay_sum({"carry / curve / swap spread": carry_overlay},
                           result.missing)
    result.underlying_score = float(rates_score)
    result.overlay = overlay
    base = float(rates_score) if side == "receive" else -float(rates_score)
    result.score = clamp(base + overlay, SCORE_MIN, SCORE_MAX)
    result.components = {"rates": float(rates_score), "overlay": overlay}
    return result


def score_fx_forward(fx_score, forward_value=None) -> DerivativeScore:
    """FX forward / NDF.

        FXForward = 0.85 x FXScore + 0.15 x ForwardValue

    ForwardValue carries forward points, the interest differential and basis.
    """
    result = DerivativeScore("fx_forward_shaffer_v1", "FX forward / NDF",
                             "Positive = BASE expected to strengthen vs QUOTE")
    if not is_finite(fx_score):
        result.missing.append("FX score")
        return result

    result.underlying_score = float(fx_score)
    if is_finite(forward_value):
        result.score = clamp(0.85 * float(fx_score) + 0.15 * float(forward_value),
                             SCORE_MIN, SCORE_MAX)
        result.components = {"fx": float(fx_score), "forward_value": float(forward_value)}
    else:
        result.missing.append("forward value")
        result.score = clamp(float(fx_score), SCORE_MIN, SCORE_MAX)
        result.notes.append(
            "Forward value unavailable; the 0.15 weight renormalized onto the "
            "FX score alone."
        )
        result.components = {"fx": float(fx_score)}
    return result


def score_swaption(rates_score, vol_value=None, theta=None, liquidity=None,
                   side: str = "receiver") -> DerivativeScore:
    """Swaption / rate option, routed through rates direction + the option frame.

    A payer swaption benefits from RISING rates, so it takes the negative of the
    bond-bullish rates score; a receiver swaption takes it directly. No separate
    macro forecast is invented.
    """
    result = DerivativeScore(
        "option_shaffer_v1", f"{side.title()} swaption",
        "Positive = attractive to BUY this swaption",
    )
    if not is_finite(rates_score):
        result.missing.append("rates score")
        return result

    directional = float(rates_score) if side == "receiver" else -float(rates_score)
    result.underlying_score = directional
    values = {"direction": directional, "vol_value": vol_value,
              "theta": theta, "liquidity": liquidity}
    scored = blend(OPTION, values)
    result.score = scored.score
    result.overlay = None if scored.score is None else scored.score - directional
    result.components = values
    result.missing.extend(scored.missing)
    result.notes.extend(scored.notes)
    return result


def option_direction_score(underlying_score, right: str) -> Optional[float]:
    """The D term of the option equation: underlying score signed by option type.

    A call benefits from a bullish underlying; a put from a bearish one.
    """
    if not is_finite(underlying_score):
        return None
    sign = 1.0 if str(right).upper().startswith("C") else -1.0
    return clamp(sign * float(underlying_score), SCORE_MIN, SCORE_MAX)


def etf_breadth(constituent_scores: Sequence[float]) -> dict:
    """Breadth components from constituents -- never a copy of the cap-weighted score."""
    clean = [float(s) for s in constituent_scores if is_finite(s)]
    if len(clean) < 3:
        return {}
    positive = sum(1 for s in clean if s > 0) / len(clean)
    ordered = sorted(clean)
    median = ordered[len(ordered) // 2] if len(ordered) % 2 else (
        (ordered[len(ordered) // 2 - 1] + ordered[len(ordered) // 2]) / 2)
    mean = sum(clean) / len(clean)
    dispersion = (sum((s - mean) ** 2 for s in clean) / len(clean)) ** 0.5
    participation = sum(1 for s in clean if abs(s) > 10) / len(clean)
    return {
        "percent_positive": 200.0 * positive - 100.0,
        "median_score": clamp(median, SCORE_MIN, SCORE_MAX),
        "dispersion": clamp(200.0 * min(dispersion / 60.0, 1.0) - 100.0,
                            SCORE_MIN, SCORE_MAX),
        "participation": 200.0 * participation - 100.0,
    }


def weighted_underlying(scores: Sequence[float], weights: Sequence[float]) -> Optional[float]:
    """U = sum(w_i x ShafferScore_i), renormalized over constituents that scored."""
    pairs = [(float(s), float(w)) for s, w in zip(scores, weights)
             if is_finite(s) and is_finite(w) and w > 0]
    if not pairs:
        return None
    total = sum(w for _, w in pairs)
    if total <= 0:
        return None
    return clamp(sum(s * w for s, w in pairs) / total, SCORE_MIN, SCORE_MAX)


def bond_price_change(modified_duration, yield_change, convexity=None) -> Optional[float]:
    """Approximate price response to a yield move.

        dP/P ~= -D_mod x dy + 0.5 x Convexity x dy^2

    Used for projected bond returns; duration affects return MAGNITUDE and is
    deliberately kept out of the conviction score.
    """
    if not is_finite(modified_duration) or not is_finite(yield_change):
        return None
    change = -float(modified_duration) * float(yield_change)
    if is_finite(convexity):
        change += 0.5 * float(convexity) * float(yield_change) ** 2
    return change
