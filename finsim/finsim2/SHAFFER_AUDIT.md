# Shaffer Score v2 and ML v2: the audit

Generated 2026-09-25 03:22:36 from the real research store (prices up to the latest close). Universe: 157 assets with at least six years of daily history; ML: 21 representative assets. Run time 20 minutes. Every number below is out of sample: each score was computed on its date with only the information available then, and compared with what happened afterwards.

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
| BIL | ETF | 2007-05-30 | 19.3 | 2010-06-07 | +16.0 |
| BKLN | ETF | 2011-03-03 | 15.5 | 2015-03-04 | +11.3 |
| BND | ETF | 2007-04-10 | 19.4 | 2011-08-05 | +14.8 |
| BNDX | ETF | 2013-06-04 | 13.3 | 2017-07-07 | +8.9 |
| BRENT | COMMODITY | 2007-07-30 | 19.1 | 2012-08-06 | +13.8 |
| BRK-B | EQUITY | 1996-05-09 | 30.3 | 2002-10-07 | +23.7 |
| BTC | CRYPTO | 2014-09-17 | 12.0 | 2022-01-07 | +4.4 |
| CAT | EQUITY | 1993-01-29 | 33.6 | 2001-09-05 | +24.7 |
| COFFEE | COMMODITY | 2000-01-03 | 26.7 | 2003-12-05 | +22.5 |
| COPPER | COMMODITY | 2000-08-30 | 26.0 | 2004-07-01 | +21.9 |
| CORN | COMMODITY | 2000-07-17 | 26.1 | 2004-06-07 | +22.0 |
| CORP_BAA | CORP_BOND | 1993-01-29 | 33.6 | 2001-09-05 | +24.7 |
| COST | EQUITY | 1993-01-29 | 33.6 | 2001-09-05 | +24.7 |
| CPER | ETF | 2011-11-15 | 14.8 | 2015-10-05 | +10.7 |
| CRM | EQUITY | 2004-06-23 | 22.2 | 2008-05-01 | +18.1 |
| CSCO | EQUITY | 1993-01-29 | 33.6 | 2002-05-01 | +24.1 |
| CVX | EQUITY | 1993-01-29 | 33.6 | 2001-09-05 | +24.7 |
| CWB | ETF | 2009-04-16 | 17.4 | 2015-02-04 | +11.3 |
| DAX | INDEX | 1993-01-29 | 33.6 | 2002-09-09 | +23.8 |
| DBA | ETF | 2007-01-05 | 19.7 | 2014-05-07 | +12.1 |
| DBC | ETF | 2006-02-06 | 20.6 | 2014-08-01 | +11.9 |
| DIA | ETF | 1998-01-20 | 28.6 | 2001-12-05 | +24.5 |
| DIS | EQUITY | 1993-01-29 | 33.6 | 2001-09-05 | +24.7 |
| DJI | INDEX | 1993-01-29 | 33.6 | 2001-09-05 | +24.7 |
| DXY | FX | 1993-01-29 | 33.6 | 2001-09-05 | +24.7 |
| EEM | ETF | 2003-04-14 | 23.4 | 2007-04-05 | +19.2 |
| EFA | ETF | 2001-08-27 | 25.0 | 2006-08-07 | +19.8 |
| EMB | ETF | 2007-12-19 | 18.7 | 2012-01-09 | +14.4 |
| ETH | CRYPTO | 2017-11-09 | 8.8 | 2025-12-03 | — |
| EURUSD | FX | 2003-12-01 | 22.8 | 2010-10-01 | +15.7 |
| EWG | ETF | 1996-03-18 | 30.5 | 2002-01-04 | +24.4 |
| EWJ | ETF | 1996-03-18 | 30.5 | 2003-03-03 | +23.3 |
| EWU | ETF | 1996-03-18 | 30.5 | 2001-09-05 | +24.7 |
| EWZ | ETF | 2000-07-14 | 26.1 | 2006-07-06 | +19.9 |
| FLOT | ETF | 2011-06-17 | 15.2 | 2015-09-04 | +10.8 |
| FTSE | INDEX | 1993-01-29 | 33.6 | 2001-09-05 | +24.7 |
| FXB | ETF | 2006-06-26 | 20.2 | 2013-09-05 | +12.8 |
| FXE | ETF | 2005-12-12 | 20.7 | 2011-10-07 | +14.7 |
| FXI | ETF | 2004-10-08 | 21.9 | 2011-02-02 | +15.3 |
| FXY | ETF | 2007-02-13 | 19.6 | 2010-12-06 | +15.5 |
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
| IEI | ETF | 2007-01-11 | 19.7 | 2011-03-01 | +15.3 |
| INDA | ETF | 2012-02-03 | 14.6 | 2018-04-05 | +8.2 |
| INTC | EQUITY | 1993-01-29 | 33.6 | 2002-04-03 | +24.2 |
| IWD | ETF | 2000-05-26 | 26.3 | 2005-08-02 | +20.9 |
| IWF | ETF | 2000-05-26 | 26.3 | 2006-01-09 | +20.4 |
| IWM | ETF | 2000-05-26 | 26.3 | 2006-01-09 | +20.4 |
| JNJ | EQUITY | 1993-01-29 | 33.6 | 2001-09-05 | +24.7 |
| JNK | ETF | 2007-12-04 | 18.8 | 2010-12-03 | +15.5 |
| JPM | EQUITY | 1993-01-29 | 33.6 | 2001-09-05 | +24.7 |
| KO | EQUITY | 1993-01-29 | 33.6 | 2001-09-05 | +24.7 |
| KRE | ETF | 2006-06-22 | 20.2 | 2010-10-07 | +15.7 |
| LLY | EQUITY | 1993-01-29 | 33.6 | 2001-09-05 | +24.7 |
| LQD | ETF | 2002-07-30 | 24.1 | 2006-06-07 | +20.0 |
| MA | EQUITY | 2006-05-25 | 20.3 | 2012-11-01 | +13.6 |
| MBB | ETF | 2007-03-16 | 19.5 | 2011-01-06 | +15.4 |
| MCD | EQUITY | 1993-01-29 | 33.6 | 2001-09-05 | +24.7 |
| META | EQUITY | 2012-05-18 | 14.3 | 2016-03-02 | +10.3 |
| MRK | EQUITY | 1993-01-29 | 33.6 | 2001-09-05 | +24.7 |
| MSFT | EQUITY | 1993-01-29 | 33.6 | 2001-09-05 | +24.7 |
| MTUM | ETF | 2013-04-18 | 13.4 | 2017-03-03 | +9.3 |
| MUB | ETF | 2007-09-10 | 19.0 | 2012-05-02 | +14.1 |
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
| PFF | ETF | 2007-03-30 | 19.5 | 2010-04-08 | +16.2 |
| PG | EQUITY | 1993-01-29 | 33.6 | 2001-09-05 | +24.7 |
| PLATINUM | COMMODITY | 1997-10-29 | 28.8 | 2004-01-14 | +22.1 |
| PSQ | ETF | 2006-06-21 | 20.2 | 2013-01-04 | +13.4 |
| QCOM | EQUITY | 1993-01-29 | 33.6 | 2001-09-05 | +24.7 |
| QLD | ETF | 2006-06-21 | 20.2 | 2013-05-01 | +13.1 |
| QQQ | ETF | 1999-03-10 | 27.5 | 2003-01-06 | +23.4 |
| QUAL | ETF | 2013-07-18 | 13.2 | 2017-06-02 | +9.0 |
| RUT | INDEX | 1993-01-29 | 33.6 | 2001-09-05 | +24.7 |
| SCHP | ETF | 2010-08-05 | 16.1 | 2014-08-05 | +11.8 |
| SDS | ETF | 2006-07-13 | 20.2 | 2012-07-05 | +13.9 |
| SGOV | ETF | 2020-06-01 | 6.3 | — | — |
| SH | ETF | 2006-06-21 | 20.2 | 2013-04-03 | +13.2 |
| SHY | ETF | 2002-07-30 | 24.1 | 2006-06-07 | +20.0 |
| SILVER | COMMODITY | 2000-08-30 | 26.0 | 2004-07-01 | +21.9 |
| SLV | ETF | 2006-04-28 | 20.4 | 2011-12-02 | +14.5 |
| SMH | ETF | 2000-06-05 | 26.3 | 2007-01-08 | +19.4 |
| SOL | CRYPTO | 2020-04-13 | 6.4 | — | — |
| SOYBEANS | COMMODITY | 2000-09-15 | 26.0 | 2004-08-02 | +21.8 |
| SPX | INDEX | 1993-01-29 | 33.6 | 2001-09-05 | +24.7 |
| SPY | ETF | 1993-01-29 | 33.6 | 2001-09-05 | +24.7 |
| SQQQ | ETF | 2010-02-11 | 16.6 | 2013-12-05 | +12.5 |
| SSO | ETF | 2006-06-21 | 20.2 | 2014-11-07 | +11.6 |
| SX5E | INDEX | 2007-03-30 | 19.5 | 2013-10-04 | +12.7 |
| T | EQUITY | 1993-01-29 | 33.6 | 2001-09-05 | +24.7 |
| TIP | ETF | 2003-12-05 | 22.8 | 2007-11-06 | +18.6 |
| TLT | ETF | 2002-07-30 | 24.1 | 2007-08-02 | +18.8 |
| TQQQ | ETF | 2010-02-11 | 16.6 | 2013-12-05 | +12.5 |
| TSLA | EQUITY | 2010-06-29 | 16.2 | 2016-11-04 | +9.6 |
| TXN | EQUITY | 1993-01-29 | 33.6 | 2001-09-05 | +24.7 |
| UNG | ETF | 2007-04-18 | 19.4 | 2012-09-10 | +13.8 |
| UNH | EQUITY | 1993-01-29 | 33.6 | 2001-09-05 | +24.7 |
| USDCAD | FX | 2003-09-17 | 23.0 | 2007-07-06 | +18.9 |
| USDCHF | FX | 2003-09-17 | 23.0 | 2010-06-07 | +16.0 |
| USDCNY | FX | 2001-06-25 | 25.2 | 2006-09-06 | +19.7 |
| USDINR | FX | 2003-12-01 | 22.8 | 2012-03-07 | +14.2 |
| USDJPY | FX | 1996-10-30 | 29.8 | 2005-10-05 | +20.7 |
| USDMXN | FX | 2003-12-01 | 22.8 | 2007-10-03 | +18.7 |
| USMV | ETF | 2011-10-20 | 14.9 | 2015-09-01 | +10.8 |
| USO | ETF | 2006-04-10 | 20.4 | 2013-01-04 | +13.4 |
| UST10Y | TREASURY | 1993-01-29 | 33.6 | 2001-09-05 | +24.7 |
| UST2Y | TREASURY | 1993-01-29 | 33.6 | 2001-09-05 | +24.7 |
| UST30Y | TREASURY | 1993-01-29 | 33.6 | 2001-09-05 | +24.7 |
| UST5Y | TREASURY | 1993-01-29 | 33.6 | 2001-09-05 | +24.7 |
| UUP | ETF | 2007-03-01 | 19.5 | 2012-05-04 | +14.1 |
| V | EQUITY | 2008-03-19 | 18.5 | 2013-09-05 | +12.8 |
| VCIT | ETF | 2009-11-23 | 16.8 | 2016-05-04 | +10.1 |
| VNQ | ETF | 2004-09-29 | 22.0 | 2011-09-02 | +14.8 |
| VTI | ETF | 2001-06-15 | 25.2 | 2005-07-05 | +20.9 |
| VXX | ETF | 2018-01-25 | 8.6 | 2022-02-07 | +4.3 |
| VZ | EQUITY | 1993-01-29 | 33.6 | 2001-09-05 | +24.7 |
| WHEAT | COMMODITY | 2000-07-17 | 26.1 | 2004-06-07 | +22.0 |
| WMT | EQUITY | 1993-01-29 | 33.6 | 2001-09-05 | +24.7 |
| WTI | COMMODITY | 2000-08-23 | 26.0 | 2004-07-01 | +21.9 |
| XLB | ETF | 1998-12-22 | 27.7 | 2003-01-02 | +23.4 |
| XLC | ETF | 2018-06-19 | 8.2 | 2026-01-05 | — |
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
| 1D | 155 | 1064 | 151129 |
| 1W | 155 | 1060 | 150468 |
| 1M | 155 | 244 | 35526 |
| 3M | 153 | 80 | 11407 |
| 6M | 153 | 36 | 5248 |
| 12M | 144 | 13 | 1836 |
| 3Y | 0 | — | — |
| 5Y | 0 | — | — |
| 10Y | 0 | — | — |

