# FinSim Live Terminal — map (C1, directive "how to work" 1)

One page: the modules, the data flow, the chart and cash accounts, the Treasury definition, where each figure is computed
(one function each), the books / lifetime model, the jobs and the OTC wiring.  Authoritative detail: `TERMINAL_DESIGN.md`.
Final at the WP-15 integration (2026-10-21): every package of the workplan is merged except the cancelled WP-25 (no glossary).

## Modules (`finsim/finsim/terminal/`, ~35,700 lines, standard library only)

| module | holds |
|---|---|
| `config.py`, `units.py`, `clock.py`, `calendar.py`, `sessions.py`, `tables.py` | paths and the books / thresholds catalogue; cents ↔ Decimal (`to_c`, `fx_c`, the rounding plug); the clock (`RealClock` / `FakeClock`, NY helpers, accounting day); the NYSE / CME calendar; per-group sessions; fees, rates, PB schedules, compliance limits and the on-screen `CONVENTIONS` |
| `db.py`, `ledger.py`, `commands.py` | SQLite (WAL, one writer + readers, the full DDL, seeds); `post()` (balanced per currency and in base), balances, FIFO lots; `CommandBus` (request-id replay, BEGIN IMMEDIATE, scoped integrity before COMMIT) |
| `accounting.py`, `accounting_checks.py` | every read model and `integrity()` (L1–L25 ledger checks, M1–M5 mark checks, per-lifetime INTEGRITY_HOLD) |
| `instruments.py`, `books.py`, `treasury.py` | the instrument master (universe seed, foreign-listing repair, futures listings, `otc_reference_ids`); books, lifetimes, eligibility (ECP floor, PB/OTC threshold, QP); capital, transfers, sweep-in / sweep-out |
| `quotes/` (`http`, `providers`, `parsers`, `cache`, `rates`, `worker`), `marks.py` | Yahoo / Cboe / Treasury.gov / FRED / Frankfurter / FINRA fetchers and the `FakeProvider`; the quote and chain caches (freshness, pins); curve / rate / FX stores; the quote worker thread and its refresher slots; `MarkProvider` (per-kind dispatch: options, bonds, private, private credit, swaps) |
| `orders.py`, `orders_rules.py`, `orders_preview.py`, `execution.py`, `matcher.py` | order validation, preview, submit / cancel / resume, trailing stops, AON packages; fills, `fund()` (own cash → one linked Treasury sweep → PB financing → refuse or the §11.6 overdraft rule); the matcher thread |
| `products/options.py`, `products/futures.py`, `fx.py`, `credit.py`, `lending.py`, `lending_accruals.py`, `repo.py` | listed options (margin, exercise, expiry), futures (IM, daily VM, final), FX spot and currency loans, securities lending (borrow, 102 % collateral, nightly fee / rebate), repo (NOT_IN_THIS_BUILD stub) |
| `prime.py`, `lifetimes.py` | prime-broker accounts, editable schedules, financing / credit interest / monthly minimum, the PB headroom hook; Reset lifetime with history, onboarding (ISDA Master + CSA + PBA), book defaults |
| `products/swaps.py`, `products/swaps_trs.py` | PB OTC swaps: IRS (dealing at model par ± the PB half-width, OFF_MARKET refusal, IM, signed DV01, daily VM CTM / STM, TERM or compounded SOFR, payments, termination, contract views); TRS and cross-currency (resets, financing and dividend accrual, notional exchanges, MTM FX resets) through the hooks WP-17 calls |
| `products/private.py`, `products/private_credit.py`, `products/dividends.py`, `products/bonds.py` + `bonds_jobs.py` | Private Markets (sourced master, listings / acquisitions, negotiated secondaries, lock-ups); private credit loans; dividends and payments in lieu; bonds (Build 2: accrual, coupons, maturity, monthly model benchmarks; execution behind `build2_bonds`) |
| `risk.py`, `compliance.py`, `hedges.py`, `strategies.py` + `strategies_catalogue.py`, `performance.py`, `news.py` | risk report (inform only), compliance notices, hedge candidates, the 60 strategy templates, performance statistics, Daily News |
| `lifecycle.py`, `lifecycle_jobs.py`, `lifecycle_eod.py` | the lifecycle scheduler thread (30 s tick, catch-up by state, passes, backoff), the money jobs and `accrue_segment`, the end-of-day / product jobs and the REGISTRY |
| `api.py`, `api_views.py`, `api_products.py`, `server.py`, `app.py`, `winpaths.py`, `smoke.py` | the Router and JSON codec (cents → decimal strings), the routes, the runtime (binds every optional module), the single-instance lock, the supervisor, `serve`; the launcher / installer; Windows known folders; the network probe |
| `static/` | the UI: `core.js`, `terminal.css`, `index.html`, 17 components (ticket, preview, hedge, swapticket, privateticket, privatecredit, pbschedule, lifetimes, performance, grid, freshness, chart, chain, header, sidebar, bookswitcher, attention) and 16 pages |

