# FinSim repair directive (owner, 2026-10-20) and the owner's amendments

Recorded by Claude (session 43815ab3) at 2026-10-20 12:41 EDT (clock). The directive below is the owner's text, verbatim.
The AMENDMENTS that follow it come from the owner's later messages the same day. **Where they conflict with the
directive, the amendments win.**

## Amendments (newest first)

-10. **Securities-lending collateral at 102%.** 2026-10-20 shortly before 13:34 ET (clock), owner: "sec lending has marked
   collateral calls to remain at 102% whether it goes up or down so either i could get collateral back or need to wire
   more", then "the above should reduce recalls".
   - Stock-borrow collateral is marked to market every day, both ways, to stay at 102% of the borrowed shares' current
     value:
     - the stock rises: a collateral call, and the owner posts more (the Treasury sweep pays it automatically, as an
       internal transfer);
     - the stock falls: the excess collateral comes back to the owner.
   - Each mark is one ledger movement per borrow per day. Collateral posted stays the owner's asset (receivable from
     the lender).
   - Recall model: keeping the borrow fully collateralised at 102% REDUCES recalls. Recalls should come only from
     lender-side reasons (the lender sells or needs the shares, hard-to-borrow tightening, corporate events such as
     votes or record dates), never from under-collateralisation while the 102% marks are met. A recall still forces a
     buy-in if not covered.
-9. **Rules and regulations.** 2026-10-20 shortly before 13:31 ET (clock), owner: "Risk should also know laws / regulations
   and inform if anything is being broken".
   - Add a compliance layer to Risk: a catalogue of the rules that apply to a US trader / fund, each with a threshold, the
     data it needs and a plain-English message.
   - It runs PRE-TRADE (ticket preview) and continuously on holdings.
   - It only INFORMS (amendment -8), naming the rule and what a real broker or regulator would require.
   - Candidates; the plan verifies each against the CURRENT rule text and cites it:
     - Reg SHO: locate (203(b)), close-out, threshold securities, Rule 201 short-sale price test after a 10% drop;
     - Reg T / FINRA 4210 margin: initial and maintenance; cash-account free-riding and good-faith violations;
     - the pattern day trader rule;
     - beneficial ownership 5% (Schedule 13D/13G) and 10% (Section 16, short-swing profits);
     - the large trader rule 13h-1;
     - Rule 13f-2 / Form SHO short-position reporting;
     - 13F once 13F securities reach USD 100M;
     - futures position limits (CFTC) and exchange accountability levels;
     - listed option position limits;
     - self-trading / wash trades between the owner's own accounts (buying in one and selling in another);
     - the tax wash-sale rule (IRS 1091) and constructive sales (1259), as information;
     - EU/UK short-selling disclosure for foreign names (0.1% / 0.5%).
   - Data needed: shares outstanding (SEC), the Reg SHO threshold lists (Nasdaq Trader, already captured), live price
     moves for Rule 201, and static limit tables.
-8. **Risk only warns.** 2026-10-20 shortly before 13:30 ET (clock), owner: "also, risk is just a warning … doesn’t
   actually have the ability to stop trades".
   - Risk limits and checks (VaR, concentration, exposure, leverage, Greeks, the old career/mission limits) only WARN.
     They warn in the ticket preview, on Daily News ("Requires your attention") and on the Risk page. They never block
     or cancel a trade.
   - Not risk limits, so still enforced (they are mechanics, not judgments):
     - a trade is refused when it cannot be paid for after the sweeps and any switched-on PB financing;
     - a short is refused without a borrow (directive section 9);
     - a lender's recall still forces a buy-in.
   - A PB margin shortfall is met by the Treasury sweep. If that cannot cover it, there is a loud warning, but no
     automatic liquidation.
