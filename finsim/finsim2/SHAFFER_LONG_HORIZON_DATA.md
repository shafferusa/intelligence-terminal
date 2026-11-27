# Long-horizon Alpha and Directional — does more history help?

Research only. The standard research records start where production's reconstructed record starts (25 years back: 2001 for the oldest assets). `engine/extrecords.py` adds research-only records for the weeks before that — the production signals' point-in-time z-scores and the realised outcome, no production score — so they can only be training data (every test era starts in 2009). The 1M–12M study (`finetune.study_long`: stronger shrinkage, family-level weights, top-10/20, stable-only, fundamental subsets, class hierarchy, nested selection; G1–G4 + FDR) was rerun on standard + extended records and compared with the standard run.

## Key findings

- **Alpha:** more training history raises the learned global model's walk-forward rank IC — 1M +0.0106 → +0.0170; 3M -0.0185 → -0.0031; 6M +0.0041 → +0.0252; 12M +0.0644 → +0.0803. Nothing clears G1 (t ≥ 2 vs production) + FDR: better, not yet good enough.
- **Directional:** 12 of 12 learned Directional variants at 1M–6M are still worse than the point-in-time prior with extended history (negative Brier gain) — more history does not rescue long-horizon direction; the prior stays.
- **The limit is time and missing data, not model choice:** test eras are unchanged (2009 on), fundamentals cannot be extended before 2009, and the panel calendar starts 1993.

## 1. What was added

| Horizon | Assets extended | Extra (training) records |
|---|---|---|
| 1M | 129 | 23,884 |
| 3M | 129 | 25,877 |
| 6M | 129 | 30,196 |
| 12M | 128 | 43,149 |
| 1W | 129 | 23,523 |

**Data in the store.** Calendar from 1993-01-29 (8472 sessions). Assets by first price: INDEX 2001–2008 1, before 1995 8; ETF 1995–2000 19, 2001–2008 36, 2009 or later 20, before 1995 1; TREASURY before 1995 4; CORP_BOND before 1995 1; COMMODITY 1995–2000 10, 2001–2008 1; FX 1995–2000 1, 2001–2008 9, before 1995 1; CRYPTO 2009 or later 3; EQUITY 1995–2000 4, 2001–2008 5, 2009 or later 4, before 1995 32.

Standard research records begin (1M, by year): 2001 51, 2002 11, 2003 16, 2004 4, 2005 4, 2006 5, 2007 5, 2008 1, 2009 5, 2010 13, 2011 7, 2012 10, 2013 4, 2014 5, 2015 3, 2016 6, 2017 1, 2018 1, 2020 1, 2021 2.

Signal coverage (share of assets with a value) by family:

| Family | 1995 | 1998 | 2001 | 2005 | 2009 | 2015 | 2025 |
|---|---|---|---|---|---|---|---|
| Momentum | 18% | 31% | 41% | 57% | 78% | 93% | 99% |
| Trend | 29% | 32% | 42% | 58% | 80% | 93% | 99% |
| Mean Reversion | 29% | 32% | 41% | 60% | 80% | 92% | 99% |
| Valuation | 0% | 6% | 6% | 10% | 13% | 35% | 39% |
| Fundamental Quality | 0% | 0% | 0% | 0% | 0% | 21% | 23% |
| Fundamental Growth | 0% | 0% | 0% | 0% | 0% | 23% | 27% |
| Risk-Adjusted Performance | 22% | 31% | 41% | 56% | 75% | 92% | 99% |
| Volatility | 26% | 32% | 41% | 57% | 78% | 93% | 99% |
| Statistical / Time Series | 26% | 31% | 41% | 57% | 77% | 91% | 99% |
| Rates | 63% | 64% | 65% | 95% | 98% | 99% | 100% |
| Credit | 100% | 100% | 100% | 100% | 100% | 100% | 100% |
| Macro | 50% | 50% | 50% | 100% | 100% | 100% | 100% |
| Liquidity | 45% | 45% | 47% | 50% | 54% | 96% | 98% |
| Cross-Asset | 43% | 45% | 53% | 64% | 80% | 93% | 99% |
| Relative Value | 19% | 30% | 38% | 54% | 75% | 91% | 98% |

