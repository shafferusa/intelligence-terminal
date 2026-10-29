# Owner directive 2026-10-28 (~17:57 ET), verbatim: Shaffer Institutional Book V3 (the owner's message: "actually:"). SUPERSEDES V2 (docs/OWNER_INSTITUTIONAL_V2_2026-10-28.md).

> OWNER DECISIONS (2026-10-28 ~18:30 ET, answers to the orchestrator's questions): (1) AVB was delisted 2026-08-17 (merged into Equity Residential, now Vivmark Residential, VMRK): "Use VMRK instead" - short VMRK for the same dollar amounts in both AVB lines (the $65M short TRS and the $12M physical short). (2) DTCC public swap-trade data (pddata.dtcc.com): "Yes, if the terms allow" - use it to price rate swaps, CDX, iTraxx and FX forwards only after the terms of use are checked and allow it.

> DTCC TERMS CHECKED (orchestrator, 2026-10-28 18:35 ET): DTCC's Terms of Use (www.dtcc.com/terms, last updated 2026-03-11) grant only a personal, revocable, limited license and PROHIBIT using any automated device, program, tool, algorithm or bot to mine or systematically scrape or extract data from the DTCC site, and compiling the Content into a database; bulk delivery is a separate paid DTCC service. FinSim's use (scheduled downloads into its database) is therefore NOT allowed, so per the owner's "Yes, if the terms allow" DTCC public dissemination data is NOT used. Rate swaps stay on free sources (labelled), iTraxx stays blocked without a free source.

> OWNER DECISIONS (2026-10-28 ~19:40 ET, answers to the V3 plan's questions): (Q1) SPCX: "Trade as listed" - the $15M purchase and the $75M receive TRS reference the observed Nasdaq listing (EDGAR CIK 1181412), labelled as exchange prices. (Q5) QQQ bearish put debit spreads: "Gross across legs" - the $30M is the sum of both legs' absolute delta (about 770 spreads). (Q3) CDX basis for book I: "Copy the Fund's basis" (IG -24.5 bp, HY +23.0 bp), labelled ASSUMED. (Q6 / Q11) Blocked exposure: "Redirect to CDX + ITB puts" - the iTraxx $50M goes to CDX buy protection (orchestrator default: CDX.NA.IG, the closest analogue of the investment-grade iTraxx Main; owner may say HY), and the housing put remainder (XHB's ~$10.64M of delta) goes into ITB (iShares U.S. Home Construction) puts where the chain is liquid; the DLR put remainder (~$1.67M, data centres, not housing) stays blocked and is re-checked after the 10-29 earnings. Every other V3 question takes the plan's default.


> ORCHESTRATOR NOTES (2026-10-28 20:08 ET, from the finished single-name CDS and FX / cross-currency plans; binding for the V3 revise and the V3 tooling): (a) ORCL single-name CDS ($25M, release 1.0.7): book it ONLY at the owner's own negotiated mid, per docs/CDS_SINGLE_NAME_PLAN.md 9.5 step 7 and its dealing-level guard - never 'per the model at booking'. The model gives ORCL about 113-116 bp against the 295-315 bp reported in public news for 10-19, so a model-level booking would be off-market; the orchestrator asks the owner for his level when 1.0.7 is deployed. No DTCC observation is quoted or used anywhere. (b) FX forwards (REVISED 20:52 ET with the plan's revision 2, V3-C-13): the JPY lines use the legacy PB path - one overnight / policy rate per currency, the OECD monthly proxy, labelled LIMITED - unless the owner holds them (plan Q8); TONA comes with 1.0.6.2. Before any V3 FX line books, check the JPY rate's source and age against 1.0.6's rate-age limit (a monthly OECD average at most 62 days past the end of its month) and the pricing path; if either fails, the line stays UNEXECUTED with the exact reason. Of the plan's 6.1 requests only R-D (the TRS stale-listing guard) goes into 1.0.6.1; the SOFR curve is package V-R of 1.0.8.

# CLAUDE CODE — SHAFFER INSTITUTIONAL BOOK V3
## $1 BILLION CAPITAL | $3 BILLION GROSS EXPOSURE | AGGRESSIVE DIRECTIONAL DERIVATIVES

You are implementing the Shaffer Institutional Book inside **FinSim1**, the institutional paper-trading simulator.

Repository: `shafferusa/intelligence-terminal`  
Local location: `C:\Users\logan\intelligence-terminal`

## NONNEGOTIABLE PORTFOLIO MANAGER INSTRUCTION

**NO PROTECTIVE OPTIONS. OPTIONS ARE AN INDEPENDENT SPECULATIVE PROFIT CENTER.**

Do not buy ORCL calls to protect short ORCL swaps. Do not buy IBIT calls to protect the IBIT short. Do not allocate SPX puts merely as insurance for long stocks.

I want options that profit directly from my economic and market convictions.

Use long puts, long calls, directional debit spreads, and other transparently priced speculative structures where appropriate.

Do not confuse an option that shares the direction of another trade with a protective hedge.

The portfolio is designed to be aggressive, highly synthetic, and concentrated in selected macro and corporate theses.

## 1. Fund initialization

Fund name: Shaffer Institutional Book.

Starting capital: USD 1,000,000,000.

Target gross exposure: USD 3,000,000,000.

Target gross leverage: 3.0× NAV.

Create a separate legal/accounting fund within FinSim1. Preserve the existing $25M fund entirely, including all trades, counterparty contracts and balances.

Do not modify FinSim2 or any newspaper applications.

Replace previous unexecuted institutional order instructions with this V3 mandate. Preserve any existing actual simulated executions; do not create duplicative positions.

## 2. Final exposure budget

| Sleeve | USD exposure |
|---|---:|
| Equity total-return swaps | $1,050M |
| CDX and single-name CDS | $300M |
| Interest-rate swaps | $300M |
| U.S. Treasury cash bonds | $400M |
| Commodity futures | $250M |
| Equity index futures | $150M |
| Speculative directional options | $150M |
| Physical long equities | $120M |
| Physical short equities | $80M |
| Physical IBIT short | $100M |
| FX forwards | $100M |
| TOTAL | $3,000M |

The total is an exposure-budget target, not $3B of cash purchases.

Measure securities at market value, derivatives at the appropriate underlying notional, and options at gross absolute delta-adjusted exposure. Separately calculate economic net delta, beta, DV01, CS01, vega, gamma, and financing commitments.

Do not count offsetting positions as independent diversification without showing the common underlying risk.

## 3. Equity TRS — $1.05B

Book with Goldman Sachs, using verified simulator dealer pricing and collateral controls.

PAY TOTAL RETURN / SHORT:

- ORCL: $150M
- META: $100M
- MU: $90M
- NVDA: $75M
- AVGO: $75M
- AMZN: $50M
- DLR: $85M
- AVB: $65M
- LEN: $60M
- DHI: $60M

RECEIVE TOTAL RETURN / LONG:

- CRWD: $60M
- PANW: $45M
- LMT: $60M
- SPCX: $75M

Gross short TRS $810M; gross long TRS $240M.

Default proposed tenor is 12 months, subject to actual quote and support. Model monthly resets, financing, dividend equivalents, daily variation margin, initial margin and settlement.

SPCX remains a private/simulated security and needs a defensible bilateral valuation mechanism. Never invent an exchange-listed trade for it.

## 4. Credit derivatives — $300M

BUY PROTECTION:

- CDX.NA.IG five-year: $100M
- CDX.NA.HY five-year: $125M
- iTraxx Europe Main five-year: $50M
- Oracle single-name five-year CDS: $25M

These are active bearish credit-spread trades intended to generate profit if credit spreads widen.

Validate index series, legal reference entities, reference obligations, coupon, upfront consideration, accrued premium, recovery assumptions and credit-event settlement.

Report CS01, upfront cash, running premium, mark-to-market and counterparty margin for every trade.

Do not fabricate an executable CDS quote if reference data is missing.

## 5. Interest-rate swaps — $300M

PAY FIXED / RECEIVE SOFR:

- 2Y: $100M
- 5Y: $125M
- 10Y: $75M

These are directional rate trades. They target profits if relevant swap rates rise.

Use actual simulated curve-consistent par rates and maturity schedules. Report mark-to-market, accruals and DV01.

The Treasury bonds elsewhere in the portfolio do not automatically convert these into hedges.

## 6. Treasury bond portfolio — $400M

Purchase by market value:

- 912810FT0: $110M
- 912810FP8: $85M
- 912810FM5: $70M
- 912810FJ2: $55M
- 912810FG8: $45M
- 912810FF0: $35M

Verify instrument identifiers, coupons, maturities, dirty prices, accrued interest and settlement amounts.

Treasuries can serve as income-generating investments and eligible financing collateral, but are not designated as protection for another sleeve.

Implement Treasury repo financing only where fully supported by cash, liabilities, collateral and haircut accounting.

## 7. Physical long stocks — $120M

Buy target market values:

SPY $20M  
SPCX $15M  
CRWD $10M  
PANW $8M  
ZS $7M  
NOW $8M  
SNOW $6M  
LMT $10M  
NOC $8M  
KTOS $5M  
GXO $5M  
JNJ $5M  
TMO $5M  
PG $4M  
WMT $2M  
NKE $1M  
PDBC $1M

Total: $120M.

Use fresh executable simulated prices and respect market calendars and liquidity.

## 8. Physical short equities — $80M

Short target market values:

C $12M  
STT $10M  
DLR $12M  
AVB $12M  
LEN $10M  
DHI $10M  
PLTR $7M  
TSLA $7M

Total: $80M.

Verify borrow availability, collateral, borrow fees, proceeds segregation and settlement.

## 9. Bitcoin — $100M

Short **$100 million of IBIT** through simulated exchange short sales.

This is an outright bearish Bitcoin position.

Model borrowed shares, proceeds, collateral, financing fees, potential recalls and margin calls.

No protective IBIT calls.

## 10. Commodities — $250M

- Long COMEX gold futures: $100M.
- Short NYMEX WTI crude futures: $100M.
- Long COMEX copper futures: $25M.
- Long diversified non-oil commodity futures: $25M.

Use actual tradable contract months, multipliers, exchange margin and daily settlement.

Gold is a long-term bullish thesis; oil is a shorter-term bearish thesis.

## 11. Index futures — $150M

- Long S&P 500 futures: $65M.
- Long Russell 2000 futures: $35M.
- Short Nasdaq-100 futures: $50M.

Use real listed contract specifications and current simulated quotes.

## 12. DIRECTIONAL OPTIONS BOOK — $150M

This is the most important change from V2.

**No protective calls, no portfolio insurance, and no hedge-design instructions.**

Every position must have its own standalone investment thesis, expected profit drivers and exit criteria.

Allocate gross absolute delta-adjusted exposure as follows:

### A. ORCL bearish puts — $35M

Buy ORCL puts with expirations that give the bearish AI investment thesis sufficient time to develop.

Evaluate strikes near the money and moderately out of the money. Compare premium, break-even price, implied volatility, delta and time decay.

These are speculative bearish options intended to make money if ORCL declines.

They are NOT protection for the existing ORCL TRS.

### B. QQQ bearish put debit spreads — $30M

Buy a higher-strike QQQ put and sell a lower-strike QQQ put at the same expiration.

Select strikes and maturities based on a credible Nasdaq decline scenario.

Calculate the net premium debit, maximum profit, maximum loss and break-even.

This is a standalone bearish technology trade.

### C. IBIT bearish puts — $25M

Buy IBIT puts to generate profits from a Bitcoin decline.

Do NOT buy calls to protect the $100M IBIT short.

Analyze implied volatility and premium expense carefully because crypto-linked options can price in substantial volatility.

### D. XHB bearish puts — $20M

Buy puts on the SPDR S&P Homebuilders ETF, subject to confirming symbol, chain liquidity and execution support.

Target a housing slowdown, weaker homebuilder margins and valuation compression.

These are independent speculative housing bets.

### E. DLR bearish puts — $10M

Buy puts on Digital Realty to express the thesis that data-center economics and financing expectations deteriorate.

Include current option liquidity, earnings dates and volatility.

### F. GLD bullish calls — $15M

Buy calls to express the expectation that gold prices appreciate.

Evaluate sufficiently long expirations to match the monetary/inflation thesis.

### G. USO bearish puts — $10M

Buy puts to express the near-term thesis that oil prices retreat.

Account for the USO ETF's futures exposure and roll characteristics rather than assuming its return perfectly equals spot WTI.

### H. TLT bearish puts — $5M

Buy puts to speculate on rising longer-duration yields and declining Treasury prices.

Account for options pricing and interest-rate sensitivity.

TOTAL: $150M absolute gross delta-adjusted exposure.

## 13. Options execution rules

For each of the eight options strategies:

1. Retrieve a valid current option chain and tradable expiration.
2. Confirm underlying identifier and contract multiplier.
3. Evaluate strike and maturity alternatives.
4. Calculate bid, ask, mid, spread, delta, gamma, theta, vega and implied volatility.
5. Determine contract quantity from the target gross absolute delta-adjusted exposure.
6. Report actual initial premium outlay and maximum theoretical loss.
7. Show target-price and expiration payoff scenarios.
8. Use limit orders and realistic size-aware simulated fills.
9. Avoid fabricating options markets, stale volatility assumptions or impossible fills.
10. Record each strategy separately in the options book.

The fund may use debit spreads, but these are **directional return trades**, not insurance.

Do not automatically add protective call legs because the book contains short stocks or short TRS.

Don't transform the trading strategy into a market-neutral portfolio.

## 14. FX forwards — $100M

With Goldman Sachs:

- Sell EUR / buy USD: $50M USD-equivalent notional.
- Sell JPY / buy USD: $50M USD-equivalent notional.

Ladder over three-, six- and twelve-month maturities.

Use correct forward points, settlement dates, cash obligations and currency translation.

Remain neutral on GBP/USD in this institutional book.

## 15. Institutional accounting and margin

Create distinct accounts for Treasury, securities, short sale financing, futures clearing, FX and Goldman OTC positions.

Calculate for every position:

- Executed notional.
- Cash premium or purchase price.
- Restricted and unrestricted cash.
- Repo and securities borrowing.
- Initial and variation margin.
- Counterparty margin.
- Realized/unrealized P&L.
- Daily financing.
- Accrued interest.
- FX translation.
- Contract resets and maturities.

Do not treat $3B notional as $3B spent, and do not regard option premium received or repo financing as profit or new NAV.

## 16. Required risk analyses

The absence of protective hedges is intentional. **Risk reporting and honest margin limits are still mandatory.**

Stress:

- Oracle rallies 50%.
- Nasdaq rallies 30% and credit spreads tighten.
- Bitcoin rallies 75%.
- Housing and homebuilders rally 30%.
- Oil jumps 40%.
- Gold falls 20%.
- Interest rates fall 150 basis points.
- All bearish theses simultaneously move against the fund.
- Options lose 50% of premium through time decay or volatility compression.
- Prime-broker collateral requirements increase sharply.

Show daily P&L, fund NAV, option premium at risk, margin calls, repo haircuts, liquidation requirements and insolvency scenarios.

Do not offset losses with hypothetical protection that is not actually booked.

## 17. Submission and booking

The portfolio manager authorizes creating this independent $1B paper fund and submitting eligible simulated orders.

Inspect the existing FinSim1 repository and any prior unexecuted institutional batches.

Cancel or supersede the previous unfilled V1/V2 proposed orders. Preserve actual fills unless an explicit unwind has been authorized.

Submit the V3 trades through the supported exchange, futures, and OTC engines.

Apply executable simulated market quotes, valid trading calendars, actual contract rules, and all funding requirements.

When fills occur:
- Record the transaction in Completed Trades.
- Update the institutional Portfolio.
- Update cash and collateral.
- Recompute NAV.
- Recompute actual gross exposure and leverage.

Keep unfilled orders in Pending Trades and unsupported instruments in a clearly labeled blocked queue.

Prevent duplicate execution on restarts.

## 18. Final report

Deliver the entire institutional order blotter with all target and executed positions.

Separate:
- Target exposure.
- Submitted exposure.
- Actual filled exposure.
- Gross notional.
- Actual delta-adjusted exposure.
- Premium paid.
- Margin required.
- Unrealized and realized P&L.
- Unrestricted cash.
- NAV.
- Achieved leverage.
- Unexecuted or rejected orders.

Provide a dedicated **Directional Options P&L** table. Every option strategy needs independent P&L and performance attribution, not a hedge-effectiveness rating.

The overarching instruction is:

**Build a $3 billion gross, derivatives-heavy, high-conviction institutional paper portfolio designed to profit from the portfolio manager's stated long and short theses. Options are return-generating speculative trades. Do not insert unwanted protective hedges or neutralize the manager's directional convictions. Execute only valid simulated orders and report every actual fill and exposure truthfully.**