-7. **Automatic cash sweeps.** 2026-10-20 shortly before 13:21 ET (clock), owner: "setup sweeps from treasury and accounts
   so it automatically grabs treasury money to pay for other accounts trades".
   - **Sweep in.** When a trade, fee, margin call or settlement in an account needs more cash than the account holds,
     exactly the shortfall moves from Treasury automatically, as one internal transfer (never P&L), linked to the trade
     and visible in Transactions.
     - Order of funding: the account's own settled cash, then the Treasury sweep, then (prime-broker accounts only) the
       PB's margin financing if the owner has it switched on, else the trade is refused with the reason.
   - **Sweep out** (Claude's default under the owner's standing "yes on everything you recommend"): at the end of each
     day, idle settled cash above what the account needs (margin, collateral, pending settlements, plus a small buffer)
     returns to Treasury. There is a switch per account in Settings.
   - Settled vs pending cash is respected. Foreign-currency needs are met from Treasury's cash in that currency, or by
     an FX conversion that is shown in the ticket preview before confirming.
-6. **Trading happens inside the account.** 2026-10-20 shortly before 13:19 ET (clock), owner: "yes picking account per
   trade is fine but essentially each account will be the left anyways with its own accounting and markets within it so
   it’ll be based on that".
   - Each account page has its own markets and its own accounting. A ticket opened on an account page trades in THAT
     account. Where an instrument is also available elsewhere (e.g. a US stock at a prime broker), the account is
     simply the page you trade from.
   - Accepted under the owner's standing "yes on everything you recommend":
     - accounts replace books (an optional strategy tag can group trades later);
     - added funds land in one central cash pot (Treasury) and move to accounts by quick internal transfers, never P&L.
-5. **One lifetime; navigation by account.** 2026-10-20 shortly before 13:19 ET (clock), owner: "yeah i can essentially
   “open up” prime broker accounts, so they’re not automatically just open. so on the side we can just make finsim a truly
   one lifetime (where i can add funds), and then on the left like the options there’s US Exchange so everything i can
   trade on US exchanges and the accounting for what i have, Debt Instruments, Credit Instruments, Foreign Markets & FX
   (this will include foreign bonds too), then Prime Broker Accounts that can be opened and added to the left. all make
   sense?"
   - **ONE lifetime.** A single persistent paper account (no multiple worlds/saves). It starts at USD 10M; funds can be
     added any time as capital deposits.
   - **The left sidebar is organised by account.** Each account page holds what can be traded there AND the
     accounting for what is held there (positions, P&L, transactions, cash):
     - US Exchange (renamed from the ETD / Exchange Traded account): everything tradeable on US exchanges - stocks,
       ETFs, listed options, futures;
     - Debt Instruments: bonds;
     - Credit Instruments: borrowing and lending - cash loans, repo, securities lending, private credit;
     - Foreign Markets & FX: non-US securities, FX, AND foreign bonds;
     - Prime Broker accounts: NOT open by default. The owner opens them (Goldman, JPMorgan, Morgan Stanley, Deutsche
       Bank ...); each opened PB appears in the sidebar.
   - This reshapes directive section 3 (module set). The account pages absorb Market Place / Derivatives / Credit.
     Daily News, Portfolio (all accounts), Risk, consolidated Accounting, Settings and Glossary remain - Claude's
     reading, put to the owner.
-4. **Prime brokers and accounts.** 2026-10-20 shortly before 13:14 ET (clock), owner: "and also, the prime brokers would
   be another thing to add such as Goldman, JPM, MS, Deutsche, etc. where i can have a relationship with them and be
   charged fees to enter in OTC or just market contracts with them where they deliver the shares or received the shares
   or do Swaps/Options/FXs/etc with them. essentially, these would be other “Accounts”. Like i’ll probably have an ETD
   Account, Credit Instrument, Debit Instrument, Foreign Markets & FX, and Account per Prime Broker".
   - **Add named prime brokers / dealers:** Goldman Sachs, JPMorgan, Morgan Stanley, Deutsche Bank, etc. The existing
     counterparties.py / prime.py are the starting point; the Desk imports DEALERS and quote_half_width, so keep
     those working.
   - Each is a relationship: onboarding, a fee schedule, financing and borrow terms.
   - The owner can trade OTC (swaps, options, FX, forwards) or execute market trades through a PB. The PB delivers or
     receives the shares (DVP settlement). Fees are charged per the PB fee schedule and posted to the ledger.
   - **Accounts structure** (inside a book; each account holds cash per currency and positions; moves between accounts
     are internal transfers):
     - an ETD account. Owner, shortly before 13:14 ET (clock): "By ETD, I mean Exchange Traded Accounts". It holds
       everything traded on an exchange: US stocks, ETFs, listed options, futures.
     - a Credit Instruments account;
     - a Debt Instruments account;
     - a Foreign Markets & FX account;
     - one account per prime broker.
   - Correction from the owner, shortly before 13:14 ET (clock): "Debt Instruments**". "Debit Instrument" was a typo; the
     account is Debt Instruments.
   - Claude's working interpretation, put to the owner:
     - Debt Instruments = bonds (Treasuries, corporates);
     - Credit Instruments = what the Credit page holds (cash borrowing, repo, securities lending, private credit).
