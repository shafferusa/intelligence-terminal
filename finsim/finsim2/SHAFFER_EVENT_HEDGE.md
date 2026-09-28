# Does the event-calendar volatility forecast improve Shaffer Hedge outcomes? — research (stage 4)

Run 2026-09-27 22:49:33 · 8677.1 s · attribution — · `python -m finsim2 lab --event-hedge`. Research only: hedge-2, its sizing, the Shaffer Score and the frozen benchmark `benchmark-2.1-2026-09-25` (sha256 `ce0f502bbaba5d5a34b7828df1fa286f4855baeb8767d4610a01a188818cdcdc`) are unchanged; nothing is promoted.

## Executive summary

**Evidence is mixed / regime-specific and is not sufficient for promotion.**

- The event-enhanced forecast of the market factor's volatility is better out of sample: squared log error 1W +0.4% against the same model without event (t -0.9), -40.9% against production; 1M +0.6% against the same model without event (t -3.0), -32.7% against production; 3M +0.2% against the same model without event (t -1.6), -6.8% against production. G1 passes at no horizon and fails at 1W, 1M, 3M.
- The better forecast does not make the Shaffer Hedge measurably better: of 50 objective × horizon tests of realised utility (λ = 1), 0 survive the Benjamini-Hochberg control; no cell passes G1–G4, so nothing is eligible for live shadow and hedge-2 stays unchanged.

Arms, as always kept apart: **production** = hedge-2 (252-day covariance) · **no-event vol** = the new log-volatility regression (63d / 21d realised volatility, VIX) without event · **event vol** = the same regression with the two market-wide calendar features (scheduled macro releases / FOMC in the next five sessions — the market factor has no earnings window) (`hedge-2-event-vol-exp`). ΔU(event − production) is the pre-registered test; ΔU(event − no-event) isolates event itself. Utility U = risk reduction − λ·profit sacrificed − hedge cost, $ per $1M book over the horizon (ES95 for crash / ES / VaR / drawdown, standard deviation otherwise); paired on identical cases; moving-block bootstrap over dates (every book on a date is one cluster); t = ΔU ÷ bootstrap SE. Protocol and gates were committed before any full result; the only change during the run was runtime (checkpoints, resume, saved cases — the first launch was stopped 3 minutes into the replay, before any date finished).

## Gates (as committed)

| Horizon | G1 forecast validity | G2 realised utility (FDR) | G3 era stability | G4 no degradation | G5 live shadow |
|---|---|---|---|---|---|
| 1W | FAIL | 0 of 17 PASS | 4 of 17 PASS | 13 of 17 PASS | NOT YET TESTABLE |
| 1M | FAIL | 0 of 17 PASS | 4 of 17 PASS | 15 of 17 PASS | NOT YET TESTABLE |
| 3M | FAIL | 0 of 17 PASS | 3 of 17 PASS | 12 of 17 PASS | NOT YET TESTABLE |

G5 cannot pass from history: a cell passing G1–G4 would be LIVE SHADOW ELIGIBLE, never production-eligible. No cell reached that point.

## The main result table (λ = 1; $ per $1M book over the horizon)

| Objective | Horizon | λ | Production U | No-event U | Event U | ΔU event−production | ΔU event−no-event | Variance Δ | ES Δ | Drawdown Δ | Cost Δ | Profit sacrificed Δ | Size ratio | Product changed % | Positive eras | clustered t | FDR | Gate status |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| min_variance | 1W | 1 | +10,155 | — | +10,147 | -8 | — | -0.64% | -360 | -155 | -14 | -111 | — | 18% | 2/4 | — | ✗ (raw p 0.571) | NO HEDGE IMPROVEMENT |
| target_vol | 1W | 1 | +6,595 | — | +6,951 | +356 | — | +1.37% | +1,560 | +31 | -11 | -139 | — | 25% | 3/4 | — | ✗ (raw p 0.127) | NO HEDGE IMPROVEMENT |
| beta | 1W | 1 | +6,287 | — | +5,834 | -454 | — | -1.17% | -1,548 | -146 | +11 | -56 | — | 23% | 1/4 | — | ✗ (raw p 0.958) | NO HEDGE IMPROVEMENT |
| systematic | 1W | 1 | +9,429 | — | +9,274 | -154 | — | -0.29% | -346 | -64 | +9 | -37 | — | 16% | 0/4 | — | ✗ (raw p 0.993) | NO HEDGE IMPROVEMENT |
| sector | 1W | 1 | +7,464 | — | +7,466 | +2 | — | +0.07% | +15 | -8 | -1 | +15 | — | 6% | 1/4 | — | ✗ (raw p 0.496) | NO HEDGE IMPROVEMENT |
| name | 1W | 1 | +9,490 | — | +9,514 | +23 | — | +0.12% | +11 | +5 | +4 | -2 | — | 3% | 1/4 | — | ✗ (raw p 0.130) | NO HEDGE IMPROVEMENT |
| crash | 1W | 1 | +11,284 | — | +11,387 | +103 | — | -1.17% | -3 | -7 | -36 | -70 | — | 12% | 2/4 | — | ✗ (raw p 0.441) | NO HEDGE IMPROVEMENT |
| es | 1W | 1 | +13,586 | — | +13,663 | +77 | — | -0.31% | +18 | -93 | -6 | -53 | — | 23% | 1/4 | — | ✗ (raw p 0.456) | NO HEDGE IMPROVEMENT |
| var | 1W | 1 | +13,586 | — | +13,663 | +77 | — | -0.31% | +18 | -93 | -6 | -53 | — | 23% | 1/4 | — | ✗ (raw p 0.456) | NO HEDGE IMPROVEMENT |
| drawdown | 1W | 1 | +13,586 | — | +13,663 | +77 | — | -0.31% | +18 | -93 | -6 | -53 | — | 23% | 1/4 | — | ✗ (raw p 0.456) | NO HEDGE IMPROVEMENT |
| duration | 1W | 1 | +379 | — | +395 | +16 | — | -0.69% | +0 | -5 | +0 | +5 | — | 1% | 4/4 | — | ✗ (raw p 0.097) | NO HEDGE IMPROVEMENT |
| curve | 1W | 1 | +328 | — | +344 | +16 | — | -0.69% | +0 | -5 | +0 | +6 | — | 1% | 3/4 | — | ✗ (raw p 0.040) | NO HEDGE IMPROVEMENT |
| credit | 1W | 1 | +762 | — | +796 | +33 | — | -0.28% | +8 | -0 | +0 | +18 | — | 3% | 4/4 | — | ✗ (raw p 0.030) | NO HEDGE IMPROVEMENT |
| fx | 1W | 1 | +223 | — | +245 | +22 | — | -0.25% | +0 | -15 | +0 | +11 | — | 2% | 2/4 | — | ✗ (raw p 0.232) | NO HEDGE IMPROVEMENT |
| commodity | 1W | 1 | +455 | — | +451 | -5 | — | -0.02% | +2 | -8 | -2 | +5 | — | 5% | 1/4 | — | ✗ (raw p 0.703) | NO HEDGE IMPROVEMENT |
| crypto | 1W | 1 | +37,830 | — | +37,849 | +20 | — | +0.00% | -1 | +10 | -1 | -30 | — | 1% | 1/1 | — | ✗ (raw p 0.229) | NO HEDGE IMPROVEMENT |
| volatility | 1W | 1 | +1,760 | — | +1,948 | +188 | — | +0.64% | +610 | +11 | -1 | +41 | — | 12% | 2/2 | — | ✗ (raw p 0.152) | NO HEDGE IMPROVEMENT |
| min_variance | 1M | 1 | +22,823 | — | +23,304 | +482 | — | +0.15% | +373 | -36 | -23 | -51 | — | 13% | 2/4 | — | ✗ (raw p 0.035) | NO HEDGE IMPROVEMENT |
| target_vol | 1M | 1 | +17,899 | — | +18,438 | +539 | — | +1.50% | +356 | +243 | -40 | -127 | — | 21% | 3/4 | — | ✗ (raw p 0.075) | NO HEDGE IMPROVEMENT |
| beta | 1M | 1 | +10,571 | — | +10,255 | -316 | — | -0.40% | -873 | -123 | +137 | -333 | — | 23% | 0/4 | — | ✗ (raw p 0.868) | NO HEDGE IMPROVEMENT |
| systematic | 1M | 1 | +19,969 | — | +19,782 | -187 | — | -0.34% | -368 | -114 | +45 | -124 | — | 15% | 1/4 | — | ✗ (raw p 0.870) | NO HEDGE IMPROVEMENT |
| sector | 1M | 1 | +18,366 | — | +18,323 | -42 | — | -0.04% | -35 | +2 | -3 | -32 | — | 6% | 3/4 | — | ✗ (raw p 0.733) | NO HEDGE IMPROVEMENT |
| name | 1M | 1 | +19,061 | — | +19,054 | -7 | — | +0.03% | +9 | -4 | +5 | +1 | — | 2% | 1/4 | — | ✗ (raw p 0.658) | NO HEDGE IMPROVEMENT |
| crash | 1M | 1 | +10,119 | — | +10,114 | -5 | — | -0.45% | -285 | -70 | -163 | -117 | — | 11% | 2/4 | — | ✗ (raw p 0.541) | NO HEDGE IMPROVEMENT |
| es | 1M | 1 | +32,324 | — | +32,522 | +198 | — | +0.24% | +206 | +37 | -18 | +27 | — | 17% | 2/4 | — | ✗ (raw p 0.274) | NO HEDGE IMPROVEMENT |
| var | 1M | 1 | +32,324 | — | +32,522 | +198 | — | +0.24% | +206 | +37 | -18 | +27 | — | 17% | 2/4 | — | ✗ (raw p 0.274) | NO HEDGE IMPROVEMENT |
| drawdown | 1M | 1 | +32,324 | — | +32,522 | +198 | — | +0.24% | +206 | +37 | -18 | +27 | — | 17% | 2/4 | — | ✗ (raw p 0.274) | NO HEDGE IMPROVEMENT |
| duration | 1M | 1 | +1,217 | — | +1,186 | -31 | — | -0.01% | -43 | +5 | +0 | -8 | — | 2% | 2/4 | — | ✗ (raw p 0.873) | NO HEDGE IMPROVEMENT |
| curve | 1M | 1 | +1,226 | — | +1,197 | -29 | — | -0.02% | -42 | +7 | +0 | -10 | — | 3% | 3/4 | — | ✗ (raw p 0.850) | NO HEDGE IMPROVEMENT |
| credit | 1M | 1 | +1,192 | — | +1,191 | -1 | — | +0.13% | +1 | +11 | -0 | -0 | — | 2% | 3/4 | — | ✗ (raw p 0.569) | NO HEDGE IMPROVEMENT |
| fx | 1M | 1 | +844 | — | +850 | +6 | — | +0.18% | -22 | +7 | +0 | +1 | — | 3% | 2/4 | — | ✗ (raw p 0.287) | NO HEDGE IMPROVEMENT |
| commodity | 1M | 1 | +733 | — | +714 | -19 | — | -0.05% | -69 | -23 | -8 | -1 | — | 4% | 1/4 | — | ✗ (raw p 0.763) | NO HEDGE IMPROVEMENT |
| crypto | 1M | 1 | +97,338 | — | +97,338 | -0 | — | -0.00% | +0 | +0 | -0 | -0 | — | 0% | 0/1 | — | ✗ (raw p 0.469) | INSUFFICIENT DATA |
| volatility | 1M | 1 | +1,003 | — | +906 | -98 | — | +0.15% | +339 | +24 | +8 | +10 | — | 17% | 1/2 | — | ✗ (raw p 0.728) | NO HEDGE IMPROVEMENT |
| min_variance | 3M | 1 | +22,730 | — | +22,556 | -174 | — | -0.04% | -1,328 | -273 | -31 | +376 | — | 13% | 1/4 | — | ✗ (raw p 0.603) | NO HEDGE IMPROVEMENT |
| target_vol | 3M | 1 | +18,107 | — | +17,570 | -537 | — | +0.38% | -3,922 | +46 | -38 | +954 | — | 20% | 2/4 | — | ✗ (raw p 0.771) | NO HEDGE IMPROVEMENT |
| beta | 3M | 1 | +9,403 | — | +7,957 | -1,447 | — | -0.22% | -4,590 | -389 | +402 | +189 | — | 19% | 0/4 | — | ✗ (raw p 0.990) | NO HEDGE IMPROVEMENT |
| systematic | 3M | 1 | +24,603 | — | +24,257 | -345 | — | -0.35% | -60 | -304 | +105 | -125 | — | 15% | 2/4 | — | ✗ (raw p 0.928) | NO HEDGE IMPROVEMENT |
| sector | 3M | 1 | +17,463 | — | +17,624 | +161 | — | +0.12% | -393 | +129 | -0 | -16 | — | 8% | 3/4 | — | ✗ (raw p 0.267) | NO HEDGE IMPROVEMENT |
| name | 3M | 1 | +25,194 | — | +25,274 | +79 | — | +0.04% | +13 | +43 | +26 | +25 | — | 3% | 2/4 | — | ✗ (raw p 0.117) | NO HEDGE IMPROVEMENT |
| crash | 3M | 1 | -1,083 | — | +6,752 | +7,835 | — | -1.90% | +4,946 | -236 | -1,185 | -1,704 | — | 17% | 3/4 | — | ✗ (raw p 0.002) | NO HEDGE IMPROVEMENT |
| es | 3M | 1 | +39,113 | — | +37,133 | -1,980 | — | -0.14% | -1,530 | -194 | -12 | +463 | — | 15% | 0/4 | — | ✗ (raw p 1.000) | NO HEDGE IMPROVEMENT |
| var | 3M | 1 | +39,113 | — | +37,133 | -1,980 | — | -0.14% | -1,530 | -194 | -12 | +463 | — | 15% | 0/4 | — | ✗ (raw p 1.000) | NO HEDGE IMPROVEMENT |
| drawdown | 3M | 1 | +39,113 | — | +37,133 | -1,980 | — | -0.14% | -1,530 | -194 | -12 | +463 | — | 15% | 0/4 | — | ✗ (raw p 1.000) | NO HEDGE IMPROVEMENT |
| duration | 3M | 1 | +2,317 | — | +2,282 | -36 | — | +0.28% | +0 | +35 | +1 | +9 | — | 4% | 2/4 | — | ✗ (raw p 0.733) | NO HEDGE IMPROVEMENT |
| curve | 3M | 1 | +2,495 | — | +2,404 | -91 | — | +0.26% | -80 | +17 | +1 | +35 | — | 8% | 2/4 | — | ✗ (raw p 0.905) | NO HEDGE IMPROVEMENT |
| credit | 3M | 1 | +2,725 | — | +2,759 | +34 | — | +0.09% | +389 | +58 | +7 | -57 | — | 5% | 3/4 | — | ✗ (raw p 0.284) | NO HEDGE IMPROVEMENT |
| fx | 3M | 1 | +1,365 | — | +1,283 | -82 | — | +0.20% | +0 | -11 | +1 | +40 | — | 4% | 2/4 | — | ✗ (raw p 0.723) | NO HEDGE IMPROVEMENT |
| commodity | 3M | 1 | +57 | — | -148 | -205 | — | +0.40% | -67 | +43 | +24 | +125 | — | 8% | 1/4 | — | ✗ (raw p 0.850) | NO HEDGE IMPROVEMENT |
| crypto | 3M | 1 | +148,615 | — | +148,532 | -84 | — | -0.01% | — | -67 | -3 | +69 | — | 6% | 0/1 | — | ✗ (raw p —) | INSUFFICIENT DATA |
| volatility | 3M | 1 | -7 | — | +42 | +49 | — | -0.05% | -381 | -59 | -39 | -43 | — | 23% | 1/2 | — | ✗ (raw p 0.292) | INSUFFICIENT DATA |

