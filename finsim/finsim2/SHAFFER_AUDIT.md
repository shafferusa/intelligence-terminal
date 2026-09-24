# Shaffer Score v2 and ML v2: the audit

Generated 2026-09-24 22:21:46 from the real research store (prices up to the latest close). Universe: 133 assets with at least six years of daily history; ML: 21 representative assets. Run time 15 minutes. Every number below is out of sample: each score was computed on its date with only the information available then, and compared with what happened afterwards.

## 1. The exact formula

```
SS_raw(a,h,t) = 100 · tanh( Σ_f W_f,a,h,t · A_f,a · H_f,h · FamilyScore_f,a,h,t / K_a,h )
FamilyScore_f = Σ_{i∈f} ω_i · s_i · c_i · r_i · d_i          ω = correlation-penalised weights, Σω = 1
K_a,h = 0.10 · Σ_f A_f,a · H_f,h over the families with data
SS_cal = 100 · tanh( g(SS_raw) / 0.25 ),  g = isotonic map from raw score to forward return (vol units), out of sample only
```

## 2–3. Families and every signal (prior direction: + bullish when high, − bearish when high, 0 learned from evidence)

**Momentum** — `ret_3m` (+) log return over 63 sessions; `ret_6m` (+) log return over 126 sessions; `ret_12m` (+) log return over 252 sessions; `mom_12_1` (+) return from 12 months ago to 1 month ago; `ret_1m` (0) log return over 21 sessions

**Trend** — `ma_cross` (+) 50-day average over 200-day average, minus 1; `dist_ma200` (+) log price minus log 200-day average; `macd` (+) (12-day EMA − 26-day EMA) / price; `pctile_252` (+) 0 = at the 1-year low, 1 = at the high; `trend_quality` (+) 6-month return / volatility × R² of the 6-month log-price trend

**Mean Reversion** — `ret_1d` (−) log return over the last session; `ret_1w` (−) log return over 5 sessions; `z_20` (−) price versus its 20-day mean, in standard deviations; `z_50` (−) price versus its 50-day mean, in standard deviations; `rsi_14` (−) Wilder relative strength index; `bb_pctb` (−) position within the 20-day ±2σ band; `mr_opportunity` (+) −20-day z-score / (1 + half-life / 21 days)

**Valuation** — `value_5y` (+) minus the z-score of log price versus its 5-year mean; `earnings_yield` (+) trailing 12-month EPS / price; `pe_rel_5y` (−) z-score of P/E versus its 5-year history; `book_to_price` (+) stockholders' equity / (shares outstanding × price); `sales_yield` (+) trailing 12-month revenue / (shares outstanding × price)

**Fundamental Quality** — `net_margin` (+) trailing 12-month net income / revenue; `roe` (+) trailing 12-month net income / latest stockholders' equity; `fund_quality` (+) return on equity + net margin

**Fundamental Growth** — `eps_growth_yoy` (+) trailing 12-month EPS growth; `rev_growth_yoy` (+) trailing 12-month revenue growth

**Risk-Adjusted Performance** — `sharpe_252` (+) excess return over the 3-month bill per unit of volatility; `sortino_252` (+) excess return per unit of downside volatility; `alpha_252` (+) annualised intercept of the market regression; `ram` (+) 12-1 momentum / 60-day volatility

**Volatility** — `vol_20` (0) annualised standard deviation of daily returns; `vol_60` (0) annualised standard deviation of daily returns; `downside_vol_60` (0) annualised root mean square of negative returns; `ewma_vol` (0) RiskMetrics λ = 0.94, annualised; `garch_vol` (0) next-day forecast, parameters refitted yearly on 3 years; `vol_ratio` (0) 20-day volatility over 1-year volatility; `vol_of_vol` (0) standard deviation of 20-day volatility over 6 months; `vol_pctile` (0) 20-day volatility's percentile over 3 years

**Statistical / Time Series** — `skew_60` (−) skewness of daily returns; `kurt_60` (0) fat-tailedness of daily returns; `ar1_63` (0) first-order autoregressive coefficient of daily returns; `acf1_252` (0) autocorrelation of daily returns; `half_life` (0) Ornstein-Uhlenbeck half-life of log price over 1 year, days; `adf_t` (0) augmented Dickey-Fuller t on log price over 1 year (month-end); `idio_vol_252` (−) volatility not explained by the market; `drawdown_252` (0) price over its 1-year high, minus 1

**Rates** — `y10` (0) FRED DGS10; `d_y10_3m` (0) change in the 10-year yield over 63 sessions; `slope_10y3m` (0) FRED T10Y3M; `d_slope_3m` (0) change in 10Y − 3M over 63 sessions; `real_y10` (0) FRED DFII10 (TIPS); `breakeven_10y` (0) FRED T5YIE; `rate_duration` (+) −modified duration × 3-month yield change (rate beta × change without a duration)

**Credit** — `credit_spread` (0) FRED BAA10Y; `d_credit_3m` (0) change over 63 sessions; `credit_signal` (0) spread level minus twice its 3-month change

**Macro** — `cpi_yoy` (0) as published (45-day lag); `unemp_gap` (0) 3-month average unemployment minus its 12-month low; `oil_mom_3m` (0) log change of WTI; `gold_mom_3m` (0) log change of gold

**Liquidity** — `nfci` (0) Chicago Fed; above zero = tighter than average; `fed_bs_growth` (0) liquidity; `vix` (0) implied volatility of the S&P 500; `d_vix_1m` (0) change over 21 sessions; `volume_z` (0) log volume versus its 60-day average

**Cross-Asset** — `dollar_mom_3m` (0) log change of the dollar index; `beta_252` (0) slope of daily returns on the S&P 500 ETF; `corr_252` (0) correlation of daily returns; `rate_beta_252` (0) beta of daily returns to daily 10-year yield changes; `dollar_beta_252` (0) beta of daily returns to the dollar index

**Relative Value** — `excess_3m` (+) 3-month log return minus the S&P 500 ETF's; `rel_strength_6m` (+) 6-month return minus SPY's; `rel_value` (+) 5-year price value minus the S&P 500's

## 4. Standardisation

Each signal's z-score uses the mean and standard deviation of its values strictly before t (expanding, after 252 observations), capped at ±3. s = δ · clip(z / 2, −1, 1), δ = the direction the signal is used in.

## 5. Predictive weight

w = |PS| × (¼ + ¾ · stability). PS (predictive strength, correlation units) is the median of: the Pearson IC of the z-score with the vol-scaled forward return; the IC implied by the directional hit rate (ρ = sin(π(hit − ½))); and the IC implied by the conditional-return spread (E[y | z > ½] − E[y | z < −½] ÷ 2.28σ). Stability = share of the three thirds of the matured history whose IC has the same sign. Hierarchical evidence: PS is shrunk toward the asset-class IC (itself shrunk toward the global IC) with weight 20 ÷ (n_eff + 20), from other assets' January checkpoints known at the time.

## 6. Confidence

c = min(1, √(n_eff / 100)) × |IC| / (|IC| + 1.96·SE) × (1 − ½·q) × data quality; SE = 1/√(n_eff − 3); q = Benjamini–Hochberg across all signals at that horizon; data quality = share of the last year with a value. n_eff = rows × step ÷ max(h, persistence), persistence = −21 / ln ρ₂₁ of the signal.

