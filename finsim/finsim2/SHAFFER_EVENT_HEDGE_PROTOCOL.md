# Does the event-calendar volatility forecast improve the Shaffer Hedge? — pre-registered protocol (stage 4)

Fixed 2026-09-27, **before any result was computed**, after stage 1 (NEW_DATA_SIGNALS.md) found that the event calendar
improves walk-forward volatility forecasts at 1W and 1M (G1, G2 and FDR passed). A better forecast is not a better
hedge; this study judges the realised hedge. Research only: hedge-2, its sizing, the λ-hedge shadow and the frozen
benchmark are not changed. Run: `python -m finsim2 lab --event-hedge`. Report: `SHAFFER_EVENT_HEDGE.md`.

## Design — identical to the breadth study (hedge/volhedge.py, BREADTH_HEDGE_RESEARCH.md)

The complete hedge chain is replayed point in time (PIT data → volatility forecast → covariance → candidates → sizing →
optimiser → package → realised P&L) for otherwise identical engines, with the same books, objectives, study dates
(1W every 10 sessions, 1M every 21, 3M every 63, from 2009), cost model, utility U(λ), inference (paired, clustered by
date, moving-block bootstrap), eras and gates G1–G5 as fixed for the breadth study on 2026-09-25. Only the challenger's
forecast changes:

| Arm | Engine |
|---|---|
| A | hedge-2 (production): Σ from the trailing 252-day sample |
| B | `hedge-2-event-vol-exp`: Σ with the market factor's (SPY) variance replaced by the event-calendar forecast (correlations kept) |
| R | reference: the same log-volatility regression without the event features (63d / 21d realised volatility, VIX) |
| C, D | the confirmed resizing challenger, alone and combined with B (from 2018, as before) |

Event forecast: log realised volatility over the horizon on [1, log σ63, log σ21, log VIX] plus the two market-wide
calendar features that exist for the market factor — scheduled macro releases (CPI, jobs, GDP, PPI, retail, FOMC) in
the next five sessions and FOMC in the next five sessions (dates known 30 days ahead; unscheduled actions only from the
next day). The per-stock earnings-window features do not exist for SPY and are not used. Pooled over the same assets as
the breadth design (equities, equity ETFs, indices), ridge, trained only on records matured before each era, level
calibrated on SPY's own training records separately for R and B.

## Gates (unchanged)

- **G1** forecast validity: the stage-1 pooled walk-forward hedge-volatility test for the event calendar at this horizon
  has t ≥ 2, AND on this study's dates the event forecast of the market factor has a lower squared log error than R
- **G2** ΔU(λ = 1) > 0: bootstrap one-sided p surviving BH at q = 0.10 across objective × horizon cells, ≥ 60 dates
- **G3** ΔU(λ = 1) > 0 in ≥ 3 of the 4 complete eras
- **G4** no material degradation (hedged ES95 within 2% of the unhedged ES95, basis error within 5%, cost within 10%)
- **G5** live shadow: ≥ 60 graded matched live outcomes with positive ΔU

Statuses as in the breadth study. Nothing is promoted automatically.
