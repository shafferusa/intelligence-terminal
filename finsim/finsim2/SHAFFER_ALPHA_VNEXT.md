# Shaffer Alpha vNext — research

Run 2026-09-26 14:35:03 · 377.4 s · `python -m finsim2 lab --vnext alpha`. Research only; production (`shaffer-alpha-2.1-production`, benchmark `benchmark-2.1-2026-09-25`) is unchanged. Protocol, challengers and gates were committed before the full run (module docstring of `engine/alphanext.py`); the 1W smoke run was seen while testing the code and nothing was changed after it.

## Summary

- **What was tested:** four challengers per horizon (global, class, sector, fundamental) that ADD new point-in-time information to the production score — SEC fundamentals first reported (FCF yield, accruals, asset growth, leverage change, gross profitability, operating-margin change, net issuance, buybacks), earnings events and surprises, sector-relative valuation / growth / momentum, macro sensitivity (credit and term premium × beta) and breadth × beta — against the production score on identical records, walk-forward over four unseen eras, the 2018 split and 2025–.
- **Improved (every gate incl. FDR):** nothing.
- **Horizons verified useful** (rank IC > 0 surviving FDR, positive quintile spread, ≥ 3 of 4 eras, positive net-of-cost long-short): production at 1W; challengers at global 1W, class 1W, sector 1W, global 1M, class 1M.
- **Longer horizons (1M–12M):** newly useful: global 1M, class 1M.
- **Caveat on "useful":** the net-of-cost criterion is a point estimate > 0; for production 1W (net L/S t +0.1), global 1M (net L/S t +1.7) the net long-short is not statistically distinguishable from zero.
- **Useful is not better:** global 1M Δ rank IC vs production +0.0234 (t +1.1); class 1M Δ rank IC vs production +0.0197 (t +1.0). Production alone is not useful at these horizons, but the challengers' improvement over it does not pass G1 or FDR, so the extension past 1W is suggestive, not verified.
- **Blocked data (the bottleneck):** forward valuation / analyst estimates, analyst and earnings revisions, historical consensus, options-implied expectations, positioning (CFTC not permitted; short interest 1 year only), fund flows, dated futures curves.

## Main table (walk-forward, identical records)

