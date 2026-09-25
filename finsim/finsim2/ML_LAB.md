# FinSim2 — product philosophy and the ML Lab

FinSim2 is the **Portfolio Manager career path**, rebuilt around real market data and quantitative research. It is not a
generic AI prediction platform. The user is the portfolio manager and makes every decision:

```
Research the market → evaluate the Shaffer Score → enter a trade → optionally apply the Shaffer Hedge → manage the portfolio → review results
```

Three systems, never combined into one number:

| System | Question it answers | Where |
|---|---|---|
| **Shaffer Score** | What does the quantitative evidence say about this product, at this horizon? Horizon-specific (1D … 12M, longer only where the evidence allows), explainable, built from economically meaningful families. | Everywhere a product appears: Markets, Asset Research, Analytics, Watchlist, Portfolio positions, trade ticket |
| **Shaffer Hedge** | What risks does this trade or portfolio create, and what hedge reduces the selected risk *without unnecessarily destroying the expected profit*? | Trade ticket (Trade only / Trade + Shaffer Hedge), Shaffer Hedge page, Analytics → Shaffer Hedge |
| **ML Lab** | Are the Shaffer formulas working, and would a different — possibly more specialised — weighting or hedge sizing have done better, out of sample? | ML Lab |

The ML Lab **does not trade and does not give its own recommendation**. It studies historical and live results and
proposes improvements to the two Shaffer systems. Nothing it finds reaches production without a deliberate promotion.

## Marketplace vs Analytics

* **Markets** is the decision and trading interface: what can I trade, what is its Shaffer Score at my horizon
  (score, calibrated score, confidence, agreement), can I trade it, should I look at a hedge. One click opens the ticket.
* **Analytics** is the inside-the-numbers research terminal: family and signal contributions, the historical score,
  calibration, OOS IC and hit rate, confidence, effective sample, decay, regime behaviour, factor exposures, risk
  decomposition, and (tab *Shaffer Hedge*) the hedge engine's view — candidates, cost, basis, tail behaviour, before /
  after — and the hedge-design comparison.

## Trade workflow

The ticket shows, for the selected horizon, the Shaffer Score, calibrated score, confidence, expected return (only where
the calibration supports one), the supporting and the opposing factors *at that horizon*, and the net long / short
scores. Then: **Trade only** or **Trade + Shaffer Hedge**. The hedge examines the *incremental* risk the trade creates.

### Hedge designs: protection against profit given up (`hedge/designs.py`)

