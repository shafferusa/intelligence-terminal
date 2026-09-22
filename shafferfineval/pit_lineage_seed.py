"""Seed the permanent lineage record of the human-designed Shaffer framework.

The Shaffer Score and the Shaffer Hedge are the invention. Machine learning is
being added to TEST, VALIDATE, CALIBRATE and PROPOSE improvements to them --
never to erase, replace or rebrand them. The lineage that makes that claim
auditable is:

    Shaffer Human Framework -> Evidence -> ML Challenger -> Human Review
        -> New Shaffer Version   (predecessor retained, never rewritten)

This module writes the first link of that chain into `pit_model_lineage`: what
each CURRENT production model is, with its authored specification, so a reader
in ten years can reconstruct the equation from the database alone. The prose
companion is `docs/MODEL-LINEAGE.md`.

Every constant here is IMPORTED from the module that implements it -- never
retyped. A weight typed twice is a weight that will disagree with itself, and a
lineage record that disagrees with production is worse than no record at all.
Each entry names its source file in `factor_definitions_json["source_file"]`.

Two honesty notes that are part of the record, not caveats to it:

1. The v1 specifications predate this research store and the repository carries
   no authoring date for them, so `created_date` is the date the lineage ROW was
   written (`LINEAGE_SEED_DATE`), and every note says so. An invented authoring
   date would be worse than an honest seeding date.

2. These models were promoted to production before `pit_promotion` existed, so
   they are seeded AT status PRODUCTION with no audit row behind them. Every
   FUTURE status change must go through `pit_store.record_promotion()`, which is
   the only sanctioned way a status moves.

Idempotent: re-running reports 'exists' and rewrites nothing. `register_model_lineage`
updates only status and notes on conflict, so a seeded specification cannot be
silently edited by a later run -- a changed formula is a NEW version, never an
amendment of the one that produced existing rows.

    python pit_lineage_seed.py [db_path]
"""

from __future__ import annotations

import sqlite3
import sys
from typing import Any, Optional

import asset_models
import company_scoring
import hedging
import political
import prediction
import pit_store
import scoring
import sector_scoring
import statlib
import storage

#: The date this lineage record was written. NOT the date the models were
#: authored -- see the module docstring.
LINEAGE_SEED_DATE = "2026-09-20"

_SEED_NOTE = (
    f"Lineage row seeded {LINEAGE_SEED_DATE}; the specification predates it and "
    f"the repository carries no authoring date. Promoted to production before "
    f"pit_promotion existed, so there is no audit row behind this status; every "
    f"future status change goes through record_promotion()."
)

#: The four derivative overlays that carry their own version string. The other
#: two (swaption, option direction) reuse `option_shaffer_v1`, which is already
#: registered as one of the nineteen asset-class models, so they are documented
#: in docs/MODEL-LINEAGE.md rather than registered as separate versions.
DERIVATIVE_OVERLAYS: dict[str, dict[str, str]] = {
    "cds_shaffer_v1": {
        "instrument": "CDS",
        "buy_protection": "-CreditStrength + CDSRelativeValueOverlay",
        "sell_protection": "-BuyProtectionScore + CarryOverlay",
        "note": "Selling protection earns carry, so it is not simply the negative "
                "of the buy score.",
    },
    "trs_shaffer_v1": {
        "instrument": "Total return swap",
        "receive": "UnderlyingScore - CarryCostOverlay",
        "pay": "-UnderlyingScore - CarryCostOverlay",
        "note": "Carry cost is subtracted on BOTH sides: financing, dealer spread "
                "and collateral are paid whichever way the swap faces.",
    },
    "irs_shaffer_v1": {
        "instrument": "Interest rate swap",
        "receive_fixed": "RatesScore + CarryCurveOverlay",
        "pay_fixed": "-RatesScore + CarryCurveOverlay",
        "note": "Inherits rates_shaffer_v1; no independent macro forecast.",
    },
    "fx_forward_shaffer_v1": {
        "instrument": "FX forward / NDF",
        "equation": "0.85 x FXScore + 0.15 x ForwardValue",
        "note": "With no forward value the 0.15 weight renormalizes onto the FX "
                "score alone.",
    },
}

