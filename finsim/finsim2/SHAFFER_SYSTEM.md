# The Shaffer System: the permanent definition

This definition was adopted on 2026-09-28 by the owner's decision, and it replaces the −100 … +100 evidence scores as
the user-facing numbers. It also fixes the learning protocol, in advance of any result. The code is in
`engine/system.py` (records, learner, live forecasts) and `engine/instruments.py` (converting an underlying forecast
into an instrument's P&L). The walk-forward results are in `SHAFFER_SYSTEM_RESULTS.md`.

## 1. What the numbers mean

| System | Question | Horizons |
|---|---|---|
| **Shaffer Alpha** | What percentage return should I expect from this asset over this longer horizon? | 1M, 3M, 6M, 12M, 2Y, 3Y, 5Y |
| **Shaffer Directional** | What percentage move should I expect over the next few sessions? | 1D, 3D, 1W |
| **Shaffer Hedge** | Given that forecast, its uncertainty and the trade's risks, how should I protect this trade? | every trade |

**The Shaffer Score is the expected percentage total return over the selected horizon.** +18.4 means an expected
+18.4%; 0.0 means flat; −7.5 means −7.5%. For an ordinary unlevered long asset the scale is −100 ≤ Score < ∞, and
−100 is the total-loss boundary.

### How the lower bound is enforced, and a correction to the proposed formula

Internally the model forecasts the log total return, y = ln(1 + R). A forecast of y has two parts: the conditional
mean μ̂ = E[y | x] and the conditional dispersion ŝ. The proposed display formula was Score = 100(e^μ̂ − 1). That is
the **median** return, not the expected return. The mean of R is higher by a convexity term:

- **Headline (the expected return):** Score = 100(E[e^y] − 1) = 100(e^(μ̂ + ½ŝ²) − 1) in the lognormal case.
  FinSim2 computes E[e^y] from the model's own calibrated residual distribution, which also covers fat tails.
- **Also shown:** the median, 100(e^μ̂ − 1).

The difference matters at long horizons. A stock with 30% volatility has ½ŝ² ≈ 0.225 over 5Y, so a median of +50%
goes with a mean near +88%. The headline follows the stated definition ("expected percentage return").

Both numbers stay above −100% for any μ̂ and ŝ (they tend to −100% only as μ̂ → −∞), and the upside is unbounded.

### What each forecast shows

**Every Alpha forecast shows:**
- the expected return and the median return;
- P(return > 0);
- P(beating the proper benchmark);
- the 50% and 90% forecast ranges;
- model reliability (from its own out-of-sample record);
- the hierarchy node used, and how far it leaned on its parents;
- the equation family and version;
- the major positive and negative contributors.

**Every Directional forecast shows:**
- the expected return and P(up);
- the expected absolute move and the 90% range, from the validated move-size model (SHAFFER_MOVE_SIZE.md);
- the scheduled events behind the forecast.

A 53% P(up) is displayed as what it is. It is not presented as a strong call.

**The proper benchmark:**

| Asset | Benchmark |
|---|---|
| Equities, equity ETFs and equity indices | SPY |
| Treasuries, corporate bonds and fixed-income ETFs | AGG |
| Commodities and commodity ETFs | DBC |
| FX, crypto, currency ETFs and the benchmarks themselves | cash (3-month T-bill, FRED DGS3MO) |
| Leveraged / inverse ETFs | SPY; their own path is forecast from their own history |

### Derivatives and non-standard payoffs

The Shaffer Alpha and Directional forecasts always describe the **underlying economic asset's unlevered percentage
move**. The trade layer (`engine/instruments.py`) converts that into the instrument's expected P&L:

| Instrument | Conversion |
|---|---|
| Futures and forwards | Quantity × multiplier × current price × E[R] |
| Options | The expected payoff under the forecast distribution of the underlying, minus the premium: E[max(S_T − K, 0)] or E[max(K − S_T, 0)] with ln S_T ~ (ln S + μ̂, ŝ²), in closed form |
| Leveraged / inverse ETFs | Forecast as their own assets from their own history (they are long instruments floored at −100%) |
| Series that trade at or below zero (a continuous commodity series in 2020) | Excluded from log-return learning on those dates |

## 2. The equation is learned by asset type and horizon

For every horizon, the model learns along one path:

    Global → Asset class → Product type → Sector → Industry → Asset

For example, NVDA at 6M is Global → EQUITY → US common → Information Technology → Semiconductors & Related Devices → NVDA:

  θ_NVDA,6M = θ_Global,6M + Δθ_Equity + Δθ_Common + Δθ_Technology + Δθ_Semis + Δθ_NVDA

**Every Δ is shrunk toward its parent according to evidence:** ridge toward the parent's coefficients, with the
strength K chosen out of sample. A node with little history inherits its parent's equation almost unchanged. This is
how "every asset gets its own equation" avoids becoming "every asset gets its own overfit equation".

**Product types:**

| Class | Product types |
|---|---|
| Equities | US common, foreign / ADR |
| ETFs | Broad market, international, sector, factor, fixed income, commodity, currency, crypto, leveraged / inverse |
| Treasuries and corporate bonds | Total-return index |
| Commodities | Their commodity group |
| FX | G10, emerging |
| Crypto | Coin |

The industry is the SEC SIC description for stocks.

### What the ML chooses

1. **Which signals matter:** elastic-net selection at the global and asset-class levels.
2. **How much they matter:** the coefficients, pooled down the hierarchy.
3. **Whether interactions matter:** momentum × volatility, valuation × rates (term spread), momentum × VIX, low
   volatility × VIX.
4. **Whether a response is nonlinear:** hinge terms max(0, x − 0.5) and max(0, −x − 0.5) for momentum, valuation
   and mean reversion. A family score is the average of clip(z / 2, −1, 1), so ±0.5 is one standard deviation. "Only matters when extreme" is learnable.
5. **Which equation family works best,** out of sample, per horizon and asset class, from this fixed candidate set:

| Family | Equation (all predict y = ln(1 + R) over the horizon) |
|---|---|
| E0 calibrated prior | a + b · base |
| E1 calibrated production | E0 + c · (production evidence index / 100) |
| E2 elastic-net families | E1 + the 11 family scores + log volatility + VIX, term spread, credit spread |
| E3 limited interactions | E2 + the four interactions |
| E4 threshold | E2 + the six hinge terms |

**The inputs:**
- **base:** the point-in-time expected log return over the horizon. The asset's expanding mean daily log return is
  shrunk toward its asset-class mean with weight n / (n + 2,520 sessions), times the horizon length.
- **The production evidence index:** the existing Shaffer Score at the matching horizon (−100 … +100). 3D uses 1D,
  and 2Y–5Y use 12M. It is still computed, and it is now one input, shown in the details as the "evidence index".
- **The family scores:** the 11 production family scores (Momentum, Trend, Mean Reversion, Valuation, Fundamental
  Quality, Fundamental Growth, Risk-Adjusted Performance, Statistical / Time Series, Relative Value, Low volatility,
  Low beta). Families that don't apply to a class are simply absent.

## 3. The learning protocol (fixed before any result)

**Records.** Every asset with at least 6 years of prices, at calendar-aligned dates:
- every 5 sessions for 1D, 3D and 1W;
- every 21 sessions for 1M and longer.

The target is y = ln(P_{t+h} / P_t) on adjusted (total-return) prices, the benchmark's y over the same window, and
features known at the close of t.

**Walk-forward.**
- Test eras 2009–12, 2013–16, 2017–20, 2021–24 and 2025–. Each era trains only on outcomes that matured before it
  starts, plus the 2018 split.
- Choices are nested inside the training data. Using an inner window of the 3 years before the era, fitted on data
  before that window:
  1. the equation family per (horizon, asset class) by inner mean squared error of y (fallback: the global choice
     when a class has fewer than 500 inner records);
  2. the elastic-net penalty λ ∈ {0.001, 0.01, 0.1} (α = 0.5) at the global and class levels;
  3. the hierarchy depth (1–6) and the shrinkage K ∈ {30, 300, 3,000, 30,000} for the chosen family.

**Out-of-sample metrics,** per horizon and asset class:
- the MSE gain against E0 (the calibrated prior), paired per date, with t adjusted for overlapping windows
  (n_eff = dates × step / h);
- the MSE gain against the current production expected return on identical records, where it exists;
- the calibration slope and intercept of realised y on μ̂ (1 is ideal);
- the cross-sectional rank IC of μ̂ (internal Alpha / stock-selection validation);
- the Brier score of P(R > 0) against the base rate;
- the coverage of the 50% and 90% ranges.

**Adoption rule** (per horizon and asset class, decided by the rule, not by hand):
- The learned equation becomes the production equation if its walk-forward MSE is **not worse than E0**
  (gain ≥ 0) **and** its calibration slope is **> 0**.
- Otherwise the production equation is **E0**, the calibrated prior.
- The ML's selection is used only where it did not lose to the simplest defensible equation out of sample.
- Whether it beats the old production expected return is reported, not required. The old number was an
  asset-by-asset calibration with no pooling.

**Dispersion, ranges and probabilities.**
- ŝ = c · σ63 · √h, with c fitted per (horizon, class) on training residuals. σ63 is the point-in-time 63-session
  volatility; 1D–1W use the move-size model's forecast instead.
- The 50% and 90% ranges, P(R > 0), E[e^y] and P(beat benchmark) all come from the empirical distribution of the
  standardised training residuals (99 quantiles), so the ranges are calibrated and not assumed normal.
- P(beat benchmark) uses the same construction for the asset's and the benchmark's relative log return.

**Reliability** (High / Medium / Low) comes from the out-of-sample record at that horizon and class:
- High: MSE gain t ≥ 2 and calibration slope within 0.5–1.5.
- Medium: gain ≥ 0 and slope > 0.
- Low: otherwise, when the forecast is E0.

**Refit and freeze.**
- The final fit uses every matured record with the choices the nested procedure makes on the final 3-year window.
  It is frozen in `frozen/shaffer_system.json` with a SHA-256 hash and refitted monthly.
- A refit never changes the candidate set, the hierarchy or the adoption rule. Changing those is a new protocol
  version.

**What the numbers can and cannot say.** Twenty-five years of research in this repository found that little beyond
the base rate predicts returns (SHAFFER_ALPHA_HORIZONS.md, NEW_DATA_SIGNALS.md). The expected-return forecasts will
therefore mostly be the calibrated prior plus small adjustments, and their ranges will be wide. That is the honest
state of the evidence, and the display says so through the reliability label. Better inputs (analyst revisions,
options) enter as new families under this same protocol.

## 4. Shaffer Hedge

The hedge receives:
- the trade;
- the Shaffer forecast at the trade's horizon (expected return, ŝ, reliability);
- the volatility and tail risk (the move-size model, stress tests);
- the portfolio's exposures;
- costs.

It reports:
- the principal unwanted risk;
- the recommended technique (no hedge, a partial broad-market, sector, futures, put, spread, rates, FX or commodity
  hedge, or a combination);
- its cost;
- the expected downside reduction;
- the expected profit sacrificed: the hedge's expected P&L under the Shaffer forecasts of both legs.

The sizing math stays hedge-2 (validated in SHAFFER_HEDGE_FINETUNE.md, not beaten in SHAFFER_EVENT_HEDGE.md). The
forecast enters the report and the profit-sacrificed figure, and the hedge keeps optimising the risk / profit trade-off
rather than neutralising everything.

## 5. Revision log

**2026-09-28: corrections found by the first full runs, before the first live spec was frozen.**

**Adoption is unaffected.** Adoption depends only on the walk-forward squared-error gain and the calibration slope
of μ̂, and none of the three corrections below changes μ̂.

1. **The inner window is defined by when outcomes end.** The wording above ("the 3 years before the era") is applied
   to the *end date* of each outcome: the inner window holds the outcomes that matured in the 3 years before the era.
   Selecting by start date made the nested choice impossible at 3Y and 5Y, because a 3-year outcome that starts
   within 3 years of the era cannot mature before it. The rule is now the same for every horizon.

2. **Residual distributions are robust and grouped by product type.**
   - They use a median-absolute-deviation scale, with standardised residuals capped at ±6 robust standard
     deviations.
   - They are estimated per product type, falling back to the asset class and then to the pool. A group with
     30–199 residuals keeps its own scale on its parent's shape.
   - Why: pooling all ETFs let the leveraged and inverse funds give SPY a 5-year "expected" return of 10¹³%.

3. **Long horizons use a blended scale, and the live residuals come from every era.**
   - For 1M and longer, ŝ is c · √(½σ63² + ½σ_history²) · √h: today's volatility is blended with the asset's
     expanding full-history volatility, because volatility mean-reverts. 1D–1W keep σ63, and in the live
     forecasts the validated move-size model.
   - The live residual distribution uses every walk-forward out-of-sample residual of the adopted equation
     (2009 →), not only the last 3 years. The last 3 years alone (a calm, rising market) gave SPY a 12M 90% range
     of +1% to +29%.

4. **Every forecast carries plain-language caveats.**
   - When its horizon had fewer than 10 independent outcomes in the test period, which is true at 2Y–5Y, since
     25 years hold only a handful of independent 5-year windows.
   - When its asset class has fewer than 5 assets (crypto).
