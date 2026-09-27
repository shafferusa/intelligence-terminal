# Residual / Meta-Learning Program — pre-registered protocol

Fixed 2026-09-27 and committed **before any market outcome of this program was computed**. The candidate list, targets,
metrics, gates and FDR families below cannot be widened after results are seen; a change needs a new protocol version.
Code: `engine/residual.py`, `hedge/hedgepolicy.py`. Reports: `SHAFFER_RESIDUAL_ML.md`, `SHAFFER_META_CURRENT.md`.

**Untouched:** production (shaffer-2.1, shaffer-alpha-2.1-production), the Directional definition (prior-only), hedge-2,
`alpha-learned-1w-global-exp` (D) and `alpha-learned-1w-hierarchy-exp` (E) — their formulas, backtests, live predictions,
gate G3-XS and clocks — and the λ-hedge live shadow. Everything here is research; every candidate gets a new immutable
version id (`resid-…-exp`); **nothing enters live shadow automatically**, even when it passes.

## 0. Data, baselines and the out-of-sample rule

* Records: the 1W signal-level research records (and 1M / 3M for §9), the same ones D and E were validated on.
* Baselines: E's and D's **walk-forward** scores — each outer era scored by the fit made before it
  (`finetune.fam_baselines`, which reproduces D = 0.0526 and E = 0.0671). A baseline score used in a residual,
  a feature or a label is always this out-of-sample score. No fit that saw an outcome is ever used to build a residual.
* Consequence: E is out of sample only from 2009, so residual / meta models can be trained only on records dated 2009 or
  later that matured before the cut. **Evaluation eras: 2013–16, 2017–20, 2021–24 (complete) and 2025– (the untouched
  final period, A7).** Model choices are never made on 2025–.
* Within-week normal scores: for each weekly cross-section, `u` = Φ⁻¹((rank − ½)/n) of the realised 1W log return;
  `zE`, `zD`, `zP` the same transform of E's, D's and production's scores.

## 1. Residual target (Alpha)

`r = u − β·zE`, with β the out-of-sample slope of u on zE over 2009–2012 (fixed; it only sets the scale and is PIT
for every later cut). A residual challenger predicts r̂ from point-in-time features; its score is **β·zE + γ·r̂** (a
correction of E, never a refit). γ ∈ {0, 0.25, 0.5, 1}.

**Features (all known at t):** the 74 signals; zE, zD, zP, zD − zE, |zE|, E percentile and its square; asset class
(one-hot); volatility, market and rates states and risk-on/off (±1); breadth (share of the cross-section above its
200-day average, PIT); cross-sectional dispersion of trailing 1W returns; signal disagreement (SD of the 15 family
means of x); log of the asset's matured history length.

## 2. Nesting

Every hyperparameter is chosen with `finetune.Ctx.pick`: per outer era, the option with the best mean inner metric over
walk-forward weeks that matured before the era (≥ 52 inner weeks; otherwise the pre-registered default). Inner metric for
Alpha challengers: weekly rank IC of the combined score with u. Defaults (used when inner history is short, e.g. the
2013 cut): γ = 0.5 and the middle grid value of every other parameter.

## 3. Candidates — complete list

**Alpha residual (FDR family "alpha"):**

