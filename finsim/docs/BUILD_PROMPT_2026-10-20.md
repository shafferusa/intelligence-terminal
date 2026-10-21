# The build prompt given to ChatGPT (written by the earlier Claude session, 2026-10-20 ~13:40 ET)

The owner re-sent this on 2026-10-20 ~16:40 ET with: "confirm that all of these marks are hit too". It is therefore part of
the specification for the terminal built here. Precedence when texts conflict: HANDOFF_2026-10-20.md (newest) > this build
prompt > DIRECTIVE amendments > the directive's verbatim text. Known conflict and its resolution: B6 says "show NO source or
delay labels in the UI" while the handoff §4/§10/§11 say delay, source and freshness must be transparent and never hidden.
Resolution: the trading tables carry no source/delay text; every price carries a small freshness indicator (age, dimmed
when stale) with the source, provider time and delay on hover and in the position/instrument drawer, and a Data Status
panel (Settings and the Daily News attention strip) lists each provider's status and delay. Nothing is hidden; nothing
clutters the ticket.

## Owner amendment (2026-10-20 17:03 ET): variation margin on swaps too
The owner: "variation margin is on swaps too btw". Every swap booked through a prime broker (IRS, TRS, cross-currency; and
any other OTC derivative under the PB's CSA) carries DAILY variation margin exactly like futures: each calendar day on which a
mark exists, the change in the swap's mark (computed from the live curve / underlying, labelled `computed`) is exchanged in cash
with the PB — paid when the mark falls, received when it rises — as one idempotent movement per swap per day (key: swap id +
date), funded by the Treasury sweep when the account lacks settled cash. VM paid/received is collateral (an asset/liability
against the PB), not P&L; the P&L is the mark change itself. Initial margin for swaps follows the PB schedule. The Cash &
financing tab, the ledger, Risk (margin headroom per PB) and the lifecycle job all include swap VM. Cross-currency swaps also
exchange notional at start and maturity per the confirmation.

## Owner amendment (2026-10-20 17:07 ET): swap variation margin AND resets
The owner: "vm and resets". Swaps carry both daily variation margin (above) and their scheduled RESETS and payments, per the
confirmation, run by the lifecycle job on the real calendar, idempotent (key: swap id + reset date), cash-settled in the account
(Treasury sweep if short):
- IRS: the floating leg fixes at each reset from the live reference rate (SOFR from FRED, compounded in arrears or term as
  booked; EURIBOR/SONIA equivalents for other currencies from FRED/ECB series where free); the fixed and floating coupons accrue
  daily (stated day count, ACT/360 for SOFR, 30/360 for the fixed leg unless booked otherwise) and the NET coupon is paid on each
  payment date (quarterly/semi-annual as booked).
- TRS: at each reset date the total-return leg (price change of the underlying since the last reset plus dividends paid in the
  period) and the financing leg (reference rate + spread on the notional for the period) are settled in cash and the notional is
  reset to the underlying's current value; dividends accrue as receivable/payable between resets.
- Cross-currency: periodic coupon exchanges in each currency, notional exchange at start and at maturity, and (if booked as
  mark-to-market XCCY) an FX notional reset on the USD leg at each reset with the difference settled in cash.
- Interaction with VM: VM collected/paid between resets is collateral; at a reset/payment the realised cash flow is exchanged and
  the collateral balance is re-measured against the post-reset mark so nothing is paid twice (settled-to-market where the PB
  schedule says so, otherwise collateralised-to-market with the collateral balance carried). Accrued coupons appear on the
  balance sheet as accrued income/expense until paid. Risk shows DV01/exposure from the current (post-reset) terms.

## Owner amendment (2026-10-20 17:09 ET): a Reset in Settings that keeps history
The owner: "a reset button in the settings to remove all positions like if i went bankrupt and wanted to restart, but there
should be history that stores this data". Design: the terminal has LIFETIMES. Settings gets a "Reset lifetime" button (typed
confirmation "RESET", one transaction, idempotent request_id). Reset CLOSES the current lifetime — nothing is deleted: every
account, position, order, fill, ledger entry, mark, collateral/loan/borrow record stays in terminal.db tagged with its
lifetime_id and becomes read-only — then opens a new lifetime: fresh Treasury with the starting capital the owner types
(default USD 10,000,000, posted once as a capital deposit), the four standard accounts empty, no prime-broker accounts, sweeps on.
Open orders of the closed lifetime are marked CANCELLED (reason "lifetime reset") in its history; its final NAV, realised P&L,
dates and the reason are recorded on the lifetime row. Settings shows the list of past lifetimes (name, start, end, final NAV,
P&L) and a "History" view lets the owner browse an archived lifetime's Portfolio, Accounting (P&L, balance sheet,
transactions with CSV export) and Activity read-only. The accounting module takes the lifetime as a parameter (default: the
current one), so every figure is per lifetime and integrity() runs per lifetime. Performance statistics are per lifetime.

## Owner amendment (2026-10-20 17:13 ET): two books; prime-broker / OTC eligibility
The owner: "you need the proper certification for prime broker relationships ... i think its the isda but correct me. I want two
books: one that starts at 10m (wont be using prime brokers / cant deal in OTC, right?), and one that starts at an isda-minimum
that can do the prime broker. whenever the 10m one reaches the isda minimum or whatever, it can start relationships".
Real-world facts to encode (shown in plain English in the app):
- OTC derivatives (swaps, forwards, OTC options) are traded under an ISDA Master Agreement plus a Credit Support Annex (CSA,
  the collateral/VM terms) with each dealer; repo under a GMRA; securities lending under an MSLA/GMSLA; a prime-brokerage
  relationship under a Prime Brokerage Agreement (PBA). ISDA itself sets no minimum.
