# Shaffer Directional vNext — research

Run 2026-09-26 14:27:34 · 131.7 s · `python -m finsim2 lab --vnext directional`. Research only; production and the Directional research definition are unchanged. Protocol, features, challengers and gates were committed before the full run (`engine/dirnext.py`); the 1W smoke run was seen while testing the code and nothing was changed after it.

## Summary

- **What was tested:** three logistic challengers per horizon (global, compact, class) adding genuinely short-horizon information — overnight gap, close location, intraday range, 1-day / 5-day reversal, volatility acceleration, volume surprise, illiquidity, sector-relative returns, earnings-event proximity, breadth, dispersion, VIX level and change — to the PIT prior and the production score, against the prior-only model on identical records (1D, 1W; 1M only if a short horizon succeeded).
- **Improved (every gate incl. calibration and FDR):** nothing.
- **1M:** not run: no 1D or 1W challenger passed every gate (rule fixed in advance).
- **Blocked:** option skew / implied-volatility history, positioning, short-sale volume (limited history, 2019 on).

## Main table (walk-forward, paired against the prior-only model on identical records)

| Horizon | Model | Brier | Brier gain vs prior (t) | vs current (t) | Log-loss gain | Balanced accuracy (Δ vs prior) | Accuracy | ECE | Calibration slope | Mean |Shaffer adjustment| | Eras Brier gain > 0 | Split t | FDR | Status |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1D | prior-only | +0.2491 | — | — | — | 50.2% | 52.8% | +0.0047 | +1.28 | — | — | — | — | benchmark |
| 1D | current (prior + production) | +0.2490 | — | — | — | 50.5% | 52.9% | +0.0059 | — | — | — | — | — | benchmark |
| 1D | global | +0.2498 | -0.00076 (-2.2) | -2.7 | -0.00156 | 51.1% (+0.89 pp) | 52.5% | +0.0192 | +0.38 | +3.01 pp | 0/4 | -2.9 | ✗ | REJECT |
| 1D | compact | +0.2490 | +0.00008 (+0.4) | -0.4 | +0.00015 | 51.0% (+0.79 pp) | 52.9% | +0.0108 | +0.86 | +1.66 pp | 2/4 | -0.5 | ✗ | REJECT |
| 1D | class | +0.2504 | -0.00132 (-3.3) | -3.8 | -0.00275 | 51.3% (+1.05 pp) | 52.3% | +0.0270 | +0.32 | +3.99 pp | 0/4 | -4.1 | ✗ | REJECT |
| 1W | prior-only | +0.2471 | — | — | — | 50.8% | 55.2% | +0.0096 | +1.16 | — | — | — | — | benchmark |
| 1W | current (prior + production) | +0.2470 | — | — | — | 50.9% | 55.2% | +0.0107 | — | — | — | — | — | benchmark |
| 1W | global | +0.2481 | -0.00106 (-2.7) | -3.0 | -0.00213 | 50.5% (-0.28 pp) | 53.3% | +0.0216 | +0.49 | +2.77 pp | 0/4 | -1.6 | ✗ | REJECT |
| 1W | compact | +0.2473 | -0.00018 (-1.2) | -1.8 | -0.00036 | 50.6% (-0.20 pp) | 54.5% | +0.0110 | +0.88 | +1.17 pp | 2/4 | -0.7 | ✗ | REJECT |
| 1W | class | +0.2487 | -0.00170 (-4.0) | -4.3 | -0.00345 | 50.8% (+0.03 pp) | 52.9% | +0.0325 | +0.38 | +3.62 pp | 0/4 | -2.3 | ✗ | REJECT |

Brier / log-loss gain > 0 = the challenger is better than prior-only. The Shaffer adjustment is |p − p_prior|: how far the model moves the base prior. Calibration slope 1 = calibrated; below 1 = overconfident (its probabilities are too extreme).

## Calibration (predicted probability of the called direction vs realised)

