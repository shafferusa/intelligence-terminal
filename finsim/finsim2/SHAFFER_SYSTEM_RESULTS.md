# The Shaffer System: walk-forward results

## Summary

**The learned equations do not beat the calibrated prior** (E0: each asset's long-run average return, shrunk toward
its product type) at almost any horizon or asset class. This matches every earlier study in this repository.
- Where they do, and the adoption rule keeps them: ETFs at 12M–5Y, Treasuries at 12M, and FX / corporate bonds at
  a few long horizons. All of these are "Medium" reliability.
- Everywhere else the production equation is E0, with "Low" reliability.
- Across all classes, the learned models' out-of-sample squared-error gain against E0 is ≤ 0 at every horizon.

**The ranges are conservative by construction.** The 90% ranges cover 91% of outcomes at 1D–1W and 94–96% at 1M–5Y,
because of the floor on the residual scale (SHAFFER_SYSTEM.md §5).

**Known weakness: crypto.** The equation rests on three assets and one long rising market (2014–2026), so its
long-horizon forecasts extrapolate that history (BTC 12M: +138%, P(>0) 96%). They carry a "thin class" warning.
Treat them as a description of the past, not a forecast.

**Known weakness: 2Y–5Y.** Those horizons have only a handful of independent outcomes in the test period, and every
such forecast says so.

Run 2026-09-28 16:59:21 under SHAFFER_SYSTEM.md (protocol fixed before any result). Each Shaffer Score is the expected percentage total return over its horizon. The learned equation is adopted per horizon and asset class only where its walk-forward error is not worse than the calibrated prior (E0) and its calibration slope is positive. Otherwise the calibrated prior is the production equation.

**The columns:**
- **MSE gain vs E0:** the out-of-sample reduction in squared error of the log return against the calibrated prior (per date, t adjusted for overlapping windows).
- **Slope:** the regression of the realised log return on the forecast (1 = perfectly calibrated).
- **Rank IC:** the within-date cross-section of stocks.
- **Coverage:** of the 50% and 90% ranges.
- **Brier:** of P(return > 0), against the base rate.

## 1M (Alpha) — 162063 records, 561 assets, eras 2009–12, 2013–16, 2017–20, 2021–24, 2025–

Learned = the nested ML choice. The prior (E0) columns give the calibrated prior's own out-of-sample record; that is the production equation wherever the learned one is not adopted.

| Class | Records | Learned: MSE gain vs E0 (t) | Eras won | Learned slope | Rank IC (t) | Prior (E0): slope · 50 / 90 coverage · Brier P(>0) vs base rate | Adopted | Reliability | Final equation |
|---|---|---|---|---|---|---|---|---|---|
| EQUITY | 85373 | -0.000230 (-1.5) | 1/5 | -0.49 | -0.006 (-0.5) | +0.34 · 63% / 96% · +0.2454 vs +0.2440 | prior | Low | E0 calibrated prior, depth 2, K 300.0 |
| ETF | 14348 | -0.000291 (-4.4) | 3/5 | -0.04 | — (—) | +1.36 · 62% / 96% · +0.2450 vs +0.2433 | prior | Low | E0 calibrated prior, depth 2, K 300.0 |
| FX | 2321 | -0.000035 (-2.9) | 1/5 | -0.23 | — (—) | -0.72 · 62% / 96% · +0.2499 vs +0.2505 | prior | Low | E0 calibrated prior, depth 2, K 300.0 |
| COMMODITY | 2315 | -0.000177 (-1.5) | 3/5 | -0.19 | — (—) | +0.22 · 59% / 95% · +0.2502 vs +0.2506 | prior | Low | E0 calibrated prior, depth 2, K 300.0 |
| INDEX | 1899 | -0.000124 (-1.5) | 1/5 | -0.39 | — (—) | -1.33 · 60% / 95% · +0.2445 vs +0.2402 | prior | Low | E0 calibrated prior, depth 2, K 300.0 |
| TREASURY | 844 | -0.000044 (-1.8) | 2/5 | +0.22 | — (—) | +0.67 · 51% / 94% · +0.2681 vs +0.2605 | prior | Low | E0 calibrated prior, depth 2, K 300.0 |
| CRYPTO | 269 | +0.002107 (+0.6) | 2/3 | -19.35 | — (—) | +0.10 · 59% / 92% · +0.2539 vs +0.2628 | prior | Low | E0 calibrated prior, depth 2, K 300.0 |
| CORP_BOND | 211 | -0.000358 (-3.9) | 1/5 | -0.04 | — (—) | -0.76 · 61% / 94% · +0.2397 vs +0.2422 | prior | Low | E0 calibrated prior, depth 2, K 300.0 |

All classes: MSE gain -0.000225 (t -1.7), slope -0.26, 90% coverage 96%.

