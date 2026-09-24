# FinSim2 product risk registry

Generated from `finsim2/hedge/products.py` (the code is the source of truth). Every product type FinSim2 knows, its status,
how FinSim2 holds it, the risk units it carries, the unit its hedges are sized in, the Shaffer signal families that apply,
the costs counted, and — for anything not fully supported — why.

Status: **SUPPORTED** priced from market quotes, risk-modelled, tradeable · **MODELLED** priced by a stated model from observed
inputs, tradeable · **PROXY** only through a listed fund (the fund itself is SUPPORTED) · **PARTIAL** · **ANALYSIS_ONLY** risk and
sizing shown, not tradeable · **NOT_SUPPORTED** no data or model: never recommended, never guessed.

A product may be recommended by Shaffer Hedge only with (1) current market data, (2) a pricing model, (3) contract/notional
conventions, (4) a risk model and (5) a sizing rule; otherwise the UI shows **PRODUCT NOT ELIGIBLE FOR SHAFFER HEDGE** and the reason.

Counts: ANALYSIS_ONLY 3, MODELLED 9, NOT_SUPPORTED 28, PARTIAL 2, PROXY 12, SUPPORTED 8 (total 62).


## Cash / short-term

| Product | Status | In FinSim2 via | Risk units | Sizing rule | Signal families | Costs counted | Limitation / reason |
|---|---|---|---|---|---|---|---|
| Cash (USD) | SUPPORTED | ledger cash | none (base currency) | — | Rates (opportunity cost) | none | Cash earns no interest in the ledger; foreign-currency cash is held through FX pairs. |
| Money-market funds | PROXY | SGOV | DV01 (≈0.1y) | DV01 | Rates | spread | No money-market fund NAV/yield data; SGOV (0–3 month T-bill ETF) stands in. |
| Treasury bills | PROXY | BIL, SGOV; DGS1MO/DGS3MO for pricing | DV01 | DV01 | Rates | spread | Individual bills are not traded in the ledger; T-bill ETFs are. |

## Equities

| Product | Status | In FinSim2 via | Risk units | Sizing rule | Signal families | Costs counted | Limitation / reason |
|---|---|---|---|---|---|---|---|
| Common stock (long) | SUPPORTED | 45 US stocks + any Yahoo symbol | beta-$ (market, sector, industry), idiosyncratic $ | shares = beta-$ ÷ (price × β) | Momentum, Trend, Mean Reversion, Valuation, Fundamental Quality/Growth, Risk-Adjusted, Volatility, Statistical, Macro, Relative Value | spread (Corwin–Schultz), commission $0 |  |
| Short stock | SUPPORTED | SHORT/COVER in the ledger | negative beta-$ | shares = beta-$ ÷ (price × β) | Momentum, Trend, Mean Reversion, Valuation, Fundamental Quality/Growth, Risk-Adjusted, Volatility, Statistical, Macro, Relative Value (net of borrow and dividends) | spread, borrow (assumed GC 0.30%/yr), dividends owed, 150% Reg-T collateral | Borrow availability and hard-to-borrow fees are not available: general-collateral borrow is assumed. |
| Preferred stock | PROXY | PFF | DV01, CS01 | DV01 / CS01 | Rates, Credit, Momentum | spread | Issue-level preferreds (call schedules, dividend coverage) are not modelled; PFF is. |
| REITs | PROXY | VNQ, XLRE | beta-$, rate sensitivity (empirical) | beta-$ | Momentum, Trend, Mean Reversion, Valuation, Fundamental Quality/Growth, Risk-Adjusted, Volatility, Statistical, Macro, Relative Value | spread | FFO/AFFO and NAV premium are not available from SEC companyfacts in a consistent form. |
| ADRs / foreign equities | PARTIAL | country ETFs (EWJ, EWG, …), foreign indices; add ADRs by symbol | beta-$ + currency $ (look-through) | beta-$; FX notional | Momentum, Trend, Mean Reversion, Valuation, Fundamental Quality/Growth, Risk-Adjusted, Volatility, Statistical, Macro, Relative Value, FX | spread | ADR/local-share basis and country risk premia are not modelled. |

