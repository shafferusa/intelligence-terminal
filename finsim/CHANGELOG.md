# FinSim Live Terminal — changelog

One plain-English line per change, compiled by the WP-15 integration from `docs/BUILD_LOG.md` (every package's line) and
the resolutions in `docs/design_proposals/BUILD_NOTES.md`.  Newest first.  Versions are `finsim.terminal.VERSION`.

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