Nested choices by era: 2009–12: COMMODITY E4/d2/K30, CORP_BOND E4/d2/K30, EQUITY E4/d3/K300, ETF E4/d4/K3000, FX E4/d4/K30, INDEX E4/d4/K30, TREASURY E4/d1/K30; 2013–16: COMMODITY E2/d1/K30, CORP_BOND E2/d1/K30, EQUITY E2/d2/K30, ETF E2/d2/K30, FX E2/d2/K30, INDEX E2/d2/K30, TREASURY E2/d6/K30; 2017–20: COMMODITY E0/d3/K300, CORP_BOND E0/d6/K300, CRYPTO E0/d1/K30, EQUITY E0/d4/K30000, FX E0/d4/K30, INDEX E0/d6/K30, TREASURY E0/d6/K300, ETF E2/d6/K3000; 2021–24: ETF E0/d4/K30, COMMODITY E1/d6/K300, CORP_BOND E1/d1/K30, CRYPTO E1/d1/K30, FX E1/d6/K30, INDEX E1/d6/K30, TREASURY E1/d6/K30, EQUITY E2/d2/K30; 2025–: EQUITY E0/d3/K30, ETF E0/d6/K30000, COMMODITY E1/d6/K30, CORP_BOND E1/d2/K30, CRYPTO E1/d1/K30, FX E1/d6/K30, INDEX E1/d6/K300, TREASURY E1/d6/K3000

## 3M (Alpha) — 160944 records, 561 assets, eras 2009–12, 2013–16, 2017–20, 2021–24, 2025–

Learned = the nested ML choice. The prior (E0) columns give the calibrated prior's own out-of-sample record; that is the production equation wherever the learned one is not adopted.

| Class | Records | Learned: MSE gain vs E0 (t) | Eras won | Learned slope | Rank IC (t) | Prior (E0): slope · 50 / 90 coverage · Brier P(>0) vs base rate | Adopted | Reliability | Final equation |
|---|---|---|---|---|---|---|---|---|---|
| EQUITY | 84475 | -0.007505 (-1.6) | 2/5 | -0.35 | +0.011 (+0.7) | +0.65 · 63% / 96% · +0.2378 vs +0.2333 | prior | Low | E0 calibrated prior, depth 2, K 300.0 |
| ETF | 14202 | -0.004920 (-1.7) | 3/5 | -0.09 | — (—) | +1.20 · 61% / 95% · +0.2409 vs +0.2336 | prior | Low | E0 calibrated prior, depth 2, K 300.0 |
| FX | 2299 | -0.000170 (-2.0) | 1/5 | -0.30 | — (—) | +0.55 · 62% / 95% · +0.2515 vs +0.2508 | prior | Low | E0 calibrated prior, depth 2, K 300.0 |
| COMMODITY | 2296 | -0.003986 (-1.2) | 4/5 | -0.13 | — (—) | +0.14 · 59% / 95% · +0.2508 vs +0.2546 | prior | Low | E0 calibrated prior, depth 2, K 300.0 |
| INDEX | 1881 | -0.004075 (-1.9) | 1/5 | -0.41 | — (—) | -1.22 · 65% / 97% · +0.2360 vs +0.2233 | prior | Low | E0 calibrated prior, depth 2, K 300.0 |
| TREASURY | 836 | -0.000077 (-1.0) | 2/5 | +0.34 | — (—) | +0.94 · 48% / 92% · +0.2553 vs +0.2527 | prior | Low | E0 calibrated prior, depth 2, K 300.0 |
| CRYPTO | 263 | +0.008939 (+0.5) | 2/3 | -36.70 | — (—) | +0.11 · 60% / 91% · +0.2535 vs +0.2543 | prior | Low | E0 calibrated prior, depth 2, K 300.0 |
| CORP_BOND | 209 | -0.004741 (-1.5) | 0/5 | -0.21 | — (—) | -1.63 · 61% / 93% · +0.2210 vs +0.2289 | prior | Low | E0 calibrated prior, depth 2, K 300.0 |

All classes: MSE gain -0.006745 (t -1.6), slope -0.29, 90% coverage 94%.

Nested choices by era: 2009–12: EQUITY E2/d3/K30, COMMODITY E4/d1/K30, CORP_BOND E4/d1/K30, ETF E4/d6/K3000, FX E4/d2/K30, INDEX E4/d4/K30, TREASURY E4/d4/K30; 2013–16: COMMODITY E2/d1/K30, CORP_BOND E2/d1/K30, EQUITY E2/d3/K30, ETF E2/d2/K30, FX E2/d2/K30, INDEX E2/d6/K30, TREASURY E2/d6/K30; 2017–20: COMMODITY E3/d2/K30, CORP_BOND E3/d6/K30, CRYPTO E3/d1/K30, EQUITY E3/d3/K30000, ETF E3/d6/K300, FX E3/d3/K300, INDEX E3/d6/K300, TREASURY E3/d6/K300; 2021–24: ETF E1/d4/K30, EQUITY E2/d6/K3000, COMMODITY E3/d3/K30, CORP_BOND E3/d6/K300, CRYPTO E3/d1/K30, FX E3/d2/K30, INDEX E3/d2/K30, TREASURY E3/d6/K3000; 2025–: ETF E0/d6/K30000, COMMODITY E1/d6/K30, CORP_BOND E1/d4/K30, CRYPTO E1/d1/K30, EQUITY E1/d3/K30, FX E1/d6/K30, INDEX E1/d6/K3000, TREASURY E1/d6/K3000

## 12M (Alpha) — 155893 records, 561 assets, eras 2009–12, 2013–16, 2017–20, 2021–24, 2025–

Learned = the nested ML choice. The prior (E0) columns give the calibrated prior's own out-of-sample record; that is the production equation wherever the learned one is not adopted.