- The legal floor for trading OTC swaps off-exchange is Eligible Contract Participant (ECP) status under CEA section 1a(18): an
  individual with more than USD 10M invested on a discretionary basis (USD 5M if hedging), or an entity with total assets over
  USD 10M. Dealers' own onboarding minimums for an ISDA/CSA + PBA are higher and vary: typically USD 25M–50M+ of net assets for
  a bulge-bracket prime broker (mini-primes accept less). The app uses an editable "PB/OTC eligibility threshold", default USD
  25,000,000 of book NAV, and states the convention on screen.
Design:
- TWO BOOKS, each a full lifetime of its own (own Treasury, own accounts, own ledger, own reset/history):
  1. "Exchange Book" — starts at USD 10,000,000. Exchange-traded only: US stocks/ETFs/ADRs, listed options, futures, Treasuries
     and corporate bonds, FX spot, cash loans from a bank, margin-style stock borrow through its (non-prime) broker. No prime
     broker accounts and no OTC derivatives UNTIL its NAV reaches the eligibility threshold; then "Open prime broker account"
     unlocks (onboarding records ISDA Master + CSA + PBA with the dealer and date, one click). If NAV later falls below the
     threshold, existing relationships stay (real dealers manage this by margin, not by closing), shown as a notice.
  2. "Prime Book" — starts at the eligibility threshold (USD 25,000,000 default, typed at creation). May open prime-broker
     accounts and trade OTC from day one (onboarding dialog still records the agreements).
- The sidebar has a compact book switcher at the top (two entries); all pages follow the selected book; Portfolio and Accounting
  offer "both books" consolidated views with internal transfers eliminated (there are none between books: books never transfer
  to each other). The "Add funds" and "Reset lifetime" actions are per book.
- The ticket refuses an OTC/PB trade in an ineligible book with the plain-English reason (this is an eligibility mechanic, not a
  risk warning).

## Owner amendment (2026-10-20 17:41 ET): THREE books (1M, 25M, 1B institutional) — supersedes the two-book layout above
The owner: "i think there should be 3 books: one at 1m, one at 25m, and one at 1b (institutional)". Same mechanics as the
two-book amendment (each book = its own lifetime: own Treasury, accounts, ledger, Add funds, Reset + history; a compact book
switcher at the top of the sidebar; consolidated views in Portfolio/Accounting; books never transfer to each other), with these
three books created on first run:
1. "Personal Book" — USD 1,000,000. Exchange-traded only (stocks/ETFs/ADRs, listed options, futures, Treasuries/corporates, FX
   spot, bank cash loans, margin-style stock borrow through its broker). Below the ECP floor (USD 10M) it cannot trade OTC at
   all; prime brokers/OTC unlock when its NAV reaches the eligibility threshold (default USD 25M, editable), with the one-click
   onboarding (ISDA Master + CSA + PBA recorded). Compliance notices relevant at this size (Reg T margin, pattern-day-trader
   rule with its USD 25,000 equity floor, cash-account good-faith/free-riding) are shown pre-trade when they apply.
2. "Fund Book" — USD 25,000,000. Eligible for prime brokers and OTC from day one (onboarding dialog records the agreements).
   13F notice when 13F securities exceed USD 100M; 13h-1 large-trader notice at the daily/monthly thresholds.
3. "Institutional Book" — USD 1,000,000,000. Prime brokers and OTC from day one; the compliance layer treats it as an
   institution (13F applies from the start, 13h-1 large trader, Section 13D/G 5% and Section 16 10% on single names, options
   and futures position limits/accountability levels, Form SHO/13f-2 short reporting), all inform-only.
The eligibility threshold, the three starting amounts and the book names are editable in Settings (changes apply to new
lifetimes; the live book's history is never rewritten).

## Owner amendment (2026-10-20 17:48 ET): PRIVATE MARKETS for the institutional (hedge-fund type) book — real companies
The owner: "institutional (hedge fund type) can also be in PRIVATE markets, make this real like anthropic, open ai / other
companies etc". Design (no fabricated prices or events; everything sourced and labelled):
- A new account "Private Markets" (sidebar, like the other accounts). Open by default in the Institutional Book; unlocks in the
  other books when their NAV reaches the Qualified Purchaser threshold (editable; default USD 25M for the Fund Book as an entity,
  USD 5M for the Personal Book as an individual), with the plain-English rule shown (accredited investor / qualified purchaser /
  QIB; Rule 506 and Rule 144 transfer restrictions).
- Instrument master `private_companies`: name, sector, country, status (private / filed for IPO / public → ticker), LAST
  REPORTED ROUND: date, round type (Series X, tender offer, secondary, SAFE), post-money valuation, implied price per share where
  disclosed, lead investors, and a SOURCE URL + as-of date for every number. Ship a starter table of ~25 real names researched
  at build time with web search and cited (Anthropic, OpenAI, SpaceX, xAI, Stripe, Databricks, Anduril, Scale AI, Perplexity,
  Mistral, Safe Superintelligence, Anysphere (Cursor), Groq, Revolut, ByteDance, Canva, Epic Games, Discord, Fanatics, Kraken,
  Ramp, Rippling, Deel, Vercel, Cerebras …); a figure that cannot be sourced is left blank, never guessed. The owner can "Record a
  reported round" (dialog: date, valuation, price per share, source URL) and "Add a private company" the same way; a company that
  lists switches its mark to the live ticker from the listing date.
- Trading: "Buy secondary shares" / "Participate in round" / "Sell secondary": the owner enters the quantity and the NEGOTIATED
  price per share (default = the last round's implied price; a discount/premium field), the placement/broker fee (default 3 %,
  editable), the settlement lag (default T+30 calendar days, pending until then) and the lock-up (default 6 months; the ticket
  shows it and the Sell ticket warns while it runs). There are no invented counterparties or fills: the trade executes at the
  owner's negotiated price because private secondaries are negotiated, and the preview says so in one sentence.
- Marks: `private-last-round` quality = the last reported round's implied price per share (or valuation × owned fraction when
  only the valuation is known); it changes ONLY when a new reported round is recorded or the owner records a secondary indication
  with a source. Shown with its as-of date; never dimmed as stale (private marks are expected to be old) but labelled "last round
  <date>". P&L = mark − cost; realised on sale.
