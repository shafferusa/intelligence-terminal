# Shaffer Hedge — design

Risk first, product second:

```
position(s) → risk vector (each risk in its own unit) → objective & target → products that carry that risk
→ each product sized in its own risk unit → optimiser (risk mismatch, cost, basis, liquidity) → Raw Shaffer Hedge
→ ML adjustment (separate, capped, only if verified) → Final Shaffer Hedge
```

Code: `finsim2/hedge/` — `market.py` (point-in-time inputs), `risk.py` (risk vector), `products.py` (registry, pricing per
instrument, sizing units, costs, eligibility), `pricing.py`, `series.py` (daily model marks for the ledger), `engine.py`
(objectives, candidates, optimiser, score, scenarios), `history.py` (walk-forward, baselines, ML), `scoring.py` (net long /
short Shaffer Scores), `service.py` (trade preview, atomic execution, hedge ledger, grading), `audit.py`
(`python -m finsim2 hedge-audit` → `HEDGE_AUDIT.md`). Product registry: `PRODUCT_REGISTRY.md`.

## 1. The risk vector

Every exposure is **dollars of P&L per unit move of a factor**, so a position's linear P&L is Σ_f R_f·Δf and its variance
Rᵀ Σ_F R (Σ_F: daily factor covariance over the trailing 252 sessions, point in time).

| Factor | Move | Exposure (what the table shows) |
|---|---|---|
| MKT | S&P 500 (SPY) daily return | beta-dollars = market value × β |
| SEC:XLK … SEC:XLC | sector ETF − SPY | beta-dollars to the spread |
| IND:SMH | semiconductors − technology | |
| STY:SIZE, STY:VALUE | IWM − SPY, IWD − IWF | |
| RATE:2Y/5Y/10Y/30Y | CMT yield change, bp | −DV01 by key rate |
| REAL:10Y | 10Y TIPS real yield, bp | −real DV01 |
| CREDIT:IG/HY | ICE BofA OAS change, bp (Baa−10Y before 2023-09) | −CS01 |
| FX:<CCY> | currency return vs USD | currency dollars |
| CMD:OIL/GAS/GOLD/SILVER/COPPER/AGRI/BROAD | commodity return | commodity dollars |
| CRYPTO | bitcoin return | crypto dollars |
| VOL | VIX change, points | $ per VIX point (options: vega × IV/VIX) |
| IDIO:<asset> | the asset's own residual | single-name dollars |

* **Structural** exposures come from the instrument: bond funds' duration split across key rates, credit funds' spread
  duration (floating-rate funds: rate duration ≈ 0.2y), currency notional (country ETFs look through to their currency),
  commodity funds 1:1, futures notional, option delta.
* **Empirical** exposures of equity-like assets: ridge regression of the asset's daily USD return on the market, its own
  sector spread, semiconductors (chip makers) and the two style spreads, shrunk toward an economic prior (market = the
  fund's leverage or 1, own sector 1, others 0), then a screen of macro factors on the residual (kept only if |t| ≥ 3 and
  it explains ≥ 2% of the residual). Bonds are not regressed on the market, so no risk is counted twice.
* Options add Greeks: delta-dollars (the linear exposure), gamma-$ per (1% move)², vega-$ per vol point, theta-$ per day.
* Risk factors, the covariance and all history use the same-day close (lag 0); live pricing uses FRED's publication lag.

## 2. Products and sizing units

Each product's exposure per unit **is** its sizing unit, so one rule sizes them all correctly:
units = (required change in the targeted exposure) ÷ (exposure per unit), projected on the targeted factors.

| Product | One unit's exposure | Rule |
|---|---|---|
| Stock / ETF | price × β (per share) | shares = beta-$ ÷ (price × β) |
| Equity index future | F × multiplier × β | contracts = beta-$ ÷ (F × multiplier × β) — margin is collateral, not exposure |
| Option | Δ × S × 100 × β (+ Γ, vega, θ) | contracts = delta-$ ÷ (S × 100 × \|Δ\| × β) — the premium is the cost |
| Treasury future | −DV01 per contract (CTD key rate) | contracts = DV01 ÷ DV01 per contract |
| Bond / credit fund | −price × D × 1e-4 (DV01), −price × SD × 1e-4 (CS01) | shares = DV01 or CS01 ÷ per-share figure |
| FX forward / future | currency notional | notional = currency exposure to remove |
| Commodity fund | commodity $ per share | shares = commodity $ ÷ price |
| Crypto fund | crypto $ per share | shares = crypto $ ÷ price |
| VIX ETN | $ per VIX point (empirical) | vega-equivalent |

