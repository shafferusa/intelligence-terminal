# Owner directive 2026-10-28 (~18:10 ET), verbatim: Shaffer Personal Fund ($1M, book P / lifetime P1)

Live state when received (read-only API, 18:11 ET): Personal Book P1 cash USD 1,000,000.00 in P1:TREASURY, NAV 1,000,661.95, no positions; the
owner's 12 orders O-000043..O-000054 in P1:US_EXCHANGE, all MARKET / DAY for the next session: O-000054 LMT BUY 100, O-000053 SPCX BUY 300,
O-000052 ORCL SELL_SHORT 400, O-000051 PANW BUY 100, O-000050 CRWD BUY 100 (WORKING); PK-000005 TSLA 2027-02-19 400 synthetic short:
O-000049 STO 4 calls + O-000048 BTO 4 puts (WORKING); PK-000004 NVDA collar: O-000045 SELL_SHORT 200 (WORKING) with O-000046 BTO 2 Feb-27 235
calls and O-000047 STO 2 Feb-27 195 puts (WAITING_PARENT); PK-000003 AAPL: O-000043 SELL_SHORT 200 (WORKING) with O-000044 BTO 2 Jun-27 380 calls
(WAITING_PARENT).

---

# CLAUDE CODE — SHAFFER PERSONAL FUND
## $1 MILLION | EXCHANGE-TRADED LONG/SHORT PORTFOLIO | EXECUTE OPENING ORDER BOOK

You are the lead trading systems engineer and institutional execution specialist for **FinSim1**.

**Repository:** `shafferusa/intelligence-terminal`  
**Local project:** `C:\Users\logan\intelligence-terminal`

Your assignment is to complete and submit the opening order book for my **$1,000,000 Shaffer Personal Fund**.

This is a separate portfolio from both my existing $25 million paper fund and the proposed $1 billion institutional fund.

**Do not modify either of those funds. Do not touch FinSim2 or the newspaper application.**

## 1. Portfolio mandate

- Fund name: Shaffer Personal Fund
- Starting capital: $1,000,000
- Base currency: USD
- Allowed products: Exchange-listed equities, ETFs, options, Treasury bonds and listed futures
- Excluded products: OTC total-return swaps, CDS, IRS, FX forwards and other OTC derivatives
- Investment style: Aggressive directional long/short investing
- Options objective: Standalone speculative profits, not automatic portfolio hedging, except for the explicitly authorized existing AAPL and NVDA order packages
- Investment themes: Bullish defense, space, cybersecurity, healthcare and gold; bearish excessive AI valuations, housing, Bitcoin and near-term oil
- Starting liquidity target: Approximately $50,000–$75,000 of uncommitted capital after opening orders

**The $1 million restriction applies to the fund's contributed capital and available funding. It is not permission to create unlimited leverage or bypass margin requirements.**

Do not transfer or borrow capital from another FinSim fund.

## 2. Preserve my existing orders

The following 12 order tickets are ALREADY SUBMITTED.

**DO NOT SUBMIT DUPLICATES. DO NOT CANCEL, MODIFY, REPLACE OR REPRICE THEM WITHOUT EXPLICIT AUTHORIZATION.**

| Order ID | Instrument | Action | Quantity | Status at last report |
|---|---|---|---:|---|
| O-000054 | LMT | BUY | 100 | WORKING |
| O-000053 | SPCX | BUY | 300 | WORKING |
| O-000052 | ORCL | SELL_SHORT | 400 | WORKING |
| O-000051 | PANW | BUY | 100 | WORKING |
| O-000050 | CRWD | BUY | 100 | WORKING |
| O-000049 | TSLA270219C00400000 | STO | 4 | WORKING |
| O-000048 | TSLA270219P00400000 | BTO | 4 | WORKING |
| O-000047 | NVDA270219P00195000 | STO | 2 | WAITING_PARENT |
| O-000046 | NVDA270219C00235000 | BTO | 2 | WAITING_PARENT |
| O-000045 | NVDA | SELL_SHORT | 200 | WORKING |
| O-000044 | AAPL270617C00380000 | BTO | 2 | WAITING_PARENT |
| O-000043 | AAPL | SELL_SHORT | 200 | WORKING |

Preserve all parent/child and all-or-none package relationships.

Existing strategies:

**TSLA synthetic short:** Buy four February 19, 2027 $400 puts and sell four February 19, 2027 $400 calls as one atomic package. Do not allow one leg to fill independently if the strategy requires all-or-none execution.