#: Applies to every equation in the framework, at every level.
MISSING_DATA_RULE = (
    "Drop and renormalise. An unavailable factor is excluded and the remaining "
    "weights are renormalised over the weight that survived; nothing is imputed, "
    "and a missing factor never silently becomes zero. A score computed with a "
    "factor missing is a DIFFERENT MODEL STATE and records its effective weights. "
    "With no factor available the score is None, never 0."
)


# --------------------------------------------------------------------------
# The records
# --------------------------------------------------------------------------

def equity_model_v1_record() -> dict[str, Any]:
    """The equity Shaffer Score exactly as authored in company_scoring.py.

    Weights are the imported constant dicts themselves, so this record cannot
    drift from the code that computes the live score.
    """
    return {
        "model_version": storage.EQUITY_MODEL_VERSION,
        "model_name": "Shaffer Score -- equity",
        "asset_class": "Equity",
        "authorship": "human",
        "status": pit_store.PRODUCTION,
        "predecessor_version": None,
        "weights": {
            "major": company_scoring.MAJOR_WEIGHTS,
            "growth": company_scoring.GROWTH_WEIGHTS,
            "profitability": company_scoring.PROFITABILITY_WEIGHTS,
            "debt": company_scoring.DEBT_WEIGHTS,
        },
        "factor_definitions": {
            "source_file": "company_scoring.py",
            "master_equation": "CompanyRawScore = 0.40V + 0.25G + 0.20P + 0.15D",
            "valuation": {
                "weight": company_scoring.MAJOR_WEIGHTS["valuation"],
                "chain": [
                    "EV = MarketCap + TotalDebt - Cash",
                    "BenchmarkEVEBITDA = winsorized mean EV/EBITDA of the EBITDA cohort",
                    "ImpliedEV = CompanyEBITDA x BenchmarkEVEBITDA",
                    "ImpliedEquityValue = ImpliedEV - CompanyDebt + CompanyCash",
                    "ImpliedPrice = ImpliedEquityValue / SharesOutstanding",
                    "ValuationGap = (ImpliedPrice - CurrentPrice) / CurrentPrice",
                ],
                "valid_multiple_band": [company_scoring.MIN_VALID_EV_EBITDA,
                                        company_scoring.MAX_VALID_EV_EBITDA],
            },
            "growth": {"weight": company_scoring.MAJOR_WEIGHTS["growth"],
                       "components": company_scoring.GROWTH_WEIGHTS},
            "profitability": {"weight": company_scoring.MAJOR_WEIGHTS["profitability"],
                              "components": company_scoring.PROFITABILITY_WEIGHTS},
            "debt": {"weight": company_scoring.MAJOR_WEIGHTS["debt"],
                     "components": company_scoring.DEBT_WEIGHTS,
                     "direction": "both components LOWER is better"},
            "higher_is_better": company_scoring.COMPONENT_HIGHER_IS_BETTER,
            "peer_construction": {
                "min_industry_peers": company_scoring.MIN_INDUSTRY_PEERS,
                "fallback": "sector, always labelled 'Sector Fallback', never silent",
                "ebitda_cohort": "ThirdQuartilePeerSet -- peers whose EBITDA sits in "
                                 "the 50th-75th percentile, deliberately not the top "
                                 "quartile",
                "min_ebitda_cohort": company_scoring.MIN_EBITDA_COHORT,
                "winsorisation": f"{100 * statlib.WINSOR_LOWER:.0f}th/"
                                 f"{100 * statlib.WINSOR_UPPER:.0f}th percentile, "
                                 f"count-based, min_trim={statlib.MIN_WINSOR_TRIM} "
                                 f"(statlib.py)",
            },
            "classification": _classification_bands(),
        },
        "transformations": {
            "valuation_score": "V = 100 x tanh(k x ValuationGap)",
            "valuation_tanh_k": company_scoring.VALUATION_TANH_K,
            "why_tanh": "An extreme gap saturates instead of dominating: +25% gap "
                        "scores about +46, +50% about +76.",
            "company_scale": company_scoring.COMPANY_SCALE,
            "why_scale": "The company term is capped at +/-75 so the sector overlay "
                         "(+/-25) has room to move the final score without either "
                         "term saturating the other out of existence.",
            "raw_score_clamp": [company_scoring.SCORE_MIN, company_scoring.SCORE_MAX],
            "company_score_clamp": [company_scoring.COMPANY_SCORE_MIN,
                                    company_scoring.COMPANY_SCORE_MAX],
            "final_score": "FinalEquityScore = CompanyScore + SectorOverlay",
            "final_score_clamp": [company_scoring.FINAL_MIN, company_scoring.FINAL_MAX],
        },
        "missing_data_rule": MISSING_DATA_RULE,
        "normalization": (
            "Percentile rank against SAME-INDUSTRY peers (average ranks, ties "
            "shared), mapped 200p-100 where higher is better and 100-200p where "
            "lower is better. Valuation is the exception: a tanh transform of the "
            "peer-implied gap. A company with no usable peers scores None -- 0.5 "
            "would be a fabricated middle."
        ),
        "overlays": {
            "sector": storage.SECTOR_MODEL_VERSION,
            "political": political.GPI_VERSION,
            "political_cap": political.OVERLAY_DEFAULT_CAP,
        },
        "calibration_version": storage.RETURN_CALIBRATION_VERSION,
        "hedge_rules": None,
        "notes": (
            "The originating human-designed scoring framework and the canonical "
            "lineage id for the equity model. `equity_shaffer_v1` in asset_models.py "
            "is the multi-asset catalogue's label for this same equation; the two "
            "names are one model. Only a model at this version, promoted, may be "
            "called the Shaffer Score. " + _SEED_NOTE
        ),
    }


