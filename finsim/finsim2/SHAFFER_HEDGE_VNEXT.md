# Shaffer Hedge vNext — research

Run 2026-09-26 16:29:51 · 342.3 s · `python -m finsim2 lab --vnext hedge`. Research only: hedge-2, its sizing and the resizing live shadow are unchanged. Protocol and gates were committed before the runs (`hedge/hedgenext.py`). Utility U = risk reduction − λ·profit sacrificed − cost ($ per $1M book over the horizon), ΔU against hedge-2 on identical cases, date-clustered bootstrap, BH FDR across every hedge test. Options remain MODEL-PRICED — FLAT VOLATILITY ASSUMPTION.

## Summary

- **Tested:** 5 experiments, 173 objective × horizon cells with enough dates — Risk estimation — EWMA covariance (full-chain replay); Sizing — capped multiple per objective; Sizing — capped multiple per objective × volatility regime; Product choice — learned product type per objective × regime; Alpha → hedge — hedge size scaled by validated 1W Alpha.
- **Improved (H1–H5 incl. FDR):** nothing.
- **Production eligibility:** none (live shadow first).
- **Survived FDR but failed a fixed gate:** 31 cells — failing H2 ×7, H4 ×31. They are listed below; under the committed protocol none of them is eligible for live shadow.

## Survived FDR but failed a fixed gate

| Experiment | Objective | Horizon | Dates | ΔU λ=0.5 | ΔU λ=1 | ΔU λ=2 | ΔU λ=5 | ΔU λ=10 | t | Eras + | Cost (hedge-2 → challenger) | Basis error (hedge-2 → challenger) | Failed |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| hedge-3-sizing-obj-exp | name | 1W | 446 | +2,772 | +2,493 | +1,936 | +264 | -2,523 | +6.86 | 3/4 | +396 → +535 | +2,489 → +4,060 | H4 |
| hedge-3-sizing-regime-exp | name | 1W | 446 | +2,772 | +2,493 | +1,936 | +264 | -2,523 | +6.86 | 3/4 | +396 → +535 | +2,489 → +4,060 | H4 |
| hedge-3-sizing-regime-exp | target_vol | 1W | 446 | +964 | +805 | +485 | -475 | -2,074 | +5.26 | 3/4 | +49 → +59 | +3,856 → +3,834 | H4 |
| hedge-3-sizing-obj-exp | target_vol | 1W | 446 | +1,115 | +903 | +479 | -792 | -2,911 | +4.49 | 3/4 | +49 → +58 | +3,856 → +3,850 | H4 |
| hedge-3-sizing-obj-exp | es | 1W | 446 | +4,278 | +4,016 | +3,493 | +1,925 | -688 | +3.53 | 3/4 | +35 → +46 | +3,429 → +3,430 | H4 |
| hedge-3-sizing-obj-exp | var | 1W | 446 | +4,278 | +4,016 | +3,493 | +1,925 | -688 | +3.53 | 3/4 | +35 → +46 | +3,429 → +3,430 | H4 |
| hedge-3-sizing-obj-exp | drawdown | 1W | 446 | +4,278 | +4,016 | +3,493 | +1,925 | -688 | +3.53 | 3/4 | +35 → +46 | +3,429 → +3,430 | H4 |
| hedge-3-sizing-regime-exp | es | 1W | 446 | +4,278 | +4,016 | +3,493 | +1,925 | -688 | +3.53 | 3/4 | +35 → +46 | +3,429 → +3,430 | H4 |
| hedge-3-sizing-regime-exp | var | 1W | 446 | +4,278 | +4,016 | +3,493 | +1,925 | -688 | +3.53 | 3/4 | +35 → +46 | +3,429 → +3,430 | H4 |
| hedge-3-sizing-regime-exp | drawdown | 1W | 446 | +4,278 | +4,016 | +3,493 | +1,925 | -688 | +3.53 | 3/4 | +35 → +46 | +3,429 → +3,430 | H4 |
| hedge-3-sizing-obj-exp | name | 1M | 212 | +3,177 | +2,429 | +932 | -3,556 | -11,036 | +3.20 | 3/4 | +533 → +734 | +2,819 → +4,530 | H4 |
| hedge-3-sizing-obj-exp | target_vol | 1M | 212 | +2,042 | +1,496 | +403 | -2,874 | -8,337 | +3.17 | 3/4 | +28 → +21 | +3,530 → +3,797 | H4 |
| hedge-3-sizing-obj-exp | sector | 1W | 446 | +804 | +661 | +374 | -488 | -1,924 | +3.15 | 3/4 | +677 → +813 | +5,968 → +6,875 | H4 |
| hedge-3-sizing-regime-exp | sector | 1W | 446 | +805 | +647 | +333 | -611 | -2,183 | +3.11 | 3/4 | +677 → +821 | +5,968 → +6,944 | H4 |
| hedge-3-sizing-obj-exp | credit | 1W | 446 | +249 | +251 | +255 | +265 | +283 | +2.99 | 3/4 | +146 → +203 | +1,558 → +2,077 | H4 |
| hedge-3-sizing-regime-exp | target_vol | 1M | 212 | +1,832 | +1,395 | +521 | -2,102 | -6,472 | +2.98 | 2/4 | +28 → +20 | +3,530 → +3,830 | H2, H4 |
| hedge-3-sizing-regime-exp | systematic | 1W | 446 | +1,649 | +1,309 | +630 | -1,409 | -4,807 | +2.96 | 3/4 | +329 → +445 | +3,162 → +5,160 | H4 |
| hedge-3-sizing-obj-exp | es | 1M | 212 | +6,532 | +5,826 | +4,414 | +180 | -6,878 | +2.96 | 3/4 | +9 → -2 | +3,092 → +3,490 | H4 |
| hedge-3-sizing-obj-exp | var | 1M | 212 | +6,532 | +5,826 | +4,414 | +180 | -6,878 | +2.96 | 3/4 | +9 → -2 | +3,092 → +3,490 | H4 |
| hedge-3-sizing-obj-exp | drawdown | 1M | 212 | +6,532 | +5,826 | +4,414 | +180 | -6,878 | +2.96 | 3/4 | +9 → -2 | +3,092 → +3,490 | H4 |
| hedge-3-sizing-obj-exp | systematic | 1W | 446 | +1,667 | +1,313 | +603 | -1,525 | -5,073 | +2.83 | 3/4 | +329 → +442 | +3,162 → +5,208 | H4 |
| hedge-3-sizing-regime-exp | es | 1M | 212 | +6,215 | +5,694 | +4,653 | +1,529 | -3,678 | +2.80 | 2/4 | +9 → -8 | +3,092 → +3,469 | H2, H4 |
| hedge-3-sizing-regime-exp | var | 1M | 212 | +6,215 | +5,694 | +4,653 | +1,529 | -3,678 | +2.80 | 2/4 | +9 → -8 | +3,092 → +3,469 | H2, H4 |
| hedge-3-sizing-regime-exp | drawdown | 1M | 212 | +6,215 | +5,694 | +4,653 | +1,529 | -3,678 | +2.80 | 2/4 | +9 → -8 | +3,092 → +3,469 | H2, H4 |
| hedge-3-sizing-regime-exp | name | 1M | 212 | +2,379 | +1,821 | +707 | -2,636 | -8,207 | +2.55 | 2/4 | +533 → +696 | +2,819 → +4,303 | H2, H4 |
| hedge-3-sizing-obj-exp | beta | 1W | 446 | +1,436 | +1,058 | +304 | -1,960 | -5,733 | +2.52 | 3/4 | +44 → +54 | +2,000 → +3,726 | H4 |
| hedge-3-sizing-regime-exp | beta | 1W | 446 | +1,436 | +1,058 | +304 | -1,960 | -5,733 | +2.52 | 3/4 | +44 → +54 | +2,000 → +3,726 | H4 |
| hedge-3-risk-exp | crash | 3M | 70 | +4,692 | +6,062 | +8,802 | +17,021 | +30,719 | +2.24 | 4/4 | +5,299 → +4,792 | +1,873 → +2,142 | H4 |
| hedge-3-sizing-obj-exp | systematic | 3M | 70 | +3,443 | +2,322 | +82 | -6,640 | -17,844 | +2.22 | 2/4 | +1,246 → +1,578 | +3,483 → +4,717 | H2, H4 |
| hedge-3-sizing-obj-exp | systematic | 1M | 212 | +2,633 | +1,740 | -45 | -5,402 | -14,329 | +1.88 | 3/4 | +593 → +832 | +3,315 → +5,381 | H4 |
| hedge-3-sizing-obj-exp | name | 3M | 70 | +5,944 | +4,252 | +869 | -9,280 | -26,196 | +1.79 | 2/4 | +1,349 → +1,865 | +2,622 → +3,836 | H2, H4 |

H4 (fixed in advance) allows at most +10% cost and +5% basis error against hedge-2. Every sizing survivor learned a larger hedge (usually the 1.5× cap), which is why cost and basis error rise: history says hedge-2 under-hedges these objectives when risk matters as much as profit (λ ≤ 2), and the gain turns negative for profit-sensitive users (λ ≥ 5–10). That is a preference trade-off, not a free improvement.

## Risk estimation — EWMA covariance (full-chain replay) (`hedge-3-risk-exp`)