## 7. Regime adjustment

r = clip(1 + mean over regime dimensions of λ·(ratio − 1), 0.5, 1.5); ratio = δ·IC in today's state ÷ |PS| (clipped 0..2); λ = n_eff_state ÷ (n_eff_state + 50). Dimensions: market, volatility, rates, inflation, growth, dollar, liquidity. Up to 12M only.

## 8. Decay

trend = 0.6·(δ·IC₃ᵧ ÷ |IC|) + 0.4·(δ·IC₁ᵧ ÷ |IC|); HEALTHY ≥ 0.6, WEAKENING ≥ 0.2, else DECAYING; INSUFFICIENT DATA below 10 independent 3-year observations. d = 1 − λ(1 − clip(½ + ½·trend, 0.1, 1)), λ = n_eff₃ᵧ ÷ (n_eff₃ᵧ + 30): gradual, never flips a sign.

## 9. Correlated signals

Within a family, ω_i = (w_i ÷ Σ_j ρ²_ij) normalised, ρ_ij the correlation of the two signals' z-scores up to the refit (the sum includes ρ_ii = 1). Two identical signals therefore count as one; families (not signals) are then summed.

## 10–11. Asset and horizon applicability

| Family | EQUITY | ETF | INDEX | TREASURY | CORP_BOND | COMMODITY | FX | CRYPTO |
|---|---|---|---|---|---|---|---|---|
| Momentum | 1.00 | 1.00 | 1.00 | 0.35 | 0.35 | 1.00 | 1.00 | 1.00 |
| Trend | 1.00 | 1.00 | 1.00 | 0.35 | 0.35 | 1.00 | 1.00 | 1.00 |
| Mean Reversion | 1.00 | 1.00 | 1.00 | 0.35 | 0.35 | 0.35 | 0.35 | 1.00 |
| Valuation | 1.00 | 0.35 | 0.35 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 |
| Fundamental Quality | 1.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 |
| Fundamental Growth | 1.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 |
| Risk-Adjusted Performance | 1.00 | 1.00 | 1.00 | 0.35 | 0.35 | 0.35 | 0.35 | 0.35 |
| Volatility | 1.00 | 1.00 | 1.00 | 1.00 | 0.35 | 1.00 | 1.00 | 1.00 |
| Statistical / Time Series | 0.35 | 0.35 | 0.35 | 0.35 | 0.35 | 0.35 | 0.35 | 1.00 |
| Rates | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 0.35 | 1.00 | 0.35 |
| Credit | 1.00 | 1.00 | 1.00 | 0.35 | 1.00 | 0.35 | 0.35 | 0.35 |
| Macro | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 |
| Liquidity | 0.35 | 0.35 | 0.35 | 1.00 | 1.00 | 0.35 | 0.35 | 1.00 |
| Cross-Asset | 0.35 | 0.35 | 0.35 | 0.35 | 0.35 | 1.00 | 1.00 | 1.00 |
| Relative Value | 1.00 | 1.00 | 0.35 | 0.35 | 0.35 | 0.35 | 0.35 | 0.35 |

| Family | 1D | 1W | 1M | 3M | 6M | 12M | 3Y | 5Y | 10Y |
|---|---|---|---|---|---|---|---|---|---|
| Momentum | 0.3 | 0.5 | 0.9 | 1.0 | 1.0 | 1.0 | 0.5 | 0.3 | 0.2 |
| Trend | 0.5 | 0.7 | 1.0 | 1.0 | 1.0 | 0.8 | 0.4 | 0.3 | 0.2 |
| Mean Reversion | 1.0 | 1.0 | 0.8 | 0.5 | 0.3 | 0.2 | 0.2 | 0.2 | 0.2 |
| Valuation | 0.1 | 0.1 | 0.3 | 0.5 | 0.7 | 1.0 | 1.0 | 1.0 | 1.0 |
| Fundamental Quality | 0.1 | 0.2 | 0.4 | 0.7 | 0.9 | 1.0 | 1.0 | 1.0 | 1.0 |
| Fundamental Growth | 0.1 | 0.2 | 0.5 | 0.8 | 1.0 | 1.0 | 0.8 | 0.6 | 0.5 |
| Risk-Adjusted Performance | 0.3 | 0.5 | 0.8 | 1.0 | 1.0 | 1.0 | 0.6 | 0.4 | 0.3 |
| Volatility | 1.0 | 1.0 | 1.0 | 0.8 | 0.6 | 0.5 | 0.3 | 0.2 | 0.2 |
| Statistical / Time Series | 1.0 | 1.0 | 0.8 | 0.6 | 0.5 | 0.4 | 0.3 | 0.2 | 0.2 |
| Rates | 0.4 | 0.5 | 0.8 | 1.0 | 1.0 | 1.0 | 0.9 | 0.8 | 0.7 |
| Credit | 0.4 | 0.5 | 0.8 | 1.0 | 1.0 | 1.0 | 0.8 | 0.6 | 0.5 |
| Macro | 0.2 | 0.3 | 0.6 | 0.9 | 1.0 | 1.0 | 1.0 | 0.9 | 0.8 |
| Liquidity | 0.5 | 0.7 | 0.9 | 1.0 | 1.0 | 0.9 | 0.6 | 0.4 | 0.3 |
| Cross-Asset | 0.5 | 0.7 | 0.9 | 1.0 | 1.0 | 0.9 | 0.6 | 0.4 | 0.3 |
| Relative Value | 0.3 | 0.5 | 0.8 | 1.0 | 1.0 | 1.0 | 0.8 | 0.6 | 0.5 |

H is an economic prior; the evidence-based family weight W (evidence × out-of-sample validation V) decides the rest.

## 12. How the historical scores are generated

One forward sweep over the calendar per asset. An observation dated t (signal z-scores at t, forward return t→t+h) enters the evidence only at t + h + 1. Evidence is refitted at the first session of each month; scores are computed weekly and on the last session by the one scoring routine, with that month's evidence, the signals on the day and the regime on the day. Matured scores feed the family validation multipliers and the calibration from the next refit. `compute_shaffer_score(asset, horizon, as_of)` runs the sweep to `as_of`; the live score is the same call on the latest session (tests prove a past date scored directly equals its record, and that later prices do not change it). Macro data are FRED first releases on their publication dates; fundamentals are dated by SEC filing; splits are applied to per-share data.

## 13. History per asset

