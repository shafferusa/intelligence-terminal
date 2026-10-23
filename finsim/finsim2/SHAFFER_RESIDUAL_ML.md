# FinSim2 — Residual ML / Meta-Learning Program

Run 2026-09-27 03:20:50 · protocol `SHAFFER_RESIDUAL_PROTOCOL.md` (fixed before any market result; amendments listed there were made after the synthetic capability suite and before market data) · research only.

**Frozen and untouched:** production (shaffer-2.1, shaffer-alpha-2.1-production), Directional prior-only, hedge-2, `alpha-learned-1w-global-exp` (D) and `alpha-learned-1w-hierarchy-exp` (E) — formulas, backtests, live predictions, G3-XS and clocks — and the λ-hedge live shadow. Every candidate below has its own immutable `resid-…-exp` research version.

Baselines reproduced: E weekly rank IC +0.0667, D +0.0522 (walk-forward, all eras). Residual table: 121751 records from 2009-01-02 to 2026-09-16 with an out-of-sample E; β (slope of u on zE, 2009–2012) = +0.0512. Evaluation 2013–2024 (eras 2013–16, 2017–20, 2021–24); 2025– untouched (A7).

## Verdict

- **E's residuals behave like noise to every pre-registered model.** The best Alpha residual (R4 boosted trees) changes the weekly rank IC by +0.0096 (t +1.7) on 2013–2024; none reaches t ≥ 2.
- E's reliability is predictable (REL AUC − ½ = +0.015, t +5.7; gates passed), but beyond E's own conviction the gain is -0.0016 (t -0.8) — reliability is almost entirely E's own conviction, which E already uses.
- D vs E: always-D minus always-E = -0.0205 (t -3.3); the selector DE-A did not pass (Δ -0.0100), the blend DE-B did not pass (Δ -0.0055).
- Tail probabilities improve on E's percentile alone (T2 top, T4 top, T4 bottom): mostly by learning which assets move a lot (asset class, signal disagreement) — magnitude, symmetric across both tails, not direction.
- Multi-horizon transfer: no gain; Directional residual: no gain over prior-only; hedge action policy: 0 of 208 cells passed H1–H5.
- **Conclusion: with the information in these 74 signals and context features, E is close to the limit of what this program's models can extract for 1W ranking.** What remains predictable is how big moves will be and how reliable E is given its own conviction — not which way E is wrong.
- Candidates eligible for a live-shadow proposal: REL, T2 top, T4 top, T4 bottom. Nothing was put into live shadow; D and E, production, the Directional prior and hedge-2 are unchanged.

## 1. Synthetic capability tests

Worlds with an intentionally incomplete baseline, run through exactly the same code as the market data, before it.

| World | Planted | Result | Checks |
|---|---|---|---|
| A | missing linear signal | PASS | ✓ R1 passes A1–A7; ✓ recovers sig1 as the largest missing weight (+) |
| B | missing sector-specific signal | PASS | ✓ R3 improves E (t ≥ 2); ✓ hierarchy beats the global residual; ✓ chooses sector or asset depth; ✓ A5 flags the single-sector concentration |
| C | missing volatility-regime interaction | PASS | ✓ R4 passes A1–A7; ✓ R4 splits on volatility and sig1; ✓ linear residual cannot see it (R1 t < 2) |
| D | missing nonlinear threshold | PASS | ✓ R4 passes A1–A7; ✓ trees beat the linear residual; ✓ R4 splits on sig1 first |
| E | optimal baseline, pure-noise residual | PASS | ✓ no Alpha residual passes; ✓ no D/E selector passes; ✓ no pairwise model passes; ✓ no tail model passes; ✓ REL adds nothing beyond conviction (t < 2) |
| F | baseline overconfident in one asset class | PASS | ✓ R3 improves E (t ≥ 2); ✓ REL lower in the overconfident class; ✓ REL beyond conviction (t ≥ 2); ✓ class-aware tail model (T3) passes |
| G | two baselines, each best in a different regime | PASS | ✓ a D/E selector passes vs always-E; ✓ prefers E in high volatility, D in low |
| H | hedge policy: planted 0.5× sizing in low volatility / a noise world | PASS | ✓ recovers the planted sizing and passes H1–H5; ✓ noise world passes nothing |

- World A: best residual R2 Δ +0.0768 (t +22.6); R1's largest missing weight `sig1`; R4's first splits `sig1`, `sig0`, `sig2`, `sig3`, `ctx:zP`.
- World C: best residual R4 Δ +0.0586 (t +28.5); R1's largest missing weight `sig0`; R4's first splits `sig1`, `sig3`, `ctx:volatility`, `sig5`, `ctx:signal_disagreement`.
- World D: best residual R4 Δ +0.0988 (t +28.6); R1's largest missing weight `sig0`; R4's first splits `sig1`, `sig2`, `sig0`, `sig3`, `sig6`.
- World E (optimal E): REL AUC − ½ t +20.6 — REL passes its gates even here, because E's conviction predicts its hits; beyond conviction t -0.8. This is why the reliability section reports the beyond-conviction test next to the gates.

## 2. Alpha residual (what E gets wrong)

Score = β·zE + γ·r̂ (a correction of E, never a refit; MT and DH are refits and PW a pairwise score, all judged on the same records). Δ = weekly rank IC(challenger) − IC(E), 2013–2024, week-clustered t.