| Objective | Horizon | Dates | ΔU λ=0.5 | ΔU λ=1 (95% CI) | ΔU λ=2 | ΔU λ=5 | ΔU λ=10 | t | Eras + | Tail (ES95 Δ) | Cost Δ | Decisions changed | Without crises | Put skew +5 vol | No options | FDR | Status |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| min_variance | 1W | 446 | -488 | -360 ([-913, +141]) | -103 | +666 | +1,949 | -1.34 | 1/4 | -849 | -28.7 | 72% | -680 | -376 | -359 | ✗ | NO HEDGE IMPROVEMENT |
| target_vol | 1W | 446 | -248 | -90 ([-950, +1,046]) | +227 | +1,175 | +2,756 | -0.18 | 1/4 | +1,235 | -23.3 | 58% | -896 | -117 | -165 | ✗ | NO HEDGE IMPROVEMENT |
| beta | 1W | 446 | -455 | -474 ([-1,207, +39]) | -512 | -627 | -818 | -1.49 | 1/4 | -1,312 | +6.3 | 39% | -184 | -329 | -172 | ✗ | NO HEDGE IMPROVEMENT |
| systematic | 1W | 446 | -144 | -145 ([-378, +55]) | -148 | -156 | -170 | -1.32 | 2/4 | -177 | -18.2 | 59% | -76 | -97 | -12 | ✗ | NO HEDGE IMPROVEMENT |
| sector | 1W | 446 | -25 | -20 ([-97, +45]) | -11 | +17 | +63 | -0.55 | 1/4 | +17 | -5.6 | 88% | -56 | -20 | -20 | ✗ | NO HEDGE IMPROVEMENT |
| name | 1W | 446 | -19 | -30 ([-101, +40]) | -51 | -116 | -224 | -0.82 | 2/4 | -135 | -15.5 | 42% | -45 | -12 | -30 | ✗ | NO HEDGE IMPROVEMENT |
| crash | 1W | 446 | -1,274 | -1,206 ([-2,850, +103]) | -1,070 | -662 | +18 | -1.60 | 1/4 | -1,391 | -49.4 | 23% | -1,025 | -2,158 | +89 | ✗ | NO HEDGE IMPROVEMENT |
| es | 1W | 446 | +195 | +260 ([-1,218, +1,572]) | +390 | +779 | +1,427 | +0.37 | 2/4 | +118 | -11.7 | 60% | -873 | +236 | +183 | ✗ | NO HEDGE IMPROVEMENT |
| var | 1W | 446 | +195 | +260 ([-1,218, +1,572]) | +390 | +779 | +1,427 | +0.37 | 2/4 | +118 | -11.7 | 60% | -873 | +236 | +183 | ✗ | NO HEDGE IMPROVEMENT |
| drawdown | 1W | 446 | +195 | +260 ([-1,218, +1,572]) | +390 | +779 | +1,427 | +0.37 | 2/4 | +118 | -11.7 | 60% | -873 | +236 | +183 | ✗ | NO HEDGE IMPROVEMENT |
| duration | 1W | 446 | +50 | +41 ([-16, +132]) | +23 | -31 | -121 | +1.09 | 1/4 | -42 | -0.6 | 40% | +69 | +41 | +41 | ✗ | NO HEDGE IMPROVEMENT |
| curve | 1W | 446 | +51 | +39 ([-29, +128]) | +15 | -58 | -179 | +0.97 | 2/4 | +25 | -0.6 | 38% | +51 | +39 | +39 | ✗ | NO HEDGE IMPROVEMENT |
| credit | 1W | 446 | +125 | +90 ([-60, +224]) | +19 | -193 | -546 | +1.24 | 2/4 | +6 | -1.7 | 98% | -25 | +90 | +90 | ✗ | NO HEDGE IMPROVEMENT |
| fx | 1W | 446 | -3 | -1 ([-27, +22]) | +3 | +16 | +38 | -0.07 | 2/4 | +49 | +0.3 | 11% | +9 | -1 | -1 | ✗ | NO HEDGE IMPROVEMENT |
| commodity | 1W | 446 | -78 | -88 ([-251, +41]) | -106 | -163 | -258 | -1.17 | 0/4 | -788 | -24.2 | 30% | -163 | +34 | +8 | ✗ | NO HEDGE IMPROVEMENT |
| crypto | 1W | 111 | -256 | -373 ([-1,283, +20]) | -607 | -1,310 | -2,482 | -1.12 | 0/1 | -1 | +0.6 | 59% | -452 | -373 | -373 | ✗ | NO HEDGE IMPROVEMENT |
| volatility | 1W | 207 | +165 | +182 ([-124, +532]) | +216 | +318 | +489 | +1.09 | 1/2 | +616 | +3.6 | 26% | -38 | +218 | -41 | ✗ | NO HEDGE IMPROVEMENT |
| min_variance | 1M | 212 | -814 | -616 ([-2,080, +1,006]) | -221 | +967 | +2,947 | -0.78 | 2/4 | -203 | -51.7 | 73% | -1,550 | -626 | -1,192 | ✗ | NO HEDGE IMPROVEMENT |
| target_vol | 1M | 212 | -1,328 | -926 ([-2,584, +780]) | -122 | +2,291 | +6,312 | -1.08 | 1/4 | -1,530 | -97.1 | 66% | -1,837 | -1,017 | -1,284 | ✗ | NO HEDGE IMPROVEMENT |
| beta | 1M | 212 | -215 | -47 ([-699, +532]) | +288 | +1,293 | +2,968 | -0.15 | 2/4 | +935 | +135.1 | 42% | -443 | +84 | -245 | ✗ | NO HEDGE IMPROVEMENT |
| systematic | 1M | 212 | -27 | +23 ([-352, +331]) | +122 | +421 | +918 | +0.13 | 2/4 | +533 | +51.5 | 64% | -274 | +96 | +9 | ✗ | NO HEDGE IMPROVEMENT |
| sector | 1M | 212 | +102 | +77 ([-152, +344]) | +27 | -121 | -369 | +0.61 | 2/4 | +284 | -17.7 | 86% | -16 | +77 | +77 | ✗ | NO HEDGE IMPROVEMENT |
| name | 1M | 212 | +112 | +63 ([-97, +339]) | -34 | -326 | -812 | +0.57 | 1/4 | +67 | -7.6 | 30% | -60 | +7 | -14 | ✗ | NO HEDGE IMPROVEMENT |
| crash | 1M | 212 | -377 | -336 ([-2,349, +888]) | -254 | -6 | +407 | -0.41 | 3/4 | -473 | -54.2 | 22% | +50 | -814 | -263 | ✗ | NO HEDGE IMPROVEMENT |
| es | 1M | 212 | -976 | -933 ([-3,778, +2,065]) | -849 | -595 | -172 | -0.63 | 3/4 | -1,072 | -54.1 | 65% | -1,712 | -1,088 | -1,334 | ✗ | NO HEDGE IMPROVEMENT |
| var | 1M | 212 | -976 | -933 ([-3,778, +2,065]) | -849 | -595 | -172 | -0.63 | 3/4 | -1,072 | -54.1 | 65% | -1,712 | -1,088 | -1,334 | ✗ | NO HEDGE IMPROVEMENT |
| drawdown | 1M | 212 | -976 | -933 ([-3,778, +2,065]) | -849 | -595 | -172 | -0.63 | 3/4 | -1,072 | -54.1 | 65% | -1,712 | -1,088 | -1,334 | ✗ | NO HEDGE IMPROVEMENT |
| duration | 1M | 212 | +6 | -3 ([-140, +117]) | -21 | -76 | -168 | -0.04 | 3/4 | -4 | +3.0 | 34% | -60 | -3 | -3 | ✗ | NO HEDGE IMPROVEMENT |
| curve | 1M | 212 | -30 | -32 ([-154, +105]) | -36 | -49 | -69 | -0.49 | 2/4 | -219 | +3.3 | 33% | -89 | -32 | -32 | ✗ | NO HEDGE IMPROVEMENT |
| credit | 1M | 212 | -13 | +19 ([-168, +252]) | +83 | +276 | +598 | +0.17 | 2/4 | -175 | -1.3 | 97% | +55 | +19 | +19 | ✗ | NO HEDGE IMPROVEMENT |
| fx | 1M | 212 | +11 | +10 ([-46, +56]) | +7 | +0 | -11 | +0.38 | 2/4 | -67 | +1.8 | 14% | -24 | +10 | +10 | ✗ | NO HEDGE IMPROVEMENT |
| commodity | 1M | 212 | -279 | -315 ([-944, +73]) | -385 | -595 | -947 | -1.21 | 3/4 | -1,474 | -25.6 | 22% | +0 | -238 | +3 | ✗ | NO HEDGE IMPROVEMENT |
| crypto | 1M | 52 | -2 | -2 ([-4, -0]) | -1 | -0 | +2 | -1.66 | 0/1 | -3 | -0.1 | 56% | -2 | -2 | -2 | ✗ | INSUFFICIENT DATA |
| volatility | 1M | 98 | -27 | +142 ([-195, +506]) | +481 | +1,497 | +3,191 | +0.80 | 1/2 | -213 | -5.8 | 29% | +216 | +192 | +13 | ✗ | NO HEDGE IMPROVEMENT |
| min_variance | 3M | 70 | -2,465 | -2,413 ([-3,939, -1,114]) | -2,310 | -2,000 | -1,484 | -3.35 | 0/4 | -3,653 | -0.7 | 72% | -2,493 | -2,632 | -2,467 | ✗ | NO HEDGE IMPROVEMENT |
| target_vol | 3M | 70 | -6,978 | -6,708 ([-12,311, -2,192]) | -6,169 | -4,552 | -1,856 | -2.60 | 0/4 | -19,286 | -57.5 | 68% | -7,777 | -6,870 | -7,123 | ✗ | NO HEDGE IMPROVEMENT |
| beta | 3M | 70 | -2,019 | -2,504 ([-5,252, -736]) | -3,476 | -6,390 | -11,246 | -2.17 | 0/4 | -7,215 | +378.1 | 38% | -3,034 | -2,439 | -2,599 | ✗ | NO HEDGE IMPROVEMENT |
| systematic | 3M | 70 | -565 | -613 ([-1,709, +391]) | -710 | -999 | -1,481 | -1.15 | 1/4 | -429 | +61.4 | 64% | -792 | -624 | -411 | ✗ | NO HEDGE IMPROVEMENT |
| sector | 3M | 70 | -585 | -196 ([-925, +852]) | +582 | +2,918 | +6,810 | -0.43 | 2/4 | -395 | -16.3 | 86% | -122 | -196 | -196 | ✗ | NO HEDGE IMPROVEMENT |
| name | 3M | 70 | +526 | +460 ([-96, +1,000]) | +327 | -72 | -737 | +1.64 | 2/4 | -248 | +7.3 | 26% | +136 | +409 | -103 | ✗ | NO HEDGE IMPROVEMENT |
| crash | 3M | 70 | +4,692 | +6,062 ([+1,531, +12,153]) | +8,802 | +17,021 | +30,719 | +2.24 | 4/4 | +2,816 | -506.5 | 27% | +7,102 | +6,160 | -533 | ✓ | NO HEDGE IMPROVEMENT |
| es | 3M | 70 | -5,105 | -4,941 ([-10,880, +653]) | -4,614 | -3,634 | -1,999 | -1.68 | 2/4 | -5,314 | -46.2 | 63% | -4,635 | -4,915 | -4,619 | ✗ | NO HEDGE IMPROVEMENT |
| var | 3M | 70 | -5,105 | -4,941 ([-10,880, +653]) | -4,614 | -3,634 | -1,999 | -1.68 | 2/4 | -5,314 | -46.2 | 63% | -4,635 | -4,915 | -4,619 | ✗ | NO HEDGE IMPROVEMENT |
| drawdown | 3M | 70 | -5,105 | -4,941 ([-10,880, +653]) | -4,614 | -3,634 | -1,999 | -1.68 | 2/4 | -5,314 | -46.2 | 63% | -4,635 | -4,915 | -4,619 | ✗ | NO HEDGE IMPROVEMENT |
| duration | 3M | 70 | -112 | -220 ([-449, +83]) | -436 | -1,085 | -2,167 | -1.62 | 2/4 | +138 | +7.4 | 34% | -169 | -220 | -220 | ✗ | NO HEDGE IMPROVEMENT |
| curve | 3M | 70 | -117 | -167 ([-531, +120]) | -267 | -566 | -1,066 | -1.00 | 2/4 | +98 | +8.5 | 31% | -131 | -167 | -167 | ✗ | NO HEDGE IMPROVEMENT |
| credit | 3M | 70 | +204 | +599 ([-409, +2,124]) | +1,387 | +3,753 | +7,696 | +0.93 | 3/4 | +3,072 | +18.8 | 99% | +535 | +599 | +599 | ✗ | NO HEDGE IMPROVEMENT |
| fx | 3M | 70 | -159 | -205 ([-520, +12]) | -299 | -580 | -1,048 | -1.51 | 1/2 | -326 | +10.4 | 19% | -258 | -205 | -205 | ✗ | NO HEDGE IMPROVEMENT |
| commodity | 3M | 70 | +224 | +272 ([-349, +1,075]) | +367 | +652 | +1,126 | +0.75 | 2/4 | +1,982 | -10.2 | 31% | +300 | +381 | +2 | ✗ | NO HEDGE IMPROVEMENT |
| crypto | 3M | 16 | +4,600 | +7,568 (—) | +13,505 | +31,316 | +61,000 | — | 0/0 | — | -7.6 | 53% | +9,132 | +7,568 | +7,568 | ✗ | INSUFFICIENT DATA |
| volatility | 3M | 31 | -150 | -13 ([-219, +380]) | +262 | +1,086 | +2,460 | -0.08 | 0/1 | -381 | -85.3 | 36% | +2 | -23 | +15 | ✗ | INSUFFICIENT DATA |

ΔU > 0: the challenger's realised utility beats hedge-2's. Tail (ES95 Δ) > 0: the challenger's hedged 95% expected shortfall is smaller (better). Cost Δ > 0: the challenger costs more. "Without crises" drops 2009, Feb–Jun 2020 and 2022; "Put skew +5 vol" charges every bought put 5 implied-vol points more; "No options" keeps only cases where neither package holds an option. Each objective is judged on its own; cells are never pooled across objectives.

## Sizing — capped multiple per objective (`hedge-3-sizing-obj-exp`)