ES Δ and Drawdown Δ: production minus event (positive = the event hedge lost less). Cost Δ and Profit sacrificed Δ: event minus production (positive = costs / gives up more). Variance Δ: change in realised variance reduction. Size ratio: median event ÷ production size in the same risk unit (quantity of the same product; market beta-dollars when the product changed and the primary risk is the market). ES, VaR and drawdown take the same engine path and are all scored on ES95, so their rows are identical.

## ΔU at every λ (event − production / event − no-event)

| Objective | Horizon | λ = 0.5 | λ = 1 | λ = 2 | λ = 5 | λ = 10 |
|---|---|---|---|---|---|---|
| min_variance | 1W | — / — | — / — | — / — | — / — | — / — |
| target_vol | 1W | — / — | — / — | — / — | — / — | — / — |
| beta | 1W | — / — | — / — | — / — | — / — | — / — |
| systematic | 1W | — / — | — / — | — / — | — / — | — / — |
| sector | 1W | — / — | — / — | — / — | — / — | — / — |
| name | 1W | — / — | — / — | — / — | — / — | — / — |
| crash | 1W | — / — | — / — | — / — | — / — | — / — |
| es | 1W | — / — | — / — | — / — | — / — | — / — |
| var | 1W | — / — | — / — | — / — | — / — | — / — |
| drawdown | 1W | — / — | — / — | — / — | — / — | — / — |
| duration | 1W | — / — | — / — | — / — | — / — | — / — |
| curve | 1W | — / — | — / — | — / — | — / — | — / — |
| credit | 1W | — / — | — / — | — / — | — / — | — / — |
| fx | 1W | — / — | — / — | — / — | — / — | — / — |
| commodity | 1W | — / — | — / — | — / — | — / — | — / — |
| crypto | 1W | — / — | — / — | — / — | — / — | — / — |
| volatility | 1W | — / — | — / — | — / — | — / — | — / — |
| min_variance | 1M | — / — | — / — | — / — | — / — | — / — |
| target_vol | 1M | — / — | — / — | — / — | — / — | — / — |
| beta | 1M | — / — | — / — | — / — | — / — | — / — |
| systematic | 1M | — / — | — / — | — / — | — / — | — / — |
| sector | 1M | — / — | — / — | — / — | — / — | — / — |
| name | 1M | — / — | — / — | — / — | — / — | — / — |
| crash | 1M | — / — | — / — | — / — | — / — | — / — |
| es | 1M | — / — | — / — | — / — | — / — | — / — |
| var | 1M | — / — | — / — | — / — | — / — | — / — |
| drawdown | 1M | — / — | — / — | — / — | — / — | — / — |
| duration | 1M | — / — | — / — | — / — | — / — | — / — |
| curve | 1M | — / — | — / — | — / — | — / — | — / — |
| credit | 1M | — / — | — / — | — / — | — / — | — / — |
| fx | 1M | — / — | — / — | — / — | — / — | — / — |
| commodity | 1M | — / — | — / — | — / — | — / — | — / — |
| crypto | 1M | — / — | — / — | — / — | — / — | — / — |
| volatility | 1M | — / — | — / — | — / — | — / — | — / — |
| min_variance | 3M | — / — | — / — | — / — | — / — | — / — |
| target_vol | 3M | — / — | — / — | — / — | — / — | — / — |
| beta | 3M | — / — | — / — | — / — | — / — | — / — |
| systematic | 3M | — / — | — / — | — / — | — / — | — / — |
| sector | 3M | — / — | — / — | — / — | — / — | — / — |
| name | 3M | — / — | — / — | — / — | — / — | — / — |
| crash | 3M | — / — | — / — | — / — | — / — | — / — |
| es | 3M | — / — | — / — | — / — | — / — | — / — |
| var | 3M | — / — | — / — | — / — | — / — | — / — |
| drawdown | 3M | — / — | — / — | — / — | — / — | — / — |
| duration | 3M | — / — | — / — | — / — | — / — | — / — |
| curve | 3M | — / — | — / — | — / — | — / — | — / — |
| credit | 3M | — / — | — / — | — / — | — / — | — / — |
| fx | 3M | — / — | — / — | — / — | — / — | — / — |
| commodity | 3M | — / — | — / — | — / — | — / — | — / — |
| crypto | 3M | — / — | — / — | — / — | — / — | — / — |
| volatility | 3M | — / — | — / — | — / — | — / — | — / — |

## Paired statistics (λ = 1, event − production)

| Objective | Horizon | Mean ΔU | Median per-case share (changed cases) | 95% CI | clustered t | % positive (changed cases) | Changed cases | Positive eras |
|---|---|---|---|---|---|---|---|---|
| min_variance | 1W | -8 | — | [-234, +185] | — | 0% | 0 | 2/4 |
| target_vol | 1W | +356 | — | [-198, +898] | — | 0% | 0 | 3/4 |
| beta | 1W | -454 | — | [-1,136, +19] | — | 0% | 0 | 1/4 |
| systematic | 1W | -154 | — | [-332, -15] | — | 0% | 0 | 0/4 |
| sector | 1W | +2 | — | [-32, +28] | — | 0% | 0 | 1/4 |
| name | 1W | +23 | — | [-14, +61] | — | 0% | 0 | 1/4 |
| crash | 1W | +103 | — | [-952, +1,285] | — | 0% | 0 | 2/4 |
| es | 1W | +77 | — | [-668, +626] | — | 0% | 0 | 1/4 |
| var | 1W | +77 | — | [-668, +626] | — | 0% | 0 | 1/4 |
| drawdown | 1W | +77 | — | [-668, +626] | — | 0% | 0 | 1/4 |
| duration | 1W | +16 | — | [-0, +33] | — | 0% | 0 | 4/4 |
| curve | 1W | +16 | — | [-0, +33] | — | 0% | 0 | 3/4 |
| credit | 1W | +33 | — | [-0, +66] | — | 0% | 0 | 4/4 |
| fx | 1W | +22 | — | [-5, +56] | — | 0% | 0 | 2/4 |
| commodity | 1W | -5 | — | [-25, +10] | — | 0% | 0 | 1/4 |
| crypto | 1W | +20 | — | [-0, +63] | — | 0% | 0 | 1/1 |
| volatility | 1W | +188 | — | [-119, +565] | — | 0% | 0 | 2/2 |
| min_variance | 1M | +482 | — | [-13, +1,406] | — | 0% | 0 | 2/4 |
| target_vol | 1M | +539 | — | [-94, +1,382] | — | 0% | 0 | 3/4 |
| beta | 1M | -316 | — | [-1,231, +259] | — | 0% | 0 | 0/4 |
| systematic | 1M | -187 | — | [-535, +127] | — | 0% | 0 | 1/4 |
| sector | 1M | -42 | — | [-227, +91] | — | 0% | 0 | 3/4 |
| name | 1M | -7 | — | [-44, +26] | — | 0% | 0 | 1/4 |
| crash | 1M | -5 | — | [-654, +761] | — | 0% | 0 | 2/4 |
| es | 1M | +198 | — | [-643, +1,557] | — | 0% | 0 | 2/4 |
| var | 1M | +198 | — | [-643, +1,557] | — | 0% | 0 | 2/4 |
| drawdown | 1M | +198 | — | [-643, +1,557] | — | 0% | 0 | 2/4 |
| duration | 1M | -31 | — | [-103, +5] | — | 0% | 0 | 2/4 |
| curve | 1M | -29 | — | [-94, +10] | — | 0% | 0 | 3/4 |
| credit | 1M | -1 | — | [-11, +10] | — | 0% | 0 | 3/4 |
| fx | 1M | +6 | — | [-11, +19] | — | 0% | 0 | 2/4 |
| commodity | 1M | -19 | — | [-71, +19] | — | 0% | 0 | 1/4 |
| crypto | 1M | -0 | — | [-0, +0] | — | 0% | 0 | 0/1 |
| volatility | 1M | -98 | — | [-497, +191] | — | 0% | 0 | 1/2 |
| min_variance | 3M | -174 | — | [-805, +611] | — | 0% | 0 | 1/4 |
| target_vol | 3M | -537 | — | [-2,300, +1,371] | — | 0% | 0 | 2/4 |
| beta | 3M | -1,447 | — | [-3,656, -134] | — | 0% | 0 | 0/4 |
| systematic | 3M | -345 | — | [-838, +84] | — | 0% | 0 | 2/4 |
| sector | 3M | +161 | — | [-230, +629] | — | 0% | 0 | 3/4 |
| name | 3M | +79 | — | [-26, +225] | — | 0% | 0 | 2/4 |
| crash | 3M | +7,835 | — | [+1,006, +14,707] | — | 0% | 0 | 3/4 |
| es | 3M | -1,980 | — | [-3,032, -654] | — | 0% | 0 | 0/4 |
| var | 3M | -1,980 | — | [-3,032, -654] | — | 0% | 0 | 0/4 |
| drawdown | 3M | -1,980 | — | [-3,032, -654] | — | 0% | 0 | 0/4 |
| duration | 3M | -36 | — | [-166, +62] | — | 0% | 0 | 2/4 |
| curve | 3M | -91 | — | [-237, +32] | — | 0% | 0 | 2/4 |
| credit | 3M | +34 | — | [-55, +252] | — | 0% | 0 | 3/4 |
| fx | 3M | -82 | — | [-394, +32] | — | 0% | 0 | 2/4 |
| commodity | 3M | -205 | — | [-673, +75] | — | 0% | 0 | 1/4 |
| crypto | 3M | -84 | — | — | — | 0% | 0 | 0/1 |
| volatility | 3M | +49 | — | [-544, +843] | — | 0% | 0 | 1/2 |