| Model | Δ IC | t | 2013–16 | 2017–20 | 2021–24 | 2025– | LS10 net vs E | A1 | A2 | A3 | A4 | A5 | A6 | A7 | Status |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| R1 ridge residual | +0.0046 | +1.1 | -0.0060 | +0.0153 | +0.0045 | +0.0151 | +0.00015 | ✓ | ✗ | ✗ | ✓ | ✗ | ✓ | ✓ | NOT VALIDATED |
| R1s uncertainty-shrunk ridge | +0.0064 | +1.5 | +0.0000 | +0.0178 | +0.0014 | +0.0171 | +0.00002 | ✓ | ✗ | ✓ | ✓ | ✗ | ✓ | ✓ | NOT VALIDATED |
| R2 elastic net | -0.0002 | -0.1 | -0.0074 | +0.0000 | +0.0066 | +0.0139 | -0.00018 | ✗ | ✗ | ✗ | ✗ | ✗ | ✓ | ✓ | NOT VALIDATED |
| R3 hierarchical residual | -0.0050 | -1.2 | -0.0118 | +0.0000 | -0.0033 | +0.0112 | -0.00053 | ✗ | ✗ | ✗ | ✗ | ✗ | ✓ | ✓ | NOT VALIDATED |
| R4 boosted trees | +0.0096 | +1.7 | +0.0095 | +0.0167 | +0.0028 | +0.0066 | +0.00042 | ✓ | ✗ | ✓ | ✓ | ✗ | ✓ | ✓ | NOT VALIDATED |
| R5 ensemble | +0.0064 | +1.0 | -0.0031 | +0.0195 | +0.0027 | +0.0163 | -0.00016 | ✓ | ✗ | ✓ | ✗ | ✗ | ✓ | ✓ | NOT VALIDATED |
| MT multi-task (economic groups) | -0.0011 | -0.3 | +0.0043 | -0.0085 | +0.0007 | -0.0031 | +0.00017 | ✗ | ✗ | ✗ | ✓ | ✗ | ✓ | ✗ | NOT VALIDATED |
| DH dynamic hierarchy | +0.0023 | +0.6 | +0.0025 | -0.0007 | +0.0052 | +0.0107 | +0.00023 | ✓ | ✗ | ✓ | ✓ | ✗ | ✓ | ✓ | NOT VALIDATED |
| PW pairwise → per-asset score | -0.0247 | -4.5 | -0.0102 | -0.0346 | -0.0291 | +0.0009 | -0.00229 | ✗ | ✗ | ✗ | ✗ | ✗ | ✓ | ✓ | NOT VALIDATED |

Nested choices (per era cut → option; the 2013 cut uses the pre-registered default):

- R1 ridge residual: 2013→`1000|0.5`, 2017→`10000|0.25`, 2021→`10000|0.5`, 2025→`10000|0.5`, 2100→`10000|0.5`
- R2 elastic net: 2013→`0.01|0.5`, 2017→`0.003|0.0`, 2021→`0.03|0.5`, 2025→`0.03|0.5`, 2100→`0.03|0.5`
- R3 hierarchical residual: 2013→`5000|sector|0.5`, 2017→`1000|class|0.0`, 2021→`20000|sector|1.0`, 2025→`20000|asset|0.5`, 2100→`20000|asset|0.5`
- R4 boosted trees: 2013→`20|0.5`, 2017→`40|0.5`, 2021→`20|1.0`, 2025→`20|1.0`, 2100→`20|1.0`
- R5 ensemble: 2013→`3|0.5`, 2017→`2|1.0`, 2021→`2|1.0`, 2025→`3|1.0`, 2100→`3|1.0`

What R1 would correct today (largest standardised residual weights, all matured data): `ret_1d` -0.0357, `vix` +0.0288, `dollar_mom_3m` +0.0278, `ctx:zD` -0.0268, `ctx:class:Equity` +0.0263, `ctx:class:Volatility` -0.0218, `mr_opportunity` -0.0215, `ret_1w` -0.0212, `ctx:market` -0.0211, `ctx:class:FX` -0.0196.

R4 (20 rounds) most-used splits: `ctx:class:Equity` ×29, `credit_signal` ×15, `corr_252` ×13, `ret_1d` ×12, `z_50` ×11, `ret_1w` ×10, `ctx:dispersion` ×10, `ctx:signal_disagreement` ×10.
R4 (40 rounds) most-used splits: `ctx:class:Equity` ×32, `credit_signal` ×28, `ret_1d` ×26, `z_50` ×20, `ret_1w` ×18, `dist_ma200` ×18, `trend_quality` ×18, `corr_252` ×17.

A5 detail — Δ with each asset class removed from the cross-section, and outside the crisis windows:

| Model | Commodity | Credit | Crypto | Equity | FX | Rates | Volatility | no crises |
|---|---|---|---|---|---|---|---|---|
| R1 | +0.0082 | +0.0051 | +0.0047 | -0.0103 | +0.0012 | +0.0012 | +0.0045 | +0.0064 |
| R1s | +0.0088 | +0.0060 | +0.0066 | -0.0055 | +0.0051 | +0.0029 | +0.0065 | +0.0097 |
| R2 | +0.0025 | +0.0002 | -0.0003 | -0.0079 | -0.0029 | -0.0021 | -0.0005 | +0.0014 |
| R3 | -0.0023 | -0.0043 | -0.0047 | -0.0120 | -0.0078 | -0.0054 | -0.0052 | -0.0024 |
| R4 | +0.0141 | +0.0091 | +0.0103 | -0.0040 | +0.0076 | +0.0073 | +0.0091 | +0.0152 |
| R5 | +0.0130 | +0.0070 | +0.0072 | -0.0110 | +0.0020 | +0.0033 | +0.0058 | +0.0116 |
| MT | -0.0002 | -0.0004 | -0.0014 | +0.0001 | -0.0024 | -0.0018 | -0.0011 | -0.0018 |
| DH | +0.0065 | +0.0017 | +0.0022 | -0.0020 | +0.0048 | -0.0022 | +0.0026 | +0.0013 |
| PW | -0.0210 | -0.0233 | -0.0242 | -0.0410 | -0.0218 | -0.0255 | -0.0243 | -0.0230 |

