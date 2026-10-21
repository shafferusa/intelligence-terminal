# FinSim Live Terminal — work plan (parallel coding agents)

> **Owner amendment 18:28 ET: NO GLOSSARY.** WP-25 is cancelled. WP-12 builds no Glossary page/sidebar entry and does not
> depend on WP-25; WP-15 removes any glossary file or reference that exists. (See BUILD_PROMPT_2026-10-20.md, top.)


Author: Claude (Fable 5.1), 2026-10-20; **amended by the plan checker 2026-10-20 ~18:20 ET** (packages WP-17…WP-26
added; WP-00, 01, 02, 03, 05, 07, 08, 09, 11, 12, 13, 15, 16 amended) to cover the owner amendments at the top of
`BUILD_PROMPT_2026-10-20.md` (swap VM + resets, Reset lifetime with history, THREE books with PB/OTC eligibility,
PRIVATE MARKETS), B4 / B7 / B8 / B10 / B16 / C1 / C4 / D1–D13 of the build prompt and directive §7 / §11. Companion of
`TERMINAL_DESIGN.md` (the single authoritative design; section numbers below refer to it). Every package: id, title,
the files it OWNS (full paths; no two packages share a file), the interfaces it CONSUMES (module.function signatures
from the design), what to build, the tests it must add (file names, hand-worked expectations in §19 of the design),
and acceptance criteria. A package is small enough for one agent to finish and test in one sitting; WP-00 is serial
and first; everything else starts the moment WP-00's files land (with `FakeProvider` / `FixedMarks` / stubs for
packages not yet merged). An interface gap is raised as a note in `finsim/docs/design_proposals/BUILD_NOTES.md` (the
only shared design file; append-only), never by editing another package's files. Progress lines go to
`finsim/docs/BUILD_LOG.md` (shared, append-only, one line per package run).

Rules for every package: Python standard library only; runs on `py -3.12` and `py -3.14`; tests are `unittest.TestCase`
style (no pytest fixtures or `pytest.raises`), use temp SQLite files, `FakeClock`, `FakeProvider`/`FixedMarks`, never
the network, never `~/.finsim`; every expected number is worked by hand in a comment; zones only via
`finsim.tzfallback.get_zone`; `Decimal` in accounting, integer cents in SQLite; nothing outside `finsim/finsim/terminal/`,
`finsim/tests/test_terminal_*.py`, `finsim/tests/terminal_helpers.py`, `finsim/tests/terminal_fixture_server.py`,
`finsim/tests/fixtures/terminal/`, `finsim/finsim/__main__.py` (WP-14 only), `finsim/CHANGELOG.md` (WP-15 only) and
`finsim/docs/` is edited. Test commands: `cd finsim && py -3.12 -m unittest tests.test_terminal_<name>` (one process at
a time per agent on the shared laptop) and `py -3.14 -m unittest tests.test_terminal_<name>`; the full discover runs
only by WP-15/WP-16. Account ids are `{lifetime}:{role}` (§13.10); `terminal_helpers` exposes the `T1` aliases
(`TREASURY`, `US_EXCHANGE`, `DEBT`, `CREDIT`, `FOREIGN_FX`, `PRIVATE`, `pb("GS")`) so scenario tests read as in §19.