| Class | Records | Learned: MSE gain vs E0 (t) | Eras won | Learned slope | Rank IC (t) | Prior (E0): slope · 50 / 90 coverage · Brier P(>0) vs base rate | Adopted | Reliability | Final equation |
|---|---|---|---|---|---|---|---|---|---|
| EQUITY | 80434 | -0.013919 (-0.7) | 1/5 | -0.43 | +0.014 (+0.4) | +1.07 · 65% / 97% · +0.2074 vs +0.2020 | prior | Low | E0 calibrated prior, depth 2, K 300.0 |
| ETF | 13545 | +0.001777 (+0.5) | 3/5 | +0.68 | — (—) | +1.09 · 56% / 93% · +0.2230 vs +0.2139 | learned | Medium | E0 calibrated prior, depth 6, K 30.0 |
| FX | 2200 | -0.000078 (-0.3) | 2/5 | +0.07 | — (—) | +0.19 · 64% / 93% · +0.2570 vs +0.2546 | prior | Low | E0 calibrated prior, depth 2, K 300.0 |
| COMMODITY | 2197 | -0.001951 (-0.6) | 2/5 | +0.20 | — (—) | +0.08 · 53% / 94% · +0.2660 vs +0.2795 | prior | Low | E0 calibrated prior, depth 2, K 300.0 |
| INDEX | 1800 | -0.003378 (-0.9) | 1/5 | -0.37 | — (—) | +0.19 · 64% / 98% · +0.2084 vs +0.1946 | prior | Low | E0 calibrated prior, depth 2, K 300.0 |
| TREASURY | 800 | +0.000567 (+0.8) | 4/5 | +0.52 | — (—) | +0.61 · 46% / 79% · +0.2463 vs +0.2732 | learned | Medium | E0 calibrated prior, depth 2, K 30.0 |
| CRYPTO | 236 | -0.390851 (-1.6) | 0/3 | -0.85 | — (—) | +1.58 · 64% / 92% · +0.2027 vs +0.2614 | prior | Low | E0 calibrated prior, depth 2, K 300.0 |
| CORP_BOND | 200 | -0.000682 (-0.9) | 2/5 | +0.03 | — (—) | +1.18 · 52% / 92% · +0.1415 vs +0.1388 | prior | Low | E0 calibrated prior, depth 2, K 300.0 |

All classes: MSE gain -0.011669 (t -0.8), slope +0.09, 90% coverage 95%.

Nested choices by era: 2009–12: EQUITY E2/d3/K300, ETF E2/d6/K300, COMMODITY E4/d2/K30, CORP_BOND E4/d3/K30, FX E4/d2/K30, INDEX E4/d4/K300, TREASURY E4/d2/K30; 2013–16: COMMODITY E0/d6/K30, CORP_BOND E0/d1/K30, EQUITY E0/d6/K300, FX E0/d6/K30, INDEX E0/d1/K30, TREASURY E0/d6/K30, ETF E3/d1/K30; 2017–20: COMMODITY E4/d1/K30, CORP_BOND E4/d6/K30, CRYPTO E4/d1/K30, EQUITY E4/d2/K30, ETF E4/d6/K30, FX E4/d2/K30, INDEX E4/d2/K30, TREASURY E4/d6/K30; 2021–24: ETF E1/d6/K30, COMMODITY E2/d1/K30, CORP_BOND E2/d6/K30, CRYPTO E2/d1/K30, FX E2/d4/K30, INDEX E2/d2/K30, TREASURY E2/d1/K30, EQUITY E4/d1/K30; 2025–: COMMODITY E1/d6/K300, CORP_BOND E1/d6/K300, CRYPTO E1/d4/K30, EQUITY E1/d3/K30, ETF E1/d6/K3000, FX E1/d6/K30, INDEX E1/d6/K30, TREASURY E1/d4/K3000

## 6M (Alpha) — 159260 records, 561 assets, eras 2009–12, 2013–16, 2017–20, 2021–24, 2025–

Learned = the nested ML choice. The prior (E0) columns give the calibrated prior's own out-of-sample record; that is the production equation wherever the learned one is not adopted.