## 3. E reliability (REL)

Label: E and the realised return on the same side of the week's median. Model: ridge logistic on the context features, walk-forward. Research display only (0–1).

- AUC − ½: +0.0147 (t +5.7, 626 weeks); by era 2013–16 +0.0051, 2017–20 +0.0199, 2021–24 +0.0190, 2025– +0.0349.
- E's rank IC inside reliability terciles (low / mid / high): +0.0258 / +0.0433 / +0.0820 (monotone); top − bottom by era 2013–16 +0.0291, 2017–20 +0.0715, 2021–24 +0.0678, 2025– +0.1390.
- Beyond E's own conviction (REL AUC − conviction-only AUC, descriptive): -0.0016 (t -0.8).
- Mean reliability by class: Commodity 0.514, Credit 0.531, Crypto 0.511, Equity 0.521, FX 0.506, Rates 0.518, Volatility 0.532.
- Largest coefficients (final fit, standardised): `ctx:volatility` +0.080, `ctx:risk` +0.057, `ctx:zE` -0.041, `ctx:|zE|` +0.040, `ctx:pE` +0.038, `ctx:zD-zE` +0.030, `ctx:class:Volatility` -0.028, `ctx:class:Credit` +0.025.
- Gates: AUC > ½ ✓, t ≥ 2 ✓, monotone terciles ✓, ≥ 2 of 3 eras ✓, FDR ✓ → **PASSED (eligible for a live-shadow proposal)**.

## 4. D versus E

Benchmarks on the residual-period records: always-E rank IC +0.0700, always-D +0.0494; D − E -0.0205 (t -3.3); by era 2013–16 —, 2017–20 -0.0316, 2021–24 -0.0095, 2025– +0.0038.

| Model | Δ vs always-E | t | eras (13–16 / 17–20 / 21–24 / 25–) | churn vs E | gates | Status |
|---|---|---|---|---|---|---|
| DE-A (choose) | -0.0100 | -2.5 | — / -0.0079 / -0.0122 / -0.0053 | 0.227 vs 0.347 | beats ✗ t ✗ eras ✗ churn ✗ FDR ✗ | NOT VALIDATED |
| DE-B (blend) | -0.0055 | -1.5 | — / -0.0134 / +0.0025 / +0.0073 | 0.267 vs 0.347 | beats ✗ t ✗ eras ✗ churn ✗ FDR ✗ | NOT VALIDATED |

What the selector learned — mean P(E closer to the outcome than D), 2013–2024: volatility=high_vol 0.492, volatility=other 0.481, market=bull 0.486, market=other 0.492, risk=risk-on 0.480, risk=other 0.492.
Largest coefficients: `ctx:|zE|` -0.609, `ctx:pE^2` +0.187, `ctx:class:FX` +0.080, `ctx:signal_disagreement` +0.079, `ctx:pE` +0.069, `ctx:class:Equity` -0.058, `ctx:class:Commodity` +0.048, `ctx:zD-zE` +0.044.

## 5. Dynamic hierarchy (DH)

E's tree with each node's deviation scaled by the James–Stein factor of its own out-of-sample evidence. K choices: 2013→5000, 2017→5000, 2021→1000, 2025→1000, 2100→1000. Final K 1000: 2901 of 18500 node × signal deviations keep any trust; mean trust by level class 0.110, ptype 0.103, sector 0.092, industry 0.063, asset 0.080.
Δ vs E +0.0023 (t +0.6) → **NOT VALIDATED** (full gate row in §2).

## 6. Tail probabilities (top and bottom decile of the week)

T1 (benchmark) = logistic on E's percentile (cubic). Gains are Brier(T1) − Brier(model) per record, weekly-clustered.

| Side | Model | Brier gain | t | log loss (model / T1) | eras 13–16 / 17–20 / 21–24 / 25– | Status |
|---|---|---|---|---|---|---|
| top | T2 top decile | +0.000541 | +2.0 | 0.3132 / 0.3255 | -0.003192 / +0.002596 / +0.002200 / +0.005868 | PASSED (eligible for a live-shadow proposal) |
| top | T3 top decile | +0.000495 | +1.9 | 0.3134 / 0.3255 | -0.003300 / +0.002579 / +0.002188 / +0.005813 | NOT VALIDATED |
| top | T4 top decile | +0.002184 | +23.9 | 0.3125 / 0.3255 | +0.002029 / +0.001978 / +0.002543 / +0.003246 | PASSED (eligible for a live-shadow proposal) |
| bottom | T2 bottom decile | +0.000331 | +1.1 | 0.3154 / 0.3250 | -0.002126 / +0.001809 / +0.001300 / +0.005360 | NOT VALIDATED |
| bottom | T3 bottom decile | -0.000377 | -1.2 | 0.3178 / 0.3250 | -0.004041 / +0.001824 / +0.001069 / +0.005360 | NOT VALIDATED |
| bottom | T4 bottom decile | +0.002541 | +19.3 | 0.3117 / 0.3250 | +0.002985 / +0.002393 / +0.002245 / +0.002256 | PASSED (eligible for a live-shadow proposal) |

T4 (top) most-used splits: `ctx:class:Commodity` ×51, `ctx:signal_disagreement` ×42, `ctx:class:Equity` ×33, `ctx:class:Rates` ×31, `ctx:class:FX` ×14, `corr_252` ×12, `ctx:zP` ×9, `ctx:log_history` ×8.

T4 (bottom) most-used splits: `ctx:class:Commodity` ×60, `ctx:class:Equity` ×48, `ctx:log_history` ×24, `ctx:class:Rates` ×18, `ctx:signal_disagreement` ×17, `vol_pctile` ×17, `ctx:zP` ×16, `ret_12m` ×9.

Base rate (top decile): 10.0%.

Base rate (bottom decile): 10.0%.