Build order (critical path in bold): **WP-00 → WP-01 → WP-02 → WP-08 → WP-11 → WP-12/WP-13 → WP-14 → WP-15 → WP-16**;
WP-03, WP-04, WP-05, WP-06, WP-07, WP-19 run in parallel after WP-02; WP-09, WP-20, WP-24, WP-25 after WP-00 (WP-24
also needs WP-01's `ChainSnapshot`); WP-22 after WP-01; WP-23 after WP-07; WP-18 and WP-21 after WP-08; WP-17 after
WP-07 + WP-08; WP-26 after WP-17 (+ WP-05 for the FX legs; WP-18 optional); WP-10 is Build 2 and runs last or later.
The owner can trade as soon as the critical path lands; every parallel package ships behind a NOT YET BUILT label
until its tests pass (§1.1).

---

## WP-00 — Core: db, bus, ledger, accounting, calendar/sessions, instrument master, books model, quote contract, test helpers, MAP.md

**Owns:** `finsim/finsim/terminal/__init__.py`, `config.py`, `units.py`, `clock.py`, `calendar.py`, `sessions.py`,
`db.py`, `ledger.py`, `commands.py`, `accounting.py`, `instruments.py`, `tables.py`, `books.py`, `treasury.py`,
`quotes/__init__.py`, `finsim/docs/MAP.md`, `finsim/tests/terminal_helpers.py`, `finsim/tests/test_terminal_core.py`,
`finsim/tests/test_terminal_ledger.py`, `finsim/tests/test_terminal_bus.py`, `finsim/tests/test_terminal_treasury.py`,
`finsim/tests/test_terminal_sessions.py`, `finsim/tests/test_terminal_accounting.py`, `finsim/tests/test_terminal_books.py`.

**Consumes:** `finsim.money`, `finsim.tzfallback.get_zone`, `finsim.calendar.us_equity_holidays`, `finsim.log`,
`<repo>/data/nyse-holidays.json`, `finsim/finsim/data/universe.json`.

**Build:** §2.1–2.11 signatures (incl. the `books.py` signatures of §2.12 and `accounting.liquidity`, per-lifetime
`integrity`); §3 DDL **complete** (every table of §3 incl. books, lifetimes, agreements, pb_schedules, otc_contracts,
otc_vm, otc_resets, private_companies, private_rounds, private_loans, dividends, news_items, the trailing-stop /
fill_mode / lock_until / NEGOTIATED / PRIVATE / SWAP columns and CHECKs — later packages cannot edit `db.py`) + seed +
migrations + backup + reader pool; §3.1 seed (books from `config.BOOKS` / `TEST_BOOK`, settings keys incl.
`pb_otc_threshold_c`, `qp_threshold_c`, `book_defaults`, `private_defaults`); §4 bus algorithm (commands row inside
the transaction, sha replay, 409 reuse, 503 busy with Retry-After, scoped integrity before COMMIT, on_commit
listeners, light handlers); §5 chart **incl. the amended accounts** (1120, 1130, 1225, 1320, 1330, 1360, 2330, 2340,
2360, 4320, 4450), `post()` with per-currency + base validation and the §5.3 plug, balances, FIFO lots with pro-rata
residual and `lock_until`; §6 every read model with scopes `{lifetime}:ALL` / `ALL` / `ALL_BOOKS` / archived (§6.0),
`liquidity()` (excess liquidity, encumbrance), positions incl. the swap / private / private-loan kinds (§6.2; the
product modules supply the marks through `services`, absent → unavailable), NAV with the amended account lists (§6.3),
P&L periods with explain, treasury, transactions, obligations, and `integrity()` with L1–L25 and M1–M5, per lifetime,
INTEGRITY_HOLD per lifetime; §7 instrument ids incl. `PRV-*` and `SWP-*`, OCC/futures parsing, FUTURES_SPECS (§12.3),
seeding from `universe.json`, `tradeable_in` with the PRIVATE and PB rules (§7.2) and `books.require_pb_otc` /
`require_private` hooks; §9 session table incl. CME breaks/holiday approximation, early closes, `day_order_expiry`,
`settle_time`, `session_overrides`; §12 tables (FEES, RATES, HTB, PB_SCHEDULES incl. BofA and the §12.4 fields,
SETTLE_TIME_ET, COMPLIANCE_LIMITS, BOOK_PROFILES); **`books.py`** (§13.10: `lifetime_id`, `account_id`,
`split_account_id`, `create_lifetime` incl. the init-capital command per lifetime, `eligibility`, `unlock_private`,
`require_pb_otc`, `require_private`); `treasury.py` handlers (`init_capital {lifetime_id}` skipped when present,
deposit (any currency), withdraw, transfer with `CROSS_BOOK` refusal, `account.update_settings`) and `sweep_in(ctx,
account, ccy, amount_c, linked_type, linked_id, shortfall_c) -> transfer_id`; `quotes/__init__.py` (QuoteRecord,
freshness incl. `private`, is_executable incl. the session/provider guards, MarkView, MarkSet, FixedMarks).
`terminal_helpers.py`: `make_runtime(tmpdir, at, offline=True, books="test" | "real")` wiring a `FakeClock`, the real
calendar/sessions/db/bus/accounting, `FixedMarks` for marks and a `FakeProvider` placeholder that WP-01 replaces
(import guarded); the `T1` account aliases; `bal`, `fund`, `submit`, `tick`, `assert_integrity`. **`docs/MAP.md`** (C1,
directive "how to work" 1): one page — modules, data flow (worker → cache → matcher → bus → ledger → read models →
API → UI), the chart and cash accounts, the Treasury definition (directive §7 wording), where each figure is computed
(one function each), the books/lifetime model; WP-15 updates it at integration.

**Tests (hand-worked):** `test_terminal_core.py` (schema creates on 3.12 and 3.14 with every §3 table and column, seed
counts: ≥ 800 equities, 25 FX pairs, 20 roots × 4 months, 4 yields, 6 indices; `accounting_day` boundaries;
`base_with_plug`; `ACCOUNT_ID_RE`), `test_terminal_ledger.py` (§19.10 incl. a CLOSED account refused), `test_terminal_bus.py`
(§19.12 rollback, replay, reuse, busy/503, light handler, integrity failure rollback, `test_d12_corrupted_balance_rolls_back`),
`test_terminal_treasury.py` (§19.1 `test_d1_initial_capital_and_deposit`, the transfer part of §19.2, CROSS_BOOK,
LIFETIME_CLOSED), `test_terminal_sessions.py` (§19.9 session table cases, settlement dates over 2026-11-26/27, CME
break, early close, overrides), `test_terminal_accounting.py` (cash and buying-power formulas with FixedMarks, NAV
partial flag, P&L explain residual 0 on a seeded ledger, L1–L25 on an empty and a seeded book, INTEGRITY_HOLD set/
cleared per lifetime, `liquidity()` formulas, scopes `ALL_BOOKS` and `{lifetime}:ALL`), `test_terminal_books.py`
(§19.14 first-run three books 1,000,000 / 25,000,000 / 1,000,000,000 and ALL_BOOKS 1,026,000,000.00; eligibility
matrix P1/F1/I1; the deposit-to-25M unlock; `has_relationships` keeps `pb_otc`; `unlock_private` creates `F1:PRIVATE`
on the first call and `P1:PRIVATE` after the 4,000,000 deposit; `require_pb_otc` / `require_private` messages).

**Acceptance:** all seven test files pass on both interpreters; `integrity("full").ok` on an empty book; scenario
19.1 exact to the cent; a corrupted `gl_balances` row makes L3 fail and the command roll back; the public signatures
match §2 verbatim (other packages code against them); `terminal_helpers.make_runtime` works without network or
`~/.finsim`; `docs/MAP.md` exists and names one function per figure.

---

## WP-01 — Quotes: providers, cache, worker, computed marks, chart events, smoke probe

**Owns:** `finsim/finsim/terminal/quotes/http.py`, `quotes/providers.py`, `quotes/cache.py`, `quotes/worker.py`,
`quotes/rates.py`, `marks.py`, `smoke.py`, `finsim/tests/test_terminal_quotes.py`, `finsim/tests/test_terminal_marks.py`,
`finsim/tests/fixtures/terminal/*.json|*.xml|*.csv|*.txt` at the top level of that folder (yahoo_spy_1m.json,
yahoo_jpy_1m.json, yahoo_daily_spy.json, yahoo_events_aapl.json, cboe_aapl_trimmed.json, treasury_curve.xml,
fred_sofr.csv, fred_hyoas.csv, finra_short.txt, frankfurter.json — trimmed copies of the probe's real shapes; NOT
`preview_aapl.json` (WP-02) and NOT the `api/` and `news/` subfolders).

**Consumes:** WP-00 `quotes.QuoteRecord/freshness/is_executable/MarkView/MarkSet`, `instruments.InstrumentMaster/Instrument`,
`sessions.SessionTable`, `db.Database`, `clock.Clock`, `commands.CommandBus.execute` (light commands
`quotes.persist_batch`, `rates.record`, `marks.record`, `provider_health.update`, `instruments.upsert`,
`dividends.upsert`), `finsim.engines.options_pricing`.

**Build:** §8.3 `http_get` (copied from `finsim2.data._http_get`, no `reqctx`), `ua_for`, `RateLimiter`,
`CircuitBreaker`, `ProviderHealth`; §8.4 providers with the parsers copied from `finsim2/data/yahoo.py::parse_chart`
and `finsim2/data/cboe.py::contracts` (OCC pattern, iv normalisation, reduced chain ≤ 400 days / ±35 %), `daily()`,
`meta()`, **`YahooChart.events(symbol)`** (`range=3mo&interval=1d&events=div,splits` → `DivEvent(ex_date, amount)` and
`SplitEvent(date, ratio)`; §13.15), `FredSeries` incl. `BAMLH0A0HYM2`, `FakeProvider` (+ `set_events`, `set_curve_dfs`
for the §19.15 FixedCurve, `set_rate`); §8.5 `QuoteCache` (pins/subscriptions/due/persist/load), `ChainCache` (LRU 2,
single-flight future), `QuoteWorker` (scheduler + 2 fetch threads, intervals, matcher hook `matcher.on_quotes` via a
settable callback, **refresher slots** that call registered callables: rates, FINRA, Frankfurter, dividend events for
held names (writes `dividends` via the light command), and the news refresher of WP-22 when `services.news` is
present), `RatesRefresher`; §8.6 `MarkProvider` (mark selection per kind incl. dispatch to `services.swaps.mark`,
`services.private.private_mark`, `services.private_credit.pc_mark` when those modules exist — absent → `unavailable`
with the note "not yet built"; `snapshot()` → an immutable `MarkSet` with `fx()`), `CurveStore` (+ `discount_factors()`
bootstrapped from par yields, semi-annual, linear in maturity — the input of §13.11), `RateStore`, `option_model`, and
the Build 2 `bond_price` / `fx_forward` functions (pure, tested now); `smoke.py`: manual network script printing 10
quotes with provenance, the Cboe honest-UA result (`refused_ua` or ok), the per-contract futures symbol check
(`ESZ26.CME`, `ZNZ26.CBT`, `6EZ26.CME`, `GCZ26.CMX`, `ZCZ26.CBT`) and one `events` fetch.

**Tests:** `test_terminal_quotes.py` — providers parse the fixtures (meta fields, last completed bar, inverted JPY,
daily bars, chain reduction counts, curve tenors, FRED blanks skipped, FINRA ratio, dividend/split events); UA per
host (Yahoo browser UA, .gov and FINRA `GOV_UA`, Cboe/Frankfurter `TERMINAL_UA`); circuit breaker opens after 5
failures and closes on success with a stubbed `http_get`; worker scheduling with a FakeClock (ORDER 15 s beats HELD
30 s; CLOSED 300 s; chain single-flight shares one fetch between two requesters; refresher slots run on their
intervals; no network); `cache.persist/load_persisted` round trip returns `close`/`stale`; freshness table (§19.12)
incl. `private`. `test_terminal_marks.py` — option model inputs and labels; mark selection per kind; `snapshot()`
immutability; `discount_factors()` on a flat 4.00 % par curve reproduces the §19.15 dfs to 8 dp; §19.13 bond and CIP
pins (`skipUnless(BUILD2)` only for execution, the pricing functions are asserted now).

**Acceptance:** tests pass on both interpreters; the `_SPX` fixture (trimmed) parses within the reduction rules;
`smoke.py` run once by the integrator on the laptop with its output pasted into `BUILD_NOTES.md` (Cboe UA verdict and
the futures symbol table) — options and deferred futures months are set LIMITED/computed-only accordingly; memory of
a full SPX parse measured < 150 MB peak (manual note).

---

## WP-02 — Equities execution, the matcher, trailing stops, package matching

**Owns:** `finsim/finsim/terminal/orders.py`, `execution.py`, `finsim/docs/TERMINAL_EXECUTION.md`,
`finsim/tests/test_terminal_orders.py`, `finsim/tests/test_terminal_equities.py`, `finsim/tests/test_terminal_sweeps.py`,
`finsim/tests/fixtures/terminal/preview_aapl.json`.

**Consumes:** WP-00 (`commands`, `ledger.post/relieve_fifo/open_lot/position`, `accounting.available/buying_power`,
`treasury.sweep_in`, `books.require_pb_otc/require_private`, `instruments`, `sessions`, `tables.FEES`,
`quotes.freshness/is_executable`); WP-01 `QuoteCache`, `ChainCache`, `MarkProvider.snapshot` (tests use `FakeProvider`
+ `FixedMarks`); `lending.locate/open_borrow/consume_for_short/return_borrow` (WP-06; WP-02 tests stay long-only and
stub these with a `FakeLending` in the test file); `products.options.margin_requirement_c` and
`products.futures.im_required_c` (WP-03/WP-04; called through `services` and absent-safe: if the module is missing the
fill path raises `NOT_IN_THIS_BUILD` for that product); `strategies.package_legs` (WP-24; absent → `package` requests
answer 501 NOT_BUILT); `prime.schedule` (WP-07; absent → DEFAULT fees).

**Build:** §10.1–10.8 and §10.9's package hooks (`hedges.package` is WP-03; `order.submit` calls it when `hedge` is
present; `package` requests resolve through `strategies.package_legs`): `validate()` with every code incl.
LIFETIME_CLOSED, NOT_ELIGIBLE (via `books`), TRAIL_INVALID, AON_LEGS; `preview()` and `PreviewStore`; pure
`evaluate()` incl. **TRAILING_STOP** (§10.5 step 8a: ratchet persisted through `order.match` action RATCHET) and
`fill_cap()`; **`evaluate_package()`** and the handler **`order.match_package`** (AON: every leg executable from the same
chain observation with displayed size ≥ remaining, one command, one margin recomputation; §10.9); `Matcher`
(on_quotes / on_submit / 15 s sweep; one command per decision; AON packages evaluated as a unit; re-checks status/
expiry inside the handler); handlers `order.submit` (incl. CONFIRM_REQUIRED, PREVIEW_STALE, hedge legs
WAITING_PARENT, AON packages WORKING together), `order.cancel` (a package cancel cancels every leg), `order.match`,
`order.expire`, `order.resume`, `order.record_rejection`; `execution.apply_fill` for equities (and the generic steps
every product uses, incl. origin NEGOTIATED with calendar-day settlement for WP-19 and the option legs of a package),
`apply_system_fill`, `fund()` with the §10.8 order and the §11.6 overdraft rule (attention in the same transaction);
hedge child resize (§10.7 step 8); `docs/TERMINAL_EXECUTION.md` = §1.3 sentences + §10.5/10.6 + TRAILING + AON in the
owner's words.

