# New-data signal families — pre-registered protocol (batch 2)

Fixed 2026-09-27, **before any result was computed**. Research only: nothing here changes production, D, E, the
Directional prior-only model, hedge-2 or the λ-hedge shadow. Run with `python -m finsim2 lab --new-data`; results go to
`NEW_DATA_SIGNALS.md` and the store key `lab:newinfo:b2`.

## Question

Does the information in the new data sources (NEW_DATA_SOURCES.md) improve, **incrementally over the frozen benchmark**,
- Shaffer **Alpha** (relative ranking; Δ rank IC over the production score),
- Shaffer **Directional** (P(up) beyond the PIT prior and the current formulation; Brier / log loss / accuracy),
- Shaffer **Hedge** risk modelling (log realised volatility beyond trailing 63 / 21-day volatility and VIX)?

## Families and features (exactly as coded in `engine/newinfo.py`, batch 2)

| Family | Applies to | Features | Track |
|---|---|---|---|
| insider | equities with a CIK | net open-market buying minus discretionary (non-10b5-1) selling over 63 sessions, buying over 126, officer / CEO / CFO buying over 126 (each per 1e-4 of 63-day dollar volume, asinh), log buyer count over 126, discretionary-selling z vs its own 3 years | historical (2006 →) |
| sec_events | equities with a CIK | log counts: all 8-Ks (63 sessions), 5.02 officer / director changes (126), 1.01 + 2.01 agreements / acquisitions (126), 2.05 / 2.06 / 3.01 / 4.01 / 4.02 restructuring, impairment, delisting, auditor change, non-reliance (252), 7.01 + 8.01 other events (21) | historical (2004-08 →) |
| event_calendar | every asset (earnings features: equities) | scheduled macro releases (CPI, jobs, GDP, PPI, retail, FOMC) in the next five sessions, FOMC in the next five sessions, earnings expected within 5 / 21 sessions (last 8-K 2.02 + 63 sessions), sessions since the last release / 63 | historical |
| cftc_positioning | assets in `data/cftc.LINKS` | speculator (non-commercial) net / open interest z vs 3 years, its 13-week change, commercial net z, managed-money (commodities) or leveraged-fund (financials) net z — each signed so + = long the asset | historical (1986 →) |
| commodity_inventories | WTI, BRENT, USO, XLE (crude); NATGAS, UNG (gas storage) | weekly change minus the same weeks' mean change over the prior 5 years (z), level vs the same weeks over 5 years (z) | historical |
| crypto_derivs | BTC, ETH, SOL, IBIT, BITO | 5-session mean funding (per 8h), its z vs 63 sessions, 5-session mean CME basis | LIMITED HISTORY (2019 →) |

Point in time: every value is used only from its publication date (Form 4: next day; 8-K: acceptance time, after the
close → next day; COT: the Saturday after Tuesday's positions, shutdown catch-up dates; EIA: Thursday / Friday; crypto:
next UTC day; scheduled macro dates: 30 days ahead; unscheduled FOMC actions: the next day).

## Tests and gates (unchanged from the 2026-09-25 new-information protocol)

Same records (the frozen lab research records — the original universe, so the expanded 500-stock universe and its
survivorship bias do not enter this test), same eras, same 2018 split, same fits and metrics as `engine/newinfo.py`.

- enough: ≥ 3 complete test eras (≥ 200 records each) and ≥ 2,000 walk-forward records
- Alpha: G1 walk-forward Δ rank IC t ≥ 2 and Δ IC ≥ 0; G2 Δ rank IC > 0 in ≥ 3 complete eras and split t ≥ 1
- Directional: G1 vs prior-only Brier gain t ≥ 2, log-loss gain > 0, accuracy gain > 0, and vs the current formulation
  Brier gain t ≥ 2; G2 Brier gain vs prior-only > 0 in ≥ 3 eras, split t ≥ 1
- Hedge: G1 walk-forward squared-error gain t ≥ 2; G2 > 0 in ≥ 3 eras, split t ≥ 1
- FDR: Benjamini-Hochberg at q = 0.10 over **this batch's** family × horizon × target G1 tests only (the batch-1 results
  are not re-opened); individual features get their own BH correction and are never admitted one by one.
- Status: SHADOW if any test passes G1, G2 and FDR; otherwise NO INCREMENTAL VALUE; LIMITED HISTORY for crypto.

A passing family becomes a **shadow** input for the next stages (horizon / sector weights for Alpha 1M–5Y, Directional
1D–1W, Hedge). It reaches a score only through those stages' own gates and the owner's approval.
