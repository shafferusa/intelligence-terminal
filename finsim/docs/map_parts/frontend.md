# FinSim front end map (finsim/finsim/static/)

Mapper: front-end subagent, 2026-10-20 (started 12:30 ET). READ ONLY. Line numbers refer to
`finsim/finsim/static/app.js` unless another file is named. app.js lines are very long (2,005 lines,
512 KB), so one "line" often holds a whole page function.

Legend: VERIFIED = read the code at the cited path:line. INFERRED = deduced, not read in full.

(Sections are being filled in incrementally; see STATUS at the end.)

## 1. Files and what each does

| File | Size / lines | Role |
|---|---|---|
| `static/index.html` | 2.2 KB, 47 lines | Shell: header bar (logo, world selector `#worldSel`, `+ Save` `#newWorldBtn`, `Saves` `#savesBtn`, book selector `#pfSel`, `+ Book` `#newPfBtn`, ticker search `#tickerQ`/`#tickerDD`, badges `#regimeBadge` `#realBadge` `#windowPill` `#nextUpdate`, sim date `#simDate`, and the three ADVANCE buttons `data-adv="1"` / `"5"` / `"21"` labelled "Advance 1D", "1W", "1M"), then `#banner`, `#nav` (sidebar), `#main`, `#modalRoot`, `#toastRoot`. Loads glossary.js, coverage.js, app.js in that order. VERIFIED index.html:21-46 |
| `static/app.js` | 512 KB, 2,005 lines | The whole SPA in one IIFE (app.js:3). Vanilla JS, hash routing, canvas charts. Header comment claims "nothing is computed for display here except formatting" (app.js:1-2) -- NOT TRUE, see 6.3. VERIFIED |
| `static/glossary.js` | 63 KB, 253 lines | `window.FINSIM_GLOSSARY = {terms, categories}` read by `glossary()` (app.js:21) and the Glossary page. |
| `static/coverage.js` | 16 KB, 56 lines | Data for the "WHAT YOU CAN TRADE" page (`pages.coverage`, app.js:946). |
| `static/style.css` | 19 KB, 239 lines | All styling (dark default, `[data-theme=light]`). |
| `static/manifest.webmanifest`, `icon-*.png` | | PWA install metadata. |

## 2. App shell, global state, helpers (VERIFIED unless marked)

- Global `state` (app.js:5): `{ worlds, world, worldId (localStorage 'finsim.world'), pfId (localStorage 'finsim.pf'), route, period }`. Later keys added ad hoc: `state.lastRoute`, `state.renderToken`, `state.liveTimer` (app.js:363-365), `state.showDone` (app.js:470), `state.securities` (read at app.js:428).
- Preferences `prefs` (app.js:9-17): `PREF_DEFAULTS = { mode, home:'#/news', options{under,tenor,qty,type,tif,tab}, otc{product,tenor_years,notional:10000000}, trading{qty:10000,bond_qty:1000000,fut_qty:10,type,tif,ccy:'USD',exec:'LIVE'}, calc{key:'PROTECTIVE_PUT',units,tenor}, market{tab,sort}, theme:'dark', charts{period:'3M'} }`; stored ONLY in browser `localStorage['finsim.prefs']` (app.js:16-17). NOTE: `otc.notional: 10000000` is a client default for the OTC ticket, not a Treasury amount (see 6.2).
- `TENOR_DAYS` + `tenorDate(tenor, from)` (app.js:18-19): computes an expiry date from `state.world.current_date` -- tied to the simulated date.
- `gl(term, label)` (app.js:23): glossary link `<a class="gl" href="#/glossary/<slug>">`. `slug()` app.js:22.
- `fmt` (app.js:29-37): money, m0, px, qty, pct, bps, signed (pos/neg spans), big (K/M/B). `esc` app.js:38. `statusBadge` app.js:39.
- `api(path, opts, retried)` (app.js:47-59): single fetch wrapper to `/api` + path, JSON body, header `X-FinSim-Key` from `keyStore` (localStorage 'finsim.key' or `?key=`), 401 -> `window.prompt` once. Throws `Error(j.error)` on non-2xx.
- `post(path, body)` top-level (app.js:500) = `api(path,{method:'POST',body})`. A second, page-local `post(path, body, ok)` at app.js:1141 prepends `P()` (My Desk tab).
- `W()` = `/worlds/{worldId}` (app.js:63). `P()` = `${W()}/portfolios/{pfId}`; with ALL BOOKS selected it silently uses `bookForAll()` = the TREASURY book, else the first book (app.js:65-66). `allNote()` explains this (app.js:67).
- Charts: `setupCanvas`, `axes`, `lineChart`, `candleChart`, `barChart` (app.js:70-100); `candleChartPro` (app.js:113-137) COMPUTES moving averages MA20/MA50 client-side (app.js:122) and the hover % change (app.js:128).
- Page furniture: `tabsHtml/prependTabs/routeTab` (app.js:103-105) -- the tab bar used by every multi-tab page; `filterHtml/bindPageFilter` (app.js:107-111) row filter.
- `tile(k, v, s, click)` (app.js:393) -- the KPI tile component. `ccyTable(rows, kind)` (app.js:388-392) cash-by-currency table.
- `liveOn()` (app.js:396) = `state.world.live.available`. `liveQuotes(ids)` -> GET `W()/live?ids=` (app.js:402), which ALSO "works the resting live book" and toasts fills via `reportSweep` (app.js:405-406). `liveFill(ids, asofSel)` (app.js:404) fills every `[data-live]` cell and REFRESHES EVERY 60 s (`setInterval(fill, 60000)`, stored in `state.liveTimer`, cleared on each render app.js:365).
- `qu()` quote-update schedule (app.js:407), `execLabel(ex)` (app.js:409) text for LIVE vs NEXT_UPDATE vs "next open".
- `dayLabel()` (app.js:398): REAL_TIME clock -> "books at the <weekday> <date> close"; SANDBOX -> the save's day.
- `lockInstructions()` (app.js:380-384) + `LOCK_BTNS` (app.js:379): when `world.trading_window.open` is false, disables every ticket submit button: `#tGo, #qlGo, #ocGo, #stGo, #ccGo, #ofGo, #fxGo, #fwGo, #spGo, .pcGo, .pcSellGo, #bwGo, #rpQuote, #rpOpen, [data-exec]` and inserts a `.lockbar`.
- `progress` top bar (app.js:375-377); `toast` (app.js:60); `modal`/`closeModal` (app.js:61-62).

### Sidebar / navigation (VERIFIED app.js:181-186)
`NAV` (app.js:181), rendered by `renderNav()` (app.js:186) as `<div class="section">Modules</div>` + `<a href class=active>`:
1 `#/news` DAILY NEWS; 2 `#/overall` ALL BOOKS; 3 `#/portfolio` PORTFOLIO; 4 `#/positions` OPEN POSITIONS; 5 `#/live` LIVE TRADES; 6 `#/groupings` GROUPINGS; 7 `#/market` MARKET PLACE; 8 `#/options` OPTIONS; 9 `#/futures` FUTURES; 10 `#/otc` OTC DERIVATIVES; 11 `#/lending` SECURITY & CREDIT LENDING; 12 `#/banking` INVESTMENT BANKING (only when `madeUp()` = `world.made_up_desks !== false`, app.js:355); 13 `#/risk` RISK; 14 `#/accounting` ACCOUNTING; 15 `#/calc` CALCULATIONS; 16 `#/settings` SETTINGS; 17 `#/glossary` GLOSSARY; 18 `#/coverage` WHAT YOU CAN TRADE.
- `ALIAS` (app.js:183) old routes -> new: home->#/news, dashboard->#/portfolio, book->#/groupings, trades->#/positions, trading/markets->#/market, commodities->#/market/commodities, fixed-income->#/market/bonds, seclending->#/lending, repo->#/lending/repo, collateral->#/lending/collateral, settlements->#/accounting/settlements, treasury->#/market/fx, macro->#/news/macro, desk->#/portfolio/desk, career->#/portfolio/career, audit->#/accounting/audit. When pages are removed, `ALIAS` must be re-pointed (book->groupings and trades->positions both target pages slated for removal).
- `NAV_OF` (app.js:185): detail pages highlight a parent: security->#/market, commodity->#/market, position->#/positions (page to be removed), strategy->#/options (page to be removed).
- `render()` (app.js:356-373): parses `#/<page>/<arg>`, applies ALIAS, `renderNav()`, requires `worldId && pfId`; with `pfId==='ALL'` only `PF_FREE = ['overall','positions','news','market','glossary','coverage','settings','security','commodity']` are allowed, else redirect to `#/overall` (app.js:360-361); dispatches `pages[page] || pages.news`; a render token guards races; wraps bare tables in `.scroll`; calls `lockInstructions()` and `bindTypeaheads()`.
- Header ticker search (app.js:281-323): `tickerIndex.load()` -> GET `W()/securities` + `W()/commodities` (app.js:287); adds synthetic SPX/NDX/RUT index-option entries (app.js:290). Enter -> `quickLook(id)`; dropdown links to `#/security/<id>`, `#/groupings/<id>`, `#/options/<id>`, `#/futures`, `#/commodity/<code>` (app.js:304-308). '/' focuses it (app.js:322).
- `attachTypeahead(inp)` (app.js:328-352) for `input[data-ta]` in tickets.

