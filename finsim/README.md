# finsim — a miniature institutional financial system

A single-player simulation of how institutional finance actually works, designed
around **one login per day**. Every morning at your update time the simulated
business day is processed whether or not you log in: markets move, your
overnight instructions meet the session, positions are marked and margined,
settlements progress, corporate actions pay, and a daily briefing is written.
In the evening you read the briefing, review the book, and leave instructions
for the next day.

**This is a simulation.** It never connects to a broker, exchange or market-data feed while you play. The universe
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
| **Market engine** | ~70 real names: stocks across all eleven GICS sectors (Apple to Nucor), three REITs, two ADRs (TSM, Toyota), a bank preferred, five ETFs (SPY, QQQ, TLT, HYG, SHV) with real fundamentals and liquidity tiers; 4 on-the-run Treasuries and representative JPMorgan (A), Ford Motor Credit (BBB-) and American Airlines (B+) bonds. Factor-model returns (market + sector + idiosyncratic), Nelson–Siegel curve and IG/HY spreads shocked on the same factor, five Markov regimes, a volatility index, ex-dividend price drops. |
| **Commodities** | 21 commodities (WTI, Brent, natural gas, gasoline, heating oil; gold, silver, copper, platinum, palladium, aluminum; corn, wheat, soybeans, coffee, sugar, cotton, cocoa; live cattle, feeder cattle, lean hogs) each with a supply/demand state: cyclical demand, decaying supply shocks that arrive as news, inventories that accumulate the balance and jump on scheduled reports (EIA weekly, USDA/LME monthly). Prices respond to changes in the balance, the macro cycle and seasonality; the **futures curve** is cost-of-carry with a convenience yield that rises when inventories are tight, so shortages backwardate and gluts contango. A COMMODITIES desk page and a page per commodity show spot, curve (today / 5d / 1m ago), contracts, fundamentals and news. |
| **Futures** | Real month codes (CLZ26), per-commodity listing cycles and expiry rules, contract multipliers, ticks, margins; equity-index (ES on SPX) and 10Y note (ZN) futures by carry. Long or short; commissions per contract; **daily variation margin** moves cash and posts to income; initial margin is swept to a clearing account at a regime-dependent rate; margin calls when cash is overdrawn; forced liquidation after three days; positions auto-close on the last trade date so nobody takes delivery. |
| **Trading** | Market-on-next-update semantics; spread crossing and square-root impact; participation caps; trailing stops that ratchet; conditional orders evaluated at the close; FIFO lots; pre-trade cash, position, margin and mandate checks. |
| **Ledger & operations** | Institutional chart of accounts with margin-deposit and futures-P&L accounts; trade-date accounting; balanced journal entries with a security dimension; trial balance and balance sheet. Trade lifecycle to settlement, RVP/DVP instructions, configurable settlement cycles, holiday calendar, fails with retry, separate cash and custody movements. Dividends, coupons, maturities, daily interest accruals. |
| **Audit** | Append-only sqlite event log; every event has a cause; state (including briefings, reviews, futures margin) is rebuilt by replay and tested to be identical. |
| **UI** | Daily briefing (login screen), portfolio (positions, futures, exposures, NAV explain, P&L explain), trading (instructions blotter, trade lifecycle), markets, commodities desk (spread tickets, physical delivery, inventory), fixed income, options, OTC derivatives, risk, macro, sec lending, repo, collateral, settlements & custody, treasury & FX, news, accounting, MY DESK (the job's own book), career (missions, scenario), audit trail. New-save dialog picks job, clock mode, timezone, update time, initial regime and scenario. |

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
POST /api/worlds/{w}/force-regime {regime}   (sandbox)
GET  /api/worlds/{w}/options                      GET  /api/worlds/{w}/options/{underlying}/chain?expiry= | surface   GET .../options/{contract}/contract
GET  /api/worlds/{w}/portfolios/{p}/options       POST .../options/exercise {contract_id, quantity}
POST /api/worlds/{w}/portfolios/{p}/strategies {strategy_type, underlying, expiry, strikes[], quantity, net_limit, expiry2, time_in_force}
POST /api/worlds/{w}/portfolios/{p}/strategies/preview   GET .../strategies | strategies/{id}
POST /api/worlds/{w}/force-split {security_id, ratio}   (sandbox)
GET  /api/worlds/{w}/otc/dealers                  GET  /api/worlds/{w}/portfolios/{p}/otc | otc/{trade} | counterparties
POST /api/worlds/{w}/portfolios/{p}/otc/rfq {product, params}   POST .../otc/rfq/{id}/execute {dealer}   POST .../otc/{trade}/terminate
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
