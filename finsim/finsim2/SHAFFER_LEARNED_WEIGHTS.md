# Shaffer learned weights — what history supports

Run 2026-09-26 17:01:08 · 2532.2 s · `python -m finsim2 lab --learned`. Research only: production (shaffer-2.1, shaffer-alpha-2.1-production, the Directional definition, hedge-2) is unchanged. Method: `engine/learned.py` (module docstring); hedge parameters: `hedge/hedgelearn.py`.

**Find the weights first, then decide how much to trust them.** For every horizon, every one of the 74 production signals gets a weight at every node of Global → Class → Product type → Sector → Industry → Asset, learned from the point-in-time research records. Two weight sets always exist: the *historical best fit* (what fits the past best, hardly pooled) and the *validated deployable* weights (pooled toward the parent, trusted in proportion to their out-of-sample evidence, specialised only where a node's own deviation survived out of sample). A failed promotion gate does not mean there are no learned weights — it means they are not trusted enough to replace production.

## Key findings

- **The engine recovers planted weights:** 10 of 10 synthetic tests passed (linear, ranking target, sparse, correlated, regime-dependent, sector-specific, horizon-specific, nonlinear interaction, pure noise, asset-specific with a short-history asset) before any market result was read.
- **Learned weights that beat production under every fixed gate (G1–G4 + FDR across 24 tests):** Alpha 1W simple global — rank IC +0.0526 vs production +0.0376, Δ t +2.3, 3/4 eras, split t +2.1, net long-short +0.154% per week vs +0.008%; Alpha 1W uniform-depth hierarchy — rank IC +0.0546 vs production +0.0376, Δ t +2.0, 3/4 eras, split t +3.4, net long-short +0.192% per week vs +0.008%. They are in live shadow now (recorded daily, graded like production); promotion needs ≥ 60 graded live outcomes and your approval. Their gain is concentrated after 2017; it is ~0 in 2009–12 and negative in 2013–16.
- **The trust-filtered 'validated deployable' set (C) was too conservative where signals are weak individually but useful together:** it trails the simple global learned weights at 1D (+0.0522 vs +0.0550), 1W (+0.0400 vs +0.0526), 12M (+0.0128 vs +0.0640). Zeroing every global weight whose own out-of-sample t is ≤ 1 removes signals that only help jointly. It is reported as designed; it is not what should be shadowed.
- **Hierarchy helps at 1D:** at pooling K = 5000, 5 of 5 depths below global beat global in the walk-forward (Class, Product type, Sector, Industry, Asset; best Asset, rank IC +0.0881 vs global +0.0553). Picking that cell uses hindsight; the nested choice (E) captures part of it.
- **Hierarchy helps at 1W:** at pooling K = 20000, 5 of 5 depths below global beat global in the walk-forward (Class, Product type, Sector, Industry, Asset; best Asset, rank IC +0.0715 vs global +0.0522). Picking that cell uses hindsight; the nested choice (E) captures part of it.
- **1M, 3M, 6M, 12M:** nothing beats production significantly; no signal weight is validated at 3M–12M; the validated model keeps no specialisation there (history does not support it).
- **Directional:** no learned weight set beats the point-in-time base prior at any horizon (Brier, log loss, calibration together).
- **Stability:** across all horizons the Alpha global weights are NO EVIDENCE 172, REGIME DEPENDENT 150, UNSTABLE 72, STABLE 50 — few signal weights are stable enough to treat as permanent.
- **Learned vs production:** the learned allocations are very different from production's (distance 0.8–0.9 on a 0–1 scale, §2 Q10). Production is not close to the learned optimum; at 1D/1W the learned weights are measurably better, at 1M–12M the differences are noise.
- **Hedge:** history supports larger hedges than hedge-2 at 1W and 1M (global validated multiple 1.35× at 1W; most risk classes and objectives at 1.35–1.5×, minimum-variance at ~1.0×). Many sizing cells survive FDR, but every one fails the fixed cost / basis-error guard H4 and the gain reverses for profit-sensitive users (λ ≥ 5). Product choice: nothing validated.

## 1. Capability test — can the engine recover planted weights?

Before any market result was interpreted, the same code was run on synthetic point-in-time datasets whose true equation is known (weekly records, 40 assets in a class / sector / industry tree, 2002–2026, regimes switching every ~26 weeks). Every planted relationship must be recovered, and pure noise must not be validated.

| Test | Planted | Recovered | Result |
|---|---|---|---|
| linear | [0.5, 0.3, -0.2] | {"recovered": [0.508, 0.303, -0.204], "max_noise_weight": 0.009, "status": ["VALIDATED", "VALIDATED", "VALIDATED"], "noise_validated": 0} | **PASS** |
| linear (ranking target) | [0.6, -0.4] | {"recovered_ratio": [0.598, -0.403]} | **PASS** |
| sparse | {"s03": 0.3, "s11": -0.2, "s20": 0.15} | {"recovered": {"s03": 0.305, "s11": -0.194, "s20": 0.15}, "false_validated": []} | **PASS** |
| correlated | {"momentum": 0.4, "valuation (corr 0.95, no effect)": 0.0} | {"recovered": {"momentum": 0.419, "valuation": -0.013}} | **PASS** |
| regime-dependent | {"high_vol": 0.4, "low_vol": -0.4} | {"recovered": {"high_vol": 0.391, "low_vol": -0.397}} | **PASS** |
| sector-specific | {"S0 (class 0)": 0.5, "S1 (class 0)": -0.5, "class 1": 0.0} | {"kept_nodes": {"sector": 2}, "global_signal_kept_nodes": 0, "recovered": {"S0 (class 0)": 0.507, "S1 (class 0)": -0.508, "class 1": 0.0}} | **PASS** |
| horizon-specific | {"1W": [0.5, -0.3, 0.0], "3M": [-0.2, 0.0, 0.5]} | {"recovered": {"1W": [0.489, -0.3, -0.002], "3M": [-0.189, 0.004, 0.494]}} | **PASS** |
| nonlinear interaction | "y = 0.6 · momentum × valuation" | {"linear_max_weight": 0.014, "boosting_gain_t": 36.5, "top_interactions": ["momentum × valuation", "noise_2 × noise_3", "noise_3 × noise_4"]} | **PASS** |
| pure noise | null | {"validated": 0, "C_oos_t": -1.18} | **PASS** |
| asset-specific + short history | {"X00 (full history)": 0.6, "X01 (20 weeks)": 0.6} | {"kept_asset_nodes": ["asset:X00"], "recovered": {"X00 (full history)": 0.525, "X01 (20 weeks, inherits)": 0.004}} | **PASS** |

All planted effects were recovered and noise was not validated; the market results below come from the same code.

## 2. Answers

**1–2. Historical best-fit and validated Alpha weights.** Every horizon has both; the full global table is in each horizon's section (signal, production weight, best fit, validated weight, reliability, era stability, OOS contribution), every node's weights are in the ML Lab (Shaffer Alpha → Learned weights) and stored with the run. Largest validated global weights (signed share of |weight|):

- **1D:** rsi_14 -12%, ret_1m +11%, ret_1d -10%, y10 +9%, gold_mom_3m -6%, dist_ma200 -6%
- **1W:** dist_ma200 -14%, mom_12_1 +13%, ret_1d -8%, sortino_252 -7%, mr_opportunity -6%, sharpe_252 +6%
- **1M:** sortino_252 -22%, z_20 -12%, bb_pctb -12%, ret_6m -9%, roe +9%, dist_ma200 -8%
- **3M:** fund_quality -33%, mom_12_1 +20%, earnings_yield -20%, ma_cross +12%, ret_6m +5%, downside_vol_60 -4%
- **6M:** ma_cross +41%, earnings_yield -24%, vol_20 -18%, ram -10%, ret_6m +6%, ewma_vol -2%
- **12M:** ram +66%, roe -34%, vol_20 +1%

**3. Which signals matter most at each horizon** (validated status, Alpha):

- **1D:** validated: ret_1m, ret_1d, rsi_14, mr_opportunity, earnings_yield, eps_growth_yoy, rev_growth_yoy, d_credit_3m, gold_mom_3m, rel_strength_6m; supported (positive OOS contribution, not validated): 28 signals.
- **1W:** validated: mom_12_1, dist_ma200, ret_1d, rsi_14, ewma_vol; supported (positive OOS contribution, not validated): 37 signals.
- **1M:** validated: roe, eps_growth_yoy; supported (positive OOS contribution, not validated): 36 signals.
- **3M:** validated: none; supported (positive OOS contribution, not validated): 35 signals.
- **6M:** validated: none; supported (positive OOS contribution, not validated): 29 signals.
- **12M:** validated: none; supported (positive OOS contribution, not validated): 29 signals.

**4–6. By asset class, sector and industry.** Specialisation is decided node by node: a node keeps its own weight for a signal only if that deviation improved its own records out of sample (clustered by year, BH across every node × signal). Kept specialisations at today's cutoff (Alpha):

- **1D:** Class 6, Product type 13, Sector 17, Industry 4, Asset 45 (K = 1000)
- **1W:** Class 10, Product type 28, Sector 9, Industry 10, Asset 47 (K = 5000)
- **1M:** Class 2, Product type 3, Sector 2, Industry 2, Asset 3 (K = 5000)
- **3M:** Class 0, Product type 0, Sector 0, Industry 0, Asset 0 (K = 50)
- **6M:** Class 0, Product type 0, Sector 0, Industry 0, Asset 0 (K = 10)
- **12M:** Class 0, Product type 0, Sector 0, Industry 0, Asset 0 (K = 10)

**7. Assets with enough evidence for asset-specific adjustments** (Alpha, at least one kept asset-level signal): 1D: AGG, AMD, BIL, BNDX, CORP_BAA, CWB, DAX, DBC, DXY, EURUSD, EWG, EWU, FTSE, GBPUSD, HD, IEF, INDA, MA, MBB, MTUM, NZDUSD, QQQ, SCHP, USDCHF, USDJPY, USDMXN, USO, UST10Y, UST30Y, VNQ, XLE, XLRE; 1W: AGG, AUDUSD, BIL, DXY, EEM, FLOT, FTSE, FXE, GLD, GOLD, GS, MA, MTUM, PSQ, QQQ, SSO, USDCHF, USMV, USO, UUP, V, VNQ, WHEAT, XLB; 1M: AGG; 3M: none; 6M: none; 12M: none.

**8. Specialisation depth that works best** (uniform depth with the best walk-forward rank IC at the K chosen for today; and the validated model's own depth):

- **1D:** uniform depth Asset (mean rank IC +0.0881, global +0.0553); validated model: global weights plus specialisations kept at Class, Product type, Sector, Industry, Asset.
- **1W:** uniform depth Asset (mean rank IC +0.0715, global +0.0522); validated model: global weights plus specialisations kept at Class, Product type, Sector, Industry, Asset.
- **1M:** uniform depth Product type (mean rank IC +0.0288, global +0.0106); validated model: global weights plus specialisations kept at Class, Product type, Sector, Industry, Asset.
- **3M:** uniform depth Asset (mean rank IC -0.0075, global -0.0184); validated model: global only (no node deviation survived).
- **6M:** uniform depth Class (mean rank IC +0.0046, global +0.0041); validated model: global only (no node deviation survived).
- **12M:** uniform depth Class (mean rank IC +0.0715, global +0.0644); validated model: global only (no node deviation survived).

**9. Stability across eras** (Alpha, global weights, six era-specific fits):

- **1D:** REGIME DEPENDENT 29, NO EVIDENCE 27, STABLE 13, UNSTABLE 5
- **1W:** NO EVIDENCE 28, REGIME DEPENDENT 21, UNSTABLE 13, STABLE 12
- **1M:** REGIME DEPENDENT 28, NO EVIDENCE 28, UNSTABLE 12, STABLE 6
- **3M:** REGIME DEPENDENT 28, NO EVIDENCE 27, UNSTABLE 12, STABLE 7
- **6M:** NO EVIDENCE 30, REGIME DEPENDENT 25, UNSTABLE 12, STABLE 7
- **12M:** NO EVIDENCE 32, REGIME DEPENDENT 19, UNSTABLE 18, STABLE 5

**10. How different are learned weights from production?** Share-weighted distance (½·Σ|learned share − production share|, 0 = identical allocation, 1 = disjoint):

- **1D:** Alpha 0.84, Directional 0.93
- **1W:** Alpha 0.82, Directional 0.81
- **1M:** Alpha 0.90, Directional 0.84
- **3M:** Alpha 0.89, Directional 0.94
- **6M:** Alpha 0.91, Directional 1.00
- **12M:** Alpha 0.89, Directional 0.99

**11. Does hierarchy improve out-of-sample performance?** Walk-forward rank IC, paired against production on identical records:

| Horizon | Production | Best fit (B) | Validated (C) | Global (D) | Uniform hierarchy (E) | C − D (hierarchy's own gain) |
|---|---|---|---|---|---|---|
| 1D | +0.0411 | +0.0547 (Δ t +2.1) | +0.0522 (Δ t +1.7) | +0.0550 (Δ t +2.3) | +0.0616 (Δ t +3.1) | -0.0028 |
| 1W | +0.0376 | +0.0363 (Δ t -0.2) | +0.0400 (Δ t +0.3) | +0.0526 (Δ t +2.3) | +0.0546 (Δ t +2.0) | -0.0126 |
| 1M | +0.0187 | +0.0155 (Δ t -0.2) | +0.0148 (Δ t -0.2) | +0.0106 (Δ t -0.5) | +0.0162 (Δ t -0.1) | +0.0042 |
| 3M | -0.0084 | -0.0083 (Δ t +0.0) | -0.0065 (Δ t +0.1) | -0.0185 (Δ t -0.3) | -0.0127 (Δ t -0.1) | +0.0120 |
| 6M | -0.0183 | -0.0057 (Δ t +0.2) | +0.0245 (Δ t +0.9) | +0.0041 (Δ t +0.5) | -0.0115 (Δ t +0.1) | +0.0204 |
| 12M | +0.0033 | +0.0232 (Δ t +0.3) | +0.0128 (Δ t +0.2) | +0.0640 (Δ t +1.1) | +0.0182 (Δ t +0.3) | -0.0512 |

**12–13. Today's learned weights and scores.** See §7 (examples) and §8 (every asset × horizon: production score, learned score, best depth, reliability). The ML Lab view *Current learned Shaffer* shows every asset's effective weights with their hierarchy source.

**14. Learned Directional weights** (beyond the PIT base prior; largest validated global weights):

- **1D:** sharpe_252 +12%, ewma_vol -11%, sortino_252 -11%, rsi_14 -9%, ret_1m +6% — Brier gain vs prior +0.00002 (t +0.4), calibration slope +0.62.
- **1W:** dist_ma200 -13%, ewma_vol -11%, mom_12_1 +9%, credit_signal -8%, ma_cross +7% — Brier gain vs prior -0.00005 (t -0.4), calibration slope +0.82.
- **1M:** vix +19%, credit_signal -15%, slope_10y3m -7%, z_20 -5%, bb_pctb -5% — Brier gain vs prior -0.00081 (t -1.8), calibration slope +0.75.
- **3M:** vix +26%, d_credit_3m -17%, slope_10y3m -14%, breakeven_10y -12%, mom_12_1 +11% — Brier gain vs prior -0.00343 (t -2.8), calibration slope +0.36.
- **6M:** slope_10y3m -43%, vix +27%, earnings_yield -9%, rel_value -7%, vol_of_vol +6% — Brier gain vs prior -0.00677 (t -2.7), calibration slope +0.23.
- **12M:** earnings_yield -69%, idio_vol_252 -25%, roe +6% — Brier gain vs prior -0.00259 (t -0.6), calibration slope +0.25.

**15. Learned hedge parameters** — §9.

**16. Validated.** Signal weights (Alpha, VALIDATED): 17 of 444 signal × horizon weights — 1D ret_1m, 1D ret_1d, 1D rsi_14, 1D mr_opportunity, 1D earnings_yield, 1D eps_growth_yoy, 1D rev_growth_yoy, 1D d_credit_3m, 1D gold_mom_3m, 1D rel_strength_6m, 1W mom_12_1, 1W dist_ma200, 1W ret_1d, 1W rsi_14, 1W ewma_vol, 1M roe, 1M eps_growth_yoy. Whole learned systems passing every gate: Alpha 1W simple global, Alpha 1W uniform-depth hierarchy.

**17. Interesting but unverified:** Alpha 1D historical best fit (t +2.1, failed G3); Alpha 1D simple global (t +2.3, failed G3); Alpha 1D uniform-depth hierarchy (t +3.1, failed G3).

**18. Live shadow:** Alpha 1W simple global, Alpha 1W uniform-depth hierarchy.

**19. Could anything eventually replace production?** Only a learned system that first passes the historical gates, then ≥ 60 graded live-shadow outcomes, then your approval. Candidates: Alpha 1W simple global, Alpha 1W uniform-depth hierarchy.

## 3–6. 1D (148,946 records, 155 assets)

### Production vs the learned weight sets (walk-forward, identical records)

| Weight set | Rank IC (t) | Δ rank IC vs production (t) | Quintile spread | Net L/S (t) | Eras won | Split t | FDR | Status |
|---|---|---|---|---|---|---|---|---|
| A production | +0.0411 (+7.7) | — | +0.10% | -0.134% (-6.4) | — | — | — | production |
| B historical best fit | +0.0547 (+12.2) | +0.0136 (+2.1) | +0.18% | +0.008% (+0.4) | 2/4 | +4.7 | ✓ | NOT VALIDATED |
| C validated deployable | +0.0522 (+8.4) | +0.0111 (+1.7) | +0.14% | -0.068% (-2.5) | 1/4 | +2.9 | ✗ | NOT VALIDATED |
| D simple global | +0.0550 (+8.9) | +0.0139 (+2.3) | +0.15% | -0.096% (-3.6) | 2/4 | +5.0 | ✓ | NOT VALIDATED |
| E uniform-depth hierarchy | +0.0616 (+11.7) | +0.0205 (+3.1) | +0.17% | -0.020% (-0.9) | 2/4 | +7.7 | ✓ | NOT VALIDATED |

Directional (P(up) beyond the PIT base prior; gates: Brier and log loss vs prior-only and vs prior + production, balanced accuracy, eras, split, calibration):

| Weight set | Brier | Brier gain vs prior (t) | vs current (t) | Log-loss gain | Balanced acc. | ECE | Slope | Eras won | Status |
|---|---|---|---|---|---|---|---|---|---|
| prior-only | +0.2491 | — | — | — | 50.3% | +0.0071 | +0.66 | — | benchmark |
| B historical best fit | +0.2492 | -0.00003 (-0.7) | -2.6 | -0.00006 | 50.1% | +0.0062 | +0.58 | 1/4 | NOT VALIDATED |
| C validated deployable | +0.2491 | +0.00002 (+0.4) | -2.1 | +0.00004 | 50.2% | +0.0085 | +0.62 | 2/4 | NOT VALIDATED |
| D simple global | +0.2491 | +0.00002 (+0.5) | -2.0 | +0.00004 | 50.2% | +0.0070 | +0.77 | 2/4 | NOT VALIDATED |
| E uniform-depth hierarchy | +0.2492 | -0.00008 (-1.9) | -3.5 | -0.00017 | 50.1% | +0.0122 | +0.38 | 0/4 | NOT VALIDATED |

### Optimisers (global level, walk-forward, every choice nested)

| Optimiser | Alpha rank IC (t) | vs ridge (t) | Directional deviance gain (t) | vs ridge (t) |
|---|---|---|---|---|
| ridge | +0.0553 (+8.9) | — (—) | -1.0 (-3.9) | — (—) |
| ridge (pointwise) | +0.0567 (+9.2) | +0.0015 (+1.8) | — (—) | — (—) |
| elastic net | +0.0607 (+9.6) | +0.0055 (+2.7) | -0.1 (-0.6) | +0.9 (+7.0) |
| sign-constrained | +0.0615 (+10.0) | +0.0062 (+2.0) | -0.4 (-2.5) | +0.6 (+3.3) |
| pairwise logistic (RankNet) | +0.0425 (+7.5) | -0.0128 (-2.6) | — (—) | — (—) |
| ridge + residual boosting | +0.0478 (+7.9) | -0.0074 (-3.5) | -1.4 (-5.2) | -0.5 (-4.1) |

Residual boosting's most used interactions (Alpha; a nonlinear diagnostic, never production): credit_spread × unemp_gap ×8, pctile_252 × ret_1d ×7, dollar_beta_252 × excess_3m ×6, ret_1d × sortino_252 ×6, d_vix_1m × ret_1d ×6, ret_1d × vol_60 ×6.

### Where specialisation stops helping (uniform depth, walk-forward mean rank IC, t)

Every cell is a walk-forward result (each era scored by weights learned before it), but K and depth are held fixed across eras here: choosing the best cell uses hindsight. The nested choice is weight set E above.

| K (pooling) | Global | Class | Product type | Sector | Industry | Asset |
|---|---|---|---|---|---|---|
| 10 | +0.0553 (+8.9) | +0.0624 (+10.1) | +0.0612 (+10.6) | +0.0595 (+11.7) | +0.0568 (+12.0) | +0.0551 (+12.3) |
| 50 | +0.0553 (+8.9) | +0.0635 (+10.5) | +0.0635 (+10.9) | +0.0644 (+12.1) | +0.0615 (+12.3) | +0.0638 (+13.2) |
| 200 | +0.0553 (+8.9) | +0.0656 (+11.1) | +0.0670 (+11.6) | +0.0693 (+12.8) | +0.0664 (+12.8) | +0.0717 (+14.2) |
| 1000 | +0.0553 (+8.9) | +0.0703 (+12.0) | +0.0742 (+12.8) | +0.0776 (+14.0) | +0.0749 (+13.9) | +0.0815 (+15.5) |
| 5000 | +0.0553 (+8.9) | +0.0731 (+12.2) | +0.0783 (+13.2) | +0.0834 (+14.5) | +0.0824 (+14.5) | +0.0881 (+15.7) |
| 20000 | +0.0553 (+8.9) | +0.0720 (+11.7) | +0.0767 (+12.5) | +0.0800 (+13.3) | +0.0799 (+13.4) | +0.0835 (+14.0) |

Validated model today: K = 1000, 28 of 74 global weights trusted (ρ > 0), 85 node × signal specialisations kept (Class 6, Product type 13, Sector 17, Industry 4, Asset 45).

### The most important table — Global node, 1D (Alpha)

Weights are on standardised signals; *shares* are signed shares of total |weight| so production and learned are on one scale. Reliability: VALIDATED (OOS t ≥ 2, BH across signals × horizons, sign stable in ≥ 4 eras) · SUPPORTED (positive OOS contribution) · DESCRIPTIVE ONLY. Current value: mean of today's signal across assets; current contribution: mean of today's validated weight × signal.

| Signal | Family | Production (share) | Best fit | Validated (share) | Trust ρ | Reliability | Era sign stability | Era class | OOS contribution t | Current value | Current contribution |
|---|---|---|---|---|---|---|---|---|---|---|---|
| ret_3m | Momentum | +0.4% | +0.0003 | +0.0003 (+0.1%) | 0.92 | SUPPORTED | 3/6 | REGIME DEPENDENT | +3.6 | -0.054 | -0.0000 |
| ret_6m | Momentum | +0.2% | -0.0006 | -0.0000 (-0.0%) | 0.00 | DESCRIPTIVE ONLY | 2/6 | NO EVIDENCE | -0.8 | +0.040 | +0.0000 |
| ret_12m | Momentum | +0.1% | -0.0104 | -0.0081 (-4.0%) | 0.78 | SUPPORTED | 2/6 | NO EVIDENCE | +2.1 | +0.026 | +0.0000 |
| mom_12_1 | Momentum | +0.2% | +0.0090 | +0.0087 (+4.2%) | 0.96 | SUPPORTED | 3/6 | NO EVIDENCE | +5.1 | +0.077 | -0.0000 |
| ret_1m | Momentum | -6.4% | +0.0232 | +0.0217 (+10.5%) | 0.94 | VALIDATED | 5/6 | STABLE | +3.9 | -0.161 | +0.0001 |
| ma_cross | Trend | +0.2% | +0.0070 | +0.0062 (+3.0%) | 0.88 | SUPPORTED | 3/6 | NO EVIDENCE | +2.9 | +0.004 | -0.0000 |
| dist_ma200 | Trend | +0.2% | -0.0126 | -0.0124 (-6.0%) | 0.98 | SUPPORTED | 3/6 | REGIME DEPENDENT | +8.2 | -0.042 | -0.0000 |
| macd | Trend | +0.9% | -0.0117 | -0.0000 (-0.0%) | 0.00 | DESCRIPTIVE ONLY | 5/6 | REGIME DEPENDENT | -0.6 | -0.113 | +0.0000 |
| pctile_252 | Trend | +0.4% | -0.0027 | -0.0000 (-0.0%) | 0.00 | DESCRIPTIVE ONLY | 4/6 | REGIME DEPENDENT | -0.6 | -0.119 | +0.0000 |
| trend_quality | Trend | +0.4% | +0.0018 | +0.0000 (+0.0%) | 0.00 | DESCRIPTIVE ONLY | 4/6 | REGIME DEPENDENT | -0.7 | -0.035 | +0.0000 |
| ret_1d | Mean Reversion | -43.5% | -0.0257 | -0.0216 (-10.5%) | 0.84 | VALIDATED | 6/6 | STABLE | +2.5 | -0.075 | -0.0054 |
| ret_1w | Mean Reversion | -20.3% | -0.0072 | -0.0000 (-0.0%) | 0.00 | SUPPORTED | 5/6 | UNSTABLE | +0.8 | -0.096 | +0.0000 |
| z_20 | Mean Reversion | -5.3% | +0.0030 | +0.0000 (+0.0%) | 0.00 | DESCRIPTIVE ONLY | 4/6 | NO EVIDENCE | -1.0 | -0.224 | +0.0000 |
| z_50 | Mean Reversion | -2.8% | -0.0018 | -0.0000 (-0.0%) | 0.00 | DESCRIPTIVE ONLY | 3/6 | REGIME DEPENDENT | -3.0 | -0.207 | +0.0001 |
| rsi_14 | Mean Reversion | -4.2% | -0.0254 | -0.0246 (-11.9%) | 0.97 | VALIDATED | 6/6 | STABLE | +5.6 | -0.238 | -0.0003 |
| bb_pctb | Mean Reversion | -5.3% | +0.0030 | +0.0000 (+0.0%) | 0.00 | DESCRIPTIVE ONLY | 4/6 | NO EVIDENCE | -1.0 | -0.224 | +0.0000 |
| mr_opportunity | Mean Reversion | +3.4% | -0.0119 | -0.0113 (-5.5%) | 0.94 | VALIDATED | 4/6 | REGIME DEPENDENT | +4.3 | +0.241 | -0.0003 |
| value_5y | Valuation | +0.0% | -0.0017 | -0.0004 (-0.2%) | 0.26 | SUPPORTED | 4/6 | REGIME DEPENDENT | +1.2 | -0.117 | -0.0000 |
| earnings_yield | Valuation | +0.0% | +0.0023 | +0.0022 (+1.1%) | 0.98 | VALIDATED | 5/6 | UNSTABLE | +8.2 | -0.036 | +0.0000 |
| pe_rel_5y | Valuation | -0.0% | -0.0001 | -0.0000 (-0.0%) | 0.00 | DESCRIPTIVE ONLY | 2/6 | NO EVIDENCE | -12.9 | -0.041 | +0.0000 |
| book_to_price | Valuation | +0.0% | -0.0029 | -0.0012 (-0.6%) | 0.42 | SUPPORTED | 5/6 | STABLE | +1.3 | -0.074 | -0.0000 |
| sales_yield | Valuation | +0.0% | -0.0006 | -0.0000 (-0.0%) | 0.00 | DESCRIPTIVE ONLY | 2/6 | NO EVIDENCE | -2.4 | -0.120 | +0.0001 |
| net_margin | Fundamental Quality | +0.0% | -0.0008 | -0.0000 (-0.0%) | 0.00 | DESCRIPTIVE ONLY | 3/6 | REGIME DEPENDENT | -6.4 | +0.075 | +0.0000 |
| roe | Fundamental Quality | +0.0% | +0.0014 | +0.0000 (+0.0%) | 0.00 | DESCRIPTIVE ONLY | 3/6 | NO EVIDENCE | -6.3 | +0.042 | +0.0000 |
| fund_quality | Fundamental Quality | +0.0% | -0.0035 | -0.0034 (-1.7%) | 0.99 | SUPPORTED | 2/6 | NO EVIDENCE | +8.4 | +0.042 | +0.0000 |
| eps_growth_yoy | Fundamental Growth | +0.0% | +0.0038 | +0.0037 (+1.8%) | 0.99 | VALIDATED | 5/6 | STABLE | +8.5 | -0.005 | +0.0000 |
| rev_growth_yoy | Fundamental Growth | +0.0% | +0.0009 | +0.0009 (+0.4%) | 0.96 | VALIDATED | 4/6 | NO EVIDENCE | +5.0 | +0.043 | -0.0000 |
| sharpe_252 | Risk-Adjusted Performance | +0.1% | +0.0071 | +0.0048 (+2.3%) | 0.67 | SUPPORTED | 3/6 | NO EVIDENCE | +1.7 | -0.133 | -0.0001 |
| sortino_252 | Risk-Adjusted Performance | +0.1% | -0.0069 | -0.0068 (-3.3%) | 0.98 | SUPPORTED | 3/6 | NO EVIDENCE | +6.8 | -0.133 | +0.0001 |
| alpha_252 | Risk-Adjusted Performance | +0.2% | +0.0014 | +0.0000 (+0.0%) | 0.00 | DESCRIPTIVE ONLY | 3/6 | NO EVIDENCE | -2.3 | -0.010 | +0.0000 |
| ram | Risk-Adjusted Performance | +0.4% | +0.0121 | +0.0065 (+3.1%) | 0.53 | SUPPORTED | 4/6 | UNSTABLE | +1.5 | +0.024 | -0.0000 |
| vol_20 | Volatility | +0.0% | -0.0020 | -0.0018 (-0.9%) | 0.91 | SUPPORTED | 3/6 | REGIME DEPENDENT | +3.4 | -0.145 | +0.0000 |
| vol_60 | Volatility | +0.0% | +0.0075 | +0.0000 (+0.0%) | 0.00 | DESCRIPTIVE ONLY | 3/6 | REGIME DEPENDENT | -0.1 | -0.068 | +0.0000 |
| downside_vol_60 | Volatility | +0.0% | -0.0093 | -0.0000 (-0.0%) | 0.00 | SUPPORTED | 4/6 | NO EVIDENCE | +0.3 | -0.081 | +0.0000 |
| ewma_vol | Volatility | +0.0% | -0.0062 | -0.0053 (-2.6%) | 0.86 | SUPPORTED | 3/6 | NO EVIDENCE | +2.6 | -0.117 | +0.0000 |
| garch_vol | Volatility | +0.0% | +0.0032 | +0.0000 (+0.0%) | 0.00 | DESCRIPTIVE ONLY | 4/6 | NO EVIDENCE | -1.2 | -0.123 | +0.0000 |
| vol_ratio | Volatility | +0.0% | -0.0069 | -0.0000 (-0.0%) | 0.00 | SUPPORTED | 4/6 | REGIME DEPENDENT | +0.2 | -0.120 | -0.0000 |
| vol_of_vol | Volatility | +0.0% | +0.0004 | +0.0000 (+0.0%) | 0.00 | DESCRIPTIVE ONLY | 3/6 | NO EVIDENCE | -1.7 | -0.108 | +0.0000 |
| vol_pctile | Volatility | +0.0% | +0.0118 | +0.0000 (+0.0%) | 0.00 | SUPPORTED | 5/6 | STABLE | +0.4 | -0.094 | +0.0000 |
| skew_60 | Statistical / Time Series | -0.9% | -0.0009 | -0.0004 (-0.2%) | 0.41 | SUPPORTED | 4/6 | NO EVIDENCE | +1.3 | +0.135 | +0.0000 |
| kurt_60 | Statistical / Time Series | +0.0% | +0.0007 | +0.0000 (+0.0%) | 0.00 | DESCRIPTIVE ONLY | 4/6 | NO EVIDENCE | -1.7 | -0.116 | +0.0000 |
| ar1_63 | Statistical / Time Series | +0.0% | -0.0010 | -0.0000 (-0.0%) | 0.00 | DESCRIPTIVE ONLY | 4/6 | NO EVIDENCE | -0.8 | +0.048 | +0.0000 |
| acf1_252 | Statistical / Time Series | +0.0% | +0.0037 | +0.0000 (+0.0%) | 0.00 | DESCRIPTIVE ONLY | 5/6 | REGIME DEPENDENT | -0.0 | -0.028 | +0.0000 |
| half_life | Statistical / Time Series | +0.0% | +0.0005 | +0.0000 (+0.0%) | 0.00 | DESCRIPTIVE ONLY | 3/6 | REGIME DEPENDENT | -1.7 | -0.070 | +0.0000 |
| adf_t | Statistical / Time Series | +0.0% | +0.0011 | +0.0009 (+0.4%) | 0.80 | SUPPORTED | 4/6 | REGIME DEPENDENT | +2.2 | -0.098 | +0.0000 |
| idio_vol_252 | Statistical / Time Series | -0.1% | -0.0018 | -0.0000 (-0.0%) | 0.00 | SUPPORTED | 3/6 | UNSTABLE | +0.0 | +0.039 | +0.0000 |
| drawdown_252 | Statistical / Time Series | +0.0% | -0.0015 | -0.0000 (-0.0%) | 0.00 | SUPPORTED | 4/6 | REGIME DEPENDENT | +0.1 | -0.011 | +0.0000 |
| y10 | Rates | +0.0% | +0.0217 | +0.0180 (+8.8%) | 0.83 | SUPPORTED | 5/6 | STABLE | +2.4 | +0.308 | -0.0242 |
| d_y10_3m | Rates | +0.0% | -0.0067 | -0.0000 (-0.0%) | 0.00 | DESCRIPTIVE ONLY | 4/6 | REGIME DEPENDENT | -1.9 | +0.541 | -0.0026 |
| slope_10y3m | Rates | +0.0% | -0.0060 | -0.0000 (-0.0%) | 0.00 | DESCRIPTIVE ONLY | 4/6 | REGIME DEPENDENT | -0.2 | -0.215 | -0.0053 |
| d_slope_3m | Rates | +0.0% | +0.0039 | +0.0000 (+0.0%) | 0.00 | DESCRIPTIVE ONLY | 4/6 | REGIME DEPENDENT | -2.1 | +0.192 | +0.0000 |
| real_y10 | Rates | +0.0% | +0.0099 | +0.0078 (+3.8%) | 0.79 | SUPPORTED | 5/6 | REGIME DEPENDENT | +2.2 | +0.797 | -0.0042 |
| breakeven_10y | Rates | +0.0% | -0.0051 | -0.0000 (-0.0%) | 0.00 | DESCRIPTIVE ONLY | 4/6 | NO EVIDENCE | -0.5 | +0.325 | +0.0000 |
| rate_duration | Rates | +0.7% | +0.0034 | +0.0000 (+0.0%) | 0.00 | DESCRIPTIVE ONLY | 4/6 | REGIME DEPENDENT | -1.0 | -0.201 | +0.0000 |
| credit_spread | Credit | +0.0% | +0.0086 | +0.0000 (+0.0%) | 0.00 | DESCRIPTIVE ONLY | 5/6 | STABLE | -0.7 | -0.598 | +0.0000 |
| d_credit_3m | Credit | +0.0% | -0.0096 | -0.0083 (-4.0%) | 0.87 | VALIDATED | 4/6 | REGIME DEPENDENT | +2.7 | -0.088 | -0.0005 |
| credit_signal | Credit | +0.0% | -0.0036 | -0.0000 (-0.0%) | 0.00 | DESCRIPTIVE ONLY | 3/6 | NO EVIDENCE | -1.0 | -0.400 | +0.0000 |
| cpi_yoy | Macro | +0.0% | +0.0028 | +0.0000 (+0.0%) | 0.00 | DESCRIPTIVE ONLY | 3/6 | REGIME DEPENDENT | -0.7 | +0.253 | +0.0000 |
| unemp_gap | Macro | +0.0% | +0.0073 | +0.0000 (+0.0%) | 0.00 | DESCRIPTIVE ONLY | 5/6 | REGIME DEPENDENT | -0.0 | -0.184 | +0.0000 |
| oil_mom_3m | Macro | +0.0% | -0.0016 | -0.0000 (-0.0%) | 0.00 | DESCRIPTIVE ONLY | 4/6 | NO EVIDENCE | -2.3 | +0.611 | +0.0000 |
| gold_mom_3m | Macro | +0.0% | -0.0148 | -0.0133 (-6.4%) | 0.90 | VALIDATED | 6/6 | STABLE | +3.1 | +0.050 | +0.0005 |
| nfci | Liquidity | +0.0% | -0.0048 | -0.0000 (-0.0%) | 0.00 | DESCRIPTIVE ONLY | 2/6 | REGIME DEPENDENT | -1.3 | +0.165 | +0.0000 |
| fed_bs_growth | Liquidity | +0.0% | -0.0038 | -0.0000 (-0.0%) | 0.00 | DESCRIPTIVE ONLY | 3/6 | NO EVIDENCE | -0.0 | -0.073 | +0.0000 |
| vix | Liquidity | +0.0% | +0.0149 | +0.0000 (+0.0%) | 0.00 | SUPPORTED | 6/6 | STABLE | +0.9 | -0.236 | +0.0000 |
| d_vix_1m | Liquidity | -1.4% | +0.0132 | +0.0000 (+0.0%) | 0.00 | SUPPORTED | 5/6 | STABLE | +0.8 | +0.022 | +0.0000 |
| volume_z | Liquidity | +1.0% | +0.0070 | +0.0000 (+0.0%) | 0.00 | SUPPORTED | 6/6 | STABLE | +0.9 | +0.327 | +0.0000 |
| dollar_mom_3m | Cross-Asset | +0.0% | +0.0087 | +0.0000 (+0.0%) | 0.00 | DESCRIPTIVE ONLY | 3/6 | REGIME DEPENDENT | -0.5 | -0.059 | +0.0000 |
| beta_252 | Cross-Asset | +0.0% | -0.0028 | -0.0017 (-0.8%) | 0.61 | SUPPORTED | 6/6 | STABLE | +1.6 | -0.071 | -0.0000 |
| corr_252 | Cross-Asset | +0.0% | +0.0015 | +0.0000 (+0.0%) | 0.00 | DESCRIPTIVE ONLY | 3/6 | NO EVIDENCE | -0.0 | -0.221 | +0.0000 |
| rate_beta_252 | Cross-Asset | +0.0% | -0.0003 | -0.0000 (-0.0%) | 0.00 | DESCRIPTIVE ONLY | 3/6 | NO EVIDENCE | -4.0 | +0.014 | +0.0000 |
| dollar_beta_252 | Cross-Asset | +0.0% | +0.0001 | +0.0000 (+0.0%) | 0.00 | DESCRIPTIVE ONLY | 3/6 | REGIME DEPENDENT | -1.4 | -0.248 | +0.0000 |
| excess_3m | Relative Value | +0.4% | +0.0062 | +0.0000 (+0.0%) | 0.00 | DESCRIPTIVE ONLY | 4/6 | UNSTABLE | -0.1 | -0.052 | +0.0002 |
| rel_strength_6m | Relative Value | +0.2% | +0.0037 | +0.0034 (+1.7%) | 0.92 | VALIDATED | 4/6 | REGIME DEPENDENT | +3.6 | -0.281 | +0.0000 |
| rel_value | Relative Value | +0.3% | +0.0016 | +0.0000 (+0.0%) | 0.00 | SUPPORTED | 3/6 | REGIME DEPENDENT | +0.6 | +0.110 | +0.0000 |

### Family weights and their signals (1D, Alpha, validated shares)

| Family | Production share | Learned share | Signals (learned) |
|---|---|---|---|
| Mean Reversion | -77.9% | -27.9% | ret_1d -10.5%, rsi_14 -11.9%, mr_opportunity -5.5% |
| Rates | +0.7% | +12.6% | y10 +8.8%, real_y10 +3.8% |
| Momentum | -5.6% | +10.9% | ret_3m +0.1%, ret_12m -4.0%, mom_12_1 +4.2%, ret_1m +10.5% |
| Macro | +0.0% | -6.4% | gold_mom_3m -6.4% |
| Credit | +0.0% | -4.0% | d_credit_3m -4.0% |
| Volatility | +0.0% | -3.5% | vol_20 -0.9%, ewma_vol -2.6% |
| Trend | +2.1% | -3.0% | ma_cross +3.0%, dist_ma200 -6.0% |
| Fundamental Growth | +0.0% | +2.3% | eps_growth_yoy +1.8%, rev_growth_yoy +0.4% |
| Risk-Adjusted Performance | +0.7% | +2.2% | sharpe_252 +2.3%, sortino_252 -3.3%, ram +3.1% |
| Fundamental Quality | +0.0% | -1.7% | fund_quality -1.7% |
| Relative Value | +1.0% | +1.7% | rel_strength_6m +1.7% |
| Cross-Asset | +0.0% | -0.8% | beta_252 -0.8% |
| Valuation | +0.0% | +0.3% | value_5y -0.2%, earnings_yield +1.1%, book_to_price -0.6% |
| Statistical / Time Series | -1.0% | +0.2% | skew_60 -0.2%, adf_t +0.4% |
| Liquidity | -0.4% | +0.0% | — |

### Confidence, regime, decay and applicability (1D; global, walk-forward)

| Construction | Alpha rank IC (t) | vs free weights (t) | Directional deviance gain (t) | vs free (t) |
|---|---|---|---|---|
| free signed weights on x (learned) | +0.0553 (+8.9) | — (—) | -1.0 (-3.9) | — (—) |
| production structure δ·x·c·r·d | +0.0450 (+8.3) | -0.0102 (-1.8) | +0.2 (+2.0) | +1.2 (+5.4) |
| without confidence (γc = 0) | +0.0464 (+8.3) | -0.0088 (-1.5) | +0.2 (+1.4) | +1.1 (+5.0) |
| without regime (γr = 0) | +0.0454 (+8.4) | -0.0098 (-1.7) | +0.2 (+2.2) | +1.2 (+5.4) |
| without decay (γd = 0) | +0.0469 (+8.5) | -0.0083 (-1.4) | +0.2 (+2.0) | +1.2 (+5.4) |
| δ·x only | +0.0502 (+8.8) | -0.0050 (-0.9) | +0.2 (+1.3) | +1.1 (+5.0) |
| applicability mask (x only where applicable) | +0.0471 (+8.7) | -0.0081 (-1.5) | -1.2 (-4.9) | -0.2 (-0.7) |

Regime-dependent weights (1D, Alpha): ret_3m (rates: rising_rates -0.015, falling_rates +0.018, t -2.9); dist_ma200 (volatility: high_vol +0.012, low_vol -0.040, t +2.5, rates: rising_rates +0.011, falling_rates -0.033, t +2.2); macd (growth: expansion -0.032, recession +0.067, t -2.2); pctile_252 (rates: rising_rates -0.015, falling_rates +0.011, t -2.1, growth: expansion -0.004, recession +0.025, t -3.0); trend_quality (rates: rising_rates -0.011, falling_rates +0.017, t -2.1); z_50 (growth: expansion -0.006, recession +0.023, t -2.9); mr_opportunity (rates: rising_rates -0.001, falling_rates -0.022, t +3.0); value_5y (rates: rising_rates +0.007, falling_rates -0.011, t +2.2, growth: expansion -0.004, recession +0.009, t -9.4).

### Score magnitude (1D) — does a larger |score| mean a stronger outcome?

| |score| | Validated: records | relative return | hit | rank IC | Production: records | relative return | hit |
|---|---|---|---|---|---|---|---|
| 0–10 | 113,105 | +0.042% | +0.9% | +0.040 | 101,007 | +0.019% | +0.7% |
| 10–25 | 7,979 | +0.140% | +3.1% | +0.086 | 14,197 | +0.018% | +1.4% |
| 25–50 | 299 | +0.222% | +7.9% | +0.075 | 5,083 | +0.069% | +1.3% |
| 50–75 | 3 | — | — | — | 954 | +0.080% | +2.4% |
| 75– | 0 | — | — | — | 145 | +0.567% | +10.0% |

Monotonic (every bucket populated and rising): validated no, production no. Directional magnitude = its calibration: 50%–55% predicted 52.2% → realised 52.7% (n 117,381), 55%–60% predicted 56.3% → realised 54.8% (n 3,333), 60%–65% predicted 61.8% → realised 57.2% (n 500), 65%–70% predicted 67.8% → realised 67.1% (n 79), 70%–75% predicted 72.5% → realised 79.5% (n 78), 75%–100% predicted 76.8% → realised 73.3% (n 15).

## 3–6. 1W (149,672 records, 155 assets)

### Production vs the learned weight sets (walk-forward, identical records)

| Weight set | Rank IC (t) | Δ rank IC vs production (t) | Quintile spread | Net L/S (t) | Eras won | Split t | FDR | Status |
|---|---|---|---|---|---|---|---|---|
| A production | +0.0376 (+6.1) | — | +0.22% | +0.008% (+0.1) | — | — | — | production |
| B historical best fit | +0.0363 (+6.6) | -0.0013 (-0.2) | +0.31% | +0.158% (+3.2) | 1/4 | +0.9 | ✗ | NOT VALIDATED |
| C validated deployable | +0.0400 (+5.7) | +0.0024 (+0.3) | +0.31% | +0.096% (+1.5) | 3/4 | +1.1 | ✗ | NOT VALIDATED |
| D simple global | +0.0526 (+7.8) | +0.0150 (+2.3) | +0.42% | +0.154% (+2.6) | 3/4 | +2.1 | ✓ | LIVE SHADOW ELIGIBLE |
| E uniform-depth hierarchy | +0.0546 (+7.6) | +0.0169 (+2.0) | +0.40% | +0.192% (+3.2) | 3/4 | +3.4 | ✓ | LIVE SHADOW ELIGIBLE |

Directional (P(up) beyond the PIT base prior; gates: Brier and log loss vs prior-only and vs prior + production, balanced accuracy, eras, split, calibration):

| Weight set | Brier | Brier gain vs prior (t) | vs current (t) | Log-loss gain | Balanced acc. | ECE | Slope | Eras won | Status |
|---|---|---|---|---|---|---|---|---|---|
| prior-only | +0.2471 | — | — | — | 50.7% | +0.0119 | +1.06 | — | benchmark |
| B historical best fit | +0.2472 | -0.00006 (-0.5) | -1.1 | -0.00009 | 50.7% | +0.0117 | +0.81 | 1/4 | NOT VALIDATED |
| C validated deployable | +0.2472 | -0.00005 (-0.4) | -0.8 | -0.00007 | 50.7% | +0.0117 | +0.82 | 1/4 | NOT VALIDATED |
| D simple global | +0.2473 | -0.00016 (-1.2) | -1.6 | -0.00029 | 50.7% | +0.0146 | +0.74 | 1/4 | NOT VALIDATED |
| E uniform-depth hierarchy | +0.2471 | -0.00001 (-0.1) | -0.7 | -0.00002 | 50.6% | +0.0075 | +0.97 | 1/4 | NOT VALIDATED |

### Optimisers (global level, walk-forward, every choice nested)

| Optimiser | Alpha rank IC (t) | vs ridge (t) | Directional deviance gain (t) | vs ridge (t) |
|---|---|---|---|---|
| ridge | +0.0522 (+7.8) | — (—) | -1.0 (-2.5) | — (—) |
| ridge (pointwise) | +0.0523 (+7.8) | +0.0001 (+0.1) | — (—) | — (—) |
| elastic net | +0.0516 (+7.7) | -0.0006 (-0.5) | -0.2 (-0.8) | +0.8 (+3.6) |
| sign-constrained | +0.0535 (+8.2) | +0.0013 (+0.4) | -0.2 (-1.0) | +0.8 (+2.7) |
| pairwise logistic (RankNet) | +0.0392 (+6.0) | -0.0130 (-2.7) | — (—) | — (—) |
| ridge + residual boosting | +0.0496 (+7.5) | -0.0026 (-1.2) | -1.6 (-3.3) | -0.7 (-3.5) |

Residual boosting's most used interactions (Alpha; a nonlinear diagnostic, never production): vix × vol_20 ×10, dist_ma200 × dollar_mom_3m ×7, dollar_mom_3m × ret_6m ×7, ret_1w × vol_20 ×6, dollar_mom_3m × y10 ×6, dist_ma200 × z_50 ×6.

### Where specialisation stops helping (uniform depth, walk-forward mean rank IC, t)

Every cell is a walk-forward result (each era scored by weights learned before it), but K and depth are held fixed across eras here: choosing the best cell uses hindsight. The nested choice is weight set E above.

| K (pooling) | Global | Class | Product type | Sector | Industry | Asset |
|---|---|---|---|---|---|---|
| 10 | +0.0522 (+7.8) | +0.0412 (+6.3) | +0.0453 (+6.7) | +0.0417 (+6.7) | +0.0405 (+6.9) | +0.0364 (+6.7) |
| 50 | +0.0522 (+7.8) | +0.0467 (+7.1) | +0.0515 (+7.7) | +0.0486 (+7.9) | +0.0473 (+8.1) | +0.0434 (+7.8) |
| 200 | +0.0522 (+7.8) | +0.0516 (+8.0) | +0.0596 (+9.1) | +0.0560 (+9.2) | +0.0542 (+9.4) | +0.0540 (+9.6) |
| 1000 | +0.0522 (+7.8) | +0.0587 (+9.4) | +0.0667 (+10.7) | +0.0640 (+10.8) | +0.0621 (+10.9) | +0.0638 (+11.4) |
| 5000 | +0.0522 (+7.8) | +0.0652 (+10.2) | +0.0710 (+11.2) | +0.0698 (+11.4) | +0.0684 (+11.4) | +0.0708 (+12.0) |
| 20000 | +0.0522 (+7.8) | +0.0651 (+9.8) | +0.0703 (+10.7) | +0.0703 (+10.9) | +0.0696 (+10.9) | +0.0715 (+11.3) |

Validated model today: K = 5000, 27 of 74 global weights trusted (ρ > 0), 104 node × signal specialisations kept (Class 10, Product type 28, Sector 9, Industry 10, Asset 47).

### The most important table — Global node, 1W (Alpha)

Weights are on standardised signals; *shares* are signed shares of total |weight| so production and learned are on one scale. Reliability: VALIDATED (OOS t ≥ 2, BH across signals × horizons, sign stable in ≥ 4 eras) · SUPPORTED (positive OOS contribution) · DESCRIPTIVE ONLY. Current value: mean of today's signal across assets; current contribution: mean of today's validated weight × signal.

| Signal | Family | Production (share) | Best fit | Validated (share) | Trust ρ | Reliability | Era sign stability | Era class | OOS contribution t | Current value | Current contribution |
|---|---|---|---|---|---|---|---|---|---|---|---|
| ret_3m | Momentum | +1.8% | -0.0102 | -0.0020 (-0.8%) | 0.20 | SUPPORTED | 3/6 | NO EVIDENCE | +1.1 | -0.045 | +0.0000 |
| ret_6m | Momentum | +1.0% | +0.0052 | +0.0039 (+1.6%) | 0.76 | SUPPORTED | 4/6 | NO EVIDENCE | +2.0 | +0.024 | -0.0000 |
| ret_12m | Momentum | +0.6% | -0.0211 | -0.0011 (-0.5%) | 0.05 | SUPPORTED | 4/6 | UNSTABLE | +1.0 | +0.052 | -0.0000 |
| mom_12_1 | Momentum | +0.9% | +0.0346 | +0.0318 (+13.1%) | 0.92 | VALIDATED | 6/6 | STABLE | +3.5 | +0.087 | -0.0000 |
| ret_1m | Momentum | -3.5% | +0.0162 | +0.0120 (+5.0%) | 0.74 | SUPPORTED | 4/6 | UNSTABLE | +2.0 | -0.104 | +0.0000 |
| ma_cross | Trend | +0.6% | +0.0180 | +0.0058 (+2.4%) | 0.32 | SUPPORTED | 4/6 | NO EVIDENCE | +1.2 | +0.015 | +0.0000 |
| dist_ma200 | Trend | +0.8% | -0.0341 | -0.0330 (-13.6%) | 0.97 | VALIDATED | 5/6 | STABLE | +5.6 | -0.018 | -0.0000 |
| macd | Trend | +4.1% | +0.0088 | +0.0000 (+0.0%) | 0.00 | SUPPORTED | 3/6 | NO EVIDENCE | +0.3 | -0.049 | +0.0000 |
| pctile_252 | Trend | +2.4% | -0.0032 | -0.0000 (-0.0%) | 0.00 | SUPPORTED | 4/6 | NO EVIDENCE | +0.4 | -0.097 | -0.0001 |
| trend_quality | Trend | +1.3% | -0.0053 | -0.0000 (-0.0%) | 0.00 | SUPPORTED | 5/6 | STABLE | +0.4 | -0.031 | +0.0000 |
| ret_1d | Mean Reversion | -15.2% | -0.0206 | -0.0182 (-7.5%) | 0.88 | VALIDATED | 6/6 | STABLE | +2.9 | +0.054 | +0.0004 |
| ret_1w | Mean Reversion | -16.2% | -0.0102 | -0.0021 (-0.9%) | 0.21 | SUPPORTED | 5/6 | STABLE | +1.1 | -0.203 | +0.0000 |
| z_20 | Mean Reversion | -7.2% | -0.0076 | -0.0000 (-0.0%) | 0.00 | DESCRIPTIVE ONLY | 4/6 | UNSTABLE | -1.0 | -0.277 | +0.0003 |
| z_50 | Mean Reversion | -5.4% | +0.0103 | +0.0036 (+1.5%) | 0.35 | SUPPORTED | 6/6 | STABLE | +1.2 | -0.198 | -0.0002 |
| rsi_14 | Mean Reversion | -7.0% | -0.0133 | -0.0114 (-4.7%) | 0.86 | VALIDATED | 5/6 | REGIME DEPENDENT | +2.7 | -0.218 | -0.0002 |
| bb_pctb | Mean Reversion | -7.2% | -0.0076 | -0.0000 (-0.0%) | 0.00 | DESCRIPTIVE ONLY | 4/6 | UNSTABLE | -1.0 | -0.277 | +0.0003 |
| mr_opportunity | Mean Reversion | +4.5% | -0.0190 | -0.0150 (-6.2%) | 0.79 | SUPPORTED | 5/6 | STABLE | +2.2 | +0.303 | +0.0000 |
| value_5y | Valuation | +0.0% | +0.0015 | +0.0000 (+0.0%) | 0.00 | DESCRIPTIVE ONLY | 5/6 | REGIME DEPENDENT | -0.2 | -0.132 | +0.0000 |
| earnings_yield | Valuation | +0.0% | -0.0008 | -0.0006 (-0.2%) | 0.79 | SUPPORTED | 3/6 | REGIME DEPENDENT | +2.2 | -0.040 | +0.0000 |
| pe_rel_5y | Valuation | -0.0% | -0.0006 | -0.0000 (-0.0%) | 0.00 | SUPPORTED | 4/6 | UNSTABLE | +0.9 | -0.036 | +0.0000 |
| book_to_price | Valuation | +0.0% | -0.0047 | -0.0029 (-1.2%) | 0.62 | SUPPORTED | 5/6 | UNSTABLE | +1.6 | -0.076 | +0.0000 |
| sales_yield | Valuation | +0.0% | -0.0001 | -0.0000 (-0.0%) | 0.00 | DESCRIPTIVE ONLY | 4/6 | NO EVIDENCE | -1.0 | -0.123 | +0.0000 |
| net_margin | Fundamental Quality | +0.0% | -0.0018 | -0.0000 (-0.0%) | 0.00 | DESCRIPTIVE ONLY | 4/6 | NO EVIDENCE | -0.6 | +0.075 | +0.0000 |
| roe | Fundamental Quality | +0.0% | +0.0023 | +0.0000 (+0.0%) | 0.00 | DESCRIPTIVE ONLY | 4/6 | REGIME DEPENDENT | -2.4 | +0.042 | +0.0000 |
| fund_quality | Fundamental Quality | +0.0% | -0.0014 | -0.0008 (-0.3%) | 0.56 | SUPPORTED | 2/6 | NO EVIDENCE | +1.5 | +0.042 | -0.0000 |
| eps_growth_yoy | Fundamental Growth | +0.0% | +0.0005 | +0.0000 (+0.0%) | 0.00 | DESCRIPTIVE ONLY | 4/6 | REGIME DEPENDENT | -2.4 | -0.005 | -0.0000 |
| rev_growth_yoy | Fundamental Growth | +0.0% | +0.0003 | +0.0000 (+0.0%) | 0.00 | DESCRIPTIVE ONLY | 4/6 | REGIME DEPENDENT | -2.0 | +0.043 | +0.0000 |
| sharpe_252 | Risk-Adjusted Performance | +0.4% | +0.0166 | +0.0146 (+6.0%) | 0.88 | SUPPORTED | 3/6 | REGIME DEPENDENT | +2.9 | -0.099 | -0.0000 |
| sortino_252 | Risk-Adjusted Performance | +0.4% | -0.0193 | -0.0172 (-7.1%) | 0.89 | SUPPORTED | 3/6 | NO EVIDENCE | +3.0 | -0.100 | +0.0000 |
| alpha_252 | Risk-Adjusted Performance | +0.8% | +0.0007 | +0.0000 (+0.0%) | 0.00 | SUPPORTED | 3/6 | REGIME DEPENDENT | +0.5 | +0.012 | +0.0000 |
| ram | Risk-Adjusted Performance | +1.7% | +0.0130 | +0.0100 (+4.1%) | 0.77 | SUPPORTED | 5/6 | REGIME DEPENDENT | +2.1 | +0.027 | +0.0000 |
| vol_20 | Volatility | +0.0% | -0.0111 | -0.0074 (-3.0%) | 0.66 | SUPPORTED | 4/6 | NO EVIDENCE | +1.7 | -0.128 | -0.0000 |
| vol_60 | Volatility | +0.0% | -0.0020 | -0.0000 (-0.0%) | 0.00 | SUPPORTED | 3/6 | NO EVIDENCE | +0.8 | -0.044 | +0.0000 |
| downside_vol_60 | Volatility | +0.0% | -0.0064 | -0.0000 (-0.0%) | 0.00 | DESCRIPTIVE ONLY | 2/6 | NO EVIDENCE | -1.7 | -0.049 | +0.0000 |
| ewma_vol | Volatility | +0.0% | +0.0107 | +0.0100 (+4.1%) | 0.93 | VALIDATED | 4/6 | NO EVIDENCE | +3.9 | -0.106 | -0.0000 |
| garch_vol | Volatility | +0.0% | +0.0038 | +0.0000 (+0.0%) | 0.00 | DESCRIPTIVE ONLY | 5/6 | STABLE | -0.9 | -0.119 | +0.0000 |
| vol_ratio | Volatility | +0.0% | -0.0102 | -0.0000 (-0.0%) | 0.00 | DESCRIPTIVE ONLY | 5/6 | UNSTABLE | -0.5 | -0.107 | +0.0000 |
| vol_of_vol | Volatility | +0.0% | +0.0015 | +0.0000 (+0.0%) | 0.00 | DESCRIPTIVE ONLY | 4/6 | NO EVIDENCE | -0.3 | -0.110 | +0.0000 |
| vol_pctile | Volatility | +0.0% | +0.0169 | +0.0098 (+4.0%) | 0.58 | SUPPORTED | 6/6 | STABLE | +1.6 | -0.107 | -0.0000 |
| skew_60 | Statistical / Time Series | -2.4% | +0.0034 | +0.0000 (+0.0%) | 0.00 | DESCRIPTIVE ONLY | 5/6 | UNSTABLE | -0.9 | +0.117 | +0.0000 |
| kurt_60 | Statistical / Time Series | +0.0% | +0.0008 | +0.0000 (+0.0%) | 0.00 | DESCRIPTIVE ONLY | 3/6 | NO EVIDENCE | -2.2 | -0.110 | +0.0000 |
| ar1_63 | Statistical / Time Series | +0.5% | -0.0015 | -0.0000 (-0.0%) | 0.00 | DESCRIPTIVE ONLY | 3/6 | NO EVIDENCE | -1.4 | +0.080 | +0.0000 |
| acf1_252 | Statistical / Time Series | +0.0% | +0.0035 | +0.0000 (+0.0%) | 0.00 | DESCRIPTIVE ONLY | 5/6 | REGIME DEPENDENT | -0.2 | -0.032 | +0.0000 |
| half_life | Statistical / Time Series | +0.0% | +0.0043 | +0.0011 (+0.5%) | 0.27 | SUPPORTED | 3/6 | REGIME DEPENDENT | +1.2 | -0.068 | -0.0000 |
| adf_t | Statistical / Time Series | +0.0% | -0.0005 | -0.0000 (-0.0%) | 0.00 | DESCRIPTIVE ONLY | 4/6 | REGIME DEPENDENT | -1.0 | -0.063 | +0.0000 |
| idio_vol_252 | Statistical / Time Series | -0.2% | -0.0058 | -0.0000 (-0.0%) | 0.00 | DESCRIPTIVE ONLY | 5/6 | REGIME DEPENDENT | -2.3 | +0.038 | +0.0000 |
| drawdown_252 | Statistical / Time Series | +0.2% | -0.0048 | -0.0000 (-0.0%) | 0.00 | SUPPORTED | 3/6 | REGIME DEPENDENT | +0.5 | +0.005 | -0.0000 |
| y10 | Rates | +0.0% | +0.0107 | +0.0000 (+0.0%) | 0.00 | DESCRIPTIVE ONLY | 3/6 | NO EVIDENCE | -0.3 | +0.306 | +0.0000 |
| d_y10_3m | Rates | +0.0% | -0.0032 | -0.0013 (-0.5%) | 0.42 | SUPPORTED | 3/6 | NO EVIDENCE | +1.3 | +0.481 | +0.0000 |
| slope_10y3m | Rates | +0.0% | -0.0074 | -0.0047 (-1.9%) | 0.63 | SUPPORTED | 4/6 | NO EVIDENCE | +1.6 | -0.181 | +0.0000 |
| d_slope_3m | Rates | +0.0% | +0.0067 | +0.0000 (+0.0%) | 0.00 | DESCRIPTIVE ONLY | 4/6 | NO EVIDENCE | -0.6 | +0.205 | +0.0000 |
| real_y10 | Rates | +0.0% | +0.0025 | +0.0000 (+0.0%) | 0.00 | DESCRIPTIVE ONLY | 4/6 | UNSTABLE | -0.0 | +0.770 | +0.0000 |
| breakeven_10y | Rates | +0.0% | -0.0102 | -0.0057 (-2.4%) | 0.56 | SUPPORTED | 4/6 | UNSTABLE | +1.5 | +0.414 | +0.0000 |
| rate_duration | Rates | +2.7% | +0.0029 | +0.0000 (+0.0%) | 0.00 | DESCRIPTIVE ONLY | 2/6 | REGIME DEPENDENT | -1.7 | -0.192 | +0.0000 |
| credit_spread | Credit | +0.0% | +0.0008 | +0.0000 (+0.0%) | 0.00 | SUPPORTED | 3/6 | NO EVIDENCE | +0.8 | -0.552 | +0.0000 |
| d_credit_3m | Credit | +0.0% | -0.0097 | -0.0000 (-0.0%) | 0.00 | DESCRIPTIVE ONLY | 5/6 | NO EVIDENCE | -0.3 | -0.029 | +0.0000 |
| credit_signal | Credit | -0.7% | -0.0067 | -0.0000 (-0.0%) | 0.00 | SUPPORTED | 4/6 | NO EVIDENCE | +0.5 | -0.415 | +0.0000 |
| cpi_yoy | Macro | +0.0% | +0.0046 | +0.0000 (+0.0%) | 0.00 | DESCRIPTIVE ONLY | 3/6 | NO EVIDENCE | -0.8 | +0.248 | +0.0000 |
| unemp_gap | Macro | +0.0% | +0.0053 | +0.0000 (+0.0%) | 0.00 | DESCRIPTIVE ONLY | 5/6 | UNSTABLE | -0.3 | -0.184 | +0.0000 |
| oil_mom_3m | Macro | +0.0% | -0.0038 | -0.0000 (-0.0%) | 0.00 | SUPPORTED | 4/6 | NO EVIDENCE | +0.6 | +0.378 | +0.0000 |
| gold_mom_3m | Macro | +0.9% | -0.0015 | -0.0000 (-0.0%) | 0.00 | DESCRIPTIVE ONLY | 3/6 | NO EVIDENCE | -2.6 | +0.145 | +0.0000 |
| nfci | Liquidity | +0.0% | +0.0017 | +0.0000 (+0.0%) | 0.00 | DESCRIPTIVE ONLY | 4/6 | NO EVIDENCE | -0.1 | +0.152 | +0.0000 |
| fed_bs_growth | Liquidity | +0.0% | -0.0044 | -0.0000 (-0.0%) | 0.00 | DESCRIPTIVE ONLY | 3/6 | UNSTABLE | -1.1 | -0.069 | +0.0000 |
| vix | Liquidity | +0.0% | +0.0176 | +0.0087 (+3.6%) | 0.49 | SUPPORTED | 6/6 | REGIME DEPENDENT | +1.4 | -0.143 | +0.0000 |
| d_vix_1m | Liquidity | -2.6% | +0.0086 | +0.0000 (+0.0%) | 0.00 | SUPPORTED | 5/6 | STABLE | +0.4 | +0.202 | +0.0000 |
| volume_z | Liquidity | -0.8% | +0.0068 | +0.0000 (+0.0%) | 0.00 | SUPPORTED | 6/6 | STABLE | +0.8 | -0.009 | +0.0000 |
| dollar_mom_3m | Cross-Asset | +0.0% | +0.0190 | +0.0074 (+3.1%) | 0.39 | SUPPORTED | 5/6 | STABLE | +1.3 | -0.067 | +0.0000 |
| beta_252 | Cross-Asset | -0.1% | -0.0074 | -0.0000 (-0.0%) | 0.00 | DESCRIPTIVE ONLY | 4/6 | REGIME DEPENDENT | -0.3 | -0.072 | +0.0000 |
| corr_252 | Cross-Asset | -0.0% | +0.0037 | +0.0000 (+0.0%) | 0.00 | DESCRIPTIVE ONLY | 3/6 | REGIME DEPENDENT | -0.6 | -0.228 | +0.0001 |
| rate_beta_252 | Cross-Asset | +0.0% | +0.0000 | +0.0000 (+0.0%) | 0.00 | DESCRIPTIVE ONLY | 3/6 | REGIME DEPENDENT | -2.0 | +0.010 | +0.0000 |
| dollar_beta_252 | Cross-Asset | +0.0% | -0.0033 | -0.0000 (-0.0%) | 0.00 | DESCRIPTIVE ONLY | 3/6 | REGIME DEPENDENT | -0.5 | -0.237 | +0.0000 |
| excess_3m | Relative Value | +2.9% | +0.0075 | +0.0000 (+0.0%) | 0.00 | SUPPORTED | 4/6 | UNSTABLE | +0.8 | -0.051 | -0.0001 |
| rel_strength_6m | Relative Value | +1.3% | -0.0018 | -0.0000 (-0.0%) | 0.00 | SUPPORTED | 3/6 | REGIME DEPENDENT | +0.8 | -0.245 | +0.0000 |
| rel_value | Relative Value | +1.5% | -0.0027 | -0.0000 (-0.0%) | 0.00 | SUPPORTED | 4/6 | NO EVIDENCE | +0.6 | +0.094 | +0.0000 |

### Family weights and their signals (1W, Alpha, validated shares)

| Family | Production share | Learned share | Signals (learned) |
|---|---|---|---|
| Momentum | +0.8% | +18.4% | ret_3m -0.8%, ret_6m +1.6%, ret_12m -0.5%, mom_12_1 +13.1%, ret_1m +5.0% |
| Mean Reversion | -53.8% | -17.8% | ret_1d -7.5%, ret_1w -0.9%, z_50 +1.5%, rsi_14 -4.7%, mr_opportunity -6.2% |
| Trend | +9.2% | -11.2% | ma_cross +2.4%, dist_ma200 -13.6% |
| Volatility | +0.0% | +5.1% | vol_20 -3.0%, ewma_vol +4.1%, vol_pctile +4.0% |
| Rates | +2.7% | -4.8% | d_y10_3m -0.5%, slope_10y3m -1.9%, breakeven_10y -2.4% |
| Liquidity | -3.4% | +3.6% | vix +3.6% |
| Cross-Asset | -0.2% | +3.1% | dollar_mom_3m +3.1% |
| Risk-Adjusted Performance | +3.3% | +3.1% | sharpe_252 +6.0%, sortino_252 -7.1%, ram +4.1% |
| Valuation | +0.1% | -1.5% | earnings_yield -0.2%, book_to_price -1.2% |
| Statistical / Time Series | -1.8% | +0.5% | half_life +0.5% |
| Fundamental Quality | +0.0% | -0.3% | fund_quality -0.3% |
| Fundamental Growth | +0.0% | +0.0% | — |
| Credit | -0.7% | +0.0% | — |
| Macro | +0.9% | +0.0% | — |
| Relative Value | +5.7% | +0.0% | — |

### Confidence, regime, decay and applicability (1W; global, walk-forward)

| Construction | Alpha rank IC (t) | vs free weights (t) | Directional deviance gain (t) | vs free (t) |
|---|---|---|---|---|
| free signed weights on x (learned) | +0.0522 (+7.8) | — (—) | -1.0 (-2.5) | — (—) |
| production structure δ·x·c·r·d | +0.0412 (+6.2) | -0.0110 (-1.8) | +0.3 (+1.8) | +1.2 (+3.6) |
| without confidence (γc = 0) | +0.0409 (+5.9) | -0.0113 (-1.9) | +0.2 (+1.4) | +1.2 (+3.4) |
| without regime (γr = 0) | +0.0422 (+6.4) | -0.0100 (-1.6) | +0.3 (+1.9) | +1.2 (+3.6) |
| without decay (γd = 0) | +0.0423 (+6.4) | -0.0100 (-1.6) | +0.3 (+1.8) | +1.2 (+3.6) |
| δ·x only | +0.0430 (+6.2) | -0.0092 (-1.6) | +0.2 (+1.4) | +1.2 (+3.4) |
| applicability mask (x only where applicable) | +0.0413 (+6.4) | -0.0109 (-1.9) | -0.0 (-0.0) | +1.0 (+2.9) |

Regime-dependent weights (1W, Alpha): rsi_14 (growth: expansion -0.002, recession -0.074, t +2.8); value_5y (rates: rising_rates +0.012, falling_rates -0.009, t +2.0, growth: expansion +0.001, recession -0.001, t -3.0); earnings_yield (rates: rising_rates -0.004, falling_rates +0.002, t -2.1); roe (growth: expansion +0.004, recession -0.025, t +2.0); eps_growth_yoy (growth: expansion +0.001, recession -0.004, t +2.2); rev_growth_yoy (rates: rising_rates +0.004, falling_rates -0.004, t +2.7); sharpe_252 (rates: rising_rates +0.057, falling_rates -0.039, t +2.3); alpha_252 (rates: rising_rates -0.008, falling_rates +0.014, t -2.1, growth: expansion +0.010, recession -0.029, t +2.5).

### Score magnitude (1W) — does a larger |score| mean a stronger outcome?

| |score| | Validated: records | relative return | hit | rank IC | Production: records | relative return | hit |
|---|---|---|---|---|---|---|---|
| 0–10 | 110,558 | +0.057% | +0.4% | +0.029 | 105,009 | +0.031% | +0.2% |
| 10–25 | 10,680 | +0.322% | +4.1% | +0.079 | 13,374 | +0.110% | +0.9% |
| 25–50 | 495 | +0.680% | +8.6% | +0.156 | 2,834 | +0.254% | +1.8% |
| 50–75 | 18 | — | — | — | 448 | +0.305% | +0.0% |
| 75– | 0 | — | — | — | 86 | +0.541% | -2.3% |

Monotonic (every bucket populated and rising): validated no, production yes. Directional magnitude = its calibration: 50%–55% predicted 53.1% → realised 54.0% (n 70,293), 55%–60% predicted 56.7% → realised 55.9% (n 47,131), 60%–65% predicted 61.5% → realised 59.0% (n 3,380), 65%–70% predicted 67.1% → realised 63.7% (n 520), 70%–75% predicted 71.9% → realised 75.0% (n 156), 75%–100% predicted 87.6% → realised 92.6% (n 271).

## 3–6. 1M (148,893 records, 155 assets)

### Production vs the learned weight sets (walk-forward, identical records)

| Weight set | Rank IC (t) | Δ rank IC vs production (t) | Quintile spread | Net L/S (t) | Eras won | Split t | FDR | Status |
|---|---|---|---|---|---|---|---|---|
| A production | +0.0187 (+1.5) | — | +0.22% | -0.046% (-0.2) | — | — | — | production |
| B historical best fit | +0.0155 (+1.3) | -0.0032 (-0.2) | +0.08% | -0.073% (-0.4) | 1/4 | +0.4 | ✗ | NOT VALIDATED |
| C validated deployable | +0.0148 (+1.0) | -0.0039 (-0.2) | +0.30% | -0.163% (-0.7) | 2/4 | +0.5 | ✗ | NOT VALIDATED |
| D simple global | +0.0106 (+0.8) | -0.0081 (-0.5) | +0.24% | -0.119% (-0.6) | 1/4 | -0.9 | ✗ | NOT VALIDATED |
| E uniform-depth hierarchy | +0.0162 (+1.1) | -0.0024 (-0.1) | +0.23% | -0.172% (-0.7) | 2/4 | -0.2 | ✗ | NOT VALIDATED |

Directional (P(up) beyond the PIT base prior; gates: Brier and log loss vs prior-only and vs prior + production, balanced accuracy, eras, split, calibration):

| Weight set | Brier | Brier gain vs prior (t) | vs current (t) | Log-loss gain | Balanced acc. | ECE | Slope | Eras won | Status |
|---|---|---|---|---|---|---|---|---|---|
| prior-only | +0.2414 | — | — | — | 51.5% | +0.0144 | +0.92 | — | benchmark |
| B historical best fit | +0.2421 | -0.00064 (-1.5) | -1.4 | -0.00133 | 51.1% | +0.0198 | +0.76 | 1/4 | NOT VALIDATED |
| C validated deployable | +0.2422 | -0.00081 (-1.8) | -1.7 | -0.00165 | 51.1% | +0.0259 | +0.75 | 0/4 | NOT VALIDATED |
| D simple global | +0.2420 | -0.00062 (-1.7) | -1.6 | -0.00127 | 51.1% | +0.0257 | +0.76 | 1/4 | NOT VALIDATED |
| E uniform-depth hierarchy | +0.2422 | -0.00076 (-1.9) | -1.8 | -0.00155 | 51.1% | +0.0225 | +0.75 | 1/4 | NOT VALIDATED |

### Optimisers (global level, walk-forward, every choice nested)

| Optimiser | Alpha rank IC (t) | vs ridge (t) | Directional deviance gain (t) | vs ridge (t) |
|---|---|---|---|---|
| ridge | +0.0106 (+0.9) | — (—) | -3.3 (-6.6) | — (—) |
| ridge (pointwise) | +0.0137 (+1.1) | +0.0031 (+1.3) | — (—) | — (—) |
| elastic net | +0.0135 (+1.1) | +0.0028 (+0.9) | -0.2 (-1.0) | +3.0 (+7.7) |
| sign-constrained | +0.0155 (+1.2) | +0.0049 (+0.5) | -2.9 (-7.2) | +0.4 (+1.9) |
| pairwise logistic (RankNet) | +0.0135 (+1.1) | +0.0028 (+0.3) | — (—) | — (—) |
| ridge + residual boosting | +0.0047 (+0.4) | -0.0059 (-1.4) | -3.8 (-6.9) | -0.5 (-3.2) |

Residual boosting's most used interactions (Alpha; a nonlinear diagnostic, never production): corr_252 × ram ×6, kurt_60 × skew_60 ×6, roe × sortino_252 ×6, dist_ma200 × ret_12m ×5, dollar_beta_252 × y10 ×5, dist_ma200 × ret_6m ×5.

### Where specialisation stops helping (uniform depth, walk-forward mean rank IC, t)

Every cell is a walk-forward result (each era scored by weights learned before it), but K and depth are held fixed across eras here: choosing the best cell uses hindsight. The nested choice is weight set E above.

| K (pooling) | Global | Class | Product type | Sector | Industry | Asset |
|---|---|---|---|---|---|---|
| 10 | +0.0106 (+0.9) | +0.0164 (+1.2) | +0.0281 (+2.0) | +0.0169 (+1.3) | +0.0170 (+1.3) | +0.0157 (+1.3) |
| 50 | +0.0106 (+0.9) | +0.0180 (+1.5) | +0.0286 (+2.2) | +0.0186 (+1.4) | +0.0173 (+1.4) | +0.0169 (+1.4) |
| 200 | +0.0106 (+0.9) | +0.0193 (+1.6) | +0.0288 (+2.4) | +0.0211 (+1.8) | +0.0197 (+1.7) | +0.0207 (+1.8) |
| 1000 | +0.0106 (+0.9) | +0.0193 (+1.6) | +0.0271 (+2.3) | +0.0235 (+2.0) | +0.0223 (+2.0) | +0.0240 (+2.2) |
| 5000 | +0.0106 (+0.9) | +0.0166 (+1.3) | +0.0216 (+1.8) | +0.0200 (+1.6) | +0.0192 (+1.6) | +0.0219 (+1.9) |
| 20000 | +0.0106 (+0.9) | +0.0136 (+1.1) | +0.0169 (+1.3) | +0.0160 (+1.3) | +0.0157 (+1.3) | +0.0172 (+1.4) |

Validated model today: K = 5000, 17 of 74 global weights trusted (ρ > 0), 12 node × signal specialisations kept (Class 2, Product type 3, Sector 2, Industry 2, Asset 3).

### The most important table — Global node, 1M (Alpha)

Weights are on standardised signals; *shares* are signed shares of total |weight| so production and learned are on one scale. Reliability: VALIDATED (OOS t ≥ 2, BH across signals × horizons, sign stable in ≥ 4 eras) · SUPPORTED (positive OOS contribution) · DESCRIPTIVE ONLY. Current value: mean of today's signal across assets; current contribution: mean of today's validated weight × signal.

| Signal | Family | Production (share) | Best fit | Validated (share) | Trust ρ | Reliability | Era sign stability | Era class | OOS contribution t | Current value | Current contribution |
|---|---|---|---|---|---|---|---|---|---|---|---|
| ret_3m | Momentum | +5.4% | -0.0178 | -0.0000 (-0.0%) | 0.00 | DESCRIPTIVE ONLY | 3/6 | REGIME DEPENDENT | -0.2 | +0.008 | +0.0000 |
| ret_6m | Momentum | +2.9% | -0.0236 | -0.0131 (-9.3%) | 0.55 | SUPPORTED | 4/6 | NO EVIDENCE | +1.5 | -0.001 | +0.0000 |
| ret_12m | Momentum | +2.0% | +0.0107 | +0.0000 (+0.0%) | 0.00 | SUPPORTED | 4/6 | NO EVIDENCE | +0.4 | +0.127 | +0.0000 |
| mom_12_1 | Momentum | +3.2% | +0.0058 | +0.0040 (+2.8%) | 0.68 | SUPPORTED | 3/6 | REGIME DEPENDENT | +1.8 | +0.095 | +0.0000 |
| ret_1m | Momentum | +1.2% | -0.0003 | -0.0000 (-0.0%) | 0.00 | DESCRIPTIVE ONLY | 2/6 | NO EVIDENCE | -0.0 | +0.107 | +0.0000 |
| ma_cross | Trend | +2.0% | +0.0282 | +0.0000 (+0.0%) | 0.00 | DESCRIPTIVE ONLY | 3/6 | NO EVIDENCE | -0.6 | +0.017 | +0.0000 |
| dist_ma200 | Trend | +2.2% | -0.0172 | -0.0117 (-8.3%) | 0.68 | SUPPORTED | 4/6 | REGIME DEPENDENT | +1.8 | +0.055 | -0.0000 |
| macd | Trend | +7.0% | +0.0044 | +0.0000 (+0.0%) | 0.00 | DESCRIPTIVE ONLY | 3/6 | REGIME DEPENDENT | -0.2 | +0.107 | +0.0000 |
| pctile_252 | Trend | +6.4% | +0.0032 | +0.0000 (+0.0%) | 0.00 | DESCRIPTIVE ONLY | 4/6 | NO EVIDENCE | -1.9 | +0.022 | +0.0000 |
| trend_quality | Trend | +3.5% | -0.0052 | -0.0000 (-0.0%) | 0.00 | SUPPORTED | 4/6 | UNSTABLE | +0.9 | -0.082 | +0.0000 |
| ret_1d | Mean Reversion | -4.3% | -0.0103 | -0.0000 (-0.0%) | 0.00 | SUPPORTED | 6/6 | STABLE | +0.7 | -0.077 | +0.0000 |
| ret_1w | Mean Reversion | -2.1% | +0.0000 | +0.0000 (+0.0%) | 0.00 | DESCRIPTIVE ONLY | 2/6 | REGIME DEPENDENT | -0.4 | -0.023 | +0.0000 |
| z_20 | Mean Reversion | -3.2% | -0.0243 | -0.0165 (-11.7%) | 0.68 | SUPPORTED | 6/6 | STABLE | +1.8 | +0.042 | -0.0000 |
| z_50 | Mean Reversion | -4.0% | +0.0094 | +0.0079 (+5.6%) | 0.84 | SUPPORTED | 3/6 | NO EVIDENCE | +2.5 | +0.039 | +0.0000 |
| rsi_14 | Mean Reversion | -0.8% | +0.0129 | +0.0000 (+0.0%) | 0.00 | DESCRIPTIVE ONLY | 4/6 | REGIME DEPENDENT | -0.7 | +0.026 | +0.0000 |
| bb_pctb | Mean Reversion | -3.2% | -0.0243 | -0.0165 (-11.7%) | 0.68 | SUPPORTED | 6/6 | STABLE | +1.8 | +0.042 | -0.0000 |
| mr_opportunity | Mean Reversion | +2.6% | -0.0306 | -0.0000 (-0.0%) | 0.00 | SUPPORTED | 6/6 | STABLE | +0.8 | -0.046 | +0.0000 |
| value_5y | Valuation | +0.2% | +0.0008 | +0.0000 (+0.0%) | 0.00 | DESCRIPTIVE ONLY | 2/6 | NO EVIDENCE | -0.5 | -0.168 | +0.0000 |
| earnings_yield | Valuation | +0.1% | -0.0021 | -0.0000 (-0.0%) | 0.00 | DESCRIPTIVE ONLY | 3/6 | NO EVIDENCE | -5.1 | -0.043 | +0.0000 |
| pe_rel_5y | Valuation | -0.2% | +0.0012 | +0.0000 (+0.0%) | 0.00 | DESCRIPTIVE ONLY | 4/6 | REGIME DEPENDENT | -7.3 | -0.037 | +0.0000 |
| book_to_price | Valuation | +0.1% | -0.0093 | -0.0075 (-5.3%) | 0.81 | SUPPORTED | 5/6 | STABLE | +2.3 | -0.078 | -0.0000 |
| sales_yield | Valuation | +0.1% | -0.0044 | -0.0041 (-2.9%) | 0.94 | SUPPORTED | 3/6 | UNSTABLE | +4.0 | -0.124 | -0.0000 |
| net_margin | Fundamental Quality | +0.0% | -0.0015 | -0.0000 (-0.0%) | 0.00 | DESCRIPTIVE ONLY | 2/6 | NO EVIDENCE | -1.3 | +0.075 | +0.0000 |
| roe | Fundamental Quality | +0.0% | +0.0130 | +0.0126 (+8.9%) | 0.96 | VALIDATED | 4/6 | REGIME DEPENDENT | +5.4 | +0.043 | -0.0000 |
| fund_quality | Fundamental Quality | +0.0% | -0.0137 | -0.0000 (-0.0%) | 0.00 | DESCRIPTIVE ONLY | 4/6 | UNSTABLE | -0.6 | +0.043 | +0.0000 |
| eps_growth_yoy | Fundamental Growth | +0.1% | +0.0015 | +0.0014 (+1.0%) | 0.98 | VALIDATED | 4/6 | REGIME DEPENDENT | +6.6 | -0.005 | +0.0000 |
| rev_growth_yoy | Fundamental Growth | +0.0% | +0.0027 | +0.0000 (+0.0%) | 0.00 | DESCRIPTIVE ONLY | 4/6 | REGIME DEPENDENT | -4.8 | +0.037 | +0.0000 |
| sharpe_252 | Risk-Adjusted Performance | +1.5% | +0.0482 | +0.0000 (+0.0%) | 0.00 | SUPPORTED | 2/6 | NO EVIDENCE | +0.7 | -0.004 | +0.0000 |
| sortino_252 | Risk-Adjusted Performance | +1.5% | -0.0433 | -0.0315 (-22.4%) | 0.73 | SUPPORTED | 4/6 | UNSTABLE | +1.9 | -0.009 | +0.0000 |
| alpha_252 | Risk-Adjusted Performance | +2.0% | -0.0068 | -0.0000 (-0.0%) | 0.00 | DESCRIPTIVE ONLY | 3/6 | REGIME DEPENDENT | -0.7 | +0.065 | +0.0000 |
| ram | Risk-Adjusted Performance | +5.1% | +0.0010 | +0.0002 (+0.1%) | 0.21 | SUPPORTED | 4/6 | REGIME DEPENDENT | +1.1 | +0.022 | +0.0000 |
| vol_20 | Volatility | +0.0% | +0.0210 | +0.0000 (+0.0%) | 0.00 | SUPPORTED | 5/6 | UNSTABLE | +0.3 | -0.010 | +0.0000 |
| vol_60 | Volatility | +0.0% | +0.0014 | +0.0012 (+0.9%) | 0.88 | SUPPORTED | 3/6 | REGIME DEPENDENT | +2.9 | +0.016 | +0.0000 |
| downside_vol_60 | Volatility | +0.0% | -0.0078 | -0.0000 (-0.0%) | 0.00 | DESCRIPTIVE ONLY | 4/6 | NO EVIDENCE | -0.4 | +0.008 | +0.0000 |
| ewma_vol | Volatility | +0.0% | -0.0138 | -0.0113 (-8.0%) | 0.82 | SUPPORTED | 4/6 | UNSTABLE | +2.3 | -0.033 | +0.0000 |
| garch_vol | Volatility | +0.0% | +0.0000 | +0.0000 (+0.0%) | 0.00 | DESCRIPTIVE ONLY | 5/6 | UNSTABLE | -1.4 | -0.092 | +0.0000 |
| vol_ratio | Volatility | -2.5% | -0.0223 | -0.0000 (-0.0%) | 0.00 | DESCRIPTIVE ONLY | 5/6 | REGIME DEPENDENT | -0.7 | +0.059 | +0.0000 |
| vol_of_vol | Volatility | +0.0% | +0.0013 | +0.0000 (+0.0%) | 0.00 | SUPPORTED | 3/6 | NO EVIDENCE | +0.1 | -0.078 | +0.0000 |
| vol_pctile | Volatility | -0.5% | +0.0234 | +0.0000 (+0.0%) | 0.00 | SUPPORTED | 4/6 | REGIME DEPENDENT | +0.8 | +0.100 | +0.0000 |
| skew_60 | Statistical / Time Series | -3.0% | -0.0010 | -0.0000 (-0.0%) | 0.00 | SUPPORTED | 3/6 | REGIME DEPENDENT | +0.4 | +0.058 | +0.0000 |
| kurt_60 | Statistical / Time Series | +0.0% | +0.0044 | +0.0000 (+0.0%) | 0.00 | DESCRIPTIVE ONLY | 5/6 | UNSTABLE | -1.8 | -0.093 | +0.0000 |
| ar1_63 | Statistical / Time Series | +0.9% | -0.0057 | -0.0000 (-0.0%) | 0.00 | DESCRIPTIVE ONLY | 3/6 | NO EVIDENCE | -0.5 | -0.021 | +0.0000 |
| acf1_252 | Statistical / Time Series | +0.0% | +0.0054 | +0.0000 (+0.0%) | 0.00 | DESCRIPTIVE ONLY | 4/6 | REGIME DEPENDENT | -0.6 | -0.074 | +0.0000 |
| half_life | Statistical / Time Series | +0.1% | +0.0057 | +0.0000 (+0.0%) | 0.00 | DESCRIPTIVE ONLY | 3/6 | NO EVIDENCE | -0.3 | -0.063 | +0.0000 |
| adf_t | Statistical / Time Series | +0.0% | +0.0047 | +0.0000 (+0.0%) | 0.00 | DESCRIPTIVE ONLY | 3/6 | REGIME DEPENDENT | -0.2 | -0.082 | +0.0000 |
| idio_vol_252 | Statistical / Time Series | -0.4% | -0.0096 | -0.0000 (-0.0%) | 0.00 | DESCRIPTIVE ONLY | 4/6 | REGIME DEPENDENT | -1.0 | +0.032 | +0.0000 |
| drawdown_252 | Statistical / Time Series | +0.2% | +0.0004 | +0.0000 (+0.0%) | 0.00 | SUPPORTED | 2/6 | REGIME DEPENDENT | +0.2 | +0.062 | +0.0000 |
| y10 | Rates | +0.0% | +0.0103 | +0.0000 (+0.0%) | 0.00 | DESCRIPTIVE ONLY | 4/6 | REGIME DEPENDENT | -0.3 | +0.225 | +0.0000 |
| d_y10_3m | Rates | +0.0% | -0.0057 | -0.0000 (-0.0%) | 0.00 | SUPPORTED | 3/6 | NO EVIDENCE | +0.8 | +0.065 | +0.0000 |
| slope_10y3m | Rates | +0.0% | -0.0003 | -0.0001 (-0.1%) | 0.28 | SUPPORTED | 4/6 | NO EVIDENCE | +1.2 | -0.223 | +0.0000 |
| d_slope_3m | Rates | +0.0% | +0.0092 | +0.0000 (+0.0%) | 0.00 | SUPPORTED | 3/6 | NO EVIDENCE | +0.4 | -0.127 | +0.0000 |
| real_y10 | Rates | +0.0% | +0.0027 | +0.0008 (+0.6%) | 0.29 | SUPPORTED | 2/6 | NO EVIDENCE | +1.2 | +0.679 | +0.0000 |
| breakeven_10y | Rates | +0.0% | -0.0047 | -0.0000 (-0.0%) | 0.00 | DESCRIPTIVE ONLY | 4/6 | UNSTABLE | -0.5 | +0.287 | +0.0000 |
| rate_duration | Rates | +5.7% | +0.0075 | +0.0000 (+0.0%) | 0.00 | DESCRIPTIVE ONLY | 1/6 | NO EVIDENCE | -0.9 | -0.024 | +0.0000 |
| credit_spread | Credit | +0.0% | +0.0049 | +0.0000 (+0.0%) | 0.00 | SUPPORTED | 4/6 | REGIME DEPENDENT | +0.1 | -0.456 | +0.0000 |
| d_credit_3m | Credit | +0.0% | +0.0064 | +0.0000 (+0.0%) | 0.00 | SUPPORTED | 4/6 | UNSTABLE | +0.6 | +0.089 | +0.0000 |
| credit_signal | Credit | -1.2% | +0.0053 | +0.0000 (+0.0%) | 0.00 | SUPPORTED | 4/6 | NO EVIDENCE | +0.9 | -0.440 | +0.0000 |
| cpi_yoy | Macro | +0.0% | +0.0027 | +0.0000 (+0.0%) | 0.00 | SUPPORTED | 3/6 | NO EVIDENCE | +0.1 | +0.244 | +0.0000 |
| unemp_gap | Macro | +0.0% | +0.0009 | +0.0000 (+0.0%) | 0.00 | DESCRIPTIVE ONLY | 4/6 | NO EVIDENCE | -0.7 | -0.155 | +0.0000 |
| oil_mom_3m | Macro | +0.0% | +0.0032 | +0.0000 (+0.0%) | 0.00 | DESCRIPTIVE ONLY | 3/6 | NO EVIDENCE | -0.8 | -0.349 | +0.0000 |
| gold_mom_3m | Macro | +2.3% | +0.0043 | +0.0000 (+0.0%) | 0.00 | DESCRIPTIVE ONLY | 5/6 | UNSTABLE | -0.2 | -0.112 | +0.0000 |
| nfci | Liquidity | +0.0% | -0.0015 | -0.0000 (-0.0%) | 0.00 | DESCRIPTIVE ONLY | 3/6 | NO EVIDENCE | -0.9 | +0.165 | +0.0000 |
| fed_bs_growth | Liquidity | +0.0% | -0.0036 | -0.0000 (-0.0%) | 0.00 | DESCRIPTIVE ONLY | 2/6 | NO EVIDENCE | -0.8 | -0.043 | +0.0000 |
| vix | Liquidity | +0.0% | +0.0164 | +0.0000 (+0.0%) | 0.00 | SUPPORTED | 4/6 | REGIME DEPENDENT | +0.5 | -0.269 | +0.0000 |
| d_vix_1m | Liquidity | -2.3% | -0.0029 | -0.0000 (-0.0%) | 0.00 | SUPPORTED | 4/6 | REGIME DEPENDENT | +0.2 | -0.205 | +0.0000 |
| volume_z | Liquidity | +0.2% | +0.0062 | +0.0000 (+0.0%) | 0.00 | SUPPORTED | 4/6 | UNSTABLE | +0.1 | -0.190 | +0.0000 |
| dollar_mom_3m | Cross-Asset | +0.0% | +0.0120 | +0.0000 (+0.0%) | 0.00 | SUPPORTED | 5/6 | STABLE | +0.4 | -0.031 | +0.0000 |
| beta_252 | Cross-Asset | +0.0% | -0.0164 | -0.0000 (-0.0%) | 0.00 | SUPPORTED | 4/6 | REGIME DEPENDENT | +0.4 | -0.066 | +0.0000 |
| corr_252 | Cross-Asset | +0.0% | +0.0111 | +0.0000 (+0.0%) | 0.00 | DESCRIPTIVE ONLY | 4/6 | REGIME DEPENDENT | -0.9 | -0.224 | +0.0000 |
| rate_beta_252 | Cross-Asset | +0.0% | +0.0039 | +0.0000 (+0.0%) | 0.00 | DESCRIPTIVE ONLY | 3/6 | REGIME DEPENDENT | -1.1 | +0.014 | +0.0000 |
| dollar_beta_252 | Cross-Asset | +0.0% | -0.0075 | -0.0000 (-0.0%) | 0.00 | DESCRIPTIVE ONLY | 3/6 | NO EVIDENCE | -0.5 | -0.251 | +0.0000 |
| excess_3m | Relative Value | +6.4% | -0.0001 | -0.0000 (-0.0%) | 0.00 | DESCRIPTIVE ONLY | 2/6 | NO EVIDENCE | -1.0 | -0.019 | +0.0000 |
| rel_strength_6m | Relative Value | +4.0% | -0.0004 | -0.0003 (-0.2%) | 0.78 | SUPPORTED | 3/6 | REGIME DEPENDENT | +2.1 | -0.211 | -0.0000 |
| rel_value | Relative Value | +3.5% | +0.0004 | +0.0000 (+0.0%) | 0.00 | DESCRIPTIVE ONLY | 4/6 | NO EVIDENCE | -0.3 | +0.091 | +0.0000 |

### Family weights and their signals (1M, Alpha, validated shares)

| Family | Production share | Learned share | Signals (learned) |
|---|---|---|---|
| Risk-Adjusted Performance | +10.0% | -22.3% | sortino_252 -22.4%, ram +0.1% |
| Mean Reversion | -15.0% | -17.8% | z_20 -11.7%, z_50 +5.6%, bb_pctb -11.7% |
| Fundamental Quality | +0.1% | +8.9% | roe +8.9% |
| Trend | +21.1% | -8.3% | dist_ma200 -8.3% |
| Valuation | +0.3% | -8.3% | book_to_price -5.3%, sales_yield -2.9% |
| Volatility | -3.0% | -7.2% | vol_60 +0.9%, ewma_vol -8.0% |
| Momentum | +14.7% | -6.5% | ret_6m -9.3%, mom_12_1 +2.8% |
| Fundamental Growth | +0.2% | +1.0% | eps_growth_yoy +1.0% |
| Rates | +5.7% | +0.5% | slope_10y3m -0.1%, real_y10 +0.6% |
| Relative Value | +14.0% | -0.2% | rel_strength_6m -0.2% |
| Statistical / Time Series | -2.2% | +0.0% | — |
| Credit | -1.2% | +0.0% | — |
| Macro | +2.3% | +0.0% | — |
| Liquidity | -2.2% | +0.0% | — |
| Cross-Asset | +0.0% | +0.0% | — |

### Confidence, regime, decay and applicability (1M; global, walk-forward)

| Construction | Alpha rank IC (t) | vs free weights (t) | Directional deviance gain (t) | vs free (t) |
|---|---|---|---|---|
| free signed weights on x (learned) | +0.0106 (+0.9) | — (—) | -3.3 (-6.6) | — (—) |
| production structure δ·x·c·r·d | +0.0190 (+1.6) | +0.0083 (+0.6) | +0.1 (+0.9) | +3.4 (+7.3) |
| without confidence (γc = 0) | +0.0157 (+1.3) | +0.0051 (+0.4) | +0.1 (+0.8) | +3.4 (+7.3) |
| without regime (γr = 0) | +0.0191 (+1.6) | +0.0085 (+0.6) | +0.1 (+0.9) | +3.4 (+7.3) |
| without decay (γd = 0) | +0.0210 (+1.8) | +0.0103 (+0.8) | +0.1 (+0.9) | +3.4 (+7.3) |
| δ·x only | +0.0187 (+1.5) | +0.0080 (+0.6) | +0.1 (+0.8) | +3.4 (+7.3) |
| applicability mask (x only where applicable) | +0.0136 (+1.2) | +0.0030 (+0.3) | -0.4 (-2.2) | +2.9 (+6.9) |

Regime-dependent weights (1M, Alpha): ret_3m (volatility: high_vol -0.044, low_vol +0.024, t -2.1); mom_12_1 (growth: expansion -0.020, recession +0.052, t -5.5); dist_ma200 (growth: expansion -0.038, recession +0.021, t -2.2); macd (volatility: high_vol +0.011, low_vol -0.012, t +3.8); ret_1w (market: bull -0.000, bear -0.005, t +3.5); rsi_14 (growth: expansion +0.030, recession -0.063, t +2.7); pe_rel_5y (rates: rising_rates -0.006, falling_rates +0.011, t -2.8); roe (growth: expansion +0.017, recession -0.041, t +2.8).

### Score magnitude (1M) — does a larger |score| mean a stronger outcome?

| |score| | Validated: records | relative return | hit | rank IC | Production: records | relative return | hit |
|---|---|---|---|---|---|---|---|
| 0–10 | 113,825 | +0.027% | +0.4% | +0.012 | 112,637 | +0.097% | +0.3% |
| 10–25 | 7,471 | +0.661% | +2.9% | +0.037 | 7,027 | +0.197% | +0.1% |
| 25–50 | 380 | +2.087% | +12.4% | +0.117 | 1,448 | +0.011% | +1.1% |
| 50–75 | 3 | — | — | — | 416 | +0.165% | -2.6% |
| 75– | 0 | — | — | — | 151 | -0.128% | -7.0% |

Monotonic (every bucket populated and rising): validated no, production no. Directional magnitude = its calibration: 50%–55% predicted 52.9% → realised 59.5% (n 22,142), 55%–60% predicted 57.5% → realised 55.4% (n 53,129), 60%–65% predicted 62.1% → realised 60.8% (n 36,568), 65%–70% predicted 66.8% → realised 63.1% (n 7,167), 70%–75% predicted 72.0% → realised 68.3% (n 1,597), 75%–100% predicted 85.1% → realised 83.1% (n 1,076).

## 3–6. 3M (143,663 records, 155 assets)

### Production vs the learned weight sets (walk-forward, identical records)

| Weight set | Rank IC (t) | Δ rank IC vs production (t) | Quintile spread | Net L/S (t) | Eras won | Split t | FDR | Status |
|---|---|---|---|---|---|---|---|---|
| A production | -0.0084 (-0.4) | — | -0.32% | -0.559% (-0.8) | — | — | — | production |
| B historical best fit | -0.0083 (-0.4) | +0.0001 (+0.0) | -0.44% | -0.204% (-0.3) | 1/4 | +0.1 | ✗ | NOT VALIDATED |
| C validated deployable | -0.0065 (-0.2) | +0.0019 (+0.1) | -0.22% | -0.769% (-0.8) | 2/4 | +0.2 | ✗ | NOT VALIDATED |
| D simple global | -0.0185 (-0.8) | -0.0101 (-0.3) | -0.80% | -0.940% (-1.0) | 2/4 | -0.9 | ✗ | NOT VALIDATED |
| E uniform-depth hierarchy | -0.0127 (-0.5) | -0.0043 (-0.1) | -0.49% | -0.565% (-0.7) | 1/4 | -0.3 | ✗ | NOT VALIDATED |

Directional (P(up) beyond the PIT base prior; gates: Brier and log loss vs prior-only and vs prior + production, balanced accuracy, eras, split, calibration):

| Weight set | Brier | Brier gain vs prior (t) | vs current (t) | Log-loss gain | Balanced acc. | ECE | Slope | Eras won | Status |
|---|---|---|---|---|---|---|---|---|---|
| prior-only | +0.2327 | — | — | — | 51.7% | +0.0360 | +0.78 | — | benchmark |
| B historical best fit | +0.2365 | -0.00381 (-3.6) | -3.7 | -0.00822 | 51.0% | +0.0513 | +0.32 | 0/4 | NOT VALIDATED |
| C validated deployable | +0.2361 | -0.00343 (-2.8) | -2.9 | -0.00749 | 51.0% | +0.0478 | +0.36 | 1/4 | NOT VALIDATED |
| D simple global | +0.2361 | -0.00348 (-3.0) | -3.1 | -0.00758 | 51.0% | +0.0464 | +0.35 | 1/4 | NOT VALIDATED |
| E uniform-depth hierarchy | +0.2362 | -0.00358 (-2.8) | -2.9 | -0.00793 | 51.0% | +0.0476 | +0.35 | 1/4 | NOT VALIDATED |

### Optimisers (global level, walk-forward, every choice nested)

| Optimiser | Alpha rank IC (t) | vs ridge (t) | Directional deviance gain (t) | vs ridge (t) |
|---|---|---|---|---|
| ridge | -0.0183 (-0.8) | — (—) | -0.3 (-1.4) | — (—) |
| ridge (pointwise) | -0.0136 (-0.6) | +0.0048 (+1.0) | — (—) | — (—) |
| elastic net | -0.0193 (-0.8) | -0.0009 (-0.6) | -0.2 (-1.0) | +0.1 (+1.4) |
| sign-constrained | -0.0211 (-1.0) | -0.0028 (-0.1) | -0.5 (-2.6) | -0.2 (-1.0) |
| pairwise logistic (RankNet) | -0.0046 (-0.2) | +0.0138 (+0.9) | — (—) | — (—) |
| ridge + residual boosting | -0.0147 (-0.7) | +0.0036 (+0.5) | -0.5 (-1.9) | -0.2 (-2.0) |

Residual boosting's most used interactions (Alpha; a nonlinear diagnostic, never production): rel_strength_6m × y10 ×13, dollar_beta_252 × rel_strength_6m ×9, dollar_beta_252 × roe ×9, dollar_beta_252 × rsi_14 ×7, dollar_beta_252 × pctile_252 ×6, macd × y10 ×6.

### Where specialisation stops helping (uniform depth, walk-forward mean rank IC, t)

Every cell is a walk-forward result (each era scored by weights learned before it), but K and depth are held fixed across eras here: choosing the best cell uses hindsight. The nested choice is weight set E above.

| K (pooling) | Global | Class | Product type | Sector | Industry | Asset |
|---|---|---|---|---|---|---|
| 10 | -0.0184 (-0.8) | -0.0192 (-0.8) | -0.0158 (-0.6) | -0.0129 (-0.5) | -0.0114 (-0.5) | -0.0085 (-0.4) |
| 50 | -0.0184 (-0.8) | -0.0183 (-0.8) | -0.0157 (-0.7) | -0.0131 (-0.6) | -0.0121 (-0.5) | -0.0090 (-0.4) |
| 200 | -0.0184 (-0.8) | -0.0148 (-0.6) | -0.0127 (-0.6) | -0.0119 (-0.5) | -0.0108 (-0.5) | -0.0075 (-0.4) |
| 1000 | -0.0184 (-0.8) | -0.0125 (-0.5) | -0.0121 (-0.5) | -0.0142 (-0.6) | -0.0132 (-0.6) | -0.0097 (-0.5) |
| 5000 | -0.0184 (-0.8) | -0.0142 (-0.6) | -0.0146 (-0.6) | -0.0155 (-0.7) | -0.0155 (-0.7) | -0.0135 (-0.6) |
| 20000 | -0.0184 (-0.8) | -0.0164 (-0.7) | -0.0167 (-0.7) | -0.0170 (-0.8) | -0.0169 (-0.7) | -0.0163 (-0.7) |

Validated model today: K = 50, 8 of 74 global weights trusted (ρ > 0), 0 node × signal specialisations kept (Class 0, Product type 0, Sector 0, Industry 0, Asset 0).

### The most important table — Global node, 3M (Alpha)

Weights are on standardised signals; *shares* are signed shares of total |weight| so production and learned are on one scale. Reliability: VALIDATED (OOS t ≥ 2, BH across signals × horizons, sign stable in ≥ 4 eras) · SUPPORTED (positive OOS contribution) · DESCRIPTIVE ONLY. Current value: mean of today's signal across assets; current contribution: mean of today's validated weight × signal.

| Signal | Family | Production (share) | Best fit | Validated (share) | Trust ρ | Reliability | Era sign stability | Era class | OOS contribution t | Current value | Current contribution |
|---|---|---|---|---|---|---|---|---|---|---|---|
| ret_3m | Momentum | +7.2% | -0.0223 | -0.0000 (-0.0%) | 0.00 | SUPPORTED | 4/6 | UNSTABLE | +0.4 | +0.115 | +0.0000 |
| ret_6m | Momentum | +5.3% | +0.0049 | +0.0023 (+4.8%) | 0.47 | SUPPORTED | 3/6 | NO EVIDENCE | +1.4 | +0.046 | -0.0000 |
| ret_12m | Momentum | +2.2% | -0.0265 | -0.0000 (-0.0%) | 0.00 | DESCRIPTIVE ONLY | 3/6 | NO EVIDENCE | -0.8 | +0.134 | +0.0000 |
| mom_12_1 | Momentum | +2.9% | +0.0277 | +0.0096 (+20.2%) | 0.35 | SUPPORTED | 4/6 | REGIME DEPENDENT | +1.2 | +0.170 | -0.0000 |
| ret_1m | Momentum | +1.3% | +0.0028 | +0.0000 (+0.0%) | 0.00 | SUPPORTED | 1/6 | NO EVIDENCE | +0.0 | -0.086 | +0.0000 |
| ma_cross | Trend | +2.6% | +0.0274 | +0.0059 (+12.3%) | 0.21 | SUPPORTED | 3/6 | NO EVIDENCE | +1.1 | +0.076 | -0.0000 |
| dist_ma200 | Trend | +3.2% | -0.0108 | -0.0000 (-0.0%) | 0.00 | DESCRIPTIVE ONLY | 3/6 | NO EVIDENCE | -0.9 | +0.020 | +0.0000 |
| macd | Trend | +4.8% | -0.0054 | -0.0000 (-0.0%) | 0.00 | SUPPORTED | 3/6 | REGIME DEPENDENT | +0.3 | -0.048 | +0.0000 |
| pctile_252 | Trend | +6.7% | +0.0092 | +0.0000 (+0.0%) | 0.00 | DESCRIPTIVE ONLY | 2/6 | REGIME DEPENDENT | -0.2 | -0.001 | +0.0000 |
| trend_quality | Trend | +5.4% | -0.0007 | -0.0000 (-0.0%) | 0.00 | DESCRIPTIVE ONLY | 5/6 | STABLE | -0.6 | -0.087 | +0.0000 |
| ret_1d | Mean Reversion | -1.5% | -0.0101 | -0.0000 (-0.0%) | 0.00 | SUPPORTED | 5/6 | STABLE | +0.2 | -0.042 | +0.0000 |
| ret_1w | Mean Reversion | -1.8% | -0.0029 | -0.0000 (-0.0%) | 0.00 | SUPPORTED | 5/6 | STABLE | +0.0 | -0.099 | +0.0000 |
| z_20 | Mean Reversion | -1.3% | -0.0139 | -0.0000 (-0.0%) | 0.00 | SUPPORTED | 6/6 | STABLE | +0.3 | -0.096 | +0.0000 |
| z_50 | Mean Reversion | -0.9% | -0.0037 | -0.0000 (-0.0%) | 0.00 | SUPPORTED | 4/6 | NO EVIDENCE | +0.3 | -0.091 | +0.0000 |
| rsi_14 | Mean Reversion | -0.7% | +0.0300 | +0.0000 (+0.0%) | 0.00 | SUPPORTED | 4/6 | REGIME DEPENDENT | +0.4 | -0.120 | +0.0000 |
| bb_pctb | Mean Reversion | -1.3% | -0.0139 | -0.0000 (-0.0%) | 0.00 | SUPPORTED | 6/6 | STABLE | +0.3 | -0.096 | +0.0000 |
| mr_opportunity | Mean Reversion | +1.8% | -0.0147 | -0.0000 (-0.0%) | 0.00 | DESCRIPTIVE ONLY | 5/6 | UNSTABLE | -0.0 | +0.053 | +0.0000 |
| value_5y | Valuation | +0.7% | +0.0030 | +0.0000 (+0.0%) | 0.00 | DESCRIPTIVE ONLY | 3/6 | REGIME DEPENDENT | -0.5 | -0.173 | +0.0000 |
| earnings_yield | Valuation | +0.1% | -0.0112 | -0.0096 (-20.2%) | 0.86 | SUPPORTED | 3/6 | REGIME DEPENDENT | +2.7 | -0.054 | -0.0000 |
| pe_rel_5y | Valuation | -0.4% | +0.0028 | +0.0000 (+0.0%) | 0.00 | DESCRIPTIVE ONLY | 4/6 | NO EVIDENCE | -1.1 | -0.012 | +0.0000 |
| book_to_price | Valuation | +0.1% | -0.0110 | -0.0000 (-0.0%) | 0.00 | DESCRIPTIVE ONLY | 4/6 | UNSTABLE | -0.2 | -0.078 | +0.0000 |
| sales_yield | Valuation | +0.2% | -0.0068 | -0.0000 (-0.0%) | 0.00 | DESCRIPTIVE ONLY | 3/6 | REGIME DEPENDENT | -1.2 | -0.123 | +0.0000 |
| net_margin | Fundamental Quality | +0.1% | -0.0005 | -0.0000 (-0.0%) | 0.00 | DESCRIPTIVE ONLY | 3/6 | NO EVIDENCE | -1.7 | +0.072 | +0.0000 |
| roe | Fundamental Quality | +0.0% | +0.0218 | +0.0000 (+0.0%) | 0.00 | SUPPORTED | 6/6 | REGIME DEPENDENT | +0.7 | +0.037 | +0.0000 |
| fund_quality | Fundamental Quality | +0.0% | -0.0214 | -0.0155 (-32.6%) | 0.73 | SUPPORTED | 4/6 | REGIME DEPENDENT | +1.9 | +0.040 | -0.0000 |
| eps_growth_yoy | Fundamental Growth | +0.1% | +0.0004 | +0.0000 (+0.0%) | 0.00 | DESCRIPTIVE ONLY | 4/6 | REGIME DEPENDENT | -3.7 | -0.021 | +0.0000 |
| rev_growth_yoy | Fundamental Growth | +0.1% | +0.0077 | +0.0000 (+0.0%) | 0.00 | DESCRIPTIVE ONLY | 5/6 | REGIME DEPENDENT | -2.3 | +0.032 | +0.0000 |
| sharpe_252 | Risk-Adjusted Performance | +1.9% | +0.0100 | +0.0000 (+0.0%) | 0.00 | SUPPORTED | 2/6 | NO EVIDENCE | +0.8 | +0.029 | +0.0000 |
| sortino_252 | Risk-Adjusted Performance | +2.0% | -0.0047 | -0.0000 (-0.0%) | 0.00 | SUPPORTED | 3/6 | NO EVIDENCE | +0.0 | +0.021 | +0.0000 |
| alpha_252 | Risk-Adjusted Performance | +3.2% | +0.0075 | +0.0000 (+0.0%) | 0.00 | SUPPORTED | 4/6 | REGIME DEPENDENT | +0.8 | +0.049 | +0.0000 |
| ram | Risk-Adjusted Performance | +5.4% | -0.0223 | -0.0000 (-0.0%) | 0.00 | DESCRIPTIVE ONLY | 3/6 | REGIME DEPENDENT | -0.3 | +0.077 | +0.0000 |
| vol_20 | Volatility | -0.2% | +0.0067 | +0.0000 (+0.0%) | 0.00 | DESCRIPTIVE ONLY | 4/6 | NO EVIDENCE | -0.3 | +0.057 | +0.0000 |
| vol_60 | Volatility | +0.0% | +0.0058 | +0.0000 (+0.0%) | 0.00 | SUPPORTED | 2/6 | UNSTABLE | +0.2 | +0.032 | +0.0000 |
| downside_vol_60 | Volatility | +0.0% | -0.0072 | -0.0021 (-4.4%) | 0.29 | SUPPORTED | 3/6 | REGIME DEPENDENT | +1.2 | -0.009 | -0.0000 |
| ewma_vol | Volatility | +0.3% | -0.0070 | -0.0018 (-3.7%) | 0.25 | SUPPORTED | 3/6 | NO EVIDENCE | +1.2 | +0.062 | -0.0000 |
| garch_vol | Volatility | +0.7% | +0.0086 | +0.0000 (+0.0%) | 0.00 | DESCRIPTIVE ONLY | 4/6 | UNSTABLE | -0.8 | +0.024 | +0.0000 |
| vol_ratio | Volatility | +0.0% | -0.0234 | -0.0000 (-0.0%) | 0.00 | SUPPORTED | 3/6 | REGIME DEPENDENT | +0.3 | +0.218 | +0.0000 |
| vol_of_vol | Volatility | -0.4% | +0.0048 | +0.0000 (+0.0%) | 0.00 | DESCRIPTIVE ONLY | 3/6 | REGIME DEPENDENT | -0.4 | -0.037 | +0.0000 |
| vol_pctile | Volatility | -0.0% | +0.0250 | +0.0000 (+0.0%) | 0.00 | DESCRIPTIVE ONLY | 4/6 | REGIME DEPENDENT | -0.0 | +0.247 | +0.0000 |
| skew_60 | Statistical / Time Series | -3.6% | -0.0145 | -0.0000 (-0.0%) | 0.00 | SUPPORTED | 4/6 | NO EVIDENCE | +0.3 | +0.088 | +0.0000 |
| kurt_60 | Statistical / Time Series | +0.0% | +0.0021 | +0.0000 (+0.0%) | 0.00 | DESCRIPTIVE ONLY | 4/6 | UNSTABLE | -0.7 | -0.055 | +0.0000 |
| ar1_63 | Statistical / Time Series | +0.1% | -0.0002 | -0.0000 (-0.0%) | 0.00 | DESCRIPTIVE ONLY | 2/6 | NO EVIDENCE | -1.0 | -0.101 | +0.0000 |
| acf1_252 | Statistical / Time Series | +0.0% | +0.0010 | +0.0000 (+0.0%) | 0.00 | DESCRIPTIVE ONLY | 4/6 | NO EVIDENCE | -0.5 | -0.069 | +0.0000 |
| half_life | Statistical / Time Series | +0.1% | +0.0095 | +0.0000 (+0.0%) | 0.00 | SUPPORTED | 5/6 | STABLE | +0.6 | -0.033 | +0.0000 |
| adf_t | Statistical / Time Series | +0.2% | +0.0047 | +0.0000 (+0.0%) | 0.00 | DESCRIPTIVE ONLY | 3/6 | NO EVIDENCE | -0.4 | -0.040 | +0.0000 |
| idio_vol_252 | Statistical / Time Series | -0.4% | -0.0287 | -0.0000 (-0.0%) | 0.00 | DESCRIPTIVE ONLY | 4/6 | REGIME DEPENDENT | -0.4 | -0.016 | +0.0000 |
| drawdown_252 | Statistical / Time Series | +0.3% | +0.0013 | +0.0008 (+1.8%) | 0.63 | SUPPORTED | 3/6 | REGIME DEPENDENT | +1.6 | +0.032 | +0.0000 |
| y10 | Rates | +0.0% | -0.0023 | -0.0000 (-0.0%) | 0.00 | DESCRIPTIVE ONLY | 4/6 | NO EVIDENCE | -0.1 | +0.167 | +0.0000 |
| d_y10_3m | Rates | +0.0% | -0.0002 | -0.0000 (-0.0%) | 0.00 | SUPPORTED | 2/6 | NO EVIDENCE | +1.0 | +0.208 | +0.0000 |
| slope_10y3m | Rates | +0.0% | +0.0029 | +0.0000 (+0.0%) | 0.00 | SUPPORTED | 3/6 | REGIME DEPENDENT | +0.9 | -0.289 | +0.0000 |
| d_slope_3m | Rates | -0.1% | +0.0028 | +0.0000 (+0.0%) | 0.00 | DESCRIPTIVE ONLY | 2/6 | NO EVIDENCE | -0.4 | +0.094 | +0.0000 |
| real_y10 | Rates | +0.0% | +0.0039 | +0.0000 (+0.0%) | 0.00 | SUPPORTED | 5/6 | STABLE | +0.8 | +0.626 | +0.0000 |
| breakeven_10y | Rates | +0.0% | -0.0040 | -0.0000 (-0.0%) | 0.00 | DESCRIPTIVE ONLY | 4/6 | UNSTABLE | -0.3 | +0.273 | +0.0000 |
| rate_duration | Rates | +6.0% | +0.0002 | +0.0000 (+0.0%) | 0.00 | DESCRIPTIVE ONLY | 1/6 | REGIME DEPENDENT | -1.2 | -0.099 | +0.0000 |
| credit_spread | Credit | +0.0% | +0.0048 | +0.0000 (+0.0%) | 0.00 | SUPPORTED | 5/6 | UNSTABLE | +0.2 | -0.549 | +0.0000 |
| d_credit_3m | Credit | +0.0% | +0.0084 | +0.0000 (+0.0%) | 0.00 | DESCRIPTIVE ONLY | 5/6 | REGIME DEPENDENT | -0.0 | -0.357 | +0.0000 |
| credit_signal | Credit | -0.4% | +0.0079 | +0.0000 (+0.0%) | 0.00 | SUPPORTED | 5/6 | UNSTABLE | +0.7 | -0.128 | +0.0000 |
| cpi_yoy | Macro | +0.0% | -0.0006 | -0.0000 (-0.0%) | 0.00 | DESCRIPTIVE ONLY | 4/6 | REGIME DEPENDENT | -0.4 | +0.523 | +0.0000 |
| unemp_gap | Macro | +0.0% | -0.0007 | -0.0000 (-0.0%) | 0.00 | DESCRIPTIVE ONLY | 2/6 | NO EVIDENCE | -0.6 | -0.111 | +0.0000 |
| oil_mom_3m | Macro | +0.8% | +0.0010 | +0.0000 (+0.0%) | 0.00 | DESCRIPTIVE ONLY | 4/6 | NO EVIDENCE | -0.5 | -0.621 | +0.0000 |
| gold_mom_3m | Macro | +2.3% | +0.0028 | +0.0000 (+0.0%) | 0.00 | DESCRIPTIVE ONLY | 3/6 | REGIME DEPENDENT | -0.5 | -0.782 | +0.0000 |
| nfci | Liquidity | +0.0% | +0.0020 | +0.0000 (+0.0%) | 0.00 | DESCRIPTIVE ONLY | 5/6 | UNSTABLE | -0.2 | +0.278 | +0.0000 |
| fed_bs_growth | Liquidity | +0.0% | -0.0015 | -0.0000 (-0.0%) | 0.00 | DESCRIPTIVE ONLY | 1/6 | UNSTABLE | -0.6 | -0.010 | +0.0000 |
| vix | Liquidity | +0.0% | +0.0030 | +0.0000 (+0.0%) | 0.00 | DESCRIPTIVE ONLY | 2/6 | NO EVIDENCE | -0.2 | -0.073 | +0.0000 |
| d_vix_1m | Liquidity | +0.0% | +0.0013 | +0.0000 (+0.0%) | 0.00 | SUPPORTED | 3/6 | NO EVIDENCE | +0.3 | +0.091 | +0.0000 |
| volume_z | Liquidity | +0.0% | +0.0036 | +0.0000 (+0.0%) | 0.00 | DESCRIPTIVE ONLY | 4/6 | NO EVIDENCE | -1.0 | +0.038 | +0.0000 |
| dollar_mom_3m | Cross-Asset | -1.9% | +0.0096 | +0.0000 (+0.0%) | 0.00 | SUPPORTED | 4/6 | UNSTABLE | +0.2 | +0.213 | +0.0000 |
| beta_252 | Cross-Asset | +0.0% | -0.0166 | -0.0000 (-0.0%) | 0.00 | DESCRIPTIVE ONLY | 4/6 | REGIME DEPENDENT | -1.0 | -0.056 | +0.0000 |
| corr_252 | Cross-Asset | +0.0% | +0.0088 | +0.0000 (+0.0%) | 0.00 | SUPPORTED | 3/6 | REGIME DEPENDENT | +0.0 | -0.225 | +0.0000 |
| rate_beta_252 | Cross-Asset | +0.0% | +0.0117 | +0.0000 (+0.0%) | 0.00 | DESCRIPTIVE ONLY | 2/6 | REGIME DEPENDENT | -0.1 | -0.033 | +0.0000 |
| dollar_beta_252 | Cross-Asset | +0.0% | -0.0095 | -0.0000 (-0.0%) | 0.00 | DESCRIPTIVE ONLY | 3/6 | NO EVIDENCE | -0.6 | -0.235 | +0.0000 |
| excess_3m | Relative Value | +7.4% | -0.0007 | -0.0000 (-0.0%) | 0.00 | SUPPORTED | 4/6 | NO EVIDENCE | +0.8 | -0.315 | +0.0000 |
| rel_strength_6m | Relative Value | +4.9% | -0.0253 | -0.0000 (-0.0%) | 0.00 | DESCRIPTIVE ONLY | 4/6 | REGIME DEPENDENT | -0.2 | -0.060 | +0.0000 |
| rel_value | Relative Value | +4.9% | -0.0042 | -0.0000 (-0.0%) | 0.00 | SUPPORTED | 3/6 | NO EVIDENCE | +0.2 | +0.090 | +0.0000 |

### Family weights and their signals (3M, Alpha, validated shares)

| Family | Production share | Learned share | Signals (learned) |
|---|---|---|---|
| Fundamental Quality | +0.1% | -32.6% | fund_quality -32.6% |
| Momentum | +18.9% | +25.0% | ret_6m +4.8%, mom_12_1 +20.2% |
| Valuation | +0.7% | -20.2% | earnings_yield -20.2% |
| Trend | +22.6% | +12.3% | ma_cross +12.3% |
| Volatility | +0.4% | -8.1% | downside_vol_60 -4.4%, ewma_vol -3.7% |
| Statistical / Time Series | -3.4% | +1.8% | drawdown_252 +1.8% |
| Mean Reversion | -5.7% | +0.0% | — |
| Fundamental Growth | +0.3% | +0.0% | — |
| Risk-Adjusted Performance | +12.5% | +0.0% | — |
| Rates | +5.9% | +0.0% | — |
| Credit | -0.4% | +0.0% | — |
| Macro | +3.1% | +0.0% | — |
| Liquidity | +0.0% | +0.0% | — |
| Cross-Asset | -1.9% | +0.0% | — |
| Relative Value | +17.2% | +0.0% | — |

### Confidence, regime, decay and applicability (3M; global, walk-forward)

| Construction | Alpha rank IC (t) | vs free weights (t) | Directional deviance gain (t) | vs free (t) |
|---|---|---|---|---|
| free signed weights on x (learned) | -0.0184 (-0.8) | — (—) | -0.3 (-1.4) | — (—) |
| production structure δ·x·c·r·d | +0.0026 (+0.1) | +0.0210 (+0.8) | -0.1 (-1.1) | +0.2 (+1.0) |
| without confidence (γc = 0) | +0.0072 (+0.4) | +0.0256 (+1.0) | -0.1 (-1.6) | +0.1 (+0.8) |
| without regime (γr = 0) | +0.0016 (+0.1) | +0.0200 (+0.8) | -0.1 (-1.0) | +0.2 (+1.1) |
| without decay (γd = 0) | +0.0033 (+0.2) | +0.0216 (+0.9) | -0.1 (-1.1) | +0.2 (+1.0) |
| δ·x only | +0.0086 (+0.4) | +0.0269 (+1.1) | -0.2 (-1.6) | +0.1 (+0.8) |
| applicability mask (x only where applicable) | +0.0111 (+0.6) | +0.0295 (+1.4) | -0.6 (-3.9) | -0.4 (-1.5) |

Regime-dependent weights (3M, Alpha): mom_12_1 (growth: expansion -0.005, recession +0.054, t -2.4); macd (growth: expansion -0.020, recession +0.028, t -4.9); pctile_252 (growth: expansion +0.007, recession +0.061, t -2.1); rsi_14 (rates: rising_rates +0.015, falling_rates +0.039, t -2.0); value_5y (growth: expansion +0.008, recession -0.033, t +3.4); earnings_yield (rates: rising_rates -0.016, falling_rates -0.004, t -2.0); sales_yield (growth: expansion -0.008, recession +0.031, t -2.0); roe (growth: expansion +0.028, recession -0.052, t +3.3).

### Score magnitude (3M) — does a larger |score| mean a stronger outcome?

| |score| | Validated: records | relative return | hit | rank IC | Production: records | relative return | hit |
|---|---|---|---|---|---|---|---|
| 0–10 | 109,114 | -0.183% | -1.0% | -0.012 | 109,201 | +0.102% | +0.3% |
| 10–25 | 8,884 | +0.902% | +5.4% | +0.127 | 8,221 | -0.315% | -2.2% |
| 25–50 | 370 | +0.721% | -0.3% | -0.114 | 948 | -0.927% | -7.3% |
| 50–75 | 66 | +10.799% | +15.2% | +0.372 | 62 | -2.137% | -9.7% |
| 75– | 2 | — | — | — | 4 | — | — |

Monotonic (every bucket populated and rising): validated no, production no. Directional magnitude = its calibration: 50%–55% predicted 52.2% → realised 61.9% (n 12,297), 55%–60% predicted 58.0% → realised 59.6% (n 27,516), 60%–65% predicted 62.8% → realised 63.1% (n 31,638), 65%–70% predicted 67.2% → realised 63.6% (n 36,601), 70%–75% predicted 72.1% → realised 60.8% (n 6,807), 75%–100% predicted 80.8% → realised 73.0% (n 3,577).

## 3–6. 6M (132,227 records, 153 assets)

### Production vs the learned weight sets (walk-forward, identical records)

| Weight set | Rank IC (t) | Δ rank IC vs production (t) | Quintile spread | Net L/S (t) | Eras won | Split t | FDR | Status |
|---|---|---|---|---|---|---|---|---|
| A production | -0.0183 (-0.6) | — | -0.53% | -0.498% (-0.3) | — | — | — | production |
| B historical best fit | -0.0057 (-0.2) | +0.0126 (+0.2) | -0.10% | +0.386% (+0.2) | 1/4 | -0.3 | ✗ | NOT VALIDATED |
| C validated deployable | +0.0245 (+0.8) | +0.0428 (+0.9) | +1.29% | -1.291% (-0.8) | 2/4 | +0.0 | ✗ | NOT VALIDATED |
| D simple global | +0.0041 (+0.1) | +0.0225 (+0.5) | -0.30% | -0.989% (-0.7) | 1/4 | -0.8 | ✗ | NOT VALIDATED |
| E uniform-depth hierarchy | -0.0115 (-0.4) | +0.0068 (+0.1) | -1.05% | -1.577% (-1.3) | 1/4 | -1.0 | ✗ | NOT VALIDATED |

Directional (P(up) beyond the PIT base prior; gates: Brier and log loss vs prior-only and vs prior + production, balanced accuracy, eras, split, calibration):

| Weight set | Brier | Brier gain vs prior (t) | vs current (t) | Log-loss gain | Balanced acc. | ECE | Slope | Eras won | Status |
|---|---|---|---|---|---|---|---|---|---|
| prior-only | +0.2230 | — | — | — | 51.5% | +0.0407 | +0.76 | — | benchmark |
| B historical best fit | +0.2288 | -0.00611 (-2.6) | -2.2 | -0.01439 | 51.2% | +0.0566 | +0.27 | 0/4 | NOT VALIDATED |
| C validated deployable | +0.2296 | -0.00677 (-2.7) | -2.4 | -0.01608 | 51.2% | +0.0637 | +0.23 | 0/4 | NOT VALIDATED |
| D simple global | +0.2291 | -0.00644 (-2.3) | -2.1 | -0.01592 | 51.3% | +0.0598 | +0.26 | 0/4 | NOT VALIDATED |
| E uniform-depth hierarchy | +0.2291 | -0.00639 (-2.3) | -2.1 | -0.01589 | 51.3% | +0.0621 | +0.26 | 0/4 | NOT VALIDATED |

### Optimisers (global level, walk-forward, every choice nested)

| Optimiser | Alpha rank IC (t) | vs ridge (t) | Directional deviance gain (t) | vs ridge (t) |
|---|---|---|---|---|
| ridge | +0.0041 (+0.1) | — (—) | -0.4 (-2.4) | — (—) |
| ridge (pointwise) | +0.0083 (+0.3) | +0.0042 (+0.6) | — (—) | — (—) |
| elastic net | +0.0031 (+0.1) | -0.0011 (-0.5) | -0.1 (-1.1) | +0.3 (+2.4) |
| sign-constrained | +0.0082 (+0.3) | +0.0041 (+0.2) | -0.4 (-2.2) | +0.0 (+0.2) |
| pairwise logistic (RankNet) | +0.0067 (+0.2) | +0.0026 (+0.2) | — (—) | — (—) |
| ridge + residual boosting | +0.0114 (+0.4) | +0.0073 (+0.7) | -0.5 (-2.4) | -0.1 (-1.5) |

Residual boosting's most used interactions (Alpha; a nonlinear diagnostic, never production): net_margin × rel_value ×28, idio_vol_252 × vol_of_vol ×10, ma_cross × net_margin ×10, dollar_beta_252 × idio_vol_252 ×8, alpha_252 × regime:growth ×8, dollar_beta_252 × earnings_yield ×7.

### Where specialisation stops helping (uniform depth, walk-forward mean rank IC, t)

Every cell is a walk-forward result (each era scored by weights learned before it), but K and depth are held fixed across eras here: choosing the best cell uses hindsight. The nested choice is weight set E above.

| K (pooling) | Global | Class | Product type | Sector | Industry | Asset |
|---|---|---|---|---|---|---|
| 10 | +0.0041 (+0.1) | -0.0066 (-0.2) | -0.0021 (-0.1) | -0.0010 (-0.0) | -0.0068 (-0.2) | -0.0058 (-0.2) |
| 50 | +0.0041 (+0.1) | -0.0046 (-0.1) | -0.0028 (-0.1) | +0.0020 (+0.1) | -0.0027 (-0.1) | -0.0011 (-0.0) |
| 200 | +0.0041 (+0.1) | -0.0052 (-0.2) | -0.0071 (-0.2) | -0.0032 (-0.1) | -0.0057 (-0.2) | -0.0017 (-0.1) |
| 1000 | +0.0041 (+0.1) | -0.0002 (-0.0) | -0.0056 (-0.2) | -0.0065 (-0.2) | -0.0093 (-0.3) | -0.0065 (-0.2) |
| 5000 | +0.0041 (+0.1) | +0.0043 (+0.1) | +0.0008 (+0.0) | +0.0001 (+0.0) | -0.0011 (-0.0) | +0.0004 (+0.0) |
| 20000 | +0.0041 (+0.1) | +0.0046 (+0.2) | +0.0034 (+0.1) | +0.0031 (+0.1) | +0.0027 (+0.1) | +0.0031 (+0.1) |

Validated model today: K = 10, 6 of 74 global weights trusted (ρ > 0), 0 node × signal specialisations kept (Class 0, Product type 0, Sector 0, Industry 0, Asset 0).

### The most important table — Global node, 6M (Alpha)

Weights are on standardised signals; *shares* are signed shares of total |weight| so production and learned are on one scale. Reliability: VALIDATED (OOS t ≥ 2, BH across signals × horizons, sign stable in ≥ 4 eras) · SUPPORTED (positive OOS contribution) · DESCRIPTIVE ONLY. Current value: mean of today's signal across assets; current contribution: mean of today's validated weight × signal.

| Signal | Family | Production (share) | Best fit | Validated (share) | Trust ρ | Reliability | Era sign stability | Era class | OOS contribution t | Current value | Current contribution |
|---|---|---|---|---|---|---|---|---|---|---|---|
| ret_3m | Momentum | +6.9% | -0.0052 | -0.0000 (-0.0%) | 0.00 | SUPPORTED | 4/6 | REGIME DEPENDENT | +0.8 | -0.076 | +0.0000 |
| ret_6m | Momentum | +7.6% | +0.0102 | +0.0044 (+5.5%) | 0.43 | SUPPORTED | 3/6 | UNSTABLE | +1.3 | -0.029 | +0.0000 |
| ret_12m | Momentum | +3.6% | -0.0072 | -0.0000 (-0.0%) | 0.00 | DESCRIPTIVE ONLY | 4/6 | NO EVIDENCE | -0.3 | +0.072 | +0.0000 |
| mom_12_1 | Momentum | +4.8% | +0.0042 | +0.0000 (+0.0%) | 0.00 | SUPPORTED | 1/6 | REGIME DEPENDENT | +0.1 | +0.168 | +0.0000 |
| ret_1m | Momentum | +0.2% | -0.0052 | -0.0000 (-0.0%) | 0.00 | DESCRIPTIVE ONLY | 5/6 | UNSTABLE | -0.1 | -0.332 | +0.0000 |
| ma_cross | Trend | +3.6% | +0.0558 | +0.0321 (+40.6%) | 0.58 | SUPPORTED | 5/6 | STABLE | +1.5 | +0.141 | +0.0000 |
| dist_ma200 | Trend | +4.7% | -0.0078 | -0.0000 (-0.0%) | 0.00 | DESCRIPTIVE ONLY | 1/6 | NO EVIDENCE | -0.7 | -0.042 | +0.0000 |
| macd | Trend | +3.2% | +0.0104 | +0.0000 (+0.0%) | 0.00 | DESCRIPTIVE ONLY | 3/6 | REGIME DEPENDENT | -0.2 | -0.257 | +0.0000 |
| pctile_252 | Trend | +7.9% | +0.0096 | +0.0000 (+0.0%) | 0.00 | DESCRIPTIVE ONLY | 3/6 | REGIME DEPENDENT | -0.9 | -0.046 | +0.0000 |
| trend_quality | Trend | +8.1% | +0.0149 | +0.0000 (+0.0%) | 0.00 | SUPPORTED | 3/6 | NO EVIDENCE | +0.2 | -0.084 | +0.0000 |
| ret_1d | Mean Reversion | -1.2% | -0.0078 | -0.0000 (-0.0%) | 0.00 | SUPPORTED | 5/6 | STABLE | +0.1 | -0.284 | +0.0000 |
| ret_1w | Mean Reversion | -0.4% | +0.0024 | +0.0000 (+0.0%) | 0.00 | DESCRIPTIVE ONLY | 2/6 | NO EVIDENCE | -0.1 | -0.266 | +0.0000 |
| z_20 | Mean Reversion | -0.2% | -0.0008 | -0.0000 (-0.0%) | 0.00 | DESCRIPTIVE ONLY | 3/6 | REGIME DEPENDENT | -0.1 | -0.440 | +0.0000 |
| z_50 | Mean Reversion | -0.2% | +0.0029 | +0.0000 (+0.0%) | 0.00 | SUPPORTED | 2/6 | NO EVIDENCE | +0.6 | -0.426 | +0.0000 |
| rsi_14 | Mean Reversion | -0.2% | +0.0037 | +0.0000 (+0.0%) | 0.00 | SUPPORTED | 2/6 | NO EVIDENCE | +0.3 | -0.436 | +0.0000 |
| bb_pctb | Mean Reversion | -0.2% | -0.0008 | -0.0000 (-0.0%) | 0.00 | DESCRIPTIVE ONLY | 3/6 | REGIME DEPENDENT | -0.1 | -0.440 | +0.0000 |
| mr_opportunity | Mean Reversion | +0.5% | -0.0044 | -0.0000 (-0.0%) | 0.00 | SUPPORTED | 4/6 | NO EVIDENCE | +0.2 | +0.378 | +0.0000 |
| value_5y | Valuation | +1.0% | +0.0071 | +0.0000 (+0.0%) | 0.00 | DESCRIPTIVE ONLY | 5/6 | NO EVIDENCE | -0.6 | -0.106 | +0.0000 |
| earnings_yield | Valuation | +0.2% | -0.0219 | -0.0192 (-24.3%) | 0.88 | SUPPORTED | 3/6 | NO EVIDENCE | +2.8 | -0.053 | -0.0000 |
| pe_rel_5y | Valuation | -0.4% | -0.0021 | -0.0000 (-0.0%) | 0.00 | DESCRIPTIVE ONLY | 3/6 | NO EVIDENCE | -1.3 | -0.024 | +0.0000 |
| book_to_price | Valuation | +0.1% | -0.0128 | -0.0000 (-0.0%) | 0.00 | DESCRIPTIVE ONLY | 4/6 | UNSTABLE | -0.3 | -0.074 | +0.0000 |
| sales_yield | Valuation | +0.1% | -0.0096 | -0.0000 (-0.0%) | 0.00 | DESCRIPTIVE ONLY | 3/6 | NO EVIDENCE | -1.3 | -0.119 | +0.0000 |
| net_margin | Fundamental Quality | +0.0% | -0.0063 | -0.0000 (-0.0%) | 0.00 | DESCRIPTIVE ONLY | 4/6 | UNSTABLE | -0.6 | +0.076 | +0.0000 |
| roe | Fundamental Quality | +0.1% | +0.0239 | +0.0000 (+0.0%) | 0.00 | SUPPORTED | 5/6 | REGIME DEPENDENT | +0.5 | +0.040 | +0.0000 |
| fund_quality | Fundamental Quality | +0.1% | -0.0217 | -0.0000 (-0.0%) | 0.00 | SUPPORTED | 3/6 | REGIME DEPENDENT | +0.8 | +0.044 | +0.0000 |
| eps_growth_yoy | Fundamental Growth | +0.1% | -0.0029 | -0.0000 (-0.0%) | 0.00 | DESCRIPTIVE ONLY | 3/6 | NO EVIDENCE | -2.8 | -0.021 | +0.0000 |
| rev_growth_yoy | Fundamental Growth | +0.2% | +0.0118 | +0.0000 (+0.0%) | 0.00 | DESCRIPTIVE ONLY | 4/6 | NO EVIDENCE | -0.0 | +0.015 | +0.0000 |
| sharpe_252 | Risk-Adjusted Performance | +4.0% | -0.0022 | -0.0000 (-0.0%) | 0.00 | SUPPORTED | 3/6 | NO EVIDENCE | +0.8 | -0.059 | +0.0000 |
| sortino_252 | Risk-Adjusted Performance | +4.1% | +0.0025 | +0.0000 (+0.0%) | 0.00 | SUPPORTED | 3/6 | REGIME DEPENDENT | +0.1 | -0.067 | +0.0000 |
| alpha_252 | Risk-Adjusted Performance | +3.3% | +0.0157 | +0.0000 (+0.0%) | 0.00 | DESCRIPTIVE ONLY | 5/6 | UNSTABLE | -0.1 | +0.047 | +0.0000 |
| ram | Risk-Adjusted Performance | +9.7% | -0.0188 | -0.0078 (-9.9%) | 0.41 | SUPPORTED | 3/6 | NO EVIDENCE | +1.3 | +0.123 | -0.0001 |
| vol_20 | Volatility | +0.0% | -0.0213 | -0.0141 (-17.8%) | 0.66 | SUPPORTED | 5/6 | REGIME DEPENDENT | +1.7 | +0.031 | +0.0001 |
| vol_60 | Volatility | +0.0% | +0.0296 | +0.0000 (+0.0%) | 0.00 | DESCRIPTIVE ONLY | 4/6 | UNSTABLE | -0.2 | -0.033 | +0.0000 |
| downside_vol_60 | Volatility | +0.4% | -0.0220 | -0.0000 (-0.0%) | 0.00 | SUPPORTED | 3/6 | NO EVIDENCE | +0.9 | -0.001 | +0.0000 |
| ewma_vol | Volatility | +0.1% | -0.0039 | -0.0015 (-1.9%) | 0.38 | SUPPORTED | 3/6 | NO EVIDENCE | +1.3 | +0.044 | +0.0000 |
| garch_vol | Volatility | +0.1% | -0.0027 | -0.0000 (-0.0%) | 0.00 | DESCRIPTIVE ONLY | 4/6 | REGIME DEPENDENT | -0.6 | +0.058 | +0.0000 |
| vol_ratio | Volatility | +0.0% | +0.0040 | +0.0000 (+0.0%) | 0.00 | SUPPORTED | 4/6 | REGIME DEPENDENT | +0.4 | +0.053 | +0.0000 |
| vol_of_vol | Volatility | -0.3% | +0.0043 | +0.0000 (+0.0%) | 0.00 | DESCRIPTIVE ONLY | 4/6 | REGIME DEPENDENT | -0.5 | -0.113 | +0.0000 |
| vol_pctile | Volatility | +0.0% | +0.0254 | +0.0000 (+0.0%) | 0.00 | DESCRIPTIVE ONLY | 3/6 | REGIME DEPENDENT | -0.0 | +0.259 | +0.0000 |
| skew_60 | Statistical / Time Series | -2.1% | -0.0187 | -0.0000 (-0.0%) | 0.00 | DESCRIPTIVE ONLY | 4/6 | REGIME DEPENDENT | -0.1 | -0.146 | +0.0000 |
| kurt_60 | Statistical / Time Series | +0.0% | +0.0009 | +0.0000 (+0.0%) | 0.00 | DESCRIPTIVE ONLY | 4/6 | NO EVIDENCE | -0.2 | +0.037 | +0.0000 |
| ar1_63 | Statistical / Time Series | +0.2% | +0.0108 | +0.0000 (+0.0%) | 0.00 | DESCRIPTIVE ONLY | 5/6 | STABLE | -0.4 | +0.001 | +0.0000 |
| acf1_252 | Statistical / Time Series | +0.0% | -0.0134 | -0.0000 (-0.0%) | 0.00 | SUPPORTED | 4/6 | UNSTABLE | +0.8 | -0.028 | +0.0000 |
| half_life | Statistical / Time Series | +0.0% | +0.0157 | +0.0000 (+0.0%) | 0.00 | SUPPORTED | 5/6 | STABLE | +0.4 | -0.036 | +0.0000 |
| adf_t | Statistical / Time Series | +0.2% | -0.0123 | -0.0000 (-0.0%) | 0.00 | DESCRIPTIVE ONLY | 5/6 | REGIME DEPENDENT | -0.9 | +0.028 | +0.0000 |
| idio_vol_252 | Statistical / Time Series | -0.5% | -0.0346 | -0.0000 (-0.0%) | 0.00 | DESCRIPTIVE ONLY | 3/6 | REGIME DEPENDENT | -1.9 | +0.024 | +0.0000 |
| drawdown_252 | Statistical / Time Series | +0.0% | -0.0396 | -0.0000 (-0.0%) | 0.00 | DESCRIPTIVE ONLY | 6/6 | STABLE | -0.5 | -0.086 | +0.0000 |
| y10 | Rates | +0.0% | -0.0010 | -0.0000 (-0.0%) | 0.00 | DESCRIPTIVE ONLY | 3/6 | NO EVIDENCE | -0.3 | +0.101 | +0.0000 |
| d_y10_3m | Rates | +0.0% | -0.0048 | -0.0000 (-0.0%) | 0.00 | DESCRIPTIVE ONLY | 3/6 | UNSTABLE | -0.0 | +0.145 | +0.0000 |
| slope_10y3m | Rates | +0.0% | +0.0015 | +0.0000 (+0.0%) | 0.00 | SUPPORTED | 3/6 | NO EVIDENCE | +0.5 | -0.331 | +0.0000 |
| d_slope_3m | Rates | +0.0% | +0.0018 | +0.0000 (+0.0%) | 0.00 | DESCRIPTIVE ONLY | 2/6 | REGIME DEPENDENT | -0.6 | +0.044 | +0.0000 |
| real_y10 | Rates | +0.0% | +0.0040 | +0.0000 (+0.0%) | 0.00 | SUPPORTED | 5/6 | UNSTABLE | +0.3 | +0.454 | +0.0000 |
| breakeven_10y | Rates | +0.0% | -0.0009 | -0.0000 (-0.0%) | 0.00 | DESCRIPTIVE ONLY | 3/6 | UNSTABLE | -0.1 | +0.574 | +0.0000 |
| rate_duration | Rates | +2.8% | -0.0120 | -0.0000 (-0.0%) | 0.00 | DESCRIPTIVE ONLY | 5/6 | REGIME DEPENDENT | -0.5 | +0.028 | +0.0000 |
| credit_spread | Credit | +0.0% | +0.0065 | +0.0000 (+0.0%) | 0.00 | DESCRIPTIVE ONLY | 5/6 | REGIME DEPENDENT | -0.1 | -0.351 | +0.0000 |
| d_credit_3m | Credit | +0.0% | +0.0004 | +0.0000 (+0.0%) | 0.00 | DESCRIPTIVE ONLY | 3/6 | NO EVIDENCE | -0.5 | +0.044 | +0.0000 |
| credit_signal | Credit | +0.0% | +0.0045 | +0.0000 (+0.0%) | 0.00 | SUPPORTED | 3/6 | NO EVIDENCE | +0.4 | -0.317 | +0.0000 |
| cpi_yoy | Macro | +0.0% | -0.0035 | -0.0000 (-0.0%) | 0.00 | DESCRIPTIVE ONLY | 3/6 | NO EVIDENCE | -0.1 | -0.049 | +0.0000 |
| unemp_gap | Macro | +0.0% | +0.0010 | +0.0000 (+0.0%) | 0.00 | DESCRIPTIVE ONLY | 3/6 | NO EVIDENCE | -0.4 | -0.039 | +0.0000 |
| oil_mom_3m | Macro | +0.0% | -0.0008 | -0.0000 (-0.0%) | 0.00 | DESCRIPTIVE ONLY | 3/6 | NO EVIDENCE | -0.0 | +0.992 | +0.0000 |
| gold_mom_3m | Macro | +0.0% | +0.0023 | +0.0000 (+0.0%) | 0.00 | DESCRIPTIVE ONLY | 4/6 | REGIME DEPENDENT | -0.2 | +0.113 | +0.0000 |
| nfci | Liquidity | +0.0% | -0.0005 | -0.0000 (-0.0%) | 0.00 | DESCRIPTIVE ONLY | 3/6 | REGIME DEPENDENT | -0.1 | +0.336 | +0.0000 |
| fed_bs_growth | Liquidity | +0.0% | +0.0012 | +0.0000 (+0.0%) | 0.00 | DESCRIPTIVE ONLY | 5/6 | UNSTABLE | -0.5 | -0.103 | +0.0000 |
| vix | Liquidity | +0.0% | +0.0004 | +0.0000 (+0.0%) | 0.00 | DESCRIPTIVE ONLY | 3/6 | NO EVIDENCE | -0.1 | +0.313 | +0.0000 |
| d_vix_1m | Liquidity | +0.0% | +0.0030 | +0.0000 (+0.0%) | 0.00 | SUPPORTED | 4/6 | NO EVIDENCE | +0.0 | +0.423 | +0.0000 |
| volume_z | Liquidity | +0.0% | +0.0015 | +0.0000 (+0.0%) | 0.00 | DESCRIPTIVE ONLY | 4/6 | REGIME DEPENDENT | -0.2 | +0.437 | +0.0000 |
| dollar_mom_3m | Cross-Asset | +0.0% | +0.0072 | +0.0000 (+0.0%) | 0.00 | SUPPORTED | 5/6 | STABLE | +0.1 | +0.140 | +0.0000 |
| beta_252 | Cross-Asset | +0.0% | -0.0214 | -0.0000 (-0.0%) | 0.00 | DESCRIPTIVE ONLY | 5/6 | REGIME DEPENDENT | -0.7 | -0.172 | +0.0000 |
| corr_252 | Cross-Asset | +0.0% | +0.0135 | +0.0000 (+0.0%) | 0.00 | SUPPORTED | 3/6 | REGIME DEPENDENT | +0.8 | -0.036 | +0.0000 |
| rate_beta_252 | Cross-Asset | +0.8% | +0.0282 | +0.0000 (+0.0%) | 0.00 | SUPPORTED | 4/6 | UNSTABLE | +0.1 | +0.183 | +0.0000 |
| dollar_beta_252 | Cross-Asset | +0.0% | -0.0110 | -0.0000 (-0.0%) | 0.00 | DESCRIPTIVE ONLY | 3/6 | NO EVIDENCE | -1.1 | +0.108 | +0.0000 |
| excess_3m | Relative Value | +4.3% | -0.0190 | -0.0000 (-0.0%) | 0.00 | DESCRIPTIVE ONLY | 5/6 | STABLE | -0.4 | +0.185 | +0.0000 |
| rel_strength_6m | Relative Value | +5.4% | -0.0193 | -0.0000 (-0.0%) | 0.00 | DESCRIPTIVE ONLY | 4/6 | NO EVIDENCE | -0.5 | +0.183 | +0.0000 |
| rel_value | Relative Value | +6.1% | +0.0010 | +0.0000 (+0.0%) | 0.00 | SUPPORTED | 2/6 | NO EVIDENCE | +0.7 | +0.031 | +0.0000 |

### Family weights and their signals (6M, Alpha, validated shares)

| Family | Production share | Learned share | Signals (learned) |
|---|---|---|---|
| Trend | +27.5% | +40.6% | ma_cross +40.6% |
| Valuation | +1.0% | -24.3% | earnings_yield -24.3% |
| Volatility | +0.3% | -19.7% | vol_20 -17.8%, ewma_vol -1.9% |
| Risk-Adjusted Performance | +21.1% | -9.9% | ram -9.9% |
| Momentum | +23.1% | +5.5% | ret_6m +5.5% |
| Mean Reversion | -1.9% | +0.0% | — |
| Fundamental Quality | +0.2% | +0.0% | — |
| Fundamental Growth | +0.3% | +0.0% | — |
| Statistical / Time Series | -2.2% | +0.0% | — |
| Rates | +2.8% | +0.0% | — |
| Credit | +0.0% | +0.0% | — |
| Macro | +0.0% | +0.0% | — |
| Liquidity | +0.0% | +0.0% | — |
| Cross-Asset | +0.8% | +0.0% | — |
| Relative Value | +15.7% | +0.0% | — |

### Confidence, regime, decay and applicability (6M; global, walk-forward)

| Construction | Alpha rank IC (t) | vs free weights (t) | Directional deviance gain (t) | vs free (t) |
|---|---|---|---|---|
| free signed weights on x (learned) | +0.0041 (+0.1) | — (—) | -0.4 (-2.4) | — (—) |
| production structure δ·x·c·r·d | -0.0197 (-0.6) | -0.0238 (-0.7) | +0.0 (+0.2) | +0.4 (+2.3) |
| without confidence (γc = 0) | -0.0191 (-0.6) | -0.0232 (-0.6) | +0.0 (+0.2) | +0.4 (+2.4) |
| without regime (γr = 0) | -0.0198 (-0.6) | -0.0239 (-0.7) | +0.0 (+0.1) | +0.4 (+2.3) |
| without decay (γd = 0) | -0.0197 (-0.6) | -0.0238 (-0.7) | +0.0 (+0.2) | +0.4 (+2.3) |
| δ·x only | -0.0184 (-0.6) | -0.0225 (-0.6) | +0.0 (+0.2) | +0.4 (+2.4) |
| applicability mask (x only where applicable) | -0.0149 (-0.6) | -0.0191 (-0.7) | -0.1 (-1.1) | +0.3 (+1.7) |

Regime-dependent weights (6M, Alpha): ret_3m (rates: rising_rates +0.005, falling_rates -0.009, t -2.3); mom_12_1 (growth: expansion -0.013, recession +0.029, t -2.9); macd (volatility: high_vol +0.015, low_vol -0.022, t +2.5); pctile_252 (volatility: high_vol +0.066, low_vol -0.045, t +2.5); z_20 (rates: rising_rates +0.008, falling_rates -0.008, t +2.1); bb_pctb (rates: rising_rates +0.008, falling_rates -0.008, t +2.1); roe (growth: expansion +0.030, recession -0.038, t +3.1); fund_quality (rates: rising_rates -0.040, falling_rates +0.004, t -2.5).

### Score magnitude (6M) — does a larger |score| mean a stronger outcome?

| |score| | Validated: records | relative return | hit | rank IC | Production: records | relative return | hit |
|---|---|---|---|---|---|---|---|
| 0–10 | 96,446 | +0.314% | -0.8% | -0.009 | 97,780 | +0.137% | +0.6% |
| 10–25 | 12,958 | +0.831% | +1.6% | +0.002 | 11,544 | -0.201% | +1.8% |
| 25–50 | 1,479 | +2.870% | +9.2% | +0.068 | 1,528 | -2.332% | -9.0% |
| 50–75 | 9 | — | — | — | 41 | — | — |
| 75– | 1 | — | — | — | 0 | — | — |

Monotonic (every bucket populated and rising): validated no, production no. Directional magnitude = its calibration: 50%–55% predicted 51.9% → realised 61.0% (n 9,637), 55%–60% predicted 58.0% → realised 68.8% (n 11,916), 60%–65% predicted 62.4% → realised 62.3% (n 26,766), 65%–70% predicted 67.6% → realised 68.8% (n 28,310), 70%–75% predicted 72.4% → realised 63.8% (n 23,242), 75%–100% predicted 80.9% → realised 69.8% (n 11,027).

## 3–6. 12M (92,549 records, 145 assets)

### Production vs the learned weight sets (walk-forward, identical records)

| Weight set | Rank IC (t) | Δ rank IC vs production (t) | Quintile spread | Net L/S (t) | Eras won | Split t | FDR | Status |
|---|---|---|---|---|---|---|---|---|
| A production | +0.0033 (+0.1) | — | +1.00% | +2.029% (+0.7) | — | — | — | production |
| B historical best fit | +0.0232 (+0.6) | +0.0200 (+0.3) | +0.54% | -3.158% (-1.1) | 1/4 | -0.4 | ✗ | NOT VALIDATED |
| C validated deployable | +0.0128 (+0.3) | +0.0095 (+0.2) | +0.26% | +0.804% (+0.3) | 2/4 | -0.4 | ✗ | NOT VALIDATED |
| D simple global | +0.0640 (+1.5) | +0.0607 (+1.1) | +2.78% | +0.844% (+0.2) | 3/4 | +0.4 | ✗ | NOT VALIDATED |
| E uniform-depth hierarchy | +0.0182 (+0.4) | +0.0149 (+0.3) | +1.62% | +0.768% (+0.3) | 1/4 | +0.0 | ✗ | NOT VALIDATED |

Directional (P(up) beyond the PIT base prior; gates: Brier and log loss vs prior-only and vs prior + production, balanced accuracy, eras, split, calibration):

| Weight set | Brier | Brier gain vs prior (t) | vs current (t) | Log-loss gain | Balanced acc. | ECE | Slope | Eras won | Status |
|---|---|---|---|---|---|---|---|---|---|
| prior-only | +0.2095 | — | — | — | 51.1% | +0.0547 | +0.33 | — | benchmark |
| B historical best fit | +0.2130 | -0.00409 (-0.9) | -1.1 | -0.01163 | 50.8% | +0.0671 | +0.15 | 0/4 | NOT VALIDATED |
| C validated deployable | +0.2112 | -0.00259 (-0.6) | -0.8 | -0.00860 | 51.3% | +0.0613 | +0.25 | 1/4 | NOT VALIDATED |
| D simple global | +0.2144 | -0.00563 (-1.0) | -1.2 | -0.01664 | 51.0% | +0.0727 | +0.11 | 0/4 | NOT VALIDATED |
| E uniform-depth hierarchy | +0.2149 | -0.00604 (-1.1) | -1.3 | -0.01762 | 50.9% | +0.0766 | +0.09 | 0/4 | NOT VALIDATED |

### Optimisers (global level, walk-forward, every choice nested)

| Optimiser | Alpha rank IC (t) | vs ridge (t) | Directional deviance gain (t) | vs ridge (t) |
|---|---|---|---|---|
| ridge | +0.0644 (+1.5) | — (—) | -0.2 (-2.4) | — (—) |
| ridge (pointwise) | +0.0707 (+1.7) | +0.0064 (+0.8) | — (—) | — (—) |
| elastic net | +0.0727 (+1.6) | +0.0083 (+0.5) | -0.1 (-1.5) | +0.1 (+1.9) |
| sign-constrained | +0.0671 (+1.5) | +0.0027 (+0.1) | -0.1 (-1.3) | +0.1 (+1.4) |
| pairwise logistic (RankNet) | +0.0461 (+1.1) | -0.0182 (-0.6) | — (—) | — (—) |
| ridge + residual boosting | +0.0716 (+1.6) | +0.0073 (+0.6) | -0.5 (-2.9) | -0.3 (-2.6) |

Residual boosting's most used interactions (Alpha; a nonlinear diagnostic, never production): dollar_beta_252 × rate_beta_252 ×17, net_margin × rate_beta_252 ×10, net_margin × sales_yield ×8, alpha_252 × rev_growth_yoy ×7, beta_252 × idio_vol_252 ×6, dollar_beta_252 × vol_pctile ×5.

### Where specialisation stops helping (uniform depth, walk-forward mean rank IC, t)

Every cell is a walk-forward result (each era scored by weights learned before it), but K and depth are held fixed across eras here: choosing the best cell uses hindsight. The nested choice is weight set E above.

| K (pooling) | Global | Class | Product type | Sector | Industry | Asset |
|---|---|---|---|---|---|---|
| 10 | +0.0644 (+1.5) | +0.0308 (+0.7) | +0.0330 (+0.8) | +0.0203 (+0.5) | +0.0158 (+0.4) | +0.0203 (+0.5) |
| 50 | +0.0644 (+1.5) | +0.0482 (+1.1) | +0.0411 (+1.0) | +0.0290 (+0.7) | +0.0242 (+0.6) | +0.0282 (+0.7) |
| 200 | +0.0644 (+1.5) | +0.0616 (+1.4) | +0.0533 (+1.3) | +0.0437 (+1.0) | +0.0361 (+0.8) | +0.0382 (+0.9) |
| 1000 | +0.0644 (+1.5) | +0.0715 (+1.7) | +0.0684 (+1.6) | +0.0649 (+1.5) | +0.0608 (+1.4) | +0.0604 (+1.4) |
| 5000 | +0.0644 (+1.5) | +0.0688 (+1.6) | +0.0697 (+1.6) | +0.0697 (+1.6) | +0.0684 (+1.6) | +0.0685 (+1.6) |
| 20000 | +0.0644 (+1.5) | +0.0658 (+1.5) | +0.0662 (+1.5) | +0.0663 (+1.6) | +0.0660 (+1.5) | +0.0661 (+1.5) |

Validated model today: K = 10, 3 of 74 global weights trusted (ρ > 0), 0 node × signal specialisations kept (Class 0, Product type 0, Sector 0, Industry 0, Asset 0).

### The most important table — Global node, 12M (Alpha)

Weights are on standardised signals; *shares* are signed shares of total |weight| so production and learned are on one scale. Reliability: VALIDATED (OOS t ≥ 2, BH across signals × horizons, sign stable in ≥ 4 eras) · SUPPORTED (positive OOS contribution) · DESCRIPTIVE ONLY. Current value: mean of today's signal across assets; current contribution: mean of today's validated weight × signal.

| Signal | Family | Production (share) | Best fit | Validated (share) | Trust ρ | Reliability | Era sign stability | Era class | OOS contribution t | Current value | Current contribution |
|---|---|---|---|---|---|---|---|---|---|---|---|
| ret_3m | Momentum | +6.7% | -0.0211 | -0.0000 (-0.0%) | 0.00 | DESCRIPTIVE ONLY | 2/6 | REGIME DEPENDENT | -0.4 | +0.213 | +0.0000 |
| ret_6m | Momentum | +7.1% | +0.0229 | +0.0000 (+0.0%) | 0.00 | DESCRIPTIVE ONLY | 4/6 | NO EVIDENCE | -1.6 | +0.141 | +0.0000 |
| ret_12m | Momentum | +4.7% | +0.0122 | +0.0000 (+0.0%) | 0.00 | DESCRIPTIVE ONLY | 4/6 | REGIME DEPENDENT | -0.8 | +0.080 | +0.0000 |
| mom_12_1 | Momentum | +4.9% | +0.0248 | +0.0000 (+0.0%) | 0.00 | SUPPORTED | 4/6 | REGIME DEPENDENT | +0.1 | +0.032 | +0.0000 |
| ret_1m | Momentum | +0.0% | +0.0194 | +0.0000 (+0.0%) | 0.00 | SUPPORTED | 3/6 | NO EVIDENCE | +0.3 | +0.172 | +0.0000 |
| ma_cross | Trend | +3.8% | +0.0444 | +0.0000 (+0.0%) | 0.00 | DESCRIPTIVE ONLY | 5/6 | UNSTABLE | -0.1 | +0.133 | +0.0000 |
| dist_ma200 | Trend | +3.4% | -0.0001 | -0.0000 (-0.0%) | 0.00 | DESCRIPTIVE ONLY | 3/6 | NO EVIDENCE | -1.2 | +0.191 | +0.0000 |
| macd | Trend | +2.2% | +0.0127 | +0.0000 (+0.0%) | 0.00 | SUPPORTED | 4/6 | REGIME DEPENDENT | +0.4 | +0.209 | +0.0000 |
| pctile_252 | Trend | +6.4% | +0.0257 | +0.0000 (+0.0%) | 0.00 | DESCRIPTIVE ONLY | 3/6 | NO EVIDENCE | -0.4 | +0.200 | +0.0000 |
| trend_quality | Trend | +5.9% | +0.0164 | +0.0000 (+0.0%) | 0.00 | SUPPORTED | 3/6 | NO EVIDENCE | +0.6 | +0.223 | +0.0000 |
| ret_1d | Mean Reversion | -0.8% | -0.0011 | -0.0000 (-0.0%) | 0.00 | DESCRIPTIVE ONLY | 6/6 | STABLE | -0.2 | -0.035 | +0.0000 |
| ret_1w | Mean Reversion | -0.1% | +0.0017 | +0.0000 (+0.0%) | 0.00 | SUPPORTED | 2/6 | NO EVIDENCE | +0.0 | +0.025 | +0.0000 |
| z_20 | Mean Reversion | -0.0% | -0.0025 | -0.0000 (-0.0%) | 0.00 | DESCRIPTIVE ONLY | 4/6 | NO EVIDENCE | -0.3 | +0.120 | +0.0000 |
| z_50 | Mean Reversion | -0.0% | +0.0139 | +0.0000 (+0.0%) | 0.00 | SUPPORTED | 5/6 | UNSTABLE | +0.6 | +0.211 | +0.0000 |
| rsi_14 | Mean Reversion | -0.0% | +0.0089 | +0.0000 (+0.0%) | 0.00 | SUPPORTED | 5/6 | UNSTABLE | +0.8 | +0.210 | +0.0000 |
| bb_pctb | Mean Reversion | -0.0% | -0.0025 | -0.0000 (-0.0%) | 0.00 | DESCRIPTIVE ONLY | 4/6 | NO EVIDENCE | -0.3 | +0.120 | +0.0000 |
| mr_opportunity | Mean Reversion | +0.1% | -0.0015 | -0.0000 (-0.0%) | 0.00 | DESCRIPTIVE ONLY | 2/6 | NO EVIDENCE | -0.3 | -0.074 | +0.0000 |
| value_5y | Valuation | +2.5% | -0.0034 | -0.0000 (-0.0%) | 0.00 | DESCRIPTIVE ONLY | 2/6 | REGIME DEPENDENT | -1.0 | -0.209 | +0.0000 |
| earnings_yield | Valuation | +0.3% | -0.0263 | -0.0000 (-0.0%) | 0.00 | SUPPORTED | 2/6 | NO EVIDENCE | +0.8 | -0.074 | +0.0000 |
| pe_rel_5y | Valuation | -0.2% | -0.0124 | -0.0000 (-0.0%) | 0.00 | DESCRIPTIVE ONLY | 2/6 | NO EVIDENCE | -0.5 | -0.002 | +0.0000 |
| book_to_price | Valuation | +0.3% | -0.0328 | -0.0000 (-0.0%) | 0.00 | SUPPORTED | 4/6 | UNSTABLE | +0.5 | -0.079 | +0.0000 |
| sales_yield | Valuation | +0.9% | -0.0097 | -0.0000 (-0.0%) | 0.00 | DESCRIPTIVE ONLY | 2/6 | NO EVIDENCE | -1.2 | -0.126 | +0.0000 |
| net_margin | Fundamental Quality | +0.1% | -0.0131 | -0.0000 (-0.0%) | 0.00 | DESCRIPTIVE ONLY | 3/6 | UNSTABLE | -0.2 | +0.069 | +0.0000 |
| roe | Fundamental Quality | +0.2% | -0.0079 | -0.0064 (-33.5%) | 0.81 | SUPPORTED | 3/6 | NO EVIDENCE | +2.3 | +0.050 | -0.0000 |
| fund_quality | Fundamental Quality | +0.2% | +0.0043 | +0.0000 (+0.0%) | 0.00 | DESCRIPTIVE ONLY | 3/6 | REGIME DEPENDENT | -0.8 | +0.052 | +0.0000 |
| eps_growth_yoy | Fundamental Growth | +0.1% | -0.0063 | -0.0000 (-0.0%) | 0.00 | DESCRIPTIVE ONLY | 4/6 | NO EVIDENCE | -1.8 | -0.020 | +0.0000 |
| rev_growth_yoy | Fundamental Growth | +0.5% | +0.0159 | +0.0000 (+0.0%) | 0.00 | DESCRIPTIVE ONLY | 4/6 | NO EVIDENCE | -2.6 | -0.011 | +0.0000 |
| sharpe_252 | Risk-Adjusted Performance | +5.2% | -0.0789 | -0.0000 (-0.0%) | 0.00 | DESCRIPTIVE ONLY | 4/6 | UNSTABLE | -0.2 | -0.041 | +0.0000 |
| sortino_252 | Risk-Adjusted Performance | +5.2% | -0.0129 | -0.0000 (-0.0%) | 0.00 | DESCRIPTIVE ONLY | 3/6 | NO EVIDENCE | -0.6 | -0.048 | +0.0000 |
| alpha_252 | Risk-Adjusted Performance | +4.6% | +0.0343 | +0.0000 (+0.0%) | 0.00 | SUPPORTED | 4/6 | UNSTABLE | +0.1 | +0.034 | +0.0000 |
| ram | Risk-Adjusted Performance | +10.9% | +0.0357 | +0.0126 (+66.0%) | 0.35 | SUPPORTED | 4/6 | UNSTABLE | +1.2 | +0.056 | +0.0000 |
| vol_20 | Volatility | +0.0% | +0.0017 | +0.0001 (+0.5%) | 0.06 | SUPPORTED | 1/6 | NO EVIDENCE | +1.0 | -0.228 | -0.0000 |
| vol_60 | Volatility | +0.0% | +0.0190 | +0.0000 (+0.0%) | 0.00 | SUPPORTED | 3/6 | NO EVIDENCE | +0.4 | -0.222 | +0.0000 |
| downside_vol_60 | Volatility | +0.0% | -0.0159 | -0.0000 (-0.0%) | 0.00 | DESCRIPTIVE ONLY | 3/6 | REGIME DEPENDENT | -0.9 | -0.262 | +0.0000 |
| ewma_vol | Volatility | +0.0% | +0.0000 | +0.0000 (+0.0%) | 0.00 | SUPPORTED | 3/6 | REGIME DEPENDENT | +0.7 | -0.232 | +0.0000 |
| garch_vol | Volatility | +0.0% | -0.0059 | -0.0000 (-0.0%) | 0.00 | SUPPORTED | 4/6 | REGIME DEPENDENT | +0.5 | -0.169 | +0.0000 |
| vol_ratio | Volatility | +0.0% | -0.0268 | -0.0000 (-0.0%) | 0.00 | DESCRIPTIVE ONLY | 3/6 | NO EVIDENCE | -0.4 | -0.332 | +0.0000 |
| vol_of_vol | Volatility | +0.0% | +0.0085 | +0.0000 (+0.0%) | 0.00 | SUPPORTED | 3/6 | NO EVIDENCE | +0.4 | +0.336 | +0.0000 |
| vol_pctile | Volatility | +0.0% | +0.0407 | +0.0000 (+0.0%) | 0.00 | DESCRIPTIVE ONLY | 5/6 | STABLE | -0.1 | -0.355 | +0.0000 |
| skew_60 | Statistical / Time Series | -1.1% | -0.0073 | -0.0000 (-0.0%) | 0.00 | DESCRIPTIVE ONLY | 4/6 | NO EVIDENCE | -0.0 | +0.149 | +0.0000 |
| kurt_60 | Statistical / Time Series | +0.0% | -0.0021 | -0.0000 (-0.0%) | 0.00 | SUPPORTED | 3/6 | REGIME DEPENDENT | +0.2 | -0.017 | +0.0000 |
| ar1_63 | Statistical / Time Series | +0.0% | +0.0137 | +0.0000 (+0.0%) | 0.00 | SUPPORTED | 3/6 | NO EVIDENCE | +0.0 | -0.095 | +0.0000 |
| acf1_252 | Statistical / Time Series | +0.0% | -0.0304 | -0.0000 (-0.0%) | 0.00 | SUPPORTED | 5/6 | STABLE | +1.0 | -0.056 | +0.0000 |
| half_life | Statistical / Time Series | +0.0% | +0.0115 | +0.0000 (+0.0%) | 0.00 | SUPPORTED | 4/6 | UNSTABLE | +0.0 | -0.029 | +0.0000 |
| adf_t | Statistical / Time Series | +0.0% | +0.0015 | +0.0000 (+0.0%) | 0.00 | DESCRIPTIVE ONLY | 4/6 | UNSTABLE | -0.0 | +0.005 | +0.0000 |
| idio_vol_252 | Statistical / Time Series | -1.0% | -0.0879 | -0.0000 (-0.0%) | 0.00 | SUPPORTED | 5/6 | UNSTABLE | +0.4 | +0.018 | +0.0000 |
| drawdown_252 | Statistical / Time Series | +0.0% | -0.0691 | -0.0000 (-0.0%) | 0.00 | SUPPORTED | 4/6 | REGIME DEPENDENT | +0.0 | +0.191 | +0.0000 |
| y10 | Rates | +0.0% | -0.0023 | -0.0000 (-0.0%) | 0.00 | DESCRIPTIVE ONLY | 3/6 | NO EVIDENCE | -0.0 | +0.053 | +0.0000 |
| d_y10_3m | Rates | +0.0% | -0.0035 | -0.0000 (-0.0%) | 0.00 | SUPPORTED | 2/6 | NO EVIDENCE | +0.5 | -0.299 | +0.0000 |
| slope_10y3m | Rates | +0.0% | -0.0019 | -0.0000 (-0.0%) | 0.00 | SUPPORTED | 2/6 | REGIME DEPENDENT | +0.2 | -0.525 | +0.0000 |
| d_slope_3m | Rates | +0.0% | +0.0042 | +0.0000 (+0.0%) | 0.00 | DESCRIPTIVE ONLY | 4/6 | REGIME DEPENDENT | -0.5 | +0.118 | +0.0000 |
| real_y10 | Rates | +0.0% | +0.0055 | +0.0000 (+0.0%) | 0.00 | SUPPORTED | 6/6 | STABLE | +0.1 | +0.376 | +0.0000 |
| breakeven_10y | Rates | +0.0% | -0.0011 | -0.0000 (-0.0%) | 0.00 | DESCRIPTIVE ONLY | 5/6 | UNSTABLE | -0.1 | +0.427 | +0.0000 |
| rate_duration | Rates | +2.6% | +0.0097 | +0.0000 (+0.0%) | 0.00 | DESCRIPTIVE ONLY | 3/6 | REGIME DEPENDENT | -0.5 | -0.115 | +0.0000 |
| credit_spread | Credit | +0.0% | -0.0042 | -0.0000 (-0.0%) | 0.00 | DESCRIPTIVE ONLY | 2/6 | NO EVIDENCE | -0.4 | -0.444 | +0.0000 |
| d_credit_3m | Credit | +0.0% | +0.0087 | +0.0000 (+0.0%) | 0.00 | DESCRIPTIVE ONLY | 5/6 | UNSTABLE | -0.5 | -0.093 | +0.0000 |
| credit_signal | Credit | +0.0% | +0.0072 | +0.0000 (+0.0%) | 0.00 | DESCRIPTIVE ONLY | 4/6 | UNSTABLE | -0.6 | -0.271 | +0.0000 |
| cpi_yoy | Macro | +0.0% | -0.0032 | -0.0000 (-0.0%) | 0.00 | DESCRIPTIVE ONLY | 3/6 | REGIME DEPENDENT | -0.3 | +0.127 | +0.0000 |
| unemp_gap | Macro | +0.0% | +0.0048 | +0.0000 (+0.0%) | 0.00 | DESCRIPTIVE ONLY | 3/6 | NO EVIDENCE | -0.2 | -0.111 | +0.0000 |
| oil_mom_3m | Macro | +0.0% | -0.0033 | -0.0000 (-0.0%) | 0.00 | DESCRIPTIVE ONLY | 4/6 | NO EVIDENCE | -0.3 | -0.387 | +0.0000 |
| gold_mom_3m | Macro | +0.0% | -0.0008 | -0.0000 (-0.0%) | 0.00 | SUPPORTED | 3/6 | REGIME DEPENDENT | +0.1 | +0.448 | +0.0000 |
| nfci | Liquidity | +0.0% | +0.0002 | +0.0000 (+0.0%) | 0.00 | DESCRIPTIVE ONLY | 5/6 | NO EVIDENCE | -0.3 | +0.166 | +0.0000 |
| fed_bs_growth | Liquidity | +0.0% | +0.0055 | +0.0000 (+0.0%) | 0.00 | DESCRIPTIVE ONLY | 4/6 | NO EVIDENCE | -0.3 | -0.238 | +0.0000 |
| vix | Liquidity | +0.0% | +0.0015 | +0.0000 (+0.0%) | 0.00 | DESCRIPTIVE ONLY | 4/6 | UNSTABLE | -0.3 | -0.238 | +0.0000 |
| d_vix_1m | Liquidity | +0.0% | +0.0051 | +0.0000 (+0.0%) | 0.00 | SUPPORTED | 5/6 | UNSTABLE | +0.0 | +0.025 | +0.0000 |
| volume_z | Liquidity | +0.0% | +0.0004 | +0.0000 (+0.0%) | 0.00 | DESCRIPTIVE ONLY | 3/6 | NO EVIDENCE | -0.1 | +0.315 | +0.0000 |
| dollar_mom_3m | Cross-Asset | +0.0% | +0.0049 | +0.0000 (+0.0%) | 0.00 | DESCRIPTIVE ONLY | 5/6 | REGIME DEPENDENT | -0.2 | -0.164 | +0.0000 |
| beta_252 | Cross-Asset | +0.0% | -0.0323 | -0.0000 (-0.0%) | 0.00 | DESCRIPTIVE ONLY | 4/6 | NO EVIDENCE | -1.0 | -0.204 | +0.0000 |
| corr_252 | Cross-Asset | +0.0% | +0.0230 | +0.0000 (+0.0%) | 0.00 | DESCRIPTIVE ONLY | 3/6 | NO EVIDENCE | -1.4 | -0.011 | +0.0000 |
| rate_beta_252 | Cross-Asset | +0.0% | +0.0505 | +0.0000 (+0.0%) | 0.00 | SUPPORTED | 5/6 | STABLE | +0.5 | +0.349 | +0.0000 |
| dollar_beta_252 | Cross-Asset | +0.0% | -0.0033 | -0.0000 (-0.0%) | 0.00 | DESCRIPTIVE ONLY | 3/6 | REGIME DEPENDENT | -0.9 | +0.148 | +0.0000 |
| excess_3m | Relative Value | +4.2% | +0.0040 | +0.0000 (+0.0%) | 0.00 | DESCRIPTIVE ONLY | 2/6 | UNSTABLE | -0.2 | -0.234 | +0.0000 |
| rel_strength_6m | Relative Value | +5.2% | -0.0492 | -0.0000 (-0.0%) | 0.00 | DESCRIPTIVE ONLY | 4/6 | UNSTABLE | -0.0 | -0.291 | +0.0000 |
| rel_value | Relative Value | +8.4% | +0.0279 | +0.0000 (+0.0%) | 0.00 | DESCRIPTIVE ONLY | 3/6 | REGIME DEPENDENT | -0.3 | +0.208 | +0.0000 |

### Family weights and their signals (12M, Alpha, validated shares)

| Family | Production share | Learned share | Signals (learned) |
|---|---|---|---|
| Risk-Adjusted Performance | +25.9% | +66.0% | ram +66.0% |
| Fundamental Quality | +0.5% | -33.5% | roe -33.5% |
| Volatility | +0.0% | +0.5% | vol_20 +0.5% |
| Momentum | +23.4% | +0.0% | — |
| Trend | +21.8% | +0.0% | — |
| Mean Reversion | -0.9% | +0.0% | — |
| Valuation | +3.7% | +0.0% | — |
| Fundamental Growth | +0.6% | +0.0% | — |
| Statistical / Time Series | -2.0% | +0.0% | — |
| Rates | +2.6% | +0.0% | — |
| Credit | +0.0% | +0.0% | — |
| Macro | +0.0% | +0.0% | — |
| Liquidity | +0.0% | +0.0% | — |
| Cross-Asset | +0.0% | +0.0% | — |
| Relative Value | +17.8% | +0.0% | — |

### Confidence, regime, decay and applicability (12M; global, walk-forward)

| Construction | Alpha rank IC (t) | vs free weights (t) | Directional deviance gain (t) | vs free (t) |
|---|---|---|---|---|
| free signed weights on x (learned) | +0.0644 (+1.5) | — (—) | -0.2 (-2.4) | — (—) |
| production structure δ·x·c·r·d | +0.0033 (+0.1) | -0.0611 (-1.1) | +0.0 (+0.2) | +0.2 (+2.2) |
| without confidence (γc = 0) | +0.0519 (+1.0) | -0.0124 (-0.2) | +0.0 (+0.2) | +0.2 (+2.2) |
| without regime (γr = 0) | +0.0049 (+0.1) | -0.0595 (-1.1) | +0.0 (+0.2) | +0.2 (+2.2) |
| without decay (γd = 0) | +0.0033 (+0.1) | -0.0611 (-1.1) | +0.0 (+0.2) | +0.2 (+2.2) |
| δ·x only | +0.0530 (+1.1) | -0.0113 (-0.2) | +0.0 (+0.2) | +0.2 (+2.2) |
| applicability mask (x only where applicable) | +0.0425 (+1.1) | -0.0218 (-0.6) | -0.0 (-0.8) | +0.2 (+1.7) |

Regime-dependent weights (12M, Alpha): ret_3m (volatility: high_vol +0.011, low_vol -0.033, t +2.1); ret_12m (growth: expansion -0.007, recession +0.032, t -3.6); mom_12_1 (growth: expansion -0.001, recession +0.042, t -3.1); macd (volatility: high_vol +0.015, low_vol -0.007, t +2.0); value_5y (volatility: high_vol +0.009, low_vol -0.015, t +2.0, growth: expansion -0.007, recession +0.010, t -12.1); fund_quality (rates: rising_rates -0.028, falling_rates +0.033, t -2.8); downside_vol_60 (market: bull +0.022, bear -0.037, t +2.0); ewma_vol (volatility: high_vol +0.006, low_vol -0.013, t +3.8).

### Score magnitude (12M) — does a larger |score| mean a stronger outcome?

| |score| | Validated: records | relative return | hit | rank IC | Production: records | relative return | hit |
|---|---|---|---|---|---|---|---|
| 0–10 | 65,287 | +0.382% | +0.6% | +0.031 | 74,894 | +0.135% | +1.0% |
| 10–25 | 11,006 | -1.496% | +2.7% | +0.132 | 8,962 | +0.485% | +0.5% |
| 25–50 | 1,825 | -3.380% | +0.4% | -0.158 | 725 | +0.247% | +1.3% |
| 50–75 | 1,161 | -3.741% | -11.7% | -0.106 | 30 | — | — |
| 75– | 5,332 | -0.508% | -1.2% | -0.082 | 0 | — | — |

Monotonic (every bucket populated and rising): validated no, production no. Directional magnitude = its calibration: 50%–55% predicted 52.5% → realised 60.9% (n 4,585), 55%–60% predicted 57.7% → realised 73.6% (n 5,341), 60%–65% predicted 62.7% → realised 64.6% (n 19,380), 65%–70% predicted 67.4% → realised 69.9% (n 12,980), 70%–75% predicted 72.4% → realised 72.2% (n 11,247), 75%–100% predicted 80.5% → realised 74.3% (n 31,086).

## 4. What matters for each type of asset (validated Alpha weights, family shares)

**1W**

| Node | Momentum | Mean Reversion | Trend | Volatility | Rates | Liquidity | Cross-Asset | Risk-Adjusted Performance | Valuation |
|---|---|---|---|---|---|---|---|---|---|
| Global (global) | +18% | -18% | -11% | +5% | -5% | +4% | +3% | +3% | -1% |
| Commodity (class) | +16% | -16% | -10% | +4% | -4% | +3% | +3% | +3% | -1% |
| Credit (class) | +18% | -17% | -11% | +5% | +1% | +3% | +3% | +3% | -1% |
| Crypto (class) | +18% | -18% | -11% | +5% | -5% | +4% | +3% | +3% | -1% |
| Equity (class) | +18% | -18% | -11% | +5% | -5% | +4% | +3% | +3% | -1% |
| FX (class) | +19% | -18% | -11% | +5% | -5% | -3% | +3% | +3% | -1% |
| Rates (class) | +15% | -15% | -9% | +4% | +17% | +3% | +3% | +3% | -1% |
| Volatility (class) | +18% | -18% | -11% | +5% | -5% | +4% | +3% | +3% | -1% |
| commodity (ptype) | +16% | -15% | -10% | +4% | -4% | +3% | +3% | +3% | -1% |
| EM debt (ptype) | +18% | -17% | -11% | +5% | +1% | +3% | +3% | +3% | -1% |
| high yield (ptype) | +18% | -17% | -11% | +5% | +1% | +3% | +3% | +3% | -1% |
| hybrid (ptype) | +18% | -17% | -11% | +5% | +1% | +3% | +3% | +3% | -1% |
| investment grade (ptype) | +16% | -15% | -10% | +4% | +14% | +3% | +3% | +3% | -1% |
|  CLO (ptype) | +18% | -17% | -11% | +5% | +1% | +3% | +3% | +3% | -1% |
| municipal (ptype) | +18% | -17% | -11% | +5% | +1% | +3% | +3% | +3% | -1% |
| crypto (ptype) | +18% | -18% | -11% | +5% | -5% | +4% | +3% | +3% | -1% |
| broad equity ETF (ptype) | +17% | -20% | -11% | +5% | -5% | +3% | +6% | +3% | -1% |
| equity index (ptype) | +18% | -18% | -11% | +5% | -5% | +4% | +3% | +3% | -1% |
| factor ETF (ptype) | +18% | -18% | -11% | +5% | -5% | +4% | +3% | +3% | -1% |
| international equity ETF (ptype) | +18% | -18% | -11% | +5% | -5% | +4% | +3% | +3% | -1% |
|  inverse ETF (ptype) | +18% | -17% | -11% | +5% | -5% | +3% | +3% | +3% | -1% |
| sector ETF (ptype) | +18% | -18% | -11% | +5% | -5% | +4% | +3% | +3% | -1% |
| single stock (ptype) | +18% | -18% | -11% | +5% | -5% | +4% | +3% | +3% | -1% |
| EM FX (ptype) | +16% | -10% | -10% | +4% | -4% | -11% | +3% | +3% | -1% |
| dollar index (ptype) | +15% | -14% | -9% | +4% | -4% | -2% | -16% | +2% | -1% |
| major FX (ptype) | +19% | -18% | -11% | +5% | -5% | -3% | +3% | +3% | -1% |
| Treasury (ptype) | +15% | -15% | -9% | +4% | +19% | +3% | +2% | +2% | -1% |
|  MBS (ptype) | +15% | -15% | -9% | +4% | +17% | +3% | +3% | +3% | -1% |
| cash (ptype) | +12% | -30% | -10% | +3% | +13% | +2% | +2% | +2% | -1% |
| inflation-linked (ptype) | +14% | -14% | -9% | +4% | +21% | +3% | +2% | +2% | -1% |
| VIX futures ETN (ptype) | +18% | -18% | -11% | +5% | -5% | +4% | +3% | +3% | -1% |

**3M**

| Node | Fundamental Quality | Momentum | Valuation | Trend | Volatility | Statistical / Time Series | Rates | Liquidity | Relative Value |
|---|---|---|---|---|---|---|---|---|---|
| Global (global) | -33% | +25% | -20% | +12% | -8% | +2% | +0% | +0% | +0% |
| Commodity (class) | -33% | +25% | -20% | +12% | -8% | +2% | +0% | +0% | +0% |
| Credit (class) | -33% | +25% | -20% | +12% | -8% | +2% | +0% | +0% | +0% |
| Crypto (class) | -33% | +25% | -20% | +12% | -8% | +2% | +0% | +0% | +0% |
| Equity (class) | -33% | +25% | -20% | +12% | -8% | +2% | +0% | +0% | +0% |
| FX (class) | -33% | +25% | -20% | +12% | -8% | +2% | +0% | +0% | +0% |
| Rates (class) | -33% | +25% | -20% | +12% | -8% | +2% | +0% | +0% | +0% |
| Volatility (class) | -33% | +25% | -20% | +12% | -8% | +2% | +0% | +0% | +0% |
| commodity (ptype) | -33% | +25% | -20% | +12% | -8% | +2% | +0% | +0% | +0% |
| EM debt (ptype) | -33% | +25% | -20% | +12% | -8% | +2% | +0% | +0% | +0% |
| high yield (ptype) | -33% | +25% | -20% | +12% | -8% | +2% | +0% | +0% | +0% |
| hybrid (ptype) | -33% | +25% | -20% | +12% | -8% | +2% | +0% | +0% | +0% |
| investment grade (ptype) | -33% | +25% | -20% | +12% | -8% | +2% | +0% | +0% | +0% |
|  CLO (ptype) | -33% | +25% | -20% | +12% | -8% | +2% | +0% | +0% | +0% |
| municipal (ptype) | -33% | +25% | -20% | +12% | -8% | +2% | +0% | +0% | +0% |
| crypto (ptype) | -33% | +25% | -20% | +12% | -8% | +2% | +0% | +0% | +0% |
| broad equity ETF (ptype) | -33% | +25% | -20% | +12% | -8% | +2% | +0% | +0% | +0% |
| equity index (ptype) | -33% | +25% | -20% | +12% | -8% | +2% | +0% | +0% | +0% |
| factor ETF (ptype) | -33% | +25% | -20% | +12% | -8% | +2% | +0% | +0% | +0% |
| international equity ETF (ptype) | -33% | +25% | -20% | +12% | -8% | +2% | +0% | +0% | +0% |
|  inverse ETF (ptype) | -33% | +25% | -20% | +12% | -8% | +2% | +0% | +0% | +0% |
| sector ETF (ptype) | -33% | +25% | -20% | +12% | -8% | +2% | +0% | +0% | +0% |
| single stock (ptype) | -33% | +25% | -20% | +12% | -8% | +2% | +0% | +0% | +0% |
| EM FX (ptype) | -33% | +25% | -20% | +12% | -8% | +2% | +0% | +0% | +0% |
| dollar index (ptype) | -33% | +25% | -20% | +12% | -8% | +2% | +0% | +0% | +0% |
| major FX (ptype) | -33% | +25% | -20% | +12% | -8% | +2% | +0% | +0% | +0% |
| Treasury (ptype) | -33% | +25% | -20% | +12% | -8% | +2% | +0% | +0% | +0% |
|  MBS (ptype) | -33% | +25% | -20% | +12% | -8% | +2% | +0% | +0% | +0% |
| cash (ptype) | -33% | +25% | -20% | +12% | -8% | +2% | +0% | +0% | +0% |
| inflation-linked (ptype) | -33% | +25% | -20% | +12% | -8% | +2% | +0% | +0% | +0% |
| VIX futures ETN (ptype) | -33% | +25% | -20% | +12% | -8% | +2% | +0% | +0% | +0% |

**12M**

| Node | Risk-Adjusted Performance | Fundamental Quality | Volatility | Valuation | Rates | Statistical / Time Series | Liquidity | Relative Value | Macro |
|---|---|---|---|---|---|---|---|---|---|
| Global (global) | +66% | -34% | +1% | +0% | +0% | +0% | +0% | +0% | +0% |
| Commodity (class) | +66% | -34% | +1% | +0% | +0% | +0% | +0% | +0% | +0% |
| Credit (class) | +66% | -34% | +1% | +0% | +0% | +0% | +0% | +0% | +0% |
| Equity (class) | +66% | -34% | +1% | +0% | +0% | +0% | +0% | +0% | +0% |
| FX (class) | +66% | -34% | +1% | +0% | +0% | +0% | +0% | +0% | +0% |
| Rates (class) | +66% | -34% | +1% | +0% | +0% | +0% | +0% | +0% | +0% |
| commodity (ptype) | +66% | -34% | +1% | +0% | +0% | +0% | +0% | +0% | +0% |
| EM debt (ptype) | +66% | -34% | +1% | +0% | +0% | +0% | +0% | +0% | +0% |
| high yield (ptype) | +66% | -34% | +1% | +0% | +0% | +0% | +0% | +0% | +0% |
| hybrid (ptype) | +66% | -34% | +1% | +0% | +0% | +0% | +0% | +0% | +0% |
| investment grade (ptype) | +66% | -34% | +1% | +0% | +0% | +0% | +0% | +0% | +0% |
|  CLO (ptype) | +66% | -34% | +1% | +0% | +0% | +0% | +0% | +0% | +0% |
| municipal (ptype) | +66% | -34% | +1% | +0% | +0% | +0% | +0% | +0% | +0% |
| broad equity ETF (ptype) | +66% | -34% | +1% | +0% | +0% | +0% | +0% | +0% | +0% |
| equity index (ptype) | +66% | -34% | +1% | +0% | +0% | +0% | +0% | +0% | +0% |
| factor ETF (ptype) | +66% | -34% | +1% | +0% | +0% | +0% | +0% | +0% | +0% |
| international equity ETF (ptype) | +66% | -34% | +1% | +0% | +0% | +0% | +0% | +0% | +0% |
|  inverse ETF (ptype) | +66% | -34% | +1% | +0% | +0% | +0% | +0% | +0% | +0% |
| sector ETF (ptype) | +66% | -34% | +1% | +0% | +0% | +0% | +0% | +0% | +0% |
| single stock (ptype) | +66% | -34% | +1% | +0% | +0% | +0% | +0% | +0% | +0% |
| EM FX (ptype) | +66% | -34% | +1% | +0% | +0% | +0% | +0% | +0% | +0% |
| dollar index (ptype) | +66% | -34% | +1% | +0% | +0% | +0% | +0% | +0% | +0% |
| major FX (ptype) | +66% | -34% | +1% | +0% | +0% | +0% | +0% | +0% | +0% |
| Treasury (ptype) | +66% | -34% | +1% | +0% | +0% | +0% | +0% | +0% | +0% |
|  MBS (ptype) | +66% | -34% | +1% | +0% | +0% | +0% | +0% | +0% | +0% |
| inflation-linked (ptype) | +66% | -34% | +1% | +0% | +0% | +0% | +0% | +0% | +0% |

## 7. Today's learned weights — examples (Alpha)

Signed shares of each weight set's total |weight| (so production and the learned sets are on one scale): production's effective weights today; C, the trust-filtered validated set; and, where they exist, the weight sets in live shadow (D global, E hierarchy — at 1W). Sorted by the live-shadow hierarchy weight where it exists, else by C; the twelve largest shown. Contribution = weight × today's (week-demeaned) signal, in the same set's units.

**NVDA — 1W** · production score -0.8 · C -0.5 · D (simple global, live shadow) -7.5 from Global · E (uniform-depth hierarchy, live shadow) +1.8 from NVDA · p_up 54.2% (prior 54.0%) · C's hierarchy source: Global 100% · path: Global → Equity → single stock → Information Technology → Semiconductors → NVDA

| Signal | Production | C validated | D global (live) | E hierarchy (live) | Current value | Contribution |
|---|---|---|---|---|---|---|
| dist_ma200 | +5.0% | -13.6% | -6.1% | -5.5% | +0.006 | -0.0198 |
| mom_12_1 | +1.1% | +13.1% | +6.2% | +4.9% | -0.045 | +0.0076 |
| ret_1d | -0.5% | -7.5% | -3.7% | -4.3% | -0.021 | -0.0029 |
| ret_12m | +1.5% | -0.5% | -3.8% | -3.9% | -0.082 | -0.0068 |
| vix | +0.0% | +3.6% | +3.2% | +3.8% | -0.104 | +0.0152 |
| ma_cross | +1.3% | +2.4% | +3.2% | +3.5% | -0.060 | +0.0046 |
| mr_opportunity | +0.0% | -6.2% | -3.4% | -3.4% | +0.245 | +0.0117 |
| sortino_252 | +2.3% | -7.1% | -3.5% | -3.2% | -0.155 | -0.0119 |
| ret_1m | +0.0% | +5.0% | +2.9% | +3.1% | -0.155 | +0.0024 |
| ret_3m | +12.6% | -0.8% | -1.8% | -2.6% | -0.023 | -0.0087 |
| sharpe_252 | +2.0% | +6.0% | +3.0% | +2.4% | -0.114 | +0.0108 |
| ram | +2.7% | +4.1% | +2.3% | +2.4% | -0.064 | +0.0066 |

**NVDA — 3M** · production score -1.9 · C -11.8 · p_up 62.8% (prior 61.6%) · C's hierarchy source: Global 100% · path: Global → Equity → single stock → Information Technology → Semiconductors → NVDA

| Signal | Production | C validated | Current value | Contribution |
|---|---|---|---|---|
| fund_quality | +0.4% | -32.6% | +1.000 | -0.0574 |
| earnings_yield | +0.0% | -20.2% | +0.039 | -0.0056 |
| mom_12_1 | +1.3% | +20.2% | +0.088 | -0.0033 |
| ma_cross | +1.2% | +12.3% | -0.004 | -0.0018 |
| ret_6m | +6.1% | +4.8% | -0.070 | -0.0010 |
| downside_vol_60 | +0.0% | -4.4% | -0.235 | +0.0016 |
| ewma_vol | +0.0% | -3.7% | -0.189 | +0.0017 |
| drawdown_252 | +0.0% | +1.8% | +0.120 | +0.0001 |
| ret_3m | +12.8% | +0.0% | +0.082 | -0.0000 |
| ret_12m | +1.4% | +0.0% | +0.006 | -0.0000 |
| ret_1m | +0.0% | +0.0% | -0.312 | -0.0000 |
| dist_ma200 | +3.7% | +0.0% | -0.079 | -0.0000 |

**AAPL — 1W** · production score +0.1 · C +2.3 · D (simple global, live shadow) -6.5 from Global · E (uniform-depth hierarchy, live shadow) -1.0 from AAPL · p_up 52.0% (prior 52.5%) · C's hierarchy source: Global 100% · path: Global → Equity → single stock → Information Technology → Hardware → AAPL

| Signal | Production | C validated | D global (live) | E hierarchy (live) | Current value | Contribution |
|---|---|---|---|---|---|---|
| dist_ma200 | +0.4% | -13.6% | -6.1% | -5.5% | +0.191 | -0.0365 |
| mom_12_1 | +4.6% | +13.1% | +6.2% | +4.9% | +0.115 | +0.0202 |
| vix | +0.0% | +3.6% | +3.2% | +4.2% | -0.104 | +0.0165 |
| ret_12m | +5.7% | -0.5% | -3.8% | -4.0% | +0.196 | -0.0245 |
| ret_1d | -5.1% | -7.5% | -3.7% | -3.8% | +0.307 | -0.0240 |
| sortino_252 | +5.7% | -7.1% | -3.5% | -3.4% | +0.277 | -0.0338 |
| ma_cross | +0.1% | +2.4% | +3.2% | +3.4% | +0.151 | +0.0158 |
| mr_opportunity | +0.0% | -6.2% | -3.4% | -3.4% | -0.785 | +0.0609 |
| ret_1m | +0.0% | +5.0% | +2.9% | +2.9% | +0.313 | +0.0246 |
| vol_20 | +0.0% | -3.0% | -2.0% | -2.6% | -0.303 | +0.0078 |
| slope_10y3m | +0.0% | -1.9% | -1.3% | -2.5% | -0.168 | -0.0218 |
| ret_3m | +5.1% | -0.8% | -1.8% | -2.5% | +0.152 | -0.0153 |

**AAPL — 3M** · production score +0.1 · C +0.2 · p_up 63.2% (prior 61.8%) · C's hierarchy source: Global 100% · path: Global → Equity → single stock → Information Technology → Hardware → AAPL

| Signal | Production | C validated | Current value | Contribution |
|---|---|---|---|---|
| fund_quality | +0.3% | -32.6% | +0.410 | -0.0223 |
| earnings_yield | +0.0% | -20.2% | -0.619 | +0.0181 |
| mom_12_1 | +0.4% | +20.2% | +0.283 | +0.0015 |
| ma_cross | +1.8% | +12.3% | +0.047 | -0.0010 |
| ret_6m | +2.7% | +4.8% | -0.050 | -0.0009 |
| downside_vol_60 | +0.0% | -4.4% | -0.352 | +0.0025 |
| ewma_vol | +0.0% | -3.7% | -0.391 | +0.0030 |
| drawdown_252 | +0.0% | +1.8% | +0.275 | +0.0005 |
| ret_3m | +0.0% | +0.0% | +0.232 | +0.0000 |
| ret_12m | +0.2% | +0.0% | +0.189 | +0.0000 |
| ret_1m | +0.0% | +0.0% | -0.275 | -0.0000 |
| dist_ma200 | +1.3% | +0.0% | +0.040 | -0.0000 |

**JPM — 1W** · production score +0.7 · C -0.9 · D (simple global, live shadow) -7.6 from Global · E (uniform-depth hierarchy, live shadow) -4.3 from JPM · p_up 53.7% (prior 54.0%) · C's hierarchy source: Global 100% · path: Global → Equity → single stock → Financials → Banks → JPM

| Signal | Production | C validated | D global (live) | E hierarchy (live) | Current value | Contribution |
|---|---|---|---|---|---|---|
| dist_ma200 | +0.0% | -13.6% | -6.1% | -5.7% | +0.263 | -0.0456 |
| mom_12_1 | +0.3% | +13.1% | +6.2% | +4.7% | +0.204 | +0.0265 |
| vix | +0.0% | +3.6% | +3.2% | +4.2% | -0.104 | +0.0171 |
| ret_12m | +0.1% | -0.5% | -3.8% | -4.2% | +0.126 | -0.0214 |
| ret_1d | -7.0% | -7.5% | -3.7% | -3.7% | +0.157 | -0.0142 |
| breakeven_10y | +0.0% | -2.4% | -1.8% | -3.3% | +0.436 | -0.0399 |
| mr_opportunity | +3.4% | -6.2% | -3.4% | -3.3% | +0.095 | +0.0186 |
| sortino_252 | +0.0% | -7.1% | -3.5% | -3.1% | +0.058 | -0.0215 |
| ma_cross | +0.0% | +2.4% | +3.2% | +2.9% | +0.346 | +0.0233 |
| slope_10y3m | +0.0% | -1.9% | -1.3% | -2.8% | -0.168 | -0.0258 |
| ret_1m | +0.0% | +5.0% | +2.9% | +2.7% | -0.199 | +0.0000 |
| vol_20 | +0.0% | -3.0% | -2.0% | -2.6% | -0.372 | +0.0120 |

**JPM — 3M** · production score -1.2 · C -2.4 · p_up 62.3% (prior 61.1%) · C's hierarchy source: Global 100% · path: Global → Equity → single stock → Financials → Banks → JPM

| Signal | Production | C validated | Current value | Contribution |
|---|---|---|---|---|
| fund_quality | +0.0% | -32.6% | +0.526 | -0.0292 |
| earnings_yield | +0.0% | -20.2% | -0.716 | +0.0216 |
| mom_12_1 | +4.2% | +20.2% | +0.002 | -0.0055 |
| ma_cross | +1.4% | +12.3% | -0.122 | -0.0037 |
| ret_6m | +1.3% | +4.8% | -0.007 | -0.0006 |
| downside_vol_60 | +0.0% | -4.4% | -0.274 | +0.0019 |
| ewma_vol | +0.0% | -3.7% | -0.131 | +0.0014 |
| drawdown_252 | +0.0% | +1.8% | +0.495 | +0.0010 |
| ret_3m | +0.0% | +0.0% | +0.366 | +0.0000 |
| ret_12m | +1.2% | +0.0% | +0.149 | -0.0000 |
| ret_1m | +0.0% | +0.0% | +0.420 | +0.0000 |
| dist_ma200 | +0.0% | +0.0% | +0.159 | +0.0000 |

**XLK — 1W** · production score -4.5 · C +0.7 · D (simple global, live shadow) -9.6 from Global · E (uniform-depth hierarchy, live shadow) -6.6 from XLK · p_up 55.0% (prior 54.9%) · C's hierarchy source: Global 100% · path: Global → Equity → sector ETF → Information Technology → XLK

| Signal | Production | C validated | D global (live) | E hierarchy (live) | Current value | Contribution |
|---|---|---|---|---|---|---|
| mom_12_1 | +0.4% | +13.1% | +6.2% | +5.5% | +0.524 | +0.0628 |
| ret_1d | -23.3% | -7.5% | -3.7% | -5.4% | +0.390 | -0.0453 |
| dist_ma200 | +0.5% | -13.6% | -6.1% | -5.2% | +0.471 | -0.0629 |
| vix | +0.0% | +3.6% | +3.2% | +4.7% | -0.104 | +0.0199 |
| mr_opportunity | +4.9% | -6.2% | -3.4% | -3.4% | -0.065 | +0.0290 |
| dollar_mom_3m | +0.0% | +3.1% | +3.4% | +3.0% | -0.104 | -0.0687 |
| ma_cross | +0.4% | +2.4% | +3.2% | +2.9% | +0.576 | +0.0367 |
| ret_1m | +0.0% | +5.0% | +2.9% | +2.9% | -0.107 | +0.0049 |
| sharpe_252 | +0.3% | +6.0% | +3.0% | +2.8% | +0.284 | +0.0309 |
| ret_3m | +0.0% | -0.8% | -1.8% | -2.8% | +0.005 | -0.0115 |
| ret_12m | +0.3% | -0.5% | -3.8% | -2.7% | +0.470 | -0.0308 |
| vol_pctile | +0.0% | +4.0% | +3.0% | +2.7% | +0.039 | +0.0050 |

**XLK — 3M** · production score +12.2 · C +2.9 · p_up 65.0% (prior 63.2%) · C's hierarchy source: Global 100% · path: Global → Equity → sector ETF → Information Technology → XLK

| Signal | Production | C validated | Current value | Contribution |
|---|---|---|---|---|
| fund_quality | +0.0% | -32.6% | +0.000 | +0.0020 |
| earnings_yield | +0.0% | -20.2% | +0.000 | -0.0042 |
| mom_12_1 | +1.2% | +20.2% | +0.650 | +0.0105 |
| ma_cross | +3.8% | +12.3% | +0.758 | +0.0099 |
| ret_6m | +7.9% | +4.8% | +0.579 | +0.0030 |
| downside_vol_60 | +0.0% | -4.4% | +0.267 | -0.0020 |
| ewma_vol | +0.0% | -3.7% | +0.635 | -0.0033 |
| drawdown_252 | +0.0% | +1.8% | +0.085 | +0.0001 |
| ret_3m | +4.2% | +0.0% | +1.000 | +0.0000 |
| ret_12m | +0.9% | +0.0% | +0.630 | +0.0000 |
| ret_1m | +0.0% | +0.0% | +0.057 | +0.0000 |
| dist_ma200 | +3.7% | +0.0% | +0.665 | +0.0000 |

**SPY — 1W** · production score +9.0 · C -1.1 · D (simple global, live shadow) -8.1 from Global · E (uniform-depth hierarchy, live shadow) -4.3 from SPY · p_up 57.6% (prior 57.0%) · C's hierarchy source: Global 94%, Class 2%, Product type 5% · path: Global → Equity → broad equity ETF → SPY

| Signal | Production | C validated | D global (live) | E hierarchy (live) | Current value | Contribution |
|---|---|---|---|---|---|---|
| ret_1d | -19.7% | -7.0% | -3.7% | -5.7% | +0.346 | -0.0440 |
| mom_12_1 | +2.0% | +12.3% | +6.2% | +5.3% | +0.280 | +0.0389 |
| dist_ma200 | +0.6% | -12.8% | -6.1% | -5.2% | +0.200 | -0.0386 |
| vix | +0.0% | +3.4% | +3.2% | +5.0% | -0.104 | +0.0216 |
| mr_opportunity | +7.5% | -5.8% | -3.4% | -3.1% | +0.243 | +0.0116 |
| ret_12m | +1.1% | -0.4% | -3.8% | -2.8% | +0.207 | -0.0197 |
| ma_cross | +1.2% | +2.2% | +3.2% | +2.8% | +0.291 | +0.0216 |
| sharpe_252 | +1.0% | +5.6% | +3.0% | +2.7% | +0.179 | +0.0256 |
| ret_1m | +0.0% | +4.6% | +2.9% | +2.6% | -0.209 | -0.0005 |
| vol_pctile | +0.0% | +3.8% | +3.0% | +2.6% | -0.619 | -0.0254 |
| slope_10y3m | +0.0% | -1.8% | -1.3% | -2.6% | -0.168 | -0.0255 |
| dollar_mom_3m | +0.0% | +2.9% | +3.4% | +2.6% | -0.104 | -0.0612 |

**SPY — 3M** · production score +9.9 · C +1.5 · p_up 68.6% (prior 66.9%) · C's hierarchy source: Global 100% · path: Global → Equity → broad equity ETF → SPY

| Signal | Production | C validated | Current value | Contribution |
|---|---|---|---|---|
| fund_quality | +0.0% | -32.6% | +0.000 | +0.0020 |
| earnings_yield | +0.0% | -20.2% | +0.000 | -0.0042 |
| mom_12_1 | +6.7% | +20.2% | +0.427 | +0.0050 |
| ma_cross | +8.0% | +12.3% | +0.298 | +0.0028 |
| ret_6m | +14.8% | +4.8% | +0.132 | +0.0003 |
| downside_vol_60 | +0.0% | -4.4% | -0.207 | +0.0014 |
| ewma_vol | +0.0% | -3.7% | -0.015 | +0.0007 |
| drawdown_252 | +0.0% | +1.8% | +0.140 | +0.0002 |
| ret_3m | +6.8% | +0.0% | +0.616 | +0.0000 |
| ret_12m | +5.0% | +0.0% | +0.337 | +0.0000 |
| ret_1m | +0.0% | +0.0% | -0.246 | -0.0000 |
| dist_ma200 | +8.3% | +0.0% | +0.175 | +0.0000 |

**UST10Y — 1W** · production score -2.2 · C -2.2 · D (simple global, live shadow) -6.7 from Global · E (uniform-depth hierarchy, live shadow) -18.1 from UST10Y · p_up 57.6% (prior 56.5%) · C's hierarchy source: Global 77%, Class 19%, Product type 3%, Sector 1% · path: Global → Rates → Treasury → intermediate → UST10Y

| Signal | Production | C validated | D global (live) | E hierarchy (live) | Current value | Contribution |
|---|---|---|---|---|---|---|
| ret_1d | -11.4% | -6.1% | -3.7% | -5.5% | -0.083 | +0.0024 |
| mom_12_1 | +0.5% | +10.6% | +6.2% | +4.9% | -0.357 | -0.0178 |
| dist_ma200 | +1.1% | -11.0% | -6.1% | -4.7% | -0.595 | +0.0317 |
| y10 | +0.0% | +7.5% | +1.9% | +4.6% | +0.307 | -0.0387 |
| vol_pctile | +0.0% | +3.3% | +3.0% | +4.2% | -0.347 | -0.0200 |
| mr_opportunity | +0.0% | -5.0% | -3.4% | -4.0% | +1.000 | -0.0325 |
| dollar_mom_3m | +0.0% | +2.5% | +3.4% | +3.8% | -0.104 | -0.0872 |
| ret_12m | +0.8% | -0.4% | -3.8% | -3.1% | -0.491 | +0.0158 |
| sharpe_252 | +0.1% | +4.9% | +3.0% | +2.9% | -0.767 | -0.0149 |
| sortino_252 | +0.1% | -5.7% | -3.5% | -2.7% | -0.745 | +0.0136 |
| excess_3m | +30.6% | +0.0% | +1.3% | +2.7% | -0.295 | -0.0139 |
| ret_1m | +0.0% | +4.0% | +2.9% | +2.5% | -0.524 | -0.0145 |

**UST10Y — 3M** · production score -2.8 · C -2.6 · p_up 68.2% (prior 66.8%) · C's hierarchy source: Global 100% · path: Global → Rates → Treasury → intermediate → UST10Y

| Signal | Production | C validated | Current value | Contribution |
|---|---|---|---|---|
| fund_quality | +0.0% | -32.6% | +0.000 | +0.0020 |
| earnings_yield | +0.0% | -20.2% | +0.000 | -0.0042 |
| mom_12_1 | +0.4% | +20.2% | -0.122 | -0.0085 |
| ma_cross | +0.2% | +12.3% | -0.302 | -0.0064 |
| ret_6m | +0.2% | +4.8% | -0.182 | -0.0017 |
| downside_vol_60 | +0.0% | -4.4% | -0.344 | +0.0024 |
| ewma_vol | +0.0% | -3.7% | -0.186 | +0.0017 |
| drawdown_252 | +0.0% | +1.8% | +0.235 | +0.0004 |
| ret_3m | +1.7% | +0.0% | -0.013 | -0.0000 |
| ret_12m | +1.5% | +0.0% | -0.030 | -0.0000 |
| ret_1m | +0.0% | +0.0% | +0.296 | +0.0000 |
| dist_ma200 | +1.2% | +0.0% | -0.183 | -0.0000 |

**TLT — 1W** · production score +2.7 · C -3.0 · D (simple global, live shadow) -2.0 from Global · E (uniform-depth hierarchy, live shadow) -3.2 from TLT · p_up 55.4% (prior 54.8%) · C's hierarchy source: Global 78%, Class 19%, Product type 3% · path: Global → Rates → Treasury → long → TLT

| Signal | Production | C validated | D global (live) | E hierarchy (live) | Current value | Contribution |
|---|---|---|---|---|---|---|
| ret_1d | -41.6% | -6.1% | -3.7% | -5.1% | +0.110 | -0.0154 |
| dist_ma200 | +0.1% | -11.1% | -6.1% | -5.0% | -0.414 | +0.0173 |
| mom_12_1 | +0.1% | +10.7% | +6.2% | +4.7% | -0.387 | -0.0189 |
| y10 | +0.0% | +7.6% | +1.9% | +4.4% | +0.322 | +0.0000 |
| vol_pctile | +0.0% | +3.3% | +3.0% | +3.9% | -0.330 | -0.0172 |
| ret_12m | +0.1% | -0.4% | -3.8% | -3.6% | -0.392 | +0.0119 |
| mr_opportunity | +0.0% | -5.0% | -3.4% | -3.6% | +0.701 | -0.0119 |
| dollar_mom_3m | +0.0% | +2.5% | +3.4% | +3.4% | +0.092 | +0.0000 |
| sortino_252 | +0.0% | -5.8% | -3.5% | -2.9% | -0.723 | +0.0135 |
| sharpe_252 | +0.0% | +4.9% | +3.0% | +2.7% | -0.740 | -0.0128 |
| excess_3m | +1.2% | +0.0% | +1.3% | +2.6% | -0.185 | -0.0085 |
| rsi_14 | +0.0% | -3.8% | -2.4% | -2.5% | -0.578 | +0.0059 |

**TLT — 3M** · production score +1.0 · C +0.4 · p_up 65.1% (prior 64.0%) · C's hierarchy source: Global 100% · path: Global → Rates → Treasury → long → TLT

| Signal | Production | C validated | Current value | Contribution |
|---|---|---|---|---|
| fund_quality | +0.0% | -32.6% | +0.000 | +0.0013 |
| earnings_yield | +0.0% | -20.2% | +0.000 | -0.0007 |
| mom_12_1 | +0.0% | +20.2% | -0.093 | -0.0030 |
| ma_cross | +3.5% | +12.3% | -0.234 | -0.0019 |
| ret_6m | +0.0% | +4.8% | -0.077 | +0.0000 |
| downside_vol_60 | +0.0% | -4.4% | -0.490 | +0.0031 |
| ewma_vol | +0.0% | -3.7% | -0.445 | +0.0025 |
| drawdown_252 | +0.0% | +1.8% | +0.307 | +0.0006 |
| ret_3m | +0.0% | +0.0% | +0.047 | -0.0000 |
| ret_12m | +2.8% | +0.0% | +0.019 | -0.0000 |
| ret_1m | +0.0% | +0.0% | +0.349 | +0.0000 |
| dist_ma200 | +0.0% | +0.0% | -0.120 | +0.0000 |

**HYG — 1W** · production score +5.3 · C -2.0 · D (simple global, live shadow) -6.5 from Global · E (uniform-depth hierarchy, live shadow) -7.7 from HYG · p_up 62.0% (prior 60.4%) · C's hierarchy source: Global 95%, Class 5% · path: Global → Credit → high yield → HYG

| Signal | Production | C validated | D global (live) | E hierarchy (live) | Current value | Contribution |
|---|---|---|---|---|---|---|
| mom_12_1 | +4.3% | +12.8% | +6.2% | +6.0% | -0.038 | +0.0103 |
| dist_ma200 | +0.0% | -13.3% | -6.1% | -4.9% | -0.129 | -0.0068 |
| dollar_mom_3m | +0.0% | +3.0% | +3.4% | +4.0% | -0.104 | -0.0876 |
| ret_1d | -4.0% | -7.3% | -3.7% | -3.9% | -0.033 | -0.0018 |
| vix | +0.0% | +3.5% | +3.2% | +3.8% | -0.104 | +0.0152 |
| sortino_252 | +0.2% | -6.9% | -3.5% | -3.5% | -0.452 | +0.0020 |
| ma_cross | +2.0% | +2.3% | +3.2% | +3.5% | -0.060 | +0.0046 |
| ret_1w | -15.8% | -0.9% | -1.8% | -3.4% | -0.288 | -0.0004 |
| y10 | +0.0% | +0.0% | +1.9% | +3.3% | +0.307 | -0.0263 |
| ret_12m | +0.7% | -0.5% | -3.8% | -3.0% | -0.097 | -0.0045 |
| vol_pctile | +0.0% | +3.9% | +3.0% | +2.9% | -0.497 | -0.0207 |
| rsi_14 | -18.4% | -4.6% | -2.4% | -2.4% | -0.846 | +0.0149 |

**HYG — 3M** · production score -2.0 · C -0.9 · p_up 70.4% (prior 68.8%) · C's hierarchy source: Global 100% · path: Global → Credit → high yield → HYG

| Signal | Production | C validated | Current value | Contribution |
|---|---|---|---|---|
| fund_quality | +0.0% | -32.6% | +0.000 | +0.0020 |
| earnings_yield | +0.0% | -20.2% | +0.000 | -0.0042 |
| mom_12_1 | +1.7% | +20.2% | +0.040 | -0.0045 |
| ma_cross | +4.7% | +12.3% | -0.031 | -0.0023 |
| ret_6m | +10.4% | +4.8% | -0.042 | -0.0008 |
| downside_vol_60 | +0.0% | -4.4% | -0.306 | +0.0021 |
| ewma_vol | +0.0% | -3.7% | -0.276 | +0.0023 |
| drawdown_252 | +0.0% | +1.8% | +0.264 | +0.0005 |
| ret_3m | +15.3% | +0.0% | +0.118 | -0.0000 |
| ret_12m | +2.2% | +0.0% | +0.039 | -0.0000 |
| ret_1m | +0.0% | +0.0% | +0.006 | +0.0000 |
| dist_ma200 | +7.2% | +0.0% | -0.034 | -0.0000 |

**WTI — 1W** · production score +2.5 · C -0.2 · D (simple global, live shadow) -6.3 from Global · E (uniform-depth hierarchy, live shadow) +13.3 from WTI · p_up 51.7% (prior 51.9%) · C's hierarchy source: Global 76%, Class 11%, Product type 1%, Sector 6%, Industry 7% · path: Global → Commodity → commodity → Energy → Oil → WTI

| Signal | Production | C validated | D global (live) | E hierarchy (live) | Current value | Contribution |
|---|---|---|---|---|---|---|
| oil_mom_3m | +0.0% | -18.8% | -0.7% | -7.0% | +0.401 | +0.2199 |
| dist_ma200 | +1.6% | -10.4% | -6.1% | -6.5% | +0.575 | -0.0846 |
| mom_12_1 | +0.0% | +10.0% | +6.2% | +4.7% | +0.335 | +0.0357 |
| ret_12m | +0.3% | -0.4% | -3.8% | -4.1% | +0.630 | -0.0537 |
| sharpe_252 | +1.2% | +4.6% | +3.0% | +3.4% | +0.385 | +0.0396 |
| dollar_mom_3m | +0.0% | +2.3% | +3.4% | +3.1% | -0.045 | -0.0474 |
| breakeven_10y | +0.0% | -1.8% | -1.8% | -3.1% | +0.383 | +0.0073 |
| ram | +0.3% | +3.1% | +2.3% | +2.6% | +0.186 | +0.0164 |
| ewma_vol | +0.0% | +3.1% | +1.9% | +2.5% | +0.227 | +0.0217 |
| sortino_252 | +1.1% | -5.4% | -3.5% | -2.3% | +0.355 | -0.0259 |
| z_50 | +0.0% | +1.1% | +1.9% | +2.3% | +0.809 | +0.0380 |
| half_life | +0.0% | +0.4% | +0.8% | +2.1% | +0.013 | +0.0098 |

**WTI — 3M** · production score -16.7 · C +4.2 · p_up 57.4% (prior 56.4%) · C's hierarchy source: Global 100% · path: Global → Commodity → commodity → Energy → Oil → WTI

| Signal | Production | C validated | Current value | Contribution |
|---|---|---|---|---|
| fund_quality | +0.0% | -32.6% | +0.000 | +0.0050 |
| earnings_yield | +0.0% | -20.2% | +0.000 | +0.0009 |
| mom_12_1 | +0.0% | +20.2% | +0.554 | +0.0098 |
| ma_cross | +0.0% | +12.3% | +0.974 | +0.0133 |
| ret_6m | +0.0% | +4.8% | +0.510 | +0.0031 |
| downside_vol_60 | +0.0% | -4.4% | +0.976 | -0.0061 |
| ewma_vol | +0.0% | -3.7% | +0.547 | -0.0024 |
| drawdown_252 | +0.0% | +1.8% | -0.429 | -0.0007 |
| ret_3m | +0.3% | +0.0% | -0.561 | -0.0000 |
| ret_12m | +0.0% | +0.0% | +0.045 | -0.0000 |
| ret_1m | +0.0% | +0.0% | -1.000 | -0.0000 |
| dist_ma200 | +0.0% | +0.0% | +0.090 | +0.0000 |

**GOLD — 1W** · production score -0.1 · C +6.5 · D (simple global, live shadow) -0.3 from Global · E (uniform-depth hierarchy, live shadow) +0.2 from GOLD · p_up 52.0% (prior 51.9%) · C's hierarchy source: Global 74%, Class 10%, Product type 2%, Sector 9%, Industry 4%, Asset 1% · path: Global → Commodity → commodity → Precious Metals → Gold → GOLD

| Signal | Production | C validated | D global (live) | E hierarchy (live) | Current value | Contribution |
|---|---|---|---|---|---|---|
| dist_ma200 | +0.0% | -10.0% | -6.1% | -6.4% | -0.550 | +0.0391 |
| gold_mom_3m | +0.0% | -20.0% | -0.3% | -5.9% | -0.003 | -0.0719 |
| mom_12_1 | +21.7% | +9.7% | +6.2% | +4.8% | +0.278 | +0.0352 |
| ret_12m | +3.7% | -0.3% | -3.8% | -4.0% | +0.183 | -0.0260 |
| dollar_mom_3m | +0.0% | +2.3% | +3.4% | +3.6% | -0.045 | -0.0594 |
| ram | +29.5% | +3.0% | +2.3% | +3.3% | +0.124 | +0.0196 |
| half_life | +0.0% | +0.3% | +0.8% | +3.3% | -0.151 | +0.0002 |
| oil_mom_3m | +0.0% | -6.4% | -0.7% | -3.1% | +0.401 | +0.1052 |
| sharpe_252 | +2.3% | +4.4% | +3.0% | +3.0% | -0.055 | +0.0171 |
| breakeven_10y | +0.0% | -1.7% | -1.8% | -2.9% | +0.383 | +0.0073 |
| acf1_252 | +0.0% | +0.0% | +0.6% | +2.8% | +0.034 | +0.0072 |
| corr_252 | +0.0% | +0.0% | +0.7% | +2.8% | +0.870 | +0.0273 |

**GOLD — 3M** · production score -3.8 · C +0.7 · p_up 57.5% (prior 56.4%) · C's hierarchy source: Global 100% · path: Global → Commodity → commodity → Precious Metals → Gold → GOLD

| Signal | Production | C validated | Current value | Contribution |
|---|---|---|---|---|
| fund_quality | +0.0% | -32.6% | +0.000 | +0.0050 |
| earnings_yield | +0.0% | -20.2% | +0.000 | +0.0009 |
| mom_12_1 | +7.4% | +20.2% | +0.630 | +0.0116 |
| ma_cross | +0.5% | +12.3% | -0.038 | -0.0023 |
| ret_6m | +1.0% | +4.8% | -0.212 | -0.0014 |
| downside_vol_60 | +0.0% | -4.4% | +0.735 | -0.0043 |
| ewma_vol | +0.0% | -3.7% | +0.826 | -0.0041 |
| drawdown_252 | +0.0% | +1.8% | -0.857 | -0.0016 |
| ret_3m | +8.5% | +0.0% | -0.916 | -0.0000 |
| ret_12m | +8.3% | +0.0% | +0.440 | +0.0000 |
| ret_1m | +0.0% | +0.0% | -0.508 | -0.0000 |
| dist_ma200 | +2.0% | +0.0% | -0.377 | -0.0000 |

**EURUSD — 1W** · production score +0.2 · C -3.2 · D (simple global, live shadow) -6.5 from Global · E (uniform-depth hierarchy, live shadow) -7.0 from EURUSD · p_up 51.5% (prior 51.9%) · C's hierarchy source: Global 94%, Class 6% · path: Global → FX → major FX → EURUSD

| Signal | Production | C validated | D global (live) | E hierarchy (live) | Current value | Contribution |
|---|---|---|---|---|---|---|
| dist_ma200 | +3.7% | -13.7% | -6.1% | -5.9% | -0.020 | -0.0171 |
| mom_12_1 | +0.6% | +13.2% | +6.2% | +5.8% | -0.074 | +0.0058 |
| ret_12m | +0.6% | -0.5% | -3.8% | -4.1% | -0.038 | -0.0091 |
| dollar_mom_3m | +0.0% | +3.1% | +3.4% | +4.0% | -0.045 | -0.0557 |
| sortino_252 | +0.0% | -7.1% | -3.5% | -4.0% | -0.418 | +0.0002 |
| vol_pctile | +0.0% | +4.1% | +3.0% | +3.5% | -0.443 | -0.0195 |
| ma_cross | +3.6% | +2.4% | +3.2% | +3.3% | -0.122 | +0.0008 |
| rsi_14 | -0.8% | -4.8% | -2.4% | -2.9% | -0.008 | -0.0154 |
| mr_opportunity | +3.5% | -6.2% | -3.4% | -2.7% | +0.526 | -0.0016 |
| ret_1m | +0.0% | +5.0% | +2.9% | +2.5% | +0.110 | +0.0119 |
| sharpe_252 | +0.0% | +6.1% | +3.0% | +2.3% | -0.356 | +0.0021 |
| d_credit_3m | +0.0% | +0.0% | -1.8% | -2.3% | -0.058 | -0.0000 |

**EURUSD — 3M** · production score -1.6 · C +1.6 · p_up 57.1% (prior 56.4%) · C's hierarchy source: Global 100% · path: Global → FX → major FX → EURUSD

| Signal | Production | C validated | Current value | Contribution |
|---|---|---|---|---|
| fund_quality | +0.0% | -32.6% | +0.000 | +0.0050 |
| earnings_yield | +0.0% | -20.2% | +0.000 | +0.0009 |
| mom_12_1 | +0.0% | +20.2% | +0.043 | -0.0029 |
| ma_cross | +1.1% | +12.3% | +0.002 | -0.0017 |
| ret_6m | +12.8% | +4.8% | -0.076 | -0.0005 |
| downside_vol_60 | +0.0% | -4.4% | -0.350 | +0.0036 |
| ewma_vol | +0.0% | -3.7% | -0.394 | +0.0034 |
| drawdown_252 | +0.0% | +1.8% | +0.251 | +0.0009 |
| ret_3m | +31.6% | +0.0% | +0.076 | +0.0000 |
| ret_12m | +0.0% | +0.0% | +0.042 | -0.0000 |
| ret_1m | +0.0% | +0.0% | -0.005 | +0.0000 |
| dist_ma200 | +5.7% | +0.0% | -0.046 | -0.0000 |

**BTC — 1W** · production score -10.5 · C -4.9 · D (simple global, live shadow) -2.2 from Global · E (uniform-depth hierarchy, live shadow) -1.8 from BTC · p_up 58.0% (prior 56.9%) · C's hierarchy source: Global 100% · path: Global → Crypto → crypto → Bitcoin → BTC

| Signal | Production | C validated | D global (live) | E hierarchy (live) | Current value | Contribution |
|---|---|---|---|---|---|---|
| mom_12_1 | +0.2% | +13.1% | +6.2% | +5.7% | -0.655 | -0.0445 |
| dist_ma200 | +1.0% | -13.6% | -6.1% | -5.4% | -0.157 | -0.0046 |
| vix | +0.0% | +3.6% | +3.2% | +3.6% | -0.145 | +0.0000 |
| ret_12m | +0.3% | -0.5% | -3.8% | -3.5% | -0.555 | +0.0195 |
| sortino_252 | +0.1% | -7.1% | -3.5% | -3.5% | -0.694 | +0.0136 |
| ret_1m | +0.0% | +5.0% | +2.9% | +3.4% | +0.312 | +0.0281 |
| vol_pctile | +0.0% | +4.0% | +3.0% | +3.3% | +0.350 | +0.0213 |
| ma_cross | +0.2% | +2.4% | +3.2% | +3.2% | -0.327 | -0.0095 |
| mr_opportunity | +0.0% | -6.2% | -3.4% | -3.1% | +0.262 | +0.0094 |
| ret_1d | +0.0% | -7.5% | -3.7% | -3.0% | +0.065 | -0.0062 |
| dollar_mom_3m | +0.0% | +3.1% | +3.4% | +2.9% | +0.092 | +0.0000 |
| sharpe_252 | +0.1% | +6.0% | +3.0% | +2.5% | -0.708 | -0.0098 |

**BTC — 3M** · production score -6.6 · C -3.0 · p_up 59.8% (prior 59.2%) · C's hierarchy source: Global 100% · path: Global → Crypto → crypto → Bitcoin → BTC

| Signal | Production | C validated | Current value | Contribution |
|---|---|---|---|---|
| fund_quality | +0.0% | -32.6% | +0.000 | +0.0013 |
| earnings_yield | +0.0% | -20.2% | +0.000 | -0.0007 |
| mom_12_1 | +1.0% | +20.2% | -0.500 | -0.0131 |
| ma_cross | +1.8% | +12.3% | -0.462 | -0.0054 |
| ret_6m | +7.1% | +4.8% | -0.471 | -0.0024 |
| downside_vol_60 | +0.0% | -4.4% | -0.417 | +0.0026 |
| ewma_vol | +0.0% | -3.7% | -0.439 | +0.0025 |
| drawdown_252 | +0.0% | +1.8% | -0.481 | -0.0013 |
| ret_3m | +15.1% | +0.0% | -0.273 | -0.0000 |
| ret_12m | +2.3% | +0.0% | -0.620 | -0.0000 |
| ret_1m | +0.0% | +0.0% | -0.550 | -0.0000 |
| dist_ma200 | +5.7% | +0.0% | -0.584 | -0.0000 |

## 8. Every asset × horizon

Learned score = the validated set C. Live-shadow scores = the learned weight sets that passed every gate (1W: D global, E hierarchy). Live shadow? = the asset is scored daily by a learned set in live shadow.

| Asset | Horizon | Production score | Learned score (C) | Live-shadow scores | p_up (prior) | Best hierarchy depth | Alpha reliability (OOS corr, t) | Directional reliability (Brier gain, t) | Live shadow? |
|---|---|---|---|---|---|---|---|---|---|
| AAPL | 1D | +0.0 | -0.5 | — | 52.4% (51.7%) | Global | -0.031 (-0.9) | -0.0003 (-1.3) | no |
| AAPL | 1W | +0.1 | +2.3 | D -6.5 / E -1.0 | 52.0% (52.5%) | Global | -0.022 (-0.6) | -0.0000 (-0.1) | yes |
| AAPL | 1M | -2.9 | +2.6 | — | 54.9% (55.3%) | Sector | -0.088 (-1.3) | +0.0017 (+1.1) | no |
| AAPL | 3M | +0.1 | +0.2 | — | 63.2% (61.8%) | Class | -0.134 (-1.1) | +0.0030 (+0.7) | no |
| AAPL | 6M | -1.4 | +4.2 | — | 70.0% (68.3%) | Class | -0.232 (-1.4) | +0.0064 (+0.9) | no |
| AAPL | 12M | -1.3 | -6.7 | — | 70.7% (72.9%) | Global | -0.099 (-0.4) | -0.0027 (-0.2) | no |
| ABBV | 1D | -0.1 | +0.7 | — | 51.5% (50.8%) | Global | +0.066 (+1.3) | +0.0002 (+0.5) | no |
| ABBV | 1W | -1.5 | +0.4 | D -2.1 / E +1.8 | 50.1% (50.9%) | Global | +0.005 (+0.1) | -0.0009 (-1.8) | yes |
| ABBV | 1M | -2.9 | -3.2 | — | 51.5% (52.0%) | Global | +0.053 (+0.5) | +0.0009 (+0.4) | no |
| ABBV | 3M | -3.4 | +3.8 | — | 55.4% (54.7%) | Global | -0.027 (-0.2) | -0.0014 (-0.3) | no |
| ABBV | 6M | -3.5 | +4.5 | — | 61.5% (59.2%) | Global | +0.066 (+0.2) | +0.0069 (+0.8) | no |
| ADBE | 1D | +4.1 | -1.4 | — | 51.9% (50.9%) | Global | +0.045 (+1.4) | +0.0002 (+0.9) | no |
| ADBE | 1W | +8.1 | -4.1 | D -5.0 / E -0.7 | 51.1% (51.1%) | Class | +0.063 (+1.9) | +0.0005 (+1.1) | yes |
| ADBE | 1M | -6.2 | +2.5 | — | 51.3% (51.9%) | Global | -0.026 (-0.4) | +0.0018 (+1.2) | no |
| ADBE | 3M | -19.6 | -25.8 | — | 55.1% (54.8%) | Class | +0.262 (+2.2) | +0.0041 (+1.4) | no |
| ADBE | 6M | -14.2 | -17.7 | — | 60.9% (59.2%) | Class | +0.157 (+0.9) | +0.0068 (+1.1) | no |
| ADBE | 12M | -12.7 | -14.8 | — | 72.1% (69.8%) | Class | +0.117 (+0.5) | +0.0154 (+2.2) | no |
| AGG | 1D | -3.3 | -0.3 | — | 55.9% (55.1%) | Product type | +0.150 (+4.5) | -0.0004 (-1.2) | no |
| AGG | 1W | +5.6 | -0.0 | D -1.9 / E -7.2 | 59.9% (58.4%) | Class | +0.135 (+4.1) | -0.0001 (-0.2) | yes |
| AGG | 1M | -2.8 | +2.1 | — | 66.7% (66.5%) | Global | -0.022 (-0.3) | -0.0009 (-0.7) | no |
| AGG | 3M | -1.5 | +0.4 | — | 71.2% (69.7%) | Global | +0.086 (+0.7) | -0.0025 (-0.8) | no |
| AGG | 6M | -0.4 | +0.8 | — | 78.4% (77.4%) | Global | +0.025 (+0.1) | -0.0064 (-0.9) | no |
| AGG | 12M | +2.0 | -4.7 | — | 79.3% (78.1%) | Global | -0.485 (-1.5) | +0.0038 (+0.4) | no |
| AMD | 1D | +24.2 | +0.2 | — | 52.7% (52.0%) | Global | -0.049 (-1.4) | -0.0003 (-1.0) | no |
| AMD | 1W | +3.6 | +4.1 | D -9.0 / E +2.2 | 52.4% (52.9%) | Product type | -0.037 (-1.1) | -0.0003 (-0.9) | yes |
| AMD | 1M | +4.1 | -3.8 | — | 54.9% (55.3%) | Product type | -0.069 (-1.0) | +0.0000 (+0.0) | no |
| AMD | 3M | +25.8 | +3.5 | — | 58.7% (57.0%) | Product type | +0.006 (+0.1) | -0.0003 (-0.0) | no |
| AMD | 6M | +1.0 | -2.8 | — | 61.7% (59.5%) | Product type | -0.072 (-0.4) | +0.0004 (+0.0) | no |
| AMD | 12M | +0.7 | -0.5 | — | 69.5% (69.0%) | Product type | +0.146 (+0.6) | -0.0074 (-0.4) | no |
| AMZN | 1D | +0.1 | -2.4 | — | 52.7% (52.0%) | Global | -0.011 (-0.3) | -0.0003 (-1.3) | no |
| AMZN | 1W | +0.3 | -4.5 | D -10.1 / E -3.2 | 52.9% (53.0%) | Sector | -0.031 (-0.9) | -0.0001 (-0.2) | yes |
| AMZN | 1M | +0.2 | +1.1 | — | 56.0% (56.4%) | Product type | -0.036 (-0.5) | +0.0023 (+1.5) | no |
| AMZN | 3M | -1.3 | -15.7 | — | 63.5% (62.3%) | Class | +0.271 (+2.3) | +0.0033 (+0.9) | no |
| AMZN | 6M | -5.0 | -10.8 | — | 67.2% (65.8%) | Global | +0.151 (+0.9) | +0.0052 (+0.7) | no |
| AMZN | 12M | -0.3 | -3.6 | — | 74.3% (70.8%) | Global | +0.145 (+0.5) | +0.0106 (+0.9) | no |
| AUDUSD | 1D | +1.6 | +2.5 | — | 51.9% (51.4%) | Global | +0.064 (+1.8) | +0.0000 (+0.1) | no |
| AUDUSD | 1W | +0.2 | -0.7 | D -11.8 / E -14.5 | 51.2% (51.9%) | Global | +0.146 (+4.0) | -0.0010 (-2.6) | yes |
| AUDUSD | 1M | -0.7 | -0.8 | — | 53.6% (54.1%) | Global | -0.043 (-0.6) | -0.0054 (-2.5) | no |
| AUDUSD | 3M | -20.3 | +3.1 | — | 57.5% (56.4%) | Global | -0.103 (-0.8) | -0.0157 (-2.1) | no |
| AUDUSD | 6M | +1.7 | +2.1 | — | 59.9% (57.6%) | Global | -0.104 (-0.5) | -0.0355 (-2.2) | no |
| AUDUSD | 12M | +0.3 | -1.1 | — | 63.4% (63.6%) | Global | -0.101 (-0.2) | +0.0113 (+1.9) | no |
| AVGO | 1D | -3.3 | -0.2 | — | 53.6% (52.8%) | Global | +0.081 (+2.0) | +0.0003 (+1.0) | no |
| AVGO | 1W | +24.7 | +2.3 | D -1.2 / E +7.5 | 54.8% (54.0%) | Global | +0.104 (+2.8) | -0.0003 (-0.6) | yes |
| AVGO | 1M | +11.2 | +4.8 | — | 55.9% (56.3%) | Global | +0.147 (+1.9) | +0.0024 (+1.3) | no |
| AVGO | 3M | +0.2 | +2.3 | — | 60.4% (59.4%) | Global | -0.136 (-1.0) | +0.0097 (+2.5) | no |
| AVGO | 6M | -2.9 | -4.3 | — | 67.0% (65.0%) | Global | +0.087 (+0.4) | +0.0052 (+0.6) | no |
| AVGO | 12M | +10.9 | +2.5 | — | 69.6% (69.8%) | Global | -0.507 (-0.7) | -0.0041 (-1.0) | no |
| BA | 1D | -0.1 | -1.5 | — | 53.0% (52.3%) | Global | +0.004 (+0.1) | -0.0000 (-0.2) | no |
| BA | 1W | -0.9 | -5.2 | D -12.2 / E -4.9 | 53.8% (53.4%) | Global | -0.032 (-1.0) | +0.0001 (+0.1) | yes |
| BA | 1M | -7.1 | +2.6 | — | 56.0% (56.4%) | Global | -0.035 (-0.5) | +0.0007 (+0.4) | no |
| BA | 3M | -3.4 | +0.1 | — | 60.2% (59.2%) | Class | +0.102 (+0.8) | +0.0000 (+0.0) | no |
| BA | 6M | -7.1 | -2.8 | — | 66.3% (64.5%) | Class | -0.057 (-0.3) | -0.0025 (-0.4) | no |
| BA | 12M | +5.1 | +3.3 | — | 67.9% (70.2%) | Global | +0.029 (+0.1) | +0.0047 (+0.3) | no |
| BAC | 1D | +0.2 | +1.9 | — | 52.9% (52.3%) | Global | +0.086 (+2.6) | +0.0001 (+0.7) | no |
| BAC | 1W | +0.5 | +0.0 | D -7.6 / E -2.1 | 53.8% (54.1%) | Product type | +0.038 (+1.1) | -0.0004 (-0.8) | yes |
| BAC | 1M | +2.8 | +2.8 | — | 58.0% (58.3%) | Global | +0.075 (+1.1) | -0.0000 (-0.0) | no |
| BAC | 3M | +3.6 | -5.5 | — | 62.9% (61.6%) | Class | +0.148 (+1.2) | +0.0004 (+0.1) | no |
| BAC | 6M | +1.2 | -4.0 | — | 66.6% (64.9%) | Global | +0.206 (+1.2) | -0.0047 (-0.7) | no |
| BAC | 12M | +11.3 | +0.2 | — | 73.9% (73.2%) | Global | +0.104 (+0.4) | -0.0003 (-0.0) | no |
| BIL | 1D | -7.9 | -99.9 | — | 85.4% (85.0%) | Asset | +0.053 (+0.8) | +0.0021 (+2.2) | no |
| BIL | 1W | +62.2 | -8.9 | D -9.8 / E -28.9 | 99.6% (99.0%) | Asset | +0.097 (+1.7) | +0.0009 (+1.2) | yes |
| BIL | 1M | +46.0 | -0.7 | — | 100.0% (100.0%) | Asset | +0.128 (+1.3) | -0.0038 (-2.2) | no |
| BIL | 3M | +21.4 | +5.3 | — | 100.0% (100.0%) | Global | -0.137 (-0.6) | -0.0535 (-4.1) | no |
| BIL | 6M | +4.9 | +0.6 | — | 96.0% (96.2%) | Global | +0.497 (+1.1) | -0.0805 (-2.5) | no |
| BKLN | 1D | +13.7 | -1.1 | — | 68.8% (68.2%) | Class | +0.115 (+2.3) | -0.0001 (-0.1) | no |
| BKLN | 1W | +1.3 | -2.1 | D -5.4 / E -4.6 | 66.0% (63.8%) | Class | +0.079 (+1.7) | +0.0007 (+0.9) | yes |
| BKLN | 1M | +1.8 | -0.3 | — | 75.5% (75.0%) | Global | +0.014 (+0.2) | +0.0001 (+0.1) | no |
| BKLN | 3M | -4.7 | +1.4 | — | 74.5% (72.9%) | Global | -0.013 (-0.1) | +0.0100 (+1.7) | no |
| BKLN | 6M | -4.8 | -1.7 | — | 75.9% (74.6%) | Global | -0.282 (-1.0) | +0.0047 (+0.8) | no |
| BKLN | 12M | +10.3 | +5.8 | — | 92.1% (90.8%) | Global | +0.136 (+0.1) | +0.0058 (+2.5) | no |
| BND | 1D | -2.6 | -3.7 | — | 56.1% (55.3%) | Global | -0.040 (-1.1) | -0.0001 (-0.2) | no |
| BND | 1W | +4.8 | +3.0 | D -5.7 / E -20.8 | 59.5% (58.1%) | Global | -0.010 (-0.3) | -0.0000 (-0.0) | yes |
| BND | 1M | -2.6 | +1.3 | — | 66.6% (66.5%) | Global | -0.018 (-0.2) | -0.0000 (-0.0) | no |
| BND | 3M | -0.9 | -1.3 | — | 72.7% (71.1%) | Global | +0.047 (+0.4) | -0.0029 (-0.8) | no |
| BND | 6M | -0.1 | -0.1 | — | 80.5% (79.7%) | Global | +0.160 (+0.8) | -0.0065 (-0.8) | no |
| BND | 12M | +0.6 | -1.7 | — | 79.5% (78.3%) | Global | -0.099 (-0.2) | +0.0034 (+1.0) | no |
| BNDX | 1D | -2.0 | -3.3 | — | 50.9% (50.1%) | Asset | +0.235 (+5.2) | +0.0011 (+1.2) | no |
| BNDX | 1W | -8.7 | -2.0 | D -2.9 / E -5.2 | 55.2% (54.4%) | Asset | +0.137 (+3.0) | -0.0004 (-0.6) | yes |
| BNDX | 1M | -4.9 | +2.5 | — | 60.2% (60.4%) | Global | -0.057 (-0.6) | -0.0009 (-0.9) | no |
| BNDX | 3M | -5.3 | -1.5 | — | 64.0% (63.0%) | Global | +0.009 (+0.1) | -0.0019 (-0.7) | no |
| BNDX | 6M | +1.2 | -2.1 | — | 72.2% (70.9%) | Global | -0.249 (-0.8) | -0.0029 (-0.3) | no |
| BRENT | 1D | +1.3 | +2.8 | — | 52.2% (51.4%) | Sector | -0.084 (-2.1) | -0.0005 (-1.4) | no |
| BRENT | 1W | +5.6 | +1.4 | D -5.9 / E +12.1 | 51.7% (51.9%) | Class | +0.023 (+0.6) | -0.0001 (-0.2) | yes |
| BRENT | 1M | +1.8 | -4.7 | — | 53.6% (54.1%) | Global | -0.162 (-1.9) | -0.0022 (-0.9) | no |
| BRENT | 3M | -14.6 | +4.2 | — | 57.5% (56.4%) | Global | -0.160 (-1.1) | -0.0131 (-1.6) | no |
| BRENT | 6M | +9.1 | -0.6 | — | 60.2% (57.6%) | Global | -0.176 (-0.8) | -0.0156 (-1.1) | no |
| BRENT | 12M | -4.2 | -2.9 | — | 63.3% (63.6%) | Global | -0.323 (-0.7) | +0.0053 (+0.7) | no |
| BRK-B | 1D | +11.6 | -0.2 | — | 51.9% (51.2%) | Global | +0.016 (+0.5) | -0.0002 (-1.1) | no |
| BRK-B | 1W | -8.0 | -1.7 | D -9.9 / E -4.8 | 51.2% (51.8%) | Product type | -0.007 (-0.2) | -0.0007 (-1.6) | yes |
| BRK-B | 1M | +4.3 | +4.5 | — | 53.2% (53.7%) | Class | -0.024 (-0.3) | -0.0005 (-0.3) | no |
| BRK-B | 3M | -1.9 | -5.5 | — | 57.3% (56.6%) | Class | +0.051 (+0.4) | -0.0001 (-0.0) | no |
| BRK-B | 6M | -5.7 | -4.8 | — | 64.5% (62.5%) | Product type | +0.123 (+0.7) | +0.0008 (+0.2) | no |
| BRK-B | 12M | -1.2 | -1.1 | — | 71.3% (70.7%) | Product type | -0.011 (-0.0) | +0.0030 (+0.3) | no |
| BTC | 1D | -19.1 | -0.7 | — | 54.5% (53.7%) | Global | +0.054 (+1.1) | -0.0002 (-0.5) | no |
| BTC | 1W | -10.5 | -4.9 | D -2.2 / E -1.8 | 58.0% (56.9%) | Global | +0.076 (+1.5) | -0.0004 (-0.6) | yes |
| BTC | 1M | -4.0 | +2.3 | — | 57.1% (57.4%) | Global | -0.057 (-0.5) | +0.0002 (+0.1) | no |
| BTC | 3M | -6.6 | -3.0 | — | 59.8% (59.2%) | Global | +0.205 (+0.7) | -0.0023 (-0.5) | no |
| BTC | 6M | -5.0 | -5.2 | — | 62.4% (60.5%) | Global | +0.091 (+0.1) | +0.0010 (+0.1) | no |
| CAT | 1D | -0.1 | -1.6 | — | 53.0% (52.3%) | Global | +0.028 (+0.8) | +0.0002 (+0.8) | no |
| CAT | 1W | -1.0 | +6.9 | D -9.2 / E -3.0 | 53.3% (53.5%) | Global | +0.026 (+0.8) | -0.0008 (-2.1) | yes |
| CAT | 1M | -2.5 | -1.1 | — | 56.1% (56.5%) | Global | -0.025 (-0.4) | -0.0003 (-0.2) | no |
| CAT | 3M | -3.5 | +10.0 | — | 61.7% (59.9%) | Global | +0.077 (+0.6) | +0.0002 (+0.1) | no |
| CAT | 6M | +0.5 | +11.0 | — | 65.0% (62.7%) | Global | +0.203 (+1.2) | +0.0007 (+0.1) | no |
| CAT | 12M | -1.3 | +1.1 | — | 71.3% (72.1%) | Product type | -0.048 (-0.2) | -0.0090 (-0.7) | no |
| COFFEE | 1D | +0.3 | +0.5 | — | 52.2% (51.4%) | Sector | +0.030 (+0.9) | -0.0000 (-0.0) | no |
| COFFEE | 1W | +4.4 | -0.5 | D -11.4 / E -1.5 | 52.1% (51.9%) | Class | +0.020 (+0.6) | -0.0008 (-2.3) | yes |
| COFFEE | 1M | -21.6 | -4.4 | — | 53.7% (54.1%) | Global | +0.013 (+0.2) | -0.0041 (-2.2) | no |
| COFFEE | 3M | -1.5 | -5.7 | — | 57.0% (56.4%) | Global | -0.040 (-0.3) | -0.0117 (-1.7) | no |
| COFFEE | 6M | -4.1 | -3.8 | — | 59.9% (57.6%) | Global | +0.163 (+0.9) | -0.0220 (-1.6) | no |
| COFFEE | 12M | +0.9 | +4.0 | — | 63.9% (63.6%) | Global | -0.061 (-0.2) | -0.0243 (-1.0) | no |
| COPPER | 1D | -6.3 | -1.6 | — | 52.0% (51.4%) | Global | +0.061 (+1.8) | -0.0007 (-2.4) | no |
| COPPER | 1W | +2.8 | +5.4 | D -1.1 / E +0.3 | 51.6% (51.9%) | Global | +0.106 (+3.2) | -0.0005 (-1.5) | yes |
| COPPER | 1M | +5.9 | -2.2 | — | 53.6% (54.1%) | Global | +0.055 (+0.8) | -0.0032 (-1.6) | no |
| COPPER | 3M | -9.0 | +3.8 | — | 57.3% (56.4%) | Global | -0.058 (-0.5) | -0.0101 (-1.5) | no |
| COPPER | 6M | +0.6 | +4.5 | — | 60.0% (57.6%) | Global | -0.013 (-0.1) | -0.0241 (-1.8) | no |
| COPPER | 12M | -1.9 | -1.7 | — | 64.3% (63.6%) | Global | +0.051 (+0.2) | -0.0389 (-1.4) | no |
| CORN | 1D | -0.0 | +0.5 | — | 52.1% (51.4%) | Product type | +0.060 (+1.8) | +0.0005 (+1.5) | no |
| CORN | 1W | +0.7 | -2.0 | D -13.0 / E +2.2 | 51.5% (51.9%) | Class | -0.010 (-0.3) | -0.0004 (-1.3) | yes |
| CORN | 1M | -1.0 | -3.4 | — | 53.6% (54.1%) | Global | -0.013 (-0.2) | -0.0017 (-0.9) | no |
| CORN | 3M | -3.6 | -0.4 | — | 57.1% (56.4%) | Global | -0.098 (-0.8) | -0.0088 (-1.3) | no |
| CORN | 6M | -0.5 | +2.4 | — | 59.8% (57.6%) | Global | -0.019 (-0.1) | -0.0238 (-1.8) | no |
| CORN | 12M | -0.1 | -0.7 | — | 62.9% (63.6%) | Global | -0.258 (-0.9) | -0.0444 (-1.5) | no |
| CORP_BAA | 1D | +0.3 | -0.0 | — | 56.1% (55.3%) | Global | +0.074 (+2.2) | +0.0005 (+2.0) | no |
| CORP_BAA | 1W | -16.9 | -3.1 | D -8.2 / E -9.9 | 60.7% (59.0%) | Global | +0.075 (+2.2) | +0.0006 (+1.2) | yes |
| CORP_BAA | 1M | +9.1 | +2.3 | — | 68.4% (68.2%) | Global | +0.052 (+0.7) | +0.0002 (+0.2) | no |
| CORP_BAA | 3M | -2.9 | -1.9 | — | 73.8% (72.2%) | Global | +0.098 (+0.8) | -0.0030 (-1.4) | no |
| CORP_BAA | 6M | -1.5 | -2.4 | — | 81.5% (80.8%) | Global | +0.119 (+0.7) | -0.0038 (-0.7) | no |
| CORP_BAA | 12M | -1.8 | -4.5 | — | 82.2% (80.8%) | Global | -0.336 (-1.4) | +0.0049 (+0.9) | no |
| COST | 1D | +9.6 | -1.0 | — | 51.5% (50.8%) | Global | -0.011 (-0.3) | -0.0003 (-1.6) | no |
| COST | 1W | +15.9 | -2.2 | D -7.0 / E -0.6 | 50.8% (50.9%) | Global | +0.004 (+0.1) | +0.0004 (+0.9) | yes |
| COST | 1M | +18.3 | +8.7 | — | 51.3% (52.0%) | Global | +0.059 (+0.8) | +0.0024 (+1.6) | no |
| COST | 3M | +16.5 | -2.5 | — | 54.7% (54.1%) | Class | -0.011 (-0.1) | +0.0031 (+1.0) | no |
| COST | 6M | +11.1 | +4.5 | — | 62.6% (60.5%) | Class | +0.012 (+0.1) | +0.0068 (+1.1) | no |
| COST | 12M | +10.2 | -3.7 | — | 66.5% (69.4%) | Global | +0.099 (+0.4) | +0.0053 (+0.5) | no |
| CPER | 1D | -11.6 | -1.1 | — | 52.0% (51.4%) | Class | +0.075 (+1.8) | -0.0001 (-0.4) | no |
| CPER | 1W | +6.9 | +7.5 | D +1.1 / E +2.1 | 51.4% (51.9%) | Product type | +0.072 (+1.8) | -0.0008 (-2.2) | yes |
| CPER | 1M | +3.1 | -3.4 | — | 53.6% (54.1%) | Global | +0.078 (+0.9) | -0.0028 (-1.1) | no |
| CPER | 3M | +2.4 | +5.5 | — | 57.4% (56.4%) | Global | -0.074 (-0.5) | -0.0008 (-0.1) | no |
| CPER | 6M | +2.8 | +6.7 | — | 60.0% (57.6%) | Global | -0.057 (-0.2) | -0.0009 (-0.1) | no |
| CPER | 12M | +0.2 | -0.6 | — | 63.7% (63.6%) | Global | -0.289 (-0.3) | -0.0026 (-0.4) | no |
| CRM | 1D | +1.3 | -0.3 | — | 51.7% (50.8%) | Global | +0.080 (+2.4) | +0.0002 (+0.8) | no |
| CRM | 1W | -1.1 | -9.6 | D -14.8 / E -13.2 | 51.3% (50.9%) | Global | +0.143 (+4.2) | -0.0004 (-0.8) | yes |
| CRM | 1M | -3.7 | +2.3 | — | 51.7% (52.3%) | Global | +0.086 (+1.2) | +0.0010 (+0.6) | no |
| CRM | 3M | +0.2 | -25.1 | — | 55.0% (55.0%) | Global | +0.142 (+1.1) | +0.0017 (+0.5) | no |
| CRM | 6M | -2.2 | -14.2 | — | 61.2% (59.5%) | Global | +0.226 (+1.2) | +0.0039 (+0.5) | no |
| CRM | 12M | -1.2 | -9.5 | — | 73.2% (69.8%) | Global | +0.017 (+0.0) | -0.0106 (-1.4) | no |
| CSCO | 1D | +4.0 | +1.0 | — | 52.6% (52.0%) | Global | +0.036 (+1.1) | +0.0002 (+0.8) | no |
| CSCO | 1W | -14.4 | +1.7 | D -8.4 / E -4.4 | 52.6% (52.9%) | Product type | +0.006 (+0.2) | +0.0001 (+0.1) | yes |
| CSCO | 1M | +3.8 | +0.5 | — | 54.9% (55.4%) | Sector | +0.015 (+0.2) | +0.0004 (+0.3) | no |
| CSCO | 3M | +9.7 | +7.0 | — | 59.5% (57.9%) | Global | +0.057 (+0.5) | +0.0017 (+0.5) | no |
| CSCO | 6M | +2.3 | +5.4 | — | 62.8% (60.4%) | Sector | +0.242 (+1.4) | +0.0025 (+0.4) | no |
| CSCO | 12M | +0.1 | +3.9 | — | 69.5% (71.5%) | Asset | -0.036 (-0.1) | +0.0101 (+1.0) | no |
| CVX | 1D | +0.1 | -0.0 | — | 50.9% (50.2%) | Global | -0.009 (-0.3) | +0.0001 (+0.5) | no |
| CVX | 1W | -4.7 | -1.3 | D -8.8 / E +0.5 | 49.0% (49.9%) | Global | -0.017 (-0.5) | -0.0001 (-0.3) | yes |
| CVX | 1M | -3.8 | -3.8 | — | 49.4% (50.0%) | Global | -0.049 (-0.7) | -0.0018 (-1.2) | no |
| CVX | 3M | +6.2 | +6.8 | — | 53.1% (51.9%) | Class | -0.022 (-0.2) | -0.0013 (-0.4) | no |
| CVX | 6M | +5.7 | +11.3 | — | 64.4% (62.3%) | Global | +0.021 (+0.1) | -0.0021 (-0.3) | no |
| CVX | 12M | +1.4 | +1.0 | — | 68.8% (69.3%) | Class | +0.007 (+0.0) | -0.0086 (-0.6) | no |
| CWB | 1D | -0.2 | -2.7 | — | 54.2% (53.3%) | Asset | +0.152 (+3.7) | +0.0001 (+0.2) | no |
| CWB | 1W | +0.4 | +3.9 | D +1.9 / E +2.7 | 56.2% (55.3%) | Product type | +0.139 (+3.5) | +0.0006 (+1.1) | yes |
| CWB | 1M | +0.3 | -5.3 | — | 59.6% (59.8%) | Global | +0.025 (+0.3) | -0.0003 (-0.2) | no |
| CWB | 3M | +13.2 | +4.8 | — | 65.6% (64.0%) | Global | +0.114 (+0.8) | +0.0027 (+0.9) | no |
| CWB | 6M | +15.8 | -0.5 | — | 67.7% (65.9%) | Global | +0.151 (+0.6) | +0.0019 (+0.3) | no |
| CWB | 12M | +7.2 | +6.1 | — | 78.3% (77.3%) | Global | +0.217 (+0.3) | +0.0019 (+0.3) | no |
| DAX | 1D | +0.7 | +2.1 | — | 53.7% (53.1%) | Product type | +0.155 (+4.7) | +0.0002 (+0.8) | no |
| DAX | 1W | +2.1 | -4.0 | D -9.3 / E -2.2 | 55.2% (54.9%) | Class | +0.084 (+2.5) | -0.0001 (-0.3) | yes |
| DAX | 1M | +0.6 | +1.6 | — | 60.2% (60.3%) | Global | +0.094 (+1.4) | -0.0002 (-0.2) | no |
| DAX | 3M | -0.7 | -1.3 | — | 62.9% (61.7%) | Class | +0.111 (+0.9) | +0.0013 (+0.4) | no |
| DAX | 6M | -4.9 | -4.3 | — | 61.3% (59.1%) | Class | +0.161 (+0.9) | -0.0031 (-0.4) | no |
| DAX | 12M | +5.4 | +5.7 | — | 65.4% (65.3%) | Class | -0.032 (-0.1) | -0.0006 (-0.1) | no |
| DBA | 1D | +0.1 | +1.8 | — | 52.0% (51.4%) | Product type | -0.069 (-1.8) | -0.0002 (-0.6) | no |
| DBA | 1W | +0.6 | +3.6 | D -2.6 / E +1.7 | 51.4% (51.9%) | Product type | +0.013 (+0.3) | -0.0009 (-1.9) | yes |
| DBA | 1M | +0.1 | -4.6 | — | 53.7% (54.1%) | Global | -0.072 (-0.9) | -0.0039 (-1.6) | no |
| DBA | 3M | +1.9 | +0.7 | — | 57.5% (56.4%) | Global | +0.054 (+0.4) | -0.0138 (-1.7) | no |
| DBA | 6M | +4.9 | +0.7 | — | 59.9% (57.6%) | Global | +0.010 (+0.0) | -0.0258 (-1.7) | no |
| DBA | 12M | +1.1 | +3.6 | — | 63.3% (63.6%) | Global | -0.005 (-0.0) | -0.0066 (-1.4) | no |
| DBC | 1D | +1.2 | +1.4 | — | 52.1% (51.4%) | Sector | +0.059 (+1.4) | +0.0000 (+0.0) | no |
| DBC | 1W | +5.2 | +5.9 | D -3.7 / E +9.9 | 51.4% (51.9%) | Asset | -0.043 (-1.0) | +0.0001 (+0.2) | yes |
| DBC | 1M | +9.6 | -7.9 | — | 53.7% (54.1%) | Global | -0.177 (-2.1) | -0.0011 (-0.4) | no |
| DBC | 3M | -26.3 | +3.9 | — | 57.9% (56.4%) | Global | -0.253 (-1.7) | -0.0027 (-0.4) | no |
| DBC | 6M | +22.2 | +2.1 | — | 60.1% (57.6%) | Global | -0.217 (-0.9) | -0.0039 (-0.3) | no |
| DBC | 12M | +0.4 | +1.5 | — | 63.3% (63.6%) | Global | -0.031 (-0.1) | +0.0012 (+0.2) | no |
| DIA | 1D | +3.4 | +0.7 | — | 54.5% (53.9%) | Global | +0.080 (+2.4) | -0.0000 (-0.1) | no |
| DIA | 1W | +5.7 | -0.5 | D -7.6 / E -4.9 | 56.9% (56.3%) | Class | +0.064 (+1.9) | +0.0005 (+1.1) | yes |
| DIA | 1M | +7.2 | -0.3 | — | 62.2% (62.3%) | Class | +0.109 (+1.6) | +0.0011 (+1.0) | no |
| DIA | 3M | -0.1 | +1.4 | — | 67.6% (65.9%) | Class | +0.058 (+0.5) | +0.0001 (+0.0) | no |
| DIA | 6M | -0.7 | -0.1 | — | 72.7% (71.4%) | Class | +0.035 (+0.2) | -0.0025 (-0.5) | no |
| DIA | 12M | +6.6 | +0.2 | — | 79.5% (78.1%) | Class | -0.163 (-0.6) | +0.0058 (+0.8) | no |
| DIS | 1D | +5.5 | +0.1 | — | 52.7% (51.8%) | Sector | -0.025 (-0.7) | +0.0001 (+0.4) | no |
| DIS | 1W | -0.1 | -4.4 | D -11.1 / E -4.8 | 53.0% (52.8%) | Global | +0.019 (+0.6) | -0.0003 (-0.6) | yes |
| DIS | 1M | +0.2 | -2.3 | — | 55.9% (56.2%) | Global | -0.008 (-0.1) | -0.0007 (-0.5) | no |
| DIS | 3M | +0.5 | -6.2 | — | 60.9% (60.0%) | Class | -0.108 (-0.9) | -0.0018 (-0.6) | no |
| DIS | 6M | +1.1 | -7.2 | — | 65.8% (64.1%) | Class | -0.039 (-0.2) | -0.0103 (-1.4) | no |
| DIS | 12M | +3.9 | +4.5 | — | 75.7% (74.1%) | Class | +0.058 (+0.2) | +0.0053 (+0.4) | no |
| DJI | 1D | +2.0 | +0.5 | — | 54.5% (53.9%) | Global | +0.087 (+2.6) | +0.0000 (+0.2) | no |
| DJI | 1W | +5.8 | -0.6 | D -8.6 / E -4.6 | 56.9% (56.4%) | Class | +0.063 (+1.9) | +0.0003 (+0.8) | yes |
| DJI | 1M | +5.9 | -0.1 | — | 62.2% (62.2%) | Class | +0.112 (+1.6) | +0.0005 (+0.4) | no |
| DJI | 3M | +2.0 | +1.0 | — | 67.5% (65.8%) | Class | +0.052 (+0.4) | -0.0003 (-0.1) | no |
| DJI | 6M | -1.1 | -0.6 | — | 72.7% (71.3%) | Global | +0.037 (+0.2) | -0.0031 (-0.6) | no |
| DJI | 12M | +7.2 | -0.1 | — | 79.6% (78.2%) | Global | -0.128 (-0.5) | +0.0020 (+0.3) | no |
| DXY | 1D | +0.0 | +1.4 | — | 51.9% (51.4%) | Asset | +0.053 (+1.6) | -0.0003 (-1.2) | no |
| DXY | 1W | -0.8 | -0.3 | D -7.5 / E +6.7 | 51.4% (51.9%) | Asset | -0.026 (-0.8) | -0.0001 (-0.4) | yes |
| DXY | 1M | -1.3 | +2.8 | — | 53.6% (54.1%) | Class | -0.090 (-1.3) | -0.0002 (-0.1) | no |
| DXY | 3M | +5.0 | +0.5 | — | 57.3% (56.4%) | Global | -0.250 (-2.1) | -0.0016 (-0.3) | no |
| DXY | 6M | +1.5 | -1.6 | — | 59.8% (57.6%) | Global | -0.222 (-1.3) | -0.0071 (-0.6) | no |
| DXY | 12M | -2.1 | -1.8 | — | 63.6% (63.6%) | Global | -0.338 (-1.4) | -0.0091 (-0.5) | no |
| EEM | 1D | -7.7 | -2.7 | — | 53.8% (53.0%) | Global | +0.062 (+1.8) | +0.0001 (+0.7) | no |
| EEM | 1W | +8.8 | +9.7 | D +1.4 / E +6.0 | 54.9% (54.6%) | Asset | +0.212 (+6.5) | -0.0005 (-1.2) | yes |
| EEM | 1M | +1.1 | -3.2 | — | 58.2% (58.4%) | Global | +0.073 (+1.1) | -0.0021 (-1.5) | no |
| EEM | 3M | +14.8 | +4.7 | — | 63.0% (61.6%) | Global | -0.037 (-0.3) | -0.0002 (-0.1) | no |
| EEM | 6M | +10.8 | +2.2 | — | 65.4% (63.2%) | Product type | +0.043 (+0.2) | -0.0026 (-0.4) | no |
| EEM | 12M | -0.5 | +5.1 | — | 74.8% (74.0%) | Global | +0.032 (+0.1) | +0.0025 (+0.3) | no |
| EFA | 1D | +32.9 | +1.3 | — | 54.5% (53.9%) | Asset | +0.092 (+2.7) | -0.0000 (-0.0) | no |
| EFA | 1W | +6.2 | -1.6 | D -7.8 / E -4.4 | 56.9% (56.5%) | Product type | +0.107 (+3.1) | -0.0005 (-1.0) | yes |
| EFA | 1M | +1.9 | -1.1 | — | 62.0% (62.0%) | Global | +0.032 (+0.4) | -0.0006 (-0.4) | no |
| EFA | 3M | +1.2 | +1.0 | — | 65.3% (63.7%) | Global | -0.056 (-0.4) | +0.0009 (+0.3) | no |
| EFA | 6M | +1.6 | -0.3 | — | 68.3% (66.5%) | Global | +0.057 (+0.3) | -0.0014 (-0.2) | no |
| EFA | 12M | +3.2 | +2.8 | — | 75.0% (73.8%) | Global | -0.007 (-0.0) | -0.0125 (-0.9) | no |
| EMB | 1D | -8.8 | -0.9 | — | 57.5% (56.8%) | Class | +0.042 (+1.1) | +0.0003 (+0.5) | no |
| EMB | 1W | -4.4 | -2.0 | D -5.2 / E -5.0 | 55.3% (54.8%) | Class | +0.091 (+2.4) | +0.0002 (+0.5) | yes |
| EMB | 1M | -3.5 | +1.9 | — | 60.5% (60.6%) | Global | +0.006 (+0.1) | +0.0005 (+0.3) | no |
| EMB | 3M | -6.3 | +1.9 | — | 64.1% (62.9%) | Global | +0.102 (+0.8) | +0.0071 (+1.3) | no |
| EMB | 6M | -1.1 | +1.3 | — | 70.4% (68.8%) | Global | +0.069 (+0.3) | +0.0015 (+0.2) | no |
| EMB | 12M | -0.2 | -0.3 | — | 71.4% (71.1%) | Global | -0.362 (-0.7) | -0.0119 (-1.6) | no |
| ETH | 1D | +0.3 | -0.4 | — | 52.7% (51.9%) | Asset | +0.030 (+0.3) | -0.0001 (-0.2) | no |
| ETH | 1W | +0.7 | -2.6 | D -1.6 / E +0.7 | 53.8% (53.6%) | Asset | +0.068 (+0.6) | -0.0000 (-0.0) | yes |
| ETH | 1M | +0.2 | -0.9 | — | 54.5% (54.8%) | Global | -0.008 (-0.0) | -0.0002 (-0.1) | no |
| ETH | 3M | -4.6 | -1.1 | — | 59.0% (58.2%) | Global | — (—) | — (—) | no |
| EURUSD | 1D | +2.0 | +0.0 | — | 52.0% (51.4%) | Asset | +0.140 (+4.2) | +0.0002 (+0.6) | no |
| EURUSD | 1W | +0.2 | -3.2 | D -6.5 / E -7.0 | 51.5% (51.9%) | Global | +0.035 (+1.0) | -0.0006 (-1.7) | yes |
| EURUSD | 1M | +0.2 | -0.3 | — | 53.6% (54.1%) | Global | -0.005 (-0.1) | -0.0040 (-2.0) | no |
| EURUSD | 3M | -1.6 | +1.6 | — | 57.1% (56.4%) | Global | -0.037 (-0.3) | -0.0134 (-1.9) | no |
| EURUSD | 6M | -1.9 | +0.5 | — | 59.9% (57.6%) | Global | +0.100 (+0.5) | -0.0229 (-1.6) | no |
| EURUSD | 12M | -0.0 | +1.9 | — | 63.3% (63.6%) | Class | +0.012 (+0.0) | +0.0072 (+1.0) | no |
| EWG | 1D | +6.2 | +1.2 | — | 54.3% (53.8%) | Product type | +0.072 (+2.1) | +0.0002 (+1.2) | no |
| EWG | 1W | +2.8 | -4.1 | D -8.5 / E -5.9 | 56.6% (56.1%) | Class | +0.077 (+2.3) | -0.0002 (-0.5) | yes |
| EWG | 1M | +0.7 | +0.3 | — | 61.6% (61.6%) | Global | +0.017 (+0.2) | -0.0015 (-1.1) | no |
| EWG | 3M | -4.4 | -1.6 | — | 64.5% (63.2%) | Product type | -0.030 (-0.2) | -0.0020 (-0.7) | no |
| EWG | 6M | -4.7 | -3.7 | — | 67.8% (66.0%) | Asset | -0.012 (-0.1) | -0.0052 (-0.9) | no |
| EWG | 12M | +3.4 | +6.3 | — | 72.9% (72.2%) | Global | -0.110 (-0.4) | -0.0118 (-1.0) | no |
| EWJ | 1D | +17.2 | +1.3 | — | 53.8% (53.1%) | Asset | +0.084 (+2.5) | +0.0004 (+1.9) | no |
| EWJ | 1W | -14.8 | -0.4 | D -10.1 / E -8.8 | 54.9% (54.9%) | Product type | +0.054 (+1.6) | +0.0002 (+0.5) | yes |
| EWJ | 1M | +4.7 | -2.0 | — | 59.4% (59.6%) | Class | -0.035 (-0.5) | +0.0005 (+0.4) | no |
| EWJ | 3M | +5.3 | +2.7 | — | 65.0% (63.2%) | Class | +0.190 (+1.6) | +0.0017 (+0.7) | no |
| EWJ | 6M | +2.5 | +0.9 | — | 66.5% (64.5%) | Asset | +0.199 (+1.1) | -0.0017 (-0.3) | no |
| EWJ | 12M | +4.0 | +2.9 | — | 71.2% (70.8%) | Product type | -0.190 (-0.7) | -0.0013 (-0.1) | no |
| EWU | 1D | +51.0 | +2.4 | — | 54.0% (53.5%) | Asset | +0.100 (+3.0) | +0.0002 (+0.9) | no |
| EWU | 1W | +14.9 | -0.7 | D -6.1 / E -2.7 | 55.6% (55.5%) | Global | +0.079 (+2.4) | -0.0001 (-0.3) | yes |
| EWU | 1M | +1.9 | -2.1 | — | 60.9% (60.9%) | Class | +0.033 (+0.5) | -0.0014 (-1.0) | no |
| EWU | 3M | -0.2 | +1.4 | — | 63.8% (62.2%) | Global | -0.015 (-0.1) | -0.0012 (-0.4) | no |
| EWU | 6M | +6.9 | +1.8 | — | 66.5% (64.5%) | Global | +0.066 (+0.4) | -0.0017 (-0.3) | no |
| EWU | 12M | +10.6 | +5.4 | — | 74.1% (73.5%) | Global | -0.006 (-0.0) | -0.0142 (-1.2) | no |
| EWZ | 1D | -0.0 | +1.8 | — | 53.3% (52.7%) | Asset | +0.094 (+2.8) | +0.0002 (+0.7) | no |
| EWZ | 1W | -1.8 | +4.3 | D +4.2 / E +3.8 | 54.0% (54.2%) | Product type | +0.105 (+3.1) | -0.0000 (-0.1) | yes |
| EWZ | 1M | -3.9 | +0.2 | — | 58.9% (59.2%) | Class | -0.033 (-0.5) | -0.0036 (-2.3) | no |
| EWZ | 3M | -2.4 | +3.4 | — | 62.8% (61.4%) | Product type | -0.086 (-0.7) | -0.0046 (-1.0) | no |
| EWZ | 6M | +2.4 | +4.7 | — | 62.7% (60.6%) | Global | +0.087 (+0.5) | -0.0058 (-0.7) | no |
| EWZ | 12M | +1.7 | +0.3 | — | 68.0% (68.0%) | Global | -0.043 (-0.1) | -0.0225 (-1.1) | no |
| FLOT | 1D | -0.8 | -2.2 | — | 74.0% (73.4%) | Global | +0.072 (+1.6) | +0.0017 (+3.0) | no |
| FLOT | 1W | +13.7 | -0.6 | D -5.6 / E -6.6 | 88.6% (84.4%) | Global | -0.056 (-1.2) | +0.0051 (+5.5) | yes |
| FLOT | 1M | +49.5 | -2.0 | — | 96.0% (95.5%) | Global | +0.046 (+0.5) | +0.0023 (+1.2) | no |
| FLOT | 3M | +5.0 | +4.7 | — | 92.9% (91.6%) | Global | -0.005 (-0.0) | +0.0078 (+1.7) | no |
| FLOT | 6M | +23.9 | +4.9 | — | 99.4% (99.4%) | Product type | -0.082 (-0.3) | +0.0013 (+0.3) | no |
| FLOT | 12M | +16.2 | +7.1 | — | 99.7% (99.6%) | Product type | -0.289 (-0.3) | +0.0003 (+0.6) | no |
| FTSE | 1D | +5.6 | +2.6 | — | 53.1% (52.6%) | Product type | +0.155 (+4.6) | +0.0003 (+1.5) | no |
| FTSE | 1W | +31.5 | +1.3 | D -5.3 / E +1.4 | 53.7% (54.0%) | Product type | +0.085 (+2.5) | -0.0005 (-1.4) | yes |
| FTSE | 1M | +9.2 | +0.8 | — | 58.1% (58.4%) | Class | +0.126 (+1.8) | -0.0018 (-1.2) | no |
| FTSE | 3M | +7.2 | +2.1 | — | 61.7% (60.1%) | Class | +0.014 (+0.1) | +0.0002 (+0.1) | no |
| FTSE | 6M | +11.8 | +2.0 | — | 60.8% (58.4%) | Class | +0.097 (+0.6) | -0.0022 (-0.3) | no |
| FTSE | 12M | +13.8 | +5.7 | — | 65.1% (65.3%) | Global | +0.106 (+0.4) | -0.0046 (-0.4) | no |
| FXB | 1D | -0.4 | +0.7 | — | 51.9% (51.4%) | Global | +0.041 (+1.1) | -0.0010 (-2.8) | no |
| FXB | 1W | -2.0 | -1.7 | D -1.4 / E -2.6 | 51.1% (51.9%) | Global | +0.113 (+3.1) | -0.0005 (-1.3) | yes |
| FXB | 1M | +0.1 | -1.6 | — | 53.7% (54.1%) | Global | +0.036 (+0.5) | -0.0031 (-1.4) | no |
| FXB | 3M | +0.6 | +2.8 | — | 57.4% (56.4%) | Global | -0.164 (-1.1) | -0.0126 (-1.5) | no |
| FXB | 6M | -0.0 | +1.8 | — | 60.0% (57.6%) | Global | -0.233 (-1.1) | -0.0209 (-1.3) | no |
| FXB | 12M | -1.5 | +1.7 | — | 63.2% (63.6%) | Global | -0.102 (-0.2) | -0.0006 (-0.1) | no |
| FXE | 1D | -0.0 | +2.6 | — | 51.9% (51.4%) | Global | +0.002 (+0.1) | +0.0000 (+0.1) | no |
| FXE | 1W | +0.1 | +0.8 | D +4.7 / E +1.1 | 51.3% (51.9%) | Global | +0.106 (+2.9) | -0.0010 (-2.7) | yes |
| FXE | 1M | -2.6 | -0.9 | — | 53.6% (54.1%) | Global | -0.026 (-0.3) | -0.0054 (-2.4) | no |
| FXE | 3M | -11.2 | +1.6 | — | 57.2% (56.4%) | Global | -0.121 (-0.9) | -0.0151 (-1.9) | no |
| FXE | 6M | -3.5 | +0.5 | — | 59.8% (57.6%) | Global | -0.157 (-0.8) | -0.0290 (-1.7) | no |
| FXE | 12M | -2.8 | +4.4 | — | 63.5% (63.6%) | Product type | -0.030 (-0.1) | +0.0022 (+0.4) | no |
| FXI | 1D | -6.6 | -1.8 | — | 53.5% (52.7%) | Global | +0.068 (+2.0) | +0.0001 (+0.5) | no |
| FXI | 1W | -2.3 | -6.7 | D -8.9 / E -9.1 | 54.5% (54.1%) | Product type | +0.138 (+3.9) | -0.0008 (-1.8) | yes |
| FXI | 1M | -2.8 | +3.3 | — | 58.2% (58.4%) | Global | +0.103 (+1.4) | -0.0035 (-2.0) | no |
| FXI | 3M | -6.5 | -1.0 | — | 63.5% (62.5%) | Global | +0.045 (+0.3) | -0.0043 (-1.1) | no |
| FXI | 6M | -2.2 | -0.0 | — | 66.2% (64.3%) | Global | +0.167 (+0.9) | -0.0123 (-1.5) | no |
| FXI | 12M | -0.7 | +7.8 | — | 69.9% (68.9%) | Asset | +0.018 (+0.0) | +0.0003 (+0.1) | no |
| FXY | 1D | +3.4 | +1.1 | — | 52.2% (51.4%) | Global | -0.035 (-1.0) | -0.0003 (-0.9) | no |
| FXY | 1W | +7.2 | -0.3 | D -3.4 / E -0.7 | 51.9% (51.9%) | Global | +0.022 (+0.6) | -0.0007 (-2.2) | yes |
| FXY | 1M | -0.2 | +2.1 | — | 53.6% (54.1%) | Global | -0.021 (-0.3) | -0.0047 (-2.4) | no |
| FXY | 3M | -7.7 | -0.5 | — | 56.8% (56.4%) | Global | +0.035 (+0.3) | -0.0155 (-2.1) | no |
| FXY | 6M | -2.9 | -2.4 | — | 59.8% (57.6%) | Global | -0.060 (-0.3) | -0.0234 (-1.5) | no |
| FXY | 12M | -2.0 | -2.1 | — | 63.7% (63.6%) | Global | -0.044 (-0.1) | +0.0131 (+2.0) | no |
| GBPUSD | 1D | -0.1 | -0.6 | — | 51.9% (51.4%) | Asset | +0.063 (+1.8) | -0.0001 (-0.4) | no |
| GBPUSD | 1W | -0.2 | -4.9 | D -7.9 / E -8.1 | 51.3% (51.9%) | Global | +0.076 (+2.1) | -0.0004 (-1.1) | yes |
| GBPUSD | 1M | -0.3 | -1.1 | — | 53.6% (54.1%) | Global | +0.069 (+0.9) | -0.0040 (-1.9) | no |
| GBPUSD | 3M | -0.7 | +1.8 | — | 57.1% (56.4%) | Global | -0.082 (-0.6) | -0.0135 (-1.7) | no |
| GBPUSD | 6M | -1.8 | +1.2 | — | 59.9% (57.6%) | Global | +0.053 (+0.3) | -0.0239 (-1.5) | no |
| GBPUSD | 12M | -0.6 | +0.8 | — | 63.2% (63.6%) | Class | +0.117 (+0.3) | -0.0008 (-0.1) | no |
| GLD | 1D | -1.2 | -4.5 | — | 52.3% (51.4%) | Class | +0.024 (+0.7) | -0.0001 (-0.3) | no |
| GLD | 1W | -0.3 | +3.9 | D -4.7 / E -6.7 | 52.1% (51.9%) | Class | +0.001 (+0.0) | -0.0006 (-1.8) | yes |
| GLD | 1M | +0.7 | -1.9 | — | 53.6% (54.1%) | Global | -0.056 (-0.8) | -0.0032 (-1.6) | no |
| GLD | 3M | -2.7 | -2.6 | — | 57.7% (56.4%) | Global | -0.114 (-0.9) | -0.0101 (-1.4) | no |
| GLD | 6M | +13.8 | +1.3 | — | 60.2% (57.6%) | Global | -0.138 (-0.7) | -0.0202 (-1.2) | no |
| GLD | 12M | +14.3 | +7.2 | — | 63.5% (63.6%) | Industry | +0.433 (+1.2) | -0.0019 (-0.4) | no |
| GOLD | 1D | +1.7 | -0.2 | — | 52.3% (51.4%) | Product type | +0.011 (+0.3) | -0.0010 (-3.5) | no |
| GOLD | 1W | -0.1 | +6.5 | D -0.3 / E +0.2 | 52.0% (51.9%) | Sector | +0.078 (+2.3) | -0.0009 (-2.9) | yes |
| GOLD | 1M | +1.3 | -1.8 | — | 53.6% (54.1%) | Global | -0.018 (-0.3) | -0.0020 (-1.0) | no |
| GOLD | 3M | -3.8 | +0.7 | — | 57.5% (56.4%) | Global | -0.094 (-0.8) | -0.0099 (-1.4) | no |
| GOLD | 6M | +21.8 | +3.9 | — | 60.2% (57.6%) | Global | -0.030 (-0.2) | -0.0148 (-1.1) | no |
| GOLD | 12M | +15.3 | +5.6 | — | 63.7% (63.6%) | Asset | +0.500 (+1.9) | -0.0337 (-1.2) | no |
| GOOGL | 1D | -0.4 | +1.2 | — | 52.8% (52.3%) | Class | +0.099 (+2.6) | +0.0005 (+1.5) | no |
| GOOGL | 1W | -2.2 | +0.7 | D -8.3 / E -8.2 | 53.3% (53.3%) | Global | +0.116 (+3.0) | +0.0007 (+1.4) | yes |
| GOOGL | 1M | +0.1 | +3.1 | — | 56.7% (57.1%) | Global | +0.062 (+0.8) | +0.0006 (+0.4) | no |
| GOOGL | 3M | -1.4 | -3.2 | — | 61.9% (60.3%) | Product type | +0.033 (+0.3) | +0.0021 (+0.7) | no |
| GOOGL | 6M | -2.4 | +9.4 | — | 68.7% (66.9%) | Sector | +0.102 (+0.5) | +0.0080 (+1.2) | no |
| GOOGL | 12M | -4.6 | -5.4 | — | 69.6% (69.6%) | Product type | -0.238 (-0.6) | +0.0017 (+0.2) | no |
| GS | 1D | +5.6 | +0.8 | — | 53.2% (52.5%) | Global | +0.071 (+2.1) | +0.0001 (+0.5) | no |
| GS | 1W | +0.0 | +3.5 | D -5.7 / E -4.2 | 53.9% (54.0%) | Global | -0.006 (-0.2) | -0.0001 (-0.3) | yes |
| GS | 1M | +0.3 | +4.5 | — | 57.4% (57.7%) | Global | +0.065 (+0.9) | -0.0012 (-0.8) | no |
| GS | 3M | +3.4 | +7.0 | — | 64.5% (62.8%) | Global | -0.022 (-0.2) | +0.0002 (+0.1) | no |
| GS | 6M | -0.9 | +3.4 | — | 66.1% (64.0%) | Global | +0.111 (+0.6) | -0.0028 (-0.5) | no |
| GS | 12M | -0.5 | +4.3 | — | 73.8% (75.2%) | Global | -0.019 (-0.1) | -0.0043 (-0.3) | no |
| HD | 1D | +0.2 | -0.9 | — | 52.9% (52.0%) | Global | +0.032 (+1.0) | -0.0001 (-0.4) | no |
| HD | 1W | +0.3 | -5.2 | D -9.4 / E -4.4 | 53.4% (52.9%) | Global | +0.019 (+0.6) | -0.0001 (-0.3) | yes |
| HD | 1M | +0.0 | +4.9 | — | 55.6% (56.0%) | Global | +0.061 (+0.9) | +0.0014 (+0.9) | no |
| HD | 3M | -6.1 | -0.2 | — | 59.1% (58.4%) | Class | -0.083 (-0.7) | +0.0013 (+0.5) | no |
| HD | 6M | -4.0 | -1.0 | — | 63.7% (61.6%) | Class | -0.024 (-0.1) | +0.0055 (+1.0) | no |
| HD | 12M | -3.3 | -0.6 | — | 65.4% (68.6%) | Class | +0.175 (+0.7) | +0.0170 (+2.4) | no |
| HSI | 1D | -14.5 | -2.0 | — | 52.1% (51.4%) | Class | +0.070 (+2.0) | -0.0000 (-0.1) | no |
| HSI | 1W | -1.3 | -3.6 | D -8.2 / E +1.6 | 51.8% (52.0%) | Global | +0.084 (+2.5) | +0.0003 (+1.0) | yes |
| HSI | 1M | -1.7 | +1.1 | — | 53.4% (53.9%) | Global | +0.044 (+0.6) | -0.0020 (-1.1) | no |
| HSI | 3M | -7.2 | -1.8 | — | 57.8% (56.8%) | Global | +0.060 (+0.5) | -0.0063 (-1.1) | no |
| HSI | 6M | -0.5 | -2.2 | — | 59.3% (57.0%) | Global | +0.092 (+0.5) | -0.0089 (-0.8) | no |
| HSI | 12M | +1.8 | +8.5 | — | 63.2% (63.1%) | Global | -0.262 (-1.0) | -0.0071 (-0.4) | no |
| HYG | 1D | -0.5 | +1.2 | — | 61.8% (61.3%) | Class | +0.097 (+2.6) | -0.0002 (-0.3) | no |
| HYG | 1W | +5.3 | -2.0 | D -6.5 / E -7.7 | 62.0% (60.4%) | Global | +0.051 (+1.3) | +0.0002 (+0.4) | yes |
| HYG | 1M | -4.1 | +1.2 | — | 70.8% (70.4%) | Global | +0.084 (+1.1) | +0.0001 (+0.1) | no |
| HYG | 3M | -2.0 | -0.9 | — | 70.4% (68.8%) | Global | -0.042 (-0.3) | +0.0107 (+1.6) | no |
| HYG | 6M | -0.7 | -2.0 | — | 76.5% (75.4%) | Global | -0.190 (-0.9) | +0.0130 (+1.2) | no |
| HYG | 12M | +1.1 | +2.8 | — | 78.9% (78.1%) | Global | -0.126 (-0.2) | -0.0098 (-1.1) | no |
| IBM | 1D | +0.1 | +1.1 | — | 51.8% (50.8%) | Product type | -0.003 (-0.1) | -0.0002 (-1.0) | no |
| IBM | 1W | -0.0 | +0.8 | D -12.2 / E -2.1 | 51.3% (50.9%) | Product type | +0.013 (+0.4) | -0.0007 (-1.7) | yes |
| IBM | 1M | +1.4 | +1.3 | — | 50.4% (51.0%) | Industry | -0.008 (-0.1) | -0.0023 (-1.5) | no |
| IBM | 3M | +1.1 | -8.8 | — | 56.3% (55.7%) | Product type | -0.101 (-0.8) | -0.0028 (-0.8) | no |
| IBM | 6M | +0.6 | -7.6 | — | 59.8% (57.1%) | Sector | -0.077 (-0.4) | -0.0056 (-0.8) | no |
| IBM | 12M | +1.2 | +2.7 | — | 64.8% (67.4%) | Class | +0.124 (+0.5) | -0.0279 (-1.7) | no |
| IEF | 1D | +5.5 | +0.9 | — | 55.3% (54.6%) | Class | +0.146 (+4.4) | +0.0002 (+0.6) | no |
| IEF | 1W | +1.0 | -0.9 | D +0.5 / E -3.1 | 58.6% (57.5%) | Class | +0.098 (+2.9) | -0.0006 (-1.3) | yes |
| IEF | 1M | -1.0 | +1.3 | — | 64.5% (64.5%) | Global | -0.025 (-0.4) | -0.0008 (-0.5) | no |
| IEF | 3M | -0.9 | -0.5 | — | 69.4% (68.1%) | Global | -0.069 (-0.6) | -0.0039 (-0.9) | no |
| IEF | 6M | +0.3 | +1.0 | — | 76.8% (75.8%) | Global | -0.082 (-0.5) | -0.0065 (-0.8) | no |
| IEF | 12M | +0.2 | -1.6 | — | 77.3% (76.3%) | Global | -0.387 (-1.2) | -0.0080 (-0.4) | no |
| IEI | 1D | -1.8 | -3.1 | — | 57.2% (56.5%) | Global | -0.001 (-0.0) | -0.0001 (-0.3) | no |
| IEI | 1W | +2.6 | +2.7 | D -6.5 / E -22.1 | 61.4% (59.7%) | Global | +0.011 (+0.3) | -0.0010 (-1.8) | yes |
| IEI | 1M | -2.2 | +2.9 | — | 70.0% (69.7%) | Global | -0.008 (-0.1) | -0.0014 (-0.9) | no |
| IEI | 3M | -2.0 | -2.2 | — | 75.2% (73.7%) | Global | -0.067 (-0.5) | -0.0048 (-1.4) | no |
| IEI | 6M | +0.4 | -0.7 | — | 84.0% (83.4%) | Global | +0.006 (+0.0) | -0.0074 (-1.0) | no |
| IEI | 12M | +2.3 | +0.4 | — | 82.8% (81.5%) | Global | +0.114 (+0.2) | -0.0010 (-0.2) | no |
| INDA | 1D | +1.0 | -0.3 | — | 53.9% (53.2%) | Class | +0.087 (+2.0) | +0.0003 (+1.1) | no |
| INDA | 1W | -0.1 | -4.6 | D -7.1 / E -4.0 | 55.5% (54.9%) | Product type | +0.083 (+1.9) | -0.0006 (-1.4) | yes |
| INDA | 1M | -0.1 | +4.5 | — | 60.0% (60.2%) | Class | -0.035 (-0.4) | -0.0005 (-0.3) | no |
| INDA | 3M | -1.5 | -6.2 | — | 61.5% (60.7%) | Global | -0.124 (-0.7) | -0.0007 (-0.2) | no |
| INDA | 6M | -4.0 | -5.9 | — | 64.0% (62.2%) | Global | +0.357 (+1.3) | -0.0072 (-0.8) | no |
| INDA | 12M | -0.9 | -5.7 | — | 71.0% (70.8%) | Product type | -0.405 (-0.4) | -0.0055 (-0.7) | no |
| INTC | 1D | +0.1 | +0.9 | — | 52.6% (51.7%) | Industry | -0.017 (-0.5) | +0.0001 (+0.3) | no |
| INTC | 1W | +1.0 | +5.9 | D -8.2 / E +3.9 | 52.2% (52.3%) | Global | +0.009 (+0.3) | +0.0001 (+0.1) | yes |
| INTC | 1M | +2.4 | -6.9 | — | 53.6% (54.2%) | Industry | -0.057 (-0.8) | -0.0012 (-0.8) | no |
| INTC | 3M | +28.5 | +15.4 | — | 55.6% (54.1%) | Class | -0.013 (-0.1) | -0.0025 (-0.8) | no |
| INTC | 6M | +1.1 | +7.5 | — | 58.0% (55.2%) | Class | +0.033 (+0.2) | -0.0006 (-0.1) | no |
| INTC | 12M | -0.9 | +5.4 | — | 60.2% (63.1%) | Class | +0.063 (+0.2) | +0.0051 (+0.4) | no |
| IWD | 1D | +4.5 | +2.0 | — | 54.5% (54.0%) | Class | +0.062 (+1.9) | -0.0000 (-0.3) | no |
| IWD | 1W | +5.8 | +0.7 | D -6.2 / E -2.0 | 56.3% (56.3%) | Product type | +0.070 (+2.1) | +0.0003 (+0.6) | yes |
| IWD | 1M | +4.3 | -4.4 | — | 62.0% (62.0%) | Class | +0.058 (+0.8) | +0.0006 (+0.5) | no |
| IWD | 3M | +4.8 | +3.0 | — | 67.8% (65.9%) | Class | -0.040 (-0.3) | +0.0000 (+0.0) | no |
| IWD | 6M | +3.1 | +2.1 | — | 73.9% (72.6%) | Global | -0.039 (-0.2) | -0.0033 (-0.6) | no |
| IWD | 12M | -0.7 | +0.2 | — | 80.0% (78.5%) | Global | +0.346 (+1.2) | +0.0020 (+0.2) | no |
| IWF | 1D | -3.6 | -1.1 | — | 54.4% (53.6%) | Global | -0.020 (-0.6) | +0.0000 (+0.1) | no |
| IWF | 1W | +1.3 | -2.4 | D -11.6 / E -7.3 | 56.5% (55.7%) | Global | +0.038 (+1.1) | +0.0003 (+0.8) | yes |
| IWF | 1M | +1.2 | +0.1 | — | 61.2% (61.3%) | Class | +0.021 (+0.3) | +0.0019 (+1.7) | no |
| IWF | 3M | +1.1 | +0.1 | — | 67.8% (66.3%) | Class | -0.012 (-0.1) | -0.0001 (-0.1) | no |
| IWF | 6M | -3.3 | -4.5 | — | 74.4% (73.2%) | Class | -0.045 (-0.3) | -0.0034 (-0.7) | no |
| IWF | 12M | +8.3 | +3.4 | — | 82.7% (81.3%) | Product type | -0.244 (-0.8) | +0.0088 (+1.2) | no |
| IWM | 1D | +5.2 | +2.0 | — | 55.1% (54.7%) | Global | +0.071 (+2.1) | +0.0001 (+0.3) | no |
| IWM | 1W | +5.3 | +1.5 | D -6.4 / E -3.1 | 57.9% (57.3%) | Global | +0.055 (+1.6) | -0.0006 (-1.3) | yes |
| IWM | 1M | +1.6 | -2.2 | — | 62.9% (62.9%) | Global | +0.104 (+1.5) | -0.0002 (-0.2) | no |
| IWM | 3M | -5.4 | +3.3 | — | 67.8% (65.9%) | Global | +0.049 (+0.4) | -0.0001 (-0.1) | no |
| IWM | 6M | +1.2 | +0.3 | — | 70.9% (69.3%) | Global | +0.212 (+1.2) | -0.0038 (-0.7) | no |
| IWM | 12M | +0.8 | -1.5 | — | 74.7% (73.8%) | Asset | +0.026 (+0.1) | +0.0019 (+0.2) | no |
| JNJ | 1D | -0.2 | -1.1 | — | 51.4% (50.7%) | Industry | +0.016 (+0.5) | -0.0003 (-1.6) | no |
| JNJ | 1W | +1.9 | +8.9 | D +0.2 / E +2.8 | 49.8% (50.8%) | Global | +0.014 (+0.4) | +0.0005 (+1.3) | yes |
| JNJ | 1M | -2.8 | -5.1 | — | 51.0% (51.5%) | Global | -0.013 (-0.2) | -0.0001 (-0.1) | no |
| JNJ | 3M | -5.2 | +10.4 | — | 55.6% (54.0%) | Product type | -0.017 (-0.1) | +0.0008 (+0.3) | no |
| JNJ | 6M | -7.1 | +9.9 | — | 59.8% (57.3%) | Class | +0.062 (+0.4) | +0.0031 (+0.6) | no |
| JNJ | 12M | +0.1 | +0.7 | — | 62.2% (62.8%) | Class | -0.219 (-0.9) | +0.0074 (+0.9) | no |
| JNK | 1D | -0.8 | +0.9 | — | 62.6% (62.0%) | Class | +0.088 (+2.2) | -0.0008 (-0.9) | no |
| JNK | 1W | -1.3 | -2.2 | D -7.3 / E -8.1 | 60.6% (59.1%) | Global | +0.006 (+0.1) | +0.0008 (+1.5) | yes |
| JNK | 1M | -3.0 | +0.7 | — | 71.7% (71.3%) | Global | +0.025 (+0.3) | +0.0002 (+0.2) | no |
| JNK | 3M | -6.0 | -0.8 | — | 71.0% (69.4%) | Global | -0.001 (-0.0) | +0.0115 (+1.4) | no |
| JNK | 6M | -0.6 | -1.9 | — | 75.8% (74.6%) | Global | -0.176 (-0.8) | +0.0109 (+1.2) | no |
| JNK | 12M | -1.8 | +1.6 | — | 77.5% (76.7%) | Global | -0.164 (-0.3) | -0.0126 (-1.3) | no |
| JPM | 1D | +1.0 | -0.5 | — | 53.3% (52.7%) | Global | +0.065 (+1.9) | -0.0001 (-0.7) | no |
| JPM | 1W | +0.7 | -0.9 | D -7.6 / E -4.3 | 53.7% (54.0%) | Product type | +0.033 (+1.0) | +0.0005 (+1.1) | yes |
| JPM | 1M | +2.3 | +5.4 | — | 57.7% (58.0%) | Global | +0.078 (+1.1) | +0.0005 (+0.4) | no |
| JPM | 3M | -1.2 | -2.4 | — | 62.3% (61.1%) | Class | +0.160 (+1.3) | +0.0025 (+1.0) | no |
| JPM | 6M | +0.4 | +1.6 | — | 66.9% (64.9%) | Global | +0.256 (+1.5) | +0.0028 (+0.5) | no |
| JPM | 12M | +12.8 | +1.3 | — | 71.9% (74.0%) | Global | +0.251 (+1.0) | +0.0062 (+0.7) | no |
| KO | 1D | +0.0 | -1.7 | — | 51.3% (50.6%) | Global | +0.040 (+1.2) | -0.0003 (-1.4) | no |
| KO | 1W | +1.9 | +2.3 | D -5.5 / E -5.1 | 49.6% (50.6%) | Global | +0.057 (+1.7) | +0.0006 (+1.8) | yes |
| KO | 1M | -6.5 | -4.0 | — | 50.5% (51.1%) | Global | +0.059 (+0.8) | +0.0004 (+0.3) | no |
| KO | 3M | -2.4 | +0.5 | — | 53.4% (52.4%) | Global | +0.116 (+1.0) | -0.0020 (-0.6) | no |
| KO | 6M | -0.5 | +5.4 | — | 59.0% (56.6%) | Global | +0.171 (+1.0) | -0.0009 (-0.1) | no |
| KO | 12M | -5.2 | -5.7 | — | 62.3% (63.6%) | Global | +0.059 (+0.2) | -0.0010 (-0.1) | no |
| KRE | 1D | +2.3 | +1.5 | — | 53.2% (52.8%) | Global | +0.008 (+0.2) | +0.0003 (+1.3) | no |
| KRE | 1W | +0.5 | -0.2 | D -8.2 / E -11.2 | 54.0% (54.2%) | Global | +0.018 (+0.5) | -0.0005 (-1.3) | yes |
| KRE | 1M | +3.5 | +1.8 | — | 58.2% (58.5%) | Global | +0.028 (+0.4) | -0.0012 (-0.8) | no |
| KRE | 3M | -1.1 | +1.5 | — | 64.1% (62.5%) | Global | -0.031 (-0.2) | -0.0000 (-0.0) | no |
| KRE | 6M | +0.7 | -0.1 | — | 67.4% (65.6%) | Global | +0.195 (+1.0) | -0.0070 (-0.9) | no |
| KRE | 12M | +2.1 | +0.7 | — | 73.0% (72.3%) | Global | +0.158 (+0.3) | -0.0024 (-0.7) | no |
| LLY | 1D | +0.1 | -1.7 | — | 52.0% (51.2%) | Asset | -0.015 (-0.4) | -0.0005 (-2.2) | no |
| LLY | 1W | +1.8 | +6.3 | D -3.6 / E +1.6 | 51.2% (51.6%) | Product type | +0.007 (+0.2) | +0.0004 (+1.0) | yes |
| LLY | 1M | +0.5 | -4.7 | — | 52.9% (53.4%) | Global | -0.009 (-0.1) | +0.0004 (+0.3) | no |
| LLY | 3M | +3.2 | -0.4 | — | 57.5% (56.2%) | Class | -0.022 (-0.2) | +0.0029 (+0.8) | no |
| LLY | 6M | -1.8 | +3.5 | — | 59.8% (57.3%) | Class | +0.042 (+0.2) | +0.0067 (+1.0) | no |
| LLY | 12M | -19.3 | -6.3 | — | 62.4% (63.1%) | Class | -0.130 (-0.5) | -0.0034 (-0.3) | no |
| LQD | 1D | +0.8 | -0.8 | — | 56.0% (55.2%) | Class | +0.132 (+4.0) | -0.0005 (-2.1) | no |
| LQD | 1W | -2.3 | -3.0 | D -2.4 / E -2.7 | 60.2% (58.6%) | Global | +0.071 (+2.1) | -0.0000 (-0.1) | yes |
| LQD | 1M | -3.7 | +2.5 | — | 67.4% (67.2%) | Global | +0.032 (+0.5) | -0.0004 (-0.4) | no |
| LQD | 3M | -1.6 | -0.1 | — | 72.4% (71.0%) | Global | +0.056 (+0.5) | -0.0039 (-1.2) | no |
| LQD | 6M | -0.7 | -0.9 | — | 81.0% (80.3%) | Global | +0.036 (+0.2) | -0.0033 (-0.4) | no |
| LQD | 12M | +0.0 | -1.6 | — | 81.5% (80.3%) | Global | -0.585 (-2.1) | +0.0018 (+0.3) | no |
| MA | 1D | +13.7 | +1.3 | — | 52.3% (51.6%) | Global | +0.062 (+1.6) | -0.0002 (-0.8) | no |
| MA | 1W | +9.3 | -8.5 | D -9.0 / E -6.7 | 52.1% (52.2%) | Class | +0.156 (+4.3) | +0.0011 (+2.5) | yes |
| MA | 1M | -5.9 | +0.9 | — | 54.3% (54.7%) | Global | +0.042 (+0.5) | +0.0035 (+2.0) | no |
| MA | 3M | -11.0 | -5.3 | — | 58.5% (58.1%) | Class | +0.180 (+1.2) | +0.0031 (+1.0) | no |
| MA | 6M | -9.0 | -4.1 | — | 64.7% (62.8%) | Class | +0.248 (+1.1) | +0.0064 (+1.0) | no |
| MA | 12M | +0.1 | +0.4 | — | 71.2% (72.1%) | Asset | +0.117 (+0.3) | +0.0024 (+0.5) | no |
| MBB | 1D | +5.4 | -0.9 | — | 55.4% (54.6%) | Product type | +0.167 (+4.8) | +0.0005 (+1.3) | no |
| MBB | 1W | -3.5 | +1.1 | D -0.4 / E -2.4 | 58.8% (57.4%) | Class | +0.081 (+2.3) | -0.0004 (-0.7) | yes |
| MBB | 1M | -4.9 | -0.5 | — | 64.3% (64.3%) | Global | -0.103 (-1.4) | +0.0008 (+0.6) | no |
| MBB | 3M | -0.6 | +0.7 | — | 69.8% (68.3%) | Global | -0.017 (-0.1) | +0.0006 (+0.2) | no |
| MBB | 6M | +4.9 | +2.6 | — | 77.6% (76.6%) | Global | -0.003 (-0.0) | -0.0034 (-0.5) | no |
| MBB | 12M | +4.0 | -1.9 | — | 77.6% (76.3%) | Global | -0.048 (-0.1) | -0.0017 (-0.4) | no |
| MCD | 1D | +20.5 | +0.7 | — | 51.8% (51.0%) | Sector | +0.010 (+0.3) | -0.0000 (-0.2) | no |
| MCD | 1W | +6.1 | +0.6 | D -4.4 / E -0.9 | 51.3% (51.2%) | Sector | -0.013 (-0.4) | +0.0003 (+0.8) | yes |
| MCD | 1M | +0.6 | +6.7 | — | 52.0% (52.6%) | Global | -0.070 (-1.0) | +0.0012 (+0.8) | no |
| MCD | 3M | +1.0 | -4.0 | — | 56.0% (55.3%) | Class | -0.054 (-0.4) | +0.0017 (+0.6) | no |
| MCD | 6M | +1.6 | +2.7 | — | 60.6% (58.3%) | Product type | -0.019 (-0.1) | +0.0029 (+0.4) | no |
| MCD | 12M | +1.1 | -0.8 | — | 63.6% (65.4%) | Class | -0.128 (-0.5) | +0.0054 (+0.5) | no |
| META | 1D | -1.9 | -1.2 | — | 52.7% (51.8%) | Sector | +0.099 (+2.4) | +0.0003 (+0.6) | no |
| META | 1W | -4.8 | -1.9 | D -2.6 / E -2.6 | 52.8% (52.6%) | Global | +0.130 (+3.1) | +0.0009 (+1.3) | yes |
| META | 1M | -5.7 | +5.7 | — | 55.0% (55.5%) | Global | -0.037 (-0.4) | -0.0004 (-0.2) | no |
| META | 3M | -9.2 | -11.8 | — | 59.9% (59.3%) | Global | -0.064 (-0.4) | +0.0018 (+0.5) | no |
| META | 6M | -4.1 | -5.8 | — | 66.0% (64.3%) | Global | +0.082 (+0.3) | -0.0013 (-0.1) | no |
| META | 12M | +0.3 | -3.5 | — | 70.8% (70.4%) | Product type | — (—) | — (—) | no |
| MRK | 1D | -0.1 | -2.6 | — | 52.0% (51.0%) | Asset | +0.011 (+0.3) | -0.0001 (-0.5) | no |
| MRK | 1W | +4.3 | +7.1 | D -1.5 / E +5.3 | 50.6% (51.3%) | Global | +0.001 (+0.0) | +0.0001 (+0.2) | yes |
| MRK | 1M | +6.8 | -10.9 | — | 52.0% (52.5%) | Global | -0.001 (-0.0) | -0.0002 (-0.1) | no |
| MRK | 3M | +8.5 | +8.1 | — | 56.9% (55.5%) | Class | +0.066 (+0.5) | +0.0025 (+0.7) | no |
| MRK | 6M | +15.1 | +7.8 | — | 62.5% (60.4%) | Global | +0.084 (+0.5) | +0.0060 (+0.9) | no |
| MRK | 12M | -9.0 | -12.1 | — | 65.7% (64.8%) | Class | -0.046 (-0.2) | +0.0002 (+0.0) | no |
| MSFT | 1D | +1.4 | -1.0 | — | 52.5% (51.6%) | Global | +0.001 (+0.0) | -0.0001 (-0.5) | no |
| MSFT | 1W | +3.1 | -4.7 | D -8.7 / E +1.8 | 52.3% (52.3%) | Product type | +0.032 (+1.0) | +0.0001 (+0.2) | yes |
| MSFT | 1M | -2.2 | -0.1 | — | 53.9% (54.4%) | Global | +0.038 (+0.6) | +0.0031 (+2.2) | no |
| MSFT | 3M | -7.2 | -11.1 | — | 59.4% (58.7%) | Product type | +0.148 (+1.2) | +0.0041 (+1.4) | no |
| MSFT | 6M | -8.3 | -10.1 | — | 63.0% (61.0%) | Global | +0.060 (+0.3) | +0.0081 (+1.5) | no |
| MSFT | 12M | +7.7 | +1.4 | — | 73.0% (73.7%) | Global | -0.165 (-0.6) | +0.0130 (+1.7) | no |
| MTUM | 1D | -6.9 | -3.2 | — | 53.5% (52.6%) | Global | +0.129 (+3.0) | -0.0002 (-0.6) | no |
| MTUM | 1W | +16.6 | +7.1 | D -2.0 / E +2.3 | 54.5% (54.0%) | Product type | +0.192 (+4.5) | +0.0012 (+2.0) | yes |
| MTUM | 1M | +1.0 | -2.0 | — | 57.1% (57.5%) | Global | +0.165 (+1.7) | +0.0013 (+0.9) | no |
| MTUM | 3M | +9.2 | +4.2 | — | 64.0% (62.6%) | Global | +0.086 (+0.5) | +0.0025 (+0.8) | no |
| MTUM | 6M | -1.3 | -4.5 | — | 69.7% (67.9%) | Global | -0.171 (-0.5) | +0.0029 (+0.4) | no |
| MUB | 1D | -49.6 | +0.8 | — | 53.4% (52.6%) | Global | +0.036 (+1.0) | +0.0002 (+0.6) | no |
| MUB | 1W | -72.7 | +0.1 | D -5.4 / E -8.8 | 55.3% (54.6%) | Global | -0.037 (-1.0) | +0.0010 (+2.3) | yes |
| MUB | 1M | -2.2 | +2.8 | — | 60.7% (60.9%) | Global | -0.042 (-0.5) | +0.0013 (+0.8) | no |
| MUB | 3M | -1.5 | -0.0 | — | 68.8% (67.1%) | Global | -0.223 (-1.6) | +0.0024 (+0.3) | no |
| MUB | 6M | -0.7 | +1.0 | — | 75.4% (74.2%) | Global | -0.204 (-1.0) | -0.0011 (-0.0) | no |
| MUB | 12M | -0.7 | -4.7 | — | 75.9% (75.0%) | Global | -0.404 (-0.9) | -0.0059 (-0.8) | no |
| N225 | 1D | -2.8 | -2.2 | — | 52.1% (51.4%) | Class | +0.026 (+0.8) | -0.0001 (-0.4) | no |
| N225 | 1W | +1.3 | +9.0 | D -3.1 / E +5.8 | 51.2% (51.8%) | Class | +0.046 (+1.4) | +0.0006 (+2.0) | yes |
| N225 | 1M | +2.1 | -5.6 | — | 53.3% (53.8%) | Class | -0.019 (-0.3) | +0.0025 (+1.4) | no |
| N225 | 3M | +10.5 | +5.6 | — | 57.6% (55.9%) | Class | +0.129 (+1.1) | +0.0020 (+0.3) | no |
| N225 | 6M | +2.7 | +1.0 | — | 56.7% (54.0%) | Class | +0.231 (+1.4) | +0.0011 (+0.1) | no |
| N225 | 12M | +4.6 | +3.7 | — | 60.8% (61.1%) | Global | +0.264 (+1.1) | +0.0010 (+0.1) | no |
| NATGAS | 1D | +5.1 | +2.3 | — | 52.0% (51.4%) | Global | +0.047 (+1.4) | +0.0005 (+1.5) | no |
| NATGAS | 1W | -0.2 | -2.5 | D -5.6 / E +8.1 | 51.5% (51.9%) | Global | +0.032 (+0.9) | -0.0008 (-2.4) | yes |
| NATGAS | 1M | +2.9 | +0.4 | — | 53.6% (54.1%) | Global | +0.058 (+0.8) | -0.0030 (-1.6) | no |
| NATGAS | 3M | -5.6 | -1.8 | — | 57.0% (56.4%) | Global | -0.023 (-0.2) | -0.0087 (-1.4) | no |
| NATGAS | 6M | +3.7 | -0.4 | — | 60.0% (57.6%) | Global | +0.062 (+0.4) | -0.0211 (-1.6) | no |
| NATGAS | 12M | +3.2 | +1.3 | — | 64.0% (63.6%) | Global | -0.133 (-0.4) | -0.0209 (-0.8) | no |
| NDX | 1D | -5.1 | -0.8 | — | 54.3% (53.7%) | Global | +0.024 (+0.7) | +0.0000 (+0.1) | no |
| NDX | 1W | -0.1 | -1.4 | D -9.0 / E -3.7 | 56.0% (55.6%) | Global | +0.065 (+1.9) | +0.0002 (+0.4) | yes |
| NDX | 1M | +1.8 | -1.4 | — | 60.2% (60.4%) | Global | +0.056 (+0.8) | +0.0020 (+1.6) | no |
| NDX | 3M | +7.2 | +1.0 | — | 66.4% (64.8%) | Class | -0.059 (-0.5) | +0.0006 (+0.3) | no |
| NDX | 6M | -2.8 | -2.6 | — | 74.2% (73.0%) | Class | -0.001 (-0.0) | -0.0020 (-0.5) | no |
| NDX | 12M | +12.2 | +3.5 | — | 82.9% (81.8%) | Class | +0.164 (+0.6) | +0.0067 (+1.1) | no |
| NFLX | 1D | -0.1 | +0.6 | — | 51.9% (51.1%) | Class | +0.138 (+3.5) | -0.0001 (-0.3) | no |
| NFLX | 1W | -1.8 | -1.8 | D -2.9 / E -0.8 | 51.3% (51.4%) | Global | +0.088 (+2.4) | -0.0002 (-0.5) | yes |
| NFLX | 1M | -6.5 | +3.6 | — | 52.6% (53.1%) | Global | +0.001 (+0.0) | +0.0016 (+0.9) | no |
| NFLX | 3M | -12.3 | -16.6 | — | 56.7% (56.1%) | Global | -0.017 (-0.1) | +0.0072 (+1.5) | no |
| NFLX | 6M | -1.4 | -9.5 | — | 60.1% (58.0%) | Asset | +0.006 (+0.0) | +0.0120 (+1.1) | no |
| NFLX | 12M | -0.7 | +0.7 | — | 68.9% (68.7%) | Global | +0.098 (+0.3) | +0.0084 (+0.7) | no |
| NKE | 1D | +7.2 | +1.5 | — | 52.6% (51.7%) | Asset | +0.014 (+0.4) | -0.0004 (-1.5) | no |
| NKE | 1W | +2.0 | -3.9 | D -7.6 / E -4.4 | 53.4% (52.5%) | Global | +0.028 (+0.8) | +0.0002 (+0.4) | yes |
| NKE | 1M | -13.0 | +6.3 | — | 54.5% (55.0%) | Global | -0.038 (-0.5) | +0.0000 (+0.0) | no |
| NKE | 3M | -22.7 | +0.1 | — | 56.9% (56.4%) | Global | -0.044 (-0.4) | -0.0025 (-0.7) | no |
| NKE | 6M | -25.8 | -5.0 | — | 63.1% (61.1%) | Global | -0.029 (-0.2) | -0.0074 (-1.0) | no |
| NKE | 12M | -7.4 | -0.8 | — | 65.1% (65.7%) | Product type | -0.169 (-0.7) | +0.0084 (+0.6) | no |
| NVDA | 1D | -1.1 | -1.4 | — | 53.2% (52.6%) | Industry | -0.029 (-0.9) | +0.0000 (+0.2) | no |
| NVDA | 1W | -0.8 | -0.5 | D -7.5 / E +1.8 | 54.2% (54.0%) | Global | -0.040 (-1.2) | -0.0000 (-0.1) | yes |
| NVDA | 1M | -3.0 | +4.3 | — | 58.1% (58.4%) | Global | -0.020 (-0.3) | +0.0030 (+1.9) | no |
| NVDA | 3M | -1.9 | -11.8 | — | 62.8% (61.6%) | Class | +0.187 (+1.6) | +0.0079 (+2.4) | no |
| NVDA | 6M | -0.1 | -2.4 | — | 67.6% (65.9%) | Class | +0.247 (+1.4) | +0.0155 (+2.4) | no |
| NVDA | 12M | +5.2 | -3.1 | — | 74.4% (74.9%) | Global | -0.016 (-0.1) | +0.0114 (+1.1) | no |
| NZDUSD | 1D | +0.2 | +1.1 | — | 51.9% (51.4%) | Asset | +0.130 (+3.5) | -0.0002 (-0.7) | no |
| NZDUSD | 1W | +0.4 | -4.4 | D -6.7 / E -7.2 | 51.6% (51.9%) | Global | +0.080 (+2.3) | -0.0001 (-0.4) | yes |
| NZDUSD | 1M | -0.1 | -1.1 | — | 53.6% (54.1%) | Global | +0.025 (+0.3) | -0.0053 (-2.5) | no |
| NZDUSD | 3M | -0.5 | +0.8 | — | 56.9% (56.4%) | Global | -0.162 (-1.2) | -0.0113 (-1.5) | no |
| NZDUSD | 6M | +0.0 | +0.9 | — | 59.9% (57.6%) | Global | -0.079 (-0.4) | -0.0247 (-1.7) | no |
| NZDUSD | 12M | +0.7 | -4.9 | — | 63.3% (63.6%) | Global | +0.043 (+0.1) | +0.0012 (+0.1) | no |
| ORCL | 1D | +3.8 | -0.0 | — | 52.9% (51.9%) | Global | +0.069 (+2.0) | +0.0001 (+0.4) | no |
| ORCL | 1W | +2.7 | -3.2 | D -6.6 / E +2.3 | 53.8% (52.8%) | Global | +0.090 (+2.7) | -0.0003 (-0.8) | yes |
| ORCL | 1M | -0.1 | +3.6 | — | 54.0% (54.5%) | Sector | +0.160 (+2.4) | -0.0010 (-0.7) | no |
| ORCL | 3M | +3.1 | -3.2 | — | 57.2% (56.5%) | Product type | +0.096 (+0.8) | +0.0006 (+0.2) | no |
| ORCL | 6M | +2.3 | -11.7 | — | 61.3% (58.7%) | Global | +0.147 (+0.8) | -0.0009 (-0.1) | no |
| ORCL | 12M | -3.2 | +0.3 | — | 57.2% (61.3%) | Product type | -0.176 (-0.7) | -0.0030 (-0.2) | no |
| PEP | 1D | +72.6 | +3.4 | — | 51.3% (50.6%) | Global | +0.053 (+1.6) | +0.0002 (+0.9) | no |
| PEP | 1W | +17.2 | -0.7 | D -4.6 / E +0.7 | 50.4% (50.6%) | Global | +0.064 (+1.9) | -0.0001 (-0.2) | yes |
| PEP | 1M | +1.5 | +2.9 | — | 50.6% (51.2%) | Global | +0.041 (+0.6) | +0.0009 (+0.6) | no |
| PEP | 3M | +8.0 | +2.5 | — | 54.7% (53.7%) | Global | +0.131 (+1.1) | +0.0012 (+0.4) | no |
| PEP | 6M | +5.2 | +7.2 | — | 59.6% (57.2%) | Global | +0.079 (+0.5) | +0.0028 (+0.5) | no |
| PEP | 12M | +2.1 | -5.8 | — | 61.2% (63.1%) | Class | -0.117 (-0.5) | +0.0169 (+1.6) | no |
| PFE | 1D | +0.4 | -2.0 | — | 52.4% (51.6%) | Asset | -0.020 (-0.6) | -0.0002 (-0.7) | no |
| PFE | 1W | +3.7 | -0.2 | D -6.7 / E -0.6 | 52.2% (52.3%) | Global | -0.013 (-0.4) | +0.0000 (+0.0) | yes |
| PFE | 1M | +1.4 | -4.1 | — | 54.5% (54.8%) | Global | -0.031 (-0.4) | -0.0018 (-1.2) | no |
| PFE | 3M | -0.9 | +6.6 | — | 59.2% (58.1%) | Class | +0.027 (+0.2) | -0.0026 (-0.8) | no |
| PFE | 6M | +8.3 | +5.5 | — | 63.7% (61.6%) | Global | +0.118 (+0.7) | -0.0047 (-0.7) | no |
| PFE | 12M | -23.5 | -2.0 | — | 65.1% (65.4%) | Global | +0.092 (+0.4) | +0.0064 (+0.6) | no |
| PFF | 1D | -20.6 | -1.4 | — | 50.6% (49.8%) | Global | +0.119 (+3.2) | +0.0001 (+0.3) | no |
| PFF | 1W | -2.8 | -2.7 | D -2.5 / E -2.1 | 55.6% (54.9%) | Class | +0.126 (+3.4) | +0.0001 (+0.1) | yes |
| PFF | 1M | -0.4 | -0.1 | — | 58.7% (59.0%) | Global | +0.101 (+1.3) | +0.0004 (+0.3) | no |
| PFF | 3M | +0.3 | +1.2 | — | 64.9% (63.7%) | Global | -0.039 (-0.3) | +0.0020 (+0.2) | no |
| PFF | 6M | +0.8 | +1.0 | — | 69.6% (68.0%) | Global | -0.024 (-0.1) | -0.0142 (-1.0) | no |
| PFF | 12M | +0.3 | -1.2 | — | 72.1% (71.6%) | Product type | +0.064 (+0.1) | -0.0083 (-1.1) | no |
| PG | 1D | +1.0 | +0.7 | — | 51.8% (51.0%) | Global | -0.027 (-0.8) | -0.0000 (-0.1) | no |
| PG | 1W | -6.4 | -4.9 | D -8.6 / E -1.1 | 51.2% (51.3%) | Product type | +0.019 (+0.6) | +0.0000 (+0.0) | yes |
| PG | 1M | +9.8 | +6.0 | — | 51.9% (52.5%) | Global | +0.049 (+0.7) | +0.0011 (+0.7) | no |
| PG | 3M | -1.0 | -3.6 | — | 54.7% (54.2%) | Global | +0.172 (+1.4) | +0.0008 (+0.3) | no |
| PG | 6M | +6.5 | -3.2 | — | 59.5% (57.2%) | Global | +0.299 (+1.8) | -0.0006 (-0.1) | no |
| PG | 12M | +0.1 | -6.5 | — | 63.6% (64.8%) | Global | -0.298 (-1.2) | +0.0054 (+0.5) | no |
| PLATINUM | 1D | -0.4 | -2.8 | — | 52.2% (51.4%) | Global | -0.004 (-0.1) | -0.0003 (-1.1) | no |
| PLATINUM | 1W | -0.1 | +7.5 | D -2.4 / E -6.4 | 51.8% (51.9%) | Class | +0.090 (+2.6) | -0.0009 (-2.7) | yes |
| PLATINUM | 1M | -1.7 | -2.7 | — | 53.6% (54.1%) | Global | +0.024 (+0.3) | -0.0048 (-2.5) | no |
| PLATINUM | 3M | -5.7 | -0.6 | — | 57.9% (56.4%) | Global | -0.112 (-0.9) | -0.0155 (-2.1) | no |
| PLATINUM | 6M | +5.6 | +1.6 | — | 60.2% (57.6%) | Global | -0.008 (-0.0) | -0.0332 (-2.2) | no |
| PLATINUM | 12M | +8.4 | +4.4 | — | 63.6% (63.6%) | Global | -0.226 (-0.6) | -0.0010 (-0.1) | no |
| PSQ | 1D | +5.4 | -10.7 | — | 49.2% (48.5%) | Sector | -0.076 (-2.0) | -0.0012 (-1.6) | no |
| PSQ | 1W | -8.0 | +0.7 | D -12.5 / E -2.7 | 45.9% (47.3%) | Global | -0.050 (-1.3) | +0.0002 (+0.7) | yes |
| PSQ | 1M | -0.3 | +2.2 | — | 43.9% (44.8%) | Global | +0.017 (+0.2) | -0.0122 (-4.4) | no |
| PSQ | 3M | -5.7 | -4.7 | — | 43.6% (43.8%) | Global | -0.054 (-0.4) | -0.0740 (-4.1) | no |
| PSQ | 6M | +2.3 | +2.7 | — | 39.6% (36.6%) | Product type | -0.048 (-0.2) | -0.1061 (-3.0) | no |
| PSQ | 12M | -7.9 | -1.0 | — | 34.9% (38.0%) | Class | -0.469 (-1.1) | +0.0246 (+1.1) | no |
| QCOM | 1D | +4.5 | +3.5 | — | 53.1% (52.3%) | Industry | +0.033 (+1.0) | +0.0001 (+0.6) | no |
| QCOM | 1W | -3.0 | -1.3 | D -10.7 / E -0.9 | 53.6% (53.4%) | Global | +0.033 (+1.0) | -0.0000 (-0.1) | yes |
| QCOM | 1M | +0.3 | +0.2 | — | 53.9% (54.4%) | Industry | +0.001 (+0.0) | -0.0014 (-0.9) | no |
| QCOM | 3M | -0.4 | -0.7 | — | 55.7% (54.7%) | Global | -0.112 (-0.9) | -0.0029 (-0.7) | no |
| QCOM | 6M | +1.2 | -1.0 | — | 68.2% (66.6%) | Global | +0.142 (+0.8) | -0.0104 (-1.4) | no |
| QCOM | 12M | -0.8 | -3.2 | — | 73.7% (73.0%) | Global | -0.149 (-0.6) | -0.0126 (-0.8) | no |
| QLD | 1D | -2.1 | -1.9 | — | 53.9% (53.2%) | Class | +0.151 (+3.9) | +0.0002 (+0.8) | no |
| QLD | 1W | +1.0 | +3.5 | D -9.6 / E +0.3 | 55.0% (54.7%) | Asset | +0.169 (+4.3) | +0.0002 (+0.3) | yes |
| QLD | 1M | +0.5 | -0.1 | — | 58.6% (58.8%) | Global | +0.109 (+1.4) | +0.0031 (+1.9) | no |
| QLD | 3M | +5.0 | +1.6 | — | 64.4% (63.0%) | Global | -0.119 (-0.9) | +0.0013 (+0.4) | no |
| QLD | 6M | -2.7 | -2.5 | — | 72.9% (71.5%) | Sector | -0.119 (-0.6) | -0.0017 (-0.3) | no |
| QLD | 12M | +7.6 | +2.8 | — | 82.0% (81.0%) | Class | -0.536 (-1.4) | -0.0021 (-0.4) | no |
| QQQ | 1D | -16.9 | -4.0 | — | 54.5% (53.7%) | Product type | +0.176 (+5.3) | +0.0000 (+0.2) | no |
| QQQ | 1W | +8.9 | +4.2 | D -2.0 / E +0.1 | 56.2% (55.7%) | Asset | +0.229 (+7.0) | +0.0008 (+1.9) | yes |
| QQQ | 1M | +1.8 | -0.8 | — | 60.1% (60.2%) | Global | +0.118 (+1.7) | +0.0021 (+1.8) | no |
| QQQ | 3M | +2.6 | +4.0 | — | 66.5% (65.0%) | Global | -0.114 (-0.9) | +0.0001 (+0.1) | no |
| QQQ | 6M | -3.0 | +0.9 | — | 74.7% (73.4%) | Class | -0.062 (-0.3) | -0.0018 (-0.4) | no |
| QQQ | 12M | +8.4 | +2.2 | — | 82.9% (81.8%) | Class | -0.095 (-0.3) | +0.0093 (+1.3) | no |
| QUAL | 1D | -0.1 | -0.3 | — | 55.2% (54.6%) | Asset | +0.150 (+3.4) | -0.0000 (-0.0) | no |
| QUAL | 1W | +0.6 | +0.5 | D -9.9 / E -3.6 | 57.7% (56.9%) | Asset | +0.123 (+2.8) | +0.0009 (+1.4) | yes |
| QUAL | 1M | +0.2 | +0.2 | — | 63.9% (63.9%) | Global | -0.082 (-0.8) | +0.0007 (+0.5) | no |
| QUAL | 3M | +0.7 | +1.4 | — | 68.2% (66.8%) | Product type | -0.313 (-1.9) | +0.0026 (+0.8) | no |
| QUAL | 6M | -0.1 | -0.8 | — | 74.4% (73.2%) | Product type | +0.056 (+0.2) | +0.0054 (+0.8) | no |
| RUT | 1D | -0.1 | +1.5 | — | 55.2% (54.7%) | Global | +0.101 (+3.0) | +0.0001 (+0.7) | no |
| RUT | 1W | +2.2 | +0.5 | D -7.7 / E -3.2 | 57.9% (57.3%) | Global | +0.053 (+1.6) | -0.0006 (-1.5) | yes |
| RUT | 1M | -0.3 | -2.2 | — | 63.0% (63.0%) | Global | +0.110 (+1.6) | -0.0005 (-0.4) | no |
| RUT | 3M | -6.3 | +3.3 | — | 67.8% (65.9%) | Global | +0.044 (+0.4) | -0.0002 (-0.1) | no |
| RUT | 6M | +1.0 | -0.1 | — | 70.8% (69.3%) | Product type | +0.132 (+0.8) | -0.0039 (-0.8) | no |
| RUT | 12M | +0.1 | -1.8 | — | 75.0% (74.0%) | Product type | -0.167 (-0.6) | +0.0002 (+0.0) | no |
| SCHP | 1D | +1.9 | +0.4 | — | 56.5% (55.8%) | Product type | +0.194 (+4.8) | -0.0003 (-0.8) | no |
| SCHP | 1W | +12.4 | +0.1 | D -0.6 / E -1.7 | 60.8% (59.3%) | Product type | +0.207 (+5.2) | -0.0009 (-1.6) | yes |
| SCHP | 1M | -1.9 | +1.6 | — | 68.4% (68.2%) | Global | +0.090 (+1.0) | +0.0008 (+0.4) | no |
| SCHP | 3M | +0.3 | +2.0 | — | 73.5% (71.8%) | Global | +0.127 (+0.8) | -0.0008 (-0.2) | no |
| SCHP | 6M | -1.0 | +1.4 | — | 81.8% (81.1%) | Global | +0.094 (+0.4) | -0.0054 (-0.7) | no |
| SCHP | 12M | +3.6 | -1.7 | — | 80.4% (79.3%) | Global | -0.345 (-0.4) | +0.0060 (+2.0) | no |
| SDS | 1D | +0.2 | -8.2 | — | 48.4% (47.7%) | Asset | -0.105 (-2.9) | -0.0008 (-1.1) | no |
| SDS | 1W | -19.6 | -0.5 | D -14.0 / E -2.5 | 44.2% (45.9%) | Sector | -0.073 (-1.9) | +0.0005 (+1.6) | yes |
| SDS | 1M | +1.0 | +3.0 | — | 41.1% (42.2%) | Class | +0.042 (+0.6) | -0.0116 (-4.0) | no |
| SDS | 3M | -5.3 | -2.8 | — | 41.5% (41.7%) | Global | -0.096 (-0.7) | -0.0736 (-3.9) | no |
| SDS | 6M | +2.6 | +1.7 | — | 36.7% (33.7%) | Sector | -0.149 (-0.7) | -0.1091 (-2.8) | no |
| SDS | 12M | -9.1 | -0.9 | — | 33.2% (36.0%) | Product type | -0.418 (-1.0) | +0.0290 (+1.4) | no |
| SH | 1D | +1.0 | -8.3 | — | 48.6% (47.9%) | Asset | -0.096 (-2.6) | -0.0013 (-1.8) | no |
| SH | 1W | -12.8 | +0.0 | D -13.7 / E -2.2 | 44.6% (46.3%) | Sector | -0.058 (-1.5) | +0.0004 (+1.3) | yes |
| SH | 1M | +0.4 | +3.1 | — | 41.9% (42.9%) | Class | +0.037 (+0.5) | -0.0114 (-3.9) | no |
| SH | 3M | -5.6 | -2.7 | — | 42.7% (42.9%) | Class | -0.077 (-0.6) | -0.0699 (-3.7) | no |
| SH | 6M | +5.3 | +1.5 | — | 38.3% (35.2%) | Sector | -0.148 (-0.7) | -0.1038 (-2.8) | no |
| SH | 12M | -8.6 | +0.3 | — | 34.3% (37.2%) | Product type | -0.466 (-1.1) | +0.0252 (+1.2) | no |
| SHY | 1D | +43.5 | +0.7 | — | 62.0% (61.3%) | Class | +0.156 (+4.5) | +0.0003 (+0.9) | no |
| SHY | 1W | +1.3 | +3.2 | D +2.3 / E +0.5 | 71.8% (68.5%) | Class | +0.061 (+1.7) | -0.0019 (-2.5) | yes |
| SHY | 1M | +5.0 | -1.7 | — | 82.8% (82.2%) | Global | -0.009 (-0.1) | -0.0011 (-0.9) | no |
| SHY | 3M | -4.1 | +0.4 | — | 88.3% (86.8%) | Global | +0.002 (+0.0) | -0.0012 (-0.5) | no |
| SHY | 6M | +10.8 | +1.5 | — | 96.4% (96.5%) | Global | +0.017 (+0.1) | -0.0068 (-1.4) | no |
| SHY | 12M | +11.8 | +2.5 | — | 93.6% (92.2%) | Global | -0.132 (-0.4) | -0.0004 (-0.1) | no |
| SILVER | 1D | +0.7 | +0.4 | — | 52.2% (51.4%) | Class | +0.040 (+1.2) | -0.0003 (-0.9) | no |
| SILVER | 1W | -0.7 | +9.0 | D +1.0 / E +3.2 | 51.7% (51.9%) | Sector | +0.074 (+2.2) | -0.0006 (-1.9) | yes |
| SILVER | 1M | -1.1 | -1.8 | — | 53.6% (54.1%) | Global | +0.029 (+0.4) | -0.0053 (-2.8) | no |
| SILVER | 3M | -1.4 | +4.0 | — | 57.8% (56.4%) | Global | -0.109 (-0.9) | -0.0111 (-1.6) | no |
| SILVER | 6M | +0.7 | +4.7 | — | 60.2% (57.6%) | Global | -0.036 (-0.2) | -0.0237 (-1.8) | no |
| SILVER | 12M | +9.1 | +3.8 | — | 63.3% (63.6%) | Global | +0.304 (+1.1) | -0.0414 (-1.5) | no |
| SLV | 1D | -0.1 | -2.3 | — | 52.2% (51.4%) | Global | +0.052 (+1.4) | +0.0001 (+0.2) | no |
| SLV | 1W | -0.7 | +7.8 | D +0.9 / E +5.8 | 51.7% (51.9%) | Sector | +0.156 (+4.1) | -0.0013 (-3.2) | yes |
| SLV | 1M | -2.2 | -1.4 | — | 53.6% (54.1%) | Global | -0.014 (-0.2) | -0.0056 (-2.6) | no |
| SLV | 3M | -2.2 | +3.7 | — | 58.0% (56.4%) | Global | -0.036 (-0.3) | -0.0129 (-1.6) | no |
| SLV | 6M | +9.6 | +5.3 | — | 60.2% (57.6%) | Global | -0.024 (-0.1) | -0.0270 (-1.6) | no |
| SLV | 12M | +6.2 | +3.2 | — | 63.2% (63.6%) | Class | -0.038 (-0.1) | -0.0002 (-0.0) | no |
| SMH | 1D | -22.2 | -2.2 | — | 53.4% (52.7%) | Global | +0.018 (+0.5) | +0.0000 (+0.3) | no |
| SMH | 1W | -2.0 | +4.7 | D -8.6 / E -3.0 | 53.9% (54.0%) | Global | +0.051 (+1.5) | +0.0004 (+1.0) | yes |
| SMH | 1M | +5.9 | -4.6 | — | 56.8% (57.1%) | Global | -0.000 (-0.0) | +0.0015 (+1.1) | no |
| SMH | 3M | +24.4 | +5.1 | — | 62.9% (61.0%) | Global | -0.022 (-0.2) | +0.0031 (+1.3) | no |
| SMH | 6M | +4.6 | +2.6 | — | 69.1% (67.4%) | Class | -0.029 (-0.2) | +0.0027 (+0.6) | no |
| SMH | 12M | +4.4 | +2.5 | — | 78.8% (77.7%) | Asset | -0.107 (-0.4) | +0.0072 (+0.7) | no |
| SOYBEANS | 1D | +0.1 | +0.8 | — | 52.1% (51.4%) | Class | +0.031 (+0.9) | -0.0005 (-1.7) | no |
| SOYBEANS | 1W | +0.5 | -0.9 | D -5.2 / E +0.7 | 51.5% (51.9%) | Global | -0.009 (-0.3) | -0.0003 (-1.0) | yes |
| SOYBEANS | 1M | +2.1 | -2.7 | — | 53.7% (54.1%) | Global | -0.029 (-0.4) | -0.0030 (-1.6) | no |
| SOYBEANS | 3M | -3.7 | +3.3 | — | 57.3% (56.4%) | Global | -0.079 (-0.7) | -0.0109 (-1.6) | no |
| SOYBEANS | 6M | +0.4 | +3.2 | — | 59.8% (57.6%) | Global | -0.091 (-0.5) | -0.0263 (-2.0) | no |
| SOYBEANS | 12M | +3.6 | -1.7 | — | 62.9% (63.6%) | Class | -0.357 (-1.3) | -0.0417 (-1.4) | no |
| SPX | 1D | +2.1 | -0.1 | — | 55.2% (54.6%) | Global | +0.099 (+3.0) | +0.0002 (+0.9) | no |
| SPX | 1W | +7.7 | -1.8 | D -7.8 / E -3.3 | 57.6% (57.1%) | Class | +0.079 (+2.4) | -0.0000 (-0.0) | yes |
| SPX | 1M | +5.7 | -1.3 | — | 63.4% (63.4%) | Class | -0.001 (-0.0) | +0.0013 (+1.2) | no |
| SPX | 3M | +7.5 | +1.6 | — | 68.7% (66.9%) | Class | -0.148 (-1.2) | +0.0006 (+0.2) | no |
| SPX | 6M | -1.4 | -2.0 | — | 75.8% (74.7%) | Product type | +0.023 (+0.1) | -0.0034 (-0.7) | no |
| SPX | 12M | +26.1 | +2.8 | — | 84.0% (82.8%) | Global | -0.053 (-0.2) | +0.0033 (+0.5) | no |
| SPY | 1D | +3.2 | -0.3 | — | 55.1% (54.5%) | Global | +0.071 (+2.1) | +0.0002 (+0.7) | no |
| SPY | 1W | +9.0 | -1.1 | D -8.1 / E -4.3 | 57.6% (57.0%) | Class | +0.071 (+2.1) | +0.0001 (+0.3) | yes |
| SPY | 1M | +6.1 | -1.2 | — | 63.3% (63.3%) | Class | +0.014 (+0.2) | +0.0016 (+1.4) | no |
| SPY | 3M | +9.9 | +1.5 | — | 68.6% (66.9%) | Class | -0.127 (-1.1) | +0.0003 (+0.1) | no |
| SPY | 6M | -3.1 | -2.0 | — | 76.6% (75.5%) | Class | -0.212 (-1.2) | -0.0032 (-0.6) | no |
| SPY | 12M | +26.4 | +2.7 | — | 84.7% (83.3%) | Global | -0.102 (-0.4) | +0.0057 (+0.9) | no |
| SQQQ | 1D | +15.9 | -15.6 | — | 48.6% (47.8%) | Asset | -0.069 (-1.8) | -0.0003 (-0.4) | no |
| SQQQ | 1W | -26.0 | -0.4 | D -10.0 / E -3.5 | 44.3% (45.9%) | Global | -0.042 (-1.1) | +0.0000 (+0.0) | yes |
| SQQQ | 1M | -2.6 | +0.7 | — | 40.9% (41.9%) | Global | +0.031 (+0.4) | -0.0131 (-4.4) | no |
| SQQQ | 3M | -0.5 | -2.7 | — | 39.7% (40.1%) | Class | -0.065 (-0.5) | -0.0683 (-3.6) | no |
| SQQQ | 6M | +10.1 | +6.0 | — | 35.7% (32.6%) | Product type | -0.078 (-0.3) | -0.0111 (-1.7) | no |
| SQQQ | 12M | -6.7 | -4.6 | — | 31.5% (34.6%) | Class | +0.147 (+0.2) | +0.0447 (+2.8) | no |
| SSO | 1D | -1.4 | -1.0 | — | 55.0% (54.4%) | Class | +0.171 (+4.6) | +0.0002 (+0.8) | no |
| SSO | 1W | +2.9 | +3.8 | D -8.6 / E -0.2 | 57.1% (56.6%) | Asset | +0.234 (+5.9) | +0.0006 (+1.1) | yes |
| SSO | 1M | +0.8 | +0.0 | — | 62.6% (62.6%) | Class | +0.116 (+1.4) | +0.0016 (+1.0) | no |
| SSO | 3M | +5.2 | +1.7 | — | 67.4% (65.9%) | Class | -0.208 (-1.4) | +0.0023 (+0.7) | no |
| SSO | 6M | -4.5 | -0.9 | — | 75.1% (73.9%) | Sector | -0.256 (-1.1) | -0.0015 (-0.2) | no |
| SSO | 12M | +8.5 | +2.3 | — | 83.5% (82.1%) | Sector | -0.148 (-0.3) | -0.0034 (-0.5) | no |
| SX5E | 1D | +3.3 | -0.1 | — | 53.9% (53.3%) | Class | +0.080 (+2.1) | +0.0003 (+1.0) | no |
| SX5E | 1W | +5.7 | +1.0 | D -0.1 / E -1.7 | 55.4% (55.3%) | Sector | +0.133 (+3.5) | +0.0001 (+0.2) | yes |
| SX5E | 1M | -0.9 | -4.5 | — | 60.6% (60.7%) | Global | +0.082 (+1.0) | -0.0020 (-1.0) | no |
| SX5E | 3M | -0.7 | +3.5 | — | 62.7% (61.4%) | Global | -0.000 (-0.0) | -0.0019 (-0.4) | no |
| SX5E | 6M | -0.1 | +2.5 | — | 61.4% (59.2%) | Class | +0.108 (+0.5) | -0.0095 (-0.9) | no |
| SX5E | 12M | -1.0 | +4.5 | — | 65.8% (65.7%) | Global | +0.456 (+1.0) | -0.0031 (-0.9) | no |
| T | 1D | +0.5 | -2.2 | — | 51.3% (50.4%) | Industry | +0.027 (+0.8) | +0.0002 (+0.8) | no |
| T | 1W | -2.6 | -7.3 | D -12.7 / E -7.6 | 50.1% (50.3%) | Global | +0.003 (+0.1) | -0.0004 (-1.0) | yes |
| T | 1M | -3.9 | +1.0 | — | 50.0% (50.6%) | Global | +0.022 (+0.3) | -0.0008 (-0.5) | no |
| T | 3M | -1.2 | -6.3 | — | 52.7% (52.2%) | Global | +0.013 (+0.1) | -0.0001 (-0.0) | no |
| T | 6M | -0.7 | -4.1 | — | 58.0% (55.5%) | Class | -0.018 (-0.1) | -0.0050 (-0.7) | no |
| T | 12M | +4.9 | +7.8 | — | 62.6% (63.7%) | Global | -0.011 (-0.0) | +0.0043 (+0.4) | no |
| TIP | 1D | +3.7 | +1.5 | — | 56.4% (55.7%) | Class | +0.094 (+2.8) | +0.0006 (+1.9) | no |
| TIP | 1W | +9.3 | +1.2 | D -3.0 / E -9.6 | 60.7% (59.1%) | Global | +0.083 (+2.4) | -0.0010 (-2.2) | yes |
| TIP | 1M | -1.5 | -0.6 | — | 68.5% (68.3%) | Global | +0.045 (+0.6) | -0.0004 (-0.3) | no |
| TIP | 3M | -0.1 | -1.1 | — | 74.6% (72.9%) | Global | +0.027 (+0.2) | -0.0033 (-0.8) | no |
| TIP | 6M | +0.4 | -1.9 | — | 81.0% (80.3%) | Global | +0.011 (+0.1) | -0.0102 (-1.2) | no |
| TIP | 12M | +1.0 | -0.8 | — | 81.4% (80.3%) | Global | -0.217 (-0.6) | +0.0023 (+0.3) | no |
| TLT | 1D | +3.8 | +0.2 | — | 53.7% (53.0%) | Class | +0.187 (+5.7) | +0.0004 (+1.0) | no |
| TLT | 1W | +2.7 | -3.0 | D -2.0 / E -3.2 | 55.4% (54.8%) | Class | +0.123 (+3.7) | -0.0003 (-0.9) | yes |
| TLT | 1M | -1.0 | +3.2 | — | 60.0% (60.2%) | Global | +0.011 (+0.2) | -0.0021 (-1.2) | no |
| TLT | 3M | +1.0 | +0.4 | — | 65.1% (64.0%) | Global | -0.075 (-0.6) | -0.0029 (-0.6) | no |
| TLT | 6M | -0.0 | +1.7 | — | 69.3% (67.8%) | Global | -0.087 (-0.5) | -0.0092 (-0.9) | no |
| TLT | 12M | -0.4 | -5.6 | — | 71.1% (70.5%) | Global | -0.296 (-0.9) | -0.0072 (-0.3) | no |
| TQQQ | 1D | -1.3 | -3.1 | — | 53.7% (52.9%) | Class | +0.110 (+2.9) | +0.0001 (+0.2) | no |
| TQQQ | 1W | +3.8 | +2.2 | D -5.8 / E -1.0 | 54.6% (54.4%) | Class | +0.149 (+3.9) | +0.0011 (+2.0) | yes |
| TQQQ | 1M | +1.3 | -0.5 | — | 57.1% (57.5%) | Global | +0.123 (+1.6) | +0.0030 (+1.8) | no |
| TQQQ | 3M | +0.4 | +3.2 | — | 63.0% (61.8%) | Global | -0.133 (-0.9) | +0.0023 (+0.6) | no |
| TQQQ | 6M | -6.1 | -2.5 | — | 71.5% (70.1%) | Asset | -0.161 (-0.7) | +0.0009 (+0.1) | no |
| TQQQ | 12M | +3.3 | -0.4 | — | 81.6% (80.6%) | Global | -0.175 (-0.2) | +0.0015 (+0.3) | no |
| TSLA | 1D | -0.0 | -1.1 | — | 52.8% (52.0%) | Global | +0.029 (+0.7) | -0.0001 (-0.4) | no |
| TSLA | 1W | -0.2 | +0.5 | D -6.9 / E -3.6 | 53.2% (53.0%) | Product type | +0.072 (+1.6) | -0.0006 (-1.1) | yes |
| TSLA | 1M | -0.9 | -0.1 | — | 56.0% (56.3%) | Sector | -0.028 (-0.3) | +0.0000 (+0.0) | no |
| TSLA | 3M | -2.3 | -3.6 | — | 62.2% (61.3%) | Industry | +0.054 (+0.3) | +0.0009 (+0.2) | no |
| TSLA | 6M | -0.8 | -0.4 | — | 70.2% (68.7%) | Global | -0.414 (-1.8) | -0.0055 (-0.6) | no |
| TSLA | 12M | -1.7 | -1.5 | — | 71.2% (70.1%) | Product type | +0.107 (+0.1) | +0.0093 (+1.0) | no |
| TXN | 1D | -5.5 | -2.2 | — | 52.7% (52.0%) | Industry | +0.020 (+0.6) | +0.0000 (+0.0) | no |
| TXN | 1W | -2.6 | +0.3 | D -10.8 / E -2.5 | 52.7% (52.8%) | Global | +0.086 (+2.6) | +0.0010 (+2.1) | yes |
| TXN | 1M | +0.6 | +1.7 | — | 54.5% (55.0%) | Industry | +0.070 (+1.0) | +0.0009 (+0.6) | no |
| TXN | 3M | +17.2 | +9.6 | — | 57.3% (56.0%) | Product type | -0.091 (-0.8) | +0.0024 (+0.9) | no |
| TXN | 6M | +0.6 | +6.2 | — | 65.1% (63.0%) | Product type | -0.200 (-1.2) | +0.0036 (+0.6) | no |
| TXN | 12M | -0.8 | -1.0 | — | 65.9% (68.0%) | Product type | -0.060 (-0.2) | +0.0117 (+1.4) | no |
| UNG | 1D | -0.2 | -0.4 | — | 52.0% (51.4%) | Global | +0.012 (+0.3) | +0.0008 (+2.1) | no |
| UNG | 1W | -0.1 | -2.0 | D -6.8 / E +5.8 | 51.4% (51.9%) | Global | -0.035 (-0.9) | -0.0008 (-1.8) | yes |
| UNG | 1M | -0.1 | -0.7 | — | 53.6% (54.1%) | Global | +0.004 (+0.1) | -0.0064 (-2.9) | no |
| UNG | 3M | -5.4 | -1.6 | — | 57.3% (56.4%) | Global | -0.032 (-0.2) | -0.0142 (-1.9) | no |
| UNG | 6M | -0.1 | -2.3 | — | 60.0% (57.6%) | Global | +0.019 (+0.1) | -0.0296 (-2.1) | no |
| UNG | 12M | +3.4 | +1.2 | — | 64.0% (63.6%) | Global | +0.034 (+0.1) | +0.0049 (+0.7) | no |
| UNH | 1D | +3.2 | -0.5 | — | 52.4% (51.7%) | Global | +0.016 (+0.5) | +0.0001 (+0.4) | no |
| UNH | 1W | +40.3 | +1.7 | D -1.9 / E +1.8 | 52.2% (52.5%) | Class | +0.026 (+0.8) | +0.0004 (+1.0) | yes |
| UNH | 1M | +11.9 | +1.4 | — | 54.6% (55.0%) | Global | +0.023 (+0.3) | +0.0027 (+1.8) | no |
| UNH | 3M | +4.4 | +5.7 | — | 58.6% (57.5%) | Class | -0.003 (-0.0) | +0.0071 (+2.5) | no |
| UNH | 6M | -15.8 | -2.4 | — | 56.0% (53.4%) | Sector | +0.057 (+0.3) | +0.0095 (+1.6) | no |
| UNH | 12M | -24.2 | -8.7 | — | 60.9% (60.4%) | Global | -0.001 (-0.0) | +0.0165 (+2.3) | no |
| USDCAD | 1D | -0.2 | -0.3 | — | 52.0% (51.4%) | Asset | -0.038 (-1.1) | -0.0001 (-0.2) | no |
| USDCAD | 1W | -0.0 | -1.0 | D -10.0 / E -5.6 | 51.4% (51.9%) | Class | -0.101 (-2.9) | +0.0001 (+0.3) | yes |
| USDCAD | 1M | -0.1 | +4.6 | — | 53.6% (54.1%) | Product type | -0.018 (-0.2) | +0.0006 (+0.3) | no |
| USDCAD | 3M | +0.1 | +0.6 | — | 57.2% (56.4%) | Global | -0.177 (-1.4) | +0.0012 (+0.2) | no |
| USDCAD | 6M | +0.0 | +2.2 | — | 59.8% (57.6%) | Global | -0.128 (-0.7) | +0.0106 (+0.9) | no |
| USDCAD | 12M | -1.2 | +2.6 | — | 63.0% (63.6%) | Global | +0.065 (+0.2) | -0.0140 (-0.8) | no |
| USDCHF | 1D | -0.6 | -2.8 | — | 52.1% (51.4%) | Asset | -0.036 (-1.1) | +0.0000 (+0.2) | no |
| USDCHF | 1W | -0.1 | +1.9 | D -10.2 / E -1.9 | 51.4% (51.9%) | Class | -0.018 (-0.5) | -0.0000 (-0.1) | yes |
| USDCHF | 1M | -0.1 | +0.4 | — | 53.6% (54.1%) | Global | -0.009 (-0.1) | -0.0027 (-1.4) | no |
| USDCHF | 3M | +0.3 | -0.7 | — | 57.0% (56.4%) | Global | -0.145 (-1.2) | -0.0087 (-1.2) | no |
| USDCHF | 6M | -0.0 | -0.6 | — | 59.8% (57.6%) | Global | +0.092 (+0.5) | -0.0167 (-1.2) | no |
| USDCHF | 12M | +4.7 | -1.8 | — | 63.3% (63.6%) | Asset | +0.015 (+0.0) | -0.0071 (-0.4) | no |
| USDCNY | 1D | +13.2 | +3.3 | — | 52.0% (51.4%) | Product type | -0.002 (-0.1) | -0.0003 (-1.2) | no |
| USDCNY | 1W | -16.6 | -5.9 | D -5.7 / E -5.3 | 51.8% (51.9%) | Product type | -0.143 (-4.3) | -0.0000 (-0.1) | yes |
| USDCNY | 1M | -47.0 | +4.3 | — | 53.5% (54.1%) | Global | -0.132 (-1.9) | -0.0030 (-1.7) | no |
| USDCNY | 3M | -23.9 | -4.6 | — | 56.8% (56.4%) | Global | -0.161 (-1.3) | -0.0069 (-1.0) | no |
| USDCNY | 6M | -6.1 | -8.8 | — | 60.0% (57.6%) | Global | -0.297 (-1.7) | -0.0008 (-0.1) | no |
| USDCNY | 12M | -7.9 | +2.4 | — | 63.4% (63.6%) | Global | -0.036 (-0.1) | +0.0027 (+0.2) | no |
| USDINR | 1D | -13.1 | -1.5 | — | 52.2% (51.4%) | Product type | +0.023 (+0.6) | +0.0001 (+0.5) | no |
| USDINR | 1W | +2.4 | +7.1 | D -2.4 / E +0.1 | 51.9% (51.9%) | Product type | -0.109 (-3.0) | -0.0001 (-0.2) | yes |
| USDINR | 1M | +1.3 | -2.4 | — | 53.6% (54.1%) | Class | +0.067 (+0.9) | +0.0005 (+0.2) | no |
| USDINR | 3M | +3.5 | +4.0 | — | 57.4% (56.4%) | Global | +0.189 (+1.4) | +0.0030 (+0.4) | no |
| USDINR | 6M | +3.5 | +3.5 | — | 59.9% (57.6%) | Global | -0.033 (-0.2) | +0.0126 (+0.9) | no |
| USDINR | 12M | +0.7 | +2.2 | — | 63.0% (63.6%) | Global | +0.120 (+0.3) | -0.0127 (-1.0) | no |
| USDJPY | 1D | -0.9 | -1.0 | — | 52.1% (51.4%) | Global | +0.011 (+0.3) | +0.0001 (+0.3) | no |
| USDJPY | 1W | -0.7 | +3.1 | D -4.3 / E -2.2 | 51.8% (51.9%) | Class | -0.038 (-1.1) | -0.0002 (-0.6) | yes |
| USDJPY | 1M | +1.2 | +0.0 | — | 53.6% (54.1%) | Global | -0.088 (-1.3) | -0.0000 (-0.0) | no |
| USDJPY | 3M | +8.7 | +2.1 | — | 57.6% (56.4%) | Global | -0.024 (-0.2) | -0.0019 (-0.3) | no |
| USDJPY | 6M | +3.7 | +2.8 | — | 59.8% (57.6%) | Global | +0.085 (+0.5) | -0.0025 (-0.2) | no |
| USDJPY | 12M | +1.5 | +1.9 | — | 63.4% (63.6%) | Global | +0.314 (+1.2) | -0.0074 (-0.4) | no |
| USDMXN | 1D | -2.8 | +0.9 | — | 52.1% (51.4%) | Asset | -0.062 (-1.8) | +0.0004 (+1.7) | no |
| USDMXN | 1W | +0.2 | -4.4 | D -7.6 / E -1.1 | 51.7% (51.9%) | Class | -0.067 (-2.0) | +0.0002 (+0.6) | yes |
| USDMXN | 1M | +0.6 | +6.4 | — | 53.5% (54.1%) | Global | +0.026 (+0.4) | -0.0009 (-0.5) | no |
| USDMXN | 3M | +1.0 | -3.0 | — | 56.7% (56.4%) | Global | -0.219 (-1.9) | +0.0011 (+0.2) | no |
| USDMXN | 6M | +1.1 | -5.7 | — | 59.9% (57.6%) | Global | -0.193 (-1.1) | +0.0066 (+0.6) | no |
| USDMXN | 12M | +0.9 | -2.8 | — | 63.6% (63.6%) | Class | +0.258 (+0.7) | -0.0068 (-0.4) | no |
| USMV | 1D | +12.5 | +1.9 | — | 53.3% (52.6%) | Class | +0.162 (+4.0) | +0.0007 (+2.2) | no |
| USMV | 1W | +20.6 | +1.1 | D +2.3 / E +4.4 | 54.3% (54.1%) | Asset | +0.217 (+5.4) | +0.0018 (+3.1) | yes |
| USMV | 1M | -5.2 | -1.9 | — | 58.4% (58.6%) | Global | +0.076 (+0.9) | +0.0013 (+0.8) | no |
| USMV | 3M | +3.0 | -2.3 | — | 64.4% (63.5%) | Global | +0.027 (+0.2) | +0.0042 (+1.5) | no |
| USMV | 6M | -1.2 | -2.0 | — | 72.6% (71.4%) | Global | +0.355 (+1.4) | +0.0016 (+0.2) | no |
| USMV | 12M | -1.7 | -2.5 | — | 76.7% (75.0%) | Class | +0.146 (+0.1) | +0.0059 (+1.7) | no |
| USO | 1D | +2.6 | +0.3 | — | 52.2% (51.4%) | Asset | +0.006 (+0.2) | -0.0003 (-0.7) | no |
| USO | 1W | +8.2 | -1.1 | D -14.8 / E +19.0 | 51.6% (51.9%) | Industry | -0.009 (-0.2) | -0.0004 (-0.9) | yes |
| USO | 1M | +6.1 | -4.9 | — | 53.6% (54.1%) | Global | -0.093 (-1.2) | -0.0024 (-1.0) | no |
| USO | 3M | -16.1 | +4.1 | — | 57.7% (56.4%) | Global | -0.014 (-0.1) | -0.0127 (-1.5) | no |
| USO | 6M | +5.6 | -2.6 | — | 60.2% (57.6%) | Global | -0.217 (-1.1) | -0.0261 (-1.5) | no |
| USO | 12M | -0.6 | +1.7 | — | 63.4% (63.6%) | Global | +0.156 (+0.3) | -0.0009 (-0.2) | no |
| UST10Y | 1D | +0.1 | +1.5 | — | 54.7% (54.0%) | Class | +0.106 (+3.2) | +0.0004 (+1.3) | no |
| UST10Y | 1W | -2.2 | -2.2 | D -6.7 / E -18.1 | 57.6% (56.5%) | Global | +0.057 (+1.7) | -0.0002 (-0.4) | yes |
| UST10Y | 1M | -1.9 | +2.7 | — | 63.0% (63.1%) | Global | -0.039 (-0.6) | -0.0023 (-1.4) | no |
| UST10Y | 3M | -2.8 | -2.6 | — | 68.2% (66.8%) | Global | -0.129 (-1.1) | -0.0051 (-1.3) | no |
| UST10Y | 6M | +0.3 | -2.0 | — | 74.2% (73.0%) | Global | -0.102 (-0.6) | -0.0069 (-0.9) | no |
| UST10Y | 12M | +0.2 | -3.4 | — | 76.3% (75.3%) | Global | -0.259 (-1.0) | -0.0049 (-0.3) | no |
| UST2Y | 1D | +8.2 | +1.4 | — | 62.1% (61.3%) | Class | +0.060 (+1.8) | +0.0004 (+1.2) | no |
| UST2Y | 1W | -11.1 | +2.0 | D -4.2 / E -15.3 | 71.2% (67.8%) | Global | -0.030 (-0.9) | -0.0010 (-1.6) | yes |
| UST2Y | 1M | +8.0 | +0.9 | — | 82.2% (81.5%) | Global | -0.078 (-1.1) | -0.0014 (-1.3) | no |
| UST2Y | 3M | -4.9 | -2.7 | — | 88.3% (86.7%) | Global | +0.001 (+0.0) | -0.0017 (-0.7) | no |
| UST2Y | 6M | -4.5 | -2.0 | — | 95.7% (95.8%) | Global | -0.143 (-0.8) | -0.0049 (-1.2) | no |
| UST2Y | 12M | +10.9 | +0.6 | — | 92.5% (91.1%) | Global | -0.107 (-0.4) | -0.0019 (-0.2) | no |
| UST30Y | 1D | -0.2 | +0.9 | — | 53.5% (52.7%) | Class | +0.114 (+3.4) | +0.0007 (+2.1) | no |
| UST30Y | 1W | +1.4 | -4.1 | D -9.7 / E -16.6 | 55.0% (54.4%) | Global | +0.086 (+2.6) | +0.0001 (+0.3) | yes |
| UST30Y | 1M | -0.3 | +2.9 | — | 58.9% (59.1%) | Global | +0.017 (+0.2) | -0.0018 (-1.0) | no |
| UST30Y | 3M | +0.2 | -1.8 | — | 64.5% (63.3%) | Global | -0.119 (-1.0) | -0.0035 (-0.7) | no |
| UST30Y | 6M | -0.1 | -1.9 | — | 67.8% (66.2%) | Global | -0.039 (-0.2) | -0.0072 (-0.8) | no |
| UST30Y | 12M | -1.1 | -6.6 | — | 70.5% (69.8%) | Global | -0.193 (-0.8) | -0.0052 (-0.3) | no |
| UST5Y | 1D | -0.7 | +1.1 | — | 56.6% (55.8%) | Class | +0.081 (+2.4) | +0.0008 (+2.7) | no |
| UST5Y | 1W | -6.8 | -1.1 | D -5.6 / E -18.3 | 61.2% (59.5%) | Global | +0.039 (+1.2) | -0.0004 (-0.9) | yes |
| UST5Y | 1M | -4.2 | +2.7 | — | 68.5% (68.3%) | Global | -0.067 (-1.0) | -0.0027 (-1.9) | no |
| UST5Y | 3M | -6.4 | -3.4 | — | 73.9% (72.3%) | Global | -0.087 (-0.7) | -0.0057 (-1.7) | no |
| UST5Y | 6M | +0.1 | -3.0 | — | 81.5% (80.8%) | Global | -0.147 (-0.8) | -0.0084 (-1.4) | no |
| UST5Y | 12M | +3.0 | -0.9 | — | 81.8% (80.5%) | Global | -0.262 (-1.0) | -0.0056 (-0.4) | no |
| UUP | 1D | -1.3 | +1.4 | — | 51.9% (51.4%) | Asset | +0.040 (+1.1) | +0.0004 (+1.3) | no |
| UUP | 1W | -1.1 | +2.7 | D -4.4 / E +4.1 | 51.2% (51.9%) | Asset | +0.083 (+2.2) | -0.0003 (-0.8) | yes |
| UUP | 1M | -0.0 | +1.4 | — | 53.6% (54.1%) | Global | +0.001 (+0.0) | +0.0008 (+0.3) | no |
| UUP | 3M | +1.5 | +4.0 | — | 57.4% (56.4%) | Global | -0.062 (-0.5) | -0.0025 (-0.3) | no |
| UUP | 6M | +1.3 | +3.2 | — | 59.9% (57.6%) | Global | +0.009 (+0.0) | +0.0047 (+0.3) | no |
| UUP | 12M | +1.1 | -2.1 | — | 63.6% (63.6%) | Global | +0.186 (+0.4) | -0.0053 (-1.0) | no |
| V | 1D | +24.9 | +1.9 | — | 52.3% (51.6%) | Class | +0.139 (+3.7) | +0.0002 (+1.1) | no |
| V | 1W | -8.8 | -6.7 | D -5.6 / E -7.4 | 52.0% (52.3%) | Class | +0.220 (+5.9) | +0.0004 (+0.9) | yes |
| V | 1M | -24.0 | -4.0 | — | 54.6% (54.9%) | Global | +0.124 (+1.6) | +0.0025 (+1.4) | no |
| V | 3M | -5.6 | -4.1 | — | 58.6% (58.2%) | Global | +0.095 (+0.7) | +0.0045 (+1.4) | no |
| V | 6M | -8.3 | -4.6 | — | 65.5% (63.7%) | Sector | +0.183 (+0.8) | +0.0087 (+1.2) | no |
| V | 12M | -5.8 | -0.9 | — | 70.9% (70.4%) | Asset | -0.103 (-0.2) | -0.0019 (-0.4) | no |
| VCIT | 1D | -1.7 | -0.1 | — | 56.8% (56.0%) | Asset | +0.049 (+1.2) | -0.0009 (-1.9) | no |
| VCIT | 1W | -11.9 | -0.1 | D -1.7 / E -4.7 | 61.9% (60.1%) | Asset | +0.083 (+2.0) | -0.0011 (-1.4) | yes |
| VCIT | 1M | -10.2 | +3.3 | — | 70.8% (70.4%) | Global | -0.048 (-0.6) | -0.0015 (-1.3) | no |
| VCIT | 3M | -10.3 | +0.4 | — | 74.3% (72.7%) | Global | +0.087 (+0.5) | -0.0020 (-0.5) | no |
| VCIT | 6M | -1.4 | -0.8 | — | 83.7% (83.1%) | Global | -0.011 (-0.0) | -0.0067 (-1.1) | no |
| VCIT | 12M | +1.4 | -2.4 | — | 86.2% (84.8%) | Global | -0.473 (-0.6) | +0.0056 (+1.7) | no |
| VNQ | 1D | +1.7 | +1.5 | — | 52.6% (52.0%) | Global | +0.116 (+3.4) | +0.0001 (+0.5) | no |
| VNQ | 1W | +10.4 | -0.5 | D -9.3 / E -10.0 | 52.9% (52.9%) | Asset | +0.178 (+5.3) | +0.0005 (+1.2) | yes |
| VNQ | 1M | +0.6 | +1.3 | — | 55.5% (55.9%) | Global | +0.119 (+1.6) | -0.0001 (-0.0) | no |
| VNQ | 3M | -0.4 | +0.7 | — | 61.0% (59.9%) | Global | +0.094 (+0.7) | -0.0020 (-0.6) | no |
| VNQ | 6M | +2.4 | +0.8 | — | 69.1% (67.5%) | Global | +0.061 (+0.3) | -0.0038 (-0.5) | no |
| VNQ | 12M | +7.2 | -3.7 | — | 71.7% (71.1%) | Class | -0.154 (-0.4) | -0.0025 (-0.6) | no |
| VTI | 1D | +2.5 | -0.3 | — | 55.2% (54.6%) | Global | +0.074 (+2.2) | +0.0000 (+0.1) | no |
| VTI | 1W | +6.0 | -0.3 | D -8.4 / E -5.0 | 57.9% (57.1%) | Class | +0.077 (+2.3) | +0.0001 (+0.3) | yes |
| VTI | 1M | +5.0 | -1.3 | — | 63.4% (63.4%) | Class | +0.029 (+0.4) | +0.0017 (+1.5) | no |
| VTI | 3M | +4.1 | +1.5 | — | 68.7% (67.0%) | Class | -0.107 (-0.9) | +0.0004 (+0.1) | no |
| VTI | 6M | -0.9 | -1.9 | — | 76.0% (74.9%) | Global | -0.119 (-0.6) | -0.0032 (-0.6) | no |
| VTI | 12M | +7.2 | +2.3 | — | 83.8% (82.6%) | Asset | -0.095 (-0.3) | +0.0029 (+0.3) | no |
| VXX | 1D | -7.4 | +0.0 | — | 47.9% (47.2%) | Asset | -0.009 (-0.1) | +0.0012 (+2.0) | no |
| VXX | 1W | -4.8 | -0.6 | D +0.2 / E -1.5 | 42.6% (44.7%) | Asset | -0.019 (-0.3) | +0.0018 (+2.3) | yes |
| VXX | 1M | +1.3 | +0.2 | — | 39.9% (41.0%) | Global | +0.052 (+0.4) | -0.0060 (-2.0) | no |
| VXX | 3M | +2.9 | +0.1 | — | 40.5% (40.7%) | Global | -0.191 (-0.8) | -0.0141 (-2.7) | no |
| VXX | 6M | +2.4 | -3.6 | — | 44.3% (41.3%) | Global | -0.492 (-0.6) | -0.0156 (-1.9) | no |
| VZ | 1D | +8.8 | -0.8 | — | 51.3% (50.5%) | Asset | -0.026 (-0.8) | +0.0001 (+0.5) | no |
| VZ | 1W | -8.4 | -3.7 | D -12.0 / E -8.1 | 49.8% (50.5%) | Global | +0.064 (+1.9) | -0.0004 (-1.0) | yes |
| VZ | 1M | -6.5 | -3.9 | — | 50.4% (51.0%) | Global | +0.017 (+0.2) | -0.0020 (-1.2) | no |
| VZ | 3M | +1.2 | +2.8 | — | 53.7% (52.6%) | Global | -0.053 (-0.4) | -0.0018 (-0.5) | no |
| VZ | 6M | +3.9 | +7.4 | — | 57.8% (55.2%) | Global | +0.037 (+0.2) | -0.0046 (-0.6) | no |
| VZ | 12M | +0.3 | +1.0 | — | 62.9% (63.2%) | Global | +0.118 (+0.5) | -0.0036 (-0.3) | no |
| WHEAT | 1D | -0.3 | +0.7 | — | 52.1% (51.4%) | Sector | +0.046 (+1.4) | +0.0002 (+0.7) | no |
| WHEAT | 1W | +23.7 | -0.5 | D -11.2 / E +0.5 | 51.6% (51.9%) | Class | +0.025 (+0.7) | -0.0006 (-1.7) | yes |
| WHEAT | 1M | -4.0 | -4.8 | — | 53.6% (54.1%) | Global | +0.025 (+0.4) | -0.0032 (-1.7) | no |
| WHEAT | 3M | -0.2 | +1.9 | — | 57.2% (56.4%) | Global | -0.106 (-0.9) | -0.0125 (-1.9) | no |
| WHEAT | 6M | +0.9 | -1.3 | — | 59.9% (57.6%) | Global | -0.079 (-0.4) | -0.0305 (-2.3) | no |
| WHEAT | 12M | -2.5 | -3.4 | — | 62.9% (63.6%) | Class | -0.202 (-0.7) | -0.0522 (-1.7) | no |
| WMT | 1D | +7.5 | -1.5 | — | 51.7% (50.8%) | Global | +0.018 (+0.5) | -0.0006 (-2.0) | no |
| WMT | 1W | +16.4 | +4.3 | D -6.8 / E -0.7 | 51.1% (51.0%) | Product type | +0.044 (+1.3) | -0.0003 (-1.1) | yes |
| WMT | 1M | +16.8 | +8.4 | — | 51.2% (52.0%) | Global | -0.007 (-0.1) | +0.0018 (+1.1) | no |
| WMT | 3M | +3.5 | -2.1 | — | 55.1% (54.1%) | Class | +0.153 (+1.3) | -0.0005 (-0.1) | no |
| WMT | 6M | +13.7 | +9.1 | — | 62.0% (59.6%) | Product type | +0.186 (+1.1) | +0.0027 (+0.4) | no |
| WMT | 12M | +10.4 | +1.5 | — | 66.6% (68.6%) | Product type | +0.016 (+0.1) | -0.0044 (-0.3) | no |
| WTI | 1D | +0.9 | +3.6 | — | 52.1% (51.4%) | Sector | -0.034 (-1.0) | -0.0002 (-0.7) | no |
| WTI | 1W | +2.5 | -0.2 | D -6.3 / E +13.3 | 51.7% (51.9%) | Class | +0.030 (+0.9) | -0.0003 (-0.9) | yes |
| WTI | 1M | +2.9 | -3.6 | — | 53.6% (54.1%) | Global | -0.003 (-0.0) | -0.0019 (-1.0) | no |
| WTI | 3M | -16.7 | +4.2 | — | 57.4% (56.4%) | Global | +0.043 (+0.4) | -0.0078 (-1.2) | no |
| WTI | 6M | +1.8 | -1.6 | — | 60.1% (57.6%) | Global | -0.001 (-0.0) | -0.0186 (-1.4) | no |
| WTI | 12M | -1.5 | -4.1 | — | 63.3% (63.6%) | Global | -0.147 (-0.5) | -0.0207 (-0.8) | no |
| XLB | 1D | +2.0 | +2.6 | — | 53.3% (52.7%) | Global | +0.067 (+2.0) | +0.0000 (+0.2) | no |
| XLB | 1W | +12.9 | +1.4 | D -5.5 / E -7.3 | 54.4% (54.3%) | Sector | +0.100 (+3.0) | -0.0004 (-0.9) | yes |
| XLB | 1M | -0.9 | -0.9 | — | 58.1% (58.4%) | Class | +0.102 (+1.5) | +0.0000 (+0.0) | no |
| XLB | 3M | -2.1 | +1.0 | — | 64.1% (62.6%) | Class | -0.001 (-0.0) | +0.0017 (+0.6) | no |
| XLB | 6M | +0.5 | +3.7 | — | 68.4% (66.7%) | Global | -0.056 (-0.3) | +0.0008 (+0.1) | no |
| XLB | 12M | +2.3 | -3.5 | — | 73.8% (73.1%) | Global | +0.083 (+0.3) | -0.0014 (-0.1) | no |
| XLC | 1D | -0.1 | +1.8 | — | 53.1% (52.3%) | Asset | +0.052 (+0.6) | -0.0001 (-0.1) | no |
| XLC | 1W | -0.9 | +3.0 | D +1.2 / E +0.5 | 53.7% (53.5%) | Product type | +0.114 (+1.3) | -0.0009 (-1.1) | yes |
| XLC | 1M | -7.8 | +0.2 | — | 57.5% (57.8%) | Asset | -0.059 (-0.2) | -0.0017 (-1.0) | no |
| XLC | 3M | -14.5 | -0.7 | — | 64.6% (63.5%) | Product type | — (—) | — (—) | no |
| XLE | 1D | +0.7 | -0.3 | — | 51.1% (50.5%) | Global | -0.018 (-0.5) | +0.0001 (+0.6) | no |
| XLE | 1W | +1.0 | +0.1 | D -6.9 / E -2.6 | 49.3% (50.4%) | Global | -0.030 (-0.9) | +0.0001 (+0.3) | yes |
| XLE | 1M | +3.2 | -6.6 | — | 50.5% (51.1%) | Global | -0.092 (-1.3) | -0.0021 (-1.5) | no |
| XLE | 3M | +1.2 | +2.8 | — | 54.5% (53.2%) | Global | +0.002 (+0.0) | -0.0012 (-0.4) | no |
| XLE | 6M | +7.0 | +7.5 | — | 66.8% (65.0%) | Product type | -0.214 (-1.2) | -0.0045 (-0.7) | no |
| XLE | 12M | -5.1 | -1.7 | — | 73.0% (72.3%) | Product type | +0.138 (+0.5) | -0.0168 (-1.2) | no |
| XLF | 1D | +13.0 | +0.9 | — | 53.6% (53.0%) | Global | +0.078 (+2.3) | -0.0000 (-0.3) | no |
| XLF | 1W | +4.2 | -1.6 | D -7.0 / E -7.9 | 54.8% (54.7%) | Class | +0.036 (+1.1) | +0.0000 (+0.0) | yes |
| XLF | 1M | +3.0 | +1.7 | — | 59.7% (59.9%) | Class | +0.008 (+0.1) | +0.0007 (+0.6) | no |
| XLF | 3M | -4.1 | -1.5 | — | 65.3% (63.9%) | Class | +0.054 (+0.4) | -0.0003 (-0.1) | no |
| XLF | 6M | -8.5 | -3.2 | — | 69.8% (68.2%) | Class | +0.156 (+0.9) | -0.0056 (-1.1) | no |
| XLF | 12M | +2.5 | +3.3 | — | 77.3% (76.3%) | Global | +0.083 (+0.3) | +0.0008 (+0.1) | no |
| XLI | 1D | +1.2 | -0.6 | — | 53.9% (53.3%) | Global | +0.050 (+1.5) | -0.0000 (-0.1) | no |
| XLI | 1W | +5.6 | +1.0 | D -9.2 / E -8.7 | 55.8% (55.3%) | Product type | +0.084 (+2.5) | -0.0002 (-0.4) | yes |
| XLI | 1M | +7.5 | +1.3 | — | 60.0% (60.2%) | Global | +0.037 (+0.5) | -0.0000 (-0.0) | no |
| XLI | 3M | -1.0 | +0.9 | — | 64.4% (62.8%) | Global | -0.023 (-0.2) | +0.0008 (+0.3) | no |
| XLI | 6M | +4.4 | +1.4 | — | 70.6% (69.1%) | Class | +0.081 (+0.5) | -0.0005 (-0.1) | no |
| XLI | 12M | +6.6 | +2.8 | — | 79.0% (77.9%) | Class | +0.173 (+0.6) | +0.0051 (+0.6) | no |
| XLK | 1D | -6.4 | -1.3 | — | 54.0% (53.2%) | Global | -0.014 (-0.4) | +0.0000 (+0.0) | no |
| XLK | 1W | -4.5 | +0.7 | D -9.6 / E -6.6 | 55.0% (54.9%) | Product type | +0.076 (+2.3) | +0.0004 (+1.1) | yes |
| XLK | 1M | +3.3 | -3.6 | — | 58.7% (58.9%) | Global | +0.079 (+1.1) | +0.0022 (+1.8) | no |
| XLK | 3M | +12.2 | +2.9 | — | 65.0% (63.2%) | Class | -0.099 (-0.8) | +0.0009 (+0.4) | no |
| XLK | 6M | -1.8 | -3.1 | — | 71.3% (69.8%) | Class | -0.225 (-1.3) | -0.0011 (-0.3) | no |
| XLK | 12M | +18.5 | +2.5 | — | 81.8% (80.6%) | Product type | -0.014 (-0.0) | +0.0099 (+1.4) | no |
| XLP | 1D | +41.7 | +0.6 | — | 51.7% (51.0%) | Global | +0.067 (+2.0) | -0.0000 (-0.1) | no |
| XLP | 1W | +27.6 | +1.1 | D -5.0 / E -4.1 | 51.0% (51.2%) | Class | +0.068 (+2.0) | +0.0007 (+1.6) | yes |
| XLP | 1M | +2.0 | +1.2 | — | 52.0% (52.6%) | Class | +0.050 (+0.7) | +0.0007 (+0.6) | no |
| XLP | 3M | -1.3 | -1.2 | — | 56.0% (55.1%) | Class | +0.074 (+0.6) | +0.0009 (+0.4) | no |
| XLP | 6M | +1.9 | +1.2 | — | 62.4% (60.2%) | Class | +0.249 (+1.5) | -0.0009 (-0.2) | no |
| XLP | 12M | -2.8 | -2.5 | — | 68.8% (68.2%) | Global | +0.071 (+0.2) | +0.0132 (+2.2) | no |
| XLRE | 1D | +2.8 | +3.6 | — | 52.4% (51.9%) | Global | -0.051 (-1.0) | -0.0004 (-0.9) | no |
| XLRE | 1W | +9.9 | -1.9 | D -8.1 / E -9.2 | 52.6% (52.8%) | Global | +0.016 (+0.3) | +0.0004 (+1.1) | yes |
| XLRE | 1M | -0.6 | -0.4 | — | 55.2% (55.6%) | Global | -0.011 (-0.1) | +0.0013 (+0.8) | no |
| XLRE | 3M | -0.7 | +0.6 | — | 60.6% (59.3%) | Class | +0.019 (+0.1) | -0.0027 (-0.6) | no |
| XLRE | 6M | +3.3 | -1.4 | — | 66.9% (65.2%) | Class | -0.167 (-0.4) | -0.0027 (-0.2) | no |
| XLU | 1D | +4.1 | +1.7 | — | 52.2% (51.5%) | Global | +0.057 (+1.7) | -0.0001 (-0.5) | no |
| XLU | 1W | +1.0 | +0.2 | D -6.8 / E -5.3 | 52.2% (52.2%) | Sector | +0.097 (+2.9) | +0.0005 (+1.5) | yes |
| XLU | 1M | +7.5 | +3.2 | — | 54.1% (54.6%) | Class | +0.134 (+2.0) | +0.0016 (+1.0) | no |
| XLU | 3M | +0.0 | -0.6 | — | 58.6% (57.4%) | Class | +0.129 (+1.1) | +0.0013 (+0.4) | no |
| XLU | 6M | -2.7 | -1.6 | — | 63.8% (61.8%) | Class | +0.048 (+0.3) | +0.0025 (+0.4) | no |
| XLU | 12M | +4.0 | +1.4 | — | 69.9% (69.3%) | Global | -0.375 (-1.4) | +0.0131 (+1.2) | no |
| XLV | 1D | -0.6 | -1.6 | — | 52.4% (51.6%) | Global | +0.028 (+0.8) | -0.0001 (-0.8) | no |
| XLV | 1W | +10.8 | +4.6 | D -2.6 / E -3.2 | 51.9% (52.4%) | Class | +0.036 (+1.1) | +0.0006 (+1.5) | yes |
| XLV | 1M | -8.2 | -6.7 | — | 54.6% (54.9%) | Class | -0.028 (-0.4) | +0.0009 (+0.7) | no |
| XLV | 3M | +0.2 | -1.2 | — | 61.1% (59.7%) | Class | -0.206 (-1.7) | +0.0005 (+0.2) | no |
| XLV | 6M | +1.5 | +3.4 | — | 67.2% (65.5%) | Class | -0.150 (-0.9) | +0.0011 (+0.2) | no |
| XLV | 12M | -14.4 | -7.0 | — | 70.3% (69.5%) | Product type | +0.244 (+0.9) | +0.0050 (+0.5) | no |
| XLY | 1D | +1.2 | -1.8 | — | 54.0% (53.3%) | Global | +0.038 (+1.1) | +0.0000 (+0.1) | no |
| XLY | 1W | +2.1 | -3.9 | D -12.1 / E -11.0 | 56.0% (55.1%) | Class | +0.053 (+1.6) | -0.0003 (-0.8) | yes |
| XLY | 1M | +4.2 | +1.6 | — | 60.1% (60.3%) | Class | +0.087 (+1.3) | +0.0015 (+1.3) | no |
| XLY | 3M | +2.0 | -2.2 | — | 66.1% (64.8%) | Class | -0.014 (-0.1) | +0.0010 (+0.4) | no |
| XLY | 6M | +0.1 | -4.2 | — | 72.7% (71.4%) | Class | +0.115 (+0.7) | -0.0028 (-0.6) | no |
| XLY | 12M | +4.5 | +2.0 | — | 78.1% (77.0%) | Class | -0.134 (-0.5) | +0.0103 (+1.6) | no |
| XOM | 1D | +1.2 | -1.6 | — | 50.9% (50.1%) | Global | -0.013 (-0.4) | +0.0000 (+0.1) | no |
| XOM | 1W | -5.0 | +3.9 | D -5.9 / E +5.1 | 48.7% (49.8%) | Global | -0.066 (-2.0) | -0.0001 (-0.1) | yes |
| XOM | 1M | +0.2 | -5.3 | — | 49.2% (49.8%) | Global | -0.092 (-1.3) | -0.0021 (-1.4) | no |
| XOM | 3M | +6.6 | +5.6 | — | 52.7% (51.4%) | Global | -0.038 (-0.3) | -0.0010 (-0.3) | no |
| XOM | 6M | +14.6 | +12.0 | — | 62.2% (59.9%) | Global | -0.151 (-0.9) | -0.0045 (-0.7) | no |
| XOM | 12M | -8.2 | -2.3 | — | 67.0% (67.5%) | Global | +0.034 (+0.1) | -0.0172 (-1.3) | no |

## 9. Learned Shaffer Hedge parameters

Hierarchy: Global → Risk class → Objective → Product class → Instrument. Sizing: best multiple of hedge-2's package (0.5–1.5×) per node, shrunk toward the parent by dates/(dates + K); product preference: realised advantage of each product type over hedge-2's choice, shrunk; κ (cost sensitivity) and β (basis-risk penalty) chosen by walk-forward on earlier eras; validation against hedge-2 with the Hedge program's fixed gates H1–H5 and BH FDR. hedge-2 is unchanged.

### Sizing multiplier — 1W (K chosen today: 60)

| Node | Dates | Historical best fit | Validated (shrunk) |
|---|---|---|---|
| global | 446 | 1.40 | 1.35 |
| risk:Commodity | 446 | 1.50 | 1.48 |
| risk:Credit | 446 | 1.50 | 1.48 |
| risk:Crypto | 111 | 1.45 | 1.42 |
| risk:Equity | 446 | 1.50 | 1.48 |
| risk:FX | 446 | 1.50 | 1.48 |
| risk:Other | 1 | — | 1.35 |
| risk:Rates | 446 | 1.50 | 1.48 |
| risk:Volatility | 207 | 1.50 | 1.47 |
| objective:Commodity/commodity | 446 | 1.30 | 1.32 |
| objective:Commodity/drawdown | 273 | 1.50 | 1.50 |
| objective:Commodity/es | 273 | 1.50 | 1.50 |
| objective:Commodity/min_variance | 273 | 1.25 | 1.29 |
| objective:Commodity/systematic | 273 | 1.50 | 1.50 |
| objective:Commodity/target_vol | 183 | 1.50 | 1.50 |
| objective:Commodity/var | 273 | 1.50 | 1.50 |
| objective:Credit/credit | 446 | 1.50 | 1.50 |
| objective:Credit/drawdown | 291 | 1.00 | 1.08 |
| objective:Credit/es | 291 | 1.00 | 1.08 |
| objective:Credit/min_variance | 291 | 0.85 | 0.96 |
| objective:Credit/name | 352 | 1.50 | 1.50 |
| objective:Credit/systematic | 8 | 1.50 | 1.48 |
| objective:Credit/target_vol | 77 | 1.00 | 1.21 |
| objective:Credit/var | 291 | 1.00 | 1.08 |
| objective:Crypto/crypto | 111 | 1.15 | 1.24 |
| objective:Crypto/drawdown | 111 | 1.50 | 1.47 |
| objective:Crypto/es | 111 | 1.50 | 1.47 |
| objective:Crypto/min_variance | 111 | 1.25 | 1.31 |
| objective:Crypto/systematic | 111 | 1.15 | 1.24 |
| objective:Crypto/target_vol | 111 | 1.50 | 1.47 |
| objective:Crypto/var | 111 | 1.50 | 1.47 |
| objective:Equity/beta | 446 | 1.50 | 1.50 |
| objective:Equity/crash | 446 | 1.50 | 1.50 |
| objective:Equity/drawdown | 446 | 1.50 | 1.50 |
| objective:Equity/es | 446 | 1.50 | 1.50 |
| objective:Equity/min_variance | 446 | 1.00 | 1.06 |
| objective:Equity/name | 446 | 1.50 | 1.50 |
| objective:Equity/sector | 446 | 1.40 | 1.41 |
| objective:Equity/systematic | 446 | 1.50 | 1.50 |
| objective:Equity/target_vol | 446 | 1.45 | 1.45 |
| objective:Equity/var | 446 | 1.50 | 1.50 |
| objective:FX/fx | 446 | 1.50 | 1.50 |
| objective:Other/drawdown | 1 | — | 1.35 |
| objective:Other/es | 1 | — | 1.35 |
| objective:Other/min_variance | 1 | — | 1.35 |
| objective:Other/name | 1 | — | 1.35 |
| objective:Other/target_vol | 1 | — | 1.35 |
| objective:Other/var | 1 | — | 1.35 |
| objective:Rates/curve | 446 | 1.50 | 1.50 |
| objective:Rates/drawdown | 446 | 1.50 | 1.50 |
| objective:Rates/duration | 446 | 1.40 | 1.41 |
| objective:Rates/es | 446 | 1.50 | 1.50 |
| objective:Rates/min_variance | 446 | 1.50 | 1.50 |
| objective:Rates/name | 446 | 1.50 | 1.50 |
| objective:Rates/systematic | 446 | 1.50 | 1.50 |
| objective:Rates/target_vol | 446 | 1.00 | 1.06 |
| objective:Rates/var | 446 | 1.50 | 1.50 |
| objective:Volatility/volatility | 207 | 1.50 | 1.49 |

Product-class and instrument nodes whose validated multiple moved ≥ 0.1 from 1: Equity/name/common_stock/XOM 1.50 (435 dates), Equity/name/common_stock/JPM 1.50 (429 dates), Equity/name/etf/EFA 1.50 (402 dates), Equity/name/etf/XLK 1.50 (391 dates), Equity/name/common_stock/AAPL 1.50 (387 dates), Equity/crash/inverse_etf/SH 1.50 (408 dates), Equity/name/etf/XLF 1.50 (356 dates), Credit/credit/high_yield/JNK 1.50 (306 dates), Equity/name/etf/QQQ 1.50 (201 dates), Rates/curve/etf/IEF 1.50 (244 dates), Credit/name/high_yield/HYG 1.50 (263 dates), Equity/name/etf/IWM 1.50 (80 dates), Rates/name/etf/IEF 1.50 (203 dates), Equity/name/etn/VXX 1.50 (170 dates), Equity/es/etf/XLE 1.50 (128 dates).

| Objective | Dates | ΔU λ=1 (95% CI) | ΔU λ=5 | t | Eras + | Cost Δ | FDR | Failed gates | Status |
|---|---|---|---|---|---|---|---|---|---|
| min_variance | 345 | +161 ([-93, +490]) | -349 | +1.1 | 1/3 | +8 | ✗ | H1, H2, H4 | NO HEDGE IMPROVEMENT |
| target_vol | 345 | +1075 ([+555, +1501]) | -850 | +4.5 | 3/3 | +11 | ✓ | H4 | NO HEDGE IMPROVEMENT |
| beta | 345 | +1412 ([+470, +2425]) | -2415 | +2.8 | 3/3 | +13 | ✓ | H4 | NO HEDGE IMPROVEMENT |
| systematic | 345 | +1904 ([+1031, +2757]) | -1397 | +4.3 | 3/3 | +116 | ✓ | H4 | NO HEDGE IMPROVEMENT |
| sector | 345 | +1277 ([+627, +1857]) | -497 | +4.1 | 3/3 | +171 | ✓ | H4 | NO HEDGE IMPROVEMENT |
| name | 345 | +3366 ([+2665, +4056]) | +560 | +9.5 | 3/3 | +175 | ✓ | H4 | NO HEDGE IMPROVEMENT |
| crash | 345 | +3086 ([-201, +6259]) | -613 | +1.9 | 3/3 | +339 | ✓ | H4 | NO HEDGE IMPROVEMENT |
| es | 345 | +4954 ([+2268, +7292]) | +2346 | +3.9 | 3/3 | +13 | ✓ | H4 | NO HEDGE IMPROVEMENT |
| var | 345 | +4954 ([+2268, +7292]) | +2346 | +3.9 | 3/3 | +13 | ✓ | H4 | NO HEDGE IMPROVEMENT |
| drawdown | 345 | +4954 ([+2268, +7292]) | +2346 | +3.9 | 3/3 | +13 | ✓ | H4 | NO HEDGE IMPROVEMENT |
| duration | 345 | -62 ([-151, -1]) | -176 | -1.6 | 1/3 | +1 | ✗ | H1, H2, H5 | NO HEDGE IMPROVEMENT |
| curve | 345 | -37 ([-114, +17]) | -111 | -1.1 | 1/3 | +2 | ✗ | H1, H2, H5 | NO HEDGE IMPROVEMENT |
| credit | 345 | +248 ([+74, +437]) | +356 | +2.7 | 3/3 | +53 | ✓ | H4 | NO HEDGE IMPROVEMENT |
| fx | 345 | +17 ([-77, +137]) | +79 | +0.3 | 2/3 | +4 | ✗ | H1, H2, H4 | NO HEDGE IMPROVEMENT |
| commodity | 345 | +48 ([-98, +208]) | -508 | +0.6 | 2/3 | +97 | ✗ | H1, H2, H4 | NO HEDGE IMPROVEMENT |
| crypto | 111 | -2334 ([-13040, +5681]) | -15743 | -0.5 | 0/1 | +395 | ✗ | H1, H2, H3, H4, H5 | NO HEDGE IMPROVEMENT |
| volatility | 205 | +710 ([+219, +1238]) | +242 | +2.7 | 2/2 | +80 | ✓ | H2, H4 | NO HEDGE IMPROVEMENT |

### Sizing multiplier — 1M (K chosen today: 20)

| Node | Dates | Historical best fit | Validated (shrunk) |
|---|---|---|---|
| global | 212 | 1.45 | 1.41 |
| risk:Commodity | 212 | 1.25 | 1.26 |
| risk:Credit | 212 | 1.50 | 1.49 |
| risk:Crypto | 52 | 1.50 | 1.48 |
| risk:Equity | 212 | 1.50 | 1.49 |
| risk:FX | 212 | 1.50 | 1.49 |
| risk:Other | 1 | — | 1.41 |
| risk:Rates | 212 | 1.50 | 1.49 |
| risk:Volatility | 98 | 1.50 | 1.48 |
| objective:Commodity/commodity | 212 | 1.05 | 1.07 |
| objective:Commodity/drawdown | 130 | 1.50 | 1.47 |
| objective:Commodity/es | 130 | 1.50 | 1.47 |
| objective:Commodity/min_variance | 130 | 0.70 | 0.78 |
| objective:Commodity/systematic | 130 | 1.45 | 1.43 |
| objective:Commodity/target_vol | 89 | 0.50 | 0.64 |
| objective:Commodity/var | 130 | 1.50 | 1.47 |
| objective:Credit/credit | 212 | 1.40 | 1.41 |
| objective:Credit/drawdown | 138 | 1.50 | 1.50 |
| objective:Credit/es | 138 | 1.50 | 1.50 |
| objective:Credit/min_variance | 138 | 1.50 | 1.50 |
| objective:Credit/name | 169 | 1.50 | 1.50 |
| objective:Credit/systematic | 4 | — | 1.49 |
| objective:Credit/target_vol | 36 | 1.50 | 1.50 |
| objective:Credit/var | 138 | 1.50 | 1.50 |
| objective:Crypto/crypto | 52 | 1.35 | 1.38 |
| objective:Crypto/drawdown | 52 | 1.50 | 1.49 |
| objective:Crypto/es | 52 | 1.50 | 1.49 |
| objective:Crypto/min_variance | 52 | 1.25 | 1.31 |
| objective:Crypto/systematic | 52 | 1.35 | 1.38 |
| objective:Crypto/target_vol | 52 | 1.50 | 1.49 |
| objective:Crypto/var | 52 | 1.50 | 1.49 |
| objective:Equity/beta | 212 | 1.50 | 1.50 |
| objective:Equity/crash | 212 | 1.25 | 1.27 |
| objective:Equity/drawdown | 212 | 1.50 | 1.50 |
| objective:Equity/es | 212 | 1.50 | 1.50 |
| objective:Equity/min_variance | 212 | 0.95 | 1.00 |
| objective:Equity/name | 212 | 1.50 | 1.50 |
| objective:Equity/sector | 212 | 1.45 | 1.45 |
| objective:Equity/systematic | 212 | 1.50 | 1.50 |
| objective:Equity/target_vol | 212 | 1.35 | 1.36 |
| objective:Equity/var | 212 | 1.50 | 1.50 |
| objective:FX/fx | 212 | 1.50 | 1.50 |
| objective:Other/drawdown | 1 | — | 1.41 |
| objective:Other/es | 1 | — | 1.41 |
| objective:Other/min_variance | 1 | — | 1.41 |
| objective:Other/name | 1 | — | 1.41 |
| objective:Other/target_vol | 1 | — | 1.41 |
| objective:Other/var | 1 | — | 1.41 |
| objective:Rates/curve | 212 | 1.50 | 1.50 |
| objective:Rates/drawdown | 212 | 1.50 | 1.50 |
| objective:Rates/duration | 212 | 1.50 | 1.50 |
| objective:Rates/es | 212 | 1.50 | 1.50 |
| objective:Rates/min_variance | 212 | 1.30 | 1.32 |
| objective:Rates/name | 212 | 1.50 | 1.50 |
| objective:Rates/systematic | 212 | 1.50 | 1.50 |
| objective:Rates/target_vol | 212 | 1.00 | 1.04 |
| objective:Rates/var | 212 | 1.50 | 1.50 |
| objective:Volatility/volatility | 98 | 1.50 | 1.50 |

Product-class and instrument nodes whose validated multiple moved ≥ 0.1 from 1: Equity/name/common_stock/JPM 1.50 (212 dates), Equity/systematic/etf/XLE 1.50 (212 dates), Equity/name/common_stock/XOM 1.50 (212 dates), Equity/name/etf/XLK 1.50 (212 dates), Equity/name/etf/EFA 1.50 (200 dates), Equity/name/etf/XLF 1.50 (192 dates), Equity/systematic/etf/XLK 1.50 (188 dates), Equity/name/common_stock/AAPL 1.50 (160 dates), Equity/systematic/etf/XLF 1.50 (153 dates), Equity/name/etf/QQQ 1.50 (128 dates), Credit/name/high_yield/HYG 1.50 (138 dates), Rates/name/etf/IEF 1.50 (103 dates), Equity/systematic/etf/SPY 1.50 (45 dates), Rates/duration/etf/IEF 1.50 (94 dates), Equity/name/etn/VXX 1.50 (92 dates).

| Objective | Dates | ΔU λ=1 (95% CI) | ΔU λ=5 | t | Eras + | Cost Δ | FDR | Failed gates | Status |
|---|---|---|---|---|---|---|---|---|---|
| min_variance | 164 | +195 ([-326, +932]) | -1794 | +0.6 | 1/3 | -6 | ✗ | H1, H2 | NO HEDGE IMPROVEMENT |
| target_vol | 164 | +1989 ([+783, +3228]) | -4016 | +3.2 | 3/3 | +9 | ✓ | H4 | NO HEDGE IMPROVEMENT |
| beta | 164 | +1373 ([-247, +3413]) | -8370 | +1.5 | 3/3 | -48 | ✗ | H1, H4, H5 | NO HEDGE IMPROVEMENT |
| systematic | 164 | +3255 ([+1619, +5301]) | -4904 | +3.5 | 3/3 | +270 | ✓ | H4 | NO HEDGE IMPROVEMENT |
| sector | 164 | +1789 ([+213, +3924]) | -4797 | +1.9 | 2/3 | +479 | ✓ | H2, H4 | NO HEDGE IMPROVEMENT |
| name | 164 | +4239 ([+2832, +5873]) | -2973 | +5.5 | 3/3 | +277 | ✓ | H4 | NO HEDGE IMPROVEMENT |
| crash | 164 | -2601 ([-4849, +544]) | -8842 | -1.9 | 0/3 | +490 | ✗ | H1, H2, H4, H5 | NO HEDGE IMPROVEMENT |
| es | 164 | +7107 ([+2885, +11811]) | +462 | +3.1 | 3/3 | -8 | ✓ | H4 | NO HEDGE IMPROVEMENT |
| var | 164 | +7107 ([+2885, +11811]) | +462 | +3.1 | 3/3 | -8 | ✓ | H4 | NO HEDGE IMPROVEMENT |
| drawdown | 164 | +7107 ([+2885, +11811]) | +462 | +3.1 | 3/3 | -8 | ✓ | H4 | NO HEDGE IMPROVEMENT |
| duration | 164 | -228 ([-462, -22]) | -558 | -2.0 | 0/3 | +0 | ✗ | H1, H2, H5 | NO HEDGE IMPROVEMENT |
| curve | 164 | -157 ([-375, +21]) | -312 | -1.6 | 0/3 | +2 | ✗ | H1, H2, H5 | NO HEDGE IMPROVEMENT |
| credit | 164 | -46 ([-870, +604]) | -93 | -0.1 | 2/3 | +179 | ✗ | H1, H2, H4 | NO HEDGE IMPROVEMENT |
| fx | 164 | +155 ([-3, +351]) | +225 | +1.7 | 3/3 | +4 | ✓ | H4 | NO HEDGE IMPROVEMENT |
| commodity | 164 | -334 ([-772, +85]) | -1772 | -1.5 | 1/3 | +90 | ✗ | H1, H2, H4, H5 | NO HEDGE IMPROVEMENT |
| crypto | 52 | +15679 ([-2061, +25333]) | +7313 | +2.2 | 1/1 | +1091 | ✗ | H1, H2, H4 | INSUFFICIENT DATA |
| volatility | 97 | +163 ([-642, +794]) | -2950 | +0.4 | 2/2 | +222 | ✗ | H1, H2, H4, H5 | NO HEDGE IMPROVEMENT |

### Sizing multiplier — 3M (K chosen today: 20)

| Node | Dates | Historical best fit | Validated (shrunk) |
|---|---|---|---|
| global | 70 | 1.30 | 1.23 |
| risk:Commodity | 70 | 1.50 | 1.44 |
| risk:Credit | 70 | 1.50 | 1.44 |
| risk:Crypto | 16 | 1.35 | 1.29 |
| risk:Equity | 70 | 1.20 | 1.21 |
| risk:FX | 70 | 1.50 | 1.44 |
| risk:Rates | 70 | 1.50 | 1.44 |
| risk:Volatility | 31 | 0.50 | 0.79 |
| objective:Commodity/commodity | 70 | 0.50 | 0.71 |
| objective:Commodity/drawdown | 41 | 1.50 | 1.48 |
| objective:Commodity/es | 41 | 1.50 | 1.48 |
| objective:Commodity/min_variance | 41 | 0.95 | 1.11 |
| objective:Commodity/systematic | 41 | 1.50 | 1.48 |
| objective:Commodity/target_vol | 27 | 0.50 | 0.90 |
| objective:Commodity/var | 41 | 1.50 | 1.48 |
| objective:Credit/credit | 70 | 1.50 | 1.49 |
| objective:Credit/drawdown | 47 | 1.50 | 1.48 |
| objective:Credit/es | 47 | 1.50 | 1.48 |
| objective:Credit/min_variance | 47 | 1.50 | 1.48 |
| objective:Credit/name | 60 | 1.50 | 1.49 |
| objective:Credit/systematic | 1 | — | 1.44 |
| objective:Credit/target_vol | 13 | 0.50 | 1.07 |
| objective:Credit/var | 47 | 1.50 | 1.48 |
| objective:Crypto/crypto | 16 | 1.40 | 1.34 |
| objective:Crypto/drawdown | 16 | 1.00 | 1.16 |
| objective:Crypto/es | 16 | 1.00 | 1.16 |
| objective:Crypto/min_variance | 16 | 1.25 | 1.27 |
| objective:Crypto/systematic | 16 | 1.40 | 1.34 |
| objective:Crypto/target_vol | 16 | 1.50 | 1.38 |
| objective:Crypto/var | 16 | 1.00 | 1.16 |
| objective:Equity/beta | 70 | 1.25 | 1.24 |
| objective:Equity/crash | 70 | 0.55 | 0.70 |
| objective:Equity/drawdown | 70 | 1.50 | 1.43 |
| objective:Equity/es | 70 | 1.50 | 1.43 |
| objective:Equity/min_variance | 70 | 0.75 | 0.85 |
| objective:Equity/name | 70 | 1.50 | 1.43 |
| objective:Equity/sector | 70 | 1.25 | 1.24 |
| objective:Equity/systematic | 70 | 1.30 | 1.28 |
| objective:Equity/target_vol | 70 | 1.05 | 1.08 |
| objective:Equity/var | 70 | 1.50 | 1.43 |
| objective:FX/fx | 70 | 1.50 | 1.49 |
| objective:Rates/curve | 70 | 1.45 | 1.45 |
| objective:Rates/drawdown | 70 | 1.50 | 1.49 |
| objective:Rates/duration | 70 | 1.35 | 1.37 |
| objective:Rates/es | 70 | 1.50 | 1.49 |
| objective:Rates/min_variance | 70 | 1.20 | 1.25 |
| objective:Rates/name | 70 | 1.50 | 1.49 |
| objective:Rates/systematic | 70 | 1.50 | 1.49 |
| objective:Rates/target_vol | 70 | 1.50 | 1.49 |
| objective:Rates/var | 70 | 1.50 | 1.49 |
| objective:Volatility/volatility | 31 | 0.50 | 0.61 |

Product-class and instrument nodes whose validated multiple moved ≥ 0.1 from 1: Credit/name/high_yield/HYG 1.50 (51 dates), Credit/credit/ig_corporate/VCIT 1.50 (46 dates), Rates/name/etf/IEF 1.50 (38 dates), Rates/systematic/treasury_future 1.50 (70 dates), Rates/es/treasury_future 1.50 (70 dates), Rates/var/treasury_future 1.50 (70 dates), Rates/drawdown/treasury_future 1.50 (70 dates), FX/fx/fx_forward 1.50 (70 dates), Credit/credit/high_yield/HYG 1.50 (10 dates), Equity/name/common_stock/JPM 1.50 (70 dates), Equity/name/common_stock/XOM 1.50 (70 dates), Equity/name/etf/XLK 1.50 (70 dates), Equity/name/etf/XLF 1.50 (67 dates), Equity/name/etf/EFA 1.50 (66 dates), Equity/name/common_stock/AAPL 1.50 (64 dates).

| Objective | Dates | ΔU λ=1 (95% CI) | ΔU λ=5 | t | Eras + | Cost Δ | FDR | Failed gates | Status |
|---|---|---|---|---|---|---|---|---|---|
| min_variance | 38 | +2682 ([-377, +5399]) | +14010 | +1.8 | 2/2 | +54 | ✗ | H1, H2, H4 | INSUFFICIENT DATA |
| target_vol | 38 | +2133 ([-234, +4799]) | -2780 | +1.7 | 2/2 | +87 | ✗ | H1, H2, H4 | INSUFFICIENT DATA |
| beta | 38 | +739 ([-1635, +4075]) | -12796 | +0.5 | 2/2 | -167 | ✗ | H1, H2, H4, H5 | INSUFFICIENT DATA |
| systematic | 38 | +3609 ([+1176, +6411]) | -12404 | +2.7 | 2/2 | +485 | ✗ | H1, H2, H4 | INSUFFICIENT DATA |
| sector | 38 | +481 ([-1118, +2704]) | -6538 | +0.5 | 2/2 | +666 | ✗ | H1, H2, H4 | INSUFFICIENT DATA |
| name | 38 | +6043 ([+2421, +11176]) | -13773 | +2.7 | 2/2 | +887 | ✗ | H1, H2, H4 | INSUFFICIENT DATA |
| crash | 38 | +6236 ([-3619, +15003]) | +28099 | +1.3 | 2/2 | -1953 | ✗ | H1, H2, H4 | INSUFFICIENT DATA |
| es | 38 | +3957 ([-5734, +14797]) | -13742 | +0.8 | 1/2 | +37 | ✗ | H1, H2, H4 | INSUFFICIENT DATA |
| var | 38 | +3957 ([-5734, +14797]) | -13742 | +0.8 | 1/2 | +37 | ✗ | H1, H2, H4 | INSUFFICIENT DATA |
| drawdown | 38 | +3957 ([-5734, +14797]) | -13742 | +0.8 | 1/2 | +37 | ✗ | H1, H2, H4 | INSUFFICIENT DATA |
| duration | 38 | -1443 ([-3212, -376]) | -3508 | -2.0 | 1/2 | -11 | ✗ | H1, H2, H3, H5 | INSUFFICIENT DATA |
| curve | 38 | -1629 ([-3350, -595]) | -3895 | -2.3 | 1/2 | -11 | ✗ | H1, H2, H3, H5 | INSUFFICIENT DATA |
| credit | 38 | +416 ([-1247, +3778]) | +1198 | +0.3 | 1/2 | +626 | ✗ | H1, H2, H4 | INSUFFICIENT DATA |
| fx | 38 | +167 ([-364, +918]) | +369 | +0.5 | 1/2 | +4 | ✗ | H1, H2, H4 | INSUFFICIENT DATA |
| commodity | 38 | -1443 ([-3076, -328]) | -4454 | -2.1 | 1/2 | +188 | ✗ | H1, H2, H3, H4, H5 | INSUFFICIENT DATA |
| crypto | 16 | +21754 (—) | -9543 | — | 0/0 | +2073 | ✗ | H1, H2, H3, H4 | INSUFFICIENT DATA |
| volatility | 31 | -747 ([-1923, -129]) | -3554 | -1.6 | 0/1 | +346 | ✗ | H1, H2, H4, H5 | INSUFFICIENT DATA |

### Risk preference and the price of tail protection — 1W

Price per $ of ES = utility given up (λ = 1) per dollar of 95% expected-shortfall reduction bought by sizing up to 1.5×; "free" = sizing up reduced the tail and raised utility at the same time.

| Node | Dates | Best multiple at λ = 0.5 / 1 / 2 / 5 / 10 | Sizing up to 1.5×: ES reduction | … utility change (λ = 1) | Price per $ of ES |
|---|---|---|---|---|---|
| global | 446 | 1.45 / 1.40 / 1.25 / 0.85 / 0.50 | +15687 | +17299 | free |
| risk:Commodity | 446 | 1.50 / 1.50 / 1.35 / 0.50 / 0.50 | +7410 | +2410 | free |
| risk:Credit | 446 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 | +2894 | +3015 | free |
| risk:Crypto | 111 | 1.50 / 1.45 / 1.35 / 1.05 / 0.50 | +2975 | +48075 | free |
| risk:Equity | 446 | 1.50 / 1.50 / 1.45 / 0.85 / 0.50 | +43102 | +21948 | free |
| risk:FX | 446 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 | +356 | +91 | free |
| risk:Rates | 446 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 | +2187 | +1009 | free |
| risk:Volatility | 207 | 1.50 / 1.50 / 1.50 / 1.50 / 0.50 | +3145 | +789 | free |
| objective:Commodity/commodity | 446 | 1.50 / 1.30 / 0.75 / 0.50 / 0.50 | +412 | +12 | free |
| objective:Commodity/drawdown | 273 | 1.50 / 1.50 / 1.50 / 0.50 / 0.50 | +677 | +486 | free |
| objective:Commodity/es | 273 | 1.50 / 1.50 / 1.50 / 0.50 / 0.50 | +677 | +486 | free |
| objective:Commodity/min_variance | 273 | 1.45 / 1.25 / 0.70 / 0.50 / 0.50 | +1495 | -24 | +0.02 |
| objective:Commodity/systematic | 273 | 1.50 / 1.50 / 1.45 / 0.50 / 0.50 | +3384 | +896 | free |
| objective:Commodity/target_vol | 183 | 1.50 / 1.50 / 0.50 / 0.50 / 0.50 | +87 | +67 | free |
| objective:Commodity/var | 273 | 1.50 / 1.50 / 1.50 / 0.50 / 0.50 | +677 | +486 | free |
| objective:Credit/credit | 446 | 1.50 / 1.50 / 1.50 / 1.50 / 0.85 | +790 | +230 | free |
| objective:Credit/drawdown | 291 | 1.00 / 1.00 / 1.00 / 1.00 / 1.00 | -0 | +0 | — |
| objective:Credit/es | 291 | 1.00 / 1.00 / 1.00 / 1.00 / 1.00 | -0 | +0 | — |
| objective:Credit/min_variance | 291 | 0.80 / 0.85 / 0.95 / 1.15 / 1.50 | -42 | -8 | — |
| objective:Credit/name | 352 | 1.50 / 1.50 / 1.50 / 1.50 / 0.75 | +2145 | +684 | free |
| objective:Credit/target_vol | 77 | 1.00 / 1.00 / 1.00 / 1.00 / 1.00 | -0 | +0 | — |
| objective:Credit/var | 291 | 1.00 / 1.00 / 1.00 / 1.00 / 1.00 | -0 | +0 | — |
| objective:Crypto/crypto | 111 | 1.20 / 1.15 / 1.10 / 0.60 / 0.50 | -29997 | -6392 | — |
| objective:Crypto/drawdown | 111 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 | +18632 | +16922 | free |
| objective:Crypto/es | 111 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 | +18632 | +16922 | free |
| objective:Crypto/min_variance | 111 | 1.30 / 1.25 / 1.20 / 0.95 / 0.50 | -13645 | +442 | — |
| objective:Crypto/systematic | 111 | 1.20 / 1.15 / 1.10 / 0.50 / 0.50 | -32059 | -7019 | — |
| objective:Crypto/target_vol | 111 | 1.50 / 1.50 / 1.50 / 1.15 / 0.50 | +22779 | +10278 | free |
| objective:Crypto/var | 111 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 | +18632 | +16922 | free |
| objective:Equity/beta | 446 | 1.50 / 1.50 / 1.40 / 0.50 / 0.50 | +4979 | +1470 | free |
| objective:Equity/crash | 446 | 1.50 / 1.50 / 1.50 / 1.10 / 0.50 | +3982 | +2712 | free |
| objective:Equity/drawdown | 446 | 1.50 / 1.50 / 1.50 / 1.50 / 0.50 | +5525 | +4669 | free |
| objective:Equity/es | 446 | 1.50 / 1.50 / 1.50 / 1.50 / 0.50 | +5525 | +4669 | free |
| objective:Equity/min_variance | 446 | 1.05 / 1.00 / 0.85 / 0.50 / 0.50 | -7010 | -3750 | — |
| objective:Equity/name | 446 | 1.50 / 1.50 / 1.50 / 1.25 / 0.50 | +11411 | +4002 | free |
| objective:Equity/sector | 446 | 1.45 / 1.40 / 1.25 / 0.65 / 0.50 | +3646 | +830 | free |
| objective:Equity/systematic | 446 | 1.50 / 1.50 / 1.40 / 0.50 / 0.50 | +6348 | +1968 | free |
| objective:Equity/target_vol | 446 | 1.50 / 1.45 / 1.15 / 0.50 / 0.50 | +3171 | +708 | free |
| objective:Equity/var | 446 | 1.50 / 1.50 / 1.50 / 1.50 / 0.50 | +5525 | +4669 | free |
| objective:FX/fx | 446 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 | +356 | +91 | free |
| objective:Rates/curve | 446 | 1.50 / 1.50 / 1.50 / 1.45 / 1.30 | +119 | +52 | free |
| objective:Rates/drawdown | 446 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 | -0 | +1 | — |
| objective:Rates/duration | 446 | 1.40 / 1.40 / 1.35 / 1.35 / 1.30 | +149 | +29 | free |
| objective:Rates/es | 446 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 | -0 | +1 | — |
| objective:Rates/min_variance | 446 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 | +623 | +300 | free |
| objective:Rates/name | 446 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 | +560 | +252 | free |
| objective:Rates/systematic | 446 | 1.50 / 1.50 / 1.50 / 1.50 / 1.10 | +736 | +373 | free |
| objective:Rates/target_vol | 446 | 1.00 / 1.00 / 1.00 / 1.00 / 1.00 | -0 | +0 | — |
| objective:Rates/var | 446 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 | -0 | +1 | — |
| objective:Volatility/volatility | 207 | 1.50 / 1.50 / 1.50 / 1.50 / 0.50 | +3145 | +789 | free |

### Risk preference and the price of tail protection — 1M

Price per $ of ES = utility given up (λ = 1) per dollar of 95% expected-shortfall reduction bought by sizing up to 1.5×; "free" = sizing up reduced the tail and raised utility at the same time.

| Node | Dates | Best multiple at λ = 0.5 / 1 / 2 / 5 / 10 | Sizing up to 1.5×: ES reduction | … utility change (λ = 1) | Price per $ of ES |
|---|---|---|---|---|---|
| global | 212 | 1.50 / 1.45 / 1.35 / 0.95 / 0.50 | +58820 | +50127 | free |
| risk:Commodity | 212 | 1.50 / 1.25 / 0.50 / 0.50 / 0.50 | +4860 | +16 | free |
| risk:Credit | 212 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 | +6888 | +7155 | free |
| risk:Crypto | 52 | 1.50 / 1.50 / 1.50 / 1.40 / 1.25 | +157760 | +170479 | free |
| risk:Equity | 212 | 1.50 / 1.50 / 1.25 / 0.50 / 0.50 | +66513 | +32071 | free |
| risk:FX | 212 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 | +1200 | +371 | free |
| risk:Rates | 212 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 | +10634 | +11149 | free |
| risk:Volatility | 98 | 1.50 / 1.50 / 0.50 / 0.50 / 0.50 | +4188 | +299 | free |
| objective:Commodity/commodity | 212 | 1.45 / 1.05 / 0.50 / 0.50 / 0.50 | +1492 | -120 | +0.08 |
| objective:Commodity/drawdown | 130 | 1.50 / 1.50 / 0.50 / 0.50 / 0.50 | +963 | +473 | free |
| objective:Commodity/es | 130 | 1.50 / 1.50 / 0.50 / 0.50 / 0.50 | +963 | +473 | free |
| objective:Commodity/min_variance | 130 | 0.90 / 0.70 / 0.50 / 0.50 / 0.50 | -3972 | -1652 | — |
| objective:Commodity/systematic | 130 | 1.50 / 1.45 / 0.95 / 0.50 / 0.50 | +5721 | +789 | free |
| objective:Commodity/target_vol | 89 | 1.15 / 0.50 / 0.50 / 0.50 / 0.50 | -1270 | -420 | — |
| objective:Commodity/var | 130 | 1.50 / 1.50 / 0.50 / 0.50 / 0.50 | +963 | +473 | free |
| objective:Credit/credit | 212 | 1.50 / 1.40 / 1.25 / 0.80 / 0.50 | +126 | +108 | free |
| objective:Credit/drawdown | 138 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 | +363 | +382 | free |
| objective:Credit/es | 138 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 | +363 | +382 | free |
| objective:Credit/min_variance | 138 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 | +533 | +311 | free |
| objective:Credit/name | 169 | 1.50 / 1.50 / 1.50 / 1.50 / 0.50 | +5140 | +1912 | free |
| objective:Credit/target_vol | 36 | 0.50 / 1.50 / 1.50 / 1.50 / 1.50 | -0 | +4 | — |
| objective:Credit/var | 138 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 | +363 | +382 | free |
| objective:Crypto/crypto | 52 | 1.35 / 1.35 / 1.35 / 1.30 / 1.15 | -14290 | +11824 | — |
| objective:Crypto/drawdown | 52 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 | +41842 | +39029 | free |
| objective:Crypto/es | 52 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 | +41842 | +39029 | free |
| objective:Crypto/min_variance | 52 | 1.25 / 1.25 / 1.25 / 1.20 / 1.10 | +12559 | -509 | +0.04 |
| objective:Crypto/systematic | 52 | 1.35 / 1.35 / 1.35 / 1.25 / 1.15 | -14290 | +11990 | — |
| objective:Crypto/target_vol | 52 | 1.50 / 1.50 / 1.50 / 1.50 / 1.30 | +48255 | +30087 | free |
| objective:Crypto/var | 52 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 | +41842 | +39029 | free |
| objective:Equity/beta | 212 | 1.50 / 1.50 / 1.25 / 0.50 / 0.50 | +7297 | +2675 | free |
| objective:Equity/crash | 212 | 1.50 / 1.25 / 0.75 / 0.50 / 0.50 | +3475 | +68 | free |
| objective:Equity/drawdown | 212 | 1.50 / 1.50 / 1.50 / 1.15 / 0.50 | +11093 | +8676 | free |
| objective:Equity/es | 212 | 1.50 / 1.50 / 1.50 / 1.15 / 0.50 | +11093 | +8676 | free |
| objective:Equity/min_variance | 212 | 1.00 / 0.95 / 0.80 / 0.50 / 0.50 | -18439 | -11350 | — |
| objective:Equity/name | 212 | 1.50 / 1.50 / 1.50 / 0.50 / 0.50 | +19558 | +6877 | free |
| objective:Equity/sector | 212 | 1.50 / 1.45 / 1.30 / 0.70 / 0.50 | +8195 | +2602 | free |
| objective:Equity/systematic | 212 | 1.50 / 1.50 / 1.40 / 0.50 / 0.50 | +11447 | +4148 | free |
| objective:Equity/target_vol | 212 | 1.45 / 1.35 / 1.10 / 0.50 / 0.50 | +1701 | +1021 | free |
| objective:Equity/var | 212 | 1.50 / 1.50 / 1.50 / 1.15 / 0.50 | +11093 | +8676 | free |
| objective:FX/fx | 212 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 | +1200 | +371 | free |
| objective:Rates/curve | 212 | 1.50 / 1.50 / 1.50 / 1.10 / 0.50 | -379 | +225 | — |
| objective:Rates/drawdown | 212 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 | +2555 | +2729 | free |
| objective:Rates/duration | 212 | 1.50 / 1.50 / 1.35 / 0.95 / 0.50 | -577 | +147 | — |
| objective:Rates/es | 212 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 | +2555 | +2729 | free |
| objective:Rates/min_variance | 212 | 1.25 / 1.30 / 1.30 / 1.35 / 1.40 | -1028 | +256 | — |
| objective:Rates/name | 212 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 | +1738 | +717 | free |
| objective:Rates/systematic | 212 | 1.50 / 1.50 / 1.50 / 1.50 / 0.85 | +3214 | +1618 | free |
| objective:Rates/target_vol | 212 | 1.00 / 1.00 / 1.00 / 1.00 / 1.00 | -0 | +0 | — |
| objective:Rates/var | 212 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 | +2555 | +2729 | free |
| objective:Volatility/volatility | 98 | 1.50 / 1.50 / 0.50 / 0.50 / 0.50 | +4188 | +299 | free |

### Risk preference and the price of tail protection — 3M

Price per $ of ES = utility given up (λ = 1) per dollar of 95% expected-shortfall reduction bought by sizing up to 1.5×; "free" = sizing up reduced the tail and raised utility at the same time.

| Node | Dates | Best multiple at λ = 0.5 / 1 / 2 / 5 / 10 | Sizing up to 1.5×: ES reduction | … utility change (λ = 1) | Price per $ of ES |
|---|---|---|---|---|---|
| global | 70 | 1.40 / 1.30 / 1.05 / 0.50 / 0.50 | +85519 | +21593 | free |
| risk:Commodity | 70 | 1.50 / 1.50 / 0.75 / 0.50 / 0.50 | +45354 | +6478 | free |
| risk:Credit | 70 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 | +13776 | +5995 | free |
| risk:Equity | 70 | 1.40 / 1.20 / 0.65 / 0.50 / 0.50 | +80874 | -11566 | +0.14 |
| risk:FX | 70 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 | +1495 | +593 | free |
| risk:Rates | 70 | 1.50 / 1.50 / 1.45 / 1.30 / 1.20 | +17658 | +12897 | free |
| risk:Volatility | 31 | 1.50 / 0.50 / 0.50 / 0.50 / 0.50 | +3397 | -163 | +0.05 |
| objective:Commodity/commodity | 70 | 0.90 / 0.50 / 0.50 / 0.50 / 0.50 | +3571 | -1281 | +0.36 |
| objective:Commodity/drawdown | 41 | 1.50 / 1.50 / 1.50 / 0.50 / 0.50 | +4923 | +3198 | free |
| objective:Commodity/es | 41 | 1.50 / 1.50 / 1.50 / 0.50 / 0.50 | +4923 | +3198 | free |
| objective:Commodity/min_variance | 41 | 1.15 / 0.95 / 0.50 / 0.50 / 0.50 | +13217 | -2434 | +0.18 |
| objective:Commodity/systematic | 41 | 1.50 / 1.50 / 1.25 / 0.50 / 0.50 | +13356 | +2346 | free |
| objective:Commodity/var | 41 | 1.50 / 1.50 / 1.50 / 0.50 / 0.50 | +4923 | +3198 | free |
| objective:Credit/credit | 70 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 | +6353 | +389 | free |
| objective:Credit/drawdown | 47 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 | +230 | +732 | free |
| objective:Credit/es | 47 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 | +230 | +732 | free |
| objective:Credit/min_variance | 47 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 | +86 | +889 | free |
| objective:Credit/name | 60 | 1.50 / 1.50 / 1.50 / 1.50 / 0.50 | +6648 | +2576 | free |
| objective:Credit/var | 47 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 | +230 | +732 | free |
| objective:Equity/beta | 70 | 1.50 / 1.25 / 0.50 / 0.50 / 0.50 | +10427 | +110 | free |
| objective:Equity/crash | 70 | 0.65 / 0.55 / 0.50 / 0.50 / 0.50 | -1449 | -12234 | — |
| objective:Equity/drawdown | 70 | 1.50 / 1.50 / 1.25 / 0.50 / 0.50 | +14416 | +6760 | free |
| objective:Equity/es | 70 | 1.50 / 1.50 / 1.25 / 0.50 / 0.50 | +14416 | +6760 | free |
| objective:Equity/min_variance | 70 | 0.90 / 0.75 / 0.50 / 0.50 / 0.50 | -30217 | -25378 | — |
| objective:Equity/name | 70 | 1.50 / 1.50 / 1.35 / 0.50 / 0.50 | +31159 | +8820 | free |
| objective:Equity/sector | 70 | 1.40 / 1.25 / 0.80 / 0.50 / 0.50 | +12605 | -242 | +0.02 |
| objective:Equity/systematic | 70 | 1.50 / 1.30 / 0.70 / 0.50 / 0.50 | +17279 | +457 | free |
| objective:Equity/target_vol | 70 | 1.30 / 1.05 / 0.50 / 0.50 / 0.50 | -2179 | -3381 | — |
| objective:Equity/var | 70 | 1.50 / 1.50 / 1.25 / 0.50 / 0.50 | +14416 | +6760 | free |
| objective:FX/fx | 70 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 | +1495 | +593 | free |
| objective:Rates/curve | 70 | 1.50 / 1.45 / 1.25 / 0.60 / 0.50 | -293 | +249 | — |
| objective:Rates/drawdown | 70 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 | +3174 | +3100 | free |
| objective:Rates/duration | 70 | 1.45 / 1.35 / 1.15 / 0.55 / 0.50 | -1151 | +123 | — |
| objective:Rates/es | 70 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 | +3174 | +3100 | free |
| objective:Rates/min_variance | 70 | 1.20 / 1.20 / 1.20 / 1.15 / 1.10 | +2276 | -1549 | +0.68 |
| objective:Rates/name | 70 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 | +3896 | +1607 | free |
| objective:Rates/systematic | 70 | 1.50 / 1.50 / 1.50 / 0.95 / 0.50 | +3409 | +3097 | free |
| objective:Rates/target_vol | 70 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 | -0 | +71 | — |
| objective:Rates/var | 70 | 1.50 / 1.50 / 1.50 / 1.50 / 1.50 | +3174 | +3100 | free |
| objective:Volatility/volatility | 31 | 1.50 / 0.50 / 0.50 / 0.50 / 0.50 | +3397 | -163 | +0.05 |

### Product preference, cost sensitivity and basis-risk penalty — 1W

Validated today: K = 20, κ (cost sensitivity) = 0.0, β (basis-risk penalty) = 1.0; historical best fit (in-sample over every era): [20, 0.0, 1.0].

| Node | Product type | Dates | Historical advantage vs hedge-2 (λ = 1) | Validated (shrunk) preference | Dispersion |
|---|---|---|---|---|---|
| global | etn | 107 | +2278 | +1919 | — |
| global | crypto_futures_etf | 53 | +129 | +94 | — |
| global | high_yield | 223 | -162 | -149 | — |
| global | ig_corporate | 211 | -180 | -164 | — |
| global | currency_trust | 48 | -401 | -283 | — |
| global | leveraged_loan | 195 | -599 | -544 | — |
| global | treasury_future | 223 | -1296 | -1189 | — |
| global | equity_index_future | 223 | -1312 | -1204 | — |
| global | commodity_etf | 32 | -1399 | -861 | — |
| global | fx_future | 217 | -2352 | -2153 | — |
| global | etf | 223 | -3256 | -2988 | — |
| global | fx_forward | 223 | -3878 | -3559 | — |
| global | inverse_etf | 223 | -6481 | -5948 | — |
| global | index_option | 223 | -15806 | -14505 | — |
| global | fx_spot | 223 | -17286 | -15863 | — |
| risk:Commodity | etf | 38 | -332 | -1248 | — |
| risk:Commodity | equity_index_future | 79 | -522 | -660 | — |
| risk:Credit | high_yield | 223 | -162 | -161 | — |
| risk:Credit | ig_corporate | 211 | -180 | -179 | — |
| risk:Credit | leveraged_loan | 195 | -599 | -594 | — |
| risk:Crypto | crypto_futures_etf | 53 | +129 | +119 | — |
| risk:Equity | etn | 107 | +1902 | +1904 | — |
| risk:Equity | equity_index_future | 223 | -1339 | -1328 | — |
| risk:Equity | fx_future | 40 | -2503 | -2387 | — |
| risk:Equity | etf | 223 | -3289 | -3265 | — |
| risk:Equity | fx_forward | 35 | -3875 | -3760 | — |
| risk:Equity | inverse_etf | 223 | -6515 | -6468 | — |
| risk:Equity | index_option | 223 | -15806 | -15698 | — |
| risk:FX | fx_future | 217 | +152 | -43 | — |
| risk:FX | fx_forward | 223 | -3 | -295 | — |
| risk:FX | currency_trust | 48 | -48 | -117 | — |
| risk:FX | fx_spot | 223 | -150 | -1444 | — |
| risk:Rates | etf | 223 | +32 | -217 | — |
| risk:Rates | treasury_future | 223 | +29 | -71 | — |
| objective:Commodity/min_variance | etf | 38 | -332 | -648 | +741 |
| objective:Commodity/min_variance | equity_index_future | 79 | -522 | -550 | +1574 |
| objective:Credit/credit | high_yield | 223 | -162 | -162 | +212 |
| objective:Credit/credit | ig_corporate | 211 | -180 | -180 | +865 |
| objective:Credit/credit | leveraged_loan | 195 | -599 | -599 | +589 |
| objective:Crypto/min_variance | crypto_futures_etf | 53 | +129 | +126 | +2098 |
| objective:Equity/beta | equity_index_future | 223 | +49 | -64 | +169 |
| objective:Equity/beta | etf | 223 | -1509 | -1653 | +463 |
| objective:Equity/beta | index_option | 99 | -1572 | -3946 | +1786 |
| objective:Equity/beta | inverse_etf | 223 | -1690 | -2083 | +620 |
| objective:Equity/crash | inverse_etf | 223 | +1700 | +1028 | +887 |
| objective:Equity/crash | index_option | 223 | -6249 | -7027 | +4360 |
| objective:Equity/crash | etn | 107 | -7738 | -6219 | +11294 |
| objective:Equity/min_variance | equity_index_future | 223 | -1388 | -1383 | +372 |
| objective:Equity/min_variance | etf | 202 | -1514 | -1671 | +1394 |
| objective:Equity/min_variance | fx_future | 40 | -2503 | -2464 | +7871 |
| objective:Equity/min_variance | fx_forward | 35 | -3875 | -3833 | +8839 |
| objective:Equity/min_variance | inverse_etf | 77 | -6525 | -6513 | +9217 |
| objective:Equity/min_variance | index_option | 39 | -7984 | -10599 | +7847 |
| objective:Equity/sector | etf | 223 | -267 | -514 | +217 |
| objective:FX/fx | fx_future | 217 | +152 | +135 | +78 |
| objective:FX/fx | fx_forward | 223 | -3 | -27 | +7 |
| objective:FX/fx | currency_trust | 48 | -48 | -69 | +400 |
| objective:FX/fx | fx_spot | 223 | -150 | -257 | +77 |
| objective:Rates/duration | etf | 223 | +32 | +11 | +212 |
| objective:Rates/duration | treasury_future | 223 | -3 | -9 | +77 |
| objective:Rates/min_variance | treasury_future | 80 | +33 | +12 | +79 |

| Objective | Dates | ΔU λ=1 (95% CI) | t | Eras + | FDR | Failed gates | Status | Chosen in test eras |
|---|---|---|---|---|---|---|---|---|
| beta | 172 | -345 ([-635, -51]) | -2.3 | 1/3 | ✗ | H1, H2, H4, H5 | NO HEDGE IMPROVEMENT | inverse_etf ×500, hedge-2 ×1319, etn ×9 |
| crash | 172 | +566 ([-235, +1362]) | +1.4 | 2/3 | ✗ | H1, H2 | NO HEDGE IMPROVEMENT | inverse_etf ×1812, hedge-2 ×16 |
| min_variance | 172 | -51 ([-196, +88]) | -0.7 | 1/3 | ✗ | H1, H2, H5 | NO HEDGE IMPROVEMENT | hedge-2 ×2167, inverse_etf ×35, treasury_future ×5, etn ×18, equity_index_future ×2 |
| sector | 172 | +0 ([+0, +0]) | — | 0/3 | ✗ | H1, H2, H5 | NO HEDGE IMPROVEMENT | hedge-2 ×1137 |
| duration | 172 | -44 ([-171, +71]) | -0.7 | 1/3 | ✗ | H1, H2, H4, H5 | NO HEDGE IMPROVEMENT | etf ×395, hedge-2 ×398, treasury_future ×1 |
| credit | 172 | -141 ([-326, +20]) | -1.6 | 1/3 | ✗ | H1, H2, H5 | NO HEDGE IMPROVEMENT | hedge-2 ×181, high_yield ×149 |
| fx | 172 | +63 ([-20, +138]) | +1.6 | 3/3 | ✗ | H1, H4 | NO HEDGE IMPROVEMENT | fx_future ×189, hedge-2 ×97 |

### Product preference, cost sensitivity and basis-risk penalty — 1M

Validated today: K = 20, κ (cost sensitivity) = 2.0, β (basis-risk penalty) = 0.5; historical best fit (in-sample over every era): [20, 2.0, 0.5].

| Node | Product type | Dates | Historical advantage vs hedge-2 (λ = 1) | Validated (shrunk) preference | Dispersion |
|---|---|---|---|---|---|
| global | fx_future | 103 | +12357 | +10348 | — |
| global | fx_forward | 106 | +5652 | +4755 | — |
| global | high_yield | 106 | +151 | +127 | — |
| global | fx_spot | 106 | -218 | -184 | — |
| global | equity_put | 45 | -267 | -185 | — |
| global | ig_corporate | 100 | -464 | -386 | — |
| global | leveraged_loan | 93 | -713 | -587 | — |
| global | etf | 106 | -1452 | -1222 | — |
| global | commodity_option | 46 | -1876 | -1308 | — |
| global | etn | 50 | -3823 | -2730 | — |
| global | treasury_future | 106 | -3828 | -3220 | — |
| global | equity_index_future | 106 | -5265 | -4430 | — |
| global | inverse_etf | 106 | -8835 | -7433 | — |
| global | index_option | 106 | -15262 | -12840 | — |
| risk:Commodity | equity_index_future | 46 | +164 | -1228 | — |
| risk:Commodity | etf | 31 | -360 | -698 | — |
| risk:Credit | high_yield | 106 | +151 | +147 | — |
| risk:Credit | ig_corporate | 100 | -464 | -451 | — |
| risk:Credit | leveraged_loan | 93 | -713 | -691 | — |
| risk:Equity | treasury_future | 53 | +1083 | -96 | — |
| risk:Equity | equity_put | 45 | -267 | -241 | — |
| risk:Equity | etf | 106 | -945 | -989 | — |
| risk:Equity | etn | 50 | -2509 | -2572 | — |
| risk:Equity | equity_index_future | 106 | -5475 | -5309 | — |
| risk:Equity | inverse_etf | 106 | -8835 | -8612 | — |
| risk:Equity | index_option | 106 | -14995 | -14653 | — |
| risk:FX | fx_future | 103 | +635 | +2214 | — |
| risk:FX | fx_forward | 106 | +27 | +777 | — |
| risk:FX | fx_spot | 106 | -218 | -213 | — |
| risk:Rates | treasury_future | 106 | -34 | -540 | — |
| risk:Rates | etf | 106 | -212 | -372 | — |
| objective:Commodity/min_variance | equity_index_future | 46 | +164 | -258 | +1276 |
| objective:Commodity/min_variance | etf | 31 | -360 | -493 | +7738 |
| objective:Credit/credit | high_yield | 106 | +151 | +151 | +984 |
| objective:Credit/credit | ig_corporate | 100 | -464 | -461 | +910 |
| objective:Credit/credit | leveraged_loan | 93 | -713 | -709 | +1308 |
| objective:Equity/beta | equity_index_future | 106 | +79 | -776 | +314 |
| objective:Equity/beta | etf | 106 | -1429 | -1359 | +1174 |
| objective:Equity/beta | inverse_etf | 106 | -4944 | -5526 | +3558 |
| objective:Equity/beta | index_option | 46 | -9873 | -11321 | +2144 |
| objective:Equity/crash | inverse_etf | 106 | +2581 | +805 | +3900 |
| objective:Equity/crash | etn | 50 | +265 | -545 | +7272 |
| objective:Equity/crash | index_option | 106 | -533 | -2774 | +2363 |
| objective:Equity/min_variance | treasury_future | 53 | +1083 | +760 | +13648 |
| objective:Equity/min_variance | etf | 98 | +650 | +372 | +5158 |
| objective:Equity/min_variance | equity_put | 45 | -267 | -259 | +6923 |
| objective:Equity/min_variance | index_option | 49 | -4590 | -7507 | +8264 |
| objective:Equity/min_variance | equity_index_future | 106 | -5555 | -5516 | +3753 |
| objective:Equity/min_variance | inverse_etf | 65 | -6473 | -6976 | +3641 |
| objective:Equity/sector | etf | 106 | -165 | -296 | +341 |
| objective:FX/fx | fx_future | 103 | +635 | +892 | +562 |
| objective:FX/fx | fx_forward | 106 | +27 | +146 | +62 |
| objective:FX/fx | fx_spot | 106 | -218 | -217 | +83 |
| objective:Rates/duration | treasury_future | 106 | -35 | -115 | +186 |
| objective:Rates/duration | etf | 106 | -365 | -366 | +748 |
| objective:Rates/min_variance | treasury_future | 105 | +1 | -85 | +496 |

| Objective | Dates | ΔU λ=1 (95% CI) | t | Eras + | FDR | Failed gates | Status | Chosen in test eras |
|---|---|---|---|---|---|---|---|---|
| beta | 58 | +0 ([+0, +0]) | — | 0/2 | ✗ | H1, H2, H5 | INSUFFICIENT DATA | hedge-2 ×631 |
| crash | 58 | +2219 ([+513, +5117]) | +1.9 | 2/2 | ✗ | H1, H2, H4 | INSUFFICIENT DATA | inverse_etf ×262, hedge-2 ×272, index_option ×97 |
| min_variance | 58 | -783 ([-1906, -3]) | -1.6 | 0/2 | ✗ | H1, H2, H3, H5 | INSUFFICIENT DATA | hedge-2 ×754, fx_forward ×12, fx_future ×4, equity_index_future ×3 |
| sector | 58 | -64 ([-365, +136]) | -0.5 | 0/2 | ✗ | H1, H2, H5 | INSUFFICIENT DATA | hedge-2 ×339, etf ×58 |
| duration | 58 | +119 ([+0, +327]) | +1.4 | 1/2 | ✗ | H1, H2 | INSUFFICIENT DATA | hedge-2 ×288, etf ×5 |
| credit | 58 | -270 ([-806, +62]) | -1.2 | 0/2 | ✗ | H1, H2, H3 | INSUFFICIENT DATA | high_yield ×53, hedge-2 ×77 |
| fx | 58 | +161 ([-18, +456]) | +1.3 | 2/2 | ✗ | H1, H2, H4 | INSUFFICIENT DATA | fx_future ×67, hedge-2 ×16, fx_forward ×19 |

### Today's effective hedge multiples (latest replay date; hedge-2 = 1.00)

- **1W:** 148 book × objective pairs; 114 with a validated multiple ≠ 1 — AAPL beta 1.50 (product), AAPL crash 1.50 (instrument), AAPL drawdown 1.50 (product), AAPL es 1.50 (product), AAPL name 1.50 (instrument), AAPL sector 1.40 (instrument), AAPL systematic 1.50 (product), AAPL target_vol 1.45 (product), AAPL var 1.50 (product), BONDS curve 1.50 (product), BONDS duration 1.40 (product), BONDS name 1.50 (product), BONDS systematic 1.50 (product), BTC crypto 1.21 (instrument), BTC drawdown 1.49 (instrument), BTC es 1.49 (instrument), BTC min_variance 1.14 (instrument), BTC systematic 1.21 (instrument), BTC target_vol 1.39 (instrument), BTC var 1.49 (instrument)…
- **1M:** 147 book × objective pairs; 134 with a validated multiple ≠ 1 — AAPL beta 1.50 (product), AAPL crash 1.15 (instrument), AAPL drawdown 1.50 (product), AAPL es 1.50 (product), AAPL min_variance 1.37 (product), AAPL name 1.31 (product), AAPL sector 1.31 (instrument), AAPL systematic 1.50 (product), AAPL target_vol 1.45 (product), AAPL var 1.50 (product), BONDS curve 1.50 (product), BONDS duration 1.50 (product), BONDS min_variance 1.05 (instrument), BONDS name 1.50 (product), BONDS systematic 1.50 (product), BTC crypto 1.35 (instrument), BTC drawdown 1.50 (instrument), BTC es 1.50 (instrument), BTC min_variance 1.26 (instrument), BTC systematic 1.35 (instrument)…
- **3M:** 148 book × objective pairs; 139 with a validated multiple ≠ 1 — AAPL beta 1.29 (product), AAPL crash 0.65 (instrument), AAPL drawdown 1.49 (product), AAPL es 1.49 (product), AAPL min_variance 0.81 (product), AAPL name 1.50 (instrument), AAPL systematic 1.45 (product), AAPL target_vol 1.14 (product), AAPL var 1.49 (product), BONDS curve 1.41 (product), BONDS drawdown 1.50 (product), BONDS duration 1.32 (product), BONDS es 1.50 (product), BONDS min_variance 1.21 (product), BONDS name 1.50 (product), BONDS systematic 1.50 (product), BONDS var 1.50 (product), BTC crypto 1.32 (instrument), BTC drawdown 1.07 (instrument), BTC es 1.07 (instrument)…