| Objective | Horizon | Dates | ΔU λ=0.5 | ΔU λ=1 (95% CI) | ΔU λ=2 | ΔU λ=5 | ΔU λ=10 | t | Eras + | Tail (ES95 Δ) | Cost Δ | Decisions changed | Without crises | Put skew +5 vol | No options | FDR | Status |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| min_variance | 1W | 446 | -52 | -46 ([-116, +3]) | -33 | +4 | +67 | -1.51 | 0/4 | -165 | -1.3 | 26% | -59 | -47 | -46 | ✗ | NO HEDGE IMPROVEMENT |
| target_vol | 1W | 446 | +1,115 | +903 ([+478, +1,266]) | +479 | -792 | -2,911 | +4.49 | 3/4 | +2,252 | +9.4 | 46% | +635 | +901 | +901 | ✓ | NO HEDGE IMPROVEMENT |
| beta | 1W | 446 | +1,436 | +1,058 ([+309, +1,955]) | +304 | -1,960 | -5,733 | +2.52 | 3/4 | +3,947 | +10.6 | 78% | +439 | +916 | +947 | ✓ | NO HEDGE IMPROVEMENT |
| systematic | 1W | 446 | +1,667 | +1,313 ([+460, +2,279]) | +603 | -1,525 | -5,073 | +2.83 | 3/4 | +3,835 | +113.8 | 79% | +711 | +1,200 | +1,310 | ✓ | NO HEDGE IMPROVEMENT |
| sector | 1W | 446 | +804 | +661 ([+280, +1,102]) | +374 | -488 | -1,924 | +3.15 | 3/4 | +2,255 | +136.1 | 79% | +260 | +661 | +661 | ✓ | NO HEDGE IMPROVEMENT |
| name | 1W | 446 | +2,772 | +2,493 ([+1,829, +3,255]) | +1,936 | +264 | -2,523 | +6.86 | 3/4 | +7,038 | +139.1 | 78% | +2,042 | +2,322 | +2,477 | ✓ | NO HEDGE IMPROVEMENT |
| crash | 1W | 446 | +2,577 | +2,214 ([-210, +5,194]) | +1,486 | -696 | -4,334 | +1.61 | 3/4 | +3,208 | +266.7 | 78% | -44 | +1,583 | +2,393 | ✗ | NO HEDGE IMPROVEMENT |
| es | 1W | 446 | +4,278 | +4,016 ([+1,984, +6,442]) | +3,493 | +1,925 | -688 | +3.53 | 3/4 | +4,550 | +10.6 | 59% | +2,014 | +4,014 | +4,014 | ✓ | NO HEDGE IMPROVEMENT |
| var | 1W | 446 | +4,278 | +4,016 ([+1,984, +6,442]) | +3,493 | +1,925 | -688 | +3.53 | 3/4 | +4,550 | +10.6 | 59% | +2,014 | +4,014 | +4,014 | ✓ | NO HEDGE IMPROVEMENT |
| drawdown | 1W | 446 | +4,278 | +4,016 ([+1,984, +6,442]) | +3,493 | +1,925 | -688 | +3.53 | 3/4 | +4,550 | +10.6 | 59% | +2,014 | +4,014 | +4,014 | ✓ | NO HEDGE IMPROVEMENT |
| duration | 1W | 446 | -73 | -94 ([-185, -42]) | -134 | -256 | -458 | -2.57 | 0/4 | -163 | -3.9 | 83% | -114 | -94 | -94 | ✗ | NO HEDGE IMPROVEMENT |
| curve | 1W | 446 | -61 | -77 ([-156, -30]) | -109 | -204 | -364 | -2.39 | 0/4 | -162 | -2.1 | 83% | -92 | -77 | -77 | ✗ | NO HEDGE IMPROVEMENT |
| credit | 1W | 446 | +249 | +251 ([+76, +405]) | +255 | +265 | +283 | +2.99 | 3/4 | +766 | +57.0 | 84% | +162 | +251 | +251 | ✓ | NO HEDGE IMPROVEMENT |
| fx | 1W | 446 | +8 | +15 ([-63, +107]) | +28 | +68 | +134 | +0.34 | 2/4 | +12 | +3.4 | 84% | +49 | +15 | +15 | ✗ | NO HEDGE IMPROVEMENT |
| commodity | 1W | 446 | +29 | -18 ([-134, +122]) | -113 | -396 | -869 | -0.28 | 2/4 | +75 | +65.0 | 77% | +106 | -418 | -88 | ✗ | NO HEDGE IMPROVEMENT |
| crypto | 1W | 111 | +596 | +333 ([-906, +1,541]) | -194 | -1,775 | -4,409 | +0.53 | 0/1 | -164 | +59.1 | 44% | +333 | +333 | +333 | ✗ | NO HEDGE IMPROVEMENT |
| volatility | 1W | 207 | +304 | +274 ([-30, +666]) | +215 | +37 | -259 | +1.54 | 1/2 | +1,382 | +68.6 | 67% | +104 | -375 | -122 | ✗ | NO HEDGE IMPROVEMENT |
| min_variance | 1M | 212 | +0 | +0 ([+0, +0]) | +0 | +0 | +0 | — | 0/4 | +0 | +0.0 | 0% | +0 | +0 | +0 | ✗ | NO HEDGE IMPROVEMENT |
| target_vol | 1M | 212 | +2,042 | +1,496 ([+610, +2,461]) | +403 | -2,874 | -8,337 | +3.17 | 3/4 | +3,097 | -6.6 | 53% | +1,002 | +1,433 | +1,641 | ✓ | NO HEDGE IMPROVEMENT |
| beta | 1M | 212 | +1,683 | +665 ([-492, +2,588]) | -1,371 | -7,477 | -17,655 | +0.85 | 3/4 | +3,081 | -37.1 | 78% | -226 | +530 | +958 | ✗ | NO HEDGE IMPROVEMENT |
| systematic | 1M | 212 | +2,633 | +1,740 ([+315, +3,941]) | -45 | -5,402 | -14,329 | +1.88 | 3/4 | +5,476 | +238.6 | 79% | +813 | +1,493 | +2,092 | ✓ | NO HEDGE IMPROVEMENT |
| sector | 1M | 212 | +1,338 | +558 ([-709, +2,580]) | -1,003 | -5,686 | -13,490 | +0.67 | 2/4 | +4,926 | +440.3 | 79% | -560 | +558 | +558 | ✗ | NO HEDGE IMPROVEMENT |
| name | 1M | 212 | +3,177 | +2,429 ([+1,363, +4,335]) | +932 | -3,556 | -11,036 | +3.20 | 3/4 | +9,174 | +201.2 | 78% | +2,688 | +2,258 | +3,347 | ✓ | NO HEDGE IMPROVEMENT |
| crash | 1M | 212 | -211 | -1,262 ([-4,775, +2,828]) | -3,366 | -9,678 | -20,197 | -0.65 | 1/4 | +1,481 | +639.2 | 78% | -4,079 | -1,767 | -1,386 | ✗ | NO HEDGE IMPROVEMENT |
| es | 1M | 212 | +6,532 | +5,826 ([+2,314, +10,028]) | +4,414 | +180 | -6,878 | +2.96 | 3/4 | +7,227 | -10.7 | 70% | +1,909 | +5,714 | +6,137 | ✓ | NO HEDGE IMPROVEMENT |
| var | 1M | 212 | +6,532 | +5,826 ([+2,314, +10,028]) | +4,414 | +180 | -6,878 | +2.96 | 3/4 | +7,227 | -10.7 | 70% | +1,909 | +5,714 | +6,137 | ✓ | NO HEDGE IMPROVEMENT |
| drawdown | 1M | 212 | +6,532 | +5,826 ([+2,314, +10,028]) | +4,414 | +180 | -6,878 | +2.96 | 3/4 | +7,227 | -10.7 | 70% | +1,909 | +5,714 | +6,137 | ✓ | NO HEDGE IMPROVEMENT |
| duration | 1M | 212 | -254 | -255 ([-620, +13]) | -257 | -261 | -269 | -1.58 | 1/4 | -184 | -9.7 | 83% | -161 | -255 | -255 | ✗ | NO HEDGE IMPROVEMENT |
| curve | 1M | 212 | -184 | -173 ([-481, +55]) | -153 | -91 | +13 | -1.27 | 1/4 | -173 | -5.9 | 83% | -113 | -173 | -173 | ✗ | NO HEDGE IMPROVEMENT |
| credit | 1M | 212 | -210 | -253 ([-917, +153]) | -339 | -598 | -1,029 | -0.93 | 1/4 | -1,240 | +94.1 | 56% | +63 | -253 | -253 | ✗ | NO HEDGE IMPROVEMENT |
| fx | 1M | 212 | +129 | +137 ([+2, +312]) | +153 | +202 | +283 | +1.73 | 3/4 | +496 | +3.5 | 84% | +113 | +137 | +137 | ✗ | NO HEDGE IMPROVEMENT |
| commodity | 1M | 212 | -246 | -362 ([-802, -75]) | -594 | -1,290 | -2,449 | -1.95 | 1/4 | -904 | +32.1 | 77% | -500 | -406 | -51 | ✗ | NO HEDGE IMPROVEMENT |
| crypto | 1M | 52 | +2,663 | +3,002 ([-2,031, +9,904]) | +3,679 | +5,711 | +9,098 | +0.99 | 0/1 | +16,159 | +352.3 | 42% | +3,505 | +3,002 | +3,002 | ✗ | INSUFFICIENT DATA |
| volatility | 1M | 98 | -28 | -86 ([-295, +169]) | -202 | -550 | -1,130 | -0.73 | 0/2 | +306 | +7.7 | 22% | -119 | -204 | -98 | ✗ | NO HEDGE IMPROVEMENT |
| min_variance | 3M | 70 | -2,924 | -1,374 ([-3,825, +710]) | +1,725 | +11,023 | +26,520 | -1.19 | 0/4 | -2,521 | -61.0 | 55% | -1,481 | -1,363 | -1,686 | ✗ | NO HEDGE IMPROVEMENT |
| target_vol | 3M | 70 | +909 | +650 ([-98, +1,518]) | +132 | -1,422 | -4,012 | +1.58 | 1/4 | +104 | +14.7 | 25% | +490 | +650 | +776 | ✗ | NO HEDGE IMPROVEMENT |
| beta | 3M | 70 | +1,511 | +532 ([-1,088, +2,374]) | -1,426 | -7,301 | -17,091 | +0.60 | 2/4 | +2,280 | -93.5 | 56% | -144 | +465 | +651 | ✗ | NO HEDGE IMPROVEMENT |
| systematic | 3M | 70 | +3,443 | +2,322 ([+585, +4,684]) | +82 | -6,640 | -17,844 | +2.22 | 2/4 | +5,431 | +331.9 | 57% | +1,893 | +2,253 | +2,628 | ✓ | NO HEDGE IMPROVEMENT |
| sector | 3M | 70 | +446 | -273 ([-1,545, +1,486]) | -1,709 | -6,020 | -13,203 | -0.35 | 2/4 | +1,982 | +442.4 | 58% | -819 | -273 | -273 | ✗ | NO HEDGE IMPROVEMENT |
| name | 3M | 70 | +5,944 | +4,252 ([+479, +9,790]) | +869 | -9,280 | -26,196 | +1.79 | 2/4 | +13,355 | +516.3 | 56% | +3,410 | +4,129 | +4,579 | ✓ | NO HEDGE IMPROVEMENT |
| crash | 3M | 70 | +1,282 | +3,341 ([-4,567, +10,794]) | +7,458 | +19,808 | +40,392 | +0.85 | 2/4 | -2,210 | -1,433.7 | 56% | +1,335 | +3,376 | +4,409 | ✗ | NO HEDGE IMPROVEMENT |
| es | 3M | 70 | +5,551 | +3,863 ([-6,277, +12,357]) | +485 | -9,646 | -26,533 | +0.81 | 1/4 | +7,272 | +32.2 | 54% | +4,412 | +3,815 | +4,392 | ✗ | NO HEDGE IMPROVEMENT |
| var | 3M | 70 | +5,551 | +3,863 ([-6,277, +12,357]) | +485 | -9,646 | -26,533 | +0.81 | 1/4 | +7,272 | +32.2 | 54% | +4,412 | +3,815 | +4,392 | ✗ | NO HEDGE IMPROVEMENT |
| drawdown | 3M | 70 | +5,551 | +3,863 ([-6,277, +12,357]) | +485 | -9,646 | -26,533 | +0.81 | 1/4 | +7,272 | +32.2 | 54% | +4,412 | +3,815 | +4,392 | ✗ | NO HEDGE IMPROVEMENT |
| duration | 3M | 70 | -907 | -1,018 ([-2,323, +55]) | -1,239 | -1,903 | -3,010 | -1.68 | 1/4 | -2,077 | -19.2 | 62% | -730 | -1,018 | -1,018 | ✗ | NO HEDGE IMPROVEMENT |
| curve | 3M | 70 | -979 | -1,087 ([-2,404, -25]) | -1,302 | -1,949 | -3,028 | -1.79 | 1/4 | -2,103 | -16.8 | 62% | -825 | -1,087 | -1,087 | ✗ | NO HEDGE IMPROVEMENT |
| credit | 3M | 70 | +85 | +79 ([-600, +1,082]) | +68 | +35 | -21 | +0.18 | 1/4 | +2,208 | +274.2 | 64% | +167 | +79 | +79 | ✗ | NO HEDGE IMPROVEMENT |
| fx | 3M | 70 | +108 | +106 ([-434, +580]) | +102 | +89 | +69 | +0.41 | 1/2 | -533 | +2.9 | 69% | +55 | +106 | +106 | ✗ | NO HEDGE IMPROVEMENT |
| commodity | 3M | 70 | -588 | -675 ([-1,400, -218]) | -849 | -1,373 | -2,245 | -2.24 | 0/4 | -1,663 | -18.5 | 56% | -661 | -701 | -41 | ✗ | NO HEDGE IMPROVEMENT |
| crypto | 3M | 16 | +0 | +0 (—) | +0 | +0 | +0 | — | 0/0 | — | +0.0 | 0% | +0 | +0 | +0 | ✗ | INSUFFICIENT DATA |
| volatility | 3M | 31 | +0 | +0 ([+0, +0]) | +0 | +0 | +0 | — | 0/1 | +0 | +0.0 | 0% | +0 | +0 | +0 | ✗ | INSUFFICIENT DATA |