-3. **Clean-up sweep.** 2026-10-20 shortly before 13:09 ET (clock), owner: "like there’s a lot of crap in here that can be
   cleaned up, like adding the books or crap takes a long time and can be ugly. like a lot can be cleaned up, please just
   scan it for inconvinences / ugly things .. such as there should be a better drop down per book in each page rather
   than it being at the top. this is just one example, find others and clean them up".
   - Sweep the whole UI for inconveniences and ugly spots, and FIX them. The owner does not need to approve each one;
     list them in the final report.
   - Named examples:
     - the book picker becomes a dropdown on each page where it matters, not the global one at the top;
     - adding a book must be quick and clean (name + amount, done).
   - Keep the visual style. Faster and fewer clicks is the goal.
-2. **Look and feel.** 2026-10-20 shortly before 13:07 ET (clock), owner: "i don’t need to see the source on the terminal
   of it, i want it to feel like a real paper trading terminal with my tweaks of having the hedges available pop up for a
   ticker’s ticker just to quicken up the speed for me."
   - **Do NOT display price sources or delay labels** in the trading UI: not on Portfolio, tickets, Market Place or
     Derivatives. This overrides directive 3.2 "mark and its source" and amendment -1 "every mark shows its source".
   - Sources and price times are still RECORDED in the data and ledger, for integrity and audit, but not shown.
   - It should feel like a real paper-trading terminal: fast, clean, live quotes.
   - **The hedge section (directive section 8) pops up automatically on the ticket** as soon as a ticker is entered,
     with ready-to-pick hedge candidates. Speed is the point.
-1. **Marks and accuracy.** 2026-10-20 shortly before 13:06 ET (clock), owner: "i want my positions to always reflect current
   marks, not this “update” thing daily. also, bonds should be real priced and same with options, or atleast somewhat
   accurate. by “live prices” i just mean however we can get it accurate enough such as the yahoo delayed. doesn’t
   need to be perfect here".
   - **Every position is revalued continuously from the latest available quote** while FinSim runs, and on every page
     load. There is no daily "update" step for marks.
   - Settlement, accruals and expiries still happen on their real dates, through a background lifecycle job, not a
     button.
   - **Options get real quotes:** delayed exchange quotes (bid/ask/last, about 15 min), e.g. Yahoo or the Cboe
     delayed-quotes JSON the warehouse already captures. Fall back to a model priced off the live underlying and a
     real implied volatility, labelled "computed".
   - **Bonds are priced from real data:**
     - Treasuries from real yields (Treasury.gov daily curve, Yahoo ^IRX/^FVX/^TNX/^TYX intraday) and, where
       available, real end-of-day prices (e.g. TreasuryDirect FedInvest);
     - corporates from the real Treasury curve plus a real spread proxy (e.g. ICE BofA OAS indices on FRED),
       labelled "computed".
   - **"Accurate enough" is the bar:** Yahoo-delayed is fine. Every mark shows its source, time and delay. A price is
     marked stale when it is older than its stated delay.
1. **Starting cash.** 2026-10-20 between 12:35 and 12:41 ET, owner: "10m with ability to add more".
   - The new live paper account starts with USD 10,000,000.
   - The owner can add more at any time. Each addition is a capital flow (a deposit) posted once in the ledger, never
     P&L.
