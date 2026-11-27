# Does better volatility forecasting improve Shaffer Hedge outcomes? — research

Run 2026-09-25 23:18:59 · 6940.9 s · attribution 2026-09-26 01:26:35 · `python -m finsim2 lab --breadth-hedge`. Research only: hedge-2, its sizing, the Shaffer Score and the frozen benchmark `benchmark-2.1-2026-09-25` (sha256 `ce0f502bbaba5d5a34b7828df1fa286f4855baeb8767d4610a01a188818cdcdc`) are unchanged; nothing is promoted.

## Executive summary

**Breadth improves volatility forecasting but does not materially improve realized Shaffer Hedge outcomes.**

- The breadth-enhanced forecast of the market factor's volatility is better out of sample: squared log error 1W -15.2% against the same model without breadth (t +6.9), -50.1% against production; 1M -8.4% against the same model without breadth (t +3.2), -38.8% against production; 3M -1.8% against the same model without breadth (t +0.7), -8.6% against production. G1 passes at 1W, 1M and fails at 3M.
- The better forecast does not make the Shaffer Hedge measurably better: of 50 objective × horizon tests of realised utility (λ = 1), 0 survive the Benjamini-Hochberg control; no cell passes G1–G4, so nothing is eligible for live shadow and hedge-2 stays unchanged.
- In the 23 cells whose utility point estimate is positive (ES / VaR / drawdown counted once), the same regression without breadth accounts for 91% of the summed gain and breadth itself for 9%. Adding breadth to that regression lowered utility in 6 of the 9 variance-sensitive cells.
- Hedge sizes barely move: the median breadth ÷ production size (same risk unit) is between 1.00 and 1.00 across cells; the decisions that change are mostly product and leg choices, and their realised effects are not significant.

Arms, as always kept apart: **production** = hedge-2 (252-day covariance) · **no-breadth vol** = the new log-volatility regression (63d / 21d realised volatility, VIX) without breadth · **breadth vol** = the same regression with the five breadth features (`hedge-2-breadth-vol-exp`). ΔU(breadth − production) is the pre-registered test; ΔU(breadth − no-breadth) isolates breadth itself. Utility U = risk reduction − λ·profit sacrificed − hedge cost, $ per $1M book over the horizon (ES95 for crash / ES / VaR / drawdown, standard deviation otherwise); paired on identical cases; moving-block bootstrap over dates (every book on a date is one cluster); t = ΔU ÷ bootstrap SE. Protocol and gates were committed before any full result; the only change during the run was runtime (checkpoints, resume, saved cases — the first launch was stopped 3 minutes into the replay, before any date finished).

## Gates (as committed)

| Horizon | G1 forecast validity | G2 realised utility (FDR) | G3 era stability | G4 no degradation | G5 live shadow |
|---|---|---|---|---|---|
| 1W | PASS | 0 of 17 PASS | 4 of 17 PASS | 12 of 17 PASS | NOT YET TESTABLE |
| 1M | PASS | 0 of 17 PASS | 1 of 17 PASS | 15 of 17 PASS | NOT YET TESTABLE |
| 3M | FAIL | 0 of 17 PASS | 3 of 17 PASS | 12 of 17 PASS | NOT YET TESTABLE |

G5 cannot pass from history: a cell passing G1–G4 would be LIVE SHADOW ELIGIBLE, never production-eligible. No cell reached that point.

## The main result table (λ = 1; $ per $1M book over the horizon)

| Objective | Horizon | λ | Production U | No-breadth U | Breadth U | ΔU breadth−production | ΔU breadth−no-breadth | Variance Δ | ES Δ | Drawdown Δ | Cost Δ | Profit sacrificed Δ | Size ratio | Product changed % | Positive eras | clustered t | FDR | Gate status |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| min_variance | 1W | 1 | +10,155 | +10,144 | +9,984 | -171 | -160 | -0.88% | -773 | -212 | -16 | -112 | 1.000 | 20% | 1/4 | -1.29 | ✗ (raw p 0.910) | IMPROVES FORECAST ONLY |
| target_vol | 1W | 1 | +6,595 | +6,962 | +6,745 | +149 | -217 | +1.58% | +849 | -56 | -13 | -29 | 1.000 | 27% | 3/4 | +0.58 | ✗ (raw p 0.327) | IMPROVES FORECAST ONLY |
| beta | 1W | 1 | +6,287 | +5,823 | +5,735 | -552 | -88 | -1.67% | -1,622 | -253 | +11 | +23 | 1.000 | 26% | 0/4 | -1.80 | ✗ (raw p 0.998) | IMPROVES FORECAST ONLY |
| systematic | 1W | 1 | +9,429 | +9,262 | +9,253 | -176 | -9 | -0.43% | -351 | -105 | +10 | -16 | 1.000 | 18% | 0/4 | -2.49 | ✗ (raw p 1.000) | IMPROVES FORECAST ONLY |
| sector | 1W | 1 | +7,464 | +7,468 | +7,458 | -6 | -10 | +0.08% | +6 | -19 | -1 | +29 | 1.000 | 7% | 1/4 | -0.39 | ✗ (raw p 0.683) | IMPROVES FORECAST ONLY |
| name | 1W | 1 | +9,490 | +9,510 | +9,512 | +21 | +2 | +0.14% | +30 | +6 | +3 | +4 | 1.000 | 3% | 1/4 | +1.00 | ✗ (raw p 0.180) | IMPROVES FORECAST ONLY |
| crash | 1W | 1 | +11,284 | +11,485 | +11,637 | +353 | +152 | -1.65% | +181 | -45 | -33 | -139 | 1.000 | 12% | 2/4 | +0.71 | ✗ (raw p 0.239) | IMPROVES FORECAST ONLY |
| es | 1W | 1 | +13,586 | +13,688 | +13,398 | -188 | -290 | -0.39% | -221 | -152 | -7 | -26 | 1.000 | 25% | 1/4 | -0.52 | ✗ (raw p 0.718) | IMPROVES FORECAST ONLY |
| var | 1W | 1 | +13,586 | +13,688 | +13,398 | -188 | -290 | -0.39% | -221 | -152 | -7 | -26 | 1.000 | 25% | 1/4 | -0.52 | ✗ (raw p 0.718) | IMPROVES FORECAST ONLY |
| drawdown | 1W | 1 | +13,586 | +13,688 | +13,398 | -188 | -290 | -0.39% | -221 | -152 | -7 | -26 | 1.000 | 25% | 1/4 | -0.52 | ✗ (raw p 0.718) | IMPROVES FORECAST ONLY |
| duration | 1W | 1 | +379 | +394 | +395 | +16 | +1 | -0.67% | -0 | -4 | +0 | +5 | 1.000 | 1% | 3/4 | +1.79 | ✗ (raw p 0.177) | IMPROVES FORECAST ONLY |
| curve | 1W | 1 | +328 | +343 | +345 | +17 | +2 | -0.67% | -0 | -4 | +0 | +4 | 1.000 | 1% | 4/4 | +1.88 | ✗ (raw p 0.040) | IMPROVES FORECAST ONLY |
| credit | 1W | 1 | +762 | +796 | +796 | +34 | +0 | -0.25% | +6 | +1 | +0 | +19 | 1.000 | 3% | 4/4 | +1.91 | ✗ (raw p 0.030) | IMPROVES FORECAST ONLY |
| fx | 1W | 1 | +223 | +245 | +252 | +29 | +6 | -0.37% | +0 | -18 | +0 | +12 | 1.000 | 2% | 2/4 | +1.57 | ✗ (raw p 0.222) | IMPROVES FORECAST ONLY |
| commodity | 1W | 1 | +455 | +451 | +452 | -4 | +1 | -0.02% | +2 | -7 | -2 | +4 | 1.000 | 4% | 2/4 | -0.40 | ✗ (raw p 0.653) | IMPROVES FORECAST ONLY |
| crypto | 1W | 1 | +37,830 | +37,849 | +37,830 | +0 | -20 | +0.00% | -0 | -0 | -0 | +0 | 1.000 | 0% | 0/1 | +0.27 | ✗ (raw p 0.384) | IMPROVES FORECAST ONLY |
| volatility | 1W | 1 | +1,760 | +1,925 | +1,911 | +151 | -14 | +0.70% | +386 | +16 | +3 | +48 | 1.000 | 11% | 2/2 | +0.90 | ✗ (raw p 0.212) | IMPROVES FORECAST ONLY |
| min_variance | 1M | 1 | +22,823 | +23,322 | +23,176 | +353 | -146 | +0.11% | -4 | -10 | -16 | -83 | 1.000 | 14% | 2/4 | +1.00 | ✗ (raw p 0.095) | IMPROVES FORECAST ONLY |
| target_vol | 1M | 1 | +17,899 | +18,422 | +18,295 | +396 | -127 | +1.37% | +161 | +100 | -40 | -51 | 1.000 | 21% | 3/4 | +1.03 | ✗ (raw p 0.160) | IMPROVES FORECAST ONLY |
| beta | 1M | 1 | +10,571 | +10,248 | +10,112 | -459 | -136 | -0.51% | -1,417 | -190 | +135 | -280 | 1.000 | 24% | 0/4 | -1.05 | ✗ (raw p 0.915) | IMPROVES FORECAST ONLY |
| systematic | 1M | 1 | +19,969 | +19,779 | +19,803 | -166 | +24 | -0.37% | -169 | -128 | +40 | -135 | 1.000 | 16% | 1/4 | -0.97 | ✗ (raw p 0.843) | IMPROVES FORECAST ONLY |
| sector | 1M | 1 | +18,366 | +18,325 | +18,310 | -56 | -16 | -0.05% | -37 | -13 | -5 | -18 | 1.000 | 6% | 2/4 | -0.74 | ✗ (raw p 0.766) | IMPROVES FORECAST ONLY |
| name | 1M | 1 | +19,061 | +19,050 | +19,063 | +2 | +13 | +0.04% | +9 | +3 | +6 | -3 | 1.000 | 2% | 2/4 | +0.16 | ✗ (raw p 0.409) | IMPROVES FORECAST ONLY |
| crash | 1M | 1 | +10,119 | +10,103 | +10,599 | +480 | +495 | -0.31% | +57 | +50 | -165 | -258 | 1.000 | 12% | 2/4 | +1.23 | ✗ (raw p 0.095) | IMPROVES FORECAST ONLY |
| es | 1M | 1 | +32,324 | +32,517 | +32,590 | +266 | +73 | +0.23% | +258 | +29 | -16 | +8 | 1.000 | 17% | 2/4 | +0.45 | ✗ (raw p 0.264) | IMPROVES FORECAST ONLY |
| var | 1M | 1 | +32,324 | +32,517 | +32,590 | +266 | +73 | +0.23% | +258 | +29 | -16 | +8 | 1.000 | 17% | 2/4 | +0.45 | ✗ (raw p 0.264) | IMPROVES FORECAST ONLY |
| drawdown | 1M | 1 | +32,324 | +32,517 | +32,590 | +266 | +73 | +0.23% | +258 | +29 | -16 | +8 | 1.000 | 17% | 2/4 | +0.45 | ✗ (raw p 0.264) | IMPROVES FORECAST ONLY |
| duration | 1M | 1 | +1,217 | +1,186 | +1,184 | -33 | -2 | -0.01% | -43 | +5 | +0 | -9 | 1.000 | 2% | 1/4 | -1.22 | ✗ (raw p 0.905) | IMPROVES FORECAST ONLY |
| curve | 1M | 1 | +1,226 | +1,197 | +1,194 | -32 | -3 | -0.02% | -42 | +6 | +0 | -9 | 1.000 | 3% | 1/4 | -1.20 | ✗ (raw p 0.895) | IMPROVES FORECAST ONLY |
| credit | 1M | 1 | +1,192 | +1,192 | +1,192 | +0 | +1 | +0.12% | +6 | +10 | -1 | -1 | 1.000 | 3% | 2/4 | +0.02 | ✗ (raw p 0.484) | IMPROVES FORECAST ONLY |
| fx | 1M | 1 | +844 | +850 | +846 | +2 | -4 | +0.15% | -22 | +5 | +0 | +0 | 1.000 | 3% | 2/4 | +0.28 | ✗ (raw p 0.494) | IMPROVES FORECAST ONLY |
| commodity | 1M | 1 | +733 | +714 | +734 | +1 | +19 | -0.05% | -3 | -14 | -8 | -15 | 1.000 | 5% | 2/4 | +0.03 | ✗ (raw p 0.474) | IMPROVES FORECAST ONLY |
| crypto | 1M | 1 | +97,338 | +97,338 | +97,338 | -0 | -0 | -0.00% | +0 | -0 | -0 | -0 | 1.000 | 0% | 0/1 | -0.25 | ✗ (raw p 0.561) | INSUFFICIENT DATA |
| volatility | 1M | 1 | +1,003 | +908 | +1,096 | +93 | +188 | +0.48% | +780 | +33 | -6 | -52 | 1.000 | 16% | 1/2 | +0.47 | ✗ (raw p 0.374) | IMPROVES FORECAST ONLY |
| min_variance | 3M | 1 | +22,730 | +22,561 | +22,581 | -150 | +20 | -0.05% | -1,052 | -321 | -27 | +519 | 1.000 | 13% | 1/4 | -0.43 | ✗ (raw p 0.643) | NO HEDGE IMPROVEMENT |
| target_vol | 3M | 1 | +18,107 | +17,510 | +17,338 | -769 | -172 | +0.15% | -5,420 | -152 | -41 | +880 | 1.000 | 20% | 1/4 | -0.83 | ✗ (raw p 0.830) | NO HEDGE IMPROVEMENT |
| beta | 3M | 1 | +9,403 | +7,957 | +7,801 | -1,602 | -156 | -0.35% | -4,590 | -554 | +400 | +279 | 1.000 | 19% | 0/4 | -1.78 | ✗ (raw p 0.998) | NO HEDGE IMPROVEMENT |
| systematic | 3M | 1 | +24,603 | +24,260 | +24,148 | -455 | -113 | -0.39% | -279 | -440 | +95 | -49 | 1.000 | 16% | 1/4 | -1.74 | ✗ (raw p 0.968) | NO HEDGE IMPROVEMENT |
| sector | 3M | 1 | +17,463 | +17,623 | +17,598 | +135 | -25 | +0.10% | -393 | +152 | +1 | -36 | 1.000 | 9% | 3/4 | +0.64 | ✗ (raw p 0.294) | NO HEDGE IMPROVEMENT |
| name | 3M | 1 | +25,194 | +25,274 | +25,283 | +88 | +9 | +0.05% | +13 | +45 | +25 | +25 | 1.000 | 3% | 2/4 | +1.28 | ✗ (raw p 0.107) | NO HEDGE IMPROVEMENT |
| crash | 3M | 1 | -1,083 | +6,841 | +7,333 | +8,416 | +492 | -1.67% | +4,946 | +10 | -1,232 | -2,237 | 1.000 | 19% | 3/4 | +2.41 | ✗ (raw p 0.002) | NO HEDGE IMPROVEMENT |
| es | 3M | 1 | +39,113 | +37,156 | +37,336 | -1,777 | +180 | +0.01% | -1,335 | -130 | -6 | +448 | 1.000 | 16% | 0/4 | -2.75 | ✗ (raw p 1.000) | NO HEDGE IMPROVEMENT |
| var | 3M | 1 | +39,113 | +37,156 | +37,336 | -1,777 | +180 | +0.01% | -1,335 | -130 | -6 | +448 | 1.000 | 16% | 0/4 | -2.75 | ✗ (raw p 1.000) | NO HEDGE IMPROVEMENT |
| drawdown | 3M | 1 | +39,113 | +37,156 | +37,336 | -1,777 | +180 | +0.01% | -1,335 | -130 | -6 | +448 | 1.000 | 16% | 0/4 | -2.75 | ✗ (raw p 1.000) | NO HEDGE IMPROVEMENT |
| duration | 3M | 1 | +2,317 | +2,282 | +2,246 | -72 | -36 | +0.24% | +0 | +19 | +2 | +22 | 1.000 | 4% | 1/4 | -1.63 | ✗ (raw p 0.990) | NO HEDGE IMPROVEMENT |
| curve | 3M | 1 | +2,495 | +2,404 | +2,373 | -122 | -32 | +0.22% | -80 | +1 | +2 | +40 | 1.000 | 9% | 1/4 | -2.17 | ✗ (raw p 1.000) | NO HEDGE IMPROVEMENT |
| credit | 3M | 1 | +2,725 | +2,760 | +2,760 | +35 | +1 | +0.07% | +389 | +57 | +7 | -62 | 1.000 | 5% | 4/4 | +0.44 | ✗ (raw p 0.279) | NO HEDGE IMPROVEMENT |
| fx | 3M | 1 | +1,365 | +1,283 | +1,273 | -92 | -10 | -0.00% | +0 | -17 | +1 | +46 | 1.000 | 3% | 1/4 | -0.87 | ✗ (raw p 0.791) | NO HEDGE IMPROVEMENT |
| commodity | 3M | 1 | +57 | -148 | -154 | -211 | -7 | +0.43% | -67 | +64 | +20 | +119 | 1.000 | 8% | 1/4 | -1.08 | ✗ (raw p 0.860) | NO HEDGE IMPROVEMENT |
| crypto | 3M | 1 | +148,615 | — | +148,532 | -84 | — | -0.01% | — | -67 | -3 | +69 | — | 6% | 0/1 | — | ✗ (raw p —) | INSUFFICIENT DATA |
| volatility | 3M | 1 | -7 | +42 | +195 | +202 | +153 | -0.16% | +0 | -162 | -68 | -291 | 1.000 | 25% | 2/2 | +0.66 | ✗ (raw p 0.212) | INSUFFICIENT DATA |