- Risk: a separate "Private / illiquid" bucket (% of NAV, concentration, lock-up calendar); liquidity shows these as illiquid;
  no VaR shocks applied to private marks (disclosed). Compliance notices: accredited/QP status, Rule 144 holding periods,
  Form D is the issuer's filing (informational), 13F does not cover private shares (informational).
- Private credit (direct loans to such companies, floating coupon = policy rate + spread, OID accretion, amendment fees,
  quarterly payments) lives in Credit Instruments as already specified and may reference the same company master.

## Owner amendment (2026-10-20 18:28 ET): NO Glossary
The owner: "glossary can just be removed". Drop the Glossary page, its sidebar entry, the glossary data module and any gl()
term links. WP-25 (Glossary data) is CANCELLED; WP-12 (UI) builds no Glossary page and does not depend on WP-25; WP-15
(integration) deletes any glossary file that was already written and removes every reference. Conventions that B16 wanted
"stated on screen in one sentence" stay as inline one-sentence notes on the pages where they matter (not in a glossary).

---

# Task: turn FinSim into a live paper-trading terminal

You are a senior Python + JavaScript engineer. Attached is the FinSim project (Python package finsim/finsim/, front end finsim/finsim/static/, tests finsim/tests/). Read all of it before changing anything. FinSim today is a single-process paper-trading SIMULATION: a model-driven market that advances one day at a time. Turn it into a LIVE paper-trading terminal: real current prices, pretend money, positions always at current marks, no simulation, no "advance day". Below: (A) verified facts about the current code, (B) exactly what to build, (C) engineering rules, (D) acceptance tests, (E) deliverables.

## A. The current code (file:line references are from the attached code)

### A1. Layout and how it runs
- Python standard-library HTTP server + SQLite. Runs on Windows, Python 3.14: pythonw -m finsim serve --db %USERPROFILE%\.finsim\finsim.db --host 127.0.0.1 --port 8765, auto-started at sign-in by a Startup-folder VBS. Home folder %USERPROFILE%\.finsim.
- finsim/app.py (installer, launcher, start_background); finsim/__main__.py (CLI; python -m finsim serve WITHOUT --db defaults to data/finsim.db relative to the current folder on port 8000 — a second database, __main__.py:13,15).
- finsim/api/server.py: Router + make_handler; EVERY API request runs under one global router.lock (server.py:353-355); a scheduler thread ticks every 60 s (server.py:405-419); serve() starts the scheduler before binding (server.py:467-468).
- finsim/api/service.py (2,042 lines): all read models and commands.
- finsim/world.py (1,179 lines): event-sourced World. finsim/store.py: SQLite event store. Also migrations.py, money.py, calendar.py (rule-based NYSE full-day holidays only), clock.py, tzfallback.py, log.py, careers.py, demo.py, domain/ (models.py, events.py, otc_models.py).
- finsim/engines/ (47 files): market.py, trading.py, options.py, options_pricing.py, futures.py, otc.py, otc_extra.py, otc_pricing.py, pricing.py, fx.py, fx_market.py, ledger.py, pnl.py, treasury.py, accruals.py, settlement.py, collateral.py, repo.py, prime.py, seclending.py, lending_market.py, ccy_funding.py, private_credit.py, private_equity.py, investment_banking.py, investors.py, institutions.py, clients.py, counterparties.py, corporate_actions.py, corporate_events.py, macro.py, global_rates.py, commodities.py, commodity_desk.py, vol.py, risk.py, briefing.py, playbook.py, mbs.py, structured.py, simulation.py, live.py, realfeed.py, realmacro.py, realnews.py.
- finsim/quant/ (stats, timeseries, volatility, derivatives, swaps, fixed_income, credit, portfolio, regression, linalg, ml, nn, stochastic, catalog): FinSim never imports it; the separate Desk app does.
- Front end: ONE file static/app.js (512 KB, ~2,000 very long lines) + glossary.js, coverage.js, index.html, style.css.
- Tests: finsim/tests/, unittest style, run with pytest (pyproject testpaths=['tests']). 35 files test FinSim; test_fs2_* and test_desk_* belong to the separate Desk app (A9) but many import finsim.world. tests/helpers.py make_world(capital=10_000_000) creates a world and runs one simulated day.

