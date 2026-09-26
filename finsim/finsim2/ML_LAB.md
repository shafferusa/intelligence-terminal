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

### Shaffer Alpha vs Shaffer Directional (`engine/directional.py`, report `SHAFFER_DIRECTIONAL_RESEARCH.md`)

The production Shaffer Score is a zero-centred *evidence* score — is this setup better or worse than normal? — and was
being judged against the sign of the *absolute* return. The Lab now keeps the two questions apart (research only; the
Marketplace score is unchanged):

* **Shaffer Alpha** (`shaffer-alpha-2.1-production` = the production score read as what it is): judged as a ranking of
  relative opportunity against the return in excess of a point-in-time base expectation — Pearson IC per asset,
  cross-sectional rank IC, weekly quintile spread, decile spread, hit rate against the weekly median, rank stability.
  Signal-level alpha challengers are fitted to that target. It does not have to beat "always bullish".
* **Shaffer Directional** (research versions `shaffer-directional-2.1-…-exp`): p_up = P(R_h > 0 | PIT information),
  score = 100·(2·p_up − 1), from a point-in-time base-return prior plus Shaffer evidence, logistic per hierarchy node shrunk
  to the parent. Judged on absolute direction against the best naive baseline (excess accuracy), balanced accuracy,
  bullish / bearish precision and recall, Brier score against the climatology forecaster, log loss, AUC, calibration
  (ECE, ten bins, nine score bands with expected vs realized p_up) and P(loss | score < −20 / −40 / −60).
* Base priors, all point-in-time (outcomes only once matured): zero; expanding class drift; class → sector → asset drift
  shrunk to the parent (K = 20 independent outcomes, crypto 200); β × the market's expanding drift with volatility drag;
  observable carry (Treasury / Baa yields for bond products, uncovered interest parity for spot FX where both policy rates
  exist); the hedge design prior made PIT; a product-specific rule (equity-like β × market, bonds with a yield → carry,
  spot FX and commodities → 0 because no forward / roll data exists, crypto → heavily shrunk group drift, VXX → own
  trailing drift, leveraged / inverse → β × market with their own drag); and the hierarchical PIT positive-return
  frequency. The main prior for challengers (`product`) was fixed in advance.
* Same eras, split and freezing as the weight research; gates fixed in advance (Directional G1: walk-forward Brier better
  than climatology with t ≥ 2 and accuracy above the best naive baseline; G2: ≥ 3 of 4 eras and the split). Leakage tests
  cover the base prior, the climatology and the era fits.
* The hedge design panel shows the research view (alpha, p_up, expected return = prior + alpha edge, horizon risk,
  validated or not) — informational; the hedge math does not use it in this phase.

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

### Signal-level weight research (same data; full report in `SHAFFER_WEIGHT_RESEARCH.md`)

9 challengers × 6 horizons, validated on four unseen eras, 2025–now and the 2018 split (13 minutes, 3 processes).
**No challenger passes G1 at any horizon; production stays, nothing enters the live shadow.**

| Horizon | Production accuracy | Best challenger | Naive baseline | Rank IC prod. → ch. (t) |
|---|---|---|---|---|
| 1D | 51.1% | 51.5% | 52.8% always bullish | 0.038 (7.2) → 0.034 (6.8) |
| 1W | 50.3% | 51.2% | 54.8% positive frequency | 0.034 (5.5) → 0.036 (6.2) |
| 1M | 50.5% | 52.6% | 58.7% positive frequency | 0.028 (2.3) → 0.037 (3.1) |
| 3M | 50.2% | 52.7% | 62.7% positive frequency | 0.016 (0.7) → 0.039 (1.9) |
| 6M | 50.5% | 54.8% | 66.0% always bullish | 0.031 (0.8) → 0.082 (2.3) |
| 12M | 50.7% | 57.7% | 72.1% always bullish | 0.047 (1.0) → 0.063 (1.3) |

* The score is centred on zero with no drift term, so its direction cannot beat "always bullish" at long horizons.
* Bullish scores are informative and grow more reliable with size (1D: 55% → 71% right from +10 to +75; 1M: 59% → 88%);
  bearish scores are right less than half the time at 1M–6M. 83–92% of records are in −10…+10.
* Production ranks assets week to week at 1D–1M (rank IC t 7.2, 5.5, 2.3).
* Gains shrink by half or more out of sample; free signed weights over-fit the most (12M: +22.6 points in sample, −2.3 out).
* Class level is the best depth; deeper specialisation does not help.
* Stable in every era at every horizon: a negative weight on idiosyncratic volatility (production gives it almost none).

