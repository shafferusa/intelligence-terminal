"""
ShafferFinEval -- Geopolitical & Policy Impact (GPI v1).

Pure stdlib. Two deliberately separate concepts, never collapsed into one
vague "politics score":

    PoliticalRiskLevel  in [0, 100]    how significant is the EVENT
    PoliticalImpact     in [-100,+100] what it does to a SPECIFIC asset

The same event is bullish oil, bearish airlines, bullish gold, bearish one
currency and irrelevant to a software company. Severity is a property of the
event; impact is a property of the pairing.

    PoliticalImpact = EventSeverity x AssetExposure x Confidence

        EventSeverity in [0, 100]
        AssetExposure in [-1, +1]
        Confidence    in [0, 1]

This engine scores ECONOMIC CONSEQUENCES only. It does not score ideology,
parties, or voter preference, and nothing in it infers political opinion.
"""

from __future__ import annotations

import datetime as _dt
import math
from dataclasses import dataclass, field
from typing import Optional

from statlib import clamp, is_finite

GPI_VERSION = "gpi_v1"

#: Event severity equation. GPI = .30C + .20S + .20R + .15F + .10K + .05U
SEVERITY_WEIGHTS = {
    "conflict": 0.30,
    "sanctions": 0.20,
    "regulation": 0.20,
    "fiscal": 0.15,
    "capital_control": 0.10,
    "uncertainty": 0.05,
}

SEVERITY_LABELS = {
    "conflict": "Conflict / physical disruption",
    "sanctions": "Sanctions / trade restrictions",
    "regulation": "Regulation / policy",
    "fiscal": "Fiscal / sovereign policy",
    "capital_control": "Capital control / ownership risk",
    "uncertainty": "Policy uncertainty",
}

#: Asset exposure components, weighted into a single [-1, +1] exposure.
EXPOSURE_WEIGHTS = {
    "revenue": 0.35,
    "supply_chain": 0.25,
    "production": 0.20,
    "regulatory": 0.20,
}

#: Half-lives in days by event class. Impact decays as exp(-lambda t) unless
#: the event remains structurally active.
HALF_LIVES = {
    "headline": 5,
    "comment": 5,
    "proposed_regulation": 90,
    "enacted_law": 720,
    "sanctions": 540,
    "conflict": 180,
    "regime_change": 1825,
    "default": 60,
}

#: Persistent classes keep full severity while status is active.
PERSISTENT = {"sanctions", "conflict", "enacted_law", "regime_change"}

ACTIVE, RESOLVED = "active", "resolved"

#: Overlay bounds for equities. Default conservative; configurable wider.
OVERLAY_DEFAULT_CAP = 10.0
OVERLAY_MAX_CAP = 15.0


@dataclass
class PoliticalEvent:
    """One geopolitical or policy event."""

    event_id: Optional[int] = None
    event_type: str = "headline"
    description: str = ""
    start_time: Optional[str] = None
    last_updated: Optional[str] = None
    end_time: Optional[str] = None
    status: str = ACTIVE
    confidence: float = 0.5
    components: dict = field(default_factory=dict)

    @property
    def severity(self) -> Optional[float]:
        return event_severity(self.components)[0]


@dataclass
class SeverityResult:
    severity: Optional[float] = None
    components: dict = field(default_factory=dict)
    weights_used: dict = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)
    coverage: float = 0.0


@dataclass
class ImpactResult:
    """The impact of one event on one asset."""

    impact: Optional[float] = None
    severity: Optional[float] = None
    exposure: Optional[float] = None
    confidence: Optional[float] = None
    decayed_severity: Optional[float] = None
    age_days: Optional[float] = None
    half_life_days: Optional[int] = None
    exposure_components: dict = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def event_severity(components: dict) -> tuple[Optional[float], SeverityResult]:
    """GPI = .30C + .20S + .20R + .15F + .10K + .05U, each component in [0,100].

    Missing components are dropped and the remaining weights renormalized --
    a missing sanctions reading is not a zero-sanctions reading.
    """
    result = SeverityResult()
    available = {}
    for key, weight in SEVERITY_WEIGHTS.items():
        value = (components or {}).get(key)
        if is_finite(value):
            available[key] = clamp(float(value), 0.0, 100.0)
        else:
            result.missing.append(SEVERITY_LABELS[key])

    result.components = available
    result.coverage = len(available) / len(SEVERITY_WEIGHTS)
    if not available:
        return None, result

    total = sum(SEVERITY_WEIGHTS[k] for k in available)
    result.weights_used = {k: SEVERITY_WEIGHTS[k] / total for k in available}
    severity = sum(result.weights_used[k] * v for k, v in available.items())
    result.severity = clamp(severity, 0.0, 100.0)
    return result.severity, result