Per-case shares decompose ΔU exactly (they sum to it): each case's share of the hedged-risk measure, profit and cost. Cases with an identical decision still carry small shares under the standard-deviation measure (the distribution's mean and spread differ), so the share of positive cases is reported among cases whose decision changed.

## Variance-sensitive vs hard-target objectives

**Variance-sensitive (minimum variance, target volatility, VaR, ES, drawdown).** Σ weighs risk against cost directly here, so the forecast can change sizes.

| Objective | Horizon | Decision changed | of which sizing / product | Legs changed | Size change when only the size changed: median / p90 | ΔU from sizing changes | ΔU from product changes | Size ratio p25–p75 |
|---|---|---|---|---|---|---|---|---|
| min_variance | 1W | 54% | 36% / 18% | 0% | — | — | — | — |
| target_vol | 1W | 50% | 26% / 25% | 0% | — | — | — | — |
| es | 1W | 48% | 25% / 23% | 0% | — | — | — | — |
| var | 1W | 48% | 25% / 23% | 0% | — | — | — | — |
| drawdown | 1W | 48% | 25% / 23% | 0% | — | — | — | — |
| min_variance | 1M | 46% | 32% / 13% | 0% | — | — | — | — |
| target_vol | 1M | 54% | 33% / 21% | 0% | — | — | — | — |
| es | 1M | 38% | 22% / 17% | 0% | — | — | — | — |
| var | 1M | 38% | 22% / 17% | 0% | — | — | — | — |
| drawdown | 1M | 38% | 22% / 17% | 0% | — | — | — | — |
| min_variance | 3M | 43% | 30% / 13% | 0% | — | — | — | — |
| target_vol | 3M | 54% | 35% / 20% | 0% | — | — | — | — |
| es | 3M | 35% | 20% / 15% | 0% | — | — | — | — |
| var | 3M | 35% | 20% / 15% | 0% | — | — | — | — |
| drawdown | 3M | 35% | 20% / 15% | 0% | — | — | — | — |

**Hard-target (beta, systematic, sector, name, duration, curve, credit, FX, commodity, crypto, volatility).** The target exposure is pinned (weight 10⁴), so sizes follow exposures and the forecast can only change product and cost trade-offs.

| Objective | Horizon | Decision changed | of which sizing / product | Legs changed | Size change when only the size changed: median / p90 | ΔU from sizing changes | ΔU from product changes | Size ratio p25–p75 |
|---|---|---|---|---|---|---|---|---|
| beta | 1W | 24% | 1% / 23% | 0% | — | — | — | — |
| systematic | 1W | 40% | 24% / 16% | 0% | — | — | — | — |
| sector | 1W | 68% | 62% / 6% | 0% | — | — | — | — |
| name | 1W | 21% | 18% / 3% | 0% | — | — | — | — |
| duration | 1W | 5% | 4% / 1% | 0% | — | — | — | — |
| curve | 1W | 5% | 4% / 1% | 0% | — | — | — | — |
| credit | 1W | 30% | 27% / 3% | 0% | — | — | — | — |
| fx | 1W | 2% | 0% / 2% | 0% | — | — | — | — |
| commodity | 1W | 6% | 2% / 5% | 0% | — | — | — | — |
| crypto | 1W | 25% | 24% / 1% | 0% | — | — | — | — |
| volatility | 1W | 21% | 8% / 12% | 0% | — | — | — | — |
| beta | 1M | 24% | 1% / 23% | 0% | — | — | — | — |
| systematic | 1M | 40% | 24% / 15% | 0% | — | — | — | — |
| sector | 1M | 71% | 65% / 6% | 0% | — | — | — | — |
| name | 1M | 12% | 10% / 2% | 0% | — | — | — | — |
| duration | 1M | 4% | 2% / 2% | 0% | — | — | — | — |
| curve | 1M | 5% | 2% / 3% | 0% | — | — | — | — |
| credit | 1M | 31% | 29% / 2% | 0% | — | — | — | — |
| fx | 1M | 5% | 3% / 3% | 0% | — | — | — | — |
| commodity | 1M | 7% | 3% / 4% | 0% | — | — | — | — |
| crypto | 1M | 24% | 24% / 0% | 0% | — | — | — | — |
| volatility | 1M | 27% | 10% / 17% | 0% | — | — | — | — |
| beta | 3M | 20% | 1% / 19% | 0% | — | — | — | — |
| systematic | 3M | 39% | 24% / 15% | 0% | — | — | — | — |
| sector | 3M | 74% | 67% / 8% | 0% | — | — | — | — |
| name | 3M | 10% | 7% / 3% | 0% | — | — | — | — |
| duration | 3M | 5% | 1% / 4% | 0% | — | — | — | — |
| curve | 3M | 10% | 1% / 8% | 0% | — | — | — | — |
| credit | 3M | 35% | 30% / 5% | 0% | — | — | — | — |
| fx | 3M | 6% | 2% / 4% | 0% | — | — | — | — |
| commodity | 3M | 10% | 3% / 8% | 0% | — | — | — | — |
| crypto | 3M | 35% | 29% / 6% | 0% | — | — | — | — |
| volatility | 3M | 39% | 16% / 23% | 0% | — | — | — | — |

What the data say about the expectation. Hard-target objectives change product as expected, and where they also "change size" the change is tiny (median column above): the pin on the target is a weight of 10⁴, not a hard constraint, and the product's other exposures (a sector ETF's or a high-yield ETF's market beta) are penalised with Σ, so a different market variance moves the optimum by a few shares; the systematic objective moves more because it matches the market and sector factors one by one, each weighted by its own variance, and a different market variance changes that balance. Variance-sensitive objectives change size more often and by more, but their realised utility difference also comes mostly from product and leg choices (ΔU from product changes) — Σ also ranks products by risk against cost, so a different volatility level changes which index future, ETF or number of legs the optimiser prefers.

## Why utility changed (attribution, λ = 1, event − production)

| Objective | Horizon | ΔU | = risk reduction Δ | + profit Δ | + cost Δ | Basis error Δ | Tail Δ (unhedged worst 5%) | Hedge-size error Δ |
|---|---|---|---|---|---|---|---|---|
| min_variance | 1W | -8 | — | — | — | — | — | — |
| target_vol | 1W | +356 | — | — | — | — | — | — |
| beta | 1W | -454 | — | — | — | — | — | — |
| systematic | 1W | -154 | — | — | — | — | — | — |
| sector | 1W | +2 | — | — | — | — | — | — |
| name | 1W | +23 | — | — | — | — | — | — |
| crash | 1W | +103 | — | — | — | — | — | — |
| es | 1W | +77 | — | — | — | — | — | — |
| var | 1W | +77 | — | — | — | — | — | — |
| drawdown | 1W | +77 | — | — | — | — | — | — |
| duration | 1W | +16 | — | — | — | — | — | — |
| curve | 1W | +16 | — | — | — | — | — | — |
| credit | 1W | +33 | — | — | — | — | — | — |
| fx | 1W | +22 | — | — | — | — | — | — |
| commodity | 1W | -5 | — | — | — | — | — | — |
| crypto | 1W | +20 | — | — | — | — | — | — |
| volatility | 1W | +188 | — | — | — | — | — | — |
| min_variance | 1M | +482 | — | — | — | — | — | — |
| target_vol | 1M | +539 | — | — | — | — | — | — |
| beta | 1M | -316 | — | — | — | — | — | — |
| systematic | 1M | -187 | — | — | — | — | — | — |
| sector | 1M | -42 | — | — | — | — | — | — |
| name | 1M | -7 | — | — | — | — | — | — |
| crash | 1M | -5 | — | — | — | — | — | — |
| es | 1M | +198 | — | — | — | — | — | — |
| var | 1M | +198 | — | — | — | — | — | — |
| drawdown | 1M | +198 | — | — | — | — | — | — |
| duration | 1M | -31 | — | — | — | — | — | — |
| curve | 1M | -29 | — | — | — | — | — | — |
| credit | 1M | -1 | — | — | — | — | — | — |
| fx | 1M | +6 | — | — | — | — | — | — |
| commodity | 1M | -19 | — | — | — | — | — | — |
| crypto | 1M | -0 | — | — | — | — | — | — |
| volatility | 1M | -98 | — | — | — | — | — | — |
| min_variance | 3M | -174 | — | — | — | — | — | — |
| target_vol | 3M | -537 | — | — | — | — | — | — |
| beta | 3M | -1,447 | — | — | — | — | — | — |
| systematic | 3M | -345 | — | — | — | — | — | — |
| sector | 3M | +161 | — | — | — | — | — | — |
| name | 3M | +79 | — | — | — | — | — | — |
| crash | 3M | +7,835 | — | — | — | — | — | — |
| es | 3M | -1,980 | — | — | — | — | — | — |
| var | 3M | -1,980 | — | — | — | — | — | — |
| drawdown | 3M | -1,980 | — | — | — | — | — | — |
| duration | 3M | -36 | — | — | — | — | — | — |
| curve | 3M | -91 | — | — | — | — | — | — |
| credit | 3M | +34 | — | — | — | — | — | — |
| fx | 3M | -82 | — | — | — | — | — | — |
| commodity | 3M | -205 | — | — | — | — | — | — |
| crypto | 3M | -84 | — | — | — | — | — | — |
| volatility | 3M | +49 | — | — | — | — | — | — |

Profit Δ and cost Δ enter U with their sign (positive = event gave up less profit / paid less). Basis error Δ > 0 = event's hedge tracked the targeted exposure worse. Tail Δ > 0 = event's hedged book lost less in the unhedged book's worst 5% of windows. Hedge-size error = mean |ex-post variance-optimal multiple − 1| of the package (Δ < 0 = closer to the right size); it is unstable when a hedge's P&L over the window is close to zero, so large values come from a few near-zero hedges and should not be read as systematic over- or under-hedging.