### Shaffer Alpha vs Shaffer Directional (same data; full report in `SHAFFER_DIRECTIONAL_RESEARCH.md`)

| Horizon | Base rate | Alpha rank IC (t) | Best directional accuracy | Naive | Excess | Brier skill vs climatology | Shaffer adds to the prior? |
|---|---|---|---|---|---|---|---|
| 1D | 52.8% | 0.041 (7.7) | 52.9% | 52.8% | +0.2 | +0.39% | barely (t 2.6) |
| 1W | 54.5% | 0.038 (6.1) | 55.1% | 54.8% | +0.3 | +0.18% | no (t 1.2) |
| 1M | 58.0% | 0.019 (1.5) | 59.1% | 58.7% | +0.4 | −0.08% | no |
| 3M | 62.3% | −0.008 | 63.1% | 62.7% | +0.4 | −0.76% | no |
| 6M | 65.6% | −0.018 | 65.9% | 66.0% | −0.1 | −0.03% | no |
| 12M | 70.9% | 0.003 | 71.8% | 72.1% | −0.3 | +2.39% | no |

* Alpha: production ranks assets at 1D and 1W; no alpha challenger improves it; the global depth is best.
* Directional: the product-specific prior is the best base prior at every horizon and beats zero drift at 1D–1M; Shaffer
  evidence adds to it only at 1D, barely. Bearish precision at 1M–6M comes from inverse / volatility funds.
* Four 1D directional challengers pass the pre-registered gates and are in the live shadow, but at 1D even a calibrated
  constant beats climatology — the report proposes a stricter pre-registered gate (beat the prior-only model, paired).

### Frozen benchmark for new-information research

New information is judged against a frozen copy of the current system, never against a moving target
(`lab.freeze_benchmark`, `python -m finsim2 lab --freeze-benchmark`). The benchmark holds only the registry's production
versions and their metrics, plus the Directional research definition (labelled as research); research outputs are
referenced by content hash. An id can be frozen once; `lab.verify_benchmark` recomputes the hash.

| | |
|---|---|
| Id | `benchmark-2.1-2026-09-25` |
| Frozen | 2026-09-25 21:16:16 UTC (research store) |
| Content hash (sha256) | `ce0f502bbaba5d5a34b7828df1fa286f4855baeb8767d4610a01a188818cdcdc` |
| Production versions | Shaffer Score `shaffer-2.1` · Shaffer Alpha `shaffer-alpha-2.1-production` · Shaffer Hedge `hedge-2` |
| Directional research definition | prior-only `prior:product`; current `alpha+prior@global` (σ(a + b·μ/σ + c·raw/100)) |
| Gates | Directional v2 (prior-only requirement), Alpha v1, hedge sizing discovery / confirmation / live |
| Source research | weight research 2026-09-25 16:10:37 (sha256 `5c970eeb…b7d065`), Alpha / Directional research 2026-09-25 17:03:52 (sha256 `984c781f…264145`) |
| Data version | `d015500b867192f5` |

Directional gate v2 (declared 2026-09-25, before any new-information result): a challenger must beat, on identical PIT
records, both the prior-only model (paired Brier gain t ≥ 2, lower log loss, higher accuracy) and the current Directional
formulation (paired Brier gain t ≥ 2). It applies to challengers evaluated from now on and is not applied retroactively.
Datasets that start after 2018 (FINRA short-sale volume from 2019) cannot take part in the pre-2018 discovery test; they
are labelled LIMITED HISTORY and evaluated on a separate recent-era / live-shadow track, never as historically verified.

### New information (`engine/newinfo.py`, reports `NEW_INFORMATION_RESEARCH.md`, `NEW_DATA_SOURCES.md`)

The earlier reweighting rounds rejected every challenger, so this phase adds *information* rather than weights. Each family
uses only data absent from the 74 production signals and the earlier candidates. Each is point in time and is tested as
an *increment* on identical records against the frozen benchmark above:

* Alpha: ridge [production score, family] against the excess-return target; paired Δ rank IC against production.
  Market-wide families enter as feature × PIT beta.
* Directional: gate v2 — beat both the prior-only model and the current formulation.
* Hedge: the log realised-volatility forecast against a baseline of 63-day and 21-day realised volatility and VIX.

The protocol is the same as before: eras, the 2018 split, weights frozen before each era, Benjamini-Hochberg at q = 0.10
across all 102 family × horizon × target tests, and features never admitted one by one. ML Lab → *New information* shows
it.

