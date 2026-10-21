# FinSim Live Terminal — status after the first-deploy integration (2026-10-21 ~02:00 ET)

Scope of this pass: make the served process start, serve the UI, quote live, preview and execute paper trades in
stocks / ETFs / options / futures across the three books, persist them and survive a restart — on an ephemeral port in a
temp `FINSIM_HOME`. Packages still being built by other agents were not edited (WP-10 bonds, WP-17 PB OTC swaps, WP-18
dividends, WP-21 private credit, WP-23 lifetimes/reset, WP-26 TRS/XCCY); the server tolerates their absence (health
`modules` = `missing`, the API answers 501 NOT_BUILT naming the package, the UI shows the page as coming online).

## What works (evidence: the smoke run below, `tests/`, `docs/design_proposals/BUILD_NOTES.md`)

- `py -3.12 -m finsim serve --port 0 --home <temp>`: starts in ~1 s, exclusive port, lock file with pid/port/version,
  `terminal.db` under the temp home, three books (P/F/I) seeded once, `integrity("ledger")` ok at start.
- Workers: quote worker, matcher, lifecycle scheduler and supervisor all alive; the `lifecycle.*` handlers are now registered
  on the bus (they were not before this pass); no errors or warnings in `terminal.log` across three starts.
- Modules bound at the last smoke: orders, options, futures, fx, credit, lending, hedges, risk, compliance, strategies,
  performance, prime (Prime service + headroom hook now installed), private, repo (stub), lifecycle, news, and — landed
  during this pass — dividends and private_credit. Missing: lifetimes (WP-23), swaps / swaps_trs (WP-17/26), bonds (WP-10).
- Instruments: search (`/api/instruments?q=`) for SPY / AAPL; resolve of the listed contract `ESZ26` (FULL, expiry
  2026-12-18) and of Yahoo's continuous `ES=F` (now a LIMITED reference row that points at the listed contract).
- Quotes from Yahoo with freshness fields (`quality` close, `source`, `delay_label`, `quote_time`, `fetched_at`, `age_s`);
  the UI renders `T.freshness()` chips on every price (ticket, preview, markets, portfolio, instrument, watchlists, chain,
  hedge).
- Orders: preview of a market BUY 10 SPY in `P1:US_EXCHANGE` (ok, fee 1.00, sweep-in 7,691.50 disclosed, "rests until the
  session opens" because the smoke ran at ~02:00 ET), submit → `O-000001 WORKING`, replay of the same request id →
  `X-Replayed: 1`; positions / cash / transactions / journal / integrity / treasury endpoints answer; integrity stays ok.
- Operations: `POST /api/shutdown` exits the process (exit 0, gone from tasklist in < 1.5 s); a second `serve` on the same
  home is refused with exit 3 in 0.5 s naming the owner's pid/port; a restart on the same home finds the same order, the same
  positions shape, the same Treasury view and a clean integrity report.
- Static UI check: every `/api/...` path built in `static/**/*.js` exists in the route table (api.py, api_views.py,
  api_products.py) — no mismatches; no glossary page or `gl()` links (only two comments saying there is none); the header
  tag reads PAPER TRADING.

## What is partial / not exercised live

- The fill path (matcher fill at the open, settlement, sweep-in execution) was not exercised against the live session: the
  smoke ran while US equities were closed, so the order rests WORKING (correct, never invented). Fills are covered by the
  unit tests (`test_terminal_orders`, `_equities`, `_sweeps`, `_futures`, `_options`) and the fixture server.
- Cboe chains were not fetched during the smoke (`providers.cboe` = null); the chain path is covered by tests only.
- Private Markets: the Fund Book opened its Private Markets account at first run (INFO attention: NAV 25,000,000.00 meets the
  qualified-purchaser threshold) — by design; `private.reseed` is not called by the housekeeping job (WP-19 ask, open).
- Lifetime reset / onboarding (`/api/books/{code}/reset`, `/onboard`) answer 501 NOT_BUILT until WP-23 lands; bonds and
  OTC swaps likewise (WP-10, WP-17, WP-26).
- Python 3.14: not re-run in this pass (time); each package reported its own 3.14 run green in BUILD_LOG.md.

## Test counts (py -3.12, one process, `py -3.12 -m unittest discover -s tests -t . -p "test_terminal_*.py"`)

- Before this pass (orchestrator, 01:34): 364 tests, 1 failure (test isolation), 1 skipped.
- After this pass (three runs, 01:45 / 01:52 / 01:57): **365 tests, OK, 1 skipped** (the skip is `test_terminal_marks`
  Build 2 execution). 31 terminal test files. Runtime ~67 s.

## Smoke results (scratchpad `integ/smoke.py`, three fresh homes; the final run: 30 checks pass / 0 fail)

| check | result | evidence |
|---|---|---|
| server starts / health | pass | version 1.0.0, schema 1, offline false, db under the temp home, startup 1.1 s |
| workers alive | pass | quotes / matcher / lifecycle / supervisor true |
| integrity at start | pass | ok, mode full, no problems |
| books | pass | P "Personal Book", F "Fund Book", I "Institutional Book" |
| Personal US Exchange account | pass | `P1:US_EXCHANGE`, kind TRADING, OPEN |
| search SPY / AAPL | pass | ids SPY; AAPL (+ AAPL-31 / AAPL-46 bond rows) |
| resolve ES=F | pass (after fixes 7 + 8 in BUILD_NOTES) | 200, kind INDEX, support LIMITED; first run: 404 request-id rule, second: 400 UNSUPPORTED |
| resolve ESZ26 | pass | 200, FUTURE, FULL, expiry 2026-12-18 |
| quotes with freshness | pass | SPY 769.05 / AAPL 339.5, quality close, Yahoo chart, "up to 15 min delayed", quote_time + fetched_at + age_s |
| preview market BUY 10 SPY | pass | ok, gross 7,690.50, fee 1.00, net -7,691.50, sweep-in 7,691.50, fill_now false, session CLOSED (next open Wed 09:30 ET) |
| submit / replay | pass | O-000001 WORKING filled 0; same request id → X-Replayed 1 |
| positions / cash / transactions / journal / integrity / treasury | pass | 200 each; integrity ok (ledger mode) |
| health after trading | pass | integrity ok; attention_open 1 (INFO PRIVATE_MARKETS_UNLOCKED for F1); providers yahoo/treasury/fred/finra/frankfurter/news ok, cboe not yet used |
| POST /api/shutdown | pass | exit 0, process gone from tasklist in 0.85-1.34 s |
| duplicate serve refused | pass | exit 3 after 0.51 s: "another FinSim Terminal (PID …, port …) owns <home>" |
| restart persistence | pass | O-000001 WORKING still there, positions shape same, integrity ok, Treasury view byte-identical |
| no process left behind | pass | driver killed none; Win32_Process shows no `finsim serve` |

## Known issues

1. `accounting.reserved_c` double-counts a resting SELL_SHORT whose collateral already left 1010 (WP-06 observation; not
   hit by tests).
2. `accounting.pb_headroom_c` counts a positive swap PV as long market value (WP-09 observation; swaps not built yet).
3. `private.reseed` is not wired into the housekeeping job (WP-19 ask).
4. The 409 PREVIEW_STALE body carries an unstored preview (no preview_id), so the ticket's Accept re-previews first (WP-13
   note) — works, one extra round trip.
