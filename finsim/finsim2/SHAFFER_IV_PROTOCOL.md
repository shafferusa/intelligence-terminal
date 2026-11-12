# Implied volatility and futures curves — pre-registered protocol

Fixed 2026-09-28, **before any result was computed** (the commit adding this file precedes the code and every run).
Research only: the Shaffer System, the Shaffer Hedge, the move-size display and every production output are unchanged.
Code: `engine/newinfo.py` (batch `iv`), `engine/ivstudy.py`. Run: `python -m finsim2 lab --iv`. Report:
`SHAFFER_IV_RESEARCH.md`.

## Question

Does option-implied information (the market's own forecast of volatility, its term structure and its skew) or the
shape of commodity futures curves improve, **on top of what FinSim2 already uses**:

1. **Move size** — the 1D / 1W / 1M volatility forecast behind Shaffer Directional's ranges and the Shaffer Hedge;
2. **Shaffer Directional** — P(up) over 1D – 1W beyond the point-in-time prior;
3. **Shaffer Alpha** — relative ranking at 1M – 12M;
4. **Shaffer Hedge** — the log realised volatility forecast at 1W and longer?

Priors from the literature, stated so the reader can judge surprise: implied volatility is a strong volatility
forecaster (1W – 1M), so a gain on target 1 is expected; the variance risk premium predicts index returns only weakly
at 1 – 3 months; option skew has a small cross-sectional effect; commodity futures carry (roll yield) is a documented
cross-sectional return premium. Nothing here is expected to predict 1D direction.

## Data (all point in time: a value is used only from the day after the close it describes)

| Track | Source | Depth | Where it runs |
|---|---|---|---|
| H — historical | FRED Cboe volatility indices (VIX, VIX3M, VXN, RVX, VXD, GVZ, OVX, VXEEM, VXEWZ, the five single-stock VIXes; `candidates.OWN_IV` maps them to assets); EIA NYMEX contracts 1–4 (WTI 1983 →, natural gas 1994 →, to 2024-04-05) | 1986 / 2007 / 2010 → | here (cloud research store) and the laptop |
| H — Cboe history | Cboe VIX9D (2011 →), VVIX (2007 →), SKEW (1990 →) CSVs | as listed | the laptop only (the host is blocked in the cloud) |
| D — options dump | the free 2008–2025 historical chains (SaidBahaDev, MIT licence stated; SPY / QQQ / IWM confirmed by search, more names if the files have them), reduced per day by `data/optionsdump.py` to the same features as the live snapshots | 2008 – 2025 | the laptop, after import |
| F — forward | the daily Cboe delayed-quote snapshots (`options_cboe`) and the Yahoo contract-month curves (`futures_curve`, `src` = 2) | from 2026-09-28 | the daily loop |

**Dump quality gate (before the dump enters any test).** For SPY, QQQ and IWM the dump's 30-day ATM IV must correlate
≥ 0.90 in daily levels with VIX, VXN and RVX, with a mean absolute difference below 5 volatility points, and cover
≥ 90% of the sessions 2008 – 2025. A dump that fails is refused for research and reported as such.

## Families (the Alpha / Directional / Hedge tests; newinfo batch `iv`)

Features are computed from values published by t; z-scores use the previous 1,260 sessions (≥ 252).
σ21 = RMS of the asset's last 21 daily log returns, annualised by √252.

| Family | Applies to | Features | Track |
|---|---|---|---|
| `iv_index` | assets with an own Cboe index (`OWN_IV`) | `iv_rv_log` = ln(IV / 100) − ln σ21; `iv_z` = z of ln IV; `iv_chg_5d` = ln IV_t − ln IV_t−5 | historical. The Alpha test is a **re-test** (the 2026-09 candidate family "Optionality" had vrp / iv_pctile / d_iv_1m) |
| `vol_term` | every asset (market-wide) | `vix_vix3m_log` = ln(VIX / VIX3M) (FRED, 2007-12 →); `d_vix_vix3m_21d` = its 21-session change | historical; `vix_vix3m_log` re-tests the candidate "vix_term" |
| `vol_cboe` | every asset (market-wide) | `vix9d_vix_log` = ln(VIX9D / VIX); `vvix_z` = z of ln VVIX; `skew_z` = z of SKEW | historical where the Cboe CSVs are stored (laptop); otherwise INSUFFICIENT DATA |
| `iv_chain` | assets with dump rows (`options_hist`) | `chain_iv_rv_log` = ln iv30 − ln σ21; `chain_skew25` = 25-delta put − call IV at 30 days; `chain_term` = ln(iv90 / iv30); `chain_pc_oi` = ln((put OI + 1) / (call OI + 1)) | historical if ≥ 3 complete eras, else INSUFFICIENT DATA |
| `iv_snapshot` | assets with Cboe snapshots (`options_cboe`) | the four `iv_chain` features from the snapshots | LIMITED HISTORY (live-shadow only) |
| `futures_curve` | commodity assets linked by `futcurve.LINKS` (WTI, USO, XLE, BRENT, NATGAS, UNG, GOLD, GLD, SILVER, SLV, COPPER, CPER, CORN, SOYBEANS, WHEAT) | `roll_1_2` = ln(c1 / c2) × 12 / months between them (positive = backwardation); `roll_1_4` = ln(c1 / c4) × 12 / months; `roll_chg_21d` = 21-session change of `roll_1_2` (months from `cN_ym`, else 1 and 3 for the EIA monthly contracts) | historical where EIA covers the root (WTI, natural gas); a real curve replaces the candidate "roll yield" proxy (spot vs ETF) |

Tests, eras, split and gates are exactly those of `engine/newinfo.py` (module docstring, fixed 2026-09-25): Alpha
G1 Δ rank IC t ≥ 2 with Δ IC ≥ 0, G2 ≥ 3 eras won and split t ≥ 1; Directional gate v2 (Brier vs the prior-only model
t ≥ 2, log-loss and accuracy gains > 0, Brier vs the current formulation t ≥ 2; G2 as above); Hedge G1 squared-error
gain t ≥ 2, G2 as above; enough data = ≥ 3 complete eras (≥ 200 records each) and ≥ 2,000 walk-forward records.
Eras 2009–12, 2013–16, 2017–20, 2021–24 (2025– reported, never counted), split 2018. **Benjamini-Hochberg q = 0.10
over this batch's own family × horizon × target G1 tests** (historical track only), horizons 1D, 1W, 1M, 3M, 6M, 12M.
Status per family: SHADOW if any test passes G1, G2 and FDR; else NO INCREMENTAL VALUE; INSUFFICIENT DATA or LIMITED
HISTORY as defined there.

## Move size (target 1; `engine/ivstudy.py`)

Records: the move-size records of `engine/movesize.py` (same assets, from 2001), at 1D (every other session), 1W
(every 5 sessions) and **1M (every 21 sessions, added for the hedge horizon)**; outcomes never overlap.

Reference **V0 = move-size M3** (σ5 / σ21 / σ63, VIX, today's move, asset class, event calendar, 8-K) — the strongest
model FinSim2 has. Challengers add:

| Model | Adds | Records |
|---|---|---|
| V1 | `log_iv` (ln own IV) + the `iv_index` features | assets with an own index; V0 refitted on the same records |
| V2 | the `vol_term` features | all |
| V3 | `log_iv30` (ln iv30) + the `iv_chain` features | records with dump rows; V0 refitted on the same records |
| V4 | every feature above | all |
| V5 | the `vol_cboe` features | all (INSUFFICIENT DATA where the Cboe CSVs are not stored) |

Rules: each added feature is centred on its training mean and a missing value is set to that mean; a feature with
under 20% coverage in an era's training records is left out of that era's fit (so a feature whose data does not exist
cannot distort a run). Fit, level calibration, 90% range (training quantiles of standardised outcomes) and loss
(QLIKE of the variance forecast) exactly as in `SHAFFER_MOVE_SIZE_PROTOCOL.md`; gain = QLIKE(V0) − QLIKE(model) per date.

Gates per horizon × model: **G1** gain > 0 with t ≥ 2; **G2** gain > 0 in all but one of the complete eras (≥ 3,
2025– excluded) and 2018 split t ≥ 1; **G3** out-of-sample 90% coverage 87 – 93%; **BH q = 0.10 over these 15 tests** (3 horizons × 5 models).
Also reported, not gated: the mean width of the 90% range relative to V0 (narrower at the same coverage = sharper
Directional ranges and cheaper hedges).

A model whose data does not cover ≥ 3 complete eras is INSUFFICIENT DATA (V3 before the dump is imported, V5 without the
Cboe CSVs) and is left out of that run's BH count.

## What a pass means

A passing model or family becomes a **research display** only. Using implied volatility in the Shaffer System's
ranges, in Directional, or in Hedge sizing needs the owner's approval and a revision entry in `SHAFFER_SYSTEM.md` §5.
Nothing is promoted automatically.

## Runs

Each family and model is judged once, on the run where its data first exists: the cloud run for `iv_index`,
`vol_term`, `futures_curve`, V1, V2 and V4 (V4 then without the Cboe CSV features); the laptop run
(`python -m finsim2 lab --iv`) for `vol_cboe` and V5; the laptop run after the dump import for `iv_chain` and V3. Later runs are reported as re-runs, never as
new confirmations. `iv_snapshot` and the Yahoo curve accumulate forward and are first evaluated after 12 months.