## Product-selection effects (common transitions, event vs production)

Transitions between two products of the same type (for example equity_index_future → equity_index_future) are changes of contract, such as NQ → ES or ES → MES.

| Objective | Horizon | Production → event product | Cases | Δ risk (signed RMS of hedged P&L change) | Δ cost / case | Δ profit sacrificed / case | Share of ΔU |
|---|---|---|---|---|---|---|---|

## Size effects (event ÷ production, same risk unit)

| Objective | Horizon | Median | p25 | p75 | Larger / smaller | By product (median) | High vol / normal vol (median) |
|---|---|---|---|---|---|---|---|

## Crisis and regime attribution (ΔU event − production, λ = 1)

| Objective | Horizon | High volatility ΔU (t) | Normal volatility ΔU (t) | Without 2009 | Without COVID 2020 | Without 2022 | Without all three |
|---|---|---|---|---|---|---|---|
| min_variance | 1W | — (—) | — (—) | — (—) | — (—) | — (—) | — (—) |
| target_vol | 1W | — (—) | — (—) | — (—) | — (—) | — (—) | — (—) |
| beta | 1W | — (—) | — (—) | — (—) | — (—) | — (—) | — (—) |
| systematic | 1W | — (—) | — (—) | — (—) | — (—) | — (—) | — (—) |
| sector | 1W | — (—) | — (—) | — (—) | — (—) | — (—) | — (—) |
| name | 1W | — (—) | — (—) | — (—) | — (—) | — (—) | — (—) |
| crash | 1W | — (—) | — (—) | — (—) | — (—) | — (—) | — (—) |
| es | 1W | — (—) | — (—) | — (—) | — (—) | — (—) | — (—) |
| duration | 1W | — (—) | — (—) | — (—) | — (—) | — (—) | — (—) |
| curve | 1W | — (—) | — (—) | — (—) | — (—) | — (—) | — (—) |
| credit | 1W | — (—) | — (—) | — (—) | — (—) | — (—) | — (—) |
| fx | 1W | — (—) | — (—) | — (—) | — (—) | — (—) | — (—) |
| commodity | 1W | — (—) | — (—) | — (—) | — (—) | — (—) | — (—) |
| crypto | 1W | — (—) | — (—) | — (—) | — (—) | — (—) | — (—) |
| volatility | 1W | — (—) | — (—) | — (—) | — (—) | — (—) | — (—) |
| min_variance | 1M | — (—) | — (—) | — (—) | — (—) | — (—) | — (—) |
| target_vol | 1M | — (—) | — (—) | — (—) | — (—) | — (—) | — (—) |
| beta | 1M | — (—) | — (—) | — (—) | — (—) | — (—) | — (—) |
| systematic | 1M | — (—) | — (—) | — (—) | — (—) | — (—) | — (—) |
| sector | 1M | — (—) | — (—) | — (—) | — (—) | — (—) | — (—) |
| name | 1M | — (—) | — (—) | — (—) | — (—) | — (—) | — (—) |
| crash | 1M | — (—) | — (—) | — (—) | — (—) | — (—) | — (—) |
| es | 1M | — (—) | — (—) | — (—) | — (—) | — (—) | — (—) |
| duration | 1M | — (—) | — (—) | — (—) | — (—) | — (—) | — (—) |
| curve | 1M | — (—) | — (—) | — (—) | — (—) | — (—) | — (—) |
| credit | 1M | — (—) | — (—) | — (—) | — (—) | — (—) | — (—) |
| fx | 1M | — (—) | — (—) | — (—) | — (—) | — (—) | — (—) |
| commodity | 1M | — (—) | — (—) | — (—) | — (—) | — (—) | — (—) |
| crypto | 1M | — (—) | — (—) | — (—) | — (—) | — (—) | — (—) |
| volatility | 1M | — (—) | — (—) | — (—) | — (—) | — (—) | — (—) |
| min_variance | 3M | — (—) | — (—) | — (—) | — (—) | — (—) | — (—) |
| target_vol | 3M | — (—) | — (—) | — (—) | — (—) | — (—) | — (—) |
| beta | 3M | — (—) | — (—) | — (—) | — (—) | — (—) | — (—) |
| systematic | 3M | — (—) | — (—) | — (—) | — (—) | — (—) | — (—) |
| sector | 3M | — (—) | — (—) | — (—) | — (—) | — (—) | — (—) |
| name | 3M | — (—) | — (—) | — (—) | — (—) | — (—) | — (—) |
| crash | 3M | — (—) | — (—) | — (—) | — (—) | — (—) | — (—) |
| es | 3M | — (—) | — (—) | — (—) | — (—) | — (—) | — (—) |
| duration | 3M | — (—) | — (—) | — (—) | — (—) | — (—) | — (—) |
| curve | 3M | — (—) | — (—) | — (—) | — (—) | — (—) | — (—) |
| credit | 3M | — (—) | — (—) | — (—) | — (—) | — (—) | — (—) |
| fx | 3M | — (—) | — (—) | — (—) | — (—) | — (—) | — (—) |
| commodity | 3M | — (—) | — (—) | — (—) | — (—) | — (—) | — (—) |
| crypto | 3M | — (—) | — (—) | — (—) | — (—) | — (—) | — (—) |
| volatility | 3M | — (—) | — (—) | — (—) | — (—) | — (—) | — (—) |

## Options (MODEL-PRICED — FLAT VOLATILITY ASSUMPTION): predefined sensitivity

| Objective | Horizon | ΔU (main) | ΔU with bought puts +5 vol points | ΔU excluding every case with an option leg |
|---|---|---|---|---|
| min_variance | 1W | -8 | -37 | +7 |
| target_vol | 1W | +356 | +335 | +342 |
| beta | 1W | -454 | -306 | -175 |
| systematic | 1W | -154 | -128 | -141 |
| sector | 1W | +2 | +2 | +2 |
| name | 1W | +23 | +28 | -1 |
| crash | 1W | +103 | -47 | +1 |
| es | 1W | +77 | +65 | +108 |
| duration | 1W | +16 | +16 | +16 |
| curve | 1W | +16 | +16 | +16 |
| credit | 1W | +33 | +33 | +33 |
| fx | 1W | +22 | +22 | +22 |
| commodity | 1W | -5 | +6 | +0 |
| crypto | 1W | +20 | +20 | +20 |
| volatility | 1W | +188 | +161 | +36 |
| min_variance | 1M | +482 | +451 | +69 |
| target_vol | 1M | +539 | +471 | +185 |
| beta | 1M | -316 | -164 | -544 |
| systematic | 1M | -187 | -126 | -286 |
| sector | 1M | -42 | -42 | -42 |
| name | 1M | -7 | -12 | -12 |
| crash | 1M | -5 | +102 | +0 |
| es | 1M | +198 | +106 | -96 |
| duration | 1M | -31 | -31 | -31 |
| curve | 1M | -29 | -29 | -29 |
| credit | 1M | -1 | -1 | -1 |
| fx | 1M | +6 | +6 | +6 |
| commodity | 1M | -19 | -10 | +6 |
| crypto | 1M | -0 | -0 | -0 |
| volatility | 1M | -98 | -118 | -3 |
| min_variance | 3M | -174 | -195 | -370 |
| target_vol | 3M | -537 | -603 | -685 |
| beta | 3M | -1,447 | -1,381 | -1,589 |
| systematic | 3M | -345 | -361 | -504 |
| sector | 3M | +161 | +161 | +161 |
| name | 3M | +79 | +70 | +5 |
| crash | 3M | +7,835 | +8,230 | +0 |
| es | 3M | -1,980 | -1,957 | -1,791 |
| duration | 3M | -36 | -36 | -36 |
| curve | 3M | -91 | -91 | -91 |
| credit | 3M | +34 | +34 | +34 |
| fx | 3M | -82 | -82 | -82 |
| commodity | 3M | -205 | -227 | +3 |
| crypto | 3M | -84 | -84 | -84 |
| volatility | 3M | +49 | +25 | +7 |

## Resizing (2018 on, where the confirmed multiples are out of sample)

| Objective | Horizon | Cases | ΔU event only | ΔU resizing only | ΔU event + resizing | Combination |
|---|---|---|---|---|---|---|

Additive: the combination ≈ the sum of the two; redundant: ≈ the larger alone; interacting: departs from both; conflicting: the two effects have opposite signs.

## Answers

**1. Does event still improve the volatility forecast out of sample?** 1W: squared log error of the market factor +0.4% against the regression without event (t -0.9, 446 dates), -40.9% against production's 252-day sample — G1 fails; 1M: squared log error of the market factor +0.6% against the regression without event (t -3.0, 212 dates), -32.7% against production's 252-day sample — G1 fails; 3M: squared log error of the market factor +0.2% against the regression without event (t -1.6, 70 dates), -6.8% against production's 252-day sample — G1 fails.

**2. Does it change hedge sizing?** Share of matched cases where the challenger's package differs (identical / same products other size / other products): min_variance 1W 46% / 36% / 18%; target_vol 1W 50% / 26% / 25%; beta 1W 76% / 1% / 23%; systematic 1W 60% / 24% / 16%; sector 1W 32% / 62% / 6%; name 1W 79% / 18% / 3%; crash 1W 88% / 0% / 12%; es 1W 52% / 25% / 23%; var 1W 52% / 25% / 23%; drawdown 1W 52% / 25% / 23%; duration 1W 95% / 4% / 1%; curve 1W 95% / 4% / 1%; credit 1W 70% / 27% / 3%; fx 1W 98% / 0% / 2%; commodity 1W 94% / 2% / 5%; crypto 1W 75% / 24% / 1%; volatility 1W 79% / 8% / 12%; min_variance 1M 54% / 32% / 13%; target_vol 1M 46% / 33% / 21%; beta 1M 76% / 1% / 23%; systematic 1M 60% / 24% / 15%; sector 1M 29% / 65% / 6%; name 1M 88% / 10% / 2%; crash 1M 89% / 0% / 11%; es 1M 62% / 22% / 17%; var 1M 62% / 22% / 17%; drawdown 1M 62% / 22% / 17%; duration 1M 96% / 2% / 2%; curve 1M 95% / 2% / 3%; credit 1M 69% / 29% / 2%; fx 1M 95% / 3% / 3%; commodity 1M 93% / 3% / 4%; crypto 1M 76% / 24% / 0%; volatility 1M 73% / 10% / 17%; min_variance 3M 57% / 30% / 13%; target_vol 3M 46% / 35% / 20%; beta 3M 80% / 1% / 19%; systematic 3M 61% / 24% / 15%; sector 3M 26% / 67% / 8%; name 3M 90% / 7% / 3%; crash 3M 83% / 0% / 17%; es 3M 65% / 20% / 15%; var 3M 65% / 20% / 15%; drawdown 3M 65% / 20% / 15%; duration 3M 95% / 1% / 4%; curve 3M 90% / 1% / 8%; credit 3M 65% / 30% / 5%; fx 3M 94% / 2% / 4%; commodity 3M 90% / 3% / 8%; crypto 3M 65% / 29% / 6%; volatility 3M 61% / 16% / 23%.