def sector_model_v1_record() -> dict[str, Any]:
    """The sector overlay exactly as authored in sector_scoring.py."""
    return {
        "model_version": storage.SECTOR_MODEL_VERSION,
        "model_name": "Shaffer sector overlay",
        "asset_class": "Sector",
        "authorship": "human",
        "status": pit_store.PRODUCTION,
        "predecessor_version": None,
        "weights": sector_scoring.SECTOR_FACTOR_WEIGHTS,
        "factor_definitions": {
            "source_file": "sector_scoring.py",
            "master_equation": "SectorRawScore = 0.40G + 0.21ROE + 0.14ROA + 0.25D",
            "labels": sector_scoring.FACTOR_LABELS,
            "higher_is_better": sector_scoring.FACTOR_HIGHER_IS_BETTER,
            "growth": "Market-cap-weighted sector return acceleration MINUS VTI "
                      "acceleration, over a 63-session recent window against a "
                      "126-session prior window.",
            "recent_window_sessions": sector_scoring.RECENT_WINDOW,
            "lookback_window_sessions": sector_scoring.LOOKBACK_WINDOW,
            "min_price_history_sessions": sector_scoring.MIN_PRICE_HISTORY,
            "guards": {
                "min_companies_per_factor": sector_scoring.MIN_COMPANIES_PER_FACTOR,
                "min_eligible_companies": sector_scoring.MIN_ELIGIBLE_COMPANIES,
                "min_factors_for_score": sector_scoring.MIN_FACTORS_FOR_SCORE,
            },
        },
        "transformations": {
            "overlay": "SectorOverlay = SectorRawScore / 4",
            "overlay_divisor": sector_scoring.OVERLAY_DIVISOR,
            "why_divisor": "The authored statement of how much a sector may move a "
                           "company: at most +/-25 against a company term capped at "
                           "+/-75. Sector context tilts the verdict, never delivers it.",
            "raw_score_clamp": [sector_scoring.RAW_SCORE_MIN,
                                sector_scoring.RAW_SCORE_MAX],
            "overlay_clamp": [sector_scoring.OVERLAY_MIN, sector_scoring.OVERLAY_MAX],
        },
        "missing_data_rule": (
            MISSING_DATA_RULE
            + f" Below {sector_scoring.MIN_FACTORS_FOR_SCORE} available factors the "
              f"sector score is withheld entirely rather than computed from one term."
        ),
        "normalization": (
            "Percentile rank ACROSS SECTORS, never raw magnitude, so one extreme "
            "sector cannot dominate. Inputs are winsorized means (5th/95th, "
            "count-based) so one extreme company cannot dominate either."
        ),
        "overlays": None,
        "calibration_version": None,
        "hedge_rules": None,
        "notes": (
            "The only number the company model consumes from this engine is "
            "SectorOverlay; the company formula itself is untouched here. " + _SEED_NOTE
        ),
    }