## 7. Pairwise (A beats B?)

400 random same-week pairs per week. Pair accuracy: PW-L 51.06% vs sign(E_A − E_B) 51.88%; gain -0.0082 (t -3.8); by era 2013–16 -0.0033, 2017–20 -0.0111, 2021–24 -0.0103, 2025– +0.0010.
Gates: gain > 0 ✗, t ≥ 2 ✗, eras ✗, FDR ✗ → **NOT VALIDATED**. Largest coefficients: `fam:Macro` -0.972, `fam:Credit` -0.308, `fam:Volatility` +0.307, `vol_20` -0.293, `fam:Liquidity` +0.244, `fam:Valuation` -0.177.
Aggregated to a per-asset score (mean win probability vs 40 opponents) and judged as an Alpha model: Δ -0.0247 (t -4.5) → NOT VALIDATED.

## 8. Multi-task learning across economic groups (MT)

Tree global → economic group → asset (22 groups): **Broad equity** (18); **Information Technology** (15); **Rates** (15); **FX** (15); **Credit** (11); **Consumer Staples** (11); **Financials** (8); **Energy** (8); **Materials** (8); **Health Care** (7); **Communication Services** (7); **Consumer Discretionary** (6); **International** (5); **US** (4).
Choices: 2013→1000|asset, 2017→1000|asset, 2021→5000|asset, 2025→5000|asset, 2018→5000|asset, 2100→5000|asset. Δ vs E -0.0011 (t -0.3) → **NOT VALIDATED**.

## 9. Multi-horizon transfer (1W informs 1M / 3M)

1M / 3M global ridge shrunk toward c × the 1W weights fitted before the same cut; judged only on its own horizon's outcomes.

| Horizon | choice | G1 | G2 | G3 | G4 | vs learned global (Δ, t) | Status |
|---|---|---|---|---|---|---|---|
| 1M | c=2|λ=100000 | ✗ | ✗ | ✗ | ✓ | +0.0114 (t +1.4) | NOT VALIDATED |
| 3M | c=2|λ=100000 | ✗ | ✗ | ✗ | ✓ | +0.0109 (t +0.5) | NOT VALIDATED |

Transfer did not pass; as pre-registered, nothing else is tried at the long horizons in this program.

## 10. Directional residual (logit P = logit P_prior + capped adjustment)

| Horizon | λ \| cap | Brier gain vs prior (t) | log loss gain | balanced acc (model / prior) | ECE (model / prior) | slope | eras won | mean \|adj\| | Status |
|---|---|---|---|---|---|---|---|---|---|
| 1W | 1000|0.03 | +0.000105 (+0.6) | +0.000209 | 50.95% / 50.90% | 0.0137 / 0.0094 | 0.99 | 1/3 | 1.45% | NOT VALIDATED |
| 1M | 1000|0.01 | -0.000292 (-0.8) | -0.000621 | 51.53% / 51.61% | 0.0134 / 0.0105 | 1.00 | 1/3 | 1.27% | NOT VALIDATED |

1W final adjustment coefficients: `E percentile` +0.093, `market state` -0.058, `volatility state` +0.033, `dispersion` -0.022, `E reliability (logit)` -0.015, `breadth state` +0.009, `volume_z` -0.007, `ret_1m` +0.004.

## 11. Hedge residual / action policy

208 objective cells judged (sizing and product policies × 1W / 1M × λ ∈ {1, 5}); passed H1–H5 with FDR: **none**.

