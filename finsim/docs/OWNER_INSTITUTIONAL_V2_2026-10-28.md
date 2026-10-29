> SUPERSEDED at about 17:57 ET the same day by V3 (OWNER_INSTITUTIONAL_V3_2026-10-28.md, owner: "actually:"). Kept for the record; build and trade from V3 only.

# Owner directive 2026-10-28 (~17:50 ET), verbatim: Shaffer Institutional Book V2 (the owner's message: "Also, for institutional book:")

# CLAUDE CODE — SHAFFER INSTITUTIONAL BOOK V2
## $1 BILLION CAPITAL | $3 BILLION GROSS EXPOSURE | DERIVATIVES-HEAVY PORTFOLIO

You are the lead institutional trading systems engineer and portfolio implementation specialist for FinSim1.

Repository: `shafferusa/intelligence-terminal`  
Local project: `C:\Users\logan\intelligence-terminal`

Your task is to replace the previously planned institutional order batch with this revised portfolio, implement any missing simulated trading functionality, and submit all valid orders.

**Work only on FinSim1. Do not modify FinSim2, the market newspaper, or the existing $25 million paper fund.**

This is a separate simulated institutional hedge fund, not a live brokerage mandate.

## 1. Fund mandate

Fund: **Shaffer Institutional Book**

Starting contributed capital: **USD 1,000,000,000**

Target opening gross exposure: **USD 3,000,000,000**

Target gross leverage: **3.0× starting NAV**

Strategy: Aggressive global multi-asset long/short with significant OTC derivatives, structured options, leveraged fixed income, and synthetic macro exposures.

Primary theses:

- Bearish AI infrastructure financing economics and excessive technology valuations.
- Bearish residential housing, homebuilders, certain real estate REITs, and credit quality.
- Bearish Bitcoin.
- Bearish EUR and JPY versus USD.
- Bearish oil in the near term.
- Bullish gold, copper, and selected inflation-sensitive commodities over the longer term.
- Bullish cybersecurity, defense, logistics, healthcare, and selected enterprise software.
- Bearish duration through pay-fixed swaps despite holding a large cash Treasury ladder.

Do not create duplicate starting capital or share positions across funds.

## 2. Exposure schedule — exactly $3B target

| Sleeve | Target |
|---|---:|
| Equity total-return swaps | $1,050,000,000 |
| Credit default swaps / CDX | $300,000,000 |
| Interest-rate swaps | $300,000,000 |
| Cash Treasuries | $400,000,000 |
| Commodity futures | $250,000,000 |
| Equity index futures | $150,000,000 |
| Options, delta-adjusted gross | $150,000,000 |
| Long physical equities | $120,000,000 |
| Short physical equities | $80,000,000 |
| Short IBIT | $100,000,000 |
| FX forwards | $100,000,000 |
| **TOTAL** | **$3,000,000,000** |

This is a mixed-instrument exposure-budget definition. Do not confuse gross notional with risk-equivalent exposure, funding requirements or money invested.

Report additional metrics: gross equity delta, net equity delta, effective beta, DV01, CS01, gamma, vega, FX currency sensitivities, commodity exposure and worst-case collateral calls.

## 3. Equity total-return swaps — $1.05B

Use Goldman Sachs as the designated simulated OTC dealer, subject to valid counterparty capacity, margin and funding.

All contracts must use independently auditable simulated dealer pricing.

**Pay total return / short:**

- ORCL — $150M
- META — $100M
- MU — $90M
- NVDA — $75M
- AVGO — $75M
- AMZN — $50M
- DLR — $85M
- AVB — $65M
- LEN — $60M
- DHI — $60M

**Receive total return / long:**

- CRWD — $60M
- PANW — $45M
- LMT — $60M
- SPCX — $75M

Total short TRS: $810M.
Total long TRS: $240M.
Total gross TRS: $1.05B.

Propose twelve-month terms with monthly resets, unless another supported tenor is required. Show each financing leg, dividends, dealer spread, initial margin, daily variation margin, reset events and termination mechanics.

For the private SPCX instrument, use a valid documented simulated bilateral valuation framework or leave blocked. Do not invent an exchange listing or executable market price.

Never carry positions over from the $25M fund.

## 4. Credit default swaps — $300M

Book only after authentic index/reference specifications and modeled quotes are available.

- Buy CDX.NA.IG 5Y protection: $100M.
- Buy CDX.NA.HY 5Y protection: $125M.
- Buy iTraxx Europe Main 5Y protection: $50M.
- Buy Oracle single-name 5Y CDS protection: $25M.

Use the actual valid index series and versions for the proposed execution date.

Track:
- Coupon frequency.
- Standardized running premium.
- Upfront consideration.
- Accrued premium.
- Recovery assumptions.
- CS01 and risky PV01.
- Credit event mechanics.
- Settlement and collateral.
- Net counterparty exposure.

A single-name CDS must reference the correct legal entity and eligible reference obligations. If no valid quote or instrument exists, mark it blocked rather than substituting a stock or bond.