| Asset | Class | Prices since | Years | First 3M score | 3M out-of-sample record (years) |
|---|---|---|---|---|---|
| AAPL | EQUITY | 1993-01-29 | 33.6 | 2001-09-05 | +24.7 |
| ABBV | EQUITY | 2013-01-02 | 13.7 | 2016-11-01 | +9.6 |
| ADBE | EQUITY | 1993-01-29 | 33.6 | 2001-09-05 | +24.7 |
| AGG | ETF | 2003-09-29 | 23.0 | 2007-09-06 | +18.8 |
| AMD | EQUITY | 1993-01-29 | 33.6 | 2002-03-05 | +24.3 |
| AMZN | EQUITY | 1997-05-15 | 29.3 | 2001-12-05 | +24.5 |
| AUDUSD | FX | 2006-05-16 | 20.3 | 2010-10-06 | +15.7 |
| AVGO | EQUITY | 2009-08-06 | 17.1 | 2013-06-03 | +13.0 |
| BA | EQUITY | 1993-01-29 | 33.6 | 2001-09-05 | +24.7 |
| BAC | EQUITY | 1993-01-29 | 33.6 | 2001-09-05 | +24.7 |
| BND | ETF | 2007-04-10 | 19.4 | 2011-08-05 | +14.8 |
| BNDX | ETF | 2013-06-04 | 13.3 | 2017-08-04 | +8.8 |
| BRENT | COMMODITY | 2007-07-30 | 19.1 | 2012-08-06 | +13.8 |
| BRK-B | EQUITY | 1996-05-09 | 30.3 | 2002-10-07 | +23.7 |
| BTC | CRYPTO | 2014-09-17 | 12.0 | 2021-12-02 | +4.5 |
| CAT | EQUITY | 1993-01-29 | 33.6 | 2001-09-05 | +24.7 |
| COFFEE | COMMODITY | 2000-01-03 | 26.7 | 2003-12-05 | +22.5 |
| COPPER | COMMODITY | 2000-08-30 | 26.0 | 2004-07-01 | +21.9 |
| CORN | COMMODITY | 2000-07-17 | 26.1 | 2004-06-07 | +22.0 |
| CORP_BAA | CORP_BOND | 1993-01-29 | 33.6 | 2001-09-05 | +24.7 |
| COST | EQUITY | 1993-01-29 | 33.6 | 2001-09-05 | +24.7 |
| CRM | EQUITY | 2004-06-23 | 22.2 | 2008-05-01 | +18.1 |
| CSCO | EQUITY | 1993-01-29 | 33.6 | 2002-05-01 | +24.1 |
| CVX | EQUITY | 1993-01-29 | 33.6 | 2001-09-05 | +24.7 |
| DAX | INDEX | 1993-01-29 | 33.6 | 2002-09-09 | +23.8 |
| DBC | ETF | 2006-02-06 | 20.6 | 2014-08-01 | +11.9 |
| DIA | ETF | 1998-01-20 | 28.6 | 2001-12-05 | +24.5 |
| DIS | EQUITY | 1993-01-29 | 33.6 | 2001-09-05 | +24.7 |
| DJI | INDEX | 1993-01-29 | 33.6 | 2001-09-05 | +24.7 |
| DXY | FX | 1993-01-29 | 33.6 | 2001-09-05 | +24.7 |
| EEM | ETF | 2003-04-14 | 23.4 | 2007-04-05 | +19.2 |
| EFA | ETF | 2001-08-27 | 25.0 | 2006-08-07 | +19.8 |
| EMB | ETF | 2007-12-19 | 18.7 | 2011-11-02 | +14.6 |
| ETH | CRYPTO | 2017-11-09 | 8.8 | 2025-10-07 | +0.7 |
| EURUSD | FX | 2003-12-01 | 22.8 | 2010-10-01 | +15.7 |
| EWG | ETF | 1996-03-18 | 30.5 | 2002-01-04 | +24.4 |
| EWJ | ETF | 1996-03-18 | 30.5 | 2003-03-03 | +23.3 |
| EWU | ETF | 1996-03-18 | 30.5 | 2001-09-05 | +24.7 |
| EWZ | ETF | 2000-07-14 | 26.1 | 2006-07-06 | +19.9 |
| FTSE | INDEX | 1993-01-29 | 33.6 | 2001-09-05 | +24.7 |
| FXI | ETF | 2004-10-08 | 21.9 | 2011-02-02 | +15.3 |
| GBPUSD | FX | 2003-12-01 | 22.8 | 2011-11-02 | +14.6 |
| GLD | ETF | 2004-11-18 | 21.8 | 2008-09-08 | +17.8 |
| GOLD | COMMODITY | 2000-08-30 | 26.0 | 2004-07-01 | +21.9 |
| GOOGL | EQUITY | 2004-08-19 | 22.1 | 2008-07-07 | +17.9 |
| GS | EQUITY | 1999-05-04 | 27.3 | 2003-03-03 | +23.3 |
| HD | EQUITY | 1993-01-29 | 33.6 | 2001-09-05 | +24.7 |
| HSI | INDEX | 1993-01-29 | 33.6 | 2001-09-05 | +24.7 |
| HYG | ETF | 2007-04-11 | 19.4 | 2013-01-07 | +13.4 |
| IBM | EQUITY | 1993-01-29 | 33.6 | 2001-09-05 | +24.7 |
| IEF | ETF | 2002-07-30 | 24.1 | 2006-06-07 | +20.0 |
| IEI | ETF | 2007-01-11 | 19.7 | 2011-02-07 | +15.3 |
| INDA | ETF | 2012-02-03 | 14.6 | 2018-04-05 | +8.2 |
| INTC | EQUITY | 1993-01-29 | 33.6 | 2002-04-03 | +24.2 |
| IWD | ETF | 2000-05-26 | 26.3 | 2005-08-02 | +20.9 |
| IWF | ETF | 2000-05-26 | 26.3 | 2006-01-09 | +20.4 |
| IWM | ETF | 2000-05-26 | 26.3 | 2006-01-09 | +20.4 |
| JNJ | EQUITY | 1993-01-29 | 33.6 | 2001-09-05 | +24.7 |
| JNK | ETF | 2007-12-04 | 18.8 | 2010-12-03 | +15.5 |
| JPM | EQUITY | 1993-01-29 | 33.6 | 2001-09-05 | +24.7 |
| KO | EQUITY | 1993-01-29 | 33.6 | 2001-09-05 | +24.7 |
| KRE | ETF | 2006-06-22 | 20.2 | 2010-08-04 | +15.9 |
| LLY | EQUITY | 1993-01-29 | 33.6 | 2001-09-05 | +24.7 |
| LQD | ETF | 2002-07-30 | 24.1 | 2006-06-07 | +20.0 |
| MA | EQUITY | 2006-05-25 | 20.3 | 2012-11-01 | +13.6 |
| MCD | EQUITY | 1993-01-29 | 33.6 | 2001-09-05 | +24.7 |
| META | EQUITY | 2012-05-18 | 14.3 | 2016-03-02 | +10.3 |
| MRK | EQUITY | 1993-01-29 | 33.6 | 2001-09-05 | +24.7 |
| MSFT | EQUITY | 1993-01-29 | 33.6 | 2001-09-05 | +24.7 |
| MTUM | ETF | 2013-04-18 | 13.4 | 2017-03-03 | +9.3 |
| MUB | ETF | 2007-09-10 | 19.0 | 2012-04-03 | +14.2 |
| N225 | INDEX | 1993-01-29 | 33.6 | 2001-09-05 | +24.7 |
| NATGAS | COMMODITY | 2000-08-30 | 26.0 | 2004-07-01 | +21.9 |
| NDX | INDEX | 1993-01-29 | 33.6 | 2001-09-05 | +24.7 |
| NFLX | EQUITY | 2002-05-23 | 24.3 | 2009-07-06 | +16.9 |
| NKE | EQUITY | 1993-01-29 | 33.6 | 2001-09-05 | +24.7 |
| NVDA | EQUITY | 1999-01-22 | 27.6 | 2008-07-03 | +17.9 |
| NZDUSD | FX | 2003-12-01 | 22.8 | 2011-10-05 | +14.7 |
| ORCL | EQUITY | 1993-01-29 | 33.6 | 2001-09-05 | +24.7 |
| PEP | EQUITY | 1993-01-29 | 33.6 | 2001-09-05 | +24.7 |
| PFE | EQUITY | 1993-01-29 | 33.6 | 2001-09-05 | +24.7 |
| PG | EQUITY | 1993-01-29 | 33.6 | 2001-09-05 | +24.7 |
| PLATINUM | COMMODITY | 1997-10-29 | 28.8 | 2004-01-14 | +22.1 |
| QCOM | EQUITY | 1993-01-29 | 33.6 | 2001-09-05 | +24.7 |
| QQQ | ETF | 1999-03-10 | 27.5 | 2003-01-06 | +23.4 |
| QUAL | ETF | 2013-07-18 | 13.2 | 2017-06-02 | +9.0 |
| RUT | INDEX | 1993-01-29 | 33.6 | 2001-09-05 | +24.7 |
| SHY | ETF | 2002-07-30 | 24.1 | 2006-06-07 | +20.0 |
| SILVER | COMMODITY | 2000-08-30 | 26.0 | 2004-07-01 | +21.9 |
| SLV | ETF | 2006-04-28 | 20.4 | 2011-12-02 | +14.5 |
| SMH | ETF | 2000-06-05 | 26.3 | 2007-01-08 | +19.4 |
| SOL | CRYPTO | 2020-04-13 | 6.4 | — | — |
| SOYBEANS | COMMODITY | 2000-09-15 | 26.0 | 2004-08-02 | +21.8 |
| SPX | INDEX | 1993-01-29 | 33.6 | 2001-09-05 | +24.7 |
| SPY | ETF | 1993-01-29 | 33.6 | 2001-09-05 | +24.7 |
| SX5E | INDEX | 2007-03-30 | 19.5 | 2013-10-04 | +12.7 |
| T | EQUITY | 1993-01-29 | 33.6 | 2001-09-05 | +24.7 |
| TIP | ETF | 2003-12-05 | 22.8 | 2007-11-06 | +18.6 |
| TLT | ETF | 2002-07-30 | 24.1 | 2007-08-02 | +18.8 |
| TSLA | EQUITY | 2010-06-29 | 16.2 | 2016-11-04 | +9.6 |
| TXN | EQUITY | 1993-01-29 | 33.6 | 2001-09-05 | +24.7 |
| UNH | EQUITY | 1993-01-29 | 33.6 | 2001-09-05 | +24.7 |
| USDCAD | FX | 2003-09-17 | 23.0 | 2007-07-06 | +18.9 |
| USDCHF | FX | 2003-09-17 | 23.0 | 2010-06-07 | +16.0 |
| USDCNY | FX | 2001-06-25 | 25.2 | 2006-09-06 | +19.7 |
| USDINR | FX | 2003-12-01 | 22.8 | 2012-03-07 | +14.2 |
| USDJPY | FX | 1996-10-30 | 29.8 | 2005-10-05 | +20.7 |
| USDMXN | FX | 2003-12-01 | 22.8 | 2007-10-03 | +18.7 |
| USMV | ETF | 2011-10-20 | 14.9 | 2015-09-01 | +10.8 |
| USO | ETF | 2006-04-10 | 20.4 | 2012-10-05 | +13.7 |
| UST10Y | TREASURY | 1993-01-29 | 33.6 | 2001-09-05 | +24.7 |
| UST2Y | TREASURY | 1993-01-29 | 33.6 | 2001-09-05 | +24.7 |
| UST30Y | TREASURY | 1993-01-29 | 33.6 | 2001-09-05 | +24.7 |
| UST5Y | TREASURY | 1993-01-29 | 33.6 | 2001-09-05 | +24.7 |
| V | EQUITY | 2008-03-19 | 18.5 | 2013-09-05 | +12.8 |
| VCIT | ETF | 2009-11-23 | 16.8 | 2015-12-02 | +10.5 |
| VTI | ETF | 2001-06-15 | 25.2 | 2005-07-05 | +20.9 |
| VZ | EQUITY | 1993-01-29 | 33.6 | 2001-09-05 | +24.7 |
| WHEAT | COMMODITY | 2000-07-17 | 26.1 | 2004-06-07 | +22.0 |
| WMT | EQUITY | 1993-01-29 | 33.6 | 2001-09-05 | +24.7 |
| WTI | COMMODITY | 2000-08-23 | 26.0 | 2004-07-01 | +21.9 |
| XLB | ETF | 1998-12-22 | 27.7 | 2003-01-02 | +23.4 |
| XLC | ETF | 2018-06-19 | 8.2 | 2025-08-05 | — |
| XLE | ETF | 1998-12-22 | 27.7 | 2003-01-02 | +23.4 |
| XLF | ETF | 1998-12-22 | 27.7 | 2003-01-02 | +23.4 |
| XLI | ETF | 1998-12-22 | 27.7 | 2003-01-02 | +23.4 |
| XLK | ETF | 1998-12-22 | 27.7 | 2003-01-02 | +23.4 |
| XLP | ETF | 1998-12-22 | 27.7 | 2003-01-02 | +23.4 |
| XLRE | ETF | 2015-10-08 | 10.9 | 2019-11-05 | +6.6 |
| XLU | ETF | 1998-12-22 | 27.7 | 2007-03-07 | +19.3 |
| XLV | ETF | 1998-12-22 | 27.7 | 2002-11-04 | +23.6 |
| XLY | ETF | 1998-12-22 | 27.7 | 2003-01-02 | +23.4 |
| XOM | EQUITY | 1993-01-29 | 33.6 | 2001-09-05 | +24.7 |

