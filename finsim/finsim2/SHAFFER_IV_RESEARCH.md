# Implied volatility and futures curves — research

Protocol `SHAFFER_IV_PROTOCOL.md` (fixed 2026-09-28 before any result). Families run 2026-09-28 18:08:25, move size 2026-09-28 18:47:12. **Research only** — the Shaffer System, the Hedge and the move-size display are unchanged; a pass is a research display until the owner approves its use.

## Verdict

- **Move size:** V1 at 1D, V2 at 1D, V4 at 1D PASSED
- **Own implied volatility (Cboe indices on FRED)** (`iv_index`): SHADOW — passed G1, G2 and FDR: 1W hedge, 1M hedge, 3M hedge
- **VIX term structure (FRED)** (`vol_term`): SHADOW — passed G1, G2 and FDR: 1W hedge
- **VIX9D, VVIX, SKEW (Cboe CSVs)** (`vol_cboe`): INSUFFICIENT DATA — fewer than 3 complete eras with data
- **Option-chain features (free 2008–2025 dump)** (`iv_chain`): INSUFFICIENT DATA — fewer than 3 complete eras with data
- **Option-chain features (daily Cboe snapshots)** (`iv_snapshot`): LIMITED HISTORY — no incremental value in the recent eras it covers
- **Futures-curve roll yield** (`futures_curve`): NO INCREMENTAL VALUE — no test passed G1, G2 and the FDR control

## Reading (written after the run)

- **What passed is about the size of moves, not their direction.** Own implied volatility and the VIX / VIX3M ratio
  improve volatility forecasts: for the hedge baseline (σ63, σ21, VIX) at 1W – 3M, and on top of the full move-size
  model (M3) at 1D. No implied-volatility family improved Alpha ranking or Directional P(up) at any horizon; at 1D both
  made Alpha ranking worse.
- **Against the stronger reference the gains shrink with the horizon.** At 1W the own-IV model gained in only 1 of 3
  complete eras over M3 (G2 failed); at 1M the 20 own-IV assets give too few records (4,936). The hedge-baseline test is
  easier to beat because that baseline has no event calendar and no short-term volatility.
- **V4 equals V2 in this run.** The own-IV and option-chain features cover under 20% of all records and the Cboe CSV
  features have no data here, so the coverage rule left only the VIX term structure in V4. Both were counted in the
  FDR control (conservative).
- **Still to be judged on the laptop, once each:** VIX9D / VVIX / SKEW (`vol_cboe`, V5) after `python -m finsim2 data cboe`,
  and the option-chain features (`iv_chain`, V3) after the options dump is imported and passes its quality gate.
- **Nothing changes in the app.** Using implied volatility in the Shaffer System's Directional ranges or the Hedge's
  volatility input needs the owner's approval and a revision entry in SHAFFER_SYSTEM.md §5.

## Target 1 — move size with implied volatility (1D / 1W / 1M)