| Class | Records | Learned: MSE gain vs E0 (t) | Eras won | Learned slope | Rank IC (t) | Prior (E0): slope · 50 / 90 coverage · Brier P(>0) vs base rate | Adopted | Reliability | Final equation |
|---|---|---|---|---|---|---|---|---|---|
| EQUITY | 83128 | -0.017605 (-1.1) | 1/5 | -0.36 | +0.006 (+0.3) | +1.18 · 64% / 97% · +0.2268 vs +0.2216 | prior | Low | E0 calibrated prior, depth 2, K 300.0 |
| ETF | 13983 | -0.012500 (-1.2) | 3/5 | -0.01 | — (—) | +1.14 · 59% / 94% · +0.2339 vs +0.2257 | prior | Low | E0 calibrated prior, depth 2, K 300.0 |
| FX | 2266 | -0.000736 (-1.5) | 2/5 | -0.05 | — (—) | +0.27 · 64% / 95% · +0.2550 vs +0.2515 | prior | Low | E0 calibrated prior, depth 2, K 300.0 |
| COMMODITY | 2263 | -0.010842 (-1.0) | 2/5 | -0.26 | — (—) | +0.14 · 58% / 94% · +0.2555 vs +0.2643 | prior | Low | E0 calibrated prior, depth 2, K 300.0 |
| INDEX | 1854 | -0.014275 (-1.1) | 1/5 | -0.44 | — (—) | -1.80 · 63% / 97% · +0.2268 vs +0.2122 | prior | Low | E0 calibrated prior, depth 2, K 300.0 |
| TREASURY | 824 | -0.000330 (-1.0) | 1/5 | +0.13 | — (—) | +0.67 · 50% / 83% · +0.2540 vs +0.2554 | prior | Low | E0 calibrated prior, depth 2, K 300.0 |
| CRYPTO | 254 | -0.083916 (-2.2) | 1/3 | -0.81 | — (—) | +1.34 · 67% / 91% · +0.2335 vs +0.2825 | prior | Low | E0 calibrated prior, depth 2, K 300.0 |
| CORP_BOND | 206 | -0.012120 (-1.0) | 3/5 | -0.26 | — (—) | -0.25 · 55% / 94% · +0.2068 vs +0.2065 | prior | Low | E0 calibrated prior, depth 2, K 300.0 |

All classes: MSE gain -0.016287 (t -1.1), slope -0.27, 90% coverage 94%.

Nested choices by era: 2009–12: ETF E3/d6/K300, COMMODITY E4/d1/K30, CORP_BOND E4/d1/K30, EQUITY E4/d2/K30, FX E4/d2/K30, INDEX E4/d1/K30, TREASURY E4/d6/K300; 2013–16: EQUITY E0/d6/K300, ETF E2/d1/K30, COMMODITY E4/d6/K3000, CORP_BOND E4/d1/K30, FX E4/d6/K3000, INDEX E4/d2/K30, TREASURY E4/d6/K30; 2017–20: COMMODITY E4/d1/K30, CORP_BOND E4/d6/K300, CRYPTO E4/d1/K30, EQUITY E4/d2/K30, ETF E4/d6/K300, FX E4/d4/K30, INDEX E4/d6/K3000, TREASURY E4/d6/K30; 2021–24: ETF E0/d6/K30, COMMODITY E3/d1/K30, CORP_BOND E3/d6/K30, CRYPTO E3/d1/K30, EQUITY E3/d6/K3000, FX E3/d6/K300, INDEX E3/d2/K30, TREASURY E3/d2/K30; 2025–: COMMODITY E0/d6/K30, CORP_BOND E0/d3/K30, CRYPTO E0/d4/K30, ETF E0/d6/K30000, FX E0/d4/K30, INDEX E0/d6/K300, TREASURY E0/d4/K300, EQUITY E1/d3/K30

## 3Y (Alpha) — 142432 records, 561 assets, eras 2009–12, 2013–16, 2017–20, 2021–24

Learned = the nested ML choice. The prior (E0) columns give the calibrated prior's own out-of-sample record; that is the production equation wherever the learned one is not adopted.

| Class | Records | Learned: MSE gain vs E0 (t) | Eras won | Learned slope | Rank IC (t) | Prior (E0): slope · 50 / 90 coverage · Brier P(>0) vs base rate | Adopted | Reliability | Final equation |
|---|---|---|---|---|---|---|---|---|---|
| EQUITY | 69658 | -0.006489 (-0.6) | 2/4 | +0.20 | +0.012 (+0.3) | +0.45 · 69% / 98% · +0.1485 vs +0.1446 | prior | Low | E0 calibrated prior, depth 2, K 300.0 |
| ETF | 11793 | +0.030019 (+1.6) | 4/4 | +0.78 | — (—) | +0.61 · 53% / 89% · +0.1935 vs +0.1861 | learned | Medium | E1 calibrated production, depth 3, K 30.0 |
| FX | 1936 | +0.000454 (+0.4) | 1/4 | +0.51 | — (—) | +0.18 · 68% / 93% · +0.2681 vs +0.2633 | learned | Medium | E1 calibrated production, depth 6, K 30.0 |
| COMMODITY | 1933 | +0.002707 (+0.2) | 1/4 | -0.67 | — (—) | -0.58 · 46% / 87% · +0.3165 vs +0.3522 | prior | Low | E0 calibrated prior, depth 2, K 300.0 |
| INDEX | 1584 | -0.008158 (-0.6) | 3/4 | -0.11 | — (—) | +0.05 · 76% / 98% · +0.1342 vs +0.1537 | prior | Low | E0 calibrated prior, depth 2, K 300.0 |
| TREASURY | 704 | -0.000823 (-0.1) | 2/4 | +0.49 | — (—) | +0.45 · 38% / 73% · +0.1717 vs +0.2291 | prior | Low | E0 calibrated prior, depth 2, K 300.0 |
| CORP_BOND | 176 | +0.003901 (+0.5) | 3/4 | +0.62 | — (—) | +4.01 · 33% / 84% · +0.1510 vs +0.1635 | learned | Medium | E1 calibrated production, depth 4, K 30.0 |
| CRYPTO | 93 | +0.035184 (+0.0) | 1/1 | -17.32 | — (—) | +0.57 · 76% / 100% · +0.1388 vs +0.1720 | prior | Low | E0 calibrated prior, depth 2, K 300.0 |