| Horizon | Model | Rank IC (t) | Δ rank IC vs production (t) | Quintile spread | Decile spread | Net L/S per period (t) · annualised | Hit vs median | Monotonicity | Eras Δ > 0 | Split Δ t | FDR | Status |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1D | production | +0.0411 (+7.7) | — | +0.10% | +0.08% | -0.134% (-6.4) · -7.0% | +1.29% | +0.94 | — | — | — | production |
| 1D | global | +0.0379 (+4.8) | -0.0032 (-0.4) | +0.12% | -0.00% | -0.096% (-2.5) · -5.0% | +1.28% | +0.25 | 2/4 | +0.6 | ✗ | REJECT |
| 1D | class | +0.0335 (+4.6) | -0.0076 (-1.0) | +0.11% | +0.02% | -0.084% (-2.5) · -4.4% | +1.28% | +0.53 | 2/4 | +0.0 | ✗ | REJECT |
| 1D | sector | +0.0289 (+4.2) | -0.0122 (-1.7) | +0.09% | +0.01% | -0.109% (-3.4) · -5.7% | +0.90% | +0.75 | 0/4 | -0.1 | ✗ | REJECT |
| 1D | fundamental | +0.0289 (+6.9) | -0.0123 (-4.0) | +0.07% | +0.05% | -0.092% (-5.0) · -4.8% | +0.91% | +0.38 | 0/4 | -2.4 | ✗ | REJECT |
| 1W | production | +0.0376 (+6.1) | — | +0.22% | +0.26% | +0.007% (+0.1) · +0.4% | +0.74% | +0.82 | — | — | — | production |
| 1W | global | +0.0515 (+5.7) | +0.0139 (+1.5) | +0.38% | +0.44% | +0.206% (+2.1) · +10.4% | +2.55% | +0.98 | 3/4 | +0.9 | ✗ | REJECT |
| 1W | class | +0.0479 (+5.8) | +0.0102 (+1.1) | +0.43% | +0.49% | +0.293% (+3.2) · +14.8% | +2.43% | +0.89 | 3/4 | +0.9 | ✗ | REJECT |
| 1W | sector | +0.0425 (+5.3) | +0.0049 (+0.6) | +0.34% | +0.50% | +0.195% (+2.3) · +9.8% | +1.97% | +0.79 | 3/4 | +0.7 | ✗ | REJECT |
| 1W | fundamental | +0.0282 (+5.9) | -0.0094 (-2.8) | +0.15% | +0.17% | -0.004% (-0.1) · -0.2% | +0.67% | -0.15 | 1/4 | -1.8 | ✗ | REJECT |
| 1M | production | +0.0187 (+1.5) | — | +0.22% | +0.08% | -0.046% (-0.2) · -0.6% | +0.71% | +0.95 | — | — | — | production |
| 1M | global | +0.0421 (+2.2) | +0.0234 (+1.1) | +0.84% | +1.41% | +0.756% (+1.7) · +9.1% | +3.29% | +0.78 | 3/4 | +0.9 | ✗ | REJECT |
| 1M | class | +0.0383 (+2.2) | +0.0197 (+1.0) | +0.91% | +1.60% | +0.823% (+2.0) · +9.9% | +2.97% | +0.55 | 3/4 | +1.1 | ✗ | REJECT |
| 1M | sector | +0.0275 (+1.6) | +0.0088 (+0.4) | +0.71% | +1.55% | +0.631% (+1.7) · +7.6% | +1.73% | +0.55 | 2/4 | +1.1 | ✗ | REJECT |
| 1M | fundamental | -0.0009 (-0.1) | -0.0196 (-1.5) | +0.05% | -0.05% | -0.090% (-0.7) · -1.1% | +0.10% | +0.05 | 0/4 | -1.0 | ✗ | REJECT |
| 3M | production | -0.0084 (-0.4) | — | -0.32% | -1.55% | -0.560% (-0.8) · -2.2% | +0.23% | +0.84 | — | — | — | production |
| 3M | global | +0.0398 (+1.3) | +0.0482 (+1.2) | +1.80% | +2.76% | +1.884% (+1.2) · +7.5% | +3.85% | +0.87 | 4/4 | +1.4 | ✗ | REJECT |
| 3M | class | +0.0309 (+1.0) | +0.0393 (+1.0) | +2.02% | +3.19% | +2.846% (+2.0) · +11.4% | +2.41% | +0.62 | 3/4 | +1.6 | ✗ | REJECT |
| 3M | sector | +0.0181 (+0.6) | +0.0265 (+0.7) | +1.74% | +3.46% | +1.958% (+1.4) · +7.8% | +1.02% | +0.49 | 3/4 | +1.5 | ✗ | REJECT |
| 3M | fundamental | -0.0178 (-1.3) | -0.0094 (-0.3) | -0.20% | -0.23% | -0.242% (-0.6) · -1.0% | -0.91% | -0.31 | 1/4 | +0.2 | ✗ | REJECT |
| 6M | production | -0.0183 (-0.6) | — | -0.53% | -2.89% | -0.498% (-0.3) · -1.0% | +1.13% | +0.78 | — | — | — | production |
| 6M | global | +0.0308 (+0.8) | +0.0491 (+1.0) | +2.22% | +2.01% | +2.796% (+1.0) · +5.6% | +3.65% | +0.44 | 3/4 | +1.0 | ✗ | REJECT |
| 6M | class | +0.0253 (+0.7) | +0.0436 (+0.9) | +2.52% | +3.75% | +3.492% (+1.4) · +7.0% | +2.05% | +0.20 | 3/4 | +1.0 | ✗ | REJECT |
| 6M | sector | +0.0110 (+0.3) | +0.0293 (+0.5) | +2.28% | +3.81% | +3.203% (+1.3) · +6.4% | +0.37% | +0.22 | 2/4 | +0.8 | ✗ | REJECT |
| 6M | fundamental | -0.0252 (-1.3) | -0.0069 (-0.2) | -0.49% | -0.98% | -1.381% (-1.2) · -2.8% | -1.32% | -0.20 | 1/4 | +0.3 | ✗ | REJECT |
| 12M | production | +0.0033 (+0.1) | — | +1.00% | -3.98% | +1.380% (+0.4) · +1.4% | +1.63% | +0.47 | — | — | — | production |
| 12M | global | -0.0134 (-0.2) | -0.0167 (-0.2) | -3.41% | -5.73% | -0.182% (-0.0) · -0.2% | -2.26% | -0.76 | 2/4 | +0.5 | ✗ | REJECT |
| 12M | class | +0.0122 (+0.2) | +0.0089 (+0.1) | -1.30% | -3.29% | +4.295% (+0.9) · +4.3% | -1.28% | -0.64 | 3/4 | +0.4 | ✗ | REJECT |
| 12M | sector | +0.0177 (+0.3) | +0.0144 (+0.2) | -0.26% | -0.71% | +0.399% (+0.1) · +0.4% | -2.20% | -0.25 | 2/4 | +0.7 | ✗ | REJECT |
| 12M | fundamental | -0.0158 (-0.6) | -0.0191 (-0.3) | -0.38% | +0.17% | -1.886% (-0.9) · -1.9% | -1.35% | -0.27 | 1/4 | +0.0 | ✗ | REJECT |