Results (research store, run 2026-09-25): 4 of 102 tests survive the FDR control, and three pass G1, G2 and FDR:

| Test | Effect | t | Eras | Status |
|---|---|---|---|---|
| Richer breadth — Alpha, 1W | Δ rank IC +0.025 (55% of production's +0.045) | 3.5 | 4 / 4, split t 3.4 | live shadow |
| Richer breadth — hedge volatility, 1W | −7.3% squared error vs the baseline | 8.6 | 4 / 4, split t 5.3 | live shadow |
| Earnings events — hedge volatility, 1W | −0.5% squared error vs the baseline | 3.0 | 3 / 4, split t 1.3 | live shadow |

* Directional: nothing passed gate v2 at any horizon, so the prior-only model remains the Directional benchmark.
* The breadth alpha works through the beta interaction. It is a market-condition × beta tilt, not new asset-specific
  information.
* The hedge results are volatility forecasts only. Hedge ratios, sizing and tails were not tested, and the hedge math is
  unchanged.
* Credit quality, term structure / real yields and the earnings-surprise re-test show no incremental value. FX carry has
  insufficient data: 10 assets and 2 eras.
* FINRA short-sale volume (from 2019; *not* short interest) is LIMITED HISTORY. It shows no value in the eras it covers
  and is not recorded live.
* Nine families are BLOCKED by data access: analyst revisions, option surfaces, dated futures curves, CFTC, short
  interest, OAS / CDS history, flows, EIA / crop data and FX forwards.

**Live shadow.** `python -m finsim2 lab --live-models` does three things:

* fits the three passing tests on every matured record;
* registers them as `newinfo-…` versions with status *live shadow*, which is outside the promotion path;
* fits the benchmark's prior-only and current Directional models.

The daily learning job then records, for every tracked asset, into the append-only ledger:

* the alpha score with each feature's contribution;
* each volatility forecast next to the baseline's own forecast;
* the benchmark's Directional p_up.

Rows are graded at maturity. Alpha is graded by cross-sectional rank IC against production on the same dates. Volatility
is graded by squared log error against the baseline on the same rows. A model shows ELIGIBLE FOR PROMOTION only after 60
graded pairs with a live gain, and promotion itself remains a separate, explicit decision.

### Breadth volatility → Shaffer Hedge outcomes (`hedge/volhedge.py`, report `BREADTH_HEDGE_RESEARCH.md`)

A better volatility forecast is not a better hedge. This study replays the complete Shaffer Hedge point in time for
every book × objective × date, from 2009, at 1W, 1M and 3M:

```
PIT data → volatility forecast → covariance → candidates → sizing → optimiser → package → realised P&L
```

It uses three otherwise identical volatility models:

* **production**: hedge-2's 252-day covariance;
* **no-breadth vol**: a log-volatility regression on 63d / 21d realised volatility and VIX;
* **breadth vol**: the same regression plus breadth.

Each hedge is judged on realised utility: U = risk reduction − λ·profit sacrificed − cost, for λ = 0.5–10. The protocol
and gates G1–G5 were committed before any full result. The study adds a production-neutral replay path to
`engine.analyze`, plus leakage tests: two stores identical up to a date give identical decisions on it.

Results (14 books, 17 objectives; 446 / 212 / 70 dates at 1W / 1M / 3M):

* The breadth forecast is better out of sample:
  * 1W: −15% squared log error vs no-breadth, −50% vs production;
  * 1M: −8% vs no-breadth;
  * 3M: not significant (G1 fails).
* Realised hedge utility is not better. 0 of 50 objective × horizon tests survive Benjamini-Hochberg, no cell passes
  G1–G4, and nothing enters live shadow.
* In the cells with positive point estimates, 91% of the summed gain comes from the new volatility architecture and
  9% from breadth. Adding breadth lowered utility in 6 of 9 variance-sensitive cells.
* Hedge sizes barely move (median ratio 1.00). What changes is mainly the product or contract choice, and those
  changes net to about zero.
* The largest point estimate is the 3M crash objective, +$8.4k per $1M. It misses FDR (p 0.0025), comes almost
  entirely from the no-breadth model, and sits at a horizon where the forecast does not validate.

Hedge-2 and its sizing are unchanged. The breadth forecast stays research information.

### Shaffer vNext: three separate research programs (`engine/alphanext.py`, `engine/dirnext.py`, `hedge/hedgenext.py`)

Reports: `SHAFFER_ALPHA_VNEXT.md`, `SHAFFER_DIRECTIONAL_VNEXT.md`, `SHAFFER_HEDGE_VNEXT.md`, and the master table
`SHAFFER_VNEXT_SUMMARY.md`. Run: `python -m finsim2 lab --fetch-sec-extra`, then `python -m finsim2 lab --vnext all`.

The ML Lab tabs are grouped into three areas plus the shared Lab: **Shaffer Alpha** (Shaffer Alpha, Alpha vNext,
Signal weights, Family weights), **Shaffer Directional** (Shaffer Directional, Directional vNext), **Shaffer Hedge**
(Hedge research, Hedge vNext) and **Lab** (production, performance, signals, new information, challengers, live
learning, versions, forecasts).

These rules are shared by all three programs:

* Production is fixed: shaffer-2.1, shaffer-alpha-2.1-production, the Directional research definition, hedge-2, the
  resizing live shadow and benchmark `benchmark-2.1-2026-09-25`.
* Every challenger is versioned (`alpha-3-…`, `directional-3-…`, `hedge-3-…`) and registered in `formula:registry` with
  status `challenger` or `rejected`.
* Each program runs walk-forward over four unseen eras, with the 2018 split and BH FDR. Gates were fixed before the
  full runs.
* Live shadow comes only after the historical gates. Promotion needs the live shadow (at least 60 graded pairs) and
  your approval.

**Alpha.** The question is which assets will beat the others. The program adds new point-in-time information on top of
the production score:

* SEC fundamentals, first reported (`data/sec_extra.py`, research-only dataset `sec_x`): free-cash-flow yield,
  accruals, asset growth, leverage change, gross profitability, operating-margin change, net issuance and buybacks;
* earnings events and surprises;
* sector-relative valuation, growth and momentum;
* credit and term premium × beta;
* breadth × beta.

It fits a hierarchical ridge (global → class → sector) per horizon, from 1D to 12M, and a filings-only variant.
Results are judged cross-sectionally: rank IC, quintile and decile spreads, net-of-cost long-short, hit rate vs the
median, monotonicity, eras, class and sector stability, and conviction buckets.

Result: no challenger passes G1–G4 plus FDR at any horizon.

* Production is useful only at 1W (rank IC 0.038, t 6.1), and its net long-short there is about zero.
* At 1M the global and class challengers are useful in absolute terms:
  * rank IC 0.042 and 0.038, t 2.2;
  * they pass FDR, 3 of 4 eras, positive spreads.

  Their gain over production is not significant (Δ rank IC t 1.1 and 1.0), so the extension past 1W is suggestive, not
  verified.
* Specialisation below global never beats its parent with evidence.
* The filings-only model is worse than production everywhere.
* The bottleneck is point-in-time analyst estimates and revisions.

**Directional.** This program estimates p_up beyond the point-in-time base prior. The base prior, the Shaffer
adjustment and the final p are kept separate. Three logistic challengers (global, compact, class) add short-horizon
information:

* gap, close location and range;
* 1-day and 5-day reversal;
* volume surprise and illiquidity;
* sector-relative returns;
* earnings proximity;
* breadth, dispersion and VIX.

A challenger must beat the prior-only model on Brier, log loss, balanced accuracy and calibration together.

Result: no challenger beats the prior at 1D or 1W.

* The global and class models are overconfident: calibration slope 0.3–0.5, and their Brier gain is positive in 0 of
  4 eras.
* The compact model is roughly neutral (Brier gain t +0.4 at 1D, −1.2 at 1W).
* 1M was not run, per the fixed rule.
* Bearish precision on ordinary equities is 42–50% for every model, which is no better than a coin.

**Hedge.** Five experiments, each against hedge-2 on identical cases and judged on realised utility (λ 0.5–10), with
gates H1–H5 and BH FDR:

* risk estimation: an EWMA covariance, replayed through the full hedge chain;
* sizing: a capped multiple per objective, and per objective × volatility regime;
* product choice: a learned product type per objective × regime, from a forced-product replay;
* Alpha → hedge: hedge size scaled by the validated 1W Alpha.

Result: nothing passes every gate, so nothing enters live shadow.

* 31 cells survive FDR, almost all in sizing. At 1W, and several at 1M, history prefers larger hedges than hedge-2 for
  the single-name, beta, systematic, sector, target-vol and tail objectives: the learned multiple is often the 1.5×
  cap.
  * The gain is ΔU(λ = 1) up to +$4k per $1M, t up to 6.9, positive in 3 of 4 eras, and it holds without the crisis
    years.
  * Every such cell fails the fixed cost and basis-error guard (H4), and the sign reverses at λ ≥ 5–10.
  * So this is a risk-versus-profit preference, not a free improvement.
* The EWMA covariance helps the 3M crash objective (ΔU +$6k, FDR), but it also fails H4.
* The product-choice and Alpha-link tests found nothing.
* The existing resizing challenger is flat to negative from 2018 on.

### Learned Shaffer weights (`engine/learned.py`, `hedge/hedgelearn.py`, report `SHAFFER_LEARNED_WEIGHTS.md`)

The original purpose of the ML Lab: **find the weights history supports, then decide how much to trust them.** Every
one of the 74 production signals gets a weight at every node of Global → Class → Product type → Sector → Industry →
Asset, for every horizon, from the point-in-time research records. The hierarchy is economic, not the wrapper:

* XLK is Equity → sector ETF → Information Technology;
* UST10Y and IEF are both Rates → Treasury → intermediate;
* WTI and USO are both Commodity → Energy → Oil.

**Targets.** Alpha ranks assets. It uses the pairwise ranking least-squares target (RankRLS: the pairwise squared
loss, solved in closed form) on week-demeaned signals. Directional estimates P(up) with the PIT base prior as the
offset, fitted by one Newton (IRLS) step and calibrated only on out-of-sample scores.

**Partial pooling.** Every node's weights are a ridge toward its parent, so a child with little evidence inherits
its parent. The global level has its own weak ridge; K only governs pooling. Five weight sets are compared
walk-forward on identical records:

* A: production;
* B: historical best fit (hardly pooled);
* C: validated deployable. K is chosen nested, every global weight is trusted by its out-of-sample leave-one-out t
  (positive-part James–Stein), and a node keeps its own deviation only if it improved that node's own records out of
  sample (yearly clusters, BH across nodes × signals);
* D: simple global learned weights;
* E: uniform-depth hierarchy, with K and depth chosen nested.

**Other diagnostics:**

* optimisers compared: ridge, elastic net, sign-constrained, RankNet, and residual boosting (as a nonlinear
  diagnostic);
* era stability (six era fits → STABLE / REGIME DEPENDENT / UNSTABLE / NO EVIDENCE);
* regime fits;
* confidence, regime, decay and applicability structure tests;
* conviction buckets.

**Capability test first.** Ten synthetic PIT worlds with known equations were run through the same code:

* linear;
* ranking target;
* sparse;
* correlated;
* regime-dependent;
* sector-specific;
* horizon-specific;
* nonlinear interaction;
* pure noise;
* asset-specific with a short-history asset.

All ten recovered what was planted, and noise was not validated. Building the suite found and fixed three engine
flaws before any market result was read:

* the global and pooling penalties were conflated;
* depth was chosen by the largest gain rather than significance;
* node deviations were measured inconsistently with how they were deployed.

**Results.**

* At 1W the simple global learned weights (D) and the uniform-depth hierarchy (E) beat production under every fixed
  gate, with FDR across 24 tests:
  * rank IC 0.053 and 0.067 vs 0.038 (E corrected 2026-09-26: the first report showed 0.055 because E was scored with the wrong pooling K; the live-shadow model was always the correct one);
  * net long-short +0.15–0.19% a week vs ~0;
  * 3 of 4 eras.

  They are in live shadow: `alpha-learned-1w-global-exp` and `alpha-learned-1w-hierarchy-exp`, recorded daily and
  graded like production. The gain is concentrated after 2017.
* At 1D the learned sets beat production on average (hierarchy t 3.1) but win only 2 of 4 eras.
* From 1M to 12M nothing is significant.
* Directional never beats the prior.
* The trust-filtered set C was too conservative: it drops signals that only help jointly. It is reported as
  designed, not shadowed.
* Learned allocations are very different from production's (share distance 0.8–0.9).
* Few weights are stable across eras.

**Hedge.** The same approach over Global → Risk class → Objective → Product class → Instrument:

* History supports larger hedges than hedge-2 at 1W and 1M. The validated multiple is 1.35× globally, and 1.35–1.5×
  for most risk classes and objectives; minimum-variance stays at ~1.0×.
* At 1W, sizing up both reduced the 95% ES and raised utility.
* All sizing cells fail the cost / basis guard H4, and the gain reverses at λ ≥ 5.
* Product preference (κ, β) validated nothing.

**ML Lab views.** Shaffer Alpha → Learned weights and Shaffer Directional → Learned weights hold *Current learned
Shaffer*: every asset's production score next to the learned ones, where the weights come from in the hierarchy,
reliability, and the per-signal weights. Shaffer Hedge → Learned hedge holds the hedge parameters. Run with
`python -m finsim2 lab --learned`.
