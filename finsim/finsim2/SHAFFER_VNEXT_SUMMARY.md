# Shaffer vNext — master summary

Three separate research programs, each against fixed production on identical point-in-time records, unseen eras, the 2018 split, BH FDR and fixed gates. Production (shaffer-2.1, shaffer-alpha-2.1-production, the Directional research definition, hedge-2, the resizing live shadow, benchmark benchmark-2.1-2026-09-25) is unchanged; nothing is promoted. Details: `SHAFFER_ALPHA_VNEXT.md`, `SHAFFER_DIRECTIONAL_VNEXT.md`, `SHAFFER_HEDGE_VNEXT.md`.

| System | Horizon / Objective | Production metric | Best challenger | Challenger metric | Incremental improvement | Eras positive | FDR | Live shadow? | Promotion? |
|---|---|---|---|---|---|---|---|---|---|
| Alpha | 1D | rank IC +0.0411 (t +7.7) | global | rank IC +0.0379 | Δ rank IC -0.0032 (t -0.4) | 2/4 | ✗ | no | no |
| Alpha | 1W | rank IC +0.0376 (t +6.1) | global | rank IC +0.0515 | Δ rank IC +0.0139 (t +1.5) | 3/4 | ✗ | no | no |
| Alpha | 1M | rank IC +0.0187 (t +1.5) | global | rank IC +0.0421 | Δ rank IC +0.0234 (t +1.1) | 3/4 | ✗ | no | no |
| Alpha | 3M | rank IC -0.0084 (t -0.4) | global | rank IC +0.0398 | Δ rank IC +0.0482 (t +1.2) | 4/4 | ✗ | no | no |
| Alpha | 6M | rank IC -0.0183 (t -0.6) | global | rank IC +0.0308 | Δ rank IC +0.0491 (t +1.0) | 3/4 | ✗ | no | no |
| Alpha | 12M | rank IC +0.0033 (t +0.1) | sector | rank IC +0.0177 | Δ rank IC +0.0144 (t +0.2) | 2/4 | ✗ | no | no |
| Directional | 1D | prior-only Brier +0.2491 | compact | Brier +0.2490 | Brier gain vs prior +0.00008 (t +0.4) | 2/4 | ✗ | no | no |
| Directional | 1W | prior-only Brier +0.2471 | compact | Brier +0.2473 | Brier gain vs prior -0.00018 (t -1.2) | 2/4 | ✗ | no | no |
| Directional | 1M | — | — | — | not run: no 1D or 1W challenger passed every gate (rule fixed in advance) | — | — | no | no |
| Hedge | min_variance 1W | U(hedge-2) +10,155 | hedge-3-sizing-regime-exp | U +10,176 | ΔU(λ=1) +21 (t +0.2) | 2/4 | ✗ | no | no |
| Hedge | min_variance 1M | U(hedge-2) +22,823 | hedge-3-sizing-obj-exp | U +22,823 | ΔU(λ=1) +0 (t —) | 0/4 | ✗ | no | no |
| Hedge | min_variance 3M | U(hedge-2) +22,730 | hedge-3-sizing-regime-exp | U +22,730 | ΔU(λ=1) +0 (t —) | 0/4 | ✗ | no | no |
| Hedge | target_vol 1W | U(hedge-2) +6,595 | hedge-3-sizing-obj-exp | U +7,498 | ΔU(λ=1) +903 (t +4.5) | 3/4 | ✓ | no | no |
| Hedge | target_vol 1M | U(hedge-2) +17,899 | hedge-3-sizing-obj-exp | U +19,395 | ΔU(λ=1) +1,496 (t +3.2) | 3/4 | ✓ | no | no |
| Hedge | target_vol 3M | U(hedge-2) +18,107 | hedge-3-sizing-obj-exp | U +18,757 | ΔU(λ=1) +650 (t +1.6) | 1/4 | ✗ | no | no |
| Hedge | beta 1W | U(hedge-2) +6,287 | hedge-3-sizing-obj-exp | U +7,346 | ΔU(λ=1) +1,058 (t +2.5) | 3/4 | ✓ | no | no |
| Hedge | beta 1M | U(hedge-2) +10,571 | hedge-3-sizing-regime-exp | U +11,309 | ΔU(λ=1) +738 (t +1.0) | 2/4 | ✗ | no | no |
| Hedge | beta 3M | U(hedge-2) +9,403 | hedge-3-sizing-obj-exp | U +9,935 | ΔU(λ=1) +532 (t +0.6) | 2/4 | ✗ | no | no |
| Hedge | systematic 1W | U(hedge-2) +9,429 | hedge-3-sizing-obj-exp | U +10,741 | ΔU(λ=1) +1,313 (t +2.8) | 3/4 | ✓ | no | no |
| Hedge | systematic 1M | U(hedge-2) +19,969 | hedge-3-sizing-obj-exp | U +21,710 | ΔU(λ=1) +1,740 (t +1.9) | 3/4 | ✓ | no | no |
| Hedge | systematic 3M | U(hedge-2) +24,603 | hedge-3-sizing-obj-exp | U +26,925 | ΔU(λ=1) +2,322 (t +2.2) | 2/4 | ✓ | no | no |
| Hedge | sector 1W | U(hedge-2) +7,464 | hedge-3-sizing-obj-exp | U +8,125 | ΔU(λ=1) +661 (t +3.1) | 3/4 | ✓ | no | no |
| Hedge | sector 1M | U(hedge-2) +18,366 | hedge-3-sizing-regime-exp | U +19,327 | ΔU(λ=1) +962 (t +1.3) | 2/4 | ✗ | no | no |
| Hedge | sector 3M | U(hedge-2) +17,463 | hedge-3-sizing-regime-exp | U +17,418 | ΔU(λ=1) -45 (t -0.5) | 0/4 | ✗ | no | no |
| Hedge | crash 1W | U(hedge-2) +11,284 | hedge-3-sizing-obj-exp | U +13,498 | ΔU(λ=1) +2,214 (t +1.6) | 3/4 | ✗ | no | no |
| Hedge | crash 1M | U(hedge-2) +10,119 | hedge-3-sizing-regime-exp | U +10,637 | ΔU(λ=1) +519 (t +0.3) | 1/4 | ✗ | no | no |
| Hedge | crash 3M | U(hedge-2) -1,083 | hedge-3-risk-exp | U +4,979 | ΔU(λ=1) +6,062 (t +2.2) | 4/4 | ✓ | no | no |
| Hedge | es 1W | U(hedge-2) +13,586 | hedge-3-sizing-obj-exp | U +17,602 | ΔU(λ=1) +4,016 (t +3.5) | 3/4 | ✓ | no | no |
| Hedge | es 1M | U(hedge-2) +32,324 | hedge-3-sizing-obj-exp | U +38,150 | ΔU(λ=1) +5,826 (t +3.0) | 3/4 | ✓ | no | no |
| Hedge | es 3M | U(hedge-2) +39,113 | hedge-3-sizing-obj-exp | U +42,976 | ΔU(λ=1) +3,863 (t +0.8) | 1/4 | ✗ | no | no |
| Hedge | duration 1W | U(hedge-2) +379 | hedge-3-risk-exp | U +421 | ΔU(λ=1) +41 (t +1.1) | 1/4 | ✗ | no | no |
| Hedge | duration 1M | U(hedge-2) +1,217 | hedge-3-risk-exp | U +1,214 | ΔU(λ=1) -3 (t -0.0) | 3/4 | ✗ | no | no |
| Hedge | duration 3M | U(hedge-2) +2,317 | hedge-3-sizing-regime-exp | U +2,335 | ΔU(λ=1) +18 (t +1.4) | 0/4 | ✗ | no | no |
| Hedge | credit 1W | U(hedge-2) +762 | hedge-3-sizing-obj-exp | U +1,013 | ΔU(λ=1) +251 (t +3.0) | 3/4 | ✓ | no | no |
| Hedge | credit 1M | U(hedge-2) +1,192 | hedge-3-risk-exp | U +1,211 | ΔU(λ=1) +19 (t +0.2) | 2/4 | ✗ | no | no |
| Hedge | credit 3M | U(hedge-2) +2,725 | hedge-3-risk-exp | U +3,324 | ΔU(λ=1) +599 (t +0.9) | 3/4 | ✗ | no | no |
| Hedge | fx 1W | U(hedge-2) +171 | hedge-3-product-exp | U +220 | ΔU(λ=1) +49 (t +1.8) | 3/4 | ✗ | no | no |
| Hedge | fx 1M | U(hedge-2) +844 | hedge-3-sizing-obj-exp | U +981 | ΔU(λ=1) +137 (t +1.7) | 3/4 | ✗ | no | no |
| Hedge | fx 3M | U(hedge-2) +1,365 | hedge-3-sizing-obj-exp | U +1,471 | ΔU(λ=1) +106 (t +0.4) | 1/2 | ✗ | no | no |

Alpha rows show the challenger with the best paired t (ties to the best evidence, not the best point estimate); Hedge rows the experiment with the largest ΔU at λ = 1 for that objective and horizon. "Promotion" is always no in this phase: promotion needs the live shadow (≥ 60 graded paired outcomes) and your explicit approval.