def asset_exposure(components: dict) -> Optional[float]:
    """Weighted asset exposure in [-1, +1].

    Each component is itself in [-1, +1]: positive means the event HELPS the
    asset, negative means it hurts. Same event, opposite signs for oil and
    airlines.
    """
    available = {}
    for key, weight in EXPOSURE_WEIGHTS.items():
        value = (components or {}).get(key)
        if is_finite(value):
            available[key] = clamp(float(value), -1.0, 1.0)
    if not available:
        return None
    total = sum(EXPOSURE_WEIGHTS[k] for k in available)
    if total <= 0:
        return None
    return clamp(
        sum((EXPOSURE_WEIGHTS[k] / total) * v for k, v in available.items()),
        -1.0, 1.0,
    )


def decay_factor(event_type: str, age_days: float, status: str = ACTIVE) -> float:
    """exp(-lambda t) with a class-specific half-life.

    A structurally active event (a running conflict, live sanctions) does not
    decay while it is active -- only once it resolves.
    """
    if not is_finite(age_days) or age_days < 0:
        return 1.0
    kind = (event_type or "default").lower()
    if status == ACTIVE and kind in PERSISTENT:
        return 1.0
    half_life = HALF_LIVES.get(kind, HALF_LIVES["default"])
    lam = math.log(2) / half_life
    return math.exp(-lam * float(age_days))


def age_in_days(start_time: Optional[str], as_of: Optional[_dt.date] = None) -> Optional[float]:
    if not start_time:
        return None
    try:
        start = _dt.date.fromisoformat(str(start_time)[:10])
    except (TypeError, ValueError):
        return None
    today = as_of or _dt.date.today()
    return max((today - start).days, 0)


def political_impact(
    event: PoliticalEvent,
    exposure_components: dict,
    as_of: Optional[_dt.date] = None,
) -> ImpactResult:
    """PoliticalImpact = DecayedSeverity x AssetExposure x Confidence.

    Result is in [-100, +100]. Any missing piece leaves the impact
    unavailable rather than assumed neutral.
    """
    result = ImpactResult()

    severity, severity_detail = event_severity(event.components)
    result.severity = severity
    result.missing.extend(severity_detail.missing)
    if severity is None:
        result.notes.append("Event severity could not be scored.")
        return result

    exposure = asset_exposure(exposure_components)
    result.exposure = exposure
    result.exposure_components = dict(exposure_components or {})
    if exposure is None:
        result.missing.append("asset exposure")
        result.notes.append(
            "No exposure mapping for this asset, so the event has no scored "
            "impact on it. That is different from an impact of zero."
        )
        return result

    confidence = event.confidence if is_finite(event.confidence) else None
    if confidence is None:
        result.missing.append("event confidence")
        result.notes.append("No confidence supplied.")
        return result
    confidence = clamp(float(confidence), 0.0, 1.0)
    result.confidence = confidence

    age = age_in_days(event.start_time, as_of)
    result.age_days = age
    result.half_life_days = HALF_LIVES.get(
        (event.event_type or "default").lower(), HALF_LIVES["default"])
    factor = decay_factor(event.event_type, age if age is not None else 0.0,
                          event.status)
    result.decayed_severity = severity * factor
    if event.status == ACTIVE and (event.event_type or "").lower() in PERSISTENT:
        result.notes.append(
            f"{event.event_type} is structurally active, so severity does not "
            f"decay while it remains so."
        )

    result.impact = clamp(
        result.decayed_severity * exposure * confidence, -100.0, 100.0)
    return result