def hedge_model_v1_record() -> dict[str, Any]:
    """The Shaffer Hedge exactly as authored in hedging.py."""
    return {
        "model_version": storage.HEDGE_MODEL_VERSION,
        "model_name": "Shaffer Hedge",
        "asset_class": "Equity",
        "authorship": "human",
        "status": pit_store.PRODUCTION,
        "predecessor_version": None,
        "weights": hedging.STRATEGY_WEIGHTS,
        "factor_definitions": {
            "source_file": "hedging.py",
            "strategy_universe": "strategy_catalog.py -- the approved workbook "
                                 "playbook; nothing invents a strategy outside it.",
            "seven_factor_score": hedging.STRATEGY_WEIGHTS,
            "eligibility": "By PAYOFF, not by name: a structure must pay more than "
                           f"{hedging.MIN_HEDGE_PAYOFF} of spot at a "
                           f"{hedging.STRESS_MOVE} stress move to count as a hedge.",
        },
        "transformations": {
            "adverse_score": "AdverseScore = max(0, -d x FinalEquityScore), "
                             "d = +1 long, -1 short",
            "hedge_ratio": "HedgeRatio = clamp((AdverseScore - 15) / 85, 0, 1)",
            "hedged_shares": "HedgedShares = abs(Shares) x HedgeRatio",
        },
        "missing_data_rule": (
            MISSING_DATA_RULE
            + " A strategy whose hedge effectiveness cannot be measured is EXCLUDED "
              "rather than scored on the remaining six factors."
        ),
        "normalization": "Each of the seven strategy factors is scored to "
                         "[-100, +100] before weighting.",
        "overlays": None,
        "calibration_version": None,
        "hedge_rules": {
            "hedge_floor": hedging.HEDGE_FLOOR,
            "hedge_span": hedging.HEDGE_SPAN,
            "why_floor": "Mild disagreement is not worth paying for: below an "
                         "adverse score of 15 the model asks for no hedge at all, "
                         "and the ramp reaches a full hedge only at 100.",
            "contract_multiplier": hedging.CONTRACT_MULTIPLIER,
            "target_dte": hedging.TARGET_DTE,
            "dte_band": [hedging.MIN_DTE, hedging.MAX_DTE],
            "stress_move": hedging.STRESS_MOVE,
            "min_hedge_payoff": hedging.MIN_HEDGE_PAYOFF,
            "max_otm_pct": hedging.MAX_OTM_PCT,
            "default_protective_pct": {str(right): pct for right, pct
                                       in hedging.DEFAULT_PROTECTIVE_PCT.items()},
            "why_defaults_only": "Only a leg sitting at its group default is repriced "
                                 "by the equity score; a deliberately different strike "
                                 "is the product's identity and is left alone, or "
                                 "every put strategy collapses into one.",
        },
        "notes": (
            "The Shaffer Hedge is part of the human invention, not a utility bolted "
            "onto it. Only a promoted production version may carry the name. "
            + _SEED_NOTE
        ),
    }


