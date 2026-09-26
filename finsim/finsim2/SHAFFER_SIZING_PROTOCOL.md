# From a validated E score to selection and position size — research protocol (pre-registered, not run)

Fixed 2026-09-26, before any sizing result has been computed. Nothing in this document is implemented as a trading
rule; FinSim2 never places trades on its own. When the research is run, this file is the rulebook it is judged by —
choices listed here cannot be widened after the results are seen, only narrowed for a *new* protocol version.

## 1. Question

The learned hierarchy E (`alpha-learned-1w-hierarchy-exp`, rank IC +0.067 walk-forward vs production +0.038) ranks
assets week to week. A ranking is not a portfolio. The question is which **simple, pre-declared** construction turns
the ranking into positions that earn more **after costs and risk** than (a) the same construction on production's
score and (b) naive portfolios — and whether the score's **magnitude** (conviction) deserves bigger positions or only
its **rank** matters.

## 2. Preconditions

* **Research (backtest) may start now** on the walk-forward records already used by the fine-tune (weekly, outer eras
  2009–12 … 2025–, never touched by any choice).
* **Any live use waits for gate G3-XS** on E (weekly live cross-sections, `engine/livexs.py`): ≥ 52 graded weeks, live
  rank IC > 0 at t ≥ 1.65, live Δ vs production ≥ 0, within 2 standard errors of the backtest, no decay alarm. A sizing
  rule on an edge that has not shown up live is not deployable, whatever its backtest.
* Production, the live-shadow models and the hedge are unchanged by this research.

## 3. The candidate rules (the complete list)

Formation weekly, on the scored cross-section; every rule is dollar-neutral long-short unless marked long-only.

| Id | Selection | Sizing inside each leg |
|---|---|---|
| S-q | top / bottom q of E's rank, q ∈ {10%, 20%, 30%} | equal weight |
| S-q-cls | top / bottom q **within each asset class** (class-neutral) | equal weight |
| R-lin | all assets | weight ∝ rank − median (rank-linear) |
| C-mult | top / bottom q | the reported ConvictionMultiplier of the score's percentile bucket, shrunk halfway to 1 |
| V-inv | top / bottom q | ∝ 1 / σ₆₀ (equal risk inside each leg) |
| A-risk | top / bottom q | ∝ score / σ₆₀ (alpha per unit of risk), capped at 3× the leg's mean weight |
| L-only | top q, long-only vs the equal-weight universe | equal weight |

Common constraints: per-asset weight ≤ 5% of gross, per-class net ≤ 20% of gross (class-neutral rules: 0), no leverage
beyond 1× gross per leg, weekly rebalance with a no-trade band b ∈ {0, ¼ weekly σ of the score rank} (positions inside
the band are not traded).

**Nested choices only:** q, b and (for C-mult) the bucket multipliers are chosen per outer era from inner walk-forward
weeks matured before the era, by inner **net** Sharpe — never by the outer result.

## 4. Costs and realism

* One-way costs by product type (`engine/finetune.COST_BY_PTYPE`: single stock 5 bp, sector ETF 3 bp, broad ETF 2 bp,
  index 1 bp, investment grade / high yield 5 bp, EM debt 6 bp, loans / municipal 8 bp, crypto 15 bp, …) on traded weight; **sensitivity at a flat 10 bp** and at 2× the
  product costs. A rule that only works at the product estimates is reported as cost-fragile (as 1D was).
* Short availability and borrow are not modelled — a known gap, reported with every result; long-only L-only is the
  control that needs neither.
* Capacity: no point-in-time volume history suitable for market-impact modelling in the store — reported as untested.

## 5. Metrics and benchmarks

Per rule, walk-forward and by era: weekly net return, annualised net Sharpe (non-overlapping weeks), t, max drawdown,
worst week, turnover, average holding period, hit rate, return by asset class, and exposure to market beta / class /
value / volatility (the neutralisation of SHAFFER_FINETUNE.md §8).

Benchmarks on identical weeks and costs: **the same construction on production's score**, the same construction on the
learned global model D, the equal-weight universe (long-only rules), and a random-rank placebo (same turnover).

## 6. Gates (all required; Benjamini–Hochberg FDR across the rules in §3)

* **P1** net long-short (or long-only excess) return > 0 with t ≥ 2 over outer walk-forward weeks;
* **P2** ≥ 3 of 4 complete eras positive;
* **P3** beats the same construction on production's score: Δ net Sharpe > 0 with a block-bootstrap 95% interval above 0;
* **P4** still positive at a flat 10 bp per side;
* **P5** max drawdown no worse than 1.5× the equal-weight construction's;
* **P6 (conviction)** C-mult and A-risk must additionally beat S-q (the same selection with equal weights) — magnitude
  sizing earns its place only if it adds to the rank.

## 7. Capability tests (before any market result is read)

1. Planted world with a known linear edge in the score: A-risk / R-lin must beat equal weights, and the nested choice
   must pick them.
2. Planted world where only the rank matters (monotone heavy-tailed transform): C-mult must NOT beat S-q.
3. Planted zero-edge world: no rule may pass P1 + FDR.
4. Planted cost-sensitive world (fast vs slow signal): the nested band b must widen as costs rise.

## 8. Outputs and versions

`SHAFFER_SIZING.md` (all rules, all gates, era tables, cost sensitivity, the conviction answer), an ML Lab view, and a
research version per rule (`sizing-e-<rule>-exp`). A passing rule enters **paper** live shadow on the weekly panel
(positions recorded, never traded) and needs its own ≥ 52 graded weeks before it can be shown as a suggestion.

## 9. Link to the hedge

The Alpha-conditional hedge test found no benefit (0 of 50 tests survive FDR, SHAFFER_HEDGE_FINETUNE.md), so a
portfolio built by these rules is hedged by hedge-2 as it stands; its hedge sizing follows the λ-surface only where a
cell has passed H-LIVE.