## 14. Effective sample by horizon

| Horizon | Assets scored | Median independent observations per asset | Total |
|---|---|---|---|
| 1D | 132 | 1156 | 135752 |
| 1W | 132 | 1147 | 135600 |
| 1M | 132 | 272 | 31915 |
| 3M | 131 | 87 | 10322 |
| 6M | 130 | 38 | 4790 |
| 12M | 123 | 14 | 1726 |
| 3Y | 0 | — | — |
| 5Y | 0 | — | — |
| 10Y | 0 | — | — |

## 15–16. Calibration: what each score band was followed by (all assets pooled)

**1D**

| Raw score | Scores | Indep. obs. | Mean return | Median | Up share | Volatility (log) | 95% CI of the mean |
|---|---|---|---|---|---|---|---|
| -100..-60 | 224 | 224 | -0.3% | -0.1% | 42% | 0.014 | -0.5% … -0.1% |
| -60..-40 | 951 | 951 | -0.0% | -0.0% | 49% | 0.013 | -0.1% … 0.1% |
| -40..-20 | 3741 | 3741 | -0.0% | 0.0% | 49% | 0.015 | -0.1% … 0.0% |
| -20..-5 | 14980 | 14980 | 0.0% | 0.0% | 50% | 0.015 | -0.0% … 0.0% |
| -5..5 | 96913 | 96913 | 0.0% | 0.0% | 52% | 0.016 | 0.0% … 0.0% |
| 5..20 | 13894 | 13894 | 0.1% | 0.1% | 54% | 0.018 | 0.0% … 0.1% |
| 20..40 | 3644 | 3644 | 0.1% | 0.0% | 53% | 0.017 | 0.0% … 0.2% |
| 40..60 | 1017 | 1017 | 0.1% | 0.1% | 55% | 0.017 | 0.0% … 0.2% |
| 60..100 | 388 | 388 | 0.4% | 0.1% | 56% | 0.029 | 0.1% … 0.7% |