**NVDA short collar:** Short 200 NVDA shares, buy two February 19, 2027 $235 calls and sell two February 19, 2027 $195 puts. Keep the existing parent-child relationship. Model short-put assignment and margin risk.

**AAPL protected short:** Short 200 AAPL shares and buy two June 17, 2027 $380 calls. Keep the existing conditional relationship.

**SPCX:** Treat this as the public equity instrument identified by the current FinSim security master. Verify the actual listing and executable quote; do not substitute an unrelated synthetic or private instrument.

Check the existing order states in the database before proceeding. Some orders may have filled or changed status since the supplied snapshot.

## 3. Opening capital allocation

Use this as the provisional funding plan:

| Category | Target commitment/reserve |
|---|---:|
| Existing four long-equity buy orders | $172,659 |
| Existing three short-stock packages — provisional margin reserve | $254,487 |
| TSLA synthetic short — provisional margin reserve | $161,688 |
| Treasury securities | $300,000 |
| New healthcare and defense stock purchases | $30,000 |
| New speculative option premiums | $25,000 |
| **Total provisional commitments** | **$943,834** |
| **Remaining buffer** | **$56,166** |

These are planning estimates, NOT confirmed margin requirements.

Recalculate everything from live simulator prices, actual option premiums, house margin rules, net credit/debit requirements, settlement, and account buying power.

Do not hardcode any of these provisional reserves as actual accounting entries.

If the new trades would leave insufficient liquidity, reduce or defer **new** trades in this priority order:

1. Individual Treasury bond purchases.
2. SGOV allocation.
3. New discretionary stock purchases.
4. New speculative options.

Do not alter the 12 existing orders to force this target.

Keep at least $50,000 of truly available liquidity if feasible under the existing positions, including maintenance and variation-margin obligations. If that cannot be achieved, report the shortfall and refrain from placing new orders that worsen it.

## 4. Add $300,000 in Treasury securities

Submit eligible buy orders, subject to funding and quote checks:

### A. SGOV

Instrument: SGOV — iShares 0–3 Month Treasury Bond ETF

Target purchase market value: **$150,000**

Convert dollars into whole ETF shares using executable quotes.

Use a limit order with defensible price controls.

### B. Treasury bond 912810FP8

CUSIP: **912810FP8**

Target purchase market value: **$75,000**

Verify the bond's coupon, maturity, security master and current dealer or simulator quote.

Calculate face amount from the target market value using the correct clean price, accrued interest and dirty settlement price.

### C. Treasury bond 912810FG8

CUSIP: **912810FG8**

Target purchase market value: **$75,000**

Apply the same security verification, pricing, accrued-interest and settlement rules.

**Total fixed-income target: $300,000.**

Treasury securities are actual investments and may have duration risk. Do not misclassify the full allocation as unrestricted cash or count pledged securities as free collateral.

Do not substitute other Treasury CUSIPs without authorization.

## 5. Add healthcare and defense longs — $30,000

### TMO — Thermo Fisher Scientific

Action: BUY

Target market value: **$15,000**

Calculate whole shares from the executable quote.

### NOC — Northrop Grumman

Action: BUY

Target market value: **$15,000**

Calculate whole shares from the executable quote.

Both are standalone long-equity investments.

Use appropriate limit prices and verify sufficient buying power.

## 6. Add four speculative options strategies — $25,000 premium budget

**These are directional profit-seeking option trades, not portfolio insurance.**

Use valid, current listed-option chains.

### Trade 1 — XHB bearish puts

Underlying: XHB — SPDR S&P Homebuilders ETF

Action: BUY TO OPEN puts

Maximum aggregate premium debit: **$8,000**

Intended horizon: Approximately three to six months.

Thesis: Housing affordability pressure, homebuilder margin deterioration and weaker construction demand.

### Trade 2 — GLD bullish calls

Underlying: GLD — SPDR Gold Shares

Action: BUY TO OPEN calls

Maximum aggregate premium debit: **$7,000**

Intended horizon: Approximately four to eight months.

Thesis: Gold appreciation driven by monetary uncertainty and inflation-related demand.

### Trade 3 — USO bearish puts

Underlying: USO — United States Oil Fund

Action: BUY TO OPEN puts

Maximum aggregate premium debit: **$5,000**

Intended horizon: Approximately one to four months.

Thesis: Near-term reversal in elevated crude-oil prices.

Account for the difference between USO ETF returns and spot crude-oil returns.

### Trade 4 — IBIT bearish puts

