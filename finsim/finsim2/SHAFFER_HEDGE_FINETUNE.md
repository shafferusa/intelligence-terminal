# Shaffer Hedge fine-tune — a λ-conditional size surface

Run 2026-09-26 19:23:11 · 3267.3 s · `python -m finsim2 lab --finetune`. Research only: hedge-2, its sizing, the resizing live shadow and the fixed gates H1–H5 are unchanged. Method: `hedge/hedgetune.py` (module docstring).

There is no single optimal hedge multiplier. Utility U = risk reduction − λ · profit sacrificed − cost: a risk-only user (λ small) wants a bigger hedge than a profit-sensitive one (λ large). So the fine-tune learns a **surface** m(objective, λ, risk class, horizon, volatility regime) × hedge-2's size, m ∈ [0.5, 1.5]: best fit per node and λ on outcomes matured before each era, shrunk toward the parent (dates / (dates + 60)), then made non-increasing in λ. Each (objective, horizon, λ) cell is validated walk-forward against hedge-2 at that same λ with H1 (utility gain, BH FDR across all cells), H2 (≥ 3/4 eras), H3 (tail not worse), H4 (cost ≤ +10%, basis error ≤ +5%), H5 (holds without crises).

## Key findings

- 560 (objective, horizon, λ) cells tested; 96 survive FDR on utility; **2 pass every gate**: 1M Commodity/target_vol at λ = 5.0, 1M Commodity/target_vol at λ = 10.0.
- **Optimal size by λ:** production size (hedge-2) = 1.00× in every row. 1W global: λ 0.5 → 1.40×, λ 1.0 → 1.35×, λ 2.0 → 1.22×, λ 5.0 → 0.87×, λ 10.0 → 0.56×; 1M global: λ 0.5 → 1.39×, λ 1.0 → 1.35×, λ 2.0 → 1.27×, λ 5.0 → 0.96×, λ 10.0 → 0.61×; 3M global: λ 0.5 → 1.22×, λ 1.0 → 1.16×, λ 2.0 → 1.03×, λ 5.0 → 0.73×, λ 10.0 → 0.73×. The multiple falls as λ rises (isotonic by construction): risk-only users get larger hedges, profit-sensitive users lighter ones. Per objective and volatility regime in SHAFFER_HEDGE_FINETUNE.md §1.
- **H4:** Yes. 29 cells beat hedge-2 on utility with FDR but failed H4: 29 on cost (> +10%), 20 on basis error (> +5%); 15 are variance-sensitive objectives. A bigger hedge mechanically costs proportionally more — the median cell bought 40.90 of extra risk reduction per extra dollar of cost. H4 caps the size of the hedge, not its efficiency. H4 is NOT changed; an efficiency-based redesign is proposed for a new research version only.
- **Alpha link:** 1W: 58351 cases; per-Alpha-bucket multiples beat one multiple walk-forward in 0/17 objectives at t ≥ 2; 1M: 27680 cases; per-Alpha-bucket multiples beat one multiple walk-forward in 1/17 objectives at t ≥ 2 (min_variance); 3M: 9033 cases; per-Alpha-bucket multiples beat one multiple walk-forward in 0/17 objectives at t ≥ 2. Across all 50 tests, 0 survive Benjamini–Hochberg FDR: **no** — conditioning the hedge size on the book's validated Alpha does not improve realised utility out of sample; isolated t ≥ 2 cells are what chance produces in this many tests.
- **Live shadow:** the passing cells are recorded in hedge-lambda-sizing-exp (research) with their multiples; they are not shadowed because the hedge live grader measures variance per hedge group, not utility at a chosen λ.

## 1. The surface — per objective, horizon and λ

*Historical best fit*: in-sample over all matured cases. *Shrunk*: pooled toward the parent and isotonic in λ — the deployable value. Risk reduction / profit sacrificed / cost / basis: realised, walk-forward, learned multiple (hedge-2 in brackets). Utility: ΔU vs hedge-2 at that λ (t). Gate: status, and which gates failed.

| Objective | Horizon | λ | Production size | Historical best-fit multiplier | Shrunk multiplier | Risk reduction | Profit sacrificed | Cost | Basis | Utility ΔU (t) | Gate result |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Commodity/commodity | 1W | 0.5 | 1.00× | 1.50× | 1.50× | 1,143 (935) | 433 (309) | 291 (201) | 1,840 (1,623) | 55.4 (+0.7) | NO HEDGE IMPROVEMENT — fails H1, H2, H4 |
| Commodity/commodity | 1W | 1.0 | 1.00× | 1.30× | 1.32× | 1,101 (935) | 399 (309) | 278 (201) | 1,837 (1,623) | -0.83 (-0.0) | NO HEDGE IMPROVEMENT — fails H1, H2, H4 |
| Commodity/commodity | 1W | 2.0 | 1.00× | 0.75× | 0.82× | 912 (935) | 301 (309) | 211 (201) | 1,745 (1,623) | -18.6 (-0.2) | NO HEDGE IMPROVEMENT — fails H1, H2, H4, H5 |
| Commodity/commodity | 1W | 5.0 | 1.00× | 0.50× | 0.51× | 655 (935) | 197 (309) | 134 (201) | 1,673 (1,623) | 344 (+1.6) | NO HEDGE IMPROVEMENT — fails H1, H2 |
| Commodity/commodity | 1W | 10.0 | 1.00× | 0.50× | 0.50× | 545 (935) | 155 (309) | 101 (201) | 1,720 (1,623) | 1,246 (+2.8) | NO HEDGE IMPROVEMENT — fails H2, H3, H4 |
| Commodity/drawdown | 1W | 0.5 | 1.00× | 1.50× | 1.50× | 2,440 (1,771) | 554 (378) | 5.53 (3.9) | 3,762 (3,809) | 580 (+1.6) | NO HEDGE IMPROVEMENT — fails H1, H2, H4, H5 |
| Commodity/drawdown | 1W | 1.0 | 1.00× | 1.50× | 1.50× | 2,440 (1,771) | 554 (378) | 5.53 (3.9) | 3,762 (3,809) | 492 (+1.3) | NO HEDGE IMPROVEMENT — fails H1, H4, H5 |
| Commodity/drawdown | 1W | 2.0 | 1.00× | 1.50× | 1.47× | 2,427 (1,771) | 553 (378) | 5.24 (3.9) | 3,766 (3,809) | 304 (+0.7) | NO HEDGE IMPROVEMENT — fails H1, H2, H4, H5 |
| Commodity/drawdown | 1W | 5.0 | 1.00× | 0.50× | 0.51× | 1,837 (1,771) | 532 (378) | 3.43 (3.9) | 3,776 (3,809) | -704 (-1.3) | NO HEDGE IMPROVEMENT — fails H1, H2, H5 |
| Commodity/drawdown | 1W | 10.0 | 1.00× | 0.50× | 0.50× | 1,638 (1,771) | 444 (378) | 3.05 (3.9) | 3,801 (3,809) | -792 (-1.5) | NO HEDGE IMPROVEMENT — fails H1, H2, H5 |
| Commodity/es | 1W | 0.5 | 1.00× | 1.50× | 1.50× | 2,440 (1,771) | 554 (378) | 5.53 (3.9) | 3,762 (3,809) | 580 (+1.6) | NO HEDGE IMPROVEMENT — fails H1, H2, H4, H5 |
| Commodity/es | 1W | 1.0 | 1.00× | 1.50× | 1.50× | 2,440 (1,771) | 554 (378) | 5.53 (3.9) | 3,762 (3,809) | 492 (+1.3) | NO HEDGE IMPROVEMENT — fails H1, H4, H5 |
| Commodity/es | 1W | 2.0 | 1.00× | 1.50× | 1.47× | 2,427 (1,771) | 553 (378) | 5.24 (3.9) | 3,766 (3,809) | 304 (+0.7) | NO HEDGE IMPROVEMENT — fails H1, H2, H4, H5 |
| Commodity/es | 1W | 5.0 | 1.00× | 0.50× | 0.51× | 1,837 (1,771) | 532 (378) | 3.43 (3.9) | 3,776 (3,809) | -704 (-1.3) | NO HEDGE IMPROVEMENT — fails H1, H2, H5 |
| Commodity/es | 1W | 10.0 | 1.00× | 0.50× | 0.50× | 1,638 (1,771) | 444 (378) | 3.05 (3.9) | 3,801 (3,809) | -792 (-1.5) | NO HEDGE IMPROVEMENT — fails H1, H2, H5 |
| Commodity/min_variance | 1W | 0.5 | 1.00× | 1.45× | 1.46× | 3,069 (2,551) | 1,641 (1,103) | 21.4 (14.5) | 7,275 (7,396) | 242 (+0.9) | NO HEDGE IMPROVEMENT — fails H1, H4, H5 |
| Commodity/min_variance | 1W | 1.0 | 1.00× | 1.25× | 1.29× | 3,098 (2,551) | 1,620 (1,103) | 20.6 (14.5) | 7,257 (7,396) | 23.6 (+0.1) | NO HEDGE IMPROVEMENT — fails H1, H4, H5 |
| Commodity/min_variance | 1W | 2.0 | 1.00× | 0.70× | 0.81× | 2,922 (2,551) | 1,499 (1,103) | 15.6 (14.5) | 7,291 (7,396) | -422 (-1.2) | NO HEDGE IMPROVEMENT — fails H1, H2, H5 |
| Commodity/min_variance | 1W | 5.0 | 1.00× | 0.50× | 0.51× | 1,918 (2,551) | 764 (1,103) | 9.28 (14.5) | 7,583 (7,396) | 1,066 (+1.8) | NO HEDGE IMPROVEMENT — fails H1, H3 |
| Commodity/min_variance | 1W | 10.0 | 1.00× | 0.50× | 0.50× | 1,520 (2,551) | 556 (1,103) | 7.86 (14.5) | 7,712 (7,396) | 4,452 (+2.2) | NO HEDGE IMPROVEMENT — fails H3 |
| Commodity/systematic | 1W | 0.5 | 1.00× | 1.50× | 1.50× | 6,611 (5,026) | 1,611 (1,081) | 597 (403) | 3,046 (2,808) | 1,126 (+2.9) | NO HEDGE IMPROVEMENT — fails H4 |
| Commodity/systematic | 1W | 1.0 | 1.00× | 1.50× | 1.50× | 6,611 (5,026) | 1,611 (1,081) | 597 (403) | 3,046 (2,808) | 861 (+1.9) | NO HEDGE IMPROVEMENT — fails H1, H4 |
| Commodity/systematic | 1W | 2.0 | 1.00× | 1.45× | 1.43× | 6,264 (5,026) | 1,569 (1,081) | 556 (403) | 2,995 (2,808) | 108 (+0.2) | NO HEDGE IMPROVEMENT — fails H1, H2, H4, H5 |
| Commodity/systematic | 1W | 5.0 | 1.00× | 0.50× | 0.51× | 5,043 (5,026) | 1,086 (1,081) | 379 (403) | 2,887 (2,808) | 17.8 (+0.0) | NO HEDGE IMPROVEMENT — fails H1, H2, H3, H5 |
| Commodity/systematic | 1W | 10.0 | 1.00× | 0.50× | 0.50× | 4,011 (5,026) | 804 (1,081) | 270 (403) | 2,970 (2,808) | 1,890 (+1.4) | NO HEDGE IMPROVEMENT — fails H1, H2, H3, H4 |
| Commodity/target_vol | 1W | 0.5 | 1.00× | 1.50× | 1.50× | 1,233 (1,064) | 801 (658) | 3.19 (2.53) | 2,384 (2,384) | 96.5 (+1.4) | NO HEDGE IMPROVEMENT — fails H1, H2, H4 |
| Commodity/target_vol | 1W | 1.0 | 1.00× | 1.50× | 1.50× | 1,230 (1,064) | 794 (658) | 3.13 (2.53) | 2,378 (2,384) | 29.2 (+0.4) | NO HEDGE IMPROVEMENT — fails H1, H2, H4, H5 |
| Commodity/target_vol | 1W | 2.0 | 1.00× | 0.50× | 0.71× | 1,178 (1,064) | 741 (658) | 2.69 (2.53) | 2,361 (2,384) | -53 (-0.8) | NO HEDGE IMPROVEMENT — fails H1, H2, H5 |
| Commodity/target_vol | 1W | 5.0 | 1.00× | 0.50× | 0.51× | 1,089 (1,064) | 677 (658) | 2.42 (2.53) | 2,373 (2,384) | -71.2 (-0.4) | NO HEDGE IMPROVEMENT — fails H1, H2, H5 |
| Commodity/target_vol | 1W | 10.0 | 1.00× | 0.50× | 0.50× | 860 (1,064) | 514 (658) | 1.88 (2.53) | 2,419 (2,384) | 1,229 (+1.9) | NO HEDGE IMPROVEMENT — fails H2 |
| Commodity/var | 1W | 0.5 | 1.00× | 1.50× | 1.50× | 2,440 (1,771) | 554 (378) | 5.53 (3.9) | 3,762 (3,809) | 580 (+1.6) | NO HEDGE IMPROVEMENT — fails H1, H2, H4, H5 |
| Commodity/var | 1W | 1.0 | 1.00× | 1.50× | 1.50× | 2,440 (1,771) | 554 (378) | 5.53 (3.9) | 3,762 (3,809) | 492 (+1.3) | NO HEDGE IMPROVEMENT — fails H1, H4, H5 |
| Commodity/var | 1W | 2.0 | 1.00× | 1.50× | 1.47× | 2,427 (1,771) | 553 (378) | 5.24 (3.9) | 3,766 (3,809) | 304 (+0.7) | NO HEDGE IMPROVEMENT — fails H1, H2, H4, H5 |
| Commodity/var | 1W | 5.0 | 1.00× | 0.50× | 0.51× | 1,837 (1,771) | 532 (378) | 3.43 (3.9) | 3,776 (3,809) | -704 (-1.3) | NO HEDGE IMPROVEMENT — fails H1, H2, H5 |
| Commodity/var | 1W | 10.0 | 1.00× | 0.50× | 0.50× | 1,638 (1,771) | 444 (378) | 3.05 (3.9) | 3,801 (3,809) | -792 (-1.5) | NO HEDGE IMPROVEMENT — fails H1, H2, H5 |
| Credit/credit | 1W | 0.5 | 1.00× | 1.50× | 1.50× | 1,294 (957) | -12.1 (-14.1) | 204 (138) | 2,068 (1,460) | 270 (+2.9) | NO HEDGE IMPROVEMENT — fails H4 |
| Credit/credit | 1W | 1.0 | 1.00× | 1.50× | 1.50× | 1,274 (957) | 1.07 (-14.1) | 200 (138) | 2,010 (1,460) | 240 (+2.6) | NO HEDGE IMPROVEMENT — fails H4 |
| Credit/credit | 1W | 2.0 | 1.00× | 1.50× | 1.50× | 1,094 (957) | 15.1 (-14.1) | 179 (138) | 1,701 (1,460) | 38.3 (+0.4) | NO HEDGE IMPROVEMENT — fails H1, H2, H4 |
| Credit/credit | 1W | 5.0 | 1.00× | 1.50× | 1.49× | 746 (957) | -14.8 (-14.1) | 135 (138) | 1,188 (1,460) | -204 (-0.8) | NO HEDGE IMPROVEMENT — fails H1, H2, H5 |
| Credit/credit | 1W | 10.0 | 1.00× | 0.85× | 0.91× | 663 (957) | -34.5 (-14.1) | 126 (138) | 1,079 (1,460) | -78.1 (-0.2) | NO HEDGE IMPROVEMENT — fails H1, H2, H3 |
| Credit/drawdown | 1W | 0.5 | 1.00× | 1.00× | 1.08× | 0 (0) | -0 (-0) | 0 (0) | 1,224 (1,224) | 0 (—) | NO HEDGE IMPROVEMENT — fails H1, H2, H5 |
| Credit/drawdown | 1W | 1.0 | 1.00× | 1.00× | 1.08× | 0 (0) | -0 (-0) | 0 (0) | 1,224 (1,224) | 0 (—) | NO HEDGE IMPROVEMENT — fails H1, H2, H5 |
| Credit/drawdown | 1W | 2.0 | 1.00× | 1.00× | 1.08× | 0 (0) | -0 (-0) | 0 (0) | 1,224 (1,224) | 0 (—) | NO HEDGE IMPROVEMENT — fails H1, H2, H5 |
| Credit/drawdown | 1W | 5.0 | 1.00× | 1.00× | 1.07× | 0 (0) | -0 (-0) | 0 (0) | 1,224 (1,224) | 0 (—) | NO HEDGE IMPROVEMENT — fails H1, H2, H5 |
| Credit/drawdown | 1W | 10.0 | 1.00× | 1.00× | 1.07× | 0 (0) | -0 (-0) | 0 (0) | 1,224 (1,224) | 0 (—) | NO HEDGE IMPROVEMENT — fails H1, H2, H5 |
| Credit/es | 1W | 0.5 | 1.00× | 1.00× | 1.08× | 0 (0) | -0 (-0) | 0 (0) | 1,224 (1,224) | 0 (—) | NO HEDGE IMPROVEMENT — fails H1, H2, H5 |
| Credit/es | 1W | 1.0 | 1.00× | 1.00× | 1.08× | 0 (0) | -0 (-0) | 0 (0) | 1,224 (1,224) | 0 (—) | NO HEDGE IMPROVEMENT — fails H1, H2, H5 |
| Credit/es | 1W | 2.0 | 1.00× | 1.00× | 1.08× | 0 (0) | -0 (-0) | 0 (0) | 1,224 (1,224) | 0 (—) | NO HEDGE IMPROVEMENT — fails H1, H2, H5 |
| Credit/es | 1W | 5.0 | 1.00× | 1.00× | 1.07× | 0 (0) | -0 (-0) | 0 (0) | 1,224 (1,224) | 0 (—) | NO HEDGE IMPROVEMENT — fails H1, H2, H5 |
| Credit/es | 1W | 10.0 | 1.00× | 1.00× | 1.07× | 0 (0) | -0 (-0) | 0 (0) | 1,224 (1,224) | 0 (—) | NO HEDGE IMPROVEMENT — fails H1, H2, H5 |
| Credit/min_variance | 1W | 0.5 | 1.00× | 0.80× | 1.12× | 11 (19.9) | -0.571 (-4.69) | 3.43 (3.08) | 2,487 (2,477) | -11.4 (-1.0) | NO HEDGE IMPROVEMENT — fails H1, H2, H4, H5 |
| Credit/min_variance | 1W | 1.0 | 1.00× | 0.85× | 1.12× | 11 (19.9) | -0.571 (-4.69) | 3.43 (3.08) | 2,487 (2,477) | -13.4 (-0.8) | NO HEDGE IMPROVEMENT — fails H1, H2, H4, H5 |
| Credit/min_variance | 1W | 2.0 | 1.00× | 0.95× | 1.12× | 11 (19.9) | -0.571 (-4.69) | 3.43 (3.08) | 2,487 (2,477) | -17.6 (-0.7) | NO HEDGE IMPROVEMENT — fails H1, H2, H4, H5 |
| Credit/min_variance | 1W | 5.0 | 1.00× | 1.15× | 1.12× | 11 (19.9) | -0.571 (-4.69) | 3.43 (3.08) | 2,487 (2,477) | -29.9 (-0.6) | NO HEDGE IMPROVEMENT — fails H1, H2, H4, H5 |
| Credit/min_variance | 1W | 10.0 | 1.00× | 1.50× | 1.12× | 11 (19.9) | -0.571 (-4.69) | 3.43 (3.08) | 2,487 (2,477) | -50.5 (-0.5) | NO HEDGE IMPROVEMENT — fails H1, H2, H4, H5 |
| Credit/name | 1W | 0.5 | 1.00× | 1.50× | 1.50× | 2,546 (1,786) | 58.2 (39.3) | 155 (104) | 1,670 (1,140) | 699 (+3.9) | NO HEDGE IMPROVEMENT — fails H4 |
| Credit/name | 1W | 1.0 | 1.00× | 1.50× | 1.50× | 2,546 (1,786) | 58.2 (39.3) | 155 (104) | 1,670 (1,140) | 690 (+3.4) | NO HEDGE IMPROVEMENT — fails H4 |
| Credit/name | 1W | 2.0 | 1.00× | 1.50× | 1.50× | 2,516 (1,786) | 60.1 (39.3) | 151 (104) | 1,635 (1,140) | 641 (+2.7) | NO HEDGE IMPROVEMENT — fails H4 |
| Credit/name | 1W | 5.0 | 1.00× | 1.50× | 1.49× | 1,245 (1,786) | 37.7 (39.3) | 81.2 (104) | 836 (1,140) | -510 (-1.6) | NO HEDGE IMPROVEMENT — fails H1, H2, H3, H5 |
| Credit/name | 1W | 10.0 | 1.00× | 0.75× | 0.84× | 937 (1,786) | 19.2 (39.3) | 52.7 (104) | 662 (1,140) | -596 (-0.8) | NO HEDGE IMPROVEMENT — fails H1, H2, H3 |
| Credit/target_vol | 1W | 0.5 | 1.00× | 1.00× | 1.21× | 0 (0) | -0 (-0) | 0 (0) | 566 (566) | 0 (—) | INSUFFICIENT DATA |
| Credit/target_vol | 1W | 1.0 | 1.00× | 1.00× | 1.21× | 0 (0) | -0 (-0) | 0 (0) | 566 (566) | 0 (—) | INSUFFICIENT DATA |
| Credit/target_vol | 1W | 2.0 | 1.00× | 1.00× | 1.20× | 0 (0) | -0 (-0) | 0 (0) | 566 (566) | 0 (—) | INSUFFICIENT DATA |
| Credit/target_vol | 1W | 5.0 | 1.00× | 1.00× | 1.19× | 0 (0) | -0 (-0) | 0 (0) | 566 (566) | 0 (—) | INSUFFICIENT DATA |
| Credit/target_vol | 1W | 10.0 | 1.00× | 1.00× | 1.17× | 0 (0) | -0 (-0) | 0 (0) | 566 (566) | 0 (—) | INSUFFICIENT DATA |
| Credit/var | 1W | 0.5 | 1.00× | 1.00× | 1.08× | 0 (0) | -0 (-0) | 0 (0) | 1,224 (1,224) | 0 (—) | NO HEDGE IMPROVEMENT — fails H1, H2, H5 |
| Credit/var | 1W | 1.0 | 1.00× | 1.00× | 1.08× | 0 (0) | -0 (-0) | 0 (0) | 1,224 (1,224) | 0 (—) | NO HEDGE IMPROVEMENT — fails H1, H2, H5 |
| Credit/var | 1W | 2.0 | 1.00× | 1.00× | 1.08× | 0 (0) | -0 (-0) | 0 (0) | 1,224 (1,224) | 0 (—) | NO HEDGE IMPROVEMENT — fails H1, H2, H5 |
| Credit/var | 1W | 5.0 | 1.00× | 1.00× | 1.07× | 0 (0) | -0 (-0) | 0 (0) | 1,224 (1,224) | 0 (—) | NO HEDGE IMPROVEMENT — fails H1, H2, H5 |
| Credit/var | 1W | 10.0 | 1.00× | 1.00× | 1.07× | 0 (0) | -0 (-0) | 0 (0) | 1,224 (1,224) | 0 (—) | NO HEDGE IMPROVEMENT — fails H1, H2, H5 |
| Crypto/crypto | 1W | 0.5 | 1.00× | 1.20× | 1.29× | 50,269 (48,520) | 13,171 (9,591) | 1,517 (1,099) | 20,613 (12,010) | -458 (-0.1) | NO HEDGE IMPROVEMENT — fails H1, H2, H3, H4 |
| Crypto/crypto | 1W | 1.0 | 1.00× | 1.15× | 1.24× | 50,295 (48,520) | 13,027 (9,591) | 1,501 (1,099) | 20,422 (12,010) | -2,062 (-0.4) | NO HEDGE IMPROVEMENT — fails H1, H2, H3, H4, H5 |
| Crypto/crypto | 1W | 2.0 | 1.00× | 1.10× | 1.17× | 50,958 (48,520) | 12,553 (9,591) | 1,444 (1,099) | 19,362 (12,010) | -3,831 (-0.7) | NO HEDGE IMPROVEMENT — fails H1, H2, H3, H4, H5 |
| Crypto/crypto | 1W | 5.0 | 1.00× | 0.60× | 0.74× | 39,117 (48,520) | 7,292 (9,591) | 834 (1,099) | 7,766 (12,010) | 2,357 (+0.3) | NO HEDGE IMPROVEMENT — fails H1, H2, H3 |
| Crypto/crypto | 1W | 10.0 | 1.00× | 0.50× | 0.51× | 29,965 (48,520) | 5,333 (9,591) | 612 (1,099) | 6,572 (12,010) | 24,516 (+1.0) | NO HEDGE IMPROVEMENT — fails H1, H2, H3 |
| Crypto/drawdown | 1W | 0.5 | 1.00× | 1.50× | 1.49× | 61,521 (45,494) | 4,582 (3,153) | 386 (268) | 7,868 (8,871) | 15,194 (+2.3) | NO HEDGE IMPROVEMENT — fails H2, H4 |
| Crypto/drawdown | 1W | 1.0 | 1.00× | 1.50× | 1.47× | 61,470 (45,494) | 4,572 (3,153) | 386 (268) | 7,864 (8,871) | 14,439 (+2.2) | NO HEDGE IMPROVEMENT — fails H2, H4 |
| Crypto/drawdown | 1W | 2.0 | 1.00× | 1.50× | 1.43× | 60,004 (45,494) | 4,467 (3,153) | 376 (268) | 7,873 (8,871) | 11,773 (+1.8) | NO HEDGE IMPROVEMENT — fails H1, H2, H4 |
| Crypto/drawdown | 1W | 5.0 | 1.00× | 1.50× | 1.32× | 39,793 (45,494) | 3,406 (3,153) | 264 (268) | 9,965 (8,871) | -6,962 (-1.0) | NO HEDGE IMPROVEMENT — fails H1, H2, H3, H4, H5 |
| Crypto/drawdown | 1W | 10.0 | 1.00× | 1.50× | 1.16× | 32,360 (45,494) | 2,899 (3,153) | 220 (268) | 10,927 (8,871) | -10,544 (-0.9) | NO HEDGE IMPROVEMENT — fails H1, H2, H3, H4 |
| Crypto/es | 1W | 0.5 | 1.00× | 1.50× | 1.49× | 61,521 (45,494) | 4,582 (3,153) | 386 (268) | 7,868 (8,871) | 15,194 (+2.3) | NO HEDGE IMPROVEMENT — fails H2, H4 |
| Crypto/es | 1W | 1.0 | 1.00× | 1.50× | 1.47× | 61,470 (45,494) | 4,572 (3,153) | 386 (268) | 7,864 (8,871) | 14,439 (+2.2) | NO HEDGE IMPROVEMENT — fails H2, H4 |
| Crypto/es | 1W | 2.0 | 1.00× | 1.50× | 1.43× | 60,004 (45,494) | 4,467 (3,153) | 376 (268) | 7,873 (8,871) | 11,773 (+1.8) | NO HEDGE IMPROVEMENT — fails H1, H2, H4 |
| Crypto/es | 1W | 5.0 | 1.00× | 1.50× | 1.32× | 39,793 (45,494) | 3,406 (3,153) | 264 (268) | 9,965 (8,871) | -6,962 (-1.0) | NO HEDGE IMPROVEMENT — fails H1, H2, H3, H4, H5 |
| Crypto/es | 1W | 10.0 | 1.00× | 1.50× | 1.16× | 32,360 (45,494) | 2,899 (3,153) | 220 (268) | 10,927 (8,871) | -10,544 (-0.9) | NO HEDGE IMPROVEMENT — fails H1, H2, H3, H4 |
| Crypto/min_variance | 1W | 0.5 | 1.00× | 1.30× | 1.36× | 56,652 (49,981) | 11,076 (7,915) | 1,240 (883) | 14,521 (14,973) | 4,733 (+1.5) | NO HEDGE IMPROVEMENT — fails H1, H2, H3, H4 |
| Crypto/min_variance | 1W | 1.0 | 1.00× | 1.25× | 1.31× | 56,757 (49,981) | 11,006 (7,915) | 1,233 (883) | 14,479 (14,973) | 3,335 (+1.0) | NO HEDGE IMPROVEMENT — fails H1, H2, H3, H4 |
| Crypto/min_variance | 1W | 2.0 | 1.00× | 1.20× | 1.24× | 57,175 (49,981) | 10,602 (7,915) | 1,190 (883) | 14,194 (14,973) | 1,513 (+0.4) | NO HEDGE IMPROVEMENT — fails H1, H2, H4, H5 |
| Crypto/min_variance | 1W | 5.0 | 1.00× | 0.95× | 0.96× | 41,402 (49,981) | 7,260 (7,915) | 775 (883) | 17,452 (14,973) | -5,197 (-0.9) | NO HEDGE IMPROVEMENT — fails H1, H2, H3, H4, H5 |
| Crypto/min_variance | 1W | 10.0 | 1.00× | 0.50× | 0.51× | 30,040 (49,981) | 4,338 (7,915) | 491 (883) | 21,120 (14,973) | 16,218 (+0.7) | NO HEDGE IMPROVEMENT — fails H1, H2, H3, H4 |
| Crypto/systematic | 1W | 0.5 | 1.00× | 1.20× | 1.29× | 54,144 (52,109) | 14,721 (10,721) | 1,690 (1,225) | 21,787 (12,694) | -431 (-0.1) | NO HEDGE IMPROVEMENT — fails H1, H2, H3, H4 |
| Crypto/systematic | 1W | 1.0 | 1.00× | 1.15× | 1.24× | 54,169 (52,109) | 14,560 (10,721) | 1,672 (1,225) | 21,585 (12,694) | -2,227 (-0.4) | NO HEDGE IMPROVEMENT — fails H1, H2, H3, H4, H5 |
| Crypto/systematic | 1W | 2.0 | 1.00× | 1.10× | 1.17× | 54,899 (52,109) | 14,030 (10,721) | 1,609 (1,225) | 20,465 (12,694) | -4,213 (-0.7) | NO HEDGE IMPROVEMENT — fails H1, H2, H3, H4, H5 |
| Crypto/systematic | 1W | 5.0 | 1.00× | 0.50× | 0.67× | 41,606 (52,109) | 8,116 (10,721) | 923 (1,225) | 8,174 (12,694) | 2,823 (+0.4) | NO HEDGE IMPROVEMENT — fails H1, H2, H3 |
| Crypto/systematic | 1W | 10.0 | 1.00× | 0.50× | 0.51× | 31,939 (52,109) | 5,960 (10,721) | 681 (1,225) | 6,946 (12,694) | 27,982 (+1.1) | NO HEDGE IMPROVEMENT — fails H1, H2, H3 |
| Crypto/target_vol | 1W | 0.5 | 1.00× | 1.50× | 1.49× | 52,364 (39,992) | 8,874 (6,123) | 907 (630) | 11,946 (12,688) | 10,719 (+4.1) | NO HEDGE IMPROVEMENT — fails H2, H4 |
| Crypto/target_vol | 1W | 1.0 | 1.00× | 1.50× | 1.47× | 52,353 (39,992) | 8,856 (6,123) | 906 (630) | 11,934 (12,688) | 9,352 (+3.3) | NO HEDGE IMPROVEMENT — fails H2, H4 |
| Crypto/target_vol | 1W | 2.0 | 1.00× | 1.50× | 1.43× | 51,547 (39,992) | 8,645 (6,123) | 883 (630) | 11,803 (12,688) | 6,256 (+1.7) | NO HEDGE IMPROVEMENT — fails H1, H2, H4 |
| Crypto/target_vol | 1W | 5.0 | 1.00× | 1.15× | 1.09× | 33,944 (39,992) | 6,110 (6,123) | 590 (630) | 14,741 (12,688) | -5,943 (-1.1) | NO HEDGE IMPROVEMENT — fails H1, H2, H3, H4, H5 |
| Crypto/target_vol | 1W | 10.0 | 1.00× | 0.50× | 0.51× | 23,783 (39,992) | 3,524 (6,123) | 362 (630) | 17,465 (12,688) | 10,042 (+0.6) | NO HEDGE IMPROVEMENT — fails H1, H2, H3, H4 |
| Crypto/var | 1W | 0.5 | 1.00× | 1.50× | 1.49× | 61,521 (45,494) | 4,582 (3,153) | 386 (268) | 7,868 (8,871) | 15,194 (+2.3) | NO HEDGE IMPROVEMENT — fails H2, H4 |
| Crypto/var | 1W | 1.0 | 1.00× | 1.50× | 1.47× | 61,470 (45,494) | 4,572 (3,153) | 386 (268) | 7,864 (8,871) | 14,439 (+2.2) | NO HEDGE IMPROVEMENT — fails H2, H4 |
| Crypto/var | 1W | 2.0 | 1.00× | 1.50× | 1.43× | 60,004 (45,494) | 4,467 (3,153) | 376 (268) | 7,873 (8,871) | 11,773 (+1.8) | NO HEDGE IMPROVEMENT — fails H1, H2, H4 |
| Crypto/var | 1W | 5.0 | 1.00× | 1.50× | 1.32× | 39,793 (45,494) | 3,406 (3,153) | 264 (268) | 9,965 (8,871) | -6,962 (-1.0) | NO HEDGE IMPROVEMENT — fails H1, H2, H3, H4, H5 |
| Crypto/var | 1W | 10.0 | 1.00× | 1.50× | 1.16× | 32,360 (45,494) | 2,899 (3,153) | 220 (268) | 10,927 (8,871) | -10,544 (-0.9) | NO HEDGE IMPROVEMENT — fails H1, H2, H3, H4 |
| Equity/beta | 1W | 0.5 | 1.00× | 1.50× | 1.50× | 10,724 (8,345) | 2,879 (1,925) | 40.1 (26.9) | 4,060 (2,009) | 1,889 (+4.7) | NO HEDGE IMPROVEMENT — fails H4 |
| Equity/beta | 1W | 1.0 | 1.00× | 1.50× | 1.50× | 10,724 (8,345) | 2,879 (1,925) | 40.1 (26.9) | 4,060 (2,009) | 1,412 (+2.8) | NO HEDGE IMPROVEMENT — fails H4 |
| Equity/beta | 1W | 2.0 | 1.00× | 1.40× | 1.40× | 10,204 (8,345) | 2,635 (1,925) | 32.3 (26.9) | 3,521 (2,009) | 434 (+0.7) | NO HEDGE IMPROVEMENT — fails H1, H4, H5 |
| Equity/beta | 1W | 5.0 | 1.00× | 0.50× | 0.54× | 4,718 (8,345) | 994 (1,925) | 13.8 (26.9) | 2,487 (2,009) | 1,042 (+0.6) | NO HEDGE IMPROVEMENT — fails H1, H2, H3, H4 |
| Equity/beta | 1W | 10.0 | 1.00× | 0.50× | 0.50× | 4,626 (8,345) | 971 (1,925) | 13.7 (26.9) | 2,527 (2,009) | 5,837 (+2.0) | NO HEDGE IMPROVEMENT — fails H1, H3, H4 |
| Equity/crash | 1W | 0.5 | 1.00× | 1.50× | 1.50× | 19,402 (15,086) | 2,760 (1,856) | 1,011 (680) | 2,507 (1,651) | 3,533 (+2.3) | NO HEDGE IMPROVEMENT — fails H4 |
| Equity/crash | 1W | 1.0 | 1.00× | 1.50× | 1.50× | 19,355 (15,086) | 2,732 (1,856) | 999 (680) | 2,477 (1,651) | 3,074 (+1.9) | NO HEDGE IMPROVEMENT — fails H1, H4 |
| Equity/crash | 1W | 2.0 | 1.00× | 1.50× | 1.49× | 19,090 (15,086) | 2,644 (1,856) | 942 (680) | 2,334 (1,651) | 2,166 (+1.2) | NO HEDGE IMPROVEMENT — fails H1, H4, H5 |
| Equity/crash | 1W | 5.0 | 1.00× | 1.10× | 1.07× | 12,918 (15,086) | 1,575 (1,856) | 546 (680) | 2,011 (1,651) | -627 (-0.7) | NO HEDGE IMPROVEMENT — fails H1, H2, H3, H4, H5 |
| Equity/crash | 1W | 10.0 | 1.00× | 0.50× | 0.50× | 8,148 (15,086) | 935 (1,856) | 343 (680) | 2,911 (1,651) | 2,606 (+0.7) | NO HEDGE IMPROVEMENT — fails H1, H2, H3, H4 |
| Equity/drawdown | 1W | 0.5 | 1.00× | 1.50× | 1.50× | 22,192 (16,388) | 2,426 (1,621) | 39.5 (26.4) | 3,368 (3,280) | 5,389 (+3.7) | NO HEDGE IMPROVEMENT — fails H4 |
| Equity/drawdown | 1W | 1.0 | 1.00× | 1.50× | 1.50× | 22,192 (16,388) | 2,426 (1,621) | 39.5 (26.4) | 3,368 (3,280) | 4,986 (+3.3) | NO HEDGE IMPROVEMENT — fails H4 |
| Equity/drawdown | 1W | 2.0 | 1.00× | 1.50× | 1.49× | 22,067 (16,388) | 2,409 (1,621) | 39 (26.4) | 3,365 (3,280) | 4,090 (+2.5) | NO HEDGE IMPROVEMENT — fails H4 |
| Equity/drawdown | 1W | 5.0 | 1.00× | 1.50× | 1.42× | 19,553 (16,388) | 2,115 (1,621) | 31.7 (26.4) | 3,350 (3,280) | 692 (+0.4) | NO HEDGE IMPROVEMENT — fails H1, H2, H4, H5 |
| Equity/drawdown | 1W | 10.0 | 1.00× | 0.50× | 0.50× | 9,295 (16,388) | 832 (1,621) | 14.4 (26.4) | 4,427 (3,280) | 808 (+0.3) | NO HEDGE IMPROVEMENT — fails H1, H2, H3, H4 |
| Equity/es | 1W | 0.5 | 1.00× | 1.50× | 1.50× | 22,192 (16,388) | 2,426 (1,621) | 39.5 (26.4) | 3,368 (3,280) | 5,389 (+3.7) | NO HEDGE IMPROVEMENT — fails H4 |
| Equity/es | 1W | 1.0 | 1.00× | 1.50× | 1.50× | 22,192 (16,388) | 2,426 (1,621) | 39.5 (26.4) | 3,368 (3,280) | 4,986 (+3.3) | NO HEDGE IMPROVEMENT — fails H4 |
| Equity/es | 1W | 2.0 | 1.00× | 1.50× | 1.49× | 22,067 (16,388) | 2,409 (1,621) | 39 (26.4) | 3,365 (3,280) | 4,090 (+2.5) | NO HEDGE IMPROVEMENT — fails H4 |
| Equity/es | 1W | 5.0 | 1.00× | 1.50× | 1.42× | 19,553 (16,388) | 2,115 (1,621) | 31.7 (26.4) | 3,350 (3,280) | 692 (+0.4) | NO HEDGE IMPROVEMENT — fails H1, H2, H4, H5 |
| Equity/es | 1W | 10.0 | 1.00× | 0.50× | 0.50× | 9,295 (16,388) | 832 (1,621) | 14.4 (26.4) | 4,427 (3,280) | 808 (+0.3) | NO HEDGE IMPROVEMENT — fails H1, H2, H3, H4 |
| Equity/min_variance | 1W | 0.5 | 1.00× | 1.05× | 1.10× | 14,435 (14,172) | 4,068 (3,762) | 81.2 (70.4) | 5,389 (5,556) | 99.7 (+1.0) | NO HEDGE IMPROVEMENT — fails H1, H2, H4 |
| Equity/min_variance | 1W | 1.0 | 1.00× | 1.00× | 1.06× | 14,233 (14,172) | 3,788 (3,762) | 75.2 (70.4) | 5,528 (5,556) | 29.8 (+0.4) | NO HEDGE IMPROVEMENT — fails H1, H2 |
| Equity/min_variance | 1W | 2.0 | 1.00× | 0.85× | 0.92× | 13,088 (14,172) | 3,178 (3,762) | 60.8 (70.4) | 6,180 (5,556) | 93.2 (+0.2) | NO HEDGE IMPROVEMENT — fails H1, H2, H3, H4 |
| Equity/min_variance | 1W | 5.0 | 1.00× | 0.50× | 0.54× | 9,010 (14,172) | 1,945 (3,762) | 36.2 (70.4) | 8,337 (5,556) | 3,957 (+1.6) | NO HEDGE IMPROVEMENT — fails H1, H2, H3, H4 |
| Equity/min_variance | 1W | 10.0 | 1.00× | 0.50× | 0.50× | 8,847 (14,172) | 1,895 (3,762) | 35.7 (70.4) | 8,421 (5,556) | 13,385 (+2.7) | NO HEDGE IMPROVEMENT — fails H3, H4 |
| Equity/name | 1W | 0.5 | 1.00× | 1.50× | 1.50× | 18,234 (13,152) | 2,823 (1,887) | 674 (451) | 4,908 (2,696) | 4,390 (+11.1) | NO HEDGE IMPROVEMENT — fails H4 |
| Equity/name | 1W | 1.0 | 1.00× | 1.50× | 1.50× | 18,233 (13,152) | 2,823 (1,887) | 674 (451) | 4,908 (2,696) | 3,922 (+9.0) | NO HEDGE IMPROVEMENT — fails H4 |
| Equity/name | 1W | 2.0 | 1.00× | 1.50× | 1.49× | 18,135 (13,152) | 2,800 (1,887) | 668 (451) | 4,838 (2,696) | 2,940 (+4.9) | NO HEDGE IMPROVEMENT — fails H4 |
| Equity/name | 1W | 5.0 | 1.00× | 1.25× | 1.20× | 10,522 (13,152) | 1,542 (1,887) | 427 (451) | 3,242 (2,696) | -884 (-1.0) | NO HEDGE IMPROVEMENT — fails H1, H2, H3, H4 |
| Equity/name | 1W | 10.0 | 1.00× | 0.50× | 0.50× | 6,861 (13,152) | 951 (1,887) | 227 (451) | 3,290 (2,696) | 3,294 (+1.4) | NO HEDGE IMPROVEMENT — fails H1, H2, H3, H4 |
| Equity/sector | 1W | 0.5 | 1.00× | 1.45× | 1.45× | 11,940 (10,073) | 2,263 (1,728) | 893 (663) | 6,938 (5,139) | 1,369 (+4.4) | NO HEDGE IMPROVEMENT — fails H4 |
| Equity/sector | 1W | 1.0 | 1.00× | 1.40× | 1.41× | 11,671 (10,073) | 2,160 (1,728) | 852 (663) | 6,601 (5,139) | 976 (+3.5) | NO HEDGE IMPROVEMENT — fails H4 |
| Equity/sector | 1W | 2.0 | 1.00× | 1.25× | 1.27× | 10,829 (10,073) | 1,912 (1,728) | 741 (663) | 5,752 (5,139) | 311 (+1.9) | NO HEDGE IMPROVEMENT — fails H1, H4, H5 |
| Equity/sector | 1W | 5.0 | 1.00× | 0.65× | 0.67× | 6,072 (10,073) | 984 (1,728) | 365 (663) | 3,317 (5,139) | 16.1 (+0.0) | NO HEDGE IMPROVEMENT — fails H1, H2, H3 |
| Equity/sector | 1W | 10.0 | 1.00× | 0.50× | 0.50× | 5,626 (10,073) | 873 (1,728) | 334 (663) | 3,147 (5,139) | 4,437 (+1.8) | NO HEDGE IMPROVEMENT — fails H1, H2, H3 |
| Equity/systematic | 1W | 0.5 | 1.00× | 1.50× | 1.50× | 15,368 (11,702) | 3,275 (2,203) | 492 (330) | 4,661 (2,466) | 2,968 (+5.6) | NO HEDGE IMPROVEMENT — fails H4 |
| Equity/systematic | 1W | 1.0 | 1.00× | 1.50× | 1.50× | 15,182 (11,702) | 3,179 (2,203) | 484 (330) | 4,452 (2,466) | 2,350 (+4.3) | NO HEDGE IMPROVEMENT — fails H4 |
| Equity/systematic | 1W | 2.0 | 1.00× | 1.40× | 1.40× | 13,951 (11,702) | 2,784 (2,203) | 426 (330) | 3,552 (2,466) | 991 (+2.3) | NO HEDGE IMPROVEMENT — fails H4 |
| Equity/systematic | 1W | 5.0 | 1.00× | 0.50× | 0.54× | 6,571 (11,702) | 1,161 (2,203) | 179 (330) | 3,240 (2,466) | 231 (+0.1) | NO HEDGE IMPROVEMENT — fails H1, H2, H3, H4 |
| Equity/systematic | 1W | 10.0 | 1.00× | 0.50× | 0.50× | 6,295 (11,702) | 1,111 (2,203) | 166 (330) | 3,332 (2,466) | 5,682 (+1.9) | NO HEDGE IMPROVEMENT — fails H1, H3, H4 |
| Equity/target_vol | 1W | 0.5 | 1.00× | 1.50× | 1.50× | 9,814 (8,008) | 2,738 (2,029) | 35.5 (25.6) | 3,525 (3,385) | 1,442 (+5.6) | NO HEDGE IMPROVEMENT — fails H4 |
| Equity/target_vol | 1W | 1.0 | 1.00× | 1.45× | 1.45× | 9,375 (8,008) | 2,538 (2,029) | 33 (25.6) | 3,379 (3,385) | 851 (+3.9) | NO HEDGE IMPROVEMENT — fails H4 |
| Equity/target_vol | 1W | 2.0 | 1.00× | 1.15× | 1.18× | 7,786 (8,008) | 1,970 (2,029) | 25.8 (25.6) | 3,557 (3,385) | -104 (-0.5) | NO HEDGE IMPROVEMENT — fails H1, H2, H4 |
| Equity/target_vol | 1W | 5.0 | 1.00× | 0.50× | 0.54× | 4,607 (8,008) | 1,056 (2,029) | 13.3 (25.6) | 4,776 (3,385) | 1,479 (+1.1) | NO HEDGE IMPROVEMENT — fails H1, H2, H3, H4 |
| Equity/target_vol | 1W | 10.0 | 1.00× | 0.50× | 0.50× | 4,463 (8,008) | 1,020 (2,029) | 12.9 (25.6) | 4,841 (3,385) | 6,560 (+2.2) | NO HEDGE IMPROVEMENT — fails H3, H4 |
| Equity/var | 1W | 0.5 | 1.00× | 1.50× | 1.50× | 22,192 (16,388) | 2,426 (1,621) | 39.5 (26.4) | 3,368 (3,280) | 5,389 (+3.7) | NO HEDGE IMPROVEMENT — fails H4 |
| Equity/var | 1W | 1.0 | 1.00× | 1.50× | 1.50× | 22,192 (16,388) | 2,426 (1,621) | 39.5 (26.4) | 3,368 (3,280) | 4,986 (+3.3) | NO HEDGE IMPROVEMENT — fails H4 |
| Equity/var | 1W | 2.0 | 1.00× | 1.50× | 1.49× | 22,067 (16,388) | 2,409 (1,621) | 39 (26.4) | 3,365 (3,280) | 4,090 (+2.5) | NO HEDGE IMPROVEMENT — fails H4 |
| Equity/var | 1W | 5.0 | 1.00× | 1.50× | 1.42× | 19,553 (16,388) | 2,115 (1,621) | 31.7 (26.4) | 3,350 (3,280) | 692 (+0.4) | NO HEDGE IMPROVEMENT — fails H1, H2, H4, H5 |
| Equity/var | 1W | 10.0 | 1.00× | 0.50× | 0.50× | 9,295 (16,388) | 832 (1,621) | 14.4 (26.4) | 4,427 (3,280) | 808 (+0.3) | NO HEDGE IMPROVEMENT — fails H1, H2, H3, H4 |
| FX/fx | 1W | 0.5 | 1.00× | 1.50× | 1.50× | 47.9 (42.2) | -46.9 (-31.4) | 12 (8.05) | 283 (123) | 9.47 (+0.2) | NO HEDGE IMPROVEMENT — fails H1, H2, H4 |
| FX/fx | 1W | 1.0 | 1.00× | 1.50× | 1.50× | 47.9 (42.2) | -46.9 (-31.4) | 12 (8.05) | 283 (123) | 17.2 (+0.3) | NO HEDGE IMPROVEMENT — fails H1, H2, H4 |
| FX/fx | 1W | 2.0 | 1.00× | 1.50× | 1.50× | 47.5 (42.2) | -46.7 (-31.4) | 12 (8.05) | 281 (123) | 32.1 (+0.5) | NO HEDGE IMPROVEMENT — fails H1, H2, H4 |
| FX/fx | 1W | 5.0 | 1.00× | 1.50× | 1.49× | 46.8 (42.2) | -46.1 (-31.4) | 11.9 (8.05) | 274 (123) | 74.6 (+0.7) | NO HEDGE IMPROVEMENT — fails H1, H2, H4 |
| FX/fx | 1W | 10.0 | 1.00× | 1.50× | 1.49× | 46.9 (42.2) | -46.2 (-31.4) | 11.9 (8.05) | 273 (123) | 149 (+0.6) | NO HEDGE IMPROVEMENT — fails H1, H2, H4 |
| Rates/curve | 1W | 0.5 | 1.00× | 1.50× | 1.50× | 379 (376) | -91.8 (-106) | 40.3 (36.5) | 1,643 (1,583) | -7.62 (-0.3) | NO HEDGE IMPROVEMENT — fails H1, H2, H4 |
| Rates/curve | 1W | 1.0 | 1.00× | 1.50× | 1.50× | 347 (376) | -75.9 (-106) | 37 (36.5) | 1,594 (1,583) | -59.5 (-1.3) | NO HEDGE IMPROVEMENT — fails H1, H2, H5 |
| Rates/curve | 1W | 2.0 | 1.00× | 1.50× | 1.50× | 323 (376) | -67.6 (-106) | 34.2 (36.5) | 1,549 (1,583) | -127 (-1.6) | NO HEDGE IMPROVEMENT — fails H1, H2, H5 |
| Rates/curve | 1W | 5.0 | 1.00× | 1.45× | 1.45× | 266 (376) | -53.6 (-106) | 27.4 (36.5) | 1,442 (1,583) | -362 (-2.1) | NO HEDGE IMPROVEMENT — fails H1, H2, H5 |
| Rates/curve | 1W | 10.0 | 1.00× | 1.30× | 1.31× | 236 (376) | -56 (-106) | 23.4 (36.5) | 1,396 (1,583) | -626 (-1.6) | NO HEDGE IMPROVEMENT — fails H1, H2, H5 |
| Rates/drawdown | 1W | 0.5 | 1.00× | 1.50× | 1.50× | 0 (0) | -2.54 (-2.48) | 0.0416 (0.0407) | 1,993 (1,993) | 0.0262 (+1.2) | NO HEDGE IMPROVEMENT — fails H1, H2 |
| Rates/drawdown | 1W | 1.0 | 1.00× | 1.50× | 1.50× | 0 (0) | -2.54 (-2.48) | 0.0416 (0.0407) | 1,993 (1,993) | 0.0532 (+1.2) | NO HEDGE IMPROVEMENT — fails H1, H2 |
| Rates/drawdown | 1W | 2.0 | 1.00× | 1.50× | 1.50× | 0 (0) | -2.51 (-2.48) | 0.0412 (0.0407) | 1,993 (1,993) | 0.0506 (+1.2) | NO HEDGE IMPROVEMENT — fails H1, H2 |
| Rates/drawdown | 1W | 5.0 | 1.00× | 1.50× | 1.49× | 0 (0) | -2.43 (-2.48) | 0.0399 (0.0407) | 1,993 (1,993) | -0.254 (-1.2) | NO HEDGE IMPROVEMENT — fails H1, H2, H5 |
| Rates/drawdown | 1W | 10.0 | 1.00× | 1.50× | 1.49× | 0 (0) | -2.43 (-2.48) | 0.0399 (0.0407) | 1,993 (1,993) | -0.54 (-1.2) | NO HEDGE IMPROVEMENT — fails H1, H2, H5 |
| Rates/duration | 1W | 0.5 | 1.00× | 1.40× | 1.41× | 410 (416) | -103 (-125) | 46.7 (43) | 1,742 (1,684) | -19.9 (-0.8) | NO HEDGE IMPROVEMENT — fails H1, H2, H5 |
| Rates/duration | 1W | 1.0 | 1.00× | 1.40× | 1.41× | 376 (416) | -82.2 (-125) | 42.9 (43) | 1,676 (1,684) | -82 (-1.6) | NO HEDGE IMPROVEMENT — fails H1, H2, H5 |
| Rates/duration | 1W | 2.0 | 1.00× | 1.35× | 1.36× | 341 (416) | -72 (-125) | 38.2 (43) | 1,594 (1,684) | -174 (-2.1) | NO HEDGE IMPROVEMENT — fails H1, H2, H5 |
| Rates/duration | 1W | 5.0 | 1.00× | 1.35× | 1.36× | 285 (416) | -63.8 (-125) | 30.7 (43) | 1,483 (1,684) | -422 (-1.9) | NO HEDGE IMPROVEMENT — fails H1, H2, H5 |
| Rates/duration | 1W | 10.0 | 1.00× | 1.30× | 1.31× | 271 (416) | -69.6 (-125) | 27 (43) | 1,435 (1,684) | -678 (-1.5) | NO HEDGE IMPROVEMENT — fails H1, H2, H5 |
| Rates/es | 1W | 0.5 | 1.00× | 1.50× | 1.50× | 0 (0) | -2.54 (-2.48) | 0.0416 (0.0407) | 1,993 (1,993) | 0.0262 (+1.2) | NO HEDGE IMPROVEMENT — fails H1, H2 |
| Rates/es | 1W | 1.0 | 1.00× | 1.50× | 1.50× | 0 (0) | -2.54 (-2.48) | 0.0416 (0.0407) | 1,993 (1,993) | 0.0532 (+1.2) | NO HEDGE IMPROVEMENT — fails H1, H2 |
| Rates/es | 1W | 2.0 | 1.00× | 1.50× | 1.50× | 0 (0) | -2.51 (-2.48) | 0.0412 (0.0407) | 1,993 (1,993) | 0.0506 (+1.2) | NO HEDGE IMPROVEMENT — fails H1, H2 |
| Rates/es | 1W | 5.0 | 1.00× | 1.50× | 1.49× | 0 (0) | -2.43 (-2.48) | 0.0399 (0.0407) | 1,993 (1,993) | -0.254 (-1.2) | NO HEDGE IMPROVEMENT — fails H1, H2, H5 |
| Rates/es | 1W | 10.0 | 1.00× | 1.50× | 1.49× | 0 (0) | -2.43 (-2.48) | 0.0399 (0.0407) | 1,993 (1,993) | -0.54 (-1.2) | NO HEDGE IMPROVEMENT — fails H1, H2, H5 |
| Rates/min_variance | 1W | 0.5 | 1.00× | 1.50× | 1.50× | 884 (666) | -232 (-155) | 16.2 (10.8) | 4,162 (4,057) | 251 (+3.5) | NO HEDGE IMPROVEMENT — fails H4 |
| Rates/min_variance | 1W | 1.0 | 1.00× | 1.50× | 1.50× | 883 (666) | -232 (-155) | 16.1 (10.8) | 4,162 (4,057) | 289 (+3.4) | NO HEDGE IMPROVEMENT — fails H4 |
| Rates/min_variance | 1W | 2.0 | 1.00× | 1.50× | 1.50× | 878 (666) | -230 (-155) | 16 (10.8) | 4,157 (4,057) | 357 (+3.1) | NO HEDGE IMPROVEMENT — fails H4 |
| Rates/min_variance | 1W | 5.0 | 1.00× | 1.50× | 1.49× | 780 (666) | -209 (-155) | 13.8 (10.8) | 4,104 (4,057) | 383 (+2.1) | NO HEDGE IMPROVEMENT — fails H2, H4 |
| Rates/min_variance | 1W | 10.0 | 1.00× | 1.50× | 1.49× | 527 (666) | -140 (-155) | 8.54 (10.8) | 4,024 (4,057) | -282 (-0.8) | NO HEDGE IMPROVEMENT — fails H1, H2, H5 |
| Rates/name | 1W | 0.5 | 1.00× | 1.50× | 1.50× | 967 (696) | -104 (-70.7) | 48.4 (32.6) | 1,093 (880) | 272 (+5.1) | NO HEDGE IMPROVEMENT — fails H4 |
| Rates/name | 1W | 1.0 | 1.00× | 1.50× | 1.50× | 957 (696) | -101 (-70.7) | 47.9 (32.6) | 1,088 (880) | 277 (+4.5) | NO HEDGE IMPROVEMENT — fails H4 |
| Rates/name | 1W | 2.0 | 1.00× | 1.50× | 1.50× | 804 (696) | -68.3 (-70.7) | 41.5 (32.6) | 1,034 (880) | 94.6 (+1.0) | NO HEDGE IMPROVEMENT — fails H1, H2, H4 |
| Rates/name | 1W | 5.0 | 1.00× | 1.50× | 1.49× | 614 (696) | -59.2 (-70.7) | 32.2 (32.6) | 926 (880) | -139 (-0.7) | NO HEDGE IMPROVEMENT — fails H1, H2, H4, H5 |
| Rates/name | 1W | 10.0 | 1.00× | 1.50× | 1.49× | 538 (696) | -56.8 (-70.7) | 28 (32.6) | 888 (880) | -293 (-0.8) | NO HEDGE IMPROVEMENT — fails H1, H2, H3, H5 |
| Rates/systematic | 1W | 0.5 | 1.00× | 1.50× | 1.50× | 2,212 (1,784) | -161 (-123) | 81 (58.5) | 2,729 (2,314) | 424 (+3.7) | NO HEDGE IMPROVEMENT — fails H4 |
| Rates/systematic | 1W | 1.0 | 1.00× | 1.50× | 1.50× | 2,077 (1,784) | -135 (-123) | 74.9 (58.5) | 2,627 (2,314) | 289 (+2.6) | NO HEDGE IMPROVEMENT — fails H2, H4 |
| Rates/systematic | 1W | 2.0 | 1.00× | 1.50× | 1.50× | 1,757 (1,784) | -90.3 (-123) | 62.5 (58.5) | 2,422 (2,314) | -96.3 (-0.6) | NO HEDGE IMPROVEMENT — fails H1, H2, H5 |
| Rates/systematic | 1W | 5.0 | 1.00× | 1.50× | 1.49× | 1,309 (1,784) | -60.9 (-123) | 46.7 (58.5) | 2,150 (2,314) | -774 (-2.1) | NO HEDGE IMPROVEMENT — fails H1, H2, H3, H5 |
| Rates/systematic | 1W | 10.0 | 1.00× | 1.10× | 1.13× | 1,065 (1,784) | -42.2 (-123) | 36.2 (58.5) | 2,005 (2,314) | -1,505 (-1.9) | NO HEDGE IMPROVEMENT — fails H1, H2, H3, H5 |
| Rates/target_vol | 1W | 0.5 | 1.00× | 1.00× | 1.06× | 0 (0) | -0 (-0) | 0 (0) | 143 (143) | 0 (—) | NO HEDGE IMPROVEMENT — fails H1, H2, H5 |
| Rates/target_vol | 1W | 1.0 | 1.00× | 1.00× | 1.06× | 0 (0) | -0 (-0) | 0 (0) | 143 (143) | 0 (—) | NO HEDGE IMPROVEMENT — fails H1, H2, H5 |
| Rates/target_vol | 1W | 2.0 | 1.00× | 1.00× | 1.06× | 0 (0) | -0 (-0) | 0 (0) | 143 (143) | 0 (—) | NO HEDGE IMPROVEMENT — fails H1, H2, H5 |
| Rates/target_vol | 1W | 5.0 | 1.00× | 1.00× | 1.05× | 0 (0) | -0 (-0) | 0 (0) | 143 (143) | 0 (—) | NO HEDGE IMPROVEMENT — fails H1, H2, H5 |
| Rates/target_vol | 1W | 10.0 | 1.00× | 1.00× | 1.05× | 0 (0) | -0 (-0) | 0 (0) | 143 (143) | 0 (—) | NO HEDGE IMPROVEMENT — fails H1, H2, H5 |
| Rates/var | 1W | 0.5 | 1.00× | 1.50× | 1.50× | 0 (0) | -2.54 (-2.48) | 0.0416 (0.0407) | 1,993 (1,993) | 0.0262 (+1.2) | NO HEDGE IMPROVEMENT — fails H1, H2 |
| Rates/var | 1W | 1.0 | 1.00× | 1.50× | 1.50× | 0 (0) | -2.54 (-2.48) | 0.0416 (0.0407) | 1,993 (1,993) | 0.0532 (+1.2) | NO HEDGE IMPROVEMENT — fails H1, H2 |
| Rates/var | 1W | 2.0 | 1.00× | 1.50× | 1.50× | 0 (0) | -2.51 (-2.48) | 0.0412 (0.0407) | 1,993 (1,993) | 0.0506 (+1.2) | NO HEDGE IMPROVEMENT — fails H1, H2 |
| Rates/var | 1W | 5.0 | 1.00× | 1.50× | 1.49× | 0 (0) | -2.43 (-2.48) | 0.0399 (0.0407) | 1,993 (1,993) | -0.254 (-1.2) | NO HEDGE IMPROVEMENT — fails H1, H2, H5 |
| Rates/var | 1W | 10.0 | 1.00× | 1.50× | 1.49× | 0 (0) | -2.43 (-2.48) | 0.0399 (0.0407) | 1,993 (1,993) | -0.54 (-1.2) | NO HEDGE IMPROVEMENT — fails H1, H2, H5 |
| Volatility/volatility | 1W | 0.5 | 1.00× | 1.50× | 1.49× | 3,045 (2,186) | 333 (231) | 265 (181) | 1,663 (1,166) | 724 (+3.1) | NO HEDGE IMPROVEMENT — fails H2, H4 |
| Volatility/volatility | 1W | 1.0 | 1.00× | 1.50× | 1.49× | 3,045 (2,186) | 333 (231) | 265 (181) | 1,663 (1,166) | 673 (+2.5) | NO HEDGE IMPROVEMENT — fails H2, H4 |
| Volatility/volatility | 1W | 2.0 | 1.00× | 1.50× | 1.49× | 2,995 (2,186) | 328 (231) | 263 (181) | 1,633 (1,166) | 533 (+1.7) | NO HEDGE IMPROVEMENT — fails H1, H2, H4 |
| Volatility/volatility | 1W | 5.0 | 1.00× | 1.50× | 1.47× | 2,320 (2,186) | 269 (231) | 231 (181) | 1,278 (1,166) | -105 (-0.3) | NO HEDGE IMPROVEMENT — fails H1, H2, H4, H5 |
| Volatility/volatility | 1W | 10.0 | 1.00× | 0.50× | 0.50× | 1,335 (2,186) | 136 (231) | 121 (181) | 811 (1,166) | 152 (+0.2) | NO HEDGE IMPROVEMENT — fails H1, H2, H3 |
| Commodity/commodity | 1M | 0.5 | 1.00× | 1.45× | 1.46× | 2,763 (2,649) | 1,018 (736) | 489 (420) | 1,318 (1,280) | -95.7 (-1.2) | NO HEDGE IMPROVEMENT — fails H1, H2, H4, H5 |
| Commodity/commodity | 1M | 1.0 | 1.00× | 1.05× | 1.10× | 2,603 (2,649) | 887 (736) | 442 (420) | 1,315 (1,280) | -219 (-2.5) | NO HEDGE IMPROVEMENT — fails H1, H2, H5 |
| Commodity/commodity | 1M | 2.0 | 1.00× | 0.50× | 0.54× | 2,268 (2,649) | 716 (736) | 365 (420) | 1,346 (1,280) | -286 (-1.3) | NO HEDGE IMPROVEMENT — fails H1, H2, H4, H5 |
| Commodity/commodity | 1M | 5.0 | 1.00× | 0.50× | 0.52× | 1,805 (2,649) | 555 (736) | 274 (420) | 1,426 (1,280) | 206 (+0.2) | NO HEDGE IMPROVEMENT — fails H1, H2, H3, H4 |
| Commodity/commodity | 1M | 10.0 | 1.00× | 0.50× | 0.51× | 1,652 (2,649) | 501 (736) | 245 (420) | 1,462 (1,280) | 1,526 (+0.9) | NO HEDGE IMPROVEMENT — fails H1, H2, H3, H4 |
| Commodity/drawdown | 1M | 0.5 | 1.00× | 1.50× | 1.49× | 2,326 (1,926) | 1,363 (968) | 15.6 (11.9) | 3,575 (3,564) | 198 (+0.1) | NO HEDGE IMPROVEMENT — fails H1, H2, H4, H5 |
| Commodity/drawdown | 1M | 1.0 | 1.00× | 1.50× | 1.43× | 2,326 (1,926) | 1,360 (968) | 15.6 (11.9) | 3,575 (3,564) | 3.52 (+0.0) | NO HEDGE IMPROVEMENT — fails H1, H2, H4, H5 |
| Commodity/drawdown | 1M | 2.0 | 1.00× | 0.50× | 0.55× | 2,302 (1,926) | 1,323 (968) | 15.2 (11.9) | 3,569 (3,564) | -338 (-0.2) | NO HEDGE IMPROVEMENT — fails H1, H2, H4, H5 |
| Commodity/drawdown | 1M | 5.0 | 1.00× | 0.50× | 0.53× | 1,764 (1,926) | 970 (968) | 10.1 (11.9) | 3,592 (3,564) | -173 (-0.3) | NO HEDGE IMPROVEMENT — fails H1, H2 |
| Commodity/drawdown | 1M | 10.0 | 1.00× | 0.50× | 0.51× | 1,579 (1,926) | 892 (968) | 9.11 (11.9) | 3,615 (3,564) | 414 (+0.2) | NO HEDGE IMPROVEMENT — fails H1, H2 |
| Commodity/es | 1M | 0.5 | 1.00× | 1.50× | 1.49× | 2,326 (1,926) | 1,363 (968) | 15.6 (11.9) | 3,575 (3,564) | 198 (+0.1) | NO HEDGE IMPROVEMENT — fails H1, H2, H4, H5 |
| Commodity/es | 1M | 1.0 | 1.00× | 1.50× | 1.43× | 2,326 (1,926) | 1,360 (968) | 15.6 (11.9) | 3,575 (3,564) | 3.52 (+0.0) | NO HEDGE IMPROVEMENT — fails H1, H2, H4, H5 |
| Commodity/es | 1M | 2.0 | 1.00× | 0.50× | 0.55× | 2,302 (1,926) | 1,323 (968) | 15.2 (11.9) | 3,569 (3,564) | -338 (-0.2) | NO HEDGE IMPROVEMENT — fails H1, H2, H4, H5 |
| Commodity/es | 1M | 5.0 | 1.00× | 0.50× | 0.53× | 1,764 (1,926) | 970 (968) | 10.1 (11.9) | 3,592 (3,564) | -173 (-0.3) | NO HEDGE IMPROVEMENT — fails H1, H2 |
| Commodity/es | 1M | 10.0 | 1.00× | 0.50× | 0.51× | 1,579 (1,926) | 892 (968) | 9.11 (11.9) | 3,615 (3,564) | 414 (+0.2) | NO HEDGE IMPROVEMENT — fails H1, H2 |
| Commodity/min_variance | 1M | 0.5 | 1.00× | 0.90× | 1.08× | 2,967 (3,208) | 2,946 (2,348) | -43.9 (-46) | 6,968 (6,910) | -542 (-1.4) | NO HEDGE IMPROVEMENT — fails H1, H2, H3, H5 |
| Commodity/min_variance | 1M | 1.0 | 1.00× | 0.70× | 0.88× | 3,137 (3,208) | 2,749 (2,348) | -40.8 (-46) | 6,946 (6,910) | -477 (-1.4) | NO HEDGE IMPROVEMENT — fails H1, H2, H5 |
| Commodity/min_variance | 1M | 2.0 | 1.00× | 0.50× | 0.55× | 3,444 (3,208) | 2,450 (2,348) | -38.5 (-46) | 6,954 (6,910) | 23.5 (+0.1) | NO HEDGE IMPROVEMENT — fails H1, H2 |
| Commodity/min_variance | 1M | 5.0 | 1.00× | 0.50× | 0.53× | 2,842 (3,208) | 1,610 (2,348) | -31.7 (-46) | 7,125 (6,910) | 3,308 (+1.4) | NO HEDGE IMPROVEMENT — fails H1 |
| Commodity/min_variance | 1M | 10.0 | 1.00× | 0.50× | 0.51× | 2,564 (3,208) | 1,451 (2,348) | -27.4 (-46) | 7,193 (6,910) | 8,308 (+1.6) | NO HEDGE IMPROVEMENT — fails H1, H3 |
| Commodity/systematic | 1M | 0.5 | 1.00× | 1.50× | 1.49× | 12,086 (9,998) | 4,857 (3,494) | 1,351 (972) | 2,976 (2,490) | 1,028 (+1.2) | NO HEDGE IMPROVEMENT — fails H1, H2, H4, H5 |
| Commodity/systematic | 1M | 1.0 | 1.00× | 1.45× | 1.39× | 12,026 (9,998) | 4,833 (3,494) | 1,339 (972) | 2,947 (2,490) | 322 (+0.3) | NO HEDGE IMPROVEMENT — fails H1, H2, H4, H5 |
| Commodity/systematic | 1M | 2.0 | 1.00× | 0.95× | 0.86× | 11,506 (9,998) | 4,441 (3,494) | 1,198 (972) | 2,725 (2,490) | -613 (-0.6) | NO HEDGE IMPROVEMENT — fails H1, H2, H4, H5 |
| Commodity/systematic | 1M | 5.0 | 1.00× | 0.50× | 0.53× | 7,051 (9,998) | 2,463 (3,494) | 633 (972) | 2,662 (2,490) | 2,544 (+0.9) | NO HEDGE IMPROVEMENT — fails H1, H2, H3, H4 |
| Commodity/systematic | 1M | 10.0 | 1.00× | 0.50× | 0.51× | 6,097 (9,998) | 1,988 (3,494) | 546 (972) | 2,791 (2,490) | 11,579 (+1.9) | NO HEDGE IMPROVEMENT — fails H1, H3, H4 |
| Commodity/target_vol | 1M | 0.5 | 1.00× | 1.15× | 1.28× | 1,530 (1,276) | 2,143 (1,508) | 7.39 (5.66) | 2,114 (2,046) | -64.7 (-0.4) | NO HEDGE IMPROVEMENT — fails H1, H2, H4, H5 |
| Commodity/target_vol | 1M | 1.0 | 1.00× | 0.50× | 0.81× | 1,532 (1,276) | 1,938 (1,508) | 6.67 (5.66) | 2,085 (2,046) | -175 (-1.1) | NO HEDGE IMPROVEMENT — fails H1, H2, H4, H5 |
| Commodity/target_vol | 1M | 2.0 | 1.00× | 0.50× | 0.57× | 1,475 (1,276) | 1,749 (1,508) | 6.09 (5.66) | 2,065 (2,046) | -282 (-1.1) | NO HEDGE IMPROVEMENT — fails H1, H2, H5 |
| Commodity/target_vol | 1M | 5.0 | 1.00× | 0.50× | 0.54× | 918 (1,276) | 930 (1,508) | 3.64 (5.66) | 2,086 (2,046) | 2,536 (+1.7) | **LIVE SHADOW ELIGIBLE** |
| Commodity/target_vol | 1M | 10.0 | 1.00× | 0.50× | 0.51× | 805 (1,276) | 821 (1,508) | 3.22 (5.66) | 2,107 (2,046) | 6,406 (+1.7) | **LIVE SHADOW ELIGIBLE** |
| Commodity/var | 1M | 0.5 | 1.00× | 1.50× | 1.49× | 2,326 (1,926) | 1,363 (968) | 15.6 (11.9) | 3,575 (3,564) | 198 (+0.1) | NO HEDGE IMPROVEMENT — fails H1, H2, H4, H5 |
| Commodity/var | 1M | 1.0 | 1.00× | 1.50× | 1.43× | 2,326 (1,926) | 1,360 (968) | 15.6 (11.9) | 3,575 (3,564) | 3.52 (+0.0) | NO HEDGE IMPROVEMENT — fails H1, H2, H4, H5 |
| Commodity/var | 1M | 2.0 | 1.00× | 0.50× | 0.55× | 2,302 (1,926) | 1,323 (968) | 15.2 (11.9) | 3,569 (3,564) | -338 (-0.2) | NO HEDGE IMPROVEMENT — fails H1, H2, H4, H5 |
| Commodity/var | 1M | 5.0 | 1.00× | 0.50× | 0.53× | 1,764 (1,926) | 970 (968) | 10.1 (11.9) | 3,592 (3,564) | -173 (-0.3) | NO HEDGE IMPROVEMENT — fails H1, H2 |
| Commodity/var | 1M | 10.0 | 1.00× | 0.50× | 0.51× | 1,579 (1,926) | 892 (968) | 9.11 (11.9) | 3,615 (3,564) | 414 (+0.2) | NO HEDGE IMPROVEMENT — fails H1, H2 |
| Credit/credit | 1M | 0.5 | 1.00× | 1.50× | 1.49× | 1,154 (1,130) | 53.7 (-45.5) | 601 (448) | 3,865 (2,657) | -178 (-0.7) | NO HEDGE IMPROVEMENT — fails H1, H2, H3, H4 |
| Credit/credit | 1M | 1.0 | 1.00× | 1.40× | 1.41× | 1,049 (1,130) | 103 (-45.5) | 583 (448) | 3,844 (2,657) | -364 (-1.2) | NO HEDGE IMPROVEMENT — fails H1, H2, H3, H4 |
| Credit/credit | 1M | 2.0 | 1.00× | 1.25× | 1.29× | 922 (1,130) | 142 (-45.5) | 542 (448) | 3,705 (2,657) | -678 (-1.5) | NO HEDGE IMPROVEMENT — fails H1, H2, H3, H4 |
| Credit/credit | 1M | 5.0 | 1.00× | 0.80× | 0.93× | 862 (1,130) | 37 (-45.5) | 411 (448) | 2,165 (2,657) | -644 (-0.8) | NO HEDGE IMPROVEMENT — fails H1, H2, H5 |
| Credit/credit | 1M | 10.0 | 1.00× | 0.50× | 0.68× | 847 (1,130) | -43.9 (-45.5) | 368 (448) | 1,486 (2,657) | -220 (-0.1) | NO HEDGE IMPROVEMENT — fails H1, H2, H5 |
| Credit/drawdown | 1M | 0.5 | 1.00× | 1.50× | 1.49× | 1,392 (1,122) | -59 (-62.7) | 6.51 (5.41) | 1,445 (1,404) | 268 (+1.2) | NO HEDGE IMPROVEMENT — fails H1, H2, H4 |
| Credit/drawdown | 1M | 1.0 | 1.00× | 1.50× | 1.49× | 1,392 (1,122) | -59 (-62.7) | 6.51 (5.41) | 1,445 (1,404) | 266 (+1.2) | NO HEDGE IMPROVEMENT — fails H1, H2, H4 |
| Credit/drawdown | 1M | 2.0 | 1.00× | 1.50× | 1.48× | 1,392 (1,122) | -59 (-62.7) | 6.51 (5.41) | 1,445 (1,404) | 262 (+1.0) | NO HEDGE IMPROVEMENT — fails H1, H2, H4 |
| Credit/drawdown | 1M | 5.0 | 1.00× | 1.50× | 1.46× | 1,392 (1,122) | -54 (-62.7) | 6.43 (5.41) | 1,443 (1,404) | 226 (+0.6) | NO HEDGE IMPROVEMENT — fails H1, H2, H4 |
| Credit/drawdown | 1M | 10.0 | 1.00× | 1.50× | 1.44× | 1,392 (1,122) | -51.8 (-62.7) | 6.4 (5.41) | 1,442 (1,404) | 161 (+0.3) | NO HEDGE IMPROVEMENT — fails H1, H2, H4, H5 |
| Credit/es | 1M | 0.5 | 1.00× | 1.50× | 1.49× | 1,392 (1,122) | -59 (-62.7) | 6.51 (5.41) | 1,445 (1,404) | 268 (+1.2) | NO HEDGE IMPROVEMENT — fails H1, H2, H4 |
| Credit/es | 1M | 1.0 | 1.00× | 1.50× | 1.49× | 1,392 (1,122) | -59 (-62.7) | 6.51 (5.41) | 1,445 (1,404) | 266 (+1.2) | NO HEDGE IMPROVEMENT — fails H1, H2, H4 |
| Credit/es | 1M | 2.0 | 1.00× | 1.50× | 1.48× | 1,392 (1,122) | -59 (-62.7) | 6.51 (5.41) | 1,445 (1,404) | 262 (+1.0) | NO HEDGE IMPROVEMENT — fails H1, H2, H4 |
| Credit/es | 1M | 5.0 | 1.00× | 1.50× | 1.46× | 1,392 (1,122) | -54 (-62.7) | 6.43 (5.41) | 1,443 (1,404) | 226 (+0.6) | NO HEDGE IMPROVEMENT — fails H1, H2, H4 |
| Credit/es | 1M | 10.0 | 1.00× | 1.50× | 1.44× | 1,392 (1,122) | -51.8 (-62.7) | 6.4 (5.41) | 1,442 (1,404) | 161 (+0.3) | NO HEDGE IMPROVEMENT — fails H1, H2, H4, H5 |
| Credit/min_variance | 1M | 0.5 | 1.00× | 1.50× | 1.49× | 2,430 (2,066) | -520 (-344) | 30.8 (20.7) | 3,248 (2,927) | 441 (+1.0) | NO HEDGE IMPROVEMENT — fails H1, H4, H5 |
| Credit/min_variance | 1M | 1.0 | 1.00× | 1.50× | 1.49× | 2,430 (2,066) | -520 (-344) | 30.8 (20.7) | 3,248 (2,927) | 529 (+1.1) | NO HEDGE IMPROVEMENT — fails H1, H4, H5 |
| Credit/min_variance | 1M | 2.0 | 1.00× | 1.50× | 1.48× | 2,449 (2,066) | -309 (-344) | 26.5 (20.7) | 3,150 (2,927) | 305 (+0.4) | NO HEDGE IMPROVEMENT — fails H1, H2, H4, H5 |
| Credit/min_variance | 1M | 5.0 | 1.00× | 1.50× | 1.46× | 2,061 (2,066) | -137 (-344) | 17.7 (20.7) | 2,870 (2,927) | -1,038 (-1.5) | NO HEDGE IMPROVEMENT — fails H1, H2, H5 |
| Credit/min_variance | 1M | 10.0 | 1.00× | 1.50× | 1.44× | 2,038 (2,066) | -132 (-344) | 17.1 (20.7) | 2,863 (2,927) | -2,149 (-1.7) | NO HEDGE IMPROVEMENT — fails H1, H2, H5 |
| Credit/name | 1M | 0.5 | 1.00× | 1.50× | 1.49× | 4,976 (3,543) | 46.3 (26.6) | 473 (324) | 1,615 (1,127) | 1,274 (+4.2) | NO HEDGE IMPROVEMENT — fails H4 |
| Credit/name | 1M | 1.0 | 1.00× | 1.50× | 1.49× | 4,971 (3,543) | 46.8 (26.6) | 472 (324) | 1,613 (1,127) | 1,259 (+3.9) | NO HEDGE IMPROVEMENT — fails H4 |
| Credit/name | 1M | 2.0 | 1.00× | 1.50× | 1.49× | 4,939 (3,543) | 48.3 (26.6) | 469 (324) | 1,601 (1,127) | 1,207 (+2.8) | NO HEDGE IMPROVEMENT — fails H4 |
| Credit/name | 1M | 5.0 | 1.00× | 1.50× | 1.47× | 3,295 (3,543) | 15.7 (26.6) | 360 (324) | 1,070 (1,127) | -229 (-0.5) | NO HEDGE IMPROVEMENT — fails H1, H2, H4, H5 |
| Credit/name | 1M | 10.0 | 1.00× | 0.50× | 0.71× | 2,180 (3,543) | 17.7 (26.6) | 242 (324) | 713 (1,127) | -1,194 (-0.8) | NO HEDGE IMPROVEMENT — fails H1, H2, H3 |
| Credit/var | 1M | 0.5 | 1.00× | 1.50× | 1.49× | 1,392 (1,122) | -59 (-62.7) | 6.51 (5.41) | 1,445 (1,404) | 268 (+1.2) | NO HEDGE IMPROVEMENT — fails H1, H2, H4 |
| Credit/var | 1M | 1.0 | 1.00× | 1.50× | 1.49× | 1,392 (1,122) | -59 (-62.7) | 6.51 (5.41) | 1,445 (1,404) | 266 (+1.2) | NO HEDGE IMPROVEMENT — fails H1, H2, H4 |
| Credit/var | 1M | 2.0 | 1.00× | 1.50× | 1.48× | 1,392 (1,122) | -59 (-62.7) | 6.51 (5.41) | 1,445 (1,404) | 262 (+1.0) | NO HEDGE IMPROVEMENT — fails H1, H2, H4 |
| Credit/var | 1M | 5.0 | 1.00× | 1.50× | 1.46× | 1,392 (1,122) | -54 (-62.7) | 6.43 (5.41) | 1,443 (1,404) | 226 (+0.6) | NO HEDGE IMPROVEMENT — fails H1, H2, H4 |
| Credit/var | 1M | 10.0 | 1.00× | 1.50× | 1.44× | 1,392 (1,122) | -51.8 (-62.7) | 6.4 (5.41) | 1,442 (1,404) | 161 (+0.3) | NO HEDGE IMPROVEMENT — fails H1, H2, H4, H5 |
| Crypto/crypto | 1M | 0.5 | 1.00× | 1.35× | 1.40× | 124,911 (106,126) | 7,296 (5,647) | 4,293 (3,141) | 20,389 (12,884) | 16,809 (+2.9) | INSUFFICIENT DATA |
| Crypto/crypto | 1M | 1.0 | 1.00× | 1.35× | 1.39× | 125,004 (106,126) | 7,345 (5,647) | 4,262 (3,141) | 20,259 (12,884) | 16,059 (+2.2) | INSUFFICIENT DATA |
| Crypto/crypto | 1M | 2.0 | 1.00× | 1.35× | 1.37× | 122,790 (106,126) | 6,544 (5,647) | 4,010 (3,141) | 18,296 (12,884) | 14,000 (+1.9) | INSUFFICIENT DATA |
| Crypto/crypto | 1M | 5.0 | 1.00× | 1.30× | 1.23× | 81,592 (106,126) | 2,244 (5,647) | 2,650 (3,141) | 9,916 (12,884) | -7,032 (-0.4) | INSUFFICIENT DATA |
| Crypto/crypto | 1M | 10.0 | 1.00× | 1.15× | 1.02× | 73,170 (106,126) | 2,512 (5,647) | 2,102 (3,141) | 8,007 (12,884) | -569 (-0.0) | INSUFFICIENT DATA |
| Crypto/drawdown | 1M | 0.5 | 1.00× | 1.50× | 1.47× | 112,733 (83,684) | 5,496 (4,026) | 2,155 (1,600) | 8,532 (8,181) | 27,759 (+5.4) | INSUFFICIENT DATA |
| Crypto/drawdown | 1M | 1.0 | 1.00× | 1.50× | 1.46× | 112,515 (83,684) | 5,488 (4,026) | 2,148 (1,600) | 8,526 (8,181) | 26,822 (+4.3) | INSUFFICIENT DATA |
| Crypto/drawdown | 1M | 2.0 | 1.00× | 1.50× | 1.43× | 104,923 (83,684) | 5,091 (4,026) | 2,020 (1,600) | 8,203 (8,181) | 18,689 (+2.8) | INSUFFICIENT DATA |
| Crypto/drawdown | 1M | 5.0 | 1.00× | 1.50× | 1.32× | 65,140 (83,684) | 3,001 (4,026) | 1,363 (1,600) | 9,176 (8,181) | -13,183 (-0.8) | INSUFFICIENT DATA |
| Crypto/drawdown | 1M | 10.0 | 1.00× | 1.50× | 1.18× | 58,908 (83,684) | 2,723 (4,026) | 1,193 (1,600) | 9,522 (8,181) | -11,342 (-0.4) | INSUFFICIENT DATA |
| Crypto/es | 1M | 0.5 | 1.00× | 1.50× | 1.47× | 112,733 (83,684) | 5,496 (4,026) | 2,155 (1,600) | 8,532 (8,181) | 27,759 (+5.4) | INSUFFICIENT DATA |
| Crypto/es | 1M | 1.0 | 1.00× | 1.50× | 1.46× | 112,515 (83,684) | 5,488 (4,026) | 2,148 (1,600) | 8,526 (8,181) | 26,822 (+4.3) | INSUFFICIENT DATA |
| Crypto/es | 1M | 2.0 | 1.00× | 1.50× | 1.43× | 104,923 (83,684) | 5,091 (4,026) | 2,020 (1,600) | 8,203 (8,181) | 18,689 (+2.8) | INSUFFICIENT DATA |
| Crypto/es | 1M | 5.0 | 1.00× | 1.50× | 1.32× | 65,140 (83,684) | 3,001 (4,026) | 1,363 (1,600) | 9,176 (8,181) | -13,183 (-0.8) | INSUFFICIENT DATA |
| Crypto/es | 1M | 10.0 | 1.00× | 1.50× | 1.18× | 58,908 (83,684) | 2,723 (4,026) | 1,193 (1,600) | 9,522 (8,181) | -11,342 (-0.4) | INSUFFICIENT DATA |
| Crypto/min_variance | 1M | 0.5 | 1.00× | 1.25× | 1.35× | 136,331 (121,311) | 10,143 (7,508) | 5,012 (3,725) | 17,951 (15,521) | 12,416 (+2.2) | INSUFFICIENT DATA |
| Crypto/min_variance | 1M | 1.0 | 1.00× | 1.25× | 1.34× | 136,504 (121,311) | 10,208 (7,508) | 4,983 (3,725) | 17,910 (15,521) | 11,235 (+1.9) | INSUFFICIENT DATA |
| Crypto/min_variance | 1M | 2.0 | 1.00× | 1.25× | 1.32× | 137,129 (121,311) | 9,349 (7,508) | 4,686 (3,725) | 16,775 (15,521) | 11,174 (+1.6) | INSUFFICIENT DATA |
| Crypto/min_variance | 1M | 5.0 | 1.00× | 1.20× | 1.18× | 91,637 (121,311) | 4,715 (7,508) | 3,104 (3,725) | 17,184 (15,521) | -15,088 (-0.8) | INSUFFICIENT DATA |
| Crypto/min_variance | 1M | 10.0 | 1.00× | 1.10× | 1.00× | 80,364 (121,311) | 4,587 (7,508) | 2,334 (3,725) | 18,768 (15,521) | -10,346 (-0.2) | INSUFFICIENT DATA |
| Crypto/systematic | 1M | 0.5 | 1.00× | 1.35× | 1.40× | 128,814 (109,398) | 7,826 (6,050) | 4,536 (3,319) | 20,969 (13,250) | 17,312 (+2.8) | INSUFFICIENT DATA |
| Crypto/systematic | 1M | 1.0 | 1.00× | 1.35× | 1.39× | 128,913 (109,398) | 7,875 (6,050) | 4,503 (3,319) | 20,835 (13,250) | 16,505 (+2.2) | INSUFFICIENT DATA |
| Crypto/systematic | 1M | 2.0 | 1.00× | 1.35× | 1.37× | 126,646 (109,398) | 7,026 (6,050) | 4,237 (3,319) | 18,816 (13,250) | 14,378 (+1.8) | INSUFFICIENT DATA |
| Crypto/systematic | 1M | 5.0 | 1.00× | 1.25× | 1.20× | 84,079 (109,398) | 2,463 (6,050) | 2,799 (3,319) | 10,198 (13,250) | -6,867 (-0.4) | INSUFFICIENT DATA |
| Crypto/systematic | 1M | 10.0 | 1.00× | 1.15× | 1.02× | 75,396 (109,398) | 2,717 (6,050) | 2,221 (3,319) | 8,234 (13,250) | 423 (+0.0) | INSUFFICIENT DATA |
| Crypto/target_vol | 1M | 0.5 | 1.00× | 1.50× | 1.47× | 126,205 (97,572) | 8,517 (6,306) | 4,014 (2,896) | 14,577 (12,708) | 26,411 (+6.9) | INSUFFICIENT DATA |
| Crypto/target_vol | 1M | 1.0 | 1.00× | 1.50× | 1.46× | 126,109 (97,572) | 8,512 (6,306) | 4,000 (2,896) | 14,549 (12,708) | 25,227 (+5.7) | INSUFFICIENT DATA |
| Crypto/target_vol | 1M | 2.0 | 1.00× | 1.50× | 1.43× | 119,794 (97,572) | 7,840 (6,306) | 3,768 (2,896) | 13,682 (12,708) | 18,284 (+2.9) | INSUFFICIENT DATA |
| Crypto/target_vol | 1M | 5.0 | 1.00× | 1.50× | 1.32× | 74,889 (97,572) | 4,181 (6,306) | 2,547 (2,896) | 14,219 (12,708) | -11,710 (-0.7) | INSUFFICIENT DATA |
| Crypto/target_vol | 1M | 10.0 | 1.00× | 1.30× | 1.09× | 64,787 (97,572) | 3,517 (6,306) | 1,884 (2,896) | 15,223 (12,708) | -3,886 (-0.1) | INSUFFICIENT DATA |
| Crypto/var | 1M | 0.5 | 1.00× | 1.50× | 1.47× | 112,733 (83,684) | 5,496 (4,026) | 2,155 (1,600) | 8,532 (8,181) | 27,759 (+5.4) | INSUFFICIENT DATA |
| Crypto/var | 1M | 1.0 | 1.00× | 1.50× | 1.46× | 112,515 (83,684) | 5,488 (4,026) | 2,148 (1,600) | 8,526 (8,181) | 26,822 (+4.3) | INSUFFICIENT DATA |
| Crypto/var | 1M | 2.0 | 1.00× | 1.50× | 1.43× | 104,923 (83,684) | 5,091 (4,026) | 2,020 (1,600) | 8,203 (8,181) | 18,689 (+2.8) | INSUFFICIENT DATA |
| Crypto/var | 1M | 5.0 | 1.00× | 1.50× | 1.32× | 65,140 (83,684) | 3,001 (4,026) | 1,363 (1,600) | 9,176 (8,181) | -13,183 (-0.8) | INSUFFICIENT DATA |
| Crypto/var | 1M | 10.0 | 1.00× | 1.50× | 1.18× | 58,908 (83,684) | 2,723 (4,026) | 1,193 (1,600) | 9,522 (8,181) | -11,342 (-0.4) | INSUFFICIENT DATA |
| Equity/beta | 1M | 0.5 | 1.00× | 1.50× | 1.49× | 17,036 (13,156) | 7,657 (5,192) | -143 (-94.6) | 3,709 (1,941) | 2,696 (+3.4) | NO HEDGE IMPROVEMENT — fails H4 |
| Equity/beta | 1M | 1.0 | 1.00× | 1.50× | 1.49× | 17,033 (13,156) | 7,647 (5,192) | -142 (-94.6) | 3,698 (1,941) | 1,470 (+1.6) | NO HEDGE IMPROVEMENT — fails H1, H4, H5 |
| Equity/beta | 1M | 2.0 | 1.00× | 1.25× | 1.25× | 15,740 (13,156) | 6,833 (5,192) | -110 (-94.6) | 2,918 (1,941) | -683 (-0.7) | NO HEDGE IMPROVEMENT — fails H1, H2, H4, H5 |
| Equity/beta | 1M | 5.0 | 1.00× | 0.50× | 0.52× | 8,161 (13,156) | 3,128 (5,192) | -38.3 (-94.6) | 2,505 (1,941) | 5,268 (+1.9) | NO HEDGE IMPROVEMENT — fails H1, H3, H4 |
| Equity/beta | 1M | 10.0 | 1.00× | 0.50× | 0.51× | 7,748 (13,156) | 2,890 (5,192) | -41.5 (-94.6) | 2,565 (1,941) | 17,563 (+3.3) | NO HEDGE IMPROVEMENT — fails H3, H4 |
| Equity/crash | 1M | 0.5 | 1.00× | 1.50× | 1.49× | 17,453 (15,521) | 7,771 (5,445) | 2,306 (1,650) | 2,561 (1,475) | 114 (+0.1) | NO HEDGE IMPROVEMENT — fails H1, H2, H4, H5 |
| Equity/crash | 1M | 1.0 | 1.00× | 1.25× | 1.30× | 17,520 (15,521) | 7,546 (5,445) | 2,223 (1,650) | 2,504 (1,475) | -674 (-0.3) | NO HEDGE IMPROVEMENT — fails H1, H2, H4, H5 |
| Equity/crash | 1M | 2.0 | 1.00× | 0.75× | 0.86× | 16,496 (15,521) | 6,400 (5,445) | 1,823 (1,650) | 1,909 (1,475) | -1,107 (-0.7) | NO HEDGE IMPROVEMENT — fails H1, H2, H4, H5 |
| Equity/crash | 1M | 5.0 | 1.00× | 0.50× | 0.52× | 10,210 (15,521) | 3,701 (5,445) | 1,038 (1,650) | 2,580 (1,475) | 4,024 (+1.0) | NO HEDGE IMPROVEMENT — fails H1, H2, H3, H4 |
| Equity/crash | 1M | 10.0 | 1.00× | 0.50× | 0.51× | 9,950 (15,521) | 3,061 (5,445) | 906 (1,650) | 2,674 (1,475) | 19,018 (+2.7) | NO HEDGE IMPROVEMENT — fails H3, H4 |
| Equity/drawdown | 1M | 0.5 | 1.00× | 1.50× | 1.49× | 38,012 (30,247) | 6,816 (4,620) | -139 (-92.1) | 3,477 (2,979) | 6,713 (+2.5) | NO HEDGE IMPROVEMENT — fails H4 |
| Equity/drawdown | 1M | 1.0 | 1.00× | 1.50× | 1.49× | 38,011 (30,247) | 6,815 (4,620) | -139 (-92.1) | 3,477 (2,979) | 5,615 (+2.0) | NO HEDGE IMPROVEMENT — fails H4 |
| Equity/drawdown | 1M | 2.0 | 1.00× | 1.50× | 1.45× | 37,742 (30,247) | 6,742 (4,620) | -137 (-92.1) | 3,424 (2,979) | 3,295 (+1.1) | NO HEDGE IMPROVEMENT — fails H1, H2, H4, H5 |
| Equity/drawdown | 1M | 5.0 | 1.00× | 1.15× | 1.03× | 32,449 (30,247) | 5,426 (4,620) | -85.7 (-92.1) | 2,995 (2,979) | -1,833 (-1.0) | NO HEDGE IMPROVEMENT — fails H1, H2, H5 |
| Equity/drawdown | 1M | 10.0 | 1.00× | 0.50× | 0.51× | 17,650 (30,247) | 2,636 (4,620) | -41.3 (-92.1) | 4,149 (2,979) | 7,190 (+1.0) | NO HEDGE IMPROVEMENT — fails H1, H3, H4 |
| Equity/es | 1M | 0.5 | 1.00× | 1.50× | 1.49× | 38,012 (30,247) | 6,816 (4,620) | -139 (-92.1) | 3,477 (2,979) | 6,713 (+2.5) | NO HEDGE IMPROVEMENT — fails H4 |
| Equity/es | 1M | 1.0 | 1.00× | 1.50× | 1.49× | 38,011 (30,247) | 6,815 (4,620) | -139 (-92.1) | 3,477 (2,979) | 5,615 (+2.0) | NO HEDGE IMPROVEMENT — fails H4 |
| Equity/es | 1M | 2.0 | 1.00× | 1.50× | 1.45× | 37,742 (30,247) | 6,742 (4,620) | -137 (-92.1) | 3,424 (2,979) | 3,295 (+1.1) | NO HEDGE IMPROVEMENT — fails H1, H2, H4, H5 |
| Equity/es | 1M | 5.0 | 1.00× | 1.15× | 1.03× | 32,449 (30,247) | 5,426 (4,620) | -85.7 (-92.1) | 2,995 (2,979) | -1,833 (-1.0) | NO HEDGE IMPROVEMENT — fails H1, H2, H5 |
| Equity/es | 1M | 10.0 | 1.00× | 0.50× | 0.51× | 17,650 (30,247) | 2,636 (4,620) | -41.3 (-92.1) | 4,149 (2,979) | 7,190 (+1.0) | NO HEDGE IMPROVEMENT — fails H1, H3, H4 |
| Equity/min_variance | 1M | 0.5 | 1.00× | 1.00× | 1.10× | 23,708 (23,561) | 11,573 (10,205) | -360 (-328) | 5,651 (5,736) | -505 (-2.2) | NO HEDGE IMPROVEMENT — fails H1, H2, H5 |
| Equity/min_variance | 1M | 1.0 | 1.00× | 0.95× | 1.06× | 23,724 (23,561) | 11,048 (10,205) | -333 (-328) | 5,651 (5,736) | -675 (-2.6) | NO HEDGE IMPROVEMENT — fails H1, H2, H5 |
| Equity/min_variance | 1M | 2.0 | 1.00× | 0.80× | 0.90× | 22,902 (23,561) | 9,742 (10,205) | -264 (-328) | 5,960 (5,736) | 201 (+0.3) | NO HEDGE IMPROVEMENT — fails H1, H2 |
| Equity/min_variance | 1M | 5.0 | 1.00× | 0.50× | 0.52× | 16,770 (23,561) | 5,980 (10,205) | -160 (-328) | 8,033 (5,736) | 14,169 (+2.6) | NO HEDGE IMPROVEMENT — fails H3, H4 |
| Equity/min_variance | 1M | 10.0 | 1.00× | 0.50× | 0.51× | 16,022 (23,561) | 5,463 (10,205) | -163 (-328) | 8,260 (5,736) | 39,712 (+3.7) | NO HEDGE IMPROVEMENT — fails H3, H4 |
| Equity/name | 1M | 0.5 | 1.00× | 1.50× | 1.49× | 30,414 (23,200) | 7,513 (5,085) | 930 (628) | 5,388 (3,076) | 5,698 (+6.2) | NO HEDGE IMPROVEMENT — fails H4 |
| Equity/name | 1M | 1.0 | 1.00× | 1.50× | 1.49× | 30,413 (23,200) | 7,513 (5,085) | 930 (628) | 5,387 (3,076) | 4,484 (+4.7) | NO HEDGE IMPROVEMENT — fails H4 |
| Equity/name | 1M | 2.0 | 1.00× | 1.50× | 1.45× | 30,290 (23,200) | 7,438 (5,085) | 920 (628) | 5,278 (3,076) | 2,092 (+1.8) | NO HEDGE IMPROVEMENT — fails H1, H4 |
| Equity/name | 1M | 5.0 | 1.00× | 0.50× | 0.52× | 18,034 (23,200) | 4,096 (5,085) | 506 (628) | 3,086 (3,076) | -94.9 (-0.0) | NO HEDGE IMPROVEMENT — fails H1, H2, H3 |
| Equity/name | 1M | 10.0 | 1.00× | 0.50× | 0.51× | 13,366 (23,200) | 2,779 (5,085) | 349 (628) | 3,286 (3,076) | 13,509 (+2.3) | NO HEDGE IMPROVEMENT — fails H3, H4 |
| Equity/sector | 1M | 0.5 | 1.00× | 1.50× | 1.49× | 21,826 (17,796) | 6,169 (4,215) | 1,769 (1,199) | 7,727 (5,072) | 2,484 (+2.4) | NO HEDGE IMPROVEMENT — fails H4 |
| Equity/sector | 1M | 1.0 | 1.00× | 1.45× | 1.45× | 21,623 (17,796) | 6,042 (4,215) | 1,728 (1,199) | 7,545 (5,072) | 1,470 (+1.4) | NO HEDGE IMPROVEMENT — fails H1, H2, H4, H5 |
| Equity/sector | 1M | 2.0 | 1.00× | 1.30× | 1.29× | 20,857 (17,796) | 5,586 (4,215) | 1,558 (1,199) | 6,864 (5,072) | -40.3 (-0.0) | NO HEDGE IMPROVEMENT — fails H1, H2, H4, H5 |
| Equity/sector | 1M | 5.0 | 1.00× | 0.70× | 0.68× | 14,788 (17,796) | 3,671 (4,215) | 919 (1,199) | 4,420 (5,072) | -4.99 (-0.0) | NO HEDGE IMPROVEMENT — fails H1, H2, H3 |
| Equity/sector | 1M | 10.0 | 1.00× | 0.50× | 0.51× | 10,848 (17,796) | 2,507 (4,215) | 669 (1,199) | 3,397 (5,072) | 10,659 (+2.0) | NO HEDGE IMPROVEMENT — fails H3 |
| Equity/systematic | 1M | 0.5 | 1.00× | 1.50× | 1.49× | 24,919 (19,523) | 8,124 (5,507) | 874 (591) | 4,632 (2,567) | 3,804 (+3.6) | NO HEDGE IMPROVEMENT — fails H4 |
| Equity/systematic | 1M | 1.0 | 1.00× | 1.50× | 1.49× | 24,918 (19,523) | 8,123 (5,507) | 874 (591) | 4,632 (2,567) | 2,496 (+2.0) | NO HEDGE IMPROVEMENT — fails H4 |
| Equity/systematic | 1M | 2.0 | 1.00× | 1.40× | 1.37× | 24,458 (19,523) | 7,791 (5,507) | 827 (591) | 4,260 (2,567) | 132 (+0.1) | NO HEDGE IMPROVEMENT — fails H1, H2, H4, H5 |
| Equity/systematic | 1M | 5.0 | 1.00× | 0.50× | 0.52× | 12,880 (19,523) | 3,768 (5,507) | 392 (591) | 3,264 (2,567) | 2,248 (+0.7) | NO HEDGE IMPROVEMENT — fails H1, H2, H3, H4 |
| Equity/systematic | 1M | 10.0 | 1.00× | 0.50× | 0.51× | 11,421 (19,523) | 3,041 (5,507) | 331 (591) | 3,343 (2,567) | 16,813 (+2.5) | NO HEDGE IMPROVEMENT — fails H3, H4 |
| Equity/target_vol | 1M | 0.5 | 1.00× | 1.45× | 1.46× | 17,648 (14,605) | 7,161 (5,203) | -176 (-124) | 3,526 (3,156) | 2,116 (+4.0) | NO HEDGE IMPROVEMENT — fails H4 |
| Equity/target_vol | 1M | 1.0 | 1.00× | 1.35× | 1.38× | 17,369 (14,605) | 6,912 (5,203) | -171 (-124) | 3,395 (3,156) | 1,102 (+1.9) | NO HEDGE IMPROVEMENT — fails H1, H4 |
| Equity/target_vol | 1M | 2.0 | 1.00× | 1.10× | 1.13× | 16,374 (14,605) | 6,215 (5,203) | -154 (-124) | 3,169 (3,156) | -225 (-0.5) | NO HEDGE IMPROVEMENT — fails H1, H2, H4, H5 |
| Equity/target_vol | 1M | 5.0 | 1.00× | 0.50× | 0.52× | 9,289 (14,605) | 3,143 (5,203) | -60.3 (-124) | 4,385 (3,156) | 4,923 (+1.6) | NO HEDGE IMPROVEMENT — fails H1, H3, H4 |
| Equity/target_vol | 1M | 10.0 | 1.00× | 0.50× | 0.51× | 8,667 (14,605) | 2,804 (5,203) | -60.8 (-124) | 4,526 (3,156) | 17,988 (+2.8) | NO HEDGE IMPROVEMENT — fails H3, H4 |
| Equity/var | 1M | 0.5 | 1.00× | 1.50× | 1.49× | 38,012 (30,247) | 6,816 (4,620) | -139 (-92.1) | 3,477 (2,979) | 6,713 (+2.5) | NO HEDGE IMPROVEMENT — fails H4 |
| Equity/var | 1M | 1.0 | 1.00× | 1.50× | 1.49× | 38,011 (30,247) | 6,815 (4,620) | -139 (-92.1) | 3,477 (2,979) | 5,615 (+2.0) | NO HEDGE IMPROVEMENT — fails H4 |
| Equity/var | 1M | 2.0 | 1.00× | 1.50× | 1.45× | 37,742 (30,247) | 6,742 (4,620) | -137 (-92.1) | 3,424 (2,979) | 3,295 (+1.1) | NO HEDGE IMPROVEMENT — fails H1, H2, H4, H5 |
| Equity/var | 1M | 5.0 | 1.00× | 1.15× | 1.03× | 32,449 (30,247) | 5,426 (4,620) | -85.7 (-92.1) | 2,995 (2,979) | -1,833 (-1.0) | NO HEDGE IMPROVEMENT — fails H1, H2, H5 |
| Equity/var | 1M | 10.0 | 1.00× | 0.50× | 0.51× | 17,650 (30,247) | 2,636 (4,620) | -41.3 (-92.1) | 4,149 (2,979) | 7,190 (+1.0) | NO HEDGE IMPROVEMENT — fails H1, H3, H4 |
| FX/fx | 1M | 0.5 | 1.00× | 1.50× | 1.49× | 502 (365) | -54.4 (-38.7) | 12.4 (8.43) | 291 (149) | 141 (+2.0) | NO HEDGE IMPROVEMENT — fails H1, H4 |
| FX/fx | 1M | 1.0 | 1.00× | 1.50× | 1.49× | 502 (365) | -54.4 (-38.7) | 12.4 (8.43) | 291 (149) | 149 (+1.7) | NO HEDGE IMPROVEMENT — fails H1, H4 |
| FX/fx | 1M | 2.0 | 1.00× | 1.50× | 1.49× | 500 (365) | -55 (-38.7) | 12.4 (8.43) | 289 (149) | 164 (+1.1) | NO HEDGE IMPROVEMENT — fails H1, H2, H4 |
| FX/fx | 1M | 5.0 | 1.00× | 1.50× | 1.47× | 487 (365) | -52.7 (-38.7) | 12.1 (8.43) | 275 (149) | 189 (+0.6) | NO HEDGE IMPROVEMENT — fails H1, H2, H4 |
| FX/fx | 1M | 10.0 | 1.00× | 1.50× | 1.46× | 480 (365) | -50.2 (-38.7) | 12 (8.43) | 269 (149) | 227 (+0.4) | NO HEDGE IMPROVEMENT — fails H1, H2, H4 |
| Rates/curve | 1M | 0.5 | 1.00× | 1.50× | 1.49× | 1,588 (1,423) | -78.9 (-53.5) | 69.9 (55.2) | 1,916 (1,693) | 163 (+2.1) | NO HEDGE IMPROVEMENT — fails H2, H4 |
| Rates/curve | 1M | 1.0 | 1.00× | 1.50× | 1.49× | 1,379 (1,423) | -56.1 (-53.5) | 58.8 (55.2) | 1,746 (1,693) | -45 (-0.6) | NO HEDGE IMPROVEMENT — fails H1, H2 |
| Rates/curve | 1M | 2.0 | 1.00× | 1.50× | 1.49× | 1,121 (1,423) | -17.7 (-53.5) | 45.3 (55.2) | 1,565 (1,693) | -364 (-1.5) | NO HEDGE IMPROVEMENT — fails H1, H2, H5 |
| Rates/curve | 1M | 5.0 | 1.00× | 1.10× | 1.16× | 940 (1,423) | -20 (-53.5) | 33.6 (55.2) | 1,424 (1,693) | -628 (-1.0) | NO HEDGE IMPROVEMENT — fails H1, H2, H5 |
| Rates/curve | 1M | 10.0 | 1.00× | 0.50× | 0.68× | 853 (1,423) | -21.9 (-53.5) | 29.4 (55.2) | 1,379 (1,693) | -860 (-0.6) | NO HEDGE IMPROVEMENT — fails H1, H2, H5 |
| Rates/drawdown | 1M | 0.5 | 1.00× | 1.50× | 1.49× | 8,841 (6,660) | -778 (-515) | 34.7 (23.4) | 2,454 (2,192) | 2,301 (+3.3) | NO HEDGE IMPROVEMENT — fails H4 |
| Rates/drawdown | 1M | 1.0 | 1.00× | 1.50× | 1.49× | 8,841 (6,660) | -778 (-515) | 34.7 (23.4) | 2,454 (2,192) | 2,433 (+3.3) | NO HEDGE IMPROVEMENT — fails H4 |
| Rates/drawdown | 1M | 2.0 | 1.00× | 1.50× | 1.49× | 8,778 (6,660) | -773 (-515) | 34.4 (23.4) | 2,445 (2,192) | 2,625 (+3.5) | NO HEDGE IMPROVEMENT — fails H4 |
| Rates/drawdown | 1M | 5.0 | 1.00× | 1.50× | 1.47× | 8,162 (6,660) | -699 (-515) | 29.6 (23.4) | 2,340 (2,192) | 2,418 (+3.0) | NO HEDGE IMPROVEMENT — fails H2, H4 |
| Rates/drawdown | 1M | 10.0 | 1.00× | 1.50× | 1.46× | 7,470 (6,660) | -575 (-515) | 24.7 (23.4) | 2,234 (2,192) | 1,409 (+1.2) | NO HEDGE IMPROVEMENT — fails H1, H2, H5 |
| Rates/duration | 1M | 0.5 | 1.00× | 1.50× | 1.49× | 1,502 (1,441) | -2.33 (1.74) | 71.4 (61.3) | 1,877 (1,739) | 53.3 (+0.9) | NO HEDGE IMPROVEMENT — fails H1, H2, H4 |
| Rates/duration | 1M | 1.0 | 1.00× | 1.50× | 1.49× | 1,317 (1,441) | 22.1 (1.74) | 61.2 (61.3) | 1,731 (1,739) | -145 (-1.3) | NO HEDGE IMPROVEMENT — fails H1, H2 |
| Rates/duration | 1M | 2.0 | 1.00× | 1.35× | 1.37× | 1,137 (1,441) | 42.9 (1.74) | 49.2 (61.3) | 1,580 (1,739) | -375 (-1.5) | NO HEDGE IMPROVEMENT — fails H1, H2, H5 |
| Rates/duration | 1M | 5.0 | 1.00× | 0.95× | 1.05× | 959 (1,441) | 21.8 (1.74) | 36.9 (61.3) | 1,437 (1,739) | -558 (-0.8) | NO HEDGE IMPROVEMENT — fails H1, H2, H5 |
| Rates/duration | 1M | 10.0 | 1.00× | 0.50× | 0.68× | 878 (1,441) | 8.48 (1.74) | 32.6 (61.3) | 1,393 (1,739) | -602 (-0.4) | NO HEDGE IMPROVEMENT — fails H1, H2, H5 |
| Rates/es | 1M | 0.5 | 1.00× | 1.50× | 1.49× | 8,841 (6,660) | -778 (-515) | 34.7 (23.4) | 2,454 (2,192) | 2,301 (+3.3) | NO HEDGE IMPROVEMENT — fails H4 |
| Rates/es | 1M | 1.0 | 1.00× | 1.50× | 1.49× | 8,841 (6,660) | -778 (-515) | 34.7 (23.4) | 2,454 (2,192) | 2,433 (+3.3) | NO HEDGE IMPROVEMENT — fails H4 |
| Rates/es | 1M | 2.0 | 1.00× | 1.50× | 1.49× | 8,778 (6,660) | -773 (-515) | 34.4 (23.4) | 2,445 (2,192) | 2,625 (+3.5) | NO HEDGE IMPROVEMENT — fails H4 |
| Rates/es | 1M | 5.0 | 1.00× | 1.50× | 1.47× | 8,162 (6,660) | -699 (-515) | 29.6 (23.4) | 2,340 (2,192) | 2,418 (+3.0) | NO HEDGE IMPROVEMENT — fails H2, H4 |
| Rates/es | 1M | 10.0 | 1.00× | 1.50× | 1.46× | 7,470 (6,660) | -575 (-515) | 24.7 (23.4) | 2,234 (2,192) | 1,409 (+1.2) | NO HEDGE IMPROVEMENT — fails H1, H2, H5 |
| Rates/min_variance | 1M | 0.5 | 1.00× | 1.25× | 1.34× | 8,306 (8,207) | -760 (-588) | 126 (91.9) | 5,392 (4,753) | 150 (+0.3) | NO HEDGE IMPROVEMENT — fails H1, H2, H4 |
| Rates/min_variance | 1M | 1.0 | 1.00× | 1.30× | 1.34× | 8,304 (8,207) | -740 (-588) | 125 (91.9) | 5,352 (4,753) | 216 (+0.3) | NO HEDGE IMPROVEMENT — fails H1, H2, H4 |
| Rates/min_variance | 1M | 2.0 | 1.00× | 1.30× | 1.34× | 8,419 (8,207) | -687 (-588) | 118 (91.9) | 5,211 (4,753) | 383 (+0.5) | NO HEDGE IMPROVEMENT — fails H1, H2, H4 |
| Rates/min_variance | 1M | 5.0 | 1.00× | 1.35× | 1.34× | 7,474 (8,207) | -351 (-588) | 81.1 (91.9) | 4,586 (4,753) | -1,908 (-1.9) | NO HEDGE IMPROVEMENT — fails H1, H2, H3, H5 |
| Rates/min_variance | 1M | 10.0 | 1.00× | 1.40× | 1.34× | 5,854 (8,207) | -291 (-588) | 61.4 (91.9) | 4,293 (4,753) | -5,299 (-1.4) | NO HEDGE IMPROVEMENT — fails H1, H2, H3, H5 |
| Rates/name | 1M | 0.5 | 1.00× | 1.50× | 1.49× | 2,962 (2,108) | -301 (-197) | 130 (87.9) | 1,152 (892) | 864 (+4.0) | NO HEDGE IMPROVEMENT — fails H4 |
| Rates/name | 1M | 1.0 | 1.00× | 1.50× | 1.49× | 2,934 (2,108) | -310 (-197) | 128 (87.9) | 1,145 (892) | 899 (+3.2) | NO HEDGE IMPROVEMENT — fails H2, H4 |
| Rates/name | 1M | 2.0 | 1.00× | 1.50× | 1.49× | 2,213 (2,108) | -146 (-197) | 102 (87.9) | 1,000 (892) | -12.1 (-0.1) | NO HEDGE IMPROVEMENT — fails H1, H2, H4 |
| Rates/name | 1M | 5.0 | 1.00× | 1.50× | 1.47× | 1,419 (2,108) | -21.6 (-197) | 61.6 (87.9) | 823 (892) | -1,538 (-2.3) | NO HEDGE IMPROVEMENT — fails H1, H2, H3, H5 |
| Rates/name | 1M | 10.0 | 1.00× | 1.50× | 1.46× | 1,220 (2,108) | -92.9 (-197) | 51.2 (87.9) | 800 (892) | -1,888 (-1.3) | NO HEDGE IMPROVEMENT — fails H1, H2, H3, H5 |
| Rates/systematic | 1M | 0.5 | 1.00× | 1.50× | 1.49× | 7,867 (6,027) | -51.8 (-22) | 157 (107) | 3,053 (2,438) | 1,804 (+3.4) | NO HEDGE IMPROVEMENT — fails H4 |
| Rates/systematic | 1M | 1.0 | 1.00× | 1.50× | 1.49× | 7,844 (6,027) | -59.6 (-22) | 157 (107) | 3,046 (2,438) | 1,805 (+3.0) | NO HEDGE IMPROVEMENT — fails H2, H4 |
| Rates/systematic | 1M | 2.0 | 1.00× | 1.50× | 1.49× | 6,925 (6,027) | -46.5 (-22) | 137 (107) | 2,836 (2,438) | 917 (+1.8) | NO HEDGE IMPROVEMENT — fails H1, H2, H4 |
| Rates/systematic | 1M | 5.0 | 1.00× | 1.50× | 1.47× | 3,905 (6,027) | 29 (-22) | 73.8 (107) | 2,134 (2,438) | -2,344 (-1.6) | NO HEDGE IMPROVEMENT — fails H1, H2, H3, H5 |
| Rates/systematic | 1M | 10.0 | 1.00× | 0.85× | 0.95× | 3,407 (6,027) | 6.81 (-22) | 57.1 (107) | 1,985 (2,438) | -2,858 (-1.0) | NO HEDGE IMPROVEMENT — fails H1, H2, H3, H5 |
| Rates/target_vol | 1M | 0.5 | 1.00× | 1.00× | 1.10× | 0 (0) | -0 (-0) | 0 (0) | 139 (139) | 0 (—) | NO HEDGE IMPROVEMENT — fails H1, H2, H5 |
| Rates/target_vol | 1M | 1.0 | 1.00× | 1.00× | 1.10× | 0 (0) | -0 (-0) | 0 (0) | 139 (139) | 0 (—) | NO HEDGE IMPROVEMENT — fails H1, H2, H5 |
| Rates/target_vol | 1M | 2.0 | 1.00× | 1.00× | 1.10× | 0 (0) | -0 (-0) | 0 (0) | 139 (139) | 0 (—) | NO HEDGE IMPROVEMENT — fails H1, H2, H5 |
| Rates/target_vol | 1M | 5.0 | 1.00× | 1.00× | 1.08× | 0 (0) | -0 (-0) | 0 (0) | 139 (139) | 0 (—) | NO HEDGE IMPROVEMENT — fails H1, H2, H5 |
| Rates/target_vol | 1M | 10.0 | 1.00× | 1.00× | 1.07× | 0 (0) | -0 (-0) | 0 (0) | 139 (139) | 0 (—) | NO HEDGE IMPROVEMENT — fails H1, H2, H5 |
| Rates/var | 1M | 0.5 | 1.00× | 1.50× | 1.49× | 8,841 (6,660) | -778 (-515) | 34.7 (23.4) | 2,454 (2,192) | 2,301 (+3.3) | NO HEDGE IMPROVEMENT — fails H4 |
| Rates/var | 1M | 1.0 | 1.00× | 1.50× | 1.49× | 8,841 (6,660) | -778 (-515) | 34.7 (23.4) | 2,454 (2,192) | 2,433 (+3.3) | NO HEDGE IMPROVEMENT — fails H4 |
| Rates/var | 1M | 2.0 | 1.00× | 1.50× | 1.49× | 8,778 (6,660) | -773 (-515) | 34.4 (23.4) | 2,445 (2,192) | 2,625 (+3.5) | NO HEDGE IMPROVEMENT — fails H4 |
| Rates/var | 1M | 5.0 | 1.00× | 1.50× | 1.47× | 8,162 (6,660) | -699 (-515) | 29.6 (23.4) | 2,340 (2,192) | 2,418 (+3.0) | NO HEDGE IMPROVEMENT — fails H2, H4 |
| Rates/var | 1M | 10.0 | 1.00× | 1.50× | 1.46× | 7,470 (6,660) | -575 (-515) | 24.7 (23.4) | 2,234 (2,192) | 1,409 (+1.2) | NO HEDGE IMPROVEMENT — fails H1, H2, H5 |
| Volatility/volatility | 1M | 0.5 | 1.00× | 1.50× | 1.48× | 4,434 (3,342) | 2,539 (1,820) | 740 (529) | 2,049 (1,464) | 521 (+1.7) | NO HEDGE IMPROVEMENT — fails H1, H2, H4, H5 |
| Volatility/volatility | 1M | 1.0 | 1.00× | 1.50× | 1.48× | 4,434 (3,342) | 2,538 (1,820) | 740 (529) | 2,048 (1,464) | 162 (+0.4) | NO HEDGE IMPROVEMENT — fails H1, H2, H4, H5 |
| Volatility/volatility | 1M | 2.0 | 1.00× | 0.50× | 0.61× | 4,166 (3,342) | 2,242 (1,820) | 702 (529) | 1,865 (1,464) | -194 (-0.4) | NO HEDGE IMPROVEMENT — fails H1, H2, H4, H5 |
| Volatility/volatility | 1M | 5.0 | 1.00× | 0.50× | 0.57× | 2,296 (3,342) | 1,135 (1,820) | 321 (529) | 1,024 (1,464) | 2,588 (+2.8) | NO HEDGE IMPROVEMENT — fails H2, H3 |
| Volatility/volatility | 1M | 10.0 | 1.00× | 0.50× | 0.52× | 2,128 (3,342) | 1,068 (1,820) | 306 (529) | 929 (1,464) | 6,530 (+3.5) | NO HEDGE IMPROVEMENT — fails H2, H3 |
| Commodity/commodity | 3M | 0.5 | 1.00× | 0.90× | 1.12× | 3,862 (3,468) | 3,591 (3,111) | 1,485 (1,307) | 1,517 (1,457) | -23.1 (-0.0) | INSUFFICIENT DATA |
| Commodity/commodity | 3M | 1.0 | 1.00× | 0.50× | 0.89× | 3,696 (3,468) | 3,354 (3,111) | 1,379 (1,307) | 1,485 (1,457) | -86.7 (-0.2) | INSUFFICIENT DATA |
| Commodity/commodity | 3M | 2.0 | 1.00× | 0.50× | 0.67× | 3,407 (3,468) | 2,919 (3,111) | 1,193 (1,307) | 1,451 (1,457) | 436 (+0.9) | INSUFFICIENT DATA |
| Commodity/commodity | 3M | 5.0 | 1.00× | 0.50× | 0.55× | 3,556 (3,468) | 2,259 (3,111) | 995 (1,307) | 1,474 (1,457) | 4,660 (+2.3) | INSUFFICIENT DATA |
| Commodity/commodity | 3M | 10.0 | 1.00× | 0.50× | 0.55× | 3,721 (3,468) | 1,978 (3,111) | 927 (1,307) | 1,494 (1,457) | 11,959 (+2.4) | INSUFFICIENT DATA |
| Commodity/drawdown | 3M | 0.5 | 1.00× | 1.50× | 1.42× | 20,897 (17,312) | 4,652 (3,731) | 2.4 (0.601) | 3,750 (3,745) | 3,124 (+2.1) | INSUFFICIENT DATA |
| Commodity/drawdown | 3M | 1.0 | 1.00× | 1.50× | 1.41× | 20,278 (17,312) | 4,518 (3,731) | 2.66 (0.601) | 3,747 (3,745) | 2,178 (+1.6) | INSUFFICIENT DATA |
| Commodity/drawdown | 3M | 2.0 | 1.00× | 1.50× | 1.13× | 18,731 (17,312) | 4,176 (3,731) | 3.1 (0.601) | 3,745 (3,745) | 526 (+0.6) | INSUFFICIENT DATA |
| Commodity/drawdown | 3M | 5.0 | 1.00× | 0.50× | 0.56× | 18,111 (17,312) | 3,989 (3,731) | 0.802 (0.601) | 3,763 (3,745) | -490 (-0.5) | INSUFFICIENT DATA |
| Commodity/drawdown | 3M | 10.0 | 1.00× | 0.50× | 0.56× | 18,111 (17,312) | 3,643 (3,731) | -5.49 (0.601) | 3,772 (3,745) | 1,692 (+1.3) | INSUFFICIENT DATA |
| Commodity/es | 3M | 0.5 | 1.00× | 1.50× | 1.42× | 20,897 (17,312) | 4,652 (3,731) | 2.4 (0.601) | 3,750 (3,745) | 3,124 (+2.1) | INSUFFICIENT DATA |
| Commodity/es | 3M | 1.0 | 1.00× | 1.50× | 1.41× | 20,278 (17,312) | 4,518 (3,731) | 2.66 (0.601) | 3,747 (3,745) | 2,178 (+1.6) | INSUFFICIENT DATA |
| Commodity/es | 3M | 2.0 | 1.00× | 1.50× | 1.13× | 18,731 (17,312) | 4,176 (3,731) | 3.1 (0.601) | 3,745 (3,745) | 526 (+0.6) | INSUFFICIENT DATA |
| Commodity/es | 3M | 5.0 | 1.00× | 0.50× | 0.56× | 18,111 (17,312) | 3,989 (3,731) | 0.802 (0.601) | 3,763 (3,745) | -490 (-0.5) | INSUFFICIENT DATA |
| Commodity/es | 3M | 10.0 | 1.00× | 0.50× | 0.56× | 18,111 (17,312) | 3,643 (3,731) | -5.49 (0.601) | 3,772 (3,745) | 1,692 (+1.3) | INSUFFICIENT DATA |
| Commodity/min_variance | 3M | 0.5 | 1.00× | 1.15× | 1.28× | 11,985 (11,498) | 12,236 (9,235) | 30.2 (21) | 7,505 (7,450) | -1,023 (-0.4) | INSUFFICIENT DATA |
| Commodity/min_variance | 3M | 1.0 | 1.00× | 0.95× | 1.18× | 12,025 (11,498) | 11,883 (9,235) | 29.7 (21) | 7,486 (7,450) | -2,130 (-0.7) | INSUFFICIENT DATA |
| Commodity/min_variance | 3M | 2.0 | 1.00× | 0.50× | 0.72× | 11,595 (11,498) | 11,047 (9,235) | 24.1 (21) | 7,472 (7,450) | -3,530 (-1.2) | INSUFFICIENT DATA |
| Commodity/min_variance | 3M | 5.0 | 1.00× | 0.50× | 0.56× | 12,054 (11,498) | 9,671 (9,235) | 16.4 (21) | 7,438 (7,450) | -1,618 (-0.5) | INSUFFICIENT DATA |
| Commodity/min_variance | 3M | 10.0 | 1.00× | 0.50× | 0.56× | 12,436 (11,498) | 8,359 (9,235) | 7.85 (21) | 7,476 (7,450) | 9,717 (+1.1) | INSUFFICIENT DATA |
| Commodity/systematic | 3M | 0.5 | 1.00× | 1.50× | 1.42× | 23,695 (19,631) | 8,413 (5,960) | 3,972 (2,900) | 3,441 (2,900) | 1,765 (+0.5) | INSUFFICIENT DATA |
| Commodity/systematic | 3M | 1.0 | 1.00× | 1.50× | 1.41× | 23,345 (19,631) | 8,292 (5,960) | 3,898 (2,900) | 3,372 (2,900) | 383 (+0.1) | INSUFFICIENT DATA |
| Commodity/systematic | 3M | 2.0 | 1.00× | 1.25× | 1.03× | 22,006 (19,631) | 7,964 (5,960) | 3,659 (2,900) | 3,191 (2,900) | -2,391 (-0.7) | INSUFFICIENT DATA |
| Commodity/systematic | 3M | 5.0 | 1.00× | 0.50× | 0.56× | 20,296 (19,631) | 6,731 (5,960) | 3,029 (2,900) | 3,023 (2,900) | -3,317 (-0.9) | INSUFFICIENT DATA |
| Commodity/systematic | 3M | 10.0 | 1.00× | 0.50× | 0.56× | 19,559 (19,631) | 5,570 (5,960) | 2,672 (2,900) | 3,047 (2,900) | 4,059 (+1.2) | INSUFFICIENT DATA |
| Commodity/var | 3M | 0.5 | 1.00× | 1.50× | 1.42× | 20,897 (17,312) | 4,652 (3,731) | 2.4 (0.601) | 3,750 (3,745) | 3,124 (+2.1) | INSUFFICIENT DATA |
| Commodity/var | 3M | 1.0 | 1.00× | 1.50× | 1.41× | 20,278 (17,312) | 4,518 (3,731) | 2.66 (0.601) | 3,747 (3,745) | 2,178 (+1.6) | INSUFFICIENT DATA |
| Commodity/var | 3M | 2.0 | 1.00× | 1.50× | 1.13× | 18,731 (17,312) | 4,176 (3,731) | 3.1 (0.601) | 3,745 (3,745) | 526 (+0.6) | INSUFFICIENT DATA |
| Commodity/var | 3M | 5.0 | 1.00× | 0.50× | 0.56× | 18,111 (17,312) | 3,989 (3,731) | 0.802 (0.601) | 3,763 (3,745) | -490 (-0.5) | INSUFFICIENT DATA |
| Commodity/var | 3M | 10.0 | 1.00× | 0.50× | 0.56× | 18,111 (17,312) | 3,643 (3,731) | -5.49 (0.601) | 3,772 (3,745) | 1,692 (+1.3) | INSUFFICIENT DATA |
| Credit/credit | 3M | 0.5 | 1.00× | 1.50× | 1.44× | 3,592 (3,187) | -365 (-482) | 1,984 (1,528) | 1,643 (1,355) | -109 (-0.2) | INSUFFICIENT DATA |
| Credit/credit | 3M | 1.0 | 1.00× | 1.50× | 1.43× | 3,525 (3,187) | -360 (-482) | 1,936 (1,528) | 1,610 (1,355) | -192 (-0.3) | INSUFFICIENT DATA |
| Credit/credit | 3M | 2.0 | 1.00× | 1.50× | 1.40× | 3,388 (3,187) | -503 (-482) | 1,779 (1,528) | 1,442 (1,355) | -8.19 (-0.0) | INSUFFICIENT DATA |
| Credit/credit | 3M | 5.0 | 1.00× | 1.50× | 1.34× | 2,947 (3,187) | -317 (-482) | 1,576 (1,528) | 1,292 (1,355) | -1,111 (-0.6) | INSUFFICIENT DATA |
| Credit/credit | 3M | 10.0 | 1.00× | 1.50× | 1.34× | 2,581 (3,187) | -195 (-482) | 1,422 (1,528) | 1,165 (1,355) | -3,371 (-0.6) | INSUFFICIENT DATA |
| Credit/name | 3M | 0.5 | 1.00× | 1.50× | 1.43× | 8,037 (5,816) | 1,367 (1,022) | 1,526 (1,085) | 1,143 (839) | 1,607 (+2.3) | INSUFFICIENT DATA |
| Credit/name | 3M | 1.0 | 1.00× | 1.50× | 1.42× | 7,930 (5,816) | 1,338 (1,022) | 1,506 (1,085) | 1,128 (839) | 1,377 (+1.6) | INSUFFICIENT DATA |
| Credit/name | 3M | 2.0 | 1.00× | 1.50× | 1.39× | 6,917 (5,816) | 1,284 (1,022) | 1,398 (1,085) | 983 (839) | 264 (+0.5) | INSUFFICIENT DATA |
| Credit/name | 3M | 5.0 | 1.00× | 1.50× | 1.32× | 6,126 (5,816) | 1,338 (1,022) | 1,270 (1,085) | 891 (839) | -1,454 (-2.3) | INSUFFICIENT DATA |
| Credit/name | 3M | 10.0 | 1.00× | 0.50× | 0.82× | 4,569 (5,816) | 1,200 (1,022) | 981 (1,085) | 712 (839) | -2,920 (-0.7) | INSUFFICIENT DATA |
| Equity/beta | 3M | 0.5 | 1.00× | 1.50× | 1.41× | 32,193 (26,590) | 19,636 (14,880) | -776 (-532) | 3,871 (2,588) | 3,469 (+2.4) | INSUFFICIENT DATA |
| Equity/beta | 3M | 1.0 | 1.00× | 1.25× | 1.22× | 28,854 (26,590) | 16,551 (14,880) | -553 (-532) | 3,103 (2,588) | 615 (+0.5) | INSUFFICIENT DATA |
| Equity/beta | 3M | 2.0 | 1.00× | 0.50× | 0.65× | 20,676 (26,590) | 10,806 (14,880) | -301 (-532) | 2,492 (2,588) | 2,003 (+0.6) | INSUFFICIENT DATA |
| Equity/beta | 3M | 5.0 | 1.00× | 0.50× | 0.55× | 17,028 (26,590) | 8,727 (14,880) | -279 (-532) | 2,804 (2,588) | 20,948 (+2.2) | INSUFFICIENT DATA |
| Equity/beta | 3M | 10.0 | 1.00× | 0.50× | 0.55× | 17,028 (26,590) | 8,727 (14,880) | -279 (-532) | 2,804 (2,588) | 51,709 (+2.8) | INSUFFICIENT DATA |
| Equity/crash | 3M | 0.5 | 1.00× | 0.65× | 0.96× | 23,687 (22,755) | 18,123 (16,641) | 6,672 (6,051) | 2,217 (1,253) | -429 (-0.1) | INSUFFICIENT DATA |
| Equity/crash | 3M | 1.0 | 1.00× | 0.55× | 0.84× | 23,897 (22,755) | 15,323 (16,641) | 5,741 (6,051) | 1,531 (1,253) | 2,770 (+0.8) | INSUFFICIENT DATA |
| Equity/crash | 3M | 2.0 | 1.00× | 0.50× | 0.65× | 22,338 (22,755) | 12,464 (16,641) | 4,611 (6,051) | 1,646 (1,253) | 9,376 (+1.5) | INSUFFICIENT DATA |
| Equity/crash | 3M | 5.0 | 1.00× | 0.50× | 0.55× | 19,813 (22,755) | 9,714 (16,641) | 3,524 (6,051) | 2,176 (1,253) | 34,219 (+2.7) | INSUFFICIENT DATA |
| Equity/crash | 3M | 10.0 | 1.00× | 0.50× | 0.55× | 19,813 (22,755) | 9,714 (16,641) | 3,524 (6,051) | 2,176 (1,253) | 68,852 (+3.3) | INSUFFICIENT DATA |
| Equity/drawdown | 3M | 0.5 | 1.00× | 1.50× | 1.41× | 61,769 (51,477) | 19,391 (14,935) | -75.2 (-65.4) | 3,434 (3,058) | 8,074 (+1.4) | INSUFFICIENT DATA |
| Equity/drawdown | 3M | 1.0 | 1.00× | 1.50× | 1.35× | 59,750 (51,477) | 18,083 (14,935) | -74.7 (-65.4) | 3,187 (3,058) | 5,134 (+0.9) | INSUFFICIENT DATA |
| Equity/drawdown | 3M | 2.0 | 1.00× | 1.25× | 1.05× | 53,480 (51,477) | 14,841 (14,935) | -45.9 (-65.4) | 3,048 (3,058) | 2,171 (+0.5) | INSUFFICIENT DATA |
| Equity/drawdown | 3M | 5.0 | 1.00× | 0.50× | 0.55× | 39,677 (51,477) | 9,888 (14,935) | -27.1 (-65.4) | 3,739 (3,058) | 13,398 (+1.0) | INSUFFICIENT DATA |
| Equity/drawdown | 3M | 10.0 | 1.00× | 0.50× | 0.55× | 39,017 (51,477) | 9,898 (14,935) | -28.5 (-65.4) | 3,830 (3,058) | 37,870 (+1.6) | INSUFFICIENT DATA |
| Equity/es | 3M | 0.5 | 1.00× | 1.50× | 1.41× | 61,769 (51,477) | 19,391 (14,935) | -75.2 (-65.4) | 3,434 (3,058) | 8,074 (+1.4) | INSUFFICIENT DATA |
| Equity/es | 3M | 1.0 | 1.00× | 1.50× | 1.35× | 59,750 (51,477) | 18,083 (14,935) | -74.7 (-65.4) | 3,187 (3,058) | 5,134 (+0.9) | INSUFFICIENT DATA |
| Equity/es | 3M | 2.0 | 1.00× | 1.25× | 1.05× | 53,480 (51,477) | 14,841 (14,935) | -45.9 (-65.4) | 3,048 (3,058) | 2,171 (+0.5) | INSUFFICIENT DATA |
| Equity/es | 3M | 5.0 | 1.00× | 0.50× | 0.55× | 39,677 (51,477) | 9,888 (14,935) | -27.1 (-65.4) | 3,739 (3,058) | 13,398 (+1.0) | INSUFFICIENT DATA |
| Equity/es | 3M | 10.0 | 1.00× | 0.50× | 0.55× | 39,017 (51,477) | 9,898 (14,935) | -28.5 (-65.4) | 3,830 (3,058) | 37,870 (+1.6) | INSUFFICIENT DATA |
| Equity/min_variance | 3M | 0.5 | 1.00× | 0.90× | 1.09× | 48,319 (48,042) | 32,323 (31,268) | 259 (229) | 5,503 (5,600) | -280 (-0.4) | INSUFFICIENT DATA |
| Equity/min_variance | 3M | 1.0 | 1.00× | 0.75× | 0.95× | 46,724 (48,042) | 28,662 (31,268) | 233 (229) | 5,733 (5,600) | 1,284 (+1.1) | INSUFFICIENT DATA |
| Equity/min_variance | 3M | 2.0 | 1.00× | 0.50× | 0.65× | 39,520 (48,042) | 21,538 (31,268) | 178 (229) | 7,086 (5,600) | 10,988 (+1.8) | INSUFFICIENT DATA |
| Equity/min_variance | 3M | 5.0 | 1.00× | 0.50× | 0.55× | 35,276 (48,042) | 18,355 (31,268) | 134 (229) | 8,039 (5,600) | 51,893 (+2.6) | INSUFFICIENT DATA |
| Equity/min_variance | 3M | 10.0 | 1.00× | 0.50× | 0.55× | 35,276 (48,042) | 18,355 (31,268) | 134 (229) | 8,039 (5,600) | 116,456 (+2.9) | INSUFFICIENT DATA |
| Equity/name | 3M | 0.5 | 1.00× | 1.50× | 1.41× | 57,315 (43,883) | 22,190 (15,875) | 3,170 (2,253) | 4,575 (2,920) | 9,357 (+4.2) | INSUFFICIENT DATA |
| Equity/name | 3M | 1.0 | 1.00× | 1.50× | 1.35× | 55,086 (43,883) | 21,208 (15,875) | 3,063 (2,253) | 4,275 (2,920) | 5,060 (+2.2) | INSUFFICIENT DATA |
| Equity/name | 3M | 2.0 | 1.00× | 1.35× | 1.11× | 45,308 (43,883) | 16,409 (15,875) | 2,534 (2,253) | 3,149 (2,920) | 76.6 (+0.0) | INSUFFICIENT DATA |
| Equity/name | 3M | 5.0 | 1.00× | 0.50× | 0.55× | 30,335 (43,883) | 10,415 (15,875) | 1,653 (2,253) | 3,062 (2,920) | 14,354 (+1.6) | INSUFFICIENT DATA |
| Equity/name | 3M | 10.0 | 1.00× | 0.50× | 0.55× | 29,830 (43,883) | 10,294 (15,875) | 1,573 (2,253) | 3,120 (2,920) | 42,437 (+2.3) | INSUFFICIENT DATA |
| Equity/sector | 3M | 0.5 | 1.00× | 1.40× | 1.36× | 39,349 (33,915) | 14,404 (11,284) | 4,163 (3,118) | 6,851 (5,182) | 2,829 (+1.6) | INSUFFICIENT DATA |
| Equity/sector | 3M | 1.0 | 1.00× | 1.25× | 1.22× | 36,458 (33,915) | 12,780 (11,284) | 3,790 (3,118) | 6,094 (5,182) | 375 (+0.3) | INSUFFICIENT DATA |
| Equity/sector | 3M | 2.0 | 1.00× | 0.80× | 0.81× | 28,216 (33,915) | 9,145 (11,284) | 2,832 (3,118) | 4,431 (5,182) | -1,134 (-0.6) | INSUFFICIENT DATA |
| Equity/sector | 3M | 5.0 | 1.00× | 0.50× | 0.55× | 23,093 (33,915) | 7,070 (11,284) | 2,042 (3,118) | 3,532 (5,182) | 11,323 (+1.4) | INSUFFICIENT DATA |
| Equity/sector | 3M | 10.0 | 1.00× | 0.50× | 0.55× | 21,792 (33,915) | 6,570 (11,284) | 1,789 (3,118) | 3,380 (5,182) | 36,344 (+2.0) | INSUFFICIENT DATA |
| Equity/systematic | 3M | 0.5 | 1.00× | 1.50× | 1.41× | 44,024 (37,185) | 19,511 (15,237) | 2,260 (1,683) | 4,144 (2,993) | 4,125 (+2.2) | INSUFFICIENT DATA |
| Equity/systematic | 3M | 1.0 | 1.00× | 1.30× | 1.25× | 40,707 (37,185) | 17,252 (15,237) | 2,053 (1,683) | 3,495 (2,993) | 1,136 (+0.7) | INSUFFICIENT DATA |
| Equity/systematic | 3M | 2.0 | 1.00× | 0.70× | 0.76× | 30,699 (37,185) | 11,987 (15,237) | 1,529 (1,683) | 2,922 (2,993) | 167 (+0.1) | INSUFFICIENT DATA |
| Equity/systematic | 3M | 5.0 | 1.00× | 0.50× | 0.55× | 26,327 (37,185) | 9,894 (15,237) | 1,196 (1,683) | 3,210 (2,993) | 16,345 (+1.5) | INSUFFICIENT DATA |
| Equity/systematic | 3M | 10.0 | 1.00× | 0.50× | 0.55× | 25,499 (37,185) | 9,736 (15,237) | 1,131 (1,683) | 3,314 (2,993) | 43,874 (+2.0) | INSUFFICIENT DATA |
| Equity/target_vol | 3M | 0.5 | 1.00× | 1.30× | 1.31× | 33,216 (29,372) | 21,357 (17,906) | 12.1 (7.45) | 3,421 (3,160) | 2,114 (+2.4) | INSUFFICIENT DATA |
| Equity/target_vol | 3M | 1.0 | 1.00× | 1.05× | 1.11× | 30,261 (29,372) | 18,253 (17,906) | 22.7 (7.45) | 3,171 (3,160) | 527 (+0.7) | INSUFFICIENT DATA |
| Equity/target_vol | 3M | 2.0 | 1.00× | 0.50× | 0.65× | 23,013 (29,372) | 12,684 (17,906) | 25.4 (7.45) | 3,679 (3,160) | 4,069 (+0.9) | INSUFFICIENT DATA |
| Equity/target_vol | 3M | 5.0 | 1.00× | 0.50× | 0.55× | 20,607 (29,372) | 11,361 (17,906) | 17.8 (7.45) | 4,055 (3,160) | 23,947 (+1.7) | INSUFFICIENT DATA |
| Equity/target_vol | 3M | 10.0 | 1.00× | 0.50× | 0.55× | 19,789 (29,372) | 10,648 (17,906) | 9.65 (7.45) | 4,160 (3,160) | 62,996 (+2.1) | INSUFFICIENT DATA |
| Equity/var | 3M | 0.5 | 1.00× | 1.50× | 1.41× | 61,769 (51,477) | 19,391 (14,935) | -75.2 (-65.4) | 3,434 (3,058) | 8,074 (+1.4) | INSUFFICIENT DATA |
| Equity/var | 3M | 1.0 | 1.00× | 1.50× | 1.35× | 59,750 (51,477) | 18,083 (14,935) | -74.7 (-65.4) | 3,187 (3,058) | 5,134 (+0.9) | INSUFFICIENT DATA |
| Equity/var | 3M | 2.0 | 1.00× | 1.25× | 1.05× | 53,480 (51,477) | 14,841 (14,935) | -45.9 (-65.4) | 3,048 (3,058) | 2,171 (+0.5) | INSUFFICIENT DATA |
| Equity/var | 3M | 5.0 | 1.00× | 0.50× | 0.55× | 39,677 (51,477) | 9,888 (14,935) | -27.1 (-65.4) | 3,739 (3,058) | 13,398 (+1.0) | INSUFFICIENT DATA |
| Equity/var | 3M | 10.0 | 1.00× | 0.50× | 0.55× | 39,017 (51,477) | 9,898 (14,935) | -28.5 (-65.4) | 3,830 (3,058) | 37,870 (+1.6) | INSUFFICIENT DATA |
| FX/fx | 3M | 0.5 | 1.00× | 1.50× | 1.44× | 564 (437) | 116 (11.7) | 11.2 (8.35) | 276 (184) | 72.1 (+0.4) | INSUFFICIENT DATA |
| FX/fx | 3M | 1.0 | 1.00× | 1.50× | 1.43× | 558 (437) | 111 (11.7) | 11.1 (8.35) | 269 (184) | 19 (+0.1) | INSUFFICIENT DATA |
| FX/fx | 3M | 2.0 | 1.00× | 1.50× | 1.40× | 544 (437) | 105 (11.7) | 10.6 (8.35) | 252 (184) | -81.5 (-0.3) | INSUFFICIENT DATA |
| FX/fx | 3M | 5.0 | 1.00× | 1.50× | 1.34× | 535 (437) | 114 (11.7) | 10.4 (8.35) | 244 (184) | -414 (-0.7) | INSUFFICIENT DATA |
| FX/fx | 3M | 10.0 | 1.00× | 1.50× | 1.34× | 535 (437) | 114 (11.7) | 10.4 (8.35) | 244 (184) | -925 (-0.8) | INSUFFICIENT DATA |
| Rates/curve | 3M | 0.5 | 1.00× | 1.50× | 1.44× | 3,172 (2,979) | -970 (-836) | 117 (97.3) | 1,968 (1,786) | 240 (+1.4) | INSUFFICIENT DATA |
| Rates/curve | 3M | 1.0 | 1.00× | 1.45× | 1.40× | 2,610 (2,979) | -777 (-836) | 96 (97.3) | 1,765 (1,786) | -427 (-2.0) | INSUFFICIENT DATA |
| Rates/curve | 3M | 2.0 | 1.00× | 1.25× | 1.25× | 2,233 (2,979) | -595 (-836) | 79 (97.3) | 1,611 (1,786) | -1,211 (-1.5) | INSUFFICIENT DATA |
| Rates/curve | 3M | 5.0 | 1.00× | 0.60× | 0.80× | 1,914 (2,979) | -473 (-836) | 64 (97.3) | 1,497 (1,786) | -2,846 (-1.2) | INSUFFICIENT DATA |
| Rates/curve | 3M | 10.0 | 1.00× | 0.50× | 0.72× | 1,917 (2,979) | -457 (-836) | 61.9 (97.3) | 1,482 (1,786) | -4,819 (-1.1) | INSUFFICIENT DATA |
| Rates/drawdown | 3M | 0.5 | 1.00× | 1.50× | 1.44× | 5,843 (5,315) | -2,030 (-1,428) | 114 (82.5) | 2,895 (2,527) | 799 (+0.4) | INSUFFICIENT DATA |
| Rates/drawdown | 3M | 1.0 | 1.00× | 1.50× | 1.43× | 5,787 (5,315) | -1,961 (-1,428) | 111 (82.5) | 2,848 (2,527) | 978 (+0.5) | INSUFFICIENT DATA |
| Rates/drawdown | 3M | 2.0 | 1.00× | 1.50× | 1.39× | 5,656 (5,315) | -1,953 (-1,428) | 101 (82.5) | 2,728 (2,527) | 1,372 (+1.0) | INSUFFICIENT DATA |
| Rates/drawdown | 3M | 5.0 | 1.00× | 1.50× | 1.29× | 5,069 (5,315) | -1,142 (-1,428) | 66.5 (82.5) | 2,352 (2,527) | -1,658 (-0.5) | INSUFFICIENT DATA |
| Rates/drawdown | 3M | 10.0 | 1.00× | 1.50× | 1.26× | 5,069 (5,315) | -1,268 (-1,428) | 61.6 (82.5) | 2,320 (2,527) | -1,827 (-0.4) | INSUFFICIENT DATA |
| Rates/duration | 3M | 0.5 | 1.00× | 1.45× | 1.41× | 2,872 (2,826) | -609 (-591) | 122 (106) | 1,948 (1,808) | 38.7 (+0.3) | INSUFFICIENT DATA |
| Rates/duration | 3M | 1.0 | 1.00× | 1.35× | 1.35× | 2,463 (2,826) | -495 (-591) | 103 (106) | 1,770 (1,808) | -456 (-1.9) | INSUFFICIENT DATA |
| Rates/duration | 3M | 2.0 | 1.00× | 1.15× | 1.20× | 2,180 (2,826) | -411 (-591) | 86.2 (106) | 1,631 (1,808) | -986 (-1.3) | INSUFFICIENT DATA |
| Rates/duration | 3M | 5.0 | 1.00× | 0.55× | 0.77× | 1,832 (2,826) | -299 (-591) | 69.6 (106) | 1,509 (1,808) | -2,419 (-1.0) | INSUFFICIENT DATA |
| Rates/duration | 3M | 10.0 | 1.00× | 0.50× | 0.72× | 1,837 (2,826) | -291 (-591) | 67.4 (106) | 1,495 (1,808) | -3,954 (-0.9) | INSUFFICIENT DATA |
| Rates/es | 3M | 0.5 | 1.00× | 1.50× | 1.44× | 5,843 (5,315) | -2,030 (-1,428) | 114 (82.5) | 2,895 (2,527) | 799 (+0.4) | INSUFFICIENT DATA |
| Rates/es | 3M | 1.0 | 1.00× | 1.50× | 1.43× | 5,787 (5,315) | -1,961 (-1,428) | 111 (82.5) | 2,848 (2,527) | 978 (+0.5) | INSUFFICIENT DATA |
| Rates/es | 3M | 2.0 | 1.00× | 1.50× | 1.39× | 5,656 (5,315) | -1,953 (-1,428) | 101 (82.5) | 2,728 (2,527) | 1,372 (+1.0) | INSUFFICIENT DATA |
| Rates/es | 3M | 5.0 | 1.00× | 1.50× | 1.29× | 5,069 (5,315) | -1,142 (-1,428) | 66.5 (82.5) | 2,352 (2,527) | -1,658 (-0.5) | INSUFFICIENT DATA |
| Rates/es | 3M | 10.0 | 1.00× | 1.50× | 1.26× | 5,069 (5,315) | -1,268 (-1,428) | 61.6 (82.5) | 2,320 (2,527) | -1,827 (-0.4) | INSUFFICIENT DATA |
| Rates/min_variance | 3M | 0.5 | 1.00× | 1.20× | 1.28× | 20,726 (19,682) | -3,406 (-2,571) | 262 (211) | 6,056 (5,451) | 1,410 (+2.1) | INSUFFICIENT DATA |
| Rates/min_variance | 3M | 1.0 | 1.00× | 1.20× | 1.27× | 20,713 (19,682) | -3,300 (-2,571) | 250 (211) | 5,891 (5,451) | 1,721 (+2.3) | INSUFFICIENT DATA |
| Rates/min_variance | 3M | 2.0 | 1.00× | 1.20× | 1.23× | 20,023 (19,682) | -3,029 (-2,571) | 223 (211) | 5,543 (5,451) | 1,245 (+2.3) | INSUFFICIENT DATA |
| Rates/min_variance | 3M | 5.0 | 1.00× | 1.15× | 1.10× | 15,266 (19,682) | -2,479 (-2,571) | 159 (211) | 4,862 (5,451) | -4,825 (-0.9) | INSUFFICIENT DATA |
| Rates/min_variance | 3M | 10.0 | 1.00× | 1.10× | 1.05× | 15,167 (19,682) | -2,509 (-2,571) | 152 (211) | 4,828 (5,451) | -5,081 (-0.5) | INSUFFICIENT DATA |
| Rates/name | 3M | 0.5 | 1.00× | 1.50× | 1.44× | 5,745 (4,223) | -1,354 (-939) | 392 (278) | 1,369 (1,080) | 1,616 (+3.1) | INSUFFICIENT DATA |
| Rates/name | 3M | 1.0 | 1.00× | 1.50× | 1.43× | 5,595 (4,223) | -1,315 (-939) | 380 (278) | 1,336 (1,080) | 1,645 (+2.5) | INSUFFICIENT DATA |
| Rates/name | 3M | 2.0 | 1.00× | 1.50× | 1.39× | 4,410 (4,223) | -933 (-939) | 283 (278) | 1,114 (1,080) | 169 (+0.4) | INSUFFICIENT DATA |
| Rates/name | 3M | 5.0 | 1.00× | 1.50× | 1.29× | 3,119 (4,223) | -709 (-939) | 196 (278) | 936 (1,080) | -2,170 (-1.3) | INSUFFICIENT DATA |
| Rates/name | 3M | 10.0 | 1.00× | 1.50× | 1.26× | 3,102 (4,223) | -720 (-939) | 194 (278) | 929 (1,080) | -3,233 (-1.0) | INSUFFICIENT DATA |
| Rates/systematic | 3M | 0.5 | 1.00× | 1.50× | 1.44× | 14,997 (11,779) | -683 (-406) | 301 (216) | 3,223 (2,638) | 3,271 (+2.4) | INSUFFICIENT DATA |
| Rates/systematic | 3M | 1.0 | 1.00× | 1.50× | 1.43× | 14,399 (11,779) | -596 (-406) | 285 (216) | 3,091 (2,638) | 2,741 (+1.9) | INSUFFICIENT DATA |
| Rates/systematic | 3M | 2.0 | 1.00× | 1.50× | 1.39× | 11,354 (11,779) | -397 (-406) | 221 (216) | 2,637 (2,638) | -449 (-0.5) | INSUFFICIENT DATA |
| Rates/systematic | 3M | 5.0 | 1.00× | 0.95× | 0.99× | 7,575 (11,779) | -60.9 (-406) | 143 (216) | 2,211 (2,638) | -5,859 (-1.0) | INSUFFICIENT DATA |
| Rates/systematic | 3M | 10.0 | 1.00× | 0.50× | 0.72× | 7,294 (11,779) | -241 (-406) | 131 (216) | 2,170 (2,638) | -6,053 (-0.5) | INSUFFICIENT DATA |
| Rates/target_vol | 3M | 0.5 | 1.00× | 1.50× | 1.44× | 82.9 (67.3) | -107 (-84.7) | 0.748 (0.592) | 192 (183) | 26.6 (+1.4) | INSUFFICIENT DATA |
| Rates/target_vol | 3M | 1.0 | 1.00× | 1.50× | 1.43× | 80.6 (67.3) | -104 (-84.7) | 0.725 (0.592) | 191 (183) | 32.2 (+1.3) | INSUFFICIENT DATA |
| Rates/target_vol | 3M | 2.0 | 1.00× | 1.50× | 1.39× | 75.2 (67.3) | -95.9 (-84.7) | 0.67 (0.592) | 187 (183) | 30.2 (+1.2) | INSUFFICIENT DATA |
| Rates/target_vol | 3M | 5.0 | 1.00× | 1.50× | 1.29× | 68.4 (67.3) | -86.3 (-84.7) | 0.603 (0.592) | 183 (183) | 9.04 (+1.2) | INSUFFICIENT DATA |
| Rates/target_vol | 3M | 10.0 | 1.00× | 1.50× | 1.26× | 68.4 (67.3) | -86.3 (-84.7) | 0.603 (0.592) | 183 (183) | 16.9 (+1.1) | INSUFFICIENT DATA |
| Rates/var | 3M | 0.5 | 1.00× | 1.50× | 1.44× | 5,843 (5,315) | -2,030 (-1,428) | 114 (82.5) | 2,895 (2,527) | 799 (+0.4) | INSUFFICIENT DATA |
| Rates/var | 3M | 1.0 | 1.00× | 1.50× | 1.43× | 5,787 (5,315) | -1,961 (-1,428) | 111 (82.5) | 2,848 (2,527) | 978 (+0.5) | INSUFFICIENT DATA |
| Rates/var | 3M | 2.0 | 1.00× | 1.50× | 1.39× | 5,656 (5,315) | -1,953 (-1,428) | 101 (82.5) | 2,728 (2,527) | 1,372 (+1.0) | INSUFFICIENT DATA |
| Rates/var | 3M | 5.0 | 1.00× | 1.50× | 1.29× | 5,069 (5,315) | -1,142 (-1,428) | 66.5 (82.5) | 2,352 (2,527) | -1,658 (-0.5) | INSUFFICIENT DATA |
| Rates/var | 3M | 10.0 | 1.00× | 1.50× | 1.26× | 5,069 (5,315) | -1,268 (-1,428) | 61.6 (82.5) | 2,320 (2,527) | -1,827 (-0.4) | INSUFFICIENT DATA |
| Volatility/volatility | 3M | 0.5 | 1.00× | 1.50× | 1.38× | 4,129 (3,579) | 3,252 (2,584) | 1,274 (1,003) | 953 (798) | -55.9 (-0.2) | INSUFFICIENT DATA |
| Volatility/volatility | 3M | 1.0 | 1.00× | 0.50× | 0.79× | 3,929 (3,579) | 3,074 (2,584) | 1,229 (1,003) | 910 (798) | -367 (-1.4) | INSUFFICIENT DATA |
| Volatility/volatility | 3M | 2.0 | 1.00× | 0.50× | 0.73× | 3,333 (3,579) | 2,550 (2,584) | 1,022 (1,003) | 781 (798) | -198 (-1.1) | INSUFFICIENT DATA |
| Volatility/volatility | 3M | 5.0 | 1.00× | 0.50× | 0.60× | 2,906 (3,579) | 1,890 (2,584) | 725 (1,003) | 676 (798) | 3,074 (+2.7) | INSUFFICIENT DATA |
| Volatility/volatility | 3M | 10.0 | 1.00× | 0.50× | 0.60× | 2,906 (3,579) | 1,890 (2,584) | 725 (1,003) | 676 (798) | 6,543 (+3.0) | INSUFFICIENT DATA |

Surface by volatility regime (shrunk multiple at λ = 0.5 / 1 / 2 / 5 / 10):

| Horizon | Node | Dates | Shrunk by λ | Best fit by λ |
|---|---|---|---|---|
| 1W | global | 446 | 1.40 / 1.35 / 1.22 / 0.87 / 0.56 | 1.45 / 1.40 / 1.25 / 0.85 / 0.50 |
| 1W | objective:Commodity/commodity | 446 | 1.50 / 1.32 / 0.82 / 0.51 / 0.50 | 1.50 / 1.30 / 0.75 / 0.50 / 0.50 |
| 1W | objective:Commodity/commodity · high_vol | 212 | 1.27 / 0.95 / 0.57 / 0.50 / 0.50 | 1.20 / 0.85 / 0.50 / 0.50 / 0.50 |
| 1W | objective:Commodity/commodity · low_vol | 234 | 1.50 / 1.46 / 1.36 / 0.90 / 0.50 | 1.50 / 1.50 / 1.50 / 1.00 / 0.50 |
| 1W | objective:Commodity/drawdown | 273 | 1.50 / 1.50 / 1.47 / 0.51 / 0.50 | 1.50 / 1.50 / 1.50 / 0.50 / 0.50 |
| 1W | objective:Commodity/drawdown · high_vol | 132 | 1.50 / 1.50 / 1.49 / 0.50 / 0.50 | 1.50 / 1.50 / 1.50 / 0.50 / 0.50 |
| 1W | objective:Commodity/drawdown · low_vol | 141 | 0.80 / 0.80 / 0.79 / 0.50 / 0.50 | 0.50 / 0.50 / 0.50 / 0.50 / 0.50 |
| 1W | objective:Commodity/es | 273 | 1.50 / 1.50 / 1.47 / 0.51 / 0.50 | 1.50 / 1.50 / 1.50 / 0.50 / 0.50 |
| 1W | objective:Commodity/es · high_vol | 132 | 1.50 / 1.50 / 1.49 / 0.50 / 0.50 | 1.50 / 1.50 / 1.50 / 0.50 / 0.50 |
| 1W | objective:Commodity/es · low_vol | 141 | 0.80 / 0.80 / 0.79 / 0.50 / 0.50 | 0.50 / 0.50 / 0.50 / 0.50 / 0.50 |
| 1W | objective:Commodity/min_variance | 273 | 1.46 / 1.29 / 0.81 / 0.51 / 0.50 | 1.45 / 1.25 / 0.70 / 0.50 / 0.50 |
| 1W | objective:Commodity/min_variance · high_vol | 132 | 1.45 / 1.16 / 0.60 / 0.50 / 0.50 | 1.45 / 1.10 / 0.50 / 0.50 / 0.50 |
| 1W | objective:Commodity/min_variance · low_vol | 141 | 1.42 / 1.40 / 1.30 / 1.20 / 1.20 | 1.40 / 1.45 / 1.50 / 1.50 / 1.50 |
| 1W | objective:Commodity/systematic | 273 | 1.50 / 1.50 / 1.43 / 0.51 / 0.50 | 1.50 / 1.50 / 1.45 / 0.50 / 0.50 |
| 1W | objective:Commodity/systematic · high_vol | 132 | 1.50 / 1.50 / 1.31 / 0.50 / 0.50 | 1.50 / 1.50 / 1.25 / 0.50 / 0.50 |
| 1W | objective:Commodity/systematic · low_vol | 141 | 1.50 / 1.50 / 1.48 / 0.96 / 0.50 | 1.50 / 1.50 / 1.50 / 1.15 / 0.50 |
| 1W | objective:Commodity/target_vol | 183 | 1.50 / 1.50 / 0.71 / 0.51 / 0.50 | 1.50 / 1.50 / 0.50 / 0.50 / 0.50 |
| 1W | objective:Commodity/target_vol · high_vol | 106 | 1.50 / 1.31 / 0.57 / 0.50 / 0.50 | 1.50 / 1.20 / 0.50 / 0.50 / 0.50 |
| 1W | objective:Commodity/target_vol · low_vol | 77 | 1.22 / 1.22 / 0.87 / 0.79 / 0.78 | 1.00 / 1.00 / 1.00 / 1.00 / 1.00 |
| 1W | objective:Commodity/var | 273 | 1.50 / 1.50 / 1.47 / 0.51 / 0.50 | 1.50 / 1.50 / 1.50 / 0.50 / 0.50 |
| 1W | objective:Commodity/var · high_vol | 132 | 1.50 / 1.50 / 1.49 / 0.50 / 0.50 | 1.50 / 1.50 / 1.50 / 0.50 / 0.50 |
| 1W | objective:Commodity/var · low_vol | 141 | 0.80 / 0.80 / 0.79 / 0.50 / 0.50 | 0.50 / 0.50 / 0.50 / 0.50 / 0.50 |
| 1W | objective:Credit/credit | 446 | 1.50 / 1.50 / 1.50 / 1.49 / 0.91 | 1.50 / 1.50 / 1.50 / 1.50 / 0.85 |
| 1W | objective:Credit/credit · high_vol | 212 | 1.50 / 1.50 / 1.50 / 0.72 / 0.59 | 1.50 / 1.50 / 1.50 / 0.50 / 0.50 |
| 1W | objective:Credit/credit · low_vol | 234 | 1.50 / 1.50 / 1.50 / 1.50 / 1.38 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 |
| 1W | objective:Credit/drawdown | 291 | 1.08 / 1.08 / 1.08 / 1.07 / 1.07 | 1.00 / 1.00 / 1.00 / 1.00 / 1.00 |
| 1W | objective:Credit/drawdown · high_vol | 159 | 1.02 / 1.02 / 1.02 / 1.02 / 1.02 | 1.00 / 1.00 / 1.00 / 1.00 / 1.00 |
| 1W | objective:Credit/drawdown · low_vol | 132 | 1.03 / 1.03 / 1.02 / 1.02 / 1.02 | 1.00 / 1.00 / 1.00 / 1.00 / 1.00 |
| 1W | objective:Credit/es | 291 | 1.08 / 1.08 / 1.08 / 1.07 / 1.07 | 1.00 / 1.00 / 1.00 / 1.00 / 1.00 |
| 1W | objective:Credit/es · high_vol | 159 | 1.02 / 1.02 / 1.02 / 1.02 / 1.02 | 1.00 / 1.00 / 1.00 / 1.00 / 1.00 |
| 1W | objective:Credit/es · low_vol | 132 | 1.03 / 1.03 / 1.02 / 1.02 / 1.02 | 1.00 / 1.00 / 1.00 / 1.00 / 1.00 |
| 1W | objective:Credit/min_variance | 291 | 1.12 / 1.12 / 1.12 / 1.12 / 1.12 | 0.80 / 0.85 / 0.95 / 1.15 / 1.50 |
| 1W | objective:Credit/min_variance · high_vol | 159 | 0.67 / 0.67 / 0.67 / 0.67 / 0.67 | 0.50 / 0.50 / 0.50 / 0.50 / 0.50 |
| 1W | objective:Credit/min_variance · low_vol | 132 | 1.38 / 1.38 / 1.38 / 1.38 / 1.38 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 |
| 1W | objective:Credit/name | 352 | 1.50 / 1.50 / 1.50 / 1.49 / 0.84 | 1.50 / 1.50 / 1.50 / 1.50 / 0.75 |
| 1W | objective:Credit/name · high_vol | 184 | 1.50 / 1.50 / 1.50 / 1.50 / 0.58 | 1.50 / 1.50 / 1.50 / 1.50 / 0.50 |
| 1W | objective:Credit/name · low_vol | 168 | 1.50 / 1.50 / 1.50 / 1.50 / 0.77 | 1.50 / 1.50 / 1.50 / 1.50 / 0.75 |
| 1W | objective:Credit/systematic | 8 | 1.49 / 1.48 / 1.47 / 1.43 / 1.40 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 |
| 1W | objective:Credit/systematic · high_vol | 8 | 1.49 / 1.49 / 1.47 / 1.44 / 1.41 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 |
| 1W | objective:Credit/target_vol | 77 | 1.21 / 1.21 / 1.20 / 1.19 / 1.17 | 1.00 / 1.00 / 1.00 / 1.00 / 1.00 |
| 1W | objective:Credit/target_vol · high_vol | 66 | 1.10 / 1.10 / 1.10 / 1.09 / 1.08 | 1.00 / 1.00 / 1.00 / 1.00 / 1.00 |
| 1W | objective:Credit/target_vol · low_vol | 11 | 1.18 / 1.18 / 1.17 / 1.16 / 1.14 | 1.00 / 1.00 / 1.00 / 1.00 / 1.00 |
| 1W | objective:Credit/var | 291 | 1.08 / 1.08 / 1.08 / 1.07 / 1.07 | 1.00 / 1.00 / 1.00 / 1.00 / 1.00 |
| 1W | objective:Credit/var · high_vol | 159 | 1.02 / 1.02 / 1.02 / 1.02 / 1.02 | 1.00 / 1.00 / 1.00 / 1.00 / 1.00 |
| 1W | objective:Credit/var · low_vol | 132 | 1.03 / 1.03 / 1.02 / 1.02 / 1.02 | 1.00 / 1.00 / 1.00 / 1.00 / 1.00 |
| 1W | objective:Crypto/crypto | 111 | 1.29 / 1.24 / 1.17 / 0.74 / 0.51 | 1.20 / 1.15 / 1.10 / 0.60 / 0.50 |
| 1W | objective:Crypto/crypto · high_vol | 39 | 1.22 / 1.17 / 1.10 / 0.64 / 0.50 | 1.10 / 1.05 / 1.00 / 0.50 / 0.50 |
| 1W | objective:Crypto/crypto · low_vol | 72 | 1.35 / 1.30 / 1.21 / 0.83 / 0.50 | 1.40 / 1.35 / 1.25 / 0.90 / 0.50 |
| 1W | objective:Crypto/drawdown | 111 | 1.49 / 1.47 / 1.43 / 1.32 / 1.16 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 |
| 1W | objective:Crypto/drawdown · high_vol | 39 | 1.49 / 1.48 / 1.46 / 1.39 / 1.29 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 |
| 1W | objective:Crypto/drawdown · low_vol | 72 | 1.49 / 1.49 / 1.47 / 1.42 / 0.80 | 1.50 / 1.50 / 1.50 / 1.50 / 0.50 |
| 1W | objective:Crypto/es | 111 | 1.49 / 1.47 / 1.43 / 1.32 / 1.16 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 |
| 1W | objective:Crypto/es · high_vol | 39 | 1.49 / 1.48 / 1.46 / 1.39 / 1.29 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 |
| 1W | objective:Crypto/es · low_vol | 72 | 1.49 / 1.49 / 1.47 / 1.42 / 0.80 | 1.50 / 1.50 / 1.50 / 1.50 / 0.50 |
| 1W | objective:Crypto/min_variance | 111 | 1.36 / 1.31 / 1.24 / 0.96 / 0.51 | 1.30 / 1.25 / 1.20 / 0.95 / 0.50 |
| 1W | objective:Crypto/min_variance · high_vol | 39 | 1.33 / 1.29 / 1.22 / 0.96 / 0.50 | 1.30 / 1.25 / 1.20 / 0.95 / 0.50 |
| 1W | objective:Crypto/min_variance · low_vol | 72 | 1.33 / 1.30 / 1.22 / 0.96 / 0.50 | 1.30 / 1.30 / 1.20 / 0.95 / 0.50 |
| 1W | objective:Crypto/systematic | 111 | 1.29 / 1.24 / 1.17 / 0.67 / 0.51 | 1.20 / 1.15 / 1.10 / 0.50 / 0.50 |
| 1W | objective:Crypto/systematic · high_vol | 39 | 1.22 / 1.17 / 1.10 / 0.60 / 0.50 | 1.10 / 1.05 / 1.00 / 0.50 / 0.50 |
| 1W | objective:Crypto/systematic · low_vol | 72 | 1.32 / 1.30 / 1.21 / 0.77 / 0.50 | 1.35 / 1.35 / 1.25 / 0.85 / 0.50 |
| 1W | objective:Crypto/target_vol | 111 | 1.49 / 1.47 / 1.43 / 1.09 / 0.51 | 1.50 / 1.50 / 1.50 / 1.15 / 0.50 |
| 1W | objective:Crypto/target_vol · high_vol | 39 | 1.49 / 1.48 / 1.46 / 1.15 / 0.50 | 1.50 / 1.50 / 1.50 / 1.25 / 0.50 |
| 1W | objective:Crypto/target_vol · low_vol | 72 | 1.49 / 1.49 / 1.47 / 1.10 / 0.50 | 1.50 / 1.50 / 1.50 / 1.10 / 0.50 |
| 1W | objective:Crypto/var | 111 | 1.49 / 1.47 / 1.43 / 1.32 / 1.16 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 |
| 1W | objective:Crypto/var · high_vol | 39 | 1.49 / 1.48 / 1.46 / 1.39 / 1.29 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 |
| 1W | objective:Crypto/var · low_vol | 72 | 1.49 / 1.49 / 1.47 / 1.42 / 0.80 | 1.50 / 1.50 / 1.50 / 1.50 / 0.50 |
| 1W | objective:Equity/beta | 446 | 1.50 / 1.50 / 1.40 / 0.54 / 0.50 | 1.50 / 1.50 / 1.40 / 0.50 / 0.50 |
| 1W | objective:Equity/beta · high_vol | 212 | 1.50 / 1.50 / 1.32 / 0.51 / 0.50 | 1.50 / 1.50 / 1.30 / 0.50 / 0.50 |
| 1W | objective:Equity/beta · low_vol | 234 | 1.50 / 1.50 / 1.44 / 0.59 / 0.50 | 1.50 / 1.50 / 1.45 / 0.60 / 0.50 |
| 1W | objective:Equity/crash | 446 | 1.50 / 1.50 / 1.49 / 1.07 / 0.50 | 1.50 / 1.50 / 1.50 / 1.10 / 0.50 |
| 1W | objective:Equity/crash · high_vol | 212 | 1.50 / 1.50 / 1.46 / 0.94 / 0.50 | 1.50 / 1.50 / 1.45 / 0.90 / 0.50 |
| 1W | objective:Equity/crash · low_vol | 234 | 1.50 / 1.50 / 1.38 / 1.01 / 0.50 | 1.50 / 1.50 / 1.35 / 1.00 / 0.50 |
| 1W | objective:Equity/drawdown | 446 | 1.50 / 1.50 / 1.49 / 1.42 / 0.50 | 1.50 / 1.50 / 1.50 / 1.50 / 0.50 |
| 1W | objective:Equity/drawdown · high_vol | 212 | 1.50 / 1.50 / 1.50 / 1.33 / 0.50 | 1.50 / 1.50 / 1.50 / 1.30 / 0.50 |
| 1W | objective:Equity/drawdown · low_vol | 234 | 1.50 / 1.50 / 1.50 / 1.48 / 1.10 | 1.50 / 1.50 / 1.50 / 1.50 / 1.25 |
| 1W | objective:Equity/es | 446 | 1.50 / 1.50 / 1.49 / 1.42 / 0.50 | 1.50 / 1.50 / 1.50 / 1.50 / 0.50 |
| 1W | objective:Equity/es · high_vol | 212 | 1.50 / 1.50 / 1.50 / 1.33 / 0.50 | 1.50 / 1.50 / 1.50 / 1.30 / 0.50 |
| 1W | objective:Equity/es · low_vol | 234 | 1.50 / 1.50 / 1.50 / 1.48 / 1.10 | 1.50 / 1.50 / 1.50 / 1.50 / 1.25 |
| 1W | objective:Equity/min_variance | 446 | 1.10 / 1.06 / 0.92 / 0.54 / 0.50 | 1.05 / 1.00 / 0.85 / 0.50 / 0.50 |
| 1W | objective:Equity/min_variance · high_vol | 212 | 1.06 / 0.97 / 0.83 / 0.51 / 0.50 | 1.05 / 0.95 / 0.80 / 0.50 / 0.50 |
| 1W | objective:Equity/min_variance · low_vol | 234 | 1.18 / 1.13 / 1.02 / 0.63 / 0.50 | 1.20 / 1.15 / 1.05 / 0.65 / 0.50 |
| 1W | objective:Equity/name | 446 | 1.50 / 1.50 / 1.49 / 1.20 / 0.50 | 1.50 / 1.50 / 1.50 / 1.25 / 0.50 |
| 1W | objective:Equity/name · high_vol | 212 | 1.50 / 1.50 / 1.50 / 0.66 / 0.50 | 1.50 / 1.50 / 1.50 / 0.50 / 0.50 |
| 1W | objective:Equity/name · low_vol | 234 | 1.50 / 1.50 / 1.50 / 1.44 / 0.54 | 1.50 / 1.50 / 1.50 / 1.50 / 0.55 |
| 1W | objective:Equity/sector | 446 | 1.45 / 1.41 / 1.27 / 0.67 / 0.50 | 1.45 / 1.40 / 1.25 / 0.65 / 0.50 |
| 1W | objective:Equity/sector · high_vol | 212 | 1.49 / 1.44 / 1.25 / 0.54 / 0.50 | 1.50 / 1.45 / 1.25 / 0.50 / 0.50 |
| 1W | objective:Equity/sector · low_vol | 234 | 1.37 / 1.28 / 1.13 / 0.62 / 0.50 | 1.35 / 1.25 / 1.10 / 0.60 / 0.50 |
| 1W | objective:Equity/systematic | 446 | 1.50 / 1.50 / 1.40 / 0.54 / 0.50 | 1.50 / 1.50 / 1.40 / 0.50 / 0.50 |
| 1W | objective:Equity/systematic · high_vol | 212 | 1.50 / 1.50 / 1.36 / 0.51 / 0.50 | 1.50 / 1.50 / 1.35 / 0.50 / 0.50 |
| 1W | objective:Equity/systematic · low_vol | 234 | 1.50 / 1.50 / 1.44 / 0.83 / 0.50 | 1.50 / 1.50 / 1.45 / 0.90 / 0.50 |
| 1W | objective:Equity/target_vol | 446 | 1.50 / 1.45 / 1.18 / 0.54 / 0.50 | 1.50 / 1.45 / 1.15 / 0.50 / 0.50 |
| 1W | objective:Equity/target_vol · high_vol | 212 | 1.46 / 1.33 / 1.00 / 0.51 / 0.50 | 1.45 / 1.30 / 0.95 / 0.50 / 0.50 |
| 1W | objective:Equity/target_vol · low_vol | 234 | 1.50 / 1.49 / 1.44 / 0.87 / 0.50 | 1.50 / 1.50 / 1.50 / 0.95 / 0.50 |
| 1W | objective:Equity/var | 446 | 1.50 / 1.50 / 1.49 / 1.42 / 0.50 | 1.50 / 1.50 / 1.50 / 1.50 / 0.50 |
| 1W | objective:Equity/var · high_vol | 212 | 1.50 / 1.50 / 1.50 / 1.33 / 0.50 | 1.50 / 1.50 / 1.50 / 1.30 / 0.50 |
| 1W | objective:Equity/var · low_vol | 234 | 1.50 / 1.50 / 1.50 / 1.48 / 1.10 | 1.50 / 1.50 / 1.50 / 1.50 / 1.25 |
| 1W | objective:FX/fx | 446 | 1.50 / 1.50 / 1.50 / 1.49 / 1.49 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 |
| 1W | objective:FX/fx · high_vol | 212 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 |
| 1W | objective:FX/fx · low_vol | 234 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 |
| 1W | objective:Other/drawdown | 1 | 1.40 / 1.35 / 1.22 / 0.87 / 0.56 | — / — / — / — / — |
| 1W | objective:Other/drawdown · high_vol | 1 | 1.40 / 1.35 / 1.22 / 0.87 / 0.56 | — / — / — / — / — |
| 1W | objective:Other/es | 1 | 1.40 / 1.35 / 1.22 / 0.87 / 0.56 | — / — / — / — / — |
| 1W | objective:Other/es · high_vol | 1 | 1.40 / 1.35 / 1.22 / 0.87 / 0.56 | — / — / — / — / — |
| 1W | objective:Other/min_variance | 1 | 1.40 / 1.35 / 1.22 / 0.87 / 0.56 | — / — / — / — / — |
| 1W | objective:Other/min_variance · high_vol | 1 | 1.40 / 1.35 / 1.22 / 0.87 / 0.56 | — / — / — / — / — |
| 1W | objective:Other/name | 1 | 1.40 / 1.35 / 1.22 / 0.87 / 0.56 | — / — / — / — / — |
| 1W | objective:Other/name · high_vol | 1 | 1.40 / 1.35 / 1.22 / 0.87 / 0.56 | — / — / — / — / — |
| 1W | objective:Other/target_vol | 1 | 1.40 / 1.35 / 1.22 / 0.87 / 0.56 | — / — / — / — / — |
| 1W | objective:Other/target_vol · high_vol | 1 | 1.40 / 1.35 / 1.22 / 0.87 / 0.56 | — / — / — / — / — |
| 1W | objective:Other/var | 1 | 1.40 / 1.35 / 1.22 / 0.87 / 0.56 | — / — / — / — / — |
| 1W | objective:Other/var · high_vol | 1 | 1.40 / 1.35 / 1.22 / 0.87 / 0.56 | — / — / — / — / — |
| 1W | objective:Rates/curve | 446 | 1.50 / 1.50 / 1.50 / 1.45 / 1.31 | 1.50 / 1.50 / 1.50 / 1.45 / 1.30 |
| 1W | objective:Rates/curve · high_vol | 212 | 1.38 / 1.38 / 1.38 / 1.33 / 1.22 | 1.35 / 1.35 / 1.35 / 1.30 / 1.20 |
| 1W | objective:Rates/curve · low_vol | 234 | 1.50 / 1.50 / 1.50 / 1.49 / 1.46 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 |
| 1W | objective:Rates/drawdown | 446 | 1.50 / 1.50 / 1.50 / 1.49 / 1.49 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 |
| 1W | objective:Rates/drawdown · high_vol | 212 | 1.11 / 1.11 / 1.11 / 1.11 / 1.11 | 1.00 / 1.00 / 1.00 / 1.00 / 1.00 |
| 1W | objective:Rates/drawdown · low_vol | 234 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 |
| 1W | objective:Rates/duration | 446 | 1.41 / 1.41 / 1.36 / 1.36 / 1.31 | 1.40 / 1.40 / 1.35 / 1.35 / 1.30 |
| 1W | objective:Rates/duration · high_vol | 212 | 1.35 / 1.35 / 1.35 / 1.35 / 1.35 | 1.25 / 1.25 / 1.30 / 1.45 / 1.50 |
| 1W | objective:Rates/duration · low_vol | 234 | 1.48 / 1.48 / 1.47 / 1.43 / 1.26 | 1.50 / 1.50 / 1.50 / 1.45 / 1.25 |
| 1W | objective:Rates/es | 446 | 1.50 / 1.50 / 1.50 / 1.49 / 1.49 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 |
| 1W | objective:Rates/es · high_vol | 212 | 1.11 / 1.11 / 1.11 / 1.11 / 1.11 | 1.00 / 1.00 / 1.00 / 1.00 / 1.00 |
| 1W | objective:Rates/es · low_vol | 234 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 |
| 1W | objective:Rates/min_variance | 446 | 1.50 / 1.50 / 1.50 / 1.49 / 1.49 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 |
| 1W | objective:Rates/min_variance · high_vol | 212 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 |
| 1W | objective:Rates/min_variance · low_vol | 234 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 |
| 1W | objective:Rates/name | 446 | 1.50 / 1.50 / 1.50 / 1.49 / 1.49 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 |
| 1W | objective:Rates/name · high_vol | 212 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 |
| 1W | objective:Rates/name · low_vol | 234 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 |
| 1W | objective:Rates/systematic | 446 | 1.50 / 1.50 / 1.50 / 1.49 / 1.13 | 1.50 / 1.50 / 1.50 / 1.50 / 1.10 |
| 1W | objective:Rates/systematic · high_vol | 212 | 1.50 / 1.50 / 1.50 / 1.34 / 0.87 | 1.50 / 1.50 / 1.50 / 1.30 / 0.80 |
| 1W | objective:Rates/systematic · low_vol | 234 | 1.50 / 1.50 / 1.50 / 1.50 / 1.43 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 |
| 1W | objective:Rates/target_vol | 446 | 1.06 / 1.06 / 1.06 / 1.05 / 1.05 | 1.00 / 1.00 / 1.00 / 1.00 / 1.00 |
| 1W | objective:Rates/target_vol · high_vol | 212 | 1.01 / 1.01 / 1.01 / 1.01 / 1.01 | 1.00 / 1.00 / 1.00 / 1.00 / 1.00 |
| 1W | objective:Rates/target_vol · low_vol | 234 | 1.01 / 1.01 / 1.01 / 1.01 / 1.01 | 1.00 / 1.00 / 1.00 / 1.00 / 1.00 |
| 1W | objective:Rates/var | 446 | 1.50 / 1.50 / 1.50 / 1.49 / 1.49 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 |
| 1W | objective:Rates/var · high_vol | 212 | 1.11 / 1.11 / 1.11 / 1.11 / 1.11 | 1.00 / 1.00 / 1.00 / 1.00 / 1.00 |
| 1W | objective:Rates/var · low_vol | 234 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 |
| 1W | objective:Volatility/volatility | 207 | 1.49 / 1.49 / 1.49 / 1.47 / 0.50 | 1.50 / 1.50 / 1.50 / 1.50 / 0.50 |
| 1W | objective:Volatility/volatility · high_vol | 122 | 1.50 / 1.50 / 1.50 / 0.92 / 0.50 | 1.50 / 1.50 / 1.50 / 0.65 / 0.50 |
| 1W | objective:Volatility/volatility · low_vol | 85 | 1.50 / 1.50 / 1.49 / 1.49 / 1.09 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 |
| 1W | risk:Commodity | 446 | 1.49 / 1.48 / 1.33 / 0.54 / 0.51 | 1.50 / 1.50 / 1.35 / 0.50 / 0.50 |
| 1W | risk:Credit | 446 | 1.49 / 1.48 / 1.47 / 1.43 / 1.39 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 |
| 1W | risk:Crypto | 111 | 1.46 / 1.42 / 1.30 / 0.99 / 0.52 | 1.50 / 1.45 / 1.35 / 1.05 / 0.50 |
| 1W | risk:Equity | 446 | 1.49 / 1.48 / 1.42 / 0.85 / 0.51 | 1.50 / 1.50 / 1.45 / 0.85 / 0.50 |
| 1W | risk:FX | 446 | 1.49 / 1.48 / 1.47 / 1.43 / 1.39 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 |
| 1W | risk:Other | 1 | 1.40 / 1.35 / 1.22 / 0.87 / 0.56 | — / — / — / — / — |
| 1W | risk:Rates | 446 | 1.49 / 1.48 / 1.47 / 1.43 / 1.39 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 |
| 1W | risk:Volatility | 207 | 1.48 / 1.47 / 1.44 / 1.36 / 0.51 | 1.50 / 1.50 / 1.50 / 1.50 / 0.50 |
| 1M | global | 212 | 1.39 / 1.35 / 1.27 / 0.96 / 0.61 | 1.50 / 1.45 / 1.35 / 0.95 / 0.50 |
| 1M | objective:Commodity/commodity | 212 | 1.46 / 1.10 / 0.54 / 0.52 / 0.51 | 1.45 / 1.05 / 0.50 / 0.50 / 0.50 |
| 1M | objective:Commodity/commodity · high_vol | 102 | 1.14 / 0.72 / 0.51 / 0.51 / 0.50 | 0.95 / 0.50 / 0.50 / 0.50 / 0.50 |
| 1M | objective:Commodity/commodity · low_vol | 110 | 1.48 / 1.36 / 1.13 / 0.51 / 0.50 | 1.50 / 1.50 / 1.45 / 0.50 / 0.50 |
| 1M | objective:Commodity/drawdown | 130 | 1.49 / 1.43 / 0.55 / 0.53 / 0.51 | 1.50 / 1.50 / 0.50 / 0.50 / 0.50 |
| 1M | objective:Commodity/drawdown · high_vol | 64 | 1.50 / 1.47 / 0.53 / 0.52 / 0.50 | 1.50 / 1.50 / 0.50 / 0.50 / 0.50 |
| 1M | objective:Commodity/drawdown · low_vol | 66 | 1.50 / 0.94 / 0.53 / 0.52 / 0.50 | 1.50 / 0.50 / 0.50 / 0.50 / 0.50 |
| 1M | objective:Commodity/es | 130 | 1.49 / 1.43 / 0.55 / 0.53 / 0.51 | 1.50 / 1.50 / 0.50 / 0.50 / 0.50 |
| 1M | objective:Commodity/es · high_vol | 64 | 1.50 / 1.47 / 0.53 / 0.52 / 0.50 | 1.50 / 1.50 / 0.50 / 0.50 / 0.50 |
| 1M | objective:Commodity/es · low_vol | 66 | 1.50 / 0.94 / 0.53 / 0.52 / 0.50 | 1.50 / 0.50 / 0.50 / 0.50 / 0.50 |
| 1M | objective:Commodity/min_variance | 130 | 1.08 / 0.88 / 0.55 / 0.53 / 0.51 | 0.90 / 0.70 / 0.50 / 0.50 / 0.50 |
| 1M | objective:Commodity/min_variance · high_vol | 64 | 0.96 / 0.71 / 0.53 / 0.52 / 0.50 | 0.85 / 0.55 / 0.50 / 0.50 / 0.50 |
| 1M | objective:Commodity/min_variance · low_vol | 66 | 1.01 / 0.89 / 0.71 / 0.57 / 0.50 | 0.95 / 0.90 / 0.85 / 0.60 / 0.50 |
| 1M | objective:Commodity/systematic | 130 | 1.49 / 1.39 / 0.86 / 0.53 / 0.51 | 1.50 / 1.45 / 0.95 / 0.50 / 0.50 |
| 1M | objective:Commodity/systematic · high_vol | 64 | 1.50 / 1.42 / 0.96 / 0.52 / 0.50 | 1.50 / 1.45 / 1.05 / 0.50 / 0.50 |
| 1M | objective:Commodity/systematic · low_vol | 66 | 1.50 / 1.40 / 0.78 / 0.52 / 0.50 | 1.50 / 1.40 / 0.70 / 0.50 / 0.50 |
| 1M | objective:Commodity/target_vol | 89 | 1.28 / 0.81 / 0.57 / 0.54 / 0.51 | 1.15 / 0.50 / 0.50 / 0.50 / 0.50 |
| 1M | objective:Commodity/target_vol · high_vol | 48 | 1.05 / 0.67 / 0.54 / 0.52 / 0.51 | 0.75 / 0.50 / 0.50 / 0.50 / 0.50 |
| 1M | objective:Commodity/target_vol · low_vol | 41 | 1.37 / 1.09 / 0.95 / 0.52 / 0.51 | 1.50 / 1.50 / 1.50 / 0.50 / 0.50 |
| 1M | objective:Commodity/var | 130 | 1.49 / 1.43 / 0.55 / 0.53 / 0.51 | 1.50 / 1.50 / 0.50 / 0.50 / 0.50 |
| 1M | objective:Commodity/var · high_vol | 64 | 1.50 / 1.47 / 0.53 / 0.52 / 0.50 | 1.50 / 1.50 / 0.50 / 0.50 / 0.50 |
| 1M | objective:Commodity/var · low_vol | 66 | 1.50 / 0.94 / 0.53 / 0.52 / 0.50 | 1.50 / 0.50 / 0.50 / 0.50 / 0.50 |
| 1M | objective:Credit/credit | 212 | 1.49 / 1.41 / 1.29 / 0.93 / 0.68 | 1.50 / 1.40 / 1.25 / 0.80 / 0.50 |
| 1M | objective:Credit/credit · high_vol | 102 | 1.44 / 1.31 / 1.05 / 0.66 / 0.57 | 1.40 / 1.25 / 0.90 / 0.50 / 0.50 |
| 1M | objective:Credit/credit · low_vol | 110 | 1.50 / 1.47 / 1.43 / 1.30 / 1.21 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 |
| 1M | objective:Credit/drawdown | 138 | 1.49 / 1.49 / 1.48 / 1.46 / 1.44 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 |
| 1M | objective:Credit/drawdown · high_vol | 78 | 1.50 / 1.50 / 1.49 / 1.48 / 1.47 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 |
| 1M | objective:Credit/drawdown · low_vol | 60 | 1.50 / 1.50 / 1.49 / 1.48 / 1.47 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 |
| 1M | objective:Credit/es | 138 | 1.49 / 1.49 / 1.48 / 1.46 / 1.44 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 |
| 1M | objective:Credit/es · high_vol | 78 | 1.50 / 1.50 / 1.49 / 1.48 / 1.47 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 |
| 1M | objective:Credit/es · low_vol | 60 | 1.50 / 1.50 / 1.49 / 1.48 / 1.47 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 |
| 1M | objective:Credit/min_variance | 138 | 1.49 / 1.49 / 1.48 / 1.46 / 1.44 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 |
| 1M | objective:Credit/min_variance · high_vol | 78 | 1.50 / 1.50 / 1.49 / 1.48 / 1.42 | 1.50 / 1.50 / 1.50 / 1.50 / 1.40 |
| 1M | objective:Credit/min_variance · low_vol | 60 | 1.50 / 1.50 / 1.49 / 1.48 / 1.47 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 |
| 1M | objective:Credit/name | 169 | 1.49 / 1.49 / 1.49 / 1.47 / 0.71 | 1.50 / 1.50 / 1.50 / 1.50 / 0.50 |
| 1M | objective:Credit/name · high_vol | 84 | 1.50 / 1.50 / 1.49 / 1.49 / 0.59 | 1.50 / 1.50 / 1.50 / 1.50 / 0.50 |
| 1M | objective:Credit/name · low_vol | 85 | 1.50 / 1.50 / 1.49 / 1.49 / 1.17 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 |
| 1M | objective:Credit/systematic | 4 | 1.48 / 1.47 / 1.45 / 1.38 / 1.30 | — / — / — / — / — |
| 1M | objective:Credit/systematic · high_vol | 4 | 1.48 / 1.47 / 1.45 / 1.38 / 1.30 | — / — / — / — / — |
| 1M | objective:Credit/target_vol | 36 | 1.37 / 1.37 / 1.37 / 1.37 / 1.37 | 0.50 / 1.50 / 1.50 / 1.50 / 1.50 |
| 1M | objective:Credit/target_vol · high_vol | 31 | 1.41 / 1.41 / 1.41 / 1.41 / 1.41 | 1.40 / 1.50 / 1.50 / 1.50 / 1.50 |
| 1M | objective:Credit/target_vol · low_vol | 5 | 1.34 / 1.34 / 1.34 / 1.34 / 1.34 | 1.00 / 1.00 / 1.00 / 1.00 / 1.00 |
| 1M | objective:Credit/var | 138 | 1.49 / 1.49 / 1.48 / 1.46 / 1.44 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 |
| 1M | objective:Credit/var · high_vol | 78 | 1.50 / 1.50 / 1.49 / 1.48 / 1.47 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 |
| 1M | objective:Credit/var · low_vol | 60 | 1.50 / 1.50 / 1.49 / 1.48 / 1.47 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 |
| 1M | objective:Crypto/crypto | 52 | 1.40 / 1.39 / 1.37 / 1.23 / 1.02 | 1.35 / 1.35 / 1.35 / 1.30 / 1.15 |
| 1M | objective:Crypto/crypto · high_vol | 18 | 1.36 / 1.34 / 1.32 / 1.06 / 0.90 | 1.25 / 1.20 / 1.15 / 0.50 / 0.50 |
| 1M | objective:Crypto/crypto · low_vol | 34 | 1.44 / 1.43 / 1.41 / 1.33 / 1.19 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 |
| 1M | objective:Crypto/drawdown | 52 | 1.47 / 1.46 / 1.43 / 1.32 / 1.18 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 |
| 1M | objective:Crypto/drawdown · high_vol | 18 | 1.36 / 1.35 / 1.33 / 1.25 / 1.14 | 1.00 / 1.00 / 1.00 / 1.00 / 1.00 |
| 1M | objective:Crypto/drawdown · low_vol | 34 | 1.48 / 1.47 / 1.46 / 1.39 / 1.30 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 |
| 1M | objective:Crypto/es | 52 | 1.47 / 1.46 / 1.43 / 1.32 / 1.18 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 |
| 1M | objective:Crypto/es · high_vol | 18 | 1.36 / 1.35 / 1.33 / 1.25 / 1.14 | 1.00 / 1.00 / 1.00 / 1.00 / 1.00 |
| 1M | objective:Crypto/es · low_vol | 34 | 1.48 / 1.47 / 1.46 / 1.39 / 1.30 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 |
| 1M | objective:Crypto/min_variance | 52 | 1.35 / 1.34 / 1.32 / 1.18 / 1.00 | 1.25 / 1.25 / 1.25 / 1.20 / 1.10 |
| 1M | objective:Crypto/min_variance · high_vol | 18 | 1.33 / 1.31 / 1.29 / 1.09 / 0.88 | 1.25 / 1.20 / 1.20 / 0.80 / 0.50 |
| 1M | objective:Crypto/min_variance · low_vol | 34 | 1.33 / 1.33 / 1.31 / 1.24 / 1.12 | 1.30 / 1.30 / 1.30 / 1.35 / 1.35 |
| 1M | objective:Crypto/systematic | 52 | 1.40 / 1.39 / 1.37 / 1.20 / 1.02 | 1.35 / 1.35 / 1.35 / 1.25 / 1.15 |
| 1M | objective:Crypto/systematic · high_vol | 18 | 1.36 / 1.34 / 1.32 / 1.04 / 0.90 | 1.25 / 1.20 / 1.15 / 0.50 / 0.50 |
| 1M | objective:Crypto/systematic · low_vol | 34 | 1.44 / 1.43 / 1.41 / 1.31 / 1.19 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 |
| 1M | objective:Crypto/target_vol | 52 | 1.47 / 1.46 / 1.43 / 1.32 / 1.09 | 1.50 / 1.50 / 1.50 / 1.50 / 1.30 |
| 1M | objective:Crypto/target_vol · high_vol | 18 | 1.48 / 1.47 / 1.44 / 1.26 / 0.95 | 1.50 / 1.50 / 1.45 / 1.05 / 0.50 |
| 1M | objective:Crypto/target_vol · low_vol | 34 | 1.48 / 1.47 / 1.46 / 1.39 / 1.24 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 |
| 1M | objective:Crypto/var | 52 | 1.47 / 1.46 / 1.43 / 1.32 / 1.18 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 |
| 1M | objective:Crypto/var · high_vol | 18 | 1.36 / 1.35 / 1.33 / 1.25 / 1.14 | 1.00 / 1.00 / 1.00 / 1.00 / 1.00 |
| 1M | objective:Crypto/var · low_vol | 34 | 1.48 / 1.47 / 1.46 / 1.39 / 1.30 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 |
| 1M | objective:Equity/beta | 212 | 1.49 / 1.49 / 1.25 / 0.52 / 0.51 | 1.50 / 1.50 / 1.25 / 0.50 / 0.50 |
| 1M | objective:Equity/beta · high_vol | 102 | 1.50 / 1.50 / 1.28 / 0.51 / 0.50 | 1.50 / 1.50 / 1.30 / 0.50 / 0.50 |
| 1M | objective:Equity/beta · low_vol | 110 | 1.50 / 1.43 / 0.93 / 0.51 / 0.50 | 1.50 / 1.40 / 0.75 / 0.50 / 0.50 |
| 1M | objective:Equity/crash | 212 | 1.49 / 1.30 / 0.86 / 0.52 / 0.51 | 1.50 / 1.25 / 0.75 / 0.50 / 0.50 |
| 1M | objective:Equity/crash · high_vol | 102 | 1.50 / 1.43 / 1.07 / 0.51 / 0.50 | 1.50 / 1.50 / 1.20 / 0.50 / 0.50 |
| 1M | objective:Equity/crash · low_vol | 110 | 1.14 / 0.98 / 0.63 / 0.51 / 0.50 | 0.95 / 0.80 / 0.50 / 0.50 / 0.50 |
| 1M | objective:Equity/drawdown | 212 | 1.49 / 1.49 / 1.45 / 1.03 / 0.51 | 1.50 / 1.50 / 1.50 / 1.15 / 0.50 |
| 1M | objective:Equity/drawdown · high_vol | 102 | 1.50 / 1.50 / 1.48 / 1.14 / 0.50 | 1.50 / 1.50 / 1.50 / 1.20 / 0.50 |
| 1M | objective:Equity/drawdown · low_vol | 110 | 1.50 / 1.50 / 1.48 / 0.91 / 0.50 | 1.50 / 1.50 / 1.50 / 0.85 / 0.50 |
| 1M | objective:Equity/es | 212 | 1.49 / 1.49 / 1.45 / 1.03 / 0.51 | 1.50 / 1.50 / 1.50 / 1.15 / 0.50 |
| 1M | objective:Equity/es · high_vol | 102 | 1.50 / 1.50 / 1.48 / 1.14 / 0.50 | 1.50 / 1.50 / 1.50 / 1.20 / 0.50 |
| 1M | objective:Equity/es · low_vol | 110 | 1.50 / 1.50 / 1.48 / 0.91 / 0.50 | 1.50 / 1.50 / 1.50 / 0.85 / 0.50 |
| 1M | objective:Equity/min_variance | 212 | 1.10 / 1.06 / 0.90 / 0.52 / 0.51 | 1.00 / 0.95 / 0.80 / 0.50 / 0.50 |
| 1M | objective:Equity/min_variance · high_vol | 102 | 1.04 / 0.99 / 0.84 / 0.51 / 0.50 | 1.00 / 0.95 / 0.80 / 0.50 / 0.50 |
| 1M | objective:Equity/min_variance · low_vol | 110 | 1.07 / 0.99 / 0.74 / 0.51 / 0.50 | 1.05 / 0.95 / 0.65 / 0.50 / 0.50 |
| 1M | objective:Equity/name | 212 | 1.49 / 1.49 / 1.45 / 0.52 / 0.51 | 1.50 / 1.50 / 1.50 / 0.50 / 0.50 |
| 1M | objective:Equity/name · high_vol | 102 | 1.50 / 1.50 / 1.48 / 0.51 / 0.50 | 1.50 / 1.50 / 1.50 / 0.50 / 0.50 |
| 1M | objective:Equity/name · low_vol | 110 | 1.50 / 1.50 / 1.48 / 0.54 / 0.50 | 1.50 / 1.50 / 1.50 / 0.55 / 0.50 |
| 1M | objective:Equity/sector | 212 | 1.49 / 1.45 / 1.29 / 0.68 / 0.51 | 1.50 / 1.45 / 1.30 / 0.70 / 0.50 |
| 1M | objective:Equity/sector · high_vol | 102 | 1.50 / 1.45 / 1.33 / 0.75 / 0.50 | 1.50 / 1.45 / 1.35 / 0.80 / 0.50 |
| 1M | objective:Equity/sector · low_vol | 110 | 1.43 / 1.35 / 1.13 / 0.56 / 0.50 | 1.40 / 1.30 / 1.05 / 0.50 / 0.50 |
| 1M | objective:Equity/systematic | 212 | 1.49 / 1.49 / 1.37 / 0.52 / 0.51 | 1.50 / 1.50 / 1.40 / 0.50 / 0.50 |
| 1M | objective:Equity/systematic · high_vol | 102 | 1.50 / 1.50 / 1.39 / 0.51 / 0.50 | 1.50 / 1.50 / 1.40 / 0.50 / 0.50 |
| 1M | objective:Equity/systematic · low_vol | 110 | 1.50 / 1.50 / 1.26 / 0.51 / 0.50 | 1.50 / 1.50 / 1.20 / 0.50 / 0.50 |
| 1M | objective:Equity/target_vol | 212 | 1.46 / 1.38 / 1.13 / 0.52 / 0.51 | 1.45 / 1.35 / 1.10 / 0.50 / 0.50 |
| 1M | objective:Equity/target_vol · high_vol | 102 | 1.39 / 1.33 / 1.08 / 0.51 / 0.50 | 1.35 / 1.30 / 1.05 / 0.50 / 0.50 |
| 1M | objective:Equity/target_vol · low_vol | 110 | 1.48 / 1.46 / 1.31 / 0.51 / 0.50 | 1.50 / 1.50 / 1.40 / 0.50 / 0.50 |
| 1M | objective:Equity/var | 212 | 1.49 / 1.49 / 1.45 / 1.03 / 0.51 | 1.50 / 1.50 / 1.50 / 1.15 / 0.50 |
| 1M | objective:Equity/var · high_vol | 102 | 1.50 / 1.50 / 1.48 / 1.14 / 0.50 | 1.50 / 1.50 / 1.50 / 1.20 / 0.50 |
| 1M | objective:Equity/var · low_vol | 110 | 1.50 / 1.50 / 1.48 / 0.91 / 0.50 | 1.50 / 1.50 / 1.50 / 0.85 / 0.50 |
| 1M | objective:FX/fx | 212 | 1.49 / 1.49 / 1.49 / 1.47 / 1.46 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 |
| 1M | objective:FX/fx · high_vol | 102 | 1.50 / 1.50 / 1.50 / 1.49 / 1.48 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 |
| 1M | objective:FX/fx · low_vol | 110 | 1.50 / 1.50 / 1.50 / 1.49 / 1.48 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 |
| 1M | objective:Other/drawdown | 1 | 1.39 / 1.35 / 1.27 / 0.96 / 0.61 | — / — / — / — / — |
| 1M | objective:Other/drawdown · low_vol | 1 | 1.39 / 1.35 / 1.27 / 0.96 / 0.61 | — / — / — / — / — |
| 1M | objective:Other/es | 1 | 1.39 / 1.35 / 1.27 / 0.96 / 0.61 | — / — / — / — / — |
| 1M | objective:Other/es · low_vol | 1 | 1.39 / 1.35 / 1.27 / 0.96 / 0.61 | — / — / — / — / — |
| 1M | objective:Other/min_variance | 1 | 1.39 / 1.35 / 1.27 / 0.96 / 0.61 | — / — / — / — / — |
| 1M | objective:Other/min_variance · low_vol | 1 | 1.39 / 1.35 / 1.27 / 0.96 / 0.61 | — / — / — / — / — |
| 1M | objective:Other/name | 1 | 1.39 / 1.35 / 1.27 / 0.96 / 0.61 | — / — / — / — / — |
| 1M | objective:Other/name · low_vol | 1 | 1.39 / 1.35 / 1.27 / 0.96 / 0.61 | — / — / — / — / — |
| 1M | objective:Other/target_vol | 1 | 1.39 / 1.35 / 1.27 / 0.96 / 0.61 | — / — / — / — / — |
| 1M | objective:Other/target_vol · low_vol | 1 | 1.39 / 1.35 / 1.27 / 0.96 / 0.61 | — / — / — / — / — |
| 1M | objective:Other/var | 1 | 1.39 / 1.35 / 1.27 / 0.96 / 0.61 | — / — / — / — / — |
| 1M | objective:Other/var · low_vol | 1 | 1.39 / 1.35 / 1.27 / 0.96 / 0.61 | — / — / — / — / — |
| 1M | objective:Rates/curve | 212 | 1.49 / 1.49 / 1.49 / 1.16 / 0.68 | 1.50 / 1.50 / 1.50 / 1.10 / 0.50 |
| 1M | objective:Rates/curve · high_vol | 102 | 1.50 / 1.50 / 1.50 / 1.34 / 0.85 | 1.50 / 1.50 / 1.50 / 1.45 / 0.95 |
| 1M | objective:Rates/curve · low_vol | 110 | 1.50 / 1.47 / 1.37 / 0.90 / 0.56 | 1.50 / 1.45 / 1.30 / 0.75 / 0.50 |
| 1M | objective:Rates/drawdown | 212 | 1.49 / 1.49 / 1.49 / 1.47 / 1.46 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 |
| 1M | objective:Rates/drawdown · high_vol | 102 | 1.50 / 1.50 / 1.50 / 1.49 / 1.48 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 |
| 1M | objective:Rates/drawdown · low_vol | 110 | 1.50 / 1.50 / 1.50 / 1.49 / 1.48 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 |
| 1M | objective:Rates/duration | 212 | 1.49 / 1.49 / 1.37 / 1.05 / 0.68 | 1.50 / 1.50 / 1.35 / 0.95 / 0.50 |
| 1M | objective:Rates/duration · high_vol | 102 | 1.50 / 1.50 / 1.45 / 1.14 / 0.72 | 1.50 / 1.50 / 1.50 / 1.20 / 0.75 |
| 1M | objective:Rates/duration · low_vol | 110 | 1.47 / 1.43 / 1.29 / 0.85 / 0.56 | 1.45 / 1.40 / 1.25 / 0.75 / 0.50 |
| 1M | objective:Rates/es | 212 | 1.49 / 1.49 / 1.49 / 1.47 / 1.46 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 |
| 1M | objective:Rates/es · high_vol | 102 | 1.50 / 1.50 / 1.50 / 1.49 / 1.48 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 |
| 1M | objective:Rates/es · low_vol | 110 | 1.50 / 1.50 / 1.50 / 1.49 / 1.48 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 |
| 1M | objective:Rates/min_variance | 212 | 1.34 / 1.34 / 1.34 / 1.34 / 1.34 | 1.25 / 1.30 / 1.30 / 1.35 / 1.40 |
| 1M | objective:Rates/min_variance · high_vol | 102 | 1.37 / 1.37 / 1.37 / 1.37 / 1.37 | 1.25 / 1.30 / 1.35 / 1.50 / 1.50 |
| 1M | objective:Rates/min_variance · low_vol | 110 | 1.31 / 1.31 / 1.28 / 1.25 / 1.15 | 1.30 / 1.30 / 1.25 / 1.20 / 1.05 |
| 1M | objective:Rates/name | 212 | 1.49 / 1.49 / 1.49 / 1.47 / 1.46 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 |
| 1M | objective:Rates/name · high_vol | 102 | 1.50 / 1.50 / 1.50 / 1.49 / 1.48 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 |
| 1M | objective:Rates/name · low_vol | 110 | 1.50 / 1.50 / 1.50 / 1.49 / 1.48 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 |
| 1M | objective:Rates/systematic | 212 | 1.49 / 1.49 / 1.49 / 1.47 / 0.95 | 1.50 / 1.50 / 1.50 / 1.50 / 0.85 |
| 1M | objective:Rates/systematic · high_vol | 102 | 1.50 / 1.50 / 1.50 / 1.49 / 0.98 | 1.50 / 1.50 / 1.50 / 1.50 / 1.00 |
| 1M | objective:Rates/systematic · low_vol | 110 | 1.50 / 1.50 / 1.50 / 1.43 / 0.79 | 1.50 / 1.50 / 1.50 / 1.40 / 0.70 |
| 1M | objective:Rates/target_vol | 212 | 1.10 / 1.10 / 1.10 / 1.08 / 1.07 | 1.00 / 1.00 / 1.00 / 1.00 / 1.00 |
| 1M | objective:Rates/target_vol · high_vol | 102 | 1.04 / 1.04 / 1.04 / 1.03 / 1.02 | 1.00 / 1.00 / 1.00 / 1.00 / 1.00 |
| 1M | objective:Rates/target_vol · low_vol | 110 | 1.04 / 1.04 / 1.04 / 1.03 / 1.02 | 1.00 / 1.00 / 1.00 / 1.00 / 1.00 |
| 1M | objective:Rates/var | 212 | 1.49 / 1.49 / 1.49 / 1.47 / 1.46 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 |
| 1M | objective:Rates/var · high_vol | 102 | 1.50 / 1.50 / 1.50 / 1.49 / 1.48 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 |
| 1M | objective:Rates/var · low_vol | 110 | 1.50 / 1.50 / 1.50 / 1.49 / 1.48 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 |
| 1M | objective:Volatility/volatility | 98 | 1.48 / 1.48 / 0.61 / 0.57 / 0.52 | 1.50 / 1.50 / 0.50 / 0.50 / 0.50 |
| 1M | objective:Volatility/volatility · high_vol | 58 | 1.49 / 1.49 / 0.56 / 0.53 / 0.51 | 1.50 / 1.50 / 0.50 / 0.50 / 0.50 |
| 1M | objective:Volatility/volatility · low_vol | 40 | 1.49 / 1.41 / 0.57 / 0.54 / 0.51 | 1.50 / 1.30 / 0.50 / 0.50 / 0.50 |
| 1M | risk:Commodity | 212 | 1.48 / 1.27 / 0.67 / 0.60 / 0.52 | 1.50 / 1.25 / 0.50 / 0.50 / 0.50 |
| 1M | risk:Credit | 212 | 1.48 / 1.47 / 1.45 / 1.38 / 1.30 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 |
| 1M | risk:Crypto | 52 | 1.44 / 1.42 / 1.38 / 1.16 / 0.91 | 1.50 / 1.50 / 1.50 / 1.40 / 1.25 |
| 1M | risk:Equity | 212 | 1.48 / 1.47 / 1.26 / 0.60 / 0.52 | 1.50 / 1.50 / 1.25 / 0.50 / 0.50 |
| 1M | risk:FX | 212 | 1.48 / 1.47 / 1.45 / 1.38 / 1.30 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 |
| 1M | risk:Other | 1 | 1.39 / 1.35 / 1.27 / 0.96 / 0.61 | — / — / — / — / — |
| 1M | risk:Rates | 212 | 1.48 / 1.47 / 1.45 / 1.38 / 1.30 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 |
| 1M | risk:Volatility | 98 | 1.46 / 1.44 / 0.79 / 0.68 / 0.54 | 1.50 / 1.50 / 0.50 / 0.50 / 0.50 |
| 3M | global | 70 | 1.22 / 1.16 / 1.03 / 0.73 / 0.73 | 1.40 / 1.30 / 1.05 / 0.50 / 0.50 |
| 3M | objective:Commodity/commodity | 70 | 1.12 / 0.89 / 0.67 / 0.55 / 0.55 | 0.90 / 0.50 / 0.50 / 0.50 / 0.50 |
| 3M | objective:Commodity/commodity · high_vol | 33 | 0.90 / 0.75 / 0.61 / 0.53 / 0.53 | 0.50 / 0.50 / 0.50 / 0.50 / 0.50 |
| 3M | objective:Commodity/commodity · low_vol | 37 | 1.26 / 1.12 / 0.99 / 0.91 / 0.91 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 |
| 3M | objective:Commodity/drawdown | 41 | 1.42 / 1.41 / 1.13 / 0.56 / 0.56 | 1.50 / 1.50 / 1.50 / 0.50 / 0.50 |
| 3M | objective:Commodity/drawdown · high_vol | 20 | 1.44 / 1.18 / 0.97 / 0.55 / 0.55 | 1.50 / 0.50 / 0.50 / 0.50 / 0.50 |
| 3M | objective:Commodity/drawdown · low_vol | 21 | 1.44 / 1.43 / 1.23 / 0.81 / 0.81 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 |
| 3M | objective:Commodity/es | 41 | 1.42 / 1.41 / 1.13 / 0.56 / 0.56 | 1.50 / 1.50 / 1.50 / 0.50 / 0.50 |
| 3M | objective:Commodity/es · high_vol | 20 | 1.44 / 1.18 / 0.97 / 0.55 / 0.55 | 1.50 / 0.50 / 0.50 / 0.50 / 0.50 |
| 3M | objective:Commodity/es · low_vol | 21 | 1.44 / 1.43 / 1.23 / 0.81 / 0.81 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 |
| 3M | objective:Commodity/min_variance | 41 | 1.28 / 1.18 / 0.72 / 0.56 / 0.56 | 1.15 / 0.95 / 0.50 / 0.50 / 0.50 |
| 3M | objective:Commodity/min_variance · high_vol | 20 | 1.20 / 1.06 / 0.67 / 0.55 / 0.55 | 0.95 / 0.70 / 0.50 / 0.50 / 0.50 |
| 3M | objective:Commodity/min_variance · low_vol | 21 | 1.34 / 1.27 / 0.93 / 0.72 / 0.55 | 1.50 / 1.50 / 1.50 / 1.15 / 0.50 |
| 3M | objective:Commodity/systematic | 41 | 1.42 / 1.41 / 1.03 / 0.56 / 0.56 | 1.50 / 1.50 / 1.25 / 0.50 / 0.50 |
| 3M | objective:Commodity/systematic · high_vol | 20 | 1.44 / 1.41 / 1.03 / 0.55 / 0.55 | 1.50 / 1.40 / 1.05 / 0.50 / 0.50 |
| 3M | objective:Commodity/systematic · low_vol | 21 | 1.44 / 1.43 / 1.15 / 0.65 / 0.55 | 1.50 / 1.50 / 1.50 / 0.90 / 0.50 |
| 3M | objective:Commodity/target_vol | 27 | 1.13 / 1.08 / 0.76 / 0.57 / 0.57 | 0.60 / 0.50 / 0.50 / 0.50 / 0.50 |
| 3M | objective:Commodity/target_vol · high_vol | 16 | 1.11 / 0.96 / 0.71 / 0.56 / 0.56 | 1.05 / 0.50 / 0.50 / 0.50 / 0.50 |
| 3M | objective:Commodity/target_vol · low_vol | 11 | 1.03 / 0.99 / 0.72 / 0.56 / 0.56 | 0.50 / 0.50 / 0.50 / 0.50 / 0.50 |
| 3M | objective:Commodity/var | 41 | 1.42 / 1.41 / 1.13 / 0.56 / 0.56 | 1.50 / 1.50 / 1.50 / 0.50 / 0.50 |
| 3M | objective:Commodity/var · high_vol | 20 | 1.44 / 1.18 / 0.97 / 0.55 / 0.55 | 1.50 / 0.50 / 0.50 / 0.50 / 0.50 |
| 3M | objective:Commodity/var · low_vol | 21 | 1.44 / 1.43 / 1.23 / 0.81 / 0.81 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 |
| 3M | objective:Credit/credit | 70 | 1.44 / 1.43 / 1.40 / 1.34 / 1.34 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 |
| 3M | objective:Credit/credit · high_vol | 33 | 1.32 / 1.19 / 1.08 / 1.04 / 1.04 | 1.10 / 0.75 / 0.50 / 0.50 / 0.50 |
| 3M | objective:Credit/credit · low_vol | 37 | 1.46 / 1.46 / 1.44 / 1.40 / 1.40 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 |
| 3M | objective:Credit/drawdown | 47 | 1.43 / 1.41 / 1.38 / 1.30 / 1.30 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 |
| 3M | objective:Credit/drawdown · high_vol | 25 | 1.45 / 1.44 / 1.41 / 1.36 / 1.36 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 |
| 3M | objective:Credit/drawdown · low_vol | 22 | 1.45 / 1.44 / 1.41 / 1.35 / 1.35 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 |
| 3M | objective:Credit/es | 47 | 1.43 / 1.41 / 1.38 / 1.30 / 1.30 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 |
| 3M | objective:Credit/es · high_vol | 25 | 1.45 / 1.44 / 1.41 / 1.36 / 1.36 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 |
| 3M | objective:Credit/es · low_vol | 22 | 1.45 / 1.44 / 1.41 / 1.35 / 1.35 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 |
| 3M | objective:Credit/min_variance | 47 | 1.43 / 1.41 / 1.38 / 1.30 / 1.30 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 |
| 3M | objective:Credit/min_variance · high_vol | 25 | 1.45 / 1.44 / 1.41 / 1.36 / 1.36 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 |
| 3M | objective:Credit/min_variance · low_vol | 22 | 1.36 / 1.36 / 1.36 / 1.35 / 1.35 | 0.95 / 1.25 / 1.50 / 1.50 / 1.50 |
| 3M | objective:Credit/name | 60 | 1.43 / 1.42 / 1.39 / 1.32 / 0.82 | 1.50 / 1.50 / 1.50 / 1.50 / 0.50 |
| 3M | objective:Credit/name · high_vol | 29 | 1.46 / 1.45 / 1.43 / 1.05 / 0.72 | 1.50 / 1.50 / 1.50 / 0.50 / 0.50 |
| 3M | objective:Credit/name · low_vol | 31 | 1.46 / 1.45 / 1.43 / 1.38 / 1.05 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 |
| 3M | objective:Credit/systematic | 1 | 1.37 / 1.34 / 1.28 / 1.14 / 1.14 | — / — / — / — / — |
| 3M | objective:Credit/systematic · high_vol | 1 | 1.37 / 1.34 / 1.28 / 1.14 / 1.14 | — / — / — / — / — |
| 3M | objective:Credit/target_vol | 13 | 1.24 / 1.24 / 1.24 / 1.21 / 1.21 | 0.50 / 0.50 / 1.50 / 1.50 / 1.50 |
| 3M | objective:Credit/target_vol · high_vol | 11 | 1.21 / 1.21 / 1.21 / 1.21 / 1.21 | 0.50 / 0.50 / 1.50 / 1.50 / 1.50 |
| 3M | objective:Credit/target_vol · low_vol | 2 | 1.24 / 1.24 / 1.24 / 1.21 / 1.21 | — / — / — / — / — |
| 3M | objective:Credit/var | 47 | 1.43 / 1.41 / 1.38 / 1.30 / 1.30 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 |
| 3M | objective:Credit/var · high_vol | 25 | 1.45 / 1.44 / 1.41 / 1.36 / 1.36 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 |
| 3M | objective:Credit/var · low_vol | 22 | 1.45 / 1.44 / 1.41 / 1.35 / 1.35 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 |
| 3M | objective:Crypto/crypto | 16 | 1.28 / 1.24 / 1.13 / 0.79 / 0.64 | 1.40 / 1.40 / 1.30 / 0.90 / 0.50 |
| 3M | objective:Crypto/crypto · high_vol | 7 | 1.29 / 1.24 / 1.09 / 0.76 / 0.63 | 1.30 / 1.20 / 0.75 / 0.50 / 0.50 |
| 3M | objective:Crypto/crypto · low_vol | 9 | 1.31 / 1.28 / 1.18 / 0.88 / 0.76 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 |
| 3M | objective:Crypto/drawdown | 16 | 1.20 / 1.16 / 1.07 / 0.81 / 0.75 | 1.00 / 1.00 / 1.00 / 1.00 / 1.00 |
| 3M | objective:Crypto/drawdown · high_vol | 7 | 1.18 / 1.14 / 1.06 / 0.83 / 0.78 | 1.00 / 1.00 / 1.00 / 1.00 / 1.00 |
| 3M | objective:Crypto/drawdown · low_vol | 9 | 1.17 / 1.14 / 1.06 / 0.83 / 0.78 | 1.00 / 1.00 / 1.00 / 1.00 / 1.00 |
| 3M | objective:Crypto/es | 16 | 1.20 / 1.16 / 1.07 / 0.81 / 0.75 | 1.00 / 1.00 / 1.00 / 1.00 / 1.00 |
| 3M | objective:Crypto/es · high_vol | 7 | 1.18 / 1.14 / 1.06 / 0.83 / 0.78 | 1.00 / 1.00 / 1.00 / 1.00 / 1.00 |
| 3M | objective:Crypto/es · low_vol | 9 | 1.17 / 1.14 / 1.06 / 0.83 / 0.78 | 1.00 / 1.00 / 1.00 / 1.00 / 1.00 |
| 3M | objective:Crypto/min_variance | 16 | 1.26 / 1.21 / 1.11 / 0.78 / 0.64 | 1.30 / 1.25 / 1.20 / 0.85 / 0.50 |
| 3M | objective:Crypto/min_variance · high_vol | 7 | 1.27 / 1.22 / 1.09 / 0.75 / 0.63 | 1.35 / 1.25 / 0.90 / 0.50 / 0.50 |
| 3M | objective:Crypto/min_variance · low_vol | 9 | 1.27 / 1.22 / 1.13 / 0.86 / 0.76 | 1.30 / 1.30 / 1.30 / 1.40 / 1.50 |
| 3M | objective:Crypto/systematic | 16 | 1.28 / 1.24 / 1.13 / 0.77 / 0.64 | 1.40 / 1.40 / 1.30 / 0.80 / 0.50 |
| 3M | objective:Crypto/systematic · high_vol | 7 | 1.29 / 1.24 / 1.09 / 0.74 / 0.63 | 1.30 / 1.20 / 0.75 / 0.50 / 0.50 |
| 3M | objective:Crypto/systematic · low_vol | 9 | 1.31 / 1.28 / 1.18 / 0.86 / 0.76 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 |
| 3M | objective:Crypto/target_vol | 16 | 1.31 / 1.26 / 1.17 / 0.80 / 0.64 | 1.50 / 1.50 / 1.50 / 0.95 / 0.50 |
| 3M | objective:Crypto/target_vol · high_vol | 7 | 1.33 / 1.29 / 1.16 / 0.77 / 0.63 | 1.50 / 1.50 / 1.10 / 0.50 / 0.50 |
| 3M | objective:Crypto/target_vol · low_vol | 9 | 1.33 / 1.29 / 1.21 / 0.89 / 0.76 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 |
| 3M | objective:Crypto/var | 16 | 1.20 / 1.16 / 1.07 / 0.81 / 0.75 | 1.00 / 1.00 / 1.00 / 1.00 / 1.00 |
| 3M | objective:Crypto/var · high_vol | 7 | 1.18 / 1.14 / 1.06 / 0.83 / 0.78 | 1.00 / 1.00 / 1.00 / 1.00 / 1.00 |
| 3M | objective:Crypto/var · low_vol | 9 | 1.17 / 1.14 / 1.06 / 0.83 / 0.78 | 1.00 / 1.00 / 1.00 / 1.00 / 1.00 |
| 3M | objective:Equity/beta | 70 | 1.41 / 1.22 / 0.65 / 0.55 / 0.55 | 1.50 / 1.25 / 0.50 / 0.50 / 0.50 |
| 3M | objective:Equity/beta · high_vol | 33 | 1.44 / 0.98 / 0.60 / 0.53 / 0.53 | 1.50 / 0.55 / 0.50 / 0.50 / 0.50 |
| 3M | objective:Equity/beta · low_vol | 37 | 1.45 / 1.33 / 0.88 / 0.53 / 0.53 | 1.50 / 1.50 / 1.25 / 0.50 / 0.50 |
| 3M | objective:Equity/crash | 70 | 0.96 / 0.84 / 0.65 / 0.55 / 0.55 | 0.65 / 0.55 / 0.50 / 0.50 / 0.50 |
| 3M | objective:Equity/crash · high_vol | 33 | 0.81 / 0.72 / 0.60 / 0.53 / 0.53 | 0.55 / 0.50 / 0.50 / 0.50 / 0.50 |
| 3M | objective:Equity/crash · low_vol | 37 | 1.16 / 1.05 / 0.59 / 0.53 / 0.53 | 1.50 / 1.40 / 0.50 / 0.50 / 0.50 |
| 3M | objective:Equity/drawdown | 70 | 1.41 / 1.35 / 1.05 / 0.55 / 0.55 | 1.50 / 1.50 / 1.25 / 0.50 / 0.50 |
| 3M | objective:Equity/drawdown · high_vol | 33 | 1.32 / 1.21 / 0.86 / 0.53 / 0.53 | 1.15 / 0.95 / 0.50 / 0.50 / 0.50 |
| 3M | objective:Equity/drawdown · low_vol | 37 | 1.45 / 1.41 / 1.22 / 0.91 / 0.53 | 1.50 / 1.50 / 1.50 / 1.50 / 0.50 |
| 3M | objective:Equity/es | 70 | 1.41 / 1.35 / 1.05 / 0.55 / 0.55 | 1.50 / 1.50 / 1.25 / 0.50 / 0.50 |
| 3M | objective:Equity/es · high_vol | 33 | 1.32 / 1.21 / 0.86 / 0.53 / 0.53 | 1.15 / 0.95 / 0.50 / 0.50 / 0.50 |
| 3M | objective:Equity/es · low_vol | 37 | 1.45 / 1.41 / 1.22 / 0.91 / 0.53 | 1.50 / 1.50 / 1.50 / 1.50 / 0.50 |
| 3M | objective:Equity/min_variance | 70 | 1.09 / 0.95 / 0.65 / 0.55 / 0.55 | 0.90 / 0.75 / 0.50 / 0.50 / 0.50 |
| 3M | objective:Equity/min_variance · high_vol | 33 | 0.99 / 0.79 / 0.60 / 0.53 / 0.53 | 0.80 / 0.50 / 0.50 / 0.50 / 0.50 |
| 3M | objective:Equity/min_variance · low_vol | 37 | 1.08 / 0.95 / 0.71 / 0.53 / 0.53 | 1.05 / 0.95 / 0.80 / 0.50 / 0.50 |
| 3M | objective:Equity/name | 70 | 1.41 / 1.35 / 1.11 / 0.55 / 0.55 | 1.50 / 1.50 / 1.35 / 0.50 / 0.50 |
| 3M | objective:Equity/name · high_vol | 33 | 1.44 / 1.41 / 0.89 / 0.53 / 0.53 | 1.50 / 1.50 / 0.50 / 0.50 / 0.50 |
| 3M | objective:Equity/name · low_vol | 37 | 1.45 / 1.41 / 1.26 / 0.80 / 0.53 | 1.50 / 1.50 / 1.50 / 1.20 / 0.50 |
| 3M | objective:Equity/sector | 70 | 1.36 / 1.22 / 0.81 / 0.55 / 0.55 | 1.40 / 1.25 / 0.80 / 0.50 / 0.50 |
| 3M | objective:Equity/sector · high_vol | 33 | 1.32 / 1.12 / 0.70 / 0.53 / 0.53 | 1.25 / 0.95 / 0.50 / 0.50 / 0.50 |
| 3M | objective:Equity/sector · low_vol | 37 | 1.41 / 1.33 / 1.02 / 0.61 / 0.53 | 1.50 / 1.50 / 1.35 / 0.70 / 0.50 |
| 3M | objective:Equity/systematic | 70 | 1.41 / 1.25 / 0.76 / 0.55 / 0.55 | 1.50 / 1.30 / 0.70 / 0.50 / 0.50 |
| 3M | objective:Equity/systematic · high_vol | 33 | 1.34 / 1.09 / 0.67 / 0.53 / 0.53 | 1.20 / 0.80 / 0.50 / 0.50 / 0.50 |
| 3M | objective:Equity/systematic · low_vol | 37 | 1.45 / 1.34 / 1.04 / 0.72 / 0.53 | 1.50 / 1.50 / 1.50 / 1.00 / 0.50 |
| 3M | objective:Equity/target_vol | 70 | 1.31 / 1.11 / 0.65 / 0.55 / 0.55 | 1.30 / 1.05 / 0.50 / 0.50 / 0.50 |
| 3M | objective:Equity/target_vol · high_vol | 33 | 1.22 / 0.93 / 0.60 / 0.53 / 0.53 | 1.05 / 0.60 / 0.50 / 0.50 / 0.50 |
| 3M | objective:Equity/target_vol · low_vol | 37 | 1.38 / 1.26 / 0.97 / 0.53 / 0.53 | 1.50 / 1.50 / 1.50 / 0.50 / 0.50 |
| 3M | objective:Equity/var | 70 | 1.41 / 1.35 / 1.05 / 0.55 / 0.55 | 1.50 / 1.50 / 1.25 / 0.50 / 0.50 |
| 3M | objective:Equity/var · high_vol | 33 | 1.32 / 1.21 / 0.86 / 0.53 / 0.53 | 1.15 / 0.95 / 0.50 / 0.50 / 0.50 |
| 3M | objective:Equity/var · low_vol | 37 | 1.45 / 1.41 / 1.22 / 0.91 / 0.53 | 1.50 / 1.50 / 1.50 / 1.50 / 0.50 |
| 3M | objective:FX/fx | 70 | 1.44 / 1.43 / 1.40 / 1.34 / 1.34 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 |
| 3M | objective:FX/fx · high_vol | 33 | 1.46 / 1.45 / 1.43 / 1.39 / 1.39 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 |
| 3M | objective:FX/fx · low_vol | 37 | 1.46 / 1.46 / 1.44 / 1.40 / 1.40 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 |
| 3M | objective:Rates/curve | 70 | 1.44 / 1.40 / 1.25 / 0.80 / 0.72 | 1.50 / 1.45 / 1.25 / 0.60 / 0.50 |
| 3M | objective:Rates/curve · high_vol | 33 | 1.46 / 1.44 / 1.34 / 1.05 / 1.00 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 |
| 3M | objective:Rates/curve · low_vol | 37 | 1.33 / 1.21 / 0.97 / 0.69 / 0.64 | 1.15 / 0.90 / 0.50 / 0.50 / 0.50 |
| 3M | objective:Rates/drawdown | 70 | 1.44 / 1.43 / 1.39 / 1.29 / 1.26 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 |
| 3M | objective:Rates/drawdown · high_vol | 33 | 1.46 / 1.45 / 1.43 / 1.36 / 1.35 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 |
| 3M | objective:Rates/drawdown · low_vol | 37 | 1.46 / 1.46 / 1.43 / 1.08 / 0.97 | 1.50 / 1.50 / 1.50 / 0.75 / 0.50 |
| 3M | objective:Rates/duration | 70 | 1.41 / 1.35 / 1.20 / 0.77 / 0.72 | 1.45 / 1.35 / 1.15 / 0.55 / 0.50 |
| 3M | objective:Rates/duration · high_vol | 33 | 1.44 / 1.40 / 1.31 / 1.03 / 1.00 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 |
| 3M | objective:Rates/duration · low_vol | 37 | 1.29 / 1.14 / 0.93 / 0.67 / 0.64 | 1.10 / 0.80 / 0.50 / 0.50 / 0.50 |
| 3M | objective:Rates/es | 70 | 1.44 / 1.43 / 1.39 / 1.29 / 1.26 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 |
| 3M | objective:Rates/es · high_vol | 33 | 1.46 / 1.45 / 1.43 / 1.36 / 1.35 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 |
| 3M | objective:Rates/es · low_vol | 37 | 1.46 / 1.46 / 1.43 / 1.08 / 0.97 | 1.50 / 1.50 / 1.50 / 0.75 / 0.50 |
| 3M | objective:Rates/min_variance | 70 | 1.28 / 1.27 / 1.23 / 1.10 / 1.05 | 1.20 / 1.20 / 1.20 / 1.15 / 1.10 |
| 3M | objective:Rates/min_variance · high_vol | 33 | 1.34 / 1.34 / 1.32 / 1.24 / 1.21 | 1.40 / 1.50 / 1.50 / 1.50 / 1.50 |
| 3M | objective:Rates/min_variance · low_vol | 37 | 1.21 / 1.18 / 1.12 / 0.87 / 0.84 | 1.10 / 1.05 / 0.95 / 0.50 / 0.50 |
| 3M | objective:Rates/name | 70 | 1.44 / 1.43 / 1.39 / 1.29 / 1.26 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 |
| 3M | objective:Rates/name · high_vol | 33 | 1.46 / 1.45 / 1.43 / 1.36 / 1.35 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 |
| 3M | objective:Rates/name · low_vol | 37 | 1.46 / 1.46 / 1.43 / 0.99 / 0.97 | 1.50 / 1.50 / 1.50 / 0.50 / 0.50 |
| 3M | objective:Rates/systematic | 70 | 1.44 / 1.43 / 1.39 / 0.99 / 0.72 | 1.50 / 1.50 / 1.50 / 0.95 / 0.50 |
| 3M | objective:Rates/systematic · high_vol | 33 | 1.46 / 1.45 / 1.43 / 1.17 / 1.00 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 |
| 3M | objective:Rates/systematic · low_vol | 37 | 1.46 / 1.40 / 1.24 / 0.80 / 0.64 | 1.50 / 1.35 / 1.00 / 0.50 / 0.50 |
| 3M | objective:Rates/target_vol | 70 | 1.44 / 1.43 / 1.39 / 1.29 / 1.26 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 |
| 3M | objective:Rates/target_vol · high_vol | 33 | 1.46 / 1.45 / 1.43 / 1.36 / 1.35 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 |
| 3M | objective:Rates/target_vol · low_vol | 37 | 1.46 / 1.46 / 1.43 / 1.37 / 1.35 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 |
| 3M | objective:Rates/var | 70 | 1.44 / 1.43 / 1.39 / 1.29 / 1.26 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 |
| 3M | objective:Rates/var · high_vol | 33 | 1.46 / 1.45 / 1.43 / 1.36 / 1.35 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 |
| 3M | objective:Rates/var · low_vol | 37 | 1.46 / 1.46 / 1.43 / 1.08 / 0.97 | 1.50 / 1.50 / 1.50 / 0.75 / 0.50 |
| 3M | objective:Volatility/volatility | 31 | 1.38 / 0.79 / 0.73 / 0.60 / 0.60 | 1.50 / 0.50 / 0.50 / 0.50 / 0.50 |
| 3M | objective:Volatility/volatility · high_vol | 20 | 1.18 / 0.72 / 0.67 / 0.58 / 0.58 | 0.60 / 0.50 / 0.50 / 0.50 / 0.50 |
| 3M | objective:Volatility/volatility · low_vol | 11 | 1.40 / 0.90 / 0.85 / 0.58 / 0.58 | 1.50 / 1.50 / 1.50 / 0.50 / 0.50 |
| 3M | risk:Commodity | 70 | 1.37 / 1.34 / 0.88 / 0.61 / 0.61 | 1.50 / 1.50 / 0.75 / 0.50 / 0.50 |
| 3M | risk:Credit | 70 | 1.37 / 1.34 / 1.28 / 1.14 / 1.14 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 |
| 3M | risk:Crypto | 16 | 1.25 / 1.20 / 1.08 / 0.76 / 0.68 | 1.40 / 1.35 / 1.30 / 0.85 / 0.50 |
| 3M | risk:Equity | 70 | 1.31 / 1.18 / 0.82 / 0.61 / 0.61 | 1.40 / 1.20 / 0.65 / 0.50 / 0.50 |
| 3M | risk:FX | 70 | 1.37 / 1.34 / 1.28 / 1.14 / 1.14 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 |
| 3M | risk:Rates | 70 | 1.37 / 1.34 / 1.25 / 1.04 / 0.98 | 1.50 / 1.50 / 1.45 / 1.30 / 1.20 |
| 3M | risk:Volatility | 31 | 1.31 / 0.94 / 0.85 / 0.65 / 0.65 | 1.50 / 0.50 / 0.50 / 0.50 / 0.50 |

## 2. The utility frontier (in-sample, descriptive)

Scaling hedge-2 from 0.5× to 1.5×: risk reduction against profit given up and cost. Marked: the minimum-risk point, the balanced point (best U at λ = 1) and the profit-preserving point (best U at λ = 10).

**1W · Commodity/commodity**

| m | Risk reduction | Profit sacrificed | Cost | Basis | U(λ=1) | U(λ=10) | Point |
|---|---|---|---|---|---|---|---|
| 0.5× | 570 | 160 | 113 | 1,705 | 296 | -1,145 | profit-preserving (λ = 10) |
| 0.6× | 668 | 192 | 136 | 1,650 | 339 | -1,390 |  |
| 0.7× | 760 | 224 | 159 | 1,607 | 377 | -1,640 |  |
| 0.8× | 846 | 256 | 181 | 1,577 | 409 | -1,897 |  |
| 0.9× | 927 | 288 | 204 | 1,561 | 435 | -2,159 |  |
| 1.0× | 1,002 | 320 | 227 | 1,559 | 455 | -2,427 |  |
| 1.1× | 1,072 | 352 | 249 | 1,572 | 470 | -2,700 |  |
| 1.2× | 1,135 | 384 | 272 | 1,599 | 478 | -2,980 |  |
| 1.3× | 1,192 | 416 | 295 | 1,639 | 481 | -3,266 | balanced (λ = 1) |
| 1.4× | 1,243 | 448 | 318 | 1,691 | 477 | -3,557 |  |
| 1.5× | 1,288 | 480 | 340 | 1,755 | 468 | -3,855 | minimum risk |

**1W · Commodity/drawdown**

| m | Risk reduction | Profit sacrificed | Cost | Basis | U(λ=1) | U(λ=10) | Point |
|---|---|---|---|---|---|---|---|
| 0.5× | 1,068 | 189 | 1.95 | 3,930 | 877 | -825 | profit-preserving (λ = 10) |
| 0.6× | 1,229 | 227 | 2.34 | 3,900 | 1,000 | -1,041 |  |
| 0.7× | 1,365 | 265 | 2.73 | 3,873 | 1,097 | -1,285 |  |
| 0.8× | 1,500 | 302 | 3.12 | 3,848 | 1,195 | -1,528 |  |
| 0.9× | 1,636 | 340 | 3.51 | 3,827 | 1,292 | -1,771 |  |
| 1.0× | 1,771 | 378 | 3.9 | 3,809 | 1,389 | -2,014 |  |
| 1.1× | 1,907 | 416 | 4.29 | 3,793 | 1,486 | -2,257 |  |
| 1.2× | 2,042 | 454 | 4.68 | 3,781 | 1,584 | -2,500 |  |
| 1.3× | 2,177 | 492 | 5.07 | 3,772 | 1,681 | -2,743 |  |
| 1.4× | 2,313 | 529 | 5.46 | 3,766 | 1,778 | -2,986 |  |
| 1.5× | 2,448 | 567 | 5.85 | 3,763 | 1,875 | -3,229 | minimum risk · balanced (λ = 1) |

**1W · Commodity/es**

| m | Risk reduction | Profit sacrificed | Cost | Basis | U(λ=1) | U(λ=10) | Point |
|---|---|---|---|---|---|---|---|
| 0.5× | 1,068 | 189 | 1.95 | 3,930 | 877 | -825 | profit-preserving (λ = 10) |
| 0.6× | 1,229 | 227 | 2.34 | 3,900 | 1,000 | -1,041 |  |
| 0.7× | 1,365 | 265 | 2.73 | 3,873 | 1,097 | -1,285 |  |
| 0.8× | 1,500 | 302 | 3.12 | 3,848 | 1,195 | -1,528 |  |
| 0.9× | 1,636 | 340 | 3.51 | 3,827 | 1,292 | -1,771 |  |
| 1.0× | 1,771 | 378 | 3.9 | 3,809 | 1,389 | -2,014 |  |
| 1.1× | 1,907 | 416 | 4.29 | 3,793 | 1,486 | -2,257 |  |
| 1.2× | 2,042 | 454 | 4.68 | 3,781 | 1,584 | -2,500 |  |
| 1.3× | 2,177 | 492 | 5.07 | 3,772 | 1,681 | -2,743 |  |
| 1.4× | 2,313 | 529 | 5.46 | 3,766 | 1,778 | -2,986 |  |
| 1.5× | 2,448 | 567 | 5.85 | 3,763 | 1,875 | -3,229 | minimum risk · balanced (λ = 1) |

**1W · Commodity/min_variance**

| m | Risk reduction | Profit sacrificed | Cost | Basis | U(λ=1) | U(λ=10) | Point |
|---|---|---|---|---|---|---|---|
| 0.5× | 1,492 | 552 | 7.27 | 7,727 | 933 | -4,031 | profit-preserving (λ = 10) |
| 0.6× | 1,742 | 662 | 8.72 | 7,646 | 1,071 | -4,886 |  |
| 0.7× | 1,973 | 772 | 10.2 | 7,572 | 1,191 | -5,760 |  |
| 0.8× | 2,186 | 883 | 11.6 | 7,505 | 1,292 | -6,652 |  |
| 0.9× | 2,379 | 993 | 13.1 | 7,446 | 1,373 | -7,564 |  |
| 1.0× | 2,551 | 1,103 | 14.5 | 7,396 | 1,433 | -8,496 |  |
| 1.1× | 2,703 | 1,214 | 16 | 7,353 | 1,473 | -9,449 |  |
| 1.2× | 2,832 | 1,324 | 17.4 | 7,319 | 1,491 | -10,424 | balanced (λ = 1) |
| 1.3× | 2,940 | 1,434 | 18.9 | 7,294 | 1,487 | -11,421 |  |
| 1.4× | 3,024 | 1,545 | 20.3 | 7,277 | 1,460 | -12,441 |  |
| 1.5× | 3,086 | 1,655 | 21.8 | 7,268 | 1,410 | -13,484 | minimum risk |

**1W · Commodity/systematic**

| m | Risk reduction | Profit sacrificed | Cost | Basis | U(λ=1) | U(λ=10) | Point |
|---|---|---|---|---|---|---|---|
| 0.5× | 2,722 | 541 | 202 | 3,223 | 1,979 | -2,885 | profit-preserving (λ = 10) |
| 0.6× | 3,222 | 649 | 242 | 3,093 | 2,332 | -3,506 |  |
| 0.7× | 3,705 | 757 | 282 | 2,984 | 2,666 | -4,144 |  |
| 0.8× | 4,168 | 865 | 323 | 2,899 | 2,981 | -4,803 |  |
| 0.9× | 4,609 | 973 | 363 | 2,839 | 3,273 | -5,483 |  |
| 1.0× | 5,026 | 1,081 | 403 | 2,808 | 3,542 | -6,187 |  |
| 1.1× | 5,417 | 1,189 | 444 | 2,804 | 3,784 | -6,918 |  |
| 1.2× | 5,778 | 1,297 | 484 | 2,830 | 3,997 | -7,678 |  |
| 1.3× | 6,108 | 1,405 | 524 | 2,883 | 4,179 | -8,469 |  |
| 1.4× | 6,405 | 1,513 | 565 | 2,962 | 4,326 | -9,294 |  |
| 1.5× | 6,664 | 1,622 | 605 | 3,066 | 4,437 | -10,156 | minimum risk · balanced (λ = 1) |

**1W · Commodity/target_vol**

| m | Risk reduction | Profit sacrificed | Cost | Basis | U(λ=1) | U(λ=10) | Point |
|---|---|---|---|---|---|---|---|
| 0.5× | 574 | 329 | 1.27 | 2,505 | 244 | -2,715 | profit-preserving (λ = 10) |
| 0.6× | 679 | 395 | 1.52 | 2,473 | 283 | -3,268 |  |
| 0.7× | 781 | 460 | 1.77 | 2,444 | 319 | -3,824 |  |
| 0.8× | 879 | 526 | 2.03 | 2,420 | 351 | -4,384 |  |
| 0.9× | 973 | 592 | 2.28 | 2,400 | 379 | -4,947 |  |
| 1.0× | 1,064 | 658 | 2.53 | 2,384 | 404 | -5,514 |  |
| 1.1× | 1,151 | 723 | 2.79 | 2,373 | 425 | -6,085 |  |
| 1.2× | 1,234 | 789 | 3.04 | 2,367 | 442 | -6,660 |  |
| 1.3× | 1,314 | 855 | 3.29 | 2,365 | 455 | -7,238 |  |
| 1.4× | 1,389 | 921 | 3.55 | 2,369 | 465 | -7,820 |  |
| 1.5× | 1,461 | 986 | 3.8 | 2,376 | 471 | -8,406 | minimum risk · balanced (λ = 1) |

**1W · Commodity/var**

| m | Risk reduction | Profit sacrificed | Cost | Basis | U(λ=1) | U(λ=10) | Point |
|---|---|---|---|---|---|---|---|
| 0.5× | 1,068 | 189 | 1.95 | 3,930 | 877 | -825 | profit-preserving (λ = 10) |
| 0.6× | 1,229 | 227 | 2.34 | 3,900 | 1,000 | -1,041 |  |
| 0.7× | 1,365 | 265 | 2.73 | 3,873 | 1,097 | -1,285 |  |
| 0.8× | 1,500 | 302 | 3.12 | 3,848 | 1,195 | -1,528 |  |
| 0.9× | 1,636 | 340 | 3.51 | 3,827 | 1,292 | -1,771 |  |
| 1.0× | 1,771 | 378 | 3.9 | 3,809 | 1,389 | -2,014 |  |
| 1.1× | 1,907 | 416 | 4.29 | 3,793 | 1,486 | -2,257 |  |
| 1.2× | 2,042 | 454 | 4.68 | 3,781 | 1,584 | -2,500 |  |
| 1.3× | 2,177 | 492 | 5.07 | 3,772 | 1,681 | -2,743 |  |
| 1.4× | 2,313 | 529 | 5.46 | 3,766 | 1,778 | -2,986 |  |
| 1.5× | 2,448 | 567 | 5.85 | 3,763 | 1,875 | -3,229 | minimum risk · balanced (λ = 1) |

**1W · Credit/credit**

| m | Risk reduction | Profit sacrificed | Cost | Basis | U(λ=1) | U(λ=10) | Point |
|---|---|---|---|---|---|---|---|
| 0.5× | 537 | 34.7 | 73 | 960 | 429 | 117 |  |
| 0.6× | 633 | 41.6 | 87.6 | 1,066 | 504 | 129 |  |
| 0.7× | 725 | 48.5 | 102 | 1,181 | 574 | 138 |  |
| 0.8× | 813 | 55.4 | 117 | 1,303 | 641 | 142 |  |
| 0.9× | 897 | 62.4 | 131 | 1,429 | 704 | 142 | profit-preserving (λ = 10) |
| 1.0× | 978 | 69.3 | 146 | 1,558 | 762 | 139 |  |
| 1.1× | 1,054 | 76.2 | 161 | 1,691 | 817 | 131 |  |
| 1.2× | 1,126 | 83.2 | 175 | 1,825 | 867 | 119 |  |
| 1.3× | 1,193 | 90.1 | 190 | 1,961 | 913 | 102 |  |
| 1.4× | 1,256 | 97 | 204 | 2,099 | 955 | 81.9 |  |
| 1.5× | 1,315 | 104 | 219 | 2,238 | 992 | 56.9 | minimum risk · balanced (λ = 1) |

**1W · Credit/drawdown**

| m | Risk reduction | Profit sacrificed | Cost | Basis | U(λ=1) | U(λ=10) | Point |
|---|---|---|---|---|---|---|---|
| 0.5× | 0 | -0 | 0 | 1,399 | 0 | 0 | minimum risk · balanced (λ = 1) · profit-preserving (λ = 10) |
| 0.6× | 0 | -0 | 0 | 1,399 | 0 | 0 |  |
| 0.7× | 0 | -0 | 0 | 1,399 | 0 | 0 |  |
| 0.8× | 0 | -0 | 0 | 1,399 | 0 | 0 |  |
| 0.9× | 0 | -0 | 0 | 1,399 | 0 | 0 |  |
| 1.0× | 0 | -0 | 0 | 1,399 | 0 | 0 |  |
| 1.1× | 0 | -0 | 0 | 1,399 | 0 | 0 |  |
| 1.2× | 0 | -0 | 0 | 1,399 | 0 | 0 |  |
| 1.3× | 0 | -0 | 0 | 1,399 | 0 | 0 |  |
| 1.4× | 0 | -0 | 0 | 1,399 | 0 | 0 |  |
| 1.5× | 0 | -0 | 0 | 1,399 | 0 | 0 |  |

**1W · Credit/es**

| m | Risk reduction | Profit sacrificed | Cost | Basis | U(λ=1) | U(λ=10) | Point |
|---|---|---|---|---|---|---|---|
| 0.5× | 0 | -0 | 0 | 1,399 | 0 | 0 | minimum risk · balanced (λ = 1) · profit-preserving (λ = 10) |
| 0.6× | 0 | -0 | 0 | 1,399 | 0 | 0 |  |
| 0.7× | 0 | -0 | 0 | 1,399 | 0 | 0 |  |
| 0.8× | 0 | -0 | 0 | 1,399 | 0 | 0 |  |
| 0.9× | 0 | -0 | 0 | 1,399 | 0 | 0 |  |
| 1.0× | 0 | -0 | 0 | 1,399 | 0 | 0 |  |
| 1.1× | 0 | -0 | 0 | 1,399 | 0 | 0 |  |
| 1.2× | 0 | -0 | 0 | 1,399 | 0 | 0 |  |
| 1.3× | 0 | -0 | 0 | 1,399 | 0 | 0 |  |
| 1.4× | 0 | -0 | 0 | 1,399 | 0 | 0 |  |
| 1.5× | 0 | -0 | 0 | 1,399 | 0 | 0 |  |

**1W · Credit/min_variance**

| m | Risk reduction | Profit sacrificed | Cost | Basis | U(λ=1) | U(λ=10) | Point |
|---|---|---|---|---|---|---|---|
| 0.5× | 11.8 | -1.59 | 1.04 | 2,802 | 12.3 | 26.6 |  |
| 0.6× | 12.9 | -1.91 | 1.25 | 2,804 | 13.6 | 30.7 |  |
| 0.7× | 13.7 | -2.22 | 1.45 | 2,806 | 14.4 | 34.4 |  |
| 0.8× | 14 | -2.54 | 1.66 | 2,808 | 14.9 | 37.7 | minimum risk |
| 0.9× | 13.9 | -2.86 | 1.87 | 2,811 | 14.9 | 40.6 | balanced (λ = 1) |
| 1.0× | 13.4 | -3.18 | 2.08 | 2,814 | 14.5 | 43.1 |  |
| 1.1× | 12.5 | -3.5 | 2.28 | 2,818 | 13.8 | 45.2 |  |
| 1.2× | 11.2 | -3.81 | 2.49 | 2,821 | 12.6 | 46.9 |  |
| 1.3× | 9.55 | -4.13 | 2.7 | 2,825 | 11 | 48.2 |  |
| 1.4× | 7.45 | -4.45 | 2.91 | 2,830 | 8.99 | 49 |  |
| 1.5× | 4.94 | -4.77 | 3.11 | 2,834 | 6.59 | 49.5 | profit-preserving (λ = 10) |

**1W · Credit/name**

| m | Risk reduction | Profit sacrificed | Cost | Basis | U(λ=1) | U(λ=10) | Point |
|---|---|---|---|---|---|---|---|
| 0.5× | 1,083 | 90.9 | 74.4 | 794 | 918 | 99.7 |  |
| 0.6× | 1,289 | 109 | 89.3 | 903 | 1,091 | 109 |  |
| 0.7× | 1,491 | 127 | 104 | 1,018 | 1,260 | 114 |  |
| 0.8× | 1,688 | 145 | 119 | 1,138 | 1,424 | 115 | profit-preserving (λ = 10) |
| 0.9× | 1,881 | 164 | 134 | 1,261 | 1,584 | 111 |  |
| 1.0× | 2,069 | 182 | 149 | 1,386 | 1,738 | 102 |  |
| 1.1× | 2,251 | 200 | 164 | 1,513 | 1,887 | 87.1 |  |
| 1.2× | 2,427 | 218 | 179 | 1,641 | 2,031 | 66.8 |  |
| 1.3× | 2,597 | 236 | 193 | 1,770 | 2,168 | 40.3 |  |
| 1.4× | 2,761 | 255 | 208 | 1,900 | 2,298 | 7.2 |  |
| 1.5× | 2,918 | 273 | 223 | 2,031 | 2,422 | -32.9 | minimum risk · balanced (λ = 1) |

**1W · Credit/target_vol**

| m | Risk reduction | Profit sacrificed | Cost | Basis | U(λ=1) | U(λ=10) | Point |
|---|---|---|---|---|---|---|---|
| 0.5× | 0 | -0 | 0 | 1,443 | 0 | 0 | minimum risk · balanced (λ = 1) · profit-preserving (λ = 10) |
| 0.6× | 0 | -0 | 0 | 1,443 | 0 | 0 |  |
| 0.7× | 0 | -0 | 0 | 1,443 | 0 | 0 |  |
| 0.8× | 0 | -0 | 0 | 1,443 | 0 | 0 |  |
| 0.9× | 0 | -0 | 0 | 1,443 | 0 | 0 |  |
| 1.0× | 0 | -0 | 0 | 1,443 | 0 | 0 |  |
| 1.1× | 0 | -0 | 0 | 1,443 | 0 | 0 |  |
| 1.2× | 0 | -0 | 0 | 1,443 | 0 | 0 |  |
| 1.3× | 0 | -0 | 0 | 1,443 | 0 | 0 |  |
| 1.4× | 0 | -0 | 0 | 1,443 | 0 | 0 |  |
| 1.5× | 0 | -0 | 0 | 1,443 | 0 | 0 |  |

**1W · Credit/var**

| m | Risk reduction | Profit sacrificed | Cost | Basis | U(λ=1) | U(λ=10) | Point |
|---|---|---|---|---|---|---|---|
| 0.5× | 0 | -0 | 0 | 1,399 | 0 | 0 | minimum risk · balanced (λ = 1) · profit-preserving (λ = 10) |
| 0.6× | 0 | -0 | 0 | 1,399 | 0 | 0 |  |
| 0.7× | 0 | -0 | 0 | 1,399 | 0 | 0 |  |
| 0.8× | 0 | -0 | 0 | 1,399 | 0 | 0 |  |
| 0.9× | 0 | -0 | 0 | 1,399 | 0 | 0 |  |
| 1.0× | 0 | -0 | 0 | 1,399 | 0 | 0 |  |
| 1.1× | 0 | -0 | 0 | 1,399 | 0 | 0 |  |
| 1.2× | 0 | -0 | 0 | 1,399 | 0 | 0 |  |
| 1.3× | 0 | -0 | 0 | 1,399 | 0 | 0 |  |
| 1.4× | 0 | -0 | 0 | 1,399 | 0 | 0 |  |
| 1.5× | 0 | -0 | 0 | 1,399 | 0 | 0 |  |

**1W · Crypto/crypto**

| m | Risk reduction | Profit sacrificed | Cost | Basis | U(λ=1) | U(λ=10) | Point |
|---|---|---|---|---|---|---|---|
| 0.5× | 26,773 | 4,796 | 549 | 6,793 | 21,428 | -21,732 | profit-preserving (λ = 10) |
| 0.6× | 31,793 | 5,755 | 659 | 6,530 | 25,379 | -26,413 |  |
| 0.7× | 36,596 | 6,714 | 769 | 7,121 | 29,113 | -31,310 |  |
| 0.8× | 41,090 | 7,673 | 879 | 8,386 | 32,538 | -36,517 |  |
| 0.9× | 45,134 | 8,632 | 989 | 10,075 | 35,513 | -42,174 |  |
| 1.0× | 48,520 | 9,591 | 1,099 | 12,010 | 37,830 | -48,490 |  |
| 1.1× | 50,964 | 10,550 | 1,209 | 14,091 | 39,205 | -55,746 |  |
| 1.2× | 52,162 | 11,509 | 1,319 | 16,260 | 39,334 | -64,249 | minimum risk · balanced (λ = 1) |
| 1.3× | 51,915 | 12,468 | 1,429 | 18,488 | 38,018 | -74,197 |  |
| 1.4× | 50,268 | 13,427 | 1,539 | 20,755 | 35,302 | -85,545 |  |
| 1.5× | 47,472 | 14,387 | 1,648 | 23,050 | 31,437 | -98,041 |  |

**1W · Crypto/drawdown**

| m | Risk reduction | Profit sacrificed | Cost | Basis | U(λ=1) | U(λ=10) | Point |
|---|---|---|---|---|---|---|---|
| 0.5× | 24,004 | 1,577 | 134 | 12,093 | 22,293 | 8,105 |  |
| 0.6× | 28,771 | 1,892 | 161 | 11,343 | 26,719 | 9,692 |  |
| 0.7× | 33,167 | 2,207 | 187 | 10,636 | 30,773 | 10,909 |  |
| 0.8× | 37,564 | 2,522 | 214 | 9,980 | 34,827 | 12,125 |  |
| 0.9× | 41,768 | 2,838 | 241 | 9,388 | 38,689 | 13,150 |  |
| 1.0× | 45,494 | 3,153 | 268 | 8,871 | 42,074 | 13,696 |  |
| 1.1× | 49,221 | 3,468 | 294 | 8,444 | 45,458 | 14,243 |  |
| 1.2× | 52,947 | 3,784 | 321 | 8,120 | 48,842 | 14,790 |  |
| 1.3× | 56,674 | 4,099 | 348 | 7,913 | 52,227 | 15,336 |  |
| 1.4× | 60,400 | 4,414 | 375 | 7,831 | 55,611 | 15,883 |  |
| 1.5× | 64,127 | 4,730 | 401 | 7,879 | 58,995 | 16,430 | minimum risk · balanced (λ = 1) · profit-preserving (λ = 10) |

**1W · Crypto/es**

| m | Risk reduction | Profit sacrificed | Cost | Basis | U(λ=1) | U(λ=10) | Point |
|---|---|---|---|---|---|---|---|
| 0.5× | 24,004 | 1,577 | 134 | 12,093 | 22,293 | 8,105 |  |
| 0.6× | 28,771 | 1,892 | 161 | 11,343 | 26,719 | 9,692 |  |
| 0.7× | 33,167 | 2,207 | 187 | 10,636 | 30,773 | 10,909 |  |
| 0.8× | 37,564 | 2,522 | 214 | 9,980 | 34,827 | 12,125 |  |
| 0.9× | 41,768 | 2,838 | 241 | 9,388 | 38,689 | 13,150 |  |
| 1.0× | 45,494 | 3,153 | 268 | 8,871 | 42,074 | 13,696 |  |
| 1.1× | 49,221 | 3,468 | 294 | 8,444 | 45,458 | 14,243 |  |
| 1.2× | 52,947 | 3,784 | 321 | 8,120 | 48,842 | 14,790 |  |
| 1.3× | 56,674 | 4,099 | 348 | 7,913 | 52,227 | 15,336 |  |
| 1.4× | 60,400 | 4,414 | 375 | 7,831 | 55,611 | 15,883 |  |
| 1.5× | 64,127 | 4,730 | 401 | 7,879 | 58,995 | 16,430 | minimum risk · balanced (λ = 1) · profit-preserving (λ = 10) |

**1W · Crypto/min_variance**

| m | Risk reduction | Profit sacrificed | Cost | Basis | U(λ=1) | U(λ=10) | Point |
|---|---|---|---|---|---|---|---|
| 0.5× | 26,735 | 3,957 | 442 | 22,332 | 22,336 | -13,281 | profit-preserving (λ = 10) |
| 0.6× | 31,841 | 4,749 | 530 | 20,518 | 26,562 | -16,178 |  |
| 0.7× | 36,796 | 5,540 | 618 | 18,832 | 30,638 | -19,226 |  |
| 0.8× | 41,541 | 6,332 | 707 | 17,313 | 34,503 | -22,484 |  |
| 0.9× | 45,983 | 7,123 | 795 | 16,008 | 38,065 | -26,045 |  |
| 1.0× | 49,981 | 7,915 | 883 | 14,973 | 41,183 | -30,050 |  |
| 1.1× | 53,324 | 8,706 | 972 | 14,267 | 43,646 | -34,711 |  |
| 1.2× | 55,721 | 9,498 | 1,060 | 13,940 | 45,163 | -40,317 |  |
| 1.3× | 56,858 | 10,289 | 1,148 | 14,019 | 45,420 | -47,183 | minimum risk · balanced (λ = 1) |
| 1.4× | 56,539 | 11,081 | 1,237 | 14,497 | 44,221 | -55,505 |  |
| 1.5× | 54,822 | 11,872 | 1,325 | 15,337 | 41,625 | -65,225 |  |

**1W · Crypto/systematic**

| m | Risk reduction | Profit sacrificed | Cost | Basis | U(λ=1) | U(λ=10) | Point |
|---|---|---|---|---|---|---|---|
| 0.5× | 28,515 | 5,360 | 612 | 7,179 | 22,543 | -25,701 | profit-preserving (λ = 10) |
| 0.6× | 33,900 | 6,432 | 735 | 6,902 | 26,733 | -31,159 |  |
| 0.7× | 39,075 | 7,505 | 857 | 7,527 | 30,713 | -36,828 |  |
| 0.8× | 43,946 | 8,577 | 980 | 8,864 | 34,390 | -42,800 |  |
| 0.9× | 48,366 | 9,649 | 1,102 | 10,649 | 37,616 | -49,222 |  |
| 1.0× | 52,109 | 10,721 | 1,225 | 12,694 | 40,164 | -56,323 |  |
| 1.1× | 54,852 | 11,793 | 1,347 | 14,893 | 41,712 | -64,424 |  |
| 1.2× | 56,223 | 12,865 | 1,469 | 17,186 | 41,888 | -73,896 | minimum risk · balanced (λ = 1) |
| 1.3× | 55,971 | 13,937 | 1,592 | 19,540 | 40,443 | -84,990 |  |
| 1.4× | 54,148 | 15,009 | 1,714 | 21,937 | 37,425 | -97,657 |  |
| 1.5× | 51,063 | 16,081 | 1,837 | 24,362 | 33,145 | -111,585 |  |

**1W · Crypto/target_vol**

| m | Risk reduction | Profit sacrificed | Cost | Basis | U(λ=1) | U(λ=10) | Point |
|---|---|---|---|---|---|---|---|
| 0.5× | 20,787 | 3,061 | 315 | 18,590 | 17,410 | -10,142 | profit-preserving (λ = 10) |
| 0.6× | 24,810 | 3,674 | 378 | 17,169 | 20,759 | -12,304 |  |
| 0.7× | 28,764 | 4,286 | 441 | 15,840 | 24,037 | -14,536 |  |
| 0.8× | 32,630 | 4,898 | 504 | 14,628 | 27,228 | -16,856 |  |
| 0.9× | 36,383 | 5,510 | 567 | 13,565 | 30,306 | -19,288 |  |
| 1.0× | 39,992 | 6,123 | 630 | 12,688 | 33,239 | -21,865 |  |
| 1.1× | 43,410 | 6,735 | 693 | 12,038 | 35,983 | -24,632 |  |
| 1.2× | 46,579 | 7,347 | 755 | 11,653 | 38,476 | -27,650 |  |
| 1.3× | 49,415 | 7,960 | 818 | 11,560 | 40,637 | -30,999 |  |
| 1.4× | 51,813 | 8,572 | 881 | 11,765 | 42,360 | -34,787 |  |
| 1.5× | 53,646 | 9,184 | 944 | 12,253 | 43,518 | -39,139 | minimum risk · balanced (λ = 1) |

**1W · Crypto/var**

| m | Risk reduction | Profit sacrificed | Cost | Basis | U(λ=1) | U(λ=10) | Point |
|---|---|---|---|---|---|---|---|
| 0.5× | 24,004 | 1,577 | 134 | 12,093 | 22,293 | 8,105 |  |
| 0.6× | 28,771 | 1,892 | 161 | 11,343 | 26,719 | 9,692 |  |
| 0.7× | 33,167 | 2,207 | 187 | 10,636 | 30,773 | 10,909 |  |
| 0.8× | 37,564 | 2,522 | 214 | 9,980 | 34,827 | 12,125 |  |
| 0.9× | 41,768 | 2,838 | 241 | 9,388 | 38,689 | 13,150 |  |
| 1.0× | 45,494 | 3,153 | 268 | 8,871 | 42,074 | 13,696 |  |
| 1.1× | 49,221 | 3,468 | 294 | 8,444 | 45,458 | 14,243 |  |
| 1.2× | 52,947 | 3,784 | 321 | 8,120 | 48,842 | 14,790 |  |
| 1.3× | 56,674 | 4,099 | 348 | 7,913 | 52,227 | 15,336 |  |
| 1.4× | 60,400 | 4,414 | 375 | 7,831 | 55,611 | 15,883 |  |
| 1.5× | 64,127 | 4,730 | 401 | 7,879 | 58,995 | 16,430 | minimum risk · balanced (λ = 1) · profit-preserving (λ = 10) |

**1W · Equity/beta**

| m | Risk reduction | Profit sacrificed | Cost | Basis | U(λ=1) | U(λ=10) | Point |
|---|---|---|---|---|---|---|---|
| 0.5× | 4,601 | 1,039 | 21.8 | 2,680 | 3,540 | -5,811 | profit-preserving (λ = 10) |
| 0.6× | 5,439 | 1,247 | 26.2 | 2,284 | 4,166 | -7,056 |  |
| 0.7× | 6,242 | 1,455 | 30.6 | 1,979 | 4,757 | -8,334 |  |
| 0.8× | 7,008 | 1,662 | 34.9 | 1,811 | 5,311 | -9,651 |  |
| 0.9× | 7,732 | 1,870 | 39.3 | 1,819 | 5,822 | -11,010 |  |
| 1.0× | 8,409 | 2,078 | 43.6 | 2,000 | 6,287 | -12,415 |  |
| 1.1× | 9,036 | 2,286 | 48 | 2,314 | 6,702 | -13,870 |  |
| 1.2× | 9,607 | 2,494 | 52.4 | 2,715 | 7,061 | -15,382 |  |
| 1.3× | 10,118 | 2,701 | 56.7 | 3,171 | 7,360 | -16,953 |  |
| 1.4× | 10,564 | 2,909 | 61.1 | 3,661 | 7,593 | -18,590 |  |
| 1.5× | 10,940 | 3,117 | 65.5 | 4,173 | 7,757 | -20,296 | minimum risk · balanced (λ = 1) |

**1W · Equity/crash**

| m | Risk reduction | Profit sacrificed | Cost | Basis | U(λ=1) | U(λ=10) | Point |
|---|---|---|---|---|---|---|---|
| 0.5× | 7,536 | 915 | 355 | 3,388 | 6,265 | -1,969 | profit-preserving (λ = 10) |
| 0.6× | 8,904 | 1,098 | 426 | 3,075 | 7,380 | -2,502 |  |
| 0.7× | 10,217 | 1,281 | 497 | 2,789 | 8,439 | -3,090 |  |
| 0.8× | 11,482 | 1,464 | 568 | 2,542 | 9,450 | -3,726 |  |
| 0.9× | 12,691 | 1,647 | 639 | 2,345 | 10,405 | -4,418 |  |
| 1.0× | 13,824 | 1,830 | 710 | 2,212 | 11,284 | -5,185 |  |
| 1.1× | 14,871 | 2,013 | 781 | 2,154 | 12,077 | -6,039 |  |
| 1.2× | 15,803 | 2,196 | 852 | 2,178 | 12,755 | -7,008 |  |
| 1.3× | 16,580 | 2,379 | 923 | 2,281 | 13,278 | -8,132 |  |
| 1.4× | 17,246 | 2,562 | 994 | 2,453 | 13,690 | -9,367 |  |
| 1.5× | 17,806 | 2,745 | 1,065 | 2,681 | 13,996 | -10,708 | minimum risk · balanced (λ = 1) |

**1W · Equity/drawdown**

| m | Risk reduction | Profit sacrificed | Cost | Basis | U(λ=1) | U(λ=10) | Point |
|---|---|---|---|---|---|---|---|
| 0.5× | 8,425 | 837 | 19.1 | 4,844 | 7,569 | 37.8 | profit-preserving (λ = 10) |
| 0.6× | 9,985 | 1,004 | 22.9 | 4,482 | 8,958 | -78.9 |  |
| 0.7× | 11,487 | 1,171 | 26.7 | 4,147 | 10,289 | -254 |  |
| 0.8× | 12,926 | 1,339 | 30.6 | 3,849 | 11,557 | -493 |  |
| 0.9× | 14,311 | 1,506 | 34.4 | 3,595 | 12,771 | -785 |  |
| 1.0× | 15,637 | 1,674 | 38.2 | 3,396 | 13,925 | -1,137 |  |
| 1.1× | 16,890 | 1,841 | 42 | 3,262 | 15,007 | -1,561 |  |
| 1.2× | 18,085 | 2,008 | 45.8 | 3,201 | 16,031 | -2,044 |  |
| 1.3× | 19,203 | 2,176 | 49.7 | 3,217 | 16,978 | -2,602 |  |
| 1.4× | 20,236 | 2,343 | 53.5 | 3,309 | 17,839 | -3,248 |  |
| 1.5× | 21,162 | 2,510 | 57.3 | 3,471 | 18,594 | -3,999 | minimum risk · balanced (λ = 1) |

**1W · Equity/es**

| m | Risk reduction | Profit sacrificed | Cost | Basis | U(λ=1) | U(λ=10) | Point |
|---|---|---|---|---|---|---|---|
| 0.5× | 8,425 | 837 | 19.1 | 4,844 | 7,569 | 37.8 | profit-preserving (λ = 10) |
| 0.6× | 9,985 | 1,004 | 22.9 | 4,482 | 8,958 | -78.9 |  |
| 0.7× | 11,487 | 1,171 | 26.7 | 4,147 | 10,289 | -254 |  |
| 0.8× | 12,926 | 1,339 | 30.6 | 3,849 | 11,557 | -493 |  |
| 0.9× | 14,311 | 1,506 | 34.4 | 3,595 | 12,771 | -785 |  |
| 1.0× | 15,637 | 1,674 | 38.2 | 3,396 | 13,925 | -1,137 |  |
| 1.1× | 16,890 | 1,841 | 42 | 3,262 | 15,007 | -1,561 |  |
| 1.2× | 18,085 | 2,008 | 45.8 | 3,201 | 16,031 | -2,044 |  |
| 1.3× | 19,203 | 2,176 | 49.7 | 3,217 | 16,978 | -2,602 |  |
| 1.4× | 20,236 | 2,343 | 53.5 | 3,309 | 17,839 | -3,248 |  |
| 1.5× | 21,162 | 2,510 | 57.3 | 3,471 | 18,594 | -3,999 | minimum risk · balanced (λ = 1) |

**1W · Equity/min_variance**

| m | Risk reduction | Profit sacrificed | Cost | Basis | U(λ=1) | U(λ=10) | Point |
|---|---|---|---|---|---|---|---|
| 0.5× | 8,636 | 1,936 | 50.5 | 9,089 | 6,650 | -10,774 | profit-preserving (λ = 10) |
| 0.6× | 10,046 | 2,323 | 60.7 | 8,246 | 7,662 | -13,247 |  |
| 0.7× | 11,291 | 2,710 | 70.8 | 7,467 | 8,510 | -15,884 |  |
| 0.8× | 12,337 | 3,098 | 80.9 | 6,774 | 9,158 | -18,720 |  |
| 0.9× | 13,147 | 3,485 | 91 | 6,197 | 9,572 | -21,791 |  |
| 1.0× | 13,688 | 3,872 | 101 | 5,771 | 9,715 | -25,133 | balanced (λ = 1) |
| 1.1× | 13,933 | 4,259 | 111 | 5,531 | 9,562 | -28,770 | minimum risk |
| 1.2× | 13,868 | 4,646 | 121 | 5,500 | 9,100 | -32,718 |  |
| 1.3× | 13,496 | 5,034 | 131 | 5,682 | 8,331 | -36,971 |  |
| 1.4× | 12,839 | 5,421 | 142 | 6,058 | 7,277 | -41,511 |  |
| 1.5× | 11,925 | 5,808 | 152 | 6,595 | 5,966 | -46,306 |  |

**1W · Equity/name**

| m | Risk reduction | Profit sacrificed | Cost | Basis | U(λ=1) | U(λ=10) | Point |
|---|---|---|---|---|---|---|---|
| 0.5× | 6,922 | 1,033 | 251 | 3,621 | 5,638 | -3,655 | profit-preserving (λ = 10) |
| 0.6× | 8,264 | 1,239 | 301 | 3,170 | 6,725 | -4,428 |  |
| 0.7× | 9,587 | 1,446 | 351 | 2,827 | 7,791 | -5,220 |  |
| 0.8× | 10,886 | 1,652 | 401 | 2,634 | 8,833 | -6,037 |  |
| 0.9× | 12,156 | 1,859 | 451 | 2,624 | 9,846 | -6,882 |  |
| 1.0× | 13,391 | 2,065 | 501 | 2,801 | 10,824 | -7,763 |  |
| 1.1× | 14,583 | 2,272 | 551 | 3,131 | 11,760 | -8,686 |  |
| 1.2× | 15,721 | 2,478 | 601 | 3,573 | 12,642 | -9,663 |  |
| 1.3× | 16,794 | 2,685 | 651 | 4,091 | 13,458 | -10,705 |  |
| 1.4× | 17,786 | 2,891 | 701 | 4,659 | 14,193 | -11,829 |  |
| 1.5× | 18,676 | 3,098 | 752 | 5,262 | 14,826 | -13,055 | minimum risk · balanced (λ = 1) |

**1W · Equity/sector**

| m | Risk reduction | Profit sacrificed | Cost | Basis | U(λ=1) | U(λ=10) | Point |
|---|---|---|---|---|---|---|---|
| 0.5× | 5,589 | 850 | 338 | 3,245 | 4,401 | -3,247 | profit-preserving (λ = 10) |
| 0.6× | 6,571 | 1,020 | 406 | 3,610 | 5,146 | -4,032 |  |
| 0.7× | 7,495 | 1,190 | 474 | 4,097 | 5,832 | -4,876 |  |
| 0.8× | 8,353 | 1,360 | 541 | 4,670 | 6,452 | -5,785 |  |
| 0.9× | 9,137 | 1,530 | 609 | 5,299 | 6,999 | -6,768 |  |
| 1.0× | 9,840 | 1,700 | 677 | 5,968 | 7,464 | -7,832 |  |
| 1.1× | 10,454 | 1,870 | 744 | 6,663 | 7,840 | -8,986 |  |
| 1.2× | 10,969 | 2,040 | 812 | 7,379 | 8,118 | -10,238 |  |
| 1.3× | 11,379 | 2,210 | 880 | 8,109 | 8,290 | -11,595 |  |
| 1.4× | 11,678 | 2,379 | 947 | 8,850 | 8,351 | -13,064 | balanced (λ = 1) |
| 1.5× | 11,859 | 2,549 | 1,015 | 9,599 | 8,295 | -14,650 | minimum risk |

**1W · Equity/systematic**

| m | Risk reduction | Profit sacrificed | Cost | Basis | U(λ=1) | U(λ=10) | Point |
|---|---|---|---|---|---|---|---|
| 0.5× | 6,119 | 1,106 | 178 | 3,632 | 4,834 | -5,123 | profit-preserving (λ = 10) |
| 0.6× | 7,250 | 1,328 | 213 | 3,143 | 5,709 | -6,240 |  |
| 0.7× | 8,340 | 1,549 | 249 | 2,758 | 6,542 | -7,399 |  |
| 0.8× | 9,381 | 1,770 | 285 | 2,526 | 7,326 | -8,607 |  |
| 0.9× | 10,365 | 1,992 | 320 | 2,491 | 8,054 | -9,870 |  |
| 1.0× | 11,285 | 2,213 | 356 | 2,659 | 8,717 | -11,199 |  |
| 1.1× | 12,130 | 2,434 | 391 | 2,997 | 9,305 | -12,602 |  |
| 1.2× | 12,890 | 2,655 | 427 | 3,455 | 9,808 | -14,091 |  |
| 1.3× | 13,552 | 2,877 | 462 | 3,993 | 10,213 | -15,677 |  |
| 1.4× | 14,105 | 3,098 | 498 | 4,581 | 10,509 | -17,372 |  |
| 1.5× | 14,538 | 3,319 | 534 | 5,204 | 10,685 | -19,188 | minimum risk · balanced (λ = 1) |

**1W · Equity/target_vol**

| m | Risk reduction | Profit sacrificed | Cost | Basis | U(λ=1) | U(λ=10) | Point |
|---|---|---|---|---|---|---|---|
| 0.5× | 4,624 | 1,122 | 23 | 5,780 | 3,479 | -6,622 | profit-preserving (λ = 10) |
| 0.6× | 5,435 | 1,347 | 27.6 | 5,271 | 4,061 | -8,061 |  |
| 0.7× | 6,198 | 1,571 | 32.2 | 4,805 | 4,595 | -9,547 |  |
| 0.8× | 6,909 | 1,796 | 36.8 | 4,396 | 5,077 | -11,085 |  |
| 0.9× | 7,563 | 2,020 | 41.4 | 4,059 | 5,502 | -12,681 |  |
| 1.0× | 8,155 | 2,245 | 46 | 3,814 | 5,864 | -14,338 |  |
| 1.1× | 8,680 | 2,469 | 50.6 | 3,681 | 6,160 | -16,063 |  |
| 1.2× | 9,132 | 2,694 | 55.2 | 3,670 | 6,383 | -17,861 |  |
| 1.3× | 9,507 | 2,918 | 59.8 | 3,783 | 6,529 | -19,735 |  |
| 1.4× | 9,800 | 3,143 | 64.4 | 4,010 | 6,593 | -21,691 | balanced (λ = 1) |
| 1.5× | 10,009 | 3,367 | 69 | 4,332 | 6,573 | -23,731 | minimum risk |

**1W · Equity/var**

| m | Risk reduction | Profit sacrificed | Cost | Basis | U(λ=1) | U(λ=10) | Point |
|---|---|---|---|---|---|---|---|
| 0.5× | 8,425 | 837 | 19.1 | 4,844 | 7,569 | 37.8 | profit-preserving (λ = 10) |
| 0.6× | 9,985 | 1,004 | 22.9 | 4,482 | 8,958 | -78.9 |  |
| 0.7× | 11,487 | 1,171 | 26.7 | 4,147 | 10,289 | -254 |  |
| 0.8× | 12,926 | 1,339 | 30.6 | 3,849 | 11,557 | -493 |  |
| 0.9× | 14,311 | 1,506 | 34.4 | 3,595 | 12,771 | -785 |  |
| 1.0× | 15,637 | 1,674 | 38.2 | 3,396 | 13,925 | -1,137 |  |
| 1.1× | 16,890 | 1,841 | 42 | 3,262 | 15,007 | -1,561 |  |
| 1.2× | 18,085 | 2,008 | 45.8 | 3,201 | 16,031 | -2,044 |  |
| 1.3× | 19,203 | 2,176 | 49.7 | 3,217 | 16,978 | -2,602 |  |
| 1.4× | 20,236 | 2,343 | 53.5 | 3,309 | 17,839 | -3,248 |  |
| 1.5× | 21,162 | 2,510 | 57.3 | 3,471 | 18,594 | -3,999 | minimum risk · balanced (λ = 1) |

**1W · FX/fx**

| m | Risk reduction | Profit sacrificed | Cost | Basis | U(λ=1) | U(λ=10) | Point |
|---|---|---|---|---|---|---|---|
| 0.5× | 103 | -19.3 | 4.27 | 309 | 118 | 291 |  |
| 0.6× | 122 | -23.1 | 5.12 | 256 | 140 | 348 |  |
| 0.7× | 141 | -27 | 5.97 | 206 | 162 | 404 |  |
| 0.8× | 159 | -30.8 | 6.83 | 161 | 183 | 460 |  |
| 0.9× | 176 | -34.7 | 7.68 | 128 | 203 | 515 |  |
| 1.0× | 193 | -38.5 | 8.53 | 117 | 223 | 569 |  |
| 1.1× | 209 | -42.4 | 9.39 | 134 | 242 | 623 |  |
| 1.2× | 225 | -46.2 | 10.2 | 171 | 261 | 676 |  |
| 1.3× | 240 | -50.1 | 11.1 | 217 | 279 | 729 |  |
| 1.4× | 254 | -53.9 | 11.9 | 268 | 296 | 782 |  |
| 1.5× | 268 | -57.8 | 12.8 | 322 | 313 | 833 | minimum risk · balanced (λ = 1) · profit-preserving (λ = 10) |

**1W · Rates/curve**

| m | Risk reduction | Profit sacrificed | Cost | Basis | U(λ=1) | U(λ=10) | Point |
|---|---|---|---|---|---|---|---|
| 0.5× | 224 | 5.31 | 18.8 | 1,469 | 200 | 153 |  |
| 0.6× | 261 | 6.37 | 22.5 | 1,511 | 232 | 174 |  |
| 0.7× | 294 | 7.44 | 26.3 | 1,560 | 260 | 193 |  |
| 0.8× | 324 | 8.5 | 30 | 1,615 | 286 | 209 |  |
| 0.9× | 352 | 9.56 | 33.8 | 1,676 | 308 | 222 |  |
| 1.0× | 376 | 10.6 | 37.5 | 1,742 | 328 | 232 |  |
| 1.1× | 397 | 11.7 | 41.3 | 1,812 | 344 | 239 |  |
| 1.2× | 416 | 12.7 | 45.1 | 1,886 | 358 | 243 |  |
| 1.3× | 431 | 13.8 | 48.8 | 1,964 | 368 | 244 | profit-preserving (λ = 10) |
| 1.4× | 443 | 14.9 | 52.6 | 2,045 | 376 | 242 |  |
| 1.5× | 452 | 15.9 | 56.3 | 2,129 | 380 | 237 | minimum risk · balanced (λ = 1) |

**1W · Rates/drawdown**

| m | Risk reduction | Profit sacrificed | Cost | Basis | U(λ=1) | U(λ=10) | Point |
|---|---|---|---|---|---|---|---|
| 0.5× | 0 | -1.02 | 0.0167 | 2,131 | 1 | 10.2 | minimum risk |
| 0.6× | 0 | -1.23 | 0.0201 | 2,131 | 1.21 | 12.2 |  |
| 0.7× | 0 | -1.43 | 0.0234 | 2,131 | 1.41 | 14.3 |  |
| 0.8× | 0 | -1.63 | 0.0268 | 2,130 | 1.61 | 16.3 |  |
| 0.9× | 0 | -1.84 | 0.0301 | 2,130 | 1.81 | 18.3 |  |
| 1.0× | 0 | -2.04 | 0.0335 | 2,130 | 2.01 | 20.4 |  |
| 1.1× | 0 | -2.25 | 0.0368 | 2,130 | 2.21 | 22.4 |  |
| 1.2× | 0 | -2.45 | 0.0402 | 2,130 | 2.41 | 24.5 |  |
| 1.3× | 0 | -2.65 | 0.0435 | 2,130 | 2.61 | 26.5 |  |
| 1.4× | 0 | -2.86 | 0.0469 | 2,130 | 2.81 | 28.5 |  |
| 1.5× | 0 | -3.06 | 0.0502 | 2,130 | 3.01 | 30.6 | balanced (λ = 1) · profit-preserving (λ = 10) |

**1W · Rates/duration**

| m | Risk reduction | Profit sacrificed | Cost | Basis | U(λ=1) | U(λ=10) | Point |
|---|---|---|---|---|---|---|---|
| 0.5× | 267 | 2.12 | 22.5 | 1,487 | 242 | 223 |  |
| 0.6× | 308 | 2.54 | 27 | 1,542 | 278 | 255 |  |
| 0.7× | 344 | 2.96 | 31.5 | 1,607 | 310 | 283 |  |
| 0.8× | 377 | 3.39 | 36 | 1,679 | 337 | 307 |  |
| 0.9× | 405 | 3.81 | 40.5 | 1,759 | 360 | 326 |  |
| 1.0× | 428 | 4.24 | 45 | 1,844 | 379 | 341 |  |
| 1.1× | 448 | 4.66 | 49.5 | 1,935 | 394 | 352 |  |
| 1.2× | 463 | 5.08 | 54 | 2,030 | 404 | 358 |  |
| 1.3× | 474 | 5.51 | 58.5 | 2,130 | 410 | 360 | profit-preserving (λ = 10) |
| 1.4× | 480 | 5.93 | 63 | 2,232 | 411 | 358 | balanced (λ = 1) |
| 1.5× | 482 | 6.35 | 67.5 | 2,338 | 408 | 351 | minimum risk |

**1W · Rates/es**

| m | Risk reduction | Profit sacrificed | Cost | Basis | U(λ=1) | U(λ=10) | Point |
|---|---|---|---|---|---|---|---|
| 0.5× | 0 | -1.02 | 0.0167 | 2,131 | 1 | 10.2 | minimum risk |
| 0.6× | 0 | -1.23 | 0.0201 | 2,131 | 1.21 | 12.2 |  |
| 0.7× | 0 | -1.43 | 0.0234 | 2,131 | 1.41 | 14.3 |  |
| 0.8× | 0 | -1.63 | 0.0268 | 2,130 | 1.61 | 16.3 |  |
| 0.9× | 0 | -1.84 | 0.0301 | 2,130 | 1.81 | 18.3 |  |
| 1.0× | 0 | -2.04 | 0.0335 | 2,130 | 2.01 | 20.4 |  |
| 1.1× | 0 | -2.25 | 0.0368 | 2,130 | 2.21 | 22.4 |  |
| 1.2× | 0 | -2.45 | 0.0402 | 2,130 | 2.41 | 24.5 |  |
| 1.3× | 0 | -2.65 | 0.0435 | 2,130 | 2.61 | 26.5 |  |
| 1.4× | 0 | -2.86 | 0.0469 | 2,130 | 2.81 | 28.5 |  |
| 1.5× | 0 | -3.06 | 0.0502 | 2,130 | 3.01 | 30.6 | balanced (λ = 1) · profit-preserving (λ = 10) |

**1W · Rates/min_variance**

| m | Risk reduction | Profit sacrificed | Cost | Basis | U(λ=1) | U(λ=10) | Point |
|---|---|---|---|---|---|---|---|
| 0.5× | 423 | -42.9 | 5.96 | 4,266 | 460 | 846 |  |
| 0.6× | 499 | -51.5 | 7.15 | 4,273 | 543 | 1,006 |  |
| 0.7× | 571 | -60.1 | 8.34 | 4,282 | 623 | 1,163 |  |
| 0.8× | 641 | -68.6 | 9.54 | 4,293 | 700 | 1,317 |  |
| 0.9× | 707 | -77.2 | 10.7 | 4,306 | 773 | 1,468 |  |
| 1.0× | 770 | -85.8 | 11.9 | 4,321 | 844 | 1,616 |  |
| 1.1× | 829 | -94.4 | 13.1 | 4,338 | 911 | 1,760 |  |
| 1.2× | 885 | -103 | 14.3 | 4,357 | 974 | 1,901 |  |
| 1.3× | 938 | -112 | 15.5 | 4,378 | 1,034 | 2,038 |  |
| 1.4× | 987 | -120 | 16.7 | 4,400 | 1,091 | 2,172 |  |
| 1.5× | 1,033 | -129 | 17.9 | 4,425 | 1,144 | 2,302 | minimum risk · balanced (λ = 1) · profit-preserving (λ = 10) |

**1W · Rates/name**

| m | Risk reduction | Profit sacrificed | Cost | Basis | U(λ=1) | U(λ=10) | Point |
|---|---|---|---|---|---|---|---|
| 0.5× | 375 | 3.63 | 17 | 726 | 355 | 322 |  |
| 0.6× | 445 | 4.36 | 20.4 | 729 | 420 | 381 |  |
| 0.7× | 512 | 5.08 | 23.8 | 740 | 483 | 437 |  |
| 0.8× | 577 | 5.81 | 27.2 | 758 | 544 | 492 |  |
| 0.9× | 640 | 6.54 | 30.6 | 784 | 603 | 545 |  |
| 1.0× | 702 | 7.26 | 33.9 | 816 | 660 | 595 |  |
| 1.1× | 761 | 7.99 | 37.3 | 853 | 715 | 643 |  |
| 1.2× | 817 | 8.71 | 40.7 | 896 | 768 | 689 |  |
| 1.3× | 872 | 9.44 | 44.1 | 943 | 818 | 733 |  |
| 1.4× | 924 | 10.2 | 47.5 | 993 | 866 | 775 |  |
| 1.5× | 974 | 10.9 | 50.9 | 1,047 | 912 | 814 | minimum risk · balanced (λ = 1) · profit-preserving (λ = 10) |

**1W · Rates/systematic**

| m | Risk reduction | Profit sacrificed | Cost | Basis | U(λ=1) | U(λ=10) | Point |
|---|---|---|---|---|---|---|---|
| 0.5× | 994 | 51 | 30.2 | 2,090 | 912 | 454 |  |
| 0.6× | 1,166 | 61.1 | 36.3 | 2,159 | 1,069 | 518 |  |
| 0.7× | 1,329 | 71.3 | 42.3 | 2,237 | 1,215 | 573 |  |
| 0.8× | 1,481 | 81.5 | 48.4 | 2,325 | 1,351 | 617 |  |
| 0.9× | 1,622 | 91.7 | 54.4 | 2,420 | 1,475 | 650 |  |
| 1.0× | 1,751 | 102 | 60.5 | 2,523 | 1,589 | 671 |  |
| 1.1× | 1,868 | 112 | 66.5 | 2,631 | 1,689 | 681 | profit-preserving (λ = 10) |
| 1.2× | 1,973 | 122 | 72.6 | 2,745 | 1,778 | 677 |  |
| 1.3× | 2,064 | 132 | 78.6 | 2,865 | 1,853 | 660 |  |
| 1.4× | 2,142 | 143 | 84.7 | 2,988 | 1,914 | 630 |  |
| 1.5× | 2,205 | 153 | 90.7 | 3,115 | 1,962 | 586 | minimum risk · balanced (λ = 1) |

**1W · Rates/target_vol**

| m | Risk reduction | Profit sacrificed | Cost | Basis | U(λ=1) | U(λ=10) | Point |
|---|---|---|---|---|---|---|---|
| 0.5× | 0 | -0 | 0 | 184 | 0 | 0 | minimum risk · balanced (λ = 1) · profit-preserving (λ = 10) |
| 0.6× | 0 | -0 | 0 | 184 | 0 | 0 |  |
| 0.7× | 0 | -0 | 0 | 184 | 0 | 0 |  |
| 0.8× | 0 | -0 | 0 | 184 | 0 | 0 |  |
| 0.9× | 0 | -0 | 0 | 184 | 0 | 0 |  |
| 1.0× | 0 | -0 | 0 | 184 | 0 | 0 |  |
| 1.1× | 0 | -0 | 0 | 184 | 0 | 0 |  |
| 1.2× | 0 | -0 | 0 | 184 | 0 | 0 |  |
| 1.3× | 0 | -0 | 0 | 184 | 0 | 0 |  |
| 1.4× | 0 | -0 | 0 | 184 | 0 | 0 |  |
| 1.5× | 0 | -0 | 0 | 184 | 0 | 0 |  |

**1W · Rates/var**

| m | Risk reduction | Profit sacrificed | Cost | Basis | U(λ=1) | U(λ=10) | Point |
|---|---|---|---|---|---|---|---|
| 0.5× | 0 | -1.02 | 0.0167 | 2,131 | 1 | 10.2 | minimum risk |
| 0.6× | 0 | -1.23 | 0.0201 | 2,131 | 1.21 | 12.2 |  |
| 0.7× | 0 | -1.43 | 0.0234 | 2,131 | 1.41 | 14.3 |  |
| 0.8× | 0 | -1.63 | 0.0268 | 2,130 | 1.61 | 16.3 |  |
| 0.9× | 0 | -1.84 | 0.0301 | 2,130 | 1.81 | 18.3 |  |
| 1.0× | 0 | -2.04 | 0.0335 | 2,130 | 2.01 | 20.4 |  |
| 1.1× | 0 | -2.25 | 0.0368 | 2,130 | 2.21 | 22.4 |  |
| 1.2× | 0 | -2.45 | 0.0402 | 2,130 | 2.41 | 24.5 |  |
| 1.3× | 0 | -2.65 | 0.0435 | 2,130 | 2.61 | 26.5 |  |
| 1.4× | 0 | -2.86 | 0.0469 | 2,130 | 2.81 | 28.5 |  |
| 1.5× | 0 | -3.06 | 0.0502 | 2,130 | 3.01 | 30.6 | balanced (λ = 1) · profit-preserving (λ = 10) |

**1W · Volatility/volatility**

| m | Risk reduction | Profit sacrificed | Cost | Basis | U(λ=1) | U(λ=10) | Point |
|---|---|---|---|---|---|---|---|
| 0.5× | 1,114 | 115 | 90.4 | 698 | 908 | -130 | profit-preserving (λ = 10) |
| 0.6× | 1,330 | 138 | 109 | 771 | 1,083 | -163 |  |
| 0.7× | 1,544 | 162 | 127 | 856 | 1,256 | -198 |  |
| 0.8× | 1,756 | 185 | 145 | 952 | 1,427 | -235 |  |
| 0.9× | 1,965 | 208 | 163 | 1,054 | 1,595 | -275 |  |
| 1.0× | 2,172 | 231 | 181 | 1,162 | 1,760 | -317 |  |
| 1.1× | 2,376 | 254 | 199 | 1,273 | 1,924 | -361 |  |
| 1.2× | 2,578 | 277 | 217 | 1,388 | 2,084 | -408 |  |
| 1.3× | 2,777 | 300 | 235 | 1,505 | 2,242 | -458 |  |
| 1.4× | 2,973 | 323 | 253 | 1,624 | 2,397 | -511 |  |
| 1.5× | 3,167 | 346 | 271 | 1,744 | 2,550 | -566 | minimum risk · balanced (λ = 1) |

**1M · Commodity/commodity**

| m | Risk reduction | Profit sacrificed | Cost | Basis | U(λ=1) | U(λ=10) | Point |
|---|---|---|---|---|---|---|---|
| 0.5× | 1,255 | 488 | 246 | 1,507 | 521 | -3,872 | profit-preserving (λ = 10) |
| 0.6× | 1,470 | 586 | 296 | 1,426 | 589 | -4,682 |  |
| 0.7× | 1,672 | 683 | 345 | 1,358 | 644 | -5,505 |  |
| 0.8× | 1,862 | 781 | 394 | 1,307 | 687 | -6,341 |  |
| 0.9× | 2,038 | 878 | 443 | 1,273 | 717 | -7,190 |  |
| 1.0× | 2,202 | 976 | 493 | 1,259 | 733 | -8,052 |  |
| 1.1× | 2,352 | 1,074 | 542 | 1,265 | 736 | -8,927 | balanced (λ = 1) |
| 1.2× | 2,489 | 1,171 | 591 | 1,290 | 726 | -9,816 |  |
| 1.3× | 2,612 | 1,269 | 640 | 1,334 | 702 | -10,718 |  |
| 1.4× | 2,721 | 1,367 | 690 | 1,395 | 665 | -11,634 |  |
| 1.5× | 2,816 | 1,464 | 739 | 1,471 | 613 | -12,564 | minimum risk |

**1M · Commodity/drawdown**

| m | Risk reduction | Profit sacrificed | Cost | Basis | U(λ=1) | U(λ=10) | Point |
|---|---|---|---|---|---|---|---|
| 0.5× | 963 | 484 | 5.93 | 3,717 | 473 | -3,882 | profit-preserving (λ = 10) |
| 0.6× | 1,156 | 581 | 7.12 | 3,674 | 568 | -4,658 |  |
| 0.7× | 1,348 | 677 | 8.31 | 3,636 | 662 | -5,435 |  |
| 0.8× | 1,541 | 774 | 9.49 | 3,605 | 757 | -6,211 |  |
| 0.9× | 1,733 | 871 | 10.7 | 3,581 | 852 | -6,987 |  |
| 1.0× | 1,926 | 968 | 11.9 | 3,564 | 946 | -7,764 |  |
| 1.1× | 2,119 | 1,065 | 13.1 | 3,553 | 1,041 | -8,540 |  |
| 1.2× | 2,311 | 1,161 | 14.2 | 3,550 | 1,136 | -9,317 |  |
| 1.3× | 2,504 | 1,258 | 15.4 | 3,554 | 1,230 | -10,093 |  |
| 1.4× | 2,696 | 1,355 | 16.6 | 3,565 | 1,325 | -10,869 |  |
| 1.5× | 2,889 | 1,452 | 17.8 | 3,583 | 1,420 | -11,646 | minimum risk · balanced (λ = 1) |

**1M · Commodity/es**

| m | Risk reduction | Profit sacrificed | Cost | Basis | U(λ=1) | U(λ=10) | Point |
|---|---|---|---|---|---|---|---|
| 0.5× | 963 | 484 | 5.93 | 3,717 | 473 | -3,882 | profit-preserving (λ = 10) |
| 0.6× | 1,156 | 581 | 7.12 | 3,674 | 568 | -4,658 |  |
| 0.7× | 1,348 | 677 | 8.31 | 3,636 | 662 | -5,435 |  |
| 0.8× | 1,541 | 774 | 9.49 | 3,605 | 757 | -6,211 |  |
| 0.9× | 1,733 | 871 | 10.7 | 3,581 | 852 | -6,987 |  |
| 1.0× | 1,926 | 968 | 11.9 | 3,564 | 946 | -7,764 |  |
| 1.1× | 2,119 | 1,065 | 13.1 | 3,553 | 1,041 | -8,540 |  |
| 1.2× | 2,311 | 1,161 | 14.2 | 3,550 | 1,136 | -9,317 |  |
| 1.3× | 2,504 | 1,258 | 15.4 | 3,554 | 1,230 | -10,093 |  |
| 1.4× | 2,696 | 1,355 | 16.6 | 3,565 | 1,325 | -10,869 |  |
| 1.5× | 2,889 | 1,452 | 17.8 | 3,583 | 1,420 | -11,646 | minimum risk · balanced (λ = 1) |

**1M · Commodity/min_variance**

| m | Risk reduction | Profit sacrificed | Cost | Basis | U(λ=1) | U(λ=10) | Point |
|---|---|---|---|---|---|---|---|
| 0.5× | 2,266 | 1,174 | -23 | 7,281 | 1,115 | -9,450 | profit-preserving (λ = 10) |
| 0.6× | 2,566 | 1,409 | -27.6 | 7,172 | 1,185 | -11,492 |  |
| 0.7× | 2,812 | 1,643 | -32.2 | 7,080 | 1,201 | -13,590 | balanced (λ = 1) |
| 0.8× | 3,002 | 1,878 | -36.8 | 7,005 | 1,160 | -15,743 |  |
| 0.9× | 3,134 | 2,113 | -41.4 | 6,948 | 1,062 | -17,954 |  |
| 1.0× | 3,208 | 2,348 | -46 | 6,910 | 907 | -20,223 |  |
| 1.1× | 3,224 | 2,582 | -50.6 | 6,891 | 692 | -22,550 | minimum risk |
| 1.2× | 3,182 | 2,817 | -55.2 | 6,890 | 420 | -24,936 |  |
| 1.3× | 3,081 | 3,052 | -59.8 | 6,908 | 88.6 | -27,380 |  |
| 1.4× | 2,922 | 3,287 | -64.4 | 6,946 | -300 | -29,881 |  |
| 1.5× | 2,707 | 3,522 | -69 | 7,001 | -746 | -32,440 |  |

**1M · Commodity/systematic**

| m | Risk reduction | Profit sacrificed | Cost | Basis | U(λ=1) | U(λ=10) | Point |
|---|---|---|---|---|---|---|---|
| 0.5× | 5,455 | 1,747 | 486 | 2,893 | 3,222 | -12,499 | profit-preserving (λ = 10) |
| 0.6× | 6,452 | 2,096 | 583 | 2,736 | 3,773 | -15,092 |  |
| 0.7× | 7,410 | 2,445 | 681 | 2,613 | 4,284 | -17,725 |  |
| 0.8× | 8,324 | 2,795 | 778 | 2,529 | 4,751 | -20,402 |  |
| 0.9× | 9,188 | 3,144 | 875 | 2,487 | 5,169 | -23,128 |  |
| 1.0× | 9,998 | 3,494 | 972 | 2,490 | 5,533 | -25,909 |  |
| 1.1× | 10,748 | 3,843 | 1,070 | 2,537 | 5,836 | -28,750 |  |
| 1.2× | 11,432 | 4,192 | 1,167 | 2,627 | 6,073 | -31,657 |  |
| 1.3× | 12,042 | 4,542 | 1,264 | 2,755 | 6,237 | -34,638 |  |
| 1.4× | 12,574 | 4,891 | 1,361 | 2,916 | 6,322 | -37,697 | balanced (λ = 1) |
| 1.5× | 13,020 | 5,240 | 1,458 | 3,104 | 6,322 | -40,841 | minimum risk |

**1M · Commodity/target_vol**

| m | Risk reduction | Profit sacrificed | Cost | Basis | U(λ=1) | U(λ=10) | Point |
|---|---|---|---|---|---|---|---|
| 0.5× | 735 | 754 | 2.83 | 2,123 | -21.8 | -6,809 | balanced (λ = 1) · profit-preserving (λ = 10) |
| 0.6× | 859 | 905 | 3.4 | 2,095 | -49.2 | -8,194 |  |
| 0.7× | 975 | 1,056 | 3.96 | 2,074 | -84.4 | -9,587 |  |
| 0.8× | 1,084 | 1,207 | 4.53 | 2,058 | -128 | -10,988 |  |
| 0.9× | 1,184 | 1,358 | 5.1 | 2,049 | -179 | -12,396 |  |
| 1.0× | 1,276 | 1,508 | 5.66 | 2,046 | -238 | -13,813 |  |
| 1.1× | 1,360 | 1,659 | 6.23 | 2,048 | -305 | -15,238 |  |
| 1.2× | 1,436 | 1,810 | 6.8 | 2,058 | -381 | -16,671 |  |
| 1.3× | 1,503 | 1,961 | 7.36 | 2,073 | -465 | -18,113 |  |
| 1.4× | 1,563 | 2,112 | 7.93 | 2,094 | -557 | -19,563 |  |
| 1.5× | 1,613 | 2,263 | 8.5 | 2,121 | -658 | -21,021 | minimum risk |

**1M · Commodity/var**

| m | Risk reduction | Profit sacrificed | Cost | Basis | U(λ=1) | U(λ=10) | Point |
|---|---|---|---|---|---|---|---|
| 0.5× | 963 | 484 | 5.93 | 3,717 | 473 | -3,882 | profit-preserving (λ = 10) |
| 0.6× | 1,156 | 581 | 7.12 | 3,674 | 568 | -4,658 |  |
| 0.7× | 1,348 | 677 | 8.31 | 3,636 | 662 | -5,435 |  |
| 0.8× | 1,541 | 774 | 9.49 | 3,605 | 757 | -6,211 |  |
| 0.9× | 1,733 | 871 | 10.7 | 3,581 | 852 | -6,987 |  |
| 1.0× | 1,926 | 968 | 11.9 | 3,564 | 946 | -7,764 |  |
| 1.1× | 2,119 | 1,065 | 13.1 | 3,553 | 1,041 | -8,540 |  |
| 1.2× | 2,311 | 1,161 | 14.2 | 3,550 | 1,136 | -9,317 |  |
| 1.3× | 2,504 | 1,258 | 15.4 | 3,554 | 1,230 | -10,093 |  |
| 1.4× | 2,696 | 1,355 | 16.6 | 3,565 | 1,325 | -10,869 |  |
| 1.5× | 2,889 | 1,452 | 17.8 | 3,583 | 1,420 | -11,646 | minimum risk · balanced (λ = 1) |

**1M · Credit/credit**

| m | Risk reduction | Profit sacrificed | Cost | Basis | U(λ=1) | U(λ=10) | Point |
|---|---|---|---|---|---|---|---|
| 0.5× | 1,066 | 102 | 213 | 1,356 | 751 | -168 | profit-preserving (λ = 10) |
| 0.6× | 1,243 | 122 | 256 | 1,586 | 865 | -238 |  |
| 0.7× | 1,407 | 143 | 298 | 1,824 | 966 | -320 |  |
| 0.8× | 1,558 | 163 | 341 | 2,067 | 1,054 | -415 |  |
| 0.9× | 1,697 | 184 | 383 | 2,314 | 1,130 | -524 |  |
| 1.0× | 1,822 | 204 | 426 | 2,563 | 1,192 | -645 |  |
| 1.1× | 1,934 | 225 | 469 | 2,814 | 1,241 | -780 |  |
| 1.2× | 2,033 | 245 | 511 | 3,067 | 1,276 | -928 |  |
| 1.3× | 2,117 | 265 | 554 | 3,321 | 1,298 | -1,090 |  |
| 1.4× | 2,188 | 286 | 596 | 3,576 | 1,306 | -1,266 | balanced (λ = 1) |
| 1.5× | 2,245 | 306 | 639 | 3,831 | 1,300 | -1,456 | minimum risk |

**1M · Credit/drawdown**

| m | Risk reduction | Profit sacrificed | Cost | Basis | U(λ=1) | U(λ=10) | Point |
|---|---|---|---|---|---|---|---|
| 0.5× | 363 | -21.4 | 1.96 | 1,458 | 382 | 574 |  |
| 0.6× | 435 | -25.6 | 2.35 | 1,463 | 459 | 689 |  |
| 0.7× | 508 | -29.9 | 2.74 | 1,470 | 535 | 804 |  |
| 0.8× | 581 | -34.2 | 3.13 | 1,478 | 612 | 919 |  |
| 0.9× | 653 | -38.4 | 3.52 | 1,487 | 688 | 1,034 |  |
| 1.0× | 726 | -42.7 | 3.91 | 1,496 | 764 | 1,149 |  |
| 1.1× | 798 | -47 | 4.3 | 1,507 | 841 | 1,264 |  |
| 1.2× | 871 | -51.2 | 4.7 | 1,519 | 917 | 1,379 |  |
| 1.3× | 943 | -55.5 | 5.09 | 1,532 | 994 | 1,493 |  |
| 1.4× | 1,016 | -59.8 | 5.48 | 1,545 | 1,070 | 1,608 |  |
| 1.5× | 1,089 | -64.1 | 5.87 | 1,560 | 1,147 | 1,723 | minimum risk · balanced (λ = 1) · profit-preserving (λ = 10) |

**1M · Credit/es**

| m | Risk reduction | Profit sacrificed | Cost | Basis | U(λ=1) | U(λ=10) | Point |
|---|---|---|---|---|---|---|---|
| 0.5× | 363 | -21.4 | 1.96 | 1,458 | 382 | 574 |  |
| 0.6× | 435 | -25.6 | 2.35 | 1,463 | 459 | 689 |  |
| 0.7× | 508 | -29.9 | 2.74 | 1,470 | 535 | 804 |  |
| 0.8× | 581 | -34.2 | 3.13 | 1,478 | 612 | 919 |  |
| 0.9× | 653 | -38.4 | 3.52 | 1,487 | 688 | 1,034 |  |
| 1.0× | 726 | -42.7 | 3.91 | 1,496 | 764 | 1,149 |  |
| 1.1× | 798 | -47 | 4.3 | 1,507 | 841 | 1,264 |  |
| 1.2× | 871 | -51.2 | 4.7 | 1,519 | 917 | 1,379 |  |
| 1.3× | 943 | -55.5 | 5.09 | 1,532 | 994 | 1,493 |  |
| 1.4× | 1,016 | -59.8 | 5.48 | 1,545 | 1,070 | 1,608 |  |
| 1.5× | 1,089 | -64.1 | 5.87 | 1,560 | 1,147 | 1,723 | minimum risk · balanced (λ = 1) · profit-preserving (λ = 10) |

**1M · Credit/min_variance**

| m | Risk reduction | Profit sacrificed | Cost | Basis | U(λ=1) | U(λ=10) | Point |
|---|---|---|---|---|---|---|---|
| 0.5× | 676 | -121 | 7.92 | 2,936 | 789 | 1,881 |  |
| 0.6× | 784 | -146 | 9.51 | 2,957 | 920 | 2,231 |  |
| 0.7× | 883 | -170 | 11.1 | 2,980 | 1,041 | 2,570 |  |
| 0.8× | 972 | -194 | 12.7 | 3,008 | 1,153 | 2,900 |  |
| 0.9× | 1,051 | -218 | 14.3 | 3,039 | 1,255 | 3,221 |  |
| 1.0× | 1,121 | -243 | 15.8 | 3,073 | 1,347 | 3,532 |  |
| 1.1× | 1,180 | -267 | 17.4 | 3,111 | 1,430 | 3,832 |  |
| 1.2× | 1,230 | -291 | 19 | 3,151 | 1,502 | 4,123 |  |
| 1.3× | 1,270 | -315 | 20.6 | 3,195 | 1,565 | 4,404 |  |
| 1.4× | 1,299 | -340 | 22.2 | 3,242 | 1,617 | 4,675 |  |
| 1.5× | 1,319 | -364 | 23.8 | 3,291 | 1,659 | 4,935 | minimum risk · balanced (λ = 1) · profit-preserving (λ = 10) |

**1M · Credit/name**

| m | Risk reduction | Profit sacrificed | Cost | Basis | U(λ=1) | U(λ=10) | Point |
|---|---|---|---|---|---|---|---|
| 0.5× | 2,805 | 281 | 167 | 796 | 2,357 | -170 | profit-preserving (λ = 10) |
| 0.6× | 3,349 | 337 | 201 | 913 | 2,811 | -221 |  |
| 0.7× | 3,885 | 393 | 234 | 1,037 | 3,258 | -280 |  |
| 0.8× | 4,414 | 449 | 268 | 1,164 | 3,697 | -346 |  |
| 0.9× | 4,934 | 505 | 301 | 1,293 | 4,127 | -421 |  |
| 1.0× | 5,444 | 562 | 335 | 1,425 | 4,547 | -506 |  |
| 1.1× | 5,943 | 618 | 368 | 1,558 | 4,957 | -602 |  |
| 1.2× | 6,430 | 674 | 402 | 1,692 | 5,354 | -710 |  |
| 1.3× | 6,903 | 730 | 435 | 1,827 | 5,738 | -832 |  |
| 1.4× | 7,362 | 786 | 469 | 1,963 | 6,107 | -968 |  |
| 1.5× | 7,804 | 842 | 502 | 2,099 | 6,459 | -1,121 | minimum risk · balanced (λ = 1) |

**1M · Credit/target_vol**

| m | Risk reduction | Profit sacrificed | Cost | Basis | U(λ=1) | U(λ=10) | Point |
|---|---|---|---|---|---|---|---|
| 0.5× | -3.99 | -9.05 | 0.863 | 1,415 | 4.2 | 85.6 | minimum risk |
| 0.6× | -4.81 | -10.9 | 1.04 | 1,415 | 5.01 | 103 |  |
| 0.7× | -5.64 | -12.7 | 1.21 | 1,414 | 5.81 | 120 |  |
| 0.8× | -6.49 | -14.5 | 1.38 | 1,414 | 6.6 | 137 |  |
| 0.9× | -7.34 | -16.3 | 1.55 | 1,413 | 7.39 | 154 |  |
| 1.0× | -8.2 | -18.1 | 1.73 | 1,413 | 8.17 | 171 |  |
| 1.1× | -9.07 | -19.9 | 1.9 | 1,413 | 8.93 | 188 |  |
| 1.2× | -9.95 | -21.7 | 2.07 | 1,413 | 9.69 | 205 |  |
| 1.3× | -10.8 | -23.5 | 2.24 | 1,413 | 10.4 | 222 |  |
| 1.4× | -11.7 | -25.3 | 2.42 | 1,413 | 11.2 | 239 |  |
| 1.5× | -12.6 | -27.1 | 2.59 | 1,413 | 11.9 | 256 | balanced (λ = 1) · profit-preserving (λ = 10) |

**1M · Credit/var**

| m | Risk reduction | Profit sacrificed | Cost | Basis | U(λ=1) | U(λ=10) | Point |
|---|---|---|---|---|---|---|---|
| 0.5× | 363 | -21.4 | 1.96 | 1,458 | 382 | 574 |  |
| 0.6× | 435 | -25.6 | 2.35 | 1,463 | 459 | 689 |  |
| 0.7× | 508 | -29.9 | 2.74 | 1,470 | 535 | 804 |  |
| 0.8× | 581 | -34.2 | 3.13 | 1,478 | 612 | 919 |  |
| 0.9× | 653 | -38.4 | 3.52 | 1,487 | 688 | 1,034 |  |
| 1.0× | 726 | -42.7 | 3.91 | 1,496 | 764 | 1,149 |  |
| 1.1× | 798 | -47 | 4.3 | 1,507 | 841 | 1,264 |  |
| 1.2× | 871 | -51.2 | 4.7 | 1,519 | 917 | 1,379 |  |
| 1.3× | 943 | -55.5 | 5.09 | 1,532 | 994 | 1,493 |  |
| 1.4× | 1,016 | -59.8 | 5.48 | 1,545 | 1,070 | 1,608 |  |
| 1.5× | 1,089 | -64.1 | 5.87 | 1,560 | 1,147 | 1,723 | minimum risk · balanced (λ = 1) · profit-preserving (λ = 10) |

**1M · Crypto/crypto**

| m | Risk reduction | Profit sacrificed | Cost | Basis | U(λ=1) | U(λ=10) | Point |
|---|---|---|---|---|---|---|---|
| 0.5× | 55,796 | 2,823 | 1,571 | 7,871 | 51,402 | 25,992 |  |
| 0.6× | 66,579 | 3,388 | 1,885 | 7,758 | 61,306 | 30,814 |  |
| 0.7× | 77,128 | 3,953 | 2,199 | 8,349 | 70,977 | 35,402 |  |
| 0.8× | 87,349 | 4,517 | 2,513 | 9,514 | 80,318 | 39,662 |  |
| 0.9× | 97,093 | 5,082 | 2,827 | 11,073 | 89,184 | 43,445 |  |
| 1.0× | 106,126 | 5,647 | 3,141 | 12,884 | 97,338 | 46,518 |  |
| 1.1× | 114,072 | 6,211 | 3,455 | 14,854 | 104,405 | 48,502 |  |
| 1.2× | 120,352 | 6,776 | 3,770 | 16,929 | 109,807 | 48,822 | profit-preserving (λ = 10) |
| 1.3× | 124,200 | 7,341 | 4,084 | 19,075 | 112,775 | 46,708 | balanced (λ = 1) |
| 1.4× | 124,916 | 7,905 | 4,398 | 21,269 | 112,612 | 41,463 | minimum risk |
| 1.5× | 122,344 | 8,470 | 4,712 | 23,498 | 109,162 | 32,931 |  |

**1M · Crypto/drawdown**

| m | Risk reduction | Profit sacrificed | Cost | Basis | U(λ=1) | U(λ=10) | Point |
|---|---|---|---|---|---|---|---|
| 0.5× | 41,842 | 2,013 | 800 | 11,080 | 39,029 | 20,913 |  |
| 0.6× | 50,210 | 2,415 | 960 | 10,275 | 46,835 | 25,096 |  |
| 0.7× | 58,579 | 2,818 | 1,120 | 9,560 | 54,641 | 29,279 |  |
| 0.8× | 66,947 | 3,221 | 1,280 | 8,957 | 62,446 | 33,461 |  |
| 0.9× | 75,315 | 3,623 | 1,440 | 8,490 | 70,252 | 37,644 |  |
| 1.0× | 83,684 | 4,026 | 1,600 | 8,181 | 78,058 | 41,827 |  |
| 1.1× | 92,052 | 4,428 | 1,760 | 8,049 | 85,864 | 46,010 |  |
| 1.2× | 100,421 | 4,831 | 1,920 | 8,104 | 93,670 | 50,192 |  |
| 1.3× | 108,789 | 5,233 | 2,080 | 8,340 | 101,475 | 54,375 |  |
| 1.4× | 117,157 | 5,636 | 2,240 | 8,744 | 109,281 | 58,558 |  |
| 1.5× | 125,526 | 6,039 | 2,400 | 9,293 | 117,087 | 62,740 | minimum risk · balanced (λ = 1) · profit-preserving (λ = 10) |

**1M · Crypto/es**

| m | Risk reduction | Profit sacrificed | Cost | Basis | U(λ=1) | U(λ=10) | Point |
|---|---|---|---|---|---|---|---|
| 0.5× | 41,842 | 2,013 | 800 | 11,080 | 39,029 | 20,913 |  |
| 0.6× | 50,210 | 2,415 | 960 | 10,275 | 46,835 | 25,096 |  |
| 0.7× | 58,579 | 2,818 | 1,120 | 9,560 | 54,641 | 29,279 |  |
| 0.8× | 66,947 | 3,221 | 1,280 | 8,957 | 62,446 | 33,461 |  |
| 0.9× | 75,315 | 3,623 | 1,440 | 8,490 | 70,252 | 37,644 |  |
| 1.0× | 83,684 | 4,026 | 1,600 | 8,181 | 78,058 | 41,827 |  |
| 1.1× | 92,052 | 4,428 | 1,760 | 8,049 | 85,864 | 46,010 |  |
| 1.2× | 100,421 | 4,831 | 1,920 | 8,104 | 93,670 | 50,192 |  |
| 1.3× | 108,789 | 5,233 | 2,080 | 8,340 | 101,475 | 54,375 |  |
| 1.4× | 117,157 | 5,636 | 2,240 | 8,744 | 109,281 | 58,558 |  |
| 1.5× | 125,526 | 6,039 | 2,400 | 9,293 | 117,087 | 62,740 | minimum risk · balanced (λ = 1) · profit-preserving (λ = 10) |

**1M · Crypto/min_variance**

| m | Risk reduction | Profit sacrificed | Cost | Basis | U(λ=1) | U(λ=10) | Point |
|---|---|---|---|---|---|---|---|
| 0.5× | 63,359 | 3,754 | 1,863 | 21,011 | 57,742 | 23,955 |  |
| 0.6× | 75,714 | 4,505 | 2,235 | 19,274 | 68,974 | 28,430 |  |
| 0.7× | 87,851 | 5,256 | 2,608 | 17,788 | 79,988 | 32,686 |  |
| 0.8× | 99,658 | 6,007 | 2,980 | 16,622 | 90,671 | 36,612 |  |
| 0.9× | 110,934 | 6,757 | 3,353 | 15,847 | 100,824 | 40,008 |  |
| 1.0× | 121,311 | 7,508 | 3,725 | 15,521 | 110,078 | 42,504 |  |
| 1.1× | 130,081 | 8,259 | 4,098 | 15,673 | 117,724 | 43,393 | profit-preserving (λ = 10) |
| 1.2× | 135,973 | 9,010 | 4,470 | 16,288 | 122,493 | 41,404 |  |
| 1.3× | 137,388 | 9,761 | 4,843 | 17,318 | 122,785 | 34,938 | minimum risk · balanced (λ = 1) |
| 1.4× | 133,796 | 10,512 | 5,215 | 18,694 | 118,069 | 23,465 |  |
| 1.5× | 126,419 | 11,262 | 5,588 | 20,346 | 109,569 | 8,208 |  |

**1M · Crypto/systematic**

| m | Risk reduction | Profit sacrificed | Cost | Basis | U(λ=1) | U(λ=10) | Point |
|---|---|---|---|---|---|---|---|
| 0.5× | 57,467 | 3,025 | 1,660 | 8,095 | 52,783 | 25,560 |  |
| 0.6× | 68,581 | 3,630 | 1,992 | 7,979 | 62,960 | 30,292 |  |
| 0.7× | 79,458 | 4,235 | 2,324 | 8,587 | 72,900 | 34,788 |  |
| 0.8× | 90,003 | 4,840 | 2,656 | 9,785 | 82,508 | 38,951 |  |
| 0.9× | 100,063 | 5,445 | 2,987 | 11,388 | 91,631 | 42,630 |  |
| 1.0× | 109,398 | 6,050 | 3,319 | 13,250 | 100,029 | 45,583 |  |
| 1.1× | 117,617 | 6,654 | 3,651 | 15,277 | 107,311 | 47,421 |  |
| 1.2× | 124,116 | 7,259 | 3,983 | 17,411 | 112,874 | 47,539 | profit-preserving (λ = 10) |
| 1.3× | 128,088 | 7,864 | 4,315 | 19,617 | 115,908 | 45,129 | balanced (λ = 1) |
| 1.4× | 128,792 | 8,469 | 4,647 | 21,874 | 115,676 | 39,452 | minimum risk |
| 1.5× | 126,073 | 9,074 | 4,979 | 24,167 | 112,019 | 30,351 |  |

**1M · Crypto/target_vol**

| m | Risk reduction | Profit sacrificed | Cost | Basis | U(λ=1) | U(λ=10) | Point |
|---|---|---|---|---|---|---|---|
| 0.5× | 49,980 | 3,153 | 1,448 | 17,249 | 45,379 | 17,003 |  |
| 0.6× | 59,791 | 3,783 | 1,738 | 15,856 | 54,270 | 20,219 |  |
| 0.7× | 69,497 | 4,414 | 2,028 | 14,655 | 63,056 | 23,330 |  |
| 0.8× | 79,066 | 5,045 | 2,317 | 13,695 | 71,704 | 26,303 |  |
| 0.9× | 88,448 | 5,675 | 2,607 | 13,031 | 80,166 | 29,090 |  |
| 1.0× | 97,572 | 6,306 | 2,896 | 12,708 | 88,370 | 31,618 |  |
| 1.1× | 106,325 | 6,936 | 3,186 | 12,753 | 96,203 | 33,776 |  |
| 1.2× | 114,533 | 7,567 | 3,476 | 13,162 | 103,490 | 35,388 |  |
| 1.3× | 121,914 | 8,197 | 3,765 | 13,902 | 109,951 | 36,174 | profit-preserving (λ = 10) |
| 1.4× | 128,030 | 8,828 | 4,055 | 14,925 | 115,147 | 35,695 |  |
| 1.5× | 132,260 | 9,459 | 4,345 | 16,176 | 118,457 | 33,329 | minimum risk · balanced (λ = 1) |

**1M · Crypto/var**

| m | Risk reduction | Profit sacrificed | Cost | Basis | U(λ=1) | U(λ=10) | Point |
|---|---|---|---|---|---|---|---|
| 0.5× | 41,842 | 2,013 | 800 | 11,080 | 39,029 | 20,913 |  |
| 0.6× | 50,210 | 2,415 | 960 | 10,275 | 46,835 | 25,096 |  |
| 0.7× | 58,579 | 2,818 | 1,120 | 9,560 | 54,641 | 29,279 |  |
| 0.8× | 66,947 | 3,221 | 1,280 | 8,957 | 62,446 | 33,461 |  |
| 0.9× | 75,315 | 3,623 | 1,440 | 8,490 | 70,252 | 37,644 |  |
| 1.0× | 83,684 | 4,026 | 1,600 | 8,181 | 78,058 | 41,827 |  |
| 1.1× | 92,052 | 4,428 | 1,760 | 8,049 | 85,864 | 46,010 |  |
| 1.2× | 100,421 | 4,831 | 1,920 | 8,104 | 93,670 | 50,192 |  |
| 1.3× | 108,789 | 5,233 | 2,080 | 8,340 | 101,475 | 54,375 |  |
| 1.4× | 117,157 | 5,636 | 2,240 | 8,744 | 109,281 | 58,558 |  |
| 1.5× | 125,526 | 6,039 | 2,400 | 9,293 | 117,087 | 62,740 | minimum risk · balanced (λ = 1) · profit-preserving (λ = 10) |

**1M · Equity/beta**

| m | Risk reduction | Profit sacrificed | Cost | Basis | U(λ=1) | U(λ=10) | Point |
|---|---|---|---|---|---|---|---|
| 0.5× | 8,690 | 2,789 | -17.1 | 2,775 | 5,918 | -19,179 | profit-preserving (λ = 10) |
| 0.6× | 10,295 | 3,346 | -20.5 | 2,387 | 6,969 | -23,147 |  |
| 0.7× | 11,845 | 3,904 | -24 | 2,079 | 7,965 | -27,171 |  |
| 0.8× | 13,336 | 4,462 | -27.4 | 1,891 | 8,901 | -31,254 |  |
| 0.9× | 14,761 | 5,019 | -30.8 | 1,859 | 9,772 | -35,403 |  |
| 1.0× | 16,114 | 5,577 | -34.2 | 1,991 | 10,571 | -39,623 |  |
| 1.1× | 17,389 | 6,135 | -37.7 | 2,259 | 11,291 | -43,922 |  |
| 1.2× | 18,578 | 6,693 | -41.1 | 2,620 | 11,927 | -48,306 |  |
| 1.3× | 19,675 | 7,250 | -44.5 | 3,042 | 12,469 | -52,783 |  |
| 1.4× | 20,671 | 7,808 | -47.9 | 3,504 | 12,911 | -57,360 |  |
| 1.5× | 21,560 | 8,366 | -51.4 | 3,990 | 13,246 | -62,045 | minimum risk · balanced (λ = 1) |

**1M · Equity/crash**

| m | Risk reduction | Profit sacrificed | Cost | Basis | U(λ=1) | U(λ=10) | Point |
|---|---|---|---|---|---|---|---|
| 0.5× | 10,458 | 2,503 | 904 | 3,283 | 7,052 | -15,476 | profit-preserving (λ = 10) |
| 0.6× | 12,118 | 3,004 | 1,084 | 2,955 | 8,030 | -19,003 |  |
| 0.7× | 13,616 | 3,504 | 1,265 | 2,664 | 8,847 | -22,692 |  |
| 0.8× | 14,850 | 4,005 | 1,446 | 2,423 | 9,400 | -26,644 |  |
| 0.9× | 15,951 | 4,505 | 1,626 | 2,247 | 9,819 | -30,731 |  |
| 1.0× | 16,932 | 5,006 | 1,807 | 2,154 | 10,119 | -34,936 |  |
| 1.1× | 17,788 | 5,507 | 1,988 | 2,154 | 10,293 | -39,267 |  |
| 1.2× | 18,559 | 6,007 | 2,169 | 2,246 | 10,383 | -43,682 | balanced (λ = 1) |
| 1.3× | 19,237 | 6,508 | 2,349 | 2,421 | 10,380 | -48,191 |  |
| 1.4× | 19,851 | 7,008 | 2,530 | 2,662 | 10,312 | -52,764 |  |
| 1.5× | 20,407 | 7,509 | 2,711 | 2,953 | 10,187 | -57,395 | minimum risk |

**1M · Equity/drawdown**

| m | Risk reduction | Profit sacrificed | Cost | Basis | U(λ=1) | U(λ=10) | Point |
|---|---|---|---|---|---|---|---|
| 0.5× | 20,255 | 2,433 | -16.6 | 4,498 | 17,838 | -4,060 | profit-preserving (λ = 10) |
| 0.6× | 23,827 | 2,920 | -20 | 4,088 | 20,928 | -5,350 |  |
| 0.7× | 27,243 | 3,406 | -23.3 | 3,717 | 23,860 | -6,798 |  |
| 0.8× | 30,492 | 3,893 | -26.6 | 3,401 | 26,626 | -8,411 |  |
| 0.9× | 33,579 | 4,380 | -29.9 | 3,154 | 29,229 | -10,188 |  |
| 1.0× | 36,385 | 4,866 | -33.3 | 2,995 | 31,552 | -12,244 |  |
| 1.1× | 39,044 | 5,353 | -36.6 | 2,937 | 33,728 | -14,449 |  |
| 1.2× | 41,451 | 5,840 | -39.9 | 2,987 | 35,651 | -16,905 |  |
| 1.3× | 43,624 | 6,326 | -43.3 | 3,139 | 37,341 | -19,595 |  |
| 1.4× | 45,615 | 6,813 | -46.6 | 3,379 | 38,848 | -22,467 |  |
| 1.5× | 47,478 | 7,299 | -49.9 | 3,691 | 40,229 | -25,466 | minimum risk · balanced (λ = 1) |

**1M · Equity/es**

| m | Risk reduction | Profit sacrificed | Cost | Basis | U(λ=1) | U(λ=10) | Point |
|---|---|---|---|---|---|---|---|
| 0.5× | 20,255 | 2,433 | -16.6 | 4,498 | 17,838 | -4,060 | profit-preserving (λ = 10) |
| 0.6× | 23,827 | 2,920 | -20 | 4,088 | 20,928 | -5,350 |  |
| 0.7× | 27,243 | 3,406 | -23.3 | 3,717 | 23,860 | -6,798 |  |
| 0.8× | 30,492 | 3,893 | -26.6 | 3,401 | 26,626 | -8,411 |  |
| 0.9× | 33,579 | 4,380 | -29.9 | 3,154 | 29,229 | -10,188 |  |
| 1.0× | 36,385 | 4,866 | -33.3 | 2,995 | 31,552 | -12,244 |  |
| 1.1× | 39,044 | 5,353 | -36.6 | 2,937 | 33,728 | -14,449 |  |
| 1.2× | 41,451 | 5,840 | -39.9 | 2,987 | 35,651 | -16,905 |  |
| 1.3× | 43,624 | 6,326 | -43.3 | 3,139 | 37,341 | -19,595 |  |
| 1.4× | 45,615 | 6,813 | -46.6 | 3,379 | 38,848 | -22,467 |  |
| 1.5× | 47,478 | 7,299 | -49.9 | 3,691 | 40,229 | -25,466 | minimum risk · balanced (λ = 1) |

**1M · Equity/min_variance**

| m | Risk reduction | Profit sacrificed | Cost | Basis | U(λ=1) | U(λ=10) | Point |
|---|---|---|---|---|---|---|---|
| 0.5× | 19,927 | 5,374 | -85.6 | 8,784 | 14,638 | -33,728 | profit-preserving (λ = 10) |
| 0.6× | 23,197 | 6,449 | -103 | 7,925 | 16,851 | -41,189 |  |
| 0.7× | 26,068 | 7,524 | -120 | 7,152 | 18,664 | -49,049 |  |
| 0.8× | 28,441 | 8,598 | -137 | 6,498 | 19,980 | -57,407 |  |
| 0.9× | 30,207 | 9,673 | -154 | 6,000 | 20,688 | -66,372 | balanced (λ = 1) |
| 1.0× | 31,262 | 10,748 | -171 | 5,700 | 20,685 | -76,047 |  |
| 1.1× | 31,536 | 11,823 | -188 | 5,630 | 19,902 | -86,505 | minimum risk |
| 1.2× | 31,008 | 12,898 | -205 | 5,798 | 18,316 | -97,764 |  |
| 1.3× | 29,716 | 13,973 | -223 | 6,184 | 15,966 | -109,787 |  |
| 1.4× | 27,745 | 15,047 | -240 | 6,752 | 12,937 | -122,489 |  |
| 1.5× | 25,201 | 16,122 | -257 | 7,460 | 9,335 | -135,764 |  |

**1M · Equity/name**

| m | Risk reduction | Profit sacrificed | Cost | Basis | U(λ=1) | U(λ=10) | Point |
|---|---|---|---|---|---|---|---|
| 0.5× | 14,453 | 2,784 | 326 | 3,614 | 11,343 | -13,716 | profit-preserving (λ = 10) |
| 0.6× | 17,228 | 3,341 | 391 | 3,214 | 13,496 | -16,574 |  |
| 0.7× | 19,947 | 3,898 | 456 | 2,945 | 15,593 | -19,489 |  |
| 0.8× | 22,600 | 4,455 | 521 | 2,844 | 17,624 | -22,470 |  |
| 0.9× | 25,173 | 5,012 | 586 | 2,929 | 19,575 | -25,531 |  |
| 1.0× | 27,648 | 5,569 | 652 | 3,185 | 21,428 | -28,689 |  |
| 1.1× | 30,005 | 6,125 | 717 | 3,574 | 23,163 | -31,966 |  |
| 1.2× | 32,218 | 6,682 | 782 | 4,060 | 24,754 | -35,387 |  |
| 1.3× | 34,254 | 7,239 | 847 | 4,611 | 26,168 | -38,985 |  |
| 1.4× | 36,074 | 7,796 | 912 | 5,207 | 27,366 | -42,798 |  |
| 1.5× | 37,635 | 8,353 | 977 | 5,834 | 28,305 | -46,871 | minimum risk · balanced (λ = 1) |

**1M · Equity/sector**

| m | Risk reduction | Profit sacrificed | Cost | Basis | U(λ=1) | U(λ=10) | Point |
|---|---|---|---|---|---|---|---|
| 0.5× | 13,252 | 2,105 | 575 | 3,185 | 10,572 | -8,371 | profit-preserving (λ = 10) |
| 0.6× | 15,631 | 2,526 | 690 | 3,533 | 12,415 | -10,317 |  |
| 0.7× | 17,886 | 2,947 | 805 | 3,999 | 14,134 | -12,385 |  |
| 0.8× | 20,001 | 3,368 | 920 | 4,547 | 15,713 | -14,595 |  |
| 0.9× | 21,955 | 3,789 | 1,035 | 5,150 | 17,131 | -16,966 |  |
| 1.0× | 23,726 | 4,210 | 1,150 | 5,791 | 18,366 | -19,520 |  |
| 1.1× | 25,289 | 4,630 | 1,266 | 6,459 | 19,393 | -22,281 |  |
| 1.2× | 26,622 | 5,051 | 1,381 | 7,146 | 20,190 | -25,272 |  |
| 1.3× | 27,700 | 5,472 | 1,496 | 7,848 | 20,732 | -28,519 |  |
| 1.4× | 28,501 | 5,893 | 1,611 | 8,560 | 20,997 | -32,043 | balanced (λ = 1) |
| 1.5× | 29,007 | 6,314 | 1,726 | 9,281 | 20,968 | -35,861 | minimum risk |

**1M · Equity/systematic**

| m | Risk reduction | Profit sacrificed | Cost | Basis | U(λ=1) | U(λ=10) | Point |
|---|---|---|---|---|---|---|---|
| 0.5× | 13,019 | 2,780 | 299 | 3,662 | 9,940 | -15,084 | profit-preserving (λ = 10) |
| 0.6× | 15,444 | 3,337 | 358 | 3,198 | 11,749 | -18,280 |  |
| 0.7× | 17,786 | 3,893 | 418 | 2,845 | 13,476 | -21,558 |  |
| 0.8× | 20,033 | 4,449 | 478 | 2,646 | 15,106 | -24,932 |  |
| 0.9× | 22,167 | 5,005 | 538 | 2,639 | 16,625 | -28,418 |  |
| 1.0× | 24,170 | 5,561 | 597 | 2,823 | 18,012 | -32,036 |  |
| 1.1× | 26,022 | 6,117 | 657 | 3,167 | 19,248 | -35,805 |  |
| 1.2× | 27,697 | 6,673 | 717 | 3,624 | 20,307 | -39,750 |  |
| 1.3× | 29,170 | 7,229 | 776 | 4,157 | 21,164 | -43,898 |  |
| 1.4× | 30,412 | 7,785 | 836 | 4,742 | 21,791 | -48,276 |  |
| 1.5× | 31,397 | 8,341 | 896 | 5,360 | 22,160 | -52,912 | minimum risk · balanced (λ = 1) |

**1M · Equity/target_vol**

| m | Risk reduction | Profit sacrificed | Cost | Basis | U(λ=1) | U(λ=10) | Point |
|---|---|---|---|---|---|---|---|
| 0.5× | 12,656 | 2,949 | -17.8 | 5,314 | 9,725 | -16,817 | profit-preserving (λ = 10) |
| 0.6× | 14,886 | 3,539 | -21.4 | 4,776 | 11,368 | -20,482 |  |
| 0.7× | 16,978 | 4,129 | -24.9 | 4,294 | 12,875 | -24,284 |  |
| 0.8× | 18,915 | 4,719 | -28.5 | 3,887 | 14,225 | -28,242 |  |
| 0.9× | 20,673 | 5,308 | -32 | 3,583 | 15,397 | -32,378 |  |
| 1.0× | 22,229 | 5,898 | -35.6 | 3,408 | 16,367 | -36,716 |  |
| 1.1× | 23,560 | 6,488 | -39.2 | 3,383 | 17,111 | -41,281 |  |
| 1.2× | 24,640 | 7,078 | -42.7 | 3,510 | 17,605 | -46,095 |  |
| 1.3× | 25,448 | 7,668 | -46.3 | 3,775 | 17,827 | -51,181 | balanced (λ = 1) |
| 1.4× | 25,967 | 8,257 | -49.8 | 4,151 | 17,759 | -56,558 |  |
| 1.5× | 26,182 | 8,847 | -53.4 | 4,611 | 17,388 | -62,237 | minimum risk |

**1M · Equity/var**

| m | Risk reduction | Profit sacrificed | Cost | Basis | U(λ=1) | U(λ=10) | Point |
|---|---|---|---|---|---|---|---|
| 0.5× | 20,255 | 2,433 | -16.6 | 4,498 | 17,838 | -4,060 | profit-preserving (λ = 10) |
| 0.6× | 23,827 | 2,920 | -20 | 4,088 | 20,928 | -5,350 |  |
| 0.7× | 27,243 | 3,406 | -23.3 | 3,717 | 23,860 | -6,798 |  |
| 0.8× | 30,492 | 3,893 | -26.6 | 3,401 | 26,626 | -8,411 |  |
| 0.9× | 33,579 | 4,380 | -29.9 | 3,154 | 29,229 | -10,188 |  |
| 1.0× | 36,385 | 4,866 | -33.3 | 2,995 | 31,552 | -12,244 |  |
| 1.1× | 39,044 | 5,353 | -36.6 | 2,937 | 33,728 | -14,449 |  |
| 1.2× | 41,451 | 5,840 | -39.9 | 2,987 | 35,651 | -16,905 |  |
| 1.3× | 43,624 | 6,326 | -43.3 | 3,139 | 37,341 | -19,595 |  |
| 1.4× | 45,615 | 6,813 | -46.6 | 3,379 | 38,848 | -22,467 |  |
| 1.5× | 47,478 | 7,299 | -49.9 | 3,691 | 40,229 | -25,466 | minimum risk · balanced (λ = 1) |

**1M · FX/fx**

| m | Risk reduction | Profit sacrificed | Cost | Basis | U(λ=1) | U(λ=10) | Point |
|---|---|---|---|---|---|---|---|
| 0.5× | 431 | -12.8 | 4.49 | 295 | 439 | 554 |  |
| 0.6× | 513 | -15.3 | 5.38 | 247 | 523 | 661 |  |
| 0.7× | 594 | -17.9 | 6.28 | 204 | 605 | 766 |  |
| 0.8× | 673 | -20.4 | 7.18 | 168 | 686 | 870 |  |
| 0.9× | 751 | -23 | 8.08 | 145 | 766 | 973 |  |
| 1.0× | 828 | -25.5 | 8.97 | 142 | 844 | 1,074 |  |
| 1.1× | 903 | -28.1 | 9.87 | 159 | 921 | 1,174 |  |
| 1.2× | 977 | -30.6 | 10.8 | 192 | 997 | 1,273 |  |
| 1.3× | 1,049 | -33.2 | 11.7 | 233 | 1,071 | 1,370 |  |
| 1.4× | 1,121 | -35.7 | 12.6 | 280 | 1,144 | 1,465 |  |
| 1.5× | 1,190 | -38.3 | 13.5 | 329 | 1,215 | 1,560 | minimum risk · balanced (λ = 1) · profit-preserving (λ = 10) |

**1M · Rates/curve**

| m | Risk reduction | Profit sacrificed | Cost | Basis | U(λ=1) | U(λ=10) | Point |
|---|---|---|---|---|---|---|---|
| 0.5× | 844 | 78.3 | 28.2 | 1,506 | 737 | 33.2 | profit-preserving (λ = 10) |
| 0.6× | 983 | 93.9 | 33.9 | 1,564 | 856 | 10.6 |  |
| 0.7× | 1,113 | 110 | 39.5 | 1,630 | 964 | -22.2 |  |
| 0.8× | 1,232 | 125 | 45.1 | 1,703 | 1,062 | -65.1 |  |
| 0.9× | 1,341 | 141 | 50.8 | 1,782 | 1,149 | -118 |  |
| 1.0× | 1,439 | 157 | 56.4 | 1,867 | 1,226 | -182 |  |
| 1.1× | 1,527 | 172 | 62.1 | 1,956 | 1,293 | -257 |  |
| 1.2× | 1,604 | 188 | 67.7 | 2,049 | 1,349 | -342 |  |
| 1.3× | 1,671 | 203 | 73.4 | 2,146 | 1,394 | -437 |  |
| 1.4× | 1,726 | 219 | 79 | 2,246 | 1,428 | -544 |  |
| 1.5× | 1,771 | 235 | 84.6 | 2,348 | 1,452 | -661 | minimum risk · balanced (λ = 1) |

**1M · Rates/drawdown**

| m | Risk reduction | Profit sacrificed | Cost | Basis | U(λ=1) | U(λ=10) | Point |
|---|---|---|---|---|---|---|---|
| 0.5× | 3,911 | -187 | 13.3 | 2,173 | 4,085 | 5,767 |  |
| 0.6× | 4,579 | -224 | 15.9 | 2,203 | 4,787 | 6,806 |  |
| 0.7× | 5,246 | -262 | 18.6 | 2,237 | 5,489 | 7,844 |  |
| 0.8× | 5,894 | -299 | 21.2 | 2,276 | 6,171 | 8,863 |  |
| 0.9× | 6,468 | -336 | 23.9 | 2,320 | 6,781 | 9,809 |  |
| 1.0× | 7,043 | -374 | 26.5 | 2,367 | 7,390 | 10,755 |  |
| 1.1× | 7,618 | -411 | 29.2 | 2,419 | 8,000 | 11,701 |  |
| 1.2× | 8,192 | -449 | 31.8 | 2,474 | 8,609 | 12,647 |  |
| 1.3× | 8,731 | -486 | 34.5 | 2,532 | 9,183 | 13,557 |  |
| 1.4× | 9,216 | -523 | 37.1 | 2,594 | 9,702 | 14,413 |  |
| 1.5× | 9,598 | -561 | 39.8 | 2,658 | 10,119 | 15,166 | minimum risk · balanced (λ = 1) · profit-preserving (λ = 10) |

**1M · Rates/duration**

| m | Risk reduction | Profit sacrificed | Cost | Basis | U(λ=1) | U(λ=10) | Point |
|---|---|---|---|---|---|---|---|
| 0.5× | 872 | 83.1 | 32.1 | 1,503 | 757 | 8.43 | profit-preserving (λ = 10) |
| 0.6× | 1,011 | 99.8 | 38.5 | 1,565 | 873 | -24.8 |  |
| 0.7× | 1,139 | 116 | 44.9 | 1,637 | 977 | -70 |  |
| 0.8× | 1,254 | 133 | 51.4 | 1,717 | 1,070 | -127 |  |
| 0.9× | 1,357 | 150 | 57.8 | 1,804 | 1,150 | -197 |  |
| 1.0× | 1,448 | 166 | 64.2 | 1,897 | 1,217 | -279 |  |
| 1.1× | 1,526 | 183 | 70.6 | 1,995 | 1,272 | -374 |  |
| 1.2× | 1,591 | 200 | 77.1 | 2,097 | 1,314 | -482 |  |
| 1.3× | 1,643 | 216 | 83.5 | 2,204 | 1,344 | -602 |  |
| 1.4× | 1,683 | 233 | 89.9 | 2,313 | 1,360 | -735 |  |
| 1.5× | 1,710 | 249 | 96.3 | 2,426 | 1,364 | -880 | minimum risk · balanced (λ = 1) |

**1M · Rates/es**

| m | Risk reduction | Profit sacrificed | Cost | Basis | U(λ=1) | U(λ=10) | Point |
|---|---|---|---|---|---|---|---|
| 0.5× | 3,911 | -187 | 13.3 | 2,173 | 4,085 | 5,767 |  |
| 0.6× | 4,579 | -224 | 15.9 | 2,203 | 4,787 | 6,806 |  |
| 0.7× | 5,246 | -262 | 18.6 | 2,237 | 5,489 | 7,844 |  |
| 0.8× | 5,894 | -299 | 21.2 | 2,276 | 6,171 | 8,863 |  |
| 0.9× | 6,468 | -336 | 23.9 | 2,320 | 6,781 | 9,809 |  |
| 1.0× | 7,043 | -374 | 26.5 | 2,367 | 7,390 | 10,755 |  |
| 1.1× | 7,618 | -411 | 29.2 | 2,419 | 8,000 | 11,701 |  |
| 1.2× | 8,192 | -449 | 31.8 | 2,474 | 8,609 | 12,647 |  |
| 1.3× | 8,731 | -486 | 34.5 | 2,532 | 9,183 | 13,557 |  |
| 1.4× | 9,216 | -523 | 37.1 | 2,594 | 9,702 | 14,413 |  |
| 1.5× | 9,598 | -561 | 39.8 | 2,658 | 10,119 | 15,166 | minimum risk · balanced (λ = 1) · profit-preserving (λ = 10) |

**1M · Rates/min_variance**

| m | Risk reduction | Profit sacrificed | Cost | Basis | U(λ=1) | U(λ=10) | Point |
|---|---|---|---|---|---|---|---|
| 0.5× | 5,291 | -113 | 48.6 | 4,464 | 5,355 | 6,369 |  |
| 0.6× | 6,205 | -135 | 58.3 | 4,569 | 6,282 | 7,498 |  |
| 0.7× | 7,045 | -158 | 68 | 4,688 | 7,135 | 8,554 |  |
| 0.8× | 7,796 | -180 | 77.8 | 4,822 | 7,898 | 9,520 |  |
| 0.9× | 8,440 | -203 | 87.5 | 4,970 | 8,555 | 10,379 |  |
| 1.0× | 8,957 | -225 | 97.2 | 5,128 | 9,085 | 11,112 |  |
| 1.1× | 9,329 | -248 | 107 | 5,298 | 9,470 | 11,700 |  |
| 1.2× | 9,541 | -270 | 117 | 5,478 | 9,694 | 12,127 |  |
| 1.3× | 9,581 | -293 | 126 | 5,666 | 9,747 | 12,383 | minimum risk · balanced (λ = 1) |
| 1.4× | 9,448 | -315 | 136 | 5,863 | 9,627 | 12,466 | profit-preserving (λ = 10) |
| 1.5× | 9,149 | -338 | 146 | 6,066 | 9,341 | 12,382 |  |

**1M · Rates/name**

| m | Risk reduction | Profit sacrificed | Cost | Basis | U(λ=1) | U(λ=10) | Point |
|---|---|---|---|---|---|---|---|
| 0.5× | 971 | -30.8 | 46.3 | 708 | 955 | 1,232 |  |
| 0.6× | 1,152 | -37 | 55.6 | 711 | 1,133 | 1,466 |  |
| 0.7× | 1,328 | -43.1 | 64.8 | 725 | 1,306 | 1,695 |  |
| 0.8× | 1,500 | -49.3 | 74.1 | 747 | 1,475 | 1,919 |  |
| 0.9× | 1,667 | -55.5 | 83.4 | 779 | 1,639 | 2,138 |  |
| 1.0× | 1,829 | -61.6 | 92.6 | 818 | 1,798 | 2,353 |  |
| 1.1× | 1,986 | -67.8 | 102 | 863 | 1,952 | 2,562 |  |
| 1.2× | 2,138 | -74 | 111 | 914 | 2,101 | 2,766 |  |
| 1.3× | 2,285 | -80.1 | 120 | 969 | 2,244 | 2,965 |  |
| 1.4× | 2,426 | -86.3 | 130 | 1,029 | 2,382 | 3,159 |  |
| 1.5× | 2,562 | -92.5 | 139 | 1,092 | 2,515 | 3,347 | minimum risk · balanced (λ = 1) · profit-preserving (λ = 10) |

**1M · Rates/systematic**

| m | Risk reduction | Profit sacrificed | Cost | Basis | U(λ=1) | U(λ=10) | Point |
|---|---|---|---|---|---|---|---|
| 0.5× | 3,280 | 261 | 60.4 | 2,140 | 2,958 | 605 |  |
| 0.6× | 3,885 | 314 | 72.4 | 2,229 | 3,499 | 675 |  |
| 0.7× | 4,468 | 366 | 84.5 | 2,330 | 4,018 | 723 |  |
| 0.8× | 5,027 | 418 | 96.6 | 2,441 | 4,513 | 747 | profit-preserving (λ = 10) |
| 0.9× | 5,560 | 471 | 109 | 2,561 | 4,980 | 744 |  |
| 1.0× | 6,062 | 523 | 121 | 2,689 | 5,418 | 712 |  |
| 1.1× | 6,531 | 575 | 133 | 2,823 | 5,823 | 646 |  |
| 1.2× | 6,963 | 628 | 145 | 2,963 | 6,190 | 543 |  |
| 1.3× | 7,355 | 680 | 157 | 3,109 | 6,518 | 399 |  |
| 1.4× | 7,702 | 732 | 169 | 3,258 | 6,801 | 212 |  |
| 1.5× | 8,002 | 784 | 181 | 3,411 | 7,036 | -23.8 | minimum risk · balanced (λ = 1) |

**1M · Rates/target_vol**

| m | Risk reduction | Profit sacrificed | Cost | Basis | U(λ=1) | U(λ=10) | Point |
|---|---|---|---|---|---|---|---|
| 0.5× | 0 | -0 | 0 | 182 | 0 | 0 | minimum risk · balanced (λ = 1) · profit-preserving (λ = 10) |
| 0.6× | 0 | -0 | 0 | 182 | 0 | 0 |  |
| 0.7× | 0 | -0 | 0 | 182 | 0 | 0 |  |
| 0.8× | 0 | -0 | 0 | 182 | 0 | 0 |  |
| 0.9× | 0 | -0 | 0 | 182 | 0 | 0 |  |
| 1.0× | 0 | -0 | 0 | 182 | 0 | 0 |  |
| 1.1× | 0 | -0 | 0 | 182 | 0 | 0 |  |
| 1.2× | 0 | -0 | 0 | 182 | 0 | 0 |  |
| 1.3× | 0 | -0 | 0 | 182 | 0 | 0 |  |
| 1.4× | 0 | -0 | 0 | 182 | 0 | 0 |  |
| 1.5× | 0 | -0 | 0 | 182 | 0 | 0 |  |

**1M · Rates/var**

| m | Risk reduction | Profit sacrificed | Cost | Basis | U(λ=1) | U(λ=10) | Point |
|---|---|---|---|---|---|---|---|
| 0.5× | 3,911 | -187 | 13.3 | 2,173 | 4,085 | 5,767 |  |
| 0.6× | 4,579 | -224 | 15.9 | 2,203 | 4,787 | 6,806 |  |
| 0.7× | 5,246 | -262 | 18.6 | 2,237 | 5,489 | 7,844 |  |
| 0.8× | 5,894 | -299 | 21.2 | 2,276 | 6,171 | 8,863 |  |
| 0.9× | 6,468 | -336 | 23.9 | 2,320 | 6,781 | 9,809 |  |
| 1.0× | 7,043 | -374 | 26.5 | 2,367 | 7,390 | 10,755 |  |
| 1.1× | 7,618 | -411 | 29.2 | 2,419 | 8,000 | 11,701 |  |
| 1.2× | 8,192 | -449 | 31.8 | 2,474 | 8,609 | 12,647 |  |
| 1.3× | 8,731 | -486 | 34.5 | 2,532 | 9,183 | 13,557 |  |
| 1.4× | 9,216 | -523 | 37.1 | 2,594 | 9,702 | 14,413 |  |
| 1.5× | 9,598 | -561 | 39.8 | 2,658 | 10,119 | 15,166 | minimum risk · balanced (λ = 1) · profit-preserving (λ = 10) |

**1M · Volatility/volatility**

| m | Risk reduction | Profit sacrificed | Cost | Basis | U(λ=1) | U(λ=10) | Point |
|---|---|---|---|---|---|---|---|
| 0.5× | 1,730 | 903 | 263 | 741 | 565 | -7,559 | profit-preserving (λ = 10) |
| 0.6× | 2,061 | 1,083 | 315 | 864 | 663 | -9,085 |  |
| 0.7× | 2,387 | 1,264 | 368 | 1,002 | 756 | -10,616 |  |
| 0.8× | 2,708 | 1,444 | 420 | 1,148 | 844 | -12,153 |  |
| 0.9× | 3,024 | 1,625 | 473 | 1,301 | 926 | -13,695 |  |
| 1.0× | 3,334 | 1,805 | 525 | 1,458 | 1,003 | -15,243 |  |
| 1.1× | 3,638 | 1,986 | 578 | 1,618 | 1,075 | -16,796 |  |
| 1.2× | 3,937 | 2,166 | 630 | 1,780 | 1,140 | -18,355 |  |
| 1.3× | 4,230 | 2,347 | 683 | 1,944 | 1,200 | -19,920 |  |
| 1.4× | 4,517 | 2,527 | 735 | 2,109 | 1,254 | -21,491 |  |
| 1.5× | 4,798 | 2,708 | 788 | 2,275 | 1,302 | -23,067 | minimum risk · balanced (λ = 1) |

**3M · Commodity/commodity**

| m | Risk reduction | Profit sacrificed | Cost | Basis | U(λ=1) | U(λ=10) | Point |
|---|---|---|---|---|---|---|---|
| 0.5× | 2,334 | 1,306 | 583 | 1,547 | 445 | -11,313 | balanced (λ = 1) · profit-preserving (λ = 10) |
| 0.6× | 2,703 | 1,568 | 700 | 1,480 | 436 | -13,673 |  |
| 0.7× | 3,039 | 1,829 | 816 | 1,430 | 393 | -16,067 |  |
| 0.8× | 3,339 | 2,090 | 933 | 1,401 | 316 | -18,495 |  |
| 0.9× | 3,605 | 2,351 | 1,049 | 1,393 | 204 | -20,959 |  |
| 1.0× | 3,836 | 2,613 | 1,166 | 1,408 | 57.1 | -23,458 |  |
| 1.1× | 4,030 | 2,874 | 1,283 | 1,443 | -126 | -25,993 |  |
| 1.2× | 4,189 | 3,135 | 1,399 | 1,499 | -346 | -28,564 |  |
| 1.3× | 4,311 | 3,397 | 1,516 | 1,571 | -602 | -31,171 |  |
| 1.4× | 4,396 | 3,658 | 1,632 | 1,660 | -894 | -33,815 |  |
| 1.5× | 4,444 | 3,919 | 1,749 | 1,761 | -1,224 | -36,496 | minimum risk |

**3M · Commodity/drawdown**

| m | Risk reduction | Profit sacrificed | Cost | Basis | U(λ=1) | U(λ=10) | Point |
|---|---|---|---|---|---|---|---|
| 0.5× | 6,204 | 1,729 | -3.67 | 3,775 | 4,478 | -11,082 | profit-preserving (λ = 10) |
| 0.6× | 7,188 | 2,075 | -4.4 | 3,726 | 5,118 | -13,554 |  |
| 0.7× | 8,173 | 2,420 | -5.13 | 3,684 | 5,758 | -16,027 |  |
| 0.8× | 9,158 | 2,766 | -5.87 | 3,650 | 6,397 | -18,499 |  |
| 0.9× | 10,142 | 3,112 | -6.6 | 3,624 | 7,037 | -20,972 |  |
| 1.0× | 11,127 | 3,458 | -7.33 | 3,606 | 7,676 | -23,444 |  |
| 1.1× | 12,112 | 3,804 | -8.07 | 3,596 | 8,316 | -25,916 |  |
| 1.2× | 13,096 | 4,149 | -8.8 | 3,594 | 8,956 | -28,389 |  |
| 1.3× | 14,081 | 4,495 | -9.53 | 3,601 | 9,595 | -30,861 |  |
| 1.4× | 15,066 | 4,841 | -10.3 | 3,616 | 10,235 | -33,334 |  |
| 1.5× | 16,050 | 5,187 | -11 | 3,638 | 10,875 | -35,806 | minimum risk · balanced (λ = 1) |

**3M · Commodity/es**

| m | Risk reduction | Profit sacrificed | Cost | Basis | U(λ=1) | U(λ=10) | Point |
|---|---|---|---|---|---|---|---|
| 0.5× | 6,204 | 1,729 | -3.67 | 3,775 | 4,478 | -11,082 | profit-preserving (λ = 10) |
| 0.6× | 7,188 | 2,075 | -4.4 | 3,726 | 5,118 | -13,554 |  |
| 0.7× | 8,173 | 2,420 | -5.13 | 3,684 | 5,758 | -16,027 |  |
| 0.8× | 9,158 | 2,766 | -5.87 | 3,650 | 6,397 | -18,499 |  |
| 0.9× | 10,142 | 3,112 | -6.6 | 3,624 | 7,037 | -20,972 |  |
| 1.0× | 11,127 | 3,458 | -7.33 | 3,606 | 7,676 | -23,444 |  |
| 1.1× | 12,112 | 3,804 | -8.07 | 3,596 | 8,316 | -25,916 |  |
| 1.2× | 13,096 | 4,149 | -8.8 | 3,594 | 8,956 | -28,389 |  |
| 1.3× | 14,081 | 4,495 | -9.53 | 3,601 | 9,595 | -30,861 |  |
| 1.4× | 15,066 | 4,841 | -10.3 | 3,616 | 10,235 | -33,334 |  |
| 1.5× | 16,050 | 5,187 | -11 | 3,638 | 10,875 | -35,806 | minimum risk · balanced (λ = 1) |

**3M · Commodity/min_variance**

| m | Risk reduction | Profit sacrificed | Cost | Basis | U(λ=1) | U(λ=10) | Point |
|---|---|---|---|---|---|---|---|
| 0.5× | 7,690 | 3,514 | -15.2 | 7,435 | 4,191 | -27,436 | profit-preserving (λ = 10) |
| 0.6× | 8,912 | 4,217 | -18.2 | 7,322 | 4,713 | -33,240 |  |
| 0.7× | 10,010 | 4,920 | -21.2 | 7,227 | 5,111 | -39,167 |  |
| 0.8× | 10,976 | 5,623 | -24.2 | 7,150 | 5,378 | -45,226 |  |
| 0.9× | 11,804 | 6,325 | -27.3 | 7,094 | 5,506 | -51,423 | balanced (λ = 1) |
| 1.0× | 12,487 | 7,028 | -30.3 | 7,057 | 5,489 | -57,766 |  |
| 1.1× | 13,018 | 7,731 | -33.3 | 7,041 | 5,320 | -64,260 |  |
| 1.2× | 13,393 | 8,434 | -36.4 | 7,045 | 4,995 | -70,911 |  |
| 1.3× | 13,608 | 9,137 | -39.4 | 7,069 | 4,510 | -77,721 |  |
| 1.4× | 13,661 | 9,840 | -42.4 | 7,114 | 3,864 | -84,693 | minimum risk |
| 1.5× | 13,552 | 10,542 | -45.5 | 7,178 | 3,055 | -91,827 |  |

**3M · Commodity/systematic**

| m | Risk reduction | Profit sacrificed | Cost | Basis | U(λ=1) | U(λ=10) | Point |
|---|---|---|---|---|---|---|---|
| 0.5× | 10,597 | 2,327 | 1,130 | 2,955 | 7,140 | -13,800 | profit-preserving (λ = 10) |
| 0.6× | 12,536 | 2,792 | 1,356 | 2,814 | 8,387 | -16,740 |  |
| 0.7× | 14,397 | 3,257 | 1,583 | 2,716 | 9,557 | -19,759 |  |
| 0.8× | 16,171 | 3,723 | 1,809 | 2,668 | 10,640 | -22,864 |  |
| 0.9× | 17,849 | 4,188 | 2,035 | 2,671 | 11,626 | -26,065 |  |
| 1.0× | 19,419 | 4,653 | 2,261 | 2,726 | 12,505 | -29,374 |  |
| 1.1× | 20,870 | 5,119 | 2,487 | 2,829 | 13,264 | -32,803 |  |
| 1.2× | 22,188 | 5,584 | 2,713 | 2,975 | 13,892 | -36,364 |  |
| 1.3× | 23,362 | 6,049 | 2,939 | 3,159 | 14,374 | -40,069 |  |
| 1.4× | 24,377 | 6,515 | 3,165 | 3,375 | 14,698 | -43,933 |  |
| 1.5× | 25,222 | 6,980 | 3,391 | 3,616 | 14,851 | -47,968 | minimum risk · balanced (λ = 1) |

**3M · Commodity/var**

| m | Risk reduction | Profit sacrificed | Cost | Basis | U(λ=1) | U(λ=10) | Point |
|---|---|---|---|---|---|---|---|
| 0.5× | 6,204 | 1,729 | -3.67 | 3,775 | 4,478 | -11,082 | profit-preserving (λ = 10) |
| 0.6× | 7,188 | 2,075 | -4.4 | 3,726 | 5,118 | -13,554 |  |
| 0.7× | 8,173 | 2,420 | -5.13 | 3,684 | 5,758 | -16,027 |  |
| 0.8× | 9,158 | 2,766 | -5.87 | 3,650 | 6,397 | -18,499 |  |
| 0.9× | 10,142 | 3,112 | -6.6 | 3,624 | 7,037 | -20,972 |  |
| 1.0× | 11,127 | 3,458 | -7.33 | 3,606 | 7,676 | -23,444 |  |
| 1.1× | 12,112 | 3,804 | -8.07 | 3,596 | 8,316 | -25,916 |  |
| 1.2× | 13,096 | 4,149 | -8.8 | 3,594 | 8,956 | -28,389 |  |
| 1.3× | 14,081 | 4,495 | -9.53 | 3,601 | 9,595 | -30,861 |  |
| 1.4× | 15,066 | 4,841 | -10.3 | 3,616 | 10,235 | -33,334 |  |
| 1.5× | 16,050 | 5,187 | -11 | 3,638 | 10,875 | -35,806 | minimum risk · balanced (λ = 1) |

**3M · Credit/credit**

| m | Risk reduction | Profit sacrificed | Cost | Basis | U(λ=1) | U(λ=10) | Point |
|---|---|---|---|---|---|---|---|
| 0.5× | 2,165 | -93.7 | 589 | 963 | 1,670 | 2,513 |  |
| 0.6× | 2,526 | -112 | 707 | 1,070 | 1,932 | 2,943 |  |
| 0.7× | 2,862 | -131 | 824 | 1,186 | 2,169 | 3,349 |  |
| 0.8× | 3,173 | -150 | 942 | 1,308 | 2,380 | 3,729 |  |
| 0.9× | 3,457 | -169 | 1,060 | 1,435 | 2,566 | 4,083 |  |
| 1.0× | 3,716 | -187 | 1,178 | 1,565 | 2,725 | 4,411 |  |
| 1.1× | 3,947 | -206 | 1,296 | 1,698 | 2,858 | 4,712 |  |
| 1.2× | 4,152 | -225 | 1,413 | 1,834 | 2,963 | 4,986 |  |
| 1.3× | 4,329 | -244 | 1,531 | 1,970 | 3,041 | 5,233 |  |
| 1.4× | 4,478 | -262 | 1,649 | 2,109 | 3,092 | 5,452 |  |
| 1.5× | 4,600 | -281 | 1,767 | 2,248 | 3,114 | 5,643 | minimum risk · balanced (λ = 1) · profit-preserving (λ = 10) |

**3M · Credit/drawdown**

| m | Risk reduction | Profit sacrificed | Cost | Basis | U(λ=1) | U(λ=10) | Point |
|---|---|---|---|---|---|---|---|
| 0.5× | 2,180 | -509 | 6.88 | 1,415 | 2,682 | 7,260 |  |
| 0.6× | 2,616 | -610 | 8.25 | 1,428 | 3,218 | 8,712 |  |
| 0.7× | 3,052 | -712 | 9.63 | 1,443 | 3,754 | 10,163 |  |
| 0.8× | 3,488 | -814 | 11 | 1,460 | 4,291 | 11,615 |  |
| 0.9× | 3,924 | -916 | 12.4 | 1,479 | 4,827 | 13,067 |  |
| 1.0× | 4,360 | -1,017 | 13.8 | 1,501 | 5,363 | 14,519 |  |
| 1.1× | 4,573 | -1,119 | 15.1 | 1,524 | 5,677 | 15,748 |  |
| 1.2× | 4,577 | -1,221 | 16.5 | 1,549 | 5,781 | 16,768 |  |
| 1.3× | 4,581 | -1,322 | 17.9 | 1,576 | 5,886 | 17,788 |  |
| 1.4× | 4,585 | -1,424 | 19.3 | 1,605 | 5,990 | 18,808 |  |
| 1.5× | 4,590 | -1,526 | 20.6 | 1,635 | 6,095 | 19,829 | minimum risk · balanced (λ = 1) · profit-preserving (λ = 10) |

**3M · Credit/es**

| m | Risk reduction | Profit sacrificed | Cost | Basis | U(λ=1) | U(λ=10) | Point |
|---|---|---|---|---|---|---|---|
| 0.5× | 2,180 | -509 | 6.88 | 1,415 | 2,682 | 7,260 |  |
| 0.6× | 2,616 | -610 | 8.25 | 1,428 | 3,218 | 8,712 |  |
| 0.7× | 3,052 | -712 | 9.63 | 1,443 | 3,754 | 10,163 |  |
| 0.8× | 3,488 | -814 | 11 | 1,460 | 4,291 | 11,615 |  |
| 0.9× | 3,924 | -916 | 12.4 | 1,479 | 4,827 | 13,067 |  |
| 1.0× | 4,360 | -1,017 | 13.8 | 1,501 | 5,363 | 14,519 |  |
| 1.1× | 4,573 | -1,119 | 15.1 | 1,524 | 5,677 | 15,748 |  |
| 1.2× | 4,577 | -1,221 | 16.5 | 1,549 | 5,781 | 16,768 |  |
| 1.3× | 4,581 | -1,322 | 17.9 | 1,576 | 5,886 | 17,788 |  |
| 1.4× | 4,585 | -1,424 | 19.3 | 1,605 | 5,990 | 18,808 |  |
| 1.5× | 4,590 | -1,526 | 20.6 | 1,635 | 6,095 | 19,829 | minimum risk · balanced (λ = 1) · profit-preserving (λ = 10) |

**3M · Credit/min_variance**

| m | Risk reduction | Profit sacrificed | Cost | Basis | U(λ=1) | U(λ=10) | Point |
|---|---|---|---|---|---|---|---|
| 0.5× | 1,516 | -884 | 26.8 | 2,842 | 2,373 | 10,329 |  |
| 0.6× | 1,736 | -1,061 | 32.2 | 2,875 | 2,765 | 12,312 |  |
| 0.7× | 1,927 | -1,238 | 37.5 | 2,914 | 3,127 | 14,266 |  |
| 0.8× | 2,088 | -1,414 | 42.9 | 2,959 | 3,460 | 16,189 |  |
| 0.9× | 2,219 | -1,591 | 48.2 | 3,010 | 3,762 | 18,083 |  |
| 1.0× | 2,319 | -1,768 | 53.6 | 3,065 | 4,033 | 19,946 |  |
| 1.1× | 2,388 | -1,945 | 59 | 3,126 | 4,274 | 21,777 |  |
| 1.2× | 2,426 | -2,122 | 64.3 | 3,192 | 4,483 | 23,578 |  |
| 1.3× | 2,432 | -2,298 | 69.7 | 3,262 | 4,661 | 25,347 | minimum risk |
| 1.4× | 2,407 | -2,475 | 75 | 3,337 | 4,807 | 27,085 |  |
| 1.5× | 2,351 | -2,652 | 80.4 | 3,415 | 4,923 | 28,791 | balanced (λ = 1) · profit-preserving (λ = 10) |

**3M · Credit/name**

| m | Risk reduction | Profit sacrificed | Cost | Basis | U(λ=1) | U(λ=10) | Point |
|---|---|---|---|---|---|---|---|
| 0.5× | 4,054 | 500 | 374 | 740 | 3,179 | -1,321 | profit-preserving (λ = 10) |
| 0.6× | 4,839 | 600 | 449 | 838 | 3,790 | -1,610 |  |
| 0.7× | 5,615 | 700 | 524 | 941 | 4,391 | -1,910 |  |
| 0.8× | 6,379 | 800 | 599 | 1,049 | 4,981 | -2,220 |  |
| 0.9× | 7,132 | 900 | 674 | 1,159 | 5,558 | -2,543 |  |
| 1.0× | 7,871 | 1,000 | 748 | 1,272 | 6,123 | -2,878 |  |
| 1.1× | 8,596 | 1,100 | 823 | 1,386 | 6,673 | -3,228 |  |
| 1.2× | 9,305 | 1,200 | 898 | 1,502 | 7,207 | -3,594 |  |
| 1.3× | 9,997 | 1,300 | 973 | 1,618 | 7,724 | -3,977 |  |
| 1.4× | 10,669 | 1,400 | 1,048 | 1,736 | 8,221 | -4,380 |  |
| 1.5× | 11,321 | 1,500 | 1,123 | 1,854 | 8,698 | -4,803 | minimum risk · balanced (λ = 1) |

**3M · Credit/var**

| m | Risk reduction | Profit sacrificed | Cost | Basis | U(λ=1) | U(λ=10) | Point |
|---|---|---|---|---|---|---|---|
| 0.5× | 2,180 | -509 | 6.88 | 1,415 | 2,682 | 7,260 |  |
| 0.6× | 2,616 | -610 | 8.25 | 1,428 | 3,218 | 8,712 |  |
| 0.7× | 3,052 | -712 | 9.63 | 1,443 | 3,754 | 10,163 |  |
| 0.8× | 3,488 | -814 | 11 | 1,460 | 4,291 | 11,615 |  |
| 0.9× | 3,924 | -916 | 12.4 | 1,479 | 4,827 | 13,067 |  |
| 1.0× | 4,360 | -1,017 | 13.8 | 1,501 | 5,363 | 14,519 |  |
| 1.1× | 4,573 | -1,119 | 15.1 | 1,524 | 5,677 | 15,748 |  |
| 1.2× | 4,577 | -1,221 | 16.5 | 1,549 | 5,781 | 16,768 |  |
| 1.3× | 4,581 | -1,322 | 17.9 | 1,576 | 5,886 | 17,788 |  |
| 1.4× | 4,585 | -1,424 | 19.3 | 1,605 | 5,990 | 18,808 |  |
| 1.5× | 4,590 | -1,526 | 20.6 | 1,635 | 6,095 | 19,829 | minimum risk · balanced (λ = 1) · profit-preserving (λ = 10) |

**3M · Equity/beta**

| m | Risk reduction | Profit sacrificed | Cost | Basis | U(λ=1) | U(λ=10) | Point |
|---|---|---|---|---|---|---|---|
| 0.5× | 14,001 | 8,261 | -94 | 2,884 | 5,834 | -68,516 | profit-preserving (λ = 10) |
| 0.6× | 16,562 | 9,913 | -113 | 2,539 | 6,762 | -82,458 |  |
| 0.7× | 19,026 | 11,566 | -132 | 2,286 | 7,592 | -96,498 |  |
| 0.8× | 21,383 | 13,218 | -150 | 2,156 | 8,315 | -110,645 |  |
| 0.9× | 23,623 | 14,870 | -169 | 2,171 | 8,923 | -124,907 |  |
| 1.0× | 25,737 | 16,522 | -188 | 2,329 | 9,403 | -139,297 |  |
| 1.1× | 27,714 | 18,174 | -207 | 2,603 | 9,746 | -153,824 |  |
| 1.2× | 29,541 | 19,827 | -226 | 2,962 | 9,940 | -168,500 |  |
| 1.3× | 31,209 | 21,479 | -244 | 3,379 | 9,974 | -183,336 | balanced (λ = 1) |
| 1.4× | 32,704 | 23,131 | -263 | 3,835 | 9,836 | -198,344 |  |
| 1.5× | 34,015 | 24,783 | -282 | 4,317 | 9,514 | -213,536 | minimum risk |

**3M · Equity/crash**

| m | Risk reduction | Profit sacrificed | Cost | Basis | U(λ=1) | U(λ=10) | Point |
|---|---|---|---|---|---|---|---|
| 0.5× | 14,303 | 8,136 | 2,649 | 2,979 | 3,518 | -69,702 | balanced (λ = 1) · profit-preserving (λ = 10) |
| 0.6× | 16,430 | 9,763 | 3,179 | 2,599 | 3,488 | -84,376 |  |
| 0.7× | 18,017 | 11,390 | 3,709 | 2,271 | 2,918 | -99,591 |  |
| 0.8× | 19,139 | 13,017 | 4,239 | 2,021 | 1,883 | -115,270 |  |
| 0.9× | 19,969 | 14,644 | 4,769 | 1,880 | 556 | -131,241 |  |
| 1.0× | 20,487 | 16,271 | 5,299 | 1,873 | -1,083 | -147,525 |  |
| 1.1× | 20,757 | 17,898 | 5,828 | 2,001 | -2,970 | -164,055 | minimum risk |
| 1.2× | 20,615 | 19,526 | 6,358 | 2,241 | -5,269 | -180,999 |  |
| 1.3× | 20,224 | 21,153 | 6,888 | 2,562 | -7,817 | -198,190 |  |
| 1.4× | 19,676 | 22,780 | 7,418 | 2,937 | -10,522 | -215,540 |  |
| 1.5× | 19,038 | 24,407 | 7,948 | 3,349 | -13,317 | -232,979 |  |

**3M · Equity/drawdown**

| m | Risk reduction | Profit sacrificed | Cost | Basis | U(λ=1) | U(λ=10) | Point |
|---|---|---|---|---|---|---|---|
| 0.5× | 30,128 | 7,624 | 31.4 | 4,410 | 22,472 | -46,146 | profit-preserving (λ = 10) |
| 0.6× | 35,470 | 9,149 | 37.7 | 3,983 | 26,284 | -56,059 |  |
| 0.7× | 40,393 | 10,674 | 44 | 3,602 | 29,675 | -66,391 |  |
| 0.8× | 44,997 | 12,199 | 50.2 | 3,281 | 32,748 | -77,042 |  |
| 0.9× | 49,448 | 13,724 | 56.5 | 3,040 | 35,668 | -87,845 |  |
| 1.0× | 53,246 | 15,249 | 62.8 | 2,900 | 37,935 | -99,302 |  |
| 1.1× | 56,742 | 16,773 | 69.1 | 2,874 | 39,899 | -111,061 |  |
| 1.2× | 60,168 | 18,298 | 75.4 | 2,967 | 41,794 | -122,890 |  |
| 1.3× | 63,249 | 19,823 | 81.6 | 3,167 | 43,345 | -135,063 |  |
| 1.4× | 65,707 | 21,348 | 87.9 | 3,456 | 44,271 | -147,861 |  |
| 1.5× | 67,662 | 22,873 | 94.2 | 3,814 | 44,695 | -161,160 | minimum risk · balanced (λ = 1) |

**3M · Equity/es**

| m | Risk reduction | Profit sacrificed | Cost | Basis | U(λ=1) | U(λ=10) | Point |
|---|---|---|---|---|---|---|---|
| 0.5× | 30,128 | 7,624 | 31.4 | 4,410 | 22,472 | -46,146 | profit-preserving (λ = 10) |
| 0.6× | 35,470 | 9,149 | 37.7 | 3,983 | 26,284 | -56,059 |  |
| 0.7× | 40,393 | 10,674 | 44 | 3,602 | 29,675 | -66,391 |  |
| 0.8× | 44,997 | 12,199 | 50.2 | 3,281 | 32,748 | -77,042 |  |
| 0.9× | 49,448 | 13,724 | 56.5 | 3,040 | 35,668 | -87,845 |  |
| 1.0× | 53,246 | 15,249 | 62.8 | 2,900 | 37,935 | -99,302 |  |
| 1.1× | 56,742 | 16,773 | 69.1 | 2,874 | 39,899 | -111,061 |  |
| 1.2× | 60,168 | 18,298 | 75.4 | 2,967 | 41,794 | -122,890 |  |
| 1.3× | 63,249 | 19,823 | 81.6 | 3,167 | 43,345 | -135,063 |  |
| 1.4× | 65,707 | 21,348 | 87.9 | 3,456 | 44,271 | -147,861 |  |
| 1.5× | 67,662 | 22,873 | 94.2 | 3,814 | 44,695 | -161,160 | minimum risk · balanced (λ = 1) |

**3M · Equity/min_variance**

| m | Risk reduction | Profit sacrificed | Cost | Basis | U(λ=1) | U(λ=10) | Point |
|---|---|---|---|---|---|---|---|
| 0.5× | 29,532 | 15,893 | 217 | 8,573 | 13,423 | -129,610 | profit-preserving (λ = 10) |
| 0.6× | 34,169 | 19,071 | 260 | 7,661 | 14,838 | -156,802 |  |
| 0.7× | 38,152 | 22,250 | 304 | 6,840 | 15,599 | -184,648 | balanced (λ = 1) |
| 0.8× | 41,353 | 25,428 | 347 | 6,144 | 15,578 | -213,276 |  |
| 0.9× | 43,646 | 28,607 | 390 | 5,621 | 14,648 | -242,811 |  |
| 1.0× | 44,922 | 31,785 | 434 | 5,322 | 12,703 | -273,363 |  |
| 1.1× | 45,116 | 34,964 | 477 | 5,285 | 9,675 | -304,998 | minimum risk |
| 1.2× | 44,216 | 38,142 | 520 | 5,516 | 5,553 | -337,727 |  |
| 1.3× | 42,270 | 41,321 | 564 | 5,984 | 386 | -371,501 |  |
| 1.4× | 39,376 | 44,499 | 607 | 6,638 | -5,731 | -406,224 |  |
| 1.5× | 35,654 | 47,678 | 651 | 7,429 | -12,675 | -441,775 |  |

**3M · Equity/name**

| m | Risk reduction | Profit sacrificed | Cost | Basis | U(λ=1) | U(λ=10) | Point |
|---|---|---|---|---|---|---|---|
| 0.5× | 23,093 | 8,055 | 826 | 3,743 | 14,212 | -58,282 | profit-preserving (λ = 10) |
| 0.6× | 27,570 | 9,666 | 991 | 3,323 | 16,912 | -70,080 |  |
| 0.7× | 31,979 | 11,277 | 1,157 | 3,006 | 19,545 | -81,946 |  |
| 0.8× | 36,308 | 12,888 | 1,322 | 2,826 | 22,098 | -93,892 |  |
| 0.9× | 40,540 | 14,499 | 1,487 | 2,809 | 24,554 | -105,934 |  |
| 1.0× | 44,656 | 16,110 | 1,652 | 2,958 | 26,894 | -118,093 |  |
| 1.1× | 48,631 | 17,721 | 1,817 | 3,250 | 29,093 | -130,393 |  |
| 1.2× | 52,433 | 19,332 | 1,983 | 3,651 | 31,119 | -142,866 |  |
| 1.3× | 56,023 | 20,943 | 2,148 | 4,130 | 32,932 | -155,552 |  |
| 1.4× | 59,351 | 22,554 | 2,313 | 4,663 | 34,484 | -168,499 |  |
| 1.5× | 62,357 | 24,165 | 2,478 | 5,232 | 35,714 | -181,767 | minimum risk · balanced (λ = 1) |

**3M · Equity/sector**

| m | Risk reduction | Profit sacrificed | Cost | Basis | U(λ=1) | U(λ=10) | Point |
|---|---|---|---|---|---|---|---|
| 0.5× | 18,280 | 6,264 | 1,166 | 3,167 | 10,850 | -45,525 | profit-preserving (λ = 10) |
| 0.6× | 21,501 | 7,517 | 1,399 | 3,482 | 12,585 | -55,064 |  |
| 0.7× | 24,538 | 8,769 | 1,633 | 3,912 | 14,136 | -64,788 |  |
| 0.8× | 27,369 | 10,022 | 1,866 | 4,425 | 15,481 | -74,718 |  |
| 0.9× | 29,971 | 11,275 | 2,099 | 4,994 | 16,598 | -84,876 |  |
| 1.0× | 32,323 | 12,528 | 2,332 | 5,603 | 17,463 | -95,285 |  |
| 1.1× | 34,400 | 13,780 | 2,566 | 6,240 | 18,054 | -105,969 |  |
| 1.2× | 36,178 | 15,033 | 2,799 | 6,897 | 18,346 | -116,952 | balanced (λ = 1) |
| 1.3× | 37,636 | 16,286 | 3,032 | 7,569 | 18,318 | -128,255 |  |
| 1.4× | 38,752 | 17,539 | 3,265 | 8,252 | 17,948 | -139,899 |  |
| 1.5× | 39,511 | 18,791 | 3,499 | 8,944 | 17,222 | -151,901 | minimum risk |

**3M · Equity/systematic**

| m | Risk reduction | Profit sacrificed | Cost | Basis | U(λ=1) | U(λ=10) | Point |
|---|---|---|---|---|---|---|---|
| 0.5× | 19,240 | 7,794 | 605 | 3,821 | 10,841 | -59,309 | profit-preserving (λ = 10) |
| 0.6× | 22,720 | 9,353 | 726 | 3,383 | 12,641 | -71,538 |  |
| 0.7× | 26,037 | 10,912 | 847 | 3,045 | 14,278 | -83,931 |  |
| 0.8× | 29,169 | 12,471 | 968 | 2,846 | 15,730 | -96,509 |  |
| 0.9× | 32,092 | 14,030 | 1,089 | 2,813 | 16,973 | -109,295 |  |
| 1.0× | 34,779 | 15,589 | 1,210 | 2,953 | 17,980 | -122,318 |  |
| 1.1× | 37,200 | 17,148 | 1,331 | 3,243 | 18,722 | -135,607 |  |
| 1.2× | 39,326 | 18,706 | 1,452 | 3,647 | 19,168 | -149,190 |  |
| 1.3× | 41,126 | 20,265 | 1,573 | 4,133 | 19,288 | -163,100 | balanced (λ = 1) |
| 1.4× | 42,571 | 21,824 | 1,694 | 4,675 | 19,053 | -177,365 |  |
| 1.5× | 43,635 | 23,383 | 1,815 | 5,255 | 18,437 | -192,010 | minimum risk |

**3M · Equity/target_vol**

| m | Risk reduction | Profit sacrificed | Cost | Basis | U(λ=1) | U(λ=10) | Point |
|---|---|---|---|---|---|---|---|
| 0.5× | 16,769 | 8,858 | 84.5 | 5,122 | 7,826 | -71,895 | profit-preserving (λ = 10) |
| 0.6× | 19,669 | 10,630 | 101 | 4,592 | 8,938 | -86,727 |  |
| 0.7× | 22,379 | 12,401 | 118 | 4,121 | 9,859 | -101,751 |  |
| 0.8× | 24,877 | 14,173 | 135 | 3,731 | 10,569 | -116,986 |  |
| 0.9× | 27,143 | 15,944 | 152 | 3,450 | 11,046 | -132,453 |  |
| 1.0× | 29,155 | 17,716 | 169 | 3,305 | 11,270 | -148,173 | balanced (λ = 1) |
| 1.1× | 30,892 | 19,487 | 186 | 3,315 | 11,218 | -164,169 |  |
| 1.2× | 32,333 | 21,259 | 203 | 3,479 | 10,871 | -180,460 |  |
| 1.3× | 33,461 | 23,031 | 220 | 3,775 | 10,211 | -197,066 |  |
| 1.4× | 34,259 | 24,802 | 237 | 4,177 | 9,220 | -214,000 |  |
| 1.5× | 34,716 | 26,574 | 254 | 4,657 | 7,889 | -231,276 | minimum risk |

**3M · Equity/var**

| m | Risk reduction | Profit sacrificed | Cost | Basis | U(λ=1) | U(λ=10) | Point |
|---|---|---|---|---|---|---|---|
| 0.5× | 30,128 | 7,624 | 31.4 | 4,410 | 22,472 | -46,146 | profit-preserving (λ = 10) |
| 0.6× | 35,470 | 9,149 | 37.7 | 3,983 | 26,284 | -56,059 |  |
| 0.7× | 40,393 | 10,674 | 44 | 3,602 | 29,675 | -66,391 |  |
| 0.8× | 44,997 | 12,199 | 50.2 | 3,281 | 32,748 | -77,042 |  |
| 0.9× | 49,448 | 13,724 | 56.5 | 3,040 | 35,668 | -87,845 |  |
| 1.0× | 53,246 | 15,249 | 62.8 | 2,900 | 37,935 | -99,302 |  |
| 1.1× | 56,742 | 16,773 | 69.1 | 2,874 | 39,899 | -111,061 |  |
| 1.2× | 60,168 | 18,298 | 75.4 | 2,967 | 41,794 | -122,890 |  |
| 1.3× | 63,249 | 19,823 | 81.6 | 3,167 | 43,345 | -135,063 |  |
| 1.4× | 65,707 | 21,348 | 87.9 | 3,456 | 44,271 | -147,861 |  |
| 1.5× | 67,662 | 22,873 | 94.2 | 3,814 | 44,695 | -161,160 | minimum risk · balanced (λ = 1) |

**3M · FX/fx**

| m | Risk reduction | Profit sacrificed | Cost | Basis | U(λ=1) | U(λ=10) | Point |
|---|---|---|---|---|---|---|---|
| 0.5× | 613 | -104 | 4.6 | 305 | 712 | 1,646 |  |
| 0.6× | 728 | -125 | 5.52 | 257 | 847 | 1,968 |  |
| 0.7× | 841 | -145 | 6.44 | 215 | 980 | 2,288 |  |
| 0.8× | 952 | -166 | 7.36 | 180 | 1,111 | 2,606 |  |
| 0.9× | 1,061 | -187 | 8.27 | 159 | 1,239 | 2,921 |  |
| 1.0× | 1,167 | -208 | 9.19 | 157 | 1,365 | 3,233 |  |
| 1.1× | 1,270 | -228 | 10.1 | 175 | 1,488 | 3,543 |  |
| 1.2× | 1,371 | -249 | 11 | 207 | 1,609 | 3,851 |  |
| 1.3× | 1,470 | -270 | 12 | 248 | 1,728 | 4,157 |  |
| 1.4× | 1,566 | -291 | 12.9 | 295 | 1,844 | 4,460 |  |
| 1.5× | 1,660 | -311 | 13.8 | 344 | 1,958 | 4,760 | minimum risk · balanced (λ = 1) · profit-preserving (λ = 10) |

**3M · Rates/curve**

| m | Risk reduction | Profit sacrificed | Cost | Basis | U(λ=1) | U(λ=10) | Point |
|---|---|---|---|---|---|---|---|
| 0.5× | 1,896 | 283 | 48.3 | 1,538 | 1,565 | -979 | profit-preserving (λ = 10) |
| 0.6× | 2,200 | 339 | 58 | 1,603 | 1,803 | -1,250 |  |
| 0.7× | 2,479 | 396 | 67.6 | 1,677 | 2,016 | -1,546 |  |
| 0.8× | 2,732 | 452 | 77.3 | 1,758 | 2,202 | -1,868 |  |
| 0.9× | 2,958 | 509 | 87 | 1,846 | 2,362 | -2,217 |  |
| 1.0× | 3,157 | 565 | 96.6 | 1,939 | 2,495 | -2,593 |  |
| 1.1× | 3,329 | 622 | 106 | 2,038 | 2,601 | -2,996 |  |
| 1.2× | 3,473 | 678 | 116 | 2,140 | 2,678 | -3,427 |  |
| 1.3× | 3,589 | 735 | 126 | 2,246 | 2,728 | -3,886 |  |
| 1.4× | 3,677 | 791 | 135 | 2,355 | 2,750 | -4,373 | balanced (λ = 1) |
| 1.5× | 3,737 | 848 | 145 | 2,467 | 2,744 | -4,888 | minimum risk |

**3M · Rates/drawdown**

| m | Risk reduction | Profit sacrificed | Cost | Basis | U(λ=1) | U(λ=10) | Point |
|---|---|---|---|---|---|---|---|
| 0.5× | 4,220 | 31.5 | 42.3 | 2,277 | 4,146 | 3,862 |  |
| 0.6× | 5,030 | 37.8 | 50.8 | 2,334 | 4,942 | 4,601 |  |
| 0.7× | 5,841 | 44.1 | 59.2 | 2,399 | 5,738 | 5,341 |  |
| 0.8× | 6,652 | 50.4 | 67.7 | 2,472 | 6,534 | 6,080 |  |
| 0.9× | 7,462 | 56.7 | 76.1 | 2,552 | 7,329 | 6,819 |  |
| 1.0× | 8,273 | 63 | 84.6 | 2,639 | 8,125 | 7,558 |  |
| 1.1× | 9,084 | 69.3 | 93 | 2,730 | 8,921 | 8,297 |  |
| 1.2× | 9,894 | 75.6 | 102 | 2,827 | 9,717 | 9,037 |  |
| 1.3× | 10,683 | 81.9 | 110 | 2,929 | 10,491 | 9,754 |  |
| 1.4× | 11,065 | 88.2 | 118 | 3,035 | 10,858 | 10,064 |  |
| 1.5× | 11,447 | 94.5 | 127 | 3,145 | 11,225 | 10,374 | minimum risk · balanced (λ = 1) · profit-preserving (λ = 10) |

**3M · Rates/duration**

| m | Risk reduction | Profit sacrificed | Cost | Basis | U(λ=1) | U(λ=10) | Point |
|---|---|---|---|---|---|---|---|
| 0.5× | 1,820 | 277 | 54.5 | 1,526 | 1,489 | -1,003 | profit-preserving (λ = 10) |
| 0.6× | 2,107 | 332 | 65.4 | 1,592 | 1,709 | -1,281 |  |
| 0.7× | 2,367 | 388 | 76.3 | 1,667 | 1,903 | -1,586 |  |
| 0.8× | 2,599 | 443 | 87.2 | 1,751 | 2,069 | -1,919 |  |
| 0.9× | 2,804 | 498 | 98.1 | 1,842 | 2,207 | -2,279 |  |
| 1.0× | 2,980 | 554 | 109 | 1,939 | 2,317 | -2,667 |  |
| 1.1× | 3,129 | 609 | 120 | 2,040 | 2,399 | -3,083 |  |
| 1.2× | 3,248 | 665 | 131 | 2,147 | 2,453 | -3,528 |  |
| 1.3× | 3,339 | 720 | 142 | 2,257 | 2,478 | -4,002 | balanced (λ = 1) |
| 1.4× | 3,402 | 775 | 153 | 2,370 | 2,474 | -4,504 |  |
| 1.5× | 3,435 | 831 | 164 | 2,487 | 2,440 | -5,036 | minimum risk |

**3M · Rates/es**

| m | Risk reduction | Profit sacrificed | Cost | Basis | U(λ=1) | U(λ=10) | Point |
|---|---|---|---|---|---|---|---|
| 0.5× | 4,220 | 31.5 | 42.3 | 2,277 | 4,146 | 3,862 |  |
| 0.6× | 5,030 | 37.8 | 50.8 | 2,334 | 4,942 | 4,601 |  |
| 0.7× | 5,841 | 44.1 | 59.2 | 2,399 | 5,738 | 5,341 |  |
| 0.8× | 6,652 | 50.4 | 67.7 | 2,472 | 6,534 | 6,080 |  |
| 0.9× | 7,462 | 56.7 | 76.1 | 2,552 | 7,329 | 6,819 |  |
| 1.0× | 8,273 | 63 | 84.6 | 2,639 | 8,125 | 7,558 |  |
| 1.1× | 9,084 | 69.3 | 93 | 2,730 | 8,921 | 8,297 |  |
| 1.2× | 9,894 | 75.6 | 102 | 2,827 | 9,717 | 9,037 |  |
| 1.3× | 10,683 | 81.9 | 110 | 2,929 | 10,491 | 9,754 |  |
| 1.4× | 11,065 | 88.2 | 118 | 3,035 | 10,858 | 10,064 |  |
| 1.5× | 11,447 | 94.5 | 127 | 3,145 | 11,225 | 10,374 | minimum risk · balanced (λ = 1) · profit-preserving (λ = 10) |

**3M · Rates/min_variance**

| m | Risk reduction | Profit sacrificed | Cost | Basis | U(λ=1) | U(λ=10) | Point |
|---|---|---|---|---|---|---|---|
| 0.5× | 13,843 | 358 | 105 | 4,668 | 13,379 | 10,159 |  |
| 0.6× | 16,352 | 429 | 127 | 4,827 | 15,796 | 11,932 |  |
| 0.7× | 18,704 | 501 | 148 | 5,008 | 18,055 | 13,547 |  |
| 0.8× | 20,843 | 572 | 169 | 5,208 | 20,102 | 14,950 |  |
| 0.9× | 22,693 | 644 | 190 | 5,426 | 21,860 | 16,064 |  |
| 1.0× | 24,160 | 716 | 211 | 5,658 | 23,233 | 16,794 |  |
| 1.1× | 25,134 | 787 | 232 | 5,904 | 24,115 | 17,031 | profit-preserving (λ = 10) |
| 1.2× | 25,522 | 859 | 253 | 6,162 | 24,410 | 16,682 | minimum risk · balanced (λ = 1) |
| 1.3× | 25,280 | 930 | 274 | 6,430 | 24,075 | 15,704 |  |
| 1.4× | 24,435 | 1,002 | 295 | 6,708 | 23,138 | 14,123 |  |
| 1.5× | 23,074 | 1,073 | 316 | 6,993 | 21,685 | 12,025 |  |

**3M · Rates/name**

| m | Risk reduction | Profit sacrificed | Cost | Basis | U(λ=1) | U(λ=10) | Point |
|---|---|---|---|---|---|---|---|
| 0.5× | 2,137 | -39.1 | 123 | 695 | 2,053 | 2,405 |  |
| 0.6× | 2,540 | -46.9 | 148 | 700 | 2,439 | 2,862 |  |
| 0.7× | 2,935 | -54.7 | 172 | 718 | 2,818 | 3,310 |  |
| 0.8× | 3,322 | -62.6 | 197 | 747 | 3,187 | 3,750 |  |
| 0.9× | 3,699 | -70.4 | 222 | 786 | 3,548 | 4,181 |  |
| 1.0× | 4,067 | -78.2 | 246 | 834 | 3,899 | 4,603 |  |
| 1.1× | 4,425 | -86 | 271 | 889 | 4,240 | 5,015 |  |
| 1.2× | 4,774 | -93.9 | 296 | 951 | 4,572 | 5,417 |  |
| 1.3× | 5,112 | -102 | 320 | 1,017 | 4,894 | 5,809 |  |
| 1.4× | 5,440 | -109 | 345 | 1,088 | 5,205 | 6,191 |  |
| 1.5× | 5,758 | -117 | 369 | 1,162 | 5,506 | 6,562 | minimum risk · balanced (λ = 1) · profit-preserving (λ = 10) |

**3M · Rates/systematic**

| m | Risk reduction | Profit sacrificed | Cost | Basis | U(λ=1) | U(λ=10) | Point |
|---|---|---|---|---|---|---|---|
| 0.5× | 7,266 | 1,100 | 131 | 2,183 | 6,036 | -3,860 | profit-preserving (λ = 10) |
| 0.6× | 8,621 | 1,319 | 157 | 2,285 | 7,145 | -4,730 |  |
| 0.7× | 9,932 | 1,539 | 183 | 2,400 | 8,210 | -5,644 |  |
| 0.8× | 11,192 | 1,759 | 209 | 2,526 | 9,224 | -6,609 |  |
| 0.9× | 12,395 | 1,979 | 235 | 2,660 | 10,181 | -7,631 |  |
| 1.0× | 13,532 | 2,199 | 261 | 2,803 | 11,072 | -8,720 |  |
| 1.1× | 14,593 | 2,419 | 287 | 2,953 | 11,887 | -9,884 |  |
| 1.2× | 15,568 | 2,639 | 313 | 3,108 | 12,616 | -11,134 |  |
| 1.3× | 16,446 | 2,859 | 339 | 3,268 | 13,247 | -12,482 |  |
| 1.4× | 17,213 | 3,079 | 366 | 3,433 | 13,769 | -13,939 |  |
| 1.5× | 17,859 | 3,299 | 392 | 3,601 | 14,169 | -15,519 | minimum risk · balanced (λ = 1) |

**3M · Rates/target_vol**

| m | Risk reduction | Profit sacrificed | Cost | Basis | U(λ=1) | U(λ=10) | Point |
|---|---|---|---|---|---|---|---|
| 0.5× | 55 | -23.8 | 0.475 | 179 | 78.3 | 293 |  |
| 0.6× | 65.5 | -28.6 | 0.57 | 182 | 93.5 | 351 |  |
| 0.7× | 76 | -33.3 | 0.666 | 186 | 109 | 409 |  |
| 0.8× | 86.2 | -38.1 | 0.761 | 189 | 124 | 467 |  |
| 0.9× | 96.4 | -42.9 | 0.856 | 194 | 138 | 524 |  |
| 1.0× | 106 | -47.6 | 0.951 | 198 | 153 | 582 |  |
| 1.1× | 116 | -52.4 | 1.05 | 203 | 168 | 639 |  |
| 1.2× | 126 | -57.2 | 1.14 | 209 | 182 | 697 |  |
| 1.3× | 136 | -61.9 | 1.24 | 214 | 196 | 754 |  |
| 1.4× | 145 | -66.7 | 1.33 | 220 | 210 | 811 |  |
| 1.5× | 154 | -71.5 | 1.43 | 226 | 224 | 867 | minimum risk · balanced (λ = 1) · profit-preserving (λ = 10) |

**3M · Rates/var**

| m | Risk reduction | Profit sacrificed | Cost | Basis | U(λ=1) | U(λ=10) | Point |
|---|---|---|---|---|---|---|---|
| 0.5× | 4,220 | 31.5 | 42.3 | 2,277 | 4,146 | 3,862 |  |
| 0.6× | 5,030 | 37.8 | 50.8 | 2,334 | 4,942 | 4,601 |  |
| 0.7× | 5,841 | 44.1 | 59.2 | 2,399 | 5,738 | 5,341 |  |
| 0.8× | 6,652 | 50.4 | 67.7 | 2,472 | 6,534 | 6,080 |  |
| 0.9× | 7,462 | 56.7 | 76.1 | 2,552 | 7,329 | 6,819 |  |
| 1.0× | 8,273 | 63 | 84.6 | 2,639 | 8,125 | 7,558 |  |
| 1.1× | 9,084 | 69.3 | 93 | 2,730 | 8,921 | 8,297 |  |
| 1.2× | 9,894 | 75.6 | 102 | 2,827 | 9,717 | 9,037 |  |
| 1.3× | 10,683 | 81.9 | 110 | 2,929 | 10,491 | 9,754 |  |
| 1.4× | 11,065 | 88.2 | 118 | 3,035 | 10,858 | 10,064 |  |
| 1.5× | 11,447 | 94.5 | 127 | 3,145 | 11,225 | 10,374 | minimum risk · balanced (λ = 1) · profit-preserving (λ = 10) |

**3M · Volatility/volatility**

| m | Risk reduction | Profit sacrificed | Cost | Basis | U(λ=1) | U(λ=10) | Point |
|---|---|---|---|---|---|---|---|
| 0.5× | 1,841 | 1,292 | 501 | 601 | 47.4 | -11,580 | balanced (λ = 1) · profit-preserving (λ = 10) |
| 0.6× | 2,197 | 1,550 | 602 | 616 | 44.8 | -13,908 |  |
| 0.7× | 2,549 | 1,809 | 702 | 645 | 38.1 | -16,240 |  |
| 0.8× | 2,896 | 2,067 | 802 | 686 | 27.3 | -18,576 |  |
| 0.9× | 3,240 | 2,325 | 902 | 738 | 12.2 | -20,917 |  |
| 1.0× | 3,579 | 2,584 | 1,003 | 798 | -7.23 | -23,261 |  |
| 1.1× | 3,914 | 2,842 | 1,103 | 864 | -31 | -25,611 |  |
| 1.2× | 4,245 | 3,101 | 1,203 | 936 | -59.1 | -27,964 |  |
| 1.3× | 4,571 | 3,359 | 1,304 | 1,011 | -91.6 | -30,322 |  |
| 1.4× | 4,892 | 3,617 | 1,404 | 1,090 | -129 | -32,685 |  |
| 1.5× | 5,209 | 3,876 | 1,504 | 1,172 | -170 | -35,052 | minimum risk |

## 3. Product preference by objective × volatility regime (an interpretable prior, not a selector)

Realised utility advantage of forcing each product type over hedge-2's own choice, shrunk toward the objective. Positive = that product would have done better than what hedge-2 picked.

| Horizon | Node | Product | Dates | Advantage | Shrunk |
|---|---|---|---|---|---|
| 1W | objective:Commodity/min_variance | commodity_etf | 21 | 584 | 151 |
| 1W | objective:Commodity/min_variance | etf | 38 | -332 | -129 |
| 1W | objective:Commodity/min_variance | equity_index_future | 79 | -522 | -297 |
| 1W | objective:Commodity/min_variance · high_vol | etf | 29 | 65.2 | -203 |
| 1W | objective:Commodity/min_variance · high_vol | equity_index_future | 45 | -959 | -709 |
| 1W | objective:Commodity/min_variance · low_vol | equity_index_future | 34 | 0 | -333 |
| 1W | objective:Credit/credit | high_yield | 223 | -162 | -128 |
| 1W | objective:Credit/credit | ig_corporate | 211 | -180 | -140 |
| 1W | objective:Credit/credit | leveraged_loan | 195 | -599 | -458 |
| 1W | objective:Credit/credit · high_vol | high_yield | 109 | -433 | -337 |
| 1W | objective:Credit/credit · high_vol | ig_corporate | 97 | -960 | -662 |
| 1W | objective:Credit/credit · high_vol | leveraged_loan | 86 | -1,004 | -838 |
| 1W | objective:Credit/credit · low_vol | ig_corporate | 114 | 811 | 469 |
| 1W | objective:Credit/credit · low_vol | high_yield | 114 | 85.8 | 0.157 |
| 1W | objective:Credit/credit · low_vol | leveraged_loan | 109 | -294 | -402 |
| 1W | objective:Crypto/min_variance | crypto_futures_etf | 53 | 129 | 60.5 |
| 1W | objective:Crypto/min_variance | crypto_etf | 26 | -1,997 | -604 |
| 1W | objective:Crypto/min_variance · low_vol | crypto_futures_etf | 34 | 404 | 229 |
| 1W | objective:Equity/beta | etn | 20 | 7,366 | 1,842 |
| 1W | objective:Equity/beta | equity_index_future | 223 | 48.8 | 38.5 |
| 1W | objective:Equity/beta | index_option | 99 | -1,572 | -979 |
| 1W | objective:Equity/beta | etf | 223 | -1,509 | -1,189 |
| 1W | objective:Equity/beta | inverse_etf | 223 | -1,690 | -1,332 |
| 1W | objective:Equity/beta · high_vol | equity_index_future | 109 | 123 | 96.6 |
| 1W | objective:Equity/beta · high_vol | etf | 109 | -1,662 | -1,608 |
| 1W | objective:Equity/beta · high_vol | inverse_etf | 109 | -1,737 | -1,720 |
| 1W | objective:Equity/beta · high_vol | index_option | 40 | -1,966 | -1,730 |
| 1W | objective:Equity/beta · low_vol | equity_index_future | 114 | -24.9 | 0.5 |
| 1W | objective:Equity/beta · low_vol | etf | 114 | -1,342 | -1,399 |
| 1W | objective:Equity/beta · low_vol | index_option | 59 | -1,404 | -1,489 |
| 1W | objective:Equity/beta · low_vol | inverse_etf | 114 | -1,625 | -1,647 |
| 1W | objective:Equity/crash | inverse_etf | 223 | 1,700 | 1,339 |
| 1W | objective:Equity/crash | index_option | 223 | -6,249 | -4,924 |
| 1W | objective:Equity/crash | etn | 107 | -7,738 | -4,958 |
| 1W | objective:Equity/crash · high_vol | inverse_etf | 109 | 2,420 | 2,165 |
| 1W | objective:Equity/crash · high_vol | index_option | 109 | -7,381 | -6,979 |
| 1W | objective:Equity/crash · high_vol | etn | 62 | -11,430 | -9,614 |
| 1W | objective:Equity/crash · low_vol | inverse_etf | 114 | 93.1 | 647 |
| 1W | objective:Equity/crash · low_vol | etn | 45 | -617 | -4,686 |
| 1W | objective:Equity/crash · low_vol | index_option | 114 | -4,223 | -4,921 |
| 1W | objective:Equity/min_variance | fx_future | 40 | -2,503 | -1,001 |
| 1W | objective:Equity/min_variance | equity_index_future | 223 | -1,388 | -1,094 |
| 1W | objective:Equity/min_variance | etf | 202 | -1,514 | -1,167 |
| 1W | objective:Equity/min_variance | fx_forward | 35 | -3,875 | -1,428 |
| 1W | objective:Equity/min_variance | index_option | 39 | -7,984 | -3,145 |
| 1W | objective:Equity/min_variance | inverse_etf | 77 | -6,525 | -3,667 |
| 1W | objective:Equity/min_variance · high_vol | equity_index_future | 109 | -1,907 | -1,723 |
| 1W | objective:Equity/min_variance · high_vol | etf | 107 | -1,840 | -1,723 |
| 1W | objective:Equity/min_variance · high_vol | fx_future | 33 | -2,869 | -2,633 |
| 1W | objective:Equity/min_variance · high_vol | fx_forward | 27 | -4,540 | -4,082 |
| 1W | objective:Equity/min_variance · high_vol | inverse_etf | 43 | -4,724 | -5,773 |
| 1W | objective:Equity/min_variance · high_vol | index_option | 23 | -10,268 | -8,617 |
| 1W | objective:Equity/min_variance · low_vol | equity_index_future | 114 | -674 | -920 |
| 1W | objective:Equity/min_variance · low_vol | etf | 95 | -756 | -1,049 |
| 1W | objective:Equity/min_variance · low_vol | inverse_etf | 34 | -9,527 | -7,611 |
| 1W | objective:Equity/sector | etf | 223 | -267 | -211 |
| 1W | objective:Equity/sector · high_vol | etf | 109 | -369 | -333 |
| 1W | objective:Equity/sector · low_vol | etf | 114 | -100 | -158 |
| 1W | objective:FX/fx | fx_future | 217 | 152 | 119 |
| 1W | objective:FX/fx | fx_forward | 223 | -2.82 | -2.22 |
| 1W | objective:FX/fx | currency_trust | 48 | -48.3 | -21.4 |
| 1W | objective:FX/fx | fx_spot | 223 | -150 | -119 |
| 1W | objective:FX/fx · high_vol | fx_future | 104 | 243 | 209 |
| 1W | objective:FX/fx · high_vol | fx_forward | 109 | -3.94 | -3.54 |
| 1W | objective:FX/fx · high_vol | currency_trust | 31 | 29.5 | -21.8 |
| 1W | objective:FX/fx · high_vol | fx_spot | 109 | -144 | -146 |
| 1W | objective:FX/fx · low_vol | fx_future | 113 | 75.7 | 102 |
| 1W | objective:FX/fx · low_vol | fx_forward | 114 | 0.724 | -0.497 |
| 1W | objective:FX/fx · low_vol | fx_spot | 114 | -151 | -151 |
| 1W | objective:Rates/duration | etf | 223 | 31.9 | 25.1 |
| 1W | objective:Rates/duration | treasury_future | 223 | -3.44 | -2.71 |
| 1W | objective:Rates/duration · high_vol | etf | 109 | 58 | 48.7 |
| 1W | objective:Rates/duration · high_vol | treasury_future | 109 | -8.34 | -6.6 |
| 1W | objective:Rates/duration · low_vol | etf | 114 | 48.7 | 42.9 |
| 1W | objective:Rates/duration · low_vol | treasury_future | 114 | -15.1 | -11.1 |
| 1W | objective:Rates/min_variance | treasury_future | 80 | 32.7 | 18.7 |
| 1W | objective:Rates/min_variance · high_vol | treasury_future | 52 | 36.5 | 34.5 |
| 1W | objective:Rates/min_variance · low_vol | treasury_future | 28 | 28.9 | 31.5 |
| 1M | objective:Commodity/min_variance | equity_index_future | 46 | 164 | 71.3 |
| 1M | objective:Commodity/min_variance | etf | 31 | -360 | -123 |
| 1M | objective:Commodity/min_variance | commodity_option | 22 | -2,287 | -614 |
| 1M | objective:Commodity/min_variance · high_vol | equity_index_future | 24 | -669 | -73.9 |
| 1M | objective:Commodity/min_variance · low_vol | equity_index_future | 22 | 924 | 368 |
| 1M | objective:Credit/credit | high_yield | 106 | 151 | 96.6 |
| 1M | objective:Credit/credit | ig_corporate | 100 | -464 | -290 |
| 1M | objective:Credit/credit | leveraged_loan | 93 | -713 | -434 |
| 1M | objective:Credit/credit · high_vol | high_yield | 50 | -120 | 27.9 |
| 1M | objective:Credit/credit · high_vol | ig_corporate | 44 | -812 | -611 |
| 1M | objective:Credit/credit · high_vol | leveraged_loan | 38 | -1,363 | -965 |
| 1M | objective:Credit/credit · low_vol | high_yield | 56 | 490 | 315 |
| 1M | objective:Credit/credit · low_vol | ig_corporate | 56 | 273 | -108 |
| 1M | objective:Credit/credit · low_vol | leveraged_loan | 55 | -178 | -457 |
| 1M | objective:Crypto/min_variance | crypto_futures_etf | 26 | 8,185 | 2,475 |
| 1M | objective:Equity/beta | equity_index_future | 106 | 79.4 | 50.7 |
| 1M | objective:Equity/beta | etf | 106 | -1,429 | -913 |
| 1M | objective:Equity/beta | inverse_etf | 106 | -4,944 | -3,157 |
| 1M | objective:Equity/beta | index_option | 46 | -9,873 | -4,284 |
| 1M | objective:Equity/beta · high_vol | equity_index_future | 50 | -68.4 | 12.2 |
| 1M | objective:Equity/beta · high_vol | etf | 50 | -912 | -1,194 |
| 1M | objective:Equity/beta · high_vol | inverse_etf | 50 | -6,927 | -5,845 |
| 1M | objective:Equity/beta · low_vol | equity_index_future | 56 | 352 | 211 |
| 1M | objective:Equity/beta · low_vol | etf | 56 | -1,921 | -1,666 |
| 1M | objective:Equity/beta · low_vol | inverse_etf | 56 | -2,851 | -3,934 |
| 1M | objective:Equity/beta · low_vol | index_option | 30 | -7,975 | -9,240 |
| 1M | objective:Equity/crash | inverse_etf | 106 | 2,581 | 1,648 |
| 1M | objective:Equity/crash | etn | 50 | 265 | 121 |
| 1M | objective:Equity/crash | index_option | 106 | -533 | -340 |
| 1M | objective:Equity/crash · high_vol | inverse_etf | 50 | 4,027 | 3,238 |
| 1M | objective:Equity/crash · high_vol | index_option | 50 | 1,984 | 611 |
| 1M | objective:Equity/crash · high_vol | etn | 28 | -2,348 | -566 |
| 1M | objective:Equity/crash · low_vol | etn | 22 | 6,205 | 1,859 |
| 1M | objective:Equity/crash · low_vol | inverse_etf | 56 | 564 | 1,607 |
| 1M | objective:Equity/crash · low_vol | index_option | 56 | -1,948 | -1,216 |
| 1M | objective:Equity/min_variance | fx_forward | 26 | 10,570 | 3,195 |
| 1M | objective:Equity/min_variance | fx_future | 22 | 11,722 | 3,145 |
| 1M | objective:Equity/min_variance | treasury_future | 53 | 1,083 | 508 |
| 1M | objective:Equity/min_variance | etf | 98 | 650 | 403 |
| 1M | objective:Equity/min_variance | commodity_option | 25 | 148 | 43.6 |
| 1M | objective:Equity/min_variance | equity_put | 45 | -267 | -114 |
| 1M | objective:Equity/min_variance | index_option | 49 | -4,590 | -2,063 |
| 1M | objective:Equity/min_variance | inverse_etf | 65 | -6,473 | -3,366 |
| 1M | objective:Equity/min_variance | equity_index_future | 106 | -5,555 | -3,547 |
| 1M | objective:Equity/min_variance · high_vol | treasury_future | 26 | 2,837 | 1,613 |
| 1M | objective:Equity/min_variance · high_vol | etf | 49 | -22.1 | 348 |
| 1M | objective:Equity/min_variance · high_vol | index_option | 24 | -6,086 | -5,017 |
| 1M | objective:Equity/min_variance · high_vol | equity_index_future | 50 | -8,831 | -7,044 |
| 1M | objective:Equity/min_variance · high_vol | inverse_etf | 31 | -9,957 | -7,660 |
| 1M | objective:Equity/min_variance · low_vol | treasury_future | 27 | 2,037 | 1,379 |
| 1M | objective:Equity/min_variance · low_vol | etf | 49 | 1,602 | 1,078 |
| 1M | objective:Equity/min_variance · low_vol | equity_put | 26 | -6,890 | -2,269 |
| 1M | objective:Equity/min_variance · low_vol | equity_index_future | 56 | -2,000 | -3,838 |
| 1M | objective:Equity/min_variance · low_vol | index_option | 25 | -3,091 | -4,149 |
| 1M | objective:Equity/min_variance · low_vol | inverse_etf | 34 | -4,059 | -5,600 |
| 1M | objective:Equity/sector | etf | 106 | -165 | -105 |
| 1M | objective:Equity/sector · high_vol | etf | 50 | -68.2 | -121 |
| 1M | objective:Equity/sector · low_vol | etf | 56 | -183 | -174 |
| 1M | objective:FX/fx | fx_future | 103 | 635 | 401 |
| 1M | objective:FX/fx | fx_forward | 106 | 26.8 | 17.1 |
| 1M | objective:FX/fx | fx_spot | 106 | -218 | -139 |
| 1M | objective:FX/fx · high_vol | fx_future | 48 | 697 | 662 |
| 1M | objective:FX/fx · high_vol | fx_forward | 50 | 45 | 35.1 |
| 1M | objective:FX/fx · high_vol | fx_spot | 50 | -181 | -201 |
| 1M | objective:FX/fx · low_vol | fx_future | 55 | 653 | 643 |
| 1M | objective:FX/fx · low_vol | fx_forward | 56 | -4.8 | 11.6 |
| 1M | objective:FX/fx · low_vol | fx_spot | 56 | -222 | -220 |
| 1M | objective:Rates/duration | treasury_future | 106 | -35 | -22.3 |
| 1M | objective:Rates/duration | etf | 106 | -365 | -233 |
| 1M | objective:Rates/duration · high_vol | treasury_future | 50 | -97.8 | -63.5 |
| 1M | objective:Rates/duration · high_vol | etf | 50 | -261 | -318 |
| 1M | objective:Rates/duration · low_vol | treasury_future | 56 | -28.7 | -32 |
| 1M | objective:Rates/duration · low_vol | etf | 56 | -417 | -390 |
| 1M | objective:Rates/min_variance | treasury_future | 105 | 1.17 | 0.743 |
| 1M | objective:Rates/min_variance · high_vol | treasury_future | 49 | 17.1 | 8.31 |
| 1M | objective:Rates/min_variance · low_vol | treasury_future | 56 | -30.3 | -14 |

## 4. H4 — what the failures are made of

| Horizon | Objective | Type | Failed on | Cost increase | Basis increase | Extra risk reduction | Extra cost | Extra profit given up | Risk per cost $ | ΔU (t) |
|---|---|---|---|---|---|---|---|---|---|---|
| 1W | Credit/credit@1.0 | hard-target | cost, basis | +45% | +38% | 318 | 62.3 | 15.2 | 5.10 | 240 (+2.6) |
| 1W | Credit/name@1.0 | hard-target | cost, basis | +50% | +46% | 760 | 51.5 | 18.9 | 14.78 | 690 (+3.4) |
| 1W | Crypto/drawdown@1.0 | variance-sensitive | cost | +44% | -11% | 15,976 | 118 | 1,419 | 135.46 | 14,439 (+2.2) |
| 1W | Crypto/es@1.0 | variance-sensitive | cost | +44% | -11% | 15,976 | 118 | 1,419 | 135.46 | 14,439 (+2.2) |
| 1W | Crypto/target_vol@1.0 | variance-sensitive | cost | +44% | -6% | 12,361 | 277 | 2,733 | 44.70 | 9,352 (+3.3) |
| 1W | Crypto/var@1.0 | variance-sensitive | cost | +44% | -11% | 15,976 | 118 | 1,419 | 135.46 | 14,439 (+2.2) |
| 1W | Equity/beta@1.0 | hard-target | cost, basis | +49% | +102% | 2,380 | 13.2 | 954 | 180.26 | 1,412 (+2.8) |
| 1W | Equity/drawdown@1.0 | variance-sensitive | cost | +49% | +3% | 5,804 | 13.1 | 805 | 444.50 | 4,986 (+3.3) |
| 1W | Equity/es@1.0 | variance-sensitive | cost | +49% | +3% | 5,804 | 13.1 | 805 | 444.50 | 4,986 (+3.3) |
| 1W | Equity/name@1.0 | hard-target | cost, basis | +50% | +82% | 5,081 | 223 | 936 | 22.74 | 3,922 (+9.0) |
| 1W | Equity/sector@1.0 | hard-target | cost, basis | +29% | +28% | 1,598 | 190 | 432 | 8.43 | 976 (+3.5) |
| 1W | Equity/systematic@1.0 | hard-target | cost, basis | +47% | +81% | 3,480 | 154 | 976 | 22.63 | 2,350 (+4.3) |
| 1W | Equity/target_vol@1.0 | variance-sensitive | cost | +28% | -0% | 1,367 | 7.31 | 509 | 187.08 | 851 (+3.9) |
| 1W | Equity/var@1.0 | variance-sensitive | cost | +49% | +3% | 5,804 | 13.1 | 805 | 444.50 | 4,986 (+3.3) |
| 1W | Rates/min_variance@1.0 | variance-sensitive | cost | +49% | +3% | 217 | 5.32 | -76.8 | 40.90 | 289 (+3.4) |
| 1W | Rates/name@1.0 | hard-target | cost, basis | +47% | +24% | 261 | 15.3 | -30.7 | 17.13 | 277 (+4.5) |
| 1W | Rates/systematic@1.0 | hard-target | cost, basis | +28% | +14% | 294 | 16.4 | -11.7 | 17.85 | 289 (+2.6) |
| 1W | Volatility/volatility@1.0 | hard-target | cost, basis | +46% | +43% | 859 | 84.1 | 102 | 10.21 | 673 (+2.5) |
| 1M | Credit/name@1.0 | hard-target | cost, basis | +46% | +43% | 1,428 | 149 | 20.2 | 9.60 | 1,259 (+3.9) |
| 1M | Equity/drawdown@1.0 | variance-sensitive | cost, basis | +50% | +17% | 7,764 | -46.4 | 2,195 | — | 5,615 (+2.0) |
| 1M | Equity/es@1.0 | variance-sensitive | cost, basis | +50% | +17% | 7,764 | -46.4 | 2,195 | — | 5,615 (+2.0) |
| 1M | Equity/name@1.0 | hard-target | cost, basis | +48% | +75% | 7,213 | 302 | 2,427 | 23.90 | 4,484 (+4.7) |
| 1M | Equity/systematic@1.0 | hard-target | cost, basis | +48% | +80% | 5,395 | 283 | 2,617 | 19.06 | 2,496 (+2.0) |
| 1M | Equity/var@1.0 | variance-sensitive | cost, basis | +50% | +17% | 7,764 | -46.4 | 2,195 | — | 5,615 (+2.0) |
| 1M | Rates/drawdown@1.0 | variance-sensitive | cost, basis | +48% | +12% | 2,181 | 11.3 | -263 | 193.89 | 2,433 (+3.3) |
| 1M | Rates/es@1.0 | variance-sensitive | cost, basis | +48% | +12% | 2,181 | 11.3 | -263 | 193.89 | 2,433 (+3.3) |
| 1M | Rates/name@1.0 | hard-target | cost, basis | +45% | +28% | 826 | 39.9 | -113 | 20.70 | 899 (+3.2) |
| 1M | Rates/systematic@1.0 | hard-target | cost, basis | +47% | +25% | 1,818 | 49.8 | -37.6 | 36.51 | 1,805 (+3.0) |
| 1M | Rates/var@1.0 | variance-sensitive | cost, basis | +48% | +12% | 2,181 | 11.3 | -263 | 193.89 | 2,433 (+3.3) |

H4 is **not** loosened after seeing these results. Proposed for a NEW research version only (not applied here, not tested on these results): replace the flat 'cost ≤ +10%, basis error ≤ +5%' guard by an objective-aware efficiency test — the extra risk reduction bought per extra dollar of cost must be ≥ 1, the gain must hold at λ = 1 and λ = 2, and for hard-target objectives the basis error must stay within +25% while variance-sensitive objectives are judged on realised variance only. It would be committed before any run that uses it.

## 5. Does validated Alpha strength change the optimal hedge?

Each hedge case's book gets its out-of-sample 1W Alpha percentile (learned hierarchy model, walk-forward), bucketed by |percentile − 50%| into weak / medium / strong and by sign. Best in-sample multiple per bucket and λ; then walk-forward: a multiple per bucket (shrunk to the single multiple) against one multiple, both learned before each era, at λ = 1.

**1W** — 58351 cases

| Alpha bucket | Cases | Dates | Best multiple at λ = 0.5 / 1 / 2 / 5 / 10 |
|---|---|---|---|
| medium + | 8810 | 381 | 1.50 / 1.45 / 1.40 / 1.10 / 0.50 |
| medium − | 7523 | 380 | 1.35 / 1.30 / 1.25 / 1.00 / 0.50 |
| strong + | 7030 | 372 | 1.50 / 1.50 / 1.50 / 1.25 / 0.50 |
| strong − | 7201 | 375 | 1.20 / 1.15 / 1.05 / 0.50 / 0.50 |
| weak + | 14502 | 429 | 1.50 / 1.50 / 1.45 / 1.10 / 0.50 |
| weak − | 13285 | 427 | 1.20 / 1.10 / 0.95 / 0.50 / 0.50 |

| Objective | Dates | ΔU per-bucket vs one multiple | t | p |
|---|---|---|---|---|
| beta | 345 | -115 | -2.9 | 0.998 |
| commodity | 345 | 0.476 | +0.0 | 0.461 |
| crash | 345 | -259 | -2.4 | 0.990 |
| credit | 345 | 10.4 | +0.5 | 0.327 |
| crypto | 111 | 948 | +1.3 | 0.110 |
| curve | 345 | -7.02 | -0.9 | 0.823 |
| drawdown | 345 | -354 | -3.1 | 1.000 |
| duration | 345 | -7.8 | -0.9 | 0.796 |
| es | 345 | -354 | -3.1 | 1.000 |
| fx | 345 | -0.648 | -0.1 | 0.616 |
| min_variance | 345 | 146 | +1.9 | 0.015 |
| name | 345 | -146 | -4.1 | 1.000 |
| sector | 345 | 16.5 | +0.3 | 0.459 |
| systematic | 345 | -44.4 | -0.9 | 0.766 |
| target_vol | 345 | -84.2 | -2.1 | 0.960 |
| var | 345 | -354 | -3.1 | 1.000 |
| volatility | 205 | -32.8 | -1.4 | 0.923 |

**1M** — 27680 cases

| Alpha bucket | Cases | Dates | Best multiple at λ = 0.5 / 1 / 2 / 5 / 10 |
|---|---|---|---|
| medium + | 4014 | 178 | 1.50 / 1.50 / 1.50 / 1.50 / 1.45 |
| medium − | 3488 | 172 | 1.45 / 1.35 / 1.25 / 0.85 / 0.50 |
| strong + | 3576 | 185 | 1.50 / 1.50 / 1.35 / 0.55 / 0.50 |
| strong − | 3372 | 182 | 1.45 / 1.35 / 1.25 / 0.60 / 0.50 |
| weak + | 7058 | 202 | 1.50 / 1.50 / 1.50 / 1.15 / 0.50 |
| weak − | 6172 | 204 | 1.25 / 1.20 / 1.10 / 0.50 / 0.50 |

| Objective | Dates | ΔU per-bucket vs one multiple | t | p |
|---|---|---|---|---|
| beta | 164 | -83.5 | -0.8 | 0.758 |
| commodity | 164 | -39.7 | -1.3 | 0.818 |
| crash | 164 | -21.2 | -0.1 | 0.486 |
| credit | 164 | 0.295 | +0.0 | 0.529 |
| crypto | 52 | -2,239 | -1.0 | 0.853 |
| curve | 164 | -20.8 | -0.6 | 0.743 |
| drawdown | 164 | -485 | -1.7 | 0.973 |
| duration | 164 | -12.8 | -0.3 | 0.653 |
| es | 164 | -485 | -1.7 | 0.973 |
| fx | 164 | -5.54 | -0.5 | 0.643 |
| min_variance | 164 | 710 | +3.6 | 0.002 |
| name | 164 | -355 | -4.4 | 1.000 |
| sector | 164 | -178 | -1.2 | 0.885 |
| systematic | 164 | -236 | -1.9 | 0.960 |
| target_vol | 164 | -155 | -1.6 | 0.948 |
| var | 164 | -485 | -1.7 | 0.973 |
| volatility | 97 | 2.44 | +0.1 | 0.461 |

**3M** — 9033 cases

| Alpha bucket | Cases | Dates | Best multiple at λ = 0.5 / 1 / 2 / 5 / 10 |
|---|---|---|---|
| medium + | 1362 | 59 | 1.50 / 1.35 / 1.00 / 0.50 / 0.50 |
| medium − | 1331 | 59 | 1.35 / 1.25 / 0.80 / 0.50 / 0.50 |
| strong + | 1127 | 60 | 1.50 / 1.45 / 0.80 / 0.50 / 0.50 |
| strong − | 1080 | 64 | 1.50 / 1.50 / 1.45 / 1.00 / 0.50 |
| weak + | 1944 | 63 | 1.50 / 1.30 / 0.65 / 0.50 / 0.50 |
| weak − | 2189 | 67 | 1.25 / 1.20 / 1.10 / 0.50 / 0.50 |

| Objective | Dates | ΔU per-bucket vs one multiple | t | p |
|---|---|---|---|---|
| beta | 38 | -579 | -1.2 | 0.830 |
| commodity | 38 | -868 | -2.1 | 1.000 |
| crash | 38 | -2,336 | -1.7 | 0.883 |
| credit | 38 | -69.8 | -0.5 | 0.621 |
| crypto | 16 | -4,762 | — | — |
| curve | 38 | -32 | -0.3 | 0.611 |
| drawdown | 38 | -1,045 | -0.8 | 0.756 |
| duration | 38 | -13.5 | -0.1 | 0.551 |
| es | 38 | -1,045 | -0.8 | 0.756 |
| fx | 38 | -45.2 | -0.7 | 0.815 |
| min_variance | 38 | -1,976 | -2.7 | 1.000 |
| name | 38 | -381 | -0.9 | 0.718 |
| sector | 38 | -458 | -1.0 | 0.800 |
| systematic | 38 | -1,174 | -1.9 | 0.975 |
| target_vol | 38 | -817 | -2.2 | 0.988 |
| var | 38 | -1,045 | -0.8 | 0.756 |
| volatility | 31 | -374 | -1.8 | 0.990 |