| Horizon | Model | 50–55% | 55–60% | 60–65% | 65–70% | 70–75% | 75%+ | Intercept | Slope |
|---|---|---|---|---|---|---|---|---|---|
| 1D | prior-only | 52.8% of 52.3% (n 118,808) | 55.3% of 56.7% (n 2,180) | 56.2% of 61.6% (n 281) | 80.0% of 67.8% (n 90) | 80.0% of 71.8% (n 25) | 50.0% of 77.1% (n 2) | -0.020 | +1.28 |
| 1D | global | 52.1% of 52.3% (n 85,657) | 53.9% of 56.8% (n 31,338) | 52.1% of 61.6% (n 3,961) | 53.7% of 66.5% (n 363) | 73.2% of 72.0% (n 56) | 81.8% of 76.3% (n 11) | +0.062 | +0.38 |
| 1D | compact | 52.8% of 52.3% (n 105,754) | 54.1% of 56.3% (n 14,804) | 60.4% of 61.7% (n 697) | 67.0% of 67.0% (n 94) | 87.5% of 72.2% (n 32) | 80.0% of 76.3% (n 5) | +0.018 | +0.86 |
| 1D | class | 51.4% of 52.3% (n 74,719) | 53.8% of 57.0% (n 36,649) | 53.6% of 61.8% (n 8,482) | 53.1% of 66.8% (n 1,258) | 56.7% of 71.9% (n 240) | 47.4% of 77.1% (n 38) | +0.070 | +0.32 |
| 1W | prior-only | 54.5% of 52.9% (n 94,967) | 56.4% of 56.4% (n 25,105) | 58.2% of 61.9% (n 1,012) | 69.5% of 67.2% (n 354) | 80.2% of 72.1% (n 81) | 95.3% of 88.2% (n 232) | +0.011 | +1.16 |
| 1W | global | 51.7% of 52.4% (n 77,105) | 55.3% of 57.0% (n 36,454) | 58.5% of 61.7% (n 6,764) | 63.3% of 66.9% (n 966) | 65.5% of 71.7% (n 200) | 93.9% of 88.4% (n 262) | +0.111 | +0.49 |
| 1W | compact | 53.7% of 52.7% (n 88,694) | 55.9% of 56.5% (n 30,415) | 60.2% of 61.7% (n 1,904) | 67.8% of 67.2% (n 382) | 78.3% of 71.9% (n 120) | 95.3% of 88.9% (n 236) | +0.050 | +0.88 |
| 1W | class | 51.5% of 52.4% (n 71,138) | 53.6% of 57.1% (n 37,930) | 57.6% of 61.9% (n 9,818) | 63.4% of 66.9% (n 2,110) | 63.3% of 72.0% (n 428) | 88.1% of 86.3% (n 327) | +0.129 | +0.38 |

## Bearish calls by product class (precision = share of down calls that went down)

