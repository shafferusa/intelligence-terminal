# FinSim Live Terminal — map (C1, directive "how to work" 1)

One page: the modules, the data flow, the chart and cash accounts, the Treasury definition, where each figure is computed
(one function each) and the books / lifetime model. Authoritative detail: `TERMINAL_DESIGN.md`. WP-15 updates this page at
integration.

## Modules (`finsim/finsim/terminal/`)

| module | holds |
|---|---|
| `config.py` | paths (`FINSIM_HOME`, `FINSIM_TERMINAL_DB`), the three books and the Test Book, thresholds (ECP floor, PB/OTC, QP), account catalogue, prime brokers, fee / sweep / preview constants, UA strings |
| `units.py` | cents ↔ Decimal (`to_c`, `from_c`), `qprice`, `mul_c`, `fx_c`, the §5.3 rounding plug `base_with_plug`, `fmt_money` |
| `clock.py` | `Clock` / `RealClock` / `FakeClock`, New York helpers, `accounting_day` (closes 17:00 ET), `trade_date` (CME evening → next business day) |
| `calendar.py` | `TradingCalendar`: the NYSE holiday file + the rule fallback, early closes, CME full closures, business-day math, `settlement_date` |
| `sessions.py` | `SessionTable`: per-group OPEN / CLOSED / BREAK / HOLIDAY / WEEKEND, `day_order_expiry`, `settle_time`, `session_overrides` |
| `db.py` | `Database`: the complete §3 DDL, migrations, PRAGMAs (WAL), one writer + a reader pool, backup, seed (books, chart, settings), id sequences |
| `ledger.py` | `CHART`, `Line` / `Entry`, `post()` (per-currency + base validation, the plug, `gl_balances` projection), balances, FIFO lots |
| `commands.py` | `Command` / `Result` / errors, `TxContext`, `CommandBus.execute` (request_id replay / reuse, BEGIN IMMEDIATE, scoped integrity before COMMIT) |
| `accounting.py` | every read model: cash, available, buying power, positions, NAV, balance sheet, P&L explain, treasury, transactions, obligations, liquidity, snapshots, `integrity()` + INTEGRITY_HOLD |
| `accounting_checks.py` | the ledger-level integrity set L1–L25 as pure functions of a connection (the companion of `accounting.py`) |
| `instruments.py` | `InstrumentMaster`, id rules (OCC, futures, FX), `FUTURES_SPECS`, seeding from `universe.json`, `tradeable_in` |
| `tables.py` | fees, rates and policy indices, borrow tiers, PB schedules (seed), session rules, settle times, compliance limits, book profiles, the §1.3 `CONVENTIONS` |
| `books.py` | books catalogue, lifetime ids, `create_lifetime`, `ensure_first_run`, `eligibility`, `require_pb_otc` / `require_onboard` / `require_private`, `unlock_private` |
| `treasury.py` | handlers `treasury.init_capital` / `deposit` / `withdraw` / `transfer`, `account.update_settings`; `sweep_in()` / `sweep_out()` |
| `quotes/__init__.py` | `QuoteRecord`, `freshness()`, `is_executable()`, `MarkView`, the `MarkSet` protocol, `FixedMarks` |
| later packages | `quotes/*` providers and cache (WP-01), `orders` / `execution` (WP-02), products, `lifecycle` (WP-08), `risk` / `compliance` (WP-09), `api` / `server` (WP-11), `static/` (WP-12/13), `app` (WP-14) |

## Data flow

```
quote worker (Yahoo / Cboe / Treasury / FRED / FINRA)  →  QuoteCache / ChainCache (freshness, never "live")
      →  Matcher (pure evaluate on observed prints)  →  CommandBus.execute(order.match …)
      →  handlers post journal entries (ledger.post: balanced per currency and in base)  →  gl_balances (the only projection), lots, fills, settlements
      →  scoped integrity (L1–L25 on the touched keys) before COMMIT  →  on_commit listeners
      →  accounting.py read models (ledger at cost + one mark snapshot)  →  api.py JSON  →  the UI (polls small snapshots)
lifecycle jobs (real clock, idempotent keys)  →  the same bus (settlements, accruals, VM, collateral marks, sweeps, snapshots)
```

Everything that changes money or positions is a command: one SQLite transaction, one `request_id` (replayed on retry, 409
when reused with another payload, no row on rejection).

## The chart and the cash accounts

Storage sign is debit-positive on every account; the natural sign for display flips liabilities, equity and income.
Balances live per `(account, gl, currency[, instrument])`.

- cash: `1010` settled (the only spendable cash); `1200` / `1250` receivables (unsettled sales, FX); `2100` / `2350` payables;
  `1230` accrued cash interest; `1400` cash collateral at the lender (102 %, not spendable).
- positions at cost: `1100` equities/ETFs, `1110` bonds, `1120` private shares, `1130` private credit at amortised cost,
  `1700` options bought; `2600` short securities at proceeds, `2900` options written. Market value, unrealised, FX result and
  NAV are computed, never posted.