**Tests:** `test_terminal_orders.py` (§19.9 all cases except hedge sizing needs only the ratio logic; evaluate() table
over every (type, side, marketability, first_eval) cell incl. TRAILING_STOP; §19.22 trailing ratchets 337.50 → 339.00
→ fill 338.90, trail_pct 337.59, BUY variant, TRAIL_INVALID, ratchet persisted; AON iron condor of §19.22 with a stubbed
`strategies.package_legs` returning the four legs: AON_SIZE then the one-command fill, premiums +4,200.00, fees 26.00,
margin 28,100.00, AON_STALE, package cancel; matcher idempotency on the same observation; cancel/expire; rejections
recorded; `test_d6_session_limit_day_expiry`, `test_d7_hedge_package_resize`), `test_terminal_equities.py` (§19.3,
§19.4), `test_terminal_sweeps.py` (§19.2 incl. CONFIRM_REQUIRED and the funding refusal; L19 linkage; overdraft rule
with a stubbed VM posting; **`test_d2_buy_100_aapl_zero_cash`**: fee 1.00, sweep 33,951.00 linked, NAV 9,999,999.00).

**Acceptance:** every scenario exact; `evaluate()` and `evaluate_package()` have no I/O; a fill never happens on a
stale, computed, previous-session or post-expiry quote; the same bar never fills twice; an AON package never fills
partially; preview JSON shape frozen as a fixture (`tests/fixtures/terminal/preview_aapl.json`) that WP-13 codes against.

---

## WP-03 — Listed options and hedge candidates

**Owns:** `finsim/finsim/terminal/products/__init__.py`, `products/options.py`, `hedges.py`,
`finsim/tests/test_terminal_options.py`, `finsim/tests/test_terminal_hedges.py`.

**Consumes:** WP-00; WP-01 `ChainCache`, `MarkProvider`, `option_model`; WP-02 `execution.apply_fill/apply_system_fill/fund`,
`orders` package hooks (`hedges.package(ctx, services, primary_order_id, candidate_id, legs, ratio)`); WP-06
`lending.open_borrow/locate` for the needs-a-borrow assignment path (stubbed in tests); WP-24 `strategies` (the
"Strategies" candidates list is served by WP-24; `hedges.candidates` only adds the §10.9 rows, incl. `TRS_OVERLAY`
when `services.swaps` exists and `books.eligibility().pb_otc`).

**Build:** §13.3: `ensure_option` use, margin formula `margin_requirement_c` (naked/covered/spread/index; a package's
legs evaluated together — iron condor = max side, §19.22), handlers `options.margin_mark` (1310 moves with `fund()`,
`margin_state`), `options.expire` (CLOSE mark rules, OCC threshold, EXERCISE/ASSIGNMENT/EXPIRY fills with the §5.4
templates, SKIP cases, index cash settlement), `options.exercise` (early, long American, option session); chain view
rows for the API (`chain_view(underlying, expiry)`); §10.9 hedge candidates for every primary type and `package()`.

**Tests:** `test_terminal_options.py` (§19.7 all variants incl. `test_d8_expiry_exercise_lapse_assignment`; margin pins
67,172.00 and 6,240.00; covered call 0 with reserved shares; spread width; iron condor 28,100.00; a chain outage → no
fills), `test_terminal_hedges.py` (strike/expiry selection from a fake chain: 0.93/1.07 snapping, ≥ 90 days; sizing
floor(qty/100); index overlay rounding; cost from ask/bid; DELTA hedge rounding; candidates carry chain quality;
`TRS_OVERLAY` present only with an eligible PB account).

**Acceptance:** scenario 19.7 exact; L16 holds after every option command; expiry never posts without an observed
close; hedge package legs resize on a 60 % partial (with WP-02).

---

## WP-04 — Futures

**Owns:** `finsim/finsim/terminal/products/futures.py`, `finsim/tests/test_terminal_futures.py`.

**Consumes:** WP-00 (`instruments.FUTURES_SPECS/last_trade_date/ensure_futures_listings`, `sessions.settle_time`,
`tables.SETTLE_TIME_ET`); WP-01 `MarkProvider`, `YahooChart.daily` (via services); WP-02 `execution.apply_fill`
(IM posting hook `im_required_c`), `apply_system_fill`, `fund`.

**Build:** §13.4: `im_required_c(inst, qty)`; handlers `futures.vm` (observed print at/before the settle time, daily
fallback, VM formula with the fills term, `fund()` + overdraft rule, `futures_settle`, `marks(SETTLE)`),
`futures.final`; IM move on open/close fills (called by WP-02's fill path); listing roll in HOUSEKEEPING (function
exposed for WP-08).

**Tests:** `test_terminal_futures.py` (§19.8 all steps incl. the unfunded VM variant; rerun idempotency; grains
multiplier 50 on a cents quote; a missing settle print → SKIPPED; final settlement on the last trade date).

**Acceptance:** scenario 19.8 exact; L15 holds at all times; nothing is invented when the print is missing.

---

## WP-05 — FX spot and currency loans

**Owns:** `finsim/finsim/terminal/fx.py`, `credit.py`, `finsim/tests/test_terminal_fx.py`, `finsim/tests/test_terminal_loans.py`.