A hedge is not judged by the variance it removes at any cost. For the trade (optionally measured on the whole book) the
ticket compares structurally different designs — no hedge, the main risk half / fully (engine's choice), partial beta
25% / 50%, full beta, sector hedge, index put, tail put on the asset, volatility hedge — each sized by the Shaffer Hedge
engine, on one common simulation:

```
U(design) = [Risk(before) − Risk(after)] − λ · [E P&L(before) − E P&L(after)] − cost
```

* Risk: ES 95% of the h-day P&L (tail, VaR, drawdown, crash objectives and the default) or its standard deviation
  (variance, beta, neutralising objectives).
* The distribution: every overlapping h-day window of the last ten years, factor moves *demeaned* (the sample's past
  drift is not a forecast); options fully re-priced at the horizon.
* The expected P&L comes from explicit drifts: a market prior (β × the market's long-run average) for every product —
  never an asset's own past average (survivorship) — plus the Shaffer evidence part of the traded asset's expected
  return *only where that horizon's calibration supports it*; products with a structural drift (VXX's roll decay,
  inverse funds) keep their own average.
* λ is the weight on expected profit given up. The panel shows the best design for λ = 0.5 … 10, so the trade-off is
  visible, and lets the user pick a design to book instead of the engine's package.

This is the Shaffer Score × Shaffer Hedge interaction: with a strong, *supported* bullish score, designs that remove the
market exposure give up more expected profit and a tail hedge can win; with a neutral score a bigger systematic hedge is
cheap; when the score disagrees with the trade's direction the panel says so ("the current quantitative evidence
disagrees with the trade"). When there is no supported evidence (today, most horizons) the panel says the score does not
change which design wins. Limitations are stated: bond carry and term premium, currency carry and commodity roll are
not modelled in the prior.

## The ML Lab

### Two datasets

1. **Historical research records** (`lab_records`). Every point-in-time sweep leaves, for every asset, horizon and weekly
   date, what was known that day — each family's score, the production raw score, each family's contribution — and,
   once the horizon has passed, what happened (the realised return, raw and volatility-scaled). "Pretend that date was
   today, compute the score with only what was known then, then look at what happened", repeated over 25 years.
   Built by `python -m finsim2 lab --build` (or the audit, or the *Rebuild research records* button).
2. **The live ledger.** Every day FinSim2 records genuine forecasts — Shaffer Score by horizon, ML volatility and drawdown,
   hedge recommendations, and every challenger in live shadow — never edited, graded when the horizon passes. It is the
   only dataset no backtest can fake.

### Weight research (`engine/lab.py`)

Does a different weighting of the Shaffer families do better — and should it differ by asset class, sector, industry,
asset or regime? For each horizon the challenger index Σ_f w_f · x_f over the production families' scores is fitted to
the volatility-scaled forward return by ridge regression **at every node of a hierarchy, each node shrunk toward its
parent**:

```
w_node = (X'X·q + λI)⁻¹ (X'y·q + λ · w_parent)        q = 5 / max(5, h)   (overlapping weekly records → effective observations)
Global → Asset class → Sector → Industry → Asset       λ = 200 effective observations (fixed in advance)
```

A ticker with little evidence inherits its industry's, then sector's, then class's, then the global weights — no ticker
is tuned on its own small sample. Variants: *one set* (global), *by class*, *class → sector → industry → asset*, *by
volatility regime*. Weights are shown next to production's actual say per family (its share of the score's
contributions).

Protocol — no look-ahead, rules fixed before any result:

| Step | What |
|---|---|
| Discovery | weights fitted only on records whose outcome was known before 2018-01-01 |
| Confirmation | those frozen weights scored on records dated from 2018 (untouched) |
| Walk-forward | expanding yearly refits: year Y predicted with records whose outcome ended before 1 January Y |
| Live shadow | the challenger is recorded daily in the prediction ledger (`shaffer:<version>`) next to production |

Metrics for every version: date-clustered IC (each week's evidence averaged across assets — correlated assets are not
independent tests), paired challenger − production IC on the same records, hit rate, calibration monotonicity,
top-minus-bottom decile spread, by class, sector and regime.

### Signal-level weight research (`engine/weights.py`, report `SHAFFER_WEIGHT_RESEARCH.md`)

The family research above can only re-scale whole families. The signal-level research learns the parameters *inside* the
Shaffer equation. The sweep now keeps, for every record, each signal's x = clip(z/2, −1, 1), its point-in-time direction δ,
weight ω, confidence c, regime factor r and decay d, and each family's multiplier g = W·A·H/K (lab_records version
`<score version>s`); these reproduce production's raw score exactly. Challengers, per horizon:

| Kind | Index | What is learned |
|---|---|---|
| signal | Σ β_i δ_i x_i c_i r_i d_i, β ≥ 0 | signal weights ω and family weights W·A·H jointly; the PIT direction δ is kept |
| scaling | Σ β_i δ_i x_i c_i^γc r_i^γr d_i^γd | also the confidence / regime / decay strengths, chosen by an inner holdout (last 4 years of each training window) |
| free | Σ β_i x_i, signed | every signal, even inactive ones — the over-fitting check |
| interact | signal + 5 regime interactions | Momentum × high vol, Valuation × rising rates, Growth × rising rates (real-yield proxy), Credit × recession, Trend × bear |
| family | Σ β_f F_f | family weights only (reference) |