| Cell | cases (dates) | departures | ΔU vs hedge-2 | t | H1 | H2 (eras) | H3 | H4 | H5 | Status |
|---|---|---|---|---|---|---|---|---|---|---|
| sizing/1W/Equity/name@1 | 345 | 83% | +2979 | +9.3 | ✗ | ✓ (3/3) | ✓ | ✗ | ✓ | NO HEDGE IMPROVEMENT |
| sizing/1M/Crypto/target_vol@1 | 52 | 83% | +17151 | +3.7 | ✗ | ✗ (1/1) | ✓ | ✗ | ✓ | INSUFFICIENT DATA |
| sizing/1W/Equity/systematic@1 | 345 | 61% | +947 | +3.7 | ✗ | ✓ (3/3) | ✓ | ✗ | ✓ | NO HEDGE IMPROVEMENT |
| sizing/1M/Equity/name@1 | 164 | 39% | +1912 | +3.3 | ✗ | ✓ (3/3) | ✓ | ✗ | ✓ | NO HEDGE IMPROVEMENT |
| sizing/1W/Equity/target_vol@1 | 345 | 40% | +386 | +3.0 | ✗ | ✓ (3/3) | ✓ | ✗ | ✓ | NO HEDGE IMPROVEMENT |
| sizing/1W/Equity/beta@1 | 345 | 49% | +540 | +2.5 | ✗ | ✓ (3/3) | ✓ | ✗ | ✓ | NO HEDGE IMPROVEMENT |
| sizing/1W/Equity/drawdown@1 | 345 | 49% | +1342 | +2.3 | ✗ | ✓ (3/3) | ✓ | ✗ | ✓ | NO HEDGE IMPROVEMENT |
| sizing/1W/Equity/es@1 | 345 | 49% | +1342 | +2.3 | ✗ | ✓ (3/3) | ✓ | ✗ | ✓ | NO HEDGE IMPROVEMENT |
| sizing/1W/Equity/var@1 | 345 | 49% | +1342 | +2.3 | ✗ | ✓ (3/3) | ✓ | ✗ | ✓ | NO HEDGE IMPROVEMENT |
| sizing/1M/Equity/target_vol@5 | 164 | 20% | +1697 | +2.2 | ✗ | ✗ (2/3) | ✓ | ✗ | ✓ | NO HEDGE IMPROVEMENT |
| sizing/1M/Equity/min_variance@5 | 164 | 27% | +4050 | +2.2 | ✗ | ✓ (3/3) | ✓ | ✗ | ✓ | NO HEDGE IMPROVEMENT |
| sizing/1M/Credit/name@1 | 129 | 14% | +274 | +2.1 | ✗ | ✗ (2/3) | ✓ | ✗ | ✓ | NO HEDGE IMPROVEMENT |
| sizing/1W/Equity/sector@1 | 345 | 50% | +351 | +2.0 | ✗ | ✓ (3/3) | ✓ | ✗ | ✓ | NO HEDGE IMPROVEMENT |
| sizing/1M/FX/fx@1 | 164 | 38% | +81 | +1.9 | ✗ | ✓ (3/3) | ✓ | ✗ | ✓ | NO HEDGE IMPROVEMENT |
| sizing/1W/Commodity/target_vol@5 | 183 | 35% | +1199 | +1.9 | ✗ | ✗ (2/3) | ✓ | ✓ | ✓ | NO HEDGE IMPROVEMENT |
| product/1W/Commodity/min_variance@5 | 137 | 1% | +14 | +1.9 | ✗ | ✗ (1/3) | ✓ | ✓ | ✓ | NO HEDGE IMPROVEMENT |
| sizing/1M/Equity/systematic@1 | 164 | 17% | +850 | +1.8 | ✗ | ✗ (2/3) | ✓ | ✗ | ✓ | NO HEDGE IMPROVEMENT |
| sizing/1M/Equity/beta@5 | 164 | 13% | +1600 | +1.7 | ✗ | ✓ (3/3) | ✓ | ✓ | ✓ | NO HEDGE IMPROVEMENT |
| sizing/1W/FX/fx@1 | 345 | 38% | +53 | +1.7 | ✗ | ✓ (3/3) | ✓ | ✗ | ✓ | NO HEDGE IMPROVEMENT |
| sizing/1W/Equity/target_vol@5 | 345 | 34% | +1698 | +1.7 | ✗ | ✓ (3/3) | ✓ | ✗ | ✓ | NO HEDGE IMPROVEMENT |
| sizing/1W/Credit/min_variance@5 | 196 | 55% | +25 | +1.6 | ✗ | ✗ (1/3) | ✓ | ✓ | ✓ | NO HEDGE IMPROVEMENT |
| sizing/1W/Equity/name@5 | 345 | 39% | +743 | +1.6 | ✗ | ✗ (2/3) | ✓ | ✗ | ✓ | NO HEDGE IMPROVEMENT |
| sizing/1W/Credit/credit@1 | 345 | 32% | +85 | +1.6 | ✗ | ✗ (2/3) | ✓ | ✗ | ✓ | NO HEDGE IMPROVEMENT |
| product/1W/FX/fx@5 | 172 | 22% | +156 | +1.5 | ✗ | ✗ (2/3) | ✓ | ✗ | ✓ | NO HEDGE IMPROVEMENT |
| sizing/1M/Equity/sector@1 | 164 | 6% | +209 | +1.5 | ✗ | ✗ (2/3) | ✓ | ✓ | ✓ | NO HEDGE IMPROVEMENT |
| product/1M/Equity/min_variance@1 | 58 | 23% | +2341 | +1.5 | ✗ | ✗ (2/2) | ✓ | ✓ | ✓ | INSUFFICIENT DATA |
| product/1W/Equity/crash@1 | 172 | 23% | +194 | +1.4 | ✗ | ✗ (1/3) | ✓ | ✓ | ✓ | NO HEDGE IMPROVEMENT |
| sizing/1W/Commodity/min_variance@5 | 273 | 36% | +963 | +1.4 | ✗ | ✗ (2/3) | ✓ | ✓ | ✓ | NO HEDGE IMPROVEMENT |
| sizing/1W/Crypto/drawdown@5 | 111 | 15% | +5319 | +1.4 | ✗ | ✗ (0/1) | ✓ | ✓ | ✓ | NO HEDGE IMPROVEMENT |
| sizing/1W/Crypto/es@5 | 111 | 15% | +5319 | +1.4 | ✗ | ✗ (0/1) | ✓ | ✓ | ✓ | NO HEDGE IMPROVEMENT |
| sizing/1W/Crypto/var@5 | 111 | 15% | +5319 | +1.4 | ✗ | ✗ (0/1) | ✓ | ✓ | ✓ | NO HEDGE IMPROVEMENT |
| sizing/1W/Equity/crash@1 | 345 | 61% | +1548 | +1.4 | ✗ | ✓ (3/3) | ✓ | ✗ | ✓ | NO HEDGE IMPROVEMENT |
| sizing/1M/Rates/min_variance@1 | 164 | 29% | +367 | +1.3 | ✗ | ✗ (2/3) | ✓ | ✗ | ✓ | NO HEDGE IMPROVEMENT |
| product/1M/Equity/min_variance@5 | 58 | 37% | +7238 | +1.3 | ✗ | ✗ (2/2) | ✓ | ✓ | ✓ | INSUFFICIENT DATA |
| sizing/1M/Rates/duration@5 | 164 | 33% | +504 | +1.3 | ✗ | ✗ (2/3) | ✓ | ✓ | ✓ | NO HEDGE IMPROVEMENT |
| sizing/1M/Commodity/systematic@5 | 130 | 6% | +939 | +1.3 | ✗ | ✗ (2/3) | ✓ | ✓ | ✓ | NO HEDGE IMPROVEMENT |
| sizing/1W/Crypto/target_vol@5 | 111 | 3% | +392 | +1.3 | ✗ | ✗ (0/1) | ✓ | ✓ | ✓ | NO HEDGE IMPROVEMENT |
| sizing/1W/Commodity/drawdown@5 | 273 | 68% | +653 | +1.2 | ✗ | ✓ (3/3) | ✓ | ✓ | ✓ | NO HEDGE IMPROVEMENT |
| sizing/1W/Commodity/es@5 | 273 | 68% | +653 | +1.2 | ✗ | ✓ (3/3) | ✓ | ✓ | ✓ | NO HEDGE IMPROVEMENT |
| sizing/1W/Commodity/var@5 | 273 | 68% | +653 | +1.2 | ✗ | ✓ (3/3) | ✓ | ✓ | ✓ | NO HEDGE IMPROVEMENT |
| … 168 more cells (all in the result) | | | | | | | | | | |