All classes: MSE gain -0.001198 (t -0.1), slope +0.68, 90% coverage 96%.

Nested choices by era: 2009–12: COMMODITY E0/d1/K30, CORP_BOND E0/d6/K30, EQUITY E0/d3/K30, FX E0/d2/K30, INDEX E0/d6/K30, TREASURY E0/d6/K30, ETF E3/d6/K300; 2013–16: ETF E1/d6/K30, EQUITY E2/d1/K30, COMMODITY E4/d1/K30, CORP_BOND E4/d4/K30, FX E4/d6/K300, INDEX E4/d2/K30, TREASURY E4/d3/K30; 2017–20: EQUITY E0/d6/K30, COMMODITY E3/d1/K30, CORP_BOND E3/d6/K30, ETF E3/d6/K30, FX E3/d4/K30, INDEX E3/d1/K30, TREASURY E3/d6/K30; 2021–24: ETF E1/d6/K30, EQUITY E2/d3/K30000, COMMODITY E3/d3/K300, CORP_BOND E3/d6/K30, CRYPTO E3/d1/K30, FX E3/d6/K30, INDEX E3/d6/K30, TREASURY E3/d6/K30

## 5Y (Alpha) — 128973 records, 558 assets, eras 2009–12, 2013–16, 2017–20, 2021–24

Learned = the nested ML choice. The prior (E0) columns give the calibrated prior's own out-of-sample record; that is the production equation wherever the learned one is not adopted.

| Class | Records | Learned: MSE gain vs E0 (t) | Eras won | Learned slope | Rank IC (t) | Prior (E0): slope · 50 / 90 coverage · Brier P(>0) vs base rate | Adopted | Reliability | Final equation |
|---|---|---|---|---|---|---|---|---|---|
| EQUITY | 58887 | -0.033230 (-1.1) | 0/4 | +0.22 | -0.018 (-0.3) | +0.37 · 68% / 98% · +0.1148 vs +0.1099 | prior | Low | E0 calibrated prior, depth 2, K 300.0 |
| ETF | 10041 | +0.068460 (+1.7) | 2/4 | +0.42 | — (—) | -1.46 · 45% / 88% · +0.1846 vs +0.1677 | learned | Medium | E3 limited interactions, depth 6, K 30.0 |
| FX | 1672 | +0.000772 (+0.2) | 1/4 | +0.64 | — (—) | +0.40 · 63% / 92% · +0.2687 vs +0.3003 | learned | Medium | E0 calibrated prior, depth 6, K 30.0 |
| COMMODITY | 1669 | -0.029652 (-0.6) | 1/4 | -0.20 | — (—) | -1.25 · 42% / 76% · +0.3577 vs +0.4414 | prior | Low | E0 calibrated prior, depth 2, K 300.0 |
| INDEX | 1368 | -0.013606 (-0.4) | 2/4 | +0.20 | — (—) | +0.05 · 71% / 97% · +0.1043 vs +0.1636 | prior | Low | E0 calibrated prior, depth 2, K 300.0 |
| TREASURY | 608 | -0.004903 (-0.3) | 2/4 | +0.07 | — (—) | +1.07 · 24% / 67% · +0.1198 vs +0.1738 | prior | Low | E0 calibrated prior, depth 2, K 300.0 |
| CORP_BOND | 152 | +0.011823 (+0.7) | 3/4 | +0.79 | — (—) | +4.95 · 19% / 80% · +0.0009 vs +0.0000 | learned | Medium | E0 calibrated prior, depth 2, K 30.0 |
| CRYPTO | 21 | — (—) | 0/1 | -1.72 | — (—) | +2.64 · 5% / 100% · +0.2291 vs +0.2857 | prior | Low | E0 calibrated prior, depth 2, K 300.0 |

All classes: MSE gain -0.015217 (t -0.7), slope +0.49, 90% coverage 95%.

Nested choices by era: 2009–12: COMMODITY E0/d6/K30, CORP_BOND E0/d6/K30, EQUITY E0/d3/K30, ETF E0/d2/K30, FX E0/d4/K30, INDEX E0/d6/K30, TREASURY E0/d6/K30; 2013–16: ETF E2/d6/K30, COMMODITY E3/d6/K30, CORP_BOND E3/d6/K300, EQUITY E3/d6/K300, FX E3/d2/K30, INDEX E3/d2/K30, TREASURY E3/d6/K30; 2017–20: COMMODITY E2/d3/K30, CORP_BOND E2/d6/K30, EQUITY E2/d6/K300, FX E2/d6/K30, INDEX E2/d1/K30, TREASURY E2/d6/K30, ETF E4/d6/K30; 2021–24: EQUITY E2/d6/K30, ETF E3/d6/K30, COMMODITY E4/d6/K30, CORP_BOND E4/d2/K30, CRYPTO E4/d1/K30, FX E4/d6/K30, INDEX E4/d1/K30, TREASURY E4/d6/K30

## 2Y (Alpha) — 149164 records, 561 assets, eras 2009–12, 2013–16, 2017–20, 2021–24

Learned = the nested ML choice. The prior (E0) columns give the calibrated prior's own out-of-sample record; that is the production equation wherever the learned one is not adopted.