def return_calibration_v1_record() -> dict[str, Any]:
    """The score-to-return mapping, versioned separately from the score itself."""
    return {
        "model_version": storage.RETURN_CALIBRATION_VERSION,
        "model_name": "Shaffer Predicted Return -- V1 calibration",
        "asset_class": "Equity",
        "authorship": "human",
        "status": pit_store.PRODUCTION,
        "predecessor_version": None,
        "weights": {"slope": prediction.V1_SLOPE, "intercept": prediction.V1_INTERCEPT},
        "factor_definitions": {
            "source_file": "prediction.py",
            "input": "FinalEquityScore in [-100, +100]",
            "output": "Absolute 12-month PRICE return from today's market price",
            "horizon": prediction.PRIMARY_HORIZON,
            "horizons_tracked_sessions": prediction.HORIZONS,
            "what_it_is_not": [
                "not excess return versus VTI",
                "not total return including dividends",
                "not an analyst consensus target",
                "not the peer-implied value from the valuation factor",
            ],
        },
        "transformations": {
            "predicted_return_pct": "PredictedReturnPct = 0.00 + 0.20 x ShafferScore",
            "predicted_price": "PredictedPrice = CurrentPrice x (1 + ReturnPct / 100)",
            "worked": "+100 -> +20%, +50 -> +10%, 0 -> 0%, -100 -> -20%",
            "model": prediction.PREDICTION_MODEL,
            "model_version_label": prediction.PREDICTION_MODEL_VERSION,
        },
        "missing_data_rule": "No score, no prediction. A missing score never becomes 0.",
        "normalization": "None -- a linear map from the score scale to percentage "
                         "points of return.",
        "overlays": None,
        "calibration_version": storage.RETURN_CALIBRATION_VERSION,
        "hedge_rules": None,
        "notes": (
            "A transparent placeholder, labelled as one in the product, and stored "
            "SEPARATELY from the score so research can ask whether 0.20 was ever the "
            "right slope without the answer being baked into the historical record. "
            "The most obviously testable claim in the framework and the likeliest "
            "first target of a validated challenger -- but 0.20 is what the terminal "
            "says until a human promotes a successor. " + _SEED_NOTE
        ),
    }