## Funds

| Product | Status | In FinSim2 via | Risk units | Sizing rule | Signal families | Costs counted | Limitation / reason |
|---|---|---|---|---|---|---|---|
| ETFs | SUPPORTED | ≈80 ETFs | beta-$ / DV01 / CS01 / commodity $ by what it holds | per risk unit | by holdings | spread | Premium/discount to NAV and flows are not available. |
| Leveraged ETFs | SUPPORTED | SSO, QLD, TQQQ | beta-$ (≈ leverage × index beta, daily) | beta-$ | Momentum, Trend, Mean Reversion, Valuation, Fundamental Quality/Growth, Risk-Adjusted, Volatility, Statistical, Macro, Relative Value; volatility drag L(L−1)σ²/2 per year | spread, expense ratio (not modelled) |  |
| Inverse ETFs | SUPPORTED | SH, SDS, PSQ, SQQQ | negative beta-$ (daily reset) | beta-$ | Momentum, Trend, Mean Reversion, Valuation, Fundamental Quality/Growth, Risk-Adjusted, Volatility, Statistical, Macro, Relative Value; volatility drag | spread |  |
| ETNs | PARTIAL | VXX | VIX-futures $ per VIX point (empirical) | vega-equivalent $ | Volatility | spread | Issuer (Barclays) credit risk is not modelled. |
| Closed-end funds | NOT_SUPPORTED | — | — | — | — | — | No NAV series: the discount/premium, the main CEF signal, cannot be computed. |

## Bonds / credit

| Product | Status | In FinSim2 via | Risk units | Sizing rule | Signal families | Costs counted | Limitation / reason |
|---|---|---|---|---|---|---|---|
| Government bonds | MODELLED | UST2Y/5Y/10Y/30Y constant-maturity indices; Treasury ETFs | DV01 by key rate (2/5/10/30Y) | DV01 | Rates, Momentum, Trend | spread (ETFs); none (index) |  |
| TIPS / inflation-linked | PROXY | TIP, SCHP (DFII10 real yield) | real-yield DV01 | real DV01 | Rates (real), Macro (inflation) | spread | Individual TIPS and index ratios are not modelled. |
| Municipal bonds | PROXY | MUB | DV01 | DV01 | Rates | spread | Tax-equivalent yield and issuer credit are not modelled. |
| Investment-grade corporates | PROXY | LQD, VCIT; CORP_BAA index | DV01 + CS01 (IG OAS) | DV01 / CS01 | Rates, Credit | spread | Issuer-level bonds are not available. |
| High-yield bonds | PROXY | HYG, JNK | DV01 + CS01 (HY OAS) | CS01 | Credit, Rates, Macro | spread | Default probabilities and recoveries are not available. |
| Floating-rate notes | PROXY | FLOT | CS01 (spread duration ≈2y), DV01 ≈0 | CS01 | Credit | spread |  |
| Bank / leveraged loans | PROXY | BKLN | CS01 (HY), DV01 ≈0 | CS01 | Credit, Macro | spread |  |
| Agency MBS | PROXY | MBB | DV01 (negative convexity not modelled) | DV01 | Rates | spread | No OAS, prepayment or pool data: negative convexity is only visible empirically. |
| ABS | NOT_SUPPORTED | — (JAAA covers AAA CLOs only) | — | — | — | — | No collateral, subordination or ABS spread data. |
| Convertible bonds | PROXY | CWB | beta-$ (empirical) | beta-$ | Momentum, Trend, Mean Reversion, Valuation, Fundamental Quality/Growth, Risk-Adjusted, Volatility, Statistical, Macro, Relative Value | spread | The bond-floor + option decomposition needs issue terms, which are not available. |

## Futures

