# finsim — a miniature institutional financial system

A single-player simulation of how institutional finance actually works, designed
around **one login per day**. Every morning at your update time the simulated
business day is processed whether or not you log in: markets move, your
overnight instructions meet the session, positions are marked and margined,
settlements progress, corporate actions pay, and a daily briefing is written.
In the evening you read the briefing, review the book, and leave instructions
for the next day.

**Two ways to play.** A **career** save plays the real market day by day: one real day is one session,
processed after the 16:00 New York close (17:00 by default) with that day's real closes for stocks, ETFs,
Treasury yields, commodities and FX, the real CPI, jobs, GDP and Fed decisions, and real headlines. Between
updates the screens show the latest real quotes (Yahoo Finance, up to 15 minutes delayed) and you can trade
at any hour: a **live ticket** fills immediately at that quote (a live limit, stop or trailing stop rests against
the quote stream and fills when a refresh finds the quote has reached it), an **instruction** executes at the next
**quote update** — every 15 minutes from 09:45 to 16:15 New York, the delayed quote's own cadence, so 09:45 carries
the open and 16:15 the close — at that quote, so a print you could already see is never yours; a limit or stop is
checked at every update until it fills, and the daily update after the close is only a safety net for a session the
terminal slept through. A career created before saves tracked the real market (simulated prices, no live quotes)
can be switched in place from Live Trades or Settings ("Switch this save to the real market"). Desks whose deal flow is
invented — the private equity pipeline and investment banking — exist only in simulated saves (their own careers, or a
sandbox); a real-market save hides them. Every ticker box (tickets, Calculations, the OTC desk) shows the matching
securities as you type. Such a save its history is overlaid
with the real closes, positions are re-marked at real prices at the next update, and live quotes and the quote updates
come on. The header shows the running engine version (`v0.11.0`); `python3 -m finsim status` prints it too, and
`python3 -m finsim phone off` (or `on`) restarts the server after a `git pull`. The strict overnight-only rule (no tickets from 09:30 New York until the
update) is a switch in Settings, off by default. A **sandbox** save is a world generated from a seed that you
advance yourself. Nothing is ever sent to a broker or an exchange; the market data is read, never traded
against (see *Where the data comes from*).

**This is a simulation.** It never connects to a broker or exchange while you play. The universe
is a snapshot of real listed companies, ETFs, representative bonds of real issuers and real institutions as
counterparties (`finsim/data/universe.json`, dated inside the file): starting prices, volumes, volatilities,
betas, dividend yields and fundamentals come from Yahoo Finance and SEC EDGAR at the snapshot date, and the
initial yield curve from Treasury yields that day. From day one every price is generated from a seed. Nothing
that happens in the game — a default, a dealer failure, a takeover, a client's request — is a statement about
the real company or institution; they are names on a simulated screen. Refresh the snapshot with
`python3 tools/refresh_universe.py` (needs internet; not part of the game).

How the snapshot becomes a world: the seeded prehistory (260 sessions) is generated as before and then rescaled so
that every series ends exactly at the snapshot — prices, commodity spots, FX, and the curve (bonds at the price the
snapshot curve implies), the ES and ZN futures riding their sources, and the bond issuers' ratings and credit
spreads reset to the snapshot's after the prehistory's random migration — and the start date's session closes at
those values. From the next session on, the simulation moves them. Saves made before this universe (format v2) are migrated on load: the fictional names are
rewritten to their real successors and their stored prices are kept, so such a save is a hybrid; start a new save
to play from today's real prices.

```
python3 -m finsim install       # once: FinSim as a local app (see below); opens the window
python3 -m finsim serve         # or run it by hand: http://127.0.0.1:8000  (terminal UI + JSON API + daily scheduler)
python3 -m finsim demo          # a scripted week: instructions, fills, settlement, dividend, futures, audit
python3 -m unittest discover -s tests
```

No third-party packages are required (see *Environment note* below).

### As a local app

`python3 -m finsim install` turns the simulator into an app for the current user only. It registers the server as
a background service that starts at login (launchd on macOS, `systemd --user` on Linux, Task Scheduler with a
Startup-folder fallback on Windows) and adds a **FinSim** launcher: `~/Applications/FinSim.app` on macOS, an
app-menu entry on Linux, a desktop and Start Menu shortcut on Windows. The launcher runs `finsim open`, which
starts the server if it is not already up and opens the terminal in its own window (Chrome, Edge, Brave or
Chromium in app mode with a profile of its own; otherwise the default browser). The browser's own *Install app*
works too, from the web manifest. The server listens on 127.0.0.1 only: nothing is exposed to the network.

Saves live in `~/.finsim/finsim.db` (override with `FINSIM_HOME` or `FINSIM_DB`; the port with `FINSIM_PORT`,
default 8765). The first time the app starts it carries over a `data/finsim.db` left by `serve`, so careers
begun before the app continue. Code updates never touch the saves: a `git pull` changes only the code, the
database is an append-only event log, and older save formats are migrated on load. Before opening the
database the server takes a consistent snapshot into `~/.finsim/backups/` (the newest ten are kept), so any
update can be undone by stopping the server and copying a snapshot back over `finsim.db`. `python3 -m finsim status` shows what is running and where; `python3 -m finsim uninstall`
removes the service and launcher and keeps the saves (`--purge` deletes them). The service starts from the
repository checkout, so keep it where it is or run `install` again after moving it.

### The pages (2026-09 rebuild)

The nav is one entry per thing you do, each page with tabs; older links (`#/home`, `#/trading`, `#/book`,
`#/treasury`, …) redirect to their new home.