ΔU > 0: the challenger's realised utility beats hedge-2's. Tail (ES95 Δ) > 0: the challenger's hedged 95% expected shortfall is smaller (better). Cost Δ > 0: the challenger costs more. "Without crises" drops 2009, Feb–Jun 2020 and 2022; "Put skew +5 vol" charges every bought put 5 implied-vol points more; "No options" keeps only cases where neither package holds an option. Each objective is judged on its own; cells are never pooled across objectives.

Learned multiples (walk-forward, per era): min_variance 1W: 0.9, 1.0, 1.05; target_vol 1W: 1.0, 1.15, 1.25, 1.35, 1.5; beta 1W: 1.0, 1.5; systematic 1W: 1.0, 1.35, 1.45, 1.5; sector 1W: 1.0, 1.15, 1.35, 1.4; name 1W: 1.0, 1.5; crash 1W: 1.0, 1.5; es 1W: 1.0, 1.5; var 1W: 1.0, 1.5; drawdown 1W: 1.0, 1.5; duration 1W: 0.5, 0.8, 1.0, 1.05, 1.35; curve 1W: 0.5, 0.8, 1.0, 1.1, 1.5; credit 1W: 1.0, 1.45, 1.5; fx 1W: 1.0, 1.5; commodity 1W: 1.0, 1.3, 1.35, 1.5; crypto 1W: 1.0, 1.15; volatility 1W: 1.0, 1.5; min_variance 1M: 1.0; target_vol 1M: 1.0, 1.3, 1.35, 1.4; beta 1M: 1.0, 1.5; systematic 1M: 1.0, 1.5; sector 1M: 1.0, 1.4, 1.45, 1.5; name 1M: 1.0, 1.5; crash 1M: 1.0, 1.45, 1.5; es 1M: 1.0, 1.5; var 1M: 1.0, 1.5; drawdown 1M: 1.0, 1.5; duration 1M: 0.55, 0.65, 0.9, 1.0, 1.4; curve 1M: 0.5, 0.75, 0.95, 1.0, 1.5; credit 1M: 1.0, 1.4, 1.5; fx 1M: 1.0, 1.5; commodity 1M: 0.5, 1.0, 1.1, 1.15, 1.45; crypto 1M: 1.0, 1.3; volatility 1M: 1.0, 1.5; min_variance 3M: 0.75, 0.8, 0.85, 1.0; target_vol 3M: 1.0, 1.1, 1.2; beta 3M: 1.0, 1.15, 1.3, 1.35; systematic 3M: 1.0, 1.2, 1.35, 1.4; sector 3M: 1.0, 1.05, 1.3, 1.35; name 3M: 1.0, 1.5; crash 3M: 0.5, 0.55, 0.65, 1.0; es 3M: 1.0, 1.5; var 3M: 1.0, 1.5; drawdown 3M: 1.0, 1.5; duration 3M: 0.5, 0.65, 1.0, 1.25; curve 3M: 0.5, 0.6, 1.0, 1.35; credit 3M: 1.0, 1.15, 1.3, 1.5; fx 3M: 1.0, 1.5; commodity 3M: 0.7, 0.85, 1.0, 1.25; crypto 3M: 1.0; volatility 3M: 1.0.

## Sizing — capped multiple per objective × volatility regime (`hedge-3-sizing-regime-exp`)

| Objective | Horizon | Dates | ΔU λ=0.5 | ΔU λ=1 (95% CI) | ΔU λ=2 | ΔU λ=5 | ΔU λ=10 | t | Eras + | Tail (ES95 Δ) | Cost Δ | Decisions changed | Without crises | Put skew +5 vol | No options | FDR | Status |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| min_variance | 1W | 446 | +9 | +21 ([-180, +230]) | +45 | +115 | +231 | +0.20 | 2/4 | -161 | +5.6 | 64% | +113 | +15 | +20 | ✗ | NO HEDGE IMPROVEMENT |
| target_vol | 1W | 446 | +964 | +805 ([+496, +1,095]) | +485 | -475 | -2,074 | +5.26 | 3/4 | +1,856 | +9.7 | 46% | +701 | +802 | +803 | ✓ | NO HEDGE IMPROVEMENT |
| beta | 1W | 446 | +1,436 | +1,058 ([+309, +1,955]) | +304 | -1,960 | -5,733 | +2.52 | 3/4 | +3,947 | +10.6 | 78% | +439 | +916 | +947 | ✓ | NO HEDGE IMPROVEMENT |
| systematic | 1W | 446 | +1,649 | +1,309 ([+481, +2,212]) | +630 | -1,409 | -4,807 | +2.96 | 3/4 | +3,808 | +116.1 | 79% | +737 | +1,194 | +1,310 | ✓ | NO HEDGE IMPROVEMENT |
| sector | 1W | 446 | +805 | +647 ([+285, +1,101]) | +333 | -611 | -2,183 | +3.11 | 3/4 | +2,267 | +144.0 | 79% | +248 | +647 | +647 | ✓ | NO HEDGE IMPROVEMENT |
| name | 1W | 446 | +2,772 | +2,493 ([+1,829, +3,255]) | +1,936 | +264 | -2,523 | +6.86 | 3/4 | +7,038 | +139.1 | 78% | +2,042 | +2,322 | +2,477 | ✓ | NO HEDGE IMPROVEMENT |
| crash | 1W | 446 | +2,514 | +2,188 ([-299, +5,197]) | +1,537 | -417 | -3,674 | +1.56 | 3/4 | +3,073 | +234.2 | 78% | -109 | +1,634 | +2,330 | ✗ | NO HEDGE IMPROVEMENT |
| es | 1W | 446 | +4,278 | +4,016 ([+1,984, +6,442]) | +3,493 | +1,925 | -688 | +3.53 | 3/4 | +4,550 | +10.6 | 59% | +2,014 | +4,014 | +4,014 | ✓ | NO HEDGE IMPROVEMENT |
| var | 1W | 446 | +4,278 | +4,016 ([+1,984, +6,442]) | +3,493 | +1,925 | -688 | +3.53 | 3/4 | +4,550 | +10.6 | 59% | +2,014 | +4,014 | +4,014 | ✓ | NO HEDGE IMPROVEMENT |
| drawdown | 1W | 446 | +4,278 | +4,016 ([+1,984, +6,442]) | +3,493 | +1,925 | -688 | +3.53 | 3/4 | +4,550 | +10.6 | 59% | +2,014 | +4,014 | +4,014 | ✓ | NO HEDGE IMPROVEMENT |
| duration | 1W | 446 | -75 | -97 ([-219, -13]) | -141 | -274 | -494 | -1.85 | 0/4 | -254 | -1.1 | 83% | -73 | -97 | -97 | ✗ | NO HEDGE IMPROVEMENT |
| curve | 1W | 446 | -60 | -76 ([-190, +17]) | -107 | -200 | -355 | -1.43 | 0/4 | -234 | -0.9 | 83% | -48 | -76 | -76 | ✗ | NO HEDGE IMPROVEMENT |
| credit | 1W | 446 | +194 | +177 ([+3, +342]) | +145 | +46 | -118 | +2.05 | 2/4 | +670 | +47.3 | 84% | +87 | +177 | +177 | ✗ | NO HEDGE IMPROVEMENT |
| fx | 1W | 446 | +8 | +15 ([-63, +107]) | +28 | +68 | +134 | +0.34 | 2/4 | +12 | +3.4 | 84% | +49 | +15 | +15 | ✗ | NO HEDGE IMPROVEMENT |
| commodity | 1W | 446 | +23 | -6 ([-118, +129]) | -65 | -241 | -534 | -0.10 | 2/4 | +54 | +56.5 | 77% | +102 | -385 | -82 | ✗ | NO HEDGE IMPROVEMENT |
| crypto | 1W | 111 | +824 | +557 ([-750, +2,020]) | +25 | -1,574 | -4,239 | +0.79 | 0/1 | +0 | +83.6 | 28% | +591 | +557 | +557 | ✗ | NO HEDGE IMPROVEMENT |
| volatility | 1W | 207 | +226 | +187 ([-41, +504]) | +108 | -126 | -518 | +1.34 | 1/2 | +837 | +51.7 | 51% | -44 | -123 | -122 | ✗ | NO HEDGE IMPROVEMENT |
| min_variance | 1M | 212 | -62 | -73 ([-258, +82]) | -95 | -161 | -271 | -0.85 | 0/4 | -247 | +2.4 | 28% | -32 | -62 | -82 | ✗ | NO HEDGE IMPROVEMENT |
| target_vol | 1M | 212 | +1,832 | +1,395 ([+531, +2,369]) | +521 | -2,102 | -6,472 | +2.98 | 2/4 | +2,723 | -7.8 | 40% | +967 | +1,329 | +1,510 | ✓ | NO HEDGE IMPROVEMENT |
| beta | 1M | 212 | +1,464 | +738 ([-318, +2,475]) | -714 | -5,072 | -12,335 | +1.04 | 2/4 | +2,950 | -44.4 | 57% | -271 | +603 | +1,027 | ✗ | NO HEDGE IMPROVEMENT |
| systematic | 1M | 212 | +2,249 | +1,597 ([+181, +3,600]) | +292 | -3,620 | -10,141 | +1.83 | 2/4 | +4,986 | +196.2 | 57% | +516 | +1,385 | +1,963 | ✗ | NO HEDGE IMPROVEMENT |
| sector | 1M | 212 | +1,407 | +962 ([-153, +2,810]) | +71 | -2,600 | -7,053 | +1.27 | 2/4 | +5,489 | +345.4 | 58% | -180 | +962 | +962 | ✗ | NO HEDGE IMPROVEMENT |
| name | 1M | 212 | +2,379 | +1,821 ([+807, +3,602]) | +707 | -2,636 | -8,207 | +2.55 | 2/4 | +7,288 | +163.1 | 56% | +1,617 | +1,684 | +2,622 | ✓ | NO HEDGE IMPROVEMENT |
| crash | 1M | 212 | +949 | +519 ([-3,093, +4,752]) | -342 | -2,922 | -7,222 | +0.26 | 1/4 | +1,603 | +224.2 | 57% | -2,147 | +328 | +828 | ✗ | NO HEDGE IMPROVEMENT |
| es | 1M | 212 | +6,215 | +5,694 ([+2,027, +9,996]) | +4,653 | +1,529 | -3,678 | +2.80 | 2/4 | +6,719 | -16.9 | 52% | +1,596 | +5,605 | +5,997 | ✓ | NO HEDGE IMPROVEMENT |
| var | 1M | 212 | +6,215 | +5,694 ([+2,027, +9,996]) | +4,653 | +1,529 | -3,678 | +2.80 | 2/4 | +6,719 | -16.9 | 52% | +1,596 | +5,605 | +5,997 | ✓ | NO HEDGE IMPROVEMENT |
| drawdown | 1M | 212 | +6,215 | +5,694 ([+2,027, +9,996]) | +4,653 | +1,529 | -3,678 | +2.80 | 2/4 | +6,719 | -16.9 | 52% | +1,596 | +5,605 | +5,997 | ✓ | NO HEDGE IMPROVEMENT |
| duration | 1M | 212 | -168 | -182 ([-471, +17]) | -210 | -293 | -431 | -1.47 | 0/4 | -330 | -0.5 | 28% | +0 | -182 | -182 | ✗ | NO HEDGE IMPROVEMENT |
| curve | 1M | 212 | -78 | -86 ([-285, +55]) | -103 | -152 | -236 | -0.99 | 0/4 | -311 | +3.5 | 64% | +49 | -86 | -86 | ✗ | NO HEDGE IMPROVEMENT |
| credit | 1M | 212 | -377 | -457 ([-1,139, +18]) | -617 | -1,098 | -1,898 | -1.55 | 0/4 | -1,854 | +122.6 | 66% | +21 | -457 | -457 | ✗ | NO HEDGE IMPROVEMENT |
| fx | 1M | 212 | +72 | +63 ([-50, +201]) | +46 | -7 | -95 | +0.99 | 2/4 | +400 | +2.8 | 68% | +12 | +63 | +63 | ✗ | NO HEDGE IMPROVEMENT |
| commodity | 1M | 212 | -266 | -288 ([-815, +45]) | -332 | -464 | -684 | -1.31 | 0/4 | -997 | -37.2 | 57% | -119 | -401 | +22 | ✗ | NO HEDGE IMPROVEMENT |
| crypto | 1M | 52 | +0 | +0 ([+0, +0]) | +0 | +0 | +0 | — | 0/1 | +0 | +0.0 | 0% | +0 | +0 | +0 | ✗ | INSUFFICIENT DATA |
| volatility | 1M | 98 | -51 | -85 ([-255, +0]) | -153 | -356 | -695 | -1.31 | 0/2 | +0 | -6.0 | 7% | -132 | -129 | -82 | ✗ | NO HEDGE IMPROVEMENT |
| min_variance | 3M | 70 | +0 | +0 ([+0, +0]) | +0 | +0 | +0 | — | 0/4 | +0 | +0.0 | 0% | +0 | +0 | +0 | ✗ | NO HEDGE IMPROVEMENT |
| target_vol | 3M | 70 | +348 | +421 ([+0, +1,344]) | +566 | +1,001 | +1,726 | +1.23 | 0/4 | +772 | +14.3 | 3% | +491 | +421 | +440 | ✗ | NO HEDGE IMPROVEMENT |
| beta | 3M | 70 | +231 | +253 ([+0, +1,096]) | +297 | +429 | +649 | +0.91 | 0/4 | +693 | +3.4 | 5% | +299 | +253 | +258 | ✗ | NO HEDGE IMPROVEMENT |
| systematic | 3M | 70 | +353 | +456 ([+0, +1,466]) | +661 | +1,278 | +2,305 | +1.22 | 0/4 | +532 | +63.4 | 5% | +544 | +453 | +498 | ✗ | NO HEDGE IMPROVEMENT |
| sector | 3M | 70 | -67 | -45 ([-79, +274]) | -1 | +131 | +350 | -0.50 | 0/4 | +493 | +99.5 | 4% | -49 | -45 | -45 | ✗ | NO HEDGE IMPROVEMENT |
| name | 3M | 70 | +208 | +174 ([+0, +945]) | +106 | -100 | -442 | +0.72 | 0/4 | +75 | +62.9 | 4% | +221 | +176 | +198 | ✗ | NO HEDGE IMPROVEMENT |
| crash | 3M | 70 | +0 | -22 ([-132, +1,944]) | -67 | -201 | -424 | -0.04 | 0/4 | +102 | +79.8 | 5% | +182 | -96 | +232 | ✗ | NO HEDGE IMPROVEMENT |
| es | 3M | 70 | +935 | +985 ([+0, +2,722]) | +1,086 | +1,389 | +1,893 | +1.42 | 0/4 | +893 | +8.7 | 4% | +1,136 | +985 | +1,069 | ✗ | NO HEDGE IMPROVEMENT |
| var | 3M | 70 | +935 | +985 ([+0, +2,722]) | +1,086 | +1,389 | +1,893 | +1.42 | 0/4 | +893 | +8.7 | 4% | +1,136 | +985 | +1,069 | ✗ | NO HEDGE IMPROVEMENT |
| drawdown | 3M | 70 | +935 | +985 ([+0, +2,722]) | +1,086 | +1,389 | +1,893 | +1.42 | 0/4 | +893 | +8.7 | 4% | +1,136 | +985 | +1,069 | ✗ | NO HEDGE IMPROVEMENT |
| duration | 3M | 70 | +15 | +18 ([-0, +49]) | +22 | +35 | +57 | +1.41 | 0/4 | -5 | -0.8 | 7% | +22 | +18 | +18 | ✗ | NO HEDGE IMPROVEMENT |
| curve | 3M | 70 | +0 | +0 ([+0, +0]) | +0 | +0 | +0 | — | 0/4 | +0 | +0.0 | 0% | +0 | +0 | +0 | ✗ | NO HEDGE IMPROVEMENT |
| credit | 3M | 70 | +11 | +39 ([+0, +146]) | +96 | +266 | +549 | +1.05 | 0/4 | +340 | +69.4 | 9% | +60 | +39 | +39 | ✗ | NO HEDGE IMPROVEMENT |
| fx | 3M | 70 | +18 | +4 ([-10, +42]) | -24 | -106 | -244 | +0.29 | 0/2 | +0 | +0.2 | 4% | +4 | +4 | +4 | ✗ | NO HEDGE IMPROVEMENT |
| commodity | 3M | 70 | +55 | +22 ([-33, +100]) | -44 | -242 | -573 | +0.65 | 0/4 | +0 | +30.9 | 3% | +63 | -3 | +0 | ✗ | NO HEDGE IMPROVEMENT |
| crypto | 3M | 16 | +0 | +0 (—) | +0 | +0 | +0 | — | 0/0 | — | +0.0 | 0% | +0 | +0 | +0 | ✗ | INSUFFICIENT DATA |
| volatility | 3M | 31 | +0 | +0 ([+0, +0]) | +0 | +0 | +0 | — | 0/1 | +0 | +0.0 | 0% | +0 | +0 | +0 | ✗ | INSUFFICIENT DATA |