How often each action was chosen out of sample (A = hedge-2): sizing/1W@1: A 23373, x1.5 15268, x0.5 5028, x1.25 2160, x0.75 680; sizing/1W@5: A 30690, x0.5 5449, x1.5 8359, x1.25 1438, x0.75 573; sizing/1M@1: x1.5 2440, A 16234, x0.5 1823, x0.75 911, x1.25 716; sizing/1M@5: x1.5 1185, A 17242, x0.75 755, x0.5 2732, x1.25 210; product/1W@1: A 6817, type:equity_index_future 199, type:etf 648, type:index_option 27, type:inverse_etf 525, type:high_yield 37, type:fx_future 27, type:treasury_future 64, type:fx_forward 41, type:fx_spot 4, type:ig_corporate 4, type:leveraged_loan 28, type:etn 8, type:currency_trust 1; product/1W@5: type:inverse_etf 450, A 6850, type:equity_index_future 185, type:etf 617, type:index_option 85, type:fx_future 45, type:treasury_future 99, type:fx_spot 7, type:high_yield 48, type:leveraged_loan 27, type:fx_forward 9, type:etn 7, type:currency_trust 1; product/1M@1: A 2116, type:etf 391, type:inverse_etf 125, type:equity_index_future 89, type:high_yield 18, type:index_option 91, type:treasury_future 56, type:fx_spot 3, type:fx_forward 6, type:leveraged_loan 1, type:etn 53, type:fx_future 6, type:equity_put 2; product/1M@5: A 2104, type:etf 369, type:inverse_etf 63, type:high_yield 8, type:equity_index_future 152, type:treasury_future 57, type:index_option 130, type:fx_future 5, type:fx_forward 5, type:fx_spot 2, type:leveraged_loan 1, type:etn 61.

Counterfactual table (1W, descriptive, all eras; $ per $1M book over the week):