| Id | Model | Grid |
|---|---|---|
| R1 | global ridge residual (week-demeaned, the learned engine's global fit on the residual table) | λ ∈ {50, 1e3, 1e4, 1e5}, γ |
| R1s | R1 with every correction shrunk by its uncertainty: r̂ · r̂² / (r̂² + se²), se = between-era disagreement of R1 refitted on each training era (no correction until ≥ 2 training eras) | as R1 |
| R2 | sparse elastic net residual | L1 = f · max\|xᵀr\|, f ∈ {0.003, 0.01, 0.03}, γ |
| R3 | hierarchical residual, global → class → product type → sector → industry → asset, partial pooling | K ∈ {1e3, 5e3, 2e4}, depth ∈ {class, sector, asset}, γ |
| R4 | shallow boosted residual: depth-2 trees on 16-bin features (20 largest \|R1 weights\| + context), rate 0.1, leaf ≥ 2% weight | rounds ∈ {20, 40}, γ |
| R5 | ensemble of R1, R3, R4 corrections | weights ∈ {(1,0,0), (0,1,0), (0,0,1), (⅓,⅓,⅓), (½,½,0), (½,0,½), (0,½,½)}, γ |
| MT | multi-task: global + economic group (the sector name across classes — e.g. energy equities with crude and energy ETFs; Treasuries with bond ETFs) + asset, a full refit **compared with E** (not a residual) | K ∈ {1e3, 5e3, 2e4}, depth ∈ {group, asset} |
| DH | dynamic hierarchy: E's tree with each node's deviation from its parent scaled by its own out-of-sample evidence (positive-part James–Stein on the node's inner t) instead of fixed pooling | K ∈ {1e3, 5e3, 2e4} |
| PW | pairwise model aggregated to a per-asset score (see §7) | — |

**Meta (FDR family "meta"):** REL (E reliability), DE-A (choose D or E), DE-B (conditional blend).
Benchmarks, not candidates: always-E, always-D.

**Tail (FDR family "tail"):** T2, T3, T4 for the top decile and for the bottom decile (T1 = E percentile alone is the
benchmark).

**Pairwise (FDR family "pair"):** PW-L (logistic on pair differences) against sign(E_A − E_B).

**Multi-horizon (FDR family "horizon"):** MH-1M, MH-3M.

**Directional residual (FDR family "directional"):** DR-1W, DR-1M.

**Hedge (FDR family "hedge"):** one policy per horizon (1W, 1M) × λ ∈ {1, 5} × objective cell.

## 4. Alpha residual gates (all required)

* **A1** positive incremental walk-forward rank IC vs E on 2013–2024 (Δ = IC(challenger) − IC(E) on the same records).
* **A2** week-clustered t ≥ 2 on Δ, and Benjamini–Hochberg FDR (q = 0.10) across the "alpha" family.
* **A3** Δ > 0 in ≥ 2 of the 3 complete eras and Δ ≥ −0.005 in the third (the 3/4 rule on the eras that exist).
* **A4** net long-short of the 10% tails after product costs ≥ E's.
* **A5** no single part explains it: Δ > 0 when each asset class is removed from the cross-section in turn, and outside
  the crisis windows (2008–09, Mar–Apr 2020, 2022).
* **A6** conviction stays calibrated: rank correlation of the 10 percentile-bucket mean returns ≥ E's − 0.10.
* **A7** the untouched 2025– period: Δ ≥ 0.

## 5. Meta gates

* **REL:** OOS discrimination — AUC of reliability for E's half-concordance (label: E and the outcome on the same side of
  the median) > 0.5 with week-clustered t ≥ 2; E's rank IC rises monotonically across reliability terciles; top-minus-
  bottom tercile IC of E > 0 in ≥ 2 of 3 eras; FDR.
* **DE-A / DE-B:** weekly rank IC > always-E with t ≥ 2; ≥ 2 of 3 eras; rank churn not above E's by more than 0.05; FDR.
* Label for D-vs-E: 1[\|pE − pu\| < \|pD − pu\|] (E's percentile closer to the outcome's). Model: ridge logistic on the
  context features; DE-A picks E when P ≥ ½, DE-B scores P·zE + (1 − P)·zD.

## 6. Tail gates

Targets: the asset finishes in the top (bottom) decile of the week's realised returns. T1 = logistic on E percentile
(cubic); T2 = + residual features (ridge logistic); T3 = T2 + class-level intercepts and E slopes with ridge pooling;
T4 = T1 + depth-2 boosting of the logistic residual (20 rounds). Gate vs T1: Brier gain > 0 with week-clustered t ≥ 2,
≥ 2 of 3 eras, log loss not worse, FDR.

## 7. Pairwise

Per week, 400 random same-date pairs (seeded). PW-L: logistic on ΔzE, ΔzD, the 15 family-mean differences, same-class
× ΔzE, Δvolatility, Δvaluation, Δmomentum, Δreliability(REL, when available). Gate vs sign(ΔzE): pair accuracy gain
> 0 with t ≥ 2 by week, ≥ 2 of 3 eras, FDR. For the Alpha family, PW is also scored per asset (mean predicted win
probability against 40 random same-week opponents) and judged by A1–A7.

## 8. Dynamic hierarchy, multi-task

Judged by the Alpha gates A1–A7 against E (they are refits, not residuals; Δ vs E).

## 9. Multi-horizon transfer (the only long-horizon idea)

1M and 3M global ridge shrunk toward c × the 1W global weights fitted on outcomes matured before the same cut, instead
of toward zero: c ∈ {0.5, 1, 2}, λ ∈ {1e3, 1e4, 1e5}. Judged only on its own horizon's outcomes: G1–G4 vs production
(the Alpha program's gates) and Δ vs the learned global model of that horizon (t ≥ 2, ≥ 3/4 eras), FDR. If it fails,
the report says so and nothing else is tried.

## 10. Directional residual

logit P = logit P_prior + clip(xᵀb) with |P − P_prior| ≤ cap. Inputs: E percentile, REL (when available), zD − zE,
volatility / market / breadth states, dispersion, ret_1w, ret_1m, volume_z, vol_20. Ridge toward 0 with λ ∈ {10, 100,
1000} and cap ∈ {1, 2, 3, 5} percentage points, nested. Gate: the Directional program's fixed gates against prior-only
(Brier t ≥ 2, log loss better, balanced accuracy, calibration slope 0.8–1.25, ECE not worse, ≥ 3/4 eras where they
exist) and FDR. 1W and 1M.

## 11. Hedge residual policy

Cases: the hedge research cases (1W, 1M). Action set: 0.50×, 0.75×, 1.00×, 1.25×, 1.50× hedge-2's package (and, on the
product-replay cases, only product types already eligible under hedge-2). Target per case and action: its exact additive
share of ΔU(λ) vs hedge-2 (`volhedge.case_contributions`) within the training set of its objective. Model: ridge per
action on context (risk class, book type, volatility state, VIX, market state, hedge-2 cost per NAV, book Alpha
percentile). Policy: the action with the highest predicted gain **only if** gain − 1.96 · se > 0 (se = between-era
disagreement), else hedge-2. Walk-forward by era; judged per objective cell with the unchanged hedge gates H1–H5 at that
λ (cost, basis, tail, eras, crises) and FDR. The counterfactual table (context | action | risk reduction | profit given
up | cost | basis | U(λ)) is reported descriptively.

## 12. Uncertainty

* Expected relative return and its interval come from E's out-of-sample percentile buckets {0–5, 5–10, 10–20, 20–40,
  40–60, 60–80, 80–90, 90–95, 95–100}: bucket mean, median, hit rate, volatility, downside, net after costs, a week-
  clustered CI of the mean, sample size, and the 2.5–97.5% range of individual outcomes (the honest "interval" for one
  asset's week — not a narrow CI from overlapping observations). Monotonicity is tested.
* A residual correction is displayed as material only when \|r̂\| > 1.96 · se; otherwise it is shown as "not
  distinguishable from zero".

## 13. Capability suite (must pass before market data)

Synthetic worlds with an intentionally incomplete baseline: (A) a missing linear signal, (B) a missing sector-specific
signal, (C) a missing volatility-regime interaction, (D) a missing nonlinear threshold, (E) an optimal baseline with pure-
noise residuals, (F) a baseline overconfident in one asset class, (G) two baselines each best in a different regime. The
engine must recover what is planted, improve the baseline out of sample, reject noise (no gate pass in E) and learn
when each baseline is better (G). If any fails, the engine is fixed before market data is touched.