Hit vs median is shown as the excess over 50%. Net L/S: equal-weight top minus bottom quintile per non-overlapping formation period of the horizon, net of 10 bp one-way × turnover. Monotonicity: Spearman of the ten score-decile mean outcomes.

## Horizon usefulness (every criterion at once)

| Horizon | Model | Rank IC | t | FDR | Quintile spread | Eras rank IC > 0 | Net L/S | Useful? |
|---|---|---|---|---|---|---|---|---|
| 1D | production | +0.0411 | +7.7 | ✓ | +0.10% | 4/4 | -0.134% | no |
| 1D | global | +0.0379 | +4.8 | ✓ | +0.12% | 4/4 | -0.096% | no |
| 1D | class | +0.0335 | +4.6 | ✓ | +0.11% | 4/4 | -0.084% | no |
| 1D | sector | +0.0289 | +4.2 | ✓ | +0.09% | 4/4 | -0.109% | no |
| 1D | fundamental | +0.0289 | +6.9 | ✓ | +0.07% | 4/4 | -0.092% | no |
| 1W | production | +0.0376 | +6.1 | ✓ | +0.22% | 4/4 | +0.007% | **YES** |
| 1W | global | +0.0515 | +5.7 | ✓ | +0.38% | 4/4 | +0.206% | **YES** |
| 1W | class | +0.0479 | +5.8 | ✓ | +0.43% | 4/4 | +0.293% | **YES** |
| 1W | sector | +0.0425 | +5.3 | ✓ | +0.34% | 4/4 | +0.195% | **YES** |
| 1W | fundamental | +0.0282 | +5.9 | ✓ | +0.15% | 4/4 | -0.004% | no |
| 1M | production | +0.0187 | +1.5 | ✗ | +0.22% | 3/4 | -0.046% | no |
| 1M | global | +0.0421 | +2.2 | ✓ | +0.84% | 4/4 | +0.756% | **YES** |
| 1M | class | +0.0383 | +2.2 | ✓ | +0.91% | 4/4 | +0.823% | **YES** |
| 1M | sector | +0.0275 | +1.6 | ✗ | +0.71% | 4/4 | +0.631% | no |
| 1M | fundamental | -0.0009 | -0.1 | ✗ | +0.05% | 1/4 | -0.090% | no |
| 3M | production | -0.0084 | -0.4 | ✗ | -0.32% | 3/4 | -0.560% | no |
| 3M | global | +0.0398 | +1.3 | ✗ | +1.80% | 3/4 | +1.884% | no |
| 3M | class | +0.0309 | +1.0 | ✗ | +2.02% | 3/4 | +2.846% | no |
| 3M | sector | +0.0181 | +0.6 | ✗ | +1.74% | 2/4 | +1.958% | no |
| 3M | fundamental | -0.0178 | -1.3 | ✗ | -0.20% | 1/4 | -0.242% | no |
| 6M | production | -0.0183 | -0.6 | ✗ | -0.53% | 2/4 | -0.498% | no |
| 6M | global | +0.0308 | +0.8 | ✗ | +2.22% | 3/4 | +2.796% | no |
| 6M | class | +0.0253 | +0.7 | ✗ | +2.52% | 3/4 | +3.492% | no |
| 6M | sector | +0.0110 | +0.3 | ✗ | +2.28% | 1/4 | +3.203% | no |
| 6M | fundamental | -0.0252 | -1.3 | ✗ | -0.49% | 1/4 | -1.381% | no |
| 12M | production | +0.0033 | +0.1 | ✗ | +1.00% | 2/4 | +1.380% | no |
| 12M | global | -0.0134 | -0.2 | ✗ | -3.41% | 1/4 | -0.182% | no |
| 12M | class | +0.0122 | +0.2 | ✗ | -1.30% | 3/4 | +4.295% | no |
| 12M | sector | +0.0177 | +0.3 | ✗ | -0.26% | 3/4 | +0.399% | no |
| 12M | fundamental | -0.0158 | -0.6 | ✗ | -0.38% | 1/4 | -1.886% | no |