### World (save) / book selection, the ADVANCE flow, save/load UI (VERIFIED)
- Boot: `loadWorlds()` (app.js:187-193) GET `/worlds`; none -> "Create a world to begin" + `openNewWorld()`.
- `loadWorld()` (app.js:194-215): GET `W()` -> `state.world`; fills `#pfSel` with `ALL BOOKS` + each portfolio; `#simDate` = `dayLabel(true)`; regime badge; REAL_TIME clock hides the `[data-adv]` buttons (app.js:201) and shows "CAREER update <time>", else "SANDBOX ... advance days yourself" (app.js:202); REAL market badge (app.js:205-207) disables advance buttons while `real_market.waiting`; banner for `replay_errors` / `integrity.problems` / migration (app.js:208-213). World payload fields read: `clock{mode,weekday,next_update,timezone,update_time}`, `current_date`, `regime{label,description,name}`, `real_market{waiting,next_session,latest_close}`, `market_source`, `replay_errors`, `integrity.problems`, `migration`, `portfolios[]`, `treasury{book_id,spare_usd}`, `trading_window`, `live.available`, `quote_updates`, `made_up_desks`, `engine_version`.
- ADVANCE: `advance(days)` (app.js:224-228) -> POST `W()/advance {days}`, toast "Closed N day(s) -> now <date>", then `loadWorld()`. Bound to every `[data-adv]` button (app.js:229) = index.html "Advance 1D / 1W / 1M" (index.html:35-37).
- `windowPill` (app.js:217-223): REAL_TIME saves only; shows "SIMULATED PRICES - no live quotes" when not live, "LIVE - quotes <=15 min delayed" + live SPY (via `liveQuotes(['SPY'])`), or the session-lock countdown; redraws every 60 s and calls `loadWorld()` when the window flips.
- New save modal `openNewWorld()` (app.js:236-269): GET `/jobs`; fields Job, Save name, Mode (`REAL_TIME` "Career" vs `SANDBOX` "a seeded world you advance yourself"), Timezone, Update time (17:00), Session lock, Seed, Start date (sandbox only), Treasury checkbox (default CHECKED: "the save opens with one book, Treasury, holding all the capital"), Starting capital (blank = "the job's standard"), Realism, Initial regime, Market (`SIMULATED` vs `REAL` replay; sandbox only), Scenario (NONE / CRISIS). POST `/worlds` body `{treasury, name, seed, start_date, capital, portfolio_name, job, clock_mode, timezone, update_time, realism, initial_regime, scenario, market_source, lock_session}` (app.js:266). Header "+ Save" = NEW world (index.html:24), not "save".
- New book modal `openNewPortfolio()` (app.js:270-278): Name, Job (SANDBOX / PORTFOLIO_MANAGER / GLOBAL_MACRO / COMMODITY_TRADER / FIXED_INCOME_PM), "make this book the save's Treasury" checkbox (only if `world.treasury && !world.treasury.book_id`), "draw it from the Treasury" checkbox (pre-ticked only if a Treasury book exists AND `spare_usd > 0`; "unticked = fresh money into the book"), Starting capital (blank = the job's standard), Realism. POST `W()/portfolios {from_treasury, portfolio_type:'TREASURY'|'PERSONAL', name, job, capital, realism}` (app.js:277).
- Saves manager `openSaves()` (app.js:977): GET `/worlds`, DELETE `/worlds/<id>` (app.js:986).
- There is NO explicit save / load / autosave button or call in the UI: grep for `autosave|autoSave|auto_save` in app.js finds nothing; the only `localStorage` writes are prefs, key, current world id and current book id (app.js:17, 45, 195, 199, 231, 1907). Persistence is entirely server-side, triggered by each mutating API call (INFERRED; server not in my area).

## 3. Pages (route, line range, API calls, client-side computations, links)

Notation: GET unless marked. "COMPUTES" = arithmetic done in the browser on server figures (duplicate-computation candidates for the single accounting module). Glossary links are `gl(...)` calls.

### 3.1 DAILY NEWS `#/news` -- app.js:1369-1376 (dispatcher), tabs `NEWS_TABS` = briefing / feed / macro (app.js:1370) -- KEEP
- Tab BRIEFING `pageBriefing` app.js:413-450: GET `P()/briefing` (414); earlier briefings modal GET `P()/briefing?date=` (448). Shows tiles Portfolio NAV / Today / P&L buckets (`BUCKET_LABEL` app.js:412), Major P&L table, position movers, market, news, attention, financing (incl. excess liquidity, app.js:432), collateral, short book, derivatives Greeks (435), OTC (436), risk (437), macro (438), corporate actions (439), FX balances (440), "Your instructions" (441-446).
  - COMPUTES: VaR as % of NAV `b.risk.var99 / b.risk.nav` (app.js:437). Everything else displayed as served.
  - Text tied to day advance: "Sandbox: instructions you enter now execute when you advance the day" (app.js:420).
  - Links: `#/position/<id>` (426, 434 -- target page to be removed), `#/commodity/<code>`, `#/security/<id>` (428, 438), attention `a.link` (server-supplied, 431), `#/options` "Options desk" (435), `#/otc`, `#/otc/counterparties` (436), `#/risk` (437), `#/news/macro` (438), `#/accounting/settlements` "Elections" (439), `#/market`, `#/portfolio`, `#/market/commodities` (446). No `gl()` links.
- Tab NEWS FEED `pageNewsFeed` app.js:1322: GET `W()/news`, `W()/corporate-actions` (1323). Filter box (1374).
- Tab MACRO & CALENDAR `pageMacro` app.js:1029-1048: GET `W()/macro` (1031). Detail in 3.23.
- Subtitle uses `state.world.current_date` and regime (app.js:1374).

### 3.2 PORTFOLIO `#/portfolio` -- app.js:1378-1443, tabs `PF_TABS` = overview / pnl / performance / career / desk (app.js:1379) -- KEEP
- Dispatcher app.js:1380-1385. On OVERVIEW it injects a Treasury panel: if this book IS the Treasury -> "can spare ..."; else a "draw cash into this book" form -> POST `W()/treasury/allocate {portfolio_id, currency, amount}` then `loadWorld()` (app.js:1384).
- OVERVIEW `pageOverview` app.js:1386-1412: GET `P()/dashboard`. Tiles NAV (click -> `navExplain` modal, GET `P()/nav-explain` app.js:451-459), Today/MTD/YTD, Since inception, Cash, Gross/net, Long/short, Financing, Collateral posted, Margin calls, Largest risk; cash-by-currency `ccyTable`; NAV chart from `d.nav_history`; next-20-sessions cash flows; working orders `ordersTable`; exposure groups; `positionsTable(d.positions, true)`.
  - COMPUTES: exposure % of NAV `v / d.nav` (1388); alpha `return_since_inception - benchmark.return` (1393); Cash (all currencies) = SUM of `cash_accounts[].base_value` (1394, same as `ccyTable` total 390); net % of NAV `net_exposure / nav` (1395); reconciliation check `|ledger_nav - nav| < 0.005` (1408); `navExplain`: "Change to explain" = `nav - prev_nav - capital_flows` (454) and SUM of component changes (456); `positionsTable` Div/Int = `dividend_income + interest_income` (465).
  - Links: `#/market/fx`, `#/overall` (1402, 1384), `#/live` (1405), `#/positions`, `#/groupings` (1407 -- both to be removed), rows -> `#/position/<id>` (465, 467).
- P&L `pagePnl` app.js:1413-1423: GET `P()/dashboard` + `P()/pnl-explain`. COMPUTES Income tile = `income.dividends + income.interest` (1416). Shows `x.reconciles` flag from the server. Rows -> `#/position/<id>`; `data-event` -> event modal.
- PERFORMANCE `pagePerformance` app.js:1424-1443: GET `P()/dashboard`, `W()/yield-curve`, benchmark `W()/securities/<bid>?period=MAX` (1427). ALL statistics are COMPUTED client-side: `perfStats()` (app.js:160-177: daily returns, mean, vol x sqrt(252), annualised return, Sharpe, Sortino, max drawdown, hit rate, best/worst, benchmark total, beta, correlation, CAPM alpha), benchmark series forward-fill (1429), drawdown series (1431), monthly returns NAV-to-NAV (1432, 1437), growth-of-100 (1439-1440). Hint admits "Returns are NAV to NAV (capital flows distort them in the month they land)" (1438) -> a Treasury draw into a book shows up as performance (see 6.2). `gl()` links: Sharpe ratio, Maximum drawdown, Beta, Alpha (1435).
- CAREER `pageCareer` app.js:1089: GET `P()/career` (1090). MY DESK `pageDesk` app.js:1105: GET `P()/desk` (1106); page-local `post(path, body, ok)` (1141) -> POST `P()/desk/quote` (x2), `/desk/lend`, `/desk/recall`, `/desk/decide` (x2), `/desk/limit`, `/desk/reduce` (1142-1149). Detail in 3.23.

### 3.3 OPEN POSITIONS `#/positions` -- app.js:1445-1564 -- TO BE REMOVED as a page
- Modes OPEN NOW / HISTORY via `state.posMode` (1447, 1551). Filter `state.posQ` (1449, 1562).
- API (1451, 13 parallel calls): `P()/positions`, `/options`, `/otc`, `/seclending`, `/repo`, `/financing`, `/private-credit`, `/commodity-desk`, `/trades` (HISTORY only), `/orders` (HISTORY only), `/private-equity`, `/settlements`, `/ccy-loans`; live quotes `W()/live?ids=` (1455); ALL BOOKS also `W()/overall` (1530).
- Sections (1459-1521): Pending, Securities, Futures, Listed options, OTC, Securities borrowed, Repo, Currency loans, FX forwards, FX spot, Private credit, Private equity, Commodity inventory; HISTORY adds Fills, Instructions, Option strategies; ALL BOOKS adds "Treasury balance sheet" and "Treasury draws by book" (1531-1537). Tables via `XL.table/tsv/csv` (app.js:885), copy/CSV buttons (1558-1561).
- COMPUTES: indicative live P&L `(live - mark) * qty * unit` per row (1458) and total (1552); Income `dividend_income + interest_income` (1462); OTC net P&L `mtm + realized` (1471); remaining qty `quantity - filled_quantity` (1512); per-book `NAV - net drawn` (1537); Market value tile = SUM securities MV + options MV + OPEN OTC MTM + OPEN private-credit MV (1546) -- excludes futures, inventory, PE, repo, borrowed stock: a different "market value" definition from the server's; Today = SUM `positions[].day_pnl` (1547); short book = SUM |MV| of negative-qty positions (1552); item counts (1548).
- ALL BOOKS mode loops over every book and TEMPORARILY OVERWRITES `state.pfId` inside an `await` loop (app.js:1528) -- any concurrent `P()` call (60 s live timers, windowPill, a click) during that loop hits the wrong book (hazard INFERRED from VERIFIED code).
- Links: rows -> `#/position/<id>`, `#/otc/blotter`, `#/lending`, `#/lending/repo`, `#/market/fx`, `#/lending/private`, `#/lending/pe`, `#/security/<id>` (1460-1493). No `gl()`.

### 3.4 LIVE TRADES `#/live` -- app.js:1566-1602 -- TO BE REMOVED as a page
- API (1569): `P()/orders`, `/trades`, `/options`, `/otc`, `/settlements`, `/collateral`, `/commodity-desk`; POST `W()/live/work` (sweep resting live orders, 1597; auto every 60 s when live orders exist, 1600); POST `W()/track-real` "Switch this save to the real market" (1599).
- Everything is filtered to `today = state.world.current_date` (1568): Filled today = `trades.filter(t => t.trade_date === today)` (1572), Also entered today (1571), OTC today (1574), settlements due (1575).
- Hosts a "Quick ticket" = the shared `ticketHtml()` / `bindTicket()` (1591, 1601).
- COMPUTES: Filled-today gross = SUM |gross_amount| (1583); calls due SUM (1583); best RFQ cost vs mid = MIN (1592).
- Text tied to day advance: "instructions execute when you advance the day" (1582).
- Links: `#/options/<under>`, `#/futures` (1586), `#/otc/blotter` (1589), `#/otc/rfq` (1592), `#/lending/collateral` (1593).

### 3.5 ACCOUNTING `#/accounting` -- app.js:1940-1946 (dispatcher), tabs `ACC_TABS` = ledger / settlements / cash / audit (app.js:1941) -- KEEP
- GENERAL LEDGER `pageLedger` app.js:1333-1345: GET `P()/balance-sheet` + `P()/ledger?limit=300[&account=][&security_id=]` (1335). Shows the SERVER's trial-balance flag and `A - L - E - NI = bs.check` (1337), balance-sheet sections ASSET/LIABILITY/EQUITY/INCOME/EXPENSE, totals, NAV, net income (1338), trial balance (1339), journal `entriesTable` (1342). Rows link `#/accounting/ledger?account=<code>` (1336); filter -> same (1343). No client arithmetic. GOOD candidate to become the single Accounting page core.
- SETTLEMENTS & CUSTODY `pageSettlements` app.js:1296-1308: GET `P()/settlements`, `/custody`, `/corporate-actions` (1297); POST `P()/elections {ca_id, quantity}` via `prompt()` (1306). Uses `state.world.settlement_cycles` (1300). Rows -> `#/position/<id>` (1304), `#/security/<id>`. COMPUTES only counts.
- CASH & TREASURY `pageCash` app.js:1310-1320: GET `P()/cash` + `P()/financing` (1311). Tiles per currency (settled, projected, accrued int), prime-broker loan + excess liquidity, repo balance; 15-day projected liquidity chart; cash movements. No arithmetic. Link `#/market/fx` (1312).
- AUDIT TRAIL `pageAudit` app.js:1347-1356: GET `W()/events?limit=100&offset=&type=&q=` (1349); paging links `#/accounting/audit?...` (1352).
- Shared tables: `entriesTable` (1241-1244), `settlementsTable` (1292-1295), `tradesTable` (1237-1240), `ordersTable` (1228-1236, hides CANCELLED/EXPIRED/REJECTED unless `state.showDone`; cancel button -> DELETE `P()/orders/<id>` via `bindRows` app.js:470). Modals: `tradeModal` GET `P()/trades/<id>` (1246-1259, lifecycle EXECUTED->...->SETTLED `LIFECYCLE` 1245); `eventModal` GET `W()/events/<id>` (1260-1266).
- DUPLICATE: `ACCT` (app.js:1289) is a hard-coded client copy of the chart of accounts (codes 1010..5800 -> names) used by `accountName()` (1290) on the position page (1276), while the ledger page uses the server's `led.chart` (1341). Two sources for account names.

### 3.6 ALL BOOKS `#/overall` -- app.js:1871-1914 -- (not in the keep list; the Treasury controls live here)
- GET `W()/overall` (1872). Tiles Total NAV (`t.nav_with_treasury ?? t.nav`), Treasury (`t.treasury`), Day P&L, Since inception, Cash (base), Exposure, Positions (1877). Treasury panel with `ccyTable(o.treasury.currencies,'treasury')` (1879) and ONE amount box `#trAmt` (step 1,000,000) shared by three buttons: "draw into book" -> POST `W()/treasury/allocate {amount, currency, portfolio_id}`, "return to treasury" -> POST `W()/treasury/return`, "add capital to treasury" -> POST `W()/treasury/fund {amount, currency}` after `confirm('Add fresh capital to the treasury?')` (1908-1911; the confirm does NOT show the amount). Book table (1887-1889) with delete -> DELETE `W()/portfolios/<id>` (1912). Treasury balance sheet (1890-1892), Treasury draws by book (1893-1894), Treasury log (1895), positions across books (1897-1899), day P&L by bucket (1900), cash by currency (1901).
- COMPUTES: total leverage `gross_exposure / nav` (1889); SUM of working orders and margin calls across books (1889); balance-sheet group totals `sum('treasury')`, `sum('all_books')` excluding the 'Net asset value' line (1891); per-book `nav - net drawn` (1894).
- INCONSISTENCY (VERIFIED): the Total NAV tile shows `t.nav_with_treasury` (1877) but the books-table Total row shows `t.nav` (1889) -- two different "total NAV" figures on one page.
- Clicking a book row sets `state.pfId` + localStorage and goes to `#/portfolio` (1907). Link `#/positions` "Open positions & blotter (all books)" (1896 -- page to be removed).

### 3.7 INVESTMENT BANKING `#/banking` -- app.js:1916-1938 -- (not in keep list; made-up deal flow, hidden for real-market saves via `madeUp()`)
- GET `P()/investment-banking` (1919); POST `/investment-banking/pitch` (1935), `/investment-banking/decide` (1936). COMPUTES default fee mid `(fee_norm[0]+fee_norm[1])/2` and IPO promise `public_multiple*0.95` (1921-1922). `gl('Investment banking')` (1924).

### 3.8 CALCULATIONS `#/calc` -- app.js:1948-1986 -- TO BE REMOVED as a page
- The strategy templates are NOT in the front end: they come from GET `P()/playbook` (`cat.strategies`, `cat.groups`) (1951, 1956-1963); the front end only renders them (so "38 templates" is a server count, INFERRED from this). State in `state.calc` (1953) seeded from `prefs.calc`; `?under=` / `?key=` query (1954-1955).
- Flow: "3 - Preview" -> POST `P()/playbook/preview {key, params{underlying, other, units, tenor_months, strikes}}` (1984); "4 - Execute the package" -> `confirm()` then POST `P()/playbook/execute` (1985) and jumps to `#/live` (page to be removed).
- COMPUTES: Margin tile `incremental_option_margin + futures_margin` (1976). Payoff chart from server `pv.payoff` (1979).
- Day-advance dependency: hint "stock and futures as market instructions for the next open" (1970).
- `gl('Strategy playbook')` (1961). Hosts the 4th order-entry path (package ticket, `#ccPrev/#ccGo`).

### 3.9 SECURITY detail `#/security/<id>` -- app.js:1152-1175 (no sidebar entry; highlights MARKET PLACE)
- GET `W()/securities/<id>?period=<state.period>` (1153). Option contracts get their own layout with Greeks tiles from the server quote (1154-1156) and "Today's synthetic session" OHLC (1156 -- simulation wording). Others: tiles, candle chart, futures curve, bond cash flows, fundamentals, corporate actions, news (1157-1170). Period buttons 1D..MAX (1164, 1173).
- Hosts the MAIN ticket `ticketHtml(sec.id, isBond, isFut)` (1156, 1165) + an order book table.
- Hard-coded figure: tile 'Borrow' = 'n/a', 'securities lending: Phase 2' (1162).
- Links: `#/options/<under>/<expiry>`, `#/security/<underlying>` (1154), `#/commodity/<code>` (1159).

### 3.10 POSITION detail `#/position/<id>` -- app.js:1269-1287 (highlights OPEN POSITIONS, which is to be removed)
- GET `P()/positions/<id>` (1270). Tiles per kind (option / short / future / long), relationships, FIFO lots, ledger balances for the security, dividends, custody movements, security loans, pledges, collateral movements, trades, settlements, daily P&L attribution, ledger entries.
- HARD-CODED FIGURE (VERIFIED): 'Financing cost' tile is `fmt.money(0)` for every long position (1273). 'Beta / delta' falls back to `p.risk.delta ?? 1` (1273).
- Many inbound links point here (`data-href="#/position/<id>"` at 426, 434, 465, 467, 1304, 1419, 1460-1465 ...): when Open Positions goes, this detail route needs a new parent in `NAV_OF` (app.js:185).

### 3.11 MARKET PLACE `#/market` -- app.js:1603-1767, tabs `MK_TABS` = equities / bonds / fx / commodities / crypto / indices / housing (app.js:1604) -- KEEP
- Common: GET `W()/securities` (1610). Layout `.grid-mkt` = left tables + right panel (1759). The right panel is the MAIN ticket `ticketHtml()` (1759, `bindTicket()` 1761) except on the FX tab, which replaces it with FX tickets (1683-1685). Row click (`data-pick`) loads the id + default qty into `#tSec/#tQty` (1763); `data-ql` opens `quickLook` (1764). Header shows `state.world.current_date`, regime, policy rate (1757).
- SIMULATION CONTROL: sandbox-only "Force regime from next update" -> POST `W()/force-regime {regime}` (1756-1762). Goes away with the simulated market.
- EQUITIES (1613-1628): filters/sort in `state.mk` (sort `vol` = `volume * last`, 1618). COMPUTES breadth up/down counts and % advancing (1620, 1623). Live column refreshed every 60 s via `liveQuotes` (1628). Shows `state.world.vol_index` "implied vol of the index" (1623 -- model figure).
- BONDS (1629-1654): GET `W()/yield-curve` (1630). COMPUTES 2s10s `(rates[7]-rates[3])*1e4` (1631), per-tenor day change `(r - prev_rates[i])*1e4` (1632), EUR/GBP/JPY 10Y vs USD and BTP-Bund / OAT-Bund spreads (1638). Hard-coded tenor indexes 3=2Y, 7=10Y, 9=30Y. Regime history badges (1648, simulated-regime concept). `liveFill` for bonds/MBS/structured/global govts (1650).
- FX (1655-1700): GET `W()/fx`, `P()/financing`, `P()/cash`, `P()/ccy-loans` (1656). Tickets: FX spot `#fxGo` -> POST `P()/fx/spot` (1698), FX forward `#fwGo` -> POST `P()/fx/forward` (1699; default maturity = `state.world.current_date + 90 days`, 1684), currency BORROW `#clGo` -> POST `P()/ccy-loans {currency, amount, term_days, tag}` (1690), repay `.clRepay` -> POST `P()/ccy-loans/<id>/repay` via `prompt()` (1691), CARRY TRADE `#ctGo` = TWO sequential calls POST `/ccy-loans` then POST `/fx/spot` (1689) -- NOT ATOMIC: if the spot leg fails the loan stays open. COMPUTES: gross foreign cash USD-equivalent SUM (1663); forward annualised `(F.rate/quote - 1)/yrs` (1669); spot estimate `mid = sb/ss`, `a*x` (1696) -- a client-side FX conversion duplicating server pricing; carry `deposit_rate - borrow_rate` (1687). Model-only content: "Today's drivers" carry/equity beta/reversion/flow decomposition (1660, 1668) is the simulated FX model.
  - NOTE for the Credit page: the existing cash-borrowing UI is HERE ("Borrow currencies", 1671-1678), plus margin draw/repay on the lending Collateral tab (see 3.13).
- CRYPTO (1701-1713), HOUSING (1714-1730, GET `W()/housing`), INDICES & VOL (1731-1744): COMPUTES cash index levels as `ETF last x factor` (SPX=SPY x10, NDX=QQQ x41.3, RUT=IWM x10.35, DJIA=DIA x100) (1732, 1738) -- a client-side synthetic price; VX contango/backwardation label (1742).
- COMMODITIES (1745-1754): GET `W()/commodities` (1746); group tiles COMPUTE up/down counts and best mover (1749); model columns inventory sigma / demand sigma / supply shock / convenience yield (1751) are simulated-market fields.
- Links: `#/security/<id>`, `#/options/<id>` (rowTools 1607), `#/commodity/<code>` (1752), `#/futures` (1753), `#/otc/rfq` (1685, 1707, 1742, 1753), `#/market/fx`, `#/market/indices` (1663, 1743). No `gl()`.

### 3.12 FUTURES `#/futures` -- app.js:1769-1808 -- TO BE REMOVED as a page
- API: GET `W()/securities`, `P()/commodity-desk`, `P()/positions`, `W()/commodities` (1771); POST `P()/spreads {near, far, side, quantity, limit_points}` calendar-spread ticket `#spGo` (1803); POST `P()/physical-delivery {on}` toggle `#phGo` (1804).
- Hosts the 3rd instance of the MAIN ticket `ticketHtml('', false, true)` "Futures ticket" (1790, `bindTicket` 1801); "close" buttons prefill side/qty (1785, 1805).
- COMPUTES: total initial margin SUM `risk.initial_margin`, today's VM SUM `day_pnl` (1776); contract count SUM |qty| (1783); inventory MV SUM (1783).
- Day-advance wording: calendar spread "all-or-none at the open" (1791).
- Links: `#/position/<id>` (1785), `#/commodity/<code>`, `#/options/<x>` options on futures (1787), `#/security/<id>` (1788, 1797). `gl('Futures contract')` (1782).

### 3.13 SECURITY & CREDIT LENDING `#/lending` -- app.js:1809-1818 (dispatcher), tabs `LEND_TABS_ALL` = seclending / repo / collateral / private / pe (1810; 'pe' only when `madeUp()`, 1813) -- becomes the CREDIT page (INFERRED from the directive)
- SECURITIES LENDING `pageSeclending` app.js:508-529 (see 3.15 below for detail).
- REPO `pageRepo` app.js:1050: GET `P()/repo` (1051), POST `W()/repo-quote` (1060), POST `P()/repo` (1060), POST `P()/repo/<id>/<act>` (1069). LOCK_BTNS `#rpQuote, #rpOpen`.
- COLLATERAL & PRIME `pageCollateral` app.js:1073: GET `P()/collateral` (1074); prime-broker MARGIN LOAN draw/repay: POST `P()/margin/draw` (1084), POST `P()/margin/repay` (1085) -- existing cash-borrowing controls. COMPUTES calls-due SUM `c.amount` (1076).
- PRIVATE CREDIT `pagePrivateCredit` app.js:1853-1868: GET `P()/private-credit` (1854); POST `/private-credit/commit {deal_id, amount}` (`.pcGo`, 1865), POST `/private-credit/sell {loan_id, amount}` after confirm (`.pcSellGo`, 1866). COMPUTES MV as % of par `market_value / open_par` and indicative coupon `policy_rate + weighted_spread_bps/1e4` (1858); prices x100 (1862). Simulated pipeline ("this month's deals", ratings migrate, defaults) -- invented deal flow. `gl('Private credit')` (1857).
- PRIVATE EQUITY `pagePrivateEquity` app.js:1822-1852 (made-up desks only): GET `P()/private-equity` (1823); POST `/private-equity/diligence` (1840), `/structure` (preview, 1841), `/bid` after confirm (1843), `/initiative` (1844), `/recap` (1845), `/cure` (1846), `/exit` (1847), `/selldown` (1848). COMPUTES paid-in = SUM of negative flows (1829), max lender leverage MAX (1830). Lock-up check `state.world.current_date < c.listed.lockup_until` (1835 -- sim date). `gl('Private equity')` (1830).

### 3.14 OPTIONS `#/options[/<under>[/<expiry>]]` -- app.js:529-639 -- TO BE REMOVED as a page (folds into Derivatives)
- Tabs (state.optTab, not in the URL): chain / strategies / positions / expirations / exercise / vol / margin (549). Preset vs remember mode via `prefs.mode` + `remembered.options` (538-547); default expiry from `tenorDate(prefs.options.tenor)` = sim date + tenor (541).
- API: GET `W()/options`, `P()/options` (543); GET `W()/options/<under>/chain?expiry=` (545); GET `W()/options/<under>/surface` (598) and AGAIN at 637 for the same vol tab (duplicate fetch, VERIFIED); option ticket `#ocGo` -> POST `P()/orders` (626) with NO server preview; strategy ticket: POST `P()/strategies/preview` (633), POST `P()/strategies` (634); GET `P()/strategies/<id>` (635); exercise POST `P()/options/exercise` via `prompt()` (636).
- COMPUTES: days to expiry from `state.world.current_date` (552); position per contract SUM (556); ATM row (564); option-ticket estimate premium `px * n * 100` and delta shares `delta * n * 100` (622) -- client duplicate of order preview, with a HARD-CODED x100 multiplier although the underlying list carries `contract_size` (554) and futures options / non-USD contracts differ; expiry "what happens" cash `intrinsic * |contracts| * 100` (604); exercise "extrinsic forgone" `extrinsic * contracts * 100` and "cash on exercise" `strike * contracts * 100` (607).
- Day-advance wording: MARKET "at the next open" (567); toast "executes at the next update" even in live mode (626); strategies "every leg fills at the next open or none does" (588, 634).
- Glossary links (many): Expiry (563), Delta, Implied volatility (IV) (565), Delta, Dollar delta, Gamma, Vega, Theta, Options margin (569, 592), Rho (592), strategy names via `glossKey` map (578, 583), Net limit (588), Realized volatility, IV rank, Skew (600), Expiry, Exercise, Assignment (604), Extrinsic value (607), Assignment (608), Options margin, Excess liquidity, Margin call, Forced liquidation (611-613), Listed option (615). Links: `#/position/<id>` (594), `#/calc?under=` (615).
- Option-id parser `optName` (531) is client-side.

### 3.15 Securities lending tab (part of 3.13) `pageSeclending` app.js:508-527
- GET `P()/seclending` (509). Short-sale workflow is three separate tickets: Request locate `#lcGo` -> POST `P()/locates {security_id, quantity}` (523); Borrow against a locate `#bwGo` -> POST `P()/loans {locate_id, quantity, collateral_type}` (524); Return -> POST `P()/loans/<id>/return {quantity}` via `prompt()` (525). The short itself is a separate SELL on the main ticket (hint 518). Nothing in the main ticket checks for a borrow before a SELL (client side); the server may (not in my area).
- COMPUTES: collateral posted SUM (cash amount or collateral value) and accrued fees SUM (513).
- Day-advance wording: "A locate ... expires after the next session"; "returned automatically after five sessions" (518).
- Links: `#/position/<id>` (520).

### 3.16 OTC DERIVATIVES `#/otc[/<tab>]` -- app.js:640-813 -- TO BE REMOVED as a page (folds into Derivatives)
- Tabs (in URL): rfq / blotter / risk / counterparties / csa / upcoming (755).
- Product forms are CLIENT-SIDE config: `OTC_FIELDS` (642-666, + 5 products added at 1989-1995) and help text `OTC_HELP` (668-692, + 1996-2002): 25+ products. Default notional 10,000,000 for IRS/FRA/CAP/FLOOR/SWAPTION/XCCY/CDS/OIS/INFLATION_SWAP/NDF (643-654), overridden by `prefs.otc.notional` (= 10,000,000) for `notional` and `usd_notional` (761). XCCY "the dollar principal exchanged at the start" (648) and FX_SWAP (655) move principal cash on day one (see 6.2).
- Formatters `fmtLevel`, `levelText`, `describeRequest` (693-746) and param reader `readOtcParams` (747) are front-end business logic tied to product semantics.
- API: GET `P()/otc`, `W()/otc/dealers`, `W()/securities` (752); RFQ: POST `P()/otc/rfq {product, params}` (804) -> quotes; deal: POST `P()/otc/rfq/<id>/execute {dealer}` (805); unwind: confirm + POST `P()/otc/<id>/terminate` (806); detail GET `P()/otc/<id>` (807). Sandbox-only simulation controls: POST `W()/default-counterparty` (809), POST `W()/credit-event` (810), shown when `clock.mode !== 'REAL_TIME'` (788).
- COMPUTES: best quote MIN cost vs mid (771); blotter Net P&L `mtm + realized` (777, same formula as Open Positions 1471); OTC MTM by bucket SUM (781).
- Session wording: "Quotes last this session" (769), "good for this session" (771).
- Glossary: product gloss (765), ISDA / CSA (769), Rate vol, Cross-currency basis (770). Links `#/otc/<tab>` (799), `#/otc/blotter` (783).

### 3.17 GROUPINGS `#/groupings[/<name>|/tags]` -- app.js:815-882 -- TO BE REMOVED as a page
- BY NAME (816-862): GET `P()/positions`, `/options`, `/otc`, `/seclending`, `/repo`, `/financing`, `W()/securities` (818). The whole page is a CLIENT-SIDE AGGREGATION ENGINE: builds groups per name (`g()` 821), futures mapped ES->SPY, ZN->UST-10Y (`futKey` 822), options via `INDEX_SRC` (184, 830), OTC by product terms (831), loans/repo/FX forwards (832-834), and COMPUTES per group MV, day, unrealized and item count (827-835) and page totals (852). Its "Market value" = securities MV + options MV + OPEN OTC MTM + FX-forward MTM (828-834) -- yet another MV definition (Open Positions excludes FX forwards and includes private credit, 1546). Income `dividend_income + interest_income` (844); OTC net `mtm + realized` (847).
- BY #TAG (865-882): GET `P()/tags` (866); COMPUTES total tagged P&L SUM and best/worst (877).
- Links: `#/groupings/<key>` (843, 859), `#/position/<id>` (844-846), `#/portfolio`, `#/options/<u>`, `#/futures`, `#/otc/blotter`, `#/lending`, `#/lending/repo`, `#/market/fx` (844-850), `#/security/<id>` (838, 855, 870). Inbound: header search "grouping" links (308), quickLook (149), Portfolio overview (1407), `ALIAS.book` (183), OTC field hint "Groupings -> By #tag" (667).
- `XL` spreadsheet helpers (885-892), `copyText`, `download` (893-894) are shared with Open Positions and ALL BOOKS.

### 3.18 SETTINGS `#/settings` -- app.js:896-943 -- KEEP
- GET `W()/options` (898), `P()/playbook` once (899). Panels: page-open mode + home page (built from `NAV`, 905), Options presets (909-914), OTC presets incl. notional default 10,000,000 (915-918), Trading ticket defaults incl. Execute LIVE/NEXT_UPDATE (919-921), Charts & theme (922), Ticket currency (923), Calculations presets (924), Market Place tab/sort (925). "Save settings" writes `prefs` to localStorage only (931-938); "Reset" (940).
- Server-side controls on this page: "Switch this save to the real market" -> POST `W()/track-real` (926, 941; REAL_TIME + simulated saves only); "This save's clock" (update time, timezone, session lock) -> POST `W()/clock` (927, 942). Both are tied to the day-update clock model.
- When pages are removed, the Options / OTC / Calculations / Market-tab panels and the `pfHome` list (from `NAV`) must change.

### 3.19 WHAT YOU CAN TRADE `#/coverage` -- app.js:945-959 (sidebar item 18; not in the keep list)
- Reads `window.FINSIM_COVERAGE` from coverage.js; COMPUTES built/partial/not-built counts (950). No API.

### 3.20 GLOSSARY `#/glossary[/<slug>]` -- app.js:961-974 -- KEEP
- Reads `window.FINSIM_GLOSSARY` (glossary.js); search `state.glossQ`, category `state.glossCat`; entries `id="gl-<slug>"`, deep link scrolls to it (970, 973); "See also" uses `gl()` (970). Every `gl(term)` elsewhere links here: if a term's slug is not in glossary.js the link lands on the page with no highlight (no check exists).

### 3.21 Saves manager (header "Saves" button) `openSaves` app.js:976-990
- GET `/worlds`; table of saves (name, id, created, events, format version); delete requires typing the name, then DELETE `/worlds/<id>` (983-986). Hint: "a snapshot of the whole database is taken every time the server starts" (980). No rename, no load/export/import, no explicit save.

### 3.22 RISK `#/risk[/<tab>]` -- app.js:991-1027 -- KEEP
- Tabs (in URL): overview / stress / liquidity / limits / history (995). GET `P()/risk` (994); custom stress POST `P()/risk/stress {equity, rates_bp, spreads_bp, vol_pts, commodity, fx}` (1025).
- COMPUTES: % of NAV `x / r.liquidity.nav` for VaR, ES and stress P&L (996, 1000, 1009, 1025); component VaR share `x / var99` (1004); per-row equity $ = SUM of `x.equity` values (1006).
- Session wording: limits "until the next close shows the book back within limits" (1017); cash ladder "next 20 sessions" (1015).
- Links: `#/risk/<tab>` (1021). No `gl()`.

### 3.23 Remaining tab bodies (VERIFIED, read in full)
- MACRO & CALENDAR `pageMacro` app.js:1029-1048: GET `W()/macro`. Tiles growth / CPI / unemployment / policy rate / next meeting / curve / credit; releases, earnings, issuer credit, rating changes, corporate events. Hint describes the seeded macro model ("seeded surprise", reaction function, 1045). Simulation-specific except when `m.source === 'REAL'` (1033-1036). Rows -> `#/security/<id>`.
- REPO `pageRepo` app.js:1050-1071: GET `P()/repo`; quote POST `W()/repo-quote` (1060) then "Execute" POST `P()/repo` (1060); row actions close / +collateral / +cash / reduce / substitute via `prompt()` -> POST `P()/repo/<id>/<act>` (1062-1069). Default face 10,000,000 (1057). COMPUTES displayed haircut `min(0.75, v * regime_multiplier)` (1058) -- client-side haircut rule.
- COLLATERAL & PRIME `pageCollateral` app.js:1073-1087: GET `P()/collateral`; prime-broker loan Draw / Repay -> POST `P()/margin/draw`, `/margin/repay {amount}` (1081, 1084-1085), no confirm. COMPUTES posted-collateral sums `cash_collateral_posted.X + securities_collateral_posted.X` (1076) and open calls SUM (1076). Shows excess liquidity from the server (1080).
- CAREER `pageCareer` app.js:1089-1102: GET `P()/career`; job, ladder, metrics (Sharpe, drawdown from the server -- a SECOND Sharpe/drawdown besides `perfStats`), limits, missions, scenario log, reviews, career log, breaches, margin calls. COMPUTES mission % (1096).
- MY DESK `pageDesk` app.js:1105-1150: GET `P()/desk`; panels investors / client requests / lending desk / corporate treasury / desk oversight, each only if present; POSTs at 1142-1149 (see 3.2). COMPUTES hit ratio `won/(won+lost)` (1116), pending-requests SUM (1131). Session wording "resolves next session", "new ones arrive each session" (1117).
- COMMODITY detail `#/commodity/<code>` `pages.commodity` app.js:473-497 (no sidebar entry; highlights MARKET PLACE via `NAV_OF`): GET `W()/commodities/<code>`, `P()/commodity-desk` (474). Second copy of the calendar-spread ticket (`#spGo` -> POST `P()/spreads`, 482-483, 492) and physical-delivery toggle (`#phGo` -> POST `P()/physical-delivery`, 486-488, 493). COMPUTES curve shape and back-vs-front % (476). Simulated fundamentals (inventory sigma, demand, supply shock, event probability, 476-479). BROKEN LINK: spread rows link `#/strategy/<id>` (485) but there is no `pages.strategy` -> `render()` falls back to Daily News (362).

## 4. Tickets: every order-entry path (VERIFIED)

| # | Ticket | Where (lines) | Preview | Confirm | Submit |
|---|---|---|---|---|---|
| 1 | MAIN ticket `ticketHtml`/`bindTicket` (1177-1227): security, side, qty or $ amount, Execute LIVE/NEXT_UPDATE (only if `liveOn()`), type MARKET/LIMIT/STOP/STOP_LIMIT/TAKE_PROFIT/TRAILING_STOP, limit, stop, trail %, condition (ref op value), TIF DAY/GTC, #tag, settle ccy | 4 placements: Security page (1156 option contract, 1165), Market Place right panel (1759), Live Trades "Quick ticket" (1591), Futures "Futures ticket" (1790) | POST `P()/orders/preview` debounced 250 ms on input (1196-1214); amount->qty via a second preview at submit (1221) | none | POST `P()/orders` (1224) -> `orderToast` (502-505) -> `render()` |
| 2 | Quick Look modal ticket (147-157) | header search Enter / `data-ql` chart buttons (304, 1764, 1806) | POST `P()/orders/preview` (MARKET only, 155) | none | POST `P()/orders` (157) |
| 3 | Option ticket `#ocGo` (566-568) | Options > chain | client-only estimate `describe()` (622) | none | POST `P()/orders` (626) |
| 4 | Option strategy ticket `#stPrev/#stGo` (581-588) | Options > strategies | POST `P()/strategies/preview` (633), optional | none | POST `P()/strategies` (634) |
| 5 | Calculations package `#ccPrev/#ccGo` (1965-1970) | Calculations | POST `P()/playbook/preview` (1984), required (Go disabled until preview) | `confirm()` (1985) | POST `P()/playbook/execute` (1985) |
| 6 | Calendar spread `#spGo` | Futures (1791-1794, 1803) AND Commodity page (482-483, 492) | none | none | POST `P()/spreads` |
| 7 | FX spot `#fxGo` / FX forward `#fwGo` (1683-1684) | Market Place > FX | spot: client-only mid estimate (1696); forward: none | none | POST `P()/fx/spot` (1698), `P()/fx/forward` (1699) |
| 8 | Currency loan `#clGo` / carry trade `#ctGo` / repay `.clRepay` (1671-1678) | Market Place > FX | carry line client-side (1687) | repay via `prompt()` | POST `P()/ccy-loans` (1690); carry = `/ccy-loans` + `/fx/spot` NON-ATOMIC (1689); POST `/ccy-loans/<id>/repay` (1691) |
| 9 | OTC RFQ `#ofGo` -> quotes -> `[data-exec]` deal (764-772) | OTC > RFQ | the RFQ quotes are the preview (POST `P()/otc/rfq`, 804) | none for deal; `confirm()` for unwind (806) | POST `P()/otc/rfq/<id>/execute` (805) |
| 10 | Short-sale chain: locate `#lcGo`, borrow `#bwGo`, return (516-525) | Lending > Securities lending | none | `prompt()` for return | POST `P()/locates`, `P()/loans`, `P()/loans/<id>/return` |
| 11 | Repo `#rpQuote` -> `#rpOpen` (1057-1060) + row actions | Lending > Repo | POST `W()/repo-quote` | `confirm()` only for close | POST `P()/repo`, `P()/repo/<id>/<act>` |
| 12 | Prime-broker margin loan `#pbDraw/#pbRepay` (1081) | Lending > Collateral & prime | none | none | POST `P()/margin/draw`, `/margin/repay` |
| 13 | Private credit commit/sell, Private equity (8 actions), Investment banking pitch/decide, My Desk (8 actions) | 1865-1866, 1840-1848, 1935-1936, 1142-1149 | PE `/structure` only | some `confirm()` | various POSTs (section 3) |
| 14 | Treasury allocate / return / fund | Portfolio overview (1384), ALL BOOKS (1908-1911) | none | `confirm()` only for fund (amount not shown) | POST `W()/treasury/allocate|return|fund` |
| 15 | Option exercise, CA elections, order cancel | 636, 1306, 470 | none | `prompt()` | POST `P()/options/exercise`, `P()/elections`; DELETE `P()/orders/<id>` |

So for securities alone there are SIX separate order-entry UIs (#1-#6), four of which (#1, #2, #3, #4) post to `/orders` or `/strategies` with different field sets and different (or no) previews. None has a hedge section. No ticket enforces "borrow before short" client-side: a SELL with no position goes straight to `/orders` (1222-1224); the borrow is a separate desk (#10). Settings defaults for tickets come from `prefs.trading` / `prefs.options` (9-12, 919-921).

## 5. Day-advance and simulated-market dependencies in the front end (all VERIFIED; to be removed or re-pointed)

- Controls: index.html:35-37 "Advance 1D/1W/1M" `[data-adv]`; `advance()` POST `W()/advance` app.js:224-229; shown only for non-REAL_TIME clocks (201), disabled while a REAL replay waits (207); `#simDate` + `dayLabel()` (198-200, 398); SANDBOX/CAREER badge (202); `windowPill` session-lock / SIMULATED PRICES badge (217-223).
- New save modal: Mode SANDBOX vs Career (244), Seed (249), Start date (250), Realism (253), Initial regime (254), Market SIMULATED vs REAL replay (255), Scenario CRISIS (257), Update time + session lock (246-248) (app.js:236-269). New book: Realism (275).
- Simulation controls: Force regime (1756-1762, POST `W()/force-regime`); default dealer / credit event (788, 809-810); "Switch this save to the real market" `track-real` (926, 941, 1582, 1599); clock settings POST `W()/clock` (927, 942).
- Model-only displays: regime badge and regime history (200, 204, 1648, 1757, 1374); `state.world.vol_index` (1623, 1742); FX "drivers" decomposition (1660, 1668); commodity fundamentals (476-479, 1751); macro seeded releases (1045); "Today's synthetic session" option bars (1156); PE/PC/IB monthly invented pipelines (1832, 1859, 1926); dealer stress (773, 788); missions/reviews (1094-1098).
- Date arithmetic on the simulated date `state.world.current_date`: `tenorDate` (19), option DTE (552), FX forward default maturity (1684), PE lock-up check (1835), Live Trades "today" filters (1568-1575), Open Positions settlement "due" (1510, 1513), CSV file names (1559, 1561), page subtitles (1374, 1757).
- Execution-timing text tied to the update cycle: `execLabel` / `qu()` (407-409), ticket hint (1185), option MARKET "at the next open" (567), "executes at the next update" (626), strategies/spreads "at the next open" (588, 634, 482, 1791), calc hint (1970), briefing hint (420), Live Trades header (1582), locate "expires after the next session" (518), OTC quotes "good for this session" (769, 771), risk limits "until the next close" (1017).
- Trade-window lock: `lockInstructions` + `LOCK_BTNS` (379-384), Settings session lock (927).
- Live quote plumbing that SURVIVES the change (keep and promote): `liveOn`, `liveQuotes` -> GET `W()/live?ids=` (396-406), `liveFill` 60 s refresh (404), `reportSweep` (406), POST `W()/live/work` resting-order sweep (1597, 1600), LIVE vs NEXT_UPDATE exec selector (147, 1180, 1683, 921), live columns on Market Place / Open Positions (1457-1458, 1625-1628, 1650, 1713, 1730, 1744, 1754).

## 6. FINDINGS FOR THE REPAIR

### 6.1 Bug 1 -- "trades disappear after a couple of days / over a weekend": front-end evidence
1. VERIFIED: LIVE TRADES lists only items dated `state.world.current_date`: fills `trades.filter(t => t.trade_date === today)` (1572), orders entered today (1571), OTC today (1574). After the date moves (advance, or the daily update), yesterday's fills vanish from that page by design.
2. VERIFIED BUG: OPEN POSITIONS fetches `/trades` and `/orders` only in HISTORY mode (1451: `mode === 'history' ? api(P()+'/orders') : []`), yet the "Pending" section shown in OPEN NOW mode is built from `orders.filter(...)` (1512) -> working instructions NEVER appear in the OPEN NOW pending table.
3. VERIFIED: `ordersTable` hides CANCELLED / EXPIRED / REJECTED orders unless "show" is toggled (1229-1231): DAY orders that expire unfilled over a weekend disappear from every blotter.
4. VERIFIED code, effect INFERRED: with ALL BOOKS selected, every book-scoped call uses `bookForAll()` = the TREASURY book (65-66). `PF_FREE` (360) lets Market Place, Security, Commodity, Daily News and Open Positions render with ALL selected, so a trade entered on the Market Place / Security page / header Quick Look while ALL is selected is booked in the TREASURY book, with no note on those pages (`allNote()` is only called at 1389 and 1663). The user then looks in their own book and the trade is "gone".
5. VERIFIED code, effect INFERRED: changing the world selector sets `state.pfId = null` (230) and `loadWorld` then picks `pfs[0]` (199) -- typically the Treasury -- so after switching saves the user lands in a different book than the one they traded in. Any stale `finsim.pf` in localStorage also falls back to `pfs[0]` silently (199).
6. INFERRED (server side): in career saves `current_date` is "the last processed close" (200, 398) while a live fill may be stamped with the real date; the `today` filters above then disagree with the real day.

### 6.2 Bug 2 -- "about 10,000,000 appears in Treasury unexplained": front-end evidence
1. VERIFIED: three UI paths create capital from nothing: new book with "draw it from the Treasury" unticked = "fresh money into the book" (274); new book ticked "make this book the save's Treasury" with blank capital = "the job's standard" (273-275); ALL BOOKS "add capital to treasury" -> POST `W()/treasury/fund` using the shared amount box `#trAmt` (step 1,000,000) after a `confirm()` that does not show the amount (1882, 1908, 1911).
2. VERIFIED: the front end always sends `capital: null` when the box is blank (266, 277). Server cross-reference (not my area, read only to locate it): `api/server.py:131` uses `body.get("capital", 10_000_000)` and `world.py:732` defaults `capital=D(10_000_000)` -- the 10,000,000 default lives there; what `None` turns into downstream is for the server mapper.
3. VERIFIED: 10,000,000 is also the client default for OTC notional and XCCY `usd_notional` (11, 643-654, 761; XCCY exchanges principal at the start, 648) and for repo face (1057, "Repo cash arrives today" 1059); currency loans put cash in "today" (1677). With ALL BOOKS selected these all book into the Treasury (see 6.1 item 4). INFERRED candidates.
4. VERIFIED: Performance counts capital flows as returns (1438), so a Treasury draw shows as performance in the receiving book.

### 6.3 Duplicate computations in the browser (move to ONE accounting module; all VERIFIED)
- Performance statistics: `perfStats` (160-177), drawdown series (1431), monthly returns (1432, 1437), growth of 100 (1439-1440). Career tab shows the server's own Sharpe / drawdown (1094) -> two Sharpe ratios.
- Cash totals: `ccyTable` total + share (390-391); Overview cash tile SUM (1394); FX foreign cash SUM (1663); Futures IM/VM SUMs (1776); Collateral posted sums (1076); seclending collateral/fees SUMs (513).
- Market value / P&L aggregates with DIFFERENT definitions: Open Positions MV = securities + options + open OTC MTM + open private credit (1546); Groupings MV = securities + options + open OTC MTM + FX-forward MTM (828-834, 852); Open Positions "Today" = SUM all positions' day_pnl (1547); Groupings day/unrealized (827-830, 852); ALL BOOKS totals (1889, 1891); ALL BOOKS Total NAV tile uses `nav_with_treasury` (1877) but the Total row uses `nav` (1889).
- Income = dividend_income + interest_income in three places (465, 1462, 844) and `income.dividends + income.interest` (1416).
- OTC net P&L = mtm + realized in three places (777, 847, 1471).
- Ratios of NAV: 437, 996/1000/1009/1025, 1388, 1395, 1858; leverage 1889; alpha 1393.
- Reconciliation checks: `|ledger_nav - nav| < 0.005` (1408); NAV explain residual `nav - prev_nav - capital_flows` (454) and component SUM (456).
- Pricing/estimates: option premium x100 (622, 604, 607); FX mid conversion (1696); FX forward annualised (1669); live-vs-mark P&L (1458, 1552); index levels = ETF x factor (1732, 1738); repo haircut `min(0.75, v*mult)` (1058); private-credit coupon and %-of-par (1858); PE paid-in (1829); bond 2s10s and curve changes (1631-1632, 1638); moving averages (122).
- Reference data duplicated client-side: chart of accounts `ACCT` (1289); OTC product forms/help (642-692, 1989-2002); `INDEX_SRC` and index factors (184, 290, 1732); `futKey` ES->SPY, ZN->UST-10Y (822); `BUCKET_LABEL` / `EXPLAIN_SHORT` (412, 1288).

### 6.4 Other defects and surprises
- VERIFIED hard-coded figures: position 'Financing cost' tile = `fmt.money(0)` (1273); security 'Borrow' = 'n/a, Phase 2' (1162); option cash x100 regardless of `contract_size` (554 vs 604/607/622).
- VERIFIED broken link `#/strategy/<id>` (485) -> Daily News fallback (362); `NAV_OF.strategy` (185) points at a page that does not exist.
- VERIFIED broken links (2 more): (a) `state.securities` is READ at 428 but never assigned anywhere in app.js, so in the Daily Briefing news panel every ref of 4 characters or fewer (NVDA, SPY, AAPL ...) links to `#/commodity/<ref>` instead of `#/security/<ref>` (428); `pages.commodity` then calls `W()/commodities/<ref>` (474), which (INFERRED) errors. (b) Groupings BY #TAG chips use `href="#bk-tag-<tag>"` (878); the `hashchange` handler (234) treats that as a route, `render()` finds no page and falls back to Daily News (357-362), so the chip navigates away instead of scrolling.
- VERIFIED (code + comment at 405): GET `W()/live?ids=` has a side effect -- it "also works the resting live book" and returns fills (`r.worked`, 402, 406). Fills are therefore triggered by page views and by the 60 s refresh timers (404, 1628, 1713, 1730), not only by explicit actions.
- VERIFIED duplicate fetch of `/options/<under>/surface` on the vol tab (598 and 637).
- VERIFIED non-atomic multi-call actions: carry trade (1689); calculations package legs "not guaranteed to fill together" (1970).
- VERIFIED race hazard: ALL BOOKS Open Positions temporarily rewrites global `state.pfId` inside an awaited loop (1528) while 60 s timers (`liveFill`, `windowPill`, live sweep 1600) may call `P()`.
- VERIFIED: the header comment "nothing is computed for display here" (1-2) is false (see 6.3).
- VERIFIED: `bindAction` uses `throw new Error('')` as control flow to keep buttons enabled (e.g. 938, 1060, 1598, 1909-1911, 1984).
- VERIFIED: deleting a save requires typing its name (983-985); there is no save/load/export, so "atomic save/load" has no front-end surface today.
- VERIFIED: all 32 distinct literal `gl('...')` terms and all OTC `gloss` terms exist in glossary.js (checked with grep); `gl()` links have no existence check.

## 7. Navigation links into pages that will be REMOVED (VERIFIED; must be re-pointed)
- `#/positions` (Open Positions): sidebar (181), `NAV_OF.position` (185), `ALIAS.trades` (183), Portfolio overview (1407), ALL BOOKS blotter button (1896), `reportSweep` re-render list (406), `PF_FREE` (360).
- `#/position/<id>` detail (parent = Open Positions): 426, 434, 465, 467, 520, 594, 844-846, 1077, 1304, 1419, 1460-1468, 1785 (17 occurrences).
- `#/live` (Live Trades): sidebar, Portfolio overview working-instructions link (1405), calc execute redirect (1985), `reportSweep` (406).
- `#/groupings[/...]`: sidebar, `ALIAS.book` (183), header search "grouping" links (308), Quick Look (149), Portfolio overview (1407), OTC tag hint (667).
- `#/options[/...]`: sidebar, `NAV_OF.strategy` (185), header search (299, 304, 308), Quick Look (149), rowTools "opts" (1607), Futures options-on-futures (1787), Commodity page (491), Groupings (845), Live Trades packages (1586), Briefing "Options desk" (435).
- `#/futures`: sidebar, header search (308), Quick Look (149), Groupings (846), Live Trades (1586), Market Place commodities hint (1753).
- `#/otc[/...]`: sidebar, Briefing (436), Live Trades (1589, 1592), Open Positions (1469), Groupings (847, 873), Market Place (1685, 1707, 1742, 1753), OTC risk rows (783).
- `#/calc`: sidebar, Quick Look (149), Options header (615).
- KEEP pages and their tab routes: `#/news[/briefing|feed|macro]`, `#/portfolio[/overview|pnl|performance|career|desk]`, `#/accounting[/ledger|settlements|cash|audit]`, `#/market[/equities|bonds|fx|commodities|crypto|indices|housing]`, `#/lending[/seclending|repo|collateral|private|pe]` (-> Credit), `#/risk[/overview|stress|liquidity|limits|history]`, `#/settings`, `#/glossary[/<slug>]`. Pages outside the keep list not named in the directive: `#/overall` (ALL BOOKS, holds the Treasury controls), `#/banking`, `#/coverage`.

## 8. Visual style to keep (style.css, VERIFIED)
- Design tokens on `:root` (style.css:1-6) with light theme override `:root[data-theme="light"]` (7-12); theme set by `applyTheme()` (app.js:26). Mono font `--mono` for numbers, `--sans` body, 13px base.
- Layout: `#app` grid 200px sidebar + main (17); `header` bar (18-24); `nav` sidebar with `.section`, `a.active` (38-40 and later); `main`; `#progress` top bar, `main.busy`, `main.fade-in` (app.js:366-369).
- Components: `.btn`, `.btn.primary`, `.btn.danger`, `.badge` + `.green/.red/.amber/.blue/.purple` (25-37); `.tiles` / `.tile .k .v .s` / `.tile.click` (47-51) built by `tile()` (app.js:393); `.panel` (59); `.grid2/.grid3/.grid4/.grid-otc` (56-58), `.grid-mkt` (ticket beside tables, 180), `.grid-calc` (181); `.kv` key-value grid (90-91); `.form` label/field grid (68-70); `.scroll` (67); `.hint` (98); `.tabs` sticky tab bar (177, 204) from `tabsHtml()`; `.subhead`; tables `th/td`, `.l`, `tr.click`, `tr.picked`, `table.xl` (spreadsheet look), `table.chain` (option chain, `.atm`, `.optq`, `th.strike`), `table.book` `.bidc/.askc`; `.pos/.neg/.muted/.mono/.ok/.error`; `.lifecycle` trade-status chips; `.tree` event tree; `.modal-bg/.modal`; `.toast/.toast.err`; `#banner(.info)`; `.lockbar`; `.legend i`; `.livepx/.liveline/.ccyline`; `.newsitem`; `.explain .k`; `.catalogue`; glossary `a.gl` dotted link and `.gl-entry(.hit)`; search dropdowns `#tickerDD .hit` and `.ta-dd .hit`; `input.pagefilter`.
- Responsive: breakpoints 1100px / 760px / 1500px (58, 110, 174, 182, 184, 189, 222); reduced-motion rule (223).
- Charts are hand-drawn canvas (`lineChart`, `candleChart`, `candleChartPro`, `barChart`, app.js:70-137) reading CSS tokens via `css('--x')`.

## 9. glossary.js and coverage.js content tied to removed pages or to the simulation (VERIFIED, both files read in full)
- glossary.js structure: `window.FINSIM_GLOSSARY = { categories: [[key, label] x 11] (glossary.js:4-8), terms: [[term, category, text, seeAlso] ...] (9-252) }`. Category 'career' = "Careers & the game".
- Entries that name pages being removed: 'Strategy playbook' -> "The Calculations page ... watch them on Live Trades" (214); '#Tag' -> "Groupings -> By #tag" (249, and see-also 'Groupings', which is not a term); 'Futures contract' -> "The Futures page lists every contract" (206).
- Entries describing the day-advance / simulated market (rewrite for "live prices, paper cash"): 'Stock (equity)' "Fills at the next session's open" (11); 'MARKET order' "Execute at the next session's open" (47); 'LIMIT order' "if the next session trades through it" (48); 'Time in force' (53); 'Expiry' "the game lists four monthlies plus two quarterlies" (60); 'Options margin' "three sessions to cure" (77); 'Net limit' "rejected at the open" (98); 'RFQ' "quotes last one session" (102); 'Margin call' (134); 'Regime' "sandbox saves can force one" (183); 'Liquidity stress' (184); 'Policy rate' "reaction function" (185); 'Rating' "migrates monthly" (189); 'Earnings' (194); 'Universe snapshot' "the simulation moves them from there" (195); 'Career' "Sandbox saves advance when you press the button" (198); 'Update time' (203); 'Real market (tracking)' "can only advance into sessions the market has already closed" (215); 'Career save' (221); 'Live ticket' (222); 'Instruction window' (223); 'Investment banking' / 'Private equity' / 'Private credit' invented deal flow (207-209); 'Inflation swap' "the simulator's price index" (239).
- VERIFIED INCONSISTENCY: 'Update time' says "default 09:00 in your timezone" (glossary.js:203) but the new-save form defaults to 17:00 (app.js:246) and 'Career save' says 17:00 New York (glossary.js:221).
- VERIFIED: 'Short sale' already states the intended rule: "You first locate and borrow them (Securities lending), then sell" (glossary.js:38); 'Margin loan' says the prime-broker loan is "Drawn automatically when a purchase settles beyond your cash" (44) -- an automatic cash-borrowing path relevant to the Credit page and to unexplained cash (INFERRED).
- 'Index (SPX, NDX, RUT, DXY, VIX)' documents the client-side ETF x factor levels (231), matching app.js:1732.
- VERIFIED glossary link defects (219 terms checked by grep): (a) the Glossary page splits each see-also field on commas (`see.split(',')`, app.js:970), so see-also references to terms whose NAMES contain commas break into fragments: 'Structured credit (CLO, CMBS, RMBS, ABS)' (referenced at glossary.js:21) and 'Index (SPX, NDX, RUT, DXY, VIX)' (referenced at 232-233); (b) see-also names with no matching term: 'CDS' (31; the term is 'Credit default swap (CDS)'), 'Futures' and 'Option' (25), 'Treasury bills' (24), 'Underwriting' and 'League table' (207), 'Leverage' and 'EBITDA' (208), 'Market impact' (222), 'Implied volatility' (242; the term is 'Implied volatility (IV)'), 'Groupings' (249). Such links open the Glossary with nothing highlighted.
- coverage.js (`window.FINSIM_COVERAGE = [[section, [[item, 'built'|'partial'|'not built', note] ...]] ...]`, 56 lines): claims "60 playbook packages plus the chain ticket" (coverage.js:31) while the owner's directive speaks of 38 strategy templates -- the real count lives in the server's `/playbook` (front end does not hard-code it). Items referencing the Calculations playbook (9, 31, 32, 34, 48), the Futures desk (33), Groupings/#tag (9), sandbox-only desks (22), "Market-on-close / on-open ... 15-minute quote updates" (52), "Resting live orders ... checked every minute" (52).

## 10. Coverage of this map
Read in full with Read: index.html (47 lines); app.js lines 1-2005; style.css lines 1-239; coverage.js lines 1-57; glossary.js lines 1-254. Server code was NOT mapped (only `api/server.py:124-135` was opened to locate the 10,000,000 default cited in 6.2; `world.py:732` was seen through grep only).

STATUS: COMPLETE