ES Δ and Drawdown Δ: production minus breadth (positive = the breadth hedge lost less). Cost Δ and Profit sacrificed Δ: breadth minus production (positive = costs / gives up more). Variance Δ: change in realised variance reduction. Size ratio: median breadth ÷ production size in the same risk unit (quantity of the same product; market beta-dollars when the product changed and the primary risk is the market). ES, VaR and drawdown take the same engine path and are all scored on ES95, so their rows are identical.

## ΔU at every λ (breadth − production / breadth − no-breadth)

| Objective | Horizon | λ = 0.5 | λ = 1 | λ = 2 | λ = 5 | λ = 10 |
|---|---|---|---|---|---|---|
| min_variance | 1W | -227 / -164 | -171 / -160 | -59 / -150 | +277 / -123 | +836 / -76 |
| target_vol | 1W | +135 / -168 | +149 / -217 | +178 / -316 | +264 / -612 | +406 / -1,105 |
| beta | 1W | -541 / -42 | -552 / -88 | -575 / -182 | -644 / -462 | -759 / -928 |
| systematic | 1W | -184 / +1 | -176 / -9 | -160 / -29 | -111 / -88 | -30 / -187 |
| sector | 1W | +8 / -2 | -6 / -10 | -36 / -25 | -124 / -72 | -271 / -150 |
| name | 1W | +24 / +4 | +21 / +2 | +17 / -4 | +4 / -19 | -19 / -46 |
| crash | 1W | +283 / +122 | +353 / +152 | +491 / +210 | +908 / +386 | +1,601 / +679 |
| es | 1W | -201 / -275 | -188 / -290 | -162 / -318 | -85 / -404 | +44 / -548 |
| var | 1W | -201 / -275 | -188 / -290 | -162 / -318 | -85 / -404 | +44 / -548 |
| drawdown | 1W | -201 / -275 | -188 / -290 | -162 / -318 | -85 / -404 | +44 / -548 |
| duration | 1W | +19 / +1 | +16 / +1 | +12 / +2 | -2 / +5 | -26 / +9 |
| curve | 1W | +19 / +1 | +17 / +2 | +13 / +4 | -0 / +11 | -22 / +22 |
| credit | 1W | +44 / +1 | +34 / +0 | +15 / -1 | -43 / -4 | -140 / -10 |
| fx | 1W | +35 / +7 | +29 / +6 | +17 / +5 | -19 / +0 | -79 / -7 |
| commodity | 1W | -2 / +0 | -4 / +1 | -7 / +2 | -19 / +6 | -38 / +13 |
| crypto | 1W | +0 / -4 | +0 / -20 | -0 / -50 | -0 / -140 | -1 / -290 |
| volatility | 1W | +175 / -3 | +151 / -14 | +102 / -37 | -43 / -106 | -285 / -221 |
| min_variance | 1M | +312 / -155 | +353 / -146 | +436 / -129 | +683 / -76 | +1,096 / +11 |
| target_vol | 1M | +371 / -99 | +396 / -127 | +447 / -184 | +601 / -355 | +856 / -639 |
| beta | 1M | -599 / -113 | -459 / -136 | -180 / -182 | +659 / -319 | +2,057 / -547 |
| systematic | 1M | -234 / +18 | -166 / +24 | -32 / +35 | +372 / +68 | +1,046 / +123 |
| sector | 1M | -65 / -6 | -56 / -16 | -38 / -35 | +17 / -93 | +108 / -190 |
| name | 1M | +1 / +9 | +2 / +13 | +6 / +20 | +16 / +43 | +33 / +82 |
| crash | 1M | +351 / +419 | +480 / +495 | +738 / +648 | +1,511 / +1,106 | +2,800 / +1,870 |
| es | 1M | +270 / +67 | +266 / +73 | +258 / +86 | +235 / +124 | +196 / +187 |
| var | 1M | +270 / +67 | +266 / +73 | +258 / +86 | +235 / +124 | +196 / +187 |
| drawdown | 1M | +270 / +67 | +266 / +73 | +258 / +86 | +235 / +124 | +196 / +187 |
| duration | 1M | -37 / -2 | -33 / -2 | -24 / -1 | +2 / +3 | +45 / +8 |
| curve | 1M | -36 / -3 | -32 / -3 | -23 / -4 | +4 / -5 | +49 / -8 |
| credit | 1M | -1 / +0 | +0 / +1 | +1 / +2 | +5 / +6 | +12 / +13 |
| fx | 1M | +2 / -4 | +2 / -4 | +2 / -2 | +2 / +2 | +1 / +8 |
| commodity | 1M | -7 / +12 | +1 / +19 | +16 / +34 | +62 / +77 | +137 / +148 |
| crypto | 1M | -0 / +0 | -0 / -0 | +0 / -0 | +1 / -0 | +1 / -0 |
| volatility | 1M | +66 / +159 | +93 / +188 | +145 / +247 | +302 / +423 | +564 / +717 |
| min_variance | 3M | +110 / +98 | -150 / +20 | -669 / -135 | -2,225 / -602 | -4,820 / -1,379 |
| target_vol | 3M | -329 / -214 | -769 / -172 | -1,649 / -88 | -4,287 / +164 | -8,685 / +583 |
| beta | 3M | -1,463 / -124 | -1,602 / -156 | -1,881 / -220 | -2,717 / -413 | -4,111 / -735 |
| systematic | 3M | -480 / -74 | -455 / -113 | -406 / -190 | -260 / -420 | -15 / -804 |
| sector | 3M | +117 / -37 | +135 / -25 | +170 / -1 | +277 / +71 | +455 / +191 |
| name | 3M | +101 / +9 | +88 / +9 | +63 / +9 | -12 / +10 | -137 / +11 |
| crash | 3M | +7,297 / +262 | +8,416 / +492 | +10,653 / +950 | +17,366 / +2,325 | +28,553 / +4,616 |
| es | 3M | -1,553 / +183 | -1,777 / +180 | -2,225 / +176 | -3,569 / +161 | -5,810 / +138 |
| var | 3M | -1,553 / +183 | -1,777 / +180 | -2,225 / +176 | -3,569 / +161 | -5,810 / +138 |
| drawdown | 3M | -1,553 / +183 | -1,777 / +180 | -2,225 / +176 | -3,569 / +161 | -5,810 / +138 |
| duration | 3M | -61 / -30 | -72 / -36 | -93 / -49 | -158 / -86 | -265 / -148 |
| curve | 3M | -103 / -29 | -122 / -32 | -162 / -36 | -281 / -51 | -479 / -75 |
| credit | 3M | +4 / -2 | +35 / +1 | +97 / +6 | +285 / +21 | +597 / +46 |
| fx | 3M | -69 / -7 | -92 / -10 | -138 / -16 | -275 / -33 | -503 / -63 |
| commodity | 3M | -152 / -10 | -211 / -7 | -330 / -0 | -686 / +20 | -1,278 / +53 |
| crypto | 3M | — / — | — / — | — / — | — / — | — / — |
| volatility | 3M | +56 / +29 | +202 / +153 | +493 / +401 | +1,367 / +1,146 | +2,824 / +2,387 |

## Paired statistics (λ = 1, breadth − production)

| Objective | Horizon | Mean ΔU | Median per-case share (changed cases) | 95% CI | clustered t | % positive (changed cases) | Changed cases | Positive eras |
|---|---|---|---|---|---|---|---|---|
| min_variance | 1W | -171 | +0.03 | [-446, +72] | -1.29 | 56% | 3234 | 1/4 |
| target_vol | 1W | +149 | -0.00 | [-354, +661] | +0.58 | 50% | 2956 | 3/4 |
| beta | 1W | -552 | +0.00 | [-1,278, -76] | -1.80 | 50% | 1299 | 0/4 |
| systematic | 1W | -176 | +0.01 | [-324, -47] | -2.49 | 63% | 2415 | 0/4 |
| sector | 1W | -6 | -0.00 | [-40, +24] | -0.39 | 49% | 2030 | 1/4 |
| name | 1W | +21 | -0.00 | [-21, +63] | +1.00 | 32% | 1289 | 1/4 |
| crash | 1W | +353 | +0.15 | [-542, +1,399] | +0.71 | 56% | 575 | 2/4 |
| es | 1W | -188 | +0.02 | [-1,008, +407] | -0.52 | 54% | 2844 | 1/4 |
| var | 1W | -188 | +0.02 | [-1,008, +407] | -0.52 | 54% | 2844 | 1/4 |
| drawdown | 1W | -188 | +0.02 | [-1,008, +407] | -0.52 | 54% | 2844 | 1/4 |
| duration | 1W | +16 | -0.01 | [-1, +35] | +1.79 | 27% | 88 | 3/4 |
| curve | 1W | +17 | -0.01 | [-0, +35] | +1.88 | 26% | 99 | 4/4 |
| credit | 1W | +34 | -0.04 | [-0, +70] | +1.91 | 21% | 228 | 4/4 |
| fx | 1W | +29 | +0.22 | [-5, +68] | +1.57 | 64% | 14 | 2/4 |
| commodity | 1W | -4 | +0.01 | [-23, +11] | -0.40 | 59% | 71 | 2/4 |
| crypto | 1W | +0 | +0.00 | [-0, +0] | +0.27 | 55% | 33 | 0/1 |
| volatility | 1W | +151 | -0.26 | [-161, +498] | +0.90 | 38% | 58 | 2/2 |
| min_variance | 1M | +353 | -0.02 | [-169, +1,219] | +1.00 | 48% | 1259 | 2/4 |
| target_vol | 1M | +396 | -0.00 | [-265, +1,244] | +1.03 | 50% | 1497 | 3/4 |
| beta | 1M | -459 | +0.14 | [-1,424, +297] | -1.05 | 55% | 555 | 0/4 |
| systematic | 1M | -166 | +0.04 | [-532, +141] | -0.97 | 59% | 1079 | 1/4 |
| sector | 1M | -56 | +0.01 | [-218, +79] | -0.74 | 71% | 979 | 2/4 |
| name | 1M | +2 | -0.00 | [-26, +31] | +0.16 | 43% | 357 | 2/4 |
| crash | 1M | +480 | +0.49 | [-202, +1,331] | +1.23 | 55% | 256 | 2/4 |
| es | 1M | +266 | -0.01 | [-614, +1,681] | +0.45 | 48% | 1067 | 2/4 |
| var | 1M | +266 | -0.01 | [-614, +1,681] | +0.45 | 48% | 1067 | 2/4 |
| drawdown | 1M | +266 | -0.01 | [-614, +1,681] | +0.45 | 48% | 1067 | 2/4 |
| duration | 1M | -33 | +0.11 | [-103, +3] | -1.22 | 73% | 33 | 1/4 |
| curve | 1M | -32 | +0.06 | [-97, +7] | -1.20 | 59% | 46 | 1/4 |
| credit | 1M | +0 | +0.01 | [-10, +11] | +0.02 | 62% | 130 | 2/4 |
| fx | 1M | +2 | -0.01 | [-13, +18] | +0.28 | 25% | 16 | 2/4 |
| commodity | 1M | +1 | +0.06 | [-56, +51] | +0.03 | 64% | 44 | 2/4 |
| crypto | 1M | -0 | +0.00 | [-0, +0] | -0.25 | 57% | 14 | 0/1 |
| volatility | 1M | +93 | +0.67 | [-319, +460] | +0.47 | 67% | 36 | 1/2 |
| min_variance | 3M | -150 | -0.76 | [-818, +542] | -0.43 | 39% | 378 | 1/4 |
| target_vol | 3M | -769 | -0.42 | [-2,568, +1,062] | -0.83 | 48% | 484 | 1/4 |
| beta | 3M | -1,602 | -0.82 | [-3,848, -311] | -1.78 | 41% | 157 | 0/4 |
| systematic | 3M | -455 | +0.06 | [-1,018, +6] | -1.74 | 57% | 361 | 1/4 |
| sector | 3M | +135 | -0.05 | [-252, +577] | +0.64 | 36% | 345 | 3/4 |
| name | 3M | +88 | -0.05 | [-25, +246] | +1.28 | 42% | 91 | 2/4 |
| crash | 3M | +8,416 | +21.15 | [+1,598, +15,259] | +2.41 | 69% | 136 | 3/4 |
| es | 3M | -1,777 | -0.19 | [-3,072, -539] | -2.75 | 44% | 316 | 0/4 |
| var | 3M | -1,777 | -0.19 | [-3,072, -539] | -2.75 | 44% | 316 | 0/4 |
| drawdown | 3M | -1,777 | -0.19 | [-3,072, -539] | -2.75 | 44% | 316 | 0/4 |
| duration | 3M | -72 | +0.00 | [-180, -7] | -1.63 | 50% | 16 | 1/4 |
| curve | 3M | -122 | -0.05 | [-247, -26] | -2.17 | 43% | 30 | 1/4 |
| credit | 3M | +35 | +0.48 | [-57, +252] | +0.44 | 70% | 40 | 4/4 |
| fx | 3M | -92 | +0.60 | [-394, +22] | -0.87 | 67% | 6 | 1/4 |
| commodity | 3M | -211 | -1.68 | [-695, +75] | -1.08 | 29% | 21 | 1/4 |
| crypto | 3M | -84 | — | — | — | 0% | 0 | 0/1 |
| volatility | 3M | +202 | +12.31 | [-394, +810] | +0.66 | 50% | 16 | 2/2 |

Per-case shares decompose ΔU exactly (they sum to it): each case's share of the hedged-risk measure, profit and cost. Cases with an identical decision still carry small shares under the standard-deviation measure (the distribution's mean and spread differ), so the share of positive cases is reported among cases whose decision changed.

## Variance-sensitive vs hard-target objectives

**Variance-sensitive (minimum variance, target volatility, VaR, ES, drawdown).** Σ weighs risk against cost directly here, so the forecast can change sizes.

