# FinSim map part: CORE (world, store, clock, calendar, simulation, API)

Mapper: read-only pass, 2026-10-20 (started 12:30 ET). All paths are relative to
`C:\Users\logan\intelligence-terminal\finsim\finsim\` unless stated. "VERIFIED" = I read the
code at the cited path:line. "INFERRED" = reasoning from what I read, not checked end to end.

Line counts (VERIFIED, `wc -l`): world.py 1179, store.py 97, migrations.py 107, clock.py 167,
calendar.py 167, engines/simulation.py 143, app.py 662, __main__.py 55, api/server.py 478,
api/service.py 2042, domain/models.py 639, domain/events.py 242, domain/otc_models.py 95,
money.py 49, version.py 7, demo.py 160, log.py 34, tzfallback.py 124.

Besides the code, I read (read-only) `C:\Users\logan\.finsim\server.log` (grep and excerpts) and listed
file sizes and dates in `C:\Users\logan\.finsim\` and `finsim\data\`. I did not open any database.

### Top findings (details in section 4)
1. F1 + F8, the "trades disappear" bug: a write that fails leaves the trade in memory but not in SQLite; the
   cached world keeps showing it until the server restarts. server.log shows exactly this:
   `catch_up -> flush -> append_events -> sqlite3.OperationalError: database is locked`
   (server.log:5025-5045, 2026-10-02 17:00), 367 "database is locked" lines and 88 IntegrityError lines, with
   two processes working the same save in the same second. On Windows a second FinSim server starts easily
   (the launcher's 1-second health probe) and keeps running, because Python's HTTPServer binds with SO_REUSEADDR.
2. F2, the "10,000,000 in Treasury" bug: `POST /portfolios` defaults capital to 10,000,000 when the key is
   missing (server.py:131) and `World.create_portfolio` defaults to 10M (world.py:732). The legacy
   `treasury_cash` pool is also added on top of the Treasury book in every Treasury figure (service.py:742-744,
   860-864). Book deletion, the automatic overdraft backstop and the repo ticket's 10M default face are other
   sources.
3. Day advance: `World.advance` (SANDBOX) and `World.catch_up` (every API call and every 60 s) both run
   `SimulationEngine.run_daily_process`, the only place where orders execute (unless LIVE), trades settle,
   interest accrues, corporate actions pay, marks and NAV snapshots happen and DAY orders expire (section 3.4,
   section 5).
4. The LIVE path already exists (`execution="LIVE"`, engines/live.py, 60 s sweep). It is the natural base for
   "today's live prices, paper cash" once the day process is replaced by a real-date lifecycle job (section 5.3).

---------------------------------------------------------------------------------------------

## 1. Files and what each does

| File | Role | Status |
|---|---|---|
| `world.py` | The `World`: an event-sourced aggregate. Commands validate and `emit` events; handlers mutate in-memory state; `derive` emits caused events (suppressed during replay). Holds every engine instance, the treasury helpers, create/load/replay/integrity, `advance`/`catch_up` (the day advance). | VERIFIED world.py:1-10, 42-116 |
| `store.py` | `EventStore`: SQLite (stdlib) append-only event log. Tables `worlds(id,name,created_utc,save_version)` and `events(world_id,seq,event_id,type,sim_date,cause_id,portfolio_id,body)`, PK `(world_id,seq)`. WAL mode. `snapshot()` = online backup copy into `backups/`, keep 10. | VERIFIED store.py:17-97 |
| `migrations.py` | Pure functions over the event list on load: v1->v2 (stamp scenario/macro defaults), v2->v3 (rename fictional tickers/institutions to real ones by regex over JSON payloads). | VERIFIED migrations.py:19-107 |
| `version.py` | `ENGINE_VERSION = "0.11.0"`, `SAVE_VERSION = 3`. | VERIFIED version.py:6-7 |
| `clock.py` | `ClockConfig(mode SANDBOX/REAL_TIME, timezone, update_time, lock_session)`; target simulated date; next update; NY session open/close; 15-minute quote ticks 09:45-16:15 NY; trading window; which print (OPEN/CLOSE) an instruction hits. | VERIFIED clock.py:29-167 |
| `calendar.py` | `BusinessCalendar` (weekends + rule-generated NYSE full-day holidays incl. Good Friday, Juneteenth; NO early closes), business-day arithmetic, month-end; `SettlementConfig` (T+n per market); `settlement_date()`. | VERIFIED calendar.py:53-167 |
| `engines/simulation.py` | `SimulationEngine.run_daily_process(d)`: THE once-per-day cycle (market generation, news, order execution, settlement, corporate actions, futures, financing desks, accruals, marks, snapshot, risk, DAY-order expiry, careers, briefing). `advance_one_day()`. | VERIFIED simulation.py:30-143 |
| `api/server.py` | stdlib `ThreadingHTTPServer`; `Router.dispatch` (all routes, one global lock); `make_handler` (static files + JSON API + access key); `Scheduler` (60 s thread: catch-up of every stored save + live-order sweep); `serve()` (backs up DB, builds Service, starts scheduler). | VERIFIED server.py:26-478 |
| `api/service.py` | `Service`: framework-agnostic facade. World cache `self.worlds`, load-on-demand with catch-up on EVERY access, create_world, all read models (dashboard, overall, balance sheet rows, treasury state, positions, cash, ledger, ...) and command wrappers. | VERIFIED service.py:1-2042 (all read) |
| `__main__.py` | CLI: `serve` (--db default `data/finsim.db` relative to cwd, --port 8000), `demo`, `install`, `open`, `status`, `uninstall`, `phone`. | VERIFIED __main__.py:9-51 |
| `app.py` | Desktop-app wrapper: FINSIM_HOME (`~/.finsim`), `db_path()` (`~/.finsim/finsim.db`), port 8765, legacy-DB adoption, `start_background` (spawns a server when the 1 s health probe fails), launcher files (Windows: `finsim-server.vbs` via a logon scheduled task, Startup-folder fallback; `finsim-open.vbs`; shortcuts), install/open/status/uninstall/phone; `/api/shutdown`-based stop (`_stop_server`, app.py:656-662); access key file `~/.finsim/access-key` (value never printed here). | VERIFIED app.py:1-662 (all read) |
| `domain/events.py` | `Event` dataclass + JSON; `E` event-type constants. | VERIFIED events.py:1-242 |
| `domain/models.py` | Dataclasses for every in-memory projection (Security, Bar, Position, CashAccount, Order, Trade, SettlementInstruction, NAVSnapshot, Portfolio, ...). | VERIFIED models.py:1-639 (all read) |
| `domain/otc_models.py` | `OE` constants (duplicates of `E` OTC types), `PRODUCTS` (28 OTC products), `BUCKET_BY_PRODUCT`, `OTCTrade`, `RFQ`, `CSA` dataclasses. | VERIFIED otc_models.py:1-95 |
| `money.py`, `log.py`, `version.py` | Decimal helpers; logging; versions. | VERIFIED all lines |
| `tzfallback.py` | `get_zone(name)`: `zoneinfo.ZoneInfo` or built-in rule zones (US DST 2nd Sun Mar - 1st Sun Nov from 2007; EU last Sun Mar - last Sun Oct 01:00 UTC; fixed offsets for Tokyo/HK/Shanghai/Singapore/Kolkata). | VERIFIED tzfallback.py:1-124 |
| `demo.py` | `run_demo(seed=42, start="2026-01-05")`: in-memory store (`EventStore(":memory:")`, demo.py:51), a $10M sandbox book (54), evening instructions, then `w.advance(1)` repeatedly (83, 108, 120, 129, 135) to show fills, settlement, dividends, realized P&L, and a replay check (`World.load`, demo.py:153). It is built entirely on the day advance and must be rewritten or removed. | VERIFIED demo.py:1-160 |

---------------------------------------------------------------------------------------------

## 2. Key functions and data structures

### 2.1 `World` state (VERIFIED world.py:42-116)

`World(world_id: str, store=None)` fields:
- Event log: `events: List[Event]`, `events_by_id`, `children` (cause tree), `replaying: bool`, `handlers: Dict[str, List[fn]]`, `_pending: List[Event]` (not yet written to the store).
- Config (set by WORLD_CREATED): `name`, `seed`, `start_date`, `current_date`, `base_currency`, `settlement_config`, `calendar = BusinessCalendar()` (always US; never persisted), `clock = ClockConfig()`, `scenario`, `save_version`, `market_source` (attribute only created in `_h_world_created`, read everywhere with `getattr(..., "SIMULATED")`).
- Markets: `securities: Dict[str, Security]` (built by `build_universe(start_date, seed)`), `market: MarketEngine`, `news: List[NewsItem]`, `corporate_actions`, `day_count`, `real_news`.
- Books: `portfolios: Dict[str, Portfolio]`, `ledgers: Dict[str, Ledger]`.
- Treasury: `treasury_cash: Dict[str, Decimal]` (LEGACY pool outside any book), `treasury_log: List[Dict]` (in-memory projection, rebuilt on replay).
- Ops: `run_log` (per processed day, NOT saved, world.py:112), `migration`, `replay_errors`, `integrity`, `last_processed_utc` (declared world.py:71; I found no writer in my files - INFERRED unused).
- ~30 engines constructed in `__init__` (world.py:73-115): trading, live, settlement, corporate, accruals, pnl, futures, collateral, seclending, repo, prime, fx, options, otc, risk, cevents, investors, clients, treasury, institutions, lenddesk, cdesk, pcredit, pequity, ibank, mbsdesk, funding, playbook, careers, briefing, simulation. Each registers handlers in `_register_core` (world.py:206-254).

### 2.2 Event plumbing (VERIFIED)
- `emit(etype, payload, cause_id=None, portfolio_id=None, sim_date=None) -> Event` (world.py:128-136): seq = `len(self.events)+1`; id `EV-{seq:06d}`; `sim_date` defaults to `current_date` (the last PROCESSED day, not the real date); payload cleaned to JSON-safe (`_clean`, world.py:1167-1179: Decimal->str, date->iso); `_append` then `_apply` (handlers run synchronously).
- `derive(...)` (world.py:138-142): emit with cause; returns None while replaying.
- `_append` (world.py:144-150): adds to `_pending` only when `store is not None and not replaying`.
- `flush()` (world.py:152-155): `store.append_events(self.id, self._pending)` then clears. NOTHING is persisted until `flush()` is called.
- `replay(events, strict=True)` (world.py:161-178): strict raises `ReplayError` on first bad event; non-strict records `replay_errors` and CONTINUES (the bad event stays in `self.events` but its state change is partial or missing).
- `check_integrity()` (world.py:180-203): per book: trial balance balances; `pnl.compute_summary(pf)["nav"] == ledger.nav()`; no negative settled custody unless option/future/borrowed. Result in `self.integrity`; reported by `/api/health` and `world_info`. It does not stop loading.

### 2.3 Domain classes used here (details in 2.7 and in 2.9, which sits after 3.6 below)
`Event(seq, id, type, sim_date, payload, cause_id, portfolio_id)` with `to_json`/`from_json` (store.py:84,89; migrations.py:16). `Portfolio`, `Security`, `Bar`, `NewsItem`, `YieldCurve`, `CashMovement`, `CustodyMovement` from `domain/models.py`.

### 2.4 Store (VERIFIED store.py)
- `EventStore(path)` (store.py:18-32): `sqlite3.connect(path, check_same_thread=False)`, `PRAGMA journal_mode=WAL`, creates tables; schema-upgrade adds `worlds.save_version`.
- `append_events(world_id, events)` (store.py:81-85): one `executemany INSERT` + `commit()` - atomic per flush (one SQLite transaction). INSERT (not upsert): a duplicate `(world_id, seq)` raises `sqlite3.IntegrityError`.
- `load_events(world_id)` (store.py:87-89): `SELECT body ... ORDER BY seq` -> `Event.from_json`.
- `create_world`, `set_save_version`, `list_worlds` (with an event COUNT subquery per world, store.py:71-74), `replace_event` (test/maintenance), `delete_world` (deletes events + row), `close`.
- `snapshot(src_path, dest_dir, keep=10)` (store.py:34-59): SQLite online backup to `dest_dir/finsim-YYYYmmdd-HHMMSS.db`; prunes to newest 10. Called ONLY by `serve()` at startup (server.py:462). Not called on autosave or before migrations at load time.

### 2.5 Clock (VERIFIED clock.py)
- `ClockConfig(mode="SANDBOX", timezone="America/New_York", update_time="17:00", lock_session=False)` (clock.py:29-44). NOTE: `World.create` callers pass `update_time` "17:00" for REAL_TIME and "09:00" for SANDBOX (service.py:105).
- `target_sim_date(cfg, cal, at)` (clock.py:51-58): local date of `at` in cfg.tz; minus one day if before update time; `cal.roll_back` to a business day. => On Sat/Sun/holiday the target is the previous business day.
- `next_update(cfg, cal, at)` (clock.py:61-68).
- `MARKET_TZ` NY, `MARKET_OPEN 09:30`, `SESSION_FINAL 16:00` (clock.py:71-73); `session_closed(d, at)` (clock.py:80-87).
- `quote_ticks(d)` 27 ticks 09:45..16:15 every 15 min (clock.py:90-103); `next_quote_tick`, `latest_quote_tick` (clock.py:106-125).
- `trading_window(cfg, cal, at)` (clock.py:128-155); `instruction_session(cfg, cal, current, at)` -> "OPEN"/"CLOSE" (clock.py:158-167; SANDBOX always "OPEN").

### 2.6 Calendar (VERIFIED calendar.py)
- `us_equity_holidays(year)` (calendar.py:53-70): New Year (observed; Saturday New Year not moved back), MLK, Presidents, Good Friday, Memorial, Juneteenth, July 4, Labor, Thanksgiving, Christmas. No ad-hoc closures (e.g. national days of mourning) and NO half-days.
- `BusinessCalendar`: `is_business_day` (weekday<5 and not holiday), `next_business_day`, `prev_business_day`, `add_business_days`, `business_days_between` ((start,end]), `roll_back`, `month_end`, `is_month_end`, `roll` (following). Holiday sets cached per year.
- `SettlementConfig.cycles` (calendar.py:141-154): US_EQUITY 1, US_TREASURY 1, corp/MBS 1, EU_GOVT 2, ... FX_SPOT 2, FUTURES 0, US_OPTIONS 1, PHYSICAL 2, CRYPTO 0, INDEX 0. `cycle_for` falls back to defaults for old saves. `settlement_date(cal, cfg, market, trade_date)`.
- ONE calendar (US) is used for every market including EU/UK/JP settlement (INFERRED from `World.calendar = BusinessCalendar()` world.py:58 and `settlement_date` signature; `SettlementConfig.calendars` = {"default": "US"} is never consulted in what I read).

### 2.7 `Event` and event types (VERIFIED domain/events.py)
- `@dataclass Event(seq: int, id: str, type: str, sim_date: str, payload: Dict = {}, cause_id: Optional[str] = None, portfolio_id: Optional[str] = None)` (events.py:215-234). `to_json` = compact JSON with Decimal->str, date->iso (events.py:225-242); `from_json(s)` = `Event(**json.loads(s))` (no validation).
- `class E` (events.py:18-212): ~190 string constants. Groups: world/clock (WORLD_CREATED, CLOCK_CHANGED, MARKET_SOURCE_CHANGED, REAL_HISTORY_LOADED, REAL_MACRO_LOADED, LIVE_SWEEP); day cycle (DAY_STARTED, MARKET_CLOSE, DAY_CLOSING, DAY_CLOSED, NAV_SNAPSHOT, DAILY_BRIEFING, REGIME_CHANGED, REGIME_FORCED, RATES_SHOCK_FORCED, SCENARIO_EVENT); capital/treasury (CAPITAL_CONTRIBUTED, CAPITAL_WITHDRAWN, TREASURY_FUNDED [legacy pool], TREASURY_ALLOCATED, TREASURY_SETUP, OPERATING_FLOW, DEBT_SERVICE); orders/trades/settlement (ORDER_*, TRADE_*, SETTLEMENT_*, CASH_MOVEMENT, CUSTODY_MOVEMENT, LEDGER_POSTED, VALUATION_MARKED); income (DIVIDEND_*, COUPON_PAID, BOND_MATURED, MBS_PAID_DOWN, INTEREST_*); futures/margin; seclending/locates/borrows (LOCATE_REQUESTED, SECURITY_LOAN_*, BORROW_FEE_*, DIVIDEND_OBLIGATION*); repo; CCY_LOAN_*; MARGIN_LOAN_*; FX_*; options (OPTION_*, STRATEGY_*, STOCK_SPLIT, CONTRACT_ADJUSTED); OTC (RFQ_*, OTC_*, CSA_*, CREDIT_EVENT, COUNTERPARTY_*); risk; corporate events; macro (ECONOMIC_RELEASE, EARNINGS_REPORTED, RATING_CHANGED, ISSUER_DEFAULTED); private credit PC_*; private equity PE_*; investment banking IB_*; careers (MISSION_*, PERFORMANCE_REVIEW, CAREER_EVENT, CLIENT_RFQ_*, DESK_*); commodity physical.
- ECONOMIC_RELEASE, EARNINGS_REPORTED, RATING_CHANGED, ISSUER_DEFAULTED have no-op core handlers (world.py:249-250).

### 2.8 Service (VERIFIED service.py:67-407 unless noted)
- `Service(store: EventStore, strict_replay: Optional[bool] = None)`: `self.worlds: Dict[str, World]` = in-process cache, NEVER invalidated or reloaded from disk (service.py:73, 258-273); `strict_replay` defaults to env `FINSIM_STRICT_REPLAY == "1"`, i.e. normally NON-strict (service.py:74); `self.now` injectable clock (tests); `scheduler_state`.
- `world(world_id, at=None, catch_up=True) -> World` (service.py:258-273): load from store on first use (`World.load`, non-strict by default), stamp migrated save version, then `w.catch_up(at)` ON EVERY CALL. Every endpoint goes through this, so ANY read can process simulated days (VERIFIED: e.g. `news` service.py:731, `dashboard` 890).
- `create_world(name, seed, start_date=None, capital=None, portfolio_name="Main Portfolio", portfolio_type="PERSONAL", realism="PROFESSIONAL", mode="SANDBOX", initial_regime="NORMAL_GROWTH", benchmark="SPY", job="SANDBOX", clock_mode="SANDBOX", timezone="America/New_York", update_time=None, scenario="NONE", at=None, market_source=None, treasury=False, lock_session=False) -> Dict` (service.py:83-155). Defaults: REAL_TIME => market_source REAL, update_time 17:00; SANDBOX => SIMULATED, update_time 09:00 (service.py:102-105). REAL: fetches `RealFeed.refresh("1y", force=True)` (Yahoo, network) and `RealMacro.refresh()`; start date = min(target_sim_date, latest real close) for REAL_TIME; history window start-420 days..latest; macro start-800 days (service.py:116-138). Then `World.create(...)` and ONE book: either "Treasury" (type TREASURY) holding the whole capital, or a normal book (service.py:148-154). Capital: `_capital(capital, job)` (service.py:52-64): blank/0 -> `JOBS[job].capital` (SANDBOX = $1,000,000,000, careers.py:40), else 0 < c <= $1T.
- `create_portfolio(world_id, name, portfolio_type, capital, realism, mode, benchmark, job="SANDBOX", from_treasury=False)` (service.py:324-345): see Finding F2.
- `place_order(...)` (service.py:352-397): session-lock check; optional FX spot to fund/convert (`settle_ccy`), then `w.place_order(..., execution, at)`; flush in finally.
- `advance(world_id, days=1)` (service.py:404-407) -> `World.advance(days)`.
- `catch_up_all(at)` / `catch_up_one(world_id, at)` (service.py:242-256).
- `live(world_id, ids)` (service.py:170-192): live quotes (Yahoo, up to 15 min delayed) for REAL saves; works the resting live book first.
- `switch_to_real(world_id)` (service.py:194-216); `work_live` / `work_live_loaded` (service.py:218-240).
- `health()` (service.py:275-284), `world_info()` (service.py:290-316), `delete_world()` (service.py:286-288: pops cache, deletes all events).
- Treasury read model `_treasury_state(w)` (service.py:740-753): Treasury BOOK cash + LEGACY `w.treasury_cash` pool summed per currency. `treasury()` (755-759), `treasury_command(action in fund|allocate|return)` (761-773).
- Accounting read models (duplicates, see F6): `balance_sheet_rows(w, pf)` (783-816), `overall()` (818-887), `dashboard()` (889-939), `_positions` (970), `cash` (1149), `_cash_accounts` (1138), `ledger` (1179), `balance_sheet` (1194), `pnl_explain` (1205), `nav_explain` (1222), `tags` (1559), `order_preview` (1622).

---------------------------------------------------------------------------------------------

## 3. Data flow

### 3.1 Process start (VERIFIED)
1. `python -m finsim serve` (__main__.py:12-33): `--db` default = env `FINSIM_DB` else `data/finsim.db` RELATIVE TO THE CURRENT DIRECTORY (__main__.py:13); `--port` default 8000 (__main__.py:15).
   The desktop app instead runs `python -m finsim serve --db <db_path()> --port <port()>` with cwd = package root (app.py:93-95, 213, 218): `db_path()` = env `FINSIM_DB` else `~/.finsim/finsim.db` (app.py:55-60); `DEFAULT_PORT = 8765` (app.py:49), env `FINSIM_PORT`.
   On first app start, `adopt_legacy_db()` copies `<package root>/data/finsim.db` to `~/.finsim/finsim.db` ONCE, only if the destination does not exist (app.py:182-201).
   On this machine BOTH files exist (VERIFIED by Glob): `C:\Users\logan\.finsim\finsim.db` (+ -wal, -shm) and `C:\Users\logan\intelligence-terminal\finsim\data\finsim.db`. See F1.
2. `serve(db_path, host, port, access_key, ...)` (server.py:456-478): refuses non-loopback without a key; `EventStore.snapshot(db, <db dir>/backups)` (newest 10 kept; on this machine `~/.finsim/backups/` holds 10 copies dated 2026-09-18 .. 2026-10-14, VERIFIED by Glob); `Service(EventStore(db))`; `start_scheduler(router)` - the thread starts BEFORE the HTTP port is bound (server.py:467-468); then `ThreadingHTTPServer`.
3. Scheduler thread (server.py:393-453): every 60 s `tick()`: for EVERY stored world (`store.list_worlds()`), under the global router lock, `service.catch_up_one(id, at)` -> loads (replays) the world into the cache if needed, then `World.catch_up(at)`; then `service.work_live_loaded()` (resting live orders of loaded REAL saves). So every save in the DB is loaded into memory within the first minute, whether or not anyone opens it (INFERRED from server.py:412-416 + service.py:251-256).

### 3.2 HTTP request (VERIFIED server.py:297-390)
- Non-`/api/` paths serve files from `finsim/static/` (falls back to index.html).
- `GET /api/health` bypasses the lock. Everything else runs `with router.lock: router.dispatch(...)` - ONE global lock, so requests and the scheduler are serialised. NotFound->404, CommandError->400, KeyError/ValueError/TypeError->400 "bad request", anything else->500. Access key: header `X-FinSim-Key` or `?key=`; loopback trusted; `/api/shutdown` loopback-only.
- Each handler calls `Service.<method>` -> `Service.world(id)` (load + catch-up) -> a World command (emit + flush) or a read model.

### 3.3 Commands -> events -> state -> store (VERIFIED world.py)
`World.<command>` validates (raises `CommandError`, nothing emitted) -> `emit()` appends to `events` + `_pending` and runs handlers synchronously (handlers may `derive` LEDGER_POSTED etc. via `World.post`, world.py:630-636) -> `flush()` writes `_pending` to SQLite in one transaction. State lives only in memory; on load it is rebuilt by replaying every event (`World.load`, world.py:707-719).

### 3.4 THE DAY ADVANCE, step by step

Entry points (VERIFIED):
- SANDBOX: `POST /api/worlds/{wid}/advance {days}` -> `Service.advance` (service.py:404-407) -> `World.advance(days, force=False)` (world.py:1079-1097). Refused for REAL_TIME clocks unless `force` (world.py:1080-1082). Per day: `real_market_ready(next_business_day(current_date))` (gates only REAL-source worlds; world.py:342-363), then `simulation.advance_one_day()`, append to `run_log`; ONE `flush()` after all days (world.py:1095).
- REAL_TIME (career): `World.catch_up(at)` (world.py:1099-1115), called by `Service.world()` on EVERY API access (service.py:271-272) and by the scheduler every 60 s. `target = target_sim_date(clock, calendar, at)` = latest business day whose update time (default 17:00 local) has passed (clock.py:51-58); loop `while current_date < target`: stop if `real_market_ready(...)` returns a reason; else `advance_one_day()`. `flush()` once at the end, only if a day closed.
- World creation: `World.create` sets `current_date = prev_business_day(start_date)` and runs `run_daily_process(start_date)`, so day 0 is a full processed day (world.py:701-704).

Non-trading days (VERIFIED): there is NO weekend/holiday processing. `advance_one_day()` jumps straight to `calendar.next_business_day(current_date)` (simulation.py:141-143); `target_sim_date` rolls back to the last business day (clock.py:58). Engines see the calendar gap only through the `prev` argument to `process_day(closing, prev)` (simulation.py:110-127; e.g. accruals.py:68 compares `today.month != prev_date.month`).

`SimulationEngine.run_daily_process(d)` (simulation.py:34-139), in order:
1. `prev` = current_date if earlier than d, else prev business day (simulation.py:36).
2. emit DAY_STARTED {date, previous} (sim_date = d) -> handler sets `current_date = d` (world.py:365-366). (simulation.py:37)
3. `apply_scenario(start)`: CRISIS script keyed on `day_count` (world.py:1022-1041). (simulation.py:38)
4. Markets: `market.player_on_loan = seclending.player_on_loan()`; `bars, curve, regime_change = market.generate_day(d)` (THE PRICE GENERATOR: simulated, or the real closes for REAL saves - engines/market.py, not my area); `state_dict()`; payload = bars [o,h,l,c,vol,bid,ask], curve, state, commodities, lending, fx, vol, dealers, macro, corporate_events; emit MARKET_CLOSE (simulation.py:40-49). Handler `_h_market_close` (world.py:600-610): during REPLAY re-ingests the stored bars/curve/state (`market.ingest_close`); always sets `current_date`, `day_count = day_index`, then `options.ensure_listings(d)`.
5. REGIME_CHANGED + NEWS (simulation.py:50-54); simulated news `market.day_news` (55-56); REAL saves: real headlines via `real_news.headlines_for(d, top-12 held names, known links)` stored as NEWS_PUBLISHED (57-76).
6. Macro payload -> ECONOMIC_RELEASE, EARNINGS_REPORTED, RATING_CHANGED; simulated issuer defaults -> `issuer_default` (CDS settle, bonds to recovery); dealer defaults -> `otc.default_counterparty` (simulation.py:78-89).
7. `cevents.announce`, `corporate.declare_upcoming` (90-91).
8. Franchise: `clients.process_day`, `institutions.process_day` (AI desks place orders) (93-94).
9. D - EXECUTION: `trading.work_orders(mkt)`: the instructions left for this session fill against the day's bar (96).
10. emit DAY_CLOSING (98). Post-trade: `settlement.process_lifecycle`, `settlement.process_due` (due settlements; fails retry) (99-100); `treasury_cover_overdrafts` (101; world.py:486-493: the Treasury book auto-funds any overdrawn book); `cevents.process_fails` (102).
11. Corporate actions: `corporate.process_day`, `mbsdesk.process_day`, `cevents.process_day` (104-106).
12. Futures + financing: `futures.expire_contracts`, `futures.daily_settlement`; `seclending`, `lenddesk`, `repo`, `funding` (ccy loans), `pcredit`, `pequity`, `ibank` `.process_day(closing, prev)`; `fx.process_day`; `otc.process_day(closing, prev)`; `options.process_day` (108-119).
13. `pnl.mark_all` (120); `futures.sweep_margin` (121); `treasury_cover_overdrafts` AGAIN (122); `prime.process_day` (margin interest; settles on month change, prime.py:134), `accruals.process_day` (accruals.py:68), `cdesk.process_day`, `treasury.process_day` (corporate-treasurer job), `investors.process_day` (mid-month / month-end flows, investors.py:75-77) (123-127); `pnl.mark_all` AGAIN (128).
14. J - results: `careers.check_limits`, `pnl.snapshot(closing)` (NAV snapshot + day P&L explain), `risk.process_day`, `trading.expire_day_orders(closing)` (unfilled DAY orders expire here), `careers.process_missions`, `careers.maybe_review` (period-end reviews; careers.py:419 `is_month_end`) (130-135).
15. K - `briefing.build(closing)` (137); emit DAY_CLOSED (138) -> handler resets `day_trade_ids`, `day_capital_flows`, `pos.day_fills` (world.py:622-627).

Month/quarter-end hooks (VERIFIED by grep, bodies not read): careers.py:419; investors.py:75-77, 120-123; accruals.py:68; prime.py:134; private_credit.py:214; macro.py:250 (monthly releases); options.py:7, 214 (monthly + quarterly listings).

Snapshots: `pnl.snapshot(closing)` once per processed day (simulation.py:131) and at `create_portfolio` (world.py:760). A DATABASE snapshot happens only at server start (store.py:34-59 via server.py:462); none per day or per action.

Autosave: there is no save action. Each command flushes its own events (`_cmd` world.py:837-841, `place_order` 794-795, `advance` 1095, `catch_up` 1112-1113, `create_portfolio` 763, treasury commands 508/524/582). "Saved" = "the events are in SQLite". There is no manual save endpoint and no autosave timer.

### 3.5 Save / load (VERIFIED unless marked)
- Format: one SQLite DB holds every world; table `events`, one row per event with its JSON body (store.py:24-28, 81-85).
- Atomicity: one SQLite transaction per flush (store.py:82-85). NOT atomic per user action when an action flushes more than once: e.g. `Service.place_order` with `settle_ccy` - `w.fx_spot` flushes via `_cmd` (world.py:914-915, 837-841) BEFORE `w.place_order`; if the order is then rejected the FX trade stays (service.py:369-378). NOT atomic against memory: if `append_events` raises, the in-memory world already holds the events and `_pending` is not cleared (world.py:152-155) - see F1.
- Load: `World.load(store, id, strict)` (world.py:707-719): `migrate(load_events)` -> new World -> `replay` -> `_relist_orphans()` (re-lists option contracts referenced by orders/trades/positions but missing from today's chains, world.py:721-730) -> `check_integrity()`. A migrated log is NOT written back (only `worlds.save_version` is updated, service.py:265-266), so the migration re-runs on every load (INFERRED: migrations.py:94-107; the store has no bulk rewrite).
- Versions: SAVE_VERSION 3; migrations 1->2, 2->3 (migrations.py:84). A save newer than the engine raises RuntimeError (migrations.py:105-106).
- Rebuild / re-seed: `_h_world_created` REBUILDS the universe and the simulated prehistory from (start_date, seed) with the CURRENT code on every load (`build_universe`, `MarketEngine(...).bootstrap()`, world.py:269-276); REAL saves then overlay the stored real history (`REAL_HISTORY_LOADED`, world.py:313-316, with the deterministic pence->pounds repair `_fix_quote_scale` 318-336). A code change to the universe or the generator therefore changes what an old save replays into (INFERRED risk). `switch_to_real` turns a simulated save into a REAL-source save from now on (world.py:293-311; service.py:194-216; `POST /track-real`).
- Delete: `DELETE /api/worlds/{wid}` deletes every event of the world, with no server-side confirmation and no snapshot first (server.py:63-65, service.py:286-288, store.py:91-94).

### 3.6 Every API endpoint (VERIFIED server.py:32-291; handler = `Service` method)

Common to ALL `/api/worlds/{wid}/...` routes: the handler calls `Service.world(wid)`, which loads the save on first use and runs `World.catch_up()` - so even a GET can process (and WRITE) simulated days for a REAL_TIME save (service.py:258-273). "W" below = emits events and flushes them to SQLite; "R" = read model only (apart from that catch-up).

| Method | Path (prefix `/api`) | Handler | R/W | Notes |
|---|---|---|---|---|
| GET | /jobs, /worlds/jobs | `jobs` | R | job catalogue (server.py:35-36, 56-57) |
| GET | /health | `health` | R | outside the lock (server.py:351-352) |
| POST | /shutdown | router.shutdown | - | loopback only (server.py:39-42, 327-328) |
| GET | /worlds | `list_worlds` | R | store rows incl. event counts |
| POST | /worlds | `create_world` | W | body: name, seed (42), start_date, capital, portfolio_name, portfolio_type, realism, mode, initial_regime, benchmark, job, clock_mode (SANDBOX), timezone, update_time, scenario, market_source, lock_session, treasury (server.py:49-55). REAL source fetches Yahoo + macro over the network. |
| GET | /worlds/{wid} | `world_info` | R | clock, trading window, live, quote_updates, treasury state, real_market waiting reason, integrity, portfolios |
| DELETE | /worlds/{wid} | `delete_world` | W (destructive) | deletes all events |
| POST | /worlds/{wid}/advance | `advance` | W | body.days; SANDBOX only (REAL_TIME refused) - THE DAY ADVANCE |
| GET | /worlds/{wid}/securities | `securities` | R | universe with last bar from `market.history` (service.py:410-450) |
| GET | /worlds/{wid}/securities/{sid}?period=1Y | `security` | R | history from `market.history` (service.py:466-528) |
| GET | /worlds/{wid}/yield-curve | `yield_curve` | R | |
| POST | /worlds/{wid}/force-regime | `force_regime` | W | sandbox scenario control |
| POST | /worlds/{wid}/force-rates | `force_rates` | W | sandbox, +-500bp |
| GET | /worlds/{wid}/lending-history/{sid} | `lending_history` | R | |
| POST | /worlds/{wid}/repo-quote | `repo_quote` | R | quote only |
| GET | /worlds/{wid}/options | `options_underlyings` | R | |
| GET | /worlds/{wid}/options/{und}/chain?expiry= | `option_chain` | R | |
| GET | /worlds/{wid}/options/{und}/surface | `vol_surface` | R | |
| GET | /worlds/{wid}/options/{cid}/contract | `option_contract` | R | |
| GET | /worlds/{wid}/macro, /fx, /housing, /overall | `macro`, `fx_market`, `housing`, `overall` | R | housing may fetch series over the network for old saves (service.py:1351) |
| POST | /worlds/{wid}/clock | `set_clock` | W | CLOCK_CHANGED |
| GET | /worlds/{wid}/live?ids= | `live` | R (+W) | live quotes; works the resting live book first (can fill) |
| POST | /worlds/{wid}/live/work | `work_live` | W | |
| POST | /worlds/{wid}/track-real | `switch_to_real` | W | network fetch; MARKET_SOURCE_CHANGED + REAL_HISTORY_LOADED |
| POST | /worlds/{wid}/force-corporate-event | `force_corporate_event` | W | sandbox |
| GET | /worlds/{wid}/otc/dealers | `otc_dealers` | R | |
| POST | /worlds/{wid}/credit-event | `credit_event` | W | sandbox |
| POST | /worlds/{wid}/default-counterparty | `default_counterparty` | W | sandbox |
| POST | /worlds/{wid}/force-split | `force_split` | W | sandbox |
| GET | /worlds/{wid}/commodities, /commodities/{code} | `commodities`, `commodity` | R | |
| GET | /worlds/{wid}/news | `news` | R | last 100 |
| GET | /worlds/{wid}/corporate-actions | `corporate_actions` | R | |
| GET | /worlds/{wid}/events?limit&offset&type&portfolio_id&q | `events` | R | the raw log |
| GET | /worlds/{wid}/events/{eid} | `event` | R | audit chain |
| POST | /worlds/{wid}/portfolios | `create_portfolio` | W | body.capital DEFAULTS TO 10_000_000 when the key is absent (server.py:131); from_treasury |
| GET | /worlds/{wid}/treasury | `treasury` | R | |
| POST | /worlds/{wid}/treasury/{fund\|allocate\|return} | `treasury_command` | W | |
| DELETE | /worlds/{wid}/portfolios/{pid} | `delete_portfolio` | W | cash returns to the Treasury book |
| GET | .../portfolios/{pid}/dashboard | `dashboard` | R | NAV, day/MTD/YTD P&L, exposures, positions |
| GET | .../portfolios/{pid}/positions | `dashboard(...)["positions"]` | R | builds the WHOLE dashboard to return positions (server.py:145-146) |
| GET | .../portfolios/{pid}/positions/{sid} | `position` | R | |
| POST | .../portfolios/{pid}/orders/preview | `order_preview` | R | |
| POST / GET | .../portfolios/{pid}/orders | `place_order` / `orders` | W / R | body: security_id, side, quantity, order_type, limit_price, stop_price, time_in_force (DAY), strategy_tag/tag, trail_pct, condition, settle_ccy, execution (NEXT_UPDATE / LIVE) |
| GET / DELETE | .../portfolios/{pid}/orders/{oid} | `order` / `cancel_order` | R / W | |
| GET | .../portfolios/{pid}/briefing?date= | `briefing` | R | |
| GET | .../portfolios/{pid}/seclending | `seclending` | R | |
| POST | .../portfolios/{pid}/locates | `request_locate` | W | |
| POST | .../portfolios/{pid}/loans | `borrow` | W | locate_id, quantity, collateral_type |
| POST | .../portfolios/{pid}/loans/{lid}/return | `return_loan` | W | |
| GET / POST | .../portfolios/{pid}/playbook, /playbook/preview, /playbook/execute | `playbook*` | R / W | |
| GET / POST | .../portfolios/{pid}/private-credit, /commit, /sell | `private_credit`, `pc_commit`, `pc_sell` | R / W | |
| GET / POST | .../portfolios/{pid}/investment-banking, /{action} | `investment_banking`, `ib_command` | R / W | |
| GET / POST | .../portfolios/{pid}/private-equity, /structure, /{action} | `private_equity`, `pe_structure`, `pe_command` | R / W | |
| GET / POST | .../portfolios/{pid}/repo | `repo_desk` / `repo_open` | R / W | |
| POST | .../portfolios/{pid}/repo/{rid}/{action} | `repo_action` | W | |
| GET / POST | .../portfolios/{pid}/ccy-loans | `ccy_loans` / `ccy_borrow` | R / W | currency, amount, term_days, tag (existing CASH BORROWING) |
| POST | .../portfolios/{pid}/ccy-loans/{lid}/repay | `ccy_repay` | W | |
| GET | .../portfolios/{pid}/collateral, /financing | `collateral`, `financing` | R | |
| POST | .../portfolios/{pid}/margin/{draw\|repay} | `margin_action` | W | prime-broker loan (CASH BORROWING) |
| POST | .../portfolios/{pid}/fx/spot, /fx/forward | `fx_spot`, `fx_forward` | W | |
| GET | .../portfolios/{pid}/tags | `tags` | R | |
| GET | .../portfolios/{pid}/corporate-actions | `corporate_actions_for` | R | |
| POST | .../portfolios/{pid}/elections | `elect` | W | |
| GET | .../portfolios/{pid}/desk, /commodity-desk | `desk`, `commodity_desk` | R | |
| POST | .../portfolios/{pid}/spreads, /physical-delivery | `place_spread`, `set_physical_delivery` | W | |
| POST | .../portfolios/{pid}/desk/{quote\|lend\|recall\|decide\|limit\|reduce} | `quote_client`, `lend_out`, `recall_lent`, `decide_request`, `set_desk_limit`, `force_reduce` | W | |
| GET / POST | .../portfolios/{pid}/risk, /risk/stress | `risk`, `risk_stress` | R | |
| GET | .../portfolios/{pid}/otc, /otc/{tid} | `otc_book`, `otc_trade` | R | |
| POST | .../portfolios/{pid}/otc/rfq, /otc/rfq/{id}/execute, /otc/{tid}/terminate | `otc_rfq`, `otc_execute`, `otc_terminate` | W | |
| GET | .../portfolios/{pid}/counterparties | `counterparties` | R | |
| GET / POST | .../portfolios/{pid}/options, /options/exercise | `options_book`, `exercise` | R / W | |
| POST / GET | .../portfolios/{pid}/strategies/preview, /strategies, /strategies/{id} | `strategy_preview`, `place_strategy`, `strategy` | R / W | |
| GET | .../portfolios/{pid}/career | `career` | R | |
| GET | .../portfolios/{pid}/trades, /trades/{tid}, /settlements, /custody, /cash | `trades`, `trade`, `settlements`, `custody`, `cash` | R | |
| GET | .../portfolios/{pid}/ledger?limit&account&security_id | `ledger` | R | |
| GET | .../portfolios/{pid}/balance-sheet, /pnl-explain?date=, /nav-explain | `balance_sheet`, `pnl_explain`, `nav_explain` | R | |
| POST | .../portfolios/{pid}/contribute | `contribute` | W | fresh capital (CAPITAL_CONTRIBUTED) |

Route-matching quirks (VERIFIED): `sub[0]` is read without a length guard (e.g. server.py:70) - safe only because `not sub` returns earlier for GET/DELETE; a `POST /api/worlds/{wid}` (empty `sub`) would raise IndexError -> 500 (INFERRED from server.py:60-70, 368-371). `GET .../strategies` returns `options_book(...)["strategies"]` (server.py:262).

### 2.9 Other core files and the domain models (VERIFIED; belongs to section 2, placed here)
- `money.py`: `D(x)` (float via `repr`), `money(x)` cents HALF_EVEN, `price(x)` 4 dp, `qty(x)` integer HALF_EVEN, `ZERO`, `to_json_number`. Decimal context precision 28 set globally at import (money.py:12-41).
- `log.py`: one `finsim` logger; level from `FINSIM_LOG_LEVEL` (default WARNING in code although the docstring says INFO; `serve()` passes INFO); rotating file when `FINSIM_LOG` is a path (log.py:12-34).
- `tzfallback.py`: `get_zone(name)` = `zoneinfo.ZoneInfo` when the tz database exists, else built-in rule zones (US/EU DST rules, `_ZONES`) because stock Windows Python lacks tzdata (tzfallback.py:3-5, 89-123).
- `domain/models.py` (dataclasses, the in-memory projections): `Security` (17-99), `Bar(date, open, high, low, close, volume, bid, ask)` (101-111), `YieldCurve` (113-125), `Lot` (127-135), `Position` (138-184: trade-date `quantity`, custody `settled_quantity`, pending receive/deliver, lots, `cost_basis`, `valuation_adjustment` [ledger 1150], `accrued_interest` [1220], realized/dividend/interest/commissions, `mark`, `market_value`, futures VM fields, option greeks, short-book `borrowed_quantity`, `borrow_fees`, `manufactured_dividends`; property `unrealized_pnl = market_value - cost_basis`), `CashAccount` (187-204: local `balance`, `accrued_interest`, `base_value` = balance for the base currency else a stored `_base_value` = ledger 1010:CCY), `Locate`, `SecurityLoan`, `CcyLoan`, `RepoTrade`, `CollateralCall`, `Pledge`, `FXTrade`, `FXForward`, `Order` (363-390: time_in_force DAY|GTC; `execute_at` OPEN|CLOSE; `execution` NEXT_UPDATE|LIVE; `executes_at`), `Trade` (396-418), `SettlementInstruction` (421-442), `CashMovement`, `CustodyMovement`, `CorporateAction`, `NAVSnapshot` (486-509: date, nav, cash, market_value, receivables, payables, accrued, day_pnl, explain, by_position, capital_flows, cumulative_realized, unrealized, exposures, leverage, ledger_nav), `MarginCall`, `Portfolio` (524-610: ~70 fields - every desk's book lives on the Portfolio; `cash_account(ccy)` and `position(sid)` CREATE on read), `Strategy`, `NewsItem`.
- `Security` carries SIMULATION PARAMETERS next to reference data (VERIFIED models.py:17-82): `beta`, `sigma_annual` (idiosyncratic vol), `adv`, `spread_bps`, `liquidity_tier`, and RULE-BASED dividend timing `div_anchor_month` (0-2 within the quarter) + `div_day_bd` (business day of the month for the ex-date) with `dividend_per_share` "quarterly amount". With real prices these generated dividend dates would not match real ex-dates (INFERRED; corporate_actions.py not read). `yahoo` / `yahoo_scale` map ids to quote symbols (0.01 for London pence).
- Existing CASH-BORROWING structures for the Credit page (VERIFIED models.py): `CcyLoan` (251-273: principal local, `rate` "fixed for a term loan; resets daily for an open one", policy rate + spread, ledger 2750/2755), `RepoTrade` side REPO = "we borrow cash" against a security (276-299: haircut, OVERNIGHT/TERM/OPEN, `auto_roll`), the prime-broker loan `Portfolio.margin_loan` + `margin_interest_accrued` (551-552; `margin_draw`/`margin_repay`, world.py:908-912). Short-sale structures: `Locate` (207-219: `valid_through`, status OPEN/USED/EXPIRED/NONE), `SecurityLoan` (222-248: collateral CASH or Treasuries at 102%, fees, rebate, recall), `Position.borrowed_quantity` (173). Rate resets, fees, rolls and recalls all advance inside the day process (simulation.py:110-113) - INFERRED from the call sites.

---------------------------------------------------------------------------------------------

## 4. FINDINGS FOR THE REPAIR

### F1. BUG "trades disappear after a couple of days / over a weekend": failed writes leave trades in memory only (VERIFIED code + VERIFIED log evidence)

Mechanism (VERIFIED code):
- `emit()` applies an event to the in-memory world immediately (world.py:128-136); persistence happens later in `flush()` (world.py:152-155).
- If `store.append_events` raises, `flush()` raises before `self._pending = []`; the in-memory world KEEPS the events (positions, trades, cash all updated) while SQLite does not have them. Nothing rolls the memory back and nothing reloads the world.
- `Service.worlds` caches the World for the life of the process and never re-reads the store (service.py:261-270). So the UI keeps showing the unsaved trades for as long as the process lives.
- The scheduler and the live sweep SWALLOW the error: `Scheduler.tick` logs "failed to catch up" and continues (server.py:415-419); `work_live_loaded` logs "resting live orders not checked (OperationalError)" and continues (service.py:233-236).
- When the server restarts (login, reboot, crash, a weekend shutdown), `World.load` replays only what reached SQLite (world.py:707-719): everything since the first failed flush is gone. That is exactly "trades disappear after a couple of days or over a weekend".

Evidence (VERIFIED, `C:\Users\logan\.finsim\server.log`, read-only):
- server.log:5025-5045: `2026-10-02 17:00:27 ERROR finsim.server: scheduler: world W-f0648015 failed to catch up` with traceback `world.py:1113 catch_up -> self.flush() -> world.py:154 -> store.py:82 append_events -> sqlite3.OperationalError: database is locked`. The day was processed in memory; its events were not written.
- The log contains 5,143 lines with `OperationalError` (grep count), e.g. one per minute per save from 2026-10-02 11:06 through 2026-10-03 13:13 (`world W-08bc157f / W-f0648015: resting live orders not checked (OperationalError)`).
- server.log:4682: the previous process's traceback (2026-10-01 21:11, `POST /api/worlds/W-f0648015/track-real failed`) is cut mid-line and the next line is a NEW process writing at 2026-10-02 11:06:47: the old process ended abruptly, and the new one hit "database is locked" from its first minute.
- "database is locked" in SQLite WAL mode means ANOTHER CONNECTION holds the write lock on the same file: in practice, a second process using the same DB (INFERRED: a second FinSim server, e.g. the login task plus a manual launch, or another app such as the FinSim2/Desk server pointed at the same file; app.py:46-47 itself warns about "two servers racing for one port at every login"). Note `serve()` starts the scheduler (which loads every save and processes due days) BEFORE binding the port (server.py:465-468), so a duplicate launch can write to the DB even if its HTTP bind then fails - and on Windows the bind most likely does NOT fail (see F8) (INFERRED).
- Two live processes on one DB also allow a SEQ COLLISION: each process numbers events `len(self.events)+1` from its own memory (world.py:122-123, 130). Whichever flushes first owns seq N+1 in SQLite; the other process's flush then fails (PRIMARY KEY (world_id, seq), store.py:27) and its whole divergent history lives only in memory. EVIDENCE (VERIFIED, server.log grep counts): `IntegrityError` 88 lines, `database is locked` 367, `failed to catch up` 6, `replay error` 0, `UNIQUE constraint` 0 (the warning prints only the exception class). server.log:6300-6387: `world W-08bc157f: resting live orders not checked (IntegrityError)` every minute 2026-10-03 11:46-13:13, and at 13:13:16,440 an `OperationalError` line and at 13:13:16,441 an `IntegrityError` line for the SAME save - two processes sweeping the same save in the same second. The log's timestamps also run out of order in blocks (e.g. server.log:6300, 6389, 6435; 15880 is 2026-10-02 17:01 after 2026-10-03 lines) - two processes appending to one log file. An IntegrityError on `INSERT INTO events` = a duplicate `(world_id, seq)` (INFERRED: the only other constraints are NOT NULL columns).
- Current state of the files (VERIFIED by `ls`): `~/.finsim/finsim.db` last written 2026-10-14 07:48, `finsim.db-wal` (7.6 MB) 2026-10-14 09:45, `finsim.db-shm` 2026-10-14 21:09; newest backup `backups/finsim-20261014-073933.db`. `server.log` was last written 2026-10-03 13:13, so the server started on 2026-10-14 logs nowhere (the login launcher `finsim-server.vbs`, app.py:345, starts pythonw without redirecting output - INFERRED). Errors since 2026-10-03 are therefore invisible.

Other ways the same symptom can arise (VERIFIED code, trigger INFERRED):
- Two different database files: `python -m finsim serve` defaults to `data/finsim.db` relative to the current directory (__main__.py:13, port 8000) while the app uses `~/.finsim/finsim.db` (app.py:59-60, port 8765). Both exist on this machine (`C:\Users\logan\intelligence-terminal\finsim\data\finsim.db`, 376 KB, 2026-09-29; `C:\Users\logan\.finsim\finsim.db`, 63 MB). A save played under one launch path is absent or stale under the other.
- Non-strict replay drops events silently: `strict_replay` is off unless `FINSIM_STRICT_REPLAY=1` (service.py:74); a failing event is recorded in `replay_errors` and skipped while its derived events (ledger postings, settlement instructions) still apply (world.py:161-178). After a code change, a trade whose handler now fails on replay disappears on the next restart; only `/api/health` "degraded" and `world_info.replay_errors` say so (service.py:279, 307).
- Replay rebuilds the universe and simulated prehistory from (start_date, seed) with the CURRENT code (world.py:269-276), so engine changes can change what an old save replays into.
- DAY orders (the default time in force: world.py:776, server.py:154) expire at every processed day's close (simulation.py:133) - an unfilled instruction "disappears" after the next update. Not a bug, but it looks like one (INFERRED as a possible user-visible cause).

Fix direction: one writer per DB (an OS lock file / `BEGIN IMMEDIATE` probe at startup that refuses a second server; start the scheduler only after the port is bound); a unit of work per user action (collect events, write them in ONE transaction, apply to memory only after commit, or reload the world from the store when a write fails); surface write failures to the UI (no swallowing); one DB path for every launch mode; strict replay by default with the error shown; log to a file in every launch mode; a ledger/event integrity check that compares the in-memory event count with `SELECT COUNT(*)` per world.

### F2. BUG "about 10,000,000 appears in Treasury unexplained": every code path in my area that can create it (VERIFIED code; which one fired is INFERRED)
1. `POST /api/worlds/{wid}/portfolios` defaults `capital` to 10,000,000 when the key is ABSENT (server.py:131). With `portfolio_type: "TREASURY"` that 10M is FRESH capital contributed to the new Treasury book (service.py:332, 343-344 -> world.py:746-747 `contribute_capital` -> CAPITAL_CONTRIBUTED). The current UI sends `capital: null` when the field is blank (static/app.js:277), which maps to the job's standard capital instead (SANDBOX = $1,000,000,000, careers.py:40) - so the 10M default fires only for a client that omits the key (an older page, a script, another front end such as the Desk app that reuses `make_handler`, server.py:297-302).
2. `World.create_portfolio(..., capital: Decimal = D(10_000_000))` (world.py:732): any internal caller that omits capital gets $10M of fresh capital (demo.py:54 passes 10M explicitly).
3. Deleting a book sends its cash to the Treasury book as "book closed" (world.py:574-580). A book created with the 10M default and later deleted puts 10M into the Treasury.
4. The LEGACY treasury pool is added on top of the Treasury BOOK in every treasury figure: `_treasury_state` sums `tb.cash` and `w.treasury_cash` per currency (service.py:742-744); `overall()` adds the pool to NAV (`nav_with_treasury`, service.py:860-864). The pool comes from old TREASURY_FUNDED events (world.py:400-404) and legacy allocations with no `treasury_id` (world.py:423-424, 433-434, 440). The pool is outside every ledger, so it can never be explained from the books. A save that has both shows book cash + pool.
5. Automatic Treasury moves during the day process: `treasury_cover_overdrafts` runs twice a day (simulation.py:101, 122; world.py:486-493) and moves Treasury cash into any overdrawn book (TREASURY_ALLOCATED "backstop", world.py:474-484). Not a +10M, but Treasury balances change with no user action.
6. `create_portfolio(from_treasury=True)` with a blank capital takes `min(job standard, everything the Treasury can spare)` (service.py:338): for SANDBOX that is the WHOLE Treasury.
7. Front end (not my area, INFERRED): the repo ticket's default face is 10,000,000 (`<input id="rpQty" ... value="10000000">`, static/app.js:1057). A repo done in the Treasury book brings roughly 10M of cash (face x price less haircut) into it.
To decide which one fired, query the save read-only: `SELECT seq, type, sim_date, body FROM events WHERE world_id=? AND type IN ('TREASURY_FUNDED','TREASURY_ALLOCATED','CAPITAL_CONTRIBUTED','PORTFOLIO_DELETED','REPO_OPENED') ORDER BY seq`.

### F3. Day processing runs on reads (VERIFIED)
`Service.world()` runs `catch_up` on every API call (service.py:271-272), so a GET (dashboard, news) can process several simulated days, fetch the network (`real_market_ready` -> `feed.refresh("1mo")`, world.py:350-356) and write - all under the one global lock (server.py:354-355). A slow feed blocks every request.

### F4. Other atomicity gaps (VERIFIED)
- `Service.place_order` with `settle_ccy`: the FX spot is placed and flushed (world.py:914-915 via `_cmd`) before the order; if the order is rejected the FX trade stays (service.py:369-378).
- `Service.create_portfolio(from_treasury=True)`: book creation and the allocation are two flushes (service.py:339-341; world.py:763, 508).
- `World.advance(days)` flushes once after all days (world.py:1095): if day 3 of 5 raises, days 1-2 are in memory and `_pending`, and the exception leaves them unflushed until some later flush (INFERRED).
- A migrated save is never written back; the migration runs again on every load (service.py:265-266; migrations.py:94-107).
- `EventStore.snapshot` runs only at server start (server.py:462); nothing snapshots before `DELETE /api/worlds/{wid}` (service.py:286-288).

### F5. Calendar and clock risks (VERIFIED code, consequences INFERRED)
- Holidays are rule-generated and full-day only (calendar.py:53-70): no early closes, no unscheduled closures. A REAL-source save waits for a close that never comes on an unscheduled closure (`real_market_ready` returns a reason forever, world.py:342-363) and stops advancing.
- `session_closed` assumes a 16:00 close every day (clock.py:73, 80-87).
- One US calendar is used for every market's settlement date (world.py:58; calendar.py:166-167); `SettlementConfig.calendars` is never read.
- `log.py` defaults to WARNING although its docstring says INFO (log.py:1, 14).

### F6. Duplicate computations in my area (VERIFIED)
- NAV twice: economic `pnl.compute_summary(pf)["nav"]` and ledger `Ledger.nav()`; compared in `check_integrity` (world.py:192-194); both returned by `dashboard` (`nav`, `ledger_nav`, service.py:919).
- Two balance sheets: `balance_sheet_rows` from `compute_summary` marks (service.py:783-816, used by `overall`) vs `balance_sheet` from ledger account balances (service.py:1194-1203).
- Day P&L: `NAVSnapshot.day_pnl` (pnl engine) vs the dashboard's `day_pnl_live = last.day_pnl + live_delta` (service.py:895-896); MTD/YTD re-summed from `nav_history` (906-907); `overall` re-sums dashboards (826-836); `career` recomputes returns/volatility from `nav_history` (service.py:657-677); `pnl_explain` / `nav_explain` (1205-1236).
- Cash: settled `CashAccount.balance`, `trading.projected_cash` (dashboard 921, `spare_cash` world.py:463-465), `_cash_accounts` (1138-1147), `cash()` (1149-1158, with deposit/overdraft rates hard-coded in the read model as policy -25bp / +150bp, 1154), `_liquidity_projection` (1160-1172), `_upcoming_cash_flows` (948-968, recomputes coupons with `BondPricer.cash_flows`). The FX rate expression `w.fx.k(c) if c in w.market.fx.spot or c == "USD" else D(1)` is repeated (service.py:749, 1143, 1154 twice).
- Contributed capital is kept twice: `Portfolio.contributed_capital`, mutated by `_h_capital` (world.py:594) and `_move_capital` (419, 429), and ledger account 3000.
- "Collateral posted" has two definitions: the dashboard adds margin deposits + collateral posted + pledged securities at market (service.py:928) while the balance sheet line uses `collateral_posted` alone (service.py:797).
- Treasury: `_treasury_state` (book + pool), `treasury()` per-book spare, `overall.allocated` re-derived from `treasury_log` (883-885).
- Positions: `_positions` (970-1020), `overall` aggregation (838-846), exposures `_group` (941-946); `GET .../positions` builds the whole dashboard (server.py:145-146).

### F7. Surprises (VERIFIED)
- `Portfolio.cash_account(ccy)` and `Portfolio.position(sid)` CREATE an entry when read (models.py:599-610); read models that call them mutate state.
- `World.last_processed_utc` is declared (world.py:71) and never set in the files I read.
- `ClockConfig` default update time is 17:00 (clock.py:33) but sandbox saves are created with 09:00 (service.py:105).
- `Event.from_json` is `Event(**d)` with no validation (events.py:228-231).
- Every stored save is loaded into memory by the scheduler within a minute of start (server.py:412-416) - memory cost grows with every save kept in the DB (63 MB DB here).
- `health()` reads the store outside the router lock on the shared `check_same_thread=False` connection (server.py:351-352; service.py:280; store.py:22) - two threads use one sqlite3 connection (INFERRED low risk: SELECT only).
- `GET .../events` and the audit tree serve the IN-MEMORY log (`w.events`, service.py:2008-2021, 2031-2042), so the audit page cannot show that events never reached the DB (it hides F1).
- `order_preview` (a read) calls `pf.cash_account(settle)` / `pf.cash_account(ccy)` (service.py:1684, 1689), creating empty cash accounts in memory that are not events and vanish on reload.
- The session lock (`_open_for_instructions`) is applied to some commands (place_order, borrow, playbook_execute, pc_*, ib/pe commands, repo_open, fx_spot/forward, place_spread, place_strategy, otc_execute) but not to others that move cash or securities (repo_action, return_loan, ccy_repay, margin_action, exercise, elect, treasury_command, contribute) (service.py:1265-1546, 1807-1849); `ccy_borrow` guards it with a pointless `hasattr` (service.py:1474).
- The Market Place list (`securities`, service.py:410-449) and `security` (466-527) show the LAST PROCESSED DAILY BAR (`market.last_bar`), not a live price; `_synthetic_book` invents depth from ADV and the simulated regime (529-538).

### F8. Root cause candidate for F1 on Windows: a second server is easy to start and does not fail (VERIFIED code, INFERRED OS behaviour)
- The login task runs `finsim-server.vbs` (`schtasks /SC ONLOGON`, app.py:443; Startup-folder copy as fallback, 444-447), which starts `pythonw -m finsim serve --db ~/.finsim/finsim.db --port 8765` with no output redirect (app.py:340-345).
- The launcher (`finsim open` -> `cmd_open` -> `start_background`, app.py:494-500, 204-218) probes `GET /api/health` with a 1-second timeout (app.py:173-179) and, if nothing answers, spawns ANOTHER server (output appended to `server.log`). At login the first server is still snapshotting the 63 MB DB and loading every save before/while binding (server.py:462-468; scheduler loads all saves, 412-416), so a probe in that window fails.
- The second server does not die on the port clash: Python 3.14's `http.server.HTTPServer` sets `allow_reuse_address = True` (C:\Users\logan\AppData\Local\Python\pythoncore-3.14-64\Lib\http\server.py:143), applied as `SO_REUSEADDR` in `socketserver.py:469-470`; on Windows SO_REUSEADDR lets a second socket bind a port that is already in use (INFERRED from Windows socket semantics, not tested here). Both processes then run a scheduler and a world cache against one DB file - matching the "database is locked" and IntegrityError lines and the interleaved log timestamps in F1.
- Fix: refuse to start when another server owns the DB (exclusive lock file or `BEGIN EXCLUSIVE` probe), bind with `SO_EXCLUSIVEADDRUSE` / `allow_reuse_address = False` on Windows, bind before starting the scheduler, and make the launcher wait longer instead of spawning.

### F9. Smaller bugs found while mapping (VERIFIED)
- Tag P&L for bonds is 100x too small: `tags()` computes `mark = qty * close * unit / (100 if bond else 1)` (service.py:1594) but `trading._unit(bond)` is already `1/100` (engines/trading.py:255-258), so a bond's per-tag mark is divided by 100 twice and its "unrealized" is roughly minus its cost.
- OTC trade schedule is never built for CAP, FLOOR, XCCY and COMMODITY_SWAP: the same `elif t.product in (...)` appears twice (service.py:1895 and 1904); the first only defines `_fix`, the second is unreachable, so `schedule` stays empty.
- `Service.create_portfolio(from_treasury=True)` with blank capital moves `min(job standard, ALL the Treasury's spare cash)` - for SANDBOX the whole Treasury (service.py:338).

---------------------------------------------------------------------------------------------

## 5. Everything tied to the simulated day advance, and what must change for "today's live prices, paper cash"

### 5.1 The machinery to remove or replace (VERIFIED locations)
| Piece | Where | What it does today |
|---|---|---|
| Manual advance | `POST /api/worlds/{wid}/advance` (server.py:66-67) -> `Service.advance` (service.py:404-407) -> `World.advance` (world.py:1079-1097) | runs N simulated business days (SANDBOX) |
| Automatic advance | `World.catch_up` (world.py:1099-1115) from `Service.world()` on every call (service.py:271-272) and `Scheduler.tick` every 60 s (server.py:405-435) | runs every business day whose update time passed (REAL_TIME) |
| The day | `SimulationEngine.run_daily_process` / `advance_one_day` (simulation.py:34-143) | the whole cycle in section 3.4 |
| Day 0 at creation | `World.create` (world.py:701-704) | processes the start date as a full day |
| Price generation | `MarketEngine.generate_day(d)` (called simulation.py:41), `bootstrap()` prehistory (world.py:276), `build_universe(start_date, seed)` (world.py:269), `ingest_close` on replay (world.py:604-607) | simulated OHLC/curve/FX/vol/commodities/dealers/macro per day, or real closes (`real_targets`) for REAL saves |
| Seed / regime / scenario | `World.seed`, `initial_regime`, `scenario` CRISIS + `apply_scenario` (world.py:1022-1041), `force_regime` (1011-1020), `force_rates` (1069-1077), `credit_event` (952-962), `default_counterparty` (1006-1009), `force_split` (929-940), `force_corporate_event` (982-1004); routes server.py:74-77, 107-116 | sandbox scenario controls; all assume a simulated market |
| Clock model | `ClockConfig.mode` SANDBOX/REAL_TIME, `update_time`, `lock_session` (clock.py:29-44); `target_sim_date`, `next_update` (51-68); `trading_window` (128-155); `instruction_session` OPEN/CLOSE (158-167); `set_clock` + CLOCK_CHANGED (world.py:1123-1151); `POST /clock` (server.py:98-99); `_open_for_instructions` (service.py:157-163) | when a "day" is processed and which print an instruction gets |
| Instructions vs live | `Order.execution` NEXT_UPDATE / LIVE, `execute_at` OPEN/CLOSE, `executes_at` (models.py:386-388); `World.place_order` (world.py:775-796); quote ticks 09:45-16:15 every 15 min (clock.py:90-125; world.py:806-819) | NEXT_UPDATE fills at the next processed day (or next 15-min tick for REAL saves); LIVE fills now at the latest quote |
| Real-market gate | `real_market_ready` (world.py:342-363), `session_closed` (clock.py:80-87), `switch_to_real` (world.py:293-311), `market_source` SIMULATED/REAL (world.py:275, 288-291) | a REAL save waits for the real close before processing a day |
| Day-stamped state | `World.current_date` (= last processed day, not today), `day_count`, every event's `sim_date` (world.py:132), `DAY_STARTED / MARKET_CLOSE / DAY_CLOSING / DAY_CLOSED` events, `Portfolio.day_capital_flows`, `day_trade_ids`, `Position.day_fills` (reset by DAY_CLOSED, world.py:622-627), `NAVSnapshot` per day (`pnl.snapshot`), `briefing` per day | "today" means the last processed session |
| Read models built on the day model | dashboard day/MTD/YTD P&L from `nav_history` + `live_delta` (service.py:893-907); `pnl_explain` / `nav_explain` (1205-1236); `career` (647-686); `_liquidity_projection` walks business days from `current_date` (1160-1172); `world_info.clock.next_update`, `real_market.waiting`, `made_up_desks` (service.py:293-316); `live()` reports `last_close_date = current_date` (175) | |

### 5.2 Lifecycle work that ONLY happens inside the day process today (VERIFIED: called only from simulation.py:93-137 in my files)
With no day advance these never run unless they are re-driven by the real date: order execution for instructions (`trading.work_orders`), settlement of due trades (`settlement.process_lifecycle`, `process_due` - T+1 equity settlement needs a real business day to pass), fails/buy-ins, dividends/coupons/maturities (`corporate.process_day`, `mbsdesk`, `cevents`), futures expiry and variation margin, securities-lending fees and recalls, repo interest and rolls, currency-loan interest (`funding`), prime-broker margin interest (month change), cash interest accruals (`accruals`, month change), FX settlement, OTC fixings/cashflows, option expiry/exercise, marks (`pnl.mark_all`), the Treasury overdraft backstop, NAV snapshot and P&L explain, risk snapshot, DAY-order expiry, career limits/reviews, investor flows, the briefing (Daily News today).

### 5.3 What has to change in the core (design notes - INFERRED from the map, for the repair lead)
1. Replace "current_date = last processed day" with the real New York date/time (`clock.market_now()`, clock.py:76-77). Every event should carry the real timestamp; `sim_date` becomes the real trade date.
2. Delete `advance`, the SANDBOX clock mode, `update_time`, `lock_session`, regimes, scenarios, forced events and `seed` as a price source. Keep `BusinessCalendar` for settlement dates (add early closes and unscheduled closures, or take trading days from the feed).
3. Prices: only the live feed (engines/live.py `quotes`, `fill`, `work`; `available()` requires `market_source == "REAL"` and a `real_feed`, live.py:65-67) plus real closes for marks. Every save becomes a REAL save; `execution` is always LIVE (fill now at the latest quote; limit/stop orders rest and are swept by the existing 60 s `work_live_loaded`, server.py:423-429). Drop NEXT_UPDATE, OPEN/CLOSE `execute_at`, and the 15-minute instruction ticks.
4. Replace `catch_up`/`run_daily_process` with a real-time lifecycle job (scheduler tick, idempotent, keyed on real dates): settle what is due today, post accruals/fees for the real days elapsed, process corporate actions with real ex/pay dates, mark at the latest real close after 16:00 NY (and at live quotes intraday), snapshot NAV once per real trading day, expire DAY orders at the real close. It must NOT run inside read endpoints (F3).
5. Persistence (fixes F1): a single writer per DB file (lock file, refuse a second server; scheduler after bind); one atomic transaction per user action; apply to memory only after commit (or reload on failure) and return the error to the UI; strict replay by default; one DB path for every launch mode; a file log in every launch mode; a startup integrity check that compares event counts, ledger balance and NAV and shows the result.
6. Existing saves: their logs contain simulated MARKET_CLOSE days, regimes and (for SIMULATED saves) generated prices. A clean start (new save, real prices from today) avoids replaying simulated history under a live-only engine; if old saves must open, they need a migration (SAVE_VERSION 4) or a read-only "legacy" mode (INFERRED).
7. Treasury (fixes F2): remove the legacy `treasury_cash` pool from every figure (or migrate it into the Treasury book with a visible ledger entry); remove the 10M defaults (server.py:131, world.py:732); make every Treasury movement an explicit, ledgered, user-visible event (the overdraft backstop included).


8. Treat `demo.py` and the sandbox-only endpoints (advance, force-regime, force-rates, force-split, force-corporate-event, credit-event, default-counterparty, track-real, clock) as removals. `market_source`, `switch_to_real` and the `MARKET_SOURCE_CHANGED` / `REAL_HISTORY_LOADED` / `REAL_MACRO_LOADED` events become unnecessary once every save is live.

Not checked in this pass (outside my area, but they decide some INFERRED points): engines/market.py (`generate_day`, `real_targets`, `bootstrap`), engines/live.py beyond the signatures, engines/trading.py (`work_orders`, `expire_day_orders`, `projected_cash`), engines/pnl.py (`compute_summary`, `snapshot`), engines/treasury.py, engines/corporate_actions.py, careers.py, and the contents of the save database.

STATUS: COMPLETE (all 18 files in my area read; finished 12:44 ET)