## 15–16. Calibration: what each score band was followed by (all assets pooled)

**1D**

| Raw score | Scores | Indep. obs. | Mean return | Median | Up share | Volatility (log) | 95% CI of the mean |
|---|---|---|---|---|---|---|---|
| -100..-60 | 267 | 267 | -0.2% | -0.0% | 37% | 0.013 | -0.4% … -0.0% |
| -60..-40 | 1062 | 1062 | -0.0% | 0.0% | 48% | 0.015 | -0.1% … 0.1% |
| -40..-20 | 4248 | 4248 | -0.0% | 0.0% | 48% | 0.016 | -0.1% … 0.0% |
| -20..-5 | 16682 | 16682 | -0.0% | 0.0% | 50% | 0.015 | -0.0% … 0.0% |
| -5..5 | 107357 | 107357 | 0.0% | 0.0% | 52% | 0.017 | 0.0% … 0.0% |
| 5..20 | 15924 | 15924 | 0.1% | 0.0% | 53% | 0.018 | 0.0% … 0.1% |
| 20..40 | 3946 | 3946 | 0.1% | 0.0% | 53% | 0.017 | 0.0% … 0.1% |
| 40..60 | 1128 | 1128 | 0.1% | 0.0% | 53% | 0.020 | 0.0% … 0.2% |
| 60..100 | 515 | 515 | 0.4% | 0.0% | 52% | 0.024 | 0.1% … 0.6% |

**1W**

| Raw score | Scores | Indep. obs. | Mean return | Median | Up share | Volatility (log) | 95% CI of the mean |
|---|---|---|---|---|---|---|---|
| -100..-60 | 122 | 122 | -0.8% | 0.0% | 45% | 0.057 | -1.8% … 0.2% |
| -60..-40 | 387 | 387 | 0.3% | 0.0% | 54% | 0.054 | -0.2% … 0.9% |
| -40..-20 | 2341 | 2341 | -0.2% | 0.0% | 51% | 0.037 | -0.3% … -0.0% |
| -20..-5 | 19794 | 19794 | 0.0% | 0.1% | 53% | 0.034 | -0.0% … 0.1% |
| -5..5 | 106478 | 106478 | 0.1% | 0.1% | 54% | 0.036 | 0.1% … 0.2% |
| 5..20 | 17767 | 17767 | 0.2% | 0.2% | 55% | 0.039 | 0.1% … 0.3% |
| 20..40 | 2778 | 2778 | 0.3% | 0.2% | 57% | 0.042 | 0.2% … 0.5% |
| 40..60 | 585 | 585 | 0.3% | 0.1% | 57% | 0.033 | 0.1% … 0.6% |
| 60..100 | 216 | 216 | 0.1% | 0.0% | 60% | 0.036 | -0.4% … 0.6% |