def equity_pit_twin_record() -> dict[str, Any]:
    """The historical research twin: same human equation, honest historical data.

    Registered with authorship 'human' because it implements a HUMAN equation --
    it is not a challenger, it is the baseline a challenger must beat. Its status
    is RESEARCH and it descends from equity_model_v1.
    """
    equity = equity_model_v1_record()
    return {
        "model_version": pit_store.EQUITY_PIT_MODEL_VERSION,
        "model_name": "Historical PIT Twin -- equity Shaffer Score",
        "asset_class": "Equity",
        "authorship": "human",
        "status": pit_store.RESEARCH,
        "predecessor_version": storage.EQUITY_MODEL_VERSION,
        "weights": equity["weights"],
        "factor_definitions": dict(
            equity["factor_definitions"],
            source_file="company_scoring.py (equation); pit_store.py and pit_policy.py "
                        "(point-in-time data, peers and identity)",
            peer_construction=dict(
                equity["factor_definitions"]["peer_construction"],
                peer_set_version=pit_store.PEER_SET_VERSION,
                as_of_industry="SIC as assigned AT FILING TIME, from the filing's own "
                               "header -- never the current company record",
                universe="includes issuers that have since died; peers are the "
                         "cohort that existed on the as-of date",
            ),
        ),
        "transformations": dict(
            equity["transformations"],
            ebitda="Assembled from XBRL tags under a versioned concept ladder, "
                   "frequently across two filings with different accessions -- not a "
                   "vendor-supplied field.",
            fact_selection="The latest vintage a reader could have seen on the as-of "
                           "date; later restatements are invisible.",
            identity="CIK-backed entity_id and a listing disambiguated by first trade "
                     "date -- never a ticker.",
        ),
        "missing_data_rule": (
            MISSING_DATA_RULE
            + " Unavailability is recorded with a REASON (never_tagged, tagged_zero, "
              "company_had_none, not_yet_filed, ladder_exhausted, stale, "
              "insufficient_peers), because 'the company genuinely had no debt' and "
              "'nobody tagged it' are different facts."
        ),
        "normalization": equity["normalization"] + (
            " Peer percentiles are computed against the point-in-time cohort, so a "
            "rank here is a rank among the companies that actually existed."
        ),
        "overlays": {"sector": pit_store.SECTOR_PIT_MODEL_VERSION},
        "calibration_version": pit_store.RETURN_CALIBRATION_VERSION,
        "hedge_rules": None,
        "notes": (
            "Applies the SAME human-authored equation as equity_model_v1 to a "
            "historically honest reconstruction of what was knowable on a past date. "
            "It will not reproduce the production number and is not supposed to: the "
            "gap between them measures how much of the production score comes from "
            "the equation and how much from surviving, restated, vendor-normalised "
            "data. THE GAP IS RESEARCH EVIDENCE, NOT A DEFECT TO BE ENGINEERED AWAY. "
            "Never relax the point-in-time discipline to make the twin agree with "
            "production, and never adjust production to agree with the twin. Label it "
            "Historical PIT Twin; it is never called the Shaffer Score. Policies: "
            f"{pit_store.LATENCY_POLICY_VERSION}, {pit_store.LADDER_VERSION}, "
            f"{pit_store.PEER_SET_VERSION}. Seeded {LINEAGE_SEED_DATE}."
        ),
    }


def asset_class_records() -> list[dict[str, Any]]:
    """One record per asset-class v1 equation, built FROM asset_models.MODELS.

    Derived programmatically rather than transcribed, so the nineteen equations
    cannot be copied into the lineage table with a typo. Sub-equations are kept
    under a 'children' key so a nested model records its full specification.
    """
    records: list[dict[str, Any]] = []
    for key, model in asset_models.MODELS.items():
        records.append({
            "model_version": model.version,
            "model_name": f"Shaffer v1 -- {model.name}",
            "asset_class": model.name,
            "authorship": "human",
            "status": pit_store.PRODUCTION,
            "predecessor_version": None,
            "weights": _model_weights(model),
            "factor_definitions": {
                "source_file": "asset_models.py",
                "model_key": key,
                "direction": model.direction,
                "factors": [_factor_spec(spec) for spec in model.factors],
                "note": model.note or "",
            },
            "transformations": {
                "scale": model.scale,
                "equation": f"Score = clamp(sum(w_i x f_i), -100, +100) x {model.scale}",
                "score_clamp": [asset_models.SCORE_MIN, asset_models.SCORE_MAX],
                "confidence": "HIGH at coverage >= 0.85 with >= 3 factors; MEDIUM at "
                              ">= 0.55 with >= 2; otherwise LOW.",
            },
            "missing_data_rule": MISSING_DATA_RULE,
            "normalization": "Every factor arrives already normalized to [-100, +100] "
                             "(percentile rank, z-score or an explicit transform). Raw "
                             "units are never mixed into a weighted equation.",
            "overlays": _overlays_for(model.version),
            "calibration_version": None,
            "hedge_rules": None,
            "notes": (
                ("The multi-asset catalogue's label for the same human equation as "
                 f"{storage.EQUITY_MODEL_VERSION}; the two names are one model. "
                 if model.version == "equity_shaffer_v1" else "")
                + _SEED_NOTE
            ),
        })
    return records