**1W**

| Raw score | Scores | Indep. obs. | Mean return | Median | Up share | Volatility (log) | 95% CI of the mean |
|---|---|---|---|---|---|---|---|
| -100..-60 | 96 | 96 | -0.2% | 0.1% | 52% | 0.051 | -1.2% … 0.8% |
| -60..-40 | 310 | 310 | 0.4% | 0.1% | 54% | 0.041 | -0.1% … 0.9% |
| -40..-20 | 2008 | 2008 | 0.0% | 0.1% | 52% | 0.030 | -0.1% … 0.1% |
| -20..-5 | 18173 | 18173 | 0.1% | 0.1% | 54% | 0.033 | 0.0% … 0.1% |
| -5..5 | 96044 | 96044 | 0.2% | 0.2% | 54% | 0.036 | 0.1% … 0.2% |
| 5..20 | 15897 | 15897 | 0.3% | 0.3% | 56% | 0.039 | 0.2% … 0.3% |
| 20..40 | 2505 | 2505 | 0.4% | 0.3% | 57% | 0.040 | 0.2% … 0.5% |
| 40..60 | 444 | 444 | 0.5% | 0.4% | 61% | 0.037 | 0.1% … 0.8% |
| 60..100 | 123 | 123 | 0.1% | 0.4% | 57% | 0.048 | -0.7% … 1.0% |

**1M**

| Raw score | Scores | Indep. obs. | Mean return | Median | Up share | Volatility (log) | 95% CI of the mean |
|---|---|---|---|---|---|---|---|
| -100..-60 | 125 | 30 | 0.1% | 0.0% | 51% | 0.021 | -0.6% … 0.8% |
| -60..-40 | 232 | 55 | -0.0% | -0.0% | 50% | 0.074 | -1.9% … 1.9% |
| -40..-20 | 1329 | 316 | 0.6% | 0.1% | 57% | 0.083 | -0.3% … 1.5% |
| -20..-5 | 13881 | 3305 | 0.5% | 0.5% | 57% | 0.084 | 0.2% … 0.8% |
| -5..5 | 104080 | 24781 | 0.7% | 0.8% | 58% | 0.068 | 0.6% … 0.8% |
| 5..20 | 13106 | 3120 | 0.7% | 0.7% | 58% | 0.077 | 0.4% … 0.9% |
| 20..40 | 1003 | 239 | 0.5% | 0.4% | 62% | 0.069 | -0.3% … 1.4% |
| 40..60 | 199 | 47 | 0.4% | 0.2% | 59% | 0.051 | -1.0% … 1.9% |
| 60..100 | 86 | 20 | 0.3% | 0.3% | 56% | 0.017 | -0.4% … 1.1% |

**3M**

| Raw score | Scores | Indep. obs. | Mean return | Median | Up share | Volatility (log) | 95% CI of the mean |
|---|---|---|---|---|---|---|---|
| -100..-60 | 19 | 2 | 0.3% | 0.4% | 63% | 0.012 | -1.5% … 2.2% |
| -60..-40 | 99 | 8 | 0.9% | 0.1% | 52% | 0.051 | -2.6% … 4.6% |
| -40..-20 | 1362 | 108 | 2.8% | 0.7% | 64% | 0.121 | 0.5% … 5.1% |
| -20..-5 | 15561 | 1235 | 2.3% | 2.1% | 62% | 0.140 | 1.5% … 3.1% |
| -5..5 | 97089 | 7705 | 2.0% | 2.0% | 62% | 0.119 | 1.7% … 2.3% |
| 5..20 | 15292 | 1214 | 2.4% | 1.9% | 62% | 0.118 | 1.8% … 3.1% |
| 20..40 | 592 | 47 | 0.3% | 0.4% | 59% | 0.103 | -2.6% … 3.3% |
| 40..60 | 61 | 5 | -0.0% | 0.3% | 59% | 0.055 | -4.8% … 5.0% |
| 60..100 | 4 | 0 | 0.4% | 0.6% | 100% | 0.002 | -0.1% … 0.9% |

**6M**

| Raw score | Scores | Indep. obs. | Mean return | Median | Up share | Volatility (log) | 95% CI of the mean |
|---|---|---|---|---|---|---|---|
| -100..-60 | 0 | | | | | | |
| -60..-40 | 153 | 6 | 8.3% | 6.3% | 78% | 0.200 | -7.7% … 27.0% |
| -40..-20 | 1935 | 77 | 4.9% | 2.9% | 63% | 0.238 | -0.6% … 10.6% |
| -20..-5 | 15871 | 630 | 4.4% | 4.6% | 67% | 0.190 | 2.8% … 5.9% |
| -5..5 | 84517 | 3354 | 4.0% | 3.6% | 64% | 0.173 | 3.4% … 4.6% |
| 5..20 | 16937 | 672 | 4.6% | 4.6% | 69% | 0.133 | 3.5% … 5.6% |
| 20..40 | 1218 | 48 | 2.8% | 2.0% | 63% | 0.101 | -0.1% … 5.7% |
| 40..60 | 78 | 3 | 3.5% | 2.7% | 65% | 0.073 | -4.6% … 12.2% |
| 60..100 | 0 | | | | | | |

**12M**

| Raw score | Scores | Indep. obs. | Mean return | Median | Up share | Volatility (log) | 95% CI of the mean |
|---|---|---|---|---|---|---|---|
| -100..-60 | 0 | | | | | | |
| -60..-40 | 50 | 1 | 18.3% | 16.6% | 100% | 0.087 | -0.2% … 40.3% |
| -40..-20 | 829 | 16 | 13.0% | 14.4% | 75% | 0.279 | -1.3% … 29.3% |
| -20..-5 | 10623 | 211 | 11.6% | 11.8% | 76% | 0.261 | 7.8% … 15.6% |
| -5..5 | 61386 | 1218 | 8.6% | 7.4% | 69% | 0.231 | 7.2% … 10.0% |
| 5..20 | 13250 | 263 | 10.1% | 9.3% | 73% | 0.203 | 7.5% … 12.9% |
| 20..40 | 791 | 16 | 7.2% | 8.4% | 70% | 0.163 | -1.1% … 16.2% |
| 40..60 | 67 | 1 | -1.9% | -4.0% | 36% | 0.103 | -17.6% … 16.9% |
| 60..100 | 1 | 0 | 14.0% | 14.0% | 100% | 0.000 | 14.0% … 14.0% |

## 17–18. Out-of-sample IC and hit rate by horizon