| Objective | Horizon | Decision changed | of which sizing / product | Legs changed | Size change when only the size changed: median / p90 | ΔU from sizing changes | ΔU from product changes | Size ratio p25–p75 |
|---|---|---|---|---|---|---|---|---|
| min_variance | 1W | 57% | 36% / 20% | 12% | 14.29% / 43.57% | -152 | -117 | 0.84–1.00 |
| target_vol | 1W | 52% | 25% / 27% | 21% | 33.33% / 70.65% | +197 | -12 | 0.67–1.05 |
| es | 1W | 50% | 25% / 25% | 18% | 25.00% / 50.00% | -68 | -339 | 0.75–1.00 |
| var | 1W | 50% | 25% / 25% | 18% | 25.00% / 50.00% | -68 | -339 | 0.75–1.00 |
| drawdown | 1W | 50% | 25% / 25% | 18% | 25.00% / 50.00% | -68 | -339 | 0.75–1.00 |
| min_variance | 1M | 47% | 32% / 14% | 7% | 8.35% / 33.33% | -178 | +628 | 1.00–1.00 |
| target_vol | 1M | 55% | 34% / 21% | 12% | 25.00% / 66.67% | +64 | +452 | 0.79–1.10 |
| es | 1M | 40% | 22% / 17% | 8% | 12.93% / 50.00% | -91 | +357 | 1.00–1.00 |
| var | 1M | 40% | 22% / 17% | 8% | 12.93% / 50.00% | -91 | +357 | 1.00–1.00 |
| drawdown | 1M | 40% | 22% / 17% | 8% | 12.93% / 50.00% | -91 | +357 | 1.00–1.00 |
| min_variance | 3M | 43% | 30% / 13% | 6% | 6.15% / 22.53% | -100 | +71 | 1.00–1.00 |
| target_vol | 3M | 54% | 35% / 20% | 12% | 21.12% / 66.67% | -401 | -345 | 0.92–1.17 |
| es | 3M | 36% | 19% / 16% | 7% | 7.89% / 33.33% | +3,181 | -4,958 | 1.00–1.00 |
| var | 3M | 36% | 19% / 16% | 7% | 7.89% / 33.33% | +3,181 | -4,958 | 1.00–1.00 |
| drawdown | 3M | 36% | 19% / 16% | 7% | 7.89% / 33.33% | +3,181 | -4,958 | 1.00–1.00 |

**Hard-target (beta, systematic, sector, name, duration, curve, credit, FX, commodity, crypto, volatility).** The target exposure is pinned (weight 10⁴), so sizes follow exposures and the forecast can only change product and cost trade-offs.

| Objective | Horizon | Decision changed | of which sizing / product | Legs changed | Size change when only the size changed: median / p90 | ΔU from sizing changes | ΔU from product changes | Size ratio p25–p75 |
|---|---|---|---|---|---|---|---|---|
| beta | 1W | 28% | 1% / 26% | 0% | 0.12% / 11.11% | +7 | -939 | 1.00–1.00 |
| systematic | 1W | 43% | 25% / 18% | 8% | 1.06% / 6.11% | +121 | -372 | 1.00–1.00 |
| sector | 1W | 70% | 63% / 7% | 3% | 0.02% / 0.25% | -11 | +9 | 1.00–1.00 |
| name | 1W | 23% | 20% / 3% | 1% | 0.02% / 1.94% | +0 | +42 | 1.00–1.00 |
| duration | 1W | 5% | 4% / 1% | 0% | 0.19% / 0.73% | -6 | +35 | 1.00–1.00 |
| curve | 1W | 5% | 4% / 1% | 1% | 0.19% / 0.73% | -6 | +36 | 1.00–1.00 |
| credit | 1W | 30% | 27% / 3% | 0% | 0.32% / 2.21% | -11 | +56 | 1.00–1.00 |
| fx | 1W | 2% | 0% / 2% | 0% | — | +0 | +61 | 1.00–1.00 |
| commodity | 1W | 6% | 2% / 4% | 3% | 0.40% / 6.25% | -0 | -5 | 1.00–1.00 |
| crypto | 1W | 27% | 27% / 0% | 0% | 0.00% / 0.33% | +0 | +0 | 1.00–1.00 |
| volatility | 1W | 21% | 10% / 11% | 2% | 5.97% / 50.00% | -27 | +302 | 1.00–1.00 |
| beta | 1M | 25% | 1% / 24% | 0% | 5.88% / 11.11% | +18 | -937 | 1.00–1.00 |
| systematic | 1M | 40% | 24% / 16% | 8% | 0.76% / 4.24% | +94 | -380 | 1.00–1.00 |
| sector | 1M | 71% | 65% / 6% | 1% | 0.02% / 0.22% | +58 | -135 | 1.00–1.00 |
| name | 1M | 13% | 11% / 2% | 1% | 0.01% / 0.62% | +0 | +6 | 1.00–1.00 |
| duration | 1M | 4% | 2% / 2% | 0% | 0.20% / 0.43% | +2 | -72 | 1.00–1.00 |
| curve | 1M | 5% | 2% / 3% | 2% | 0.20% / 0.43% | +2 | -70 | 1.00–1.00 |
| credit | 1M | 34% | 31% / 3% | 0% | 0.61% / 3.37% | +12 | -12 | 1.00–1.00 |
| fx | 1M | 5% | 3% / 3% | 0% | 0.09% / 0.34% | -0 | +4 | 1.00–1.00 |
| commodity | 1M | 8% | 3% / 5% | 1% | 11.11% / 50.00% | -28 | +10 | 1.00–1.00 |
| crypto | 1M | 25% | 25% / 0% | 0% | 0.00% / 0.01% | -0 | +0 | 1.00–1.00 |
| volatility | 1M | 27% | 10% / 16% | 1% | 5.88% / 50.00% | -7 | +121 | 1.00–1.00 |
| beta | 3M | 21% | 2% / 19% | 0% | 5.56% / 12.50% | +52 | -2,412 | 1.00–1.00 |
| systematic | 3M | 41% | 25% / 16% | 9% | 0.87% / 3.53% | +82 | -762 | 1.00–1.00 |
| sector | 3M | 76% | 68% / 9% | 1% | 0.03% / 0.17% | -75 | +229 | 1.00–1.00 |
| name | 3M | 10% | 8% / 3% | 1% | 0.01% / 1.40% | -7 | +215 | 1.00–1.00 |
| duration | 3M | 5% | 1% / 4% | 0% | 0.23% / 0.34% | +0 | -118 | 1.00–1.00 |
| curve | 3M | 10% | 1% / 9% | 5% | 0.23% / 0.34% | +0 | -197 | 1.00–1.00 |
| credit | 3M | 33% | 28% / 5% | 0% | 0.46% / 5.86% | +69 | -16 | 1.00–1.00 |
| fx | 3M | 6% | 3% / 3% | 0% | 0.00% / 0.11% | +1 | -131 | 1.00–1.00 |
| commodity | 3M | 12% | 3% / 8% | 1% | 4.76% / 25.00% | -8 | -273 | 1.00–1.00 |
| crypto | 3M | 35% | 29% / 6% | 0% | — | — | — | — |
| volatility | 3M | 36% | 11% / 25% | 2% | 9.46% / 33.33% | +50 | +75 | 1.00–1.00 |

What the data say about the expectation. Hard-target objectives change product as expected, and where they also "change size" the change is tiny (median column above): the pin on the target is a weight of 10⁴, not a hard constraint, and the product's other exposures (a sector ETF's or a high-yield ETF's market beta) are penalised with Σ, so a different market variance moves the optimum by a few shares; the systematic objective moves more because it matches the market and sector factors one by one, each weighted by its own variance, and a different market variance changes that balance. Variance-sensitive objectives change size more often and by more, but their realised utility difference also comes mostly from product and leg choices (ΔU from product changes) — Σ also ranks products by risk against cost, so a different volatility level changes which index future, ETF or number of legs the optimiser prefers.

## Why utility changed (attribution, λ = 1, breadth − production)

| Objective | Horizon | ΔU | = risk reduction Δ | + profit Δ | + cost Δ | Basis error Δ | Tail Δ (unhedged worst 5%) | Hedge-size error Δ |
|---|---|---|---|---|---|---|---|---|
| min_variance | 1W | -171 | -299 | +112 | +15.6 | +129.2 | -51 | +11.097 |
| target_vol | 1W | +149 | +108 | +29 | +12.7 | +216.9 | +978 | +0.281 |
| beta | 1W | -552 | -518 | -23 | -11.1 | -266.9 | -2,284 | +0.042 |
| systematic | 1W | -176 | -182 | +16 | -9.5 | -58.7 | -260 | +0.015 |
| sector | 1W | -6 | +22 | -29 | +1.1 | -0.2 | +12 | +0.048 |
| name | 1W | +21 | +29 | -4 | -3.3 | -7.0 | -29 | +0.011 |
| crash | 1W | +353 | +181 | +139 | +32.7 | +220.1 | +195 | -0.025 |
| es | 1W | -188 | -221 | +26 | +7.4 | +61.3 | -252 | +3.149 |
| var | 1W | -188 | -221 | +26 | +7.4 | +61.3 | -252 | +3.149 |
| drawdown | 1W | -188 | -221 | +26 | +7.4 | +61.3 | -252 | +3.149 |
| duration | 1W | +16 | +21 | -5 | -0.2 | +7.9 | -0 | -0.165 |
| curve | 1W | +17 | +21 | -4 | -0.3 | +8.9 | -0 | -0.170 |
| credit | 1W | +34 | +53 | -19 | -0.0 | +282.7 | +7 | -0.038 |
| fx | 1W | +29 | +41 | -12 | -0.1 | +65.1 | +0 | -2.023 |
| commodity | 1W | -4 | -1 | -4 | +1.6 | +1.7 | +2 | -0.038 |
| crypto | 1W | +0 | +0 | -0 | +0.0 | -0.0 | -0 | -0.011 |
| volatility | 1W | +151 | +202 | -48 | -2.7 | -29.1 | +417 | +0.451 |
| min_variance | 1M | +353 | +254 | +83 | +16.3 | -79.5 | +1,254 | -0.038 |
| target_vol | 1M | +396 | +305 | +51 | +40.1 | +251.8 | +1,932 | +0.247 |
| beta | 1M | -459 | -604 | +280 | -135.0 | -134.7 | -887 | +0.024 |
| systematic | 1M | -166 | -261 | +135 | -40.5 | -48.2 | -26 | +0.011 |
| sector | 1M | -56 | -79 | +18 | +5.1 | +8.4 | +4 | +0.005 |
| name | 1M | +2 | +5 | +3 | -5.7 | -1.9 | +1 | +0.026 |
| crash | 1M | +480 | +57 | +258 | +165.0 | +44.7 | +5 | -0.081 |
| es | 1M | +266 | +258 | -8 | +16.2 | +2.9 | +227 | +0.376 |
| var | 1M | +266 | +258 | -8 | +16.2 | +2.9 | +227 | +0.376 |
| drawdown | 1M | +266 | +258 | -8 | +16.2 | +2.9 | +227 | +0.376 |
| duration | 1M | -33 | -41 | +9 | -0.4 | +2.8 | -0 | +0.015 |
| curve | 1M | -32 | -41 | +9 | -0.4 | +3.6 | -0 | +0.022 |
| credit | 1M | +0 | -2 | +1 | +0.6 | -6.9 | -12 | +0.238 |
| fx | 1M | +2 | +2 | -0 | -0.1 | -2.5 | -24 | -2.186 |
| commodity | 1M | +1 | -23 | +15 | +8.3 | +0.2 | -61 | +0.311 |
| crypto | 1M | -0 | -0 | +0 | +0.0 | -0.1 | +0 | +0.000 |
| volatility | 1M | +93 | +34 | +52 | +6.2 | +52.6 | +910 | -1.157 |
| min_variance | 3M | -150 | +342 | -519 | +27.3 | +29.4 | -3,856 | -0.006 |
| target_vol | 3M | -769 | +70 | -880 | +40.7 | +215.6 | -2,775 | +0.006 |
| beta | 3M | -1,602 | -923 | -279 | -400.3 | -333.7 | -6,221 | +0.028 |
| systematic | 3M | -455 | -409 | +49 | -95.3 | +7.7 | -354 | +0.007 |
| sector | 3M | +135 | +100 | +36 | -0.8 | +5.6 | -1 | +0.030 |
| name | 3M | +88 | +138 | -25 | -25.2 | +1.7 | -0 | -0.027 |
| crash | 3M | +8,416 | +4,946 | +2,237 | +1,232.3 | +229.3 | +3,427 | +0.156 |
| es | 3M | -1,777 | -1,335 | -448 | +6.1 | +4.3 | -930 | -0.062 |
| var | 3M | -1,777 | -1,335 | -448 | +6.1 | +4.3 | -930 | -0.062 |
| drawdown | 3M | -1,777 | -1,335 | -448 | +6.1 | +4.3 | -930 | -0.062 |
| duration | 3M | -72 | -49 | -22 | -1.6 | -2.2 | +0 | -0.042 |
| curve | 3M | -122 | -81 | -40 | -1.8 | -2.6 | -86 | -0.049 |
| credit | 3M | +35 | -21 | +62 | -6.9 | +4.7 | +389 | +0.031 |
| fx | 3M | -92 | -45 | -46 | -1.4 | +0.1 | +0 | +0.424 |
| commodity | 3M | -211 | -73 | -119 | -20.2 | -19.8 | +0 | +0.178 |
| crypto | 3M | -84 | — | — | — | — | — | — |
| volatility | 3M | +202 | -157 | +291 | +67.5 | +5.9 | +0 | +0.976 |

Profit Δ and cost Δ enter U with their sign (positive = breadth gave up less profit / paid less). Basis error Δ > 0 = breadth's hedge tracked the targeted exposure worse. Tail Δ > 0 = breadth's hedged book lost less in the unhedged book's worst 5% of windows. Hedge-size error = mean |ex-post variance-optimal multiple − 1| of the package (Δ < 0 = closer to the right size); it is unstable when a hedge's P&L over the window is close to zero, so large values come from a few near-zero hedges and should not be read as systematic over- or under-hedging.

## Product-selection effects (common transitions, breadth vs production)

Transitions between two products of the same type (for example equity_index_future → equity_index_future) are changes of contract, such as NQ → ES or ES → MES.