## Data flow

```
quote worker thread (Yahoo / Cboe / Treasury.gov / FRED / FINRA / Frankfurter; FakeProvider offline)
      →  QuoteCache / ChainCache / CurveStore / RateStore / FxStore (freshness, never "live")
      →  Matcher thread (pure evaluate on observed prints)  →  CommandBus.execute(order.match …)
      →  handlers post journal entries (balanced per currency and in base)  →  gl_balances, lots, fills, settlements, otc_*
      →  scoped integrity (L1–L25 on the touched keys) before COMMIT  →  on_commit listeners
      →  accounting.py read models (ledger at cost + one mark snapshot)  →  api*.py JSON  →  the UI (polls small snapshots)
lifecycle thread (real clock, idempotent keys)  →  the same bus: settlements, accruals, VM, fixings, payments, resets,
      expiries, collateral marks, sweeps, marks recorded at the close, snapshots, housekeeping, integrity
```

Everything that changes money or positions is a command: one SQLite transaction, one `request_id` (replayed on retry, 409
when reused with another payload, no row on rejection).  Nothing in a request handler fetches from the network.

## The chart and the cash accounts

Storage sign is debit-positive on every account; the natural sign for display flips liabilities, equity and income.
Balances live per `(account, gl, currency[, instrument])`.

- cash: `1010` settled (the only spendable cash); `1200` / `1250` receivables (sales, FX); `2100` / `2350` payables;
  `1230` accrued cash interest; `1400` cash collateral at the lender (102 %, not spendable).
- positions at cost: `1100` equities / ETFs, `1110` bonds, `1120` private shares, `1130` private credit at amortised cost,
  `1700` options bought; `2600` short securities at proceeds, `2900` options written.  Market value, unrealised, FX result
  and NAV are computed, never posted.
- accrued: `1210` dividends receivable, `1220` bond / loan interest, `1225` swap receive legs, `1410` rebates; `2300` /
  `2310` / `2320` / `2340` (swap pay legs) / `2755` accrued expenses.
- margin and OTC: `1300` futures IM, `1310` option margin, `1320` / `1330` OTC VM posted / IM, `2330` OTC VM received,
  `1360` / `2360` cross-currency notional lent / borrowed (in their own currencies).
- financing: `2700` PB financing, `2750` currency loans, `2500` repo (Build 2).
- equity: `3000` contributed capital (Treasury only), `3100` Treasury advances (nets to zero per lifetime), `3900` FX clearing.
- results: `4000` realised, `4400` futures VM, `4450` swap results; income `4200` / `4300` / `4320` / `4350`; expenses
  `5000` fees, `5100` / `5150` / `5300` / `5400` financing costs.

## Treasury (directive §7, sentence TREASURY)

Treasury is the lifetime's own funds: contributed capital plus retained results (its cash interest) minus what it has
advanced to the accounts; advances are internal transfers that net to zero across the lifetime.  Opening capital posts
once (`init:capital:{lifetime}`); deposits and withdrawals are capital flows, never P&L; a sweep-in moves exactly an
account's shortfall as one linked transfer; sweep-out returns idle settled cash after 17:30 ET.  Formulas
(`Accounting.treasury`): `capital = −balance(3000)`, `advances = +balance(3100)` of the Treasury account, `retained =
Treasury NAV − capital + advances`, `available = settled USD − reserve`; identity `Treasury NAV = capital + retained −
advances` to the cent.  `excess liquidity = Treasury available + Σ accounts max(0, available) − futures maintenance
shortfalls`; `encumbrance = Σ (1400 + 1300 + 1310 + 1320 + 1330)` (`Accounting.liquidity`).

## One function per figure (`accounting.Accounting`)

| figure | function |
|---|---|
| settled / pending / held at lender / reserved / available per currency | `cash(account_id)`; `available(account_id, ccy, settle_date)` (module `available_c`, floored at 0; `liquidity_split`) |
| buying power (own + Treasury + PB headroom, net of commitments) | `buying_power(account_id, ccy)` (`buying_power_split`); PB headroom from `prime.pb_headroom_detail` (the hook) |
| positions, lots, average cost, market value, unrealised, realised / income / financing / fees to date, day P&L, weight, days held | `positions(scope)`, `position()`, `lots()`; a swap row's mark is the NAV overlay, the live model mark in `extra.model_mark` |
| NAV, assets, liabilities, quality / partial, unrealised, FX result, capital, advances, retained | `nav(scope)` (per account: `_account_calc`) |
| balance sheet rows and the memo tie `NAV = capital + advances + retained` | `balance_sheet(scope)` |
| P&L today / MTD / YTD / ITD with the explain and residual 0 | `pnl(scope, period)` |
| capital flows in a window | `flows(scope, from_ts, to_ts)` |
| the daily snapshot row | `snapshot_row(scope, snap_date)` + `insert_snapshot` (the SNAPSHOT job) |
| Treasury view | `treasury(lifetime_id)` |
| ledger transactions (every entry once, lines expanded) | `transactions(scope, …)` |
| pending settlements, borrows, loans, margin, collateral, OTC | `obligations(account_id)` |
| excess liquidity, encumbrance | `liquidity(scope)` |
| integrity L1–L25 (error, hold) and M1–M5 (warn) per lifetime | `integrity(mode, touched, snapshot, lifetime_id)`; `is_held(lifetime_id)` |

