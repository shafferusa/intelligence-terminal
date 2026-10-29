# FinSim Live Terminal — changelog

One plain-English line per change, compiled by the WP-15 integration from `docs/BUILD_LOG.md` (every package's line) and
the resolutions in `docs/design_proposals/BUILD_NOTES.md`.  Newest first.  Versions are `finsim.terminal.VERSION`.

## 1.0.7 (built 2026-10-29; single-name CDS on every prime broker)

**Single-name CDS**

- New on every prime broker's OTC tab: single-name credit default swaps on US-listed companies (contracts CDS-000001 ...). You buy or sell protection on one company's senior unsecured debt (bankruptcy and failure to pay; no restructuring) for 1, 2, 3, 4, 5, 7 or 10 years on the standard semi-annual roll. The coupon is 100 or 500 bp, with an upfront and the accrued rebate settling three business days later. Coupons are quarterly, variation margin is daily, and you can terminate at any time.
- Every single-name spread is a MODEL (the equity-implied CreditGrades spread plus the ICE BofA AAA OAS floor) and has not been validated against traded levels. A first deal on a name in a prime-broker account needs one of two things: your negotiated mid, which is kept as a basis to the model so the mark keeps moving with the model's inputs, or your confirmation to deal at the MODEL. Without one, the preview shows every number but cannot be booked. A name the model shows as distressed needs a negotiated mid.
- Before you save a dealing level or clear it, the dialog shows what the change does to every open contract on that name in the account, and the change is recorded.
- The dealer deals at the mid plus or minus a half-spread of 2 to 40 bp, set by the rating-like bucket of the mid and multiplied by the prime broker's OTC spread multiplier. The initial margin is 4 % to 30 % of the notional by the same tier and broker; both are assumed and editable on the desk. An accepted quote keeps its spread, coupon, maturity and tier.
- A credit event is simulated only when you enter one (bankruptcy or failure to pay, at the recovery you enter). The protection, less the accrued premium, settles the next business day and the contract closes.
- Risk shows CS01 and jump to default per contract and per company, a warning when one company's default would cost more than 10 % of NAV, spread scenarios, and share-price scenarios through the model.
- Settings > Unsupported lists what single-name CDS do not cover: dealer quotes, traded prints, reference obligations, restructuring clauses and real credit events. The CDX line no longer mentions single-name CDS.

**The desk**

- New sub-tab Single-name CDS on every prime broker's OTC tab, beside Credit (CDX). Its header says the spread is a MODEL (equity-implied CreditGrades plus the ICE BofA AAA OAS floor, not a dealer quote). It shows:
  - the FRED rating ladder with its date and the floor's age;
  - how many reference entities SEC EDGAR offers and when the list was refreshed (the first run says it is loading, about 2-3 minutes);
  - the broker's IM % and dealer half-spread for each spread tier.
- Search by ticker or company name, or browse the offered companies 50 a page, sorted by ticker, name or debt. Filters: non-financial, financial, REIT or your contracts. Each row shows:
  - the reported debt (the exact amount and its parts in the hover);
  - the balance-sheet date (amber when 6 to 15 months old);
  - the financial / REIT flag and where it comes from (filing tags until the SIC code is checked, 'known so far');
  - the 5-year MODEL spread once computed in this session;
  - why a name is not offered, in words.
- The ticket: tenor 1, 2, 3, 4, 5, 7 or 10 years with its maturity date, buy or sell protection, notional, and the coupon (100 or 500 bp). By default the coupon follows the MODEL spread's bucket, and your negotiated mid never moves it. The first preview of a name fetches its SEC filings and 1,000 daily closes and asks again by itself.
- The preview starts with one plain sentence of what the trade does. It then shows:
  - the MODEL spread as raw CreditGrades plus the AAA floor, with every input, its date and source (the model's intermediates behind 'show the model');
  - your dealing level, the negotiated basis, the mid and the dealt spread;
  - the upfront in USD and % with the price, the accrued rebate, the initial payment and its T+3 date, and the coupon schedule;
  - value at mid, cost of dealing, CS01, rates DV01, jump to default, credit exposure, IM, funding, variation margin and the assumed terms.
- No deal on the bare model. Until you set your negotiated mid (Negotiated mid ...) or confirm dealing at the MODEL (Deal at the MODEL ...) for a company in that account, the preview shows every number but Request quote stays off and says why. A distressed company needs your negotiated mid.
- Both level dialogs show, before you save:
  - every open contract on the company with its value now, its value at the new mid and the change, and the total;
  - the tier, IM % and half-spread before and after.
- Request quote asks the prime broker's simulated dealer for a firm quote (spread, coupon, maturity and tier fixed when it quotes); accept or decline it under Quote requests.
- Assumptions edits each tier's IM % and half-spread (assumed); later quotes use them, and past postings stay.


- Derivatives: a single-name CDS row shows its dealt spread against the MODEL mid now and its CS01.
  - Simulate credit event ... closes the contract: bankruptcy or failure to pay, at the recovery you enter, only when you enter it.
  - The drawer shows the company, the MODEL now with its inputs, CS01, jump to default and the credit-event record.
- Risk: a Single-name CDS panel, shown when the book holds one:
  - each contract's mid, bucket, CS01, jump to default and value;
  - per company the gross and net, jump to default and its share of NAV, CS01, your dealing level and the -30 / +30 / +50 % equity shocks;
  - spread and equity scenarios and the default of the largest jump-to-default name.
- Portfolio footer: single-name CDS credit long / short. Positions: buy protection / sell protection. Cash: CDS coupon, and a CDS- contract opens the OTC tab.
- Nothing changes for your CDX contracts or any other position.

**The spread model (MODEL, not validated)**

- Single-name CDS spreads are a MODEL, labelled as one everywhere, and not validated against traded levels. The model is the equity-implied CreditGrades spread (RiskMetrics 2002: L-bar 0.5, lambda 0.3, recovery 40 %) on the share price, the 1000-day realised volatility of Yahoo daily closes and SEC-reported debt per share, plus the ICE BofA AAA corporate OAS from FRED as a floor. A rating-like bucket comes from the FRED ladder of the same date; it is not a credit rating.
- Distressed names (model survival now under 95 %, or over 2,000 bp) are computed by the integral form of the same spread (Simpson's rule, 2,000 steps). The closed form, which overflows or cancels near the barrier, is never used there. Every refusal says why in words.
- Volatility rows and split events are stored per session, so a variation margin of a past date uses that date's inputs, also after a restart. A live price needs this session's split check; a missing input is fetched in the background and the ticket retries by itself.
- New route GET /api/otc/cds/model: a name's MODEL spread with every input, its date and source (202 while its inputs are fetched). New background jobs 'Single-name CDS inputs': the on-demand fetch queue every 5 s, and the open contracts' equities from 16:45 ET.

**Reference entities**

- Single-name CDS reference entities from SEC EDGAR (credit_entities.py). The list covers every company listed on NYSE, Nasdaq, Cboe or the Texas Stock Exchange in the SEC's company_tickers_exchange.json; OTC rows, rows with no exchange and any other exchange string are left out, and an unknown string is counted and logged. Each company has one primary ticker: the instrument master's, else the first common-stock-looking one. Its debt comes from SEC XBRL: the latest balance sheet that carries a debt tag, recipes R1-R6 with the parts rule, rejected when above total liabilities. Its share count comes from the cover page, else the balance sheet, else the diluted weighted average. Each balance sheet records the day the terminal first knew it, so a variation margin of a past date uses what was known then.
- Each name says in words whether a single-name CDS is offered, and if not, why: foreign private issuer; no debt reported (or only in the company's own tags); debt above total liabilities; no share count; a balance sheet or share count over 15 months old; debt under USD 100,000,000 (assumed); no longer listed. A balance sheet 6-15 months old is offered with its age in amber. Banks, insurers and finance companies are flagged financial (offered LIMITED), and REITs are flagged REIT; the flag comes from the filing tags until the SIC code is checked.
- The list refreshes itself (refresher cds_fundamentals). It runs in full on the first run and on Mondays: the tickers file and five quarters of XBRL frames, about 201 SEC requests and about a minute. On other days it runs after 06:30 ET with two quarters, about 80 requests, and never twice a day. A name's SIC code and newest 10-Q / 10-K are checked when it is first previewed: SEC submissions, plus companyfacts when a newer filing exists; a file over 16 MB is never read. SEC is called at 4 requests a second at most with the terminal's own user agent and shows as SEC EDGAR in Data Status.
- GET /api/otc/cds/entities searches by ticker, any of the company's tickers, CIK or name. Filters: all, non-financial, financial, REIT, my contracts ("known so far"). It sorts by ticker, name or debt, 50 a page, and returns the universe line and, per row, the 5-year MODEL spread computed this session.
- Single-name CDS in the platform views:
  - Risk: the bucket "Credit derivatives (single-name CDS notional)", outside the headline (protection bought is short credit), with the single-name CDS block and its jump-to-default concentration warnings.
  - Portfolio: the footer's single-name CDS credit long / short and the asset class "OTC CDS".
  - Labels such as "ORCL 5Y CDS buy protection".
  - Completed Trades: rows for a CDS booking (its dealt spread in bp) and a CDS credit event.
  - The words "CDS coupon".
  - The reference equity of an open CDS is watched and its close recorded.
- Reference entities: a company whose only symbols in the SEC's exchange file are preferred shares, warrants, units or rights is listed but not offered ('no common stock listed'), because the spread model needs the share price of listed common stock. The live check of 2026-10-29 had offered 9 such names on a preferred's price (CMS-PB, SCE-PG, SNUS-PH, TRTN-PA, AHL-PD, ANG-PD, CDR-PB, CFTR-PA, PHXE-P).
- Reference entities: a joint filing's figures stay with its parent. The SEC's XBRL API lists the consolidated figures of a parent-and-subsidiaries 10-Q under one co-registrant's CIK. A figure filed by another listed registrant under that registrant's own name is no longer taken as this company's, both in the daily frames and in the on-demand company-facts check. Entergy Arkansas (EAI) had shown Entergy Corp's USD 34.6B; it now says 'no debt reported'.
- SEC requests: the 4-a-second pacer now uses the high-resolution clock and spaces each request from the previous actual start. Live, 280 requests kept a smallest gap of 0.250 s and never more than 4 in any second; before the fix there were 5 inside one second 23 times on Windows' 15.6 ms clock.
- Reference entities on 2026-10-29 (live): 1,994 offered of 6,064 listed, 1,139 of them with debt of USD 1B or more. That is above the plan's 1,650-1,800 estimate and reconciled with it. The plan's own measurement gives 1,663 on the same data. 206 more are multi-class issuers whose share count is the diluted weighted average (step 3 of the plan's share chain, which that measurement did not model). 117 have their newest balance sheet in another quarter, 17 come from other rules, and 9 are left out. 47 of the 1,994 are foreign private issuers that their first preview's SEC check refuses, as designed.

**Tests**

- Verified 2026-10-29 (P8-style, plan 9.3). Suites: 1176 tests green on py-3.12 and py-3.14, on the repo tree and on a full rehearsal copy. CDX non-regression A/B on two copies of one read-only backup of the live database (base 1.0.6.1 code against 1.0.7, same FakeClock) is identical in marks, VM after the next FRED print, accruals, the 2026-12-21 coupons (7,583.33 and 50,555.56), positions, NAV and ledger, apart from the listed intended differences. Real-data walk on a copy: 1,996 reference entities offered of 6,064 listed; ORCL 5Y MODEL 114.99 bp (raw 73.99 + AAA 41.00), BBB-like; the dealing-level guard refuses the bare model in words (409 on Request quote). A test negotiated mid of 300 bp booked through RFQ at 315 bp with USD 2,184,003.20 upfront paid. Lifecycle rehearsal of nights, first VM, termination and credit event matches hand-worked lines, with integrity ok after every step. UI: 0 console errors and 0 HTTP >= 400 at 1440 and 1100.

**Known issues, fixed in the next release** (display only; no money effect; no CDS contract exists yet)

- A quote and the booking toast call your negotiated mid a 'MODEL mid'; it is your level (model + your basis).
- In the Risk single-name rows, VALUE uses the current mid while JUMP TO DEFAULT uses the last VM mark; the BUCKET shown is the model's, not the mid's.
- When a contract cannot be valued, the Risk single-name block shows 0.00 for CS01, jump-to-default and the spread scenarios instead of 'unavailable'.
- Two rows of the spread model's input report (Ford, AAL) are misleading; the app itself refuses Ford (no debt).

## 1.0.6.1 (built 2026-10-29; your Goldman FX trades: prime-broker FX forwards to 2 years and the EUR/USD cross-currency swap)

**Prime-broker FX forwards out to 2 years**

- Prime-broker forwards on USD, EUR and GBP pairs are now priced on one term curve per currency: GBP on the Bank of England UK OIS curve (observed); EUR on the ECB euro-area AAA government curve plus the prime broker's EUR OIS spread (a proxy; the spread is an assumed schedule term); USD on the Treasury par curve, as on the swap desk (a proxy). Each curve starts at its overnight print (SONIA, ESTR, SOFR).
- Tenors 1W, 2W, 1M to 1Y, 18M and 2Y, or any business day up to the 2-year date. A value date on a holiday of the pair's settlement calendars (TARGET2, London, New York) is refused and names the calendar.
- A cross's spot (EUR/GBP) is derived from its two USD pairs; its own print is kept beside it, and a difference of more than 5 pips refuses the quote.
- Every quote and booked forward keeps its full pricing record: the spot legs, each curve with its date and nodes, the discount factors, the outright and points, the basis levels, the dealer's half-width, the initial margin, the notional limits (none configured), the settlement funding and the cross-checks, each labelled observed, MODEL, PROXY or ASSUMED.
- Delivery happens at 00:30 ET on the value date, with no price input. A currency that is not there waits, unfunded; nothing is ever converted on its own. The delivery note is never empty, and the drawer shows a simulated confirmation.
- Pairs with another currency (JPY, CHF, CAD, AUD and others) keep the old method up to 1 year, labelled "LEGACY METHOD - LIMITED"; beyond 1 year they are refused.
- A price that needs a print older than 10 days, a curve older than 3 business days or a missing curve point is refused with its reason; nothing falls back to a flat rate.

**The EUR/USD cross-currency swap**

- New: a EUR/USD float / float swap. The Fund pays EUR on compounded ESTR plus the dealer's spread and receives USD on compounded SOFR, quarterly, ACT/360, with a 2-business-day payment delay. It starts on the spot date (TARGET2 and New York days) and both exchanges are at the deal rate (no reset).
- The dealer quotes its par spread on the same curves plus the schedule half-width, rounded against the Fund. The EUR/USD basis level is a disclosed, editable Goldman Sachs schedule term (0.0 bp, ASSUMED).
- An inception conversion (sell the EUR received at the start for USD at the FX desk) can be booked in the same command; it settles together with the start exchange, payment versus payment.
- Coupons are paid at the published compounded indices (the ECB ESTR index, the NY Fed SOFR Index), with a true-up of the nightly estimates.
- Every cross-currency swap is margined daily on its CSA exposure (the full value, notionals and accrued coupons included); NAV keeps the clean mark.
- A termination is cash-settled in USD at the full value with the dealer's half-width against the Fund, with no re-exchange. It is refused before the start while a conversion is linked. This also changes the fixed / fixed swap, whose termination used to need the foreign currency in cash.
- Settings > Unsupported says which floating legs are offered (EUR ESTR / USD SOFR) and which are not yet (GBP SONIA, JPY, CHF, CAD, AUD, fixed / float).

**Settlement funding**

- The settlement-funding instruction: two business days before an obligation in EUR (or another currency the FX desk deals) is due, FinSim buys that currency with USD as an audited FX desk spot deal named after the obligation. If USD is short, the purchase waits with an error; nothing moves on its own. The plan shows every obligation, purchase and the USD gap ahead (for the forward's 2028 delivery). You can switch the instruction per contract.
- Linked deals (the inception conversion, the funding purchases) settle only inside their contract's own event, never alone.

**Risk**

- The Risk page has a new "Currency risk (FX)" panel: per currency the nominal and the PV delta, the USD effect of 1 % and 5 %, the moves of each USD pair and each traded cross, rate risk per currency, the basis and EUR OIS spread sensitivities, forward points, the prime brokers' counterparty lines (initial margin, variation margin, exposure to a default), a liquidity ladder and the notional limits. It replaces the single +/-5 % USD scenario row.
- Currency exposure counts each balance once: the ledger part plus the part only the OTC contracts know.
- OTC FX forwards and cross-currency swaps leave the USD DV01 rows and the rates scenario; their rate risk is shown per currency.

**Your trades and the booking program**

- New: `python -m finsim otcplan` books a plan of OTC trades through the dealer's quote-request desk. Before it accepts a quote, an independent re-pricer re-derives every number and must match exactly. Every booking carries an owner reference, so a re-run never books twice. Its check (read-only) proves the booking against a snapshot taken before it.
- Your two Goldman trades of 2026-10-28 are the first plan: the EUR/USD cross-currency swap with its EUR to USD conversion, and the EUR/GBP 2-year forward.
- A forward check runs a copy of your book through the swap's start date and compares it with an untouched control copy before anything is booked live.
- OTC bookings and quote requests can carry an owner reference (unique); Completed Trades shows it and names what each linked FX deal serves. Pending lists expired and declined quote requests.

**Other**

- A new equity total return swap on a stock with no trade for more than 5 sessions is refused ("no trade since <date>: the security may be delisted or suspended").
- The Pending Trades order drawer shows stored internal codes in words (session closed, strategy names, fill modes).
- New data, all free: ESTR and its index (ECB), the SOFR Index (NY Fed), SONIA, its index and the UK OIS curve (Bank of England, fetched with FinSim's own honest user agent only), the ECB AAA curve; FRED as the fallback where it carries the series. A read-only self-check of these sources: `tests\terminal_rfr_selfcheck.py`.
- The swap and forward drawers' rate-risk number is the same rate01 the ticket and the Risk page show.

**Fixes from the acceptance run**

- The FX booking program accepts a dealer quote priced on FX prints up to 5 minutes old. Yahoo's free FX prints arrive about 3 minutes after their time, so the old 2-minute rule refused every real quote. A quote declined only because its prints were too old is now asked for again instead of ending the trade.
- The booking checker no longer fails its currency-exposure item when a run booked nothing. A missing input file now gives a one-line message instead of a Python error.
- A forward or cross-currency swap quote now counts its own initial margin in the settlement-funding gap it shows (the EUR purchase of 2028), so the quote shows the same gap as the booked contract.
- A total return swap preview outside market hours now fetches the underlying's quote itself, so a delisted name (AVB) is refused within seconds instead of waiting for a close that never comes.
- The cross-currency swap's confirmation shows its notionals as amounts (EUR 2,000,000.00 / USD ...). The swap drawers show the initial margin as USD 44,600.00, not 44600.00.
- FX closes: an FX pair's daily close is the 17:00 ET print, else the 1-minute bar ending at 17:00 ET, else unavailable; it is never taken from a daily bar. A new FX booking no longer back-fills ten days of closes or raises MARK_MISSING for dates nothing needs.
- Yahoo daily bars are dated by the offset in force at each bar, which fixes summer-time FX bars landing a day early.
- Forward check of the FX booking:
  - control and run copies run on one set of inputs;
  - held curves are re-dated on business days only;
  - the START is shown and checked: its net per currency against the quote (USD -40.00), its funding source, and settled cash at or above zero.
- The EUR/GBP forward now says that the dealer's half-width (2.0 bp) and the 6 % initial margin are assumed Goldman Sachs schedule terms, as the basis levels are. Its pricing record, stored when the forward is booked, ends with "ASSUMED: the basis levels, the dealer half-width and IM are Goldman Sachs schedule terms". On the ticket, the dealt-rate line says the half-width is a Goldman Sachs schedule term, ASSUMED, and the IM rule ends "(the prime broker's schedule, ASSUMED)".
- The forward ticket shows its sensitivities as money (EUR -1,882,614.77, not EUR -1882614.77). The swap ticket's "rates +1 bp" figures are shown the same way.
- Completed Trades never shows a currency pair the wrong way round:
  - the swap's inception conversion now reads "EUR/USD spot", SELL EUR / BUY USD @ 1.11578 USD per EUR (it read "USD/EUR spot ... rate");
  - the swap reads "EUR/USD cross-currency swap", PAY EUR / RECEIVE USD, 1.1158 USD per EUR;
  - the execution drawer, the CSV and Pending Trades read the same.
- The booking program's report now shows the Treasury's cash and the Goldman Sachs collateral after booking:
  - the Treasury's settled and available cash;
  - Goldman's initial margin posted against the margin required, and the variation margin posted and received.
  The program used to ask the terminal for two pages that do not exist, so both read "unavailable". Its restart check now compares this cash and collateral too, and fails when a page cannot be read instead of passing.

## 1.0.6 (built 2026-10-29)

- Your imported positions, your restated NAV history and your ledger are unchanged by this release.

**Navigation and keys**

- The F-keys follow the new sidebar:
  - top: Dashboard F12, Portfolio F7;
  - Trading: US Exchange F1, Debt Instruments F2, Credit Instruments F3, Foreign Markets & FX F4, Private Markets F5, the prime broker account F6, Watchlists Shift+F9;
  - Fund: Pending Trades F8, Completed Trades Shift+F8, Accounts Shift+F4, Treasury F10, Cash & Collateral Shift+F5, Derivatives Shift+F6, History Shift+F10, Settings Shift+F11;
  - book views: Risk F11, Accounting Shift+F7.
- Six keys changed from 1.0.5: F8 opens Pending Trades (before: the Orders of the account you were on), Shift+F4 Accounts (before: Positions), Shift+F5 Cash & Collateral (before: Cash & financing), Shift+F6 Derivatives (before: Transactions), Shift+F8 Completed Trades (before: Activity) and Shift+F10 the History hub (before: the book's last past lifetime).
- On an account page, F8 and Shift+F8 open that account's pending and completed trades, and Shift+F5 its Cash & financing.
- F12 opens the Dashboard, the landing page: the selected book's fund figures and its "Requires your attention" tiles.
- These have no default key; bind them in Settings > Keyboard: Trading (the last venue you used, else US Exchange); the Orders, Positions, Cash & financing, Transactions and Markets of the account you are on (elsewhere: US Exchange); Activity (also a tab of History); Account 1 .. 9 of the selected book; Next book. Keys you changed yourself in Settings > Keyboard are kept.

**Pending Trades and Completed Trades**

- New: Pending Trades (F8) lists every working, partly filled, held, queued (next-session) and waiting order, plus the open OTC quote requests. Filters: open / filled / cancelled / expired / rejected / all (the order history), account, class, instrument, all books. Each row shows the market from the quote cache with its freshness, the distance to the limit / stop, the expiry in words, the counterparty, the cash the order reserves and its stage. Row actions: cancel, resume, modify, replace and review ("would fill N at P now" or why not; nothing is written). The order drawer shows the timeline, the fills with the quote used and their settlement, the journal entries, sweeps, borrow, package and the original preview. Package legs stay one row each, with the package's state derived from its legs.
- New: Completed Trades (Shift+F8) lists every execution, one row each: fills, FX deals, OTC bookings / terminations / maturities and financing trades. Filters: date, instrument, account, class, counterparty, status, source and import batch, with load more and the server CSV of every column. The execution drawer has Trade, Cash movements, Accounting, Position and Audit tabs, and offers the simulated counterparty fail you trigger yourself and its resolution (retry, cash compensation, buy-in).
- New: amend a working order (price, total quantity, time in force) or replace it in one step; a short sale keeps its borrow, and waiting hedge legs follow the new order.
- New: good-till-date (GTD) and next-session orders. The ticket says extended hours are not simulated.
- Refused submissions from the bond, FX, OTC, OTC quote and private-trade tickets are recorded and listed under Rejected.
- The account Orders page has load more, so every fill is reachable (US Exchange showed only the first 200).

**Portfolio, history and performance**

- New: the History hub (Shift+F10). Your NAV history as a table and a chart, each restated day beside its original (a toggle shows the original snapshots), and for a restated day its split - original NAV, the effect of each imported line, interest, marks at the official close and dating - with a residual of zero. "Portfolio as of" shows that day's positions and marks, read-only. Transactions, account activity, performance, closed positions, imports and past lifetimes are tabs of the same page.
- New: closed positions, on the Portfolio and in the History hub: open and close dates, quantity, average open and close prices, realised P&L, fees, income, financing, days held and how each one closed; CSV.
- New: a click (or Enter) on a Portfolio row, or one click on an account's Positions row, opens the position's drawer: every fill with its order and settlement state, every entry with the cash it moved (paged), open and closed lots, the borrow, marks and dividends. The instrument page is one click away in the drawer.
- New: return tiles on the Portfolio: today, month to date, year to date and since inception (time-weighted and simple).
- Position rows show total P&L (unrealised + realised + income + financing + fees), fees to date, the counterparty (the stock lender of a short, Goldman for an OTC contract, the clearing house for a listed name), the asset class and the sector; margin and collateral come from the collateral module.
- Sectors: the Portfolio groups by sector with long, short, net and % of NAV, and Risk shows the same; options and TRS count in their underlying's sector. You can set a name's sector yourself ("Sector ..." in the drawer); VMRK reads "unclassified" until you do.
- Market value and exposure are told apart: the Portfolio footer shows futures notional, TRS reference shares x today's price, CDX notional with its credit sign and the IRS DV01 - the same definitions as Risk. Risk's TRS exposure is now reference shares x the price now (before: the contract notional).
- Performance counts trading days only: weekend and holiday interest is folded into the next trading day; annualised ratios appear after 20 trading days ("insufficient history" before); the cumulative time-weighted return and the daily series are always shown.
- Risk: a Reg T panel per account (requirement, eligible collateral, excess or deficit, the assumed terms), and each exposure bucket says whether it is in the gross. Activity: a "margin calls" filter.
- Wording: Activity rows and their details read in words (accounts by name, money as amounts, rates as percents); the Accounting transactions, ledger and entry dialog show kinds and memos in words; STT's dividend note says "not held on the ex-date (held since 2026-10-22)"; the Treasury page offline says SOFR is not loaded yet, without convention codes; a stale FX forward mark shows its real age.
- The Portfolio and account Positions tables fit a 1440-pixel screen with the ticket open; on narrower screens they scroll, never clip.

**OTC dealing and derivatives**

- New: OTC deals now go through a request for quote. On a prime broker's OTC tab, Preview is followed by Request quote instead of Book. The simulated dealer answers on the next lifecycle tick (about 30 s) with a model quote labelled "indicative model quote, simulated dealer", good for 120 seconds. Accept books it at the quoted terms while it is valid; after that it expires and acceptance is refused. Decline closes it. Nothing is booked before you accept. Open requests show on the desk, on the Derivatives page and in Pending Trades.
- An accepted quote is honoured. Swaps, TRS and CDX book at the quoted rate, reset price or spread. OTC options, forwards and cross-currency swaps book on the market prices the quote was made from. If something else moved in between (a volatility, a curve, the dealer's schedule), the acceptance is refused and nothing is booked.
- New contracts start PENDING CONFIRMATION and are confirmed at 17:05 ET ("CONFIRMED - simulated confirmation"). Your as-of contracts keep their as-of confirmation. The two TRS you booked on 1.0.5 say "no confirmation record: booked before confirmations were simulated".
- New: the Derivatives page (Shift+F6). OTC contracts: the whole book, with open quote requests on top, Terminate, and each contract's terms, credit events and timeline (requested, quoted, accepted, booked, initial margin, confirmation, every variation margin, fixing, payment and reset). Listed options: greeks and strategies (your QQQ bear put spread). Futures: initial margin, daily variation margin, open trade equity and the delivery warning (GC and ZN Dec 26 are physically delivered: first notice about 2026-11-30, roll or close before about 2026-11-27). Servicing: the resets, payments, coupons and expiries coming up.
- New: a simulated CDX credit event, only when you ask for it. On a CDX row, "Simulate credit event" takes the defaulted name, the recovery and the date. The defaulted notional is the original notional / 125 names for IG or / 100 for HY (equal weights, assumed). It settles protection x (1 - recovery) the next business day, net of the premium accrued on it. The notional, the premium, the initial margin and the contract cost then follow the smaller notional. Real credit events and auctions are not tracked (Unsupported list: "CDX credit events: SIMULATED on request").
- Fixed: a TRS booked after the close deals at the official closing print (the ticket waits for it by itself), never at the last delayed print before 16:00.
- Fixed: the IRS preview now says its par rate comes from the Treasury par curve, used as a proxy for the SOFR swap curve. No SOFR swap rate is sourced, and dealers' SOFR swap rates usually sit below Treasury par.
- Fixed: model marks no longer end in an internal code. A refused OTC or CDX preview explains why inside the ticket. The options and forwards tickets no longer ask you to preview again while they retry by themselves.

**Cash, collateral and margin**

- New: the Accounts page (Shift+F4) lists every account of the book (Treasury, US Exchange, Debt Instruments, Credit Instruments, Foreign Markets & FX, Private Markets and each prime-broker account). For each it shows NAV, settled cash, available, reserved, restricted, margin required and its excess, working orders, settling trades, attention items and the custodian. A row opens that account; Treasury opens the Treasury page. "Open prime broker account" starts the usual onboarding, and nothing is ever opened for you. Below the table are the margin calls to act on and your positions grouped by custodian:
  - US Exchange: PB custody, DVP;
  - listed options: OCC via the clearing broker;
  - futures: CME Clearing via the futures commission merchant;
  - OTC contracts: bilateral with Goldman Sachs under the ISDA / CSA;
  - cash: the bank.
- New: the Cash & Collateral page (Shift+F5) shows:
  - cash by account and currency, split into available, restricted, Reg T margin cash and reserved;
  - settlement states;
  - short-sale proceeds against the cash collateral the stock lender holds, with your own cash between them;
  - interest and financing by type, for today, the month and since inception;
  - collateral by counterparty: posted and received, net per CSA, cash and securities, initial and variation margin, requirement against eligible;
  - margin per account with the Reg T terms;
  - margin calls, CSA pledges and Treasury bills;
  - an assumptions editor for each account's Reg T percentages and the CSA reuse flag.
- New: Reg T margin on US Exchange and the prime-broker accounts. The terms are assumed and you can edit them:
  - 50 % initial and 30 % maintenance of the short value;
  - fully paid longs count at 50 % of their value;
  - free cash counts in full.
- If the longs' loan value does not cover the Reg T requirement, the account's own cash that the requirement needs is held back from available cash, buying power and the evening sweep ("margin requirement (Reg T)"), and a shortfall raises a margin call. On your book today the requirement is covered by your longs, so nothing is held back.
- New: margin calls, recorded every business day at 17:20 ET from the first evening after the update. They are never backdated. They cover futures variation margin, OTC variation and initial margin under the Goldman CSA, stock-borrow collateral top-ups and Reg T shortfalls. Each call shows its counterparty, amount and due time (the next business day 10:00 ET).
  - A variation-margin call is met by its own payment: your cash, a Treasury sweep or PB financing.
  - A Reg T call is met by cash you move into the account.
  - An unpaid call stays open with an error item and is marked unmet after its due time.
- A margin call moves nothing by itself, and positions are never sold for you. Calls also show on the Activity page, and the Dashboard and Risk pages show the margin figures.
- New: Treasury bills, notes and bonds can be pledged to Goldman Sachs under its CSA as securities collateral. The assumed haircuts are 2 % for bills and 4 % for notes and bonds; you can edit them in the prime-broker schedule. A pledge moves no cash and no ledger balance, and the pledged face cannot be sold or pledged again until you release it. The Treasury page lists your Treasury securities as cash equivalents (market value, maturity, accrued discount), or "Treasury securities: none".
- A refused transfer now names what holds the cash back, for example "settled cash 100,000.00, less margin requirement (Reg T) 100,000.00". A night that waits for SOFR says so in plain words.

**The historical import**

- New: History > Imports shows each import batch of the book. For IMP-F1-20261027-A that is its state ("applied 2026-10-28 09:57 ET"), every line's decision, what was found and what was done (with the order, fill, contract, settlement and transfer ids it created), the business-date cash table, the funding moves, the restated NAV with its split (import effect by line, mark policy by position, dating) and the bridge to the workbook's 25,770,700.86. "Check against the book now" compares every line with the book as it stands, read-only.
- `python -m finsim import ...` runs the historical import tool, with the same options as `python -m finsim.terminal.importer`.
- A batch that is already applied, run again (a dry run on a copy of the book or a second apply), only checks and reports: it records no input, sends no command, posts nothing and leaves the batch's record as the apply wrote it. In 1.0.5 such a dry run with a later data pack was refused at the first option line.
- Report wording: differences are spelled out, and the restore procedure applies to 1.0.5 or later.

**Starting FinSim**

- `python -m finsim` now works from the repository root as well as from its `finsim` folder (`cd C:\Users\logan\intelligence-terminal`, then `python -m finsim serve`). The root's `finsim` folder carries a small `__main__.py` that runs the real package; before, Python stopped with "No module named finsim.__main__". Nothing is pip-installed and FinSim2 is untouched.
- `FinSim.cmd` (the same file in the repository root and in its `finsim` folder) starts FinSim from any folder. With no command it opens the app; `FinSim.cmd serve` (in PowerShell, `.\FinSim.cmd serve`) and every other command pass through with their exit codes. It uses Python 3.12 when the py launcher has it, else `python`.
- `python -m finsim` with no command prints a three-line hint (serve / open / status) instead of an error.
- `serve` first prints the code folder, version and database it runs. On your real home it also says which folder the FinSim icon and Windows login start when that is another one.
- `status` names the folder the server runs from and the folder the Startup entry starts: `Startup entry -> <folder> (finsim-terminal <version>, release <tag>, commit <SHA>)`. It also checks that the entry's Python and code exist. When the running server is not this folder's code, it no longer says "restart" if a restart would only start the installed release again; it gives the two real choices: open the installed app, or stop and serve here.
- `install` is refused from a git working tree, such as the repository, unless you pass `--from-working-tree`: the installed app comes from a release folder made from a commit. The sequence it prints is for the folder it installed.
- `status` and `install` point out the two old Chrome app shortcuts named FinSim, which only open a window and cannot start the server. They are left in place for you to delete.
- The Startup entry and the FinSim icon write why they did not start (Python or the code missing) to `%USERPROFILE%\.finsim\launcher.log`.
- `python -m finsim legacy` (the retired game's archive view) is removed. Nothing was deleted: its code and `finsim.db` are kept.
- The README describes FinSim1 as it is now: no news, the new navigation, the start commands with their exit codes, the installed app, data and backups, the market-data sources, the limits and the tests. FinSim2's section is byte-for-byte unchanged.

**Tests**

- Three new acceptance tests:
  - a browser smoke test opens every main page in headless Edge through FinSim1's own standard-library DevTools client (`tests\terminal_cdp.py`) and checks for no console errors, no failed requests and no news strings;
  - a directive test map (`tests\test_terminal_directive_map.py`) maps the 25 areas of your directive's section 18 to 96 tests, and fails when a mapped test disappears, is renamed or would be skipped;
  - the whole-fund reconciliation now includes a Treasury bill pledged under the Goldman CSA and released again, with no P&L, no ledger movement and no NAV change.
- The 1.0.3.1 short-sale proof (22 checks) now runs as a regression test. Hand-worked tests cover Reg T with an excess and with a shortfall, margin calls met and unmet, and a CSA pledge: 1,000,000 face of a bill at 99.00 with a 2 % haircut is eligible for 970,200.00.
- New startup tests (`test_terminal_launch.py`) start the real server as a child process from both folders and check its marker, the "already running" refusal of a second start, the shutdown with no lock files left, a database in the 1.0.3.1 layout (migrated, chart synced, backed up, integrity ok), and `FinSim.cmd` run from another folder.
- `python tests\run_terminal_suite.py` runs FinSim1's suite from any folder and ends with "RAN n FAIL n ERR n SKIP n FILES n" and "ResourceWarnings n". `py -3.14 tests\run_terminal_suite.py` runs it on Python 3.14.
- The bond tests close their fixture files (no more ResourceWarnings).
- 832 tests on Python 3.12 and 3.14, none failing.

**Fixes from the acceptance walk**

- FX forwards, FX options and cross-currency legs are never priced on a rate that has stopped updating. A daily rate (SOFR, the ECB deposit rate, SONIA) may be at most 10 days old, and a monthly OECD average at most 62 days past the month it averages. CHF, whose free series ended in March 2024, is now spot only, with the reason. Every rate on a ticket shows its date and age.
- OTC equity forwards are marked at the SOFR-carry mid. The prime broker's financing spread is charged on the side you deal: SOFR + spread when you buy, SOFR - spread when you sell, so a sale is no longer dealt above the funding-neutral forward. A name with no dividend data is not priced; its dividend yield is never assumed to be 0.
- FX forward tenors are ON, 1W, 2W, 1M .. 1Y on both desks. Pairs without a usable rate are listed spot only, with the reason.
- A cross-currency swap now starts on the spot date of its FX trade date: the initial exchange settles then, with its own funding, and the booking moves only the initial margin. It is margined from its trade date. The ticket picks currencies from a list, says how to fund a currency you lend, and names swaps by the market pair (XCCY EUR/USD). Legs remain fixed / fixed in this version.
- Risk: OTC FX forwards count in the currency exposure and the FX scenarios, outside the headline gross.
- Dashboard: its mark snapshot takes about 3 ms instead of about 240 ms, and the answer is within its 300 ms budget again.
- Wording: contract events and timeline rows are in words with one shape; a settlement-fail item names the account; a missing daily bar is said as a data gap; the jobs list (`GET /api/jobs`) gives the product jobs' next run; dealer quotes spell out the deal; forwards on Positions show their legs and value date.
- The page declares its own empty icon, so the browser no longer asks the server for /favicon.ico (that 404 was a console error on the first page load).
- An OTC contract booked after the 17:00 ET cut-off gets its first variation margin on its own business day (the next day, the day of its initial margin), never on a day that had already closed.
- The OTC contract timeline shows each step at the date and clock time it was recorded, or "as of" its business date when it was recorded later (the import, a catch-up), with the recording time in words. A step posted after the 17:00 ET cut-off names its business date in words. The rows read in order.
- Pending Trades and Completed Trades fit 1440 px with the ticket dock closed or open: related fields share a cell, and with the dock open the secondary columns fold into the cells beside them. Copy and CSV still give one column per field.
- Derivatives > OTC contracts (9 columns) and the prime broker's OTC contracts table (7 columns, fits with the dock open) stack related figures, so every mark is on screen. The quote-request block wraps its quote on every page.
- After "Resolve -> retry" on a simulated settlement fail, the attention item that asked you to resolve it closes. The settlement says "retry requested" until it settles.
- An FX forward's notional reads "EUR 1,000,000.00 (USD 1,114,100.00 at the spot rate 1.1141 when dealt: the IM base)". The USD figure is the initial-margin base, not the amount delivered.

**Coming next:** 1.0.6.1 - the Goldman FX trades (the EUR/USD cross-currency swap and the EUR/GBP 2-year forward), with prime-broker forwards out to 2 years on real curves; 1.0.7 - single-name CDS; 1.0.8 - the Institutional book pack: a stress engine, the fund book and a SOFR swap curve.

## 1.0.5 (built 2026-10-28; your Goldman trades and options, as of 2026-10-21)

**Your Goldman trades and options**

- New: a historical import that books trades as of their real dates. It runs from the command line (`py -3.12 -m finsim.terminal.importer`), first as a read-only report, then as a dry run on a copy of the database, and only then for real, after a backup. It is idempotent: running it again books nothing twice.
- Your batch IMP-F1-20261027-A ships with this release, byte for byte. It keeps your 23 existing positions exactly as they were (differences from your workbook are reported, never changed), and books:
  - with Goldman Sachs, value date 2026-10-21, at your dealt terms: TRS ORCL short 38,700 at 129.13, TRS META short 7,200 at 697.52, CDX.NA.IG 5Y buy protection 3,000,000 at 64 bp, CDX.NA.HY 5Y buy protection 4,000,000 at 352 bp, and a 3-year USD SOFR swap paying 4.720 % fixed on 7,500,000 (Goldman's schedule terms: TRS financing SOFR - 0.60 %, initial margin per the Goldman schedule);
  - the five listed options as backdated fills at your prices on 2026-10-22 (ORCL 140C, META 660C, IBIT 46C, and the QQQ 700 / 675 put spread as one package);
  - every margin call, variation margin, financing accrual and payment since then, each on its own date and on that date's official inputs.
- Every as-of row says so: "As-of booking - simulated ... entered <time> by the operator at the owner's instruction (batch ...)". Nothing is labelled modeled.
- Your NAV history is restated, never forced: each restated day keeps the original beside it, with the split (original NAV, the effect of each imported line, interest, marks at the official close, dating) and a residual of zero.

**Money, NAV and collateral**

- Restricted cash: collateral the counterparty posts stays its collateral. Variation margin received under the prime broker's CSA (net of what you posted) and cash collateral for shares you lent are excluded from available cash, buying power and the evening sweep, and earn no PB credit interest. A payment to the same counterparty may still use it.
- Official closes: a stock's recorded close is the regular-session closing print (at or after 16:00), never an earlier print. A close that differs from the official print is re-recorded before the 17:05 swap variation margin, and any revision, one tick included, is reported. (Found by an after-close check on 2026-10-27: a TRS margin would otherwise have been valued on the 15:59:59 print.)
- OTC contracts carry their cost basis in the Portfolio: a CDX upfront is no longer a day-one realised gain, and TRS financing and swap coupons count as financing.
- Evening jobs post on their own business date: the 17:05 swap variation margin, the 17:10 collateral marks and the sweeps that fund them.
- A catch-up snapshot is the NAV of its own business date, valued at that day's recorded closes.
- Settlement fails: a purchase that cannot be funded by the end of its settlement date is marked failed, keeps its error item and is retried; when it settles it is marked resolved. (The buttons for a fail you trigger yourself arrive with the Completed Trades drawer in 1.0.6.)
- The Portfolio footer's gross long / short equal the NAV block, with OTC contracts beside them at market value. A prime-broker account counts its OTC contracts as positions, and OTC quantities show in their own units (reference shares or USD notional).
- Borrow lenders read "street lender (assumed terms)".

**Navigation and news**

- News is gone from FinSim1: the Daily News page, its background jobs and its endpoint are removed; nothing fetches or shows news. Stored headlines stay in the database, untouched.
- The Dashboard is the landing page (F12), with the selected book's "Requires your attention" tiles. An old news bookmark opens it with "That page was removed - showing the Dashboard".
- The new navigation's pages (Pending and Completed Trades, Accounts, Cash & Collateral, Derivatives, History) answer at their addresses with NOT YET BUILT until 1.0.6. The data behind Completed Trades is already there: every fill, FX deal, OTC booking and financing trade, one row per execution, with its parent order or ticket.
- A second start on a running FinSim says who is running (version, PID, since when, address, code folder) and what to do instead.
- Every start adds the chart-of-accounts rows an older database lacks.

**Tests**

- A whole-fund reconciliation test runs a mixed fund through two days of end-of-day work and checks after every step that NAV = assets - liabilities, margin and collateral are never P&L, the counterparty's collateral is never swept or spent, and no account's cash is below zero at the close of any day.
- An import checker (tests/terminal_import_check.py) verifies a dry run of the import: your existing records identical, the ten bookings at your exact terms, no account below zero on any business date, integrity ok, restatement residual 0, and a second run books nothing.

**Coming in 1.0.6:** the Pending Trades and Completed Trades blotters, the full Dashboard, the Accounts, Cash & Collateral, Derivatives and History pages, the rest of the news code removed, the Windows startup fix, the directive's full test map and browser check, and 17 small fixes left over from 1.0.4.

## 1.0.4 (built 2026-10-27)

- Includes the 1.0.3.1 hotfix: short-sale collateral paid from the sale's proceeds, orders that wait for funds, and borrows for names added on demand.

**Markets**

- Debt Instruments, Credit Instruments and Foreign Markets & FX are now real marketplaces. The prime broker's OTC tab trades swaps, options, forwards and CDX (see below).
- Every account page has the tab bar Markets / Positions / Orders / Cash & financing / Transactions.
- Futures are grouped (CME equity index, CME rates, CME FX, NYMEX energy, COMEX metals, CBOT grains). Each contract shows its most active month, with a month picker.
- A name with no print for 10 sessions reads "no print since <date> - possibly delisted" and cannot be bought or sold from the list.
- A click or Enter on an options underlying opens its chain. Option rows offer buy to open / sell to open, "add symbol" puts the symbol on the account's watchlist, and empty bid / ask columns are hidden.

**Trading**

- Hedge on a held position proposes hedges sized to that position and submits only the hedge. One leg goes as its own order, several as an all-or-none package; the position is never ordered a second time.
- New hedge candidates: an FX forward (opens the FX desk) and Treasury futures sized by a bond's DV01. The TRS overlay now opens the prime broker's TRS form filled in; it used to be offered as an order leg that could not be submitted. Ratios read "1 contract per 100 shares", and a zero-size candidate is hidden with the reason.
- Every option strategy template previews on strikes that have never traded (it used to fail on an unknown instrument).
- A bond, FX pair, private name or swap typed into the order ticket goes to its own ticket in the right account. One that still reaches the preview is refused there with a pointer, never at submit. The ticket labels the quantity's unit.
- The preview checks an option stop against the contract's bid (sells) or ask (buys), and a trailing stop starts from that price, as fills already did. Order events say whether the bid or the ask was used.
- A Cboe outage now marks that underlying's option quotes stale, so option market orders are refused instead of filling on old prices. After a restart, no saved price executes until a fresh quote arrives.
- A triggered stop-limit stays triggered and fills when its limit is reached later. A limit order that is marketable when it arrives fills at the executable price, not at its limit.
- The volume cap is shared by every order against the same minute bar, across accounts and books.
- Stock orders fill at the Cboe bid / ask when a cached option chain's quote is at least as recent as the last print.
- The preview shows the trade and settlement dates of the first session the order can trade in (an order entered on Thanksgiving settles Monday 11-30).
- Opening option trades do not fill after 15:30 ET on expiry day. GTC orders expire at the close of the 90th day's session.
- Treasury sweeps are automatic at any size; a confirmation is asked only above a threshold you set. The large-order confirmation (above 25 % of NAV) names its rule and threshold.
- A fill refused for any reason other than cash suspends the order with the reason and one notice.
- An exercise or assignment that delivers shares cancels or trims the working sell orders on them. Selling shares that are lent out asks you to recall them first.
- A symbol opened for the first time is fetched ahead of the lists and reads "no quote yet - fetching" until it is quoted. The option chain's freshness chip follows the same staleness rule as the ticket.
- The wash-sale notice counts only replacement purchases. FX and foreign previews show only the notices that apply.
- The preview lists each fee (venue taxes included) and the cash any hedge legs need, and repeats a typed limit. The result shows plain fill prices, and "quote used" says whether the bid, ask or last was used.
- The instrument page shows the session's 1-minute chart, a labelled spec, and the chain on the nearest expiry 7-30 days out, centred on the money (days to expiry listed, "no bid" on one-sided quotes). It opens the ticket without forcing a side (options: buy to open).
- Bond, private and OTC instrument pages show their mark with its source and date instead of a Yahoo chart, chip or chain.
- Positions show the actions first, visible with the ticket open, and offer only what each kind can do: Close / Hedge / Exercise / Return, the bond ticket, Sell for a private name, Terminate for a swap, Repay for a private loan. New columns: "% acct NAV" and exposure.
- The exercise dialog explains each right (a put sells 100 shares at the strike), compares intrinsic value with the mark and warns when the option is out of the money.
- Orders show a trailing stop as a percent ("trail 2.00 %") and which side of the quote was used. "New order" opens the ticket that fits the account.
- A watchlist refuses a symbol the chosen account cannot trade (TLT in Debt) and opens rows in an account that trades them.

**Prime broker / OTC**

- Interest-rate, total return and cross-currency swaps are in this release; they were held out of 1.0.3. Terminating a swap now first settles every night it missed, so its closing payments are right.
- The swap ticket keeps every typed term for each swap type through previews, errors, refreshes and tab switches. A refused field stays marked, an out-of-date preview cannot be booked, and the ticket shows its funding line.
- A cross-currency swap is refused while the only FX rate is the previous day's ECB reference. It deals at the live delayed rate, shown with its source and time in the preview and the confirmation.
- Swap initial margin is funded from what the account has available (payables due count) and swept from Treasury at booking, as the preview shows.
- The first TRS preview on a name with no quote yet says it is fetching the price and retries by itself, instead of failing once.
- The terminate dialog says the dealer re-prices when you press Terminate, and shows the model mark as of the last refresh.
- Prime-broker positions show the full OTC name (for example "CDS index: buy protection on CDX.NA.HY 5Y"), and the settlements ladder names each CDX initial payment.
- The contracts table puts status and Terminate first and fits beside the ticket; on a narrow screen it scrolls on its own. It refreshes in place every 30 seconds, and if a load fails it says "could not be loaded - retrying" instead of showing an empty list.
- A contract's drawer shows dealt / accrued / next payment and its variation-margin history, each entry linked to its journal entry.
- Prime-broker pages no longer answer "busy" while swaps are held. The header and sidebar update right after a booking or a termination.
- A TRS or cross-currency swap's variation margin waits for that day's close and posts when the close arrives, with one warning if it keeps waiting. It never uses an earlier or intraday price.
- Swap positions show side and notional in their own currency, with Terminate and a link to the OTC tab instead of Close / Hedge.
- New: OTC options and forwards with the prime broker. Equity options are priced on the Cboe implied vol, and FX options on a CurrencyShares ETF proxy vol or a 30-day realised vol (labelled). There are also equity forwards, and FX forwards that deliver both currencies on the value date.
- The OTC ticket shows the model inputs, the greeks, the dealer's half-width and the initial margin from the broker's schedule, and asks you to confirm. Premiums settle T+1 (equity) or T+2 (FX), variation margin is daily, and expiries and maturities settle on their date. Early termination is at the model value with the half-width against you.
- New: CDX index protection. Buy or sell protection on CDX.NA.IG and CDX.NA.HY 5Y (series 47, to 2031-12-20; coupons 100 / 500 bp) on the OTC tab's Credit (CDX) desk.
- The CDX spread is a proxy, FRED's ICE BofA option-adjusted spread, labelled "not a CDX dealer quote" and LIMITED; there is no price once it is more than 3 business days old. Marks are model values from each day's proxy and the Treasury curve. Credit events in the index are not tracked.
- A CDX preview shows the upfront, price, CS01 and rates DV01. The initial payment (upfront and accrued rebate) settles T+1, and initial margin is assumed at 2 % for IG and 6 % for HY; basis, half-spread and margin are editable. Variation margin is daily, coupons are quarterly, and a contract is terminated from the contracts table.
- Risk shows CDX notional apart from the headline exposure, with a credit block and CS01.
- The OTC tab shows the signed agreements and the dealer's terms above the Swaps / Options / Forwards / Credit (CDX) tabs and one contracts table of every kind. Below the eligibility floor it gives the reason once and disables Preview and Book.
- Prime-broker schedules show labels and units (typed in %, bp or vol points), the seed value and the allowed range. Ranges are checked in the form, and "opened <date>" shows until the first edit. Each broker has its own initial-margin terms for each OTC product; the seeds are assumed, editable and differ by broker.
- A flat prime-broker relationship can be closed ("Close relationship").
- The onboarding window lists every dealer's schedule side by side and marks dealers already open. "+ Open prime broker account" appears only when the book can open one.

**Credit**

- Credit Instruments has seven tabs: Currency loans, Repo & reverse repo, Securities lending, Stock borrows, Private credit, Rates and Counterparties.
- A counterparty directory lists 10 banks, the 25 NY Fed primary dealers, 6 agent lenders, the book's prime brokers and private-credit borrowers from the company master. Their terms are assumed, editable and labelled as such, and exposure is shown per counterparty.
- Currency loans: pick the lender, whose spread and facility limit apply. Rows show accrued interest, the index value and its date, the spread and the all-in rate. A full payoff repays principal and interest, sweeping the currency from Treasury.
- Repo and reverse repo against Treasuries: SOFR plus or minus the dealer's spread (ACT/360), overnight, term or open, with roll and close. Haircuts are 1 / 2 / 3 %, and collateral is marked daily both ways.
- A bond pledged in a repo cannot be sold, and a repo on a bond that has not settled yet is refused.
- Securities lending: lend long shares through an agent lender for your share of the tier fee. You hold 102 % cash collateral, pay a rebate on it and mark it daily, and you recall the shares when you choose.
- Stock borrows: locate and borrow ahead of a short sale; every open borrow of the book is listed with Return. Lent and borrowed shares show their market value (it read n/a).
- Private credit: the borrower comes from the company master, and the Private Markets qualified-purchaser rule applies. A commitment needs a recent SOFR print. Without a recent high-yield spread it books only if you acknowledge that, and the first later print becomes its issue spread. Partial prepayment is at par, and the maturity and last payment dates are shown side by side.

**FX**

- Foreign Markets & FX has tabs FX spot, FX forwards, Foreign listings, Foreign bonds and Currency positions.
- The FX desk deals spot and forwards between any quoted currencies, and crosses such as EURJPY directly. Before Confirm, the preview shows the deal rate, spread, both legs, value date and funding. FX rows open the desk, and USD/CAD and USD/TRY spot settle T+1.
- FX forwards are priced by covered interest parity from spot and each currency's rate, shown with its date. Monthly proxy rates are LIMITED, and a currency with no rate gets no price. Tenors run from overnight to 1 year, or a broken date, and no cash moves until the value date. From Portfolio, Enter or a double click on a desk forward opens the FX account's positions and the FX desk; the row reads "EURUSD fwd" with both legs and the value date.
- Selling a currency the account cannot deliver by the value date is refused, with the shortfall and how to fix it; a forward gets a warning instead.
- Foreign listings on a known venue trade in that venue's session and currency. The preview shows the currency needed, the USD conversion that would fund it and venue taxes (UK and Hong Kong stamp duty, French and Italian transaction tax; assumed rates). The conversion is booked with the fill only when you tick it.
- Pence prices read "GBp 2,700.00" with the GBP value.
- Currency positions show each currency's settled, pending and forward amounts with their USD value, also on the FX account's Positions page.

**Debt**

- Debt Instruments lists real US Treasuries by CUSIP from Treasury's fiscal data (462 on 2026-10-21). Prices come from TreasuryDirect FedInvest, moved by today's Yahoo yield change when that price is older, or else from the par curve, each with its source and date. A Curve tab charts the curve.
- A bond or T-bill preview refuses what your cash and the Treasury sweep cannot cover, names the largest face that fits, and keeps Submit off.
- Bills, notes and bonds trade on a bond ticket by face amount: clean price, yield and accrued, at the mark plus or minus a disclosed half-spread, fee 0.02 per 100 face (minimum 5.00). They settle T+1 (foreign bonds T+2 in their currency) and trade 08:00-17:00 ET. Bond trading is on by default; bonds cannot be sold short.
- Generic corporates by rating and tenor are priced from the curve plus that rating's ICE BofA spread from FRED, or show unavailable with the reason. They are labelled generic.
- Foreign government benchmarks: Germany (ECB AAA curve) and Japan (MOF) priced daily; UK, France and Italy from monthly OECD yields.
- TIPS, FRNs and the monthly UK / French / Italian benchmarks are LIMITED and cannot be traded; each row says why.
- Bond accrual, coupons and redemption run nightly in the bond's currency. The old monthly model benchmarks are retired.
- While FRED is in maintenance, Data Status says "FRED unavailable (maintenance)" and the last good values keep their true date. SOFR comes from the NY Fed and the ECB deposit rate from the ECB, both labelled.

**Risk and leverage**

- New tiles: Gross exposure / NAV and Gross notional / NAV, with definitions on hover. Long, short and net are shown as % of NAV per account, per prime broker and per book, and TRS notional / NAV has its own figure. The leverage notice stays a warning. FX-desk forwards count at their notional in their own "FX forwards (notional)" line, outside the headline.
- Risk adds exposure by class, account and currency; equity delta and greeks by underlying; bond DV01 by tenor with full-reprice rate scenarios; financing and liquidity; and exposure by counterparty. A per-prime-broker block shows OTC contracts, initial margin, variation margin, signed DV01 and readable names.
- Each scenario group has one plain sentence. Every section updates in place, and News tiles open the section they count.
- Unpriced private holdings count at cost in the private bucket and in concentration, which is judged against the book's NAV.
- The futures maintenance notice on a closing order shows the margin after the close.

**Portfolio and accounting**

- Portfolio is rebuilt:
  - positions grouped by class with subtotals
  - a footer with gross long / short / net and futures notional
  - weights as a share of the selected book's NAV (or all books')
  - a by-account table with P&L today and % of total
  - a book column in All books
  - an account filter by name and a Positions CSV
  - the refresh keeps your filters.
- Activity shows the selected book's current lifetime, with All books on a toggle, and lists open items. It filters by type (orders, settlements, transfers, financing, OTC, attention, other, system); system rows are hidden unless asked for. Each row's details show their debit / credit lines.
- History is read-only, with a way back to lifetimes. Its P&L (inception to date) and balance sheet now show figures.
- Accounting adds Transactions (with CSV) and Cash & financing tabs. The P&L explain rows add up to "explained", Integrity lists one row per check it ran, and Treasury advances read the same on the Treasury page and the balance sheet.
- Transactions show the other side of each line. A futures fill's quantity and price appear on the fill row only, and the margin row has its own memo.
- Treasury:
  - Transfer defaults to Treasury -> the first trading account and refuses the same account twice.
  - Add funds / Withdraw take plain amounts (at most 2 decimals), ask to confirm and submit on Enter.
  - Sweep reasons name and link their fill.
  - The reserve is set per book, and available never shows below zero.
- Cash & financing updates in place, so typed values survive the refresh. It lists the day's sweeps with what each funded, and OTC initial margin and variation margin posted / received with each contract's history. It also shows loan rows with full payoff, and the prime-broker financing switch (off until you turn it on) with headroom, limit and drawn.
- Loans are drawn on the Credit page and currencies converted on the FX desk; Cash & financing links to both.
- Empty tables say "no ..." instead of "loading ...". Long cells wrap so tables fit 1440 px, and a double click opens one detail, not two.

**Money and settlement**

- Short-option margin is now a reservation against available cash, and no cash moves. Margin already posted is released back to cash once, at the first start of 1.0.4.
- Same-day round trips show the real available cash, with no phantom sweep: receivables and payables due by the same date count.
- Preview and fill fund an order the same way: own cash, then one Treasury sweep, then prime-broker financing, then a currency conversion where needed. Nothing moves unless the whole need is covered.
- A working futures order reserves only the initial margin of the contracts it opens. A future closed during the day shows its P&L in NAV before the evening variation margin.
- Positions without a price are carried at cost in NAV and on the balance sheet.
- A Treasury transfer checks settled cash in that currency; unsettled sale proceeds are refused, naming the settled amount. A deposit in a currency with no FX rate yet is refused plainly.
- Options exercise, assign or lapse with the expiry date as trade date. Loans with a maturity date now mature on it.
- Nightly jobs:
  - A catch-up no longer sweeps back cash moved in during the day.
  - The prime broker's monthly minimum is charged correctly.
  - Missed borrow-fee nights accrue before the day's collateral mark.
  - Resetting a lifetime with settlements pending no longer raises an integrity hold.
- A failed job clears on its next success, and retries are counted in Data Status. A job that keeps waiting raises one warning naming the cause.
- A command retried after a failed attempt goes through, because nothing half-made is left behind. A command that keeps failing raises one notice, not one per try.

**Private Markets and books**

- The private ticket docks beside a full-width companies grid. Preview without a price asks for the negotiated price per share; previews show the risk lines, and the result shows the sweep.
- "Mark as listed" moves a company to listed; a source link is required. Listed or acquired companies cannot be bought, watchlisted or given a funding round in Private Markets. In US Exchange, searching SpaceX, xAI or Cursor finds SPCX, and Cerebras finds CBRS.
- The qualified-purchaser rule applies to buys and rounds only; selling a private holding is never blocked.
- A Fund book that started at the threshold stays eligible for new prime brokers and Private Markets when dealing costs take it just below. A Private account, once open, stays open. A notice appears when a book with relationships falls below a threshold.
- Renaming a book in Settings takes effect, and a new lifetime takes its book's name.

**Screens, News and Settings**

- Memos and sweep reasons read in words ("for OTC initial margin SWP-000002 (at booking)") instead of internal codes, and refusals name the account and the dates involved.
- The header's P&L today includes fees (the parts on hover) and matches Portfolio. The version moved to the server chip's tooltip. With the server down, the banner says so first and the integrity dot turns grey.
- The sidebar shows Treasury NAV and cash, available cash by currency and one short reason per dimmed row, and wraps figures instead of cutting them. The book switcher uses short names and compact NAV.
- Each book sees only its own attention items, plus global ones, with readable names, and the strip can be expanded. Orders and attention items act only in their own book.
- Switching books forgets the other book's account, and a link to another book's account switches book. A closed lifetime's account is read-only, with no ticket.
- A page whose data fails to load shows the error with Retry instead of "loading ...", and an unknown page or tab falls back to a real one. The ticket dock with no instrument shows an empty ticket with a hint. Toasts are capped and stacked.
- Daily News reads the published newspaper index hourly, with the local copy as fallback. It lists the latest editions with dates and says "newest edition <date>" when the newest is old. Its tiles link to what they count, and the compliance tile counts only real notices.
- Settings sections load correctly on a direct load or a refresh.
  - New Calendar section: holidays, early closes and session hours.
  - New Margins & limits section: assumed futures margins and position limits, editable and applied to new requirements.
  - Data Status shows the next lifecycle job, the database size and the refreshers.
  - Unsupported reads in plain words.
  - The legacy simulation link shows only when the simulation is running.
- A second server pointed at a database already in use is refused, naming the running one, before anything is opened, backed up or written. Dropped browser connections no longer log tracebacks.

**Keyboard**

- The default keys are F-keys: F1 US Exchange, F2 Debt, F3 Credit, F4 Foreign Markets & FX, F5 Private Markets, F6 the prime broker (press again for the next one), F7 Portfolio, F8 Orders, F9 order ticket, F10 Treasury, F11 Risk, F12 Daily News.
- Shift+F1 / Shift+F2 / Shift+F3 select the Personal / Fund / Institutional book. Shift+F4 Positions, Shift+F5 Cash & financing, Shift+F6 Transactions, Shift+F7 Accounting, Shift+F8 Activity, Shift+F9 Watchlists, Shift+F10 History, Shift+F11 Settings, Shift+F12 (or ?) keyboard help.
- The 1.0.3 Alt+1..9, Alt+B and "g" keys no longer have a default; their actions can be bound. Ticket and table keys keep their 1.0.3 defaults, and "/" goes only to a search box you can see.
- F-keys work while the cursor is in a box (they type nothing). A key bound to an action never reaches the browser (F5 does not reload), and an open dialog takes no page keys.
- Settings > Keyboard (also opened by the help's Change keys button): change any key, with swap or cancel when the key is already in use; clear, reset one or reset all; see refused keys with the reason; and test a key. Table keys and the keys of every ticket and desk (order, bond, FX, private, swap, OTC, CDX) can be changed too.
- Your keys are saved in the app. They apply in every browser and survive a reload, a restart and a lifetime reset, and every key hint on screen follows them.

**Fixes**

- Your own keys now load before any key acts; a key pressed while the page was starting could run its default. A change made in another window is picked up.
- Text-editing Ctrl keys (Ctrl+C and the like) never act inside a text box. Ctrl+F4, Ctrl+Page Up / Page Down, Ctrl+Esc, Ctrl+Shift+Esc and Alt+Esc are refused as keys, with the reason.
- Ctrl+Enter no longer submits the ticket while a dialog is open. Enter or Space presses a focused button, Cmd+Enter also submits, and Shift with the numpad + / - steps ten lots.
- "Test a key" lets Tab leave the box (it held the keyboard).
- The command line prints its "ready" line as soon as the server is up (it could appear only at shutdown when the output went to a file).
- The swap ticket's SOFR fixing choices and the FX desk's currency boxes fit their width at 1100 and 1440 px.
- The 1.0.3 key hints left on screen (g n, Alt+1..9, Alt+B, "press b / s") now show the keys in force, and the FX desk's keys can be changed like the others.

## 1.0.3.2 (hotfix, staged 2026-10-27)

- The consolidated Portfolio page's positions grid did not render (the page stopped on an error); it now shows every position with class subtotals.

## 1.0.3.1 (hotfix, deployed 2026-10-21 21:04 ET)

- A short sale's 102 % cash collateral is now posted at each fill and paid first from that sale's own proceeds. The account needs only the 2 % excess plus the fee, and a working short order reserves only that; the whole 102 % used to be posted from cash when the order was placed. Borrows opened before keep their collateral.
- An order whose only problem is cash is accepted and waits for funds instead of being refused. The preview warns that it will queue, and the order shows "waiting for funds: needs X, available Y" with one notice. It holds back no cash, so later orders still fill, and it starts working by itself once the cash is there.
- A name added on demand gets its average daily volume from three months of Yahoo daily bars, so it can be borrowed and sold short. It used to have no volume, and every short on it was refused.

## Unreleased (WP-15 integration, 2026-10-21)

- Private Markets: a company that listed or was acquired (SpaceX as SPCX, Cerebras, xAI, Anysphere) is no longer a private name; the API's Private account markets view now lists only private names (plus listed shares still held) and names the listed ones apart.
- Swaps are wired end to end: IRS, total return and cross-currency swaps book through an eligible prime-broker account's OTC tab, with daily variation margin, fixings, payments, TRS resets and cross-currency exchanges run by the lifecycle job.
- The swap ticket now previews total return swaps (observed reset price, notional, financing rate and direction, reset dates, financing per period) and cross-currency swaps (deal FX rate, each leg's quote and model par, notional exchanges, reset dates, schedule per currency).
- Cross-currency rates may be left blank to deal at the prime broker's quote; either notional may be given; both legs' day counts can be chosen.
- An interest-rate swap at a fixed rate better than the dealer's quote is refused (OFF_MARKET) instead of showing a day-one gain no dealer would give.
- Variation margin on a TRS or a cross-currency swap is valued at the close of its own date (the underlying's close, the pair's 17:00 close); when only a later price exists it catches up instead of using another day's price.
- The variation-margin reference after a TRS reset is ordered by command, so a reset that posted no cash still re-bases it (one shared definition).
- The contracts table shows a TRS's side, underlying and reset price, and a cross-currency swap's legs, accruals and notionals in their own currencies.
- A TRS underlying and a cross-currency swap's FX pair are quoted, recorded at the close and (for a TRS) checked for dividends even when nothing else holds them, so resets and margin never wait on an unwatched name.
- Strategy templates and the hedge panel offer a TRS only when the TRS module is present (no more "available" templates that then answer NOT_BUILT).
- The swap position row shows the value NAV carries (the mark at the last variation margin) as its mark, with the live model mark in the position drawer.
- The position drawer lists the name's dividends (ex-date, pay date, received / accrued / upcoming) with the dividend convention.
- Bonds (Build 2) are wired: nightly accrual, coupons, redemption and the monthly model benchmarks run in the lifecycle; bond trades go through POST /api/bonds/trade behind the build2_bonds setting (off by default); the order ticket refuses a bond with "model price, not executable in this build".
- The housekeeping job now runs the private-company master sync (listings and status moves reach an existing database every night, not only at start).
- "Run job" in the API runs only the job named (it ran every job) and refuses an unknown name.
- The Risk page's prime-broker row shows the full financing headroom its formula describes, with what is left after commitments beside it.
- The swap reset sentence mentions SOFR compounded in arrears; the prime-broker fields note names the wire fields (equity_min, monthly_minimum).
- New tests: the D1-D12 acceptance map and a one-database reconciliation through the real runtime and the API (test_terminal_reconcile), and the integration requests (test_terminal_integration).

## 1.0.3 (fix release, built and gated 2026-10-21 15:03 ET)

- Option limit and stop orders are judged on the contract's chain bid and ask, not on a last trade that may be hours old.
- A failed quote fetch never makes an old price look fresh: during a provider outage prices keep their true age, and stale prices never execute.
- Currency and credit loans accrue interest for every night owed, weekends and holidays included (the maturity payoff skipped nights).
- US Exchange Markets lists every active US stock, ETF, ADR, REIT and preferred in the master (about 760, was 60) and every options underlying (was 30); names not on screen refresh in the background at up to 50 a minute, each about every 20 minutes.
- Quotes are saved only when they change and unwatched ones are pruned after an hour, so the database no longer grows with the number of names listed.
- Order ticket: Close on a long option sells to close (it bought another contract), closing a future previews the margin it releases, and quantity, type, prices and hedges no longer carry over to the next instrument.
- Private Markets and Private Credit pages no longer answer busy (503) with several pages open: valuing marks reuses the open database reader instead of opening a second one.
- Swaps (IRS, TRS, cross-currency) stay out of the deployed release until the overnight swap-termination defect is fixed.

## 1.0.2 (fix release, deployed 2026-10-21 13:15 ET)

- Market orders fill on real Yahoo data: Yahoo's zero-volume placeholder minute and the newest minute that is not final yet no longer read as no trading; the volume cap uses the latest completed minute with volume.
- An option picked on the chain is listed from the cached Cboe chain with its chain quote (it was unknown, or registered as a stock); Yahoo option symbols are refused with the reason.

## 1.0.1 (fix release, deployed 2026-10-21 10:39 ET)

- Foreign listings (London, Hong Kong, Tokyo …) were seeded as US dollar US Exchange stocks with the wrong Yahoo symbols; they are now foreign listings in their own currency (pence handled), shown with live quotes under Foreign Markets & FX and repaired in place at every start.
- Treasury showed cents as dollars (USD 100,000,000.00 for 1,000,000.00); every cents figure the API serves is now a decimal string.
- SOFR showed as 387.00 % in places; rate units are now defined once (published percents vs fractions) and every display follows it.
- Available cash never shows below zero: what working orders need beyond the account's own cash is shown as "reserved (Treasury sweep covers N)".
- A second order can no longer be accepted on cash an earlier order will sweep (it used to be suspended at the open); buying power counts earlier commitments.
- The header clock no longer shows --:--:-- on the first paint.
- Markets refreshes update in place: the filter, sort, selection and scroll stay, and a click is never swallowed by a refresh.
- "status" shows providers that have not been called yet as idle (not DOWN), integrity not yet checked as such, and times in ET.
- The Startup / Desktop / Start Menu folders are found through the Windows known-folder API; non-ASCII folders are handled safely.
- Portfolio -> Performance answered 400 whenever SOFR was loaded; it now reads SOFR as a fraction.
- The prime-broker schedule editor no longer zeroes the minimum commission when another field is saved; bad money values are refused with the field named.
- The currency-loan toast and grid show SOFR 3.87 % (not 387.00 %).
- SpaceX (listed as SPCX on 2026-06-12) and the other listed or acquired companies left the private list; the master was re-verified with sources.

## 1.0.0 (first deployment, 2026-10-21 02:07 ET)

- A new live paper-trading terminal replaces the day-advance simulation: real delayed prices, pretend money, one ledger, no simulated market (finsim/terminal; the old simulation stays as `python -m finsim legacy`).
- Three books from the first run — Personal (1,000,000), Fund (25,000,000), Institutional (1,000,000,000) — each its own lifetime with its own Treasury, accounts and ledger.
- One double-entry ledger in SQLite is the single source of truth; every figure is computed by one accounting module; integrity checks run after every command and on start.
- Every command is one transaction with a request id (retries are safe, nothing is half-written); errors surface with the reason.
- Exactly one server per home: a second one refuses to start; stop / status / install / open / uninstall manage it; a rotating log is written.
- Treasury holds the capital; an account that needs cash gets exactly its shortfall swept from Treasury, linked to the trade; idle cash returns after the close.
- Quotes come from Yahoo (delayed), option chains from Cboe, the Treasury curve from Treasury.gov, rates from FRED; every price shows its freshness; stale prices never execute.
- Orders: market, limit, stop, stop-limit and trailing stops; sessions and holidays from the real calendar; partial fills capped by observed volume; all-or-none option packages.
- Listed options with Reg T-style margin, exercise, assignment and expiry on the real date; futures with initial margin and daily variation margin.
- Short selling borrows first (102 % collateral marked daily both ways, fees and rebates accrued nightly); no borrow means no short.
- FX spot (T+2) and currency loans; repaying a foreign loan needs that currency.
- Prime-broker accounts with editable fee schedules, financing and credit interest, opened through onboarding that records the ISDA Master, CSA and PBA.
- Interest-rate swaps through a prime broker: dealt at the model par rate plus the disclosed half-width, daily variation margin, fixings (term or compounded SOFR), net payments.
- Private Markets for eligible books: researched company master with sources, negotiated secondaries with fees, settlement lag and lock-ups, last-round marks.
- Private credit loans with coupons, OID accretion, fees and quarterly payments; dividends and payments in lieu from Yahoo's events.
- Risk (exposures, greeks, DV01, scenarios, liquidity, per-PB margin) and compliance notices inform and never block.
- Daily News with real headlines and a "Requires your attention" strip; Portfolio performance statistics against SOFR and SPY.
- Reset lifetime closes a book read-only and starts a new one; History browses past lifetimes.
- 60 strategy templates and hedge candidates on the ticket (protective put, collar, put spread, index overlay, TRS overlay).
- No glossary (owner decision); conventions are one sentence on the page where they matter.