def derivative_overlay_records() -> list[dict[str, Any]]:
    """The four derivative overlays that carry their own version string.

    They INHERIT an underlying score rather than running an independent economic
    model, which is the property that stops a derivative contradicting its own
    underlying. That inheritance is the specification, so it is what is recorded.
    """
    records: list[dict[str, Any]] = []
    for version, spec in DERIVATIVE_OVERLAYS.items():
        records.append({
            "model_version": version,
            "model_name": f"Shaffer v1 overlay -- {spec['instrument']}",
            "asset_class": spec["instrument"],
            "authorship": "human",
            "status": pit_store.PRODUCTION,
            "predecessor_version": None,
            "weights": None,
            "factor_definitions": dict(spec, source_file="asset_models.py"),
            "transformations": {k: v for k, v in spec.items()
                                if k not in ("instrument", "note")},
            "missing_data_rule": "No underlying score, no derivative score. An "
                                 "unavailable overlay component contributes 0 to the "
                                 "overlay sum and is listed as missing.",
            "normalization": "Inherited from the underlying, already in [-100, +100].",
            "overlays": None,
            "calibration_version": None,
            "hedge_rules": None,
            "notes": spec.get("note", "") + " " + _SEED_NOTE,
        })
    return records


def gpi_v1_record() -> dict[str, Any]:
    """The geopolitical overlay, versioned separately from the equity model."""
    return {
        "model_version": political.GPI_VERSION,
        "model_name": "Shaffer geopolitical overlay (GPI)",
        "asset_class": "Overlay",
        "authorship": "human",
        "status": pit_store.PRODUCTION,
        "predecessor_version": None,
        "weights": {
            "severity": political.SEVERITY_WEIGHTS,
            "exposure": political.EXPOSURE_WEIGHTS,
        },
        "factor_definitions": {
            "source_file": "political.py",
            "severity_equation": "GPI = .30C + .20S + .20R + .15F + .10K + .05U",
            "severity_labels": political.SEVERITY_LABELS,
            "exposure": "Components weighted into a single exposure in [-1, +1].",
            "half_lives_days": political.HALF_LIVES,
            "persistent_classes": sorted(political.PERSISTENT),
        },
        "transformations": {
            "decay": "Impact decays as exp(-lambda t) unless the event class is "
                     "structurally persistent while its status is active.",
            "overlay_default_cap": political.OVERLAY_DEFAULT_CAP,
            "overlay_max_cap": political.OVERLAY_MAX_CAP,
        },
        "missing_data_rule": MISSING_DATA_RULE,
        "normalization": "Severity and exposure are bounded by construction; the "
                         "overlay is capped before it reaches a score.",
        "overlays": None,
        "calibration_version": None,
        "hedge_rules": None,
        "notes": (
            "Recorded as its own lineage entry rather than folded into the equity "
            "model, because it is a separate human hypothesis with a separate "
            "testable claim. " + _SEED_NOTE
        ),
    }


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def _classification_bands() -> dict[str, list]:
    """The four score bands as [lower, upper], read from scoring.BAND_THRESHOLDS.

    The lowest band has no threshold of its own -- it is what a score below the
    last listed floor is called -- so it is closed at SCORE_MIN here rather than
    left implicit.
    """
    bands: dict[str, list] = {}
    upper = scoring.SCORE_MAX
    for threshold, label in scoring.BAND_THRESHOLDS:
        bands[label] = [threshold, upper]
        upper = threshold
    bands[scoring.BEARISH] = [scoring.SCORE_MIN, upper]
    return bands


def _factor_spec(spec) -> dict[str, Any]:
    """One FactorSpec as plain JSON-able data, recursing into sub-equations."""
    out: dict[str, Any] = {
        "key": spec.key,
        "label": spec.label,
        "weight": spec.weight,
        "higher_is_better": spec.higher_is_better,
    }
    if spec.note:
        out["note"] = spec.note
    if spec.children:
        out["children"] = [_factor_spec(child) for child in spec.children]
    return out