| Objective | Horizon | Production → breadth product | Cases | Δ risk (signed RMS of hedged P&L change) | Δ cost / case | Δ profit sacrificed / case | Share of ΔU |
|---|---|---|---|---|---|---|---|
| min_variance | 1W | equity_index_future → equity_index_future | 666 | -1,454 | -21.6 | -360 | +110 |
| min_variance | 1W | etf → etf | 133 | +8,477 | -34.3 | -196 | -88 |
| min_variance | 1W | etf → equity_index_future | 97 | +12,474 | -33.0 | -1,191 | -135 |
| min_variance | 1W | equity_index_future → no hedge | 88 | +5,331 | -19.0 | -270 | -19 |
| min_variance | 1W | equity_index_future → etf | 82 | +3,339 | -46.4 | -1,137 | +12 |
| min_variance | 1W | no hedge → equity_index_future | 28 | -7,711 | +14.9 | +230 | +18 |
| target_vol | 1W | equity_index_future → equity_index_future | 518 | -7,350 | -2.3 | +168 | +203 |
| target_vol | 1W | equity_index_future → no hedge | 497 | +9,859 | -26.7 | -488 | -359 |
| target_vol | 1W | no hedge → equity_index_future | 134 | -12,464 | +18.9 | +1,450 | +131 |
| target_vol | 1W | etf → etf | 111 | -3,501 | -23.5 | -422 | +20 |
| target_vol | 1W | etf → no hedge | 83 | +10,651 | -41.3 | +285 | -84 |
| target_vol | 1W | equity_index_future → etf | 50 | +4,205 | -43.2 | -197 | -6 |
| target_vol | 1W | etf → equity_index_future | 48 | +14,607 | +0.0 | -1,383 | -73 |
| target_vol | 1W | no hedge → etf | 22 | -12,074 | +19.5 | +334 | +25 |
| beta | 1W | equity_index_future → equity_index_future | 828 | +5,858 | -5.8 | +34 | -213 |
| beta | 1W | equity_index_future → etf | 129 | +12,108 | -0.7 | -656 | -148 |
| beta | 1W | etf → equity_index_future | 122 | +7,285 | -4.5 | -437 | -39 |
| beta | 1W | index_option → equity_index_future | 81 | +24,081 | +739.2 | +3,861 | -536 |
| beta | 1W | etf → etf | 22 | +11,741 | -0.0 | -1,861 | -17 |
| systematic | 1W | equity_index_future → equity_index_future | 550 | +6,851 | +7.8 | -62 | -235 |
| systematic | 1W | etf → etf | 231 | +3,357 | +27.6 | +4 | -16 |
| systematic | 1W | equity_index_future → etf | 70 | +10,481 | +165.4 | -1,135 | -59 |
| systematic | 1W | etf → equity_index_future | 56 | -2,365 | -112.3 | +68 | +4 |
| systematic | 1W | commodity_option → commodity_option | 28 | +1,301 | -37.7 | +889 | -5 |
| systematic | 1W | index_option → equity_index_future | 21 | +20,557 | +986.1 | +3,620 | -106 |
| sector | 1W | etf → etf | 210 | -3,616 | -14.6 | +398 | +9 |
| name | 1W | equity_index_future → equity_index_future | 39 | -5,815 | -0.8 | +516 | +10 |
| crash | 1W | index_option → inverse_etf | 303 | -12,666 | -314.0 | -107 | +2,539 |
| crash | 1W | inverse_etf → index_option | 237 | +17,487 | -218.1 | -2,572 | -1,214 |
| crash | 1W | index_option → index_option | 33 | -5,033 | -162.7 | +59 | +26 |
| es | 1W | equity_index_future → equity_index_future | 650 | -2,760 | -6.2 | +218 | +95 |
| es | 1W | equity_index_future → no hedge | 314 | +6,970 | -20.0 | -381 | -174 |
| es | 1W | etf → etf | 154 | +6,243 | -15.2 | -504 | -28 |
| es | 1W | no hedge → equity_index_future | 76 | -7,394 | +12.0 | +617 | +154 |
| es | 1W | etf → equity_index_future | 58 | +12,540 | -17.7 | +459 | -232 |
| es | 1W | etf → no hedge | 57 | +5,708 | -23.0 | -360 | -135 |
| es | 1W | equity_index_future → etf | 56 | +3,958 | -26.2 | -739 | +23 |
| commodity | 1W | commodity_option → commodity_option | 40 | -632 | -36.4 | +167 | -5 |
| volatility | 1W | index_option → index_option | 30 | -9,596 | +41.6 | +300 | +302 |
| min_variance | 1M | equity_index_future → equity_index_future | 227 | +7,212 | -2.2 | -85 | -151 |
| min_variance | 1M | etf → etf | 47 | +6,170 | -28.4 | -541 | -19 |
| target_vol | 1M | equity_index_future → equity_index_future | 314 | +1,060 | -7.8 | -212 | +0 |
| target_vol | 1M | equity_index_future → no hedge | 83 | +14,798 | -28.8 | -1,083 | -135 |
| target_vol | 1M | no hedge → equity_index_future | 55 | -18,177 | +22.6 | +2,504 | +105 |
| target_vol | 1M | etf → etf | 26 | -6,454 | -109.7 | +1,295 | -10 |
| beta | 1M | equity_index_future → equity_index_future | 395 | +8,955 | -16.9 | +215 | -277 |
| beta | 1M | index_option → equity_index_future | 61 | -4,809 | +5,169.3 | -11,603 | +267 |
| beta | 1M | equity_index_future → etf | 36 | +15,950 | +76.5 | -2,742 | -27 |
| beta | 1M | etf → equity_index_future | 33 | +34,164 | -317.0 | -3,649 | -280 |
| systematic | 1M | equity_index_future → equity_index_future | 267 | +13,657 | +19.5 | -12 | -497 |
| systematic | 1M | etf → etf | 78 | +6,884 | +102.0 | -792 | -0 |
| sector | 1M | etf → etf | 84 | +10,872 | -84.6 | -298 | -135 |
| crash | 1M | index_option → inverse_etf | 136 | -21,480 | -1,386.4 | +2,204 | +1,886 |
| crash | 1M | inverse_etf → index_option | 106 | +35,962 | -1,710.5 | -8,122 | -821 |
| es | 1M | equity_index_future → equity_index_future | 317 | +1,457 | -4.4 | -477 | +170 |
| es | 1M | etf → etf | 25 | -4,463 | -11.4 | -514 | +17 |
| volatility | 1M | index_option → index_option | 22 | -3,068 | -28.5 | -350 | +121 |
| min_variance | 3M | equity_index_future → equity_index_future | 82 | -10,437 | -16.3 | +2,113 | -86 |
| target_vol | 3M | equity_index_future → equity_index_future | 102 | +13,319 | -26.2 | +2,478 | -690 |
| target_vol | 3M | no hedge → equity_index_future | 27 | -32,796 | +31.9 | +7,639 | +97 |
| target_vol | 3M | equity_index_future → no hedge | 20 | +33,281 | -53.9 | -1,292 | -297 |
| beta | 3M | equity_index_future → equity_index_future | 112 | +22,040 | +32.9 | +1,716 | -1,314 |
| systematic | 3M | equity_index_future → equity_index_future | 95 | +22,958 | +115.7 | +599 | -957 |
| systematic | 3M | etf → etf | 26 | +18,569 | +486.7 | -1,539 | -77 |
| sector | 3M | etf → etf | 39 | -12,309 | +7.2 | -446 | +229 |
| crash | 3M | inverse_etf → index_option | 77 | +66,189 | -9,163.2 | -17,802 | +8,171 |
| crash | 3M | index_option → inverse_etf | 52 | -34,224 | -3,920.2 | -4,930 | +9,179 |
| es | 3M | equity_index_future → equity_index_future | 103 | -8,687 | -4.6 | +1,732 | -4,813 |

## Size effects (breadth ÷ production, same risk unit)

| Objective | Horizon | Median | p25 | p75 | Larger / smaller | By product (median) | High vol / normal vol (median) |
|---|---|---|---|---|---|---|---|
| min_variance | 1W | 1.000 | 0.84 | 1.00 | 21% / 41% | crypto_etf 1.00, crypto_futures_etf 1.00, equity_index_future 1.00, etf 0.98, index_option 1.19, treasury_future 1.00 | 1.00 / 0.97 |
| target_vol | 1W | 1.000 | 0.67 | 1.05 | 29% / 43% | crypto_etf 1.00, crypto_futures_etf 1.00, equity_index_future 1.00, etf 0.98 | 1.00 / 0.94 |
| beta | 1W | 1.000 | 1.00 | 1.00 | 12% / 16% | equity_index_future 1.00, etf 1.00, index_option 1.00 | 1.00 / 1.00 |
| systematic | 1W | 1.000 | 1.00 | 1.00 | 21% / 19% | commodity_option 1.00, crypto_etf 1.00, crypto_futures_etf 1.00, equity_index_future 1.00, etf 1.00, ig_corporate 1.00, index_option 1.00, treasury_future 1.00 | 1.00 / 1.00 |
| sector | 1W | 1.000 | 1.00 | 1.00 | 25% / 41% | etf 1.00 | 1.00 / 1.00 |
| name | 1W | 1.000 | 1.00 | 1.00 | 7% / 15% | common_stock 1.00, equity_index_future 1.00, equity_put 1.00, etf 1.00, etn 1.00, high_yield 1.00, ig_corporate 1.00, index_option 1.00, treasury_future 1.00 | 1.00 / 1.00 |
| crash | 1W | 1.000 | 1.00 | 1.00 | 7% / 6% | index_option 1.00, inverse_etf 1.00 | 1.00 / 1.00 |
| es | 1W | 1.000 | 0.75 | 1.00 | 21% / 37% | crypto_etf 1.00, crypto_futures_etf 1.00, equity_index_future 1.00, etf 0.98 | 1.00 / 1.00 |
| duration | 1W | 1.000 | 1.00 | 1.00 | 2% / 2% | etf 1.00, treasury_future 1.00 | 1.00 / 1.00 |
| curve | 1W | 1.000 | 1.00 | 1.00 | 2% / 2% | etf 1.00, treasury_future 1.00 | 1.00 / 1.00 |
| credit | 1W | 1.000 | 1.00 | 1.00 | 12% / 16% | high_yield 1.00, ig_corporate 1.00, leveraged_loan 1.00 | 1.00 / 1.00 |
| fx | 1W | 1.000 | 1.00 | 1.00 | 0% / 0% | fx_forward 1.00, fx_future 1.00 | 1.00 / 1.00 |
| commodity | 1W | 1.000 | 1.00 | 1.00 | 1% / 0% | commodity_etf 1.00, commodity_option 1.00, etf 1.00 | 1.00 / 1.00 |
| crypto | 1W | 1.000 | 1.00 | 1.00 | 9% / 18% | crypto_etf 1.00, crypto_futures_etf 1.00 | 1.00 / 1.00 |
| volatility | 1W | 1.000 | 1.00 | 1.00 | 6% / 6% | etn 1.00, index_option 1.00 | 1.00 / 1.00 |
| min_variance | 1M | 1.000 | 1.00 | 1.00 | 22% / 24% | commodity_option 1.00, crypto_futures_etf 1.00, equity_index_future 1.00, equity_put 1.00, etf 1.00, index_option 1.16, treasury_future 1.00 | 1.00 / 1.00 |
| target_vol | 1M | 1.000 | 0.79 | 1.10 | 33% / 39% | crypto_futures_etf 1.00, equity_index_future 1.00, equity_put 1.10, etf 1.00, index_option 1.29 | 1.00 / 1.00 |
| beta | 1M | 1.000 | 1.00 | 1.00 | 12% / 13% | equity_index_future 1.00, etf 1.00, index_option 0.98 | 1.00 / 1.00 |
| systematic | 1M | 1.000 | 1.00 | 1.00 | 18% / 19% | commodity_option 1.00, crypto_futures_etf 1.00, equity_index_future 1.00, etf 1.00, ig_corporate 1.00, index_option 1.00, treasury_future 1.00 | 1.00 / 1.00 |
| sector | 1M | 1.000 | 1.00 | 1.00 | 30% / 38% | etf 1.00 | 1.00 / 1.00 |
| name | 1M | 1.000 | 1.00 | 1.00 | 6% / 7% | common_stock 1.00, equity_index_future 1.00, equity_put 1.00, etf 1.00, etn 1.00, high_yield 1.00, ig_corporate 1.00, index_option 1.00, treasury_future 1.00 | 1.00 / 1.00 |
| crash | 1M | 1.000 | 1.00 | 1.00 | 6% / 5% | index_option 1.00, inverse_etf 1.00 | 1.00 / 1.00 |
| es | 1M | 1.000 | 1.00 | 1.00 | 19% / 21% | crypto_futures_etf 1.00, equity_index_future 1.00, equity_put 1.00, etf 1.00, index_option 1.22, treasury_future 1.00 | 1.00 / 1.00 |
| duration | 1M | 1.000 | 1.00 | 1.00 | 1% / 1% | etf 1.00, treasury_future 1.00 | 1.00 / 1.00 |
| curve | 1M | 1.000 | 1.00 | 1.00 | 1% / 1% | etf 1.00, treasury_future 1.00 | 1.00 / 1.00 |
| credit | 1M | 1.000 | 1.00 | 1.00 | 17% / 15% | high_yield 1.00, ig_corporate 1.00 | 1.00 / 1.00 |
| fx | 1M | 1.000 | 1.00 | 1.00 | 0% / 1% | fx_forward 1.00 | 1.00 / 1.00 |
| commodity | 1M | 1.000 | 1.00 | 1.00 | 1% / 2% | commodity_etf 1.00, commodity_option 1.00, etf 1.00 | 1.00 / 1.00 |
| crypto | 1M | 1.000 | 1.00 | 1.00 | 5% / 20% | crypto_futures_etf 1.00 | 1.00 / 1.00 |
| volatility | 1M | 1.000 | 1.00 | 1.00 | 6% / 6% | etn 1.00, index_option 1.00 | 1.00 / 1.00 |
| min_variance | 3M | 1.000 | 1.00 | 1.00 | 22% / 18% | equity_index_future 1.00, etf 1.00, treasury_future 1.00 | 1.00 / 1.00 |
| target_vol | 3M | 1.000 | 0.92 | 1.17 | 35% / 32% | equity_index_future 1.00, etf 1.00 | 1.00 / 1.00 |
| beta | 3M | 1.000 | 1.00 | 1.00 | 8% / 14% | equity_index_future 1.00 | 1.00 / 1.00 |
| systematic | 3M | 1.000 | 1.00 | 1.00 | 17% / 20% | commodity_option 1.00, equity_index_future 1.00, etf 1.00, treasury_future 1.00 | 1.00 / 1.00 |
| sector | 3M | 1.000 | 1.00 | 1.00 | 38% / 36% | etf 1.00 | 1.00 / 1.00 |
| name | 3M | 1.000 | 1.00 | 1.00 | 5% / 4% | common_stock 1.00, equity_index_future 1.00, etf 1.00, etn 1.00, high_yield 1.00, treasury_future 1.00 | 1.00 / 1.00 |
| crash | 3M | 1.000 | 1.00 | 1.00 | 8% / 11% | index_option 1.00, inverse_etf 1.00 | 1.00 / 1.00 |
| es | 3M | 1.000 | 1.00 | 1.00 | 19% / 15% | equity_index_future 1.00, etf 1.00, treasury_future 1.00 | 1.00 / 1.00 |
| duration | 3M | 1.000 | 1.00 | 1.00 | 1% / 1% | etf 1.00, treasury_future 1.00 | 1.00 / 1.00 |
| curve | 3M | 1.000 | 1.00 | 1.00 | 1% / 1% | etf 1.00, treasury_future 1.00 | 1.00 / 1.00 |
| credit | 3M | 1.000 | 1.00 | 1.00 | 18% / 12% | high_yield 1.00, ig_corporate 1.00 | 1.00 / 1.00 |
| fx | 3M | 1.000 | 1.00 | 1.00 | 0% / 1% | fx_forward 1.00 | 1.00 / 1.00 |
| commodity | 3M | 1.000 | 1.00 | 1.00 | 2% / 1% | commodity_option 1.00 | 1.00 / 1.00 |
| volatility | 3M | 1.000 | 1.00 | 1.00 | 9% / 6% | — | 1.00 / 1.00 |

## Crisis and regime attribution (ΔU breadth − production, λ = 1)