## 5. USD interest-rate swaps — $300M

Pay fixed / receive compounded SOFR:

- Two-year IRS: $100M notional.
- Five-year IRS: $125M notional.
- Ten-year IRS: $75M notional.

Use curve-consistent par swap rates, schedules, day-count conventions, collateral discounting and dealer funding assumptions.

Report swap DV01 per tenor and the combined rates exposure alongside the cash Treasury holdings.

Do not price these trades using the ten-year Treasury yield as a universal swap rate.

## 6. Treasury securities — $400M

Purchase at target MARKET VALUE, not face value:

- 912810FT0 — $110M.
- 912810FP8 — $85M.
- 912810FM5 — $70M.
- 912810FJ2 — $55M.
- 912810FG8 — $45M.
- 912810FF0 — $35M.

Verify the security master, CUSIPs, coupon schedules, maturities, clean prices, dirty prices and accrued interest.

Permit a simulated Treasury repo financing facility only if its legal and accounting mechanics exist.

If repo is used:
- Record a secured financing liability.
- Apply realistic repo haircuts and funding rates.
- Segregate pledged securities.
- Model interest accrual.
- Track collateral eligibility, margin calls, rollovers and refinancing risks.
- Never count repo proceeds as new NAV.

Do not pledge the same Treasury bonds simultaneously to incompatible counterparties.

## 7. Physical long equity book — $120M

Submit eligible simulated buy orders targeting:

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

Convert target values to shares using valid market quotes and board-lot requirements.

Use conservative market-impact simulations for large tickets, not unlimited instantaneous liquidity.

SPCX must retain its existing private-market or synthetic instrument identity.

## 8. Physical short equity book — $80M

Submit locate-verified simulated short-sale orders:

C $12M  
STT $10M  
DLR $12M  
AVB $12M  
LEN $10M  
DHI $10M  
PLTR $7M  
TSLA $7M

Total: $80M.

Model borrow locates, fees, rebate interest, restricted short-sale proceeds, recalls, dividend liabilities, financing and margin requirements.

Do not use the ambiguous VMRK label unless its security master mapping is verified. The intended apartment REIT here is AVB.

## 9. Bitcoin — $100M short IBIT

Submit a $100 million simulated physical short of IBIT.

Requirements:
- Confirm borrow availability and realistic borrow capacity.
- Account for gross share quantity.
- Reserve collateral.
- Accrue borrow fees.
- Model exchange and market gap risk.
- Apply concentration margin.
- Track recalls and buy-in scenarios.

This is a single $100M IBIT short, not $100M in IBIT plus another $100M in Bitcoin futures.

The protective IBIT calls are a separate options budget and must be shown as hedging the short exposure.

## 10. Commodities — $250M

- Long gold futures — $100M.
- Short WTI crude futures — $100M.
- Long copper futures — $25M.
- Long diversified commodity futures excluding material oil exposure — $25M.

Use real listed futures contract months, multipliers, expiry calendars and settlement conventions.

Ladder the WTI short rather than concentrate the whole exposure in one front-month contract.

Require daily variation-margin processing and explicit delivery-risk controls.

Do not create phantom contracts for instruments the simulator does not support.

## 11. Equity index futures — $150M

- Long S&P 500 futures — $65M.
- Long Russell 2000 futures — $35M.
- Short Nasdaq-100 futures — $50M.

Price individual valid listed contract months.

Keep index futures exposure separate from SPY market value and TRS reference values.

Calculate index beta and sector overlap across the entire fund.

## 12. Option overlays — $150M absolute gross delta exposure

Construct properly priced, liquid, exchange-listed option positions.

Target:
- ORCL long protective calls — $60M absolute delta-adjusted exposure.
- Housing puts on liquid relevant instruments — $30M.
- QQQ bearish put spreads — $30M gross absolute delta-adjusted exposure across legs.
- IBIT long protective calls — $20M.
- SPX protective long puts — $10M.

Strike and expiration selection must be based on verified chains, executable premiums, delta, gamma, vega, theta, liquidity and the expected time horizon of the underlying investment thesis.

Protective calls must be sized and evaluated against the corresponding short exposures. Do not assume that a long call spread provides unlimited protection above its short strike.

Do not accidentally copy the 500 short ORCL calls from the separate $25M fund.

Report:
- Premium debit/credit.
- Margin requirements.
- Expiration dates.
- Exercise and assignment exposure.
- Combined payoff with the underlying TRS or stock.
- Overnight gap risk.
- Percentage of the underlying position actually hedged.

If option markets are unavailable, preserve the desired exposure as a pending or blocked instruction; never fabricate option premiums or Greeks.

## 13. Housing short analytics

Build a dedicated HOUSING RISK page or view within FinSim1, using existing UI patterns.