| Horizon | Model | Class | Records | Bear calls | Bear precision | Bull calls | Bull precision |
|---|---|---|---|---|---|---|---|
| 1D | prior-only | ordinary equities | 37,544 | 545 | 45.3% | 36,999 | 52.7% |
| 1D | prior-only | equity ETFs / indices | 32,909 | 205 | 50.2% | 32,704 | 53.9% |
| 1D | prior-only | bonds | 18,280 | 169 | 39.1% | 18,111 | 54.5% |
| 1D | prior-only | commodities | 14,855 | 7 | 42.9% | 14,848 | 51.5% |
| 1D | prior-only | FX | 12,208 | 0 | — | 12,208 | 50.4% |
| 1D | prior-only | inverse / leveraged ETFs | 4,851 | 1,659 | 55.8% | 3,192 | 52.0% |
| 1D | prior-only | crypto | 476 | 43 | 46.5% | 433 | 51.3% |
| 1D | prior-only | volatility products | 263 | 91 | 56.0% | 172 | 37.8% |
| 1D | global | ordinary equities | 37,544 | 9,851 | 48.5% | 27,693 | 53.1% |
| 1D | global | equity ETFs / indices | 32,909 | 7,859 | 49.5% | 25,050 | 55.0% |
| 1D | global | bonds | 18,280 | 2,121 | 53.7% | 16,159 | 55.6% |
| 1D | global | commodities | 14,855 | 4,263 | 49.8% | 10,592 | 52.0% |
| 1D | global | FX | 12,208 | 2,114 | 51.4% | 10,094 | 50.8% |
| 1D | global | inverse / leveraged ETFs | 4,851 | 1,855 | 48.6% | 2,996 | 48.0% |
| 1D | global | crypto | 476 | 110 | 46.4% | 366 | 50.8% |
| 1D | global | volatility products | 263 | 117 | 59.0% | 146 | 39.0% |
| 1D | compact | ordinary equities | 37,544 | 5,139 | 50.4% | 32,405 | 53.2% |
| 1D | compact | equity ETFs / indices | 32,909 | 3,718 | 51.7% | 29,191 | 54.6% |
| 1D | compact | bonds | 18,280 | 1,753 | 50.1% | 16,527 | 55.0% |
| 1D | compact | commodities | 14,855 | 2,475 | 48.8% | 12,380 | 51.6% |
| 1D | compact | FX | 12,208 | 2,151 | 51.9% | 10,057 | 50.9% |
| 1D | compact | inverse / leveraged ETFs | 4,851 | 1,538 | 52.3% | 3,313 | 50.1% |
| 1D | compact | crypto | 476 | 95 | 44.2% | 381 | 50.4% |
| 1D | compact | volatility products | 263 | 92 | 64.1% | 171 | 42.1% |
| 1D | class | ordinary equities | 37,544 | 14,126 | 48.7% | 23,418 | 53.5% |
| 1D | class | equity ETFs / indices | 32,909 | 7,810 | 48.8% | 25,099 | 54.7% |
| 1D | class | bonds | 18,280 | 2,614 | 50.6% | 15,666 | 55.4% |
| 1D | class | commodities | 14,855 | 4,287 | 49.5% | 10,568 | 51.9% |
| 1D | class | FX | 12,208 | 6,908 | 49.9% | 5,300 | 50.8% |
| 1D | class | inverse / leveraged ETFs | 4,851 | 1,826 | 51.0% | 3,025 | 49.6% |
| 1D | class | crypto | 476 | 110 | 46.4% | 366 | 50.8% |
| 1D | class | volatility products | 263 | 151 | 55.6% | 112 | 33.9% |
| 1W | prior-only | ordinary equities | 37,909 | 343 | 47.8% | 37,566 | 55.1% |
| 1W | prior-only | equity ETFs / indices | 33,078 | 90 | 68.9% | 32,988 | 56.7% |
| 1W | prior-only | bonds | 18,342 | 77 | 42.9% | 18,265 | 56.6% |
| 1W | prior-only | commodities | 14,872 | 6 | 66.7% | 14,866 | 52.4% |
| 1W | prior-only | FX | 12,133 | 0 | — | 12,133 | 49.9% |
| 1W | prior-only | inverse / leveraged ETFs | 4,709 | 2,758 | 60.7% | 1,951 | 59.0% |
| 1W | prior-only | crypto | 458 | 0 | — | 458 | 52.8% |
| 1W | prior-only | volatility products | 250 | 230 | 63.5% | 20 | 20.0% |
| 1W | global | ordinary equities | 37,909 | 7,116 | 43.3% | 30,793 | 54.7% |
| 1W | global | equity ETFs / indices | 33,078 | 4,575 | 42.7% | 28,503 | 56.5% |
| 1W | global | bonds | 18,342 | 1,569 | 46.1% | 16,773 | 56.9% |
| 1W | global | commodities | 14,872 | 3,807 | 48.5% | 11,065 | 52.7% |
| 1W | global | FX | 12,133 | 3,338 | 49.5% | 8,795 | 49.7% |
| 1W | global | inverse / leveraged ETFs | 4,709 | 2,241 | 58.4% | 2,468 | 52.8% |
| 1W | global | crypto | 458 | 25 | 56.0% | 433 | 53.3% |
| 1W | global | volatility products | 250 | 200 | 67.5% | 50 | 46.0% |
| 1W | compact | ordinary equities | 37,909 | 2,076 | 41.8% | 35,833 | 54.9% |
| 1W | compact | equity ETFs / indices | 33,078 | 1,027 | 39.8% | 32,051 | 56.5% |
| 1W | compact | bonds | 18,342 | 634 | 50.2% | 17,708 | 56.9% |
| 1W | compact | commodities | 14,872 | 1,101 | 47.6% | 13,771 | 52.4% |
| 1W | compact | FX | 12,133 | 945 | 49.6% | 11,188 | 49.9% |
| 1W | compact | inverse / leveraged ETFs | 4,709 | 2,477 | 60.4% | 2,232 | 56.2% |
| 1W | compact | crypto | 458 | 4 | 50.0% | 454 | 52.9% |
| 1W | compact | volatility products | 250 | 209 | 66.0% | 41 | 41.5% |
| 1W | class | ordinary equities | 37,909 | 9,050 | 45.3% | 28,859 | 55.2% |
| 1W | class | equity ETFs / indices | 33,078 | 5,140 | 41.8% | 27,938 | 56.3% |
| 1W | class | bonds | 18,342 | 3,104 | 45.1% | 15,238 | 57.0% |
| 1W | class | commodities | 14,872 | 4,144 | 46.7% | 10,728 | 52.0% |
| 1W | class | FX | 12,133 | 7,476 | 49.8% | 4,657 | 49.6% |
| 1W | class | inverse / leveraged ETFs | 4,709 | 2,301 | 58.3% | 2,408 | 53.0% |
| 1W | class | crypto | 458 | 25 | 56.0% | 433 | 53.3% |
| 1W | class | volatility products | 250 | 213 | 65.7% | 37 | 40.5% |