Pricing (`pricing.py`): options by Black-Scholes-Merton (European) or CRR (American), implied volatility from the Cboe
volatility index for that underlying (VIX + 3-month VIX interpolated in variance for the S&P 500; VXN, RVX, VXD, GVZ, OVX,
VXEEM, VXEWZ, and the equity VIXes for AAPL/AMZN/GOOGL/GS/IBM), flat across strikes; equity-index futures at fair value
S·e^{(r−q)T}; Treasury futures as the forward price of the 6% notional coupon at the approximate CTD maturity on the CMT
curve; FX forwards and futures by covered interest parity with FRED short rates. None of these is a market quote for the
contract itself, and the registry says so.

## 3. Objectives and targets

Grouped by what the hedge is for, because that decides how a product is judged: **Variance reduction** (minimum
variance, target volatility), **Beta reduction** (reduce / neutralize beta), **Tail protection** (crash, Expected
Shortfall, VaR — judged on the worst 10% of walk-forward windows), **Drawdown protection** (judged on the average
in-window drawdown), **Factor neutralization** (systematic, sector, name, duration, curve, credit, FX, commodity,
crypto, volatility — judged on variance). Every candidate also carries its **hedge type** from the walk-forward:
variance hedge, tail hedge, both, "tail hedge (adds variance)" — an out-of-the-money index put is typically the
last — or weak. An option that is a poor variance hedge can be an excellent tail hedge; it scores low under a
variance objective and high under Tail protection.

Option prices are labelled **MODEL-PRICED — FLAT VOLATILITY ASSUMPTION**: without chains or skew, out-of-the-money
puts are probably priced too cheaply, which flatters crash hedges. Futures and forwards are labelled fair-value
model prices.

beta · neutral · **systematic** (all factors except single-name residual and style tilts — the trade-ticket default) ·
sector · name (a position: all its exposures) · duration (covariance-weighted) · curve (key rate by key rate) · credit ·
fx · commodity · crypto · volatility · crash (sized on the −20% scenario, full option re-pricing) · var · es · drawdown ·
min_variance · target_vol. Target R*: targeted factors × (1 − hedge %), or an explicit target beta / volatility.

## 4. The optimiser

```
min_q  (γh/2)·[ w_S·(X_S − R*_S)ᵀ Σ_SS (X_S − R*_S) + X_Nᵀ Σ_NN X_N ] + Σ_j c_j|q_j| + Σ_j k_j|q_j|^1.5
X = R + Bq  (after-hedge exposures; B = exposures per unit of each hedge; the hedges' own residuals are in X_N = basis)
γ = A/NAV (A = 2), h = horizon in sessions, w_S = 10,000 when sizing (the user's target binds), 50 when choosing products
and whole lots (a lot-granularity miss must not outweigh a cost difference); 1 for variance objectives.
Multi-factor objectives match each targeted factor on its own (no cross-covariance between targeted factors).
c_j: expected frictions over the horizon (below); k_j: square-root market impact 0.5·σ·√(notional/ADV).
```

Forward stepwise selection (the product that lowers J most, up to 2 legs, 3 for systematic/name/variance objectives; a
later leg must improve J by ≥ 2%), whole contracts/shares by searching the neighbouring integers. A held asset is never
used to hedge itself except for the single-name objective, bounded by the holding (never flipped).

**Costs** count only frictions — the expected P&L relative to a frictionless, fair-value hedge with the same exposure, idle
cash earning the bill rate: spread (Corwin–Schultz high/low estimate, ≥ 1bp), commission ($0 stocks, $2.25/futures side,
$0.65/option), futures ticks and rolls, borrow (general collateral 0.30%/yr — assumed), no interest on short proceeds
(retail), option volatility premium (premium at implied vol − value at the GARCH forecast vol), fund structural drift
(trailing-3-year residual return beyond the (1 − β)·r a fully funded fund earns, only if |t| ≥ 2), leveraged-fund
volatility drag L(L−1)σ²/2. Carry embedded in futures/forward prices and dividends owed by shorts are shown, not counted.

