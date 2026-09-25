r"""The Shaffer Score, v2: the configuration (families, signals, economic priors, applicability, constants).

The engine is finsim2/engine/shaffer.py. There is ONE scoring routine, used for every date: the live score is
simply the last date of the same point-in-time run that produces the historical record.

    SS_raw(a,h,t) = 100 · tanh( Σ_f W_f,a,h,t · A_f,a · H_f,h · FamilyScore_f,a,h,t  /  K_a,h )
    FamilyScore_f = Σ_{i∈f} ω_i · s_i · c_i · r_i · d_i           (ω = correlation-penalised weights, Σω = 1)

  s  standardised directional reading in −1..+1: δ · clip(z / 2, −1, 1), z = the signal's expanding z-score
     (statistics of values strictly before t, capped ±3), δ = +1/−1 the direction the signal is used in.
  w  historical weight = |PredictiveStrength| × (¼ + ¾ · Stability); PredictiveStrength = the median of three
     estimates in correlation units (Pearson IC, the IC implied by the directional hit rate, the IC implied by the
     conditional-return spread), so no single noisy statistic dominates. ω = w / Σ_j ρ²_ij, normalised in the family.
  c  confidence = min(1, √(n_eff / N_FULL)) × CI-strength × (1 − ½·q) × data quality; CI-strength = |IC| / (|IC| +
     1.96·SE), SE = 1/√(n_eff − 3); q = Benjamini–Hochberg across all signals at that horizon.
  r  regime adjustment in [R_MIN, R_MAX]: the IC inside today's regime states relative to overall, each state
     shrunk by n_eff / (n_eff + N_REGIME).
  d  decay in [D_MIN, 1]: the last 3 years' and last year's IC relative to the full history (0.6 / 0.4 blend),
     shrunk toward 1 when the recent sample is small; never flips a sign.
  W  family weight = family evidence (min(1, Σ ω·|PS| / PS_REF)) × V_f, V_f the family score's own
     out-of-sample validation multiplier (1 until it has enough matured history; falls toward 0 with a negative
     record).
  A  asset applicability (table below), H horizon applicability (an economic prior; the evidence-based W decides
     the rest), K = KAPPA · Σ_f A·H over the families present (a fixed scale chosen before looking at results).

Direction δ: a signal has an economic prior (+1, −1, or 0 = agnostic). With a prior it is used in the prior's
direction; evidence against it mutes the signal (weight 0) and only strong, stable, well-sampled evidence reverses
it (|t| ≥ FLIP_T, n_eff ≥ FLIP_N, stability ≥ ⅔). An agnostic signal is used only once the evidence gives it a
direction (|t| ≥ AGNOSTIC_T, n_eff ≥ AGNOSTIC_N, stability ≥ ⅔).

Calibrated score: g(SS_raw) = the isotonic (monotone) map from raw score to the vol-scaled forward return, learned
only from matured out-of-sample pairs before the refit date. The calibrated score is the evidence RELATIVE to the
asset's own average outcome ȳ over those pairs:
    edge = (g(SS_raw) − ȳ) · n_eff_bin / (n_eff_bin + 20)          (shrunk toward 0 when the bin is small)
    SS_cal = 100 · tanh(edge / G)
The expected return is TOTAL — the asset's average plus the evidence:
    E[R] = exp((ȳ + edge) · σ · √(h/252)) − 1 = typical + evidence part,  typical = exp(ȳ · σ · √(h/252)) − 1
so sign(SS_cal) = sign(evidence part), while E[R] itself can have the other sign for an asset with a strong average
(NVDA 1M on 2026-09-24: SS_cal −33, E[R] +1.5% = typical +2.5% + evidence −1.0%). Expected return and the typical range are
shown only when that calibration has enough evidence.

Override: a file at ~/.finsim2/shaffer_score.py (or FINSIM2_SHAFFER) defining VERSION and score_asset(inputs) is
shown as a separate live-only "custom" score; the history, calibration and ledger always use the built-in engine.
"""
from __future__ import annotations

import importlib.util
import math
import os
from typing import Dict, List, Optional

NAME = "Shaffer Score"
VERSION: str = "2.1"      # 2.1: calibration bin merge fixed (a bin was double-counted, another dropped)
FORMULA = (r"SS_{a,h,t}=100\tanh\left(\frac{\sum_f W_{f,a,h,t}\,A_{f,a}\,H_{f,h}\sum_{i\in f}\omega_{i,a,h,t}\,s_{i,a,t}\,c_{i,a,h,t}"
           r"\,r_{i,a,t}\,d_{i,a,h,t}}{K_{a,h}}\right)")