### A2. Persistence, and why trades disappear
- SQLite append-only events table, primary key (world_id, seq), WAL, one transaction per flush (store.py:82 append_events). No save button or autosave; every mutating call persists.
- World.emit() applies an event in memory and queues it in _pending; flush() writes later (world.py:128-155). World._cmd flushes in a finally (world.py:837-841), so a half-failed command still saves its partial events. If append_events raises, _pending is not cleared and memory is not rolled back.
- Service caches each World for the life of the process and never re-reads the DB (service.py:258-273). The scheduler and live sweep swallow flush errors (server.py:415-419, service.py:233-236). A trade can show in the UI and never reach disk; it vanishes at the next restart.
- Evidence in %USERPROFILE%\.finsim\server.log: 367 "database is locked", 88 IntegrityError (duplicate (world_id, seq)), 6 "failed to catch up"; interleaved timestamps show TWO server processes writing one DB. Likely cause: start_background probes /api/health with a 1 s timeout and spawns another server if nothing answers (app.py:173-179, 204-218); Python's HTTPServer sets allow_reuse_address=True (SO_REUSEADDR), which on Windows lets a second process bind the same port. The logon VBS starts pythonw with no output redirect, so later errors are invisible.
- Load = migrate + replay. Replay is NON-strict by default (service.py:74; FINSIM_STRICT_REPLAY=1 makes it strict): a failing event (e.g. TRADE_EXECUTED for an unknown security, trading.py:630) is skipped and only logged. _h_world_created rebuilds the universe and a simulated prehistory from (start_date, seed) with the CURRENT code on every load.
- Display bug on top: the Live Trades page shows only trades with trade_date === current_date (app.js:1567-1575). If the remembered world is missing, app.js silently opens the most recently created world.

### A3. The day-advance machinery (all of it goes away)
- World.advance (POST /advance, world.py:1079-1097) and World.catch_up (runs on EVERY Service.world() call and every 60 s tick, world.py:1099-1115) call SimulationEngine.run_daily_process(d) (simulation.py:34-139): DAY_STARTED; market.generate_day (price generator, market.py:873-1087: Markov regimes, factor equity model, Nelson-Siegel curve walk, invented OHLC/volume/spreads); MARKET_CLOSE; news/macro; clients/AI desks; trading.work_orders; settlement; Treasury overdraft backstop (world.py:474-493, simulation.py:101,122); corporate actions, futures, sec lending, repo, currency loans, private credit/PE/IB, FX, OTC, options; marks (twice); accruals; investor flows; career limits; NAV snapshot; risk; DAY-order expiry; missions; briefing; DAY_CLOSED. Weekends/holidays skipped.
- "REAL" mode still simulates, then rescales each bar so its close equals Yahoo's close (_pin_day, market.py:581-657); open/high/low/volume/spreads stay invented; it still produces invented earnings, ratings and issuer DEFAULTS (macro.py:234-272, simulation.py:81-86); a save created intraday stores Yahoo's partial bar as the close (service.py:121-131, market.py:563-569).
- Front end: header buttons (index.html:35-37, data-adv 1/5/21) call advance() (app.js:224-229).

### A4. Live data already in the code
- engines/live.py: Yahoo quotes "up to 15 min delayed" (live.py:37); LIVE tickets fill immediately via trading._fill (live.py:445-451) but use the last SIMULATED bar's volume and spread (live.py:405-408); resting orders swept every 60 s; GET /live has a side effect: it works orders and returns fills (app.js:402-406). Positions are NEVER marked live (marks only run inside run_daily_process). Options reprice only spot at live (live.py:206-215); bonds use a Nelson-Siegel fit to ^IRX/^TNX/^TYX (^FVX ignored at close, market.py:660-682); IG/HY spreads simulated.
- realfeed.py, realmacro.py, realnews.py fetch real data; realnews.py sends a browser UA to federalreserve.gov (realnews.py:22-23, 28-29), which is not allowed (rule in B6).
- Network fetches run INSIDE request handlers under the global lock and freeze the app.

### A5. Ledger, cash, P&L, Treasury, and duplicates
- engines/ledger.py chart (1010:CCY cash, 1200/2100 accrued, 3000 contributed capital, 4910, 5150 …); key '2800' defined twice (ledger.py:51, 54); currency-loan interest posts to 5150, missing from PNL_ACCOUNTS (pnl.py:21-22 vs ccy_funding.py:176-177).
- TWO money systems: ~15 handlers change Portfolio.cash/base_value directly AND post to 1010:CCY; pnl.compute_summary NAV (pnl.py:136-184) vs Ledger.nav (ledger.py:242-245) compared only at load (world.py:180-203), mismatch just recorded.
- Duplicates: two NAVs; two balance sheets (marks vs ledger); day/MTD/YTD P&L recomputed in four places; cash in five read models; contributed capital on Portfolio AND account 3000; two definitions of "collateral posted"; performance stats in careers.py:446-461 AND service.py:655-668 (different return definitions); briefing day_return ignores capital flows (briefing.py:32); balance sheet "Collateral received" (service.py:812) not in NAV.
- Treasury = a TREASURY-type book plus a legacy treasury_cash pool added on top (service.py:740-753, 859-864); the Treasury log records only FUNDED and TREASURY_ALLOCATED (world.py:404, 446-447); _h_capital (world.py:585-598) never logs CAPITAL_CONTRIBUTED.