A bearish signal has to earn its precision on ordinary equities and ETFs; inverse, leveraged and volatility products decay by construction, so their bear precision is not evidence of skill.

## Regimes (Brier gain vs prior-only, t; ≥ 100 independent observations; descriptive, never promoted)

| Horizon | State | n_eff | global | compact | class |
|---|---|---|---|---|---|
| 1D | breadth high | 770 | -2.7 | -0.6 | -3.8 |
| 1D | breadth low | 337 | -1.0 | +0.2 | -1.6 |
| 1D | dispersion high | 334 | -1.8 | +1.0 | -2.1 |
| 1D | dispersion low | 766 | -0.9 | +0.3 | -2.1 |
| 1D | earnings event (≤ 60 sessions) | 925 | -1.9 | -0.0 | -2.4 |
| 1D | market: bear | 198 | -1.1 | +0.8 | -1.0 |
| 1D | market: bull | 781 | -1.2 | +0.3 | -2.4 |
| 1D | no recent earnings event | 926 | -2.1 | +0.5 | -3.2 |
| 1D | rates: falling_rates | 523 | -1.5 | +1.5 | -2.2 |
| 1D | rates: rising_rates | 540 | -0.7 | -0.2 | -2.0 |
| 1D | volatility: high_vol | 500 | -1.0 | +0.7 | -1.4 |
| 1D | volatility: low_vol | 574 | -2.9 | -1.3 | -4.4 |
| 1W | breadth high | 769 | -1.9 | -1.2 | -3.6 |
| 1W | breadth low | 337 | -2.2 | -0.2 | -3.3 |
| 1W | dispersion high | 333 | -1.1 | -0.8 | -1.7 |
| 1W | dispersion low | 766 | -2.0 | -0.9 | -2.8 |
| 1W | earnings event (≤ 60 sessions) | 924 | -2.4 | -1.5 | -2.6 |
| 1W | market: bear | 198 | -2.2 | -1.8 | -2.3 |
| 1W | market: bull | 780 | -1.1 | +0.1 | -2.4 |
| 1W | no recent earnings event | 925 | -2.4 | -0.7 | -3.9 |
| 1W | rates: falling_rates | 523 | -2.9 | -2.1 | -3.5 |
| 1W | rates: rising_rates | 539 | -1.4 | +0.5 | -2.7 |
| 1W | volatility: high_vol | 500 | -3.0 | -1.7 | -2.9 |
| 1W | volatility: low_vol | 573 | -1.4 | +0.4 | -3.3 |

## Era stability (Brier gain vs prior-only)

| Horizon | Model | 2009–12 | 2013–16 | 2017–20 | 2021–24 | 2025– |
|---|---|---|---|---|---|---|
| 1D | global | -0.00170 | -0.00021 | -0.00014 | -0.00159 | +0.00055 |
| 1D | compact | -0.00057 | +0.00050 | +0.00076 | -0.00060 | +0.00047 |
| 1D | class | -0.00141 | -0.00174 | -0.00063 | -0.00208 | +0.00004 |
| 1W | global | -0.00201 | -0.00092 | -0.00110 | -0.00080 | +0.00042 |
| 1W | compact | -0.00066 | +0.00002 | -0.00020 | +0.00016 | -0.00023 |
| 1W | class | -0.00253 | -0.00208 | -0.00160 | -0.00139 | +0.00027 |

## What the compact model uses (final fit)

- **1D:** intercept +0.052, prior_z +1.658, production +0.408, rev_1d -0.048, rev_5d -0.017, gap +0.000, vol_accel +0.000, breadth_thrust_10d +0.003, d_vix_5d -0.006
- **1W:** intercept +0.085, prior_z +1.750, production +0.482, rev_1d -0.003, rev_5d -0.021, gap +0.005, vol_accel -0.009, breadth_thrust_10d +0.052, d_vix_5d +0.002

## Answers

- **Can Directional beat the base prior?** No. No challenger beats the PIT prior-only model on Brier, log loss, balanced accuracy and calibration together on unseen history; the prior remains the Directional answer and Shaffer's adjustment should stay near zero.
- **Live-shadow eligibility:** none. **Production eligibility:** none.
- **Next data bottleneck:** option skew and implied-volatility history (the classic short-horizon directional information), order-flow / positioning, and intraday data; the daily OHLCV transforms tested here are Tier 3 and did not carry it.