| Class | Records | Learned: MSE gain vs E0 (t) | Eras won | Learned slope | Rank IC (t) | Prior (E0): slope · 50 / 90 coverage · Brier P(>0) vs base rate | Adopted | Reliability | Final equation |
|---|---|---|---|---|---|---|---|---|---|
| EQUITY | 75046 | -0.003920 (-0.6) | 1/4 | +0.32 | -0.039 (-0.9) | +0.68 · 67% / 97% · +0.1749 vs +0.1712 | prior | Low | E0 calibrated prior, depth 2, K 300.0 |
| ETF | 12669 | +0.018068 (+1.6) | 3/4 | +0.81 | — (—) | +0.86 · 53% / 92% · +0.2040 vs +0.1950 | learned | Medium | E1 calibrated production, depth 4, K 30.0 |
| FX | 2068 | +0.000213 (+0.4) | 2/4 | +0.20 | — (—) | +0.10 · 67% / 95% · +0.2611 vs +0.2546 | learned | Medium | E0 calibrated prior, depth 6, K 30.0 |
| COMMODITY | 2065 | -0.010718 (-0.8) | 2/4 | -0.01 | — (—) | -0.10 · 52% / 91% · +0.2798 vs +0.3008 | prior | Low | E0 calibrated prior, depth 2, K 300.0 |
| INDEX | 1692 | -0.004735 (-0.8) | 1/4 | -0.18 | — (—) | -0.02 · 69% / 97% · +0.1808 vs +0.1710 | prior | Low | E0 calibrated prior, depth 2, K 300.0 |
| TREASURY | 752 | -0.005774 (-1.7) | 2/4 | -0.03 | — (—) | +0.97 · 46% / 77% · +0.2215 vs +0.2703 | prior | Low | E0 calibrated prior, depth 2, K 300.0 |
| CORP_BOND | 188 | -0.000869 (-0.5) | 2/4 | +1.98 | — (—) | +4.33 · 47% / 88% · +0.1153 vs +0.1233 | prior | Low | E0 calibrated prior, depth 2, K 300.0 |
| CRYPTO | 129 | -0.148522 (-0.2) | 0/1 | +150.75 | — (—) | +0.78 · 71% / 100% · +0.1869 vs +0.3313 | prior | Low | E0 calibrated prior, depth 2, K 300.0 |

All classes: MSE gain -0.001235 (t -0.2), slope +0.83, 90% coverage 96%.

Nested choices by era: 2009–12: EQUITY E2/d1/K30, COMMODITY E4/d6/K30, CORP_BOND E4/d3/K30, ETF E4/d1/K30, FX E4/d2/K30, INDEX E4/d4/K30, TREASURY E4/d6/K3000; 2013–16: COMMODITY E0/d6/K30, CORP_BOND E0/d1/K30, EQUITY E0/d6/K30, FX E0/d6/K30, INDEX E0/d6/K30, TREASURY E0/d6/K30, ETF E2/d2/K30; 2017–20: COMMODITY E0/d4/K30, CORP_BOND E0/d6/K30, EQUITY E0/d6/K300, FX E0/d4/K30, INDEX E0/d1/K30, TREASURY E0/d6/K30, ETF E3/d6/K30; 2021–24: ETF E0/d6/K30, COMMODITY E2/d4/K300, CORP_BOND E2/d4/K30, CRYPTO E2/d1/K30, EQUITY E2/d3/K30000, FX E2/d6/K30, INDEX E2/d2/K30, TREASURY E2/d6/K30

## 1D (Directional) — 683177 records, 561 assets, eras 2009–12, 2013–16, 2017–20, 2021–24, 2025–

Learned = the nested ML choice. The prior (E0) columns give the calibrated prior's own out-of-sample record; that is the production equation wherever the learned one is not adopted.

| Class | Records | Learned: MSE gain vs E0 (t) | Eras won | Learned slope | Rank IC (t) | Prior (E0): slope · 50 / 90 coverage · Brier P(>0) vs base rate | Adopted | Reliability | Final equation |
|---|---|---|---|---|---|---|---|---|---|
| EQUITY | 360975 | -0.000001 (-0.6) | 2/5 | +0.28 | +0.007 (+1.1) | -3.73 · 51% / 91% · +0.2500 vs +0.2500 | prior | Low | E0 calibrated prior, depth 2, K 300.0 |
| ETF | 60646 | -0.000003 (-1.4) | 3/5 | +0.21 | — (—) | -0.73 · 51% / 91% · +0.2580 vs +0.2500 | prior | Low | E0 calibrated prior, depth 2, K 300.0 |
| FX | 9812 | +0.000000 (+0.4) | 2/5 | +0.57 | — (—) | +0.26 · 52% / 91% · +0.2502 vs +0.2502 | learned | Medium | E0 calibrated prior, depth 4, K 30.0 |
| COMMODITY | 9785 | +0.000000 (+0.2) | 1/5 | +0.39 | — (—) | -1.31 · 52% / 91% · +0.2502 vs +0.2501 | learned | Medium | E0 calibrated prior, depth 1, K 30.0 |
| INDEX | 8028 | +0.000001 (+1.0) | 2/5 | +0.97 | — (—) | -3.75 · 51% / 91% · +0.2508 vs +0.2499 | learned | Medium | E0 calibrated prior, depth 6, K 300.0 |
| TREASURY | 3568 | -0.000001 (-3.8) | 2/5 | -0.52 | — (—) | -0.07 · 49% / 90% · +0.2484 vs +0.2486 | prior | Low | E0 calibrated prior, depth 2, K 300.0 |
| CRYPTO | 1140 | +0.000365 (+2.9) | 3/3 | -2.00 | — (—) | +0.01 · 39% / 83% · +0.2884 vs +0.2510 | prior | Low | E0 calibrated prior, depth 2, K 300.0 |
| CORP_BOND | 892 | -0.000000 (-1.2) | 2/5 | +0.07 | — (—) | +1.13 · 49% / 95% · +0.2492 vs +0.2510 | prior | Low | E0 calibrated prior, depth 2, K 300.0 |

