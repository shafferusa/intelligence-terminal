# Shaffer Hedge: the audit

Generated 2026-09-25 14:31:39 from the real research store (data to 2026-09-24); 330 case-horizons with history, run time 8.6 minutes. Every result is walk-forward: the hedge is sized at each month-start with information available then (trailing-year betas, structural DV01/CS01/currency exposure, the option's delta from that day's Cboe volatility index) for 100% of the exposure, and judged on what happened over the following horizon. A $500,000 holding is the book.

## 1–3. Risk factors, instruments and their sizing units

| Factor | Unit |
|---|---|
| MKT | $ per 100% S&P 500 move (beta-dollars) |
| RATE | $ per +1bp |
| REAL | $ per +1bp |
| CREDIT | $ per +1bp spread |
| FX | $ per 100% currency move (currency dollars) |
| CMD | $ per 100% commodity move |
| CRYPTO | $ per 100% bitcoin move |
| VOL | $ per +1 VIX point |
| IDIO | $ per 100% residual move |
| SEC | $ per 100% sector-spread move |
| IND | $ per 100% industry-spread move |
| STY | $ per 100% style-spread move |

| Product type | Status | Risk unit | Sizing rule | Reason / limitation |
|---|---|---|---|---|
| Cash (USD) | SUPPORTED | none (base currency) | — | Cash earns no interest in the ledger; foreign-currency cash is held through FX pairs. |
| Money-market funds | PROXY | DV01 (≈0.1y) | DV01 | No money-market fund NAV/yield data; SGOV (0–3 month T-bill ETF) stands in. |
| Treasury bills | PROXY | DV01 | DV01 | Individual bills are not traded in the ledger; T-bill ETFs are. |
| Common stock (long) | SUPPORTED | beta-$ (market, sector, industry), idiosyncratic $ | shares = beta-$ ÷ (price × β) |  |
| Short stock | SUPPORTED | negative beta-$ | shares = beta-$ ÷ (price × β) | Borrow availability and hard-to-borrow fees are not available: general-collateral borrow is assumed. |
| Preferred stock | PROXY | DV01, CS01 | DV01 / CS01 | Issue-level preferreds (call schedules, dividend coverage) are not modelled; PFF is. |
| REITs | PROXY | beta-$, rate sensitivity (empirical) | beta-$ | FFO/AFFO and NAV premium are not available from SEC companyfacts in a consistent form. |
| ADRs / foreign equities | PARTIAL | beta-$ + currency $ (look-through) | beta-$; FX notional | ADR/local-share basis and country risk premia are not modelled. |
| ETFs | SUPPORTED | beta-$ / DV01 / CS01 / commodity $ by what it holds | per risk unit | Premium/discount to NAV and flows are not available. |
| Leveraged ETFs | SUPPORTED | beta-$ (≈ leverage × index beta, daily) | beta-$ |  |
| Inverse ETFs | SUPPORTED | negative beta-$ (daily reset) | beta-$ |  |
| ETNs | PARTIAL | VIX-futures $ per VIX point (empirical) | vega-equivalent $ | Issuer (Barclays) credit risk is not modelled. |
| Closed-end funds | NOT_SUPPORTED | — | — | No NAV series: the discount/premium, the main CEF signal, cannot be computed. |
| Government bonds | MODELLED | DV01 by key rate (2/5/10/30Y) | DV01 |  |
| TIPS / inflation-linked | PROXY | real-yield DV01 | real DV01 | Individual TIPS and index ratios are not modelled. |
| Municipal bonds | PROXY | DV01 | DV01 | Tax-equivalent yield and issuer credit are not modelled. |
| Investment-grade corporates | PROXY | DV01 + CS01 (IG OAS) | DV01 / CS01 | Issuer-level bonds are not available. |
| High-yield bonds | PROXY | DV01 + CS01 (HY OAS) | CS01 | Default probabilities and recoveries are not available. |
| Floating-rate notes | PROXY | CS01 (spread duration ≈2y), DV01 ≈0 | CS01 |  |
| Bank / leveraged loans | PROXY | CS01 (HY), DV01 ≈0 | CS01 |  |
| Agency MBS | PROXY | DV01 (negative convexity not modelled) | DV01 | No OAS, prepayment or pool data: negative convexity is only visible empirically. |
| ABS | NOT_SUPPORTED | — | — | No collateral, subordination or ABS spread data. |
| Convertible bonds | PROXY | beta-$ (empirical) | beta-$ | The bond-floor + option decomposition needs issue terms, which are not available. |
| Equity index futures | MODELLED | beta-$ = price × multiplier × β | contracts = beta-$ ÷ (F × multiplier × β) | Priced at fair value S·e^{(r−q)T} (index close, 3-month bill, trailing dividend yield of the tracking ETF), not a futures quote. |
| Single-stock futures | NOT_SUPPORTED | — | — | No US single-stock futures have traded since OneChicago closed (2020). |
| Treasury futures | MODELLED | DV01 per contract at the CTD key rate | contracts = DV01 ÷ DV01 per contract | Modelled as the forward price of the 6% notional coupon at the approximate cheapest-to-deliver maturity on the Treasury curve; conversion factors, delivery options and the actual CTD are not modelled. |
| Commodity futures | ANALYSIS_ONLY | commodity $ = price × multiplier | contracts = commodity $ ÷ (price × multiplier) | Only continuous front-month series are available: without per-contract prices a held position would book roll gaps as P&L. Commodity ETFs (USO, UNG, GLD, SLV, CPER, DBA, DBC) are used for hedging instead. |
| FX futures | MODELLED | currency $ = contract size × price | contracts = currency $ ÷ (size × price) | Priced by covered interest parity from spot and FRED short rates (CHF's rate series ended in 2024, so 6S is ineligible). |
| Crypto futures | NOT_SUPPORTED | — | — | No futures basis or term-structure data; BITO (futures ETF) and IBIT are the tradeable proxies. |
| FX spot | SUPPORTED | currency $ | notional = currency exposure |  |
| FX forwards | MODELLED | currency $ | notional = currency exposure to remove | Covered interest parity with FRED short rates (USD 3M bill, ECB deposit rate, SONIA, monthly JPY/CAD/AUD call rates). |
| NDFs | NOT_SUPPORTED | — | — | No onshore/offshore rate or NDF-point data (INR, CNY). |
| Commodity forwards | NOT_SUPPORTED | — | — | No storage, convenience-yield or forward-curve data. |
| Equity calls | MODELLED | delta-$, gamma, vega, theta | contracts = delta-$ ÷ (S × 100 × |Δ| × β) | Other single stocks (e.g. NVDA) have no implied-volatility data and are not eligible; flat volatility (no skew). |
| Equity puts | MODELLED | delta-$, gamma, vega, theta | contracts = delta-$ ÷ (S × 100 × |Δ| × β) | As calls; OTM put premiums are likely understated without skew. |
| Index / ETF options | MODELLED | delta-$, gamma, vega, theta | contracts = delta-$ ÷ (S × 100 × |Δ| × β) | Implied volatility is the index's 30-day (and for the S&P 500 the 3-month) level, flat across strikes: no skew data. |
| FX options | NOT_SUPPORTED | — | — | No FX implied-volatility data (EVZ was discontinued in 2025). |
| Commodity options | MODELLED | delta-$, vega | delta-$ | ETF options only; options on futures are not modelled. |
| Crypto options | NOT_SUPPORTED | — | — | No crypto implied-volatility data. |
| Warrants | NOT_SUPPORTED | — | — | No warrant terms or dilution data. |
| Interest-rate swaps | ANALYSIS_ONLY | DV01 | notional = DV01 ÷ DV01 per $ | No SOFR swap curve: swap spreads are unknown, so a held swap cannot be valued faithfully. |
| OIS | NOT_SUPPORTED | — | — | No OIS curve. |
| FRAs | NOT_SUPPORTED | — | — | No forward-rate (term SOFR/futures) curve. |
| Caps | NOT_SUPPORTED | — | — | No interest-rate volatility data. |
| Floors | NOT_SUPPORTED | — | — | No interest-rate volatility data. |
| Swaptions | NOT_SUPPORTED | — | — | No swaption volatility surface. |
| Total return swaps | NOT_SUPPORTED | — | — | No dealer financing spreads; the equity-index future carries the same exposure and is supported. |
| Equity swaps | NOT_SUPPORTED | — | — | As TRS. |
| Commodity swaps | NOT_SUPPORTED | — | — | No commodity forward curves. |
| FX swaps | NOT_SUPPORTED | — | — | Equivalent to spot + forward; the forward is supported. |
| Cross-currency swaps | NOT_SUPPORTED | — | — | No cross-currency basis data. |
| Inflation swaps | NOT_SUPPORTED | — | — | No zero-coupon inflation swap quotes (TIPS breakevens are available for analysis). |
| CDS — buy protection | NOT_SUPPORTED | — | — | No single-name CDS spreads. |
| CDS — sell protection | NOT_SUPPORTED | — | — | No single-name CDS spreads. |
| CDX / iTraxx | NOT_SUPPORTED | — | — | No index quotes; credit risk is hedged with HYG/JNK/LQD sized by CS01. |
| Variance swaps | ANALYSIS_ONLY | vega notional | vega notional | The VIX approximates the 30-day fair strike, but there is no dealer market data to value a held swap. |
| Volatility swaps | NOT_SUPPORTED | — | — | No volatility-swap quotes. |
| VIX futures | NOT_SUPPORTED | — | — | No VIX futures curve (the Cboe futures host is not reachable); VXX is the tradeable proxy. |
| VIX options | NOT_SUPPORTED | — | — | Needs the VIX futures curve. |
| Crypto spot | SUPPORTED | crypto $ | notional | Shorting spot crypto is not supported (no borrow); IBIT/BITO can be shorted. |
| Crypto perpetuals | NOT_SUPPORTED | — | — | No funding-rate or open-interest data. |
| Structured notes | NOT_SUPPORTED | — | — | No payoff model is registered for any note; FinSim2 refuses rather than guesses. |

## 4. Exact hedge math

```
Shaffer Hedge: identify the risk, set the target, find products that carry that risk, size each in its own risk
unit, optimise the package, score it, then (only if verified) apply a capped ML adjustment.

    R_after = R_before + B q                                     (B: exposures per unit of each hedge product)
    min_q  (γh/2)·(R + Bq − R*)ᵀ Σ (R + Bq − R*) + Σ_j c_j|q_j| + Σ_j k_j|q_j|^1.5
           risk mismatch (incl. every hedge's own residual,    cost over the     market impact (square-root law,
           i.e. basis risk, via its IDIO factor)               horizon           the liquidity penalty)
    γ = A / NAV (A = relative risk aversion, default 2), h = horizon in sessions, Σ = daily factor covariance.
    Factors the objective does not target keep R*_f = R_f, so a hedge that adds sector or currency exposure is penalised.
    Turnover: existing hedges enter R, so the optimiser only adds what is missing.

    SH_j = 100·tanh(E_j·Q_j·L_j·R_j·B_j·T_j / 1.0)
    E: realised ÷ expected variance reduction in the walk-forward history (−1…1.25); expected reduction if no history
    Q: value of the risk removed ÷ (value + expected cost); value = (γh/2)·ΔVar
    L: 1 / (1 + participation/10%) with participation = hedge notional ÷ average daily traded value
    R: realised reduction in windows that started in today's regime ÷ all windows, shrunk by n/(n+20), in [0.5, 1.5]
    B: share of the target risk the product can remove at all: ρ² of the product with the targeted P&L
    T: tail suitability (crash objective): hedge gain in the −20% scenario ÷ the gain of a linear hedge with the same
       delta, in [0.5, 1.5]; 1 for other objectives
```

## 21–25. Walk-forward results

Realised reduction = 1 − Σ(hedged daily P&L)² ÷ Σ(unhedged daily P&L)² over every window. Effectiveness = realised ÷ the reduction expected from the trailing year at each start (linear hedges). Tail = the worst 10% of windows for the unhedged book.

**22. By hedge product**

| Group | Cases | Indep. windows | Median realised variance reduction | Median effectiveness | Median tail-loss reduction | Mean hedge P&L per window | Mean upside given up |
|---|---|---|---|---|---|---|---|
| Stock short | 9 | 1254 | +100.0% | 1.00 | +99.8% | -15,163 | 37,734 |
| Credit ETF short | 12 | 1672 | +95.5% | 1.02 | +98.5% | -3,537 | 8,956 |
| Treasury ETF short | 12 | 1672 | +81.2% | 1.00 | +98.7% | -1,540 | 9,291 |
| Treasury future | 36 | 5016 | +69.9% | — | +86.5% | -227 | 7,308 |
| Sector ETF short | 15 | 2090 | +63.9% | 1.06 | +77.0% | -11,943 | 26,108 |
| Equity index future | 54 | 7524 | +55.3% | 1.01 | +64.5% | -9,589 | 19,736 |
| ETF short | 36 | 5016 | +53.9% | 1.01 | +60.7% | -10,153 | 19,329 |
| Inverse ETF | 36 | 5016 | +53.8% | 1.01 | +63.0% | -8,271 | 17,579 |
| Crypto ETF short | 6 | 206 | +48.5% | 1.04 | +142.6% | -13,889 | 90,753 |
| Single-stock put | 9 | 1254 | +34.8% | — | +148.3% | -15,017 | 36,040 |
| Index/ETF put | 90 | 12521 | -3.8% | — | +108.1% | -11,681 | 24,227 |
| FX forward | 9 | 1254 | -20.0% | — | +18.0% | 1,685 | 5,341 |
| Commodity ETF put | 6 | 836 | -30.2% | — | +197.3% | -6,905 | 39,846 |

**23. By risk**

| Group | Cases | Indep. windows | Median realised variance reduction | Median effectiveness | Median tail-loss reduction | Mean hedge P&L per window | Mean upside given up |
|---|---|---|---|---|---|---|---|
| Single name | 18 | 2508 | +100.0% | 1.00 | +128.4% | -15,090 | 36,887 |
| Credit (CS01) | 12 | 1672 | +95.5% | 1.02 | +98.5% | -3,537 | 8,956 |
| Rates (DV01) | 48 | 6688 | +70.2% | 1.00 | +88.0% | -555 | 7,804 |
| Crypto | 6 | 206 | +48.5% | 1.04 | +142.6% | -13,889 | 90,753 |
| Equity beta | 231 | 32167 | +43.6% | 1.01 | +71.8% | -10,439 | 21,500 |
| Currency | 9 | 1254 | -20.0% | — | +18.0% | 1,685 | 5,341 |
| Commodity | 6 | 836 | -30.2% | — | +197.3% | -6,905 | 39,846 |

**25. By horizon**

| Group | Cases | Indep. windows | Median realised variance reduction | Median effectiveness | Median tail-loss reduction | Mean hedge P&L per window | Mean upside given up |
|---|---|---|---|---|---|---|---|
| 3M | 110 | 6399 | +49.1% | 1.02 | +71.7% | -18,916 | 34,729 |
| 1M | 110 | 19412 | +48.9% | 1.02 | +84.9% | -6,546 | 19,740 |
| 1W | 110 | 19520 | +46.5% | 0.98 | +92.8% | -554 | 8,660 |

**24. By regime at the start of the window** (all cases pooled; variance-weighted)

| Regime | Windows | Realised variance reduction |
|---|---|---|
| Liquidity expansion | 58128 | +20.3% |
| Expansion | 53250 | +18.5% |
| Bull market | 48687 | +11.3% |
| Low inflation | 43221 | +10.3% |
| Low volatility | 33639 | -0.2% |
| Rising rates | 29832 | +21.8% |
| Falling rates | 28296 | +18.6% |
| High volatility | 24489 | +41.7% |
| High inflation | 14907 | +42.6% |
| Bear market | 9441 | +47.0% |
| Recession | 4878 | +38.1% |

## 20, 26. Baselines and cost against risk reduction

Per case: realised variance reduction of the static rule (the Raw Shaffer Hedge ratio) against fixed 25% / 50% hedges and the trailing minimum-variance ratio, on the same windows (linear hedges).

| Book | Hedge | Horizon | Static rule | Fixed 25% | Fixed 50% | Min-variance | Mean hedge P&L | Residual $/day | Unhedged $/day |
|---|---|---|---|---|---|---|---|---|---|
| SPY | SPY short | 1W | +100.0% | +43.8% | +75.0% | +100.0% | -892 | 6 | 4,670 |
| SPY | SPY short | 1M | +100.0% | +43.8% | +75.0% | +100.0% | -6,711 | 6 | 5,306 |
| SPY | SPY short | 3M | +100.0% | +43.8% | +75.0% | +100.0% | -19,844 | 6 | 5,281 |
| SPY | ES future | 1W | +99.7% | +43.7% | +74.8% | +99.6% | -713 | 272 | 4,670 |
| SPY | ES future | 1M | +99.7% | +43.8% | +75.0% | +99.6% | -5,842 | 309 | 5,306 |
| SPY | ES future | 3M | +99.7% | +43.8% | +75.1% | +99.6% | -17,264 | 311 | 5,281 |
| SPY | SH (inverse ETF) long | 1W | +99.5% | +43.7% | +74.9% | +99.4% | -566 | 333 | 4,670 |
| SPY | SH (inverse ETF) long | 1M | +99.5% | +43.8% | +74.9% | +99.4% | -5,556 | 378 | 5,306 |
| SPY | SH (inverse ETF) long | 3M | +99.5% | +43.8% | +75.0% | +99.4% | -16,407 | 379 | 5,281 |
| QQQ | SPY short | 1W | +84.2% | +37.5% | +64.0% | +84.2% | -896 | 2,296 | 5,781 |
| QQQ | SPY short | 1M | +86.7% | +38.8% | +66.2% | +86.7% | -7,470 | 2,382 | 6,520 |
| QQQ | SPY short | 3M | +86.3% | +39.0% | +66.4% | +86.3% | -22,309 | 2,403 | 6,503 |
| QQQ | ES future | 1W | +84.3% | +37.5% | +64.1% | +84.3% | -674 | 2,289 | 5,781 |
| QQQ | ES future | 1M | +86.7% | +38.9% | +66.4% | +86.5% | -6,406 | 2,379 | 6,520 |
| QQQ | ES future | 3M | +86.3% | +39.1% | +66.5% | +86.1% | -19,152 | 2,407 | 6,503 |
| QQQ | SH (inverse ETF) long | 1W | +84.0% | +37.6% | +64.1% | +84.1% | -487 | 2,311 | 5,781 |
| QQQ | SH (inverse ETF) long | 1M | +86.3% | +38.9% | +66.2% | +86.2% | -6,009 | 2,415 | 6,520 |
| QQQ | SH (inverse ETF) long | 3M | +85.9% | +39.1% | +66.4% | +85.8% | -17,963 | 2,443 | 6,503 |
| IWM | SPY short | 1W | +71.4% | +32.8% | +55.6% | +71.4% | -1,222 | 3,163 | 5,915 |
| IWM | SPY short | 1M | +75.7% | +33.7% | +57.5% | +75.8% | -7,754 | 3,363 | 6,825 |
| IWM | SPY short | 3M | +75.3% | +33.6% | +57.4% | +75.4% | -22,906 | 3,369 | 6,777 |
| IWM | ES future | 1W | +71.7% | +32.8% | +55.7% | +71.8% | -1,018 | 3,148 | 5,915 |
| IWM | ES future | 1M | +75.8% | +33.7% | +57.6% | +75.8% | -6,766 | 3,354 | 6,825 |
| IWM | ES future | 3M | +75.4% | +33.8% | +57.6% | +75.4% | -19,976 | 3,362 | 6,777 |
| IWM | SH (inverse ETF) long | 1W | +71.1% | +32.7% | +55.5% | +71.2% | -859 | 3,182 | 5,915 |
| IWM | SH (inverse ETF) long | 1M | +75.5% | +33.7% | +57.5% | +75.4% | -6,456 | 3,378 | 6,825 |
| IWM | SH (inverse ETF) long | 3M | +75.1% | +33.7% | +57.5% | +75.0% | -19,051 | 3,384 | 6,777 |
| NVDA | SPY short | 1W | +34.0% | +14.8% | +25.3% | +34.1% | -703 | 11,852 | 14,587 |
| NVDA | SPY short | 1M | +40.7% | +20.0% | +33.5% | +40.7% | -10,255 | 10,889 | 14,144 |
| NVDA | SPY short | 3M | +39.8% | +20.1% | +33.4% | +39.7% | -31,697 | 10,965 | 14,129 |
| NVDA | ES future | 1W | +34.3% | +14.8% | +25.5% | +34.3% | -333 | 11,826 | 14,587 |
| NVDA | ES future | 1M | +40.9% | +20.1% | +33.6% | +40.8% | -8,513 | 10,870 | 14,144 |
| NVDA | ES future | 3M | +39.9% | +20.2% | +33.6% | +39.7% | -26,504 | 10,953 | 14,129 |
| NVDA | SH (inverse ETF) long | 1W | +33.5% | +14.7% | +25.2% | +33.6% | -19 | 11,891 | 14,587 |
| NVDA | SH (inverse ETF) long | 1M | +40.4% | +20.0% | +33.4% | +40.4% | -7,755 | 10,916 | 14,144 |
| NVDA | SH (inverse ETF) long | 3M | +39.4% | +20.1% | +33.4% | +39.3% | -24,204 | 10,996 | 14,129 |
| AAPL | SPY short | 1W | +43.5% | +19.7% | +33.6% | +43.6% | -478 | 6,007 | 7,993 |
| AAPL | SPY short | 1M | +45.6% | +20.9% | +35.4% | +45.6% | -7,295 | 6,596 | 8,941 |
| AAPL | SPY short | 3M | +45.5% | +21.1% | +35.7% | +45.4% | -21,867 | 6,593 | 8,931 |
| AAPL | ES future | 1W | +43.7% | +19.8% | +33.7% | +43.7% | -268 | 5,996 | 7,993 |
| AAPL | ES future | 1M | +45.3% | +20.9% | +35.5% | +45.1% | -6,281 | 6,612 | 8,941 |
| AAPL | ES future | 3M | +45.1% | +21.2% | +35.7% | +44.8% | -18,853 | 6,617 | 8,931 |
| AAPL | SH (inverse ETF) long | 1W | +43.8% | +19.9% | +33.8% | +43.8% | -90 | 5,994 | 7,993 |
| AAPL | SH (inverse ETF) long | 1M | +45.1% | +20.9% | +35.4% | +44.9% | -5,915 | 6,626 | 8,941 |
| AAPL | SH (inverse ETF) long | 3M | +44.9% | +21.1% | +35.7% | +44.6% | -17,730 | 6,629 | 8,931 |
| MSFT | SPY short | 1W | +39.5% | +18.1% | +30.7% | +39.5% | -505 | 6,446 | 8,288 |
| MSFT | SPY short | 1M | +53.2% | +23.9% | +40.7% | +53.2% | -7,279 | 5,708 | 8,345 |
| MSFT | SPY short | 3M | +53.9% | +24.4% | +41.5% | +53.9% | -21,918 | 5,620 | 8,274 |
| MSFT | ES future | 1W | +40.3% | +18.3% | +31.1% | +40.3% | -288 | 6,407 | 8,288 |
| MSFT | ES future | 1M | +53.6% | +24.1% | +41.1% | +53.6% | -6,253 | 5,683 | 8,345 |
| MSFT | ES future | 3M | +54.2% | +24.6% | +41.8% | +54.2% | -18,859 | 5,598 | 8,274 |
| MSFT | SH (inverse ETF) long | 1W | +39.4% | +18.1% | +30.7% | +39.4% | -113 | 6,455 | 8,288 |
| MSFT | SH (inverse ETF) long | 1M | +53.2% | +24.0% | +40.9% | +53.2% | -5,897 | 5,709 | 8,345 |
| MSFT | SH (inverse ETF) long | 3M | +53.8% | +24.5% | +41.6% | +53.8% | -17,771 | 5,624 | 8,274 |
| JPM | SPY short | 1W | +52.6% | +23.0% | +39.4% | +52.6% | -1,253 | 5,131 | 7,455 |
| JPM | SPY short | 1M | +52.6% | +22.6% | +38.9% | +52.6% | -7,919 | 5,857 | 8,507 |
| JPM | SPY short | 3M | +51.6% | +22.1% | +38.1% | +51.7% | -23,434 | 5,856 | 8,421 |
| JPM | ES future | 1W | +52.3% | +22.9% | +39.3% | +52.4% | -1,078 | 5,149 | 7,455 |
| JPM | ES future | 1M | +52.8% | +22.7% | +39.1% | +52.9% | -7,066 | 5,845 | 8,507 |
| JPM | ES future | 3M | +51.8% | +22.3% | +38.3% | +52.0% | -20,901 | 5,844 | 8,421 |
| JPM | SH (inverse ETF) long | 1W | +52.8% | +23.0% | +39.5% | +52.9% | -949 | 5,124 | 7,455 |
| JPM | SH (inverse ETF) long | 1M | +52.8% | +22.7% | +39.1% | +52.9% | -6,888 | 5,846 | 8,507 |
| JPM | SH (inverse ETF) long | 3M | +51.8% | +22.3% | +38.3% | +51.9% | -20,365 | 5,844 | 8,421 |
| XOM | SPY short | 1W | +35.2% | +14.4% | +25.1% | +35.1% | -536 | 5,902 | 7,332 |
| XOM | SPY short | 1M | +31.1% | +13.6% | +23.3% | +31.0% | -5,474 | 6,657 | 8,019 |
| XOM | SPY short | 3M | +30.0% | +13.2% | +22.6% | +29.9% | -16,280 | 6,701 | 8,010 |
| XOM | ES future | 1W | +34.9% | +14.3% | +24.9% | +34.7% | -430 | 5,913 | 7,332 |
| XOM | ES future | 1M | +30.8% | +13.6% | +23.2% | +30.7% | -4,951 | 6,669 | 8,019 |
| XOM | ES future | 3M | +29.8% | +13.2% | +22.6% | +29.6% | -14,692 | 6,712 | 8,010 |
| XOM | SH (inverse ETF) long | 1W | +34.7% | +14.3% | +24.8% | +34.4% | -361 | 5,925 | 7,332 |
| XOM | SH (inverse ETF) long | 1M | +30.9% | +13.6% | +23.3% | +30.7% | -4,907 | 6,667 | 8,019 |
| XOM | SH (inverse ETF) long | 3M | +29.8% | +13.3% | +22.7% | +29.7% | -14,517 | 6,710 | 8,010 |
| XLK | SPY short | 1W | +80.5% | +35.3% | +60.4% | +80.5% | -830 | 2,803 | 6,345 |
| XLK | SPY short | 1M | +84.7% | +37.9% | +64.6% | +84.7% | -7,710 | 2,748 | 7,035 |
| XLK | SPY short | 3M | +84.7% | +38.0% | +64.8% | +84.7% | -22,992 | 2,739 | 7,008 |
| XLK | ES future | 1W | +80.5% | +35.2% | +60.4% | +80.5% | -597 | 2,800 | 6,345 |
| XLK | ES future | 1M | +84.9% | +38.0% | +64.8% | +84.8% | -6,580 | 2,733 | 7,035 |
| XLK | ES future | 3M | +84.9% | +38.2% | +65.0% | +84.8% | -19,653 | 2,724 | 7,008 |
| XLK | SH (inverse ETF) long | 1W | +80.4% | +35.3% | +60.5% | +80.3% | -393 | 2,813 | 6,345 |
| XLK | SH (inverse ETF) long | 1M | +84.5% | +37.9% | +64.6% | +84.4% | -6,132 | 2,770 | 7,035 |
| XLK | SH (inverse ETF) long | 3M | +84.5% | +38.1% | +64.9% | +84.4% | -18,326 | 2,761 | 7,008 |
| EFA | SPY short | 1W | +68.3% | +31.2% | +53.0% | +68.3% | -1,029 | 2,715 | 4,822 |
| EFA | SPY short | 1M | +73.6% | +32.2% | +55.1% | +73.6% | -6,223 | 2,803 | 5,452 |
| EFA | SPY short | 3M | +73.0% | +31.7% | +54.5% | +73.1% | -18,376 | 2,806 | 5,406 |
| EFA | ES future | 1W | +68.1% | +31.2% | +52.9% | +68.3% | -882 | 2,722 | 4,822 |
| EFA | ES future | 1M | +73.4% | +32.2% | +55.2% | +73.6% | -5,515 | 2,810 | 5,452 |
| EFA | ES future | 3M | +73.0% | +31.8% | +54.5% | +73.1% | -16,282 | 2,811 | 5,406 |
| EFA | SH (inverse ETF) long | 1W | +67.9% | +31.2% | +52.9% | +68.1% | -773 | 2,731 | 4,822 |
| EFA | SH (inverse ETF) long | 1M | +73.4% | +32.2% | +55.2% | +73.5% | -5,345 | 2,811 | 5,452 |
| EFA | SH (inverse ETF) long | 3M | +73.0% | +31.8% | +54.6% | +73.0% | -15,778 | 2,811 | 5,406 |
| EEM | SPY short | 1W | +51.6% | +24.2% | +40.9% | +51.7% | -1,139 | 4,097 | 5,891 |
| EEM | SPY short | 1M | +59.5% | +26.2% | +44.8% | +59.5% | -6,697 | 4,227 | 6,638 |
| EEM | SPY short | 3M | +58.5% | +25.7% | +44.0% | +58.5% | -19,886 | 4,226 | 6,556 |
| EEM | ES future | 1W | +52.1% | +24.3% | +41.1% | +52.2% | -983 | 4,079 | 5,891 |
| EEM | ES future | 1M | +59.6% | +26.3% | +45.0% | +59.7% | -5,938 | 4,221 | 6,638 |
| EEM | ES future | 3M | +58.6% | +25.9% | +44.2% | +58.7% | -17,664 | 4,218 | 6,556 |
| EEM | SH (inverse ETF) long | 1W | +51.4% | +24.2% | +40.8% | +51.6% | -863 | 4,106 | 5,891 |
| EEM | SH (inverse ETF) long | 1M | +59.4% | +26.2% | +44.9% | +59.4% | -5,766 | 4,232 | 6,638 |
| EEM | SH (inverse ETF) long | 3M | +58.4% | +25.8% | +44.2% | +58.5% | -17,174 | 4,228 | 6,556 |
| AMZN | SPY short | 1W | +40.2% | +18.0% | +30.7% | +40.2% | -1,261 | 6,884 | 8,901 |
| AMZN | SPY short | 1M | +37.6% | +17.1% | +29.0% | +37.7% | -8,161 | 8,197 | 10,379 |
| AMZN | SPY short | 3M | +36.7% | +17.4% | +29.3% | +36.8% | -24,295 | 8,198 | 10,304 |
| AMZN | ES future | 1W | +40.5% | +18.1% | +30.8% | +40.6% | -1,001 | 6,867 | 8,901 |
| AMZN | ES future | 1M | +37.7% | +17.1% | +29.1% | +37.7% | -6,940 | 8,195 | 10,379 |
| AMZN | ES future | 3M | +36.5% | +17.4% | +29.3% | +36.6% | -20,655 | 8,208 | 10,304 |
| AMZN | SH (inverse ETF) long | 1W | +40.1% | +18.0% | +30.8% | +40.2% | -786 | 6,889 | 8,901 |
| AMZN | SH (inverse ETF) long | 1M | +37.4% | +17.1% | +29.0% | +37.4% | -6,438 | 8,211 | 10,379 |
| AMZN | SH (inverse ETF) long | 3M | +36.3% | +17.3% | +29.2% | +36.3% | -19,142 | 8,225 | 10,304 |
| QQQ | NQ future | 1W | +99.7% | +43.6% | +74.7% | +98.0% | -641 | 302 | 5,781 |
| QQQ | NQ future | 1M | +99.7% | +43.6% | +74.8% | +98.2% | -7,699 | 334 | 6,520 |
| QQQ | NQ future | 3M | +99.7% | +43.6% | +74.8% | +98.3% | -22,981 | 335 | 6,503 |
| NVDA | NQ future | 1W | +46.7% | +18.6% | +32.6% | +47.1% | -212 | 10,653 | 14,587 |
| NVDA | NQ future | 1M | +52.8% | +23.4% | +40.0% | +52.2% | -10,457 | 9,717 | 14,144 |
| NVDA | NQ future | 3M | +52.4% | +23.4% | +40.0% | +51.9% | -32,164 | 9,743 | 14,129 |
| AAPL | NQ future | 1W | +51.8% | +22.7% | +38.9% | +53.0% | -284 | 5,550 | 7,993 |
| AAPL | NQ future | 1M | +55.3% | +24.0% | +41.2% | +54.9% | -7,654 | 5,977 | 8,941 |
| AAPL | NQ future | 3M | +55.3% | +24.1% | +41.3% | +54.9% | -22,834 | 5,969 | 8,931 |
| MSFT | NQ future | 1W | +49.8% | +21.6% | +37.1% | +48.2% | -322 | 5,874 | 8,288 |
| MSFT | NQ future | 1M | +62.0% | +27.0% | +46.4% | +60.4% | -7,678 | 5,145 | 8,345 |
| MSFT | NQ future | 3M | +63.0% | +27.5% | +47.2% | +61.3% | -22,891 | 5,035 | 8,274 |
| XLK | NQ future | 1W | +92.6% | +40.3% | +69.2% | +91.1% | -543 | 1,724 | 6,345 |
| XLK | NQ future | 1M | +94.8% | +41.8% | +71.5% | +93.0% | -7,931 | 1,608 | 7,035 |
| XLK | NQ future | 3M | +94.7% | +41.7% | +71.3% | +93.0% | -23,644 | 1,609 | 7,008 |
| AMZN | NQ future | 1W | +50.5% | +21.6% | +37.3% | +50.9% | -866 | 6,261 | 8,901 |
| AMZN | NQ future | 1M | +48.9% | +20.5% | +35.5% | +49.5% | -8,307 | 7,417 | 10,379 |
| AMZN | NQ future | 3M | +49.1% | +20.9% | +36.0% | +49.3% | -24,924 | 7,354 | 10,304 |
| NVDA | SMH short | 1W | +61.8% | +25.8% | +44.7% | +59.8% | -1,530 | 9,014 | 14,587 |
| NVDA | SMH short | 1M | +61.7% | +28.3% | +48.1% | +59.4% | -14,541 | 8,758 | 14,144 |
| NVDA | SMH short | 3M | +61.5% | +28.4% | +48.1% | +58.9% | -44,892 | 8,770 | 14,129 |
| AAPL | XLK short | 1W | +52.7% | +23.6% | +40.2% | +55.1% | -769 | 5,499 | 7,993 |
| AAPL | XLK short | 1M | +56.8% | +24.6% | +42.3% | +57.3% | -8,963 | 5,873 | 8,941 |
| AAPL | XLK short | 3M | +56.8% | +24.8% | +42.5% | +57.0% | -26,748 | 5,869 | 8,931 |
| MSFT | XLK short | 1W | +53.3% | +23.1% | +39.7% | +52.1% | -797 | 5,665 | 8,288 |
| MSFT | XLK short | 1M | +63.9% | +27.8% | +47.7% | +63.1% | -8,906 | 5,012 | 8,345 |
| MSFT | XLK short | 3M | +64.9% | +28.4% | +48.7% | +64.1% | -26,596 | 4,899 | 8,274 |
| JPM | XLF short | 1W | +81.1% | +34.1% | +58.9% | +78.3% | -996 | 3,241 | 7,455 |
| JPM | XLF short | 1M | +81.0% | +33.1% | +57.7% | +78.6% | -7,239 | 3,708 | 8,507 |
| JPM | XLF short | 3M | +80.6% | +32.8% | +57.2% | +78.0% | -21,356 | 3,707 | 8,421 |
| XOM | XLE short | 1W | +73.7% | +34.4% | +58.1% | -17.7% | -1,007 | 3,757 | 7,332 |
| XOM | XLE short | 1M | +72.0% | +34.0% | +57.4% | -35.0% | -3,784 | 4,247 | 8,019 |
| XOM | XLE short | 3M | +74.7% | +33.9% | +57.7% | +4.1% | -11,023 | 4,031 | 8,010 |
| AAPL | AAPL short (itself) | 1W | +100.0% | +43.8% | +75.0% | +100.0% | -1,332 | 6 | 7,993 |
| AAPL | AAPL short (itself) | 1M | +100.0% | +43.8% | +75.0% | +100.0% | -11,036 | 6 | 8,941 |
| AAPL | AAPL short (itself) | 3M | +100.0% | +43.8% | +75.0% | +100.0% | -33,045 | 6 | 8,931 |
| AMZN | AMZN short (itself) | 1W | +100.0% | +43.8% | +75.0% | +100.0% | -1,128 | 6 | 8,901 |
| AMZN | AMZN short (itself) | 1M | +100.0% | +43.8% | +75.0% | +100.0% | -11,265 | 6 | 10,379 |
| AMZN | AMZN short (itself) | 3M | +100.0% | +43.8% | +75.0% | +100.0% | -33,101 | 6 | 10,304 |
| GOOGL | GOOGL short (itself) | 1W | +100.0% | +43.8% | +75.0% | +100.0% | -2,414 | 6 | 9,227 |
| GOOGL | GOOGL short (itself) | 1M | +100.0% | +43.8% | +75.0% | +100.0% | -10,872 | 6 | 8,763 |
| GOOGL | GOOGL short (itself) | 3M | +100.0% | +43.8% | +75.0% | +100.0% | -32,277 | 6 | 8,737 |
| TLT | IEF short | 1W | +81.2% | +38.9% | +65.4% | +83.0% | -466 | 1,900 | 4,382 |
| TLT | IEF short | 1M | +82.9% | +40.1% | +67.3% | +84.7% | -1,858 | 1,911 | 4,621 |
| TLT | IEF short | 3M | +82.9% | +40.2% | +67.5% | +84.4% | -5,719 | 1,900 | 4,591 |
| IEF | IEF short | 1W | +100.0% | +43.8% | +75.0% | +100.0% | -201 | 6 | 1,879 |
| IEF | IEF short | 1M | +100.0% | +43.8% | +75.0% | +100.0% | -800 | 6 | 2,020 |
| IEF | IEF short | 3M | +100.0% | +43.8% | +75.0% | +100.0% | -2,461 | 6 | 2,016 |
| LQD | IEF short | 1W | +37.9% | +23.1% | +37.2% | +42.5% | -234 | 2,028 | 2,575 |
| LQD | IEF short | 1M | +44.9% | +28.9% | +46.0% | +52.5% | -935 | 1,806 | 2,433 |
| LQD | IEF short | 3M | +45.5% | +28.9% | +46.1% | +52.5% | -2,877 | 1,801 | 2,440 |
| AGG | IEF short | 1W | +68.3% | +41.9% | +67.2% | +78.5% | -169 | 777 | 1,381 |
| AGG | IEF short | 1M | +71.5% | +41.8% | +67.7% | +80.2% | -676 | 807 | 1,511 |
| AGG | IEF short | 3M | +71.8% | +41.7% | +67.6% | +80.1% | -2,080 | 803 | 1,513 |
| HYG | JNK short | 1W | +95.9% | +41.1% | +70.8% | +95.9% | -516 | 456 | 2,246 |
| HYG | JNK short | 1M | +96.8% | +41.5% | +71.4% | +96.8% | -2,245 | 449 | 2,518 |
| HYG | JNK short | 3M | +96.9% | +41.5% | +71.5% | +96.9% | -6,728 | 441 | 2,498 |
| HYG | BKLN short | 1W | -16.7% | +34.2% | +42.8% | — | -513 | 2,426 | 2,246 |
| HYG | BKLN short | 1M | +28.6% | +31.1% | +46.3% | — | -3,141 | 2,127 | 2,518 |
| HYG | BKLN short | 3M | +27.7% | +31.2% | +46.3% | — | -9,230 | 2,124 | 2,498 |
| JNK | HYG short | 1W | +95.5% | +44.4% | +75.1% | +96.0% | -619 | 470 | 2,215 |
| JNK | HYG short | 1M | +96.5% | +44.5% | +75.4% | +96.9% | -2,445 | 464 | 2,493 |
| JNK | HYG short | 3M | +96.6% | +44.5% | +75.5% | +97.0% | -7,255 | 454 | 2,472 |
| LQD | VCIT short | 1W | +84.7% | +38.7% | +65.6% | +85.6% | -663 | 1,008 | 2,575 |
| LQD | VCIT short | 1M | +87.3% | +40.9% | +69.0% | +88.7% | -2,236 | 867 | 2,433 |
| LQD | VCIT short | 3M | +87.5% | +40.9% | +69.1% | +88.6% | -6,852 | 862 | 2,440 |
| EWJ | JPY forward (rolled) | 1W | -32.0% | -2.4% | -8.5% | -0.7% | 397 | 5,795 | 5,043 |
| EWJ | JPY forward (rolled) | 1M | -26.5% | -1.5% | -6.5% | -0.9% | 2,505 | 6,292 | 5,594 |
| EWJ | JPY forward (rolled) | 3M | -26.9% | -1.6% | -6.6% | -0.9% | 7,729 | 6,279 | 5,573 |
| EWG | EUR forward (rolled) | 1W | -17.3% | -1.3% | -4.6% | +0.6% | 17 | 6,637 | 6,127 |
| EWG | EUR forward (rolled) | 1M | -12.9% | -0.6% | -3.0% | -0.4% | 767 | 7,229 | 6,803 |
| EWG | EUR forward (rolled) | 3M | -13.3% | -0.7% | -3.1% | -0.5% | 2,526 | 7,141 | 6,708 |
| EWU | GBP forward (rolled) | 1W | -25.9% | -1.4% | -6.1% | -0.6% | -387 | 5,900 | 5,258 |
| EWU | GBP forward (rolled) | 1M | -19.7% | -0.7% | -4.2% | -0.7% | 356 | 6,352 | 5,807 |
| EWU | GBP forward (rolled) | 3M | -20.0% | -0.7% | -4.3% | -0.4% | 1,253 | 6,324 | 5,774 |
| BTC | BITO short | 1W | +44.9% | +51.6% | +76.3% | — | 9,370 | 10,391 | 13,995 |
| BTC | BITO short | 1M | +48.5% | +53.6% | +79.5% | — | -8,615 | 11,772 | 16,396 |
| BTC | BITO short | 3M | +48.4% | +53.5% | +79.4% | — | -28,488 | 11,813 | 16,441 |
| BTC | IBIT short | 1W | +44.8% | +55.5% | +81.5% | — | 6,274 | 9,629 | 12,956 |
| BTC | IBIT short | 1M | +52.3% | +52.7% | +79.0% | — | -21,784 | 10,474 | 15,162 |
| BTC | IBIT short | 3M | +51.7% | +53.0% | +79.3% | — | -40,094 | 10,503 | 15,119 |

## 27–28. Where it worked and where it failed

**Largest gains from hedging (hedge P&L in the window):**

- NVDA hedged with SPY 5% OTM put (3M) (1M), 2020-02-18 → 2020-03-18: book -158,084, hedged +1,027,289
- USO hedged with USO 5% OTM put (3M) (3M), 2020-02-18 → 2020-05-18: book -529,466, hedged +520,357
- USO hedged with USO 5% OTM put (3M) (3M), 2019-12-16 → 2020-03-18: book -445,866, hedged +561,397
- NVDA hedged with SPY 5% OTM put (3M) (3M), 2019-12-16 → 2020-03-18: book -15,712, hedged +989,170
- USO hedged with USO 5% OTM put (3M) (3M), 2020-01-16 → 2020-04-17: book -462,820, hedged +531,328
- USO hedged with USO 5% OTM put (3M) (1M), 2020-02-18 → 2020-03-18: book -378,525, hedged +459,146
- AAPL hedged with SPY 5% OTM put (3M) (1M), 2020-02-18 → 2020-03-18: book -108,884, hedged +695,764
- AAPL hedged with SPY 5% OTM put (3M) (3M), 2019-12-16 → 2020-03-18: book -39,806, hedged +713,745
- XLK hedged with SPY 5% OTM put (3M) (1M), 2020-02-18 → 2020-03-18: book -135,196, hedged +589,225
- AMZN hedged with SPY 5% OTM put (3M) (3M), 2019-12-16 → 2020-03-18: book +28,447, hedged +745,387

**Largest losses from hedging:**

- BTC hedged with BITO short (3M), 2022-12-15 → 2023-03-20: book +257,477, hedged -117,083
- BTC hedged with IBIT short (3M), 2024-09-19 → 2024-12-18: book +250,019, hedged -104,761
- BTC hedged with BITO short (3M), 2024-09-19 → 2024-12-18: book +250,019, hedged -93,606
- BTC hedged with IBIT short (3M), 2024-01-19 → 2024-04-19: book +238,489, hedged -101,181
- BTC hedged with IBIT short (3M), 2024-08-20 → 2024-11-18: book +232,639, hedged -98,621
- BTC hedged with BITO short (3M), 2023-12-18 → 2024-03-20: book +255,734, hedged -73,214
- USO hedged with USO 5% OTM put (3M) (3M), 2020-05-18 → 2020-08-17: book +125,298, hedged -194,003
- NVDA hedged with SPY ATM put (3M) (3M), 2020-03-18 → 2020-06-17: book +324,906, hedged +49,608
- NVDA hedged with SMH short (3M), 2020-03-18 → 2020-06-17: book +324,906, hedged +62,923
- NVDA hedged with SPY 5% OTM put (3M) (3M), 2020-03-18 → 2020-06-17: book +324,906, hedged +68,270

**Hedges that increased variance overall** (realised reduction below zero):

- SPY ← SPY 5% OTM put (3M) (1W): realised -22.6%
- SPY ← SPY 5% OTM put (3M) (1M): realised -138.1%
- SPY ← SPY 5% OTM put (3M) (3M): realised -180.6%
- QQQ ← SPY 5% OTM put (3M) (1W): realised -21.2%
- QQQ ← SPY 5% OTM put (3M) (1M): realised -159.8%
- QQQ ← SPY 5% OTM put (3M) (3M): realised -212.8%
- IWM ← SPY 5% OTM put (3M) (1W): realised -28.4%
- IWM ← SPY 5% OTM put (3M) (1M): realised -129.4%
- IWM ← SPY 5% OTM put (3M) (3M): realised -158.9%
- NVDA ← SPY 5% OTM put (3M) (1W): realised -4.1%
- NVDA ← SPY 5% OTM put (3M) (1M): realised -132.5%
- NVDA ← SPY 5% OTM put (3M) (3M): realised -178.9%
- AAPL ← SPY 5% OTM put (3M) (1W): realised -16.1%
- AAPL ← SPY 5% OTM put (3M) (1M): realised -100.4%
- AAPL ← SPY 5% OTM put (3M) (3M): realised -135.8%
- MSFT ← SPY 5% OTM put (3M) (1W): realised -14.3%
- MSFT ← SPY 5% OTM put (3M) (1M): realised -97.9%
- MSFT ← SPY 5% OTM put (3M) (3M): realised -133.7%
- JPM ← SPY 5% OTM put (3M) (1W): realised -7.8%
- JPM ← SPY 5% OTM put (3M) (1M): realised -57.1%
- JPM ← SPY 5% OTM put (3M) (3M): realised -63.8%
- XOM ← SPY 5% OTM put (3M) (1M): realised -35.7%
- XOM ← SPY 5% OTM put (3M) (3M): realised -51.1%
- XLK ← SPY 5% OTM put (3M) (1W): realised -15.1%
- XLK ← SPY 5% OTM put (3M) (1M): realised -164.8%
- XLK ← SPY 5% OTM put (3M) (3M): realised -216.1%
- EFA ← SPY 5% OTM put (3M) (1W): realised -26.4%
- EFA ← SPY 5% OTM put (3M) (1M): realised -73.7%
- EFA ← SPY 5% OTM put (3M) (3M): realised -91.1%
- EEM ← SPY 5% OTM put (3M) (1W): realised -26.2%
- EEM ← SPY 5% OTM put (3M) (1M): realised -80.2%
- EEM ← SPY 5% OTM put (3M) (3M): realised -96.2%
- AMZN ← SPY 5% OTM put (3M) (1W): realised -3.8%
- AMZN ← SPY 5% OTM put (3M) (1M): realised -70.1%
- AMZN ← SPY 5% OTM put (3M) (3M): realised -111.4%
- QQQ ← QQQ 5% OTM put (3M) (1M): realised -7.5%
- QQQ ← QQQ 5% OTM put (3M) (3M): realised -46.5%
- NVDA ← QQQ 5% OTM put (3M) (1M): realised -16.5%
- NVDA ← QQQ 5% OTM put (3M) (3M): realised -47.7%
- AAPL ← QQQ 5% OTM put (3M) (1M): realised -11.3%
- AAPL ← QQQ 5% OTM put (3M) (3M): realised -31.9%
- MSFT ← QQQ 5% OTM put (3M) (1M): realised -7.1%
- MSFT ← QQQ 5% OTM put (3M) (3M): realised -29.7%
- XLK ← QQQ 5% OTM put (3M) (1M): realised -12.1%
- XLK ← QQQ 5% OTM put (3M) (3M): realised -49.3%
- AMZN ← QQQ 5% OTM put (3M) (3M): realised -20.7%
- HYG ← BKLN short (1W): realised -16.7%
- EWJ ← JPY forward (rolled) (1W): realised -32.0%
- EWJ ← JPY forward (rolled) (1M): realised -26.5%
- EWJ ← JPY forward (rolled) (3M): realised -26.9%
- EWG ← EUR forward (rolled) (1W): realised -17.3%
- EWG ← EUR forward (rolled) (1M): realised -12.9%
- EWG ← EUR forward (rolled) (3M): realised -13.3%
- EWU ← GBP forward (rolled) (1W): realised -25.9%
- EWU ← GBP forward (rolled) (1M): realised -19.7%
- EWU ← GBP forward (rolled) (3M): realised -20.0%
- GLD ← GLD 5% OTM put (3M) (1M): realised -24.5%
- GLD ← GLD 5% OTM put (3M) (3M): realised -62.1%
- USO ← USO 5% OTM put (3M) (1W): realised -184.4%
- USO ← USO 5% OTM put (3M) (1M): realised -43.8%
- USO ← USO 5% OTM put (3M) (3M): realised -30.2%

## 18–19. The ML adjustment

FinalHedge = RawHedge × (1 + 0.5 × MLAdjustment), |MLAdjustment| ≤ 0.3 → at most ±15% of the raw hedge. A ridge model per (risk, hedge kind, horizon) learns log(ex-post minimum-variance ratio ÷ raw ratio) from the VIX, the trailing 1-year and 3-month correlation, the change in beta, the 3-month market return and the bill rate, trained only on windows that ended before each prediction. It is used only when it beats the static rule AND the minimum-variance ratio out of sample (paired t ≥ 2 on n_eff ≥ 30) and has not decayed in the latest third.

| Group | OOS windows | n_eff | t vs static rule | t vs min-variance | Verified | Reasons |
|---|---|---|---|---|---|---|
| credit:spot:21 | 478 | 120 | 1.9 | -1.6 | no | does not beat the static rule (t 1.9); does not beat the minimum-variance ratio (t -1.6) |
| credit:spot:5 | 480 | 120 | 1.2 | -1.1 | no | does not beat the static rule (t 1.2); does not beat the minimum-variance ratio (t -1.1) |
| credit:spot:63 | 472 | 39 | 1.5 | -1.3 | no | does not beat the static rule (t 1.5); does not beat the minimum-variance ratio (t -1.3) |
| crypto:spot:21 | 59 | 30 | 10.1 | -7.3 | no | does not beat the minimum-variance ratio (t -7.3) |
| crypto:spot:5 | 60 | 30 | 5.6 | -4.1 | no | does not beat the minimum-variance ratio (t -4.1) |
| crypto:spot:63 | 56 | 9 | 8.5 | -6.6 | no | n_eff 9 < 30; does not beat the minimum-variance ratio (t -6.6) |
| equity:equity_future:21 | 2148 | 120 | 0.0 | 2.1 | no | does not beat the static rule (t 0.0) |
| equity:equity_future:5 | 2158 | 120 | -1.9 | -0.0 | no | does not beat the static rule (t -1.9); does not beat the minimum-variance ratio (t -0.0); decayed: worse than the static rule in the most recent third |
| equity:equity_future:63 | 2124 | 39 | -0.8 | 2.0 | no | does not beat the static rule (t -0.8); does not beat the minimum-variance ratio (t 2.0); decayed: worse than the static rule in the most recent third |
| equity:spot:21 | 3461 | 120 | 0.8 | 2.5 | no | does not beat the static rule (t 0.8) |
| equity:spot:5 | 3478 | 120 | -1.1 | 2.4 | no | does not beat the static rule (t -1.1); decayed: worse than the static rule in the most recent third |
| equity:spot:63 | 3422 | 39 | -1.0 | 1.3 | no | does not beat the static rule (t -1.0); does not beat the minimum-variance ratio (t 1.3); decayed: worse than the static rule in the most recent third |
| fx:forward:21 | 358 | 120 | 13.2 | -9.1 | no | does not beat the minimum-variance ratio (t -9.1) |
| fx:forward:5 | 358 | 120 | 8.5 | -6.0 | no | does not beat the minimum-variance ratio (t -6.0) |
| fx:forward:63 | 354 | 39 | 10.5 | -8.3 | no | does not beat the minimum-variance ratio (t -8.3) |
| name:spot:21 | 358 | 120 | 0.9 | 2.2 | no | does not beat the static rule (t 0.9) |
| name:spot:5 | 360 | 120 | -1.2 | -0.8 | no | does not beat the static rule (t -1.2); does not beat the minimum-variance ratio (t -0.8) |
| name:spot:63 | 354 | 39 | 1.5 | 2.3 | no | does not beat the static rule (t 1.5) |
| rates:spot:21 | 478 | 120 | 3.9 | -3.1 | no | does not beat the minimum-variance ratio (t -3.1) |
| rates:spot:5 | 480 | 120 | 0.6 | -1.7 | no | does not beat the static rule (t 0.6); does not beat the minimum-variance ratio (t -1.7) |
| rates:spot:63 | 472 | 39 | 3.0 | -2.0 | no | does not beat the minimum-variance ratio (t -2.0) |

## 18b. Objective-specific hedge ML (richer features)

The variance-only ML layer (history.ml_layer) learns one multiple of the raw hedge and judges it on variance. A tail hedge, a drawdown hedge or a DV01 hedge is not trying to minimise variance, so here each objective gets its own target, its own model and its own out-of-sample test:

Verified = beats the static rule AND the minimum-variance multiple out of sample (t ≥ 2, or bootstrap p ≤ 0.025 for the pooled VaR/ES), n_eff ≥ 30, not worse in the latest third. Gain = 1 − adjusted ÷ static on the same windows.

| Group | Metric | OOS windows | n_eff (dates) | Gain vs static | Gain vs constant | t / p vs static | t / p vs constant | t / p vs min-var | Mean adj. (constant) | Verified | Reasons |
|---|---|---|---|---|---|---|---|---|---|---|---|
| commodity:option:21 | variance | 239 | 120 | +29.1% | +4.4% | 2.5 | 1.6 | — | -0.22 (-0.23) | no | does not beat a constant resizing (t 1.6) |
| commodity:option:21 | exposure | 239 | 120 | +14.5% | +1.4% | 6.3 | 1.4 | — | -0.09 (-0.10) | no | does not beat a constant resizing (t 1.4) |
| commodity:option:21 | downside | 239 | 120 | +29.9% | +1.8% | 3.6 | 2.1 | — | -0.27 (-0.27) | yes | — |
| commodity:option:21 | drawdown | 239 | 120 | +15.3% | +0.5% | 10.0 | 2.2 | — | -0.25 (-0.25) | yes | — |
| commodity:option:21 | var95 | 239 | 120 | +11.9% | +0.7% | p 0.00 | p 0.00 | p — | -0.11 (-0.11) | yes | — |
| commodity:option:21 | es95 | 239 | 120 | +12.5% | +5.4% | p 0.00 | p 0.00 | p — | -0.11 (-0.11) | yes | — |
| commodity:option:21 | variance basic | 239 | 120 | +26.0% | +0.2% | 2.7 | 0.7 | — | -0.22 (-0.23) | no | does not beat a constant resizing (t 0.7) |
| commodity:option:5 | variance | 240 | 120 | +29.7% | +5.8% | 1.4 | 1.3 | — | -0.23 (-0.24) | no | does not beat the static rule (t 1.4); does not beat a constant resizing (t 1.3) |
| commodity:option:5 | exposure | 240 | 120 | +15.9% | +2.5% | 4.2 | 1.6 | — | -0.11 (-0.11) | no | does not beat a constant resizing (t 1.6) |
| commodity:option:5 | downside | 240 | 120 | +25.5% | +0.7% | 2.3 | 1.8 | — | -0.27 (-0.27) | no | does not beat a constant resizing (t 1.8) |
| commodity:option:5 | drawdown | 240 | 120 | +16.2% | -0.1% | 8.9 | -0.9 | — | -0.26 (-0.26) | no | does not beat a constant resizing (t -0.9) |
| commodity:option:5 | var95 | 240 | 120 | +7.9% | -0.0% | p 0.00 | p 0.46 | p — | -0.12 (-0.12) | no | does not beat a constant resizing (bootstrap p 0.46) |
| commodity:option:5 | es95 | 240 | 120 | +8.3% | -1.0% | p 0.00 | p 0.88 | p — | -0.12 (-0.12) | no | does not beat a constant resizing (bootstrap p 0.88) |
| commodity:option:5 | variance basic | 240 | 120 | +26.1% | +1.1% | 1.4 | 1.2 | — | -0.23 (-0.24) | no | does not beat the static rule (t 1.4); does not beat a constant resizing (t 1.2) |
| commodity:option:63 | variance | 236 | 39 | +24.1% | +4.2% | 2.2 | 1.0 | — | -0.19 (-0.19) | no | does not beat a constant resizing (t 1.0) |
| commodity:option:63 | exposure | 236 | 39 | +0.5% | -1.9% | 0.2 | -0.6 | — | -0.01 (-0.02) | no | does not beat the static rule (t 0.2); does not beat a constant resizing (t -0.6) |
| commodity:option:63 | downside | 236 | 39 | +25.5% | +2.0% | 3.2 | 1.4 | — | -0.24 (-0.24) | no | does not beat a constant resizing (t 1.4) |
| commodity:option:63 | drawdown | 236 | 39 | +13.9% | +0.3% | 6.6 | 1.2 | — | -0.27 (-0.27) | no | does not beat a constant resizing (t 1.2) |
| commodity:option:63 | var95 | 236 | 39 | +12.9% | -0.2% | p 0.00 | p 0.26 | p — | -0.16 (-0.16) | no | does not beat a constant resizing (bootstrap p 0.26) |
| commodity:option:63 | es95 | 236 | 39 | +10.1% | +1.9% | p 0.00 | p 0.00 | p — | -0.16 (-0.16) | yes | — |
| commodity:option:63 | variance basic | 236 | 39 | +21.3% | +0.6% | 2.4 | 0.4 | — | -0.18 (-0.19) | no | does not beat a constant resizing (t 0.4) |
| credit:spot:21 | variance | 478 | 120 | +6.7% | -1.7% | 2.3 | -0.9 | -1.5 | -0.11 (-0.12) | no | does not beat a constant resizing (t -0.9); does not beat the minimum-variance ratio (t -1.5) |
| credit:spot:21 | exposure | 478 | 120 | +2.1% | -0.5% | 4.3 | -1.0 | -0.3 | -0.04 (-0.05) | no | does not beat a constant resizing (t -1.0); does not beat the minimum-variance ratio (t -0.3) |
| credit:spot:21 | downside | 478 | 120 | +8.1% | -1.2% | 1.4 | -1.0 | -1.1 | -0.13 (-0.13) | no | does not beat the static rule (t 1.4); does not beat a constant resizing (t -1.0); does not beat the minimum-variance ratio (t -1.1) |
| credit:spot:21 | drawdown | 478 | 120 | +2.0% | -0.3% | 2.0 | -0.8 | -2.7 | -0.10 (-0.10) | no | does not beat a constant resizing (t -0.8); does not beat the minimum-variance ratio (t -2.7) |
| credit:spot:21 | var95 | 478 | 120 | +2.6% | -0.4% | p 0.26 | p 0.53 | p 0.92 | -0.06 (-0.06) | no | does not beat the static rule (bootstrap p 0.26); does not beat a constant resizing (bootstrap p 0.53); does not beat the minimum-variance ratio (bootstrap p 0.92) |
| credit:spot:21 | es95 | 478 | 120 | +2.6% | -0.4% | p 0.00 | p 0.84 | p 0.91 | -0.06 (-0.06) | no | does not beat a constant resizing (bootstrap p 0.84); does not beat the minimum-variance ratio (bootstrap p 0.91) |
| credit:spot:21 | variance basic | 478 | 120 | +6.8% | -1.6% | 2.1 | -1.1 | -1.5 | -0.12 (-0.12) | no | does not beat a constant resizing (t -1.1); does not beat the minimum-variance ratio (t -1.5) |
| credit:spot:5 | variance | 480 | 120 | +8.0% | -0.3% | 1.2 | -0.6 | -1.1 | -0.08 (-0.09) | no | does not beat the static rule (t 1.2); does not beat a constant resizing (t -0.6); does not beat the minimum-variance ratio (t -1.1) |
| credit:spot:5 | exposure | 480 | 120 | +2.9% | +0.2% | 2.7 | 1.1 | -2.2 | -0.04 (-0.04) | no | does not beat a constant resizing (t 1.1); does not beat the minimum-variance ratio (t -2.2) |
| credit:spot:5 | downside | 480 | 120 | +5.9% | -4.8% | 1.2 | -1.0 | -1.0 | -0.10 (-0.11) | no | does not beat the static rule (t 1.2); does not beat a constant resizing (t -1.0); does not beat the minimum-variance ratio (t -1.0) |
| credit:spot:5 | drawdown | 480 | 120 | +1.3% | -0.0% | 1.8 | -0.1 | -1.4 | -0.09 (-0.11) | no | does not beat the static rule (t 1.8); does not beat a constant resizing (t -0.1); does not beat the minimum-variance ratio (t -1.4) |
| credit:spot:5 | var95 | 480 | 120 | +2.0% | -0.7% | p 0.18 | p 0.57 | p 0.36 | -0.05 (-0.05) | no | does not beat the static rule (bootstrap p 0.18); does not beat a constant resizing (bootstrap p 0.57); does not beat the minimum-variance ratio (bootstrap p 0.36) |
| credit:spot:5 | es95 | 480 | 120 | +1.3% | -0.7% | p 0.00 | p 0.98 | p 0.49 | -0.05 (-0.05) | no | does not beat a constant resizing (bootstrap p 0.98); does not beat the minimum-variance ratio (bootstrap p 0.49) |
| credit:spot:5 | variance basic | 480 | 120 | +7.6% | -0.8% | 1.2 | -1.1 | -1.1 | -0.09 (-0.09) | no | does not beat the static rule (t 1.2); does not beat a constant resizing (t -1.1); does not beat the minimum-variance ratio (t -1.1) |
| credit:spot:63 | variance | 472 | 39 | +9.2% | +0.1% | 1.5 | 0.2 | -1.3 | -0.12 (-0.14) | no | does not beat the static rule (t 1.5); does not beat a constant resizing (t 0.2); does not beat the minimum-variance ratio (t -1.3) |
| credit:spot:63 | exposure | 472 | 39 | +5.7% | -0.4% | 4.1 | -0.5 | -0.3 | -0.05 (-0.06) | no | does not beat a constant resizing (t -0.5); does not beat the minimum-variance ratio (t -0.3) |
| credit:spot:63 | downside | 472 | 39 | +9.9% | +0.2% | 1.4 | 1.2 | -1.2 | -0.14 (-0.15) | no | does not beat the static rule (t 1.4); does not beat a constant resizing (t 1.2); does not beat the minimum-variance ratio (t -1.2) |
| credit:spot:63 | drawdown | 472 | 39 | +4.4% | +0.3% | 2.4 | 1.2 | -2.3 | -0.12 (-0.13) | no | does not beat a constant resizing (t 1.2); does not beat the minimum-variance ratio (t -2.3) |
| credit:spot:63 | var95 | 472 | 39 | +8.5% | +0.5% | p 0.11 | p 0.44 | p 0.84 | -0.09 (-0.09) | no | does not beat the static rule (bootstrap p 0.11); does not beat a constant resizing (bootstrap p 0.44); does not beat the minimum-variance ratio (bootstrap p 0.84) |
| credit:spot:63 | es95 | 472 | 39 | +4.9% | +0.8% | p 0.01 | p 0.06 | p 0.98 | -0.09 (-0.09) | no | does not beat a constant resizing (bootstrap p 0.06); does not beat the minimum-variance ratio (bootstrap p 0.98) |
| credit:spot:63 | variance basic | 472 | 39 | +8.5% | -0.7% | 1.5 | -1.4 | -1.3 | -0.13 (-0.14) | no | does not beat the static rule (t 1.5); does not beat a constant resizing (t -1.4); does not beat the minimum-variance ratio (t -1.3) |
| crypto:spot:21 | variance | 59 | 30 | +40.9% | -0.0% | 10.1 | -2.2 | — | -0.30 (-0.30) | no | does not beat a constant resizing (t -2.2) |
| crypto:spot:21 | exposure | 59 | 30 | +68.1% | -0.9% | 8.0 | -1.8 | — | -0.27 (-0.28) | no | does not beat a constant resizing (t -1.8) |
| crypto:spot:21 | downside | 59 | 30 | +40.6% | +0.0% | 9.3 | 0.1 | — | -0.30 (-0.30) | no | does not beat a constant resizing (t 0.1) |
| crypto:spot:21 | drawdown | 59 | 30 | +34.2% | +0.5% | 9.7 | 2.9 | — | -0.28 (-0.28) | yes | — |
| crypto:spot:21 | var95 | 59 | 30 | +23.5% | -0.9% | p 0.00 | p 0.45 | p — | -0.17 (-0.16) | no | does not beat a constant resizing (bootstrap p 0.45) |
| crypto:spot:21 | es95 | 59 | 30 | +26.5% | +2.6% | p 0.00 | p 0.10 | p — | -0.17 (-0.16) | no | does not beat a constant resizing (bootstrap p 0.10) |
| crypto:spot:21 | variance basic | 59 | 30 | +40.9% | -0.0% | 10.1 | -1.0 | — | -0.30 (-0.30) | no | does not beat a constant resizing (t -1.0) |
| crypto:spot:5 | variance | 60 | 30 | +41.6% | +0.8% | 5.7 | 2.0 | — | -0.29 (-0.29) | yes | — |
| crypto:spot:5 | exposure | 60 | 30 | +33.3% | +2.2% | 4.9 | 2.0 | — | -0.17 (-0.17) | no | does not beat a constant resizing (t 2.0) |
| crypto:spot:5 | downside | 60 | 30 | +35.0% | +2.4% | 3.7 | 2.4 | — | -0.24 (-0.23) | yes | — |
| crypto:spot:5 | drawdown | 60 | 30 | +18.2% | +0.9% | 6.4 | 2.3 | — | -0.23 (-0.23) | yes | — |
| crypto:spot:5 | var95 | 60 | 30 | +11.8% | +3.7% | p 0.00 | p 0.34 | p — | -0.09 (-0.09) | no | does not beat a constant resizing (bootstrap p 0.34) |
| crypto:spot:5 | es95 | 60 | 30 | +7.5% | +1.8% | p 0.00 | p 0.11 | p — | -0.09 (-0.09) | no | does not beat a constant resizing (bootstrap p 0.11) |
| crypto:spot:5 | variance basic | 60 | 30 | +41.4% | +0.4% | 5.7 | 1.3 | — | -0.29 (-0.29) | no | does not beat a constant resizing (t 1.3) |
| crypto:spot:63 | variance | 56 | 9 | +41.0% | +0.0% | 8.5 | -1.3 | — | -0.30 (-0.30) | no | n_eff 9 < 30; does not beat a constant resizing (t -1.3) |
| crypto:spot:63 | exposure | 56 | 9 | +80.3% | -0.3% | 8.9 | -1.7 | — | -0.30 (-0.30) | no | n_eff 9 < 30; does not beat a constant resizing (t -1.7) |
| crypto:spot:63 | downside | 56 | 9 | +40.9% | -0.0% | 7.7 | -1.3 | — | -0.30 (-0.30) | no | n_eff 9 < 30; does not beat a constant resizing (t -1.3) |
| crypto:spot:63 | drawdown | 56 | 9 | +38.4% | -0.0% | 4.9 | -1.3 | — | -0.30 (-0.30) | no | n_eff 9 < 30; does not beat a constant resizing (t -1.3) |
| crypto:spot:63 | var95 | 56 | 9 | +25.5% | +5.7% | p 0.00 | p 0.12 | p — | -0.14 (-0.13) | no | n_eff 9 < 30; does not beat a constant resizing (bootstrap p 0.12) |
| crypto:spot:63 | es95 | 56 | 9 | +25.0% | +5.9% | p 0.00 | p 0.08 | p — | -0.14 (-0.13) | no | n_eff 9 < 30; does not beat a constant resizing (bootstrap p 0.08) |
| crypto:spot:63 | variance basic | 56 | 9 | +41.0% | +0.0% | 8.5 | -1.0 | — | -0.30 (-0.30) | no | n_eff 9 < 30; does not beat a constant resizing (t -1.0) |
| equity:equity_future:21 | variance | 2148 | 120 | +0.0% | +0.1% | 0.1 | 1.6 | 2.2 | 0.01 (0.01) | no | does not beat the static rule (t 0.1); does not beat a constant resizing (t 1.6) |
| equity:equity_future:21 | exposure | 2148 | 120 | +0.1% | +0.1% | 0.6 | 0.5 | 1.3 | 0.00 (0.00) | no | does not beat the static rule (t 0.6); does not beat a constant resizing (t 0.5); does not beat the minimum-variance ratio (t 1.3) |
| equity:equity_future:21 | downside | 2148 | 120 | +0.4% | +0.1% | 1.8 | 1.1 | 3.0 | -0.05 (-0.05) | no | does not beat the static rule (t 1.8); does not beat a constant resizing (t 1.1) |
| equity:equity_future:21 | drawdown | 2148 | 120 | +0.4% | +0.0% | 2.1 | 1.0 | 4.9 | -0.07 (-0.06) | no | does not beat a constant resizing (t 1.0) |
| equity:equity_future:21 | var95 | 2148 | 120 | +1.3% | -0.7% | p 0.18 | p 0.47 | p 0.02 | -0.04 (-0.05) | no | does not beat the static rule (bootstrap p 0.18); does not beat a constant resizing (bootstrap p 0.47) |
| equity:equity_future:21 | es95 | 2148 | 120 | +0.4% | +0.1% | p 0.16 | p 0.23 | p 0.03 | -0.04 (-0.05) | no | does not beat the static rule (bootstrap p 0.16); does not beat a constant resizing (bootstrap p 0.23); does not beat the minimum-variance ratio (bootstrap p 0.03) |
| equity:equity_future:21 | variance basic | 2148 | 120 | -0.1% | +0.0% | -1.1 | 0.6 | 2.2 | 0.01 (0.01) | no | does not beat the static rule (t -1.1); does not beat a constant resizing (t 0.6); decayed: worse than the static rule in the most recent third |
| equity:equity_future:5 | variance | 2160 | 120 | -0.0% | -0.0% | -0.9 | -0.8 | 0.4 | -0.00 (-0.00) | no | does not beat the static rule (t -0.9); does not beat a constant resizing (t -0.8); does not beat the minimum-variance ratio (t 0.4); decayed: worse than the static rule in the most recent third |
| equity:equity_future:5 | exposure | 2160 | 120 | -0.0% | -0.0% | -0.6 | -0.5 | -0.4 | -0.00 (-0.00) | no | does not beat the static rule (t -0.6); does not beat a constant resizing (t -0.5); does not beat the minimum-variance ratio (t -0.4); decayed: worse than the static rule in the most recent third |
| equity:equity_future:5 | downside | 2160 | 120 | -0.1% | +0.0% | -0.8 | 0.2 | 1.4 | -0.04 (-0.04) | no | does not beat the static rule (t -0.8); does not beat a constant resizing (t 0.2); does not beat the minimum-variance ratio (t 1.4) |
| equity:equity_future:5 | drawdown | 2160 | 120 | -0.2% | +0.1% | -0.8 | 1.5 | 2.8 | -0.05 (-0.06) | no | does not beat the static rule (t -0.8); does not beat a constant resizing (t 1.5) |
| equity:equity_future:5 | var95 | 2160 | 120 | +0.9% | -0.2% | p 0.50 | p 0.57 | p 0.73 | -0.03 (-0.04) | no | does not beat the static rule (bootstrap p 0.50); does not beat a constant resizing (bootstrap p 0.57); does not beat the minimum-variance ratio (bootstrap p 0.73) |
| equity:equity_future:5 | es95 | 2160 | 120 | -0.2% | -0.0% | p 0.84 | p 0.67 | p 0.16 | -0.03 (-0.04) | no | does not beat the static rule (bootstrap p 0.84); does not beat a constant resizing (bootstrap p 0.67); does not beat the minimum-variance ratio (bootstrap p 0.16) |
| equity:equity_future:5 | variance basic | 2160 | 120 | -0.0% | -0.0% | -0.9 | -0.7 | 0.4 | -0.00 (-0.00) | no | does not beat the static rule (t -0.9); does not beat a constant resizing (t -0.7); does not beat the minimum-variance ratio (t 0.4); decayed: worse than the static rule in the most recent third |
| equity:equity_future:63 | variance | 2124 | 39 | +0.1% | +0.2% | 1.4 | 1.2 | 1.9 | 0.01 (0.02) | no | does not beat the static rule (t 1.4); does not beat a constant resizing (t 1.2); does not beat the minimum-variance ratio (t 1.9) |
| equity:equity_future:63 | exposure | 2124 | 39 | +0.3% | +0.4% | 0.7 | 0.8 | 1.5 | 0.00 (0.01) | no | does not beat the static rule (t 0.7); does not beat a constant resizing (t 0.8); does not beat the minimum-variance ratio (t 1.5) |
| equity:equity_future:63 | downside | 2124 | 39 | +0.5% | +0.2% | 1.3 | 1.2 | 2.3 | -0.04 (-0.04) | no | does not beat the static rule (t 1.3); does not beat a constant resizing (t 1.2) |
| equity:equity_future:63 | drawdown | 2124 | 39 | +0.7% | +0.0% | 2.1 | 1.0 | 3.3 | -0.09 (-0.09) | no | does not beat a constant resizing (t 1.0) |
| equity:equity_future:63 | var95 | 2124 | 39 | +1.5% | -0.1% | p 0.09 | p 0.58 | p 0.01 | -0.06 (-0.07) | no | does not beat the static rule (bootstrap p 0.09); does not beat a constant resizing (bootstrap p 0.58) |
| equity:equity_future:63 | es95 | 2124 | 39 | +0.4% | +0.1% | p 0.24 | p 0.17 | p 0.02 | -0.06 (-0.07) | no | does not beat the static rule (bootstrap p 0.24); does not beat a constant resizing (bootstrap p 0.17) |
| equity:equity_future:63 | variance basic | 2124 | 39 | -0.1% | +0.0% | -1.0 | 0.7 | 2.0 | 0.02 (0.02) | no | does not beat the static rule (t -1.0); does not beat a constant resizing (t 0.7); does not beat the minimum-variance ratio (t 2.0); decayed: worse than the static rule in the most recent third |
| equity:option:21 | variance | 3575 | 120 | +22.9% | +0.1% | 2.6 | 0.8 | — | -0.24 (-0.24) | no | does not beat a constant resizing (t 0.8) |
| equity:option:21 | exposure | 3575 | 120 | +26.5% | -0.3% | 8.9 | -1.2 | — | -0.20 (-0.20) | no | does not beat a constant resizing (t -1.2) |
| equity:option:21 | downside | 3575 | 120 | +25.3% | +0.2% | 3.2 | 1.6 | — | -0.27 (-0.27) | no | does not beat a constant resizing (t 1.6) |
| equity:option:21 | drawdown | 3575 | 120 | +11.7% | +0.3% | 11.6 | 3.3 | — | -0.25 (-0.24) | yes | — |
| equity:option:21 | var95 | 3575 | 120 | +7.2% | +1.9% | p 0.00 | p 0.04 | p — | -0.12 (-0.11) | no | does not beat a constant resizing (bootstrap p 0.04) |
| equity:option:21 | es95 | 3575 | 120 | +6.0% | +2.4% | p 0.00 | p 0.00 | p — | -0.12 (-0.11) | yes | — |
| equity:option:21 | variance basic | 3575 | 120 | +22.7% | -0.1% | 2.6 | -0.8 | — | -0.24 (-0.24) | no | does not beat a constant resizing (t -0.8) |
| equity:option:5 | variance | 3594 | 121 | +15.5% | +0.3% | 6.3 | 2.7 | — | -0.22 (-0.22) | yes | — |
| equity:option:5 | exposure | 3594 | 121 | +12.7% | +0.1% | 9.5 | 1.3 | — | -0.18 (-0.18) | no | does not beat a constant resizing (t 1.3) |
| equity:option:5 | downside | 3594 | 121 | +14.2% | +0.3% | 7.5 | 3.0 | — | -0.23 (-0.22) | yes | — |
| equity:option:5 | drawdown | 3594 | 121 | +9.4% | +0.1% | 9.9 | 3.0 | — | -0.22 (-0.22) | yes | — |
| equity:option:5 | var95 | 3594 | 121 | +4.6% | +0.8% | p 0.00 | p 0.27 | p — | -0.12 (-0.12) | no | does not beat a constant resizing (bootstrap p 0.27) |
| equity:option:5 | es95 | 3594 | 121 | +5.1% | +1.1% | p 0.00 | p 0.10 | p — | -0.12 (-0.12) | no | does not beat a constant resizing (bootstrap p 0.10) |
| equity:option:5 | variance basic | 3594 | 121 | +15.3% | +0.1% | 6.2 | 1.3 | — | -0.22 (-0.22) | no | does not beat a constant resizing (t 1.3) |
| equity:option:63 | variance | 3538 | 40 | +20.8% | +0.5% | 1.8 | 1.1 | — | -0.22 (-0.22) | no | does not beat the static rule (t 1.8); does not beat a constant resizing (t 1.1) |
| equity:option:63 | exposure | 3538 | 40 | +7.7% | +1.2% | 3.0 | 1.5 | — | -0.06 (-0.05) | no | does not beat a constant resizing (t 1.5) |
| equity:option:63 | downside | 3538 | 40 | +24.1% | +0.2% | 2.0 | 1.6 | — | -0.26 (-0.26) | no | does not beat the static rule (t 2.0); does not beat a constant resizing (t 1.6) |
| equity:option:63 | drawdown | 3538 | 40 | +9.9% | -0.0% | 5.0 | -0.0 | — | -0.24 (-0.24) | no | does not beat a constant resizing (t -0.0) |
| equity:option:63 | var95 | 3538 | 40 | +3.9% | -0.2% | p 0.00 | p 0.48 | p — | -0.13 (-0.13) | no | does not beat a constant resizing (bootstrap p 0.48) |
| equity:option:63 | es95 | 3538 | 40 | +2.7% | +0.2% | p 0.00 | p 0.14 | p — | -0.13 (-0.13) | no | does not beat a constant resizing (bootstrap p 0.14) |
| equity:option:63 | variance basic | 3538 | 40 | +20.5% | +0.1% | 1.8 | 0.3 | — | -0.22 (-0.22) | no | does not beat the static rule (t 1.8); does not beat a constant resizing (t 0.3) |
| equity:spot:21 | variance | 3461 | 120 | +0.0% | +0.1% | 0.5 | 1.4 | 2.5 | 0.01 (0.01) | no | does not beat the static rule (t 0.5); does not beat a constant resizing (t 1.4); decayed: worse than the static rule in the most recent third |
| equity:spot:21 | exposure | 3461 | 120 | +0.0% | +0.1% | 0.5 | 0.5 | 1.8 | 0.00 (0.00) | no | does not beat the static rule (t 0.5); does not beat a constant resizing (t 0.5); does not beat the minimum-variance ratio (t 1.8) |
| equity:spot:21 | downside | 3461 | 120 | +0.5% | +0.1% | 2.5 | 1.3 | 2.2 | -0.05 (-0.05) | no | does not beat a constant resizing (t 1.3) |
| equity:spot:21 | drawdown | 3461 | 120 | +0.6% | +0.1% | 2.8 | 1.9 | 5.3 | -0.07 (-0.07) | no | does not beat a constant resizing (t 1.9) |
| equity:spot:21 | var95 | 3461 | 120 | +1.6% | +0.6% | p 0.14 | p 0.34 | p 0.02 | -0.05 (-0.05) | no | does not beat the static rule (bootstrap p 0.14); does not beat a constant resizing (bootstrap p 0.34) |
| equity:spot:21 | es95 | 3461 | 120 | +0.4% | +0.1% | p 0.11 | p 0.10 | p 0.00 | -0.05 (-0.05) | no | does not beat the static rule (bootstrap p 0.11); does not beat a constant resizing (bootstrap p 0.10) |
| equity:spot:21 | variance basic | 3461 | 120 | -0.1% | -0.0% | -1.8 | -0.3 | 2.5 | 0.01 (0.01) | no | does not beat the static rule (t -1.8); does not beat a constant resizing (t -0.3); decayed: worse than the static rule in the most recent third |
| equity:spot:5 | variance | 3480 | 120 | -0.0% | -0.0% | -0.5 | -0.5 | 2.5 | -0.00 (-0.00) | no | does not beat the static rule (t -0.5); does not beat a constant resizing (t -0.5); decayed: worse than the static rule in the most recent third |
| equity:spot:5 | exposure | 3480 | 120 | -0.1% | -0.0% | -0.9 | -0.9 | 1.6 | -0.01 (-0.00) | no | does not beat the static rule (t -0.9); does not beat a constant resizing (t -0.9); does not beat the minimum-variance ratio (t 1.6); decayed: worse than the static rule in the most recent third |
| equity:spot:5 | downside | 3480 | 120 | -0.1% | -0.0% | -0.3 | -0.2 | 2.0 | -0.04 (-0.05) | no | does not beat the static rule (t -0.3); does not beat a constant resizing (t -0.2) |
| equity:spot:5 | drawdown | 3480 | 120 | -0.1% | +0.0% | -0.6 | 0.6 | 3.3 | -0.05 (-0.06) | no | does not beat the static rule (t -0.6); does not beat a constant resizing (t 0.6) |
| equity:spot:5 | var95 | 3480 | 120 | +0.4% | -0.1% | p 0.42 | p 0.60 | p 0.07 | -0.04 (-0.04) | no | does not beat the static rule (bootstrap p 0.42); does not beat a constant resizing (bootstrap p 0.60); does not beat the minimum-variance ratio (bootstrap p 0.07) |
| equity:spot:5 | es95 | 3480 | 120 | -0.1% | +0.0% | p 0.67 | p 0.56 | p 0.00 | -0.04 (-0.04) | no | does not beat the static rule (bootstrap p 0.67); does not beat a constant resizing (bootstrap p 0.56) |
| equity:spot:5 | variance basic | 3480 | 120 | -0.0% | -0.0% | -0.9 | -0.9 | 2.5 | -0.00 (-0.00) | no | does not beat the static rule (t -0.9); does not beat a constant resizing (t -0.9); decayed: worse than the static rule in the most recent third |
| equity:spot:63 | variance | 3422 | 39 | +0.1% | +0.2% | 1.2 | 1.3 | 1.3 | 0.01 (0.01) | no | does not beat the static rule (t 1.2); does not beat a constant resizing (t 1.3); does not beat the minimum-variance ratio (t 1.3) |
| equity:spot:63 | exposure | 3422 | 39 | +0.4% | +0.5% | 0.9 | 1.0 | 1.8 | 0.00 (0.01) | no | does not beat the static rule (t 0.9); does not beat a constant resizing (t 1.0); does not beat the minimum-variance ratio (t 1.8) |
| equity:spot:63 | downside | 3422 | 39 | +0.7% | +0.2% | 1.6 | 1.4 | 1.5 | -0.05 (-0.05) | no | does not beat the static rule (t 1.6); does not beat a constant resizing (t 1.4); does not beat the minimum-variance ratio (t 1.5) |
| equity:spot:63 | drawdown | 3422 | 39 | +1.1% | +0.1% | 2.9 | 1.4 | 5.6 | -0.10 (-0.10) | no | does not beat a constant resizing (t 1.4) |
| equity:spot:63 | var95 | 3422 | 39 | +0.5% | -0.0% | p 0.07 | p 0.41 | p 0.00 | -0.07 (-0.07) | no | does not beat the static rule (bootstrap p 0.07); does not beat a constant resizing (bootstrap p 0.41) |
| equity:spot:63 | es95 | 3422 | 39 | +0.7% | +0.1% | p 0.11 | p 0.15 | p 0.00 | -0.07 (-0.07) | no | does not beat the static rule (bootstrap p 0.11); does not beat a constant resizing (bootstrap p 0.15) |
| equity:spot:63 | variance basic | 3422 | 39 | -0.1% | -0.0% | -1.4 | -0.0 | 1.3 | 0.01 (0.01) | no | does not beat the static rule (t -1.4); does not beat a constant resizing (t -0.0); does not beat the minimum-variance ratio (t 1.3); decayed: worse than the static rule in the most recent third |
| fx:forward:21 | variance | 358 | 120 | +4.2% | -0.0% | 13.7 | -0.4 | -9.2 | -0.28 (-0.28) | no | does not beat a constant resizing (t -0.4); does not beat the minimum-variance ratio (t -9.2) |
| fx:forward:21 | exposure | 358 | 120 | +19.3% | +0.0% | 24.8 | 0.7 | -11.8 | -0.28 (-0.27) | no | does not beat a constant resizing (t 0.7); does not beat the minimum-variance ratio (t -11.8) |
| fx:forward:21 | downside | 358 | 120 | +3.4% | -0.0% | 10.7 | -0.7 | -6.1 | -0.24 (-0.24) | no | does not beat a constant resizing (t -0.7); does not beat the minimum-variance ratio (t -6.1) |
| fx:forward:21 | drawdown | 358 | 120 | +0.7% | +0.0% | 5.3 | 0.6 | 0.9 | -0.11 (-0.12) | no | does not beat a constant resizing (t 0.6); does not beat the minimum-variance ratio (t 0.9) |
| fx:forward:21 | var95 | 358 | 120 | -0.1% | +0.0% | p 0.58 | p 0.42 | p 0.00 | -0.02 (-0.03) | no | does not beat the static rule (bootstrap p 0.58); does not beat a constant resizing (bootstrap p 0.42); decayed: worse than the static rule in the most recent third |
| fx:forward:21 | es95 | 358 | 120 | +0.1% | +0.0% | p 0.23 | p 0.19 | p 0.00 | -0.02 (-0.03) | no | does not beat the static rule (bootstrap p 0.23); does not beat a constant resizing (bootstrap p 0.19) |
| fx:forward:21 | variance basic | 358 | 120 | +4.2% | -0.0% | 13.8 | -1.0 | -9.1 | -0.28 (-0.28) | no | does not beat a constant resizing (t -1.0); does not beat the minimum-variance ratio (t -9.1) |
| fx:forward:5 | variance | 360 | 120 | +3.7% | -0.2% | 10.0 | -1.0 | -6.2 | -0.21 (-0.21) | no | does not beat a constant resizing (t -1.0); does not beat the minimum-variance ratio (t -6.2) |
| fx:forward:5 | exposure | 360 | 120 | +6.1% | +0.0% | 10.7 | 0.5 | -5.5 | -0.21 (-0.21) | no | does not beat a constant resizing (t 0.5); does not beat the minimum-variance ratio (t -5.5) |
| fx:forward:5 | downside | 360 | 120 | +3.2% | -0.1% | 7.0 | -1.5 | -4.4 | -0.15 (-0.16) | no | does not beat a constant resizing (t -1.5); does not beat the minimum-variance ratio (t -4.4) |
| fx:forward:5 | drawdown | 360 | 120 | +1.2% | +0.0% | 7.1 | 0.3 | -3.3 | -0.12 (-0.12) | no | does not beat a constant resizing (t 0.3); does not beat the minimum-variance ratio (t -3.3) |
| fx:forward:5 | var95 | 360 | 120 | +0.8% | +0.3% | p 0.40 | p 0.58 | p 0.21 | -0.03 (-0.03) | no | does not beat the static rule (bootstrap p 0.40); does not beat a constant resizing (bootstrap p 0.58); does not beat the minimum-variance ratio (bootstrap p 0.21); decayed: worse than the static rule in the most recent third |
| fx:forward:5 | es95 | 360 | 120 | -0.0% | -0.0% | p 0.69 | p 0.89 | p 0.06 | -0.03 (-0.03) | no | does not beat the static rule (bootstrap p 0.69); does not beat a constant resizing (bootstrap p 0.89); does not beat the minimum-variance ratio (bootstrap p 0.06); decayed: worse than the static rule in the most recent third |
| fx:forward:5 | variance basic | 360 | 120 | +3.8% | -0.1% | 9.9 | -0.9 | -6.2 | -0.21 (-0.21) | no | does not beat a constant resizing (t -0.9); does not beat the minimum-variance ratio (t -6.2) |
| fx:forward:63 | variance | 354 | 39 | +4.5% | -0.0% | 10.5 | -0.5 | -8.3 | -0.30 (-0.30) | no | does not beat a constant resizing (t -0.5); does not beat the minimum-variance ratio (t -8.3) |
| fx:forward:63 | exposure | 354 | 39 | +25.1% | -0.0% | 25.0 | -2.5 | -11.3 | -0.30 (-0.30) | no | does not beat a constant resizing (t -2.5); does not beat the minimum-variance ratio (t -11.3) |
| fx:forward:63 | downside | 354 | 39 | +4.1% | +0.0% | 8.7 | 0.9 | -6.0 | -0.29 (-0.29) | no | does not beat a constant resizing (t 0.9); does not beat the minimum-variance ratio (t -6.0) |
| fx:forward:63 | drawdown | 354 | 39 | +0.5% | +0.1% | 2.0 | 0.9 | 1.9 | -0.12 (-0.12) | no | does not beat the static rule (t 2.0); does not beat a constant resizing (t 0.9); does not beat the minimum-variance ratio (t 1.9) |
| fx:forward:63 | var95 | 354 | 39 | -0.1% | -0.1% | p 0.47 | p 0.62 | p 0.01 | -0.00 (-0.00) | no | does not beat the static rule (bootstrap p 0.47); does not beat a constant resizing (bootstrap p 0.62); decayed: worse than the static rule in the most recent third |
| fx:forward:63 | es95 | 354 | 39 | -0.1% | -0.0% | p 0.81 | p 0.85 | p 0.00 | -0.00 (-0.00) | no | does not beat the static rule (bootstrap p 0.81); does not beat a constant resizing (bootstrap p 0.85) |
| fx:forward:63 | variance basic | 354 | 39 | +4.5% | -0.0% | 10.5 | -1.8 | -8.3 | -0.30 (-0.30) | no | does not beat a constant resizing (t -1.8); does not beat the minimum-variance ratio (t -8.3) |
| name:option:21 | variance | 358 | 120 | +31.2% | +0.8% | 3.9 | 1.7 | — | -0.24 (-0.23) | no | does not beat a constant resizing (t 1.7) |
| name:option:21 | downside | 358 | 120 | +36.0% | +0.6% | 4.7 | 2.1 | — | -0.27 (-0.27) | yes | — |
| name:option:21 | drawdown | 358 | 120 | +18.3% | +0.8% | 13.3 | 4.1 | — | -0.25 (-0.25) | yes | — |
| name:option:21 | var95 | 358 | 120 | +5.2% | +1.2% | p 0.01 | p 0.20 | p — | -0.10 (-0.09) | no | does not beat a constant resizing (bootstrap p 0.20) |
| name:option:21 | es95 | 358 | 120 | +8.2% | +2.4% | p 0.00 | p 0.00 | p — | -0.10 (-0.09) | yes | — |
| name:option:21 | variance basic | 358 | 120 | +31.2% | +0.7% | 3.8 | 2.8 | — | -0.24 (-0.23) | yes | — |
| name:option:5 | variance | 360 | 120 | +31.5% | +1.5% | 7.5 | 2.1 | — | -0.23 (-0.22) | yes | — |
| name:option:5 | downside | 360 | 120 | +33.8% | +1.4% | 6.0 | 1.5 | — | -0.25 (-0.25) | no | does not beat a constant resizing (t 1.5) |
| name:option:5 | drawdown | 360 | 120 | +18.4% | +0.6% | 10.2 | 1.8 | — | -0.24 (-0.24) | no | does not beat a constant resizing (t 1.8) |
| name:option:5 | var95 | 360 | 120 | +7.4% | +0.3% | p 0.01 | p 0.22 | p — | -0.10 (-0.10) | no | does not beat a constant resizing (bootstrap p 0.22) |
| name:option:5 | es95 | 360 | 120 | +8.4% | +2.1% | p 0.00 | p 0.07 | p — | -0.10 (-0.10) | no | does not beat a constant resizing (bootstrap p 0.07) |
| name:option:5 | variance basic | 360 | 120 | +31.4% | +1.3% | 7.5 | 2.1 | — | -0.22 (-0.22) | yes | — |
| name:option:63 | variance | 354 | 39 | +21.7% | +0.9% | 3.3 | 1.5 | — | -0.19 (-0.18) | no | does not beat a constant resizing (t 1.5) |
| name:option:63 | downside | 354 | 39 | +28.8% | +0.5% | 3.4 | 1.3 | — | -0.25 (-0.25) | no | does not beat a constant resizing (t 1.3) |
| name:option:63 | drawdown | 354 | 39 | +11.6% | +0.0% | 6.8 | 0.1 | — | -0.22 (-0.22) | no | does not beat a constant resizing (t 0.1) |
| name:option:63 | var95 | 354 | 39 | +5.2% | +0.1% | p 0.00 | p 0.40 | p — | -0.12 (-0.12) | no | does not beat a constant resizing (bootstrap p 0.40) |
| name:option:63 | es95 | 354 | 39 | +5.4% | +0.2% | p 0.00 | p 0.00 | p — | -0.12 (-0.12) | yes | — |
| name:option:63 | variance basic | 354 | 39 | +21.7% | +0.8% | 3.2 | 2.8 | — | -0.19 (-0.18) | yes | — |
| name:spot:21 | variance | 358 | 120 | +0.0% | +0.0% | — | — | 0.9 | 0.00 (0.00) | no | does not beat the static rule (t —); does not beat a constant resizing (t —); does not beat the minimum-variance ratio (t 0.9) |
| name:spot:21 | downside | 358 | 120 | +0.0% | +0.0% | — | — | 0.9 | 0.00 (0.00) | no | does not beat the static rule (t —); does not beat a constant resizing (t —); does not beat the minimum-variance ratio (t 0.9) |
| name:spot:21 | drawdown | 358 | 120 | +3.2% | +0.3% | 3.4 | 0.5 | 3.5 | -0.00 (-0.00) | no | does not beat a constant resizing (t 0.5) |
| name:spot:21 | var95 | 358 | 120 | -228.9% | -6.4% | p 1.00 | p 0.99 | p 1.00 | -0.01 (-0.01) | no | does not beat the static rule (bootstrap p 1.00); does not beat a constant resizing (bootstrap p 0.99); does not beat the minimum-variance ratio (bootstrap p 1.00); decayed: worse than the static rule in the most recent third |
| name:spot:21 | es95 | 358 | 120 | -396.0% | -20.5% | p 1.00 | p 1.00 | p 1.00 | -0.01 (-0.01) | no | does not beat the static rule (bootstrap p 1.00); does not beat a constant resizing (bootstrap p 1.00); does not beat the minimum-variance ratio (bootstrap p 1.00); decayed: worse than the static rule in the most recent third |
| name:spot:21 | variance basic | 358 | 120 | +0.0% | +0.0% | — | — | 0.9 | 0.00 (0.00) | no | does not beat the static rule (t —); does not beat a constant resizing (t —); does not beat the minimum-variance ratio (t 0.9) |
| name:spot:5 | variance | 360 | 120 | +0.0% | +0.0% | — | — | 0.7 | 0.00 (0.00) | no | does not beat the static rule (t —); does not beat a constant resizing (t —); does not beat the minimum-variance ratio (t 0.7) |
| name:spot:5 | downside | 360 | 120 | -165.8% | +25.7% | -7.9 | 4.1 | -8.0 | -0.00 (-0.00) | no | does not beat the static rule (t -7.9); does not beat the minimum-variance ratio (t -8.0); decayed: worse than the static rule in the most recent third |
| name:spot:5 | drawdown | 360 | 120 | -33.6% | +12.6% | -5.0 | 4.8 | -5.2 | -0.00 (-0.00) | no | does not beat the static rule (t -5.0); does not beat the minimum-variance ratio (t -5.2); decayed: worse than the static rule in the most recent third |
| name:spot:5 | var95 | 360 | 120 | -414.8% | -19.2% | p 1.00 | p 0.90 | p 1.00 | -0.01 (-0.01) | no | does not beat the static rule (bootstrap p 1.00); does not beat a constant resizing (bootstrap p 0.90); does not beat the minimum-variance ratio (bootstrap p 1.00); decayed: worse than the static rule in the most recent third |
| name:spot:5 | es95 | 360 | 120 | -931.2% | -53.5% | p 1.00 | p 1.00 | p 1.00 | -0.01 (-0.01) | no | does not beat the static rule (bootstrap p 1.00); does not beat a constant resizing (bootstrap p 1.00); does not beat the minimum-variance ratio (bootstrap p 1.00); decayed: worse than the static rule in the most recent third |
| name:spot:5 | variance basic | 360 | 120 | +0.0% | +0.0% | — | — | 0.7 | 0.00 (0.00) | no | does not beat the static rule (t —); does not beat a constant resizing (t —); does not beat the minimum-variance ratio (t 0.7) |
| name:spot:63 | variance | 354 | 39 | +0.0% | +0.0% | — | — | 1.0 | 0.00 (0.00) | no | does not beat the static rule (t —); does not beat a constant resizing (t —); does not beat the minimum-variance ratio (t 1.0) |
| name:spot:63 | downside | 354 | 39 | +0.0% | +0.0% | — | — | 1.0 | 0.00 (0.00) | no | does not beat the static rule (t —); does not beat a constant resizing (t —); does not beat the minimum-variance ratio (t 1.0) |
| name:spot:63 | drawdown | 354 | 39 | +2.5% | -0.0% | 2.8 | -0.0 | 2.6 | -0.00 (-0.00) | no | does not beat a constant resizing (t -0.0) |
| name:spot:63 | var95 | 354 | 39 | -221.7% | -4.0% | p 1.00 | p 0.81 | p 1.00 | -0.02 (-0.02) | no | does not beat the static rule (bootstrap p 1.00); does not beat a constant resizing (bootstrap p 0.81); does not beat the minimum-variance ratio (bootstrap p 1.00); decayed: worse than the static rule in the most recent third |
| name:spot:63 | es95 | 354 | 39 | -362.1% | -5.8% | p 1.00 | p 0.93 | p 1.00 | -0.02 (-0.02) | no | does not beat the static rule (bootstrap p 1.00); does not beat a constant resizing (bootstrap p 0.93); does not beat the minimum-variance ratio (bootstrap p 1.00); decayed: worse than the static rule in the most recent third |
| name:spot:63 | variance basic | 354 | 39 | +0.0% | +0.0% | — | — | 1.0 | 0.00 (0.00) | no | does not beat the static rule (t —); does not beat a constant resizing (t —); does not beat the minimum-variance ratio (t 1.0) |
| rates:spot:21 | variance | 478 | 120 | +5.6% | +0.4% | 5.8 | 1.4 | -3.7 | -0.15 (-0.15) | no | does not beat a constant resizing (t 1.4); does not beat the minimum-variance ratio (t -3.7) |
| rates:spot:21 | exposure | 478 | 120 | +20.5% | +5.0% | 6.9 | 3.6 | -4.1 | -0.14 (-0.14) | no | does not beat the minimum-variance ratio (t -4.1) |
| rates:spot:21 | downside | 478 | 120 | +6.4% | +0.5% | 4.9 | 1.7 | -3.6 | -0.14 (-0.15) | no | does not beat a constant resizing (t 1.7); does not beat the minimum-variance ratio (t -3.6) |
| rates:spot:21 | drawdown | 478 | 120 | +1.2% | +0.5% | 1.3 | 3.8 | -6.3 | -0.13 (-0.14) | no | does not beat the static rule (t 1.3); does not beat the minimum-variance ratio (t -6.3) |
| rates:spot:21 | var95 | 478 | 120 | -2.3% | +0.1% | p 0.49 | p 0.50 | p 0.87 | -0.05 (-0.05) | no | does not beat the static rule (bootstrap p 0.49); does not beat a constant resizing (bootstrap p 0.50); does not beat the minimum-variance ratio (bootstrap p 0.87); decayed: worse than the static rule in the most recent third |
| rates:spot:21 | es95 | 478 | 120 | +1.2% | +0.0% | p 0.02 | p 0.31 | p 0.81 | -0.05 (-0.05) | no | does not beat a constant resizing (bootstrap p 0.31); does not beat the minimum-variance ratio (bootstrap p 0.81) |
| rates:spot:21 | variance basic | 478 | 120 | +5.5% | +0.3% | 4.9 | 3.1 | -3.7 | -0.16 (-0.15) | no | does not beat the minimum-variance ratio (t -3.7) |
| rates:spot:5 | variance | 480 | 120 | +2.6% | +0.4% | 2.5 | 0.7 | -2.0 | -0.13 (-0.14) | no | does not beat a constant resizing (t 0.7); does not beat the minimum-variance ratio (t -2.0) |
| rates:spot:5 | exposure | 480 | 120 | +3.9% | +1.7% | 2.2 | 2.0 | -2.2 | -0.11 (-0.12) | no | does not beat a constant resizing (t 2.0); does not beat the minimum-variance ratio (t -2.2) |
| rates:spot:5 | downside | 480 | 120 | +5.1% | +0.4% | 2.7 | 0.8 | -1.8 | -0.11 (-0.11) | no | does not beat a constant resizing (t 0.8); does not beat the minimum-variance ratio (t -1.8) |
| rates:spot:5 | drawdown | 480 | 120 | +1.9% | +0.4% | 1.9 | 2.0 | -3.9 | -0.11 (-0.11) | no | does not beat the static rule (t 1.9); does not beat the minimum-variance ratio (t -3.9) |
| rates:spot:5 | var95 | 480 | 120 | +1.6% | +0.9% | p 0.22 | p 0.42 | p 0.98 | -0.05 (-0.05) | no | does not beat the static rule (bootstrap p 0.22); does not beat a constant resizing (bootstrap p 0.42); does not beat the minimum-variance ratio (bootstrap p 0.98) |
| rates:spot:5 | es95 | 480 | 120 | +1.6% | +0.5% | p 0.06 | p 0.09 | p 0.91 | -0.05 (-0.05) | no | does not beat the static rule (bootstrap p 0.06); does not beat a constant resizing (bootstrap p 0.09); does not beat the minimum-variance ratio (bootstrap p 0.91) |
| rates:spot:5 | variance basic | 480 | 120 | +2.4% | +0.2% | 1.8 | 0.9 | -2.0 | -0.13 (-0.14) | no | does not beat the static rule (t 1.8); does not beat a constant resizing (t 0.9); does not beat the minimum-variance ratio (t -2.0) |
| rates:spot:63 | variance | 472 | 39 | +5.6% | +0.3% | 3.8 | 0.8 | -2.3 | -0.16 (-0.16) | no | does not beat a constant resizing (t 0.8); does not beat the minimum-variance ratio (t -2.3) |
| rates:spot:63 | exposure | 472 | 39 | +26.1% | +5.6% | 4.2 | 2.2 | -2.2 | -0.15 (-0.15) | no | does not beat the minimum-variance ratio (t -2.2) |
| rates:spot:63 | downside | 472 | 39 | +6.7% | +0.4% | 3.9 | 1.4 | -2.8 | -0.16 (-0.16) | no | does not beat a constant resizing (t 1.4); does not beat the minimum-variance ratio (t -2.8) |
| rates:spot:63 | drawdown | 472 | 39 | +1.6% | +0.2% | 1.0 | 1.2 | -4.1 | -0.15 (-0.15) | no | does not beat the static rule (t 1.0); does not beat a constant resizing (t 1.2); does not beat the minimum-variance ratio (t -4.1) |
| rates:spot:63 | var95 | 472 | 39 | +3.6% | +0.4% | p 0.15 | p 0.48 | p 0.97 | -0.06 (-0.06) | no | does not beat the static rule (bootstrap p 0.15); does not beat a constant resizing (bootstrap p 0.48); does not beat the minimum-variance ratio (bootstrap p 0.97) |
| rates:spot:63 | es95 | 472 | 39 | +3.8% | -0.0% | p 0.00 | p 0.77 | p 0.98 | -0.06 (-0.06) | no | does not beat a constant resizing (bootstrap p 0.77); does not beat the minimum-variance ratio (bootstrap p 0.98) |
| rates:spot:63 | variance basic | 472 | 39 | +5.6% | +0.3% | 3.3 | 2.5 | -2.3 | -0.17 (-0.16) | no | does not beat the minimum-variance ratio (t -2.3) |
| rates:treasury_future:21 | variance | 1432 | 120 | +0.9% | +0.7% | 3.5 | 2.9 | — | -0.09 (-0.10) | yes | — |
| rates:treasury_future:21 | exposure | 1432 | 120 | +6.2% | +6.9% | 4.2 | 5.1 | — | -0.06 (-0.07) | yes | — |
| rates:treasury_future:21 | downside | 1432 | 120 | +1.2% | +0.7% | 2.3 | 3.1 | — | -0.09 (-0.09) | yes | — |
| rates:treasury_future:21 | drawdown | 1432 | 120 | +0.4% | +0.6% | 0.9 | 5.2 | — | -0.07 (-0.07) | no | does not beat the static rule (t 0.9) |
| rates:treasury_future:21 | var95 | 1432 | 120 | -0.6% | +0.4% | p 0.56 | p 0.44 | p — | -0.01 (-0.01) | no | does not beat the static rule (bootstrap p 0.56); does not beat a constant resizing (bootstrap p 0.44) |
| rates:treasury_future:21 | es95 | 1432 | 120 | -0.2% | +0.1% | p 0.69 | p 0.27 | p — | -0.01 (-0.01) | no | does not beat the static rule (bootstrap p 0.69); does not beat a constant resizing (bootstrap p 0.27); decayed: worse than the static rule in the most recent third |
| rates:treasury_future:21 | variance basic | 1432 | 120 | +0.4% | +0.1% | 0.9 | 1.8 | — | -0.10 (-0.10) | no | does not beat the static rule (t 0.9); does not beat a constant resizing (t 1.8) |
| rates:treasury_future:5 | variance | 1440 | 120 | +0.2% | +0.8% | 0.3 | 1.6 | — | -0.07 (-0.08) | no | does not beat the static rule (t 0.3); does not beat a constant resizing (t 1.6) |
| rates:treasury_future:5 | exposure | 1440 | 120 | +1.2% | +2.4% | 1.9 | 3.5 | — | -0.04 (-0.05) | no | does not beat the static rule (t 1.9) |
| rates:treasury_future:5 | downside | 1440 | 120 | +1.2% | +0.6% | 1.9 | 2.1 | — | -0.07 (-0.08) | no | does not beat the static rule (t 1.9) |
| rates:treasury_future:5 | drawdown | 1440 | 120 | +1.0% | +0.5% | 2.0 | 3.4 | — | -0.07 (-0.08) | yes | — |
| rates:treasury_future:5 | var95 | 1440 | 120 | -1.0% | +0.5% | p 0.52 | p 0.48 | p — | -0.03 (-0.03) | no | does not beat the static rule (bootstrap p 0.52); does not beat a constant resizing (bootstrap p 0.48); decayed: worse than the static rule in the most recent third |
| rates:treasury_future:5 | es95 | 1440 | 120 | +0.4% | +0.1% | p 0.23 | p 0.18 | p — | -0.03 (-0.03) | no | does not beat the static rule (bootstrap p 0.23); does not beat a constant resizing (bootstrap p 0.18) |
| rates:treasury_future:5 | variance basic | 1440 | 120 | -0.5% | +0.1% | -0.6 | 1.9 | — | -0.08 (-0.08) | no | does not beat the static rule (t -0.6); does not beat a constant resizing (t 1.9) |
| rates:treasury_future:63 | variance | 1416 | 39 | +0.6% | +0.7% | 2.0 | 2.3 | — | -0.09 (-0.09) | yes | — |
| rates:treasury_future:63 | exposure | 1416 | 39 | +5.8% | +8.5% | 2.1 | 3.2 | — | -0.06 (-0.06) | yes | — |
| rates:treasury_future:63 | downside | 1416 | 39 | +0.9% | +0.7% | 1.6 | 2.6 | — | -0.09 (-0.10) | no | does not beat the static rule (t 1.6) |
| rates:treasury_future:63 | drawdown | 1416 | 39 | -0.3% | +0.5% | -0.5 | 2.4 | — | -0.06 (-0.06) | no | does not beat the static rule (t -0.5); decayed: worse than the static rule in the most recent third |
| rates:treasury_future:63 | var95 | 1416 | 39 | -0.6% | -0.3% | p 0.78 | p 0.51 | p — | -0.01 (-0.02) | no | does not beat the static rule (bootstrap p 0.78); does not beat a constant resizing (bootstrap p 0.51); decayed: worse than the static rule in the most recent third |
| rates:treasury_future:63 | es95 | 1416 | 39 | -0.3% | +0.0% | p 0.68 | p 0.60 | p — | -0.01 (-0.02) | no | does not beat the static rule (bootstrap p 0.68); does not beat a constant resizing (bootstrap p 0.60); decayed: worse than the static rule in the most recent third |
| rates:treasury_future:63 | variance basic | 1416 | 39 | +0.1% | +0.2% | 0.3 | 2.0 | — | -0.10 (-0.09) | no | does not beat the static rule (t 0.3) |

| Metric | Groups tested | Verified |
|---|---|---|
| Variance | 33 | 5 |
| Residual factor exposure (beta / DV01 / CS01 / FX) | 27 | 2 |
| Downside semivariance | 33 | 5 |
| Drawdown | 33 | 7 |
| VaR 95% (window) | 33 | 1 |
| ES 95% (window) | 33 | 5 |
| Variance, original six features | 33 | 3 |

**Reading it honestly.** 225 group × metric tests were run; at a one-sided 2.3% level about 5 would pass by chance alone. Where a model passes, compare its mean adjustment with the constant's: when they are nearly equal and the gain vs the constant is a fraction of a percent, the useful information is the static rule's SIZING BIAS (what the constant learned), not conditional skill. The sizing bias is reported below; it is not applied to the static rule automatically.

| Group | Constant adjustment learned | Variance gain of the constant vs static | Model's gain vs static |
|---|---|---|---|
| commodity:option:21 | -0.23 (hedge × 0.89) | +25.8% | +29.1% |
| commodity:option:5 | -0.24 (hedge × 0.88) | +25.3% | +29.7% |
| commodity:option:63 | -0.19 (hedge × 0.90) | +20.8% | +24.1% |
| credit:spot:21 | -0.12 (hedge × 0.94) | +8.3% | +6.7% |
| credit:spot:5 | -0.09 (hedge × 0.95) | +8.3% | +8.0% |
| credit:spot:63 | -0.14 (hedge × 0.93) | +9.1% | +9.2% |
| crypto:spot:21 | -0.30 (hedge × 0.85) | +40.9% | +40.9% |
| crypto:spot:5 | -0.29 (hedge × 0.86) | +41.2% | +41.6% |
| crypto:spot:63 | -0.30 (hedge × 0.85) | +41.0% | +41.0% |
| equity:option:21 | -0.24 (hedge × 0.88) | +22.8% | +22.9% |
| equity:option:5 | -0.22 (hedge × 0.89) | +15.2% | +15.5% |
| equity:option:63 | -0.22 (hedge × 0.89) | +20.4% | +20.8% |
| fx:forward:21 | -0.28 (hedge × 0.86) | +4.2% | +4.2% |
| fx:forward:5 | -0.21 (hedge × 0.90) | +3.9% | +3.7% |
| fx:forward:63 | -0.30 (hedge × 0.85) | +4.5% | +4.5% |
| name:option:21 | -0.23 (hedge × 0.88) | +30.7% | +31.2% |
| name:option:5 | -0.22 (hedge × 0.89) | +30.5% | +31.5% |
| name:option:63 | -0.18 (hedge × 0.91) | +21.0% | +21.7% |
| rates:spot:21 | -0.15 (hedge × 0.92) | +5.2% | +5.6% |
| rates:spot:5 | -0.14 (hedge × 0.93) | +2.2% | +2.6% |
| rates:spot:63 | -0.16 (hedge × 0.92) | +5.3% | +5.6% |
| rates:treasury_future:21 | -0.10 (hedge × 0.95) | +0.2% | +0.9% |
| rates:treasury_future:5 | -0.08 (hedge × 0.96) | -0.6% | +0.2% |
| rates:treasury_future:63 | -0.09 (hedge × 0.95) | -0.1% | +0.6% |