**3. By how much?** Challenger ÷ production gross hedge notional, median [10th–90th percentile] (share of cases larger / smaller): min_variance 1W 1.000 [0.67–1.14] (23% / 38%); target_vol 1W 1.000 [0.50–1.52] (31% / 42%); beta 1W 1.000 [0.98–1.03] (12% / 12%); systematic 1W 1.000 [0.98–1.03] (21% / 20%); sector 1W 1.000 [1.00–1.00] (27% / 41%); name 1W 1.000 [1.00–1.00] (7% / 14%); crash 1W 1.000 [1.00–1.00] (5% / 7%); es 1W 1.000 [0.50–1.25] (23% / 36%); var 1W 1.000 [0.50–1.25] (23% / 36%); drawdown 1W 1.000 [0.50–1.25] (23% / 36%); duration 1W 1.000 [1.00–1.00] (2% / 2%); curve 1W 1.000 [1.00–1.00] (3% / 2%); credit 1W 1.000 [1.00–1.00] (13% / 17%); fx 1W 1.000 [1.00–1.00] (1% / 1%); commodity 1W 1.000 [1.00–1.00] (2% / 3%); crypto 1W 1.000 [1.00–1.00] (8% / 17%); volatility 1W 1.000 [1.00–1.03] (11% / 7%); min_variance 1M 1.000 [0.86–1.11] (23% / 25%); target_vol 1M 1.000 [0.58–1.50] (34% / 39%); beta 1M 1.000 [0.97–1.02] (11% / 13%); systematic 1M 1.000 [0.98–1.02] (19% / 20%); sector 1M 1.000 [1.00–1.00] (33% / 38%); name 1M 1.000 [1.00–1.00] (5% / 7%); crash 1M 1.000 [1.00–1.00] (5% / 6%); es 1M 1.000 [0.83–1.16] (21% / 22%); var 1M 1.000 [0.83–1.16] (21% / 22%); drawdown 1M 1.000 [0.83–1.16] (21% / 22%); duration 1M 1.000 [1.00–1.00] (1% / 2%); curve 1M 1.000 [1.00–1.00] (2% / 3%); credit 1M 1.000 [0.99–1.00] (15% / 17%); fx 1M 1.000 [1.00–1.00] (2% / 3%); commodity 1M 1.000 [1.00–1.00] (1% / 5%); crypto 1M 1.000 [1.00–1.00] (5% / 18%); volatility 1M 1.000 [1.00–1.17] (15% / 10%); min_variance 3M 1.000 [0.93–1.10] (23% / 21%); target_vol 3M 1.000 [0.71–1.50] (40% / 31%); beta 3M 1.000 [0.99–1.00] (9% / 11%); systematic 3M 1.000 [0.98–1.01] (18% / 21%); sector 3M 1.000 [1.00–1.00] (40% / 34%); name 3M 1.000 [1.00–1.00] (4% / 5%); crash 3M 1.000 [1.00–1.16] (11% / 7%); es 3M 1.000 [0.93–1.14] (20% / 16%); var 3M 1.000 [0.93–1.14] (20% / 16%); drawdown 3M 1.000 [0.93–1.14] (20% / 16%); duration 3M 1.000 [1.00–1.00] (2% / 3%); curve 3M 1.000 [1.00–1.00] (4% / 5%); credit 3M 1.000 [1.00–1.01] (20% / 15%); fx 3M 1.000 [1.00–1.00] (3% / 3%); commodity 3M 1.000 [1.00–1.00] (4% / 6%); crypto 3M 1.000 [1.00–1.00] (6% / 29%); volatility 3M 1.000 [1.00–1.20] (19% / 10%).

**4. Does it change product selection?** min_variance 1W: products differ in 18% of cases (equity_index_future → equity_index_future ×624, etf → etf ×134, equity_index_future → no hedge ×70); target_vol 1W: products differ in 25% of cases (equity_index_future → equity_index_future ×502, equity_index_future → no hedge ×415, no hedge → equity_index_future ×126); beta 1W: products differ in 23% of cases (equity_index_future → equity_index_future ×732, equity_index_future → etf ×104, etf → equity_index_future ×94); systematic 1W: products differ in 16% of cases (equity_index_future → equity_index_future ×493, etf → etf ×215, equity_index_future → etf ×59); sector 1W: products differ in 6% of cases (etf → etf ×184); name 1W: products differ in 3% of cases (equity_index_future → equity_index_future ×40, etf → etf ×15, etf → common_stock ×15); crash 1W: products differ in 12% of cases (index_option → inverse_etf ×310, inverse_etf → index_option ×216, index_option → index_option ×35); es 1W: products differ in 23% of cases (equity_index_future → equity_index_future ×635, equity_index_future → no hedge ×242, etf → etf ×152); var 1W: products differ in 23% of cases (equity_index_future → equity_index_future ×635, equity_index_future → no hedge ×242, etf → etf ×152); drawdown 1W: products differ in 23% of cases (equity_index_future → equity_index_future ×635, equity_index_future → no hedge ×242, etf → etf ×152); duration 1W: products differ in 1% of cases (etf → treasury_future ×11, treasury_future → treasury_future ×4, treasury_future → etf ×1); curve 1W: products differ in 1% of cases (etf → treasury_future ×12, treasury_future → treasury_future ×10, treasury_future → etf ×1); credit 1W: products differ in 3% of cases (high_yield → high_yield ×17, high_yield → leveraged_loan ×4); fx 1W: products differ in 2% of cases (fx_forward → currency_trust ×10, fx_spot → currency_trust ×3, currency_trust → fx_forward ×1); commodity 1W: products differ in 5% of cases (commodity_option → commodity_option ×46, commodity_option → etf ×6, etf → etf ×3); crypto 1W: products differ in 1% of cases (crypto_etf → crypto_futures_etf ×1); volatility 1W: products differ in 12% of cases (index_option → index_option ×34); min_variance 1M: products differ in 13% of cases (equity_index_future → equity_index_future ×214, etf → etf ×40, equity_index_future → etf ×18); target_vol 1M: products differ in 21% of cases (equity_index_future → equity_index_future ×317, equity_index_future → no hedge ×72, no hedge → equity_index_future ×55); beta 1M: products differ in 23% of cases (equity_index_future → equity_index_future ×376, index_option → equity_index_future ×61, equity_index_future → etf ×34); systematic 1M: products differ in 15% of cases (equity_index_future → equity_index_future ×254, etf → etf ×78, commodity_option → commodity_option ×19); sector 1M: products differ in 6% of cases (etf → etf ×81); name 1M: products differ in 2% of cases (equity_index_future → equity_index_future ×14, index_option → index_option ×9, common_stock → equity_index_future ×7); crash 1M: products differ in 11% of cases (index_option → inverse_etf ×129, inverse_etf → index_option ×107, index_option → index_option ×13); es 1M: products differ in 17% of cases (equity_index_future → equity_index_future ×297, etf → etf ×23, equity_index_future → etf ×17); var 1M: products differ in 17% of cases (equity_index_future → equity_index_future ×297, etf → etf ×23, equity_index_future → etf ×17); drawdown 1M: products differ in 17% of cases (equity_index_future → equity_index_future ×297, etf → etf ×23, equity_index_future → etf ×17); duration 1M: products differ in 2% of cases (treasury_future → etf ×11, etf → treasury_future ×5, treasury_future → treasury_future ×3); curve 1M: products differ in 3% of cases (treasury_future → treasury_future ×14, treasury_future → etf ×11, etf → treasury_future ×5); credit 1M: products differ in 2% of cases (leveraged_loan → high_yield ×4, high_yield → high_yield ×3, high_yield → leveraged_loan ×1); fx 1M: products differ in 3% of cases (fx_forward → currency_trust ×3, fx_spot → currency_trust ×2, currency_trust → fx_forward ×2); commodity 1M: products differ in 4% of cases (commodity_option → commodity_option ×13, commodity_option → etf ×9, etf → etf ×2); volatility 1M: products differ in 17% of cases (index_option → index_option ×23); min_variance 3M: products differ in 13% of cases (equity_index_future → equity_index_future ×85, etf → etf ×9, etf → equity_index_future ×3); target_vol 3M: products differ in 20% of cases (equity_index_future → equity_index_future ×102, no hedge → equity_index_future ×28, equity_index_future → no hedge ×18); beta 3M: products differ in 19% of cases (equity_index_future → equity_index_future ×110, index_option → equity_index_future ×11, etf → equity_index_future ×8); systematic 3M: products differ in 15% of cases (equity_index_future → equity_index_future ×86, etf → etf ×28, commodity_option → commodity_option ×8); sector 3M: products differ in 8% of cases (etf → etf ×35); name 3M: products differ in 3% of cases (treasury_future → treasury_future ×7, etf → common_stock ×5, equity_index_future → etf ×3); crash 3M: products differ in 17% of cases (inverse_etf → index_option ×76, index_option → inverse_etf ×49, index_option → index_option ×2); es 3M: products differ in 15% of cases (equity_index_future → equity_index_future ×95, etf → etf ×6, etf → equity_index_future ×6); var 3M: products differ in 15% of cases (equity_index_future → equity_index_future ×95, etf → etf ×6, etf → equity_index_future ×6); drawdown 3M: products differ in 15% of cases (equity_index_future → equity_index_future ×95, etf → etf ×6, etf → equity_index_future ×6); duration 3M: products differ in 4% of cases (treasury_future → etf ×7, etf → treasury_future ×3, treasury_future → treasury_future ×1); curve 3M: products differ in 8% of cases (treasury_future → treasury_future ×15, treasury_future → etf ×7, etf → treasury_future ×3); credit 3M: products differ in 5% of cases (high_yield → leveraged_loan ×3, high_yield → high_yield ×2, leveraged_loan → high_yield ×1); fx 3M: products differ in 4% of cases (fx_forward → currency_trust ×2, currency_trust → fx_spot ×1, currency_trust → fx_forward ×1); commodity 3M: products differ in 8% of cases (commodity_option → commodity_option ×10, etf → commodity_option ×2, commodity_option → etf ×1); crypto 3M: products differ in 6% of cases (crypto_etf → crypto_spot ×1); volatility 3M: products differ in 23% of cases (index_option → index_option ×9, index_option → no hedge ×1) What the product changes did is in the attribution table below.

**5. Does realised variance reduction improve?** Challenger minus production, percentage points: min_variance 1W -0.64%; target_vol 1W +1.37%; beta 1W -1.17%; systematic 1W -0.29%; sector 1W +0.07%; name 1W +0.12%; crash 1W -1.17%; es 1W -0.31%; var 1W -0.31%; drawdown 1W -0.31%; duration 1W -0.69%; curve 1W -0.69%; credit 1W -0.28%; fx 1W -0.25%; commodity 1W -0.02%; crypto 1W +0.00%; volatility 1W +0.64%; min_variance 1M +0.15%; target_vol 1M +1.50%; beta 1M -0.40%; systematic 1M -0.34%; sector 1M -0.04%; name 1M +0.03%; crash 1M -0.45%; es 1M +0.24%; var 1M +0.24%; drawdown 1M +0.24%; duration 1M -0.01%; curve 1M -0.02%; credit 1M +0.13%; fx 1M +0.18%; commodity 1M -0.05%; crypto 1M -0.00%; volatility 1M +0.15%; min_variance 3M -0.04%; target_vol 3M +0.38%; beta 3M -0.22%; systematic 3M -0.35%; sector 3M +0.12%; name 3M +0.04%; crash 3M -1.90%; es 3M -0.14%; var 3M -0.14%; drawdown 3M -0.14%; duration 3M +0.28%; curve 3M +0.26%; credit 3M +0.09%; fx 3M +0.20%; commodity 3M +0.40%; crypto 3M -0.01%; volatility 3M -0.05%.