NAV identity used throughout (pinned per lifetime and per account by `tests/test_terminal_reconcile.py`): per account and
currency, `S` = assets − liabilities at cost, `B` = their base at posting rates, `U` = Σ (market value − cost) incl. futures
open trade equity and swap overlays; `NAV = Σ fx(S + U)`, `unrealised = Σ [fx(S + U) − fx(S)]`, `fx_result = Σ_{c≠USD}
[fx(S) − B]`, so `NAV = capital + advances + realised + income − financing − fees + unrealised + fx_result` exactly.

## Books and lifetimes (§13.10)

Three books on first run — Personal `P` (USD 1,000,000), Fund `F` (USD 25,000,000), Institutional `I` (USD
1,000,000,000); the tests also use the Test Book `T` (USD 10,000,000).  A lifetime is one run of a book (`P1`, `P2`, …);
every account is `{lifetime}:{role}` (`TREASURY`, `US_EXCHANGE`, `DEBT`, `CREDIT`, `FOREIGN_FX`, `PRIVATE`, `PB_GS` …).
Books never transfer to each other.  Scopes: an account, `{lifetime}:ALL`, `ALL` (the selected book's open lifetime),
`ALL_BOOKS` (Σ open lifetimes).  Eligibility (`books.eligibility`): ECP floor USD 10M; PB/OTC at the editable threshold
(default 25M) or the Institutional profile; relationships recorded at onboarding (`agreements`: ISDA Master, CSA, PBA)
survive a later NAV fall; Private Markets at the qualified-purchaser threshold per profile (5M / 25M / 0).  A reset
(WP-23) closes the lifetime read-only and opens the next one with the typed capital.

## Lifecycle jobs (`lifecycle_eod.REGISTRY`, the tick order)

SETTLE_DUE → ACCRUE_DAILY → BOND_ACCRUE → PAY_MONTHLY → PB_MONTHLY_MIN → DIVIDEND → DIVPAY → PC_PAY → BOND_COUPON →
BOND_MATURITY → BOND_LISTING → SWAP_FIX → SWAP_PAY → TRS_RESET → XCCY_EVENTS → OPTION_EXPIRY → FUTURES_VM → FUTURES_FINAL →
SWAP_VM → OPTION_MARGIN_MARK → COLLATERAL_MARK → BORROW_CLEANUP → SWEEP_OUT → MARKS_RECORD (phase 1, iterated until a
pass completes nothing new) → SNAPSHOT (phase 2) → EXPIRE_ORDERS → HOUSEKEEPING (incl. the private master sync) →
INTEGRITY_FULL (phase 3).  ACCRUE_DAILY kinds include the swap legs `swaprcv` / `swappay`, `trsfin`, private credit and PB
financing / credit.  `POST /api/jobs/run {job}` (loopback) runs one job now.

## OTC wiring (WP-17 + WP-26, end to end since WP-15)

`server._bind_optional` binds `services.swaps` and `services.swaps_trs` (their handlers on the bus); routes `POST
/api/otc/preview`, `POST|GET /api/otc/contracts`, `POST /api/otc/contracts/{id}/terminate`; the PB page's OTC tab mounts
`components/swapticket.js` in an eligible lifetime (the ELIGIBILITY sentence otherwise).  `swaps` owns the gates (OTC kind →
PB account → eligibility), the IRS, SWAP_VM for every kind (TRS / XCCY valued through `mark_trs` / `mark_xccy` as of the
VM date; CAUGHT_UP when only a later price exists) and the views; `swaps_trs` owns TRS / XCCY terms, booking, marks,
accruals, resets and exchanges.  The VM reference after a reset is ordered by command (`swaps._reference_mark_c`, one
definition).  The names OTC contracts observe without holding them (`instruments.otc_reference_ids`: TRS underlyings, XCCY
FX pairs) are pinned by the quote worker at HELD, recorded by MARKS_RECORD and, for a TRS, checked for dividends.