### A6. Why ~10,000,000 appears in Treasury (confirmed in code)
1. World.create_portfolio defaults capital=D(10_000_000) (world.py:732); POST /portfolios defaults body.get('capital', 10_000_000) (api/server.py:131): fresh money, not a Treasury draw; a TREASURY-type book always takes the fresh path (service.py:332, 342-344).
2. _capital maps 0/''/null to the job's standard capital (service.py:52-60; $1B for SANDBOX, careers.py:40); the UI sends capital:null for a blank box (app.js:273-275).
3. Investor subscriptions are never removed from pf.investors['pending']; _deal re-sends CAPITAL_CONTRIBUTED for every past subscription at every month end (investors.py:131-137; only redemptions removed, investors.py:182).
4. Career missions pay capital bonuses (PM_VAR: 5,000,000, careers.py:115-116, 174-184, 387-393).
5. Deleting a book sends ALL its cash to Treasury, even fresh money (world.py:573-580).
6. UI: "add capital to treasury" (POST treasury/fund, app.js:1908, 1911) never shows the amount in its confirm; repo ticket default face 10,000,000 (app.js:1057); XCCY ticket default notional 10,000,000.
Not causes (checked): principal returned twice, double settlement, capital re-applied on load, reverse-repo collateral counted as cash, variation margin + expiry double posting.