| Objective | Horizon | High volatility ΔU (t) | Normal volatility ΔU (t) | Without 2009 | Without COVID 2020 | Without 2022 | Without all three |
|---|---|---|---|---|---|---|---|
| min_variance | 1W | -125 (-0.59) | -245 (-2.16) | -237 (-2.03) | -115 (-1.03) | -166 (-1.24) | -173 (-1.66) |
| target_vol | 1W | +525 (+1.19) | -381 (-2.36) | +192 (+0.68) | -56 (-0.31) | +124 (+0.44) | -75 (-0.37) |
| beta | 1W | -787 (-1.37) | -225 (-1.88) | -571 (-1.53) | -202 (-2.02) | -618 (-1.74) | -230 (-2.05) |
| systematic | 1W | -232 (-1.95) | -88 (-1.56) | -169 (-2.05) | -104 (-2.35) | -191 (-2.40) | -97 (-2.14) |
| sector | 1W | -12 (-0.47) | -16 (-1.23) | +1 (+0.08) | -20 (-1.42) | -1 (-0.08) | -10 (-0.64) |
| name | 1W | +45 (+1.25) | -13 (-2.38) | +24 (+1.01) | -3 (-0.27) | +32 (+1.50) | +7 (+0.73) |
| crash | 1W | +309 (+0.44) | +87 (+0.89) | +345 (+0.84) | +334 (+0.64) | +554 (+1.11) | +537 (+1.18) |
| es | 1W | -76 (-0.13) | -430 (-1.20) | -172 (-0.49) | -138 (-0.37) | -309 (-0.79) | -260 (-0.63) |
| duration | 1W | +24 (+1.99) | -0 (-0.62) | +17 (+1.71) | -0 (-0.40) | +18 (+1.82) | -0 (-0.45) |
| curve | 1W | +24 (+1.98) | +2 (+1.06) | +17 (+1.79) | +1 (+1.12) | +19 (+1.93) | +1 (+1.09) |
| credit | 1W | +42 (+2.29) | +1 (+1.23) | +35 (+1.85) | +2 (+1.43) | +34 (+1.89) | +1 (+1.06) |
| fx | 1W | +35 (+1.55) | +0 (—) | +33 (+1.71) | -1 (-0.45) | +31 (+1.55) | +0 (—) |
| commodity | 1W | +5 (+0.53) | -17 (-0.93) | -6 (-0.81) | -4 (-0.46) | -4 (-0.43) | -8 (-0.81) |
| crypto | 1W | +0 (+0.86) | -0 (-0.68) | +0 (+0.27) | +0 (+0.27) | +0 (+0.09) | +0 (+0.09) |
| volatility | 1W | +278 (+1.35) | -208 (-1.02) | +151 (+0.90) | -16 (-0.18) | +115 (+0.56) | -116 (-1.26) |
| min_variance | 1M | +635 (+1.03) | +51 (+0.37) | +475 (+1.52) | +233 (+0.78) | +99 (+0.50) | +49 (+0.35) |
| target_vol | 1M | +992 (+1.45) | -283 (-1.05) | +433 (+1.02) | +547 (+1.59) | +80 (+0.27) | +245 (+0.77) |
| beta | 1M | -907 (-1.23) | +250 (+1.16) | -479 (-1.17) | -238 (-0.64) | -602 (-1.37) | -381 (-0.90) |
| systematic | 1M | -266 (-0.96) | +15 (+0.18) | -149 (-0.83) | -170 (-0.97) | -277 (-2.09) | -283 (-1.74) |
| sector | 1M | -13 (-0.13) | -166 (-1.01) | -66 (-0.76) | -61 (-0.74) | -81 (-0.96) | -105 (-1.07) |
| name | 1M | -6 (-0.24) | -1 (-0.17) | +3 (+0.21) | +13 (+1.52) | -2 (-0.14) | +9 (+1.08) |
| crash | 1M | +683 (+1.40) | +289 (+0.96) | +528 (+1.53) | +370 (+0.85) | +457 (+1.03) | +412 (+1.04) |
| es | 1M | +450 (+0.37) | +46 (+0.17) | +365 (+0.60) | +540 (+0.97) | +112 (+0.18) | +458 (+0.83) |
| duration | 1M | -38 (-1.03) | -12 (-0.84) | -35 (-1.25) | -40 (-1.41) | -36 (-1.18) | -50 (-1.49) |
| curve | 1M | -39 (-0.98) | -7 (-0.45) | -34 (-1.25) | -39 (-1.43) | -34 (-1.10) | -46 (-1.37) |
| credit | 1M | +1 (+0.10) | -1 (-0.28) | +0 (+0.02) | -4 (-0.91) | -0 (-0.00) | -5 (-1.02) |
| fx | 1M | +3 (+0.26) | -0 (-0.99) | +2 (+0.21) | +6 (+0.67) | +6 (+0.82) | +12 (+1.81) |
| commodity | 1M | +20 (+0.69) | -46 (-1.13) | -5 (-0.23) | +0 (+0.01) | +1 (+0.03) | -8 (-0.29) |
| crypto | 1M | +0 (—) | -0 (-1.00) | -0 (-0.25) | -0 (-0.25) | +0 (+0.05) | +0 (+0.05) |
| volatility | 1M | +219 (+0.92) | -291 (-0.92) | +93 (+0.47) | -72 (-0.42) | +96 (+0.39) | -149 (-0.72) |
| min_variance | 3M | +191 (+0.34) | -520 (-0.77) | -124 (-0.35) | -439 (-0.92) | -167 (-0.46) | -475 (-0.83) |
| target_vol | 3M | -781 (-0.35) | -918 (-0.99) | -943 (-1.00) | -768 (-0.86) | -1,369 (-1.84) | -1,607 (-2.25) |
| beta | 3M | -105 (-0.25) | -2,887 (-2.08) | -1,653 (-1.87) | -1,633 (-1.84) | -1,724 (-1.97) | -1,825 (-1.76) |
| systematic | 3M | -284 (-0.73) | -529 (-2.24) | -461 (-1.50) | -495 (-1.65) | -573 (-2.21) | -631 (-1.79) |
| sector | 3M | +273 (+0.47) | +29 (+0.95) | +144 (+0.61) | +137 (+0.60) | -72 (-0.47) | -77 (-0.48) |
| name | 3M | +160 (+1.11) | -5 (-0.77) | +96 (+1.25) | +95 (+1.25) | +115 (+1.63) | +134 (+1.86) |
| crash | 3M | +11,138 (+1.16) | +5,260 (+1.84) | +9,054 (+2.99) | +4,893 (+1.72) | +9,082 (+2.57) | +5,711 (+2.65) |
| es | 3M | -3,250 (-2.32) | -688 (-0.77) | -1,891 (-2.96) | -1,200 (-2.22) | -1,728 (-2.59) | -1,198 (-2.07) |
| duration | 3M | -135 (-1.68) | +43 (+1.07) | -75 (-1.79) | -77 (-1.64) | -66 (-1.59) | -76 (-1.48) |
| curve | 3M | -227 (-1.88) | +38 (+0.87) | -128 (-2.35) | -131 (-2.36) | -81 (-1.73) | -95 (-1.83) |
| credit | 3M | +86 (+0.68) | +7 (+1.04) | +37 (+0.43) | +86 (+1.16) | +39 (+0.43) | +96 (+1.10) |
| fx | 3M | +15 (+1.19) | -212 (-1.69) | -95 (-0.88) | -97 (-0.88) | -110 (-1.07) | -121 (-0.98) |
| commodity | 3M | -311 (-0.81) | -132 (-1.27) | -207 (-1.03) | -242 (-1.27) | -199 (-0.96) | -239 (-1.10) |
| crypto | 3M | — (—) | — (—) | — (—) | — (—) | — (—) | — (—) |
| volatility | 3M | +507 (+2.09) | -265 (—) | +202 (+0.66) | +183 (+0.63) | -33 (-0.11) | -64 (-0.22) |

## Options (MODEL-PRICED — FLAT VOLATILITY ASSUMPTION): predefined sensitivity

| Objective | Horizon | ΔU (main) | ΔU with bought puts +5 vol points | ΔU excluding every case with an option leg |
|---|---|---|---|---|
| min_variance | 1W | -171 | -198 | -170 |
| target_vol | 1W | +149 | +125 | +103 |
| beta | 1W | -552 | -407 | -252 |
| systematic | 1W | -176 | -140 | -149 |
| sector | 1W | -6 | -6 | -6 |
| name | 1W | +21 | +22 | -0 |
| crash | 1W | +353 | +286 | +0 |
| es | 1W | -188 | -202 | -154 |
| duration | 1W | +16 | +16 | +16 |
| curve | 1W | +17 | +17 | +17 |
| credit | 1W | +34 | +34 | +34 |
| fx | 1W | +29 | +29 | +29 |
| commodity | 1W | -4 | +6 | +0 |
| crypto | 1W | +0 | +0 | +0 |
| volatility | 1W | +151 | +146 | +35 |
| min_variance | 1M | +353 | +343 | -108 |
| target_vol | 1M | +396 | +328 | +49 |
| beta | 1M | -459 | -307 | -691 |
| systematic | 1M | -166 | -112 | -322 |
| sector | 1M | -56 | -56 | -56 |
| name | 1M | +2 | +2 | -13 |
| crash | 1M | +480 | +682 | +0 |
| es | 1M | +266 | +221 | -52 |
| duration | 1M | -33 | -33 | -33 |
| curve | 1M | -32 | -32 | -32 |
| credit | 1M | +0 | +0 | +0 |
| fx | 1M | +2 | +2 | +2 |
| commodity | 1M | +1 | +14 | +6 |
| crypto | 1M | -0 | -0 | -0 |
| volatility | 1M | +93 | +65 | -1 |
| min_variance | 3M | -150 | -158 | -396 |
| target_vol | 3M | -769 | -826 | -989 |
| beta | 3M | -1,602 | -1,536 | -1,746 |
| systematic | 3M | -455 | -470 | -621 |
| sector | 3M | +135 | +135 | +135 |
| name | 3M | +88 | +79 | +14 |
| crash | 3M | +8,416 | +8,817 | +0 |
| es | 3M | -1,777 | -1,728 | -1,660 |
| duration | 3M | -72 | -72 | -72 |
| curve | 3M | -122 | -122 | -122 |
| credit | 3M | +35 | +35 | +35 |
| fx | 3M | -92 | -92 | -92 |
| commodity | 3M | -211 | -237 | +3 |
| crypto | 3M | -84 | -84 | -84 |
| volatility | 3M | +202 | +198 | +10 |

## Resizing (2018 on, where the confirmed multiples are out of sample)

| Objective | Horizon | Cases | ΔU breadth only | ΔU resizing only | ΔU breadth + resizing | Combination |
|---|---|---|---|---|---|---|
| min_variance | 1W | 2956 | -154 | -2 | -175 | additive |
| target_vol | 1W | 2956 | +246 | -5 | +231 | additive |
| beta | 1W | 2407 | -753 | -179 | -757 | additive |
| systematic | 1W | 2956 | -242 | -84 | -280 | additive |
| sector | 1W | 1525 | +8 | +0 | +8 | additive |
| name | 1W | 2845 | +51 | -117 | -71 | conflicting |
| crash | 1W | 2407 | +471 | -623 | -389 | conflicting |
| es | 1W | 2956 | -437 | -1 | -460 | additive |
| duration | 1W | 1122 | +21 | +0 | +21 | additive |
| curve | 1W | 1122 | +22 | +0 | +22 | additive |
| credit | 1W | 475 | +38 | +0 | +38 | additive |
| fx | 1W | 423 | +40 | +3 | +31 | redundant |
| commodity | 1W | 593 | -7 | -19 | -25 | additive |
| crypto | 1W | 124 | +0 | +0 | +0 | additive |
| volatility | 1W | 270 | +151 | +0 | +151 | additive |
| min_variance | 1M | 1403 | +875 | -96 | +784 | conflicting |
| target_vol | 1M | 1403 | +667 | -8 | +639 | additive |
| beta | 1M | 1143 | -599 | +109 | -599 | conflicting |
| systematic | 1M | 1403 | -115 | +55 | -72 | conflicting |
| sector | 1M | 723 | -102 | +0 | -102 | additive |
| name | 1M | 1351 | -0 | +21 | +18 | additive |
| crash | 1M | 1143 | +241 | +146 | +488 | interacting |
| es | 1M | 1403 | -269 | -26 | -398 | interacting |
| duration | 1M | 536 | -47 | -19 | -64 | additive |
| curve | 1M | 536 | -46 | -19 | -63 | additive |
| credit | 1M | 244 | -1 | -26 | -27 | additive |
| fx | 1M | 197 | +0 | -56 | -57 | additive |
| commodity | 1M | 287 | +9 | -52 | -45 | conflicting |
| crypto | 1M | 55 | -0 | +0 | -0 | additive |
| volatility | 1M | 133 | +102 | +0 | +102 | additive |
| min_variance | 3M | 457 | +70 | -22 | +81 | conflicting |
| target_vol | 3M | 457 | -741 | -22 | -741 | additive |
| beta | 3M | 373 | -2,503 | +74 | -2,503 | additive |
| systematic | 3M | 457 | -479 | -22 | -499 | additive |
| sector | 3M | 236 | +162 | +0 | +162 | additive |
| name | 3M | 441 | +153 | -132 | +83 | conflicting |
| crash | 3M | 373 | +7,861 | +13 | +8,174 | additive |
| es | 3M | 457 | -2,606 | +112 | -2,458 | additive |
| duration | 3M | 170 | -115 | +0 | -115 | additive |
| curve | 3M | 170 | -182 | +0 | -182 | additive |
| credit | 3M | 73 | +58 | -238 | -190 | conflicting |
| fx | 3M | 68 | -146 | -45 | -166 | additive |
| commodity | 3M | 91 | +66 | +339 | +404 | additive |
| volatility | 3M | 44 | +202 | +0 | +202 | additive |

Additive: the combination ≈ the sum of the two; redundant: ≈ the larger alone; interacting: departs from both; conflicting: the two effects have opposite signs.

## Answers

**1. Does breadth still improve the volatility forecast out of sample?** 1W: squared log error of the market factor -15.2% against the regression without breadth (t +6.9, 446 dates), -50.1% against production's 252-day sample — G1 passes; 1M: squared log error of the market factor -8.4% against the regression without breadth (t +3.2, 212 dates), -38.8% against production's 252-day sample — G1 passes; 3M: squared log error of the market factor -1.8% against the regression without breadth (t +0.7, 70 dates), -8.6% against production's 252-day sample — G1 fails.

**2. Does it change hedge sizing?** Share of matched cases where the challenger's package differs (identical / same products other size / other products): min_variance 1W 43% / 36% / 20%; target_vol 1W 48% / 25% / 27%; beta 1W 72% / 1% / 26%; systematic 1W 57% / 25% / 18%; sector 1W 30% / 63% / 7%; name 1W 77% / 20% / 3%; crash 1W 88% / 0% / 12%; es 1W 50% / 25% / 25%; var 1W 50% / 25% / 25%; drawdown 1W 50% / 25% / 25%; duration 1W 95% / 4% / 1%; curve 1W 95% / 4% / 1%; credit 1W 70% / 27% / 3%; fx 1W 98% / 0% / 2%; commodity 1W 94% / 2% / 4%; crypto 1W 73% / 27% / 0%; volatility 1W 79% / 10% / 11%; min_variance 1M 53% / 32% / 14%; target_vol 1M 45% / 34% / 21%; beta 1M 75% / 1% / 24%; systematic 1M 60% / 24% / 16%; sector 1M 29% / 65% / 6%; name 1M 87% / 11% / 2%; crash 1M 88% / 0% / 12%; es 1M 60% / 22% / 17%; var 1M 60% / 22% / 17%; drawdown 1M 60% / 22% / 17%; duration 1M 96% / 2% / 2%; curve 1M 95% / 2% / 3%; credit 1M 66% / 31% / 3%; fx 1M 95% / 3% / 3%; commodity 1M 92% / 3% / 5%; crypto 1M 75% / 25% / 0%; volatility 1M 73% / 10% / 16%; min_variance 3M 57% / 30% / 13%; target_vol 3M 46% / 35% / 20%; beta 3M 79% / 2% / 19%; systematic 3M 59% / 25% / 16%; sector 3M 24% / 68% / 9%; name 3M 90% / 8% / 3%; crash 3M 81% / 0% / 19%; es 3M 64% / 19% / 16%; var 3M 64% / 19% / 16%; drawdown 3M 64% / 19% / 16%; duration 3M 95% / 1% / 4%; curve 3M 90% / 1% / 9%; credit 3M 67% / 28% / 5%; fx 3M 94% / 3% / 3%; commodity 3M 88% / 3% / 8%; crypto 3M 65% / 29% / 6%; volatility 3M 64% / 11% / 25%.