ΔU > 0: the challenger's realised utility beats hedge-2's. Tail (ES95 Δ) > 0: the challenger's hedged 95% expected shortfall is smaller (better). Cost Δ > 0: the challenger costs more. "Without crises" drops 2009, Feb–Jun 2020 and 2022; "Put skew +5 vol" charges every bought put 5 implied-vol points more; "No options" keeps only cases where neither package holds an option. Each objective is judged on its own; cells are never pooled across objectives.

Learned multiples (walk-forward, per era): min_variance 1W: 0.9 (high_vol), 0.95 (high_vol), 1.0 (high_vol), 1.0 (low_vol), 1.15 (low_vol), 1.2 (low_vol), 1.25 (low_vol); target_vol 1W: 1.0 (high_vol), 1.0 (low_vol), 1.05 (high_vol), 1.15 (high_vol), 1.2 (high_vol), 1.4 (high_vol), 1.5 (low_vol); beta 1W: 1.0 (high_vol), 1.0 (low_vol), 1.5 (high_vol), 1.5 (low_vol); systematic 1W: 1.0 (high_vol), 1.0 (low_vol), 1.35 (high_vol), 1.4 (high_vol), 1.45 (high_vol), 1.45 (low_vol), 1.5 (high_vol), 1.5 (low_vol); sector 1W: 1.0 (high_vol), 1.0 (low_vol), 1.15 (high_vol), 1.2 (low_vol), 1.3 (low_vol), 1.35 (high_vol), 1.45 (high_vol); name 1W: 1.0 (high_vol), 1.0 (low_vol), 1.5 (high_vol), 1.5 (low_vol); crash 1W: 1.0 (high_vol), 1.0 (low_vol), 1.05 (low_vol), 1.45 (high_vol), 1.45 (low_vol), 1.5 (high_vol), 1.5 (low_vol); es 1W: 1.0 (high_vol), 1.0 (low_vol), 1.5 (high_vol), 1.5 (low_vol); var 1W: 1.0 (high_vol), 1.0 (low_vol), 1.5 (high_vol), 1.5 (low_vol); drawdown 1W: 1.0 (high_vol), 1.0 (low_vol), 1.5 (high_vol), 1.5 (low_vol); duration 1W: 0.5 (high_vol), 0.5 (low_vol), 0.55 (high_vol), 0.7 (high_vol), 1.0 (high_vol), 1.0 (low_vol), 1.2 (high_vol), 1.4 (low_vol), 1.5 (low_vol); curve 1W: 0.5 (high_vol), 0.5 (low_vol), 1.0 (high_vol), 1.0 (low_vol), 1.3 (high_vol), 1.5 (low_vol); credit 1W: 0.6 (low_vol), 1.0 (high_vol), 1.0 (low_vol), 1.4 (high_vol), 1.5 (high_vol), 1.5 (low_vol); fx 1W: 1.0 (high_vol), 1.0 (low_vol), 1.5 (high_vol), 1.5 (low_vol); commodity 1W: 0.9 (high_vol), 0.95 (high_vol), 1.0 (high_vol), 1.0 (low_vol), 1.45 (low_vol), 1.5 (high_vol), 1.5 (low_vol); crypto 1W: 1.0 (high_vol), 1.0 (low_vol), 1.35 (low_vol); volatility 1W: 1.0 (high_vol), 1.0 (low_vol), 1.5 (high_vol), 1.5 (low_vol); min_variance 1M: 0.95 (high_vol), 1.0 (high_vol), 1.0 (low_vol), 1.05 (low_vol), 1.1 (low_vol); target_vol 1M: 1.0 (high_vol), 1.0 (low_vol), 1.25 (high_vol), 1.3 (high_vol), 1.5 (low_vol); beta 1M: 1.0 (high_vol), 1.0 (low_vol), 1.45 (low_vol), 1.5 (high_vol), 1.5 (low_vol); systematic 1M: 1.0 (high_vol), 1.0 (low_vol), 1.5 (high_vol), 1.5 (low_vol); sector 1M: 1.0 (high_vol), 1.0 (low_vol), 1.35 (low_vol), 1.4 (high_vol), 1.4 (low_vol), 1.45 (low_vol), 1.5 (high_vol); name 1M: 1.0 (high_vol), 1.0 (low_vol), 1.5 (high_vol), 1.5 (low_vol); crash 1M: 0.65 (low_vol), 0.95 (low_vol), 1.0 (high_vol), 1.0 (low_vol), 1.1 (low_vol), 1.5 (high_vol); es 1M: 1.0 (high_vol), 1.0 (low_vol), 1.5 (high_vol), 1.5 (low_vol); var 1M: 1.0 (high_vol), 1.0 (low_vol), 1.5 (high_vol), 1.5 (low_vol); drawdown 1M: 1.0 (high_vol), 1.0 (low_vol), 1.5 (high_vol), 1.5 (low_vol); duration 1M: 0.6 (high_vol), 1.0 (high_vol), 1.0 (low_vol), 1.35 (low_vol), 1.45 (high_vol); curve 1M: 0.75 (high_vol), 1.0 (high_vol), 1.0 (low_vol), 1.05 (low_vol), 1.2 (high_vol), 1.4 (low_vol), 1.5 (high_vol); credit 1M: 0.7 (high_vol), 1.0 (high_vol), 1.0 (low_vol), 1.25 (high_vol), 1.5 (high_vol), 1.5 (low_vol); fx 1M: 1.0 (high_vol), 1.0 (low_vol), 1.5 (high_vol), 1.5 (low_vol); commodity 1M: 0.5 (high_vol), 0.7 (high_vol), 1.0 (high_vol), 1.0 (low_vol), 1.5 (low_vol); crypto 1M: 1.0 (high_vol), 1.0 (low_vol); volatility 1M: 1.0 (high_vol), 1.0 (low_vol), 1.5 (high_vol); min_variance 3M: 1.0 (high_vol), 1.0 (low_vol); target_vol 3M: 1.0 (high_vol), 1.0 (low_vol), 1.5 (low_vol); beta 3M: 1.0 (high_vol), 1.0 (low_vol), 1.5 (low_vol); systematic 3M: 1.0 (high_vol), 1.0 (low_vol), 1.5 (low_vol); sector 3M: 1.0 (high_vol), 1.0 (low_vol), 1.5 (low_vol); name 3M: 1.0 (high_vol), 1.0 (low_vol), 1.5 (low_vol); crash 3M: 1.0 (high_vol), 1.0 (low_vol), 1.5 (low_vol); es 3M: 1.0 (high_vol), 1.0 (low_vol), 1.5 (low_vol); var 3M: 1.0 (high_vol), 1.0 (low_vol), 1.5 (low_vol); drawdown 3M: 1.0 (high_vol), 1.0 (low_vol), 1.5 (low_vol); duration 3M: 0.9 (low_vol), 1.0 (high_vol), 1.0 (low_vol); curve 3M: 1.0 (high_vol), 1.0 (low_vol); credit 3M: 1.0 (high_vol), 1.0 (low_vol), 1.5 (low_vol); fx 3M: 1.0 (high_vol), 1.0 (low_vol), 1.5 (low_vol); commodity 3M: 1.0 (high_vol), 1.0 (low_vol), 1.5 (low_vol); crypto 3M: 1.0 (high_vol), 1.0 (low_vol); volatility 3M: 1.0 (high_vol), 1.0 (low_vol).

## Product choice — learned product type per objective × regime (`hedge-3-product-exp`)