**1M**

| Raw score | Scores | Indep. obs. | Mean return | Median | Up share | Volatility (log) | 95% CI of the mean |
|---|---|---|---|---|---|---|---|
| -100..-60 | 126 | 30 | 0.1% | 0.1% | 52% | 0.021 | -0.6% … 0.8% |
| -60..-40 | 240 | 57 | -0.0% | 0.0% | 50% | 0.073 | -1.9% … 1.9% |
| -40..-20 | 1479 | 352 | 0.4% | 0.1% | 56% | 0.085 | -0.5% … 1.3% |
| -20..-5 | 15498 | 3690 | 0.3% | 0.4% | 56% | 0.084 | 0.1% … 0.6% |
| -5..5 | 115545 | 27511 | 0.6% | 0.7% | 58% | 0.069 | 0.5% … 0.7% |
| 5..20 | 14658 | 3490 | 0.5% | 0.5% | 57% | 0.079 | 0.2% … 0.7% |
| 20..40 | 1121 | 267 | 0.4% | 0.3% | 62% | 0.066 | -0.4% … 1.2% |
| 40..60 | 272 | 65 | 0.4% | 0.3% | 68% | 0.044 | -0.6% … 1.5% |
| 60..100 | 270 | 64 | 0.4% | 0.4% | 86% | 0.009 | 0.2% … 0.6% |

**3M**

| Raw score | Scores | Indep. obs. | Mean return | Median | Up share | Volatility (log) | 95% CI of the mean |
|---|---|---|---|---|---|---|---|
| -100..-60 | 19 | 2 | 0.3% | 0.4% | 63% | 0.012 | -1.5% … 2.2% |
| -60..-40 | 99 | 8 | 1.1% | 0.1% | 52% | 0.052 | -2.5% … 4.8% |
| -40..-20 | 1457 | 116 | 2.7% | 0.7% | 64% | 0.118 | 0.5% … 4.9% |
| -20..-5 | 17174 | 1363 | 2.1% | 1.6% | 61% | 0.136 | 1.4% … 2.8% |
| -5..5 | 107635 | 8542 | 1.7% | 1.8% | 61% | 0.121 | 1.5% … 2.0% |
| 5..20 | 16577 | 1316 | 2.0% | 1.6% | 61% | 0.120 | 1.3% … 2.6% |
| 20..40 | 693 | 55 | 0.4% | 0.9% | 64% | 0.095 | -2.1% … 2.9% |
| 40..60 | 115 | 9 | 0.5% | 1.1% | 78% | 0.040 | -2.1% … 3.2% |
| 60..100 | 7 | 1 | 0.8% | 1.0% | 100% | 0.003 | 0.2% … 1.5% |

**6M**

| Raw score | Scores | Indep. obs. | Mean return | Median | Up share | Volatility (log) | 95% CI of the mean |
|---|---|---|---|---|---|---|---|
| -100..-60 | 0 | | | | | | |
| -60..-40 | 151 | 6 | 8.4% | 6.4% | 79% | 0.201 | -7.7% … 27.4% |
| -40..-20 | 2143 | 85 | 4.0% | 2.1% | 60% | 0.240 | -1.1% … 9.5% |
| -20..-5 | 18007 | 715 | 3.4% | 3.8% | 64% | 0.197 | 2.0% … 5.0% |
| -5..5 | 91481 | 3630 | 3.6% | 3.3% | 64% | 0.173 | 3.0% … 4.2% |
| 5..20 | 18916 | 751 | 4.0% | 4.3% | 68% | 0.148 | 2.9% … 5.1% |
| 20..40 | 1474 | 58 | 0.8% | 2.1% | 62% | 0.138 | -2.7% … 4.5% |
| 40..60 | 78 | 3 | 3.3% | 2.7% | 65% | 0.073 | -4.7% … 12.1% |
| 60..100 | 0 | | | | | | |

**12M**

| Raw score | Scores | Indep. obs. | Mean return | Median | Up share | Volatility (log) | 95% CI of the mean |
|---|---|---|---|---|---|---|---|
| -100..-60 | 0 | | | | | | |
| -60..-40 | 48 | 1 | 18.5% | 16.6% | 100% | 0.085 | 0.2% … 40.1% |
| -40..-20 | 844 | 17 | 13.4% | 15.2% | 76% | 0.278 | -0.8% … 29.5% |
| -20..-5 | 11608 | 230 | 10.3% | 10.5% | 73% | 0.268 | 6.6% … 14.2% |
| -5..5 | 65202 | 1294 | 8.0% | 7.1% | 68% | 0.237 | 6.6% … 9.4% |
| 5..20 | 13974 | 277 | 9.5% | 8.9% | 73% | 0.216 | 6.7% … 12.3% |
| 20..40 | 814 | 16 | 6.4% | 7.4% | 68% | 0.167 | -1.9% … 15.4% |
| 40..60 | 66 | 1 | -1.8% | -3.6% | 36% | 0.104 | -17.9% … 17.4% |
| 60..100 | 0 | | | | | | |

## 17–18. Out-of-sample IC and hit rate by horizon

The Stouffer t treats assets as independent; assets move together, so it overstates significance. The date-clustered t averages each week's evidence across assets first and is the one to read.

| Horizon | Assets | IC (weighted by n_eff) | Median IC | Share of assets with IC > 0 | t (Stouffer, assets independent) | t (date-clustered) | Hit rate (|score| ≥ 5) |
|---|---|---|---|---|---|---|---|
| 1D | 155 | +0.005 | -0.003 | 48% | +2.1 | +0.2 | 52.0% |
| 1W | 155 | +0.012 | +0.008 | 60% | +4.7 | +1.2 | 51.0% |
| 1M | 155 | +0.007 | +0.005 | 53% | +2.3 | +0.4 | 51.0% |
| 3M | 153 | -0.003 | -0.003 | 48% | +0.3 | +0.1 | 49.4% |
| 6M | 153 | +0.013 | +0.019 | 58% | +1.4 | +0.3 | 51.9% |
| 12M | 144 | -0.009 | +0.011 | 50% | -0.3 | +0.1 | 51.4% |
| 3Y | 0 | | | | | | |
| 5Y | 0 | | | | | | |
| 10Y | 0 | | | | | | |

## 19. By asset class

**1W**: COMMODITY IC -0.005 (11 assets, 45% positive, hit 52%); CORP_BOND IC +0.088 (1 assets, 100% positive, hit 52%); CRYPTO IC +0.014 (2 assets, 100% positive, hit 46%); EQUITY IC +0.011 (45 assets, 60% positive, hit 51%); ETF IC +0.017 (72 assets, 65% positive, hit 50%); FX IC -0.012 (11 assets, 18% positive, hit 50%); INDEX IC +0.005 (9 assets, 67% positive, hit 48%); TREASURY IC +0.041 (4 assets, 75% positive, hit 48%)

