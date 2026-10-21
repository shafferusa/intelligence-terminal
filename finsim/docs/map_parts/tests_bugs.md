# Map part: tests + read-only bug hunt (FinSim, package `finsim`)

Author: mapping subagent, 2026-10-20 (read-only; no code, tests or Python were run; no git; no network).
Legend: **VERIFIED** = I read the code at the cited path:line. **INFERRED** = reasoning from what I read, not confirmed by execution.
Paths are relative to `C:\Users\logan\intelligence-terminal\finsim\` unless absolute.

---

## Part 1 - Tests

### 1.1 How tests run (VERIFIED)

- `pyproject.toml` `[tool.pytest.ini_options] testpaths = ["tests"]`; optional extra `test = ["pytest>=8"]`. Package has
  `dependencies = []` (standard library only). Command (INFERRED from config, not run): from `finsim\`:
  `python -m pytest tests -q` or a subset, e.g. `python -m pytest tests/test_daily.py tests/test_financing.py -q`.
  The tests are `unittest.TestCase` style (helpers take `tc` = a TestCase, `tests/helpers.py:20`), so
  `python -m unittest tests.test_daily` also works (INFERRED).
- `tests/__init__.py` is empty (0 bytes) - `tests` is a package so `from tests.helpers import ...` works.
- `__pycache__` holds `cpython-312` and `cpython-314-pytest-9.1.1` files, i.e. tests have been run under Python 3.12 and
  3.14 with pytest 9.1.1 (VERIFIED by filenames only).

### 1.2 Fixtures: `tests/helpers.py` (VERIFIED, 102 lines)

- `helpers.py:5` inserts the repo root on `sys.path`.
- `make_world(seed=42, start="2026-01-05", capital=10_000_000, store=None, regime="NORMAL_GROWTH")` (`helpers.py:13-17`):
  `EventStore(":memory:")` -> `World.create("t","test",seed,start,store=store,initial_regime=regime)` (this RUNS ONE
  SIMULATED DAY: `world.py:702-704`) -> `w.create_portfolio("Test","PERSONAL",D(capital))`. Returns `(w, pf, store)`.
  NOTE the default test capital is exactly **10,000,000** - the same figure as the Treasury bug (see Part 2b).
- `assert_ledger_invariants(tc, w, pf)` (`helpers.py:20-101`) - the existing "ledger integrity" check, test-side only:
  trial balance balances (`:22-23`); ledger NAV == `w.pnl.compute_summary(pf)["nav"]` (`:24-25`); per position lots ==
  quantity and cost (`:37-38`), valuation adj == MV - cost (`:39`), long/short cost and MTM accounts (`:40-50`),
  custody box (`:54`), pledges <= custody (`:56`), borrowed qty == open loans (`:57`); cash GL `1010:{ccy}` == cash
  account base value (`:58-59`); clearing margin 1300 == futures IM + options margin (`:60-62`); collateral 1400 (`:63`);
  borrow fees 2310, rebates 1410, manufactured divs 2320 (`:64-66`); repo 2500 / reverse repo 1500 / accrued 2330 / 1240
  (`:67-70`); margin loan 2700 / 2340 (`:71-72`); FX forwards 1600, FX receivable/payable 1250/2350 (`:73-76`); OTC
  1800/2800 and closed OTC trades off balance sheet (`:77-83`); VM received 2450 (`:84`); lending 2460/1430/2370 (`:85-90`);
  investor fees 2360 (`:91-92`); Treasury-job debt 2380 (`:93-94`); CSA VM/IM (`:95-98`); base cash never negative (`:99`).
  It checks ONE book at a time; it has NO cross-book check (sum of capital moved between Treasury and books nets to zero,
  Treasury cash == fundings - allocations + returns), and NO save/reload equality check.
- The production twin is `World.check_integrity()` (`finsim/world.py:180-203`): only trial balance, ledger NAV ==
  economic NAV, and no negative custody; runs on load (`world.py:718`). Much weaker than the test helper.

### 1.3 Which test files test what (VERIFIED by import lines; counts = `def test_` occurrences)

Desk app (`finsim2`) - NOT FinSim, out of scope here:
- `test_fs2_*.py` (34 files): all import `finsim2` (Desk / research engine). Three also import small pieces of FinSim:
  `test_fs2_equations.py:9` (`finsim.quant.catalog.EQUATIONS`), `test_fs2_news.py:11` (`finsim.tzfallback`),
  `test_fs2_terminal.py:15` (`finsim.world.CommandError`) - keep those names stable or update these tests.
- `test_desk_*.py` (82 files) and `test_fxa_f10_verify_alerts.py`: Desk tests. MANY import `finsim.world`
  (e.g. `test_desk_core.py`, `test_desk_accruals.py`, `test_desk_orders.py`, `test_desk_otc_products.py` also imports
  `finsim.engines.counterparties`) because the Desk drives a FinSim `World` as its book engine; `*_static.py` and
  `test_desk_1b_schema*.py` import neither (static file checks). They are Desk tests, but **a FinSim change to
  `World`/engines can break them** - run them as a regression guard after the repair.
- `test_finsim2.py` (22 tests) and `test_research_api.py` (39 tests): import both `finsim.world` and `finsim2`.
- `_cdp.py`, `desk_lane4_shim.py`: Desk browser/e2e helpers.

Original FinSim (`finsim` only) - 35 files (38 other test_*.py minus test_finsim2, test_research_api, test_fxa_f10_verify_alerts):

| file | tests | imports / what it covers |
|---|---|---|
| test_api.py | 3 | `finsim.api.server`, `.service`, `store`, helpers - HTTP/API layer |
| test_app.py | 5 | `finsim` (app launcher) |
| test_backstop.py | 2 | service, store, world - Treasury backstop of overdrafts |
| test_banking_housing_news.py | 7 | service, investment_banking, realnews |
| test_career.py | 6 | service, calendar, clock, realmacro, realnews - career (REAL_TIME) saves |
| test_careers.py | 10 | events, store, world - jobs, missions, reviews |
| test_ccy_funding.py | 2 | ccy_funding, realmacro - currency loans |
| test_ccy_visibility.py | 4 | service, store |
| test_commodities.py | 6 | commodity_desk, options |
| test_daily.py | 9 | clock, events, store, world, helpers - the daily cycle / career clock |
| test_engines.py | 16 | calendar, models, market, pricing, world, helpers |
| test_expansion.py / test_expansion2.py | 5 / 6 | service, realfeed, commodities, fx_market, options_pricing, otc_extra |
| test_financing.py | 21 | events, store, world, helpers - repo, sec lending, prime, margin |
| test_fx_routing.py | 2 | realfeed, world |
| test_global_rates.py / test_rates_global.py | 3 / 3 | global_rates, pricing / commodities, options |
| test_infrastructure.py | 9 | server, service, events, log, migrations, store, version, world - saves, migrations, replay |
| test_institutions.py | 4 | institutions (AI desks) |
| test_live.py | 8 | service, store, world - live quotes / live execution |
| test_macro.py | 3 | macro |
| test_master_scenario.py | 1 | careers, events, store, world - long scenario |
| test_mbs.py | 2 | mbs |
| test_operations.py | 7 | world |
| test_options.py | 23 | engines, options, vol, store, world |
| test_options_global.py | 6 | service, store |
| test_otc.py | 18 | models, engines, counterparties, world |
| test_private_equity.py | 4 | service, private_equity |
| test_quant.py | 29 | `finsim.quant` only - pure math, no World |
| test_risk.py | 7 | risk |
| test_structured.py | 1 | collateral, pricing, structured |
| test_ticket_fixes.py | 5 | service, store |
| test_tzfallback.py | 4 | `finsim` (tzfallback) |
| test_universe_extra.py | 2 | fx_market, otc_extra |
| test_vertical_slice.py | 1 | events, world, helpers - end-to-end slice |

### 1.4 Runtime and memory estimate (INFERRED - nothing was run)

- Every World-based test pays for `World.create`, which bootstraps a simulated prehistory for the whole universe
  (`MarketEngine(..., prehistory_days=260)`, `engines/market.py:397-402, 533`; `world.py:269-276`) and then runs one full
  simulated day (`world.py:702-704`). Each `advance` runs the whole A-K daily cycle (`engines/simulation.py:34-139`).
- Counts (VERIFIED by grep of the files): test_financing 19 worlds / 62 advance calls; test_options 19 / 53; test_otc
  18 / 26 (loops up to range(20)); test_engines 14 / 18 (loop up to range(40)); test_daily 9 / 18 (a loop up to
  range(120)); test_careers 3 / 28; test_infrastructure 6 / 5; test_live 5 / 6; test_master_scenario 1 world, loops of
  15. Across the 35 FinSim files there are roughly 150-200 world creations.
- Estimate: 1-5 s per world creation, 0.2-1 s per simulated day -> the FinSim subset is on the order of 10-30 minutes
  single-process; peak memory ~0.3-1 GB (one or two worlds alive at a time, each holding bars for the full universe plus
  option chains). `test_quant.py` builds no World: seconds, negligible memory - the safe first run. Measure one small file
  before running more; do not run the Desk suites (test_desk_*, test_fs2_*) unless needed.

### 1.5 Gaps versus the directive (VERIFIED by what exists; "missing" = I found no such test)

- **Ledger integrity**: per-book only (`helpers.assert_ledger_invariants`). Missing: a cross-book check (the Treasury's
  cash explained by its own log; sum of capital in == sum of contributions; transfers net to zero between books), and a
  production check equal in strength to the test helper (`World.check_integrity` is weaker, `world.py:180-203`).
- **Persistence**: reload tests compare NAV and dates only (`test_backstop.py:44-45`, `test_daily.py:202-205`,
  `test_infrastructure.py:93-192`). Missing: trade count / positions / orders equality after reload; unflushed events at
  process exit; two processes writing one save (seq collision); a resilient (non-strict) load dropping a trade.
  `test_infrastructure.py:151-180` tests strict vs resilient load only on a deliberately damaged event.
- **Day advance**: almost every FinSim test drives `w.advance` / `catch_up` / `make_world` (which itself runs a simulated
  day). Removing the day advance invalidates most of the 35 FinSim test files; they need a fake live-quote provider instead
  of the seeded `MarketEngine`.
- **Treasury**: only `test_backstop.py` (2 tests). Missing: funding appears in the Treasury log; default capital; book
  deletion returning fresh capital; career bonuses landing in the Treasury book.
- **Weekend**: `test_daily.py:182-205` covers a SIMULATED career world across a weekend with a reload (passes by design).
  Missing: REAL-market saves (`real_market_ready` does a network refresh inside catch-up, `world.py:342-362`), live fills
  made before a weekend, and a Service-level restart.

---

## Part 2 - Bug hunt (read-only)

### 2.0 Core persistence model (VERIFIED) - needed for both bugs

- Event-sourced: only events are stored (`store.py:1-6`); table `events(world_id, seq, ..., PRIMARY KEY(world_id, seq))`
  (`store.py:25-27`). State is rebuilt by replay on load (`world.py:707-719`).
- `World.emit` (`world.py:128-136`) appends to memory and `_pending`; nothing reaches disk until `World.flush()`
  (`world.py:152-155`), which writes `_pending` and clears it ONLY after a successful insert. `next_seq()` is
  `len(self.events) + 1` (`world.py:122-123`).
- `derive()` (used by `post()` for ledger entries) is a no-op during replay (`world.py:138-142, 630-636`).
- Load: `World.load` -> `migrate()` -> `replay(strict)` -> `_relist_orphans()` -> `check_integrity()` (`world.py:707-719`).
  Migrations rebuild only WORLD_CREATED and MARKET_CLOSE payloads and drop nothing (`migrations.py:15-94`, outline read);
  they run in memory on every load and only the version number is written back (`service.py:265-266`).
  `Service.strict_replay` defaults to False unless env `FINSIM_STRICT_REPLAY=1` (`api/service.py:74`); in resilient mode a
  failing event is skipped and recorded in `replay_errors` (`world.py:161-178`), and the service only logs it
  (`api/service.py:267-268`).
- Every page access goes through `Service.world()`, which loads on first use and runs `catch_up` (process due days) on
  every call for career saves (`api/service.py:258-273`).
- Saves and ports (VERIFIED): `python -m finsim serve` defaults to `--db data/finsim.db` (relative to the current
  directory) and `--port 8000` (`finsim/__main__.py:13,15`); the app launcher uses `FINSIM_HOME` (default `~/.finsim`)
  `finsim.db` and port 8765 (`finsim/app.py:49,60,67-68,95`). The legacy DB is copied once, only when the app DB does not
  exist (`app.py:186-200`); after that the two files diverge. A snapshot of the DB is taken before a save is opened
  (`store.py:34-59`, keep 10).

### Ranking summary

Trades disappearing: S1 Live Trades "today" filter (display; certain to happen) > S2 two DBs / per-port localStorage
fallback (silent switch of save) > S4b partial day left unflushed after an exception > S3 resilient replay skipping a
trade on reload > S4 two processes on one DB (flush stuck forever) > S5 DAY orders expiring (orders, by design).
Treasury +~10M: T1 contributions into the Treasury book are invisible in the Treasury log, plus two hard 10,000,000
defaults > T2 career mission bonus (5,000,000 per mission for the default PORTFOLIO_MANAGER job; TREASURY_MANAGER job
receives ~EUR 9M monthly) > T3 deleting a fresh-funded book moves its cash into the Treasury > T4 legacy pool added on
top > T5 0 treated as blank. The owner's save would settle it: list CAPITAL_CONTRIBUTED and TREASURY_ALLOCATED events for
the Treasury book's portfolio_id (read-only SQL on the events table, filtering `portfolio_id`).

### 2a. "Trades disappear from a world after a couple of days or over a weekend" - suspects ranked

**S1 (most likely; VERIFIED code). The Live Trades page shows only trades whose trade_date equals the world's
current_date, and current_date moves at every processed day.**
- `app.js:1567-1572`: `const today = state.world.current_date; ... const fills = trades.filter(t => t.trade_date === today);`
  (also `todayOrders` filters `entered_date === today`, `app.js:1571`; OTC `trade_date === today`, `app.js:1574`).
- `trading.py:532`: every fill stamps `"trade_date": w.current_date.isoformat()`; `trading.py:525` settlement date from
  `w.current_date`.
- `current_date` is advanced by `_h_day_started` (`world.py:365-366`) and `_h_market_close` (`world.py:600-609`) on every
  processed day; a career save processes days by itself (`world.py:1099-1115`, `service.py:271-272`).
- `_h_day_closed` also wipes `pf.day_trade_ids`, `pf.day_capital_flows`, `pos.day_fills` (`world.py:622-627`).
- In a career save a LIVE trade made during Monday's session is stamped with the last PROCESSED date (Friday): the live
  path builds `Bar(w.current_date.isoformat(), live, ...)` and calls `w.trading._fill(...)` (`engines/live.py:445, 451`,
  VERIFIED), because the day is processed after the close (`clock.py:51-58` target date; update default 17:00, `service.py:105`). It shows as
  "today" until Monday's update, then drops off. Over a weekend: Friday's trades show until Monday's update.
- The trades are still in `pf.trades` (`service.py:1101-1104` returns all of them), so this is a display filter - but it is
  exactly "gone after a day / over the weekend". Positions are not date-filtered: `_positions` keeps any position with a
  quantity, pending movement or trade ids (`service.py:974-976`); the Open Positions page fetches `/trades` only in its
  'history' mode (`app.js:1446-1451`). These three `=== today` filters (`app.js:1571, 1572, 1574`) are the only
  sim-date equality filters on trades/orders in app.js (VERIFIED by grep). The directive removes the Live Trades page; the replacement blotter must
  list all trades (or filter by a real wall-clock date, not `current_date`).
- Repro test (hand-worked): SANDBOX world (`make_world`, start 2026-01-05 Mon), BUY 100 AAPL MARKET, `advance(1)` -> one
  trade with trade_date 2026-01-06, `GET /trades` length 1; `advance(1)` (current_date 2026-01-07) -> `/trades` still
  length 1 (assert), and a "today" filter on 2026-01-07 returns 0 (documents the bug). After the repair: the blotter for
  the book returns 1 trade regardless of the date.

**S2 (VERIFIED code, INFERRED trigger). Two different save files / two origins: the user may simply be looking at another
save after a restart.**
- Two DB paths and ports (2.0 above). `app.js:5,195` remember the world in `localStorage`, which is per origin (port).
  If the remembered id is not in `/worlds`, the app silently opens `state.worlds[0]` (`app.js:190`) = the most recently
  CREATED world (`store.py:72-73` `ORDER BY created_utc DESC`) and the first book (`app.js:199`). No message is shown.
- `app.py:47` notes a past problem of "two servers racing for one port at every login" (Startup entry).
- After a reboot/weekend, if the server came up the other way, or from another current directory (a relative
  `data/finsim.db` creates a new empty DB, `store.py:20-21`), the trades "disappear".
- Check (read-only, for the owner): list worlds and event counts in BOTH `%USERPROFILE%\.finsim\finsim.db` and
  `finsim\data\finsim.db`; and `server.log` for which `--db` was used.
- Repro test: two `EventStore` files; create a world in A; open B -> `list_worlds()` empty; front-end logic falls back to
  `worlds[0]`. Desired after repair: one fixed DB path, and an explicit "save not found" message instead of a silent fallback.

**S3 (VERIFIED code, INFERRED trigger). Resilient replay silently drops a trade on the next load.**
- `strict_replay` is off by default (`service.py:74`); `replay(strict=False)` skips the failing event (`world.py:170-176`).
- `_h_trade_executed` needs the security to exist at that point of replay: `sec = w.securities[p["security_id"]]`
  (`trading.py:630`). Option contracts are listed only at MARKET_CLOSE from the replayed price level
  (`world.py:610`, `options.py:228-241`); orphans are relisted only AFTER the replay (`world.py:715-716, 721-730`), too late
  for the trade handler. Any engine/migration change that alters replayed prices (e.g. `_fix_quote_scale`,
  `world.py:318-336`, which depends on `market.history` at replay time) can make a contract unknown during replay.
- Then the trade, its lots, settlement and ledger entry vanish on the next server start ("after a couple of days"); the
  order can still show FILLED. Only `replay_errors` / `integrity` in `/api/health` and `world_info` reveal it
  (`service.py:279-284, 307-308`).
- Repro test: `make_world` with a store file; buy 1 option contract; `advance(1)`; then simulate the drift with
  `store.replace_event(...)` (`store.py:76-79`) on the MARKET_CLOSE that listed it (move the underlying close so the
  chain's strikes change); `World.load(store, id, strict=False)` -> expect `replay_errors` non-empty and the trade
  missing; `strict=True` -> `ReplayError`. After the repair: load must refuse or report loudly, never open short.

**S4 (VERIFIED code, INFERRED trigger). Events that never reach disk.**
- Persistence happens only at `flush()`; if any code path emits and the process stops before the next flush, those
  events (fills included) are lost. Flush call sites: `world.py:310,508,524,582,704,763,772,795,804,827,841,939,1003,
  1019,1076,1095,1113,1145`; `service.py:391,1308`; `engines/ccy_funding.py:99,123`; `engines/playbook.py:464`.
- If one insert fails (e.g. `UNIQUE (world_id, seq)` because two server processes append to the same save, both
  computing `seq = len(events)+1`), `_pending` is never cleared (`world.py:152-155`), every later flush fails again, and
  NOTHING after that point is saved, while the in-memory world looks normal until the restart.
- Also in-memory-only mutations (lost on reload, not trades but order state): `o.last_tick = tick_iso` in the live sweep
  (`engines/live.py:354`), `_note()` writes `order.reason` and `order.history` without an event (`trading.py:543-545`).
- Repro test: one sqlite file; `w1 = World.load(store, id)`, `w2 = World.load(store, id)`; `w1.place_order(...)` (flushes
  seq N); `w2.place_order(...)` -> IntegrityError on flush; then `w2.contribute_capital(...)` -> still raises; reload ->
  w2's order absent. After the repair: atomic save with a version check that fails loudly.

**S4b (VERIFIED code path, INFERRED trigger). A daily process that raises half-way leaves a partial day in memory, unsaved.**
- `World.advance` flushes only after the whole loop (`world.py:1085-1095`); `World.catch_up` flushes only `if closed:` after
  the loop (`world.py:1106-1113`). If any engine raises inside `run_daily_process` (`simulation.py:34-139`) after step D
  (`work_orders`, `:96`, where overnight instructions fill), the DAY_STARTED / MARKET_CLOSE / TRADE_EXECUTED events are
  applied in memory (the UI shows the fills, `current_date` has moved) but are not on disk.
- The scheduler swallows the exception and continues (`api/server.py:415-419`); the next catch-up sees
  `current_date == target` and does nothing, so nothing flushes until some unrelated command does - then a half-processed
  day (no DAY_CLOSED) is written; or, if the server stops first (shutdown over a weekend), the fills are lost.
- Within one process, requests and scheduler ticks are serialised by one lock (`api/server.py:29, 354-355, 415-416,
  426`), so this is not a thread race; two processes on one DB are not protected (S4).
- Also VERIFIED: the scheduler loads EVERY save in the store and keeps it in memory (`api/server.py:412-416` ->
  `service.py:251-256, 261-269`; no eviction found) - relevant to the owner's memory limits.
- Repro test: monkeypatch one engine (e.g. `w.risk.process_day`) to raise; place a MARKET order; `advance(1)` raises; assert
  that `len(store.load_events(id)) < len(w.events)` (unflushed partial day). After the repair: a day/batch is all-or-nothing
  (atomic write, or roll back the in-memory state).

**S5 (VERIFIED, by design but tied to the day advance). DAY instructions expire at the end of the session they execute
in** (`trading.py:552-561`, called at `simulation.py:133`): an unfilled DAY order entered Friday evening is EXPIRED by
Monday's update. Orders, not trades, but it reads as "my order disappeared over the weekend". GTC orders persist.

**Not a cause (VERIFIED):** the universe is rebuilt deterministically from the save's start date and seed
(`world.py:269`), so equities do not change between loads; weekend skipping in catch-up is by business day
(`clock.py:51-58`, `world.py:1099-1115`) and is tested (`test_daily.py:182-205`).

### 2b. "About 10,000,000 appears in Treasury, unexplained" - suspects ranked

What "Treasury" is (VERIFIED): the book with `portfolio_type == "TREASURY"` (`world.py:395-398`); the Treasury view is
its cash balances plus any legacy pool (`service.py:740-753`), log = `w.treasury_log[-40:]` (`service.py:755-759`). Not to
be confused with the TREASURY_MANAGER job engine (`engines/treasury.py`, applies when `pf.job == "TREASURY_MANAGER"`,
`treasury.py:52-53`).

**T1 (most likely; VERIFIED). Money enters the Treasury book as CAPITAL_CONTRIBUTED, which the Treasury log never records,
and two code paths default to exactly 10,000,000.**
- `treasury_log` is appended only for legacy FUNDED and TREASURY_ALLOCATED (`world.py:404, 446-447`). `_h_capital`
  (`world.py:585-598`) does not append to it. So `fund_treasury` (`world.py:455-461` -> `contribute_capital`), the
  opening capital, mission bonuses and promotions into the Treasury book all raise its cash with NO line in the Treasury
  log: they look unexplained on the Treasury page. Front end (VERIFIED): the ALL BOOKS page tile "Treasury" shows
  `totals.treasury` = `_treasury_state.total_usd` (`app.js:1877`, `service.py:861`), "holds X in USD terms" shows
  `total_base` (`app.js:1880`), and the "Treasury log" table lists only `treasury_log` rows (`app.js:1895`). The new-book
  form ticks "draw it from the Treasury" only when the Treasury can spare cash; unticked = fresh money (`app.js:274`).
- Default 10,000,000: `World.create_portfolio(..., capital: Decimal = D(10_000_000), ...)` (`world.py:732`, contributed at
  `world.py:746-747`) and the HTTP route `body.get("capital", 10_000_000)` (`api/server.py:131`). The app's own new-book
  form always sends `capital` (number or null, `app.js:277`), so the 10M default fires for any other client/script that
  omits the key (and `demo.py:54` makes a 10M book; the test helper uses 10M, `helpers.py:13`).
- Repro test A (log gap): `Service(EventStore(":memory:"))`; `create_world("firm", 42, start_date="2026-01-05",
  capital=50_000_000, treasury=True, clock_mode="SANDBOX")`; `treasury_command(wid, "fund", {"amount": 1_000_000})`.
  Hand-worked: Treasury USD cash = 50,000,000 + 1,000,000 = 51,000,000. Current: `treasury(wid)["log"]` has no entry for
  either amount. Desired: log lines OPENING 50,000,000 and FUNDED 1,000,000 that sum to the balance.
- Repro test B (default): `server` route POST `/worlds/{wid}/portfolios` with body `{"name": "X", "portfolio_type":
  "TREASURY"}` on a world without a Treasury -> current: book cash 10,000,000 (contributed, unexplained). Desired: 0 or
  an error asking for an amount.

**T2 (VERIFIED code; trigger depends on the save's job). Career mechanics add capital to the Treasury book.**
- The Treasury book inherits the world's job (`service.py:150-152` passes `job`), and the new-world form pre-selects
  PORTFOLIO_MANAGER (`app.js:241`). So `careers.start_missions` runs for the Treasury book (`world.py:758`). A completed
  mission pays `job.capital x 5%` as CAPITAL_CONTRIBUTED (`careers.py:387-393`): PORTFOLIO_MANAGER 100,000,000 x 0.05 =
  **5,000,000 per mission** (`careers.py:42-46`). Mission PM_VAR is `_streak(w, pf, "VAR", 40)` = 40 consecutive NAV
  snapshots with no VaR breach (`careers.py:115-116, 174-184`: counts snapshots back from the latest until a date with a
  "VAR" breach; done when clean >= 40). VAR breaches are emitted only when a VaR/shortfall limit is SOFT or HARD
  (`engines/risk.py:442-446`), which a cash-only Treasury book should never hit (INFERRED: VaR of cash ~0), so after 40
  business days the Treasury book receives +5,000,000 with reason "mission bonus: Respect the risk budget". The bonus is
  not in the Treasury log (T1). A second PM book in the same save earns its own 5,000,000 the same way (in that book).
- Mission bonus size by job = 5% of the job's standard capital (`careers.py:40-90, 389`), to match against the owner's
  observed amount: PORTFOLIO_MANAGER 5.0M; GLOBAL_MACRO 12.5M; COMMODITY_TRADER 5.0M; FIXED_INCOME_PM 12.5M; HEDGE_FUND
  25M; PRIVATE_EQUITY 100M; INVESTMENT_BANKER 25M; BANK_TRADER 2.5M; DERIVATIVES_TRADER 2.5M; SEC_LENDING 5.0M;
  REPO_TRADER 5.0M; TREASURY_MANAGER 6.0M; RISK_MANAGER 0; SANDBOX has no missions.
- Side note (VERIFIED): `_streak_metric` keeps mission counters in `pf.mission_state` mutated inside a check function
  (`careers.py:187-195`), not via an event, so those counters reset on every reload.
- Promotions on an EXCEEDS quarterly/annual review add `job.capital x capital_step` (`careers.py:536-542`; skipped for
  SANDBOX, `careers.py:423`).
- If the Treasury book's job is TREASURY_MANAGER, the corporate-treasury engine credits monthly operating receipts EUR
  9,000,000 x U(0.85,1.15), GBP 4,000,000, JPY 900,000,000 and a base net of 6M - 14M on the rolled 20th
  (`engines/treasury.py:35-38, 76-82, 139-158`): EUR ~9M is about USD 10M.
- Repro test: `create_world(job="PORTFOLIO_MANAGER", treasury=True, capital=100_000_000, clock_mode="SANDBOX")`;
  `advance(45)` with no trades; count CAPITAL_CONTRIBUTED events on the Treasury book whose reason starts "mission bonus".
  Desired: 0 (a Treasury has no missions). Hand-worked if one fires: Treasury cash = 100,000,000 + 5,000,000 + interest.

**T3 (VERIFIED). Deleting a book sends ALL its cash to the Treasury, including fresh capital that never came from it.**
- `delete_portfolio` emits TO_TREASURY for every positive balance (`world.py:573-580`), whether the book was funded from
  the Treasury (`service.py:335-342`) or with fresh capital (`service.py:343-344`, the default when "From Treasury" is
  unchecked).
- Repro test: Treasury 50,000,000; new book "B" capital 10,000,000 from_treasury=False (fresh); delete B -> Treasury cash
  60,000,000, log "book closed +10,000,000" although nothing was drawn. Hand-worked desired: drawn 0, returned 0 (or a
  logged contribution), Treasury stays 50,000,000 unless the owner chooses to transfer.

**T4 (VERIFIED code, INFERRED trigger). Legacy pool added on top of the Treasury book.** `_treasury_state` adds
`w.treasury_cash` (fed by legacy TREASURY_FUNDED events, `world.py:400-404`) to the book's balances
(`service.py:742-744`); `_move_capital` moves money to/from that pool whenever an allocation event has no resolvable
`treasury_id` (`world.py:423-424, 433-434, 440`). A save from the first treasury version that later got a Treasury book
shows book + pool.

**T5 (VERIFIED, lower likelihood for exactly 10M).** `_capital()` treats 0 and "0" as blank -> the job's standard capital
(`service.py:52-64`; SANDBOX = 1,000,000,000, `careers.py:40`). In `create_world` an explicit 0 becomes the standard
(`service.py:148`); `create_portfolio` handles only numeric 0 (`service.py:338, 343`).

**Checked, not a source of +10M (VERIFIED):** repo/reverse repo open/close move cash symmetrically and post matching GL
lines (`engines/repo.py:248-276, 361-389`); the settlement and overdraft backstops move money OUT of the Treasury
(TO_BOOK; `settlement.py:89-96`, `world.py:474-493`). Note: the backstop is a side effect inside a "check" function
(`settlement.py:94`). The Treasury book itself is never backstopped (`world.py:480, 489`); its own shortfalls go to the
prime broker: at settlement (`settlement.py:96`) and in the daily sweep "negative base cash becomes a loan"
(`engines/prime.py:136-139`, VERIFIED), which raises the book's CASH with a matching loan - visible as Treasury cash on a
page that shows cash, not NAV (`service.py:742`). Amount INFERRED.

### 2c. Duplicate computations noticed in my area (VERIFIED locations)

- Treasury figures computed three ways: `_treasury_state` (`service.py:740-753`), `overall()` totals
  (`service.py:859-864`, `nav_with_treasury = nav + legacy`), and `treasury()` book cash (`service.py:757-758`).
- Two integrity checks with different coverage: `World.check_integrity` (`world.py:180-203`) and
  `helpers.assert_ledger_invariants` (`helpers.py:20-101`).
- Balance-sheet rows rebuilt from `compute_summary` in the service (`service.py:783-816`). The "Collateral received"
  liability line (`service.py:812`, from `pf.collateral_received`, set at reverse-repo open `repo.py:272`) is NOT in the
  NAV formula (`engines/pnl.py:172-173`) and has no GL posting (`repo.py:270-276` posts only 1500 / 1010). VERIFIED: while
  a reverse repo is open the displayed balance sheet lists a liability that the "Net asset value" row
  (`service.py:815` = `s["nav"]`) ignores, so assets minus liabilities != NAV on screen.
- `compute_summary` is a read that writes state: it sets `p.notional` for futures (`engines/pnl.py:166-167`). NAV mixes
  GL balances with sub-ledger values (private credit / equity from objects, `pnl.py:156-157`), and the GL NAV
  `led.nav()` is compared to it in `check_integrity` (`world.py:192-194`).
- Day P&L "live delta" in the dashboard (`service.py:895-896`) vs the snapshot P&L in `nav_history`.

### 2d. Everything tied to the simulated day advance (must be replaced for "current-day live prices")

- `engines/simulation.py` (whole file): `run_daily_process(d)` A-K cycle (`:34-139`) - market generation
  `w.market.generate_day(d)` (`:41`), MARKET_CLOSE with all bars (`:43-49`), news, AI desks, `work_orders` (`:96`),
  settlement (`:99-100`), Treasury overdraft cover x2 (`:101, :122`), corporate actions, futures expiry/VM (`:108-109`),
  every engine's `process_day(closing, prev)` (`:110-127`), marks (`:120, :128`), careers, snapshot (`:131`), DAY expiry
  (`:133`), briefing; `advance_one_day()` (`:141-143`).
- `world.py`: `create` runs a day (`:701-704`); `advance` (`:1079-1097`); `catch_up` (`:1099-1115`); `real_market_ready`
  (`:342-362`); `_h_day_started` / `_h_market_close` set `current_date` and `day_count` (`:365-366, 600-610`), replay of
  stored bars (`:603-607`), option listing per day (`:610`); `_h_day_closed` (`:622-627`); scenario keyed on `day_count`
  (`:1022-1027`); `next_update_at`, `trading_window`, `set_clock` (`:1117-1148`); `instruction_session` (`:821-823`).
- `clock.py`: `target_sim_date`, `next_update`, `trading_window`, `instruction_session` (`:51-68, 128-167`).
- `api/service.py`: catch-up on every access (`:258-273`), `catch_up_all/one` (`:242-256`), `advance` (`:404-407`),
  start-date choice by clock (`:125-142`), `world_info` (`day_index`, `next_update`, `real_market`, `:290-316`).
- `engines/trading.py`: fills against session bars (`_bar`, `_fill`, `:232, 466-541`), `trade_date` and settlement from
  `current_date` (`:525, 532`), DAY expiry (`:552-561`), `work_orders` (`:327-347`).
- `engines/live.py`: live fills and the 15-minute instruction ticks (`:302-367`) - the part that already uses real quotes.
- Accruals that use `(today - prev).days` per processed day, e.g. repo (`repo.py:167-199`), corporate Treasury debt
  service and flows (`treasury.py:66-99`), careers missions/reviews (`careers.py:375-432`).
- `app.js`: Advance button (`:226`), Live Trades "today" filter (`:1567-1574`), sim date label (`:199`).
- Tests: `helpers.make_world` runs a simulated day (`helpers.py:13-17` -> `world.py:702-704`); most FinSim tests call
  `advance`.

### 2e. Proposed hand-worked tests for the repaired design (live prices, paper cash, no day advance)

Each with a fake quote provider (fixed prices) so the expected numbers are exact.
1. Ledger integrity (every book and the save): Treasury opens with 1,000,000.00 USD; book B draws 400,000.00; B buys
   1,000 XYZ at 100.00, commission 0 -> B cash 300,000.00, B position MV 100,000.00, B NAV 400,000.00; Treasury cash
   600,000.00; save NAV 1,000,000.00; Treasury log rows: OPENING +1,000,000, TO_BOOK -400,000 -> sums to 600,000.
   Price moves to 110.00 -> B NAV 410,000.00, save NAV 1,010,000.00; trial balances balance; contributed capital
   total 1,000,000.00.
2. Persistence: after (1) save -> load -> identical trades (1), positions (XYZ 1,000), cash (300,000.00 / 600,000.00),
   NAVs and Treasury log; then a simulated crash between two writes leaves either both or neither on disk.
3. No date filter: the blotter lists the trade on the next calendar day and after a weekend (wall clock moved +3 days).
4. Treasury explained: every change in Treasury cash has a log row; sum(log) == balance (to the cent); no default
   capital anywhere (creating a book without an amount is an error or 0).
5. Short sale needs a borrow first: SELL 100 XYZ with no locate/borrow -> rejected; after borrowing 100 -> accepted;
   cash +10,000.00 at 100.00, short MV -10,000.00, NAV unchanged at the trade.

Read partially (only the cited ranges): `engines/pnl.py` (136-177), `engines/prime.py` (60-153), `careers.py` (24-98,
112-118, 174-195, 330-337, 370-548), `engines/repo.py` (167-201, 248-389), `engines/settlement.py` (70-120),
`engines/live.py` (298-367; 445, 451), `engines/risk.py` (430-447), `engines/trading.py` (327-371, 505-639), `api/service.py` (44-168, 236-415, 740-899,
970-992), `world.py` (128-262, 263-640, 660-842, 1076-1125), `migrations.py` (outline only), `app.js` (lines cited).

STATUS: PARTIAL - not yet read: `engines/ledger.py`; `engines/market.py` generate_day / bootstrap; `engines/risk.py`
`report()` / limit statuses (only `process_day` 430-447 read; needed to confirm T2's 40-day outcome for a cash book); `engines/options.py` beyond
the listing code; `engines/accruals.py` (cash interest); `domain/models.py`; the bodies of the FinSim test files beyond
the outlines cited; `static/glossary.js`, `coverage.js`, `index.html`, `style.css`.