| Objective | Volatility | Action | Risk reduction | Profit given up | Cost | Basis error | U(λ=1) | U(λ=5) |
|---|---|---|---|---|---|---|---|---|
| beta | high_vol | 0.50× | +6239 | +1741 | 18 | 3417 | +4479 | -2486 |
| beta | high_vol | 0.75× | +9037 | +2612 | 27 | 2309 | +6398 | -4050 |
| beta | high_vol | 1.00× (hedge-2) | +11525 | +3483 | 37 | 2395 | +8005 | -5925 |
| beta | high_vol | 1.25× | +13601 | +4353 | 46 | 3590 | +9202 | -8211 |
| beta | high_vol | 1.50× | +15148 | +5224 | 55 | 5177 | +9869 | -11026 |
| beta | low_vol | 0.50× | +2316 | +388 | 25 | 1736 | +1902 | +348 |
| beta | low_vol | 0.75× | +3291 | +583 | 38 | 1355 | +2671 | +340 |
| beta | low_vol | 1.00× (hedge-2) | +4115 | +777 | 50 | 1547 | +3288 | +180 |
| beta | low_vol | 1.25× | +4765 | +971 | 63 | 2165 | +3731 | -154 |
| beta | low_vol | 1.50× | +5219 | +1165 | 75 | 2952 | +3978 | -683 |
| commodity | high_vol | 0.50× | +568 | +243 | 135 | 2071 | +189 | -781 |
| commodity | high_vol | 0.75× | +792 | +364 | 203 | 1974 | +225 | -1231 |
| commodity | high_vol | 1.00× (hedge-2) | +975 | +485 | 271 | 1974 | +219 | -1722 |
| commodity | high_vol | 1.25× | +1116 | +607 | 339 | 2071 | +171 | -2255 |
| commodity | high_vol | 1.50× | +1215 | +728 | 406 | 2252 | +81 | -2831 |
| commodity | low_vol | 0.50× | +656 | +78 | 92 | 1240 | +487 | +174 |
| commodity | low_vol | 0.75× | +944 | +117 | 137 | 1082 | +690 | +221 |
| commodity | low_vol | 1.00× (hedge-2) | +1202 | +156 | 183 | 988 | +863 | +237 |
| commodity | low_vol | 1.25× | +1428 | +195 | 229 | 976 | +1004 | +222 |
| commodity | low_vol | 1.50× | +1619 | +234 | 275 | 1050 | +1111 | +173 |
| crash | high_vol | 0.50× | +10788 | +1461 | 404 | 4482 | +8923 | +3081 |
| crash | high_vol | 0.75× | +15693 | +2191 | 606 | 3580 | +12897 | +4133 |
| crash | high_vol | 1.00× (hedge-2) | +19783 | +2921 | 808 | 2980 | +16054 | +4370 |
| crash | high_vol | 1.25× | +22782 | +3651 | 1010 | 2876 | +18121 | +3516 |
| crash | high_vol | 1.50× | +24925 | +4382 | 1212 | 3316 | +19331 | +1805 |
| crash | low_vol | 0.50× | +3924 | +410 | 310 | 1872 | +3204 | +1566 |
| crash | low_vol | 0.75× | +5544 | +614 | 465 | 1326 | +4465 | +2007 |
| crash | low_vol | 1.00× (hedge-2) | +6843 | +819 | 620 | 1096 | +5404 | +2127 |
| crash | low_vol | 1.25× | +7789 | +1024 | 775 | 1354 | +5990 | +1894 |
| crash | low_vol | 1.50× | +8345 | +1229 | 929 | 1912 | +6187 | +1272 |
| credit | high_vol | 0.50× | +709 | +140 | 82 | 1188 | +486 | -74 |
| credit | high_vol | 0.75× | +1018 | +210 | 123 | 1557 | +684 | -157 |
| credit | high_vol | 1.00× (hedge-2) | +1294 | +280 | 165 | 1973 | +849 | -272 |
| credit | high_vol | 1.25× | +1538 | +350 | 206 | 2411 | +981 | -420 |
| credit | high_vol | 1.50× | +1746 | +421 | 247 | 2862 | +1079 | -603 |
| credit | low_vol | 0.50× | +308 | -53 | 65 | 717 | +296 | +510 |
| credit | low_vol | 0.75× | +436 | -80 | 98 | 896 | +419 | +740 |
| credit | low_vol | 1.00× (hedge-2) | +547 | -107 | 130 | 1098 | +523 | +951 |
| credit | low_vol | 1.25× | +639 | -134 | 163 | 1312 | +610 | +1145 |
| credit | low_vol | 1.50× | +713 | -160 | 196 | 1533 | +678 | +1320 |
| crypto | high_vol | 0.50× | +36012 | +7555 | 593 | 7016 | +27864 | -2356 |
| crypto | high_vol | 0.75× | +52319 | +11332 | 889 | 9456 | +40097 | -5233 |
| crypto | high_vol | 1.00× (hedge-2) | +64149 | +15110 | 1186 | 15638 | +47853 | -12587 |
| crypto | high_vol | 1.25× | +63804 | +18887 | 1482 | 22685 | +43434 | -32116 |
| crypto | high_vol | 1.50× | +51625 | +22665 | 1778 | 29994 | +27182 | -63478 |
| crypto | low_vol | 0.50× | +20917 | +3278 | 526 | 6666 | +17114 | +4002 |
| crypto | low_vol | 0.75× | +30691 | +4917 | 789 | 6511 | +24986 | +5319 |
| crypto | low_vol | 1.00× (hedge-2) | +39342 | +6556 | 1051 | 9439 | +31736 | +5513 |
| crypto | low_vol | 1.25× | +45460 | +8194 | 1314 | 13584 | +35952 | +3174 |
| crypto | low_vol | 1.50× | +46594 | +9833 | 1577 | 18130 | +35184 | -4150 |
| curve | high_vol | 0.50× | +178 | +3 | 18 | 1587 | +157 | +146 |
| curve | high_vol | 0.75× | +241 | +4 | 27 | 1713 | +210 | +193 |
| curve | high_vol | 1.00× (hedge-2) | +286 | +6 | 36 | 1882 | +245 | +222 |
| curve | high_vol | 1.25× | +314 | +7 | 45 | 2083 | +262 | +233 |
| curve | high_vol | 1.50× | +324 | +9 | 54 | 2309 | +261 | +227 |
| curve | low_vol | 0.50× | +359 | +7 | 20 | 1356 | +332 | +302 |
| curve | low_vol | 0.75× | +505 | +11 | 29 | 1467 | +464 | +419 |
| curve | low_vol | 1.00× (hedge-2) | +627 | +15 | 39 | 1609 | +573 | +514 |
| curve | low_vol | 1.25× | +726 | +19 | 49 | 1774 | +659 | +584 |
| curve | low_vol | 1.50× | +800 | +22 | 59 | 1956 | +719 | +629 |

## 12. Current predictions and uncertainty

E percentile buckets → the asset's 1W log return minus the week's mean (2013–2024; monotonicity ρ 1.00, 0 adjacent violations):

| Bucket | n | mean | median | hit | vol | downside | net long | net short | 95% CI of mean | 95% of single outcomes |
|---|---|---|---|---|---|---|---|---|---|---|
| 0–5 | 4394 | -0.48% | -0.30% | 42.8% | 3.52% | 2.91% | -0.57% | 0.38% | [-0.61%, -0.34%] | [-7.7%, 5.7%] |
| 5–10 | 4393 | -0.23% | -0.14% | 46.4% | 3.09% | 2.32% | -0.32% | 0.14% | [-0.34%, -0.12%] | [-6.7%, 5.9%] |
| 10–20 | 8706 | -0.16% | -0.12% | 46.7% | 3.15% | 2.33% | -0.25% | 0.08% | [-0.24%, -0.09%] | [-6.6%, 6.2%] |
| 20–40 | 17559 | -0.06% | -0.04% | 49.1% | 3.10% | 2.23% | -0.15% | -0.02% | [-0.11%, -0.02%] | [-6.2%, 5.8%] |
| 40–60 | 17515 | 0.04% | 0.02% | 50.5% | 3.02% | 2.08% | -0.04% | -0.13% | [-0.00%, 0.09%] | [-5.8%, 6.1%] |
| 60–80 | 17557 | 0.07% | 0.04% | 50.9% | 3.10% | 2.16% | -0.02% | -0.15% | [0.01%, 0.12%] | [-5.7%, 6.1%] |
| 80–90 | 8700 | 0.15% | 0.07% | 51.4% | 3.09% | 2.07% | 0.06% | -0.24% | [0.07%, 0.22%] | [-5.5%, 6.1%] |
| 90–95 | 4394 | 0.17% | 0.11% | 52.3% | 3.30% | 2.35% | 0.08% | -0.26% | [0.06%, 0.28%] | [-5.8%, 6.5%] |
| 95–100 | 4393 | 0.38% | 0.18% | 54.5% | 3.45% | 2.12% | 0.28% | -0.48% | [0.24%, 0.53%] | [-6.0%, 7.6%] |

