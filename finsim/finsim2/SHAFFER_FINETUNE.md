# Shaffer fine-tune — the learned 1W Alpha, made more robust

Run 2026-09-26 18:52:13 · 5125.2 s · `python -m finsim2 lab --finetune`. Research only: production (shaffer-2.1, shaffer-alpha-2.1-production, the Directional definition, hedge-2, the resizing challenger, benchmark-2.1-2026-09-25) and the two live-shadow learned models (alpha-learned-1w-global-exp, alpha-learned-1w-hierarchy-exp) are unchanged. Method: `engine/finetune.py` (module docstring); hedge: `hedge/hedgetune.py` → SHAFFER_HEDGE_FINETUNE.md.

**The rule for every fine-tune:** the outer walk-forward (eras 2009–12, 2013–16, 2017–20, 2021–24, 2025–) is never touched; every hyperparameter — the blend weight, the half-life, the window, the penalty, the regime dimension, the smoothing — is chosen per era from inner walk-forward weeks that matured before the era began. A fine-tuned model must pass the Alpha program's fixed gates against production (G1 t ≥ 2, G2 split, G3 ≥ 3/4 eras, G4 spread and net long-short), Benjamini–Hochberg FDR across every fine-tune, **and** a new gate G5 against BOTH learned models already in live shadow — the global model D and the hierarchy E (Δ rank IC > 0 with t ≥ 2.0 and ≥ 3/4 complete eras not worse, against each). Beating production is no longer enough: the bar is the learned models we already have.

## Key findings

- **Capability first:** 6 of 6 new planted tests passed (ranking-only, time-decaying, regime-switching, hierarchy-specific, transaction-cost-sensitive, and no false improvement on a plain world) before any market result was read.
- **Reproduction — and a correction:** the fine-tune code reproduces the learned global model (D, rank IC +0.0526) exactly. The live-shadow hierarchy (E) measures +0.0671, not the +0.0546 in SHAFFER_LEARNED_WEIGHTS.md: the learned engine's walk-forward scored E with the validated model's pooling K instead of E's own K_E (e.g. K = 10 instead of 5000 in 2013–16). The live-shadow model itself was always built with K_E, so what is in live shadow is the stronger model; only its reported backtest was wrong (fixed in `learned.py`, erratum added to that report). Consequence here: G5 must beat E as well as D (production +0.0376).
- **No fine-tune beats both live-shadow learned models under every gate.** The closest is blend: Δ rank IC vs learned global +0.0145 (t +4.0), vs learned hierarchy +0.0000 (t +1.0, 4/4 eras not worse). The learned hierarchy already in live shadow (E) stays the model to watch; fine-tuning did not find a robust improvement on it.
- **A hindsight trap, caught and removed:** in a first pass, three hierarchy fine-tunes (stability penalty, time decay, rolling window) appeared to beat E by +0.001–0.002 rank IC. The entire difference came from 2009–12, where no inner history exists and those families fell back to a product-type-depth hierarchy — a default that reflected what the learned run had already shown — while E falls back to the global model. With the same no-history default as E (pooling K = 200, global depth), every one of them is at or below E. Reported here because it is exactly the kind of overfitting the fine-tune must not do.
- **Beat production but not the bar:** 15 fine-tunes pass G1 and G3 against production (as D and E do) yet fail G5 or FDR — they are variations of the same edge, not improvements on it.
- **Beat the global model but not the hierarchy:** stability penalty (hierarchy), regime-conditional (hierarchy), time decay (hierarchy), rolling window (hierarchy), blend, learned hierarchy + turnover smoothing, nested model selection — every one is a hierarchy model; what they gain over D is the hierarchy E already delivers.
- **Costs and extremes:** for the learned global model the best net long-short is at the 10% tails (+0.324% per week after product costs, turnover 78%); at 20% it is +0.303% (gross +0.422%).
- **Conviction:** score percentile → relative return is not monotonic (AlphaReliability = rank correlation of bucket means +0.99). The ConvictionMultiplier is reported, not applied.
- **1D after costs:** the nested cost-aware 1D strategy earns +0.144% per trade net (t +5.3, 3/4 eras positive, active 25% of weeks) — **profitable after costs under the fixed test**; but it rests on the product-specific cost estimates (single stock 5 bp, ETF 2–3 bp one way): at a flat 10 bp per side the best 1D tail (learned hierarchy (E), 5%) nets -0.019% per trade. It does not survive — treat 1D as cost-fragile.
- **1M–12M:** no model — lower-dimensional, family-level, stable-only or fundamental — is validated. The effective sample (independent periods per signal) is the binding limit, see §10.
- **Directional:** adding the out-of-sample 1W Alpha percentile to the point-in-time prior passes the prior-only gate (Brier gain vs prior +0.00060, t +5.8). Directional stays conservative: the gain is small in probability terms and earns only a research version, never a change to the Directional definition.
- **Hedge:** the λ-conditional size surface is learned per objective and volatility regime (§ SHAFFER_HEDGE_FINETUNE.md); 2 (objective, horizon, λ) cells pass H1–H5. H4 is not loosened; a redesign is proposed for a new version only.

## 1. Capability — can the new optimisers find what they claim to find?

Planted synthetic worlds (16 assets in a class / sector tree, 2002–2026, weekly, regimes switching every ~26 weeks). The same code that ran on market data must recover each planted effect, and must NOT report an improvement where none exists.

| Test | Planted / expected | Recovered | Result |
|---|---|---|---|
| ranking-specific effect | {"truth_ratio": -0.6} | {"recovered_ratio": -0.641, "rank_objective_ric": 0.3194, "value_regression_ric": 0.3047} | **PASS** |
| time-decaying relationship | {"truth_after_2014": {"momentum": 0.5, "valuation": 0.0}} | {"chosen_half_life": "3", "chosen_window": "5", "final_weights": {"momentum": 0.449, "valuation": 0.023}} | **PASS** |
| regime-switching relationship | {"truth": {"high_vol": 0.4, "low_vol": -0.4}} | {"chosen": "volatility/0.25", "recovered": {"high_vol": 0.325, "low_vol": -0.309}} | **PASS** |
| hierarchy-specific relationship | "—" | {"hierarchy_minus_global_ric": 0.1001, "t": 16.47} | **PASS** |
| transaction-cost-sensitive effect | "—" | {"chosen_by_rank_ic": "fast", "chosen_by_net_after_costs": "slow"} | **PASS** |
| no false improvement (plain world) | "—" | {"nested_minus_global_ric": 0.0006, "t": 1.03, "chosen_today": "stability classes"} | **PASS** |

The last test calibrated gate G5: on a plain linear world nested selection across the fine-tune families reached t ≈ 1 against the learned global model by chance, so G5 requires t ≥ 2 — fixed before the market run.
The earlier suite of the learned engine (10/10 passed: linear, ranking target, sparse, correlated, regime-dependent, sector-specific, horizon-specific, interaction, noise, asset-specific) still applies.

## 2. Answers

**1. Can 1W Alpha improve beyond the current learned models?** Not robustly. The best single family against the stronger live model is blend (its α is 0 from 2013 on — it IS the hierarchy) (Δ rank IC vs learned hierarchy +0.0000, t +1.0; vs learned global +0.0145, t +4.0); nested selection across all families — the honest version of 'pick the best' — gives Δ vs hierarchy -0.0040 (t -2.0). None clears G5 against both (t ≥ 2.0, ≥ 3/4 eras).

**2. Which optimisation method works best?** Ranked by the weaker of the two comparisons (vs learned global D / vs learned hierarchy E, Δ rank IC and t): blend +0.0145 (+4.0) / +0.0000 (+1.0); stability penalty (hierarchy) +0.0130 (+3.2) / -0.0014 (-0.7); regime-conditional (hierarchy) +0.0129 (+3.3) / -0.0016 (-1.0); time decay (hierarchy) +0.0121 (+3.0) / -0.0024 (-1.7); rolling window (hierarchy) +0.0109 (+2.8) / -0.0036 (-1.9); signal clusters +0.0013 (+0.8) / -0.0132 (-3.3). Worst: listwise (ListNet). Identical to a live-shadow model (the inner choice picked the baseline itself): time decay (global), interactions (economic), learned global + turnover smoothing, learned hierarchy + turnover smoothing. Hierarchy-based fine-tunes lead against D because they contain E; against E the differences are small. The ranking objectives (pairwise, listwise, relative-return ridge) are worse than the pairwise least-squares target already in use.

