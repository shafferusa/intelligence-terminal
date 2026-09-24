# FinSim2 — architecture

FinSim2 is a quantitative evidence engine for portfolio management. It does not simulate trading desks or careers.
It keeps real market history, computes the equation library on it, tests which signals have predicted which
horizons, trains simple machine-learning models walk-forward, and shows the evidence next to the user's
portfolio. The user decides.

Standard library only (Python 3.11). Pure-Python numerics (`finsim.quant` plus `finsim2.engine.models`).

```
Market data (Yahoo daily history, FRED macro, SEC fundamentals)  ->  SQLite research store
    -> FeatureEngine   (point-in-time features per asset per date)
    -> SignalEngine    (expanding z-scores, capped; direction)
    -> HorizonEngine   (forward returns; IC, hit rate, p-value, effective sample, by regime)
    -> RegimeEngine    (point-in-time regime labels)
    -> MLEngine        (walk-forward, purged; leaderboard; importance; ensemble; predictions)
    -> AnalyticsEngine (quant score, ML score, confidence, explanations, equations tab, backtests)
    -> Portfolio       (ledger, analytics, optimisation, Monte Carlo, scenarios)
```

## Package layout

```
finsim2/
  __main__.py            CLI: serve | open | status | stop | phone | refresh | research | audit | hedge-audit
  server.py              HTTP routes (/api/fs2/...), background jobs, static UI
  shaffer_score.py       Shaffer Score v2 configuration: formula, constants, 15 families, priors, A and H tables
  data/
    universe.py          the research universe (UNIVERSE list of dicts), FRED_SERIES, horizons
    store.py             Store: SQLite (prices, macro, fundamentals, kv cache, predictions, model runs, portfolio)
    yahoo.py             daily history (period1=0 .. now), incremental updates
    fred.py              first-release vintages + publication dates (API, FRED_API_KEY); fredgraph.csv fallback
    sec.py               EDGAR companyfacts -> point-in-time fundamentals (by filing date)
    refresh.py           refresh(store, progress) -> downloads everything that is stale
  engine/
    align.py             common business-day calendar; aligned price / macro series
    features.py          FeatureEngine
    signals.py           standardisation, direction, families
    horizons.py          targets, IC / hit rate / p-values / effective sample size, signal-horizon matrix
    regimes.py           regime labels
    models.py            fast pure-Python models (linear family, logistic, binned trees, RF, GBM)
    ml.py                datasets, walk-forward, leaderboard, importance, ensemble, prediction
    scores.py            quant score, ML score, confidence, explanation, "what matters"
    equations.py         the equations tab: every catalogue equation evaluated on an asset
    backtest.py          signal backtests (execution lag, net-of-cost trades)
    portfolio.py         ledger + analytics
    optimize.py          frontier, min-variance, max-Sharpe, risk parity
    montecarlo.py        block bootstrap / GBM simulations
    scenario.py          factor-shock scenarios, historical analogues
    tracking.py          prediction log, realised scoring, model decay, correlation decay
    shaffer.py           Shaffer v2: the one point-in-time sweep (compute_shaffer_score), attribution, priors
    audit.py             universe replay of Shaffer and ML -> SHAFFER_AUDIT.md
  hedge/                 Shaffer Hedge (see SHAFFER_HEDGE.md): market, risk, products, pricing, series, engine,
                         history, scoring, service, audit
    research.py          orchestration: per-asset research bundle, universe run, caching
  static/                the UI
```

## Conventions (every module relies on these)

**Calendar.** All series are aligned to one business-day calendar: the dates on which SPY traded (from the store),
extended with weekdays for dates after SPY's last bar. A series is forward-filled onto the calendar at most 5
business days (then missing). Crypto weekend moves roll into Monday. A non-positive price (WTI on 2020-04-20) has no log
return and is treated as missing by every analytic; the store and the ledger keep the real quote.

**Prices.** `adj_close` (split- and dividend-adjusted) drives returns; `close` is the quote shown to the user.
Missing values are `None`, never NaN, in anything returned by an API.

**Horizons** (trading days on the calendar):
`HORIZONS = [("1D",1),("1W",5),("1M",21),("3M",63),("6M",126),("12M",252),("3Y",756),("5Y",1260),("10Y",2520)]`.

**Targets.** `fwd_h[t] = ln(P[t+h] / P[t])` using adj_close; `None` when t+h is beyond the data.