- margin: `1300` futures IM, `1310` option margin, `1320` / `1330` OTC VM posted / IM, `2330` OTC VM received.
- financing: `2700` PB financing, `2750` / `2755` currency loans and their accrued interest, `2500` repo (Build 2).
- equity: `3000` contributed capital (Treasury only), `3100` Treasury advances (nets to zero per lifetime), `3900` FX clearing.
- results: `4000` realised, `4400` futures VM, `4450` swap results; income `4200` / `4300` / `4320` / `4350`; expenses `5000` fees,
  `5100` / `5150` / `5300` / `5400` financing costs.

## Treasury (directive §7, sentence TREASURY)

Treasury is the lifetime's own funds: contributed capital plus retained results (its cash interest) minus what it has
advanced to the accounts; advances are internal transfers that net to zero across the lifetime. Opening capital posts once
(`init:capital:{lifetime}`); deposits and withdrawals are capital flows, never P&L; a sweep-in moves exactly an account's
shortfall as one linked transfer; sweep-out returns idle settled cash after 17:30 ET. Formulas (`Accounting.treasury`):
`capital = −balance(3000)`, `advances = +balance(3100)` of the Treasury account, `retained = Treasury NAV − capital +
advances`, `available = settled USD − reserve`; identity `Treasury NAV = capital + retained − advances` to the cent.
`excess liquidity = Treasury available + Σ accounts max(0, available) − futures maintenance shortfalls`; `encumbrance =
Σ (1400 + 1300 + 1310 + 1320 + 1330)` (`Accounting.liquidity`).

## One function per figure (`accounting.Accounting`)

| figure | function |
|---|---|
| settled / pending / held at lender / reserved / available per currency | `cash(account_id)`; `available(account_id, ccy, settle_date)` (module `available_c`) |
| buying power (own + Treasury + PB headroom) | `buying_power(account_id, ccy)`; `pb_headroom_c` (WP-07 may replace via `pb_headroom_fn`) |
| positions, lots, average cost, market value, unrealised, realised / income / financing / fees to date, day P&L, weight, days held | `positions(scope)`, `position()`, `lots()` |
| NAV, assets, liabilities, quality / partial, unrealised, FX result, capital, advances, retained | `nav(scope)` (per account: `_account_calc`) |
| balance sheet rows and the memo tie `NAV = capital + advances + retained` | `balance_sheet(scope)` |
| P&L today / MTD / YTD / ITD with the explain and residual 0 | `pnl(scope, period)` |
| capital flows in a window | `flows(scope, from_ts, to_ts)` |
| the daily snapshot row (NAV, cumulative components, flows_day, pnl_day, detail) | `snapshot_row(scope, snap_date)` + `insert_snapshot` (written by the SNAPSHOT job) |
| Treasury view | `treasury(lifetime_id)` |
| ledger transactions (every entry once, lines expanded) | `transactions(scope, …)` |
| pending settlements, borrows, loans, margin, collateral, OTC | `obligations(account_id)` |
| excess liquidity, encumbrance | `liquidity(scope)` |
| integrity L1–L25 (error, hold) and M1–M5 (warn) per lifetime | `integrity(mode, touched, snapshot, lifetime_id)`; `is_held(lifetime_id)` |

NAV identity used throughout: per account and currency, `S` = assets − liabilities at cost, `B` = their base at posting rates,
`U` = Σ (market value − cost); `NAV = Σ fx(S + U)`, `unrealised = Σ [fx(S + U) − fx(S)]`, `fx_result = Σ_{c≠USD} [fx(S) − B]`,
so `NAV = capital + advances + realised + income − financing − fees + unrealised + fx_result` exactly.

## Books and lifetimes (§13.10)

Three books on first run — Personal `P` (USD 1,000,000), Fund `F` (USD 25,000,000), Institutional `I` (USD 1,000,000,000);
the test fixture uses the Test Book `T` (USD 10,000,000). A lifetime is one run of a book (`P1`, `P2`, …); every account is
`{lifetime}:{role}` (`TREASURY`, `US_EXCHANGE`, `DEBT`, `CREDIT`, `FOREIGN_FX`, `PRIVATE`, `PB_GS` …), so every ledger row
belongs to exactly one lifetime. Books never transfer to each other (`CROSS_BOOK`). Scopes: an account, `{lifetime}:ALL`,
`ALL` (the selected book's open lifetime), `ALL_BOOKS` (every open lifetime). `books.ensure_first_run` (server start,
`make_runtime`) creates the missing lifetimes and also posts `init:capital:{lid}` for any OPEN lifetime whose init
command is missing (a crash between the row commit and the capital command heals on the next start; idempotent). A reset (WP-23) closes the lifetime read-only
(the ledger refuses CLOSED accounts) and opens the next one with the typed capital. Eligibility (`books.eligibility`):
ECP floor USD 10M; PB/OTC at the editable threshold (default 25M) or the Institutional profile, relationships persist;
Private Markets at the qualified-purchaser threshold per profile (5M / 25M / 0).