### A7. Other confirmed bugs (fix or make obsolete)
- tags() divides bond marks by 100 twice (service.py:1594, trading.py:255-258).
- OTC schedule never built for CAP/FLOOR/XCCY/COMMODITY_SWAP: unreachable duplicate elif (service.py:1895/1904).
- order_preview creates cash accounts as a side effect (service.py:1684, 1689).
- Non-USD options: mark in local currency with no FX (pnl.py:48) vs cost in base (trading.py:519, 533, 648-650) → phantom P&L.
- After a split, option ids keep the old strike (options.py:1031-1032; 259-261).
- Early assignment random (options.py:760-794); multi-leg packages pre-checked then filled leg by leg (trading.py:346-366), not atomic.
- Futures/options expire only if that exact day is processed (futures.py:81, options.py:704); futures margin-call path never runs (_check_margin_call, futures.py:123, no caller).
- Short bond trades omit accrued interest (trading.py:686, 743 vs settlement.py:154-183).
- Risk: every rates future uses duration 6.5 (SR3 DV01 ~6x too big); BTC/VIX/DX/Nikkei futures booked as SPY equity; foreign securities have no FX factor; cash ladder adds currencies without conversion.
- Front end: market value defined two ways (app.js:1546 vs 828-852); NAV tile vs table disagree (1877 vs 1889); perfStats duplicated (160-177, 1424-1443); "Financing cost" tile always 0 (1273); "Borrow" tile says "n/a, Phase 2" (1162); broken links (#/strategy/<id> 485; state.securities never set 428; tag chips 878; glossary see-also split on commas 970); carry trade = two separate POSTs (1689); ALL BOOKS mode rewrites state.pfId in an awaited loop (1528).
- Six separate order-entry UIs: main ticket (app.js:1177-1227), Quick Look modal (147-157), option ticket (626), option strategy ticket (633-634), Calculations package (1984-1985), and others.

### A8. Pages today (app.js)
Daily News (1369-1376), Portfolio (1378-1443; tabs overview/pnl/performance/career/desk), Open Positions (1445-1564), Live Trades (1566-1602), Accounting (1940-1946), All Books (1871-1914, Treasury controls), Investment Banking (1916-1938), Calculations (1948-1986, 38 strategy templates), Security detail (1152-1175), Position detail (1269-1287), Market Place (1603-1767), Futures (1769-1808), Security & Credit Lending (1809-1818), Options (529-639), OTC Derivatives (640-813), Groupings (815-882), Settings (896-943), Coverage (945-959), Glossary (961-974), Saves (976-990), Risk (991-1027).

### A9. Code you must NOT break
A separate daily-use app ("Desk", package finsim2, same repo) imports these. They must stay importable and behave the same; do not edit finsim2/:
finsim.world.CommandError; finsim.app (installer/launcher helpers); finsim.engines.counterparties.DEALERS, quote_half_width; finsim.engines.otc_pricing; finsim.calendar.us_equity_holidays; finsim.tzfallback.get_zone; finsim.log.get_logger; finsim.engines.risk.STRESS_SCENARIOS; every finsim.quant module. Run the Desk tests (test_fs2_*, test_desk_*) before and after; whatever passed before must still pass.

## B. What to build

### B1. Product
A fast, clean, real-feeling paper-trading terminal. ONE lifetime account (no multiple worlds). Starts with USD 10,000,000 pretend cash; "Add funds" adds more any time. Real current prices (delayed is fine; "accurate enough"). Every position always shows its current mark and live P&L. No simulation, no day advance, no game features. Risk only warns, never blocks.

### B2. Recommended architecture
Build the terminal as a NEW subpackage finsim/terminal/ plus a new UI in finsim/static/terminal/ (reuse style.css to keep the visual style); make python -m finsim serve serve it on port 8765. Reuse correct old parts (money.py, calendar.py, tzfallback, quant/, options_pricing, otc_pricing, counterparties, the Yahoo fetch logic in live.py) instead of untangling the simulation. Leave old simulation modules importable but unwired (A9). New database %USERPROFILE%\.finsim\terminal.db; leave the old finsim.db untouched as an archive.

### B3. Treasury and accounts
- Treasury = central cash pot. Opening 10M and every "Add funds" land there as a CAPITAL DEPOSIT (posted once, never P&L).
- Accounts (each has its own cash per currency and positions; moves between Treasury and accounts are internal transfers, never P&L):
  1. US Exchange: everything traded on US exchanges (stocks, ETFs, ADRs, REITs, preferreds, listed options, futures). Open by default.
  2. Debt Instruments: US bonds (Treasuries, corporates). Open by default.
  3. Credit Instruments: borrowing and lending (cash loans in any currency, repo, securities lending, private credit). Open by default.
  4. Foreign Markets & FX: non-US stocks, foreign bonds, FX spot and forwards. Open by default.
  5. Prime broker accounts: NOT open by default. "Open prime broker account" offers Goldman Sachs, J.P. Morgan, Morgan Stanley, Deutsche Bank, BofA Securities, Barclays, UBS, Citi; each opened PB gets its own sidebar entry.
- No "books" (optional later: a strategy tag on trades).

### B4. Prime brokers
- Each PB has an editable fee schedule (Settings): commission per share/contract, financing on debit balances (policy rate + spread), interest on credit cash, general-collateral and hard-to-borrow rates, OTC dealer spread multiplier, optional monthly minimum. Realistic made-up defaults that differ by PB.
- Through a PB: buy/sell/short stocks (the PB executes and settles DVP into that account, delivering or receiving the shares), borrow stock, margin loan (switchable), and OTC with the PB as counterparty: equity/FX options, forwards, swaps (IRS, TRS, cross-currency). All fees post to the ledger.
- Margin is per PB (a long at one PB never offsets a short at another). Risk shows exposure per PB.

### B5. Automatic sweeps
- Sweep in: when a trade, fee, margin call, collateral call or settlement needs more cash than the account has, exactly the shortfall moves from Treasury automatically, as one internal transfer linked to the trade. Funding order: the account's settled cash → Treasury sweep → (PB only) the PB margin loan if switched on → else refuse with the reason. Foreign currency: Treasury's cash in that currency, else an FX conversion shown in the preview before confirming.
- Sweep out: end of each day, idle settled cash above what the account needs (margin, collateral, pending settlements, small buffer) returns to Treasury. Per-account switch, default on.

### B6. Live prices and continuous marks
- A background quote service, never inside request handlers or under a global lock: keeps quotes for held and watched instruments fresh (every 15-60 s, batched), caches them, serves the UI from cache; on failure keep the last price and retry.
- Positions are revalued from the latest quote on every read; P&L = today vs prior close, unrealized vs cost, realized to date. No daily "update" for marks.
- Sources (free only):
  - Stocks/ETFs/ADRs/REITs/preferreds/indices/FX/futures: Yahoo Finance (query1/query2 v8 chart or spark; ~15 min delay is fine). Futures per contract (ESZ26.CME, CLX26.NYM, ZNZ26.CBT) and continuous (ES=F); FX like EURUSD=X.
  - Listed options: real delayed quotes (bid/ask/last, IV) from https://cdn.cboe.com/api/global/delayed_quotes/options/{SYMBOL}.json (indices with a leading underscore, e.g. _SPX; about 1 request/second) or Yahoo's chain. Fallback: Black-Scholes off the live underlying and the chain's IV.
  - Treasuries: Treasury.gov daily par yield curve (CSV) + Yahoo ^IRX/^FVX/^TNX/^TYX intraday; price each bond off the live curve; TreasuryDirect FedInvest end-of-day prices where available.
  - Corporate bonds: live Treasury curve + a real spread proxy (FRED ICE BofA OAS: BAMLC0A0CM investment grade, BAMLH0A0HYM2 high yield; key from env var FRED_API_KEY).
  - Policy rate: FRED (DFF or SOFR).
  - OTC swaps/forwards/OTC options and private credit: model-priced from live curve, spot and vol.
- Rules: free data only; never Stooq; never bypass anti-bot protections; a browser User-Agent ONLY for Yahoo; .gov sites get a proper project User-Agent; keys only from environment variables, never printed or committed.
- Store each mark's source and time internally (audit/integrity) but show NO source or delay labels in the UI. A stale price may be slightly dimmed.

### B7. Orders and execution
- Market orders fill immediately at the live ask (buy) / bid (sell) in market hours, plus commission (PB schedule, or a default for the plain accounts). Outside hours they queue and fill at the first quote after the open.
- Limit, stop, stop-limit, trailing stop fill when a live quote crosses the trigger (checked on every refresh). DAY expires at the close; GTC keeps working. Cancelled/expired orders stay in Transactions.
- Option packages fill all-or-none.
- The account is the page the ticket is opened from.
- ONE ticket component everywhere: preview (every leg: price, quantity, cash, fees, margin/collateral, settlement date, borrow terms; totals from one price snapshot) → one confirm.

### B8. Hedge pop-up on the ticket (speed is the point)
As soon as a ticker is entered, show ready-to-pick hedges (one click to add; strike/expiry/ratio editable; previewed and confirmed with the main trade; all-or-none where the structure needs it, otherwise the hedge only executes if the main order fills; resized if the main order partly fills):
- Long stock/ETF: protective put (strike ≈ 7% below spot, nearest expiry ≥ 3 months), collar (that put + call ≈ 7% above), put spread, short-futures or equity-TRS overlay sized to notional.
- Short stock: the borrow first (B9), then protective call, call spread, collar.
- Bond: DV01-matched Treasury futures or swap overlay; option on the future where listed.
- FX spot: forward or FX option. Futures: option on the future.
- The 38 strategy templates of the old Calculations page (app.js:1948-1986) become selectable packages here and in the options package builder, keeping their sizing, strike-vs-spot and tenor logic.

### B9. Short selling and securities lending
- A short first locates: availability, borrow rate, rebate, collateral, hard-to-borrow status, recall risk. The borrow executes first; the short sells only if it filled; no borrow → rejected with reason; partial borrow → short scaled down.
- Collateral = 102% of the borrowed shares' current value, marked daily BOTH ways: stock up → collateral call (paid by the Treasury sweep); stock down → excess returned. One movement per borrow per day. Posted collateral stays the owner's asset.
- Recalls only for lender-side reasons (lender sells/needs shares, hard-to-borrow tightening, votes/record dates), never for collateral while 102% is met. Uncovered recall → buy-in.
- Short proceeds restricted until covered. Borrow fees accrue every calendar day. Dividends paid in lieu while short. Cover = buy back, then return the borrow.

### B10. Credit Instruments
Cash borrowing (any currency; lender, principal, fixed or floating = policy rate + spread, day count, term, interest schedule, collateral; draw, accrue daily, pay interest, repay part/all; liability; interest = Financing in P&L). A borrowed currency is settled cash at once and can be spot-converted the same day (FX settles T+2 while the loan accrues from drawdown). Repo (proceeds = collateral value − haircut; roll), securities borrowing and lending, collateral, private credit (floating coupon = policy rate + spread, OID accretion, amendment fees, quarterly payments).

### B11. Settlement and the lifecycle job (real calendar, background)
- A background job on the real calendar (never inside read endpoints): settlement (equities, options, bonds T+1; FX T+2; futures daily variation margin; OTC per confirmation); daily accruals every calendar day incl. weekends (bonds, loans, repo, borrow, private credit, cash interest); dividends and coupons on their real dates; option/futures expiries on the real date (ITM exercise into shares/cash, OTM lapse, assignment of written options); 102% collateral marks; end-of-day sweep-out. Idempotent: each event fires exactly once per instrument, account and date, and on start-up it catches up missed real dates.
- Show settled vs pending cash; only settled cash is spendable unless a rule says otherwise.

### B12. One accounting module
One module exposes pnl(account, period), balance_sheet(account), positions(account), transactions(account, filter), cash(account), treasury(), nav(account) (account = one, or all). Every page and API reads only from it; delete all duplicate computations (A5, A7). "All accounts" consolidates with internal transfers eliminated.

### B13. Ledger integrity, enforced
integrity() after every write and at start-up: every event balances; each account's cash = opening + its entries; assets − liabilities = NAV per account and consolidated; NAV change = P&L + capital flows; each position's MV = quantity × mark × multiplier (contract size, par factor, notional); no fill/settlement/coupon/dividend/accrual/margin/fee/financing twice for the same instrument, account and date; Treasury = capital + retained results − net transfers to accounts; every short has a live borrow and every borrow a short (or an explicit reason). A failure shows on Daily News under "Requires your attention"; the app never silently continues.

### B14. Persistence
- Exactly one server per database: exclusive lock at start (lock file or SQLite exclusive mode); a second server refuses with a clear message; the launcher never spawns a second one (fix A2; don't rely on SO_REUSEADDR).
- Each user action is one transaction (all events commit or none); a failed write rolls back memory and the UI shows the error. Strict replay. One database path. File logging in every launch mode.

### B15. Risk (warn only) and rules/regulations
- Risk page: exposure by class/account/consolidated; delta/gamma/vega/theta by underlying with signs; DV01 and curve exposure (correct per-contract DV01 for rates futures); FX exposure; scenarios (equities ±5/10/20%, rates ±25/50/100 bp, vol ±5 pts, FX ±5%) revaluing actual positions with the same pricing functions as the marks; concentration; financing and liquidity (excess liquidity, margin headroom per PB, recall risk, exposure per PB). Every figure ties to Portfolio.
- Risk only WARNS (ticket preview, Daily News, Risk page); never blocks or cancels. Still enforced (mechanics): a trade that can't be paid for after sweeps/PB financing; a short with no borrow; a recall buy-in. PB margin shortfall → Treasury sweep; if impossible, a loud warning, no automatic liquidation.
- Compliance layer that informs (pre-trade and on holdings), naming the rule in plain English and what a real broker/regulator would require. Verify each against the current rule text: Reg SHO (locate 203(b), close-out, threshold securities, Rule 201 price test after a 10% drop); Reg T / FINRA 4210 initial and maintenance margin; cash-account free-riding and good-faith violations; pattern day trader; 5% beneficial ownership (13D/13G) and 10% (Section 16 short-swing profits); large trader 13h-1; Rule 13f-2 short reporting; 13F at $100M; CFTC futures position limits and accountability levels; listed option position limits; self-trading between the owner's own accounts; tax wash-sale and constructive sale (informational); EU/UK short disclosure 0.1%/0.5%. Data: shares outstanding (SEC), Reg SHO threshold lists (Nasdaq Trader), live price moves, static limit tables.

### B16. Pages and look
- Sidebar, in order: Daily News; Portfolio (all accounts, live P&L, Performance tab); US Exchange; Debt Instruments; Credit Instruments; Foreign Markets & FX; each opened prime broker; "+ Open prime broker account"; Risk; Accounting (consolidated: P&L today/MTD/YTD/inception, balance sheet, transactions with CSV export, cash and financing, Treasury); Settings (PB fee schedules, sweep switches, holidays); Glossary (entries for every new term; fix the comma bug).
- Each account page: tabs Markets (what trades there; ticket on the right with the hedge pop-up), Positions (detail drawer: fills, settlement, cash flows, mark history, linked hedge, linked borrow), Activity/Transactions, Cash & financing.
- Portfolio columns: account, instrument, class, signed quantity (footer: gross long, gross short), average cost, mark, market value, today's P&L, unrealized, realized, income to date, financing to date, % of NAV, days held; grouped by class with subtotals; filters; copy/CSV.
- Performance: daily returns annualised by √252, volatility, Sharpe and Sortino vs the policy rate, beta/alpha by daily regression on SPY, max drawdown, hit rate, monthly NAV-to-NAV table with flows excluded.
- Daily News: real news + "Requires your attention" (integrity failures, warnings, compliance notices, collateral calls, recalls, working-orders count). Numbers only from the accounting module.
- Must feel like a real paper-trading terminal: fast, clean, live-updating quotes without reloads, few clicks, no source/delay labels. Sweep the whole UI for clunky or ugly spots and fix them (no global selector at the top; one small dialog for "Add funds" and "Open PB account"; no long multi-step forms; consistent tables; working links).
- State conventions on screen in one sentence where they matter (day count, settlement, how a mark is chosen), but not data sources.

### B17. Remove
Day advance (buttons, endpoints, scheduler catch-up); the simulated market generator from the runtime path; worlds/saves UI; all game parts (careers, missions, bonuses, investors, investment banking, private equity deal flow, clients/AI desks, institutions, playbook); duplicate tickets (one remains); the pages Open Positions, Live Trades, Groupings, Options, Futures, OTC Derivatives, Calculations, All Books, Investment Banking, Coverage (useful content moves into account pages, Portfolio and Accounting). Every link to a removed page points to its new home.

## C. Engineering rules
1. Read everything first. Write docs/MAP.md (one page: modules, data flow, ledger and cash accounts, Treasury definition, where each figure is computed) before coding.
2. Small commits. Every bug fix and calculation gets a regression test that fails on the old code. Every expected value is worked by hand from the inputs with the arithmetic in a comment; never copy the app's output into a test. Tests use a fake, deterministic quote provider (no network in tests).
3. Run the full suite (FinSim + Desk tests) before every commit; never leave the app unable to start.
4. Keep CHANGELOG.md: one plain-English line per change.
5. Standard library unless unavoidable. No secrets in code, logs or commits.
6. Don't edit finsim2/; keep A9 working.

## D. Acceptance tests (all must pass; hand-worked)
1. Start: Treasury = 10,000,000. Add funds 2,500,000 → 12,500,000; NAV +2,500,000 as a capital flow, P&L 0.
2. Buy 100 AAPL in US Exchange with 0 cash there: the sweep moves exactly 100 × ask + commission from Treasury, linked to the fill; settles T+1; NAV changes only by the commission.
3. Restart the server between every step; nothing lost or duplicated; a second server on the same DB refuses to start.
4. Short 1,000 shares with borrow: borrow first, collateral 102%; stock +10% → call = 1.02 × 1,000 × ΔP paid by sweep; stock −5% → excess returned; borrow fee accrues 3 calendar days over a weekend.
5. Short with no borrow → rejected with reason; partial borrow → short scaled down.
6. Market order outside hours queues and fills at the first quote after the open; buy limit fills only when ask ≤ limit; DAY expires at the close; cancelled/expired stay in Transactions.
7. Long stock + protective put from the pop-up: previewed and confirmed together; partial stock fill resizes the hedge.
8. Option expiry on the real date: ITM long call exercises into shares; OTM lapses; short ITM assigned.
9. Borrow 1,000,000 EUR in Credit, convert to USD at spot the same day: EUR loan liability from drawdown, accruing from that date; EUR cash settled at once; FX settles T+2 (pending EUR payable / USD receivable until then); repaying in EUR later needs a EUR purchase; every balance asserted by hand at each step.
10. Open a Goldman account, buy stock through it: Goldman's commission; DVP into the Goldman account; margin per PB; exposure per PB on Risk.
11. Compliance: short in a stock down 10% today → Rule 201 notice; crossing 5% of shares outstanding → 13D/13G notice; buying in one account while selling the same stock in another → self-trading notice; none blocks.
12. integrity() passes after every step; corrupting one ledger entry makes it fail and shows under "Requires your attention".
13. The Desk tests pass exactly as before.

## E. Deliver
The working app (python -m finsim serve, port 8765), docs/MAP.md, CHANGELOG.md, all tests passing, and a short report: each bug with root cause and fix; every calculation corrected (before/after from tests); what integrity() guards; pages removed and where their content went; UI clean-ups made; test count; anything found but not changed, with the reason.