**6. Does realised ES reduction improve?** Hedged ES95, production minus challenger ($): min_variance 1W -360; target_vol 1W +1,560; beta 1W -1,548; systematic 1W -346; sector 1W +15; name 1W +11; crash 1W -3; es 1W +18; var 1W +18; drawdown 1W +18; duration 1W +0; curve 1W +0; credit 1W +8; fx 1W +0; commodity 1W +2; crypto 1W -1; volatility 1W +610; min_variance 1M +373; target_vol 1M +356; beta 1M -873; systematic 1M -368; sector 1M -35; name 1M +9; crash 1M -285; es 1M +206; var 1M +206; drawdown 1M +206; duration 1M -43; curve 1M -42; credit 1M +1; fx 1M -22; commodity 1M -69; crypto 1M +0; volatility 1M +339; min_variance 3M -1,328; target_vol 3M -3,922; beta 3M -4,590; systematic 3M -60; sector 3M -393; name 3M +13; crash 3M +4,946; es 3M -1,530; var 3M -1,530; drawdown 3M -1,530; duration 3M +0; curve 3M -80; credit 3M +389; fx 3M +0; commodity 3M -67; volatility 3M -381.

**7. Does realised drawdown protection improve?** Mean max drawdown of the hedged book, production minus challenger ($): min_variance 1W -155; target_vol 1W +31; beta 1W -146; systematic 1W -64; sector 1W -8; name 1W +5; crash 1W -7; es 1W -93; var 1W -93; drawdown 1W -93; duration 1W -5; curve 1W -5; credit 1W -0; fx 1W -15; commodity 1W -8; crypto 1W +10; volatility 1W +11; min_variance 1M -36; target_vol 1M +243; beta 1M -123; systematic 1M -114; sector 1M +2; name 1M -4; crash 1M -70; es 1M +37; var 1M +37; drawdown 1M +37; duration 1M +5; curve 1M +7; credit 1M +11; fx 1M +7; commodity 1M -23; crypto 1M +0; volatility 1M +24; min_variance 3M -273; target_vol 3M +46; beta 3M -389; systematic 3M -304; sector 3M +129; name 3M +43; crash 3M -236; es 3M -194; var 3M -194; drawdown 3M -194; duration 3M +35; curve 3M +17; credit 3M +58; fx 3M -11; commodity 3M +43; crypto 3M -67; volatility 3M -59.

**8. Does tail protection improve?** Mean hedged P&L in the unhedged book's worst 5% of windows, challenger minus production ($): min_variance 1W +358; target_vol 1W +1,949; beta 1W -2,178; systematic 1W -266; sector 1W +12; name 1W -46; crash 1W -30; es 1W +24; var 1W +24; drawdown 1W +24; duration 1W +0; curve 1W +0; credit 1W +8; fx 1W +0; commodity 1W +2; crypto 1W -1; volatility 1W +660; min_variance 1M +1,238; target_vol 1M +2,281; beta 1M -224; systematic 1M -92; sector 1M +5; name 1M +1; crash 1M -207; es 1M +319; var 1M +319; drawdown 1M +319; duration 1M -0; curve 1M -0; credit 1M -16; fx 1M -24; commodity 1M -61; crypto 1M +0; volatility 1M +396; min_variance 3M -4,012; target_vol 3M -2,370; beta 3M -6,284; systematic 3M -489; sector 3M -0; name 3M -0; crash 3M +3,427; es 3M -1,102; var 3M -1,102; drawdown 3M -1,102; duration 3M +0; curve 3M -86; credit 3M +388; fx 3M +0; commodity 3M +0; volatility 3M -572.

**9. Does hedge cost rise or fall?** Mean cost, challenger minus production ($): min_variance 1W -13.6; target_vol 1W -10.8; beta 1W +11.2; systematic 1W +8.6; sector 1W -0.9; name 1W +3.5; crash 1W -36.1; es 1W -5.5; var 1W -5.5; drawdown 1W -5.5; duration 1W +0.3; curve 1W +0.3; credit 1W +0.0; fx 1W +0.1; commodity 1W -1.6; crypto 1W -0.6; volatility 1W -0.7; min_variance 1M -23.1; target_vol 1M -40.2; beta 1M +137.0; systematic 1M +45.3; sector 1M -3.0; name 1M +5.2; crash 1M -163.3; es 1M -17.9; var 1M -17.9; drawdown 1M -17.9; duration 1M +0.4; curve 1M +0.5; credit 1M -0.5; fx 1M +0.1; commodity 1M -8.5; crypto 1M -0.0; volatility 1M +7.9; min_variance 3M -30.7; target_vol 3M -38.4; beta 3M +401.6; systematic 3M +104.5; sector 3M -0.1; name 3M +25.6; crash 3M -1,184.8; es 3M -11.9; var 3M -11.9; drawdown 3M -11.9; duration 3M +0.5; curve 3M +0.8; credit 3M +7.4; fx 3M +1.4; commodity 3M +24.1; crypto 3M -3.5; volatility 3M -39.4.

**10. Is more expected profit sacrificed?** Realised profit sacrificed (−mean hedge P&L), challenger minus production ($): min_variance 1W -110.8; target_vol 1W -139.4; beta 1W -55.5; systematic 1W -37.4; sector 1W +15.1; name 1W -1.6; crash 1W -70.1; es 1W -53.4; var 1W -53.4; drawdown 1W -53.4; duration 1W +5.0; curve 1W +5.8; credit 1W +18.0; fx 1W +10.8; commodity 1W +5.1; crypto 1W -30.0; volatility 1W +41.3; min_variance 1M -51.2; target_vol 1M -126.7; beta 1M -332.9; systematic 1M -124.2; sector 1M -32.1; name 1M +1.2; crash 1M -116.8; es 1M +26.6; var 1M +26.6; drawdown 1M +26.6; duration 1M -7.6; curve 1M -9.7; credit 1M -0.2; fx 1M +1.4; commodity 1M -0.9; crypto 1M -0.2; volatility 1M +10.0; min_variance 3M +376.3; target_vol 3M +953.8; beta 3M +188.8; systematic 3M -125.2; sector 3M -16.1; name 3M +25.3; crash 3M -1,704.2; es 3M +462.5; var 3M +462.5; drawdown 3M +462.5; duration 3M +9.1; curve 3M +34.8; credit 3M -56.9; fx 3M +39.8; commodity 3M +125.2; crypto 3M +68.8; volatility 3M -43.2.

**11. Does total hedge utility improve?** No cell shows a significant utility improvement at λ = 1 after the FDR control. Point estimates for every cell are in the main table.

**12. At which λ values?** ΔU for λ = 0.5 / 1.0 / 2.0 / 5.0 / 10.0: min_variance 1W -63 / -8 / +103 / +435 / +989; target_vol 1W +286 / +356 / +495 / +914 / +1,611; beta 1W -482 / -454 / -398 / -232 / +46; systematic 1W -173 / -154 / -117 / -5 / +182; sector 1W +9 / +2 / -13 / -58 / -134; name 1W +23 / +23 / +25 / +30 / +38; crash 1W +68 / +103 / +173 / +383 / +734; es 1W +50 / +77 / +131 / +291 / +558; var 1W +50 / +77 / +131 / +291 / +558; drawdown 1W +50 / +77 / +131 / +291 / +558; duration 1W +18 / +16 / +11 / -4 / -29; curve 1W +19 / +16 / +10 / -7 / -36; credit 1W +42 / +33 / +15 / -38 / -128; fx 1W +28 / +22 / +12 / -21 / -75; commodity 1W -2 / -5 / -10 / -25 / -51; crypto 1W +5 / +20 / +50 / +140 / +290; volatility 1W +208 / +188 / +147 / +23 / -184; min_variance 1M +456 / +482 / +533 / +686 / +942; target_vol 1M +476 / +539 / +666 / +1,046 / +1,679; beta 1M -483 / -316 / +17 / +1,015 / +2,680; systematic 1M -249 / -187 / -63 / +310 / +931; sector 1M -58 / -42 / -10 / +86 / +246; name 1M -6 / -7 / -8 / -11 / -17; crash 1M -63 / -5 / +112 / +462 / +1,046; es 1M +211 / +198 / +171 / +91 / -41; var 1M +211 / +198 / +171 / +91 / -41; drawdown 1M +211 / +198 / +171 / +91 / -41; duration 1M -35 / -31 / -24 / -1 / +37; curve 1M -34 / -29 / -19 / +10 / +58; credit 1M -1 / -1 / -1 / -0 / +1; fx 1M +7 / +6 / +5 / +0 / -7; commodity 1M -19 / -19 / -18 / -15 / -11; crypto 1M -0 / -0 / +0 / +1 / +2; volatility 1M -93 / -98 / -108 / -138 / -187; min_variance 3M +14 / -174 / -551 / -1,680 / -3,561; target_vol 3M -60 / -537 / -1,491 / -4,352 / -9,121; beta 3M -1,352 / -1,447 / -1,635 / -2,202 / -3,146; systematic 3M -408 / -345 / -220 / +155 / +782; sector 3M +153 / +161 / +177 / +225 / +306; name 3M +92 / +79 / +54 / -22 / -148; crash 3M +6,983 / +7,835 / +9,540 / +14,652 / +23,173; es 3M -1,749 / -1,980 / -2,443 / -3,830 / -6,143; var 3M -1,749 / -1,980 / -2,443 / -3,830 / -6,143; drawdown 3M -1,749 / -1,980 / -2,443 / -3,830 / -6,143; duration 3M -31 / -36 / -45 / -72 / -118; curve 3M -73 / -91 / -126 / -230 / -404; credit 3M +6 / +34 / +91 / +262 / +546; fx 3M -62 / -82 / -122 / -241 / -440; commodity 3M -142 / -205 / -330 / -706 / -1,331; crypto 3M -49 / -84 / -153 / -359 / -703; volatility 3M +27 / +49 / +92 / +221 / +437.

**13. Which hedge objectives benefit?** None passes G1–G4. Positive point estimates without passing: target_vol 1W, sector 1W, name 1W, crash 1W, es 1W, var 1W, drawdown 1W, duration 1W, curve 1W, credit 1W, fx 1W, crypto 1W, volatility 1W, min_variance 1M, target_vol 1M, es 1M, var 1M, drawdown 1M, fx 1M, sector 3M, name 3M, crash 3M, credit 3M, volatility 3M.

**14. Which hedge products benefit?** 168 product × objective × horizon breakdowns with enough dates; significant and positive after their own BH: none.

**15. Which portfolio types benefit?** 255 portfolio-type × objective × horizon breakdowns with enough dates; significant and positive after their own BH: none.

**16. Which regimes benefit?** 420 regime × objective × horizon breakdowns with enough dates; significant and positive after their own BH: none. Liquidity stress: no point-in-time liquidity series is available, so it is not tested.

**17. Does event-vol outperform the fixed-resizing baseline?** From 2018 (where the resizing multiples are out of sample), ΔU at λ = 1 of B vs C: min_variance 1W +152; target_vol 1W +576; beta 1W -453; systematic 1W -120; sector 1W +16; name 1W +169; crash 1W +797; es 1W -162; var 1W -162; drawdown 1W -162; duration 1W +21; curve 1W +21; credit 1W +36; fx 1W +28; commodity 1W +10; crypto 1W +20; volatility 1W +189; min_variance 1M +1,285; target_vol 1M +942; beta 1M -550; systematic 1M -218; sector 1M -110; name 1M -38; crash 1M -160; es 1M -246; var 1M +1,738; drawdown 1M -246; duration 1M -29; curve 1M -27; credit 1M +25; fx 1M +61; commodity 1M +62; crypto 1M -0; volatility 1M -91; min_variance 3M +128; target_vol 3M -572; beta 3M -2,600; systematic 3M -396; sector 3M +145; name 3M +269; crash 3M +7,180; es 3M -2,681; var 3M -2,681; drawdown 3M -2,681; duration 3M -54; curve 3M -128; credit 3M +299; fx 3M -85; commodity 3M -272; crypto 3M -84; volatility 3M +49.