| Objective | Horizon | Dates | ΔU λ=0.5 | ΔU λ=1 (95% CI) | ΔU λ=2 | ΔU λ=5 | ΔU λ=10 | t | Eras + | Tail (ES95 Δ) | Cost Δ | Decisions changed | Without crises | Put skew +5 vol | No options | FDR | Status |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| beta | 1W | 223 | +25 | +49 ([+0, +157]) | +97 | +239 | +476 | +1.23 | 0/4 | +0 | +4.1 | 0% | +59 | +89 | +0 | ✗ | NO HEDGE IMPROVEMENT |
| crash | 1W | 223 | +54 | +9 ([-644, +923]) | -83 | -358 | -817 | +0.02 | 2/4 | +104 | +3.5 | 14% | +117 | +708 | -598 | ✗ | NO HEDGE IMPROVEMENT |
| min_variance | 1W | 223 | -133 | -98 ([-243, +4]) | -28 | +183 | +533 | -1.55 | 0/4 | -192 | +0.6 | 3% | -131 | -98 | -98 | ✗ | NO HEDGE IMPROVEMENT |
| sector | 1W | 223 | +0 | +0 ([+0, +0]) | +0 | +0 | +0 | — | 0/4 | +0 | +0.0 | 0% | +0 | +0 | +0 | ✗ | NO HEDGE IMPROVEMENT |
| duration | 1W | 223 | -74 | -109 ([-277, +36]) | -178 | -387 | -734 | -1.36 | 1/4 | -27 | +104.2 | 46% | -139 | -109 | -109 | ✗ | NO HEDGE IMPROVEMENT |
| credit | 1W | 223 | -18 | -43 ([-134, +36]) | -92 | -241 | -487 | -0.99 | 1/4 | +0 | +13.3 | 12% | -45 | -43 | -43 | ✗ | NO HEDGE IMPROVEMENT |
| fx | 1W | 223 | +47 | +49 ([-4, +104]) | +55 | +71 | +99 | +1.80 | 3/4 | +122 | +0.5 | 42% | +16 | +49 | +49 | ✗ | NO HEDGE IMPROVEMENT |
| beta | 1M | 106 | -418 | -363 ([-1,181, +360]) | -255 | +72 | +616 | -0.92 | 0/4 | +162 | +285.1 | 14% | -528 | -176 | -655 | ✗ | NO HEDGE IMPROVEMENT |
| crash | 1M | 106 | -116 | -129 ([-353, +6]) | -155 | -234 | -365 | -1.41 | 0/4 | -50 | +52.6 | 2% | -125 | +88 | +0 | ✗ | NO HEDGE IMPROVEMENT |
| min_variance | 1M | 106 | -120 | -84 ([-708, +313]) | -12 | +204 | +564 | -0.32 | 0/4 | +62 | +253.3 | 6% | -83 | -2 | +237 | ✗ | NO HEDGE IMPROVEMENT |
| sector | 1M | 106 | +0 | +0 ([+0, +0]) | +0 | +0 | +0 | — | 0/4 | +0 | +0.0 | 0% | +0 | +0 | +0 | ✗ | NO HEDGE IMPROVEMENT |
| duration | 1M | 106 | -223 | -207 ([-649, +117]) | -174 | -77 | +85 | -1.06 | 0/4 | -3 | +155.9 | 11% | -254 | -207 | -207 | ✗ | NO HEDGE IMPROVEMENT |
| credit | 1M | 106 | -222 | -231 ([-697, +5]) | -251 | -308 | -405 | -1.29 | 0/4 | -379 | -21.9 | 12% | -60 | -231 | -231 | ✗ | NO HEDGE IMPROVEMENT |
| fx | 1M | 106 | +0 | +7 ([-49, +90]) | +22 | +66 | +140 | +0.21 | 1/4 | +40 | +0.8 | 22% | +13 | +7 | +7 | ✗ | NO HEDGE IMPROVEMENT |

ΔU > 0: the challenger's realised utility beats hedge-2's. Tail (ES95 Δ) > 0: the challenger's hedged 95% expected shortfall is smaller (better). Cost Δ > 0: the challenger costs more. "Without crises" drops 2009, Feb–Jun 2020 and 2022; "Put skew +5 vol" charges every bought put 5 implied-vol points more; "No options" keeps only cases where neither package holds an option. Each objective is judged on its own; cells are never pooled across objectives.

Chosen product types in the test eras: beta 1W: hedge-2 ×2250, equity_index_future ×88; crash 1W: hedge-2 ×1342, etn ×143, inverse_etf ×853; min_variance 1W: hedge-2 ×2647, etf ×192; sector 1W: hedge-2 ×1443; duration 1W: hedge-2 ×289, etf ×553, treasury_future ×115; credit 1W: hedge-2 ×268, ig_corporate ×125; fx 1W: hedge-2 ×92, fx_future ×249; beta 1M: hedge-2 ×759, equity_index_future ×209, etf ×143; crash 1M: hedge-2 ×825, inverse_etf ×286; min_variance 1M: hedge-2 ×1056, etf ×293; sector 1M: hedge-2 ×685; duration 1M: hedge-2 ×398, etf ×63; credit 1M: hedge-2 ×112, high_yield ×83; fx 1M: hedge-2 ×97, fx_future ×56.

## Alpha → hedge — hedge size scaled by validated 1W Alpha (`hedge-3-alpha-exp`)

| Objective | Horizon | Dates | ΔU λ=0.5 | ΔU λ=1 (95% CI) | ΔU λ=2 | ΔU λ=5 | ΔU λ=10 | t | Eras + | Tail (ES95 Δ) | Cost Δ | Decisions changed | Without crises | Put skew +5 vol | No options | FDR | Status |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| min_variance | 1W | 446 | -193 | -185 ([-568, +35]) | -168 | -118 | -36 | -1.20 | 0/4 | -838 | -0.2 | 45% | -20 | -185 | -185 | ✗ | NO HEDGE IMPROVEMENT |
| target_vol | 1W | 446 | -123 | -118 ([-334, +9]) | -109 | -81 | -35 | -1.35 | 0/4 | -344 | -0.0 | 34% | -12 | -118 | -118 | ✗ | NO HEDGE IMPROVEMENT |
| beta | 1W | 446 | +0 | +0 ([+0, +0]) | +0 | +0 | +0 | — | 0/4 | +0 | +0.0 | 0% | +0 | +0 | +0 | ✗ | NO HEDGE IMPROVEMENT |
| systematic | 1W | 446 | +0 | +0 ([+0, +0]) | +0 | +0 | +0 | — | 0/4 | +0 | +0.0 | 0% | +0 | +0 | +0 | ✗ | NO HEDGE IMPROVEMENT |
| sector | 1W | 446 | +0 | +0 ([+0, +0]) | +0 | +0 | +0 | — | 0/4 | +0 | +0.0 | 0% | +0 | +0 | +0 | ✗ | NO HEDGE IMPROVEMENT |
| name | 1W | 446 | +0 | +0 ([+0, +0]) | +0 | +0 | +0 | — | 0/4 | +0 | +0.0 | 0% | +0 | +0 | +0 | ✗ | NO HEDGE IMPROVEMENT |
| crash | 1W | 446 | -420 | -413 ([-1,325, +48]) | -400 | -361 | -296 | -1.18 | 0/4 | -426 | +0.2 | 23% | +18 | -378 | -35 | ✗ | NO HEDGE IMPROVEMENT |
| es | 1W | 446 | -405 | -402 ([-1,300, +101]) | -394 | -371 | -334 | -1.12 | 1/4 | -409 | -0.1 | 44% | +16 | -402 | -402 | ✗ | NO HEDGE IMPROVEMENT |
| var | 1W | 446 | -405 | -402 ([-1,300, +101]) | -394 | -371 | -334 | -1.12 | 1/4 | -409 | -0.1 | 44% | +16 | -402 | -402 | ✗ | NO HEDGE IMPROVEMENT |
| drawdown | 1W | 446 | -405 | -402 ([-1,300, +101]) | -394 | -371 | -334 | -1.12 | 1/4 | -409 | -0.1 | 44% | +16 | -402 | -402 | ✗ | NO HEDGE IMPROVEMENT |
| duration | 1W | 161 | -2 | -1 ([-7, +3]) | -1 | -0 | +1 | -0.53 | 0/3 | -3 | +0.0 | 34% | -3 | -1 | -1 | ✗ | NO HEDGE IMPROVEMENT |
| curve | 1W | 161 | -2 | -1 ([-7, +3]) | -1 | -0 | +1 | -0.53 | 0/3 | -3 | +0.0 | 34% | -3 | -1 | -1 | ✗ | NO HEDGE IMPROVEMENT |
| credit | 1W | 93 | +0 | +0 ([+0, +0]) | +0 | +0 | +0 | — | 0/2 | +0 | +0.0 | 0% | +0 | +0 | +0 | ✗ | NO HEDGE IMPROVEMENT |
| fx | 1W | 68 | +0 | +0 ([-2, +3]) | -0 | -1 | -3 | +0.20 | 1/2 | -0 | +0.0 | 38% | +2 | +0 | +0 | ✗ | NO HEDGE IMPROVEMENT |
| commodity | 1W | 151 | +53 | +56 ([-0, +153]) | +62 | +81 | +113 | +1.43 | 1/3 | +128 | -0.1 | 47% | +1 | +56 | +62 | ✗ | NO HEDGE IMPROVEMENT |
| crypto | 1W | 13 | +0 | +0 (—) | +0 | +0 | +0 | — | 0/0 | — | +0.0 | 0% | +0 | +0 | +0 | ✗ | INSUFFICIENT DATA |
| volatility | 1W | 20 | +0 | +0 (—) | +0 | +0 | +0 | — | 0/0 | +0 | +0.0 | 0% | +0 | +0 | +0 | ✗ | INSUFFICIENT DATA |

ΔU > 0: the challenger's realised utility beats hedge-2's. Tail (ES95 Δ) > 0: the challenger's hedged 95% expected shortfall is smaller (better). Cost Δ > 0: the challenger costs more. "Without crises" drops 2009, Feb–Jun 2020 and 2022; "Put skew +5 vol" charges every bought put 5 implied-vol points more; "No options" keeps only cases where neither package holds an option. Each objective is judged on its own; cells are never pooled across objectives.

Learned κ per era: min_variance: [0.0, 1.0]; target_vol: [0.0, 1.0]; beta: [0.0]; systematic: [0.0]; sector: [0.0]; name: [0.0]; crash: [0.0, 1.0]; es: [0.0, 0.75, 1.0]; var: [0.0, 0.75, 1.0]; drawdown: [0.0, 0.75, 1.0]; duration: [0.0, 1.0]; curve: [0.0, 1.0]; credit: [0.0]; fx: [0.0, 1.0]; commodity: [0.0, 1.0]; crypto: [0.0]; volatility: [0.0].

## Which product worked best in which conditions (forced-product replay; descriptive)