## Era stability (Δ rank IC vs production, walk-forward eras)

| Horizon | Model | 2009–12 | 2013–16 | 2017–20 | 2021–24 | 2025– |
|---|---|---|---|---|---|---|
| 1D | global | -0.0260 | +0.0062 | -0.0122 | +0.0221 | -0.0025 |
| 1D | class | -0.0289 | +0.0073 | -0.0173 | +0.0107 | -0.0061 |
| 1D | sector | -0.0273 | -0.0018 | -0.0178 | -0.0027 | -0.0066 |
| 1D | fundamental | -0.0235 | -0.0047 | -0.0179 | -0.0020 | -0.0118 |
| 1W | global | -0.0044 | +0.0265 | +0.0123 | +0.0284 | -0.0009 |
| 1W | class | -0.0036 | +0.0183 | +0.0084 | +0.0242 | -0.0016 |
| 1W | sector | -0.0032 | +0.0104 | +0.0052 | +0.0098 | +0.0017 |
| 1W | fundamental | -0.0225 | -0.0084 | -0.0062 | +0.0017 | -0.0133 |
| 1M | global | +0.0336 | +0.0419 | -0.0306 | +0.0457 | +0.0290 |
| 1M | class | +0.0332 | +0.0199 | -0.0268 | +0.0428 | +0.0439 |
| 1M | sector | +0.0315 | -0.0024 | -0.0251 | +0.0155 | +0.0451 |
| 1M | fundamental | -0.0066 | -0.0382 | -0.0413 | -0.0012 | -0.0016 |
| 3M | global | +0.0413 | +0.0273 | +0.0775 | +0.0276 | +0.0961 |
| 3M | class | +0.0276 | -0.0004 | +0.0775 | +0.0342 | +0.0851 |
| 3M | sector | +0.0269 | -0.0332 | +0.0830 | +0.0053 | +0.0887 |
| 3M | fundamental | +0.0296 | -0.0204 | -0.0134 | -0.0288 | -0.0256 |
| 6M | global | +0.0742 | +0.0372 | +0.0622 | -0.0166 | +0.1751 |
| 6M | class | +0.0892 | +0.0209 | +0.0591 | -0.0066 | +0.0828 |
| 6M | sector | +0.1074 | -0.0015 | +0.0660 | -0.0749 | +0.0911 |
| 6M | fundamental | +0.0474 | -0.0009 | -0.0462 | -0.0374 | +0.0248 |
| 12M | global | +0.0385 | -0.0706 | -0.0366 | +0.0327 | -0.1627 |
| 12M | class | +0.0620 | -0.0415 | +0.0239 | +0.0342 | -0.2117 |
| 12M | sector | +0.0765 | -0.0303 | +0.0784 | -0.0375 | -0.1239 |
| 12M | fundamental | +0.0105 | -0.0172 | -0.0504 | -0.0265 | +0.0202 |