**1M**: COMMODITY IC +0.002 (11 assets, 55% positive, hit 50%); CORP_BOND IC +0.052 (1 assets, 100% positive, hit 55%); CRYPTO IC +0.080 (2 assets, 100% positive, hit 30%); EQUITY IC +0.005 (45 assets, 53% positive, hit 48%); ETF IC +0.010 (72 assets, 54% positive, hit 50%); FX IC -0.027 (11 assets, 18% positive, hit 45%); INDEX IC +0.009 (9 assets, 56% positive, hit 49%); TREASURY IC +0.051 (4 assets, 75% positive, hit 48%)

**3M**: COMMODITY IC -0.021 (11 assets, 45% positive, hit 44%); CORP_BOND IC -0.070 (1 assets, 0% positive, hit 35%); CRYPTO IC +0.146 (1 assets, 100% positive, hit 64%); EQUITY IC -0.003 (45 assets, 47% positive, hit 47%); ETF IC +0.005 (71 assets, 51% positive, hit 45%); FX IC -0.056 (11 assets, 36% positive, hit 43%); INDEX IC -0.002 (9 assets, 56% positive, hit 49%); TREASURY IC +0.051 (4 assets, 25% positive, hit 44%)

**6M**: COMMODITY IC -0.010 (11 assets, 45% positive, hit 47%); CORP_BOND IC -0.080 (1 assets, 0% positive, hit 40%); CRYPTO IC +0.035 (1 assets, 100% positive, hit —); EQUITY IC -0.012 (45 assets, 47% positive, hit 43%); ETF IC +0.051 (71 assets, 72% positive, hit 50%); FX IC -0.046 (11 assets, 36% positive, hit 43%); INDEX IC +0.051 (9 assets, 56% positive, hit 50%); TREASURY IC -0.012 (4 assets, 25% positive, hit 30%)

**12M**: COMMODITY IC -0.022 (11 assets, 45% positive, hit 51%); CORP_BOND IC -0.080 (1 assets, 0% positive, hit —); EQUITY IC -0.037 (43 assets, 42% positive, hit 46%); ETF IC +0.003 (65 assets, 57% positive, hit 53%); FX IC -0.001 (11 assets, 45% positive, hit 46%); INDEX IC +0.043 (9 assets, 56% positive, hit 48%); TREASURY IC +0.074 (4 assets, 50% positive, hit 40%)

## 20. By regime

**1W**: bear +0.004; bull +0.013; expansion +0.012; falling rates +0.009; high inflation +0.011; high vol +0.017; liquidity expansion +0.011; low inflation +0.010; low vol +0.003; recession +0.007; rising rates +0.011; strong dollar +0.009; weak dollar +0.011

**1M**: bear -0.002; bull +0.003; expansion +0.001; falling rates +0.006; high inflation -0.010; high vol +0.006; liquidity expansion +0.004; low inflation +0.003; low vol -0.005; recession +0.014; rising rates +0.006; strong dollar +0.004; weak dollar -0.002

**3M**: bear -0.013; bull -0.023; expansion -0.017; falling rates +0.002; high inflation +0.009; high vol +0.003; liquidity expansion -0.023; low inflation -0.032; low vol -0.034; recession -0.002; rising rates -0.012; strong dollar -0.018; weak dollar -0.003

**6M**: bear -0.035; bull -0.016; expansion -0.009; falling rates +0.021; high inflation +0.029; high vol -0.006; liquidity expansion -0.012; low inflation -0.037; low vol -0.017; recession +0.024; rising rates +0.010; strong dollar -0.009; weak dollar +0.022

**12M**: bear -0.041; bull -0.045; expansion -0.022; falling rates +0.002; high inflation -0.057; high vol -0.055; liquidity expansion -0.046; low inflation -0.023; low vol -0.040; recession +0.063; rising rates -0.023; strong dollar -0.047; weak dollar +0.029

## 21–22. Which families add independent information, and which look useless

Own IC = the family score's out-of-sample IC; incremental IC = its partial correlation with the forward return after removing the rest of the score. Pooled across assets (weighted by independent observations; t combined by Stouffer).

**1W**

| Family | Assets | Own IC | t | Incremental IC | t | Verdict |
|---|---|---|---|---|---|---|
| Credit | 1 | +0.730 | +23.3 | +0.714 | +22.3 | adds independent information |
| Cross-Asset | 1 | +0.322 | +7.4 | +0.317 | +7.3 | adds independent information |
| Risk-Adjusted Performance | 155 | +0.006 | +3.6 | +0.006 | +3.7 | adds independent information |
| Mean Reversion | 153 | +0.009 | +3.5 | +0.010 | +3.4 | adds independent information |
| Macro | 1 | +0.124 | +2.7 | +0.121 | +2.7 | adds independent information |
| Liquidity | 28 | -0.003 | -0.6 | -0.004 | -0.9 | no measurable value |
| Relative Value | 154 | -0.002 | -0.9 | -0.002 | -0.9 | no measurable value |
| Trend | 154 | -0.005 | -1.1 | -0.005 | -1.1 | weak |
| Fundamental Growth | 45 | -0.011 | -1.9 | -0.010 | -1.8 | weak |
| Fundamental Quality | 45 | -0.011 | -2.1 | -0.010 | -1.9 | weak |
| Momentum | 155 | -0.001 | -0.3 | -0.005 | -2.3 | negative record |
| Volatility | 16 | -0.013 | -3.3 | -0.013 | -3.4 | negative record |
| Valuation | 115 | -0.009 | -3.1 | -0.010 | -3.5 | negative record |
| Statistical / Time Series | 154 | -0.010 | -3.3 | -0.011 | -3.5 | negative record |
| Rates | 110 | -0.014 | -4.3 | -0.017 | -5.3 | negative record |

**1M**

| Family | Assets | Own IC | t | Incremental IC | t | Verdict |
|---|---|---|---|---|---|---|
| Credit | 1 | +0.692 | +11.2 | +0.681 | +10.8 | adds independent information |
| Risk-Adjusted Performance | 155 | +0.006 | +2.4 | +0.007 | +2.2 | adds independent information |
| Trend | 155 | -0.003 | +1.7 | -0.002 | +0.8 | weak |
| Mean Reversion | 153 | +0.008 | +1.6 | +0.003 | +0.4 | weak |
| Cross-Asset | 1 | -0.005 | -0.1 | -0.003 | -0.0 | no measurable value |
| Momentum | 155 | +0.002 | +0.9 | -0.001 | -0.1 | no measurable value |
| Liquidity | 14 | -0.003 | -0.1 | -0.004 | -0.2 | no measurable value |
| Fundamental Quality | 44 | -0.015 | -1.4 | -0.015 | -1.4 | weak |
| Relative Value | 154 | -0.007 | -1.5 | -0.008 | -1.6 | weak |
| Macro | 4 | -0.100 | -2.4 | -0.088 | -1.9 | weak |
| Volatility | 37 | -0.022 | -2.1 | -0.021 | -2.1 | negative record |
| Statistical / Time Series | 154 | -0.014 | -2.2 | -0.016 | -2.8 | negative record |
| Fundamental Growth | 45 | -0.040 | -3.3 | -0.039 | -3.3 | negative record |
| Valuation | 108 | -0.030 | -4.8 | -0.030 | -4.9 | negative record |
| Rates | 111 | -0.029 | -4.4 | -0.032 | -5.0 | negative record |

**3M**