Aggregate:
- DLR physical and TRS shorts.
- AVB physical and TRS shorts.
- LEN physical and TRS shorts.
- DHI physical and TRS shorts.
- Housing put options.
- Treasury-yield exposure relevant to mortgage rates.
- Any other residential or commercial real estate exposure.

Track:
- Sector net delta.
- REIT financing sensitivity.
- Homebuilder equity beta.
- Housing-starts and mortgage-rate risk factors where available.
- Scenario correlations.
- Borrow financing.
- Housing-related CDS exposure if later authorized.

CME Case-Shiller home-price futures can be researched as a potential future sleeve, but **DO NOT add extra exposure outside this $3B budget**. They require actual market liquidity and reliable clearing/settlement support.

Do not pretend a short homebuilder stock is exactly equivalent to a direct short on housing prices.

## 14. FX forwards — $100M

With Goldman Sachs:

- Sell EUR / buy USD — $50M USD equivalent.
- Buy USD / sell JPY — $50M USD equivalent.

Ladder across three-, six- and twelve-month maturities.

Use dealer forward quotes, documented rate curves, forward points, correct currency directions, settlement calendars and collateral.

No speculative GBP position in this institutional fund.

## 15. Institutional funding and account architecture

Create a distinct institutional fund entity, master book and treasury cash ledger.

All trades must have:
- Unique fund ID.
- Counterparty.
- Executing account.
- Settlement account.
- Strategy sleeve.
- Trade and settlement dates.
- Position identifiers.
- Independent cash and collateral entries.

Set up proper Goldman Sachs prime brokerage arrangements within the paper simulator.

Calculate actual modeled:
- Cash requirements.
- Borrowing capacity.
- Unencumbered Treasury collateral.
- OTC initial margin.
- OTC variation margin.
- Listed futures margin.
- Short-sale collateral.
- Option premiums and writing margin.
- Repo haircuts.
- Liquidity reserves.

Never fabricate available buying power to force the fund to exactly $3B exposure.

## 16. Simultaneous market shocks

Evaluate at minimum:

1. ORCL +50%, technology short basket +30%.
2. Bitcoin +75%.
3. Gold −20% and oil +35%.
4. Credit spreads tighten 100 bp while stock shorts rally.
5. Treasury yields fall 150 bp.
6. U.S. housing equities and REITs rally 30%.
7. EUR and JPY appreciate 15% against USD.
8. An adverse joint scenario combining all of the above.
9. Sudden volatility expansion across written and purchased options.
10. Counterparty collateral calls with reduced repo financing availability.

For each scenario, report:
- Position P&L.
- Portfolio NAV.
- Gross and net exposures.
- Initial and variation margin calls.
- Unrestricted cash.
- Collateral shortfall.
- Required liquidations.
- Whether the simulator enters a margin-default or insolvency state.

A derivative-heavy portfolio can suffer margin calls before its investment thesis has time to unfold. The simulator must represent this explicitly.

## 17. Execution workflow

1. Inspect existing FinSim1 code, current branches, ledger state and pending institutional orders.
2. Preserve the original $25M paper fund without modification.
3. Initialize the new $1B institutional fund only if it does not already exist.
4. Replace any prior unexecuted V1 institutional order batch with this V2 batch; never cancel or overwrite actual fills without a separate authorized unwind.
5. Build every target order and OTC deal ticket.
6. Obtain the latest eligible market and dealer-simulation inputs.
7. Run funding, liquidity, margin, credit and position-limit checks.
8. Submit and execute eligible simulated orders through the existing trading and OTC booking engines.
9. Apply realistic market hours, liquidity constraints, contract validity and execution timestamps.
10. Keep blocked, rejected or partially filled orders explicitly identified.
11. Persist the result, test restart behavior, and reconcile all cash and collateral accounts.
12. Prevent duplicate orders, capital contributions or simulated fills if the workflow is rerun.

The portfolio manager authorizes simulated order submission and valid simulated execution. This authorization does not permit invented market quotes, fictitious fills or bypassed margin limits.

## 18. Final report

Produce the institutional opening order book with:

- Ticker, CUSIP or contract reference.
- Long/short or pay/receive direction.
- Requested target exposure.
- Submitted quantity.
- Filled quantity.
- Execution price.
- Actual gross notional.
- Current market value.
- Realized and unrealized P&L.
- Option premiums.
- Dealer financing and margin.
- Borrowing and repo costs.
- Collateral by counterparty.
- Contract maturity.
- Filled versus pending versus blocked status.

Then deliver a fund-level reconciliation:

**$1B contributed capital → actual cash and holdings → financing and liabilities → current NAV → actual gross exposure → actual leverage.**

Report the exact gap between the $3B requested exposure and the gross exposure actually filled.

Make clear which figures are independently observable simulated exchange prices, which are model-valued OTC contracts, and which are still hypothetical proposed orders.

Do not modify the newspaper functionality or create new reporting pages outside FinSim1.

**Proceed with implementing and submitting the V2 institutional portfolio now, while enforcing every accounting, execution, margin and counterparty control.**