Fundamentals: first SEC filing in the store 2009-05-07 (45 companies). Families with < 10% coverage in 1998 (absent in extended records): Valuation, Fundamental Quality, Fundamental Growth.

## 2. Alpha: standard vs extended records

### 1M

Training starts 1994-08-24 (standard: 2001). Test weeks unchanged (921); independent periods ≈ 219; signals with |era t| ≥ 2: 7 → 8; STABLE signals 6 → 7.

| Model | Rank IC standard | Rank IC extended | Δ vs production extended (t) | Eras + | FDR | Status (extended) |
|---|---|---|---|---|---|---|
| learned global (D) | +0.0106 | +0.0170 | -0.0017 (-0.1) | 2/4 | — | baseline |
| stronger shrinkage (λ nested) | +0.0124 | +0.0182 | -0.0005 (-0.0) | 2/4 | no | NOT VALIDATED |
| family-level weights (15) | +0.0190 | +0.0187 | +0.0000 (+0.0) | 1/4 | no | NOT VALIDATED |
| top 10 signals (training correlation) | +0.0117 | +0.0123 | -0.0064 (-0.4) | 2/4 | no | NOT VALIDATED |
| top 20 signals (training correlation) | +0.0119 | +0.0188 | +0.0001 (+0.0) | 2/4 | no | NOT VALIDATED |
| stable signals only | +0.0317 | +0.0195 | -0.0030 (-0.1) | 1/2 | no | NOT VALIDATED |
| fundamental only | -0.0051 | +0.0007 | -0.0180 (-1.2) | 0/4 | no | NOT VALIDATED |
| relative value + fundamental | +0.0019 | +0.0068 | -0.0119 (-0.7) | 1/4 | no | NOT VALIDATED |
| macro + fundamental | +0.0002 | +0.0032 | -0.0155 (-1.1) | 0/4 | no | NOT VALIDATED |
| class-level hierarchy (K nested) | +0.0166 | +0.0135 | -0.0052 (-0.3) | 1/4 | no | NOT VALIDATED |
| nested selection (reduced dimension) | +0.0027 | +0.0058 | -0.0130 (-0.7) | 2/4 | no | NOT VALIDATED |

- Rank IC improved with more training history in 8 of 11 models.
- Validated with extended records: **none** — more history did not make this horizon learnable.

### 3M

Training starts 1994-08-24 (standard: 2001). Test weeks unchanged (912); independent periods ≈ 72; signals with |era t| ≥ 2: 8 → 5; STABLE signals 7 → 3.

| Model | Rank IC standard | Rank IC extended | Δ vs production extended (t) | Eras + | FDR | Status (extended) |
|---|---|---|---|---|---|---|
| learned global (D) | -0.0185 | -0.0031 | +0.0052 (+0.2) | 2/4 | — | baseline |
| stronger shrinkage (λ nested) | -0.0168 | -0.0031 | +0.0052 (+0.2) | 2/4 | no | NOT VALIDATED |
| family-level weights (15) | +0.0035 | +0.0094 | +0.0177 (+0.6) | 2/4 | no | NOT VALIDATED |
| top 10 signals (training correlation) | -0.0382 | -0.0071 | +0.0012 (+0.0) | 2/4 | no | NOT VALIDATED |
| top 20 signals (training correlation) | -0.0303 | -0.0010 | +0.0073 (+0.2) | 2/4 | no | NOT VALIDATED |
| stable signals only | -0.0045 | -0.0062 | -0.0183 (-0.5) | 1/2 | no | NOT VALIDATED |
| fundamental only | -0.0256 | -0.0109 | -0.0026 (-0.1) | 2/4 | no | NOT VALIDATED |
| relative value + fundamental | +0.0191 | +0.0042 | +0.0126 (+0.4) | 2/4 | no | NOT VALIDATED |
| macro + fundamental | -0.0190 | -0.0126 | -0.0042 (-0.2) | 1/4 | no | NOT VALIDATED |
| class-level hierarchy (K nested) | -0.0297 | -0.0246 | -0.0163 (-0.4) | 1/4 | no | NOT VALIDATED |
| nested selection (reduced dimension) | +0.0016 | -0.0158 | -0.0075 (-0.2) | 2/4 | no | NOT VALIDATED |