## 5. The Shaffer Hedge Score

```
SH_j = 100·tanh(E·Q·L·R·B·T / 1.0)
E  x = walk-forward realised ÷ expected variance reduction (tail objectives: tail-loss reduction ÷ requested share;
   drawdown: drawdown reduction ÷ requested share);
   E = min(x, 2.5 − x) in [−1, 1.25]: over-delivering beyond 1.25× the request is over-hedging and is penalised
Q  value of the risk removed ÷ (value + expected cost), value = (γh/2)·ΔVar (target-weighted)
L  1 / (1 + participation/10%), participation = notional ÷ average daily traded value (tracking ETF for futures/options)
R  realised reduction in windows that began in today's market and volatility regime ÷ all windows, shrunk n/(n+20), [0.5, 1.5]
B  ρ² of the product's P&L with the targeted P&L (how much of the risk it can remove at all)
T  crash objective: hedge gain in the −20% scenario ÷ the gain of a linear hedge with the same delta, [0.5, 1.5]; else 1
```

## 6. Walk-forward validation and the ML adjustment

At each month-start t (≤ 15 years back), with data up to t only: the book's exposure (trailing-252-day beta, or the
structural DV01/CS01/currency/commodity exposure), the hedge's exposure per unit at t (its beta, the model DV01, the
option's delta from that day's Cboe index), the hedge quantity by the product's rule; then the realised daily P&L over
(t, t+h]. Futures roll at fair value; options are re-priced daily (static and dynamic delta variants), roll at expiry.
Baselines on the same windows: no hedge, fixed 25%, fixed 50%, the static rule (= the Raw Shaffer Hedge ratio) and the
minimum-variance ratio for the same target share.

ML: a ridge model per (risk, hedge kind, horizon) of log(ex-post minimum-variance multiple × target share) — the error in
the hedge's beta — from the VIX, 1-year and 3-month correlation, the change in beta, the 3-month market return and the bill
rate, trained walk-forward on windows that ended before each prediction.

```
FinalHedge = RawHedge × (1 + 0.5 · MLAdjustment),  |MLAdjustment| ≤ 0.30  →  at most ±15% of the raw hedge
used only if, out of sample, it beats the static rule AND the minimum-variance ratio (paired t ≥ 2, n_eff ≥ 30) and has
not decayed in the most recent third; otherwise MLAdjustment = 0 and the reason is shown
```

Result (`HEDGE_AUDIT.md`, data to 2026-09-24, 110 cases × 1W/1M/3M, full-hedge target): verified in one group only
(single-name hedges at 3M); everywhere else the adjustment is 0.

## 7. Trades, execution and the hedge ledger

The trade ticket prices the trade, computes the risk it adds (the trade's own risk vector) and the book before the trade,
after it and after the hedge, and proposes the systematic hedge; the user picks 25/50/75/100%/custom, an alternate product,
or declines. **Trade + Shaffer Hedge** submits every leg to `Ledger.trade_package`: validated together (cash, 150% short
collateral, futures margin, holdings, no option writing) and inserted in one SQLite transaction — all legs or none; a
failing package reports "no leg was recorded". Every executed or declined proposal is appended to `hedge_recommendations`
and graded once after its horizon: realised variance reduction, hedge P&L, upside given up, basis error, effectiveness.

## 8. What it cannot do (data limits)

No option chains (the Cboe chain host is not reachable here): no skew, no single-stock options without a Cboe volatility
index (NVDA puts are ineligible), bid/ask of options assumed. Futures and forwards are model-priced, not quoted; no open
interest or futures volume. Commodity and crypto futures, CDS/CDX, swaps, swaptions, caps/floors, VIX futures/options,
variance swaps and structured notes are not tradeable (see the registry). Borrow rates are assumed (general collateral).
Cash earns no interest in the ledger.