| Objective | Horizon | Product type | State | Dates | U hedge-2 | U this type | Risk reduction | Cost | Basis error | Profit sacrificed | hedge-2 uses it |
|---|---|---|---|---|---|---|---|---|---|---|---|
| beta | 1W | equity_index_future | all | 223 | +6,337 | +6,385 | +8,188 | +64.3 | +1,985.6 | +1,738 | 83% |
| beta | 1W | equity_index_future | high_vol | 109 | +7,314 | +7,437 | +10,746 | +70.9 | +2,301.6 | +3,238 | 79% |
| beta | 1W | equity_index_future | low_vol | 114 | +4,233 | +4,208 | +4,545 | +57.9 | +1,620.1 | +278 | 87% |
| beta | 1W | etf | all | 223 | +6,337 | +4,828 | +7,146 | +453.1 | +1,483.8 | +1,865 | 15% |
| beta | 1W | etf | high_vol | 109 | +7,314 | +5,652 | +9,539 | +458.4 | +1,698.7 | +3,428 | 18% |
| beta | 1W | etf | low_vol | 114 | +4,233 | +2,892 | +3,683 | +447.9 | +1,239.4 | +344 | 12% |
| beta | 1W | etn | all | 20 | +23,863 | +31,229 | +14,072 | +469.4 | +9,013.3 | -17,626 | 3% |
| beta | 1W | index_option | all | 99 | +6,864 | +5,291 | +7,265 | +1,207.8 | +2,588.5 | +765 | 5% |
| beta | 1W | index_option | high_vol | 40 | +9,308 | +7,341 | +11,666 | +1,537.3 | +2,424.7 | +2,787 | 9% |
| beta | 1W | index_option | low_vol | 59 | +4,265 | +2,861 | +3,365 | +1,002.1 | +2,685.7 | -498 | 3% |
| beta | 1W | inverse_etf | all | 223 | +6,369 | +4,679 | +7,136 | +737.7 | +418.2 | +1,720 | 0% |
| beta | 1W | inverse_etf | high_vol | 109 | +7,327 | +5,590 | +9,498 | +854.4 | +434.8 | +3,053 | 0% |
| beta | 1W | inverse_etf | low_vol | 114 | +4,285 | +2,660 | +3,698 | +623.4 | +401.4 | +414 | 0% |
| crash | 1W | etn | all | 107 | +19,197 | +11,459 | +14,055 | +492.1 | +4,622.3 | +2,104 | 0% |
| crash | 1W | etn | high_vol | 62 | +22,433 | +11,003 | +16,068 | +364.3 | +4,951.4 | +4,701 | 0% |
| crash | 1W | etn | low_vol | 45 | +13,217 | +12,600 | +11,795 | +668.2 | +4,126.2 | -1,473 | 1% |
| crash | 1W | index_option | all | 223 | +13,999 | +7,750 | +9,397 | +539.3 | +3,263.6 | +1,108 | 23% |
| crash | 1W | index_option | high_vol | 109 | +17,781 | +10,400 | +13,249 | +710.1 | +3,787.5 | +2,138 | 37% |
| crash | 1W | index_option | low_vol | 114 | +9,210 | +4,987 | +5,466 | +373.2 | +2,656.3 | +105 | 9% |
| crash | 1W | inverse_etf | all | 223 | +13,999 | +15,699 | +18,143 | +743.7 | +446.5 | +1,700 | 77% |
| crash | 1W | inverse_etf | high_vol | 109 | +17,781 | +20,202 | +24,130 | +858.4 | +464.5 | +3,070 | 63% |
| crash | 1W | inverse_etf | low_vol | 114 | +9,210 | +9,303 | +10,302 | +632.0 | +428.2 | +367 | 90% |
| min_variance | 1W | commodity_etf | all | 32 | +1,804 | +405 | +210 | +1.2 | +7,502.6 | -197 | 12% |
| min_variance | 1W | crypto_etf | all | 26 | +40,030 | +38,033 | +27,953 | +913.6 | +13,526.4 | -10,994 | 85% |
| min_variance | 1W | crypto_futures_etf | all | 53 | +54,098 | +54,227 | +41,997 | +842.2 | +14,871.7 | -13,073 | 60% |
| min_variance | 1W | crypto_futures_etf | low_vol | 34 | +44,399 | +44,804 | +31,184 | +933.4 | +13,985.5 | -14,553 | 59% |
| min_variance | 1W | equity_index_future | all | 223 | +10,122 | +8,760 | +12,160 | +103.1 | +6,688.8 | +3,296 | 81% |
| min_variance | 1W | equity_index_future | high_vol | 109 | +11,606 | +9,739 | +15,916 | +122.1 | +7,879.9 | +6,055 | 78% |
| min_variance | 1W | equity_index_future | low_vol | 114 | +6,838 | +6,182 | +6,821 | +84.2 | +5,243.4 | +555 | 84% |
| min_variance | 1W | etf | all | 202 | +9,352 | +7,839 | +12,621 | +254.1 | +7,024.5 | +4,527 | 29% |
| min_variance | 1W | etf | high_vol | 107 | +10,287 | +8,462 | +15,594 | +283.1 | +7,937.1 | +6,848 | 27% |
| min_variance | 1W | etf | low_vol | 95 | +5,353 | +4,584 | +5,751 | +209.3 | +5,323.9 | +958 | 31% |
| min_variance | 1W | fx_forward | all | 35 | +10,261 | +6,386 | +5,486 | +51.4 | +12,416.3 | -952 | 3% |
| min_variance | 1W | fx_forward | high_vol | 27 | +11,777 | +7,236 | +5,841 | +52.1 | +13,524.7 | -1,447 | 3% |
| min_variance | 1W | fx_future | all | 40 | +8,248 | +5,745 | +4,930 | +34.2 | +12,852.6 | -849 | 23% |
| min_variance | 1W | fx_future | high_vol | 33 | +9,110 | +6,241 | +5,080 | +33.4 | +13,699.2 | -1,195 | 22% |
| min_variance | 1W | index_option | all | 39 | +11,712 | +3,728 | +5,825 | +256.1 | +9,706.7 | +1,841 | 17% |
| min_variance | 1W | index_option | high_vol | 23 | +12,662 | +2,394 | +5,791 | +505.3 | +11,160.0 | +2,891 | 13% |
| min_variance | 1W | inverse_etf | all | 77 | +9,029 | +2,538 | +7,378 | +386.1 | +9,582.4 | +4,454 | 0% |
| min_variance | 1W | inverse_etf | high_vol | 43 | +7,554 | +2,877 | +10,360 | +565.1 | +10,369.9 | +6,918 | 0% |
| min_variance | 1W | inverse_etf | low_vol | 34 | +10,476 | +949 | +2,310 | +150.3 | +8,434.4 | +1,210 | 0% |
| min_variance | 1W | treasury_future | all | 86 | +3,381 | +2,089 | +1,609 | +38.7 | +6,000.6 | -519 | 90% |
| min_variance | 1W | treasury_future | high_vol | 57 | +3,696 | +2,066 | +1,363 | +35.5 | +6,027.8 | -739 | 89% |
| min_variance | 1W | treasury_future | low_vol | 29 | +2,684 | +2,454 | +2,318 | +43.6 | +5,958.4 | -180 | 93% |
| sector | 1W | etf | all | 223 | +7,465 | +7,198 | +9,611 | +653.7 | +5,621.4 | +1,759 | 100% |
| sector | 1W | etf | high_vol | 109 | +9,195 | +8,826 | +12,471 | +625.3 | +6,900.4 | +3,020 | 100% |
| sector | 1W | etf | low_vol | 114 | +4,494 | +4,394 | +5,597 | +681.7 | +3,983.8 | +522 | 100% |
| duration | 1W | etf | all | 223 | +422 | +454 | +642 | +185.3 | +585.2 | +2 | 23% |
| duration | 1W | etf | high_vol | 109 | +371 | +429 | +417 | +164.4 | +620.6 | -177 | 26% |
| duration | 1W | etf | low_vol | 114 | +683 | +732 | +1,099 | +204.1 | +551.3 | +163 | 21% |
| duration | 1W | treasury_future | all | 223 | +475 | +472 | +517 | +49.6 | +1,971.6 | -5 | 87% |
| duration | 1W | treasury_future | high_vol | 109 | +399 | +391 | +280 | +48.7 | +2,067.3 | -160 | 82% |
| duration | 1W | treasury_future | low_vol | 114 | +815 | +800 | +992 | +50.3 | +1,877.2 | +141 | 91% |
| credit | 1W | high_yield | all | 223 | +755 | +593 | +852 | +157.3 | +1,413.1 | +103 | 78% |
| credit | 1W | high_yield | high_vol | 109 | +1,113 | +680 | +1,144 | +166.3 | +1,681.5 | +297 | 71% |
| credit | 1W | high_yield | low_vol | 114 | +271 | +357 | +427 | +148.9 | +1,104.3 | -79 | 84% |
| credit | 1W | ig_corporate | all | 211 | +2,007 | +1,827 | +2,287 | +237.5 | +1,628.9 | +223 | 34% |
| credit | 1W | ig_corporate | high_vol | 97 | +2,813 | +1,853 | +2,395 | +212.2 | +1,800.8 | +329 | 46% |
| credit | 1W | ig_corporate | low_vol | 114 | +1,115 | +1,925 | +2,317 | +259.1 | +1,466.7 | +133 | 23% |
| credit | 1W | leveraged_loan | all | 195 | +799 | +200 | +479 | +306.7 | +2,123.4 | -28 | 5% |
| credit | 1W | leveraged_loan | high_vol | 86 | +1,287 | +282 | +728 | +318.3 | +3,005.6 | +128 | 6% |
| credit | 1W | leveraged_loan | low_vol | 109 | +285 | -9 | +133 | +297.2 | +890.2 | -155 | 4% |
| fx | 1W | currency_trust | all | 48 | -35 | -83 | -11 | +10.9 | +688.4 | +61 | 2% |
| fx | 1W | currency_trust | high_vol | 31 | -145 | -116 | -23 | +11.4 | +740.7 | +81 | 2% |
| fx | 1W | fx_forward | all | 223 | +164 | +161 | +157 | +8.7 | +71.0 | -12 | 93% |
| fx | 1W | fx_forward | high_vol | 109 | +204 | +200 | +148 | +8.6 | +65.6 | -61 | 92% |
| fx | 1W | fx_forward | low_vol | 114 | +217 | +217 | +281 | +8.9 | +77.7 | +54 | 95% |
| fx | 1W | fx_future | all | 217 | +212 | +363 | +349 | +11.9 | +276.0 | -27 | 7% |
| fx | 1W | fx_future | high_vol | 104 | +291 | +534 | +385 | +12.4 | +305.6 | -161 | 12% |
| fx | 1W | fx_future | low_vol | 113 | +254 | +329 | +451 | +11.4 | +242.2 | +111 | 3% |
| fx | 1W | fx_spot | all | 223 | +171 | +20 | +122 | +107.9 | +166.2 | -6 | 2% |
| fx | 1W | fx_spot | high_vol | 109 | +202 | +57 | +103 | +104.8 | +203.7 | -59 | 2% |
| fx | 1W | fx_spot | low_vol | 114 | +240 | +88 | +268 | +112.3 | +93.1 | +67 | 3% |
| beta | 1M | equity_index_future | all | 106 | +8,252 | +8,331 | +15,136 | +103.4 | +1,968.5 | +6,701 | 93% |
| beta | 1M | equity_index_future | high_vol | 50 | +10,509 | +10,441 | +20,362 | +121.9 | +2,371.2 | +9,800 | 92% |
| beta | 1M | equity_index_future | low_vol | 56 | +3,393 | +3,745 | +7,717 | +86.6 | +1,512.2 | +3,885 | 94% |
| beta | 1M | etf | all | 106 | +8,252 | +6,822 | +14,506 | +938.7 | +1,598.2 | +6,745 | 4% |
| beta | 1M | etf | high_vol | 50 | +10,509 | +9,597 | +20,073 | +881.1 | +2,041.6 | +9,595 | 6% |
| beta | 1M | etf | low_vol | 56 | +3,393 | +1,472 | +6,618 | +991.1 | +1,042.7 | +4,154 | 2% |
| beta | 1M | index_option | all | 46 | +735 | -9,138 | +4,131 | +3,230.6 | +2,294.7 | +10,039 | 7% |
| beta | 1M | index_option | low_vol | 30 | -1,039 | -9,014 | +2,965 | +2,579.3 | +1,838.4 | +9,399 | 7% |
| beta | 1M | inverse_etf | all | 106 | +8,417 | +3,473 | +12,322 | +1,842.2 | +699.8 | +7,006 | 0% |
| beta | 1M | inverse_etf | high_vol | 50 | +10,471 | +3,544 | +16,416 | +2,424.3 | +918.6 | +10,447 | 0% |
| beta | 1M | inverse_etf | low_vol | 56 | +3,742 | +891 | +6,051 | +1,308.6 | +406.4 | +3,851 | 0% |
| crash | 1M | etn | all | 50 | -3,051 | -2,786 | +5,190 | +848.5 | +4,776.9 | +7,128 | 0% |
| crash | 1M | etn | high_vol | 28 | -1,544 | -3,892 | +5,597 | +163.9 | +5,390.4 | +9,325 | 0% |
| crash | 1M | etn | low_vol | 22 | -10,638 | -4,433 | +1,618 | +1,719.8 | +3,857.4 | +4,331 | 1% |
| crash | 1M | index_option | all | 106 | +2,498 | +1,965 | +6,765 | +1,523.0 | +3,627.2 | +3,276 | 24% |
| crash | 1M | index_option | high_vol | 50 | +77 | +2,061 | +9,062 | +2,088.1 | +4,383.1 | +4,913 | 38% |
| crash | 1M | index_option | low_vol | 56 | +3,493 | +1,545 | +4,343 | +1,009.3 | +2,766.5 | +1,789 | 12% |
| crash | 1M | inverse_etf | all | 106 | +2,498 | +5,079 | +13,955 | +1,856.1 | +732.4 | +7,019 | 75% |
| crash | 1M | inverse_etf | high_vol | 50 | +77 | +4,104 | +16,993 | +2,434.1 | +959.2 | +10,455 | 62% |
| crash | 1M | inverse_etf | low_vol | 56 | +3,493 | +4,057 | +9,284 | +1,330.8 | +433.1 | +3,897 | 87% |
| min_variance | 1M | commodity_etf | all | 23 | +4,928 | +681 | +337 | +12.7 | +19,579.8 | -357 | 0% |
| min_variance | 1M | commodity_option | all | 46 | +10,181 | +8,305 | +9,560 | -94.1 | +7,988.0 | +1,349 | 34% |
| min_variance | 1M | commodity_option | high_vol | 22 | +10,449 | +5,840 | +11,017 | -6.8 | +10,367.6 | +5,184 | 38% |
| min_variance | 1M | commodity_option | low_vol | 24 | +5,299 | +8,917 | +6,551 | -174.6 | +4,846.6 | -2,191 | 31% |
| min_variance | 1M | crypto_futures_etf | all | 26 | +91,963 | +100,148 | +110,626 | +3,753.3 | +17,482.3 | +6,725 | 69% |
| min_variance | 1M | equity_index_future | all | 106 | +18,897 | +13,552 | +27,955 | +158.9 | +6,528.1 | +14,244 | 76% |
| min_variance | 1M | equity_index_future | high_vol | 50 | +25,387 | +16,850 | +39,001 | +189.0 | +7,954.7 | +21,963 | 72% |
| min_variance | 1M | equity_index_future | low_vol | 56 | +7,267 | +5,385 | +12,653 | +131.2 | +4,857.0 | +7,137 | 80% |
| min_variance | 1M | equity_put | all | 45 | -4,118 | -4,385 | +9,159 | -1,512.0 | +11,373.6 | +15,056 | 42% |
| min_variance | 1M | equity_put | low_vol | 26 | +7,920 | +1,030 | +6,824 | -2,673.9 | +7,353.9 | +8,468 | 62% |
| min_variance | 1M | etf | all | 98 | +20,828 | +21,335 | +35,632 | +1,013.9 | +6,010.9 | +13,283 | 18% |
| min_variance | 1M | etf | high_vol | 49 | +22,798 | +22,916 | +46,013 | +1,108.3 | +7,031.3 | +21,988 | 20% |
| min_variance | 1M | etf | low_vol | 49 | +11,084 | +12,213 | +15,500 | +895.9 | +4,413.4 | +2,390 | 16% |
| min_variance | 1M | fx_forward | all | 36 | -1,656 | +3,970 | +6,391 | +27.5 | +10,807.9 | +2,394 | 7% |
| min_variance | 1M | fx_forward | high_vol | 25 | -8,765 | +5,016 | +7,749 | +32.5 | +11,811.5 | +2,701 | 5% |
| min_variance | 1M | fx_future | all | 22 | -3,754 | +7,968 | +11,459 | +40.5 | +11,732.4 | +3,451 | 6% |
| min_variance | 1M | index_option | all | 50 | +4,338 | -518 | +16,097 | -1,606.5 | +9,970.3 | +18,221 | 43% |
| min_variance | 1M | index_option | high_vol | 24 | +589 | -5,426 | +21,127 | -1,937.3 | +12,467.9 | +28,491 | 48% |
| min_variance | 1M | index_option | low_vol | 26 | +5,472 | +1,755 | +8,587 | -1,280.8 | +6,651.3 | +8,112 | 39% |
| min_variance | 1M | inverse_etf | all | 65 | +15,963 | +9,490 | +19,732 | +1,539.1 | +7,216.2 | +8,703 | 0% |
| min_variance | 1M | inverse_etf | high_vol | 31 | +21,256 | +11,299 | +31,187 | +2,447.4 | +8,066.3 | +17,441 | 0% |
| min_variance | 1M | inverse_etf | low_vol | 34 | +9,011 | +4,952 | +8,386 | +897.9 | +6,550.0 | +2,535 | 0% |
| min_variance | 1M | treasury_future | all | 105 | +7,270 | +3,477 | +3,936 | +90.7 | +6,944.9 | +368 | 67% |
| min_variance | 1M | treasury_future | high_vol | 49 | +7,463 | +3,117 | +4,197 | +88.0 | +8,141.7 | +993 | 63% |
| min_variance | 1M | treasury_future | low_vol | 56 | +5,126 | +3,847 | +3,706 | +93.3 | +5,553.3 | -234 | 71% |
| sector | 1M | etf | all | 106 | +18,627 | +18,462 | +25,382 | +1,077.2 | +5,914.4 | +5,843 | 100% |
| sector | 1M | etf | high_vol | 50 | +25,161 | +25,093 | +35,165 | +1,055.9 | +7,481.0 | +9,017 | 100% |
| sector | 1M | etf | low_vol | 56 | +8,230 | +8,047 | +12,054 | +1,096.8 | +3,947.9 | +2,910 | 100% |
| duration | 1M | etf | all | 106 | +856 | +491 | +1,327 | +586.6 | +603.7 | +249 | 15% |
| duration | 1M | etf | high_vol | 50 | +1,198 | +937 | +1,659 | +477.2 | +675.0 | +245 | 16% |
| duration | 1M | etf | low_vol | 56 | +506 | +89 | +1,021 | +678.9 | +536.1 | +253 | 14% |
| duration | 1M | treasury_future | all | 106 | +952 | +917 | +1,738 | +65.8 | +2,090.9 | +755 | 92% |
| duration | 1M | treasury_future | high_vol | 50 | +1,321 | +1,223 | +2,158 | +66.1 | +2,389.2 | +869 | 90% |
| duration | 1M | treasury_future | low_vol | 56 | +586 | +557 | +1,279 | +65.6 | +1,795.2 | +656 | 94% |
| credit | 1M | high_yield | all | 106 | +406 | +557 | +1,145 | +402.3 | +2,012.5 | +186 | 67% |
| credit | 1M | high_yield | high_vol | 50 | +45 | -75 | +1,444 | +373.8 | +2,773.2 | +1,145 | 61% |
| credit | 1M | high_yield | low_vol | 56 | +376 | +867 | +657 | +426.7 | +964.4 | -636 | 71% |
| credit | 1M | ig_corporate | all | 100 | +4,513 | +4,049 | +5,030 | +627.2 | +1,580.1 | +353 | 55% |
| credit | 1M | ig_corporate | high_vol | 44 | +6,331 | +5,519 | +6,456 | +536.4 | +1,884.7 | +401 | 61% |
| credit | 1M | ig_corporate | low_vol | 56 | +2,351 | +2,624 | +3,638 | +698.6 | +1,291.4 | +316 | 50% |
| credit | 1M | leveraged_loan | all | 93 | +261 | -452 | -45 | +735.0 | +3,373.9 | -328 | 6% |
| credit | 1M | leveraged_loan | high_vol | 38 | -124 | -1,487 | -142 | +664.0 | +5,078.3 | +681 | 11% |
| credit | 1M | leveraged_loan | low_vol | 55 | +431 | +253 | -29 | +787.1 | +888.9 | -1,069 | 2% |
| fx | 1M | fx_forward | all | 106 | +1,037 | +1,064 | +1,244 | +9.2 | +69.4 | +171 | 95% |
| fx | 1M | fx_forward | high_vol | 50 | +1,091 | +1,136 | +1,456 | +9.3 | +83.7 | +311 | 93% |
| fx | 1M | fx_forward | low_vol | 56 | +1,020 | +1,015 | +1,031 | +9.0 | +47.5 | +7 | 97% |
| fx | 1M | fx_future | all | 103 | +1,227 | +1,862 | +2,052 | +14.2 | +277.5 | +176 | 4% |
| fx | 1M | fx_future | high_vol | 48 | +1,290 | +1,987 | +2,441 | +14.4 | +303.2 | +440 | 9% |
| fx | 1M | fx_future | low_vol | 55 | +1,200 | +1,853 | +1,791 | +14.0 | +250.6 | -76 | 0% |
| fx | 1M | fx_spot | all | 106 | +1,002 | +783 | +1,174 | +212.6 | +85.9 | +178 | 2% |
| fx | 1M | fx_spot | high_vol | 50 | +1,073 | +892 | +1,368 | +200.0 | +98.2 | +276 | 2% |
| fx | 1M | fx_spot | low_vol | 56 | +984 | +762 | +1,052 | +227.6 | +68.5 | +62 | 1% |