HORIZONS = [("1D", 1), ("1W", 5), ("1M", 21), ("3M", 63), ("6M", 126), ("12M", 252), ("3Y", 756), ("5Y", 1260), ("10Y", 2520)]
MIN_NEFF_SHOW = 10       # a horizon is shown only with at least this many independent observations (10Y rarely qualifies)

# evidence constants
N_FULL = 100.0           # independent observations for full confidence (c's sample term)
N_REGIME = 50.0          # shrinkage of regime-conditional evidence
PS_REF = 0.05            # predictive strength (IC units) at which a family counts as fully evidenced
S_SCALE = 2.0            # s = clip(z / S_SCALE, −1, 1)
R_MIN, R_MAX = 0.5, 1.5
D_MIN = 0.1
FLIP_T, FLIP_N = 3.0, 60.0
AGNOSTIC_T, AGNOSTIC_N = 2.0, 30.0
KAPPA = 0.10             # K = KAPPA · Σ A·H
G_SCALE = 0.25           # calibrated edge (in forward-return standard deviations) that maps to ±76
MIN_TRAIN_YEARS = 3      # matured history before a horizon's first score
HISTORY_YEARS = 25       # reconstructed record: at most the last 25 years where data allow

# family -> [(signal, prior direction)]; priors are economic expectations for the asset's own future return
FAMILIES: Dict[str, List[tuple]] = {
    "Momentum": [("ret_3m", 1), ("ret_6m", 1), ("ret_12m", 1), ("mom_12_1", 1), ("ret_1m", 0)],
    "Trend": [("ma_cross", 1), ("dist_ma200", 1), ("macd", 1), ("pctile_252", 1), ("trend_quality", 1)],
    "Mean Reversion": [("ret_1d", -1), ("ret_1w", -1), ("z_20", -1), ("z_50", -1), ("rsi_14", -1), ("bb_pctb", -1), ("mr_opportunity", 1)],
    "Valuation": [("value_5y", 1), ("earnings_yield", 1), ("pe_rel_5y", -1), ("book_to_price", 1), ("sales_yield", 1)],
    "Fundamental Quality": [("net_margin", 1), ("roe", 1), ("fund_quality", 1)],
    "Fundamental Growth": [("eps_growth_yoy", 1), ("rev_growth_yoy", 1)],
    "Risk-Adjusted Performance": [("sharpe_252", 1), ("sortino_252", 1), ("alpha_252", 1), ("ram", 1)],
    "Volatility": [("vol_20", 0), ("vol_60", 0), ("downside_vol_60", 0), ("ewma_vol", 0), ("garch_vol", 0), ("vol_ratio", 0), ("vol_of_vol", 0), ("vol_pctile", 0)],
    "Statistical / Time Series": [("skew_60", -1), ("kurt_60", 0), ("ar1_63", 0), ("acf1_252", 0), ("half_life", 0), ("adf_t", 0), ("idio_vol_252", -1), ("drawdown_252", 0)],
    "Rates": [("y10", 0), ("d_y10_3m", 0), ("slope_10y3m", 0), ("d_slope_3m", 0), ("real_y10", 0), ("breakeven_10y", 0), ("rate_duration", 1)],
    "Credit": [("credit_spread", 0), ("d_credit_3m", 0), ("credit_signal", 0)],
    "Macro": [("cpi_yoy", 0), ("unemp_gap", 0), ("oil_mom_3m", 0), ("gold_mom_3m", 0)],
    "Liquidity": [("nfci", 0), ("fed_bs_growth", 0), ("vix", 0), ("d_vix_1m", 0), ("volume_z", 0)],
    "Cross-Asset": [("dollar_mom_3m", 0), ("beta_252", 0), ("corr_252", 0), ("rate_beta_252", 0), ("dollar_beta_252", 0)],
    "Relative Value": [("excess_3m", 1), ("rel_strength_6m", 1), ("rel_value", 1)],
}
FAMILY_OF = {sig: f for f, sigs in FAMILIES.items() for sig, _ in sigs}
PRIOR = {sig: p for f, sigs in FAMILIES.items() for sig, p in sigs}