## Stability by asset class and sector (best challenger vs production, rank IC)

| Horizon | Best challenger | Group | Records | Challenger rank IC (t) | Production rank IC (t) |
|---|---|---|---|---|---|
| 1D | global | ETF | 52,724 | +0.0487 (+5.2) | +0.0524 (+7.4) |
| 1D | global | EQUITY | 37,544 | +0.0481 (+6.2) | +0.0376 (+5.6) |
| 1D | global | COMMODITY | 9,393 | +0.0170 (+1.3) | +0.0317 (+2.7) |
| 1D | global | FX | 9,167 | +0.0080 (+0.6) | +0.0306 (+2.4) |
| 1D | global | INDEX | 7,662 | +0.0451 (+2.8) | +0.0448 (+3.4) |
| 1D | global | Information Technology | 11,228 | +0.0399 (+3.5) | +0.0544 (+5.3) |
| 1W | global | ETF | 52,527 | +0.0703 (+6.1) | +0.0498 (+5.8) |
| 1W | global | EQUITY | 37,909 | +0.0514 (+6.6) | +0.0349 (+5.5) |
| 1W | global | COMMODITY | 9,478 | +0.0045 (+0.4) | -0.0045 (-0.4) |
| 1W | global | FX | 9,125 | +0.0201 (+1.4) | +0.0235 (+1.8) |
| 1W | global | INDEX | 7,805 | +0.0507 (+3.3) | +0.0784 (+6.1) |
| 1W | global | Information Technology | 11,284 | +0.0650 (+6.0) | +0.0519 (+5.1) |
| 1M | global | ETF | 53,100 | +0.0647 (+2.5) | +0.0142 (+0.8) |
| 1M | global | EQUITY | 37,562 | +0.0110 (+0.7) | +0.0209 (+1.6) |
| 1M | global | COMMODITY | 9,402 | -0.0110 (-0.4) | -0.0003 (-0.0) |
| 1M | global | FX | 9,079 | -0.0197 (-0.7) | +0.0235 (+0.8) |
| 1M | global | INDEX | 7,761 | -0.0116 (-0.4) | +0.0092 (+0.3) |
| 1M | global | Information Technology | 11,327 | +0.0417 (+2.0) | +0.0508 (+2.4) |
| 3M | global | ETF | 50,558 | +0.0680 (+1.6) | -0.0050 (-0.2) |
| 3M | global | EQUITY | 37,434 | -0.0040 (-0.2) | +0.0091 (+0.5) |
| 3M | global | COMMODITY | 9,375 | +0.0064 (+0.1) | -0.0750 (-1.8) |
| 3M | global | FX | 8,789 | -0.0160 (-0.3) | -0.0086 (-0.1) |
| 3M | global | INDEX | 7,672 | -0.0028 (-0.1) | -0.0578 (-1.2) |
| 3M | global | Information Technology | 11,161 | +0.0219 (+0.6) | +0.0194 (+0.6) |
| 6M | global | ETF | 45,440 | +0.0565 (+1.0) | +0.0194 (+0.4) |
| 6M | global | EQUITY | 36,000 | -0.0165 (-0.5) | -0.0292 (-1.0) |
| 6M | global | COMMODITY | 9,077 | +0.0314 (+0.5) | -0.0465 (-0.7) |
| 6M | global | FX | 8,423 | -0.0208 (-0.3) | -0.0034 (-0.0) |
| 6M | global | INDEX | 7,529 | -0.0739 (-1.1) | -0.0350 (-0.5) |
| 6M | global | Information Technology | 10,709 | -0.0069 (-0.1) | +0.0173 (+0.3) |
| 12M | sector | EQUITY | 32,200 | +0.0007 (+0.0) | +0.0139 (+0.3) |
| 12M | sector | ETF | 28,813 | +0.0679 (+0.7) | +0.0719 (+1.0) |
| 12M | sector | INDEX | 7,027 | +0.0078 (+0.1) | -0.0184 (-0.2) |
| 12M | sector | COMMODITY | 6,597 | +0.1538 (+1.4) | -0.0967 (-0.8) |
| 12M | sector | FX | 5,777 | -0.0467 (-0.3) | -0.0525 (-0.4) |
| 12M | sector | Information Technology | 9,731 | -0.0630 (-0.9) | +0.0680 (+0.9) |