| Product | Status | In FinSim2 via | Risk units | Sizing rule | Signal families | Costs counted | Limitation / reason |
|---|---|---|---|---|---|---|---|
| Equity index futures | MODELLED | ES, MES, NQ, MNQ, RTY, M2K, YM, MYM | beta-$ = price × multiplier × β | contracts = beta-$ ÷ (F × multiplier × β) | underlying index + carry (r − q) | 1 tick + $2.25/contract/side; roll at each quarter; margin (SPAN-like estimate) is collateral, not exposure | Priced at fair value S·e^{(r−q)T} (index close, 3-month bill, trailing dividend yield of the tracking ETF), not a futures quote. |
| Single-stock futures | NOT_SUPPORTED | — | — | — | — | — | No US single-stock futures have traded since OneChicago closed (2020). |
| Treasury futures | MODELLED | ZT, ZF, ZN, TN, ZB, UB | DV01 per contract at the CTD key rate | contracts = DV01 ÷ DV01 per contract | Rates + carry | 1 tick + $2.25/contract/side | Modelled as the forward price of the 6% notional coupon at the approximate cheapest-to-deliver maturity on the Treasury curve; conversion factors, delivery options and the actual CTD are not modelled. |
| Commodity futures | ANALYSIS_ONLY | CL, BZ, NG, GC, SI, HG (continuous front-month quotes) | commodity $ = price × multiplier | contracts = commodity $ ÷ (price × multiplier) | Momentum, Trend, Macro | — | Only continuous front-month series are available: without per-contract prices a held position would book roll gaps as P&L. Commodity ETFs (USO, UNG, GLD, SLV, CPER, DBA, DBC) are used for hedging instead. |
| FX futures | MODELLED | 6E, 6B, 6J, 6A, 6C, 6S | currency $ = contract size × price | contracts = currency $ ÷ (size × price) | FX + carry | 1 tick + $2.25/contract/side | Priced by covered interest parity from spot and FRED short rates (CHF's rate series ended in 2024, so 6S is ineligible). |
| Crypto futures | NOT_SUPPORTED | — | — | — | — | — | No futures basis or term-structure data; BITO (futures ETF) and IBIT are the tradeable proxies. |

## FX / forwards

| Product | Status | In FinSim2 via | Risk units | Sizing rule | Signal families | Costs counted | Limitation / reason |
|---|---|---|---|---|---|---|---|
| FX spot | SUPPORTED | 10 pairs + DXY | currency $ | notional = currency exposure | Momentum, Trend, Rates, Macro | spread |  |
| FX forwards | MODELLED | FWD:<pair>:<date> | currency $ | notional = currency exposure to remove | FX + forward carry (rate differential) | 1bp of notional | Covered interest parity with FRED short rates (USD 3M bill, ECB deposit rate, SONIA, monthly JPY/CAD/AUD call rates). |
| NDFs | NOT_SUPPORTED | — | — | — | — | — | No onshore/offshore rate or NDF-point data (INR, CNY). |
| Commodity forwards | NOT_SUPPORTED | — | — | — | — | — | No storage, convenience-yield or forward-curve data. |

## Options

| Product | Status | In FinSim2 via | Risk units | Sizing rule | Signal families | Costs counted | Limitation / reason |
|---|---|---|---|---|---|---|---|
| Equity calls | MODELLED | AAPL, AMZN, GOOGL, GS, IBM (Cboe equity VIX indices) | delta-$, gamma, vega, theta | contracts = delta-$ ÷ (S × 100 × |Δ| × β) | underlying Shaffer + (forecast vol − implied vol) − theta | premium, spread (5%), $0.65/contract | Other single stocks (e.g. NVDA) have no implied-volatility data and are not eligible; flat volatility (no skew). |
| Equity puts | MODELLED | same as calls | delta-$, gamma, vega, theta | contracts = delta-$ ÷ (S × 100 × |Δ| × β) | as calls, bearish direction | premium, spread, commission | As calls; OTM put premiums are likely understated without skew. |
| Index / ETF options | MODELLED | SPX, SPY, NDX, QQQ, RUT, IWM, DJI, DIA, EEM, EWZ (VIX, VXV, VXN, RVX, VXD, VXEEM, VXEWZ) | delta-$, gamma, vega, theta | contracts = delta-$ ÷ (S × 100 × |Δ| × β) | direction + vol pricing + convexity | premium, spread (2–3%), commission | Implied volatility is the index's 30-day (and for the S&P 500 the 3-month) level, flat across strikes: no skew data. |
| FX options | NOT_SUPPORTED | — | — | — | — | — | No FX implied-volatility data (EVZ was discontinued in 2025). |
| Commodity options | MODELLED | options on GLD and USO (GVZ, OVX) | delta-$, vega | delta-$ | direction + vol | premium, spread (3%) | ETF options only; options on futures are not modelled. |
| Crypto options | NOT_SUPPORTED | — | — | — | — | — | No crypto implied-volatility data. |
| Warrants | NOT_SUPPORTED | — | — | — | — | — | No warrant terms or dilution data. |

## Rates derivatives

| Product | Status | In FinSim2 via | Risk units | Sizing rule | Signal families | Costs counted | Limitation / reason |
|---|---|---|---|---|---|---|---|
| Interest-rate swaps | ANALYSIS_ONLY | DV01 per $ notional from the Treasury curve | DV01 | notional = DV01 ÷ DV01 per $ | Rates | — | No SOFR swap curve: swap spreads are unknown, so a held swap cannot be valued faithfully. |
| OIS | NOT_SUPPORTED | — | — | — | — | — | No OIS curve. |
| FRAs | NOT_SUPPORTED | — | — | — | — | — | No forward-rate (term SOFR/futures) curve. |
| Caps | NOT_SUPPORTED | — | — | — | — | — | No interest-rate volatility data. |
| Floors | NOT_SUPPORTED | — | — | — | — | — | No interest-rate volatility data. |
| Swaptions | NOT_SUPPORTED | — | — | — | — | — | No swaption volatility surface. |

## Other swaps

| Product | Status | In FinSim2 via | Risk units | Sizing rule | Signal families | Costs counted | Limitation / reason |
|---|---|---|---|---|---|---|---|
| Total return swaps | NOT_SUPPORTED | — | — | — | — | — | No dealer financing spreads; the equity-index future carries the same exposure and is supported. |
| Equity swaps | NOT_SUPPORTED | — | — | — | — | — | As TRS. |
| Commodity swaps | NOT_SUPPORTED | — | — | — | — | — | No commodity forward curves. |
| FX swaps | NOT_SUPPORTED | — | — | — | — | — | Equivalent to spot + forward; the forward is supported. |
| Cross-currency swaps | NOT_SUPPORTED | — | — | — | — | — | No cross-currency basis data. |
| Inflation swaps | NOT_SUPPORTED | — | — | — | — | — | No zero-coupon inflation swap quotes (TIPS breakevens are available for analysis). |

## Credit derivatives

| Product | Status | In FinSim2 via | Risk units | Sizing rule | Signal families | Costs counted | Limitation / reason |
|---|---|---|---|---|---|---|---|
| CDS — buy protection | NOT_SUPPORTED | — | — | — | — | — | No single-name CDS spreads. |
| CDS — sell protection | NOT_SUPPORTED | — | — | — | — | — | No single-name CDS spreads. |
| CDX / iTraxx | NOT_SUPPORTED | — | — | — | — | — | No index quotes; credit risk is hedged with HYG/JNK/LQD sized by CS01. |

## Volatility

| Product | Status | In FinSim2 via | Risk units | Sizing rule | Signal families | Costs counted | Limitation / reason |
|---|---|---|---|---|---|---|---|
| Variance swaps | ANALYSIS_ONLY | S&P 500 (fair strike ≈ VIX²) | vega notional | vega notional | Volatility | — | The VIX approximates the 30-day fair strike, but there is no dealer market data to value a held swap. |
| Volatility swaps | NOT_SUPPORTED | — | — | — | — | — | No volatility-swap quotes. |
| VIX futures | NOT_SUPPORTED | — | — | — | — | — | No VIX futures curve (the Cboe futures host is not reachable); VXX is the tradeable proxy. |
| VIX options | NOT_SUPPORTED | — | — | — | — | — | Needs the VIX futures curve. |

## Crypto

| Product | Status | In FinSim2 via | Risk units | Sizing rule | Signal families | Costs counted | Limitation / reason |
|---|---|---|---|---|---|---|---|
| Crypto spot | SUPPORTED | BTC, ETH, SOL (long only) | crypto $ | notional | Momentum, Trend, Volatility, Liquidity, Macro | spread | Shorting spot crypto is not supported (no borrow); IBIT/BITO can be shorted. |
| Crypto perpetuals | NOT_SUPPORTED | — | — | — | — | — | No funding-rate or open-interest data. |

## Structured

| Product | Status | In FinSim2 via | Risk units | Sizing rule | Signal families | Costs counted | Limitation / reason |
|---|---|---|---|---|---|---|---|
| Structured notes | NOT_SUPPORTED | — | — | — | — | — | No payoff model is registered for any note; FinSim2 refuses rather than guesses. |

## Contract conventions

| Root | Contract | Kind | Underlying | Multiplier / size | CTD maturity (Treasury) |
|---|---|---|---|---|---|
| ES | E-mini S&P 500 | equity_index | SPX | 50 |  |
| MES | Micro E-mini S&P 500 | equity_index | SPX | 5 |  |
| NQ | E-mini Nasdaq-100 | equity_index | NDX | 20 |  |
| MNQ | Micro E-mini Nasdaq-100 | equity_index | NDX | 2 |  |
| RTY | E-mini Russell 2000 | equity_index | RUT | 50 |  |
| M2K | Micro E-mini Russell 2000 | equity_index | RUT | 5 |  |
| YM | E-mini Dow ($5) | equity_index | DJI | 5 |  |
| MYM | Micro E-mini Dow | equity_index | DJI | 0.5 |  |
| ZT | 2-Year T-Note | treasury |  | 200000 | 1.9 |
| ZF | 5-Year T-Note | treasury |  | 100000 | 4.2 |
| ZN | 10-Year T-Note | treasury |  | 100000 | 6.6 |
| TN | Ultra 10-Year T-Note | treasury |  | 100000 | 9.5 |
| ZB | U.S. Treasury Bond | treasury |  | 100000 | 15.5 |
| UB | Ultra U.S. Treasury Bond | treasury |  | 100000 | 25.0 |
| 6E | Euro FX | fx | EURUSD | 125000 |  |
| 6B | British Pound | fx | GBPUSD | 62500 |  |
| 6J | Japanese Yen | fx | USDJPY | 12500000 |  |
| 6A | Australian Dollar | fx | AUDUSD | 100000 |  |
| 6C | Canadian Dollar | fx | USDCAD | 100000 |  |
| 6S | Swiss Franc | fx | USDCHF | 125000 |  |
| CL | WTI Crude Oil | commodity | WTI | 1000 |  |
| BZ | Brent Crude | commodity | BRENT | 1000 |  |
| NG | Henry Hub Natural Gas | commodity | NATGAS | 10000 |  |
| GC | Gold | commodity | GOLD | 100 |  |
| SI | Silver | commodity | SILVER | 5000 |  |
| HG | Copper | commodity | COPPER | 25000 |  |
| BTC | CME Bitcoin | crypto | BTC | 5 |  |
| MBT | CME Micro Bitcoin | crypto | BTC | 0.1 |  |

Options: 100 shares per contract; European (cash-settled) on SPX/NDX/RUT/DJI, American on ETFs and stocks (settled at intrinsic value in the ledger). Margin estimates (collateral, never exposure): equity_index 6.0%, treasury 2.5%, fx 4.0%, forward 5.0% of notional; short sales need 150% of their value (Reg T).

## ProductRiskProfile fields

`product_type`, `underlying`, `currency`, `market_value`, `notional`, `equity_beta`, `factor_betas`, `delta`, `gamma`, `vega`, `theta`, `rho`, `duration`, `convexity`, `DV01`, `key_rate_DV01`, `spread_duration`, `CS01`, `FX_exposure`, `commodity_exposure`, `crypto_exposure`, `inflation_exposure`, `volatility_exposure`, `liquidity`, `financing`, `borrow_cost`, `carry` — fields a product does not have are `null`, never invented.

## Risk-factor units

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