| Family | Assets | Own IC | t | Incremental IC | t | Verdict |
|---|---|---|---|---|---|---|
| Credit | 3 | +0.066 | +2.9 | +0.055 | +2.0 | some evidence |
| Mean Reversion | 147 | +0.019 | +2.1 | +0.018 | +1.9 | some evidence |
| Trend | 153 | +0.001 | +1.2 | +0.010 | +1.7 | some evidence |
| Liquidity | 2 | +0.014 | +0.2 | +0.037 | +0.5 | no measurable value |
| Cross-Asset | 12 | -0.011 | -0.1 | -0.010 | -0.1 | no measurable value |
| Macro | 15 | -0.008 | -0.3 | -0.006 | -0.2 | no measurable value |
| Momentum | 153 | -0.013 | -0.4 | -0.010 | -0.7 | no measurable value |
| Statistical / Time Series | 153 | -0.013 | -1.0 | -0.015 | -1.2 | weak |
| Risk-Adjusted Performance | 152 | -0.016 | -1.1 | -0.015 | -1.4 | weak |
| Fundamental Quality | 39 | -0.040 | -1.8 | -0.041 | -1.9 | weak |
| Fundamental Growth | 44 | -0.047 | -2.2 | -0.043 | -2.1 | negative record |
| Relative Value | 151 | -0.020 | -2.2 | -0.021 | -2.3 | negative record |
| Volatility | 24 | -0.054 | -2.9 | -0.051 | -2.6 | negative record |
| Valuation | 103 | -0.052 | -4.9 | -0.052 | -4.7 | negative record |
| Rates | 116 | -0.053 | -4.8 | -0.059 | -5.3 | negative record |

**6M**

| Family | Assets | Own IC | t | Incremental IC | t | Verdict |
|---|---|---|---|---|---|---|
| Trend | 152 | +0.026 | +2.6 | +0.031 | +2.6 | adds independent information |
| Momentum | 150 | +0.028 | +2.4 | +0.023 | +1.6 | some evidence |
| Cross-Asset | 1 | +0.191 | +1.2 | +0.165 | +1.0 | weak |
| Mean Reversion | 145 | +0.000 | -0.1 | +0.005 | +0.2 | no measurable value |
| Risk-Adjusted Performance | 148 | +0.012 | +1.1 | +0.000 | -0.0 | weak |
| Volatility | 8 | -0.016 | -0.2 | -0.022 | -0.4 | no measurable value |
| Liquidity | 4 | -0.069 | -1.0 | -0.094 | -1.3 | weak |
| Credit | 3 | -0.131 | -1.6 | -0.140 | -1.7 | weak |
| Fundamental Quality | 35 | -0.061 | -1.8 | -0.059 | -1.8 | weak |
| Statistical / Time Series | 153 | -0.021 | -1.5 | -0.025 | -1.8 | weak |
| Relative Value | 150 | -0.028 | -1.9 | -0.032 | -2.1 | negative record |
| Fundamental Growth | 38 | -0.078 | -2.4 | -0.074 | -2.3 | negative record |
| Rates | 91 | -0.063 | -3.3 | -0.066 | -3.6 | negative record |
| Valuation | 79 | -0.086 | -4.5 | -0.082 | -4.3 | negative record |

**12M**

| Family | Assets | Own IC | t | Incremental IC | t | Verdict |
|---|---|---|---|---|---|---|
| Trend | 144 | +0.022 | +0.8 | +0.065 | +2.5 | adds independent information |
| Momentum | 142 | -0.002 | -0.0 | +0.014 | +0.6 | no measurable value |
| Mean Reversion | 133 | +0.003 | -0.0 | +0.004 | +0.1 | no measurable value |
| Valuation | 77 | -0.031 | -0.8 | -0.033 | -0.8 | no measurable value |
| Statistical / Time Series | 144 | -0.021 | -0.9 | -0.021 | -0.9 | no measurable value |
| Risk-Adjusted Performance | 136 | -0.022 | -1.2 | -0.024 | -1.3 | weak |
| Rates | 63 | -0.060 | -1.5 | -0.064 | -1.6 | weak |
| Fundamental Quality | 33 | -0.079 | -1.8 | -0.075 | -1.7 | weak |
| Relative Value | 140 | -0.039 | -1.4 | -0.052 | -1.9 | weak |
| Fundamental Growth | 39 | -0.148 | -3.2 | -0.152 | -3.3 | negative record |

## 23. Decaying signals (at the latest refit)

**1W**: `ret_3m` decaying in 81 assets, weakening in 3, healthy in 1; `macd` decaying in 62 assets, weakening in 7, healthy in 3; `excess_3m` decaying in 54 assets, weakening in 14, healthy in 22; `rate_duration` decaying in 37 assets, weakening in 10, healthy in 16; `ret_1d` decaying in 29 assets, weakening in 16, healthy in 86; `skew_60` decaying in 28 assets, weakening in 14, healthy in 28; `mr_opportunity` decaying in 25 assets, weakening in 14, healthy in 60; `ret_1w` decaying in 18 assets, weakening in 11, healthy in 99; `rsi_14` decaying in 16 assets, weakening in 5, healthy in 79; `z_20` decaying in 14 assets, weakening in 8, healthy in 85

Signal status across assets at 1W: muted: no reliable direction: 7574; active: 3150; muted: evidence against the prior: 1739; active: direction from evidence: 27; reversed by evidence: 9

**1M**: `ret_3m` decaying in 88 assets, weakening in 7, healthy in 4; `excess_3m` decaying in 75 assets, weakening in 6, healthy in 11; `macd` decaying in 63 assets, weakening in 11, healthy in 3; `ret_1d` decaying in 57 assets, weakening in 17, healthy in 39; `skew_60` decaying in 40 assets, weakening in 8, healthy in 36; `rate_duration` decaying in 36 assets, weakening in 6, healthy in 20; `mr_opportunity` decaying in 34 assets, weakening in 9, healthy in 44; `ret_1w` decaying in 31 assets, weakening in 17, healthy in 55; `z_20` decaying in 21 assets, weakening in 7, healthy in 69; `bb_pctb` decaying in 21 assets, weakening in 7, healthy in 69

Signal status across assets at 1M: muted: no reliable direction: 7573; active: 3080; muted: evidence against the prior: 1798; active: direction from evidence: 27; reversed by evidence: 12; insufficient: 9

**3M**: `ret_3m` decaying in 95 assets, weakening in 6, healthy in 12; `macd` decaying in 91 assets, weakening in 9, healthy in 2; `excess_3m` decaying in 58 assets, weakening in 23, healthy in 8; `rate_duration` decaying in 47 assets, weakening in 7, healthy in 15; `skew_60` decaying in 41 assets, weakening in 20, healthy in 41; `ret_1d` decaying in 34 assets, weakening in 6, healthy in 58; `mr_opportunity` decaying in 18 assets, weakening in 10, healthy in 68; `pctile_252` decaying in 16 assets, weakening in 1, healthy in 1; `ret_1w` decaying in 15 assets, weakening in 14, healthy in 74; `z_20` decaying in 12 assets, weakening in 6, healthy in 70

Signal status across assets at 3M: muted: no reliable direction: 7549; active: 2978; muted: evidence against the prior: 1889; active: direction from evidence: 51; insufficient: 25; reversed by evidence: 7

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
| shaffer | 21 | 12 | -0.004 | +0.026 |

**3M** — 21 assets; verified ML edge: none; mean ensemble IC +0.063; direction model verified in 0; volatility model verified in 13/21; drawdown model verified in 6/21; R² vs mean > 0 in 0/21 (median -0.482)