CLASSES = ["EQUITY", "ETF", "INDEX", "TREASURY", "CORP_BOND", "COMMODITY", "FX", "CRYPTO"]
_P = 0.35   # plausible but not a core driver for that asset class
# A_{f,a}: 1 = a core family for the class (following the design brief), _P = secondary, 0 = does not apply
APPLICABILITY: Dict[str, Dict[str, float]] = {
    "Momentum":                  dict(EQUITY=1, ETF=1, INDEX=1, TREASURY=_P, CORP_BOND=_P, COMMODITY=1, FX=1, CRYPTO=1),
    "Trend":                     dict(EQUITY=1, ETF=1, INDEX=1, TREASURY=_P, CORP_BOND=_P, COMMODITY=1, FX=1, CRYPTO=1),
    "Mean Reversion":            dict(EQUITY=1, ETF=1, INDEX=1, TREASURY=_P, CORP_BOND=_P, COMMODITY=_P, FX=_P, CRYPTO=1),
    "Valuation":                 dict(EQUITY=1, ETF=_P, INDEX=_P, TREASURY=0, CORP_BOND=0, COMMODITY=0, FX=0, CRYPTO=0),
    "Fundamental Quality":       dict(EQUITY=1, ETF=0, INDEX=0, TREASURY=0, CORP_BOND=0, COMMODITY=0, FX=0, CRYPTO=0),
    "Fundamental Growth":        dict(EQUITY=1, ETF=0, INDEX=0, TREASURY=0, CORP_BOND=0, COMMODITY=0, FX=0, CRYPTO=0),
    "Risk-Adjusted Performance": dict(EQUITY=1, ETF=1, INDEX=1, TREASURY=_P, CORP_BOND=_P, COMMODITY=_P, FX=_P, CRYPTO=_P),
    "Volatility":                dict(EQUITY=1, ETF=1, INDEX=1, TREASURY=1, CORP_BOND=_P, COMMODITY=1, FX=1, CRYPTO=1),
    "Statistical / Time Series": dict(EQUITY=_P, ETF=_P, INDEX=_P, TREASURY=_P, CORP_BOND=_P, COMMODITY=_P, FX=_P, CRYPTO=1),
    "Rates":                     dict(EQUITY=1, ETF=1, INDEX=1, TREASURY=1, CORP_BOND=1, COMMODITY=_P, FX=1, CRYPTO=_P),
    "Credit":                    dict(EQUITY=1, ETF=1, INDEX=1, TREASURY=_P, CORP_BOND=1, COMMODITY=_P, FX=_P, CRYPTO=_P),
    "Macro":                     dict(EQUITY=1, ETF=1, INDEX=1, TREASURY=1, CORP_BOND=1, COMMODITY=1, FX=1, CRYPTO=1),
    "Liquidity":                 dict(EQUITY=_P, ETF=_P, INDEX=_P, TREASURY=1, CORP_BOND=1, COMMODITY=_P, FX=_P, CRYPTO=1),
    "Cross-Asset":               dict(EQUITY=_P, ETF=_P, INDEX=_P, TREASURY=_P, CORP_BOND=_P, COMMODITY=1, FX=1, CRYPTO=1),
    "Relative Value":            dict(EQUITY=1, ETF=1, INDEX=_P, TREASURY=_P, CORP_BOND=_P, COMMODITY=_P, FX=_P, CRYPTO=_P),
}
# H_{f,h} (1D 1W 1M 3M 6M 12M 3Y 5Y 10Y): an economic prior; the evidence-based family weight W decides the rest
HORIZON_FIT: Dict[str, List[float]] = {
    "Momentum":                  [0.3, 0.5, 0.9, 1.0, 1.0, 1.0, 0.5, 0.3, 0.2],
    "Trend":                     [0.5, 0.7, 1.0, 1.0, 1.0, 0.8, 0.4, 0.3, 0.2],
    "Mean Reversion":            [1.0, 1.0, 0.8, 0.5, 0.3, 0.2, 0.2, 0.2, 0.2],
    "Valuation":                 [0.1, 0.1, 0.3, 0.5, 0.7, 1.0, 1.0, 1.0, 1.0],
    "Fundamental Quality":       [0.1, 0.2, 0.4, 0.7, 0.9, 1.0, 1.0, 1.0, 1.0],
    "Fundamental Growth":        [0.1, 0.2, 0.5, 0.8, 1.0, 1.0, 0.8, 0.6, 0.5],
    "Risk-Adjusted Performance": [0.3, 0.5, 0.8, 1.0, 1.0, 1.0, 0.6, 0.4, 0.3],
    "Volatility":                [1.0, 1.0, 1.0, 0.8, 0.6, 0.5, 0.3, 0.2, 0.2],
    "Statistical / Time Series": [1.0, 1.0, 0.8, 0.6, 0.5, 0.4, 0.3, 0.2, 0.2],
    "Rates":                     [0.4, 0.5, 0.8, 1.0, 1.0, 1.0, 0.9, 0.8, 0.7],
    "Credit":                    [0.4, 0.5, 0.8, 1.0, 1.0, 1.0, 0.8, 0.6, 0.5],
    "Macro":                     [0.2, 0.3, 0.6, 0.9, 1.0, 1.0, 1.0, 0.9, 0.8],
    "Liquidity":                 [0.5, 0.7, 0.9, 1.0, 1.0, 0.9, 0.6, 0.4, 0.3],
    "Cross-Asset":               [0.5, 0.7, 0.9, 1.0, 1.0, 0.9, 0.6, 0.4, 0.3],
    "Relative Value":            [0.3, 0.5, 0.8, 1.0, 1.0, 1.0, 0.8, 0.6, 0.5],
}