## The existing resizing challenger (hedge-2-sizing-exp), 2018 on

| Objective | Horizon | Dates | ΔU λ=1 | t | Eras + (of those after 2018) |
|---|---|---|---|---|---|
| min_variance | 1W | 219 | -2 | -1.08 | 1 |
| target_vol | 1W | 219 | -5 | -1.64 | 0 |
| beta | 1W | 219 | -179 | -1.12 | 1 |
| systematic | 1W | 219 | -84 | -1.50 | 0 |
| sector | 1W | 219 | +0 | — | 0 |
| name | 1W | 219 | -117 | -3.97 | 0 |
| crash | 1W | 219 | -623 | -1.28 | 1 |
| es | 1W | 219 | -1 | -0.65 | 0 |
| var | 1W | 219 | -1 | -0.65 | 0 |
| drawdown | 1W | 219 | -1 | -0.65 | 0 |
| duration | 1W | 219 | +0 | — | 0 |
| curve | 1W | 219 | +0 | — | 0 |
| credit | 1W | 219 | +0 | — | 0 |
| fx | 1W | 219 | +3 | +0.17 | 1 |
| commodity | 1W | 219 | -19 | -0.64 | 0 |
| crypto | 1W | 111 | +0 | — | 0 |
| volatility | 1W | 205 | +0 | — | 0 |
| min_variance | 1M | 104 | -96 | -0.79 | 1 |
| target_vol | 1M | 104 | -8 | -0.17 | 2 |
| beta | 1M | 104 | +109 | +1.83 | 1 |
| systematic | 1M | 104 | +55 | +0.98 | 2 |
| sector | 1M | 104 | +0 | — | 0 |
| name | 1M | 104 | +21 | +0.20 | 1 |
| crash | 1M | 104 | +146 | +2.12 | 2 |
| es | 1M | 104 | -26 | -0.24 | 2 |
| var | 1M | 104 | -2,009 | -2.34 | 0 |
| drawdown | 1M | 104 | -26 | -0.24 | 2 |
| duration | 1M | 104 | -19 | -1.31 | 1 |
| curve | 1M | 104 | -19 | -1.31 | 1 |
| credit | 1M | 104 | -26 | -0.18 | 1 |
| fx | 1M | 104 | -56 | -1.98 | 0 |
| commodity | 1M | 104 | -52 | -0.37 | 1 |
| crypto | 1M | 52 | +0 | — | 0 |
| volatility | 1M | 97 | +0 | — | 0 |
| min_variance | 3M | 34 | -22 | -0.28 | 1 |
| target_vol | 3M | 34 | -22 | -1.21 | 1 |
| beta | 3M | 34 | +74 | +1.15 | 0 |
| systematic | 3M | 34 | -22 | -0.24 | 1 |
| sector | 3M | 34 | +0 | — | 0 |
| name | 3M | 34 | -132 | -2.32 | 0 |
| crash | 3M | 34 | +13 | +0.07 | 2 |
| es | 3M | 34 | +112 | +2.31 | 2 |
| var | 3M | 34 | +112 | +2.31 | 2 |
| drawdown | 3M | 34 | +112 | +2.31 | 2 |
| duration | 3M | 34 | +0 | — | 0 |
| curve | 3M | 34 | +0 | — | 0 |
| credit | 3M | 34 | -238 | -0.43 | 1 |
| fx | 3M | 34 | -45 | -0.33 | 1 |
| commodity | 3M | 34 | +339 | +0.55 | 1 |
| crypto | 3M | 16 | +0 | — | 0 |
| volatility | 3M | 31 | +0 | — | 0 |

## Answers

- **Did better risk estimation make hedges better?** 0 of 48 cells pass every gate; 17 have a positive point estimate at λ = 1.
- **Did sizing improve (objective / regime resizing)?** 0 of 48 cells pass every gate; 32 have a positive point estimate at λ = 1. / 0 of 48 cells pass every gate; 35 have a positive point estimate at λ = 1. The strongest evidence of the program is here: larger hedges (learned multiples up to the 1.5× cap) raise realised utility at λ ≤ 2 for single-name, beta, systematic, sector, target-vol and tail objectives at 1W (and several at 1M), surviving FDR in most of the eras — but every such cell fails H4 (cost and basis error rise with size) and the sign reverses at λ ≥ 5–10.
- **Did product choice improve?** 0 of 14 cells pass every gate; 4 have a positive point estimate at λ = 1.
- **Did validated Alpha improve the hedge?** 0 of 15 cells pass every gate; 2 have a positive point estimate at λ = 1.
- **Live-shadow eligibility:** none. **Production eligibility:** none.
- **Next data bottleneck:** real option chains with skew (option hedge costs and tail protection are model-priced today), dated futures curves (roll and basis), point-in-time credit spreads (OAS history) and liquidity data.