All classes: MSE gain -0.000000 (t -0.4), slope +0.27, 90% coverage 91%.

Nested choices by era: 2009–12: COMMODITY E0/d3/K3000, CORP_BOND E0/d4/K300, EQUITY E0/d2/K30, ETF E0/d1/K30, FX E0/d4/K30, INDEX E0/d6/K30000, TREASURY E0/d4/K30; 2013–16: COMMODITY E0/d1/K30, FX E0/d2/K30, CORP_BOND E3/d3/K300, EQUITY E3/d3/K30, ETF E3/d2/K30, INDEX E3/d3/K3000, TREASURY E3/d4/K30; 2017–20: FX E0/d4/K30, TREASURY E0/d6/K3000, COMMODITY E3/d2/K30, CORP_BOND E3/d2/K30, CRYPTO E3/d1/K30, EQUITY E3/d2/K30, ETF E3/d6/K30000, INDEX E4/d6/K3000; 2021–24: FX E0/d4/K30, COMMODITY E3/d4/K300, CORP_BOND E3/d4/K300, CRYPTO E3/d1/K30, EQUITY E3/d4/K300, ETF E3/d6/K300, INDEX E3/d4/K30, TREASURY E3/d2/K30; 2025–: COMMODITY E0/d4/K30, CORP_BOND E0/d6/K3000, CRYPTO E0/d1/K30, EQUITY E0/d6/K30000, ETF E0/d4/K3000, FX E0/d4/K30, INDEX E0/d6/K300, TREASURY E0/d6/K3000

## 3D (Directional) — 682603 records, 561 assets, eras 2009–12, 2013–16, 2017–20, 2021–24, 2025–

Learned = the nested ML choice. The prior (E0) columns give the calibrated prior's own out-of-sample record; that is the production equation wherever the learned one is not adopted.

| Class | Records | Learned: MSE gain vs E0 (t) | Eras won | Learned slope | Rank IC (t) | Prior (E0): slope · 50 / 90 coverage · Brier P(>0) vs base rate | Adopted | Reliability | Final equation |
|---|---|---|---|---|---|---|---|---|---|
| EQUITY | 360526 | -0.000007 (-1.4) | 2/5 | -0.10 | +0.001 (+0.4) | -1.87 · 51% / 91% · +0.2490 vs +0.2492 | prior | Low | E0 calibrated prior, depth 2, K 300.0 |
| ETF | 60573 | -0.000022 (-5.6) | 1/5 | -0.03 | — (—) | -0.33 · 51% / 91% · +0.2505 vs +0.2482 | prior | Low | E0 calibrated prior, depth 2, K 300.0 |
| FX | 9801 | -0.000001 (-3.6) | 1/5 | +0.09 | — (—) | +0.16 · 52% / 91% · +0.2509 vs +0.2504 | prior | Low | E0 calibrated prior, depth 2, K 300.0 |
| COMMODITY | 9772 | +0.000003 (+2.6) | 4/5 | +2.12 | — (—) | -2.03 · 51% / 91% · +0.2497 vs +0.2501 | learned | Medium | E3 limited interactions, depth 6, K 300.0 |
| INDEX | 8018 | -0.000002 (-1.3) | 1/5 | -0.12 | — (—) | -1.39 · 52% / 90% · +0.2476 vs +0.2457 | prior | Low | E0 calibrated prior, depth 2, K 300.0 |
| TREASURY | 3564 | +0.000000 (+0.5) | 2/5 | +0.80 | — (—) | +0.08 · 49% / 90% · +0.2578 vs +0.2505 | learned | Medium | E2 elastic-net families, depth 2, K 30.0 |
| CRYPTO | 1137 | +0.000064 (+1.1) | 2/3 | -21.09 | — (—) | -0.46 · 43% / 86% · +0.2545 vs +0.2519 | prior | Low | E0 calibrated prior, depth 2, K 300.0 |
| CORP_BOND | 891 | +0.000000 (+0.2) | 3/5 | +0.55 | — (—) | +1.38 · 48% / 92% · +0.2492 vs +0.2487 | learned | Medium | E2 elastic-net families, depth 1, K 30.0 |

All classes: MSE gain -0.000008 (t -1.9), slope -0.03, 90% coverage 91%.