| Baseline | Cases | Ensemble wins | Mean baseline IC | Mean ensemble IC (same rows) |
|---|---|---|---|---|
| historical mean | 21 | 21 | -0.175 | +0.063 |
| mean reversion | 21 | 11 | +0.021 | +0.063 |
| momentum | 21 | 19 | -0.025 | +0.062 |
| previous return | 21 | 18 | -0.031 | +0.063 |
| shaffer | 21 | 15 | +0.000 | +0.056 |

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
| shaffer | 20 | 13 | -0.074 | +0.013 |

## 25. Shaffer vs ML

| Horizon | Cases | Mean Shaffer IC | Mean ML IC (same rows) | ML better in |
|---|---|---|---|---|
| 1D | 21 | +0.008 | +0.021 | 10 |
| 1W | 21 | +0.010 | +0.027 | 10 |
| 1M | 21 | -0.004 | +0.026 | 12 |
| 3M | 21 | +0.000 | +0.056 | 15 |
| 6M | 21 | -0.054 | +0.017 | 15 |
| 12M | 20 | -0.074 | +0.013 | 13 |

## 26. Does combining them help out of sample?

α chosen on the first half of each asset's common out-of-sample period; ICs measured on the untouched second half.

| Horizon | Cases | Mean α (weight on Shaffer) | IC combined | IC Shaffer | IC ML | Combined beats both in |
|---|---|---|---|---|---|---|
| 1D | 21 | +0.40 | +0.018 | +0.003 | +0.024 | 4 |
| 1W | 21 | +0.32 | +0.006 | -0.019 | +0.026 | 1 |
| 1M | 21 | +0.36 | +0.012 | +0.002 | +0.035 | 0 |
| 3M | 21 | +0.45 | +0.043 | -0.004 | +0.113 | 0 |
| 6M | 20 | +0.47 | -0.005 | -0.066 | +0.064 | 1 |
| 12M | 19 | +0.35 | +0.095 | -0.073 | +0.132 | 0 |

## 27. Candidate families (shadow): do they add information?

Seven families of economically different information were added in shadow: they are computed point in time and run through the same evidence machinery (weights, confidence, regime, decay, validation) but are NOT in the production score. Every asset's matured out-of-sample record is split at one common date, 2018-01-01: before it decides, from it confirms. The incremental IC (partial correlation of the family score with the forward return given the production score) is pooled **by date** — each week's average across assets, then a t on that weekly series with overlapping windows removed — because a hundred correlated equities are not a hundred independent tests. **Admission rule** (fixed before the results): at least 5 assets; date-clustered t ≥ 2 before 2018; positive with t ≥ 1 from 2018, positive in a majority of assets; the score's IC with the family not lower and its calibration monotonicity not more than 0.05 lower from 2018. Production weights were not re-tuned. (A first run pooled assets as independent — Stouffer — and split each asset's record into its own thirds; that overstated significance and was replaced by this test before anything was admitted.)

**Carry** — `carry` (+) equities and funds: trailing 12-month distributions ÷ price − 3-month bill; constant-maturity bonds: yield at the duration − bill; currency pairs: base-currency short rate − quote-currency rate; `div_yield` (+) trailing 12-month dividends (by ex-date) ÷ price; indices use their tracking fund

**Yield Curve** — `curvature` (0) Treasury butterfly: positive = a humped belly; `d_curvature_3m` (0) change in 2·5Y − 2Y − 10Y over 63 sessions; `slope_5s30s` (0) FRED DGS30 − DGS5; `d_slope_2s10s_1m` (0) change in 10Y − 2Y over 21 sessions; `roll_down` (+) bonds: (yield at D − yield at D − 1 year) × D on today's curve, the price gain from ageing one year

**Term Structure** — `vix_term` (0) above 0 = backwardation (near-term stress priced above 3-month); `roll_yield` (+) commodity fund's 1-year return − the front-month futures' price change − bill: negative in contango, positive in backwardation (WTI/USO, NATGAS/UNG, COPPER/CPER); `d_roll_yield_3m` (0) change in the roll yield over 63 sessions

**Inflation** — `infl_accel` (0) CPI 3-month annualised − 12-month (first releases, publication dates); `d_breakeven_3m` (0) change in the 5-year breakeven (T5YIE) over 63 sessions; `d_real_y10_3m` (0) change in the 10-year TIPS yield (DFII10) over 63 sessions

**FX** — `d_rate_diff_3m` (+) change in (foreign − US) short rate for the asset's currency exposure; `d_rate_diff_12m` (+) change in (foreign − US) short rate over 252 sessions

**Commodity** — `real_rate_transmit` (0) 1-year beta of daily returns to daily real-yield changes × the 3-month real-yield change; `usd_transmit` (0) 1-year dollar beta × the dollar's 3-month move; `cmd_breadth_3m` (0) share of the 11 commodity futures with a positive 3-month return, minus ½

**Optionality** — `vrp` (0) own 30-day implied volatility − 20-day realised volatility; `iv_pctile` (0) own implied volatility's percentile over 3 years; `d_iv_1m` (0) change in own implied volatility over 21 sessions

**1D**

| Family | Assets | Own IC (before 2018) | Incremental IC before (clustered t) | Incremental IC from 2018 (clustered t) | Assets > 0 (from 2018) | Score IC without → with | Monotonicity without → with | Verdict |
|---|---|---|---|---|---|---|---|---|
| Carry | 114 | +0.004 | +0.004 (+0.6) | +0.012 (+1.3) | 62% | -0.013 → -0.013 | +0.02 → +0.01 | REJECT: incremental IC not significant before 2018 (date-clustered t +0.6); the score's IC falls with it from 2018 (-0.000) |
| Commodity | 145 | -0.005 | -0.005 (-0.4) | — (—) | — | -0.017 → -0.017 | -0.00 → -0.00 | REJECT: incremental IC not significant before 2018 (date-clustered t -0.4); not confirmed from 2018 (incremental IC —, t —); positive in only — of assets from 2018; the score's IC falls with it from 2018 (-0.000) |
| FX | 12 | -0.010 | +0.000 (+0.0) | +0.006 (+0.3) | 50% | -0.026 → -0.024 | -0.01 → -0.00 | REJECT: incremental IC not significant before 2018 (date-clustered t +0.0); not confirmed from 2018 (incremental IC +0.006, t +0.3) |
| Inflation | 150 | +0.088 | +0.081 (+1.0) | — (—) | — | -0.015 → -0.015 | +0.01 → +0.01 | REJECT: incremental IC not significant before 2018 (date-clustered t +1.0); not confirmed from 2018 (incremental IC —, t —); positive in only — of assets from 2018; the score's IC falls with it from 2018 (-0.000) |
| Optionality | 20 | -0.021 | -0.032 (-1.6) | -0.023 (-1.0) | 17% | -0.034 → -0.035 | -0.13 → -0.13 | REJECT: incremental IC not significant before 2018 (date-clustered t -1.6); not confirmed from 2018 (incremental IC -0.023, t -1.0); positive in only 17% of assets from 2018; the score's IC falls with it from 2018 (-0.002) |
| Term Structure | 150 | -0.007 | -0.017 (-0.7) | -0.068 (-1.7) | 0% | -0.015 → -0.015 | +0.01 → +0.01 | REJECT: incremental IC not significant before 2018 (date-clustered t -0.7); not confirmed from 2018 (incremental IC -0.068, t -1.7); positive in only 0% of assets from 2018; the score's IC falls with it from 2018 (-0.000) |
| Yield Curve | 150 | +0.005 | +0.012 (+0.6) | +0.002 (+0.1) | 47% | -0.015 → -0.015 | +0.01 → +0.01 | REJECT: incremental IC not significant before 2018 (date-clustered t +0.6); not confirmed from 2018 (incremental IC +0.002, t +0.1); positive in only 47% of assets from 2018; the score's IC falls with it from 2018 (-0.000) |
| All seven together | 150 | — | — (—) | — (—) | — | -0.015 → -0.015 | +0.01 → +0.00 | — |