Underlying: IBIT — iShares Bitcoin Trust

Action: BUY TO OPEN puts

Maximum aggregate premium debit: **$5,000**

Intended horizon: Approximately three to six months.

Thesis: Bitcoin-price decline and contraction in speculative risk appetite.

### Option selection requirements

For each strategy:

1. Retrieve a current, valid option chain.
2. Select an expiration aligned with the intended horizon.
3. Compare at-the-money and moderately out-of-the-money strikes.
4. Calculate executable bid/ask, implied volatility, delta, gamma, theta and vega.
5. Prefer reasonable liquidity and bid/ask spreads.
6. Size contracts so total premium plus fees never exceeds the individual budget.
7. Use BUY TO OPEN limit orders; never invent premiums or execute at arbitrary prices.
8. Report break-even, maximum loss and price-target scenarios.
9. If no sufficiently liquid, correctly priced contract exists, leave the strategy BLOCKED rather than fabricate a fill.

Do not create additional short option legs for these four strategies.

The maximum combined new option premium debit is **$25,000**, excluding any fees unless already reserved within the budget. Prefer staying below the limit after fees.

## 7. Execution and order handling

The 12 existing orders were submitted after the October 28 regular session, with the next session identified as October 29.

Before creating new orders:

- Inspect the actual simulator clock and exchange session calendar.
- Reload order statuses.
- Check current cash, buying power, margin and reserved collateral.
- Reconcile existing pending orders with potential executions.
- Inspect duplicate-order identifiers and package relationships.

For new eligible orders:

- Create unique order IDs.
- Submit into Pending Trades.
- Execute only at valid simulated exchange sessions with supported quotes.
- Preserve partial fills and remaining quantities.
- Transfer completed executions into Completed Trades.
- Revalue positions and account equity.
- Recalculate buying power after each execution.
- Stop submitting further orders if the required liquidity buffer is breached.

Market orders from the existing blotter must remain unchanged unless separately authorized.

Do not guarantee fills at tomorrow's opening price.

## 8. Required risk controls

The personal book is bearish several correlated technology companies simultaneously.

Aggregate the economic exposures from:

- ORCL physical short.
- NVDA physical short and collar.
- AAPL physical short and long calls.
- TSLA synthetic short.
- Any technology beta from long equities and options.

Explicitly calculate the potential losses from the synthetic TSLA position and the NVDA written puts.

Do not mistakenly classify option sale proceeds as unrestricted profits.

Model securities borrowing, short-sale collateral, margin interest, assignment, option expiration, early exercise where applicable, and settlement obligations.

Stress:

- ORCL +30%.
- NVDA +30%.
- AAPL +20%.
- TSLA +40%.
- Nasdaq +15%.
- Bitcoin +40%.
- Gold −15%.
- Oil +25%.
- Housing stocks +20%.
- Simultaneous unfavorable technology rally.

Report P&L, equity, buying power and margin-call risk.

A $1 million opening capital limit does not mean the maximum possible loss is $1 million. Do not suppress modeled liabilities or losses beyond the available funds.

## 9. Book separation and persistence

Maintain separate identifiers and ledgers for:

- Shaffer Personal Fund — $1 million.
- Existing $25 million paper fund.
- Shaffer Institutional Fund — proposed $1 billion.

Do not share positions, capital contributions, collateral, trade identifiers or realized P&L across these accounts.

Make all new order submission idempotent.

On restart, the simulator must recover order states, package relationships, securities, margin reservations and cash ledgers without duplication.

## 10. Final delivery

After processing the order batch, give me:

1. All 12 existing order tickets and their current statuses.
2. Every newly submitted order.
3. Filled quantity and execution price for each order.
4. Pending and rejected orders with explanations.
5. Exact Treasury face amounts and settlement prices.
6. Chosen option contracts, expirations, strikes, premiums and Greeks.
7. Actual initial margin and collateral requirements.
8. Unrestricted cash and total buying power.
9. Gross and net equity exposure.
10. Realized and unrealized P&L.
11. Total fund NAV and leverage.
12. The exact amount of initial capital committed and remaining available.
13. Results of the market stress tests.

**Do not finish with a theoretical portfolio or a list of recommendations. Inspect FinSim1, preserve the existing orders, submit the eligible new trades, and deliver the actual order book and accounting reconciliation.**

If any trade fails funding, instrument availability, market-data or margin checks, leave it unexecuted and tell me exactly why.

The portfolio manager authorizes valid simulated order submission for the additional positions above, subject to the stated capital and margin constraints.
