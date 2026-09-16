# finsim — a miniature institutional financial system

A single-player simulation of how institutional finance actually works, designed
around **one login per day**. Every morning at your update time the simulated
business day is processed whether or not you log in: markets move, your
overnight instructions meet the session, positions are marked and margined,
settlements progress, corporate actions pay, and a daily briefing is written.
In the evening you read the briefing, review the book, and leave instructions
for the next day.

**This is a simulation.** It never connects to a broker, exchange or market-data
feed. All prices are generated from a seed.

```
python3 -m finsim serve         # http://127.0.0.1:8000  (terminal UI + JSON API + daily scheduler)
python3 -m finsim demo          # a scripted week: instructions, fills, settlement, dividend, futures, audit
python3 -m unittest discover -s tests
```

No third-party packages are required (see *Environment note* below).

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
| **Saves & careers** | Multiple independent saves. Jobs: Sandbox, Portfolio Manager ($100MM vs equity benchmark), Global Macro Trader ($250MM; rates, index and commodity futures, government bonds), Commodity Trader ($100MM; energy, metals, ags, livestock), Fixed-Income PM ($250MM; Treasuries, corporates, note futures vs a 5Y benchmark). Each job fixes capital, mandate (instrument classes enforced at order entry), risk limits (gross leverage, single-position %, drawdown), benchmark and a promotion ladder. Hedge-fund, bank-trader, derivatives, sec-lending, repo, treasury and risk-manager jobs are listed as *planned* until their modules exist. |
| **Reviews & progression** | Month, quarter and year-end reviews from the book's own history: return, benchmark, alpha, max drawdown, Sharpe, largest contributor/loss, P&L by bucket, risk breaches, settlement failures, margin calls missed, a rating and an evaluation. EXCEEDS at a quarter end promotes and allocates capital; two UNACCEPTABLE quarters demote. |
| **Market engine** | 24 fictional equities/ETFs/ADR/preferred/REIT with fundamentals and liquidity tiers; 4 Treasuries and 3 corporates. Factor-model returns (market + sector + idiosyncratic), Nelson–Siegel curve and IG/HY spreads shocked on the same factor, five Markov regimes, a volatility index, ex-dividend price drops. |
| **Commodities** | 21 commodities (WTI, Brent, natural gas, gasoline, heating oil; gold, silver, copper, platinum, palladium, aluminum; corn, wheat, soybeans, coffee, sugar, cotton, cocoa; live cattle, feeder cattle, lean hogs) each with a supply/demand state: cyclical demand, decaying supply shocks that arrive as news, inventories that accumulate the balance and jump on scheduled reports (EIA weekly, USDA/LME monthly). Prices respond to changes in the balance, the macro cycle and seasonality; the **futures curve** is cost-of-carry with a convenience yield that rises when inventories are tight, so shortages backwardate and gluts contango. A COMMODITIES desk page and a page per commodity show spot, curve (today / 5d / 1m ago), contracts, fundamentals and news. |
| **Futures** | Real month codes (CLZ26), per-commodity listing cycles and expiry rules, contract multipliers, ticks, margins; equity-index (ES on SPXE) and 10Y note (ZN) futures by carry. Long or short; commissions per contract; **daily variation margin** moves cash and posts to income; initial margin is swept to a clearing account at a regime-dependent rate; margin calls when cash is overdrawn; forced liquidation after three days; positions auto-close on the last trade date so nobody takes delivery. |
| **Trading** | Market-on-next-update semantics; spread crossing and square-root impact; participation caps; trailing stops that ratchet; conditional orders evaluated at the close; FIFO lots; pre-trade cash, position, margin and mandate checks. |
| **Ledger & operations** | Institutional chart of accounts with margin-deposit and futures-P&L accounts; trade-date accounting; balanced journal entries with a security dimension; trial balance and balance sheet. Trade lifecycle to settlement, RVP/DVP instructions, configurable settlement cycles, holiday calendar, fails with retry, separate cash and custody movements. Dividends, coupons, maturities, daily interest accruals. |
| **Audit** | Append-only sqlite event log; every event has a cause; state (including briefings, reviews, futures margin) is rebuilt by replay and tested to be identical. |
| **UI** | Daily briefing (login screen), portfolio (positions, futures, exposures, NAV explain, P&L explain), trading (instructions blotter, trade lifecycle), markets, commodities desk, fixed income, settlements & custody, treasury, news, accounting, career, audit trail. New-save dialog picks job, clock mode, timezone and update time. |

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

### What is deliberately not built yet

Listed in the navigation as *Not yet built* rather than as screens that do nothing:
options, OTC derivatives and ISDA/CSA collateral, a risk dashboard (VaR, stress), counterparties
with credit quality and default, AI institutions, physical-commodity mechanics, bank RFQ flow.
They correspond to Phases 3–8 of the spec and plug into the same event/ledger/daily-cycle core.

## Architecture

```
finsim/
  money.py            Decimal money/price/quantity helpers (all ledger amounts are Decimal cents)
  calendar.py         business calendar, holiday rules, configurable settlement cycles
  domain/events.py    Event + type constants        domain/models.py  state projections
  world.py            World: command handlers, event log, replay, derive(), handler registry
  store.py            sqlite event store (append-only; swap for PostgreSQL by changing this file)
  clock.py            real-time clock: target date, next update, catch-up
  careers.py          jobs, mandates, limits, reviews, promotion
  engines/
    market.py         universe, regimes, factor model, curve, dividends, contract listings, vol index
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
    settlement.py     lifecycle states, DVP/RVP processing, fails, custody & cash movements
    corporate_actions.py  dividends, coupons, maturities
    accruals.py       bond and cash interest
    pnl.py            marks, NAV snapshots, explain buckets
    briefing.py       the daily briefing
    simulation.py     the daily process
  api/service.py      framework-agnostic API (dicts in/out)
  api/server.py       stdlib HTTP adapter + static UI
  static/             index.html, app.js, style.css (no build step, no CDN)
  demo.py             the §46 walkthrough
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
```

## Environment note

The spec recommends Next.js + FastAPI + PostgreSQL + NumPy/QuantLib. The environment this
was built in had no package registry access, so the implementation is standard-library
only: `http.server` instead of FastAPI, `sqlite3` instead of PostgreSQL, pure-Python
numerics, a vanilla-JS terminal UI instead of Next.js. The boundaries were kept so those
swaps are local: `api/service.py` is framework-agnostic (a FastAPI app would be a thin
adapter), `store.py` is the only persistence code, and `BondPricer` sits behind the
`Instrument` interface where QuantLib would go.