Nested choices by era: 2009–12: COMMODITY E0/d6/K300, TREASURY E0/d1/K30, ETF E3/d3/K30, INDEX E3/d4/K3000, CORP_BOND E4/d4/K30, EQUITY E4/d3/K30, FX E4/d2/K30; 2013–16: EQUITY E0/d4/K300, ETF E0/d4/K300, FX E0/d6/K30, INDEX E0/d6/K30, TREASURY E0/d6/K30, COMMODITY E2/d2/K30, CORP_BOND E2/d6/K300; 2017–20: INDEX E0/d4/K30, EQUITY E2/d3/K30, FX E2/d2/K30, COMMODITY E3/d2/K30, CORP_BOND E3/d3/K30000, CRYPTO E3/d1/K30, ETF E4/d6/K3000, TREASURY E4/d6/K3000; 2021–24: COMMODITY E0/d6/K30, EQUITY E0/d6/K3000, ETF E0/d6/K300, FX E0/d4/K300, INDEX E0/d6/K30, TREASURY E0/d4/K300, CORP_BOND E2/d4/K300, CRYPTO E2/d1/K30; 2025–: COMMODITY E0/d6/K30, EQUITY E0/d3/K30, ETF E0/d6/K30000, FX E0/d3/K300, INDEX E0/d3/K300, TREASURY E0/d3/K3000, CORP_BOND E2/d4/K30, CRYPTO E2/d1/K30

## 1W (Directional) — 682594 records, 561 assets, eras 2009–12, 2013–16, 2017–20, 2021–24, 2025–

Learned = the nested ML choice. The prior (E0) columns give the calibrated prior's own out-of-sample record; that is the production equation wherever the learned one is not adopted.

| Class | Records | Learned: MSE gain vs E0 (t) | Eras won | Learned slope | Rank IC (t) | Prior (E0): slope · 50 / 90 coverage · Brier P(>0) vs base rate | Adopted | Reliability | Final equation |
|---|---|---|---|---|---|---|---|---|---|
| EQUITY | 360526 | -0.000007 (-1.2) | 2/5 | -0.03 | +0.009 (+1.8) | -1.06 · 51% / 91% · +0.2484 vs +0.2483 | prior | Low | E0 calibrated prior, depth 2, K 300.0 |
| ETF | 60573 | -0.000009 (-2.3) | 1/5 | +0.16 | — (—) | +2.08 · 50% / 91% · +0.2491 vs +0.2477 | prior | Low | E0 calibrated prior, depth 2, K 300.0 |
| FX | 9801 | -0.000001 (-2.7) | 0/5 | -0.29 | — (—) | -0.38 · 51% / 91% · +0.2504 vs +0.2501 | prior | Low | E0 calibrated prior, depth 2, K 300.0 |
| COMMODITY | 9770 | +0.000001 (+1.4) | 3/5 | +0.99 | — (—) | -0.23 · 51% / 91% · +0.2502 vs +0.2503 | learned | Medium | E0 calibrated prior, depth 6, K 30.0 |
| INDEX | 8019 | -0.000009 (-1.5) | 0/5 | -0.41 | — (—) | -0.35 · 51% / 90% · +0.2476 vs +0.2456 | prior | Low | E0 calibrated prior, depth 2, K 300.0 |
| TREASURY | 3564 | -0.000001 (-1.3) | 3/5 | +0.30 | — (—) | +0.60 · 48% / 88% · +0.2650 vs +0.2505 | prior | Low | E0 calibrated prior, depth 2, K 300.0 |
| CRYPTO | 1137 | -0.000023 (-0.2) | 1/3 | -23.99 | — (—) | +0.17 · 42% / 85% · +0.2530 vs +0.2527 | prior | Low | E0 calibrated prior, depth 2, K 300.0 |
| CORP_BOND | 891 | -0.000005 (-1.8) | 1/5 | +0.29 | — (—) | +0.05 · 50% / 93% · +0.2417 vs +0.2427 | prior | Low | E0 calibrated prior, depth 2, K 300.0 |

All classes: MSE gain -0.000007 (t -1.4), slope +0.08, 90% coverage 91%.

Nested choices by era: 2009–12: COMMODITY E0/d2/K30, TREASURY E1/d6/K300, CORP_BOND E2/d3/K30, ETF E2/d4/K30000, INDEX E2/d2/K30, EQUITY E4/d3/K300, FX E4/d3/K3000; 2013–16: COMMODITY E0/d6/K300, FX E0/d6/K30, INDEX E0/d6/K30, ETF E1/d4/K300, CORP_BOND E2/d1/K30, EQUITY E2/d2/K30, TREASURY E2/d6/K300; 2017–20: COMMODITY E0/d4/K3000, EQUITY E2/d2/K30, CORP_BOND E3/d6/K300, CRYPTO E3/d1/K30, ETF E3/d6/K3000, FX E3/d3/K3000, TREASURY E3/d6/K3000, INDEX E4/d6/K3000; 2021–24: COMMODITY E0/d6/K30, ETF E0/d6/K300, FX E0/d6/K30, INDEX E0/d6/K30, TREASURY E0/d6/K300, EQUITY E1/d6/K3000, CORP_BOND E2/d4/K3000, CRYPTO E2/d1/K30; 2025–: COMMODITY E0/d6/K30, EQUITY E0/d3/K30, ETF E0/d6/K30000, FX E0/d6/K3000, INDEX E0/d6/K3000, TREASURY E0/d3/K3000, CORP_BOND E4/d2/K30, CRYPTO E4/d1/K30