**3. By how much?** Challenger ÷ production gross hedge notional, median [10th–90th percentile] (share of cases larger / smaller): min_variance 1W 1.000 [0.63–1.15] (22% / 43%); target_vol 1W 1.000 [0.48–1.56] (30% / 44%); beta 1W 1.000 [0.96–1.04] (14% / 13%); systematic 1W 1.000 [0.98–1.03] (23% / 20%); sector 1W 1.000 [1.00–1.00] (26% / 44%); name 1W 1.000 [1.00–1.00] (8% / 15%); crash 1W 1.000 [1.00–1.00] (5% / 7%); es 1W 1.000 [0.50–1.25] (22% / 38%); var 1W 1.000 [0.50–1.25] (22% / 38%); drawdown 1W 1.000 [0.50–1.25] (22% / 38%); duration 1W 1.000 [1.00–1.00] (2% / 2%); curve 1W 1.000 [1.00–1.00] (3% / 2%); credit 1W 1.000 [1.00–1.00] (13% / 17%); fx 1W 1.000 [1.00–1.00] (1% / 1%); commodity 1W 1.000 [1.00–1.00] (2% / 3%); crypto 1W 1.000 [1.00–1.00] (9% / 18%); volatility 1W 1.000 [1.00–1.03] (11% / 7%); min_variance 1M 1.000 [0.85–1.11] (23% / 26%); target_vol 1M 1.000 [0.54–1.50] (34% / 41%); beta 1M 1.000 [0.97–1.03] (12% / 13%); systematic 1M 1.000 [0.98–1.02] (20% / 20%); sector 1M 1.000 [1.00–1.00] (30% / 41%); name 1M 1.000 [1.00–1.00] (6% / 7%); crash 1M 1.000 [1.00–1.00] (5% / 7%); es 1M 1.000 [0.80–1.16] (20% / 23%); var 1M 1.000 [0.80–1.16] (20% / 23%); drawdown 1M 1.000 [0.80–1.16] (20% / 23%); duration 1M 1.000 [1.00–1.00] (1% / 2%); curve 1M 1.000 [1.00–1.00] (2% / 3%); credit 1M 1.000 [0.99–1.00] (17% / 17%); fx 1M 1.000 [1.00–1.00] (2% / 3%); commodity 1M 1.000 [1.00–1.00] (2% / 6%); crypto 1M 1.000 [1.00–1.00] (5% / 20%); volatility 1M 1.000 [1.00–1.06] (14% / 9%); min_variance 3M 1.000 [0.92–1.10] (24% / 20%); target_vol 3M 1.000 [0.67–1.59] (36% / 34%); beta 3M 1.000 [0.98–1.00] (9% / 12%); systematic 3M 1.000 [0.98–1.01] (19% / 22%); sector 3M 1.000 [1.00–1.00] (39% / 37%); name 3M 1.000 [1.00–1.00] (5% / 5%); crash 3M 1.000 [1.00–1.16] (11% / 7%); es 3M 1.000 [0.92–1.14] (20% / 17%); var 3M 1.000 [0.92–1.14] (20% / 17%); drawdown 3M 1.000 [0.92–1.14] (20% / 17%); duration 3M 1.000 [1.00–1.00] (2% / 4%); curve 3M 1.000 [1.00–1.00] (4% / 6%); credit 3M 1.000 [1.00–1.01] (20% / 13%); fx 3M 1.000 [1.00–1.00] (3% / 3%); commodity 3M 1.000 [1.00–1.00] (6% / 6%); crypto 3M 1.000 [1.00–1.00] (6% / 29%); volatility 3M 1.000 [1.00–1.14] (17% / 10%).

**4. Does it change product selection?** min_variance 1W: products differ in 20% of cases (equity_index_future → equity_index_future ×666, etf → etf ×133, etf → equity_index_future ×97); target_vol 1W: products differ in 27% of cases (equity_index_future → equity_index_future ×518, equity_index_future → no hedge ×497, no hedge → equity_index_future ×134); beta 1W: products differ in 26% of cases (equity_index_future → equity_index_future ×828, equity_index_future → etf ×129, etf → equity_index_future ×122); systematic 1W: products differ in 18% of cases (equity_index_future → equity_index_future ×550, etf → etf ×231, equity_index_future → etf ×70); sector 1W: products differ in 7% of cases (etf → etf ×210); name 1W: products differ in 3% of cases (equity_index_future → equity_index_future ×39, equity_index_future → etf ×17, etf → etf ×16); crash 1W: products differ in 12% of cases (index_option → inverse_etf ×303, inverse_etf → index_option ×237, index_option → index_option ×33); es 1W: products differ in 25% of cases (equity_index_future → equity_index_future ×650, equity_index_future → no hedge ×314, etf → etf ×154); var 1W: products differ in 25% of cases (equity_index_future → equity_index_future ×650, equity_index_future → no hedge ×314, etf → etf ×154); drawdown 1W: products differ in 25% of cases (equity_index_future → equity_index_future ×650, equity_index_future → no hedge ×314, etf → etf ×154); duration 1W: products differ in 1% of cases (etf → treasury_future ×8, treasury_future → treasury_future ×3, treasury_future → etf ×1); curve 1W: products differ in 1% of cases (treasury_future → treasury_future ×10, etf → treasury_future ×8, treasury_future → etf ×1); credit 1W: products differ in 3% of cases (high_yield → high_yield ×17, high_yield → leveraged_loan ×4); fx 1W: products differ in 2% of cases (fx_forward → currency_trust ×9, fx_spot → currency_trust ×3, currency_trust → fx_forward ×1); commodity 1W: products differ in 4% of cases (commodity_option → commodity_option ×40, commodity_option → etf ×6, etf → etf ×3); volatility 1W: products differ in 11% of cases (index_option → index_option ×30); min_variance 1M: products differ in 14% of cases (equity_index_future → equity_index_future ×227, etf → etf ×47, etf → equity_index_future ×17); target_vol 1M: products differ in 21% of cases (equity_index_future → equity_index_future ×314, equity_index_future → no hedge ×83, no hedge → equity_index_future ×55); beta 1M: products differ in 24% of cases (equity_index_future → equity_index_future ×395, index_option → equity_index_future ×61, equity_index_future → etf ×36); systematic 1M: products differ in 16% of cases (equity_index_future → equity_index_future ×267, etf → etf ×78, commodity_option → commodity_option ×19); sector 1M: products differ in 6% of cases (etf → etf ×84); name 1M: products differ in 2% of cases (equity_index_future → equity_index_future ×14, index_option → index_option ×9, common_stock → equity_index_future ×7); crash 1M: products differ in 12% of cases (index_option → inverse_etf ×136, inverse_etf → index_option ×106, index_option → index_option ×13); es 1M: products differ in 17% of cases (equity_index_future → equity_index_future ×317, etf → etf ×25, equity_index_future → etf ×17); var 1M: products differ in 17% of cases (equity_index_future → equity_index_future ×317, etf → etf ×25, equity_index_future → etf ×17); drawdown 1M: products differ in 17% of cases (equity_index_future → equity_index_future ×317, etf → etf ×25, equity_index_future → etf ×17); duration 1M: products differ in 2% of cases (treasury_future → etf ×11, etf → treasury_future ×4, treasury_future → treasury_future ×3); curve 1M: products differ in 3% of cases (treasury_future → treasury_future ×16, treasury_future → etf ×11, etf → treasury_future ×4); credit 1M: products differ in 3% of cases (high_yield → high_yield ×5, leveraged_loan → high_yield ×4, high_yield → leveraged_loan ×1); fx 1M: products differ in 3% of cases (fx_forward → currency_trust ×3, fx_spot → currency_trust ×2, currency_trust → fx_forward ×1); commodity 1M: products differ in 5% of cases (commodity_option → commodity_option ×14, commodity_option → etf ×9, etf → etf ×2); volatility 1M: products differ in 16% of cases (index_option → index_option ×22); min_variance 3M: products differ in 13% of cases (equity_index_future → equity_index_future ×82, etf → etf ×10, etf → equity_index_future ×3); target_vol 3M: products differ in 20% of cases (equity_index_future → equity_index_future ×102, no hedge → equity_index_future ×27, equity_index_future → no hedge ×20); beta 3M: products differ in 19% of cases (equity_index_future → equity_index_future ×112, index_option → equity_index_future ×11, etf → equity_index_future ×8); systematic 3M: products differ in 16% of cases (equity_index_future → equity_index_future ×95, etf → etf ×26, commodity_option → commodity_option ×9); sector 3M: products differ in 9% of cases (etf → etf ×39); name 3M: products differ in 3% of cases (treasury_future → treasury_future ×7, etf → common_stock ×5, equity_index_future → etf ×3); crash 3M: products differ in 19% of cases (inverse_etf → index_option ×77, index_option → inverse_etf ×52, index_option → index_option ×6); es 3M: products differ in 16% of cases (equity_index_future → equity_index_future ×103, etf → etf ×8, etf → equity_index_future ×6); var 3M: products differ in 16% of cases (equity_index_future → equity_index_future ×103, etf → etf ×8, etf → equity_index_future ×6); drawdown 3M: products differ in 16% of cases (equity_index_future → equity_index_future ×103, etf → etf ×8, etf → equity_index_future ×6); duration 3M: products differ in 4% of cases (treasury_future → etf ×8, etf → treasury_future ×3, treasury_future → treasury_future ×1); curve 3M: products differ in 9% of cases (treasury_future → treasury_future ×15, treasury_future → etf ×8, etf → treasury_future ×3); credit 3M: products differ in 5% of cases (high_yield → leveraged_loan ×3, high_yield → high_yield ×2, leveraged_loan → high_yield ×1); fx 3M: products differ in 3% of cases (fx_forward → currency_trust ×2, currency_trust → fx_forward ×1); commodity 3M: products differ in 8% of cases (commodity_option → commodity_option ×10, etf → commodity_option ×2, commodity_option → etf ×2); crypto 3M: products differ in 6% of cases (crypto_etf → crypto_spot ×1); volatility 3M: products differ in 25% of cases (index_option → index_option ×10, index_option → no hedge ×1) What the product changes did is in the attribution table below.

**5. Does realised variance reduction improve?** Challenger minus production, percentage points: min_variance 1W -0.88%; target_vol 1W +1.58%; beta 1W -1.67%; systematic 1W -0.43%; sector 1W +0.08%; name 1W +0.14%; crash 1W -1.65%; es 1W -0.39%; var 1W -0.39%; drawdown 1W -0.39%; duration 1W -0.67%; curve 1W -0.67%; credit 1W -0.25%; fx 1W -0.37%; commodity 1W -0.02%; crypto 1W +0.00%; volatility 1W +0.70%; min_variance 1M +0.11%; target_vol 1M +1.37%; beta 1M -0.51%; systematic 1M -0.37%; sector 1M -0.05%; name 1M +0.04%; crash 1M -0.31%; es 1M +0.23%; var 1M +0.23%; drawdown 1M +0.23%; duration 1M -0.01%; curve 1M -0.02%; credit 1M +0.12%; fx 1M +0.15%; commodity 1M -0.05%; crypto 1M -0.00%; volatility 1M +0.48%; min_variance 3M -0.05%; target_vol 3M +0.15%; beta 3M -0.35%; systematic 3M -0.39%; sector 3M +0.10%; name 3M +0.05%; crash 3M -1.67%; es 3M +0.01%; var 3M +0.01%; drawdown 3M +0.01%; duration 3M +0.24%; curve 3M +0.22%; credit 3M +0.07%; fx 3M -0.00%; commodity 3M +0.43%; crypto 3M -0.01%; volatility 3M -0.16%.

**6. Does realised ES reduction improve?** Hedged ES95, production minus challenger ($): min_variance 1W -773; target_vol 1W +849; beta 1W -1,622; systematic 1W -351; sector 1W +6; name 1W +30; crash 1W +181; es 1W -221; var 1W -221; drawdown 1W -221; duration 1W -0; curve 1W -0; credit 1W +6; fx 1W +0; commodity 1W +2; crypto 1W -0; volatility 1W +386; min_variance 1M -4; target_vol 1M +161; beta 1M -1,417; systematic 1M -169; sector 1M -37; name 1M +9; crash 1M +57; es 1M +258; var 1M +258; drawdown 1M +258; duration 1M -43; curve 1M -42; credit 1M +6; fx 1M -22; commodity 1M -3; crypto 1M +0; volatility 1M +780; min_variance 3M -1,052; target_vol 3M -5,420; beta 3M -4,590; systematic 3M -279; sector 3M -393; name 3M +13; crash 3M +4,946; es 3M -1,335; var 3M -1,335; drawdown 3M -1,335; duration 3M +0; curve 3M -80; credit 3M +389; fx 3M +0; commodity 3M -67; volatility 3M +0.

**7. Does realised drawdown protection improve?** Mean max drawdown of the hedged book, production minus challenger ($): min_variance 1W -212; target_vol 1W -56; beta 1W -253; systematic 1W -105; sector 1W -19; name 1W +6; crash 1W -45; es 1W -152; var 1W -152; drawdown 1W -152; duration 1W -4; curve 1W -4; credit 1W +1; fx 1W -18; commodity 1W -7; crypto 1W -0; volatility 1W +16; min_variance 1M -10; target_vol 1M +100; beta 1M -190; systematic 1M -128; sector 1M -13; name 1M +3; crash 1M +50; es 1M +29; var 1M +29; drawdown 1M +29; duration 1M +5; curve 1M +6; credit 1M +10; fx 1M +5; commodity 1M -14; crypto 1M -0; volatility 1M +33; min_variance 3M -321; target_vol 3M -152; beta 3M -554; systematic 3M -440; sector 3M +152; name 3M +45; crash 3M +10; es 3M -130; var 3M -130; drawdown 3M -130; duration 3M +19; curve 3M +1; credit 3M +57; fx 3M -17; commodity 3M +64; crypto 3M -67; volatility 3M -162.

**8. Does tail protection improve?** Mean hedged P&L in the unhedged book's worst 5% of windows, challenger minus production ($): min_variance 1W -51; target_vol 1W +978; beta 1W -2,284; systematic 1W -260; sector 1W +12; name 1W -29; crash 1W +195; es 1W -252; var 1W -252; drawdown 1W -252; duration 1W -0; curve 1W -0; credit 1W +7; fx 1W +0; commodity 1W +2; crypto 1W -0; volatility 1W +417; min_variance 1M +1,254; target_vol 1M +1,932; beta 1M -887; systematic 1M -26; sector 1M +4; name 1M +1; crash 1M +5; es 1M +227; var 1M +227; drawdown 1M +227; duration 1M -0; curve 1M -0; credit 1M -12; fx 1M -24; commodity 1M -61; crypto 1M +0; volatility 1M +910; min_variance 3M -3,856; target_vol 3M -2,775; beta 3M -6,221; systematic 3M -354; sector 3M -1; name 3M -0; crash 3M +3,427; es 3M -930; var 3M -930; drawdown 3M -930; duration 3M +0; curve 3M -86; credit 3M +389; fx 3M +0; commodity 3M +0; volatility 3M +0.