| Horizon | Assets | IC (weighted by n_eff) | Median IC | Share of assets with IC > 0 | Combined t (Stouffer) | Hit rate (|score| ≥ 5) |
|---|---|---|---|---|---|---|
| 1D | 132 | +0.005 | +0.002 | 51% | +1.9 | 52.0% |
| 1W | 132 | +0.011 | +0.008 | 60% | +3.8 | 51.0% |
| 1M | 132 | +0.003 | +0.005 | 52% | +0.5 | 50.3% |
| 3M | 131 | -0.006 | -0.011 | 44% | -0.6 | 49.4% |
| 6M | 130 | +0.005 | +0.013 | 55% | +0.4 | 51.2% |
| 12M | 123 | -0.016 | -0.024 | 44% | -0.6 | 51.0% |
| 3Y | 0 | | | | | |
| 5Y | 0 | | | | | |
| 10Y | 0 | | | | | |

## 19. By asset class

**1W**: COMMODITY IC -0.005 (11 assets, 45% positive, hit 52%); CORP_BOND IC +0.088 (1 assets, 100% positive, hit 52%); CRYPTO IC +0.014 (2 assets, 100% positive, hit 46%); EQUITY IC +0.011 (45 assets, 60% positive, hit 51%); ETF IC +0.015 (49 assets, 67% positive, hit 50%); FX IC -0.012 (11 assets, 18% positive, hit 50%); INDEX IC +0.005 (9 assets, 67% positive, hit 48%); TREASURY IC +0.041 (4 assets, 75% positive, hit 48%)

**1M**: COMMODITY IC +0.002 (11 assets, 55% positive, hit 50%); CORP_BOND IC +0.051 (1 assets, 100% positive, hit 55%); CRYPTO IC +0.078 (2 assets, 100% positive, hit 33%); EQUITY IC +0.005 (45 assets, 53% positive, hit 48%); ETF IC -0.002 (49 assets, 53% positive, hit 49%); FX IC -0.027 (11 assets, 18% positive, hit 45%); INDEX IC +0.009 (9 assets, 56% positive, hit 49%); TREASURY IC +0.051 (4 assets, 75% positive, hit 48%)

**3M**: COMMODITY IC -0.021 (11 assets, 45% positive, hit 44%); CORP_BOND IC -0.070 (1 assets, 0% positive, hit 35%); CRYPTO IC +0.119 (2 assets, 50% positive, hit 63%); EQUITY IC -0.003 (45 assets, 47% positive, hit 47%); ETF IC -0.001 (48 assets, 44% positive, hit 45%); FX IC -0.056 (11 assets, 36% positive, hit 43%); INDEX IC -0.002 (9 assets, 56% positive, hit 49%); TREASURY IC +0.051 (4 assets, 25% positive, hit 44%)

**6M**: COMMODITY IC -0.010 (11 assets, 45% positive, hit 47%); CORP_BOND IC -0.079 (1 assets, 0% positive, hit 41%); CRYPTO IC +0.034 (1 assets, 100% positive, hit —); EQUITY IC -0.012 (45 assets, 47% positive, hit 43%); ETF IC +0.038 (48 assets, 71% positive, hit 49%); FX IC -0.047 (11 assets, 36% positive, hit 43%); INDEX IC +0.051 (9 assets, 56% positive, hit 50%); TREASURY IC -0.013 (4 assets, 25% positive, hit 30%)

**12M**: COMMODITY IC -0.023 (11 assets, 45% positive, hit 51%); CORP_BOND IC -0.083 (1 assets, 0% positive, hit —); EQUITY IC -0.037 (43 assets, 42% positive, hit 46%); ETF IC -0.021 (44 assets, 43% positive, hit 47%); FX IC +0.004 (11 assets, 45% positive, hit 46%); INDEX IC +0.044 (9 assets, 56% positive, hit 49%); TREASURY IC +0.072 (4 assets, 50% positive, hit 40%)

## 20. By regime

**1W**: bear +0.005; bull +0.012; expansion +0.011; falling rates +0.008; high inflation +0.010; high vol +0.019; liquidity expansion +0.009; low inflation +0.009; low vol -0.000; recession +0.006; rising rates +0.010; strong dollar +0.007; weak dollar +0.012

**1M**: bear -0.002; bull -0.004; expansion -0.004; falling rates -0.001; high inflation -0.014; high vol +0.004; liquidity expansion -0.002; low inflation -0.001; low vol -0.011; recession +0.010; rising rates +0.005; strong dollar -0.002; weak dollar -0.005

**3M**: bear -0.014; bull -0.028; expansion -0.023; falling rates -0.005; high inflation +0.012; high vol -0.000; liquidity expansion -0.029; low inflation -0.036; low vol -0.037; recession -0.003; rising rates -0.012; strong dollar -0.020; weak dollar -0.007

**6M**: bear -0.042; bull -0.027; expansion -0.019; falling rates +0.016; high inflation +0.026; high vol -0.017; liquidity expansion -0.026; low inflation -0.042; low vol -0.021; recession +0.016; rising rates +0.002; strong dollar -0.018; weak dollar +0.019

**12M**: bear -0.052; bull -0.051; expansion -0.031; falling rates -0.005; high inflation -0.053; high vol -0.065; liquidity expansion -0.058; low inflation -0.028; low vol -0.045; recession +0.060; rising rates -0.030; strong dollar -0.054; weak dollar +0.023

## 21–22. Which families add independent information, and which look useless

Own IC = the family score's out-of-sample IC; incremental IC = its partial correlation with the forward return after removing the rest of the score. Pooled across assets (weighted by independent observations; t combined by Stouffer).

**1W**

| Family | Assets | Own IC | t | Incremental IC | t | Verdict |
|---|---|---|---|---|---|---|
| Mean Reversion | 130 | +0.009 | +3.3 | +0.010 | +3.5 | adds independent information |
| Risk-Adjusted Performance | 132 | -0.000 | +0.2 | +0.000 | +0.3 | no measurable value |
| Volatility | 14 | +0.002 | +0.2 | +0.001 | -0.0 | no measurable value |
| Relative Value | 131 | -0.001 | -0.4 | -0.001 | -0.4 | no measurable value |
| Liquidity | 28 | -0.003 | -0.6 | -0.004 | -0.9 | no measurable value |
| Momentum | 132 | -0.002 | -0.7 | -0.004 | -1.4 | weak |
| Fundamental Growth | 45 | -0.011 | -1.9 | -0.010 | -1.8 | weak |
| Trend | 131 | -0.006 | -1.9 | -0.006 | -1.9 | weak |
| Fundamental Quality | 45 | -0.011 | -2.1 | -0.010 | -1.9 | weak |
| Valuation | 95 | -0.008 | -2.8 | -0.009 | -3.1 | negative record |
| Statistical / Time Series | 132 | -0.011 | -4.0 | -0.012 | -4.2 | negative record |
| Rates | 96 | -0.015 | -4.5 | -0.019 | -5.6 | negative record |

**1M**