Momentum × asset class is the class level of the hierarchy; trend × horizon is the per-horizon fit. Each is fitted at every
node of Global → Class → Sector → Industry → Asset with ridge shrinkage toward the parent (λ = 200 effective
observations), bounded weights (|β| ≤ 0.2 on standardised inputs), q = 5/max(5, h) for overlapping records; the `signal`
kind is also fitted with the hierarchy cut at each depth to measure where specialisation stops helping. Challenger raw =
100·tanh(index / s), s matched so its |score| distribution equals production's (bands comparable).

Validation: weights frozen before each era, trained only on outcomes matured before it — → 2008 test 2009–12, → 2012 test
2013–16, → 2016 test 2017–20, → 2020 test 2021–24, → 2024 test 2025– — plus the 2018 split. Metrics on the same records for
production, challenger and five naive baselines (always bullish, PIT positive-return frequency, previous direction, 12-1
momentum, 1-month mean reversion): directional accuracy with a date-clustered 95% interval, IC, rank IC, monotonicity,
top − bottom decile, accuracy by |score| band, the nine signed score bands (independent observations, % positive, mean,
median, direction right), by class. Training (in-sample) accuracy is shown next to it so a fit that only works in sample is
visible. Gates: G1 pooled walk-forward Δ IC t ≥ 2 and Δ accuracy ≥ 0; G2 ≥ 3 of 4 complete eras won, 2018 split Δ IC t ≥ 1,
accuracy above the best naive baseline; G3 live shadow. Status REJECT / SHADOW / ELIGIBLE FOR PROMOTION / INSUFFICIENT DATA;
a multi-metric research score (evidence − complexity − sign flips − weight turnover − concentration) ranks challengers
for display only. SHADOW challengers are recorded daily (production and challenger score, both versions, hierarchy node,
horizon, confidence, maturity date). `python -m finsim2 lab --weights` runs it and writes the report; ML Lab → *Signal
weights* shows it. For the hedge, `weights.reliability` gives how often a production score of that size was right at that
horizon against the best naive baseline — shown on the design panel, not used by the hedge math in this phase.

### Versions and promotion

Every formula has an ID and a registry entry (kv `formula:registry`): formula, families and signals, applicability,
horizon fit, weights (per node), training cutoff, date introduced, validation results. Production: `shaffer-2.1` and
`hedge-2`. Challengers: `shaffer-2.1-<variant>-exp`, `hedge-2-sizing-exp`.

```
Production Shaffer → ML Lab proposes a challenger → G1 historical walk-forward → G2 untouched confirmation → G3 live shadow → promote (user)
```

* G1: walk-forward before 2018, challenger IC above production with paired date-clustered t ≥ 2
* G2: confirmation from 2018, paired t ≥ 1 and calibration monotonicity not more than 0.05 lower
* G3: at least 60 graded live-shadow forecasts, challenger live IC ≥ production's on the same forecasts
* Promotion: only when G1–G3 pass, and only by an explicit user action (ML Lab → Challengers → Promote, with a
  confirmation). A promoted Shaffer weighting becomes a new score VERSION (history and calibration recomputed with it);
  a promoted hedge sizing is applied by the live hedge engine to its groups. Nothing is ever promoted automatically, and
  production is never continuously rewritten.

### Hedge research

The raw hedge stays financial mathematics (beta, DV01, CS01, FX notional, option delta / gamma / vega, covariance,
target risk). The Lab asks whether, historically, it was too big or too small:

* **Sizing study**: for every hedge group and objective, the constant resizing H = m · H_raw that worked best on windows
  before 2018, frozen and tested on the windows after (clustered by date). Confirmed multiples form the
  `hedge-2-sizing-exp` challenger (e.g. option hedges ≈ ×0.89, crypto ETF hedges ×0.85, credit ×0.94 — only where
  confirmed), live-shadowed on graded hedge recommendations (their daily P&L paths are kept for this) before promotion.