Built 2026-09-28 18:47:12. Reference V0 = move-size M3 (σ5 / σ21 / σ63, VIX, today's move, class, event calendar, 8-K). QLIKE gain = V0's loss − the model's loss (positive = better), per date, walk-forward, V0 refitted on the same records. PASSED needs G1 (gain t ≥ 2), G2 (gain in all but one complete era, ≥ 3; split t ≥ 1), G3 (90% range covers 87–93%) and BH q = 0.10 over the 8 evaluable tests. Range width = the model's mean 90% range ÷ V0's (below 1 = sharper).

| Horizon | Model | Records (assets) | QLIKE gain (t) | Eras won | Split t | 90% coverage | Range width | Status |
|---|---|---|---|---|---|---|---|---|
| 1D | V1 + own implied volatility (Cboe index) | 51908 (20) | +0.0227 (+3.7) | 3/4 | +3.4 | 90.7% | 1.028 | PASSED |
| 1D | V2 + VIX term structure (VIX / VIX3M) | 1536528 (561) | +0.0088 (+3.2) | 3/4 | +3.2 | 90.0% | 0.991 | PASSED |
| 1D | V3 + option-chain features (dump) | 0 (0) | — (—) | —/— | — | — | — | INSUFFICIENT DATA (0 records) |
| 1D | V4 + every implied-volatility feature | 1536528 (561) | +0.0088 (+3.2) | 3/4 | +3.2 | 90.0% | 0.991 | PASSED |
| 1D | V5 + VIX9D, VVIX, SKEW (Cboe CSVs) | 1536528 (561) | +0.0000 (—) | 0/4 | — | 90.2% | 1.000 | INSUFFICIENT DATA (its features have data in 0 complete era(s)) |
| 1W | V1 + own implied volatility (Cboe index) | 20755 (20) | +0.0162 (+2.2) | 1/3 | +2.5 | 89.5% | 1.035 | NOT VALIDATED |
| 1W | V2 + VIX term structure (VIX / VIX3M) | 614539 (561) | +0.0065 (+1.4) | 1/4 | +1.9 | 89.8% | 0.988 | NOT VALIDATED |
| 1W | V3 + option-chain features (dump) | 0 (0) | — (—) | —/— | — | — | — | INSUFFICIENT DATA (0 records) |
| 1W | V4 + every implied-volatility feature | 614539 (561) | +0.0065 (+1.4) | 1/4 | +1.9 | 89.8% | 0.988 | NOT VALIDATED |
| 1W | V5 + VIX9D, VVIX, SKEW (Cboe CSVs) | 614539 (561) | +0.0000 (—) | 0/4 | — | 90.1% | 1.000 | INSUFFICIENT DATA (its features have data in 0 complete era(s)) |
| 1M | V1 + own implied volatility (Cboe index) | 4936 (20) | — (—) | —/— | — | — | — | INSUFFICIENT DATA (4936 records) |
| 1M | V2 + VIX term structure (VIX / VIX3M) | 146166 (561) | +0.0107 (+1.0) | 3/4 | +1.0 | 90.1% | 0.995 | NOT VALIDATED |
| 1M | V3 + option-chain features (dump) | 0 (0) | — (—) | —/— | — | — | — | INSUFFICIENT DATA (0 records) |
| 1M | V4 + every implied-volatility feature | 146166 (561) | +0.0107 (+1.0) | 3/4 | +1.0 | 90.1% | 0.995 | NOT VALIDATED |
| 1M | V5 + VIX9D, VVIX, SKEW (Cboe CSVs) | 146166 (561) | +0.0000 (—) | 0/4 | — | 90.3% | 1.000 | INSUFFICIENT DATA (its features have data in 0 complete era(s)) |


## Targets 2–4 — Alpha, Directional and Hedge (newinfo batch `iv`)

## New information for the Shaffer systems — research

Run 2026-09-28 18:08:25 · 2180.7 s · generated by `python -m finsim2 lab --newinfo`. Research only: production scoring, the Directional research definition and the hedge math are unchanged, and no family is added to production.

**Benchmark:** `benchmark-2.1-2026-09-25` (frozen 2026-09-25 21:16:16, sha256 `ce0f502bbaba5d5a34b7828df1fa286f4855baeb8767d4610a01a188818cdcdc`) — Shaffer Score `shaffer-2.1`, Shaffer Alpha `shaffer-alpha-2.1-production`, Shaffer Hedge `hedge-2`; the Directional research definition (prior-only and current). Every family is judged by what it adds to this benchmark on identical records.

Question: which genuinely new point-in-time information improves Shaffer Alpha (relative ranking), Shaffer Directional (absolute direction beyond the PIT prior) or Shaffer Hedge modelling (volatility forecasts) *incrementally*? Families were built only from information absent from the 74 production signals and the earlier candidate families (the earnings-surprise family is labelled as a re-test). Data sources and their status: `NEW_DATA_SOURCES.md`.

Protocol (fixed before any result): weights and standardisation frozen before each era (→ 2008 test 2009–12, → 2012 test 2013–16, → 2016 test 2017–20, → 2020 test 2021–24, → 2024 test 2025–) and the 2018 split; Alpha — paired Δ rank IC against the production score; Directional — gate v2 (beat the prior-only model: Brier t ≥ 2, lower log loss, higher accuracy; and the current formulation: Brier t ≥ 2); Hedge — squared error of the log realised-volatility forecast against a baseline of 63-day and 21-day realised volatility and VIX; G2 ≥ 3 of 4 eras and the split; Benjamini-Hochberg at q = 0.1 across all 51 family × horizon × target tests. Families whose data start after 2018 are on a separate LIMITED HISTORY track and are never presented as historically verified.

### The main table

| Family | Assets | Historical coverage | Alpha Δ rank IC (best horizon, t) | Directional Δ Brier vs prior (best, t) | Directional Δ accuracy vs prior | Hedge improvement (best, t) | Era stability | Status |
|---|---|---|---|---|---|---|---|---|
| Own implied volatility (Cboe indices on FRED) | 20 | from 2001-09-05 (historical) | +0.068 (3M, t +1.1) | +0.00032 (1M, t +0.2) | +1.30% | +14.1% of baseline error (1W, t +11.6) | 4/4 eras | **SHADOW** |
| VIX term structure (FRED) | 559 | from 2007-12-05 (historical) | +0.013 (6M, t +0.4) | +0.00060 (1D, t +2.5) | -0.03% | +3.4% of baseline error (1W, t +4.3) | 3/4 eras | **SHADOW** |
| VIX9D, VVIX, SKEW (Cboe CSVs) | 0 | from — (historical) | — (—, t —) | — (—, t —) | — | — (—, t —) | —/— eras | **INSUFFICIENT DATA** |
| Option-chain features (free 2008–2025 dump) | 0 | from — (historical) | — (—, t —) | — (—, t —) | — | — (—, t —) | —/— eras | **INSUFFICIENT DATA** |
| Option-chain features (daily Cboe snapshots) | 0 | from — (limited) | — (—, t —) | — (—, t —) | — | — (—, t —) | —/— eras | **LIMITED HISTORY** |
| Futures-curve roll yield | 5 | from 2001-12-27 (historical) | — (—, t —) | +0.00209 (1M, t +0.8) | +3.91% | -6.4% of baseline error (12M, t -0.3) | 3/3 eras | **NO INCREMENTAL VALUE** |

"Best horizon" = the horizon with the largest t (a positive best is not a pass: passing needs G1, G2 and the FDR control). Hedge improvement = the walk-forward reduction in squared error of the log-volatility forecast, as a share of the baseline's own squared error. Era stability = the most test eras with a gain, of the complete ones, over the family's tests.

### Reading these results

- **Directional: nothing.** No family × horizon reached gate v2's G1 (beating both the prior-only model and the current formulation). The prior-only model remains the Directional benchmark; no new information in this study changes absolute direction.
- **Own implied volatility (Cboe indices on FRED) — hedge at 1W:** the log-volatility forecast's squared error falls by 14.1% of the baseline's (t +11.6, 16,800 forecasts) — a material improvement.
- **VIX term structure (FRED) — hedge at 1W:** the log-volatility forecast's squared error falls by 3.4% of the baseline's (t +4.3, 415,061 forecasts) — a modest improvement.
- **Own implied volatility (Cboe indices on FRED) — hedge at 1M:** the log-volatility forecast's squared error falls by 22.8% of the baseline's (t +7.3, 16,908 forecasts) — a material improvement.
- **Own implied volatility (Cboe indices on FRED) — hedge at 3M:** the log-volatility forecast's squared error falls by 16.9% of the baseline's (t +3.2, 16,711 forecasts) — a material improvement.
- **What the hedge result is not.** Only volatility forecasts were tested. Hedge ratios, sizing, covariance and tail outcomes were not, and the Shaffer Hedge math is unchanged: a better volatility forecast is a candidate input, recorded in live shadow, not a better hedge.
- **Option-chain features (daily Cboe snapshots): LIMITED HISTORY.** No incremental value in the recent eras it covers. Its data start in 2019, so it is judged only on the eras it covers and is never presented as historically verified; the pre-2018 gates were not relaxed for it.
- **Everything else.** Futures-curve roll yield: no incremental value. VIX9D, VVIX, SKEW (Cboe CSVs), Option-chain features (free 2008–2025 dump): too few assets or eras to judge. 

### Answers

**1. Which new datasets were actually obtainable?** From the allowed domains: SEC first-reported filings and the own price / volume panel (earnings events), FRED credit, term-premium, real-yield, breakeven and foreign short-rate series, FINRA Reg SHO short-sale volume (2019 →), and — only as recent windows or daily quotas — Finnhub earnings / recommendations, Nasdaq short interest (1 year), Massive whole-market aggregates (2 years), Alpha Vantage consensus (25 requests a day). Full list with status: `NEW_DATA_SOURCES.md`.

**2. Which were point in time historically?** Own implied volatility (Cboe indices on FRED) — daily close, used from the next day (historical track); VIX term structure (FRED) — daily close, used from the next day (historical track); VIX9D, VVIX, SKEW (Cboe CSVs) — daily close, used from the next day (historical track); Option-chain features (free 2008–2025 dump) — session close, used from the next day (historical track); Option-chain features (daily Cboe snapshots) — after the close, used from the next day (LIMITED HISTORY: starts after 2018); Futures-curve roll yield — settlement, used from the next day (historical track).

**3. Which families were built?**

| Family | Features | Source | Data quality | Records (largest horizon) | Coverage by feature |
|---|---|---|---|---|---|
| Own implied volatility (Cboe indices on FRED) *(re-test)* | iv_rv_log, iv_z, iv_chg_5d | FRED Cboe volatility indices mapped by candidates.OWN_IV (VIX, VXN, RVX, VXD, GVZ, OVX, VXEEM, VXEWZ, 5 stocks) | high (Cboe's own index methodology) | 19,482 | iv_rv_log 100%, iv_z 97%, iv_chg_5d 100% |
| VIX term structure (FRED) *(re-test)* | vix_vix3m_log, d_vix_vix3m_21d | FRED VIXCLS / VXVCLS (2007-12 →) | high | 434,876 | vix_vix3m_log 100%, d_vix_vix3m_21d 100% |
| VIX9D, VVIX, SKEW (Cboe CSVs) | vix9d_vix_log, vvix_z, skew_z | Cboe daily history CSVs (data/cboe.py; VIX9D 2011 →, VVIX 2007 →, SKEW 1990 →) | high | 0 | — |
| Option-chain features (free 2008–2025 dump) | chain_iv_rv_log, chain_skew25, chain_term, chain_pc_oi | historical chains imported by data/optionsdump.py -> options_hist | depends on the dump's quality gate (SHAFFER_IV_PROTOCOL.md) | 0 | — |
| Option-chain features (daily Cboe snapshots) | chain_iv_rv_log, chain_skew25, chain_term, chain_pc_oi | Cboe delayed quotes (data/cboe.py -> options_cboe), from 2026-09-28 | high (delayed quotes, FinSim2's own IV interpolation) | 0 | — |
| Futures-curve roll yield | roll_1_2, roll_1_4, roll_chg_21d | EIA NYMEX contracts 1–4 (WTI 1983 →, natural gas 1994 →, to 2024-04-05) + Yahoo contract months (2026-09 →) | high (exchange settlements); April 2024 – September 2026 gap | 4,294 | roll_1_2 100%, roll_1_4 100%, roll_chg_21d 100% |

**4. Which improved Alpha ranking?** None passed G1, G2 and the FDR control.

**5. Which improved Directional forecasts beyond the prior-only model?** None passed G1, G2 and the FDR control.

**6. Which improved hedge modelling (volatility forecasts beyond 63d / 21d realised volatility and VIX)?** Passed G1, G2 and FDR: Own implied volatility (Cboe indices on FRED) at 1W; VIX term structure (FRED) at 1W; Own implied volatility (Cboe indices on FRED) at 1M; Own implied volatility (Cboe indices on FRED) at 3M.

**7. Which effects survived all eras?** Own implied volatility (Cboe indices on FRED) — hedge at 1W; Own implied volatility (Cboe indices on FRED) — hedge at 1M; Futures-curve roll yield — directional at 1M; Own implied volatility (Cboe indices on FRED) — hedge at 3M (a gain in every complete era; significance is a separate question).

**8. Which effects were regime-specific?** Significant (t ≥ 2) inside one regime state while failing G1 overall — not tested against the multiple comparisons this search involves, so these are leads, not findings: VIX term structure (FRED) — Directional at 1D in bear (t +2.5, n_eff 198); VIX term structure (FRED) — Directional at 1D in high_vol (t +2.1, n_eff 500); VIX term structure (FRED) — Directional at 1D in recession (t +2.3, n_eff 146).

**9. Which disappeared after multiple-testing correction?** Family tests: 51 run, 5 survive Benjamini-Hochberg at q = 0.1. Individual features: 48 tests, 0 nominally significant (one-sided p < 0.05), 0 survive the correction. Features are never admitted one by one.

**10. Which should remain shadow?** Own implied volatility (Cboe indices on FRED), VIX term structure (FRED) (passed every historical gate); LIMITED HISTORY (never historically verified; a live-shadow track is the only route open to it): Option-chain features (daily Cboe snapshots) — no incremental value in the recent eras it covers, so it is not recorded live either.

**11. Which qualify for live shadow?** Exactly the tests that passed every gate: Own implied volatility (Cboe indices on FRED) — hedge at 1W (`newinfo-iv-index-hedge-1W`); VIX term structure (FRED) — hedge at 1W (`newinfo-vol-term-hedge-1W`); Own implied volatility (Cboe indices on FRED) — hedge at 1M (`newinfo-iv-index-hedge-1M`); Own implied volatility (Cboe indices on FRED) — hedge at 3M (`newinfo-iv-index-hedge-3M`). `python -m finsim2 lab --live-models` fits each on every matured record and registers it as *live shadow* (outside the promotion path); the daily learning job then records its forecast for every tracked asset in the append-only ledger — alpha scores with each feature's contribution, graded against production by cross-sectional rank IC on the same dates; volatility forecasts next to the baseline's own forecast, graded against realised volatility. The rest of a SHADOW family (other targets, other horizons) is not recorded. Separately, the benchmark's own prior-only and current Directional p_up are recorded daily for every tracked asset and horizon, so any future challenger has a live comparison.

**12. Which are blocked by data availability?** Analyst expectations / revisions — no point-in-time estimate history on the available tiers (Finnhub estimates / revisions 403; Alpha Vantage 25 requests a day); Option surface / skew — no historical chains (Yahoo options needs authentication; Cboe chain statistics retired); pricing stays MODEL-PRICED — FLAT VOLATILITY ASSUMPTION. Since 2026-09-28 tested in batch iv (SHAFFER_IV_PROTOCOL.md); Dated futures curves — only unexpired contracts are downloadable, so past curves cannot be rebuilt. Since 2026-09-28: EIA contracts 1–4 to April 2024, tested in batch iv; Short interest — only the last year is available (Nasdaq); IG / HY OAS history, CDS, CDX, ratings — ICE OAS on FRED is limited to 3 years; no free CDS / ratings source; ETF / fund flows, creations / redemptions — no free point-in-time source; FX forward points, inflation differentials — no forward-point source; foreign CPI on FRED is stale (UK to 2025-03, Japan to 2021).

**13. Which data source would add the most future value?** An analyst-estimates source with point-in-time revision history (the highest-priority missing family), then CFTC positioning (`www.cftc.gov` on the allowlist), EIA inventories (`api.eia.gov`), and historical option chains. Each is an owner's data decision, not something to approximate.

**14. Is the 74-signal set information-limited rather than weight-limited?** For relative ranking, neither lever helped: every reweighting was rejected and no new family passed. For absolute direction, the limit is neither weights nor the families tested: no family beat the prior-only and current models, so direction beyond the prior is not demonstrated from any information available here.

**15. Is anything eligible for promotion?** No. Promotion needs the historical gates, the FDR control and a live-shadow record (G3); no family has live history, and nothing is added to production in this phase.

### Per family

#### Own implied volatility (Cboe indices on FRED) — SHADOW

passed G1, G2 and FDR: 1W hedge, 1M hedge, 3M hedge. Source: FRED Cboe volatility indices mapped by candidates.OWN_IV (VIX, VXN, RVX, VXD, GVZ, OVX, VXEEM, VXEWZ, 5 stocks). PIT: daily close, used from the next day.

| Horizon | Records | Alpha Δ rank IC (t) | Alpha Δ IC (t) | Dir. Brier vs prior (t) | vs current (t) | Log loss vs prior (t) | Δ accuracy vs prior | Balanced acc. | Hedge vol gain (t) | Eras won A / D / H | Split t A / D / H |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1D | 16,790 | -0.0598 (-3.4) | -0.0155 (-0.5) | -0.00095 (-2.3) | -1.7 | -2.3 | -1.05% | 49.5% | — | 0 / 1 / — of 4 | -1.1 / +1.4 / — |
| 1W | 16,800 | -0.0169 (-1.4) | -0.0107 (-0.5) | -0.00004 (-0.1) | +0.1 | -0.1 | -0.72% | 50.0% | +0.0318 (+11.6) = 14.1% of baseline | 1 / 2 / 4 of 4 | -1.0 / -0.1 / +8.1 |
| 1M | 16,908 | -0.0042 (-0.1) | +0.0407 (+0.8) | +0.00032 (+0.2) | +0.3 | +0.2 | +1.30% | 50.1% | +0.0305 (+7.3) = 22.8% of baseline | 3 / 2 / 4 of 4 | +0.0 / -0.5 / +5.3 |
| 3M | 16,711 | +0.0682 (+1.1) | +0.0291 (+0.4) | -0.00198 (-0.6) | -0.1 | -0.7 | +2.95% | 48.1% | +0.0217 (+3.2) = 16.9% of baseline | 2 / 1 / 4 of 4 | +0.3 / -1.2 / +2.8 |
| 6M | 16,098 | +0.0549 (+0.9) | +0.0035 (+0.0) | -0.00614 (-1.0) | -0.3 | -1.3 | +1.78% | 47.6% | +0.0144 (+1.7) = 11.4% of baseline | 3 / 1 / 3 of 4 | +0.3 / -0.9 / +2.0 |
| 12M | 11,784 | +0.0445 (+0.3) | +0.2251 (+1.2) | -0.01588 (-1.6) | -1.5 | -1.8 | -0.95% | 49.4% | +0.0141 (+0.9) = 10.6% of baseline | 1 / 0 / 2 of 3 | +0.5 / -1.2 / +1.4 |

Individual features (Alpha Δ rank IC t by horizon; † nominal p < 0.05, ✓ survives FDR): iv_rv_log: 1D -3.7, 1W -1.2, 1M -0.1, 3M +0.9, 6M +0.4, 12M +0.7; iv_z: 1D -3.0, 1W +0.7, 1M +0.0, 3M +0.8, 6M +1.0, 12M +0.4; iv_chg_5d: 1D -3.4, 1W -0.8, 1M -0.6, 3M +0.1, 6M +0.2, 12M +0.7

#### VIX term structure (FRED) — SHADOW

passed G1, G2 and FDR: 1W hedge. Source: FRED VIXCLS / VXVCLS (2007-12 →). PIT: daily close, used from the next day.

| Horizon | Records | Alpha Δ rank IC (t) | Alpha Δ IC (t) | Dir. Brier vs prior (t) | vs current (t) | Log loss vs prior (t) | Δ accuracy vs prior | Balanced acc. | Hedge vol gain (t) | Eras won A / D / H | Split t A / D / H |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1D | 411,223 | -0.0509 (-8.8) | -0.0031 (-0.2) | +0.00060 (+2.5) | +2.3 | +2.5 | -0.03% | 49.4% | — | 0 / 1 / — of 4 | -0.7 / -1.0 / — |
| 1W | 415,067 | -0.0219 (-3.6) | +0.0021 (+0.1) | -0.00080 (-3.2) | -2.8 | -3.2 | -0.12% | 50.2% | +0.0085 (+4.3) = 3.4% of baseline | 0 / 1 / 3 of 4 | -1.0 / +1.3 / +2.3 |
| 1M | 414,643 | -0.0005 (-0.0) | +0.0066 (+0.2) | -0.00352 (-3.7) | -3.7 | -3.7 | +0.06% | 49.4% | -0.0038 (-1.1) = -2.7% of baseline | 2 / 2 / 1 of 4 | +1.5 / +1.2 / +0.7 |
| 3M | 405,734 | +0.0036 (+0.1) | +0.0645 (+1.5) | -0.00782 (-2.8) | -2.8 | -2.8 | -0.33% | 48.7% | -0.0030 (-0.2) = -2.2% of baseline | 1 / 2 / 2 of 4 | -0.5 / +0.0 / -0.1 |
| 6M | 382,920 | +0.0129 (+0.4) | +0.1327 (+1.5) | -0.02516 (-1.8) | -1.8 | -2.1 | +0.06% | 47.7% | -0.0663 (-2.7) = -38.5% of baseline | 1 / 2 / 0 of 4 | -0.7 / +0.4 / -0.6 |
| 12M | 259,137 | +0.0142 (+0.3) | +0.1268 (+1.0) | -0.00018 (-0.1) | +0.8 | -0.1 | -0.48% | 49.7% | -0.0416 (-1.1) = -25.3% of baseline | 2 / 2 / 1 of 3 | -0.9 / -1.4 / -0.1 |

Individual features (Alpha Δ rank IC t by horizon; † nominal p < 0.05, ✓ survives FDR): vix_vix3m_log: 1D -8.6, 1W -3.4, 1M -0.8, 3M +0.1, 6M +0.3, 12M +0.1; d_vix_vix3m_21d: 1D -8.4, 1W -5.2, 1M -0.7, 3M -0.8, 6M -0.8, 12M -0.4

#### VIX9D, VVIX, SKEW (Cboe CSVs) — INSUFFICIENT DATA

fewer than 3 complete eras with data. Source: Cboe daily history CSVs (data/cboe.py; VIX9D 2011 →, VVIX 2007 →, SKEW 1990 →). PIT: daily close, used from the next day.

| Horizon | Records | Alpha Δ rank IC (t) | Alpha Δ IC (t) | Dir. Brier vs prior (t) | vs current (t) | Log loss vs prior (t) | Δ accuracy vs prior | Balanced acc. | Hedge vol gain (t) | Eras won A / D / H | Split t A / D / H |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1D | 0 | fewer than 1,000 records with data | | | | | | | | | |
| 1W | 0 | fewer than 1,000 records with data | | | | | | | | | |
| 1M | 0 | fewer than 1,000 records with data | | | | | | | | | |
| 3M | 0 | fewer than 1,000 records with data | | | | | | | | | |
| 6M | 0 | fewer than 1,000 records with data | | | | | | | | | |
| 12M | 0 | fewer than 1,000 records with data | | | | | | | | | |

#### Option-chain features (free 2008–2025 dump) — INSUFFICIENT DATA

fewer than 3 complete eras with data. Source: historical chains imported by data/optionsdump.py -> options_hist. PIT: session close, used from the next day.

| Horizon | Records | Alpha Δ rank IC (t) | Alpha Δ IC (t) | Dir. Brier vs prior (t) | vs current (t) | Log loss vs prior (t) | Δ accuracy vs prior | Balanced acc. | Hedge vol gain (t) | Eras won A / D / H | Split t A / D / H |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1D | 0 | fewer than 1,000 records with data | | | | | | | | | |
| 1W | 0 | fewer than 1,000 records with data | | | | | | | | | |
| 1M | 0 | fewer than 1,000 records with data | | | | | | | | | |
| 3M | 0 | fewer than 1,000 records with data | | | | | | | | | |
| 6M | 0 | fewer than 1,000 records with data | | | | | | | | | |
| 12M | 0 | fewer than 1,000 records with data | | | | | | | | | |

#### Option-chain features (daily Cboe snapshots) — LIMITED HISTORY

no incremental value in the recent eras it covers. Source: Cboe delayed quotes (data/cboe.py -> options_cboe), from 2026-09-28. PIT: after the close, used from the next day.

| Horizon | Records | Alpha Δ rank IC (t) | Alpha Δ IC (t) | Dir. Brier vs prior (t) | vs current (t) | Log loss vs prior (t) | Δ accuracy vs prior | Balanced acc. | Hedge vol gain (t) | Eras won A / D / H | Split t A / D / H |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1D | 0 | fewer than 1,000 records with data | | | | | | | | | |
| 1W | 0 | fewer than 1,000 records with data | | | | | | | | | |
| 1M | 0 | fewer than 1,000 records with data | | | | | | | | | |
| 3M | 0 | fewer than 1,000 records with data | | | | | | | | | |
| 6M | 0 | fewer than 1,000 records with data | | | | | | | | | |
| 12M | 0 | fewer than 1,000 records with data | | | | | | | | | |

#### Futures-curve roll yield — NO INCREMENTAL VALUE

no test passed G1, G2 and the FDR control. Source: EIA NYMEX contracts 1–4 (WTI 1983 →, natural gas 1994 →, to 2024-04-05) + Yahoo contract months (2026-09 →). PIT: settlement, used from the next day.

| Horizon | Records | Alpha Δ rank IC (t) | Alpha Δ IC (t) | Dir. Brier vs prior (t) | vs current (t) | Log loss vs prior (t) | Δ accuracy vs prior | Balanced acc. | Hedge vol gain (t) | Eras won A / D / H | Split t A / D / H |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1D | 2,678 | — (—) | +0.0567 (+1.9) | -0.00059 (-1.4) | -1.6 | -1.4 | -0.59% | 49.0% | — | 0 / 2 / — of 3 | +0.0 / -1.8 / — |
| 1W | 2,688 | — (—) | +0.0398 (+1.0) | -0.00099 (-1.3) | -1.4 | -1.3 | -0.52% | 50.6% | -0.0017 (-1.7) = -0.9% of baseline | 0 / 1 / 1 of 3 | +0.0 / -0.2 / -0.8 |
| 1M | 2,783 | — (—) | +0.0911 (+1.2) | +0.00209 (+0.8) | +0.8 | +0.7 | +3.91% | 51.1% | -0.0024 (-0.9) = -2.0% of baseline | 0 / 3 / 1 of 3 | +0.0 / +0.3 / -0.8 |
| 3M | 2,740 | — (—) | +0.1439 (+1.4) | +0.00434 (+0.6) | +0.6 | +0.5 | +0.22% | 50.6% | -0.0053 (-0.9) = -3.8% of baseline | 0 / 2 / 1 of 3 | +0.0 / +0.0 / -1.1 |
| 6M | 1,832 | — (—) | +0.0409 (+0.3) | -0.03049 (-1.3) | -1.3 | -1.3 | -4.89% | 44.9% | -0.0043 (-0.5) = -2.5% of baseline | 0 / 0 / 0 of 2 | +0.0 / -1.3 / -0.8 |
| 12M | 823 | — (—) | -0.0656 (-0.1) | -0.16011 (-1.7) | -1.7 | -1.7 | -4.39% | 42.7% | -0.0072 (-0.3) = -6.4% of baseline | 0 / 0 / 0 of 1 | +0.0 / +0.0 / +0.0 |

Individual features (Alpha Δ rank IC t by horizon; † nominal p < 0.05, ✓ survives FDR): roll_1_2: 1D —, 1W —, 1M —, 3M —, 6M —, 12M —; roll_1_4: 1D —, 1W —, 1M —, 3M —, 6M —, 12M —; roll_chg_21d: 1D —, 1W —, 1M —, 3M —, 6M —, 12M —