2. **No simulation.** 2026-10-20 shortly after 12:27 ET, owner: "there's no need to the 'advance day' thing ... this should all be
   current day live prices. no simulation, real but fake cash obv".
   - The day-advance model and the simulated, model-driven market go away.
   - FinSim becomes paper trading at today's real (live) prices with pretend cash. This overrides, wherever they
     conflict:
     - the directive's "keep ... the day-advance model";
     - "a model-driven market that advances one day at a time";
     - section 5's next-session execution against a simulated session bar;
     - section 6's day-advance routine;
     - rule 5, "Do not add external data feeds, accounts or network calls. The market stays model-driven".
   - **Price data rules:** only free sources (the owner's $0-data rule); never Stooq; never bypass anti-bot walls; a
     browser User-Agent only for Yahoo Finance.
   - **Every price shows its source and delay.**
   - **Classes with no free live price** are model-priced from live inputs and labelled "computed". These are OTC
     options, swaps, private credit and most corporate bonds.
   - **Order execution:**
     - market orders fill at the live ask (buy) or bid (sell) during market hours;
     - outside market hours they wait for the open;
     - limit and stop orders fill when the live price reaches them, checked while FinSim runs.
   - **Accruals** (interest, borrow fees, coupons) run by real calendar day.
   - **The old simulated worlds** are kept as read-only archives, never deleted. Trading happens in a fresh live paper
     account; saving and switching between accounts stays.
   - Claude proposed these as defaults shortly before 12:32 ET. The owner did not object, and they may still change them.
3. **Who does the work.** 2026-10-20 12:17 ET (clock), owner: "now i'm thinking you can quickly edit it for me since you
   found it". Claude (this session) does the repair, not Fable.

0. **Game parts and live defaults.** 2026-10-20 shortly before 12:42 ET, owner: "Also just yes on everything you recommend" (recorded 12:42 ET, clock).
   - **DROP the game parts** with the simulation: careers / career progression, investment banking, private equity, clients,
     institutions, investors, playbook and similar.
   - Remove them from the UI and the day-advance machinery, and archive their code if it is easy, rather than leaving dead
     paths.
   - The Portfolio "Career" tab goes.
   - **The live-price defaults in amendment 2 are APPROVED.**

## The directive (verbatim)

FinSim: repair, simplify and return to daily paper trading

What this is

FinSim is my single-process paper-trading simulation: a model-driven market that advances one day at a time, with books, a ledger, trade instructions that execute at the next session, listed and OTC derivatives, financing and a risk view. It is buggy and has grown too many pages. Your job is to repair it, cut it down to the module set below, make every number reconcile to the ledger, and hand it back for daily use. It is a repair and consolidation, not a rebuild: keep the visual style, the day-advance model, the save/load "worlds", and the existing engine wherever it is correct.

How to work

	1.	Read the whole codebase before changing anything. Write a one-page map of it first (modules, the day-advance routine, world save/load, the ledger and cash accounts, Treasury, where each P&L figure is computed) and keep it in docs/MAP.md.
	2.	Work in small commits. Every bug fix and every calculation change carries a regression test that fails on the old code. Every expected value in a test is worked by hand from the inputs with the arithmetic in a comment; never copy the app's own output into a test.
	3.	Run the full test suite before every commit. Never leave the app in a state that does not start.
	4.	Keep CHANGELOG.md current: one line per change, in plain English.
	5.	Do not add external data feeds, accounts or network calls. The market stays model-driven.
	6.	When something is a convention rather than a fact (day count, how a mark is chosen), state the convention on screen in one sentence, not only in code.
	7.	Finish with the report in section 12.

1. The two known bugs, first

(a) Trades disappear from a world after a couple of days or over a weekend.
Reproduce before fixing: place one of each (market buy, GTC limit, DAY limit, option package, futures contract, short sale with its borrow, FX spot, FX forward, swap RFQ, private-credit commitment), save the world, advance one day, advance across a weekend, advance across a month end; reload the save at every step; diff the complete set of orders, fills, positions and ledger entries against the step before. Suspects, in order: the non-trading-day skip rebuilding state from a snapshot taken before recent trades; a save written while the advance is mid-way (partial world); DAY/GTC expiry logic applied to filled orders or to positions; anything keyed by calendar date colliding when two sessions share a date; a "rebuild world" path that re-runs seeding; autosave racing a manual save. Fix the root cause, keep the reproduction as a permanent test.

(b) About 10,000,000 appears in Treasury unexplained.
Reproduce the same way, hashing the ledger and every cash balance after each advance and each load; find the first step where cash changes without a matching, single ledger entry. Suspects: opening capital or seed funding applied again on load or on advance; a settlement posted twice; repo or loan maturity returning principal twice; variation margin posted daily and then settled again at expiry; a reverse repo's collateral cash counted as the book's own. Fix the root cause, keep the test.

2. Ledger integrity, enforced

Add one integrity check (integrity()), run after every day advance, every save and every load, and tested directly:

	•	every ledger event balances (debits equal credits);
	•	each account's cash equals its opening cash plus the sum of its entries;
	•	assets minus liabilities equals NAV for each book and consolidated;
	•	NAV today minus NAV yesterday equals today's P&L plus capital flows, nothing else;
	•	every position's market value equals quantity x mark x multiplier (contract size for futures and options, par factor for bonds, notional for swaps);
	•	no fill, settlement, coupon, dividend, interest accrual, margin movement, fee or financing charge exists twice for the same instrument, account and date;
	•	Treasury equals capital plus retained results minus what it has advanced to books (section 7);
	•	a short position has a live borrow behind it; a borrow has either a short or an explicit reason.
A failure is shown on Daily News under "Requires your attention" with the item that failed. The app never silently continues.

3. The module set (remove everything else)

Sidebar, in this order: Daily News, Portfolio, Accounting, Market Place, Derivatives, Credit, Risk, Settings, Glossary. Remove Open Positions, Live Trades, Groupings, Options, Futures, OTC Derivatives and Calculations as pages; fold what they do into the modules below. Any navigation link, glossary entry or Daily News link that pointed at a removed page points at its new home.

3.1 Daily News

Keep as is, but every tile and the Major P&L table read from the same functions as Accounting (section 4); no figure may be computed here. Add the integrity status and the working-instructions count (what Live Trades showed) to "Requires your attention".

3.2 Portfolio

One page, every open position across all books, with P&L. Columns: book, account, instrument, class, quantity (long and short as signed quantity, with gross long and gross short totals in the footer), average cost, mark and its source (model close, dealer quote, computed), market value, today's P&L, unrealised, realised to date, income to date (dividends, coupons, interest), financing to date (borrow fees, repo cost, PB cost, margin interest), weight of NAV, days held. Group by class (securities, futures, listed options, OTC derivatives, FX, financing, private credit) with a class subtotal. Filters: book, class, text. Click a row to open the position's detail drawer: fills, settlement, cash flows, mark history, linked hedge (section 8), linked borrow (section 9). Keep the Performance tab (section 11 proofreads it) and the Career tab if it is used; drop My Desk unless it holds something not shown elsewhere. Copy and CSV export stay.

3.3 Accounting

One page with a book selector (each book, plus "All books" consolidated) and these tabs:

	•	P&L: the Major P&L categories for today, month to date, year to date and since inception; by book and consolidated. One row per category, same list as Daily News.
	•	Balance sheet: assets (cash by currency, securities at market, futures variation receivable, options at market, OTC PV, collateral posted, private credit at market, accrued income), liabilities (short securities at market, repo, PB loan, collateral received, accrued expenses, options written), NAV; per book and consolidated with internal transfers eliminated.
	•	Positions: every open position of the selected book with full detail (what Portfolio shows, plus cost basis lots, settlement state, mark history).
	•	Transactions: every ledger entry of the selected book (date, type, instrument, quantity, price, gross, fees, net, account, counterparty, linked order or event), filterable, CSV export. Trades, settlements, expiries, assignments, resets, coupons, dividends, borrow fees, margin, repo, loans, FX, capital flows, internal transfers: all of them, nothing summarised away.
	•	Cash and financing: cash by currency and account, repo balances and cost, PB loan and cost, securities borrow and fees, collateral posted and received with haircuts, Treasury (section 7).
Every number on this page is produced by one function in one module (accounting.js or equivalent); Daily News, Portfolio and Risk call the same functions. Delete every duplicate computation.

3.4 Market Place

Tabs: Equities & ETFs (stocks, ADRs, REITs, preferreds, ETFs), Bonds & Rates (Treasuries, corporates, the curve), FX, Commodities (spot). Keep the scenario/regime controls and the market summary tiles. The ticket on the right is the standard ticket (section 8) with the hedge section. Nothing from the derivatives families is listed here.

3.5 Derivatives

One page, tabs: Listed options (chains by underlying and expiry, with the chain table, Greeks, implied vol, and a package builder for spreads, collars, straddles, condors, calendars, synthetics), Futures (what the Futures page shows today), OTC options (dealer RFQ, FX options, equity options, exotic terms where supported), Forwards (FX and equity forwards), Swaps (IRS, TRS, cross-currency; the RFQ flow with dealer quotes). Each tab has its own ticket; all tickets share one preview and one confirmation path (section 8). The 38 strategy templates from Calculations become the package builder inside Listed options (and TRS inside Swaps); the Calculations page itself is removed.

3.6 Credit (renamed from Security & Credit Lending)

Tabs: Cash borrowing (new: borrow USD and other currencies: lender, currency, principal, rate type fixed or floating off the policy rate plus spread, day count, term, interest schedule, collateral required; draw, accrue daily, pay interest, repay part or all; the balance is a liability of the borrowing book and interest is "Financing" in P&L), Securities lending (borrow stock to short, lend stock from inventory; rates, rebates, recalls, hard-to-borrow list), Repo, Collateral & prime (collateral posted and received, haircuts, calls, PB loan, excess liquidity), Private credit (as today). A borrowed currency is settled cash at once: the same day it can be spot-converted (section 9 test) and the FX settles on its own T+2 while the loan accrues from drawdown.

3.7 Risk

Keep, but clean: one page with exposure by class, by book and consolidated; delta/vega/theta/gamma by underlying with signs per position; DV01 and curve exposure; FX exposure by currency; a scenario table (equities ±5/10/20%, rates ±25/50/100 bp, vol ±5 points, FX ±5%) revaluing the actual positions through the same pricing functions the marks use; concentration (top positions as % NAV); financing and liquidity (excess liquidity, encumbrance, margin headroom, borrow recall risk). Remove anything that is decorative or that duplicates Accounting. Every figure must tie to the positions on Portfolio.

3.8 Settings and Glossary

Keep. The glossary gains entries for anything new (Credit terms, hedge ticket, Treasury definition).

4. One source for every figure

Create (or consolidate into) a single accounting module that exposes: pnl(book, period), balanceSheet(book), positions(book), transactions(book, filter), cash(book), treasury(), nav(book). Daily News, Portfolio, Accounting and Risk read only from these. Grep the codebase for every place a P&L, NAV or market value is computed and remove the duplicates. Add a test that renders each page's numbers and compares them to the module's output for the same world.

5. Orders and execution (what Live Trades did, now inside the tickets and Portfolio)

Keep the instruction model: instructions execute at the next session against that session's open, high, low, close and volume. Market orders cross the spread and pay impact; limit orders fill only if the session range reaches the limit; stop and trailing stop as today; orders above 20% of session volume fill partially and keep working (GTC) or expire (DAY); conditions evaluated at the close. Working instructions appear in a strip on the page where they were placed and in Portfolio's "Working" section; cancel from either. Cancelled and expired orders stay in Transactions, never deleted. Test each rule with a hand-built session bar. Option packages are all-or-none as today; futures and options fill at the session's settlement or chain price per the stated rule.

6. Settlement and the calendar

State the settlement convention per class (equities T+1, bonds T+1, FX T+2, options T+1, futures daily variation, OTC per confirmation) and apply it; cash is "settled" versus "pending" and only settled cash is spendable except where a rule says otherwise. The day-advance routine must skip weekends and the holiday list in Settings without losing or re-running anything (section 1a). Accruals (bond, loan, repo, borrow, private credit, cash interest) run per calendar day including weekends; fills, settlements and expiries only on business days. Month-end and quarter-end events (coupons, private-credit payments, swap resets) fire exactly once.

7. Treasury

Write the definition in docs/MAP.md and the glossary: Treasury is the book-level balance of own funds: opening capital plus retained results plus capital flows, less what has been advanced to trading accounts, plus financing drawn at book level (repo, PB loan) and collateral posted. Then make the Cash and financing tab and Daily News follow it: opening capital posts once; advances to accounts are internal transfers that net to zero at book level; collateral posted stays an asset; "excess liquidity", "available" and "Treasury encumbrance" each have one formula shown on hover and in the glossary. Anything on the old Treasury view not derived from the ledger goes.

8. The ticket, with the hedge built in

One ticket component used everywhere, with a preview (every leg with its price, source, quantity, cash, fees, margin or collateral, settlement date, borrow terms; totals from one price snapshot) and one confirmation. Below the main leg, a Hedge section:

	•	For a long equity or ETF: autofilled candidates from the live chain and the dealer desk: protective put (strike about 7% below, nearest expiry at or after 3 months), collar (that put plus a call about 7% above), put spread, and an equity TRS or short futures overlay sized to the notional. Each shows cost, protection level, and the net package cash; the user picks one or none, can edit strike, expiry and ratio, and the package previews and confirms together (all-or-none where the structure needs it, otherwise sequential with the hedge dependent on the primary fill).
	•	For a short equity: protective call, call spread, collar; and the borrow (section 9) above the hedge.
	•	For a bond: Treasury futures or swap overlay (DV01-matched) and an option on the future where listed.
	•	For FX spot: a forward or an FX option.
	•	For futures: an option on the future.
	•	The hedge candidates are sized from the primary quantity and the contract multiplier, and resized if the primary partly fills.
	•	The 38 templates become selectable packages inside this section and inside the Derivatives package builder; sizing, strikes relative to spot and tenor logic carry over from Calculations.
	•	Everything that Calculations previewed (every leg off today's quotes, option margin, dealer fair spread) stays in the preview.

9. Short selling and borrowing, in order

A short sale ticket must, before the user can approve, go to Securities lending: locate the stock, show availability, the borrow rate, rebate, collateral required (cash at the stated percentage of market value, marked daily), hard-to-borrow status and recall risk. The borrow is a leg of the package: it executes first; the short sells only if the borrow filled; if the borrow fails the short is rejected with the reason. Short proceeds are restricted (not spendable) until the position is covered. Cover buys back and then returns the borrow; a recall forces a buy-in as today. Test: short with available borrow; short with no borrow (rejected); short partly borrowed (short scaled); recall; dividend paid in lieu while short; borrow fee accrual across a weekend.

Borrowed currency and spot FX (bug test required): borrow 1,000,000 EUR in Credit, the same day convert it to USD at spot: the EUR loan balance is a liability from the drawdown date and accrues from that date; the EUR cash is settled at once; the FX converts at the quoted rate and settles T+2 with pending EUR payable and USD receivable until then; after settlement the USD is settled cash; repaying the loan in EUR later requires buying EUR (another spot). Assert every balance at each step by hand (loan, EUR cash settled and pending, USD cash settled and pending, accrued interest, P&L FX effect as the rate moves). Also test: borrow USD and buy stock the same day; repay with insufficient cash (refused with the reason); floating rate reset on the policy-rate change; early repayment with accrued interest.

10. Persistence

Save writes the whole world atomically (temp file, then rename); load validates the file and refuses a partial or mismatched version with a clear message rather than loading a subset; autosave on advance cannot race a manual save (one writer, queued). Test: save, reload, advance, save, reload, compare everything; and a corrupted file is refused.

11. Calculations to proofread, each with a hand-worked test

Bond accrued interest and dirty price on the real day count, coupon on schedule, par redemption; repo interest and haircut (proceeds = collateral value less haircut), repo roll; securities borrow fee on market value and the rebate on cash collateral; cash interest on balances at the policy rate; option P&L, Greeks with signs per position (shorts negative), expiry (ITM exercise into shares or cash, OTM lapse, assignment of written options), package margin; futures initial margin posted and released, variation margin daily, expiry at final settlement; FX forward mark under covered interest parity and settlement at maturity; swap accrual, resets and payments; TRS financing leg and return leg; private credit floating coupon (policy rate plus spread), OID accretion, amendment fees, quarterly payment, mark at price; dividends and dividends in lieu; the Portfolio performance statistics (daily returns annualised by root 252, volatility, Sharpe and Sortino against the policy rate, beta and alpha from a daily regression on the benchmark's closes, max drawdown, hit rate, monthly table NAV to NAV with flows excluded). Where the app is wrong, fix the code; where it is a convention, state it on screen.

12. Finish

Deliver: a report listing each bug with root cause and fix; every calculation corrected (before and after figures from the tests); what the integrity check guards; the pages removed and where their content went; the test count; and anything found but not changed, with the reason. Then the app goes back into daily use.