def _model_weights(model) -> dict[str, Any]:
    """Top-level weights, with a nested dict wherever a factor has children."""
    weights: dict[str, Any] = {}
    for spec in model.factors:
        if spec.children:
            weights[spec.key] = {child.key: child.weight for child in spec.children}
        else:
            weights[spec.key] = spec.weight
    return weights


def _overlays_for(model_version: str) -> Optional[dict[str, Any]]:
    """The derivative overlays a given asset-class model feeds, if any."""
    if model_version == "rates_shaffer_v1":
        return {"irs": "irs_shaffer_v1",
                "swaption": "option_shaffer_v1 (receiver = +rates, payer = -rates)"}
    if model_version == "corp_credit_shaffer_v1":
        return {"cds": "cds_shaffer_v1"}
    if model_version == "fx_shaffer_v1":
        return {"forward": "fx_forward_shaffer_v1"}
    if model_version == "option_shaffer_v1":
        return {"direction": "call = +UnderlyingScore, put = -UnderlyingScore"}
    return None


def all_records() -> list[dict[str, Any]]:
    """Every record this module seeds, in a deterministic order.

    The five the research store needs first come first, so a partial run still
    leaves the equity lineage and its PIT twin complete.
    """
    return [
        equity_model_v1_record(),
        sector_model_v1_record(),
        hedge_model_v1_record(),
        return_calibration_v1_record(),
        equity_pit_twin_record(),
        gpi_v1_record(),
        *asset_class_records(),
        *derivative_overlay_records(),
    ]


# --------------------------------------------------------------------------
# Seeding
# --------------------------------------------------------------------------

def register_record(conn: sqlite3.Connection, record: dict[str, Any]) -> str:
    """Write one lineage record. Returns 'written' or 'exists'.

    'exists' is not a failure and never an overwrite: a registered specification
    is permanent, and a changed formula is a NEW version rather than an
    amendment of the one that produced existing rows.
    """
    version = record["model_version"]
    existing = conn.execute(
        "SELECT 1 FROM pit_model_lineage WHERE model_version = ?", (version,)
    ).fetchone()
    if existing:
        return "exists"

    fields = {k: v for k, v in record.items()
              if k not in ("model_version", "model_name", "asset_class", "authorship")}
    fields.setdefault("created_date", LINEAGE_SEED_DATE)
    pit_store.register_model_lineage(
        conn, version, record["model_name"], record["asset_class"],
        record["authorship"], **fields,
    )
    return "written"


def seed_lineage(conn: sqlite3.Connection) -> dict[str, str]:
    """Register every current production model. Idempotent.

    Returns {model_version: 'written' | 'exists'}. Safe to run repeatedly: the
    second run writes nothing and says so.
    """
    return {record["model_version"]: register_record(conn, record)
            for record in all_records()}


def lineage_rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Every registered version, production first, for a report or a UI page."""
    return conn.execute(
        """SELECT * FROM pit_model_lineage
            ORDER BY CASE status WHEN 'PRODUCTION' THEN 0 WHEN 'RESEARCH' THEN 1
                                 ELSE 2 END,
                     model_version"""
    ).fetchall()


def main(argv: Optional[list[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    db_path = argv[0] if argv else pit_store.DEFAULT_PIT_DB_PATH
    conn = pit_store.init_db(db_path)
    result = seed_lineage(conn)

    written = sum(1 for status in result.values() if status == "written")
    print(f"lineage seed -> {db_path}")
    for version, status in result.items():
        print(f"  {status:8s} {version}")
    print(f"{written} written, {len(result) - written} already present, "
          f"{len(result)} total.")
    print("Status changes from here on go through pit_store.record_promotion().")
    return 0


if __name__ == "__main__":
    sys.exit(main())