## Specialisation (global → class → sector)

| Horizon | Global Δ rank IC (t) | Class Δ (t) | Sector Δ (t) | Deeper point estimate above its parent? | Specialisation justified? |
|---|---|---|---|---|---|
| 1D | -0.0032 (-0.4) | -0.0076 (-1.0) | -0.0122 (-1.7) | no | no |
| 1W | +0.0139 (+1.5) | +0.0102 (+1.1) | +0.0049 (+0.6) | no | no |
| 1M | +0.0234 (+1.1) | +0.0197 (+1.0) | +0.0088 (+0.4) | no | no |
| 3M | +0.0482 (+1.2) | +0.0393 (+1.0) | +0.0265 (+0.7) | no | no |
| 6M | +0.0491 (+1.0) | +0.0436 (+0.9) | +0.0293 (+0.5) | no | no |
| 12M | -0.0167 (-0.2) | +0.0089 (+0.1) | +0.0144 (+0.2) | class > global, sector > class | no |

A deeper model is justified only if it beats its parent out of sample AND passes the gates itself; a higher point estimate with t well below 2 is not evidence.

## What the models used, by horizon (global challenger, final fit)

Weights on standardised inputs (asset-level features are cross-sectional percentile ranks −0.5…0.5; market-wide ones are PIT z × beta), largest magnitude first. A weight is not evidence by itself; the gates above are.

- **1D:** production +0.110, net_new_highs -0.033, d_term_premium_3m -0.026, d_quality_spread_3m -0.024, quality_spread_z -0.023, fcf_yield +0.021, pead_volume +0.020, buyback_yield +0.019
- **1W:** production +0.089, net_new_highs -0.065, quality_spread_z -0.037, d_real5_3m -0.037, fcf_yield +0.031, breadth_thrust_10d +0.030, accruals -0.028, term_premium -0.025
- **1M:** net_new_highs -0.073, pct_above_50d -0.070, d_real5_3m -0.069, quality_spread_z -0.056, breadth_thrust_10d +0.055, fcf_yield +0.055, accruals -0.052, pead_volume +0.047
- **3M:** accruals -0.102, net_new_highs -0.101, quality_spread_z -0.083, fcf_yield +0.076, pct_above_50d -0.076, d_quality_spread_3m -0.069, d_real5_3m -0.067, rel_rev_growth +0.065
- **6M:** accruals -0.140, roe_change +0.110, d_quality_spread_3m -0.106, fcf_yield +0.090, term_premium -0.085, quality_spread_z -0.081, d_term_premium_3m -0.081, rel_rev_growth +0.072
- **12M:** d_quality_spread_3m -0.179, accruals -0.161, rel_book_to_price -0.137, roe_change +0.128, fcf_yield +0.118, term_premium -0.100, rel_roe -0.094, sue_rev +0.092

## Conviction: does a larger |Alpha| mean a larger relative return?