- Rank IC improved with more training history in 8 of 11 models.
- Validated with extended records: **none** — more history did not make this horizon learnable.

### 6M

Training starts 1994-08-24 (standard: 2001). Test weeks unchanged (899); independent periods ≈ 36; signals with |era t| ≥ 2: 7 → 7; STABLE signals 7 → 7.

| Model | Rank IC standard | Rank IC extended | Δ vs production extended (t) | Eras + | FDR | Status (extended) |
|---|---|---|---|---|---|---|
| learned global (D) | +0.0041 | +0.0252 | +0.0436 (+1.2) | 3/4 | — | baseline |
| stronger shrinkage (λ nested) | +0.0041 | +0.0252 | +0.0436 (+1.2) | 3/4 | no | NOT VALIDATED |
| family-level weights (15) | +0.0086 | +0.0181 | +0.0364 (+0.9) | 2/4 | no | NOT VALIDATED |
| top 10 signals (training correlation) | -0.0124 | +0.0132 | +0.0316 (+0.8) | 3/4 | no | NOT VALIDATED |
| top 20 signals (training correlation) | -0.0061 | +0.0232 | +0.0415 (+1.1) | 3/4 | no | NOT VALIDATED |
| stable signals only | -0.0407 | -0.0156 | -0.0396 (-0.8) | 1/2 | no | NOT VALIDATED |
| fundamental only | -0.0046 | +0.0024 | +0.0208 (+0.6) | 1/4 | no | NOT VALIDATED |
| relative value + fundamental | +0.0103 | +0.0069 | +0.0253 (+0.6) | 2/4 | no | NOT VALIDATED |
| macro + fundamental | +0.0142 | +0.0152 | +0.0336 (+0.8) | 2/4 | no | NOT VALIDATED |
| class-level hierarchy (K nested) | -0.0119 | -0.0003 | +0.0181 (+0.3) | 1/4 | no | NOT VALIDATED |
| nested selection (reduced dimension) | -0.0050 | -0.0211 | -0.0027 (-0.1) | 1/4 | no | NOT VALIDATED |

- Rank IC improved with more training history in 9 of 11 models.
- Validated with extended records: **none** — more history did not make this horizon learnable.

### 12M

Training starts 1994-08-24 (standard: 2001). Test weeks unchanged (868); independent periods ≈ 17; signals with |era t| ≥ 2: 7 → 9; STABLE signals 5 → 7.