E percentile buckets → the asset's 1W log return minus the week's mean (2025–; monotonicity ρ 0.98, 1 adjacent violations):

| Bucket | n | mean | median | hit | vol | downside | net long | net short | 95% CI of mean | 95% of single outcomes |
|---|---|---|---|---|---|---|---|---|---|---|
| 0–5 | 677 | -0.66% | -0.47% | 39.6% | 3.96% | 3.31% | -0.75% | 0.57% | [-1.06%, -0.26%] | [-9.9%, 6.8%] |
| 5–10 | 629 | -0.55% | -0.41% | 41.8% | 3.50% | 2.96% | -0.64% | 0.46% | [-0.88%, -0.22%] | [-8.9%, 5.4%] |
| 10–20 | 1322 | -0.26% | -0.28% | 42.3% | 3.93% | 2.84% | -0.35% | 0.17% | [-0.52%, 0.00%] | [-8.1%, 6.9%] |
| 20–40 | 2614 | -0.10% | -0.19% | 45.6% | 3.74% | 2.67% | -0.18% | 0.01% | [-0.29%, 0.10%] | [-7.6%, 7.7%] |
| 40–60 | 2608 | 0.02% | -0.06% | 48.9% | 3.67% | 2.58% | -0.07% | -0.10% | [-0.13%, 0.17%] | [-7.6%, 7.8%] |
| 60–80 | 2614 | 0.08% | 0.03% | 50.4% | 3.83% | 2.76% | -0.01% | -0.16% | [-0.10%, 0.25%] | [-7.9%, 7.7%] |
| 80–90 | 1326 | 0.21% | 0.09% | 51.5% | 3.92% | 2.58% | 0.12% | -0.30% | [-0.05%, 0.47%] | [-7.5%, 8.2%] |
| 90–95 | 628 | 0.79% | 0.49% | 58.6% | 3.95% | 1.96% | 0.69% | -0.88% | [0.46%, 1.12%] | [-6.1%, 9.5%] |
| 95–100 | 678 | 0.51% | 0.31% | 55.5% | 4.25% | 2.60% | 0.41% | -0.61% | [0.10%, 0.93%] | [-8.3%, 9.6%] |

Today's research-only view of 155 assets is in `SHAFFER_META_CURRENT.md` and in the ML Lab (Residual / Meta ML → Current predictions). The interval shown for one asset is the bucket's 95% range of single weekly outcomes, not the narrow CI of the mean. No sizing is derived from any of it.

## 13. What passed

- [meta] REL E reliability
- [tail] T2 top
- [tail] T4 top
- [tail] T4 bottom

## 14. What failed

| Family | Candidate | Failed gates |
|---|---|---|
| alpha | R1 ridge residual | A2, A3, A5, fdr |
| alpha | R1s uncertainty-shrunk ridge | A2, A5, fdr |
| alpha | R2 elastic net | A1, A2, A3, A4, A5, fdr |
| alpha | R3 hierarchical residual | A1, A2, A3, A4, A5, fdr |
| alpha | R4 boosted trees | A2, A5, fdr |
| alpha | R5 ensemble | A2, A4, A5, fdr |
| alpha | MT multi-task (economic groups) | A1, A2, A3, A5, A7, fdr |
| alpha | DH dynamic hierarchy | A2, A5, fdr |
| alpha | PW pairwise → per-asset score | A1, A2, A3, A4, A5, fdr |
| meta | DE-A | beats_E, t_ok, eras, churn, fdr |
| meta | DE-B | beats_E, t_ok, eras, churn, fdr |
| tail | T3 top | t_ok |
| tail | T2 bottom | t_ok, fdr |
| tail | T3 bottom | brier_gain, t_ok, fdr |
| pair | PW-L | accuracy_gain, t_ok, eras, fdr |
| horizon | MH-1M | G1, G2, G3, vs_global, fdr |
| horizon | MH-3M | G1, G2, G3, vs_global, fdr |
| directional | DR-1W | brier, eras, fdr |
| directional | DR-1M | brier, logloss, balanced, eras, fdr |
| hedge | 208 policy cells | no cell passed H1–H5 + FDR |

## 15. Eligible for a live-shadow proposal

Passed every pre-registered gate — eligible to be PROPOSED for live shadow (not activated; a proposal needs a decision, its own frozen version and a live expectation):

- [meta] REL — `resid-reliability-exp`. It passed, but its AUC beyond a conviction-only model is -0.0016 (t -0.8): it re-expresses E's own |score| — the capability world with an optimal E passes the same gates. Useful as a display of how confident E is; not a reason to change anything. A live-shadow proposal is not recommended.
- [tail] T2 top — `resid-t2-top-exp`. Brier gain +0.00054 (t +2.0), era 2013–16 -0.00319: a borderline pass, dominated by T4 on the same target.
- [tail] T4 top — `resid-t4-top-exp`. Today P(top decile) and P(bottom decile) correlate +0.84 across 155 assets. Brier gain +0.00218 (t +23.9) in every era; it splits mostly on `ctx:class:Commodity`, `ctx:signal_disagreement`, `ctx:class:Equity`, `ctx:class:Rates` — which assets make large moves, a magnitude effect present in both tails, not a view on direction. It improves tail probabilities for risk display; it is not an Alpha improvement.
- [tail] T4 bottom — `resid-t4-bottom-exp`. Today P(top decile) and P(bottom decile) correlate +0.84 across 155 assets. Brier gain +0.00254 (t +19.3) in every era; it splits mostly on `ctx:class:Commodity`, `ctx:class:Equity`, `ctx:log_history`, `ctx:class:Rates` — which assets make large moves, a magnitude effect present in both tails, not a view on direction. It improves tail probabilities for risk display; it is not an Alpha improvement.