**Point in time.** A feature at date t uses data dated <= t only. Macro: with `FRED_API_KEY` the revised
(non-daily) series - CPIAUCSL, UNRATE, INDPRO, M2SL, NFCI, WALCL - are stored as first-release values (FRED API,
`output_type=4`) with the date each was first published, and a value is visible from the first calendar date on or
after that date (US data is released before the close); later revisions never reach the past. FRED returns first
releases only from each series' vintage coverage (UNRATE 1960, CPI 1972, M2 1980, NFCI and WALCL 2011), so
history starts there. Without a key (or for daily market series, which are not revised in practice) the latest
vintage is stored and shifted by the fixed `lag_days`; NFCI, re-estimated weekly, is then not used at all (`nfci`
and the liquidity regime are None). Transforms (year on year, Sahm gap, 26-week growth) are shown only once every
observation they read is visible. The research bundle's `data_notes` says which case applies. Fundamentals are
keyed by SEC filing date. Standardisation uses expanding (or trailing) statistics only.
Model training for a prediction at t only uses rows whose target window ended before t (row date + h < t).

**Effective sample size.** Overlapping h-day targets and slow-moving signals are not independent:
`n_eff = n / max(h, persistence)`, where persistence is the signal's decorrelation length (from its lag-21
autocorrelation). p-values (Student t), Benjamini–Hochberg q-values across signals and confidence all use n_eff,
not n. `usefulness = IC · min(1, |t| / 2)`.

**Point in time.** `evaluate(..., cutoff=c)` uses only rows whose target window closed before `c`, and ranks
within those rows only (ranks over the whole history would carry later data in). The quant score's history refits
its weights every 63 sessions this way; its out-of-sample IC feeds the confidence and the backtester. The
full-sample signal record below (its `direction`, IC, usefulness) describes the whole history: it is evidence for
today, not something a backtest may use on past dates.