**18. Are event-vol and resizing additive?** From 2018, ΔU(D) against ΔU(B) + ΔU(C), λ = 1: min_variance 1W +120 vs +147; target_vol 1W +556 vs +566; beta 1W -643 vs -811; systematic 1W -238 vs -287; sector 1W +16 vs +16; name 1W -69 vs -65; crash 1W -706 vs -450; es 1W -187 vs -163; var 1W -187 vs -163; drawdown 1W -187 vs -163; duration 1W +21 vs +21; curve 1W +21 vs +21; credit 1W +36 vs +36; fx 1W +27 vs +34; commodity 1W -27 vs -29; crypto 1W +20 vs +20; volatility 1W +189 vs +189; min_variance 1M +1,101 vs +1,092; target_vol 1M +897 vs +926; beta 1M -441 vs -332; systematic 1M -108 vs -108; sector 1M -110 vs -110; name 1M +4 vs +3; crash 1M +236 vs +132; es 1M -368 vs -297; var 1M -2,258 vs -2,281; drawdown 1M -368 vs -297; duration 1M -64 vs -66; curve 1M -62 vs -64; credit 1M -28 vs -28; fx 1M -39 vs -51; commodity 1M -45 vs -43; crypto 1M -0 vs -0; volatility 1M -91 vs -91; min_variance 3M +117 vs +84; target_vol 3M -594 vs -616; beta 3M -2,527 vs -2,453; systematic 3M -441 vs -441; sector 3M +145 vs +145; name 3M +68 vs +6; crash 3M +7,544 vs +7,206; es 3M -2,405 vs -2,456; var 3M -2,405 vs -2,456; drawdown 3M -2,405 vs -2,456; duration 3M -54 vs -54; curve 3M -128 vs -128; credit 3M -187 vs -177; fx 3M -151 vs -174; commodity 3M +404 vs +406; crypto 3M -84 vs -84; volatility 3M +49 vs +49.

**19. Does the result survive all eras?** Complete eras with ΔU > 0 (λ = 1): min_variance 1W 2/4; target_vol 1W 3/4; beta 1W 1/4; systematic 1W 0/4; sector 1W 1/4; name 1W 1/4; crash 1W 2/4; es 1W 1/4; var 1W 1/4; drawdown 1W 1/4; duration 1W 4/4; curve 1W 3/4; credit 1W 4/4; fx 1W 2/4; commodity 1W 1/4; crypto 1W 1/1; volatility 1W 2/2; min_variance 1M 2/4; target_vol 1M 3/4; beta 1M 0/4; systematic 1M 1/4; sector 1M 3/4; name 1M 1/4; crash 1M 2/4; es 1M 2/4; var 1M 2/4; drawdown 1M 2/4; duration 1M 2/4; curve 1M 3/4; credit 1M 3/4; fx 1M 2/4; commodity 1M 1/4; crypto 1M 0/1; volatility 1M 1/2; min_variance 3M 1/4; target_vol 3M 2/4; beta 3M 0/4; systematic 3M 2/4; sector 3M 3/4; name 3M 2/4; crash 3M 3/4; es 3M 0/4; var 3M 0/4; drawdown 3M 0/4; duration 3M 2/4; curve 3M 2/4; credit 3M 3/4; fx 3M 2/4; commodity 3M 1/4; crypto 3M 0/1; volatility 3M 1/2.

**20. Should the volatility forecast enter production Hedge?** No. The better forecast does not make the Shaffer Hedge measurably better; event stays a research forecast.

**21. Should hedge sizing change?** No change follows from this study: where the forecast changes sizes, the realised outcomes do not justify it (see answers 3 and 11).

**22. Is anything eligible for live shadow or promotion?** No: nothing passes G1–G4, so nothing enters live shadow and nothing is eligible for promotion.

## Attribution (what the changed decisions did)

| Objective | Horizon | Decision change | Cases | Variance reduction Δ (pp) | Hedge P&L Δ | Cost Δ | Basis error Δ | Hedge-size error Δ | Tail Δ |
|---|---|---|---|---|---|---|---|---|---|
| min_variance | 1W | sizing | 2031 | -0.314% | +28.7 | -2.7 | +97.7 | +0.491 | +380 |
| min_variance | 1W | product | 1028 | -0.324% | +82.1 | -10.9 | +228.0 | +134.090 | +1,871 |
| target_vol | 1W | sizing | 1462 | +0.837% | +73.6 | -1.8 | +141.6 | +0.529 | +4,581 |
| target_vol | 1W | product | 1404 | +0.532% | +65.8 | -8.9 | +449.6 | +0.981 | +3,002 |
| beta | 1W | sizing | 55 | +0.000% | +0.8 | +0.0 | -34.2 | -0.004 | -4 |
| beta | 1W | product | 1067 | -1.171% | +54.8 | +11.2 | -729.6 | +0.130 | -9,893 |
| systematic | 1W | sizing | 1387 | +0.075% | -7.5 | +1.3 | +11.0 | -0.007 | +96 |
| systematic | 1W | product | 906 | -0.370% | +44.9 | +7.2 | -360.2 | +0.098 | -2,092 |
| sector | 1W | sizing | 1779 | +0.010% | -0.2 | +0.0 | +1.9 | +0.002 | +8 |
| sector | 1W | product | 184 | +0.064% | -14.9 | -0.9 | -49.5 | +0.501 | +519 |
| name | 1W | sizing | 1025 | +0.014% | +1.0 | -0.1 | +1.1 | -0.021 | +32 |
| name | 1W | product | 157 | +0.102% | +0.6 | +3.6 | -176.4 | +0.528 | -1,610 |
| crash | 1W | product | 564 | -1.174% | +70.1 | -36.1 | +931.3 | -0.566 | -1,998 |
| es | 1W | sizing | 1431 | -0.014% | +11.3 | -1.0 | +44.3 | +0.923 | -197 |
| es | 1W | product | 1302 | -0.298% | +42.1 | -4.5 | +130.0 | +39.571 | +549 |
| var | 1W | sizing | 1431 | -0.014% | +11.3 | -1.0 | +44.3 | +0.923 | -197 |
| var | 1W | product | 1302 | -0.298% | +42.1 | -4.5 | +130.0 | +39.571 | +549 |
| drawdown | 1W | sizing | 1431 | -0.014% | +11.3 | -1.0 | +44.3 | +0.923 | -197 |
| drawdown | 1W | product | 1302 | -0.298% | +42.1 | -4.5 | +130.0 | +39.571 | +549 |
| duration | 1W | sizing | 73 | +0.000% | -0.0 | +0.0 | +4.2 | +1.073 | -0 |
| duration | 1W | product | 16 | -0.685% | -5.0 | +0.3 | +1,146.2 | -40.810 | — |
| curve | 1W | sizing | 76 | +0.000% | -0.0 | +0.0 | +4.0 | +1.033 | -0 |
| curve | 1W | product | 23 | -0.688% | -5.8 | +0.3 | +911.3 | -30.654 | +916 |
| credit | 1W | sizing | 210 | +0.083% | -2.3 | -0.0 | +21.9 | +0.068 | +4 |
| credit | 1W | product | 21 | -0.363% | -15.7 | +0.0 | +3,583.4 | -1.966 | +259 |
| fx | 1W | sizing | 2 | +0.000% | +0.0 | -0.0 | -0.3 | +0.003 | — |
| fx | 1W | product | 14 | -0.248% | -10.8 | +0.1 | +437.2 | +70.034 | — |
| commodity | 1W | sizing | 19 | -0.007% | +0.1 | -0.1 | +9.5 | +0.147 | — |
| commodity | 1W | product | 57 | -0.010% | -5.2 | -1.5 | +64.2 | -0.533 | -459 |
| crypto | 1W | sizing | 30 | +0.000% | -0.2 | -0.0 | +0.2 | -0.046 | -6 |
| crypto | 1W | product | 1 | +0.001% | +30.2 | -0.6 | -1,365.6 | -0.079 | — |
| volatility | 1W | sizing | 23 | -0.014% | -17.8 | -1.2 | -145.6 | +2.889 | -39 |
| volatility | 1W | product | 34 | +0.650% | -23.5 | +0.5 | +4.2 | +1.210 | +5,423 |
| min_variance | 1M | sizing | 867 | -0.079% | -11.5 | -28.7 | -89.9 | +0.029 | +2,006 |
| min_variance | 1M | product | 364 | +0.226% | +62.7 | +5.7 | -351.9 | -0.236 | -391 |
| target_vol | 1M | sizing | 886 | +1.029% | +89.9 | -27.5 | +192.5 | +0.171 | +3,755 |
| target_vol | 1M | product | 566 | +0.475% | +36.8 | -12.7 | +647.4 | +0.666 | +2,815 |
| beta | 1M | sizing | 17 | +0.006% | -1.7 | +0.0 | +71.6 | -0.078 | — |
| beta | 1M | product | 514 | -0.407% | +334.6 | +136.9 | -446.9 | +0.095 | -2,478 |
| systematic | 1M | sizing | 651 | +0.040% | -20.2 | -0.8 | +12.9 | -0.001 | -192 |
| systematic | 1M | product | 416 | -0.377% | +144.4 | +46.1 | -386.9 | +0.064 | -3,055 |
| sector | 1M | sizing | 889 | +0.010% | -0.0 | +0.1 | +1.6 | -0.000 | +7 |
| sector | 1M | product | 81 | -0.048% | +32.1 | -3.1 | +390.5 | -0.025 | +2,985 |
| name | 1M | sizing | 260 | +0.016% | -0.8 | -0.1 | +5.3 | +0.037 | +8 |
| name | 1M | product | 58 | +0.010% | -0.4 | +5.3 | -119.3 | +0.895 | -1,279 |
| crash | 1M | product | 250 | -0.447% | +116.8 | -163.3 | +419.2 | -0.715 | -7,104 |
| es | 1M | sizing | 590 | +0.084% | -9.6 | -15.4 | -11.5 | +0.064 | +1,513 |
| es | 1M | product | 449 | +0.154% | -17.0 | -2.5 | +34.6 | +0.488 | -1,268 |
| var | 1M | sizing | 590 | +0.084% | -9.6 | -15.4 | -11.5 | +0.064 | +1,513 |
| var | 1M | product | 449 | +0.154% | -17.0 | -2.5 | +34.6 | +0.488 | -1,268 |
| drawdown | 1M | sizing | 590 | +0.084% | -9.6 | -15.4 | -11.5 | +0.064 | +1,513 |
| drawdown | 1M | product | 449 | +0.154% | -17.0 | -2.5 | +34.6 | +0.488 | -1,268 |
| duration | 1M | sizing | 15 | +0.003% | +0.1 | -0.0 | -20.6 | +0.365 | — |
| duration | 1M | product | 19 | -0.015% | +7.4 | +0.4 | +219.4 | -3.160 | — |
| curve | 1M | sizing | 15 | +0.003% | +0.1 | -0.0 | -20.6 | +0.365 | — |
| curve | 1M | product | 30 | -0.022% | +9.5 | +0.5 | +181.3 | -1.825 | -1,961 |
| credit | 1M | sizing | 113 | +0.151% | +0.0 | -0.1 | -13.1 | +0.095 | +6 |
| credit | 1M | product | 8 | -0.025% | +0.1 | -0.4 | -38.0 | +9.993 | — |
| fx | 1M | sizing | 8 | +0.000% | +0.0 | -0.0 | -0.1 | +0.043 | — |
| fx | 1M | product | 9 | +0.185% | -1.4 | +0.1 | -157.3 | -2.457 | — |
| commodity | 1M | sizing | 16 | -0.017% | -9.0 | -0.7 | -66.2 | +2.445 | — |
| commodity | 1M | product | 24 | -0.036% | +9.9 | -7.8 | +41.0 | +5.240 | +1,965 |
| crypto | 1M | sizing | 13 | -0.000% | +0.2 | -0.0 | -0.2 | +0.000 | — |
| volatility | 1M | sizing | 13 | +0.071% | -2.4 | +1.7 | +109.2 | +2.188 | — |
| volatility | 1M | product | 23 | +0.083% | -7.6 | +6.2 | +165.7 | -9.729 | +2,374 |
| min_variance | 3M | sizing | 263 | -0.018% | -335.3 | -23.1 | +65.3 | -0.005 | +228 |
| min_variance | 3M | product | 117 | -0.021% | -41.0 | -7.6 | +18.8 | -0.023 | -15,609 |
| target_vol | 3M | sizing | 310 | +0.171% | -342.9 | -33.9 | +197.8 | -0.100 | -1,388 |
| target_vol | 3M | product | 174 | +0.206% | -610.9 | -4.4 | +426.0 | -0.050 | -16,360 |
| beta | 3M | sizing | 10 | +0.012% | -5.3 | +0.1 | +56.9 | -0.066 | — |
| beta | 3M | product | 137 | -0.235% | -183.6 | +401.5 | -1,510.6 | +0.144 | -37,938 |
| systematic | 3M | sizing | 212 | +0.020% | -35.8 | +0.4 | +11.5 | -0.002 | -623 |
| systematic | 3M | product | 134 | -0.375% | +161.0 | +104.1 | +2.5 | +0.050 | -6,091 |
| sector | 3M | sizing | 301 | +0.004% | -3.0 | +0.2 | +1.2 | -0.001 | -2 |
| sector | 3M | product | 35 | +0.120% | +19.1 | -0.3 | +201.4 | +0.290 | -8,996 |
| name | 3M | sizing | 59 | +0.005% | -0.1 | +0.1 | +13.0 | -0.024 | -10 |
| name | 3M | product | 24 | +0.040% | -25.1 | +25.5 | +26.8 | -0.972 | -1,203 |
| crash | 3M | product | 128 | -1.898% | +1,704.2 | -1,184.8 | +1,121.3 | +0.918 | +20,564 |
| es | 3M | sizing | 175 | -0.003% | -164.7 | -17.1 | +26.0 | -0.189 | -790 |
| es | 3M | product | 133 | -0.133% | -297.8 | +5.1 | +89.0 | -0.103 | -4,680 |
| var | 3M | sizing | 175 | -0.003% | -164.7 | -17.1 | +26.0 | -0.189 | -790 |
| var | 3M | product | 133 | -0.133% | -297.8 | +5.1 | +89.0 | -0.103 | -4,680 |
| drawdown | 3M | sizing | 175 | -0.003% | -164.7 | -17.1 | +26.0 | -0.189 | -790 |
| drawdown | 3M | product | 133 | -0.133% | -297.8 | +5.1 | +89.0 | -0.103 | -4,680 |
| duration | 3M | sizing | 4 | -0.000% | -0.0 | -0.0 | -0.2 | +0.008 | — |
| duration | 3M | product | 11 | +0.280% | -9.1 | +0.5 | -8.4 | -1.517 | — |
| curve | 3M | sizing | 4 | -0.000% | -0.0 | -0.0 | -0.2 | +0.008 | — |
| curve | 3M | product | 25 | +0.263% | -34.8 | +0.8 | -14.1 | -0.736 | -1,203 |
| credit | 3M | sizing | 36 | +0.027% | +1.1 | -0.3 | -4.8 | +0.111 | +30 |
| credit | 3M | product | 6 | +0.059% | +55.9 | +7.7 | +119.1 | -0.001 | — |
| fx | 3M | sizing | 2 | +0.000% | +0.0 | -0.0 | -0.0 | -0.002 | — |
| fx | 3M | product | 4 | +0.196% | -39.8 | +1.4 | -358.8 | +7.172 | — |
| commodity | 3M | sizing | 5 | -0.002% | +1.3 | -0.3 | +0.5 | +0.567 | — |
| commodity | 3M | product | 14 | +0.399% | -126.5 | +24.5 | -264.8 | +2.213 | — |
| crypto | 3M | sizing | 5 | -0.000% | +1.0 | -0.0 | -0.5 | +0.000 | — |
| crypto | 3M | product | 1 | -0.012% | -69.8 | -3.4 | -52.5 | +4.468 | — |
| volatility | 3M | sizing | 7 | -0.050% | +2.2 | -21.0 | -2.2 | +1.655 | — |
| volatility | 3M | product | 10 | +0.001% | +40.9 | -18.4 | +71.0 | +1.457 | — |