# ------------------------------------------------------------------ candidate families (shadow until admitted)
# New, economically different information (engine/candidates.py). They run through the same point-in-time evidence
# machinery, but a family enters the production score at a horizon ONLY after the audit's admission test: out of
# sample, pooled across assets, its incremental IC over the production score is significant (Stouffer t >= 2) on the
# first two thirds of each asset's record AND keeps its sign on the last third, and the score with the family is
# not worse calibrated (IC and monotonicity) than without it. Governing rule: no re-tuning of production weights.
CANDIDATE_FAMILIES: Dict[str, List[tuple]] = {
    "Carry": [("carry", 1), ("div_yield", 1)],
    "Yield Curve": [("curvature", 0), ("d_curvature_3m", 0), ("slope_5s30s", 0), ("d_slope_2s10s_1m", 0), ("roll_down", 1)],
    "Term Structure": [("vix_term", 0), ("roll_yield", 1), ("d_roll_yield_3m", 0)],
    "Inflation": [("infl_accel", 0), ("d_breakeven_3m", 0), ("d_real_y10_3m", 0)],
    "FX": [("d_rate_diff_3m", 1), ("d_rate_diff_12m", 1)],
    "Commodity": [("real_rate_transmit", 0), ("usd_transmit", 0), ("cmd_breadth_3m", 0)],
    "Optionality": [("vrp", 0), ("iv_pctile", 0), ("d_iv_1m", 0)],
}
APPLICABILITY.update({
    "Carry":          dict(EQUITY=1, ETF=1, INDEX=1, TREASURY=1, CORP_BOND=1, COMMODITY=0, FX=1, CRYPTO=0),
    "Yield Curve":    dict(EQUITY=_P, ETF=1, INDEX=_P, TREASURY=1, CORP_BOND=1, COMMODITY=_P, FX=_P, CRYPTO=_P),
    "Term Structure": dict(EQUITY=1, ETF=1, INDEX=1, TREASURY=_P, CORP_BOND=_P, COMMODITY=1, FX=_P, CRYPTO=_P),
    "Inflation":      dict(EQUITY=1, ETF=1, INDEX=1, TREASURY=1, CORP_BOND=1, COMMODITY=1, FX=1, CRYPTO=_P),
    "FX":             dict(EQUITY=_P, ETF=_P, INDEX=_P, TREASURY=0, CORP_BOND=0, COMMODITY=_P, FX=1, CRYPTO=0),
    "Commodity":      dict(EQUITY=_P, ETF=_P, INDEX=_P, TREASURY=0, CORP_BOND=0, COMMODITY=1, FX=_P, CRYPTO=_P),
    "Optionality":    dict(EQUITY=1, ETF=1, INDEX=1, TREASURY=0, CORP_BOND=0, COMMODITY=1, FX=0, CRYPTO=0),
})
HORIZON_FIT.update({
    "Carry":          [0.2, 0.3, 0.6, 0.9, 1.0, 1.0, 1.0, 0.9, 0.8],
    "Yield Curve":    [0.4, 0.5, 0.8, 1.0, 1.0, 1.0, 0.9, 0.8, 0.7],
    "Term Structure": [0.5, 0.7, 1.0, 1.0, 0.9, 0.7, 0.4, 0.3, 0.2],
    "Inflation":      [0.2, 0.3, 0.6, 0.9, 1.0, 1.0, 1.0, 0.9, 0.8],
    "FX":             [0.4, 0.6, 0.9, 1.0, 1.0, 0.9, 0.6, 0.4, 0.3],
    "Commodity":      [0.5, 0.7, 0.9, 1.0, 1.0, 0.9, 0.6, 0.4, 0.3],
    "Optionality":    [0.8, 1.0, 1.0, 0.8, 0.6, 0.4, 0.2, 0.2, 0.2],
})
# family -> horizons at which it passed the admission test (filled only from an audit result; empty = shadow only)
ADMITTED: Dict[str, List[str]] = {}