**Backtests (engine/backtest.py).** Three rules keep them free of look-ahead. (1) *Execution lag*: a signal observed
at the close of day t is traded `lag` sessions later (default 1), so it first earns the close t+1 -> t+2 return;
`lag = 0` (trade at the close that produced the signal) is kept for comparison and labelled optimistic. (2)
*Point-in-time direction*: a feature is pointed the way its history said at each date by
`horizons.expanding_direction`, the sign of the running correlation with the h-day forward return over rows
i + h < t only (every max(1, h//4) sessions; 0 until 60 such rows and while |t| < 1 on n_eff = n/h), never by the
full-sample sign; the user's "invert" still applies on top. The quant score history and the ML forecasts are
already signed forecasts. (3) *Expanding ML standardisation*: each walk-forward ML forecast is z-scored with the
mean/SD of the forecasts made before it only (`research.expanding_standardize`, at least 20). Daily and per-trade
returns are net of costs; metrics include Calmar (CAGR / |max drawdown|), `buy_hold_*` (the same asset held over the
same window; `benchmark_*` are the old names) and alpha/beta against SPY.

**Signal record** (horizon engine output, one per signal per horizon):
```
{"signal": "mom_12_1", "family": "Momentum", "horizon": "3M", "h": 63,
 "ic": 0.12, "ic_recent": 0.05, "hit_rate": 0.58, "t": 2.1, "p": 0.04, "n": 6100, "n_eff": 96,
 "usefulness": 0.35, "direction": 1, "by_regime": {"bull": {"ic":..,"n_eff":..}, ...}}
```
`usefulness` in [-1, 1] = IC shrunk by evidence: `ic * min(1, |t| / 2)`.

**Standardised analytic output** (equations tab, analytics tabs):
```
{"id": "46", "name": "GARCH(1,1)", "family": "Volatility", "formula": "<latex>", "variables": {"omega":..},
 "value": 0.214, "display": "21.4%", "fmt": "pct", "percentile": 0.78, "direction": "Increasing",
 "signal": "Slightly Bearish" | "Bullish" | ... | "Neutral" | null,
 "usefulness": 0.62 | null, "confidence": 0.72 | null, "best_horizon": "1W" | null, "hit_rate": 0.61 | null,
 "ic": 0.18 | null, "n": 5000, "n_eff": 1000, "regime": "High volatility",
 "interpretation": "Elevated near-term volatility", "history": {"dates": [...], "values": [...]},
 "applies": true, "note": "..."}
```

## Shaffer Score v2 (engine/shaffer.py)

`ShafferRun(research, asset).run(until)` is one forward sweep over the calendar.
- An observation dated t (z-scores at t, the vol-scaled forward return t→t+h) joins the running sums at t + h + 1.
- At the first session of each month, the evidence per signal is recomputed from those sums: PS, stability,
  confidence, regime ratios, decay, direction, BH q-values and the family correlation matrix.
- Scores are computed weekly and on the last session by `score_at`, the only scoring routine.
- Matured scores feed the family validation V and the isotonic calibration from the next refit.
- `compute_shaffer_score(asset, h, as_of)` is `run(until=as_of)`'s last record. The live score is the same call on
  the latest session.

Hierarchy:
- A full run stores the per-signal evidence sums at each January in kv `shaffer_cp:{asset}:{VERSION}`
  (horizons ≤ 12M).
- `load_priors` sums every *other* asset's checkpoints by class and globally.
- At a refit in year Y the asset uses the latest checkpoint year ≤ Y. The class IC is shrunk toward the global
  (N0 = 50), and the asset PS toward the class (N0 = 20).

The research bundle carries the result of `shaffer_full` (cached in kv by data version). `research.shaffer_series`
gives the point-in-time score history used by backtests and as an ML baseline.

## ML v2 (engine/ml.py)

`train_horizon`:
- Runs purged walk-forward on the first 85% of rows (development). Ensemble weights are the mean positive fold IC.
- Scores the last 15% (holdout) once, with models trained on development rows that matured before it.
- Compares every baseline with the ensemble on the same rows.

The verified-edge rule has four conditions:
- t ≥ 2 on n_eff;
- the ensemble's IC beats each non-constant baseline by 0.01;
- holdout IC > 0;
- block-permutation p < 0.05.

If any condition fails, the score is 0 and the message is `NO VERIFIED ML EDGE: <reasons>`.

The direction, volatility and drawdown models are fitted separately (`_simple_wf`) against, respectively:
- the expanding base rate (Brier);
- current and EWMA volatility (RMSE);
- the expanding drawdown frequency (Brier).

Final models go to kv `mlmodels:{asset}`. `forecast_today` uses them. `server.App.daily_learning` runs after each
full refresh; it forecasts every day and retrains monthly.

`scores.agreement` compares Shaffer with ML, but only when the ML edge is verified. The combined score
α·SS + (1 − α)·ML is evaluated only in the audit: α is chosen on the first half of the common out-of-sample period
and tested on the second. It is never shown as a primary score.

## Ledger (engine/portfolio.py)

One replay (`Ledger._replay`) values every position type on every date and validates every change:
- **Spot:** BUY/SELL long; SHORT/COVER short, with the proceeds held as collateral, a daily general-collateral borrow fee
  and dividends debited.
- **Futures and forwards** (`FUT:<root>:<code>`, `FWD:<pair>:<date>`): value = quantity × contract size × (model price −
  average entry); P&L is realised on reduction and at expiry.
- **Options** (`OPT:<und>:<C|P>:<strike>:<expiry>`): bought only, marked to model, settled at intrinsic value.

Model marks come from `hedge/series.py`, with the same functions as the live quote. Buying power = cash − 150% × shorts
− futures/forward margin, and it must stay ≥ 0 after every trade. `trade_package` validates several legs together and
stores them with `Store.add_transactions` in one SQLite transaction. `analytics()` measures risk on economic exposure:
- futures notional → tracking ETF;
- Treasury futures → IEF-duration dollars;
- FX → currency trusts;
- options → delta-dollars.

## Store schema (SQLite, ~/.finsim2/research.db, WAL)

```
assets(id TEXT PRIMARY KEY, name, asset_class, sector, country, currency, yahoo, cik, duration REAL,
       convexity REAL, fred TEXT, meta TEXT)                    -- meta: JSON
prices(asset_id, date, open, high, low, close, adj_close, volume, PRIMARY KEY(asset_id, date))
macro(series, date, value, published, PRIMARY KEY(series, date))  -- date = observation date; published = first
                                                                  -- publication (first release) or NULL (lag on read)
                                                                  -- kv macro_kind:{series} = first_release | latest_vintage
fundamentals(asset_id, concept, period_end, filed, value, form, fp, PRIMARY KEY(asset_id, concept, period_end, filed))
kv(key PRIMARY KEY, value TEXT, updated_at)                     -- JSON caches of computed analytics
fetch_log(source, key, fetched_at, status, note)
predictions(id INTEGER PRIMARY KEY, asset_id, horizon, model, model_version, made_on, target_date, predicted,
            error_band, confidence, score, realized, error, scored_on, detail TEXT)
model_runs(id INTEGER PRIMARY KEY, level, key, horizon, model, version, train_start, train_end, test_start,
           test_end, features TEXT, params TEXT, metrics TEXT, created_at)
backtests(id INTEGER PRIMARY KEY, spec TEXT, result TEXT, created_at)
portfolios(id TEXT PRIMARY KEY, name, base_currency, created)
transactions(id INTEGER PRIMARY KEY, portfolio_id, date, kind, asset_id, quantity, price, fee, currency, note, basis_date,
             created_at, voided_at, package_id)        -- kind: DEPOSIT WITHDRAW BUY SELL SHORT COVER; asset_id may be FUT:/OPT:/FWD:
hedge_recommendations(...)                            -- append-only Shaffer Hedge proposals, graded once (see SHAFFER_HEDGE.md §7)
snapshots(portfolio_id, date, nav, detail TEXT, PRIMARY KEY(portfolio_id, date))
watchlist(asset_id PRIMARY KEY, added, note)
```

## Asset classes

`EQUITY, ETF, INDEX, TREASURY, CORP_BOND, COMMODITY, FUTURE, FX, CRYPTO` (bond ETFs are `ETF` with `duration`).
A Treasury asset (`TREASURY`) is a constant-maturity total-return index built from the FRED yield
(`r_t = y_{t-1}/252 - D*dy + 0.5*C*dy^2`), so the bond equations apply to it.

## Features (engine/features.py) — names are part of the contract

Every feature is a list aligned to the calendar (`None` where not computable). Families in brackets.

- [Returns] `ret_1d ret_1w ret_1m ret_3m ret_6m ret_12m` (log returns over 1/5/21/63/126/252 days), `excess_3m` (vs SPY)
- [Momentum] `mom_12_1` (252-day return skipping the last 21), `rel_strength_6m` (6M return minus SPY's), `ma_cross` (MA50/MA200 − 1)
- [Statistics] `z_20 z_50` (price vs its 20/50-day mean in std units), `dist_ma200` (ln P − ln MA200), `skew_60 kurt_60`, `pctile_252` (position in the 252-day range, 0..1)
- [Risk] `vol_20 vol_60 downside_vol_60` (annualised), `sharpe_252 sortino_252`, `drawdown_252` (≤ 0), `beta_252 corr_252 alpha_252 idio_vol_252` (vs SPY)
- [Volatility] `ewma_vol garch_vol` (annualised, GARCH refitted yearly on the trailing 3 years), `vol_ratio` (vol_20 / vol over 252), `vol_of_vol` (std of vol_20 over 126 days), `vol_pctile` (vol_20 percentile over 3 years)
- [Technical] `rsi_14`, `macd` ((EMA12 − EMA26) / price), `bb_pctb` (Bollinger %b, 20, 2σ), `volume_z` (log volume vs 60-day)
- [TimeSeries] `ar1_63` (AR(1) coefficient of daily returns over 63 days), `acf1_252`, `half_life` (OU half-life of log price over 252 days, days), `adf_t` (ADF t-stat of log price over 252 days, month-end, carried forward)
- [Valuation] `value_5y` (−z of ln price vs its 5-year mean), and for stocks with fundamentals `earnings_yield`, `pe_rel_5y` (z of P/E vs its own 5 years), `eps_growth_yoy`, `rev_growth_yoy`, `net_margin`
- [Rates] `y10`, `d_y10_3m`, `slope_10y3m`, `d_slope_3m`, `real_y10`, `breakeven_10y`, `rate_beta_252` (asset return beta to Δy10)
- [Credit] `credit_spread` (BAA − 10Y), `d_credit_3m`
- [Macro] `vix`, `d_vix_1m`, `dollar_mom_3m`, `oil_mom_3m`, `gold_mom_3m`, `cpi_yoy`, `unemp_gap` (Sahm-style: 3-month average unemployment minus its 12-month low), `nfci`, `fed_bs_growth` (26-week), `dollar_beta_252`

Yields and spreads are decimals (0.042), returns are log returns, volatilities annualised decimals.