* **Objective-specific ML adjustments** (see `SHAFFER_HEDGE.md` §6): verified only if they beat the static rule, a
  constant resizing and the minimum-variance multiple.

Evaluation is not one number: variance, drawdown, tail loss, VaR, ES, basis error, hedge P&L, cost, upside sacrificed.

### What the Lab will not do

Choose weights on the full dataset and call them out of sample; tune every ticker on a tiny sample; keep optimising until
history looks good; use future data; promote anything automatically.

## Results on the real research store (data to 2026-09-24)

Research records: **819,392** point-in-time scored dates over 157 assets (1D 151k, 1W 150k, 1M 149k, 3M 144k, 6M 132k,
12M 93k). Weight research run: 3 minutes (one process per horizon).

**No weighting challenger passes discovery (G1) at any horizon; none enters the live shadow.** Paired challenger −
production IC, date-clustered t in brackets:

| Horizon | Variant | Walk-forward before 2018 | Confirmation (frozen, from 2018) | Walk-forward from 2018 |
|---|---|---|---|---|
| 1D | by class | −0.007 (−0.6) | +0.011 (+2.7) | +0.012 (+3.2) |
| 1W | by regime | −0.003 (−0.3) | +0.012 (+1.1) | +0.012 (+1.1) |
| 1M | hierarchy | −0.021 (−1.2) | +0.008 (+0.3) | −0.018 (−0.8) |
| 3M | global | −0.006 (−0.2) | +0.055 (+1.2) | +0.040 (+0.9) |
| 3M | hierarchy | −0.011 (−0.3) | +0.034 (+0.9) | −0.001 (−0.0) |
| 6M | by regime | +0.023 (+0.3) | +0.028 (+0.4) | +0.056 (+1.0) |
| 12M | by regime | −0.001 (−0.0) | +0.039 (+0.5) | +0.062 (+0.8) |

What this says:

* **Specialisation does not help yet.** The full class → sector → industry → asset hierarchy is usually *worse* out of
  sample than one global set (e.g. 1M walk-forward before 2018: −0.021; 3M from 2018: −0.001 vs +0.040 for global),
  even with shrinkage toward the parent: asset-level weights (visible in the weight table — e.g. Fundamental Quality at
  +59% for NVDA at 3M) are fitted noise.
* The few challengers that look better after 2018 (1D by class, t 3.2) were worse before it — exactly the pattern the
  discovery gate exists to stop.
* Several pass the confirmation gate alone (3M global, class and regime), none passes discovery.
* Production keeps higher calibration monotonicity on the pooled deciles than every challenger except at 3M.

Hedge sizing study (hedge audit, same data): **72 of 174** group × objective multiples confirmed after 2018 — the
`hedge-2-sizing-exp` challenger is in live shadow.

* Option hedges (equity, single-name, commodity) are oversized: every confirmed multiple is at the ×0.85 limit (the ±15%
  cap), worth +19% to +39% variance / downside reduction after 2018 — the real bias is at least that large.
* FX forwards ×0.85 (variance +4–5%), credit ×0.85–0.95 on the exposure / ES objectives, Treasury ETF shorts ×0.875.
* Linear equity hedges (SPY short, ES / MES futures) are correctly sized: the best multiple is ×1.02 and not confirmed.
* Crypto: not enough history before 2018 to discover anything.

Hedge designs, NVDA $150k at 6M (the ticket): expected return +5.1% = market prior only (no supported Shaffer evidence at
6M), ES 95% of the 6M P&L over 479 windows. Best by λ: 0.5–2 full beta hedge (1 MNQ future; ES cut by $23k for $5k of
expected profit), 5 an index put, 10 no hedge. With no supported evidence the Shaffer Score does not change which design
wins — the panel says so.