**3. Does blending global + hierarchy help?** α (weight on the global model's rank) chosen per era: 2009: 0.5, 2013: 0.0, 2017: 0.0, 2021: 0.0, 2025: 0.0; today α = 0.0. Rank IC +0.0671 vs global +0.0526 and hierarchy +0.0671; Δ vs global +0.0145 (t +4.0) — not a validated improvement.

**4. Does time decay help?** time decay (global): half-life (years) per era 2009: None, 2013: None, 2017: None, 2021: None, 2025: None, today None; Δ vs global +0.0000 (t —), vs hierarchy -0.0145 (t -4.0); time decay (hierarchy): half-life (years) per era 2009: none · 200 · global, 2013: 3 · 1000 · ptype, 2017: 15 · 5000 · ptype, 2021: None · 5000 · asset, 2025: None · 20000 · asset, today 15 · 5000 · ptype; Δ vs global +0.0121 (t +3.0), vs hierarchy -0.0024 (t -1.7). Not validated — 'None' (all history) is chosen whenever the inner weeks prefer it, and where a shorter memory is chosen it does not beat the full-history model out of sample.

**5. Does rolling history help?** rolling window (global): window (years) per era 2009: None, 2013: 5, 2017: None, 2021: None, 2025: None, today None; Δ vs global -0.0032 (t -2.5), vs hierarchy -0.0177 (t -4.8); rolling window (hierarchy): window (years) per era 2009: none · 200 · global, 2013: 5 · 5000 · ptype, 2017: 5 · 5000 · sector, 2021: 15 · 5000 · asset, 2025: 15 · 20000 · asset, today 15 · 20000 · asset; Δ vs global +0.0109 (t +2.8), vs hierarchy -0.0036 (t -1.9). Not validated — 'None' (all history) is chosen whenever the inner weeks prefer it, and where a shorter memory is chosen it does not beat the full-history model out of sample.

**6. Do regimes explain instability?** Regime-conditional weights (dimension and shrinkage chosen per era, heavily shrunk to the base): global per era 2009: none, 2013: volatility · 0.25, 2017: volatility · 4.0, 2021: volatility · 0.25, 2025: volatility · 4.0, Δ vs global -0.0026 (t -1.1); hierarchy Δ +0.0129 (t +3.3). Of 74 signals, 14 are REGIME DEPENDENT, 15 UNSTABLE, 12 STABLE, 33 NO EVIDENCE. Regimes explain little that survives out of sample: conditioning did not improve the ranking.

**7. Which signals are genuinely stable?** mom_12_1 (+), dist_ma200 (−), trend_quality (−), ret_1d (−), ret_1w (−), z_50 (+), mr_opportunity (−), garch_vol (+), vol_pctile (+), d_vix_1m (+), volume_z (+), dollar_mom_3m (+) — STABLE means the leave-one-era-out / leave-one-class-out weight keeps its sign and its size varies little (12 of 74). Signals with a stable sign (weaker): 12.

**8. Is the 1W edge concentrated in extreme scores?** Learned global, long top / short bottom fraction, per week after product costs: 50% +0.153% (gross +0.227%, t +4.4), 30% +0.238% (gross +0.340%, t +4.9), 20% +0.303% (gross +0.422%, t +5.2), 10% +0.324% (gross +0.463%, t +4.4), 5% +0.318% (gross +0.474%, t +3.3). Gross spread per name rises sharply toward the tails — the edge is concentrated in extreme scores; costs and n_eff decide how far out to go.

**9. Can 1D become profitable after costs?** Yes: the nested cost-aware choice (model × regime filter × tail fraction, by inner net round-trip P&L; standing aside when nothing was positive) earns +0.144% per trade (t +5.3), eras +0.000%, +0.014%, +0.134%, +0.418%, +0.162%; but it rests on the product-specific cost estimates (single stock 5 bp, ETF 2–3 bp one way): at a flat 10 bp per side the best 1D tail (learned hierarchy (E), 5%) nets -0.019% per trade. It does not survive — treat 1D as cost-fragile.

**10. Can any 1M–6M model become validated?** No. See §10 for why (sample limits) and which reduced-dimension model came closest.

**11. What hierarchy depth is best now?** The nested choice (pooling K | depth) per era: 2009: 200 · global, 2013: 5000 · ptype, 2017: 5000 · ptype, 2021: 5000 · asset, 2025: 20000 · asset; today **20000|asset**. The hierarchy's rank IC +0.0671 vs global +0.0526; Δ +0.0145 (t +4.0).

**12. What should today's learned 1W weights be?** Unchanged — the learned hierarchy already in live shadow (E); largest signed shares of |weight| (the average over assets of the hierarchy's weights (each asset uses its own node's weights; depth 20000|asset)): dist_ma200 -6.1%, mom_12_1 +5.6%, ret_1d -4.3%, ret_12m -3.9%, vix +3.9%, mr_opportunity -3.4%, sortino_252 -3.3%, ma_cross +3.2%, sharpe_252 +2.9%, dollar_mom_3m +2.8%. Full table in §5.

**13. What should today's learned scores be?** (learned hierarchy (E)) Highest: DBC +0.183, EEM +0.169, AVGO +0.156, MTUM +0.132, MRK +0.118, USO +0.111, TIP +0.110, IEI +0.103; lowest: GOOGL -0.081, T -0.089, BNDX -0.093, BIL -0.098, VZ -0.100, EWJ -0.101, FXI -0.108, CRM -0.187. All assets in §14.

**14. Does Alpha magnitude support conviction sizing?** AlphaReliability +0.99, not monotonic across 12 percentile buckets. The tails carry more expected relative return than the middle, so a ConvictionMultiplier is supported descriptively — it is reported (§7), not applied; applying it would be a new version.

**15. What hedge multiplier is optimal by λ / objective?** 1W global: λ 0.5 → 1.40×, λ 1.0 → 1.35×, λ 2.0 → 1.22×, λ 5.0 → 0.87×, λ 10.0 → 0.56×; 1M global: λ 0.5 → 1.39×, λ 1.0 → 1.35×, λ 2.0 → 1.27×, λ 5.0 → 0.96×, λ 10.0 → 0.61×; 3M global: λ 0.5 → 1.22×, λ 1.0 → 1.16×, λ 2.0 → 1.03×, λ 5.0 → 0.73×, λ 10.0 → 0.73×. The multiple falls as λ rises (isotonic by construction): risk-only users get larger hedges, profit-sensitive users lighter ones. Per objective and volatility regime in SHAFFER_HEDGE_FINETUNE.md §1.

**16. Can H4 failures be economically understood?** Yes. 29 cells beat hedge-2 on utility with FDR but failed H4: 29 on cost (> +10%), 20 on basis error (> +5%); 15 are variance-sensitive objectives. A bigger hedge mechanically costs proportionally more — the median cell bought 40.90 of extra risk reduction per extra dollar of cost. H4 caps the size of the hedge, not its efficiency. H4 is NOT changed; an efficiency-based redesign is proposed for a new research version only.

**17. Does Alpha strength change the optimal hedge?** 1W: 58351 cases; per-Alpha-bucket multiples beat one multiple walk-forward in 0/17 objectives at t ≥ 2; 1M: 27680 cases; per-Alpha-bucket multiples beat one multiple walk-forward in 1/17 objectives at t ≥ 2 (min_variance); 3M: 9033 cases; per-Alpha-bucket multiples beat one multiple walk-forward in 0/17 objectives at t ≥ 2. Across all 50 tests, 0 survive Benjamini–Hochberg FDR: **no** — conditioning the hedge size on the book's validated Alpha does not improve realised utility out of sample; isolated t ≥ 2 cells are what chance produces in this many tests.

**18. Is anything newly eligible for live shadow?** Alpha: nothing. Hedge sizing cells that pass every gate: 1M Commodity/target_vol at λ = 5.0, 1M Commodity/target_vol at λ = 10.0. They are recorded in hedge-lambda-sizing-exp with their multiples, but NOT put in live shadow: the hedge live grader measures variance per hedge group, not utility at a chosen λ, so a λ-conditional size cannot be graded honestly until a λ-aware grader exists.

**19. Is anything eligible for eventual production promotion?** Not yet — by rule. Promotion needs a model in live shadow with ≥ 60 graded live outcomes that confirm the backtest, and your approval. The two learned 1W models entered live shadow on their registration date; nothing new starts a clock today.

## 3. Model selection — 1W Alpha

Walk-forward 2009 → today (outer eras never touched by any choice). Net long-short: top minus bottom 20% of each week's scored cross-section, equal weight, after one-way product costs on traded weight (single stock 5 bp, sector ETF 3 bp, index 1 bp, …). Turnover: share of each leg replaced per week. Stability: rank autocorrelation of scores week to week (1 = no churn). Eras: complete eras where the model beat production / was not worse than the learned global (D) · hierarchy (E) model.

| Model | Family | Rank IC | Δ vs production (t) | Δ vs learned global (t) | Δ vs learned hierarchy (t) | Net weekly L/S | Turnover | Eras + vs prod | Eras ≥ global · hierarchy | FDR | Stability | Live-shadow eligible? |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| production (shaffer-alpha-2.1) | — | +0.0376 | — | — | — | — | — | — | — | — | — | (production) |
| learned global (D) | baseline | +0.0526 | +0.0150 (+2.3) | — | -0.0145 (-4.0) | +0.303% | 68% | 3/4 | 4/4 · 1/4 | — | 0.29 | already in live shadow |
| learned hierarchy (E) | baseline | +0.0671 | +0.0295 (+4.3) | +0.0145 (+4.0) | — | +0.365% | 63% | 3/4 | 3/4 · 4/4 | — | 0.37 | already in live shadow |
| blend | blend | +0.0671 | +0.0295 (+4.3) | +0.0145 (+4.0) | +0.0000 (+1.0) | +0.365% | 63% | 3/4 | 3/4 · 4/4 | yes | 0.37 | NOT VALIDATED |
| ridge (relative return) | ranking objective | +0.0437 | +0.0060 (+0.9) | -0.0090 (-3.2) | -0.0235 (-5.7) | +0.255% | 64% | 1/4 | 0/4 · 0/4 | no | 0.34 | NOT VALIDATED |
| elastic net | ranking objective | +0.0529 | +0.0153 (+2.3) | +0.0003 (+1.1) | -0.0142 (-3.9) | +0.295% | 68% | 3/4 | 3/4 · 1/4 | yes | 0.28 | NOT VALIDATED |
| pairwise (RankNet) | ranking objective | +0.0395 | +0.0018 (+0.3) | -0.0130 (-2.7) | -0.0275 (-4.8) | +0.207% | 58% | 2/4 | 2/4 · 0/4 | no | 0.48 | NOT VALIDATED |
| listwise (ListNet) | ranking objective | +0.0264 | -0.0112 (-1.7) | -0.0262 (-4.1) | -0.0406 (-5.7) | +0.001% | 44% | 0/4 | 0/4 · 0/4 | no | 0.70 | NOT VALIDATED |
| stability penalty (global) | stability | +0.0534 | +0.0157 (+2.4) | +0.0007 (+0.5) | -0.0138 (-3.7) | +0.284% | 68% | 3/4 | 3/4 · 1/4 | yes | 0.28 | NOT VALIDATED |
| stability penalty (hierarchy) | stability | +0.0657 | +0.0280 (+4.0) | +0.0130 (+3.2) | -0.0014 (-0.7) | +0.364% | 63% | 3/4 | 3/4 · 1/4 | yes | 0.36 | NOT VALIDATED |
| stability classes | stability | +0.0521 | +0.0144 (+2.2) | -0.0006 (-1.0) | -0.0150 (-4.2) | +0.309% | 68% | 3/4 | 3/4 · 1/4 | yes | 0.28 | NOT VALIDATED |
| time decay (global) | time | +0.0526 | +0.0150 (+2.3) | +0.0000 (—) | -0.0145 (-4.0) | +0.303% | 68% | 3/4 | 4/4 · 1/4 | yes | 0.29 | NOT VALIDATED |
| time decay (hierarchy) | time | +0.0647 | +0.0271 (+3.8) | +0.0121 (+3.0) | -0.0024 (-1.7) | +0.355% | 62% | 3/4 | 3/4 · 2/4 | yes | 0.39 | NOT VALIDATED |
| rolling window (global) | time | +0.0495 | +0.0118 (+1.8) | -0.0032 (-2.5) | -0.0177 (-4.8) | +0.279% | 67% | 3/4 | 3/4 · 1/4 | yes | 0.30 | NOT VALIDATED |
| rolling window (hierarchy) | time | +0.0635 | +0.0259 (+3.7) | +0.0109 (+2.8) | -0.0036 (-1.9) | +0.341% | 62% | 3/4 | 4/4 · 1/4 | yes | 0.39 | NOT VALIDATED |
| regime-conditional (global) | regime | +0.0500 | +0.0124 (+1.8) | -0.0026 (-1.1) | -0.0171 (-4.2) | +0.286% | 66% | 3/4 | 1/4 · 0/4 | yes | 0.31 | NOT VALIDATED |
| regime-conditional (hierarchy) | regime | +0.0655 | +0.0279 (+4.0) | +0.0129 (+3.3) | -0.0016 (-1.0) | +0.366% | 63% | 3/4 | 3/4 · 1/4 | yes | 0.37 | NOT VALIDATED |
| signal clusters | clusters | +0.0539 | +0.0163 (+2.5) | +0.0013 (+0.8) | -0.0132 (-3.3) | +0.284% | 70% | 3/4 | 2/4 · 1/4 | yes | 0.23 | NOT VALIDATED |
| signs | signs | +0.0530 | +0.0154 (+2.4) | +0.0004 (+0.2) | -0.0141 (-3.7) | +0.319% | 70% | 3/4 | 3/4 · 1/4 | yes | 0.24 | NOT VALIDATED |
| interactions (economic) | interactions | +0.0526 | +0.0150 (+2.3) | +0.0000 (—) | -0.0145 (-4.0) | +0.303% | 68% | 3/4 | 4/4 · 1/4 | yes | 0.29 | NOT VALIDATED |
| interactions (GBM-suggested) | interactions | +0.0526 | +0.0150 (+2.3) | +0.0000 (—) | -0.0145 (-4.0) | +0.303% | 68% | 3/4 | 4/4 · 1/4 | — | 0.29 | descriptive only (selection not PIT) |
| learned global + turnover smoothing | turnover | +0.0526 | +0.0150 (+2.3) | +0.0000 (—) | -0.0145 (-4.0) | +0.303% | 68% | 3/4 | 4/4 · 1/4 | yes | 0.29 | NOT VALIDATED |
| learned hierarchy + turnover smoothing | turnover | +0.0671 | +0.0295 (+4.3) | +0.0145 (+4.0) | +0.0000 (—) | +0.365% | 63% | 3/4 | 3/4 · 4/4 | yes | 0.37 | NOT VALIDATED |
| nested model selection | meta | +0.0631 | +0.0255 (+3.7) | +0.0105 (+3.3) | -0.0040 (-2.0) | +0.367% | 64% | 3/4 | 4/4 · 2/4 | yes | 0.35 | NOT VALIDATED |

Gates per model (G1–G4 vs production, G5 vs both learned live-shadow models):

| Model | G1 | G2 (split t) | G3 | G4 | G5 | Quintile spread | Pearson IC | Monotonicity | Hit vs median |
|---|---|---|---|---|---|---|---|---|---|
| learned global (D) | ✓ | ✓ (+2.1) | ✓ | ✓ | — | +0.42% | +0.0449 | +0.98 | 52% |
| learned hierarchy (E) | ✓ | ✓ (+4.7) | ✓ | ✓ | — | +0.48% | +0.0550 | +0.99 | 52% |
| blend | ✓ | ✓ (+4.7) | ✓ | ✓ | ✗ | +0.48% | +0.0509 | +0.96 | 52% |
| ridge (relative return) | ✗ | ✓ (+1.6) | ✗ | ✓ | ✗ | +0.37% | +0.0402 | +0.92 | 52% |
| elastic net | ✓ | ✓ (+2.2) | ✓ | ✓ | ✗ | +0.42% | +0.0450 | +0.99 | 52% |
| pairwise (RankNet) | ✗ | ✓ (+1.3) | ✗ | ✓ | ✗ | +0.31% | +0.0328 | +0.90 | 52% |
| listwise (ListNet) | ✗ | ✗ (+0.1) | ✗ | ✗ | ✗ | +0.10% | +0.0277 | +0.88 | 50% |
| stability penalty (global) | ✓ | ✓ (+3.2) | ✓ | ✓ | ✗ | +0.40% | +0.0437 | +0.96 | 52% |
| stability penalty (hierarchy) | ✓ | ✓ (+5.9) | ✓ | ✓ | ✗ | +0.47% | +0.0523 | +0.99 | 52% |
| stability classes | ✓ | ✓ (+2.6) | ✓ | ✓ | ✗ | +0.43% | +0.0453 | +0.99 | 52% |
| time decay (global) | ✓ | ✓ (+2.1) | ✓ | ✓ | ✗ | +0.42% | +0.0449 | +0.98 | 52% |
| time decay (hierarchy) | ✓ | ✓ (+4.3) | ✓ | ✓ | ✗ | +0.47% | +0.0508 | +0.96 | 52% |
| rolling window (global) | ✗ | ✓ (+2.1) | ✓ | ✓ | ✗ | +0.40% | +0.0422 | +0.99 | 52% |
| rolling window (hierarchy) | ✓ | ✓ (+2.5) | ✓ | ✓ | ✗ | +0.45% | +0.0534 | +0.95 | 52% |
| regime-conditional (global) | ✗ | ✓ (+1.7) | ✓ | ✓ | ✗ | +0.41% | +0.0443 | +0.99 | 52% |
| regime-conditional (hierarchy) | ✓ | ✓ (+4.9) | ✓ | ✓ | ✗ | +0.48% | +0.0543 | +0.99 | 52% |
| signal clusters | ✓ | ✓ (+2.8) | ✓ | ✓ | ✗ | +0.41% | +0.0457 | +0.99 | 52% |
| signs | ✓ | ✓ (+2.5) | ✓ | ✓ | ✗ | +0.43% | +0.0468 | +0.96 | 52% |
| interactions (economic) | ✓ | ✓ (+2.1) | ✓ | ✓ | ✗ | +0.42% | +0.0449 | +0.98 | 52% |
| interactions (GBM-suggested) | ✓ | ✓ (+2.1) | ✓ | ✓ | ✗ | +0.42% | +0.0449 | +0.98 | 52% |
| learned global + turnover smoothing | ✓ | ✓ (+2.1) | ✓ | ✓ | ✗ | +0.42% | +0.0449 | +0.98 | 52% |
| learned hierarchy + turnover smoothing | ✓ | ✓ (+4.7) | ✓ | ✓ | ✗ | +0.48% | +0.0550 | +0.99 | 52% |
| nested model selection | ✓ | ✓ (+4.7) | ✓ | ✓ | ✗ | +0.48% | +0.0459 | +0.99 | 52% |

Rank IC by era (walk-forward):

| Model | 2009–12 | 2013–16 | 2017–20 | 2021–24 | 2025– | Δ vs global by era | Δ vs hierarchy by era |
|---|---|---|---|---|---|---|---|
| learned global (D) | +0.0407 | +0.0351 | +0.0431 | +0.0732 | +0.0996 | +0.000 +0.000 +0.000 +0.000 +0.000 | +0.000 -0.014 -0.033 -0.015 -0.007 |
| learned hierarchy (E) | +0.0407 | +0.0487 | +0.0758 | +0.0869 | +0.1126 | -0.000 +0.014 +0.033 +0.015 +0.007 | +0.000 +0.000 +0.000 +0.000 +0.000 |
| blend | +0.0407 | +0.0487 | +0.0758 | +0.0869 | +0.1126 | -0.000 +0.014 +0.033 +0.015 +0.007 | +0.000 +0.000 +0.000 +0.000 +0.000 |
| ridge (relative return) | +0.0360 | +0.0231 | +0.0321 | +0.0654 | +0.0906 | -0.006 -0.012 -0.011 -0.008 -0.008 | -0.005 -0.026 -0.044 -0.023 -0.015 |
| elastic net | +0.0403 | +0.0350 | +0.0435 | +0.0738 | +0.1005 | -0.000 +0.000 +0.000 +0.001 +0.001 | +0.000 -0.014 -0.032 -0.014 -0.006 |
| pairwise (RankNet) | +0.0183 | +0.0353 | +0.0434 | +0.0475 | +0.0731 | -0.020 +0.000 +0.000 -0.026 -0.022 | -0.020 -0.014 -0.032 -0.041 -0.029 |
| listwise (ListNet) | +0.0261 | +0.0173 | +0.0242 | +0.0267 | +0.0573 | -0.016 -0.018 -0.019 -0.047 -0.042 | -0.016 -0.032 -0.052 -0.061 -0.049 |
| stability penalty (global) | +0.0407 | +0.0351 | +0.0479 | +0.0705 | +0.1016 | +0.000 +0.000 +0.005 -0.003 +0.002 | +0.000 -0.014 -0.028 -0.017 -0.005 |
| stability penalty (hierarchy) | +0.0407 | +0.0448 | +0.0776 | +0.0816 | +0.1158 | -0.001 +0.010 +0.035 +0.009 +0.010 | -0.000 -0.004 +0.002 -0.005 +0.003 |
| stability classes | +0.0407 | +0.0351 | +0.0431 | +0.0707 | +0.0996 | +0.000 +0.000 +0.000 -0.002 +0.000 | +0.000 -0.014 -0.033 -0.017 -0.007 |
| time decay (global) | +0.0407 | +0.0351 | +0.0431 | +0.0732 | +0.0996 | +0.000 +0.000 +0.000 +0.000 +0.000 | +0.000 -0.014 -0.033 -0.015 -0.007 |
| time decay (hierarchy) | +0.0407 | +0.0397 | +0.0743 | +0.0869 | +0.1126 | -0.000 +0.005 +0.031 +0.015 +0.007 | +0.000 -0.009 -0.001 +0.000 +0.000 |
| rolling window (global) | +0.0407 | +0.0210 | +0.0431 | +0.0732 | +0.0996 | +0.000 -0.014 +0.000 +0.000 +0.000 | +0.000 -0.028 -0.033 -0.015 -0.007 |
| rolling window (hierarchy) | +0.0407 | +0.0451 | +0.0639 | +0.0869 | +0.1115 | +0.000 +0.010 +0.021 +0.015 +0.006 | +0.000 -0.004 -0.012 -0.000 -0.001 |
| regime-conditional (global) | +0.0407 | +0.0307 | +0.0486 | +0.0618 | +0.0954 | -0.001 -0.002 +0.006 -0.011 -0.004 | -0.001 -0.017 -0.027 -0.026 -0.012 |
| regime-conditional (hierarchy) | +0.0407 | +0.0466 | +0.0794 | +0.0822 | +0.1038 | -0.001 +0.013 +0.036 +0.010 -0.002 | -0.001 -0.001 +0.004 -0.005 -0.009 |
| signal clusters | +0.0407 | +0.0393 | +0.0486 | +0.0691 | +0.1000 | -0.000 +0.004 +0.006 -0.004 +0.000 | +0.000 -0.010 -0.027 -0.019 -0.007 |
| signs | +0.0407 | +0.0351 | +0.0483 | +0.0705 | +0.0988 | +0.000 +0.000 +0.005 -0.003 -0.002 | +0.000 -0.014 -0.027 -0.017 -0.009 |
| interactions (economic) | +0.0407 | +0.0351 | +0.0431 | +0.0732 | +0.0996 | +0.000 +0.000 +0.000 +0.000 +0.000 | +0.000 -0.014 -0.033 -0.015 -0.007 |
| interactions (GBM-suggested) | +0.0407 | +0.0351 | +0.0431 | +0.0732 | +0.0996 | +0.000 +0.000 +0.000 +0.000 +0.000 | +0.000 -0.014 -0.033 -0.015 -0.007 |
| learned global + turnover smoothing | +0.0407 | +0.0351 | +0.0431 | +0.0732 | +0.0996 | +0.000 +0.000 +0.000 +0.000 +0.000 | +0.000 -0.014 -0.033 -0.015 -0.007 |
| learned hierarchy + turnover smoothing | +0.0407 | +0.0487 | +0.0758 | +0.0869 | +0.1126 | -0.000 +0.014 +0.033 +0.015 +0.007 | +0.000 +0.000 +0.000 +0.000 +0.000 |
| nested model selection | +0.0407 | +0.0351 | +0.0758 | +0.0822 | +0.1126 | +0.000 +0.000 +0.033 +0.011 +0.007 | +0.000 -0.014 +0.000 -0.004 +0.000 |

## 4. What the inner walk-forward chose

Hyperparameters chosen at each outer cut from inner weeks matured before it (≥ 52 inner weeks, else the default); 'final' = today's choice from all matured data.

| Model | 2009 | 2013 | 2017 | 2021 | 2025 | split 2018 | final | Note |
|---|---|---|---|---|---|---|---|---|
| learned global (D) | — | — | — | — | — | — | global |  |
| learned hierarchy (E) | 200 · global | 5000 · ptype | 5000 · ptype | 5000 · asset | 20000 · asset | 5000 · ptype | 20000 · asset |  |
| blend | 0.5 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 |  |
| ridge (relative return) | — | — | — | — | — | — | - |  |
| elastic net | 0.003 | 0.003 | 0.003 | 0.003 | 0.01 | 0.003 | 0.01 |  |
| pairwise (RankNet) | — | — | — | — | — | — | - |  |
| listwise (ListNet) | — | — | — | — | — | — | - |  |
| stability penalty (global) | 0.0 | 0.0 | 30000.0 | 10000.0 | 10000.0 | 10000.0 | 10000.0 |  |
| stability penalty (hierarchy) | none · 200 · global | 100000.0 · 1000 · ptype | 100000.0 · 20000 · ptype | 100000.0 · 5000 · ptype | 100000.0 · 20000 · asset | 100000.0 · 20000 · ptype | 30000.0 · 20000 · ptype |  |
| stability classes | 0 | 0 | 0 | 1000.0 | 0 | 1000.0 | 1000.0 |  |
| time decay (global) | None | None | None | None | None | None | None |  |
| time decay (hierarchy) | none · 200 · global | 3 · 1000 · ptype | 15 · 5000 · ptype | None · 5000 · asset | None · 20000 · asset | 10 · 5000 · ptype | 15 · 5000 · ptype |  |
| rolling window (global) | None | 5 | None | None | None | None | None |  |
| rolling window (hierarchy) | none · 200 · global | 5 · 5000 · ptype | 5 · 5000 · sector | 15 · 5000 · asset | 15 · 20000 · asset | 5 · 5000 · sector | 15 · 20000 · asset |  |
| regime-conditional (global) | none | volatility · 0.25 | volatility · 4.0 | volatility · 0.25 | volatility · 4.0 | volatility · 0.25 | volatility · 4.0 |  |
| regime-conditional (hierarchy) | none | volatility · 0.25 | volatility · 4.0 | volatility · 0.25 | volatility · 1.0 | volatility · 4.0 | volatility · 4.0 |  |
| signal clusters | ridge | fuse 50000 | fuse 50000 | fuse 50000 | fuse 50000 | fuse 50000 | fuse 50000 |  |
| signs | free | free | economic sign | economic sign | economic sign | economic sign unless learned sign stable | economic sign |  |
| interactions (economic) | none | none | none | none | none | none | none |  |
| interactions (GBM-suggested) | none | none | none | none | none | none | none | the three interactions were read off a boosting diagnostic run over every era, so their selection is not point in time — descriptive only, never eligible |
| learned global + turnover smoothing | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 |  |
| learned hierarchy + turnover smoothing | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 |  |
| nested model selection | learned global (D) | learned global (D) | blend | regime-conditional (hierarchy) | blend | blend | blend |  |

## 5. Weights — and exactly why a model differs

Signed share of |weight| (so different scalings compare). *Production*: the average production share on 1W research records. *Learned global*: D's weights from all matured data. *Learned hierarchy*: the record-weighted average of the hierarchy's deployable weights over assets (its global node equals D's). *Era stability*: leave-one-era-out / leave-one-class-out class. *Reliability*: out-of-sample t of the signal's global weight (learned run). *Contribution to ΔIC*: change in the walk-forward mean weekly rank IC when ONLY that signal's weight is moved from D's to the challenger's, era by era — the columns sum approximately to the model's Δ vs learned global.

### elastic net — Δ vs learned global +0.0003 (t +1.1); sum of per-signal contributions -0.0024

| Signal | Production | Learned global | Learned hierarchy | New challenger | Era stability | Reliability (OOS t) | Contribution to ΔIC |
|---|---|---|---|---|---|---|---|
| mom_12_1 | +0.9% | +6.2% | +5.6% | +5.2% | STABLE | +3.5 | -0.00132 |
| sharpe_252 | +0.4% | +3.0% | +2.9% | -0.0% | REGIME DEPENDENT | +2.9 | -0.00115 |
| dist_ma200 | +0.8% | -6.1% | -6.1% | -5.7% | STABLE | +5.6 | -0.00111 |
| ret_12m | +0.6% | -3.8% | -3.9% | -0.5% | UNSTABLE | +1.0 | +0.00054 |
| rsi_14 | -7.0% | -2.4% | -2.0% | -2.9% | UNSTABLE | +2.7 | -0.00056 |
| sortino_252 | +0.4% | -3.5% | -3.3% | -0.3% | NO EVIDENCE | +3.0 | +0.00043 |
| z_50 | -5.4% | +1.9% | +1.8% | +1.4% | STABLE | +1.2 | +0.00044 |
| ram | +1.7% | +2.3% | +2.4% | +2.3% | UNSTABLE | +2.1 | -0.00040 |
| ma_cross | +0.6% | +3.2% | +3.2% | +1.8% | NO EVIDENCE | +1.2 | -0.00039 |
| ret_6m | +1.0% | +0.9% | +0.6% | +0.0% | NO EVIDENCE | +2.0 | +0.00029 |
| d_credit_3m | +0.0% | -1.8% | -1.8% | -1.7% | NO EVIDENCE | -0.3 | -0.00028 |
| excess_3m | +2.9% | +1.3% | +1.8% | +1.3% | UNSTABLE | +0.8 | +0.00026 |
| macd | +4.1% | +1.6% | +0.8% | +0.8% | NO EVIDENCE | +0.3 | +0.00022 |
| vol_pctile | +0.0% | +3.0% | +2.8% | +4.2% | STABLE | +1.6 | -0.00019 |
| mr_opportunity | +4.5% | -3.4% | -3.4% | -4.4% | STABLE | +2.2 | +0.00018 |
| credit_signal | -0.7% | -1.2% | -1.0% | -1.0% | NO EVIDENCE | +0.5 | +0.00014 |
| rel_strength_6m | +1.3% | -0.3% | +0.1% | -0.0% | REGIME DEPENDENT | +0.8 | -0.00014 |
| drawdown_252 | +0.2% | -0.9% | -1.0% | -1.3% | REGIME DEPENDENT | +0.5 | +0.00013 |
| fund_quality | +0.0% | -0.3% | -0.3% | -0.0% | NO EVIDENCE | +1.5 | -0.00012 |
| ret_1m | -3.5% | +2.9% | +2.8% | +3.0% | UNSTABLE | +2.0 | +0.00012 |
| roe | +0.0% | +0.4% | +0.4% | +0.1% | NO EVIDENCE | -2.4 | +0.00011 |
| ret_1w | -16.2% | -1.8% | -1.7% | -3.2% | STABLE | +1.1 | +0.00009 |
| ret_3m | +1.8% | -1.8% | -2.3% | -1.8% | NO EVIDENCE | +1.1 | -0.00010 |
| pctile_252 | +2.4% | -0.6% | -0.6% | -0.2% | NO EVIDENCE | +0.4 | -0.00009 |
| garch_vol | +0.0% | +0.7% | +0.9% | +1.1% | STABLE | -0.9 | -0.00009 |
| y10 | +0.0% | +1.9% | +1.6% | +2.4% | NO EVIDENCE | -0.3 | +0.00007 |
| slope_10y3m | +0.0% | -1.3% | -1.8% | -1.6% | NO EVIDENCE | +1.6 | -0.00007 |
| idio_vol_252 | -0.2% | -1.0% | -0.7% | -1.4% | NO EVIDENCE | -2.3 | +0.00007 |
| dollar_mom_3m | +0.0% | +3.4% | +2.8% | +5.3% | STABLE | +1.3 | -0.00005 |
| ar1_63 | +0.5% | -0.3% | -0.2% | -0.2% | NO EVIDENCE | -1.4 | +0.00007 |

Why: the gain comes from ret_12m (+0.00054), z_50 (+0.00044), sortino_252 (+0.00043), ret_6m (+0.00029), excess_3m (+0.00026); it is given back by mom_12_1 (-0.00132), sharpe_252 (-0.00115), dist_ma200 (-0.00111).

### All 74 signals — today's weights under every global fine-tune (signed share)

| Signal | Production | D | ridge (relative return) | elastic net | pairwise (RankNet) | listwise (ListNet) | stability penalty (global) | stability classes | time decay (global) | rolling window (global) |
|---|---|---|---|---|---|---|---|---|---|---|
| ret_3m | +1.8% | -1.8% | -0.8% | -1.8% | -2.0% | -1.2% | -2.3% | -0.9% | -1.8% | -1.8% |
| ret_6m | +1.0% | +0.9% | +1.5% | +0.0% | +1.3% | +1.7% | -0.3% | +0.1% | +0.9% | +0.9% |
| ret_12m | +0.6% | -3.8% | -4.7% | -0.5% | +4.7% | -8.7% | +0.5% | -0.2% | -3.8% | -3.8% |
| mom_12_1 | +0.9% | +6.2% | +4.7% | +5.2% | -0.6% | +6.5% | +1.3% | +6.8% | +6.2% | +6.2% |
| ret_1m | -3.5% | +2.9% | +2.7% | +3.0% | +1.1% | +2.6% | +1.2% | +1.8% | +2.9% | +2.9% |
| ma_cross | +0.6% | +3.2% | +3.5% | +1.8% | +2.4% | +4.1% | +0.1% | +0.8% | +3.2% | +3.2% |
| dist_ma200 | +0.8% | -6.1% | -6.7% | -5.7% | -6.0% | -6.0% | -0.5% | -6.4% | -6.1% | -6.1% |
| macd | +4.1% | +1.6% | +1.2% | +0.8% | -0.4% | +0.6% | +0.3% | +0.7% | +1.6% | +1.6% |
| pctile_252 | +2.4% | -0.6% | -0.7% | -0.2% | +0.9% | -2.0% | -0.6% | -0.1% | -0.6% | -0.6% |
| trend_quality | +1.3% | -1.0% | -1.5% | -0.5% | -2.6% | -1.7% | -1.2% | -0.6% | -1.0% | -1.0% |
| ret_1d | -15.2% | -3.7% | -3.2% | -5.9% | -2.8% | -1.2% | -6.4% | -6.8% | -3.7% | -3.7% |
| ret_1w | -16.2% | -1.8% | -1.9% | -3.2% | -1.2% | -0.2% | -4.0% | -4.2% | -1.8% | -1.8% |
| z_20 | -7.2% | -1.4% | -1.3% | -1.5% | -0.0% | -0.8% | -1.9% | -1.4% | -1.4% | -1.4% |
| z_50 | -5.4% | +1.9% | +2.8% | +1.4% | +3.8% | +3.1% | +1.5% | +1.3% | +1.9% | +1.9% |
| rsi_14 | -7.0% | -2.4% | -2.0% | -2.9% | -3.3% | -1.4% | -2.7% | -1.5% | -2.4% | -2.4% |
| bb_pctb | -7.2% | -1.4% | -1.3% | -1.7% | -0.0% | -0.8% | -1.9% | -1.4% | -1.4% | -1.4% |
| mr_opportunity | +4.5% | -3.4% | -3.0% | -4.4% | -0.8% | -0.7% | -4.5% | -4.3% | -3.4% | -3.4% |
| value_5y | +0.0% | +0.3% | +0.9% | +0.3% | -0.0% | +1.7% | +0.3% | +0.4% | +0.3% | +0.3% |
| earnings_yield | +0.0% | -0.1% | -0.3% | -0.1% | +0.3% | +0.4% | -0.3% | -0.4% | -0.1% | -0.1% |
| pe_rel_5y | -0.0% | -0.1% | -0.1% | -0.1% | +1.0% | -0.1% | -0.0% | -0.2% | -0.1% | -0.1% |
| book_to_price | +0.0% | -0.9% | -0.5% | -1.3% | -0.7% | +0.3% | -0.3% | -1.1% | -0.9% | -0.9% |
| sales_yield | +0.0% | -0.0% | -0.2% | -0.0% | +0.4% | -0.5% | -0.5% | -0.1% | -0.0% | -0.0% |
| net_margin | +0.0% | -0.3% | +0.0% | -0.3% | +0.2% | -1.4% | -0.0% | -0.4% | -0.3% | -0.3% |
| roe | +0.0% | +0.4% | +0.4% | +0.1% | -1.5% | -0.2% | +0.0% | +0.4% | +0.4% | +0.4% |
| fund_quality | +0.0% | -0.3% | -0.5% | -0.0% | +2.0% | +1.4% | +0.1% | -0.0% | -0.3% | -0.3% |
| eps_growth_yoy | +0.0% | +0.1% | -0.2% | +0.0% | -0.1% | -0.2% | +0.1% | +0.1% | +0.1% | +0.1% |
| rev_growth_yoy | +0.0% | +0.1% | +0.1% | +0.0% | +1.0% | +0.2% | +0.1% | +0.1% | +0.1% | +0.1% |
| sharpe_252 | +0.4% | +3.0% | +1.7% | -0.0% | +4.9% | +5.5% | +0.2% | -0.0% | +3.0% | +3.0% |
| sortino_252 | +0.4% | -3.5% | -2.0% | -0.3% | -4.4% | -5.2% | +0.2% | -0.2% | -3.5% | -3.5% |
| alpha_252 | +0.8% | +0.1% | +1.8% | +0.2% | +0.4% | +2.1% | +1.4% | +0.5% | +0.1% | +0.1% |
| ram | +1.7% | +2.3% | +2.5% | +2.3% | +1.1% | +1.1% | +3.1% | +1.0% | +2.3% | +2.3% |
| vol_20 | +0.0% | -2.0% | -1.4% | -1.2% | -1.7% | +0.9% | -0.7% | -1.3% | -2.0% | -2.0% |
| vol_60 | +0.0% | -0.4% | -1.5% | -0.0% | +2.5% | -3.4% | -0.8% | -0.5% | -0.4% | -0.4% |
| downside_vol_60 | +0.0% | -1.2% | +0.5% | -1.0% | -2.6% | -2.3% | -0.7% | -0.8% | -1.2% | -1.2% |
| ewma_vol | +0.0% | +1.9% | +2.8% | +0.0% | +0.3% | +2.3% | -0.1% | -0.2% | +1.9% | +1.9% |
| garch_vol | +0.0% | +0.7% | +0.2% | +1.1% | -0.9% | +0.9% | +1.1% | +1.1% | +0.7% | +0.7% |
| vol_ratio | +0.0% | -1.8% | -2.1% | -2.4% | -0.7% | -2.0% | -2.6% | -1.8% | -1.8% | -1.8% |
| vol_of_vol | +0.0% | +0.3% | -0.5% | +0.3% | -1.4% | -0.6% | +0.6% | +0.4% | +0.3% | +0.3% |
| vol_pctile | +0.0% | +3.0% | +2.7% | +4.2% | +2.8% | +0.0% | +4.5% | +4.2% | +3.0% | +3.0% |
| skew_60 | -2.4% | +0.6% | +0.9% | +0.9% | -0.9% | -0.4% | +1.0% | +0.9% | +0.6% | +0.6% |
| kurt_60 | +0.0% | +0.1% | +0.4% | +0.2% | +0.5% | -1.6% | +0.3% | +0.2% | +0.1% | +0.1% |
| ar1_63 | +0.5% | -0.3% | -0.4% | -0.2% | +0.2% | +1.0% | -0.4% | -0.3% | -0.3% | -0.3% |
| acf1_252 | +0.0% | +0.6% | +0.5% | +0.8% | +0.7% | +0.4% | +1.1% | +1.0% | +0.6% | +0.6% |
| half_life | +0.0% | +0.8% | +0.7% | +0.9% | -0.7% | +0.8% | +1.2% | +1.1% | +0.8% | +0.8% |
| adf_t | +0.0% | -0.1% | -0.1% | -0.0% | +0.3% | +1.1% | -0.2% | -0.0% | -0.1% | -0.1% |
| idio_vol_252 | -0.2% | -1.0% | -1.3% | -1.4% | -0.2% | +0.7% | -1.7% | -1.0% | -1.0% | -1.0% |
| drawdown_252 | +0.2% | -0.9% | +0.0% | -1.3% | -3.0% | +1.6% | -1.7% | -1.6% | -0.9% | -0.9% |
| y10 | +0.0% | +1.9% | +0.5% | +2.4% | -0.2% | +0.1% | +2.3% | +1.6% | +1.9% | +1.9% |
| d_y10_3m | +0.0% | -0.6% | -0.2% | -0.3% | +2.0% | -0.7% | -0.5% | -0.0% | -0.6% | -0.6% |
| slope_10y3m | +0.0% | -1.3% | -0.6% | -1.6% | -1.3% | -0.7% | -1.6% | -1.1% | -1.3% | -1.3% |
| d_slope_3m | +0.0% | +1.2% | +0.9% | +1.3% | -0.1% | +1.1% | +1.6% | +1.0% | +1.2% | +1.2% |
| real_y10 | +0.0% | +0.5% | +0.7% | +0.9% | +0.8% | +0.5% | +1.4% | +1.4% | +0.5% | +0.5% |
| breakeven_10y | +0.0% | -1.8% | -1.5% | -2.7% | -0.8% | +0.2% | -3.2% | -2.4% | -1.8% | -1.8% |
| rate_duration | +2.7% | +0.5% | +0.6% | +0.7% | -0.0% | +0.8% | +0.8% | +0.7% | +0.5% | +0.5% |
| credit_spread | +0.0% | +0.1% | +0.3% | +0.0% | -0.1% | -0.4% | -0.3% | -0.4% | +0.1% | +0.1% |
| d_credit_3m | +0.0% | -1.8% | -2.1% | -1.7% | -0.7% | +0.5% | -1.3% | -0.9% | -1.8% | -1.8% |
| credit_signal | -0.7% | -1.2% | -1.6% | -1.0% | -1.9% | +0.9% | -0.7% | -0.5% | -1.2% | -1.2% |
| cpi_yoy | +0.0% | +0.8% | +0.7% | +1.2% | +1.2% | +0.4% | +1.5% | +1.2% | +0.8% | +0.8% |
| unemp_gap | +0.0% | +0.9% | +0.9% | +1.4% | +0.6% | -0.7% | +1.8% | +1.5% | +0.9% | +0.9% |
| oil_mom_3m | +0.0% | -0.7% | -1.0% | -1.0% | +0.4% | -0.5% | -1.3% | -1.1% | -0.7% | -0.7% |
| gold_mom_3m | +0.9% | -0.3% | -0.8% | -0.4% | -0.5% | -1.1% | -0.7% | -0.5% | -0.3% | -0.3% |
| nfci | +0.0% | +0.3% | +0.6% | +0.4% | +0.4% | -0.2% | +0.6% | +0.5% | +0.3% | +0.3% |
| fed_bs_growth | +0.0% | -0.8% | -0.9% | -1.1% | -1.3% | -0.4% | -1.4% | -1.2% | -0.8% | -0.8% |
| vix | +0.0% | +3.2% | +4.4% | +4.9% | +3.2% | +1.0% | +5.0% | +5.6% | +3.2% | +3.2% |
| d_vix_1m | -2.6% | +1.5% | +1.6% | +2.4% | +0.4% | +0.5% | +3.2% | +2.8% | +1.5% | +1.5% |
| volume_z | -0.8% | +1.2% | +1.5% | +1.9% | +0.6% | +0.9% | +2.3% | +2.2% | +1.2% | +1.2% |
| dollar_mom_3m | +0.0% | +3.4% | +3.1% | +5.3% | +4.2% | +1.4% | +5.7% | +6.1% | +3.4% | +3.4% |
| beta_252 | -0.1% | -1.3% | +0.0% | -1.8% | -1.0% | -0.1% | -2.0% | -2.2% | -1.3% | -1.3% |
| corr_252 | -0.0% | +0.7% | -0.8% | +0.8% | +1.6% | -1.3% | +1.0% | +1.2% | +0.7% | +0.7% |
| rate_beta_252 | +0.0% | +0.0% | +0.6% | +0.0% | +0.0% | +0.5% | -0.1% | -0.0% | +0.0% | +0.0% |
| dollar_beta_252 | +0.0% | -0.6% | -1.6% | -0.9% | -0.9% | -0.5% | -1.2% | -0.9% | -0.6% | -0.6% |
| excess_3m | +2.9% | +1.3% | +1.2% | +1.3% | +2.5% | -0.4% | +1.0% | +0.7% | +1.3% | +1.3% |
| rel_strength_6m | +1.3% | -0.3% | -0.4% | -0.0% | +0.6% | +0.2% | -0.2% | +0.6% | -0.3% | -0.3% |
| rel_value | +1.5% | -0.5% | -0.2% | -0.6% | +1.4% | -0.4% | -1.1% | -0.6% | -0.5% | -0.5% |

## 6. Score thresholds and costs (1W)

Long the top / short the bottom fraction of each week's cross-section; net after product-specific costs, and with a flat 10 bp per side for sensitivity. n = weeks; persistence = average weeks a name stays in a leg.

| Model | Tails | Gross/wk | Net/wk | Net (flat 10 bp) | t (net) | Net/yr | Turnover | Hit | Max drawdown | Persistence (wk) |
|---|---|---|---|---|---|---|---|---|---|---|
| production | 50% | +0.122% | +0.057% | -0.029% | +1.8 | +3.0% | 38% | 50% | -12.3% | 2.8 |
| production | 30% | +0.180% | +0.096% | -0.014% | +2.2 | +5.0% | 48% | 51% | -20.7% | 2.2 |
| production | 20% | +0.212% | +0.118% | -0.002% | +2.3 | +6.1% | 54% | 50% | -27.8% | 1.9 |
| production | 10% | +0.242% | +0.131% | +0.004% | +2.0 | +6.8% | 60% | 53% | -35.2% | 1.8 |
| production | 5% | +0.308% | +0.185% | +0.058% | +2.1 | +9.6% | 63% | 52% | -40.8% | 1.7 |
| learned global (D) | 50% | +0.227% | +0.153% | +0.053% | +4.4 | +7.9% | 43% | 55% | -13.0% | 2.4 |
| learned global (D) | 30% | +0.340% | +0.238% | +0.104% | +4.9 | +12.4% | 59% | 55% | -15.5% | 1.7 |
| learned global (D) | 20% | +0.422% | +0.303% | +0.151% | +5.2 | +15.7% | 68% | 56% | -19.8% | 1.5 |
| learned global (D) | 10% | +0.463% | +0.324% | +0.151% | +4.4 | +16.8% | 78% | 56% | -29.2% | 1.3 |
| learned global (D) | 5% | +0.474% | +0.318% | +0.132% | +3.3 | +16.6% | 85% | 53% | -53.6% | 1.2 |
| learned hierarchy (E) | 50% | +0.262% | +0.193% | +0.101% | +5.6 | +10.0% | 40% | 57% | -13.5% | 2.6 |
| learned hierarchy (E) | 30% | +0.379% | +0.285% | +0.161% | +5.9 | +14.8% | 55% | 60% | -18.3% | 1.9 |
| learned hierarchy (E) | 20% | +0.475% | +0.365% | +0.223% | +6.2 | +19.0% | 63% | 59% | -21.9% | 1.6 |
| learned hierarchy (E) | 10% | +0.613% | +0.481% | +0.318% | +6.3 | +25.0% | 74% | 60% | -29.2% | 1.4 |
| learned hierarchy (E) | 5% | +0.718% | +0.570% | +0.392% | +5.8 | +29.6% | 81% | 58% | -53.6% | 1.3 |
| blend | 50% | +0.262% | +0.193% | +0.101% | +5.6 | +10.0% | 40% | 57% | -13.5% | 2.6 |
| blend | 30% | +0.379% | +0.285% | +0.161% | +5.9 | +14.8% | 55% | 60% | -18.3% | 1.9 |
| blend | 20% | +0.475% | +0.365% | +0.223% | +6.2 | +19.0% | 63% | 59% | -21.9% | 1.6 |
| blend | 10% | +0.613% | +0.481% | +0.318% | +6.3 | +25.0% | 74% | 60% | -29.2% | 1.4 |
| blend | 5% | +0.718% | +0.570% | +0.392% | +5.8 | +29.6% | 81% | 58% | -53.6% | 1.3 |
| nested model selection | 50% | +0.260% | +0.190% | +0.096% | +5.5 | +9.9% | 41% | 57% | -13.0% | 2.6 |
| nested model selection | 30% | +0.387% | +0.292% | +0.165% | +6.0 | +15.2% | 56% | 59% | -15.5% | 1.9 |
| nested model selection | 20% | +0.479% | +0.367% | +0.223% | +6.2 | +19.1% | 64% | 59% | -19.8% | 1.6 |
| nested model selection | 10% | +0.631% | +0.497% | +0.333% | +6.5 | +25.8% | 74% | 60% | -29.2% | 1.4 |
| nested model selection | 5% | +0.693% | +0.543% | +0.365% | +5.4 | +28.2% | 82% | 58% | -53.6% | 1.3 |

Rank churn (week-to-week rank correlation of the same assets' scores): production 0.42, learned global (D) 0.29, learned hierarchy (E) 0.37, blend 0.37, nested model selection 0.35.

## 7. Conviction calibration — AlphaReliability and the ConvictionMultiplier (reported, not applied)

Within-week score percentile → next-week return relative to the week's median (walk-forward records). CI: 95%, week-clustered. ConvictionMultiplier = bucket mean ÷ mean |bucket mean| of the upper half.

**production** — AlphaReliability +0.90 · not monotonic

| Percentile | n | Mean rel. return | Median | Hit | Vol | Downside | 95% CI | ConvictionMultiplier |
|---|---|---|---|---|---|---|---|---|
| 0–5 | 6543 | -0.12% | -0.08% | 47% | 3.8% | -2.13% | [-0.25%, -0.01%] | -0.99 |
| 5–10 | 6081 | -0.04% | -0.02% | 49% | 3.3% | -1.87% | [-0.15%, +0.03%] | -0.37 |
| 10–20 | 11947 | -0.03% | -0.01% | 49% | 3.0% | -1.88% | [-0.12%, +0.03%] | -0.28 |
| 20–30 | 12114 | +0.01% | -0.03% | 48% | 3.1% | -1.83% | [-0.06%, +0.08%] | +0.11 |
| 30–40 | 12017 | +0.12% | +0.00% | 50% | 3.2% | -1.88% | [+0.05%, +0.18%] | +0.98 |
| 40–50 | 11946 | -0.02% | -0.03% | 48% | 3.2% | -2.00% | [-0.08%, +0.05%] | -0.17 |
| 50–60 | 12227 | +0.05% | +0.00% | 49% | 3.2% | -2.00% | [-0.01%, +0.11%] | +0.40 |
| 60–70 | 12091 | +0.11% | +0.00% | 50% | 3.2% | -1.94% | [+0.05%, +0.18%] | +0.88 |
| 70–80 | 12041 | +0.15% | +0.04% | 51% | 3.4% | -1.96% | [+0.06%, +0.21%] | +1.24 |
| 80–90 | 12117 | +0.13% | +0.03% | 50% | 3.3% | -1.97% | [+0.05%, +0.19%] | +1.09 |
| 90–95 | 6084 | +0.16% | +0.06% | 52% | 3.3% | -2.02% | [+0.07%, +0.26%] | +1.33 |
| 95–100 | 6543 | +0.13% | -0.01% | 49% | 3.3% | -1.90% | [+0.06%, +0.25%] | +1.06 |

**learned global (D)** — AlphaReliability +0.99 · not monotonic

| Percentile | n | Mean rel. return | Median | Hit | Vol | Downside | 95% CI | ConvictionMultiplier |
|---|---|---|---|---|---|---|---|---|
| 0–5 | 6544 | -0.16% | -0.17% | 45% | 3.8% | -2.25% | [-0.28%, -0.03%] | -0.81 |
| 5–10 | 6081 | -0.20% | -0.18% | 45% | 3.3% | -2.10% | [-0.30%, -0.09%] | -1.06 |
| 10–20 | 11947 | -0.13% | -0.12% | 46% | 3.3% | -2.04% | [-0.21%, -0.05%] | -0.67 |
| 20–30 | 12114 | -0.04% | -0.05% | 48% | 3.3% | -1.95% | [-0.11%, +0.03%] | -0.23 |
| 30–40 | 12017 | +0.01% | -0.02% | 49% | 3.1% | -1.90% | [-0.05%, +0.08%] | +0.05 |
| 40–50 | 11946 | +0.07% | +0.03% | 51% | 3.0% | -1.85% | [+0.01%, +0.13%] | +0.35 |
| 50–60 | 12227 | +0.12% | +0.02% | 50% | 3.1% | -1.82% | [+0.06%, +0.18%] | +0.64 |
| 60–70 | 12090 | +0.07% | +0.01% | 50% | 3.1% | -1.87% | [+0.01%, +0.13%] | +0.38 |
| 70–80 | 12041 | +0.13% | +0.04% | 51% | 3.1% | -1.83% | [+0.06%, +0.20%] | +0.68 |
| 80–90 | 12119 | +0.21% | +0.08% | 52% | 3.2% | -1.89% | [+0.14%, +0.28%] | +1.12 |
| 90–95 | 6081 | +0.27% | +0.12% | 53% | 3.4% | -1.93% | [+0.17%, +0.37%] | +1.44 |
| 95–100 | 6544 | +0.33% | +0.09% | 52% | 4.0% | -2.20% | [+0.19%, +0.44%] | +1.74 |

**learned hierarchy (E)** — AlphaReliability +0.98 · not monotonic

| Percentile | n | Mean rel. return | Median | Hit | Vol | Downside | 95% CI | ConvictionMultiplier |
|---|---|---|---|---|---|---|---|---|
| 0–5 | 6544 | -0.30% | -0.23% | 43% | 3.5% | -2.16% | [-0.40%, -0.15%] | -1.36 |
| 5–10 | 6081 | -0.22% | -0.15% | 45% | 3.2% | -2.08% | [-0.30%, -0.10%] | -1.00 |
| 10–20 | 11947 | -0.12% | -0.14% | 46% | 3.4% | -1.99% | [-0.20%, -0.04%] | -0.55 |
| 20–30 | 12114 | -0.05% | -0.06% | 48% | 3.2% | -1.93% | [-0.13%, +0.01%] | -0.24 |
| 30–40 | 12017 | +0.01% | -0.01% | 49% | 3.3% | -1.93% | [-0.07%, +0.07%] | +0.03 |
| 40–50 | 11946 | +0.05% | +0.00% | 50% | 3.1% | -1.88% | [-0.00%, +0.11%] | +0.25 |
| 50–60 | 12227 | +0.13% | +0.05% | 51% | 3.1% | -1.87% | [+0.08%, +0.19%] | +0.60 |
| 60–70 | 12090 | +0.11% | +0.01% | 50% | 3.1% | -1.88% | [+0.04%, +0.16%] | +0.49 |
| 70–80 | 12041 | +0.11% | +0.05% | 51% | 3.2% | -1.93% | [+0.04%, +0.18%] | +0.50 |
| 80–90 | 12119 | +0.20% | +0.07% | 52% | 3.2% | -1.88% | [+0.12%, +0.27%] | +0.90 |
| 90–95 | 6081 | +0.34% | +0.16% | 54% | 3.4% | -1.93% | [+0.23%, +0.44%] | +1.55 |
| 95–100 | 6544 | +0.44% | +0.17% | 54% | 3.7% | -2.03% | [+0.28%, +0.53%] | +1.97 |

**blend** — AlphaReliability +0.98 · not monotonic

| Percentile | n | Mean rel. return | Median | Hit | Vol | Downside | 95% CI | ConvictionMultiplier |
|---|---|---|---|---|---|---|---|---|
| 0–5 | 6544 | -0.30% | -0.23% | 43% | 3.5% | -2.16% | [-0.40%, -0.15%] | -1.36 |
| 5–10 | 6081 | -0.22% | -0.15% | 45% | 3.2% | -2.08% | [-0.30%, -0.10%] | -1.00 |
| 10–20 | 11947 | -0.12% | -0.14% | 46% | 3.4% | -1.99% | [-0.20%, -0.04%] | -0.55 |
| 20–30 | 12114 | -0.05% | -0.06% | 48% | 3.2% | -1.93% | [-0.13%, +0.01%] | -0.24 |
| 30–40 | 12017 | +0.01% | -0.01% | 49% | 3.3% | -1.93% | [-0.07%, +0.07%] | +0.03 |
| 40–50 | 11946 | +0.05% | +0.00% | 50% | 3.1% | -1.88% | [-0.00%, +0.11%] | +0.25 |
| 50–60 | 12227 | +0.13% | +0.05% | 51% | 3.1% | -1.87% | [+0.08%, +0.19%] | +0.60 |
| 60–70 | 12090 | +0.11% | +0.01% | 50% | 3.1% | -1.88% | [+0.04%, +0.16%] | +0.49 |
| 70–80 | 12041 | +0.11% | +0.05% | 51% | 3.2% | -1.93% | [+0.04%, +0.18%] | +0.50 |
| 80–90 | 12119 | +0.20% | +0.07% | 52% | 3.2% | -1.88% | [+0.12%, +0.27%] | +0.90 |
| 90–95 | 6081 | +0.35% | +0.16% | 54% | 3.4% | -1.93% | [+0.23%, +0.44%] | +1.55 |
| 95–100 | 6544 | +0.44% | +0.17% | 54% | 3.7% | -2.03% | [+0.28%, +0.53%] | +1.97 |

**nested model selection** — AlphaReliability +0.99 · not monotonic

| Percentile | n | Mean rel. return | Median | Hit | Vol | Downside | 95% CI | ConvictionMultiplier |
|---|---|---|---|---|---|---|---|---|
| 0–5 | 6544 | -0.33% | -0.25% | 42% | 3.5% | -2.21% | [-0.42%, -0.17%] | -1.46 |
| 5–10 | 6081 | -0.19% | -0.15% | 45% | 3.2% | -2.07% | [-0.28%, -0.08%] | -0.87 |
| 10–20 | 11947 | -0.12% | -0.11% | 46% | 3.4% | -2.03% | [-0.20%, -0.04%] | -0.54 |
| 20–30 | 12114 | -0.07% | -0.07% | 47% | 3.1% | -1.90% | [-0.13%, +0.00%] | -0.29 |
| 30–40 | 12017 | +0.00% | -0.03% | 48% | 3.1% | -1.90% | [-0.07%, +0.06%] | +0.00 |
| 40–50 | 11946 | +0.08% | +0.01% | 50% | 3.2% | -1.88% | [+0.02%, +0.14%] | +0.36 |
| 50–60 | 12227 | +0.13% | +0.05% | 51% | 3.1% | -1.86% | [+0.07%, +0.19%] | +0.58 |
| 60–70 | 12090 | +0.09% | +0.01% | 50% | 3.2% | -1.88% | [+0.02%, +0.15%] | +0.42 |
| 70–80 | 12041 | +0.14% | +0.05% | 51% | 3.2% | -1.90% | [+0.06%, +0.20%] | +0.61 |
| 80–90 | 12119 | +0.17% | +0.05% | 51% | 3.2% | -1.88% | [+0.10%, +0.25%] | +0.78 |
| 90–95 | 6081 | +0.40% | +0.19% | 55% | 3.4% | -1.91% | [+0.28%, +0.48%] | +1.80 |
| 95–100 | 6544 | +0.40% | +0.17% | 54% | 3.8% | -2.10% | [+0.25%, +0.50%] | +1.81 |

## 8. Robustness — neutralisation, groups, leave one group out

Weekly rank IC against the relative return before and after removing, cross-sectionally, market beta, class, value (earnings yield) and volatility. Size is not neutralised: the store has no point-in-time market cap.

| Model | Raw rank IC (t) | Neutralised rank IC (t) | Kept |
|---|---|---|---|
| production | +0.0371 (+6.0) | +0.0452 (+10.4) | 122% |
| learned global (D) | +0.0524 (+7.8) | +0.0645 (+13.4) | 123% |
| learned hierarchy (E) | +0.0669 (+10.1) | +0.0768 (+16.2) | 115% |
| blend | +0.0669 (+10.1) | +0.0768 (+16.2) | 115% |
| nested model selection | +0.0628 (+9.5) | +0.0744 (+15.5) | 118% |

Within-class rank IC (each class ranked on its own, ≥ 8 names a week):

| Class | production | learned global (D) | learned hierarchy (E) |
|---|---|---|---|
| Commodity | -0.0041 (-0.4, 913 wk) | +0.0565 (+5.2, 913 wk) | +0.0689 (+6.3, 913 wk) |
| Credit | +0.0469 (+3.4, 616 wk) | +0.1167 (+7.4, 616 wk) | +0.1456 (+9.4, 616 wk) |
| Crypto | — (—, 0 wk) | — (—, 0 wk) | — (—, 0 wk) |
| Equity | +0.0441 (+7.0, 920 wk) | +0.0590 (+9.1, 920 wk) | +0.0658 (+9.9, 920 wk) |
| FX | +0.0177 (+1.5, 849 wk) | +0.0202 (+1.8, 849 wk) | +0.0389 (+3.4, 849 wk) |
| Rates | +0.1773 (+14.7, 882 wk) | +0.1390 (+9.4, 882 wk) | +0.1869 (+13.4, 882 wk) |
| Volatility | — (—, 0 wk) | — (—, 0 wk) | — (—, 0 wk) |

Leave one group out — the learned global model refitted walk-forward without the group, compared with production on what remains. A model that only works because of one group would collapse here.

| Left out | Records | Assets | Learned rank IC | Production | Δ (t) |
|---|---|---|---|---|---|
| Equity class | 51798 | 62 | +0.0374 | +0.0395 | -0.0021 (-0.2) |
| Rates class | 136132 | 140 | +0.0512 | +0.0343 | +0.0169 (+2.6) |
| Credit class | 141771 | 144 | +0.0500 | +0.0366 | +0.0135 (+2.0) |
| Commodity class | 133143 | 137 | +0.0558 | +0.0422 | +0.0136 (+2.0) |
| FX class | 136552 | 140 | +0.0657 | +0.0403 | +0.0253 (+3.9) |
| crypto | 149214 | 153 | +0.0524 | +0.0379 | +0.0145 (+2.2) |
| bond products (rates + credit) | 128231 | 129 | +0.0479 | +0.0332 | +0.0147 (+2.2) |
| ETFs | 90500 | 83 | +0.0317 | +0.0309 | +0.0008 (+0.1) |
| inverse / leveraged products | 144963 | 148 | +0.0564 | +0.0384 | +0.0180 (+2.7) |
| mega-cap stocks | 137199 | 142 | +0.0482 | +0.0351 | +0.0131 (+1.9) |
| Information Technology | 132435 | 140 | +0.0517 | +0.0357 | +0.0160 (+2.3) |
| Financials | 141166 | 147 | +0.0517 | +0.0377 | +0.0140 (+2.0) |
| Energy | 141743 | 147 | +0.0531 | +0.0397 | +0.0134 (+2.0) |
| Health Care | 141682 | 148 | +0.0561 | +0.0388 | +0.0173 (+2.6) |

The edge weakens below t = 2 without: Equity class, ETFs, mega-cap stocks.

## 9. Clusters and interactions

Signal clusters today (|ρ| ≥ 0.6 on training records): 41 — {ret_3m, excess_3m}; {ret_6m, dist_ma200, ma_cross, trend_quality, rel_strength_6m, pctile_252, drawdown_252, ret_12m, mom_12_1, sharpe_252, sortino_252, ram, alpha_252}; {ret_1m, macd, z_50, rsi_14, z_20, bb_pctb, mr_opportunity}; {value_5y, rel_value}; {book_to_price, sales_yield}; {net_margin, roe, fund_quality}; {vol_20, ewma_vol, vol_60, downside_vol_60, garch_vol}; {vol_ratio, vol_pctile}; {y10, slope_10y3m}; {d_y10_3m, d_slope_3m}; {d_credit_3m, credit_signal}; {vix, d_vix_1m}; {beta_252, corr_252}.

Predeclared economic interactions, each added alone to the learned global model (weight nested; GBM-suggested ones are descriptive only because the GBM saw the whole sample):

| Interaction | Weight today | Rank IC | Δ vs learned global (t) |
|---|---|---|---|
| momentum × volatility | -0.01031 | +0.0514 | -0.0008 (-1.6) |
| reversal × volatility | -0.05549 | +0.0522 | +0.0000 (+0.0) |
| valuation × rates | -0.02261 | +0.0516 | -0.0006 (-1.1) |
| quality × credit | -0.00301 | +0.0519 | -0.0003 (-0.3) |
| breadth × beta | +0.00423 | +0.0519 | -0.0003 (-0.9) |
| trend × liquidity | +0.02831 | +0.0522 | +0.0000 (+0.1) |
| momentum × VIX | -0.01116 | +0.0525 | +0.0003 (+0.6) |

## 10a. 1D — a cost-aware model

1D records are weekly snapshots: each one-day trade is a full round trip; turnover per trade 100%.

Nested cost-aware strategy (model × regime filter × tail fraction chosen per era by inner net P&L after round-trip costs; stand aside when no option had a positive inner net): net per trade +0.144%, t +5.3, weeks 925, eras +0.000%, +0.014%, +0.134%, +0.418%, +0.162%, active 25% → **PROFITABLE AFTER COSTS**.

Choices per era: 2009: stand aside, 2013: learned global (D) | bear only | 5%, 2017: learned global (D) | bear only | 5%, 2021: learned hierarchy (E) | high volatility only | 5%, 2025: learned hierarchy (E) | high volatility only | 5%, 2018: learned hierarchy (E) | high volatility only | 5%, 2100: learned hierarchy (E) | none | 5%.

| Model | 1D rank IC (t) | Δ vs learned global (t) |
|---|---|---|
| production | +0.0353 (+7.7) | — |
| learned global (D) | +0.0553 (+8.9) | +0.0000 (—) |
| learned hierarchy (E) | +0.0842 (+14.3) | +0.0290 (+8.2) |
| global λ=1000 | +0.0554 (+8.8) | +0.0001 (+0.2) |
| global λ=10000 | +0.0578 (+9.1) | +0.0026 (+1.6) |
| global λ=100000 | +0.0590 (+9.1) | +0.0038 (+1.2) |

| Model | Tails | Gross/trade | Net/trade | Net (flat 10 bp) | t | Hit | Max drawdown |
|---|---|---|---|---|---|---|---|
| production | 50% | +0.061% | -0.109% | -0.339% | -7.6 | 34% | -104.5% |
| production | 30% | +0.095% | -0.080% | -0.305% | -4.3 | 38% | -81.7% |
| production | 20% | +0.099% | -0.083% | -0.301% | -3.9 | 39% | -83.8% |
| production | 10% | +0.100% | -0.094% | -0.300% | -3.7 | 39% | -96.3% |
| production | 5% | +0.082% | -0.117% | -0.318% | -3.6 | 41% | -115.8% |
| learned global (D) | 50% | +0.102% | -0.067% | -0.298% | -3.9 | 38% | -65.7% |
| learned global (D) | 30% | +0.135% | -0.039% | -0.265% | -1.6 | 44% | -52.9% |
| learned global (D) | 20% | +0.155% | -0.022% | -0.245% | -0.8 | 46% | -50.0% |
| learned global (D) | 10% | +0.179% | -0.004% | -0.221% | -0.1 | 46% | -53.8% |
| learned global (D) | 5% | +0.214% | +0.028% | -0.186% | +0.7 | 49% | -49.0% |
| learned hierarchy (E) | 50% | +0.122% | -0.047% | -0.278% | -3.2 | 41% | -50.4% |
| learned hierarchy (E) | 30% | +0.174% | +0.002% | -0.226% | +0.1 | 49% | -43.4% |
| learned hierarchy (E) | 20% | +0.234% | +0.060% | -0.166% | +2.4 | 51% | -25.8% |
| learned hierarchy (E) | 10% | +0.312% | +0.133% | -0.088% | +4.3 | 52% | -25.5% |
| learned hierarchy (E) | 5% | +0.381% | +0.200% | -0.019% | +5.3 | 55% | -33.2% |

## 10. Long horizons (1M / 3M / 6M / 12M) — sample limits and simpler models

Overlapping horizons have far fewer independent periods than weeks. Every variant is nested (stronger shrinkage, family-level weights, top 10 / 20 signals, stable signals only, fundamental / relative value / macro subsets, class-level hierarchy) and FDR is applied across all 1M–12M variants.

### 1M

Effective sample: 921 walk-forward weeks ≈ **219 independent periods** for 74 signals → 2.96 per signal; mean cross-section 132 assets. Era-to-era coefficient t (median) 0.69; signals with |era t| ≥ 2: 7. Stability classes: NO EVIDENCE 48, UNSTABLE 20, STABLE 6.

| Model | Rank IC | Δ vs production (t) | Δ vs learned global (t) | Eras + | FDR | Status |
|---|---|---|---|---|---|---|
| learned global (D) | +0.0106 | -0.0081 (-0.5) | — | 1/4 | — | baseline |
| stronger shrinkage (λ nested) | +0.0124 | -0.0062 (-0.4) | +0.0018 (+0.9) | 2/4 | no | NOT VALIDATED |
| family-level weights (15) | +0.0190 | +0.0003 (+0.0) | +0.0081 (+0.7) | 2/4 | no | NOT VALIDATED |
| top 10 signals (training correlation) | +0.0117 | -0.0070 (-0.4) | +0.0008 (+0.1) | 1/4 | no | NOT VALIDATED |
| top 20 signals (training correlation) | +0.0119 | -0.0068 (-0.4) | +0.0013 (+0.1) | 1/4 | no | NOT VALIDATED |
| stable signals only | +0.0317 | +0.0086 (+0.4) | +0.0169 (+1.0) | 1/2 | no | NOT VALIDATED |
| fundamental only | -0.0051 | -0.0238 (-1.4) | -0.0160 (-1.0) | 0/4 | no | NOT VALIDATED |
| relative value + fundamental | +0.0019 | -0.0168 (-1.0) | -0.0088 (-0.6) | 1/4 | no | NOT VALIDATED |
| macro + fundamental | +0.0002 | -0.0185 (-1.2) | -0.0106 (-0.8) | 0/4 | no | NOT VALIDATED |
| class-level hierarchy (K nested) | +0.0166 | -0.0021 (-0.1) | +0.0062 (+0.5) | 1/4 | no | NOT VALIDATED |
| nested selection (reduced dimension) | +0.0027 | -0.0160 (-0.9) | -0.0080 (-0.7) | 1/4 | no | NOT VALIDATED |

- Best complexity: **stable signals only** (rank IC +0.0317).
- Stable weights (learned run): ret_1d -0.0000, z_20 -0.0165, bb_pctb -0.0165, mr_opportunity -0.0000, book_to_price -0.0075, dollar_mom_3m +0.0000.
- Reduced dimension helps: no variant beats the full learned global model at t ≥ 2.
- Validated Alpha edge: **none** — null result, reported as such.
- Within-class rank IC (production / learned global): Commodity -0.000 / +0.026; Credit +0.025 / +0.050; Crypto — / —; Equity +0.014 / +0.029; FX +0.029 / +0.007; Rates +0.035 / +0.070; Volatility — / —.

### 3M

Effective sample: 912 walk-forward weeks ≈ **72 independent periods** for 74 signals → 0.98 per signal; mean cross-section 130 assets. Era-to-era coefficient t (median) 0.77; signals with |era t| ≥ 2: 8. Stability classes: NO EVIDENCE 45, UNSTABLE 22, STABLE 7.

| Model | Rank IC | Δ vs production (t) | Δ vs learned global (t) | Eras + | FDR | Status |
|---|---|---|---|---|---|---|
| learned global (D) | -0.0185 | -0.0101 (-0.3) | — | 2/4 | — | baseline |
| stronger shrinkage (λ nested) | -0.0168 | -0.0084 (-0.2) | +0.0017 (+0.5) | 2/4 | no | NOT VALIDATED |
| family-level weights (15) | +0.0035 | +0.0119 (+0.3) | +0.0217 (+1.1) | 2/4 | no | NOT VALIDATED |
| top 10 signals (training correlation) | -0.0382 | -0.0298 (-0.8) | -0.0196 (-1.0) | 2/4 | no | NOT VALIDATED |
| top 20 signals (training correlation) | -0.0303 | -0.0219 (-0.6) | -0.0117 (-0.8) | 2/4 | no | NOT VALIDATED |
| stable signals only | -0.0045 | -0.0175 (-0.4) | +0.0107 (+0.4) | 1/2 | no | NOT VALIDATED |
| fundamental only | -0.0256 | -0.0172 (-0.5) | -0.0071 (-0.2) | 1/4 | no | NOT VALIDATED |
| relative value + fundamental | +0.0191 | +0.0274 (+0.7) | +0.0373 (+1.8) | 2/4 | no | NOT VALIDATED |
| macro + fundamental | -0.0190 | -0.0106 (-0.3) | -0.0005 (-0.0) | 1/4 | no | NOT VALIDATED |
| class-level hierarchy (K nested) | -0.0297 | -0.0214 (-0.6) | -0.0117 (-0.6) | 1/4 | no | NOT VALIDATED |
| nested selection (reduced dimension) | +0.0016 | +0.0100 (+0.3) | +0.0201 (+1.3) | 2/4 | no | NOT VALIDATED |

- Best complexity: **relative value + fundamental** (rank IC +0.0191).
- Stable weights (learned run): trend_quality -0.0000, ret_1d -0.0000, ret_1w -0.0000, z_20 -0.0000, bb_pctb -0.0000, half_life +0.0000, real_y10 +0.0000.
- Reduced dimension helps: no variant beats the full learned global model at t ≥ 2.
- Validated Alpha edge: **none** — null result, reported as such.
- Within-class rank IC (production / learned global): Commodity -0.058 / +0.019; Credit +0.001 / -0.031; Crypto — / —; Equity -0.014 / +0.010; FX +0.028 / -0.019; Rates +0.133 / +0.038; Volatility — / —.

### 6M

Effective sample: 899 walk-forward weeks ≈ **36 independent periods** for 74 signals → 0.48 per signal; mean cross-section 123 assets. Era-to-era coefficient t (median) 0.78; signals with |era t| ≥ 2: 7. Stability classes: NO EVIDENCE 47, UNSTABLE 20, STABLE 7.

| Model | Rank IC | Δ vs production (t) | Δ vs learned global (t) | Eras + | FDR | Status |
|---|---|---|---|---|---|---|
| learned global (D) | +0.0041 | +0.0224 (+0.5) | — | 1/4 | — | baseline |
| stronger shrinkage (λ nested) | +0.0041 | +0.0224 (+0.5) | +0.0000 (—) | 1/4 | no | NOT VALIDATED |
| family-level weights (15) | +0.0086 | +0.0269 (+0.6) | +0.0045 (+0.2) | 1/4 | no | NOT VALIDATED |
| top 10 signals (training correlation) | -0.0124 | +0.0059 (+0.1) | -0.0165 (-0.7) | 2/4 | no | NOT VALIDATED |
| top 20 signals (training correlation) | -0.0061 | +0.0122 (+0.3) | -0.0102 (-0.5) | 2/4 | no | NOT VALIDATED |
| stable signals only | -0.0407 | -0.0651 (-1.1) | -0.0386 (-1.2) | 0/2 | no | NOT VALIDATED |
| fundamental only | -0.0046 | +0.0137 (+0.4) | -0.0088 (-0.2) | 1/4 | no | NOT VALIDATED |
| relative value + fundamental | +0.0103 | +0.0286 (+0.6) | +0.0062 (+0.2) | 2/4 | no | NOT VALIDATED |
| macro + fundamental | +0.0142 | +0.0325 (+0.7) | +0.0101 (+0.3) | 2/4 | no | NOT VALIDATED |
| class-level hierarchy (K nested) | -0.0119 | +0.0064 (+0.1) | -0.0160 (-0.6) | 1/4 | no | NOT VALIDATED |
| nested selection (reduced dimension) | -0.0050 | +0.0133 (+0.3) | -0.0091 (-0.4) | 1/4 | no | NOT VALIDATED |

- Best complexity: **macro + fundamental** (rank IC +0.0142).
- Stable weights (learned run): ma_cross +0.0321, ret_1d -0.0000, ar1_63 +0.0000, half_life +0.0000, drawdown_252 -0.0000, dollar_mom_3m +0.0000, excess_3m -0.0000.
- Reduced dimension helps: no variant beats the full learned global model at t ≥ 2.
- Validated Alpha edge: **none** — null result, reported as such.
- Within-class rank IC (production / learned global): Commodity -0.049 / +0.018; Credit +0.050 / -0.060; Crypto — / —; Equity -0.015 / +0.032; FX +0.003 / +0.045; Rates +0.200 / +0.080; Volatility — / —.

### 12M

Effective sample: 868 walk-forward weeks ≈ **17 independent periods** for 74 signals → 0.23 per signal; mean cross-section 97 assets. Era-to-era coefficient t (median) 0.82; signals with |era t| ≥ 2: 7. Stability classes: NO EVIDENCE 45, UNSTABLE 24, STABLE 5.

| Model | Rank IC | Δ vs production (t) | Δ vs learned global (t) | Eras + | FDR | Status |
|---|---|---|---|---|---|---|
| learned global (D) | +0.0644 | +0.0611 (+1.1) | — | 3/4 | — | baseline |
| stronger shrinkage (λ nested) | +0.0644 | +0.0611 (+1.1) | +0.0000 (—) | 3/4 | no | NOT VALIDATED |
| family-level weights (15) | -0.0088 | -0.0121 (-0.2) | -0.0732 (-2.0) | 2/4 | no | NOT VALIDATED |
| top 10 signals (training correlation) | +0.0692 | +0.0659 (+1.1) | +0.0048 (+0.2) | 3/4 | no | NOT VALIDATED |
| top 20 signals (training correlation) | +0.0826 | +0.0793 (+1.3) | +0.0182 (+0.8) | 3/4 | no | NOT VALIDATED |
| stable signals only | +0.0112 | -0.0193 (-0.3) | -0.0497 (-0.6) | 0/2 | no | NOT VALIDATED |
| fundamental only | +0.0150 | +0.0117 (+0.2) | -0.0493 (-1.0) | 3/4 | no | NOT VALIDATED |
| relative value + fundamental | -0.0128 | -0.0161 (-0.3) | -0.0772 (-1.8) | 2/4 | no | NOT VALIDATED |
| macro + fundamental | +0.0086 | +0.0053 (+0.1) | -0.0557 (-1.1) | 2/4 | no | NOT VALIDATED |
| class-level hierarchy (K nested) | +0.0367 | +0.0334 (+0.6) | -0.0277 (-1.2) | 2/4 | no | NOT VALIDATED |
| nested selection (reduced dimension) | +0.0849 | +0.0816 (+1.4) | +0.0206 (+1.0) | 3/4 | no | NOT VALIDATED |

- Best complexity: **nested selection (reduced dimension)** (rank IC +0.0849).
- Stable weights (learned run): ret_1d -0.0000, vol_pctile +0.0000, acf1_252 -0.0000, real_y10 +0.0000, rate_beta_252 +0.0000.
- Reduced dimension helps: no variant beats the full learned global model at t ≥ 2.
- Validated Alpha edge: **none** — null result, reported as such.
- Within-class rank IC (production / learned global): Commodity -0.051 / +0.145; Credit +0.104 / +0.286; Equity -0.010 / +0.061; FX -0.036 / +0.062; Rates +0.154 / +0.029.

## 11. Directional — does the validated Alpha percentile add to the prior?

Logistic P(R_1W > 0) on [1, prior z, Alpha percentile] fitted on records matured before each era (the Alpha percentile (learned hierarchy (E)) is itself out of sample), against prior-only and prior + production, with the Directional program's fixed gates. Directional stays conservative: a pass would only earn a research version, never a direct change.

| | Brier | Log loss | AUC | ECE |
|---|---|---|---|---|
| prior + Alpha percentile | 0.24644 | 0.68575 | 0.5410 | 0.0082 |
| prior only | 0.24704 | 0.68699 | 0.5268 | 0.0127 |

Paired vs prior: {"n": 100707, "brier_gain": 0.000597, "brier_t": 5.785, "delta_acc": 0.0003462, "delta_acc_t": 0.413, "logloss_gain": 0.001213, "logloss_t": 5.773}; vs prior + production: {"n": 100707, "brier_gain": 0.0005704, "brier_t": 4.641, "delta_acc": 0.001784, "delta_acc_t": 2.149, "logloss_gain": 0.001158, "logloss_t": 4.614}; calibration slope +1.08. Gates: {"t": 5.785175640466178, "brier_gain": 0.0005969974824790253, "logloss_gain": 0.0012133980658222015, "balanced_gain": 0.0023803451159698508, "vs_current_t": 4.641440257943136, "G1": true, "split_t": n → **PASS**.

## 12. Today's scores (all matured data)

Score = today's research record × each model's final weights (signals demeaned by today's cross-section); the blend is a rank mix. Sorted by the learned global model.

| Asset | Production raw | learned global (D) | learned hierarchy (E) | blend | nested model selection |
|---|---|---|---|---|---|
| JNJ | +1.88 | +0.099 | +0.069 | +0.357 | +0.357 |
| EEM | +8.78 | +0.088 | +0.169 | +0.494 | +0.494 |
| CPER | +6.92 | +0.084 | +0.057 | +0.299 | +0.299 |
| SILVER | -0.71 | +0.082 | +0.069 | +0.351 | +0.351 |
| MRK | +4.29 | +0.077 | +0.118 | +0.474 | +0.474 |
| UNH | +40.30 | +0.071 | +0.078 | +0.377 | +0.377 |
| BND | +4.78 | +0.068 | +0.099 | +0.442 | +0.442 |
| PLATINUM | -0.10 | +0.065 | +0.050 | +0.279 | +0.279 |
| GOLD | -0.15 | +0.065 | +0.048 | +0.273 | +0.273 |
| FXE | +0.05 | +0.063 | +0.019 | +0.091 | +0.091 |
| XLV | +10.76 | +0.062 | +0.068 | +0.344 | +0.344 |
| DBA | +0.55 | +0.062 | +0.050 | +0.286 | +0.286 |
| TIP | +9.31 | +0.057 | +0.110 | +0.461 | +0.461 |
| IEI | +2.63 | +0.057 | +0.103 | +0.455 | +0.455 |
| EWZ | -1.83 | +0.056 | +0.068 | +0.338 | +0.338 |
| N225 | +1.32 | +0.055 | +0.084 | +0.409 | +0.409 |
| COPPER | +2.79 | +0.054 | +0.013 | +0.052 | +0.052 |
| AVGO | +24.71 | +0.052 | +0.156 | +0.487 | +0.487 |
| LLY | +1.76 | +0.048 | +0.044 | +0.240 | +0.240 |
| SLV | -0.68 | +0.048 | +0.060 | +0.318 | +0.318 |
| DBC | +5.24 | +0.047 | +0.183 | +0.500 | +0.500 |
| MTUM | +16.59 | +0.042 | +0.132 | +0.481 | +0.481 |
| UST2Y | -11.07 | +0.041 | +0.004 | -0.045 | -0.045 |
| USDJPY | -0.72 | +0.040 | +0.034 | +0.195 | +0.195 |
| MCD | +6.13 | +0.038 | +0.043 | +0.221 | +0.221 |
| USDINR | +2.43 | +0.036 | +0.020 | +0.104 | +0.104 |
| PEP | +17.19 | +0.035 | +0.070 | +0.364 | +0.364 |
| GLD | -0.30 | +0.033 | +0.102 | +0.448 | +0.448 |
| USMV | +20.56 | +0.030 | +0.079 | +0.383 | +0.383 |
| SHY | +1.30 | +0.030 | +0.009 | +0.013 | +0.013 |
| XLP | +27.62 | +0.029 | +0.060 | +0.305 | +0.305 |
| ADBE | +8.11 | +0.029 | -0.014 | -0.143 | -0.143 |
| SSO | +2.88 | +0.029 | +0.086 | +0.416 | +0.416 |
| SCHP | +12.37 | +0.028 | +0.066 | +0.331 | +0.331 |
| CWB | +0.44 | +0.026 | +0.048 | +0.266 | +0.266 |
| FTSE | +31.52 | +0.026 | +0.021 | +0.110 | +0.110 |
| MUB | -72.74 | +0.024 | -0.014 | -0.149 | -0.149 |
| FXY | +7.24 | +0.023 | +0.060 | +0.312 | +0.312 |
| KO | +1.95 | +0.023 | -0.040 | -0.318 | -0.318 |
| XLB | +12.92 | +0.022 | +0.028 | +0.143 | +0.143 |
| UST5Y | -6.84 | +0.021 | -0.012 | -0.117 | -0.117 |
| GS | +0.02 | +0.021 | +0.018 | +0.078 | +0.078 |
| VNQ | +10.36 | +0.019 | +0.095 | +0.435 | +0.435 |
| XOM | -5.00 | +0.018 | +0.090 | +0.422 | +0.422 |
| FXB | -2.02 | +0.016 | +0.018 | +0.084 | +0.084 |
| XLC | -0.91 | +0.016 | +0.009 | +0.019 | +0.019 |
| EWU | +14.92 | +0.015 | +0.006 | -0.006 | -0.006 |
| QLD | +0.97 | +0.015 | +0.092 | +0.429 | +0.429 |
| ETH | +0.71 | +0.014 | +0.022 | +0.123 | +0.123 |
| IWD | +5.78 | +0.014 | +0.033 | +0.182 | +0.182 |
| VCIT | -11.90 | +0.013 | +0.007 | +0.000 | +0.000 |
| BIL | +62.18 | +0.012 | -0.098 | -0.474 | -0.474 |
| QUAL | +0.59 | +0.011 | +0.081 | +0.396 | +0.396 |
| IWM | +5.29 | +0.011 | +0.033 | +0.188 | +0.188 |
| HYG | +5.28 | +0.010 | +0.005 | -0.013 | -0.013 |
| AGG | +5.64 | +0.009 | -0.021 | -0.214 | -0.214 |
| USDCAD | -0.01 | +0.009 | +0.019 | +0.097 | +0.097 |
| AAPL | +0.12 | +0.009 | +0.010 | +0.026 | +0.026 |
| QQQ | +8.90 | +0.009 | +0.044 | +0.234 | +0.234 |
| ORCL | +2.65 | +0.009 | +0.052 | +0.292 | +0.292 |
| UST10Y | -2.16 | +0.007 | -0.002 | -0.078 | -0.078 |
| USDCHF | -0.13 | +0.007 | +0.034 | +0.201 | +0.201 |
| PFE | +3.74 | +0.007 | +0.017 | +0.071 | +0.071 |
| IEF | +1.02 | +0.007 | -0.055 | -0.390 | -0.390 |
| UNG | -0.12 | +0.006 | +0.081 | +0.390 | +0.390 |
| WMT | +16.36 | +0.006 | +0.003 | -0.052 | -0.052 |
| XLU | +1.01 | +0.006 | +0.047 | +0.260 | +0.260 |
| XLE | +0.99 | +0.005 | +0.046 | +0.253 | +0.253 |
| XLF | +4.18 | +0.002 | -0.005 | -0.091 | -0.091 |
| COST | +15.86 | +0.002 | +0.016 | +0.065 | +0.065 |
| VXX | -4.81 | +0.002 | -0.026 | -0.240 | -0.240 |
| INDA | -0.11 | +0.001 | -0.020 | -0.195 | -0.195 |
| EMB | -4.41 | -0.000 | +0.026 | +0.136 | +0.136 |
| SX5E | +5.68 | -0.001 | -0.030 | -0.266 | -0.266 |
| JNK | -1.30 | -0.002 | -0.002 | -0.065 | -0.065 |
| NFLX | -1.78 | -0.004 | +0.021 | +0.117 | +0.117 |
| DXY | -0.75 | -0.004 | +0.013 | +0.039 | +0.039 |
| NVDA | -0.75 | -0.004 | +0.004 | -0.032 | -0.032 |
| DIA | +5.73 | -0.005 | -0.011 | -0.110 | -0.110 |
| NKE | +2.01 | -0.005 | -0.017 | -0.175 | -0.175 |
| MBB | -3.52 | -0.005 | -0.042 | -0.331 | -0.331 |
| BAC | +0.50 | -0.006 | +0.011 | +0.032 | +0.032 |
| JPM | +0.72 | -0.006 | -0.024 | -0.234 | -0.234 |
| NATGAS | -0.17 | -0.006 | +0.083 | +0.403 | +0.403 |
| FLOT | +13.69 | -0.006 | +0.005 | -0.026 | -0.026 |
| WHEAT | +23.72 | -0.007 | +0.005 | -0.019 | -0.019 |
| RUT | +2.18 | -0.007 | -0.013 | -0.123 | -0.123 |
| SPX | +7.65 | -0.008 | -0.028 | -0.247 | -0.247 |
| USDCNY | -16.56 | -0.008 | -0.077 | -0.442 | -0.442 |
| EFA | +6.24 | -0.008 | -0.020 | -0.208 | -0.208 |
| TQQQ | +3.85 | -0.008 | +0.044 | +0.227 | +0.227 |
| COFFEE | +4.41 | -0.009 | -0.050 | -0.383 | -0.383 |
| BRENT | +5.62 | -0.010 | +0.065 | +0.325 | +0.325 |
| XLRE | +9.89 | -0.011 | +0.000 | -0.058 | -0.058 |
| SPY | +9.03 | -0.012 | -0.008 | -0.104 | -0.104 |
| KRE | +0.52 | -0.013 | -0.035 | -0.286 | -0.286 |
| HSI | -1.27 | -0.013 | +0.035 | +0.208 | +0.208 |
| CORP_BAA | -16.87 | -0.013 | +0.004 | -0.039 | -0.039 |
| INTC | +0.97 | -0.013 | +0.032 | +0.162 | +0.162 |
| AUDUSD | +0.18 | -0.015 | -0.057 | -0.396 | -0.396 |
| WTI | +2.45 | -0.015 | +0.074 | +0.370 | +0.370 |
| VTI | +6.01 | -0.015 | -0.016 | -0.162 | -0.162 |
| CSCO | -14.44 | -0.016 | -0.072 | -0.429 | -0.429 |
| EURUSD | +0.19 | -0.018 | -0.047 | -0.364 | -0.364 |
| EWG | +2.80 | -0.018 | -0.039 | -0.305 | -0.305 |
| PG | -6.42 | -0.018 | +0.007 | +0.006 | +0.006 |
| DJI | +5.84 | -0.019 | -0.040 | -0.312 | -0.312 |
| SMH | -2.02 | -0.019 | +0.045 | +0.247 | +0.247 |
| MSFT | +3.14 | -0.020 | +0.026 | +0.130 | +0.130 |
| CVX | -4.71 | -0.020 | +0.032 | +0.156 | +0.156 |
| NZDUSD | +0.43 | -0.021 | -0.042 | -0.325 | -0.325 |
| UUP | -1.13 | -0.023 | +0.030 | +0.149 | +0.149 |
| TSLA | -0.15 | -0.023 | -0.019 | -0.188 | -0.188 |
| NDX | -0.05 | -0.023 | -0.036 | -0.292 | -0.292 |
| AMD | +3.62 | -0.024 | -0.007 | -0.097 | -0.097 |
| PSQ | -8.02 | -0.025 | +0.013 | +0.045 | +0.045 |
| TLT | +2.73 | -0.026 | -0.057 | -0.403 | -0.403 |
| CAT | -1.01 | -0.026 | -0.024 | -0.227 | -0.227 |
| XLI | +5.63 | -0.027 | -0.023 | -0.221 | -0.221 |
| DAX | +2.12 | -0.027 | -0.032 | -0.279 | -0.279 |
| ABBV | -1.52 | -0.028 | +0.033 | +0.169 | +0.169 |
| BTC | -10.52 | -0.029 | -0.032 | -0.273 | -0.273 |
| HD | +0.30 | -0.029 | -0.029 | -0.260 | -0.260 |
| CORN | +0.75 | -0.031 | +0.015 | +0.058 | +0.058 |
| XLK | -4.53 | -0.031 | -0.019 | -0.182 | -0.182 |
| LQD | -2.27 | -0.032 | -0.048 | -0.370 | -0.370 |
| USDMXN | +0.23 | -0.033 | -0.002 | -0.071 | -0.071 |
| UST30Y | +1.40 | -0.034 | -0.003 | -0.084 | -0.084 |
| PFF | -2.77 | -0.034 | -0.038 | -0.299 | -0.299 |
| SOYBEANS | +0.46 | -0.034 | -0.015 | -0.156 | -0.156 |
| META | -4.82 | -0.035 | -0.047 | -0.357 | -0.357 |
| BRK-B | -8.02 | -0.036 | -0.013 | -0.130 | -0.130 |
| BKLN | +1.31 | -0.037 | -0.020 | -0.201 | -0.201 |
| GBPUSD | -0.20 | -0.037 | -0.059 | -0.416 | -0.416 |
| AMZN | +0.31 | -0.038 | -0.014 | -0.136 | -0.136 |
| BNDX | -8.75 | -0.039 | -0.093 | -0.468 | -0.468 |
| EWJ | -14.77 | -0.039 | -0.101 | -0.487 | -0.487 |
| V | -8.78 | -0.040 | -0.081 | -0.448 | -0.448 |
| SH | -12.84 | -0.040 | +0.039 | +0.214 | +0.214 |
| GOOGL | -2.21 | -0.042 | -0.081 | -0.455 | -0.455 |
| SDS | -19.65 | -0.045 | +0.033 | +0.175 | +0.175 |
| QCOM | -2.97 | -0.047 | -0.046 | -0.351 | -0.351 |
| TXN | -2.55 | -0.048 | -0.059 | -0.409 | -0.409 |
| FXI | -2.27 | -0.050 | -0.108 | -0.494 | -0.494 |
| MA | +9.31 | -0.052 | -0.049 | -0.377 | -0.377 |
| DIS | -0.08 | -0.052 | -0.028 | -0.253 | -0.253 |
| USO | +8.18 | -0.056 | +0.111 | +0.468 | +0.468 |
| IWF | +1.31 | -0.059 | -0.066 | -0.422 | -0.422 |
| VZ | -8.39 | -0.065 | -0.100 | -0.481 | -0.481 |
| SQQQ | -26.03 | -0.065 | -0.016 | -0.169 | -0.169 |
| XLY | +2.08 | -0.066 | -0.077 | -0.435 | -0.435 |
| IBM | -0.03 | -0.066 | -0.045 | -0.344 | -0.344 |
| BA | -0.87 | -0.067 | -0.045 | -0.338 | -0.338 |
| T | -2.60 | -0.074 | -0.089 | -0.461 | -0.461 |
| CRM | -1.10 | -0.129 | -0.187 | -0.500 | -0.500 |

## 13. Versions

Every fine-tuned model has its own immutable version id; the two existing live-shadow models are never modified. 'challenger' = in live shadow (passed every gate), 'research' = recorded, not shadowed.

| Version | Horizon | Model | Status |
|---|---|---|---|
| alpha-learned-1w-rank-relret-exp | 1W | ridge (relative return) | research |
| alpha-learned-1w-rank-elasticnet-exp | 1W | elastic net | research |
| alpha-learned-1w-rank-pairwise-exp | 1W | pairwise (RankNet) | research |
| alpha-learned-1w-rank-listwise-exp | 1W | listwise (ListNet) | research |
| alpha-learned-1w-stability-exp | 1W | stability penalty (global) | research |
| alpha-learned-1w-stability-hier-exp | 1W | stability penalty (hierarchy) | research |
| alpha-learned-1w-stability-class-exp | 1W | stability classes | research |
| alpha-learned-1w-regime-exp | 1W | regime-conditional (global) | research |
| alpha-learned-1w-regime-hier-exp | 1W | regime-conditional (hierarchy) | research |
| alpha-learned-1w-decay-exp | 1W | time decay (global) | research |
| alpha-learned-1w-decay-hier-exp | 1W | time decay (hierarchy) | research |
| alpha-learned-1w-window-exp | 1W | rolling window (global) | research |
| alpha-learned-1w-window-hier-exp | 1W | rolling window (hierarchy) | research |
| alpha-learned-1w-cluster-exp | 1W | signal clusters | research |
| alpha-learned-1w-sign-exp | 1W | signs | research |
| alpha-learned-1w-interact-exp | 1W | interactions (economic) | research |
| alpha-learned-1w-blend-exp | 1W | blend | research |
| alpha-learned-1w-smooth-exp | 1W | learned global + turnover smoothing | research |
| alpha-learned-1w-smooth-hier-exp | 1W | learned hierarchy + turnover smoothing | research |
| alpha-learned-1w-nested-exp | 1W | nested model selection | research |
| alpha-learned-1m-stronger-shrinkage-(λ-nested)-exp | 1M | stronger shrinkage (λ nested) | research |
| alpha-learned-1m-family-level-weights-(15)-exp | 1M | family-level weights (15) | research |
| alpha-learned-1m-top-10-signals-(training-correlation)-exp | 1M | top 10 signals (training correlation) | research |
| alpha-learned-1m-top-20-signals-(training-correlation)-exp | 1M | top 20 signals (training correlation) | research |
| alpha-learned-1m-stable-signals-only-exp | 1M | stable signals only | research |
| alpha-learned-1m-fundamental-only-exp | 1M | fundamental only | research |
| alpha-learned-1m-relative-value-+-fundamental-exp | 1M | relative value + fundamental | research |
| alpha-learned-1m-macro-+-fundamental-exp | 1M | macro + fundamental | research |
| alpha-learned-1m-class-level-hierarchy-(K-nested)-exp | 1M | class-level hierarchy (K nested) | research |
| alpha-learned-1m-nested-selection-(reduced-dimension)-exp | 1M | nested selection (reduced dimension) | research |
| alpha-learned-3m-stronger-shrinkage-(λ-nested)-exp | 3M | stronger shrinkage (λ nested) | research |
| alpha-learned-3m-family-level-weights-(15)-exp | 3M | family-level weights (15) | research |
| alpha-learned-3m-top-10-signals-(training-correlation)-exp | 3M | top 10 signals (training correlation) | research |
| alpha-learned-3m-top-20-signals-(training-correlation)-exp | 3M | top 20 signals (training correlation) | research |
| alpha-learned-3m-stable-signals-only-exp | 3M | stable signals only | research |
| alpha-learned-3m-fundamental-only-exp | 3M | fundamental only | research |
| alpha-learned-3m-relative-value-+-fundamental-exp | 3M | relative value + fundamental | research |
| alpha-learned-3m-macro-+-fundamental-exp | 3M | macro + fundamental | research |
| alpha-learned-3m-class-level-hierarchy-(K-nested)-exp | 3M | class-level hierarchy (K nested) | research |
| alpha-learned-3m-nested-selection-(reduced-dimension)-exp | 3M | nested selection (reduced dimension) | research |
| alpha-learned-6m-stronger-shrinkage-(λ-nested)-exp | 6M | stronger shrinkage (λ nested) | research |
| alpha-learned-6m-family-level-weights-(15)-exp | 6M | family-level weights (15) | research |
| alpha-learned-6m-top-10-signals-(training-correlation)-exp | 6M | top 10 signals (training correlation) | research |
| alpha-learned-6m-top-20-signals-(training-correlation)-exp | 6M | top 20 signals (training correlation) | research |
| alpha-learned-6m-stable-signals-only-exp | 6M | stable signals only | research |
| alpha-learned-6m-fundamental-only-exp | 6M | fundamental only | research |
| alpha-learned-6m-relative-value-+-fundamental-exp | 6M | relative value + fundamental | research |
| alpha-learned-6m-macro-+-fundamental-exp | 6M | macro + fundamental | research |
| alpha-learned-6m-class-level-hierarchy-(K-nested)-exp | 6M | class-level hierarchy (K nested) | research |
| alpha-learned-6m-nested-selection-(reduced-dimension)-exp | 6M | nested selection (reduced dimension) | research |
| alpha-learned-12m-stronger-shrinkage-(λ-nested)-exp | 12M | stronger shrinkage (λ nested) | research |
| alpha-learned-12m-family-level-weights-(15)-exp | 12M | family-level weights (15) | research |
| alpha-learned-12m-top-10-signals-(training-correlation)-exp | 12M | top 10 signals (training correlation) | research |
| alpha-learned-12m-top-20-signals-(training-correlation)-exp | 12M | top 20 signals (training correlation) | research |
| alpha-learned-12m-stable-signals-only-exp | 12M | stable signals only | research |
| alpha-learned-12m-fundamental-only-exp | 12M | fundamental only | research |
| alpha-learned-12m-relative-value-+-fundamental-exp | 12M | relative value + fundamental | research |
| alpha-learned-12m-macro-+-fundamental-exp | 12M | macro + fundamental | research |
| alpha-learned-12m-class-level-hierarchy-(K-nested)-exp | 12M | class-level hierarchy (K nested) | research |
| alpha-learned-12m-nested-selection-(reduced-dimension)-exp | 12M | nested selection (reduced dimension) | research |
| hedge-lambda-sizing-exp | 1W–3M | λ-conditional hedge size surface | research (2 cells pass every gate; live shadow needs a λ-aware hedge grader, not built) |

