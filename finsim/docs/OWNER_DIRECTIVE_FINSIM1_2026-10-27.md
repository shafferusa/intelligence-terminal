# FINSIM1 - COMPLETE THE INSTITUTIONAL PAPER-TRADING TERMINAL (owner directive, 2026-10-27, verbatim)

OWNER DECISION (2026-10-27 15:04 EDT, verbatim: "Use Goldman terms"): the OTC terms of his TRS / CDX / IRS follow the app's Goldman Sachs schedule, not his workbook's assumptions: TRS financing SOFR - 0.60 % (not SOFR - 1.00 %), initial margin per the GS schedule (TRS 10 %, CDX IG 2 % / HY 6 %, IRS DV01 x 50 bp, about 1,405,615.50 in total; not 15 % / 5 % / 2 %), and no 5,000,000 T-bill reserve. This confirms plan defaults Q2, Q3 and Q5 as the owner's answers.

> **OWNER CLARIFICATION, 2026-10-27 (later the same morning; it SUPERSEDES sections 5, 7, 9, 10, 11, 12 and 17 wherever they conflict),
> verbatim:** "No, book the TRS, CDX, and Rate Swap with Goldman for Value Date 10/21. The model is because you didn't build them in time to
> incorporate them, but they need to be in my portfolio as if they were there since oct 21. Options are exchange, so just backdate them.
> Do not just model them, actually have them in my portfolio."
>
> Meaning for the build:
> - TRS ORCL (short, 38,700 ref shares, initial price 129.13), TRS META (short, 7,200, 697.52), CDX.NA.IG 5Y buy protection 3,000,000 at
>   64 bp, CDX.NA.HY 5Y buy protection 4,000,000 at 352 bp, and the USD SOFR IRS pay fixed 4.72 % / receive compounded SOFR, 7,500,000,
>   3 years, are REAL OTC contracts booked with Goldman Sachs (F1:PB_GS) with trade / value / effective date 2026-10-21 - not modeled or
>   hypothetical entries. They carry their full lifecycle from 2026-10-21: initial margin, every day's variation margin, financing, dividend
>   equivalents, coupon / premium accruals, resets and fixings caught up day by day to today from recorded or historical closes, exactly as if
>   they had been booked on 2026-10-21.
> - The options (ORCL Nov 20 140C long 387 at 3.55, META Nov 20 660C long 72 at 23.59, IBIT Nov 20 46C long 105 at 2.10, QQQ Nov 20 700P long
>   150 at 17.64 and 675P short 150 at 10.25, the QQQ legs grouped as a put spread) are REAL exchange executions, backdated (the next regular
>   options session after the owner's instruction: 2026-10-22, unless the code shows they belong on 2026-10-21) with premiums, fees and
>   settlement as for any fill.
> - They are the owner's real positions: no "modeled" or "hypothetical" label. The audit trail stays truthful the way a real desk records a
>   late booking: an AS-OF trade (value / trade date 2026-10-21 or 22, entered 2026-10-27, booked by the operator at the owner's instruction).
> - The equities, PDBC, the five short sales and GCZ26 / ZNZ26 already exist as real simulated executions: preserve them untouched; report any
>   difference from the workbook, never alter them.

YOUR ROLE
You are the lead software engineer and institutional trading-systems architect responsible for completing FinSim1, my existing paper-trading application.
I am the Portfolio Manager.
Your responsibility is to finish the existing application, reconcile my historical trades, and make FinSim1 a fully functioning institutional trading simulator.
FinSim1 is NOT FinSim2. FinSim1 is a trading, execution, settlement, custody, prime-brokerage, and accounting simulator. FinSim2 is a separate quantitative research application.
Do not modify FinSim2. Do not build a news application. Do not add market research, investment recommendations, newspapers, or AI analysis to FinSim1. Remove the existing news and daily briefing functionality from FinSim1.
My objective is to simulate how an institutional hedge fund operates, including the complete lifecycle of transactions across multiple financial instruments.

## 1. START BY REVIEWING THE EXISTING APPLICATION
Repository: shafferusa/intelligence-terminal. Local directory: C:\Users\logan\intelligence-terminal. Relevant development branch: claude/finsim-live-terminal. Also inspect: claude/finance-simulation-game-y0nkv0.
The existing FinSim1 application is located under: finsim/. FinSim2 is located under: finsim2/.
First actions - before changing code: 1. Inspect the current local Git branch and working tree. 2. Review recent commits. 3. Compare the existing FinSim1 implementation against the latest development branch. 4. Identify uncommitted changes. 5. Inspect the existing database. 6. Review the transaction ledger. 7. Review existing positions and accounts. 8. Review the trade-execution engine. 9. Review the order-management system. 10. Review settlement, custody, and collateral workflows. 11. Run the existing test suite.
Preserve all working functionality. Do not overwrite or reset existing data. Create a recoverable backup before making database changes.
Existing functionality to preserve - the application already has or previously implemented: Institutional accounts; Treasury cash management; Equity trading; Bonds and credit instruments; Listed options; Futures; FX; OTC derivatives; Prime-broker accounts; Custody; Trade settlement; Corporate actions; Collateral; Margin; Double-entry accounting; Transaction history; Market data; Portfolio valuation; NAV calculations; Automated end-of-day processing.
Verify each component's actual implementation. Do not assume historical test results prove that the current code works.

## 2. REMOVE NEWS FROM FINSIM1
The current FinSim1 README describes a daily briefing system and real-market headlines. I no longer want this functionality.
Remove the following from FinSim1 only: News pages; News navigation; Daily news briefings; Market commentary; Automated newspaper generation; News-related background jobs; News-related API endpoints that are no longer needed; Unused news dependencies.
Keep market-data functionality. Market data is necessary for trading and valuation. News is not.
Do not modify the separate intelligence-terminal website or FinSim2 news features. Do not delete unrelated user data.

## 3. TWO TRADE BLOTTERS - ESSENTIAL FEATURE
I want two clearly separated trade blotters within FinSim1.

A. PENDING TRADES - orders that have been submitted but have not been fully executed. Examples: Pending market orders; Resting limit orders; Stop orders; Stop-limit orders; Trailing-stop orders; Orders scheduled for the next market session; Orders awaiting a simulated dealer quote; Orders awaiting user acceptance; Partially filled orders with remaining quantity.
Each pending order must show: Order ID; Instrument; Asset class (Equity, futures, options, bond, FX, OTC); Side (Buy, sell, short, cover, pay, receive); Order type (Market, limit, stop, etc.); Quantity (original requested); Filled quantity; Remaining quantity; Limit/stop price (where applicable); Current market price (latest available quote); Order status (Pending, working, partially filled, etc.); Submitted time; Expiration (DAY, GTC, GTD, etc.); Account; Counterparty (where applicable).
Pending-order actions: Submit; Modify; Cancel; Replace; Review; Execute when conditions are met; View order history.
The application must simulate the appropriate execution rules. A limit order should not execute at an unfavorable price merely because the user submitted it. A market order should execute according to the selected simulation's trading-session and quote rules. A partially filled order must show both the executed quantity and the outstanding remainder. Pending orders do not create positions or change NAV until execution occurs. Reserved buying power or margin may change, but that must be accounted for separately.

B. COMPLETED TRADES - actual simulated executions. A completed trade is an executed transaction, not necessarily a settled transaction.
Each execution must show: Execution ID; Order ID (parent order); Instrument; Side; Executed quantity; Execution price; Execution time; Trade date; Settlement date (expected or actual); Account; Counterparty (where applicable); Commission/fees; Status (Executed, settling, settled, failed); Source (Simulated fill or historical modeled entry).
Completed-trade functionality: View trade details; Filter by date; Filter by instrument; Filter by account; Filter by asset class; Filter by counterparty; View settlement status; View cash movements; View associated position; View transaction accounting; Export completed trades; Audit historical changes.
A single order may produce multiple executions. The completed blotter must preserve those individual fills. Do not merge multiple fills into a fictional execution.
Order lifecycle: Created -> Submitted -> Working -> Partially Filled -> Filled. Alternative paths: Canceled; Rejected; Expired. Following execution: Executed -> Pending Settlement -> Settled. Or: Executed -> Settlement Failed -> Resolved. OTC instruments may require additional negotiation, confirmation, and collateral states. The user should be able to follow the transaction through its entire lifecycle.

## 4. PORTFOLIO POSITIONS - SEPARATE FROM TRADE BLOTTERS
The trade blotters show orders and executions. The portfolio page shows the resulting positions. These must be separate views.
The portfolio should display: Current open positions; Long and short exposure; Quantity; Average entry price; Current market price; Market value; Unrealized P&L; Realized P&L; Daily P&L; Total P&L; Asset class; Account; Counterparty; Margin requirements; Collateral requirements.
The user must be able to click a position and view all transactions that created or modified it. Closed positions should remain available in historical records. Do not confuse a position with a trade.

## 5. HISTORICAL TRADE RECONCILIATION
My current paper portfolio was reconstructed from historical trades beginning around October 21, 2026. The latest audited reference file is: FinSim_Final_Audited_Blotter_2026-10-23.xlsx. Use that workbook if available.
Before importing positions, compare the intended portfolio with existing application transactions. For every intended trade: 1. Search for an existing order. 2. Search for an existing execution. 3. Search for existing position records. 4. Compare instrument and quantity. 5. Compare trade date. 6. Compare execution price. 7. Determine whether the transaction is already represented. 8. Add only genuinely missing transactions. 9. Preserve original records. 10. Recalculate accounting.
Never import duplicates. Historical modeled transactions should be clearly labeled. Do not represent an invented historical fill as an independently confirmed execution.
For the purpose of my paper fund, a backdated modeled transaction may be treated as executed within the simulation, provided its modeled status remains visible. These transactions should appear in the Completed Trades blotter, not Pending Trades, because the modeled scenario assumes they occurred historically. Unexecuted trade ideas belong in Pending Trades only when I actually submit an order.

## 6. EQUITY AND ETF PORTFOLIO TO RECONCILE
These are the intended holdings from the October 23 audited reconstruction. The October 26 marks are projected reference values and should be replaced with verified historical prices where available.

| Symbol | Direction | Quantity | Entry price | Oct 23 mark | Oct 26 projected mark |
|---|---|---:|---:|---:|---:|
| CRWD | Long | 5,000 | 242.60 | 257.57 | 261.83 |
| ZS | Long | 4,750 | 233.00 | 255.15 | 260.45 |
| PANW | Long | 3,450 | 356.58 | 380.55 | 390.46 |
| SNOW | Long | 4,350 | 318.89 | 325.43 | 334.95 |
| NOW | Long | 10,500 | 154.25 | 158.96 | 158.18 |
| WMT | Long | 8,500 | 119.44 | 119.89 | 120.29 |
| PG | Long | 4,790 | 162.14 | 161.02 | 159.92 |
| NKE | Long | 20,000 | 37.05 | 37.30 | 37.91 |
| SPCX | Long | 7,850 | 154.21 | 147.49 | 146.90 |
| LMT | Long | 2,700 | 579.51 | 604.18 | 635.20 |
| NOC | Long | 2,300 | 544.70 | 551.44 | 565.41 |
| KTOS | Long | 19,000 | 38.27 | 38.68 | 40.43 |
| GXO | Long | 26,000 | 48.09 | 48.43 | 49.31 |
| JNJ | Long | 7,750 | 260.54 | 263.81 | 266.30 |
| TMO | Long | 2,150 | 691.70 | 673.82 | 684.89 |
| STT | Short | 8,800 | 166.64 | 167.83 | 169.94 |
| C | Short | 12,000 | 122.20 | 120.62 | 121.04 |
| DLR | Short | 8,500 | 174.80 | 172.37 | 174.53 |
| VMRK | Short | 24,210 | 62.08 | 63.03 | 62.78 |
| IBIT | Short | 10,500 | 46.28 | 45.96 | 45.22 |
| PDBC | Long | 50,050 | 20.14 | 20.34 | 20.15 |

Validate SPCX and VMRK against the instrument master. If either is synthetic or custom, preserve its intended simulated instrument identity. Do not substitute another security.
For these holdings, calculate: Current market value; Unrealized P&L; Daily P&L; Cumulative P&L; Sector exposure; Account allocation.

## 7. TOTAL RETURN SWAPS
Two historical modeled short TRS positions:

| Reference | Direction | Reference shares | Entry price | Oct 23 mark | Oct 26 projected mark |
|---|---|---:|---:|---:|---:|
| ORCL | Short TRS | 38,700 | 129.13 | 126.16 | 126.06 |
| META | Short TRS | 7,200 | 697.52 | 642.62 | 630.56 |

Reference date: October 21, 2026. Initial reference notionals: ORCL $4,997,331; META $5,022,144.
These are OTC derivative contracts, not physical short sales. Support: Contract creation; Dealer counterparty; Reference notional; Financing leg; Dividend-equivalent payments; Daily valuation; Variation margin; Initial margin; Collateral transfers; Reset dates; Maturity; Termination.
The transaction blotter should display the executed OTC contract. The portfolio page should display the current derivative position. The cash ledger should display financing and margin movements. Do not count the TRS reference notional as an asset or liability equal to its full face amount.

## 8. FUTURES
Historical intended futures positions:

| Instrument | Direction | Contracts | Entry | Oct 23 mark | Oct 26 reference |
|---|---|---:|---:|---:|---|
| GCZ26 | Long | 5 | 4,140.98 | 4,010.20 | 4,038.40 indicative |
| ZNZ26 | Short | 19 | 105.3141 | 106.0938 | Retrieve settlement |

Multipliers: GCZ26 100 ounces; ZNZ26 $1,000 per full price point. The original FinSim execution log reportedly contains these futures transactions. Check before importing.
Support: Futures order tickets; Futures execution; Initial margin; Maintenance margin; Daily variation margin; Settlement; Expiration; Position closing. Variation margin must reconcile to the cash ledger.

## 9. CREDIT DEFAULT SWAPS
Modeled positions:

| Instrument | Direction | Notional | Entry spread | Oct 23 spread |
|---|---|---:|---:|---:|
| CDX.NA.IG 5Y | Buy protection | $3,000,000 | 64 bp | 63 bp |
| CDX.NA.HY 5Y | Buy protection | $4,000,000 | 352 bp | 356 bp |

Historical modeled effective date: October 21, 2026.
Support: OTC trade tickets; Dealer quotes; Contract confirmation; Credit-index selection; Notional; Protection direction; Coupon; Upfront payment; Premium accrual; Daily valuation; Credit events; Collateral; Maturity.
Use proper modeled CDS valuation rather than multiplying spread changes by notional alone.

## 10. INTEREST-RATE SWAP
Modeled position: USD SOFR IRS, 3-year maturity. Direction: Pay fixed, receive floating. Notional: $7,500,000. Modeled fixed rate: 4.72%. October 23 indicative market rate: 4.548%. Historical modeled effective date: October 21, 2026.
Support: Fixed/floating legs; Payment schedules; SOFR resets; Accrued interest; Discounting; Daily mark-to-market; Collateral; Termination.
The IRS must appear in the completed-trades blotter and current portfolio.

## 11. OPTIONS
Historical modeled positions (all expirations November 20, 2026; standard multiplier 100):

| Instrument | Side | Contracts | Entry premium | Oct 23 mark |
|---|---|---:|---:|---:|
| ORCL Nov 20 $140 call | Long | 387 | $3.55 | $3.6026 |
| META Nov 20 $660 call | Long | 72 | $23.59 | $20.2762 |
| IBIT Nov 20 $46 call | Long | 105 | $2.10 | $2.0095 |
| QQQ Nov 20 $700 put | Long | 150 | Retrieve | Retrieve |
| QQQ Nov 20 $675 put | Short | 150 | Retrieve | Retrieve |

Retrieve the missing QQQ option premiums from the audited workbook. Do not invent them.
Support: Options order entry; Multi-leg orders; Individual execution records; Premium payments; Greeks; American-style exercise; Assignment; Expiration; Margin; Daily valuation.
The QQQ positions form a put spread. Preserve each option leg separately while allowing the application to group them as a strategy.

## 12. CASH AND COLLATERAL
Historical modeled balances include: $5 million Treasury-bill reserve target; $144,903.50 operating cash estimate; $440,133 modeled option premiums paid; $277,320 modeled CDS upfront cash received; -$80,204.30 modeled cumulative futures variation margin.
These figures are reconciliation references. They are not guaranteed current balances. The application must calculate actual simulated balances from ledger entries.
Support: Available cash; Restricted cash; Treasury securities; Securities collateral; Cash collateral; Margin; Short-sale proceeds; Financing charges; Interest; Variation margin; Settlement receivables; Settlement payables.
Do not double-count cash flows. Do not treat collateral transfers as trading profits or losses.

## 13. NAV AND PERFORMANCE
Starting paper-fund capital: $25,000,000. October 23 reconstructed NAV benchmark: $25,770,700.86. October 26 previous estimated NAV references: approximately $26.05 million under a rough modeled scenario; approximately $26.16 million under an incomplete position-price roll-forward. These are not accounting targets. Independently calculate NAV from the actual simulated ledger.
The application should display: Starting capital; Current NAV; Daily P&L; Daily return; Cumulative P&L; Cumulative return; Realized P&L; Unrealized P&L; Financing costs; Margin; Cash; Collateral; Gross exposure; Net exposure.
Do not invent balancing transactions to force NAV to a predetermined number.

## 14. ACCOUNT STRUCTURE
Preserve the existing FinSim1 institutional accounts: Central Treasury; US Exchange; Debt Instruments; Credit Instruments; Foreign & FX. Preserve the existing Goldman Sachs prime-broker relationship. Support other prime brokers through the existing onboarding workflow. Do not automatically create new relationships.
The application should support: Internal transfers; Prime-broker transfers; Collateral movements; Securities lending; Securities borrowing; Margin calls; Cash sweeps; Settlement; Custody; Interest accrual. All account activity must reconcile to the consolidated fund balance sheet.

## 15. MARKET DATA AND EXECUTION
Keep the existing real-market simulation functionality. The application should use market data to simulate execution and update positions.
Support: Live or delayed quotes; Market orders; Limit orders; Stop orders; Stop-limit orders; Trailing stops; Day orders; GTC orders; Extended-hours behavior where supported; Next-session instructions; Partial fills; Rejected orders; Canceled orders.
Clearly label simulated fills. Do not claim orders were executed at a real exchange.
For historical pricing: Use the latest available closing price; Record the valuation timestamp; Record the pricing source; Preserve historical marks; Identify stale quotes; Use modeled marks where necessary. For OTC derivatives, distinguish indicative model valuations from dealer-confirmed prices.

## 16. USER INTERFACE
Preserve the existing terminal design unless functionality requires changes. I want a professional institutional trading interface.
Main navigation - the core navigation should include: Dashboard (Fund NAV, P&L, exposures, cash, margin, and account summary); Portfolio (all current open positions); Trading (order entry for supported instruments); Pending Trades (working, queued, partially filled, and otherwise pending orders); Completed Trades (all executions, including historical modeled executions, with settlement status); Accounts (Treasury, exchange, credit, debt, FX, and prime-broker accounts); Cash & Collateral (cash movements, collateral balances, margin, and funding); Derivatives (OTC contracts, options, futures, and related servicing); History (historical portfolio NAV, transactions, and account activity); Settings (simulation settings, market-data preferences, and account configuration).
These are functional requirements, not instructions to duplicate existing pages unnecessarily. Reuse existing screens where possible. No News page. No Newspaper page. No AI Research page.

## 17. HISTORICAL IMPORT REQUIREMENTS
Create a safe historical-import procedure. Each imported transaction should contain: Unique transaction ID; Source; Instrument; Quantity; Side; Trade date; Execution timestamp; Entry price; Settlement date; Account; Counterparty; Historical-model flag; Import batch ID.
The import must be idempotent. Running it twice must not duplicate transactions. If a trade already exists, preserve the original execution and report the match. If the existing record differs from the intended portfolio, report the discrepancy before making an irreversible correction.
Historical modeled executions should appear in Completed Trades. They should also affect historical positions, cash, and NAV. Do not merely insert positions without corresponding transaction and accounting records.

## 18. REQUIRED TESTING
Run the existing test suite and add tests for: 1. Pending market orders. 2. Pending limit orders. 3. Stop orders. 4. Partial fills. 5. Canceled orders. 6. Rejected orders. 7. Completed-trade history. 8. Settlement progression. 9. Failed settlement. 10. Historical imports. 11. Duplicate-import prevention. 12. Equity long accounting. 13. Physical short accounting. 14. Futures variation margin. 15. Options premiums. 16. TRS financing. 17. CDS valuation. 18. IRS valuation. 19. Cash transfers. 20. Collateral movements. 21. Prime-broker balances. 22. Historical NAV reconstruction. 23. Current NAV calculation. 24. Database persistence. 25. Application startup.
Verify the actual interface in a browser. Do not claim completion based only on backend tests.

## 19. WINDOWS STARTUP AND DEPLOYMENT
The previous startup attempts failed when running: python -m finsim from the repository root. Inspect the package structure and fix the startup workflow. The README currently documents running the module from the inner FinSim directory. Verify the correct commands. A preferred workflow is:
cd C:\Users\logan\intelligence-terminal
cd finsim
python -m finsim serve
Also verify whether the existing installer and desktop shortcut work. The user should be able to launch FinSim1 reliably without manual troubleshooting. Do not modify the FinSim2 startup system.

## 20. DELIVERY REQUIREMENTS
When finished, provide: Application status (what was already complete and what required repair); Code changes (what you implemented); Historical reconciliation (every imported, preserved, corrected, or unresolved transaction); Pending-trades blotter (demonstrate working orders and order management); Completed-trades blotter (demonstrate historical and new executions); Portfolio (show all open positions with current marks and P&L); Accounting (show the reconciled cash, collateral, derivatives, and NAV); Testing (provide actual test results); Deployment (provide the working Windows startup command and Git commit SHA); Remaining limitations (identify any functionality that is not actually complete). Do not report modeled valuations as independently confirmed dealer marks.

## FINAL INSTRUCTION
Finish FinSim1 as an institutional paper-trading and portfolio-accounting simulator.
My main priorities are: 1. Preserve the existing working application. 2. Remove news and daily briefings from FinSim1. 3. Implement a complete Pending Trades blotter. 4. Implement a complete Completed Trades blotter. 5. Backdate missing historical portfolio transactions. 6. Reconcile the entire $25 million paper portfolio. 7. Finish trade execution and settlement workflows. 8. Finish cash, collateral, margin, and derivatives accounting. 9. Calculate accurate live NAV and P&L. 10. Test, commit, and deploy the working application.
Do not redesign FinSim1 into FinSim2. Do not build a research application. Do not build a newspaper. Do not stop after writing a development plan. Inspect the code, implement the missing functionality, test the complete system, and deliver a working FinSim1 terminal.