| Family | Assets | Own IC | t | Incremental IC | t | Verdict |
|---|---|---|---|---|---|---|
| Mean Reversion | 131 | +0.005 | +0.5 | +0.003 | +0.1 | no measurable value |
| Cross-Asset | 1 | -0.005 | -0.1 | -0.003 | -0.0 | no measurable value |
| Risk-Adjusted Performance | 132 | -0.004 | -0.5 | -0.001 | -0.1 | no measurable value |
| Liquidity | 14 | -0.003 | -0.1 | -0.004 | -0.2 | no measurable value |
| Relative Value | 131 | -0.004 | -0.8 | -0.005 | -0.9 | no measurable value |
| Momentum | 132 | -0.005 | -0.8 | -0.005 | -1.1 | weak |
| Trend | 132 | -0.009 | -1.6 | -0.008 | -1.3 | weak |
| Fundamental Quality | 44 | -0.015 | -1.4 | -0.015 | -1.4 | weak |
| Volatility | 33 | -0.020 | -1.8 | -0.018 | -1.6 | weak |
| Statistical / Time Series | 132 | -0.016 | -2.7 | -0.017 | -2.9 | negative record |
| Fundamental Growth | 45 | -0.040 | -3.3 | -0.039 | -3.3 | negative record |
| Macro | 2 | -0.159 | -3.9 | -0.158 | -3.9 | negative record |
| Valuation | 93 | -0.030 | -4.8 | -0.030 | -4.8 | negative record |
| Rates | 97 | -0.032 | -4.8 | -0.034 | -5.1 | negative record |

**3M**

| Family | Assets | Own IC | t | Incremental IC | t | Verdict |
|---|---|---|---|---|---|---|
| Mean Reversion | 126 | +0.016 | +1.4 | +0.015 | +1.4 | some evidence |
| Trend | 131 | -0.003 | -0.2 | +0.007 | +0.8 | no measurable value |
| Liquidity | 2 | +0.014 | +0.2 | +0.037 | +0.5 | no measurable value |
| Cross-Asset | 12 | -0.010 | -0.1 | -0.007 | -0.0 | no measurable value |
| Macro | 11 | -0.006 | -0.1 | -0.006 | -0.1 | no measurable value |
| Credit | 2 | -0.018 | -0.2 | -0.014 | -0.2 | no measurable value |
| Volatility | 19 | -0.034 | -1.3 | -0.034 | -1.3 | weak |
| Relative Value | 129 | -0.016 | -1.6 | -0.016 | -1.6 | weak |
| Momentum | 131 | -0.019 | -2.0 | -0.014 | -1.6 | weak |
| Statistical / Time Series | 131 | -0.018 | -1.7 | -0.019 | -1.7 | weak |
| Fundamental Quality | 39 | -0.040 | -1.8 | -0.041 | -1.9 | weak |
| Fundamental Growth | 44 | -0.047 | -2.2 | -0.043 | -2.1 | negative record |
| Risk-Adjusted Performance | 129 | -0.027 | -2.9 | -0.025 | -2.7 | negative record |
| Valuation | 86 | -0.051 | -4.5 | -0.051 | -4.4 | negative record |
| Rates | 98 | -0.053 | -4.6 | -0.059 | -5.1 | negative record |

**6M**

| Family | Assets | Own IC | t | Incremental IC | t | Verdict |
|---|---|---|---|---|---|---|
| Trend | 129 | +0.018 | +1.2 | +0.028 | +1.9 | some evidence |
| Momentum | 127 | +0.022 | +1.4 | +0.027 | +1.7 | some evidence |
| Cross-Asset | 1 | +0.191 | +1.2 | +0.164 | +1.0 | weak |
| Mean Reversion | 123 | -0.002 | -0.3 | +0.002 | -0.0 | no measurable value |
| Volatility | 4 | -0.018 | -0.2 | -0.014 | -0.2 | no measurable value |
| Risk-Adjusted Performance | 125 | +0.002 | -0.1 | -0.005 | -0.5 | no measurable value |
| Liquidity | 4 | -0.069 | -1.0 | -0.095 | -1.3 | weak |
| Credit | 3 | -0.131 | -1.6 | -0.140 | -1.7 | weak |
| Statistical / Time Series | 130 | -0.023 | -1.6 | -0.026 | -1.8 | weak |
| Fundamental Quality | 35 | -0.061 | -1.8 | -0.059 | -1.8 | weak |
| Relative Value | 127 | -0.035 | -2.3 | -0.035 | -2.3 | negative record |
| Fundamental Growth | 38 | -0.078 | -2.4 | -0.074 | -2.3 | negative record |
| Rates | 82 | -0.061 | -3.2 | -0.063 | -3.3 | negative record |
| Valuation | 70 | -0.085 | -4.3 | -0.082 | -4.1 | negative record |

**12M**

| Family | Assets | Own IC | t | Incremental IC | t | Verdict |
|---|---|---|---|---|---|---|
| Trend | 123 | +0.020 | +0.7 | +0.065 | +2.5 | adds independent information |
| Momentum | 121 | -0.005 | -0.2 | +0.014 | +0.6 | no measurable value |
| Mean Reversion | 114 | +0.003 | +0.0 | +0.004 | +0.1 | no measurable value |
| Statistical / Time Series | 123 | -0.022 | -0.9 | -0.022 | -0.9 | no measurable value |
| Risk-Adjusted Performance | 117 | -0.022 | -1.1 | -0.021 | -1.0 | weak |
| Valuation | 67 | -0.042 | -1.3 | -0.046 | -1.4 | weak |
| Rates | 58 | -0.060 | -1.6 | -0.061 | -1.6 | weak |
| Fundamental Quality | 33 | -0.079 | -1.8 | -0.075 | -1.7 | weak |
| Relative Value | 119 | -0.042 | -1.6 | -0.058 | -2.2 | negative record |
| Fundamental Growth | 39 | -0.148 | -3.2 | -0.152 | -3.3 | negative record |

## 23. Decaying signals (at the latest refit)

**1W**: `ret_3m` decaying in 65 assets, weakening in 2, healthy in 1; `macd` decaying in 52 assets, weakening in 5, healthy in 3; `excess_3m` decaying in 47 assets, weakening in 13, healthy in 17; `rate_duration` decaying in 33 assets, weakening in 8, healthy in 11; `ret_1d` decaying in 29 assets, weakening in 12, healthy in 72; `skew_60` decaying in 27 assets, weakening in 14, healthy in 26; `mr_opportunity` decaying in 23 assets, weakening in 11, healthy in 50; `ret_1w` decaying in 17 assets, weakening in 11, healthy in 80; `rsi_14` decaying in 16 assets, weakening in 5, healthy in 66; `z_20` decaying in 13 assets, weakening in 8, healthy in 70

Signal status across assets at 1W: muted: no reliable direction: 5020; active: 2541; muted: evidence against the prior: 1421; active: direction from evidence: 15; reversed by evidence: 5

**1M**: `ret_3m` decaying in 69 assets, weakening in 7, healthy in 3; `excess_3m` decaying in 66 assets, weakening in 6, healthy in 9; `macd` decaying in 52 assets, weakening in 10, healthy in 2; `ret_1d` decaying in 50 assets, weakening in 16, healthy in 31; `skew_60` decaying in 35 assets, weakening in 7, healthy in 31; `rate_duration` decaying in 33 assets, weakening in 4, healthy in 13; `mr_opportunity` decaying in 29 assets, weakening in 7, healthy in 37; `ret_1w` decaying in 26 assets, weakening in 14, healthy in 48; `z_20` decaying in 21 assets, weakening in 5, healthy in 58; `bb_pctb` decaying in 21 assets, weakening in 5, healthy in 58