**1W**

| Family | Assets | Own IC (before 2018) | Incremental IC before (clustered t) | Incremental IC from 2018 (clustered t) | Assets > 0 (from 2018) | Score IC without → with | Monotonicity without → with | Verdict |
|---|---|---|---|---|---|---|---|---|
| Carry | 114 | +0.006 | +0.001 (+0.2) | +0.036 (+3.8) | 75% | -0.005 → -0.004 | +0.04 → +0.05 | REJECT: incremental IC not significant before 2018 (date-clustered t +0.2) |
| Commodity | 145 | -0.008 | -0.009 (-0.4) | — (—) | — | -0.010 → -0.010 | +0.02 → +0.02 | REJECT: incremental IC not significant before 2018 (date-clustered t -0.4); not confirmed from 2018 (incremental IC —, t —); positive in only — of assets from 2018; the score's IC falls with it from 2018 (-0.000) |
| FX | 12 | -0.041 | -0.016 (-0.6) | -0.014 (-0.5) | 36% | -0.034 → -0.038 | -0.16 → -0.18 | REJECT: incremental IC not significant before 2018 (date-clustered t -0.6); not confirmed from 2018 (incremental IC -0.014, t -0.5); positive in only 36% of assets from 2018; the score's IC falls with it from 2018 (-0.004) |
| Inflation | 150 | -0.002 | -0.004 (-0.2) | — (—) | — | -0.007 → -0.007 | +0.03 → +0.03 | REJECT: incremental IC not significant before 2018 (date-clustered t -0.2); not confirmed from 2018 (incremental IC —, t —); positive in only — of assets from 2018; the score's IC falls with it from 2018 (-0.000) |
| Optionality | 20 | -0.012 | -0.008 (-0.6) | -0.071 (-1.4) | 0% | -0.029 → -0.031 | -0.09 → -0.10 | REJECT: incremental IC not significant before 2018 (date-clustered t -0.6); not confirmed from 2018 (incremental IC -0.071, t -1.4); positive in only 0% of assets from 2018; the score's IC falls with it from 2018 (-0.002) |
| Term Structure | 150 | -0.005 | -0.069 (-2.2) | -0.019 (-1.1) | 0% | -0.007 → -0.007 | +0.03 → +0.03 | REJECT: incremental IC not significant before 2018 (date-clustered t -2.2); not confirmed from 2018 (incremental IC -0.019, t -1.1); positive in only 0% of assets from 2018; the score's IC falls with it from 2018 (-0.000) |
| Yield Curve | 150 | -0.010 | -0.016 (-0.8) | -0.021 (-0.8) | 29% | -0.007 → -0.007 | +0.03 → +0.03 | REJECT: incremental IC not significant before 2018 (date-clustered t -0.8); not confirmed from 2018 (incremental IC -0.021, t -0.8); positive in only 29% of assets from 2018; the score's IC falls with it from 2018 (-0.000) |
| All seven together | 150 | — | — (—) | — (—) | — | -0.007 → -0.006 | +0.03 → +0.04 | — |

**1M**

| Family | Assets | Own IC (before 2018) | Incremental IC before (clustered t) | Incremental IC from 2018 (clustered t) | Assets > 0 (from 2018) | Score IC without → with | Monotonicity without → with | Verdict |
|---|---|---|---|---|---|---|---|---|
| Carry | 113 | +0.021 | +0.023 (+1.5) | +0.072 (+3.5) | 79% | -0.005 → +0.004 | +0.04 → +0.09 | REJECT: incremental IC not significant before 2018 (date-clustered t +1.5) |
| Commodity | 144 | -0.038 | -0.038 (-1.5) | -0.062 (-0.9) | 25% | -0.013 → -0.013 | +0.02 → +0.02 | REJECT: incremental IC not significant before 2018 (date-clustered t -1.5); not confirmed from 2018 (incremental IC -0.062, t -0.9); positive in only 25% of assets from 2018; the score's IC falls with it from 2018 (-0.000) |
| FX | 12 | -0.088 | -0.094 (-1.8) | -0.007 (-0.1) | 22% | -0.049 → -0.051 | -0.11 → -0.14 | REJECT: incremental IC not significant before 2018 (date-clustered t -1.8); not confirmed from 2018 (incremental IC -0.007, t -0.1); positive in only 22% of assets from 2018; the score's IC falls with it from 2018 (-0.002) |
| Inflation | 149 | -0.010 | -0.007 (-0.1) | -0.077 (-2.0) | 20% | -0.008 → -0.009 | +0.04 → +0.03 | REJECT: incremental IC not significant before 2018 (date-clustered t -0.1); not confirmed from 2018 (incremental IC -0.077, t -2.0); positive in only 20% of assets from 2018; the score's IC falls with it from 2018 (-0.000) |
| Optionality | 20 | -0.003 | -0.009 (-0.1) | -0.068 (-0.8) | 0% | -0.021 → -0.025 | -0.04 → -0.04 | REJECT: incremental IC not significant before 2018 (date-clustered t -0.1); not confirmed from 2018 (incremental IC -0.068, t -0.8); positive in only 0% of assets from 2018; the score's IC falls with it from 2018 (-0.003) |
| Term Structure | 149 | -0.043 | -0.051 (-1.9) | -0.030 (-0.5) | 20% | -0.008 → -0.009 | +0.04 → +0.03 | REJECT: incremental IC not significant before 2018 (date-clustered t -1.9); not confirmed from 2018 (incremental IC -0.030, t -0.5); positive in only 20% of assets from 2018; the score's IC falls with it from 2018 (-0.000) |
| Yield Curve | 149 | +0.015 | +0.005 (+0.1) | -0.052 (-1.0) | 32% | -0.008 → -0.009 | +0.04 → +0.04 | REJECT: incremental IC not significant before 2018 (date-clustered t +0.1); not confirmed from 2018 (incremental IC -0.052, t -1.0); positive in only 32% of assets from 2018; the score's IC falls with it from 2018 (-0.000) |
| All seven together | 149 | — | — (—) | — (—) | — | -0.008 → -0.003 | +0.04 → +0.06 | — |

**3M**