Hedge-size error = mean |ex-post optimal multiple − 1| of the package (lower = closer to the size that would have minimised the window's variance); negative Δ = the challenger's size was closer. Pieces are additive shares of the cell.

## Any better volatility model vs event itself (R = regression without event)

| Objective | Horizon | ΔU R − A (λ = 1) | ΔU B − R | ΔU with a conservative put skew (+5 vol) | ΔU without option cases | Baselines U: none / half / A / B |
|---|---|---|---|---|---|---|
| min_variance | 1W | -11 | +3 | -37 | +7 | +0 / +6,689 / +10,155 / +10,147 |
| target_vol | 1W | +367 | -11 | +335 | +342 | +0 / +3,839 / +6,595 / +6,951 |
| beta | 1W | -464 | +10 | -306 | -175 | +0 / +3,540 / +6,287 / +5,834 |
| systematic | 1W | -167 | +13 | -128 | -141 | +0 / +5,314 / +9,429 / +9,274 |
| sector | 1W | +3 | -1 | +2 | +2 | +0 / +4,401 / +7,464 / +7,466 |
| name | 1W | +20 | +3 | +28 | -1 | +0 / +4,975 / +9,490 / +9,514 |
| crash | 1W | +201 | -98 | -47 | +1 | +0 / +6,265 / +11,284 / +11,387 |
| es | 1W | +102 | -25 | +65 | +108 | +0 / +7,288 / +13,586 / +13,663 |
| var | 1W | +102 | -25 | +65 | +108 | +0 / +7,288 / +13,586 / +13,663 |
| drawdown | 1W | +102 | -25 | +65 | +108 | +0 / +7,288 / +13,586 / +13,663 |
| duration | 1W | +15 | +1 | +16 | +16 | +0 / +242 / +379 / +395 |
| curve | 1W | +15 | +1 | +16 | +16 | +0 / +200 / +328 / +344 |
| credit | 1W | +34 | -0 | +33 | +33 | +0 / +429 / +762 / +796 |
| fx | 1W | +23 | -0 | +22 | +22 | +0 / +118 / +223 / +245 |
| commodity | 1W | -5 | -0 | +6 | +0 | +0 / +296 / +455 / +451 |
| crypto | 1W | +20 | -0 | +20 | +20 | +0 / +21,428 / +37,830 / +37,849 |
| volatility | 1W | +165 | +23 | +161 | +36 | +0 / +908 / +1,760 / +1,948 |
| min_variance | 1M | +499 | -17 | +451 | +69 | +0 / +15,263 / +22,823 / +23,304 |
| target_vol | 1M | +524 | +16 | +471 | +185 | +0 / +10,406 / +17,899 / +18,438 |
| beta | 1M | -323 | +7 | -164 | -544 | +0 / +5,918 / +10,571 / +10,255 |
| systematic | 1M | -190 | +3 | -126 | -286 | +0 / +11,039 / +19,969 / +19,782 |
| sector | 1M | -40 | -2 | -42 | -42 | +0 / +10,572 / +18,366 / +18,323 |
| name | 1M | -10 | +4 | -12 | -12 | +0 / +10,130 / +19,061 / +19,054 |
| crash | 1M | -16 | +11 | +102 | +0 | +0 / +7,052 / +10,119 / +10,114 |
| es | 1M | +193 | +5 | +106 | -96 | +0 / +17,896 / +32,324 / +32,522 |
| var | 1M | +193 | +5 | +106 | -96 | +0 / +17,896 / +32,324 / +32,522 |
| drawdown | 1M | +193 | +5 | +106 | -96 | +0 / +17,896 / +32,324 / +32,522 |
| duration | 1M | -31 | -0 | -31 | -31 | +0 / +757 / +1,217 / +1,186 |
| curve | 1M | -29 | -0 | -29 | -29 | +0 / +737 / +1,226 / +1,197 |
| credit | 1M | -1 | -0 | -1 | -1 | +0 / +751 / +1,192 / +1,191 |
| fx | 1M | +6 | +0 | +6 | +6 | +0 / +439 / +844 / +850 |
| commodity | 1M | -19 | -0 | -10 | +6 | +0 / +521 / +733 / +714 |
| crypto | 1M | -0 | +0 | -0 | -0 | +0 / +51,402 / +97,338 / +97,338 |
| volatility | 1M | -95 | -2 | -118 | -3 | +0 / +565 / +1,003 / +906 |
| min_variance | 3M | -170 | -5 | -195 | -370 | +0 / +17,727 / +22,730 / +22,556 |
| target_vol | 3M | -597 | +60 | -603 | -685 | +0 / +11,224 / +18,107 / +17,570 |
| beta | 3M | -1,446 | -0 | -1,381 | -1,589 | +0 / +5,834 / +9,403 / +7,957 |
| systematic | 3M | -342 | -3 | -361 | -504 | +0 / +14,283 / +24,603 / +24,257 |
| sector | 3M | +160 | +1 | +161 | +161 | +0 / +10,850 / +17,463 / +17,624 |
| name | 3M | +79 | +0 | +70 | +5 | +0 / +13,361 / +25,194 / +25,274 |
| crash | 3M | +7,924 | -89 | +8,230 | +0 | +0 / +3,518 / -1,083 / +6,752 |
| es | 3M | -1,957 | -23 | -1,957 | -1,791 | +0 / +23,023 / +39,113 / +37,133 |
| var | 3M | -1,957 | -23 | -1,957 | -1,791 | +0 / +23,023 / +39,113 / +37,133 |
| drawdown | 3M | -1,957 | -23 | -1,957 | -1,791 | +0 / +23,023 / +39,113 / +37,133 |
| duration | 3M | -36 | +0 | -36 | -36 | +0 / +1,489 / +2,317 / +2,282 |
| curve | 3M | -91 | +0 | -91 | -91 | +0 / +1,565 / +2,495 / +2,404 |
| credit | 3M | +34 | -0 | +34 | +34 | +0 / +1,670 / +2,725 / +2,759 |
| fx | 3M | -82 | +0 | -82 | -82 | +0 / +712 / +1,365 / +1,283 |
| commodity | 3M | -205 | +0 | -227 | +3 | +0 / +445 / +57 / -148 |
| crypto | 3M | -84 | +0 | -84 | -84 | +0 / +79,690 / +148,615 / +148,532 |
| volatility | 3M | +49 | +0 | +25 | +7 | +0 / +47 / -7 / +42 |


## Final decision

| Component | Historical result | Live-shadow eligible? | Production change? | Reason |
|---|---|---|---|---|
| Event volatility forecast | better out of sample at no horizon (G1) | no (as a hedge input) | no | a better forecast; it did not produce better realised hedges — kept as research information |
| Event hedge sizing | 5 of 9 variance-sensitive cells positive (ES / VaR / drawdown counted once), none significant after FDR | no | no | sizes rarely change and the changes do not pay |
| Event product selection | products change in up to 25% of cases; realised effects net to about zero; 13 of 33 hard-target cells positive, none significant | no | no | no significant or stable gain |
| Existing resizing challenger (2018 on) | ΔU median +0 across cells | unchanged (its own live shadow continues) | no | separate experiment; not improved by event |
| Event + resizing (2018 on) | ΔU median +0 across cells | no | no | the combination adds nothing the parts lack |
| Shaffer Hedge production (hedge-2) | no cell passes G1–G4 | — | no | unchanged: nothing passed the historical gates |
