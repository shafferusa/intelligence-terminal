# Owner instruction 2026-10-28 (~17:40 ET), verbatim: FinSim1 Goldman Sachs FX trades (implement and auto-book)

# CLAUDE CODE — FINSIM1 GOLDMAN SACHS FX TRADES: IMPLEMENT AND AUTO-BOOK

You are working on my existing **FinSim1 institutional paper-trading system**, not FinSim2, the newspaper app, or the intelligence terminal.

Repository: `shafferusa/intelligence-terminal`  
Local project: `C:\Users\logan\intelligence-terminal`

I am the portfolio manager. Implement, execute, and reconcile two simulated OTC currency contracts with my existing Goldman Sachs prime brokerage account.

**This is an explicit instruction to auto-book both contracts once the simulator can produce valid dealer quotes, cash flows, and margin calculations. Do not merely create draft tickets. Do not book fabricated prices or bypass accounting controls.**

## 1. Trade A — EUR/USD Cross-Currency Swap

- Counterparty: Goldman Sachs (PB)
- Instrument: Cross-currency swap
- Pay currency: EUR
- Receive currency: USD
- Pay notional: **EUR 2,000,000**
- Receive notional: Calculate using the executable deal FX rate
- Tenor: **24 months**
- Pay leg: EUR floating, €STR-linked where supported
- Receive leg: USD floating, compounded SOFR where supported
- Pay and receive rates: Use the dealer pricing engine
- Cross-currency basis spread: Use the dealer pricing engine
- Day-count conventions: Use correct conventions for the actual benchmark legs
- Notional reset: **OFF**
- Counterparty collateral: Existing Goldman Sachs CSA
- Principal exchange: Model initial and final exchanges with exact settlement instructions
- Trade date: Actual simulator booking date, not a historical backdate
- Status: **Executed only after successful booking**

My economic objective is **bearish EUR / bullish USD**.

Important: A currency swap's pay/receive coupon labels alone do not guarantee the intended FX exposure. Verify the principal-exchange direction, use of initial proceeds, and the aggregate currency balances. If the EUR received at inception must be converted into USD to establish a genuine short-EUR position, book that conversion with its own audited cash legs. Do not create an unaccounted FX position.

## 2. Trade B — EUR/GBP FX Forward

- Counterparty: Goldman Sachs (PB)
- Instrument: Deliverable FX forward
- Currency pair: **EUR/GBP**
- Direction: **SELL EUR / BUY GBP**
- Sell notional: **EUR 2,000,000**
- Buy notional: Determine from dealer forward quote
- Tenor: **24 months**
- Forward rate: Calculate from appropriate market data, discount curves, forward points and dealer spread
- Settlement: Deliver EUR, receive GBP
- Trade date: Actual simulator booking date
- Status: **Executed only after successful booking**

This is a short-EUR/long-GBP trade. A decline in EUR/GBP should benefit its currency-price exposure, all else equal.

Do not reverse the EUR/GBP quotation or incorrectly create a GBP/EUR short.

## 3. Auto-booking requirements

1. Inspect FinSim1's current OTC instruments, execution services, Goldman counterparty setup, cash ledgers, FX quote engine, valuation engine and margin engine.
2. Fix or implement missing FX forward and CCS support inside FinSim1 as needed. Preserve existing contracts and balances.
3. Generate defensible simulated Goldman dealer quotes from available market inputs. Store the market data timestamp, curves, spot, forward points, basis and pricing methodology.
4. Validate currency availability, settlement arrangements, notional limits, collateral requirements and sufficient available funds.
5. Book both contracts automatically if valid. If either cannot be priced or financed, leave it unexecuted with the precise blocking reason. Never silently insert arbitrary spreads or rates.
6. Create separate unique contract IDs and completed-trade records for each execution.
7. Book initial principal exchanges and any required spot conversion for the CCS without double-counting cash or NAV.
8. Set up quarterly or contract-appropriate interest accruals, payment schedules, daily valuation, forward settlement, financing, collateral, initial margin and variation margin.
9. Translate GBP/EUR exposures and P&L correctly into USD for portfolio NAV.
10. Ensure the booking is **idempotent**: rerunning the command must not create duplicate contracts or cash transfers.
11. Preserve audit trails for quotation, booking, transfers, settlements, valuation and margin.
12. Keep any pending or failed orders in Pending Trades. Successfully executed contracts must appear in Completed Trades, Goldman Sachs accounts and the portfolio.
13. Do not change any existing Oracle, META, MU, USO, CDS, IRS, equity, futures, or option positions.
14. Run unit tests, integration tests, accounting reconciliation and restart-persistence checks.
15. Commit and document the completed changes according to the repository's existing workflow.

## 4. Essential exposure checks

These are **two separate contracts, each with EUR 2 million notional**, representing **EUR 4 million of gross directional exposure**, not a single EUR 2 million position.

The CCS involves EUR/USD, while the forward involves EUR/GBP. Do not claim they form a perfectly offset USD bridge or a pure EUR/GBP exposure.

Report each trade's exposure independently, then calculate combined sensitivities to:

- EUR/USD
- EUR/GBP
- GBP/USD
- EUR and USD funding rates
- Cross-currency basis
- Forward points
- Counterparty and collateral risk

Show the incremental margin and liquidity effects on my existing Goldman Sachs portfolio.

## 5. Final delivery

When implementation and execution finish, report:

- Both contract IDs and executed statuses
- Actual simulated booking timestamps
- Executed EUR/USD deal spot and CCS notionals
- CCS pay and receive rates, basis and principal exchanges
- EUR/GBP forward execution rate and GBP receivable
- Initial margin and required cash transfers
- Current mark-to-market and daily valuation methodology
- Relevant interest and settlement schedules
- Available Treasury cash and Goldman collateral after booking
- Reconciled fund NAV
- Completed Trades confirmation
- Any failed checks or unexecuted transactions

**Execute the implementation and booking workflow now. Do not stop after creating the tickets. If execution is blocked, report the issue truthfully rather than representing the contracts as filled.**