| Family | Assets | Own IC (before 2018) | Incremental IC before (clustered t) | Incremental IC from 2018 (clustered t) | Assets > 0 (from 2018) | Score IC without → with | Monotonicity without → with | Verdict |
|---|---|---|---|---|---|---|---|---|
| Carry | 109 | +0.014 | +0.020 (+0.7) | +0.078 (+1.9) | 60% | -0.044 → -0.022 | -0.12 → -0.04 | REJECT: incremental IC not significant before 2018 (date-clustered t +0.7) |
| Commodity | 140 | -0.012 | -0.051 (-0.8) | +0.010 (+0.2) | 71% | -0.048 → -0.048 | -0.12 → -0.12 | REJECT: incremental IC not significant before 2018 (date-clustered t -0.8); not confirmed from 2018 (incremental IC +0.010, t +0.2); the score's IC falls with it from 2018 (-0.000) |
| FX | 12 | -0.165 | -0.180 (-1.6) | -0.002 (-0.0) | 60% | -0.113 → -0.099 | -0.35 → -0.31 | REJECT: incremental IC not significant before 2018 (date-clustered t -1.6); not confirmed from 2018 (incremental IC -0.002, t -0.0) |
| Inflation | 145 | -0.023 | -0.033 (-0.5) | +0.020 (+0.3) | 50% | -0.045 → -0.045 | -0.12 → -0.11 | REJECT: incremental IC not significant before 2018 (date-clustered t -0.5); not confirmed from 2018 (incremental IC +0.020, t +0.3) |
| Optionality | 20 | -0.106 | -0.071 (-0.7) | — (—) | — | -0.072 → -0.072 | -0.17 → -0.17 | REJECT: incremental IC not significant before 2018 (date-clustered t -0.7); not confirmed from 2018 (incremental IC —, t —); positive in only — of assets from 2018; the score's IC falls with it from 2018 (-0.000) |
| Term Structure | 145 | — | — (—) | — (—) | — | -0.045 → -0.046 | -0.12 → -0.12 | REJECT: incremental IC not significant before 2018 (date-clustered t —); not confirmed from 2018 (incremental IC —, t —); positive in only — of assets from 2018; the score's IC falls with it from 2018 (-0.000) |
| Yield Curve | 145 | +0.087 | +0.074 (+1.1) | -0.145 (-1.4) | 12% | -0.045 → -0.047 | -0.12 → -0.12 | REJECT: incremental IC not significant before 2018 (date-clustered t +1.1); not confirmed from 2018 (incremental IC -0.145, t -1.4); positive in only 12% of assets from 2018; the score's IC falls with it from 2018 (-0.002) |
| All seven together | 145 | — | — (—) | — (—) | — | -0.045 → -0.028 | -0.12 → -0.04 | — |

**6M**

| Family | Assets | Own IC (before 2018) | Incremental IC before (clustered t) | Incremental IC from 2018 (clustered t) | Assets > 0 (from 2018) | Score IC without → with | Monotonicity without → with | Verdict |
|---|---|---|---|---|---|---|---|---|
| Carry | 105 | -0.000 | +0.002 (+0.1) | +0.083 (+1.5) | 67% | -0.017 → +0.015 | +0.01 → +0.08 | REJECT: incremental IC not significant before 2018 (date-clustered t +0.1) |
| Commodity | 133 | — | — (—) | -0.012 (-0.1) | 50% | -0.023 → -0.024 | +0.00 → +0.00 | REJECT: incremental IC not significant before 2018 (date-clustered t —); not confirmed from 2018 (incremental IC -0.012, t -0.1); the score's IC falls with it from 2018 (-0.001) |
| FX | 12 | -0.249 | -0.365 (-1.5) | +0.080 (+0.8) | 90% | -0.012 → -0.006 | +0.03 → +0.07 | REJECT: incremental IC not significant before 2018 (date-clustered t -1.5); not confirmed from 2018 (incremental IC +0.080, t +0.8) |
| Inflation | 138 | — | — (—) | — (—) | — | -0.023 → -0.023 | -0.00 → -0.00 | REJECT: incremental IC not significant before 2018 (date-clustered t —); not confirmed from 2018 (incremental IC —, t —); positive in only — of assets from 2018; the score's IC falls with it from 2018 (-0.000) |
| Optionality | 20 | — | — (—) | — (—) | — | -0.044 → -0.044 | -0.12 → -0.12 | REJECT: incremental IC not significant before 2018 (date-clustered t —); not confirmed from 2018 (incremental IC —, t —); positive in only — of assets from 2018; the score's IC falls with it from 2018 (-0.000) |
| Term Structure | 138 | — | — (—) | — (—) | — | -0.023 → -0.023 | -0.00 → -0.00 | REJECT: incremental IC not significant before 2018 (date-clustered t —); not confirmed from 2018 (incremental IC —, t —); positive in only — of assets from 2018; the score's IC falls with it from 2018 (-0.000) |
| Yield Curve | 138 | +0.106 | +0.069 (+1.3) | -0.126 (-1.1) | 41% | -0.023 → -0.024 | -0.00 → -0.00 | REJECT: incremental IC not significant before 2018 (date-clustered t +1.3); not confirmed from 2018 (incremental IC -0.126, t -1.1); positive in only 41% of assets from 2018; the score's IC falls with it from 2018 (-0.002) |
| All seven together | 138 | — | — (—) | — (—) | — | -0.023 → -0.001 | -0.00 → +0.04 | — |

**12M**

| Family | Assets | Own IC (before 2018) | Incremental IC before (clustered t) | Incremental IC from 2018 (clustered t) | Assets > 0 (from 2018) | Score IC without → with | Monotonicity without → with | Verdict |
|---|---|---|---|---|---|---|---|---|
| Carry | 77 | +0.097 | +0.104 (+1.3) | +0.129 (+1.9) | 62% | -0.050 → -0.000 | -0.06 → -0.00 | REJECT: incremental IC not significant before 2018 (date-clustered t +1.3) |
| Commodity | 97 | — | — (—) | — (—) | — | -0.041 → -0.041 | -0.06 → -0.06 | REJECT: incremental IC not significant before 2018 (date-clustered t —); not confirmed from 2018 (incremental IC —, t —); positive in only — of assets from 2018; the score's IC falls with it from 2018 (-0.000) |
| FX | 8 | — | — (—) | +0.002 (+0.0) | 67% | +0.051 → +0.011 | +0.13 → +0.03 | REJECT: incremental IC not significant before 2018 (date-clustered t —); not confirmed from 2018 (incremental IC +0.002, t +0.0); the score's IC falls with it from 2018 (-0.040); calibration less monotone with it |
| Inflation | 102 | — | — (—) | — (—) | — | -0.040 → -0.040 | -0.06 → -0.06 | REJECT: incremental IC not significant before 2018 (date-clustered t —); not confirmed from 2018 (incremental IC —, t —); positive in only — of assets from 2018; the score's IC falls with it from 2018 (-0.000) |
| Optionality | 18 | — | — (—) | — (—) | — | -0.010 → -0.010 | -0.01 → -0.01 | REJECT: incremental IC not significant before 2018 (date-clustered t —); not confirmed from 2018 (incremental IC —, t —); positive in only — of assets from 2018; the score's IC falls with it from 2018 (-0.000) |
| Term Structure | 102 | — | — (—) | — (—) | — | -0.040 → -0.040 | -0.06 → -0.06 | REJECT: incremental IC not significant before 2018 (date-clustered t —); not confirmed from 2018 (incremental IC —, t —); positive in only — of assets from 2018; the score's IC falls with it from 2018 (-0.000) |
| Yield Curve | 102 | +0.531 | +0.519 (+1.2) | -0.561 (-1.9) | 0% | -0.040 → -0.044 | -0.06 → -0.06 | REJECT: incremental IC not significant before 2018 (date-clustered t +1.2); not confirmed from 2018 (incremental IC -0.561, t -1.9); positive in only 0% of assets from 2018; the score's IC falls with it from 2018 (-0.005) |
| All seven together | 102 | — | — (—) | — (—) | — | -0.040 → -0.010 | -0.06 → -0.02 | — |

**Admitted:** none — every candidate family stays in shadow. Admission is applied only by recording it in `shaffer_score.ADMITTED` (and a new score version), never automatically.