| Horizon | Model | |score| bucket | Records | n_eff | Signed relative return | Hit | Rank IC | Volatility | Drawdown | Monotonic? |
|---|---|---|---|---|---|---|---|---|---|---|
| 1D | production | 0–10 | 101,007 | 926 | +0.019% | +0.67% | +0.023 | +0.21% | -2.8% | no |
| 1D | production | 10–25 | 14,197 | 926 | +0.018% | +1.43% | +0.028 | +0.48% | -5.3% |  |
| 1D | production | 25–50 | 5,083 | 909 | +0.069% | +1.29% | +0.064 | +0.67% | -10.0% |  |
| 1D | production | 50–75 | 954 | 517 | +0.080% | +2.41% | +0.180 | +1.19% | -14.6% |  |
| 1D | production | 75– | 145 | 119 | +0.567% | +10.00% | +0.353 | +1.85% | -5.5% |  |
| 1D | global | 0–10 | 115,352 | 926 | +0.008% | +0.01% | +0.009 | +0.26% | -8.5% | no |
| 1D | global | 10–25 | 5,684 | 586 | +0.059% | +1.32% | +0.032 | +2.00% | -19.8% |  |
| 1D | global | 25–50 | 335 | 118 | +0.575% | +5.22% | +0.141 | +3.34% | -23.6% |  |
| 1D | global | 50–75 | 15 | — | — | — | — | — | — |  |
| 1D | global | 75– | 0 | — | — | — | — | — | — |  |
| 1W | production | 0–10 | 105,009 | 925 | +0.031% | +0.17% | +0.017 | +0.44% | -5.3% | **yes** |
| 1W | production | 10–25 | 13,374 | 919 | +0.110% | +0.85% | +0.044 | +1.15% | -21.9% |  |
| 1W | production | 25–50 | 2,834 | 813 | +0.254% | +1.83% | +0.093 | +2.25% | -25.8% |  |
| 1W | production | 50–75 | 448 | 344 | +0.305% | +0.00% | +0.217 | +4.26% | -53.2% |  |
| 1W | production | 75– | 86 | 78 | +0.541% | -2.33% | +0.098 | +2.63% | -8.8% |  |
| 1W | global | 0–10 | 108,301 | 925 | +0.059% | +1.19% | +0.035 | +0.59% | -8.7% | no |
| 1W | global | 10–25 | 12,161 | 715 | +0.286% | +4.50% | +0.092 | +3.52% | -57.2% |  |
| 1W | global | 25–50 | 1,200 | 314 | +1.010% | +6.00% | +0.103 | +7.16% | -53.6% |  |
| 1W | global | 50–75 | 82 | 40 | +3.097% | +8.54% | +0.140 | +12.01% | -27.7% |  |
| 1W | global | 75– | 7 | — | — | — | — | — | — |  |
| 1M | production | 0–10 | 112,637 | 220 | +0.097% | +0.31% | +0.010 | +0.88% | -7.1% | no |
| 1M | production | 10–25 | 7,027 | 219 | +0.197% | +0.15% | +0.047 | +3.37% | -43.5% |  |
| 1M | production | 25–50 | 1,448 | 168 | +0.011% | +1.10% | +0.070 | +5.25% | -95.9% |  |
| 1M | production | 50–75 | 416 | 75 | +0.165% | -2.64% | -0.027 | +4.63% | -28.3% |  |
| 1M | production | 75– | 151 | 32 | -0.128% | -6.95% | -0.088 | +2.47% | -13.1% |  |
| 1M | global | 0–10 | 111,472 | 220 | +0.157% | +2.12% | +0.043 | +1.34% | -14.6% | no |
| 1M | global | 10–25 | 9,242 | 150 | +0.857% | +6.80% | +0.124 | +8.52% | -72.7% |  |
| 1M | global | 25–50 | 882 | 59 | +4.723% | +13.61% | +0.229 | +15.22% | -79.4% |  |
| 1M | global | 50–75 | 80 | 9 | +13.805% | +22.50% | +0.416 | +23.75% | -48.6% |  |
| 1M | global | 75– | 3 | — | — | — | — | — | — |  |
| 3M | production | 0–10 | 109,201 | 72 | +0.102% | +0.30% | -0.011 | +1.62% | -16.4% | no |
| 3M | production | 10–25 | 8,221 | 72 | -0.315% | -2.18% | -0.045 | +5.24% | -52.8% |  |
| 3M | production | 25–50 | 948 | 41 | -0.927% | -7.28% | +0.015 | +5.20% | -44.3% |  |
| 3M | production | 50–75 | 62 | 5 | -2.137% | -9.68% | +0.247 | +4.75% | -11.6% |  |
| 3M | production | 75– | 4 | — | — | — | — | — | — |  |
| 3M | global | 0–10 | 107,392 | 72 | +0.436% | +2.77% | +0.044 | +2.07% | -6.8% | no |
| 3M | global | 10–25 | 10,014 | 53 | +2.023% | +8.50% | +0.096 | +12.97% | -44.9% |  |
| 3M | global | 25–50 | 927 | 19 | +9.812% | +20.12% | +0.275 | +23.99% | -76.0% |  |
| 3M | global | 50–75 | 99 | 3 | +34.843% | +34.85% | +0.577 | +30.21% | +0.0% |  |
| 3M | global | 75– | 4 | — | — | — | — | — | — |  |
| 6M | production | 0–10 | 97,780 | 36 | +0.137% | +0.57% | -0.017 | +2.19% | -5.7% | no |
| 6M | production | 10–25 | 11,544 | 35 | -0.201% | +1.76% | -0.102 | +6.81% | -17.6% |  |
| 6M | production | 25–50 | 1,528 | 23 | -2.332% | -8.97% | -0.267 | +9.39% | -73.1% |  |
| 6M | production | 50–75 | 41 | — | — | — | — | — | — |  |
| 6M | production | 75– | 0 | — | — | — | — | — | — |  |
| 6M | global | 0–10 | 97,941 | 36 | +0.472% | +2.74% | +0.021 | +2.72% | -3.8% | no |
| 6M | global | 10–25 | 11,820 | 28 | +2.486% | +7.23% | -0.046 | +14.17% | -57.2% |  |
| 6M | global | 25–50 | 1,042 | 10 | +13.942% | +19.00% | +0.149 | +36.62% | -60.1% |  |
| 6M | global | 50–75 | 82 | 1 | +52.967% | +40.24% | +0.381 | +31.97% | +0.0% |  |
| 6M | global | 75– | 8 | — | — | — | — | — | — |  |
| 12M | production | 0–10 | 74,894 | 17 | +0.135% | +1.00% | -0.029 | +3.08% | -12.3% | no |
| 12M | production | 10–25 | 8,962 | 17 | +0.485% | +0.47% | -0.094 | +10.69% | -19.3% |  |
| 12M | production | 25–50 | 725 | 7 | +0.247% | +1.31% | -0.307 | +12.11% | -39.7% |  |
| 12M | production | 50–75 | 30 | — | — | — | — | — | — |  |
| 12M | production | 75– | 0 | — | — | — | — | — | — |  |
| 12M | sector | 0–10 | 81,323 | 17 | -0.880% | -4.06% | -0.038 | +3.97% | -30.4% | no |
| 12M | sector | 10–25 | 3,202 | 8 | +3.070% | +3.15% | +0.006 | +36.69% | -56.5% |  |
| 12M | sector | 25–50 | 86 | 1 | +23.182% | +4.65% | +0.178 | +51.63% | -62.4% |  |
| 12M | sector | 50–75 | 0 | — | — | — | — | — | — |  |
| 12M | sector | 75– | 0 | — | — | — | — | — | — |  |