| Page | What is there |
|---|---|
| **Daily News** | the briefing (the login screen), the news feed, macro & calendar |
| **Portfolio** | overview (NAV, exposure, positions), P&L (today by source and by position, income statement), performance (total/annualised return, vol, Sharpe, Sortino, drawdown, hit rate, alpha/beta vs the benchmark, growth of 100, monthly table), career, my desk |
| **Open Positions** | everything open in every book as spreadsheet grids: securities, futures, listed options, OTC trades, securities borrowed, repo, FX forwards, private-credit loans, commodity inventory; a **History** toggle brings back every fill, instruction, strategy and FX deal; Copy/CSV per table or for the page |
| **Live Trades** | today: working instructions (cancel here), option packages working all-or-none, fills, OTC dealt today, open RFQs, settlements due or failing, calls to meet, a quick ticket |
| **Groupings** | everything open grouped by the name it is on — SPY shares with SPY/SPX options and ES futures, a short with its loan and its calls, a bond with its repo and CDS, rates swaps together, each currency's forwards and swaps |
| **Market Place** | where you trade, with the ticket beside the tables. Six tabs: **equities & ETFs** (500+ stocks, 127 ETFs, 79 ADRs, REITs, preferreds), **bonds & rates** (the Treasury ladder from 3-month bills to the 30-year and STRIPS, 130 corporates, 20 USD sovereigns, munis, agencies), **FX** (18 currencies and the dollar index), **commodities**, **crypto** (28 coins and stablecoins, crypto futures, live 24/7) and **indices & vol** (SPX/NDX/RUT/DJIA levels, the VIX and DXY, every futures term structure). Equities: sort by gainers/losers/volume/yield/vol, filter by sector and class; a row loads the ticket, *chart* opens the quick look), **bonds & rates** (curve, history, spreads, the bond universe), **foreign exchange** (see below), **commodities** (spot, curve shape, inventories, reports; the front month loads the ticket). Sandbox saves force the regime here. |
| **Options** | the chain and ticket, strategies, positions & Greeks, expirations, exercise, vol surface, margin; a ticker box jumps to any underlying's chain |
| **Futures** | every listed contract on every underlying (ES/NQ/RTY/YM, ZT/ZF/ZN/ZB/SR3, VX, DX, BTC/ETH, 21 commodities) with notional, margin and expiry; your futures with variation margin; the ticket; calendar spreads; physical delivery, inventory and deliveries |
| **OTC Derivatives** | RFQ to seven dealers, 28 products: swaps, OIS, FRAs, caps/floors, swaptions, inflation swaps, cross-currency, FX forwards, NDFs, FX swaps, FX options, TRS, dividend/variance/volatility swaps, barrier and digital options, CDS on 150+ issuers and sovereigns plus CDX/iTraxx indices, commodity swaps, forwards and Asian options, crypto perpetuals, and three structured notes (principal-protected, reverse convertible, autocallable); every request takes a #tag — **OTC equity options** (any strike/expiry on any name or SPX), **FX options**, **equity forwards**, **commodity forwards** and dealer **FX forwards**; a product finder (“fx option”, “forward”) picks the product; blotter, risk, counterparties, ISDA/CSA, upcoming |
| **Treasury** | the save's cash pool. A new save puts the whole starting capital in the Treasury (the default in the new-save dialog); every book starts empty and draws what it needs (the Portfolio page offers it, All Books has the controls, a new book can draw its capital at creation), returns what it can spare (settled cash after everything pending), and a deleted book's cash goes back. Fresh capital can be added to the Treasury; the total NAV on All Books is books plus Treasury. GET /worlds/{w}/treasury, POST /worlds/{w}/treasury/{fund,allocate,return} |
| **All Books** | the whole save as one big book, named after the world: every book's NAV, day / MTD / YTD / since-inception P&L, cash, long, short, net exposure, leverage and open items side by side with totals; positions added up across books with the split by book underneath; the day's P&L by bucket and cash by currency for all books together. The book selector's first entry (★ ALL BOOKS) opens it; a flat book (nothing held, working or owed) can be deleted from here — never the last one, and its history stays in the event log |
| **Security & Credit Lending** | securities lending (locate → borrow → short), repo, collateral & prime brokerage, and **private credit**: a monthly pipeline of loans to sponsor-backed private companies (first lien, unitranche, second lien, mezzanine; OID, spread, covenants, leverage), commit from $1m, quarterly floating coupons, daily marks off a loan-market spread, rating migration, covenant amendments, defaults and workouts, secondary sales at a bid; and **private equity**: monthly deal flow (founder-owned businesses, carve-outs, sponsor secondaries via auctions or proprietary talks), bid an EV/EBITDA multiple with a chosen leverage (senior term loan at SOFR + spread, second lien above the senior cap; lenders cap leverage by sector and cycle), diligence that reveals the quality-of-earnings adjustment, quarterly operating reports (growth, margin, free cash flow, debt paydown, covenants with equity cures or a restructuring), value creation (cost programme, growth plan, new CEO, add-ons), dividend recaps, sale and IPO exits, quarterly marks that lag the public comps, DPI / RVPI / TVPI / IRR |
| **Risk** | VaR, expected shortfall, stress, liquidity, limits, history |
| **Accounting** | general ledger and balance sheet, settlements & custody, cash & treasury (cash ledgers, liquidity projection, movements), audit trail |
| **Calculations** | the strategy playbook: 60 ready-made packages, each with what you are trading in one sentence, why, when it fits and the risk in words (protective puts at several strikes, tail hedge, collars incl. zero-cost, put-spread hedge, protected shorts, short collar, call-spread hedge, covered and partial covered calls, cash-secured put, bull/bear call and put spreads, straddles, strangles, iron condor, synthetic long/short/protected TRS, core-plus-overlay, delta-neutral TRS, long/short/protected futures, relative value long/short and protected). Pick one, set underlying, units, tenor and per-leg strikes, **preview** every leg off today's quotes with Greeks, margin, max gain/loss, breakevens and the payoff chart, then **execute** the whole package in one click |
| **Groupings** (by #tag) | a second tab: every fill, working instruction, OTC trade and FX deal that carries the same #tag, with its P&L — the tag is typed on any ticket, FX deal, OTC request; playbook strategies tag their legs |
| **What you can trade** | the master checklist, item by item: built, partial (with the simplification named) or not built |
| **Settings, Glossary** | presets per page: the ticket's settlement currency, the calculations defaults, the Market Place tab and sort, dark or light theme, and — for a career — this save's update time and timezone; every term the screens use |

**Every ticket has a currency.** *Settle in* names the currency that pays for a buy or receives a sale;
when it is not the security's currency the conversion is dealt at spot alongside the instruction and
settles T+2, so euros or yen sitting in the book can buy dollar assets, and a sale can land in another
currency. The estimate line shows both legs and the cash balances by currency.

**Search on every page.** The header box (press `/`) matches tickers, names, bonds, contracts and commodities;
Enter opens a **quick look** — candle chart with moving averages and a crosshair, the quote, a ticket, and
links to the full page, grouping, options chain and calculations — without leaving the page. Each page has its
own filter box for its tables, the options page a ticker box for chains, the OTC page a product finder.

**Foreign exchange.** The FX tab shows each currency against the dollar the way the market quotes it (EUR/USD,
USD/JPY), with day/week/month changes, the foreign short rate, carry versus the dollar, realised vol, the
deviation from its long-run level, forward points at 1M/3M/6M/1Y (covered interest parity), and a
decomposition of today's move into the model's drivers: carry drift, the currency's beta to the equity
factor (AUD and CAD risk-on, JPY and CHF havens), mean reversion, and the residual flow. A chart per pair,
the rate differential, the forward curve, a cross-rate matrix, your cash and exposure per currency, and
spot and forward tickets beside it; currency options and margined forwards are on the OTC desk.

**Track the real market.** Career saves do this by default; a sandbox can too (market source *REAL*). The world fetches real daily
closes (Yahoo Finance; stocks, ETFs, ADRs, Treasury yields, commodity front months, FX), stores a year of
history in the save as one event (replay never touches the network), starts on the last close, and pins every
session it advances into to that day's real closes — bonds off the real curve, options off the vol surface,
futures off spot, OTC valuations and P&L all follow. It can only advance into sessions the market has already
closed; the header badge says which close it is waiting for. Regime and scenario still drive news and vol.
Simulated saves are unchanged.

### Settings, glossary, saves

**Settings** (in the nav) sets how every page opens: the options page's default underlying, expiry tenor (e.g.
three months out, snapped to the nearest listed expiry), contracts, order type and tab; the OTC page's default
product, tenor and notional; the trading ticket's sizes and order type; the chart period; the home page. Two
modes: always start from the presets, or remember where you were while the app is open. Changing an option's
underlying never changes the expiry (the same date is kept and snapped to that name's listings) and trades
re-render the same chain. Settings live in the browser, so each device keeps its own. **Glossary** defines every
instrument you can trade and every term the screens use, with how the simulator handles it; dotted-underlined
words on the pages link into it. **Saves** (header button) lists the worlds and deletes one after you type its
name; a database snapshot is taken every time the server starts.

### On your phone

The game keeps running on the computer; the phone is a second screen for it. `python3 -m finsim phone` opens
the server to your network behind an access key and prints a link like `http://192.168.1.20:8765/?key=…`.
Open it on the phone while it is on the same Wi-Fi, then *Add to Home Screen* from the browser's share or menu:
the link keeps the key, so from then on it opens like an app (the page has a phone layout, a manifest and
home-screen icons). Every API call from another device must carry the key (header `X-FinSim-Key` or `?key=`);
without it the server answers 401 and the page asks for the key once. The page and its assets are served to
anyone who can reach the port, the data is not. The desktop launcher on the same machine needs no key.

The computer must be awake, and the phone on the same network. For anywhere-access install
[Tailscale](https://tailscale.com) on both devices: `finsim phone` also prints the Tailscale link, and nothing
is opened to the public internet. `python3 -m finsim phone off` goes back to this machine only; the key lives
in `~/.finsim/access-key` (delete it to rotate). Running `serve` by hand with a non-local `--host` refuses to
start without `--key`.

### Where the data comes from

| Data | Source | When |
|---|---|---|
| Daily closes: stocks, ETFs, ADRs, REITs, preferreds | Yahoo Finance's public quote endpoints (`query2.finance.yahoo.com`, the `spark` and `chart` JSON; no key, browser-style user agent) | career saves: every session; the universe snapshot: `tools/refresh_universe.py` |
| Treasury yields (13-week bill, 5, 10 and 30 years) | Yahoo (`^IRX`, `^FVX`, `^TNX`, `^TYX`) | every session |
| Commodity front months, FX | Yahoo (`CL=F`, `GC=F`, `ZC=F` …; `EURUSD=X`, `JPY=X` …) | every session |
| The daily newspaper (`site/reports`, this repository's own editions) | read from disk by `engines/realnews.py: NewspaperNews`: the AM and PM editions of the session day, the political, economic, business and markets sections (Top Stories, What Changed Today, The Economy, Business, What Moved Markets, Winners & Losers, Tomorrow) — never science, technology or local; each story's headline and deck, linked to the published page | career saves: when a session is processed, ahead of the wire |
| Live quotes (the live view, live tickets, live FX deals) | the same Yahoo `spark` endpoint's metadata: regular-session price and time, up to 15 minutes delayed on most exchanges; cached one minute in memory, never written to disk | career saves: whenever a page or ticket asks |
| Crypto spot (28 coins, stablecoins) | Yahoo (`BTC-USD`, `ETH-USD` …); circulating supply is an approximate table for market-cap tiers | career saves: every session, and live around the clock |
| The dollar index and the VIX | Yahoo `DX-Y.NYB` and `^VIX` | every session (DX and VX futures ride them) |
| 18 currencies | Yahoo (`SEK=X`, `MXN=X`, `BRL=X`, `CNH=X`, `KRW=X`, `INR=X` …) | every session |
| Shares outstanding, fundamentals, sector | SEC EDGAR company facts (`data.sec.gov`, with the required user agent) | the universe snapshot only |
| CPI, core CPI, unemployment, payrolls, GDP, retail sales, core PCE, the Fed's target range | FRED, the St. Louis Fed's public CSV endpoint (`fred.stlouisfed.org/graph/fredgraph.csv`; no key) | career saves: stored when the save is made, refreshed every six hours |
| Headlines | Yahoo Finance news search for the market and the names in your books; the Federal Reserve's press-release feed | career saves: fetched when each session is processed, stored in the save |

Everything fetched is stored in the save as events, so replaying a save never touches the network; the
cache lives under `~/.finsim/`. Corporate bonds are representative issues priced off the real curve and
the issuer's rating, not quoted bonds; options are priced by the simulator's vol surface off the real
closes; the economic release dates follow the usual calendar (CPI around the 12th, jobs the first Friday,
GDP near the end of the following month) rather than each agency's exact schedule, and no consensus
figures are fetched. Earnings, ratings and counterparties remain the simulation's own.

### Playing with a friend

The game runs on your computer, so there are three ways to share it:

1. **Same computer, different saves.** Every save is a separate world; a friend playing on your machine
   makes their own save from the *+ Save* button (their instructions, their book, their briefing).
2. **Your computer, their phone or laptop.** `python3 -m finsim phone` opens your server to the network
   behind an access key and prints the link; on the same Wi-Fi they open it and pick or create their save.
   For anywhere-access install [Tailscale](https://tailscale.com) on both machines and use the Tailscale
   link it prints: nothing is exposed to the public internet, and the key is required for every call.
   Your computer must be on for the daily update to run (it runs at 17:00 New York whether or not anyone
   is logged in).
3. **Their own copy.** They clone the repository and run `python3 -m finsim install`: the same game, their
   own saves, no dependency on your machine. Careers on two machines see the same real market, so you can
   compare books at the end of a month.

The saves database (`~/.finsim/finsim.db`) can also be copied to hand a whole world to someone.

## The daily cycle

A save is a job. Career saves run in **real time**: one calendar day is one
simulated business day, aligned to the real date, processed at 09:00 in your
timezone (both configurable). Weekends and exchange holidays do not process a
session, but Monday's update carries three days of commodity/news event risk.
If the server was off or you were away, the world catches up day by day the
next time it runs (a scheduler thread checks every minute; every API call also
checks). Sandbox saves ignore the clock and advance on demand.

`SimulationEngine.run_daily_process(d)` is the whole day, in order:

1. macro/regime update, commodity fundamentals, news
2. reprice equities, bonds, the curve, credit spreads, commodity curves and every
   listed futures contract; list new contract months
3. execute the instructions left overnight against the session (open/high/low/close
   and volume): market, limit, stop, stop-limit, take-profit, trailing stop, and
   any order carrying a *condition* on a security close, a curve tenor yield
   (`CURVE:10Y`) or a commodity spot (`SPOT:CL`); partial fills above 20% of
   session volume; good-for-day = good for the next session
4. post-trade lifecycle and settlements due today (fails retry daily)
5. corporate actions: ex/pay dates, coupons, maturities
6. futures: expiries auto-close, variation margin, initial-margin sweep, margin
   calls, forced liquidation after three unpaid days; bond and cash interest accruals
7. mark positions, check the job's risk limits, snapshot NAV with a P&L explain
   that reconciles to the ledger, expire good-for-day orders, generate period-end
   performance reviews
8. write the **daily briefing**: NAV and today's P&L by bucket (equities,
   commodities, rates, credit, dividends, bond interest, financing, fees), what
   happened in markets, today's news, position movers, what your instructions did,
   and a "requires your attention" list (margin calls, overdrafts, failed
   settlements, limit breaches, expiring contracts, settlements and cash flows due,
   ex-dates, reviews, regime changes)

Nothing requires intraday attention; the challenge is positioning, hedging,
funding, liquidity and risk over days and months.

### What is built

| Area | What exists |
|---|---|
| **Saves & careers** | Multiple independent saves. Jobs: Sandbox, Portfolio Manager ($100MM vs equity benchmark), Global Macro Trader ($250MM; rates, index and commodity futures, government bonds), Commodity Trader ($100MM; energy, metals, ags, livestock), Fixed-Income PM ($250MM; Treasuries, corporates, note futures vs a 5Y benchmark). Each job fixes capital, mandate (instrument classes enforced at order entry), risk limits (gross leverage, single-position %, drawdown), benchmark and a promotion ladder. Phase 8 adds the Hedge Fund Manager ($500MM, outside investors), Bank Rates & Credit Trader, Derivatives Trader, Securities Lending Trader, Repo/Funding Trader, Corporate Treasurer and Risk Manager (four AI desks), each with missions, plus crisis mode. |
| **Reviews & progression** | Month, quarter and year-end reviews from the book's own history: return, benchmark, alpha, max drawdown, Sharpe, largest contributor/loss, P&L by bucket, risk breaches, settlement failures, margin calls missed, a rating and an evaluation. EXCEEDS at a quarter end promotes and allocates capital; two UNACCEPTABLE quarters demote. |
| **Market engine** | 171 real names: 138 stocks across all eleven GICS sectors, REITs, ADRs (TSM, Toyota, ASML, Novo Nordisk, Alibaba, SAP, Shell), a bank preferred, 19 ETFs (SPY, QQQ, IWM, DIA, sector ETFs, GLD, SLV, USO, TLT, IEF, LQD, HYG, BND, SHV, VNQ, EEM, EFA) with real fundamentals and liquidity tiers; 4 on-the-run Treasuries and 15 representative corporate bonds from JPMorgan, Microsoft, Apple and Exxon (A/AA) through Ford, Boeing, GM, Wells Fargo, Verizon, AT&T, CVS, Kraft Heinz and Occidental (BBB) to American Airlines, Carnival, Royal Caribbean, Warner Bros. Discovery, Delta and Moderna (BB/B). Optionally the real market's closes (see *Track the real market*). Factor-model returns (market + sector + idiosyncratic), Nelson–Siegel curve and IG/HY spreads shocked on the same factor, five Markov regimes, a volatility index, ex-dividend price drops. |
| **Commodities** | 21 commodities (WTI, Brent, natural gas, gasoline, heating oil; gold, silver, copper, platinum, palladium, aluminum; corn, wheat, soybeans, coffee, sugar, cotton, cocoa; live cattle, feeder cattle, lean hogs) each with a supply/demand state: cyclical demand, decaying supply shocks that arrive as news, inventories that accumulate the balance and jump on scheduled reports (EIA weekly, USDA/LME monthly). Prices respond to changes in the balance, the macro cycle and seasonality; the **futures curve** is cost-of-carry with a convenience yield that rises when inventories are tight, so shortages backwardate and gluts contango. A COMMODITIES desk page and a page per commodity show spot, curve (today / 5d / 1m ago), contracts, fundamentals and news. |
| **Futures** | Real month codes (CLZ26), per-commodity listing cycles and expiry rules, contract multipliers, ticks, margins; equity-index (ES on SPX) and 10Y note (ZN) futures by carry. Long or short; commissions per contract; **daily variation margin** moves cash and posts to income; initial margin is swept to a clearing account at a regime-dependent rate; margin calls when cash is overdrawn; forced liquidation after three days; positions auto-close on the last trade date so nobody takes delivery. |
| **Trading** | Market-on-next-update semantics; spread crossing and square-root impact; participation caps; trailing stops that ratchet; conditional orders evaluated at the close; FIFO lots; pre-trade cash, position, margin and mandate checks. |
| **Ledger & operations** | Institutional chart of accounts with margin-deposit and futures-P&L accounts; trade-date accounting; balanced journal entries with a security dimension; trial balance and balance sheet. Trade lifecycle to settlement, RVP/DVP instructions, configurable settlement cycles, holiday calendar, fails with retry, separate cash and custody movements. Dividends, coupons, maturities, daily interest accruals. |
| **Audit** | Append-only sqlite event log; every event has a cause; state (including briefings, reviews, futures margin) is rebuilt by replay and tested to be identical. |
| **UI** | Fifteen pages with tabs (see *The pages* above): Daily News, Portfolio, Open Positions, Live Trades, Groupings, Market Place, Options, Futures, OTC Derivatives, Security & Credit Lending, Risk, Accounting, Calculations, Settings, Glossary; a quick-look chart and ticket from the search box on every page. New-save dialog picks job, clock mode, timezone, update time, market source (simulated or real closes), initial regime and scenario. |

### Phase 2 — financing (built)

| Desk | What exists |
|---|---|
| **Collateral engine** (`engines/collateral.py`) | One schedule of eligibility and haircuts by asset class and purpose (repo, lender collateral, prime-broker financing), scaled in stressed regimes; a pledge registry so a security in custody is either sellable or pledged, never both; cash collateral through an explicit posted-collateral account; one call object for every source with issue and due dates. Dashboard: available, encumbered, posted, received, calls. |
| **Securities lending** (`engines/seclending.py`, `engines/lending_market.py`) | A lending market per stock (lendable supply, other borrowers, utilization, rate = base + utilization curve + volatility + stress + specials that arrive as news). Locate (partial availability, expires next session, creates no borrow) → borrow (shares into custody, 102% cash or Treasury collateral out, rebate on cash) → short sale delivers the borrowed shares (short lots at proceeds, `2600` at proceeds, `2610` MTM) → daily fee accrual, daily collateral marks, daily re-rating to the market → manufactured dividends over ex-dates (`5400`) → recalls with a 2-business-day deadline (replacement borrow, buy to cover, or return) → unmet recalls end in a lender buy-in with a penalty → covered borrows return automatically once the shares are back, fees settle. |
| **Repo** (`engines/repo.py`) | Repo (cash in, securities encumbered, exposure retained) and reverse repo (cash out, collateral received). Overnight with auto-roll at the new rate, term, and open. Rates by collateral quality and regime; haircuts from the collateral engine; daily interest; daily marks against the haircut-adjusted requirement raise a call due next cycle; unmet calls are unwound by the counterparty. Post collateral, post cash, reduce, substitute (old released only when the replacement covers), close. |
| **Prime brokerage** (`engines/prime.py`) | Base-currency cash never stays negative: shortfalls become an explicit margin loan at policy + 100bp, idle cash repays it. Financing value of unencumbered longs at financing haircuts plus cash, less loan and 30% of short market value = excess liquidity. Purchases, futures margin and cash collateral are financeable up to excess. Negative excess raises a call; a second unmet cycle liquidates futures then unencumbered longs through ordinary forced trades. |
| **FX** (`engines/fx.py`, `engines/fx_market.py`) | USD, EUR, GBP, JPY, CHF, CAD, AUD cash ledgers held in local units with base-currency carrying values (`1010:CCY`); spot rates and a short rate per currency in the market; spot trades settle T+2 through FX receivable/payable, realising against carrying value; daily retranslation to unrealised FX; forwards priced by covered interest parity, marked daily and physically settled at maturity; foreign cash earns or pays its own rate. |
| **Briefing & attention** | Financing (repo balance and average cost, borrow expense today, PB loan and cost, excess liquidity), Collateral (posted, received, available, calls due, Treasury encumbrance), Short book (market value, borrow cost, hard-to-borrow count, recalls, positions), FX (balances, net exposures, forwards). Attention items: margin/collateral calls, recalls, buy-ins, borrow-rate spikes, term repo maturing tomorrow, collateral concentration, negative foreign cash, margin loan drawn, forwards settling. |
| **P&L explain** | Buckets: equities, commodities, rates, credit, fx, dividends, dividends paid in lieu, bond interest, cash interest, securities borrow (fees less rebates and buy-in penalties), repo financing, prime-broker financing, fees. Still reconciles exactly to the NAV change every day. |
| **Scenario control** | Sandbox worlds can force the next day's regime (`POST /force-regime`), which is how the stress scenario and tests are made deterministic. |

The definition-of-done scenario ($100MM → Treasuries → repo → equities → locate → borrow → short → proceeds settle → fees and marks → dividend while short → partial recall and replacement borrow → liquidity crisis → haircut up → collateral call → post collateral → cover → return → repay repo → replay) is `tests/test_financing.py::DefinitionOfDoneTest`, deterministic under seed 42.

### Phase 3 — listed options (built)

| Piece | What exists |
|---|---|
| **Instruments** (`engines/options.py`) | Contracts are securities (`asset_class OPTION`) with a deliverable abstraction: equity/ETF/ADR/REIT options on large and mid caps deliver 100 shares (American, physical); index options on `SPX` (10× the SPY level) are European and cash-settled. Chains list deterministically: four monthlies plus two quarterlies on third Fridays (rolled to a business day), 17 strikes around spot at a price-dependent step, re-listed as spot moves; contracts expire in the log. |
| **Vol surface** (`engines/vol.py`) | Per underlying: 30-day ATM IV, skew, curvature and term slope. ATM mean-reverts toward structural vol, 20-day realized vol, the market volatility index and the regime, jumps with the vol index and is persistent; skew steepens and the term structure inverts in stress. Stored in the market close so replay is exact. |
| **Pricing** (`engines/options_pricing.py`) | Black–Scholes–Merton for European contracts; Cox–Ross–Rubinstein tree with early exercise and a European control variate for American ones; Greeks (delta, gamma, vega per vol point, theta per day, rho per bp) analytic or read off the tree; intrinsic/extrinsic; no-arbitrage bounds; implied vol. Verified against put–call parity, finite differences and American ≥ European. |
| **Quotes & liquidity** | Theoretical mid wrapped in a spread that widens with distance from the money, tenor, the underlying's tier and regime stress; seeded volume and open interest; synthetic session bars derived from the underlying's open/high/low/close, so option orders use the same order types, participation cap and impact model as everything else. |
| **Orders & accounting** | BUY/SELL open or close through the ordinary order engine (market, limit, stop, stop-limit, take-profit, DAY/GTC, conditions). Premium × 100 settles T+1 through the settlement engine; $0.65/contract commission. Long options at cost (`1700`) with MTM (`1750`); written options as a liability at premium received (`2900`) with MTM (`2910`); FIFO lots; realized on close. Clearing chain exchange → clearinghouse → clearing member. |
| **Margin** | Transparent rules: long — none; covered call — none, and the covering shares are reserved (cannot be sold, lent or pledged); vertical (same expiry) — strike width; naked — premium + 20% of the underlying − OTM amount, floor 10%; × regime multiplier. Swept with futures initial margin to the clearing account (`1300`), financeable by the prime broker; unmet calls end in forced liquidation that buys written options back. |
| **Exercise, assignment, expiry** | Holder exercise (American) books an ordinary trade in the underlying at the strike that settles through custody and cash, and closes the contract. Seeded assignment on short American contracts: deep ITM puts (rational), calls mainly the day before an ex-dividend date when the dividend exceeds the remaining extrinsic. Expiry: OTM expires worthless, ITM (≥ $0.01) auto-exercised/assigned, index contracts cash-settled at intrinsic; briefing warns three sessions ahead with exactly what will happen. |
| **Strategies** | Tickets for covered call, protective put, bull/bear call and put spreads, straddles, strangles, calendar, butterfly, iron condor, collar, synthetic long/short: parent strategy with child legs, a net debit/credit limit per unit, all-or-none execution at the next open, analytics (net premium, max gain/loss, breakevens, payoff, Greeks, margin). |
| **Corporate actions** | Stock splits (sandbox `POST /force-split`) scale history, positions, lots, loans and pledges; whole ratios multiply contracts and divide strikes, other ratios adjust the deliverable; every contract adjustment is an event. |
| **P&L** | Exact ledger P&L in an `options` bucket; an approximate Greek attribution (delta, ½ gamma dS², vega × dIV, theta × days) from the previous close's Greeks with the residual shown and labelled approximate. |
| **UI** | OPTIONS page: chain with ticket, positions & Greeks by contract and underlying, strategies (preview, place, detail with payoff chart), volatility (term structure, skew grid, IV history, IV rank), expirations, exercise/assignment log, margin detail. Option contract and option position pages; derivatives section and attention items in the briefing. |

The deterministic options scenario (shares → covered call → protective put → index iron condor → naked put → early exercise → 2-for-1 split → recession → expirations → replay) is `tests/test_options.py::OptionsDefinitionOfDoneTest`.

### Phase 4 — OTC derivatives, dealers, ISDA/CSA (built)

| Piece | What exists |
|---|---|
| **Dealers** (`engines/counterparties.py`) | Six real dealers (Goldman Sachs, J.P. Morgan, Morgan Stanley, Citigroup, Barclays, Deutsche Bank) with rating, CDS spread, capital, product coverage, quoting width (per product), lean and style. Credit state moves daily with the credit cycle and seeded shocks (news, downgrades), stored in the market close. |
| **RFQ** (`engines/otc.py`) | Request-for-quote per product: the engine's fair mid (par rate, forward, premium, basis, financing spread, market CDS spread, strip average) and each dealer's bid/ask from its width × regime × funding stress, with the player's executable level and cost vs mid; stressed dealers decline. Quotes expire at the next update. Executing books the trade under that dealer's ISDA/CSA if initial margin and any upfront are financeable. |
| **Products** (`engines/otc_pricing.py`) | Interest-rate swaps (fixed 30/360 vs 3M term rate ACT/360, schedules rolled to business days, fixings, coupons, par rate, DV01), FRAs (fixing, discounted settlement), caps/floors (Black-76 caplets on a regime-driven rate vol, periodic payoffs), European swaptions (Black-76 on the forward par rate; physical exercise into a booked swap or cash settlement; expiry), cross-currency swaps (initial/final notional exchange in both cash ledgers, quarterly interest in each currency, valued against the market basis), equity total return swaps (monthly resets of price change + dividends less financing at policy + spread), single-name CDS (running 100/500 with upfront, quarterly premium, accrual, CS01, protection at recovery on a credit event), commodity swaps (period average of spot vs fixed, priced off the futures strip). |
| **Accounting** | Dirty PV as derivative asset (`1800`) or liability (`2800`) with daily MTM (`4900`); every cash flow realised (`4910`); P&L on a payment day nets to the accrual already recognised. Buckets: rates, FX, equities, credit, commodities. |
| **ISDA / CSA** | One netting set per dealer: threshold, minimum transfer amount, independent amounts by product. Daily variation margin either way in cash (posted → `1400` ref `CSA:<dealer>`; received → cash + liability `2450`), initial margin posted and segregated (`1400` ref `IM:<dealer>`), the dealer's IM segregated at a third party (memo). Unfundable margin raises a CSA call; one unmet cycle and the dealer closes the netting set out at mid less unwind costs. |
| **Counterparty risk** | Per dealer: gross/net MTM, netting benefit, VM/IM, current exposure (net MTM − VM held + VM posted), PFE (current + notional × product add-on × √T scaled by net-to-gross), 1y PD from the dealer's CDS, expected loss; concentration attention item above 5% of NAV. Counterparty default (sandbox `POST /default-counterparty`, and the hook for the macro engine): early termination at mid, collateral applied, unsecured claims recover at the recovery rate, IM returned. Credit events on issuers (`POST /credit-event`) settle CDS. |
| **UI** | OTC page: RFQ (product forms, quotes ranked by cost vs mid, dealer table), blotter with per-trade drill-down (analytics, terms, cash flows, schedule, ledger entries, unwind), risk (DV01, vega, CS01, deltas), counterparties (exposure table, dealer CDS history), ISDA/CSA (terms, balances, calls, collateral movements), upcoming events. Briefing section and attention items (CSA calls, close-outs, concentration, dealer credit news, expiries and exchanges). |

Simplifications, stated: single zero curve for all discounting and forwards (no OIS/term basis); foreign legs discounted at a flat foreign short rate plus the market basis; flat hazard rates; lognormal rate vol from a regime table; dealer IM received is off balance sheet; no compression, novation or clearing of OTC trades.

### Phase 5 — risk (built)

| Piece | What exists |
|---|---|
| **Exposures** (`engines/risk.py`) | Every position, OTC trade, FX balance and forward reduced to linear sensitivities: equity dollars per security (and beta dollars), DV01, CS01 (IG at half the HY index shock), option vega and gamma dollars, rate vega, commodity dollars by code, FX dollars by currency. |
| **VaR / ES** | 1-day historical simulation: the last 250 sessions of factor changes (each security's return, 5Y rate, IG/HY spreads, commodity spots, FX spots, ATM implied vols) replayed through the sensitivities. VaR 95/99, expected shortfall 97.5, parametric VaR from the same series, 10-day scaling, worst/best historical day, component VaR (Euler), the P&L series. The method is printed with the numbers. |
| **Stress** | Eleven named scenarios (equity crash, rates ±100, credit +200, vol spike, commodity sell-off, oil spike, strong dollar, 2008-style, 2020-style, stagflation) plus custom shocks, with worst contributors per scenario. Not full revaluation; stated. |
| **Liquidity** | Days to liquidate every position at 20% of ADV (regime depth applied), buckets ≤1/≤5/≤20/>20 days, illiquid share of NAV, and a 20-session cash ladder of known flows (settlements, dividends, coupons, fixed OTC coupons and CDS premia). |
| **Limits** | Per job, soft and hard: VaR 99%/NAV, ES/NAV, largest counterparty exposure/NAV, illiquid share, plus the job's leverage and drawdown. Soft breaches warn; a hard breach accepts only risk-reducing orders and no new OTC trades until the next close is back within limits. Daily `RISK_SNAPSHOT` in the log (history on the page), `RISK_BREACH` events with severity. |
| **UI** | RISK page: overview (VaR tiles, factor exposures, component VaR, historical P&L series, exposure rows), stress (table, chart, custom scenario), liquidity (position ladder, cash ladder), limits (utilisation bars), history. Briefing risk section and breach attention items. |

### Phase 6 — operations depth (built)

| Piece | What exists |
|---|---|
| **Corporate events** (`engines/corporate_events.py`) | Seeded announcements with future effective dates (stored in the market close): stock dividends (processed as splits, options adjusted), special dividends, cash tender offers (election up to the cap, bought at the offer on the effective date), rights issues (subscribe at the discount by the deadline, unexercised rights lapse), cash mergers (price jumps toward the deal, options settle at intrinsic against the deal price, TRS terminated, every holder cashed out, stock delisted), spin-offs (new listing with its own history, shares distributed pro rata with cost basis carved out), bond calls (redeemed at the call price plus accrued). Sandbox `POST /force-corporate-event` for any of them. |
| **Elections** | `POST /portfolios/{p}/elections {ca_id, quantity}`; deadlines and outcomes in the briefing; election status on SETTLEMENTS & CUSTODY. |
| **Fails** | Failed deliveries and payments charged daily (3% annual on the cash amount, `5800`); a delivery failed for five sessions is bought in by the receiving party through a forced purchase with a 2% penalty. Match breaks, affirmation states, DVP/RVP retry existed already. |
| **Custody view** | Per security: settled, trade-date, pending receive/deliver, pledged, borrowed, reserved for written calls, failing, available. |

### Phase 7 — macro world (built)

| Piece | What exists |
|---|---|
| **Macro state** (`engines/macro.py`) | Growth, inflation (fed by growth and the 20-day oil move) and unemployment (Okun) mean-revert toward the regime's anchors; stored in the market close. |
| **Central bank** | Reaction function (real neutral rate r* + inflation + ½ inflation gap + ½ growth gap), eight meetings a year on a fixed calendar, 25/50bp steps toward the target with occasional surprises; the curve's short end is pulled toward the policy rate; decisions move rates, equities and vol. During the seeded prehistory the policy rate simply tracks the curve; at go-live the activity variables snap to the initial regime's anchors and r* is calibrated so the world starts in equilibrium (the bank then reacts to how growth and inflation drift). |
| **Releases** | Payrolls, PMI, CPI, retail sales, quarterly GDP on calendar dates with consensus from the state and seeded surprises that shock the curve level, the equity factor, FX and the vol index; each release is an `ECONOMIC_RELEASE` event and news. |
| **Earnings** | Every company reports each quarter on a seeded date against a consensus from its fundamentals; the surprise jumps the stock and updates EPS/revenue; dividend cuts in downturns and raises after strong years. `EARNINGS_REPORTED` events, calendar on the MACRO page and in the briefing. |
| **Credit** | Issuer spreads track rating norms scaled by the credit index; monthly rating migration when spreads trade wide or tight; daily default hazard from the spread (higher in recession and stress). A default marks the bonds at recovery (flat), writes off accrued interest, stops coupons, pays the recovery 30 sessions later, settles CDS and collapses the equity next session. Sustained dealer stress can end in a counterparty default. `RATING_CHANGED`, `ISSUER_DEFAULTED`, `BOND_DEFAULTED` events. |
| **UI** | MACRO page (state, policy path chart, calendar, releases, earnings, issuer credit, rating changes, corporate events); briefing macro and corporate sections with attention items. |

### Phase 8 — careers and game modes (built)

Every job in `careers.py` is playable. A save is a job; each job has a mandate (allowed asset classes, gross
leverage, concentration, drawdown), a ladder of titles, monthly/quarterly reviews, and **missions** — proficiency
tests checked at every close from the portfolio's actual state (`MISSION_STARTED/UPDATED/COMPLETED/FAILED`); a
completed mission earns a 5% capital allocation. The **MY DESK** page and the briefing's `desk` section carry the
job's own book:

| Job | Desk book |
|---|---|
| **Hedge fund** (`engines/investors.py`) | Six seeded investors own the fund. A 1.5% management fee accrues daily (`5900`/`2360`) and is paid monthly; a 15% performance fee over the high-water mark crystallises in December. Investors score trailing returns, drawdown and the regime: redemptions are notified on the 15th and paid at month end (`CAPITAL_WITHDRAWN`), subscriptions follow good quarters. |
| **Bank trader / derivatives trader** (`engines/clients.py`) | Named clients send one to three requests a session (Treasury and corporate blocks, equity blocks, IRS, FRA, swaptions, CDS, TRS, cross-currency). You quote a level in the request's unit or pass; the client compares it with the street's half-width and, if you win, deals **at your level**: blocks book as commission-free trades, OTC requests become client-facing trades under a client CSA (counterparty `CLIENT:*`, visible in exposure with `is_client`). Missions count wins and DV01 discipline. |
| **Securities lending** (`LendDeskEngine` in `engines/seclending.py`) | The lend side: lend settled inventory against 102% cash collateral (`2460`), earn the fee (`4360`, receivable `1430`), pay the rebate (`5310`, payable `2370`), monthly settlement, recalls with a two-day return, random early returns after a five-session minimum, collateral marked daily. Lent shares are encumbered (`LENT` pledges) so they cannot be delivered. |
| **Corporate treasurer** (`engines/treasury.py`) | A company's treasury: 600MM of debt (two fixed notes, a floating term loan) funding operating assets carried at cost (`1900`/`2380`); monthly EUR/GBP/JPY receipts and base-currency payments on the 20th (`OPERATING_FLOW`, `4980`); coupons and maturities (`DEBT_SERVICE`, `5950`); a minimum-liquidity rule. Missions: hedge the receipts with forwards or a cross-currency swap, fix the floating debt with pay-fixed swaps, never breach liquidity. |
| **Repo trader** | Matched-book and term-funding missions on the Phase 2 repo desk. |
| **Risk manager** | See Phase 10. |

**Crisis mode** (`scenario: CRISIS` at creation, sandbox or career): a scripted funding crisis keyed on the
processed-day index — liquidity stress and a Treasury sell-off on day 1, an issuer default on day 5, a dealer
failure on day 10, recession from day 31, easing from day 96 — recorded as `SCENARIO_EVENT`s, shown on the career
page and in the briefing. Sandbox worlds can also shock the curve directly (`POST /force-rates {bp}`).

### Phase 10 — AI institutions (built)

`engines/institutions.py`: a risk-manager save creates four rule-based desks (equity momentum, rates/credit carry,
index vol seller with delta hedging, commodity trend) as ordinary portfolios with jobs, limits and reviews. They
trade through the same order, settlement, margin, P&L and risk engines. Orders above 8% of a desk's NAV (× its
`request_mult`) are escalated as `DESK_REQUEST`s; the risk manager approves (placed at once), rejects, or lets
them lapse after five sessions, after which the desk works the order in clips at the threshold. The risk manager
sets `gross_mult` / `request_mult` / `var_mult`, can force a reduction (a market order on the desk's behalf), and
sees every desk's breaches, hard limits and pending requests in the briefing and on MY DESK. Missions: no hard
breach on any desk for 20 sessions, decide eight requests in time.

### Phase 9 — commodity depth (built)

| Piece | What exists |
|---|---|
| **Calendar-spread tickets** (`engines/commodity_desk.py`) | Buy one contract month, sell another of the same commodity as one all-or-none ticket (a `FUTURES_CALENDAR` strategy) with an optional differential limit; the clearing house margins the pair at 35% of one outright (`futures.required_margin` nets paired lots per commodity). Ticket on the commodity page. |
| **Options on futures** (`engines/options.py`) | American options on the front two contract months of every physical commodity, one future per contract, expiring three sessions before the future's last trade date; priced with Black-76 (the futures price is its own forward) off a vol surface seeded from the contract's structural vol; exercise and assignment open a futures position at the strike, marked to settlement that night; margin: covered one-for-one by an opposite future, spreads at the width, naked shorts at premium plus the future's initial margin. P&L in the commodities bucket, delta in commodity exposure. |
| **Physical delivery** | A portfolio setting (`POST /physical-delivery`). Held into the last trade date, a long takes delivery (the future closes at settlement and inventory `PHYS-<code>` is bought at settlement, T+2), a short delivers from inventory or is bought in at settlement plus a 2% penalty (`5700`). Inventory is a `PHYSICAL` security priced daily at the commodity's spot with a dealing spread, pays storage and insurance daily (`5320`, explain bucket `storage`), cannot be pledged or shorted, and is sold with an ordinary sell order. |

### Phase 11 — infrastructure (built)

| Piece | What exists |
|---|---|
| **Save versioning** (`version.py`, `migrations.py`) | Every save records the `SAVE_VERSION` it was created with; `World.load` migrates older logs step by step with pure functions over the event list (v1→v2 stamps the scenario and the macro/corporate-event keys older closes lack), reports the migration, and refuses saves from a newer engine. The store carries the version and upgrades its own schema. |
| **Resilient replay** | `World.load(strict=False)` isolates a damaged event, records it in `replay_errors` and continues; strict mode raises `ReplayError` with the sequence number. Every load runs an integrity check (trial balances, ledger vs economic NAV, custody). The service opens saves non-strictly by default (`FINSIM_STRICT_REPLAY=1` to change), the UI shows a banner, `GET /api/health` reports status, versions, scheduler state and per-world integrity. |
| **Scheduler under test** (`api/server.py`) | `Scheduler.tick(at)` is the unit of work; the thread loop injects the real clock and sleep. Tests drive it with a fake clock across weekends, a holiday and update times, and check idempotency, ordering and `next_update`. |
| **Logging** (`log.py`) | One `finsim` logger (level `FINSIM_LOG_LEVEL`, rotating file at `FINSIM_LOG`): world creation, days processed with event counts and timings (`run_log`), rejected commands, request outcomes, replay errors, migrations. |
| **Master scenario** (`tests/test_master_scenario.py`) | A 45-step end-to-end walk through every subsystem in one world (trading, financing, derivatives, OTC, risk, operations, macro, careers, commodities, infrastructure) with the accounting invariants checked at every checkpoint, replay equality and a bit-for-bit determinism check against a second run. |

### 2026-09 — expansion (built)

| Piece | What exists |
|---|---|
| **Universe** (`tools/refresh_universe.py`, `data/universe.json`) | 171 equities/ETFs/ADRs and 19 bonds, refreshed from Yahoo Finance and EDGAR; ~37k securities once every option chain is listed. `make_world` stays under seven seconds, a day under half a second. |
| **Real market** (`engines/realfeed.py`) | `RealFeed`: Yahoo spark closes for every symbol (batched, cached under `~/.finsim/realfeed-cache.json`, shared by every save), `history()` stored in the save as `REAL_HISTORY_LOADED`, `targets_for(date)` pinning a session's equities, commodity front months, FX and Treasury yields; `Market._pin_day` rescales the seeded session onto those closes so every derived price follows. `World.real_market_ready(d)` guards `advance` and the scheduler. |
| **OTC options & forwards** (`engines/otc.py`) | `EQUITY_OPTION` (Black–Scholes–Merton on any name or SPX, any strike, cash-settled), `FX_OPTION` (Garman–Kohlhagen, per-currency vol table with a regime multiplier), `EQUITY_FORWARD` (F = S·e^{(r−q)T}), `COMMODITY_FORWARD` (off the curve), `FX_FORWARD` (CIP) — priced, quoted by the dealers with their widths and leans, valued daily, margined under the CSA, settled in cash at maturity, replayed exactly. |
| **Investment banking** (`engines/investment_banking.py`) | A simulated career and desk (switched off in real-market saves): monthly mandates — sell-side and buy-side M&A, IPOs, block trades, bond issues, leveraged-loan underwriting — pitched against rival banks with a fee and a promise (valuation, range, discount, concession, spread); the client answers in three sessions on fee, promise, reputation and league standing. Execution: a sale process brings bids (accept or hold out), a buy-side offer lands or loses on the premium, an IPO builds a book and the banker prices it (first-day return follows the coverage and the price against fair value; a broken IPO costs reputation), a block is bought at the discount and resold into the market at the simulation's prices (real underwriting risk), a bond fills or is pulled on its concession, a loan syndicates or hangs at a discount in a seizure. Fees are cash on closing; announcements go on the wire as DEALS; a league table (fees YTD against ten named rivals) and a 0–100 reputation. Accounts 1190/4390/4395/5370. Job: Investment Banker ($500m). |
| **Real estate & housing** (`api/service.py: housing`) | Market Place → Real Estate & Housing: equity REITs by property type, homebuilders (DHI, LEN, NVR, PHM, TOL, KBH), building products, mortgage REITs (AGNC, NLY, STWD), real estate ETFs, agency bonds; the housing indicators — the 30-year mortgage rate, starts, permits, existing-home sales and the Case-Shiller index (FRED, as known on the save's date, in career saves; derived from the curve and the cycle in a sandbox). |
| **Private equity** (`engines/private_equity.py`) | Deterministic monthly deal flow across seven sectors with realistic public multiples (software 16x … energy 7x), margins, growth, capex and working-capital intensity; bids resolve in five sessions (auctions are competitive, proprietary sellers give more; a hidden quality-of-earnings adjustment surfaces at the first report unless diligence was bought); a won deal closes at once — senior term loan (S+325–475bp, 1% amortisation, 50% excess-cash-flow sweep) up to 4.5x, second lien at S+850bp above, advisory/legal/financing fees expensed, management rollover; quarterly reports drive revenue (sector, cycle, equity market, programmes), margin, FCF, paydown or revolver draws, net-leverage and interest-cover covenants (cure with equity within ten sessions or the lenders take the keys); marks blend entry multiple toward the public comps over eight quarters; cost programme / growth plan / new CEO / add-on (multiple arbitrage) with execution risk; dividend recaps; sale process (strategic premium, thirty sessions, can fail in a recession) and IPO (10% discount, a quarter sold at the offer, daily-marked listed stake, 180-day lock-up, block sales); MOIC, IRR, DPI/RVPI/TVPI. Accounts 1180/1185 and 4370/4380/5360; its own P&L bucket; in NAV. Job: Private Equity Partner ($2bn). |
| **Private credit** (`engines/private_credit.py`) | Deterministic monthly pipeline of sponsor-backed deals; commitments (≥ $1m in $100k steps) fund at par less OID, accrue and pay quarterly floating coupons, accrete the discount, are marked daily off a loan-market spread (HY index × regime × borrower drift), migrate ratings monthly, breach and amend covenants, default with a workout and recovery, and sell in the secondary market at a bid. Accounts 1170/1175/1225 and 4320/4330/4340/5350; its own P&L bucket; inside the economic NAV and the ledger reconciliation. |
| **Playbook** (`engines/playbook.py`) | 38 strategies as leg templates (stock, option with strike rules incl. zero-cost search, futures notional-matched, TRS); `preview` resolves every leg off today's chain/quotes with Greeks, margin and a payoff grid; `execute` places the package (locate + borrow + short for short legs, RFQ + best-dealer deal for swaps). |
| **FX market view** (`Service.fx_market`) | Per currency: market-convention quote, changes, rate differential, carry, realised vol, forward points from CIP, and the decomposition of today's move into carry, equity-factor beta, mean reversion and residual flow, recomputed from the stored history (no new state). |

### Test map

`python3 -m unittest discover -s tests` (stdlib only). By subsystem: Core (`test_engines`, `test_daily`, `test_api`,
`test_vertical_slice`), Financing (`test_financing`), Options (`test_options`), OTC (`test_otc`), Risk (`test_risk`),
Operations (`test_operations`), Macro (`test_macro`), Game/Careers (`test_careers`, `test_institutions`),
Commodities (`test_commodities`), Infrastructure (`test_infrastructure`, `test_master_scenario`).

### What is deliberately not built yet

Everything in the roadmap has a first implementation. Remaining depth (a second tier) would be: serial and
mid-curve commodity options, exchange-for-physical and warehouse receipts, sector-specific desk strategies for the AI
institutions, and a richer investor model (side pockets, gates).

## Architecture

```
finsim/
  money.py            Decimal money/price/quantity helpers (all ledger amounts are Decimal cents)
  calendar.py         business calendar, holiday rules, configurable settlement cycles
  domain/events.py    Event + type constants        domain/models.py  state projections
  world.py            World: command handlers, event log, replay, derive(), handler registry
  store.py            sqlite event store (append-only; swap for PostgreSQL by changing this file)
  clock.py            real-time clock: target date, next update, catch-up
  careers.py          jobs, mandates, limits, reviews, promotion, missions, the crisis script
  engines/
    market.py         universe, regimes, factor model, curve, dividends, contract listings, vol index, vol surfaces
    commodities.py    commodity specs, fundamentals, spot, futures curves, contract expiry rules, news
    pricing.py        Instrument interface, BondPricer
    trading.py        order validation, once-per-day execution against the session, lots/FIFO, futures fills
    futures.py        variation margin, initial-margin sweeps, expiry
    collateral.py     haircuts, eligibility, pledges/encumbrance, cash collateral, calls
    lending_market.py lendable supply, utilization, borrow rates, specials
    seclending.py     locates, loans, fees, marks, recalls, buy-ins, returns
    repo.py           repo / reverse repo, rolls, marks, calls, substitution
    prime.py          margin loan, financing value, excess liquidity, calls, forced liquidation
    fx_market.py      spot rates and short rates per currency
    fx.py             multi-currency cash, spot, forwards, translation
    vol.py            implied-vol surfaces per underlying (ATM, skew, curvature, term; regime dynamics)
    options_pricing.py  BSM, CRR tree with control variate, Greeks, bounds, implied vol
    options.py        listings, chains, quotes, synthetic bars, margin, exercise/assignment/expiry, strategies, splits
    otc_pricing.py    curve bootstrapping, swap/FRA/cap/swaption/CDS/XCCY/TRS/commodity-swap valuation
    counterparties.py dealers (ratings, CDS, stress, per-product widths), CSA terms, client counterparties
    otc.py            RFQ, execution, lifecycle, netting sets, CSA margin, close-out, credit events, defaults, exposure/PFE
    risk.py           factor exposures, historical VaR/ES, parametric VaR, stress, liquidity ladders, limits
    corporate_events.py  seeded corporate events (tenders, rights, mergers, spin-offs, calls), elections, fails/buy-ins
    macro.py          macro state, central-bank reaction function, releases, earnings, credit migration, defaults
    investors.py      hedge-fund investors: fees, notices, redemptions, subscriptions, high-water mark
    clients.py        the client franchise for bank/derivatives traders: requests, quotes, win logic, booking
    treasury.py       corporate treasury: debt stack, operating flows, debt service, hedging dashboard
    institutions.py   AI desks (momentum, carry, vol seller, commodity trend), requests, oversight
    seclending.py     … plus LendDeskEngine (the lend side)
    counterparties.py dealers, credit state, quoting widths, ISDA/CSA terms
    otc_pricing.py    curve utilities, swaps, FRAs, Black-76 caps/swaptions, CDS, TRS, commodity swaps
    otc.py            RFQ, trade lifecycle (fixings, payments, resets, exercise), marks, CSA margining, exposure, close-outs
    risk.py           exposures, historical VaR/ES, stress, component VaR, liquidity ladders, limits
    corporate_events.py  corporate event announcements, elections, effective-date processing, fail charges, buy-ins
    macro.py          macro state, central bank, releases, earnings, credit migration and defaults
    settlement.py     lifecycle states, DVP/RVP processing, fails, custody & cash movements
    corporate_actions.py  dividends, coupons, maturities
    accruals.py       bond and cash interest
    pnl.py            marks, NAV snapshots, explain buckets
    briefing.py       the daily briefing
    simulation.py     the daily process
  api/service.py      framework-agnostic API (dicts in/out)
  api/server.py       stdlib HTTP adapter + static UI
  static/             index.html, app.js, style.css, web manifest and icons (no build step, no CDN)
  app.py              the local app: install / open / status / uninstall (launchd, systemd --user, Task Scheduler)
  demo.py             the §46 walkthrough
tools/                refresh_universe.py (the real-universe snapshot), make_icons.py (the app icon)
tests/                unittest suites (engine, lifecycle, API over HTTP, replay)
```

**Event sourcing.** Commands validate and `emit` events. Applying an event mutates state
via the engine that owns that event type. Handlers can `derive` further events (a trade
derives its ledger posting, settlement instruction and valuation mark). On replay,
`derive` is a no-op because the derived events are already in the log — so the state is
always a pure projection of the stored sequence.

**Clock** (`clock.py`): the target simulated date at any real moment is the latest
business day whose update time has passed in the save's timezone; `World.catch_up()`
runs each missed day in order. Career worlds refuse manual advancing.

**Determinism.** Every random draw is seeded from `(world_seed, date, …)`, never from a
running RNG, so replay, pre-history regeneration and per-order execution noise are all
reproducible and independent of call order.

## API (all JSON)

```
GET  /api/jobs
GET  /api/worlds                                  POST /api/worlds {name, seed, job, clock_mode, timezone, update_time, start_date, capital, ...}
GET  /api/worlds/{w}                              POST /api/worlds/{w}/advance {days}   (sandbox only)
GET  /api/worlds/{w}/securities                   GET  /api/worlds/{w}/securities/{id}?period=3M
GET  /api/worlds/{w}/commodities                  GET  /api/worlds/{w}/commodities/{code}
GET  /api/worlds/{w}/yield-curve | news | corporate-actions | events?type=&q= | events/{id}
POST /api/worlds/{w}/portfolios                   POST /api/worlds/{w}/portfolios/{p}/orders
     {security_id, side, quantity, order_type, limit_price, stop_price, trail_pct, condition:{ref,op,value}, time_in_force}
POST /api/worlds/{w}/portfolios/{p}/orders/preview {security_id, side, quantity | amount, limit_price}   (cash estimate; amount -> quantity)
GET  /api/worlds/{w}/portfolios/{p}/briefing?date= | career | dashboard | positions/{sec} | orders | orders/{id} (DELETE cancels)
     | trades | trades/{id} | settlements | custody | cash | ledger?account=&security_id=
     | balance-sheet | pnl-explain?date= | nav-explain
GET  /api/worlds/{w}/portfolios/{p}/seclending | repo | collateral | financing
POST /api/worlds/{w}/portfolios/{p}/locates {security_id, quantity}      POST .../loans {locate_id, quantity, collateral_type}
POST /api/worlds/{w}/portfolios/{p}/loans/{id}/return {quantity}
POST /api/worlds/{w}/repo-quote {side, security_id, quantity, term_type, term_days}
POST /api/worlds/{w}/portfolios/{p}/repo {side, security_id, quantity, term_type, term_days, auto_roll}
POST /api/worlds/{w}/portfolios/{p}/repo/{id}/close | collateral | cash | reduce | substitute
POST /api/worlds/{w}/portfolios/{p}/margin/draw | repay {amount}
POST /api/worlds/{w}/portfolios/{p}/fx/spot {buy_ccy, sell_ccy, amount, amount_ccy}   POST .../fx/forward {buy_ccy, sell_ccy, buy_amount, maturity}
GET  /api/worlds/{w}/fx                           (every currency vs USD: quote, carry, vol, forward points, today's drivers, history, cross rates)
GET  /api/worlds/{w}/portfolios/{p}/playbook      POST .../playbook/preview | execute {key, params:{underlying, other, units, tenor_months, strikes:{legIndex: pct|strike}}}
GET  /api/worlds/{w}/portfolios/{p}/private-credit   POST .../private-credit/commit {deal_id, amount}   POST .../private-credit/sell {loan_id, amount}
POST /api/worlds {..., capital}                                (any amount up to 1e12 for any job; blank = the job's standard)
GET  /api/worlds/{w}/portfolios/{p}/tags                      (every #tag: fills, working instructions, OTC trades, FX deals, P&L)
POST .../orders {..., tag} · .../fx/spot {..., tag} · .../fx/forward {..., tag} · .../otc/rfq {product, params:{..., tag}}
POST /api/worlds {..., market_source: SIMULATED|REAL, lock_session}   GET /api/worlds/{w} → market_source, real_market {latest_close, next_session, waiting}, trading_window {open, lock, session_running, reason}, live {available, instruction_session}
POST /api/worlds/{w}/clock {update_time, timezone, lock_session}      (career saves; a real-market save must update at 16:00 New York or later)
GET  /api/worlds/{w}/live?ids=SPY,NVDA,CLZ26                 (latest real quotes next to the save's last close; without ids: the index ETFs and everything held)
POST .../orders {..., settle_ccy, execution: LIVE|NEXT_UPDATE}   (LIVE fills now at the latest quote and returns the trade; the FX spot for another settlement currency is dealt alongside, live too)
     a LIVE limit / stop / stop-limit / take-profit / trailing stop the quote has not reached rests: GET .../live sweeps the resting book before answering (`worked` says what filled), the scheduler sweeps every loaded save once a minute
GET  /api/worlds/{w}/treasury · POST .../treasury/fund {amount} · POST .../treasury/allocate {portfolio_id, amount} · POST .../treasury/return {portfolio_id, amount}
POST /api/worlds {..., treasury: true}   (capital into the Treasury, first book empty) · POST .../portfolios {..., from_treasury: true}   (a book funded from the Treasury)
GET  /api/worlds/{w}/overall                                   (every book's dashboard side by side, positions added up across books, cash, the day's P&L by bucket)
DELETE /api/worlds/{w}/portfolios/{p}                          (close a flat book; refused with the list of open items otherwise; never the last book)
GET  /api/worlds/{w}/housing                                   (REITs by property type, homebuilders, mortgage REITs, ETFs, agency bonds, housing indicators)
GET  .../portfolios/{p}/investment-banking                    (mandates with my pitches, engagements with pending decisions, closed deals, league table, reputation)
POST .../investment-banking/pitch {mandate_id, fee_pct, promise} · POST .../investment-banking/decide {engagement_id, choice, value}
GET  .../portfolios/{p}/private-equity                        (deal flow with my bids and diligence, portfolio companies with reports, MOIC, IRR, fund totals DPI/RVPI/TVPI, the market)
POST .../private-equity/structure {deal_id, multiple, leverage} (sources & uses)  · POST .../private-equity/{diligence|bid|initiative|recap|cure|exit|selldown}
POST /api/worlds/{w}/track-real                               (switch a simulated save to the real market: MARKET_SOURCE_CHANGED + REAL_HISTORY_LOADED events; needs the network once)
POST /api/worlds/{w}/live/work                                (sweep now → {checked, instructions, quoted, filled, triggered, ratcheted, tick, next_tick}; instructions are worked once per 15-minute quote update, 09:45–16:15 NY)
POST .../fx/spot {..., execution: LIVE}                       (a spot deal at the pair's live quote)
     with the session lock on, career saves refuse trading and dealing commands from 09:30 New York until the update (409 with the reason); cancels are always accepted
POST /api/worlds/{w}/force-regime {regime}   (sandbox)
GET  /api/worlds/{w}/options                      GET  /api/worlds/{w}/options/{underlying}/chain?expiry= | surface   GET .../options/{contract}/contract
GET  /api/worlds/{w}/portfolios/{p}/options       POST .../options/exercise {contract_id, quantity}
POST /api/worlds/{w}/portfolios/{p}/strategies {strategy_type, underlying, expiry, strikes[], quantity, net_limit, expiry2, time_in_force}
POST /api/worlds/{w}/portfolios/{p}/strategies/preview   GET .../strategies | strategies/{id}
POST /api/worlds/{w}/force-split {security_id, ratio}   (sandbox)
GET  /api/worlds/{w}/otc/dealers                  GET  /api/worlds/{w}/portfolios/{p}/otc | otc/{trade} | counterparties
POST /api/worlds/{w}/portfolios/{p}/otc/rfq {product, params}   POST .../otc/rfq/{id}/execute {dealer}   POST .../otc/{trade}/terminate
     products: IRS FRA CAP FLOOR SWAPTION XCCY TRS CDS COMMODITY_SWAP EQUITY_OPTION FX_OPTION EQUITY_FORWARD COMMODITY_FORWARD FX_FORWARD
POST /api/worlds/{w}/credit-event {reference}     POST /api/worlds/{w}/default-counterparty {dealer}   (sandbox)
GET  /api/worlds/{w}/portfolios/{p}/risk          POST .../risk/stress {equity, rates_bp, spreads_bp, vol_pts, commodity, fx, commodity_by_code}
GET  /api/worlds/{w}/macro                        GET  /api/worlds/{w}/portfolios/{p}/corporate-actions   POST .../elections {ca_id, quantity}
POST /api/worlds/{w}/force-corporate-event {security_id, kind, terms, effective}   (sandbox)
POST /api/worlds/{w}/force-rates {bp}   (sandbox)         POST /api/worlds {..., scenario: NONE|CRISIS}
GET  /api/worlds/{w}/portfolios/{p}/desk                  (investors | clients | lending | treasury | oversight, by job; missions)
POST /api/worlds/{w}/portfolios/{p}/desk/quote {rfq_id, level | pass}   POST .../desk/lend {security_id, quantity}   POST .../desk/recall {lend_id, quantity}
POST /api/worlds/{w}/portfolios/{p}/desk/decide {desk_id, request_id, approve, note}   POST .../desk/limit {desk_id, key, value}   POST .../desk/reduce {desk_id, security_id, fraction}
```

## Environment note

The spec recommends Next.js + FastAPI + PostgreSQL + NumPy/QuantLib. The environment this
was built in had no package registry access, so the implementation is standard-library
only: `http.server` instead of FastAPI, `sqlite3` instead of PostgreSQL, pure-Python
numerics, a vanilla-JS terminal UI instead of Next.js. The boundaries were kept so those
swaps are local: `api/service.py` is framework-agnostic (a FastAPI app would be a thin
adapter), `store.py` is the only persistence code, and `BondPricer` sits behind the
`Instrument` interface where QuantLib would go.
