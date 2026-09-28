# FinSim2 / Shaffer System: north star (owner's charter)

Set by the owner on 2026-09-28. It governs every piece of FinSim2 research and engineering. The current FinSim2 /
Shaffer System state is the source of truth. `SHAFFER_SYSTEM.md` is the permanent definition of the forecasts, and
this charter says what they are for.

**The owner's goal statement (2026-09-28, verbatim):**

> Build the Shaffer System into an end-to-end quant investing framework.
> Shaffer Alpha: 1M–5Y expected return. Shaffer Directional: 1D–1W expected return, P(up), and move size.
> Shaffer Hedge: best hedge for every trade based on risk, cost, and profit sacrificed.
> Use point-in-time historical data and ML to learn the best equation and weights by horizon, asset class, sector,
> industry, and asset, with strict out-of-sample validation and shrinkage to avoid overfitting.
> Priority is better data first, then better models.
> Turn the forecasts into a market-neutral long/short portfolio, size positions by validated conviction, hedge unwanted
> risk, include realistic costs, and compare the full strategy against SPY on return, Sharpe, drawdown, beta, and alpha.
> FinSim2 is the paper-trading/display front end; the Shaffer ML/data engine can run separately and feed it.
> Nothing reaches production without proven historical and live evidence plus my approval.

**What follows from it:**

- **Order of work:** data before models.
- **Sizing:** conviction counts only where it has been validated out of sample. Unvalidated signal gets no weight.
- **Promotion gate:** historical evidence (walk-forward, pre-registered, FDR) → a live-shadow period with graded
  forward results → the owner's explicit approval. The ML engine may propose and shadow changes automatically, but it
  can never promote them.

**Main goal:** find the best historically supported equation and weights for each asset and horizon, turn those
forecasts into a robust long/short portfolio, and use the Shaffer Hedge to control risk without destroying expected
profit.

The work is a full end-to-end quant investment process, built for personal research and a future
portfolio-manager presentation.

## 1. Shaffer Alpha

- **Horizons:** 1M, 3M, 6M, 12M, 2Y, 3Y, 5Y.
- **Output:** expected % total return.
- **Learning:** the ML learns the equation and the weights from point-in-time history.
- **Differences:** horizons, asset classes, sectors, industries and assets may use different equations and weights
  when the evidence supports it.
- **Hierarchical shrinkage:** Global → Class → Product Type → Sector → Industry → Asset.

## 2. Shaffer Directional

- **Horizons:** 1D, 3D, 1W.
- **Output:** expected % return, P(up), expected move size and a calibrated range.
- **Learning:** the same principle as Alpha. The equation and weights are learned from history by asset and horizon.
- **No forced edge:** directional edge is not forced where none exists.

## 3. Shaffer Hedge

- It is applied to every trade.
- It determines the proper hedge technique, size, cost, downside reduction and profit sacrificed.
- It uses the best validated volatility, options, event and cross-asset risk information.
- It is judged by realized hedge utility, not by forecast accuracy alone.

## 4. Strategy

- Stocks come first, but the end state covers all major ETD and OTC asset classes.
- The book is a **market-neutral long/short portfolio** (the owner's choice, 2026-09-28).
- **Pipeline:** forecasts → position selection → sizing → portfolio risk → Shaffer Hedge → after-cost returns.
- **Evaluated against SPY on:** total return, Sharpe, max drawdown, beta, factor-adjusted alpha, and after-cost
  performance.
- **Headline yardstick** (owner's choice, 2026-09-28): risk-adjusted leads. A "beat" needs a higher Sharpe than SPY
  AND positive factor-adjusted alpha after costs. Raw total return versus SPY, drawdown and beta are shown alongside.

## 5. Data (the main priority now)

Find and ingest the richest legitimate point-in-time historical data possible:

- equities, including delisted names
- fundamentals
- analyst estimates and revisions
- options chains, implied volatility, skew and open interest
- futures curves by contract
- rates, credit and FX
- crypto derivatives
- positioning and flows
- event data

Do not keep endlessly tuning the same signals. Better data feeds the same ML framework.

## 6. Discipline

- Strict point-in-time history.
- Walk-forward only.
- Tests are pre-registered before any result.
- FDR / multiple-testing control.
- Nothing is promoted without the owner's approval.
- Existing live-shadow experiments stay frozen.
- FinSim2 is the paper-trading and display front end. The Shaffer ML engine can become a separate, linked service.