def aggregate_impacts(impacts: list[ImpactResult]) -> Optional[float]:
    """Combine several events on one asset.

    Events are additive but saturating: two moderate shocks compound, ten do
    not sum to an impossible number. tanh keeps the aggregate inside the band.
    """
    usable = [i.impact for i in impacts if i.impact is not None]
    if not usable:
        return None
    total = sum(usable)
    return clamp(100.0 * math.tanh(total / 100.0), -100.0, 100.0)


def political_overlay(
    aggregate_impact: Optional[float], cap: float = OVERLAY_DEFAULT_CAP
) -> Optional[float]:
    """Scale an asset's aggregate political impact into the score overlay.

    Default band is [-10, +10]; configurable to [-15, +15]. Deliberately small:
    the overlay should only move companies with real exposure, and must never
    swamp the fundamental arithmetic.
    """
    if not is_finite(aggregate_impact):
        return None
    bound = clamp(abs(float(cap)), 0.0, OVERLAY_MAX_CAP)
    return clamp(float(aggregate_impact) / 100.0 * bound, -bound, bound)


def fx_relative_gpi(base_impact, quote_impact) -> Optional[float]:
    """GPI_pair = GPI_base - GPI_quote.

    A currency pair is a relative trade, so its political factor is the
    difference between the two countries' impacts, not either one alone.
    """
    base = float(base_impact) if is_finite(base_impact) else None
    quote = float(quote_impact) if is_finite(quote_impact) else None
    if base is None and quote is None:
        return None
    return clamp((base or 0.0) - (quote or 0.0), -100.0, 100.0)


#: Political raw features stored for ML research, so the Lab can test WHICH
#: political input mattered rather than only seeing the final overlay.
POLITICAL_FEATURES = (
    "conflict", "sanctions", "regulation", "fiscal", "capital_control",
    "uncertainty", "event_severity", "event_confidence", "revenue_exposure",
    "production_exposure", "supply_chain_exposure", "regulatory_exposure",
    "net_exposure", "event_age_days", "decayed_severity", "political_impact",
)


def political_feature_row(event: PoliticalEvent, impact: ImpactResult) -> dict:
    """Flatten one event/asset pairing into ML features."""
    exposure = impact.exposure_components or {}
    return {
        "conflict": event.components.get("conflict"),
        "sanctions": event.components.get("sanctions"),
        "regulation": event.components.get("regulation"),
        "fiscal": event.components.get("fiscal"),
        "capital_control": event.components.get("capital_control"),
        "uncertainty": event.components.get("uncertainty"),
        "event_severity": impact.severity,
        "event_confidence": impact.confidence,
        "revenue_exposure": exposure.get("revenue"),
        "production_exposure": exposure.get("production"),
        "supply_chain_exposure": exposure.get("supply_chain"),
        "regulatory_exposure": exposure.get("regulatory"),
        "net_exposure": impact.exposure,
        "event_age_days": impact.age_days,
        "decayed_severity": impact.decayed_severity,
        "political_impact": impact.impact,
    }


GPI_DETAILS = """\
**Geopolitical & Policy Impact (GPI v1)**

Two separate numbers, never merged:

    PoliticalRiskLevel  [0, 100]     how significant is the EVENT
    PoliticalImpact     [-100, +100] what it does to a SPECIFIC asset

    GPI severity = 0.30 Conflict + 0.20 Sanctions + 0.20 Regulation
                 + 0.15 Fiscal + 0.10 CapitalControl + 0.05 Uncertainty

    PoliticalImpact = DecayedSeverity x AssetExposure x Confidence

        AssetExposure = 0.35 Revenue + 0.25 SupplyChain
                      + 0.20 Production + 0.20 Regulatory      in [-1, +1]

The same event is bullish oil and bearish airlines because the exposure sign
differs, not because severity differs.

**Decay** is `exp(-ln2 x t / halflife)`, by event class: a headline fades in
days, a proposed regulation in months, an enacted law in years. Structurally
active events (live sanctions, a running conflict) do not decay while active.

**Equity overlay** is capped at ±10 by default (±15 configurable) and is applied
as a SEPARATE layer:

    FinalShafferEquityScore = CompanyScore + SectorOverlay + PoliticalOverlay

The company arithmetic is untouched.

**FX** uses the relative difference: GPI_pair = GPI_base − GPI_quote.

This engine scores measurable economic consequences only. It does not score
ideology, parties or voter preference.
"""