**9. Does hedge cost rise or fall?** Mean cost, challenger minus production ($): min_variance 1W -15.6; target_vol 1W -12.7; beta 1W +11.1; systematic 1W +9.5; sector 1W -1.1; name 1W +3.3; crash 1W -32.7; es 1W -7.4; var 1W -7.4; drawdown 1W -7.4; duration 1W +0.2; curve 1W +0.3; credit 1W +0.0; fx 1W +0.1; commodity 1W -1.6; crypto 1W -0.0; volatility 1W +2.7; min_variance 1M -16.3; target_vol 1M -40.1; beta 1M +135.0; systematic 1M +40.5; sector 1M -5.1; name 1M +5.7; crash 1M -165.0; es 1M -16.2; var 1M -16.2; drawdown 1M -16.2; duration 1M +0.4; curve 1M +0.4; credit 1M -0.6; fx 1M +0.1; commodity 1M -8.3; crypto 1M -0.0; volatility 1M -6.2; min_variance 3M -27.3; target_vol 3M -40.7; beta 3M +400.3; systematic 3M +95.3; sector 3M +0.8; name 3M +25.2; crash 3M -1,232.3; es 3M -6.1; var 3M -6.1; drawdown 3M -6.1; duration 3M +1.6; curve 3M +1.8; credit 3M +6.9; fx 3M +1.4; commodity 3M +20.2; crypto 3M -3.5; volatility 3M -67.5.

**10. Is more expected profit sacrificed?** Realised profit sacrificed (−mean hedge P&L), challenger minus production ($): min_variance 1W -111.9; target_vol 1W -28.5; beta 1W +23.0; systematic 1W -16.2; sector 1W +29.4; name 1W +4.4; crash 1W -138.8; es 1W -25.8; var 1W -25.8; drawdown 1W -25.8; duration 1W +4.7; curve 1W +4.3; credit 1W +19.3; fx 1W +12.0; commodity 1W +3.8; crypto 1W +0.1; volatility 1W +48.4; min_variance 1M -82.5; target_vol 1M -51.1; beta 1M -279.6; systematic 1M -134.7; sector 1M -18.2; name 1M -3.4; crash 1M -257.8; es 1M +7.8; var 1M +7.8; drawdown 1M +7.8; duration 1M -8.6; curve 1M -9.0; credit 1M -1.3; fx 1M +0.1; commodity 1M -15.2; crypto 1M -0.2; volatility 1M -52.4; min_variance 3M +518.9; target_vol 3M +879.5; beta 3M +278.8; systematic 3M -48.9; sector 3M -35.5; name 3M +25.0; crash 3M -2,237.4; es 3M +448.1; var 3M +448.1; drawdown 3M +448.1; duration 3M +21.5; curve 3M +39.6; credit 3M -62.4; fx 3M +45.7; commodity 3M +118.6; crypto 3M +68.8; volatility 3M -291.4.

**11. Does total hedge utility improve?** No cell shows a significant utility improvement at λ = 1 after the FDR control. Point estimates for every cell are in the main table.

**12. At which λ values?** ΔU for λ = 0.5 / 1.0 / 2.0 / 5.0 / 10.0: min_variance 1W -227 / -171 / -59 / +277 / +836; target_vol 1W +135 / +149 / +178 / +264 / +406; beta 1W -541 / -552 / -575 / -644 / -759; systematic 1W -184 / -176 / -160 / -111 / -30; sector 1W +8 / -6 / -36 / -124 / -271; name 1W +24 / +21 / +17 / +4 / -19; crash 1W +283 / +353 / +491 / +908 / +1,601; es 1W -201 / -188 / -162 / -85 / +44; var 1W -201 / -188 / -162 / -85 / +44; drawdown 1W -201 / -188 / -162 / -85 / +44; duration 1W +19 / +16 / +12 / -2 / -26; curve 1W +19 / +17 / +13 / -0 / -22; credit 1W +44 / +34 / +15 / -43 / -140; fx 1W +35 / +29 / +17 / -19 / -79; commodity 1W -2 / -4 / -7 / -19 / -38; crypto 1W +0 / +0 / -0 / -0 / -1; volatility 1W +175 / +151 / +102 / -43 / -285; min_variance 1M +312 / +353 / +436 / +683 / +1,096; target_vol 1M +371 / +396 / +447 / +601 / +856; beta 1M -599 / -459 / -180 / +659 / +2,057; systematic 1M -234 / -166 / -32 / +372 / +1,046; sector 1M -65 / -56 / -38 / +17 / +108; name 1M +1 / +2 / +6 / +16 / +33; crash 1M +351 / +480 / +738 / +1,511 / +2,800; es 1M +270 / +266 / +258 / +235 / +196; var 1M +270 / +266 / +258 / +235 / +196; drawdown 1M +270 / +266 / +258 / +235 / +196; duration 1M -37 / -33 / -24 / +2 / +45; curve 1M -36 / -32 / -23 / +4 / +49; credit 1M -1 / +0 / +1 / +5 / +12; fx 1M +2 / +2 / +2 / +2 / +1; commodity 1M -7 / +1 / +16 / +62 / +137; crypto 1M -0 / -0 / +0 / +1 / +1; volatility 1M +66 / +93 / +145 / +302 / +564; min_variance 3M +110 / -150 / -669 / -2,225 / -4,820; target_vol 3M -329 / -769 / -1,649 / -4,287 / -8,685; beta 3M -1,463 / -1,602 / -1,881 / -2,717 / -4,111; systematic 3M -480 / -455 / -406 / -260 / -15; sector 3M +117 / +135 / +170 / +277 / +455; name 3M +101 / +88 / +63 / -12 / -137; crash 3M +7,297 / +8,416 / +10,653 / +17,366 / +28,553; es 3M -1,553 / -1,777 / -2,225 / -3,569 / -5,810; var 3M -1,553 / -1,777 / -2,225 / -3,569 / -5,810; drawdown 3M -1,553 / -1,777 / -2,225 / -3,569 / -5,810; duration 3M -61 / -72 / -93 / -158 / -265; curve 3M -103 / -122 / -162 / -281 / -479; credit 3M +4 / +35 / +97 / +285 / +597; fx 3M -69 / -92 / -138 / -275 / -503; commodity 3M -152 / -211 / -330 / -686 / -1,278; crypto 3M -49 / -84 / -153 / -359 / -703; volatility 3M +56 / +202 / +493 / +1,367 / +2,824.

**13. Which hedge objectives benefit?** None passes G1–G4. Positive point estimates without passing: target_vol 1W, name 1W, crash 1W, duration 1W, curve 1W, credit 1W, fx 1W, crypto 1W, volatility 1W, min_variance 1M, target_vol 1M, name 1M, crash 1M, es 1M, var 1M, drawdown 1M, credit 1M, fx 1M, commodity 1M, volatility 1M, sector 3M, name 3M, crash 3M, credit 3M, volatility 3M.

**14. Which hedge products benefit?** 168 product × objective × horizon breakdowns with enough dates; significant and positive after their own BH: none.

**15. Which portfolio types benefit?** 255 portfolio-type × objective × horizon breakdowns with enough dates; significant and positive after their own BH: none.

**16. Which regimes benefit?** 420 regime × objective × horizon breakdowns with enough dates; significant and positive after their own BH: none. Liquidity stress: no point-in-time liquidity series is available, so it is not tested.

**17. Does breadth-vol outperform the fixed-resizing baseline?** From 2018 (where the resizing multiples are out of sample), ΔU at λ = 1 of B vs C: min_variance 1W -152; target_vol 1W +251; beta 1W -574; systematic 1W -159; sector 1W +8; name 1W +168; crash 1W +1,094; es 1W -436; var 1W -436; drawdown 1W -436; duration 1W +21; curve 1W +22; credit 1W +38; fx 1W +37; commodity 1W +12; crypto 1W +0; volatility 1W +151; min_variance 1M +971; target_vol 1M +675; beta 1M -708; systematic 1M -170; sector 1M -102; name 1M -21; crash 1M +96; es 1M -243; var 1M +1,740; drawdown 1M -243; duration 1M -29; curve 1M -27; credit 1M +26; fx 1M +56; commodity 1M +62; crypto 1M -0; volatility 1M +102; min_variance 3M +92; target_vol 3M -719; beta 3M -2,577; systematic 3M -457; sector 3M +162; name 3M +285; crash 3M +7,848; es 3M -2,718; var 3M -2,718; drawdown 3M -2,718; duration 3M -115; curve 3M -182; credit 3M +296; fx 3M -101; commodity 3M -273; crypto 3M -84; volatility 3M +202.

**18. Are breadth-vol and resizing additive?** From 2018, ΔU(D) against ΔU(B) + ΔU(C), λ = 1: min_variance 1W -175 vs -157; target_vol 1W +231 vs +241; beta 1W -757 vs -932; systematic 1W -280 vs -326; sector 1W +8 vs +8; name 1W -71 vs -66; crash 1W -389 vs -153; es 1W -460 vs -437; var 1W -460 vs -437; drawdown 1W -460 vs -437; duration 1W +21 vs +21; curve 1W +22 vs +22; credit 1W +38 vs +38; fx 1W +31 vs +43; commodity 1W -25 vs -27; crypto 1W +0 vs +0; volatility 1W +151 vs +151; min_variance 1M +784 vs +779; target_vol 1M +639 vs +659; beta 1M -599 vs -490; systematic 1M -72 vs -60; sector 1M -102 vs -102; name 1M +18 vs +21; crash 1M +488 vs +387; es 1M -398 vs -294; var 1M -2,318 vs -2,278; drawdown 1M -398 vs -294; duration 1M -64 vs -66; curve 1M -63 vs -64; credit 1M -27 vs -27; fx 1M -57 vs -56; commodity 1M -45 vs -43; crypto 1M -0 vs -0; volatility 1M +102 vs +102; min_variance 3M +81 vs +49; target_vol 3M -741 vs -764; beta 3M -2,503 vs -2,430; systematic 3M -499 vs -501; sector 3M +162 vs +162; name 3M +83 vs +22; crash 3M +8,174 vs +7,874; es 3M -2,458 vs -2,493; var 3M -2,458 vs -2,493; drawdown 3M -2,458 vs -2,493; duration 3M -115 vs -115; curve 3M -182 vs -182; credit 3M -190 vs -180; fx 3M -166 vs -190; commodity 3M +404 vs +405; crypto 3M -84 vs -84; volatility 3M +202 vs +202.

**19. Does the result survive all eras?** Complete eras with ΔU > 0 (λ = 1): min_variance 1W 1/4; target_vol 1W 3/4; beta 1W 0/4; systematic 1W 0/4; sector 1W 1/4; name 1W 1/4; crash 1W 2/4; es 1W 1/4; var 1W 1/4; drawdown 1W 1/4; duration 1W 3/4; curve 1W 4/4; credit 1W 4/4; fx 1W 2/4; commodity 1W 2/4; crypto 1W 0/1; volatility 1W 2/2; min_variance 1M 2/4; target_vol 1M 3/4; beta 1M 0/4; systematic 1M 1/4; sector 1M 2/4; name 1M 2/4; crash 1M 2/4; es 1M 2/4; var 1M 2/4; drawdown 1M 2/4; duration 1M 1/4; curve 1M 1/4; credit 1M 2/4; fx 1M 2/4; commodity 1M 2/4; crypto 1M 0/1; volatility 1M 1/2; min_variance 3M 1/4; target_vol 3M 1/4; beta 3M 0/4; systematic 3M 1/4; sector 3M 3/4; name 3M 2/4; crash 3M 3/4; es 3M 0/4; var 3M 0/4; drawdown 3M 0/4; duration 3M 1/4; curve 3M 1/4; credit 3M 4/4; fx 3M 1/4; commodity 3M 1/4; crypto 3M 0/1; volatility 3M 2/2.

**20. Should the volatility forecast enter production Hedge?** No. The better forecast does not make the Shaffer Hedge measurably better; breadth stays a research forecast.

**21. Should hedge sizing change?** No change follows from this study: where the forecast changes sizes, the realised outcomes do not justify it (see answers 3 and 11).

**22. Is anything eligible for live shadow or promotion?** No: nothing passes G1–G4, so nothing enters live shadow and nothing is eligible for promotion.

## Attribution (what the changed decisions did)