ALL_FAMILIES: Dict[str, List[tuple]] = {**FAMILIES, **CANDIDATE_FAMILIES}
ALL_FAMILY_OF = {sig: f for f, sigs in ALL_FAMILIES.items() for sig, _ in sigs}
PRIOR.update({sig: p for f, sigs in CANDIDATE_FAMILIES.items() for sig, p in sigs})


def in_production(family: str, horizon: str) -> bool:
    """A production family, or a candidate admitted at this horizon."""
    return family in FAMILIES or horizon in ADMITTED.get(family, ())


HORIZON_LABELS = [lab for lab, _ in HORIZONS]


def applicability(family: str, asset_class: str) -> float:
    return float(APPLICABILITY.get(family, {}).get(asset_class, _P))


def horizon_fit(family: str, horizon: str) -> float:
    row = HORIZON_FIT.get(family)
    return float(row[HORIZON_LABELS.index(horizon)]) if row and horizon in HORIZON_LABELS else 0.5


# ------------------------------------------------------------------ optional live-only override (a custom score shown alongside)
def _override_path() -> str:
    return os.environ.get("FINSIM2_SHAFFER") or os.path.join(os.environ.get("FINSIM2_HOME") or os.path.join(os.path.expanduser("~"), ".finsim2"), "shaffer_score.py")


def is_override() -> bool:
    return os.path.isfile(_override_path())


def load_override():
    """(version, function, source, error) for an override file defining score_asset(inputs), or Nones."""
    path = _override_path()
    if not os.path.isfile(path):
        return None, None, None, None
    try:
        spec = importlib.util.spec_from_file_location("finsim2_shaffer_override", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        fn = getattr(mod, "score_asset", None)
        if not callable(fn):
            return None, None, path, "the file defines no score_asset(inputs)"
        return getattr(mod, "VERSION", "custom"), fn, path, None
    except Exception as e:          # a broken override never takes the pages down
        return None, None, path, f"failed to load: {e!r}"


def custom_score(inputs: dict) -> Optional[dict]:
    """Run the override (live only), never raising. Returns {version, source, horizons: {h: score}} or None."""
    version, fn, source, err = load_override()
    if fn is None:
        return {"version": None, "source": source, "error": err} if source else None
    try:
        res = fn(inputs) or {}
        hs = {}
        for h, r in (res.get("horizons") or {}).items():
            v = r.get("score") if isinstance(r, dict) else r
            try:
                v = float(v)
                hs[h] = max(-100.0, min(100.0, v)) if math.isfinite(v) else None
            except (TypeError, ValueError):
                hs[h] = None
        return {"version": version, "source": source, "horizons": hs}
    except Exception as e:
        return {"version": version, "source": source, "error": f"{type(e).__name__}: {e}"}


def status() -> Dict:
    ov = load_override()
    return {"name": NAME, "version": VERSION, "enabled": True, "state": "live", "formula": FORMULA,
            "source": "built-in (finsim2/engine/shaffer.py)", "families": {f: [{"signal": s, "prior": p} for s, p in sigs] for f, sigs in FAMILIES.items()},
            "applicability": APPLICABILITY, "horizon_fit": HORIZON_FIT, "horizons": HORIZON_LABELS,
            "constants": {k: globals()[k] for k in ("N_FULL", "N_REGIME", "PS_REF", "S_SCALE", "R_MIN", "R_MAX", "D_MIN", "FLIP_T", "FLIP_N",
                                                     "AGNOSTIC_T", "AGNOSTIC_N", "KAPPA", "G_SCALE", "MIN_TRAIN_YEARS", "HISTORY_YEARS", "MIN_NEFF_SHOW")},
            "override_path": _override_path(), "override": {"version": ov[0], "source": ov[2], "error": ov[3]} if ov[2] else None}
