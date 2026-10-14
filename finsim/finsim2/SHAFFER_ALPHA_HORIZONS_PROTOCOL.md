# Shaffer Alpha across horizons (1M – 5Y) — pre-registered protocol

Fixed 2026-09-27, **before any result was computed** (stage 2 of the program the owner asked for: "the best
indicator of a stock's movement, by how much"). Research only: production, D, E, the Directional prior-only model,
hedge-2 and the λ-hedge shadow are not changed. Code: `engine/alphahz.py`. Run: `python -m finsim2 lab
--alpha-horizons all`. Report: `SHAFFER_ALPHA_HORIZONS.md`.

## Universe and records

- US stocks (`EQUITY`) with ≥ 6 years of prices: the original universe plus the stocks added by
  `universe --expand 500` (flagged `meta.expanded`, survivorship-biased — see G3).
- One record every 21 sessions per stock from 1995 (after 252 sessions of the stock's own history, ≥ 50% of the
  features present), for horizons **1M, 3M, 6M, 12M, 24M, 36M, 60M**; only matured outcomes.
- Features (point in time): the stock-specific production families as the signed mean of clip(z / 2, ±1) of their
  member signals — Momentum, Trend, Mean Reversion, Valuation, Fundamental Quality, Fundamental Growth,
  Risk-Adjusted Performance, Statistical / Time Series, Relative Value — plus Low volatility (−vol_60) and Low beta
  (−beta_252). Market-wide families (Rates, Credit, Macro, Liquidity, Volatility, Cross-Asset) cannot rank stocks on one
  date and are left out. **New-data features**: every feature of a batch-2 family (NEW_DATA_SIGNALS_PROTOCOL.md) whose
  Alpha test passed G1, G2 and FDR at any horizon — the rule is fixed now, the list follows from stage 1's result.
- Outcome: log excess return over SPY to t + h (magnitude); vol-scaled y = excess / (vol_60 · √(h/252)), clipped ±4
  (ranking target: the date's centred cross-sectional rank of y).
- Baseline: the production Shaffer score at the nearest standard research record (≤ 5 sessions earlier) for the same
  horizon; **24M, 36M and 60M are compared with production's 12M score** (production has no longer horizon).

## Model

Pairwise ranking least squares (features demeaned within the date, target = centred rank), weights on standardised
features, records weighted q = 21 / max(21, h). Partial pooling — every node is a ridge toward its parent:
global (all horizons) → horizon group (short 1M–3M, medium 6M–12M, long 24M–60M) → horizon → sector → stock.

| Variant | Depth |
|---|---|
| V1 horizon | global → group → horizon |
| V2 sector | + sector |
| V3 stock | + sector + stock |
| V* (deployable) | depth and shrinkage K ∈ {10, 100, 1,000, 10,000} chosen per horizon by nested inner validation |

Nested choices: for each test era, fits on outcomes matured before the three years preceding the era are scored on those
three years' matured records; the best mean rank IC picks K for each depth and V*'s depth. The 2018 split makes its own
inner choice on data before 2018.

## Tests

Walk-forward test eras 2009–12, 2013–16, 2017–20, 2021–24, 2025– (training = outcomes matured before the era) and the
2018 split. Per-date Spearman rank IC; paired Δ = variant − production **on identical records** (stocks with a
production score); t over dates with n_eff = dates · 21 / h (overlapping outcomes). An era is complete with ≥ 24 dates.

- **G1** walk-forward Δ rank IC > 0 with t ≥ 2
- **G2** Δ > 0 in at least ⅔ of complete eras (and ≥ 2 complete eras); for V*, also split Δ t ≥ 1
- **G3** survivorship check: Δ > 0 on the original (pre-expansion) stocks alone
- **FDR** Benjamini-Hochberg q = 0.10 across horizons × {V1, V2, V3, V*}
- A horizon is **PASSED** when V* passes G1, G2, G3 and FDR; otherwise NOT VALIDATED (production stays the reference).

## Magnitude ("by how much")

For V*, in every training window the score's within-date percentile is binned in deciles; each decile's mean excess log
return over SPY, its 5% and 95% quantiles and the share of records that beat SPY are applied to the test records.
Reported out of sample: top − bottom decile spread, calibration slope (realised on predicted), 90%-range coverage and the
Brier score of P(beat SPY) against the base rate. Today's view (research display) gives every stock's percentile, expected
excess return, 90% range and P(beat SPY) per horizon, marked VALIDATED only where the horizon passed.

A passing horizon becomes a **shadow** candidate; it reaches a live score only through the registry's live gate and the
owner's approval.
