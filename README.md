# finsim — a miniature institutional financial system

A single-player simulation of how institutional finance actually works: portfolio
accounting, a double-entry general ledger, a simulated multi-asset market, order
execution with liquidity, a full post-trade lifecycle, DVP settlement and custody,
corporate actions, accruals, daily NAV with P&L explain, and an immutable audit trail
that every number on screen can be traced back to.

**This is a simulation.** It never connects to a broker, exchange or market-data feed.
All prices are generated from a seed.

## Status: Phase 1 (financial core) — the vertical slice works end to end

The spec's 17-step first build (§46) is implemented, tested and demonstrable:

```
python3 -m finsim demo          # prints the walkthrough with every accounting entry
python3 -m finsim serve         # http://127.0.0.1:8000  (terminal UI + JSON API)
python3 -m unittest discover -s tests
```

No third-party packages are required (see *Environment note* below).

### What is built

| Area | What exists |
|---|---|
| **World** | Seeded, deterministic. 260 business days of pre-history. Multiple worlds per database, multiple portfolios per world (personal, L/S equity, macro, fixed income, credit, pension, bank desk, treasury desk …). Realism setting (Beginner / Intermediate / Professional) and Sandbox / Portfolio-Manager mode with an ETF benchmark. |
| **Market engine** | 24 fictional equities/ETFs/ADR/preferred/REIT across 10 sectors with fundamentals, liquidity tiers, ADV, spreads, dividend policies; 4 Treasuries and 3 corporate bonds (A- / BBB / B+). Returns come from a **factor model** (market + sector + idiosyncratic) — assets are correlated, not independent random walks. Nelson–Siegel yield curve and IG/HY spread indices shocked with loadings on the market factor. Five **regimes** (normal growth, hiking, cutting, recession, liquidity crisis) via a Markov chain that change drift, vol, spreads, depth and the stock/rates correlation. Prices drop by the dividend on ex-date. |
| **Pricing engine** | Uniform `Instrument` interface (`price / market_value / accrued / cash_flows / risk_metrics / next_events`). Bonds: clean/dirty price off the curve (+ credit spread), Act/Act accrued, YTM (Newton), Macaulay/modified duration, convexity, DV01, benchmark spread, hazard-implied PD. Equities: beta, realized vol, beta-dollar exposure. |
| **Trading** | Market / limit / stop / stop-limit, DAY / GTC. Market orders cross the spread and pay square-root **market impact** scaled by realized vol and participation; orders above 20% of session volume **partially fill** and keep working; limit orders fill only when marketable; stops trigger on the tape. Every fill records reference quotes, spread cost, impact cost and participation. Pre-trade checks: projected settled cash (settled cash + receivables − payables − working buys), long position net of working sells, lot sizes; no naked shorts until the securities-lending module exists. |
| **Positions** | Trade-date quantity vs settled (custody) quantity, pending receive/deliver, **FIFO lots**, cost basis, realized/unrealized, per-security income and fees. |
| **Ledger** | Institutional chart of accounts, trade-date accounting, balanced journal entries with a security dimension, trial balance, balance sheet, income statement. Ledger NAV == economic NAV is asserted in tests after every scenario. |
| **Lifecycle & settlement** | EXECUTED → CAPTURED → MATCHED → AFFIRMED → CLEARED → SETTLEMENT_PENDING → SETTLED, with seeded match breaks. Settlement instructions (RVP/DVP) with ISIN/CUSIP, delivering/receiving party, custodian. Configurable settlement cycles per market (US equity T+1, EU T+2 …) and a rule-based NYSE holiday calendar. **Fails** when settled cash or custody securities are short; failed instructions retry daily and settle late. Cash movements and securities movements are recorded separately. |
| **Corporate actions & accruals** | Dividend declared (news) → ex-date entitlement (income + receivable) → pay date (cash). Bond coupons, maturities, daily Act/Act accrual. Cash earns policy − 25bp / pays policy + 150bp (Act/360), accrued daily and settled monthly. |
| **NAV & P&L explain** | Daily snapshot whose explain (equity price, fixed-income price, dividends, bond interest, cash interest, commissions) is derived from ledger account deltas and **sums exactly** to the NAV change; per-position attribution; live "NAV explain" on the dashboard down to the journal entries. |
| **Audit** | Append-only event log (sqlite). Every event has a `cause_id`; the UI shows the causal tree (e.g. `ORDER_ENTERED → TRADE_EXECUTED → {LEDGER_POSTED, SETTLEMENT_INSTRUCTION_CREATED, VALUATION_MARKED → LEDGER_POSTED}`). State is a projection: reloading a world replays the log and reproduces it exactly (tested). |
| **UI** | Terminal-style pages: Home (dashboard), Markets, Security (candles, order book, analytics, fundamentals, ticket), Portfolio (positions, exposures, P&L explain), Position drill-down (lots, trades, settlements, ledger, dividends, custody, daily attribution, relationships), Trading (blotters, ticket, trade lifecycle modal), Fixed Income (curve, history, bond analytics), Settlements & Custody, Treasury (cash ledgers, liquidity projection), News, Accounting (balance sheet, trial balance, journal), Audit trail (event explorer). |