| Model | Rank IC standard | Rank IC extended | Δ vs production extended (t) | Eras + | FDR | Status (extended) |
|---|---|---|---|---|---|---|
| learned global (D) | +0.0644 | +0.0803 | +0.0771 (+1.5) | 3/4 | — | baseline |
| stronger shrinkage (λ nested) | +0.0644 | +0.0803 | +0.0771 (+1.5) | 3/4 | no | NOT VALIDATED |
| family-level weights (15) | -0.0088 | +0.0192 | +0.0159 (+0.3) | 3/4 | no | NOT VALIDATED |
| top 10 signals (training correlation) | +0.0692 | +0.0945 | +0.0912 (+1.8) | 3/4 | no | NOT VALIDATED |
| top 20 signals (training correlation) | +0.0826 | +0.0824 | +0.0791 (+1.6) | 3/4 | no | NOT VALIDATED |
| stable signals only | +0.0112 | +0.0055 | -0.0247 (-0.3) | 1/2 | no | NOT VALIDATED |
| fundamental only | +0.0150 | +0.0323 | +0.0291 (+0.7) | 3/4 | no | NOT VALIDATED |
| relative value + fundamental | -0.0128 | +0.0368 | +0.0336 (+0.7) | 3/4 | no | NOT VALIDATED |
| macro + fundamental | +0.0086 | +0.0372 | +0.0340 (+0.8) | 3/4 | no | NOT VALIDATED |
| class-level hierarchy (K nested) | +0.0367 | +0.0778 | +0.0746 (+1.4) | 3/4 | no | NOT VALIDATED |
| nested selection (reduced dimension) | +0.0849 | +0.0767 | +0.0735 (+1.4) | 3/4 | no | NOT VALIDATED |

- Rank IC improved with more training history in 8 of 11 models.
- Validated with extended records: **none** — more history did not make this horizon learnable.

## 3. Directional: standard vs extended records

The learned Directional model (P(up) with the point-in-time base prior as offset) against the prior, with the Directional program's fixed gates.

| Horizon | Variant | Brier gain vs prior, standard (t) | extended (t) | Status extended |
|---|---|---|---|---|
| 1M | C validated deployable | -0.00081 (-1.8) | -0.00075 (-2.0) | NOT VALIDATED |
| 1M | B historical best fit | -0.00064 (-1.5) | -0.00063 (-2.0) | NOT VALIDATED |
| 1M | D simple global | -0.00062 (-1.7) | -0.00066 (-2.6) | NOT VALIDATED |
| 1M | E uniform-depth hierarchy | -0.00076 (-1.9) | -0.00086 (-3.2) | NOT VALIDATED |
| 3M | C validated deployable | -0.00343 (-2.8) | -0.00465 (-3.3) | NOT VALIDATED |
| 3M | B historical best fit | -0.00381 (-3.6) | -0.00499 (-3.8) | NOT VALIDATED |
| 3M | D simple global | -0.00348 (-3.0) | -0.00402 (-3.1) | NOT VALIDATED |
| 3M | E uniform-depth hierarchy | -0.00358 (-2.8) | -0.00407 (-3.2) | NOT VALIDATED |
| 6M | C validated deployable | -0.00677 (-2.7) | -0.00761 (-2.7) | NOT VALIDATED |
| 6M | B historical best fit | -0.00611 (-2.6) | -0.00759 (-2.6) | NOT VALIDATED |
| 6M | D simple global | -0.00644 (-2.3) | -0.00800 (-2.6) | NOT VALIDATED |
| 6M | E uniform-depth hierarchy | -0.00639 (-2.3) | -0.00785 (-2.5) | NOT VALIDATED |

## 4. What would actually move the long horizons

- **Independent periods, not records.** Overlapping 3M–12M windows give ~72 / 36 / 17 independent periods over the test eras for 74 signals; extended history adds training periods but not test periods, and cannot add pre-2009 fundamentals. The binding constraint is time.
- **A longer calendar.** The panel calendar starts 1993-01-29; many indices, Treasuries and 32 stocks have prices before that. Extending it changes production's reconstructed record (HISTORY_YEARS and the calendar are production inputs), so it is a separate decision, not done here.
- **Point-in-time fundamentals before 2009.** SEC XBRL starts in 2009; earlier first-reported fundamentals would need a licensed point-in-time source (not in the allowed data sources).
- **A wider cross-section.** More assets per date shrink the noise in each period's IC; the store holds 160 assets. Adding long-history assets (more single stocks with 1990s data, international indices, commodity futures) needs a data fetch on your machine.
- **Fewer parameters.** Family-level and stable-only models are the right shape for ~1 independent period per signal; they remain the candidates to watch.