**Consumes:** WP-00 (`ledger.post` with explicit `fx_rate` lines, `accounting.cash`, `treasury.sweep_in`, `tables.RATES/POLICY_INDEX`,
`calendar.settlement_date`), WP-01 `RateStore`, `MarkProvider.snapshot().fx`, WP-02 `fund` (for the USD leg shortfall
at settlement — invoked by WP-08's SETTLE_DUE through `fx.settle`), WP-08 `lifecycle.accrue_segment(ctx, kind, ref, base_c, rate, basis, night) -> int` (the cumulative-rounding
accrual helper of §11.3; WP-05's tests carry a local copy of the same signature so they run before WP-08 is merged).

**Build:** §13.5 `spot_preview`, handler `fx.spot` (two-leg entry at the deal rate, settlements, `fx_deals`),
`fx.settle` (both legs with `fund()` for a payable shortfall); §13.6 `credit.loan_draw`, `credit.loan_repay`,
`credit.pb_draw/pb_repay`, `accrual_lines_loan` (segment restart on a new policy observation); Build 2 `fx.forward`
stub returning `NOT_IN_THIS_BUILD`; the currency-leg helper `fx.fund_ccy(ctx, account, ccy, need_c, link)` that WP-26
uses for XCCY notional exchanges (account cash in that currency → Treasury's → refuse `INSUFFICIENT_FUNDS_CCY`).

**Tests:** `test_terminal_fx.py` (§19.6 every step and balance incl. −20.00, −6,306.32, −6,392.64, the 6,805.01
settlement sweep, the 76.38 night, −6,240.01 and −6,757.92 — `test_d9_eur_loan_and_spot`; JPY inversion; a stale FX
quote → 409), `test_terminal_loans.py` (draw/accrue/repay refusal/partial repay; unsupported currency; USD loan same-day
stock buy; §19.23 floating reset 83.33 / 83.34 after a new observation; early repayment with accrued 319.45).

**Acceptance:** scenario 19.6 exact to the cent with residual 0 at every step; L1 per-currency and base balance on
every FX entry; L14 holds.

---

## WP-06 — Credit: securities lending (borrow book, collateral marks, accruals) and repo (Build 2 stub)

**Owns:** `finsim/finsim/terminal/lending.py`, `repo.py`, `finsim/tests/test_terminal_shorts.py`, `finsim/tests/test_terminal_lending.py`.

**Consumes:** WP-00 (`ledger`, `treasury.sweep_in`, `tables.HTB/RATES`, `instruments`), WP-01 `MarkProvider`, FINRA
store (`finra_short` table filled by WP-01's refresher; tests insert rows), WP-02 `execution.apply_fill/fund` and the
short-sale hooks (`consume_for_short`, `return_borrow` called from WP-02's fill path), WP-08 `lifecycle.accrue_segment`.

**Build:** §13.1 and §12.2: `locate()` (tiers, availability, collateral), `open_borrow()` (collateral entry with
`fund()`, `reason 'pending short sale'`), `consume_for_short()`, `return_borrow()` (pro rata collateral, fee/rebate
settlement), `collateral_mark_lines()`, `accrual_lines()`, handlers `credit.borrow_stock`, `credit.return_stock`,
`credit.collateral_mark`; recall INFO notice rule; BORROW_CLEANUP helper for WP-08 (also used by `book.reset_lifetime`
for borrows backing only cancelled orders); `repo.py` handlers return `NOT_IN_THIS_BUILD` (Build 2 body specified in §13.9).

**Tests:** `test_terminal_shorts.py` (§19.5 end to end with WP-02: every posting, the 0.42/0.41/0.42 and
4.77/4.78/4.77 accruals, the 2,040.00 call and return, the cover with 1.68 and 19.29, NAVs 199,995.00 / 198,008.07 /
199,007.61; **`test_d4_collateral_marks_both_ways`**: +10 % → call 5,100.00 by sweep, −5 % → 2,805.00 returned;
**`test_d5_no_borrow_and_partial`**: NO_BORROW; T3 → 800; BACKS_SHORT), `test_terminal_lending.py` (tier table over
FINRA ratio / ADV cells; collateral rounding; cleanup returns an unused borrow; recall notice after 3 sessions ≥ 0.80).

**Acceptance:** scenario 19.5 exact; L12 and L13 hold after every command; no buy-in or recall is ever generated.

---

## WP-07 — Prime brokers: accounts, editable fee schedules, financing, credit interest, monthly minimum

**Owns:** `finsim/finsim/terminal/prime.py`, `finsim/finsim/terminal/static/components/pbschedule.js`,
`finsim/tests/test_terminal_prime.py`.

**Consumes:** WP-00 (`tables.PB_SCHEDULES` incl. BofA, `pb_schedules` table, `accounts` rows, `commands`, `books`),
WP-02 (`execution.fund` PB-draw branch calls `credit.pb_draw` from WP-05 via services), WP-05 `credit.pb_draw/pb_repay`,
WP-08 `accrue_segment`, WP-12 `T.*` helpers (for the component).

**Build:** §13.7 and §12.4: handler `account.open_pb` (idempotent per broker; nine brokers incl. `BOFA`; copies the seed
schedule into `pb_schedules`), **`account.update_schedule`** (light; field validation; SETTINGS_CHANGED event with
before/after; never restates past postings), `schedule(account) -> PbSchedule` read from `pb_schedules` and consumed by
WP-02's fee lookup and WP-17's half-width / spread / `swap_margin_mode`, PB headroom (`pb_headroom_c` = 50 % long MV −
2700 − OTC IM/VM posted, capped by `pb_limit_c`) exposed for `accounting.buying_power` (WP-00 calls
`prime.pb_headroom_c` if present), nightly PB interest lines (`accrual_lines_pb`), **PB credit interest lines**
(`accrual_lines_pb_credit`, kind `pbcredit`) and the **monthly minimum** line (`monthly_minimum_line`) for WP-08; OTC
non-swap endpoints answering `NOT_IN_THIS_BUILD`; `components/pbschedule.js` (Settings → Prime brokers editor, mounted
by WP-12's settings page).

**Tests:** `test_terminal_prime.py` (open GS → `F1:PB_GS` with the schedule; 409 on reopen; a buy in `F1:PB_GS` charges
0.0030/share min 1.00: 1,000 AAPL @ 339.50 → fee 3.00 (**`test_d10_goldman_buy`** with DVP settlement rows in the PB
account); financing switch: with Treasury empty a 50,000 need draws 2700 up to headroom; interest night `50,000 ×
(3.87 + 1.25) % / 360 = 7.1111 → 7.11`; per-PB margin isolation; §19.23: schedule edit 0.0025 → fee 2.50 with the
before/after event; credit interest 86.67; monthly minimum 380.00 and the SKIPPED case; BofA row present; an
ineligible lifetime cannot open a PB (through `books`)).

**Acceptance:** tests pass; L14 (2700 ≤ limit) holds; Risk shows exposure per PB (with WP-09); the schedule editor
renders from the fixture server (with WP-12).

---

## WP-08 — Treasury sweeps, lifecycle scheduler and jobs

**Owns:** `finsim/finsim/terminal/lifecycle.py`, `finsim/tests/test_terminal_lifecycle.py`, `finsim/tests/test_terminal_persistence.py`.

**Consumes:** WP-00 (`commands`, `ledger`, `accounting` (snapshot inputs, integrity per lifetime), `treasury.sweep_in`,
`calendar`, `sessions`, `clock.accounting_day`, `books.unlock_private`), WP-01 (`MarkProvider`, `YahooChart.daily` via
services, `marks.record`), WP-02 (`execution.fund`, `order.expire` handler), product job handlers from
WP-03/04/05/06/07/17/18/21/26 (`options.expire`, `options.margin_mark`, `futures.vm`, `futures.final`, `fx.settle`,
`credit.collateral_mark`, lending accrual and cleanup helpers, loan and PB accrual lines, PB credit interest and
monthly minimum, `swap.vm`, `swap.fix`, `swap.pay`, `trs.reset`, `xccy.exchange`, `xccy.fx_reset`, `div.ex`, `div.pay`,
`pc.pay`, private-credit accrual lines) — all optional at import (a missing module skips its job with a note in
`/api/jobs`).

**Build:** §11.1 `Job` protocol, `REGISTRY` in the amended tick order (§11.1), `LifecycleScheduler` (30 s tick,
`tick(at)` for tests, catch-up by state, `job_failures` backoff), `accrue_segment(ctx, kind, ref, base_c, rate, basis,
night) -> posted_c` implementing §11.3 with `accrual_state`; §11.2 jobs SETTLE_DUE (incl. `fund()`, `SETTLEMENT_UNFUNDED`
and calendar-day due dates for private trades), ACCRUE_DAILY (all kinds incl. swaprcv/swappay/trsfin/pccpn/pcoid/
pbcredit through the product modules' line functions, RATE_LAG, 10-day skip rule), PAY_MONTHLY (+ PB_MONTHLY_MIN),
DIVIDEND / DIVPAY, PC_PAY, SWAP_FIX, SWAP_PAY, TRS_RESET, XCCY_EVENTS, SWAP_VM, SWEEP_OUT (§11.2 formula, once per
calendar day), MARKS_RECORD (16:20 / settle+5 / 17:05, daily-bar catch-up "recorded late", CLOSE_REVISED notice; SPY
always recorded for §6.9), SNAPSHOT (17:45 every calendar day per account and `{lifetime}:ALL`; `nav_snapshots` fields
incl. `flows_day_c`, `pnl_day_c`, `partial`; calls `books.unlock_private`), EXPIRE_ORDERS, BORROW_CLEANUP, HOUSEKEEPING
(futures listing roll, expired options/futures status, private-company master re-seed, quote_cache prune only),
INTEGRITY_FULL (per lifetime → `meta`, Attention, INTEGRITY_HOLD); the `POST /api/jobs/run` entry point function
`run_now(services, job=None)`; `due()` of every job restricted to OPEN lifetimes.

**Tests:** `test_terminal_lifecycle.py` (§19.11 catch-up with 4 nightly keys per object and exactly-once; settlement
idempotency and the unfunded path; sweep-out numbers of §11.5 and the 40,000.00 case; snapshot `partial` flag and
`pnl_day_c` continuity (M4) per lifetime; DAY/GTC expiry keys; housekeeping never touches `order_events`; a failing job
backs off and surfaces; a job whose module is missing is SKIPPED with a note; §19.23 Treasury cash interest 576.02;
jobs ignore CLOSED lifetimes), `test_terminal_persistence.py` (§19.12 restart persistence over §19.2–19.5 with the real
jobs — `test_restart_between_every_step` for D3).

**Acceptance:** every job replays to a no-op; a 7-day clock jump posts exactly 7 accruals per object; the snapshot
chain has no gaps after catch-up; restart persistence exact.

---

## WP-09 — Risk and compliance (inform only), per-book profiles, private/illiquid and OTC buckets

**Owns:** `finsim/finsim/terminal/risk.py`, `compliance.py`, `finsim/tests/test_terminal_risk.py`.

**Consumes:** WP-00 (`accounting.positions/nav/cash/liquidity`, `tables.COMPLIANCE_LIMITS/FUT_DV01/BOOK_PROFILES`,
`books.eligibility`, `instruments` spec fields), WP-01 (`MarkProvider`, `option_model` for scenario re-pricing, chain
greeks), WP-02 preview hook (`orders.preview` calls `compliance.pretrade` and `risk.warnings_for_preview` if present),
WP-17/26 (`swaps.mark` with a shifted curve, `dv01_c` — optional), WP-19 (private positions via `accounting`).

**Build:** §14 `risk.report` (exposures, greeks, DV01 incl. swaps, FX exposure, scenario table through the same pricing
as the marks, concentration, financing & liquidity incl. per-PB headroom with OTC IM/VM, excess liquidity and
encumbrance, the private/illiquid bucket with lock-up calendar, the OTC block per PB, warnings incl. the OTC VM and
private thresholds), `compliance.pretrade` and `holdings` with every notice named in §14, filtered by the book's
profile (§12.5) and the "verify the current rule text" wording; never a block.

**Tests:** `test_terminal_risk.py` (every figure ties to `accounting.positions` on a seeded book; Rule 201 notice when
`day_low ≤ 0.90 × prev_close`; 13D/G at 5 % of `shares_outstanding`; self-trading notice across two own accounts;
wash-sale notice; option/futures limit notices; PDT only for INDIVIDUAL; 13F "applies from the start" only for
INSTITUTION; scenario table signs; private marks unchanged under shocks; warnings never appear as blockers;
`test_d11_compliance_notices`, `test_exposure_per_pb`).

**Acceptance:** tests pass; `/api/risk` and `/api/compliance` (WP-11) render from these functions only.

---

## WP-10 — Bonds and rates (Build 2; ships LIMITED in Build 1)

**Owns:** `finsim/finsim/terminal/products/bonds.py`, `finsim/tests/test_terminal_bonds.py`.

**Consumes:** WP-00, WP-01 (`CurveStore`, `RateStore`, `MarkProvider.bond_price`), WP-02 `execution.apply_fill`,
WP-08 `accrue_segment`, WP-06 for bond shorts.

**Build:** §13.8: monthly benchmark listing, curated corporates, computed marks via `MarkProvider.bond_price`
(exposed in Build 1 for the DEBT page), handlers `bonds.accrue`, `bonds.coupon`, `bonds.mature`, fill postings with
accrued; DEBT execution enabled behind `settings.build2_bonds = true` (default false → `UNSUPPORTED` at validate with
"model price, not executable in this build").

**Tests:** `test_terminal_bonds.py` (§19.13 price 96.1027 and accrued 0.5503; a buy of 1,000,000 face at 96.1027 with
accrued 0.5503 → `Dr 1110 961,027.00 / Dr 1220 5,503.00 / Dr 5000 200.00 (0.02 × 10,000 ≥ 5.00) / Cr 2100 966,730.00`;
nightly accrual delta; coupon on schedule; maturity at 100 with realised = 100 − 96.1027 per 100).

**Acceptance:** tests pass; with the flag off the DEBT page shows computed marks and no ticket; with it on the
scenario is exact.

---

## WP-11 — HTTP server and API

**Owns:** `finsim/finsim/terminal/api.py`, `server.py`, `finsim/tests/test_terminal_api.py`, `finsim/tests/test_terminal_server.py`.

**Consumes:** everything public in WP-00…WP-09 and WP-17…WP-26 (stubs where a package is not merged: a route whose
service is missing answers 501 `NOT_BUILT` with the package id, never a fake number).

**Build:** §15 every route with the documented shapes and codes — incl. the amended routes `/api/books*`,
`/api/history/{lifetime}/…`, `/api/accounts/{id}/schedule`, `/api/otc/*`, `/api/private/*`, `/api/credit/private*`,
`/api/strategies*`, `/api/dividends`, `/api/news`, `/api/portfolio/performance`, `/api/unsupported`, the `book` query
parameter resolving scope `ALL` — (JSON codec for cents/Decimals/epochs, `X-Replayed`, `Retry-After`, 202 for
fetch-triggering reads, CSV exports, loopback-only shutdown/jobs); `Router.handle` testable without sockets; §16
`TerminalServer` (SO_EXCLUSIVEADDRUSE, `allow_reuse_address = False`), `Runtime` assembly (optional product modules
bound when importable, `news` when WP-22 is present), **first-run `books.create_lifetime` for every book without an
OPEN lifetime** (§3.1), startup sequence with exit codes, `msvcrt.locking` lock, rotating log via `finsim.log.get_logger`,
worker start order, `Supervisor` (restart + report, stall detection), graceful shutdown, `--offline` guard and
`health.offline`, `health.news.injection_flags`.

**Tests:** `test_terminal_api.py` (§19.12 API round trip through `Router.handle`: health shape incl. the marker,
preview → submit → order → fills → transactions, every error body, 400 without request_id, 202 chain fetching, CSV
export; `/api/books` shape with eligibility; `/api/history/P1/...` read_only after a stubbed reset; 501 NOT_BUILT for a
missing module route; `test_orders_history_keeps_cancelled_expired`; one `http.client` test on a temp port),
`test_terminal_server.py` (duplicate server exit 3 — `test_duplicate_server_exit_3`, exclusive bind `OSError`, health
< 50 ms while the writer lock is held 2 s, shutdown within 3 s and lock released, off-loopback shutdown 403, supervisor
restarts a killed worker thread and writes an attention row, `--offline` on the default home refused, first run
creates P1/F1/I1).

**Acceptance:** tests pass on both interpreters; no route touches the writer lock on a read or the network on the
request thread (asserted with a provider that sleeps 5 s while `GET /api/accounts` answers < 200 ms).

---

## WP-12 — UI shell, components and pages (book switcher, Daily News mount, Settings sections, History, style tokens)

**Owns:** `finsim/finsim/terminal/static/index.html`, `terminal.css`, `core.js`, `components/sidebar.js`,
`components/header.js`, `components/attention.js`, `components/freshness.js`, `components/grid.js`,
`components/chart.js`, `components/chain.js`, `components/bookswitcher.js`, `pages/markets.js`, `pages/instrument.js`,
`pages/positions.js`, `pages/orders.js`, `pages/cash.js`, `pages/transactions.js`, `pages/treasury.js`,
`pages/portfolio.js`, `pages/accounting.js`, `pages/risk.js`, `pages/activity.js`, `pages/watchlists.js`,
`pages/settings.js`, `pages/glossary.js`, `pages/history.js`, `finsim/tests/terminal_fixture_server.py` (serves canned
JSON for every route from `tests/fixtures/terminal/api/*.json`, used for the browser check),
`finsim/tests/fixtures/terminal/api/*.json` except WP-13's `preview_*.json`, `hedges_*.json`, `orders_submit_*.json`,
`strategies_*.json`, `finsim/tests/test_terminal_static.py`.

**Consumes:** §15 route shapes (fixtures recorded from WP-11's tests), §17 spec, `/api/state.conventions`,
`/api/books`, `/api/unsupported`; WP-25 `glossary-data.js`; the components of WP-07/17/19/20/21/23 (mounted when their
file exists; a missing file degrades to a "not yet built" block).

**Build:** §17.1–17.5, §17.7, §17.8 (renderer only) and §17.9 (mount only): the shell, **tokens copied verbatim from
`finsim/finsim/static/style.css` with the aliases of §17.2**, `T.*` helpers, every component and page, the freshness
renderer incl. `q-private` ("nothing shows a bare number"), pollers with visibility pause, lost-response rule, offline
banner, PAPER tag, header card, the **book switcher** (selected book → every poller passes `book=`), the **sidebar in
B16 order** with Daily News first and the eligibility states, Portfolio with the B16 columns and the Performance tab
mount, Settings sections (Books, Lifetimes mount, Prime brokers mount, Private defaults, Data Status, Unsupported list
from `/api/unsupported`), History pages, NOT IN THIS BUILD / NOT YET BUILT states, glossary renderer from
`glossary-data.js` (see-also arrays), legacy link. `components/ticket.js`, `preview.js`, `hedge.js` are WP-13
(index.html loads them; a missing file degrades to a "ticket not built" dock); `pages/news.js` (WP-22),
`pages/private_markets.js` + `privateticket.js` (WP-19), `swapticket.js` (WP-17), `privatecredit.js` (WP-21),
`performance.js` (WP-20), `lifetimes.js` (WP-23), `pbschedule.js` (WP-07) are loaded by index.html and mounted when
present.

**Tests:** `test_terminal_static.py` (every referenced JS/CSS file exists or is in the optional list, ASCII/UTF-8
without CDN links; **every `:root` token of `finsim/finsim/static/style.css` is present in `terminal.css` with the same
value** plus the aliases; the fixture server serves every route in §15 with the fixture; a Python `html.parser` smoke
that `index.html` has the roots the scripts expect incl. `#bookswitcher`, `#news`, `#history`). Browser check (manual,
scripted in `docs/TERMINAL_CHECKLIST.md` by WP-15): every page renders from the fixture server with zero console
errors at 1100 px and 1440 px; switching the book re-renders every page.

**Acceptance:** pages render from fixtures; freshness chips change with injected ages; attention strip shows a forced
provider failure; keyboard map works incl. Alt+B; no bare numbers; no console errors; the old style tokens are reused.

---

## WP-13 — Ticket, preview and hedge panel (trailing stops, strategies tab, AON, eligibility blockers)

**Owns:** `finsim/finsim/terminal/static/components/ticket.js`, `components/preview.js`, `components/hedge.js`,
`finsim/tests/fixtures/terminal/api/preview_*.json`, `hedges_*.json`, `orders_submit_*.json`, `strategies_*.json`.

**Consumes:** WP-12 `T.*` helpers and the dock slot; §10.3 preview shape (fixture from WP-02); §15 `/api/orders`,
`/api/orders/preview`, `/api/hedges`, `/api/strategies`, `/api/strategies/preview`, `/api/credit/locate` shapes and error
codes.

**Build:** §17.6: fields incl. the `TRAIL` type (trail amount / %), debounced live preview, submit enablement rules, the
PREVIEW_STALE diff with Accept & submit / Cancel, CONFIRM_REQUIRED confirm, QUOTE_STALE/NO_BORROW/INSUFFICIENT_FUNDS
blockers with the three amounts, NOT_ELIGIBLE / NOT_ELIGIBLE_QP / LIFETIME_CLOSED blockers with the plain-English
reason, LOCKUP warning, borrow block, margin block (package margin once for AON), conventions footer (incl. TRAILING /
AON), result panel, hedge panel auto-population on instrument entry (202 spinner, single shared fetch) with the two
tabs **Hedges** (§10.9 candidates) and **Strategies** (the 60 templates: group filter, units / tenor / strike
overrides, per-leg prices, totals, payoff grid, AON sentence and single Submit), candidate edit (strike/expiry/ratio),
keyboard map inside the ticket, request-id handling and the lost-response re-GET.

**Tests:** fixture-driven browser check items in `docs/TERMINAL_CHECKLIST.md` (preview renders every block from
`preview_aapl.json`; 409 PREVIEW_STALE shows the diff and resubmits with the new preview_id; CONFIRM_REQUIRED resubmits
with `confirm_large`; a simulated network error after submit triggers the orders re-GET before re-enabling Submit; the
Strategies tab renders `strategies_iron_condor.json` with the AON sentence; a TRAIL order shows the ratchet rule).

**Acceptance:** against the real server (WP-15): enter AAPL → preview within 400 ms of typing stop → hedge candidates
appear → submit 1 share during the session → fill shown → position visible after F5 and after a server restart.

---

## WP-14 — Launcher, CLI, installer

**Owns:** `finsim/finsim/terminal/app.py`, `finsim/finsim/__main__.py` (re-pointed; `legacy` subcommand),
`finsim/tests/test_terminal_app.py`, `finsim/docs/TERMINAL_RUNBOOK.md`.

**Consumes:** WP-11 `server.serve`, `finsim.app` helpers (`_browser_candidates`, `open_window`, `_known_desktop`,
`_win_special`, `_ico_from_png`, `_ascii`, `_vb`), `finsim.api.server.serve` (legacy), `finsim2/install/*.vbs` as the
VBS model.

**Build:** §18 every command with the exit codes; `launcher_files()` pure; `install` ordering (sha256 baseline → stop
the old engine → remove task/old VBS → write/copy VBS → shortcuts → start → status → sha256 again); `status` version
comparison and "restart to serve the new code"; `stop` liveness by `OpenProcess` + image name; `legacy` on a backup
copy; `open` via the VBS; `uninstall --purge` confirmation; the runbook with the final PowerShell sequence and the
rollback (`uninstall`, restore the old Startup entry from `<home>/backups/`).

**Tests:** `test_terminal_app.py` (§19.12 launcher files; `install_plan()` and `uninstall_plan()` are pure lists of
paths/tasks asserted; `status` text when health lacks the marker; `legacy` chooses `legacy-view.db` and never the live
path; the stale-lock and liveness helpers with a fake `OpenProcess`).

**Acceptance (manual, by WP-16 on the laptop):** `py -3.12 -m finsim stop` stops the 0.11.0 engine; `install
--no-open` replaces `Startup\FinSim Server.vbs`, removes the `FinSim Server` task, starts 1.0.0; `status` shows
`finsim-terminal 1.0.0`, integrity ok, the Startup entry; `open` opens the window; `legacy` serves the copy on 8766;
`sha256(finsim.db)` unchanged before/after.

---

## WP-15 — Integration (checklist, reconciliation, CHANGELOG.md, MAP.md final, UI clean-up list)

**Owns:** `finsim/docs/TERMINAL_CHECKLIST.md`, `finsim/CHANGELOG.md`, `finsim/tests/test_terminal_reconcile.py`,
`finsim/docs/design_proposals/BUILD_NOTES.md` (resolution log), plus cross-package fixes made **with the owning
package's agent** (the integrator edits another package's file only to resolve a BUILD_NOTES item and records it
there; it also updates WP-00's `docs/MAP.md` at integration under the same rule).

**Consumes:** everything.

**Build:** wire the real `Runtime` end to end (`serve` with the real providers on a scratch home: `--home
<scratch>/.finsim-test`); run the smoke probe and set LIMITED/computed-only flags per its verdict; resolve every
BUILD_NOTES item; **`CHANGELOG.md`** (C4): one plain-English line per change, compiled from every package's
`BUILD_LOG.md` line and BUILD_NOTES resolutions (packages never edit it); the **UI clean-up list** (directive −3: every
inconvenience fixed, for the report) in the checklist; write the browser checklist (§19.12 browser check: Personal /
Fund / Institutional books with 1,000,000 / 25,000,000 / 1,000,000,000 and the PAPER tag; book switch; Daily News with
real headlines and the attention strip; US Exchange → AAPL → preview with freshness → submit 1 share in session →
Positions and Transactions → F5 → `stop`/`open` → position still there; a swap preview in the Fund book's PB account
and the NOT_ELIGIBLE text in the Personal book; a private secondary preview in the Institutional book; Reset lifetime
on a scratch book and the History page; console and network tabs clean; every page at 1100 and 1440 px); the
reconciliation suite.

**Tests:** `test_terminal_reconcile.py` (§19.12 reconciliation over §19.1–19.8 **and §19.15–19.19** in one DB across
`T1` plus a second test lifetime, every L and M check, the NAV identity to the cent incl. swap overlays, private marks
and private loans, 3100 vs advances per lifetime, transactions completeness, residual 0.00, `ALL_BOOKS` = Σ lifetimes).

**Acceptance:** full `test_terminal_*` suite green on `py -3.12 -m unittest` and `py -3.14 -m unittest`; the real UI
passes the checklist on the scratch home; no BUILD_NOTES item open; `CHANGELOG.md` and `MAP.md` current.

---

## WP-16 — Verification and deployment

**Owns:** `finsim/docs/TERMINAL_VERIFICATION.md` (the report), the scratchpad `baseline/` folder (outside the repo).

**Consumes:** everything; `HANDOFF_2026-10-20.md` §12–§15 deliverables; `BUILD_PROMPT_2026-10-20.md` §E;
`TERMINAL_DECISIONS.md` §6 bar.

**Build / run:** (1) capture the baseline **before** any terminal code is imported by the old suites: `py -3.14 -m pytest
tests/test_app.py tests/test_tzfallback.py tests/test_quant.py tests/test_api.py -q` and `tests/test_fs2_*.py
tests/test_desk_*.py` (results into `baseline/`), `sha256(~/.finsim/finsim.db)`; (2) run the terminal suites on both
interpreters; (3) run the old subsets again and diff against the baseline (document existing failures separately from
regressions — D13); (4) deploy on the laptop with the §18 sequence (the old engine stopped first; hashes before/after);
(5) browser check on the real server with console/API review and screenshots (incl. the three books, Daily News, a
swap preview, a private preview, History); (6) restart persistence on the real home (`stop`/`open`, trades survive);
(7) duplicate-server check (`serve` twice → exit 3); (8) write the report per §E: each bug with root cause and fix,
every calculation corrected (before/after from tests), what `integrity()` guards (L1–L25, M1–M5), pages of the old app
removed and where their content went (Calculations → Strategies tab; Live Trades → Orders/Activity; OTC Derivatives →
PB OTC tab; All Books → book switcher + Treasury; Options/Futures → account Markets tabs; Coverage/Groupings/IB/Saves →
dropped with the reason), UI clean-ups made (from WP-15's list), test counts, hand-worked reconciliations, baseline vs
after, limitations (§20), anything found but not changed with the reason, commit hash and branch on the integration
branch (no merge to `main`).

**Acceptance:** every item in TERMINAL_DECISIONS §6 and BUILD_PROMPT §E is evidenced in the report; `finsim.db`
byte-identical; the Startup folder holds exactly one `FinSim Server.vbs` pointing at the terminal; the scheduled task
`FinSim Server` is gone; the branch is pushed and the hash reported.

---

## WP-17 — PB OTC swaps: IRS core, computed marks, DV01, IM, daily variation margin (CTM/STM), accruals, fixings, payments, termination, swap ticket

**Owns:** `finsim/finsim/terminal/products/swaps.py`, `finsim/finsim/terminal/static/components/swapticket.js`,
`finsim/tests/test_terminal_swaps.py`.

**Consumes:** WP-00 (`ledger`, `commands`, `books.require_pb_otc`, `instruments` (SWAP rows), `tables`, the
`otc_contracts` / `otc_vm` / `otc_resets` tables), WP-01 (`CurveStore.discount_factors`, `RateStore`, `MarkProvider`
dispatch, `FakeProvider.set_curve_dfs`), WP-02 (`execution.fund`), WP-07 (`prime.schedule`: IRS half-width,
`otc_spread_mult`, `swap_margin_mode`), WP-08 (`accrue_segment`; the jobs SWAP_VM / SWAP_FIX / SWAP_PAY call this
module's handlers), WP-12 `T.*` (component); `finsim.quant.swaps`, `finsim.engines.otc_pricing.schedule/yearfrac`
(pure float helpers, converted with `D(repr())`).

**Build:** §13.11: `preview()` (par rate from the dfs, dealt rate ± half-width, `mark0`, `dv01_c`, IM = DV01 × 50 bp,
schedules modified-following on the trading calendar, conventions), handlers `otc.book` (IM `Dr 1330 / Cr 1010` with
`fund()`, contract + SWAP instrument rows, `mark_c = mark0`), `otc.terminate`, `swap.vm` (CTM: 1320 / 2330 one movement
with the zero-crossing rule, `vm_balance_c`; STM: 4450 settlement and mark reset; Δ = 0 → SKIPPED; no curve → SKIPPED),
`swap.fix` (RATE_LAG, segment restart), `swap.pay` (net settlement with `fund()`), `accrual_lines_swap` (kinds swaprcv
/ swappay through `accrue_segment`, 30/360 and ACT/360), `mark()` and `dv01_c()` (clean PV, inputs), `ContractView` for
the API, the XCCY/TRS hooks WP-26 extends; `components/swapticket.js` (IRS / TRS / XCCY form → `/api/otc/preview` →
confirm; contracts table with VM and reset history; mounted on the PB page's OTC tab by WP-12 when the lifetime is
eligible). Non-swap OTC kinds answer `NOT_IN_THIS_BUILD`.

**Tests:** `test_terminal_swaps.py` (§19.15 exact: par 4.0000 %, dealt 4.0100 %, mark0 −1,903.86, DV01 1,903.86, IM
95,193.00, NAV 98,096.14; day-1 VM post 1,903.86; +10 bp mark 17,114.00 and the 19,017.86 two-part entry, NAV
117,114.00, L22; accruals 1,113.89 × 4 then 1,113.88 and 1,075.00; payment 200,500.00 / 195,650.00 / net 4,850.00;
STM variant; termination; NO_CURVE skip; NOT_ELIGIBLE in P1; NOT_TRADEABLE_HERE outside a PB account; rerun
idempotency of every key).

**Acceptance:** scenario 19.15 exact to the cent with residual 0 at every step; L22 holds after every command; VM is
never posted twice for a date; nothing is booked without a curve; the swap ticket renders from the fixture server.

---

## WP-18 — Dividends, payments in lieu, split notices

**Owns:** `finsim/finsim/terminal/products/dividends.py`, `finsim/tests/test_terminal_dividends.py`.

**Consumes:** WP-00 (`ledger`, `calendar.add_business_days`, `v_positions`, `fills`), WP-01 (`YahooChart.events` via the
worker's refresher → `dividends` table; `FakeProvider.set_events`), WP-08 (the DIVIDEND / DIVPAY jobs call `div.ex` /
`div.pay`; `accrue_segment` not needed), WP-26 (TRS dividend accrual reads the same table — optional).

**Build:** §13.15: `events_for()`, handlers `div.ex` (position at the prior close per account, long 1210/4200, short
5400/2320, idem `div:…`), `div.pay` (`pay_date` or ex + 15 business days, idem `divpay:…`), `div.split_notice`
(Attention WARN only), `due()` helpers for WP-08 (keys from `dividends` rows ≤ today not in `jobs_done`), position-row
notes (`income_pending_c`, the convention text).

**Tests:** `test_terminal_dividends.py` (§19.19: 260.00 on 2026-11-09, pay 2026-12-01 with the 15-business-day count
across Thanksgiving, no income for shares bought on the ex-date, payment in lieu 100.00 on the short, P&L explain
Δincome +260.00 / Δfinancing −100.00 residual 0, rerun idempotency, split → WARN only, catch-up after downtime).

**Acceptance:** scenario 19.19 exact; nothing posts without a `dividends` row sourced from Yahoo; the convention is
labelled on every row.

---

## WP-19 — Private Markets: sourced company master, negotiated secondary trades, last-round marks, lock-ups, QP gate, UI

**Owns:** `finsim/finsim/terminal/products/private.py`, `finsim/finsim/terminal/data/private_companies.json`,
`finsim/finsim/terminal/static/pages/private_markets.js`, `finsim/finsim/terminal/static/components/privateticket.js`,
`finsim/tests/test_terminal_private.py`.

**Consumes:** WP-00 (`ledger`, `commands`, `books.require_private/unlock_private`, `instruments` (PRIVATE rows),
`private_companies` / `private_rounds` tables, `lots.lock_until`, `calendar`), WP-01 (`MarkProvider` dispatch to
`private_mark`), WP-02 (`execution.apply_fill` with origin NEGOTIATED and calendar-day settlement, `fund`), WP-09
(reads positions — optional), WP-12 `T.*` (page + component), WP-08 (SETTLE_DUE pays the T+30 settlement; HOUSEKEEPING
re-seeds the master).

**Build:** §13.13: **the master file — researched with web search at build time: every `post_money_usd` /
`price_per_share` carries `source_url` + `as_of` or is `null`; ~25 real names (Anthropic, OpenAI, SpaceX, xAI, Stripe,
Databricks, Anduril, Scale AI, Perplexity, Mistral, Safe Superintelligence, Anysphere (Cursor), Groq, Revolut, ByteDance,
Canva, Epic Games, Discord, Fanatics, Kraken, Ramp, Rippling, Deel, Vercel, Cerebras); nothing estimated** (this is the
only package allowed to use the network, and only the WebSearch tool, only at build time, never in code or tests);
seeding into the tables and `instruments(kind PRIVATE, support COMPUTED)`; `private_mark()` (quality `private`,
"last round <date>", unavailable when only a valuation is known, listed mark after PUBLIC + ticker); handlers
`private.add_company`, `private.record_round` (source URL mandatory → `SOURCE_REQUIRED`), `private.trade` (BUY/SELL
SECONDARY or ROUND at the negotiated price, fee %, settle_days calendar, lock-up on the lot, LOCKUP warning on sells,
FIFO realised); `preview()`; the page (companies grid with mark, source, as-of; dialogs) and the ticket component
(mounted by WP-12 on the PRIVATE account page).

**Tests:** `test_terminal_private.py` (§19.17: the data-file guard (≥ 20 rows, every number sourced, the named ids
present); BUY 10,000 @ 95.00 → fee 28,500.00, sweep 978,500.00, settlement 2026-11-19, lock-up 2027-04-20, MV
1,000,000.00, unrealised 50,000.00, NAV 1,000,021,500.00; record round 120.00 → unrealised 250,000.00; SOURCE_REQUIRED;
SELL 2,000 @ 118.00 inside the lock-up → LOCKUP warning, realised 46,000.00, the exact entry; NOT_ELIGIBLE_QP in P1;
PRICE_REQUIRED; NOT_TRADEABLE_HERE for shorts; PUBLIC switch to the listed mark; L24).

**Acceptance:** scenario 19.17 exact; no number in the master without a source; private marks never age into stale
and never execute; the page renders from the fixture server.

---

## WP-20 — Portfolio Performance statistics

**Owns:** `finsim/finsim/terminal/performance.py`, `finsim/finsim/terminal/static/components/performance.js`,
`finsim/tests/test_terminal_performance.py`.

**Consumes:** WP-00 (`nav_snapshots` per lifetime, `accounting.flows`, `marks` CLOSE rows, `RateStore.sofr` via WP-01),
WP-08 (snapshots written at 17:45; SPY always recorded), WP-11 (`/api/portfolio/performance`), WP-12 (`pages/portfolio.js`
mounts the component); `finsim.quant.stats` helpers (pure floats).

**Build:** §6.9 `statistics()` (pure: daily flow-adjusted returns, annualised return, volatility, Sharpe, Sortino,
beta/alpha vs SPY closes, max drawdown, hit rate, monthly NAV-to-NAV table with flows excluded, data window, skipped
pairs, `insufficient history`), `PerfView` JSON (ratios 4 dp floats, NAVs cents); the Performance tab component (table
+ monthly table + the sentence PERF).

**Tests:** `test_terminal_performance.py` (§19.20 exact: returns [0.01, −0.01, 0.02, 0.00], ann_ret 1.2600, vol 0.2049,
Sharpe 6.0160, Sortino 15.3672, beta 0.4000, alpha 0.7397, max_dd 0.0100, hit rate 0.5000, monthly 0.019898;
insufficient history with one snapshot; a missing SPY close skips the pair; per-lifetime isolation).

**Acceptance:** every statistic reproduces the hand-worked numbers to 4 dp; the tab shows "insufficient history" with
the count until two snapshots exist; nothing is computed on the page.

---

## WP-21 — Private credit (direct loans): commit, coupon + OID accrual, quarterly payments, amendment fees, computed mark, repayment

**Owns:** `finsim/finsim/terminal/products/private_credit.py`, `finsim/finsim/terminal/static/components/privatecredit.js`,
`finsim/tests/test_terminal_private_credit.py`.

**Consumes:** WP-00 (`ledger`, `commands`, `private_loans` table, `calendar`, `tables.POLICY_INDEX`), WP-01
(`RateStore` SOFR and `BAMLH0A0HYM2`, `MarkProvider` dispatch to `pc_mark`), WP-02 (`fund`), WP-08 (`accrue_segment`;
ACCRUE_DAILY kinds pccpn/pcoid and PC_PAY call this module), WP-19 (`private_companies` for the borrower picker —
optional), WP-12 `T.*` (component mounted on the Credit page).

**Build:** §13.14: handlers `pc.commit`, `pc.amend_fee`, `pc.repay`, `pc.pay` (job), `accrual_lines_pc` (coupon ACT/360
floating with segment restart on a new observation; OID straight-line over calendar nights), `pc_mark()` (amortised
cost × (1 − ΔOAS × T_remaining), inputs), `PrivateLoanView`; the Credit page section component (commit form + loans
table).

**Tests:** `test_terminal_private_credit.py` (§19.18 exact: cash out 4,900,000.00; coupon 1,370.83 / 1,370.84; OID 91.24
nights; payment 126,116.67 on 2027-01-20; 1130 = 4,908,394.16 (L24); fee 25,000.00; mark 4,773,379.70 at +100 bp with
1,004 days remaining; unrealised −135,014.46; par repayment realising 91,605.84; SOFR missing → SKIPPED; EUR →
CURRENCY_UNSUPPORTED).

**Acceptance:** scenario 19.18 exact; L24 holds; no default, rating or review event is ever generated.

---

## WP-22 — Daily News: real headlines (Yahoo news search, Fed RSS, the repo's newspaper) and the attention strip

**Owns:** `finsim/finsim/terminal/news.py`, `finsim/finsim/terminal/static/pages/news.js`,
`finsim/tests/fixtures/terminal/news/` (yahoo_news.json, fed_rss.xml, newspaper_index.json),
`finsim/tests/test_terminal_news.py`.

**Consumes:** WP-00 (`config.NEWS_*`, `config.newspaper_index`, `news_items` table, `attention`, `accounting`), WP-01
(`quotes.http.http_get` / `ua_for`, the worker's refresher slot registration), WP-09 (`risk.report.warnings`,
`compliance.holdings` counts — optional), WP-11 (`/api/news`, `health.news`), WP-12 `T.*` (page).

**Build:** §17.9: `NewsStore` (fetchers for the three sources with the UA policy — Yahoo `BROWSER_UA`, Fed `GOV_UA`,
newspaper from disk; sanitising, 300-char truncation, instruction-pattern flags counted in `injection_flags`, dedupe
by (source, link), bounded retention 500 rows), `refresh(which)` callable from the worker slot (never on a request
thread), `items()`, `attention_summary(services, scope)` (every count from `accounting` / `attention` / `risk` /
`compliance`), the page (strip + three columns + the NEWS sentence; opens links in a new tab; no interpretation).

**Tests:** `test_terminal_news.py` (§19.21: UA per source captured from a stubbed `http_get`; 13 items from the
fixtures newest first; an item without a title skipped; the `learn` slot skipped; dedupe on a second refresh;
`injection_flags == 1` for the planted text; `attention_summary` counts on the §19.5 book incl. `collateral_calls_today
1`; `test_attention_shows_integrity_failure` (D12) with a corrupted `gl_balances` row → `integrity.ok False`, `["L3"]`;
a failing fetch keeps the last items and sets the provider state).

**Acceptance:** tests pass; no network in tests; the Fed is never fetched with a browser UA; the page renders from the
fixture server with the strip's numbers equal to the API's.

---

## WP-23 — Lifetimes: Reset lifetime with history, onboarding agreements, book settings, History UI

**Owns:** `finsim/finsim/terminal/lifetimes.py`, `finsim/finsim/terminal/static/components/lifetimes.js`,
`finsim/tests/test_terminal_lifetimes.py`.

**Consumes:** WP-00 (`books.create_lifetime/eligibility`, `lifetimes` / `agreements` tables, `accounting.nav/integrity`
per lifetime, `treasury`), WP-02 (`order.cancel` logic reused for the cancellation sweep), WP-06 (`BORROW_CLEANUP`
helper for borrows backing cancelled orders — optional), WP-07 (`account.open_pb`), WP-08 (snapshot writer for the
final `{lifetime}:ALL` row), WP-11 (`/api/books/*`, `/api/history/*`), WP-12 (`pages/settings.js` mounts the component;
`pages/history.js` is WP-12's).

**Build:** §13.10: handlers `book.reset_lifetime` (one transaction: cancel open orders "lifetime reset", borrow cleanup,
integrity report stored, final figures, last snapshot, accounts CLOSED, new lifetime with the typed capital; typed
"RESET" confirmation; idempotent request id), `book.onboard_pb` (three agreements + `account.open_pb`; NOT_ELIGIBLE),
`book.update_settings` (thresholds, book defaults for new lifetimes, names), `history()` read model; the Settings
component (Reset dialog, past lifetimes table with History links).

**Tests:** `test_terminal_lifetimes.py` (§19.14 reset: cancelled order, final NAV 1,000,049.00, pnl 49.00, CLOSED
accounts, P2 with 1,000,000.00, `nav("P1:ALL")` frozen, `ALL_BOOKS` excludes P1, LIFETIME_CLOSED / ACCOUNT_CLOSED on
writes, L23, lower-case confirm → 400, replay returns the stored result; onboarding P1 refused, F1 ok with three
agreements, idempotent; `book_defaults` applies to the next lifetime only; an integrity failure at reset is recorded,
not blocking).

**Acceptance:** scenario 19.14 exact; nothing is deleted by a reset (row counts before/after asserted); the archived
lifetime answers every read route with `read_only: true`.

---

## WP-24 — Strategy templates data module (the old Calculations page's 60 templates) and package resolution

**Owns:** `finsim/finsim/terminal/strategies.py`, `finsim/tests/test_terminal_strategies.py`.

**Consumes:** WP-00 (`instruments`, `tables`, `books.eligibility`), WP-01 (`ChainSnapshot` / `ChainCache.get`,
`QuoteCache.get`), WP-02 (`order.submit` consumes `package_legs`; `evaluate_package` is WP-02's), WP-03
(`margin_requirement_c` for the package margin — optional at import), WP-17 (`swaps.preview` for TRS legs — optional).

**Build:** §13.16: `GROUPS` / `STRATEGIES` copied verbatim from `finsim/engines/playbook.py` (60 entries; the module
never imports `finsim.engines.playbook`), `resolve_strike`, `zero_cost_call`, `expiry_for_tenor`, `size_legs`,
`package_legs` (prices per leg from the cached chain / records, totals, incremental option margin when WP-03 is
present, payoff grid and max gain / loss / breakevens as the old `_payoff`, `fill_mode`), `available`/`reason` per
template per account (TRS needs an eligible PB account; a futures leg needs the front month listed), the
`/api/strategies*` view functions WP-11 routes to.

**Tests:** `test_terminal_strategies.py` (§19.22 resolution pins: 715 / 770 / 840 / 730 / 800; `expiry_for_tenor` →
2027-02-19 and the 2027-01-22 variant; sizing 1,000 + 10 puts, 5 calls, 10 + 20, 2 ES; `fill_mode` AON for IRON_CONDOR
and SEQUENTIAL for PROTECTIVE_PUT; the iron-condor package of §19.22 priced from a fixture chain: credit +4,200.00,
margin 28,100.00, payoff max gain 4,200.00 − fees, max loss 30,000 − 4,200 = 25,800.00 (per the old `_payoff` grid at
the near expiry), breakevens 730 − 4.20 = 725.80 and 800 + 4.20 = 804.20; TRS template unavailable outside an eligible
PB account; exactly 60 templates and 12 groups; every leg rule parses).

**Acceptance:** tests pass; the catalogue equals the old one entry for entry (keys, groups, legs); no network, no
simulation imports.

---

## WP-25 — Glossary data (reuse of the old glossary, see-also fix, new terms)

**Owns:** `finsim/finsim/terminal/static/glossary-data.js`, `finsim/tests/test_terminal_glossary.py`.

**Consumes:** `finsim/finsim/static/glossary.js` (read-only source), §1.3 conventions (`config` / `tables` — the test
imports the sentences from `finsim.terminal.tables.CONVENTIONS`), WP-12 (`pages/glossary.js` renders it).

**Build:** §17.8: copy and rewrite the 222 entries for the terminal (game terms removed, simulator mechanics
rewritten, unsupported products suffixed "— not in this build"), see-also as arrays (the comma bug), `home` for terms
whose old page was removed (B17), every §1.3 convention key as an entry with the verbatim sentence, and every new term
listed in §17.8 (Treasury definition, Available, Excess liquidity, Encumbrance, Book, Lifetime, Reset lifetime, ECP,
ISDA/CSA/PBA, GMRA, MSLA/GMSLA, Qualified purchaser, Accredited investor, Rule 144, Form D, Private secondary, Lock-up,
Last-round mark, Swap VM, CTM vs STM, TRS reset, XCCY notional exchange, MTM XCCY, Trailing stop, AON package,
Participation cap, Freshness states, OID accretion, Amendment fee, PDT, 13F, 13h-1, Form SHO / 13f-2, Rule 201, Reg T,
Daily News, Requires your attention, each performance statistic).

**Tests:** `test_terminal_glossary.py` (parses the JS data with a small bracket-aware reader — the file is a single
`window.TERMINAL_GLOSSARY = {...};` JSON-compatible literal; every see-also target exists; every convention key
present with the exact sentence; every listed new term present; no "simulator" / "next session" / "career" in any
definition; no entry's see-also contains a comma-separated string).

**Acceptance:** tests pass; the glossary page (WP-12) renders every category from the fixture server with working
see-also links.

---

## WP-26 — TRS and cross-currency swaps: resets, financing and dividend accrual, notional exchanges, MTM FX resets

**Owns:** `finsim/finsim/terminal/products/swaps_trs.py`, `finsim/tests/test_terminal_swaps_trs.py`.

**Consumes:** WP-17 (`otc.book` kinds TRS / XCCY dispatch here; `swap.vm`, IM, `ContractView`, `accrual_lines_swap`
hooks), WP-00, WP-01 (`MarkProvider` for the underlying and `fx()`, FX CLOSE marks), WP-02 (`fund`), WP-05
(`fx.fund_ccy` for the paid currency; FX settlement helpers), WP-08 (`accrue_segment`; the jobs TRS_RESET /
XCCY_EVENTS call this module), WP-18 (`dividends` rows for TRS dividend accrual — optional, 0 when absent).

**Build:** §13.12: TRS booking (reset_px from a fresh/CLOSE mark, notional, IM 10 %), `mark_trs`, financing accrual
(kind `trsfin`), dividend accrual on the contract, handler `trs.reset` (return leg, dividend settle, financing paid,
notional reset, VM balance re-measured in the same entry); XCCY booking (IM 2 %), `mark_xccy`, handlers `xccy.exchange`
(start / maturity notional legs, each balanced in its currency; `fx.fund_ccy`), `xccy.fx_reset` (MTM reset of the USD
leg at the FX CLOSE), per-currency coupon legs through WP-17's accrual lines; `due()` helpers for WP-08.

**Tests:** `test_terminal_swaps_trs.py` (§19.16 exact: TRS notional 3,395,000.00, IM 339,500.00, financing 421.55 /
421.54, day-10 mark 25,000.00 and VM 10,000.00, dividend accrual 2,600.00, reset at 345.00: return 55,000.00,
financing 12,646.38, collateral 55,000.00 returned in the same entry, notional 3,450,000.00, period P&L 44,953.62, net
cash −10,046.38, L22; payer variant signs; XCCY start legs at 1.1238, IM 224,760.00, coupons 1,248.67 and 694.44 (base
at the command's rate), FX reset 62,000.00, maturity reversal, `pnl` residual 0 and the neutral FX effect).

**Acceptance:** scenario 19.16 exact to the cent; L1 holds on every multi-currency entry; L22 after every command;
nothing is paid twice at a reset.

---

## Package dependency summary

```
WP-00 core (+ books model, MAP.md)
  ├─ WP-01 quotes (+ chart events, dfs) ──┐
  ├─ WP-09 risk / compliance              │
  ├─ WP-20 performance                    │
  ├─ WP-25 glossary data                  │
  ├─ WP-24 strategies (needs WP-01 chain) │
  ├─ WP-22 news (needs WP-01 http) ───────┤
  └─ WP-02 equities / matcher / trailing / AON (needs WP-00 + WP-01 contracts)
        ├─ WP-03 options + hedges ──┐
        ├─ WP-04 futures            │
        ├─ WP-05 FX + loans         ├─ WP-08 lifecycle (consumes all product jobs) ─┐
        ├─ WP-06 lending + repo     │        ├─ WP-18 dividends                     │
        ├─ WP-07 prime brokers ─────┘        ├─ WP-21 private credit                │
        │     └─ WP-23 lifetimes             ├─ WP-17 swaps IRS (+ WP-07) ─ WP-26 TRS/XCCY (+ WP-05)
        ├─ WP-19 private markets (web-sourced master)                              │
        └─ WP-10 bonds (Build 2, after WP-08)                                       │
WP-11 server + API (after WP-00/01/02; stubs for the rest) ─────────────────────────┤
WP-12 UI shell (after WP-11 fixtures) ─ WP-13 ticket (after WP-12) ─────────────────┤
WP-14 launcher (after WP-11) ───────────────────────────────────────────────────────┤
WP-15 integration (after all Build 1 packages) ─ WP-16 verification + deployment ───┘
```