### What is deliberately not built yet

The navigation lists these as *Not yet built* rather than showing screens that do nothing:
securities lending / shorting, repo, margin & prime brokerage, collateral, futures & options,
OTC derivatives & ISDA/CSA, risk dashboard (VaR, stress), counterparties, macro dashboard,
news engine beyond real corporate-action/regime events, career modes, AI institutions.
They correspond to Phases 2–8 of the spec and plug into the same event/ledger core.

## Architecture

```
finsim/
  money.py            Decimal money/price/quantity helpers (all ledger amounts are Decimal cents)
  calendar.py         business calendar, holiday rules, configurable settlement cycles
  domain/events.py    Event + type constants        domain/models.py  state projections
  world.py            World: command handlers, event log, replay, derive(), handler registry
  store.py            sqlite event store (append-only; swap for PostgreSQL by changing this file)
  engines/
    market.py         universe, regimes, factor model, curve, dividends (pure function of seed+date)
    pricing.py        Instrument interface, BondPricer
    trading.py        order validation, execution model, TRADE_EXECUTED handler (lots/FIFO, ledger, SI, mark)
    settlement.py     lifecycle states, DVP/RVP processing, fails, custody & cash movements
    corporate_actions.py  dividends, coupons, maturities
    accruals.py       bond and cash interest
    pnl.py            marks, NAV snapshots, explain
    simulation.py     the daily cycle
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

**Daily cycle** (`SimulationEngine.advance_one_day`): close the current day — lifecycle
statuses, settlements due, corporate actions, accruals, mark-to-market, NAV snapshot,
expire DAY orders — then open the next business day: publish its prices, re-mark
positions, fill working orders at the open, declare new dividends, announce regime
changes. The player trades "in" the open day at that day's quotes. This is the
daily-granularity convention; intraday time is a later refinement.

**Determinism.** Every random draw is seeded from `(world_seed, date, …)`, never from a
running RNG, so replay, pre-history regeneration and per-order execution noise are all
reproducible and independent of call order.

## API (all JSON)

```
GET  /api/worlds                                  POST /api/worlds {name, seed, start_date, capital, ...}
GET  /api/worlds/{w}                              POST /api/worlds/{w}/advance {days}
GET  /api/worlds/{w}/securities                   GET  /api/worlds/{w}/securities/{id}?period=3M
GET  /api/worlds/{w}/yield-curve | news | corporate-actions | events?type=&q= | events/{id}
POST /api/worlds/{w}/portfolios                   POST /api/worlds/{w}/portfolios/{p}/orders
GET  /api/worlds/{w}/portfolios/{p}/dashboard | positions/{sec} | orders | orders/{id} (DELETE cancels)
     | trades | trades/{id} | settlements | custody | cash | ledger?account=&security_id=
     | balance-sheet | pnl-explain?date= | nav-explain
```

## Environment note

The spec recommends Next.js + FastAPI + PostgreSQL + NumPy/QuantLib. The environment this
was built in had no package registry access, so the implementation is standard-library
only: `http.server` instead of FastAPI, `sqlite3` instead of PostgreSQL, pure-Python
numerics, a vanilla-JS terminal UI instead of Next.js. The boundaries were kept so those
swaps are local: `api/service.py` is framework-agnostic (a FastAPI app would be a thin
adapter), `store.py` is the only persistence code, and `BondPricer` sits behind the
`Instrument` interface where QuantLib would go.