| Objective | Horizon | Decision change | Cases | Variance reduction Δ (pp) | Hedge P&L Δ | Cost Δ | Basis error Δ | Hedge-size error Δ | Tail Δ |
|---|---|---|---|---|---|---|---|---|---|
| min_variance | 1W | sizing | 2070 | -0.425% | +7.8 | -2.0 | +117.3 | +0.549 | +120 |
| min_variance | 1W | product | 1164 | -0.455% | +104.1 | -13.6 | +371.4 | +48.775 | +288 |
| target_vol | 1W | sizing | 1429 | +0.788% | +43.5 | -1.6 | +220.7 | +0.689 | +3,380 |
| target_vol | 1W | product | 1527 | +0.794% | -15.0 | -11.0 | +482.3 | +0.294 | +739 |
| beta | 1W | sizing | 68 | -0.001% | +1.1 | -0.0 | -35.4 | +0.014 | -71 |
| beta | 1W | product | 1231 | -1.666% | -24.0 | +11.1 | -910.7 | +0.158 | -9,893 |
| systematic | 1W | sizing | 1410 | +0.111% | -8.0 | +1.7 | +15.0 | -0.011 | +128 |
| systematic | 1W | product | 1005 | -0.541% | +24.1 | +7.8 | -451.7 | +0.098 | -1,871 |
| sector | 1W | sizing | 1820 | +0.012% | -0.5 | +0.0 | +2.2 | +0.003 | +8 |
| sector | 1W | product | 210 | +0.066% | -29.0 | -1.1 | -51.9 | +0.635 | -3 |
| name | 1W | sizing | 1125 | +0.010% | +0.4 | -0.1 | +0.3 | -0.007 | +13 |
| name | 1W | product | 164 | +0.132% | -4.8 | +3.4 | -204.1 | +0.427 | -541 |
| crash | 1W | product | 575 | -1.651% | +138.8 | -32.7 | +1,221.9 | -0.205 | -729 |
| es | 1W | sizing | 1406 | -0.085% | +21.4 | -1.3 | +68.0 | +1.189 | -140 |
| es | 1W | product | 1438 | -0.306% | +4.4 | -6.1 | +147.9 | +17.143 | -679 |
| var | 1W | sizing | 1406 | -0.085% | +21.4 | -1.3 | +68.0 | +1.189 | -140 |
| var | 1W | product | 1438 | -0.306% | +4.4 | -6.1 | +147.9 | +17.143 | -679 |
| drawdown | 1W | sizing | 1406 | -0.085% | +21.4 | -1.3 | +68.0 | +1.189 | -140 |
| drawdown | 1W | product | 1438 | -0.306% | +4.4 | -6.1 | +147.9 | +17.143 | -679 |
| duration | 1W | sizing | 76 | +0.001% | +0.1 | -0.0 | +0.5 | +1.615 | -0 |
| duration | 1W | product | 12 | -0.668% | -4.8 | +0.2 | +1,338.8 | -36.789 | — |
| curve | 1W | sizing | 80 | +0.001% | +0.1 | -0.0 | +0.5 | +1.534 | -0 |
| curve | 1W | product | 19 | -0.675% | -4.4 | +0.3 | +983.4 | -23.733 | — |
| credit | 1W | sizing | 207 | +0.104% | -2.8 | -0.0 | +28.5 | +0.061 | +0 |
| credit | 1W | product | 21 | -0.353% | -16.5 | +0.0 | +3,725.7 | -1.997 | +245 |
| fx | 1W | product | 14 | -0.372% | -12.0 | +0.1 | +637.3 | +70.670 | — |
| commodity | 1W | sizing | 20 | -0.005% | -0.1 | -0.0 | +4.6 | -0.014 | -0 |
| commodity | 1W | product | 51 | -0.020% | -3.6 | -1.6 | +67.6 | -0.878 | -459 |
| crypto | 1W | sizing | 33 | +0.000% | -0.1 | -0.0 | -0.1 | -0.041 | -1 |
| volatility | 1W | sizing | 28 | -0.015% | -15.2 | -1.9 | -136.9 | +2.969 | -24 |
| volatility | 1W | product | 30 | +0.714% | -33.1 | +4.6 | -93.6 | +1.315 | +5,423 |
| min_variance | 1M | sizing | 874 | -0.133% | +3.0 | -23.9 | -80.2 | +0.044 | +1,877 |
| min_variance | 1M | product | 385 | +0.245% | +79.5 | +7.6 | -334.9 | -0.355 | +1,123 |
| target_vol | 1M | sizing | 922 | +0.931% | +40.4 | -32.5 | +223.6 | +0.292 | +3,395 |
| target_vol | 1M | product | 575 | +0.440% | +10.7 | -7.6 | +778.8 | +0.443 | +2,216 |
| beta | 1M | sizing | 15 | +0.007% | +0.1 | +0.0 | +64.6 | -0.091 | — |
| beta | 1M | product | 540 | -0.516% | +279.5 | +134.9 | -510.8 | +0.102 | -5,557 |
| systematic | 1M | sizing | 653 | +0.059% | -23.2 | -0.5 | +11.2 | -0.003 | -230 |
| systematic | 1M | product | 426 | -0.430% | +157.9 | +41.0 | -495.1 | +0.074 | -2,977 |
| sector | 1M | sizing | 895 | +0.010% | -0.1 | +0.0 | +1.6 | -0.000 | +6 |
| sector | 1M | product | 84 | -0.060% | +18.2 | -5.2 | +303.6 | +0.082 | +480 |
| name | 1M | sizing | 297 | +0.017% | -0.9 | -0.1 | +5.5 | +0.034 | +8 |
| name | 1M | product | 60 | +0.021% | +4.4 | +5.8 | -116.0 | +0.995 | -1,123 |
| crash | 1M | product | 256 | -0.306% | +257.8 | -165.0 | +319.8 | -0.704 | +4,613 |
| es | 1M | sizing | 595 | +0.020% | -35.9 | -10.6 | +17.8 | +0.271 | +896 |
| es | 1M | product | 472 | +0.213% | +28.1 | -5.6 | -11.9 | +1.617 | +155 |
| var | 1M | sizing | 595 | +0.020% | -35.9 | -10.6 | +17.8 | +0.271 | +896 |
| var | 1M | product | 472 | +0.213% | +28.1 | -5.6 | -11.9 | +1.617 | +155 |
| drawdown | 1M | sizing | 595 | +0.020% | -35.9 | -10.6 | +17.8 | +0.271 | +896 |
| drawdown | 1M | product | 472 | +0.213% | +28.1 | -5.6 | -11.9 | +1.617 | +155 |
| duration | 1M | sizing | 15 | +0.003% | +0.1 | -0.0 | -20.8 | +0.374 | — |
| duration | 1M | product | 18 | -0.012% | +8.5 | +0.4 | +223.3 | +0.470 | — |
| curve | 1M | sizing | 15 | +0.003% | +0.1 | -0.0 | -20.8 | +0.374 | — |
| curve | 1M | product | 31 | -0.018% | +8.9 | +0.5 | +173.1 | +0.463 | -1,961 |
| credit | 1M | sizing | 120 | +0.155% | +0.9 | -0.1 | -13.4 | +0.093 | +9 |
| credit | 1M | product | 10 | -0.032% | +0.4 | -0.5 | -45.9 | +8.057 | — |
| fx | 1M | sizing | 8 | +0.000% | -0.0 | -0.0 | -0.1 | +0.019 | — |
| fx | 1M | product | 8 | +0.154% | -0.0 | +0.1 | -36.1 | -1.426 | — |
| commodity | 1M | sizing | 18 | -0.010% | -7.8 | -0.2 | -71.2 | +3.443 | — |
| commodity | 1M | product | 26 | -0.041% | +23.0 | -8.1 | +48.2 | +4.411 | +1,965 |
| crypto | 1M | sizing | 14 | -0.000% | +0.2 | -0.0 | -0.2 | +0.000 | — |
| volatility | 1M | sizing | 14 | -0.004% | -5.0 | -1.5 | +63.6 | +4.279 | — |
| volatility | 1M | product | 22 | +0.480% | +57.4 | -4.7 | +283.6 | -9.772 | +3,088 |
| min_variance | 3M | sizing | 263 | -0.055% | -305.4 | -20.0 | +81.7 | +0.004 | -641 |
| min_variance | 3M | product | 115 | +0.004% | -213.5 | -7.3 | -10.1 | -0.056 | -16,017 |
| target_vol | 3M | sizing | 308 | +0.084% | -278.6 | -30.6 | +228.4 | -0.032 | -1,898 |
| target_vol | 3M | product | 176 | +0.064% | -601.0 | -10.1 | +631.9 | +0.035 | -18,563 |
| beta | 3M | sizing | 17 | +0.017% | -19.1 | +0.1 | +107.3 | -0.043 | — |
| beta | 3M | product | 140 | -0.370% | -259.6 | +400.2 | -1,543.5 | +0.150 | -32,275 |
| systematic | 3M | sizing | 221 | +0.039% | -47.7 | +0.6 | +15.9 | -0.003 | -729 |
| systematic | 3M | product | 140 | -0.426% | +96.6 | +94.7 | +29.7 | +0.049 | -1,437 |
| sector | 3M | sizing | 306 | +0.004% | -3.0 | +0.2 | +1.0 | -0.000 | -2 |
| sector | 3M | product | 39 | +0.101% | +38.5 | +0.6 | +150.5 | +0.352 | -8,996 |
| name | 3M | sizing | 66 | +0.004% | -1.6 | +0.1 | +12.9 | -0.029 | +3 |
| name | 3M | product | 25 | +0.047% | -23.3 | +25.1 | +32.8 | -0.861 | -1,203 |
| crash | 3M | product | 136 | -1.673% | +2,237.4 | -1,232.3 | +961.7 | +0.839 | +20,564 |
| es | 3M | sizing | 173 | +0.054% | -112.4 | -15.3 | +6.4 | -0.149 | +99 |
| es | 3M | product | 143 | -0.041% | -335.7 | +9.2 | +16.5 | -0.181 | -3,830 |
| var | 3M | sizing | 173 | +0.054% | -112.4 | -15.3 | +6.4 | -0.149 | +99 |
| var | 3M | product | 143 | -0.041% | -335.7 | +9.2 | +16.5 | -0.181 | -3,830 |
| drawdown | 3M | sizing | 173 | +0.054% | -112.4 | -15.3 | +6.4 | -0.149 | +99 |
| drawdown | 3M | product | 143 | -0.041% | -335.7 | +9.2 | +16.5 | -0.181 | -3,830 |
| duration | 3M | sizing | 4 | -0.000% | -0.0 | -0.0 | -0.2 | +0.008 | — |
| duration | 3M | product | 12 | +0.238% | -21.5 | +1.6 | -71.3 | -1.047 | — |
| curve | 3M | sizing | 4 | -0.000% | -0.0 | -0.0 | -0.2 | +0.008 | — |
| curve | 3M | product | 26 | +0.221% | -39.6 | +1.8 | -47.8 | -0.565 | -1,203 |
| credit | 3M | sizing | 34 | +0.019% | +6.0 | -0.8 | -8.5 | +0.110 | +30 |
| credit | 3M | product | 6 | +0.056% | +56.4 | +7.7 | +114.9 | -0.008 | — |
| fx | 3M | sizing | 3 | +0.000% | -0.1 | -0.0 | -0.4 | +0.005 | — |
| fx | 3M | product | 3 | -0.003% | -45.7 | +1.4 | +3.2 | +14.686 | — |
| commodity | 3M | sizing | 6 | -0.001% | -2.0 | +0.0 | -0.0 | -0.585 | — |
| commodity | 3M | product | 15 | +0.431% | -116.6 | +20.2 | -254.1 | +2.383 | — |
| crypto | 3M | sizing | 5 | -0.000% | +1.0 | -0.0 | -0.5 | +0.000 | — |
| crypto | 3M | product | 1 | -0.012% | -69.8 | -3.4 | -52.5 | +4.468 | — |
| volatility | 3M | sizing | 5 | -0.015% | +13.3 | -19.6 | +14.3 | +2.689 | — |
| volatility | 3M | product | 11 | -0.141% | +278.1 | -48.0 | +23.9 | +2.407 | — |

Hedge-size error = mean |ex-post optimal multiple − 1| of the package (lower = closer to the size that would have minimised the window's variance); negative Δ = the challenger's size was closer. Pieces are additive shares of the cell.

## Any better volatility model vs breadth itself (R = regression without breadth)

| Objective | Horizon | ΔU R − A (λ = 1) | ΔU B − R | ΔU with a conservative put skew (+5 vol) | ΔU without option cases | Baselines U: none / half / A / B |
|---|---|---|---|---|---|---|
| min_variance | 1W | -11 | -160 | -198 | -170 | +0 / +6,689 / +10,155 / +9,984 |
| target_vol | 1W | +367 | -217 | +125 | +103 | +0 / +3,839 / +6,595 / +6,745 |
| beta | 1W | -464 | -88 | -407 | -252 | +0 / +3,540 / +6,287 / +5,735 |
| systematic | 1W | -167 | -9 | -140 | -149 | +0 / +5,314 / +9,429 / +9,253 |
| sector | 1W | +3 | -10 | -6 | -6 | +0 / +4,401 / +7,464 / +7,458 |
| name | 1W | +20 | +2 | +22 | -0 | +0 / +4,975 / +9,490 / +9,512 |
| crash | 1W | +201 | +152 | +286 | +0 | +0 / +6,265 / +11,284 / +11,637 |
| es | 1W | +102 | -290 | -202 | -154 | +0 / +7,288 / +13,586 / +13,398 |
| var | 1W | +102 | -290 | -202 | -154 | +0 / +7,288 / +13,586 / +13,398 |
| drawdown | 1W | +102 | -290 | -202 | -154 | +0 / +7,288 / +13,586 / +13,398 |
| duration | 1W | +15 | +1 | +16 | +16 | +0 / +242 / +379 / +395 |
| curve | 1W | +15 | +2 | +17 | +17 | +0 / +200 / +328 / +345 |
| credit | 1W | +34 | +0 | +34 | +34 | +0 / +429 / +762 / +796 |
| fx | 1W | +23 | +6 | +29 | +29 | +0 / +118 / +223 / +252 |
| commodity | 1W | -5 | +1 | +6 | +0 | +0 / +296 / +455 / +452 |
| crypto | 1W | +20 | -20 | +0 | +0 | +0 / +21,428 / +37,830 / +37,830 |
| volatility | 1W | +165 | -14 | +146 | +35 | +0 / +908 / +1,760 / +1,911 |
| min_variance | 1M | +499 | -146 | +343 | -108 | +0 / +15,263 / +22,823 / +23,176 |
| target_vol | 1M | +524 | -127 | +328 | +49 | +0 / +10,406 / +17,899 / +18,295 |
| beta | 1M | -323 | -136 | -307 | -691 | +0 / +5,918 / +10,571 / +10,112 |
| systematic | 1M | -190 | +24 | -112 | -322 | +0 / +11,039 / +19,969 / +19,803 |
| sector | 1M | -40 | -16 | -56 | -56 | +0 / +10,572 / +18,366 / +18,310 |
| name | 1M | -10 | +13 | +2 | -13 | +0 / +10,130 / +19,061 / +19,063 |
| crash | 1M | -16 | +495 | +682 | +0 | +0 / +7,052 / +10,119 / +10,599 |
| es | 1M | +193 | +73 | +221 | -52 | +0 / +17,896 / +32,324 / +32,590 |
| var | 1M | +193 | +73 | +221 | -52 | +0 / +17,896 / +32,324 / +32,590 |
| drawdown | 1M | +193 | +73 | +221 | -52 | +0 / +17,896 / +32,324 / +32,590 |
| duration | 1M | -31 | -2 | -33 | -33 | +0 / +757 / +1,217 / +1,184 |
| curve | 1M | -29 | -3 | -32 | -32 | +0 / +737 / +1,226 / +1,194 |
| credit | 1M | -1 | +1 | +0 | +0 | +0 / +751 / +1,192 / +1,192 |
| fx | 1M | +6 | -4 | +2 | +2 | +0 / +439 / +844 / +846 |
| commodity | 1M | -19 | +19 | +14 | +6 | +0 / +521 / +733 / +734 |
| crypto | 1M | -0 | -0 | -0 | -0 | +0 / +51,402 / +97,338 / +97,338 |
| volatility | 1M | -95 | +188 | +65 | -1 | +0 / +565 / +1,003 / +1,096 |
| min_variance | 3M | -170 | +20 | -158 | -396 | +0 / +17,727 / +22,730 / +22,581 |
| target_vol | 3M | -597 | -172 | -826 | -989 | +0 / +11,224 / +18,107 / +17,338 |
| beta | 3M | -1,446 | -156 | -1,536 | -1,746 | +0 / +5,834 / +9,403 / +7,801 |
| systematic | 3M | -342 | -113 | -470 | -621 | +0 / +14,283 / +24,603 / +24,148 |
| sector | 3M | +160 | -25 | +135 | +135 | +0 / +10,850 / +17,463 / +17,598 |
| name | 3M | +79 | +9 | +79 | +14 | +0 / +13,361 / +25,194 / +25,283 |
| crash | 3M | +7,924 | +492 | +8,817 | +0 | +0 / +3,518 / -1,083 / +7,333 |
| es | 3M | -1,957 | +180 | -1,728 | -1,660 | +0 / +23,023 / +39,113 / +37,336 |
| var | 3M | -1,957 | +180 | -1,728 | -1,660 | +0 / +23,023 / +39,113 / +37,336 |
| drawdown | 3M | -1,957 | +180 | -1,728 | -1,660 | +0 / +23,023 / +39,113 / +37,336 |
| duration | 3M | -36 | -36 | -72 | -72 | +0 / +1,489 / +2,317 / +2,246 |
| curve | 3M | -91 | -32 | -122 | -122 | +0 / +1,565 / +2,495 / +2,373 |
| credit | 3M | +34 | +1 | +35 | +35 | +0 / +1,670 / +2,725 / +2,760 |
| fx | 3M | -82 | -10 | -92 | -92 | +0 / +712 / +1,365 / +1,273 |
| commodity | 3M | -205 | -7 | -237 | +3 | +0 / +445 / +57 / -154 |
| crypto | 3M | -84 | +0 | -84 | -84 | +0 / +79,690 / +148,615 / +148,532 |
| volatility | 3M | +49 | +153 | +198 | +10 | +0 / +47 / -7 / +195 |


## Final decision

| Component | Historical result | Live-shadow eligible? | Production change? | Reason |
|---|---|---|---|---|
| Breadth volatility forecast | better out of sample at 1W, 1M (G1) | no (as a hedge input) | no | a better forecast; it did not produce better realised hedges — kept as research information |
| Breadth hedge sizing | 4 of 9 variance-sensitive cells positive (ES / VaR / drawdown counted once), none significant after FDR | no | no | sizes rarely change and the changes do not pay |
| Breadth product selection | products change in up to 27% of cases; realised effects net to about zero; 16 of 33 hard-target cells positive, none significant | no | no | no significant or stable gain |
| Existing resizing challenger (2018 on) | ΔU median -1 across cells | unchanged (its own live shadow continues) | no | separate experiment; not improved by breadth |
| Breadth + resizing (2018 on) | ΔU median -63 across cells | no | no | the combination adds nothing the parts lack |
| Shaffer Hedge production (hedge-2) | no cell passes G1–G4 | — | no | unchanged: nothing passed the historical gates |