5. Ticket CSS is injected from `ticket.js` rather than `terminal.css` (cosmetic).
6. `accounting.py` is 1,344 lines (over the ~900 guideline; a split needs a new module).
7. The CME/SIFMA calendar is approximated by the NYSE list (decisions s7.2, stated on screen).

## Package state (from BUILD_LOG.md, as of this pass)

| package | state | tests (3.12 / 3.14) |
|---|---|---|
| WP-00 core (db, bus, ledger, accounting, calendar, sessions, instruments, books, treasury, quotes contract) | done | 79/79 both |
| WP-01 quotes (http, providers, cache, worker, rates, marks) | done | 27/27 + marks 13 pass 1 skip, both |
| WP-02 orders / execution / matcher | done | 29 (orders 23, sweeps 4, equities 2), both |
| WP-03 options + hedges | done | 15/15 + 15/15, both |
| WP-04 futures | done | 10/10 both |
| WP-05 FX spot + currency loans | done | 5/5 + 7/7 both |
| WP-06 lending + repo stub | done | shorts 3/3 + lending 8/8 both |
| WP-07 prime brokers | done | 8/8 both |
| WP-08 lifecycle | done | 14/14 + persistence 1/1 both |
| WP-09 risk / compliance | done | 9/9 both |
| WP-10 bonds | being built | — (module missing; 501 NOT_BUILT) |
| WP-11 server + API | done | api 17/17 + server 9/9 both |
| WP-12 UI shell / components / pages / fixture server | done | static 8/8 both |
| WP-13 ticket / preview / hedge panel | done | via static 8/8 (fixtures) + browser check on the fixture server |
| WP-14 launcher / CLI / installer | done | 38/38 both |
| WP-17 PB OTC swaps | being built | — |
| WP-18 dividends | being built (module landed during this pass, imports and binds) | — |
| WP-19 private markets | done | 7/7 both |
| WP-20 performance | done | 11/11 both |
| WP-21 private credit | being built (module landed during this pass, imports and binds) | — |
| WP-22 daily news | done | 12/12 both |
| WP-23 lifetimes / reset | being built | — (module missing; reset/onboard 501) |
| WP-24 strategies | done | 19/19 both |
| WP-25 glossary | cancelled (owner) | — |
| WP-26 TRS / XCCY | being built | — |

Rules kept during this pass: temp homes only (never `~/.finsim`), never ports 8765 / 8865, no git commands, one test
process at a time, every started server stopped (shutdown → exit 0 verified three times), no edits to the files of the
packages still being built.