Hit is the excess over 50%. Volatility is that of the bucket's weekly mean; drawdown is the log drawdown of its equity curve over non-overlapping periods of the horizon. If the bucket means do not rise monotonically, Alpha magnitude must not size positions (answer below).

## Answers

- **Live-shadow eligibility:** none — no challenger passes G1–G4 and the FDR control.
- **Production eligibility:** none (live shadow — 60 graded paired outcomes — and your approval come first).
- **Conviction sizing:** production |Alpha| is monotonic in relative return at 1W; elsewhere magnitude must not be used for sizing.
- **Conviction, descriptive only:** among the buckets with enough records, signed relative return rises with |score| for 21 of 24 challenger × horizon fits (global 1D, class 1D, fundamental 1D, global 1W, sector 1W, fundamental 1W, global 1M, class 1M, sector 1M, fundamental 1M, global 3M, class 3M, sector 3M, global 6M, class 6M, sector 6M, fundamental 6M, global 12M, class 12M, sector 12M, fundamental 12M). This is not evidence for conviction sizing: it holds even for rejected models, the bucket volatility rises just as fast (higher-volatility names land in the high-|score| buckets, so a larger signed move is partly a larger move), the top buckets are thin (see n_eff), and the fixed monotonicity rule (every bucket populated and rising) is not met.
- **Next data bottleneck:** point-in-time analyst estimates and revisions (forward valuation, earnings revisions), then options-implied expectations, positioning / short interest history and fund flows. Every longer-horizon Alpha source that is plausibly informative is on that list; the fundamentals available from filings were tested here.