Signal status across assets at 1M: muted: no reliable direction: 5020; active: 2489; muted: evidence against the prior: 1472; active: direction from evidence: 15; reversed by evidence: 6

**3M**: `ret_3m` decaying in 75 assets, weakening in 5, healthy in 11; `macd` decaying in 75 assets, weakening in 9, healthy in 2; `excess_3m` decaying in 46 assets, weakening in 22, healthy in 8; `rate_duration` decaying in 40 assets, weakening in 6, healthy in 11; `ret_1d` decaying in 33 assets, weakening in 4, healthy in 49; `skew_60` decaying in 33 assets, weakening in 16, healthy in 35; `mr_opportunity` decaying in 16 assets, weakening in 10, healthy in 57; `ret_1w` decaying in 15 assets, weakening in 14, healthy in 62; `pctile_252` decaying in 10 assets, weakening in 1, healthy in 1; `z_20` decaying in 10 assets, weakening in 6, healthy in 60

Signal status across assets at 3M: muted: no reliable direction: 5009; active: 2400; muted: evidence against the prior: 1554; active: direction from evidence: 26; insufficient: 11; reversed by evidence: 2

## 24. ML against each baseline

Cases = assets where both had out-of-sample forecasts on the same rows; wins = ensemble rank IC above the baseline's by more than 0.01. A constant forecast (zero) has no rank IC, so the zero and historical-mean baselines are also judged by squared error: R² vs mean > 0 means the ensemble's out-of-sample errors were smaller than the expanding historical mean's.

**1D** — 21 assets; verified ML edge: EFA, EURUSD, USDJPY; mean ensemble IC +0.021; direction model verified in 0; volatility model verified in 0/0; drawdown model verified in 0/0; R² vs mean > 0 in 3/21 (median -0.029)

| Baseline | Cases | Ensemble wins | Mean baseline IC | Mean ensemble IC (same rows) |
|---|---|---|---|---|
| historical mean | 21 | 16 | -0.043 | +0.021 |
| mean reversion | 21 | 6 | +0.041 | +0.021 |
| momentum | 21 | 10 | +0.010 | +0.021 |
| previous return | 21 | 17 | -0.047 | +0.021 |
| shaffer | 21 | 10 | +0.008 | +0.021 |

**1W** — 21 assets; verified ML edge: GOLD, XOM; mean ensemble IC +0.027; direction model verified in 0; volatility model verified in 9/21; drawdown model verified in 19/21; R² vs mean > 0 in 1/21 (median -0.045)

| Baseline | Cases | Ensemble wins | Mean baseline IC | Mean ensemble IC (same rows) |
|---|---|---|---|---|
| historical mean | 21 | 17 | -0.045 | +0.027 |
| mean reversion | 21 | 8 | +0.037 | +0.027 |
| momentum | 21 | 12 | +0.005 | +0.027 |
| previous return | 21 | 17 | -0.045 | +0.027 |
| shaffer | 21 | 10 | +0.010 | +0.027 |

**1M** — 21 assets; verified ML edge: none; mean ensemble IC +0.026; direction model verified in 0; volatility model verified in 11/21; drawdown model verified in 10/21; R² vs mean > 0 in 0/21 (median -0.196)

| Baseline | Cases | Ensemble wins | Mean baseline IC | Mean ensemble IC (same rows) |
|---|---|---|---|---|
| historical mean | 21 | 18 | -0.111 | +0.026 |
| mean reversion | 21 | 8 | +0.030 | +0.026 |
| momentum | 21 | 11 | -0.006 | +0.026 |
| previous return | 21 | 14 | -0.013 | +0.026 |
| shaffer | 21 | 12 | -0.005 | +0.026 |

**3M** — 21 assets; verified ML edge: none; mean ensemble IC +0.063; direction model verified in 0; volatility model verified in 13/21; drawdown model verified in 6/21; R² vs mean > 0 in 0/21 (median -0.482)

| Baseline | Cases | Ensemble wins | Mean baseline IC | Mean ensemble IC (same rows) |
|---|---|---|---|---|
| historical mean | 21 | 21 | -0.175 | +0.063 |
| mean reversion | 21 | 11 | +0.021 | +0.063 |
| momentum | 21 | 19 | -0.025 | +0.062 |
| previous return | 21 | 18 | -0.031 | +0.063 |
| shaffer | 21 | 15 | +0.001 | +0.056 |

**6M** — 21 assets; verified ML edge: none; mean ensemble IC +0.038; direction model verified in 0; volatility model verified in 13/20; drawdown model verified in 4/20; R² vs mean > 0 in 0/20 (median -0.651)

| Baseline | Cases | Ensemble wins | Mean baseline IC | Mean ensemble IC (same rows) |
|---|---|---|---|---|
| historical mean | 21 | 17 | -0.239 | +0.021 |
| mean reversion | 21 | 11 | +0.020 | +0.021 |
| momentum | 21 | 12 | -0.017 | +0.020 |
| previous return | 21 | 10 | +0.019 | +0.021 |
| shaffer | 21 | 15 | -0.054 | +0.017 |

**12M** — 21 assets; verified ML edge: none; mean ensemble IC +0.024; direction model verified in 1; volatility model verified in 14/20; drawdown model verified in 4/20; R² vs mean > 0 in 0/20 (median -0.538)

| Baseline | Cases | Ensemble wins | Mean baseline IC | Mean ensemble IC (same rows) |
|---|---|---|---|---|
| historical mean | 21 | 19 | -0.312 | +0.048 |
| mean reversion | 21 | 10 | +0.024 | +0.048 |
| momentum | 21 | 14 | -0.073 | +0.048 |
| previous return | 21 | 16 | -0.106 | +0.048 |
| shaffer | 20 | 13 | -0.090 | +0.013 |

## 25. Shaffer vs ML

| Horizon | Cases | Mean Shaffer IC | Mean ML IC (same rows) | ML better in |
|---|---|---|---|---|
| 1D | 21 | +0.008 | +0.021 | 10 |
| 1W | 21 | +0.010 | +0.027 | 10 |
| 1M | 21 | -0.005 | +0.026 | 12 |
| 3M | 21 | +0.001 | +0.056 | 15 |
| 6M | 21 | -0.054 | +0.017 | 15 |
| 12M | 20 | -0.090 | +0.013 | 13 |

## 26. Does combining them help out of sample?

α chosen on the first half of each asset's common out-of-sample period; ICs measured on the untouched second half.

| Horizon | Cases | Mean α (weight on Shaffer) | IC combined | IC Shaffer | IC ML | Combined beats both in |
|---|---|---|---|---|---|---|
| 1D | 21 | +0.40 | +0.019 | +0.003 | +0.024 | 4 |
| 1W | 21 | +0.32 | +0.006 | -0.019 | +0.026 | 2 |
| 1M | 21 | +0.36 | +0.012 | +0.002 | +0.035 | 0 |
| 3M | 21 | +0.45 | +0.045 | -0.003 | +0.114 | 0 |
| 6M | 20 | +0.48 | -0.004 | -0.067 | +0.064 | 1 |
| 12M | 19 | +0.35 | +0.078 | -0.112 | +0.130 | 0 |
